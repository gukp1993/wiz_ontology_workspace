"""业务规则管理一期后端回归（20260917，20260920 字段精简后更新）。

覆盖：workflow.businessRules / businessRuleAssociations 容错读取（老数据按空数组）、
必填校验（20260920：名称/业务定义必填；规则内容与历史 output 选填、空值不阻断）、
标识唯一、引用存在性与组合唯一（删除保护）、contracts.classify
（新增=兼容、删除=破坏性、正文/定义/输出变化=待确认、仅名称变化=兼容）、
草稿持久化与发布快照往返（R04：旧 output 保存发布后原值保留）、既有 workflow 字段零丢失。

纯 python3 标准库直跑；WIZ_WORKBENCH_ROOT 挂临时数据根，真实 ontology/ 只读不碰。
运行：python3 tests/test_business_rules.py
"""
import copy
import json
import os
import shutil
import sys
import tempfile
from uuid import uuid4

REPO = __import__('pathlib').Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
TMP = __import__('pathlib').Path(tempfile.mkdtemp(prefix='wiz_bizrules_'))
os.environ['WIZ_WORKBENCH_ROOT'] = str(TMP)

from workbench import contracts, versions, workspaces  # noqa: E402  （临时根就位后再 import）
from workbench.model_format import decode_state, encode_state  # noqa: E402
from workbench.workflow import (business_rule_associations, business_rules,  # noqa: E402
definition_errors)

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
]

RULE = {'id': 'rule-1', 'name': '储能运行异常判定', 'description': '识别无法由正常业务原因解释的运行异常。',
        'content': '先核验数据质量和有效边界，再判断实际响应。\n偏差超过阈值提示异常。',
        'output': '判定结果、异常类型、依据与原因。'}


def onto_state(rules=None, assoc=None, extra_workflow=None):
    w = {'actions': [], 'interfaces': [], 'functions': [],
         'businessRules': copy.deepcopy(rules if rules is not None else [dict(RULE)]),
         'businessRuleAssociations': copy.deepcopy(assoc if assoc is not None else [
             {'objectTypeId': 'mg:StorageDevice', 'ruleId': 'rule-1'}])}
    if extra_workflow:
        w.update(copy.deepcopy(extra_workflow))
    return {'ontology': {'@context': {}, '@graph': copy.deepcopy(GRAPH)}, 'workflow': w}


# --- A11 / 协议：老数据缺新键按空读取，其他字段无损 --------------------------------

legacy = {'ontology': {'@context': {}, '@graph': copy.deepcopy(GRAPH)},
          'workflow': {'actions': [{'id': 'a1', 'name': '旧动作', 'status': 'active'}], 'custom': 'keep-me'}}
check(business_rules(legacy) == [], 'A11 老 workflow 缺 businessRules 按空数组读取')
check(business_rule_associations(legacy) == [], 'A11 老 workflow 缺 businessRuleAssociations 按空数组读取')
legacy_errs = definition_errors(legacy)
check(any('旧动作 缺少业务描述' in e for e in legacy_errs) and not any('业务规则' in e or '对象规则' in e for e in legacy_errs),
      'A11 老数据既有校验不受影响，且不产生规则类错误', legacy_errs)

# --- 必填字段校验（20260920 字段精简：名称/业务定义必填，规则内容与历史 output 选填） -----

check(not definition_errors(onto_state()), '名称/业务定义齐全的规则与合法关联通过校验')
for key, title in (('name', '名称'), ('description', '业务定义')):
    bad = dict(RULE, **{key: '  '})
    errs = definition_errors(onto_state([bad], []))
    check(any(f'缺少{title}' in e for e in errs), f'缺 {title} 报错（R02：必填项仍在发布前拦截）', errs)

# R01/R02：规则内容（content）与历史 output 为空/空白/null/整键缺失都不阻断校验
for key, title in (('content', '规则内容'), ('output', '输出结果')):
    for value in ('', '   ', None):
        errs = definition_errors(onto_state([dict(RULE, **{key: value})], []))
        check(not errs, f'{title} 为 {value!r} 时不产生校验错误（选填）', errs)
    missing = {k: v for k, v in RULE.items() if k != key}
    check(not definition_errors(onto_state([missing], [])), f'{title} 整个键缺失也不阻断校验', definition_errors(onto_state([missing], [])))
check(not definition_errors(onto_state([{'id': 'rule-min', 'name': '最小规则', 'description': '只填必填两项'}], [])),
      'R01 只填名称+业务定义的新规则即可保存/发布（content/output 均无键）')

