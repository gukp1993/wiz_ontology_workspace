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
from workbench import model_format  # noqa: E402
from workbench import storage  # noqa: E402
from workbench.ontology_build import alignment  # noqa: E402
from workbench.ontology_build import materials as materials_domain  # noqa: E402
from workbench.ontology_build import protocol  # noqa: E402
from workbench.ontology_build import runner  # noqa: E402
from workbench.ontology_build import tasks as tasks_domain  # noqa: E402
from workbench.ontology_build import ontology_adapter as adapter_mod  # noqa: E402
from workbench.paths import DATA_ROOT  # noqa: E402
from workbench.storage import engine as sto  # noqa: E402
from workbench.storage import engine as engine_mod  # noqa: E402
from workbench.storage import ontology_build as ob_store  # noqa: E402

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

def _upload_bytes(data, task_id, rel_path):
    """按字节内容走完整分片上传三步流（init → chunk* → complete）。"""
    status, init = api('/api/build-upload-init',
                       {'taskId': task_id, 'relPath': rel_path, 'size': len(data)})
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


def upload(path_obj, task_id):
    return _upload_bytes(path_obj.read_bytes(), task_id, path_obj.name)


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


# --- 回归流共用搭建（D03/D04/D09/D11/D14/D17 等） -----------------------------------

def _provider_id():
    _, caps = api('/api/build-capabilities')
    return (caps.get('provider') or {}).get('id')


def _main_user_id():
    rows = db_rows("SELECT user_id FROM wb_users WHERE username_key = 'build_e2e_main'")
    return rows[0]['user_id'] if rows else ''


def setup_generated_task(task_name):
    """建任务→传 3 份合成物料→扫描→确认范围→生成，返回 (task_id, batch_id, run)。"""
    provider_id = require(_provider_id(), '未找到默认 LLM 提供方')
    status, created = api('/api/build-task-create', {'name': task_name})
    task_id = require((created.get('task') or {}).get('id'),
                      '任务创建失败：%s' % _short(created))
    for name in ('schema.sql', 'Device.java', 'req.md'):
        status, body = upload(FIXTURE_DIR / name, task_id)
        if status != 200:
            raise Abort('搭建「%s」上传 %s 失败：%s' % (task_name, name, _short(body)))
    status, scan = api('/api/build-scan', {'taskId': task_id})
    run = poll_run(task_id, require(scan.get('runId'), '扫描未返回 runId'))
    if run.get('state') != 'succeeded':
        raise Abort('搭建「%s」扫描失败：%s' % (task_name, run.get('error')))
    _, detail = api('/api/build-task', query='?taskId=' + task_id)
    scope_rev = (detail.get('scope') or {}).get('revision') or 0
    scope = {'goal': '设备运行管理', 'include': '设备台账、监测数据', 'exclude': '收益结算模块',
             'relations': '', 'coverage': '', 'openQuestions': []}
    status, saved = api('/api/build-scope-save',
                        {'taskId': task_id, 'revision': scope_rev, 'scope': scope})
    revision = require((saved.get('scope') or {}).get('revision'),
                       '范围保存失败：%s' % _short(saved))
    status, confirmed = api('/api/build-scope-confirm',
                            {'taskId': task_id, 'revision': revision, 'providerId': provider_id})
    batch_id = require(confirmed.get('batchId'),
                       '范围确认失败：%s' % _short(confirmed))
    run = poll_run(task_id, require(confirmed.get('runId'), '确认未返回 runId'))
    if run.get('state') != 'succeeded':
        raise Abort('搭建「%s」生成失败：%s' % (task_name, run.get('error')))
    return task_id, batch_id, run


def candidates_by_name(task_id, batch_id):
    _, body = api('/api/build-candidates', query='?taskId=%s&batch=%s' % (task_id, batch_id))
    return {c['name']: c for c in (body.get('items') or [])}, body


# --- 回归流 2：评审语义（D17/D03/D04/D11 + usage） ----------------------------------

def flow_review_semantics():
    task_id, batch_id, run = setup_generated_task('评审语义回归')
    usage = run.get('usage') or {}
    check(int(usage.get('calls') or 0) >= 1 and int(usage.get('promptBytes') or 0) > 0,
          '生成运行的 usage 由 LLM 调用累计（不再恒为空）', actual=usage)
    items, _body = candidates_by_name(task_id, batch_id)
    device = require(items.get('设备'), '候选缺少「设备」')
    capacity = require(items.get('额定容量'), '候选缺少「额定容量」')
    bogus = require(items.get('捏造字段'), '候选缺少「捏造字段」')

    # D17：弱证据候选无说明转纳入 → 422 INVALID_STATE + REASON_REQUIRED（不再是 400）
    status, blocked = api('/api/build-candidate-decide',
                          {'candidateId': bogus['id'], 'decision': 'include',
                           'revision': bogus['revision']})
    check(status == 422 and blocked.get('code') == 'INVALID_STATE'
          and (blocked.get('issues') or [{}])[0].get('code') == 'REASON_REQUIRED',
          '弱证据无说明转纳入 → 422 INVALID_STATE/REASON_REQUIRED（D17）',
          actual=(status, blocked))
    status, okinc = api('/api/build-candidate-decide',
                        {'candidateId': bogus['id'], 'decision': 'include',
                         'reason': '已人工核对来源，确认保留', 'revision': bogus['revision']})
    status2, back = api('/api/build-candidate-decide',
                        {'candidateId': bogus['id'], 'decision': 'defer',
                         'revision': (okinc.get('candidate') or {}).get('revision')})
    bogus_revision = (back.get('candidate') or {}).get('revision', bogus['revision'])
    check(status == 200 and status2 == 200,
          '补理由后纳入成功；再回退为暂缓（D17）', actual=(status, status2, back))

    # D17：合并的语义阻断都走 422 INVALID_STATE + 细分码
    status, mism = api('/api/build-candidates-merge',
                       {'taskId': task_id, 'primaryId': device['id'],
                        'mergeIds': [capacity['id']]})
    check(status == 422 and mism.get('code') == 'INVALID_STATE'
          and (mism.get('issues') or [{}])[0].get('code') == 'MERGE_TYPE_MISMATCH',
          '跨类型合并 → 422 INVALID_STATE/MERGE_TYPE_MISMATCH（D17）', actual=(status, mism))
    status, dmism = api('/api/build-candidates-merge',
                        {'taskId': task_id, 'primaryId': capacity['id'],
                         'mergeIds': [bogus['id']]})
    check(status == 422 and dmism.get('code') == 'INVALID_STATE'
          and (dmism.get('issues') or [{}])[0].get('code')
          == 'MERGE_DATA_TYPE_MISMATCH',
          'dataType 不一致合并 → 422 INVALID_STATE/MERGE_DATA_TYPE_MISMATCH（D17）',
          actual=(status, dmism))

    # D03：修正 dataType 后合并；被合并候选从列表/计数/预检选定集合全部消失
    status, upd = api('/api/build-candidate-update',
                      {'candidateId': bogus['id'], 'fields': {'dataType': 'number'},
                       'revision': bogus_revision})
    bogus_revision = (upd.get('candidate') or {}).get('revision', bogus_revision)
    check(status == 200 and upd.get('candidate'), '把 dataType 修正为 number', actual=(status, upd))
    status, merged = api('/api/build-candidates-merge',
                         {'taskId': task_id, 'primaryId': capacity['id'],
                          'mergeIds': [bogus['id']], 'confirmed': True,
                          'revision': capacity['revision']})
    check(status == 200 and bool(merged.get('opId'))
          and (merged.get('candidate') or {}).get('id') == capacity['id'],
          '执行合并成功（保留项=额定容量）', actual=(status, _short(merged)))
    items2, body2 = candidates_by_name(task_id, batch_id)
    counts = body2.get('counts') or {}
    decision_sum = sum((counts.get('byDecision') or {}).values())
    check(bogus['id'] not in {c['id'] for c in (body2.get('items') or [])}
          and body2.get('total') == 2 and decision_sum == 2
          and bogus['name'] not in items2,
          '被合并候选不在列表/total/counts（D03）',
          actual=(body2.get('total'), decision_sum, list(items2)))
    capacity_revision = (merged.get('candidate') or {}).get('revision', capacity['revision'])
    status, pre = api('/api/build-deliver-precheck', {'taskId': task_id})
    selected = set(pre.get('selectedIds') or [])
    check(status == 200 and pre.get('ok') is True and bogus['id'] not in selected
          and capacity['id'] in selected,
          '预检选定集合排除被合并候选（D03）', actual=(status, pre.get('ok'), selected))

    # D04：人工排除的候选在再生成后不得自动复活（强制 defer + origin.revived）
    status, exc = api('/api/build-candidate-decide',
                      {'candidateId': capacity['id'], 'decision': 'exclude',
                       'reason': '本期不含该属性', 'revision': capacity_revision})
    check(status == 200 and (exc.get('candidate') or {}).get('decision') == 'exclude',
          '旧批次人工排除候选（为 D04 铺垫）', actual=(status, exc))
    # D11 铺垫：b1 里人工改名设备；再生成后 keepManual 应把新批次的名称改回人工值
    status, renamed = api('/api/build-candidate-update',
                          {'candidateId': device['id'], 'fields': {'name': '储能设备'},
                           'revision': device['revision']})
    check(status == 200 and (renamed.get('candidate') or {}).get('name') == '储能设备',
          'b1 人工改名「储能设备」（D11 铺垫）', actual=(status, _short(renamed)))
    status, regen = api('/api/build-regenerate', {'taskId': task_id})
    b2 = require(regen.get('batchId'), '再生成未返回 batchId：%s' % _short(regen))
    run2 = poll_run(task_id, require(regen.get('runId'), '再生成未返回 runId'))
    check(run2.get('state') == 'succeeded', '再生成运行 succeeded',
          actual=run2.get('error') or run2.get('state'))
    items_b2, _ = candidates_by_name(task_id, b2)
    cap2 = require(items_b2.get('额定容量'), '新批次缺少「额定容量」')
    dev2 = require(items_b2.get('设备'), '新批次缺少「设备」')
    check(cap2['decision'] == 'defer' and (cap2.get('origin') or {}).get('revived') is True,
          '新批次同键候选被强制 defer 且标记 revived（D04）',
          actual=(cap2['decision'], cap2.get('origin')))
    status, diff = api('/api/build-diff', query='?taskId=%s&batch=%s' % (task_id, b2))
    buckets = diff.get('buckets') or {}
    changed_ids = {item.get('id'): item for item in (buckets.get('changed') or [])}
    check(status == 200 and cap2['id'] in (buckets.get('excludedProtected') or []),
          '差异报告 excludedProtected 命中被排除候选（D04/D11）',
          actual=(status, buckets.get('excludedProtected')))
    check('name' in (changed_ids.get(dev2['id']) or {}).get('fieldsChanged', []),
          'b2 设备与 b1 人工改名构成 changed.name（D11）', actual=changed_ids.get(dev2['id']))
    status, resolved = api('/api/build-diff-resolve',
                           {'taskId': task_id, 'candidateId': dev2['id'],
                            'choice': 'keepManual', 'revision': dev2['revision']})
    cand = resolved.get('candidate') or {}
    items_after, _ = candidates_by_name(task_id, b2)
    cap_after = items_after.get('额定容量') or {}
    check(status == 200 and cand.get('name') == '储能设备'
          and cand.get('definition') == '储能设备台账'
          and cap_after.get('name') == '额定容量'
          and cap_after.get('decision') == 'defer',
          'keepManual 取回人工名且不张冠李戴（其他候选原样）（D11）',
          actual=(status, cand.get('name'), cand.get('definition'),
                  cap_after.get('name'), cap_after.get('decision')))
    return None


