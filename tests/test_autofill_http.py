"""整表自动填写 autofill/1 HTTP 集成回归（T4，2026-09-22）：mode=fill + protocol=2 真实链路。

隔离规则（沿用 test_assist_api.py 的自管端口 + 模型桩模式）：
- 纯标准库；临时数据根（WIZ_WORKBENCH_ROOT）+ 独立端口子进程服务（服务 18931 / 桩 18932），
  测完自起进程全部停掉；
- 模型桩：本地线程起 OpenAI 兼容 /chat/completions 假端点，提供方配置指向它；
  不读写真实业务数据，不连真实 LLM/MySQL/Redis。

覆盖：
- 首填正常链路：200 envelope 字段齐全（protocol/formId/schemaVersion/schemaDigest/target/
  指纹/sessionId/roundId/operations/questions/unresolved/summary），session/round 下发；
- 续轮握手：应用操作后 draft 变化 → 旧 contextToken 409 CONTEXT_STALE，必须新取令牌；
- 跨轮问题答复成功（basis.kind=question 跨轮核验）+ 跨轮/未知问题 id → 400 问题已过期；
- 跨用户 token 409（沿用既有隔离语义）；目标不存在/已删除 404；
- 引用不存在 → 转 unresolved（不写入 operations）；
- protocol 缺失/非 2 → 400 INVALID_ARGUMENT；无默认模型 → 422；
- 模型桩非法 JSON → 502；桩截断（finish_reason=length）→ 502；
- 只读不变式：生成前后 revision 与 SQLite 库内容不变（测试进程直读库断言）；
- 密钥（stub-key）与原始 trace 不出现在任何响应；
- （附带）assist_forms.FormContract 组节点 atomicGroup 展开为组内叶子路径全集（T2 冻结口径）。
运行：python3 tests/test_autofill_http.py
"""
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import auth_client

REPO = Path(__file__).resolve().parents[1]
PORT = 18931
STUB_PORT = 18932
BASE = f'http://127.0.0.1:{PORT}'
TMP = Path(tempfile.mkdtemp(prefix='wiz_autofill_http_'))
PROC = None
STUB_HTTPD = None
PASSED = []


def check(cond, message, actual=None):
    if cond:
        return
    print(f'\n[失败] {message}')
    if actual is not None:
        print('  实际: ' + json.dumps(actual, ensure_ascii=False, default=str)[:2000])
    shutdown()
    sys.exit(1)


def ok(step, desc):
    PASSED.append(step)
    print(f'通过 {step}) {desc}')


def shutdown():
    global PROC, STUB_HTTPD
    if PROC is not None:
        PROC.terminate()
        try:
            PROC.wait(timeout=10)
        except subprocess.TimeoutExpired:
            PROC.kill()
        PROC = None
    if STUB_HTTPD is not None:
        STUB_HTTPD.shutdown()
        STUB_HTTPD.server_close()
        STUB_HTTPD = None


# --- 模型桩：OpenAI 兼容 chat/completions -------------------------------------

STUB_STATE = {'content': '{}', 'delay': 0, 'finish': 'stop', 'hits': 0,
              'last_system': '', 'last_user': ''}


class StubHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
        try:
            payload = json.loads(body)
        except ValueError:
            payload = {}
        auth = self.headers.get('Authorization', '')
        STUB_STATE['hits'] += 1
        if not auth.startswith('Bearer stub-key'):
            self._respond(401, {'error': 'bad key'})
            return
        messages = payload.get('messages') or []
        if len(messages) == 2:
            STUB_STATE['last_system'] = str(messages[0].get('content') or '')
            STUB_STATE['last_user'] = str(messages[1].get('content') or '')
        if STUB_STATE['delay']:
            time.sleep(STUB_STATE['delay'])
        self._respond(200, {'choices': [{'message': {'content': STUB_STATE['content']},
                                         'finish_reason': STUB_STATE['finish']}]})

    def _respond(self, code, payload):
        data = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def start_stub():
    global STUB_HTTPD
    STUB_HTTPD = ThreadingHTTPServer(('127.0.0.1', STUB_PORT), StubHandler)
    threading.Thread(target=STUB_HTTPD.serve_forever, daemon=True).start()


# --- HTTP ---------------------------------------------------------------------

COOKIE_A = {'value': ''}
COOKIE_B = {'value': ''}