# A01（20260921）：content 选填但提供时必须为文本——数字/布尔/数组/对象逐类型拒绝，单条消息同时含
# 规则名称、标识、字段名（content）、「必须为文本」原因与修复建议；不经 str() 隐式转成合法说明
for bad in (1, 3.14, True, ['先核验数据质量'], {'口径': 'x'}):
    errs = definition_errors(onto_state([dict(RULE, content=bad)], []))
    matched = [e for e in errs if '必须为文本' in e and 'content' in e]
    check(len(matched) == 1
          and '「储能运行异常判定」(rule-1)' in matched[0]
          and f'当前类型 {type(bad).__name__}' in matched[0]
          and '请改为文本或删除该字段' in matched[0],
          f'A01 content 为 {type(bad).__name__} 报恰好一条错误（单条消息含规则名/标识/字段名/类型原因/建议）', errs)

# A01 不误伤：同一状态里合法规则与非法 content 记录并存，只有非法记录报错
mixed_rules = [dict(RULE), dict(RULE, id='rule-bad', name='非法内容规则', description='定义占位', content={'x': 1})]
errs = definition_errors(onto_state(mixed_rules, []))
check(len(errs) == 1 and 'rule-bad' in errs[0] and '非法内容规则' in errs[0]
      and 'content' in errs[0] and '必须为文本' in errs[0],
      'A01 合法规则与非法 content 规则并存：只有非法记录报单条错误（不误伤）', errs)

dup = dict(RULE, name='另一条')
errs = definition_errors(onto_state([dict(RULE), dup], []))
check(any('重复标识' in e for e in errs), '规则标识重复报错', errs)

# --- 引用校验（A13 / 删除保护） ----------------------------------------------------

errs = definition_errors(onto_state([dict(RULE)], [{'objectTypeId': 'mg:Ghost', 'ruleId': 'rule-1'}]))
check(any('不存在的对象类型' in e for e in errs), '引用悬空对象类型报错', errs)

errs = definition_errors(onto_state([dict(RULE)], [{'objectTypeId': 'StorageDevice', 'ruleId': 'rule-1'}]))
check(not errs, '关联 objectTypeId 兼容 bare 写法', errs)

errs = definition_errors(onto_state([dict(RULE)], [{'objectTypeId': 'mg:StorageDevice', 'ruleId': 'ghost'}]))
check(any('引用不存在的规则' in e for e in errs), '引用不存在的规则报错（删除仍被引用的规则被拦截）', errs)

errs = definition_errors(onto_state([dict(RULE)], [
    {'objectTypeId': 'mg:StorageDevice', 'ruleId': 'rule-1'},
    {'objectTypeId': 'mg:StorageDevice', 'ruleId': 'rule-1'},
    {'objectTypeId': 'mg:StorageCluster', 'ruleId': 'rule-1'}]))
check(any('引用重复' in e for e in errs) and not any('StorageCluster' in e for e in errs),
      '同对象同规则重复关联报错；不同对象引用同一规则合法', errs)

errs = definition_errors(onto_state([dict(RULE)], 'not-a-list'))
check(any('必须是列表' in e for e in errs), '关联非列表报格式错误', errs)

# --- 变更分类（需求 §6） ----------------------------------------------------------

old = onto_state()
old_assoc = [{'objectTypeId': 'mg:StorageDevice', 'ruleId': 'rule-1'}]
check(contracts.classify(old, onto_state([dict(RULE), dict(RULE, id='rule-2', name='新规则')], old_assoc))['type'] == 'compatible',
      '新增规则 → 兼容')
check(contracts.classify(old, onto_state([dict(RULE)], old_assoc + [{'objectTypeId': 'mg:StorageCluster', 'ruleId': 'rule-1'}]))['type'] == 'compatible',
      '新增对象规则引用 → 兼容')
check(contracts.classify(old, onto_state([], []))['type'] == 'breaking', '删除已发布规则 → 破坏性')
check(contracts.classify(old, onto_state([dict(RULE)], []))['type'] == 'breaking', '移除对象规则引用 → 破坏性')
check(contracts.classify(old, onto_state([dict(RULE, content='口径调整后的正文')], old_assoc))['type'] == 'pending',
      '规则内容变化 → 待确认')
check(contracts.classify(old, onto_state([dict(RULE, description='定义微调')], old_assoc))['type'] == 'pending',
      '业务定义变化 → 待确认')
check(contracts.classify(old, onto_state([dict(RULE, output='输出描述调整')], old_assoc))['type'] == 'pending',
      '输出结果变化 → 待确认')
check(contracts.classify(old, onto_state([dict(RULE, name='改名后的规则')], old_assoc))['type'] == 'compatible',
      'A10 仅名称变化 → 兼容')

