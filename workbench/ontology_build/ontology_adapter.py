"""候选 → 工作台本体协议：稳定 ID 分配、definitionOrder 与编辑态装配。

边界（开发计划 §3 / 执行指令 §4-P6）：
* 只生成**新本体草稿**：不追加已有本体、不发布、不生成项目映射/连接/编排。
* 所有定义 ID 由本模块分配（系统生成），模型只给批内临时键；引用按临时键映射成稳定 ID。
* 输出的是**编辑器态 ontology（JSON-LD @graph）**，与前端 modelFormat.ts 的
  decode/encode 形态一致；保存时由现有 workspaces._payload_of 走 encode_state
  转成 schema 快照，因此这里绝不能直接产出 schema 形态（否则草稿打不开）。
* 规则/动作**不进本体图**（D06）：现行协议（workbench/workflow.py 与
  frontend/src/ontology/businessRuleModel.ts / actionModel.ts 镜像）把两者装配到
  state.workflow —— 规则 → workflow.businessRules（+ businessRuleAssociations），
  动作 → workflow.actions（definitionVersion 2 简化动作，+ actionAssociations）。
  本体图里没有 mg:BusinessRule/mg:Action 节点类型，metadata 组也不放它们。
* definitionOrder 与各定义集合严格一致；证据/候选/人工决定留在任务侧，不进本体。
"""
import copy
import uuid

from workbench.ontology_build import protocol
from workbench.storage import engine as sto


class AdapterError(ValueError):
    """协议适配失败（字段非法/引用无法解析/结构不完整）。"""


def _new_id(prefix):
    return prefix + uuid.uuid4().hex[:12]


# 本体 JSON-LD 节点类型（与 model_format.GROUPS 一致）。规则/动作不在其中（D06）：
# 它们按现行协议进 workflow 业务定义区，见 assemble 的 workflow 装配。
NODE_TYPES = {'object': 'owl:Class', 'property': 'owl:DatatypeProperty',
              'link': 'owl:ObjectProperty'}

# xsd 标量映射：数据契约里的数据类型 → 图节点 rdfs:range（值已含唯一前缀，禁止再拼接）
_XSD = {'text': 'xsd:string', 'number': 'xsd:double', 'boolean': 'xsd:boolean',
        'dateTime': 'xsd:dateTime', 'array': 'xsd:string', 'struct': 'xsd:string'}
# 时间序列观测值类型 → rdfs:range（D02）：与 model_format.py 的
# `node['rdfs:range'] = {'@id': 'xsd:' + dtype['valueType']}` 同一口径，
# 单一前缀；取值集合必须等于 protocol.VALUE_TYPES（= SERIES_VALUE_TYPES）。
_SERIES_XSD = {'string': 'xsd:string', 'double': 'xsd:double', 'decimal': 'xsd:decimal',
               'integer': 'xsd:integer', 'boolean': 'xsd:boolean', 'date': 'xsd:date',
               'dateTime': 'xsd:dateTime'}


def field_value(fields, key, default=None):
    value = (fields or {}).get(key)
    return default if value is None else value


def build_data_type(fields):
    """候选 fields → (dataType 描述, 图节点 range 与标记, warnings)。

    普通类型：`{type: <枚举>}`；时间序列：`{type:'timeSeries', valueType:<标量>}`
    （与 20260915 时间序列数据类型调整一致）。观测值类型缺失或不在
    protocol.VALUE_TYPES（= model_format.SERIES_VALUE_TYPES）内一律抛
    AdapterError——预检与交付阻断已按同一枚举确定性拒绝（D02/D09），
    这里绝不静默降级成文本（静默改写人工确认过的类型）。
    """
    warnings = []
    raw = str((fields or {}).get('dataType') or 'text')
    if raw not in protocol.PROPERTY_DATA_TYPES:
        raise AdapterError('不支持的数据类型：%s' % raw)
    if raw == 'timeSeries':
        value_type = str((fields or {}).get('valueType') or '')
        if value_type not in _SERIES_XSD:
            raise AdapterError('时间序列属性缺少有效的观测值类型（可选：%s）'
                               % '/'.join(protocol.VALUE_TYPES))
        return ({'type': 'timeSeries', 'valueType': value_type},
                {'@id': _SERIES_XSD[value_type]}, 'timeSeries', warnings)
    return ({'type': raw}, {'@id': _XSD.get(raw, 'xsd:string')}, 'scalar', warnings)


