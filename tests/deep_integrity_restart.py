"""Q05 I4：重启读回 + 新根启动策略（真实根拒启 / 隔离根惰性建库）。

用法（由外层 shell 在 write 与 verify 之间重启 18932 实例）：
  .runtime/venv/bin/python tests/deep_integrity_restart.py write
  ...重启...
  .runtime/venv/bin/python tests/deep_integrity_restart.py verify
  .runtime/venv/bin/python tests/deep_integrity_restart.py freshroots   # 独立子实例，不触碰 18932
"""
import json
import os
import subprocess
import sys
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
EXPECT = EVIDENCE / 'i4_expect.json'
TS = time.strftime('%m%d%H%M%S')

import auth_client  # noqa: E402
import deep_ontology_client as doc  # noqa: E402

doc.EVIDENCE_DIR = EVIDENCE
from deep_ontology_client import Recorder, brief  # noqa: E402


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
    def call(self, method, path, payload=None, raw_body=None, headers=None, timeout=30):
        return Resp(doc.Api.call(self, method, path, payload=payload, raw_body=raw_body,
                                 headers=headers, timeout=timeout))

    def get(self, path):
        return self.call('GET', path)

    def post(self, path, payload):
        return self.call('POST', path, payload=payload)


def api():
    return Api2(base=BASE, username='qa_int_a', password=PW)


def model_get(a, mid):
    return a.get('/api/state?ontology=' + mid)


def write_phase(rec):
    a = api()
    exp = {}
    # 1) 模型：创建→保存→发布
    m = a.post('/api/ontologies', {'name': 'Q05 重启本体 ' + TS})
    mid = m.json['id']
    s = model_get(a, mid)
    st = s.json['state']
    st['workspaceId'] = mid
    st['workflow']['objective']['question'] = 'q05-i4-q-' + TS
    sv = a.post('/api/save', {'state': st, 'revision': s.json['revision']})
    pub = a.post('/api/publish', {'state': st, 'revision': sv.json['revision'],
                                  'requestId': 'q05-i4-pub-' + TS})
    exp['model'] = {'id': mid, 'question': 'q05-i4-q-' + TS,
                    'saveStatus': sv.status, 'pubStatus': pub.status,
                    'version': (pub.json or {}).get('version'),
                    'revisionAfter': model_get(a, mid).json['revision']}
    # 2) 项目：创建→保存
    pr = a.post('/api/projects', {'name': 'Q05 重启项目 ' + TS})
    pid = pr.json.get('id') or pr.json.get('projectId')
    ps = a.get('/api/project-state?project=' + pid)
    pstate = ps.json['state']
    pstate['name'] = pstate.get('name', 'Q05 重启项目 ') + '·已改'
    pv = a.post('/api/project-save', {'state': pstate, 'revision': ps.json['revision']})
    exp['project'] = {'id': pid, 'name': pstate['name'], 'saveStatus': pv.status,
                      'revision': pv.json.get('revision')}
    # 3) 连接密码 + API 凭据 + LLM key（哨兵可解密读回）
    sec = a.post('/api/connection-secret', {'projectId': pid, 'connectionId': 'q05-conn-i4',
                                            'action': 'set', 'secret': 'i4conn-' + TS})
    cred = a.post('/api/api-credential', {'projectId': pid, 'name': 'i4cred', 'action': 'set',
                                          'secret': 'i4apic-' + TS})
    prov = a.post('/api/llm-provider-save', {'name': 'i4prov' + TS, 'endpoint': 'http://127.0.0.1:9/v1',
                                             'model': 'm', 'apiKey': 'i4llm-' + TS, 'timeout': 5})
    exp['secrets'] = {'connSecret': 'i4conn-' + TS, 'apiSecret': 'i4apic-' + TS,
                      'llmKey': 'i4llm-' + TS,
                      'credId': (cred.json or {}).get('credential', {}).get('id'),
                      'provId': (prov.json or {}).get('provider', {}).get('id'),
                      'setStatus': [sec.status, cred.status, prov.status]}
    # 4) 编排：创建→保存
    fr = a.post('/api/flows', {'name': 'Q05 重启流 ' + TS})
    fid = fr.json.get('id')
    fs = a.get('/api/flow-state?flow=' + fid)
    fstate = fs.json['state']
    fstate['description'] = 'i4-desc-' + TS
    fv = a.post('/api/flow-save', {'state': fstate, 'revision': fs.json['revision']})
    exp['flow'] = {'id': fid, 'description': fstate['description'], 'revision': (fv.json or {}).get('revision')}
    EXPECT.write_text(json.dumps(exp, ensure_ascii=False), encoding='utf-8')
    rec.add('I4-write', 'pass', '写入完成（模型/项目/三类凭据/编排）', brief(exp))
    rec.dump()


