"""R3-01 ～ R3-04 修复后复验（断言正确行为，不是缺陷复现）。

验收报告 B.5 要求：缺陷复现脚本在修复后需转为「断言正确行为」的回归。
本文件与同目录三份复现脚本一一对应，但**断言的是修复后的正确结果**，
可单命令对照报告编号复验：

    .venv/bin/python '文档/需求/20260920_从物料自动构建本体/验收证据_第三轮/复验_修复后回归.py'

覆盖：
* R3-01 取消重试并发：旧 worker 晚写入必须被拒，且不得清掉新 worker 的执行登记；
* R3-02 排除继承：人工排除跨批次持续保护，直到用户显式重新纳入；
* R3-03 合并引用：合并别名贯通属性/规则/动作，非空宿主解析不到必须阻断；
* R3-04 时序编辑协议：平铺 payload 被接受，旧嵌套 payload 被拒且给出可读纠正提示。

隔离：每次运行自建临时数据根（WIZ_WORKBENCH_ROOT + 该根下 SQLite），结束清理；
不访问网络、不连真实库、不碰 18871 / 18765，也不读写真实 ontology 数据。
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO))

RESULTS = []


def check(name, ok, detail=''):
    RESULTS.append((name, bool(ok)))
    print(('  PASS ' if ok else '  FAIL ') + name + (('  -> ' + str(detail)[:400]) if not ok else ''))


class IsolatedRoot:
    """临时数据根；with 块内所有 storage 访问都落在它下面。"""

    def __enter__(self):
        self._previous = {key: os.environ.get(key) for key in
                          ('WIZ_WORKBENCH_ROOT', 'WIZ_DATABASE_URL')}
        self.path = Path(tempfile.mkdtemp(prefix='wiz_r3_recheck_'))
        os.environ['WIZ_WORKBENCH_ROOT'] = str(self.path)
        os.environ['WIZ_DATABASE_URL'] = 'sqlite:///' + str(self.path / 'data' / 'workbench.sqlite3')
        from workbench import storage
        storage.ensure_ready()
        return self.path

    def __exit__(self, *exc):
        from workbench.storage import engine as sto
        sto.reset_engine()
        from workbench import storage
        storage.mark_unready()
        for key, value in self._previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(self.path, ignore_errors=True)
        return False


# ── R3-01：取消重试并发（真实 runner 线程 + 真实 SQLite） ───────────────────────
def check_r3_01():
    print('R3-01 取消后重试：旧 worker 晚写入必须被拒、新 worker 登记必须保留')
    from workbench.storage import engine as sto
    from workbench.storage import ontology_build as store
    from workbench.ontology_build import runner

    uid = 'r3-01-owner'
    with sto.write_tx() as tx:
        def setup(conn):
            task_id = store.create_task(conn, uid, 'r3-01')
            run_id, _lease = store.create_run(conn, task_id, uid, 'scan', {})
            return run_id
        run_id = tx.run(setup)

    a_started, b_started = threading.Event(), threading.Event()
    a_go, b_go = threading.Event(), threading.Event()
    seen = {}

    def worker_a(user, run):
        a_started.set()
        a_go.wait(10)
        seen['a_lease'] = runner.lease_of(user, run)
        try:
            runner.stage(user, run, 'OLD_WORKER_WRITE', '旧 worker 的晚写入')
            seen['a_accepted'] = True
        except runner.Cancelled:
            seen['a_accepted'] = False

    def worker_b(user, run):
        b_started.set()
        b_go.wait(10)
        runner.stage(user, run, 'NEW_WORKER_WRITE', '新 worker 的正常写入')
        seen['b_lease'] = runner.lease_of(user, run)

    thread_a = runner.submit(uid, run_id, worker_a)
    assert a_started.wait(5), 'worker A 未启动'
    runner.request_cancel(uid, run_id)
    with sto.write_tx() as tx:                      # 模拟 post_run_resume 的重试路径
        tx.run(lambda conn: store.update_run(conn, run_id, uid, state='queued', attempt=2,
                                             cancel_requested=False))
    thread_b = runner.submit(uid, run_id, worker_b)
    assert b_started.wait(5), 'worker B 未启动'
    a_go.set()
    thread_a.join(6)
    with sto.read_connection() as conn:
        row = store.get_run(conn, run_id, uid)
        snapshot = {'state': row['state'], 'stage': row['stage'], 'lease': row['lease_token']}
    active = runner.active_runs()
    b_go.set()
    thread_b.join(6)
    with sto.read_connection() as conn:
        final = store.get_run(conn, run_id, uid)

    check('R3-01 旧 worker 的晚写入被拒绝', seen.get('a_accepted') is False, seen)
    check('R3-01 旧 worker 未污染 stage', snapshot['stage'] != 'OLD_WORKER_WRITE', snapshot)
    check('R3-01 旧 worker 未把新任务改成终态', snapshot['state'] == 'queued', snapshot)
    check('R3-01 新 worker 的登记未被旧 worker 清掉', (uid, run_id) in active, active)
    check('R3-01 新 worker 正常完成并写回成功',
          final is not None and final['state'] == 'succeeded' and final['stage'] == 'NEW_WORKER_WRITE',
          dict(final) if final is not None else None)
    check('R3-01 两次 submit 的执行权不同（旧 worker 拿不到新 lease）',
          bool(seen.get('a_lease')) and bool(seen.get('b_lease')) and seen['a_lease'] != seen['b_lease'],
          {'a': seen.get('a_lease', '')[:8], 'b': seen.get('b_lease', '')[:8]})


# ── R3-02：排除继承跨批次持续（真实库） ─────────────────────────────────────────
def check_r3_02():
    print('R3-02 人工排除跨批次持续保护，直到用户显式重新纳入')
    from workbench.storage import engine as sto
    from workbench.storage import ontology_build as store
    from workbench.ontology_build import pipeline

    uid = 'r3-02-owner'
    aligned = 'property:soc#double@device'

    def mk_batch(conn, task_id, decision, reviewed=False):
        batch_id = store.create_batch(conn, task_id, uid, 'run-%s' % decision, {})
        store.create_candidate(conn, task_id, uid, batch_id, {
            'type': 'property', 'key': 'soc', 'name': 'SOC', 'definition': '荷电状态',
            'fields': {'dataType': 'timeSeries', 'valueType': 'double'},
            'ownerKey': 'device', 'evidenceStatus': 'supported', 'decision': decision,
            'alignedKey': aligned})
        if reviewed:
            items = store.all_candidates(conn, task_id, uid, batch_id=batch_id)
            store.update_candidate(conn, items[0]['id'], uid, reviewed=True)
        return batch_id

    with sto.write_tx() as tx:
        def seed(conn):
            task_id = store.create_task(conn, uid, 'r3-02')
            b1 = mk_batch(conn, task_id, 'exclude')
            b2 = mk_batch(conn, task_id, 'defer')
            b3 = mk_batch(conn, task_id, 'include')
            return task_id, b1, b2, b3
        task_id, b1, b2, b3 = tx.run(seed)

    # 第 3 批（B3）生成时：B1 的人工 exclude 仍在 B2 处于受保护（defer）状态，
    # 未显式重新纳入 → B3 的候选必须继续被保护。
    item_b3 = {'alignedKey': aligned, 'decision': 'include', 'origin': {}}
    with sto.write_tx() as tx:
        protected = tx.run(lambda conn: pipeline.inherit_manual_exclusions(
            conn, uid, task_id, b3, [item_b3]))
    check('R3-02 连续第 3 批仍被保护（保护数=1）', protected == 1, protected)
    check('R3-02 第 3 批候选被强制 defer', item_b3['decision'] == 'defer', item_b3)
    check('R3-02 第 3 批候选带 revived 标记', bool((item_b3.get('origin') or {}).get('revived')), item_b3)

    # 用户在 B2 显式重新纳入（reviewed=True）→ 后续批次解除保护
    with sto.write_tx() as tx:
        def reinclude(conn):
            items = store.all_candidates(conn, task_id, uid, batch_id=b2)
            store.update_candidate(conn, items[0]['id'], uid, decision='include', reviewed=True)
            b4 = mk_batch(conn, task_id, 'include')
            return b4
        b4 = tx.run(reinclude)
    item_b4 = {'alignedKey': aligned, 'decision': 'include', 'origin': {}}
    with sto.write_tx() as tx:
        protected_b4 = tx.run(lambda conn: pipeline.inherit_manual_exclusions(
            conn, uid, task_id, b4, [item_b4]))
    check('R3-02 用户显式重新纳入后不再保护（保护数=0）', protected_b4 == 0, protected_b4)
    check('R3-02 解除保护后 decision 保持 include', item_b4['decision'] == 'include', item_b4)


# ── R3-03：合并引用贯通与阻断 ───────────────────────────────────────────────────
def check_r3_03():
    print('R3-03 合并别名贯通属性/动作引用；非空宿主解析不到必须阻断')
    from workbench.ontology_build import ontology_adapter as adapter

    def cand(cid, key, kind, **kw):
        base = dict(id=cid, key=key, type=kind, name=key, definition='d', decision='include',
                    origin={}, fields={}, ownerKey='', evidenceStatus='supported')
        base.update(kw)
        return base

    o1 = cand('o1', 'obj-main', 'object')
    o2 = cand('o2', 'obj-alias', 'object', origin={'mergedInto': 'o1'})
    prop = cand('p1', 'capacity', 'property', ownerKey='obj-alias',
                fields={'dataType': 'number'})
    act = cand('a1', 'stop', 'action', ownerKey='obj-alias', fields={})
    aliases = {'obj-alias': 'o1', 'o2': 'o1'}

    graph, workflow, _id_map, _warn = adapter.assemble([o1, prop], aliases=aliases)
    node = next(n for n in graph['@graph'] if n['@type'] == 'owl:DatatypeProperty')
    domain = (node.get('rdfs:domain') or {}).get('@id')
    main_id = next(n['@id'] for n in graph['@graph'] if n['@type'] == 'owl:Class')
    check('R3-03 属性宿主沿合并别名解析到保留项', domain == main_id, {'domain': domain, 'main': main_id})

    # 每次 assemble 都重新分配稳定 ID，所以动作断言必须用**本次**装配出的主对象 id
    graph2, workflow2, _m2, _w2 = adapter.assemble([o1, act], aliases=aliases)
    main_id2 = next(n['@id'] for n in graph2['@graph'] if n['@type'] == 'owl:Class')
    check('R3-03 动作关联指向保留项（不再静默为空）',
          len(workflow2['actionAssociations']) == 1
          and workflow2['actionAssociations'][0]['objectTypeId'] == main_id2,
          workflow2['actionAssociations'])

    blocked = False
    message = ''
    try:
        adapter.assemble([o1, prop])          # 不给别名：非空宿主解析不到
    except adapter.AdapterError as exc:
        blocked, message = True, str(exc)
    check('R3-03 无别名时属性宿主解析不到被阻断', blocked, message)

    blocked_act = False
    message_act = ''
    try:
        adapter.assemble([o1, act])
    except adapter.AdapterError as exc:
        blocked_act, message_act = True, str(exc)
    check('R3-03 无别名时动作宿主解析不到被阻断（不再静默丢关联）', blocked_act, message_act)
    check('R3-03 阻断文案可读且含候选名',
          'stop' in message_act or 'obj-alias' in message_act, message_act)


# ── R3-04：时间序列编辑协议（前端 payload 口径） ────────────────────────────────
def check_r3_04():
    print('R3-04 时序编辑：平铺 payload 被接受，旧嵌套 payload 被拒且有可读提示')
    from unittest.mock import patch
    from workbench.ontology_build import protocol, review

    candidate = {'id': 'probe-series', 'taskId': 'probe-task', 'type': 'property',
                 'name': 'SOC采样', 'definition': '电量百分比采样', 'ownerKey': '',
                 'fields': {'dataType': 'timeSeries', 'valueType': 'double'},
                 'revision': 'r-probe', 'evidence': {}, 'evidenceStatus': 'supported',
                 'conflicts': [], 'decision': 'include', 'reviewed': False, 'reason': '',
                 'issues': [], 'origin': {}}

    def attempt(payload):
        with patch.object(review, 'owner', return_value='r3-04-owner'), \
             patch.object(review.store, 'get_candidate', return_value=candidate), \
             patch.object(review.store, 'candidate_view', side_effect=lambda row: row), \
             patch.object(review.store, 'update_candidate',
                          side_effect=lambda conn, cid, o, **kw: candidate), \
             patch.object(review, 'validate_candidate', return_value=[]):
            try:
                review.update_candidate(None, 'probe-series', payload, 'r-probe')
                return None
            except ValueError as exc:
                return str(exc)

    flat_ok = attempt({'name': '新名称', 'definition': '电量百分比采样',
                       'dataType': 'timeSeries', 'valueType': 'double'})
    check('R3-04 平铺 payload（前端新口径）被接受', flat_ok is None, flat_ok)

    nested = attempt({'name': '新名称', 'definition': '电量百分比采样',
                      'dataType': {'type': 'timeSeries', 'valueType': 'number'}})
    check('R3-04 旧嵌套 payload 被拒', nested is not None, nested)
    check('R3-04 拒绝文案给出纠正方向与允许枚举',
          nested is not None and '平铺' in nested and 'double' in nested, nested)

    bad_value = attempt({'name': '新名称', 'definition': 'd',
                         'dataType': 'timeSeries', 'valueType': 'number'})
    check('R3-04 非法观测值类型（number）被拒并列出允许枚举',
          bad_value is not None and 'double' in bad_value, bad_value)

    check('R3-04 观测值枚举与本体协议一致（7 项，与 model_format 同源）',
          tuple(protocol.VALUE_TYPES) == ('string', 'double', 'decimal', 'integer',
                                          'boolean', 'date', 'dateTime'),
          list(protocol.VALUE_TYPES))


def main():
    for name, func in (('R3-01', check_r3_01), ('R3-02', check_r3_02),
                       ('R3-03', check_r3_03), ('R3-04', check_r3_04)):
        try:
            with IsolatedRoot():
                func()
        except Exception as exc:
            traceback.print_exc()
            check('%s 复验脚本自身异常' % name, False, '%s: %s' % (type(exc).__name__, exc))
        print('')
    passed = sum(1 for _name, ok in RESULTS if ok)
    total = len(RESULTS)
    print('========== 复验汇总 ==========')
    print('通过 %d / %d' % (passed, total))
    for name, ok in RESULTS:
        if not ok:
            print('  失败: ' + name)
    return 0 if passed == total else 1


if __name__ == '__main__':
    sys.exit(main())
