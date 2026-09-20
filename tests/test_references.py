"""草稿悬空引用检查与保存边界（20260920 需求 11～13 / 验收意见「保存边界」）。

覆盖：
- 单元：broken_references 覆盖六类引用（domain/range、契约签名、动作/规则关联、值类型、指标）；
- 边界：new_broken_references 只报「本次新引入」，历史遗留失效引用不阻断（需求 §6）；
- HTTP：POST /api/save 首次保存放行；把有效引用改坏 → 422 BROKEN_REFERENCE 且不写入；
  历史遗留失效草稿可继续保存；「未填写完整」（缺名称）仍按 200 + errors 保存。

运行：python3 tests/test_references.py（隔离根 + 独立端口）
"""
import json
import os
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


def graph_state(**over):
    """JSON-LD 形态草稿（保存层 decode_state 后的形态；悬空引用检查在这一层执行）。"""
    base = {
        'workspaceId': 'storage',
        'ontology': {'@context': {'mg': 'https://example.com/microgrid/'}, '@graph': [
            {'@id': 'mg:obj_a', '@type': 'owl:Class', 'rdfs:label': '对象A', 'rdfs:comment': 'd'},
            {'@id': 'mg:sp_1', '@type': 'mg:SharedProperty', 'rdfs:label': '共享1', 'rdfs:comment': 'd',
             'rdfs:range': {'@id': 'xsd:double'}, 'mg:valueSuffix': '', 'mg:visibility': 'normal'},
            {'@id': 'mg:p_1', '@type': 'owl:DatatypeProperty', 'rdfs:label': '共享1',
             'rdfs:domain': {'@id': 'mg:obj_a'}, 'mg:sharedProperty': {'@id': 'mg:sp_1'}, 'mg:apiName': 'p_1'},
            {'@id': 'mg:p_priv', '@type': 'owl:DatatypeProperty', 'rdfs:label': '私有1',
             'rdfs:domain': {'@id': 'mg:obj_a'}, 'rdfs:range': {'@id': 'xsd:string'}, 'mg:apiName': 'p_priv'},
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
                     'objectTypes': [{'id': 'mg:obj_a', 'displayName': '对象A', 'description': 'd'}],
                     'linkTypes': [], 'sharedProperties': [
                         {'id': 'mg:sp_1', 'displayName': '共享1', 'description': 'd',
                          'dataType': {'type': 'double'}, 'valueSuffix': '', 'visibility': 'normal'}],
                     'properties': [
                         {'id': 'mg:p_1', 'objectTypeId': 'mg:obj_a', 'sharedPropertyId': 'mg:sp_1', 'apiName': 'p_1'},
                         {'id': 'mg:p_priv', 'objectTypeId': 'mg:obj_a', 'displayName': '私有1',
                          'description': 'd', 'dataType': {'type': 'string'}, 'apiName': 'p_priv'}],
                     'valueTypes': [], 'metadata': [],
                     'definitionOrder': ['mg:obj_a', 'mg:sp_1', 'mg:p_1', 'mg:p_priv']},
        'workflow': {'objective': {'name': '引用测试本体'}, 'functions': [], 'actions': [], 'interfaces': [],
                     'businessRules': [], 'businessRuleAssociations': [], 'actionAssociations': []},
        'metrics': {'metrics': []}, 'rules': {'rules': []}, 'layout': {},
    }


def check_unit():
    from workbench import references
    # 基线：无悬空
    s = graph_state()
    if references.broken_references(s):
        fail('干净草稿不应有悬空引用', references.broken_references(s))
    ok('单元①', '干净草稿零悬空')

    # 契约引用被删属性
    s2 = graph_state(workflow={'objective': {'name': 'x'},
                               'functions': [{'id': 'fn1', 'name': '查询', 'guide_version': 3,
                                              'outputs': [{'id': 'o1', 'name': 'y',
                                                           'ref': {'kind': 'property', 'id': 'mg:p_priv'}}]}],
                               'actions': [], 'interfaces': [], 'businessRules': [],
                               'businessRuleAssociations': [], 'actionAssociations': []})
    assert not references.broken_references(s2), references.broken_references(s2)
    s2['ontology']['@graph'] = [n for n in s2['ontology']['@graph'] if n['@id'] != 'mg:p_priv']
    errs = references.broken_references(s2)
    if not any('mg:p_priv' in e for e in errs):
        fail('契约引用被删属性应报悬空', errs)
    ok('单元②', '契约签名引用悬空可检出')

    # 关联悬空
    s3 = graph_state()
    s3['workflow']['businessRuleAssociations'] = [{'objectTypeId': 'mg:missing', 'ruleId': 'rule_missing'}]
    s3['workflow']['actionAssociations'] = [{'objectTypeId': 'mg:obj_a', 'actionId': 'act_missing'}]
    errs3 = references.broken_references(s3)
    if len(errs3) < 3:
        fail('对象/规则/动作关联悬空都应检出', errs3)
    ok('单元③', '对象/规则/动作关联悬空可检出')

    # 值类型悬空
    s4 = graph_state()
    s4['ontology']['@graph'][1]['mg:valueType'] = {'@id': 'mg:vt_missing'}
    errs4 = references.broken_references(s4)
    if not any('值类型' in e for e in errs4):
        fail('值类型引用悬空应检出', errs4)
    ok('单元④', '值类型引用悬空可检出')

    # new_broken_references：历史遗留放行、新引入阻断
    # ① 历史遗留：草稿本身带一条失效规则关联（此前就坏），同内容/无关修改不阻断
    legacy = graph_state()
    legacy['workflow']['businessRuleAssociations'] = [{'objectTypeId': 'mg:missing', 'ruleId': 'rule_missing'}]
    newly, allowed = references.new_broken_references(legacy, legacy)
    if not allowed or newly:
        fail('历史遗留失效引用不应阻断同内容保存', (newly, allowed))
    tweaked = json.loads(json.dumps(legacy))
    tweaked['ontology']['@graph'][0]['rdfs:comment'] = '无关注释修改'
    newly_ok, allowed_ok = references.new_broken_references(legacy, tweaked)
    if not allowed_ok or newly_ok:
        fail('历史遗留失效下做无关修改仍应可保存', (newly_ok, allowed_ok))
    # ② 新引入：干净基线 → 删除被契约引用的属性
    clean = graph_state()
    clean['workflow']['functions'] = [{'id': 'fn1', 'name': '查询', 'guide_version': 3,
                                       'outputs': [{'id': 'o1', 'name': 'y',
                                                    'ref': {'kind': 'property', 'id': 'mg:p_priv'}}]}]
    if references.broken_references(clean):
        fail('带契约引用的干净草稿不应有悬空', references.broken_references(clean))
    broken = json.loads(json.dumps(clean))
    broken['ontology']['@graph'] = [n for n in broken['ontology']['@graph'] if n['@id'] != 'mg:p_priv']
    newly2, allowed2 = references.new_broken_references(clean, broken)
    if allowed2 or not newly2:
        fail('新引入悬空应阻断', (newly2, allowed2))
    if not any('mg:p_priv' in e for e in newly2):
        fail('新引入错误需含具体引用 id', newly2)
    ok('单元⑤', 'new_broken_references：历史遗留放行 / 新引入阻断')
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
    code, st = request('GET', f'/api/state?ontology={ontology_id}', jar=cookie)
    if code != 200:
        fail('读取新本体草稿应成功', (code, st))
    token = st['revision']
    s = schema_state()
    s['workspaceId'] = ontology_id
    s['workflow']['objective']['name'] = '引用测试本体'
    code, r = request('POST', '/api/save', {'state': s, 'revision': token}, jar=cookie)
    if code != 200:
        fail('首次保存应成功', (code, r))
    token = r['revision']
    ok('HTTP①', '首次保存放行（无基线可比）')

    # ② 正常增量保存
    s['ontology']['objectTypes'][0]['description'] = '改过的定义'
    code, r = request('POST', '/api/save', {'state': s, 'revision': token}, jar=cookie)
    if code != 200:
        fail('普通修改应保存成功', (code, r))
    token = r['revision']
    ok('HTTP②', '普通修改保存成功')

    # ③ 把被契约引用的属性删掉 → 422 且未写入
    # ③ 先用「带契约引用」的合法草稿建立基线，再删除被引用的属性 → 阻断
    with_contract = json.loads(json.dumps(s))
    with_contract['workflow']['functions'] = [{'id': 'fn1', 'name': '查询私有属性', 'guide_version': 3,
                                               'outputs': [{'id': 'o1', 'name': 'y',
                                                            'ref': {'kind': 'property', 'id': 'mg:p_priv'}}]}]
    code, r = request('POST', '/api/save', {'state': with_contract, 'revision': token}, jar=cookie)
    if code != 200:
        fail('带契约引用的草稿应可保存（引用有效）', (code, r))
    token = r['revision']
    broken = json.loads(json.dumps(with_contract))
    broken['ontology']['properties'] = [p for p in broken['ontology']['properties'] if p['id'] != 'mg:p_priv']
    broken['ontology']['definitionOrder'] = [i for i in broken['ontology']['definitionOrder'] if i != 'mg:p_priv']
    code, r = request('POST', '/api/save', {'state': broken, 'revision': token}, jar=cookie)
    if code != 422 or r.get('code') != 'BROKEN_REFERENCE':
        fail('删除被契约引用的属性应 422 BROKEN_REFERENCE', (code, r))
    if not any('mg:p_priv' in e for e in r.get('errors') or []):
        fail('响应需列出具体引用', r)
    code, after = request('GET', f'/api/state?ontology={ontology_id}', jar=cookie)
    ids = [p['id'] for p in after['state']['ontology']['properties']]
    if 'mg:p_priv' not in ids:
        fail('阻断后不应写入（属性仍在）', ids)
    if after['revision'] != token:
        fail('阻断后 revision 不应推进', (token, after['revision']))
    ok('HTTP③', '删除有效引用 → 422 且零写入、revision 不变')

    # ④ 历史遗留失效引用可继续保存：先在隔离库直接种入「带失效引用」的草稿（模拟历史遗留），
    #    再做无关修改提交——此时失效引用在基线里已存在，不应阻断（需求 §6 不锁死草稿）。
    from workbench import workspaces as ws_mod
    import sqlalchemy as sa
    from workbench.storage import assets as store_mod
    from workbench.storage.engine import write_tx
    def seed_legacy():
        saved = dict(os.environ)
        os.environ['WIZ_DATABASE_URL'] = 'sqlite:///' + str(TMP / 'data' / 'workbench.sqlite3')
        auth_client.bind_fixture_user('refuser', 'test1234')
        from workbench import auth as auth_mod
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
    code, after2 = request('GET', f'/api/state?ontology={ontology_id}', jar=cookie)
    cur = json.loads(json.dumps(after2['state']))
    cur['ontology']['objectTypes'][0]['description'] = '历史遗留下做无关修改'
    code, r = request('POST', '/api/save', {'state': cur, 'revision': after2['revision']}, jar=cookie)
    if code != 200:
        fail('历史遗留失效引用不应阻断无关修改的保存', (code, r))
    ok('HTTP④', '历史遗留失效引用仍可保存（不锁死草稿）')

    # ⑤ 未填写完整仍按 200 + errors
    incomplete = json.loads(json.dumps(cur))
    incomplete['ontology']['objectTypes'][0]['displayName'] = ''
    code, r = request('POST', '/api/save', {'state': incomplete, 'revision': r['revision']}, jar=cookie)
    if code != 200 or not r.get('errors'):
        fail('未填写完整应按 200 + errors 保存', (code, r))
    ok('HTTP⑤', '未填写完整仍允许保存（errors 提示）')

    print(f'\n全部 {len(PASSED)} 步通过')
    shutdown()


if __name__ == '__main__':
    try:
        main()
    finally:
        shutdown()
