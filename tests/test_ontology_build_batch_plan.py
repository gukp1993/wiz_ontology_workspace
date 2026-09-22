"""D04 装箱/拆分/稳定ID/覆盖守恒回归（batch_plan.py，2026-09-22）。

被测模块：workbench/ontology_build/batch_plan.py（纯函数；契约来源
workbench/ontology_build/batch_contracts.py D00 冻结 + budget.py D02 +
semantic_units.py D03 造真实输入联调）。

场景（对应整合计划 v3 §8 D04 行验收标准 + 任务指令测试清单 1–10）：
 1. 同 20 事实：简单组 → 少数几个 job；换成 enlarged（data 载荷 + 高校准估算）→
    job 数变多（计划规模因估算不同而不同，§9 场景1）；反馈收缩只缩不扩
    （shrink_for_feedback 建议批量 < 候选批 → 退回更小批次）。
 2. 硬约束触发：contextTokens 小的 profile → 大单元放不下 → infeasible →
    split_or_block 字段二分；field 子作业可独立装箱（真实可派发）。
 3. 嵌套拆分：深度 2 拆分链 splitPath = root/L、root/L/L、root/L/R；子 jobId 用
    contracts.stable_job_id 派生、重复调用恒同且不与父相同。
 4. 不可分：单事实无结构（任意深度）→ OVERSIZED_ATOMIC_TARGET；
    depth=8 → SPLIT_DEPTH_EXCEEDED（深度优先于不可分判定）。
 5. verify_split_coverage：左半覆盖/右半缺失 → 不 ok；重复覆盖 → 不 ok；
    context 重叠 → ok；selector 重复认领 / whole+field 混认领 / 额外目标 → 不 ok。
 6. 软目标：大估算组合 → 触发缩组且 softTargetExceeded 记录；单原子目标超软但
    硬预算内 → 允许单独派发并记录。
 7. 覆盖守恒端到端：初始 targets 全集 == 全部叶 job（split 后递归）primary 并集
    （contracts.coverage_check 验证不重不漏）。
 8. batch_state 联调：plan_initial/pack_next/split 产出的事件经 apply_event 全部
    合法，validate_plan_doc 零错误，pending 清空。
 9. 4096 目标边界：4097 个 targets → 明确不可规划原因 TARGET_BUDGET_EXCEEDED。
10. 乱序输入稳定：同一集合不同输入顺序 → 相同 job 划分、相同 jobId、相同
    infeasible 与 reason。

隔离（AGENTS.md 测试铁律）：纯内存测试，不 import 存储层、不设
WIZ_WORKBENCH_ROOT、不访问网络、不写任何文件；全部数据为合成数据。

运行：python3 tests/run.py --test tests/test_ontology_build_batch_plan.py
"""
import copy
import json
import random
import sys
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from workbench.ontology_build import batch_contracts as contracts  # noqa: E402
from workbench.ontology_build import batch_plan  # noqa: E402
from workbench.ontology_build import batch_state  # noqa: E402
from workbench.ontology_build import budget  # noqa: E402
from workbench.ontology_build import semantic_units  # noqa: E402

PASSED = []
FAILED = []
SEQ = [0]


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


def main():
    try:
        scenario_1_scale_by_estimate()
        scenario_2_hard_budget_and_field_split()
        scenario_3_nested_split_paths()
        scenario_4_unsplittable_and_depth()
        scenario_5_verify_split_coverage()
        scenario_6_soft_target()
        scenario_7_coverage_conservation()
        scenario_8_batch_state_integration()
        scenario_9_target_budget_boundary()
        scenario_10_order_stability()
        scenario_11_shapes_and_guards()
    except Exception as exc:  # noqa: BLE001 - 测试脚本顶层兜底，失败必须可见
        FAILED.append('未捕获异常:%s' % exc)
        print('[异常] %s' % traceback.format_exc())
    print('\n===== D04 batch_plan 回归 =====')
    print('通过 %d 项 / 失败 %d 项' % (len(PASSED), len(FAILED)))
    for item in FAILED:
        print('  失败: %s' % item)
    return 0 if not FAILED else 1


# --- 合成物料与 profile 工具 ---------------------------------------------------------


def jsonld_fact(fid, node, snippet='x' * 120, material='m1', data=None):
    """JSON-LD 节点事实（D03 ①：materialId+nodeId 高置信分组，kind=jsonld_node）。"""
    return {'id': fid, 'taskId': 't1', 'materialId': material,
            'locator': {'kind': 'jsonld', 'nodeId': node}, 'snippet': snippet,
            'kind': 'text', 'data': data, 'quality': 'ok'}


def doc_fact(fid, section, snippet='x' * 120, material='m1'):
    """文档章节事实（D03 ④：materialId+章节，kind=doc_section）。"""
    return {'id': fid, 'taskId': 't1', 'materialId': material,
            'locator': {'kind': 'md', 'section': section}, 'snippet': snippet,
            'kind': 'text', 'data': None, 'quality': 'ok'}


