"""无表对象与关联聚合（任务 B）后端校验测试。

覆盖 project_validation 新增分支：
1. identity 分支（registered 不要求 connection/table/primary_key；登记实例校验；
   无块 = 旧数据库身份原校验原样；未知 kind 报错并保留数据）；
2. registered 属性（field 枚举、文本兼容类型、限登记对象）；
3. aggregate 属性（SUM、出向链接、终点身份表直接数值字段、递归/空值/缺失/单位口径）；
4. membership 关系（数量关系、实例登记、范围、条件字段目录与类型、操作符枚举）；
5. 引用保护（validate_project 只读，不删除数据；旧对象校验零变化）。

纯 python3 脚本（无 pytest），内存 dict 构造 state + ontology_state，不启动服务、
不读写真实 ontology/（未设 WIZ_WORKBENCH_ROOT 时自动落到临时目录）。
运行：WIZ_WORKBENCH_ROOT=$(mktemp -d) python3 tests/test_registered_validation.py
"""
import copy
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if not os.environ.get('WIZ_WORKBENCH_ROOT'):
    os.environ['WIZ_WORKBENCH_ROOT'] = tempfile.mkdtemp(prefix='wiz_registered_validation_')

from workbench import projects  # noqa: E402
from workbench.project_validation import validate_project  # noqa: E402

FAILURES = []


def run(name, fn):
    try:
        fn()
        print(f'通过  {name}')
    except Exception as exc:
        FAILURES.append(name)
        print(f'失败  {name}: {type(exc).__name__}: {exc}')


# --- 夹具 ------------------------------------------------------------------------

MYSQL_CONN = {'id': 'mysql_main', 'name': '主数据库', 'engine': 'mysql',
              'host': '127.0.0.1', 'port': 3306, 'database': 'demo'}

CATALOG = {'mysql_main': {'tables': [
    {'name': 'device', 'fields': [
        {'name': 'id', 'dataType': 'bigint', 'key': 'pri'},
        {'name': 'park', 'dataType': 'varchar'},
        {'name': 'del_flag', 'dataType': 'int'},
        {'name': 'capacity', 'dataType': 'double'},
        {'name': 'code', 'dataType': 'varchar'}]},
]}}


def prop(api, dtype='double', domain='mg:System', **kw):
    node = {'@id': f'mg:{api}', '@type': 'owl:DatatypeProperty', 'rdfs:label': api,
            'mg:apiName': api, 'rdfs:domain': {'@id': domain}, 'rdfs:range': {'@id': f'xsd:{dtype}'}}
    node.update(kw)
    return node


def link(api, source='mg:System', target='mg:Device', cardinality=None):
    node = {'@id': f'mg:{api}', '@type': 'owl:ObjectProperty', 'rdfs:label': api,
            'rdfs:domain': {'@id': source}, 'rdfs:range': {'@id': target}}
    if cardinality:
        node['mg:cardinality'] = cardinality
    return node


GRAPH = [
    {'@id': 'mg:System', '@type': 'owl:Class', 'rdfs:label': '储能系统'},
    {'@id': 'mg:Device', '@type': 'owl:Class', 'rdfs:label': '储能设备'},
    {'@id': 'mg:Station', '@type': 'owl:Class', 'rdfs:label': '储能单元'},
    prop('sys_name', 'string'),
    # 数值类型经共享定义合并（effective 路径）
    prop('sys_capacity', **{'mg:sharedProperty': {'@id': 'mg:sharedCapacity'}}),
    {'@id': 'mg:sharedCapacity', '@type': 'mg:SharedProperty', 'rdfs:range': {'@id': 'xsd:double'}},
    prop('sys_note', 'string'),
    prop('sys_series', **{'mg:valueShape': 'timeSeries'}),
    prop('dev_capacity', domain='mg:Device'),
    prop('dev_code', 'string', domain='mg:Device'),
    prop('dev_series', domain='mg:Device', **{'mg:valueShape': 'timeSeries'}),
    prop('st_power', domain='mg:Station'),
    link('containsDevice', cardinality='one-to-many'),
    link('peersDevice', cardinality='many-to-many'),
    link('aliasDevice', cardinality='one-to-one'),
    link('plainLink'),                                        # 未标注数量关系
    {'@id': 'mg:noRangeLink', '@type': 'owl:ObjectProperty', 'rdfs:label': '缺终点',
     'rdfs:domain': {'@id': 'mg:System'}},                     # 缺终点定义
    link('foreignLink', source='mg:Device', target='mg:System', cardinality='one-to-many'),
    # 入向多对一：设备属于系统 —— System 在 range 侧仍可作为集合端（成员=Device）
    link('deviceBelongsTo', source='mg:Device', target='mg:System', cardinality='many-to-one'),
]


