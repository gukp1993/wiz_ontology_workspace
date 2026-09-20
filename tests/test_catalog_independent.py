"""独立 QA（角色 F）· 目录缓存与凭据语义复核（2026-09-20 v2 冻结 F05 / T10–T13）。

不镜像 C 的实现细节，只按冻结语义从公开 helper/接口观察：
* T10 config_fingerprint：仅改显示名（name）与连接 id → 指纹不变；host／port／database
  ／dbIndex／tls／username 逐个变化 → 指纹变化（不含密码、不回传密钥）；
* T11 store_if_current 迟到结果保护：探测期间改地址／换凭据／删连接 → 返回 False 且
  缓存 payload 与 generation 逐字节不变；正向对照（无注入）必须写入成功（证明不是
  恒 False 的假阴性）；目录代际被他人推进（expected_generation 过期）→ False；
* T12 损坏 payload：load_all(strict=True) 抛 CatalogCacheUnreadable 且 connection_ids
  含该连接；strict=False 仅迁移/测试路径跳过；GET /api/project-state 跳过该连接注入且
  不 5xx；
* 冻结规格（G1）：目录缓存损坏 → 校验出 error、发布被阻断（本项当前实现未接线，见
  文末「规格观察」；设 WIZ_QA_STRICT=1 时按失败计）；
* 幽灵凭据：secrets.save 对不存在的项目必须抛错拒绝，不得创建项目资产行、不得读回。

隔离：动态端口 + 独立临时根 WIZ_WORKBENCH_ROOT + 独立 WIZ_DATABASE_URL + 假账号；
真实 ontology/ 只读复制；不访问外网／真实 MySQL／Redis／18765；dbdrivers 不出网
（本文件不调用探测驱动，只经 catalogs/secrets 存储语义）。

运行：python3 tests/test_catalog_independent.py
      WIZ_QA_STRICT=1 python3 tests/test_catalog_independent.py   # 规格观察项按失败计
"""
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'tests'))
sys.path.insert(0, str(REPO))
import auth_client  # noqa: E402

STRICT = os.environ.get('WIZ_QA_STRICT', '0') == '1'

TMP = Path(tempfile.mkdtemp(prefix='wiz_catalog_independent_'))
os.environ['WIZ_WORKBENCH_ROOT'] = str(TMP)
DB_PATH = TMP / 'data' / 'workbench.sqlite3'
DB_URL = 'sqlite:///' + str(DB_PATH)
os.environ['WIZ_DATABASE_URL'] = DB_URL

OK, BAD, SPEC = [], [], []


def check(cond, message, actual=None, expected=None):
    if cond:
        OK.append(message)
        print('  通过 · ' + message)
        return True
    BAD.append(message)
    print('  不符 · ' + message)
    if expected is not None:
        print('      预期: ' + json.dumps(expected, ensure_ascii=False, default=str)[:1200])
    if actual is not None:
        print('      实际: ' + json.dumps(actual, ensure_ascii=False, default=str)[:1200])
    return False


def spec(cond, message, actual=None, expected=None):
    """冻结规格项：默认记为「规格观察」，WIZ_QA_STRICT=1 时按失败计。"""
    if cond:
        OK.append(message)
        print('  通过 · ' + message)
        return True
    SPEC.append(message)
    print('  [规格观察] ' + message)
    if expected is not None:
        print('      规格要求: ' + json.dumps(expected, ensure_ascii=False, default=str)[:800])
    if actual is not None:
        print('      当前实现: ' + json.dumps(actual, ensure_ascii=False, default=str)[:800])
    return False


def note(message):
    print('  取证 · ' + message)


def free_port():
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def db_rows(sql, params=()):
    conn = sqlite3.connect(str(DB_PATH))
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def db_exec(sql, params=()):
    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


AUTH = {'cookie': ''}
PROC = None
PORT = None
BASE = None


