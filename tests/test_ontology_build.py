"""『从物料生成本体』端到端回归（合成物料 + 本地假 LLM + 真实隔离 HTTP 服务）。

覆盖链路：任务创建 → 分片上传 → 扫描解析（事实与定位器逐条回读原文件）→ 范围消息/保存/
确认（含矛盾阻断 → 补覆盖说明后解除）→ 生成候选（捏造 factId 被剔除）→ 候选评审（排除被依赖
对象 → 交付预检阻断 → 恢复 → 预检通过）→ 原子交付（同 requestId 幂等重放、不同 requestId 与
二次交付被拒）→ 交付草稿可编辑（/api/state 结构自洽）→ 跨账号隔离 → 上传安全边界。

隔离（AGENTS.md 测试隔离铁律）：
* WIZ_WORKBENCH_ROOT / WIZ_DATABASE_URL 都指向本次运行新建的临时目录，服务端口取动态空闲端口；
  断言 workbench.paths.DATA_ROOT 与 storage.engine.resolve_url() 都落在临时根内，绝不碰真实
  ontology/。
* 模型调用全部打到本进程内的本地假 LLM（127.0.0.1 随机端口，标准库 http.server），按 system
  prompt 区分「范围澄清」与「候选抽取」两种应答；不访问外网、不使用真实模型与真实密钥。
* 材料为运行时生成的合成物料（DDL / Java / Markdown），不读不写任何真实业务数据。
* 结束清理临时根、假 LLM 线程与工作台 HTTP 服务。

运行：python3 tests/test_ontology_build.py
"""
import base64
import hashlib
import io
import json
import os
import shutil
import socket
import sqlite3
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / 'tests'))

TMP = Path(tempfile.mkdtemp(prefix='wiz_ontology_build_'))
os.environ['WIZ_WORKBENCH_ROOT'] = str(TMP)
os.environ['WIZ_DATABASE_URL'] = 'sqlite:///' + str(TMP / 'data' / 'workbench.sqlite3')

import auth_client  # noqa: E402
from workbench import storage  # noqa: E402
from workbench.ontology_build import protocol  # noqa: E402
from workbench.paths import DATA_ROOT  # noqa: E402
from workbench.storage import engine as engine_mod  # noqa: E402

DB_PATH = TMP / 'data' / 'workbench.sqlite3'
FIXTURE_DIR = TMP / 'materials'
TERMINAL_RUN_STATES = ('succeeded', 'failed', 'cancelled', 'interrupted')
BOGUS_FACT_ID = 'bf-DOES-NOT-EXIST-9999'

PASSED = []
FAILED = []
SEQ = [0]


class Abort(Exception):
    """基础设施级失败（服务未起、关键响应缺字段）：立即结束并保留诊断。"""


def _short(value, limit=400):
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = repr(value)
    return text if len(text) <= limit else text[:limit] + '…'


def check(cond, message, actual=None, expected=None):
    SEQ[0] += 1
    if cond:
        PASSED.append(message)
        print(f'通过 {SEQ[0]}) {message}')
        return True
    FAILED.append(message)
    print(f'[失败] {SEQ[0]}) {message}')
    if expected is not None:
        print('  预期: ' + _short(expected))
    if actual is not None:
        print('  实际: ' + _short(actual))
    return False


def require(value, message):
    if value is None or value == '' or value == []:
        raise Abort(message)
    return value


# --- 本地假 LLM（标准库 http.server，127.0.0.1 随机端口）---------------------------

