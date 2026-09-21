"""Project configuration validation (V3 section 8) — B2 拆分自 projects.py.

组织函数 validate_project 汇总各职责校验：连接、对象绑定（identity+补充来源+目录过期
提示）、计算实现、属性来源（含 database match{kind:'property'} 依赖环检测）、链接映射、
契约覆盖（实现输出环 + 被引用未实现警告）。

依赖读取与失效（2026-09-20 v2 冻结，见接口文档 03 分册 §2.2/§3.3）：
- `degraded_catalogs`（目录缓存损坏的连接 id 列表，由路由层依据存储层结果传入）：
  每个连接追加一条 error 阻断发布；None/空 = 与既有行为逐字节一致。
- 编排依赖经 `flows.dependency_state` 三态消费：missing 沿用既有「不存在或已删除」文案；
  unreadable（含存储不可用，由 dependency_state 归一为 unreadable）一律 fail-closed
  （error 阻断发布，绝不当「不存在」）。动作绑定的 `flows.listing()` 读取失败同样
  fail-closed：`storage.StorageUnavailable` 原样抛出（路由 503），其他失败按行报 error。
  校验只读，不删除、不改写任何原配置。

B2 为机械提取：循环顺序、追加顺序、字符串文案、status 判定与原实现完全一致，
行为修复不属于本包（由金样 tests/fixtures/validation_golden.json 锁定）。
注意：开头仍按现存行为调用 derive_display_names 就地改写 state 的 title_key
（规范化调整属于后续步骤）。

任务 B（无表对象与关联聚合，2026-09-15）新增分支校验（旧分支零改动）：
- identity 分支：registered 对象不要求 connection/table/primary_key，校验登记实例
  （非空、同对象编号唯一、≤128、禁换行）；无 identity 块的数据库对象原校验原样；
  identity 块存在但 kind 未知 → error「未知的实例来源方式」（数据原样保留）。
- 属性来源 kind registered（登记信息）/ aggregate（关联聚合，SUM、身份表直接数值字段、
  禁聚合依赖聚合）；items 新 kind：registeredIdentity / aggregateSource。
- 链接映射 kind membership（按条件选择成员）；items 新 kind：membership。
validate_project 只读：不删除、不迁移任何项目数据，仅产出 errors/warnings/items。
"""
import re
import zoneinfo

from workbench.query_rules import is_query_rule, query_rule_errors, rule_input_errors, scan_sql_params, strip_sql_noise_mysql
from workbench import action_http
from workbench import calc_functions
from workbench import flows

from workbench import storage

# 依赖目录读取状态哨兵：区别于 None（未知/未提供，跳过检查）与已知空集合。
_READ_FAILED = object()

from workbench.contracts import is_contract
from workbench.properties import effective, signature_data_type
from workbench.project_mapping import (
    KEY_TOKEN,
    DATETIME_FIELD_TYPES,
    INT_FIELD_TYPES,
    NUMERIC_FIELD_TYPES,
    IDENTITY_TEXT_LIMIT,
    bare,
    catalog_field_meta,
    catalog_fields,
    catalog_tables,
    derive_display_names,
    field_type,
    graph_value,
    identity_field_mapping,
    identity_kind,
    link_end_issue,
    member_side_of,
    membership_rules,
    object_property_node,
    property_node,
    registered_instances,
    sources_of,
    value_shape,
)


def _inline_constant_ok(data_type, value):
    """内联 SQL 常量参数：明确填写的 0／false／空字符串是有效值；仅缺失或类型不符无效。"""
    if value is None:
        return False
    if data_type == 'double':
        text = str(value).strip()
        try:
            return text != '' and float(text) == float(text)
        except (ValueError, TypeError):
            return False
    if data_type == 'boolean':
        return value is True or value is False or value in ('true', 'false')
    return True


def validate_project(state, ontology_state, degraded_catalogs=None):
    """项目草稿校验总入口（只读；不改写除 derive_display_names 既有改写外的任何数据）。

    `degraded_catalogs`：目录缓存损坏（读取失败）的连接 id 列表，由路由层依据存储层
    读取结果传入（03 分册 §2.2）。每个 id 追加一条 error（含连接 id/名称）+ 一条
    `connection` items 条目阻断发布；`None`/空列表 = 与既有行为逐字节一致（金样不变）。
    """
    derive_display_names(state, ontology_state)
    graph = ontology_state['ontology']['@graph']
    classes = {n['@id'] for n in graph if n['@type'] == 'owl:Class'}
    bare_classes = {bare(c) for c in classes}
    relations = {n['@id'].removeprefix('mg:') for n in graph if n['@type'] == 'owl:ObjectProperty'}
    functions = {f['id']: f for f in ontology_state.get('workflow', {}).get('functions', [])}
    bindings = state.get('bindings', {})
    object_bindings = bindings.get('object_bindings', [])
    catalogs = bindings.get('catalogs') if isinstance(bindings.get('catalogs'), dict) else {}
    connections = [c for c in state.get('connections', {}).get('connections', []) if isinstance(c, dict)]
    conn_by_id = {c.get('id'): c for c in connections if c.get('id')}
    parameters = state.get('parameters') if isinstance(state.get('parameters'), dict) else {}
    implementations = {i['id']: i for i in state.get('implementations', []) if isinstance(i, dict)}
    ctx = {'state': state, 'graph': graph, 'classes': classes, 'bare_classes': bare_classes,
           'relations': relations, 'functions': functions, 'bindings': bindings,
           'object_bindings': object_bindings, 'catalogs': catalogs, 'connections': connections,
           'conn_by_id': conn_by_id, 'parameters': parameters, 'implementations': implementations,
           'implementation_list': state.get('implementations', []), 'mapped_types': {},
           'ontology_state': ontology_state}
    errors, warnings, items = [], [], []
    _check_connections(ctx, errors, items)
    _check_object_bindings(ctx, errors, warnings, items)
    _check_implementations(ctx, errors, items)
    _check_property_sources(ctx, errors, warnings, items)
    _check_link_mappings(ctx, errors, warnings, items)
    _check_contract_coverage(ctx, errors, warnings)
    _check_action_bindings(ctx, errors, warnings, items)
    _check_mapping_descriptions(ctx, errors, warnings, items)
    errors = list(dict.fromkeys(errors))
    warnings = list(dict.fromkeys(warnings))
    if degraded_catalogs:
        ids = (degraded_catalogs if isinstance(degraded_catalogs, (list, tuple, set, frozenset))
               else [degraded_catalogs])
        _append_degraded_catalog_issues(state, ids, errors, items)
    return {'errors': errors, 'warnings': warnings, 'items': items}


def _append_degraded_catalog_issues(state, degraded_catalogs, errors, items):
    """目录缓存损坏的连接：逐条 error + connection 条目（03 分册 §2.2，fail-closed）。

    文案与 items 结构与路由层兜底 `_validate_with_degraded` 完全一致（B 落地后由本函数
    统一出报告，路由层不再自行追加）。绝不静默跳过：跳过会让用户在目录读取失败时
    把配置发布出去。
    """
    names = {str(c.get('id')): str(c.get('name') or c.get('id') or '')
             for c in (state.get('connections') or {}).get('connections') or []
             if isinstance(c, dict)}
    for cid in degraded_catalogs:
        label = names.get(str(cid)) or str(cid)
        text = (f'数据连接 {label}：目录缓存读取失败（缓存内容损坏），'
                f'无法核对表与字段；请重新刷新该连接的目录后再发布。')
        if text not in errors:
            errors.append(text)
        items.append({'kind': 'connection', 'id': str(cid),
                      'name': f'数据连接 · {label}',
                      'status': 'invalid', 'issues': [text]})


def _check_mapping_descriptions(ctx, errors, warnings, items):
    """项目说明检查（2026-09-19）：结构与失效引用（阻断发布、定位到说明项）；
    「只有说明、未形成可执行配置」给 warning（不算映射完成，不伪装成语义校验）。"""
    from workbench import mapping_descriptions
    block = ctx['bindings'].get('mappingDescriptions')
    if block is None:
        return
    try:
        mapping_descriptions.normalize_block(block)
    except ValueError as exc:
        errors.append('项目说明结构无效：' + str(exc))
        items.append({'kind': 'mappingDescription', 'id': 'mappingDescriptions', 'name': '项目说明',
                      'status': 'error', 'issues': [str(exc)]})
        return
    for ref in mapping_descriptions.stale_references(ctx['state'], ctx['ontology_state']):
        where = ref['id'] + ((' / ' + ref['item_id']) if ref['item_id'] else '')
        text = '项目说明指向的本体元素不存在：' + where + '（' + ref['text'] + '）。请修复或清除该条说明。'
        errors.append(text)
        items.append({'kind': 'mappingDescription', 'id': where, 'name': mapping_descriptions.KIND_LABELS[ref['kind']],
                      'status': 'error', 'issues': [text]})
    if mapping_descriptions.plain_description_only(ctx['state']):
        warnings.append('项目说明尚未形成可执行配置：只有说明不代表映射完成，可执行取值仍需配置数据来源或接口。')


def _check_connections(ctx, errors, items):
    connections = ctx['connections']
    seen_conn_names = set()
    for conn in connections:
        cid = str(conn.get('id', '') or '')
        cname = str(conn.get('name', '')).strip()
        label = cname or cid or '未命名'
        issues = []
        if conn.get('adapter'):
            pass  # legacy json_fixture connections are accepted as-is (design doc §8)
        elif not cname:
            issues.append('数据连接名称未填写')
        elif cname.casefold() in seen_conn_names:
            issues.append(f'数据连接名称重复：{cname}')
        else:
            seen_conn_names.add(cname.casefold())
        if not conn.get('adapter') and conn.get('engine') not in ('mysql', 'redis'):
            issues.append('连接引擎仅支持 MySQL 与 Redis')
        errors.extend(f'数据连接 {label}：{i}' for i in issues)
        items.append({'kind': 'connection', 'id': cid, 'name': f'数据连接 · {label}',
                      'status': 'invalid' if issues else 'valid', 'issues': issues})


