"""动作 HTTP 接口映射（implementation.kind='api', schemaVersion=2）校验回归（20260917 需求）。

覆盖两部分：

1. 纯模块 `workbench/action_http.py`：合法配置零错误、固定值边界（0／false／空串）、
   路径占位符与路径参数一一对应、同位置参数重名（请求头大小写不敏感）、GET 带请求体、
   动作输入不存在／动作无输入、实例主键未配置、对象属性三种失败（不在版本／时间序列／
   未配置来源）、凭据引用、Bearer 与 Authorization 冲突、API Key 名称必填与同名冲突、
   Content-Type 冲突、地址 userinfo／fragment、地址中已有同名 Query 参数警告；
   以及 `api_view` 读取零丢失（不修改传入 dict、未知字段原样保留）。
2. 项目校验接入 `project_validation._check_action_bindings`：v2 合法／非法绑定的
   errors 与 items 状态、历史 api（无 schemaVersion）行为不变、旧 flow 绑定不被当作 v2、
   未绑定动作零输出，以及引用版本＋项目映射上下文的真实组装（primary_key／registered
   身份、动作输入、属性取值未配置）。

纯 python3 标准库直跑；WIZ_WORKBENCH_ROOT 挂临时数据根，真实 ontology/ 只读不碰。
运行：python3 tests/test_action_http.py
"""
import copy
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
TMP = Path(tempfile.mkdtemp(prefix='wiz_action_http_'))
os.environ['WIZ_WORKBENCH_ROOT'] = str(TMP)

from workbench import action_http  # noqa: E402  （临时根就位后再 import）
from workbench.project_validation import validate_project  # noqa: E402

PASSED = []


def check(cond, message, actual=None):
    if cond:
        PASSED.append(message)
        print(f'通过) {message}')
        return
    print(f'[失败] {message}')
    if actual is not None:
        print('  实际: ' + json.dumps(actual, ensure_ascii=False, default=str)[:2000])
    shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(1)


def has(texts, fragment):
    return any(fragment in str(t) for t in texts)


# --- 夹具：引用版本 graph / 动作定义 -------------------------------------------------

GRAPH = [
    {'@id': 'mg:StorageDevice', '@type': 'owl:Class', 'rdfs:label': '储能设备'},
    {'@id': 'mg:deviceCode', '@type': 'owl:DatatypeProperty', 'rdfs:label': '设备编号',
     'rdfs:domain': {'@id': 'mg:StorageDevice'}, 'mg:apiName': 'deviceCode'},
    {'@id': 'mg:maxPower', '@type': 'owl:DatatypeProperty', 'rdfs:label': '最大功率',
     'rdfs:domain': {'@id': 'mg:StorageDevice'}, 'mg:apiName': 'maxPower'},
    {'@id': 'mg:powerSeries', '@type': 'owl:DatatypeProperty', 'rdfs:label': '功率序列',
     'rdfs:domain': {'@id': 'mg:StorageDevice'}, 'mg:apiName': 'powerSeries',
     'mg:valueShape': 'timeSeries'},
]


def onto_state(actions=None, assoc=None):
    return {'ontology': {'@context': {}, '@graph': copy.deepcopy(GRAPH)},
            'workflow': {'actions': copy.deepcopy(actions or []),
                         'actionAssociations': copy.deepcopy(assoc or [])}}


ACTION = {'id': 'act_stop', 'name': '停止充放电', 'definitionVersion': 2, 'status': 'experimental',
          'inputs': [{'id': 'in_power', 'name': 'power', 'type': 'double'}]}
ACTION_NO_INPUT = {'id': 'act_idle', 'name': '空闲待机', 'definitionVersion': 2, 'status': 'experimental'}
ASSOC = [{'objectTypeId': 'mg:StorageDevice', 'actionId': 'act_stop'},
         {'objectTypeId': 'mg:StorageDevice', 'actionId': 'act_idle'}]

PROPS = action_http.property_index(GRAPH, 'mg:StorageDevice')
for _info in PROPS.values():
    _info['configured'] = True
INPUTS = [{'id': 'in_power', 'name': 'power'}]
CREDS = {'cred_1'}