class FakeLlm(BaseHTTPRequestHandler):
    """按 system prompt 分流：范围澄清 → questions/patch；候选抽取 → candidates。

    抽取应答故意包含一个捏造 factId（BOGUS_FACT_ID），用于验证「越界证据被剔除」。
    """
    calls = []
    facts_seen = []

    def log_message(self, *args):  # 不打印访问日志
        pass

    def do_POST(self):
        length = int(self.headers.get('Content-Length') or 0)
        body = json.loads(self.rfile.read(length).decode() or '{}')
        messages = body.get('messages') or []
        system = str(messages[0].get('content') or '') if messages else ''
        user_text = str(messages[-1].get('content') or '') if messages else ''
        if '抽取器' in system:
            FakeLlm.calls.append('extract')
            payload = self._extract_payload(user_text)
        else:
            FakeLlm.calls.append('scope')
            payload = {
                'questions': [{'text': '是否纳入收益结算模块？', 'blocking': False,
                               'suggestion': '暂不纳入', 'reason': '本期聚焦设备运行'}],
                'patch': {'include': '设备台账、监测数据'},
                'notes': ['材料覆盖：DDL 与需求文档一致']}
        data = json.dumps({'choices': [{'message': {'content': json.dumps(payload, ensure_ascii=False)},
                                        'finish_reason': 'stop'}]}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    @staticmethod
    def _extract_payload(user_text):
        """从抽取请求里取真实 factId，产出确定性的候选（含 1 个捏造证据候选）。"""
        try:
            facts = json.loads(user_text).get('facts') or []
        except ValueError:
            facts = []
        ids = [str(item.get('id')) for item in facts
               if isinstance(item, dict) and item.get('id')]
        FakeLlm.facts_seen = list(ids)
        good = ids[:3]
        return {'candidates': [
            {'key': 'obj-device', 'type': 'object', 'name': '设备', 'definition': '储能设备台账',
             'fields': {}, 'ownerKey': '', 'evidence': {'_record': good},
             'evidenceStatus': 'supported', 'conflicts': []},
            {'key': 'prop-capacity', 'type': 'property', 'name': '额定容量', 'definition': '设备额定容量',
             'fields': {'dataType': 'number'}, 'ownerKey': 'obj-device',
             'evidence': {'definition': good[:2]}, 'evidenceStatus': 'supported', 'conflicts': []},
            {'key': 'prop-bogus', 'type': 'property', 'name': '捏造字段', 'definition': '模型编造的字段',
             'fields': {'dataType': 'text'}, 'ownerKey': 'obj-device',
             'evidence': {'definition': [BOGUS_FACT_ID]},
             'evidenceStatus': 'supported', 'conflicts': []}]}


# --- 服务生命周期 ---------------------------------------------------------------

def free_port():
    """动态空闲端口：绑定 0 取端口后立即关闭（不写死 18765/18871，避免与其它实例冲突）。"""
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

PORT = free_port()
BASE = 'http://127.0.0.1:%d' % PORT
HTTPD = ThreadingHTTPServer(('127.0.0.1', PORT), wb_server.Handler)
threading.Thread(target=HTTPD.serve_forever, daemon=True).start()


def shutdown():
    for server in (HTTPD, LLM_SERVER):
        try:
            server.shutdown()
            server.server_close()
        except Exception:
            pass
    shutil.rmtree(str(TMP), ignore_errors=True)


# --- HTTP 客户端 ---------------------------------------------------------------

def http(path, payload=None, query='', token=None, origin=True):
    """返回 (status, body)；token=None 表示不带会话 Cookie（未登录场景）。"""
    data = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    headers = {'Content-Type': 'application/json'}
    if origin:
        headers['Origin'] = BASE  # server.py 的 Origin 白名单按 server_port 判定
    if token:
        headers['Cookie'] = 'wiz_session=' + token
    request = urllib.request.Request(BASE + path + query, data=data, headers=headers,
                                     method='POST' if payload is not None else 'GET')
    try:
        with urllib.request.urlopen(request, timeout=90) as resp:
            return resp.status, json.loads(resp.read().decode() or '{}')
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode(errors='replace')
        try:
            return exc.code, json.loads(raw or '{}')
        except ValueError:
            return exc.code, {'raw': raw[:400]}


SESSION = {'token': ''}


def api(path, payload=None, query=''):
    return http(path, payload, query, token=SESSION['token'])


def db_rows(sql, params=()):
    con = sqlite3.connect(str(DB_PATH))
    try:
        con.row_factory = sqlite3.Row
        return con.execute(sql, params).fetchall()
    finally:
        con.close()


# --- 物料夹具与定位器回读 ---------------------------------------------------------

FIXTURE_LINES = {}


def write_fixtures():
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    files = {
        'schema.sql': (
            'CREATE TABLE device (\n'
            '  id BIGINT PRIMARY KEY,\n'
            "  capacity DOUBLE COMMENT '额定容量',\n"
            '  cluster_id BIGINT,\n'
            '  FOREIGN KEY (cluster_id) REFERENCES cluster(id)\n'
            ');\n'),
        'Device.java': (
            'package com.x;\n'
            '@Entity\n'
            'public class Device {\n'
            '  @Column(name="capacity") private Double capacity;\n'
            '  @ManyToOne private Cluster cluster;\n'
            '}\n'),
        'req.md': (
            '# 设备管理需求\n\n'
            '## 范围\n\n'
            '设备台账与监测数据。\n\n'
            '| 字段 | 说明 |\n'
            '|---|---|\n'
            '| capacity | 额定容量 |\n'),
    }
    for name, text in files.items():
        (FIXTURE_DIR / name).write_text(text, encoding='utf-8')
        FIXTURE_LINES[name] = text.splitlines()


def fixture_line(rel_path, number):
    """原始文件第 number 行（1 基）去空白；越界返回 None。"""
    lines = FIXTURE_LINES.get(str(rel_path or ''))
    if not lines or not isinstance(number, int) or number < 1 or number > len(lines):
        return None
    return lines[number - 1].strip()


def locator_problem(snippet, locator):
    """ddl/code 定位器的 file/line 必须能回读原文件对应行（与 snippet 首行逐字一致）。"""
    if not isinstance(locator, dict) or locator.get('kind') not in ('ddl', 'code'):
        return None
    rel, number = locator.get('file'), locator.get('line')
    expected = fixture_line(rel, number)
    if expected is None:
        return '定位器无法回读原文件：file=%s line=%s' % (rel, number)
    first = (snippet or '').splitlines()[0].strip() if (snippet or '').strip() else ''
    if first != expected:
        return 'file=%s line=%s 原文=%r snippet 首行=%r' % (rel, number, expected, first)
    return None


# --- 上传与轮询 -----------------------------------------------------------------

def upload(path_obj, task_id):
    data = path_obj.read_bytes()
    status, init = api('/api/build-upload-init',
                       {'taskId': task_id, 'relPath': path_obj.name, 'size': len(data)})
    if status != 200:
        return status, init
    chunk = int(init['chunkBytes'])
    index = 0
    for offset in range(0, len(data), chunk):
        part = data[offset:offset + chunk]
        status, body = api('/api/build-upload-chunk',
                           {'uploadId': init['uploadId'], 'index': index,
                            'hash': hashlib.sha256(part).hexdigest(),
                            'dataBase64': base64.b64encode(part).decode()})
        if status != 200:
            return status, body
        index += 1
    return api('/api/build-upload-complete',
               {'uploadId': init['uploadId'], 'finalHash': hashlib.sha256(data).hexdigest()})


def poll_run(task_id, run_id, timeout=150.0):
    deadline = time.time() + timeout
    body = {}
    while True:
        _, body = api('/api/build-run', query='?taskId=%s&runId=%s' % (task_id, run_id))
        run = body.get('run') or {}
        if run.get('state') in TERMINAL_RUN_STATES or time.time() >= deadline:
            return run
        time.sleep(0.3)


def poll_assistant_message(task_id, timeout=60.0):
    deadline = time.time() + timeout
    messages = []
    while True:
        _, body = api('/api/build-messages', query='?taskId=%s&after=0' % task_id)
        messages = body.get('messages') or []
        if any(m.get('role') == 'assistant' and (m.get('content') or '').strip() for m in messages):
            return messages
        if time.time() >= deadline:
            return messages
        time.sleep(0.3)


# --- 主流程 ---------------------------------------------------------------------

def main():
    # 0) 隔离：数据根与库 URL 必须在本次临时根内（绝不落到真实 ontology/）
    check(str(DATA_ROOT) == str(TMP) and str(REPO) not in str(DATA_ROOT),
          'DATA_ROOT 指向本次临时根（未被真实仓库根劫持）', actual=str(DATA_ROOT), expected=str(TMP))
    check(str(engine_mod.resolve_url()).startswith('sqlite:///' + str(TMP)),
          'storage.engine.resolve_url() 落在临时根内', actual=engine_mod.resolve_url())
    check(DB_PATH.is_file(), '隔离 sqlite 库已在临时根内创建', actual=str(DB_PATH))
    _, token = auth_client.register_or_login(BASE, 'build_e2e_main', 'test1234')
    SESSION['token'] = require(token, '测试账号会话未建立')
    status, auth_state = api('/api/auth-state')
    check(status == 200 and bool((auth_state.get('user') or {}).get('username')),
          '经真实认证接口建立测试账号会话（Cookie wiz_session）',
          actual=(status, auth_state))

    # 1) 能力与任务
    status, caps = api('/api/build-capabilities')
    check(status == 200 and caps.get('limits') == protocol.LIMITS,
          'GET build-capabilities 200 且限额与协议一致', actual=(status, caps.get('limits')),
          expected=protocol.LIMITS)
    status, provider = api('/api/llm-provider-save',
                           {'name': 'fake-local', 'endpoint': LLM_URL, 'model': 'fake-1',
                            'apiKey': 'sk-fake', 'timeout': 30, 'temperature': 0,
                            'isDefault': True})
    provider_id = require((provider.get('provider') or {}).get('id'),
                          'LLM 提供方未保存：%s' % _short(provider))
    check(status == 200 and LLM_URL.startswith('http://127.0.0.1:'),
          '保存本地假 LLM 提供方（127.0.0.1，不使用真实模型）', actual=(status, provider))
    _, caps = api('/api/build-capabilities')
    check((caps.get('provider') or {}).get('id') == provider_id,
          '能力接口回显当前默认提供方', actual=caps.get('provider'), expected=provider_id)

    status, created = api('/api/build-task-create', {'name': '端到端构建任务'})
    task_id = require((created.get('task') or {}).get('id'), '任务创建失败：%s' % _short(created))
    status, fetched = api('/api/build-task', query='?taskId=' + task_id)
    check(status == 200 and created.get('task', {}).get('id') == fetched.get('task', {}).get('id')
          and (created.get('task') or {}).get('name') == '端到端构建任务',
          'POST build-task-create 200 且可按 ID 读回同一任务',
          actual=(status, created.get('task', {}).get('id'),
                  fetched.get('task', {}).get('name')), expected='(200, 同 ID, 端到端构建任务)')
    status, listing = api('/api/build-tasks', query='?limit=5')
    check(status == 200 and listing.get('total', 0) >= 1,
          'GET build-tasks 列表可见本任务', actual=listing.get('total'))

    # 2) 分片上传合成物料
    write_fixtures()
    for name in ('schema.sql', 'Device.java', 'req.md'):
        status, body = upload(FIXTURE_DIR / name, task_id)
        check(status == 200 and len(body.get('materials') or []) == 1,
              '分片上传 %s 成功并登记 1 份材料' % name, actual=(status, body))
    status, materials = api('/api/build-materials', query='?taskId=' + task_id)
    items = materials.get('items') or []
    check(status == 200 and len(items) == 3 and {m['relPath'] for m in items} ==
          {'schema.sql', 'Device.java', 'req.md'},
          'GET build-materials 返回 3 份材料', actual=[m.get('relPath') for m in items])

    # 3) 扫描解析：事实数与定位器逐条回读原文件
    status, scan = api('/api/build-scan', {'taskId': task_id})
    run_id = require(scan.get('runId'), '扫描未返回 runId：%s' % _short(scan))
    run = poll_run(task_id, run_id)
    check(run.get('state') == 'succeeded',
          '扫描运行 succeeded', actual=run.get('error') or run.get('state'))
    _, materials = api('/api/build-materials', query='?taskId=' + task_id)
    parse_states = {m['relPath']: m['parseState'] for m in materials.get('items') or []}
    check(all(state in ('success', 'partial') for state in parse_states.values()),
          '三份材料解析成功（success/partial）', actual=parse_states)
    fact_total = sum(int((m.get('coverage') or {}).get('factCount') or 0)
                     for m in materials.get('items') or [])
    check(fact_total >= 8, '解析产出真实事实（coverage.factCount 汇总）', actual=fact_total,
          expected='>= 8')

    facts = db_rows('SELECT fact_id, module, kind, locator_json, snippet FROM wb_build_facts '
                    'WHERE task_id = ?', (task_id,))
    check(len(facts) == fact_total and len(facts) >= 8,
          '库中事实行数与 coverage.factCount 一致', actual=(len(facts), fact_total))
    problems = []
    for row in facts:
        problem = locator_problem(row['snippet'], json.loads(row['locator_json'] or '{}'))
        if problem:
            problems.append('%s: %s' % (row['fact_id'], problem))
    check(not problems, '每条 ddl/code 事实的 file/line 都能回读原文件对应行',
          actual=problems[:3], expected='0 条不一致')
    capacity = [row for row in facts if json.loads(row['locator_json'] or '{}') ==
                {'kind': 'ddl', 'file': 'schema.sql', 'line': 3, 'table': 'device',
                 'column': 'capacity'}]
    check(bool(capacity) and fixture_line('schema.sql', 3) in capacity[0]['snippet'],
          'capacity 列事实定位到 schema.sql:3 且与原文一致',
          actual=[dict(zip(('id', 'snippet'), (c['fact_id'], c['snippet']))) for c in capacity])
    fact_ids = {row['fact_id'] for row in facts}

    # 4) 范围：消息 → 保存 → 矛盾阻断 → 补覆盖说明后确认
    _, scope0 = api('/api/build-messages', query='?taskId=' + task_id)
    status, sent = api('/api/build-message', {'taskId': task_id, 'revision': scope0.get('revision'),
                                              'content': '本期只做设备运行，不要收益结算'})
    check(status == 200 and sent.get('assistantPending') is True,
          'POST build-message 受理并排入助手回复', actual=(status, sent))
    messages = poll_assistant_message(task_id)
    check(any(m.get('role') == 'assistant' and (m.get('content') or '').strip() for m in messages),
          '助手真实（本地假 LLM）回复落库', actual=[(m.get('role'), (m.get('content') or '')[:40])
                                                for m in messages])
    _, body = api('/api/build-messages', query='?taskId=' + task_id)
    scope = {'goal': '设备运行管理', 'include': '设备台账、监测数据', 'exclude': '设备台账、收益结算',
             'relations': '园区与电表只保留必要关联', 'coverage': '', 'openQuestions': []}
    status, saved = api('/api/build-scope-save', {'taskId': task_id, 'revision': body.get('revision', 0),
                                                 'scope': scope})
    revision = require((saved.get('scope') or {}).get('revision'),
                       '范围保存失败：%s' % _short(saved))
    check(status == 200 and revision, 'POST build-scope-save 200 并返回新 revision', actual=saved)
    status, blocked = api('/api/build-scope-confirm',
                          {'taskId': task_id, 'revision': revision, 'providerId': provider_id})
    codes = [issue.get('code') for issue in (blocked.get('issues') or [])]
    check(status == 422 and 'SCOPE_CONFLICT' in codes,
          '纳入/排除同词且无覆盖说明 → 范围确认被阻断', actual=(status, blocked.get('issues')))
    scope['coverage'] = '设备台账在纳入范围内；收益结算明确排除，两者不重复'
    status, saved = api('/api/build-scope-save', {'taskId': task_id, 'revision': revision,
                                                  'scope': scope})
    revision = (saved.get('scope') or {}).get('revision')
    status, confirmed = api('/api/build-scope-confirm',
                            {'taskId': task_id, 'revision': revision, 'providerId': provider_id})
    batch_id = require(confirmed.get('batchId'), '范围确认未启动生成：%s' % _short(confirmed))
    check(status == 200 and bool(confirmed.get('runId')),
          '补充覆盖说明后范围确认通过并启动生成', actual=(status, confirmed))

    # 5) 生成候选
    run = poll_run(task_id, confirmed['runId'])
    check(run.get('state') == 'succeeded', '生成运行 succeeded',
          actual=run.get('error') or run.get('state'))
    check(bool(FakeLlm.facts_seen) and set(FakeLlm.facts_seen) <= fact_ids,
          '假 LLM 收到的是本次材料真实 factId', actual=len(FakeLlm.facts_seen),
          expected='非空且全部属于本任务')
    status, candidates = api('/api/build-candidates',
                             query='?taskId=%s&batch=%s' % (task_id, batch_id))
    items = candidates.get('items') or []
    rows = db_rows('SELECT candidate_id, name, evidence_status, evidence_json FROM '
                   'wb_build_candidates WHERE task_id = ? AND batch_id = ?', (task_id, batch_id))
    check(status == 200 and len(items) == 3 and len(rows) == len(items),
          '模型产出的 3 个候选全部入库（API 与库中数量一致）',
          actual=(status, len(items), len(rows)), expected='(200, 3, 3)')

    # 6) 证据真实性：捏造 factId 被剔除，真实证据可回读
    bogus = [row for row in rows if row['name'] == '捏造字段']
    check(bool(bogus) and bogus[0]['evidence_status'] != 'supported'
          and BOGUS_FACT_ID not in (bogus[0]['evidence_json'] or ''),
          '捏造证据的候选被降级且证据引用被清空',
          actual=[(r['name'], r['evidence_status'], r['evidence_json']) for r in rows])
    check(not db_rows('SELECT 1 FROM wb_build_facts WHERE fact_id = ?', (BOGUS_FACT_ID,)),
          '库中不存在被捏造的 factId', actual=BOGUS_FACT_ID)
    check(not db_rows('SELECT 1 FROM wb_build_candidates WHERE evidence_json LIKE ?',
                      ('%' + BOGUS_FACT_ID + '%',)),
          '任何候选的证据字段都不含被捏造的 factId', actual=BOGUS_FACT_ID)
    device = next((c for c in items if c['name'] == '设备'), None)
    require(device, '候选中缺少对象「设备」：%s' % _short([c.get('name') for c in items]))
    status, detail = api('/api/build-candidate', query='?candidateId=' + device['id'])
    groups = detail.get('evidence') or []
    shown = [item for group in groups for item in (group.get('items') or [])]
    mismatched = [item for item in shown
                  if locator_problem(item.get('snippet'), item.get('locator') or {})]
    check(status == 200 and bool(shown) and not mismatched,
          '候选证据详情可回读原文件（file/line 与片段一致）',
          actual=(status, len(shown), mismatched[:2]))

    # 7) 评审：排除被依赖对象 → 预检阻断 → 恢复 → 预检通过
    status, excluded = api('/api/build-candidate-decide',
                           {'candidateId': device['id'], 'decision': 'exclude',
                            'reason': '本期不做设备主数据', 'revision': device['revision']})
    excluded_revision = (excluded.get('candidate') or {}).get('revision')
    check(status == 200 and bool(excluded_revision), '排除对象候选成功', actual=(status, excluded))
    status, precheck = api('/api/build-deliver-precheck', {'taskId': task_id})
    issue_codes = [issue.get('code') for issue in (precheck.get('issues') or [])]
    check(status == 200 and precheck.get('ok') is False and precheck.get('issues')
          and 'DEPENDENCY_NOT_INCLUDED' in issue_codes,
          '排除被依赖对象后交付预检 ok=false 且列出依赖阻断',
          actual=(status, precheck.get('ok'), issue_codes))
    status, restored = api('/api/build-candidate-decide',
                           {'candidateId': device['id'], 'decision': 'include', 'reason': '恢复',
                            'revision': excluded_revision})
    check(status == 200 and (restored.get('candidate') or {}).get('decision') == 'include',
          '恢复对象候选成功', actual=(status, restored))
    status, precheck = api('/api/build-deliver-precheck', {'taskId': task_id})
    token = require(precheck.get('checkToken'), '预检未返回 checkToken：%s' % _short(precheck))
    check(status == 200 and precheck.get('ok') is True and not precheck.get('issues'),
          '恢复后交付预检 ok=true 且无阻断项',
          actual=(status, precheck.get('ok'), precheck.get('issues')))

    # 8) 交付：原子创建 + 幂等重放 + 二次交付拒绝
    request_id = 'req-build-e2e-1'
    status, delivered = api('/api/build-deliver', {'taskId': task_id, 'name': '端到端设备本体',
                                                   'checkToken': token, 'requestId': request_id})
    ontology_id = require(delivered.get('ontologyId'), '交付未返回 ontologyId：%s' % _short(delivered))
    check(status == 200 and delivered.get('replayed') is False,
          '交付创建本体草稿 200（replayed=false）', actual=(status, delivered))
    status, replay = api('/api/build-deliver', {'taskId': task_id, 'name': '端到端设备本体',
                                                'checkToken': token, 'requestId': request_id})
    check(status == 200 and replay.get('ontologyId') == ontology_id
          and replay.get('replayed') is True,
          '同 requestId 重放返回同一 ontologyId 且 replayed=true', actual=(status, replay))
    status, other = api('/api/build-deliver', {'taskId': task_id, 'name': '端到端设备本体二次',
                                               'checkToken': token, 'requestId': 'req-build-e2e-2'})
    other_codes = [issue.get('code') for issue in (other.get('issues') or [])]
    check(status in (409, 422) and 'ALREADY_DELIVERED' in other_codes,
          '不同 requestId 再次交付被拒（409/422）', actual=(status, other_codes))
    status, precheck2 = api('/api/build-deliver-precheck', {'taskId': task_id})
    codes2 = [issue.get('code') for issue in (precheck2.get('issues') or [])]
    check(status in (409, 422) and 'ALREADY_DELIVERED' in codes2,
          '交付后预检被拒（一个任务只能交付一次）', actual=(status, codes2))
    _, listing = api('/api/build-tasks', query='?limit=5')
    task = next((t for t in (listing.get('items') or []) if t.get('id') == task_id), {})
    check(task.get('status') == 'delivered',
          '任务状态推进为 delivered', actual=task.get('status'))

    # 9) 交付草稿可编辑：/api/state 结构自洽
    status, state = api('/api/state', query='?ontology=' + ontology_id)
    payload = (state.get('state') or {}) if status == 200 else {}
    ontology = payload.get('ontology') or {}
    objects = ontology.get('objectTypes') or []
    properties = ontology.get('properties') or []
    links = ontology.get('linkTypes') or []
    check(status == 200 and payload.get('workspaceId') == ontology_id and objects and properties,
          '交付后 /api/state 可读到新草稿且含对象与属性',
          actual=(status, len(objects), len(properties)))
    all_ids = [node.get('id') for node in objects + properties + links]
    order = ontology.get('definitionOrder') or []
    check(set(order) == set(all_ids) and len(order) == len(all_ids),
          'definitionOrder 与定义集合完全一致', actual=(order, all_ids))
    object_ids = {node.get('id') for node in objects}
    check(all(node.get('objectTypeId') in object_ids for node in properties),
          '每个属性都挂在交付出的对象上',
          actual=[(n.get('displayName'), n.get('objectTypeId')) for n in properties])

    # 10) 跨账号隔离与未登录
    _, other_token = auth_client.register_or_login(BASE, 'build_e2e_other', 'test1234')
    status, _ = http('/api/build-task', query='?taskId=' + task_id, token=other_token)
    check(status == 404, '跨账号读任务按不存在处理（404）', actual=status)
    status, other_list = http('/api/build-tasks', query='?limit=10', token=other_token)
    check(status == 200 and other_list.get('total') == 0,
          '跨账号任务列表为空', actual=(status, other_list.get('total')))
    status, _ = http('/api/build-tasks', query='?limit=1')
    check(status == 401, '未登录 GET 被拒（401）', actual=status)
    status, _ = http('/api/build-task-create', {'name': 'x'}, token=None)
    check(status == 401, '未登录 POST 被拒（401）', actual=status)

    # 11) 上传安全边界
    before = len(db_rows('SELECT 1 FROM wb_build_materials WHERE task_id = ?', (task_id,)))
    status, _ = api('/api/build-upload-init',
                    {'taskId': task_id, 'relPath': '../evil.txt', 'size': 10})
    check(status in (400, 404)
          and not db_rows("SELECT 1 FROM wb_build_uploads WHERE rel_path LIKE '%evil%'"),
          '上传路径穿越被拒且未登记上传会话', actual=(status,))
    status, _ = api('/api/build-upload-init',
                    {'taskId': task_id, 'relPath': 'big.bin',
                     'size': protocol.FILE_BYTES + 1024})
    check(status in (400, 413, 422)
          and not db_rows("SELECT 1 FROM wb_build_uploads WHERE rel_path = 'big.bin'"),
          '超过单文件限额的上传被拒（不落库）', actual=status, expected='400/413/422')

    escape_name = 'wiz-escape-' + os.urandom(4).hex() + '.txt'
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('ok/readme.md', '# 子目录文档\n')
        archive.writestr('../' + escape_name, 'x')
    zip_bytes = buffer.getvalue()
    zip_init_status, init = api('/api/build-upload-init',
                                {'taskId': task_id, 'relPath': 'src.zip', 'size': len(zip_bytes)})
    digest = hashlib.sha256(zip_bytes).hexdigest()
    if zip_init_status == 200:
        api('/api/build-upload-chunk',
            {'uploadId': init['uploadId'], 'index': 0, 'hash': digest,
             'dataBase64': base64.b64encode(zip_bytes).decode()})
        status, zip_result = api('/api/build-upload-complete',
                                 {'uploadId': init['uploadId'], 'finalHash': digest})
    else:
        status, zip_result = zip_init_status, init
    after = len(db_rows('SELECT 1 FROM wb_build_materials WHERE task_id = ?', (task_id,)))
    leaked = list(TMP.rglob(escape_name)) + ([TMP.parent / escape_name]
                                             if (TMP.parent / escape_name).exists() else [])
    check(zip_init_status == 200 and status in (400, 422) and after == before and not leaked
          and not db_rows("SELECT 1 FROM wb_build_materials WHERE rel_path LIKE '%readme%'"),
          'ZIP 目录穿越整包被拒：无材料登记、无文件逃逸',
          actual=(zip_init_status, status, before, after, [str(p) for p in leaked],
                  _short(zip_result)))
    check('scope' in FakeLlm.calls and 'extract' in FakeLlm.calls,
          '全程只经本地假 LLM（范围澄清与候选抽取各至少一次）', actual=FakeLlm.calls)
    return 0


if __name__ == '__main__':
    code = 1
    try:
        code = main()
    except Abort as exc:
        print(f'\n[中断] {exc}')
    except Exception as exc:  # 未预期异常不吞：打印类型与消息，便于定位
        import traceback
        traceback.print_exc()
        FAILED.append('未预期异常: %s: %s' % (type(exc).__name__, exc))
    finally:
        shutdown()
    total = len(PASSED) + len(FAILED)
    print('\n========== 汇总 ==========')
    print(f'通过 {len(PASSED)} / {total}')
    for name in FAILED:
        print('  失败: ' + name)
    if total == 0:
        print('[错误] 未执行任何断言——不算通过')
        sys.exit(2)
    sys.exit(0 if code == 0 and not FAILED else 1)
