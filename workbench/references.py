"""草稿引用完整性：稳定 ID 引用的目标必须存在（20260920 需求 11～13 保存边界）。

与 validate（必填/命名/结构类校验）的分工：
* validate.errors 是**填写提示**，不阻断保存（草稿允许带错保存）；
* 本模块只回答一个问题：**当前草稿里有没有指向不存在稳定 ID 的引用**（悬空引用）。
  保存边界据此比较「本次保存是否把此前有效的引用改坏」，只阻断**新引入**的悬空引用，
  历史遗留的失效引用允许继续保存（需求 §6：不能把草稿永久锁死，历史版本引用不失效）。

每条结果 = {'key': 稳定比较键, 'message': 展示文案}：
* key 只由稳定 ID 组成（来源记录 id + 引用字段/槽位身份 + 目标 id），**改显示名不变、改目标必变**。
  保存边界按 key 比较：既有失效引用改名称不算新失效；同名同文案但身份不同的新失效不会被去重放过。
* message 供前端展示，业务名称只出现在这里。

检查范围（接口文档 02 §2.2 已登记；与 frontend/src/ontology/editorModel.ts 的
graphReferenceEntries 对齐，前端会拦的删除这里同样会拦）：
1. 图内引用：rdfs:domain / rdfs:range、mg:sharedProperty、mg:valueType、
   取值函数（mg:valueSource.functionId）、嵌套值类型（mg:constraint 的 elementValueType / fields[].valueType）；
   domain/range 与前端 matches 同口径，同时接受 mg: 前缀与裸名（B2 修正：裸名 o1 按 mg:o1 核对）；
2. 计算/动作/接口三类定义的签名槽位 inputs/outputs 的 ref:{kind:object|property|base}
   （base 只在带稳定 id 时按图内引用核对，正常 base 引用只有 dataType）；
   槽位无稳定 id 时用「同槽位内同一目标引用的出现序号」做槽位身份，不用所在序号（B3 修正）；
3. 计算/动作/接口三类定义的字段级引用：适用对象类型（object_type / object_types / applicable_objects）、
   输出属性、链接/计算/接口引用、顶层取值函数调用、步骤调用、属性绑定、参数；以及 properties
   （必需共享属性）/ implementations（实现对象类型）清单（前端对三类定义同样遍历，实际数据主要在接口定义上）；
4. 动作/规则关联的 objectTypeId / ruleId / actionId；
5. 指标（metrics）的 rule_ref / applicable_types；旧 rules 的成员类型与成员关系。

前端另有两块不在本模块范围，已在接口文档 02 §2.2 注明：
* 「接口实现完整性」保护（删除实现对象上最后一条满足接口要求的共享属性实例，
  editorModel.graphReferenceEntries 的「实现对象必需属性」分支）：那不是悬空引用（接口引用的共享属性仍在），
  删除后由发布校验提示补齐；
* 项目映射 `state.bindings` 的引用（同一遍历中 `mapped` / `object_bindings` 分支）：属项目区状态，走 `/api/project-save`
  与项目校验，不经过本体保存边界。
"""

KIND_CLASS = 'owl:Class'
KIND_OBJECT_PROP = 'owl:ObjectProperty'
KIND_DATATYPE_PROP = 'owl:DatatypeProperty'
KIND_SHARED_PROP = 'mg:SharedProperty'
KIND_VALUE_TYPE = 'mg:ValueType'

# 三类可编辑业务定义（与前端 editorModel.graphReferenceEntries 的遍历一致）
WORKFLOW_KINDS = (('functions', '计算契约'), ('actions', '动作定义'), ('interfaces', '接口定义'))

# 历史 inputs 里的 value_type / value_type_ref 语义不明确（当前前端无写入方）：
# 可能是值类型稳定 id，也可能是标量类型名；这些名字按「不是引用」跳过，避免误报。
PRIMITIVE_NAMES = {'string', 'double', 'decimal', 'integer', 'boolean', 'date', 'dateTime',
                   'text', 'number', 'datetime', 'timestamp', 'object', 'list', 'array', 'struct'}


def _full(type_id):
    s = str(type_id or '').strip()
    return s if (not s or s.startswith('mg:')) else 'mg:' + s


def _canon(target, known):
    """目标 id 的规范引用身份：精确命中优先，其次补 mg: 前缀命中，否则原样（不臆造新身份）。"""
    value = str(target or '').strip()
    if value in known:
        return value
    candidate = _full(value)
    return candidate if candidate in known else value