def build(facts):
    result = semantic_units.build_targets(facts)
    return result['targets'], {fact['id']: fact for fact in facts}


def profile_for(context_tokens, output_limit, request_output=None, ratio=None):
    """contracts.build_profile 组装启用的 BudgetProfile（D01 真实实现）。"""
    env = {
        'WIZ_BUILD_ADAPTIVE_BATCHING': '1',
        'WIZ_BUILD_PROFILE_PROVIDER_ID': 'prov',
        'WIZ_BUILD_PROFILE_MODEL': 'model-x',
        'WIZ_BUILD_CONTEXT_TOKENS': str(context_tokens),
        'WIZ_BUILD_OUTPUT_LIMIT_TOKENS': str(output_limit),
    }
    if request_output is not None:
        env['WIZ_BUILD_REQUEST_OUTPUT_TOKENS'] = str(request_output)
    if ratio is not None:
        env['WIZ_BUILD_OUTPUT_TARGET_RATIO'] = str(ratio)
    profile = contracts.build_profile(env)
    assert not profile['errors'], profile['errors']
    return profile


DEFAULT_PROFILE = None


def default_profile():
    global DEFAULT_PROFILE
    if DEFAULT_PROFILE is None:
        DEFAULT_PROFILE = profile_for(128000, 32000)
    return DEFAULT_PROFILE


def job_primary_ids(job):
    return list(job['orderedPrimaryTargetIds'])


# --- 场景 1：计划规模因估算不同而不同 + 反馈只缩不扩 ----------------------------------


def scenario_1_scale_by_estimate():
    print('\n-- 场景1：同 20 事实，简单组 vs enlarged 高估算 → job 数不同 --')
    facts = [jsonld_fact('f-%02d' % index, 'n-%02d' % index) for index in range(20)]
    targets, facts_by_id = build(facts)
    profile = default_profile()

    simple = batch_plan.pack_next(targets, targets, facts_by_id, profile)
    check(2 <= len(simple['jobs']) <= 5, '简单 20 事实装箱为少数几个作业',
          actual=len(simple['jobs']), expected='2..5')
    check(not simple['infeasible'] and not simple['softTargetExceeded'],
          '简单组全部可容纳且未触软目标', actual=(simple['infeasible'],
                                                  simple['softTargetExceeded']))
    check(all(len(job['orderedPrimaryTargetIds']) <= contracts.MAX_PRIMARY_TARGETS_PER_JOB
              for job in simple['jobs']), '每个作业 primary ≤ 64')
    check(sum(len(job['orderedPrimaryTargetIds']) for job in simple['jobs']) == 20,
          '简单组 20 目标全部进入作业')

    # enlarged：同 20 事实，目标带无结构 data（slots=4）+ 高校准估算（E=9000 > T/2）
    enlarged_targets = [dict(target, data='d' * 64) for target in targets]
    calibration = {'jsonld_node': 9000}
    enlarged = batch_plan.pack_next(enlarged_targets, targets, facts_by_id, profile,
                                    calibration=calibration)
    check(len(enlarged['jobs']) == 20, 'enlarged 高估算退到每单元一个作业',
          actual=len(enlarged['jobs']), expected=20)
    check(len(simple['jobs']) < len(enlarged['jobs']),
          '计划规模因估算不同而不同（§9 场景1）',
          actual=(len(simple['jobs']), len(enlarged['jobs'])))
    check(all(job['estimate']['expectedOutput'] >= 9000 for job in enlarged['jobs']),
          '校准有数据时 E 用 expected_output（≥ 校准值）',
          actual=[job['estimate']['expectedOutput'] for job in enlarged['jobs']][:3])
    check(all(budget.unit_slots(job['primaryTargets']) == 4 for job in enlarged['jobs']),
          'enlarged 无结构 data 目标按 4 slots 计')

    # 反馈收缩只缩不扩：候选批 ΣE 在硬预算内，但 shrink_for_feedback 建议批量更小
    pair = [jsonld_fact('g-1', 'gn-1'), doc_fact('g-2', 'sec-2')]
    pair_targets, pair_facts = build(pair)
    order = [str(target['targetId']) for target in
             sorted(pair_targets, key=lambda item: item['targetId'])]
    first_kind = [t['kind'] for t in pair_targets
                  if t['targetId'] == order[0]][0]
    second_kind = [t['kind'] for t in pair_targets
                   if t['targetId'] == order[1]][0]
    pair_calibration = {first_kind: 800, second_kind: 15000}
    pair_plan = batch_plan.pack_next(pair_targets, pair_targets, pair_facts, profile,
                                     calibration=pair_calibration)
    check(len(pair_plan['jobs']) == 2, '反馈收缩建议批量 1 → 两单元分两批（只缩不扩）',
          actual=[job_primary_ids(job) for job in pair_plan['jobs']])
    check([len(job['orderedPrimaryTargetIds']) for job in pair_plan['jobs']] == [1, 1],
          '收缩后每批一个语义单元')


