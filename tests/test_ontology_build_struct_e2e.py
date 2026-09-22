"""结构化格式解析端到端回归（真实隔离 HTTP 实例 + 合成物料，2026-09-22）。

契约：`文档/需求/20260920_从物料自动构建本体/需求说明_结构化格式解析支持_v1.md`（v1.1）§2 / §6。
覆盖链路：建任务 → 分片上传 9 份结构化样本（json / jsonld / jsonId / yaml / csv / properties /
ini / toml / jsonl）→ 触发扫描 → 逐份断言 `parseState=success`、`coverage.factCount>0`、
`coverage.notes` 不含「文本线索降级」类降级标记（新解析器生效的证明）、至少一条事实带结构化定位
（键路径 / 节点 @id / 真实行号）→ 坏 JSON 显式失败路径（不得静默成功）→ 既有 markdown 格式回归
不退化。

隔离（AGENTS.md 测试隔离铁律）：
* `WIZ_WORKBENCH_ROOT` / `WIZ_DATABASE_URL` 都指向本次运行新建的临时目录，服务端口取动态空闲
  端口（绝不使用 18765 / 18881 等其他实例端口）；断言 `workbench.paths.DATA_ROOT` 与
  `storage.engine.resolve_url()` 都落在临时根内，不碰真实 `ontology/`。
* 物料为运行时生成的合成样本（含中文值，验证编码链路），不读不写任何真实业务数据。
* 不调用真实模型：本测试不配置 LLM 提供方，结构化后缀必须走专用解析层而非 LLM 兜底 / 文本线索。
* 结束清理临时根与工作台 HTTP 服务。

运行：python3 tests/test_ontology_build_struct_e2e.py
"""
import base64
import hashlib
import json
import os
import re
import shutil
import socket
import sqlite3
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / 'tests'))

TMP = Path(tempfile.mkdtemp(prefix='wiz_build_struct_e2e_'))
os.environ['WIZ_WORKBENCH_ROOT'] = str(TMP)
os.environ['WIZ_DATABASE_URL'] = 'sqlite:///' + str(TMP / 'data' / 'workbench.sqlite3')

import auth_client  # noqa: E402
from workbench import storage  # noqa: E402
from workbench.ontology_build import protocol  # noqa: E402
from workbench.ontology_build.parsers import base as parsers_base  # noqa: E402

DB_PATH = TMP / 'data' / 'workbench.sqlite3'
FIXTURE_DIR = TMP / 'materials'
TERMINAL_RUN_STATES = ('succeeded', 'failed', 'cancelled', 'interrupted')
# 其他实例占用的固定端口：本测试必须走动态端口，不得抢占
RESERVED_PORTS = (18765, 18881)
# 「文本线索降级」类标记：出现在 coverage.notes 里即说明该后缀没走专用解析层
DEGRADE_MARKERS = ('文本线索降级', '仅文本线索', '无结构解析', '覆盖不足', '未适配专项解析器')
STRUCTURED_KINDS = ('json', 'yaml', 'properties', 'csv', 'ini', 'toml')

PASSED = []
FAILED = []
SEQ = [0]


class Abort(Exception):
    """基础设施级失败（服务未起、接线未落盘、关键响应缺字段）：立即结束并保留诊断。"""


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


# --- 服务生命周期（动态端口 + 临时根，绝不抢占固定端口） ------------------------------

def free_port():
    """动态空闲端口：绑定 0 取端口后立即关闭。"""
    sock = socket.socket()
    try:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]
    finally:
        sock.close()


storage.ensure_ready()
from workbench import server as wb_server  # noqa: E402


class QuietHandler(wb_server.Handler):
    """不打印访问日志：本测试断言多，只保留用例输出（与既有测试静音假 LLM 同一做法）。"""

    def log_message(self, *args):
        pass


