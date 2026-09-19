"""隔离片段测试（flow-run testMode=isolated）回归：范围计划 / 覆盖合法性 / 取值预检 /
真实范围执行 / 传递跳过 / 旧调用兼容（20260919_函数编排配置与调试优化）。

确定性样例（需求 §12）：五节点标量公式链 A→B→C→D→E，
A={x}、B={x}+2、C={x}*3、D={x}/2、E={x}*100；B.x=10 时片段结果 12/36/18，
A/E 真实执行计数为 0。纯 python3 直跑，不连接任何真实服务。
运行：python3 tests/test_flow_test_plan.py
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
TMP = Path(tempfile.mkdtemp(prefix='wiz_ftest_'))
os.environ['WIZ_WORKBENCH_ROOT'] = str(TMP)

from workbench import flow_routes, flow_test_plan  # noqa: E402
from workbench.flow_test_plan import PlanError  # noqa: E402

PASSED = []
EXEC_COUNTS = {}


def check(cond, message, actual=None):
    if cond:
        PASSED.append(message)
        print(f'通过) {message}')
        return
    print(f'[失败] {message}')
    if actual is not None:
        print('  实际:', json.dumps(actual, ensure_ascii=False, default=str)[:2000])
    shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(1)


def expect_plan_error(status, message, fn):
    try:
        fn()
    except PlanError as exc:
        return exc.status == status and message in exc.message, f'status={exc.status} msg={exc.message}'
    except Exception as exc:  # noqa: BLE001
        return False, f'意外异常 {type(exc).__name__}: {exc}'
    return False, '未抛出 PlanError'


# ── 确定性五节点公式链（需求 §12） ─────────────────────────────────────────────
def calc_node(nid, formula, source):
    return {'id': nid, 'kind': 'calc', 'name': f'节点{nid}',
            'inputs': [{'id': 'in_x', 'name': 'x', 'label': 'X', 'type': {'type': 'number'}, 'source': source}],
            'outputs': [{'id': 'out_v', 'name': 'v', 'label': 'V', 'type': {'type': 'number'}}],
            'implementation': {'language': 'calc', 'mode': 'formula', 'formulas': {'v': formula}}}


def chain_state(extra_d_input=None):
    """A={x}(入口) → B={x}+2 → C={x}*3 → D={x}/2 → E={x}*100；extra_d_input 给 D 追加侧路输入。"""
    d = calc_node('D', '{x} / 2', {'kind': 'node', 'nodeId': 'C', 'outputId': 'out_v'})
    if extra_d_input:
        d = dict(d)
        d['inputs'] = d['inputs'] + [dict(extra_d_input, id='in_y')]
    return {'schemaVersion': 1, 'flowId': 'f1', 'name': '公式链', 'description': '',
            'inputs': [{'id': 'inp_x', 'name': 'x', 'label': 'X', 'type': {'type': 'number'}}],
            'outputs': [], 'connections': [],
            'nodes': [
                calc_node('A', '{x}', {'kind': 'flowInput', 'inputId': 'inp_x'}),
                calc_node('B', '{x} + 2', {'kind': 'node', 'nodeId': 'A', 'outputId': 'out_v'}),
                calc_node('C', '{x} * 3', {'kind': 'node', 'nodeId': 'B', 'outputId': 'out_v'}),
                d,
                calc_node('E', '{x} * 100', {'kind': 'node', 'nodeId': 'D', 'outputId': 'out_v'}),
            ]}


STATE = chain_state()

# 1) 范围计划：B–D 片段 ────────────────────────────────────────────────────────
plan = flow_test_plan.plan_scope(STATE, ['B', 'C', 'D'], {})
check(plan['orderedTargets'] == ['B', 'C', 'D'], 'B–D 计划顺序为依赖拓扑序 B→C→D', plan)
check(plan['excludedIds'] == ['A', 'E'], 'A/E 属于范围外不执行', plan['excludedIds'])
ext = {(e['nodeId'], e['inputId']) for e in plan['externalInputs']}
check(('B', 'in_x') in ext and len(ext) == 1, '仅 B.x 是必须手工提供的外部输入', plan['externalInputs'])
check(all(e['required'] for e in plan['externalInputs']), '外部输入标记为必须提供')

# 2) 范围计划：结构错误（400 层） ─────────────────────────────────────────────
ok, got = expect_plan_error(400, '重复', lambda: flow_test_plan.plan_scope(STATE, ['B', 'B'], {}))
check(ok, '重复节点 ID 拒绝', got)
ok, got = expect_plan_error(400, '不存在', lambda: flow_test_plan.plan_scope(STATE, ['B', 'Z'], {}))
check(ok, '不存在节点 ID 拒绝', got)
ok, got = expect_plan_error(400, '非空', lambda: flow_test_plan.plan_scope(STATE, [], {}))
check(ok, '空 targets 拒绝（绝不解释为全图）', got)
ok, got = expect_plan_error(400, '不连通', lambda: flow_test_plan.plan_scope(STATE, ['B', 'D'], {}))
check(ok, 'B+D 缺 C：集合不连通拒绝（不猜测中间节点）', got)
ok, got = expect_plan_error(400, '不连通', lambda: flow_test_plan.plan_scope(STATE, ['D', 'B'], {}))
check(ok, '逆序选择同样按连通性拒绝', got)
cyclic = {'schemaVersion': 1, 'flowId': 'f2', 'inputs': [], 'outputs': [], 'connections': [],
          'nodes': [calc_node('A', '{x}', {'kind': 'node', 'nodeId': 'B', 'outputId': 'out_v'}),
                    calc_node('B', '{x}', {'kind': 'node', 'nodeId': 'A', 'outputId': 'out_v'})]}
ok, got = expect_plan_error(400, '循环依赖', lambda: flow_test_plan.plan_scope(cyclic, ['A', 'B'], {}))
check(ok, '被测集合成环拒绝', got)

# 侧路范围外输入：D 另有来自范围外 X 的输入 → B.x 与 D.y 都必须提供
side = {'id': 'in_x', 'name': 'y', 'label': 'Y', 'type': {'type': 'text'},
        'source': {'kind': 'node', 'nodeId': 'X', 'outputId': 'out_o'}}
plan_side = flow_test_plan.plan_scope(chain_state(side), ['B', 'C', 'D'], {})
side_keys = {(e['nodeId'], e['inputId']) for e in plan_side['externalInputs']}
check(side_keys == {('B', 'in_x'), ('D', 'in_y')}, '侧路范围外输入按目标节点+输入分别列出', plan_side['externalInputs'])

# 3) 覆盖合法性（400 层） ─────────────────────────────────────────────────────
ok, got = expect_plan_error(400, '不允许测试值覆盖',
                            lambda: flow_test_plan.plan_scope(STATE, ['B', 'C', 'D'], {'C': {'in_x': 1}}))
check(ok, '范围内内部依赖（C.x←B）不允许覆盖', got)
fixed_state = {'schemaVersion': 1, 'flowId': 'f3', 'inputs': [], 'outputs': [], 'connections': [],
               'nodes': [{'id': 'F', 'kind': 'calc', 'name': 'F',
                          'inputs': [{'id': 'in_f', 'name': 'f', 'type': {'type': 'number'},
                                      'source': {'kind': 'fixed', 'valueType': 'number', 'value': 1}}],
                          'outputs': [{'id': 'out_v', 'name': 'v', 'type': {'type': 'number'}}],
                          'implementation': {'mode': 'formula', 'formulas': {'v': '{f} + 1'}}}]}
ok, got = expect_plan_error(400, '固定来源', lambda: flow_test_plan.plan_scope(fixed_state, ['F'], {'F': {'in_f': 9}}))
check(ok, '固定来源不允许覆盖', got)
ok, got = expect_plan_error(400, '歧义',
                            lambda: flow_test_plan.plan_scope(STATE, ['B', 'C', 'D'], {'B': {'in_x': 1}}) if False
                            else flow_test_plan.plan_scope(STATE, ['A'], {'A': {'in_x': 1}}))
check(ok, '入口参数来源输入不允许 overrides（须走入口值，同用即歧义）', got)
ok, got = expect_plan_error(400, '范围外节点', lambda: flow_test_plan.plan_scope(STATE, ['B', 'C', 'D'], {'A': {'in_x': 1}}))
check(ok, 'overrides 引用范围外节点拒绝', got)
ok, got = expect_plan_error(400, '无效覆盖项', lambda: flow_test_plan.plan_scope(STATE, ['B', 'C', 'D'], {'B': {'in_nope': 1}}))
check(ok, '未知输入覆盖项拒绝', got)

# 4) 取值预检（422 层，先于任何执行） ─────────────────────────────────────────
plan_bcd = flow_test_plan.plan_scope(STATE, ['B', 'C', 'D'], {})
entry_values, resolved = flow_test_plan.precheck_values(STATE, plan_bcd, {'x': 10}, {'B': {'in_x': 10}})
check(resolved['B']['in_x'] == 10 and entry_values.get('x') == 10, '边界值 10 与入口值 10 预检通过', resolved)
entry_values, resolved = flow_test_plan.precheck_values(STATE, plan_bcd, {'x': 0}, {'B': {'in_x': 0}})
check(resolved['B']['in_x'] == 0, '显式 0 有效不丢失', resolved)
bool_state = {'schemaVersion': 1, 'flowId': 'f4', 'inputs': [], 'outputs': [], 'connections': [],
              'nodes': [{'id': 'G', 'kind': 'calc', 'name': 'G',
                         'inputs': [{'id': 'in_b', 'name': 'b', 'label': 'B', 'type': {'type': 'boolean'}}],
                         'outputs': [{'id': 'out_v', 'name': 'v', 'type': {'type': 'boolean'}}],
                         'implementation': {'mode': 'formula', 'formulas': {'v': '{b}'}}}]}
plan_g = flow_test_plan.plan_scope(bool_state, ['G'], {'G': {'in_b': False}})
_, resolved = flow_test_plan.precheck_values(bool_state, plan_g, {}, {'G': {'in_b': False}})
check(resolved['G']['in_b'] is False, '显式 False 有效不丢失', resolved)
ok, got = expect_plan_error(422, '请提供本次测试值',
                            lambda: flow_test_plan.precheck_values(STATE, plan_bcd, {'x': 10}, {}))
check(ok, '缺边界值 422（预检阶段，零节点执行）', got)
ok, got = expect_plan_error(422, '需要数值',
                            lambda: flow_test_plan.precheck_values(STATE, plan_bcd, {'x': 10}, {'B': {'in_x': 'abc'}}))
check(ok, '边界值类型不符 422', got)
plan_a = flow_test_plan.plan_scope(STATE, ['A'], {})
check(len(plan_a['entryRequirements']) == 1 and plan_a['entryRequirements'][0]['entryId'] == 'inp_x',
      '单节点 A 的入口参数需求被识别', plan_a.get('entryRequirements'))
ok, got = expect_plan_error(422, '请提供该入口值',
                            lambda: flow_test_plan.precheck_values(STATE, plan_a, {}, {}))
check(ok, '被测节点引用的入口参数缺失 422', got)
# B–D 片段不含 A：不因范围外 A 的入口绑定要求入口值（正常通过）
_, resolved_bcd = flow_test_plan.precheck_values(STATE, plan_bcd, {}, {'B': {'in_x': 10}})
check(resolved_bcd['B']['in_x'] == 10, '范围外 A 的入口绑定不进入 B–D 片段预检要求', resolved_bcd)
ok, got = expect_plan_error(422, '不存在的入口参数',
                            lambda: flow_test_plan.precheck_values(STATE, plan_bcd, {'nope': 1}, {'B': {'in_x': 10}}))
check(ok, '未知入口参数拒绝', got)
obj_state = {'schemaVersion': 1, 'flowId': 'f5', 'inputs': [], 'outputs': [], 'connections': [],
             'nodes': [{'id': 'O', 'kind': 'calc', 'name': 'O',
                        'inputs': [{'id': 'in_o', 'name': 'o', 'label': 'O', 'type': {
                            'type': 'object', 'fields': [
                                {'id': 'fld_a', 'name': 'a', 'label': 'A', 'type': {'type': 'number'}},
                                {'id': 'fld_b', 'name': 'b', 'label': 'B', 'type': {'type': 'text'}}]}}],
                        'outputs': [{'id': 'out_v', 'name': 'v', 'type': {'type': 'number'}}],
                        'implementation': {'mode': 'formula', 'formulas': {'v': '{o}.a'}}}]}
plan_o = flow_test_plan.plan_scope(obj_state, ['O'], {'O': {'in_o': {'a': 1, 'b': 'x'}}})
_, resolved = flow_test_plan.precheck_values(obj_state, plan_o, {}, {'O': {'in_o': {'a': 1, 'b': 'x'}}})
check(resolved['O']['in_o'] == {'a': 1, 'b': 'x'}, '对象输入按声明结构验证通过', resolved)
ok, got = expect_plan_error(422, '缺少声明字段',
                            lambda: flow_test_plan.precheck_values(obj_state, plan_o, {}, {'O': {'in_o': {'a': 1}}}))
check(ok, '对象缺声明字段 422（缺字段≠显式空值）', got)
ok, got = expect_plan_error(422, '需要文本',
                            lambda: flow_test_plan.precheck_values(obj_state, plan_o, {}, {'O': {'in_o': {'a': 1, 'b': 2}}}))
check(ok, '对象字段类型不符 422', got)
ok, got = expect_plan_error(422, '数值不能是 NaN 或无穷',
                            lambda: flow_test_plan.precheck_values(STATE, plan_bcd, {'x': 10}, {'B': {'in_x': float('nan')}}))
check(ok, 'NaN 数值拒绝', got)
check(flow_test_plan.validate_test_value({'type': 'list', 'elementType': {'type': 'number'}}, [1, 'a']) is not None,
      '列表元素类型错误被识别')

# 5) 真实范围执行：B–D 输入 10 → 12/36/18，A/E 计数 0 ─────────────────────────
import auth_client as _auth_client  # noqa: E402
_auth_client.bind_fixture_user()  # 路由层走 auth.require_user_id（与 test_flow_executor 一致）
CTX = {'project_id': '', 'connections': {}, 'credential_ids': None}
body, status_code = flow_routes.post_flow_run({'state': STATE, 'testMode': 'isolated', 'targets': ['B', 'C', 'D'],
                                               'inputOverrides': {'B': {'in_x': 10}}, 'inputs': {'x': 10}})
check(status_code == 200, 'isolated B–D 请求成功', (status_code, body))
statuses = {r['nodeId']: r['status'] for r in body['nodeResults']}
check(list(statuses) == ['B', 'C', 'D'], 'nodeResults 只包含所选范围且按执行顺序', body['nodeResults'])
outputs = {r['nodeId']: r['outputs'].get('v') for r in body['nodeResults']}
check(outputs == {'B': 12, 'C': 36, 'D': 18}, 'B.x=10 → B=12、C=36、D=18（真实执行）', outputs)
check('A' not in statuses and 'E' not in statuses, 'A/E 未出现在执行结果中（执行计数 0 的结构证据）', statuses)
check(body['nodeResults'][0].get('inputs') == {'x': 10} and body['nodeResults'][0].get('inputsTruncated') is False,
      '节点实际输入预览返回且未截断', body['nodeResults'][0].get('inputs'))

# 6) 预检失败零执行：缺边界值 → 422 且无 nodeResults ──────────────────────────
body, status_code = flow_routes.post_flow_run({'state': STATE, 'testMode': 'isolated', 'targets': ['B', 'C', 'D'],
                                               'inputOverrides': {}, 'inputs': {'x': 10}})
check(status_code == 422 and 'nodeResults' not in body, '缺边界值在任何节点执行前 422 拒绝（零执行）', (status_code, body))
body, status_code = flow_routes.post_flow_run({'state': STATE, 'testMode': 'isolated', 'targets': ['B', 'C', 'D'],
                                               'inputOverrides': {'C': {'in_x': 1}}, 'inputs': {'x': 10}})
check(status_code == 400, '内部依赖覆盖在结构层 400 拒绝', (status_code, body))
body, status_code = flow_routes.post_flow_run({'state': STATE, 'testMode': 'isolated', 'inputOverrides': {'B': {'in_x': 1}}})
check(status_code == 400, '省略 targets 却携带 isolated/overrides 400', (status_code, body))
body, status_code = flow_routes.post_flow_run({'state': STATE, 'testMode': 'whatever', 'targets': ['B']})
check(status_code == 400, '非法 testMode 值 400', (status_code, body))

# 7) 失败传递：B 失败 → C/D skipped；已成功结果保留 ────────────────────────────
broken = chain_state()
broken['nodes'][1] = dict(broken['nodes'][1])
# 语法合法但运行期必然失败（除零）：必须穿过配置检查、在执行期失败
broken['nodes'][1]['implementation'] = {'mode': 'formula', 'formulas': {'v': '{x} / 0'}}
body, status_code = flow_routes.post_flow_run({'state': broken, 'testMode': 'isolated', 'targets': ['B', 'C', 'D'],
                                               'inputOverrides': {'B': {'in_x': 10}}, 'inputs': {'x': 10}})
statuses = {r['nodeId']: r['status'] for r in body['nodeResults']}
check(status_code == 200 and statuses == {'B': 'failed', 'C': 'skipped', 'D': 'skipped'}, 'B 失败沿所选范围传递：C/D 均 skipped', body['nodeResults'])
check(body['status'] == 'error' and body['nodeResults'][0].get('error'), '失败节点保留错误信息', body['nodeResults'][0])
check(body['nodeResults'][0].get('inputs') == {'x': 10}, '执行器已收到输入后失败可展示已解析输入', body['nodeResults'][0])

# 中间失败（C 失败）→ D skipped，B 成功保留
broken_c = chain_state()
broken_c['nodes'][2] = dict(broken_c['nodes'][2])
broken_c['nodes'][2]['implementation'] = {'mode': 'formula', 'formulas': {'v': '{x} / 0'}}
body, status_code = flow_routes.post_flow_run({'state': broken_c, 'testMode': 'isolated', 'targets': ['B', 'C', 'D'],
                                               'inputOverrides': {'B': {'in_x': 10}}, 'inputs': {'x': 10}})
statuses = {r['nodeId']: r['status'] for r in body['nodeResults']}
check(statuses == {'B': 'success', 'C': 'failed', 'D': 'skipped'}, 'C 失败 → B 成功保留、D skipped', statuses)

# 8) 全图对照：入口 10 → E=1800（A/E 真实执行） ───────────────────────────────
body, status_code = flow_routes.post_flow_run({'state': STATE, 'inputs': {'x': 10}})
check(status_code == 404, '无 testMode 的省略 targets 仍走全图分支（存在性/revision gate，不被 isolated 改义）', (status_code, body))

plan_all = flow_test_plan.plan_scope(STATE, ['A', 'B', 'C', 'D', 'E'], {})
entry_values, resolved = flow_test_plan.precheck_values(STATE, plan_all, {'x': 10}, {})
run_all = __import__('workbench.flow_executor', fromlist=['flow_executor']).run(
    STATE, plan_all['orderedTargets'], entry_values, CTX, isolated_overrides=resolved)
all_outputs = {r['nodeId']: r['outputs'].get('v') for r in run_all['nodeResults']}
check(all_outputs == {'A': 10, 'B': 12, 'C': 36, 'D': 18, 'E': 1800}, '全图入口 10 → E=1800（A–E 全部执行）', all_outputs)

# 9) 旧调用兼容（无 testMode）：单节点技术名覆盖、链闭合、[] 拒绝 ────────────────
body, status_code = flow_routes.post_flow_run({'state': STATE, 'targets': ['B'], 'inputs': {'B.x': 10}})
check(status_code == 200 and body['nodeResults'][0]['outputs']['v'] == 12,
      '旧单节点测试（技术名 B.x 覆盖）行为不变', body)
body, status_code = flow_routes.post_flow_run({'state': STATE, 'targets': ['B', 'C'], 'inputs': {'B.x': 10}})
check(status_code == 400, '旧多节点链仍要求上游闭合（不被新模式静默改义）', (status_code, body))
body, status_code = flow_routes.post_flow_run({'state': STATE, 'targets': [], 'inputs': {}})
check(status_code == 400, 'targets=[] 仍是一律 400（绝不解释为全图）', (status_code, body))

# 10) bounded_preview：列表 ≤100 项（previewTruncated 标记结构），其他输入不变 ────
big = list(range(250))
preview, truncated = flow_test_plan.bounded_preview({'rows': big, 'x': 1})
rows_view = preview['rows']
check(truncated is True and isinstance(rows_view, dict) and rows_view.get('previewTruncated') is True
      and rows_view.get('totalItems') == 250 and len(rows_view.get('items') or []) == 100
      and preview['x'] == 1,
      '超 100 项列表以标记结构表示且仅含 100 项，其他输入不变',
      (type(rows_view).__name__, truncated))
preview, truncated = flow_test_plan.bounded_preview({'x': 1})
check(truncated is False and preview == {'x': 1}, '小结果不标记截断且结构不变')

# 11) R06：预览字节预算（UTF-8 JSON ≤65536）+ 原值不变 ─────────────────────────
def _utf8_len(obj):
    return len(json.dumps(obj, ensure_ascii=False, default=str).encode('utf-8'))

cases = {
    '1MiB 文本': {'text': 'x' * (1024 * 1024)},
    '中文长文本': {'备注': '储能' * 60000},
    '嵌套对象': {'deep': {'a': {'b': {'c': ['x' * 500] * 500}}}},
    '101+ 列表': {'rows': list(range(5000))},
    '超长字段名': {'k' * 5000: 'v'},
    '多字段合计超限': {f'field_{i}': '值' * 200 for i in range(300)},
}
for label, original in cases.items():
    preview, truncated = flow_test_plan.bounded_preview(original)
    size = _utf8_len(preview)
    check(size <= 65536, f'{label}：预览序列化 ≤65536 字节（实际 {size}）', size)
    check(truncated is True, f'{label}：truncated 正确标记')
    untouched = json.dumps(original, ensure_ascii=False, default=str) == json.dumps(
        {k: v for k, v in original.items()}, ensure_ascii=False, default=str)
    check(untouched, f'{label}：原输入对象未被修改')
    if label == '1MiB 文本':
        marker = preview['text']
        check(marker.get('previewTruncated') is True and marker.get('originalLength') == 1024 * 1024
              and len(marker.get('shownPrefix') or '') <= 512,
              '截短字符串带 shownPrefix/originalLength 标记', {k: v for k, v in marker.items() if k != 'shownPrefix'})
    if label == '101+ 列表':
        check(preview['rows'].get('totalItems') == 5000 and len(preview['rows'].get('items') or []) == 100,
              '大列表标记 items≤100 且 totalItems 正确')

shutil.rmtree(TMP, ignore_errors=True)
print(f'\n全部通过：{len(PASSED)} 项')
