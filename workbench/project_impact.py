"""升级影响匹配（升级预检用纯函数，2026-09-20 v2 修正）。

背景：`projects.upgrade_check` 原先用 `prop in rid or rid in prop or bare(rid).endswith(prop)`
之类的子串/后缀匹配判断「本体定义变化影响哪些项目配置」，会误报（相似 apiName 相互命中）
与漏报（属性经 sharedPropertyId 继承共享定义时对不上），也不区分定义是否真的被项目绑定。

本模块只做纯计算（不改数据、不访问存储）：
- 定义与绑定的对应按稳定 id 精确匹配 + apiName 解析，不使用任何子串判断；
- 属性绑定 key 是 apiName，先经当前/目标版本的属性记录解析出稳定 id，再与变更原因 id 比较；
- 共享属性变更通过属性记录的 `sharedPropertyId` 命中（当前或目标版本任一存在即可）；
- 值类型变更通过属性记录的 `valueTypeId` 命中；
- 未被项目绑定的定义不产生绑定级影响（由调用方按既有「未直接引用」文案兜底）。

唯一对外入口 `binding_impacts(state, current_state, target_state, reasons)`。
"""
from workbench.project_mapping import bare

_GRAPH_TYPES = {'properties': 'owl:DatatypeProperty', 'sharedProperties': 'mg:SharedProperty',
                'valueTypes': 'mg:ValueType', 'objectTypes': 'owl:Class', 'linkTypes': 'owl:ObjectProperty'}


def _api_name(value):
    if isinstance(value, dict):
        value = value.get('@value')
    return str(value or '').strip()


def _ref_id(value):
    if isinstance(value, dict):
        value = value.get('@id')
    return str(value or '').strip()


def _records(ontology_state):
    """本体状态 → {组: {稳定 id: {apiName, objectTypeId, sharedPropertyId, valueTypeId}}}。

    兼容两种形态：JSON schema（`ontology.schemaVersion` 存在，记录字段已是结构化 id）
    与 JSON-LD 图（稳定 id 即 `@id`，字段经 mg:* / rdfs:* 读取）。结构异常只跳过该条，
    绝不抛错——升级预检必须能给出报告而不是 500。
    """
    out = {group: {} for group in _GRAPH_TYPES}
    if not isinstance(ontology_state, dict):
        return out
    ontology = ontology_state.get('ontology')
    if not isinstance(ontology, dict):
        return out
    if 'schemaVersion' in ontology:
        for group in out:
            for record in ontology.get(group) or []:
                if not isinstance(record, dict) or not record.get('id'):
                    continue
                out[group][str(record['id'])] = {
                    'apiName': _api_name(record.get('apiName')),
                    'objectTypeId': _ref_id(record.get('objectTypeId')),
                    'sharedPropertyId': _ref_id(record.get('sharedPropertyId')),
                    'valueTypeId': _ref_id(record.get('valueTypeId')),
                }
        return out
    for node in ontology.get('@graph') or []:
        if not isinstance(node, dict):
            continue
        group = next((g for g, t in _GRAPH_TYPES.items() if node.get('@type') == t), None)
        if group is None or not node.get('@id'):
            continue
        out[group][str(node['@id'])] = {
            'apiName': _api_name(node.get('mg:apiName')) or str(node['@id']).removeprefix('mg:'),
            'objectTypeId': _ref_id(node.get('rdfs:domain')),
            'sharedPropertyId': _ref_id(node.get('mg:sharedProperty')),
            'valueTypeId': _ref_id(node.get('mg:valueType')),
        }
    return out


def _property_records(index, object_type, api_name):
    """某对象上 apiName 对应的属性记录 id 集合（同一 apiName 只应有一条；全等比较）。"""
    wanted = bare(object_type)
    return {rid for rid, record in index['properties'].items()
            if record['apiName'] == api_name and bare(record['objectTypeId']) == wanted}