def system_binding(properties=None, relations=None, instances=None, identity=None):
    b = {'object_type': 'System', 'connection': '', 'table': '', 'primary_key': '', 'title_key': '',
         'properties': properties if properties is not None else {},
         'relations': relations if relations is not None else [],
         'sources': [], 'related_sources': []}
    b['identity'] = {'kind': 'registered',
                     'instances': instances if instances is not None else [
                         {'id': 'SYS-001', 'label': '一号系统'}, {'id': 'SYS-002', 'label': ''}]} \
        if identity is None else identity
    return b


def device_binding(properties=None, identity=None, **kw):
    b = {'object_type': 'Device', 'connection': 'mysql_main', 'table': 'device', 'primary_key': 'id',
         'title_key': '', 'properties': properties if properties is not None else {
             'dev_capacity': 'capacity', 'dev_code': 'code'},
         'relations': [], 'sources': [], 'related_sources': []}
    if identity is not None:
        b['identity'] = identity
        b.update({'connection': '', 'table': '', 'primary_key': ''})
    b.update(kw)
    return b


def station_binding(**kw):
    b = {'object_type': 'Station', 'connection': 'mysql_main', 'table': 'device', 'primary_key': 'id',
         'title_key': '', 'properties': {}, 'relations': [], 'sources': [], 'related_sources': []}
    b.update(kw)
    return b


def validate(bindings, catalogs=CATALOG, graph=None):
    state = {'projectId': 'p1', 'name': '登记校验项目', 'ontologyId': 'storage', 'ontologyVersion': '1',
             'connections': {'connections': [dict(MYSQL_CONN)]},
             'bindings': {'notice': '', 'object_bindings': bindings,
                          'observation_binding': {}, 'source_candidates': [], 'catalogs': catalogs},
             'implementations': [], 'parameters': {}}
    ontology = {'ontology': {'@context': {}, '@graph': copy.deepcopy(GRAPH if graph is None else graph)},
                'workflow': {'functions': []}}
    return validate_project(state, ontology)


def expect_error(report, text):
    assert text in report['errors'], f'缺少错误「{text}」，实际 errors={report["errors"]}'


def expect_no_error(report, fragment):
    hit = [e for e in report['errors'] if fragment in e]
    assert not hit, f'不应出现含「{fragment}」的错误，实际 {hit}'


def expect_warning(report, text):
    assert text in report['warnings'], f'缺少警告「{text}」，实际 warnings={report["warnings"]}'


def item(report, kind, iid):
    return next(i for i in report['items'] if i['kind'] == kind and i['id'] == iid)


def membership(relation='containsDevice', rules=None, target='Device'):
    return {'kind': 'membership', 'relation': relation, 'target_type': target,
            'rules': rules if rules is not None else []}


def aggregate(**kw):
    config = {'kind': 'aggregate', 'relation': 'containsDevice', 'property': 'dev_capacity',
              'operator': 'sum', 'empty': 'null', 'missing': 'incomplete', 'inputUnitConfirmed': True}
    config.update(kw)
    return config


# --- 1. identity 分支（A1）--------------------------------------------------------

def t_registered_identity_valid():
    report = validate([system_binding()])
    assert report['errors'] == [], f'登记对象不应再要求数据库身份三要素：{report["errors"]}'
    assert item(report, 'objectBinding', 'System')['status'] == 'valid'
    identity_item = item(report, 'registeredIdentity', 'System.identity')
    assert identity_item['status'] == 'valid' and identity_item['issues'] == []


def t_registered_identity_empty_instances():
    for instances in ([], None, 'not-a-list'):
        identity = {'kind': 'registered'}
        if instances is not None:
            identity['instances'] = instances
        report = validate([system_binding(identity=identity)])
        expect_error(report, '实例登记 System：未配置登记实例')


