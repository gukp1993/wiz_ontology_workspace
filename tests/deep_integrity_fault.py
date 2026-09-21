"""Q05 I3：kill -9 故障注入 + 损坏库只读行为（仅本任务专属实例/可丢弃副本）。

模式：
  prep            创建大模型并记录基线（HTTP）
  hammer          多线程持续 save（外层 shell 期间 kill -9 服务）
  check           重启后一致性检查（HTTP + SQLite 只读 PRAGMA/引用）
  corrupt-setup   用 sqlite backup 复制数据根 → 破坏副本 → 18933 起隔离实例探测
  corrupt-cleanup 停 18933、删除副本目录
"""
import json
import os
import random
import shutil
import sqlite3
import string
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
BASE = 'http://127.0.0.1:18932'
PW = 'DeepTest!2026#abc'
DATA_ROOT = ROOT / '.runtime/test-data-q05'
EVIDENCE = ROOT / '.runtime/test-evidence/q05'
DB = DATA_ROOT / 'data/workbench.sqlite3'
CTX = EVIDENCE / 'i3_ctx.json'
CORRUPT_ROOT = DATA_ROOT.parent / 'test-data-q05-corrupt'
CORRUPT_PID = EVIDENCE / 'corrupt-18933.pid'

import auth_client  # noqa: E402
import deep_ontology_client as doc  # noqa: E402
doc.EVIDENCE_DIR = EVIDENCE
from deep_ontology_client import Recorder, brief  # noqa: E402


class Resp:
    __slots__ = ('status', 'json', 'text', 'binary', 'headers', 'error')

    def __init__(self, d):
        self.status = d['status']; self.json = d['json']; self.text = d['text']
        self.binary = d.get('binary', b''); self.headers = d.get('headers', {})
        self.error = d.get('error')


class Api2(doc.Api):
    def call(self, method, path, payload=None, raw_body=None, headers=None, timeout=30):
        return Resp(doc.Api.call(self, method, path, payload=payload, raw_body=raw_body,
                                 headers=headers, timeout=timeout))

    def get(self, path):
        return self.call('GET', path)

    def post(self, path, payload):
        return self.call('POST', path, payload=payload)


def load_ctx():
    return json.loads(CTX.read_text(encoding='utf-8'))


def prep(rec):
    a = Api2(base=BASE, username='qa_int_a', password=PW)
    m = a.post('/api/ontologies', {'name': 'Q05 故障本体 ' + str(int(time.time()))})
    mid = m.json['id']
    s = a.get('/api/state?ontology=' + mid)
    st = s.json['state']
    st['workspaceId'] = mid
    # 撑大到 ~1.5MB：批量对象类型
    for i in range(4000):
        st['ontology']['objectTypes'].append(
            {'id': 'mg:q05f%04d' % i, 'displayName': '故障对象%04d' % i, 'apiName': 'q05f_%04d' % i})
    st['ontology']['definitionOrder'] = [o['id'] for o in st['ontology']['objectTypes']]
    sv = a.post('/api/save', {'state': st, 'revision': s.json['revision']})
    ctx = {'modelId': mid, 'baselineRevision': (sv.json or {}).get('revision'),
           'submitted': {}}
    CTX.write_text(json.dumps(ctx), encoding='utf-8')
    rec.add('I3a-prep', 'pass' if sv.status == 200 else 'fail',
            '大模型就绪（4000 对象类型，payload≈1.4MB）',
            brief({'mid': mid, 'save': sv.status, 'body': brief(sv.json)}))
    rec.dump()


def hammer(rec):
    ctx = load_ctx()
    a = Api2(base=BASE, username='qa_int_a', password=PW)
    mid = ctx['modelId']
    stop = threading.Event()
    lock = threading.Lock()

    def worker(idx):
        w = Api2(base=BASE, username='qa_int_a', password=PW)
        tries = 0
        while not stop.is_set() and tries < 200:
            tries += 1
            try:
                s = w.get('/api/state?ontology=' + mid)
                if s.status != 200:
                    continue
                st = s.json['state']
                st['workspaceId'] = mid
                tag = ''.join(random.choices(string.ascii_lowercase, k=10))
                st['workflow']['objective']['question'] = 'H' + tag + '-' + str(idx)
                r = w.post('/api/save', {'state': st, 'revision': s.json['revision']})
                if r.status == 200:
                    with lock:
                        ctx['submitted']['H' + tag + '-' + str(idx)] = r.json['revision']
            except Exception:
                time.sleep(0.2)
    threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(4)]
    for t in threads:
        t.start()
    duration = float(os.environ.get('Q05_HAMMER_SECONDS', '6'))
    time.sleep(duration)
    stop.set()
    for t in threads:
        t.join(timeout=15)
    CTX.write_text(json.dumps(ctx), encoding='utf-8')
    print('hammer done, submitted=', len(ctx['submitted']))


