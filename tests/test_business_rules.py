"""业务规则管理一期后端回归（20260917）。

覆盖：workflow.businessRules / businessRuleAssociations 容错读取（老数据按空数组）、
四字段必填校验、标识唯一、引用存在性与组合唯一（删除保护）、contracts.classify
（新增=兼容、删除=破坏性、正文/定义/输出变化=待确认、仅名称变化=兼容）、
草稿持久化与发布快照往返、既有 workflow 字段零丢失。

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

# --- 四字段校验 ------------------------------------------------------------------

check(not definition_errors(onto_state()), '四字段齐全的规则与合法关联通过校验')
for key, title in (('name', '名称'), ('description', '业务定义'), ('content', '规则内容'), ('output', '输出结果')):
    bad = dict(RULE, **{key: '  '})
    errs = definition_errors(onto_state([bad], []))
    check(any(f'缺少{title}' in e for e in errs), f'缺 {title} 报错（发布禁止不完整规则）', errs)

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

print(f'\n{len(PASSED)} 项断言全部通过')
shutil.rmtree(TMP, ignore_errors=True)