# --- 回归流 3：脏候选确定性阻断（D09/D14/D02） -------------------------------------

def flow_bad_candidates():
    task_id, batch_id, _run = setup_generated_task('脏数据阻断回归')
    uid = require(_main_user_id(), '找不到测试账号 user_id')
    items, _body = candidates_by_name(task_id, batch_id)
    bogus = require(items.get('捏造字段'), '候选缺少「捏造字段」')
    status, inc = api('/api/build-candidate-decide',
                      {'candidateId': bogus['id'], 'decision': 'include',
                       'reason': '种子回归：纳入以验证结构阻断覆盖全部选定候选',
                       'revision': bogus['revision']})
    check(status == 200, '弱证据候选补理由纳入（脏数据回归铺垫）', actual=(status, inc))

    def mutate(conn):
        conn.execute(sto.text("UPDATE wb_build_candidates SET name = '', definition = '' "
                              "WHERE task_id = :t AND batch_id = :b AND ckey = 'obj-device'"),
                     {'t': task_id, 'b': batch_id})
        conn.execute(sto.text('UPDATE wb_build_candidates SET fields_json = :f '
                              'WHERE task_id = :t AND batch_id = :b AND ckey = :k'),
                     {'f': sto.json_dumps({'dataType': 'bogus-type'}),
                      't': task_id, 'b': batch_id, 'k': 'prop-capacity'})
        conn.execute(sto.text('UPDATE wb_build_candidates SET fields_json = :f '
                              'WHERE task_id = :t AND batch_id = :b AND ckey = :k'),
                     {'f': sto.json_dumps({'dataType': 'timeSeries'}),
                      't': task_id, 'b': batch_id, 'k': 'prop-bogus'})
        ob_store.create_candidate(conn, task_id, uid, batch_id, {
            'type': 'property', 'key': 'prop-ts-invalid', 'name': '温度序列',
            'definition': '每分钟温度观测', 'fields': {'dataType': 'timeSeries', 'valueType': 'text'},
            'ownerKey': 'obj-device', 'evidence': {}, 'evidenceStatus': 'supported',
            'decision': 'include', 'alignedKey': 'seed:property:温度序列'})
        ob_store.create_candidate(conn, task_id, uid, batch_id, {
            'type': 'rule', 'key': 'rule-power-limit', 'name': '功率约束',
            'definition': '储能设备功率上限', 'fields': {'content': 'p <= 额定容量 × 2'},
            'ownerKey': 'obj-device', 'evidence': {}, 'evidenceStatus': 'supported',
            'decision': 'include', 'alignedKey': 'seed:rule:功率约束'})

    with sto.write_tx() as tx:
        tx.run(mutate)
    expected = {'NAME_REQUIRED', 'DEFINITION_REQUIRED', 'DATA_TYPE_INVALID',
                'OBSERVATION_VALUE_TYPE_MISSING', 'OBSERVATION_VALUE_TYPE_INVALID'}
    status, pre = api('/api/build-deliver-precheck', {'taskId': task_id})
    codes = {issue.get('code') for issue in (pre.get('issues') or [])}
    check(status == 200 and pre.get('ok') is False and expected <= codes,
          '预检确定性列出全部结构阻断（含旧枚举值 text 的 INVALID）（D09/D02）',
          actual=(status, pre.get('ok'), sorted(codes)))
    # D10：checkToken 必填——缺令牌直接提交先是形态错误 400，绝不进入交付事务
    status, no_token = api('/api/build-deliver', {'taskId': task_id, 'name': '应被阻断',
                                                  'requestId': 'req-bad-notoken'})
    check(status == 400 and no_token.get('code') == 'INVALID_ARGUMENT'
          and 'checkToken' in str(no_token.get('error') or ''),
          '交付缺少 checkToken → 400 INVALID_ARGUMENT（D10）', actual=(status, no_token))
    dirty_token = require(pre.get('checkToken'), '预检未返回 checkToken：%s' % _short(pre))
    status, delivered = api('/api/build-deliver', {'taskId': task_id, 'name': '应被阻断',
                                                   'checkToken': dirty_token,
                                                   'requestId': 'req-bad-1'})
    dv_codes = {issue.get('code') for issue in (delivered.get('issues') or [])}
    check(status == 422 and delivered.get('code') == 'INVALID_STATE' and expected <= dv_codes,
          '交付同口径拒绝（不再出现预检通过/交付 400 的分裂）（D09/D17）',
          actual=(status, delivered.get('code'), sorted(dv_codes)))
    items, _body = candidates_by_name(task_id, batch_id)
    rule = require(items.get('功率约束'), '种子规则未出现在列表')
    device = next((c for c in (_body.get('items') or []) if c.get('key') == 'obj-device'), None)
    require(device, '种子改造后找不到设备')
    status, exc = api('/api/build-candidate-decide',
                      {'candidateId': device['id'], 'decision': 'exclude',
                       'reason': '宿主排除', 'revision': device['revision']})
    check(status == 200, '排除宿主对象', actual=(status, exc))
    status, pre2 = api('/api/build-deliver-precheck', {'taskId': task_id})
    deps = [issue for issue in (pre2.get('issues') or [])
            if issue.get('code') == 'DEPENDENCY_NOT_INCLUDED']
    check(any(issue.get('candidateId') == rule['id'] for issue in deps),
          '规则经宿主对象（ownerKey）进入交付依赖阻断（D14）',
          actual=[(i.get('candidateId'), i.get('message')) for i in deps])


