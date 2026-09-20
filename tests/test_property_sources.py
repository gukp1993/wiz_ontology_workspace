"""项目侧属性来源新结构（database 直选表／Redis 直连／computed 输出形态声明）后端校验测试。

纯 python3 脚本（无 pytest），全部用内存 dict 构造 state + ontology_state，
不启动服务器、不占用端口、不读写真实 ontology/（未设 WIZ_WORKBENCH_ROOT 时自动落到临时目录）。
运行：WIZ_WORKBENCH_ROOT=$(mktemp -d) python3 tests/test_property_sources.py
"""
import copy
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if not os.environ.get('WIZ_WORKBENCH_ROOT'):
    os.environ['WIZ_WORKBENCH_ROOT'] = tempfile.mkdtemp(prefix='wiz_property_sources_')

from workbench import projects

FAILURES = []


def run(name, fn):
    try:
        fn()
        print(f'通过  {name}')
    except Exception as exc:
        FAILURES.append(name)
        print(f'失败  {name}: {type(exc).__name__}: {exc}')


# --- 夹具 ----------------------------------------------------------------------

STATION = {'@id': 'mg:Station', '@type': 'owl:Class', 'rdfs:label': '储能单元'}

MYSQL_CONN = {'id': 'mysql_main', 'name': '主数据库', 'engine': 'mysql',
              'host': '127.0.0.1', 'port': 3306, 'database': 'demo'}
REDIS_CONN = {'id': 'redis_main', 'name': '缓存', 'engine': 'redis', 'host': '127.0.0.1', 'port': 6379}

CATALOG = {'mysql_main': {'tables': [
    {'name': 'station', 'fields': [
        {'name': 'id', 'dataType': 'bigint'}, {'name': 'name', 'dataType': 'varchar'},
        {'name': 'rated_power', 'dataType': 'double'}]},
    {'name': 'telemetry_sample', 'fields': [
        {'name': 'id', 'dataType': 'bigint'}, {'name': 'object_id', 'dataType': 'bigint'},
        {'name': 'object_type', 'dataType': 'varchar'}, {'name': 'metric_code', 'dataType': 'varchar'},
        {'name': 'value', 'dataType': 'double'}, {'name': 'sampled_at', 'dataType': 'datetime'},
        {'name': 'seq', 'dataType': 'int'}]},
]}}


def prop_node(api, dtype='double', **kw):
    node = {'@id': f'mg:{api}', '@type': 'owl:DatatypeProperty', 'rdfs:label': api, 'mg:apiName': api,
            'rdfs:domain': {'@id': 'mg:Station'}, 'rdfs:range': {'@id': f'xsd:{dtype}'}}
    node.update(kw)
    return node


def base_graph(soc_series=False):
    soc = prop_node('soc', **({'mg:valueShape': 'timeSeries'} if soc_series else {}))
    return [dict(STATION), prop_node('rated_power'), soc,
            prop_node('device_code', 'string'), prop_node('temp_avg')]


def validate(properties, graph=None, functions=None, implementations=None, parameters=None,
             catalogs=None, degraded_catalogs=None):
    binding = {'object_type': 'Station', 'connection': 'mysql_main', 'table': 'station', 'primary_key': 'id',
               'title_key': '', 'properties': properties, 'relations': []}
    state = {'projectId': 'p1', 'name': '校验测试项目', 'ontologyId': 'storage', 'ontologyVersion': '1',
             'connections': {'connections': [dict(MYSQL_CONN), dict(REDIS_CONN)]},
             'bindings': {'notice': '', 'object_bindings': [binding],
                          'observation_binding': {}, 'source_candidates': [],
                          'catalogs': CATALOG if catalogs is None else catalogs},
             'implementations': implementations or [],
             'parameters': parameters or {}}
    ontology = {'ontology': {'@context': {}, '@graph': base_graph() if graph is None else graph},
                'workflow': {'functions': functions or []}}
    return projects.validate_project(state, ontology, degraded_catalogs=degraded_catalogs)


def soc_series_config():
    """合法的长表 SOC（timeSeries 目标）database 配置。"""
    return {'kind': 'database', 'connection': 'mysql_main', 'table': 'telemetry_sample',
            'lookup': {'match': [
                {'field': 'object_id', 'operator': 'eq', 'value': {'kind': 'identityKey'}},
                {'field': 'object_type', 'operator': 'eq', 'value': {'kind': 'constant', 'value': 'cluster'}},
                {'field': 'metric_code', 'operator': 'eq', 'value': {'kind': 'constant', 'value': 'soc'}}],
                'timeRange': {'field': 'sampled_at', 'start': {'kind': 'context', 'name': 'startTime'},
                              'end': {'kind': 'context', 'name': 'endTime'}, 'bounds': '[start,end)'}},
            'result': {'valueField': 'value', 'timestampField': 'sampled_at',
                       'timestampEncoding': 'datetime', 'timezone': 'Asia/Shanghai'}}


