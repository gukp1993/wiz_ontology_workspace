"""成功收尾状态守卫回归（R5-01：取消后不得被成功收尾覆盖）。

复刻第 5 轮验收报告 §4 的相邻竞态：worker 完成最后一次业务内容写入后、返回
wrapper 之前暂停，此刻 request_cancel 已提交（state=cancelled, cancel_requested=1），
随后 worker 正常返回触发 finish_success——修复前它只核对 lease，会把取消覆盖成
succeeded 并把任务推进到评审。修复口径（同报告 §4）：finish_success 在同一个
BEGIN IMMEDIATE 写事务内读行核对归属/lease/取消标记/当前状态，仅 running 允许
转成功；成功转换实际成立才推进任务阶段；_finish 同原则审计（终态一律不改写）。

六个场景（对应报告 §4 验收表，Event 固定时序，不依赖 sleep）：
1. 最后内容写入后、成功收尾前取消（无重试）：最终 cancelled、cancel_requested
   保留、任务不推进、已完成内容保留。
2. 成功先完成，再发取消请求：保持 succeeded，cancel_requested 不被置位。
3. 取消后重试 B，旧 A 再收尾：A 不修改 B 的状态、阶段、候选。
4. 正常成功 + 重复成功收尾：首次推进任务阶段，重复调用幂等、不重复写内容。
5. 取消后 worker 抛异常：失败收尾不把 cancelled 覆盖成 failed。
6. queued 直达成功被拒：只允许 running 收尾成功，拒绝时不动任务阶段。

「LLM 返回前取消由 content_tx 拦截」由 tests/test_ontology_build_late_write.py
（23 项）覆盖，本文件不重复。

隔离（AGENTS.md 测试隔离铁律）：每个场景独立临时根（WIZ_WORKBENCH_ROOT +
该根下 WIZ_DATABASE_URL），不访问网络、不连真实库；结束销毁全部临时根。

运行：python3 tests/test_ontology_build_finish_guard.py
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
sys.path.insert(0, str(REPO))

from workbench import storage  # noqa: E402
from workbench.ontology_build import runner  # noqa: E402
from workbench.storage import engine as sto  # noqa: E402
from workbench.storage import ontology_build as store  # noqa: E402

UID = 'finish-guard-owner'
WAIT_SECONDS = 15
JOIN_SECONDS = 25

PASSED = []
FAILED = []
SEQ = [0]
ROOTS = []


def _short(value, limit=300):
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = repr(value)
    return text if len(text) <= limit else text[:limit] + '…'


def check(cond, message, actual=None):
    SEQ[0] += 1
    if cond:
        PASSED.append(message)
        print('通过 %d) %s' % (SEQ[0], message))
        return True
    FAILED.append(message)
    print('[失败] %d) %s' % (SEQ[0], message))
    if actual is not None:
        print('  实际: ' + _short(actual))
    return False


def new_isolated_root(tag):
    root = Path(tempfile.mkdtemp(prefix='wiz_finish_guard_%s_' % tag))
    ROOTS.append(root)
    os.environ['WIZ_WORKBENCH_ROOT'] = str(root)
    os.environ['WIZ_DATABASE_URL'] = 'sqlite:///' + str(root / 'data' / 'workbench.sqlite3')
    sto.reset_engine()
    storage.mark_unready()
    storage.ensure_ready()
    if str(root) not in sto.resolve_url():
        raise AssertionError('隔离根未生效：%s' % sto.resolve_url())
    return root


def cleanup_roots():
    for root in ROOTS:
        shutil.rmtree(root, ignore_errors=True)
    del ROOTS[:]


# --- 种子与读取 ----------------------------------------------------------------------

def seed_task_run(kind='generate'):
    """建任务（推进到 generating，收尾守卫要核对任务阶段）+ 批次 + run。"""

    def body(conn):
        task_id = store.create_task(conn, UID, '收尾守卫回归 %s' % kind)
        store.touch_task(conn, task_id, UID, status='generating', stage_label='生成中')
        batch_id = store.create_batch(conn, task_id, UID, '', {})
        run_id, _lease = store.create_run(conn, task_id, UID, kind, {}, batch_id)
        return task_id, batch_id, run_id

    with sto.write_tx() as tx:
        return tx.run(body)


def read_run(run_id):
    with sto.read_connection() as conn:
        row = store.get_run(conn, run_id, UID)
    return dict(row) if row is not None else None


def read_task(task_id):
    with sto.read_connection() as conn:
        return store.get_task(conn, task_id, UID)


def read_candidates(task_id, batch_id):
    with sto.read_connection() as conn:
        return store.all_candidates(conn, task_id, UID, batch_id=batch_id)


def retry_path(run_id):
    """与 post_run_resume 相同的重试语义：回 queued、推进 attempt（轮换 lease）、清取消标记。"""

    def body(conn):
        row = store.get_run(conn, run_id, UID)
        store.update_run(conn, run_id, UID, state='queued', error='', retryable=False,
                         cancel_requested=False, attempt=int(row['attempt']) + 1)

    with sto.write_tx() as tx:
        tx.run(body)


def gated_job(task_id, batch_id, name, paused, release, fail_after=False, block=True,
              block_before_content=False):
    """worker 作业：stage 进 running → content_tx 写一条候选 →（可选）暂停在 Event 上。

    block=False 时不停留，直接返回（给 B 用）；block_before_content=True 时在
    **内容写入之前**暂停（复刻「A 阻塞在模型调用」的真实时序：取消后 A 的内容写
    与收尾都应被拒）；否则内容写入后暂停（内容在取消前已合法落库，应保留）。
    fail_after=True 时释放后抛异常（验证失败收尾不覆盖取消）。
    """
    def job(owner, run_id):
        runner.stage(owner, run_id, 'abstract', '收尾守卫测试阶段', {'done': 1, 'total': 2})
        if block and block_before_content:
            paused.set()
            if not release.wait(WAIT_SECONDS):
                raise AssertionError('gate 超时：release 未到达')

        def body(conn):
            store.create_candidate(conn, task_id, owner, batch_id,
                                   {'type': 'object', 'key': 'k-' + name, 'name': name,
                                    'definition': '收尾守卫回归候选',
                                    'evidenceStatus': 'supported', 'decision': 'include',
                                    'alignedKey': 'object:k-' + name})
        runner.content_tx(owner, run_id, body)
        if not block or block_before_content:
            return
        paused.set()
        if not release.wait(WAIT_SECONDS):
            raise AssertionError('gate 超时：release 未到达')
        if fail_after:
            raise RuntimeError('A 收尾前异常（模拟收尾竞态）')
    return job


# --- 场景 ----------------------------------------------------------------------------

def scenario_cancel_between_content_and_finish():
    """场景1：最后内容写入后、成功收尾前取消（无重试）→ 保持 cancelled，任务不推进。"""
    new_isolated_root('s1')
    task_id, batch_id, run_id = seed_task_run()
    paused, release = threading.Event(), threading.Event()
    thread = runner.submit(UID, run_id, gated_job(task_id, batch_id, '取消前已写入', paused, release))
    check(paused.wait(WAIT_SECONDS), '场景1 worker 到达暂停点')
    runner.request_cancel(UID, run_id)
    row = read_run(run_id)
    check(row['state'] == 'cancelled' and int(row['cancel_requested'] or 0) == 1,
          '场景1 取消已提交（cancelled + 标记）', row)
    release.set()
    thread.join(JOIN_SECONDS)
    check(not thread.is_alive(), '场景1 worker 已退出')
    row = read_run(run_id)
    check(row['state'] == 'cancelled', '场景1 最终保持 cancelled（不被成功收尾覆盖）', row)
    check(int(row['cancel_requested'] or 0) == 1, '场景1 cancel_requested 保留（不被抹平）', row)
    # 08 §12.3（fca1f2b 冻结）：取消时任务由 generating 回退 scope；收尾不得把它推到 review。
    check(read_task(task_id)['status'] == 'scope', '场景1 取消回退 scope 且未被收尾推进（08 §12.3）',
          read_task(task_id))
    check(len(read_candidates(task_id, batch_id)) == 1, '场景1 已完成内容保留（1 条候选）',
          read_candidates(task_id, batch_id))


def scenario_success_then_cancel_request():
    """场景2：成功先完成，再发取消请求 → 保持 succeeded，取消标记不被置位。"""
    new_isolated_root('s2')
    task_id, batch_id, run_id = seed_task_run()
    thread = runner.submit(UID, run_id, gated_job(task_id, batch_id, '先成功', threading.Event(),
                                                  threading.Event(), block=False))
    thread.join(JOIN_SECONDS)
    row = read_run(run_id)
    check(row['state'] == 'succeeded', '场景2 首次成功', row)
    check(read_task(task_id)['status'] == 'review', '场景2 任务推进到评审', read_task(task_id))
    runner.request_cancel(UID, run_id)
    row = read_run(run_id)
    check(row['state'] == 'succeeded', '场景2 保持 succeeded（实际已完成状态）', row)
    check(int(row['cancel_requested'] or 0) == 0, '场景2 取消标记未被置位', row)


def scenario_cancel_retry_b_then_a_finishes():
    """场景3：取消后重试 B，旧 A 再收尾 → A 不修改 B 的状态、阶段、候选。

    A 在内容写入**之前**被门住（复刻真实「阻塞在模型调用」时序）：取消与重试后，
    A 释放时其内容写与成功收尾都必须被拒，库里只有 B 的结果。
    """
    new_isolated_root('s3')
    task_id, batch_id, run_id = seed_task_run()
    a_paused, a_release = threading.Event(), threading.Event()
    a_thread = runner.submit(UID, run_id, gated_job(task_id, batch_id, 'A旧结果', a_paused, a_release,
                                                    block_before_content=True))
    check(a_paused.wait(WAIT_SECONDS), '场景3 A 到达暂停点（尚未写任何内容）')
    runner.request_cancel(UID, run_id)
    retry_path(run_id)
    b_thread = runner.submit(UID, run_id, gated_job(task_id, batch_id, 'B新结果',
                                                    threading.Event(), threading.Event(), block=False))
    b_thread.join(JOIN_SECONDS)
    check(read_run(run_id)['state'] == 'succeeded', '场景3 B 完成成功', read_run(run_id))
    a_release.set()
    a_thread.join(JOIN_SECONDS)
    check(not a_thread.is_alive(), '场景3 A 已退出')
    row = read_run(run_id)
    names = sorted(c['name'] for c in read_candidates(task_id, batch_id))
    check(row['state'] == 'succeeded', '场景3 终态仍为 B 的 succeeded', row)
    check(names == ['B新结果'], '场景3 候选只有 B 的（A 的内容写与收尾都被拒）', names)
    check(read_task(task_id)['status'] == 'review', '场景3 任务阶段保持 review（A 未改阶段）',
          read_task(task_id))


def scenario_normal_and_duplicate_finish():
    """场景4：正常成功推进任务阶段；重复成功收尾幂等，不重复产生业务内容。"""
    new_isolated_root('s4')
    task_id, batch_id, run_id = seed_task_run()
    thread = runner.submit(UID, run_id, gated_job(task_id, batch_id, '正常成功', threading.Event(),
                                                  threading.Event(), block=False))
    thread.join(JOIN_SECONDS)
    row = read_run(run_id)
    check(row['state'] == 'succeeded', '场景4 首次成功', row)
    check(read_task(task_id)['status'] == 'review', '场景4 任务推进到评审', read_task(task_id))
    before = read_candidates(task_id, batch_id)
    runner.finish_success(UID, run_id)  # 重复收尾：必须幂等
    row = read_run(run_id)
    check(row['state'] == 'succeeded', '场景4 重复收尾后仍 succeeded', row)
    check(read_candidates(task_id, batch_id) == before, '场景4 重复收尾不重复产生业务内容')
    check(read_task(task_id)['status'] == 'review', '场景4 重复收尾后任务阶段不变', read_task(task_id))


def scenario_failure_does_not_overwrite_cancel():
    """场景5：取消后 worker 抛异常 → 失败收尾不得把 cancelled 覆盖成 failed。"""
    new_isolated_root('s5')
    task_id, batch_id, run_id = seed_task_run()
    paused, release = threading.Event(), threading.Event()
    thread = runner.submit(UID, run_id, gated_job(task_id, batch_id, '取消后异常', paused, release,
                                                  fail_after=True))
    check(paused.wait(WAIT_SECONDS), '场景5 worker 到达暂停点')
    runner.request_cancel(UID, run_id)
    release.set()
    thread.join(JOIN_SECONDS)
    row = read_run(run_id)
    check(row['state'] == 'cancelled', '场景5 最终保持 cancelled（不被 failed 覆盖）', row)
    check(row['error'] == '已取消', '场景5 错误文案仍是取消原文', row)


def scenario_queued_direct_finish_rejected():
    """场景6：只允许 running 成功收尾——queued 直达成功被拒，任务阶段不动。"""
    new_isolated_root('s6')
    task_id, batch_id, run_id = seed_task_run()
    runner.finish_success(UID, run_id)  # 未经 stage 的 queued run（非 worker 线程直调）
    row = read_run(run_id)
    check(row['state'] == 'queued', '场景6 queued 不被直达成功', row)
    check(read_task(task_id)['status'] == 'generating', '场景6 任务阶段未被推进', read_task(task_id))


SCENARIOS = (
    ('cancel_between_content_and_finish', scenario_cancel_between_content_and_finish),
    ('success_then_cancel_request', scenario_success_then_cancel_request),
    ('cancel_retry_b_then_a_finishes', scenario_cancel_retry_b_then_a_finishes),
    ('normal_and_duplicate_finish', scenario_normal_and_duplicate_finish),
    ('failure_does_not_overwrite_cancel', scenario_failure_does_not_overwrite_cancel),
    ('queued_direct_finish_rejected', scenario_queued_direct_finish_rejected),
)


def main():
    for name, func in SCENARIOS:
        print('--- %s ---' % name)
        try:
            func()
        except Exception as exc:  # 场景自身异常不吞：计失败并打印堆栈
            traceback.print_exc()
            check(False, '场景 %s 自身异常：%s' % (name, type(exc).__name__), str(exc))
        print('')
    cleanup_roots()
    total = len(PASSED) + len(FAILED)
    print('========== 汇总 ==========')
    print('通过 %d / %d' % (len(PASSED), total))
    for name in FAILED:
        print('  失败: ' + name)
    if total == 0:
        print('[错误] 未执行任何断言——不算通过')
        return 2
    return 0 if not FAILED else 1


if __name__ == '__main__':
    sys.exit(main())
