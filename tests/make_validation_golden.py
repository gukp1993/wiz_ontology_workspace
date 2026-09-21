"""生成 validate_project 的金样（golden）夹具。

用途：B2 拆分（validate_project → project_validation.py）前，先用**当前**实现
把一组覆盖性项目状态的 {errors, warnings, items} 完整落到
tests/fixtures/validation_golden.json；拆分后 tests/test_validation_split.py
逐样例回放比对，保证输出逐字节等价（含顺序）。

纯内存 dict 构造，不启动服务。按仓库测试隔离约定，未设 WIZ_WORKBENCH_ROOT
时自动落到临时目录（导入 workbench 需要定位数据根，但本脚本不写入任何数据）。
运行：python3 tests/make_validation_golden.py
"""
import copy
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if not os.environ.get('WIZ_WORKBENCH_ROOT'):
    os.environ['WIZ_WORKBENCH_ROOT'] = tempfile.mkdtemp(prefix='wiz_golden_maker_')

from workbench import projects  # noqa: E402  (import after WIZ_WORKBENCH_ROOT fallback)

# 账号体系（20260918）：validate_project 读取 flow/凭据按当前账号过滤，生成与回放必须同一账号
from pathlib import Path as _P
import sys as _sys
_sys.path.insert(0, str(_P(__file__).resolve().parent))
import auth_client as _auth_client
_auth_client.bind_fixture_user()

FIXTURE = Path(__file__).resolve().parent / 'fixtures' / 'validation_golden.json'


# --- 本体夹具 --------------------------------------------------------------------
# 覆盖：两个对象类型；属性含 timeSeries（直标与共享继承两种）、显示名称（共享继承）、
# dict 形式 mg:apiName；链接类型含 many-to-one（dict 形 cardinality）／one-to-many／未标注；
# 契约 guide_version 3 带 refs 结构化签名（object/base/property 三种），另带旧版函数。

STATION = {'@id': 'mg:Station', '@type': 'owl:Class', 'rdfs:label': '储能单元'}
BATTERY = {'@id': 'mg:Battery', '@type': 'owl:Class', 'rdfs:label': '电池包'}

SHARED_TITLE = {'@id': 'mg:sharedTitle', '@type': 'mg:SharedProperty',
                'mg:isDisplayName': {'@value': True}}
SHARED_SERIES = {'@id': 'mg:sharedSeries', '@type': 'mg:SharedProperty',
                 'mg:valueShape': 'timeSeries'}


def prop(api, dtype='double', domain='mg:Station', **kw):
    node = {'@id': f'mg:{api}', '@type': 'owl:DatatypeProperty', 'rdfs:label': api,
            'mg:apiName': api, 'rdfs:domain': {'@id': domain}, 'rdfs:range': {'@id': f'xsd:{dtype}'}}
    node.update(kw)
    return node


GRAPH = [
    dict(STATION), dict(BATTERY),
    # 显示名称经共享属性继承（derive_display_names 的合并路径）
    prop('station_name', 'string', **{'mg:sharedProperty': {'@id': 'mg:sharedTitle'}}),
    prop('rated_power', 'double'),
    # timeSeries 形态经共享属性继承（_value_shape 的 effective 路径）
    prop('soc', 'double', **{'mg:sharedProperty': {'@id': 'mg:sharedSeries'}}),
    prop('device_code', 'string'),
    prop('temp_avg', 'double'),
    # mg:apiName 的 {'@value': …} dict 形式（property_api 兼容分支）
    prop('health', 'double', **{'mg:apiName': {'@value': 'health'}}),
    prop('capacity', 'double', domain='mg:Battery'),
    dict(SHARED_TITLE), dict(SHARED_SERIES),
    # 链接类型：many-to-one 用 dict 形 cardinality（graph_value 兼容分支）
    {'@id': 'mg:hasBattery', '@type': 'owl:ObjectProperty', 'rdfs:label': '含电池包',
     'mg:cardinality': {'@value': 'many-to-one'}},
    {'@id': 'mg:belongsToStation', '@type': 'owl:ObjectProperty', 'rdfs:label': '所属单元',
     'mg:cardinality': 'one-to-many'},
    {'@id': 'mg:relatedUnit', '@type': 'owl:ObjectProperty', 'rdfs:label': '关联单元'},
]

FUNCTIONS = [
    {'id': 'fn-series', 'name': '序列取数', 'guide_version': 3,
     'inputs': [{'id': 'in1', 'name': '单元', 'ref': {'kind': 'object', 'id': 'mg:Station'}}],
     'outputs': [{'id': 'series', 'name': '序列输出', 'ref': {'kind': 'base', 'dataType': 'double'}}]},
    {'id': 'fn-health', 'name': '健康度计算', 'guide_version': 3,
     'inputs': [{'id': 'in1', 'name': '输入值', 'ref': {'kind': 'base', 'dataType': 'double'}}],
     'outputs': [{'id': 'health', 'name': '健康度', 'ref': {'kind': 'base', 'dataType': 'double'}}]},
    # 输出 ref 指向另一个属性定义 → computed 的跨属性口径警告
    {'id': 'fn-cross', 'name': '跨属性输出', 'guide_version': 3, 'inputs': [],
     'outputs': [{'id': 'out1', 'name': '输出', 'ref': {'kind': 'property', 'id': 'mg:health'}}]},
    {'id': 'fn-self', 'name': '自属性输出', 'guide_version': 3, 'inputs': [],
     'outputs': [{'id': 'out1', 'name': '输出', 'ref': {'kind': 'property', 'id': 'mg:rated_power'}}]},
    # 旧版函数：非契约，只读展示
    {'id': 'fn-legacy', 'name': '旧版函数', 'guide_version': 1, 'steps': []},
]


def ontology_state(graph=None, functions=None):
    return {'ontology': {'@context': {}, '@graph': copy.deepcopy(graph if graph is not None else GRAPH)},
            'workflow': {'functions': copy.deepcopy(functions if functions is not None else FUNCTIONS)}}


# --- 项目夹具 --------------------------------------------------------------------

MYSQL_MAIN = {'id': 'mysql_main', 'name': '主数据库', 'engine': 'mysql',
              'host': '127.0.0.1', 'port': 3306, 'database': 'demo'}
MYSQL_AUX = {'id': 'mysql_aux', 'name': '辅助库', 'engine': 'mysql',
             'host': '127.0.0.2', 'port': 3307, 'database': 'aux'}
REDIS_MAIN = {'id': 'redis_main', 'name': '缓存', 'engine': 'redis', 'host': '127.0.0.1', 'port': 6379}
ADAPTER_CONN = {'id': 'json1', 'name': '旧样例连接', 'adapter': 'json_fixture'}
BAD_ENGINE_CONN = {'id': 'bad1', 'name': '奇怪引擎', 'engine': 'mongodb', 'host': 'x'}
NONAME_CONN = {'id': 'noname1', 'engine': 'mysql', 'host': 'x'}

CATALOG = {
    'mysql_main': {'tables': [
        {'name': 'station', 'fields': [
            {'name': 'id', 'dataType': 'bigint', 'key': 'pri'},
            {'name': 'station_name', 'dataType': 'varchar'},
            {'name': 'rated_power', 'dataType': 'double'},
            {'name': 'device_code', 'dataType': 'varchar'},
            {'name': 'temp_avg', 'dataType': 'double'},
            {'name': 'health', 'dataType': 'double'}]},
        {'name': 'battery', 'fields': [
            {'name': 'id', 'dataType': 'bigint', 'key': 'pri'},
            {'name': 'station_id', 'dataType': 'bigint', 'key': 'uni'},
            {'name': 'capacity', 'dataType': 'double'},
            {'name': 'group_no', 'dataType': 'int'}]},
        {'name': 'telemetry', 'fields': [
            {'name': 'id', 'dataType': 'bigint'},
            {'name': 'object_id', 'dataType': 'bigint'},
            {'name': 'object_type', 'dataType': 'varchar'},
            {'name': 'metric_code', 'dataType': 'varchar'},
            {'name': 'value', 'dataType': 'double'},
            {'name': 'value_str', 'dataType': 'varchar'},
            {'name': 'seq', 'dataType': 'int'},
            {'name': 'sampled_at', 'dataType': 'datetime'},
            {'name': 'sampled_ts', 'dataType': 'bigint'},
            {'name': 'group_no', 'dataType': 'int'}]},
    ]},
    'mysql_aux': {'tables': [
        {'name': 'ext_table', 'fields': [
            {'name': 'station_ref', 'dataType': 'bigint'},
            {'name': 'ext_value', 'dataType': 'double'}]},
    ]},
}


def binding(object_type='Station', **kw):
    b = {'object_type': object_type, 'connection': 'mysql_main', 'table': 'station',
         'primary_key': 'id', 'title_key': '', 'properties': {}, 'relations': [],
         'sources': [], 'related_sources': []}
    b.update(kw)
    return b


def project(bindings=None, connections=None, implementations=None, parameters=None, catalogs=CATALOG):
    return {'projectId': 'p1', 'name': '金样项目', 'ontologyId': 'storage', 'ontologyVersion': '1',
            'connections': {'connections': connections
                            if connections is not None else [dict(MYSQL_MAIN), dict(REDIS_CONN_SAFE())]},
            'bindings': {'notice': '', 'object_bindings': bindings if bindings is not None else [],
                         'observation_binding': {}, 'source_candidates': [],
                         'catalogs': catalogs},
            'implementations': implementations if implementations is not None else [],
            'parameters': parameters if parameters is not None else {}}


def REDIS_CONN_SAFE():
    return dict(REDIS_MAIN)


def db_source(sid, **kw):
    s = {'id': sid, 'name': sid, 'kind': 'db', 'connection': 'mysql_main', 'table': 'battery',
         'matchLeft': 'id', 'matchRight': 'station_id', 'cardinality': 'one'}
    s.update(kw)
    return s


def impl(iid, contract_id, inputs=None, outputs=None, rule='return 1', **kw):
    record = {'id': iid, 'name': iid, 'contractId': contract_id,
              'inputBindings': inputs if inputs is not None else [],
              'outputDeclarations': outputs if outputs is not None else [], 'rule': rule}
    record.update(kw)
    return record