def _link_records(index, relation):
    rid = str(relation or '')
    if rid in index['linkTypes']:
        return {rid}
    return {key for key, record in index['linkTypes'].items() if record['apiName'] == rid}


def binding_impacts(state, current_state, target_state, reasons):
    """把本体变更原因映射到项目配置影响（纯函数；与前端 impacts 条目同构）。

    入参：
    - `state`：项目草稿（读 bindings.object_bindings / implementations）；
    - `current_state` / `target_state`：升级前后的本体状态（用于解析 apiName ↔ 稳定 id
      与 sharedPropertyId／valueTypeId 继承，兼容 JSON schema 与 JSON-LD 两种形态）；
    - `reasons`：`contracts.classify(current_state, target_state)['reasons']`。

    返回 `(impacts, matched)`：
    - impacts：与既有实现同构的条目列表 `{area, ref, severity, text}`，顺序按 reasons 顺序；
    - matched：已产生绑定级影响的 `{(area, id)}` 集合——调用方对未命中的
      breaking/pending 原因继续输出既有「本项目当前配置未直接引用」文案。
    """
    index_now, index_next = _records(current_state), _records(target_state)
    bindings = state.get('bindings', {}) if isinstance(state, dict) else {}
    object_bindings = [b for b in (bindings.get('object_bindings') or []) if isinstance(b, dict)]
    implementations = [i for i in (state.get('implementations') or []) if isinstance(i, dict)]
    impl_by_id = {i.get('id'): i for i in implementations if i.get('id')}
    impacts, matched = [], set()

    def add(area, ref, severity, text):
        impacts.append({'area': area, 'ref': ref, 'severity': severity, 'text': text})

    for reason in reasons or []:
        if not isinstance(reason, dict):
            continue
        area, rid, severity = reason.get('area'), str(reason.get('id') or ''), reason.get('severity')
        text = str(reason.get('text') or '')
        if area == 'objectTypes':
            for b in object_bindings:
                if bare(rid) == bare(b.get('object_type')):
                    add('objectBinding', b.get('object_type'), severity, text + '；此对象的数据映射需要核对')
                    matched.add((area, rid))
        elif area in ('properties', 'sharedProperties', 'valueTypes'):
            for b in object_bindings:
                for prop in (b.get('properties') or {}):
                    hit = False
                    for index in (index_now, index_next):
                        for record_id in _property_records(index, b.get('object_type'), prop):
                            record = index['properties'][record_id]
                            if area == 'properties':
                                hit = record_id == rid or hit
                            elif area == 'sharedProperties':
                                hit = record['sharedPropertyId'] == rid or hit
                            else:  # valueTypes
                                hit = record['valueTypeId'] == rid or hit
                    if hit:
                        add('propertyMapping', f"{b.get('object_type')}.{prop}", severity,
                            text + '；此属性的来源绑定需要核对')
                        matched.add((area, rid))
        elif area == 'linkTypes':
            for b in object_bindings:
                for r in b.get('relations') or []:
                    if not isinstance(r, dict):
                        continue
                    relation = str(r.get('relation') or '')
                    if relation and (bare(rid) == relation or rid in _link_records(index_now, relation)
                                     or rid in _link_records(index_next, relation)):
                        add('linkMapping', f"{b.get('object_type')}.{relation}", severity,
                            text + '；此链接映射需要核对')
                        matched.add((area, rid))
        elif area == 'contracts':
            for impl in implementations:
                if impl.get('contractId') == rid:
                    add('implementation', impl.get('id'), severity, text + '；此实现需要核对或重写')
                    matched.add((area, rid))
            for b in object_bindings:
                for prop, value in (b.get('properties') or {}).items():
                    if not (isinstance(value, dict) and value.get('kind') == 'computed'):
                        continue
                    impl = impl_by_id.get(value.get('implementation'))
                    if impl and impl.get('contractId') == rid:
                        add('propertySource', f"{b.get('object_type')}.{prop}", severity,
                            text + '；此属性的计算来源需要核对')
                        matched.add((area, rid))
    return impacts, matched
