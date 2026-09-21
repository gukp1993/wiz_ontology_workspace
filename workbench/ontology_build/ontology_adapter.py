"""候选 → 工作台本体协议：稳定 ID 分配、definitionOrder 与编辑态装配。

边界（开发计划 §3 / 执行指令 §4-P6）：
* 只生成**新本体草稿**：不追加已有本体、不发布、不生成项目映射/连接/编排。
* 所有定义 ID 由本模块分配（系统生成），模型只给批内临时键；引用按临时键映射成稳定 ID。
* 输出的是**编辑器态 ontology（JSON-LD @graph）**，与前端 modelFormat.ts 的
  decode/encode 形态一致；保存时由现有 workspaces._payload_of 走 encode_state
  转成 schema 快照，因此这里绝不能直接产出 schema 形态（否则草稿打不开）。
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


# 本体 JSON-LD 节点类型（与 model_format.GROUPS 一致）
NODE_TYPES = {'object': 'owl:Class', 'property': 'owl:DatatypeProperty',
              'link': 'owl:ObjectProperty', 'rule': 'mg:BusinessRule', 'action': 'mg:Action'}

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
    """候选 → JSON-LD 图节点（不含对象/端点引用，由 assemble 补齐）。"""
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
    elif ctype == 'rule':
        content = str(fields.get('content') or '').strip()
        if content:
            node['mg:constraint'] = {'@type': '@json', '@value': {'content': content}}
    elif ctype == 'action':
        effect = str(fields.get('effect') or '').strip()
        if effect:
            node['mg:business'] = {'@type': '@json', '@value': {'effect': effect}}
    else:
        # 对象：有依据的别名才写入（没依据不编造）
        aliases = fields.get('aliases')
        if isinstance(aliases, list) and aliases:
            node['mg:aliases'] = [str(a) for a in aliases[:20]]
    return node


COLLECTIONS = ('objectTypes', 'properties', 'linkTypes', 'businessRules', 'actions')
ID_PREFIX = {'object': 'obj_', 'property': 'prop_', 'link': 'link_', 'rule': 'rule_', 'action': 'act_'}


def assemble(candidates):
    """选定候选集合 → 编辑器态 ontology（JSON-LD）与 ID 映射。

    返回 (ontology, id_map, warnings)。任一引用解析不到即抛 AdapterError
    （在交付事务里回滚，绝不落半成品）。
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
    for candidate in candidates:
        node = build_node(candidate, assigned[candidate['id']], warnings)
        ctype = candidate['type']
        if ctype == 'property':
            owner = _resolve(candidate.get('ownerKey'), by_key, by_id)
            if owner is None or owner['type'] != 'object':
                raise AdapterError('属性「%s」所属对象无法解析（未选入或不存在）' % candidate['name'])
            node['rdfs:domain'] = {'@id': assigned.get(owner['id']) or ''}
            if not node['rdfs:domain']['@id']:
                raise AdapterError('属性「%s」所属对象未分配定义 ID' % candidate['name'])
        elif ctype == 'link':
            fields = candidate.get('fields') or {}
            source = _resolve(fields.get('sourceRef'), by_key, by_id)
            target = _resolve(fields.get('targetRef'), by_key, by_id)
            if source is None or target is None:
                raise AdapterError('链接「%s」两端对象无法解析（缺端点或未选入）' % candidate['name'])
            if source['type'] != 'object' or target['type'] != 'object':
                raise AdapterError('链接「%s」的端点必须是对象定义' % candidate['name'])
            node['rdfs:domain'] = {'@id': assigned.get(source['id']) or ''}
            node['rdfs:range'] = {'@id': assigned.get(target['id']) or ''}
            if not node['rdfs:domain']['@id'] or not node['rdfs:range']['@id']:
                raise AdapterError('链接「%s」端点未分配定义 ID' % candidate['name'])
        elif ctype in ('rule', 'action'):
            owner = _resolve(candidate.get('ownerKey'), by_key, by_id)
            if owner is not None and owner['type'] == 'object' and assigned.get(owner['id']):
                node['rdfs:domain'] = {'@id': assigned[owner['id']]}
        graph.append(node)
        order.append(node['@id'])

    ontology = {'@context': copy.deepcopy(_DEFAULT_NAMESPACES), '@graph': graph,
                'definitionOrder': order}
    return ontology, id_map, warnings


def _resolve(reference, by_key, by_id):
    """引用解析：接受候选临时键或候选 ID（两种都允许，先键后 ID）。"""
    token = str(reference or '')
    if not token:
        return None
    return by_key.get(token) or by_id.get(token)


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


def verify_structure(ontology):
    """结构自检（交付前最后一道确定性校验）：definitionOrder 与图一致、引用都在图内。

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
    return issues


def build_state(ontology, name):
    """编辑器态完整 state：复用现有工作台的空白态结构，只替换 ontology。

    直接借用 model_routes.blank_state（唯一口径），保证与「新建空白本体」产出的
    草稿完全同构——否则现有对象建模页打不开生成的草稿。
    """
    from workbench import model_routes
    state = model_routes.blank_state(name)
    state['ontology'] = ontology
    return state


def name_key(name):
    return str(name or '').strip().casefold()

