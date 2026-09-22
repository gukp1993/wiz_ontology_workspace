"""R2 冲突候选门禁回归（2026-09-22 缺陷修复：跨批发现冲突后候选仍被默认纳入交付）。

缺陷与修复口径：
* 逐批 verify 给 supported 候选默认 decision=include；跨批 alignment.align 合并同名
  候选后证据状态降级为 conflict（或 inferred/insufficient），但 decision 未重算——
  未经人工确认的冲突项带着 include 进入默认交付集合。
* 修复 1（生成端）：合并后二次 verify 对带 alignedKey 的候选重算——状态不再是
  supported 且 reviewed 非 true 的自动 include 撤销为 defer，登记 AUTO_INCLUDE_REVOKED；
  逐批首次赋值与人工决定（reviewed=true / exclude）不变。
* 修复 2（交付端硬门禁）：delivery._selected_candidates 只放行
  decision=include 且（reviewed=true 或「无 conflicts 且 evidenceStatus=supported」）；
  被拦候选计入预检排除报告（conflictExcluded*），不静默消失。

覆盖场景（对应修复两边 + 边界）：
1. 跨批冲突自动降级 defer（两批同名对象定义冲突：逐批 include → 合并 conflict →
   二次 verify defer + issue + 汇总 note）。
2. 人工确认（reviewed=true）保留 include（verify 层与交付门禁层都尊重人工决定）。
3. 交付选定集排除未确认冲突/证据不足候选并计数（真实临时库播种 + delivery.precheck）。
4. 无冲突候选不受影响（合并后仍 supported 的同名候选保持 include 并照常交付选定）。
5. 逐批首次赋值行为不变（supported→include；弱证据/无证据默认 defer；exclude 不被改写）。

隔离（AGENTS.md 测试隔离铁律）：每个场景独立临时根（WIZ_WORKBENCH_ROOT +
该根下 WIZ_DATABASE_URL），纯域函数与存储层短事务播种，不起服务、不连真实库、
不读写真实 ontology/；auth.bind_request 绑定假账号；结束销毁全部临时根。

运行：python3 tests/test_ontology_build_conflict_gate.py
"""
import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from workbench import auth  # noqa: E402
from workbench import storage  # noqa: E402
from workbench.ontology_build import alignment, delivery, pipeline  # noqa: E402
from workbench.storage import engine as sto  # noqa: E402
from workbench.storage import ontology_build as store  # noqa: E402

UID = 'conflict-gate-owner'
FACT_IDS = ('bf-aaa', 'bf-bbb')

PASSED = []
FAILED = []
SEQ = [0]
ROOTS = []


def _short(value, limit=400):
    try:
        text = repr(value)
    except Exception:  # noqa: BLE001 - 摘要展示绝不影响断言流程
        text = str(value)
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


def new_isolated_root(tag):
    """新临时根 + 该根下的 SQLite；重置引擎后惰性初始化（绝不碰真实 ontology/）。"""
    root = Path(tempfile.mkdtemp(prefix='wiz_conflict_gate_%s_' % tag))
    ROOTS.append(root)
    os.environ['WIZ_WORKBENCH_ROOT'] = str(root)
    os.environ['WIZ_DATABASE_URL'] = 'sqlite:///' + str(root / 'data' / 'workbench.sqlite3')
    sto.reset_engine()
    storage.ensure_ready()
    if str(root) not in sto.resolve_url():
        raise AssertionError('隔离根未生效：%s' % sto.resolve_url())
    auth.bind_request({'userId': UID, 'username': 'conflict_gate_tester'})
    return root


# --- 候选工厂（模拟两批模型输出：合法证据、同名对象、定义可冲突） ---------------------

def batch_candidate(name, definition, fact_id):
    return {'key': 'obj-' + name, 'type': 'object', 'name': name,
            'definition': definition, 'fields': {}, 'ownerKey': '',
            'evidence': {'definition': [fact_id]},
            'evidenceStatus': 'supported', 'conflicts': []}


# --- 场景 1：跨批冲突自动降级 defer ---------------------------------------------------