def v2(**overrides):
    """本期实现对象；默认地址不含占位符，需要路径参数的用例显式传 path。"""
    impl = {'kind': 'api', 'schemaVersion': 2, 'method': 'POST',
            'path': 'https://ems.example.com/api/devices/stop',
            'bodyFormat': 'json', 'parameters': [], 'auth': {'type': 'none'}}
    impl.update(overrides)
    return impl


PATH_WITH_DEVICE = 'https://ems.example.com/api/devices/{deviceId}/stop'


def param(name, position, value, **extra):
    row = {'name': name, 'in': position, 'value': value}
    row.update(extra)
    return row


CONST = lambda kind, value: {'from': 'constant', 'type': kind, 'value': value}  # noqa: E731
INSTANCE = {'from': 'instanceId'}


def issues(implementation, **overrides):
    kwargs = {'action_inputs': INPUTS, 'properties': PROPS, 'identity_ready': True,
              'credential_ids': CREDS}
    kwargs.update(overrides)
    return action_http.api_issues(implementation, **kwargs)


# --- 1. 合法配置零错误 --------------------------------------------------------------

errors, warnings = issues(v2(path=PATH_WITH_DEVICE, parameters=[
    param('deviceId', 'path', dict(INSTANCE)),
    param('power', 'body', {'from': 'actionInput', 'inputId': 'in_power'})]))
check(errors == [] and warnings == [], '合法 v2 配置（路径＋实例主键＋动作输入）零错误零警告', (errors, warnings))

errors, warnings = issues(v2(method='post', path=PATH_WITH_DEVICE, parameters=[
    param('deviceId', 'path', dict(INSTANCE)),
    param('power', 'query', {'from': 'actionInput', 'inputName': 'power'})]))
check(errors == [] and warnings == [], '历史输入无稳定 ID 时按 inputName 兼容匹配，方法大小写不敏感', (errors, warnings))

# --- 2. 固定值边界 ------------------------------------------------------------------

errors, _ = issues(v2(parameters=[param('power', 'body', CONST('number', 0))]))
check(errors == [], '数值固定值 0 是有效固定值', errors)

errors, _ = issues(v2(parameters=[param('power', 'body', CONST('boolean', False))]))
check(errors == [], '布尔固定值 false 是有效固定值', errors)

errors, _ = issues(v2(parameters=[param('note', 'body', CONST('string', ''))]))
check(errors == [], '文本空串可作为显式固定值', errors)

errors, _ = issues(v2(parameters=[param('power', 'body', CONST('number', ''))]))
check(len(errors) == 1 and has(errors, '请输入有效数值'), '数值固定值填空串报错（并提示 0 是有效值）', errors)

errors, _ = issues(v2(parameters=[param('power', 'body', CONST('number', 'abc'))]))
check(len(errors) == 1 and has(errors, '请输入有效数值'), '数值固定值填 abc 报错', errors)

errors, _ = issues(v2(parameters=[param('note', 'body', {'from': 'constant', 'type': 'string'})]))
check(has(errors, '固定文本值无效'), '文本固定值缺失（None）报错，要求显式保存为空文本', errors)

errors, _ = issues(v2(parameters=[param('note', 'body', CONST('emoji', 'x'))]))
check(has(errors, '固定值类型必须是文本、数值或是否'), '未知固定值类型报错', errors)

# --- 3. 路径占位符 ↔ 路径参数 --------------------------------------------------------

errors, _ = issues(v2(path='https://ems.example.com/api/devices/{deviceId}/stop',
                      parameters=[param('otherId', 'path', dict(INSTANCE))]))
check(has(errors, '路径占位符 {deviceId} 尚未配置对应的路径参数'), '地址有占位符但缺对应路径参数报错', errors)
check(has(errors, '地址中不存在对应的 {otherId} 占位符'), '路径参数缺对应占位符报错', errors)
check(len(errors) == 2, '占位符与路径参数不匹配各报一条', errors)

errors, _ = issues(v2(path='https://ems.example.com/api/stop',
                      parameters=[param('deviceId', 'path', dict(INSTANCE))]))
check(len(errors) == 1 and has(errors, '占位符'), '地址无占位符时只报一条（路径参数多余）', errors)

# --- 4. 同位置重名（请求头大小写不敏感） ---------------------------------------------

