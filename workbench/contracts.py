"""V3 structured calculation contracts and ontology change classification.

Contracts live in workflow.functions with guide_version 3: a machine-checkable
signature (named inputs / named outputs referencing ontology types) plus free
text for business conventions. Legacy function records are preserved verbatim
and surfaced as migration-pending items instead of being rewritten.
"""
import json
from uuid import uuid4

BASE_TYPES = ('string', 'double', 'integer', 'boolean', 'timestamp', 'array', 'struct', 'timeSeries')


def is_contract(record):
    return isinstance(record, dict) and record.get('guide_version') == 3


def make_contract(name=''):
    return {
        'id': str(uuid4()), 'name': name, 'description': '', 'status': 'experimental',
        'guide_version': 3, 'no_inputs': False, 'inputs': [], 'outputs': [],
        'conventions': '', 'applicable_objects': [],
    }


def new_signature_id(prefix):
    return prefix + '_' + uuid4().hex[:12]


def ref_label(ref, graph):
    """Resolve a signature reference to a display label for messages."""
    if not isinstance(ref, dict):
        return ''
    if ref.get('kind') == 'base':
        return {'string': '文本', 'double': '数值', 'integer': '整数', 'boolean': '是/否',
                'timestamp': '时间', 'array': '数组', 'struct': '结构体', 'timeSeries': '时间序列'}.get(ref.get('dataType'), ref.get('dataType', ''))
    target = next((n for n in graph if n['@id'] == ref.get('id')), None)
    return (target or {}).get('rdfs:label', ref.get('id', ''))


def signature_errors(state):
    """Structural checks for V3 contracts; called from workflow.definition_errors."""
    w = state.get('workflow', {})
    graph = state['ontology']['@graph']
    classes = {n['@id'] for n in graph if n['@type'] == 'owl:Class'}
    props = {n['@id'] for n in graph if n['@type'] in ('mg:SharedProperty', 'owl:DatatypeProperty')}
    errors = []
    for n in w.get('functions', []):
        if not is_contract(n):
            continue
        label = n.get('name') or n['id']
        if n.get('no_inputs') and n.get('inputs'):
            errors.append(f'{label} 已声明无输入，不能同时填写输入参数')
        seen = set()
        for group, items, require_one in (('输入参数', n.get('inputs') or [], False), ('输出', n.get('outputs') or [], True)):
            if require_one and not items:
                errors.append(f'{label} 至少声明一个输出')
            for item in items:
                if not isinstance(item, dict) or not item.get('id'):
                    errors.append(f'{label} 的{group}缺少稳定标识，请通过表单重新添加')
                    continue
                if item['id'] in seen:
                    errors.append(f'{label} 的签名标识 {item["id"]} 重复')
                seen.add(item['id'])
                name = str(item.get('name', '')).strip()
                if not name:
                    errors.append(f'{label} 的{group}缺少名称')
                ref = item.get('ref')
                if not isinstance(ref, dict):
                    errors.append(f'{label} 的{group}「{name or item["id"]}」未选择类型引用')
                    continue
                if ref.get('kind') == 'object' and ref.get('id') not in classes:
                    errors.append(f'{label} 的{group}「{name}」引用不存在的对象类型')
                elif ref.get('kind') == 'property' and ref.get('id') not in props:
                    errors.append(f'{label} 的{group}「{name}」引用不存在的属性')
                elif ref.get('kind') == 'base' and ref.get('dataType') not in BASE_TYPES:
                    errors.append(f'{label} 的{group}「{name}」基础数据类型无效')
                elif ref.get('kind') not in ('object', 'property', 'base'):
                    errors.append(f'{label} 的{group}「{name}」类型引用无效')
                if isinstance(ref, dict) and ref.get('kind') == 'base' and ref.get('dataType') == 'timeSeries':
                    from workbench.model_format import SERIES_VALUE_TYPES
                    if not isinstance(ref.get('valueType'), str) or ref['valueType'] not in SERIES_VALUE_TYPES:errors.append(f'{label} 的{group}「{name}」时间序列观测值类型无效')
        applicable = n.get('applicable_objects') or []
        if any(t not in classes for t in applicable):
            errors.append(f'{label} 适用对象引用了不存在的对象类型')
        if len(set(applicable)) != len(applicable):
            errors.append(f'{label} 适用对象重复')
    return errors


