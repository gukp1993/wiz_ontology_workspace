"""『合并后引用规范化』域级 + 真实交付端到端回归（R3-03）。

覆盖：
1. 域级：`assemble(..., aliases=合并别名表)` 把其它候选指向**被合并候选** key/ID 的
   引用规范化到最终保留项——属性 rdfs:domain、链接两端、规则/动作宿主与关联集合；
   按候选 ID 与按 key 两种别名都解析，合并链（A→B→C）传递到 C，环/断链不静默改指。
2. 域级反例：不给别名时**非空**宿主/端点解析不到必须抛 AdapterError（绝不静默落成
   actionAssociations=[]）；空字符串 = 未声明宿主，允许不挂关联、不阻断。
3. 真实交付端到端：真实临时 SQLite 根 + 真实 store / 交付域，合并两个对象后
   预检 ok → `delivery.deliver()` 成功 → 读回草稿断言属性 domain 与动作/规则关联
   都指向主对象稳定 id（被合并候选不进入交付）。
4. 撤销合并：`origin.mergedInto` 清空 → 别名消失 → 属性和规则/动作的宿主解析不到 →
   交付预检被阻断，且 issues 可定位到具体候选（不再静默通过）。
5. 合并链传递解析与环保护：别名全部落到本批次候选 ID（不编造、不死循环）。
6. `verify_structure` 不变式：规则/动作关联的 objectTypeId、属性 rdfs:domain、
   链接两端必须都在本体图 @graph 的 ID 集合内。

隔离（AGENTS.md 测试隔离铁律）：WIZ_WORKBENCH_ROOT 与 WIZ_DATABASE_URL 都指向本次
运行新建的临时目录（结束清理），用 auth.bind_request 绑定的假账号 ID 建合成任务/批次/
候选；不启动服务、不连真实库、不读写真实 ontology/。

运行：.venv/bin/python tests/test_ontology_build_merge_refs.py
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

TMP = Path(tempfile.mkdtemp(prefix='wiz_ob_merge_refs_'))
os.environ['WIZ_WORKBENCH_ROOT'] = str(TMP)
os.environ['WIZ_DATABASE_URL'] = 'sqlite:///' + str(TMP / 'data' / 'workbench.sqlite3')

from workbench import auth  # noqa: E402
from workbench import storage  # noqa: E402
from workbench import workspaces  # noqa: E402
from workbench.ontology_build import delivery  # noqa: E402
from workbench.ontology_build import ontology_adapter as adapter  # noqa: E402
from workbench.ontology_build import review  # noqa: E402
from workbench.paths import DATA_ROOT  # noqa: E402
from workbench.storage import assets as asset_store  # noqa: E402
from workbench.storage import engine as sto  # noqa: E402
from workbench.storage import ontology_build as store  # noqa: E402

OWNER = 'u-merge-refs-owner'
PASSED = []
FAILED = []
SEQ = [0]


def check(cond, message, actual=None, expected=None):
    SEQ[0] += 1
    if cond:
        PASSED.append(message)
        print('通过 %d) %s' % (SEQ[0], message))
        return True
    FAILED.append(message)
    print('[失败] %d) %s' % (SEQ[0], message))
    if expected is not None:
        print('  预期: %s' % _short(expected))
    if actual is not None:
        print('  实际: %s' % _short(actual))
    return False


def _short(value, limit=500):
    text = repr(value) if not isinstance(value, (dict, list, str)) else str(value)
    return text if len(text) <= limit else text[:limit] + '…'


def bind_owner():
    """绑定假账号（跨账号/他人数据一律不碰）：store 与域层都按此归属隔离。"""
    auth.bind_request({'userId': OWNER, 'username': 'merge_refs_tester'})


# --- 域级：候选工厂与装配辅助 -------------------------------------------------------

def cand(cid, key, ctype, name, **kw):
    base = {'id': cid, 'key': key, 'type': ctype, 'name': name,
            'definition': '%s 的业务定义' % name, 'fields': {}, 'ownerKey': '',
            'evidence': {}, 'evidenceStatus': 'supported', 'conflicts': [],
            'decision': 'include', 'origin': {}}
    base.update(kw)
    return base


def graph_node(ontology, label, node_type=None):
    for node in ontology.get('@graph') or []:
        if node.get('rdfs:label') == label and (node_type is None or node.get('@type') == node_type):
            return node
    return None


def raises_adapter_error(fn):
    try:
        fn()
    except adapter.AdapterError as exc:
        return str(exc)
    return None


def domain_fixture():
    """O2（含别名键）合并进 O1：属性/链接/规则/动作都还按 O2 的旧 key 引用它。"""
    o1 = cand('c-o1', 'obj-device-main', 'object', '设备')
    o2 = cand('c-o2', 'obj-device-alias', 'object', '设备（别名）',
              origin={'mergedInto': 'c-o1'})
    prop = cand('c-p', 'capacity', 'property', '额定容量',
                fields={'dataType': 'number'}, ownerKey='obj-device-alias')
    link = cand('c-l', 'device-link', 'link', '设备连接',
                fields={'sourceRef': 'obj-device-alias', 'targetRef': 'obj-device-main',
                        'cardinality': {'source': 'one', 'target': 'many'}})
    rule = cand('c-r', 'rule-power', 'rule', '功率约束',
                fields={'content': 'p <= 2 * cap'}, ownerKey='obj-device-alias')
    act = cand('c-a', 'act-reset', 'action', '复位告警',
               fields={'effect': '告警被清除'}, ownerKey='obj-device-alias')
    return o1, o2, prop, link, rule, act


def check_domain_alias_resolution():
    o1, _o2, prop, link, rule, act = domain_fixture()
    aliases_key = {'obj-device-alias': 'c-o1'}   # 别名按「被合并候选的 key」建表
    aliases_id = {'c-o2': 'c-o1'}                # 别名按「被合并候选的 ID」建表

    ontology, _wf, id_map, _warn = adapter.assemble([o1, prop], aliases=aliases_key)
    node = graph_node(ontology, '额定容量') or {}
    check(((node.get('rdfs:domain') or {}).get('@id')) == id_map['c-o1'],
          '域级：带别名表时属性 rdfs:domain 指向主对象稳定 id（不再抛错）',
          actual=(node.get('rdfs:domain'), id_map['c-o1']))
    prop_by_id = dict(prop, id='c-p2', key='capacity-by-id', ownerKey='c-o2')
    ontology2, _wf2, id_map2, _warn2 = adapter.assemble([o1, prop_by_id], aliases=aliases_id)
    check(((graph_node(ontology2, '额定容量') or {}).get('rdfs:domain') or {}).get('@id')
          == id_map2['c-o1'],
          '域级：ownerKey 用候选 ID 时，按 ID 建的别名同样规范化到主对象',
          actual=id_map2)

    ontology3, workflow, id_map3, _warn3 = adapter.assemble(
        [o1, prop, link, rule, act], aliases=aliases_key)
    link_node = graph_node(ontology3, '设备连接') or {}
    action_rec = (workflow.get('actions') or [{}])[0]
    rule_rec = (workflow.get('businessRules') or [{}])[0]
    check(((link_node.get('rdfs:domain') or {}).get('@id')) == id_map3['c-o1']
          and ((link_node.get('rdfs:range') or {}).get('@id')) == id_map3['c-o1'],
          '域级：链接两端（sourceRef/targetRef 走别名）都指向主对象',
          actual=(link_node.get('rdfs:domain'), link_node.get('rdfs:range')))
    check(workflow.get('actionAssociations')
          == [{'objectTypeId': id_map3['c-o1'], 'actionId': action_rec.get('id')}]
          and action_rec.get('name') == '复位告警',
          '域级：动作保留在 workflow.actions 且 actionAssociations 指向主对象',
          actual=workflow.get('actionAssociations'))
    check(workflow.get('businessRuleAssociations')
          == [{'objectTypeId': id_map3['c-o1'], 'ruleId': rule_rec.get('id')}],
          '域级：规则 businessRuleAssociations 指向主对象',
          actual=workflow.get('businessRuleAssociations'))
    check(adapter.verify_structure(ontology3, workflow) == [],
          '域级：规范化后的图 + workflow 通过结构自检（引用全在图内）',
          actual=adapter.verify_structure(ontology3, workflow))


def check_domain_without_aliases():
    o1, _o2, prop, link, rule, act = domain_fixture()
    prop_msg = raises_adapter_error(lambda: adapter.assemble([o1, prop]))
    check(prop_msg is not None and '额定容量' in prop_msg,
          '域级反例：不给别名时属性所属对象无法解析 → AdapterError（含候选名）',
          actual=prop_msg)
    act_msg = raises_adapter_error(lambda: adapter.assemble([o1, act]))
    check(act_msg is not None and '复位告警' in act_msg,
          '域级反例：动作宿主非空且解析不到 → AdapterError（不再静默 actionAssociations=[]）',
          actual=act_msg)
    rule_msg = raises_adapter_error(lambda: adapter.assemble([o1, rule]))
    check(rule_msg is not None and '功率约束' in rule_msg,
          '域级反例：规则宿主非空且解析不到 → AdapterError',
          actual=rule_msg)
    link_msg = raises_adapter_error(lambda: adapter.assemble([o1, link]))
    check(link_msg is not None and '设备连接' in link_msg,
          '域级反例：链接端点非空且解析不到 → AdapterError',
          actual=link_msg)

    silent = None
    try:
        _o, wf, _m, _w = adapter.assemble([o1, act])
        silent = wf.get('actionAssociations')
    except adapter.AdapterError:
        silent = 'AdapterError'
    check(silent == 'AdapterError',
          '域级反例：动作装配不可能再返回 actionAssociations=[] 的静默结果',
          actual=silent)

    # 空 ownerKey = 未声明宿主：允许不挂关联，不阻断装配
    orphan = cand('c-a2', 'act-unbound', 'action', '无宿主动作', ownerKey='')
    ontology, workflow, _id_map, _warn = adapter.assemble([o1, orphan])
    check(len(workflow.get('actions') or []) == 1
          and workflow.get('actionAssociations') == []
          and adapter.verify_structure(ontology, workflow) == [],
          '域级：空 ownerKey（未声明宿主）不阻断，动作照常装配、不挂关联',
          actual=workflow.get('actionAssociations'))


def check_domain_alias_edge_cases():
    o1, _o2, prop, _link, _rule, _act = domain_fixture()
    # 别名指向的主候选不在本次选定集合 → 仍按无法解析处理
    outside = raises_adapter_error(
        lambda: adapter.assemble([o1, prop], aliases={'obj-device-alias': 'c-missing'}))
    check(outside is not None and '额定容量' in outside,
          '域级：别名指向未选定候选 → 按无法解析抛 AdapterError（不静默改指）',
          actual=outside)
    # 别名链：适配器接受未展平的链式别名（b 不在选定集合，继续跟到 c）
    chained = adapter.assemble([o1, prop], aliases={'obj-device-alias': 'c-b',
                                                    'c-b': 'c-o1'})
    check(((graph_node(chained[0], '额定容量') or {}).get('rdfs:domain') or {}).get('@id')
          == chained[2]['c-o1'],
          '域级：未展平的别名链继续跟随到最终保留项', actual=chained[2])
    # 纯环别名：必须终止并报错，绝不递归死循环
    cycle = raises_adapter_error(
        lambda: adapter.assemble([o1, prop], aliases={'obj-device-alias': 'c-y',
                                                      'c-y': 'c-z', 'c-z': 'c-y'}))
    check(cycle is not None and '额定容量' in cycle,
          '域级：别名成环 → 有界终止并抛 AdapterError（不死循环、不编造主候选）',
          actual=cycle)


def check_verify_structure_invariants():
    o1, _o2, prop, _link, _rule, act = domain_fixture()
    ontology, workflow, _id_map, _warn = adapter.assemble(
        [o1, prop, act], aliases={'obj-device-alias': 'c-o1'})
    check(adapter.verify_structure(ontology, workflow) == [],
          '域级不变式：别名规范化后的装配无结构 issues', actual=adapter.verify_structure(ontology, workflow))

    dangling = dict(workflow)
    dangling['actionAssociations'] = [{'objectTypeId': 'obj_not_in_graph',
                                       'actionId': (workflow['actions'] or [{}])[0].get('id')}]
    check(adapter.verify_structure(ontology, dangling) != [],
          '域级不变式：动作关联的 objectTypeId 不在 @graph 内 → 结构自检报出',
          actual=adapter.verify_structure(ontology, dangling))

    domainless = {'@context': ontology['@context'],
                  '@graph': [node for node in ontology['@graph']
                             if node.get('rdfs:label') != '额定容量'],
                  'definitionOrder': [node['@id'] for node in ontology['@graph']
                                      if node.get('rdfs:label') != '额定容量']}
    domainless['@graph'].append({'@id': 'prop_free', '@type': 'owl:DatatypeProperty',
                                 'rdfs:label': '游离属性', 'rdfs:comment': 'd',
                                 'rdfs:range': {'@id': 'xsd:string'},
                                 'dataType': {'type': 'text'}})
    domainless['definitionOrder'].append('prop_free')
    check(any('缺少所属对象' in text for text in adapter.verify_structure(domainless)),
          '域级不变式：属性缺 rdfs:domain → 结构自检报出',
          actual=adapter.verify_structure(domainless))

    open_link = {'@context': ontology['@context'],
                 '@graph': list(ontology['@graph']) + [
                     {'@id': 'link_free', '@type': 'owl:ObjectProperty',
                      'rdfs:label': '无端点链接', 'rdfs:comment': 'd',
                      'mg:cardinality': {'@type': '@json', '@value': {'source': 'one',
                                                                     'target': 'many'}}}],
                 'definitionOrder': list(ontology['definitionOrder']) + ['link_free']}
    check(adapter.verify_structure(open_link) != [],
          '域级不变式：链接缺两端引用 → 结构自检报出',
          actual=adapter.verify_structure(open_link))


# --- 真实存储：任务/批次/候选搭建 ---------------------------------------------------

def seed_task(name, seeds):
    """真实 SQLite 根里建任务 + 运行 + 批次 + 候选（含对齐键），返回 (task_id, batch_id)。"""
    def body(conn):
        task_id = store.create_task(conn, OWNER, name)
        run_id, _lease = store.create_run(conn, task_id, OWNER, 'generate',
                                          baseline={'test': name})
        batch_id = store.create_batch(conn, task_id, OWNER, run_id, {'test': name})
        for seed in seeds:
            payload = dict(seed)
            payload.setdefault('alignedKey', 'seed:%s:%s' % (payload.get('type'),
                                                             payload.get('name')))
            store.create_candidate(conn, task_id, OWNER, batch_id, payload)
        store.touch_task(conn, task_id, OWNER, status='review', stage_label='评审初稿')
        return task_id, batch_id
    with sto.write_tx() as tx:
        return tx.run(body)


def candidates_of(task_id, batch_id, include_merged=False):
    with sto.read_connection() as conn:
        return store.all_candidates(conn, task_id, OWNER, batch_id=batch_id,
                                    include_merged=include_merged)


def by_key(items):
    return {str(node.get('key')): node for node in items}


def merge_pair(task_id, primary_id, merged_ids):
    """真实 merge_apply（写 review_op，可撤销），返回 (op_id, 主候选新 revision)。"""
    def body(conn):
        row = store.get_candidate(conn, primary_id, OWNER)
        result = review.merge_apply(conn, task_id, primary_id, merged_ids, row['revision'])
        fresh = store.get_candidate(conn, primary_id, OWNER)
        return result['opId'], fresh['revision']
    with sto.write_tx() as tx:
        return tx.run(body)


def undo_merge(task_id, op_id, revision):
    with sto.write_tx() as tx:
        return tx.run(lambda conn: review.undo_review_op(conn, task_id, op_id, revision))


def read_aliases(task_id, batch_id):
    with sto.read_connection() as conn:
        return delivery._merge_aliases(conn, task_id, OWNER, batch_id)


def precheck(task_id, batch_id=None):
    with sto.read_connection() as conn:
        return delivery.precheck(conn, task_id, batch_id)


def deliver_task(task_id, name, token, request_id):
    with sto.write_tx() as tx:
        return tx.run(lambda conn: delivery.deliver(conn, task_id, name, token, request_id))


def e2e_seeds():
    """O2 是可被合并的重复对象；属性/链接/规则/动作都按 O2 的 key 引用宿主。"""
    return [
        {'type': 'object', 'key': 'obj-device', 'name': '设备', 'definition': '储能设备台账',
         'evidence': {'_record': ['f-1']}, 'evidenceStatus': 'supported', 'decision': 'include'},
        {'type': 'object', 'key': 'obj-device-alias', 'name': '设备别名',
         'definition': '同一设备的另一种叫法', 'evidence': {'_record': ['f-2']},
         'evidenceStatus': 'supported', 'decision': 'include'},
        {'type': 'property', 'key': 'prop-capacity', 'name': '额定容量',
         'definition': '设备额定容量', 'fields': {'dataType': 'number'},
         'ownerKey': 'obj-device-alias', 'evidence': {'_record': ['f-3']},
         'evidenceStatus': 'supported', 'decision': 'include'},
        {'type': 'link', 'key': 'link-device', 'name': '设备连接',
         'definition': '设备与设备的连接', 'ownerKey': '',
         'fields': {'sourceRef': 'obj-device-alias', 'targetRef': 'obj-device',
                    'cardinality': {'source': 'one', 'target': 'many'}},
         'evidence': {'_record': ['f-4']}, 'evidenceStatus': 'supported', 'decision': 'include'},
        {'type': 'rule', 'key': 'rule-power', 'name': '功率约束',
         'definition': '储能设备功率上限', 'fields': {'content': 'p <= 2 * cap'},
         'ownerKey': 'obj-device-alias', 'evidence': {'_record': ['f-5']},
         'evidenceStatus': 'supported', 'decision': 'include'},
        {'type': 'action', 'key': 'act-reset', 'name': '复位告警',
         'definition': '复归设备当前活动告警', 'fields': {'effect': '告警状态被清除'},
         'ownerKey': 'obj-device-alias', 'evidence': {'_record': ['f-6']},
         'evidenceStatus': 'supported', 'decision': 'include'},
    ]


def check_e2e_delivery_after_merge():
    """报告点名要补的真实交付端到端：合并两对象后属性/动作仍指向主对象且交付成功。"""
    task_id, batch_id = seed_task('合并引用交付回归', e2e_seeds())
    items = by_key(candidates_of(task_id, batch_id))
    primary = items['obj-device']
    alias = items['obj-device-alias']
    op_id, primary_revision = merge_pair(task_id, primary['id'], [alias['id']])
    check(bool(op_id), '端到端：真实 store 执行合并（O2 → O1）成功', actual=op_id)

    aliases = read_aliases(task_id, batch_id)
    check(aliases.get('obj-device-alias') == primary['id']
          and aliases.get(alias['id']) == primary['id'],
          '端到端：别名表把被合并候选的 key 与 ID 都映射到主候选 ID',
          actual=aliases)

    pre = precheck(task_id, batch_id)
    token = pre.get('checkToken')
    check(pre.get('ok') is True and not pre.get('issues') and bool(token),
          '端到端：合并后交付预检 ok=true 且无阻断项', actual=(pre.get('ok'), pre.get('issues')))

    delivered = deliver_task(task_id, '合并引用交付本体', token, 'req-merge-refs-e2e')
    ontology_id = delivered.get('ontologyId')
    check(bool(ontology_id), '端到端：deliver() 交付成功并返回 ontologyId',
          actual=delivered.get('ontologyId') or delivered)

    state = workspaces.read_draft(ontology_id) if ontology_id else None
    graph = ((state or {}).get('ontology') or {}).get('@graph') or []
    workflow = (state or {}).get('workflow') or {}
    objects = [node for node in graph if node.get('@type') == 'owl:Class']
    prop_node = graph_node({'@graph': graph}, '额定容量') or {}
    link_node = graph_node({'@graph': graph}, '设备连接') or {}
    with sto.read_connection() as conn:
        asset = asset_store.get_asset(conn, 'model', ontology_id, owner_user_id=OWNER)
    candidate_map = ((asset or {}).get('summary') or {}).get('candidateMap') or {}
    main_id = candidate_map.get(primary['id'])
    check(len(objects) == 1 and objects[0].get('@id') == main_id
          and objects[0].get('rdfs:label') == '设备' and alias['id'] not in candidate_map,
          '端到端：交付草稿只含主对象（被合并候选不进入交付）',
          actual=([node.get('rdfs:label') for node in objects], sorted(candidate_map)))
    check(main_id and ((prop_node.get('rdfs:domain') or {}).get('@id')) == main_id,
          '端到端：读回交付草稿，属性 domain 指向主对象稳定 id',
          actual=(prop_node.get('rdfs:domain'), 'main=%s' % main_id))
    check(main_id and ((link_node.get('rdfs:domain') or {}).get('@id')) == main_id
          and ((link_node.get('rdfs:range') or {}).get('@id')) == main_id,
          '端到端：链接两端也指向主对象稳定 id',
          actual=(link_node.get('rdfs:domain'), link_node.get('rdfs:range')))
    action_rec = (workflow.get('actions') or [{}])[0]
    rule_rec = (workflow.get('businessRules') or [{}])[0]
    action_assoc = workflow.get('actionAssociations') or []
    rule_assoc = workflow.get('businessRuleAssociations') or []
    check(action_assoc == [{'objectTypeId': main_id, 'actionId': candidate_map.get(items['act-reset']['id'])}]
          and candidate_map.get(items['act-reset']['id']) == action_rec.get('id'),
          '端到端：动作关联指向主对象（不再静默 actionAssociations=[]）',
          actual=action_assoc)
    check(rule_assoc == [{'objectTypeId': main_id, 'ruleId': candidate_map.get(items['rule-power']['id'])}],
          '端到端：规则关联指向主对象', actual=rule_assoc)
    draft_issues = adapter.verify_structure(
        {'@graph': graph, 'definitionOrder': [node['@id'] for node in graph]}, workflow)
    check(draft_issues == [] and alias['id'] not in json.dumps(state, ensure_ascii=False),
          '端到端：读回草稿通过结构自检且不含被合并候选的候选 ID',
          actual=draft_issues)
    return {'task_id': task_id, 'batch_id': batch_id, 'primary_revision': primary_revision,
            'main_id': main_id, 'action_assoc': action_assoc}


def check_e2e_undo_merge_blocks():
    """撤销合并后别名消失：属性和动作的宿主解析不到 → 预检被阻断且可定位到候选。"""
    seeds = e2e_seeds()
    for seed in seeds:                       # O2 改为暂缓：合并前它本来不可交付
        if seed['key'] == 'obj-device-alias':
            seed['decision'] = 'defer'
    task_id, batch_id = seed_task('撤销合并阻断回归', seeds)
    items = by_key(candidates_of(task_id, batch_id))
    primary = items['obj-device']
    alias = items['obj-device-alias']
    prop_id = items['prop-capacity']['id']
    act_id = items['act-reset']['id']

    before = precheck(task_id, batch_id)
    by_candidate = {issue.get('candidateId'): issue.get('code')
                    for issue in (before.get('issues') or [])}
    check(before.get('ok') is False and by_candidate.get(prop_id) == 'DEPENDENCY_NOT_INCLUDED'
          and by_candidate.get(act_id) == 'DEPENDENCY_NOT_INCLUDED',
          '撤销场景铺垫：宿主暂缓时预检逐条报可定位依赖阻断（属性与动作都在内）',
          actual=sorted((issue.get('code'), issue.get('candidateId'))
                        for issue in (before.get('issues') or [])))

    op_id, primary_revision = merge_pair(task_id, primary['id'], [alias['id']])
    after_merge = precheck(task_id, batch_id)
    check(after_merge.get('ok') is True and not after_merge.get('issues'),
          '撤销场景铺垫：合并后别名规范化，预检转为通过（被合并宿主的引用不再阻断）',
          actual=after_merge.get('issues'))

    undo_merge(task_id, op_id, primary_revision)
    aliases = read_aliases(task_id, batch_id)
    check('obj-device-alias' not in aliases and alias['id'] not in aliases,
          '撤销合并：别名消失（origin.mergedInto 清空后不再有旧 key 别名）',
          actual=aliases)

    blocked = precheck(task_id, batch_id)
    located = {issue.get('candidateId') for issue in (blocked.get('issues') or [])}
    check(blocked.get('ok') is False and prop_id in located and act_id in located,
          '撤销合并：交付预检被阻断且 issues 能定位到属性和动作候选（不静默通过）',
          actual=sorted((issue.get('code'), issue.get('candidateId'))
                        for issue in (blocked.get('issues') or [])))

    with sto.read_connection() as conn:
        _pool, selected = delivery._selected_candidates(conn, task_id, OWNER, batch_id)
        aliases = delivery._merge_aliases(conn, task_id, OWNER, batch_id)
        assembly_message = raises_adapter_error(lambda: adapter.assemble(selected, aliases))
    check(assembly_message is not None,
          '撤销合并：按当前选定集合装配直接抛 AdapterError（宿主解析不到即阻断）',
          actual=assembly_message)

    try:
        deliver_task(task_id, '撤销合并后不应创建的本体', blocked.get('checkToken'),
                     'req-merge-refs-undo')
        deliver_error = None
    except delivery.DeliveryBlocked as exc:
        deliver_error = [issue.get('code') for issue in exc.issues]
    check(deliver_error is not None and 'DEPENDENCY_NOT_INCLUDED' in deliver_error,
          '撤销合并：deliver() 同样被阻断（预检与交付同口径）', actual=deliver_error)
    return {'task_id': task_id, 'blocked_issues': len(blocked.get('issues') or [])}


def check_e2e_merge_chain_and_cycle():
    """合并链（A→B→C）传递解析到 C；环保护：终止、不编造、后续解析报错。"""
    seeds = [
        {'type': 'object', 'key': 'obj-a', 'name': '对象A', 'definition': 'A',
         'evidence': {'_record': ['f-a']}, 'evidenceStatus': 'supported', 'decision': 'include'},
        {'type': 'object', 'key': 'obj-b', 'name': '对象B', 'definition': 'B',
         'evidence': {'_record': ['f-b']}, 'evidenceStatus': 'supported', 'decision': 'include'},
        {'type': 'object', 'key': 'obj-c', 'name': '对象C', 'definition': 'C',
         'evidence': {'_record': ['f-c']}, 'evidenceStatus': 'supported', 'decision': 'include'},
        {'type': 'property', 'key': 'prop-c', 'name': 'C的属性', 'definition': '挂在 C 上',
         'fields': {'dataType': 'text'}, 'ownerKey': 'obj-c',
         'evidence': {'_record': ['f-p']}, 'evidenceStatus': 'supported', 'decision': 'include'},
    ]
    task_id, batch_id = seed_task('合并链与环保护回归', seeds)
    items = by_key(candidates_of(task_id, batch_id))

    def set_merged(node_id, target_id):
        """直接在真实候选行上写合并指向（merge_apply 拒绝把已合并项作为保留项，
        这里模拟历史批次留下的合并链，真实库真实行、非 mock）。"""
        def body(conn):
            row = store.get_candidate(conn, node_id, OWNER)
            origin = dict(store.candidate_view(row).get('origin') or {})
            origin['mergedInto'] = target_id
            store.update_candidate(conn, node_id, OWNER, origin=origin)
        with sto.write_tx() as tx:
            tx.run(body)

    # merge_apply 拒绝把已被合并的候选作为保留项，因此链按存储层直接写（真实库真实行）
    set_merged(items['obj-b']['id'], items['obj-a']['id'])
    set_merged(items['obj-c']['id'], items['obj-b']['id'])
    aliases = read_aliases(task_id, batch_id)
    check(aliases.get('obj-b') == items['obj-a']['id']
          and aliases.get('obj-c') == items['obj-a']['id']
          and aliases.get(items['obj-c']['id']) == items['obj-a']['id'],
          '合并链：A→B→C 的别名都被传递解析到最终保留项 C 的主候选（A）',
          actual=aliases)

    with sto.read_connection() as conn:
        _items, selected = delivery._selected_candidates(conn, task_id, OWNER, batch_id)
        ontology, _wf, id_map, _warn = adapter.assemble(selected, aliases)
    node = graph_node(ontology, 'C的属性') or {}
    check(((node.get('rdfs:domain') or {}).get('@id')) == id_map[items['obj-a']['id']],
          '合并链：属性（宿主指向链末 C）装配到最终保留项 A 的稳定 id',
          actual=(node.get('rdfs:domain'), id_map[items['obj-a']['id']]))

    set_merged(items['obj-a']['id'], items['obj-c']['id'])      # 成环：A→C→B→A
    cyclic = read_aliases(task_id, batch_id)
    pool_ids = {node['id'] for node in candidates_of(task_id, batch_id, include_merged=True)}
    check(set(cyclic.values()) <= pool_ids,
          '环保护：别名表终止且取值都落在本批次候选 ID（不编造主候选）',
          actual=cyclic)
    with sto.read_connection() as conn:
        _items, selected = delivery._selected_candidates(conn, task_id, OWNER, batch_id)
        cycle_message = raises_adapter_error(lambda: adapter.assemble(selected, cyclic))
        cycle_issues = review.deliver_blockers(conn, task_id, batch_id).get('issues') or []
    check(cycle_message is not None,
          '环保护：环上引用后续解析报错（AdapterError），绝不静默改指或死循环',
          actual=cycle_message)
    check(any(issue.get('candidateId') == items['prop-c']['id'] for issue in cycle_issues),
          '环保护：交付预检同样就该候选产出可定位 issue', actual=cycle_issues)


def main():
    check(str(DATA_ROOT) == str(TMP), '隔离：DATA_ROOT 指向本次临时根', actual=str(DATA_ROOT))
    storage.ensure_ready()
    check(str(sto.resolve_url()).startswith('sqlite:///' + str(TMP)),
          '隔离：数据库 URL 落在临时根内（不碰真实库）', actual=sto.resolve_url())
    bind_owner()

    check_domain_alias_resolution()
    check_domain_without_aliases()
    check_domain_alias_edge_cases()
    check_verify_structure_invariants()
    e2e = check_e2e_delivery_after_merge()
    undo = check_e2e_undo_merge_blocks()
    check_e2e_merge_chain_and_cycle()

    print('\n端到端读回断言：主对象稳定 id=%s；动作关联=%s'
          % (e2e.get('main_id'), e2e.get('action_assoc')))
    print('撤销合并阻断：可定位 issues=%d 条' % undo.get('blocked_issues'))
    print('\n合计 %d 项：通过 %d，失败 %d' % (SEQ[0], len(PASSED), len(FAILED)))


if __name__ == '__main__':
    try:
        main()
    finally:
        try:
            sto.reset_engine()
        except Exception:
            pass
        shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(1 if FAILED else 0)