# --- 持久化与发布快照（A3/A12） ----------------------------------------------------

ontology_id = str(uuid4())
state = onto_state(extra_workflow={'persisted': 'marker'})
state['workspaceId'] = ontology_id
state['metrics'] = {'metrics': []}
state['rules'] = {'rules': []}
workspaces.write_draft(state)
reloaded = workspaces.read_draft(ontology_id)
check(reloaded['workflow']['businessRules'] == state['workflow']['businessRules']
      and reloaded['workflow']['businessRuleAssociations'] == state['workflow']['businessRuleAssociations']
      and reloaded['workflow'].get('persisted') == 'marker',
      'A11/A3 草稿保存回读：规则、引用与未知 workflow 字段无损')

entry = versions.publish(ontology_id, state, {'changeType': 'initial'})
snapshot = versions.read_state(ontology_id, entry['version'])
check(snapshot['workflow']['businessRules'] == [dict(RULE)]
      and snapshot['workflow']['businessRuleAssociations'] == [{'objectTypeId': 'mg:StorageDevice', 'ruleId': 'rule-1'}],
      'A12 发布快照携带规则与引用', snapshot['workflow'])
older = versions.read_state(ontology_id, entry['version'])
versions.publish(ontology_id, onto_state([], []), {'changeType': 'breaking'})
again = versions.read_state(ontology_id, versions.latest(ontology_id)['version'])
check(older['workflow']['businessRules'] == [dict(RULE)] and again['workflow']['businessRules'] == [],
      'A12 历史快照不可变；新版本按新内容读取')

roundtrip = decode_state(encode_state(onto_state()))
check(roundtrip['workflow'] == onto_state()['workflow'], 'encode/decode 状态往返 workflow 零丢失')

# --- R04：旧 output 零丢失（改名/保存/发布/配置导出导入后原值保留；只读历史区可见） --------

legacy_rule = dict(RULE)                       # 含非空 output 的历史规则
keep_id = str(uuid4())
keep_state = onto_state([legacy_rule], [{'objectTypeId': 'mg:StorageDevice', 'ruleId': 'rule-1'}])
keep_state['workspaceId'] = keep_id
keep_state['metrics'] = {'metrics': []}
keep_state['rules'] = {'rules': []}
workspaces.write_draft(keep_state)

# 前端表单只提交三字段（RULE_FIELDS），历史 output 由 Object.assign 之外的键原地保留：
# 这里按同一语义构造「改名 + 改业务定义后保存」的下一状态。
edited = copy.deepcopy(keep_state)
edited['workflow']['businessRules'][0].update({'name': '改名后的规则', 'description': '定义微调'})
workspaces.write_draft(edited, expected_token=workspaces.current_token(keep_id))
keep_back = workspaces.read_draft(keep_id)
check([r.get('output') for r in keep_back['workflow']['businessRules']] == [legacy_rule['output']],
      'R04 改名/改定义保存后旧 output 原值保留（未传该键时不清空）', keep_back['workflow']['businessRules'])
check([r.get('content') for r in keep_back['workflow']['businessRules']] == [legacy_rule['content']],
      'R04 保存同时保留历史规则内容', keep_back['workflow']['businessRules'])

keep_entry = versions.publish(keep_id, keep_back, {'changeType': 'pending'})
keep_snapshot = versions.read_state(keep_id, keep_entry['version'])
check(keep_snapshot['workflow']['businessRules'][0].get('output') == legacy_rule['output'],
      'R04 发布快照保留旧 output', keep_snapshot['workflow']['businessRules'])

# 配置导出/导入走的编码链路（encode_state 本体形态 + 事件流）不得改写 workflow 里的 output
exported = encode_state(keep_back)
check(json.dumps(exported['workflow'], sort_keys=True, ensure_ascii=False)
      == json.dumps(keep_back['workflow'], sort_keys=True, ensure_ascii=False),
      'R04 encode_state（导出/入库形态）不改写 workflow 历史字段')
check(decode_state(exported)['workflow']['businessRules'][0].get('output') == legacy_rule['output'],
      'R04 导出后再解码，旧 output 仍在', decode_state(exported)['workflow']['businessRules'])

# 只读历史区可见性判据：非空才展示（前端 legacyOutput/NodeEditModal 同口径）；空白不显示
check(all(str(r.get('output') or '').strip() for r in keep_back['workflow']['businessRules']),
      'R04 历史区展示判据：有非空 output 才显示历史补充说明')
