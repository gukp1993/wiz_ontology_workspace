"""生成控输出 v3 在线接线回归（D16）：真实隔离 HTTP 服务 + 本地假 LLM + schema2 计划。

覆盖（08 §14 / 整合计划 v3 §9 场景 2、9 摘要部分）：
1. capabilities.generationBudget 生效值展示（默认关 → 开）。
2. scope-confirm 预检：profile 绑定不符 → 422 BUDGET_PROFILE_MISMATCH，task/run 零状态变更。
3. 全链路 v2 生成：schema2 轮询摘要（jobs/coverage/budget）、usage token 统计、候选落库。
4. resume 守卫：未知 schemaVersion → 422 拒绝且候选不删；blocked → 422 需新建计划；
   损坏的非整数版本（'x'/'2.5'）同样 422 不裸抛 500，合法 schema2 仍 200 受理。
5. abstract 重抽象：新 epoch 且再次成功。

隔离：WIZ_WORKBENCH_ROOT/WIZ_DATABASE_URL 指向临时目录；端口动态；假 LLM 在 127.0.0.1。
运行：python3 tests/run.py --test tests/test_ontology_build_budget_http.py
"""
import base64
import hashlib
import json
import os
import shutil
import socket
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

TMP = Path(tempfile.mkdtemp(prefix='wiz_budget_http_'))
os.environ['WIZ_WORKBENCH_ROOT'] = str(TMP)
os.environ['WIZ_DATABASE_URL'] = 'sqlite:///' + str(TMP / 'data' / 'workbench.sqlite3')
# 默认关闭：先在关闭状态断言 capabilities，再在测试中打开
os.environ.pop('WIZ_BUILD_ADAPTIVE_BATCHING', None)

import auth_client  # noqa: E402
from workbench import storage  # noqa: E402

TERMINAL = ('succeeded', 'failed', 'cancelled', 'interrupted')
BOGUS_FACT_ID = 'bf-DOES-NOT-EXIST-9999'

PASSED = []
FAILED = []


def check(name, ok, actual=None, expected=None):
    if ok:
        PASSED.append(name)
    else:
        FAILED.append(name)
        print('[失败] %s\n  实际=%s\n  期望=%s' % (name, str(actual)[:400], str(expected)[:400]))


class FakeLlm(BaseHTTPRequestHandler):
    calls = []

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
        if '抽取器' in system:
            FakeLlm.calls.append('extract')
            payload = self._extract_payload(user_text)
        else:
            FakeLlm.calls.append('scope')
            payload = {'questions': [], 'patch': {}, 'notes': ['假模型范围应答']}
        self._reply(200, {'choices': [{'message': {'content': json.dumps(payload, ensure_ascii=False)},
                                       'finish_reason': 'stop'}],
                          'usage': {'prompt_tokens': 210, 'completion_tokens': 90,
                                    'total_tokens': 300}})

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
    request = urllib.request.Request(url, data=data, headers=headers, method='POST' if data else 'GET')
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read().decode() or '{}')
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode() or '{}')
        except ValueError:
            return exc.code, {}


def poll_run(task_id, run_id, timeout=45):
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


def make_material(task_id):
    """上传一个合成 JSON 材料并扫描（内容确定、无真实数据）。"""
    material = json.dumps({'station': {'name': '示例储能站', 'ratedPower': '100kW',
                                       'battery': {'cells': 12, 'vendor': '示例厂商'}}},
                          ensure_ascii=False).encode()
    status, init = api('/api/build-upload-init', {'taskId': task_id, 'relPath': 'sample.json',
                                                  'size': len(material)})
    assert status == 200, init
    upload_id = init['uploadId']
    chunk = base64.b64encode(material).decode()
    status, _ = api('/api/build-upload-chunk', {'uploadId': upload_id, 'index': 0,
                                                'hash': hashlib.sha256(material).hexdigest(),
                                                'dataBase64': chunk})
    assert status == 200
    status, done = api('/api/build-upload-complete', {'uploadId': upload_id,
                                                      'finalHash': hashlib.sha256(material).hexdigest()})
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


def setup_scope(task_id):
    status, saved = api('/api/build-scope-save',
                        {'taskId': task_id,
                         'scope': {'goal': '建立电站设备本体', 'include': '电站、电池、功率',
                                   'exclude': '', 'relations': '', 'coverage': ''},
                         'revision': 0})
    assert status == 200, saved
    return saved['scope']['revision']


