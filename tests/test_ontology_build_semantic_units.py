"""语义目标与来源 selector 回归（D03：semantic_units.py，纯函数，零 IO）。

契约（冻结，提交 87d1473）：workbench/ontology_build/batch_contracts.py「目标 / 选择器」
节 + 需求文档 v3 §4.1/§8 D03 行 + 接口文档 08 §1.3 locator 形态。

十一个场景：
1. JSON-LD 事实（locator 带 nodeId）按 materialId+nodeId 分组，kind=jsonld_node，高置信；
   nodeId 优先于普通 json path（分组优先级①最高）。
2. DDL 事实按 materialId+表分组（同表不同列同组，跨表异组），kind=ddl_table。
3. code 事实按 materialId+限定符号分组；无 symbol 的 code 事实与相邻未定位事实落
   相邻窗口（低置信）；物料内孤立未定位事实 kind=single_fact。
4. 文档事实 md/docx 按章节、pdf 按页分组，kind=doc_section。
5. 普通无定位事实 → 相邻窗口 + groupingConfidence='low'；targets 的 factId 集合 ==
   输入 fact id 集合，一个 fact 恰一个 whole target，绝不丢事实。
6. 稀有字段保留：值为 true/数字/空串/短词/「像元数据」的事实照常生成 target（防
   「像元数据就删」回归）。
7. targetId 稳定：同输入两次调用全结果一致；不同 factId 不同 id；id 等于冻结公式。
8. canonical_selector/target_digest 与 batch_contracts 一致（whole/field/range 三形态），
   单元 kind 封闭在契约枚举内。
9. targets_for_facts 过滤辅助：按 factId 集合取 targets，保持输入顺序。
10. context_fact_ids：全量 targets 作解析表 + 主目标组（"一组 target"）作主集，同
    subjectKey 邻近事实作上下文；排除主目标、跨主体不入、max_context 生效、整组覆盖时
    为空——上下文与主目标 factId 不相交（不算覆盖的机械保证）。
11. 普通 JSON 键路径按最近对象路径分组：数组下标保留、末段叶子键收敛、深度 1 记根。

脚本风格同 tests/test_ontology_build_finish_guard.py：REPO sys.path、PASSED/FAILED、
main 退出码、python3 直跑。纯函数测试：不起服务、不建临时根、不写任何存储。

运行：python3 tests/test_ontology_build_semantic_units.py
"""
import hashlib
import sys
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from workbench.ontology_build import batch_contracts as contracts  # noqa: E402
from workbench.ontology_build import semantic_units as su  # noqa: E402

# batch_contracts「目标 / 选择器」节冻结的单元 kind 枚举
VALID_KINDS = {'jsonld_node', 'ddl_table', 'code_symbol', 'doc_section', 'json_object',
               'adjacent_window', 'single_fact'}

PASSED = []
FAILED = []
SEQ = [0]


def check(cond, message, actual=None):
    SEQ[0] += 1
    if cond:
        PASSED.append(message)
        print('通过 %d) %s' % (SEQ[0], message))
        return True
    FAILED.append(message)
    print('[失败] %d) %s' % (SEQ[0], message))
    if actual is not None:
        print('  实际: ' + repr(actual)[:400])
    return False


def fact(fid, material_id, locator=None, snippet='片段', kind=None, data=None, quality='high'):
    """存储 fact_view 形状的测试事实（接口 08 §1.3）。"""
    return {'id': fid, 'taskId': 'task-1', 'materialId': material_id,
            'locator': locator if locator is not None else {},
            'snippet': snippet,
            'kind': kind if kind is not None else (locator or {}).get('kind', 'text'),
            'data': data if data is not None else {}, 'quality': quality}


def group_of(result, subject_key):
    for group in result['groups']:
        if group['subjectKey'] == subject_key:
            return group
    return None


def targets_of_kind(result, kind):
    return [target for target in result['targets'] if target['kind'] == kind]


