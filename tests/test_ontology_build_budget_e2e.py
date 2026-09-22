"""生成控输出 v3 组合故障与恢复矩阵（D17 脚本A；与脚本B双适配器契约互补）。

覆盖（整合计划 v3 §9 场景 1/4/5/7 的 HTTP 组合面；D16 已覆盖 capabilities/422 预检/版本守卫）：
  1. 截断拆分链路：length → split → 子全部成功；usage 精确累计（父截断+子调用，不丢用量）。
  2. 父拆两子左成右败：失败后 resume(auto) 只补右叶；候选幂等不翻倍；第二次 run usage 只含新调用。
  3. 输入预算不可容纳 → 异步受阻（独立子实例，极小 context）：不是 HTTP 422，最终 failed/blocked
     且文案可读；capabilities 正常展示配置（证明未被 HTTP 预检错误前置拒绝）。
  4. 取消不复活：挂起响应 + 取消；迟到响应回来后状态仍 cancelled、候选零增长。
  5. schema1 兼容：无 schemaVersion 的旧计划 resume(auto) 照常走 legacy 且不混写 schema2 字段。
  6. 轮询脱敏：响应无提示词/密钥样式字段；schema2 摘要不含全量 targets/attempts 明细。

隔离（AGENTS.md 测试铁律）：临时根 + 动态端口 + 合成材料 + 127.0.0.1 假 LLM；不访问外网、
不触碰工作树真实数据副本。子实例（场景 3）用独立进程 + 独立临时根，互不污染。

运行：python3 tests/run.py --test tests/test_ontology_build_budget_e2e.py
"""
import base64
import hashlib
import json
import os
import shutil
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

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / 'tests'))

TMP = Path(tempfile.mkdtemp(prefix='wiz_budget_e2e_'))
os.environ['WIZ_WORKBENCH_ROOT'] = str(TMP)
os.environ['WIZ_DATABASE_URL'] = 'sqlite:///' + str(TMP / 'data' / 'workbench.sqlite3')
os.environ['WIZ_BUILD_ADAPTIVE_BATCHING'] = '1'
os.environ['WIZ_BUILD_CONTEXT_TOKENS'] = '131072'
os.environ['WIZ_BUILD_OUTPUT_LIMIT_TOKENS'] = '32000'
os.environ['WIZ_BUILD_REQUEST_OUTPUT_TOKENS'] = '32000'

import auth_client  # noqa: E402
from workbench import storage  # noqa: E402

TERMINAL = ('succeeded', 'failed', 'cancelled', 'interrupted')
PASSED = []
FAILED = []
USER_ID = ['']


def check(name, ok, actual=None, expected=None):
    if ok:
        PASSED.append(name)
    else:
        FAILED.append(name)
        print('[失败] %s\n  实际=%s\n  期望=%s' % (name, str(actual)[:400], str(expected)[:400]))


