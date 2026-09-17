"""Shared pure mapping helpers for project bindings (B2 拆分自 projects.py).

身份映射、补充来源（新旧格式合并）、目录元数据、字段类型集合与本体节点查找等
双方（存储层 projects.py / 校验层 project_validation.py）共用的纯辅助。
本模块不得 import projects（避免循环依赖）；properties/contracts 均为叶子模块，可安全引用。

命名说明：原 projects.py 私有名（_bare、_sources_of 等）在此去下划线成公开名，
projects.py 内如仍有引用经兼容别名或直接改调用点。
"""
import re

from workbench.properties import effective, is_display_name, data_type


def bare(type_id):
    return str(type_id).removeprefix('mg:')


def property_api(node):
    return node.get('mg:apiName', {}).get('@value') if isinstance(node.get('mg:apiName'), dict) else node.get('mg:apiName') or node['@id'].removeprefix('mg:')


def derive_display_names(state, ontology_state):
    """对象绑定跟随本体引用版本中的「显示名称」标记（mg:isDisplayName）。

    标记在引用版本的属性上（含共享定义继承）；项目层不再单独选择。
    未标记的对象保持草稿中的既有 title_key 不变。
    注意：这是现存行为——直接改写 state 中绑定的 title_key（B2 拆分保持原样，
    规范化调整属于后续步骤）。
    """
    graph = ontology_state['ontology']['@graph']
    titles = {}
    for n in graph:
        if n.get('@type') != 'owl:DatatypeProperty':
            continue
        merged = effective(n, graph)
        if is_display_name(merged):
            api = merged.get('mg:apiName')
            if isinstance(api, dict):
                api = api.get('@value')
            titles[merged.get('rdfs:domain', {}).get('@id', '')] = api or merged['@id'].removeprefix('mg:')
    for b in state.get('bindings', {}).get('object_bindings', []):
        marker = 'mg:' + str(b.get('object_type', ''))
        if marker in titles:
            b['title_key'] = titles[marker]
    return state


def graph_value(node, key):
    """Read a graph literal that may be stored as {'@value': x} or as plain x."""
    value = (node or {}).get(key)
    return value.get('@value') if isinstance(value, dict) else value


KEY_TOKEN = re.compile(r'\{([^{}\s]+)\}')


def sources_of(binding):
    """Unified view of an object binding's supplementary data sources.

    New-style ``sources`` entries are used as-is. Legacy ``related_sources``
    entries are adapted (db source on the binding's own connection, strict
    one-to-one matching) and only fill ids the new list does not cover, so
    both generations coexist and merge by id.
    """
    out = {}
    for source in binding.get('related_sources') or []:
        if not isinstance(source, dict) or not source.get('id'):
            continue
        out[source['id']] = {'id': source['id'], 'name': source.get('name', ''),
                             'kind': 'db', 'connection': binding.get('connection', ''),
                             'table': source.get('table', ''),
                             'matchLeft': source.get('source_field', ''),
                             'matchRight': source.get('target_field', ''),
                             'cardinality': 'one', 'legacy': True}
    for source in binding.get('sources') or []:
        if isinstance(source, dict) and source.get('id'):
            out[source['id']] = dict(source)
    return out


def catalog_tables(catalogs, conn_id):
    """Map of table name -> table entry for a connection's refreshed catalog, or None."""
    entry = catalogs.get(conn_id) if isinstance(catalogs, dict) and isinstance(catalogs.get(conn_id), dict) else None
    tables = entry.get('tables') if entry else None
    if not isinstance(tables, list):
        return None
    return {t.get('name'): t for t in tables if isinstance(t, dict) and t.get('name')}


def catalog_fields(tables, table_name):
    entry = tables.get(table_name) if tables else None
    fields = entry.get('fields') if isinstance(entry, dict) else None
    return {f.get('name') for f in fields if isinstance(f, dict)} if isinstance(fields, list) else None