def assert_target_shape(result):
    """所有 target 逐字段符合冻结形状（供各场景复用）。"""
    ok = True
    for target in result['targets']:
        if set(target) != {'targetId', 'factId', 'selector', 'kind', 'subjectKey',
                           'materialId', 'groupingConfidence'}:
            ok = check(False, 'target 字段集合与冻结契约一致', target)
        if target['selector'] != {'type': 'whole'}:
            ok = check(False, 'v3 目标 selector 默认 whole', target)
        if target['kind'] not in VALID_KINDS:
            ok = check(False, '单元 kind 在契约枚举内', target)
        if target['groupingConfidence'] not in ('high', 'low'):
            ok = check(False, 'groupingConfidence 取值合法', target)
    for group in result['groups']:
        if set(group) != {'subjectKey', 'targetIds', 'confidence'}:
            ok = check(False, 'group 字段集合与冻结契约一致', group)
    return ok


# --- 场景 1：JSON-LD 按 materialId+nodeId 分组 ---------------------------------------

def scenario_jsonld_grouping():
    facts = [
        fact('f-j1', 'm1', {'kind': 'json', 'file': 'dev.jsonld', 'path': '@graph[0].name',
                            'nodeId': 'urn:dev-1'}),
        fact('f-j2', 'm1', {'kind': 'json', 'file': 'dev.jsonld', 'path': '@graph[0].desc',
                            'nodeId': 'urn:dev-1'}),
        fact('f-j3', 'm1', {'kind': 'json', 'file': 'dev.jsonld', 'path': '@graph[1].name',
                            'nodeId': 'urn:dev-2'}),
    ]
    result = su.build_targets(facts)
    assert_target_shape(result)
    group1 = group_of(result, 'jsonld:m1#urn:dev-1')
    check(group1 is not None and len(group1['targetIds']) == 2,
          '场景1 同 materialId+nodeId 的两条事实同组（2 个 target）', result['groups'])
    check(group_of(result, 'jsonld:m1#urn:dev-2') is not None,
          '场景1 另一 nodeId 独立成组（jsonld:m1#urn:dev-2）', [g['subjectKey'] for g in result['groups']])
    check(all(target['kind'] == 'jsonld_node' for target in result['targets']),
          '场景1 全部目标 kind=jsonld_node', [t['kind'] for t in result['targets']])
    check(all(target['groupingConfidence'] == 'high' for target in result['targets'])
          and all(group['confidence'] == 'high' for group in result['groups']),
          '场景1 定位分组置信 high（target 与 group 两级）', result['groups'])
    check(group1 is not None and group1['targetIds'][0] != group1['targetIds'][1],
          '场景1 同组内不同 factId 的 targetId 互不相同', group1)
    stats = result['stats']
    check(stats == {'factCount': 3, 'targetCount': 3, 'lowConfidenceGroups': 0},
          '场景1 stats：3 事实 3 目标 0 低置信组', stats)
    # 优先级：nodeId 存在时按 nodeId 而不是按 json path 分组
    check(group1 is not None and 'f-j1' in _fact_ids_of(result, group1)
          and 'f-j3' not in _fact_ids_of(result, group1),
          '场景1 分组键是 nodeId 而非 path（@graph[0] 与 @graph[1] 不同节点）', group1)


def _fact_ids_of(result, group):
    by_tid = {t['targetId']: t['factId'] for t in result['targets']}
    return {by_tid.get(tid) for tid in group['targetIds']}


# --- 场景 2：DDL 按 materialId+表 ----------------------------------------------------