# --- 回归流 4：时间序列端到端交付（D02） -------------------------------------------

def flow_timeseries_delivery():
    task_id, batch_id, _run = setup_generated_task('时序交付回归')
    items, _body = candidates_by_name(task_id, batch_id)
    capacity = require(items.get('额定容量'), '候选缺少「额定容量」')
    status, to_ts = api('/api/build-candidate-update',
                        {'candidateId': capacity['id'],
                         'fields': {'dataType': 'timeSeries', 'valueType': 'double'},
                         'revision': capacity['revision']})
    check(status == 200
          and (to_ts.get('candidate') or {}).get('fields', {}).get('valueType') == 'double',
          '属性改为 timeSeries/double 保存成功', actual=(status, _short(to_ts)))
    revision = (to_ts.get('candidate') or {}).get('revision', capacity['revision'])
    # R3-04 后：非法观测值类型在**保存**这一步就被拒绝（与交付同枚举），
    # 而不是先写进候选再等预检报错。断言拒绝文案给出允许枚举，且库里的值未被改写。
    status, dirty = api('/api/build-candidate-update',
                        {'candidateId': capacity['id'], 'fields': {'valueType': 'text'},
                         'revision': revision})
    check(status == 400 and 'double' in str(dirty.get('error') or ''),
          '非法观测值类型在保存时被拒并列出允许枚举（D02/R3-04）',
          actual=(status, _short(dirty)))
    status, kept = api('/api/build-candidates', query='?taskId=%s&batch=%s' % (task_id, batch_id))
    kept_value = next((c.get('fields', {}).get('valueType')
                       for c in (kept.get('items') or []) if c['id'] == capacity['id']), None)
    check(kept_value == 'double', '被拒的非法值没有写进候选（仍是 double）', actual=kept_value)
    # 预检兜底：绕过编辑接口直接往库里塞一个非法观测值，预检必须确定性拦下。
    with sqlite3.connect(str(TMP / 'data' / 'workbench.sqlite3')) as conn:
        conn.execute("UPDATE wb_build_candidates SET fields_json = "
                     "replace(fields_json, '\"valueType\": \"double\"', '\"valueType\": \"text\"') "
                     "WHERE candidate_id = ?", (capacity['id'],))
        conn.commit()
    status, pre = api('/api/build-deliver-precheck', {'taskId': task_id})
    codes = {issue.get('code') for issue in (pre.get('issues') or [])}
    check(status == 200 and pre.get('ok') is False
          and 'OBSERVATION_VALUE_TYPE_INVALID' in codes,
          '库里残留的旧枚举值 text 仍被预检拦截：ok=false 且报 INVALID（D02 兜底）',
          actual=(status, pre.get('ok'), sorted(codes)))
    # 还原为合法值，继续走「修正后预检通过 → 交付」的正常链路
    with sqlite3.connect(str(TMP / 'data' / 'workbench.sqlite3')) as conn:
        conn.execute("UPDATE wb_build_candidates SET fields_json = "
                     "replace(fields_json, '\"valueType\": \"text\"', '\"valueType\": \"double\"') "
                     "WHERE candidate_id = ?", (capacity['id'],))
        conn.commit()
    status, fixed = api('/api/build-candidate-update',
                        {'candidateId': capacity['id'], 'fields': {'valueType': 'double'},
                         'revision': revision})
    status, pre2 = api('/api/build-deliver-precheck', {'taskId': task_id})
    token = require(pre2.get('checkToken'), '预检未返回 checkToken：%s' % _short(pre2))
    check(status == 200 and pre2.get('ok') is True and not pre2.get('issues'),
          '修正后预检通过（D02 预检/交付同枚举）', actual=(status, pre2.get('ok'), pre2.get('issues')))
    status, delivered = api('/api/build-deliver', {'taskId': task_id, 'name': '储能时序本体回归',
                                                   'checkToken': token, 'requestId': 'req-ts-1'})
    ontology_id = require(delivered.get('ontologyId'), '时序交付失败：%s' % _short(delivered))
    check(status == 200, 'timeSeries 属性端到端交付 200', actual=(status, _short(delivered)))
    status, state = api('/api/state', query='?ontology=' + ontology_id)
    raw = json.dumps(state, ensure_ascii=False)
    properties = ((state.get('state') or {}).get('ontology') or {}).get('properties') or []
    ts_nodes = [node for node in properties
                if '容量' in str(node.get('displayName') or node.get('name') or '')]
    check(status == 200 and 'xsd:xsd:' not in raw and ts_nodes
          and (((ts_nodes[0].get('dataType') or {}).get('valueType')) == 'double'),
          '交付草稿时序属性 valueType=double 且全文无 xsd:xsd: 双前缀（D02）',
          actual=(status, 'xsd:xsd:' in raw, ts_nodes[:1]))
    # P2-2（第四轮验收 20260921）：timeSeries 切回普通类型不得残留旧观测值类型——
    # dataType 改回 number 后，响应 candidate.fields 里不允许再出现 valueType 键
    # （历史实现会残留 double，与「valueType 仅 timeSeries 有意义」的冻结口径冲突）。
    number_revision = (fixed.get('candidate') or {}).get('revision') or revision
    status, back = api('/api/build-candidate-update',
                       {'candidateId': capacity['id'], 'fields': {'dataType': 'number'},
                        'revision': number_revision})
    fields_after = (back.get('candidate') or {}).get('fields') or {}
    check(status == 200 and fields_after.get('dataType') == 'number'
          and 'valueType' not in fields_after,
          'timeSeries 切回 number 后 fields 不残留 valueType 键（P2-2）',
          actual=(status, fields_after))


# --- 回归流 4b：规则/动作交付落位（D06） --------------------------------------------