def catalog_field_meta(tables, table_name):
    """Map of field name -> raw catalog field entry (with dataType) for one table."""
    entry = tables.get(table_name) if tables else None
    fields = entry.get('fields') if isinstance(entry, dict) else None
    return {f.get('name'): f for f in fields if isinstance(f, dict) and f.get('name')} if isinstance(fields, list) else {}


NUMERIC_FIELD_TYPES = {'double', 'float', 'decimal', 'numeric', 'real', 'integer',
                       'int', 'bigint', 'smallint', 'tinyint', 'mediumint'}
INT_FIELD_TYPES = {'integer', 'int', 'bigint', 'smallint', 'tinyint', 'mediumint'}
DATETIME_FIELD_TYPES = {'date', 'datetime', 'timestamp'}


def field_type(field_meta):
    """Lowercased catalog data type of a field entry ({}-safe)."""
    return str((field_meta or {}).get('dataType', '') or '').lower()


def property_node(graph, ot, prop):
    """Ontology DatatypeProperty node for one object property (by apiName), or None."""
    return next((n for n in graph if n['@type'] == 'owl:DatatypeProperty'
                 and bare(n.get('rdfs:domain', {}).get('@id')) == ot
                 and property_api(n) == prop), None)


def value_shape(node, graph):
    """Compatibility branch for existing source validators; derived from property type."""
    return 'timeSeries' if data_type(node, graph)['type'] == 'timeSeries' else 'scalar'


def link_end_issue(source_id, sources):
    """Structural issue for one end of a link mapping; empty id means the identity source."""
    if not source_id:
        return None
    source = sources.get(source_id)
    if source is None:
        return '链接端点引用的数据来源不存在'
    if source.get('kind') != 'db':
        return '链接字段不能来自 Redis 来源'
    if source.get('cardinality') == 'many':
        return '链接字段来自多条匹配的来源，不能确定唯一行'
    return None


# --- 无表对象（registered）与关联聚合（aggregate / membership）纯辅助 ----------------
# 任务 B（后端校验）冻结接口：以下函数同时供任务 C 的只读执行器复用。
# 语义与数据契约（与前端 A 一致）：
#   identity: {kind:'registered', instances:[{id,label}]}（无块 = 旧数据库身份）
#   属性来源新 kind: registered(field:'id'|'label') / aggregate(relation,property,operator:'sum',
#     empty:'null' 字符串, missing:'incomplete', inputUnitConfirmed)
#   relations 新 kind:'membership'（rules:[{sourceInstance,scope,conditions:[...]}]；无 kind = 旧 column 关联）
# 全部为纯读取：不修改、不删除、不迁移任何项目数据（未知形态原样保留）。

DATABASE_IDENTITY = 'database'
REGISTERED_IDENTITY = 'registered'
IDENTITY_TEXT_LIMIT = 128
MEMBERSHIP_CARDINALITIES = ('one-to-many', 'many-to-many')
# 集合端（成员规则/聚合的持有者）按端判定的合法数量关系：
# 持有者在 domain 侧（出向）须 one-to-many／many-to-many；在 range 侧（入向）须
# many-to-one（多对一的反向即一对多成员集合）／many-to-many。
OWNER_DOMAIN_CARDINALITIES = ('one-to-many', 'many-to-many')
OWNER_RANGE_CARDINALITIES = ('many-to-one', 'many-to-many')


def member_side_of(graph, owner_type, relation):
    """按「集合拥有者 = owner_type」判定链接的成员端（方向无关，§3.2 按端判定）。

    返回 (member_type, cardinality, orientation)：orientation 'out'=出向（owner=domain，
    成员=range）、'in'=入向（owner=range，成员=domain）；不成立时 member_type 为 ''。
    供校验与执行器共用的单一判定来源。
    """
    node = object_property_node(graph, relation) if relation else None
    if node is None:
        return '', '', ''
    domain = bare(node.get('rdfs:domain', {}).get('@id', '')) if isinstance(node.get('rdfs:domain'), dict) else ''
    range_ref = node.get('rdfs:range')
    rng = bare(range_ref.get('@id')) if isinstance(range_ref, dict) else ''
    cardinality = str(graph_value(node, 'mg:cardinality') or '')
    if domain == owner_type and cardinality in OWNER_DOMAIN_CARDINALITIES and rng:
        return rng, cardinality, 'out'
    if rng == owner_type and cardinality in OWNER_RANGE_CARDINALITIES and domain:
        return domain, cardinality, 'in'
    return '', cardinality, ''


