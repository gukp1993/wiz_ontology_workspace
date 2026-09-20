"""升级影响匹配（project_impact.binding_impacts）回归（2026-09-20 v2）。

覆盖 P07 修复点：稳定 id 精确匹配（不误报相似 apiName）、共享属性继承
（sharedPropertyId 命中不遗漏）、值类型继承、未绑定定义不产生绑定级影响、
链接映射按稳定 id 与 apiName 命中、契约影响的实现与计算来源定位。

纯 python3 标准库直跑，不启动服务、不访问真实数据。
运行：python3 tests/test_upgrade_impact.py
"""
import copy
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('WIZ_WORKBENCH_ROOT', __import__('tempfile').mkdtemp(prefix='wiz_upgrade_impact_'))

from workbench import contracts  # noqa: E402
from workbench.project_impact import binding_impacts  # noqa: E402

FAILURES = []


def run(name, fn):
    try:
        fn()
        print(f'通过  {name}')
    except Exception as exc:
        FAILURES.append(name)
        print(f'失败  {name}: {type(exc).__name__}: {exc}')


def onto(properties, shared=(), value_types=(), links=()):
    graph = [{'@id': 'mg:Station', '@type': 'owl:Class', 'rdfs:label': '储能单元'}]
    for rec in properties:
        node = {'@id': rec['id'], '@type': 'owl:DatatypeProperty',
                'rdfs:label': rec.get('label', rec.get('apiName', '')),
                'rdfs:domain': {'@id': 'mg:' + rec.get('objectTypeId', 'Station')},
                'mg:apiName': rec.get('apiName', ''), 'rdfs:range': {'@id': 'xsd:double'}}
        if rec.get('sharedPropertyId'):
            node['mg:sharedProperty'] = {'@id': rec['sharedPropertyId']}
        if rec.get('valueTypeId'):
            node['mg:valueType'] = {'@id': rec['valueTypeId']}
        graph.append(node)
    for rec in shared:
        graph.append({'@id': rec['id'], '@type': 'mg:SharedProperty',
                      'rdfs:label': rec.get('label', ''), 'mg:apiName': rec.get('apiName', '')})
    for rec in value_types:
        graph.append({'@id': rec['id'], '@type': 'mg:ValueType', 'rdfs:label': rec.get('label', '')})
    for rec in links:
        graph.append({'@id': rec['id'], '@type': 'owl:ObjectProperty',
                      'rdfs:label': rec.get('label', ''),
                      'rdfs:domain': {'@id': 'mg:Station'}, 'rdfs:range': {'@id': 'mg:Battery'},
                      'mg:cardinality': rec.get('cardinality', 'one-to-many')})
    return {'ontology': {'@context': {}, '@graph': graph}, 'workflow': {}}


def project(bound, relations=(), implementations=()):
    return {'projectId': 'p1',
            'bindings': {'object_bindings': [{'object_type': 'Station', 'connection': 'c1',
                                              'table': 'station', 'primary_key': 'id',
                                              'properties': dict(bound), 'relations': list(relations)}]},
            'implementations': list(implementations)}


def diff_impacts(state, current, target):
    diff = contracts.classify(current, target)
    impacts, matched = binding_impacts(state, current, target, diff['reasons'])
    return diff, impacts, matched


def t_similar_api_name_not_matched():
    """误报修复：绑定 apiName 'power' 不得命中属性 'mg:max_power' 的变化（旧子串匹配会误报）。"""
    current = onto([{'id': 'mg:max_power', 'apiName': 'max_power'}, {'id': 'mg:power', 'apiName': 'power'}])
    target = onto([{'id': 'mg:power', 'apiName': 'power'}])          # 删除 max_power（breaking）
    state = project({'power': {'kind': 'field', 'field': 'power'}})  # 只绑定 power
    diff, impacts, matched = diff_impacts(state, current, target)
    assert ('properties', 'mg:max_power') not in matched, f'相似 apiName 不应误报：{impacts}'
    assert not any(i['area'] == 'propertyMapping' for i in impacts), f'未绑定定义不产生属性影响：{impacts}'


def t_shared_property_inheritance_matched():
    """漏报修复：共享属性定义变化经 sharedPropertyId 命中属性绑定。"""
    current = onto([{'id': 'mg:rated_power2', 'apiName': 'rated_power2', 'sharedPropertyId': 'mg:sp_1'}],
                   shared=[{'id': 'mg:sp_1', 'apiName': 'shared_rated_power', 'label': '共享额定功率'}])
    target = onto([{'id': 'mg:rated_power2', 'apiName': 'rated_power2', 'sharedPropertyId': 'mg:sp_1'}],
                  shared=[{'id': 'mg:sp_1', 'apiName': 'shared_rated_power', 'label': '共享额定功率2'}])
    state = project({'rated_power2': {'kind': 'field', 'field': 'rated_power2'}})
    diff, impacts, matched = diff_impacts(state, current, target)
    assert any(r['area'] == 'sharedProperties' and r['id'] == 'mg:sp_1' for r in diff['reasons']), diff['reasons']
    assert ('sharedProperties', 'mg:sp_1') in matched, f'共享属性变化应命中绑定：{impacts}'
    assert any(i['area'] == 'propertyMapping' and i['ref'] == 'Station.rated_power2' for i in impacts), impacts