# --- 场景 2：硬约束 → infeasible → 字段二分拆分且子作业可独立装箱 ---------------------


def scenario_2_hard_budget_and_field_split():
    print('\n-- 场景2：硬预算放不下 → infeasible → split_or_block --')
    big = jsonld_fact('f-big', 'n-big', snippet='S' * 2500,
                      data={'a': 'a' * 500, 'b': 'b' * 500, 'c': 'c' * 500, 'd': 'd' * 500})
    smalls = [jsonld_fact('f-s%d' % index, 'n-s%d' % index) for index in range(3)]
    facts = [big] + smalls
    targets, facts_by_id = build(facts)
    profile = profile_for(9000, 4096)   # 输入可容纳上限 ≈ 2856 token（utf8_proxy 字节）
    effective = profile['effective']
    check(effective['inputReserve'] == 2048 and effective['targetBudget'] == 2048,
          'profile 推导：reserve=2048、T=2048', actual=effective)

    packed = batch_plan.pack_next(targets, targets, facts_by_id, profile)
    big_targets = [target for target in targets if target['factId'] == 'f-big']
    big_ids = sorted(target['targetId'] for target in big_targets)
    check(sorted(packed['infeasible']) == big_ids, '大单元进 infeasible（规则 d）',
          actual=packed['infeasible'], expected=big_ids)
    packed_ids = {tid for job in packed['jobs'] for tid in job_primary_ids(job)}
    check(not (set(big_ids) & packed_ids), '不可容纳目标绝不进入任何作业')
    check(sum(len(job['orderedPrimaryTargetIds']) for job in packed['jobs']) == 3,
          '3 个小单元正常装箱')

    split = batch_plan.split_or_block(big_targets, facts_by_id, 0, profile)
    check(split['action'] == 'split' and len(split['children']) == 2,
          '结构化 data → 字段二分拆分出两个子作业', actual=split['action'])
    fields = [selector['field'] for selector in split['splitPath'][big_targets[0]['targetId']]]
    check(fields == ['a', 'b', 'c', 'd'], '拆分映射：父targetId → 全部子 selector（稳定序）',
          actual=fields)
    check(all(child.get('splitSelectors') for child in split['children']),
          '子作业携带 splitSelectors 份额')
    check([child['splitPath'] for child in split['children']] == ['root/fa', 'root/fc'],
          '字段拆分 splitPath = root/f<首字段>', actual=[c['splitPath']
                                                        for c in split['children']])
    ok, problems = batch_plan.verify_split_coverage(big_targets, split['children'])
    check(ok, '字段拆分覆盖守恒（selector 认领不重）', actual=problems)

    # 子作业真实可装箱：字段切片后输入回到硬预算内
    for child in split['children']:
        child_pack = batch_plan.pack_next(child['primaryTargets'], [], facts_by_id, profile)
        check(len(child_pack['jobs']) == 1 and not child_pack['infeasible'],
              'field 子作业 %s 可独立装箱' % child['splitPath'],
              actual=(len(child_pack['jobs']), child_pack['infeasible']))

    check(batch_plan.split_or_block(big_targets, facts_by_id, 0, profile)
          == batch_plan.split_or_block(big_targets, facts_by_id, 0, profile),
          'split_or_block 纯函数：同输入恒同输出')


# --- 场景 3：嵌套拆分深度 2、splitPath 与稳定 jobId -----------------------------------


def scenario_3_nested_split_paths():
    print('\n-- 场景3：嵌套拆分链 splitPath 与稳定子 jobId --')
    facts = [jsonld_fact('f-%02d' % index, 'n-1') for index in range(4)]
    targets, facts_by_id = build(facts)
    profile = default_profile()
    split1 = batch_plan.split_or_block(targets, facts_by_id, 0, profile, plan_epoch=7,
                                       parent_job_id='j-root', parent_split_path='root')
    check(split1['action'] == 'split', '单元内 4 目标二分')
    left, right = split1['children']
    check([child['splitPath'] for child in split1['children']] == ['root/L', 'root/R'],
          '第一层 splitPath = root/L、root/R',
          actual=[child['splitPath'] for child in split1['children']])
    check(left['parentId'] == 'j-root' and right['parentId'] == 'j-root'
          and left['rootId'] == 'j-root',
          '子作业 parentId/rootId 指向父作业')
    check([len(child['orderedPrimaryTargetIds']) for child in split1['children']] == [2, 2],
          '二分对半：2+2')
    expected_left = contracts.stable_job_id(7, 'j-root', 'root/L', left['primaryTargets'])
    expected_right = contracts.stable_job_id(7, 'j-root', 'root/R', right['primaryTargets'])
    check(left['jobId'] == expected_left and right['jobId'] == expected_right,
          '子 jobId 用 contracts.stable_job_id(epoch, 父, 路径, 目标) 派生')
    check('j-root' not in (left['jobId'], right['jobId']) and left['jobId'] != right['jobId'],
          '子 jobId 与父不同且互不相同')
    split1_again = batch_plan.split_or_block(targets, facts_by_id, 0, profile, plan_epoch=7,
                                             parent_job_id='j-root', parent_split_path='root')
    check([child['jobId'] for child in split1['children']]
          == [child['jobId'] for child in split1_again['children']],
          '重复拆分恒同 jobId（不按完成顺序生成）')

    split2 = batch_plan.split_or_block(left['primaryTargets'], facts_by_id, 1, profile,
                                       plan_epoch=7, parent_job_id=left['jobId'],
                                       parent_split_path='root/L', root_job_id='j-root')
    check([child['splitPath'] for child in split2['children']] == ['root/L/L', 'root/L/R'],
          '第二层 splitPath = root/L/L、root/L/R',
          actual=[child['splitPath'] for child in split2['children']])
    check(all(child['rootId'] == 'j-root' for child in split2['children']),
          '孙作业 rootId 仍指向根作业')
    check(all(child['parentId'] == left['jobId'] for child in split2['children']),
          '孙作业 parentId 指向 L 子作业')
    ok, problems = batch_plan.verify_split_coverage(left['primaryTargets'],
                                                    split2['children'])
    check(ok, '二级拆分覆盖守恒', actual=problems)
    grandchild_ids = {child['jobId'] for child in split2['children']}
    check(not (grandchild_ids & {left['jobId'], right['jobId'], 'j-root'}),
          '拆分链各层 jobId 两两不同')