def t_registered_identity_instance_issues():
    report = validate([system_binding(identity={'kind': 'registered', 'instances': [
        {'id': 'SYS-001', 'label': '一号'},
        {'id': 'SYS-001', 'label': '重复'},
        {'id': 'S' + 'x' * 128, 'label': ''},        # 129 字符
        {'id': 'SYS\n003', 'label': ''},
        {'id': '', 'label': ''},
        {'id': 'SYS-004', 'label': 42},
        {'id': 'SYS-005', 'label': 'L' * 129},
        'not-a-dict',
    ]})])
    for text in ('实例登记 System：实例编号重复：SYS-001',
                 '实例登记 System：实例编号长度不能超过 128 字符',
                 '实例登记 System：实例编号不能包含换行',
                 '实例登记 System：实例编号未填写',
                 '实例登记 System：实例显示名称必须是文本',
                 '实例登记 System：实例显示名称长度不能超过 128 字符',
                 '实例登记 System：登记实例格式无效'):
        expect_error(report, text)
    assert item(report, 'registeredIdentity', 'System.identity')['status'] == 'invalid'


def t_identity_unknown_kind():
    report = validate([system_binding(identity={'kind': 'mystery', 'instances': [{'id': 'X'}]})])
    expect_error(report, '对象映射 System：未知的实例来源方式')
    expect_no_error(report, '未配置来源数据表')  # 未知分支不再叠加数据库身份要求


def t_database_identity_unchanged():
    report = validate([station_binding(connection='', table='', primary_key='')])
    for text in ('对象映射 Station：未配置来源数据表',
                 '对象映射 Station：未配置实例主键字段',
                 '对象映射 Station：未配置实例来源数据连接'):
        expect_error(report, text)
    report = validate([station_binding(connection='ghost')])
    expect_error(report, '对象映射 Station：实例来源引用的数据连接不存在')


# --- 2. registered 属性 ------------------------------------------------------------

def t_registered_property_valid():
    report = validate([system_binding(properties={
        'sys_name': {'kind': 'registered', 'field': 'label'},
        'sys_note': {'kind': 'registered', 'field': 'id'}})])
    expect_no_error(report, '属性来源 System')
    assert item(report, 'propertySource', 'System.sys_name')['status'] == 'valid'


def t_registered_property_variants():
    report = validate([system_binding(properties={
        'sys_name': {'kind': 'registered', 'field': 'name'},        # 字段枚举外
        'sys_capacity': {'kind': 'registered', 'field': 'id'},      # 数值属性（共享定义合并）
        'sys_series': {'kind': 'registered', 'field': 'id'}})])     # 时间序列属性
    expect_error(report, '属性来源 System.sys_name：登记信息字段仅支持实例编号或显示名称')
    expect_error(report, '属性来源 System.sys_capacity：登记信息只能绑定文本类型属性')
    expect_error(report, '属性来源 System.sys_series：时间序列属性不能绑定登记信息')


def t_registered_property_on_database_object():
    report = validate([station_binding(properties={
        'st_power': {'kind': 'registered', 'field': 'id'}})])
    expect_error(report, '属性来源 Station.st_power：登记信息来源只适用于实例来源为项目登记的对象')


# --- 3. aggregate 属性（A8）--------------------------------------------------------

def t_aggregate_valid():
    report = validate([system_binding(properties={'sys_capacity': aggregate()},
                                       relations=[membership(rules=[
                                           {'sourceInstance': 'SYS-001', 'scope': 'filtered',
                                            'conditions': [{'field': 'park', 'operator': 'eq', 'value': 'P01'}]}])]),
                       device_binding()])
    assert report['errors'] == [], f'合法聚合配置不应报错：{report["errors"]}'
    assert report['warnings'] == [], f'单位已确认且已有成员规则，不应有警告：{report["warnings"]}'
    agg = item(report, 'aggregateSource', 'System.sys_capacity')
    assert agg['status'] == 'valid' and agg['issues'] == []


def t_aggregate_warnings():
    report = validate([system_binding(properties={'sys_capacity': aggregate(inputUnitConfirmed=False)})])
    expect_warning(report, '属性来源 System.sys_capacity：尚未确认成员数值单位口径一致')
    expect_warning(report, '属性来源 System.sys_capacity：聚合引用的链接尚无成员规则')


