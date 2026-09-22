"""辅助填写接口 HTTP 集成回归（T4）：assist-context / assist-generate 真实链路。

隔离规则（沿用 test_export_restore_http.py）：
- 纯标准库；临时数据根（WIZ_WORKBENCH_ROOT）+ 独立端口子进程服务；
- 模型桩：本地线程起一个 OpenAI 兼容 /chat/completions 假端点，提供方配置指向它——
  覆盖"真实网络调用链"（经授权配置之外的合成桩，不代表真实模型联调）；
- 不读写真实业务数据，不连真实 LLM/MySQL/Redis。

覆盖：
- 401 未登录 / 400 形态与白名单 / 404 目标不存在；
- 409 CONTEXT_STALE：篡改令牌、draft 变化、权威状态变化；
- 422 MODEL_NOT_CONFIGURED：未配置提供方；
- 200 happy path（mode=fill 携 protocol:2 走 autofill/1，桩返回合法 operations JSON；
  2026-09-22 整表自动填写改版后旧 suggestions 协议已停用，本文件 fill 用例按新协议最小迁移）；
- 502 MODEL_BAD_RESPONSE（桩返回非 JSON）；
- 504 MODEL_TIMEOUT（provider timeout=1s，桩延迟 3s）；
- 只读不变式：建议生成前后本体草稿 revision 与内容不变。
运行：python3 tests/test_assist_api.py
"""
import json
import os
import socket
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
PORT = 18911
STUB_PORT = 18912
BASE = f'http://127.0.0.1:{PORT}'
TMP = Path(tempfile.mkdtemp(prefix='wiz_assist_api_'))
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
        STUB_HTTPD = None


# --- 模型桩：OpenAI 兼容 chat/completions -------------------------------------

STUB_STATE = {'content': '{}', 'delay': 0, 'hits': 0}


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
        if STUB_STATE['delay']:
            time.sleep(STUB_STATE['delay'])
        self._respond(200, {'choices': [{'message': {'content': STUB_STATE['content']},
                                         'finish_reason': 'stop'}]})

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

COOKIE = {'value': ''}


def request(method, path, payload=None, with_origin=None):
    data = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    headers = {'Content-Type': 'application/json'}
    if COOKIE['value']:
        headers['Cookie'] = 'wiz_session=' + COOKIE['value']
    if method == 'POST':
        headers['Origin'] = with_origin or BASE
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode())
        except ValueError:
            return exc.code, {}


