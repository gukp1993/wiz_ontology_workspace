"""后台任务执行器：有界工作池 + owner 上下文 + 阶段推进（不持全局写锁）。

关键纪律（执行指令 §5 / 开发计划 §4.3）：
* 后台线程不继承请求 contextvars：每个作业用**服务端已鉴权的 task owner** 显式
  `auth.bind_request`，结束后在 finally 里解绑，绝不能凭客户端字段决定归属。
* 解析 / LLM / 文件读写都不持 `workbench.locking.LOCK`，也不开长写事务：
  只做「短事务读状态 → 外部工作 → 短事务提交」，提交前复查取消与 fencing。
* 业务内容写点（候选/消息/材料事实，R4-01）必须经 `content_tx()` 落库：同一
  写事务内先核对取消与执行权再写内容，晚到 worker 的旧结果不可能通过核对。
* 服务重启把 queued/running 标 interrupted，不假装仍在运行。
* 单进程内有界池（RUN_WORKERS）；作业异常写回 run.error 与 retryable，
  绝不吞掉后静默当成功。

执行权 fencing（D13；R3-01 修复）：
* 执行令牌绑定到**具体 worker**：每次 `submit()` 生成唯一 worker token，并在短写事务里
  用 `store.rotate_run_lease` 显式轮换 lease（即使重试路径没推进 attempt，两次 submit 也
  绝不共用执行权）；token 登记在 `_workers`，worker 线程把自己的 token 放进
  `threading.local`。
* `lease_of()` **只按调用线程自己的 token** 解析。旧 worker A 因此永远读不到取消+重试后
  新 worker B 的 lease：A 的 stage / 终态写回按自己那份已作废的 lease 判定，一律丢弃。
  （此前的实现按 (user, run) 共享一个键，重试后的 submit 覆盖登记 → A 读到 B 的 lease，
  晚写入被判合法，且 A 退出时把 B 的登记一起删掉。）
* 旧 worker 的 finally 只弹自己的 token，绝不按 (user, run) 删别人的登记。
* 非 worker 线程（同步直调 pipeline 的测试/脚本）没有执行权：`lease_of()` 返回 ''，
  即不启用 lease 条件写（既有直调路径语义不变）；需要真实执行权时用
  `begin_worker_scope()` 显式进入。
"""
import contextlib
import json
import threading
import traceback

from workbench import auth
from workbench.ontology_build import batch_contracts
from workbench.ontology_build import protocol
from workbench.storage import engine as sto
from workbench.storage import ontology_build as store


class Cancelled(Exception):
    """作业在阶段边界检测到取消；不写入结果。"""


_pool_lock = threading.Lock()
_semaphore = threading.BoundedSemaphore(protocol.RUN_WORKERS)
# 执行登记（按 worker token 索引，不是按 (user, run)）：一个 run 上可能同时存在
# 「旧 attempt 的垂死 worker + 新 attempt 的 worker」，两者必须是各自独立的登记。
_workers = {}
# 当前线程所属的 worker token（worker 线程启动时设置、finally 清空）。
# lease_of / stage / _finish 的执行权只从这一个来源解析。
_worker_local = threading.local()
# 兼容层：仅供**非 worker 线程**（白盒测试 / 直跑脚本）显式声明本线程使用的 lease。
# 生产路径（submit / begin_worker_scope）从不写它；worker 线程忽略它，因此不存在
# 「旧 worker 读到新 worker lease」的通道。
_leases = {}


def _new_worker_token():
    return 'w-' + sto.new_id()


def _capture_lease(user_id, run_id):
    """读运行当前 DB lease（`begin_worker_scope(lease=None)` 用）；任何失败返回 ''。"""
    try:
        with sto.read_connection() as conn:
            state = store.run_state(conn, run_id, user_id)
        return str((state or {}).get('lease_token') or '')
    except Exception:
        return ''


def _rotate_lease(user_id, run_id):
    """短写事务里显式轮换执行权，返回本次 worker 专属 lease（运行不存在返回 ''）。"""
    with sto.write_tx() as tx:
        return str(tx.run(lambda conn: store.rotate_run_lease(conn, run_id, user_id)) or '')


def _current_token():
    return str(getattr(_worker_local, 'token', '') or '')


def _push_token(token):
    """把 token 写进当前线程本地，返回被替换的旧值（退出时恢复用）。"""
    previous = _current_token()
    _worker_local.token = token
    return previous


