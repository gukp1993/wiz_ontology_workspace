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
import threading
import traceback

from workbench import auth
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