def _canon_graph_ref(target, known):
    """domain/range 目标的图内引用核对（B2 修正）：返回 (引用 id, 是否按图内引用处理)。

    与前端 editorModel.graphReferenceEntries 的 matches 同口径——稳定 id 精确命中优先，
    其次补 mg: 前缀命中；仍不命中且是不含冒号的裸名时，按 mg: 补齐后的身份核对（前端对
    「稳定 id 与去前缀短名」同判，删除目标对象时同样命中该引用，后端不能放行）；
    其他带前缀写法（xsd:/http: 等）保持原样跳过。
    节点 id 本身是裸名（历史数据）时精确命中，不臆造 mg: 前缀，避免误报。
    """
    value = str(target or '').strip()
    if not value:
        return '', False
    canonical = _canon(value, known)
    if canonical in known:
        return canonical, True
    if ':' not in value:
        return _full(value), True
    return value, value.startswith('mg:')


def _ref_id(node, key):
    value = (node or {}).get(key)
    if isinstance(value, dict):
        return str(value.get('@id') or '')
    return str(value or '')


def _json_value(node, key):
    """mg:* 的 @value JSON 字段容错读取。"""
    value = (node or {}).get(key)
    if isinstance(value, dict) and isinstance(value.get('@value'), dict):
        return value['@value']
    return value if isinstance(value, dict) else {}


def _records(rows):
    return [r for r in (rows or []) if isinstance(r, dict)]


def _list(value):
    """只认数组字段：历史数据里同名字段若被写成对象，按「没有清单」处理，避免误报。"""
    return value if isinstance(value, list) else []


def _group(state, key, inner):
    """state[key][inner] 的容错读取（缺失/非对象一律按空列表）。"""
    block = state.get(key) if isinstance(state, dict) else None
    if not isinstance(block, dict):
        return []
    return _records(block.get(inner))


def _entry(key, message):
    return {'key': key, 'message': message}


def _unique(entries):
    """同一引用（同 key）只保留一条。"""
    out, seen = [], set()
    for item in entries:
        if item['key'] in seen:
            continue
        seen.add(item['key'])
        out.append(item)
    return out


