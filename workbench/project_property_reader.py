"""受限只读预览执行器（无表对象与关联聚合 · 任务 C）。

POST /api/project-property-preview 的全部业务逻辑：登记信息预览（不查库）、
关联聚合预览（成员条件 → 参数化只读 SELECT → 求和与数据质量结果）与
仅成员预览（property 为空时，供链接映射页「预览成员」按钮）。

执行边界（设计方案 §5 / AGENTS.md #8）：
- 请求只接收 projectId/revision/objectType/instanceId/property（property 可为空
  → 仅成员预览）；不接收 SQL、表名、密码或任何配置覆盖，配置一律来自已保存
  的项目草稿。
- 短锁内读取草稿并核对 revision（不匹配 → 409），复制快照后立即释放锁；
  所有网络访问绝不持有全局写锁（LOCK 只在 projects.load + 深拷贝期间持有）。
- 本体固定为项目引用的已发布版本（versions.read_state）；表结构目录来自服务端
  catalogs 存储；密码只经 secrets vault 进入连接参数，永不回传、不写日志。
- 业务查询只在本模块执行（pymysql 参数化只读 SELECT + LIMIT）；dbdrivers 仍只
  承担固定只读探测，错误分类复用 dbdrivers._classify 的脱敏消息（不含密码、
  连接串或原始异常文本）。
- 单条成员规则候选记录上限 10000，超量直接提示缩小范围、不对截断记录求和；
  连接超时 5 秒／读取超时 8 秒，失败不自动重试。不写任何业务数据、不发布、
  不触发模型或设备指令。
"""
import copy
import re
import ssl
from datetime import datetime, date, time, timezone
from decimal import Decimal

from workbench import projects, versions, workspaces, dbdrivers
from workbench import secrets as secrets_store
from workbench import catalogs as catalog_store
from workbench.locking import LOCK
from workbench.project_mapping import (
    INT_FIELD_TYPES,
    NUMERIC_FIELD_TYPES,
    bare,
    catalog_field_meta,
    catalog_tables,
    field_type,
    graph_value,
    member_side_of,
    property_node,
    sources_of,
)
from workbench.properties import data_type

MAX_CANDIDATES = 10_000          # 单条成员规则候选上限；LIMIT 取 +1 用于超量判定
PREVIEW_MEMBERS = 20             # membersPreview 上限
NUMERIC_PROPERTY_TYPES = ('double', 'decimal', 'integer')
MEMBERSHIP_OPERATORS = ('eq', 'ne', 'in', 'isnull', 'notnull')

# 表/列标识白名单：先过正则、再过目录命中，之后才允许反引号引用。
_IDENT = re.compile(r'^[A-Za-z0-9_$]{1,64}$')


class PreviewFailed(Exception):
    """业务/配置解析失败：对外只携带脱敏中文消息，以 status:'error' 200 返回。"""


class StaleRevision(Exception):
    """revision 过期：以 409 + currentRevision 返回（与 project-save 一致语义）。"""

    def __init__(self, current_revision):
        super().__init__('项目配置已更新，请刷新后重试')
        self.current_revision = current_revision


def _fail(message):
    raise PreviewFailed(message)


def _result(status, message='', value=None, member_count=0, missing_count=0,
            members=None, truncated=False):
    """统一响应：冻结契约的全部字段始终存在。"""
    return {'status': status, 'value': value, 'memberCount': member_count,
            'missingCount': missing_count, 'membersPreview': members or [],
            'truncated': truncated,
            'evaluatedAt': datetime.now(timezone.utc).isoformat(),
            'message': message}


def preview(payload):
    """POST /api/project-property-preview 入口（路由层语义，返回 (payload, status)）。

    协议层错误（参数缺失/非法、项目不存在）抛 ValueError → 4xx；revision 过期 409；
    其余业务错误一律 200 + status:'error' + message。
    """
    try:
        return _preview(payload), 200
    except StaleRevision as exc:
        return {'error': '项目配置已更新，请刷新后重试',
                'currentRevision': exc.current_revision}, 409
    except PreviewFailed as exc:
        return _result('error', str(exc)), 200


