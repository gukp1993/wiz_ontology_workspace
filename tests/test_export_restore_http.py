"""批次 A 回归：/api/export 与 /api/restore 的真实 HTTP 链路。

背景（代码审查复核确认的两个已存在故障）：
- R1 导出：`POST_ROUTES['/api/export']` 原值为 None，do_POST 用 `handler is None`
  判路由存在性，导致导出在前置检查阶段就返回 404，ZIP 分支永不可达。
- R2 恢复：`post_restore` 调用了全仓不存在的 `_server_current()`，恢复必然 NameError。

本测试只断言业务行为，并为上述两点提供防回归：
- 导出返回可打开的真实 ZIP（含 manifest/ontology），且不修改草稿；
- 导出仍受既有安全边界约束（Origin 403、未知端点 404）；
- 恢复产出新 revision、内容与快照一致、旧 revision 409 且不改草稿；
- 其他本体的快照被拒绝；恢复前备份已落库；项目草稿不受影响。

隔离规则（沿用 test_project_api_roundtrip.py）：
- 纯 python3 标准库（无 pytest）；临时数据根 + 独立端口的子进程服务；
- 直接写库时显式设置 WIZ_DATABASE_URL 指向临时库，避免连到真实数据库；
- 不读写真实业务数据：快照由测试自身导出后回灌，不依赖仓库已发布版本。

运行：python3 tests/test_export_restore_http.py
"""
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PORT = 18811
ORIGIN = f'http://127.0.0.1:{PORT}'
BASE = f'http://127.0.0.1:{PORT}'
SNAPSHOT_NAME = 'test-snapshot-a.zip'

TMP = Path(tempfile.mkdtemp(prefix='wiz_export_restore_'))
os.environ['WIZ_WORKBENCH_ROOT'] = str(TMP)

PROC = None
PASSED = []


def check(cond, message, actual=None, expected=None):
    if cond:
        return
    print(f'\n[失败] {message}')
    if expected is not None:
        print('  预期: ' + json.dumps(expected, ensure_ascii=False, default=str)[:2000])
    if actual is not None:
        print('  实际: ' + json.dumps(actual, ensure_ascii=False, default=str)[:2000])
    shutdown()
    print(f'\n（临时根保留供排查：{TMP}）')
    sys.exit(1)


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
        PROC = None


# --- HTTP ---------------------------------------------------------------------

def request(method, path, payload=None, origin=ORIGIN, raw=False):
    data = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    headers = {'Content-Type': 'application/json'}
    if method == 'POST':
        headers['Origin'] = origin
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read()
            return (resp.status, body) if raw else (resp.status, json.loads(body.decode()))
    except urllib.error.HTTPError as exc:
        body = exc.read()
        if raw:
            return exc.code, body
        try:
            return exc.code, json.loads(body.decode())
        except ValueError:
            return exc.code, body.decode()


# --- 直连临时库（仅用于回灌测试快照 / 读取备份，不碰真实库）-----------------------

def db_call(fn):
    """在 WIZ_DATABASE_URL 指向临时库的前提下执行一段存储层代码。"""
    import os as _os
    _os.environ['WIZ_DATABASE_URL'] = 'sqlite:///' + str(TMP / 'data' / 'workbench.sqlite3')
    sys.path.insert(0, str(REPO))
    try:
        from workbench import storage
        storage.ensure_ready()
        return fn()
    finally:
        _os.environ.pop('WIZ_DATABASE_URL', None)


def put_release_zip(identifier, name, blob):
    def body():
        from workbench.storage import assets as store, artifacts as artifact_store
        from workbench.storage.engine import write_tx, utcnow

        def _inner(conn):
            asset = store.get_asset(conn, 'model', identifier)
            assert asset is not None, f'本体 {identifier} 的资产未创建'
            artifact_store.put_artifact(conn, blob, artifact_store.PURPOSE_RELEASE_ZIP,
                                        legacy_name=name, owner_asset_uid=asset['asset_uid'],
                                        media_type='application/zip', now=utcnow())
        with write_tx() as tx:
            tx.run(_inner)
    db_call(body)


