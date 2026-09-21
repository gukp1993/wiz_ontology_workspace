"""Q02 深度测试公共构造器（只读业务代码，仅测试用）。

- 用 @graph（JSON-LD 编辑）形态构造本体状态；读回统一走 encode_state 的 schema 形态。
- 提供逐字段 diff 工具，用于「保存后读回不一致」类断言。
"""
import copy
import json
import sys
from pathlib import Path

REPO = str(Path(__file__).resolve().parents[1])
if REPO not in sys.path:
    sys.path.insert(0, REPO)
sys.path.insert(0, str(Path(__file__).resolve().parent))

from deep_ontology_client import blank_state  # noqa: E402
from workbench.model_format import encode_state  # noqa: E402

CARDINALITIES = ('one-to-one', 'one-to-many', 'many-to-one', 'many-to-many')


def obj(node_id, label, comment='业务定义占位'):
    return {'@id': 'mg:' + node_id, '@type': 'owl:Class', 'rdfs:label': label, 'rdfs:comment': comment}


def private_prop(node_id, label, owner_id, xsd='double', series=False, shared_ref=None):
    node = {'@id': 'mg:' + node_id, '@type': 'owl:DatatypeProperty', 'rdfs:label': label,
            'rdfs:domain': {'@id': 'mg:' + owner_id}, 'rdfs:range': {'@id': 'xsd:' + xsd}}
    if series:
        node['mg:valueShape'] = 'timeSeries'
    if shared_ref:
        node['mg:sharedProperty'] = {'@id': shared_ref}
    return node


def shared_prop(node_id, label, xsd='double', series=False):
    node = {'@id': 'mg:' + node_id, '@type': 'mg:SharedProperty', 'rdfs:label': label,
            'rdfs:range': {'@id': 'xsd:' + xsd}}
    if series:
        node['mg:valueShape'] = 'timeSeries'
    return node


def link(node_id, label, src_id, tgt_id, cardinality='one-to-many', reverse='被包含'):
    return {'@id': 'mg:' + node_id, '@type': 'owl:ObjectProperty', 'rdfs:label': label,
            'rdfs:domain': {'@id': src_id}, 'rdfs:range': {'@id': tgt_id},
            'mg:cardinality': cardinality, 'mg:reverseLabel': reverse}


def state_with(graph, name='Q02本体'):
    st = blank_state(name)
    st['ontology']['@graph'] = copy.deepcopy(graph)
    return st


def schema_of(state):
    """本地按服务端同一函数 encode，作为读回比对基线。"""
    return encode_state(state)


def pick_schema(state, groups=('objectTypes', 'linkTypes', 'properties', 'sharedProperties', 'valueTypes')):
    onto = state['ontology']
    out = {g: copy.deepcopy(onto.get(g, [])) for g in groups}
    if 'definitionOrder' in onto:
        out['definitionOrder'] = list(onto['definitionOrder'])
    return out


def diff_records(sent_groups, got_groups):
    """按 id 比对两组 schema 记录，返回不一致明细。"""
    problems = []
    if 'definitionOrder' in sent_groups or 'definitionOrder' in got_groups:
        if sent_groups.get('definitionOrder') != got_groups.get('definitionOrder'):
            problems.append(f'definitionOrder 不一致 发={sent_groups.get("definitionOrder")} 回={got_groups.get("definitionOrder")}')
    for group in sent_groups:
        if group == 'definitionOrder':
            continue
        sent = {r.get('id'): r for r in sent_groups.get(group, [])}
        got = {r.get('id'): r for r in got_groups.get(group, [])}
        for rid in sorted(set(sent) | set(got)):
            if rid not in got:
                problems.append(f'{group}:{rid} 读回缺失')
            elif rid not in sent:
                problems.append(f'{group}:{rid} 读回多出')
            elif sent[rid] != got[rid]:
                problems.append(f'{group}:{rid} 字段不同 发={json.dumps(sent[rid], ensure_ascii=False)} 回={json.dumps(got[rid], ensure_ascii=False)}')
    return problems


def find_record(state_schema, group, rid):
    for r in state_schema['ontology'].get(group, []):
        if r.get('id') == rid:
            return r
    return None


def rule(rule_id='rule-q02', name='异常判定规则', description='识别运行异常。', **extra):
    row = {'id': rule_id, 'name': name, 'description': description}
    row.update(copy.deepcopy(extra))
    return row


def action_v2(action_id='act-q02', name='切换系统', description='把负载切换到备用系统。',
              status='active', **extra):
    row = {'id': action_id, 'name': name, 'description': description,
           'status': status, 'definitionVersion': 2}
    row.update(copy.deepcopy(extra))
    return row