# --- 场景 4：不可分与深度上限 ---------------------------------------------------------


def scenario_4_unsplittable_and_depth():
    print('\n-- 场景4：OVERSIZED_ATOMIC_TARGET 与 SPLIT_DEPTH_EXCEEDED --')
    plain = jsonld_fact('f-plain', 'n-plain', snippet='P' * 3000)   # 无 data 结构
    targets, facts_by_id = build([plain])
    profile = default_profile()
    for depth in (0, 5):
        blocked = batch_plan.split_or_block(targets, facts_by_id, depth, profile)
        check(blocked['action'] == 'blocked'
              and blocked['code'] == contracts.OVERSIZED_ATOMIC_TARGET,
              'depth=%d 单事实无结构 → OVERSIZED_ATOMIC_TARGET' % depth,
              actual=blocked)
        check('补充' in blocked['message'] or '缩小' in blocked['message'],
              '受阻文案提示补充材料/缩小任务（不伪造范围）', actual=blocked['message'])

    group = [jsonld_fact('f-%02d' % index, 'n-1') for index in range(4)]
    group_targets, group_facts = build(group)
    depth_blocked = batch_plan.split_or_block(group_targets, group_facts, 8, profile)
    check(depth_blocked['action'] == 'blocked'
          and depth_blocked['code'] == contracts.SPLIT_DEPTH_EXCEEDED,
          'depth=8 → SPLIT_DEPTH_EXCEEDED', actual=depth_blocked)
    atomic_at_depth = batch_plan.split_or_block(targets, facts_by_id, 8, profile)
    check(atomic_at_depth['code'] == contracts.SPLIT_DEPTH_EXCEEDED,
          '深度优先于不可分判定（depth=8 即受阻）', actual=atomic_at_depth)
    check(depth_blocked['message'].find('8') >= 0, '受阻信息含实际深度与上限')

    try:
        batch_plan.split_or_block([], facts_by_id, 0, profile)
        check(False, '空目标拆分应抛 ValueError', actual='未抛出', expected='ValueError')
    except ValueError:
        check(True, '空目标拆分抛 ValueError（拒绝静默）')


# --- 场景 5：verify_split_coverage 不重不漏 -------------------------------------------