# --- change classification between two ontology states -----------------------

GROUP_LABELS = {'objectTypes': '对象类型', 'linkTypes': '链接类型', 'properties': '属性',
                'sharedProperties': '共享属性', 'valueTypes': '值类型'}
# Fields whose change invalidates stable references or value semantics.
STRUCTURAL_FIELDS = {
    'objectTypes': set(),
    'linkTypes': {'sourceObjectTypeId', 'targetObjectTypeId', 'cardinality'},
    'properties': {'objectTypeId', 'dataType', 'apiName', 'sharedPropertyId', 'valueTypeId'},
    'sharedProperties': {'dataType', 'apiName', 'valueTypeId', 'valueSuffix'},
    'valueTypes': {'dataType', 'apiName', 'constraint'},
}
FIELD_LABELS = {'sourceObjectTypeId': '起点', 'targetObjectTypeId': '终点', 'cardinality': '数量关系',
                'objectTypeId': '所属对象', 'dataType': '数据类型', 'apiName': '技术标识', 'sharedPropertyId': '共享定义',
                'valueTypeId': '值类型', 'valueSuffix': '单位后缀', 'constraint': '约束'}


def _as_schema(state):
    from workbench.model_format import encode_ontology
    ontology = state.get('ontology', {})
    if 'schemaVersion' in ontology:
        return ontology
    return encode_ontology(ontology)


def _norm_shape(record):
    """Compare equivalent old/new type representations without a breaking change."""
    from workbench.model_format import canonical_record
    return canonical_record(record)


def _contract_signature(record):
    def item_sig(item):
        ref = item.get('ref') or {}
        return [item.get('id'), ref.get('kind'), ref.get('id'), ref.get('dataType'), ref.get('valueType')]
    return {
        'no_inputs': bool(record.get('no_inputs')),
        'inputs': sorted(map(item_sig, record.get('inputs') or [])),
        'outputs': sorted(map(item_sig, record.get('outputs') or [])),
    }