def _preview(payload):
    if not isinstance(payload, dict):
        raise ValueError('预览请求格式无效')
    project_id = projects.clean_id(payload.get('projectId'))
    revision_in = payload.get('revision')
    if not isinstance(revision_in, str) or not revision_in:
        raise ValueError('预览请求缺少 revision（需携带客户端当前项目 revision）')
    object_type = str(payload.get('objectType') or '')
    instance_id = str(payload.get('instanceId') or '')
    prop = str(payload.get('property') or '')
    if not (object_type and instance_id):
        raise ValueError('预览请求缺少 objectType／instanceId')

    # 1) 短锁：读取已保存草稿、核对 revision（head token）、复制快照后立即释放，联网不持锁。
    with LOCK:
        state, _saved = projects.load(project_id)
        current_revision = projects.current_token(project_id)
        if revision_in != current_revision:
            raise StaleRevision(current_revision)
        snapshot = copy.deepcopy(state)

    try:
        ontology_state = versions.read_state(
            workspaces.clean_id(snapshot.get('ontologyId')), str(snapshot.get('ontologyVersion') or ''))
    except versions.VersionNotFound:
        _fail('引用的本体版本不存在，请先在项目信息中绑定已发布版本')
    except ValueError:
        _fail('项目尚未绑定本体版本，无法预览')
    graph = ontology_state['ontology']['@graph']
    catalogs = catalog_store.load_all(project_id)

    binding = next((b for b in snapshot.get('bindings', {}).get('object_bindings', [])
                    if isinstance(b, dict) and b.get('object_type') == object_type), None)
    if binding is None:
        _fail('该对象类型尚未配置数据映射，请先在对象映射页处理')

    identity = binding.get('identity') if isinstance(binding.get('identity'), dict) else {}
    if identity.get('kind') != 'registered' or not isinstance(identity.get('instances'), list):
        _fail('该对象的实例来源不是项目登记，无法按实例预览')
    instance = next((i for i in identity['instances']
                     if isinstance(i, dict) and str(i.get('id') or '') == instance_id), None)
    if instance is None:
        _fail('实例未登记或已被删除，请刷新后重试')

    source = (binding.get('properties') or {}).get(prop)
    if not prop:
        # 阶段0契约扩展：property 为空 → 仅成员预览（供链接映射页「预览成员」）。
        return _members_only_preview(project_id, snapshot, graph, catalogs,
                                     binding, object_type, instance)
    if source is None:
        _fail('该属性尚未配置取值来源，请先保存属性映射')
    kind = source.get('kind') if isinstance(source, dict) else None
    if kind == 'registered':
        return _registered_preview(source, instance)
    if kind == 'aggregate':
        return _aggregate_preview(project_id, snapshot, graph, catalogs,
                                  binding, object_type, prop, source, instance)
    if kind == 'flow':
        # 函数编排取值：本期不执行编排，明确告知而非报通用「不支持」，避免误判为配置错误。
        _fail('该属性来源为函数编排，配置校验已通过；取值预览暂不执行编排，'
              '请到「函数编排」工作区运行编排查看结果')
    _fail('该属性来源不支持预览（本轮仅支持登记信息与关联聚合）')


# --- 登记信息预览（不查库）-------------------------------------------------------------


def _registered_preview(source, instance):
    field = source.get('field')
    if field == 'id':
        value = str(instance.get('id') or '')
    elif field == 'label':
        value = str(instance.get('label') or '')
    else:
        _fail('登记信息取值仅支持实例编号（id）或显示名称（label）')
    return _result('ok', f'登记信息取值（{field}），未查询数据库', value=value)


# --- 关联聚合预览 -----------------------------------------------------------------------