def _pop_token(previous):
    _worker_local.token = previous or ''


def lease_of(user_id, run_id):
    """本线程持有的 fencing token（供管线直写 update_run 时带条件）。

    解析源只有一个：**调用线程自己的 worker token**（`submit()` 或
    `begin_worker_scope()` 登记时生成）。token 不存在、或登记记录的 (user, run) 与
    入参不匹配 → 返回 ''。'' 表示「本线程没有执行权」：不启用 lease 条件写，这与既有
    同步直调路径兼容（它们本来就没有执行权）。真实 worker 的 lease 永远是它自己提交时
    轮换出来的那一份，因此旧 worker 不可能读到新 worker / 新 attempt 的 lease。

    非 worker 线程额外支持 `_leases[(user, run)]` 显式覆盖（仅白盒测试模拟 lease 失配
    用，生产路径从不写它）；worker 线程一律忽略该兼容表。
    """
    key = (str(user_id), str(run_id))
    token = _current_token()
    if token:
        with _pool_lock:
            record = _workers.get(token)
        if not record:
            return ''
        if (str(record.get('user')), str(record.get('run'))) != key:
            return ''
        return str(record.get('lease') or '')
    with _pool_lock:
        return str(_leases.get(key) or '')


def _owner_user(user_id):
    """构造最小用户上下文（后台线程没有请求上下文）。"""
    return {'userId': user_id}


def submit(user_id, run_id, job):
    """提交一个后台作业；job(owner_user_id, run_id) 在 owner 上下文中执行。

    执行令牌绑定本次 worker：
    * 先在短写事务里显式轮换 lease（`store.rotate_run_lease`），拿到本次 worker 专属
      的 fencing token —— 两次 submit 绝不共用执行权，即使重试路径没有推进 attempt；
    * 登记 `_workers[token]`，worker 线程启动时把 token 放进线程本地（`lease_of()` 的
      唯一解析源），结束后只弹自己的登记，绝不动同 run 其它 worker 的登记。

    作业正常返回即视为成功（终态写 succeeded）；抛 Cancelled → cancelled，
    其它异常 → failed 并记录可读文案。作业自己也可以提前写终态，
    _finish 只在终态未落定时补写，不会覆盖已写结果。

    返回本次 worker 的 `threading.Thread`（调用方可靠它 join / 判存活）。
    """
    lease = _rotate_lease(user_id, run_id)
    token = _new_worker_token()
    with _pool_lock:
        _workers[token] = {'user': user_id, 'run': run_id, 'lease': lease}

    def wrapper():
        previous = _push_token(token)
        auth.bind_request(_owner_user(user_id))
        try:
            job(user_id, run_id)
        except Cancelled:
            _finish(user_id, run_id, 'cancelled', '已取消', retryable=True)
        except Exception as exc:  # 作业异常写入 run，不吞
            traceback.print_exc()
            _finish(user_id, run_id, 'failed', _message(exc), retryable=True)
        else:
            finish_success(user_id, run_id)
        finally:
            auth.bind_request(None)
            _pop_token(previous)
            with _pool_lock:
                _workers.pop(token, None)  # 只清自己的登记

    auth.bind_request(None)  # 提交者可能是请求线程，避免身份泄漏进新线程
    thread = threading.Thread(target=wrapper, name='build-run-%s' % str(run_id)[:8], daemon=True)
    thread.start()
    return thread


@contextlib.contextmanager
def begin_worker_scope(user_id, run_id, lease=None):
    """让**当前线程**取得某个 run 的执行权（不经 submit 直调 pipeline 的测试/脚本用）。

    * 进入时登记 `_workers[token]`：`lease=None` 读当前 DB lease 作为本次执行权；
      显式传 `lease` 则用调用者已知的那一份（例如白盒模拟已被轮换掉的旧 worker）。
    * 作用域内本线程的 `lease_of()` / `check_cancelled()` / `stage()` /
      `finish_success()` 都按这份执行权生效（yield 出持有的 lease）。
    * 退出时清空线程本地并只弹自己的登记；`lease=None` 的作用域关闭时，若 DB 里的
      lease 仍是本次这一份就轮换掉（执行权随作用域结束作废，晚写入自动失配）；
      已被新 worker 轮换过则不动，绝不误伤继任者。
    """
    token = _new_worker_token()
    held = str(lease) if lease else _capture_lease(user_id, run_id)
    with _pool_lock:
        _workers[token] = {'user': user_id, 'run': run_id, 'lease': held}
    previous = _push_token(token)
    try:
        yield held
    finally:
        _pop_token(previous)
        with _pool_lock:
            _workers.pop(token, None)
        if not lease and held:
            _retire_lease(user_id, run_id, held)