def t_value_type_inheritance_matched():
    current = onto([{'id': 'mg:soc', 'apiName': 'soc', 'valueTypeId': 'mg:vt_1'}],
                   value_types=[{'id': 'mg:vt_1', 'label': '占比'}])
    target = onto([{'id': 'mg:soc', 'apiName': 'soc', 'valueTypeId': 'mg:vt_1'}],
                  value_types=[{'id': 'mg:vt_1', 'label': '占比（已改）'}])
    state = project({'soc': {'kind': 'field', 'field': 'soc'}})
    diff, impacts, matched = diff_impacts(state, current, target)
    assert ('valueTypes', 'mg:vt_1') in matched, f'值类型变化应命中绑定：{impacts}'


def t_unbound_definition_no_impact():
    """未绑定定义的变化不产生绑定级影响（交由调用方「未直接引用」兜底）。"""
    current = onto([{'id': 'mg:health', 'apiName': 'health'}, {'id': 'mg:power', 'apiName': 'power'}])
    target = onto([{'id': 'mg:power', 'apiName': 'power'}])
    state = project({'power': {'kind': 'field', 'field': 'power'}})
    diff, impacts, matched = diff_impacts(state, current, target)
    assert ('properties', 'mg:health') not in matched, f'未绑定属性不得命中：{impacts}'
    assert impacts == [], f'未绑定定义不应产生绑定级影响：{impacts}'


def t_object_and_link_matching():
    current = onto([], links=[{'id': 'mg:hasBattery', 'label': '包含电池'}])
    target = onto([], links=[])
    state = project({}, relations=[{'relation': 'hasBattery', 'field': 'x', 'targetField': 'y'}])
    diff, impacts, matched = diff_impacts(state, current, target)
    assert ('linkTypes', 'mg:hasBattery') in matched, f'链接删除应命中：{impacts}'
    assert any(i['area'] == 'linkMapping' and i['ref'] == 'Station.hasBattery' for i in impacts), impacts
    # 相似链接名不误报：绑定 belongsToStation 不得命中 hasBattery 的删除
    state2 = project({}, relations=[{'relation': 'belongsToStation', 'field': 'x', 'targetField': 'y'}])
    _diff2, impacts2, matched2 = diff_impacts(state2, current, target)
    assert ('linkTypes', 'mg:hasBattery') not in matched2, f'相似链接名不应误报：{impacts2}'


def t_contract_impacts():
    def fn(version):
        return {'id': 'fn1', 'name': '取数', 'guide_version': 3, 'no_inputs': True,
                'inputs': [], 'outputs': [{'id': 'o1', 'name': 'out', 'ref': {'kind': 'base', 'dataType': 'double'}}],
                'conventions': '', 'applicable_objects': [], 'note': version}
    current = onto([{'id': 'mg:power', 'apiName': 'power'}])
    current['workflow'] = {'functions': [fn('a')]}
    target = copy.deepcopy(current)
    target['workflow'] = {'functions': [fn('b')]}  # 契约仅改说明 → compatible，但仍定位影响
    state = project({'power': {'kind': 'computed', 'implementation': 'impl1', 'output': 'o1'}},
                    implementations=[{'id': 'impl1', 'contractId': 'fn1'}])
    diff, impacts, matched = diff_impacts(state, current, target)
    assert ('contracts', 'fn1') in matched, f'契约变化应命中：{impacts}'
    assert any(i['area'] == 'implementation' and i['ref'] == 'impl1' for i in impacts), impacts
    assert any(i['area'] == 'propertySource' and i['ref'] == 'Station.power' for i in impacts), impacts


def t_schema_shape_supported():
    """JSON schema 形态（记录字段结构化）同样支持。"""
    current = {'ontology': {'schemaVersion': 1,
                            'objectTypes': [{'id': 'mg:Station'}],
                            'properties': [{'id': 'mg:max_power', 'apiName': 'max_power', 'objectTypeId': 'mg:Station'},
                                           {'id': 'mg:power', 'apiName': 'power', 'objectTypeId': 'mg:Station'}],
                            'linkTypes': [], 'sharedProperties': [], 'valueTypes': []},
               'workflow': {}}
    target = copy.deepcopy(current)
    target['ontology']['properties'] = [r for r in target['ontology']['properties'] if r['id'] != 'mg:max_power']
    state = project({'power': {'kind': 'field', 'field': 'power'}})
    diff, impacts, matched = diff_impacts(state, current, target)
    assert impacts == [], f'schema 形态：未绑定定义不应误报：{impacts}'


if __name__ == '__main__':
    run('a) 相似 apiName 不误报（稳定 id 精确匹配）', t_similar_api_name_not_matched)
    run('b) 共享属性继承 sharedPropertyId 不遗漏', t_shared_property_inheritance_matched)
    run('c) 值类型继承 valueTypeId 命中', t_value_type_inheritance_matched)
    run('d) 未绑定定义不产生绑定级影响', t_unbound_definition_no_impact)
    run('e) 链接映射按稳定 id 命中且相似名不误报', t_object_and_link_matching)
    run('f) 契约变化定位实现与计算来源', t_contract_impacts)
    run('g) JSON schema 形态兼容', t_schema_shape_supported)
    if FAILURES:
        print(f'\n{len(FAILURES)} 项失败：{", ".join(FAILURES)}')
        sys.exit(1)
    print('\n全部通过')