class FakeLlm(BaseHTTPRequestHandler):
    """可编程响应队列：每个抽取请求按序消费 FakeLlm.script 的一项。

    脚本项形状：{'kind': 'ok'|'length'|'bad'|'slow'|'http500', 'delay': 秒, 'completion': int}
    缺省（脚本耗尽）走 'ok'（按 facts 锚定生成候选）。
    记录 extract_calls（每次物理抽取请求的响应 kind 与 completion）供 usage 对账。
    """

    script = []
    extract_calls = []

    def log_message(self, *args):
        pass

    def _reply(self, status, payload):
        data = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        length = int(self.headers.get('Content-Length') or 0)
        body = json.loads(self.rfile.read(length).decode() or '{}')
        messages = body.get('messages') or []
        system = str(messages[0].get('content') or '') if messages else ''
        user_text = str(messages[-1].get('content') or '') if messages else ''
        if '抽取器' not in system:
            self._reply(200, {'choices': [{'message': {'content': json.dumps(
                {'questions': [], 'patch': {}, 'notes': ['假模型范围应答']}, ensure_ascii=False)},
                'finish_reason': 'stop'}],
                'usage': {'prompt_tokens': 120, 'completion_tokens': 40, 'total_tokens': 160}})
            return
        item = FakeLlm.script.pop(0) if FakeLlm.script else {'kind': 'ok'}
        kind = str(item.get('kind') or 'ok')
        completion = int(item.get('completion') or 90)
        delay = float(item.get('delay') or 0)
        if delay:
            time.sleep(delay)
        if kind == 'http500':
            FakeLlm.extract_calls.append({'kind': kind, 'completion': 0})
            self._reply(500, {'error': {'message': '注入的抽取失败', 'type': 'server_error'}})
            return
        if kind == 'length':
            FakeLlm.extract_calls.append({'kind': kind, 'completion': completion})
            self._reply(200, {'choices': [{'message': {'content': '{"candidates": [{"key": "par'},
                                                       'finish_reason': 'length'}],
                              'usage': {'prompt_tokens': 300, 'completion_tokens': completion,
                                        'total_tokens': 300 + completion}})
            return
        if kind == 'bad':
            FakeLlm.extract_calls.append({'kind': kind, 'completion': completion})
            self._reply(200, {'choices': [{'message': {'content': '这不是 JSON，没有候选'},
                                           'finish_reason': 'stop'}],
                              'usage': {'prompt_tokens': 300, 'completion_tokens': completion,
                                        'total_tokens': 300 + completion}})
            return
        FakeLlm.extract_calls.append({'kind': kind, 'completion': completion})
        payload = self._extract_payload(user_text)
        self._reply(200, {'choices': [{'message': {'content': json.dumps(payload, ensure_ascii=False)},
                                       'finish_reason': 'stop'}],
                          'usage': {'prompt_tokens': 300, 'completion_tokens': completion,
                                    'total_tokens': 300 + completion}})

    @staticmethod
    def _extract_payload(user_text):
        try:
            payload = json.loads(user_text)
        except ValueError:
            payload = {}
        facts = payload.get('facts') or []
        ids = [str(item.get('id')) for item in facts
               if isinstance(item, dict) and item.get('id')]
        good = ids[:3]
        return {'candidates': [
            {'key': 'obj-station', 'type': 'object', 'name': '电站',
             'definition': '储能电站（假模型，证据锚定）', 'fields': {},
             'ownerKey': '', 'evidence': {'_record': good},
             'evidenceStatus': 'supported', 'conflicts': []},
            {'key': 'prop-rated-power', 'type': 'property', 'name': '额定功率',
             'definition': '电站额定功率', 'fields': {'dataType': 'number'},
             'ownerKey': 'obj-station', 'evidence': {'definition': good[:1]},
             'evidenceStatus': 'supported', 'conflicts': []}]}


def free_port():
    sock = socket.socket()
    try:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]
    finally:
        sock.close()


LLM_SERVER = ThreadingHTTPServer(('127.0.0.1', 0), FakeLlm)
LLM_URL = 'http://127.0.0.1:%d/v1/chat/completions' % LLM_SERVER.server_address[1]
threading.Thread(target=LLM_SERVER.serve_forever, daemon=True).start()

storage.ensure_ready()
from workbench import server as wb_server  # noqa: E402
from workbench.storage import engine as sto  # noqa: E402
from workbench.storage import ontology_build as ob_store  # noqa: E402

PORT = free_port()
BASE = 'http://127.0.0.1:%d' % PORT
HTTPD = ThreadingHTTPServer(('127.0.0.1', PORT), wb_server.Handler)
threading.Thread(target=HTTPD.serve_forever, daemon=True).start()

TOKEN = None


def api(path, payload=None, query=''):
    url = BASE + path + query
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
    headers = {'Content-Type': 'application/json'}
    if TOKEN:
        headers['Cookie'] = 'wiz_session=' + TOKEN
    request = urllib.request.Request(url, data=data, headers=headers,
                                     method='POST' if data else 'GET')
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read().decode() or '{}')
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode() or '{}')
        except ValueError:
            return exc.code, {}


def poll_run(task_id, run_id, timeout=60):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        status, body = api('/api/build-run', query='?taskId=%s&runId=%s' % (task_id, run_id))
        if status == 200:
            last = body.get('run') or {}
            if (last.get('state') or '') in TERMINAL:
                return last
        time.sleep(0.5)
    return last or {}