def build_node(candidate, node_id, warnings):
    """图类型候选（object/property/link）→ JSON-LD 图节点（端点引用由 assemble 补齐）。

    规则/动作候选不走这里（D06），由 assemble 装配进 workflow。
    """
    ctype = candidate['type']
    fields = candidate.get('fields') or {}
    node = {'@id': node_id, '@type': NODE_TYPES[ctype],
            'rdfs:label': candidate['name'],
            'rdfs:comment': candidate['definition']}
    if ctype == 'property':
        data_type, range_node, shape, type_warnings = build_data_type(fields)
        warnings += type_warnings
        node['rdfs:range'] = range_node
        node['dataType'] = data_type
        if shape == 'timeSeries':
            node['mg:valueShape'] = 'timeSeries'  # 旧编辑器适配层内部标记（与 decode_ontology 一致）
    elif ctype == 'link':
        cardinality = fields.get('cardinality') or {}
        source_card = str(cardinality.get('source') or '')
        target_card = str(cardinality.get('target') or '')
        if source_card not in ('one', 'many') or target_card not in ('one', 'many'):
            raise AdapterError('链接「%s」缺少有效的两端数量关系（不能默认多对一）' % candidate['name'])
        node['mg:cardinality'] = {'@type': '@json', '@value': {'source': source_card, 'target': target_card}}
    else:
        # 对象：有依据的别名才写入（没依据不编造）
        aliases = fields.get('aliases')
        if isinstance(aliases, list) and aliases:
            node['mg:aliases'] = [str(a) for a in aliases[:20]]
    return node


ID_PREFIX = {'object': 'obj_', 'property': 'prop_', 'link': 'link_', 'rule': 'rule_', 'action': 'act_'}
# 简化动作（20260917 需求）：definitionVersion 2 只填名称/业务定义/预期效果；
# status 是 workflow.definition_errors 必填枚举，新导入定义与 Excel 导入器同口径。
ACTION_DEFINITION_VERSION = 2
ACTION_NEW_STATUS = 'experimental'


def assemble(candidates, aliases=None):
    """选定候选集合 → (编辑器态 ontology JSON-LD, workflow 装配, ID 映射, warnings)。

    规则/动作（D06）不进 @graph：按现行协议装配为
    * `workflow.businessRules`：`{id, name, description, content?}`（content 选填，
      口径见 20260920《规则动作字段精简》需求 §2）；
    * `workflow.actions`：`{id, name, description, effect?, definitionVersion:2, status}`
      （effect 选填；简化动作不再内嵌 object_type，对象关联只走关联集合）；
    * 对象关联：`workflow.businessRuleAssociations` / `workflow.actionAssociations`，
      `{objectTypeId, ruleId|actionId}`，objectTypeId 用宿主对象在本图分配的
      稳定 @id（与 definitionOrder 同源，不发明新引用格式）。
    任一引用解析不到即抛 AdapterError（在交付事务里回滚，绝不落半成品）。

    aliases（选传，默认 None = 无别名，完全保持旧行为）：合并别名表
    `{被合并候选的 key 或候选 ID: 最终保留项候选 ID}`。评审合并后候选从选定集合
    消失，其它候选仍按旧 key/ID 引用它；调用方（delivery）按本批次全部候选
    （含已合并）构造本表传入，装配即把旧引用规范化到保留项（R3-03）。
    解析顺序严格为 key → 候选 ID → 别名；别名指向的保留项必须出现在本次
    candidates 里，否则仍按「无法解析」处理（抛 AdapterError），绝不静默丢弃。
    """
    by_key, by_id = {}, {}
    for candidate in candidates:
        by_key.setdefault(str(candidate.get('key') or ''), candidate)
        by_id[candidate['id']] = candidate
    assigned, warnings = {}, []

    def stable(candidate):
        if candidate['id'] not in assigned:
            assigned[candidate['id']] = _new_id(ID_PREFIX[candidate['type']])
        return assigned[candidate['id']]

    id_map = {c['id']: stable(c) for c in candidates if c['type'] != 'property'}
    for c in candidates:
        if c['type'] == 'property':
            id_map[c['id']] = stable(c)

    graph, order = [], []
    workflow = {'businessRules': [], 'businessRuleAssociations': [],
                'actions': [], 'actionAssociations': []}
    for candidate in candidates:
        ctype = candidate['type']
        if ctype in ('rule', 'action'):
            _assemble_business(candidate, assigned, by_key, by_id, workflow, aliases)
            continue
        node = build_node(candidate, assigned[candidate['id']], warnings)
        if ctype == 'property':
            owner = _require_object_ref(candidate.get('ownerKey'), by_key, by_id, aliases,
                                        '属性', candidate['name'], '所属对象')
            if owner is not None:
                node['rdfs:domain'] = {'@id': assigned.get(owner['id']) or ''}
                if not node['rdfs:domain']['@id']:
                    raise AdapterError('属性「%s」所属对象未分配定义 ID' % candidate['name'])
        elif ctype == 'link':
            fields = candidate.get('fields') or {}
            source = _require_object_ref(fields.get('sourceRef'), by_key, by_id, aliases,
                                         '链接', candidate['name'], '源端对象',
                                         allow_empty=False)
            target = _require_object_ref(fields.get('targetRef'), by_key, by_id, aliases,
                                         '链接', candidate['name'], '目标端对象',
                                         allow_empty=False)
            node['rdfs:domain'] = {'@id': assigned.get(source['id']) or ''}
            node['rdfs:range'] = {'@id': assigned.get(target['id']) or ''}
            if not node['rdfs:domain']['@id'] or not node['rdfs:range']['@id']:
                raise AdapterError('链接「%s」端点对象未分配定义 ID' % candidate['name'])
        graph.append(node)
        order.append(node['@id'])

    ontology = {'@context': copy.deepcopy(_DEFAULT_NAMESPACES), '@graph': graph,
                'definitionOrder': order}
    return ontology, workflow, id_map, warnings