def series_binding_prop(**kw):
    """合法长表 SOC（timeSeries 目标）database 配置。"""
    config = {'kind': 'database', 'connection': 'mysql_main', 'table': 'telemetry',
              'lookup': {'match': [
                  {'field': 'object_id', 'operator': 'eq', 'value': {'kind': 'identityKey'}},
                  {'field': 'object_type', 'operator': 'eq', 'value': {'kind': 'constant', 'value': 'cluster'}},
                  {'field': 'metric_code', 'operator': 'eq', 'value': {'kind': 'constant', 'value': 'soc'}}],
                  'timeRange': {'field': 'sampled_at', 'start': {'kind': 'context', 'name': 'startTime'},
                                'end': {'kind': 'context', 'name': 'endTime'}, 'bounds': '[start,end)'}},
              'result': {'valueField': 'value', 'timestampField': 'sampled_at',
                         'timestampEncoding': 'datetime', 'timezone': 'Asia/Shanghai'}}
    config.update(kw)
    return config


SAMPLES = []


def sample(name, state, onto=None):
    SAMPLES.append({'name': name, 'state': state, 'ontology_state': onto if onto is not None else ontology_state()})


# --- 1. 基础与连接 ----------------------------------------------------------------

sample('01_empty_project', project())

sample('02_connections_invalid', project(bindings=[], connections=[
    dict(MYSQL_MAIN), dict(MYSQL_MAIN),  # 名称重复
    {'id': 'c3', 'name': '', 'engine': 'mysql'},  # 未命名
    dict(BAD_ENGINE_CONN),  # 引擎不支持
    {'id': 'c5', 'name': '杂项', 'engine': 'redis', 'adapter': 'json_fixture'},  # adapter 豁免
    dict(ADAPTER_CONN),  # 旧 adapter 连接原样接受
    dict(NONAME_CONN),  # 未命名 + 无 adapter
    'not-a-dict',  # 非法条目被过滤
]))

sample('03_binding_connection_missing', project(bindings=[binding(connection='ghost_conn')]))
# 身份来源必填项：允许保存草稿，但任何一项缺失均阻止项目发布。
for field in ('connection', 'table', 'primary_key'):
    sample('03_required_identity_' + field, project(bindings=[binding(**{field: ''})]))
sample('03_whitespace_connection', project(bindings=[binding(connection='  ', properties={'rated_power':'rated_power'})]))
sample('03_explicit_fixture_connection', project(bindings=[binding(connection='json1')], connections=[dict(ADAPTER_CONN)]))

# --- 2. 对象绑定 ------------------------------------------------------------------

sample('04_binding_unconfigured', project(bindings=[binding(connection='', table='', primary_key='')]))

sample('05_binding_unknown_object_type', project(bindings=[binding(object_type='Ghost')]))

sample('06_binding_duplicate_object_type', project(
    bindings=[binding(properties={'rated_power': 'rated_power'}), binding(table='battery')]))

sample('07_title_key_derived_but_unmapped_warning', project(
    bindings=[binding(properties={'rated_power': 'rated_power'})]))
# derive_display_names 会把共享标记的 station_name 写进 title_key（未绑定 → warning + 副作用）

sample('08_binding_stale_catalog_hints', project(bindings=[
    binding(table='ghost_table'),
    binding(object_type='Battery', table='battery', primary_key='ghost_pk',
             properties={'capacity': 'capacity'})]))

sample('09_binding_malformed', project(bindings=[
    binding(object_type='Battery', table='battery', properties=None,
            relations=['not-a-dict', {'relation': 'hasBattery'}])]))
# properties=None / relations 含非法条目走容错分支。两个已知易碎点未纳入金样：
# 非 dict 绑定会使 derive_display_names 抛 AttributeError；properties=None 且
# title_key 非空（本体标记了显示名）时 `in None` 抛 TypeError（现有行为，见会话报告）。

# --- 3. 补充来源 ------------------------------------------------------------------

sample('10_sources_new_and_legacy_mix', project(bindings=[binding(
    connection='',
    sources=[db_source('s1'),
             db_source('s2', cardinality='many', connection='mysql_aux', table='ext_table',
                       matchLeft='id', matchRight='station_ref'),
             {'id': 'r1', 'name': '状态缓存', 'kind': 'redis', 'connection': 'redis_main',
              'keyTemplate': 'status:{id}', 'cardinality': 'one'},
             {'id': 'l2', 'name': '新格式覆盖', 'kind': 'db', 'connection': 'mysql_main',
              'table': 'battery', 'matchLeft': 'id', 'matchRight': 'station_id', 'cardinality': 'one'}],
    related_sources=[{'id': 'l1', 'name': '旧补充来源', 'table': 'battery',
                      'source_field': 'id', 'target_field': 'station_id'},
                     {'id': 'l2', 'name': '旧同名来源', 'table': 'battery',
                      'source_field': 'id', 'target_field': 'station_id'}])]))
# identity connection 为空（adapter 时代旧数据豁免）+ 新旧来源按 id 合并 + 跨库登记 warning

sample('11_sources_invalid', project(bindings=[binding(
    sources=[{'kind': 'db'},  # 无 id
             db_source('dup'), db_source('dup', name='dup2'),  # 同列表内 id 重复
             db_source('noname', name=''),  # 名称未填写
             {'id': 'noconn', 'name': '未绑连接', 'kind': 'db'},  # 未绑定连接
             db_source('ghostconn', connection='ghost'),  # 连接不存在
             db_source('wrongengine', connection='redis_main'),  # db 来源需要 MySQL
             {'id': 'nofields', 'name': '缺匹配', 'kind': 'db', 'connection': 'mysql_main'},  # 表/匹配字段未填
             db_source('badcard', cardinality='some'),  # 匹配数量非法
             {'id': 'file1', 'name': '文件', 'kind': 'file'},  # 来源类型无效
             {'id': 'r2', 'name': '错引擎', 'kind': 'redis', 'connection': 'mysql_main'},
             'not-a-source'],  # 来源配置格式无效
)]))

sample('12_sources_catalog_hints', project(bindings=[binding(sources=[
    db_source('s1', table='ghost_table'),
    db_source('s2', matchRight='ghost_field'),
    db_source('s3', matchLeft='ghost_left'),
])]))
# 来源表不在目录 / matchRight 不在表目录 / matchLeft 不在身份表目录

sample('13_sources_catalog_absent', project(bindings=[binding(sources=[
    db_source('s1', connection='mysql_aux', table='ext_table', matchRight='station_ref'),
])], catalogs={'mysql_main': CATALOG['mysql_main']}))
# 辅助库目录缺失：目录相关提示跳过（catalogs 无该连接条目）

# --- 4. 属性来源：string / 非法 ----------------------------------------------------

sample('14_props_string_and_malformed', project(bindings=[binding(properties={
    'device_code': 'device_code',  # 旧字符串直取：合法
    'rated_power': '',  # 未填写字段名（pending）
    'temp_avg': 42,  # 非字符串非对象：格式无效
    'health': {'kind': 'quantum', 'field': 'x'},  # 未知 kind
    'ghost_prop': 'device_code',  # 本体中不存在的属性（无节点，仍按 scalar 处理）
})]))

# --- 5. 属性来源：field / related（补充来源取数） -----------------------------------

sample('15_props_field_related', project(bindings=[binding(
    sources=[db_source('s_many', cardinality='many')],
    properties={
        'rated_power': {'kind': 'field', 'source': 's_many', 'field': 'capacity',
                        'selection': 'latest', 'timeField': 'id', 'tieBreaker': 'group_no'},
        'temp_avg': {'kind': 'field', 'source': 's_many', 'field': 'capacity',
                     'selection': 'latest', 'timeField': 'id'},  # 无次级排序 → warning
        'health': {'kind': 'field', 'source': 's_many', 'field': 'capacity',
                   'selection': 'latest'},  # 缺时间字段 → block
        'station_name': {'kind': 'field', 'source': 's_many', 'field': 'capacity',
                         'selection': 'average'},  # 平均：合法
        'device_code': {'kind': 'field', 'source': 's_many', 'field': 'capacity',
                        'selection': 'sum'},  # 字符串属性汇总 → block
        'rated_power2': {'kind': 'related', 'source': 'ghost', 'field': ''},  # 来源不存在 + 字段未填
    })]))
# 注意 rated_power2/station_name 等非本体属性 api 名，node 为 None 走容错分支

# --- 6. 属性来源：redis -----------------------------------------------------------

sample('16_props_redis_get_direct_valid', project(bindings=[binding(properties={
    'device_code': {'kind': 'redis', 'connection': 'redis_main', 'command': 'GET',
                    'key': 'status:{room}', 'params': {'room': {'from': 'identityField', 'field': 'id'}},
                    'conversion': 'number', 'missing': 'null'},
})]))

sample('17_props_redis_variants', project(bindings=[binding(properties={
    'rated_power': {'kind': 'redis', 'source': 'r1', 'connection': 'redis_main', 'command': 'GET',
                    'key': 'k', 'params': {}},  # 来源与连接同时设置 → block
    'temp_avg': {'kind': 'redis', 'command': 'GET', 'key': 'k', 'params': {}},  # 都空 → block
    'health': {'kind': 'redis', 'connection': 'mysql_main', 'command': 'GET', 'key': 'k',
               'params': {}},  # 直连引擎错 → block
    'device_code': {'kind': 'redis', 'source': 's_db', 'command': 'GET', 'key': 'k',
                    'params': {}},  # 登记来源非 redis → block
    'station_name': {'kind': 'redis', 'source': 'ghost', 'command': 'GET', 'key': 'k',
                     'params': {}},  # 来源不存在 → block
    'ghost_prop': {'kind': 'redis', 'connection': 'redis_main', 'command': 'HGET', 'key': 'h:{a}',
                   'params': {}},  # HGET 缺 Hash 字段 + 占位符未绑定
    'ghost2': {'kind': 'redis', 'connection': 'redis_main', 'command': 'GET', 'key': 'k:{p1}:{p2}',
               'params': {'p1': {'from': 'primary'},
                          'p2': {'from': 'bogus'}}},  # 参数绑定配置无效
    'ghost3': {'kind': 'redis', 'connection': 'redis_main', 'command': 'GET', 'key': 'k:{pp}',
               'params': {'pp': {'from': 'property', 'property': 'ghost_prop'}}},  # 参数属性非字段来源
    'ghost4': {'kind': 'redis', 'connection': 'redis_main', 'command': 'GET', 'key': 'k:{pf}',
               'params': {'pf': {'from': 'property', 'property': 'device_code'}}},  # 合法属性参数
    'ghost5': {'kind': 'redis', 'connection': 'redis_main', 'command': 'GET', 'key': 'k:{pi}',
               'params': {'pi': {'from': 'identityField'}}},  # 身份表字段未填
    'ghost6': {'kind': 'redis', 'connection': 'redis_main', 'command': 'GET', 'key': 'k:{px}',
               'params': {'px': {'from': 'identityField', 'field': 'ghost_field'}}},  # 目录外字段 warning
    'ghost7': {'kind': 'redis', 'connection': 'redis_main', 'command': 'DEL', 'key': 'k',
               'params': {}, 'conversion': 'json', 'missing': 'zero'},  # 读取方式/转换/缺失均非法
})], parameters={}))
# device_code 同对象为合法字符串字段映射，供参数属性绑定使用；params 目标属性含 self 引用见下例