def _check_object_bindings(ctx, errors, warnings, items):
    object_bindings = ctx['object_bindings']
    bare_classes = ctx['bare_classes']
    conn_by_id = ctx['conn_by_id']
    catalogs = ctx['catalogs']
    mapped_types = ctx['mapped_types']
    if len({b.get('object_type') for b in object_bindings}) != len(object_bindings):
        errors.append('同一对象类型重复配置数据映射')
    for b in object_bindings:
        name = b.get('object_type', '')
        issues = []
        if name not in bare_classes:
            issues.append('引用的版本中不存在此对象类型')
        # identity 分支（任务 B）：registered 不要求 connection/table/primary_key；
        # 无 identity 块 = 旧数据库身份，原校验原样；块存在但 kind 未知 → error 并保留数据。
        ikind = identity_kind(b)
        if ikind is None:
            issues.append('未知的实例来源方式')
        elif ikind != 'registered':
            if not str(b.get('table', '')).strip():
                issues.append('未配置来源数据表')
            if not str(b.get('primary_key', '')).strip():
                issues.append('未配置实例主键字段')
        if b.get('title_key') and b.get('title_key') not in b.get('properties', {}):
            warnings.append(f'对象映射 {name}：本体标记的显示名称属性尚未绑定数据字段，实例列表将显示为 ID')
        if ikind == 'registered':
            pass  # 登记身份不经过数据库连接要求；实例列表在下方 registeredIdentity 条目单独校验
        elif not str(b.get('connection', '') or '').strip():
            issues.append('未配置实例来源数据连接')
        elif b.get('connection') not in conn_by_id:
            issues.append('实例来源引用的数据连接不存在')
        if issues and all('未配置' in i for i in issues) and not b.get('properties') and not b.get('relations'):
            status = 'unconfigured'
        elif issues:
            status = 'invalid'
        else:
            status = 'valid'
        errors.extend(f'对象映射 {name}：{i}' for i in issues)
        mapped_types[name] = b
        items.append({'kind': 'objectBinding', 'id': name, 'name': f'对象映射 · {name}', 'status': status, 'issues': issues})

        # 登记实例校验（registered 身份）：独立条目，不与数据库身份要求混排。
        if ikind == 'registered':
            id_issues = _registered_identity_issues(b)
            errors.extend(f'实例登记 {name}：{i}' for i in id_issues)
            items.append({'kind': 'registeredIdentity', 'id': f'{name}.identity',
                          'name': f'实例登记 · {name}',
                          'status': 'invalid' if id_issues else 'valid', 'issues': id_issues})

        # Supplementary sources: new `sources` list merged with legacy `related_sources` by id.
        sources = sources_of(b)
        id_issues = {}
        for key in ('sources', 'related_sources'):
            seen = set()
            for source in b.get(key) or []:
                if not isinstance(source, dict):
                    errors.append(f'数据来源 {name}：来源配置格式无效')
                    continue
                sid = source.get('id')
                if not sid:
                    errors.append(f'数据来源 {name}：来源标识缺失')
                elif sid in seen:
                    id_issues.setdefault(sid, []).append('来源标识重复')
                seen.add(sid)
        for sid, source in sources.items():
            raw_name = str(source.get('name', '') or '').strip()
            sname = raw_name or str(sid)
            sissues = list(id_issues.get(sid, []))
            if not raw_name:
                sissues.append('来源名称未填写')
            conn_id = str(source.get('connection', '') or '').strip()
            conn = conn_by_id.get(source.get('connection')) if conn_id else None
            # Legacy related sources inherit the binding's connection; when the binding
            # itself has none (old adapter-era data) the requirement is waived instead
            # of blocking a configuration the previous validator accepted.
            if not conn_id and not source.get('legacy'):
                sissues.append('来源未绑定数据连接')
            elif conn is None and (conn_id or not source.get('legacy')):
                sissues.append('来源引用的数据连接不存在')
            if source.get('kind') == 'db':
                # Engine checks target new-style connections; adapter connections have none.
                if conn is not None and conn.get('engine') and conn.get('engine') != 'mysql':
                    sissues.append('数据库来源需要 MySQL 连接')
                for field, title in (('table', '表／视图'), ('matchLeft', '实例来源匹配字段'), ('matchRight', '目标匹配字段')):
                    if not str(source.get(field, '') or '').strip():
                        sissues.append('来源未填写' + title)
                if source.get('cardinality') not in (None, '', 'one', 'many'):
                    sissues.append('来源匹配数量仅支持一条（one）或多条（many）')
                # Stale-catalog hints: UI 只允许从目录选择；目录过期时给出刷新提示而非静默通过。
                src_tables = catalog_tables(catalogs, conn_id)
                st_name = str(source.get('table', '') or '')
                if src_tables is not None and st_name:
                    if st_name not in src_tables:
                        warnings.append(f'数据来源 {name}/{sname}：来源表「{st_name}」不在当前表结构目录中，请刷新表结构')
                    else:
                        mr = str(source.get('matchRight', '') or '')
                        if mr and mr not in (catalog_fields(src_tables, st_name) or set()):
                            warnings.append(f'数据来源 {name}/{sname}：匹配字段「{mr}」不在表「{st_name}」的目录中，请刷新表结构')
                id_tables = catalog_tables(catalogs, str(b.get('connection', '') or ''))
                id_table_name = str(b.get('table', '') or '')
                ml = str(source.get('matchLeft', '') or '')
                if id_tables is not None and id_table_name in (id_tables or {}) and ml:
                    if ml not in (catalog_fields(id_tables, id_table_name) or set()):
                        warnings.append(f'数据来源 {name}/{sname}：实例来源匹配字段「{ml}」不在表「{id_table_name}」的目录中，请刷新表结构')
            elif source.get('kind') == 'redis':
                if conn is not None and conn.get('engine') and conn.get('engine') != 'redis':
                    sissues.append('Redis 来源需要 Redis 连接')
            else:
                sissues.append('来源类型无效')
            # Cross-connection sources may be registered, but cross-database joins
            # are not executed in this round; flag instead of pretending.
            if conn_id and str(b.get('connection', '') or '').strip() and conn_id != str(b.get('connection')):
                warnings.append(f'来源「{sname}」与实例来源不在同一数据连接：跨库联合查询未在本轮实现，仅登记对应关系')
            errors.extend(f'数据来源 {name}/{sname}：{i}' for i in sissues)
            items.append({'kind': 'objectSource', 'id': f'{name}.{sid}', 'name': f'数据来源 · {name}/{sname}',
                          'status': 'invalid' if sissues else 'valid', 'issues': sissues})

        # Stale-catalog hints for the identity source itself.
        id_tables = catalog_tables(catalogs, str(b.get('connection', '') or ''))
        id_table_name = str(b.get('table', '') or '')
        if id_tables is not None and id_table_name:
            if id_table_name not in id_tables:
                warnings.append(f'对象映射 {name}：来源表「{id_table_name}」不在当前表结构目录中，请刷新表结构')
            else:
                pk = str(b.get('primary_key', '') or '')
                if pk and pk not in (catalog_fields(id_tables, id_table_name) or set()):
                    warnings.append(f'对象映射 {name}：实例主键「{pk}」不在表「{id_table_name}」的目录中，请刷新表结构')


def _registered_identity_issues(binding):
    """registered 身份的登记实例校验（规则 1）：非空列表、编号唯一且为合规文本、label 可空文本。"""
    issues = []
    identity = binding.get('identity')
    instances = identity.get('instances') if isinstance(identity, dict) else None
    if not isinstance(instances, list) or not instances:
        return ['未配置登记实例']
    seen = set()
    for inst in instances:
        if not isinstance(inst, dict):
            issues.append('登记实例格式无效')
            continue
        iid = inst.get('id')
        if not isinstance(iid, str) or not iid.strip():
            issues.append('实例编号未填写')
        elif len(iid) > IDENTITY_TEXT_LIMIT:
            issues.append('实例编号长度不能超过 128 字符')
        elif '\n' in iid or '\r' in iid:
            issues.append('实例编号不能包含换行')
        elif iid in seen:
            issues.append(f'实例编号重复：{iid}')
        else:
            seen.add(iid)
        label = inst.get('label')
        if label is not None and not isinstance(label, str):
            issues.append('实例显示名称必须是文本')
        elif isinstance(label, str) and len(label) > IDENTITY_TEXT_LIMIT:
            issues.append('实例显示名称长度不能超过 128 字符')
    return issues


def _check_implementations(ctx, errors, items):
    functions = ctx['functions']
    classes = ctx['classes']
    mapped_types = ctx['mapped_types']
    implementations = ctx['implementations']
    for impl in ctx['implementation_list']:
        if not isinstance(impl, dict):
            continue
        if is_query_rule(impl):
            issues = query_rule_errors(impl, ctx)
            label = impl.get('name') or '未命名规则'
            errors.extend(f'取值规则 {label}：{issue}' for issue in issues)
            items.append({'kind': 'implementation', 'id': impl.get('id', ''), 'name': f'取值规则 · {label}', 'status': 'invalid' if issues else 'valid', 'issues': issues})
            continue
        if calc_functions.is_calc_function(impl):
            issues = calc_functions.check_function(impl)
            label = impl.get('name') or '未命名计算函数'
            errors.extend(f'计算函数 {label}：{issue}' for issue in issues)
            items.append({'kind': 'implementation', 'id': impl.get('id', ''), 'name': f'计算函数 · {label}', 'status': 'invalid' if issues else 'valid', 'issues': issues})
            continue
        contract = functions.get(impl.get('contractId'))
        label = impl.get('name') or impl.get('contractId', '')
        issues = []
        if not contract or not is_contract(contract):
            issues.append('引用的计算契约不存在，或尚未迁移为通用契约')
            status = 'invalid'
        else:
            label = f"{contract.get('name') or label}"
            bound = {b.get('inputId') for b in impl.get('inputBindings', []) if isinstance(b, dict)}
            for item in contract.get('inputs') or []:
                if item['id'] not in bound:
                    issues.append(f"输入「{item.get('name') or item['id']}」未绑定来源")
            declared = {d.get('outputId') for d in impl.get('outputDeclarations', []) if isinstance(d, dict)}
            for item in contract.get('outputs') or []:
                if item['id'] not in declared:
                    issues.append(f"输出「{item.get('name') or item['id']}」未声明返回")
            for binding in impl.get('inputBindings', []):
                source = binding.get('source') or {}
                kind = source.get('kind')
                if kind == 'object':
                    if source.get('objectType') not in classes:
                        issues.append('输入绑定的对象类型不存在')
                elif kind == 'field':
                    target = mapped_types.get(bare(source.get('objectType', '')))
                    if not target:
                        issues.append('输入绑定引用了未映射对象的数据字段')
                    else:
                        allowed = set(target.get('properties', {}))
                        if target.get('primary_key'):
                            allowed.add(target['primary_key'])
                        if target.get('title_key'):
                            allowed.add(target['title_key'])
                        if source.get('property') not in allowed:
                            issues.append('输入绑定引用的字段尚未在属性来源中配置')
                elif kind == 'constant':
                    if not str(source.get('value', '')).strip():
                        issues.append('常量输入没有取值')
                elif kind == 'output':
                    other = implementations.get(source.get('implementation'))
                    if not other:
                        issues.append('输入绑定引用的实现不存在')
                    else:
                        other_contract = functions.get(other.get('contractId'), {})
                        if not any(o.get('outputId') == source.get('outputId') for o in other.get('outputDeclarations', [])):
                            issues.append('输入绑定引用的输出未在该实现中声明')
                else:
                    issues.append('输入绑定来源无效')
            if not str(impl.get('rule', '')).strip() and not issues:
                issues.append('尚未填写项目处理规则')
            status = 'unconfigured' if issues == ['尚未填写项目处理规则'] else ('invalid' if issues else 'valid')
        errors.extend(f'计算实现 {label}：{i}' for i in issues if '不存在' in i or '未声明' in i or '无效' in i)
        items.append({'kind': 'implementation', 'id': impl.get('id', ''), 'name': f'计算实现 · {label}', 'status': status, 'issues': issues})


# 计算函数输出/参数类型 ↔ 本体属性 range 的相容集合（首期数值/文本/是-否）
_CALC_RANGE_COMPAT = {'number': ('double', 'decimal', 'integer'), 'string': ('string',), 'boolean': ('boolean',)}


def _calc_range_ok(calc_type, target_range):
    return target_range in _CALC_RANGE_COMPAT.get(calc_type, ())


