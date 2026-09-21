"""runner 执行权 fencing 并发回归（R3-01：取消+重试时旧 worker 不得污染新 attempt）。

复刻第三轮验收证据 `文档/需求/20260920_从物料自动构建本体/验收证据_第三轮/取消重试并发.py`
的真实时序（真实 SQLite 临时根 + 真实 runner 线程 + threading.Event 控时序），但断言**修复后的
正确行为**：

1. 取消+重试并发：A 等待中取消并按 post_run_resume 路径把 run 置回 queued、attempt 递增、
   再 submit B。释放 A 后断言：
   * A 线程里 `lease_of` 仍是 A 自己的旧 lease（不是 B 的）；
   * A 的 `stage()` 抛 Cancelled，晚写入不被接受（stage 列不是 A 写的内容）；
   * A 的终态写回（_finish 的 cancelled / finish_success 的 succeeded）被丢弃，
     run.state 仍是 B 的 queued（既不 cancelled 也不 failed 也不 succeeded，error 为空）；
   * A 退出只清自己的登记：B 等待期间 `active_runs()` 仍包含该 run、`worker_slots()` 扣 1；
   * 释放 B 后由 B 写成 succeeded，B 的阶段推进生效。
2. 顺序 submit 同一 run：两次的执行令牌（worker token 与 lease）必然不同，不共用。
3. `begin_worker_scope()`：直调 pipeline 的脚本可用它取得/作废执行权；显式传入已被轮换的
   旧 lease 时，作用域内的写入一律按失配丢弃。
4. 收尾：worker 正常结束后 `_workers` 无残留，`active_runs()` 为空。

隔离（AGENTS.md 测试隔离铁律）：自建临时根与该根下的 SQLite，只写临时目录；
不访问网络、不连真实库、不依赖其它测试文件。

运行：python3 tests/test_ontology_build_runner_isolation.py
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TMP = Path(tempfile.mkdtemp(prefix='wiz_runner_isolation_'))
os.environ['WIZ_WORKBENCH_ROOT'] = str(TMP)
os.environ['WIZ_DATABASE_URL'] = 'sqlite:///' + str(TMP / 'data' / 'workbench.sqlite3')
sys.path.insert(0, str(REPO))

from workbench.ontology_build import protocol, runner  # noqa: E402
from workbench.paths import DATA_ROOT  # noqa: E402
from workbench.storage import engine as sto  # noqa: E402
from workbench.storage import ontology_build as store  # noqa: E402

UID = 'runner-isolation-owner'
WAIT_SECONDS = 15
JOIN_SECONDS = 25

PASSED = []
FAILED = []
SEQ = [0]


def _short(value, limit=400):
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = repr(value)
    return text if len(text) <= limit else text[:limit] + '…'


def check(cond, message, actual=None, expected=None):
    SEQ[0] += 1
    if cond:
        PASSED.append(message)
        print('通过 %d) %s' % (SEQ[0], message))
        return True
    FAILED.append(message)
    print('[失败] %d) %s' % (SEQ[0], message))
    if expected is not None:
        print('  预期: ' + _short(expected))
    if actual is not None:
        print('  实际: ' + _short(actual))
    return False


def _new_run(kind='scan'):
    """建任务 + run（真实存储层，短写事务）。"""
    def body(conn):
        task_id = store.create_task(conn, UID, 'runner 隔离回归任务')
        run_id, lease = store.create_run(conn, task_id, UID, kind, {})
        return task_id, run_id, lease
    with sto.write_tx() as tx:
        return tx.run(body)


def _row(run_id):
    with sto.read_connection() as conn:
        row = store.get_run(conn, run_id, UID)
    return dict(row) if row is not None else None


def _tokens_for(run_id):
    """该 run 当前登记在册的 worker token（只读快照，用于断言无残留/不共用）。"""
    with runner._pool_lock:
        return sorted(token for token, record in runner._workers.items()
                      if str(record.get('run')) == str(run_id))


def race_scenario(mode):
    """取消 + 重试并发：A 被释放后不得写入、不得删 B 的登记，最终由 B 写成功。

    mode='raise'：A 的 stage 抛 Cancelled 后继续向上抛 → wrapper 走 _finish('cancelled')；
    mode='swallow'：A 吞掉 Cancelled 正常返回 → wrapper 走 finish_success('succeeded')
    （验收报告实测被接受的正是这一条晚结果）。
    """
    _task_id, run_id, _lease = _new_run()
    out = {'mode': mode}
    started = {'a': threading.Event(), 'b': threading.Event()}
    go = {'a': threading.Event(), 'b': threading.Event()}

    def job_a(user, run):
        out['a_lease_in_thread'] = runner.lease_of(user, run)
        started['a'].set()
        go['a'].wait(WAIT_SECONDS)
        out['a_lease_after_b_submit'] = runner.lease_of(user, run)
        try:
            runner.stage(user, run, 'OLD_WORKER_WRITE', '旧 worker 的晚写入')
            out['a_stage_accepted'] = True
        except runner.Cancelled as exc:
            out['a_stage_accepted'] = False
            out['a_stage_cancel'] = str(exc)
            if mode == 'raise':
                raise
        out['a_job_returned'] = True

    def job_b(user, run):
        out['b_lease_in_thread'] = runner.lease_of(user, run)
        started['b'].set()
        go['b'].wait(WAIT_SECONDS)
        runner.stage(user, run, 'NEW_WORKER_WRITE', '新 worker 正常推进')
        out['b_stage_ok'] = True

    thread_a = runner.submit(UID, run_id, job_a)
    out['a_is_thread'] = isinstance(thread_a, threading.Thread)
    if not started['a'].wait(WAIT_SECONDS):
        raise AssertionError('旧 worker A 未启动')

    runner.request_cancel(UID, run_id)  # 用户取消
    with sto.write_tx() as tx:  # post_run_resume 的重试路径：queued + attempt 递增
        tx.run(lambda conn: store.update_run(conn, run_id, UID, state='queued', error='',
                                             retryable=False, cancel_requested=False,
                                             attempt=2))
    out['lease_after_retry'] = str(_row(run_id)['lease_token'])

    thread_b = runner.submit(UID, run_id, job_b)  # 同一 run 的新 worker
    out['b_is_thread'] = isinstance(thread_b, threading.Thread)
    if not started['b'].wait(WAIT_SECONDS):
        raise AssertionError('新 worker B 未启动')
    out['lease_after_b_submit'] = str(_row(run_id)['lease_token'])
    out['active_while_b_waiting'] = runner.active_runs()
    out['slots_while_b_waiting'] = runner.worker_slots()

    go['a'].set()
    thread_a.join(JOIN_SECONDS)
    out['a_alive'] = thread_a.is_alive()
    row = _row(run_id)
    out['state_after_a_exit'] = row['state']
    out['stage_after_a_exit'] = row['stage']
    out['error_after_a_exit'] = row['error']
    out['active_after_a_exit'] = runner.active_runs()

    go['b'].set()
    thread_b.join(JOIN_SECONDS)
    out['b_alive'] = thread_b.is_alive()
    row = _row(run_id)
    out['final_state'] = row['state']
    out['final_stage'] = row['stage']
    out['active_after_all'] = runner.active_runs()
    out['tokens_residue'] = _tokens_for(run_id)
    return run_id, out


def scenario_cancel_retry_race():
    for mode in ('raise', 'swallow'):
        run_id, out = race_scenario(mode)
        tag = '取消+重试并发[%s]' % mode
        check(out['a_is_thread'] and out['b_is_thread'],
              '%s：submit 返回 threading.Thread（调用方可 join）' % tag,
              actual=(out['a_is_thread'], out['b_is_thread']))
        check(bool(out.get('a_lease_in_thread')) and out['a_lease_in_thread'] != '',
              '%s：A 在自己的线程里持有真实 lease（非空执行权，不是靠空 lease 逃过条件写）' % tag,
              actual=out.get('a_lease_in_thread'))
        check(out['a_lease_after_b_submit'] == out['a_lease_in_thread']
              and out['a_lease_after_b_submit'] != out['lease_after_b_submit'],
              '%s：B 接管后 A 仍读到自己那份旧 lease（绝不读到 B 的）' % tag,
              expected={'a_lease': out.get('a_lease_in_thread'),
                        'db_lease_after_b': out.get('lease_after_b_submit')},
              actual={'a_lease_after_b_submit': out.get('a_lease_after_b_submit')})
        check(out['a_lease_in_thread'] != out['lease_after_retry']
              and out['lease_after_retry'] != out['lease_after_b_submit'],
              '%s：重试轮换与 submit 轮换都让旧 lease 作废' % tag,
              actual=(out.get('a_lease_in_thread'), out.get('lease_after_retry'),
                      out.get('lease_after_b_submit')))
        check(out.get('a_stage_accepted') is False,
              '%s：旧 worker 的 stage 抛 Cancelled，晚写入不被接受' % tag,
              actual={'a_stage_accepted': out.get('a_stage_accepted'),
                      'cancel': out.get('a_stage_cancel')})
        check(out.get('stage_after_a_exit') not in ('OLD_WORKER_WRITE',)
              and out.get('state_after_a_exit') == 'queued'
              and out.get('error_after_a_exit') == '',
              '%s：A 退出后 run 仍是继承来的 queued（终态写回既不 cancelled/failed 也不 succeeded）' % tag,
              expected={'state': 'queued', 'stage': '', 'error': ''},
              actual={'state': out.get('state_after_a_exit'), 'stage': out.get('stage_after_a_exit'),
                      'error': out.get('error_after_a_exit')})
        check((UID, run_id) in out.get('active_while_b_waiting', [])
              and out.get('slots_while_b_waiting') == protocol.RUN_WORKERS - 1,
              '%s：B 等待期间 active_runs 仍含该 run（同一 run 只占一格）' % tag,
              expected={'active': [(UID, run_id)], 'slots': protocol.RUN_WORKERS - 1},
              actual={'active': out.get('active_while_b_waiting'),
                      'slots': out.get('slots_while_b_waiting')})
        check(not out.get('a_alive') and (UID, run_id) in out.get('active_after_a_exit', []),
              '%s：A 退出的 finally 只清自己的登记，不删 B 的' % tag,
              expected={'a_alive': False, 'active': [(UID, run_id)]},
              actual={'a_alive': out.get('a_alive'), 'active': out.get('active_after_a_exit')})
        check(not out.get('b_alive') and out.get('final_state') == 'succeeded'
              and out.get('final_stage') == 'NEW_WORKER_WRITE' and out.get('b_stage_ok'),
              '%s：释放 B 后由 B 写成 succeeded，且 B 的阶段写入生效' % tag,
              expected={'state': 'succeeded', 'stage': 'NEW_WORKER_WRITE'},
              actual={'state': out.get('final_state'), 'stage': out.get('final_stage')})
        check(out.get('active_after_all') == [] and out.get('tokens_residue') == [],
              '%s：worker 结束后登记无残留（active_runs 为空、该 run 无 token）' % tag,
              actual={'active': out.get('active_after_all'),
                      'tokens': out.get('tokens_residue')})
        print('  [%s] 关键快照: %s' % (mode, _short({
            'a_lease': out.get('a_lease_in_thread'),
            'lease_after_retry': out.get('lease_after_retry'),
            'lease_after_b_submit': out.get('lease_after_b_submit'),
            'state_after_a_exit': out.get('state_after_a_exit'),
            'stage_after_a_exit': out.get('stage_after_a_exit'),
            'final': (out.get('final_state'), out.get('final_stage')),
        })))


def scenario_sequential_submits_rotate():
    _task_id, run_id, _lease = _new_run()
    out = {'run_id': run_id}

    def make(phase):
        def job(user, run):
            out[phase + '_lease'] = runner.lease_of(user, run)
            out[phase + '_token'] = str(getattr(runner._worker_local, 'token', '') or '')
            out[phase + '_registered'] = _tokens_for(run)
            out[phase + '_db_lease'] = str(_row(run)['lease_token'])
        return job

    first = runner.submit(UID, run_id, make('first'))
    out['first_is_thread'] = isinstance(first, threading.Thread)
    first.join(JOIN_SECONDS)
    out['workers_after_first'] = dict(runner._workers)
    second = runner.submit(UID, run_id, make('second'))
    out['second_is_thread'] = isinstance(second, threading.Thread)
    second.join(JOIN_SECONDS)
    out['workers_after_second'] = dict(runner._workers)
    out['lease_of_main_thread'] = runner.lease_of(UID, run_id)
    out['active_after_all'] = runner.active_runs()

    check(not first.is_alive() and not second.is_alive(),
          '顺序 submit：两个 worker 都已结束', actual=(first.is_alive(), second.is_alive()))
    check(out['first_is_thread'] and out['second_is_thread'],
          '顺序 submit：submit 始终返回 threading.Thread',
          actual=(out['first_is_thread'], out['second_is_thread']))
    check(bool(out.get('first_lease')) and bool(out.get('second_lease'))
          and out['first_lease'] != out['second_lease'],
          '顺序 submit 同一 run：两次的 lease 必然不同（每次 submit 显式轮换执行权）',
          actual=(out.get('first_lease'), out.get('second_lease')))
    check(bool(out.get('first_token')) and bool(out.get('second_token'))
          and out['first_token'] != out['second_token'],
          '顺序 submit 同一 run：两次的 worker token 必然不同（不共用登记）',
          actual=(out.get('first_token'), out.get('second_token')))
    check(out.get('first_lease') == out.get('first_db_lease')
          and out.get('second_lease') == out.get('second_db_lease'),
          '每个 worker 在自己线程里读到的 lease 就是当时 DB 里的 lease',
          expected=(out.get('first_db_lease'), out.get('second_db_lease')),
          actual=(out.get('first_lease'), out.get('second_lease')))
    check(out.get('first_registered') == [out.get('first_token')]
          and out.get('second_registered') == [out.get('second_token')],
          'worker 运行期间登记只有自己一个 token（不叠加、不覆盖旧 worker 的登记）',
          actual=(out.get('first_registered'), out.get('second_registered')))
    check(out.get('workers_after_first') == {} and out.get('workers_after_second') == {}
          and out.get('active_after_all') == [],
          'worker 正常结束后 _workers 无残留',
          actual=(out.get('workers_after_first'), out.get('workers_after_second')))
    check(out.get('lease_of_main_thread') == '',
          '非 worker 线程没有执行权：lease_of 返回空（不启用 lease 条件写，兼容直调路径）',
          actual=out.get('lease_of_main_thread'))


def scenario_begin_worker_scope():
    _task_id, run_id, _lease = _new_run()
    out = {'run_id': run_id}
    out['lease_before'] = str(_row(run_id)['lease_token'])

    with runner.begin_worker_scope(UID, run_id) as held:
        out['held'] = held
        out['lease_in_scope'] = runner.lease_of(UID, run_id)
        out['token_in_scope'] = str(getattr(runner._worker_local, 'token', '') or '')
        out['tokens_in_scope'] = _tokens_for(run_id)
        runner.stage(UID, run_id, 'SCOPE_STAGE', '直调 pipeline 的阶段推进')
        out['stage_in_scope'] = _row(run_id)['stage']
    out['lease_after_scope'] = str(_row(run_id)['lease_token'])
    out['lease_of_after_scope'] = runner.lease_of(UID, run_id)
    out['workers_after_scope'] = dict(runner._workers)

    with runner.begin_worker_scope(UID, run_id, lease='stale-scope-token') as stale:
        out['stale_held'] = stale
        out['lease_of_stale'] = runner.lease_of(UID, run_id)
        try:
            runner.stage(UID, run_id, 'STALE_SCOPE_WRITE', '过期执行权的晚写入')
            out['stale_stage_accepted'] = True
        except runner.Cancelled:
            out['stale_stage_accepted'] = False
    out['stage_after_stale'] = _row(run_id)['stage']
    out['active_after_all'] = runner.active_runs()

    check(out.get('held') == out.get('lease_before') != ''
          and out.get('lease_in_scope') == out.get('held'),
          'begin_worker_scope(lease=None)：本次执行权就是进入时的 DB lease',
          expected=out.get('lease_before'), actual=(out.get('held'), out.get('lease_in_scope')))
    check(out.get('tokens_in_scope') == [out.get('token_in_scope')]
          and out.get('stage_in_scope') == 'SCOPE_STAGE',
          'begin_worker_scope：作用域内的 stage 按自己的执行权命中并写入',
          actual={'tokens': out.get('tokens_in_scope'), 'stage': out.get('stage_in_scope')})
    check(out.get('lease_after_scope') not in ('', out.get('held'))
          and out.get('lease_of_after_scope') == '',
          'begin_worker_scope 退出：自己的执行权被轮换作废，线程本地已清空',
          expected={'rotated_away_from': out.get('held')},
          actual={'lease_after_scope': out.get('lease_after_scope'),
                  'lease_of_after_scope': out.get('lease_of_after_scope')})
    check(out.get('workers_after_scope') == {},
          'begin_worker_scope 退出：只弹自己的登记', actual=out.get('workers_after_scope'))
    check(out.get('stale_held') == 'stale-scope-token'
          and out.get('lease_of_stale') == 'stale-scope-token'
          and out.get('stale_stage_accepted') is False
          and out.get('stage_after_stale') == 'SCOPE_STAGE',
          '显式传入已被轮换的旧 lease：作用域内写入按失配丢弃（stage 不被改写）',
          actual={'stale_stage_accepted': out.get('stale_stage_accepted'),
                  'stage': out.get('stage_after_stale')})
    check(out.get('active_after_all') == [],
          'begin_worker_scope 全部退出后 active_runs 为空', actual=out.get('active_after_all'))


def main():
    if str(TMP) not in str(DATA_ROOT) or str(TMP) not in sto.resolve_url():
        print('[错误] 隔离根未生效：DATA_ROOT=%s url=%s' % (DATA_ROOT, sto.resolve_url()))
        return 2
    print('隔离根: %s' % TMP)
    print('库地址: %s' % sto.resolve_url())
    if not sto.schema_status()[0]:
        sto.initialize()
    scenario_cancel_retry_race()
    scenario_sequential_submits_rotate()
    scenario_begin_worker_scope()
    return 0


def shutdown():
    """收尾：确认没有遗留 worker 线程，再销毁临时根（绝不碰真实 ontology/）。"""
    leftover = [t for t in threading.enumerate() if t.name.startswith('build-run-')]
    for thread in leftover:
        thread.join(10)
    alive = [t.name for t in leftover if t.is_alive()]
    if alive:
        FAILED.append('worker 线程未退出: %s' % alive)
    if runner._workers:
        FAILED.append('worker 登记残留: %s' % sorted(runner._workers))
    sto.reset_engine()
    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == '__main__':
    code = 1
    try:
        code = main()
    except Exception as exc:
        traceback.print_exc()
        FAILED.append('未预期异常: %s: %s' % (type(exc).__name__, exc))
    finally:
        shutdown()
    total = len(PASSED) + len(FAILED)
    print('\n========== 汇总 ==========')
    print('通过 %d / %d' % (len(PASSED), total))
    for name in FAILED:
        print('  失败: ' + name)
    if total == 0:
        print('[错误] 未执行任何断言——不算通过')
        sys.exit(2)
    sys.exit(0 if code == 0 and not FAILED else 1)