def flow_rules_actions_delivery():
    """D06：交付含规则/动作的候选后，草稿落 workflow.businessRules/actions（带对象关联），
    本体 metadata 无自造 resourceType；交付草稿可经 /api/save 原样回环（协议合法）。"""
    task_id, batch_id, _run = setup_generated_task('规则动作交付回归')
    uid = require(_main_user_id(), '找不到测试账号 user_id')

    def seed(conn):
        ob_store.create_candidate(conn, task_id, uid, batch_id, {
            'type': 'rule', 'key': 'rule-soc-low', 'name': 'SOC告警下限',
            'definition': 'SOC 低于 10% 时触发告警', 'fields': {'content': 'soc < 0.10'},
            'ownerKey': 'obj-device', 'evidence': {}, 'evidenceStatus': 'supported',
            'decision': 'include', 'alignedKey': 'seed:rule:SOC告警下限'})
        ob_store.create_candidate(conn, task_id, uid, batch_id, {
            'type': 'rule', 'key': 'rule-name-unique', 'name': '命名唯一约束',
            'definition': '同类设备名称不得重复', 'fields': {},
            'ownerKey': 'obj-device', 'evidence': {}, 'evidenceStatus': 'supported',
            'decision': 'include', 'alignedKey': 'seed:rule:命名唯一约束'})
        ob_store.create_candidate(conn, task_id, uid, batch_id, {
            'type': 'action', 'key': 'act-reset-alarm', 'name': '复位告警',
            'definition': '复归设备当前活动告警', 'fields': {'effect': '告警状态被清除'},
            'ownerKey': 'obj-device', 'evidence': {}, 'evidenceStatus': 'supported',
            'decision': 'include', 'alignedKey': 'seed:action:复位告警'})

    with sto.write_tx() as tx:
        tx.run(seed)
    status, pre = api('/api/build-deliver-precheck', {'taskId': task_id})
    token = require(pre.get('checkToken'), '预检未返回 checkToken：%s' % _short(pre))
    check(status == 200 and pre.get('ok') is True and not pre.get('issues'),
          '含规则/动作的选定集合预检通过（D06）', actual=(status, pre.get('ok'), pre.get('issues')))
    status, delivered = api('/api/build-deliver', {'taskId': task_id, 'name': '规则动作交付回归本体',
                                                   'checkToken': token, 'requestId': 'req-rules-1'})
    check(status == 200, '含规则/动作交付 200', actual=(status, _short(delivered)))
    ontology_id = require(delivered.get('ontologyId'), '规则动作交付失败：%s' % _short(delivered))
    status, state = api('/api/state', query='?ontology=' + ontology_id)
    payload = (state.get('state') or {}) if status == 200 else {}
    ontology = payload.get('ontology') or {}
    workflow = payload.get('workflow') or {}
    rules = {str(r.get('name')): r for r in (workflow.get('businessRules') or [])}
    actions = {str(a.get('name')): a for a in (workflow.get('actions') or [])}
    check(status == 200 and 'SOC告警下限' in rules and '命名唯一约束' in rules
          and '复位告警' in actions,
          '/api/state 的 workflow.businessRules/actions 含交付内容（D06）',
          actual=(status, sorted(rules), sorted(actions)))
    soc = rules.get('SOC告警下限') or {}
    unique_rule = rules.get('命名唯一约束') or {}
    reset = actions.get('复位告警') or {}
    check(soc.get('description') == 'SOC 低于 10% 时触发告警' and soc.get('content') == 'soc < 0.10'
          and 'content' not in unique_rule and 'output' not in soc and 'output' not in unique_rule,
          '规则含名称/业务定义/选填内容，空内容不编造、不生成 output（D06）',
          actual=(soc, unique_rule))
    check(reset.get('effect') == '告警状态被清除' and reset.get('definitionVersion') == 2
          and reset.get('status') in ('experimental', 'active', 'deprecated'),
          '动作为简化动作 v2 结构（名称/定义/预期效果/状态）（D06）', actual=reset)
    object_ids = {node.get('id') for node in (ontology.get('objectTypes') or [])}
    rule_ids = {str(r.get('id')) for r in (workflow.get('businessRules') or [])}
    rule_assoc = workflow.get('businessRuleAssociations') or []
    action_assoc = workflow.get('actionAssociations') or []
    check(len(rule_assoc) == 2 and all(a.get('objectTypeId') in object_ids
                                       and a.get('ruleId') in rule_ids for a in rule_assoc)
          and len(action_assoc) == 1
          and action_assoc[0].get('objectTypeId') in object_ids
          and action_assoc[0].get('actionId') == reset.get('id'),
          '对象关联走现行关联集合、引用稳定 ID（D06）', actual=(rule_assoc, action_assoc))
    raw = json.dumps(payload, ensure_ascii=False)
    check('"mg:BusinessRule"' not in raw and '"mg:Action"' not in raw,
          '交付草稿全文无 mg:BusinessRule/mg:Action 自造 resourceType（D06）')
    defined = [node.get('id') for node in (ontology.get('objectTypes') or [])
               + (ontology.get('properties') or []) + (ontology.get('linkTypes') or [])]
    order = ontology.get('definitionOrder') or []
    check(set(order) == set(defined) and len(order) == len(defined),
          '规则/动作移出图后 definitionOrder 仍与图定义集合一致（D06）',
          actual=(order, defined))
    # 原样回环保存：交付草稿是协议合法的编辑器态（decode→validate→encode 无损）
    status, saved = api('/api/save', {'state': payload, 'revision': state.get('revision')})
    check(status == 200 and bool(saved.get('revision')),
          '交付草稿（含规则/动作）POST /api/save 原样回环 200（D06 协议合法）',
          actual=(status, _short(saved)))
    status, reload = api('/api/state', query='?ontology=' + ontology_id)
    wf_after = (reload.get('state') or {}).get('workflow') or {}
    check(status == 200 and len(wf_after.get('businessRules') or []) == 2
          and len(wf_after.get('actions') or []) >= 1
          and len(wf_after.get('businessRuleAssociations') or []) == 2,
          '保存回环后规则/动作与关联仍在 workflow（未落回 metadata）（D06）',
          actual=(status, sorted(str(r.get("name")) for r in (wf_after.get('businessRules') or []))))