PORT = free_port()
BASE = 'http://127.0.0.1:%d' % PORT
HTTPD = ThreadingHTTPServer(('127.0.0.1', PORT), QuietHandler)
threading.Thread(target=HTTPD.serve_forever, daemon=True).start()


def shutdown():
    try:
        HTTPD.shutdown()
        HTTPD.server_close()
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


# --- 库内事实读取（解析结果落库后按材料回读定位器） ---------------------------------

def _loads(raw, fallback):
    try:
        value = json.loads(raw or '')
    except (TypeError, ValueError):
        return fallback
    return value if isinstance(value, type(fallback)) else fallback


def db_rows(sql, params=(), attempts=20):
    """只读查询临时库；偶发占用时短暂重试（服务在同进程另有写线程）。"""
    last = None
    for _ in range(attempts):
        con = None
        try:
            con = sqlite3.connect(str(DB_PATH), timeout=10)
            con.row_factory = sqlite3.Row
            return con.execute(sql, params).fetchall()
        except sqlite3.OperationalError as exc:   # 数据库忙：稍等重试
            last = exc
            time.sleep(0.25)
        finally:
            if con is not None:
                con.close()
    raise Abort('读取临时库失败：%s' % last)


def facts_of(task_id, rel_path):
    """回读某材料的全部事实（含 locator/data 解析结果）。"""
    rows = db_rows(
        'SELECT f.fact_id AS fid, f.kind AS kd, f.module AS md, f.locator_json AS lc, '
        'f.snippet AS sn, f.quality AS q, f.data_json AS dj '
        'FROM wb_build_facts f JOIN wb_build_materials m '
        'ON f.material_id = m.material_id AND f.task_id = m.task_id '
        'WHERE f.task_id = ? AND m.rel_path = ? ORDER BY f.fact_id', (task_id, rel_path))
    return [{'id': r['fid'], 'kind': r['kd'], 'module': r['md'], 'locator': _loads(r['lc'], {}),
             'snippet': r['sn'], 'quality': r['q'], 'data': _loads(r['dj'], {})} for r in rows]


# --- 合成物料（含中文值与点号键） ---------------------------------------------------

