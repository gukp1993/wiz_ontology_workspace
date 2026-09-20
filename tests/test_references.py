"""草稿悬空引用检查与保存边界（20260920 需求 11～13 / 验收意见第二轮 S1～S3）。

覆盖：
- 单元：broken_references 覆盖图内 domain/range/共享定义/值类型/取值函数/嵌套值类型、
  契约签名、计算/动作/接口三类定义的字段级引用、接口 properties/implementations、
  动作/规则关联、指标与旧 rules；
- 单元：比较键只由稳定 ID 组成——改显示名 key 不变、改目标 key 必变（S2）；
- 单元：旧 rules id 集修正——有效旧引用放行、真实删除阻断、历史失效重复保存放行（S3）；
- 边界：new_broken_references 只报「本次新引入」，历史遗留失效引用不阻断（需求 §6）；
- HTTP：POST /api/save 首次保存放行；删除被契约/接口引用的属性与对象类型 → 422
  BROKEN_REFERENCE 且零写入、revision 不推进（S1）；既有失效引用改名可保存、新增同名同文案
  但身份不同的失效引用仍阻断（S2）；「未填写完整」仍按 200 + errors 保存。

运行：python3 tests/test_references.py（隔离根 + 独立端口 18831）
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import auth_client
from pathlib import Path as _P
sys.path.insert(0, str(_P(__file__).resolve().parents[1]))

REPO = Path(__file__).resolve().parents[1]
PORT = 18831
BASE = f'http://127.0.0.1:{PORT}'
TMP = Path(tempfile.mkdtemp(prefix='wiz_references_'))
os.environ['WIZ_WORKBENCH_ROOT'] = str(TMP)
PROC = None
PASSED = []


def ok(step, desc):
    PASSED.append(step)
    print(f'通过 {step}) {desc}')


def fail(message, actual=None):
    print(f'\n[失败] {message}')
    if actual is not None:
        print('  实际: ' + json.dumps(actual, ensure_ascii=False, default=str)[:1500])
    shutdown()
    print(f'（临时根保留：{TMP}）')
    sys.exit(1)


def shutdown():
    global PROC
    if PROC is not None:
        PROC.terminate()
        try:
            PROC.wait(timeout=10)
        except subprocess.TimeoutExpired:
            PROC.kill()
        PROC = None


def clone(value):
    return json.loads(json.dumps(value))


def broken_entries(state):
    from workbench import references
    return references.broken_references(state)


def broken_messages(state):
    return [item['message'] for item in broken_entries(state)]


def broken_keys(state):
    return sorted(item['key'] for item in broken_entries(state))


def new_broken(previous, current):
    from workbench import references
    return references.new_broken_references(previous, current)


def graph_state(**over):
    """JSON-LD 形态草稿（保存层 decode_state 后的形态；悬空引用检查在这一层执行）。"""
    base = {
        'workspaceId': 'storage',
        'ontology': {'@context': {'mg': 'https://example.com/microgrid/'}, '@graph': [
            {'@id': 'mg:obj_a', '@type': 'owl:Class', 'rdfs:label': '对象A', 'rdfs:comment': 'd'},
            {'@id': 'mg:obj_b', '@type': 'owl:Class', 'rdfs:label': '对象B', 'rdfs:comment': 'd'},
            {'@id': 'mg:sp_1', '@type': 'mg:SharedProperty', 'rdfs:label': '共享1', 'rdfs:comment': 'd',
             'rdfs:range': {'@id': 'xsd:double'}, 'mg:valueSuffix': '', 'mg:visibility': 'normal'},
            {'@id': 'mg:p_1', '@type': 'owl:DatatypeProperty', 'rdfs:label': '共享1',
             'rdfs:domain': {'@id': 'mg:obj_a'}, 'mg:sharedProperty': {'@id': 'mg:sp_1'}, 'mg:apiName': 'p_1'},
            {'@id': 'mg:p_priv', '@type': 'owl:DatatypeProperty', 'rdfs:label': '私有1',
             'rdfs:domain': {'@id': 'mg:obj_a'}, 'rdfs:range': {'@id': 'xsd:string'}, 'mg:apiName': 'p_priv'},
            {'@id': 'mg:link_1', '@type': 'owl:ObjectProperty', 'rdfs:label': '所属于', 'mg:cardinality': 'many-to-one',
             'rdfs:domain': {'@id': 'mg:obj_a'}, 'rdfs:range': {'@id': 'mg:obj_b'}},
        ]},
        'workflow': {'objective': {'name': '引用测试本体'}, 'functions': [], 'actions': [], 'interfaces': [],
                     'businessRules': [], 'businessRuleAssociations': [], 'actionAssociations': []},
        'metrics': {'metrics': []}, 'rules': {'rules': []}, 'layout': {},
    }
    base.update(over)
    return base


def schema_state():
    """HTTP 保存用的 schema 形态（与下面 graph_state 内容等价）。"""
    return {
        'workspaceId': 'storage',
        'ontology': {'schemaVersion': 1, 'namespaces': {'mg': 'https://example.com/microgrid/'},
                     'objectTypes': [{'id': 'mg:obj_a', 'displayName': '对象A', 'description': 'd'},
                                     {'id': 'mg:obj_b', 'displayName': '对象B', 'description': 'd'}],
                     'linkTypes': [{'id': 'mg:link_1', 'displayName': '所属于', 'description': 'd',
                                    'sourceObjectTypeId': 'mg:obj_a', 'targetObjectTypeId': 'mg:obj_b',
                                    'cardinality': 'many-to-one'}],
                     'sharedProperties': [
                         {'id': 'mg:sp_1', 'displayName': '共享1', 'description': 'd',
                          'dataType': {'type': 'double'}, 'valueSuffix': '', 'visibility': 'normal'}],
                     'properties': [
                         {'id': 'mg:p_1', 'objectTypeId': 'mg:obj_a', 'sharedPropertyId': 'mg:sp_1', 'apiName': 'p_1'},
                         {'id': 'mg:p_priv', 'objectTypeId': 'mg:obj_a', 'displayName': '私有1',
                          'description': 'd', 'dataType': {'type': 'string'}, 'apiName': 'p_priv'}],
                     'valueTypes': [], 'metadata': [],
                     'definitionOrder': ['mg:obj_a', 'mg:obj_b', 'mg:link_1', 'mg:sp_1', 'mg:p_1', 'mg:p_priv']},
        'workflow': {'objective': {'name': '引用测试本体'}, 'functions': [], 'actions': [], 'interfaces': [],
                     'businessRules': [], 'businessRuleAssociations': [], 'actionAssociations': []},
        'metrics': {'metrics': []}, 'rules': {'rules': []}, 'layout': {},
    }


def drop_property(state, property_id):
    state = clone(state)
    state['ontology']['properties'] = [p for p in state['ontology']['properties'] if p['id'] != property_id]
    state['ontology']['definitionOrder'] = [i for i in state['ontology']['definitionOrder'] if i != property_id]
    return state


def drop_object_type(state, type_id):
    """删除对象类型及其相连的链接（保持 definitionOrder 完整，只让接口 implementations 悬空）。"""
    state = clone(state)
    dropped_links = [l['id'] for l in state['ontology']['linkTypes']
                     if l.get('sourceObjectTypeId') == type_id or l.get('targetObjectTypeId') == type_id]
    state['ontology']['objectTypes'] = [t for t in state['ontology']['objectTypes'] if t['id'] != type_id]
    state['ontology']['linkTypes'] = [l for l in state['ontology']['linkTypes'] if l['id'] not in dropped_links]
    state['ontology']['definitionOrder'] = [i for i in state['ontology']['definitionOrder']
                                            if i != type_id and i not in dropped_links]
    return state


def interface_record(**over):
    record = {'id': 'iface_1', 'name': '储能接口', 'description': '接口业务描述',
              'status': 'experimental', 'properties': [], 'implementations': []}
    record.update(over)
    return record


def contract_record(outputs, **over):
    record = {'id': 'fn1', 'name': '查询私有属性', 'guide_version': 3, 'outputs': outputs}
    record.update(over)
    return record


def check_unit():
    # 基线：无悬空
    s = graph_state()
    if broken_entries(s):
        fail('干净草稿不应有悬空引用', broken_entries(s))
    ok('单元①', '干净草稿零悬空')

    # 契约引用被删属性
    s2 = graph_state(workflow={'objective': {'name': 'x'},
                               'functions': [contract_record(outputs=[{'id': 'o1', 'name': 'y',
                                                                      'ref': {'kind': 'property', 'id': 'mg:p_priv'}}])],
                               'actions': [], 'interfaces': [], 'businessRules': [],
                               'businessRuleAssociations': [], 'actionAssociations': []})
    assert not broken_entries(s2), broken_entries(s2)
    s2['ontology']['@graph'] = [n for n in s2['ontology']['@graph'] if n['@id'] != 'mg:p_priv']
    errs = broken_messages(s2)
    if not any('mg:p_priv' in e for e in errs):
        fail('契约引用被删属性应报悬空', errs)
    ok('单元②', '契约签名引用悬空可检出')

    # 关联悬空
    s3 = graph_state()
    s3['workflow']['businessRuleAssociations'] = [{'objectTypeId': 'mg:missing', 'ruleId': 'rule_missing'}]
    s3['workflow']['actionAssociations'] = [{'objectTypeId': 'mg:obj_a', 'actionId': 'act_missing'}]
    errs3 = broken_messages(s3)
    if len(errs3) < 3:
        fail('对象/规则/动作关联悬空都应检出', errs3)
    ok('单元③', '对象/规则/动作关联悬空可检出')

    # 值类型悬空
    s4 = graph_state()
    s4['ontology']['@graph'][2]['mg:valueType'] = {'@id': 'mg:vt_missing'}
    errs4 = broken_messages(s4)
    if not any('值类型' in e for e in errs4):
        fail('值类型引用悬空应检出', errs4)
    ok('单元④', '值类型引用悬空可检出')

    # new_broken_references：历史遗留放行、新引入阻断
    # ① 历史遗留：草稿本身带一条失效规则关联（此前就坏），同内容/无关修改不阻断
    legacy = graph_state()
    legacy['workflow']['businessRuleAssociations'] = [{'objectTypeId': 'mg:missing', 'ruleId': 'rule_missing'}]
    newly, allowed = new_broken(legacy, legacy)
    if not allowed or newly:
        fail('历史遗留失效引用不应阻断同内容保存', (newly, allowed))
    tweaked = clone(legacy)
    tweaked['ontology']['@graph'][0]['rdfs:comment'] = '无关注释修改'
    newly_ok, allowed_ok = new_broken(legacy, tweaked)
    if not allowed_ok or newly_ok:
        fail('历史遗留失效下做无关修改仍应可保存', (newly_ok, allowed_ok))
    # ② 新引入：干净基线 → 删除被契约引用的属性
    clean = graph_state()
    clean['workflow']['functions'] = [contract_record(outputs=[{'id': 'o1', 'name': 'y',
                                                                'ref': {'kind': 'property', 'id': 'mg:p_priv'}}])]
    if broken_entries(clean):
        fail('带契约引用的干净草稿不应有悬空', broken_entries(clean))
    broken = clone(clean)
    broken['ontology']['@graph'] = [n for n in broken['ontology']['@graph'] if n['@id'] != 'mg:p_priv']
    newly2, allowed2 = new_broken(clean, broken)
    if allowed2 or not newly2:
        fail('新引入悬空应阻断', (newly2, allowed2))
    if not any('mg:p_priv' in e for e in newly2):
        fail('新引入错误需含具体引用 id', newly2)
    ok('单元⑤', 'new_broken_references：历史遗留放行 / 新引入阻断')

    # 接口 properties / implementations 与同类图内引用（S1 覆盖）
    s6 = graph_state()
    s6['workflow']['interfaces'] = [interface_record(properties=['mg:sp_1', 'mg:p_priv'], implementations=['obj_missing'])]
    s6['ontology']['@graph'][3]['mg:valueSource'] = {'@type': '@json',
                                                     '@value': {'kind': 'function', 'functionId': 'fn_missing'}}
    s6['ontology']['@graph'][2]['mg:constraint'] = {'@type': '@json', '@value': {
        'kind': 'struct', 'fields': [{'name': 'f1', 'valueType': 'mg:vt_missing'}]}}
    errs6 = broken_messages(s6)
    if not any('接口' in e and '实现对象类型' in e for e in errs6):
        fail('接口 implementations 悬空应检出', errs6)
    if not any('取值函数' in e for e in errs6):
        fail('取值函数引用悬空应检出', errs6)
    if not any('结构体字段' in e for e in errs6):
        fail('嵌套值类型引用悬空应检出', errs6)
    if len(errs6) != 3:
        fail('上述三项之外不应有其他悬空', errs6)
    ok('单元⑥', '接口 implementations、取值函数、嵌套值类型悬空可检出')

    # 接口 properties：目标属性（共享或对象属性）被删即悬空
    s7 = graph_state()
    s7['workflow']['interfaces'] = [interface_record(properties=['mg:sp_1', 'mg:p_priv'], implementations=['obj_b'])]
    if broken_messages(s7):
        fail('接口引用存在的共享/对象属性不应报悬空', broken_messages(s7))
    dropped = clone(s7)
    dropped['ontology']['@graph'] = [n for n in dropped['ontology']['@graph']
                                     if n['@id'] not in ('mg:sp_1', 'mg:p_priv')]
    errs7 = broken_messages(dropped)
    for target in ('mg:sp_1', 'mg:p_priv'):
        if not any('接口' in e and target in e for e in errs7):
            fail(f'接口 properties 引用的 {target} 被删应报悬空', errs7)
    ok('单元⑦', '接口 properties 引用的共享/对象属性被删可检出')

    # 同一批字段在动作/接口上的覆盖（前端 graphReferenceEntries 对三类定义统一遍历）：
    # 签名槽位、顶层 function_ref、properties/implementations 清单、base 引用带 id。
    s7b = graph_state()
    s7b['workflow']['actions'] = [
        {'id': 'act_1', 'name': '动作1', 'object_type': 'mg:obj_a', 'function_ref': 'fn_missing',
         'outputs': [{'id': 'o1', 'name': '结果', 'ref': {'kind': 'property', 'id': 'mg:ghost_prop'}}]},
    ]
    s7b['workflow']['interfaces'] = [
        {'id': 'if_2', 'name': '接口2', 'guide_version': 3,
         'inputs': [{'id': 'i1', 'name': '来源', 'ref': {'kind': 'object', 'id': 'mg:ghost_obj'}}],
         'properties': ['mg:ghost_shared'], 'implementations': ['ghost_type']},
    ]
    s7b['workflow']['functions'] = [
        {'id': 'fn_base', 'name': '函数B', 'guide_version': 3,
         'outputs': [{'id': 'ob', 'name': '数组', 'ref': {'kind': 'base', 'dataType': 'array', 'id': 'mg:vt_gone'}}]},
        {'id': 'fn_base_ok', 'name': '函数C', 'guide_version': 3,
         'outputs': [{'id': 'oc', 'name': '数值', 'ref': {'kind': 'base', 'dataType': 'double'}}]},
        {'id': 'fn_pref', 'name': '函数D', 'guide_version': 3,
         'outputs': [{'id': 'od', 'name': '属性', 'ref': {'kind': 'property', 'id': 'p_priv'}}]},
    ]
    errs7b = broken_messages(s7b)
    for fragment in ('计算契约不存在（fn_missing）', '引用不存在的属性（mg:ghost_prop）',
                     '引用不存在的对象类型（mg:ghost_obj）', '要求的共享属性不存在（mg:ghost_shared）',
                     '引用的实现对象类型不存在（ghost_type）', '引用不存在的内容（mg:vt_gone）'):
        if not any(fragment in e for e in errs7b):
            fail(f'新增覆盖字段应报悬空：{fragment}', errs7b)
    if len(errs7b) != 6:
        fail('新增覆盖字段应恰好检出六条悬空', errs7b)
    # 不带 id 的 base 引用与省 mg: 前缀写法（mg:p_priv）不报；清单字段写成非数组时按空处理
    s7c = graph_state()
    s7c['workflow']['interfaces'] = [interface_record(properties={'x': 'y'}, implementations='not-a-list')]
    s7c['workflow']['functions'] = [{'id': 'fn_c', 'name': '函数C', 'guide_version': 3,
                                     'outputs': [{'id': 'o1', 'name': 'y', 'ref': {'kind': 'base', 'dataType': 'double'}}]}]
    if broken_messages(s7c):
        fail('base 无 id / 清单非数组不应误报', broken_messages(s7c))
    ok('单元⑦b', '动作/接口签名槽位、顶层 function_ref、清单字段、base 引用可检出且不误报')

    # 比较键：改显示名不变、改目标必变（S2）
    base = graph_state()
    base['workflow']['functions'] = [contract_record(outputs=[{'id': 'o1', 'name': 'y',
                                                               'ref': {'kind': 'property', 'id': 'mg:ghost'}}])]
    base['workflow']['interfaces'] = [interface_record(properties=['mg:sp_missing'], implementations=['obj_missing'])]
    base_keys = broken_keys(base)
    if len(base_keys) != 3:
        fail('样例应产生三条悬空条目', base_keys)
    renamed = clone(base)
    renamed['workflow']['functions'][0]['name'] = '查询（改名）'
    renamed['workflow']['functions'][0]['outputs'][0]['name'] = '输出改名'
    renamed['workflow']['interfaces'][0]['name'] = '储能接口（改名）'
    renamed['ontology']['@graph'][0]['rdfs:label'] = '对象A（改名）'
    if broken_keys(renamed) != base_keys:
        fail('仅改显示名不应改变比较键', (base_keys, broken_keys(renamed)))
    if broken_messages(renamed) == broken_messages(base):
        fail('展示文案应随业务名称变化（证明改名确实改了文案）', broken_messages(renamed))
    moved = clone(base)
    moved['workflow']['functions'][0]['outputs'][0]['ref'] = {'kind': 'property', 'id': 'mg:ghost_2'}
    if broken_keys(moved) == base_keys:
        fail('改目标 id 必须改变比较键', (base_keys, broken_keys(moved)))
    newly_renamed, allowed_renamed = new_broken(base, renamed)
    if not allowed_renamed or newly_renamed:
        fail('仅改名称的历史失效引用不应阻断', (newly_renamed, allowed_renamed))
    ok('单元⑧', '比较键用稳定 ID：改名称不变、改目标必变')

    # 旧 rules 引用（S3）：有效放行 / 真实删除阻断 / 历史失效重复保存放行
    s8 = graph_state()
    s8['rules'] = {'rules': [{'id': 'r1', 'name': '旧规则', 'member_type': 'mg:obj_a',
                              'membership_relations': ['mg:link_1']}]}
    s8['metrics'] = {'metrics': [{'id': 'm1', 'name': '指标', 'rule_ref': 'r1',
                                  'applicable_types': ['mg:obj_a']}]}
    if broken_messages(s8):
        fail('指标引用存在的旧规则不应报悬空', broken_messages(s8))
    deleted = clone(s8)
    deleted['rules'] = {'rules': []}
    errs8 = broken_messages(deleted)
    if not any('规则' in e and 'r1' in e for e in errs8):
        fail('旧规则被删除后指标引用应报悬空', errs8)
    _, allowed_valid = new_broken(s8, s8)
    if not allowed_valid:
        fail('有效旧引用状态自身不应阻断', allowed_valid)
    newly8, allowed_deleted = new_broken(s8, deleted)
    if allowed_deleted or not newly8:
        fail('删除旧规则应阻断', (newly8, allowed_deleted))
    newly_again, allowed_again = new_broken(deleted, deleted)
    if not allowed_again or newly_again:
        fail('历史失效的旧规则引用应可继续保存', (newly_again, allowed_again))
    dangling_relation = clone(s8)
    dangling_relation['rules']['rules'][0]['membership_relations'] = ['mg:link_missing']
    if not any('成员关系' in e for e in broken_messages(dangling_relation)):
        fail('旧规则成员关系悬空应检出', broken_messages(dangling_relation))
    ok('单元⑨', '旧 rules 引用：有效放行 / 真实删除阻断 / 历史失效放行')
    return True


def request(method, path, payload=None, jar=None):
    data = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    headers = {'Content-Type': 'application/json', 'Origin': BASE}
    if jar:
        headers['Cookie'] = 'wiz_session=' + jar
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode() or '{}')
    except urllib.error.HTTPError as exc:
        body = exc.read().decode()
        try:
            return exc.code, json.loads(body or '{}')
        except ValueError:
            return exc.code, body


def save_ok(state, token, cookie, label):
    code, r = request('POST', '/api/save', {'state': state, 'revision': token}, jar=cookie)
    if code != 200:
        fail(f'{label} 应保存成功', (code, r))
    return r['revision']


def save_blocked(state, token, cookie, label, contains):
    code, r = request('POST', '/api/save', {'state': state, 'revision': token}, jar=cookie)
    if code != 422 or r.get('code') != 'BROKEN_REFERENCE':
        fail(f'{label} 应 422 BROKEN_REFERENCE', (code, r))
    errors = r.get('errors') or []
    if not any(contains in e for e in errors):
        fail(f'{label} 响应需列出具体引用（含 {contains}）', errors)
    return errors


def state_now(ontology_id, cookie):
    code, st = request('GET', f'/api/state?ontology={ontology_id}', jar=cookie)
    if code != 200:
        fail('读取草稿应成功', (code, st))
    return st


def expect_unchanged(ontology_id, cookie, token, label, property_id=None, type_id=None):
    after = state_now(ontology_id, cookie)
    if after['revision'] != token:
        fail(f'{label} 阻断后 revision 不应推进', (token, after['revision']))
    if property_id:
        ids = [p['id'] for p in after['state']['ontology']['properties']]
        if property_id not in ids:
            fail(f'{label} 阻断后不应写入（属性仍在）', ids)
    if type_id:
        ids = [t['id'] for t in after['state']['ontology']['objectTypes']]
        if type_id not in ids:
            fail(f'{label} 阻断后不应写入（对象类型仍在）', ids)


def main():
    global PROC
    os.environ['WIZ_DATABASE_URL'] = 'sqlite:///' + str(TMP / 'data' / 'workbench.sqlite3')
    check_unit()
    env = dict(os.environ)
    env['WIZ_WORKBENCH_ROOT'] = str(TMP)
    env['WIZ_WORKBENCH_PORT'] = str(PORT)
    log = open(TMP / 'server.log', 'w')
    PROC = subprocess.Popen([sys.executable, '-m', 'workbench.server'], cwd=str(REPO), env=env,
                            stdout=log, stderr=subprocess.STDOUT)
    auth_client.wait_ready(BASE)
    _, cookie = auth_client.register_or_login(BASE, 'refuser', 'test1234')

    # ① 新建本体后首次保存：允许（清空基线不可比的情形用新建本体复现）
    code, created = request('POST', '/api/ontologies', {'name': '引用测试本体'}, jar=cookie)
    if code not in (200, 201):
        fail('新建本体应成功', (code, created))
    ontology_id = created['id'] if isinstance(created, dict) else created
    st = state_now(ontology_id, cookie)
    token = st['revision']
    s = schema_state()
    s['workspaceId'] = ontology_id
    s['workflow']['objective']['name'] = '引用测试本体'
    token = save_ok(s, token, cookie, '首次保存')
    ok('HTTP①', '首次保存放行（无基线可比）')

    # ② 正常增量保存
    s['ontology']['objectTypes'][0]['description'] = '改过的定义'
    token = save_ok(s, token, cookie, '普通修改')
    ok('HTTP②', '普通修改保存成功')

    # ③ 把被契约引用的属性删掉 → 422 且未写入
    with_contract = clone(s)
    with_contract['workflow']['functions'] = [contract_record(outputs=[{'id': 'o1', 'name': 'y',
                                                                        'ref': {'kind': 'property', 'id': 'mg:p_priv'}}])]
    token = save_ok(with_contract, token, cookie, '带契约引用的草稿（引用有效）')
    save_blocked(drop_property(with_contract, 'mg:p_priv'), token, cookie,
                 '删除被契约引用的属性', 'mg:p_priv')
    expect_unchanged(ontology_id, cookie, token, '删除被契约引用的属性', property_id='mg:p_priv')
    ok('HTTP③', '删除契约有效引用 → 422 且零写入、revision 不变')

    # ④ S1 复现：接口 properties 引用属性 → 删除该属性 → 422 且未写入
    with_iface = clone(with_contract)
    with_iface['workflow']['interfaces'] = [interface_record(properties=['mg:p_1', 'mg:p_priv'])]
    token = save_ok(with_iface, token, cookie, '接口 properties 引用属性的草稿')
    # S1 原始复现：先引用共享支持的属性 mg:p_1，删除后应 422（修复前实测 200 并写入悬空）
    save_blocked(drop_property(with_iface, 'mg:p_1'), token, cookie,
                 '删除接口 properties 引用的共享支持属性',
                 '接口定义「储能接口」要求的共享属性不存在（mg:p_1）')
    expect_unchanged(ontology_id, cookie, token, '删除接口引用的共享支持属性', property_id='mg:p_1')
    save_blocked(drop_property(with_iface, 'mg:p_priv'), token, cookie,
                 '删除被接口 properties 引用的属性', '接口定义「储能接口」要求的共享属性不存在（mg:p_priv）')
    expect_unchanged(ontology_id, cookie, token, '删除被接口引用的属性', property_id='mg:p_priv')
    ok('HTTP④', '删除接口引用属性（含共享支持属性）→ 422 且零写入、revision 不变（S1）')

    # ⑤ 同类覆盖：接口 implementations 引用对象类型 → 删除该对象类型 → 422 且未写入
    with_impl = clone(with_iface)
    with_impl['workflow']['interfaces'] = [interface_record(properties=['mg:p_priv'], implementations=['obj_b'])]
    token = save_ok(with_impl, token, cookie, '接口 implementations 引用对象类型的草稿')
    save_blocked(drop_object_type(with_impl, 'mg:obj_b'), token, cookie,
                 '删除被接口 implementations 引用的对象类型',
                 '接口定义「储能接口」引用的实现对象类型不存在（obj_b）')
    expect_unchanged(ontology_id, cookie, token, '删除被接口引用的对象类型', type_id='mg:obj_b')
    ok('HTTP⑤', '删除接口实现对象类型 → 422 且零写入、revision 不变')

    # ⑥ 历史遗留失效引用可继续保存：先在隔离库直接种入「带失效引用」的草稿（模拟历史遗留），
    #    再做无关修改提交——此时失效引用在基线里已存在，不应阻断（需求 §6 不锁死草稿）。
    from workbench import workspaces as ws_mod
    from workbench import auth as auth_mod
    def seed_legacy():
        saved = dict(os.environ)
        os.environ['WIZ_DATABASE_URL'] = 'sqlite:///' + str(TMP / 'data' / 'workbench.sqlite3')
        auth_client.bind_fixture_user('refuser', 'test1234')
        uid2 = auth_client.user_id_from_db(TMP, 'refuser')
        auth_mod.bind_request({'userId': uid2})
        try:
            cur_state = ws_mod.read_draft(ontology_id)
            cur_state['workflow']['functions'] = [{'id': 'fn_legacy', 'name': '历史契约', 'guide_version': 3,
                                                   'outputs': [{'id': 'o1', 'name': 'y',
                                                                'ref': {'kind': 'property', 'id': 'mg:never_existed'}}]}]
            ws_mod.write_draft(cur_state)
        finally:
            os.environ.clear(); os.environ.update(saved)
    seed_legacy()
    after2 = state_now(ontology_id, cookie)
    cur = clone(after2['state'])
    cur['ontology']['objectTypes'][0]['description'] = '历史遗留下做无关修改'
    token = save_ok(cur, after2['revision'], cookie, '历史遗留失效引用下的无关修改')
    ok('HTTP⑥', '历史遗留失效引用仍可保存（不锁死草稿）')

    # ⑦ S2：比较键用稳定 ID 而非展示文案——既有失效引用改名可保存，新增同名同文案的新失效仍阻断
    renamed = clone(cur)
    renamed['workflow']['functions'][0]['name'] = '历史契约（改名后）'
    token = save_ok(renamed, token, cookie, '既有失效引用仅改名称')
    added = clone(renamed)
    added['workflow']['functions'][0]['outputs'] = added['workflow']['functions'][0]['outputs'] + [
        {'id': 'o2', 'name': 'y', 'ref': {'kind': 'property', 'id': 'mg:never_existed_2'}}]
    save_blocked(added, token, cookie, '新增同名但不同目标的失效引用', 'mg:never_existed_2')
    dup = clone(renamed)
    dup['workflow']['functions'][0]['outputs'] = dup['workflow']['functions'][0]['outputs'] + [
        {'id': 'o3', 'name': 'y', 'ref': {'kind': 'property', 'id': 'mg:never_existed'}}]
    save_blocked(dup, token, cookie, '新增同目标同文案但不同槽位的失效引用', 'mg:never_existed')
    expect_unchanged(ontology_id, cookie, token, '新增失效引用')
    after3 = state_now(ontology_id, cookie)
    if [o['id'] for o in after3['state']['workflow']['functions'][0]['outputs']] != ['o1']:
        fail('阻断后不应写入新增输出槽位', after3['state']['workflow']['functions'][0]['outputs'])
    still = clone(renamed)
    still['ontology']['objectTypes'][0]['description'] = '改名后继续做无关修改'
    token = save_ok(still, token, cookie, '改名后的历史失效引用继续保存')
    ok('HTTP⑦', '既有失效改名可保存 / 同名同文案的新失效仍 422（S2）')

    # ⑧ 未填写完整仍按 200 + errors
    incomplete = clone(still)
    incomplete['ontology']['objectTypes'][0]['displayName'] = ''
    code, r = request('POST', '/api/save', {'state': incomplete, 'revision': token}, jar=cookie)
    if code != 200 or not r.get('errors'):
        fail('未填写完整应按 200 + errors 保存', (code, r))
    ok('HTTP⑧', '未填写完整仍允许保存（errors 提示）')

    print(f'\n全部 {len(PASSED)} 步通过')
    shutdown()
    shutil.rmtree(TMP, ignore_errors=True)
    print('临时根已清理')


if __name__ == '__main__':
    try:
        main()
    finally:
        shutdown()