def request(method, path, payload=None, cookie=None):
    cookie = cookie if cookie is not None else COOKIE_A['value']
    data = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    headers = {'Content-Type': 'application/json', 'Origin': BASE}
    if cookie:
        headers['Cookie'] = 'wiz_session=' + cookie
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode())
        except ValueError:
            return exc.code, {}


def assist_context_token(space, target_kind, target_id='', draft=None, cookie=None):
    """取 fill 用途的 contextToken；返回 (status, body)。"""
    payload = {'space': space, 'targetKind': target_kind, 'targetId': target_id,
               'purpose': 'fill', 'draft': draft if draft is not None else {}}
    if space == 'project':
        payload['projectId'] = 'p_autofill'
    return request('POST', '/api/assist-context', payload, cookie=cookie)


def fill_generate(token, draft, intent='', session_id=None, answers=None,
                  protocol=2, cookie=None, request_id=None):
    if request_id is None:
        request_id = 'af-' + str(int(time.time() * 1000))
    body = {'requestId': request_id, 'contextToken': token, 'mode': 'fill',
            'draft': draft, 'intent': intent}
    if protocol is not None:
        body['protocol'] = protocol
    if session_id:
        body['sessionId'] = session_id
    if answers:
        body['answers'] = answers
    return request('POST', '/api/assist-generate', body, cookie=cookie)


# --- 库内容只读断言（测试进程直读 SQLite） ---------------------------------------

def db_dump():
    """全部业务表行内容快照（排除 wb_sessions：登录滑动续期与生成无关）。"""
    con = sqlite3.connect(str(TMP / 'data' / 'workbench.sqlite3'))
    try:
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        dump = {}
        for table in sorted(tables):
            if table == 'wb_sessions':
                continue
            rows = con.execute('SELECT * FROM %s' % table).fetchall()
            dump[table] = sorted(repr(row) for row in rows)
        return dump
    finally:
        con.close()


# --- 附带：FormContract 组节点 atomicGroup 展开检查（T2 冻结口径） ------------------

def check_contract_group_expansion():
    sys.path.insert(0, str(REPO))
    from workbench import assist_forms
    # 真实 property 契约（叶子声明 atomicGroup）：行为不变
    fc = assist_forms.FormContract('property')
    check(fc.atomic_groups() == {'typeCore': ['dataType', 'obsType']},
          'property 契约叶子声明的原子组应保持成员为叶子路径', fc.atomic_groups())
    # 组节点声明 atomicGroup（04 §6.1 示例形态）：必须展开为组内叶子路径全集
    doc = {
        'formId': 'property', 'schemaVersion': 1, 'title': '属性定义', 'space': 'ontology',
        'fields': [
            {'id': 'label', 'label': '属性名称', 'type': 'text', 'required': True, 'maxLength': 120},
            {'id': 'dataType', 'label': '数据类型', 'type': 'group', 'atomicGroup': 'typeCore',
             'fields': [
                 {'id': 'type', 'label': '类型', 'type': 'enum',
                  'enum': ['string', 'double', 'timeSeries']},
                 {'id': 'valueType', 'label': '值类型', 'type': 'enum', 'enum': ['double'],
                  'visibleWhen': {'field': 'dataType.type', 'op': 'eq', 'value': 'timeSeries'},
                  'requires': 'dataType.type'}]},
        ],
        'refProviders': {},
    }
    import tempfile
    tmp = tempfile.mkdtemp(prefix='wiz_autofill_contract_')
    with open(os.path.join(tmp, 'property.json'), 'w', encoding='utf-8') as fh:
        json.dump(doc, fh, ensure_ascii=False)
    fc2 = assist_forms.FormContract('property', None, tmp)
    check(fc2.atomic_groups() == {'typeCore': ['dataType.type', 'dataType.valueType']},
          '组节点 atomicGroup 应展开为组内叶子路径全集', fc2.atomic_groups())
    try:
        fc2.field_def('dataType')
        check(False, '组节点不可经 field_def 寻址（应 KeyError）')
    except KeyError:
        pass


# --- 主流程 ---------------------------------------------------------------------