SAMPLES = {
    # JSON：嵌套键路径 + 数组（数组只产出摘要事实）
    'demo.json': (
        '{\n'
        '  "device": {\n'
        '    "name": "储能设备",\n'
        '    "capacity": 2.5,\n'
        '    "tags": ["并网", "调频", "备用"],\n'
        '    "cluster": {"id": "cl-01", "region": "华东"}\n'
        '  },\n'
        '  "version": 1\n'
        '}\n'),
    # JSON-LD：@context + @graph，节点含 name/definition/skos:definition
    'demo.jsonld': (
        '{\n'
        '  "@context": {"skos": "http://www.w3.org/2004/02/skos/core#"},\n'
        '  "@graph": [\n'
        '    {\n'
        '      "@id": "czy:entity:01",\n'
        '      "@type": "Entity",\n'
        '      "name": "储能簇",\n'
        '      "definition": "储能簇，一组储能设备的集合"\n'
        '    },\n'
        '    {\n'
        '      "@id": "czy:attr:01",\n'
        '      "@type": "Attribute",\n'
        '      "name": "soc",\n'
        '      "skos:definition": "荷电状态，0-100%"\n'
        '    }\n'
        '  ]\n'
        '}\n'),
    # 用户真实样本后缀：非标准扩展名 .jsonId 也必须走结构化解析
    'sample.jsonId': (
        '{\n'
        '  "@context": {"skos": "http://www.w3.org/2004/02/skos/core#"},\n'
        '  "@graph": [\n'
        '    {\n'
        '      "@id": "czy:entity:02",\n'
        '      "@type": "Entity",\n'
        '      "name": "电池簇",\n'
        '      "skos:definition": "电池簇，由多个电池模组构成"\n'
        '    }\n'
        '  ]\n'
        '}\n'),
    # YAML：两层嵌套
    'cfg.yaml': (
        'app:\n'
        '  name: 储能监控平台\n'
        '  server:\n'
        '    host: 127.0.0.1\n'
        '    port: 8080\n'
        '  pools:\n'
        '    - name: main\n'
        '      size: 5\n'),
    # CSV：表头 + 3 行
    'table.csv': (
        'device_id,capacity,status\n'
        'd-001,2.5,online\n'
        'd-002,3.0,offline\n'
        'd-003,1.8,online\n'),
    # properties：点号键 + 中文值（注释行不计事实）
    'app.properties': (
        '# 储能监控平台配置（注释不计入事实）\n'
        'spring.datasource.url=jdbc:mysql://127.0.0.1:3306/energy\n'
        'spring.datasource.username=储能管理员\n'
        'spring.redis.timeout=3000\n'
        'app.title=储能监控平台（中文值）\n'),
    # INI：节 + 键
    'conf.ini': (
        '; 储能服务配置（注释行）\n'
        '[server]\n'
        'host = 127.0.0.1\n'
        'port = 8080\n'
        '\n'
        '[storage]\n'
        'driver = mysql\n'
        'pool_size = 8\n'),
    # TOML：表 + 标量 + 数组
    'conf.toml': (
        '# 储能监控配置（注释行）\n'
        'title = "储能监控"\n'
        '\n'
        '[server]\n'
        'host = "127.0.0.1"\n'
        'port = 8080\n'
        '\n'
        '[limits]\n'
        'thresholds = [10, 20, 30]\n'
        'enabled = true\n'),
    # JSONL：两行 JSON，定位带真实行号
    'log.jsonl': (
        '{"ts": "2026-09-22T10:00:00", "device": "d-001", "kwh": 12.5}\n'
        '{"ts": "2026-09-22T10:01:00", "device": "d-002", "kwh": 9.8}\n'),
    # 失败路径：语法错误的 JSON（整篇坏 → 显式失败，零事实）
    'broken.json': (
        '{\n'
        '  "device": "储能设备",\n'
        '  "capacity": 2.5,\n'
        '  "tags": [1, 2,\n'
        '}\n'),
    # 既有格式回归：markdown 仍走 md 解析
    'notes.md': (
        '# 设备运行说明\n'
        '\n'
        '储能设备运行监测与额定容量说明。\n'
        '\n'
        '| 字段 | 说明 |\n'
        '|---|---|\n'
        '| capacity | 额定容量 |\n'),
}

STRUCT_SAMPLES = ('demo.json', 'demo.jsonld', 'sample.jsonId', 'cfg.yaml', 'table.csv',
                  'app.properties', 'conf.ini', 'conf.toml', 'log.jsonl')

FIXTURE_LINES = {}


def write_fixtures():
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    for name, text in SAMPLES.items():
        (FIXTURE_DIR / name).write_text(text, encoding='utf-8')
        FIXTURE_LINES[name] = text.splitlines()


def fixture_line(rel_path, number):
    """原始文件第 number 行（1 基）去空白；越界返回 None。"""
    lines = FIXTURE_LINES.get(str(rel_path or ''))
    if not lines or not isinstance(number, int) or number < 1 or number > len(lines):
        return None
    return lines[number - 1].strip()


_BARE_SEGMENT = re.compile(r'^[^.\[]+')


def jsonpath_get(tree, path):
    """极简键路径回放：支持 `$.a.b`、`$["a.b"]`、`$[0]`；取不到返回 (False, None)。"""
    if not str(path or '').startswith('$'):
        return False, None
    rest, node = str(path)[1:], tree
    while rest:
        if rest.startswith('.'):
            match = _BARE_SEGMENT.match(rest[1:])
            if not match:
                return False, None
            key = match.group(0)
            rest = rest[1 + len(key):]
        elif rest.startswith('['):
            end = rest.find(']')
            if end < 0:
                return False, None
            inner = rest[1:end]
            rest = rest[end + 1:]
            if inner.startswith('"') and inner.endswith('"'):
                key = json.loads(inner)
            else:
                try:
                    key = int(inner)
                except ValueError:
                    return False, None
        else:
            return False, None
        if isinstance(node, dict) and isinstance(key, str) and key in node:
            node = node[key]
        elif isinstance(node, list) and isinstance(key, int) and 0 <= key < len(node):
            node = node[key]
        else:
            return False, None
    return True, node


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