def _assemble_business(candidate, assigned, by_key, by_id, workflow, aliases=None):
    """规则/动作候选 → workflow 业务定义与对象关联（D06 的正确落位）。

    R3-03：宿主引用解析不到时绝不静默丢弃关联——**非空** ownerKey 抛 AdapterError
    （在交付事务里回滚），空字符串仍表示「未声明宿主」（不挂关联、不阻断）。
    """
    ctype = candidate['type']
    label = protocol.TYPE_LABELS.get(ctype, ctype)
    fields = candidate.get('fields') or {}
    node_id = assigned[candidate['id']]
    record = {'id': node_id, 'name': candidate['name'], 'description': candidate['definition']}
    owner = _require_object_ref(candidate.get('ownerKey'), by_key, by_id, aliases,
                                label, candidate['name'], '宿主对象')
    owner_id = None
    if owner is not None:
        owner_id = assigned.get(owner['id']) or None
        if not owner_id:
            raise AdapterError('%s「%s」的宿主对象未分配定义 ID' % (label, candidate['name']))
    if ctype == 'rule':
        content = str(fields.get('content') or '').strip()
        if content:
            record['content'] = content   # 选填：不生成 output，不发明字段
        workflow['businessRules'].append(record)
        if owner_id:
            workflow['businessRuleAssociations'].append({'objectTypeId': owner_id, 'ruleId': node_id})
    else:
        effect = str(fields.get('effect') or '').strip()
        if effect:
            record['effect'] = effect     # 选填（预期效果）
        record['definitionVersion'] = ACTION_DEFINITION_VERSION
        record['status'] = ACTION_NEW_STATUS
        workflow['actions'].append(record)
        if owner_id:
            workflow['actionAssociations'].append({'objectTypeId': owner_id, 'actionId': node_id})


def _require_object_ref(reference, by_key, by_id, aliases, label, name, role,
                        allow_empty=True):
    """对象引用解析：**非空**解析不到即抛 AdapterError（R3-03，绝不静默丢弃关联）。

    空引用（''/None）：allow_empty=True（宿主类引用，如属性 ownerKey 与规则/动作
    ownerKey）= 未声明宿主，返回 None，由调用方决定不挂关联（属性缺 domain 仍由
    verify_structure 与交付预检报出）；allow_empty=False（链接两端）沿用既有口径
    直接抛错——链接没有端点就不是合法定义，绝不生成半成品节点。
    """
    token = str(reference or '').strip()
    if not token:
        if allow_empty:
            return None
        raise AdapterError('链接「%s」缺少%s（链接必须同时给出源端与目标端对象）'
                           % (name, role))
    node = _resolve(token, by_key, by_id, aliases)
    if node is None or node['type'] != 'object':
        raise AdapterError('%s「%s」的%s「%s」无法解析（未选入、已合并或不存在）；'
                           '请纳入该对象、清除该引用，或确认合并保留项'
                           % (label, name, role, token))
    return node