def scenario_cross_batch_conflict_deferred():
    tag = '场景1 跨批冲突自动降级defer'
    b1 = [batch_candidate('电池', '储能电池设备', 'bf-aaa')]
    b2 = [batch_candidate('电池', '电池管理系统', 'bf-bbb')]
    v1, r1 = pipeline.verify_candidates(b1, FACT_IDS)
    v2, r2 = pipeline.verify_candidates(b2, FACT_IDS)
    check(v1[0]['decision'] == 'include' and v2[0]['decision'] == 'include'
          and v1[0]['evidenceStatus'] == 'supported',
          '%s：逐批 verify 首次赋值不变——各自 supported 且默认 include（R2 前提）' % tag,
          actual=[(v1[0]['decision'], v1[0]['evidenceStatus']),
                  (v2[0]['decision'], v2[0]['evidenceStatus'])])
    check(not any(i.get('code') == 'AUTO_INCLUDE_REVOKED' for i in v1[0]['issues'])
          and not any(i.get('code') == 'AUTO_INCLUDE_REVOKED' for i in v2[0]['issues']),
          '%s：逐批阶段不产生撤销 issue（首次赋值逻辑不变）' % tag)

    aligned = alignment.align(v1 + v2)
    merged = aligned['candidates'][0]
    check(merged['evidenceStatus'] == 'conflict' and merged['decision'] == 'include'
          and merged.get('alignedKey') and merged['conflicts'],
          '%s：跨批 align 降级 conflict 但 decision 仍是 include（修复前的缺陷中间态）' % tag,
          actual=(merged['evidenceStatus'], merged['decision'], len(merged['conflicts'])))

    final, report = pipeline.verify_candidates(aligned['candidates'], FACT_IDS)
    node = final[0]
    revoked = [i for i in node['issues'] if i.get('code') == 'AUTO_INCLUDE_REVOKED']
    check(node['decision'] == 'defer' and node['evidenceStatus'] == 'conflict',
          '%s：合并后二次 verify 把未经确认的自动 include 撤销为 defer' % tag,
          expected={'decision': 'defer', 'evidenceStatus': 'conflict'},
          actual={'decision': node['decision'], 'evidenceStatus': node['evidenceStatus']})
    check(len(revoked) == 1 and 'conflict' in revoked[0]['message']
          and '请人工确认' in revoked[0]['message'] and revoked[0]['field'] == 'decision',
          '%s：撤销附 AUTO_INCLUDE_REVOKED issue（带状态与人工确认指引）' % tag,
          actual=revoked)
    check(any('自动撤销' in note for note in report['notes']),
          '%s：verify 报告 notes 注明撤销数量（进生成摘要 notes）' % tag,
          actual=report['notes'])
    check(not any('自动撤销' in note for note in r1['notes'] + r2['notes'])
          and any('自动撤销' in note for note in report['notes']),
          '%s：撤销汇总只出现在合并后那次 verify 的报告里' % tag)


# --- 场景 2：人工确认 reviewed=true 保留 include ---------------------------------------

def scenario_reviewed_include_respected():
    tag = '场景2 人工确认reviewed=true保留include'
    # verify 层：带 alignedKey 的冲突候选，reviewed=true 的人工 include 不被撤销
    confirmed = dict(batch_candidate('电池', '合并后人工确认的定义', 'bf-aaa'),
                     alignedKey='object:电池', decision='include', reviewed=True,
                     evidenceStatus='conflict',
                     conflicts=[{'field': 'definition', 'sides': [], 'note': 'x'}])
    final, _report = pipeline.verify_candidates([confirmed], FACT_IDS)
    check(final[0]['decision'] == 'include'
          and not any(i.get('code') == 'AUTO_INCLUDE_REVOKED' for i in final[0]['issues']),
          '%s：verify 层 reviewed=true 的人工决定不被自动撤销' % tag,
          actual=final[0]['decision'])

    # 交付门禁层：reviewed=true 的冲突候选照常进选定集合（尊重人工决定）
    allowed = delivery._delivery_allowed({'decision': 'include', 'reviewed': True,
                                          'evidenceStatus': 'conflict', 'conflicts': [1]})
    check(allowed is True, '%s：交付门禁放行 reviewed=true 的冲突候选' % tag)

    # 同一候选若未经确认（reviewed 缺失），verify 层照撤、门禁照拦——语义不弱化
    unconfirmed = dict(batch_candidate('电池', '合并后冲突定义', 'bf-aaa'),
                       alignedKey='object:电池', decision='include',
                       evidenceStatus='conflict',
                       conflicts=[{'field': 'definition', 'sides': [], 'note': 'x'}])
    final2, _r = pipeline.verify_candidates([unconfirmed], FACT_IDS)
    check(final2[0]['decision'] == 'defer'
          and delivery._delivery_allowed(dict(final2[0], reviewed=False)) is False,
          '%s：同一冲突候选未经确认时 verify 撤销且门禁拦截（双防线一致）' % tag,
          actual=final2[0]['decision'])