sample('18_props_redis_self_param_and_series', project(bindings=[binding(properties={
    'device_code': {'kind': 'redis', 'connection': 'redis_main', 'command': 'GET', 'key': 'k:{self}',
                    'params': {'self': {'from': 'property', 'property': 'device_code'}}},  # 参数引用自身
    'soc': {'kind': 'redis', 'connection': 'redis_main', 'command': 'GET', 'key': 'k',
            'params': {}},  # 序列属性绑 Redis → block
})]))

# --- 7. 属性来源：database（scalar）-----------------------------------------------

def scalar_db(match=None, **kw):
    config = {'kind': 'database', 'connection': 'mysql_main', 'table': 'telemetry',
              'lookup': {'match': match if match is not None else [
                  {'field': 'object_id', 'operator': 'eq', 'value': {'kind': 'identityKey'}}]},
              'result': {'valueField': 'value'}}
    config.update(kw)
    return config


sample('19_props_database_match_value_kinds', project(bindings=[binding(properties={
    'rated_power': scalar_db(match=[
        {'field': 'object_id', 'operator': 'eq', 'value': {'kind': 'identityKey'}},
        {'field': 'metric_code', 'operator': 'eq', 'value': {'kind': 'constant', 'value': 'power'}},
        {'field': 'group_no', 'operator': 'eq', 'value': {'kind': 'constant', 'value': '5'}},
        {'field': 'seq', 'operator': 'eq', 'value': {'kind': 'constant', 'value': '3.5'}}]),  # int 字段小数
    'device_code': scalar_db(match=[
        {'field': 'id', 'operator': 'eq', 'value': {'kind': 'identityField', 'field': 'id'}},
        {'field': 'metric_code', 'operator': 'eq', 'value': {'kind': 'parameter', 'parameter': 'p1'}}]),  # 参数存在
    'temp_avg': scalar_db(match=[
        {'field': 'id', 'operator': 'eq', 'value': {'kind': 'identityField', 'field': 'ghost_field'}}]),  # 目录外 warning
    'health': scalar_db(match=[
        {'field': 'id', 'operator': 'eq', 'value': {'kind': 'identityField'}}]),  # 身份字段未填
    'station_name': scalar_db(match=[
        {'field': 'metric_code', 'operator': 'eq', 'value': {'kind': 'property', 'property': 'device_code'}}]),  # 引用合法属性
    'ghost_prop': scalar_db(match=[
        {'field': 'metric_code', 'operator': 'eq', 'value': {'kind': 'property', 'property': ''}}]),  # 属性未填
    'ghost2': scalar_db(match=[
        {'field': 'metric_code', 'operator': 'eq', 'value': {'kind': 'property', 'property': 'ghost2'}}]),  # 引用自身
    'ghost3': scalar_db(match=[
        {'field': 'metric_code', 'operator': 'eq', 'value': {'kind': 'property', 'property': 'unmapped'}}]),  # 未配置映射
    'ghost4': scalar_db(match=[
        {'field': 'metric_code', 'operator': 'eq', 'value': {'kind': 'property', 'property': 'soc'}}]),  # 引用序列属性
    'ghost5': scalar_db(match=[
        {'field': 'metric_code', 'operator': 'eq', 'value': {'kind': 'constant', 'value': '  '}}]),  # 常量未填
    'ghost6': scalar_db(match=[
        {'field': 'metric_code', 'operator': 'eq', 'value': {'kind': 'parameter', 'parameter': 'nope'}}]),  # 参数不存在
    'ghost7': scalar_db(match=[
        {'field': 'metric_code', 'operator': 'eq', 'value': {'kind': 'cosmic'}}]),  # 比较值来源无效
    'ghost8': scalar_db(match=[
        {'field': '', 'operator': 'eq', 'value': {'kind': 'constant', 'value': 'x'}}]),  # 匹配字段未选
    'ghost9': scalar_db(match=[
        {'field': 'metric_code', 'operator': 'gt', 'value': {'kind': 'constant', 'value': 'x'}}]),  # 操作符非 eq
    'ghost10': scalar_db(match=['not-a-cond']),  # 匹配条件格式无效
})], parameters={'p1': {'value': 'power'}}))

sample('20_props_database_no_instance_binding', project(bindings=[binding(properties={
    'rated_power': scalar_db(match=[
        {'field': 'metric_code', 'operator': 'eq', 'value': {'kind': 'constant', 'value': 'power'}}]),
})]))

sample('21_props_database_identity_row_and_missing_pk', project(bindings=[
    binding(table='telemetry', primary_key='', properties={
        'rated_power': scalar_db(match=[
            {'field': 'object_id', 'operator': 'eq', 'value': {'kind': 'identityKey'}}])}),  # 主键缺失 + identityKey
    binding(object_type='Battery', table='battery', properties={
        'capacity': {'kind': 'database', 'connection': 'mysql_main', 'table': 'battery',
                     'lookup': {'match': []}, 'result': {'valueField': 'capacity'}}}),  # 身份表本行空匹配
]))

sample('22_props_database_scalar_result_variants', project(bindings=[binding(properties={
    'rated_power': scalar_db(result={'valueField': 'value', 'selection': 'latest',
                                     'timestampField': 'sampled_ts', 'timestampEncoding': 'epochSeconds',
                                     'secondarySort': {'field': 'seq', 'order': 'descending'}}),
    'temp_avg': scalar_db(result={'valueField': 'value', 'selection': 'latest',
                                  'timestampField': 'sampled_at', 'timestampEncoding': 'datetime',
                                  'timezone': 'Asia/Shanghai'}),  # 合法 latest
    'health': scalar_db(result={'valueField': 'value', 'selection': 'latest',
                                'timestampField': 'sampled_at', 'timestampEncoding': 'datetime',
                                'timezone': 'Asia/Shanghai', 'secondarySort': {'field': 'ghost_f'}}),  # 次级排序目录外 warning
    'device_code': scalar_db(result={'valueField': 'value', 'selection': 'median'}),  # 多行方式非法
    'station_name': scalar_db(result={'valueField': 'value', 'selection': 'sum'}),  # 合法汇总
    'ghost1': scalar_db(result={'valueField': 'value_str', 'selection': 'average'}),  # 汇总非数值字段
    'ghost2': scalar_db(result={'valueField': ''}),  # 值字段未选
    'ghost3': scalar_db(result={'valueField': 'ghost_field'}),  # 值字段目录外 warning
    'ghost4': scalar_db(result={'valueField': 'value', 'selection': 'latest',
                                'timestampField': 'sampled_at', 'timestampEncoding': 'datetime',
                                'timezone': 'Asia/Shanghai',
                                'secondarySort': {'field': 'seq', 'order': 'shuffle'}}),  # 次级排序方向非法
    'ghost5': scalar_db(result={'valueField': 'value', 'missing': 'default'}),  # 缺失策略非法
    'ghost6': scalar_db(result={'valueField': 'value', 'timestampField': 'sampled_at'}),  # scalar 残留序列字段
})]))

sample('23_props_database_time_range_on_scalar', project(bindings=[binding(properties={
    'rated_power': {'kind': 'database', 'connection': 'mysql_main', 'table': 'telemetry',
                    'lookup': {'match': [{'field': 'object_id', 'operator': 'eq',
                                          'value': {'kind': 'identityKey'}}],
                               'timeRange': {'field': 'sampled_at',
                                             'start': {'kind': 'context', 'name': 'startTime'},
                                             'end': {'kind': 'context', 'name': 'endTime'},
                                             'bounds': '[start,end)'}},
                    'result': {'valueField': 'value'}},
})]))

sample('24_props_database_catalog_absent_warning', project(bindings=[binding(properties={
    'rated_power': scalar_db(),
})], catalogs={}))

# --- 8. 属性来源：database（timeSeries）-------------------------------------------

sample('25_props_database_series_valid', project(bindings=[binding(properties={
    'soc': series_binding_prop(),
})]))