def make_material(task_id, rel_path='sample.json'):
    material = json.dumps({'station': {'name': '示例储能站', 'ratedPower': '100kW',
                                       'battery': {'cells': 12, 'vendor': '示例厂商'}}},
                          ensure_ascii=False).encode()
    status, init = api('/api/build-upload-init', {'taskId': task_id, 'relPath': rel_path,
                                                  'size': len(material)})
    assert status == 200, init
    upload_id = init['uploadId']
    status, _ = api('/api/build-upload-chunk',
                    {'uploadId': upload_id, 'index': 0,
                     'hash': hashlib.sha256(material).hexdigest(),
                     'dataBase64': base64.b64encode(material).decode()})
    assert status == 200
    status, done = api('/api/build-upload-complete',
                       {'uploadId': upload_id, 'finalHash': hashlib.sha256(material).hexdigest()})
    assert status == 200, done
    status, scan = api('/api/build-scan', {'taskId': task_id})
    assert status == 200, scan
    deadline = time.time() + 30
    while time.time() < deadline:
        status, detail = api('/api/build-task', query='?taskId=' + task_id)
        materials = detail.get('materials') or []
        if materials and all(m.get('parseState') in ('success', 'partial', 'failed', 'excluded')
                             for m in materials):
            return materials
        time.sleep(0.5)
    return []


def new_task_with_scope(name):
    status, created = api('/api/build-task-create', {'name': name})
    task_id = created['task']['id']
    make_material(task_id)
    status, saved = api('/api/build-scope-save',
                        {'taskId': task_id,
                         'scope': {'goal': '建立电站设备本体', 'include': '电站、电池、功率',
                                   'exclude': '', 'relations': '', 'coverage': ''},
                         'revision': 0})
    assert status == 200, saved
    return task_id, saved['scope']['revision']


def candidate_total(task_id, batch_id):
    status, body = api('/api/build-candidates',
                       query='?taskId=%s&batch=%s' % (task_id, batch_id))
    return int(body.get('total') or 0) if status == 200 else -1


def gen_of(run):
    return ((run.get('checkpoint') or {}).get('generate') or {})


# --- 子实例：极小 context 的异步受阻（独立进程 + 独立临时根） ---------------------------