def count_restore_backups(identifier):
    def body():
        from workbench.storage import assets as store, artifacts as artifact_store
        from workbench.storage.engine import read_connection
        with read_connection() as conn:
            asset = store.get_asset(conn, 'model', identifier)
            if asset is None:
                return 0
            rows = artifact_store.artifact_meta_list(conn, asset['asset_uid'],
                                                     artifact_store.PURPOSE_RESTORE_BACKUP) or []
        return len(rows)
    return db_call(body)


# --- 主流程 -------------------------------------------------------------------

def start_server():
    global PROC
    probe = socket.socket()
    try:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(('127.0.0.1', PORT))
    except OSError as exc:
        print(f'[失败] 端口 {PORT} 已被占用：{exc}')
        sys.exit(1)
    finally:
        probe.close()

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
    check(ready, '服务未在 20 秒内就绪', actual='未就绪', expected=200)
    ok('0', f'隔离服务已就绪 WIZ_WORKBENCH_ROOT={TMP} 端口 {PORT}')


def note_of(state):
    return (state.get('workflow', {}).get('release', {}) or {}).get('note', '')


def main():
    start_server()

    # 1) 基线：草稿 A（随后导出为快照）
    status, payload = request('GET', '/api/state?ontology=storage')
    check(status == 200, 'GET /api/state 应 200', actual=(status, payload), expected=200)
    state, revision = payload['state'], payload['revision']
    state.setdefault('workflow', {}).setdefault('release', {})['note'] = 'SNAPSHOT-A'
    status, saved = request('POST', '/api/save', {'state': state, 'revision': revision})
    check(status == 200 and saved.get('revision'), '保存草稿 A 应 200 并返回新 revision',
          actual=(status, saved), expected=200)
    revision_a = saved['revision']
    state, revision = payload['state'], revision_a
    ok('1', f'草稿 A 已保存 revision={revision_a[:12]}…')

    # 2) 导出：真实 ZIP（修复前此处恒定 404）
    status, blob = request('POST', '/api/export', {'state': state}, raw=True)
    check(status == 200, 'POST /api/export 应 200（修复前为 404）',
          actual=(status, blob[:200]), expected=200)
    check(blob[:2] == b'PK', '导出体应为 ZIP（PK 魔数）', actual=blob[:4].hex(), expected='504b…')
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        names = set(archive.namelist())
        check('manifest.json' in names, '导出 ZIP 应含 manifest.json', actual=sorted(names),
              expected='manifest.json')
        manifest = json.loads(archive.read('manifest.json'))
        check(manifest.get('ontology', {}).get('id') == 'storage',
              'manifest 应记录来源本体 storage', actual=manifest.get('ontology'), expected='storage')
        declared = [p for p in (manifest.get('files') or {}).values() if p]
        check(declared and all(p in names for p in declared),
              'manifest 声明的模型文件应全部存在', actual=sorted(names), expected=sorted(declared))
        check('ontology' in (manifest.get('files') or {}),
              'manifest files 应声明 ontology 定义文件', actual=manifest.get('files'),
              expected='含 ontology 键')
    ok('2', f'导出 ZIP {len(blob)} 字节，含 {len(names)} 个条目')

    # 3) 导出是只读操作：不改草稿 revision
    status, after = request('GET', '/api/state?ontology=storage')
    check(status == 200 and after['revision'] == revision_a,
          '导出不得修改草稿 revision', actual=(status, after['revision']), expected=revision_a)
    ok('3', '导出后草稿 revision 未变')

    # 4) 安全边界仍在导出路径上生效
    status, body = request('POST', '/api/export', {'state': state}, origin='http://evil.example')
    check(status == 403, '导出：非法 Origin 应 403', actual=(status, body), expected=403)
    status, body = request('POST', '/api/not-exist-endpoint', {'state': state})
    check(status == 404, '未知端点应 404', actual=(status, body), expected=404)
    ok('4', '导出仍受 Origin 校验；未知端点仍 404')

    # 5) 回灌快照（测试自建，不依赖仓库已发布版本），再写一份不同的草稿 B
    put_release_zip('storage', SNAPSHOT_NAME, blob)
    status, listed = request('GET', '/api/releases?ontology=storage')
    check(status == 200 and SNAPSHOT_NAME in (listed or []),
          'GET /api/releases 应列出测试快照', actual=(status, listed), expected=[SNAPSHOT_NAME])
    state_b = json.loads(json.dumps(payload['state']))
    state_b.setdefault('workflow', {}).setdefault('release', {})['note'] = 'DRAFT-B'
    status, saved = request('POST', '/api/save', {'state': state_b, 'revision': revision_a})
    check(status == 200, '保存草稿 B 应 200', actual=(status, saved), expected=200)
    revision_b = saved['revision']
    ok('5', f'快照已回灌，草稿 B 已保存 revision={revision_b[:12]}…')

    # 6) 旧 revision 恢复 → 409，且不改草稿
    status, conflict = request('POST', '/api/restore',
                               {'state': state_b, 'revision': revision_a, 'release': SNAPSHOT_NAME})
    check(status == 409, '旧 revision 恢复应 409', actual=(status, conflict), expected=409)
    check(conflict.get('currentRevision') == revision_b,
          '409 应带服务端最新 currentRevision', actual=conflict.get('currentRevision'),
          expected=revision_b)
    status, current = request('GET', '/api/state?ontology=storage')
    check(note_of(current['state']) == 'DRAFT-B', '409 后草稿不得被改动',
          actual=note_of(current['state']), expected='DRAFT-B')
    ok('6', '旧 revision 恢复返回 409 且草稿保持 DRAFT-B')

    # 7) 正常恢复（修复前此处 NameError → 400）
    backups_before = count_restore_backups('storage')
    status, restored = request('POST', '/api/restore',
                               {'state': state_b, 'revision': revision_b, 'release': SNAPSHOT_NAME})
    check(status == 200, 'POST /api/restore 应 200（修复前 NameError → 400）',
          actual=(status, restored), expected=200)
    check(bool(restored.get('revision')) and restored['revision'] != revision_b,
          '恢复应产生新的 revision', actual=restored.get('revision'), expected='!= ' + revision_b[:12])
    check(note_of(restored.get('state', {})) == 'SNAPSHOT-A',
          '响应 state 应与快照内容一致', actual=note_of(restored.get('state', {})),
          expected='SNAPSHOT-A')
    status, current = request('GET', '/api/state?ontology=storage')
    check(note_of(current['state']) == 'SNAPSHOT-A' and current['revision'] == restored['revision'],
          '服务端草稿应等于快照，且 revision 与响应一致',
          actual=(note_of(current['state']), current['revision']),
          expected=('SNAPSHOT-A', restored['revision']))
    backups_after = count_restore_backups('storage')
    check(backups_after == backups_before + 1, '恢复前备份应已落库',
          actual=(backups_before, backups_after), expected='+1')
    ok('7', f'恢复成功：内容回到 SNAPSHOT-A，新 revision={restored["revision"][:12]}…，备份 {backups_after} 份')

    # 8) 跨本体快照应被拒绝
    status, created = request('POST', '/api/ontologies', {'name': '恢复隔离测试本体'})
    check(status == 201 and created.get('id'), '创建独立本体应 201', actual=(status, created),
          expected=201)
    other = created['id']
    status, other_payload = request('GET', f'/api/state?ontology={other}')
    check(status == 200, f'GET /api/state?ontology={other} 应 200', actual=(status, other_payload),
          expected=200)
    status, rejected = request('POST', '/api/restore',
                               {'state': other_payload['state'], 'revision': other_payload['revision'],
                                'release': SNAPSHOT_NAME})
    check(status in (400, 404, 422), '跨本体恢复快照应被拒绝',
          actual=(status, rejected), expected='4xx')
    rejected_status = status
    status, other_after = request('GET', f'/api/state?ontology={other}')
    check(other_after['revision'] == other_payload['revision'],
          '被拒绝后目标本体草稿不得变更', actual=other_after['revision'],
          expected=other_payload['revision'])
    ok('8', f'跨本体恢复被拒（{rejected_status}），目标本体草稿未变')

    shutdown()
    print(f'\n全部通过（{len(PASSED)} 步）：导出可用、恢复可用、边界与隔离符合预期。')
    print(f'临时根：{TMP}')


if __name__ == '__main__':
    try:
        main()
    finally:
        shutdown()