sample('26_props_database_series_variants', project(bindings=[binding(properties={
    'soc': series_binding_prop(result={'valueField': 'value'}),
    'ghost1': series_binding_prop(result={'valueField': ''}),  # 值字段未选
    'ghost2': series_binding_prop(result={'valueField': 'value', 'selection': 'sum'}),  # 序列不支持聚合
    'ghost3': series_binding_prop(result={'valueField': 'value', 'missing': 'error'}),  # 序列不支持无记录策略
    'ghost4': series_binding_prop(result={'valueField': 'value', 'timestampField': 'sampled_at',
                                          'timestampEncoding': 'datetime', 'timezone': 'Asia/Shanghai',
                                          'order': 'shuffle'}),  # 排序方式非法
    'ghost5': series_binding_prop(result={'valueField': 'value', 'timestampField': 'sampled_at',
                                          'timestampEncoding': 'iso'}),  # 编码非法
    'ghost6': series_binding_prop(result={'valueField': 'value', 'timestampField': 'seq',
                                          'timestampEncoding': 'datetime',
                                          'timezone': 'Asia/Shanghai'}),  # datetime 编码配数值字段
    'ghost7': series_binding_prop(result={'valueField': 'value', 'timestampField': 'value_str',
                                          'timestampEncoding': 'epochMillis'}),  # 数值编码配文本字段
    'ghost8': series_binding_prop(result={'valueField': 'value', 'timestampField': 'sampled_at',
                                          'timestampEncoding': 'datetime'}),  # 缺时区
    'ghost9': series_binding_prop(result={'valueField': 'value', 'timestampField': 'sampled_at',
                                          'timestampEncoding': 'datetime', 'timezone': 'Mars/Olympus'}),
    'ghost10': series_binding_prop(result={'valueField': 'value', 'timestampField': 'sampled_at',
                                           'timestampEncoding': 'datetime', 'timezone': 'Asia/Shanghai',
                                           'duplicateTimestamp': 'merge'}),  # 重复时刻处理非法
    'ghost11': series_binding_prop(result={'valueField': 'value', 'timestampField': 'sampled_at',
                                           'timestampEncoding': 'datetime', 'timezone': 'Asia/Shanghai',
                                           'duplicateTimestamp': 'secondarySort'}),  # 次级排序缺字段
    'ghost12': series_binding_prop(result={'valueField': 'value', 'timestampField': 'sampled_at',
                                           'timestampEncoding': 'datetime', 'timezone': 'Asia/Shanghai',
                                           'duplicateTimestamp': 'secondarySort',
                                           'secondarySort': {'field': 'ghost_f'}}),  # 次级排序目录外 warning
    'ghost13': series_binding_prop(result={'valueField': 'value', 'timestampField': 'sampled_at',
                                           'timestampEncoding': 'datetime', 'timezone': 'Asia/Shanghai',
                                           'duplicateTimestamp': 'secondarySort',
                                           'secondarySort': {'field': 'seq', 'order': 'weird'}}),
    'ghost14': series_binding_prop(result={'valueField': 'value', 'timestampField': 'sampled_at',
                                           'timestampEncoding': 'datetime', 'timezone': 'Asia/Shanghai',
                                           'duplicateTimestamp': 'secondarySort',
                                           'secondarySort': {'field': 'seq', 'order': 'descending'}}),  # 合法
})], ))

sample('27_props_database_series_time_range_variants', project(bindings=[binding(properties={
    'soc': dict(series_binding_prop(), lookup={'match': [
        {'field': 'object_id', 'operator': 'eq', 'value': {'kind': 'identityKey'}}],
        'timeRange': {'field': 'ghost_field',
                      'start': {'kind': 'context', 'name': 'startTime'},
                      'end': {'kind': 'context', 'name': 'endTime'}, 'bounds': '[start,end)'}}),
    'ghost1': dict(series_binding_prop(), lookup={'match': [
        {'field': 'object_id', 'operator': 'eq', 'value': {'kind': 'identityKey'}}],
        'timeRange': {'field': 'sampled_at'}}),  # 起止缺省 → block
    'ghost2': dict(series_binding_prop(), lookup={'match': [
        {'field': 'object_id', 'operator': 'eq', 'value': {'kind': 'identityKey'}}],
        'timeRange': {'field': 'sampled_at',
                      'start': {'kind': 'literal', 'value': '2026-01-01'},
                      'end': {'kind': 'context', 'name': 'endTime'}, 'bounds': '[start,end)'}}),
    'ghost3': dict(series_binding_prop(), lookup={'match': [
        {'field': 'object_id', 'operator': 'eq', 'value': {'kind': 'identityKey'}}],
        'timeRange': {'field': 'sampled_at',
                      'start': {'kind': 'context', 'name': 'startTime'},
                      'end': {'kind': 'context', 'name': 'endTime'}, 'bounds': '(start,end)'}}),
})]))

sample('28_props_database_direct_shape_series', project(bindings=[binding(properties={
    # 本体直标 timeSeries（不经共享继承）：soc2 节点带 mg:valueShape
    'soc': series_binding_prop(),
})], ), onto=ontology_state(graph=GRAPH + [prop('soc2', 'double', **{'mg:valueShape': 'timeSeries'})]))
# 注：soc2 未配置映射，仅丰富本体；序列本体形态直标样本见 28b
sample('28b_props_database_series_direct_marker', project(
    bindings=[binding(properties={'soc2': series_binding_prop()})]),
    onto=ontology_state(graph=GRAPH + [prop('soc2', 'double', **{'mg:valueShape': 'timeSeries'})]))

sample('29_props_database_cycle', project(bindings=[binding(properties={
    'rated_power': {'kind': 'database', 'connection': 'mysql_main', 'table': 'telemetry',
                    'lookup': {'match': [
                        {'field': 'object_id', 'operator': 'eq', 'value': {'kind': 'identityKey'}},
                        {'field': 'metric_code', 'operator': 'eq',
                         'value': {'kind': 'property', 'property': 'device_code'}}]},
                    'result': {'valueField': 'value'}},
    'device_code': {'kind': 'database', 'connection': 'mysql_main', 'table': 'telemetry',
                    'lookup': {'match': [
                        {'field': 'object_id', 'operator': 'eq', 'value': {'kind': 'identityKey'}},
                        {'field': 'metric_code', 'operator': 'eq',
                         'value': {'kind': 'property', 'property': 'rated_power'}}]},
                    'result': {'valueField': 'value'}},
    'temp_avg': {'kind': 'database', 'connection': 'mysql_main', 'table': 'telemetry',
                 'lookup': {'match': [
                     {'field': 'object_id', 'operator': 'eq', 'value': {'kind': 'identityKey'}},
                     {'field': 'metric_code', 'operator': 'eq',
                      'value': {'kind': 'property', 'property': 'device_code'}}]},
                 'result': {'valueField': 'value'}},  # 环上节点的下游：同样标记循环
})]))

# --- 9. 属性来源：computed ---------------------------------------------------------

sample('30_props_computed_valid', project(
    bindings=[binding(properties={'soc': {'kind': 'computed', 'implementation': 'impl1', 'output': 'series'}})],
    implementations=[impl('impl1', 'fn-series', rule='return series',
                          outputs=[{'outputId': 'series', 'valueShape': 'timeSeries'}])]))

sample('31_props_computed_variants', project(
    bindings=[binding(properties={
        'soc': {'kind': 'computed', 'implementation': 'impl1', 'output': 'series'},  # 形态不匹配（缺省 scalar）
        'rated_power': {'kind': 'computed', 'implementation': 'ghost', 'output': 'x'},  # 实现不存在
        'temp_avg': {'kind': 'computed', 'implementation': 'impl2', 'output': 'out1'},  # 契约不存在（fn-legacy）
        'health': {'kind': 'computed', 'implementation': 'impl3', 'output': 'nope'},  # 输出未声明
        'device_code': {'kind': 'computed', 'implementation': 'impl4', 'output': 'out1'},  # 跨属性 ref warning
        'station_name': {'kind': 'computed', 'implementation': 'impl5', 'output': 'out1'},  # 自属性 ref：无 warning
    })],
    implementations=[
        impl('impl1', 'fn-series', outputs=[{'outputId': 'series'}]),
        impl('impl2', 'fn-legacy', outputs=[{'outputId': 'out1'}]),
        impl('impl3', 'fn-health', outputs=[{'outputId': 'health'}]),
        impl('impl4', 'fn-cross', outputs=[{'outputId': 'out1'}]),
        impl('impl5', 'fn-self', outputs=[{'outputId': 'out1'}]),
    ]))

# --- 10. 链接映射 ------------------------------------------------------------------

BATTERY_BINDING = dict(binding(object_type='Battery', table='battery',
                               properties={'capacity': 'capacity'}))


def link(**kw):
    base = {'relation': 'hasBattery', 'target_type': 'Battery',
            'sourceId': '', 'field': 'station_id', 'targetSourceId': '', 'targetField': 'id'}
    base.update(kw)
    return base


sample('32_links_old_format', project(bindings=[
    binding(relations=[{'relation': 'hasBattery', 'target_type': 'Battery', 'column': 'station_id'},
                       'not-a-link']),
    BATTERY_BINDING]))

sample('33_links_new_valid', project(bindings=[
    binding(relations=[link(),
                       link(relation='belongsToStation', target_type='Station', field='station_id',
                            targetField='id')]),  # 反向链接（Battery→Station 在 Station 绑定上仅测结构
    BATTERY_BINDING]))
# 注：第二条件的 many-to-one 唯一键在 catalog battery.id(key=pri) 上验证

sample('34_links_variants', project(bindings=[
    binding(relations=[
        link(sourceId='ghost'),  # 端点来源不存在
        link(sourceId='r1'),  # Redis 来源不能作链接端点
        link(sourceId='s_many'),  # 多条匹配来源
        link(field=''),  # 起点字段未选
        link(relation='ghostRel'),  # 链接类型不存在
        link(target_type='Ghost'),  # 终点对象类型不存在
        link(target_type='Device'),  # 终点对象未映射
        link(targetField=''),  # 目标字段未选
        link(targetField='group_no'),  # 非唯一键
        link(targetField='ghost_col'),  # 唯一性未知 warning
    ], sources=[{'id': 'r1', 'name': '缓存', 'kind': 'redis', 'connection': 'redis_main'},
                db_source('s_many', cardinality='many')]),
    BATTERY_BINDING]))

sample('35_links_one_to_one_target', project(bindings=[
    binding(relations=[link(relation='belongsToStation', target_type='Battery',
                            field='id', targetField='station_id')]),  # one-to-many 不查唯一键
    BATTERY_BINDING]))

# --- 11. 计算实现 ------------------------------------------------------------------

sample('36_impl_valid', project(
    bindings=[binding()],
    implementations=[impl('impl1', 'fn-health', rule='return in1',
                          inputs=[{'inputId': 'in1', 'source': {'kind': 'constant', 'value': '0.5'}}],
                          outputs=[{'outputId': 'health'}])]))

sample('37_impl_unbound_and_rule_pending', project(
    bindings=[binding()],
    implementations=[impl('impl1', 'fn-health', rule='',
                          outputs=[{'outputId': 'health'}])]))  # 输入未绑定 + 规则未填 → unconfigured