def _aggregate_preview(project_id, snapshot, graph, catalogs,
                       binding, object_type, prop, source, instance):
    relation_key = str(source.get('relation') or '')
    member_prop = str(source.get('property') or '')
    if not relation_key or not member_prop:
        _fail('聚合配置缺少关联链接或成员属性，请先完整配置')
    if source.get('operator') != 'sum':
        _fail('聚合方式仅支持求和（sum）')
    if source.get('empty') not in (None, '', 'null'):
        _fail('空集合策略仅支持返回空（null），无法按当前配置执行')
    if source.get('missing') not in (None, '', 'incomplete'):
        _fail('缺失策略仅支持标记不完整（incomplete），无法按当前配置执行')

    relation_mapping = next((r for r in binding.get('relations') or []
                             if isinstance(r, dict) and r.get('kind') == 'membership'
                             and bare(str(r.get('relation') or '')) == relation_key), None)
    if relation_mapping is None:
        _fail('此链接尚未配置按条件选择成员的映射，请先在链接映射页处理')
    member = _resolve_member_source(project_id, snapshot, catalogs, binding, object_type,
                                    relation_mapping, relation_key, graph)
    target_binding, link_node = member['target_binding'], member['link_node']
    link_label = str(graph_value(link_node, 'rdfs:label') or relation_key)

    # 本体数值属性校验（目标属性与成员属性都必须是数值单值属性；时间序列/文本拒绝）。
    _require_numeric_property(graph, object_type, prop, '聚合目标属性')
    member_node = _require_numeric_property(graph, member['target_type'], member_prop, '聚合成员属性')
    member_prop_label = str(graph_value(member_node, 'rdfs:label') or member_prop)

    # 成员值字段：必须是身份表直接数值字段（含可证明等价的旧表示），其他来源明确拒绝。
    member_config = (target_binding.get('properties') or {}).get(member_prop)
    if member_config is None:
        _fail('成员对象尚未映射该成员属性，请先配置属性来源')
    value_field = _direct_field(target_binding, member_config)
    if value_field is None:
        _unsupported_member_source(member_config)
    if value_field not in member['field_meta'] or not _IDENT.match(value_field):
        _fail('成员值字段不在目录中，请刷新表结构')
    if field_type(member['field_meta'].get(value_field)) not in NUMERIC_FIELD_TYPES:
        _fail(f'成员值字段「{value_field}」不是数值字段，请核对映射与表结构')

    members = _collect_members(member, relation_mapping, instance, value_field)
    member_list = list(members.values())
    member_count = len(member_list)
    preview, truncated = _members_preview(members)
    if member_count == 0:
        return _result('empty', '未匹配到成员设备', value=None, member_count=0)
    missing = [entry for entry in member_list if entry['value'] is None]
    if missing:
        return _result('incomplete',
                       f'{len(missing)} 个成员的取值缺失，结果不完整（缺失不按 0 计）',
                       value=None, member_count=member_count, missing_count=len(missing),
                       members=preview, truncated=truncated)
    try:
        total = sum(float(entry['value']) for entry in member_list)
    except (TypeError, ValueError):
        _fail('成员取值不是有效数值，请核对表结构与映射')
    value = int(total) if float(total).is_integer() else total
    return _result('ok',
                   f'沿【{link_label}】对 {member_count} 个成员的【{member_prop_label}】求和',
                   value=value, member_count=member_count, members=preview, truncated=truncated)


# --- 仅成员预览（property 为空；供链接映射页「预览成员」）--------------------------------


def _members_only_preview(project_id, snapshot, graph, catalogs, binding, object_type, instance):
    instance_key = str(instance.get('id') or '')
    candidates = []
    for relation_mapping in binding.get('relations') or []:
        if not isinstance(relation_mapping, dict) or relation_mapping.get('kind') != 'membership':
            continue
        if any(isinstance(rule, dict) and str(rule.get('sourceInstance') or '') == instance_key
               for rule in relation_mapping.get('rules') or []):
            candidates.append(relation_mapping)
    if not candidates:
        _fail('该实例尚未配置任何链接的成员条件')
    if len(candidates) > 1:
        _fail('该实例在多个链接下配置了成员条件，请从具体属性的关联聚合预览指定链接')
    relation_mapping = candidates[0]
    relation_key = bare(str(relation_mapping.get('relation') or ''))
    member = _resolve_member_source(project_id, snapshot, catalogs, binding, object_type,
                                    relation_mapping, relation_key, graph)
    members = _collect_members(member, relation_mapping, instance, '')  # 不取值字段
    preview, truncated = _members_preview(members)
    return _result('ok', '仅成员预览', value=None, member_count=len(members),
                   missing_count=0, members=preview, truncated=truncated)


