"""Codex 独立复验（2026-09-20 五项合并验收）· 后端反例重演。

运行（工作树根）：python3 tests/codex_reacceptance_backend_20260920.py
隔离：WIZ_WORKBENCH_ROOT=<mktemp> + 独立 WIZ_DATABASE_URL（临时根内 SQLite），
假账号进程内直调路由函数；不启动/连接任何已有服务端口，不触碰真实 ontology/ 与库。
与实施者测试（test_project_flow_source / test_publish_guards / test_business_rules）
独立：本文件自行构造数据与断言，覆盖：
- R02：空壳编排校验阻断与文案区分、合法编排不误拦、发布路由 422 零版本；
- R03：目录在校验执行中变化（首 token 之后、提交复核之前的窗口）→ 409 零写入；
       拒绝后同 requestId 重试恰好新增一个版本；成功回执重放不重复发布；同 key 异内容 409；
- A01：正式发布路由拒绝非文本 content/effect 且零新增版本；必填 null 定位文案；
       含非法旧记录的草稿编辑保存不被阻断（发布仍全量检查）。
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

TMP = Path(tempfile.mkdtemp(prefix='wiz_codex_reaccept_'))
os.environ['WIZ_WORKBENCH_ROOT'] = str(TMP)
os.environ['WIZ_DATABASE_URL'] = 'sqlite:///' + str(TMP / 'data' / 'workbench.sqlite3')
os.environ.pop('WIZ_WORKBENCH_PORT', None)

import auth_client  # noqa: E402
auth_client.bind_fixture_user()

from workbench import flows, model_routes, projects, versions, workspaces  # noqa: E402
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


# ============ R02 · 校验层反例（独立夹具） =========================================

GRAPH = [
    {'@id': 'mg:Dev', '@type': 'owl:Class', 'rdfs:label': '储能设备'},
    {'@id': 'mg:code', '@type': 'owl:DatatypeProperty', 'rdfs:label': '编号',
     'rdfs:domain': {'@id': 'mg:Dev'}, 'mg:apiName': 'code', 'rdfs:range': {'@id': 'xsd:string'}},
    {'@id': 'mg:power', '@type': 'owl:DatatypeProperty', 'rdfs:label': '功率',
     'rdfs:domain': {'@id': 'mg:Dev'}, 'mg:apiName': 'power', 'rdfs:range': {'@id': 'xsd:double'}},
]


def vstate(props, extra=None):
    st = {'projectId': 'rvp', 'name': '复验项目', 'ontologyId': 'm', 'ontologyVersion': '1.0.0',
          'connections': {'connections': [{'id': 'c1', 'name': '库', 'engine': 'mysql'}]},
          'bindings': {'object_bindings': [{'object_type': 'Dev', 'connection': 'c1',
                                            'table': 't', 'primary_key': 'id',
                                            'properties': copy.deepcopy(props)}],
                       'actionBindings': []},
          'implementations': [], 'parameters': {}}
    if extra:
        st.update(extra)
    return st


VONT = {'ontology': {'@context': {}, '@graph': copy.deepcopy(GRAPH)}, 'workflow': {}}


def verr(props, extra=None):
    return validate_project(vstate(props, extra), VONT)['errors']


def rv_flow(name, wired):
    fid = flows.create(name)['id']
    st = flows.read_draft(fid)
    st.pop('_draft')
    st['outputs'] = [{'id': 'out_p', 'name': 'power', 'label': '功率', 'type': {'type': 'number'}}]
    if wired:
        st['nodes'] = [{'id': 'nd', 'kind': 'calc', 'name': '计算', 'inputs': [],
                        'outputs': [{'id': 'nd_out', 'name': 'value', 'label': '值',
                                     'type': {'type': 'number'}}],
                        'implementation': {'mode': 'formula', 'formulas': {'value': '1'}}}]
        st['outputs'][0]['binding'] = {'kind': 'node', 'nodeId': 'nd', 'outputId': 'nd_out'}
    flows.save_draft(st)
    return fid


FLOW_SHELL = rv_flow('空壳编排R02', wired=False)
FLOW_OK = rv_flow('合法编排R02', wired=True)
F_SRC = lambda fid: {'kind': 'flow', 'flow': fid, 'output': 'out_p', 'inputs': {}}
FIELD = {'kind': 'field', 'field': 'code'}

shell_errors = verr({'code': dict(FIELD), 'power': F_SRC(FLOW_SHELL)})
check(any('尚未绑定来源节点输出' in e for e in shell_errors),
      'R02 空壳编排（只有输出声明、无来源绑定）→ 项目校验必须报错', shell_errors)
check(not any('不存在' in e or '读取失败' in e for e in shell_errors),
      'R02 空壳编排不得被误报为不存在/读取失败', shell_errors)

ok_errors = verr({'code': dict(FIELD), 'power': F_SRC(FLOW_OK)})
check(not any('编排' in e for e in ok_errors),
      'R02 合法编排绑定必须通过（不因缺项目级模型上下文误拦）', ok_errors)

gone = verr({'code': dict(FIELD), 'power': F_SRC('no-such-flow-id')})
check(any('不存在或已删除' in e for e in gone), 'R02 不存在的编排 → 「不存在或已删除」', gone)

from unittest import mock  # noqa: E402
with mock.patch.object(flows, 'dependency_state',
                       lambda fid: {'status': 'unreadable', 'reason': 'boom'}):
    unread = verr({'code': dict(FIELD), 'power': F_SRC(FLOW_OK)})
check(any('读取失败' in e for e in unread) and not any('不存在' in e for e in unread),
      'R02 编排读取失败 → 按「读取失败」阻断且与不存在区分', unread)

# 动作绑定路径同样阻断
VONT_ACT = {'ontology': {'@context': {}, '@graph': copy.deepcopy(GRAPH)},
            'workflow': {'actions': [{'id': 'act1', 'name': '停', 'description': 'd',
                                      'definitionVersion': 2, 'status': 'active'}],
                         'actionAssociations': [{'objectTypeId': 'mg:Dev', 'actionId': 'act1'}]}}
act_state = vstate({'code': dict(FIELD)},
                   {'bindings': {'object_bindings': [{'object_type': 'Dev', 'connection': 'c1',
                                                      'table': 't', 'primary_key': 'id',
                                                      'properties': {'code': dict(FIELD)}}],
                                  'actionBindings': [{'id': 'ab1', 'objectTypeId': 'Dev',
                                                      'actionId': 'act1',
                                                      'implementation': {'kind': 'flow',
                                                                         'flowId': FLOW_SHELL}}]}})
act_errors = validate_project(act_state, VONT_ACT)['errors']
check(any('尚未绑定来源节点输出' in e for e in act_errors),
      'R02 动作绑定引用空壳编排 → 同样阻断', act_errors)

# 签名不变、内部结构变坏：把合法编排的输出绑定摘掉（声明保留）→ 仍须识别
bad = flows.read_draft(FLOW_SHELL)
st_broken = flows.read_draft(FLOW_OK)
st_broken.pop('_draft')
st_broken['nodes'] = [{'id': 'nd', 'kind': 'calc', 'name': '计算', 'inputs': [],
                       'outputs': [{'id': 'nd_out', 'name': 'value', 'label': '值',
                                    'type': {'type': 'number'}}],
                       'implementation': {}}]  # 实现整体失效，签名不变
flows.save_draft(st_broken)
broken_errors = verr({'code': dict(FIELD), 'power': F_SRC(FLOW_OK)})
check(bool(broken_errors), 'R02 签名不变但节点实现变坏 → 仍必须阻断', broken_errors)

# ============ R02/R03 · 路由级发布语义 ============================================

MODEL = model_routes.blank_state('复验本体')
MODEL['ontology']['@graph'].extend([n for n in copy.deepcopy(GRAPH)])
mk = workspaces.create('复验本体', copy.deepcopy(MODEL))
mid = mk['id']
mstate = copy.deepcopy(MODEL)
mstate['workspaceId'] = mid
mstate['metrics'] = {'metrics': []}
mstate['rules'] = {'rules': []}
workspaces.write_draft(copy.deepcopy(mstate))
resp, status = model_routes.post_publish({'state': encode_state(mstate),
                                          'revision': workspaces.current_token(mid),
                                          'changeType': 'initial'})
check(status == 200 and resp.get('version') == '1.0.0', '复验前置：模型发布 1.0.0', (status, resp))

pid = projects.create('复验项目R02', mid, '1.0.0')['id']
pstate, _ = projects.load(pid)
prev = projects.current_token(pid)
pstate['connections'] = {'connections': [{'id': 'c1', 'name': '库', 'engine': 'mysql',
                                          'host': '127.0.0.1', 'port': 3306,
                                          'username': 'u', 'tls': 'none', 'database': 'd'}]}
pstate['bindings']['object_bindings'] = [{
    'object_type': 'Dev', 'connection': 'c1', 'table': 't1', 'primary_key': 'id',
    'properties': {'code': dict(FIELD), 'power': F_SRC(FLOW_SHELL)}}]
from workbench import catalogs as catalogs_mod  # noqa: E402


def write_catalog(tables):
    bind = auth_client.bind_fixture_user()  # noqa: F841
    catalogs_mod.store(pid, 'c1', {'database': 'd', 'refreshedAt': '2026-09-20T00:00:00+00:00',
                                   'tables': tables})


CAT_BASE = [{'name': 't1', 'kind': 'table', 'fields': [
    {'name': 'id', 'dataType': 'bigint', 'key': 'pri', 'comment': ''},
    {'name': 'code', 'dataType': 'varchar', 'key': '', 'comment': ''}]}]
CAT_CHANGED = copy.deepcopy(CAT_BASE)
CAT_CHANGED[0]['fields'].append({'name': 'extra', 'dataType': 'double', 'key': '', 'comment': ''})

def releases():
    return [e.get('version') for e in projects.published_versions(pid)]


resp, status = _routes.post_project_write(
    {'state': copy.deepcopy(pstate), 'revision': prev, 'requestId': 'rv-save-1'},
    '/api/project-save')
check(status == 200, '复验前置：保存含空壳编排引用的草稿 200', (status, resp))
prev = resp['revision']
write_catalog(CAT_BASE)
labels0 = releases()

resp, status = _routes.post_project_write(
    {'state': copy.deepcopy(pstate), 'revision': prev, 'requestId': 'rv-r02-pub'},
    '/api/project-publish')
check(status == 422 and '尚未绑定来源节点输出' in json.dumps(resp, ensure_ascii=False),
      'R02 空壳编排被项目属性引用 → 发布路由 422 且报结构错误', (status, resp))
check(releases() == labels0, 'R02 拒绝发布不新增版本', releases())

# 换合法编排：恢复上面被摘坏的实现
st_fix = flows.read_draft(FLOW_OK)
st_fix.pop('_draft')
st_fix['nodes'] = [{'id': 'nd', 'kind': 'calc', 'name': '计算', 'inputs': [],
                    'outputs': [{'id': 'nd_out', 'name': 'value', 'label': '值',
                                 'type': {'type': 'number'}}],
                    'implementation': {'mode': 'formula', 'formulas': {'value': '1'}}}]
flows.save_draft(st_fix)
pstate_ok = copy.deepcopy(pstate)
pstate_ok['bindings']['object_bindings'][0]['properties']['power'] = F_SRC(FLOW_OK)
resp, status = _routes.post_project_write(
    {'state': copy.deepcopy(pstate_ok), 'revision': prev, 'requestId': 'rv-r02-save2'},
    '/api/project-save')
check(status == 200, '复验：保存合法编排引用草稿 200', (status, resp))
prev = resp['revision']

# --- R03：目录在「依赖快照已取、提交复核前」（校验执行中）变化 → 409 零写入 ---
real_validate = _routes._validate_with_degraded


def validate_with_catalog_change(state_in, ontology_state, degraded):
    write_catalog(CAT_CHANGED)  # 校验进行中改目录：晚于同一读取基线，早于事务复核
    return real_validate(state_in, ontology_state, degraded)


labels_before_r03 = releases()
with mock.patch.object(_routes, '_validate_with_degraded', validate_with_catalog_change):
    resp, status = _routes.post_project_write(
        {'state': copy.deepcopy(pstate_ok), 'revision': prev, 'requestId': 'rv-r03-win'},
        '/api/project-publish')
check(status == 409 and isinstance(resp, dict) and resp.get('reason') == 'DEPENDENCY_CHANGED',
      'R03 校验执行中目录变化（首 token 后、复核前）→ 409 DEPENDENCY_CHANGED', (status, resp))
check(releases() == labels_before_r03, 'R03 拒绝路径零版本写入', releases())
check(projects.current_token(pid) == prev,
      'R03 拒绝路径草稿 revision 不推进（无部分写入）', projects.current_token(pid))

# 同 requestId 在依赖稳定后重试：应正常处理并发布（不误重放失败、不重复发布）
resp, status = _routes.post_project_write(
    {'state': copy.deepcopy(pstate_ok), 'revision': prev, 'requestId': 'rv-r03-win'},
    '/api/project-publish')
check(status == 200 and isinstance(resp, dict) and resp.get('version'),
      'R03 409 后同 requestId 重试按当前一致基线重验并发布', (status, resp))
labels_after_retry = releases()
check(len(labels_after_retry) == len(labels_before_r03) + 1,
      '重试恰好新增一个版本（不重复）', labels_after_retry)

# 成功回执重放：同 key 同内容 → 回放、不新增
resp2, status2 = _routes.post_project_write(
    {'state': copy.deepcopy(pstate_ok), 'revision': prev, 'requestId': 'rv-r03-win'},
    '/api/project-publish')
check(status2 == 200 and resp2.get('idempotentReplay') is True
      and resp2.get('version') == resp.get('version'),
      'R03 同 key 同内容重放返回既有回执（idempotentReplay）', (status2, resp2))
check(releases() == labels_after_retry, '重放不新增版本', releases())

# 同 key 异内容 → 409 冲突（回执指纹先于校验比较）
different = copy.deepcopy(pstate_ok)
different['bindings']['object_bindings'][0]['table'] = 't1_codex_changed'
resp3, status3 = _routes.post_project_write(
    {'state': different, 'revision': resp['revision'], 'requestId': 'rv-r03-win'},
    '/api/project-publish')
check(status3 == 409, 'R03 同 key 异内容 → 409 幂等冲突', (status3, resp3))
check(releases() == labels_after_retry, '幂等冲突不新增版本', releases())

# ============ A01 · 正式路由字段类型边界（独立值） ==================================

RULE = {'id': 'rv-rule', 'name': '复验规则', 'description': '复验定义', 'content': '复验内容'}


def rule_state(mut=None):
    st = copy.deepcopy(mstate)
    rule = dict(RULE)
    if mut:
        mut(rule)
    st['workflow'] = {'businessRules': [rule], 'businessRuleAssociations': []}
    return st


a01_id = workspaces.create('A01 复验本体', copy.deepcopy(MODEL))['id']


def labels(identifier):
    return [e['version'] for e in versions.listing(identifier)]


st_for_route = copy.deepcopy(rule_state())
st_for_route['workspaceId'] = a01_id
workspaces.write_draft(copy.deepcopy(st_for_route))
resp, status = model_routes.post_publish(
    {'state': encode_state(st_for_route), 'revision': workspaces.current_token(a01_id),
     'changeType': 'initial'})
check(status == 200 and resp.get('version') == '1.0.0', 'A01 前置：合法文本规则发布 1.0.0',
      (status, resp))
labels0 = labels(a01_id)

for bad_value in ({'w': 1}, ['w'], 42, False):
    bad = copy.deepcopy(st_for_route)
    bad['workflow']['businessRules'][0]['content'] = bad_value
    resp, status = model_routes.post_publish(
        {'state': encode_state(bad), 'revision': workspaces.current_token(a01_id),
         'changeType': 'initial'})
    joined = json.dumps(resp, ensure_ascii=False)
    check(status == 422 and '必须是文本' in joined,
          f'A01 content={bad_value!r} → 正式路由 422「必须是文本」', (status, resp))
    check(labels(a01_id) == labels0, f'A01 content={bad_value!r} 拒绝后零新增版本', labels(a01_id))

nulldesc = copy.deepcopy(st_for_route)
nulldesc['workflow']['businessRules'][0]['description'] = None
resp, status = model_routes.post_publish(
    {'state': encode_state(nulldesc), 'revision': workspaces.current_token(a01_id),
     'changeType': 'initial'})
joined = json.dumps(resp, ensure_ascii=False)
check(status == 422 and '缺少业务定义' in joined and 'NoneType' not in joined,
      'A01 description=null → 422 定位文案且不暴露 NoneType', (status, resp))

# 含非法旧记录的草稿：编辑无关字段仍可保存（不被整稿必填锁死），发布仍被全量检查拦下
dirty = copy.deepcopy(st_for_route)
dirty['workflow']['businessRules'].append({'id': 'rv-bad', 'name': '旧脏记录',
                                           'description': '有效', 'content': {'legacy': True}})
saved, _s = model_routes.post_save(
    {'state': encode_state(dirty), 'revision': workspaces.current_token(a01_id)})
check(saved.get('revision') if isinstance(saved, dict) else False,
      'A01 含非法旧 content 的草稿允许保存（errors 提示但不锁死）', saved)
resp, status = model_routes.post_publish(
    {'state': encode_state(dirty), 'revision': workspaces.current_token(a01_id),
     'changeType': 'compatible'})
check(status == 422 and '必须是文本' in json.dumps(resp, ensure_ascii=False),
      'A01 发布仍检查全部定义：脏记录存在时拒绝发布', (status, resp))
check(labels(a01_id) == labels0, 'A01 脏记录发布拒绝后仍零新增版本', labels(a01_id))

# 动作侧：effect 非文本经正式路由 422（v2 动作），合法缺键可发布
ACT = {'id': 'rv-act', 'name': '复验动作', 'description': '复验定义',
       'definitionVersion': 2, 'status': 'active'}
act_id = workspaces.create('A01 动作复验本体', copy.deepcopy(MODEL))['id']
act_full = copy.deepcopy(mstate)
act_full['workspaceId'] = act_id
act_full['metrics'] = {'metrics': []}
act_full['rules'] = {'rules': []}
act_full['workflow'] = {'actions': [dict(ACT)], 'actionAssociations': []}
workspaces.write_draft(copy.deepcopy(act_full))
resp, status = model_routes.post_publish(
    {'state': encode_state(act_full), 'revision': workspaces.current_token(act_id),
     'changeType': 'initial'})
check(status == 200 and resp.get('version') == '1.0.0',
      'A01 动作前置：无 effect 键的最小 v2 动作可发布', (status, resp))
act_labels0 = labels(act_id)
for bad_value in ([{'w': 1}], 7, True, {'a': {'b': 2}}):
    bad = copy.deepcopy(act_full)
    bad['workflow']['actions'][0]['effect'] = bad_value
    resp, status = model_routes.post_publish(
        {'state': encode_state(bad), 'revision': workspaces.current_token(act_id),
         'changeType': 'initial'})
    check(status == 422 and '必须是文本' in json.dumps(resp, ensure_ascii=False),
          f'A01 动作 effect={bad_value!r} → 422「必须是文本」', (status, resp))
    check(labels(act_id) == act_labels0, f'A01 动作 effect={bad_value!r} 零新增版本',
          labels(act_id))

print(f'\n汇总：{sum(1 for r in RESULTS if r[0] == "PASS")} 通过 / '
      f'{sum(1 for r in RESULTS if r[0] == "FAIL")} 失败')
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if any(r[0] == 'FAIL' for r in RESULTS) else 0)