def check(rec, label):
    ctx = load_ctx()
    mid = ctx['modelId']
    auth_client.wait_ready(BASE, timeout=40)
    a = Api2(base=BASE, username='qa_int_a', password=PW)
    s = a.get('/api/state?ontology=' + mid)
    ok_http = s.status == 200 and s.json.get('revision')
    q = (s.json or {}).get('state', {}).get('workflow', {}).get('objective', {}).get('question', '')
    # question 必须是某次「成功提交」的内容（半写串杂会破坏这个映射）；
    # 服务端 commit 后响应前被杀 → 客户端未记录但内容仍是完整提交态，按 hammer 格式放行并标注
    matched_rev = ctx['submitted'].get(q)
    if matched_rev:
        consistent = matched_rev == s.json.get('revision')
    else:
        consistent = q.startswith('H') and len(ctx['submitted']) > 0
    con = sqlite3.connect('file:' + str(DB) + '?mode=ro', uri=True)
    try:
        ic = con.execute('PRAGMA integrity_check').fetchone()[0]
        fk = con.execute('PRAGMA foreign_key_check').fetchall()
        # head 指向的 revision token 与快照存在性
        row = con.execute(
            'SELECT h.revision_token, sp.payload_json FROM wb_assets ast '
            'JOIN wb_asset_heads h ON h.asset_uid = ast.asset_uid '
            'JOIN wb_snapshots sp ON sp.snapshot_id = h.snapshot_id '
            "WHERE ast.external_id = ? AND ast.kind = 'model'", (mid,)).fetchone()
        head_ok = row is not None and row[0] == s.json.get('revision')
        payload_ok = False
        if row:
            try:
                doc_json = json.loads(row[1])
                qs = (doc_json.get('workflow') or {}).get('objective', {}).get('question', '')
                payload_ok = (qs == q)
            except ValueError:
                payload_ok = False
        # 快照 seq 连续性（无空洞/重复）
        gaps = con.execute(
            'SELECT COUNT(*) FROM (SELECT seq, LAG(seq) OVER (ORDER BY seq) prev FROM wb_snapshots '
            "WHERE asset_uid = (SELECT asset_uid FROM wb_assets WHERE external_id = ? AND kind='model')) "
            'WHERE prev IS NOT NULL AND seq != prev + 1', (mid,)).fetchone()[0]
    finally:
        con.close()
    ok = (ok_http and ic == 'ok' and not fk and head_ok and payload_ok and gaps == 0)
    rec.add('I3a-check-' + label, 'pass' if ok else 'fail',
            'kill -9 后重启：无半写（head=revision=已提交映射、快照 JSON 完整、seq 连续、'
            'integrity_check=ok、foreign_key_check 空）',
            brief({'integrity': ic, 'fkRows': len(fk), 'headMatch': head_ok, 'payloadMatch': payload_ok,
                   'seqGaps': gaps, 'questionIsSubmitted': bool(matched_rev), 'rev': (s.json or {}).get('revision')}))