def main():
    global PROC
    probe = socket.socket()
    try:
        probe.bind(('127.0.0.1', PORT))
    except OSError as exc:
        print(f'[失败] 端口 {PORT} 已被占用：{exc}')
        sys.exit(1)
    finally:
        probe.close()
    start_stub()
    env = dict(os.environ, WIZ_WORKBENCH_ROOT=str(TMP), WIZ_WORKBENCH_PORT=str(PORT))
    PROC = subprocess.Popen([sys.executable, '-m', 'workbench.server'], cwd=str(REPO), env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    auth_client.wait_ready(BASE)
    _user, cookie = auth_client.register_or_login(BASE, 'assist_api', 'assist1234')
    COOKIE['value'] = cookie

    # 1) 未登录 401
    saved = COOKIE['value']
    COOKIE['value'] = ''
    code, body = request('POST', '/api/assist-context',
                         {'space': 'ontology', 'targetKind': 'object', 'purpose': 'fill', 'draft': {}})
    check(code == 401 and body.get('code') == 'UNAUTHENTICATED', '未登录应 401', body)
    COOKIE['value'] = saved
    ok(1, '未登录 401 UNAUTHENTICATED')

    # 2) 未知 targetKind / 白名单外 draft 键 → 400
    code, body = request('POST', '/api/assist-context',
                         {'space': 'ontology', 'targetKind': 'nope', 'purpose': 'fill', 'draft': {}})
    check(code == 400, '未知 targetKind 应 400', (code, body))
    code, body = request('POST', '/api/assist-context',
                         {'space': 'ontology', 'targetKind': 'object', 'purpose': 'fill',
                          'draft': {'hacked': 'x'}})
    check(code == 400 and '白名单' in str(body.get('error')), 'draft 白名单外键应 400', (code, body))
    ok(2, '400 形态/白名单校验')

    # 3) 未配置模型：context 仍 200 且 modelReady=false；generate 422
    code, body = request('POST', '/api/assist-context',
                         {'space': 'ontology', 'targetKind': 'object', 'purpose': 'fill', 'draft': {}})
    check(code == 200 and body['context']['modelReady'] is False, '无模型 context 200/modelReady=false', (code, body))
    token = body['contextToken']
    code, body = request('POST', '/api/assist-generate',
                         {'requestId': 'r1', 'contextToken': token, 'mode': 'fill',
                          'protocol': 2, 'draft': {}, 'intent': '帮我写对象定义'})
    check(code == 422 and body.get('code') == 'MODEL_NOT_CONFIGURED', '无模型 generate 422', (code, body))
    ok(3, '未配置模型 422 MODEL_NOT_CONFIGURED')

    # 4) 登记提供方（指向本地模型桩）→ context modelReady=true
    code, body = request('POST', '/api/llm-provider-save',
                         {'name': '桩模型', 'endpoint': f'http://127.0.0.1:{STUB_PORT}/v1/chat/completions',
                          'model': 'stub-model', 'apiKey': 'stub-key', 'timeout': 5, 'isDefault': True})
    check(code in (200, 201), '提供方登记成功', (code, body))
    code, body = request('POST', '/api/assist-context',
                         {'space': 'ontology', 'targetKind': 'object', 'purpose': 'fill', 'draft': {}})
    check(code == 200 and body['context']['modelReady'] is True, '有模型 modelReady=true', (code, body))
    token = body['contextToken']
    fp = body['contextFingerprint']
    ok(4, '提供方登记与 modelReady')

    # 5) 篡改令牌 → 409
    code, body = request('POST', '/api/assist-generate',
                         {'requestId': 'r2', 'contextToken': token[:-2] + 'xx', 'mode': 'fill',
                          'protocol': 2, 'draft': {}})
    check(code == 409 and body.get('code') == 'CONTEXT_STALE', '篡改令牌 409', (code, body))
    ok(5, '篡改令牌 409 CONTEXT_STALE')

    # 6) happy path：桩返回合法 operations（autofill/1）→ 200 ok；且只读不变式
    fill_output = {'operations': [
        {'op': 'set', 'field': 'comment',
         'value': '由若干电池包并联组成、可整体参与充放电的储能单元。',
         'basis': {'kind': 'intent', 'quote': '储能簇是一组电池包，可以整体充放电'}}],
        'questions': [], 'unresolved': []}
    STUB_STATE['content'] = json.dumps(fill_output, ensure_ascii=False)
    _code, before = request('GET', '/api/state')
    code, body = request('POST', '/api/assist-generate',
                         {'requestId': 'r3', 'contextToken': token, 'mode': 'fill',
                          'protocol': 2, 'draft': {},
                          'intent': '储能簇是一组电池包，可以整体充放电'})
    check(code == 200 and body.get('status') == 'ok', 'generate 200 ok', (code, body))
    check(body.get('protocol') == 'autofill/1', 'fill 响应携带 autofill/1 协议标识', body.get('protocol'))
    check(body['operations'] and body['operations'][0]['field'] == 'comment',
          '合法操作下发', body.get('operations'))
    check(body.get('contextFingerprint') == fp, '指纹一致', (fp, body.get('contextFingerprint')))
    check(STUB_STATE['hits'] >= 1, '模型桩被真实调用')
    _code, after = request('GET', '/api/state')
    check(before == after, '生成前后本体草稿零变化（只读不变式）')
    ok(6, 'happy path + 只读不变式')

    # 7) draft 变化 → 409
    code, body = request('POST', '/api/assist-generate',
                         {'requestId': 'r4', 'contextToken': token, 'mode': 'fill',
                          'protocol': 2, 'draft': {'label': ' changed'}, 'intent': 'x'})
    check(code == 409 and body.get('code') == 'CONTEXT_STALE', 'draft 变化 409', (code, body))
    ok(7, 'draft 变化 409 CONTEXT_STALE')

    # 8) 模型输出非 JSON → 502
    STUB_STATE['content'] = '这不是JSON'
    code, body = request('POST', '/api/assist-generate',
                         {'requestId': 'r5', 'contextToken': token, 'mode': 'fill',
                          'protocol': 2, 'draft': {}})
    check(code == 502 and body.get('code') == 'MODEL_BAD_RESPONSE', '非 JSON 输出 502', (code, body))
    ok(8, '模型输出非 JSON 502 MODEL_BAD_RESPONSE')

    # 9) 超时 → 504（登记 1s 超时提供方并设为默认；桩延迟 3s）
    code, body = request('POST', '/api/llm-provider-save',
                         {'name': '慢桩', 'endpoint': f'http://127.0.0.1:{STUB_PORT}/v1/chat/completions',
                          'model': 'stub-slow', 'apiKey': 'stub-key', 'timeout': 1, 'isDefault': True})
    check(code in (200, 201), '慢速提供方登记成功', (code, body))
    _code, body = request('POST', '/api/assist-context',
                          {'space': 'ontology', 'targetKind': 'object', 'purpose': 'fill', 'draft': {}})
    token_slow = body['contextToken']
    STUB_STATE['delay'] = 3
    code, body = request('POST', '/api/assist-generate',
                         {'requestId': 'r6', 'contextToken': token_slow, 'mode': 'fill',
                          'protocol': 2, 'draft': {}})
    STUB_STATE['delay'] = 0
    check(code == 504 and body.get('code') == 'MODEL_TIMEOUT', '超时 504', (code, body))
    ok(9, '模型超时 504 MODEL_TIMEOUT')

    # 10) 空建议 → 200 empty
    STUB_STATE['content'] = json.dumps({'operations': [], 'questions': [], 'unresolved': []},
                                       ensure_ascii=False)
    code, body = request('POST', '/api/assist-generate',
                         {'requestId': 'r7', 'contextToken': token, 'mode': 'fill',
                          'protocol': 2, 'draft': {}})
    check(code == 200 and body.get('status') == 'empty', '空建议 200 empty', (code, body))
    ok(10, '有效请求无建议 200 status=empty')

    print(f'\n全部通过（{len(PASSED)} 组）')


if __name__ == '__main__':
    try:
        main()
    finally:
        shutdown()