def t_aggregate_strategy_fields():
    report = validate([system_binding(properties={
        'sys_capacity': aggregate(operator='avg', empty=None, missing='zero')})])
    expect_error(report, '属性来源 System.sys_capacity：聚合方式本轮仅支持求和（sum）')
    expect_error(report, '属性来源 System.sys_capacity：空值策略必须为字符串 "null"')
    expect_error(report, '属性来源 System.sys_capacity：缺失处理策略本轮仅支持 incomplete')
    report = validate([system_binding(properties={'sys_capacity': aggregate(empty='error')})])
    expect_error(report, '属性来源 System.sys_capacity：空值策略本轮仅支持 "null"')


def t_aggregate_relation_branches():
    cases = {'': '未选择聚合链接',
             'ghostLink': '引用的版本中不存在此链接类型',
             'foreignLink': '聚合链接必须以当前对象为集合端（出向一对多／多对多，或入向多对一／多对多）',
             'noRangeLink': '聚合链接必须以当前对象为集合端（出向一对多／多对多，或入向多对一／多对多）'}
    for relation, text in cases.items():
        report = validate([system_binding(properties={'sys_capacity': aggregate(relation=relation)}),
                           device_binding()])
        expect_error(report, f'属性来源 System.sys_capacity：{text}')
    # 入向多对一（设备属于系统）：System 作为集合端可聚合 Device 成员
    report = validate([system_binding(properties={'sys_capacity': aggregate(relation='deviceBelongsTo')}),
                       device_binding()])
    expect_no_error(report, '集合端')


def t_aggregate_member_property_branches():
    for prop_api, text in (('ghost_prop', '聚合成员属性在终点对象中不存在'),
                           ('dev_code', '聚合成员属性必须是数值属性'),
                           ('dev_series', '聚合成员属性不能是时间序列')):
        report = validate([system_binding(properties={'sys_capacity': aggregate(property=prop_api)}),
                           device_binding()])
        expect_error(report, f'属性来源 System.sys_capacity：{text}')


def t_aggregate_member_mapping_branches():
    direct = '成员属性不是身份表直接数值字段，本轮聚合不支持'
    # 终点对象未映射
    report = validate([system_binding(properties={'sys_capacity': aggregate()})])
    expect_error(report, '属性来源 System.sys_capacity：聚合终点对象尚未配置数据映射')
    # 补充来源 field：不是身份表直接字段
    report = validate([system_binding(properties={'sys_capacity': aggregate()}),
                       device_binding(properties={'dev_capacity': {'kind': 'field', 'source': 's1',
                                                                   'field': 'capacity'}},
                                      sources=[{'id': 's1', 'name': '补充', 'kind': 'db',
                                                'connection': 'mysql_main', 'table': 'device',
                                                'matchLeft': 'id', 'matchRight': 'id',
                                                'cardinality': 'one'}])])
    expect_error(report, f'属性来源 System.sys_capacity：{direct}')
    # database 直选他表：不是身份表直接字段
    report = validate([system_binding(properties={'sys_capacity': aggregate()}),
                       device_binding(properties={
                           'dev_capacity': {'kind': 'database', 'connection': 'mysql_main',
                                            'table': 'other_table', 'lookup': {'match': []},
                                            'result': {'valueField': 'capacity'}}})])
    expect_error(report, f'属性来源 System.sys_capacity：{direct}')
    # 等价表示一律接受：{kind:'field', source:''} / database 直连身份表 result.valueField
    report = validate([system_binding(properties={'sys_capacity': aggregate()}),
                       device_binding(properties={
                           'dev_capacity': {'kind': 'field', 'source': '', 'field': 'capacity'},
                           'dev_code': {'kind': 'database', 'connection': 'mysql_main', 'table': 'device',
                                        'lookup': {'match': []}, 'result': {'valueField': 'code'}}})])
    expect_no_error(report, direct)


def t_aggregate_member_field_catalog_types():
    # 目录中字段为文本：成员属性不是直接数值字段
    report = validate([system_binding(properties={'sys_capacity': aggregate()}),
                       device_binding(properties={'dev_capacity': 'park'})])
    expect_error(report, '属性来源 System.sys_capacity：成员属性不是身份表直接数值字段，本轮聚合不支持')
    # 目录存在但字段不在：刷新提示 warning
    report = validate([system_binding(properties={'sys_capacity': aggregate()}),
                       device_binding(properties={'dev_capacity': 'ghost_field'})])
    expect_warning(report, '属性来源 System.sys_capacity：成员字段「ghost_field」不在终点身份表目录中，请刷新表结构')