# --- 回归流 5：写端点 revision 口径（D05 路由侧 + D10 checkToken 必填） ---------------
def flow_revision_contracts():
    """五类写端点：形态错误 400 / CAS 不匹配 409+currentRevision / 语义阻断 422+issues。"""
    task_id, batch_id, _run = setup_generated_task('修订口径回归')
    items, _body = candidates_by_name(task_id, batch_id)
    device = require(items.get('设备'), '候选缺少「设备」')
    capacity = require(items.get('额定容量'), '候选缺少「额定容量」')
    bogus = require(items.get('捏造字段'), '候选缺少「捏造字段」')
    _, detail = api('/api/build-task', query='?taskId=' + task_id)
    task_token = require((detail.get('task') or {}).get('revision'), '任务详情未返回 revision')
    scope_revision = int((detail.get('scope') or {}).get('revision') or 0)

    def err_code(body_obj):
        return body_obj.get('code')

    def current(body_obj):
        return body_obj.get('currentRevision')

    # 1) candidate-decide：revision 必填（缺失/空串 400），比对的是该候选的 r-uuid token
    status, missing = api('/api/build-candidate-decide',
                          {'candidateId': device['id'], 'decision': 'defer'})
    check(status == 400 and err_code(missing) == 'INVALID_ARGUMENT',
          '候选决定缺 revision → 400 INVALID_ARGUMENT（D05）', actual=(status, missing))
    status, empty = api('/api/build-candidate-decide',
                        {'candidateId': device['id'], 'decision': 'defer', 'revision': ''})
    check(status == 400 and err_code(empty) == 'INVALID_ARGUMENT',
          '候选决定空串 revision → 400（不再跳过 CAS）（D05）', actual=(status, empty))
    status, wrong = api('/api/build-candidate-decide',
                        {'candidateId': device['id'], 'decision': 'defer',
                         'revision': 'r-not-a-real-token'})
    check(status == 409 and err_code(wrong) == 'REVISION_CONFLICT'
          and current(wrong) == device['revision'],
          '候选决定传错 token → 409 + currentRevision=候选当前 token（D05）',
          actual=(status, wrong))
    status, decided = api('/api/build-candidate-decide',
                          {'candidateId': device['id'], 'decision': 'defer',
                           'revision': device['revision']})
    check(status == 200 and (decided.get('candidate') or {}).get('decision') == 'defer',
          '候选决定传正确 token 成功', actual=(status, decided))

    # 2) candidate-update：数字 revision → 400（必须是字符串 token）
    status, numeric = api('/api/build-candidate-update',
                          {'candidateId': capacity['id'],
                           'fields': {'definition': '额定容量（回归改写）'}, 'revision': 1})
    check(status == 400 and err_code(numeric) == 'INVALID_ARGUMENT',
          '候选编辑传数字 revision → 400（D05）', actual=(status, numeric))
    status, upd = api('/api/build-candidate-update',
                      {'candidateId': bogus['id'], 'fields': {'dataType': 'number'},
                       'revision': bogus['revision']})
    check(status == 200 and (upd.get('candidate') or {}).get('revision')
          != bogus['revision'], '候选编辑成功后推进候选 token',
          actual=(status, _short(upd)))

    # 3) candidates-merge：预览不带 revision；执行必须带保留项候选 token
    status, preview = api('/api/build-candidates-merge',
                          {'taskId': task_id, 'primaryId': capacity['id'],
                           'mergeIds': [bogus['id']], 'confirmed': False})
    check(status == 200 and isinstance(preview.get('fields'), list)
          and 'evidenceCount' in preview,
          '合并预览（confirmed=false）不带 revision 直接 200（响应为扁平预览对象）',
          actual=(status, _short(preview)))
    status, no_rev = api('/api/build-candidates-merge',
                         {'taskId': task_id, 'primaryId': capacity['id'],
                          'mergeIds': [bogus['id']], 'confirmed': True})
    check(status == 400 and err_code(no_rev) == 'INVALID_ARGUMENT',
          '执行合并缺 revision → 400（D05）', actual=(status, no_rev))
    status, blank_rev = api('/api/build-candidates-merge',
                            {'taskId': task_id, 'primaryId': capacity['id'],
                             'mergeIds': [bogus['id']], 'confirmed': True, 'revision': ''})
    check(status == 400 and err_code(blank_rev) == 'INVALID_ARGUMENT',
          '执行合并空串 revision → 400（不再退化为 409/无条件写）（D05）',
          actual=(status, blank_rev))
    status, task_tok = api('/api/build-candidates-merge',
                           {'taskId': task_id, 'primaryId': capacity['id'],
                            'mergeIds': [bogus['id']], 'confirmed': True,
                            'revision': task_token})
    check(status == 409 and err_code(task_tok) == 'REVISION_CONFLICT'
          and current(task_tok) == capacity['revision'],
          '执行合并误传任务 token → 409 且 currentRevision 是候选 token（D05）',
          actual=(status, task_tok))
    status, merged = api('/api/build-candidates-merge',
                         {'taskId': task_id, 'primaryId': capacity['id'],
                          'mergeIds': [bogus['id']], 'confirmed': True,
                          'revision': capacity['revision']})
    op_id = require(merged.get('opId'), '合并执行未返回 opId：%s' % _short(merged))
    merged_revision = (merged.get('candidate') or {}).get('revision')
    check(status == 200 and bool(op_id) and merged_revision
          and merged_revision != capacity['revision'],
          '执行合并（保留项候选 token）成功并推进 token', actual=(status, _short(merged)))

    # 4) review-undo：必填候选 token；错 token 409
    status, no_rev = api('/api/build-review-undo', {'taskId': task_id, 'opId': op_id})
    check(status == 400 and err_code(no_rev) == 'INVALID_ARGUMENT',
          '撤销缺 revision → 400（D05）', actual=(status, no_rev))
    status, blank_rev = api('/api/build-review-undo',
                            {'taskId': task_id, 'opId': op_id, 'revision': ''})
    check(status == 400 and err_code(blank_rev) == 'INVALID_ARGUMENT',
          '撤销空串 revision → 400（域层「空值跳过比对」在路由层被收口）（D05）',
          actual=(status, blank_rev))
    status, wrong = api('/api/build-review-undo',
                        {'taskId': task_id, 'opId': op_id, 'revision': task_token})
    check(status == 409 and err_code(wrong) == 'REVISION_CONFLICT'
          and current(wrong) == merged_revision,
          '撤销传错 token → 409 + currentRevision=保留项当前 token（D05）',
          actual=(status, wrong))
    status, undone = api('/api/build-review-undo',
                         {'taskId': task_id, 'opId': op_id, 'revision': merged_revision})
    check(status == 200 and undone.get('ok') is True
          and (undone.get('candidate') or {}).get('id') == capacity['id'],
          '撤销传正确 token 成功', actual=(status, _short(undone)))

    # 5) regenerate：revision 可选整数（scopeRevision），不再强读 r-uuid token
    status, token_rev = api('/api/build-regenerate',
                            {'taskId': task_id, 'revision': 'r-not-an-integer'})
    check(status == 400 and err_code(token_rev) == 'INVALID_ARGUMENT',
          '再生成传候选/任务 token → 400（必须是整数，不再按 r-uuid 强读）（D05）',
          actual=(status, token_rev))
    status, stale_rev = api('/api/build-regenerate', {'taskId': task_id,
                                                      'revision': scope_revision + 7})
    check(status == 409 and err_code(stale_rev) == 'REVISION_CONFLICT'
          and current(stale_rev) == str(scope_revision),
          '再生成传过期整数 → 409 + currentRevision=当前 scopeRevision（D05）',
          actual=(status, stale_rev))
    status, regen = api('/api/build-regenerate', {'taskId': task_id})
    run2 = poll_run(task_id, require(regen.get('runId'), '再生成（省略 revision）未返回 runId'))
    b2 = require(regen.get('batchId'), '再生成未返回 batchId：%s' % _short(regen))
    check(status == 200 and run2.get('state') == 'succeeded',
          '再生成省略 revision → 跳过范围比对并成功起跑（08 §7）',
          actual=(status, run2.get('state'), run2.get('error')))
    status, regen2 = api('/api/build-regenerate', {'taskId': task_id,
                                                   'revision': scope_revision})
    run3 = poll_run(task_id, require(regen2.get('runId'), '按整数 revision 再生成未返回 runId'))
    check(status == 200 and run3.get('state') == 'succeeded',
          '再生成传当前 scopeRevision 整数同样通过', actual=(status, run3.get('state')))

    # 6) diff-resolve：该候选的候选级 CAS
    items_b2, _ = candidates_by_name(task_id, b2)
    dev2 = require(items_b2.get('设备'), '新批次缺少「设备」')
    status, no_rev = api('/api/build-diff-resolve',
                         {'taskId': task_id, 'candidateId': dev2['id'], 'choice': 'acceptNew'})
    check(status == 400 and err_code(no_rev) == 'INVALID_ARGUMENT',
          '差异裁决缺 revision → 400（D05）', actual=(status, no_rev))
    status, blank_rev = api('/api/build-diff-resolve',
                            {'taskId': task_id, 'candidateId': dev2['id'],
                             'choice': 'acceptNew', 'revision': ''})
    check(status == 400 and err_code(blank_rev) == 'INVALID_ARGUMENT',
          '差异裁决空串 revision → 400（D05）', actual=(status, blank_rev))
    status, wrong = api('/api/build-diff-resolve',
                        {'taskId': task_id, 'candidateId': dev2['id'],
                         'choice': 'acceptNew', 'revision': task_token})
    check(status == 409 and err_code(wrong) == 'REVISION_CONFLICT'
          and current(wrong) == dev2['revision'],
          '差异裁决误传任务 token → 409 + currentRevision=候选 token（D05）',
          actual=(status, wrong))
    status, resolved = api('/api/build-diff-resolve',
                           {'taskId': task_id, 'candidateId': dev2['id'],
                            'choice': 'acceptNew', 'revision': dev2['revision']})
    check(status == 200 and (resolved.get('candidate') or {}).get('id') == dev2['id'],
          '差异裁决传正确候选 token 成功', actual=(status, _short(resolved)))

    # 7) material-exclude：revision 是 materialRevision 的字符串形态（必填）
    status, mats = api('/api/build-materials', query='?taskId=' + task_id)
    material_rev = int(mats.get('revision') or 0)
    material_id = require((mats.get('items') or [{}])[0].get('id'), '材料清单为空')
    for label, payload_body in (
            ('缺 revision', {'taskId': task_id, 'materialId': material_id, 'excluded': True}),
            ('数字 revision', {'taskId': task_id, 'materialId': material_id, 'excluded': True,
                               'revision': material_rev}),
            ('空串 revision', {'taskId': task_id, 'materialId': material_id, 'excluded': True,
                               'revision': ''})):
        status, body_obj = api('/api/build-material-exclude', payload_body)
        check(status == 400 and err_code(body_obj) == 'INVALID_ARGUMENT',
              '物料排除 %s → 400 INVALID_ARGUMENT（D05）' % label, actual=(status, body_obj))
    status, mismatch = api('/api/build-material-exclude',
                           {'taskId': task_id, 'materialId': material_id, 'excluded': True,
                            'revision': str(material_rev + 5)})
    check(status == 409 and err_code(mismatch) == 'REVISION_CONFLICT'
          and current(mismatch) == str(material_rev),
          '物料排除传错字符串 → 409 + currentRevision=字符串物料修订（D05）',
          actual=(status, mismatch))
    status, excluded = api('/api/build-material-exclude',
                           {'taskId': task_id, 'materialId': material_id, 'excluded': True,
                            'revision': str(material_rev)})
    check(status == 200 and (excluded.get('material') or {}).get('excluded') is True
          and int((excluded.get('task') or {}).get('materialRevision') or 0) == material_rev + 1,
          '物料排除按 String(materialRevision) 通过并推进修订', actual=(status, _short(excluded)))