def _resolve(reference, by_key, by_id, aliases=None):
    """引用解析：候选临时键 → 候选稳定 ID → 合并别名表（R3-03）。

    别名表（aliases = {被合并候选的 key 或 ID: 最终保留项候选 ID}）只在键与 ID
    都未命中时生效：合并后候选被移出选定集合，其它候选仍按旧 key/ID 引用它，
    这里把它规范化到保留项。别名指向的保留项不在本次集合中（by_id 取不到）
    时返回 None，由调用点抛 AdapterError，绝不静默降级。
    """
    token = str(reference or '')
    if not token:
        return None
    node = by_key.get(token) or by_id.get(token)
    if node is not None or not aliases:
        return node
    current = str(aliases.get(token) or '')
    seen = {token}
    for _ in range(8):          # 别名表已传递解析；这里只做有界兜底（环保护）
        if not current or current in seen:
            return None
        node = by_id.get(current)
        if node is not None:
            return node
        seen.add(current)
        current = str(aliases.get(current) or '')
    return None


# 与 model_routes.DEFAULT_NAMESPACES 保持一致（新本体沿用工作台默认命名空间）
_DEFAULT_NAMESPACES = {
    'rdf': 'http://www.w3.org/1999/02/22-rdf-syntax-ns#',
    'rdfs': 'http://www.w3.org/2000/01/rdf-schema#',
    'xsd': 'http://www.w3.org/2001/XMLSchema#',
    'owl': 'http://www.w3.org/2002/07/owl#',
    'mg': 'https://ontology.local/mg#',
}


def selection_digest(selected_ids):
    """选定集合指纹：用于 checkToken 绑定（顺序稳定，集合变化即失效）。"""
    return sto.content_hash(sto.json_dumps(sorted(str(i) for i in selected_ids)).encode('utf-8'))


def make_check_token(batch_id, selection_ids, scope_revision, material_revision):
    """预检令牌：绑定批次、选定集合、范围与材料修订；任一变化即失效。"""
    raw = '|'.join([str(batch_id or ''), selection_digest(selection_ids),
                    str(int(scope_revision or 0)), str(int(material_revision or 0))])
    return sto.content_hash(raw.encode('utf-8'))[:32]


def payload_digest(ontology):
    """交付幂等指纹：只含本体内容（definitionOrder + 图），不含 revision 与时间。"""
    return sto.content_hash(sto.json_dumps(ontology).encode('utf-8'))


def verify_structure(ontology, workflow=None):
    """结构自检（交付前最后一道确定性校验）：definitionOrder 与图一致、引用都在图内。

    传入 workflow 装配（D06）时同步自检：规则/动作四键结构、名称与业务定义非空、
    关联的 objectTypeId/ruleId/actionId 都指向本次交付的定义。

    返回 issues 列表（空 = 通过）。服务端校验，不能只靠前端。
    """
    issues = []
    graph = ontology.get('@graph') or []
    ids = set()
    for node in graph:
        node_id = node.get('@id')
        if not node_id:
            issues.append('存在缺少 @id 的定义')
            continue
        if node_id in ids:
            issues.append('定义 ID 重复：%s' % node_id)
        ids.add(node_id)
        if not str(node.get('rdfs:label') or '').strip():
            issues.append('定义 %s 缺少名称' % node_id)
        if node.get('@type') not in NODE_TYPES.values():
            issues.append('定义 %s 使用了本体图不支持的节点类型 %s' % (node_id, node.get('@type')))
    order = ontology.get('definitionOrder') or []
    if set(order) != ids:
        issues.append('definitionOrder 与定义集合不是同一组 ID')
    elif len(order) != len(ids):
        issues.append('definitionOrder 数量与定义集合不一致')
    for node in graph:
        kind = node.get('@type')
        if kind == 'owl:Class':
            continue
        domain = (node.get('rdfs:domain') or {}).get('@id')
        if domain and domain not in ids:
            issues.append('定义「%s」的起点对象不在本次定义集合内' % node.get('rdfs:label'))
        if kind == 'owl:ObjectProperty':
            if not domain:
                issues.append('链接「%s」缺少起点对象' % node.get('rdfs:label'))
            target = (node.get('rdfs:range') or {}).get('@id')
            if not target or target not in ids:
                issues.append('链接「%s」的端点对象不在本次定义集合内' % node.get('rdfs:label'))
        if kind == 'owl:DatatypeProperty':
            if not node.get('rdfs:domain', {}).get('@id'):
                issues.append('属性「%s」缺少所属对象' % node.get('rdfs:label'))
            data_type = node.get('dataType') or {}
            if data_type.get('type') not in protocol.PROPERTY_DATA_TYPES:
                issues.append('属性「%s」的数据类型不合法' % node.get('rdfs:label'))
            if data_type.get('type') == 'timeSeries' and data_type.get('valueType') not in protocol.VALUE_TYPES:
                issues.append('时间序列属性「%s」缺少有效观测值类型' % node.get('rdfs:label'))
    if workflow is not None:
        issues.extend(_verify_workflow(workflow, ids))
    return issues