# --- 共享：成员来源解析与成员集合 -------------------------------------------------------


def _resolve_member_source(project_id, snapshot, catalogs, binding, object_type,
                           relation_mapping, relation_key, graph):
    """解析成员来源：目标绑定、链接方向/数量关系、连接+凭据、目录白名单与表/主键。"""
    target_type = str(relation_mapping.get('target_type') or '')
    target_binding = next((b for b in snapshot.get('bindings', {}).get('object_bindings', [])
                           if isinstance(b, dict) and b.get('object_type') == target_type), None)
    if target_binding is None:
        _fail('成员对象尚未配置数据映射')
    if not str(target_binding.get('connection') or '').strip():
        _fail('成员对象必须是数据库表来源（项目登记对象不能作为聚合成员）')

    link_node = next((n for n in graph if n['@type'] == 'owl:ObjectProperty'
                      and bare(n['@id']) == relation_key), None)
    if link_node is None:
        _fail('引用的本体版本中不存在此链接，请升级引用后重试')
    # 集合端按端判定（方向无关）：出向一对多/多对多或入向多对一/多对多，成员=另一端。
    member_type, cardinality, orientation = member_side_of(graph, object_type, relation_key)
    if not member_type or member_type != target_type:
        _fail('链接方向或数量关系与成员规则不一致，请核对链接映射配置'
              '（集合端须出向一对多／多对多，或入向多对一／多对多）')
    # 归一化为「集合端视角」的成员基数：入向多对一的反向即一对多成员集合（参与归属核对）。
    if orientation == 'in' and cardinality == 'many-to-one':
        cardinality = 'one-to-many'

    conn_id = str(target_binding.get('connection') or '')
    conn = next((c for c in snapshot.get('connections', {}).get('connections', [])
                 if isinstance(c, dict) and c.get('id') == conn_id), None)
    if conn is None:
        _fail('成员对象引用的数据连接不存在')
    if conn.get('engine') and conn.get('engine') != 'mysql':
        _fail('成员对象需要 MySQL 连接（Redis 连接不能作为聚合成员来源）')
    try:
        cfg = dbdrivers.normalize_config(conn)
    except ValueError as exc:
        _fail(f'数据连接配置无效：{exc}')
    secret = secrets_store.read(project_id, conn_id)

    table = str(target_binding.get('table') or '')
    pk_field = str(target_binding.get('primary_key') or '')
    tables = catalog_tables(catalogs, conn_id)
    if tables is None:
        _fail('表结构目录未读取或已过期，请先在数据连接页刷新表结构')
    if table not in tables:
        _fail('成员表不在当前表结构目录中，请刷新表结构')
    field_meta = catalog_field_meta(tables, table)
    if pk_field not in field_meta or not _IDENT.match(pk_field):
        _fail('实例主键字段不在目录中，请刷新表结构')

    # 预览明细中的名称列：仅当显示名称属性映射为身份表直接字段时提供。
    label_field = ''
    title_key = str(target_binding.get('title_key') or '')
    if title_key:
        candidate = _direct_field(target_binding, (target_binding.get('properties') or {}).get(title_key))
        if candidate and candidate in field_meta and _IDENT.match(candidate):
            label_field = candidate
    return {'target_type': target_type, 'target_binding': target_binding, 'link_node': link_node,
            'cardinality': cardinality, 'cfg': cfg, 'secret': secret, 'table': table,
            'pk_field': pk_field, 'field_meta': field_meta, 'label_field': label_field}


