"""动作库与对象动作关联后端回归（20260917 需求）。

覆盖：v2 动作三字段校验与旧校验隔离、历史动作零丢失、actionAssociations 校验
（去重/悬空引用/删除保护）、effective 关联推导（显式 ∪ 历史动作 object_type）、
contracts.classify 变更分类（文本变化=pending、删除动作/关联=breaking、新增=compatible）、
项目 actionBindings 校验（组合隔离、失效阻塞、未绑定不阻塞、编排存在性、未知结构零丢失）、
发布快照携带关联与绑定、状态往返不丢字段。

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

from workbench import contracts, projects, versions  # noqa: E402  （临时根就位后再 import）
from workbench.model_format import decode_state, encode_state  # noqa: E402
from workbench.project_validation import validate_project  # noqa: E402
from workbench.workflow import (definition_errors, effective_action_associations,  # noqa: E402
                                is_action_v2)

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


# --- 1. v2 动作校验（验收 1/7） ---------------------------------------------------

errs = definition_errors(onto_state([dict(A2)]))
check(not errs, '验收1 v2 动作仅三字段即可通过校验，不再要求对象/参数/条件/权限/验收', errs)

bad = dict(A2, effect=' ')
check(definition_errors(onto_state([bad])) == ['停止充放电 缺少业务效果'], 'v2 缺业务效果单独报错')

for field, keep in [('criteria', None), ('permission', None)]:
    record = {k: v for k, v in LEGACY.items() if k != field}
    errs = definition_errors(onto_state([dict(record)]))
    check(any(field in e for e in errs), f'验收7 历史动作缺 {field} 仍按旧校验报错', errs)

errs = definition_errors(onto_state([dict(LEGACY)]))
check(any('参数引用不存在的对象类型' in e for e in errs) and not any('动作定义未完整' in e for e in errs),
      '完整历史动作走旧校验路径（输入参数仍被检查；四字段齐全不再报未完整）', errs)
check(is_action_v2(A2) and not is_action_v2(LEGACY), 'definitionVersion 区分新旧格式')

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

print(f'\n{len(PASSED)} 项断言全部通过')
shutil.rmtree(TMP, ignore_errors=True)