# --- 白盒与小单元：D13 fencing / D02 / D08 / D19 / D12 ------------------------------
def flow_lease_fencing(task_id):
    """D13：lease 条件写回与重试轮换（存储层白盒）+ runner 阶段边界失配。"""
    uid = require(_main_user_id(), '找不到测试账号 user_id')
    outcome = {}

    def body(conn):
        run_id, lease = ob_store.create_run(conn, task_id, uid, 'generate', {}, '')
        outcome['run_id'], outcome['lease'] = run_id, lease
        outcome['wrong_hit'] = ob_store.update_run(conn, run_id, uid, state='running',
                                                   stage='x', lease='not-my-lease')
        outcome['state_after_wrong'] = (ob_store.run_state(conn, run_id, uid) or {}).get('state')
        outcome['right_hit'] = ob_store.update_run(conn, run_id, uid, state='running',
                                                   stage='y', lease=lease)
        ob_store.update_run(conn, run_id, uid, attempt=2)  # 重试接管：轮换 lease
        outcome['new_lease'] = (ob_store.run_state(conn, run_id, uid) or {}).get('lease_token')
        outcome['stale_hit'] = ob_store.update_run(conn, run_id, uid, state='succeeded',
                                                   error='迟到的旧结果', lease=lease)
        outcome['final'] = ob_store.run_state(conn, run_id, uid)

    with sto.write_tx() as tx:
        tx.run(body)
    run_id = outcome.get('run_id') or ''
    check(outcome.get('wrong_hit') is False and outcome.get('state_after_wrong') == 'queued',
          '携带错误 lease 的写回被拒绝且未改状态（D13）', actual=dict(outcome, run_id=None))
    check(bool(outcome.get('right_hit')), '携带正确 lease 的写回命中（D13）')
    check(outcome.get('new_lease') and outcome.get('new_lease') != outcome.get('lease'),
          'attempt 推进（重试接管）轮换 lease_token（D13）',
          actual=(outcome.get('lease'), outcome.get('new_lease')))
    check(outcome.get('stale_hit') is False
          and (outcome.get('final') or {}).get('state') != 'succeeded',
          '轮换后旧 worker 的迟到终态写回被丢弃（D13：取消+重试不双写）',
          actual=(outcome.get('stale_hit'), (outcome.get('final') or {}).get('state')))
    raised = False
    runner._leases[(str(uid), str(run_id))] = 'stale-worker-token'
    try:
        with sto.read_connection() as conn:
            try:
                runner.check_cancelled(conn, run_id, uid)
            except runner.Cancelled:
                raised = True
    finally:
        runner._leases.pop((str(uid), str(run_id)), None)
    check(raised, 'runner 阶段边界：lease 失配立即 Cancelled（D13）')