# --- 场景 3：交付选定集排除未确认冲突项并计数 -------------------------------------------

def seed_delivery_batch(conflict_reviewed=False):
    """真实临时库播种一个批次的候选：覆盖门禁的放行/拦截全部分支。

    行 1 conflict+conflicts 未确认（拦截）；行 2 inferred 无 conflicts 未确认（拦截，
    门禁按「非 supported 且未确认」口径）；行 3 supported（放行）；
    行 4 conflict 但 reviewed=true（放行，人工决定）；行 5 defer（本就不在选定集合）。
    注意 create_candidate 不读 payload.reviewed（列硬编码 0），人工确认必须像评审页
    一样走 store.update_candidate（reviewed=true 真实 UPDATE）。
    """

    def body(conn):
        task_id = store.create_task(conn, UID, '交付门禁回归')
        batch_id = store.create_batch(conn, task_id, UID, '', {})

        def seed(ckey, name, status, decision, conflicts=None):
            cid = store.create_candidate(conn, task_id, UID, batch_id, {
                'type': 'object', 'key': ckey, 'name': name,
                'definition': '%s 的业务定义' % name, 'fields': {}, 'ownerKey': '',
                'evidence': {}, 'evidenceStatus': status, 'conflicts': conflicts or [],
                'decision': decision,
                'alignedKey': 'object:%s' % name})
            return cid

        seed('obj-冲突未确认', '冲突未确认', 'conflict', 'include',
             conflicts=[{'field': 'definition', 'sides': [], 'note': 'x'}])
        seed('obj-推断未确认', '推断未确认', 'inferred', 'include')
        seed('obj-有依据', '有依据', 'supported', 'include')
        confirmed_id = seed('obj-冲突已确认', '冲突已确认', 'conflict', 'include',
                            conflicts=[{'field': 'definition', 'sides': [], 'note': 'x'}])
        seed('obj-暂缓', '暂缓', 'supported', 'defer')
        if conflict_reviewed:
            updated = store.update_candidate(conn, confirmed_id, UID, reviewed=True,
                                             reason='评审页人工确认')
            if updated is None:
                raise AssertionError('人工确认候选更新失败')
        return task_id, batch_id

    with sto.write_tx() as tx:
        return tx.run(body)