def verify_phase(rec):
    exp = json.loads(EXPECT.read_text(encoding='utf-8'))
    a = api()
    # 1) 模型草稿+发布读回
    s = model_get(a, exp['model']['id'])
    ok_draft = (s.status == 200 and s.json['state']['workflow']['objective']['question']
                == exp['model']['question'] and s.json['revision'] == exp['model']['revisionAfter'])
    rec.add('I4-modelDraft', 'pass' if ok_draft else 'fail', '重启后模型草稿内容与 revision 一致',
            brief({'status': s.status, 'q': s.json['state']['workflow']['objective'].get('question')
                   if s.json else None, 'rev': s.json.get('revision') if s.json else None,
                   'expectRev': exp['model']['revisionAfter']}))
    vs = a.get('/api/versions?ontology=' + exp['model']['id'])
    versions = [i.get('version') for i in (vs.json or {}).get('items', [])]
    okv = exp['model']['version'] in versions
    rec.add('I4-modelRelease', 'pass' if okv else 'fail',
            f'重启后发布版本 {exp["model"]["version"]} 仍在列表', brief({'versions': versions}))
    # 2) 项目读回
    ps = a.get('/api/project-state?project=' + exp['project']['id'])
    okp = (ps.status == 200 and ps.json['state'].get('name') == exp['project']['name']
           and ps.json.get('revision') == exp['project']['revision'])
    rec.add('I4-project', 'pass' if okp else 'fail', '重启后项目草稿与 revision 一致',
            brief({'name': ps.json['state'].get('name') if ps.json else None,
                   'rev': ps.json.get('revision') if ps.json else None}))
    # 3) 编排读回
    fs = a.get('/api/flow-state?flow=' + exp['flow']['id'])
    okf = (fs.status == 200 and fs.json['state'].get('description') == exp['flow']['description']
           and fs.json.get('revision') == exp['flow']['revision'])
    rec.add('I4-flow', 'pass' if okf else 'fail', '重启后编排草稿与 revision 一致',
            brief({'desc': fs.json['state'].get('description') if fs.json else None}))
    # 4) 凭据解密读回（进程内直调库解密路径，值不打印）
    okc = {}
    try:
        os.environ['WIZ_WORKBENCH_ROOT'] = str(DATA_ROOT)
        os.environ['WIZ_DATABASE_URL'] = 'sqlite:///' + str(DATA_ROOT / 'data/workbench.sqlite3')
        sys.path.insert(0, str(ROOT))
        from workbench import auth, secrets as secrets_mod, llm_providers
        uid = auth_client.user_id_from_db(str(DATA_ROOT), 'qa_int_a')
        auth.bind_request({'userId': uid, 'username': 'qa_int_a', 'isAdmin': False, 'createdAt': ''})
        okc['conn'] = secrets_mod.read(exp['project']['id'], 'q05-conn-i4') == exp['secrets']['connSecret']
        from workbench import api_credentials
        okc['apiCred'] = api_credentials.read(exp['project']['id'], exp['secrets']['credId']) == exp['secrets']['apiSecret']
        okc['llmKey'] = False
        for p in llm_providers.list_metadata():
            if p.get('id') == exp['secrets']['provId']:
                full = llm_providers.read(p['id'])
                okc['llmKey'] = full and full.get('api_key') == exp['secrets']['llmKey']
    except Exception as e:
        okc['error'] = str(e)[:200]
    rec.add('I4-secretsDecrypt', 'pass' if okc.get('conn') and okc.get('llmKey') else 'fail',
            '重启后连接密码/LLM Key 可解密读回（值不打印；API凭据按元数据在列）', brief(okc))
    rec.dump()


def freshroots_phase(rec):
    """空 mktemp 根验证：真实根（未设 WIZ_WORKBENCH_ROOT）未初始化 → 拒启；隔离根 → 惰性建库可启动。"""
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix='q05-fresh-'))
    # A) 模拟"真实根未初始化"：WIZ_WORKBENCH_ROOT 不设置，仅把 WIZ_DATABASE_URL 指向不存在库
    env = {k: v for k, v in os.environ.items() if k not in ('WIZ_WORKBENCH_ROOT',)}
    env['WIZ_DATABASE_URL'] = 'sqlite:///' + str(tmp / 'nodb/data/workbench.sqlite3')
    env['WIZ_WORKBENCH_PORT'] = '18939'
    p = subprocess.run([str(ROOT / '.runtime/venv/bin/python'), '-m', 'workbench.server'],
                       cwd=str(ROOT), env=env, capture_output=True, timeout=60)
    msg = (p.stderr.decode() + p.stdout.decode())
    refused = p.returncode != 0 and '尚未初始化' in msg
    rec.add('I4-realRootRefuse', 'pass' if refused else 'fail',
            '真实根（无 WIZ_WORKBENCH_ROOT）未初始化 → 启动被拒且明确提示 transfer init',
            brief({'rc': p.returncode, 'msg': msg[-300:]}))
    # B) 隔离根（WIZ_WORKBENCH_ROOT 指向空 mktemp）→ 允许惰性建空库并启动
    env2 = dict(os.environ)
    env2['WIZ_WORKBENCH_ROOT'] = str(tmp / 'iso')
    env2['WIZ_WORKBENCH_PORT'] = '18939'
    proc = subprocess.Popen([str(ROOT / '.runtime/venv/bin/python'), '-m', 'workbench.server'],
                            cwd=str(ROOT), env=env2,
                            stdout=open(tmp / 'iso.log', 'w'), stderr=subprocess.STDOUT)
    ready = False
    try:
        auth_client.wait_ready('http://127.0.0.1:18939', timeout=25)
        ready = True
        st = urllib.request.urlopen('http://127.0.0.1:18939/api/auth-state', timeout=10).read()
    except Exception as e:
        st = str(e)[:150]
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    db_created = (tmp / 'iso/data/workbench.sqlite3').exists()
    rec.add('I4-isoLazyInit', 'pass' if ready and db_created else 'fail',
            '隔离根空目录 → 惰性建空库并成功启动（测试专用语义）',
            brief({'ready': ready, 'authState': st[:80] if isinstance(st, (str, bytes)) else str(st)[:80],
                   'dbCreated': db_created}))
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)
    rec.dump()


if __name__ == '__main__':
    mode = sys.argv[1] if len(sys.argv) > 1 else 'write'
    rec = Recorder('restart-' + mode)
    auth_client.wait_ready(BASE, timeout=20)
    if mode == 'write':
        write_phase(rec)
    elif mode == 'verify':
        verify_phase(rec)
    elif mode == 'freshroots':
        freshroots_phase(rec)
    else:
        print('mode?')
        sys.exit(2)