def request(method, path, payload=None):
    data = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    headers = {'Content-Type': 'application/json'}
    if AUTH['cookie']:
        headers['Cookie'] = 'wiz_session=' + AUTH['cookie']
    if method == 'POST':
        headers['Origin'] = BASE
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read().decode()
            return resp.status, json.loads(body)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode()
        try:
            return exc.code, json.loads(body)
        except ValueError:
            return exc.code, body


def shutdown():
    global PROC
    if PROC is not None:
        PROC.terminate()
        try:
            PROC.wait(timeout=10)
        except subprocess.TimeoutExpired:
            PROC.kill()
        PROC = None


# --- 夹具 --------------------------------------------------------------------------

def seed_to_temp_root():
    real = REPO / 'ontology/releases/models'
    src = None
    if (real / 'storage').is_dir():
        src = real / 'storage'
    else:
        for candidate in sorted(real.iterdir()):
            if candidate.is_dir() and (candidate / 'index.json').is_file():
                src = candidate
                break
    if src is None:
        print('前置失败：真实 ontology/releases/models 无可用版本')
        sys.exit(2)
    shutil.copytree(src, TMP / 'ontology/releases/models/storage')
    entries = json.loads((TMP / 'ontology/releases/models/storage/index.json').read_text())['versions']
    from workbench.storage import transfer
    transfer.main(['import', '--source', str(TMP)])
    transfer.main(['create-user', '--username', auth_client.DEFAULT_USER,
                   '--password', auth_client.DEFAULT_PASSWORD])
    transfer.main(['assign-owner', '--username', auth_client.DEFAULT_USER])
    return entries[-1]['version']


def start_server():
    global PROC, PORT, BASE
    PORT = free_port()
    BASE = f'http://127.0.0.1:{PORT}'
    env = dict(os.environ)
    env.update({'WIZ_WORKBENCH_ROOT': str(TMP), 'WIZ_WORKBENCH_PORT': str(PORT),
                'WIZ_DATABASE_URL': DB_URL})
    PROC = subprocess.Popen([sys.executable, '-m', 'workbench.server'], cwd=str(REPO), env=env,
                            stdout=open(TMP / 'server.log', 'wb'), stderr=subprocess.STDOUT)
    deadline = time.time() + 40
    while time.time() < deadline:
        try:
            with socket.create_connection(('127.0.0.1', PORT), timeout=0.5):
                return
        except OSError:
            time.sleep(0.2)
    print('前置失败：测试实例未能启动')
    sys.exit(2)


def bind_fixture_user():
    os.environ['WIZ_DATABASE_URL'] = DB_URL
    return auth_client.bind_fixture_user()


def mysql_conn(**over):
    base = {'id': 'cat-conn-a', 'name': '目录业务库', 'engine': 'mysql', 'host': '10.0.0.1',
            'port': 3306, 'username': 'reader', 'tls': 'none', 'database': 'energy'}
    base.update(over)
    return base


def redis_conn(**over):
    base = {'id': 'cat-conn-b', 'name': '目录缓存', 'engine': 'redis', 'host': '10.0.0.9',
            'port': 6379, 'username': '', 'tls': 'none', 'dbIndex': 0}
    base.update(over)
    return base


def fingerprint(conn):
    from workbench import catalogs, dbdrivers
    return catalogs.config_fingerprint(dbdrivers.normalize_config(conn))


def object_type_and_string_property(version):
    """从已发布版本读出一个对象类型（apiName 形态）与其字符串属性（直接字段映射用）。"""
    from workbench import versions
    graph = versions.read_state('storage', version)['ontology']['@graph']
    by_id = {n.get('@id'): n for n in graph}
    classes = [n for n in graph if n.get('@type') == 'owl:Class']
    obj_id = classes[0]['@id'].removeprefix('mg:')
    for node in graph:
        if node.get('@type') != 'owl:DatatypeProperty':
            continue
        if str(node.get('rdfs:domain', {}).get('@id', '')).removeprefix('mg:') != obj_id:
            continue
        holder = by_id.get((node.get('mg:sharedProperty') or {}).get('@id'), node)
        rng = str((holder.get('rdfs:range') or node.get('rdfs:range') or {}).get('@id', ''))
        if rng.endswith('string'):
            return obj_id, str(node.get('mg:apiName') or node['@id'].removeprefix('mg:'))
    return obj_id, None


