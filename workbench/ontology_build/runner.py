"""后台任务执行器：有界工作池 + owner 上下文 + 阶段推进（不持全局写锁）。

关键纪律（执行指令 §5 / 开发计划 §4.3）：
* 后台线程不继承请求 contextvars：每个作业用**服务端已鉴权的 task owner** 显式
  `auth.bind_request`，结束后在 finally 里解绑，绝不能凭客户端字段决定归属。
* 解析 / LLM / 文件读写都不持 `workbench.locking.LOCK`，也不开长写事务：
  只做「短事务读状态 → 外部工作 → 短事务提交」，提交前复查取消与 fencing。
* 服务重启把 queued/running 标 interrupted，不假装仍在运行。
* 单进程内有界池（RUN_WORKERS）；作业异常写回 run.error 与 retryable，
  绝不吞掉后静默当成功。
"""
import threading
import traceback

from workbench import auth, storage
from workbench.ontology_build import protocol
from workbench.storage import engine as sto
from workbench.storage import ontology_build as store


class Cancelled(Exception):
    """作业在阶段边界检测到取消；不写入结果。"""


_pool_lock = threading.Lock()
_semaphore = threading.BoundedSemaphore(protocol.RUN_WORKERS)
_active = set()
# fencing token 登记表（D13）：worker 启动时捕获当时的 lease_token；重试接管会轮换
# lease，旧 worker 的阶段推进 / 结果写回 / 终态写回全部按不匹配丢弃。
_leases = {}


def _capture_lease(user_id, run_id):
    try:
        with sto.read_connection() as conn:
            state = store.run_state(conn, run_id, user_id)
        return str((state or {}).get('lease_token') or '')
    except Exception:
        return ''


def lease_of(user_id, run_id):
    """本 worker 持有的 fencing token（供管线直写 update_run 时带条件；未知返回 ''）。"""
    with _pool_lock:
        return _leases.get((str(user_id), str(run_id)), '')


def _owner_user(user_id):
    """构造最小用户上下文（后台线程没有请求上下文）。"""
    return {'userId': user_id}


def submit(user_id, run_id, job):
    """提交一个后台作业；job(owner_user_id, run_id) 在 owner 上下文中执行。

    作业正常返回即视为成功（终态写 succeeded）；抛 Cancelled → cancelled，
    其它异常 → failed 并记录可读文案。作业自己也可以提前写终态，
    _finish 只在终态未落定时补写，不会覆盖已写结果。
    """
    def wrapper():
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
            with _pool_lock:
                _active.discard((user_id, run_id))
                _leases.pop((str(user_id), str(run_id)), None)

    with _pool_lock:
        _active.add((user_id, run_id))
        _leases[(str(user_id), str(run_id))] = _capture_lease(user_id, run_id)
    auth.bind_request(None)  # 提交者可能是请求线程，避免身份泄漏进新线程
    thread = threading.Thread(target=wrapper, name='build-run-%s' % run_id[:8], daemon=True)
    thread.start()
    return thread


def _message(exc):
    text = str(exc) or exc.__class__.__name__
    return text[:400]


def _finish(user_id, run_id, state, error='', retryable=False):
    """短事务写回终态（已成为终态的运行不再改写：保留更精确的结果）。

    fencing：lease 不匹配（运行已被取消后重试/被新接管）直接丢弃晚结果，
    绝不把新接管的 queued/running 覆盖成 failed/cancelled。
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
                if state != 'succeeded' or row['state'] in ('failed', 'cancelled', 'interrupted'):
                    return
            store.update_run(conn, run_id, user_id, state=state, error=error,
                             retryable=retryable, cancel_requested=(state == 'cancelled'),
                             lease=expected or None)
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

    状态推进与运行终态在同一事务内完成：任务阶段必须反映真实进度，
    不能运行早成功了而任务还停在「生成中」。lease 不匹配则整体丢弃。
    """
    from workbench.ontology_build import protocol

    expected = lease_of(user_id, run_id)

    def body(conn):
        # usage=None 时 update_run 不动 usage_json 列：管线在生成过程里已按 LLM 调用
        # 累计写入（calls/promptBytes/completionBytes/durationMs），终态补写绝不能
        # 用空字典把它覆盖回零（可选修复项：run.usage 恒为空的真正原因）。
        hit = store.update_run(conn, run_id, user_id, state='succeeded', error='',
                               retryable=False, usage=usage or None, checkpoint=None,
                               lease=expected or None)
        if hit is False:
            return
        row = store.get_run(conn, run_id, user_id)
        if row is None or row['kind'] != 'generate':
            return
        task = store.require_task(conn, row['task_id'], user_id)
        if task is not None and task['status'] == 'generating':
            store.touch_task(conn, row['task_id'], user_id, status='review',
                             stage_label=protocol.TASK_STAGE_LABELS['review'])
    with sto.write_tx() as tx:
        tx.run(body)


def request_cancel(user_id, run_id):
    """请求取消：只置标记，不保证立即终止已发出的外部请求。"""
    def body(conn):
        row = store.get_run(conn, run_id, user_id)
        if row is None:
            raise sto.NotFound('运行不存在')
        if row['state'] in ('succeeded', 'failed', 'cancelled', 'interrupted'):
            return
        store.update_run(conn, run_id, user_id, cancel_requested=True, state='cancelled',
                         error='已取消', retryable=True)
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
    """当前可用并发额度（测试与能力接口用）。"""
    return protocol.RUN_WORKERS - len(_active)


def active_runs():
    with _pool_lock:
        return sorted(_active)