def t_aggregate_recursive_member():
    report = validate([system_binding(properties={'sys_capacity': aggregate()}),
                       device_binding(properties={'dev_capacity': aggregate(
                           relation='foreignLink', property='st_power')})])
    expect_error(report, '属性来源 System.sys_capacity：聚合成员属性不能是聚合结果，不支持递归聚合')


def t_aggregate_result_property_type():
    report = validate([system_binding(properties={'sys_note': aggregate(property='dev_code')})])
    expect_error(report, '属性来源 System.sys_note：聚合结果属性必须是数值属性')
    report = validate([system_binding(properties={'sys_series': aggregate()})])
    expect_error(report, '属性来源 System.sys_series：时间序列属性不能配置关联聚合')


def t_aggregate_on_database_object():
    report = validate([station_binding(properties={
        'st_power': {'kind': 'aggregate', 'relation': 'containsDevice', 'property': 'dev_capacity',
                     'operator': 'sum', 'empty': 'null', 'missing': 'incomplete',
                     'inputUnitConfirmed': True}})])
    expect_error(report, '属性来源 Station.st_power：关联聚合只适用于实例来源为项目登记的对象')


# --- 4. membership 关系（A3/A4）---------------------------------------------------

def t_membership_valid():
    rules = [
        {'sourceInstance': 'SYS-001', 'scope': 'filtered', 'conditions': [
            {'field': 'park', 'operator': 'eq', 'value': 'P01'},        # 文本字段任意取值
            {'field': 'del_flag', 'operator': 'eq', 'value': 0},        # 数值字段数值取值
            {'field': 'del_flag', 'operator': 'in', 'value': [0, 1]},   # in 数组
            {'field': 'code', 'operator': 'notnull'},
            {'field': 'park', 'operator': 'isnull'}]},
        {'sourceInstance': 'SYS-002', 'scope': 'filtered', 'conditions': [
            {'field': 'del_flag', 'operator': 'ne', 'value': 9}]},
    ]
    report = validate([system_binding(relations=[membership(rules=rules)]), device_binding()])
    assert report['errors'] == [], f'合法成员规则不应报错：{report["errors"]}'
    assert item(report, 'membership', 'System.containsDevice')['status'] == 'valid'


def t_membership_scope_all_warning():
    report = validate([system_binding(relations=[membership(rules=[
        {'sourceInstance': 'SYS-001', 'scope': 'all'}])]), device_binding()])
    assert report['errors'] == []
    expect_warning(report, '成员规则 containsDevice：该来源表全部有效记录将作为成员')


def t_membership_scope_and_conditions():
    report = validate([system_binding(relations=[membership(rules=[
        {'sourceInstance': 'SYS-001', 'scope': 'any'},
        {'sourceInstance': 'SYS-002', 'scope': 'filtered', 'conditions': []},
        {'sourceInstance': 'SYS-001', 'scope': 'filtered'}])]), device_binding()])
    expect_error(report, '成员规则 containsDevice：成员范围无效')
    expect_error(report, '成员规则 containsDevice：按条件选择必须至少一条条件；如需整表请在范围中显式选择')


def t_membership_source_instance():
    report = validate([system_binding(relations=[membership(rules=[
        {'sourceInstance': 'GHOST', 'scope': 'all'},
        {'sourceInstance': None, 'scope': 'all'},
        {'sourceInstance': 'SYS-001', 'scope': 'all'},
        {'sourceInstance': 'SYS-001', 'scope': 'all'}])]), device_binding()])
    expect_error(report, '成员规则 containsDevice：成员规则引用的实例未登记：GHOST')
    expect_error(report, '成员规则 containsDevice：成员规则未选择起点实例')
    expect_error(report, '成员规则 containsDevice：同一实例在该链接下重复配置成员规则')