def scenario_ddl_grouping():
    facts = [
        fact('f-d1', 'm2', {'kind': 'ddl', 'file': 'schema.sql', 'line': 3,
                            'table': 't_device', 'column': 'device_id'}),
        fact('f-d2', 'm2', {'kind': 'ddl', 'file': 'schema.sql', 'line': 4,
                            'table': 't_device', 'column': 'station_name'}),
        fact('f-d3', 'm2', {'kind': 'ddl', 'file': 'schema.sql', 'line': 9,
                            'table': 't_reading', 'column': 'value'}),
    ]
    result = su.build_targets(facts)
    assert_target_shape(result)
    group = group_of(result, 'ddl:m2#t_device')
    check(group is not None and sorted(_fact_ids_of(result, group)) == ['f-d1', 'f-d2'],
          '场景2 同表两列事实同组（ddl:m2#t_device）', result['groups'])
    check(group_of(result, 'ddl:m2#t_reading') is not None,
          '场景2 不同表独立成组（ddl:m2#t_reading）', [g['subjectKey'] for g in result['groups']])
    check(len(targets_of_kind(result, 'ddl_table')) == 3,
          '场景2 全部目标 kind=ddl_table', [t['kind'] for t in result['targets']])
    check(all(t['groupingConfidence'] == 'high' for t in result['targets']),
          '场景2 DDL 分组置信 high', result['targets'])


# --- 场景 3：代码按限定符号；无 symbol 落相邻窗口/低置信 ------------------------------

def scenario_code_symbol_grouping():
    facts = [
        fact('f-c1', 'm3', {'kind': 'code', 'file': 'bms.py', 'line': 10,
                            'symbol': 'BMSConfig.load'}, kind='code'),
        fact('f-c2', 'm3', {'kind': 'code', 'file': 'bms.py', 'line': 22,
                            'symbol': 'BMSConfig.load'}, kind='code'),
        fact('f-c3', 'm3', {'kind': 'code', 'file': 'bms.py', 'line': 40}, kind='code'),
        fact('f-p1', 'm3', {}),
        fact('f-x1', 'm5', {}),
    ]
    result = su.build_targets(facts)
    assert_target_shape(result)
    group = group_of(result, 'code:m3#BMSConfig.load')
    check(group is not None and sorted(_fact_ids_of(result, group)) == ['f-c1', 'f-c2'],
          '场景3 同限定符号两条代码事实同组（code:m3#BMSConfig.load）', result['groups'])
    window = group_of(result, 'window:m3#w1')
    check(window is not None and sorted(_fact_ids_of(result, window)) == ['f-c3', 'f-p1'],
          '场景3 无 symbol 的 code 事实与相邻未定位事实合并为相邻窗口', result['groups'])
    check(window is not None and window['confidence'] == 'low'
          and all(t['groupingConfidence'] == 'low'
                  for t in result['targets'] if t['factId'] in ('f-c3', 'f-p1')),
          '场景3 相邻窗口置信 low（组与 target 两级）', window)
    check(sorted(t['kind'] for t in result['targets'] if t['factId'] in ('f-c3', 'f-p1'))
          == ['adjacent_window', 'adjacent_window'],
          '场景3 窗口成员 kind=adjacent_window（不因原是 code 事实而改判）',
          [(t['factId'], t['kind']) for t in result['targets']])
    single = group_of(result, 'single:m5#f-x1')
    check(single is not None and _fact_ids_of(result, single) == {'f-x1'},
          '场景3 物料内孤立未定位事实自成一组（single:m5#f-x1）', result['groups'])
    check([t['kind'] for t in result['targets'] if t['factId'] == 'f-x1'] == ['single_fact'],
          '场景3 孤立无组事实 kind=single_fact', [(t['factId'], t['kind']) for t in result['targets']])
    check(result['stats']['lowConfidenceGroups'] == 2,
          '场景3 stats.lowConfidenceGroups=2（窗口 + 孤立）', result['stats'])


# --- 场景 4：文档按章节（md/docx section，pdf 页） -----------------------------------