def _retire_lease(user_id, run_id, lease):
    """作废自己的执行权：DB lease 仍是这一份才轮换（已被接管则不动继任者的 lease）。"""
    def body(conn):
        state = store.run_state(conn, run_id, user_id)
        if state is None or str(state.get('lease_token') or '') != str(lease):
            return ''
        return store.rotate_run_lease(conn, run_id, user_id)

    try:
        with sto.write_tx() as tx:
            return str(tx.run(body) or '')
    except Exception:
        traceback.print_exc()
        return ''


def _message(exc):
    text = str(exc) or exc.__class__.__name__
    return text[:400]


def _finish(user_id, run_id, state, error='', retryable=False):
    """短事务写回非成功终态（cancelled / failed）。

    fencing：本线程的执行权（lease_of）不匹配（运行已被取消后重试/被新接管）直接丢弃
    晚结果，绝不把新接管的 queued/running 覆盖成 failed/cancelled。
    终态审计（第 5 轮报告 §4 要求的同类排查）：行已是终态时一律不改写——
    取消后 worker 再抛异常不会把 cancelled 覆盖成 failed，重复收尾幂等；
    「成功先提交、取消后到」由 request_cancel 的终态检查保证保持 succeeded。

    任务阶段回退（V2-8 / G23 配套缺陷修复，08 §12.3）：generate 运行转入 failed
    （或被 worker 在阶段边界察觉的 cancelled）时，任务若仍停在「生成中」则同一事务
    回退为「确定范围」——与 request_cancel 的取消回退同一口径，任务列表不得在失败后
    仍显示「生成中」。已写入的候选/事实保留，可从失败批次/阶段重试。
    """
    try:
        expected = lease_of(user_id, run_id)

        def body(conn):
            row = store.get_run(conn, run_id, user_id)
            if row is None:
                return
            if expected and str(row['lease_token']) != expected:
                return
            if row['state'] in ('succeeded', 'failed', 'cancelled', 'interrupted'):
                return
            hit = store.update_run(conn, run_id, user_id, state=state, error=error,
                                   retryable=retryable, cancel_requested=(state == 'cancelled'),
                                   lease=expected or None)
            if hit and state in ('failed', 'cancelled') and row['kind'] == 'generate':
                task = store.require_task(conn, row['task_id'], user_id)
                if task is not None and task['status'] == 'generating':
                    store.touch_task(conn, row['task_id'], user_id, status='scope',
                                     stage_label=protocol.TASK_STAGE_LABELS['scope'])
        with sto.write_tx() as tx:
            tx.run(body)
    except Exception:
        traceback.print_exc()


def check_cancelled(conn, run_id, owner_user_id):
    """阶段边界复查：已取消 / 未运行 / lease 已被轮换 → 抛 Cancelled（晚结果禁止写入）。"""
    state = store.run_state(conn, run_id, owner_user_id)
    if state is None:
        raise Cancelled('运行不存在')
    expected = lease_of(owner_user_id, run_id)
    if expected and str(state.get('lease_token') or '') != expected:
        raise Cancelled('运行已被重试接管或已轮换执行权，旧结果不再写入')
    if state['cancel_requested'] or state['state'] in ('cancelled', 'interrupted'):
        raise Cancelled('用户已取消')


def content_tx(user_id, run_id, body):
    """业务内容写事务：同一写事务内先核对取消与执行权，再执行 body(conn) 写内容。

    语义（R4-01）：候选、对话消息、材料事实等业务内容一律经这里落库，不能在
    内容写完之后才靠下一次 stage/update_run 检查——那是「先污染后拦截」。
    为什么同一事务内核对就足够：SQLite BEGIN IMMEDIATE 串行化所有写事务，
    核对通过后到提交前，取消标记与 lease 轮换都无法并发插入，晚到 worker 的
    内容不可能在通过核对后落库。核对失败抛 Cancelled，任何内容都不写。
    """
    def guarded(conn):
        check_cancelled(conn, run_id, user_id)
        return body(conn)
    with sto.write_tx() as tx:
        return tx.run(guarded)