def main():
    global TOKEN, USER_ID
    _user, token = auth_client.register_or_login(BASE, 'budget_http_main', 'test1234')
    TOKEN = token
    with sto.read_connection() as conn:
        row = conn.execute(sto.text("SELECT user_id FROM wb_users WHERE username = 'budget_http_main'")
                           ).mappings().first()
    USER_ID = str(row['user_id'])

    # 1) capabilities：默认关闭
    status, caps = api('/api/build-capabilities')
    budget = caps.get('generationBudget') or {}
    check('capabilities 默认关闭（enabled=false 且无错误）',
          status == 200 and budget.get('enabled') is False and budget.get('configErrors') == [],
          budget, {'enabled': False})
    check('capabilities 展示计划限额（4096/512/1024/1MiB）',
          budget.get('planTargets') == 4096 and budget.get('planJobs') == 512
          and budget.get('planAttempts') == 1024 and budget.get('checkpointBytes') == 1048576,
          budget, 'plan limits')

    # 开启（绑定稍后注册的 provider——先占位）
    os.environ['WIZ_BUILD_ADAPTIVE_BATCHING'] = '1'
    os.environ['WIZ_BUILD_CONTEXT_TOKENS'] = '131072'
    os.environ['WIZ_BUILD_OUTPUT_LIMIT_TOKENS'] = '32000'
    os.environ['WIZ_BUILD_PROFILE_MODEL'] = 'fake-1'

    # 2) provider 与任务
    status, provider_resp = api('/api/llm-provider-save',
                                {'name': 'fake-local', 'endpoint': LLM_URL, 'model': 'fake-1',
                                 'apiKey': 'sk-fake', 'timeout': 30, 'temperature': 0,
                                 'isDefault': True})
    provider_id = (provider_resp.get('provider') or {}).get('id')
    assert provider_id, provider_resp
    status, caps = api('/api/build-capabilities')
    budget = caps.get('generationBudget') or {}
    check('capabilities 启用后（未绑定 provider 前）提示 REQUIRED 且 enabled=true',
          budget.get('enabled') is True
          and any(e.get('code') == 'BUDGET_PROFILE_REQUIRED' for e in budget.get('configErrors') or []),
          budget, 'required hint')

    # 3) 绑定不符：422 BUDGET_PROFILE_MISMATCH 且零状态变更
    os.environ['WIZ_BUILD_PROFILE_PROVIDER_ID'] = 'other-provider'
    status, created = api('/api/build-task-create', {'name': '预算HTTP任务'})
    task_id = created['task']['id']
    make_material(task_id)
    revision = setup_scope(task_id)
    status, before_detail = api('/api/build-task', query='?taskId=' + task_id)
    status, rejected = api('/api/build-scope-confirm', {'taskId': task_id, 'revision': revision})
    check('profile 绑定不符 → 422 BUDGET_PROFILE_MISMATCH',
          status == 422 and rejected.get('code') == 'BUDGET_PROFILE_MISMATCH',
          (status, rejected), 422)
    status, after = api('/api/build-task', query='?taskId=' + task_id)
    check('422 拒绝后零状态变更（任务状态/范围确认/运行均未变）',
          after.get('task', {}).get('status') == before_detail.get('task', {}).get('status')
          and (after.get('scope') or {}).get('confirmed') is False
          and (after.get('run') or {}).get('id') == (before_detail.get('run') or {}).get('id'),
          {'before': before_detail.get('task', {}).get('status'),
           'after': after.get('task', {}).get('status'),
           'confirmed': (after.get('scope') or {}).get('confirmed')},
          'zero change')

    # 4) 绑定正确 → 全链路 v2 生成
    os.environ['WIZ_BUILD_PROFILE_PROVIDER_ID'] = provider_id
    status, confirmed = api('/api/build-scope-confirm', {'taskId': task_id, 'revision': revision})
    check('scope-confirm 200 且返回 runId/batchId',
          status == 200 and confirmed.get('runId') and confirmed.get('batchId'),
          (status, confirmed), 200)
    run = poll_run(task_id, confirmed['runId'])
    check('v2 生成成功', run.get('state') == 'succeeded', run.get('state'), 'succeeded')
    gen = ((run.get('checkpoint') or {}).get('generate') or {})
    check('轮询摘要是 schema2（jobs/coverage/budget 齐备）',
          gen.get('schemaVersion') == 2 and 'jobs' in gen and 'coverage' in gen
          and 'budget' in gen and 'adaptive' in gen,
          gen, 'schema2 view')
    check('覆盖守恒：已处理目标 == 总目标且无 pending',
          gen.get('coverage', {}).get('targetTotal', 0) > 0
          and gen.get('coverage', {}).get('targetCompleted')
          == gen.get('coverage', {}).get('targetTotal')
          and gen.get('coverage', {}).get('targetPending') == 0,
          gen.get('coverage'), 'complete coverage')
    usage = run.get('usage') or {}
    check('usage 带可空 token 统计与 unknown 计数（本轮全部已知，planEpoch 为计划代次）',
          usage.get('promptTokens') == 210 and usage.get('knownCompletionTokens') == 90
          and usage.get('unknownUsageCalls') == 0 and usage.get('planEpoch') is not None,
          usage, 'token stats')
    status, cands = api('/api/build-candidates', query='?taskId=%s&batch=%s'
                        % (task_id, confirmed['batchId']))
    check('候选已落库且评审可见（schema2 与 legacy 同一批语义）',
          status == 200 and (cands.get('total') or 0) >= 1,
          (status, cands.get('total')), 'candidates>=1')

    # 5) resume 守卫：直接改库注入未知版本与 blocking 两种 checkpoint
    with sto.write_tx() as tx:
        def _inject(conn):
            run_row = ob_store.get_run(conn, confirmed['runId'], USER_ID)
            assert run_row is not None
            doc = json.loads(run_row['checkpoint_json'] or '{}')
            gen_doc = doc.get('generate') or {}
            gen_doc['schemaVersion'] = 99
            gen_doc['pendingTargetIds'] = []
            doc['generate'] = gen_doc
            ob_store.update_run(conn, confirmed['runId'], USER_ID,
                                state='failed', error='注入失败', retryable=True,
                                checkpoint=doc)
        tx.run(_inject)
    status, rejected = api('/api/build-run-resume', {'taskId': task_id, 'runId': confirmed['runId'],
                                                     'resumeMode': 'auto'})
    check('未知 schemaVersion → 422 UNKNOWN_CHECKPOINT_SCHEMA（不删候选）',
          status == 422 and rejected.get('code') == 'UNKNOWN_CHECKPOINT_SCHEMA',
          (status, rejected), 422)
    status, cands = api('/api/build-candidates', query='?taskId=%s&batch=%s'
                        % (task_id, confirmed['batchId']))
    check('拒绝恢复后候选仍保留', status == 200 and (cands.get('total') or 0) >= 1,
          (status, cands.get('total')), 'candidates kept')

    # 还原 schema2 并注入 blocking → resume 422 需新建计划
    with sto.write_tx() as tx:
        def _inject2(conn):
            _raw = ob_store.get_run(conn, confirmed['runId'], USER_ID)
            print('[诊断] run state=%r checkpoint 前120字符=%r' % (
                _raw['state'] if _raw else None,
                ( (_raw['checkpoint_json'] or '')[:120] if _raw else 'ROW-NONE')))
            doc = json.loads((_raw['checkpoint_json'] if _raw else '{}') or '{}')
            doc['generate']['schemaVersion'] = 2
            doc['generate']['blocking'] = {'code': 'OVERSIZED_ATOMIC_TARGET',
                                           'message': '存在不可拆分超长目标'}
            doc['generate']['pendingTargetIds'] = ['t-x']
            ob_store.update_run(conn, confirmed['runId'], USER_ID,
                                state='failed', error='注入受阻', retryable=True,
                                checkpoint=doc)
        tx.run(_inject2)
    status, rejected = api('/api/build-run-resume', {'taskId': task_id, 'runId': confirmed['runId'],
                                                     'resumeMode': 'auto'})
    check('blocked 计划 → 422 且提示需新建计划（不把重试当万能入口）',
          status == 422 and '新建' in json.dumps(rejected, ensure_ascii=False),
          (status, rejected), 422)

    # 5.1) P2-3 修复回归：损坏的非整数 schemaVersion（'x'/'2.5'）必须按「未知版本」安全拒绝
    #      → 422 UNKNOWN_CHECKPOINT_SCHEMA，绝不裸抛 ValueError（HTTP 500）；候选保留、
    #      run 状态不变。合法 schema2 与未知整数 99 的既有行为不得回归。
    def _inject_checkpoint(run_id, version, blocking=None, pending=None):
        """覆写 checkpoint.generate.schemaVersion 并置 run=failed（其余计划字段不动）。"""
        with sto.write_tx() as tx:
            def body(conn):
                row = ob_store.get_run(conn, run_id, USER_ID)
                assert row is not None
                doc = json.loads(row['checkpoint_json'] or '{}')
                gen_doc = doc.get('generate') or {}
                gen_doc['schemaVersion'] = version
                if blocking is None:
                    gen_doc.pop('blocking', None)
                else:
                    gen_doc['blocking'] = blocking
                gen_doc['pendingTargetIds'] = list(pending or [])
                doc['generate'] = gen_doc
                ob_store.update_run(conn, run_id, USER_ID, state='failed',
                                    error='注入 schemaVersion=%r' % (version,),
                                    retryable=True, checkpoint=doc)
            tx.run(body)

    status, cands_before = api('/api/build-candidates', query='?taskId=%s&batch=%s'
                               % (task_id, confirmed['batchId']))
    count_before = cands_before.get('total') or 0
    check('注入前候选基线可见（>=1）', status == 200 and count_before >= 1,
          (status, count_before), '>=1')

    _inject_checkpoint(confirmed['runId'], 'x')
    status, rejected = api('/api/build-run-resume', {'taskId': task_id,
                                                     'runId': confirmed['runId'],
                                                     'resumeMode': 'auto'})
    check("损坏版本 'x' → 422 UNKNOWN_CHECKPOINT_SCHEMA（不是 500）",
          status == 422 and rejected.get('code') == 'UNKNOWN_CHECKPOINT_SCHEMA',
          (status, rejected), 422)
    status, cands_after = api('/api/build-candidates', query='?taskId=%s&batch=%s'
                              % (task_id, confirmed['batchId']))
    check("'x' 拒绝后候选仍保留（数量与注入前一致）",
          status == 200 and (cands_after.get('total') or 0) == count_before,
          (status, cands_after.get('total')), count_before)
    # 直接读库核对 run 状态（不经 GET /api/build-run：该轮询视图自身对损坏版本仍会裸抛
    # int()，属另一处问题、不在本次 resume 预检修复范围内，避免测试耦合到它）。
    with sto.read_connection() as conn:
        _run_row = ob_store.get_run(conn, confirmed['runId'], USER_ID)
    check("'x' 拒绝后 run 仍为 failed（状态未变、未入队）",
          _run_row is not None and str(_run_row['state']) == 'failed',
          None if _run_row is None else _run_row['state'], 'failed')

    _inject_checkpoint(confirmed['runId'], '2.5')
    status, rejected = api('/api/build-run-resume', {'taskId': task_id,
                                                     'runId': confirmed['runId'],
                                                     'resumeMode': 'auto'})
    check("损坏版本 '2.5' → 422 UNKNOWN_CHECKPOINT_SCHEMA（不是 500）",
          status == 422 and rejected.get('code') == 'UNKNOWN_CHECKPOINT_SCHEMA',
          (status, rejected), 422)

    _inject_checkpoint(confirmed['runId'], 99)
    status, rejected = api('/api/build-run-resume', {'taskId': task_id,
                                                     'runId': confirmed['runId'],
                                                     'resumeMode': 'auto'})
    check('未知整数版本 99 → 仍 422 UNKNOWN_CHECKPOINT_SCHEMA（既有行为不回归）',
          status == 422 and rejected.get('code') == 'UNKNOWN_CHECKPOINT_SCHEMA',
          (status, rejected), 422)

    # 合法 schema2 计划（无 blocking、无 pending）→ resume 照常受理 200（既有行为不回归）
    _inject_checkpoint(confirmed['runId'], 2)
    status, accepted = api('/api/build-run-resume', {'taskId': task_id,
                                                     'runId': confirmed['runId'],
                                                     'resumeMode': 'auto'})
    check('schemaVersion=2 干净计划 → 200 受理（既有行为不回归）',
          status == 200 and accepted.get('runId') == confirmed['runId'],
          (status, accepted), 200)
    poll_run(task_id, confirmed['runId'])   # 等终态，避免与后续 regenerate 并发

    # 6) abstract 重抽象：新 epoch 成功
    # 恢复一个干净的成功 checkpoint（直接再跑一次 scope-confirm 生成新 batch 更简单——
    # 这里用 regenerate 触发全新计划）
    status, regen = api('/api/build-regenerate', {'taskId': task_id})
    check('regenerate（v2 开启）200 且新运行成功',
          status == 200 and poll_run(task_id, regen['runId']).get('state') == 'succeeded',
          (status, regen), 200)
    run2 = poll_run(task_id, regen['runId'])
    gen2 = ((run2.get('checkpoint') or {}).get('generate') or {})
    check('新运行仍是 schema2 且 coverage 完整',
          gen2.get('schemaVersion') == 2
          and gen2.get('coverage', {}).get('targetCompleted')
          == gen2.get('coverage', {}).get('targetTotal'),
          gen2.get('coverage'), 'complete')

    # 7) legacy resume 兼容回归（本次修复的回归点）：schema1 checkpoint（无 schemaVersion
    #    或 =1）在自适应开启时也必须照常受理——不得被 v2 预检误拦。
    os.environ.pop('WIZ_BUILD_ADAPTIVE_BATCHING', None)   # 关掉 v2，走纯 legacy 生成
    status, created2 = api('/api/build-task-create', {'name': 'legacy续跑任务'})
    task2 = created2['task']['id']
    make_material(task2)
    rev2 = setup_scope(task2)
    status, conf2 = api('/api/build-scope-confirm', {'taskId': task2, 'revision': rev2})
    check('legacy 生成受理（自适应关闭）', status == 200, (status, conf2), 200)
    run_l = poll_run(task2, conf2['runId'])
    check('legacy 生成完成', run_l.get('state') == 'succeeded', run_l.get('state'), 'succeeded')
    gen_l = ((run_l.get('checkpoint') or {}).get('generate') or {})
    check('legacy 摘要保持 schema1 形状（无 schemaVersion/无 jobs 明细）',
          gen_l.get('schemaVersion') is None and 'jobs' not in gen_l and 'batches' in gen_l,
          list(gen_l.keys()), 'schema1')
    # 人工置 failed 后 resume(auto)：schema1 缺省版本必须被受理（回归：曾被误判 UNKNOWN）
    with sto.write_tx() as tx:
        def _fail_legacy(conn):
            ob_store.update_run(conn, conf2['runId'], USER_ID, state='failed',
                                error='注入失败', retryable=True)
        tx.run(_fail_legacy)
    status, resumed_l = api('/api/build-run-resume',
                            {'taskId': task2, 'runId': conf2['runId'], 'resumeMode': 'auto'})
    check('legacy resume 被受理（缺省版本不得误判 UNKNOWN_CHECKPOINT_SCHEMA）',
          status == 200, (status, resumed_l), 200)
    check('legacy resume 执行完成', poll_run(task2, conf2['runId']).get('state') == 'succeeded',
          poll_run(task2, conf2['runId']).get('state'), 'succeeded')
    # 追加（P2-3）：空串 schemaVersion 与「缺失」同义 → 仍走 legacy 受理（安全解析不得
    # 把 '' 当成未知版本；'x'/'2.5' 等才按未知版本 422）。
    with sto.write_tx() as tx:
        def _blank_version(conn):
            row = ob_store.get_run(conn, conf2['runId'], USER_ID)
            doc = json.loads(row['checkpoint_json'] or '{}')
            doc['generate']['schemaVersion'] = ''
            ob_store.update_run(conn, conf2['runId'], USER_ID, state='failed',
                                error='注入空串版本', retryable=True, checkpoint=doc)
        tx.run(_blank_version)
    status, resumed_blank = api('/api/build-run-resume',
                                {'taskId': task2, 'runId': conf2['runId'],
                                 'resumeMode': 'auto'})
    check("schemaVersion=''（空串=缺省）→ 仍走 legacy 受理 200",
          status == 200, (status, resumed_blank), 200)
    poll_run(task2, conf2['runId'])

    # 追加（P2-3 同源修复）：损坏 schemaVersion 的轮询视图不得裸抛（原按 schema1 原样返回）
    with sto.write_tx() as tx:
        def _broken_poll(conn):
            row = ob_store.get_run(conn, conf2['runId'], USER_ID)
            doc = json.loads(row['checkpoint_json'] or '{}')
            doc['generate']['schemaVersion'] = 'x'
            ob_store.update_run(conn, conf2['runId'], USER_ID, checkpoint=doc)
        tx.run(_broken_poll)
    status, view_broken = api('/api/build-run', query='?taskId=%s&runId=%s'
                              % (task2, conf2['runId']))
    check('损坏 schemaVersion 的轮询视图不裸抛（200 原样返回）',
          status == 200, (status, str(view_broken)[:160]), 200)
    status, detail_broken = api('/api/build-task', query='?taskId=' + task2)
    check('损坏 schemaVersion 的任务详情同样不裸抛', status == 200,
          (status, str(detail_broken)[:160]), 200)

    print('========== 汇总 ==========')
    print('通过 %d / %d' % (len(PASSED), len(PASSED) + len(FAILED)))
    for name in FAILED:
        print('  失败: ' + name)
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
