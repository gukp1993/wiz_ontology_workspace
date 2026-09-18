"""显示格式优化（任务 C）：mg:formatting 持久化零丢失回归（A12 / A13）。

两部分：
1. 纯内存：带 mode:code + 未知扩展键 {'x-custom':1} + 旧 min≠max、natural、builtin 新键
   的 formatting 属性，经 model_format.encode/decode 往返零丢失；validate_json 接受。
2. 临时服务：WIZ_WORKBENCH_ROOT=<临时根> + WIZ_WORKBENCH_PORT=18930 起隔离实例，
   保存含上述配置的本体草稿 → 重新 GET /api/state 校验完整保留，服务重启后仍保留；
   同时验证 /api/format-preview 对历史 code 配置的透传。真实 ontology/ 只读未写。

纯 python3 标准库（无 pytest）。
运行：python3 tests/test_formatting_roundtrip.py
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PORT = 18930
ORIGIN = BASE = f'http://127.0.0.1:{PORT}'

TMP = Path(tempfile.mkdtemp(prefix='wiz_fmt_roundtrip_'))
os.environ['WIZ_WORKBENCH_ROOT'] = str(TMP)  # 防御：万一测试内 import workbench 模块

sys.path.insert(0, str(REPO))

PROC = None
PASSED = []
FAILURES = []


def check(cond, message, actual=None, expected=None):
    if cond:
        return
    FAILURES.append(message)
    print(f'[失败] {message}')
    if expected is not None:
        print('  预期: ' + json.dumps(expected, ensure_ascii=False, default=str)[:2000])
    if actual is not None:
        print('  实际: ' + json.dumps(actual, ensure_ascii=False, default=str)[:2000])


def ok(step, desc):
    PASSED.append(step)
    print(f'通过 {step}) {desc}')


def shutdown():
    global PROC
    if PROC is not None:
        PROC.terminate()
        try:
            PROC.wait(timeout=10)
        except subprocess.TimeoutExpired:
            PROC.kill()
            try:
                PROC.wait(timeout=5)  # SIGKILL 后也等端口真正释放
            except subprocess.TimeoutExpired:
                pass
        PROC = None


def request(method, path, payload=None):
    data = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    headers = {'Content-Type': 'application/json'}
    if method == 'POST':
        headers['Origin'] = ORIGIN
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            return exc.code, json.loads(raw)
        except ValueError:
            return exc.code, raw


def start_service():
    global PROC
    env = dict(os.environ, WIZ_WORKBENCH_ROOT=str(TMP), WIZ_WORKBENCH_PORT=str(PORT))
    PROC = subprocess.Popen([sys.executable, '-m', 'workbench.server'], cwd=str(REPO), env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    deadline = time.time() + 20
    ready = False
    while time.time() < deadline:
        if PROC.poll() is not None:
            print('[失败] 服务进程提前退出，输出如下：')
            print(PROC.stdout.read().decode(errors='replace'))
            sys.exit(1)
        try:
            status, _ = request('GET', '/api/ontologies')
            if status == 200:
                ready = True
                break
            time.sleep(0.2)
        except (urllib.error.URLError, ConnectionError, OSError):
            time.sleep(0.2)
    check(ready, '服务未在 20 秒内就绪（/api/ontologies）', actual='未就绪', expected='200')


# --- 配置样例（阶段 0 冻结 schema；含未知扩展键与历史键）--------------------------------

LEGACY_CODE_CONFIG = {
    'mode': 'code',
    'code': 'return String(value).toUpperCase()',
    'style': 'upper',              # 历史样式键与 code 共存
    'minDecimals': 1, 'maxDecimals': 3,  # 旧 min≠max 精度
    'x-custom': 1,                 # 未知扩展键必须零丢失
}

NATURAL_CONFIG = {
    'mode': 'natural',
    'instruction': '输入为 0—100 的 SOC，保留一位小数并添加百分号，只输出结果。',
    'emptyText': '暂无读数',
    'x-meta': {'kept': True},
}

BUILTIN_NEW_KEYS_CONFIG = {
    'mode': 'builtin',
    'style': 'series',
    'timeFormat': {'style': 'datetime', 'precision': 'minute', 'timezone': 'UTC'},
    'valueFormat': {'style': 'percent', 'percentInput': 'hundred', 'decimals': 1},
    'maxItems': 25,
    'elementType': 'number',
    'elementFormat': {'style': 'standard', 'decimals': 1},
    'fields': ['manufacturer', 'model'],
    'template': '{manufacturer} / {model}',
    'mappings': [{'from': 'running', 'to': '运行中'}],
    'separator': '；',
    'emptyText': '—',
    'x-keep': {'nested': [1, 2]},
}

ALL_CONFIGS = {'legacy_code': LEGACY_CODE_CONFIG, 'natural': NATURAL_CONFIG,
               'builtin_new_keys': BUILTIN_NEW_KEYS_CONFIG}

CONTEXT = {'mg': 'https://example.org/mg#', 'xsd': 'http://www.w3.org/2001/XMLSchema#'}

RANGES = {'legacy_code': 'xsd:string', 'natural': 'xsd:string', 'builtin_new_keys': 'xsd:double'}


def legacy_graph():
    station = {'@id': 'mg:Station', '@type': 'owl:Class', 'rdfs:label': '电站'}
    nodes = [station]
    for key, config in ALL_CONFIGS.items():
        nodes.append({'@id': f'mg:{key}', '@type': 'owl:DatatypeProperty', 'rdfs:label': key,
                      'mg:apiName': key, 'rdfs:domain': {'@id': 'mg:Station'},
                      'rdfs:range': {'@id': RANGES[key]},
                      'mg:formatting': {'@type': '@json', '@value': config}})
    return nodes


# --- 1) model_format 纯内存往返 ---------------------------------------------------------

def test_encode_decode_roundtrip():
    from workbench import model_format
    legacy = {'@context': dict(CONTEXT), '@graph': legacy_graph()}
    model = model_format.encode_ontology(legacy)
    props = {r['id']: r for r in model['properties']}
    for key, config in ALL_CONFIGS.items():
        check(props[f'mg:{key}'].get('formatting') == config,
              f'encode 后 schema 记录 formatting 应与原配置全等（{key}）',
              actual=props[f'mg:{key}'].get('formatting'), expected=config)
    decoded = model_format.decode_ontology(model)
    check(decoded == legacy, 'decode(encode(legacy)) 应与原 JSON-LD 完全一致（零丢失）',
          actual=decoded, expected=legacy)
    # schema 形态再走一遍 validate_json（含未知扩展键的 formatting 不应被协议层拒绝）
    model_format.validate_json(model)
    # schema → JSON-LD → schema 二次往返同样零丢失
    model2 = model_format.encode_ontology(decoded)
    check(model2 == model, '二次往返后 schema 形态稳定', actual=model2, expected=model)


# --- 2) 临时服务保存 / 重载 ---------------------------------------------------------------

def schema_property(key):
    dtype = {'type': 'timeSeries', 'valueType': 'double'} if key == 'builtin_new_keys' else {'type': 'string'}
    return {'id': f'mg:{key}', 'displayName': key, 'apiName': key, 'objectTypeId': 'mg:Device',
            'dataType': dtype, 'formatting': ALL_CONFIGS[key]}


def fetch_properties():
    status, payload = request('GET', '/api/state')
    check(status == 200, 'GET /api/state 应 200', actual=status, expected=200)
    return payload


def verify_state_formatting(payload, phase):
    props = {r['id']: r for r in payload['state']['ontology']['properties']}
    for key, config in ALL_CONFIGS.items():
        record = props.get(f'mg:{key}')
        check(record is not None, f'{phase}：属性 mg:{key} 应存在',
              actual=sorted(props), expected=list(ALL_CONFIGS))
        if record is not None:
            check(record.get('formatting') == config,
                  f'{phase}：mg:{key}.formatting 应与保存内容完全一致（含未知扩展键）',
                  actual=record.get('formatting'), expected=config)
    return props


def test_service_roundtrip():
    probe = socket.socket()
    try:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(('127.0.0.1', PORT))
    except OSError as exc:
        print(f'[失败] 端口 {PORT} 已被占用，无法启动测试实例：{exc}')
        sys.exit(1)
    finally:
        probe.close()

    start_service()
    ok('1', f'隔离服务已就绪 WIZ_WORKBENCH_ROOT={TMP} 端口 {PORT}（真实 ontology/ 未挂载）')

    # 空白临时根：GET /api/state 返回空白本体，注入属性后保存
    payload = fetch_properties()
    state, revision = payload['state'], payload['revision']
    state['ontology']['objectTypes'].append({'id': 'mg:Device', 'displayName': '演示设备'})
    for key in ALL_CONFIGS:
        state['ontology']['properties'].append(schema_property(key))
    state['ontology']['definitionOrder'] += ['mg:Device'] + [f'mg:{k}' for k in ALL_CONFIGS]

    status, saved = request('POST', '/api/save', {'state': state, 'revision': revision})
    check(status == 200 and saved.get('revision'), 'POST /api/save 应 200 并返回新 revision',
          actual={'status': status, 'body': saved}, expected=200)
    ok('2', '含 mode:code + 未知键 + min≠max 的本体草稿保存成功')

    reloaded = fetch_properties()
    check(reloaded.get('revision') == saved.get('revision'), '保存后 GET /api/state revision 应一致',
          actual=reloaded.get('revision'), expected=saved.get('revision'))
    verify_state_formatting(reloaded, '保存后重读')
    ok('3', '保存后重新 GET /api/state：三份 formatting（含 x-custom / x-keep / x-meta）零丢失')

    # /api/format-preview 对历史 code 配置透传可用（服务端路径复用同一 format_value）
    status, preview = request('POST', '/api/format-preview',
                              {'value': 'abc', 'dataType': 'string', 'config': LEGACY_CODE_CONFIG})
    check(status == 200 and preview.get('formatted') == 'ABC',
          '/api/format-preview 历史 code 配置应输出 ABC',
          actual={'status': status, 'body': preview}, expected={'formatted': 'ABC'})
    ok('4', '/api/format-preview 透传历史 code 配置正常执行')

    # 重启同一临时根：落盘数据仍完整（A12 持久化 + A13 只写临时根）
    shutdown()
    start_service()
    after_restart = fetch_properties()
    verify_state_formatting(after_restart, '重启后')
    ok('5', '服务重启后 formatting 配置仍完整保留')

    # 库化后草稿不再落盘：直接读当前 head 快照 payload 验证同一不变量
    from workbench.storage import assets as store
    from workbench.storage.engine import read_connection as _rc
    os.environ.setdefault('WIZ_DATABASE_URL', '')
    from workbench.storage.engine import resolve_url as _rurl
    with _rc(_rurl()) as _conn:
        _head = store.read_current('model', 'storage')
    check(_head is not None, '草稿应写入临时根数据库（storage 资产存在）', actual=str(TMP), expected='head 快照存在')
    if _head is not None:
        on_disk = _head['snapshot']['payload']
        disk_props = {r['id']: r for r in (on_disk.get('ontology') or {}).get('properties', [])}
        check(disk_props.get('mg:legacy_code', {}).get('formatting', {}).get('x-custom') == 1,
              '快照 JSON 中未知扩展键 x-custom 应原样存在',
              actual=disk_props.get('mg:legacy_code'), expected=LEGACY_CODE_CONFIG)
    ok('6', '落盘草稿文件含未知扩展键，全部写入发生在临时根内')


def main():
    test_encode_decode_roundtrip()
    if FAILURES:
        print('\n内存往返阶段已有失败，跳过服务阶段')
    else:
        ok('0', 'model_format encode/decode 往返零丢失 + validate_json 接受')
        test_service_roundtrip()


if __name__ == '__main__':
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        import traceback
        traceback.print_exc()
        print(f'\n（临时根保留供排查：{TMP}）')
        sys.exit(1)
    finally:
        shutdown()
    if FAILURES:
        print(f'\n{len(FAILURES)} 项失败：')
        for item in FAILURES:
            print(f'  - {item}')
        print(f'（临时根保留供排查：{TMP}）')
        sys.exit(1)
    import shutil
    shutil.rmtree(TMP, ignore_errors=True)
    print(f'\n全部通过（{len(PASSED)} 步）；临时根已清理')