def upload(name, task_id):
    return _upload_bytes((FIXTURE_DIR / name).read_bytes(), task_id, name)


def poll_run(task_id, run_id, timeout=120.0):
    deadline = time.time() + timeout
    body = {}
    while True:
        _, body = api('/api/build-run', query='?taskId=%s&runId=%s' % (task_id, run_id))
        run = body.get('run') or {}
        if run.get('state') in TERMINAL_RUN_STATES or time.time() >= deadline:
            return run
        time.sleep(0.3)


def scan_task(task_id):
    """触发扫描并轮询到终态；返回 run（失败时状态与原因由调用方断言）。"""
    status, scan = api('/api/build-scan', {'taskId': task_id})
    if status != 200:
        raise Abort('触发扫描失败（HTTP %s）：%s' % (status, _short(scan)))
    run_id = require(scan.get('runId'), '扫描未返回 runId：%s' % _short(scan))
    return poll_run(task_id, run_id)


def materials_by_path(task_id):
    status, body = api('/api/build-materials', query='?taskId=' + task_id)
    if status != 200:
        raise Abort('读取材料清单失败（HTTP %s）：%s' % (status, _short(body)))
    return {m['relPath']: m for m in (body.get('items') or [])}


def structured_locator(kind, locator):
    """按需求 §2 的定位粒度判定一条事实是否是结构化定位（键路径 / 行号 / 节点 @id）。"""
    if not isinstance(locator, dict) or locator.get('kind') != kind:
        return False
    if locator.get('nodeId'):
        return True
    path = str(locator.get('path') or '')
    if kind in ('json', 'yaml', 'toml'):
        return path.startswith('$')
    if kind in ('csv', 'properties', 'ini'):
        return isinstance(locator.get('line'), int) and locator['line'] >= 1
    return bool(path)


# --- 前置：接线就绪判定（缺一即无法进行端到端验证，按未就绪明确报告） -------------------

def preflight():
    problems = []
    for kind in STRUCTURED_KINDS:
        if kind not in protocol.MATERIAL_KINDS:
            problems.append('protocol.MATERIAL_KINDS 缺少 %s' % kind)
        if kind not in parsers_base.REGISTRY:
            problems.append('parsers.REGISTRY 未注册 %s 解析器' % kind)
    if problems:
        raise Abort('结构化解析接线未落盘，端到端测试无法进行：%s' % '；'.join(problems))


# --- 主流程 ---------------------------------------------------------------------

