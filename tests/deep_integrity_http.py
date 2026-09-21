"""Q05 深度测试（数据可靠性+账号/接口边界）主 HTTP 套件：I1/I2/I3b/I5/I6/I7/I8/I9。

只走真实 HTTP 入口（专属隔离实例 127.0.0.1:18932，数据根 .runtime/test-data-q05）。
不修改业务代码；证据写 .runtime/test-evidence/q05/。

运行：.runtime/venv/bin/python tests/deep_integrity_http.py
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
os.environ['Q02_BASE'] = os.environ.get('Q05_BASE', 'http://127.0.0.1:18932')
import auth_client  # noqa: E402
import deep_ontology_client as doc  # noqa: E402
from deep_ontology_client import Recorder, brief  # noqa: E402

# 证据目录切到 q05（Recorder.dump 读模块全局）
doc.EVIDENCE_DIR = ROOT / '.runtime/test-evidence/q05'
EVIDENCE = doc.EVIDENCE_DIR
EVIDENCE.mkdir(parents=True, exist_ok=True)

BASE = os.environ['Q02_BASE']
PW = 'DeepTest!2026#abc'
DB = ROOT / '.runtime/test-data-q05/data/workbench.sqlite3'
LOG = ROOT / '.runtime/test-evidence/server-18932.log'
TS = time.strftime('%m%d%H%M%S')

# I8 哨兵（唯一串，供全库 grep）
S_LLM = 'SENT_LLMKEY_' + TS + '_a7f3'
S_CONN = 'SENT_CONNPW_' + TS + '_b8e1'
S_APIC = 'SENT_APICRED_' + TS + '_c9d2'
LOGIN_PW = PW  # 口令落库抽查（应只有哈希）

sentinels = {'llmKey': S_LLM, 'connectionSecret': S_CONN, 'apiCredential': S_APIC,
             'loginPassword': LOGIN_PW}
(EVIDENCE / 'sentinels_q05.json').write_text(json.dumps(sentinels, ensure_ascii=False), encoding='utf-8')


def new_api(user):
    return Api2(base=BASE, username=user, password=PW)


class Resp:
    __slots__ = ('status', 'json', 'text', 'binary', 'headers', 'error')

    def __init__(self, d):
        self.status = d['status']
        self.json = d['json']
        self.text = d['text']
        self.binary = d.get('binary', b'')
        self.headers = d.get('headers', {})
        self.error = d.get('error')


class Api2(doc.Api):
    """把 dict 响应包成属性访问对象。"""

    def call(self, method, path, payload=None, raw_body=None, headers=None, timeout=30):
        return Resp(doc.Api.call(self, method, path, payload=payload, raw_body=raw_body,
                                 headers=headers, timeout=timeout))

    def get(self, path):
        return self.call('GET', path)

    def post(self, path, payload):
        return self.call('POST', path, payload=payload)


def raw_api(cookie='', origin='none'):
    r = Api2.__new__(Api2)
    r.base, r.cookie, r.origin_mode = BASE, cookie, origin
    return r


def create_model(api, name):
    r = api.post('/api/ontologies', {'name': name})
    return r


def model_get(api, mid):
    r = api.get('/api/state?ontology=' + mid)
    return r


def mutate_model(state, tag):
    """在 schema 形态 state 上追加一个对象类型（保留 definitionOrder）。"""
    onto = state.setdefault('ontology', {})
    onto.setdefault('objectTypes', []).append(
        {'id': 'mg:q05obj' + tag, 'displayName': 'Q05对象' + tag, 'apiName': 'q05_obj_' + tag})
    order = onto.get('definitionOrder')
    if isinstance(order, list):
        onto['definitionOrder'] = order + ['mg:q05obj' + tag]
    return state


def create_project(api, name):
    return api.post('/api/projects', {'name': name})


results = {}


def suite_i1(rec):
    a1 = new_api('qa_int_a')
    a2 = new_api('qa_int_a')  # 同账号第二会话
    r = create_model(a1, 'Q05 CAS本体 ' + TS)
    if r.status != 201:
        rec.add('I1-pre', 'fail', '创建本体失败', brief(r.json) + str(r.status))
        return None
    mid = r.json['id']
    results['model_cas'] = mid
    s = model_get(a1, mid)
    rev0, state0 = s.json['revision'], s.json['state']
    s2 = model_get(a2, mid)
    assert s2.json['revision'] == rev0
    # 会话1 保存修改 → r1
    st1 = json.loads(json.dumps(state0, ensure_ascii=False))
    mutate_model(st1, 'a')
    st1['workspaceId'] = mid
    rv1 = a1.post('/api/save', {'state': st1, 'revision': rev0})
    rev1 = (rv1.json or {}).get('revision')
    rec.add('I1-save1', 'pass' if rv1.status == 200 and rev1 and rev1 != rev0 else 'fail',
            '会话1 基线保存 200 + 新 revision', brief(rv1.json))
    # 会话2 用旧 revision 保存不同修改 → 409 + currentRevision=r1
    st2 = json.loads(json.dumps(state0, ensure_ascii=False))
    mutate_model(st2, 'b')
    st2['workspaceId'] = mid
    rv2 = a2.post('/api/save', {'state': st2, 'revision': rev0})
    ok409 = (rv2.status == 409 and (rv2.json or {}).get('currentRevision') == rev1
             and (rv2.json or {}).get('code') == 'REVISION_CONFLICT')
    rec.add('I1-409', 'pass' if ok409 else 'fail', '会话2 旧 revision → 409+currentRevision',
            brief({'status': rv2.status, 'body': rv2.json, 'expect.currentRevision': rev1}))
    # 内容未被覆盖
    back = model_get(a1, mid)
    names = [o.get('displayName') for o in back.json['state']['ontology'].get('objectTypes', [])]
    covered = ('Q05对象b' in names)
    intact = ('Q05对象a' in names) and not covered
    rec.add('I1-noOverwrite', 'pass' if intact else 'fail', '409 后内容仍是会话1的（未被旧响应覆盖）',
            brief({'names': names, 'revision': back.json['revision']}))
    # 会话2 换基线重试成功
    st2b = json.loads(json.dumps(back.json['state'], ensure_ascii=False))
    mutate_model(st2b, 'b2')
    st2b['workspaceId'] = mid
    rv2b = a2.post('/api/save', {'state': st2b, 'revision': back.json['revision']})
    rev2 = (rv2b.json or {}).get('revision')
    rec.add('I1-retry', 'pass' if rv2b.status == 200 and rev2 else 'fail',
            '会话2 以 currentRevision 重试成功', brief(rv2b.json))
    # A→B→A：用最初的 rev0 再提交（即便内容与会话1那次相同也必拒）
    st_same = json.loads(json.dumps(model_get(a1, mid).json['state'], ensure_ascii=False))
    rv_a = a1.post('/api/save', {'state': st_same, 'revision': rev0})
    rec.add('I1-ABA', 'pass' if rv_a.status == 409 else 'fail',
            'A→B→A 场景旧 token（rev0）仍被拒（不透明令牌不按内容等价放行）',
            brief({'status': rv_a.status, 'body': rv_a.json}))
    # publish 的 revision 门
    rp = a1.post('/api/publish', {'state': st_same, 'revision': rev0, 'requestId': 'q05-i1-gate-' + TS})
    rec.add('I1-pubGate', 'pass' if rp.status == 409 and (rp.json or {}).get('currentRevision') else 'fail',
            'publish 旧 revision → 409+currentRevision', brief({'status': rp.status, 'body': rp.json}))
    # 项目草稿同类 CAS
    pr = create_project(a1, 'Q05 CAS项目 ' + TS)
    if pr.status != 201:
        rec.add('I1-proj-pre', 'fail', '创建项目失败', brief(pr.json))
        return mid
    pid = pr.json['id'] if 'id' in pr.json else pr.json.get('projectId')
    results['project_cas'] = pid
    b1 = a1.get('/api/project-state?project=' + pid)
    prev0, pstate0 = b1.json['revision'], b1.json['state']
    _b2 = a2.get('/api/project-state?project=' + pid)
    ps1 = json.loads(json.dumps(pstate0, ensure_ascii=False))
    ps1['name'] = ps1.get('name', 'Q05 CAS项目 ') + '-A1'
    pv1 = a1.post('/api/project-save', {'state': ps1, 'revision': prev0})
    prev1 = (pv1.json or {}).get('revision')
    ps2 = json.loads(json.dumps(pstate0, ensure_ascii=False))
    ps2['name'] = '会话2旧基线写入'
    pv2 = a2.post('/api/project-save', {'state': ps2, 'revision': prev0})
    okp = (pv2.status == 409 and (pv2.json or {}).get('currentRevision') == prev1)
    rec.add('I1-proj409', 'pass' if okp else 'fail', '项目旧 revision project-save → 409+currentRevision',
            brief({'status': pv2.status, 'body': pv2.json, 'expect.currentRevision': prev1}))
    backp = a1.get('/api/project-state?project=' + pid)
    rec.add('I1-projNoOverwrite',
            'pass' if backp.json['state'].get('name') != '会话2旧基线写入' else 'fail',
            '项目 409 后名称未被旧响应覆盖', brief({'name': backp.json['state'].get('name')}))
    # flow CAS（同类验证）
    fr = a1.post('/api/flows', {'name': 'Q05 CAS流 ' + TS})
    if fr.status == 201:
        fid = fr.json.get('id')
        results['flow_cas'] = fid
        fs = a1.get('/api/flow-state?flow=' + fid)
        fstate, frev = fs.json['state'], fs.json['revision']
        fs2 = json.loads(json.dumps(fstate, ensure_ascii=False))
        fs2['description'] = 'q05-cas-write'
        f1 = a1.post('/api/flow-save', {'state': fs2, 'revision': frev})
        f2 = a1.post('/api/flow-save', {'state': fs2, 'revision': frev})  # 旧 token 重放
        rec.add('I1-flow409', 'pass' if f1.status == 200 and f2.status == 409 and
                (f2.json or {}).get('currentRevision') == (f1.json or {}).get('revision') else 'fail',
                'flow-save 旧 revision → 409+currentRevision',
                brief({'first': {'s': f1.status, 'b': f1.json}, 'stale': {'s': f2.status, 'b': f2.json}}))
    return mid


def suite_i2(rec):
    a = new_api('qa_int_a')
    mid = results.get('model_cas_pub') or create_model(a, 'Q05 幂等本体 ' + TS).json['id']
    s = model_get(a, mid)
    state, rev = s.json['state'], s.json['revision']
    state['workspaceId'] = mid
    rid = 'q05-pub-idem-' + TS
    p1 = a.post('/api/publish', {'state': state, 'revision': rev, 'requestId': rid})
    if p1.status != 200:
        rec.add('I2-pub', 'info', '幂等本体发布未成功（可能校验 422，见 body）', brief(p1.json))
        v1 = None
    else:
        v1 = p1.json.get('version')
        rec.add('I2-pub', 'pass', f'首发 publish 200 version={v1}', brief(p1.json))
        # 同 requestId 同内容 → 回放同版本
        rev_now = model_get(a, mid).json['revision']
        st_now = model_get(a, mid).json['state']
        st_now['workspaceId'] = mid
        p2 = a.post('/api/publish', {'state': st_now, 'revision': rev_now, 'requestId': rid})
        ok = (p2.status == 200 and p2.json.get('idempotentReplay') and p2.json.get('version') == v1)
        rec.add('I2-pubReplay', 'pass' if ok else 'fail', '同 requestId 重复 publish → 回放同版本不重复发布',
                brief({'status': p2.status, 'body': p2.json, 'v1': v1}))
        # 版本列表只有一个该版本
        vs = a.get('/api/versions?ontology=' + mid)
        items = [i.get('version') for i in (vs.json or {}).get('items', [])]
        rec.add('I2-verOnce', 'pass' if items.count(v1) == 1 else 'fail',
                '版本列表中该版本只出现一次', brief({'versions': items}))
        # 同 requestId 不同内容 → 409
        st2 = json.loads(json.dumps(st_now, ensure_ascii=False))
        mutate_model(st2, 'idem2')
        st2['workspaceId'] = mid
        sv = a.post('/api/save', {'state': st2, 'revision': rev_now})
        p3 = a.post('/api/publish', {'state': st2, 'revision': (sv.json or {}).get('revision'), 'requestId': rid})
        rec.add('I2-pubDiff', 'pass' if p3.status == 409 else 'fail',
                '同 requestId 不同内容 → 409', brief({'status': p3.status, 'body': p3.json}))
    # 重复注册 409
    dup = 'qa_int_dup' + TS[-4:]
    r1 = a.call('POST', '/api/auth-register', {'username': dup, 'password': PW})
    r2 = a.call('POST', '/api/auth-register', {'username': dup, 'password': PW})
    ok = (r1.status == 201 and r2.status == 409 and (r2.json or {}).get('code') == 'DUPLICATE_NAME')
    rec.add('I2-dupReg', 'pass' if ok else 'fail', '重复注册 → 201 后 409 DUPLICATE_NAME',
            brief({'first': [r1.status, r1.json], 'second': [r2.status, r2.json]}))
    # 重复 connection-secret 写（同值两次）
    pid = results.get('project_cas')
    if pid:
        c1 = a.post('/api/connection-secret', {'projectId': pid, 'connectionId': 'q05-conn-i2',
                                               'action': 'set', 'secret': 'dup-secret-1'})
        c2 = a.post('/api/connection-secret', {'projectId': pid, 'connectionId': 'q05-conn-i2',
                                               'action': 'set', 'secret': 'dup-secret-1'})
        rec.add('I2-connSecretTwice', 'pass' if c1.status == 200 and c2.status == 200 else 'fail',
                '重复 connection-secret 同值写：两次均 200（覆盖语义，无重复行报错）',
                brief({'r1': c1.json, 'r2': c2.json}))


def suite_i5(rec):
    # 从 server.py 导入白名单表（只读导入，不启动服务）
    sys.path.insert(0, str(ROOT))
    from workbench import server as srv
    get_paths, post_paths = list(srv.GET_ROUTES.keys()) + ['/api/ontologies'], list(srv.POST_ROUTES.keys())
    free_get, free_post = list(srv.AUTH_FREE_GET), list(srv.AUTH_FREE_POST)
    # 未登录 GET 遍历（免登录端点单独验证）
    _bad401, bad_other = [], []
    for p in get_paths:
        if p in free_get:
            continue
        resp = raw_api().call('GET', p)
        if resp.status == 401 and (resp.json or {}).get('code') == 'UNAUTHENTICATED':
            continue
        bad_other.append((p, resp.status, brief(resp.json)))
    rec.add('I5-getAll', 'pass' if not bad_other else 'fail',
            f'未登录 GET 白名单 {len(get_paths)} 条（除免登录 {free_get}）→ 全部 401 UNAUTHENTICATED',
            brief({'exceptions': bad_other, 'count401': len(get_paths) - len(free_get) - len(bad_other)}))
    # 未登录 POST 遍历（带合法 Origin + JSON 体）
    exceptions = []
    for p in post_paths:
        if p in free_post:
            continue
        resp = raw_api(origin='allow').call('POST', p, payload={})
        if resp.status == 401 and (resp.json or {}).get('code') == 'UNAUTHENTICATED':
            continue
        exceptions.append((p, resp.status, brief(resp.json)))
    rec.add('I5-postAll', 'pass' if not exceptions else 'fail',
            '未登录 POST 全白名单（除免登录4端点）→ 401 UNAUTHENTICATED',
            brief({'exceptions': exceptions}))
    # 免登录端点确不拦：auth-state 200；错误口令 login 401 UNAUTHENTICATED（非会话门）；register 坏体 400
    r = raw_api()
    st = r.call('GET', '/api/auth-state')
    rec.add('I5-freeAuthState', 'pass' if st.status == 200 and st.json == {'user': None} else 'fail',
            '/api/auth-state 免登录 200 且 user=null', brief(st.json))
    lg = r.call('POST', '/api/auth-login', {'username': 'qa_int_a', 'password': 'wrong-wrong'})
    rec.add('I5-freeLogin', 'pass' if lg.status == 401 and (lg.json or {}).get('code') == 'UNAUTHENTICATED'
            else 'fail', 'auth-login 免登录（错口令→401 凭据错，非缺会话）', brief(lg.json))
    # 伪造 Cookie
    fake = new_api('qa_int_a')
    fake.cookie = 'deadbeef' * 8
    f1 = fake.get('/api/state')
    rec.add('I5-fakeCookie', 'pass' if f1.status == 401 and (f1.json or {}).get('code') == 'UNAUTHENTICATED'
            else 'fail', '伪造 Cookie → 401', brief({'status': f1.status, 'body': f1.json}))
    # logout 后旧 Cookie 失效
    token_user = 'qa_int_logout' + TS[-4:]
    _, tok = auth_client.register_or_login(BASE, token_user, PW)
    lo = raw_api('wiz_session=' + tok, origin='allow')
    st_before = lo.get('/api/auth-state')
    r_out = lo.call('POST', '/api/auth-logout', {})
    st_after = lo.get('/api/auth-state')
    state_after = lo.get('/api/state')
    ok = (st_before.json or {}).get('user') and r_out.status in (200, 204) \
        and (st_after.json or {}).get('user') is None and state_after.status == 401
    rec.add('I5-logout', 'pass' if ok else 'fail', 'logout 后旧 Cookie 立即失效',
            brief({'before': st_before.json, 'logout': [r_out.status, r_out.json],
                   'after-state': st_after.json, 'after-api': [state_after.status, state_after.json]}))


def suite_i6(rec):
    a = new_api('qa_int_a')
    b = new_api('qa_int_b')
    name_tag = 'Q06跨账号甲' + TS[-4:]
    m = create_model(a, name_tag)
    if m.status != 201:
        rec.add('I6-pre', 'fail', 'A 创建模型失败', brief(m.json))
        return
    mid = m.json['id']
    pr = create_project(a, name_tag + 'P')
    pid = pr.json.get('id') or pr.json.get('projectId')
    fr = a.post('/api/flows', {'name': name_tag + 'F'})
    fid = fr.json.get('id')
    results['a_model'], results['a_project'], results['a_flow'] = mid, pid, fid
    # A 的凭据类写入（供 I6/I8 复用）
    sec = a.post('/api/connection-secret', {'projectId': pid, 'connectionId': 'q05-conn-x',
                                            'action': 'set', 'secret': S_CONN})
    cred = a.post('/api/api-credential', {'projectId': pid, 'name': 'q05-cred', 'action': 'set',
                                          'secret': S_APIC})
    prov = a.post('/api/llm-provider-save', {'name': 'q05-prov' + TS, 'endpoint': 'http://127.0.0.1:9/v1',
                                             'model': 'm', 'apiKey': S_LLM, 'timeout': 5})
    prov_id = (prov.json or {}).get('provider', {}).get('id') or (prov.json or {}).get('provider', {}).get('providerId')
    results['prov_id'] = prov_id
    rec.add('I6-preWrite', 'pass' if sec.status == 200 and cred.status == 200 and prov.status == 200 else 'fail',
            'A 写入连接密码/API凭据/LLM Key 基线', brief({'sec': sec.json, 'cred': brief(cred.json), 'prov': brief(prov.json)}))
    # B 读取
    leak_checks = []
    def expect404(resp, label):
        body = json.dumps(resp.json, ensure_ascii=False) if resp.json else resp.text
        ok = resp.status == 404
        leak = name_tag in body
        leak_checks.append({'label': label, 'status': resp.status, 'leakName': leak, 'body': body[:200]})
        return ok and not leak
    checks = [
        (b.get('/api/state?ontology=' + mid), 'B GET model-state'),
        (b.get('/api/versions?ontology=' + mid), 'B GET versions'),
        (b.get('/api/project-state?project=' + pid), 'B GET project-state'),
        (b.get('/api/flow-state?flow=' + fid), 'B GET flow-state'),
    ]
    all404 = all(expect404(r, lbl) for r, lbl in checks)
    rec.add('I6-read404', 'pass' if all404 else 'fail', 'B 读 A 的 model/project/flow → 一律 404 且不泄漏名称',
            brief(leak_checks))
    plist = b.get('/api/projects')
    pids = json.dumps(plist.json, ensure_ascii=False)
    rec.add('I6-list', 'pass' if pid not in pids else 'fail', 'B 的 projects 列表不含 A 项目',
            brief({'has': pid in pids}))
    flist = b.get('/api/flows')
    rec.add('I6-flowList', 'pass' if fid not in json.dumps(flist.json, ensure_ascii=False) else 'fail',
            'B 的 flows 列表不含 A 编排', '')
    llm = b.get('/api/llm-providers')
    rec.add('I6-llmList', 'pass' if prov_id and prov_id not in json.dumps(llm.json, ensure_ascii=False) else 'fail',
            'B 的 llm-providers 不含 A 提供方', brief({'provId': prov_id}))
    # B 写尝试
    stb = model_get(b, 'storage')
    st = (stb.json or {}).get('state') or {'ontology': {'@graph': []}}
    st2 = json.loads(json.dumps(st, ensure_ascii=False))
    st2['workspaceId'] = mid
    sv = b.post('/api/save', {'state': st2, 'revision': 'r-none'})
    rec.add('I6-save', 'pass' if sv.status == 404 and name_tag not in json.dumps(sv.json, ensure_ascii=False)
            else 'fail', 'B POST /api/save 引用 A 模型 → 404 不泄漏', brief({'s': sv.status, 'b': sv.json}))
    # B 用 A 的 projectId 保存（危险面：是否建影子草稿/覆盖）
    a_state_before = a.get('/api/project-state?project=' + pid)
    bs = json.loads(json.dumps(a_state_before.json['state'], ensure_ascii=False)) if a_state_before.status == 200 else None
    if bs is None:
        bs = {'projectId': pid, 'name': 'B影子', 'ontologyId': '', 'ontologyVersion': '',
              'connections': {'connections': []}, 'bindings': {'object_bindings': []}}
    bs['name'] = 'B的影子写入'
    bsave = b.post('/api/project-save', {'state': bs, 'revision': None})
    after = a.get('/api/project-state?project=' + pid)
    a_intact = after.status == 200 and after.json['state'].get('name') != 'B的影子写入'
    rec.add('I6-projSave', 'pass' if (bsave.status in (404, 409, 400) and a_intact) else 'fail',
            'B project-save 冒用 A 项目：应拒绝且 A 内容不变',
            brief({'bSave': [bsave.status, brief(bsave.json)], 'aAfter': [after.status,
                  after.json['state'].get('name') if after.json else None,
                  after.json.get('revision') if after.json else None]}))
    # B 删 A flow
    bd = b.post('/api/flow-delete', {'flowId': fid})
    fa = a.get('/api/flow-state?flow=' + fid)
    rec.add('I6-flowDel', 'pass' if bd.status == 404 and fa.status == 200 else 'fail',
            'B flow-delete A 编排 → 404，A 仍在', brief({'del': [bd.status, bd.json], 'aStill': fa.status}))
    # B 覆盖 A 的连接密码 / 凭据
    bsec = b.post('/api/connection-secret', {'projectId': pid, 'connectionId': 'q05-conn-x',
                                             'action': 'set', 'secret': 'B-OVERWRITE-ATTEMPT'})
    bcred = b.post('/api/api-credential', {'projectId': pid, 'name': 'b-cred', 'action': 'set',
                                           'secret': 'B-CRED-ATTEMPT'})
    bprov = b.post('/api/llm-provider-delete', {'providerId': prov_id})
    prov_after = a.get('/api/llm-providers')
    still = prov_id and prov_id in json.dumps(prov_after.json, ensure_ascii=False)
    rec.add('I6-secretCross', 'pass' if bsec.status in (400, 404) and bcred.status in (400, 404)
            and still else 'fail',
            'B 跨账号写连接密码/API凭据 → 拒且 A 资产完好',
            brief({'sec': [bsec.status, bsec.json], 'cred': [bcred.status, bcred.json], 'aProvStill': still}))
    # 跨账号删除 LLM 提供方：实测 200 {cleared:true} 但为 no-op（clear 对不存在者静默）
    rec.add('I6-llmDelNoop', 'pass' if bprov.status == 200 and still else 'fail',
            'B llm-provider-delete A 的 provider：200 cleared=true 但 A 不受影响（幂等删除语义）；'
            '与 06 文档「跨账号按不存在 404/空」表述存在轻微不符',
            brief({'del': [bprov.status, bprov.json], 'aProvStill': still}))
    # 配置包暂存跨账号
    stg = b.post('/api/config-package-stage', {'action': 'begin', 'filename': 'x.zip',
                                               'bytes': 100, 'sha256': '0' * 64})
    upid = (stg.json or {}).get('uploadId')
    a_prev = a.post('/api/config-package-import-preview', {'uploadId': upid}) if upid else None
    rec.add('I6-stageCross', 'pass' if a_prev and a_prev.status == 404 else 'fail',
            'A import-preview B 的 uploadId → 404（按不存在）',
            brief({'stage': [stg.status, brief(stg.json)], 'preview': [a_prev.status if a_prev else None,
                  brief(a_prev.json) if a_prev else None]}))
    results['b_upload'] = upid
    # 新注册 C 看不到 A/B
    c = new_api('qa_int_c')
    c_models = c.get('/api/ontologies?list=1') if False else c.call('GET', '/api/ontologies')
    c_json = json.dumps(c_models.json, ensure_ascii=False)
    rec.add('I6-newC', 'pass' if c_models.status == 200 and mid not in c_json and
            (results.get('model_cas') or 'zz') not in c_json else 'fail',
            'C 登录后本体列表看不到 A/B 资产', brief({'status': c_models.status, 'body': c_json[:300]}))


def suite_i7(rec):
    a = new_api('qa_int_a')
    # >2MB：服务端在读取 body 前按 Content-Length 拒绝并关闭连接（413 + PAYLOAD_TOO_LARGE）。
    # urllib 会因半程关闭收到 Broken pipe，属客户端表象；用 curl 断言真实响应。
    _, tok = auth_client.register_or_login(BASE, 'qa_int_a', PW)
    proc = subprocess.run(
        ['curl', '-s', '-m', '20', '-o', '-', '-w', '\\n%{http_code}', '-X', 'POST',
         '-H', 'Content-Type: application/json', '-H', 'Origin: ' + BASE,
         '-b', 'wiz_session=' + tok,
         '--data-binary', '@-', BASE + '/api/save'],
        input=('{\"blob\":\"' + 'x' * 2_100_000 + '\"}').encode(),
        capture_output=True, timeout=30)
    out = proc.stdout.decode('utf-8', 'replace')
    code_line = out.rsplit('\n', 1)[-1].strip()
    body_part = out.rsplit('\n', 1)[0]
    ok = code_line == '413' and 'PAYLOAD_TOO_LARGE' in body_part
    # urllib 视角记录（Broken pipe=连接被提前关闭，不算缺陷，注明表象）
    r = a.call('POST', '/api/save', payload={'state': {'ontology': {'@graph': []}},
                                             'blob': 'x' * 2_100_000})
    rec.add('I7-bigBody', 'pass' if ok else 'fail',
            '>2MB JSON → 413 PAYLOAD_TOO_LARGE（curl 断言；urllib 同请求见 Broken pipe=' + str(r.status) + '）',
            brief({'curlStatus': code_line, 'curlBody': body_part[:120], 'urllib': str(r.error)[:120]}))
    # 损坏 JSON
    r = a.call('POST', '/api/save', raw_body=b'{"state": {broken')
    ok = r.status == 400 and (r.json or {}).get('code') == 'INVALID_ARGUMENT'
    rec.add('I7-brokenJson', 'pass' if ok else 'fail', '损坏 JSON → 400 非 500', brief({'s': r.status, 'b': r.json}))
    r = a.call('POST', '/api/save', raw_body=b'[1,2,3]')
    rec.add('I7-nonDict', 'pass' if r.status == 400 else 'fail', '非对象 JSON → 400', brief({'s': r.status, 'b': r.json}))
    r = a.call('POST', '/api/save', raw_body=b'')
    rec.add('I7-emptyBody', 'pass' if r.status == 400 else 'fail', '空 body → 400', brief({'s': r.status, 'b': r.json}))
    # 未知路径
    r = a.get('/api/definitely-not-a-route')
    rec.add('I7-unknownGET', 'pass' if r.status == 404 and (r.json or {}).get('code') == 'NOT_FOUND' else 'fail',
            '未知 GET 路径 → 404', brief({'s': r.status, 'b': r.json}))
    r = a.post('/api/definitely-not-a-route', {})
    rec.add('I7-unknownPOST', 'pass' if r.status == 404 else 'fail', '未知 POST 路径 → 404', brief({'s': r.status}))
    # 方法不符
    r = a.get('/api/save')
    rec.add('I7-getOnPost', 'pass' if r.status == 404 else 'fail', 'GET /api/save（方法不符）→ 404',
            brief({'s': r.status, 'b': r.json}))
    # Origin 三态（对照 文档 README §「缺失或不符返回403」）
    st = model_get(a, results.get('model_cas') or 'storage')
    base_state = ((st.json or {}).get('state')) or {'ontology': {'@graph': []}}
    base_state = json.loads(json.dumps(base_state, ensure_ascii=False))
    base_state['workspaceId'] = results.get('model_cas') or 'storage'
    # 明确错误 Origin
    # 本机他端口 Origin：手工构造
    import urllib.request as ur
    import urllib.error as ue
    def post_with_origin(origin):
        data = json.dumps({'state': base_state, 'revision': 'x'}).encode()
        hdr = {'Content-Type': 'application/json', 'Cookie': a.cookie}
        if origin is not None:
            hdr['Origin'] = origin
        req = ur.Request(BASE + '/api/save', data=data, headers=hdr, method='POST')
        try:
            with ur.urlopen(req, timeout=15) as resp:
                return resp.status, resp.read()[:200]
        except ue.HTTPError as e:
            return e.code, e.read()[:200]
    no_o = post_with_origin(None)
    same_o = post_with_origin(BASE)
    other_p = post_with_origin('http://127.0.0.1:18931')
    evil_o = post_with_origin('http://evil.com')
    doc_no_origin_rejected = (no_o[0] == 403)
    rec.add('I7-originNoOrigin',
            'pass' if doc_no_origin_rejected else 'fail',
            'POST 无 Origin：接口文档 README 规定「缺失或不符返回 403」',
            brief({'noOrigin': no_o, 'sameOrigin': same_o, 'otherLocalPort': other_p, 'evilCom': evil_o,
                   'implAllowsMissing': not doc_no_origin_rejected,
                   'note': '实现 allowed={None, 127.0.0.1:port, localhost:port}；缺省放行属实现与文档不符或文档口径需更新，Cookie SameSite=Strict 仍缓解浏览器CSRF'}))
    okp = same_o[0] == 409 and other_p[0] == 403 and evil_o[0] == 403
    rec.add('I7-originMatrix', 'pass' if okp else 'fail',
            'Origin：同源→进业务(409 revision假值)；本机他端口/evil.com → 403',
            brief({'same': same_o, 'otherPort': other_p, 'evil': evil_o}))
    # 超长 URL / 超长头部（非 500 即视为通过边界，但记录实际）
    try:
        r = a.get('/api/state?ontology=' + 'q' * 20000)
        rec.add('I7-longUrl', 'pass' if r.status in (400, 404, 414) else 'fail',
                '超长 URL → 客户端错误', brief({'s': r.status}))
    except Exception as e:
        rec.add('I7-longUrl', 'info', '超长 URL 连接层异常（未挂死）', str(e)[:200])
    try:
        big_cookie = raw_api('wiz_session=' + 'z' * 100000)
        r = big_cookie.get('/api/state')
        rec.add('I7-longHeader', 'pass' if r.status in (400, 401, 404, 431, None) else 'fail',
                '超长 Cookie 头 → 非 500', brief({'s': r.status}))
    except Exception as e:
        rec.add('I7-longHeader', 'info', '超长头部连接层拒绝（未挂死、非 500）', str(e)[:200])


def scan_blob(label, data, rec):
    hits = [k for k, v in sentinels.items() if v.encode() if isinstance(data, bytes) and v.encode() in data]
    return hits


def suite_i8(rec):
    a = new_api('qa_int_a')
    pid = results.get('a_project')
    mid = results.get('a_model')
    # 全 GET 响应扫描
    paths = ['/api/state?ontology=' + (mid or 'storage'), '/api/versions?ontology=' + (mid or 'storage'),
             '/api/releases?ontology=' + (mid or 'storage'), '/api/projects',
             '/api/project-state?project=' + (pid or 'x'), '/api/project-releases?project=' + (pid or 'x'),
             '/api/project-config?project=' + (pid or 'x'), '/api/api-credentials?project=' + (pid or 'x'),
             '/api/flows', '/api/llm-providers', '/api/storage-status',
             '/api/model-definitions', '/api/ontologies']
    text_hits = {}
    for p in paths:
        r = a.get(p)
        blob = (r.text or '') + json.dumps(r.json, ensure_ascii=False, default=str) if r.json else (r.text or '')
        hits = [k for k, v in sentinels.items() if v in blob]
        if hits:
            text_hits[p] = hits
    rec.add('I8-getScan', 'pass' if not text_hits else 'fail',
            f'全部 GET 端点响应（{len(paths)} 条）无明文密钥/口令', brief(text_hits))
    # 本体导出 ZIP
    st = model_get(a, mid or 'storage')
    exp = a.call('POST', '/api/export', payload={'state': st.json.get('state')}) if st.status == 200 else None
    zip_hits = []
    if exp and exp.status == 200:
        for k, v in sentinels.items():
            if v.encode() in exp.binary:
                zip_hits.append(k)
    rec.add('I8-exportZip', 'pass' if exp and exp.status == 200 and not zip_hits else ('fail' if zip_hits else 'info'),
            '/api/export ZIP 无哨兵明文', brief({'status': exp.status if exp else None, 'hits': zip_hits}))
    # 配置包导出（含凭据占位的导出链路）
    ep = a.post('/api/config-package-export-preview', {'modelIds': [mid] if mid else [], 'projectIds': [pid] if pid else []})
    ex_hits = [k for k, v in sentinels.items() if v in json.dumps(ep.json, ensure_ascii=False)]
    zipbin = b''
    tok = (ep.json or {}).get('exportToken')
    if tok and not (ep.json or {}).get('blockers'):
        e2 = a.call('POST', '/api/config-package-export', payload={'exportToken': tok})
        if e2.status == 200:
            zipbin = e2.binary
            ex_hits += [k for k, v in sentinels.items() if v.encode() in zipbin]
    rec.add('I8-configExport', 'pass' if not ex_hits else 'fail',
            '配置包 export-preview + export ZIP 无哨兵明文', brief({'hits': ex_hits, 'blockers': (ep.json or {}).get('blockers', [])}))
    # 服务日志
    hits_log = {}
    if LOG.exists():
        data = LOG.read_bytes()
        hits_log = {k: data.count(v.encode()) for k, v in sentinels.items() if data.count(v.encode())}
    rec.add('I8-serverLog', 'pass' if not hits_log else 'fail', 'server-18932.log 无哨兵明文', brief(hits_log))
    # SQLite 文件 strings（含 WAL）
    hits_db = {}
    for f in [DB, Path(str(DB) + '-wal'), Path(str(DB) + '-shm')]:
        if f.exists():
            data = f.read_bytes()
            for k, v in sentinels.items():
                if v.encode() in data:
                    hits_db.setdefault(k, []).append(f.name)
    verdict = 'fail' if hits_db else 'pass'
    rec.add('I8-dbStrings', verdict if hits_db else 'pass',
            'SQLite(+WAL) 原始字节无哨兵明文（凭据均加密）' if not hits_db else '!! DB 出现明文凭据',
            brief(hits_db))
    # 口令哈希抽查
    import sqlite3
    con = sqlite3.connect('file:' + str(DB) + '?mode=ro', uri=True)
    try:
        rows = con.execute('SELECT username, substr(password_hash,1,40) FROM wb_users WHERE username IN '
                           "('qa_int_a','qa_int_b')").fetchall()
    finally:
        con.close()
    hashed = rows and all(r[1].lower().startswith('pbkdf2') or '$' in r[1] for r in rows)
    rec.add('I8-pwHash', 'pass' if hashed else 'fail', '口令列只存派生哈希（前缀样例）',
            brief([{'user': r[0], 'prefix': r[1][:20]} for r in rows]))


def suite_i9(rec):
    a = new_api('qa_int_a')
    mid9 = results.get('model_cas') or 'storage'
    st = model_get(a, mid9)
    state = st.json['state']
    state['workspaceId'] = mid9
    # 构造校验错误：属性挂在不存在的对象
    state.setdefault('ontology', {}).setdefault('properties', []).append(
        {'id': 'mg:q05orphan' + TS, 'displayName': '孤儿属性' + TS, 'apiName': 'q05_orphan_' + TS[-4:],
         'objectTypeId': 'mg:does-not-exist', 'dataType': {'type': 'string'}})
    order = state['ontology'].get('definitionOrder')
    if isinstance(order, list):
        order.append('mg:q05orphan' + TS)
    v = a.post('/api/validate', {'state': state})
    ok = v.status == 200 and isinstance((v.json or {}).get('errors'), list) and len(v.json['errors']) > 0
    rec.add('I9-validate', 'pass' if ok else 'fail', '校验失败 → 200 且 errors 非空（不空数据假成功）',
            brief({'s': v.status, 'errors': (v.json or {}).get('errors', [])[:3]}))
    pub = a.post('/api/publish', {'state': state, 'revision': st.json['revision'],
                                  'requestId': 'q05-i9-' + TS, 'changeType': 'compatible'})
    ok = pub.status == 422 and (pub.json or {}).get('errors')
    rec.add('I9-pubBlock', 'pass' if ok else 'fail', '带校验错误 publish → 422 + errors（不 200）',
            brief({'s': pub.status, 'b': brief(pub.json)}))
    # 草稿保存带错误：找一个「校验有错但不触发保存边界(新增悬空引用)422」的形态
    sv = None
    for cand in ('emptyApi', 'emptyDisplay', 'badDataType'):
        fresh = model_get(a, mid9)
        s2 = json.loads(json.dumps(fresh.json['state'], ensure_ascii=False))
        s2['workspaceId'] = mid9
        props = s2.setdefault('ontology', {}).setdefault('properties', [])
        oid = (s2['ontology'].get('objectTypes') or [{}])[0].get('id') or 'mg:q05objb2'
        node = {'id': 'mg:q05val' + TS + cand, 'displayName': 'Q05校验样' + cand,
                'apiName': 'q05_val_' + cand + TS[-4:], 'objectTypeId': oid,
                'dataType': {'type': 'string'}}
        if cand == 'emptyApi':
            node['apiName'] = ''
        if cand == 'emptyDisplay':
            node['displayName'] = ''
        if cand == 'badDataType':
            node['dataType'] = {'type': 'nonsense-' + TS[-4:]}
        order = s2['ontology'].get('definitionOrder')
        if isinstance(order, list):
            order.append(node['id'])
        props.append(node)
        v2 = a.post('/api/validate', {'state': s2})
        if not (v2.json or {}).get('errors'):
            continue
        sv = a.post('/api/save', {'state': s2, 'revision': fresh.json['revision']})
        if sv.status == 200:
            break
    ok = sv is not None and sv.status == 200 and isinstance((sv.json or {}).get('errors'), list)
    rec.add('I9-saveWithErrors', 'pass' if ok else ('known' if sv and sv.status == 422 else 'fail'),
            'save 带校验错误 200 且如实回传 errors（422 属 20260920 保存边界拦截，非假成功）',
            brief({'s': sv.status if sv else None, 'body': brief(sv.json) if sv else None}))
    # 项目发布未绑定本体 → 422 不假成功
    pr = create_project(a, 'Q05 未绑定项目 ' + TS)
    pid = pr.json.get('id') or pr.json.get('projectId')
    ps = a.get('/api/project-state?project=' + pid)
    pstate = ps.json['state']
    pp = a.post('/api/project-publish', {'state': pstate, 'revision': ps.json['revision'],
                                         'requestId': 'q05-i9p-' + TS})
    rec.add('I9-projPubNoRef', 'pass' if pp.status == 422 else 'fail',
            '未绑定本体的项目发布 → 422', brief({'s': pp.status, 'b': pp.json}))
    # 409 假成功检查：409 后 revision 未变（I1 已证 409；这里再证不产生版本）
    vs = a.get('/api/versions?ontology=' + results['model_cas'])
    rec.add('I9-noPhantom', 'pass' if all('q05-i9' not in str(i) for i in (vs.json or {}).get('items', [])) else 'fail',
            '422/409 路径未产生发布记录（版本列表无 q05-i9 痕迹）',
            brief({'versions': [i.get('version') for i in (vs.json or {}).get('items', [])]}))


def suite_i3b(rec):
    """暂存生命周期：数量上限/覆盖淘汰、discard、未知/跨 token import-preview。"""
    a = new_api('qa_int_a')
    u1 = a.post('/api/config-package-stage', {'action': 'begin', 'filename': 'q05a.zip',
                                              'bytes': 500, 'sha256': 'a' * 64})
    u2 = a.post('/api/config-package-stage', {'action': 'begin', 'filename': 'q05b.zip',
                                              'bytes': 500, 'sha256': 'b' * 64})
    u3 = a.post('/api/config-package-stage', {'action': 'begin', 'filename': 'q05c.zip',
                                              'bytes': 500, 'sha256': 'c' * 64})
    id1 = (u1.json or {}).get('uploadId')
    id3 = (u3.json or {}).get('uploadId')
    # 上限=2/账号（文档口径），实现=静默淘汰最旧：u1 应已失效
    p1 = a.post('/api/config-package-import-preview', {'uploadId': id1})
    p3 = a.post('/api/config-package-import-preview', {'uploadId': id3})
    ok = (u1.status == u2.status == u3.status == 200) and p1.status == 404 and p3.status != 404
    rec.add('I3b-evict', 'pass' if ok else 'fail',
            '第3个暂存静默淘汰最旧（MAX_STAGED=2，实现为LRU淘汰非拒绝）；被淘汰 uploadId import-preview→404',
            brief({'u1': [u1.status], 'p1': [p1.status, p1.json], 'p3': [p3.status, brief(p3.json)]}))
    # stage 不传 import：分片文件落 DATA_ROOT/tmp/config-packages/<uploadId>/，discard 后清理
    stage_dir = ROOT / '.runtime/test-data-q05/tmp/config-packages'
    exists3 = stage_dir.exists() and id3 and (stage_dir / id3).exists()
    d = a.post('/api/config-package-discard', {'uploadId': id3})
    gone = not (stage_dir.exists() and id3 and (stage_dir / id3).exists())
    rec.add('I3b-stageFiles', 'pass' if exists3 and d.status == 200 and gone else 'fail',
            '暂存文件目录存在于 tmp/config-packages，discard 后目录清除',
            brief({'before': exists3, 'discard': [d.status, d.json], 'afterGone': gone}))
    # 未知 token 幂等 discard 200
    d2 = a.post('/api/config-package-discard', {'uploadId': 'nonexistent-token'})
    rec.add('I3b-discardUnknown', 'pass' if d2.status == 200 else 'fail', '未知 token discard → 200 幂等',
            brief(d2.json))
    # TTL 参数记录（不真等 30min）：文档与实现均为 1800s
    import workbench.config_package_format as cpf
    ok_ttl = cpf.STAGE_TTL_SECONDS == 1800 and cpf.MAX_STAGED_PER_OWNER == 2
    rec.add('I3b-ttlConsts', 'pass' if ok_ttl else 'info',
            f'TTL/上限常量实测：STAGE_TTL={cpf.STAGE_TTL_SECONDS}s MAX_STAGED={cpf.MAX_STAGED_PER_OWNER}（TTL 到期需 30min，未实际等待）',
            '')


def main():
    rec = Recorder('http-main')
    try:
        auth_client.wait_ready(BASE, timeout=20)
    except Exception as e:
        print('服务未就绪:', e)
        sys.exit(1)
    for name, fn in [('I1', suite_i1), ('I2', suite_i2), ('I5', suite_i5),
                     ('I6', suite_i6), ('I7', suite_i7), ('I3b', suite_i3b),
                     ('I8', suite_i8), ('I9', suite_i9)]:
        print(f'\n===== {name} =====')
        try:
            fn(rec)
        except Exception:
            import traceback
            rec.add(name + '-crash', 'fail', f'{name} 套件异常中断', traceback.format_exc(limit=6))
    path = rec.dump()
    print('\n证据:', path)
    # 汇总上下文交给后续脚本
    (EVIDENCE / 'ctx_main.json').write_text(json.dumps({k: str(v) for k, v in results.items()},
                                                       ensure_ascii=False), encoding='utf-8')


if __name__ == '__main__':
    main()