errors, _ = issues(v2(parameters=[param('X-Token', 'header', CONST('string', 'a')),
                                  param('x-token', 'header', CONST('string', 'b'))]))
check(len(errors) == 1 and has(errors, '同一位置的参数名重复'), '请求头参数重名大小写不敏感', errors)

errors, _ = issues(v2(parameters=[param('deviceId', 'query', CONST('string', 'a')),
                                  param('deviceId', 'body', CONST('string', 'b'))]))
check(errors == [], '不同位置的同名参数不算重复', errors)

# --- 5. GET 带请求体 ----------------------------------------------------------------

errors, _ = issues(v2(method='GET', path='https://ems.example.com/api/status',
                      parameters=[param('power', 'body', CONST('number', 1))]))
check(has(errors, 'GET 请求不允许请求体参数'), 'GET 带请求体参数报错', errors)

# --- 6. 动作输入 --------------------------------------------------------------------

errors, _ = issues(v2(parameters=[param('power', 'body', {'from': 'actionInput', 'inputId': 'in_power'})]),
                   action_inputs=[])
check(len(errors) == 1 and has(errors, '没有输入参数'), '动作无输入时报「没有输入参数」且不伪造选项', errors)

errors, _ = issues(v2(parameters=[param('power', 'body', {'from': 'actionInput', 'inputId': 'ghost'})]))
check(has(errors, '选择的动作输入不在引用版本中'), '引用的动作输入不在引用版本中报错', errors)

errors, _ = issues(v2(parameters=[param('power', 'body', {'from': 'actionInput'})]))
check(has(errors, '选择的动作输入不在引用版本中'), '未选择动作输入时报错', errors)

# --- 7. 实例识别 --------------------------------------------------------------------

INSTANCE_PARAM = param('deviceId', 'path', dict(INSTANCE))
errors, _ = issues(v2(path=PATH_WITH_DEVICE, parameters=[INSTANCE_PARAM]), identity_ready=False)
check(has(errors, '尚未完成实例识别'), '未配置实例识别时 instanceId 来源报错', errors)
errors, _ = issues(v2(path=PATH_WITH_DEVICE, parameters=[INSTANCE_PARAM]))
check(errors == [], '有实例识别时 instanceId 来源通过', errors)
check(action_http.identity_ready({'primary_key': 'device_id'})
      and action_http.identity_ready({'identity': {'kind': 'registered'}})
      and action_http.identity_ready({'identity': {'kind': 'registered'}, 'primary_key': ' '})
      and not action_http.identity_ready({'primary_key': ' '})
      and not action_http.identity_ready({'identity': {'kind': 'database'}, 'primary_key': 'id'})
      and not action_http.identity_ready({'identity': {'kind': 'unknown'}, 'primary_key': 'x'})
      and not action_http.identity_ready({})
      and not action_http.identity_ready(None),
      'identity_ready：数据库身份需 primary_key 非空、identity.kind=registered 视为就绪；未知 identity.kind 不解释为就绪')

# --- 8. 对象属性来源 ----------------------------------------------------------------

PROP_PARAM = lambda pid: param('code', 'body', {'from': 'property', 'propertyId': pid})  # noqa: E731

errors, _ = issues(v2(parameters=[PROP_PARAM('mg:ghost')]))
check(len(errors) == 1 and has(errors, '不在项目引用版本中'), '属性不在引用版本报错', errors)

errors, _ = issues(v2(parameters=[PROP_PARAM('mg:powerSeries')]))
check(len(errors) == 1 and has(errors, '时间序列属性不能作为本期的标量参数'), '时间序列属性不能作为标量参数', errors)

unconfigured = {k: dict(v) for k, v in PROPS.items()}
unconfigured['mg:deviceCode']['configured'] = False
errors, _ = issues(v2(parameters=[PROP_PARAM('mg:deviceCode')]), properties=unconfigured)
check(len(errors) == 1 and has(errors, '尚未在「属性取值」中配置来源'), '属性未配置项目来源报错', errors)

errors, _ = issues(v2(parameters=[PROP_PARAM('mg:deviceCode')]))
check(errors == [], '属性在版本中且已配置来源时通过', errors)