def scenario_doc_section_grouping():
    facts = [
        fact('f-m1', 'm4', {'kind': 'md', 'file': 'readme.md', 'section': '安装', 'line': 3}),
        fact('f-m2', 'm4', {'kind': 'md', 'file': 'readme.md', 'section': '安装', 'line': 9}),
        fact('f-w1', 'm4', {'kind': 'docx', 'file': '说明.docx', 'section': '概述', 'block': 2}),
        fact('f-p1', 'm4', {'kind': 'pdf', 'file': '手册.pdf', 'page': 3}),
    ]
    result = su.build_targets(facts)
    assert_target_shape(result)
    group = group_of(result, 'doc:m4#安装')
    check(group is not None and sorted(_fact_ids_of(result, group)) == ['f-m1', 'f-m2'],
          '场景4 同一 md 章节两条事实同组（doc:m4#安装）', result['groups'])
    check(group_of(result, 'doc:m4#概述') is not None,
          '场景4 docx 章节独立成组（doc:m4#概述）', [g['subjectKey'] for g in result['groups']])
    check(group_of(result, 'doc:m4#page:3') is not None,
          '场景4 pdf 无 section，按真实定位字段 page 分组（doc:m4#page:3）',
          [g['subjectKey'] for g in result['groups']])
    check(len(targets_of_kind(result, 'doc_section')) == 4,
          '场景4 全部目标 kind=doc_section', [t['kind'] for t in result['targets']])
    check(all(t['groupingConfidence'] == 'high' for t in result['targets']),
          '场景4 文档分组置信 high', result['targets'])


# --- 场景 5：普通无定位事实 → 相邻窗口低置信，绝不丢事实 -----------------------------

def scenario_plain_adjacent_window():
    facts = [
        fact('f-n1', 'm6', {}),
        fact('f-n2', 'm6', {}),
        fact('f-n3', 'm6', {}),
        fact('f-t1', 'm7', {'kind': 'text', 'file': 'a.log', 'line': 5}),
    ]
    result = su.build_targets(facts)
    assert_target_shape(result)
    window = group_of(result, 'window:m6#w1')
    check(window is not None and sorted(_fact_ids_of(result, window)) == ['f-n1', 'f-n2', 'f-n3'],
          '场景5 同物料连续三条未定位事实合并为一个相邻窗口', result['groups'])
    check(window is not None and window['confidence'] == 'low',
          '场景5 相邻窗口 groupingConfidence=low', window)
    check(group_of(result, 'single:m7#f-t1') is not None,
          '场景5 text 定位（冻结清单外）按 fallback 处理：孤立成 single 组',
          [g['subjectKey'] for g in result['groups']])
    target_fact_ids = sorted(t['factId'] for t in result['targets'])
    check(target_fact_ids == sorted(['f-n1', 'f-n2', 'f-n3', 'f-t1']),
          '场景5 targets 的 factId 集合 == 输入 fact id 集合（不丢任何 fact）', target_fact_ids)
    check(result['stats']['factCount'] == result['stats']['targetCount'] == 4,
          '场景5 一个 fact 恰一个 whole target（factCount == targetCount）', result['stats'])
    # 不同物料的 fallback 不跨物料合并（f-t1 不进 m6 的窗口）
    check('f-t1' not in _fact_ids_of(result, window or {'targetIds': []}),
          '场景5 相邻窗口不跨物料合并', window)


# --- 场景 6：稀有字段保留（防「像元数据就删」回归） ----------------------------------

def scenario_rare_values_preserved():
    facts = [
        fact('f-r1', 'm8', {}, snippet='true', data={'enabled': True}),
        fact('f-r2', 'm8', {}, snippet='42', data={'count': 0}),
        fact('f-r3', 'm8', {}, snippet='是', data={'unit': ''}),
        fact('f-r4', 'm8', {}, snippet='', data=None),
        fact('f-r5', 'm8', {'kind': 'json', 'file': 'c.json', 'path': 'version'},
             snippet='1.0.3', data={'value': '1.0.3'}),
    ]
    result = su.build_targets(facts)
    assert_target_shape(result)
    target_fact_ids = sorted(t['factId'] for t in result['targets'])
    check(target_fact_ids == ['f-r1', 'f-r2', 'f-r3', 'f-r4', 'f-r5'],
          '场景6 布尔/数字/空串/短词事实全部生成 target，一个不丢', target_fact_ids)
    check(result['stats']['factCount'] == result['stats']['targetCount'] == 5,
          '场景6 稀有值事实 factCount == targetCount == 5', result['stats'])
    check(group_of(result, 'json:m8#<root>') is not None
          and 'f-r5' in _fact_ids_of(result, group_of(result, 'json:m8#<root>')),
          '场景6 深度 1 的 json path 落根对象主体（json:m8#<root>）', result['groups'])