def stage(user_id, run_id, stage, label=None, progress=None):
    """短事务推进阶段。

    进度是「真实阶段 + 已处理数量」，不做虚假倒计时：progress 由调用方按实际
    处理条目数给出。取消检查在同一事务里完成，避免晚结果写进新基线。
    """
    expected = lease_of(user_id, run_id)

    def body(conn):
        check_cancelled(conn, run_id, user_id)
        hit = store.update_run(conn, run_id, user_id, state='running', stage=stage,
                               stage_label=label or '',
                               progress=progress if progress is not None else None,
                               lease=expected or None)
        if hit is False:
            raise Cancelled('运行执行权已转移，晚结果丢弃')
    with sto.write_tx() as tx:
        tx.run(body)


def finish_success(user_id, run_id, usage=None):
    """把运行标成功；生成类运行同时把任务推进到「评审初稿」。

    终态收尾与内容写入同一状态原则（R5-01）：在同一个 BEGIN IMMEDIATE 写事务内
    先读行核对——归属（get_run 带 owner）、执行权（lease）、取消标记、当前状态——
    全部通过才允许转成功，取消/重试无法插在核对与写入之间：
    * 已取消或 cancel_requested=1 → 保留 cancelled，不用成功覆盖（用户已看到取消）；
    * failed / interrupted → 不转成功；succeeded → 幂等返回；
    * 仅 running 允许成功收尾：当前三类管线作业都会先 stage 进 running，
      queued 直达成功没有真实调用场景（第 5 轮验收报告 §4）。
    成功转换实际成立才在同一事务内推进任务阶段；拒绝收尾不动任务阶段。
    「成功先提交、取消后到」由 request_cancel 的终态检查保证保持 succeeded。
    """
    from workbench.ontology_build import protocol

    expected = lease_of(user_id, run_id)

    def body(conn):
        row = store.get_run(conn, run_id, user_id)
        if row is None:
            return
        if expected and str(row['lease_token']) != expected:
            return  # 执行权已被重试接管或轮换：晚到收尾整体丢弃
        if row['cancel_requested'] or row['state'] == 'cancelled':
            return  # 取消已先提交：保留 cancelled，不用成功覆盖
        if row['state'] in ('failed', 'interrupted', 'succeeded'):
            return  # 失败/中断不转成功；重复成功收尾幂等
        if row['state'] != 'running':
            return
        # usage=None 时 update_run 不动 usage_json 列：管线在生成过程里已按 LLM 调用
        # 累计写入（calls/promptBytes/completionBytes/durationMs），终态补写绝不能
        # 用空字典把它覆盖回零。
        hit = store.update_run(conn, run_id, user_id, state='succeeded', error='',
                               retryable=False, usage=usage or None, checkpoint=None,
                               lease=expected or None)
        if hit is False:
            return
        # 成功转换实际成立，才推进任务阶段（同事务；拒绝收尾时不顺带改任务）
        if row['kind'] != 'generate':
            return
        task = store.require_task(conn, row['task_id'], user_id)
        if task is None:
            return
        if task['status'] == 'generating':
            store.touch_task(conn, row['task_id'], user_id, status='review',
                             stage_label=protocol.TASK_STAGE_LABELS['review'])
            return
        # V2-8：取消/失败曾把任务回退到「确定范围」，之后重试成功且本次运行基线
        # 仍未过期（材料/范围修订与冻结基线一致）→ 恢复到「评审初稿」；基线已变
        # （结果 stale）则保持 scope，由用户重新确认生成。
        if task['status'] == 'scope' and _baseline_matches(conn, row, user_id, task):
            store.touch_task(conn, row['task_id'], user_id, status='review',
                             stage_label=protocol.TASK_STAGE_LABELS['review'])
    with sto.write_tx() as tx:
        tx.run(body)


def _baseline_matches(conn, run_row, user_id, task_row):
    """run 冻结基线与当前材料/范围修订是否一致（空基线按一致处理：测试种子/历史行）。"""
    baseline = store._loads(run_row['baseline_json'] or '{}', {})
    if not baseline:
        return True
    scope = store.get_scope(conn, run_row['task_id'], user_id)
    if int(baseline.get('scopeRevision', -1)) != int(scope.get('revision') or 0):
        return False
    if int(baseline.get('materialRevision', -1)) != int(task_row['material_revision']):
        return False
    return True


