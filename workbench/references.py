"""草稿引用完整性：稳定 ID 引用的目标必须存在（20260920 需求 11～13 保存边界）。

与 validate（必填/命名/结构类校验）的分工：
* validate.errors 是**填写提示**，不阻断保存（草稿允许带错保存）；
* 本模块只回答一个问题：**当前草稿里有没有指向不存在稳定 ID 的引用**（悬空引用）。
  保存边界据此比较「本次保存是否把此前有效的引用改坏」，只阻断**新引入**的悬空引用，
  历史遗留的失效引用允许继续保存（需求 §6：不能把草稿永久锁死，历史版本引用不失效）。

检查范围（接口文档 02 §2.2 已登记）：
1. 图内 rdfs:domain / rdfs:range（对象类型）；
2. 契约签名 inputs/outputs 的 ref:{kind:object|property}；
3. 动作/规则关联的 objectTypeId / ruleId / actionId；
4. 属性/共享属性的 mg:valueType 值类型引用；
5. 指标（metrics）的 rule_ref / applicable_types；旧 rules 的成员类型与关系。
"""

KIND_CLASS = 'owl:Class'
KIND_OBJECT_PROP = 'owl:ObjectProperty'
KIND_DATATYPE_PROP = 'owl:DatatypeProperty'
KIND_SHARED_PROP = 'mg:SharedProperty'
KIND_VALUE_TYPE = 'mg:ValueType'


def _full(type_id):
    s = str(type_id or '').strip()
    return s if (not s or s.startswith('mg:')) else 'mg:' + s


def _ref_id(node, key):
    value = (node or {}).get(key)
    if isinstance(value, dict):
        return str(value.get('@id') or '')
    return str(value or '')


def broken_references(state):
    """当前草稿的悬空引用清单（每条含业务名称与稳定 ID，便于前端定位）。"""
    errors = []
    ontology = (state or {}).get('ontology') or {}
    graph = ontology.get('@graph') if isinstance(ontology.get('@graph'), list) else []
    by_id = {n.get('@id'): n for n in graph if isinstance(n, dict) and n.get('@id')}
    classes = {n['@id'] for n in by_id.values() if n.get('@type') == KIND_CLASS}
    properties = {n['@id'] for n in by_id.values() if n.get('@type') in (KIND_SHARED_PROP, KIND_DATATYPE_PROP)}
    value_types = {n['@id'] for n in by_id.values() if n.get('@type') == KIND_VALUE_TYPE}

    def name_of(node, fallback=''):
        return str((node or {}).get('rdfs:label') or fallback or (node or {}).get('@id') or '')

    # 1) 图内 domain / range / 值类型引用
    for node in by_id.values():
        label = name_of(node)
        domain = _ref_id(node, 'rdfs:domain')
        if domain and domain.startswith('mg:') and domain not in classes:
            errors.append(f'「{label}」所属的对象类型不存在（{domain}）')
        range_ref = _ref_id(node, 'rdfs:range')
        if node.get('@type') == KIND_OBJECT_PROP and range_ref.startswith('mg:') and range_ref not in classes:
            errors.append(f'链接「{label}」的终点对象类型不存在（{range_ref}）')
        shared = _ref_id(node, 'mg:sharedProperty')
        if shared and shared not in properties and by_id.get(shared, {}).get('@type') != KIND_SHARED_PROP:
            errors.append(f'属性「{label}」引用的共享定义不存在（{shared}）')
        vt = _ref_id(node, 'mg:valueType')
        if vt and vt.startswith('mg:') and vt not in value_types:
            errors.append(f'属性「{label}」引用的值类型不存在（{vt}）')

    # 2) 契约签名（V3：inputs/outputs 的 ref:{kind,id}）
    workflow = (state or {}).get('workflow') or {}
    for fn in workflow.get('functions') or []:
        if not isinstance(fn, dict):
            continue
        label = fn.get('name') or fn.get('id') or '未命名契约'
        for slot, items in (('inputs', fn.get('inputs') or []), ('outputs', fn.get('outputs') or [])):
            for item in items:
                if not isinstance(item, dict):
                    continue
                ref = item.get('ref') if isinstance(item.get('ref'), dict) else {}
                kind, ref_id = ref.get('kind'), str(ref.get('id') or '')
                if not ref_id:
                    continue
                slot_label = '输入' if slot == 'inputs' else '输出'
                if kind == 'object' and ref_id not in classes:
                    errors.append(f'契约「{label}」的{slot_label}「{item.get("name") or ref_id}」引用不存在的对象类型（{ref_id}）')
                elif kind == 'property' and ref_id not in properties:
                    errors.append(f'契约「{label}」的{slot_label}「{item.get("name") or ref_id}」引用不存在的属性（{ref_id}）')

    # 3) 动作 / 规则关联
    rule_ids = {r.get('id') for r in (workflow.get('businessRules') or []) if isinstance(r, dict)}
    action_ids = {a.get('id') for a in (workflow.get('actions') or []) if isinstance(a, dict)}
    for assoc in workflow.get('businessRuleAssociations') or []:
        if not isinstance(assoc, dict):
            continue
        if _full(assoc.get('objectTypeId')) not in classes:
            errors.append(f'规则关联引用的对象类型不存在（{assoc.get("objectTypeId")}）')
        if str(assoc.get('ruleId') or '') not in rule_ids:
            errors.append(f'对象关联的业务规则不存在（{assoc.get("ruleId")}）')
    for assoc in workflow.get('actionAssociations') or []:
        if not isinstance(assoc, dict):
            continue
        if _full(assoc.get('objectTypeId')) not in classes:
            errors.append(f'动作关联引用的对象类型不存在（{assoc.get("objectTypeId")}）')
        if str(assoc.get('actionId') or '') not in action_ids:
            errors.append(f'对象关联的动作不存在（{assoc.get("actionId")}）')

    # 4) 指标与旧 rules 的引用（历史兼容数据同样检查）
    rules_ids = set()
    metrics = (state or {}).get('metrics') or {}
    for metric in metrics.get('metrics') or []:
        if not isinstance(metric, dict):
            continue
        label = metric.get('name') or metric.get('id') or '未命名指标'
        if metric.get('rule_ref') and str(metric['rule_ref']) not in rules_ids and metric.get('rule_ref') not in rule_ids:
            errors.append(f'指标「{label}」引用的规则不存在（{metric["rule_ref"]}）')
        for t in metric.get('applicable_types') or []:
            if _full(t) not in classes:
                errors.append(f'指标「{label}」引用的对象类型不存在（{t}）')
    legacy_rules = (state or {}).get('rules') or {}
    for rule in legacy_rules.get('rules') or []:
        if not isinstance(rule, dict):
            continue
        label = rule.get('name') or rule.get('id') or '未命名规则'
        if rule.get('member_type') and _full(rule['member_type']) not in classes:
            errors.append(f'规则「{label}」的成员类型不存在（{rule["member_type"]}）')
    return errors


def new_broken_references(previous, current):
    """本次保存**新引入**的悬空引用：current 有、previous 没有的错误。

    previous 为 None（首次保存）时不阻断——那时没有「此前有效」的基线可比。
    返回 (新增错误列表, 是否可保存)。
    """
    if previous is None:
        return [], True
    before = set(broken_references(previous))
    newly = [e for e in broken_references(current) if e not in before]
    return newly, not newly