errors, _ = issues(v2(parameters=[param('code', 'body', {'from': 'property'})]))
check(has(errors, '请选择对象属性'), '未选择属性时报错', errors)

# --- 9. 认证与凭据 ------------------------------------------------------------------

errors, _ = issues(v2(auth={'type': 'bearer', 'credentialId': 'ghost'}))
check(has(errors, '选择的 API 凭据不在当前项目中'), '凭据不在当前项目报错', errors)

errors, _ = issues(v2(auth={'type': 'bearer', 'credentialId': 'cred_1'}))
check(errors == [], '凭据引用合法时通过', errors)

errors, _ = issues(v2(auth={'type': 'bearer'}))
check(has(errors, '请选择当前项目的 API 凭据引用'), '凭据未选择报错', errors)

errors, _ = issues(v2(auth={'type': 'bearer', 'credentialId': 'cred_1'},
                      parameters=[param('Authorization', 'header', CONST('string', 'x'))]))
check(has(errors, 'Authorization 已由认证配置提供'), 'Bearer 与 Authorization 请求头参数冲突', errors)

errors, _ = issues(v2(auth={'type': 'bearer', 'credentialId': 'cred_1'},
                      parameters=[param('authorization', 'header', CONST('string', 'x'))]))
check(has(errors, 'Authorization 已由认证配置提供'), 'Authorization 冲突判定大小写不敏感', errors)

errors, _ = issues(v2(auth={'type': 'apiKey', 'credentialId': 'cred_1', 'in': 'header', 'name': ''}))
check(len(errors) == 1 and has(errors, '请填写 API Key 的参数名称'), 'API Key 名称必填', errors)

errors, _ = issues(v2(auth={'type': 'apiKey', 'credentialId': 'cred_1', 'in': 'header', 'name': 'X-API-Key'},
                      parameters=[param('x-api-key', 'header', CONST('string', 'x'))]))
check(len(errors) == 1 and has(errors, '位置和名称重复'), 'API Key 与参数表同位置同名冲突（请求头大小写不敏感）', errors)

errors, _ = issues(v2(auth={'type': 'apiKey', 'credentialId': 'cred_1', 'in': 'body', 'name': 'k'}))
check(has(errors, 'API Key 参数位置只能是请求头或 Query'), 'API Key 位置无效报错', errors)

errors, _ = issues(v2(auth={'type': 'certificate'}))
check(has(errors, '认证方式无效'), '未知认证方式报错', errors)

errors, _ = issues(v2(parameters=[param('Content-Type', 'header', CONST('string', 'x'))]))
check(has(errors, '重复声明 Content-Type'), 'content-type 请求头参数冲突', errors)

# --- 10. 地址合法性 与 地址中已有 Query 参数 ------------------------------------------

errors, _ = issues(v2(path='https://user:pw@ems.example.com/api/stop#frag', method='GET'))
check(has(errors, '不能包含账号密码') and has(errors, 'fragment'), '地址含 userinfo／fragment 报错', errors)

errors, _ = issues(v2(path='/api/devices/stop'))
check(has(errors, '必须是 http:// 或 https:// 开头的完整地址'), '相对路径地址报错', errors)

errors, _ = issues(v2(path='ftp://ems.example.com/api'))
check(has(errors, '必须是 http:// 或 https:// 开头的完整地址'), '非 http(s) 协议报错', errors)

errors, _ = issues(v2(path=''))
check(has(errors, '请填写接口地址'), '地址为空报错', errors)

errors, warnings = issues(v2(method='GET', path='https://ems.example.com/api/stop?mode=fast',
                             parameters=[param('mode', 'query', CONST('string', 'fast'))]))
check(errors == [] and has(warnings, '地址中已有同名 Query 参数'), '地址已含同名 Query 参数时自动识别并给警告', (errors, warnings))

errors, warnings = issues(v2(method='GET', path='https://ems.example.com/api/stop',
                             parameters=[param('mode', 'query', CONST('string', 'fast'))]),
                          query_names_in_path=['mode'])
check(errors == [] and has(warnings, '地址中已有同名 Query 参数'), '调用方传入 query_names_in_path 时同样给警告', (errors, warnings))