INTENT_R1 = '新建对象叫储能簇，它是一组电池包，可以整体充放电'
FILL_R1 = {
    'operations': [
        {'op': 'set', 'field': 'label', 'value': '储能簇',
         'basis': {'kind': 'intent', 'quote': '对象叫储能簇'}},
        {'op': 'set', 'field': 'comment',
         'value': '由若干电池包并联组成、可整体参与充放电的储能单元。',
         'basis': {'kind': 'intent', 'quote': '一组电池包，可以整体充放电'}},
    ],
    'questions': [{'text': '对象层级是什么？', 'fields': ['comment'],
                   'options': ['设备', '部件'], 'allowUnsure': True}],
    'unresolved': [],
}
DRAFT_R1_APPLIED = {'label': '储能簇',
                    'comment': '由若干电池包并联组成、可整体参与充放电的储能单元。'}
FILL_R2_QUESTION_BASIS = {
    'operations': [{'op': 'set', 'field': 'comment',
                    'value': '储能簇由若干电池包并联组成、可整体参与充放电。',
                    'basis': {'kind': 'question', 'questionId': 'q_1'}}],
    'questions': [], 'unresolved': [],
}
INTENT_LINK = '起点用演示设备，终点指向不存在的对象'
FILL_LINK_REFS = {
    'operations': [
        {'op': 'set', 'field': 'from', 'value': 'mg:demo_device',
         'basis': {'kind': 'intent', 'quote': '起点用演示设备'}},
        {'op': 'set', 'field': 'to', 'value': 'mg:object_ghost',
         'basis': {'kind': 'intent', 'quote': '终点指向不存在的对象'}},
    ],
    'questions': [], 'unresolved': [],
}


def seed_ontology():
    _code, body = request('GET', '/api/state')
    check(_code == 200, 'GET /api/state 应 200', _code)
    state, revision = body['state'], body['revision']
    ontology = state['ontology']
    ontology['objectTypes'].extend([
        {'id': 'mg:demo_device', 'displayName': '演示设备'},
        {'id': 'mg:demo_device2', 'displayName': '演示设备2'},
    ])
    ontology['linkTypes'].append({'id': 'mg:link_demo', 'displayName': '演示链接',
                                  'sourceObjectTypeId': 'mg:demo_device',
                                  'targetObjectTypeId': 'mg:demo_device2',
                                  'cardinality': 'one-to-many'})
    ontology['definitionOrder'] += ['mg:demo_device', 'mg:demo_device2', 'mg:link_demo']
    code, saved = request('POST', '/api/save', {'state': state, 'revision': revision})
    check(code == 200 and saved.get('revision'), '播种本体草稿应 200', (code, saved))