def scenario_5_verify_split_coverage():
    print('\n-- 场景5：覆盖守恒校验的各种失败形态 --')
    facts = [jsonld_fact('f-%d' % index, 'n-%d' % index) for index in range(4)]
    targets, facts_by_id = build(facts)
    profile = default_profile()
    split = batch_plan.split_or_block(targets, facts_by_id, 0, profile)
    left, right = split['children']

    ok, problems = batch_plan.verify_split_coverage(targets, split['children'])
    check(ok, '正常二分 → ok', actual=problems)

    ok_missing, problems_missing = batch_plan.verify_split_coverage(targets, [left])
    check(not ok_missing and any('未被任何子作业覆盖' in item for item in problems_missing),
          '左半覆盖/右半缺失 → 不 ok（漏）', actual=problems_missing)

    c1, c2 = left, right
    dup_children = [c1, dict(c2, primaryTargets=c2['primaryTargets']
                             + [left['primaryTargets'][0]],
                             orderedPrimaryTargetIds=job_primary_ids(c2)
                             + [left['primaryTargets'][0]['targetId']])]
    ok_dup, problems_dup = batch_plan.verify_split_coverage(targets, dup_children)
    check(not ok_dup and any('重复覆盖' in item for item in problems_dup),
          '重复覆盖 → 不 ok（重）', actual=problems_dup)

    overlap_left = dict(copy.deepcopy(c1),
                        contextFactIds=list(c1['contextFactIds'])
                        + [right['primaryTargets'][0]['factId']])
    ok_overlap, problems_overlap = batch_plan.verify_split_coverage(
        targets, [overlap_left, copy.deepcopy(c2)])
    check(ok_overlap, 'context 重叠不参与比对 → ok', actual=problems_overlap)

    # field selector 映射：重复认领 / whole+field 混认领 / 额外目标
    keyed = jsonld_fact('f-key', 'n-key', data={'a': 1, 'b': 2})
    keyed_targets, _ = build([keyed])
    parent = keyed_targets[0]
    field_a = {'type': 'field', 'field': 'a'}
    child_target = dict(parent, selector=field_a, targetId='t-fake-field-a')

    dup_field = [
        {'jobId': 'c-a', 'primaryTargets': [dict(child_target)],
         'splitSelectors': {parent['targetId']: [dict(field_a)]}, 'contextFactIds': []},
        {'jobId': 'c-b', 'primaryTargets': [dict(child_target)],
         'splitSelectors': {parent['targetId']: [dict(field_a)]}, 'contextFactIds': []},
    ]
    ok_fdup, problems_fdup = batch_plan.verify_split_coverage([parent], dup_field)
    check(not ok_fdup and any('重复认领' in item for item in problems_fdup),
          'selector 拆分重复认领 → 不 ok', actual=problems_fdup)

    mixing = [
        {'jobId': 'c-w', 'primaryTargets': [dict(parent)], 'contextFactIds': []},
        {'jobId': 'c-f', 'primaryTargets': [dict(child_target)],
         'splitSelectors': {parent['targetId']: [dict(field_a)]}, 'contextFactIds': []},
    ]
    ok_mix, problems_mix = batch_plan.verify_split_coverage([parent], mixing)
    check(not ok_mix and any('混用' in item for item in problems_mix),
          'whole 与 field 混认领同一父目标 → 不 ok', actual=problems_mix)

    foreign = dict(parent, factId='f-other', targetId='t-other')
    extra = [{'jobId': 'c-x', 'primaryTargets': [foreign], 'contextFactIds': []}]
    ok_extra, problems_extra = batch_plan.verify_split_coverage([parent], extra)
    check(not ok_extra and any('额外目标' in item for item in problems_extra)
          and any('未被任何子作业覆盖' in item for item in problems_extra),
          '额外目标 + 父目标漏覆盖 → 不 ok', actual=problems_extra)

    ok_empty, problems_empty = batch_plan.verify_split_coverage([parent], [])
    check(not ok_empty and problems_empty, 'children 为空 → 不 ok', actual=problems_empty)


# --- 场景 6：软目标缩组与单原子超软允许 ------------------------------------------------


def scenario_6_soft_target():
    print('\n-- 场景6：软目标 V≤3000 缩组与 softTargetExceeded 记录 --')
    facts = [jsonld_fact('f-%d' % index, 'n-%d' % index, snippet='z' * 12000)
             for index in range(3)]
    targets, facts_by_id = build(facts)
    profile = default_profile()
    packed = batch_plan.pack_next(targets, targets, facts_by_id, profile)
    check(len(packed['jobs']) == 3, '超软组合缩组为每单元一个作业（§4.2 先缩组）',
          actual=[job_primary_ids(job) for job in packed['jobs']])
    check(all(len(job['orderedPrimaryTargetIds']) == 1 for job in packed['jobs']),
          '缩组后每作业恰一个原子单元')
    check(sorted(packed['softTargetExceeded']) == sorted(job['jobId']
                                                         for job in packed['jobs']),
          '单原子超软但硬预算内 → 允许派发并记录 softTargetExceeded',
          actual=packed['softTargetExceeded'])
    check(all(job['estimate']['softTargetExceeded'] for job in packed['jobs']),
          '作业 estimate 记录软目标超限标记')
    check(all(job['estimate']['expectedOutput'] <= profile['effective']['targetBudget']
              for job in packed['jobs']),
          '超软不超硬：E 均在 T 内', actual=[job['estimate']['expectedOutput']
                                             for job in packed['jobs']])
    check(all(job['estimate']['visibleOutput'] > contracts.SOFT_PACKING_TARGET_TOKENS
              for job in packed['jobs']),
          'V 估算确超 3000 软目标', actual=[job['estimate']['visibleOutput']
                                            for job in packed['jobs']])

    # 对照：小事实不触软目标
    small_facts = [jsonld_fact('g-%d' % index, 'gn-%d' % index) for index in range(6)]
    small_targets, small_facts_by_id = build(small_facts)
    small_pack = batch_plan.pack_next(small_targets, small_targets, small_facts_by_id,
                                      profile)
    check(not small_pack['softTargetExceeded'], '小事实组合不触软目标',
          actual=small_pack['softTargetExceeded'])
    check('软目标超限作业 0 个' in small_pack['reason'], 'reason 汇总软目标计数',
          actual=small_pack['reason'])