errors, warnings = issues(v2(method='GET', path='https://ems.example.com/api/stop',
                             parameters=[param('mode', 'query', CONST('string', 'fast'))]))
check(errors == [] and warnings == [], '地址中没有同名 Query 参数时不产生警告', (errors, warnings))

# --- 11. 读取零丢失 ------------------------------------------------------------------

raw = v2(parameters=[param('deviceId', 'path', dict(INSTANCE))],
         customSection={'keep': [1, 2]}, legacyField='保留我')
raw_copy = copy.deepcopy(raw)
view = action_http.api_view(raw)
check(raw == raw_copy, 'api_view 读取零丢失：不修改传入 dict（深比较前后一致）')
check(view['method'] == 'POST' and view['bodyFormat'] == 'json' and view['auth']['type'] == 'none',
      'api_view 在内存视图里补默认值（不改存储）', view)
check(raw.get('customSection') == {'keep': [1, 2]} and raw.get('legacyField') == '保留我',
      '未知字段原样保留在实现里')
check(action_http.is_v2(raw) and not action_http.is_v2({'kind': 'api', 'path': '/x'})
      and not action_http.is_v2({'kind': 'flow', 'schemaVersion': 2})
      and action_http.is_v2({'kind': 'api', 'schemaVersion': '2'}),
      'is_v2 仅认 kind=api 且 schemaVersion=2（含字符串写法）')
check(action_http.placeholders('https://x/{a}/y/{b}/{a}') == ['a', 'b']
      and action_http.placeholders('/no/placeholder') == [],
      'placeholders 按出现顺序去重提取')


# --- 12. 项目校验接入 ---------------------------------------------------------------

def obj_row(**overrides):
    row = {'object_type': 'StorageDevice', 'connection': 'conn1', 'table': 'devices',
           'primary_key': 'device_id', 'properties': {'deviceCode': {'kind': 'field', 'field': 'device_code'}}}
    row.update(overrides)
    return row


def proj_state(bindings=None, obj_row_value=None):
    return {'projectId': 'p1', 'name': '动作项目', 'ontologyId': 'storage', 'ontologyVersion': '1.0.0',
            'connections': {'connections': [{'id': 'conn1', 'name': '主库', 'engine': 'mysql'}]},
            'bindings': {'notice': '', 'source_candidates': [],
                         'object_bindings': [obj_row() if obj_row_value is None else obj_row_value],
                         'actionBindings': copy.deepcopy(bindings or [])},
            'implementations': [], 'parameters': {}}


def action_item(report):
    return next((i for i in report['items'] if i.get('kind') == 'actionBinding'), None)


REF = onto_state([dict(ACTION), dict(ACTION_NO_INPUT)], ASSOC)

legal_impl = v2(path=PATH_WITH_DEVICE, parameters=[param('deviceId', 'path', dict(INSTANCE)),
                                                   param('power', 'body', {'from': 'actionInput', 'inputId': 'in_power'})])
report = validate_project(proj_state([{'id': 'r1', 'objectTypeId': 'StorageDevice', 'actionId': 'act_stop',
                                       'implementation': copy.deepcopy(legal_impl)}]), copy.deepcopy(REF))
check(report['errors'] == [] and report['warnings'] == [],
      'v2 合法绑定通过项目校验（errors／warnings 均为空）', report)
item = action_item(report)
check(item is not None and item['status'] == 'valid' and item['issues'] == []
      and item['name'] == '动作绑定 · 储能设备 / 停止充放电',
      'v2 合法绑定产生 valid 的 actionBinding 条目', item)

bad_impl = v2(path='')
report = validate_project(proj_state([{'id': 'r1', 'objectTypeId': 'StorageDevice', 'actionId': 'act_stop',
                                       'implementation': copy.deepcopy(bad_impl)}]), copy.deepcopy(REF))
check(any('动作绑定' in e and '停止充放电' in e and '请填写接口地址' in e for e in report['errors']),
      'v2 非法绑定错误按「动作绑定 {名称}：…」上报', report['errors'])
item = action_item(report)
check(item is not None and item['status'] == 'invalid' and has(item['issues'], '请填写接口地址'),
      'v2 非法绑定条目 status=invalid 且 issues 可见', item)