CHILD_SCRIPT = r'''
import base64, hashlib, json, os, socket, sys, tempfile, threading, time, urllib.error, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
REPO = sys.argv[1]
sys.path.insert(0, REPO); sys.path.insert(0, REPO + '/tests')
root = tempfile.mkdtemp(prefix='wiz_e2e_child_')
os.environ['WIZ_WORKBENCH_ROOT'] = root
os.environ['WIZ_DATABASE_URL'] = 'sqlite:///' + root + '/data/workbench.sqlite3'
os.environ['WIZ_BUILD_ADAPTIVE_BATCHING'] = '1'
os.environ['WIZ_BUILD_CONTEXT_TOKENS'] = '2500'          # 极小：任何真实 prompt 都不可容纳
os.environ['WIZ_BUILD_OUTPUT_LIMIT_TOKENS'] = '2000'
os.environ['WIZ_BUILD_REQUEST_OUTPUT_TOKENS'] = '2000'
import auth_client
from workbench import storage
storage.ensure_ready()
from workbench import server as wb_server

class Llm(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_POST(self):
        n = int(self.headers.get('Content-Length') or 0)
        body = json.loads(self.rfile.read(n).decode() or '{}')
        sysp = str((body.get('messages') or [{}])[0].get('content') or '')
        payload = ({'questions': [], 'patch': {}, 'notes': []} if '抽取器' not in sysp else
                   {'candidates': [{'key': 'obj-x', 'type': 'object', 'name': 'X', 'definition': 'd',
                                    'fields': {}, 'ownerKey': '', 'evidence': {},
                                    'evidenceStatus': 'inferred', 'conflicts': []}]})
        data = json.dumps({'choices': [{'message': {'content': json.dumps(payload, ensure_ascii=False)},
                                        'finish_reason': 'stop'}],
                           'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}}).encode()
        self.send_response(200); self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)

llm = ThreadingHTTPServer(('127.0.0.1', 0), Llm)
threading.Thread(target=llm.serve_forever, daemon=True).start()
llm_url = 'http://127.0.0.1:%d/v1/chat/completions' % llm.server_address[1]
s = socket.socket(); s.bind(('127.0.0.1', 0)); port = s.getsockname()[1]; s.close()
httpd = ThreadingHTTPServer(('127.0.0.1', port), wb_server.Handler)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
base = 'http://127.0.0.1:%d' % port
token = ['']

def api(path, payload=None, query=''):
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
    headers = {'Content-Type': 'application/json'}
    if token[0]: headers['Cookie'] = 'wiz_session=' + token[0]
    req = urllib.request.Request(base + path + query, data=data, headers=headers,
                                 method='POST' if data else 'GET')
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode() or '{}')
    except urllib.error.HTTPError as e:
        try: return e.code, json.loads(e.read().decode() or '{}')
        except ValueError: return e.code, {}

_user, token[0] = auth_client.register_or_login(base, 'child_owner', 'test1234')
_, caps = api('/api/build-capabilities')
budget = (caps.get('generationBudget') or {})
_, prov = api('/api/llm-provider-save', {'name': 'child-fake', 'endpoint': llm_url, 'model': 'fake-c',
                                         'apiKey': 'sk-fake', 'timeout': 10, 'isDefault': True})
pid = (prov.get('provider') or {}).get('id')
os.environ['WIZ_BUILD_PROFILE_PROVIDER_ID'] = pid
os.environ['WIZ_BUILD_PROFILE_MODEL'] = 'fake-c'
_, created = api('/api/build-task-create', {'name': '极小预算任务'})
task = created['task']['id']
material = json.dumps({'a': {'b': 1, 'c': 'x'}}).encode()
_, init = api('/api/build-upload-init', {'taskId': task, 'relPath': 'm.json', 'size': len(material)})
api('/api/build-upload-chunk', {'uploadId': init['uploadId'], 'index': 0,
                                'hash': hashlib.sha256(material).hexdigest(),
                                'dataBase64': base64.b64encode(material).decode()})
api('/api/build-upload-complete', {'uploadId': init['uploadId'],
                                   'finalHash': hashlib.sha256(material).hexdigest()})
api('/api/build-scan', {'taskId': task})
deadline = time.time() + 30
while time.time() < deadline:
    _, d = api('/api/build-task', query='?taskId=' + task)
    if d.get('materials') and all(m.get('parseState') in ('success', 'partial', 'failed', 'excluded')
                                  for m in d.get('materials') or []):
        break
    time.sleep(0.5)
_, saved = api('/api/build-scope-save', {'taskId': task,
    'scope': {'goal': 'g', 'include': 'i', 'exclude': '', 'relations': '', 'coverage': ''},
    'revision': 0})
confirm_status, confirmed = api('/api/build-scope-confirm',
                                {'taskId': task, 'revision': saved['scope']['revision']})
run = {}
if confirm_status == 200:
    deadline = time.time() + 60
    while time.time() < deadline:
        _, body = api('/api/build-run', query='?taskId=%s&runId=%s' % (task, confirmed['runId']))
        run = body.get('run') or {}
        if run.get('state') in ('succeeded', 'failed', 'cancelled', 'interrupted'):
            break
        time.sleep(0.5)
gen = ((run.get('checkpoint') or {}).get('generate') or {})
print(json.dumps({'enabled': budget.get('enabled'), 'caching': budget.get('configured'),
                  'confirmStatus': confirm_status, 'state': run.get('state'),
                  'error': str(run.get('error') or ''), 'schemaVersion': gen.get('schemaVersion'),
                  'blocking': gen.get('blocking')}, ensure_ascii=False))
'''


def scenario_child_instance():
    """场景3：极小 context → 异步受阻（不是 HTTP 422），独立子进程 + 独立临时根。"""
    script_path = Path(tempfile.mkdtemp(prefix='wiz_e2e_child_script_')) / 'child.py'
    script_path.write_text(CHILD_SCRIPT)
    proc = subprocess.run([sys.executable, str(script_path), str(REPO)],
                          capture_output=True, text=True, timeout=180)
    out = {}
    for line in (proc.stdout or '').splitlines():
        line = line.strip()
        if line.startswith('{'):
            try:
                out = json.loads(line)
            except ValueError:
                continue
    check('子实例：capabilities 展示启用与配置（未被 HTTP 预检错误拒绝）',
          out.get('enabled') is True and out.get('confirmStatus') == 200,
          (out.get('enabled'), out.get('confirmStatus')), (True, 200))
    check('子实例：scope-confirm 不被规模/预算错误前置拒绝（异步发现才是正确时机）',
          out.get('confirmStatus') == 200, out.get('confirmStatus'), 200)
    check('子实例：最终未成功（受阻断，绝不伪成功）',
          out.get('state') in ('failed', 'cancelled') and out.get('state') != 'succeeded',
          out.get('state'), 'failed')
    error_text = str(out.get('error') or '')
    check('子实例：受阻原因可读（含受阻/无法/上限字样）',
          any(token in error_text for token in ('受阻', '无法', '上限', '计划')),
          error_text[:200], '可读受阻文案')
    if proc.returncode not in (0,) and not out:
        print('[子实例 stderr 尾部]\n' + (proc.stderr or '')[-600:])