def _check_calc_binding(value, impl, b, ot, prop, graph, node, shape, dep_edges):
    """computed/mode=calcFunction 绑定校验：输出存在、类型相容、输入绑定齐全合法。只读。"""
    blocking = []
    output = impl.get('output') if isinstance(impl.get('output'), dict) else {}
    if str(value.get('output', '') or '') != str(output.get('id', '')):
        blocking.append('计算函数输出不存在，请选择函数输出')
    if shape == 'timeSeries':
        blocking.append('计算函数输出为单值，不能绑定时间序列属性')
    elif node is not None:
        target = effective(node, graph).get('rdfs:range', {}).get('@id', '').replace('xsd:', '')
        if not _calc_range_ok(str(output.get('type', '')), target):
            blocking.append('计算函数输出类型与属性数据类型不匹配')
    entries = value.get('inputs') if isinstance(value.get('inputs'), dict) else {}
    declared = {str(i.get('id')): i for i in impl.get('inputs', []) if isinstance(i, dict)}
    for input_id, item in declared.items():
        name = str(item.get('name', '') or input_id)
        entry = entries.get(input_id)
        if not isinstance(entry, dict):
            blocking.append(f'计算函数输入「{name}」未绑定取值')
            continue
        source = entry.get('from')
        if source == 'property':
            ref = str(entry.get('property', '') or '')
            ref_node = property_node(graph, ot, ref) if ref else None
            if ref_node is None:
                blocking.append(f'计算函数输入「{name}」引用的属性不存在')
                continue
            if ref == prop:
                blocking.append('属性来源存在循环依赖')
                continue
            if value_shape(ref_node, graph) == 'timeSeries':
                blocking.append(f'计算函数输入「{name}」引用的属性「{ref}」是时间序列，不能作为输入')
                continue
            ref_range = effective(ref_node, graph).get('rdfs:range', {}).get('@id', '').replace('xsd:', '')
            if not _calc_range_ok(str(item.get('type', '')), ref_range):
                blocking.append(f'计算函数输入「{name}」引用的属性「{ref}」数据类型不匹配')
                continue
            if dep_edges is not None:
                dep_edges.setdefault((ot, prop), set()).add((ot, ref))
        elif source == 'constant':
            if not calc_functions.constant_value_ok(str(item.get('type', '')), entry.get('value')):
                blocking.append(f'计算函数输入「{name}」的固定值无效')
        else:
            blocking.append(f'计算函数输入「{name}」的绑定来源无效')
    for input_id in entries:
        if input_id not in declared:
            blocking.append(f'计算函数不存在输入 {input_id}，请重新绑定（函数签名可能已修改）')
    return blocking


# flow 类型 → 本体属性 xsd range 兼容表（与前端 PropertySources.FLOW_RANGE_COMPAT 镜像）；
# object 不可绑定属性；list 仅时间序列属性可绑（需元素为已声明字段的对象 + result 字段映射）。
_FLOW_RANGE_COMPAT = {'number': ('double', 'decimal', 'integer'), 'text': ('string',),
                      'boolean': ('boolean',), 'datetime': ('dateTime',)}


def _flow_constant_ok(flow_type, value):
    """编排输入固定值有效性（与前端 flowConstantOk 镜像）：0/false/空串是有效值。"""
    if value is None:
        return False
    if flow_type == 'number':
        if isinstance(value, bool):
            return False
        try:
            float(str(value).strip())
            return str(value).strip() != ''
        except (ValueError, TypeError):
            return False
    if flow_type == 'boolean':
        return value in (True, False, 'true', 'false')
    if flow_type == 'datetime':
        return str(value).strip() != ''
    return True


def _flow_check_context(ctx):
    """项目校验传给 flows.check_flow 的上下文（每次 validate_project 解析一次）。

    三态协议（B01/B02，2026-09-21，接口文档 03 §2.2 / 04 §2.2）：已知集合（含空集合）
    ≠ 未知（None，跳过）≠ 读取失败（_READ_FAILED 哨兵）。缓存保存的是**读取状态**，
    由 `_flow_struct_errors` 结合编排实际声明的依赖决定如何使用，先查哪个编排都
    不影响后查编排的结论。

    - connections：本项目数据连接元数据。校验目标总是确定项目，连接列表恒为已知
      集合（**包括明确为空**）——只有 ctx 里根本没有该键/类型异常才按未知处理；
    - credential_ids：本项目 API 凭据 id 集合，读取失败记 _READ_FAILED（仅当编排
      实际引用凭据时才阻断，见 `_flow_struct_errors`）；
    - llm_meta：账号 LLM 提供方元数据，读取失败同上；账号未配置任何提供方时为 []，
      由 `_flow_struct_errors` 按编排是否声明提供方决定是否传入。
    """
    resolved = ctx.get('flow_check_context')
    if resolved is not None:
        return resolved
    credential_ids = None
    project_id = str((ctx.get('state') or {}).get('projectId') or '')
    if project_id:
        try:
            from workbench import api_credentials
            credential_ids = api_credentials.ids(project_id)
        except Exception:  # noqa: BLE001 凭据目录不可读：记为读取失败，不得冒充「无上下文」
            credential_ids = _READ_FAILED
    try:
        from workbench import llm_providers
        llm_meta = llm_providers.list_metadata()
    except Exception:  # noqa: BLE001 提供方目录不可读：同上，按编排依赖决定是否阻断
        llm_meta = _READ_FAILED
    connections = ctx.get('connections')
    if not isinstance(connections, list):
        connections = None  # 确定项目的校验恒有列表；异常形态才按未知处理
    resolved = {'connections': connections,
                'credential_ids': credential_ids, 'llm_meta': llm_meta}
    ctx['flow_check_context'] = resolved
    return resolved


def _raw_id_present(impl, key):
    """`providerId`/`credentialId` 是否**已声明**——与检查器底层口径完全一致（C01，2026-09-21）。

    `flows._check_llm_provider` 与 `flow_http.implementation_issues` 都用
    `str(impl.get(key) or '')` 的真值判定「有没有引用」，不做 strip。适配层的声明判据
    必须与之一致：纯空白 ID（空格 / Tab / 换行）是**已声明但无效**——不得因「看起来
    没填」而跳过检查（否则目录读取失败时会 fail-open 放行发布，C01 反例）。
    只有键缺失、None、空字符串才算未声明；此处只读判断，不修改、不 trim 任何数据。
    """
    return bool(str((impl or {}).get(key) or '')) if isinstance(impl, dict) else False


def _flow_declares_provider(flow_state):
    """编排是否声明了 LLM 提供方（用于区分「账号还没配模型」与「引用的模型没了/读不到」）。

    判据按原始非空字符串（含纯空白 ID，与检查器一致），见 `_raw_id_present`。"""
    nodes = flow_state.get('nodes') if isinstance(flow_state, dict) else None
    for node in nodes or []:
        if isinstance(node, dict) and _raw_id_present(node.get('implementation'), 'providerId'):
            return True
    return False


def _flow_references_credential(flow_state):
    """编排是否有 HTTP 节点声明 API 凭据（决定凭据目录是否为其实际依赖）。

    HTTP 节点识别与 `flows._check_body` 的分派口径一致（`node.kind or impl.language`）；
    声明判据同 `_raw_id_present`（含纯空白 ID）。"""
    nodes = flow_state.get('nodes') if isinstance(flow_state, dict) else None
    for node in nodes or []:
        if not isinstance(node, dict):
            continue
        impl = node.get('implementation')
        kind = (node.get('kind') or (impl or {}).get('language')) if isinstance(impl, dict) else None
        if kind == 'http' and _raw_id_present(impl, 'credentialId'):
            return True
    return False


def _flow_struct_errors(flow_id, flow_state, ctx):
    """编排自身结构/配置错误（R02，2026-09-20；B01/B02 上下文修订，2026-09-21）：
    复用 flows.check_flow，不执行任何节点。

    返回 error 文案列表；同一次 validate_project 内按 flowId 缓存（同一编排被多属性/
    多绑定引用只查一次）。上下文三态语义：

    - 连接集合恒按已知（含 []）传入 → 引用不存在连接报「数据连接不存在或已被删除」；
    - 提供方/凭据目录读取失败时：编排**实际声明**该依赖（providerId / HTTP credentialId）
      → 追加一条「读取失败」阻断（与「不存在」区分，不回显异常原文），该项上下文以 None
      传入检查器（读取失败不得冒充「引用不存在」）；未声明 → 不因无关故障误伤；
    - 提供方列表为空按「账号尚未配置模型」处理：仅在编排显式声明 providerId 时传空列表
      （check_flow 报「尚未配置/引用失效」），否则 None 跳过——账号级引导状态不是编排
      结构错误；
    - 检查器自身抛错同样 fail-closed（阻断而非放行）。
    """
    cache = ctx.setdefault('flow_struct_cache', {})
    if flow_id in cache:
        return cache[flow_id]
    context = _flow_check_context(ctx)
    llm_meta = context['llm_meta']
    credential_ids = context['credential_ids']
    blocked = []
    if llm_meta is _READ_FAILED:
        llm_meta = None  # 读取失败不得冒充「引用不存在」，存在性判定改由下面的阻断承担
        if _flow_declares_provider(flow_state):
            blocked.append('LLM 提供方目录读取失败，暂不能校验编排引用的提供方；请稍后重试')
    elif llm_meta == [] and not _flow_declares_provider(flow_state):
        llm_meta = None
    if credential_ids is _READ_FAILED:
        if _flow_references_credential(flow_state):
            credential_ids = None
            blocked.append('API 凭据目录读取失败，暂不能校验编排引用的凭据；请稍后重试')
        else:
            credential_ids = None
    try:
        report = flows.check_flow(flow_state, context['connections'], llm_meta, credential_ids)
        cache[flow_id] = blocked + [str(e) for e in (report.get('errors') or []) if str(e)]
    except Exception as exc:  # noqa: BLE001 编排检查失败必须阻断，不得静默放行
        cache[flow_id] = blocked + [f'编排检查失败（{type(exc).__name__}），暂不能校验该引用']
    return cache[flow_id]