report = validate_project(proj_state([{'id': 'r1', 'objectTypeId': 'StorageDevice', 'actionId': 'act_stop',
                                       'implementation': v2(parameters=[
                                           param('power', 'body', {'from': 'actionInput', 'inputId': 'ghost'})])}]),
                          copy.deepcopy(REF))
check(has(report['errors'], '选择的动作输入不在引用版本中'),
      '项目校验按引用版本的输入集合校验 actionInput（inputId 悬空报错）', report['errors'])

report = validate_project(proj_state([{'id': 'r1', 'objectTypeId': 'StorageDevice', 'actionId': 'act_idle',
                                       'implementation': v2(parameters=[
                                           param('power', 'body', {'from': 'actionInput', 'inputId': 'in_power'})])}]),
                          copy.deepcopy(REF))
check(has(report['errors'], '没有输入参数'),
      '引用版本中该动作无输入时报「没有输入参数」（不伪造选项）', report['errors'])

report = validate_project(proj_state([{'id': 'r1', 'objectTypeId': 'StorageDevice', 'actionId': 'act_stop',
                                       'implementation': v2(parameters=[PROP_PARAM('mg:deviceCode')])}]),
                          copy.deepcopy(REF))
check(report['errors'] == [], '项目已配置属性取值时 property 来源通过（按对象映射行读取 configured）', report['errors'])

report = validate_project(proj_state([{'id': 'r1', 'objectTypeId': 'StorageDevice', 'actionId': 'act_stop',
                                       'implementation': v2(parameters=[PROP_PARAM('mg:maxPower')])}]),
                          copy.deepcopy(REF))
check(has(report['errors'], '尚未在「属性取值」中配置来源'),
      '对象映射未配置该属性来源时 property 来源报错', report['errors'])

report = validate_project(proj_state([{'id': 'r1', 'objectTypeId': 'StorageDevice', 'actionId': 'act_stop',
                                       'implementation': v2(parameters=[PROP_PARAM('mg:powerSeries')])}]),
                          copy.deepcopy(REF))
check(has(report['errors'], '时间序列属性不能作为本期的标量参数'),
      '项目校验沿用引用版本的时间序列属性判定', report['errors'])

# 历史 api（无 schemaVersion）：完全保留旧行为，只查 path 非空
report = validate_project(proj_state([{'id': 'r1', 'objectTypeId': 'StorageDevice', 'actionId': 'act_stop',
                                       'implementation': {'kind': 'api', 'path': '/api/devices/{id}/stop'}}]),
                          copy.deepcopy(REF))
check(report['errors'] == [] and action_item(report)['status'] == 'valid',
      '历史 api（无 schemaVersion，相对路径）仍按旧规则通过', report)
report = validate_project(proj_state([{'id': 'r1', 'objectTypeId': 'StorageDevice', 'actionId': 'act_stop',
                                       'implementation': {'kind': 'api', 'path': ''}}]), copy.deepcopy(REF))
check(report['errors'] == ['动作绑定 动作绑定 · 储能设备 / 停止充放电：项目接口路径未填写'],
      '历史 api 只在路径为空时报错（逐字沿用旧版文案，含既有的前缀重复）', report['errors'])

# 旧 flow 绑定：不被当成 v2
report = validate_project(proj_state([{'id': 'r1', 'objectTypeId': 'StorageDevice', 'actionId': 'act_stop',
                                       'implementation': {'kind': 'flow', 'flowId': 'ghost-flow'}}]),
                          copy.deepcopy(REF))
check(has(report['errors'], '引用的函数编排不存在或已删除')
      and not has(report['errors'], '接口地址') and report['errors'][0].startswith('动作绑定'),
      '旧 flow 绑定仍按编排不存在报错，不被当作 v2 校验', report['errors'])
report = validate_project(proj_state([{'id': 'r1', 'objectTypeId': 'StorageDevice', 'actionId': 'act_stop',
                                       'implementation': {'kind': 'flow'}}]), copy.deepcopy(REF))
check(has(report['errors'], '函数编排未选择'), 'flow 未选择编排报错（行为不变）', report['errors'])