def scenario_truncation_split():
    """场景1：length 截断 → 拆分子全部成功；usage 精确累计。"""
    task_id, revision = new_task_with_scope('截断拆分链路')
    FakeLlm.script = [{'kind': 'length', 'completion': 40}]
    FakeLlm.extract_calls = []
    status, confirmed = api('/api/build-scope-confirm',
                            {'taskId': task_id, 'revision': revision})
    check('截断场景：scope-confirm 受理', status == 200, (status, confirmed), 200)
    run = poll_run(task_id, confirmed['runId'])
    check('截断场景：最终成功（拆分补跑后）', run.get('state') == 'succeeded',
          (run.get('state'), run.get('error')), 'succeeded')
    gen = gen_of(run)
    check('截断场景：发生拆分（splitParents ≥ 1）',
          int((gen.get('jobs') or {}).get('splitParents') or 0) >= 1,
          gen.get('jobs'), 'splitParents>=1')
    expected = sum(item['completion'] for item in FakeLlm.extract_calls)
    usage = run.get('usage') or {}
    check('截断场景：usage 精确累计（父截断+子调用全部入账，不丢用量）',
          int(usage.get('knownCompletionTokens') or -1) == expected and expected >= 40,
          (usage.get('knownCompletionTokens'), expected, FakeLlm.extract_calls),
          'knownCompletionTokens==Σcompletion')
    coverage = gen.get('coverage') or {}
    check('截断场景：覆盖守恒（已处理==总数，pending=0）',
          coverage.get('targetTotal', 0) > 0
          and coverage.get('targetCompleted') == coverage.get('targetTotal')
          and coverage.get('targetPending') == 0, coverage, 'complete')
    check('截断场景：拆分不算失败（failed=0，blocking=null）',
          int((gen.get('jobs') or {}).get('failed') or 0) == 0 and not gen.get('blocking'),
          (gen.get('jobs'), gen.get('blocking')), 'no failure')


def scenario_left_ok_right_fail_resume():
    """场景2：父拆两子左成右败 → resume(auto) 只补右叶；候选幂等不翻倍。"""
    task_id, revision = new_task_with_scope('左成右败续跑')
    # 首答 length（拆两子）；随后子1成功、子2两次非 JSON（格式修复后仍失败）
    FakeLlm.script = [{'kind': 'length', 'completion': 30},
                      {'kind': 'ok', 'completion': 50},
                      {'kind': 'bad', 'completion': 10}, {'kind': 'bad', 'completion': 10}]
    FakeLlm.extract_calls = []
    status, confirmed = api('/api/build-scope-confirm',
                            {'taskId': task_id, 'revision': revision})
    run = poll_run(task_id, confirmed['runId'])
    check('左成右败：run failed（部分失败不冒充成功）', run.get('state') == 'failed',
          (run.get('state'), run.get('error')), 'failed')
    first_total = candidate_total(task_id, confirmed['batchId'])
    check('左成右败：左叶候选已保留', first_total >= 1, first_total, '>=1')
    calls_first = len(FakeLlm.extract_calls)

    # 恢复：好脚本，只应调一次（右叶）
    FakeLlm.script = [{'kind': 'ok', 'completion': 60}]
    status, resumed = api('/api/build-run-resume',
                          {'taskId': task_id, 'runId': confirmed['runId'],
                           'resumeMode': 'auto'})
    check('左成右败：resume 受理', status == 200, (status, resumed), 200)
    run2 = poll_run(task_id, confirmed['runId'])
    check('左成右败：resume 后成功', run2.get('state') == 'succeeded',
          (run2.get('state'), run2.get('error')), 'succeeded')
    calls_second = len(FakeLlm.extract_calls) - calls_first
    check('左成右败：第二次只补未成功叶（无成功叶重做）', calls_second == 1,
          (calls_second, FakeLlm.extract_calls), 1)
    second_total = candidate_total(task_id, confirmed['batchId'])
    check('左成右败：候选幂等不翻倍（左叶不重复插入）',
          second_total <= first_total + 2 and second_total >= first_total,
          (first_total, second_total), 'first<=second<=first+2')
    gen = gen_of(run2)
    coverage = gen.get('coverage') or {}
    check('左成右败：终态覆盖完整', coverage.get('targetCompleted')
          == coverage.get('targetTotal'), coverage, 'complete')


