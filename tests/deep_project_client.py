"""Q03 深度测试公共 HTTP 客户端（只读业务代码，仅测试用）。

约定（复用 tests/deep_ontology_client.py 的 Api 客户端模式，改指向 q03 与 qa_deep_b）：
- 目标服务为已运行的隔离实例 http://127.0.0.1:18931（不启动/不停止/不重启）。
- 所有请求走真实 HTTP 生产入口；本模块只负责加 Cookie、Origin 与解析响应。
- POST 必须带 Origin（server.py 白名单校验）。
- 证据以 JSONL 追加写入 .runtime/test-evidence/q03/。
- 主用账号 qa_deep_b；qa_deep_a 仅跨账号只读探测。
"""
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import auth_client  # noqa: E402

BASE = os.environ.get('Q03_BASE', 'http://127.0.0.1:18931')
USER = os.environ.get('Q03_USER', 'qa_deep_b')
PASSWORD = os.environ.get('Q03_PASSWORD', 'DeepTest!2026#abc')
EVIDENCE_DIR = Path(__file__).resolve().parents[1] / '.runtime/test-evidence/q03'

CTX = {'mg': 'https://example.com/microgrid/', 'owl': 'http://www.w3.org/2002/07/owl#',
       'rdfs': 'http://www.w3.org/2000/01/rdf-schema#', 'xsd': 'http://www.w3.org/2001/XMLSchema#'}


class Api:
    """绑定单个账号会话的最小 HTTP 客户端。"""

    def __init__(self, base=BASE, username=USER, password=PASSWORD, origin_mode='allow'):
        self.base = base
        self.username = username
        self.origin_mode = origin_mode
        headers = auth_client.auth_headers(base, username, password)
        self.cookie = headers['Cookie']

    def _origin(self):
        if self.origin_mode == 'allow':
            return self.base
        if self.origin_mode == 'none':
            return None
        return 'http://evil.example'

    def call(self, method, path, payload=None, raw_body=None, headers=None, timeout=60):
        data = None
        hdrs = {'Cookie': self.cookie}
        origin = self._origin()
        if origin:
            hdrs['Origin'] = origin
        if raw_body is not None:
            data = raw_body
            hdrs['Content-Type'] = 'application/json'
        elif payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode()
            hdrs['Content-Type'] = 'application/json'
        if headers:
            hdrs.update(headers)
        body_bytes = None
        req = urllib.request.Request(self.base + path, data=data, headers=hdrs, method=method)
        status = None
        error = None
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body_bytes = resp.read()
                status = resp.status
                resp_headers = dict(resp.headers)
        except urllib.error.HTTPError as exc:
            body_bytes = exc.read()
            status = exc.code
            resp_headers = dict(exc.headers or {})
            error = 'http_error'
        except Exception as exc:  # noqa: BLE001
            return {'status': None, 'json': None, 'text': '', 'error': type(exc).__name__ + ': ' + str(exc),
                    'headers': {}, 'binary': b''}
        content_type = resp_headers.get('Content-Type', '')
        parsed = None
        text = ''
        if 'application/json' in content_type:
            try:
                parsed = json.loads(body_bytes.decode())
            except ValueError:
                text = body_bytes.decode('utf-8', 'replace')
        else:
            text = body_bytes.decode('utf-8', 'replace') if len(body_bytes) < 4000 else '<binary %d bytes>' % len(body_bytes)
        return {'status': status, 'json': parsed, 'text': text, 'headers': resp_headers,
                'binary': body_bytes, 'error': error}

    def get(self, path):
        return self.call('GET', path)

    def post(self, path, payload):
        return self.call('POST', path, payload=payload)


class Recorder:
    def __init__(self, suite):
        self.suite = suite
        self.results = []
        EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)

    def add(self, case_id, verdict, title, evidence='', kind=''):
        self.results.append({'case': case_id, 'verdict': verdict, 'title': title,
                             'evidence': evidence[:4000], 'kind': kind})
        flag = {'pass': '✓', 'fail': '✗ 缺陷', 'blocked': '⚠ 阻塞', 'untested': '—',
                'known': '✗ 基线已知', 'info': '·'}[verdict]
        print(f'[{flag}] {case_id} {title}')
        if evidence and verdict in ('fail', 'known', 'blocked', 'info'):
            for line in str(evidence).splitlines()[:14]:
                print('      ' + line)

    def dump(self):
        path = EVIDENCE_DIR / (self.suite + '.jsonl')
        with path.open('a', encoding='utf-8') as fh:
            for row in self.results:
                fh.write(json.dumps(row, ensure_ascii=False) + '\n')
        passed = sum(1 for r in self.results if r['verdict'] == 'pass')
        failed = sum(1 for r in self.results if r['verdict'] in ('fail', 'known'))
        print(f'\n== {self.suite}: 记录 {len(self.results)} 条，通过 {passed}，失败/已知 {failed} ==')
        return path


def brief(obj, limit=1500):
    text = json.dumps(obj, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + '…'


# --- 合成本体状态构造（@graph 形态，与前端 JSON-LD 编辑一致） ---------------------

def blank_state(name='Q03合成本体'):
    return {'ontology': {'@context': dict(CTX), '@graph': []},
            'workflow': {'objective': {'name': name, 'question': '', 'scope': '', 'acceptance': ''},
                         'functions': [], 'actions': [], 'interfaces': [],
                         'businessRules': [], 'businessRuleAssociations': [],
                         'actionAssociations': [],
                         'release': {'note': '', 'reviewer': ''}},
            'metrics': {'metrics': []}, 'rules': {'rules': []},
            'layout': {'positions': {}, 'zoom': 1, 'pan': {'x': 0, 'y': 0}}}


def as_schema(state):
    """把 @graph 形态转成 schema 形态（与后端 encode_state 同一实现，口径一致）。"""
    from workbench.model_format import encode_state
    return encode_state(state)