# 未绑定任何动作：零输出、不阻断
empty = {'projectId': 'p1', 'name': '动作项目', 'ontologyId': 'storage',
         'connections': {'connections': []},
         'bindings': {'notice': '', 'object_bindings': [], 'actionBindings': []},
         'implementations': [], 'parameters': {}}
report = validate_project(copy.deepcopy(empty), copy.deepcopy(REF))
check(report['errors'] == [] and report['warnings'] == [] and report['items'] == [],
      '未配置任何绑定与映射的项目零输出、不阻断', report)

no_action_binding = proj_state([])
report = validate_project(copy.deepcopy(no_action_binding), copy.deepcopy(REF))
check(not any('动作绑定' in e for e in report['errors']) and action_item(report) is None,
      '有对象映射但未绑定动作时不产生动作绑定条目与错误', report)

# 零丢失：v2 配置不被校验改写（未知字段与项目行均原样）
raw_binding = {'id': 'r1', 'objectTypeId': 'StorageDevice', 'actionId': 'act_stop',
               'implementation': v2(path=PATH_WITH_DEVICE, roles_note='保留我', parameters=[
                   param('deviceId', 'path', dict(INSTANCE))])}
raw_binding_copy = copy.deepcopy(raw_binding)
validate_project(proj_state([raw_binding]), copy.deepcopy(REF))
check(raw_binding == raw_binding_copy, '校验只读：v2 实现未知字段零丢失', raw_binding)

# registered 登记身份（无主键字段）视为实例识别就绪；主键为空则报实例识别错误
registered_row = obj_row(connection='', table='', primary_key='',
                         identity={'kind': 'registered', 'instances': [{'id': 'DEV-1', 'label': '设备一'}]},
                         properties={'deviceCode': {'kind': 'field', 'field': 'device_code'}})
report = validate_project(proj_state([{'id': 'r1', 'objectTypeId': 'StorageDevice', 'actionId': 'act_stop',
                                       'implementation': v2(path=PATH_WITH_DEVICE,
                                                            parameters=[param('deviceId', 'path', dict(INSTANCE))])}],
                                    obj_row_value=registered_row), copy.deepcopy(REF))
check(report['errors'] == [] and action_item(report)['status'] == 'valid',
      'registered 登记身份视为实例识别就绪，instanceId 来源通过', report)

no_pk_row = obj_row(primary_key='')
report = validate_project(proj_state([{'id': 'r1', 'objectTypeId': 'StorageDevice', 'actionId': 'act_stop',
                                       'implementation': v2(path=PATH_WITH_DEVICE,
                                                            parameters=[param('deviceId', 'path', dict(INSTANCE))])}],
                                    obj_row_value=no_pk_row), copy.deepcopy(REF))
check(has(report['errors'], '尚未完成实例识别'),
      '实例主键为空时项目校验报实例识别 error', report['errors'])
check(action_item(report)['status'] == 'invalid', '实例识别未就绪时绑定条目 invalid', action_item(report))

unknown_identity_row = obj_row(primary_key='', identity={'kind': 'mystery'})
report = validate_project(proj_state([{'id': 'r1', 'objectTypeId': 'StorageDevice', 'actionId': 'act_stop',
                                       'implementation': v2(path=PATH_WITH_DEVICE,
                                                            parameters=[param('deviceId', 'path', dict(INSTANCE))])}],
                                    obj_row_value=unknown_identity_row), copy.deepcopy(REF))
check(has(report['errors'], '尚未完成实例识别'),
      'identity.kind 未知时不按已就绪解释（instanceId 来源报错）', report['errors'])

no_row = {'object_type': 'StorageDevice'}
report = validate_project(proj_state([{'id': 'r1', 'objectTypeId': 'StorageDevice', 'actionId': 'act_stop',
                                       'implementation': v2(path=PATH_WITH_DEVICE,
                                                            parameters=[param('deviceId', 'path', dict(INSTANCE))])}],
                                    obj_row_value=no_row), copy.deepcopy(REF))
check(has(report['errors'], '尚未完成实例识别') and has(report['errors'], '未配置实例主键字段'),
      '无实例识别配置的数据库对象 instanceId 来源报错', report['errors'])

print(f'\n全部通过（{len(PASSED)} 项断言）')
shutil.rmtree(TMP, ignore_errors=True)