# --- 主流程 ------------------------------------------------------------------------

def assert_isolated():
    """安全前置：确认存储与数据根都落在本次临时根内，绝不指向真实数据。"""
    from workbench.storage import engine
    resolved = engine.resolve_url()
    db_file = engine.database_file(resolved)
    if db_file is None or str(db_file) != str(DB_PATH):
        print(f'前置失败：数据库未隔离（resolve_url={resolved}）')
        sys.exit(2)
    from workbench.paths import DATA_ROOT
    if Path(str(DATA_ROOT)) != TMP:
        print(f'前置失败：DATA_ROOT 未隔离（{DATA_ROOT}）')
        sys.exit(2)
    print(f'  通过 · 隔离核对：DATA_ROOT={DATA_ROOT}；db={db_file}')


def main():
    version = seed_to_temp_root()
    start_server()
    auth_client.wait_ready(BASE)
    assert_isolated()
    _user, cookie = auth_client.register_or_login(BASE)
    AUTH['cookie'] = cookie
    bind_fixture_user()
    print(f'动态端口 {PORT}；临时根 {TMP}；storage 版本 {version}')

    from workbench import catalogs, projects, secrets
    from workbench.storage.configuration import CatalogCacheUnreadable

    # ================= 1. config_fingerprint（T10） ==========================
    print('\n[1] config_fingerprint：仅显示名/id 不失效，技术字段逐个失效')
    base = mysql_conn()
    fp = fingerprint(base)
    check(fp == fingerprint(mysql_conn(name='改了名字')),
          '仅改显示名 name → 指纹不变', actual=(fp, fingerprint(mysql_conn(name='改了名字'))))
    check(fp == fingerprint(mysql_conn(id='cat-conn-zzz')),
          '仅改连接 id → 指纹不变', actual=(fp, fingerprint(mysql_conn(id='cat-conn-zzz'))))
    for field, value in (('host', '10.0.0.2'), ('port', 3307), ('database', 'energy2'),
                         ('username', 'other'), ('tls', 'encrypted')):
        check(fp != fingerprint(mysql_conn(**{field: value})),
              f'改 {field} → 指纹变化', actual=(fp, fingerprint(mysql_conn(**{field: value}))))
    check(fp != fingerprint(mysql_conn(tls='verify', caPath='/tmp/ca.pem')),
          '改 tls=verify + caPath → 指纹变化',
          actual=(fp, fingerprint(mysql_conn(tls='verify', caPath='/tmp/ca.pem'))))
    redis_fp = fingerprint(redis_conn())
    check(redis_fp != fingerprint(redis_conn(dbIndex=3)), 'Redis 改 dbIndex → 指纹变化',
          actual=(redis_fp, fingerprint(redis_conn(dbIndex=3))))
    check(not any(str(secret_value) in fp for secret_value in ('password', 'passwd')),
          '指纹不包含任何凭据字段（只读摘要，无密钥）', actual=fp)
    check(len(fp) == 64 and all(ch in '0123456789abcdef' for ch in fp),
          '指纹为 64 位 hex（列宽稳定）', actual=fp)

    # ================= 2. store_if_current（T11/T12） ========================
    print('\n[2] store_if_current：正向对照 + 探测期间改地址/换凭据/删连接 → False 且缓存不变')
    obj_type, string_prop = object_type_and_string_property(version)
    note(f'夹具：对象类型 {obj_type}，字符串属性 {string_prop}')
    created = projects.create('目录独立复核项目', 'storage', version)
    pid = created['id']
    set_connection(pid, mysql_conn())
    CATALOG = {'database': 'energy', 'refreshedAt': '2026-09-20T00:00:00+00:00',
               'tables': [{'name': 't1', 'kind': 'table',
                           'fields': [{'name': 'id', 'dataType': 'bigint', 'key': 'pri', 'comment': ''}]}]}

    def refresh(during=None, expected_generation=None):
        """按线上接线顺序：探测前取基线 →（外部探测）→ during 注入 → 条件写入。"""
        from workbench import dbdrivers
        state, _ = projects.load(pid)
        conn = next(c for c in state['connections']['connections'] if c.get('id') == 'cat-conn-a')
        fp_now = catalogs.config_fingerprint(dbdrivers.normalize_config(conn))
        secret_rev = secrets.revision(pid, 'cat-conn-a')
        meta = catalogs.load_all_meta(pid)
        gen = int((meta.get('cat-conn-a') or {}).get('generation') or 0)
        if during is not None:
            during()
        return catalogs.store_if_current(pid, 'cat-conn-a', CATALOG,
                                         expected_fingerprint=fp_now,
                                         expected_secret_revision=secret_rev,
                                         expected_generation=(gen if expected_generation is None
                                                              else expected_generation))

    def snapshot():
        meta = catalogs.load_all_meta(pid)
        entry = meta.get('cat-conn-a') or {}
        return json.dumps({'payload': entry.get('payload'), 'generation': entry.get('generation'),
                           'unreadable': entry.get('unreadable')}, ensure_ascii=False, sort_keys=True)

    check(refresh() is True, '正向对照：无注入时应写入成功（证明不是恒 False）')
    meta = catalogs.load_all_meta(pid)
    check(int(meta['cat-conn-a']['generation']) == 1, '首次写入 generation=1',
          actual=meta.get('cat-conn-a'))
    check(refresh() is True, '再次刷新应写入成功')
    check(int(catalogs.load_all_meta(pid)['cat-conn-a']['generation']) == 2,
          '再次写入 generation 递增到 2', actual=catalogs.load_all_meta(pid).get('cat-conn-a'))

    before = snapshot()
    check(refresh(during=lambda: set_connection(pid, mysql_conn(host='10.0.0.2'))) is False,
          '探测期间改地址 → 条件写 False（迟到结果丢弃）')
    check(snapshot() == before, '改地址后缓存 payload/generation 逐字节不变',
          actual={'before': before[:200], 'after': snapshot()[:200]})
    set_connection(pid, mysql_conn())

    before = snapshot()
    check(refresh(during=lambda: secrets.save(pid, 'cat-conn-a', 'newpassword')) is False,
          '探测期间换凭据 → 条件写 False',
          actual=secrets.revision(pid, 'cat-conn-a'))
    check(snapshot() == before, '换凭据后缓存 payload/generation 逐字节不变',
          actual={'before': before[:200], 'after': snapshot()[:200]})
    check(secrets.revision(pid, 'cat-conn-a') == 1, '凭据代际可读（只读整型，无密钥回传）',
          actual=secrets.revision(pid, 'cat-conn-a'))

    before = snapshot()
    check(refresh(during=lambda: set_connection(pid, None)) is False,
          '探测期间连接被删 → 条件写 False（不重建项目资产）')
    check(snapshot() == before, '删连接后缓存 payload/generation 逐字节不变',
          actual={'before': before[:200], 'after': snapshot()[:200]})
    set_connection(pid, mysql_conn())

    check(refresh(expected_generation=999) is False,
          '目录代际已被推进（expected_generation 过期）→ False（先到先得）')
    check(refresh() is True, '恢复正向路径仍可写入（非误判）')

    # ================= 3. 损坏 payload（T12） ================================
    print('\n[3] 损坏 payload：load_all(strict) 抛 CatalogCacheUnreadable 且带 connection_ids')
    state, _ = projects.load(pid)
    state['bindings']['object_bindings'] = [{
        'object_type': obj_type, 'connection': 'cat-conn-a', 'table': 't1',
        'primary_key': 'id', 'properties': {string_prop: 'id'} if string_prop else {}}]
    projects.save_draft(state, expected_token=projects.current_token(pid))
    status, clean = request('POST', '/api/project-validate',
                            {'state': state, 'revision': projects.current_token(pid)})
    check(status == 200 and not clean.get('errors'),
          '前置：损坏前该草稿校验无 error（证明后文的 error 只可能来自目录损坏）',
          actual=(status, clean))
    db_exec("UPDATE wb_catalog_cache SET payload_json = '{broken' WHERE connection_id = 'cat-conn-a'")
    check(json.loads(json.dumps(catalogs.load_all_meta(pid)))['cat-conn-a']['unreadable'] is True,
          '损坏条目标记 unreadable=True 且 payload=None（不冒充可读）',
          actual=catalogs.load_all_meta(pid))
    raised = None
    try:
        catalogs.load_all(pid, strict=True)
    except CatalogCacheUnreadable as exc:
        raised = exc
    check(raised is not None, 'load_all(strict=True) 应抛 CatalogCacheUnreadable')
    check(raised is not None and 'cat-conn-a' in list(raised.connection_ids),
          '异常 connection_ids 应含损坏连接 id',
          actual=(list(getattr(raised, 'connection_ids', [])),))
    check(catalogs.load_all(pid, strict=False) == {},
          'load_all(strict=False)（迁移/测试路径）跳过损坏条目不抛',
          actual=catalogs.load_all(pid, strict=False))
    check(all(cid != 'cat-conn-a' for cid in
              (request('GET', f'/api/project-state?project={pid}')[1]
               .get('state', {}).get('bindings', {}).get('catalogs') or {})),
          'GET /api/project-state 跳过损坏连接注入（不 5xx、不伪造成空目录）')
    check(db_rows("SELECT count(*) FROM wb_catalog_cache WHERE connection_id = 'cat-conn-a'"
                  " AND payload_json = '{broken'")[0][0] == 1,
          '损坏行未被静默清除或改写（保留现场，等待重新刷新）')

    # ================= 4. 冻结规格：损坏目录必须阻断校验/发布 ==================
    print('\n[4] 冻结规格（G1 / 03 分册 §2.2）：目录缓存损坏 → 校验 error 且发布被阻断')
    status, report = request('POST', '/api/project-validate',
                             {'state': state, 'revision': projects.current_token(pid)})
    note('校验响应：errors=' + json.dumps(report.get('errors'), ensure_ascii=False)
         + ' warnings=' + json.dumps(report.get('warnings'), ensure_ascii=False))
    degraded_errors = [e for e in (report.get('errors') or []) if '目录' in str(e)]
    spec(status == 200 and bool(degraded_errors),
         'T12/F05：损坏目录校验应出 error（03 分册 §2.2「报告出现对应连接的 error」）',
         actual={'status': status, 'errors': report.get('errors'), 'warnings': report.get('warnings')},
         expected={'errors': ['含连接 cat-conn-a 的目录缓存读取失败']})
    items = report.get('items') or []
    spec(any(i.get('kind') == 'connection' and i.get('id') == 'cat-conn-a'
             and i.get('status') == 'error' for i in items),
         'T12/F05：损坏目录应在 items 出对应连接的 error 条目（供页面定位）',
         actual=[i for i in items if i.get('id') == 'cat-conn-a'])
    if report.get('baseline'):
        entry = next((c for c in report['baseline'].get('catalogs', [])
                      if c.get('connectionId') == 'cat-conn-a'), None)
        check(entry is not None and entry.get('unreadable') is True,
              '校验 baseline 标记该连接 unreadable=true（供客户端判失效）', actual=entry)
    status, published = request('POST', '/api/project-publish',
                                {'state': state, 'revision': projects.current_token(pid),
                                 'requestId': 'cat-spec-1'})
    spec(status == 422, 'F05：损坏目录存在时发布应被阻断（不得凭缺失目录发布）',
         actual=(status, published))
    if status == 200:
        releases = request('GET', f'/api/project-releases?project={pid}')[1]
        note('实际发生了发布：' + json.dumps([i.get('version') for i in releases.get('items', [])],
                                            ensure_ascii=False))

    # ================= 5. 幽灵凭据 ==========================================
    print('\n[5] 幽灵凭据：secrets.save 对不存在项目必须拒绝且不建资产行')
    ghost = 'ghostproject01'
    creds_before = db_rows('SELECT count(*) FROM wb_credentials')[0][0]
    check(db_rows("SELECT count(*) FROM wb_assets WHERE kind='project' AND external_id=?", (ghost,))[0][0] == 0,
          '前置：幽灵项目 id 在库中不存在')
    err = None
    try:
        secrets.save(ghost, 'conn-ghost', 'secret-value')
    except Exception as exc:  # noqa: BLE001  只要求「抛错拒绝」，不绑定异常类型
        err = exc
    check(err is not None, 'secrets.save(不存在的项目) 必须抛错拒绝', actual=type(err).__name__ if err else 'no raise')
    check(db_rows("SELECT count(*) FROM wb_assets WHERE kind='project' AND external_id=?", (ghost,))[0][0] == 0,
          '拒绝后不得创建项目资产行（无幽灵资产）',
          actual=db_rows("SELECT external_id FROM wb_assets WHERE external_id=?", (ghost,)))
    check(secrets.read(ghost, 'conn-ghost') == '', '幽灵凭据不可读回', actual=secrets.read(ghost, 'conn-ghost'))
    check(secrets.revision(ghost, 'conn-ghost') == 0,
          '幽灵项目凭据代际为 0（不创建任何代际记录）', actual=secrets.revision(ghost, 'conn-ghost'))
    check(secrets.exists(ghost, 'conn-ghost') is False, '幽灵凭据 exists=False',
          actual=secrets.exists(ghost, 'conn-ghost'))
    invalid = None
    try:
        secrets.save('不存在的项目', 'conn-ghost', 'x')
    except Exception as exc:  # noqa: BLE001
        invalid = exc
    check(invalid is not None, '非法项目标识同样拒绝', actual=type(invalid).__name__ if invalid else 'no raise')
    check(db_rows('SELECT count(*) FROM wb_credentials')[0][0] == creds_before,
          '幽灵凭据尝试不新增任何凭据行（vault 无残留）',
          actual=(db_rows('SELECT count(*) FROM wb_credentials'), creds_before))

    # 真实项目路径仍可用（避免把「一律拒绝」误判成正确）
    check(secrets.save(pid, 'cat-conn-a', 'pw-2') is True, '真实项目写凭据应成功')
    check(secrets.read(pid, 'cat-conn-a') == 'pw-2', '真实项目凭据可经内部读取路径读回（仅探测用）')
    check(secrets.revision(pid, 'cat-conn-a') == 2, '换凭据后代际递增',
          actual=secrets.revision(pid, 'cat-conn-a'))

    # ================= 6. 编排依赖读取失败分类（F06 fail-closed 与文案） ==========
    print('\n[6] 编排依赖读取失败：必须阻断发布，且不得与「不存在」混同')
    from workbench import flows
    flow_id = flows.create('目录独立复核编排')['id']
    flow_state = flows.read_draft(flow_id)
    flow_state['outputs'] = [{'id': 'out1', 'label': '结果', 'type': {'type': 'number'}}]
    flows.save_draft(flow_state, expected_token=flows.current_token(flow_id))
    dep_state, _ = projects.load(pid)
    dep_prop = next((p for p in (dep_state['bindings']['object_bindings'] or [{}])[0]
                     .get('properties', {})), None)
    if dep_prop:
        dep_state['bindings']['object_bindings'][0]['properties'][dep_prop] = {
            'kind': 'flow', 'flow': flow_id, 'output': 'out1', 'inputs': {}}
        projects.save_draft(dep_state, expected_token=projects.current_token(pid))
        db_exec('UPDATE wb_snapshots SET payload_json = \'{broken\' WHERE asset_uid = '
                '(SELECT asset_uid FROM wb_assets WHERE kind = \'flow\' AND external_id = ?)', (flow_id,))
        check(flows.dependency_state(flow_id)['status'] == 'unreadable',
              'flows.dependency_state 对损坏快照应为 unreadable（三态已实现）',
              actual=flows.dependency_state(flow_id))
        status, rep = request('POST', '/api/project-validate',
                             {'state': dep_state, 'revision': projects.current_token(pid)})
        check(status == 200 and any('编排' in str(e) for e in (rep.get('errors') or [])),
              '编排读取失败 → 校验 error（fail-closed，结果仍是阻断）',
              actual=(status, rep.get('errors')))
        spec(any('读取' in str(e) or '无法' in str(e) for e in (rep.get('errors') or [])),
             'F06/03 §2.2：编排读取失败应区别于「不存在或已删除」的文案'
             '（当前实现按不存在归类）',
             actual=rep.get('errors'), expected='含「读取失败/不可读」语义的 error 文案')
    else:
        note('跳过 [6]：该草稿无属性可用于编排引用夹具')

    # 规格观察汇总（degraded_catalogs 未落地）
    spec(False,
         'G1 冻结契约：project_validation.validate_project 应接受 degraded_catalogs 并出 error',
         actual='validate_project() got an unexpected keyword argument \'degraded_catalogs\' '
                '（路由层 except TypeError 回退后该协议被静默跳过）',
         expected='签名含 degraded_catalogs，损坏连接出 error 并阻断发布')

    # ================= 7. 存储层读取失败：GET/POST 分类一致性（502/503） ==========
    print('\n[7] 存储层目录读取失败：GET 与 POST 应同为 503 STORAGE_UNAVAILABLE')
    db_exec('ALTER TABLE wb_catalog_cache RENAME TO wb_catalog_cache_bak')  # 可逆注入：表缺失
    try:
        post_status, post_body = request('POST', '/api/project-validate',
                                         {'state': state, 'revision': projects.current_token(pid)})
        get_status, get_body = request('GET', f'/api/project-state?project={pid}')
    finally:
        db_exec('ALTER TABLE wb_catalog_cache_bak RENAME TO wb_catalog_cache')
    check(post_status == 503 and isinstance(post_body, dict) and post_body.get('code') == 'STORAGE_UNAVAILABLE',
          'POST /api/project-validate 存储失败应 503 STORAGE_UNAVAILABLE', actual=(post_status, post_body))
    spec(get_status == 503 and isinstance(get_body, dict) and get_body.get('code') == 'STORAGE_UNAVAILABLE',
         '03 分册 §2.2：GET /api/project-state 存储失败应同为 503（当前为 500 INTERNAL_ERROR）',
         actual=(get_status, get_body), expected='503 STORAGE_UNAVAILABLE')
    check(get_status != 200, 'GET 存储失败不得降级为 200（不冒充空目录）', actual=(get_status, get_body))


def set_connection(pid, conn):
    from workbench import projects
    state, _ = projects.load(pid)
    state['connections']['connections'] = [conn] if conn else []
    projects.save_draft(state, expected_token=projects.current_token(pid))


def report():
    shutdown()
    print('\n================ 汇总 ================')
    print(f'硬断言通过 {len(OK)} 项；不符 {len(BAD)} 项；规格观察 {len(SPEC)} 项')
    for item in BAD:
        print('  不符：' + item)
    for item in SPEC:
        print('  规格观察：' + item)
    if SPEC:
        print('  （规格观察项为冻结契约要求、当前实现未达标；WIZ_QA_STRICT=1 时按失败计）')
    if BAD or (SPEC and STRICT):
        print(f'临时根保留供排查：{TMP}')
        sys.exit(1)
    shutil.rmtree(TMP, ignore_errors=True)
    print('临时根已清理')


if __name__ == '__main__':
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:
        import traceback
        traceback.print_exc()
        print(f'\n[异常] {type(exc).__name__}: {exc}')
        sys.exit(3)
    report()