# --- 场景 7：targetId 稳定 -----------------------------------------------------------

def scenario_target_id_stability():
    facts = [
        fact('f-a', 'm9', {'kind': 'json', 'file': 'd.jsonld', 'path': '@graph[0]',
                           'nodeId': 'urn:x-1'}),
        fact('f-b', 'm9', {'kind': 'ddl', 'file': 's.sql', 'line': 1, 'table': 't1',
                           'column': 'c1'}),
    ]
    first = su.build_targets(facts)
    second = su.build_targets(facts)
    check(first == second, '场景7 同输入两次调用整个结果一致（targets/groups/stats）', None)
    ids = {t['factId']: t['targetId'] for t in first['targets']}
    check(ids['f-a'] != ids['f-b'], '场景7 不同 factId 产生不同 targetId', ids)
    expected_a = 't-' + hashlib.sha256(('f-a' + 'whole').encode('utf-8')).hexdigest()[:16]
    check(ids['f-a'] == expected_a,
          '场景7 targetId 等于冻结公式 t-+sha256(factId+canonical_selector)[:16]', ids['f-a'])
    check(ids['f-a'].startswith('t-') and len(ids['f-a']) == len('t-') + 16,
          '场景7 targetId 形态：t- 前缀 + 16 hex', ids['f-a'])


# --- 场景 8：与 batch_contracts 口径一致（whole/field/range） -------------------------

def scenario_contracts_consistency():
    selectors = [
        {'type': 'whole'},
        {'type': 'field', 'field': 'pressure'},
        {'type': 'range', 'start': 2, 'end': 7},
    ]
    for selector in selectors:
        check(su.canonical_selector(selector) == contracts.canonical_selector(selector),
              '场景8 canonical_selector 与 batch_contracts 一致：%s' % selector,
              su.canonical_selector(selector))
        digest_input = {'factId': 'f-1', 'selector': selector}
        check(su.target_digest(digest_input) == contracts.target_digest(digest_input),
              '场景8 target_digest 与 batch_contracts 一致：%s' % selector,
              su.target_digest(digest_input))
    check(su.canonical_selector({'type': 'whole'}) == 'whole'
          and su.canonical_selector(None) == 'whole',
          '场景8 whole/缺省 selector 规范化为字面 whole', su.canonical_selector(None))
    check(su.canonical_selector({'type': 'field', 'field': 'pressure'}) == 'field:pressure',
          '场景8 field selector 规范化为 field:<字段>', None)
    check(su.canonical_selector({'type': 'range', 'start': 2, 'end': 7}) == 'range:2:7',
          '场景8 range selector 规范化为 range:<起>:<止>', None)
    facts = [
        fact('f-k1', 'm10', {'kind': 'json', 'file': 'e.jsonld', 'path': '@graph[0]',
                             'nodeId': 'urn:k1'}),
        fact('f-k2', 'm10', {'kind': 'ddl', 'file': 's2.sql', 'line': 2, 'table': 't2',
                             'column': 'c2'}),
        fact('f-k3', 'm10', {}),
        fact('f-k4', 'm10', {}),
    ]
    result = su.build_targets(facts)
    mismatched = [t['targetId'] for t in result['targets']
                  if t['targetId'] != 't-' + hashlib.sha256(
                      (t['factId'] + contracts.canonical_selector(t['selector']))
                      .encode('utf-8')).hexdigest()[:16]]
    check(not mismatched, '场景8 build_targets 全部 targetId 与 batch_contracts 口径重算一致',
          mismatched)
    kinds = {t['kind'] for t in result['targets']}
    check(kinds <= VALID_KINDS, '场景8 产出单元 kind 封闭在契约枚举内', kinds)


# --- 场景 9：targets_for_facts 过滤辅助 ----------------------------------------------

