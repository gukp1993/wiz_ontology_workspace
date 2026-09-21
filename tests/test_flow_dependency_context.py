"""B01/B02 依赖上下文反例与合法对照（补充修复指令 2026-09-21 · N01 独立QA用例）。

运行（工作树根，清除继承 WIZ_* 后由脚本自设隔离）：
  env -u WIZ_WORKBENCH_ROOT -u WIZ_WORKBENCH_PORT -u WIZ_DATABASE_URL \
      .runtime/venv/bin/python tests/test_flow_dependency_context.py

隔离：WIZ_WORKBENCH_ROOT=<mktemp> + 独立 WIZ_DATABASE_URL（临时根内 SQLite），
假账号进程内直调路由/校验公开函数；不启动服务、不触碰真实 ontology/ 与库。
故障注入只 mock 依赖目录读取（llm_providers.list_metadata / api_credentials.ids），
不执行任何 Python/SQL/Redis/HTTP 业务逻辑。

断言的是修复后的期望语义（补充修复指令 §「已知空集合≠未知上下文≠读取失败」）：
- B01：项目明确空连接列表时，编排引用不存在的数据连接必须产生 error 并被发布路由 422 拒绝；
       登记实例 + 纯公式编排 + 空连接仍合法；
- B02：编排显式依赖的模型/凭据目录读取失败必须阻断（文案含「读取失败」、与「不存在」
       区分、不回显异常原文），不需要的依赖不被无关故障误伤；同次校验编排顺序不影响结论；
       依赖恢复后重新校验/发布成功；
- 缓存与回归：非法引用（不存在 id、空凭据集合）在修复前后语义一致（明确「不存在」）。

对被验 HEAD 6955e76 预期 B01/B02 阻断类断言失败——该失败输出即反例证据；
修复实施后本文件应全绿并纳入回归。
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
sys.path.insert(0, str(REPO / 'tests'))

TMP = Path(tempfile.mkdtemp(prefix='wiz_b01b02_ctx_'))
os.environ['WIZ_WORKBENCH_ROOT'] = str(TMP)
os.environ['WIZ_DATABASE_URL'] = 'sqlite:///' + str(TMP / 'data' / 'workbench.sqlite3')
os.environ.pop('WIZ_WORKBENCH_PORT', None)

import auth_client  # noqa: E402
auth_client.bind_fixture_user()

from unittest import mock  # noqa: E402
from workbench import api_credentials, flows, llm_providers, model_routes, projects, workspaces  # noqa: E402
from workbench import project_routes as _routes  # noqa: E402
from workbench.model_format import encode_state  # noqa: E402
from workbench.project_validation import validate_project  # noqa: E402

RESULTS = []


def check(cond, message, actual=None):
    tag = '通过' if cond else '失败'
    print(f'[{tag}] {message}')
    if not cond:
        RESULTS.append(('FAIL', message, actual))
        if actual is not None:
            print('  实际: ' + json.dumps(actual, ensure_ascii=False, default=str)[:1500])
    else:
        RESULTS.append(('PASS', message, None))


# ============ 夹具 ================================================================

GRAPH = [
    {'@id': 'mg:Dev', '@type': 'owl:Class', 'rdfs:label': '储能设备'},
    {'@id': 'mg:power', '@type': 'owl:DatatypeProperty', 'rdfs:label': '功率',
     'rdfs:domain': {'@id': 'mg:Dev'}, 'mg:apiName': 'power', 'rdfs:range': {'@id': 'xsd:double'}},
    {'@id': 'mg:temp', '@type': 'owl:DatatypeProperty', 'rdfs:label': '温度',
     'rdfs:domain': {'@id': 'mg:Dev'}, 'mg:apiName': 'temp', 'rdfs:range': {'@id': 'xsd:double'}},
]
VONT = {'ontology': {'@context': {}, '@graph': copy.deepcopy(GRAPH)}, 'workflow': {}}
VONT_ACT = {'ontology': {'@context': {}, '@graph': copy.deepcopy(GRAPH)},
            'workflow': {'actions': [{'id': 'act1', 'name': '停', 'description': 'd',
                                      'definitionVersion': 2, 'status': 'active'}],
                         'actionAssociations': [{'objectTypeId': 'mg:Dev', 'actionId': 'act1'}]}}

NUMBER_OUT = [{'id': 'nd_out', 'name': 'value', 'label': '值', 'type': {'type': 'number'}}]


def reg_binding(props):
    """登记实例对象映射：无 connection/table/primary_key，属性全部走 flow 来源。"""
    return {'object_type': 'Dev', 'connection': '', 'table': '', 'primary_key': '',
            'title_key': '', 'identity': {'kind': 'registered',
                                          'instances': [{'id': 'D-001', 'label': '设备一号'}]},
            'properties': props, 'relations': [], 'sources': [], 'related_sources': []}


def vstate(conn_list, props, action_bindings=None):
    return {'projectId': 'b01rp', 'name': '依赖上下文复验', 'ontologyId': 'm',
            'ontologyVersion': '1.0.0',
            'connections': {'connections': conn_list},
            'bindings': {'object_bindings': [reg_binding(props)],
                         'actionBindings': action_bindings or []},
            'implementations': [], 'parameters': {}}


def mk_flow(name, node):
    fid = flows.create(name)['id']
    st = flows.read_draft(fid)
    st.pop('_draft')
    st['outputs'] = [{'id': 'out_p', 'name': 'power', 'label': '功率', 'type': {'type': 'number'}}]
    st['nodes'] = [node]
    st['outputs'][0]['binding'] = {'kind': 'node', 'nodeId': 'nd', 'outputId': 'nd_out'}
    flows.save_draft(st)
    return fid


def F_SRC(fid):
    return {'kind': 'flow', 'flow': fid, 'output': 'out_p', 'inputs': {}}


# —— 各类编排节点 ——
NODE_FORMULA = {'id': 'nd', 'kind': 'calc', 'name': '计算', 'inputs': [], 'outputs': NUMBER_OUT,
                'implementation': {'mode': 'formula', 'formulas': {'value': '1'}}}
NODE_REDIS_GONE = {'id': 'nd', 'kind': 'redis', 'name': '读SOC', 'inputs': [], 'outputs': NUMBER_OUT,
                   'implementation': {'connectionId': 'deleted-redis', 'command': 'GET',
                                      'args': ['1'], 'keyTemplate': 'soc:1'}}
NODE_SQL_GONE = {'id': 'nd', 'kind': 'sql', 'name': '查功率', 'inputs': [], 'outputs': NUMBER_OUT,
                 'implementation': {'connectionId': 'deleted-mysql', 'sql': 'SELECT 1 AS value'}}
NODE_REDIS_OK = {'id': 'nd', 'kind': 'redis', 'name': '读SOC(合法)', 'inputs': [], 'outputs': NUMBER_OUT,
                 'implementation': {'connectionId': 'redis1', 'command': 'GET',
                                    'args': ['1'], 'keyTemplate': 'soc:1'}}
NODE_SQL_OK = {'id': 'nd', 'kind': 'sql', 'name': '查功率(合法)', 'inputs': [], 'outputs': NUMBER_OUT,
               'implementation': {'connectionId': 'mysql1', 'sql': 'SELECT 1 AS value'}}
NODE_PY_PROVIDER = {'id': 'nd', 'kind': 'python', 'name': 'LLM加工', 'inputs': [], 'outputs': NUMBER_OUT,
                    'implementation': {'code': 'def main():\n    return {"value": 1}\n',
                                       'providerId': 'lost-provider'}}
NODE_PY_NO_PROVIDER = {'id': 'nd', 'kind': 'python', 'name': '纯Python', 'inputs': [], 'outputs': NUMBER_OUT,
                       'implementation': {'code': 'def main():\n    return {"value": 1}\n'}}
NODE_HTTP_CRED = {'id': 'nd', 'kind': 'http', 'name': '接口取数', 'inputs': [], 'outputs': NUMBER_OUT,
                  'implementation': {'url': 'http://127.0.0.1:9/api', 'method': 'GET',
                                     'credentialId': 'lost-cred'}}
NODE_HTTP_NO_CRED = {'id': 'nd', 'kind': 'http', 'name': '匿名接口', 'inputs': [], 'outputs': NUMBER_OUT,
                     'implementation': {'url': 'http://127.0.0.1:9/api', 'method': 'GET'}}

CONN_REDIS = {'id': 'redis1', 'name': '缓存', 'engine': 'redis', 'host': '127.0.0.1', 'port': 6379}
CONN_MYSQL = {'id': 'mysql1', 'name': '库', 'engine': 'mysql', 'host': '127.0.0.1', 'port': 3306,
              'database': 'd', 'username': 'u'}

FLOW_FORMULA = mk_flow('B01公式编排', NODE_FORMULA)
FLOW_REDIS_GONE = mk_flow('B01Redis失联编排', NODE_REDIS_GONE)
FLOW_SQL_GONE = mk_flow('B01SQL失联编排', NODE_SQL_GONE)
FLOW_REDIS_OK = mk_flow('B01Redis合法编排', NODE_REDIS_OK)
FLOW_SQL_OK = mk_flow('B01SQL合法编排', NODE_SQL_OK)
FLOW_PROVIDER = mk_flow('B02显式提供方编排', NODE_PY_PROVIDER)
FLOW_PY_PLAIN = mk_flow('B02无提供方Python编排', NODE_PY_NO_PROVIDER)
FLOW_HTTP_CRED = mk_flow('B02凭据引用编排', NODE_HTTP_CRED)
FLOW_HTTP_PLAIN = mk_flow('B02匿名HTTP编排', NODE_HTTP_NO_CRED)

SECRET_MARK = 'sk-INJECT-DO-NOT-ECHO-9f2e'
ERR_PROVIDER = RuntimeError(f'llm catalog unavailable ({SECRET_MARK})')
ERR_CREDENTIAL = RuntimeError(f'credential catalog unavailable ({SECRET_MARK})')


def verr(state, ont=None):
    return validate_project(state, ont or VONT)['errors']


def has_read_failure(errors):
    return any('读取失败' in e for e in errors)


def leaks_secret(errors):
    joined = json.dumps(errors, ensure_ascii=False)
    return SECRET_MARK in joined or 'unavailable (' in joined


# ============ B01 · 明确空连接集合 ================================================

e = verr(vstate([], {'power': F_SRC(FLOW_REDIS_GONE)}))
check(any('数据连接不存在' in x for x in e),
      'B01 空连接项目 + Redis节点引用不存在连接 → 项目校验必须 error', e)
check(not has_read_failure(e), 'B01 明确空集合应报「不存在」，不得含糊成读取失败', e)

e = verr(vstate([], {'power': F_SRC(FLOW_SQL_GONE)}))
check(any('数据连接不存在' in x for x in e),
      'B01 空连接项目 + SQL节点引用不存在连接 → 同样 error', e)

e = verr(vstate([], {'power': F_SRC(FLOW_REDIS_GONE)},
                [{'id': 'ab1', 'objectTypeId': 'Dev', 'actionId': 'act1',
                  'implementation': {'kind': 'flow', 'flowId': FLOW_REDIS_GONE}}]), VONT_ACT)
check(any('数据连接不存在' in x for x in e) and
      any('动作绑定' in x and '数据连接不存在' in x for x in e),
      'B01 属性与动作两条绑定路径同时引用失联编排 → 两侧都不可绕过', e)

e = verr(vstate([dict(CONN_REDIS)], {'power': F_SRC(FLOW_REDIS_GONE)}))
check(any('数据连接不存在' in x for x in e),
      'B01 非空连接列表中的不存在ID → 明确「不存在」（既有语义回归）', e)

e = verr(vstate([dict(CONN_REDIS), dict(CONN_MYSQL)],
                {'power': F_SRC(FLOW_REDIS_OK), 'temp': F_SRC(FLOW_SQL_OK)}))
check(not any('数据连接' in x or '编排' in x for x in e),
      'B01 合法Redis/SQL连接元数据对照（不实际查询）→ 零连接类错误', e)

e = verr(vstate([], {'power': F_SRC(FLOW_FORMULA)}))
check(not any('编排' in x or '数据连接' in x for x in e),
      'B01 登记实例 + 纯公式编排 + 空连接集合 → 合法，不要求数据库身份表', e)

# ============ B02 · 依赖目录读取失败 ==============================================

with mock.patch.object(llm_providers, 'list_metadata', side_effect=ERR_PROVIDER):
    e = verr(vstate([], {'power': F_SRC(FLOW_PROVIDER)}))
check(has_read_failure(e) and any('提供方' in x for x in e),
      'B02 显式providerId + 模型目录读取异常 → 必须按「读取失败」阻断', e)
check(not any('不存在' in x for x in e),
      'B02 读取失败不得误报为「提供方不存在」', e)
check(not leaks_secret(e), 'B02 错误文案不回显异常原文/密钥细节', e)

with mock.patch.object(llm_providers, 'list_metadata', side_effect=ERR_PROVIDER):
    e = verr(vstate([], {'power': F_SRC(FLOW_FORMULA)}))
check(not e, 'B02 纯公式编排不被无关模型目录故障误伤', e)

with mock.patch.object(llm_providers, 'list_metadata', side_effect=ERR_PROVIDER):
    e = verr(vstate([], {'power': F_SRC(FLOW_PY_PLAIN), 'temp': F_SRC(FLOW_HTTP_CRED)}))
check(not has_read_failure(e),
      'B02 不声明providerId的Python编排不因模型目录故障阻断（与既有空列表降级一致）', e)
check(any('凭据' in x and '不存在' in x for x in e)
      and not any('凭据' in x and '读取失败' in x for x in e),
      'B02 凭据目录可读时引用不存在凭据照常判「不存在」（模型故障不干扰凭据三态）', e)
check(not leaks_secret(e), 'B02 凭据故障文案不回显异常原文', e)

with mock.patch.object(api_credentials, 'ids', side_effect=ERR_CREDENTIAL):
    e = verr(vstate([], {'power': F_SRC(FLOW_HTTP_CRED)}))
check(any('读取失败' in x for x in e), 'B02 凭据引用编排：ids()异常必须阻断', e)

with mock.patch.object(llm_providers, 'list_metadata', side_effect=ERR_PROVIDER), \
        mock.patch.object(api_credentials, 'ids', side_effect=ERR_CREDENTIAL):
    e = verr(vstate([], {'power': F_SRC(FLOW_HTTP_PLAIN)}))
check(not e, 'B02 匿名HTTP编排不被模型+凭据双故障误伤', e)

# —— 缓存顺序无关：同一 validate_project 内先查无依赖编排再查有依赖编排（及反序）——
with mock.patch.object(llm_providers, 'list_metadata', side_effect=ERR_PROVIDER):
    e1 = verr(vstate([], {'power': F_SRC(FLOW_FORMULA), 'temp': F_SRC(FLOW_PROVIDER)}))
    e2 = verr(vstate([], {'power': F_SRC(FLOW_PROVIDER), 'temp': F_SRC(FLOW_FORMULA)}))
check(has_read_failure(e1) and has_read_failure(e2) and len(e1) == len(e2),
      'B02 同次校验中编排先后顺序不改变结论（公式在前/在后均须只报一次提供方读取失败）',
      {'forward': e1, 'reverse': e2})
check(sum('读取失败' in x for x in e1) == 1,
      'B02 顺序测试：仅依赖模型的编排产生一条阻断，公式编排不受缓存影响', e1)

# —— 明确不存在（读取成功）与读取失败必须区分 ——
e = verr(vstate([], {'power': F_SRC(FLOW_PROVIDER)}))
check(any('提供方不存在' in x or 'LLM 提供方不存在' in x for x in e) and not has_read_failure(e),
      'B02 目录可读但不含该providerId → 明确「提供方不存在」（非读取失败）', e)

with mock.patch.object(api_credentials, 'ids', return_value={'cred-x'}):
    fid_known = mk_flow('B02已知凭据编排', {**NODE_HTTP_CRED,
                                           'implementation': {**NODE_HTTP_CRED['implementation'],
                                                              'credentialId': 'cred-x'}})
    e = verr(vstate([], {'power': F_SRC(fid_known)}))
check(not e, 'B02 合法凭据元数据对照（ids可读且含引用ID）→ 零错误', e)

with mock.patch.object(llm_providers, 'list_metadata',
                       return_value=[{'id': 'lost-provider', 'name': '恢复的提供方'}]):
    e = verr(vstate([], {'power': F_SRC(FLOW_PROVIDER)}))
check(not e, 'B02 依赖恢复后重新校验成功（providerId可解析）', e)

# ============ 正式发布路由边界 =====================================================

MODEL = model_routes.blank_state('依赖上下文复验本体')
MODEL['ontology']['@graph'].extend(copy.deepcopy(GRAPH))
mid = workspaces.create('依赖上下文复验本体', copy.deepcopy(MODEL))['id']
mstate = copy.deepcopy(MODEL)
mstate['workspaceId'] = mid
mstate['metrics'] = {'metrics': []}
mstate['rules'] = {'rules': []}
workspaces.write_draft(copy.deepcopy(mstate))
resp, status = model_routes.post_publish({'state': encode_state(mstate),
                                          'revision': workspaces.current_token(mid),
                                          'changeType': 'initial'})
check(status == 200 and resp.get('version') == '1.0.0', '路由前置：模型发布 1.0.0', (status, resp))


def route_project(label, props, apply_patch=None):
    pid = projects.create(label, mid, '1.0.0')['id']
    pstate, _ = projects.load(pid)
    prev = projects.current_token(pid)
    pstate['connections'] = {'connections': []}
    pstate['bindings']['object_bindings'] = [reg_binding(props)]
    resp, status = _routes.post_project_write(
        {'state': copy.deepcopy(pstate), 'revision': prev, 'requestId': f'{label}-save'},
        '/api/project-save')
    check(status == 200, f'{label} 前置：草稿保存 200（保存不因依赖错误锁死）', (status, resp))
    prev = resp['revision']

    def releases():
        return [v.get('version') for v in projects.published_versions(pid)]

    ctx = mock.patch.object(llm_providers, 'list_metadata', side_effect=ERR_PROVIDER) \
        if apply_patch == 'provider' else \
        mock.patch.object(api_credentials, 'ids', side_effect=ERR_CREDENTIAL) \
        if apply_patch == 'credential' else None
    if ctx:
        with ctx:
            resp, status = _routes.post_project_write(
                {'state': copy.deepcopy(pstate), 'revision': prev,
                 'requestId': f'{label}-pub'}, '/api/project-publish')
    else:
        resp, status = _routes.post_project_write(
            {'state': copy.deepcopy(pstate), 'revision': prev,
             'requestId': f'{label}-pub'}, '/api/project-publish')
    return pid, prev, resp, status, releases


pid, prev, resp, status, releases = route_project('B01路由Redis', {'power': F_SRC(FLOW_REDIS_GONE)})
check(status == 422 and '数据连接不存在' in json.dumps(resp, ensure_ascii=False),
      'B01 空连接项目发布引用失联Redis连接的编排 → 发布路由 422', (status, resp))
check(releases() == [], 'B01 拒绝发布零新增版本', releases())
check(projects.current_token(pid) == prev, 'B01 拒绝发布草稿revision不推进',
      projects.current_token(pid))

pid, prev, resp, status, releases = route_project('B02路由提供方故障', {'power': F_SRC(FLOW_PROVIDER)},
                                                  apply_patch='provider')
check(status != 200 and '读取失败' in json.dumps(resp, ensure_ascii=False),
      'B02 显式providerId + 模型目录读取异常 → 发布路由受控失败', (status, resp))
check(SECRET_MARK not in json.dumps(resp, ensure_ascii=False), 'B02 发布响应不回显注入密钥串')
check(releases() == [], 'B02 模型故障拒绝后零新增版本', releases())

pid, prev, resp, status, releases = route_project('B02路由凭据故障', {'power': F_SRC(FLOW_HTTP_CRED)},
                                                  apply_patch='credential')
check(status != 200 and '读取失败' in json.dumps(resp, ensure_ascii=False),
      'B02 HTTP凭据引用 + 凭据目录读取异常 → 发布路由受控失败（补验收报告未测的凭据端到端反例）',
      (status, resp))
check(SECRET_MARK not in json.dumps(resp, ensure_ascii=False), 'B02 凭据故障响应不回显异常原文')
check(releases() == [], 'B02 凭据故障拒绝后零新增版本', releases())

# 恢复依赖 → 同项目重新发布成功
with mock.patch.object(llm_providers, 'list_metadata',
                       return_value=[{'id': 'lost-provider', 'name': '恢复的提供方'}]):
    resp, status = _routes.post_project_write(
        {'state': copy.deepcopy(projects.load(pid)[0]), 'revision': projects.current_token(pid),
         'requestId': 'b02-cred-recover'}, '/api/project-publish')
check(status == 422 and '凭据' in json.dumps(resp, ensure_ascii=False),
      'B02 仅恢复模型不恢复凭据：凭据项目仍须被拒（此时真实目录可读→明确「凭据不存在」）', (status, resp))
with mock.patch.object(llm_providers, 'list_metadata',
                       return_value=[{'id': 'lost-provider', 'name': '恢复的提供方'}]), \
        mock.patch.object(api_credentials, 'ids', return_value={'lost-cred'}):
    resp, status = _routes.post_project_write(
        {'state': copy.deepcopy(projects.load(pid)[0]), 'revision': projects.current_token(pid),
         'requestId': 'b02-cred-fix'}, '/api/project-publish')
check(status == 200 and resp.get('version'),
      'B02 依赖全部恢复后重新发布成功', (status, resp))

pid, prev, resp, status, releases = route_project('B01路由合法公式', {'power': F_SRC(FLOW_FORMULA)})
check(status == 200 and resp.get('version'),
      'B01 登记实例 + 纯公式编排 + 空连接 → 正式路由发布成功（合法对照不误伤）', (status, resp))

# ============ C01 · 空白依赖引用（2026-09-21 第二轮独立验收，硬断言化） ==================
# 判据与检查器底层一致：str(impl.get(key) or '') 的非空真值即「已声明」（不 strip）。
# 纯空白 ID 属「已声明但无效」：目录可读 → 不存在；目录读取失败 → 读取失败阻断；
# 两条路径都必须阻断发布（修复前：模型空集合/读取异常两态均 200 并新增版本）。
WHITESPACE_IDS = ['   ', '\t', '\n', ' \t \n ']


def _blank_flow(name, template, key, value):
    node = copy.deepcopy(template)
    node['implementation'][key] = value
    return mk_flow(name, node)


# —— 目录可读：空白 providerId / credentialId 必须判「不存在」（不得跳过） ——
for i, blank in enumerate(WHITESPACE_IDS):
    fid = _blank_flow(f'C01模型空白{i}', NODE_PY_PROVIDER, 'providerId', blank)
    with mock.patch.object(llm_providers, 'list_metadata', return_value=[]):
        e = verr(vstate([], {'power': F_SRC(fid)}))
    check(any('提供方' in x and ('不存在' in x or '尚未配置' in x) for x in e),
          f'C01 空白 providerId（{blank!r}）+ 模型空集合 → 必须报「不存在/未配置」，不得跳过', e)

for i, blank in enumerate(WHITESPACE_IDS):
    fid = _blank_flow(f'C01凭据空白{i}', NODE_HTTP_CRED, 'credentialId', blank)
    with mock.patch.object(api_credentials, 'ids', return_value=set()):
        e = verr(vstate([], {'power': F_SRC(fid)}))
    check(any('凭据' in x and '不存在' in x for x in e),
          f'C01 空白 credentialId（{blank!r}）+ 凭据空集合 → 必须报「不存在」，不得跳过', e)

# —— 目录可读但非空集合（不含该空白 ID）：同样必须判「不存在」 ——
fid_blank_provider = _blank_flow('C01模型空白非空集', NODE_PY_PROVIDER, 'providerId', '   ')
with mock.patch.object(llm_providers, 'list_metadata',
                       return_value=[{'id': 'other-provider', 'name': '别的模型'}]):
    e = verr(vstate([], {'power': F_SRC(fid_blank_provider)}))
check(any('提供方不存在' in x for x in e) and not has_read_failure(e),
      'C01 空白 providerId + 模型非空集合（不含该 ID）→ 明确「提供方不存在」（非读取失败）', e)

fid_blank_cred = _blank_flow('C01凭据空白非空集', NODE_HTTP_CRED, 'credentialId', '   ')
with mock.patch.object(api_credentials, 'ids', return_value={'other-cred'}):
    e = verr(vstate([], {'power': F_SRC(fid_blank_cred)}))
check(any('凭据' in x and '不存在' in x for x in e) and not has_read_failure(e),
      'C01 空白 credentialId + 凭据非空集合（不含该 ID）→ 明确「凭据不存在」（非读取失败）', e)

# —— 目录读取失败：空白 ID 同样按「已声明」走 fail-closed ——
for i, blank in enumerate(WHITESPACE_IDS):
    fid = _blank_flow(f'C01模型故障空白{i}', NODE_PY_PROVIDER, 'providerId', blank)
    with mock.patch.object(llm_providers, 'list_metadata', side_effect=ERR_PROVIDER):
        e = verr(vstate([], {'power': F_SRC(fid)}))
    check(has_read_failure(e) and not leaks_secret(e),
          f'C01 空白 providerId（{blank!r}）+ 模型目录读取异常 → 必须「读取失败」阻断且不回显原文', e)

for i, blank in enumerate(WHITESPACE_IDS):
    fid = _blank_flow(f'C01凭据故障空白{i}', NODE_HTTP_CRED, 'credentialId', blank)
    with mock.patch.object(api_credentials, 'ids', side_effect=ERR_CREDENTIAL):
        e = verr(vstate([], {'power': F_SRC(fid)}))
    check(has_read_failure(e) and not leaks_secret(e),
          f'C01 空白 credentialId（{blank!r}）+ 凭据目录读取异常 → 必须「读取失败」阻断且不回显原文', e)

# —— 真正未填写（键缺失/None/空字符串）仍按未声明：不读取目录、不被故障误伤 ——
FID_NO_PROVIDER_KEY = mk_flow('C01无provider键', {k: v for k, v in NODE_PY_NO_PROVIDER.items()})
FID_PROVIDER_NONE = _blank_flow('C01providerNone', NODE_PY_NO_PROVIDER, 'providerId', None)
FID_PROVIDER_EMPTY = _blank_flow('C01provider空串', NODE_PY_NO_PROVIDER, 'providerId', '')
FID_NO_CRED_KEY = mk_flow('C01无凭据键', {k: v for k, v in NODE_HTTP_NO_CRED.items()})
FID_CRED_NONE = _blank_flow('C01凭据None', NODE_HTTP_NO_CRED, 'credentialId', None)
FID_CRED_EMPTY = _blank_flow('C01凭据空串', NODE_HTTP_NO_CRED, 'credentialId', '')

with mock.patch.object(llm_providers, 'list_metadata', side_effect=ERR_PROVIDER):
    for fid, label in ((FID_NO_PROVIDER_KEY, '键缺失'), (FID_PROVIDER_NONE, 'None'), (FID_PROVIDER_EMPTY, '空字符串')):
        e = verr(vstate([], {'power': F_SRC(fid)}))
        check(not has_read_failure(e),
              f'C01 providerId {label} = 未声明 → 模型目录故障不误伤（不读取无关目录）', e)

with mock.patch.object(api_credentials, 'ids', side_effect=ERR_CREDENTIAL):
    for fid, label in ((FID_NO_CRED_KEY, '键缺失'), (FID_CRED_NONE, 'None'), (FID_CRED_EMPTY, '空字符串')):
        e = verr(vstate([], {'power': F_SRC(fid)}))
        check(not has_read_failure(e),
              f'C01 credentialId {label} = 未声明 → 凭据目录故障不误伤（不读取无关目录）', e)

# —— 正常 ID 的三态对照（可读/空集合/读取失败）不被本修订改变 ——
with mock.patch.object(llm_providers, 'list_metadata', return_value=[]):
    e = verr(vstate([], {'power': F_SRC(FLOW_PROVIDER)}))
check(any('不存在' in x or '尚未配置' in x for x in e) and not has_read_failure(e),
      'C01 对照：正常 providerId + 空集合 → 明确「不存在/未配置」（非读取失败）', e)
with mock.patch.object(api_credentials, 'ids', return_value=set()):
    e = verr(vstate([], {'power': F_SRC(FLOW_HTTP_CRED)}))
check(any('凭据' in x and '不存在' in x for x in e) and not has_read_failure(e),
      'C01 对照：正常 credentialId + 空集合 → 明确「不存在」（非读取失败）', e)

# —— 动作绑定路径同样不可绕过（空白 ID） ——
fid_act_blank = _blank_flow('C01动作绑定空白模型', NODE_PY_PROVIDER, 'providerId', '   ')
with mock.patch.object(llm_providers, 'list_metadata', return_value=[]):
    e = verr(vstate([], {'power': F_SRC(FLOW_FORMULA)},
                    [{'id': 'abC01', 'objectTypeId': 'Dev', 'actionId': 'act1',
                      'implementation': {'kind': 'flow', 'flowId': fid_act_blank}}]), VONT_ACT)
check(any('动作绑定' in x and '提供方' in x for x in e),
      'C01 动作绑定引用空白 providerId 编排 → 同样阻断（两条路径一致）', e)

# —— 正式发布路由：空白 ID 两态都必须受控失败、零新增版本、revision 不推进 ——
for dimension, template, key, patch_obj, method, mode, expect in (
        ('模型空白可读', NODE_PY_PROVIDER, 'providerId', llm_providers, 'list_metadata', 'empty', '提供方'),
        ('模型空白故障', NODE_PY_PROVIDER, 'providerId', llm_providers, 'list_metadata', 'failed', '读取失败'),
        ('凭据空白可读', NODE_HTTP_CRED, 'credentialId', api_credentials, 'ids', 'empty', '凭据'),
        ('凭据空白故障', NODE_HTTP_CRED, 'credentialId', api_credentials, 'ids', 'failed', '读取失败')):
    fid = _blank_flow(f'C01路由{dimension}', template, key, '   ')
    patch_args = ({'return_value': [] if patch_obj is llm_providers else set()}
                  if mode == 'empty' else {'side_effect': RuntimeError(f'injected-{dimension}')})
    with mock.patch.object(patch_obj, method, **patch_args):
        pid, prev, resp, status, releases = route_project(f'C01{dimension}', {'power': F_SRC(fid)})
        second_state = copy.deepcopy(projects.load(pid)[0])
        resp2, status2 = _routes.post_project_write(
            {'state': second_state, 'revision': projects.current_token(pid),
             'requestId': f'C01{dimension}-again'}, '/api/project-publish')
    check(status == 422 and expect in json.dumps(resp, ensure_ascii=False),
          f'C01 发布路由：{dimension} → 422 且文案含「{expect}」', (status, resp))
    check(releases() == [], f'C01 发布路由：{dimension} 零新增版本', releases())
    check(projects.current_token(pid) == prev, f'C01 发布路由：{dimension} 草稿 revision 不推进',
          projects.current_token(pid))
    check(status2 == 422 and releases() == [] and projects.current_token(pid) == prev,
          f'C01 发布路由：{dimension} 重复请求同样零写入', (status2, resp2))
    check('injected-' not in json.dumps(resp, ensure_ascii=False),
          f'C01 发布响应不回显注入异常原文（{dimension}）')

# —— 改正空白 ID 后必须可恢复发布（合规修复路径，不是死锁） ——
NODE_PY_PROVIDER_FIXED = {
    **NODE_PY_PROVIDER,
    'implementation': {**NODE_PY_PROVIDER['implementation'], 'providerId': 'fixed-provider'}}
FID_FIXED = mk_flow('C01改正后可发布', NODE_PY_PROVIDER_FIXED)
with mock.patch.object(llm_providers, 'list_metadata',
                       return_value=[{'id': 'fixed-provider', 'name': '修好的模型'}]):
    pid, prev, resp, status, releases = route_project('C01改正恢复', {'power': F_SRC(FID_FIXED)})
check(status == 200 and resp.get('version'),
      'C01 把空白 providerId 改成有效值（依赖可读）→ 重新发布成功（可恢复、未被锁死）',
      (status, resp))
check(releases() != [], 'C01 恢复发布确实产生版本（拒绝路径与恢复路径可区分）', releases())

print(f'\n汇总：{sum(1 for r in RESULTS if r[0] == "PASS")} 通过 / '
      f'{sum(1 for r in RESULTS if r[0] == "FAIL")} 失败')
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if any(r[0] == 'FAIL' for r in RESULTS) else 0)