def _verify_workflow(workflow, object_ids):
    """workflow 装配自检（D06）：规则/动作记录与关联引用完整。"""
    issues = []
    rules = workflow.get('businessRules') or []
    actions = workflow.get('actions') or []
    rule_ids = set()
    for record in rules:
        label = str(record.get('name') or '').strip() or str(record.get('id') or '')
        if not str(record.get('name') or '').strip():
            issues.append('规则「%s」缺少名称' % label)
        if not str(record.get('description') or '').strip():
            issues.append('规则「%s」缺少业务定义' % label)
        if not record.get('id') or record['id'] in rule_ids:
            issues.append('规则「%s」的定义 ID 缺失或重复' % label)
        rule_ids.add(record.get('id'))
    action_ids = set()
    for record in actions:
        label = str(record.get('name') or '').strip() or str(record.get('id') or '')
        if not str(record.get('name') or '').strip():
            issues.append('动作「%s」缺少名称' % label)
        if not str(record.get('description') or '').strip():
            issues.append('动作「%s」缺少业务定义' % label)
        if not record.get('id') or record['id'] in action_ids:
            issues.append('动作「%s」的定义 ID 缺失或重复' % label)
        action_ids.add(record.get('id'))
    for row in workflow.get('businessRuleAssociations') or []:
        if row.get('objectTypeId') not in object_ids:
            issues.append('规则关联的对象不在本次定义集合内（%s）' % row.get('objectTypeId'))
        if row.get('ruleId') not in rule_ids:
            issues.append('规则关联指向不存在的规则（%s）' % row.get('ruleId'))
    for row in workflow.get('actionAssociations') or []:
        if row.get('objectTypeId') not in object_ids:
            issues.append('动作关联的对象不在本次定义集合内（%s）' % row.get('objectTypeId'))
        if row.get('actionId') not in action_ids:
            issues.append('动作关联指向不存在的动作（%s）' % row.get('actionId'))
    return issues


def build_state(ontology, name, workflow=None):
    """编辑器态完整 state：复用现有工作台的空白态结构，装配 ontology 与 workflow。

    直接借用 model_routes.blank_state（唯一口径），保证与「新建空白本体」产出的
    草稿完全同构——否则现有对象建模页打不开生成的草稿。
    workflow（D06）只写入有内容的键：规则进 businessRules（+关联），
    动作合并进现有 actions 列表（+关联）；空集合不写，保持草稿最小形态。
    """
    from workbench import model_routes
    state = model_routes.blank_state(name)
    state['ontology'] = ontology
    additions = workflow or {}
    target = state.setdefault('workflow', {})
    rules = additions.get('businessRules') or []
    if rules:
        target['businessRules'] = list(rules)
    rule_assoc = additions.get('businessRuleAssociations') or []
    if rule_assoc:
        target['businessRuleAssociations'] = list(rule_assoc)
    actions = additions.get('actions') or []
    if actions:
        target['actions'] = list(target.get('actions') or []) + list(actions)
    action_assoc = additions.get('actionAssociations') or []
    if action_assoc:
        target['actionAssociations'] = list(action_assoc)
    return state


def name_key(name):
    return str(name or '').strip().casefold()