sample('38_impl_variants', project(
    bindings=[binding()],
    implementations=[
        impl('impl1', 'ghost-fn'),  # 契约不存在
        impl('impl2', 'fn-legacy', outputs=[{'outputId': 'x'}]),  # 旧版函数未迁移
        impl('impl3', 'fn-health', outputs=[], inputs=[
            {'inputId': 'in1', 'source': {'kind': 'object', 'objectType': 'mg:Ghost'}},
            {'inputId': 'in1', 'source': {'kind': 'object', 'objectType': 'mg:Station'}},
            {'inputId': 'in1', 'source': {'kind': 'field', 'objectType': 'Station', 'property': 'ghost'}},
            {'inputId': 'in1', 'source': {'kind': 'field', 'objectType': 'Ghost', 'property': 'x'}},
            {'inputId': 'in1', 'source': {'kind': 'constant', 'value': ' '}},
            {'inputId': 'in1', 'source': {'kind': 'bogus'}},
        ]),
        impl('impl4', 'fn-health', inputs=[
            {'inputId': 'in1', 'source': {'kind': 'output', 'implementation': 'ghost', 'outputId': 'x'}},
            {'inputId': 'in1', 'source': {'kind': 'output', 'implementation': 'impl3', 'outputId': 'ghost'}},
        ], outputs=[{'outputId': 'health'}], rule='chain'),
        'not-an-impl',  # 非法条目被过滤
    ]))
# impl3 同时覆盖：输出未声明 + 输入绑定各类错误；impl4 覆盖 output 引用不存在/未声明

sample('39_impl_field_input_allowed_keys', project(
    bindings=[binding(properties={'rated_power': 'rated_power'})],
    implementations=[impl('impl1', 'fn-health', rule='ok',
                          inputs=[{'inputId': 'in1', 'source': {'kind': 'field', 'objectType': 'Station',
                                                                'property': 'rated_power'}}],
                          outputs=[{'outputId': 'health'}])]))  # 主键/字符串映射字段在允许集内

sample('40_impl_output_cycle', project(
    bindings=[binding()],
    implementations=[
        impl('implA', 'fn-health', rule='a', outputs=[{'outputId': 'health'}], inputs=[
            {'inputId': 'in1', 'source': {'kind': 'output', 'implementation': 'implB', 'outputId': 'health'}}]),
        impl('implB', 'fn-health', rule='b', outputs=[{'outputId': 'health'}], inputs=[
            {'inputId': 'in1', 'source': {'kind': 'output', 'implementation': 'implA', 'outputId': 'health'}}]),
    ]))

# --- 12. 契约覆盖与参数 --------------------------------------------------------

sample('41_parameters_redis_binding', project(
    bindings=[binding(properties={
        'device_code': {'kind': 'redis', 'source': 'r1', 'command': 'GET', 'key': 'k:{p}',
                        'params': {'p': {'from': 'primary'}}},
    }, sources=[{'id': 'r1', 'name': '缓存', 'kind': 'redis', 'connection': 'redis_main'}])],
    parameters={'p1': {'value': 'x', 'note': '项目参数'}}))
# 登记来源 + primary 参数绑定；同时携带一个未被引用的项目参数


# Unified dataType: project output types are derived from the contract without a shape selector.
for suffix, ref in [('base_series', {'kind':'base','dataType':'timeSeries','valueType':'double'}),
                    ('property_series', {'kind':'property','id':'mg:soc'}),
                    ('base_number_mismatch', {'kind':'base','dataType':'double'})]:
    contract = {'id':'unified-series','name':'SOC查询','guide_version':3,'inputs':[],
                'outputs':[{'id':'series','name':'SOC序列','ref':ref}]}
    sample('42_type_' + suffix, project(
        bindings=[binding(properties={'soc':{'kind':'computed','implementation':'unified-impl','output':'series'}})],
        implementations=[impl('unified-impl','unified-series',outputs=[{'outputId':'series'}])]),
        onto=ontology_state(functions=[contract]))


# 项目独立取值规则，不依赖本体契约；保留既有金样不变。
QUERY_RULE = json.loads((Path(__file__).parent / 'fixtures/soc_query_rule.json').read_text())
QUERY_ONTOLOGY = ontology_state(functions=[])
next(n for n in QUERY_ONTOLOGY['ontology']['@graph'] if n['@id']=='mg:sharedSeries')['rdfs:range']={'@id':'xsd:double'}
for suffix in ['valid','future_reference','missing_identity','wrong_range','wrong_object','wrong_output','scalar_target']:
    rule = copy.deepcopy(QUERY_RULE)
    target = 'soc'
    output = 'series'
    if suffix=='future_reference': rule['steps'][0]['table']='{{steps.storage.sampleTable}}'
    if suffix=='missing_identity': rule['steps'][0]['where'][2]['value']='1'
    if suffix=='wrong_range': rule['steps'][-1]['where'][1]['op']='gte'
    if suffix=='wrong_object': rule['objectType']='Battery'
    if suffix=='wrong_output': output='ghost'
    if suffix=='scalar_target': target='rated_power'
    sample('43_query_rule_'+suffix, project(bindings=[binding(properties={target:{'kind':'computed','implementation':rule['id'],'output':output}})],implementations=[rule]),onto=QUERY_ONTOLOGY)


REUSABLE_QUERY_RULE = copy.deepcopy(QUERY_RULE)
REUSABLE_QUERY_RULE.update(schemaVersion=2, name='通用采样查询')
REUSABLE_QUERY_RULE.pop('objectType')
for w in REUSABLE_QUERY_RULE['steps'][0]['where']:
    w['value'] = '{{inputs.' + w['field'] + '}}'
for suffix in ['unbound', 'valid_inputs', 'missing_inputs', 'literal_id', 'reuse_across_objects']:
    args={'model_name':'station','attr_name':'soc','model_id':'{id}'}
    source={'kind':'computed','implementation':REUSABLE_QUERY_RULE['id'],'output':'series','inputs':args}
    if suffix=='missing_inputs':source.pop('inputs')
    if suffix=='literal_id':args['model_id']='1'
    bindings=[] if suffix=='unbound' else [binding(properties={'soc':source})]
    onto=copy.deepcopy(QUERY_ONTOLOGY)
    if suffix=='reuse_across_objects':
        onto['ontology']['@graph'].append(prop('battery_soc',domain='mg:Battery',**{'mg:valueShape':'timeSeries'}))
        bindings.append(binding(object_type='Battery',table='battery',properties={'battery_soc':{**source,'inputs':{'model_name':'battery','attr_name':'battery_soc','model_id':'{id}'}}}))
    sample('44_reusable_rule_'+suffix,project(bindings=bindings,implementations=[REUSABLE_QUERY_RULE]),onto=onto)


# --- 13. 无表对象与关联聚合（任务 B）----------------------------------------------
# 新样例只经 sample 级自定义本体扩展（GRAPH + …），既有样例的默认本体零变化。
# 覆盖：registered 身份（多实例）、registered 属性（登记信息→显示名称）、
# aggregate 属性（SUM、共享定义数值类型、身份表直接字段）、membership 关系
# （filtered 条件 / all 整表 / 数量关系与目录校验）与未知 identity kind。

SYSTEM_GRAPH = GRAPH + [
    {'@id': 'mg:System', '@type': 'owl:Class', 'rdfs:label': '储能系统'},
    # 显示名称 + 文本类型均经共享定义合并（effective 路径）
    {'@id': 'mg:sharedSystemName', '@type': 'mg:SharedProperty',
     'rdfs:range': {'@id': 'xsd:string'}, 'mg:isDisplayName': {'@value': True}},
    prop('system_name', domain='mg:System', **{'mg:sharedProperty': {'@id': 'mg:sharedSystemName'}}),
    # 聚合结果属性：数值类型经共享定义合并
    {'@id': 'mg:sharedCapacity', '@type': 'mg:SharedProperty', 'rdfs:range': {'@id': 'xsd:double'}},
    prop('rated_capacity', domain='mg:System', **{'mg:sharedProperty': {'@id': 'mg:sharedCapacity'}}),
    prop('system_note', 'string', domain='mg:System'),
    # 一对多链接：系统包含电池包（domain/range 完整标注，成员规则支持的数量关系）
    {'@id': 'mg:containsBattery', '@type': 'owl:ObjectProperty', 'rdfs:label': '包含电池包',
     'rdfs:domain': {'@id': 'mg:System'}, 'rdfs:range': {'@id': 'mg:Battery'},
     'mg:cardinality': 'one-to-many'},
    # 一对一链接：成员规则不支持的数量关系
    {'@id': 'mg:systemAlias', '@type': 'owl:ObjectProperty', 'rdfs:label': '系统别名',
     'rdfs:domain': {'@id': 'mg:System'}, 'rdfs:range': {'@id': 'mg:Battery'},
     'mg:cardinality': 'one-to-one'},
    # 多对多链接：仅用于「聚合引用的链接尚无成员规则」分支（不配置 membership）
    {'@id': 'mg:systemPeer', '@type': 'owl:ObjectProperty', 'rdfs:label': '关联系统',
     'rdfs:domain': {'@id': 'mg:System'}, 'rdfs:range': {'@id': 'mg:Battery'},
     'mg:cardinality': 'many-to-many'},
]

SYSTEM_ONTOLOGY = ontology_state(graph=SYSTEM_GRAPH)


def system_binding(**kw):
    b = binding(object_type='System', connection='', table='', primary_key='',
                identity={'kind': 'registered', 'instances': [
                    {'id': 'SYS-001', 'label': '一号储能系统'},
                    {'id': 'SYS-002', 'label': ''}]},
                properties={
                    'system_name': {'kind': 'registered', 'field': 'label'},
                    'rated_capacity': {'kind': 'aggregate', 'relation': 'containsBattery',
                                       'property': 'capacity', 'operator': 'sum',
                                       'empty': 'null', 'missing': 'incomplete',
                                       'inputUnitConfirmed': True}},
                relations=[{'kind': 'membership', 'relation': 'containsBattery', 'target_type': 'Battery',
                            'rules': [
                                {'sourceInstance': 'SYS-001', 'scope': 'filtered', 'conditions': [
                                    {'field': 'group_no', 'operator': 'eq', 'value': 2},
                                    {'field': 'station_id', 'operator': 'notnull'}]},
                                {'sourceInstance': 'SYS-002', 'scope': 'filtered', 'conditions': [
                                    {'field': 'group_no', 'operator': 'in', 'value': [3, 4]},
                                    {'field': 'station_id', 'operator': 'ne', 'value': 9}]}]}])
    b.update(kw)
    return b


# 45：登记系统 + 登记信息属性 + 关联聚合（合法路径，多实例分别配置 filtered 成员条件）
sample('45_registered_system_aggregate', project(bindings=[system_binding(), BATTERY_BINDING]),
       onto=copy.deepcopy(SYSTEM_ONTOLOGY))