def main():
    # 0) 隔离
    from workbench.paths import DATA_ROOT
    from workbench.storage import engine as engine_mod
    check(str(DATA_ROOT) == str(TMP) and str(REPO) not in str(DATA_ROOT),
          'DATA_ROOT 指向本次临时根（未被真实仓库根劫持）',
          actual=str(DATA_ROOT), expected=str(TMP))
    check(str(engine_mod.resolve_url()).startswith('sqlite:///' + str(TMP)),
          'storage.engine.resolve_url() 落在临时根内', actual=engine_mod.resolve_url())
    check(DB_PATH.is_file(), '隔离 sqlite 库已在临时根内创建', actual=str(DB_PATH))
    check(PORT not in RESERVED_PORTS and PORT > 0,
          '服务使用动态空闲端口（未占用 18765/18881 等其他实例端口）', actual=PORT)

    # 1) 登录（种子方式与既有回归一致）
    auth_client.wait_ready(BASE, timeout=30)
    _, token = auth_client.register_or_login(BASE, 'build_struct_e2e', 'test1234')
    SESSION['token'] = require(token, '测试账号会话未建立')
    status, auth_state = api('/api/auth-state')
    check(status == 200 and bool((auth_state.get('user') or {}).get('username')),
          '经真实认证接口建立测试账号会话（Cookie wiz_session）', actual=(status, auth_state))

    # 2) 扩展名 → 新 kind → 专用解析器：接线表直接核对（需求 §3）
    kind_map = {name: protocol.detect_kind(name) for name in STRUCT_SAMPLES}
    check(all(kind in STRUCTURED_KINDS and kind in parsers_base.REGISTRY
              for kind in kind_map.values()),
          '9 种结构化后缀经 detect_kind 映射到新 kind 且均已注册专用解析器（§3）',
          actual=kind_map)

    # 3) 建任务并上传 9 份合成样本
    status, created = api('/api/build-task-create', {'name': '结构化解析端到端'})
    task_id = require((created.get('task') or {}).get('id'),
                      '任务创建失败：%s' % _short(created))
    check(status == 200, 'POST build-task-create 200 并返回任务 ID', actual=(status, task_id))
    write_fixtures()
    for name in STRUCT_SAMPLES:
        status, body = upload(name, task_id)
        check(status == 200 and len(body.get('materials') or []) == 1,
              '分片上传 %s 成功并登记 1 份材料' % name, actual=(status, _short(body)))
    items = materials_by_path(task_id)
    check(set(items) == set(STRUCT_SAMPLES) and len(items) == len(STRUCT_SAMPLES),
          'GET build-materials 返回 9 份材料', actual=sorted(items))

    # 4) 扫描并逐份断言
    run = scan_task(task_id)
    check(run.get('state') == 'succeeded', '扫描运行 succeeded',
          actual=run.get('error') or run.get('state'))
    items = materials_by_path(task_id)
    states = {name: (items.get(name) or {}).get('parseState') for name in STRUCT_SAMPLES}
    check(all(state == 'success' for state in states.values()),
          '9 份结构化样本全部 parseState=success（含 .jsonId 非标准后缀）', actual=states)

    for name in STRUCT_SAMPLES:
        material = items.get(name) or {}
        coverage = material.get('coverage') or {}
        kind = protocol.detect_kind(name)
        facts = facts_of(task_id, name)
        count = int(coverage.get('factCount') or 0)

        check(count > 0, '%s：coverage.factCount > 0（产出结构化事实）' % name,
              actual=(count, material.get('parseState')))
        check(count == len(facts), '%s：库内事实行数与 coverage.factCount 一致' % name,
              actual=(count, len(facts)))

        notes = [str(note) for note in (coverage.get('notes') or [])]
        hit = [note for note in notes for marker in DEGRADE_MARKERS if marker in note]
        check(not hit, '%s：coverage.notes 无「文本线索降级」类降级标记（走专用解析层）' % name,
              actual=hit or notes[:3])

        located = [fact for fact in facts if structured_locator(kind, fact['locator'])]
        check(bool(located),
              '%s：至少一条事实是结构化定位（kind=%s，键路径/节点 @id/真实行号）' % (name, kind),
              actual=[fact['locator'] for fact in facts[:3]])

    # 逐格式细粒度断言（定位可回读原文 / 取值可回放）
    demo_facts = facts_of(task_id, 'demo.json')
    leaves = {fact['locator'].get('path'): fact for fact in demo_facts}
    replay_ok, replay_value = jsonpath_get(json.loads(SAMPLES['demo.json']), '$.device.capacity')
    check(replay_ok and leaves.get('$.device.capacity', {}).get('snippet') == '2.5'
          and replay_value == 2.5,
          'demo.json：叶子事实路径可回放原文（$.device.capacity = 2.5）',
          actual=(replay_value, leaves.get('$.device.capacity', {}).get('snippet')))
    check(leaves.get('$.device.cluster.region', {}).get('snippet') == '华东',
          'demo.json：深层嵌套键路径事实保留中文值（$.device.cluster.region）',
          actual=leaves.get('$.device.cluster.region', {}).get('snippet'))
    tags = next((fact for fact in demo_facts
                 if fact['kind'].endswith('ArraySummary')
                 and fact['locator'].get('path') == '$.device.tags'), {})
    check(tags.get('data', {}).get('length') == 3,
          'demo.json：数组仅产出摘要事实（长度=3，不逐元素展开）',
          actual=(tags.get('kind'), tags.get('data')))

    jsonld_facts = facts_of(task_id, 'demo.jsonld')
    nodes = [fact for fact in jsonld_facts if fact['locator'].get('nodeId')]
    node_ids = {fact['locator'].get('nodeId') for fact in nodes}
    check({'czy:entity:01', 'czy:attr:01'} <= node_ids
          and any(fact['quality'] == 'high' for fact in nodes),
          'demo.jsonld：@graph 节点按 @id 产出高质量事实（定位=节点 @id）',
          actual=(sorted(node_ids), [fact['quality'] for fact in nodes][:3]))
    # 字段事实按 (节点 @id, 规范字段名) 索引：两个节点都有 definition 语义字段
    # （definition / skos:definition），只按 canonical 建键会因落库顺序不同而互相覆盖。
    fields = {(fact['locator'].get('nodeId'), fact['data'].get('canonical')): fact
              for fact in jsonld_facts if fact['kind'].endswith('jsonLdField')}
    entity_fields = {canonical for node_id, canonical in fields if node_id == 'czy:entity:01'}
    check({'name', 'definition'} <= entity_fields,
          'demo.jsonld：储能簇节点产出 name/definition 语义字段事实',
          actual=sorted((node_id, canonical) for node_id, canonical in fields
                        if node_id == 'czy:entity:01'))
    check(fields.get(('czy:entity:01', 'definition'), {}).get('data', {}).get('value')
          == '储能簇，一组储能设备的集合',
          'demo.jsonld：definition 字段值逐字取原文',
          actual=fields.get(('czy:entity:01', 'definition'), {}).get('data'))
    check(fields.get(('czy:attr:01', 'definition'), {}).get('data', {}).get('field')
          == 'skos:definition',
          'demo.jsonld：skos:definition（前缀写法）按语义字段识别且归属正确节点',
          actual=fields.get(('czy:attr:01', 'definition'), {}).get('data'))

    jsonid_facts = facts_of(task_id, 'sample.jsonId')
    check(any(fact['locator'].get('nodeId') == 'czy:entity:02' for fact in jsonid_facts),
          'sample.jsonId：非标准扩展名 .jsonId 仍走 JSON-LD 结构化解析（节点 @id 定位）',
          actual=[fact['locator'] for fact in jsonid_facts[:3]])

    yaml_facts = facts_of(task_id, 'cfg.yaml')
    yaml_paths = {fact['locator'].get('path'): fact for fact in yaml_facts}
    yaml_replay = jsonpath_get(yaml.safe_load(SAMPLES['cfg.yaml']), '$.app.server.host')
    check(yaml_paths.get('$.app.server.host', {}).get('snippet') == '127.0.0.1'
          and yaml_replay == (True, '127.0.0.1'),
          'cfg.yaml：两层嵌套键路径事实可回放原文（与 JSON 同一遍历口径）',
          actual=(yaml_paths.get('$.app.server.host', {}).get('snippet'), yaml_replay,
                  sorted(path for path in yaml_paths if path)[:6]))

    csv_facts = facts_of(task_id, 'table.csv')
    header = next((fact for fact in csv_facts if fact['kind'] == 'csvHeader'), {})
    summary = next((fact for fact in csv_facts if fact['kind'] == 'csvSummary'), {})
    check(header.get('data', {}).get('columns') == ['device_id', 'capacity', 'status']
          and header.get('locator', {}).get('line') == 1,
          'table.csv：表头列名事实 + 定位到表头行（行号=1）',
          actual=(header.get('data', {}).get('columns'), header.get('locator')))
    check(summary.get('data', {}).get('rowCount') == 3,
          'table.csv：总行数事实正确（3 行数据）', actual=summary.get('data'))
    capacity_column = next((fact for fact in csv_facts
                            if fact['kind'] == 'csvColumn'
                            and fact['data'].get('column') == 'capacity'), {})
    check(capacity_column.get('data', {}).get('nonEmptyCount') == 3
          and capacity_column.get('data', {}).get('inferredType') == 'double',
          'table.csv：列统计事实（非空计数 + 推断类型）',
          actual=capacity_column.get('data'))

    prop_facts = facts_of(task_id, 'app.properties')
    prop_entries = {fact['data'].get('key'): fact for fact in prop_facts}
    title = prop_entries.get('app.title', {})
    title_line = fixture_line('app.properties', title.get('locator', {}).get('line') or 0)
    check(title.get('data', {}).get('value') == '储能监控平台（中文值）'
          and 'app.title' in str(title_line or ''),
          'app.properties：中文值按 Java 规范解码且定位行可回读原文',
          actual=(title.get('data', {}).get('value'), title_line))
    check(prop_entries.get('spring.datasource.url', {}).get('data', {}).get('hierarchy')
          == ['spring', 'datasource', 'url'],
          'app.properties：点号键的层级提示记入 data.hierarchy',
          actual=prop_entries.get('spring.datasource.url', {}).get('data'))
    check(not any(fact.get('locator', {}).get('line') == 1 for fact in prop_facts),
          'app.properties：注释行（第 1 行）不计入事实',
          actual=[fact['locator'] for fact in prop_facts][:3])

    ini_facts = facts_of(task_id, 'conf.ini')
    ini_entries = {(fact['data'].get('section'), fact['data'].get('key')): fact
                   for fact in ini_facts}
    pool = ini_entries.get(('storage', 'pool_size'), {})
    pool_line = fixture_line('conf.ini', pool.get('locator', {}).get('line') or 0)
    check(pool.get('data', {}).get('value') == '8' and 'pool_size' in str(pool_line or '')
          and pool.get('locator', {}).get('section') == 'storage',
          'conf.ini：节 + 键事实，定位带真实行号且行号可回读原文',
          actual=(pool.get('data'), pool.get('locator'), pool_line))

    toml_facts = facts_of(task_id, 'conf.toml')
    toml_paths = {fact['locator'].get('path'): fact for fact in toml_facts}
    port_fact = toml_paths.get('$.server.port', {})
    port_line = fixture_line('conf.toml', port_fact.get('locator', {}).get('line') or 0)
    check(port_fact.get('snippet') == '8080' and 'port' in str(port_line or ''),
          'conf.toml：[table] 标量按键路径产出事实且行号可回读原文',
          actual=(port_fact.get('snippet'), port_fact.get('locator'), port_line))
    thresholds = toml_paths.get('$.limits.thresholds', {})
    check(thresholds.get('kind') == 'tomlArraySummary' and thresholds.get('data', {}).get('length') == 3,
          'conf.toml：数组按统一口径产出摘要事实（长度=3）',
          actual=(thresholds.get('kind'), thresholds.get('data')))

    jsonl_facts = facts_of(task_id, 'log.jsonl')
    jsonl_lines = sorted({fact['locator'].get('line') for fact in jsonl_facts})
    check(jsonl_lines == [1, 2] and all(fact['locator'].get('kind') == 'json'
                                        for fact in jsonl_facts),
          'log.jsonl：两行 JSON 逐行解析，定位带真实行号 1/2', actual=jsonl_lines)

    # 5) 失败路径：语法错误的 JSON 必须显式失败，不得静默成功
    status, body = upload('broken.json', task_id)
    check(status == 200, '上传语法错误的 broken.json 成功（材料登记与解析能力分离）',
          actual=(status, _short(body)))
    run = scan_task(task_id)
    check(run.get('state') == 'succeeded',
          '重扫运行 succeeded（单文件解析失败不阻塞其余材料）',
          actual=run.get('error') or run.get('state'))
    items = materials_by_path(task_id)
    broken = items.get('broken.json') or {}
    broken_cov = broken.get('coverage') or {}
    broken_facts = facts_of(task_id, 'broken.json')
    reasons = [str(item.get('reason') or '') for item in (broken_cov.get('failedSegments') or [])]
    text = ' '.join([str(item) for item in (broken_cov.get('notes') or [])] + reasons)
    check(broken.get('parseState') in ('failed', 'partial'),
          'broken.json：明确失败（failed/partial），不得静默成功', actual=broken.get('parseState'))
    check(int(broken_cov.get('factCount') or 0) == 0 and not broken_facts,
          'broken.json：坏 JSON 产出 0 条事实（不当作空内容/降级线索继续）',
          actual=(broken_cov.get('factCount'), len(broken_facts)))
    check('语法错误' in text and '不回退' in text,
          'broken.json：coverage 写明语法错误位置且声明不回退 LLM 兜底/文本线索',
          actual=(text[:200], reasons[:1]))
    check(any(isinstance(item.get('locator'), dict) and item['locator'].get('line')
              for item in (broken_cov.get('failedSegments') or [])),
          'broken.json：failedSegments 带错误行号（可定位修复）',
          actual=broken_cov.get('failedSegments'))
    rest_states = {name: (items.get(name) or {}).get('parseState') for name in STRUCT_SAMPLES}
    check(all(state == 'success' for state in rest_states.values()),
          '坏文件不传染：其余 9 份样本仍为 success（复用既有事实）', actual=rest_states)

    # 6) 既有格式回归：markdown 仍走 md 解析
    status, body = upload('notes.md', task_id)
    check(status == 200, '上传 notes.md 成功', actual=(status, _short(body)))
    run = scan_task(task_id)
    check(run.get('state') == 'succeeded', '第三次扫描 succeeded',
          actual=run.get('error') or run.get('state'))
    items = materials_by_path(task_id)
    notes_md = items.get('notes.md') or {}
    md_cov = notes_md.get('coverage') or {}
    md_facts = facts_of(task_id, 'notes.md')
    md_notes = [str(item) for item in (md_cov.get('notes') or [])]
    check(notes_md.get('parseState') == 'success' and int(md_cov.get('factCount') or 0) > 0,
          'notes.md：既有 markdown 解析不退化（success 且 factCount>0）',
          actual=(notes_md.get('parseState'), md_cov.get('factCount')))
    check(any(fact['locator'].get('kind') == 'md' for fact in md_facts),
          'notes.md：定位器仍为 md（既有格式未被新解析器改动）',
          actual=[fact['locator'] for fact in md_facts[:3]])
    check(not any(marker in note for note in md_notes for marker in DEGRADE_MARKERS),
          'notes.md：coverage.notes 无降级标记', actual=md_notes[:3])

    # 7) 落库一致性：库内事实总数 = 各材料 coverage.factCount 之和
    items = materials_by_path(task_id)
    total_facts = db_rows('SELECT COUNT(*) AS n FROM wb_build_facts WHERE task_id = ?',
                          (task_id,))[0]['n']
    coverage_total = sum(int((m.get('coverage') or {}).get('factCount') or 0)
                         for m in items.values())
    check(total_facts == coverage_total and total_facts > 0,
          '库内事实总数与全部材料 coverage.factCount 之和一致',
          actual=(total_facts, coverage_total))
    return 0


if __name__ == '__main__':
    code = 1
    try:
        preflight()
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