def _collect_members(member, relation_mapping, instance, value_field):
    """执行该实例的全部成员规则并按主键去重；含超量阻断与一对多归属核对。

    value_field 为空字符串时为「仅成员预览」：不取值字段、无取值冲突判定。
    """
    cfg, secret = member['cfg'], member['secret']
    table, pk_field = member['table'], member['pk_field']
    label_field, field_meta = member['label_field'], member['field_meta']
    instance_key = str(instance.get('id') or '')
    rules = [r for r in relation_mapping.get('rules') or [] if isinstance(r, dict)]
    own_rules = [r for r in rules if str(r.get('sourceInstance') or '') == instance_key]
    if not own_rules:
        _fail('该实例尚未在此链接配置成员条件')
    members = {}  # 主键值 -> {'value','label','fields'}；跨本实例全部规则按主键去重
    # 先执行本实例规则收集成员，再执行其他实例规则做一对多归属核对。
    ordered_rules = list(own_rules) + [r for r in rules if str(r.get('sourceInstance') or '') != instance_key]
    for rule in ordered_rules:
        is_own = str(rule.get('sourceInstance') or '') == instance_key
        if not is_own and member['cardinality'] != 'one-to-many':
            continue  # 多对多允许跨实例共享成员；仅一对多需要核对归属
        try:
            columns, sql, params, fields_used = compile_rule_query(
                rule, table, pk_field, label_field, value_field, field_meta)
            rows = _fetch(cfg, secret, sql, params)
        except PreviewFailed:
            if is_own:
                raise
            continue  # 其他实例的规则失效不阻塞本实例预览（其自身预览会暴露问题）
        if len(rows) > MAX_CANDIDATES:
            if is_own:
                _fail('候选记录超过 10000，请缩小成员范围')
            continue
        pk_index = columns.index(pk_field)
        label_index = columns.index(label_field) if label_field else None
        value_index = columns.index(value_field) if value_field else None
        for row in rows:
            pk_value = row[pk_index]
            existing = members.get(pk_value)
            if not is_own:
                # 一对多归属核对：本实例成员若同时命中其他起点实例的规则 → 报冲突。
                if existing is not None:
                    _fail('成员同时属于多个系统实例，请核对归属条件')
                continue
            if existing is None:
                members[pk_value] = {
                    'value': row[value_index] if value_index is not None else None,
                    'label': row[label_index] if label_index is not None else None,
                    'fields': {name: row[columns.index(name)] for name in fields_used},
                }
                continue
            # 同主键重复行：取值相同则去重，不同则报数据冲突（不静默取一条）。
            if value_index is not None and not _same_value(existing['value'], row[value_index]):
                _fail(f'重复主键「{_jsonable(pk_value)}」的成员取值冲突，请核对成员数据')
            if existing['label'] is None and label_index is not None:
                existing['label'] = row[label_index]
    return members


def _members_preview(members):
    """成员明细前 PREVIEW_MEMBERS 条 + 是否截断。"""
    preview = []
    for pk_value, entry in list(members.items())[:PREVIEW_MEMBERS]:
        item = {'id': _jsonable(pk_value), 'fields': {
            name: _jsonable(value) for name, value in entry['fields'].items()}}
        if entry['label'] is not None:
            item['label'] = _jsonable(entry['label'])
        preview.append(item)
    return preview, len(members) > PREVIEW_MEMBERS


def _require_numeric_property(graph, object_type, prop, label):
    node = property_node(graph, object_type, prop)
    if node is None:
        _fail(f'{label}「{prop}」不存在于引用的本体版本')
    dtype = data_type(node, graph)
    if dtype.get('type') == 'timeSeries':
        _fail(f'{label}「{prop}」是时间序列属性，不能用于本轮聚合')
    if dtype.get('type') not in NUMERIC_PROPERTY_TYPES:
        _fail(f'{label}「{prop}」必须是数值属性')
    return node


def _unsupported_member_source(member_config):
    kind = member_config.get('kind') if isinstance(member_config, dict) else 'legacy'
    if kind == 'aggregate':
        _fail('成员属性本身是聚合结果，不支持聚合嵌套（避免递归循环）')
    if kind == 'redis':
        _fail('成员属性是 Redis 来源，本轮聚合仅支持身份表直接数值字段')
    if kind == 'computed':
        _fail('成员属性是计算来源，本轮聚合仅支持身份表直接数值字段')
    _fail('成员属性必须是身份表直接数值字段（补充来源／多行选取等映射不支持聚合）')