def _check_flow_binding(value, flow_state, b, ot, prop, graph, node, shape, dep_edges):
    """kind='flow' 函数编排取值校验：编排存在、输出存在且类型相容、输入绑定齐全合法。只读。

    flow_state 为 None 表示编排不存在或已删除（由调用方读取并缓存）。
    编排自身结构错误由调用方先经 `_flow_struct_errors` 单独判定并阻断；本函数只负责
    所选输出与输入绑定是否与当前编排签名一致。"""
    blocking = []
    if flow_state is None:
        return ['引用的函数编排不存在或已删除']
    declared_outs = [o for o in flow_state.get('outputs', []) if isinstance(o, dict)]
    selected = next((o for o in declared_outs if str(o.get('id', '')) == str(value.get('output', '') or '')), None)
    if not str(value.get('output', '') or ''):
        blocking.append('未选择编排输出')
    elif selected is None:
        blocking.append('编排输出不存在，请重新选择（编排签名可能已修改）')
    else:
        out_type = str((selected.get('type') or {}).get('type', '') if isinstance(selected.get('type'), dict) else '')
        type_decl = selected.get('type') if isinstance(selected.get('type'), dict) else {}
        if out_type == 'object':
            blocking.append('编排输出为对象，不能绑定属性')
        elif out_type == 'list':
            # 列表输出：标量属性拒绝；时间序列属性在元素为已声明字段的对象且配置
            # result 字段映射（取值字段=number、时间字段=datetime，稳定 id）后可绑。
            if shape != 'timeSeries':
                blocking.append('编排输出为列表，不能绑定标量属性')
            else:
                elem = type_decl.get('elementType') if isinstance(type_decl.get('elementType'), dict) else {}
                fields = [f for f in elem.get('fields', []) if isinstance(f, dict)] if elem.get('type') == 'object' else []
                if not fields:
                    blocking.append('列表输出的元素需为已声明字段的对象，才能绑定时间序列属性')
                else:
                    result = value.get('result') if isinstance(value.get('result'), dict) else {}
                    by_id = {str(f.get('id')): f for f in fields}

                    def field_type(fid):
                        f = by_id.get(str(fid or ''))
                        t = f.get('type') if isinstance(f, dict) else None
                        return str((t or {}).get('type', '')) if isinstance(t, dict) else ''

                    v_field = str(result.get('valueField', '') or '')
                    t_field = str(result.get('timestampField', '') or '')
                    if not v_field:
                        blocking.append('未选择取值字段')
                    elif v_field not in by_id:
                        blocking.append('取值字段不存在，请重新选择（编排签名可能已修改）')
                    elif field_type(v_field) != 'number':
                        blocking.append('取值字段不是数值类型')
                    if not t_field:
                        blocking.append('未选择时间字段')
                    elif t_field not in by_id:
                        blocking.append('时间字段不存在，请重新选择（编排签名可能已修改）')
                    elif field_type(t_field) != 'datetime':
                        blocking.append('时间字段不是日期时间类型')
        elif shape == 'timeSeries':
            blocking.append('函数编排输出为单值，不能绑定时间序列属性')
        elif node is not None:
            target = effective(node, graph).get('rdfs:range', {}).get('@id', '').replace('xsd:', '')
            if target not in _FLOW_RANGE_COMPAT.get(out_type, ()):
                blocking.append('编排输出类型与属性数据类型不匹配')
    entries = value.get('inputs') if isinstance(value.get('inputs'), dict) else {}
    declared = {str(i.get('id')): i for i in flow_state.get('inputs', []) if isinstance(i, dict)}
    identity_ready = identity_kind(b) == 'registered' or bool(str(b.get('primary_key', '') or '').strip())
    for input_id, item in declared.items():
        name = str(item.get('label') or item.get('name') or input_id)
        in_type = str((item.get('type') or {}).get('type', '') if isinstance(item.get('type'), dict) else '')
        entry = entries.get(input_id)
        if not isinstance(entry, dict):
            blocking.append(f'编排输入「{name}」未绑定取值')
            continue
        source = entry.get('from')
        if source == 'property':
            ref = str(entry.get('property', '') or '')
            ref_node = property_node(graph, ot, ref) if ref else None
            if ref_node is None:
                blocking.append(f'编排输入「{name}」引用的属性不存在')
                continue
            if ref == prop:
                blocking.append('属性来源存在循环依赖')
                continue
            if value_shape(ref_node, graph) == 'timeSeries':
                blocking.append(f'编排输入「{name}」引用的属性「{ref}」是时间序列，不能作为输入')
                continue
            ref_range = effective(ref_node, graph).get('rdfs:range', {}).get('@id', '').replace('xsd:', '')
            if ref_range not in _FLOW_RANGE_COMPAT.get(in_type, ()):
                blocking.append(f'编排输入「{name}」引用的属性「{ref}」数据类型不匹配')
                continue
            if dep_edges is not None:
                dep_edges.setdefault((ot, prop), set()).add((ot, ref))
        elif source == 'constant':
            if not _flow_constant_ok(in_type, entry.get('value')):
                blocking.append(f'编排输入「{name}」的固定值无效')
        elif source == 'instanceId':
            if in_type != 'text':
                blocking.append(f'编排输入「{name}」不是文本类型，不能绑定实例编号')
            elif not identity_ready:
                blocking.append('当前对象未配置实例身份，不能绑定实例编号')
        else:
            blocking.append(f'编排输入「{name}」的绑定来源无效')
    for input_id in entries:
        if input_id not in declared:
            blocking.append(f'编排不存在输入 {input_id}，请重新绑定（编排签名可能已修改）')
    return blocking


