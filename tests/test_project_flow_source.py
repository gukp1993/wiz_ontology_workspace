"""项目属性「函数编排」取值来源后端校验回归（2026-09-18 需求）。

覆盖 `project_validation` 对 `kind='flow'` 的校验分支：编排存在性、输出选择与类型
相容、对象/列表输出与时间序列属性拒绝、输入绑定齐全性与来源合法性（当前对象属性／
固定值／实例编号）、固定值边界（0／false／空串为有效值）、输入类型相容表、引用属性
存在性与类型匹配、编排签名变更（多余输入）、以及本对象内属性间的循环依赖检测。

纯 python3 标准库直跑；WIZ_WORKBENCH_ROOT 挂临时数据根，真实 ontology/ 只读不碰；
编排用 flows 模块在临时根内自建，不依赖真实编排数据。
运行：python3 tests/test_project_flow_source.py
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
TMP = Path(tempfile.mkdtemp(prefix='wiz_flow_src_'))
os.environ['WIZ_WORKBENCH_ROOT'] = str(TMP)

from workbench import flows  # noqa: E402  （临时根就位后再 import）
from workbench.project_validation import validate_project  # noqa: E402

# 账号体系（20260918）：域级测试绑定测试账号
from pathlib import Path as _P
sys.path.insert(0, str(_P(__file__).resolve().parent))
import auth_client as _auth_client
_auth_client.bind_fixture_user()

PASSED = []


def check(cond, message, actual=None):
    """断言：失败即打印实际值、清理临时根并退出（与仓库既有回归脚本同法）。"""
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


# --- 夹具：引用版本 graph（deviceCode=string / maxPower=double / online=boolean） ------------

GRAPH = [
    {'@id': 'mg:StorageDevice', '@type': 'owl:Class', 'rdfs:label': '储能设备'},
    {'@id': 'mg:deviceCode', '@type': 'owl:DatatypeProperty', 'rdfs:label': '设备编号',
     'rdfs:domain': {'@id': 'mg:StorageDevice'}, 'mg:apiName': 'deviceCode',
     'rdfs:range': {'@id': 'xsd:string'}},
    {'@id': 'mg:maxPower', '@type': 'owl:DatatypeProperty', 'rdfs:label': '最大功率',
     'rdfs:domain': {'@id': 'mg:StorageDevice'}, 'mg:apiName': 'maxPower',
     'rdfs:range': {'@id': 'xsd:double'}},
    {'@id': 'mg:online', '@type': 'owl:DatatypeProperty', 'rdfs:label': '是否在线',
     'rdfs:domain': {'@id': 'mg:StorageDevice'}, 'mg:apiName': 'online',
     'rdfs:range': {'@id': 'xsd:boolean'}},
    {'@id': 'mg:powerSeries', '@type': 'owl:DatatypeProperty', 'rdfs:label': '功率序列',
     'rdfs:domain': {'@id': 'mg:StorageDevice'}, 'mg:apiName': 'powerSeries',
     'mg:valueShape': 'timeSeries', 'rdfs:range': {'@id': 'xsd:double'}},
]

FIELD_CODE = {'kind': 'field', 'field': 'device_code'}


def onto():
    return {'ontology': {'@context': {}, '@graph': copy.deepcopy(GRAPH)}, 'workflow': {}}


def proj(props):
    """项目状态夹具：单一对象绑定，primary_key 已配置（实例编号绑定可用）。"""
    return {'projectId': 'p1', 'name': '编排项目', 'ontologyId': 'storage', 'ontologyVersion': '1.0.0',
            'connections': {'connections': [{'id': 'conn1', 'name': '主库', 'engine': 'mysql'}]},
            'bindings': {'notice': '', 'source_candidates': [],
                         'object_bindings': [{'object_type': 'StorageDevice', 'connection': 'conn1',
                                              'table': 'devices', 'primary_key': 'device_id',
                                              'properties': copy.deepcopy(props)}],
                         'actionBindings': []},
            'implementations': [], 'parameters': {}}


def make_flow(name, inputs=(), outputs=()):
    """在临时根内创建编排：inputs/outputs 为编排级声明（与前端 flow state 结构一致）。"""
    created = flows.create(name)
    state = flows.read_draft(created['id'])
    state.pop('_draft')
    state['inputs'] = [dict(i, id=i.get('id') or 'in_' + i['name']) for i in inputs]
    state['outputs'] = [dict(o, id=o.get('id') or 'out_' + o['name']) for o in outputs]
    flows.save_draft(state)
    return created['id']


def flow_src(flow_id, output, inputs=None, result=None):
    src = {'kind': 'flow', 'flow': flow_id, 'output': output, 'inputs': dict(inputs or {})}
    if result is not None:
        src['result'] = dict(result)
    return src


def errs(props):
    return validate_project(proj(props), onto())['errors']


# 编排 A：输入 设备编号(text) + 系数(number)，输出 功率(number)
FLOW_A = make_flow('功率计算',
    inputs=[{'name': 'code', 'label': '设备编号', 'type': {'type': 'text'}},
            {'name': 'factor', 'label': '系数', 'type': {'type': 'number'}}],
    outputs=[{'name': 'power', 'label': '功率', 'type': {'type': 'number'}}])
# 编排 B：对象输出（不可绑定任何属性）
FLOW_OBJ = make_flow('对象输出', outputs=[{'name': 'bundle', 'label': '数据包', 'type': {'type': 'object'}}])
# 编排 C：文本输出，输入 功率(number)，用于构造与 A 的循环依赖
FLOW_C = make_flow('编号生成', inputs=[{'name': 'power', 'label': '功率', 'type': {'type': 'number'}}],
                   outputs=[{'name': 'code', 'label': '编号', 'type': {'type': 'text'}}])
# 编排 S：系列列表输出 list<object{timestamp:datetime, value:number}>（时间序列属性可绑）
FLOW_SERIES = make_flow('采样序列',
    outputs=[{'name': 'samples', 'label': '采样序列',
              'type': {'type': 'list', 'elementType': {'type': 'object', 'fields': [
                  {'id': 'fld_ts', 'name': 'timestamp', 'label': '时间戳', 'type': {'type': 'datetime'}},
                  {'id': 'fld_v', 'name': 'value', 'label': '采样值', 'type': {'type': 'number'}}]}}}])
# 编排 S2：同结构但取值字段是文本（字段类型不符用）
FLOW_S2 = make_flow('采样序列文本值',
    outputs=[{'name': 'samples', 'label': '采样序列',
              'type': {'type': 'list', 'elementType': {'type': 'object', 'fields': [
                  {'id': 'fld_ts', 'name': 'timestamp', 'label': '时间戳', 'type': {'type': 'datetime'}},
                  {'id': 'fld_v', 'name': 'value', 'label': '采样值', 'type': {'type': 'text'}}]}}}])
# 编排 S3：数值标量列表（元素不是对象，不可绑时间序列）
FLOW_NUM_LIST = make_flow('数值列表',
    outputs=[{'name': 'values', 'label': '数值列表', 'type': {'type': 'list', 'elementType': {'type': 'number'}}}])

# 1) 合法配置零错误 --------------------------------------------------------------------------
report = validate_project(
    proj({'deviceCode': dict(FIELD_CODE),
          'maxPower': flow_src(FLOW_A, 'out_power',
                               {'in_code': {'from': 'property', 'property': 'deviceCode'},
                                'in_factor': {'from': 'constant', 'value': 1.5}})}), onto())
check(report['errors'] == [], '合法编排取值通过校验（属性+固定值绑定齐全）', report['errors'])

# 2) 未选择编排 / 编排不存在 ------------------------------------------------------------------
check(has(errs({'maxPower': flow_src('', 'out_power')}), '未选择函数编排'),
      '未选择函数编排应阻断', errs({'maxPower': flow_src('', 'out_power')}))
missing = errs({'maxPower': flow_src('11111111-2222-4333-8444-555555555555', 'out_power')})
check(has(missing, '引用的函数编排不存在或已删除'), '引用不存在的编排应阻断', missing)

# 3) 输出：未选择 / 不存在 / 类型不匹配 / 对象输出 / 时间序列属性 -------------------------------
check(has(errs({'maxPower': flow_src(FLOW_A, '')}), '未选择编排输出'),
      '未选择编排输出应阻断')
check(has(errs({'maxPower': flow_src(FLOW_A, 'out_gone')}), '编排输出不存在'),
      '输出 id 不存在应阻断（编排签名变更）')
mismatch = errs({'deviceCode': flow_src(FLOW_A, 'out_power',
                                        {'in_code': {'from': 'constant', 'value': 'x'},
                                         'in_factor': {'from': 'constant', 'value': 1}})})
check(has(mismatch, '编排输出类型与属性数据类型不匹配'), 'number 输出绑定 string 属性应阻断', mismatch)
check(has(errs({'maxPower': flow_src(FLOW_OBJ, 'out_bundle')}), '编排输出为对象'),
      '对象输出不能绑定标量属性')
series = errs({'powerSeries': flow_src(FLOW_A, 'out_power',
                                       {'in_code': {'from': 'constant', 'value': 'x'},
                                        'in_factor': {'from': 'constant', 'value': 1}})})
check(has(series, '函数编排输出为单值，不能绑定时间序列属性'), '单值输出不能绑定时间序列属性', series)

# 3b) 列表输出 → 时间序列属性（2026-09-19 新契约：result 字段映射） -----------------------------
ok_series = errs({'powerSeries': flow_src(FLOW_SERIES, 'out_samples',
                                          result={'valueField': 'fld_v', 'timestampField': 'fld_ts'}),
                  'deviceCode': dict(FIELD_CODE)})
check(ok_series == [], '合法列表输出+result 映射绑定时间序列属性零错误', ok_series)
no_result = errs({'powerSeries': flow_src(FLOW_SERIES, 'out_samples')})
check(has(no_result, '未选择取值字段') and has(no_result, '未选择时间字段'),
      '列表输出缺 result 映射应阻断（取值/时间字段双提示）', no_result)
gone_field = errs({'powerSeries': flow_src(FLOW_SERIES, 'out_samples',
                                           result={'valueField': 'fld_gone', 'timestampField': 'fld_ts'})})
check(has(gone_field, '取值字段不存在'), 'result 指向不存在的元素字段应阻断（编排签名变更）', gone_field)
wrong_type = errs({'powerSeries': flow_src(FLOW_S2, 'out_samples',
                                           result={'valueField': 'fld_v', 'timestampField': 'fld_ts'})})
check(has(wrong_type, '取值字段不是数值类型'), '取值字段为文本类型应阻断', wrong_type)
ts_wrong = errs({'powerSeries': flow_src(FLOW_SERIES, 'out_samples',
                                         result={'valueField': 'fld_v', 'timestampField': 'fld_v'})})
check(has(ts_wrong, '时间字段不是日期时间类型'), '时间字段为数值类型应阻断', ts_wrong)
scalar_list = errs({'powerSeries': flow_src(FLOW_NUM_LIST, 'out_values')})
check(has(scalar_list, '列表输出的元素需为已声明字段的对象'), '标量元素列表不可绑时间序列属性', scalar_list)
scalar_prop = errs({'maxPower': flow_src(FLOW_SERIES, 'out_samples',
                                         result={'valueField': 'fld_v', 'timestampField': 'fld_ts'})})
check(has(scalar_prop, '编排输出为列表，不能绑定标量属性'), '列表输出绑标量属性仍应阻断', scalar_prop)
partial_result = errs({'powerSeries': flow_src(FLOW_SERIES, 'out_samples',
                                               result={'valueField': 'fld_v'})})
check(has(partial_result, '未选择时间字段') and not has(partial_result, '未选择取值字段'),
      'result 只填取值字段时仅提示缺时间字段', partial_result)

# 4) 输入绑定：缺失 / 无效来源 / 多余输入（签名变更） -------------------------------------------
partial = errs({'maxPower': flow_src(FLOW_A, 'out_power',
                                     {'in_code': {'from': 'constant', 'value': 'x'}})})
check(has(partial, '编排输入「系数」未绑定取值'), '输入未绑定应阻断（按 label 提示）', partial)
bad_src = errs({'maxPower': flow_src(FLOW_A, 'out_power',
                                     {'in_code': {'from': 'unknownWay', 'property': 'deviceCode'},
                                      'in_factor': {'from': 'constant', 'value': 1}})})
check(has(bad_src, '的绑定来源无效'), '未知绑定来源应阻断', bad_src)
extra = errs({'maxPower': flow_src(FLOW_A, 'out_power',
                                   {'in_code': {'from': 'constant', 'value': 'x'},
                                    'in_factor': {'from': 'constant', 'value': 1},
                                    'in_removed': {'from': 'constant', 'value': 1}})})
check(has(extra, '编排不存在输入'), '多余输入 id 应阻断（编排已删除该输入）', extra)

# 5) 固定值边界：0 / false 有效，非数字无效 -----------------------------------------------------
zero = errs({'maxPower': flow_src(FLOW_A, 'out_power',
                                  {'in_code': {'from': 'constant', 'value': ''},
                                   'in_factor': {'from': 'constant', 'value': 0}})})
check(not has(zero, '固定值无效'), '固定值 0 与空字符串均为有效值（不得误判缺失）', zero)
bad_const = errs({'maxPower': flow_src(FLOW_A, 'out_power',
                                       {'in_code': {'from': 'constant', 'value': 'x'},
                                        'in_factor': {'from': 'constant', 'value': 'abc'}})})
check(has(bad_const, '编排输入「系数」的固定值无效'), 'number 输入填非数字应阻断', bad_const)
bool_bad = errs({'maxPower': flow_src(FLOW_A, 'out_power',
                                      {'in_code': {'from': 'constant', 'value': 'x'},
                                       'in_factor': {'from': 'constant', 'value': True}})})
check(has(bool_bad, '的固定值无效'), 'number 输入填布尔应阻断', bool_bad)

# 6) 实例编号：仅可绑文本输入；非文本输入阻断 ---------------------------------------------------
inst_bad = errs({'maxPower': flow_src(FLOW_A, 'out_power',
                                      {'in_code': {'from': 'instanceId'},
                                       'in_factor': {'from': 'constant', 'value': 1}})})
check(not has(inst_bad, '不能绑定实例编号'), '文本输入绑定实例编号应通过', inst_bad)
inst_num = errs({'maxPower': flow_src(FLOW_A, 'out_power',
                                      {'in_code': {'from': 'constant', 'value': 'x'},
                                       'in_factor': {'from': 'instanceId'}})})
check(has(inst_num, '不是文本类型，不能绑定实例编号'), 'number 输入绑定实例编号应阻断', inst_num)

# 7) 属性引用：不存在 / 类型不匹配 / 时间序列 ---------------------------------------------------
gone = errs({'maxPower': flow_src(FLOW_A, 'out_power',
                                  {'in_code': {'from': 'property', 'property': 'noSuchProp'},
                                   'in_factor': {'from': 'constant', 'value': 1}})})
check(has(gone, '编排输入「设备编号」引用的属性不存在'), '引用不存在的属性应阻断', gone)
wrong_type = errs({'maxPower': flow_src(FLOW_A, 'out_power',
                                        {'in_code': {'from': 'property', 'property': 'online'},
                                         'in_factor': {'from': 'constant', 'value': 1}})})
check(has(wrong_type, '数据类型不匹配'), 'text 输入引用 boolean 属性应阻断', wrong_type)
ts_in = errs({'maxPower': flow_src(FLOW_A, 'out_power',
                                   {'in_code': {'from': 'property', 'property': 'powerSeries'},
                                    'in_factor': {'from': 'constant', 'value': 1}})})
check(has(ts_in, '是时间序列，不能作为输入'), '时间序列属性不能作为编排输入', ts_in)

# 8) 循环依赖：maxPower ← deviceCode ← maxPower ------------------------------------------------
cycle = errs({'maxPower': flow_src(FLOW_A, 'out_power',
                                   {'in_code': {'from': 'property', 'property': 'deviceCode'},
                                    'in_factor': {'from': 'constant', 'value': 1}}),
              'deviceCode': flow_src(FLOW_C, 'out_code',
                                     {'in_power': {'from': 'property', 'property': 'maxPower'}})})
check(has(cycle, '循环依赖'), '编排输入互相引用形成环应阻断', cycle)

# 9) 自引用：属性输入引用自身 -------------------------------------------------------------------
self_ref = errs({'maxPower': flow_src(FLOW_A, 'out_power',
                                      {'in_code': {'from': 'property', 'property': 'maxPower'},
                                       'in_factor': {'from': 'constant', 'value': 1}})})
check(has(self_ref, '循环依赖') or has(self_ref, '属性来源存在循环依赖'),
      '属性引用自身应阻断', self_ref)

shutil.rmtree(TMP, ignore_errors=True)
print(f'\n全部通过（{len(PASSED)} 步）：kind=flow 取值来源校验符合契约。')