# 46：错误/警告分支——实例编号重复、超长、换行、编号未填、label 非文本；
# registered 字段非法；aggregate 操作符/空值策略(None)/缺失策略非法、结果属性非数值、
# 链接无成员规则、单位未确认；membership 未登记实例、重复实例、范围无效、
# filtered 无条件、字段不在目录、操作符非法、数值不兼容、in 非数组、isnull 带值、
# 一对一数量关系、起点/终点与链接定义不一致、未知链接、未知 identity kind、旧 relation 原样。
sample('46_registered_membership_variants', project(bindings=[
    system_binding(
        identity={'kind': 'registered', 'instances': [
            {'id': 'SYS-001', 'label': '一号储能系统'},
            {'id': 'SYS-001', 'label': '重复编号'},                     # 同对象编号重复
            {'id': 'SYS-002', 'label': ''},
            {'id': 'S' + 'x' * 130, 'label': ''},                       # 编号超长
            {'id': 'SYS-003\nBAD', 'label': ''},                        # 编号含换行
            {'id': '', 'label': 42}]},                                  # 编号未填 + label 非文本
        properties={
            'system_name': {'kind': 'registered', 'field': 'name'},     # 登记信息字段非法
            'rated_capacity': {'kind': 'aggregate', 'relation': 'containsBattery', 'property': 'capacity',
                               'operator': 'avg', 'empty': None, 'missing': 'zero'},  # 三项非法 + 单位未确认
            'system_note': {'kind': 'aggregate', 'relation': 'systemPeer', 'property': 'capacity',
                            'operator': 'sum', 'empty': 'null', 'missing': 'incomplete'}},  # 结果非数值 + 尚无成员规则
        relations=[
            {'kind': 'membership', 'relation': 'containsBattery', 'target_type': 'Battery', 'rules': [
                {'sourceInstance': 'SYS-001', 'scope': 'filtered', 'conditions': [
                    {'field': 'group_no', 'operator': 'eq', 'value': 'abc'},   # 数值字段文本值
                    {'field': 'ghost_col', 'operator': 'lt', 'value': 1},      # 字段不在目录 + 操作符非法
                    {'field': 'station_id', 'operator': 'in', 'value': 3},     # in 取值非数组
                    {'field': 'id', 'operator': 'isnull', 'value': 0}]},       # isnull 携带取值
                {'sourceInstance': 'SYS-001', 'scope': 'all'},                 # 重复实例 + 整表 warning
                {'sourceInstance': 'SYS-GHOST', 'scope': 'any'},               # 实例未登记 + 范围无效
                {'sourceInstance': 'SYS-002', 'scope': 'filtered', 'conditions': []}]},  # 无条件
            {'kind': 'membership', 'relation': 'systemAlias', 'target_type': 'Battery', 'rules': [
                {'sourceInstance': 'SYS-002', 'scope': 'all'}]},               # 一对一数量关系 + 整表 warning
            {'kind': 'membership', 'relation': 'belongsToStation', 'target_type': 'Battery', 'rules': [
                {'sourceInstance': 'SYS-002', 'scope': 'all'}]},               # 起点不是当前对象 + 终点与定义不一致
            {'kind': 'membership', 'relation': 'ghostLink', 'target_type': 'Ghost', 'rules': []},  # 未知链接/终点
            {'relation': 'hasBattery', 'target_type': 'Battery', 'column': 'station_id'}],  # 旧 relation 原样
    ),
    BATTERY_BINDING,
    binding(object_type='Station', identity={'kind': 'mystery'}),     # 未知 identity kind
]),
       onto=copy.deepcopy(SYSTEM_ONTOLOGY))


# Generic editable query rules: preserve legacy fixtures and add V3 coverage.
V3_QUERY_RULE = copy.deepcopy(REUSABLE_QUERY_RULE)
V3_QUERY_RULE['schemaVersion'] = 3
V3_QUERY_RULE['inputs'] = [dict(name=n, type='string', source='runtime' if n in ('startTime','endTime') else 'binding') for n in ('model_name','attr_name','model_id','startTime','endTime')]
sample('v3_query_series', project(bindings=[binding(properties={'soc': {'kind':'computed','implementation':V3_QUERY_RULE['id'],'output':'series','inputs':{'model_name':'station','attr_name':'soc','model_id':'{id}'}}})], implementations=[V3_QUERY_RULE]), QUERY_ONTOLOGY)
V3_SCALAR_RULE = copy.deepcopy(V3_QUERY_RULE)
V3_SCALAR_RULE['steps'] = [dict(id='lookup',name='查询功率',table='station',cardinality='one',where=[dict(field='id',op='eq',value='{{inputs.model_id}}')],select=[{'as':'value','field':'rated_power'}])]
V3_SCALAR_RULE['result'] = dict(type='scalar',valueType='double',step='lookup',value='value')
sample('v3_query_scalar', project(bindings=[binding(properties={'rated_power': {'kind':'computed','implementation':V3_SCALAR_RULE['id'],'output':'series','inputs':{'model_name':'station','attr_name':'power','model_id':'{id}'}}})], implementations=[V3_SCALAR_RULE]), QUERY_ONTOLOGY)
invalid_v3=copy.deepcopy(V3_QUERY_RULE)
invalid_v3['steps'][0]['select'].pop(0)
sample('v3_query_deleted_output', project(implementations=[invalid_v3]), QUERY_ONTOLOGY)

# V4 卡片式 SQL 步骤（mode='sqlSteps'）：三步 SOC 定位（point one / storage one / samples many），
# SQL 内 :输入、:前置步骤.列、{{前置步骤.列}} 动态标识；policies/extensions 等字段原样保留不校验。
V4_SQL_STEPS_RULE = {
    'kind': 'queryRule', 'schemaVersion': 4, 'mode': 'sqlSteps',
    'id': 'q4-sqlsteps-soc', 'name': 'SOC 卡片式查询', 'connection': 'mysql_main',
    'inputs': [
        {'name': 'model_id', 'type': 'string', 'description': '实例主键', 'source': 'binding'},
        {'name': 'startTime', 'type': 'dateTime', 'source': 'runtime'},
        {'name': 'endTime', 'type': 'dateTime', 'source': 'runtime'},
    ],
    'steps': [
        {'id': 'stg_point', 'key': 'point', 'name': '定位测点', 'cardinality': 'one',
         'sql': "SELECT scada_table, scada_id FROM s_attr_scada "
                "WHERE model_name = 'm_storage_cluster_phase' AND attr_name = 'soc' AND model_id = :model_id"},
        {'id': 'stg_storage', 'key': 'storage', 'name': '定位采样存储', 'cardinality': 'one',
         'sql': 'SELECT sample_table_name FROM {{point.scada_table}} WHERE scada_id = :point.scada_id'},
        {'id': 'stg_samples', 'key': 'samples', 'name': '读取采样记录', 'cardinality': 'many',
         'sql': 'SELECT record_time AS ts, value FROM {{storage.sample_table_name}} '
                'WHERE record_time >= :startTime AND record_time < :endTime'},
    ],
    'result': {'step': 'stg_samples', 'type': 'timeSeries', 'valueType': 'double',
               'value': 'value', 'timestamp': 'ts'},
    'extensions': {'vendor': 'sql-steps'},
}

sample('v4_sqlsteps_valid', project(
    bindings=[binding(properties={'soc': {'kind': 'computed', 'implementation': V4_SQL_STEPS_RULE['id'],
                                           'output': 'series',
                                           'inputs': {'model_name': 'x', 'attr_name': 'soc', 'model_id': '{id}'}}})],
    implementations=[copy.deepcopy(V4_SQL_STEPS_RULE)]), QUERY_ONTOLOGY)

invalid_v4 = copy.deepcopy(V4_SQL_STEPS_RULE)
invalid_v4['steps'][1]['sql'] = 'SELECT sample_table_name FROM {{samples.sample_table_name}} WHERE scada_id = :point.scada_id'  # 前向引用后面的 key
invalid_v4['steps'][2]['key'] = 'storage'  # 与第 2 步 key 重复
sample('v4_sqlsteps_invalid', project(implementations=[invalid_v4]), QUERY_ONTOLOGY)


# 属性内 SQL 取值（computed/mode=inlineSql）：匿名内联模板，与命名规则互斥；只校验配置不执行。
INLINE_VALID = {'kind': 'computed', 'mode': 'inlineSql',
                'inlineSql': {'version': 1, 'connection': 'mysql_main',
                              'sql': 'SELECT SUM(rated_power) AS value FROM m_storage_phase WHERE park_id_column = :park',
                              'params': {'park': {'from': 'projectParameter', 'key': 'park_id'}}}}
sample('inline_sql_valid', project(
    bindings=[binding(properties={'rated_power': copy.deepcopy(INLINE_VALID)})],
    parameters={'park_id': 'P001'}), QUERY_ONTOLOGY)

sample('inline_sql_no_param', project(
    bindings=[binding(properties={'rated_power': {'kind': 'computed', 'mode': 'inlineSql',
                                                  'inlineSql': {'version': 1, 'connection': 'mysql_main',
                                                                'sql': 'SELECT SUM(rated_power) AS value FROM m_storage_phase',
                                                                'params': {}}}})]), QUERY_ONTOLOGY)

inline_unbound = {'kind': 'computed', 'mode': 'inlineSql',
                  'inlineSql': {'version': 1, 'connection': 'mysql_main',
                                'sql': "SELECT :p, 'x:fake' FROM t -- :ghost\n/* :block */",
                                'params': {}}}
sample('inline_sql_unbound', project(bindings=[binding(properties={'rated_power': inline_unbound})]), QUERY_ONTOLOGY)

inline_unknown = {'kind': 'computed', 'mode': 'inlineSql',
                  'inlineSql': {'version': 2, 'connection': 'mysql_main', 'sql': 'SELECT 1', 'params': {}}}
sample('inline_sql_unknown_version', project(bindings=[binding(properties={'rated_power': inline_unknown})]), QUERY_ONTOLOGY)

inline_conflict = dict(copy.deepcopy(INLINE_VALID))
inline_conflict['implementation'] = 'fn-series'
inline_conflict['output'] = 'series'
sample('inline_sql_conflict', project(
    bindings=[binding(properties={'rated_power': inline_conflict})],
    parameters={'park_id': 'P001'}), QUERY_ONTOLOGY)