def _direct_field(binding, config):
    """把属性映射解析为身份表上的直接字段；无法证明等价时返回 None。

    接受三种表示：旧字符串直取；kind:'field' 且无补充来源（或来源与身份表
    同连接同表、一对一）；kind:'database' 且与身份表同连接同表、按当前实例
    绑定、无时间范围与多行选取（可证明等价的直取）。
    """
    if isinstance(config, str):
        return config.strip() or None
    if not isinstance(config, dict):
        return None
    kind = config.get('kind')
    if kind == 'field':
        field = str(config.get('field') or '').strip()
        if not field:
            return None
        source_id = str(config.get('source') or '')
        if not source_id:
            return field
        source = sources_of(binding).get(source_id)
        if (source and source.get('kind') == 'db'
                and source.get('cardinality') in (None, '', 'one')
                and str(source.get('connection') or '') == str(binding.get('connection') or '')
                and str(source.get('table') or '') == str(binding.get('table') or '')):
            return field
        return None
    if kind == 'database':
        if (str(config.get('connection') or '') != str(binding.get('connection') or '')
                or str(config.get('table') or '') != str(binding.get('table') or '')):
            return None
        lookup = config.get('lookup') if isinstance(config.get('lookup'), dict) else {}
        result = config.get('result') if isinstance(config.get('result'), dict) else {}
        if lookup.get('timeRange') is not None or result.get('selection'):
            return None
        binds_instance = any(
            isinstance(match, dict) and isinstance(match.get('value'), dict)
            and match['value'].get('kind') in ('identityKey', 'identityField')
            for match in lookup.get('match') or [])
        if not binds_instance:
            return None
        return str(result.get('valueField') or '').strip() or None
    return None


# --- SQL 构造（标识与参数严格分离）-----------------------------------------------------


def _quote(identifier):
    if not _IDENT.match(identifier or ''):
        raise ValueError('标识无效')  # 上层在目录校验前拦截；此处为纵深防御
    return '`' + identifier + '`'


def _condition_value(value, ftype, field):
    if value is None or (isinstance(value, str) and not value.strip()):
        _fail(f'条件字段「{field}」缺少比较值')
    if ftype in NUMERIC_FIELD_TYPES:
        try:
            number = float(int(value)) if isinstance(value, bool) else (
                float(value) if isinstance(value, (int, float)) else float(str(value).strip()))
        except (TypeError, ValueError):
            _fail(f'条件字段「{field}」的取值与数值类型不兼容')
        if ftype in INT_FIELD_TYPES and not number.is_integer():
            _fail(f'条件字段「{field}」是整数字段，取值需要是整数')
        return int(number) if ftype in INT_FIELD_TYPES else number
    return str(value)


def _compile_conditions(conditions, field_meta):
    """条件 → (SQL 片段列表, 参数列表, 使用字段列表)。标识只经白名单反引号引用，值全部参数化。"""
    clauses, params, fields_used = [], [], []
    for condition in conditions:
        if not isinstance(condition, dict):
            _fail('成员条件配置格式无效')
        field = str(condition.get('field') or '')
        if not _IDENT.match(field):
            _fail(f'条件字段「{field}」不在目录中，请刷新表结构')
        meta = field_meta.get(field)
        if meta is None:
            _fail(f'条件字段「{field}」不在目录中，请刷新表结构')
        operator = condition.get('operator')
        if operator not in MEMBERSHIP_OPERATORS:
            _fail('成员条件操作符仅支持等于／不等于／属于列表／为空／不为空')
        quoted = _quote(field)
        ftype = field_type(meta)
        if operator == 'eq':
            clauses.append(f'{quoted} = %s')
            params.append(_condition_value(condition.get('value'), ftype, field))
        elif operator == 'ne':
            clauses.append(f'{quoted} <> %s')
            params.append(_condition_value(condition.get('value'), ftype, field))
        elif operator == 'in':
            values = condition.get('value')
            if not isinstance(values, list) or not values:
                _fail(f'条件字段「{field}」的属于列表需要至少一个取值')
            clauses.append(f'{quoted} IN ({", ".join(["%s"] * len(values))})')
            params.extend(_condition_value(value, ftype, field) for value in values)
        elif operator == 'isnull':
            clauses.append(f'{quoted} IS NULL')
        else:
            clauses.append(f'{quoted} IS NOT NULL')
        fields_used.append(field)
    return clauses, params, fields_used