def request_cancel(user_id, run_id):
    """请求取消：只置标记，不保证立即终止已发出的外部请求。

    任务级语义（08 §12.3）：同一写事务内若任务处于 generating，回退为 scope
    （确定范围）——任务列表不得在取消后仍显示「生成中」；已写入的候选/事实保留。
    """
    def body(conn):
        row = store.get_run(conn, run_id, user_id)
        if row is None:
            raise sto.NotFound('运行不存在')
        if row['state'] in ('succeeded', 'failed', 'cancelled', 'interrupted'):
            return
        store.update_run(conn, run_id, user_id, cancel_requested=True, state='cancelled',
                         error='已取消', retryable=True)
        task = store.require_task(conn, row['task_id'], user_id)
        if task is not None and task['status'] == 'generating':
            store.touch_task(conn, row['task_id'], user_id, status='scope',
                             stage_label=protocol.TASK_STAGE_LABELS['scope'])
    with sto.write_tx() as tx:
        tx.run(body)


def interrupt_stale_runs():
    """服务启动调用：queued/running → interrupted（绝不显示假运行中）。"""
    try:
        with sto.write_tx() as tx:
            return tx.run(lambda conn: store.mark_stale_runs_interrupted(conn))
    except Exception:
        traceback.print_exc()
        return 0


def in_owner_context(user_id):
    """给同步测试/直跑脚本用的上下文管理器语义（返回绑定/解绑函数对）。"""
    return lambda: auth.bind_request(_owner_user(user_id)), lambda: auth.bind_request(None)


def worker_slots():
    """当前可用并发额度（测试与能力接口用）；同一 run 的新旧 worker 只占一格。"""
    return protocol.RUN_WORKERS - len(active_runs())


def active_runs():
    """登记在册的活跃运行：按 (user, run) 去重、排序（同 run 有多少 worker 都算一条）。"""
    with _pool_lock:
        pairs = {(str(record['user']), str(record['run'])) for record in _workers.values()}
    return sorted(pairs)


# --- v2 计划收尾/恢复守卫（D10，2026-09-22 追加）-------------------------------------
#
# 契约来源：batch_contracts.py（D00 冻结）「终态检查」节、整合计划 v3 §6「持久化、
# 用量与恢复协议」、接口文档 08 §14.5「恢复与版本语义」。任务表 §8 D10 行：晚结果拒绝、
# auto/abstract 区分、未知版本无删除。既有函数（submit/stage/finish_success/_finish/
# check_cancelled/content_tx/begin_worker_scope 等）一概不改，本节只追加。

# resumable_jobs 的可续叶状态：失败只补未成功叶（成功叶绝不重做）；running 叶是崩溃
# 残留（尝试由恢复方 mark_interrupted_unknown 收口后重新派发）。blocked（预算受阻，
# 续跑须显式新计划，§6：不能用重试无限追加额度）、superseded（§4.3 已被替换，目标由
# 替代作业认领）不是可续叶；split 父作业不是叶。
_RESUMABLE_LEAF_STATES = (batch_contracts.JOB_QUEUED, batch_contracts.JOB_RUNNING,
                          batch_contracts.JOB_FAILED)


def _plan_schema_version(value):
    """checkpoint.generate.schemaVersion 安全解析：缺失 → None；非法/非整数 → -1。

    -1 与 ≥3 一样归入「未知版本拒绝恢复」；绝不猜 0/1 把未知计划当旧批次放行。
    """
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1


