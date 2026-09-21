# -*- coding: utf-8 -*-
"""Q06 分层性能与资源测试（仅隔离实例；必须独占运行，不与其他负载测试并行）。

用法（先手动启动专属实例，见 .runtime/test-evidence/q06/README）：
  .runtime/venv/bin/python tests/deep_perf.py gen --scale 10
  .runtime/venv/bin/python tests/deep_perf.py gen --scale 100
  .runtime/venv/bin/python tests/deep_perf.py gen --scale 500
  .runtime/venv/bin/python tests/deep_perf.py bench
  .runtime/venv/bin/python tests/deep_perf.py concurrency
  .runtime/venv/bin/python tests/deep_perf.py resources --port 18932
环境变量：PERF_BASE（默认 http://127.0.0.1:18932）。
状态构造走 @graph JSON-LD 编辑形态（与 Q02 套件同一批 builder）；
全部经真实 HTTP 生产入口；只写专属数据根合成数据；不修改业务代码。
"""
import argparse
import json
import os
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO))

from deep_ontology_client import blank_state  # noqa: E402
from deep_ontology_common import obj, private_prop, link  # noqa: E402
import auth_client  # noqa: E402

BASE = os.environ.get('PERF_BASE', 'http://127.0.0.1:18932')
USER = 'qa_perf'
PWD = 'DeepPerf!2026#perf'
OUT_DIR = REPO / '.runtime' / 'test-evidence' / 'q06'
CACHE = OUT_DIR / 'ontologies.json'
OUT_DIR.mkdir(parents=True, exist_ok=True)

_cookie = None


def cookie():
    global _cookie
    if _cookie is None:
        auth_client.register_or_login(BASE, USER, PWD)
        _cookie = auth_client.auth_headers(BASE, USER, PWD)['Cookie']
    return _cookie


def call(path, payload=None, method=None, timeout=60):
    hdrs = {'Cookie': cookie(), 'Origin': BASE}
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        hdrs['Content-Type'] = 'application/json'
    req = urllib.request.Request(BASE + path, data=data, headers=hdrs,
                                 method=method or ('POST' if data else 'GET'))
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read(), time.perf_counter() - t0
    except urllib.error.HTTPError as e:
        return e.code, e.read(), time.perf_counter() - t0


def log(rec):
    line = json.dumps(dict(rec, ts=time.strftime('%H:%M:%S')), ensure_ascii=False)
    print(line)
    with open(OUT_DIR / 'perf.jsonl', 'a', encoding='utf-8') as f:
        f.write(line + '\n')


def pct(samples, p):
    s = sorted(samples)
    return s[max(0, min(len(s) - 1, int(round(p / 100 * (len(s) - 1)))))]


def make_graph(n_obj, n_prop):
    graph = []
    for i in range(n_obj):
        oid = f'perfobj{i:04d}'
        graph.append(obj(oid, f'性能对象{i:04d}', '合成对象，用于性能基线测量。' * 4))
        for j in range(n_prop):
            graph.append(private_prop(f'perfattr{i:04d}_{j:02d}', f'属性{j:02d}', oid, xsd='double'))
    for i in range(max(0, n_obj - 1)):
        graph.append(link(f'perflink{i:04d}', '顺序关联', f'mg:perfobj{i:04d}', f'mg:perfobj{i + 1:04d}'))
    return graph


def gen(scale):
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    if str(scale) in cache:
        print(f'scale {scale} already exists: {cache[str(scale)]}')
        return
    name = f'PERF-S{scale}-{uuid.uuid4().hex[:6]}'
    st, body, dt = call('/api/ontologies', {'name': name}, 'POST')
    assert st == 201, f'create failed {st} {body[:200]}'
    oid = json.loads(body)['id']
    state = blank_state(name)
    state['ontology']['@graph'] = make_graph(scale, 10)
    state['workspaceId'] = oid
    st, body, dt = call('/api/state?ontology=' + oid)
    rev = json.loads(body)['revision']
    st, body, dt = call('/api/save', {'state': state, 'revision': rev}, 'POST')
    j = json.loads(body) if body[:1] == b'{' else {}
    size_kb = round(len(json.dumps({'state': state, 'revision': rev})) / 1024)
    print(f'gen {scale}: save HTTP {st} {dt * 1000:.0f}ms body={size_kb}KB rev={str(j.get("revision"))[:14]}')
    assert st == 200, body[:400]
    cache[str(scale)] = {'modelId': oid, 'revision': j['revision'], 'body_kb': size_kb}
    CACHE.write_text(json.dumps(cache, indent=1))
    log({'phase': 'gen', 'scale': scale, 'first_save_ms': round(dt * 1000), 'body_kb': size_kb})