# --- 场景 7：覆盖守恒端到端（初始全集 == 叶并集） --------------------------------------


def _plan_until_leaves(targets, facts_by_id, profile, depth=0):
    """测试用递归规划：初始作业 + 装箱 + 不可容纳单元建 job 后拆分再装箱。

    返回全部叶作业定义（单元/目标二分子目标为父目标子集，可继续 pack_next）。
    depth 递增传给 split_or_block，模拟真实执行器深度。
    """
    leaves = []
    initial = batch_plan.plan_initial(targets, facts_by_id, profile, {})
    claimed = set()
    if initial['initialJob']:
        leaves.append(initial['initialJob'])
        claimed.update(job_primary_ids(initial['initialJob']))
    rest = [target for target in targets if target['targetId'] not in claimed]
    packed = batch_plan.pack_next(rest, targets, facts_by_id, profile)
    leaves.extend(packed['jobs'])
    claimed.update(tid for job in packed['jobs'] for tid in job_primary_ids(job))

    stuck = [target for target in targets if target['targetId'] not in claimed]
    if stuck:
        # 按 subjectKey 聚成不可容纳单元，逐个建 job → split → 子作业继续装箱
        units = {}
        for target in stuck:
            units.setdefault(target['subjectKey'], []).append(target)
        for index, unit in enumerate(sorted(units.values(),
                                            key=lambda items: items[0]['targetId'])):
            parent_id = 'j-stuck-%d' % index
            split = batch_plan.split_or_block(unit, facts_by_id, depth, profile,
                                              plan_epoch=1, parent_job_id=parent_id,
                                              parent_split_path='root')
            check(split['action'] == 'split', '不可容纳单元 %d 拆分为两个子作业' % index,
                  actual=split.get('code'))
            for child in split['children']:
                sub = batch_plan.pack_next(child['primaryTargets'], targets, facts_by_id,
                                           profile)
                if sub['infeasible']:
                    leaves.extend(_plan_until_leaves(child['primaryTargets'], facts_by_id,
                                                     profile, depth + 1))
                else:
                    leaves.extend(sub['jobs'])
    return leaves


def scenario_7_coverage_conservation():
    print('\n-- 场景7：初始 targets 全集 == 全部叶 job primary 并集 --')
    facts = [jsonld_fact('f-b%d' % index, 'node-big', snippet='B' * 550) for index in range(6)]
    facts += [jsonld_fact('f-s%d' % index, 'n-s%d' % index, snippet='s' * 200)
              for index in range(6)]
    targets, facts_by_id = build(facts)
    profile = profile_for(9000, 4096)

    leaves = _plan_until_leaves(targets, facts_by_id, profile)
    check(len(leaves) >= 4, '递归规划产出多个叶作业', actual=len(leaves))
    covered_ids = []
    for job in leaves:
        covered_ids.extend(job_primary_ids(job))
    planned_digests = sorted(contracts.target_digest(target) for target in targets)
    covered_digests = sorted(contracts.target_digest(target) for target in targets
                             if target['targetId'] in set(covered_ids))
    verdict = contracts.coverage_check(planned_digests, covered_digests)
    check(verdict['ok'], 'contracts.coverage_check：叶并集==计划全集且不重不漏',
          actual=verdict)
    check(len(covered_ids) == len(set(covered_ids)) == 12, '12 目标各被恰一个叶作业认领',
          actual=len(covered_ids))
    check(all(len(job['orderedPrimaryTargetIds']) <= contracts.MAX_PRIMARY_TARGETS_PER_JOB
              for job in leaves), '全部叶作业 primary ≤ 64')


# --- 场景 8：batch_state 联调（事件全部合法） ------------------------------------------


