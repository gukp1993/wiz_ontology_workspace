"""动作库与对象动作关联后端回归（20260917 需求，20260920 字段精简后更新）。

覆盖：v2 动作校验（20260920：名称/业务定义必填；预期效果 effect 选填、为空不阻断）
与旧校验隔离、历史动作零丢失、actionAssociations 校验
（去重/悬空引用/删除保护）、effective 关联推导（显式 ∪ 历史动作 object_type）、
contracts.classify 变更分类（文本变化=pending、删除动作/关联=breaking、新增=compatible）、
项目 actionBindings 校验（组合隔离、失效阻塞、未绑定不阻塞、编排存在性、未知结构零丢失）、
发布快照携带关联与绑定、状态往返不丢字段（R05：effect 原键读写、有值保留）。

纯 python3 标准库直跑；WIZ_WORKBENCH_ROOT 挂临时数据根，真实 ontology/ 只读不碰。
运行：python3 tests/test_action_library.py
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
TMP = Path(tempfile.mkdtemp(prefix='wiz_actions_'))
os.environ['WIZ_WORKBENCH_ROOT'] = str(TMP)

from workbench import contracts, projects, versions, workspaces  # noqa: E402  （临时根就位后再 import）
from workbench.model_format import decode_state, encode_state  # noqa: E402
from workbench.project_validation import validate_project  # noqa: E402
from workbench.workflow import (definition_errors, effective_action_associations,  # noqa: E402
                                is_action_v2)

# 账号体系（20260918）：域级测试需绑定测试账号作为当前用户
from pathlib import Path as _P
sys.path.insert(0, str(_P(__file__).resolve().parent))
import auth_client as _auth_client
_auth_client.bind_fixture_user()

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


GRAPH = [
    {'@id': 'mg:StorageDevice', '@type': 'owl:Class', 'rdfs:label': '储能设备'},
    {'@id': 'mg:StorageCluster', '@type': 'owl:Class', 'rdfs:label': '储能簇'},
    {'@id': 'mg:StorageSystem', '@type': 'owl:Class', 'rdfs:label': '储能系统'},
]


def onto_state(actions=None, assoc=None):
    return {'ontology': {'@context': {}, '@graph': copy.deepcopy(GRAPH)},
            'workflow': {'actions': copy.deepcopy(actions or []),
                         'actionAssociations': copy.deepcopy(assoc or [])}}


A2 = {'id': 'act_stop', 'name': '停止充放电', 'description': '请求目标对象停止当前充电或放电',
      'effect': '请求停止充放电，目标功率为 0，以设备反馈确认完成', 'definitionVersion': 2, 'status': 'experimental'}
LEGACY = {'id': 'action.change_system', 'name': '调整所属系统', 'description': '调整设备归属',
          'object_type': 'StorageDevice', 'inputs': [{'name': 'device', 'type': 'object', 'required': True}],
          'effect': '结束原归属、建立新归属', 'criteria': '存在且不冲突', 'permission': '资产管理员',
          'acceptance': '设备A 转到系统二', 'status': 'active', 'implementation_ref': ''}


def proj_state(bindings=None):
    return {'projectId': 'p1', 'name': '动作项目', 'ontologyId': 'storage', 'ontologyVersion': '1.0.0',
            'connections': {'connections': []},
            'bindings': {'notice': '', 'object_bindings': [], 'observation_binding': {},
                         'source_candidates': [], 'actionBindings': copy.deepcopy(bindings or [])},
            'implementations': [], 'parameters': {}}


# --- 1. v2 动作校验（验收 1/7；20260920 字段精简：仅名称/业务定义必填） -----------------

errs = definition_errors(onto_state([dict(A2)]))
check(not errs, '验收1 v2 动作仅三字段即可通过校验，不再要求对象/参数/条件/权限/验收', errs)

minimal = {'id': 'act_minimal', 'name': '最小动作', 'description': '只填名称与业务定义',
           'definitionVersion': 2, 'status': 'experimental'}
check(not definition_errors(onto_state([dict(minimal)])),
      'R01/R05 v2 动作缺 effect 键即可通过校验（预期效果选填）', definition_errors(onto_state([dict(minimal)])))
for value in ('', '   ', None):
    errs = definition_errors(onto_state([dict(A2, effect=value)]))
    check(not errs, f'R05 v2 动作 effect 为 {value!r} 时报错已移除（空值按未填处理）', errs)
for key, title in (('name', '名称'), ('description', '业务描述')):
    errs = definition_errors(onto_state([dict(A2, **{key: '  '})]))
    check(any(f'缺少{title}' in e for e in errs), f'R02 v2 动作缺 {title} 仍报错（必填项不变）', errs)

for field, keep in [('criteria', None), ('permission', None)]:
    record = {k: v for k, v in LEGACY.items() if k != field}
    errs = definition_errors(onto_state([dict(record)]))
    check(any(field in e for e in errs), f'验收7 历史动作缺 {field} 仍按旧校验报错', errs)

errs = definition_errors(onto_state([dict(LEGACY)]))
check(any('参数引用不存在的对象类型' in e for e in errs) and not any('动作定义未完整' in e for e in errs),
      '完整历史动作走旧校验路径（输入参数仍被检查；四字段齐全不再报未完整）', errs)
check(is_action_v2(A2) and not is_action_v2(LEGACY), 'definitionVersion 区分新旧格式')

# 兼容性审计最小修复：历史动作的 effect/criteria 等为 JSON null 时按「未填」报缺失，不再抛 AttributeError
# （原实现 n.get(key,'').strip() 会让 definition_errors 崩溃，进而使保存/发布整体 500）
for value in (None, 0, {'a': 1}, ['x']):
    errs = definition_errors(onto_state([dict(LEGACY, effect=value, status='active')]))
    check(any('动作定义未完整：effect' in e for e in errs),
          f'历史动作 effect 为 {value!r} 时按未填报缺失（不崩溃、不悄悄转字符串）', errs)

# v2 动作的 effect 为 null/空白仍按未填处理（选填语义保持；A01 后非文本改为受控报错，见下）
for value in (None, '', '   '):
    check(not definition_errors(onto_state([dict(A2, effect=value)])),
          f'R05 v2 动作 effect 为 {value!r} 时零错误', definition_errors(onto_state([dict(A2, effect=value)])))

# A01（2026-09-20 验收修复）：v2 动作 effect 非文本（对象/数组/数值/布尔）必须受控报错，
# 不再被静默跳过（此前 effect 完全不检查，数组/对象可正式发布）
# （20260921 合并口径：文案统一为 test 分支富格式「必须为文本」，断言随之同步）
for value in ({'wrong': 'object'}, ['wrong'], 3, True):
    errs = definition_errors(onto_state([dict(A2, effect=value)]))
    check(any('必须为文本' in e for e in errs),
          f'A01 v2 动作 effect 为 {value!r} → 校验报「必须为文本」', errs)
null_desc_action = definition_errors(onto_state([dict(A2, description=None)]))
check(any('缺少业务描述' in e and '停止充放电' in e for e in null_desc_action),
      'A01 v2 动作 description=null → 定位动作与字段的可读文案（不再 AttributeError 泛化）', null_desc_action)
check(not any('NoneType' in e for e in null_desc_action), 'A01 description=null 文案不暴露 NoneType', null_desc_action)
check(not definition_errors(onto_state([dict(A2, name='停止充放电', description='d')])),
      'A01 最小 v2 动作（无 effect 键）仍零错误')

# A01（20260921）：v2 动作 effect 选填但提供时必须为文本——数字/布尔/数组/对象逐类型拒绝，
# 单条消息同时含动作名称、标识、字段名（effect）、「必须为文本」原因与修复建议；不隐式转字符串
for bad in (1, 3.14, True, ['功率为 0'], {'效果': 'x'}):
    errs = definition_errors(onto_state([dict(A2, effect=bad)]))
    matched = [e for e in errs if '必须为文本' in e and 'effect' in e]
    check(len(matched) == 1
          and '「停止充放电」(act_stop)' in matched[0]
          and f'当前类型 {type(bad).__name__}' in matched[0]
          and '请改为文本或删除该字段' in matched[0],
          f'A01 v2 动作 effect 为 {type(bad).__name__} 报恰好一条错误（单条消息含名称/标识/字段名/类型原因/建议）', errs)

# A01 不误伤：合法动作与非法 effect 记录并存（含合法历史动作），只有非法记录报错
mixed_actions = [dict(A2), dict(A2, id='act-bad', name='非法效果动作', effect=['a']), dict(LEGACY)]
errs = definition_errors(onto_state(mixed_actions))
matched = [e for e in errs if '必须为文本' in e]
check(len(matched) == 1 and 'act-bad' in matched[0] and 'effect' in matched[0]
      and not any('act_stop' in e for e in matched) and not any('action.change_system' in e for e in matched),
      'A01 合法/历史动作与非法 effect 动作并存：只有非法记录报单条错误（不误伤）', errs)

# --- 2. 关联集合校验（验收 4：删除保护；§5 去重/悬空） ------------------------------

errs = definition_errors(onto_state([dict(A2)], [{'objectTypeId': 'mg:StorageDevice', 'actionId': 'act_stop'}]))
check(not errs, '合法关联通过校验', errs)

errs = definition_errors(onto_state([dict(A2)], [{'objectTypeId': 'mg:StorageDevice', 'actionId': 'ghost'}]))
check(any('请先移除关联再删除动作' in e for e in errs), '删除仍被关联的动作被拒绝（后端删除保护）', errs)

errs = definition_errors(onto_state([dict(A2)], [
    {'objectTypeId': 'mg:Ghost', 'actionId': 'act_stop'},
    {'objectTypeId': 'mg:Ghost', 'actionId': 'act_stop'},
    {'objectTypeId': 'mg:StorageDevice', 'actionId': 'act_stop'},
    {'objectTypeId': 'mg:StorageDevice', 'actionId': 'act_stop'}]))
check(any('不存在的对象类型' in e for e in errs) and len([e for e in errs if '重复' in e]) == 2,
      '关联悬空对象类型报错；重复组合逐条报错', errs)

errs = definition_errors(onto_state([dict(A2)], [{'objectTypeId': 'StorageDevice', 'actionId': 'act_stop'}]))
check(not errs, '关联 objectTypeId 兼容 bare 写法（与 mg: 前缀等价）', errs)

errs = definition_errors(onto_state([dict(A2)], 'not-a-list'))
check(errs == ['对象动作关联必须是列表'], '关联非列表报格式错误', errs)

# --- 3. effective 关联推导（§5 内存推导历史对象关联） --------------------------------

eff = effective_action_associations(onto_state([dict(LEGACY)], [{'objectTypeId': 'mg:StorageDevice', 'actionId': 'act_stop'}]))
pairs = {(r['objectTypeId'], r['actionId']) for r in eff}
check(('mg:StorageDevice', 'act_stop') in pairs and ('mg:StorageDevice', 'action.change_system') in pairs,
      'effective 关联 = 显式集合 ∪ 历史动作 object_type 推导', eff)
check(len(eff) == len(pairs), 'effective 关联去重', eff)

# --- 4. 变更分类（§5 变更分类） ----------------------------------------------------

old = onto_state([dict(A2)], [{'objectTypeId': 'mg:StorageDevice', 'actionId': 'act_stop'}])
old_assoc = [{'objectTypeId': 'mg:StorageDevice', 'actionId': 'act_stop'}]
check(contracts.classify(old, onto_state([dict(A2, effect='修改后的效果')], old_assoc))['type'] == 'pending',
      '业务效果文本变化 → 待确认')
check(contracts.classify(old, onto_state([dict(A2)], []))['type'] == 'breaking', '移除对象动作关联 → 破坏性')
check(contracts.classify(old, onto_state([], []))['type'] == 'breaking', '删除动作定义 → 破坏性')
mixed = onto_state([dict(A2), dict(A2, id='act2', name='修改显示名称')], old_assoc + [
    {'objectTypeId': 'mg:StorageCluster', 'actionId': 'act2'}])
check(contracts.classify(old, mixed)['type'] == 'compatible', '新增动作/新增关联 → 兼容')
reasons = contracts.classify(old, onto_state([], []))['reasons']
check(any(r['area'] == 'actions' for r in reasons) and any(r['area'] == 'actionAssociations' for r in reasons),
      '分类理由区分动作与关联', reasons)
legacy_old = onto_state([dict(LEGACY)])
legacy_new = onto_state([dict(LEGACY, effect='效果文本调整')])
check(contracts.classify(legacy_old, legacy_new)['type'] == 'pending', '历史动作内容变化 → 待确认')

# --- 5. 项目 actionBindings 校验（验收 5/6） ---------------------------------------

ref = onto_state([dict(A2)], [{'objectTypeId': 'mg:StorageDevice', 'actionId': 'act_stop'}])
ok = validate_project(proj_state([
    {'id': 'r1', 'objectTypeId': 'StorageDevice', 'actionId': 'act_stop',
     'implementation': {'kind': 'api', 'path': '/api/devices/{id}/stop', 'roles': '运维', 'paramNotes': 'storageId ← 设备标识'}}]), copy.deepcopy(ref))
check(not ok['errors'], '合法 api 绑定通过校验', ok['errors'])
check(ok['items'] and ok['items'][0]['kind'] == 'actionBinding' and ok['items'][0]['status'] == 'valid',
      '合法绑定产生 valid 条目', ok['items'])

none = validate_project(proj_state([]), copy.deepcopy(ref))
check(not none['errors'] and not none['warnings'] and not none['items'],
      '未绑定动作不产生任何输出（不阻止发布）', none)

# 同一动作在两种对象上分别绑定不同接口，互不覆盖（验收 5）
ref2 = onto_state([dict(A2)], [
    {'objectTypeId': 'mg:StorageDevice', 'actionId': 'act_stop'},
    {'objectTypeId': 'mg:StorageCluster', 'actionId': 'act_stop'}])
two = validate_project(proj_state([
    {'id': 'r1', 'objectTypeId': 'StorageDevice', 'actionId': 'act_stop',
     'implementation': {'kind': 'api', 'path': '/device/stop'}},
    {'id': 'r2', 'objectTypeId': 'StorageCluster', 'actionId': 'act_stop',
     'implementation': {'kind': 'api', 'path': '/cluster/stop'}}]), copy.deepcopy(ref2))
check(not two['errors'] and len(two['items']) == 2, '同动作两对象分别绑定不同接口互不覆盖', two)

stale = validate_project(proj_state([
    {'id': 'r1', 'objectTypeId': 'StorageDevice', 'actionId': 'act_stop',
     'implementation': {'kind': 'api', 'path': '/stop'}}]), onto_state([dict(A2)], []))
check(any('关联已失效' in e for e in stale['errors']) and stale['items'][0]['status'] == 'invalid',
      '验收6 引用升级后失效绑定保留并报 error（阻止发布，不自动删除）', stale)

dup = validate_project(proj_state([
    {'id': 'r1', 'objectTypeId': 'StorageDevice', 'actionId': 'act_stop',
     'implementation': {'kind': 'api', 'path': '/a'}},
    {'id': 'r2', 'objectTypeId': 'StorageDevice', 'actionId': 'act_stop',
     'implementation': {'kind': 'api', 'path': '/b'}}]), copy.deepcopy(ref))
check(any('重复配置绑定' in e for e in dup['errors']), '同对象同动作重复绑定报错', dup)

flow_missing = validate_project(proj_state([
    {'id': 'r1', 'objectTypeId': 'StorageDevice', 'actionId': 'act_stop',
     'implementation': {'kind': 'flow', 'flowId': 'ghost-flow'}}]), copy.deepcopy(ref))
check(any('函数编排' in e for e in flow_missing['errors']), 'flow 模式引用不存在的编排报错', flow_missing)

no_path = validate_project(proj_state([
    {'id': 'r1', 'objectTypeId': 'StorageDevice', 'actionId': 'act_stop',
     'implementation': {'kind': 'api', 'path': ''}}]), copy.deepcopy(ref))
check(any('接口路径' in e for e in no_path['errors']), 'api 模式接口路径必填', no_path)

no_impl = validate_project(proj_state([
    {'id': 'r1', 'objectTypeId': 'StorageDevice', 'actionId': 'act_stop', 'implementation': {}}]), copy.deepcopy(ref))
check(any('实现方式未选择' in e for e in no_impl['errors']), '实现方式缺失报错', no_impl)

unknown = {'id': 'r3', 'objectTypeId': 'StorageDevice', 'actionId': 'act_stop',
           'implementation': {'kind': 'mystery', 'path': '/x', 'custom': 'keep-me'}, 'extra': 'v'}
unknown_copy = copy.deepcopy(unknown)
report = validate_project(proj_state([unknown]), copy.deepcopy(ref))
check(any('实现方式无效' in e for e in report['errors']), '未知实现 kind 报格式错误')
check(unknown == unknown_copy, '校验只读：未知字段零丢失不被重写')

legacy_ref = onto_state([dict(LEGACY)])
legacy_bind = validate_project(proj_state([
    {'id': 'r1', 'objectTypeId': 'StorageDevice', 'actionId': 'action.change_system',
     'implementation': {'kind': 'api', 'path': '/system/change'}}]), copy.deepcopy(legacy_ref))
check(not legacy_bind['errors'], '验收7 历史动作按 object_type 推导关联后可绑定', legacy_bind['errors'])

bad_list = proj_state([])
bad_list['bindings']['actionBindings'] = 'oops'
bad = validate_project(bad_list, copy.deepcopy(ref))
check(any('必须是列表' in e for e in bad['errors']), 'actionBindings 非列表报格式错误', bad['errors'])

# --- 6. 发布快照与状态往返（验收 6/7） ----------------------------------------------

from uuid import uuid4  # noqa: E402
ontology_id = str(uuid4())
workspaces_state = onto_state([dict(A2)], [{'objectTypeId': 'mg:StorageDevice', 'actionId': 'act_stop'}])
workspaces_state['workspaceId'] = ontology_id
workspaces_state['metrics'] = {'metrics': []}
workspaces_state['rules'] = {'rules': []}
entry = versions.publish(ontology_id, workspaces_state, {'changeType': 'initial'})
snapshot = versions.read_state(ontology_id, entry['version'])
check(snapshot['workflow'].get('actionAssociations') == [{'objectTypeId': 'mg:StorageDevice', 'actionId': 'act_stop'}]
      and snapshot['workflow']['actions'][0]['definitionVersion'] == 2,
      '发布快照原样携带动作与关联集合', snapshot['workflow'])

roundtrip = decode_state(encode_state(workspaces_state))
check(roundtrip['workflow'] == workspaces_state['workflow'], '本体状态 encode/decode 往返 workflow 零丢失')

# --- R05：effect 保留原键、有值保留、无值不报错；稳定 id 与关联不变 -------------------

# 无 effect 键的最小动作：保存→发布→读回，不凭空补键、不报错
minimal_id = str(uuid4())
minimal_state = onto_state([dict(minimal)], [{'objectTypeId': 'mg:StorageDevice', 'actionId': 'act_minimal'}])
minimal_state['workspaceId'] = minimal_id
minimal_state['metrics'] = {'metrics': []}
minimal_state['rules'] = {'rules': []}
workspaces.write_draft(minimal_state)
minimal_back = workspaces.read_draft(minimal_id)
check('effect' not in minimal_back['workflow']['actions'][0],
      'R05 无 effect 的动作保存回读不补键（空值不落盘、不报错）', minimal_back['workflow']['actions'])
check(minimal_back['workflow']['actions'][0]['id'] == 'act_minimal'
      and minimal_back['workflow']['actionAssociations'] == [{'objectTypeId': 'mg:StorageDevice', 'actionId': 'act_minimal'}],
      'R05 稳定 id 与对象动作关联不因字段精简改变', minimal_back['workflow'])
minimal_entry = versions.publish(minimal_id, minimal_back, {'changeType': 'initial'})
minimal_snapshot = versions.read_state(minimal_id, minimal_entry['version'])
check(minimal_snapshot['workflow']['actions'][0].get('id') == 'act_minimal'
      and 'effect' not in minimal_snapshot['workflow']['actions'][0],
      'R05 无 effect 的动作可发布，快照同样不补键', minimal_snapshot['workflow'])

# effect 有值：改名/改定义后保存，原键原值保留；发布快照同样保留
keep_id = str(uuid4())
keep_state = onto_state([dict(A2)], [{'objectTypeId': 'mg:StorageDevice', 'actionId': 'act_stop'}])
keep_state['workspaceId'] = keep_id
keep_state['metrics'] = {'metrics': []}
keep_state['rules'] = {'rules': []}
workspaces.write_draft(keep_state)
edited = copy.deepcopy(keep_state)
edited['workflow']['actions'][0].update({'name': '停止充放电（改名）', 'description': '业务定义微调'})
workspaces.write_draft(edited, expected_token=workspaces.current_token(keep_id))
keep_back = workspaces.read_draft(keep_id)
check(keep_back['workflow']['actions'][0].get('effect') == A2['effect'],
      'R05 改名/改定义保存后 effect 原键原值保留', keep_back['workflow']['actions'])
keep_entry = versions.publish(keep_id, keep_back, {'changeType': 'pending'})
check(versions.read_state(keep_id, keep_entry['version'])['workflow']['actions'][0].get('effect') == A2['effect'],
      'R05 发布快照保留 effect 原值')
check(is_action_v2(keep_back['workflow']['actions'][0]),
      'R05 保存不改变 definitionVersion（不批量提升动作版本）')

pstate = proj_state([{'id': 'r1', 'objectTypeId': 'StorageDevice', 'actionId': 'act_stop',
                      'implementation': {'kind': 'api', 'path': '/stop', 'roles': '运维', 'paramNotes': 'x'}}])
# 库化后：actionBindings 原样进入快照 bindings 并可读回（等价于旧 bindings.yaml 落盘检查）
projects.save_draft(pstate)
reloaded = projects.load('p1')[0]
check(reloaded['bindings'].get('actionBindings') == pstate['bindings']['actionBindings'],
      'actionBindings 随快照保存并读回原样')

published = projects.publish(pstate)
check(published.get('version') == 'v1', '项目发布成功')
releases = projects.published_versions('p1')
check(releases and releases[-1].get('version') == 'v1', '项目发布清单可读')

# --- A01（2026-09-20 验收修复）：动作 effect 非文本经正式发布路由必须 422 且零版本写入 --------
# 独立验收复现：effect 传数组经 post_publish 返回 200 并产出 1.0.0、快照保留非法值。
from workbench import model_routes  # noqa: E402
from workbench.model_format import encode_state as _encode  # noqa: E402


def publish_route(identifier, state_in):
    return model_routes.post_publish({'state': _encode(state_in),
                                      'revision': workspaces.current_token(identifier),
                                      'changeType': 'initial'})


def version_labels(identifier):
    return [e['version'] for e in versions.listing(identifier)]


act_created = workspaces.create('A01 动作本体', model_routes.blank_state('A01 动作本体'))
act_id = act_created['id']
act_state = onto_state([dict(A2)], [{'objectTypeId': 'mg:StorageDevice', 'actionId': 'act_stop'}])
act_state['ontology']['@context'] = copy.deepcopy(model_routes.blank_state()['ontology']['@context'])
act_state['workspaceId'] = act_id
act_state['metrics'] = {'metrics': []}
act_state['rules'] = {'rules': []}
workspaces.write_draft(copy.deepcopy(act_state))
resp, status = publish_route(act_id, act_state)
check(status == 200 and resp.get('version') == '1.0.0',
      'A01 对照：合法 v2 动作经正式发布路由 200 且产出 1.0.0', (status, resp))
labels_ok = version_labels(act_id)

for bad_value, title in ((['wrong'], '数组'), ({'wrong': 'object'}, '对象'), (3, '数值'), (True, '布尔值')):
    bad_state = copy.deepcopy(act_state)
    bad_state['workflow']['actions'][0]['effect'] = bad_value
    resp, status = publish_route(act_id, bad_state)
    check(status == 422 and any('必须为文本' in e for e in resp.get('errors', [])),
          f'A01 动作 effect 为{title} → 正式发布路由 422 且给「必须为文本」', (status, resp))
    check(version_labels(act_id) == labels_ok,
          f'A01 动作 effect 为{title} 拒绝发布后零新增版本', version_labels(act_id))

# 合法空值仍可发布（选填语义不变）：effect 缺失/空串/纯空白分别发布 → 均成功，不补键
for value in (None, '', '   '):
    ok_state = copy.deepcopy(act_state)
    if value is None:
        ok_state['workflow']['actions'][0].pop('effect', None)
    else:
        ok_state['workflow']['actions'][0]['effect'] = value
    resp, status = publish_route(act_id, ok_state)
    check(status == 200 and resp.get('version'),
          f'A01 动作 effect 为 {value!r} 时仍可发布（选填空值语义保持）', (status, resp))
    snapshot = versions.read_state(act_id, resp['version'])['workflow']['actions'][0]
    check(('effect' not in snapshot) if value is None else (snapshot.get('effect') == value),
          f'A01 effect {value!r} 发布快照不被清洗/补充', snapshot)

print(f'\n{len(PASSED)} 项断言全部通过')
shutil.rmtree(TMP, ignore_errors=True)