def unit_domain_rules():
    # D02：协议枚举与模型格式单一来源
    check(set(protocol.VALUE_TYPES) == set(model_format.SERIES_VALUE_TYPES),
          'protocol.VALUE_TYPES 与 model_format.SERIES_VALUE_TYPES 完全一致（D02）',
          actual=(protocol.VALUE_TYPES, sorted(model_format.SERIES_VALUE_TYPES)))

    # D02：适配器单前缀 + 脏值拒绝
    obj = {'id': 'c1', 'key': 'o1', 'type': 'object', 'name': '设备', 'definition': '台账',
           'fields': {}, 'ownerKey': '', 'evidence': {'_record': ['f1']},
           'evidenceStatus': 'supported', 'conflicts': []}
    prop = {'id': 'c2', 'key': 'p1', 'type': 'property', 'name': '功率', 'definition': '有功功率',
            'fields': {'dataType': 'timeSeries', 'valueType': 'double'}, 'ownerKey': 'o1',
            'evidence': {}, 'evidenceStatus': 'supported', 'conflicts': []}
    ontology, _workflow, _id_map, _warnings = adapter_mod.assemble([obj, prop])
    graph_text = json.dumps(ontology, ensure_ascii=False)
    check('"xsd:double"' in graph_text and 'xsd:xsd:' not in graph_text,
          'timeSeries range 为单一前缀 xsd:double（D02）', actual=graph_text[:200])
    missing = dict(prop, id='c3', key='p2', fields={'dataType': 'timeSeries'})
    raised = False
    try:
        adapter_mod.assemble([obj, missing])
    except adapter_mod.AdapterError:
        raised = True
    check(raised, '缺 valueType 的时序属性装配即报错（不再静默降级为文本）（D02）')
    bad = dict(prop, id='c4', key='p3', fields={'dataType': 'timeSeries', 'valueType': 'number'})
    raised = False
    try:
        adapter_mod.assemble([obj, bad])
    except adapter_mod.AdapterError:
        raised = True
    check(raised, 'valueType 不在枚举内（旧值 number/text）抛 AdapterError（D02）')

    # D06：规则/动作装配进 workflow，不进本体 @graph；关联用宿主对象稳定 ID
    rule = {'id': 'c5', 'key': 'r1', 'type': 'rule', 'name': '功率约束', 'definition': '储能设备功率上限',
            'fields': {'content': 'p <= 额定容量 × 2'}, 'ownerKey': 'o1',
            'evidence': {}, 'evidenceStatus': 'supported', 'conflicts': []}
    action = {'id': 'c6', 'key': 'a1', 'type': 'action', 'name': '复位告警', 'definition': '复归设备活动告警',
              'fields': {}, 'ownerKey': 'o1',
              'evidence': {}, 'evidenceStatus': 'supported', 'conflicts': []}
    onto2, wf2, _map2, _warn2 = adapter_mod.assemble([obj, prop, rule, action])
    graph_text2 = json.dumps(onto2, ensure_ascii=False)
    check('mg:BusinessRule' not in graph_text2 and 'mg:Action' not in graph_text2
          and all(node.get('@type') in adapter_mod.NODE_TYPES.values() for node in onto2['@graph']),
          '本体 @graph 不再出现 mg:BusinessRule/mg:Action 自造节点类型（D06）',
          actual=[node.get('@type') for node in onto2['@graph']])
    rule_rec = (wf2.get('businessRules') or [{}])[0]
    action_rec = (wf2.get('actions') or [{}])[0]
    obj_id = onto2['@graph'][0]['@id']
    check(rule_rec.get('name') == '功率约束' and rule_rec.get('description') == '储能设备功率上限'
          and rule_rec.get('content') == 'p <= 额定容量 × 2' and 'output' not in rule_rec
          and str(rule_rec.get('id', '')).startswith('rule_'),
          '规则 → workflow.businessRules：name/description/content 按现行协议、不造 output（D06）',
          actual=rule_rec)
    check(action_rec.get('name') == '复位告警' and action_rec.get('description') == '复归设备活动告警'
          and action_rec.get('definitionVersion') == 2 and 'effect' not in action_rec
          and action_rec.get('status') in ('experimental', 'active', 'deprecated')
          and str(action_rec.get('id', '')).startswith('act_'),
          '动作 → workflow.actions：简化动作 v2 结构，effect 选填不编造（D06）', actual=action_rec)
    check(wf2.get('businessRuleAssociations') == [{'objectTypeId': obj_id, 'ruleId': rule_rec.get('id')}]
          and wf2.get('actionAssociations') == [{'objectTypeId': obj_id, 'actionId': action_rec.get('id')}],
          '规则/动作经关联集合用宿主对象稳定 @id 引用（D06）',
          actual=(wf2.get('businessRuleAssociations'), wf2.get('actionAssociations')))
    check(adapter_mod.verify_structure(onto2, wf2) == [],
          '图 + workflow 装配通过交付前结构自检（D06）',
          actual=adapter_mod.verify_structure(onto2, wf2))
    dangling = {'businessRules': [], 'actions': [{'id': 'act_x', 'name': '悬空', 'description': 'd'}],
                'businessRuleAssociations': [],
                'actionAssociations': [{'objectTypeId': 'obj_missing', 'actionId': 'act_x'}]}
    check(bool(adapter_mod.verify_structure(onto2, dangling)),
          '悬空动作关联被结构自检报告（D06 自检有效）')
    state2 = adapter_mod.build_state(onto2, 'D06 自检', wf2)
    check(len(state2['workflow'].get('businessRules') or []) == 1
          and len(state2['workflow'].get('actions') or []) == 1
          and len(state2['workflow'].get('businessRuleAssociations') or []) == 1
          and len(state2['workflow'].get('actionAssociations') or []) == 1
          and state2['ontology'].get('@graph') == onto2['@graph'],
          'build_state 把规则/动作写进 state.workflow，本体保持图形态（D06）',
          actual=list(state2['workflow']))

    # D08：宿主参与对齐键（键与 ID 引用都解析；跨批次用规范名）
    def _obj(ckey, cid, name):
        return {'id': cid, 'key': ckey, 'type': 'object', 'name': name, 'definition': 'd',
                'fields': {}, 'ownerKey': '', 'evidence': {'_record': ['f']},
                'evidenceStatus': 'supported', 'conflicts': []}

    def _prop(ckey, name, owner, data_type='number'):
        return {'id': ckey, 'key': ckey, 'type': 'property', 'name': name, 'definition': 'd',
                'fields': {'dataType': data_type}, 'ownerKey': owner,
                'evidence': {'definition': ['f']}, 'evidenceStatus': 'supported', 'conflicts': []}

    different = alignment.align([_obj('oa', 'i1', '设备'), _obj('ob', 'i2', '储能簇'),
                                 _prop('p1', '容量', 'oa'), _prop('p2', '容量', 'ob')])
    check(len(different['candidates']) == 4,
          '同名同 dataType 不同宿主：不自动合并（D08）',
          actual=len(different['candidates']), expected=4)
    mixed = alignment.align([_obj('oa', 'i1', '设备'), _obj('ob', 'i2', '储能簇'),
                             _prop('p1', '容量', 'oa'), _prop('p2', '容量', 'i1')])
    merged_prop = [c for c in mixed['candidates'] if c['type'] == 'property']
    check(len(mixed['candidates']) == 3 and len(merged_prop) == 1,
          'ownerKey 用键或对象 ID 都解析到同一宿主并正常合并（D08）',
          actual=(len(mixed['candidates']), len(merged_prop)))
    check(merged_prop and merged_prop[0].get('alignedKey') == 'property:容量#number@设备',
          'alignedKey 携带规范化宿主名（跨批次稳定）（D08）',
          actual=merged_prop[0].get('alignedKey') if merged_prop else None)

    # D19：范围冲突判定收紧为全等 + 逐对豁免 + 长词不丢
    def _scope(**over):
        base = {'goal': 'g', 'include': '', 'exclude': '', 'coverage': '', 'openQuestions': []}
        base.update(over)
        return base

    codes_of = lambda issues: [i.get('code') for i in issues]  # noqa: E731
    check(not codes_of(tasks_domain.scope_has_blocking_issues(
        _scope(include='储能', exclude='储能簇'))),
        '「储能」与「储能簇」不再前缀子串误报（D19）')
    long_term = '电池簇电压均衡控制策略说明'
    check('SCOPE_CONFLICT' in codes_of(tasks_domain.scope_has_blocking_issues(
        _scope(include=long_term, exclude=long_term))),
        '超过 12 字的长词冲突仍被检出（D19）', actual=long_term)
    check('SCOPE_CONFLICT' in codes_of(tasks_domain.scope_has_blocking_issues(
        _scope(include='PCS、BMS', exclude='PCS', coverage='本期聚焦电芯'))),
        '无关的覆盖说明不再全局豁免冲突（D19）')
    check(not codes_of(tasks_domain.scope_has_blocking_issues(
        _scope(include='PCS、BMS', exclude='PCS', coverage='PCS 先按排除处理，口径以本文说明为准'))),
        '覆盖说明点名该冲突词后豁免（D19）')

    # D12：zip_bomb_guard 压缩比 + 展开期实际字节预算
    bomb_path = TMP / 'zipbomb-unit.zip'
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('zeros.bin', bytes(5 * 1024 * 1024))
    bomb_path.write_bytes(buffer.getvalue())
    ok, reason = materials_domain.zip_bomb_guard(bomb_path)
    check(ok is False and '解压炸弹' in reason,
          '5MiB 声明 + 极小压缩量被压缩比规则拒绝（D12）', actual=(ok, reason))
    normal_path = TMP / 'zipnormal-unit.zip'
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('a.md', '# 设备说明\n' * 50)
    normal_path.write_bytes(buffer.getvalue())
    ok, reason = materials_domain.zip_bomb_guard(normal_path)
    check(ok is True, '普通文本压缩包通过防爆检查（D12 无误伤）', actual=(ok, reason))
    saved_limit = protocol.ZIP_EXPANDED_BYTES
    real_path = TMP / 'zipreal-unit.zip'
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('big.txt', b'x' * 2000)
    real_path.write_bytes(buffer.getvalue())
    raised_limit = False
    budget = {'bytes': 0, 'entries': 0, 'real': 0}
    try:
        protocol.ZIP_EXPANDED_BYTES = 1000  # 预算压到 1KB：2000 字节的合法条目也必须中止
        with zipfile.ZipFile(str(real_path)) as archive:
            info = archive.infolist()[0]
            try:
                materials_domain._write_member(archive, info, TMP / 'member-part.out',
                                               int(info.file_size), budget)
            except materials_domain.LimitExceeded:
                raised_limit = True
    finally:
        protocol.ZIP_EXPANDED_BYTES = saved_limit
    check(raised_limit and budget['real'] > 1000,
          '实际解压字节超过共享预算立即中止（D12 实字节计数）',
          actual=(raised_limit, budget))


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
    check(status == 422 and blocked.get('code') == 'INVALID_STATE'
          and 'SCOPE_CONFLICT' in codes,
          '纳入/排除同词且无覆盖说明 → 范围确认被阻断（422 INVALID_STATE + issues）',
          actual=(status, blocked.get('code'), blocked.get('issues')))
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
    # D10：同 requestId 换 payload（换名）→ 幂等指纹失配 → 409 REVISION_CONFLICT
    status, mutated = api('/api/build-deliver', {'taskId': task_id, 'name': '同请求号换内容',
                                                 'checkToken': token, 'requestId': request_id})
    check(status == 409 and mutated.get('code') == 'REVISION_CONFLICT',
          '同 requestId 不同 payload → 409 REVISION_CONFLICT（D10）',
          actual=(status, mutated.get('code'), mutated.get('message')))
    # D10：已交付任务 + 不同 requestId → 409 ALREADY_DELIVERED（不再是 422 清单）
    status, other = api('/api/build-deliver', {'taskId': task_id, 'name': '端到端设备本体二次',
                                               'checkToken': token, 'requestId': 'req-build-e2e-2'})
    check(status == 409 and other.get('code') == 'ALREADY_DELIVERED'
          and not (other.get('issues') or []),
          '不同 requestId 再次交付 → 409 ALREADY_DELIVERED（D10）', actual=(status, other))
    status, precheck2 = api('/api/build-deliver-precheck', {'taskId': task_id})
    codes2 = [issue.get('code') for issue in (precheck2.get('issues') or [])]
    check(status == 422 and precheck2.get('code') == 'INVALID_STATE'
          and 'ALREADY_DELIVERED' in codes2,
          '交付后预检 422 INVALID_STATE + issues 含 ALREADY_DELIVERED（D10/D17 口径）',
          actual=(status, precheck2.get('code'), codes2))
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
    status, evil = api('/api/build-upload-init',
                       {'taskId': task_id, 'relPath': '../evil.txt', 'size': 10})
    check(status == 400 and evil.get('code') == 'INVALID_ARGUMENT'
          and not db_rows("SELECT 1 FROM wb_build_uploads WHERE rel_path LIKE '%evil%'"),
          '上传路径穿越被拒（400 INVALID_ARGUMENT）且未登记上传会话', actual=(status, evil))
    status, big = api('/api/build-upload-init',
                      {'taskId': task_id, 'relPath': 'big.bin',
                       'size': protocol.FILE_BYTES + 1024})
    check(status == 422 and big.get('code') == 'LIMIT_EXCEEDED'
          and not db_rows("SELECT 1 FROM wb_build_uploads WHERE rel_path = 'big.bin'"),
          '超过单文件限额的上传被拒（422 LIMIT_EXCEEDED，不落库）', actual=(status, big))

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
    check(zip_init_status == 200 and status == 422 and zip_result.get('code') == 'ZIP_INVALID'
          and after == before and not leaked
          and not db_rows("SELECT 1 FROM wb_build_materials WHERE rel_path LIKE '%readme%'"),
          'ZIP 目录穿越整包被拒（422 ZIP_INVALID）：无材料登记、无文件逃逸',
          actual=(zip_init_status, status, before, after, [str(p) for p in leaked],
                  _short(zip_result)))

    # 11b) 真实分片上传（D01）：>384 字节的分片必须能通过，多分片续传/幂等/冲突码必须正确
    status, bound_task = api('/api/build-task-create', {'name': '分片上限回归'})
    bound_id = require((bound_task.get('task') or {}).get('id'),
                       '分片回归任务创建失败：%s' % _short(bound_task))
    # 5KB 单分片（此前必 400「参数 dataBase64 过长」）
    small = bytes((1000 + i * 7) % 251 for i in range(5 * 1024))
    status, small_result = _upload_bytes(small, bound_id, 'notes/blocks.bin.txt')
    check(status == 200 and (small_result.get('materials') or [{}])[0].get('size') == len(small),
          '>384 字节分片上传成功并登记材料（D01）', actual=(status, _short(small_result)))
    # 多分片（>chunkBytes）：续传幂等、跳号 400、乱序内容 409、摘要不符 422、完整后登记
    total_bytes = protocol.CHUNK_BYTES + 88_000
    blob = bytes((3 + i * 31) % 256 for i in range(4096)) * (total_bytes // 4096 + 1)
    blob = blob[:total_bytes]
    status, init = api('/api/build-upload-init',
                       {'taskId': bound_id, 'relPath': 'notes/big.bin', 'size': len(blob)})
    upload_id = require(init.get('uploadId'), '多分片上传 init 失败：%s' % _short(init))
    check(status == 200 and init.get('chunkBytes') == protocol.CHUNK_BYTES
          and init.get('maxChunks') == 2,
          '多分片 init 返回 chunkBytes/maxChunks', actual=(status, init))
    first = blob[:protocol.CHUNK_BYTES]
    second = blob[protocol.CHUNK_BYTES:]
    first_hash = hashlib.sha256(first).hexdigest()
    status, one = api('/api/build-upload-chunk',
                      {'uploadId': upload_id, 'index': 0, 'hash': first_hash,
                       'dataBase64': base64.b64encode(first).decode()})
    check(status == 200 and one.get('received') == 1 and one.get('nextIndex') == 1,
          '512KB 分片（base64 %d 字符）上传成功' % len(base64.b64encode(first).decode()),
          actual=(status, one))
    status, again = api('/api/build-upload-chunk',
                        {'uploadId': upload_id, 'index': 0, 'hash': first_hash,
                         'dataBase64': base64.b64encode(first).decode()})
    check(status == 200 and again.get('received') == 1,
          '同 index 同 hash 幂等重放', actual=(status, again))
    status, conflict = api('/api/build-upload-chunk',
                           {'uploadId': upload_id, 'index': 0,
                            'hash': hashlib.sha256(second).hexdigest(),
                            'dataBase64': base64.b64encode(second).decode()})
    check(status == 409 and conflict.get('code') == 'UPLOAD_CONFLICT',
          '同 index 不同内容 → 409 UPLOAD_CONFLICT（D07）', actual=(status, conflict))
    status, skip = api('/api/build-upload-chunk',
                       {'uploadId': upload_id, 'index': 5, 'hash': first_hash,
                        'dataBase64': base64.b64encode(first).decode()})
    check(status == 400 and skip.get('code') == 'INVALID_ARGUMENT',
          '跳号分片 → 400', actual=(status, skip))
    status, mismatch = api('/api/build-upload-chunk',
                           {'uploadId': upload_id, 'index': 1, 'hash': '0' * 64,
                            'dataBase64': base64.b64encode(second).decode()})
    check(status == 422 and mismatch.get('code') == 'HASH_MISMATCH',
          '分片摘要不符 → 422 HASH_MISMATCH（D07）', actual=(status, mismatch))
    status, oversize = api('/api/build-upload-chunk',
                           {'uploadId': upload_id, 'index': 1, 'hash': first_hash,
                            'dataBase64': 'A' * (protocol.CHUNK_BASE64_CHARS + 8)})
    check(status == 422 and oversize.get('code') == 'LIMIT_EXCEEDED',
          'base64 超过 chunkBytes 上限 → 422 LIMIT_EXCEEDED（不是 400 参数过长）',
          actual=(status, oversize))
    status, two = api('/api/build-upload-chunk',
                      {'uploadId': upload_id, 'index': 1,
                       'hash': hashlib.sha256(second).hexdigest(),
                       'dataBase64': base64.b64encode(second).decode()})
    check(status == 200 and two.get('received') == 2, '尾分片上传成功', actual=(status, two))
    status, early = api('/api/build-upload-complete',
                        {'uploadId': upload_id, 'finalHash': 'f' * 64})
    check(status == 422 and early.get('code') == 'HASH_MISMATCH',
          '整体摘要不符 → 422 HASH_MISMATCH（D07）', actual=(status, early))
    status, done = api('/api/build-upload-complete',
                       {'uploadId': upload_id,
                        'finalHash': hashlib.sha256(blob).hexdigest()})
    materials = done.get('materials') or []
    check(status == 200 and len(materials) == 1 and materials[0].get('size') == len(blob),
          '多分片续传完成后按声明大小登记材料', actual=(status, _short(done)))
    status, abort_other = http('/api/build-upload-abort', {'uploadId': upload_id},
                              token=other_token)
    check(status == 404 and abort_other.get('code') == 'NOT_FOUND',
          '跨账号 abort 上传会话按不存在处理（404，D07）', actual=(status, abort_other))
    # D07 路由侧：四端点都必须把域层细分码原样透出（不得压成 400 INVALID_ARGUMENT）
    status, init_ghost = api('/api/build-upload-init',
                             {'taskId': 'no-such-task', 'relPath': 'x.md', 'size': 10})
    check(status == 404 and init_ghost.get('code') == 'NOT_FOUND',
          'init 传不存在任务 → 404 NOT_FOUND（D07）', actual=(status, init_ghost))
    for label, endpoint, ghost in (
            ('chunk', '/api/build-upload-chunk',
             {'uploadId': 'no-such-upload', 'index': 0, 'hash': '0' * 64,
              'dataBase64': base64.b64encode(b'x').decode()}),
            ('complete', '/api/build-upload-complete',
             {'uploadId': 'no-such-upload', 'finalHash': '0' * 64}),
            ('abort', '/api/build-upload-abort', {'uploadId': 'no-such-upload'})):
        status, body_obj = api(endpoint, ghost)
        check(status == 404 and body_obj.get('code') == 'NOT_FOUND',
              '%s 不存在/已结束的上传会话 → 404 NOT_FOUND（D07）' % label,
              actual=(status, body_obj))
    # 已完成会话不可再用：域层按「不存在」处理（404），不是 400/500
    status, after_done = api('/api/build-upload-complete',
                             {'uploadId': upload_id,
                              'finalHash': hashlib.sha256(blob).hexdigest()})
    check(status == 404 and after_done.get('code') == 'NOT_FOUND',
          'complete 成功后重复 complete → 404 NOT_FOUND（会话已关闭，D07）',
          actual=(status, after_done))
    check('scope' in FakeLlm.calls and 'extract' in FakeLlm.calls,
          '全程只经本地假 LLM（范围澄清与候选抽取各至少一次）', actual=FakeLlm.calls)

    # 12) 验收缺陷防回归流（D02/D03/D04/D08/D09/D11/D12/D13/D14/D17/D19 + usage）
    unit_domain_rules()
    flow_review_semantics()
    flow_bad_candidates()
    flow_timeseries_delivery()
    flow_rules_actions_delivery()
    # D05/D10 路由层：五类写端点 revision 口径与交付令牌必填
    flow_revision_contracts()
    flow_lease_fencing(task_id)
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