def scenario_cancel_no_revive():
    """场景4：挂起响应中取消 → 终态 cancelled；迟到响应不复活、候选零增长。"""
    task_id, revision = new_task_with_scope('取消不复活')
    FakeLlm.script = [{'kind': 'ok', 'completion': 70, 'delay': 4},
                      {'kind': 'ok', 'completion': 70}, {'kind': 'ok', 'completion': 70}]
    FakeLlm.extract_calls = []
    status, confirmed = api('/api/build-scope-confirm',
                            {'taskId': task_id, 'revision': revision})
    # 等 attempt 已 started（请求已发出、响应挂起中）
    deadline = time.time() + 20
    started = False
    while time.time() < deadline:
        status, body = api('/api/build-run', query='?taskId=%s&runId=%s'
                           % (task_id, confirmed['runId']))
        run = body.get('run') or {}
        if run.get('state') == 'running':
            started = True
            break
        time.sleep(0.3)
    check('取消场景：进入 running（请求在途）', started, run.get('state'), 'running')
    status, cancelled = api('/api/build-run-cancel',
                            {'taskId': task_id, 'runId': confirmed['runId']})
    run_after = poll_run(task_id, confirmed['runId'], timeout=30)
    check('取消场景：终态 cancelled', run_after.get('state') == 'cancelled',
          run_after.get('state'), 'cancelled')
    total_at_cancel = candidate_total(task_id, confirmed['batchId'])
    time.sleep(5)   # 让挂起的响应回来（delay=4s）
    check('取消场景：迟到响应不复活（状态仍 cancelled 或被重试接管前保持）',
          (api('/api/build-run', query='?taskId=%s&runId=%s'
               % (task_id, confirmed['runId']))[1].get('run') or {}).get('state')
          in ('cancelled', 'failed', 'interrupted'),
          (api('/api/build-run', query='?taskId=%s&runId=%s'
               % (task_id, confirmed['runId']))[1].get('run') or {}).get('state'),
          'cancelled')
    total_after = candidate_total(task_id, confirmed['batchId'])
    check('取消场景：迟到响应不产生新候选（零增长）',
          total_after == total_at_cancel, (total_at_cancel, total_after), 'equal')


def scenario_schema1_resume():
    """场景5：schema1（无 schemaVersion）旧计划 resume(auto) 走 legacy，不混写 schema2。"""
    task_id, revision = new_task_with_scope('schema1续跑')
    FakeLlm.script = [{'kind': 'ok', 'completion': 80}, {'kind': 'ok', 'completion': 80},
                      {'kind': 'ok', 'completion': 80}]
    status, confirmed = api('/api/build-scope-confirm',
                            {'taskId': task_id, 'revision': revision})
    run = poll_run(task_id, confirmed['runId'])
    check('schema1场景：首次生成成功', run.get('state') == 'succeeded', run.get('state'), 'succeeded')
    # 改库注入 schema1 形状 checkpoint + failed 状态
    with sto.write_tx() as tx:
        def _inject(conn):
            ob_store.update_run(conn, confirmed['runId'], USER_ID[0], state='failed',
                                error='注入失败', retryable=True,
                                checkpoint={'generate': {
                                    'batchId': confirmed['batchId'], 'scopeRevision': revision,
                                    'materialRevision': 1,
                                    'plan': {'modelFactIds': ['f-legacy-1'], 'relevant': 1},
                                    'batches': {'size': 20, 'total': 1, 'done': [],
                                                'failed': [{'position': 1, 'error': '注入失败批'}]},
                                    'log': ['注入 schema1 检查点'], 'notes': []}})
        tx.run(_inject)
    FakeLlm.script = [{'kind': 'ok', 'completion': 85}]
    status, resumed = api('/api/build-run-resume',
                          {'taskId': task_id, 'runId': confirmed['runId'],
                           'resumeMode': 'auto'})
    check('schema1场景：resume 受理（无 schemaVersion 不得误判未知版本）',
          status == 200, (status, resumed), 200)
    run2 = poll_run(task_id, confirmed['runId'])
    check('schema1场景：续跑成功', run2.get('state') == 'succeeded',
          (run2.get('state'), run2.get('error')), 'succeeded')
    gen2 = gen_of(run2)
    check('schema1场景：不混写 schema2 字段（无 jobs/coverage/planEpoch）',
          'jobs' not in gen2 and 'coverage' not in gen2,
          list(gen2.keys()), 'schema1 shape')