def _check_property_sources(ctx, errors, warnings, items):
    graph = ctx['graph']
    object_bindings = ctx['object_bindings']
    catalogs = ctx['catalogs']
    conn_by_id = ctx['conn_by_id']
    parameters = ctx['parameters']
    functions = ctx['functions']
    implementations = ctx['implementations']
    mapped_types = ctx['mapped_types']

    # 本对象内 database 属性经 match {kind:'property'} 形成依赖图，用于循环依赖检测（§3 规则 5）。
    dep_edges = {}
    # flowId → flows.dependency_state 三态结果（found/missing/unreadable），同一编排多属性引用共享缓存
    flow_deps = {}
    for b in object_bindings:
        dep_ot = b.get('object_type', '')
        dep_props = b.get('properties') or {}
        for dep_prop, dep_value in dep_props.items():
            dep_is_calc = (isinstance(dep_value, dict) and dep_value.get('kind') == 'computed'
                           and dep_value.get('mode') == 'calcFunction')
            if dep_is_calc:
                dep_fn = implementations.get(dep_value.get('implementation'))
                if calc_functions.is_calc_function(dep_fn):
                    dep_entries = dep_value.get('inputs') if isinstance(dep_value.get('inputs'), dict) else {}
                    for dep_input_id, dep_entry in dep_entries.items():
                        if not isinstance(dep_entry, dict) or dep_entry.get('from') != 'property':
                            continue
                        dep_ref = str(dep_entry.get('property', '') or '')
                        if dep_ref in dep_props and dep_ref != dep_prop:
                            dep_edges.setdefault((dep_ot, dep_prop), set()).add((dep_ot, dep_ref))
            dep_is_flow = isinstance(dep_value, dict) and dep_value.get('kind') == 'flow'
            if dep_is_flow:
                # 函数编排输入的属性引用同样构成依赖边（循环检测与计算函数同一套规则）。
                dep_entries = dep_value.get('inputs') if isinstance(dep_value.get('inputs'), dict) else {}
                for dep_input_id, dep_entry in dep_entries.items():
                    if not isinstance(dep_entry, dict) or dep_entry.get('from') != 'property':
                        continue
                    dep_ref = str(dep_entry.get('property', '') or '')
                    if dep_ref in dep_props and dep_ref != dep_prop:
                        dep_edges.setdefault((dep_ot, dep_prop), set()).add((dep_ot, dep_ref))
            if not (isinstance(dep_value, dict) and dep_value.get('kind') == 'database'):
                continue
            dep_lookup = dep_value.get('lookup') if isinstance(dep_value.get('lookup'), dict) else {}
            for dep_cond in dep_lookup.get('match') or []:
                if not isinstance(dep_cond, dict):
                    continue
                dep_ref = dep_cond.get('value') if isinstance(dep_cond.get('value'), dict) else {}
                if dep_ref.get('kind') == 'property' and dep_ref.get('property') in dep_props and dep_ref.get('property') != dep_prop:
                    dep_edges.setdefault((dep_ot, dep_prop), set()).add((dep_ot, dep_ref['property']))
    dep_cycle_props, visiting, done = set(), set(), set()

    def collect_dep_cycles(node, path):
        if node in visiting:
            dep_cycle_props.update(path[path.index(node):])
        elif node not in done:
            visiting.add(node)
            path.append(node)
            for child in dep_edges.get(node, ()):
                collect_dep_cycles(child, path)
            path.pop()
            visiting.remove(node)
            done.add(node)

    for dep_node in dep_edges:
        collect_dep_cycles(dep_node, [])

    for b in object_bindings:
        ot = b.get('object_type', '')
        properties = b.get('properties') or {}
        sources = sources_of(b)
        id_tables = catalog_tables(catalogs, str(b.get('connection', '') or ''))
        id_fields = catalog_fields(id_tables, str(b.get('table', '') or ''))
        # apiNames usable as redis key parameters: field-backed sources on this object
        # (legacy string values or field/related mappings with a non-empty field).
        field_backed = {api for api, value in properties.items()
                        if (isinstance(value, str) and str(value).strip())
                        or (isinstance(value, dict) and value.get('kind') in ('field', 'related')
                            and str(value.get('field', '') or '').strip())}
        for prop, value in properties.items():
            blocking, pending = [], []
            kind = value.get('kind') if isinstance(value, dict) else ('string' if isinstance(value, str) else '?invalid')
            node = property_node(graph, ot, prop)
            shape = value_shape(node, graph) if node is not None else 'scalar'
            if node is None:
                # 属性键在引用的本体版本中不存在（被删除或改名）：该属性绑定已失效、阻断发布。
                # 原配置原样保留（只读校验），由用户在绑定处重新选择或确认移除。
                blocking.append('引用的版本中不存在此属性')
            # 目标形态 timeSeries 的属性只允许 database 或 computed（§3 规则 8）。
            if shape == 'timeSeries':
                if kind == 'redis':
                    blocking.append('Redis 普通读取不能作为时间序列来源')
                elif kind in ('string', 'field', 'related'):
                    blocking.append('时间序列属性需要数据库直选表的时间序列映射，或声明序列输出的函数结果')
                elif kind == 'registered':
                    blocking.append('时间序列属性不能绑定登记信息')
                elif kind == 'aggregate':
                    blocking.append('时间序列属性不能配置关联聚合')
            if kind == 'string':
                if not value.strip():
                    pending.append('未填写字段名')
            elif kind == '?invalid':
                blocking.append('属性来源配置格式无效')
            elif kind == 'database':
                conn_id = str(value.get('connection', '') or '').strip()
                table = str(value.get('table', '') or '').strip()
                conn = conn_by_id.get(conn_id) if conn_id else None
                if conn is None:
                    blocking.append('数据库取值引用的数据连接不存在')
                elif conn.get('engine') != 'mysql':
                    blocking.append('数据库取值需要 MySQL 连接')
                if not table:
                    pending.append('未选择数据表')
                lookup = value.get('lookup') if isinstance(value.get('lookup'), dict) else {}
                result = value.get('result') if isinstance(value.get('result'), dict) else {}
                time_range = lookup.get('timeRange') if isinstance(lookup.get('timeRange'), dict) else None
                tables = catalog_tables(catalogs, conn_id)
                fields = catalog_fields(tables, table)
                field_meta = catalog_field_meta(tables, table)
                if conn is not None and table and tables is None:
                    warnings.append(f'属性来源 {ot}.{prop}：表结构目录未读取或已过期，元数据待核对')
                elif table and tables is not None and table not in tables:
                    warnings.append(f'属性来源 {ot}.{prop}：表「{table}」不在当前表结构目录中，请刷新表结构')

                def field_warn(label, field_name):
                    if field_name and fields and field_name not in fields:
                        warnings.append(f'属性来源 {ot}.{prop}：{label}「{field_name}」不在表「{table}」的目录中，请刷新表结构')

                def check_timezone(res):
                    tz = str(res.get('timezone', '') or '')
                    if not tz:
                        blocking.append('datetime 时间编码必须填写来源时区')
                        return
                    try:
                        zoneinfo.ZoneInfo(tz)
                    except (ValueError, KeyError, OSError, ImportError):
                        blocking.append('时区不是可识别的 IANA 时区名称')

                def check_timestamp_field(res, label):
                    ts_field = str(res.get('timestampField', '') or '')
                    if not ts_field:
                        blocking.append(f'{label}必须选择时间字段')
                        return
                    encoding = res.get('timestampEncoding')
                    if encoding not in ('datetime', 'epochSeconds', 'epochMillis'):
                        blocking.append('时间编码仅支持 datetime／epochSeconds／epochMillis')
                        return
                    ts_type = field_type(field_meta.get(ts_field))
                    if ts_type:
                        if encoding == 'datetime' and ts_type not in DATETIME_FIELD_TYPES:
                            blocking.append(f'时间字段「{ts_field}」的类型与 datetime 编码不兼容')
                        elif encoding != 'datetime' and ts_type not in NUMERIC_FIELD_TYPES:
                            blocking.append(f'时间字段「{ts_field}」的类型与数值时间戳编码不兼容')
                    if encoding == 'datetime':
                        check_timezone(res)

                value_field = str(result.get('valueField', '') or '')
                if not value_field:
                    blocking.append('未选择值字段')
                else:
                    field_warn('值字段', value_field)
                binds_instance = False
                for cond in lookup.get('match') or []:
                    if not isinstance(cond, dict):
                        blocking.append('匹配条件配置格式无效')
                        continue
                    m_field = str(cond.get('field', '') or '')
                    if not m_field:
                        blocking.append('匹配条件未选择来源字段')
                    else:
                        field_warn('匹配字段', m_field)
                    if cond.get('operator') != 'eq':
                        blocking.append('匹配条件操作符仅支持等于（eq）')
                    m_value = cond.get('value') if isinstance(cond.get('value'), dict) else {}
                    v_kind = m_value.get('kind')
                    if v_kind == 'identityKey':
                        binds_instance = True
                        if not str(b.get('primary_key', '') or '').strip():
                            blocking.append('使用当前实例主键匹配，但对象映射未配置实例主键字段')
                    elif v_kind == 'identityField':
                        binds_instance = True
                        identity_field = str(m_value.get('field', '') or '')
                        if not identity_field:
                            blocking.append('匹配条件的身份表字段未填写')
                        elif id_fields is not None and identity_field not in id_fields:
                            warnings.append(f'属性来源 {ot}.{prop}：身份表字段「{identity_field}」不在表「{b.get("table", "")}」的目录中，请刷新表结构')
                    elif v_kind == 'property':
                        ref_prop = str(m_value.get('property', '') or '')
                        if not ref_prop:
                            blocking.append('匹配条件引用的属性未填写')
                        elif ref_prop == prop:
                            blocking.append('匹配条件不能引用属性自身')
                        elif ref_prop not in properties:
                            blocking.append('匹配条件引用的属性尚未配置数据映射')
                        else:
                            ref_node = property_node(graph, ot, ref_prop)
                            if ref_node is not None and value_shape(ref_node, graph) != 'scalar':
                                blocking.append('匹配条件引用的属性必须是单值属性')
                    elif v_kind == 'constant':
                        c_value = m_value.get('value')
                        if c_value is None or not str(c_value).strip():
                            blocking.append('匹配条件的常量取值未填写')
                        else:
                            f_type = field_type(field_meta.get(m_field))
                            if f_type in NUMERIC_FIELD_TYPES:
                                try:
                                    number = float(str(c_value))
                                except ValueError:
                                    number = None
                                if number is None or (f_type in INT_FIELD_TYPES and not number.is_integer()):
                                    blocking.append('常量取值与字段类型不兼容')
                    elif v_kind == 'parameter':
                        if str(m_value.get('parameter', '') or '') not in parameters:
                            blocking.append('匹配条件引用的项目参数不存在')
                    else:
                        blocking.append('匹配条件比较值来源无效')
                # 至少一条 match 绑定当前实例；身份表本行（同连接同表）自动按主键定位（§3 规则 4）。
                identity_row = (bool(conn_id) and bool(table)
                                and conn_id == str(b.get('connection', '') or '').strip()
                                and table == str(b.get('table', '') or '').strip())
                if not binds_instance and not identity_row:
                    blocking.append('匹配条件至少一条需要绑定当前实例（当前实例主键或身份表字段）')
                if time_range is not None:
                    if shape != 'timeSeries':
                        blocking.append('单值属性不能映射时间序列结果')
                    else:
                        range_field = str(time_range.get('field', '') or '')
                        if not range_field:
                            blocking.append('时间范围未选择时间字段')
                        else:
                            field_warn('时间范围字段', range_field)
                        range_start = time_range.get('start') if isinstance(time_range.get('start'), dict) else {}
                        range_end = time_range.get('end') if isinstance(time_range.get('end'), dict) else {}
                        if range_start != {'kind': 'context', 'name': 'startTime'} or range_end != {'kind': 'context', 'name': 'endTime'}:
                            blocking.append('时间范围起止必须是调用上下文 startTime／endTime')
                        if time_range.get('bounds') != '[start,end)':
                            blocking.append('时间范围边界只支持 [start,end)')
                if shape == 'timeSeries':
                    check_timestamp_field(result, '时间序列结果')
                    if str(result.get('selection') or '').strip():
                        blocking.append('时间序列属性不支持聚合取值规则')
                    if result.get('missing') not in (None, '', 'null'):
                        blocking.append('时间序列属性不支持无记录策略配置（无记录返回空列表）')
                    if (result.get('order') or 'ascending') not in ('ascending', 'descending'):
                        blocking.append('排序方式仅支持升序（ascending）或降序（descending）')
                    duplicate = result.get('duplicateTimestamp') or 'error'
                    if duplicate not in ('error', 'secondarySort'):
                        blocking.append('重复时刻处理仅支持报错（error）或次级排序（secondarySort）')
                    elif duplicate == 'secondarySort':
                        secondary = result.get('secondarySort') if isinstance(result.get('secondarySort'), dict) else {}
                        if not str(secondary.get('field', '') or '').strip():
                            blocking.append('次级排序必须选择排序字段')
                        else:
                            field_warn('次级排序字段', str(secondary.get('field', '') or ''))
                            if (secondary.get('order') or 'ascending') not in ('ascending', 'descending'):
                                blocking.append('次级排序方式仅支持升序或降序')
                else:
                    selection = result.get('selection') or ''
                    if selection not in ('', 'latest', 'sum', 'average'):
                        blocking.append('多行处理方式仅支持取最新／求和／求平均')
                        selection = ''
                    series_keys = [k for k in ('order', 'duplicateTimestamp') if result.get(k)]
                    if selection != 'latest':
                        series_keys += [k for k in ('timestampField', 'timestampEncoding', 'timezone', 'secondarySort') if result.get(k)]
                    if series_keys:
                        blocking.append('单值属性不能映射时间序列结果')
                    if selection == 'latest':
                        check_timestamp_field(result, '取最新记录')
                        secondary = result.get('secondarySort') if isinstance(result.get('secondarySort'), dict) else None
                        if secondary is None or not str(secondary.get('field', '') or '').strip():
                            warnings.append(f'属性来源 {ot}.{prop}：取最新记录未配置并列时点的次级排序字段，接入执行时需明确唯一记录')
                        else:
                            field_warn('次级排序字段', str(secondary.get('field', '') or ''))
                            if (secondary.get('order') or 'ascending') not in ('ascending', 'descending'):
                                blocking.append('次级排序方式仅支持升序或降序')
                    elif selection in ('sum', 'average') and value_field in field_meta:
                        if field_type(field_meta[value_field]) not in NUMERIC_FIELD_TYPES:
                            blocking.append('汇总取值只适用于数值字段，请核对业务口径')
                    if result.get('missing') not in (None, '', 'null', 'error'):
                        blocking.append('缺失处理策略无效')
            elif kind == 'computed':
                if value.get('mode') == 'inlineSql':
                    # 属性内 SQL 取值（方案 §5-6）：匿名内联模板，与命名规则互斥；只校验配置，
                    # 不执行 SQL，也不进入演示执行器（merged() 过滤一切 dict 来源）。
                    if any(k in value for k in ('implementation', 'output', 'inputs')):
                        blocking.append('内联 SQL 与命名规则取值不能同时配置')
                    inline = value.get('inlineSql')
                    if not isinstance(inline, dict):
                        blocking.append('内联 SQL 结构无效')
                    elif inline.get('version') != 1:
                        blocking.append('内联 SQL 版本不受支持，配置已原样保留')
                    else:
                        inline_conn = str(inline.get('connection', '') or '').strip()
                        conn = conn_by_id.get(inline_conn) if inline_conn else None
                        if conn is None:
                            blocking.append('内联 SQL 引用的数据连接不存在')
                        elif conn.get('engine') != 'mysql':
                            blocking.append('内联 SQL 需要 MySQL 连接')
                        sql = inline.get('sql')
                        if not isinstance(sql, str) or not sql.strip():
                            pending.append('未填写 SQL 模板')
                        else:
                            code = strip_sql_noise_mysql(sql)
                            if re.search(r'\{\{[^{}]*\}\}', code):
                                blocking.append('内联 SQL 不支持动态表名／字段名与步骤引用，请改用「引用已有规则」方式')
                            if not re.match(r'^\s*SELECT\b', code, re.I):
                                blocking.append('内联 SQL 不支持 WITH 开头语句，请改用「引用已有规则」方式'
                                                if re.match(r'^\s*WITH\b', code, re.I) else '内联 SQL 须以 SELECT 开始')
                            if len([s for s in code.split(';') if s.strip()]) != 1:
                                blocking.append('内联 SQL 只能填写一条 SELECT')
                            identity_ready = identity_kind(b) == 'registered' or bool(str(b.get('primary_key', '') or '').strip())
                            inline_params = inline.get('params') if isinstance(inline.get('params'), dict) else {}
                            for name in scan_sql_params(sql):
                                target = inline_params.get(name)
                                if not isinstance(target, dict):
                                    blocking.append(f'参数 :{name} 未绑定取值')
                                    continue
                                source = target.get('from')
                                if source == 'constant':
                                    if target.get('dataType') not in ('string', 'double', 'boolean', 'dateTime'):
                                        blocking.append(f'参数 :{name} 的常量数据类型无效')
                                    elif not _inline_constant_ok(target.get('dataType'), target.get('value')):
                                        blocking.append(f'参数 :{name} 的常量值无效')
                                elif source == 'projectParameter':
                                    key = str(target.get('key', '') or '').strip()
                                    if not key or key not in parameters:
                                        blocking.append(f'参数 :{name} 引用的项目参数「{key or "未选择"}」不存在或已失效')
                                elif source == 'instanceId':
                                    if not identity_ready:
                                        blocking.append(f'当前对象未配置实例身份，不能绑定实例编号参数 :{name}')
                                else:
                                    blocking.append(f'参数 :{name} 的绑定来源无效')
                else:
                    impl = implementations.get(value.get('implementation'))
                    contract = None
                    if not impl:
                        blocking.append('引用的计算实现不存在')
                    elif is_query_rule(impl):
                        if impl.get('schemaVersion') in (2, 3, 4):
                            blocking.extend(rule_input_errors(value.get('inputs'), b, impl))
                        elif impl.get('objectType') != ot:
                            blocking.append('取值规则的适用对象与当前对象不一致')
                        if value.get('output') != 'series':
                            blocking.append('取值规则输出不存在，请选择采样时间序列')
                        if impl.get('schemaVersion') in (3, 4):
                            result = impl.get('result', {})
                            if shape != result.get('type'):
                                blocking.append('取值规则返回类型与属性要求不匹配')
                            if node:
                                target = effective(node, graph).get('rdfs:range', {}).get('@id', '').replace('xsd:', '')
                                actual = result.get('valueType')
                                if target != actual and not (target in ('double','decimal','integer') and actual == 'double'):
                                    blocking.append('取值规则值类型与属性数据类型不匹配')
                        else:
                            if shape != 'timeSeries':
                                blocking.append('取值规则输出为时间序列，不能绑定单值属性')
                            elif node and effective(node, graph).get('rdfs:range', {}).get('@id') not in ('xsd:double', 'xsd:decimal', 'xsd:integer'):
                                blocking.append('取值规则输出为数值时间序列，属性观测值类型不匹配')
                        if query_rule_errors(impl, ctx):
                            blocking.append('所选取值规则配置不完整，请到属性取值规则页处理')
                    elif calc_functions.is_calc_function(impl):
                        blocking.extend(_check_calc_binding(value, impl, b, ot, prop, graph, node, shape, dep_edges))
                    else:
                        contract = functions.get(impl.get('contractId'))
                        if not contract or not is_contract(contract):
                            blocking.append('实现引用的契约不存在')
                        elif not any((o.get('outputId') == value.get('output') for o in impl.get('outputDeclarations', []))):
                            blocking.append('所选输出未在该实现中声明')
                        else:
                            # 输出声明形态缺省 scalar，与目标属性形态不一致即 block（§2.5）。
                            declaration = next((d for d in impl.get('outputDeclarations', [])
                                                if isinstance(d, dict) and d.get('outputId') == value.get('output')), {})
                            output = next((o for o in contract.get('outputs', []) if o.get('id') == value.get('output')), {})
                            dtype = signature_data_type(output.get('ref'), graph, declaration) or {}
                            if ('timeSeries' if dtype.get('type') == 'timeSeries' else 'scalar') != shape:
                                blocking.append('函数输出数据类型与属性要求不匹配')
                    if node and contract:
                        output = next((o for o in contract.get('outputs', []) if o.get('id') == value.get('output')), None)
                        ref = (output or {}).get('ref') or {}
                        if ref.get('kind') == 'property' and ref.get('id') not in (node.get('mg:sharedProperty', {}).get('@id'), node['@id']):
                            warnings.append(f'属性 {prop} 的计算输出指向另一个属性定义，请核对业务口径')
            elif kind == 'flow':
                # 函数编排取值（2026-09-18）：只读引用编排工作区的 flowId + 输出 + 输入绑定。
                # 依赖读取三态（2026-09-20 v2 冻结，见 03 分册 §2.2）：按 flowId 缓存
                # flows.dependency_state —— missing 沿用「不存在或已删除」；unreadable
                # 一律 fail-closed 报 error（绝不与「不存在」混同、也不跳过该检查后放行）；
                # found 用其 state。软删除（status=deleted）按不存在处理。
                flow_id = str(value.get('flow', '') or '').strip()
                if not flow_id:
                    blocking.append('未选择函数编排')
                else:
                    if flow_id not in flow_deps:
                        flow_deps[flow_id] = flows.dependency_state(flow_id)
                    dep = flow_deps[flow_id]
                    dep_status = dep.get('status')
                    flow_state = dep.get('state') if dep_status == 'found' else None
                    if isinstance(flow_state, dict) and flow_state.get('status') == 'deleted':
                        flow_state = None     # 软删除按不存在处理（文案与既有行为一致）
                        dep_status = 'missing'
                    if dep_status == 'unreadable':
                        reason = str(dep.get('reason') or '读取失败')
                        blocking.append(f'引用的函数编排 {flow_id}：读取失败（{reason}），'
                                        f'暂不能校验；请稍后重试')
                    else:
                        # 编排自身结构/配置错误（复用 check_flow，不运行节点；R02 2026-09-20）：
                        # 空壳、输出未绑定、节点实现失效等让该属性取值引用不可用，必须阻断发布。
                        binding_blocking = _check_flow_binding(value, flow_state, b, ot, prop,
                                                               graph, node, shape, dep_edges)
                        struct_errors = _flow_struct_errors(flow_id, flow_state, ctx)
                        blocking.extend(f'引用的函数编排：{e}' for e in struct_errors)
                        blocking.extend(binding_blocking)
            elif kind in ('field', 'related'):
                source_id = str(value.get('source', '') or '')
                source = sources.get(source_id) if source_id else None
                if source_id and source is None:
                    blocking.append('属性引用的数据来源不存在')
                field_name = str(value.get('field', '') or '')
                if not field_name.strip():
                    pending.append('未填写字段名')
                elif not source_id or source is not None:
                    # 有目录（该表字段目录可读）时字段必须命中；目录缺失/未刷新（None）保持现状不报
                    # （表级过期提示由数据来源/对象映射分支给出）。换表后原配置保留，新表若恰好
                    # 有同名字段也不自动确认——仍按目录逐项核对。
                    if source is not None:
                        src_conn = str(source.get('connection') or b.get('connection', '') or '')
                        src_table = str(source.get('table') or '')
                    else:
                        src_conn = str(b.get('connection', '') or '')
                        src_table = str(b.get('table', '') or '')
                    src_fields = catalog_fields(catalog_tables(catalogs, src_conn), src_table)
                    if src_fields is not None and field_name not in src_fields:
                        blocking.append(f'引用的字段「{field_name}」不在表「{src_table}」的目录中，请重新选择字段')
                if source is not None and source.get('cardinality') == 'many':
                    selection = value.get('selection')
                    if selection not in ('latest', 'sum', 'average'):
                        blocking.append('多条匹配来源必须选择取值规则（取最新／求和／求平均）')
                    elif selection == 'latest':
                        if not str(value.get('timeField', '') or '').strip():
                            blocking.append('取最新记录必须指定排序时间字段')
                        elif not str(value.get('tieBreaker', '') or '').strip():
                            warnings.append(f'属性来源 {ot}.{prop}：取最新记录未配置并列时点的次级排序字段，接入执行时需明确唯一记录')
                    else:
                        if node is not None and effective(node, graph).get('rdfs:range', {}).get('@id') not in ('xsd:double', 'xsd:decimal', 'xsd:integer'):
                            blocking.append('汇总取值只适用于数值属性，请核对业务口径')
            elif kind == 'redis':
                source_id = str(value.get('source', '') or '')
                direct_id = str(value.get('connection', '') or '')
                source = None
                if bool(source_id) == bool(direct_id):
                    # source 与 connection 互斥：都空或都非空均无效（§2.3）。
                    blocking.append('Redis 来源配置无效：登记来源与直连连接只能选其一')
                elif direct_id:
                    direct = conn_by_id.get(direct_id)
                    if direct is None:
                        blocking.append('Redis 直连引用的数据连接不存在')
                    elif direct.get('engine') != 'redis':
                        blocking.append('Redis 直连需要 Redis 连接')
                else:
                    source = sources.get(source_id)
                    if source is None:
                        blocking.append('属性引用的数据来源不存在')
                    elif source.get('kind') != 'redis':
                        blocking.append('Redis 查询必须选择 Redis 数据来源')
                key = str(value.get('key', '') or '')
                if not key.strip():
                    pending.append('未填写 Key 模板')
                command = value.get('command') or 'GET'
                if command not in ('GET', 'HGET'):
                    blocking.append('读取方式仅支持 GET 与 HGET')
                if command == 'HGET' and not str(value.get('hashField', '') or '').strip():
                    pending.append('HGET 读取必须填写 Hash 字段')
                params = value.get('params') if isinstance(value.get('params'), dict) else {}
                for token in KEY_TOKEN.findall(key):
                    if token not in params:
                        blocking.append(f'Key 模板占位符「{{{token}}}」未绑定参数')
                for token, target in params.items():
                    if not isinstance(target, dict) or target.get('from') not in ('primary', 'property', 'identityField'):
                        blocking.append(f'参数「{token}」绑定配置无效')
                    elif target.get('from') == 'property':
                        if target.get('property') == prop or target.get('property') not in field_backed:
                            blocking.append('参数绑定的属性不是有效的字段来源')
                    elif target.get('from') == 'identityField':
                        identity_field = str(target.get('field', '') or '')
                        if not identity_field:
                            blocking.append(f'参数「{token}」的身份表字段未填写')
                        elif id_fields is not None and identity_field not in id_fields:
                            warnings.append(f'属性来源 {ot}.{prop}：参数「{token}」的身份表字段「{identity_field}」不在表「{b.get("table", "")}」的目录中，请刷新表结构')
                if value.get('conversion') not in (None, '', 'number', 'integer', 'text'):
                    blocking.append('结果转换方式无效')
                if value.get('missing') not in (None, '', 'null', 'error'):
                    blocking.append('缺失处理策略无效')
            elif kind == 'registered':
                # 登记信息来源（任务 B 规则 2）：field ∈ {id,label}；目标属性须为文本兼容；
                # 只在实例来源为项目登记的对象上有意义。
                if identity_kind(b) != 'registered':
                    blocking.append('登记信息来源只适用于实例来源为项目登记的对象')
                if value.get('field') not in ('id', 'label'):
                    blocking.append('登记信息字段仅支持实例编号或显示名称')
                if node is not None and effective(node, graph).get('rdfs:range', {}).get('@id') != 'xsd:string':
                    blocking.append('登记信息只能绑定文本类型属性')
            elif kind == 'aggregate':
                # 关联聚合来源（任务 B 规则 3，A8）：SUM、一跳成员链接（按端判定，方向无关）、
                # 成员对象身份表直接数值字段，结构上拒绝自引用 / 聚合依赖聚合。
                if identity_kind(b) != 'registered':
                    blocking.append('关联聚合只适用于实例来源为项目登记的对象')
                if value.get('operator') != 'sum':
                    blocking.append('聚合方式本轮仅支持求和（sum）')
                if node is not None and effective(node, graph).get('rdfs:range', {}).get('@id') not in ('xsd:double', 'xsd:decimal', 'xsd:integer'):
                    blocking.append('聚合结果属性必须是数值属性')
                relation = str(value.get('relation', '') or '')
                rel_node = object_property_node(graph, relation) if relation else None
                target_type = ''
                if not relation:
                    blocking.append('未选择聚合链接')
                elif rel_node is None:
                    blocking.append('引用的版本中不存在此链接类型')
                else:
                    # 集合端按端判定（方向无关）：出向一对多/多对多，或入向多对一/多对多；成员=另一端。
                    member_type, _card, _orient = member_side_of(graph, ot, relation)
                    if not member_type:
                        blocking.append('聚合链接必须以当前对象为集合端（出向一对多／多对多，或入向多对一／多对多）')
                    target_type = member_type
                    member = str(value.get('property', '') or '')
                    if member_type:
                        member_node = property_node(graph, target_type, member) if member else None
                        if not member:
                            blocking.append('未选择聚合成员属性')
                        elif member_node is None:
                            blocking.append('聚合成员属性在终点对象中不存在')
                        else:
                            if value_shape(member_node, graph) != 'scalar':
                                blocking.append('聚合成员属性不能是时间序列')
                            if effective(member_node, graph).get('rdfs:range', {}).get('@id') not in ('xsd:double', 'xsd:decimal', 'xsd:integer'):
                                blocking.append('聚合成员属性必须是数值属性')
                        target_binding = mapped_types.get(target_type)
                        if target_binding is None:
                            blocking.append('聚合终点对象尚未配置数据映射')
                        else:
                            member_value = (target_binding.get('properties') or {}).get(member)
                            if isinstance(member_value, dict) and member_value.get('kind') == 'aggregate':
                                blocking.append('聚合成员属性不能是聚合结果，不支持递归聚合')
                            direct = identity_field_mapping(target_binding, member) if member else None
                            if not direct:
                                blocking.append('成员属性不是身份表直接数值字段，本轮聚合不支持')
                            else:
                                t_tables = catalog_tables(catalogs, str(target_binding.get('connection', '') or ''))
                                t_meta = catalog_field_meta(t_tables, str(target_binding.get('table', '') or ''))
                                if t_meta:
                                    member_meta = t_meta.get(direct)
                                    if member_meta is None:
                                        warnings.append(f'属性来源 {ot}.{prop}：成员字段「{direct}」不在终点身份表目录中，请刷新表结构')
                                    elif field_type(member_meta) not in NUMERIC_FIELD_TYPES:
                                        blocking.append('成员属性不是身份表直接数值字段，本轮聚合不支持')
                empty = value.get('empty')
                if empty is None:
                    blocking.append('空值策略必须为字符串 "null"')
                elif empty != 'null':
                    blocking.append('空值策略本轮仅支持 "null"')
                if value.get('missing') != 'incomplete':
                    blocking.append('缺失处理策略本轮仅支持 incomplete')
                if value.get('inputUnitConfirmed') is not True:
                    warnings.append(f'属性来源 {ot}.{prop}：尚未确认成员数值单位口径一致')
                if relation and not membership_rules(b, relation):
                    warnings.append(f'属性来源 {ot}.{prop}：聚合引用的链接尚无成员规则')
            else:
                blocking.append('属性来源方式无效')
            if (ot, prop) in dep_cycle_props:
                blocking.append('属性来源存在循环依赖')
            blocking = list(dict.fromkeys(blocking))
            issues = blocking + pending
            errors.extend(f'属性来源 {ot}.{prop}：{i}' for i in blocking)
            status = 'invalid' if blocking else ('unconfigured' if pending else 'valid')
            items.append({'kind': 'aggregateSource' if kind == 'aggregate' else 'propertySource', 'id': f'{ot}.{prop}',
                          'name': f'属性来源 · {ot}.{prop}', 'status': status, 'issues': issues})