def classify(old_state, new_state):
    """Compare two ontology states; suggest a change type with reasons.

    Returns {'type': 'breaking'|'compatible'|'pending'|'initial', 'reasons': [...]}.
    'pending' means the structural diff is not decisive and a human must confirm.
    """
    reasons = []
    old, new = _as_schema(old_state), _as_schema(new_state)
    for group, label in GROUP_LABELS.items():
        old_map = {r['id']: r for r in old.get(group, [])}
        new_map = {r['id']: r for r in new.get(group, [])}
        for rid in sorted(set(old_map) - set(new_map)):
            reasons.append({'severity': 'breaking', 'area': group, 'id': rid,
                            'text': f'删除{label} {rid}'})
        for rid in sorted(set(new_map) - set(old_map)):
            reasons.append({'severity': 'compatible', 'area': group, 'id': rid,
                            'text': f'新增{label} {rid}'})
        for rid in sorted(set(old_map) & set(new_map)):
            a, b = old_map[rid], new_map[rid]
            if group in ('properties', 'sharedProperties'):
                a, b = _norm_shape(a), _norm_shape(b)
            changed = [k for k in set(a) | set(b) if a.get(k) != b.get(k)]
            if not changed:
                continue
            structural = [k for k in changed if k in STRUCTURAL_FIELDS[group]]
            if structural:
                fields = '、'.join(FIELD_LABELS.get(k, k) for k in sorted(structural))
                reasons.append({'severity': 'breaking', 'area': group, 'id': rid,
                                'text': f'{label} {rid} 的{fields}发生变化'})
            else:
                reasons.append({'severity': 'compatible', 'area': group, 'id': rid,
                                'text': f'{label} {rid} 仅调整名称或说明'})
    old_f = {f['id']: f for f in old_state.get('workflow', {}).get('functions', [])}
    new_f = {f['id']: f for f in new_state.get('workflow', {}).get('functions', [])}
    for fid in sorted(set(old_f) - set(new_f)):
        reasons.append({'severity': 'breaking', 'area': 'contracts', 'id': fid, 'text': f'删除计算契约 {fid}'})
    for fid in sorted(set(new_f) - set(old_f)):
        reasons.append({'severity': 'compatible', 'area': 'contracts', 'id': fid, 'text': f'新增计算契约 {fid}'})
    for fid in sorted(set(old_f) & set(new_f)):
        a, b = old_f[fid], new_f[fid]
        if a == b:
            continue
        if is_contract(a) and is_contract(b):
            if _contract_signature(a) != _contract_signature(b):
                reasons.append({'severity': 'breaking', 'area': 'contracts', 'id': fid,
                                'text': f'计算契约 {fid} 的输入或输出签名发生变化'})
            else:
                reasons.append({'severity': 'compatible', 'area': 'contracts', 'id': fid,
                                'text': f'计算契约 {fid} 仅调整名称、说明或业务约定'})
        else:
            reasons.append({'severity': 'pending', 'area': 'contracts', 'id': fid,
                            'text': f'历史定义 {fid} 发生变化，请人工确认业务影响'})
    for key, label in (('actions', '动作定义'), ('interfaces', '接口定义')):
        if key == 'actions':
            # 动作按记录比较（20260917 需求）：删除已关联能力=破坏性；业务效果等文本变化=待确认。
            old_a = {a.get('id'): a for a in old_state.get('workflow', {}).get('actions', []) if isinstance(a, dict) and a.get('id')}
            new_a = {a.get('id'): a for a in new_state.get('workflow', {}).get('actions', []) if isinstance(a, dict) and a.get('id')}
            for aid in sorted(set(old_a) - set(new_a)):
                name = old_a[aid].get('name') or aid
                reasons.append({'severity': 'breaking', 'area': 'actions', 'id': aid,
                                'text': f'删除动作定义 {name}（{aid}）'})
            for aid in sorted(set(new_a) - set(old_a)):
                name = new_a[aid].get('name') or aid
                reasons.append({'severity': 'compatible', 'area': 'actions', 'id': aid,
                                'text': f'新增动作定义 {name}（{aid}）'})
            for aid in sorted(set(old_a) & set(new_a)):
                if json.dumps(old_a[aid], sort_keys=True, ensure_ascii=False) != json.dumps(new_a[aid], sort_keys=True, ensure_ascii=False):
                    reasons.append({'severity': 'pending', 'area': 'actions', 'id': aid,
                                    'text': f'动作定义 {new_a[aid].get("name") or aid} 发生变化，请人工确认业务影响'})
        else:
            a = json.dumps(old_state.get('workflow', {}).get(key, []), sort_keys=True, ensure_ascii=False)
            b = json.dumps(new_state.get('workflow', {}).get(key, []), sort_keys=True, ensure_ascii=False)
            if a != b:
                reasons.append({'severity': 'pending', 'area': key, 'id': '', 'text': f'{label}发生变化，请人工确认业务影响'})
    old_rel = {(r.get('objectTypeId'), r.get('actionId')) for r in old_state.get('workflow', {}).get('actionAssociations', []) if isinstance(r, dict)}
    new_rel = {(r.get('objectTypeId'), r.get('actionId')) for r in new_state.get('workflow', {}).get('actionAssociations', []) if isinstance(r, dict)}
    for pair in sorted(old_rel - new_rel, key=str):
        reasons.append({'severity': 'breaking', 'area': 'actionAssociations', 'id': '',
                        'text': f'移除对象动作关联 {pair[0]} + {pair[1]}'})
    for pair in sorted(new_rel - old_rel, key=str):
        reasons.append({'severity': 'compatible', 'area': 'actionAssociations', 'id': '',
                        'text': f'新增对象动作关联 {pair[0]} + {pair[1]}'})
    # 业务规则一期（20260917）：新增/新增引用=兼容；删除已发布定义/引用=破坏性；
    # 业务定义/规则内容/输出变化=待确认；仅名称变化=兼容。
    old_r = {r.get('id'): r for r in old_state.get('workflow', {}).get('businessRules', []) if isinstance(r, dict) and r.get('id')}
    new_r = {r.get('id'): r for r in new_state.get('workflow', {}).get('businessRules', []) if isinstance(r, dict) and r.get('id')}
    for rid in sorted(set(old_r) - set(new_r)):
        reasons.append({'severity': 'breaking', 'area': 'businessRules', 'id': rid,
                        'text': f'删除业务规则 {old_r[rid].get("name") or rid}'})
    for rid in sorted(set(new_r) - set(old_r)):
        reasons.append({'severity': 'compatible', 'area': 'businessRules', 'id': rid,
                        'text': f'新增业务规则 {new_r[rid].get("name") or rid}'})
    for rid in sorted(set(old_r) & set(new_r)):
        a, b = old_r[rid], new_r[rid]
        if json.dumps(a, sort_keys=True, ensure_ascii=False) == json.dumps(b, sort_keys=True, ensure_ascii=False):
            continue
        text_only = all(a.get(k) == b.get(k) for k in ('description', 'content', 'output'))
        name = b.get('name') or rid
        if text_only:
            reasons.append({'severity': 'compatible', 'area': 'businessRules', 'id': rid,
                            'text': f'业务规则 {name} 仅调整名称'})
        else:
            reasons.append({'severity': 'pending', 'area': 'businessRules', 'id': rid,
                            'text': f'业务规则 {name} 的定义、内容或输出发生变化，请人工确认业务影响'})
    old_rr = {(r.get('objectTypeId'), r.get('ruleId')) for r in old_state.get('workflow', {}).get('businessRuleAssociations', []) if isinstance(r, dict)}
    new_rr = {(r.get('objectTypeId'), r.get('ruleId')) for r in new_state.get('workflow', {}).get('businessRuleAssociations', []) if isinstance(r, dict)}
    for pair in sorted(old_rr - new_rr, key=str):
        reasons.append({'severity': 'breaking', 'area': 'businessRuleAssociations', 'id': '',
                        'text': f'移除对象规则引用 {pair[0]} + {pair[1]}'})
    for pair in sorted(new_rr - old_rr, key=str):
        reasons.append({'severity': 'compatible', 'area': 'businessRuleAssociations', 'id': '',
                        'text': f'新增对象规则引用 {pair[0]} + {pair[1]}'})
    for key in ('metrics', 'rules'):
        a = json.dumps(old_state.get(key), sort_keys=True, ensure_ascii=False)
        b = json.dumps(new_state.get(key), sort_keys=True, ensure_ascii=False)
        if a != b:
            reasons.append({'severity': 'pending', 'area': key, 'id': '', 'text': '历史规则或指标定义发生变化，请人工确认业务影响'})
    severities = {r['severity'] for r in reasons}
    if 'breaking' in severities:
        change_type = 'breaking'
    elif 'pending' in severities:
        change_type = 'pending'
    elif reasons:
        change_type = 'compatible'
    else:
        change_type = 'compatible'
    return {'type': change_type, 'reasons': reasons}


def value_source_todos(ontology_state):
    """Legacy property valueSource entries that should migrate into project bindings."""
    todos = []
    for node in ontology_state.get('ontology', {}).get('@graph', []):
        source = node.get('mg:valueSource', {}).get('@value')
        if node.get('@type') == 'owl:DatatypeProperty' and isinstance(source, dict) and source.get('kind') == 'function':
            todos.append({'propertyId': node['@id'],
                          'ownerType': node.get('rdfs:domain', {}).get('@id', ''),
                          'functionId': source.get('functionId', ''),
                          'arguments': source.get('arguments', '')})
    return todos