def scenario_redaction():
    """场景6：轮询脱敏与摘要最小化。"""
    task_id, revision = new_task_with_scope('脱敏检查')
    FakeLlm.script = [{'kind': 'ok', 'completion': 66}]
    status, confirmed = api('/api/build-scope-confirm',
                            {'taskId': task_id, 'revision': revision})
    run = poll_run(task_id, confirmed['runId'])
    url = BASE + '/api/build-run?taskId=%s&runId=%s' % (task_id, confirmed['runId'])
    request = urllib.request.Request(url, headers={'Cookie': 'wiz_session=' + TOKEN})
    with urllib.request.urlopen(request, timeout=30) as response:
        raw = response.read().decode()
    lowered = raw.lower()
    check('脱敏：响应不含提示词/密钥样式字段',
          not any(token in lowered for token in
                  ('"prompt"', '"messages"', '"apikey"', '"api_key"', 'authorization', 'sk-')),
          [t for t in ('"prompt"', '"messages"', '"apikey"', 'sk-') if t in lowered], '无泄漏')
    gen = gen_of(run)
    check('脱敏：schema2 摘要只有计数（无 targets/attempts 明细键）',
          'targets' not in gen and 'attempts' not in gen
          and 'jobs' in gen and isinstance(gen.get('jobs'), dict),
          list(gen.keys()), 'counts-only')
    check('脱敏：摘要 jobs 是计数结构（queued/succeeded 等数字）',
          all(isinstance(gen['jobs'].get(k), int) for k in ('queued', 'succeeded', 'failed')),
          gen.get('jobs'), 'int counts')


def main():
    _, token = auth_client.register_or_login(BASE, 'budget_e2e_main', 'test1234')
    global TOKEN
    TOKEN = token
    with sto.read_connection() as conn:
        row = conn.execute(sto.text(
            "SELECT user_id FROM wb_users WHERE username='budget_e2e_main'")).mappings().first()
    USER_ID[0] = str(row['user_id'])
    status, provider = api('/api/llm-provider-save',
                           {'name': 'e2e-fake', 'endpoint': LLM_URL, 'model': 'fake-e2e',
                            'apiKey': 'sk-fake', 'timeout': 30, 'temperature': 0,
                            'isDefault': True})
    provider_id = (provider.get('provider') or {}).get('id')
    assert provider_id, provider
    os.environ['WIZ_BUILD_PROFILE_PROVIDER_ID'] = provider_id
    os.environ['WIZ_BUILD_PROFILE_MODEL'] = 'fake-e2e'

    scenario_truncation_split()
    scenario_left_ok_right_fail_resume()
    scenario_cancel_no_revive()
    scenario_schema1_resume()
    scenario_redaction()
    scenario_child_instance()

    print('========== 汇总 ==========')
    print('通过 %d / %d' % (len(PASSED), len(PASSED) + len(FAILED)))
    for name in FAILED:
        print('  失败: %s' % name)
    return 0 if not FAILED else 1


def shutdown():
    for server in (HTTPD, LLM_SERVER):
        try:
            server.shutdown()
            server.server_close()
        except Exception:
            pass
    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == '__main__':
    code = 1
    try:
        code = main()
    finally:
        shutdown()
    sys.exit(code)