def identity_kind(binding):
    """对象绑定的实例来源方式（identity 块分支判定，纯读取）。

    返回 'database'：无 identity 块（旧 connection/table/primary_key 数据库身份）；
    返回 'registered'：identity 为 dict 且 kind == 'registered'（项目登记实例）；
    返回 None：identity 块存在但 kind 未知 —— 调用方必须报错并原样保留数据，
    不得按默认类型解释或执行。
    """
    identity = binding.get('identity')
    if not isinstance(identity, dict):
        return DATABASE_IDENTITY
    return REGISTERED_IDENTITY if identity.get('kind') == REGISTERED_IDENTITY else None


def registered_instances(binding):
    """登记实例条目列表（仅保留 dict 条目、保持存储顺序）；非 registered 身份返回 []。

    条目结构 {id, label}；本函数不做合法性校验（校验在 project_validation），
    执行方需自行跳过无有效 id 的条目。
    """
    if identity_kind(binding) != REGISTERED_IDENTITY:
        return []
    identity = binding.get('identity') or {}
    instances = identity.get('instances') if isinstance(identity.get('instances'), list) else []
    return [i for i in instances if isinstance(i, dict)]


def membership_rules(binding, relation):
    """对象在某链接（按 relation 稳定键）下的成员规则列表；无 membership 配置返回 []。

    聚合校验用「是否为空」判断该链接是否已有成员规则；执行器用逐条规则定位成员。
    只收集 kind == 'membership' 且 relation 匹配的条目（无 kind 的旧 column 关联不算）。
    """
    out = []
    for r in binding.get('relations') or []:
        if isinstance(r, dict) and r.get('kind') == 'membership' and str(r.get('relation', '') or '') == str(relation):
            rules = r.get('rules') if isinstance(r.get('rules'), list) else []
            out.extend(rule for rule in rules if isinstance(rule, dict))
    return out


def identity_field_mapping(binding, prop):
    """属性的「身份表直接字段」视图：返回可直接读取的字段名，无法证明时返回 None。

    接受三种等价表示（本轮聚合的成员属性要求）：
    - 旧字符串映射（非空字符串，值即身份表字段名）；
    - {kind:'field', source:''}（source 缺省或空 = 身份来源本表，field 非空）；
    - 旧 {kind:'database'} 且 connection/table 与绑定身份表一致（result.valueField 直连）。
    {kind:'related'}、补充来源 field、跨表 database、redis/computed 等一律返回 None。
    """
    properties = binding.get('properties') if isinstance(binding.get('properties'), dict) else {}
    value = properties.get(prop)
    if isinstance(value, str):
        return value.strip() or None
    if not isinstance(value, dict):
        return None
    if value.get('kind') == 'field' and not str(value.get('source', '') or '').strip():
        return str(value.get('field', '') or '').strip() or None
    if value.get('kind') == 'database':
        conn = str(value.get('connection', '') or '').strip()
        table = str(value.get('table', '') or '').strip()
        if (conn and table
                and conn == str(binding.get('connection', '') or '').strip()
                and table == str(binding.get('table', '') or '').strip()):
            result = value.get('result') if isinstance(value.get('result'), dict) else {}
            return str(result.get('valueField', '') or '').strip() or None
    return None


def object_property_node(graph, relation):
    """按去前缀稳定键查找本体 owl:ObjectProperty 节点，或 None（与 relations 集合同口径）。"""
    return next((n for n in graph if n.get('@type') == 'owl:ObjectProperty'
                 and bare(n.get('@id', '')) == str(relation)), None)