def source_errors(report, prop='soc'):
    return [e for e in report['errors'] if e.startswith(f'属性来源 Station.{prop}：')]


def source_item(report, prop='soc'):
    return next(i for i in report['items'] if i['kind'] == 'propertySource' and i['id'] == f'Station.{prop}')


# --- a) 兼容：旧编码语义不破坏（A1）---------------------------------------------

def t_legacy_string_mapping():
    report = validate({'rated_power': 'rated_power'})
    assert report['errors'] == [], f'旧字符串直取不应报错：{report["errors"]}'
    assert source_item(report, 'rated_power')['status'] == 'valid'


# --- b) database 直选表 ----------------------------------------------------------

def t_database_series_valid():
    report = validate({'soc': soc_series_config()}, graph=base_graph(soc_series=True))
    assert report['errors'] == [], f'合法长表 SOC 配置不应报错：{report["errors"]}'
    assert source_item(report)['status'] == 'valid'


def t_database_identity_row_direct():
    """身份表本行（同连接同表）空匹配自动按主键定位，等价旧直取。"""
    config = {'kind': 'database', 'connection': 'mysql_main', 'table': 'station',
              'lookup': {'match': []}, 'result': {'valueField': 'rated_power'}}
    report = validate({'rated_power': config})
    assert report['errors'] == [], f'身份表本行空匹配不应报错：{report["errors"]}'


def t_database_missing_instance_binding():
    soc = soc_series_config()
    soc['lookup']['match'] = [c for c in soc['lookup']['match'] if c['value']['kind'] != 'identityKey']
    report = validate({'soc': soc}, graph=base_graph(soc_series=True))
    assert any('绑定当前实例' in e for e in source_errors(report)), f'缺少当前实例绑定应 block：{report["errors"]}'
    assert source_item(report)['status'] == 'invalid'


def t_database_property_ref_series_blocked():
    soc = soc_series_config()
    soc['lookup']['match'].append({'field': 'object_id', 'operator': 'eq',
                                   'value': {'kind': 'property', 'property': 'temp_avg'}})
    graph = base_graph(soc_series=True)
    graph[-1] = prop_node('temp_avg', **{'mg:valueShape': 'timeSeries'})  # 被引用属性为序列形态
    temp_avg = {'kind': 'database', 'connection': 'mysql_main', 'table': 'station',
                'lookup': {'match': []}, 'result': {'valueField': 'rated_power'}}
    report = validate({'soc': soc, 'temp_avg': temp_avg}, graph=graph)
    assert any('匹配条件引用的属性必须是单值属性' in e for e in source_errors(report)), \
        f'引用序列属性应 block：{report["errors"]}'


def t_database_constant_type_mismatch():
    soc = soc_series_config()
    soc['lookup']['match'].append({'field': 'value', 'operator': 'eq',
                                   'value': {'kind': 'constant', 'value': 'abc'}})  # double 字段
    report = validate({'soc': soc}, graph=base_graph(soc_series=True))
    assert any('常量取值与字段类型不兼容' in e for e in source_errors(report)), \
        f'常量非数值应 block：{report["errors"]}'
    soc2 = soc_series_config()
    soc2['lookup']['match'].append({'field': 'seq', 'operator': 'eq',
                                    'value': {'kind': 'constant', 'value': '3.5'}})  # int 字段要求整数
    report2 = validate({'soc': soc2}, graph=base_graph(soc_series=True))
    assert any('常量取值与字段类型不兼容' in e for e in source_errors(report2)), \
        f'整数字段传小数应 block：{report2["errors"]}'


def t_database_series_missing_timestamp_field():
    soc = soc_series_config()
    del soc['result']['timestampField']
    report = validate({'soc': soc}, graph=base_graph(soc_series=True))
    assert any('时间序列结果必须选择时间字段' in e for e in source_errors(report)), \
        f'序列缺时间字段应 block：{report["errors"]}'


def t_database_bad_timezone():
    soc = soc_series_config()
    soc['result']['timezone'] = 'Mars/Olympus'
    report = validate({'soc': soc}, graph=base_graph(soc_series=True))
    assert any('IANA 时区' in e for e in source_errors(report)), f'非法时区应 block：{report["errors"]}'