def scenario_targets_for_facts_filter():
    facts = [
        fact('f-1', 'm11', {'kind': 'json', 'file': 'f.jsonld', 'path': '@graph[0]',
                            'nodeId': 'urn:a'}),
        fact('f-2', 'm11', {'kind': 'json', 'file': 'f.jsonld', 'path': '@graph[0]',
                            'nodeId': 'urn:a'}),
        fact('f-3', 'm11', {}),
    ]
    picked = su.targets_for_facts(facts, {'f-1', 'f-3'})
    check([t['factId'] for t in picked] == ['f-1', 'f-3'],
          '场景9 targets_for_facts 只取给定 factId 集合且保持输入顺序',
          [t['factId'] for t in picked])
    check(su.targets_for_facts(facts, ['不存在']) == [],
          '场景9 未知 factId 无匹配返回空列表', None)
    check(su.targets_for_facts([], ['f-1']) == [], '场景9 空 facts 输入返回空列表', None)
    full = su.targets_for_facts(facts, ['f-1', 'f-2', 'f-3'])
    check(full == su.build_targets(facts)['targets'],
          '场景9 全集过滤结果与 build_targets targets 同形同源', None)


# --- 场景 10：context_fact_ids 上下文选择 --------------------------------------------

def scenario_context_fact_ids_selection():
    facts = [
        fact('f1', 'm12', {'kind': 'json', 'file': 'g.jsonld', 'path': '@graph[0]',
                           'nodeId': 'urn:dev'}),
        fact('f2', 'm12', {'kind': 'json', 'file': 'g.jsonld', 'path': '@graph[0]',
                           'nodeId': 'urn:dev'}),
        fact('f3', 'm12', {'kind': 'json', 'file': 'g.jsonld', 'path': '@graph[0]',
                           'nodeId': 'urn:dev'}),
        fact('f4', 'm12', {'kind': 'json', 'file': 'g.jsonld', 'path': '@graph[1]',
                           'nodeId': 'urn:other'}),
    ]
    result = su.build_targets(facts)
    all_targets = result['targets']
    tid_of = {t['factId']: t['targetId'] for t in all_targets}

    def primary_group(fact_ids, subject_key):
        return {'subjectKey': subject_key, 'targetIds': [tid_of[f] for f in fact_ids],
                'confidence': 'high'}

    # 主目标 = dev 组的 f1（如 split 后的子集）：上下文 = 同主体其余邻近事实 f2/f3
    context = su.context_fact_ids(all_targets, [primary_group(['f1'], 'jsonld:m12#urn:dev')])
    check(context == ['f2', 'f3'],
          '场景10 主目标 f1 的上下文 = 同 subjectKey 邻近事实 f2/f3（按 targets 序）', context)
    check('f1' not in context and 'f4' not in context,
          '场景10 上下文排除主目标自身与其他主体事实（上下文不算覆盖）', context)
    check(su.context_fact_ids(all_targets, [primary_group(['f1'], 'jsonld:m12#urn:dev')],
                              max_context=1) == ['f2'],
          '场景10 max_context 截断生效', None)
    check(su.context_fact_ids(all_targets, [primary_group(['f1'], 'jsonld:m12#urn:dev')],
                              max_context=0) == [],
          '场景10 max_context=0 返回空', None)
    # 主目标跨两个主体：f2（dev 组）+ f4（other 组）
    context_multi = su.context_fact_ids(
        all_targets, [primary_group(['f2'], 'jsonld:m12#urn:dev'),
                      primary_group(['f4'], 'jsonld:m12#urn:other')])
    check(context_multi == ['f1', 'f3'],
          '场景10 多主组：同主体邻近事实都入上下文，主目标 factId 被排除', context_multi)
    # 主组覆盖整个主体时不造上下文
    check(su.context_fact_ids(
        all_targets, [primary_group(['f1', 'f2', 'f3'], 'jsonld:m12#urn:dev')]) == [],
        '场景10 主组已覆盖整个主体 → 上下文为空（不为已覆盖部分生成上下文）', None)
    check(su.context_fact_ids([], [primary_group(['f1'], 'jsonld:m12#urn:dev')]) == [],
          '场景10 无全量 targets 可解析时返回空上下文', None)
    check(su.context_fact_ids(all_targets, []) == [],
          '场景10 无主组返回空上下文', None)
    primary_set = {'f2', 'f4'}
    check(not (set(context_multi) & primary_set),
          '场景10 返回上下文与主目标 factId 不相交（机械保证不算覆盖）', None)
    # 主组引用未知 targetId（与全量 targets 不同源）：不崩溃、不误选
    ghost = {'subjectKey': 'jsonld:m12#urn:dev', 'targetIds': ['t-ghost'], 'confidence': 'high'}
    check(su.context_fact_ids(all_targets, [ghost]) == ['f1', 'f2', 'f3'],
          '场景10 主组含未知 targetId 时安全跳过（subjectKey 仍生效）', None)