def bench():
    cache = json.loads(CACHE.read_text())
    N = 20
    for scale in sorted(cache, key=int):
        oid = cache[scale]['modelId']
        st, body, _ = call(f'/api/state?ontology={oid}')
        assert st == 200
        full = json.loads(body)
        state = full['state']
        rev = full['revision']
        saves, vals, reads = [], [], []
        for i in range(N):
            st, body, dt = call('/api/save', {'state': state, 'revision': rev}, 'POST')
            if st != 200:
                log({'phase': 'bench', 'scale': scale, 'iter': i, 'FAILED': body[:200]})
                break
            rev = json.loads(body)['revision']
            saves.append(dt)
            st, body, dt = call('/api/validate', {'state': state}, 'POST')
            if st == 200:
                vals.append(dt)
            else:
                log({'phase': 'bench', 'scale': scale, 'validate_status': st, 'err': body[:150]})
            st, body, dt = call(f'/api/state?ontology={oid}')
            reads.append(dt)
        for op, samples in (('save', saves), ('validate', vals), ('read_state', reads)):
            if samples:
                log({'phase': 'bench', 'scale': scale, 'op': op, 'n': len(samples),
                     'p50_ms': round(pct(samples, 50) * 1000),
                     'p95_ms': round(pct(samples, 95) * 1000),
                     'max_ms': round(max(samples) * 1000),
                     'mean_ms': round(statistics.mean(samples) * 1000)})


def concurrency():
    cache = json.loads(CACHE.read_text())
    scale = max(cache, key=int)
    oid = cache[scale]['modelId']
    for workers in (1, 2, 4, 8):
        per = max(4, 32 // workers)
        lat, lock, errors = [], threading.Lock(), [0]

        def worker():
            local = []
            for _ in range(per):
                st, body, dt = call(f'/api/state?ontology={oid}')
                if st == 200:
                    local.append(dt)
                else:
                    local.append(None)
            with lock:
                for x in local:
                    (lat.append(x) if x is not None else errors.__setitem__(0, errors[0] + 1))

        t0 = time.perf_counter()
        ts = [threading.Thread(target=worker) for _ in range(workers)]
        [t.start() for t in ts]
        [t.join(timeout=60) for t in ts]
        wall = time.perf_counter() - t0
        log({'phase': 'concurrency_read', 'scale': scale, 'workers': workers,
             'requests': workers * per, 'ok': len(lat), 'err': errors[0],
             'wall_s': round(wall, 2), 'rps': round(len(lat) / wall, 1),
             'p50_ms': round(pct(lat, 50) * 1000) if lat else None,
             'p95_ms': round(pct(lat, 95) * 1000) if len(lat) > 3 else None,
             'max_ms': round(max(lat) * 1000) if lat else None})


def resources(port):
    out = subprocess.run(['lsof', '-tiTCP:' + str(port), '-sTCP:LISTEN'], capture_output=True, text=True)
    pids = out.stdout.strip().splitlines()
    rss = None
    if pids:
        r = subprocess.run(['ps', '-o', 'rss=', '-p', pids[0]], capture_output=True, text=True)
        if r.stdout.strip():
            rss = int(r.stdout.strip())
    db = REPO / '.runtime' / 'test-data-q06' / 'data' / 'workbench.sqlite3'
    log({'phase': 'resources', 'port': port, 'pid': pids[:1], 'rss_kb': rss,
         'db_bytes': db.stat().st_size if db.exists() else None})


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('cmd', choices=['gen', 'bench', 'concurrency', 'resources'])
    p.add_argument('--scale', type=int)
    p.add_argument('--port', type=int, default=18932)
    a = p.parse_args()
    {'gen': lambda: gen(a.scale), 'bench': bench, 'concurrency': concurrency,
     'resources': lambda: resources(a.port)}[a.cmd]()