def scenario_8_batch_state_integration():
    print('\n-- 场景8：pack/split 事件经 batch_state.apply_event 全部合法 --')
    facts = [jsonld_fact('f-b%d' % index, 'node-big', snippet='B' * 550) for index in range(6)]
    facts += [jsonld_fact('f-s%d' % index, 'n-s%d' % index, snippet='s' * 200)
              for index in range(6)]
    targets, facts_by_id = build(facts)
    profile = profile_for(9000, 4096)
    doc = batch_state.create_plan('batch-d04', 1, 1, targets, profile, 'legacy-v1',
                                  ['scope-digest', 'material-digest'])
    check(doc['schemaVersion'] == contracts.CHECKPOINT_SCHEMA_VERSION,
          'create_plan 基线（schemaVersion=2）')

    events = []
    initial = batch_plan.plan_initial(targets, facts_by_id, profile, {})
    if initial['initialJob']:
        events.append(dict(initial['initialJob'], type='job_created'))
    claimed = set()
    for event in events:
        claimed.update(event['orderedPrimaryTargetIds'])
    pending = [target for target in targets if target['targetId'] not in claimed]
    packed = batch_plan.pack_next(pending, targets, facts_by_id, profile)
    for job in packed['jobs']:
        events.append(dict(job, type='job_created'))
        claimed.update(job_primary_ids(job))
    stuck = [target for target in targets if target['targetId'] not in claimed]
    if stuck:
        parent_id = 'j-stuck-manual'
        stuck_job = batch_plan.job_definition(parent_id, stuck, [], {'slots': 6},
                                              split_path='root')
        events.append(dict(stuck_job, type='job_created'))
        split = batch_plan.split_or_block(stuck, facts_by_id, 0, profile, plan_epoch=1,
                                          parent_job_id=parent_id, parent_split_path='root')
        events.append({'type': 'job_split', 'jobId': parent_id,
                       'children': split['children']})

    current = doc
    for event in events:
        try:
            current = batch_state.apply_event(current, event)
        except ValueError as exc:
            check(False, '事件 %s 被 batch_state 拒绝' % event.get('type'), actual=str(exc))
            return
    check(True, '全部 %d 个 job_created/job_split 事件合法（无 ValueError）' % len(events))
    errors = batch_state.validate_plan_doc(current)
    check(errors == [], 'validate_plan_doc 零错误', actual=errors)
    check(current['pendingTargetIds'] == [], 'pending 清空（全部目标被叶作业认领）',
          actual=current['pendingTargetIds'])
    covered = []
    for leaf_id in batch_state.effective_leaf_jobs(current):
        covered.extend(current['jobs'][leaf_id]['orderedPrimaryTargetIds'])
    verdict = contracts.coverage_check(
        sorted(contracts.target_digest(target) for target in targets),
        sorted(contracts.target_digest(target) for target in targets
               if target['targetId'] in set(covered)))
    check(verdict['ok'], '状态机叶覆盖与计划全集守恒', actual=verdict)
    split_parents = [job_id for job_id, job in current['jobs'].items() if job['children']]
    check(len(split_parents) == (1 if stuck else 0), 'split 父作业状态为 split 且有 children',
          actual=split_parents)


# --- 场景 9：4096 目标边界 ------------------------------------------------------------


def scenario_9_target_budget_boundary():
    print('\n-- 场景9：4097 个 targets → TARGET_BUDGET_EXCEEDED --')
    facts = [jsonld_fact('f-%04d' % index, 'n-%04d' % index) for index in range(4097)]
    targets, facts_by_id = build(facts)
    check(len(targets) == 4097, '构造 4097 个目标')
    result = batch_plan.plan_initial(targets, facts_by_id, default_profile(), {})
    check(result['ok'] is False and result['code'] == contracts.TARGET_BUDGET_EXCEEDED,
          '明确不可规划原因 TARGET_BUDGET_EXCEEDED',
          actual=(result['ok'], result['code']))
    check(len(result['pendingTargetIds']) == 4097 and not result['jobs']
          and result['initialJob'] is None,
          '拒绝规划：不产生任何作业，全部目标保持 pending',
          actual=(len(result['pendingTargetIds']), len(result['jobs'])))
    check(str(contracts.MAX_PLAN_TARGETS) in result['message'] and '4097' in result['message'],
          '受阻信息含实际数与上限', actual=result['message'])
    within = batch_plan.plan_initial(targets[:4096], facts_by_id, profile_for(64000, 32000), {})
    check(within['ok'] is True, '4096 个目标（恰在上限内）正常规划', actual=within['code'])


# --- 场景 10：乱序输入稳定 ------------------------------------------------------------


def scenario_10_order_stability():
    print('\n-- 场景10：同一集合不同输入顺序 → 相同划分与 jobId --')
    facts = [jsonld_fact('f-%02d' % index, 'n-%02d' % index) for index in range(6)]
    facts.append(jsonld_fact('f-big', 'n-big', snippet='S' * 2500,
                             data={'a': 'a' * 500, 'b': 'b' * 500}))
    profile = profile_for(8000, 4096)

    shuffled = list(facts)
    random.Random(7).shuffle(shuffled)
    targets_a, facts_a = build(facts)
    targets_b, facts_b = build(shuffled)
    check({t['targetId'] for t in targets_a} == {t['targetId'] for t in targets_b},
          'targetId 内容派生：乱序输入同目标集')

    pack_a = batch_plan.pack_next(targets_a, targets_a, facts_a, profile)
    pack_b = batch_plan.pack_next(targets_b, targets_b, facts_b, profile)
    check([job['jobId'] for job in pack_a['jobs']] == [job['jobId'] for job in pack_b['jobs']],
          '相同作业序列与 jobId',
          actual=([j['jobId'] for j in pack_a['jobs']], [j['jobId'] for j in pack_b['jobs']]))
    check([job_primary_ids(job) for job in pack_a['jobs']]
          == [job_primary_ids(job) for job in pack_b['jobs']], '相同目标划分')
    check(pack_a['infeasible'] == pack_b['infeasible']
          and pack_a['softTargetExceeded'] == pack_b['softTargetExceeded']
          and pack_a['reason'] == pack_b['reason'], 'infeasible/软超限/reason 一致')

    initial_a = batch_plan.plan_initial(targets_a, facts_a, profile, {'planEpoch': 3})
    initial_b = batch_plan.plan_initial(targets_b, facts_b, profile, {'planEpoch': 3})
    check((initial_a['initialJob'] or {}).get('jobId')
          == (initial_b['initialJob'] or {}).get('jobId')
          and initial_a['pendingTargetIds'] == initial_b['pendingTargetIds'],
          'plan_initial 乱序稳定（同 epoch 同初始作业与 pending）')

    split_a = batch_plan.split_or_block(targets_a, facts_a, 0, profile, plan_epoch=2,
                                        parent_job_id='j-p', parent_split_path='root')
    split_b = batch_plan.split_or_block(targets_b, facts_b, 0, profile, plan_epoch=2,
                                        parent_job_id='j-p', parent_split_path='root')
    check([child['jobId'] for child in split_a['children']]
          == [child['jobId'] for child in split_b['children']], '拆分子 jobId 乱序稳定')