def scenario_delivery_gate_excludes():
    tag = '场景3 交付选定集排除未确认冲突项'
    task_id, batch_id = seed_delivery_batch(conflict_reviewed=True)
    with sto.read_connection() as conn:
        items, selected = delivery._selected_candidates(conn, task_id, UID, batch_id)
    names = sorted(c['name'] for c in selected)
    check(names == ['冲突已确认', '有依据'], tag + '：门禁只放行 supported 与 reviewed=true 的 include',
          expected=['冲突已确认', '有依据'], actual=names)
    check(len(items) == 5, tag + '：items 仍返回全部未合并候选（排除项可见、不删行）',
          actual=len(items))

    with sto.read_connection() as conn:
        pre = delivery.precheck(conn, task_id, batch_id)
    check(pre['conflictExcluded'] == 2,
          tag + '：预检 conflictExcluded 计数=2（冲突未确认 + 非 supported 未确认）',
          expected=2, actual=pre['conflictExcluded'])
    check(sorted(item['name'] for item in pre['conflictExcludedItems'])
          == ['冲突未确认', '推断未确认'],
          tag + '：conflictExcludedItems 逐项列出被拦截候选（含类型与状态）',
          actual=pre['conflictExcludedItems'])
    check(any('未经人工确认' in note and '2' in note for note in pre['notes']),
          tag + '：预检 notes 注明「N 项候选未经人工确认已移出选定集合」',
          actual=pre['notes'])
    check(set(pre['selectedIds']) == {c['id'] for c in selected},
          tag + '：selectedIds 与 _selected_candidates 门禁口径一致')
    check(pre['counts'] == {'object': 2} and pre['deferred'] == 1,
          tag + '：counts/deferred 只统计门禁后的选定集合（暂缓仍单列）',
          actual=(pre['counts'], pre['deferred']))

    # 反向对照：同一批次若「冲突已确认」行未经人工确认，三行 include 全部被拦/未放行
    task2, batch2 = seed_delivery_batch(conflict_reviewed=False)
    with sto.read_connection() as conn:
        _items3, selected3 = delivery._selected_candidates(conn, task2, UID, batch2)
        rows3 = {c['name']: c for c in store.all_candidates(conn, task2, UID, batch_id=batch2)}
    check(sorted(c['name'] for c in selected3) == ['有依据'],
          tag + '：未人工确认时 reviewed 缺省的冲突候选不进选定集合',
          actual=sorted(c['name'] for c in selected3))
    check(rows3['冲突已确认']['reviewed'] is False
          and rows3['冲突已确认']['decision'] == 'include',
          tag + '：对照批次的冲突候选仍是被门禁拦截的未确认 include',
          actual=(rows3['冲突已确认']['reviewed'], rows3['冲突已确认']['decision']))

    # 人工确认后重跑门禁：冲突已确认行进入选定集合（尊重人工决定照常交付）
    with sto.write_tx() as tx:
        def confirm(conn):
            store.update_candidate(conn, rows3['冲突已确认']['id'], UID, reviewed=True,
                                   reason='后续人工确认')
        tx.run(confirm)
    with sto.read_connection() as conn:
        _items4, selected4 = delivery._selected_candidates(conn, task2, UID, batch2)
    check(sorted(c['name'] for c in selected4) == ['冲突已确认', '有依据'],
          tag + '：reviewed=true 后门禁放行人工确认的冲突候选',
          actual=sorted(c['name'] for c in selected4))


# --- 场景 4：无冲突候选不受影响 ---------------------------------------------------------

def scenario_no_conflict_untouched():
    tag = '场景4 无冲突候选不受影响'
    b1 = [batch_candidate('充电桩', '交流充电设备', 'bf-aaa')]
    b2 = [batch_candidate('充电桩', '交流充电设备', 'bf-bbb')]   # 同名同定义：合并无冲突
    v1, _ = pipeline.verify_candidates(b1, FACT_IDS)
    v2, _ = pipeline.verify_candidates(b2, FACT_IDS)
    aligned = alignment.align(v1 + v2)
    final, report = pipeline.verify_candidates(aligned['candidates'], FACT_IDS)
    node = final[0]
    check(node['decision'] == 'include' and node['evidenceStatus'] == 'supported'
          and not node['conflicts'],
          '%s：同名同定义合并后仍 supported——默认 include 保持、不误伤' % tag,
          actual=(node['decision'], node['evidenceStatus'], len(node['conflicts'])))
    check(not any(i.get('code') == 'AUTO_INCLUDE_REVOKED' for i in node['issues'])
          and not any('自动撤销' in note for note in report['notes']),
          '%s：无冲突路径不产生撤销 issue 与撤销 note' % tag,
          actual=report['notes'])

    # 单候选（未合并）但带 alignedKey：状态 supported 时 include 同样保持
    solo = dict(batch_candidate('变压器', '升压变压器设备', 'bf-aaa'), alignedKey='object:变压器')
    final2, _ = pipeline.verify_candidates([solo], FACT_IDS)
    check(final2[0]['decision'] == 'include',
          '%s：带 alignedKey 但状态仍 supported 的候选不被撤销' % tag,
          actual=final2[0]['decision'])

    # 交付门禁对 supported 无冲突候选照常放行
    check(delivery._delivery_allowed({'decision': 'include', 'reviewed': False,
                                      'evidenceStatus': 'supported', 'conflicts': []}) is True,
          '%s：交付门禁放行 supported 且无 conflicts 的未确认 include' % tag)