def compile_rule_query(rule, table, pk_field, label_field, value_field, field_meta):
    """一条成员规则 → (列名列表, SQL, 参数列表, 条件字段列表)。

    SELECT <主键>,<名称列>,<条件字段…>,<值字段> FROM <身份表> [WHERE …] LIMIT 10001；
    表/列标识先过正则与目录白名单再反引号引用；比较值全部走 %s 参数。
    """
    scope = rule.get('scope')
    conditions = rule.get('conditions') if isinstance(rule.get('conditions'), list) else []
    if scope == 'filtered':
        if not conditions:
            _fail('按条件选择成员至少需要一条条件（使用整表范围需显式选择）')
        clauses, params, fields_used = _compile_conditions(conditions, field_meta)
    elif scope == 'all':
        clauses, params, fields_used = [], [], []  # 显式全表：无 WHERE
    else:
        _fail('成员范围无效（仅支持按条件选择或显式全表）')
    columns = [pk_field]
    if label_field and label_field not in columns:
        columns.append(label_field)
    for name in fields_used:
        if name not in columns:
            columns.append(name)
    if value_field and value_field not in columns:
        columns.append(value_field)
    for name in columns:
        _quote(name)  # 纵深防御：任何列名不过白名单即拒绝构造
    sql = f'SELECT {", ".join(_quote(c) for c in columns)} FROM {_quote(table)}'
    if clauses:
        sql += ' WHERE ' + ' AND '.join(clauses)
    sql += f' LIMIT {MAX_CANDIDATES + 1}'
    return columns, sql, params, fields_used


# --- 只读执行（联网，不持锁）-----------------------------------------------------------


def _connect(cfg, secret):
    """按 dbdrivers 的连接参数构建 pymysql 连接（只读用途，autocommit，短超时）。"""
    import pymysql
    tls = cfg.get('tls', 'none')
    kwargs = {}
    if tls == 'none':
        kwargs['ssl_disabled'] = True
    elif tls == 'verify':
        kwargs['ssl'] = {'ca': cfg.get('caPath') or None, 'cert_reqs': ssl.CERT_REQUIRED}
    else:
        kwargs['ssl_disabled'] = False
    return pymysql.connect(host=cfg['host'], port=cfg['port'], user=cfg.get('username') or '',
                           password=secret or '', database=cfg['database'],
                           connect_timeout=5, read_timeout=8, write_timeout=8,
                           charset='utf8mb4', autocommit=True, **kwargs)


def _classified_message(exc):
    """脱敏错误消息：复用 dbdrivers._classify 的分类文案，不泄密码/连接串/原始异常。"""
    try:
        return dbdrivers._classify(exc).get('message') or '数据查询失败：请核对数据连接与权限'
    except Exception:  # noqa: BLE001 — 分类器自身异常也不能把原始信息带出去
        return '数据查询失败：请核对数据连接与权限'


def _fetch(cfg, secret, sql, params):
    try:
        connection = _connect(cfg, secret)
    except ImportError as exc:
        raise PreviewFailed(_classified_message(exc))
    except Exception as exc:  # noqa: BLE001 — 驱动/网络错误统一脱敏
        raise PreviewFailed(_classified_message(exc))
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            return cursor.fetchall()
    except Exception as exc:  # noqa: BLE001
        raise PreviewFailed(_classified_message(exc))
    finally:
        try:
            connection.close()
        except Exception:  # noqa: BLE001 — 关闭失败不能掩盖查询结果
            pass


def _same_value(left, right):
    if left is None or right is None:
        return left is None and right is None
    try:
        return float(left) == float(right)
    except (TypeError, ValueError):
        return str(left) == str(right)


def _jsonable(value):
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.decode('utf-8', 'replace')
    return str(value)