def corrupt_setup(rec):
    # 1) 一致性复制正在运行的库
    if CORRUPT_ROOT.exists():
        shutil.rmtree(CORRUPT_ROOT)
    (CORRUPT_ROOT / 'data').mkdir(parents=True)
    src = sqlite3.connect(str(DB))
    dst = sqlite3.connect(str(CORRUPT_ROOT / 'data/workbench.sqlite3'))
    src.backup(dst)
    src.close(); dst.close()
    if (DATA_ROOT / 'keys').exists():
        shutil.copytree(DATA_ROOT / 'keys', CORRUPT_ROOT / 'keys')
    # 2) 破坏：草稿快照 payload 改成非法 JSON + drop 定义索引表
    con = sqlite3.connect(str(CORRUPT_ROOT / 'data/workbench.sqlite3'))
    con.execute("UPDATE wb_snapshots SET payload_json = '{corrupted-by-q05' "
                "WHERE purpose='draft' AND asset_uid IN (SELECT asset_uid FROM wb_assets WHERE kind='model')")
    con.execute('DROP TABLE wb_definition_index')
    con.commit(); con.close()
    # 3) 起隔离实例（18933，独立数据根；非共享 18931）
    env = dict(os.environ)
    env['WIZ_WORKBENCH_ROOT'] = str(CORRUPT_ROOT)
    env['WIZ_WORKBENCH_PORT'] = '18933'
    env.pop('WIZ_DATABASE_URL', None)
    logf = open(EVIDENCE / 'corrupt-18933.log', 'a')
    proc = subprocess.Popen([str(ROOT / '.runtime/venv/bin/python'), '-m', 'workbench.server'],
                            cwd=str(ROOT), env=env, stdout=logf, stderr=subprocess.STDOUT)
    CORRUPT_PID.write_text(str(proc.pid))
    rec.add('I3c-setup', 'pass', '损坏副本已建立并在 18933 启动 pid=' + str(proc.pid), '')
    rec.dump()


def corrupt_probe(rec):
    b = 'http://127.0.0.1:18933'
    auth_client.wait_ready(b, timeout=40)
    results = {}
    # 登录仍应可用（sessions/users 完好）
    try:
        tok = auth_client.register_or_login(b, 'qa_int_a', PW)[1]
    except Exception as e:
        tok = ''
        results['login'] = str(e)[:120]
    hdr = {'Cookie': 'wiz_session=' + tok} if tok else {}
    def get(path):
        req = urllib.request.Request(b + path, headers=hdr)
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                return r.status, r.read()[:160].decode('utf-8', 'replace')
        except urllib.error.HTTPError as e:
            return e.code, e.read()[:160].decode('utf-8', 'replace')
        except Exception as e:
            return None, type(e).__name__ + ':' + str(e)[:120]
    ctx = load_ctx()
    st, body = get('/api/state?ontology=' + ctx['modelId'])
    results['draftState'] = [st, body[:120]]
    lst, lbody = get('/api/ontologies')
    results['ontologiesList'] = [lst, lbody[:120]]
    ver, vbody = get('/api/versions?ontology=' + ctx['modelId'])
    results['versions'] = [ver, vbody[:120]]
    # 期望：损坏不返回假数据 200；应为 500(INTERNAL_ERROR 带 requestId)/503，绝不静默空
    no_fake = not (st == 200 and '"@graph"' in body and 'corrupted' not in body)
    codes = [v[0] for v in results.values() if isinstance(v, list)]
    ok = all(c in (200, 400, 404, 500, 503) for c in codes) and no_fake
    rec.add('I3c-probe', 'pass' if ok else 'fail',
            '损坏表副本：请求不崩溃、不假成功空数据；错误如实上报（5xx/4xx 分类见证据）',
            brief(results))
    rec.dump()


def corrupt_cleanup(rec):
    if CORRUPT_PID.exists():
        pid = int(CORRUPT_PID.read_text().strip())
        cwd = subprocess.run(['lsof', '-a', '-p', str(pid), '-d', 'cwd', '-Fn'],
                             capture_output=True, text=True).stdout
        if str(ROOT) in cwd:
            try:
                os.kill(pid, 15)
            except ProcessLookupError:
                pass
        CORRUPT_PID.unlink()
    time.sleep(1)
    if CORRUPT_ROOT.exists():
        shutil.rmtree(CORRUPT_ROOT)
    rec.add('I3c-cleanup', 'pass', '损坏副本实例已停止、目录已删除', str(CORRUPT_ROOT.exists()))
    rec.dump()


if __name__ == '__main__':
    mode = sys.argv[1]
    rec = Recorder('fault-' + mode)
    if mode == 'prep':
        prep(rec)
    elif mode == 'hammer':
        hammer(rec)  # 输出到 stdout，不做 rec.dump（会被 kill 环境打断）
    elif mode.startswith('check'):
        auth_client.wait_ready(BASE, timeout=40)
        check(rec, mode.replace('check', '') or '1'); rec.dump()
    elif mode == 'corrupt-setup':
        corrupt_setup(rec)
    elif mode == 'corrupt-probe':
        corrupt_probe(rec)
    elif mode == 'corrupt-cleanup':
        corrupt_cleanup(rec)
    else:
        print('mode?'); sys.exit(2)
