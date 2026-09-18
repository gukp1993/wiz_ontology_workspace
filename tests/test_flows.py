"""函数编排一期后端回归（存储 / 修订并发 / 配置检查 / 复制重映射 / 软删除）。

纯 python3 标准库直跑：WIZ_WORKBENCH_ROOT 挂临时数据根，一切写入只进临时目录，
真实 ontology/ 只读不碰。路由函数直接调用（与 test_query_rules.py 同法），
不占真实端口。对应验收项：A8/A9/A11/A12/A13/A14/A15 及往返零丢失。
运行：python3 tests/test_flows.py
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
TMP = Path(tempfile.mkdtemp(prefix='wiz_flows_'))
os.environ['WIZ_WORKBENCH_ROOT'] = str(TMP)

from workbench import flows, flow_routes  # noqa: E402  （临时根就位后再 import）

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


def blank(name='测试编排'):
    created = flows.create(name)
    state = flows.read_draft(created['id'])
    state.pop('_draft')
    return created, state


def node(kind, name, inputs=(), outputs=(), impl=None, description=''):
    return {'id': f'nd_{name}', 'kind': kind, 'name': name, 'description': description,
            'inputs': [dict(i, id='in_' + i['name']) for i in inputs],
            'outputs': [dict(o, id='out_' + o['name']) for o in outputs],
            'implementation': impl or ({'language': 'python', 'code': f'def main():\n    return None\n'} if kind == 'python'
                                       else {'language': 'sql', 'sql': 'SELECT 1', 'connectionId': ''})}


# 1) 创建 / 列表 / 修订往返 / 未知字段零丢失 ------------------------------------------------
created, state = blank()
check(bool(created['id']) and flows.read_draft(created['id']) is not None, 'A12 创建编排并落盘草稿')
state['nodes'].append(node('python', '清洗', inputs=[{'name': 'raw', 'label': '原始数据', 'type': {'type': 'text'}}],
                           outputs=[{'name': 'rows', 'label': '行列表', 'type': {'type': 'list', 'elementType': {'type': 'object'}}}]))
state['nodes'][0]['vendorNote'] = 'keep-me'
result = flows.save_draft(state)
loaded = flows.read_draft(created['id'])
check(loaded.pop('_draft') is not None and loaded == state, 'A2/A3 保存重开深度一致（含对象/列表类型声明）',
      {'loaded': loaded, 'state': state})
check(loaded['nodes'][0]['vendorNote'] == 'keep-me', '未知字段零丢失保留')
rev1 = result['revision']
check(flows.save_draft(state)['seq'] == 3, '修订序号递增（重复内容也产生新修订）')

# 2) 并发：旧 revision 拒绝（A13） ------------------------------------------------------------
flows.save_draft(state)                       # 服务端最新修订 = 当前内容
state_newer = copy.deepcopy(state)
state_newer['description'] = '服务端较新的草稿'
flows.save_draft(state_newer)                 # 服务端又前进一版
payload, status = flow_routes.post_flow_save({'state': state, 'revision': rev1})
check(status == 409 and payload.get('currentRevision') == flows.current_token(created['id']),
      'A13 旧 revision 保存被 409 拒绝且带 currentRevision', {'status': status, 'payload': payload})

# 3) 结构损坏 / 非法请求拒绝 -------------------------------------------------------------------
bad = copy.deepcopy(state)
bad['inputs'] = 'not-a-list'
payload, status = flow_routes.post_flow_save({'state': bad, 'revision': flows.current_token(created['id'])})
check(status == 400, '结构损坏（inputs 非列表）保存被拒绝', {'status': status})
bad_boundary = {'flowId': created['id'], 'nodes': [dict(node('python', 'x'), id='flow-input')], 'inputs': [], 'outputs': [], 'connections': []}
payload, status = flow_routes.post_flow_save({'state': bad_boundary,
                                              'revision': flows.current_token(created['id'])})
check(status == 400, '边界节点 ID 混入处理节点被拒绝', {'status': status})
payload, status = flow_routes.post_flow_check({'state': None})
check(status == 400, 'flow-check 缺 state 返回 400', {'status': status})

# 4) 空编排 + 名称校验 ------------------------------------------------------------------------
created2, state2 = blank('空编排')
check(flows.check_flow(state2)['status'] == 'passed', '空编排（仅名称）配置检查通过')
try:
    flows.create('测试编排')
    check(False, '同名编排创建被拒绝')
except ValueError:
    check(True, '同名编排创建被拒绝')

# 5) 循环依赖（A9） ---------------------------------------------------------------------------
created3, flow = blank('环检测')
a = node('python', 'A', inputs=[{'name': 'x', 'label': 'x', 'type': {'type': 'number'}}],
         outputs=[{'name': 'ao', 'label': 'ao', 'type': {'type': 'number'}}])
b = node('python', 'B', inputs=[{'name': 'y', 'label': 'y', 'type': {'type': 'number'}}],
         outputs=[{'name': 'bo', 'label': 'bo', 'type': {'type': 'number'}}])
flow['nodes'] += [a, b]
flow['nodes'][0]['inputs'][0]['source'] = {'kind': 'node', 'nodeId': a['id'], 'outputId': 'out_ao'}
check(any('自身' in e for e in flows.check_flow(flow)['errors']), 'A9 自引用被识别')
flow['nodes'][1]['inputs'][0]['source'] = {'kind': 'node', 'nodeId': a['id'], 'outputId': 'out_ao'}
check(not any('循环' in e for e in flows.check_flow(flow)['errors']), '无环引用不报循环')
flow['nodes'][0]['inputs'][0]['source'] = {'kind': 'node', 'nodeId': b['id'], 'outputId': 'out_bo'}
report = flows.check_flow(flow)
check(any('循环依赖' in e and 'A' in e and 'B' in e for e in report['errors']), 'A9 双节点循环被识别', report['errors'])
c = node('python', 'C', inputs=[{'name': 'z', 'label': 'z', 'type': {'type': 'number'}}],
         outputs=[{'name': 'co', 'label': 'co', 'type': {'type': 'number'}}])
c['inputs'][0]['source'] = {'kind': 'node', 'nodeId': a['id'], 'outputId': 'out_ao'}
flow['nodes'].append(c)
b['inputs'][0]['source'] = {'kind': 'node', 'nodeId': c['id'], 'outputId': 'out_co'}
check(any('循环依赖' in e and 'C' in e for e in flows.check_flow(flow)['errors']), 'A9 三节点循环被识别')

# 6) 类型相容 / 失效引用（A6/A8） --------------------------------------------------------------
created4, flow = blank('类型检查')
src = node('python', '来源', outputs=[
    {'name': 'num', 'label': '数值', 'type': {'type': 'number'}},
    {'name': 'obj', 'label': '对象', 'type': {'type': 'object', 'fields': [
        {'id': 'fld_a', 'name': 'aa', 'label': '甲', 'type': {'type': 'text'}}]}}])
dst = node('sql', '目标', inputs=[{'name': 'v', 'label': 'v', 'type': {'type': 'text'}}],
           outputs=[], impl={'language': 'sql', 'sql': 'SELECT :v AS v', 'connectionId': 'cn_1'})
flow['nodes'] = [src, dst]
flow['connections'] = [{'id': 'cn_1', 'name': '业务数据库', 'engine': 'mysql'}]
flow['nodes'][1]['inputs'][0]['source'] = {'kind': 'nodeField', 'nodeId': src['id'], 'outputId': 'out_obj', 'fieldPath': ['fld_a']}
check(flows.check_flow(flow)['status'] == 'passed', 'A6 对象字段引用（按稳定 ID 路径）检查通过',
      flows.check_flow(flow)['errors'])
flow['nodes'][1]['inputs'][0]['type'] = {'type': 'number'}
check(any('类型不相容' in e for e in flows.check_flow(flow)['errors']), '文本→数值 类型不相容报错')
flow['nodes'][1]['inputs'][0]['type'] = {'type': 'text'}
flow['nodes'][1]['inputs'][0]['source']['fieldPath'] = ['fld_missing']
check(any('不存在的对象字段' in e for e in flows.check_flow(flow)['errors']), '失效字段引用报错（A8 定位）')
flow['nodes'][1]['inputs'][0]['source']['nodeId'] = 'nd_gone'
check(any('不存在的节点' in e for e in flows.check_flow(flow)['errors']), '失效节点引用报错')
flow['nodes'][1]['inputs'][0]['source'] = {'kind': 'flowInput', 'inputId': 'fin_gone'}
check(any('不存在的编排入口参数' in e for e in flows.check_flow(flow)['errors']), '失效入口参数引用报错')

# 7) 固定值与未绑定区分（A11） ------------------------------------------------------------------
created5, flow = blank('固定值')
p = node('python', 'P', inputs=[{'name': 'a', 'label': 'a', 'type': {'type': 'number'}},
                                {'name': 'b', 'label': 'b', 'type': {'type': 'boolean'}},
                                {'name': 'c', 'label': 'c', 'type': {'type': 'text'}},
                                {'name': 'd', 'label': 'd', 'type': {'type': 'text'}}])
flow['nodes'] = [p]
flow['nodes'][0]['inputs'][0]['source'] = {'kind': 'fixed', 'valueType': 'number', 'value': 0}
flow['nodes'][0]['inputs'][1]['source'] = {'kind': 'fixed', 'valueType': 'boolean', 'value': False}
flow['nodes'][0]['inputs'][2]['source'] = {'kind': 'fixed', 'valueType': 'text', 'value': ''}
check(flows.check_flow(flow)['errors'] == [], 'A11 固定值 0 / false / 空字符串是有效绑定', flows.check_flow(flow)['errors'])
check(len([w for w in flows.check_flow(flow)['warnings'] if 'd' in w]) == 1, '未绑定的输入 d 单独提示待完善')
flow['nodes'][0]['inputs'][0]['source'] = {'kind': 'fixed', 'valueType': 'number', 'value': 'abc'}
check(any('固定值' in e for e in flows.check_flow(flow)['errors']), '固定值类型不匹配报错')

# 8) SQL 节点：数据连接来自项目"数据连接"菜单（A10） -----------------------------------------
created6, flow = blank('SQL 检查')
s = node('sql', '查询', inputs=[{'name': 'limit_n', 'label': '条数', 'type': {'type': 'number'}}],
         outputs=[{'name': 'rows', 'label': '结果', 'type': {'type': 'list', 'elementType': {'type': 'object'}}}],
         impl={'language': 'sql', 'sql': "SELECT id FROM t WHERE k = '-- :not_a_param' AND n < :limit_n", 'connectionId': 'cn_x'})
flow['nodes'] = [s]
ctx = [{'id': 'cn_ok', 'name': '业务数据库', 'engine': 'mysql'}]
report = flows.check_flow(flow, ctx)
check(any('数据连接不存在' in e for e in report['errors']), '引用不在项目数据连接中的 id 报错')
check(any('请选择数据连接' not in e for e in report['errors']), '已选连接不误报未选择')
check(not any('not_a_param' in e for e in report['errors']), '注释内的伪参数不误判')
report = flows.check_flow(flow)
check(not any('数据连接不存在' in e for e in report['errors']) and
      any('待' in w for w in report['warnings']),
      '无项目上下文（列表页）时失效引用降为提示')
flow['nodes'][0]['inputs'].append({'id': 'in_extra', 'name': 'unused_p', 'label': '多余', 'type': {'type': 'text'}, 'source': None})
report = flows.check_flow(flow, ctx)
check(any('unused_p' in w for w in report['warnings']), '未引用的输入参数提示')
flow['nodes'][0]['implementation']['connectionId'] = 'cn_ok'
check(flows.check_flow(flow, ctx)['errors'] == [], '引用项目数据连接后通过')
flow['nodes'][0]['implementation']['connectionId'] = ''
report = flows.check_flow(flow, ctx)
check(any('请选择数据连接' in e for e in report['errors']), '未选数据连接报错')

# 8b) Redis 节点：行模式/单键模式/连接引擎（SOC 场景链路） -------------------------------------
created6b, flow = blank('Redis 检查')
clusters = node('sql', '簇列表', inputs=[{'name': 'device_id', 'label': '设备', 'type': {'type': 'text'}}],
                outputs=[{'name': 'clusters', 'label': '簇', 'type': {'type': 'list', 'elementType': {'type': 'object', 'fields': [
                    {'id': 'c1', 'name': 'id', 'label': '簇id', 'type': {'type': 'text'}},
                    {'id': 'c2', 'name': 'capacity', 'label': '容量', 'type': {'type': 'number'}}]}}}],
                impl={'language': 'sql', 'sql': 'SELECT id, capacity FROM m_storage_cluster_phase WHERE storage_id = :device_id', 'connectionId': 'pc1'})
rnode = node('redis', '簇SOC', inputs=[{'name': 'clusters', 'label': '簇', 'type': clusters['outputs'][0]['type']}],
             outputs=[{'name': 'socs', 'label': 'SOC', 'type': {'type': 'list', 'elementType': {'type': 'number'}}}],
             impl={'language': 'redis', 'connectionId': 'pc2', 'keyTemplate': 'm_storage_cluster_phase-{{id}}-soc'})
rnode['inputs'][0]['source'] = {'kind': 'node', 'nodeId': clusters['id'], 'outputId': clusters['outputs'][0]['id']}
flow['nodes'] = [clusters, rnode]
ctx = [{'id': 'pc1', 'name': '业务库', 'engine': 'mysql'}, {'id': 'pc2', 'name': '缓存', 'engine': 'redis'}]
report = flows.check_flow(flow, ctx)
check(report['errors'] == [], 'Redis 行模式（按行字段批量取 key）通过', report['errors'])
rnode['implementation']['keyTemplate'] = 'm_storage_cluster_phase-{{nope}}-soc'
check(any('不存在的字段' in e for e in flows.check_flow(flow, ctx)['errors']), '行字段缺失报错')
rnode['implementation']['keyTemplate'] = 'k-{{id}}-${device_id}'
check(any('混用' in e for e in flows.check_flow(flow, ctx)['errors']), '行模式与参数混用报错')
rnode['implementation']['keyTemplate'] = 'm_storage_cluster_phase-${cluster_id}-soc'
check(any('未声明' in e for e in flows.check_flow(flow, ctx)['errors']), '单键模式引用未声明参数报错')
rnode['implementation']['keyTemplate'] = 'm_storage_cluster_phase-${clusters}-soc'
check(flows.check_flow(flow, ctx)['errors'] == [], '单键模式引用已声明参数通过')
rnode['implementation']['keyTemplate'] = 'm_storage_cluster_phase-{{id}}-soc'
rnode['implementation']['connectionId'] = 'pc1'
check(any('Redis' in e for e in flows.check_flow(flow, ctx)['errors']), 'Redis 节点选 MySQL 连接报错')
rnode['implementation']['connectionId'] = 'pc2'
# 项目数据连接引用复制时保持不变
flows.save_draft(flow)
payload, status = flow_routes.post_flow_copy({'flowId': created6b['id']})
copied = flows.read_draft(payload['id']); copied.pop('_draft')
csql, credis = copied['nodes']
check(credis['implementation']['connectionId'] == 'pc2' and csql['implementation']['connectionId'] == 'pc1',
      '项目数据连接引用复制时保持原 id（不重映射）')
check(flows.check_flow(copied, ctx)['errors'] == [], '复制后的 Redis 编排检查通过')

# 9) 编排输出绑定（A12 待完善定位） -------------------------------------------------------------
created7, flow = blank('输出绑定')
src = node('python', '算数', outputs=[{'name': 'value', 'label': '结果', 'type': {'type': 'number'}}])
flow['nodes'] = [src]
flow['outputs'] = [{'id': 'fout_1', 'name': 'result', 'label': '最终结果', 'type': {'type': 'number'}, 'binding': None}]
report = flows.check_flow(flow)
check(any('尚未绑定' in e for e in report['errors']) and
      any(i['kind'] == 'flow-output' and i['id'] == 'fout_1' for i in report['items']),
      '编排输出未绑定报错且 item 带 kind/id 定位', report)
flow['outputs'][0]['binding'] = {'kind': 'node', 'nodeId': src['id'], 'outputId': 'out_value'}
check(flows.check_flow(flow)['status'] == 'passed', '编排输出绑定后检查通过')
flows.save_draft(flow)  # 服务端保留这份通过态，供列表状态断言使用
flow['outputs'][0]['type'] = {'type': 'text'}
check(any('不相容' in e for e in flows.check_flow(flow)['errors']), '输出声明类型与来源矛盾报错')

# 10) 复制重映射（A14） -------------------------------------------------------------------------
created8, flow = blank('复制源')
flow['inputs'] = [{'id': 'fin_1', 'name': 'factor', 'label': '系数', 'type': {'type': 'number'}}]
flow['connections'] = [{'id': 'cn_1', 'name': '业务库', 'engine': 'mysql'}]
n1 = node('python', 'N1', inputs=[{'name': 'f', 'label': 'f', 'type': {'type': 'number'}},
                                  {'name': 'g', 'label': 'g', 'type': {'type': 'text'}}],
          outputs=[{'name': 'o', 'label': 'o', 'type': {'type': 'number'}},
                   {'name': 'obj', 'label': 'obj', 'type': {'type': 'object', 'fields': [
                       {'id': 'fld_1', 'name': 'ff', 'label': 'ff', 'type': {'type': 'text'}}]}}],
          impl={'language': 'python', 'code': 'def main(f, g):\n    return {"o": f}\n'})
n1['inputs'][0]['source'] = {'kind': 'flowInput', 'inputId': 'fin_1'}
n1['inputs'][1]['source'] = {'kind': 'fixed', 'valueType': 'text', 'value': '原样'}
n2 = node('sql', 'N2', inputs=[{'name': 'v', 'label': 'v', 'type': {'type': 'number'}}], outputs=[],
          impl={'language': 'sql', 'sql': 'SELECT :v', 'connectionId': 'cn_1'})
n2['inputs'][0]['source'] = {'kind': 'node', 'nodeId': n1['id'], 'outputId': 'out_o'}
flow['nodes'] = [n1, n2]
flow['outputs'] = [{'id': 'fout_9', 'name': 'result', 'label': '结果', 'type': {'type': 'number'},
                    'binding': {'kind': 'node', 'nodeId': n1['id'], 'outputId': 'out_o'}},
                   {'id': 'fout_10', 'name': 'field', 'label': '字段', 'type': {'type': 'text'},
                    'binding': {'kind': 'nodeField', 'nodeId': n1['id'], 'outputId': 'out_obj', 'fieldPath': ['fld_1']}}]
flow['layout'] = {'positions': {n1['id']: {'x': 10, 'y': 20}, n2['id']: {'x': 300, 'y': 20}}, 'zoom': 1.2, 'pan': {'x': 1, 'y': 2}}
flows.save_draft(flow)
original_revision = flows.current_token(created8['id'])
payload, status = flow_routes.post_flow_copy({'flowId': created8['id']})
check(status == 201 and payload['id'] != created8['id'], 'A14 复制生成新编排', {'status': status})
copied = flows.read_draft(payload['id'])
copied.pop('_draft')
check(flows.current_token(created8['id']) == original_revision, 'A14 原编排未被修改')
all_new_ids = [copied['flowId'], *[i['id'] for i in copied['inputs']], *[o['id'] for o in copied['outputs']],
               *[n['id'] for n in copied['nodes']], *[c['id'] for c in copied['connections']],
               *[f['id'] for f in copied['nodes'][0]['outputs'][1]['type']['fields']]]
old_ids = {created8['id'], 'fin_1', 'fout_9', 'fout_10', 'cn_1', n1['id'], n2['id'], 'out_o', 'out_obj', 'fld_1'}
check(not (set(all_new_ids) & old_ids), 'A14 全部稳定 ID 重新生成')
copied_n1, copied_n2 = copied['nodes']
check(copied_n1['inputs'][0]['source'] == {'kind': 'flowInput', 'inputId': copied['inputs'][0]['id']},
      'A14 入口参数引用重映射')
check(copied_n2['inputs'][0]['source']['nodeId'] == copied_n1['id'] and
      copied_n2['inputs'][0]['source']['outputId'] == copied_n1['outputs'][0]['id'], 'A14 节点输出引用重映射')
check(copied['outputs'][0]['binding']['nodeId'] == copied_n1['id'], 'A14 编排输出绑定重映射')
field_out = copied['outputs'][1]
check(field_out['binding']['fieldPath'] == [copied_n1['outputs'][1]['type']['fields'][0]['id']],
      'A14 对象字段路径重映射')
check(copied_n2['implementation']['connectionId'] == copied['connections'][0]['id'], 'A14 逻辑连接引用重映射')
check(set(copied['layout']['positions']) == {copied_n1['id'], copied_n2['id']}, 'A14 布局位置按键重映射')
check(copied_n1['inputs'][1]['source'] == {'kind': 'fixed', 'valueType': 'text', 'value': '原样'}, 'A14 固定值原样保留')
check(flows.check_flow(copied)['status'] == 'passed', 'A14 复制后配置检查通过', flows.check_flow(copied)['errors'])
try:
    flow_routes.post_flow_copy({'flowId': payload['id']})
    check(True, '默认副本名自动去重')
except Exception:
    check(False, '默认副本名自动去重')

# 11) 软删除（A15） ------------------------------------------------------------------------------
created9, _ = blank('旁证编排')
flows.save_draft(flows.read_draft(created8['id']))
before_seq = flows.read_draft(created8['id'])['_draft']['seq']
payload, status = flow_routes.post_flow_delete({'flowId': created8['id']})
check(status == 200, 'A15 软删除成功')
after_state = flows.read_draft(created8['id'])
check(after_state['_draft']['seq'] == before_seq + 1 and after_state['status'] == 'deleted',
      'A15 删除不清除修订历史（仅追加 deleted 修订，seq 单调递增）')
names = [i['id'] for i in flows.listing()]
check(created8['id'] not in names and created9['id'] in names, 'A15 删除后列表隐藏且其他编排不受影响')
check(any(i['id'] == created8['id'] for i in flows.listing(include_deleted=True)), 'A15 includeDeleted 可见已删编排')
payload, status = flow_routes.post_flow_delete({'flowId': 'nonexistent'})
check(status == 404, '删除不存在的编排返回 404')

# 12) 类型嵌套深度与结构告警 ----------------------------------------------------------------------
created10, flow = blank('深嵌套')
deep = {'type': 'list', 'elementType': {'type': 'object', 'fields': [
    {'id': 'f1', 'name': 'a', 'label': 'a', 'type': {'type': 'object', 'fields': [
        {'id': 'f2', 'name': 'b', 'label': 'b', 'type': {'type': 'object', 'fields': [
            {'id': 'f3', 'name': 'c', 'label': 'c', 'type': {'type': 'text'}}]}}]}}]}}
flow['inputs'] = [{'id': 'fin_deep', 'name': 'deep', 'label': '深层', 'type': deep}]
flow['nodes'] = [node('python', 'P', inputs=[{'name': 'shape', 'label': '结构', 'type': {'type': 'object'}}])]
report = flows.check_flow(flow)
check(not report['errors'] and any('待完善' in w for w in report['warnings']) and report['status'] == 'pending',
      '未声明字段的对象结构是待完善（warning）而非错误', report)
deep['elementType']['fields'][0]['type']['fields'][0]['type']['fields'].append(
    {'id': 'f4', 'name': 'over', 'label': 'over', 'type': {'type': 'object', 'fields': [
        {'id': 'f5', 'name': 'x', 'label': 'x', 'type': {'type': 'text'}}]}})
check(any('嵌套' in e for e in flows.check_flow(flow)['errors']), '超过对象嵌套上限（4 层）报错')

# 13) flow-state / 列表状态字段 -------------------------------------------------------------------
flows.save_draft(flow)  # created10 当前内存态：1 节点 + 未声明字段对象输入 → pending
payload, status = flow_routes.get_flow_state({'flow': [created9['id']]})
check(status == 200 and payload['state']['flowId'] == created9['id'], 'flow-state 返回草稿与 revision')
payload, status = flow_routes.get_flow_state({'flow': ['missing!']})
check(status in (400, 404), 'flow-state 非法标识被拒')
payload, status = flow_routes.get_flows({})
items = {i['id']: i for i in payload['items']}
check(items[created7['id']]['configStatus'] == 'passed' and items[created10['id']]['configStatus'] == 'pending',
      '列表 configStatus 区分通过/待完善', items.get(created10['id']))
check(items[created7['id']]['nodeCount'] == 1, '列表 nodeCount 正确')


# 14) v2 节点校验：HTTP / 计算 / Python(LLM) / Redis 白名单 / 动态 SQL / 执行参数 ---------------
v2 = flows.blank_flow('v2check', 'v2 节点校验')
v2['nodes'] = [
    {'id': 'nd_http', 'kind': 'http', 'name': '推送', 'inputs': [], 'outputs': [],
     'implementation': {'language': 'http', 'method': 'GET', 'url': 'ftp://x/{undeclared}',
                        'headers': {}, 'bodyMode': 'none', 'body': '', 'responsePath': 'a b'}},
    {'id': 'nd_calc', 'kind': 'calc', 'name': '计算',
     'inputs': [], 'outputs': [{'id': 'out_o', 'name': 'o', 'label': 'o', 'type': {'type': 'number'}}],
     'implementation': {'language': 'calc', 'mode': 'formula', 'formulas': {}}},
    {'id': 'nd_py', 'kind': 'python', 'name': '脚本', 'inputs': [], 'outputs': [],
     'implementation': {'language': 'python', 'code': 'def not_main():\n    pass\n', 'providerId': 'llm-ghost'}},
    {'id': 'nd_redis', 'kind': 'redis', 'name': '缓存', 'inputs': [], 'outputs': [],
     'implementation': {'language': 'redis', 'connectionId': '', 'command': 'KEYS', 'keyTemplate': 'k:${p}', 'args': []}},
    {'id': 'nd_sql', 'kind': 'sql', 'name': '查询', 'inputs': [], 'outputs': [],
     'implementation': {'language': 'sql', 'connectionId': '', 'sql': 'SELECT <if test="a != null">1</ifhi>'}},
]
report = flows.check_flow(v2, [], [], None)
errors = report['errors']
for needle in ['http(s)', '未声明的输入参数', '提取路径', '缺少公式', 'main 函数', 'LLM 提供方不存在',
               'KEYS', '无法解析']:
    check(any(needle in e for e in errors), f'v2 校验报错含「{needle}」', errors)
check(not any('循环依赖' in e for e in errors), '五节点无依赖不成环')
v2['nodes'][0]['implementation'].update({'url': 'https://x/{p}', 'responsePath': 'a.b'})
v2['nodes'][0]['inputs'] = []
errors = flows.check_flow(v2, [], [], None)['errors']
check(any('占位' in e and '{p}' in e for e in errors), 'URL 占位未声明报错', errors)
v2['nodes'][1]['inputs'] = [{'id': 'in_x', 'name': 'x', 'label': 'x', 'type': {'type': 'object'}, 'source': None}]
v2['nodes'][1]['outputs'] = [{'id': 'out_o', 'name': 'o', 'label': 'o', 'type': {'type': 'number'}}]
v2['nodes'][1]['implementation']['formulas'] = {'o': '{x} + 1'}
errors = flows.check_flow(v2, [], [], None)['errors']
check(any('不支持引用' in e for e in errors), '公式引用对象类型输入报错', errors)
v2['nodes'][2]['implementation']['code'] = 'def main():\n    return None\n'
v2['nodes'][2]['implementation']['providerId'] = ''
errors = flows.check_flow(v2, [], ['llm-ok'], None)['errors']
check(not any('main' in e for e in errors), '合法 main 通过语法检查')
v2['nodes'][4]['implementation']['sql'] = 'SELECT * FROM t WHERE a = #{a} <foreach collection="rows" item="x">#{x}</foreach>'
v2['nodes'][4]['inputs'] = [{'id': 'in_a', 'name': 'a', 'label': 'a', 'type': {'type': 'text'}, 'source': None}]
errors = flows.check_flow(v2, [], [], None)['errors']
check(any('foreach 引用了未声明的集合' in e for e in errors), 'foreach 集合未声明报错', errors)
v2['nodes'][4]['inputs'].append({'id': 'in_rows', 'name': 'rows', 'label': 'rows',
                                 'type': {'type': 'list', 'elementType': {'type': 'text'}}, 'source': None})
v2['nodes'][4]['execution'] = {'timeoutMs': 99999999, 'maxRows': 0, 'allowWrite': 'yes'}
errors = flows.check_flow(v2, [], [], None)['errors']
def _flat(entries):  # 兼容纯文本与 (text, code, field) 结构化两种格式
    return [' '.join(map(str, e)) if isinstance(e, (list, tuple)) else e for e in entries]
flat = _flat(errors)
check(any('超时无效' in e for e in flat) and any('行数上限无效' in e for e in flat)
      and any('允许写' in e for e in flat), '执行参数越界报错', flat)


# 15) 结构化诊断（仅 API 定位信息；errors/warnings/items/status 内容与顺序兼容） -------------------
diag_flow = flows.blank_flow('diagcheck', '诊断校验')
diag_flow['inputs'] = [{'id': 'fin_dev', 'name': 'device_id', 'label': '设备', 'type': {'type': 'text'}}]
diag_flow['nodes'] = [
    {'id': 'nd_sql', 'kind': 'sql', 'name': '查询簇',
     'inputs': [{'id': 'in_dev', 'name': 'device_id', 'label': '设备', 'type': {'type': 'text'}, 'source': None}],
     'outputs': [{'id': 'out_rows', 'name': 'rows', 'label': '行', 'type': {'type': 'list', 'elementType': {'type': 'text'}}}],
     'implementation': {'language': 'sql', 'connectionId': 'ghost-conn', 'sql': 'SELECT 1 FROM t WHERE id = :device_id'}},
]
report = flows.check_flow(diag_flow, [{'id': 'real-conn', 'name': '库', 'engine': 'mysql'}], [], None)
diags = report['diagnostics']
check(isinstance(diags, list) and all(set(d) >= {'code', 'level', 'message', 'kind', 'id', 'section', 'field', 'parameterId'} for d in diags),
      'diagnostics 元素字段齐全', diags)
codes = {(d['code'], d['id'], d['section']) for d in diags}
check(('INPUT_BINDING_MISSING', 'nd_sql', 'inputs') in codes, '缺绑定诊断指向 inputs/binding', codes)
check(('CONNECTION_NOT_FOUND', 'nd_sql', 'implementation') in codes, '连接失效诊断指向 implementation/connectionId', codes)
check(all(d['parameterId'] in (None, 'in_dev', 'out_rows') for d in diags), 'parameterId 为稳定 ID 或空')
check(report['errors'][:1] == ['数据连接不存在或已被删除，请重新选择'], 'errors 文案与顺序不受诊断影响', report['errors'])
check(any(d['code'] == 'INPUT_BINDING_MISSING' and d['level'] == 'warning' for d in diags),
      '缺绑定按 warning 级别进诊断', diags)
# 修复绑定后重检：诊断随之更新
diag_flow['nodes'][0]['inputs'][0]['source'] = {'kind': 'flowInput', 'inputId': 'fin_dev'}
diag_flow['nodes'][0]['implementation']['connectionId'] = 'real-conn'
report2 = flows.check_flow(diag_flow, [{'id': 'real-conn', 'name': '库', 'engine': 'mysql'}], [], None)
codes2 = {(d['code'], d['id']) for d in report2['diagnostics']}
check(('INPUT_BINDING_MISSING', 'nd_sql') not in codes2 and ('CONNECTION_NOT_FOUND', 'nd_sql') not in codes2,
      '修复后旧诊断不再出现', codes2)
check(report2['diagnostics'] == [] , '完全通过的编排诊断为空', report2['diagnostics'])

print(f'\n全部通过：{len(PASSED)} 项')
shutil.rmtree(TMP, ignore_errors=True)