# --- 场景 5：逐批首次赋值行为不变 + exclude 不被改写 -------------------------------------

def scenario_first_assignment_unchanged():
    tag = '场景5 逐批首次赋值行为不变'
    supported = batch_candidate('PCS', '变流器功率变换单元', 'bf-aaa')
    weak = dict(batch_candidate('弱证据对象', '只有兜底证据', 'bf-aaa'))
    no_evidence = dict(batch_candidate('无证据对象', '没有任何证据', 'bf-aaa'), evidence={},
                       evidenceStatus='supported')
    excluded = dict(batch_candidate('已排除对象', '人工否决的定义', 'bf-aaa'),
                    alignedKey='object:已排除对象', decision='exclude',
                    evidenceStatus='conflict',
                    conflicts=[{'field': 'definition', 'sides': [], 'note': 'x'}])
    final, _report = pipeline.verify_candidates(
        [supported, weak, no_evidence, excluded], FACT_IDS,
        weak_fact_ids={'bf-aaa'})   # bf-aaa 是弱证据：弱证据候选必须降级
    by_name = {node['name']: node for node in final}
    check(by_name['PCS']['decision'] == 'defer',
          '%s：证据命中弱证据事实的候选默认暂缓（V2-3 行为不变）' % tag,
          actual=by_name['PCS']['decision'])
    check(by_name['弱证据对象']['decision'] == 'defer'
          and by_name['弱证据对象']['evidenceStatus'] == 'inferred',
          '%s：弱证据降级 inferred → 默认 defer（首次赋值逻辑不变）' % tag,
          actual=(by_name['弱证据对象']['decision'], by_name['弱证据对象']['evidenceStatus']))
    check(by_name['无证据对象']['evidenceStatus'] == 'insufficient'
          and by_name['无证据对象']['decision'] == 'defer',
          '%s：supported 但无证据 → insufficient/defer（只降不升不变）' % tag,
          actual=(by_name['无证据对象']['evidenceStatus'], by_name['无证据对象']['decision']))
    check(by_name['已排除对象']['decision'] == 'exclude'
          and not any(i.get('code') == 'AUTO_INCLUDE_REVOKED'
                      for i in by_name['已排除对象']['issues']),
          '%s：人工 exclude 决定不被撤销逻辑改写（只撤自动 include）' % tag,
          actual=by_name['已排除对象']['decision'])

    # 无弱证据时的正常首次赋值：supported → include（对照组）
    clean, _ = pipeline.verify_candidates([batch_candidate('EMS', '能量管理系统', 'bf-bbb')],
                                          FACT_IDS, weak_fact_ids={'bf-aaa'})
    check(clean[0]['decision'] == 'include' and clean[0]['evidenceStatus'] == 'supported',
          '%s：干净证据候选首次赋值仍 supported/include（既有行为回归）' % tag,
          actual=(clean[0]['decision'], clean[0]['evidenceStatus']))


# --- 入口 -----------------------------------------------------------------------------

SCENARIOS = [
    ('cross_batch_conflict_deferred', scenario_cross_batch_conflict_deferred),
    ('reviewed_include_respected', scenario_reviewed_include_respected),
    ('delivery_gate_excludes', scenario_delivery_gate_excludes),
    ('no_conflict_untouched', scenario_no_conflict_untouched),
    ('first_assignment_unchanged', scenario_first_assignment_unchanged),
]


def main():
    for tag, fn in SCENARIOS:
        print('\n----- %s -----' % tag)
        new_isolated_root(tag)
        fn()
    return 0


def shutdown():
    sto.reset_engine()
    for root in ROOTS:
        shutil.rmtree(root, ignore_errors=True)


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
