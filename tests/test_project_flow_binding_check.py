"""R02 修复回归：项目发布必须检查被引用编排的配置有效性（2026-09-21）。

缺陷（验收记录 §4 R02，基线 d6c73c2）：project_validation._check_flow_binding 只做
结构核对（编排存在、输出存在、类型相容、输入绑定），从不调用 flows.check_flow——
被项目 kind:'flow' 引用、自身 check 已判 OUTPUT_BINDING_MISSING 的空壳编排，
project-validate 无阻断、project-publish 200 并新增版本。

修复语义（逐条对应验收要求；20260921 合并口径见括注）：
- 对**被项目引用**的编排调用 flows.check_flow（纯配置检查：不执行 SQL/Python、不探测连接；
  合并后按 main 侧 B01/B02 已验收语义传入项目连接/凭据/模型上下文——连接集合恒为已知，
  引用不存在连接报 error，目录读取失败按编排实际声明 fail-closed）；
- check_flow 的 errors（阻断级）逐条转成项目阻断项，单条消息同时包含属性定位
  （对象类型.属性）、编排标识（名称(id)）与具体原因（如「未绑定」）；
- 同一编排在一次 validate 内只检查一次（按引用编排 id 缓存）；warnings 不阻断；
- 未被引用的编排不检查、不阻断；
- 引用不存在的编排：既有文案保留「不存在」语义并补上编排定位；
- 发布一致性：project-publish 复用同一校验入口（projects.validate_project 即
  project_validation.validate_project 的兼容转发），修复自动生效。

纯 python3 标准库直跑；WIZ_WORKBENCH_ROOT 挂临时数据根，真实 ontology/ 只读不碰；
编排用 flows 模块在临时根内自建，不依赖真实编排数据。
运行：python3 tests/test_project_flow_binding_check.py
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
TMP = Path(tempfile.mkdtemp(prefix='wiz_flow_bind_check_'))
os.environ['WIZ_WORKBENCH_ROOT'] = str(TMP)

from workbench import flows  # noqa: E402  （临时根就位后再 import）
from workbench.project_validation import validate_project  # noqa: E402
from workbench import projects  # noqa: E402

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
        print('  实际: ' + json.dumps(actual, ensure_ascii=False, default=str)[:2400])
    shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(1)


def has(texts, fragment):
    return any(fragment in str(t) for t in texts)


# --- 夹具：引用版本 graph（deviceCode=string / maxPower=double / socAvg=double /
#     online=boolean / powerSeries=timeSeries） ---------------------------------------

GRAPH = [
    {'@id': 'mg:StorageDevice', '@type': 'owl:Class', 'rdfs:label': '储能设备'},
    {'@id': 'mg:deviceCode', '@type': 'owl:DatatypeProperty', 'rdfs:label': '设备编号',
     'rdfs:domain': {'@id': 'mg:StorageDevice'}, 'mg:apiName': 'deviceCode',
     'rdfs:range': {'@id': 'xsd:string'}},
    {'@id': 'mg:maxPower', '@type': 'owl:DatatypeProperty', 'rdfs:label': '最大功率',
     'rdfs:domain': {'@id': 'mg:StorageDevice'}, 'mg:apiName': 'maxPower',
     'rdfs:range': {'@id': 'xsd:double'}},
    {'@id': 'mg:socAvg', '@type': 'owl:DatatypeProperty', 'rdfs:label': '平均SOC',
     'rdfs:domain': {'@id': 'mg:StorageDevice'}, 'mg:apiName': 'socAvg',
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
    """项目状态夹具：单一对象绑定，primary_key 已配置。"""
    return {'projectId': 'p1', 'name': '编排配置项目', 'ontologyId': 'storage', 'ontologyVersion': '1.0.0',
            'connections': {'connections': [{'id': 'conn1', 'name': '主库', 'engine': 'mysql'}]},
            'bindings': {'notice': '', 'source_candidates': [],
                         'object_bindings': [{'object_type': 'StorageDevice', 'connection': 'conn1',
                                              'table': 'devices', 'primary_key': 'device_id',
                                              'properties': copy.deepcopy(props)}],
                         'actionBindings': []},
            'implementations': [], 'parameters': {}}


def make_flow(name, wired, outputs=()):
    """在临时根内创建编排。

    wired=True：接一个 Python 节点并绑定输出 → flows.check_flow 零 error 零 warning；
    wired=False：只声明输出（空壳）→ check_flow 报 OUTPUT_BINDING_MISSING error
    （输出未绑定来源节点输出）+「没有处理节点」warning。
    """
    created = flows.create(name)
    state = flows.read_draft(created['id'])
    state.pop('_draft')
    state['outputs'] = [dict(o, id=o.get('id') or 'out_' + o['name']) for o in outputs]
    if wired:
        state['nodes'] = [{'id': 'nd_impl', 'kind': 'python', 'name': '实现', 'inputs': [],
                           'outputs': [dict(o, id='nd_out_' + o['name']) for o in state['outputs']],
                           'implementation': {'language': 'python',
                                              'code': 'def main():\n    return None\n'}}]
        for out in state['outputs']:
            out['binding'] = {'kind': 'node', 'nodeId': 'nd_impl',
                              'outputId': 'nd_out_' + out['name']}
    flows.save_draft(state)
    return created['id']


def flow_src(flow_id, output):
    return {'kind': 'flow', 'flow': flow_id, 'output': output, 'inputs': {}}


def errs(props):
    return validate_project(proj(props), onto())['errors']


# 编排 B：空壳（输出未绑定来源）→ check_flow error OUTPUT_BINDING_MISSING
FLOW_BAD = make_flow('空壳未绑定编排', wired=False,
                     outputs=[{'name': 'power', 'label': '功率', 'type': {'type': 'number'}}])
# 编排 G：配置正确（Python 节点 + 输出绑定）→ check_flow 零 error
FLOW_OK = make_flow('合法编排', wired=True,
                    outputs=[{'name': 'power', 'label': '功率', 'type': {'type': 'number'}}])
# 编排 W：仅有 warning 无 error → 不阻断
# （20260921 合并口径：main 侧 B01/B02 已验收语义为「项目校验时连接集合恒为已知（含空），
# 引用不存在连接即 error」；本夹具改用项目内有效连接 conn1，另以「已声明输入参数未在 SQL 中引用」
# 制造纯 warning，保持「仅 warning 不阻断」的验证意图。）
FLOW_WARN = make_flow('仅警告编排', wired=False,
                      outputs=[{'name': 'power', 'label': '功率', 'type': {'type': 'number'}}])
_state = flows.read_draft(FLOW_WARN)
_state.pop('_draft')
_state['nodes'] = [{'id': 'nd_sql', 'kind': 'sql', 'name': '取数',
                    'inputs': [{'id': 'in_p', 'name': 'p_unused', 'label': '未用参数',
                                'type': {'type': 'number'},
                                'source': {'kind': 'fixed', 'valueType': 'number', 'value': 1}}],
                    'outputs': [{'id': 'nd_out_power', 'name': 'power', 'label': '功率',
                                 'type': {'type': 'number'}}],
                    'implementation': {'connectionId': 'conn1', 'sql': 'SELECT 1'}}]
_state['outputs'][0]['binding'] = {'kind': 'node', 'nodeId': 'nd_sql', 'outputId': 'nd_out_power'}
flows.save_draft(_state)
check(len(flows.check_flow(flows.read_draft(FLOW_WARN))['errors']) == 0
      and len(flows.check_flow(flows.read_draft(FLOW_WARN))['warnings']) > 0,
      '夹具自检：FLOW_WARN 应为「仅 warning 无 error」编排')
# 编排 U：同类空壳问题，但**不被**任何项目属性引用
FLOW_UNREF = make_flow('未被引用的空壳编排', wired=False,
                       outputs=[{'name': 'power', 'label': '功率', 'type': {'type': 'number'}}])

# 1) R02 反例：引用「输出未绑定」的空壳编排 → 阻断，单条消息含 属性定位+编排标识+未绑定原因 --
r02 = errs({'maxPower': flow_src(FLOW_BAD, 'out_power')})
check(has(r02, '属性来源 StorageDevice.maxPower：引用的编排 空壳未绑定编排(' + FLOW_BAD + ') 配置无效'),
      'R02 反例：空壳编排被引用必须阻断（消息含属性定位与编排标识）', r02)
check(has(r02, '尚未绑定来源节点输出'),
      '阻断消息携带未绑定原因（check_flow 错误原文）', r02)
check(any('maxPower' in str(e) and '尚未绑定来源' in str(e) for e in r02),
      '硬性约束：属性定位与未绑定原因出现在同一条消息里', r02)
check(len(r02) == 1, '结构核对本身无问题时空壳编排只产生 check_flow 这一条阻断', r02)

# 2) 引用不存在的编排 → 仍阻断（既有「不存在」语义 + 编排定位；属性定位在前缀） ----------------
GHOST = 'ghost0notfound'
missing = errs({'maxPower': flow_src(GHOST, 'out_power')})
check(has(missing, '不存在') and has(missing, '不存在或已删除（' + GHOST + '）'),
      '引用不存在的编排仍阻断：保留「不存在」语义并补上编排 id', missing)
check(any('StorageDevice.maxPower' in str(e) and GHOST in str(e) for e in missing),
      '不存在的编排：属性定位与编排定位在同一条消息里', missing)

# 3) 引用配置正确的编排 → 无该类阻断（验收记录反例的对照面） ---------------------------------
ok = errs({'deviceCode': dict(FIELD_CODE),
           'maxPower': flow_src(FLOW_OK, 'out_power')})
check(ok == [], '配置正确的编排被引用零阻断', ok)

# 4) 同一编排被两个属性引用 → check_flow 只执行一次（按引用编排 id 缓存） --------------------
_real_check_flow = flows.check_flow
_calls = []


def _counting_check_flow(state, *args, **kw):
    # 20260921 合并口径：main 侧 B01/B02 会以位置参数传入项目连接/模型/凭据上下文，
    # 包装器须原样透传（*args），计数目的不变。
    _calls.append(state.get('flowId') if isinstance(state, dict) else None)
    return _real_check_flow(state, *args, **kw)


flows.check_flow = _counting_check_flow
try:
    twice = validate_project(proj({'maxPower': flow_src(FLOW_BAD, 'out_power'),
                                   'socAvg': flow_src(FLOW_BAD, 'out_power')}), onto())
finally:
    flows.check_flow = _real_check_flow
check(_calls == [FLOW_BAD], '同一编排被两个属性引用时 check_flow 只执行一次（缓存命中）', _calls)
check(has(twice['errors'], 'StorageDevice.maxPower') and has(twice['errors'], 'StorageDevice.socAvg'),
      '两个引用属性各自得到阻断（属性定位可区分）', twice['errors'])

# 5) 未被引用的编排存在同类问题 → 不出现在 errors（只检查被引用的编排） ---------------------
unref = errs({'deviceCode': dict(FIELD_CODE),
              'maxPower': flow_src(FLOW_OK, 'out_power')})
check(not has(unref, FLOW_UNREF) and not has(unref, '配置无效'),
      '未被引用编排的同类问题不产生任何项目阻断', unref)

# 6) check_flow 只有 warnings（无 errors）→ 不阻断 ------------------------------------------
warn_only = errs({'maxPower': flow_src(FLOW_WARN, 'out_power')})
check(warn_only == [], '仅 warning 的编排（无 error）不阻断项目', warn_only)

# 7) 发布一致性：project-publish 复用同一校验入口（projects.validate_project 兼容转发） ------
check(projects.validate_project is validate_project,
      '发布路径（project_routes → projects.validate_project）与校验共用同一函数，修复自动生效')

# 8) 验收记录反例的直接演示（对照）：未绑定输出 → errors 阻断；正确编排 → 无阻断 -------------
blocked = validate_project(proj({'maxPower': flow_src(FLOW_BAD, 'out_power')}), onto())
clean = validate_project(proj({'maxPower': flow_src(FLOW_OK, 'out_power')}), onto())
print('\n--- 验收记录 R02 反例演示（直接函数调用） ---')
print('空壳编排被引用  errors=' + json.dumps(blocked['errors'], ensure_ascii=False))
print('合法编排被引用  errors=' + json.dumps(clean['errors'], ensure_ascii=False))
check(blocked['errors'] and not clean['errors'],
      'R02 反例闭环：未绑定输出编排阻断 / 正确编排放行')

shutil.rmtree(TMP, ignore_errors=True)
print(f'\n全部通过（{len(PASSED)} 步）：R02 修复符合验收语义（被引用编排配置有效性检查）。')