# --- 场景 11：普通 JSON 最近对象路径（数组下标保留） ---------------------------------

def scenario_structured_json_paths():
    facts = [
        fact('f-s1', 'm13', {'kind': 'json', 'file': 'h.json', 'path': 'bms.items[2].pressure.value'}),
        fact('f-s2', 'm13', {'kind': 'json', 'file': 'h.json', 'path': 'bms.items[2].pressure.unit'}),
        fact('f-s3', 'm13', {'kind': 'json', 'file': 'h.json', 'path': 'bms.items[3].pressure.value'}),
        fact('f-s4', 'm13', {'kind': 'json', 'file': 'h.json', 'path': 'items[2]'}),
        fact('f-s5', 'm13', {'kind': 'yaml', 'file': 'i.yaml', 'path': 'server.host'}),
    ]
    result = su.build_targets(facts)
    assert_target_shape(result)
    same_obj = group_of(result, 'json:m13#bms.items[2].pressure')
    check(same_obj is not None and sorted(_fact_ids_of(result, same_obj)) == ['f-s1', 'f-s2'],
          '场景11 同一最近对象路径的叶子同组（json:m13#bms.items[2].pressure）',
          [g['subjectKey'] for g in result['groups']])
    check(group_of(result, 'json:m13#bms.items[3].pressure') is not None,
          '场景11 数组下标保留：items[3] 与 items[2] 不并组',
          [g['subjectKey'] for g in result['groups']])
    check(group_of(result, 'json:m13#items[2]') is not None,
          '场景11 末段自带下标时整条 path 原样保留（json:m13#items[2]）',
          [g['subjectKey'] for g in result['groups']])
    check(group_of(result, 'json:m13#server') is not None,
          '场景11 yaml 与 json 同一遍历口径（server.host → 最近对象 server）',
          [g['subjectKey'] for g in result['groups']])
    check(all(t['kind'] == 'json_object' for t in result['targets']),
          '场景11 全部目标 kind=json_object', [t['kind'] for t in result['targets']])
    check(all(t['groupingConfidence'] == 'high' for t in result['targets']),
          '场景11 有节点路径的 JSON 分组置信 high（不当整文件一个主体）', result['targets'])
    check(su.object_path_of('a.b[2].c') == 'a.b[2]'
          and su.object_path_of('$.store.book[0].title') == 'store.book[0]'
          and su.object_path_of('leaf') == '',
          '场景11 object_path_of 规则：末段叶子收敛/$前缀容忍/深度1记根',
          [su.object_path_of(p) for p in ('a.b[2].c', '$.store.book[0].title', 'leaf')])


SCENARIOS = (
    ('jsonld_grouping', scenario_jsonld_grouping),
    ('ddl_grouping', scenario_ddl_grouping),
    ('code_symbol_grouping', scenario_code_symbol_grouping),
    ('doc_section_grouping', scenario_doc_section_grouping),
    ('plain_adjacent_window', scenario_plain_adjacent_window),
    ('rare_values_preserved', scenario_rare_values_preserved),
    ('target_id_stability', scenario_target_id_stability),
    ('contracts_consistency', scenario_contracts_consistency),
    ('targets_for_facts_filter', scenario_targets_for_facts_filter),
    ('context_fact_ids_selection', scenario_context_fact_ids_selection),
    ('structured_json_paths', scenario_structured_json_paths),
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