def broken_references(state):
    """当前草稿的悬空引用清单，每项含稳定比较键与展示文案。"""
    errors = []
    state = state or {}
    ontology = state.get('ontology') or {}
    graph = ontology.get('@graph') if isinstance(ontology.get('@graph'), list) else []
    by_id = {n.get('@id'): n for n in graph if isinstance(n, dict) and n.get('@id')}
    classes = {n['@id'] for n in by_id.values() if n.get('@type') == KIND_CLASS}
    properties = {n['@id'] for n in by_id.values() if n.get('@type') in (KIND_SHARED_PROP, KIND_DATATYPE_PROP)}
    relations = {n['@id'] for n in by_id.values() if n.get('@type') == KIND_OBJECT_PROP}
    value_types = {n['@id'] for n in by_id.values() if n.get('@type') == KIND_VALUE_TYPE}

    workflow = state.get('workflow') if isinstance(state.get('workflow'), dict) else {}
    functions = _records(workflow.get('functions'))
    actions = _records(workflow.get('actions'))
    interfaces = _records(workflow.get('interfaces'))
    function_ids = {str(r['id']) for r in functions if r.get('id')}
    action_ids = {str(r['id']) for r in actions if r.get('id')}
    interface_ids = {str(r['id']) for r in interfaces if r.get('id')}
    rule_ids = {str(r['id']) for r in _records(workflow.get('businessRules')) if r.get('id')}
    # 旧 rules 集合（历史兼容数据）的稳定 id 集：指标 rule_ref 判定必须包含它，否则有效旧引用被误报
    legacy_rules = _group(state, 'rules', 'rules')
    legacy_rule_ids = {str(r['id']) for r in legacy_rules if r.get('id')}

    def name_of(node, fallback=''):
        return str((node or {}).get('rdfs:label') or fallback or (node or {}).get('@id') or '')

    def field_ref(src, slot, target, known, message):
        """字段级引用检查：目标不在 known（含 mg: 前缀容错）即悬空。"""
        value = str(target or '').strip()
        if not value or value in known or _full(value) in known:
            return
        errors.append(_entry(f'{src}:{slot}:{_full(value)}', f'{message}（{target}）'))

    # 1) 图内 domain / range / 共享定义 / 值类型 / 取值函数 / 嵌套值类型
    for node in by_id.values():
        label = name_of(node)
        source_id = node['@id']
        domain_raw = _ref_id(node, 'rdfs:domain')
        domain, domain_is_ref = _canon_graph_ref(domain_raw, classes)
        if domain_is_ref and domain not in classes:
            errors.append(_entry(f'graph:{source_id}:rdfs:domain:{domain}',
                                 f'「{label}」所属的对象类型不存在（{domain_raw}）'))
        # 链接的 range 指向对象类型；数据属性的 range 允许指向值类型（两边都算存在）
        known_range = classes if node.get('@type') == KIND_OBJECT_PROP else classes | value_types
        range_raw = _ref_id(node, 'rdfs:range')
        range_ref, range_is_ref = _canon_graph_ref(range_raw, known_range)
        if range_is_ref and range_ref not in known_range:
            reason = '链接终点对象类型' if node.get('@type') == KIND_OBJECT_PROP else '数据类型'
            errors.append(_entry(f'graph:{source_id}:rdfs:range:{range_ref}',
                                 f'「{label}」的{reason}不存在（{range_raw}）'))
        shared = _ref_id(node, 'mg:sharedProperty')
        if shared and shared not in properties:
            errors.append(_entry(f'graph:{source_id}:mg:sharedProperty:{shared}',
                                 f'属性「{label}」引用的共享定义不存在（{shared}）'))
        vt = _ref_id(node, 'mg:valueType')
        if vt.startswith('mg:') and vt not in value_types:
            errors.append(_entry(f'graph:{source_id}:mg:valueType:{vt}',
                                 f'属性「{label}」引用的值类型不存在（{vt}）'))
        source = _json_value(node, 'mg:valueSource')
        function_ref = str(source.get('functionId') or '')
        if source.get('kind') == 'function' and function_ref and function_ref not in function_ids:
            errors.append(_entry(f'graph:{source_id}:mg:valueSource.functionId:{_full(function_ref)}',
                                 f'属性「{label}」引用的取值函数不存在（{function_ref}）'))
        constraint = _json_value(node, 'mg:constraint')
        element = str(constraint.get('elementValueType') or '')
        if element and _canon(element, value_types) not in value_types:
            errors.append(_entry(f'graph:{source_id}:mg:constraint.elementValueType:{_full(element)}',
                                 f'「{label}」数组元素引用的值类型不存在（{element}）'))
        fields_seen = {}
        for index, field in enumerate(constraint.get('fields') or []):
            if not isinstance(field, dict):
                continue
            type_ref = str(field.get('valueType') or '')
            if type_ref and _canon(type_ref, value_types) not in value_types:
                # 槽位身份用字段名（值类型约束要求字段名非空且唯一）；没有名称时才退回标识，
                # 且用「同一目标引用的出现序号」而非所在序号（B3 同口径：其他字段增删不改写比较键）。
                slot = str(field.get('name') or '')
                if not slot:
                    marker = f'valueType:{_full(type_ref)}'
                    ordinal = fields_seen.get(marker, 0)
                    fields_seen[marker] = ordinal + 1
                    slot = f'{marker}#{ordinal}'
                errors.append(_entry(f'graph:{source_id}:mg:constraint.fields:{slot}:valueType:{_full(type_ref)}',
                                     f'「{label}」结构体字段「{field.get("name") or index}」引用的值类型不存在（{type_ref}）'))

    # 2) 三类定义（计算/动作/接口）的签名槽位 inputs/outputs（V3 ref:{kind,id}），
    #    与前端 editorModel.graphReferenceEntries 的签名槽位遍历一致。
    for kind, kind_label in WORKFLOW_KINDS:
        for index, record in enumerate(workflow.get(kind) or []):
            if not isinstance(record, dict):
                continue
            record_id = str(record.get('id') or f'#{index}')
            label = record.get('name') or record.get('id') or f'未命名{kind_label}'
            for slot, slot_label in (('inputs', '输入'), ('outputs', '输出')):
                # B3（20260920 W3 最小修复）：没有稳定 id 的槽位不再用「所在序号」做槽位身份，
                # 改用「同槽位内同一目标引用的出现序号」——删除/重排其他槽位不会改写既有失效
                # 引用的比较键（不再把历史遗留失效误报为新引入），而新增同目标槽位仍产生新键，
                # 不放松「新增悬空引用阻断保存」的保护。
                same_target_seen = {}
                for item_index, item in enumerate(record.get(slot) or []):
                    if not isinstance(item, dict):
                        continue
                    ref = item.get('ref') if isinstance(item.get('ref'), dict) else {}
                    ref_kind = str(ref.get('kind') or '')
                    target = str(ref.get('id') or '')
                    if not target:
                        continue
                    slot_id = str(item.get('id') or '')
                    if not slot_id:
                        marker = f'{ref_kind}:{_full(target)}'
                        ordinal = same_target_seen.get(marker, 0)
                        same_target_seen[marker] = ordinal + 1
                        slot_id = f'{marker}#{ordinal}'
                    item_label = str(item.get('name') or '') or f'#{item_index + 1}'
                    src_slot = f'{kind}:{record_id}:{slot}:{slot_id}'
                    if ref_kind == 'object' and _canon(target, classes) not in classes:
                        errors.append(_entry(f'{src_slot}:object:{_full(target)}',
                                             f'{kind_label}「{label}」的{slot_label}「{item_label}」引用不存在的对象类型（{target}）'))
                    elif ref_kind == 'property' and _canon(target, properties) not in properties:
                        errors.append(_entry(f'{src_slot}:property:{_full(target)}',
                                             f'{kind_label}「{label}」的{slot_label}「{item_label}」引用不存在的属性（{target}）'))
                    elif ref_kind == 'base' and _canon(target, by_id) not in by_id:
                        # base 引用正常只有 dataType、没有 id；带 id 的按图内引用核对（前端同样忽略 kind 直接比对 id）
                        errors.append(_entry(f'{src_slot}:base:{_full(target)}',
                                             f'{kind_label}「{label}」的{slot_label}「{item_label}」引用不存在的内容（{target}）'))

    # 3) 三类业务定义的字段级引用（与前端 graphReferenceEntries 的字段清单一致）
    for kind, kind_label in WORKFLOW_KINDS:
        for index, record in enumerate(workflow.get(kind) or []):
            if not isinstance(record, dict):
                continue
            record_id = str(record.get('id') or f'#{index}')
            label = record.get('name') or record.get('id') or f'未命名{kind_label}'
            src = f'{kind}:{record_id}'
            prefix = f'{kind_label}「{label}」'
            # 适用对象类型：object_type（历史单一）/ object_types（多选）/ applicable_objects（V3 契约）
            field_ref(src, 'object_type', record.get('object_type'), classes, f'{prefix}的适用对象类型不存在')
            for slot in ('object_types', 'applicable_objects'):
                for target in record.get(slot) or []:
                    field_ref(src, slot, target, classes, f'{prefix}的适用对象类型不存在')
            field_ref(src, 'output_property', record.get('output_property'), properties, f'{prefix}的输出属性不存在')
            field_ref(src, 'relation_ref', record.get('relation_ref'), relations, f'{prefix}引用的链接类型不存在')
            field_ref(src, 'interface_ref', record.get('interface_ref'), interface_ids, f'{prefix}引用的接口不存在')
            # 顶层取值函数调用（历史 functions 记录写法，前端对三类定义同样遍历 function_ref）
            field_ref(src, 'function_ref', record.get('function_ref'), function_ids, f'{prefix}调用的计算契约不存在')
            # 必需共享属性 / 实现对象类型清单（前端对三类定义都遍历，实际主要在接口定义）
            for target in _list(record.get('properties')):
                value = str(target or '').strip()
                if value and _canon(value, by_id) not in by_id:
                    errors.append(_entry(f'{kind}:{record_id}:properties:{_full(value)}',
                                         f'{prefix}要求的共享属性不存在（{value}）'))
            for target in _list(record.get('implementations')):
                if _full(target) not in classes:
                    errors.append(_entry(f'{kind}:{record_id}:implementations:{_full(target)}',
                                         f'{prefix}引用的实现对象类型不存在（{target}）'))
            # 步骤 / 属性绑定 / 历史参数没有各自稳定 id：槽位身份取「引用目标」本身，
            # 记录重排不会改变比较键，也不会把既有失效误判成新引入。
            for step in record.get('steps') or []:
                if isinstance(step, dict):
                    field_ref(f'{src}:steps', 'function_ref', step.get('function_ref'), function_ids,
                              f'{prefix}的调用步骤引用的计算契约不存在')
            for binding in record.get('property_bindings') or []:
                if not isinstance(binding, dict):
                    continue
                field_ref(f'{src}:property_bindings', 'object_type', binding.get('object_type'), classes,
                          f'{prefix}的属性绑定所属对象类型不存在')
                field_ref(f'{src}:property_bindings', 'property_ref', binding.get('property_ref'), properties,
                          f'{prefix}的属性绑定引用的属性不存在')
            for item in record.get('inputs') or []:
                if not isinstance(item, dict):
                    continue
                slot_label = f'参数「{item.get("name") or "未命名"}」'
                field_ref(f'{src}:inputs', 'object_type', item.get('object_type'), classes, f'{prefix}的{slot_label}引用的对象类型不存在')
                field_ref(f'{src}:inputs', 'property_ref', item.get('property_ref'), properties, f'{prefix}的{slot_label}引用的属性不存在')
                field_ref(f'{src}:inputs', 'interface_ref', item.get('interface_ref'), interface_ids, f'{prefix}的{slot_label}引用的接口不存在')
                for key in ('value_type', 'value_type_ref'):
                    value = str(item.get(key) or '').strip()
                    if value and value not in PRIMITIVE_NAMES:
                        field_ref(f'{src}:inputs', key, value, value_types, f'{prefix}的{slot_label}引用的值类型不存在')

    # 4) 动作 / 规则关联
    for assoc in _records(workflow.get('businessRuleAssociations')):
        object_id, rule_id = assoc.get('objectTypeId'), assoc.get('ruleId')
        key = f'assoc:rule:{object_id}:{rule_id}'
        if _full(object_id) not in classes:
            errors.append(_entry(f'{key}:object_type', f'规则关联引用的对象类型不存在（{object_id}）'))
        if str(rule_id or '') not in rule_ids:
            errors.append(_entry(f'{key}:rule', f'对象关联的业务规则不存在（{rule_id}）'))
    for assoc in _records(workflow.get('actionAssociations')):
        object_id, action_id = assoc.get('objectTypeId'), assoc.get('actionId')
        key = f'assoc:action:{object_id}:{action_id}'
        if _full(object_id) not in classes:
            errors.append(_entry(f'{key}:object_type', f'动作关联引用的对象类型不存在（{object_id}）'))
        if str(action_id or '') not in action_ids:
            errors.append(_entry(f'{key}:action', f'对象关联的动作不存在（{action_id}）'))

    # 5) 指标与旧 rules 的引用（历史兼容数据同样检查）
    for index, metric in enumerate(_group(state, 'metrics', 'metrics')):
        metric_id = str(metric.get('id') or f'#{index}')
        label = metric.get('name') or metric.get('id') or '未命名指标'
        rule_ref = str(metric.get('rule_ref') or '')
        if rule_ref and rule_ref not in rule_ids and rule_ref not in legacy_rule_ids:
            errors.append(_entry(f'metric:{metric_id}:rule_ref:{rule_ref}',
                                 f'指标「{label}」引用的规则不存在（{rule_ref}）'))
        for target in metric.get('applicable_types') or []:
            if _full(target) not in classes:
                errors.append(_entry(f'metric:{metric_id}:applicable_types:{_full(target)}',
                                     f'指标「{label}」引用的对象类型不存在（{target}）'))
    for index, rule in enumerate(legacy_rules):
        rule_id = str(rule.get('id') or f'#{index}')
        label = rule.get('name') or rule.get('id') or '未命名规则'
        if rule.get('member_type') and _full(rule['member_type']) not in classes:
            errors.append(_entry(f'rule:{rule_id}:member_type:{_full(rule["member_type"])}',
                                 f'规则「{label}」的成员类型不存在（{rule["member_type"]}）'))
        for target in rule.get('membership_relations') or []:
            if str(target) not in relations:
                errors.append(_entry(f'rule:{rule_id}:membership_relations:{_full(target)}',
                                     f'规则「{label}」的成员关系不存在（{target}）'))
    return _unique(errors)


def new_broken_references(previous, current):
    """本次保存**新引入**的悬空引用：current 有、previous 没有（按稳定 key 比较）。

    previous 为 None（首次保存）时不阻断——那时没有「此前有效」的基线可比。
    返回 (新增引用的展示文案列表, 是否可保存)。
    """
    if previous is None:
        return [], True
    before = {item['key'] for item in broken_references(previous)}
    newly = [item['message'] for item in broken_references(current) if item['key'] not in before]
    return newly, not newly