# --- 场景 11（补充）：形状守卫与上下文语义 ---------------------------------------------


def scenario_11_shapes_and_guards():
    print('\n-- 场景11（补充）：作业定义形状、context 语义与 guard --')
    facts = [jsonld_fact('f-%d' % index, 'n-%d' % index) for index in range(5)]
    targets, facts_by_id = build(facts)
    profile = default_profile()
    packed = batch_plan.pack_next(targets, targets, facts_by_id, profile)
    job = packed['jobs'][0]
    check(job['jobId'].startswith('j-') and job['splitPath'] == 'root'
          and job['parentId'] == '' and job['rootId'] == job['jobId'],
          '作业定义冻结字段：jobId/splitPath/parentId/rootId', actual=job['jobId'])
    check(job_primary_ids(job) == [t['targetId'] for t in job['primaryTargets']],
          'orderedPrimaryTargetIds 与 primaryTargets 一致')
    check(set(job['estimate']) >= {'slots', 'inputTokens', 'expectedOutput', 'visibleOutput',
                                   'softTargetExceeded'},
          'estimate 记录 slots/I/E/V/软超限（§4.3）', actual=sorted(job['estimate']))
    json.dumps(job['estimate'])   # JSON 安全

    # context 语义：主目标所在 subjectKey 的邻近事实作背景，不算覆盖、与主目标 factId 不相交
    node_facts = [jsonld_fact('h-%d' % index, 'hn-1') for index in range(3)]
    node_targets, node_facts_by_id = build(node_facts)
    partial = batch_plan.pack_next([node_targets[0]], node_targets, node_facts_by_id, profile)
    ctx = partial['jobs'][0]['contextFactIds']
    check(sorted(ctx) == ['h-1', 'h-2'], '同 subjectKey 邻近事实进 contextFactIds',
          actual=ctx)
    check(ctx and ctx[0] not in {t['factId'] for t in partial['jobs'][0]['primaryTargets']},
          'context 与主目标 factId 不相交')

    whole = batch_plan.pack_next(node_targets, node_targets, node_facts_by_id, profile)
    check(all(job['contextFactIds'] == [] for job in whole['jobs']),
          '主组已覆盖整个主体时 context 为空')

    # guard：profile 未启用 / 缺 effective
    disabled = contracts.build_profile({})
    empty = batch_plan.pack_next(targets, targets, facts_by_id, disabled)
    check(empty == {'jobs': [], 'softTargetExceeded': [], 'infeasible': [],
                    'reason': empty['reason']} and '无法装箱' in empty['reason'],
          'profile 未启用 → 不装箱并给出原因', actual=empty['reason'])
    initial_guard = batch_plan.plan_initial(targets, facts_by_id, disabled, {})
    check(initial_guard['ok'] is False and initial_guard['initialJob'] is None
          and len(initial_guard['pendingTargetIds']) == 5,
          'plan_initial 同样拒绝并保持全部 pending', actual=initial_guard['reason'])

    # 纯函数：输入不被修改
    snapshot = copy.deepcopy(targets)
    batch_plan.pack_next(targets, targets, facts_by_id, profile)
    batch_plan.plan_initial(targets, facts_by_id, profile, {})
    check(targets == snapshot, 'pack_next/plan_initial 不修改传入 targets')

    messages_a = batch_plan.estimate_batch_messages(
        batch_plan.targets_with_content(targets, facts_by_id), [], None)
    messages_b = batch_plan.estimate_batch_messages(
        batch_plan.targets_with_content(targets, facts_by_id), [], None)
    check(messages_a == messages_b, '估算组包确定性（同输入恒同 messages）')
    check(contracts.estimate_request(messages_a)['inputTokens']
          == budget.estimate_request(messages_a)['inputTokens'],
          '组包与契约 estimate_request 同一口径')


if __name__ == '__main__':
    sys.exit(main())