def main():
    global PROC
    for port in (PORT, STUB_PORT):
        probe = socket.socket()
        try:
            probe.bind(('127.0.0.1', port))
        except OSError as exc:
            print(f'[失败] 端口 {port} 已被占用：{exc}')
            sys.exit(1)
        finally:
            probe.close()
    start_stub()
    env = dict(os.environ, WIZ_WORKBENCH_ROOT=str(TMP), WIZ_WORKBENCH_PORT=str(PORT))
    PROC = subprocess.Popen([sys.executable, '-m', 'workbench.server'], cwd=str(REPO), env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    auth_client.wait_ready(BASE)
    _user_a, COOKIE_A['value'] = auth_client.register_or_login(BASE, 'autofill_a', 'autofill1234')
    _user_b, COOKIE_B['value'] = auth_client.register_or_login(BASE, 'autofill_b', 'autofill1234')

    # 1) 无默认模型：fill（protocol=2）→ 422 MODEL_NOT_CONFIGURED
    code, body = assist_context_token('ontology', 'object')
    check(code == 200 and body['context']['modelReady'] is False, '无模型 context 200', (code, body))
    token = body['contextToken']
    code, body = fill_generate(token, {}, intent='x', request_id='af-1')
    check(code == 422 and body.get('code') == 'MODEL_NOT_CONFIGURED',
          '无默认模型 fill 422', (code, body))
    ok(1, '无默认模型 422 MODEL_NOT_CONFIGURED')

    # 2) protocol 缺失 / 非 2 → 400 INVALID_ARGUMENT（旧 fill 协议停用）
    code, body = assist_context_token('ontology', 'object')
    token = body['contextToken']
    code, body = fill_generate(token, {}, intent='x', protocol=None, request_id='af-2')
    check(code == 400 and body.get('code') == 'INVALID_ARGUMENT' and 'protocol' in str(body.get('error')),
          'protocol 缺失 400', (code, body))
    code, body = assist_context_token('ontology', 'object')
    token = body['contextToken']
    code, body = fill_generate(token, {}, intent='x', protocol=3, request_id='af-3')
    check(code == 400 and body.get('code') == 'INVALID_ARGUMENT', 'protocol=3 400', (code, body))
    ok(2, 'protocol 缺失/非 2 → 400 INVALID_ARGUMENT')

    # 3) 播种本体（对象×2 + 链接）并登记模型桩提供方
    seed_ontology()
    code, body = request('POST', '/api/llm-provider-save',
                         {'name': '桩模型', 'endpoint': f'http://127.0.0.1:{STUB_PORT}/v1/chat/completions',
                          'model': 'stub-model', 'apiKey': 'stub-key', 'timeout': 5,
                          'isDefault': True})
    check(code in (200, 201), '提供方登记成功', (code, body))

    # 4) 只读不变式基线：库内容 + revision 快照
    baseline = db_dump()
    _code, before_state = request('GET', '/api/state')
    ok(4, '库内容与 revision 基线快照完成')

    # 5) 首填正常链路：stub 返回合法 operations → 200 envelope 字段齐全、session/round 下发
    STUB_STATE['content'] = json.dumps(FILL_R1, ensure_ascii=False)
    code, body = assist_context_token('ontology', 'object')
    check(code == 200 and body['context']['modelReady'] is True, '有模型 context 200', (code, body))
    fp1 = body['contextFingerprint']
    token_r1 = body['contextToken']
    code, body = fill_generate(token_r1, {}, intent=INTENT_R1, request_id='af-5')
    check(code == 200, '首填应 200', (code, body))
    check(body.get('protocol') == 'autofill/1', '响应携带 autofill/1 协议标识', body.get('protocol'))
    check(body.get('status') == 'ok', '首填 status ok', body.get('status'))
    for key in ('requestId', 'formId', 'schemaVersion', 'schemaDigest', 'target',
                'draftFingerprint', 'contextFingerprint', 'sessionId', 'roundId',
                'operations', 'questions', 'unresolved', 'summary', 'meta'):
        check(key in body, 'envelope 缺少字段 %s' % key, sorted(body))
    check(body['formId'] == 'object' and body['schemaVersion'] == 1, 'formId/schemaVersion', body)
    check(isinstance(body['schemaDigest'], str) and len(body['schemaDigest']) == 64,
          'schemaDigest 为 sha256 hex', body.get('schemaDigest'))
    check(body['target'] == {'space': 'ontology', 'targetKind': 'object', 'targetId': ''},
          'target 回显', body.get('target'))
    check(body['contextFingerprint'] == fp1, 'contextFingerprint 与 context 一致')
    check(isinstance(body['draftFingerprint'], str) and len(body['draftFingerprint']) == 64,
          'draftFingerprint 为 sha256 hex')
    session_id, round_id = body['sessionId'], body['roundId']
    check(session_id.startswith('s_') and round_id.startswith('r_'),
          'sessionId/roundId 下发', (session_id, round_id))
    check(len(body['operations']) == 2
          and body['operations'][0]['field'] == 'label'
          and body['operations'][0]['value'] == '储能簇'
          and body['operations'][0]['basis']['kind'] == 'intent',
          '合法 operations 原样下发', body.get('operations'))
    check(len(body['questions']) == 1 and body['questions'][0]['id'] == 'q_1'
          and body['questions'][0]['fields'] == ['comment']
          and body['questions'][0]['options'] == ['设备', '部件']
          and body['questions'][0]['allowUnsure'] is True,
          '问题签发（服务端换发 q_1）', body.get('questions'))
    check(body['unresolved'] == [] and '变更' in body['summary'], 'unresolved 空 + summary', body.get('summary'))
    check(body['requestId'] == 'af-5', 'requestId 回传')
    check('stub-key' not in json.dumps(body, ensure_ascii=False), '密钥不出现在响应')
    check('trace' not in body, '原始 trace 不出现在响应')
    check('整表自动填写' in STUB_STATE['last_system'], 'fill 专用系统提示已启用')
    check('answeredQuestions' in STUB_STATE['last_user'], 'fill 用户载荷结构')
    check(STUB_STATE['hits'] >= 1, '模型桩被真实调用')
    ok(5, '首填正常链路（envelope 齐全 + session/round 下发）')

    # 6) 跨轮问题答复成功：应用操作 → draft 变化 → 新 contextToken + sessionId + answers
    STUB_STATE['content'] = json.dumps(FILL_R2_QUESTION_BASIS, ensure_ascii=False)
    code, body = assist_context_token('ontology', 'object', draft=DRAFT_R1_APPLIED)
    check(code == 200, '续轮 context 200', (code, body))
    token_r2 = body['contextToken']
    check(token_r2 != token_r1, '续轮必须新取 contextToken')
    code, body = fill_generate(token_r2, DRAFT_R1_APPLIED, intent='层级按设备',
                               session_id=session_id, request_id='af-6',
                               answers=[{'questionId': 'q_1', 'value': '设备'}])
    check(code == 200 and body.get('status') == 'ok', '跨轮答复续填应 200', (code, body))
    check(len(body['operations']) == 1
          and body['operations'][0]['basis'] == {'kind': 'question', 'questionId': 'q_1'},
          'basis.kind=question 跨轮核验通过', body.get('operations'))
    check(body['sessionId'] == session_id and body['roundId'] != round_id,
          '同一 session 延续、round 更新', (body.get('sessionId'), body.get('roundId')))
    ok(6, '跨轮问题答复成功（question 依据 + 会话延续）')

    # 7) 跨轮问题 id 400：已答问题不可重复作答；未知问题 id 同样拒绝
    code, body = assist_context_token('ontology', 'object', draft=DRAFT_R1_APPLIED)
    token_r3 = body['contextToken']
    code, body = fill_generate(token_r3, DRAFT_R1_APPLIED, intent='再答一次',
                               session_id=session_id, request_id='af-7a',
                               answers=[{'questionId': 'q_1', 'value': '再次回答'}])
    check(code == 400 and body.get('code') == 'INVALID_ARGUMENT'
          and '问题已过期' in str(body.get('error')),
          '已答问题重复作答 400', (code, body))
    code, body = fill_generate(token_r3, DRAFT_R1_APPLIED, intent='伪造问题',
                               session_id=session_id, request_id='af-7b',
                               answers=[{'questionId': 'q_99', 'value': 'x'}])
    check(code == 400 and '问题已过期' in str(body.get('error')), '未知问题 id 400', (code, body))
    code, body = fill_generate(token_r3, DRAFT_R1_APPLIED, intent='无会话答案',
                               request_id='af-7c',
                               answers=[{'questionId': 'q_1', 'value': 'x'}])
    check(code == 400 and '问题已过期' in str(body.get('error')), '会话失效仍带答案 400', (code, body))
    ok(7, '跨轮/未知问题 id → 400 INVALID_ARGUMENT（问题已过期，请重新发起）')

    # 8) 续轮握手：draft 再变化后旧令牌（round-1 的 token_r1）→ 409 CONTEXT_STALE
    draft_r3 = dict(DRAFT_R1_APPLIED, comment='手改后的业务定义')
    code, body = fill_generate(token_r1, draft_r3, intent='x', session_id=session_id,
                               request_id='af-8')
    check(code == 409 and body.get('code') == 'CONTEXT_STALE',
          '应用操作后 draft 变化，旧 token 必须 409', (code, body))
    ok(8, '续轮握手：旧 contextToken + 新 draft → 409 CONTEXT_STALE')

    # 9) 引用存在性核验：候选外引用 → unresolved，不进入 operations
    STUB_STATE['content'] = json.dumps(FILL_LINK_REFS, ensure_ascii=False)
    code, body = assist_context_token('ontology', 'link', target_id='mg:link_demo')
    check(code == 200, 'link 目标 context 200', (code, body))
    token_link = body['contextToken']
    code, body = fill_generate(token_link, {}, intent=INTENT_LINK, request_id='af-9')
    check(code == 200 and body.get('formId') == 'link', 'link 首填 200', (code, body))
    check(len(body['operations']) == 1 and body['operations'][0]['field'] == 'from'
          and body['operations'][0]['value'] == 'mg:demo_device',
          '候选内引用照常下发', body.get('operations'))
    check(len(body['unresolved']) == 1 and body['unresolved'][0]['field'] == 'to'
          and '对象' in body['unresolved'][0]['reason'],
          '候选外引用转 unresolved（含中文原因）', body.get('unresolved'))
    check(body['sessionId'] != session_id, '跨目标签发新会话')
    ok(9, '引用不存在 → unresolved 非写入')

    # 10) 跨用户 token：既有隔离语义——uid 不匹配 409；目标对其他账号不可见按不存在 404
    code, body = assist_context_token('ontology', 'object')
    token_object_a = body['contextToken']
    code, body = fill_generate(token_object_a, {}, intent='x', request_id='af-10a',
                               cookie=COOKIE_B['value'])
    check(code == 409 and body.get('code') == 'CONTEXT_STALE', '跨用户 token（新建目标）409', (code, body))
    code, body = fill_generate(token_link, {}, intent='x', request_id='af-10b',
                               cookie=COOKIE_B['value'])
    check(code == 404 and body.get('code') == 'NOT_FOUND',
          '跨用户已存目标按不存在 404（账号隔离）', (code, body))
    ok(10, '跨用户 token 409 / 跨账号目标不可见 404（既有隔离语义）')

    # 11) 模型输出非法：非 JSON → 502；截断 → 502
    STUB_STATE['content'] = '这不是JSON'
    code, body = fill_generate(token_link, {}, intent='x', request_id='af-11a')
    check(code == 502 and body.get('code') == 'MODEL_BAD_RESPONSE', '非 JSON 输出 502', (code, body))
    STUB_STATE['content'] = json.dumps({'operations': [], 'questions': [], 'unresolved': []})
    STUB_STATE['finish'] = 'length'
    code, body = fill_generate(token_link, {}, intent='x', request_id='af-11b')
    STUB_STATE['finish'] = 'stop'
    check(code == 502 and body.get('code') == 'MODEL_BAD_RESPONSE', '截断输出 502', (code, body))
    ok(11, 'stub 非法 JSON / 截断 → 502 MODEL_BAD_RESPONSE')

    # 12) 只读不变式：以上全部生成前后库内容与 revision 不变（直读库断言）
    after = db_dump()
    _code, after_state = request('GET', '/api/state')
    check(after == baseline, '生成前后库内容零变化（直读 SQLite 断言）',
          [t for t in set(baseline) | set(after) if baseline.get(t) != after.get(t)])
    check(after_state['revision'] == before_state['revision'], '生成前后 revision 不变')
    ok(12, '前后 revision 与库内容不变（读库断言）')

    # 13) 目标不可见/已删除 → 404（沿用既有隔离语义）
    code, body = assist_context_token('ontology', 'link', target_id='mg:link_missing')
    check(code == 404 and body.get('code') == 'NOT_FOUND', '不存在目标 context 404', (code, body))
    _code, state_body = request('GET', '/api/state')
    state, revision = state_body['state'], state_body['revision']
    state['ontology']['linkTypes'] = [r for r in state['ontology']['linkTypes']
                                      if r['id'] != 'mg:link_demo']
    state['ontology']['definitionOrder'] = [i for i in state['ontology']['definitionOrder']
                                            if i != 'mg:link_demo']
    code, saved = request('POST', '/api/save', {'state': state, 'revision': revision})
    check(code == 200, '删除链接目标应 200', (code, saved))
    code, body = fill_generate(token_link, {}, intent='x', request_id='af-13')
    check(code == 404 and body.get('code') == 'NOT_FOUND', '目标删除后 generate 404', (code, body))
    ok(13, '目标不存在/已删除 → 404 NOT_FOUND')

    # 14) 收尾生成（empty）同样零写入
    baseline2 = db_dump()
    STUB_STATE['content'] = json.dumps({'operations': [], 'questions': [], 'unresolved': []})
    code, body = assist_context_token('ontology', 'object')
    code, body = fill_generate(body['contextToken'], {}, intent='没有任何要求', request_id='af-14')
    check(code == 200 and body.get('status') == 'empty', '无操作无问题 → 200 empty', (code, body))
    check(body['operations'] == [] and body['questions'] == [] and body['unresolved'] == [],
          'empty 响应三数组为空', body)
    check(db_dump() == baseline2, 'empty 生成同样零写入')
    ok(14, '有效请求无内容 200 status=empty 且零写入')

    print(f'\n全部通过（{len(PASSED)} 组）')


if __name__ == '__main__':
    try:
        check_contract_group_expansion()
        print('通过 0) FormContract 组节点 atomicGroup 展开为叶子路径全集')
        main()
    finally:
        shutdown()