def finish_success_guarded(user_id, run_id, usage=None, plan_doc=None):
    """v2 计划成功收尾防线（整合计划 §6：「runner.finish_success 对 v2 增加防线，防调用方漏检」）。

    在既有 finish_success 的核对（归属/lease/取消/当前状态，见 finish_success docstring）
    **之前**先跑 batch_contracts.final_state_check(plan_doc)：
    * 终态检查不过（仍有 failed/blocked/active 叶、pendingTargetIds 未清、叶覆盖与计划
      不一致、blocking 未清）→ **冻结语义：返回 (False, violations)，不改 run 状态**——
      终态检查不过绝不能标 succeeded；
    * plan_doc=None → 退化为既有 finish_success 语义（schema1 旧批次兼容路径），
      返回 (True, [])（拒绝情形与既有一致：静默不动状态，见 finish_success）；
    * 终态检查通过 → 委派既有 finish_success 完成收尾（标 succeeded + generate 任务
      推进「评审初稿」），返回 (True, [])。

    plan_doc 是 checkpoint 形态 {'generate': doc}（与 contracts.final_state_check 入参
    一致，即 update_run(checkpoint=...) 写库的那一份）；直接传 generate doc 会被
    final_state_check 以「缺少 schemaVersion=2 计划」拒绝——守卫宁拒勿放。

    边界说明：终态核对与收尾是两个相邻步骤而非同一事务；两者受同一 lease 条件保护，
    持有当前 lease 的写入方只有当前 worker 自己（旧 worker 的写入在 update_run 的
    lease 条件处被拒），中间不存在可插入的第三方写点。
    返回 (bool, violations)：violations 为 final_state_check 的 [{'code','message'}]。
    """
    if plan_doc is None:
        finish_success(user_id, run_id, usage)
        return True, []
    check = batch_contracts.final_state_check(plan_doc)
    if not check.get('ok'):
        return False, list(check.get('violations') or [])
    finish_success(user_id, run_id, usage)
    return True, []


def resume_plan_guard(user_id, task_id, run_id, resume_mode):
    """恢复前版本语义守卫（08 §14.5 / 整合计划 §6 冻结口径）。**只读**：不改 run/
    checkpoint/候选/任务；epoch 推进与候选清理由调用方负责。

    规则（冻结）：
    a) run 无 checkpoint.generate、或 schemaVersion 缺失/=1 → legacy 路径：auto 保持
       原冻结批次路径、abstract 维持「全部抽象重跑」——绝不把旧批号伪装为 v2 叶状态；
    b) schemaVersion=2 → v2 路径：auto=按计划续跑（计划指纹一致性由调用方用
       plan_fingerprint_matches 比对后处理，不匹配 422 BUDGET_PLAN_MISMATCH）；
       abstract=显式新 epoch 语义（候选清理与计划替换同事务、旧调用用量保留有界历史
       摘要，由调用方执行；本守卫不拦合法新 epoch，见场景9）；
    c) schemaVersion 为其他值（≥3 或非法）→ ok=False + UNKNOWN_CHECKPOINT_SCHEMA：
       **拒绝恢复，绝不删候选兜底**；
    另有防御性拒绝（冻结返回形状内的附加码）：resumeMode 非法、运行不存在/不属于该
    任务、读库异常——一律 fail-closed（宁可拒绝恢复，不放行未知状态）。

    返回 {'ok': True, 'mode': 'v2_auto'|'v2_abstract'|'legacy_auto'|'legacy_abstract'}
    或 {'ok': False, 'code': str, 'message': str}。
    """
    if resume_mode not in ('auto', 'abstract'):
        return {'ok': False, 'code': 'RESUME_MODE_INVALID',
                'message': 'resumeMode 只能是 auto 或 abstract：%r' % (resume_mode,)}
    try:
        with sto.read_connection() as conn:
            row = store.get_run(conn, run_id, user_id)
    except Exception as exc:  # 读库失败按拒绝处理（fail-closed），不猜测版本
        return {'ok': False, 'code': 'RESUME_GUARD_ERROR',
                'message': '读取运行失败：%s' % str(exc or type(exc).__name__)[:200]}
    if row is None or str(row['task_id'] or '') != str(task_id or ''):
        return {'ok': False, 'code': 'RUN_NOT_FOUND', 'message': '运行不存在或不属于该任务'}
    try:
        checkpoint = json.loads(row['checkpoint_json']) if row['checkpoint_json'] else {}
    except (TypeError, ValueError):
        checkpoint = {}
    generate = checkpoint.get('generate') if isinstance(checkpoint, dict) else None
    version = _plan_schema_version(
        generate.get('schemaVersion') if isinstance(generate, dict) else None)
    if version in (None, 1):
        return {'ok': True, 'mode': 'legacy_' + str(resume_mode)}
    if version == batch_contracts.CHECKPOINT_SCHEMA_VERSION:
        return {'ok': True, 'mode': 'v2_' + str(resume_mode)}
    raw = generate.get('schemaVersion') if isinstance(generate, dict) else None
    return {'ok': False, 'code': batch_contracts.UNKNOWN_CHECKPOINT_SCHEMA,
            'message': '未知 checkpoint.generate.schemaVersion=%r，拒绝恢复（候选保留，'
                       '绝不删候选兜底）' % (raw,)}