def _check_link_mappings(ctx, errors, warnings, items):
    graph = ctx['graph']
    relations = ctx['relations']
    bare_classes = ctx['bare_classes']
    object_bindings = ctx['object_bindings']
    catalogs = ctx['catalogs']
    mapped_types = ctx['mapped_types']
    for b in object_bindings:
        ot = b.get('object_type', '')
        sources = sources_of(b)
        for r in b.get('relations') or []:
            if not isinstance(r, dict):
                continue
            relation = str(r.get('relation', '') or '')
            if r.get('kind') == 'membership':
                # 按条件选择成员（任务 B 规则 4）：走独立校验与独立 items 条目。
                _check_membership_relation(ctx, b, r, errors, warnings, items)
                continue
            blocking, pending = [], []
            if relation not in relations:
                blocking.append('引用的版本中不存在此链接类型')
            if r.get('target_type') not in bare_classes:
                blocking.append('引用的版本中不存在此链接终点对象类型')
            is_new_format = any(k in r for k in ('field', 'targetSourceId', 'targetField'))
            if not is_new_format:
                notice = '链接映射为旧格式（仅字段），建议在链接映射页重新选择两端来源保存'
                warnings.append(notice)
                pending.append(notice)
            else:
                field = str(r.get('field', r.get('column', '')) or '')
                source_id = str(r.get('sourceId', '') or '')
                issue = link_end_issue(source_id, sources)
                if issue:
                    blocking.append(issue)
                if not field.strip():
                    pending.append('未选择起点字段')
                target_binding = mapped_types.get(r.get('target_type'))
                if target_binding is None:
                    blocking.append('链接终点对象尚未配置数据映射')
                target_source_id = str(r.get('targetSourceId', '') or '')
                target_field = str(r.get('targetField', '') or '')
                if target_binding is not None:
                    target_sources = sources_of(target_binding)
                    issue = link_end_issue(target_source_id, target_sources)
                    if issue:
                        blocking.append(issue)
                    if not target_field.strip():
                        pending.append('未选择目标匹配字段')
                    if target_field.strip() and relation in relations:
                        node = object_property_node(graph, relation)
                        # many-to-one / one-to-one links need a unique target match key;
                        # without a refreshed catalog the uniqueness stays unverified.
                        if graph_value(node, 'mg:cardinality') in ('many-to-one', 'one-to-one'):
                            t_source = target_sources.get(target_source_id) if target_source_id else None
                            t_conn = str((t_source or {}).get('connection') or target_binding.get('connection', '') or '')
                            t_table = str((t_source or {}).get('table') or target_binding.get('table', '') or '')
                            catalog = catalogs.get(t_conn) if isinstance(catalogs.get(t_conn), dict) else {}
                            table_meta = next((t for t in catalog.get('tables') or []
                                               if isinstance(t, dict) and t.get('name') == t_table), None)
                            field_meta = next((f for f in (table_meta or {}).get('fields') or []
                                               if isinstance(f, dict) and f.get('name') == target_field), None)
                            if field_meta is None:
                                warnings.append('目标匹配字段唯一性未知，待数据接入后验证')
                            elif field_meta.get('key') not in ('pri', 'uni'):
                                blocking.append(f'目标匹配字段「{target_field}」不是唯一键，无法满足数量关系')
            issues = blocking + pending
            errors.extend(f'链接映射 {relation}：{i}' for i in blocking)
            status = 'invalid' if blocking else ('unconfigured' if pending else 'valid')
            items.append({'kind': 'linkMapping', 'id': f'{ot}.{relation}',
                          'name': f'链接映射 · {relation}', 'status': status, 'issues': issues})