# 计算函数（kind=calculationFunction）：公式实现 + 属性绑定（输入来源、类型相容、循环依赖）。
CALC_SOC = {'kind': 'calculationFunction', 'schemaVersion': 1, 'id': 'cf-soc', 'name': '计算 SOC',
            'description': '剩余电量与额定容量之比',
            'inputs': [{'id': 'inp_a', 'name': '剩余电量', 'type': 'number'},
                       {'id': 'inp_b', 'name': '额定容量', 'type': 'number'}],
            'implementation': {'language': 'calc-expression-1', 'expression': '{inp_a} / {inp_b} * 100'},
            'output': {'id': 'out_v', 'name': 'SOC', 'type': 'number'}}


def calc_binding(inputs, output='out_v', impl_id='cf-soc'):
    return {'kind': 'computed', 'mode': 'calcFunction', 'implementation': impl_id, 'output': output, 'inputs': inputs}


sample('calc_function_valid', project(
    bindings=[binding(properties={'rated_power': calc_binding({'inp_a': {'from': 'property', 'property': 'temp_avg'},
                                                               'inp_b': {'from': 'constant', 'value': 100}})})],
    implementations=[copy.deepcopy(CALC_SOC)]), QUERY_ONTOLOGY)

sample('calc_function_missing_inputs', project(
    bindings=[binding(properties={'rated_power': calc_binding({})})],
    implementations=[copy.deepcopy(CALC_SOC)]), QUERY_ONTOLOGY)

sample('calc_function_type_mismatch', project(
    bindings=[binding(properties={'rated_power': calc_binding({'inp_a': {'from': 'property', 'property': 'station_name'},
                                                               'inp_b': {'from': 'constant', 'value': 100}})})],
    implementations=[copy.deepcopy(CALC_SOC)]), QUERY_ONTOLOGY)

sample('calc_function_timeseries_target', project(
    bindings=[binding(properties={'soc': calc_binding({'inp_a': {'from': 'property', 'property': 'temp_avg'},
                                                       'inp_b': {'from': 'constant', 'value': 100}})})],
    implementations=[copy.deepcopy(CALC_SOC)]), QUERY_ONTOLOGY)

sample('calc_function_direct_cycle', project(
    bindings=[binding(properties={'rated_power': calc_binding({'inp_a': {'from': 'property', 'property': 'rated_power'},
                                                               'inp_b': {'from': 'constant', 'value': 100}})})],
    implementations=[copy.deepcopy(CALC_SOC)]), QUERY_ONTOLOGY)

calc_fn_bad = copy.deepcopy(CALC_SOC)
calc_fn_bad['implementation']['expression'] = '{inp_ghost} + 1'
sample('calc_function_bad_expression', project(implementations=[calc_fn_bad]), QUERY_ONTOLOGY)

stale_fn = copy.deepcopy(CALC_SOC)
stale_fn['inputs'] = [CALC_SOC['inputs'][0]]  # 删除仍被公式引用的 inp_b
sample('calc_function_stale_binding', project(
    bindings=[binding(properties={'rated_power': calc_binding({'inp_a': {'from': 'constant', 'value': 1},
                                                               'inp_b': {'from': 'constant', 'value': 100}})})],
    implementations=[stale_fn]), QUERY_ONTOLOGY)


sample('inline_sql_registered', project(
    bindings=[binding(object_type='Battery', connection='', table='', primary_key='',
                      identity={'kind': 'registered', 'instances': [{'id': 'B1', 'label': '电池一'}]},
                      properties={'capacity': {'kind': 'computed', 'mode': 'inlineSql',
                                               'inlineSql': {'version': 1, 'connection': 'mysql_main',
                                                             'sql': 'SELECT :p AS value, :sid AS sid FROM dual',
                                                             'params': {'p': {'from': 'projectParameter', 'key': 'park_id'},
                                                                        'sid': {'from': 'instanceId'}}}}})],
    parameters={'park_id': 'P001'}), QUERY_ONTOLOGY)

# 审阅修复：步骤唯一性——many 步骤重复 key（此前漏检）、重复 ID、空 ID、非法记录数。
dup_many_v4 = copy.deepcopy(V4_SQL_STEPS_RULE)
dup_many_v4['steps'].append({'id': 'stg_more', 'key': 'samples', 'name': '再读采样', 'cardinality': 'many',
                             'sql': 'SELECT 1 AS v FROM t'})
sample('v4_sqlsteps_dup_many_key', project(implementations=[dup_many_v4]), QUERY_ONTOLOGY)

bad_ids_v4 = copy.deepcopy(V4_SQL_STEPS_RULE)
bad_ids_v4['steps'][1]['id'] = bad_ids_v4['steps'][0]['id']       # 与第 1 步 ID 重复（key 不同）
bad_ids_v4['steps'][2]['id'] = ''                                  # 空 ID
bad_ids_v4['steps'][2]['cardinality'] = 'single'                   # 非法记录数
sample('v4_sqlsteps_bad_ids', project(implementations=[bad_ids_v4]), QUERY_ONTOLOGY)


# --- 14. 项目动作绑定（20260917 需求）----------------------------------------------
# 有效动作与关联只来自项目引用的已发布本体版本；未绑定不产生输出，失效绑定报 error。
ACTION_V2 = {'id': 'act-stop', 'name': '停止充放电', 'description': '请求目标对象停止充放电',
             'effect': '目标功率为 0，以设备反馈确认完成', 'definitionVersion': 2, 'status': 'experimental'}
ACTION_LEGACY = {'id': 'action.change_system', 'name': '调整所属系统', 'description': '调整设备归属',
                 'object_type': 'Station', 'inputs': [], 'effect': '结束原归属、建立新归属',
                 'criteria': '存在且不冲突', 'permission': '资产管理员', 'acceptance': '设备A 转到系统二',
                 'status': 'active', 'implementation_ref': ''}


def action_ontology(actions=None, assoc=None, graph=None):
    state = ontology_state(graph=graph)
    state['workflow']['actions'] = copy.deepcopy(actions if actions is not None else [ACTION_V2])
    state['workflow']['actionAssociations'] = copy.deepcopy(
        assoc if assoc is not None else [{'objectTypeId': 'mg:Station', 'actionId': 'act-stop'}])
    return state


def action_binding(object_type='Station', action_id='act-stop', **kw):
    row = {'id': 'bind-1', 'objectTypeId': object_type, 'actionId': action_id,
           'implementation': {'kind': 'api', 'path': '/api/stop'}}
    row.update(kw)
    return row


def abind(action_id='act-stop', **kw):
    return action_binding(action_id=action_id, **kw)


def action_project(rows, object_bindings=None, **kw):
    """actionBindings 专用项目夹具：project(bindings=…) 是对象映射，不能混用。"""
    p = project(bindings=object_bindings if object_bindings is not None else [], **kw)
    p['bindings']['actionBindings'] = rows
    return p


# 合法：api 绑定 + 历史动作按 object_type 推导关联后绑定
sample('47_action_bindings_valid', action_project([
    abind(), abind('action.change_system',
                   implementation={'kind': 'api', 'path': '/system/change', 'roles': '运维',
                                   'paramNotes': 'deviceId ← 当前设备'})]),
    onto=action_ontology(actions=[ACTION_V2, ACTION_LEGACY], assoc=[{'objectTypeId': 'mg:Station', 'actionId': 'act-stop'}]))

# flow 绑定引用不存在的编排（合法 flow 绑定依赖环境编排数据，不放金样；
# 由 tests/test_action_library.py 在受控临时根内覆盖）
from workbench import flows as _flows  # noqa: E402  （确保导入路径与运行时一致）
sample('48_action_bindings_flow_missing', action_project(
    [abind(implementation={'kind': 'flow', 'flowId': 'ghost-flow'})]), action_ontology())

# 失效关联：组合不在引用版本的有效关联 → error（保留不删除）
sample('49_action_bindings_stale', action_project([abind()]),
    onto=action_ontology(assoc=[]))

# 重复组合 / 对象不存在 / 动作不存在 / 实现缺失与未知 kind / 条目格式无效
sample('50_action_bindings_variants', action_project([
    abind(implementation={'kind': 'api', 'path': '/a'}),
    abind(implementation={'kind': 'api', 'path': '/b'}),                        # 重复组合
    action_binding(object_type='Ghost', implementation={'kind': 'api', 'path': '/x'}),  # 对象不存在
    abind('ghost-action', implementation={'kind': 'api', 'path': '/y'}),        # 动作不存在
    abind(implementation={}),                                                   # 实现方式缺失
    abind(implementation={'kind': 'mystery', 'path': '/z', 'custom': 'keep'}),  # 未知 kind（字段保留）
    {'id': 'bind-9', 'objectTypeId': 'Station', 'actionId': 'act-stop'},        # 实现字段整体缺失
    'not-a-row']),                                                              # 条目格式无效
    onto=action_ontology())
sample('51_action_bindings_api_no_path', action_project(
    [abind(implementation={'kind': 'api', 'path': '  '})]), action_ontology())

non_list = project(bindings=[binding()])
non_list['bindings']['actionBindings'] = 'oops'
sample('52_action_bindings_not_list', non_list, action_ontology())


# --- 15. 动作 HTTP 接口配置（20260917 动作接口映射一期）------------------------------
# 仅 implementation.schemaVersion=2 启用本期完整校验；无 schemaVersion 的历史 api 走旧规则（见例 51）。
# 项目侧实例识别与「属性取值」决定 instanceId / property 来源是否可用；凭据目录为空时任何凭据引用都报错。
def api_binding(method='POST', path='https://ems.example.com/api/storage/stop', **kw):
    impl = {'kind': 'api', 'schemaVersion': 2, 'method': method, 'path': path, 'bodyFormat': 'json',
            'description': '', 'parameters': [], 'auth': {'type': 'none'}}
    impl.update(kw)
    return action_binding(implementation=impl)


def api_param(pid, name, where, value):
    return {'id': pid, 'name': name, 'in': where, 'value': value}


# 已配置来源的标量属性（station_name 有来源、rated_power 供“未配置来源”用例）；
# sources 需显式声明，否则属性来源校验器会先报“数据来源不存在”。
DEVICE_BINDING = binding(title_key='station_name', sources=[db_source('s_main')], properties={
    'station_name': {'kind': 'field', 'source': 's_main', 'field': 'station_name'}})