def t_database_series_selection_blocked():
    soc = soc_series_config()
    soc['result']['selection'] = 'sum'
    report = validate({'soc': soc}, graph=base_graph(soc_series=True))
    assert any('时间序列属性不支持聚合取值规则' in e for e in source_errors(report)), \
        f'序列结果带聚合取值规则应 block：{report["errors"]}'


def t_database_series_missing_blocked():
    soc = soc_series_config()
    soc['result']['missing'] = 'error'
    report = validate({'soc': soc}, graph=base_graph(soc_series=True))
    assert any('时间序列属性不支持无记录策略配置' in e for e in source_errors(report)), \
        f'序列结果带无记录策略应 block：{report["errors"]}'


def t_database_time_range_on_scalar():
    config = {'kind': 'database', 'connection': 'mysql_main', 'table': 'telemetry_sample',
              'lookup': {'match': [{'field': 'object_id', 'operator': 'eq', 'value': {'kind': 'identityKey'}}],
                         'timeRange': {'field': 'sampled_at', 'start': {'kind': 'context', 'name': 'startTime'},
                                       'end': {'kind': 'context', 'name': 'endTime'}, 'bounds': '[start,end)'}},
              'result': {'valueField': 'value'}}
    report = validate({'rated_power': config})
    assert any('单值属性不能映射时间序列结果' in e for e in source_errors(report, 'rated_power')), \
        f'scalar 目标带 timeRange 应 block：{report["errors"]}'
    config2 = copy.deepcopy(config)
    del config2['lookup']['timeRange']
    config2['result'].update({'timestampField': 'sampled_at', 'timestampEncoding': 'datetime',
                              'timezone': 'Asia/Shanghai'})
    report2 = validate({'rated_power': config2})
    assert any('单值属性不能映射时间序列结果' in e for e in source_errors(report2, 'rated_power')), \
        f'scalar 目标带时间序列 result 应 block：{report2["errors"]}'


def t_database_table_pending():
    """表未选择是唯一 pending（待配置）；其余结构性问题 blocking。"""
    config = {'kind': 'database', 'connection': 'mysql_main', 'table': '',
              'lookup': {'match': [{'field': 'object_id', 'operator': 'eq', 'value': {'kind': 'identityKey'}}]},
              'result': {'valueField': 'rated_power'}}
    report = validate({'rated_power': config})
    item = source_item(report, 'rated_power')
    assert item['status'] == 'unconfigured', f'未选表应为待配置：{item}'
    assert any('未选择数据表' in i for i in item['issues']), f'应提示未选择数据表：{item["issues"]}'


def t_database_catalog_unavailable_warning():
    report = validate({'soc': soc_series_config()}, graph=base_graph(soc_series=True), catalogs={})
    assert any('表结构目录未读取或已过期' in w for w in report['warnings']), \
        f'目录不可用应提示元数据待核对：{report["warnings"]}'
    assert source_item(report)['status'] == 'valid', f'目录不可用不应 block：{source_item(report)}'


def t_database_dependency_cycle():
    def db(ref):
        return {'kind': 'database', 'connection': 'mysql_main', 'table': 'telemetry_sample',
                'lookup': {'match': [
                    {'field': 'object_id', 'operator': 'eq', 'value': {'kind': 'identityKey'}},
                    {'field': 'metric_code', 'operator': 'eq',
                     'value': {'kind': 'property', 'property': ref}}]},
                'result': {'valueField': 'value'}}
    report = validate({'rated_power': db('device_code'), 'device_code': db('rated_power')})
    assert sum('属性来源存在循环依赖' in e for e in report['errors']) >= 2, \
        f'循环依赖未检出：{report["errors"]}'
    assert source_item(report, 'rated_power')['status'] == 'invalid'
    assert source_item(report, 'device_code')['status'] == 'invalid'


# --- c) Redis：直连与登记来源 -----------------------------------------------------

def t_redis_direct_valid():
    config = {'kind': 'redis', 'connection': 'redis_main', 'command': 'GET', 'key': 'status:{room}',
              'params': {'room': {'from': 'identityField', 'field': 'name'}},
              'conversion': 'number', 'missing': 'null'}
    report = validate({'device_code': config})
    assert report['errors'] == [], f'Redis 直连合法配置不应报错：{report["errors"]}'
    assert source_item(report, 'device_code')['status'] == 'valid'