blank_output = dict(RULE, output='   ')
check(not definition_errors(onto_state([blank_output], [])) and not str(blank_output['output']).strip(),
      'R04 空白 output 不阻断校验（历史区按空白不显示）')

# --- A01（2026-09-20 验收修复）：字段类型边界 + 正式发布路由拒绝且零版本写入 ---------------
# 独立复现：规则 content 传对象、动作 effect 传数组，此前经正式 post_publish 返回 200 并出版本。
# （20260921 合并口径：content 文案统一为富格式「必须为文本」，以下断言随之同步；name 仍走必填助手「必须是文本」）
check(any('必须为文本' in e for e in definition_errors(onto_state([dict(RULE, content={'wrong': 'object'})], []))),
      'A01 规则 content 为对象 → 校验报「必须为文本」（不 str 掩盖）',
      definition_errors(onto_state([dict(RULE, content={'wrong': 'object'})], [])))
check(any('必须为文本' in e for e in definition_errors(onto_state([dict(RULE, content=['wrong'])], []))),
      'A01 规则 content 为数组 → 校验报「必须为文本」')
check(any('必须是文本' in e for e in definition_errors(onto_state([dict(RULE, name={'bad': 1})], []))),
      'A01 规则 name 为对象 → 校验报「必须是文本」')
null_desc = definition_errors(onto_state([dict(RULE, description=None)], []))
check(any('规则' in e and '缺少业务定义' in e for e in null_desc),
      'A01 规则 description=null → 定位到规则与字段的可读报错（不再 AttributeError 泛化）', null_desc)
check(not any('配置不完整或不受当前执行器支持' in e for e in null_desc),
      'A01 规则 description=null 不落外层泛化错误', null_desc)

from workbench import model_routes  # noqa: E402
from workbench.model_format import encode_state as _encode  # noqa: E402


def publish_route(identifier, state_in):
    """走正式 post_publish 路由函数（同 HTTP 处理器调用路径）。"""
    payload = {'state': _encode(state_in), 'revision': workspaces.current_token(identifier),
               'changeType': 'initial'}
    return model_routes.post_publish(payload)


def version_labels(identifier):
    return [e['version'] for e in versions.listing(identifier)]


a01_created = workspaces.create('A01 字段类型本体', model_routes.blank_state('A01 字段类型本体'))
a01_id = a01_created['id']
a01_state = onto_state([dict(RULE)], [{'objectTypeId': 'mg:StorageDevice', 'ruleId': 'rule-1'}])
# post_publish 要求命名空间上下文为默认值（防远程 context 注入），故用 blank_state 的 context
a01_state['ontology']['@context'] = copy.deepcopy(model_routes.blank_state()['ontology']['@context'])
a01_state['workspaceId'] = a01_id
a01_state['metrics'] = {'metrics': []}
a01_state['rules'] = {'rules': []}
workspaces.write_draft(copy.deepcopy(a01_state))
resp, status = publish_route(a01_id, a01_state)
check(status == 200 and resp.get('version') == '1.0.0',
      'A01 对照：合法规则经正式发布路由 200 且产出 1.0.0', (status, resp))
labels_after_ok = version_labels(a01_id)
check(labels_after_ok == ['1.0.0'], 'A01 对照版本列表为 1.0.0', labels_after_ok)

for bad_value, title in (({'wrong': 'object'}, '对象'), (['wrong'], '数组'), (3, '数值'), (True, '布尔值')):
    bad_state = copy.deepcopy(a01_state)
    bad_state['workflow']['businessRules'][0]['content'] = bad_value
    resp, status = publish_route(a01_id, bad_state)
    check(status == 422 and any('必须为文本' in e for e in resp.get('errors', [])),
          f'A01 规则 content 为{title} → 正式发布路由 422 且给「必须为文本」', (status, resp))
    check(version_labels(a01_id) == labels_after_ok,
          f'A01 规则 content 为{title} 拒绝发布后零新增版本', version_labels(a01_id))

# description=null：路由返回可读错误（不是泛化 500/异常）
null_state = copy.deepcopy(a01_state)
null_state['workflow']['businessRules'][0]['description'] = None
resp, status = publish_route(a01_id, null_state)
joined = '；'.join(resp.get('errors', [])) if isinstance(resp, dict) else str(resp)
check(status == 422 and '缺少业务定义' in joined and 'NoneType' not in joined,
      'A01 规则 description=null 正式路由 422 且文案定位字段（不暴露 NoneType）', (status, resp))
check(version_labels(a01_id) == labels_after_ok, 'A01 description=null 拒绝后零新增版本')

print(f'\n{len(PASSED)} 项断言全部通过')
shutil.rmtree(TMP, ignore_errors=True)