def plan_fingerprint_matches(checkpoint, expected_fingerprint):
    """auto 恢复的计划指纹一致性辅助（D16 按冻结 parts 组装并计算期望指纹后传入比对）。

    checkpoint 接受完整 checkpoint dict（含 'generate' 键）或 generate doc 本身。
    存储指纹缺失/为空、或期望指纹为空 → False（与 batch_state.fingerprint_mismatch
    同一口径：不一致即拒绝，绝不放行空指纹）。纯函数，零副作用。
    """
    doc = checkpoint if isinstance(checkpoint, dict) else {}
    generate = doc.get('generate')
    if not isinstance(generate, dict):
        generate = doc
    stored = str(generate.get('fingerprint') or '')
    expected = str(expected_fingerprint or '')
    return bool(stored) and bool(expected) and stored == expected


def resumable_jobs(plan_doc):
    """v2 计划的待续叶作业清单（D10 冻结辅助：成功叶不重做，失败只补未成功叶）。

    返回叶作业（无 children）id 列表（按计划内登记序），状态 ∈ {queued, running, failed}：
    * succeeded 不返回——成功叶不重做（候选与完成标记已在库，绝不双写，§6）；
    * blocked 不返回——预算受阻属终态，续跑必须显式新计划（§6 不能用重试追加额度）；
    * superseded 不返回——已被替换，目标由替代作业认领（§4.3 旧作业不可再派发）；
    * split 父作业不是叶；失败叶返回（重做方式——重派发/重打包——由调用方定）。
    plan_doc 接受 checkpoint 形态 {'generate': doc} 或 generate doc 本身；
    非 dict / 非 schemaVersion=2 → []（legacy 计划没有 v2 叶状态可列）。
    """
    doc = plan_doc if isinstance(plan_doc, dict) else {}
    generate = doc.get('generate')
    if isinstance(generate, dict):
        doc = generate
    if not doc or _plan_schema_version(doc.get('schemaVersion')) != \
            batch_contracts.CHECKPOINT_SCHEMA_VERSION:
        return []
    jobs = doc.get('jobs') if isinstance(doc.get('jobs'), dict) else {}
    resumable = []
    for job_id, job in jobs.items():
        job = job if isinstance(job, dict) else {}
        if job.get('children'):
            continue
        if str(job.get('state') or '') in _RESUMABLE_LEAF_STATES:
            resumable.append(str(job_id))
    return resumable


@contextlib.contextmanager
def claim_for_resume(user_id, run_id):
    """直调 pipeline 前**显式接管**执行权（恢复入口/测试用；不经 submit 的同步路径）。

    复用 submit() 的轮换语义：进入时短写事务 `store.rotate_run_lease`（旧 worker 手里
    的执行权从此失配，晚结果在 update_run 的 lease 条件处被拒），并给**当前线程**登记
    专属 worker token——作用域内 `lease_of()` 解析到本次新 lease，stage/finish/content_tx
    全部按新执行权（带条件）生效；yield 出持有的 lease。与 begin_worker_scope(lease=None)
    的差别：后者进入时只**读取**当前 DB lease、不轮换（旧 worker 若仍持有同一 lease，
    其写入在新作用域内不会被拦）；claim_for_resume 进入即轮换，是真正的「接管」。
    退出语义与 begin_worker_scope 相同：只弹自己的登记；DB lease 仍是本次这一份才轮换
    作废（已被更新接管则不动继任者）。运行不存在时轮换返回 ''：作用域内不启用 lease
    条件写（后续 get_run 自会拒绝），退出时无需作废。
    """
    token = _new_worker_token()
    lease = _rotate_lease(user_id, run_id)
    with _pool_lock:
        _workers[token] = {'user': user_id, 'run': run_id, 'lease': lease}
    previous = _push_token(token)
    try:
        yield lease
    finally:
        _pop_token(previous)
        with _pool_lock:
            _workers.pop(token, None)
        if lease:
            _retire_lease(user_id, run_id, lease)