def t_redis_source_connection_mutex():
    both = {'kind': 'redis', 'source': 'r1', 'connection': 'redis_main', 'command': 'GET',
            'key': 'k', 'params': {}}
    report = validate({'device_code': both})
    assert any('只能选其一' in e for e in source_errors(report, 'device_code')), \
        f'来源与连接同时设置应 block：{report["errors"]}'
    neither = {'kind': 'redis', 'command': 'GET', 'key': 'k', 'params': {}}
    report2 = validate({'device_code': neither})
    assert any('只能选其一' in e for e in source_errors(report2, 'device_code')), \
        f'来源与连接都空应 block：{report2["errors"]}'


def t_redis_identity_field_catalog_warning():
    config = {'kind': 'redis', 'connection': 'redis_main', 'command': 'GET', 'key': 'status:{room}',
              'params': {'room': {'from': 'identityField', 'field': 'not_a_field'}}}
    report = validate({'device_code': config})
    assert not source_errors(report, 'device_code'), \
        f'目录外身份表字段应是 warning 不 block：{report["errors"]}'
    assert any('身份表字段' in w and 'not_a_field' in w for w in report['warnings']), \
        f'应提示字段不在目录：{report["warnings"]}'


def t_redis_series_target_blocked():
    config = {'kind': 'redis', 'connection': 'redis_main', 'command': 'GET', 'key': 'soc', 'params': {}}
    report = validate({'soc': config}, graph=base_graph(soc_series=True))
    assert any('Redis 普通读取不能作为时间序列来源' in e for e in source_errors(report)), \
        f'Redis 绑序列属性应 block：{report["errors"]}'


# --- d) computed：输出形态声明 ----------------------------------------------------

def contract_and_impl(value_shape=None):
    declaration = {'outputId': 'series'}
    if value_shape:
        declaration['valueShape'] = value_shape
    contract = {'id': 'fn1', 'name': '序列取数', 'guide_version': 3,
                'inputs': [], 'outputs': [{'id': 'series'}]}
    impl = {'id': 'impl1', 'name': '序列取数实现', 'contractId': 'fn1', 'inputBindings': [],
            'outputDeclarations': [declaration], 'rule': 'return series'}
    return contract, impl


def t_computed_shape_mismatch():
    contract, impl = contract_and_impl()  # 声明缺省 = scalar
    computed = {'kind': 'computed', 'implementation': 'impl1', 'output': 'series'}
    report = validate({'soc': computed}, graph=base_graph(soc_series=True),
                      functions=[contract], implementations=[impl])
    assert any('函数输出数据类型与属性要求不匹配' in e for e in source_errors(report)), \
        f'标量输出绑序列属性应 block：{report["errors"]}'


def t_computed_shape_match():
    contract, impl = contract_and_impl('timeSeries')
    computed = {'kind': 'computed', 'implementation': 'impl1', 'output': 'series'}
    report = validate({'soc': computed}, graph=base_graph(soc_series=True),
                      functions=[contract], implementations=[impl])
    assert report['errors'] == [], f'声明形态匹配不应报错：{report["errors"]}'
    assert source_item(report)['status'] == 'valid'


# --- e) 结构分支兜底 --------------------------------------------------------------

def t_unknown_kind():
    report = validate({'device_code': {'kind': 'quantum', 'field': 'x'}})
    assert any('属性来源方式无效' in e for e in source_errors(report, 'device_code')), \
        f'未知 kind 应报属性来源方式无效：{report["errors"]}'
    report2 = validate({'device_code': 42})
    assert any('属性来源配置格式无效' in e for e in source_errors(report2, 'device_code')), \
        f'非字符串非对象应报格式无效：{report2["errors"]}'


# --- f) 属性键不存在 / 字段目录核对 / 目录缓存读取失败（2026-09-20 v2） -------------

def t_property_key_missing_blocked():
    """属性键在引用版本中不存在（被删除/改名）→ error 阻断，原配置保留。"""
    report = validate({'ghost_prop': 'rated_power'})
    assert any('引用的版本中不存在此属性' in e for e in source_errors(report, 'ghost_prop')), \
        f'属性键不存在应 block：{report["errors"]}'
    item = source_item(report, 'ghost_prop')
    assert item['status'] == 'invalid' and '引用的版本中不存在此属性' in item['issues'], item
    # 存在的属性不受影响（0／false／空串边界不误拒：合法配置零错误）
    ok = validate({'rated_power': 'rated_power', 'device_code': 'device_code'})
    assert ok['errors'] == [], f'存在属性的合法配置不得报错：{ok["errors"]}'


