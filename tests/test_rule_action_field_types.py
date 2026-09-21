"""A01 针对性回归（20260921）：业务规则 content 与 v2 动作 effect 选填字段的类型校验。

缺陷（基线 d6c73c2 验收复现）：content/effect 名义「选填文本」，实现零类型校验，
`content={'x':1}`、`effect=['a']` 经 validate(200 零错误)→save(200)→publish(200)
进入不可变发布快照。修复后（workbench/workflow.py）：

- 缺键 / null / 空串 / 空白串 → 按未填或文本放行（O3-03「选填」语义不变）；
- 数字 / 布尔 / 数组 / 对象 → 恰好一条错误，单条消息同时含定义名称/标识、字段名
  （content/effect）、「必须为文本」原因与修复建议，不经 str() 隐式转换；
- 校验在共享定义校验内：/api/validate、/api/save（200 + errors，草稿允许不完整）、
  /api/publish（errors 非空 → 422，版本零新增）经 model_routes.validate 共用同一实现
  （本文件不起服务，直接调 workflow.definition_errors 验证同一入口）；
- v1 动作 effect 必填逻辑不动；规则历史 output 键「只保留不校验」口径不动。

纯 python3 标准库直跑，无网络、无端口。
运行：python3 tests/test_rule_action_field_types.py
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
TMP = Path(tempfile.mkdtemp(prefix='wiz_field_types_'))
os.environ['WIZ_WORKBENCH_ROOT'] = str(TMP)  # 套件惯例：即使纯函数校验也不碰真实数据根

from workbench.workflow import definition_errors, is_action_v2  # noqa: E402

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

RULE = {'id': 'rule-a01', 'name': 'A01 示例规则', 'description': '识别运行异常。',
        'content': '先核验数据质量，再判断实际响应。'}
ACTION_V2 = {'id': 'act-a01', 'name': 'A01 示例动作', 'description': '请求目标对象停止充放电',
             'effect': '请求停止充放电，目标功率为 0', 'definitionVersion': 2, 'status': 'experimental'}
ACTION_V1 = {'id': 'action.legacy', 'name': '历史动作', 'description': '调整设备归属',
             'object_type': 'StorageDevice',
             'inputs': [{'name': 'device', 'type': 'object', 'required': True}],
             'effect': '结束原归属、建立新归属', 'criteria': '存在且不冲突', 'permission': '资产管理员',
             'acceptance': '设备A 转到系统二', 'status': 'active', 'implementation_ref': ''}


def onto_state(rules=None, actions=None, rule_assoc=None, action_assoc=None):
    return {'ontology': {'@context': {}, '@graph': copy.deepcopy(GRAPH)},
            'workflow': {'functions': [],
                         'actions': copy.deepcopy(actions or []),
                         'interfaces': [],
                         'businessRules': copy.deepcopy(rules or []),
                         'businessRuleAssociations': copy.deepcopy(rule_assoc or []),
                         'actionAssociations': copy.deepcopy(action_assoc or [])}}


LEGAL_STATE = onto_state([dict(RULE)], [dict(ACTION_V2)],
                         [{'objectTypeId': 'mg:StorageDevice', 'ruleId': 'rule-a01'}],
                         [{'objectTypeId': 'mg:StorageDevice', 'actionId': 'act-a01'}])

BAD_VALUES = (1, 3.14, True, ['先核验数据质量'], {'口径': 'x'})
LEGAL_VALUES = ('先核验数据质量，再判断异常。', '', '   ', None)  # 加上「缺键」共 5 种放行形态


# --- 1. 规则 content：放行形态（缺键/null/空串/空白串/合法文本） -----------------------

for value in LEGAL_VALUES:
    label = '缺键' if value is None else repr(value)
    rule = dict(RULE)
    if value is not None:
        rule['content'] = value
    errs = definition_errors(onto_state([rule], []))
    check(not errs, f'规则 content 为 {label} 时零错误（选填语义保留）', errs)
check(not definition_errors(onto_state([{k: v for k, v in RULE.items() if k != 'content'}], [])),
      '规则整键缺 content 时零错误（选填语义保留）')

# --- 2. 规则 content：拒绝形态（数字/布尔/数组/对象）且单条消息同时含字段定位与原因 -----

for bad in BAD_VALUES:
    errs = definition_errors(onto_state([dict(RULE, content=bad)], []))
    check(len(errs) == 1, f'规则 content 为 {type(bad).__name__} 时恰好一条错误', errs)
    message = errs[0]
    check('content' in message and '必须为文本' in message,
          f'规则 content 为 {type(bad).__name__}：单条消息同时含字段定位（content）与文本类型原因', errs)
    check('「A01 示例规则」(rule-a01)' in message and f'当前类型 {type(bad).__name__}' in message
          and '请改为文本或删除该字段' in message,
          f'规则 content 为 {type(bad).__name__}：单条消息含规则名/标识/当前类型/修复建议', errs)

# --- 3. v2 动作 effect：放行形态 -----------------------------------------------------

for value in LEGAL_VALUES:
    label = '缺键' if value is None else repr(value)
    action = dict(ACTION_V2)
    if value is not None:
        action['effect'] = value
    errs = definition_errors(onto_state([], [action]))
    check(not errs, f'v2 动作 effect 为 {label} 时零错误（选填语义保留）', errs)
check(not definition_errors(onto_state([], [{k: v for k, v in ACTION_V2.items() if k != 'effect'}])),
      'v2 动作整键缺 effect 时零错误（选填语义保留）')

# --- 4. v2 动作 effect：拒绝形态且单条消息同时含字段定位与原因 -------------------------

for bad in BAD_VALUES:
    errs = definition_errors(onto_state([], [dict(ACTION_V2, effect=bad)]))
    check(len(errs) == 1, f'v2 动作 effect 为 {type(bad).__name__} 时恰好一条错误', errs)
    message = errs[0]
    check('effect' in message and '必须为文本' in message,
          f'v2 动作 effect 为 {type(bad).__name__}：单条消息同时含字段定位（effect）与文本类型原因', errs)
    check('「A01 示例动作」(act-a01)' in message and f'当前类型 {type(bad).__name__}' in message
          and '请改为文本或删除该字段' in message,
          f'v2 动作 effect 为 {type(bad).__name__}：单条消息含动作名/标识/当前类型/修复建议', errs)

# --- 5. 并存不误伤：合法规则/动作与非法记录同处一状态，只有非法记录各报一条 ------------

mixed = onto_state(
    [dict(RULE), dict(RULE, id='rule-bad', name='非法内容规则', description='定义占位', content={'x': 1})],
    [dict(ACTION_V2), dict(ACTION_V2, id='act-bad', name='非法效果动作', effect=['a'])],
    [{'objectTypeId': 'mg:StorageDevice', 'ruleId': 'rule-a01'},
     {'objectTypeId': 'mg:StorageCluster', 'ruleId': 'rule-bad'}],
    [{'objectTypeId': 'mg:StorageDevice', 'actionId': 'act-a01'},
     {'objectTypeId': 'mg:StorageCluster', 'actionId': 'act-bad'}])
errs = definition_errors(mixed)
check(len(errs) == 2, '合法与非法记录并存时全状态恰好两条错误', errs)
rule_msg = next((e for e in errs if 'content' in e), '')
action_msg = next((e for e in errs if 'effect' in e), '')
check('rule-bad' in rule_msg and '非法内容规则' in rule_msg and '必须为文本' in rule_msg,
      '并存时 content 错误定位到非法规则本身', errs)
check('act-bad' in action_msg and '非法效果动作' in action_msg and '必须为文本' in action_msg,
      '并存时 effect 错误定位到非法动作本身', errs)
check(not any('rule-a01' in e or 'act-a01' in e for e in errs),
      '并存时合法规则/动作不被误伤（消息不含合法记录标识）', errs)

# --- 6. 边界不回归：v1 动作 effect 必填不动；规则历史 output 只保留不校验 ---------------

errs = definition_errors(onto_state([], [{k: v for k, v in ACTION_V1.items() if k != 'effect'}]))
check(any('动作定义未完整：effect' in e for e in errs),
      'v1 动作缺 effect 仍按旧必填逻辑报错（不被 A01 放行口径波及）', errs)
for bad in (1, {'a': 1}, ['x'], None):
    errs = definition_errors(onto_state([], [dict(ACTION_V1, effect=bad)]))
    check(any('动作定义未完整：effect' in e for e in errs),
          f'v1 动作 effect 为 {type(bad).__name__} 仍按旧必填逻辑报「未完整」（非 A01 新文案）', errs)

for bad in ({'历史': 1}, ['历史'], 1, None):
    rule = dict(RULE, output=bad)
    errs = definition_errors(onto_state([rule], []))
    check(not errs, f'规则历史 output 为 {type(bad).__name__} 仍零错误（只保留不校验口径不变）', errs)

check(is_action_v2(ACTION_V2) and not is_action_v2(ACTION_V1), 'definitionVersion 新旧判定不受影响')

print(f'\n{len(PASSED)} 项断言全部通过')
shutil.rmtree(TMP, ignore_errors=True)