def t_membership_condition_fields():
    report = validate([system_binding(relations=[membership(rules=[
        {'sourceInstance': 'SYS-001', 'scope': 'filtered', 'conditions': [
            {'field': 'ghost_col', 'operator': 'eq', 'value': 1}]}])]), device_binding()])
    expect_error(report, '成员规则 containsDevice：条件字段「ghost_col」不在终点对象身份表目录中')
    report = validate([system_binding(relations=[membership(rules=[
        {'sourceInstance': 'SYS-001', 'scope': 'filtered', 'conditions': [
            {'field': 'park', 'operator': 'eq', 'value': 'P01'}]}])]),
        device_binding()], catalogs={})  # 目录缺失 → 刷新提示
    assert report['errors'] == []
    expect_warning(report, '成员规则 containsDevice：终点表结构目录未读取或已过期，条件字段待核对')


def t_membership_condition_operators_and_values():
    report = validate([system_binding(relations=[membership(rules=[
        {'sourceInstance': 'SYS-001', 'scope': 'filtered', 'conditions': [
            {'field': 'del_flag', 'operator': 'lt', 'value': 1},
            {'field': 'del_flag', 'operator': 'eq', 'value': 'abc'},
            {'field': 'del_flag', 'operator': 'in', 'value': 3},
            {'field': 'park', 'operator': 'isnull', 'value': ''},
            {'field': 'code', 'operator': 'notnull', 'value': 0}]}])]), device_binding()])
    expect_error(report, '成员规则 containsDevice：条件操作符仅支持等于／不等于／属于列表／为空／不为空')
    expect_error(report, '成员规则 containsDevice：条件取值与字段类型不兼容')
    expect_error(report, '成员规则 containsDevice：属于列表的条件取值必须是列表')
    expect_error(report, '成员规则 containsDevice：为空／不为空条件不能携带取值')
    expect_no_error(report, '成员条件未选择字段')  # 其余条件字段本身合法


def t_membership_condition_field_required():
    report = validate([system_binding(relations=[membership(rules=[
        {'sourceInstance': 'SYS-001', 'scope': 'filtered', 'conditions': [
            {'field': '', 'operator': 'eq', 'value': 1}]}])]), device_binding()])
    expect_error(report, '成员规则 containsDevice：成员条件未选择字段')


def t_membership_cardinality():
    for relation, text in (('aliasDevice', '成员规则 aliasDevice：该数量关系不支持成员规则（集合端须出向一对多／多对多，或入向多对一／多对多）'),
                           ('plainLink', '成员规则 plainLink：该数量关系不支持成员规则（集合端须出向一对多／多对多，或入向多对一／多对多）')):
        report = validate([system_binding(relations=[membership(relation=relation, rules=[
            {'sourceInstance': 'SYS-001', 'scope': 'filtered',
             'conditions': [{'field': 'park', 'operator': 'eq', 'value': 'P01'}]}])]),
            device_binding()])
        expect_error(report, text)
    report = validate([system_binding(relations=[membership(relation='peersDevice', rules=[
        {'sourceInstance': 'SYS-001', 'scope': 'filtered',
         'conditions': [{'field': 'park', 'operator': 'eq', 'value': 'P01'}]}])]),
        device_binding()])  # 多对多允许
    expect_no_error(report, '该数量关系不支持成员规则')
    # 入向多对一（设备属于系统）：成员规则挂在 System（range 侧），成员=Device
    report = validate([system_binding(relations=[membership(relation='deviceBelongsTo', rules=[
        {'sourceInstance': 'SYS-001', 'scope': 'filtered',
         'conditions': [{'field': 'park', 'operator': 'eq', 'value': 'P01'}]}])]),
        device_binding()])
    expect_no_error(report, '成员规则')
    assert item(report, 'membership', 'System.deviceBelongsTo')['status'] == 'valid', '入向成员规则应为 valid'


def t_membership_link_consistency():
    report = validate([system_binding(relations=[
        membership(relation='ghostLink', target='Ghost'),                           # 链接与终点都不存在
        membership(target='Station'),                                               # 终点与链接定义不一致
        membership(relation='foreignLink')]),                                       # 起点不是当前对象
        device_binding()])
    expect_error(report, '成员规则 ghostLink：引用的版本中不存在此链接类型')
    expect_error(report, '成员规则 ghostLink：引用的版本中不存在此链接终点对象类型')
    expect_error(report, '成员规则 containsDevice：成员规则的成员对象端与链接定义不一致')
    expect_error(report, '成员规则 containsDevice：链接终点对象尚未配置数据映射')
    expect_error(report, '成员规则 foreignLink：该数量关系不支持成员规则（集合端须出向一对多／多对多，或入向多对一／多对多）')