def t_field_missing_with_catalog_blocked():
    """field/related 来源字段：有目录但字段不存在 → error；目录缺失保持现状不报。"""
    # 身份表字段 device_code 存在；目录中不存在的字段应 block
    bad = validate({'rated_power': {'kind': 'field', 'field': 'no_such_field'}})
    assert any('不在表' in e and 'no_such_field' in e for e in source_errors(bad, 'rated_power')), \
        f'目录中不存在的字段应 block：{bad["errors"]}'
    assert source_item(bad, 'rated_power')['status'] == 'invalid'
    # 目录缺失（{}）时保持现状：不报字段错误（表结构过期另有提示路径）
    no_catalog = validate({'rated_power': {'kind': 'field', 'field': 'no_such_field'}}, catalogs={})
    assert not any('no_such_field' in e for e in source_errors(no_catalog, 'rated_power')), \
        f'无目录时不应报字段缺失：{no_catalog["errors"]}'


def t_degraded_catalogs_param():
    """degraded_catalogs 形参：每个损坏连接一条 error（含 id/名称）+ connection 条目；默认 None 不变。"""
    base = validate({'rated_power': 'rated_power'})
    plain = validate({'rated_power': 'rated_power'}, degraded_catalogs=None)
    assert plain == base, 'degraded_catalogs=None 时结果必须与既有行为逐字节一致'
    with_degraded = validate({'rated_power': 'rated_power'}, degraded_catalogs=['mysql_main'])
    # 文案含连接名称或 id（路由层兜底的等价口径：优先名称、无名回退 id）
    assert any('目录缓存读取失败' in e and ('mysql_main' in e or '主数据库' in e)
               for e in with_degraded['errors']), f'损坏连接应 block：{with_degraded["errors"]}'
    conn_items = [i for i in with_degraded['items'] if i['kind'] == 'connection' and i['id'] == 'mysql_main']
    assert conn_items and conn_items[-1]['status'] == 'invalid', f'应有 connection invalid 条目：{conn_items}'
    # 名称来自项目连接配置（非 id 回退）
    assert '主数据库' in conn_items[-1]['issues'][0], f'文案应含连接名称：{conn_items[-1]}'


if __name__ == '__main__':
    run('a1) 旧字符串字段映射语义不破坏（通过）', t_legacy_string_mapping)
    run('b1) 合法 database 长表 SOC（timeSeries 目标）通过', t_database_series_valid)
    run('b2) 身份表本行空匹配自动定位（等价直取）通过', t_database_identity_row_direct)
    run('b3) 缺当前实例绑定 block', t_database_missing_instance_binding)
    run('b4) match 引用序列属性 block', t_database_property_ref_series_blocked)
    run('b5) 常量与字段类型不兼容 block（数值／整数）', t_database_constant_type_mismatch)
    run('b6) timeSeries 漏 timestampField block', t_database_series_missing_timestamp_field)
    run('b7) timezone 非法 block', t_database_bad_timezone)
    run('b8) timeRange／时间序列 result 出现在 scalar 目标 block', t_database_time_range_on_scalar)
    run('b8b) timeSeries result 带 selection（聚合）block', t_database_series_selection_blocked)
    run('b8c) timeSeries result 带 missing（无记录策略）block', t_database_series_missing_blocked)
    run('b9) 表未选择为 pending 待配置', t_database_table_pending)
    run('b10) 目录不可用 → 元数据待核对 warning 不 block', t_database_catalog_unavailable_warning)
    run('b11) database 属性循环依赖检出', t_database_dependency_cycle)
    run('c1) Redis 直连 + identityField 参数合法通过', t_redis_direct_valid)
    run('c2) Redis source／connection 互斥 block（都空／都非空）', t_redis_source_connection_mutex)
    run('c3) Redis identityField 目录外字段 warning 不 block', t_redis_identity_field_catalog_warning)
    run('c4) Redis 绑定序列属性 block', t_redis_series_target_blocked)
    run('d1) computed 声明形态缺省 scalar 与序列目标不匹配 block', t_computed_shape_mismatch)
    run('d2) computed 声明形态匹配通过', t_computed_shape_match)
    run('e1) 未知 kind 报「属性来源方式无效」／非法结构报格式无效', t_unknown_kind)
    run('f1) 属性键在引用版本中不存在 block，原配置保留', t_property_key_missing_blocked)
    run('f2) field 来源字段有目录但不存在 block；无目录保持现状', t_field_missing_with_catalog_blocked)
    run('f3) degraded_catalogs 形参生效（None 时逐字节不变）', t_degraded_catalogs_param)
    if FAILURES:
        print(f'\n{len(FAILURES)} 项失败：{", ".join(FAILURES)}')
        sys.exit(1)
    print('\n全部通过')