def _check_membership_relation(ctx, b, r, errors, warnings, items):
    """membership 链接映射校验（任务 B 规则 4，A3/A4）：按条件选择成员。

    集合端按端判定（方向无关）：出向一对多/多对多或入向多对一/多对多允许，
    一对一等不支持的数量关系 → error；target_type 须等于成员端对象。规则：sourceInstance
    须存在于本对象登记实例且
    同链接不重复；filtered 至少一条条件、all 记 warning；条件字段须在终点对象身份表目录
    （目录缺失 → warning 刷新提示，有目录但字段不在 → error）；操作符与取值按目录字段
    类型校验。只读，不改数据。执行期冲突检测（一对多下同一成员落入多个实例）归任务 C。
    """
    graph = ctx['graph']
    bare_classes = ctx['bare_classes']
    mapped_types = ctx['mapped_types']
    catalogs = ctx['catalogs']
    ot = b.get('object_type', '')
    relation = str(r.get('relation', '') or '')
    blocking, pending = [], []
    rel_node = object_property_node(graph, relation) if relation else None
    if not relation:
        blocking.append('未选择成员链接')
    elif rel_node is None:
        blocking.append('引用的版本中不存在此链接类型')
    if r.get('target_type') not in bare_classes:
        blocking.append('引用的版本中不存在此链接终点对象类型')
    if rel_node is not None:
        # 集合端按端判定（方向无关）：出向一对多/多对多（成员=range），
        # 或入向多对一/多对多（成员=domain）；两端都不是当前对象、或数量关系不满足 → error。
        member_type, _card, _orient = member_side_of(graph, ot, relation)
        if not member_type:
            domain = bare(rel_node.get('rdfs:domain', {}).get('@id', '')) if isinstance(rel_node.get('rdfs:domain'), dict) else ''
            range_ref = rel_node.get('rdfs:range')
            rng = bare(range_ref.get('@id')) if isinstance(range_ref, dict) else ''
            if domain == ot or rng == ot:
                blocking.append('该数量关系不支持成员规则（集合端须出向一对多／多对多，或入向多对一／多对多）')
            else:
                blocking.append('成员规则链接的两端都不是当前对象')
        elif member_type != r.get('target_type'):
            blocking.append('成员规则的成员对象端与链接定义不一致')
    target_binding = mapped_types.get(r.get('target_type'))
    t_fields = None
    t_meta = {}
    if target_binding is None:
        blocking.append('链接终点对象尚未配置数据映射')
    else:
        if identity_kind(target_binding) == 'registered':
            blocking.append('成员规则要求终点对象为数据库来源')
        t_tables = catalog_tables(catalogs, str(target_binding.get('connection', '') or ''))
        t_fields = catalog_fields(t_tables, str(target_binding.get('table', '') or ''))
        t_meta = catalog_field_meta(t_tables, str(target_binding.get('table', '') or ''))
    raw_rules = r.get('rules') if isinstance(r.get('rules'), list) else []
    if not raw_rules:
        pending.append('尚未配置成员规则')
    registered_ids = {i.get('id') for i in registered_instances(b) if isinstance(i.get('id'), str)}
    seen_sources = set()
    for rule in raw_rules:
        if not isinstance(rule, dict):
            blocking.append('成员规则格式无效')
            continue
        sid = rule.get('sourceInstance')
        if not str(sid or '').strip():
            blocking.append('成员规则未选择起点实例')
        elif sid not in registered_ids:
            blocking.append(f'成员规则引用的实例未登记：{sid}')
        elif sid in seen_sources:
            blocking.append('同一实例在该链接下重复配置成员规则')
        else:
            seen_sources.add(sid)
        scope = rule.get('scope')
        conditions = rule.get('conditions') if isinstance(rule.get('conditions'), list) else []
        if scope == 'filtered':
            if not conditions:
                blocking.append('按条件选择必须至少一条条件；如需整表请在范围中显式选择')
        elif scope == 'all':
            warnings.append(f'成员规则 {relation}：该来源表全部有效记录将作为成员')
        else:
            blocking.append('成员范围无效')
        for cond in conditions:
            if not isinstance(cond, dict):
                blocking.append('成员条件配置格式无效')
                continue
            field = str(cond.get('field', '') or '')
            op = cond.get('operator')
            if not field:
                blocking.append('成员条件未选择字段')
            elif target_binding is None:
                pass  # 终点未映射已有 error，不再叠加目录提示
            elif t_fields is None:
                warnings.append(f'成员规则 {relation}：终点表结构目录未读取或已过期，条件字段待核对')
            elif field not in t_fields:
                blocking.append(f'条件字段「{field}」不在终点对象身份表目录中')
            if op not in ('eq', 'ne', 'in', 'isnull', 'notnull'):
                blocking.append('条件操作符仅支持等于／不等于／属于列表／为空／不为空')
            elif op in ('eq', 'ne'):
                if field in t_meta and field_type(t_meta[field]) in NUMERIC_FIELD_TYPES:
                    try:
                        float(str(cond.get('value')))
                    except (TypeError, ValueError):
                        blocking.append('条件取值与字段类型不兼容')
            elif op == 'in':
                if not isinstance(cond.get('value'), list):
                    blocking.append('属于列表的条件取值必须是列表')
            elif cond.get('value') is not None:
                blocking.append('为空／不为空条件不能携带取值')
    issues = blocking + pending
    errors.extend(f'成员规则 {relation}：{i}' for i in blocking)
    status = 'invalid' if blocking else ('unconfigured' if pending else 'valid')
    items.append({'kind': 'membership', 'id': f'{ot}.{relation}',
                  'name': f'成员规则 · {ot}.{relation}', 'status': status, 'issues': issues})