def t_membership_target_registered():
    report = validate([system_binding(relations=[membership(rules=[
        {'sourceInstance': 'SYS-001', 'scope': 'filtered',
         'conditions': [{'field': 'park', 'operator': 'eq', 'value': 'P01'}]}])]),
        device_binding(identity={'kind': 'registered', 'instances': [{'id': 'DEV-001'}]})])
    expect_error(report, '成员规则 containsDevice：成员规则要求终点对象为数据库来源')


def t_membership_empty_rules_pending():
    report = validate([system_binding(relations=[membership()]), device_binding()])
    assert report['errors'] == [], f'空规则应允许保存草稿：{report["errors"]}'
    member_item = item(report, 'membership', 'System.containsDevice')
    assert member_item['status'] == 'unconfigured' and member_item['issues'] == ['尚未配置成员规则']


def t_legacy_relation_untouched():
    """无 kind 的旧 relation 原样走旧分支（仅旧格式提示，无 membership 校验）。"""
    report = validate([system_binding(relations=[
        {'relation': 'containsDevice', 'target_type': 'Device', 'column': 'park'}]),
        device_binding()])
    assert report['errors'] == []
    assert item(report, 'linkMapping', 'System.containsDevice')['status'] == 'unconfigured'
    expect_warning(report, '链接映射为旧格式（仅字段），建议在链接映射页重新选择两端来源保存')


# --- 5. 引用保护与旧对象零变化 -----------------------------------------------------

def t_validate_does_not_mutate_data():
    bindings = [
        system_binding(properties={'sys_name': {'kind': 'registered', 'field': 'label'}},
                       relations=[membership(rules=[{'sourceInstance': 'SYS-001', 'scope': 'filtered',
                                                     'conditions': [{'field': 'park', 'operator': 'eq',
                                                                     'value': 'P01'}]}])]),
        device_binding(),
        station_binding(identity={'kind': 'mystery'})]
    state = {'projectId': 'p1', 'name': '保护测试', 'ontologyId': 'storage', 'ontologyVersion': '1',
             'connections': {'connections': [dict(MYSQL_CONN)]},
             'bindings': {'notice': '', 'object_bindings': bindings,
                          'observation_binding': {}, 'source_candidates': [], 'catalogs': CATALOG},
             'implementations': [], 'parameters': {}}
    ontology = {'ontology': {'@context': {}, '@graph': copy.deepcopy(GRAPH)},
                'workflow': {'functions': []}}
    validate_project(copy.deepcopy(state), copy.deepcopy(ontology))
    # 净副作用应恰好等于 derive_display_names（现存行为）：identity/属性/关系配置原样保留
    expected = projects.derive_display_names(copy.deepcopy(state), copy.deepcopy(ontology))
    _actual = projects.derive_display_names(state, ontology)  # 先跑一次 validate 再比对
    report = validate_project(state, ontology)
    assert state == expected, 'validate_project 修改了除 title_key 之外的数据'
    assert state['bindings']['object_bindings'][0]['identity']['kind'] == 'registered'
    assert state['bindings']['object_bindings'][2]['identity'] == {'kind': 'mystery'}
    assert report['items'], '报告应正常产出'
    expect_error(report, '对象映射 Station：未知的实例来源方式')


def t_old_object_validation_unchanged():
    """旧数据库对象（设备）校验零变化：错误文案与旧实现一致。"""
    report = validate([device_binding(connection='ghost', table='', primary_key='')])
    for text in ('对象映射 Device：未配置来源数据表',
                 '对象映射 Device：未配置实例主键字段',
                 '对象映射 Device：实例来源引用的数据连接不存在'):
        expect_error(report, text)
    report = validate([device_binding()])
    assert report['errors'] == []
    kinds = {i['kind'] for i in report['items']}
    assert kinds == {'connection', 'objectBinding', 'propertySource'}, f'旧对象不应产生新 items：{kinds}'


def main():
    tests = [(name[2:], fn) for name, fn in sorted(globals().items())
             if name.startswith('t_') and callable(fn)]
    for name, fn in tests:
        run(name, fn)
    print(f'\n{len(tests)} 项测试执行完毕')
    if FAILURES:
        print(f'{len(FAILURES)} 项失败：{"、".join(FAILURES)}')
        sys.exit(1)
    print('全部通过')


if __name__ == '__main__':
    main()