# 合法：完整地址 + 路径占位符 + 实例主键 + 数值 0／布尔 false 固定值 + 已配置来源的属性
sample('53_action_v2_api_valid', action_project([
    api_binding(path='https://ems.example.com/api/devices/{deviceId}/stop', parameters=[
        api_param('p1', 'deviceId', 'path', {'from': 'instanceId'}),
        api_param('p2', 'power', 'body', {'from': 'constant', 'type': 'number', 'value': 0}),
        api_param('p3', 'force', 'body', {'from': 'constant', 'type': 'boolean', 'value': False}),
        api_param('p4', 'code', 'header', {'from': 'property', 'propertyId': 'mg:station_name'}),
    ])], object_bindings=[DEVICE_BINDING]), action_ontology())

# 配置错误：相对地址、GET 带请求体、同位置重名（含请求头大小写）、数值固定值非法、占位符缺参数
sample('54_action_v2_api_config_errors', action_project([
    api_binding(method='GET', path='/api/stop', parameters=[
        api_param('p1', 'power', 'body', {'from': 'constant', 'type': 'number', 'value': 'abc'}),
        api_param('p2', 'power', 'body', {'from': 'constant', 'type': 'number', 'value': 0}),
        api_param('p3', 'force', 'header', {'from': 'constant', 'type': 'boolean', 'value': True}),
        api_param('p4', 'FORCE', 'header', {'from': 'constant', 'type': 'boolean', 'value': True}),
    ]),
    api_binding(path='https://ems.example.com/api/devices/{deviceId}/stop', parameters=[]),
]), action_ontology())

# 引用类错误：时间序列属性、未配置来源的属性、不在引用版本的属性、动作无输入、凭据不在项目；
# 第二条同时覆盖 registered/database 身份缺失、API Key 名称必填与未知字段保留（roles/paramNotes/custom）
TS_GRAPH = copy.deepcopy(GRAPH)
TS_GRAPH.append({'@id': 'mg:soc_series', '@type': 'owl:DatatypeProperty', 'mg:apiName': 'soc_series',
                 'rdfs:label': 'SOC 序列', 'rdfs:domain': {'@id': 'mg:Station'},
                 'rdfs:range': {'@id': 'xsd:double'}, 'mg:valueShape': 'timeSeries'})
sample('55_action_v2_api_reference_errors', action_project([
    api_binding(path='https://ems.example.com/api/x', parameters=[
        api_param('p1', 'series', 'body', {'from': 'property', 'propertyId': 'mg:soc_series'}),
        api_param('p2', 'unmapped', 'body', {'from': 'property', 'propertyId': 'mg:rated_power'}),
        api_param('p3', 'ghost', 'body', {'from': 'property', 'propertyId': 'mg:ghost'}),
        api_param('p4', 'input', 'body', {'from': 'actionInput', 'inputId': 'in-1'}),
    ], auth={'type': 'bearer', 'credentialId': 'cred-ghost'}),
    api_binding(method='DELETE', path='https://ems.example.com/api/y/{id}', parameters=[
        api_param('p1', 'id', 'query', {'from': 'instanceId'}),
    ], auth={'type': 'apiKey', 'credentialId': '', 'in': 'query', 'name': ''},
        roles='运维', paramNotes='deviceId ← 当前设备', custom={'nested': [1, 2]}),
], object_bindings=[binding(title_key='station_name', primary_key='', properties={})]),
    onto=action_ontology(graph=TS_GRAPH))

# 未知字段保留：v2 实现里的 roles/paramNotes/custom 原样写进金样（新表单不丢旧字段）
sample('56_action_v2_api_unknown_kept', action_project([
    api_binding(path='https://ems.example.com/api/z', parameters=[],
                roles='运维', paramNotes='p ← 固定值', custom={'nested': [1, 2]}),
], object_bindings=[binding(title_key='station_name')]), action_ontology())

# --- 16. C01 空白依赖引用（2026-09-21 第二轮独立验收补充） ---------------------------
# 纯空白 providerId 属「已声明但无效」（判据 = 原始非空字符串，见接口文档 04 §2.2）：
# 账号模型目录可读（本夹具为空集合）时必须判「提供方不存在/尚未配置」，不得因 strip
# 判空而跳过检查后放行。编排由 tests/golden_c01_flow.py 以固定 flowId 播种（生成端与
# 回放端各自隔离根内写入同一内容，保证金样可复现）。
import golden_c01_flow as _c01_flow  # noqa: E402  （tests/ 已在 sys.path）
_c01_flow.seed(_flows)
sample('57_c01_blank_provider_reference', project(bindings=[
    binding(properties={'rated_power': {'kind': 'flow', 'flow': _c01_flow.FLOW_ID,
                                        'output': 'out_p', 'inputs': {}}})]))


def main():
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    entries = []
    for s in SAMPLES:
        state = copy.deepcopy(s['state'])       # 金样记录调用前状态（validate 有 title_key 副作用）
        ontology = copy.deepcopy(s['ontology_state'])
        report = projects.validate_project(state, ontology)
        entries.append({'name': s['name'], 'state': s['state'], 'ontology_state': s['ontology_state'],
                        'report': report})
    counts = {'errors': sum(len(e['report']['errors']) for e in entries),
              'warnings': sum(len(e['report']['warnings']) for e in entries),
              'items': sum(len(e['report']['items']) for e in entries)}
    FIXTURE.write_text(json.dumps({'samples': entries}, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'金样已写入 {FIXTURE}')
    print(f'样例 {len(entries)} 个：errors {counts["errors"]} 条，warnings {counts["warnings"]} 条，items {counts["items"]} 条')
    for e in entries:
        flag = 'ERROR' if e['report']['errors'] else ('warn ' if e['report']['warnings'] else 'clean')
        print(f"  [{flag}] {e['name']}: {len(e['report']['errors'])}e/{len(e['report']['warnings'])}w/{len(e['report']['items'])}i")



# --- 项目说明（bindings.mappingDescriptions，2026-09-19）---
DESC_OK = {'schemaVersion': 1,
           'objects': {'mg:Station': '储能单元来自 station 表，id 唯一。'},
           'properties': {'mg:Station': {'mg:rated_power': '读取 station.rated_power 字段。'}},
           'links': {}, 'actions': {}}
DESC_STALE = {'schemaVersion': 1,
              'objects': {'mg:Ghost': '不存在的对象。'},
              'properties': {'mg:Station': {'mg:removed_prop': '已删除属性。'}},
              'links': {'mg:ghost_link': '不存在的链接。'},
              'actions': {'mg:Station': {'act_missing': '不存在的动作。'}}}
sample('mapping_desc_ok', project(
    bindings=[binding()], implementations=[],
    catalogs=CATALOG) | {'bindings': {'notice': '', 'object_bindings': [binding()], 'observation_binding': {}, 'source_candidates': [], 'mappingDescriptions': DESC_OK, 'catalogs': CATALOG}})
sample('mapping_desc_plain_only', project() | {'bindings': {'notice': '', 'object_bindings': [], 'observation_binding': {}, 'source_candidates': [], 'mappingDescriptions': DESC_OK, 'catalogs': CATALOG}})
sample('mapping_desc_stale', project() | {'bindings': {'notice': '', 'object_bindings': [], 'observation_binding': {}, 'source_candidates': [], 'mappingDescriptions': DESC_STALE, 'catalogs': CATALOG}})

# --- R02 金样（2026-09-21）：项目引用编排自身配置有效性 ----------------------------
# flow 绑定此前不放金样：样例依赖环境编排数据（见 48 号样例注释），而编排库按账号
# 隔离在数据根里。本节在隔离临时根内播种**固定 flowId** 的金样专用编排；回放侧
# （test_validation_split.py）调用同一 seed_golden_flows()（幂等）得到同构内容，
# 依赖三态在生成与回放两侧稳定返回 found，金样才能锁定
# 「被项目引用的编排配置无效 → 阻断发布」的 R02 修复语义。

_GOLDEN_FLOWS = (
    # (flowId, 名称, 是否接节点, 输出声明)。不接节点 → 输出缺 binding →
    # check_flow error OUTPUT_BINDING_MISSING（空壳编排，R02 反例）。
    ('gldbadflow00001', '金样未绑定输出编排', False,
     [{'id': 'out_power', 'name': 'power', 'label': '功率', 'type': {'type': 'number'}}]),
    ('gldokflow000001', '金样合法编排', True,
     [{'id': 'out_power', 'name': 'power', 'label': '功率', 'type': {'type': 'number'}}]),
)


def seed_golden_flows():
    """在隔离数据根播种金样专用编排（固定 flowId，生成/回放两侧内容同构；幂等）。"""
    from workbench import flows as _flows
    for fid, name, wired, outputs in _GOLDEN_FLOWS:
        if _flows.current_token(fid) is not None:
            continue  # 已播种：重复运行不推进已有草稿
        state = _flows.blank_flow(fid, name)
        state['outputs'] = [dict(o) for o in outputs]
        if wired:
            state['nodes'] = [{'id': 'nd_impl', 'kind': 'python', 'name': '实现',
                               'inputs': [],
                               'outputs': [{'id': 'nd_out_power', 'name': 'power', 'label': '功率',
                                            'type': {'type': 'number'}}],
                               'implementation': {'language': 'python',
                                                  'code': 'def main():\n    return None\n'}}]
            state['outputs'][0]['binding'] = {'kind': 'node', 'nodeId': 'nd_impl',
                                              'outputId': 'nd_out_power'}
        _flows.save_draft(state)


seed_golden_flows()


def flow_src(flow_id, output):
    return {'kind': 'flow', 'flow': flow_id, 'output': output, 'inputs': {}}


sample('57_flow_binding_output_unbound', project(bindings=[binding(properties={
    'rated_power': flow_src('gldbadflow00001', 'out_power')})]))
# 被引用编排输出未绑定来源（OUTPUT_BINDING_MISSING）→ 新增阻断项：
# 属性定位 + 编排标识 + 未绑定原因在同一条 error 里（R02 修复语义）

sample('58_flow_binding_config_valid', project(bindings=[binding(properties={
    'rated_power': flow_src('gldokflow000001', 'out_power')})]))
# 被引用编排自身配置有效 → 不产生编排配置类阻断（防过度阻断护栏）

if __name__ == '__main__':
    main()