def _check_contract_coverage(ctx, errors, warnings):
    functions = ctx['functions']
    object_bindings = ctx['object_bindings']
    implementations = ctx['implementations']
    edges = {}
    for impl in ctx['implementation_list']:
        if not isinstance(impl, dict):
            continue
        for binding in impl.get('inputBindings', []):
            source = (binding.get('source') or {})
            if source.get('kind') == 'output':
                edges.setdefault(impl['id'], set()).add(source.get('implementation'))
    visiting, done = set(), set()

    def has_cycle(node):
        if node in visiting:
            return True
        if node in done:
            return False
        visiting.add(node)
        for child in edges.get(node, ()):
            if has_cycle(child):
                return True
        visiting.remove(node)
        done.add(node)
        return False

    if any(has_cycle(node) for node in list(edges)):
        errors.append('计算实现之间存在循环依赖，请调整输出引用')

    implemented = {i.get('contractId') for i in ctx['implementation_list'] if isinstance(i, dict)}
    referenced = set()
    for b in object_bindings:
        for value in (b.get('properties') or {}).values():
            if isinstance(value, dict) and value.get('kind') == 'computed':
                contract_id = (implementations.get(value.get('implementation')) or {}).get('contractId')
                if contract_id:
                    referenced.add(contract_id)
    for fid, function in functions.items():
        if is_contract(function) and fid in referenced and fid not in implemented:
            warnings.append(f'契约「{function.get("name") or fid}」被属性来源引用但尚无实现')


def _action_v2_inputs(actions, action_id):
    """该动作在项目引用版本中真实存在的输入（宽容读取、零补写）。

    仅接受 dict 且 name 非空且唯一的输入；有稳定 id 时一并带上（供 inputId 匹配）。
    动作无输入或定义缺失一律返回空列表——校验据此报「没有输入参数」，
    绝不凭空生成选项、也不回写本体定义。
    """
    action = actions.get(action_id) if isinstance(actions, dict) else None
    raw = action.get('inputs') if isinstance(action, dict) else None
    out, seen = [], set()
    if not isinstance(raw, list):
        return out
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get('name') or '').strip()
        if not name or name in seen:
            continue
        seen.add(name)
        entry = {'name': name}
        identifier = str(item.get('id') or '').strip()
        if identifier:
            entry['id'] = identifier
        out.append(entry)
    return out


def _action_v2_api_issues(ctx, object_id, action_id, actions, implementation):
    """本期（schemaVersion=2）api 实现校验：组装引用版本＋项目映射上下文后交给 action_http。

    引用版本数据（动作输入、对象属性）来自 ctx['ontology_state']；项目侧数据
    （属性取值是否已配置、实例识别是否就绪、凭据目录）来自项目自身配置。
    只读：不补写默认值、不迁移旧格式。
    """
    graph = ctx['graph']
    type_id = 'mg:' + str(object_id or '').removeprefix('mg:')
    properties = action_http.property_index(graph, type_id)
    # 项目属性取值只按 apiName 匹配（与前端 hasConfiguredSource 的取值一致）；
    # 对象类型按 bare 形式比较（绑定里是 bare，映射行同样）。
    object_row = None
    if str(object_id or '').strip():
        for b in ctx.get('object_bindings') or []:
            if isinstance(b, dict) and bare(b.get('object_type')) == bare(object_id):
                object_row = b
                break
    configured = object_row.get('properties') if isinstance(object_row, dict) and isinstance(object_row.get('properties'), dict) else {}
    for info in properties.values():
        info['configured'] = action_http.configured_source(configured.get(info['api']))
    credential_ids = None
    try:
        from workbench import api_credentials
        credential_ids = api_credentials.ids(str(ctx['state'].get('projectId') or ''))
    except Exception:
        credential_ids = None  # 凭据目录不可读（或尚未就绪）时跳过该项检查，不阻断其余校验
    return action_http.api_issues(
        implementation,
        action_inputs=_action_v2_inputs(actions, action_id),
        properties=properties,
        identity_ready=action_http.identity_ready(object_row),
        credential_ids=credential_ids,
    )


def _check_action_bindings(ctx, errors, warnings, items):
    """项目动作绑定（20260917 需求）：按「项目＋对象类型＋动作」组合校验，只读、零删除。

    有效动作与关联只来自项目固定引用的已发布本体版本（ctx 里的 ontology_state 即
    versions.read_state 的结果）：显式 workflow.actionAssociations ∪ 历史动作 object_type 推导。
    未绑定动作不产生任何输出（不阻止发布）；失效绑定保留并报 error（阻止发布），
    由用户在项目草稿中确认移除。roles/paramNotes 是说明性文本，不做格式校验。

    实现方式分支：`kind='api'` 且 `schemaVersion=2`（action_http.is_v2）走本期完整
    HTTP 接口校验（action_http.api_issues，见 _action_v2_api_issues）；历史 api
    （无 schemaVersion）只要求接口路径非空；`kind='flow'` 仍校验编排存在性；
    其它 kind 报「实现方式无效」。旧格式一律原样保留，不自动升级。
    """
    from workbench.workflow import effective_action_associations
    rows = ctx['state'].get('bindings', {}).get('actionBindings', [])
    if rows is None:
        return
    if not isinstance(rows, list):
        errors.append('动作绑定配置必须是列表')
        items.append({'kind': 'actionBinding', 'id': 'actionBindings', 'name': '动作绑定',
                      'status': 'invalid', 'issues': ['动作绑定配置必须是列表']})
        return
    if not rows:
        return
    ontology_state = ctx['ontology_state']
    graph = ctx['graph']
    classes = {n['@id'].removeprefix('mg:') for n in graph if n['@type'] == 'owl:Class'}
    label_of = {n['@id'].removeprefix('mg:'): (n.get('rdfs:label') or n['@id'].removeprefix('mg:')) for n in graph if n.get('@id')}
    actions = {a.get('id'): a for a in ontology_state.get('workflow', {}).get('actions', [])
               if isinstance(a, dict) and a.get('id')}
    valid_pairs = {(r['objectTypeId'].removeprefix('mg:'), r['actionId'])
                   for r in effective_action_associations(ontology_state)}
    flow_ids = None
    flow_deps = {}   # flowId → dependency_state 三态（同一编排多处引用只读一次）
    try:
        from workbench import flows
        flow_ids = {f.get('id') for f in flows.listing() if isinstance(f, dict) and f.get('id')}
    except storage.StorageUnavailable:
        raise  # 存储不可用：原样抛出，由路由转 503（绝不降级为「跳过检查」）
    except Exception:
        flow_ids = None  # 列表读取失败 → fail-closed：逐行 flow 绑定报 error（不再跳过存在性检查）
    seen = set()
    for index, row in enumerate(rows, 1):
        combo = f'#{index}'
        issues = []
        if not isinstance(row, dict):
            errors.append(f'动作绑定第 {index} 条格式无效')
            items.append({'kind': 'actionBinding', 'id': f'actionBinding-{index}', 'name': f'动作绑定 · 第 {index} 条',
                          'status': 'invalid', 'issues': ['配置格式无效：应为对象类型＋动作＋实现的对象']})
            continue
        object_id = row.get('objectTypeId')
        action_id = row.get('actionId')
        object_label = label_of.get(str(object_id or ''), str(object_id or '未指定'))
        action_label = (actions.get(action_id) or {}).get('name') or str(action_id or '未指定')
        combo = f'{object_id}:{action_id}'
        name = f'动作绑定 · {object_label} / {action_label}'
        if not str(object_id or '').strip() or not str(action_id or '').strip():
            issues.append('对象类型或动作标识未填写')
        else:
            if str(object_id) not in classes:
                issues.append('引用本体的对象类型不在项目引用版本中')
            if action_id not in actions:
                issues.append('引用的动作不在项目引用版本中')
            if (str(object_id), action_id) in seen:
                issues.append('同一对象类型与动作重复配置绑定')
            seen.add((str(object_id), action_id))
            if not issues and (str(object_id), action_id) not in valid_pairs:
                issues.append('关联已失效：引用版本中此对象类型未关联该动作（历史配置保留）')
        implementation = row.get('implementation')
        if not isinstance(implementation, dict) or not str(implementation.get('kind') or '').strip():
            issues.append('实现方式未选择（项目接口或函数编排）')
        else:
            kind = implementation.get('kind')
            if kind == 'api':
                if action_http.is_v2(implementation):
                    # 本期格式（schemaVersion=2）：完整配置校验，错误同样并入该绑定的 issues，
                    # 由下方统一按既有「动作绑定 {name}：…」文案上报。
                    v2_errors, v2_warnings = _action_v2_api_issues(ctx, object_id, action_id, actions, implementation)
                    issues.extend(v2_errors)
                    warnings.extend(f'动作绑定 {name}：{i}' for i in v2_warnings)
                elif not str(implementation.get('path') or '').strip():
                    # 历史 api 格式（无 schemaVersion）：行为原样，仅要求接口路径非空。
                    issues.append('项目接口路径未填写')
            elif kind == 'flow':
                flow_id = implementation.get('flowId')
                if not str(flow_id or '').strip():
                    issues.append('函数编排未选择')
                elif flow_ids is None:
                    # 编排列表读取失败：fail-closed，绝不当成「不存在」也不跳过检查
                    issues.append(f'引用的函数编排 {flow_id}：读取失败，暂不能校验该绑定；请稍后重试')
                elif flow_id not in flow_ids:
                    issues.append('引用的函数编排不存在或已删除')
                else:
                    # 编排自身结构错误（R02 2026-09-20）：动作的实现编排不可用同样阻断。
                    dep = flow_deps.get(flow_id)
                    if dep is None:
                        dep = flows.dependency_state(flow_id)
                        flow_deps[flow_id] = dep
                    if dep.get('status') == 'unreadable':
                        issues.append(f'引用的函数编排 {flow_id}：读取失败，暂不能校验该绑定；请稍后重试')
                    elif dep.get('status') == 'missing' or isinstance(dep.get('state'), dict) and dep['state'].get('status') == 'deleted':
                        issues.append('引用的函数编排不存在或已删除')
                    elif isinstance(dep.get('state'), dict):
                        issues.extend(f'引用编排：{e}' for e in _flow_struct_errors(flow_id, dep['state'], ctx))
            else:
                issues.append(f'实现方式无效：{kind}')
        if '同一对象类型与动作重复配置绑定' not in issues and issues:
            errors.extend(f'动作绑定 {name}：{i}' for i in issues)
        elif issues:  # 重复组合不重复计 error，仅标记条目
            errors.append(f'动作绑定 {name}：同一对象类型与动作重复配置绑定')
        items.append({'kind': 'actionBinding', 'id': str(row.get('id') or combo), 'name': name,
                      'status': 'invalid' if issues else 'valid', 'issues': issues})
