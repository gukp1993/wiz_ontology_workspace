"""独立 QA（角色 F）· 对抗性复核项目发布守卫（2026-09-20 v2 冻结协议）。

只从公开接口/行为出发复核，不镜像实现细节：
* 幂等（T19）：并发同 requestId 同内容（threading.Barrier 真并发，无 sleep 碰运气）、
  成功后带旧 revision 重试、语义相同但 dict 键序不同、数组顺序不同的敏感面；
* 幂等回执与版本一致：回放响应必须与首次成功响应逐字段一致（不是重新生成）；
* 保存边界（T14）：只删被引用连接 → 422 + 零写入（project-state 逐字节不变）、
  连接与引用同批移除放行、引用藏在 implementations[].connection 与
  property inlineSql.connection 时同样被拦；
* 依赖重验（T18）：受控注入在两次依赖读取之间改变编排 head → 409
  reason=DEPENDENCY_CHANGED、project-releases 不新增、草稿 revision 不推进；
* 跨账号（T21）：B 用 A 的 requestId 不得回放 A 的回执，也不得读到 A 的 version/revision。

隔离：动态端口（绑定 0 取空闲端口）+ 独立临时根 WIZ_WORKBENCH_ROOT +
独立 WIZ_DATABASE_URL + 假账号；真实 ontology/ 只读复制到临时根；不访问外网/真实
MySQL/Redis/18765。与 L 的 test_publish_guards.py 端口不同，可并行运行。

运行：python3 tests/test_publish_guards_adversarial.py
"""
import copy
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import unittest.mock as mock

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'tests'))
sys.path.insert(0, str(REPO))
import auth_client  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix='wiz_publish_adversarial_'))
os.environ['WIZ_WORKBENCH_ROOT'] = str(TMP)
DB_PATH = TMP / 'data' / 'workbench.sqlite3'
DB_URL = 'sqlite:///' + str(DB_PATH)
os.environ['WIZ_DATABASE_URL'] = DB_URL

PROC = None
PORT = None
BASE = None
OK, BAD = [], []


def free_port():
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


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


def note(message):
    print('  取证 · ' + message)


def shutdown():
    global PROC
    if PROC is not None:
        PROC.terminate()
        try:
            PROC.wait(timeout=10)
        except subprocess.TimeoutExpired:
            PROC.kill()
        PROC = None


AUTH = {'cookie': ''}


def request(method, path, payload=None, cookie=None, raw=False):
    data = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    headers = {'Content-Type': 'application/json'}
    token = cookie if cookie is not None else AUTH['cookie']
    if token:
        headers['Cookie'] = 'wiz_session=' + token
    if method == 'POST':
        headers['Origin'] = BASE
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read().decode()
            return resp.status, (body if raw else json.loads(body))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode()
        try:
            return exc.code, (body if raw else json.loads(body))
        except ValueError:
            return exc.code, body


def db_rows(sql, params=()):
    conn = sqlite3.connect(str(DB_PATH))
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


# --- 夹具：临时根 + 假账号 + 动态端口实例 ------------------------------------------

def seed_release():
    releases = TMP / 'ontology/releases/models/storage'
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
        print('前置失败：真实 ontology/releases/models 无可用版本可作种子')
        sys.exit(2)
    shutil.copytree(src, releases)  # 只读复制；真实目录零写入
    entries = json.loads((releases / 'index.json').read_text())['versions']
    return entries[-1]['version']


def import_seed():
    from workbench.storage import transfer
    code = transfer.main(['import', '--source', str(TMP)])
    check(code == 0, '前置：种子发布导入临时存储库', actual=code)
    transfer.main(['create-user', '--username', auth_client.DEFAULT_USER,
                   '--password', auth_client.DEFAULT_PASSWORD])
    transfer.main(['assign-owner', '--username', auth_client.DEFAULT_USER])


def start_server():
    global PROC, PORT, BASE
    PORT = free_port()
    BASE = f'http://127.0.0.1:{PORT}'
    env = dict(os.environ)
    env.update({'WIZ_WORKBENCH_ROOT': str(TMP), 'WIZ_WORKBENCH_PORT': str(PORT),
                'WIZ_DATABASE_URL': DB_URL})
    log = open(TMP / 'server.log', 'wb')
    PROC = subprocess.Popen([sys.executable, '-m', 'workbench.server'], cwd=str(REPO), env=env,
                            stdout=log, stderr=subprocess.STDOUT)
    deadline = time.time() + 40
    while time.time() < deadline:
        try:
            with socket.create_connection(('127.0.0.1', PORT), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.2)
    shutdown()
    print('前置失败：测试实例未能在 40 秒内启动（端口 %s）' % PORT)
    sys.exit(2)


def bind_fixture_user():
    """进程内直调域函数：指向同一测试库并绑定同一假账号（与 HTTP 会话同账号）。"""
    os.environ['WIZ_DATABASE_URL'] = DB_URL
    return auth_client.bind_fixture_user()


def scalar_property_and_type(version):
    """从已发布版本（JSON-LD 形态）读出一个 scalar 数值属性 apiName 与其对象类型。

    复用线上同一条读取路径（versions.read_state）；scalar 数值判定：解析
    mg:sharedProperty 指向的共享定义后的 rdfs:range 为数值且形态不是 timeSeries
    （时间序列属性不能绑单值编排输出，会先被校验拦下）。
    """
    bind_fixture_user()
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
        shape = holder.get('mg:valueShape') or node.get('mg:valueShape') or ''
        if str(shape) == 'timeSeries':
            continue
        rng = str((holder.get('rdfs:range') or node.get('rdfs:range') or {}).get('@id', '')).removeprefix('xsd:')
        if rng in ('double', 'decimal', 'integer', 'float'):
            return obj_id, str(node.get('mg:apiName') or node['@id'].removeprefix('mg:'))
    print('前置失败：未在已发布版本中找到 scalar 数值属性')
    sys.exit(2)


def write_catalog(project_id, connection_id, tables):
    bind_fixture_user()
    from workbench import catalogs
    catalogs.store(project_id, connection_id,
                   {'database': 'semp_demo', 'refreshedAt': '2026-09-20T00:00:00+00:00', 'tables': tables})


def reorder_keys(value):
    """递归按逆序重建所有 dict（值不变、语义相同），用于探测指纹的键序敏感性。"""
    if isinstance(value, dict):
        return {k: reorder_keys(value[k]) for k in sorted(value.keys(), reverse=True)}
    if isinstance(value, list):
        return [reorder_keys(item) for item in value]
    return value


def publish(payload, cookie=None):
    return request('POST', '/api/project-publish', payload, cookie=cookie)


def releases_of(project_id):
    status, payload = request('GET', f'/api/project-releases?project={project_id}')
    check(status == 200, f'读取 project-releases 应 200（实际 {status}）', actual=payload)
    return [item.get('version') for item in payload.get('items', [])]


def state_revision(project_id):
    status, payload = request('GET', f'/api/project-state?project={project_id}')
    check(status == 200, f'读取 project-state 应 200（实际 {status}）', actual=payload)
    return payload['revision']


def receipt_rows(request_key):
    return db_rows('SELECT owner_key, request_hash, response_json FROM wb_requests '
                   'WHERE operation = ? AND request_key = ? ORDER BY owner_key',
                   ('project-publish', request_key))


def body_text(value):
    return json.dumps(value, ensure_ascii=False, default=str) if not isinstance(value, str) else value


def assert_no_leak(status, body, why):
    text = body_text(body)
    check(status < 500, f'{why}：不得出现 5xx（实际 {status}）', actual=text[:400])
    for marker in ('UNIQUE constraint', 'IntegrityError', 'Traceback'):
        check(marker not in text, f'{why}：响应不得外泄 {marker}', actual=text[:400])


# --- 主流程 ------------------------------------------------------------------------

def assert_isolated():
    """安全前置：确认存储与数据根都落在本次临时根内，绝不指向真实数据。"""
    from workbench.storage import engine
    resolved = engine.resolve_url()
    db_file = engine.database_file(resolved)
    if db_file is None or str(TMP) not in str(db_file) or str(db_file) != str(DB_PATH):
        print(f'前置失败：数据库未隔离（resolve_url={resolved}）')
        sys.exit(2)
    from workbench.paths import DATA_ROOT
    if Path(str(DATA_ROOT)) != TMP:
        print(f'前置失败：DATA_ROOT 未隔离（{DATA_ROOT}）')
        sys.exit(2)
    print(f'  通过 · 隔离核对：DATA_ROOT={DATA_ROOT}；db={db_file}')


def main():
    version = seed_release()
    import_seed()
    start_server()
    auth_client.wait_ready(BASE)
    assert_isolated()
    _user, cookie = auth_client.register_or_login(BASE)
    AUTH['cookie'] = cookie
    print(f'动态端口 {PORT}；临时根 {TMP}；storage 版本 {version}')

    status, created = request('POST', '/api/projects',
                              {'name': '对抗性守卫项目', 'ontology': 'storage', 'version': version})
    check(status == 201, '创建 A 项目应 201', actual=(status, created))
    project = created['id']
    status, current = request('GET', f'/api/project-state?project={project}')
    state = current['state']
    revision = current['revision']

    obj_type, prop_api = scalar_property_and_type(version)
    note(f'夹具：对象类型 {obj_type}，标量属性 {prop_api}')

    # 编排（number 输出）：供依赖重验使用
    bind_fixture_user()
    from workbench import flows
    flow_id = flows.create('对抗性守卫编排')['id']
    flow_state = flows.read_draft(flow_id)
    flow_state['outputs'] = [{'id': 'out1', 'label': '结果', 'type': {'type': 'number'}}]
    flows.save_draft(flow_state, expected_token=flows.current_token(flow_id))

    MYSQL_CONN = {'id': 'adv-conn-01', 'name': '对抗业务库', 'engine': 'mysql',
                  'host': '127.0.0.1', 'port': 3306, 'username': 'reader', 'tls': 'none',
                  'database': 'semp_demo'}
    REDIS_CONN = {'id': 'adv-conn-02', 'name': '对抗缓存', 'engine': 'redis',
                  'host': '127.0.0.1', 'port': 6379, 'username': '', 'tls': 'none', 'dbIndex': 0}
    state['connections'] = {'connections': [copy.deepcopy(MYSQL_CONN)]}
    state['bindings']['object_bindings'] = [{
        'object_type': obj_type, 'connection': 'adv-conn-01', 'table': 't1', 'primary_key': 'id',
        'properties': {prop_api: {'kind': 'flow', 'flow': flow_id, 'output': 'out1', 'inputs': {}}}}]
    status, saved = request('POST', '/api/project-save', {'state': state, 'revision': revision})
    check(status == 200, '保存含连接与编排引用的草稿应 200', actual=(status, saved))
    revision = saved['revision']
    write_catalog(project, 'adv-conn-01', [{'name': 't1', 'kind': 'table', 'fields': [
        {'name': 'id', 'dataType': 'bigint', 'key': 'pri', 'comment': ''}]}])
    status, report = request('POST', '/api/project-validate', {'state': state, 'revision': revision})
    check(status == 200 and not report.get('errors'), '夹具草稿应无校验错误（可发布）', actual=report)
    base_state = copy.deepcopy(state)

    # ============ A1 并发同 requestId 同内容 =====================================
    print('\n[A1] 并发同 requestId 同内容（threading.Barrier 真并发，4 路）')
    KEY_CONC = 'adv-conc-1'
    LANES = 4
    stale_revision = revision
    payload = {'state': copy.deepcopy(base_state), 'revision': revision, 'requestId': KEY_CONC}
    results, lock = [], threading.Lock()
    barrier = threading.Barrier(LANES, timeout=30)

    def fire():
        local = copy.deepcopy(payload)
        try:
            barrier.wait()
        except threading.BrokenBarrierError:
            pass
        out = publish(local)
        with lock:
            results.append(out)

    threads = [threading.Thread(target=fire) for _ in range(LANES)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=90)
    check(len(results) == LANES, f'并发 {LANES} 个发布请求都应返回响应', actual=results)
    codes = [status for status, _ in results]
    check(all(code in (200, 409) for code in codes),
          f'并发响应只能是 200 或 409（实际 {codes}）',
          actual=[body_text(b)[:200] for _, b in results])
    ok200 = [(status, body) for status, body in results if status == 200]
    check(len(ok200) >= 1, '并发同 requestId 至少一个请求成功', actual=codes)
    for status, body in results:
        assert_no_leak(status, body, '并发幂等响应')
    versions = {body.get('version') for _, body in ok200}
    revisions = {body.get('revision') for _, body in ok200}
    check(len(versions) == 1 and len(revisions) == 1,
          '并发的所有成功响应版本号与 revision 完全一致（回放同一回执）',
          actual=(sorted(versions), sorted(revisions)))
    note(f'并发结果：{codes}；成功响应 version={sorted(versions)} revision={sorted(revisions)}')
    conflicts = [(s, b) for s, b in results if s == 409]
    for _, body in conflicts:
        check(isinstance(body, dict) and body.get('code') == 'REVISION_CONFLICT',
              '并发的 409 应是明确冲突码（非裸唯一约束异常）', actual=body)
    labels = releases_of(project)
    check(labels == ['v1'], '并发的同 requestId 请求最终只多一个版本（v1）', actual=labels)
    rows = receipt_rows(KEY_CONC)
    check(len(rows) == 1, 'wb_requests 同 requestId 只有一行回执', actual=rows)
    first_success = ok200[0][1]
    revision = str(first_success.get('revision') or revision)

    # ============ A2 成功后再同 key 同内容（带旧 revision → 回放优先于 CAS） ======
    print('\n[A2] 成功后同 key 同内容重试（带旧 revision，验证回放优先于 CAS）')
    status, replay = publish({'state': copy.deepcopy(base_state), 'revision': stale_revision,
                              'requestId': KEY_CONC})
    check(status == 200, '同 key 同内容带旧 revision 重试应 200 回放', actual=(status, replay))
    assert_no_leak(status, replay, '幂等回放响应')
    check(isinstance(replay, dict) and replay.get('idempotentReplay') is True,
          '回放响应应标记 idempotentReplay', actual=replay)
    check(isinstance(replay, dict) and replay.get('version') == first_success.get('version'),
          '回放 version 与首次成功完全一致', actual=(replay, first_success))
    check(isinstance(replay, dict) and replay.get('revision') == first_success.get('revision'),
          '回放 revision 与首次成功完全一致（不是重新生成）', actual=(replay, first_success))
    check(releases_of(project) == ['v1'], '回放不新增版本', actual=releases_of(project))

    # ============ A3 同 key 异内容 → 409 ========================================
    print('\n[A3] 同 requestId 异内容 → 409 零写入')
    changed = copy.deepcopy(base_state)
    changed['projectMeta']['note'] = '内容已变化'
    status, conflict = publish({'state': changed, 'revision': stale_revision, 'requestId': KEY_CONC})
    check(status == 409, '同 key 异内容应 409', actual=(status, conflict))
    assert_no_leak(status, conflict, '幂等冲突响应')
    check(releases_of(project) == ['v1'], '幂等冲突不新增版本', actual=releases_of(project))
    check(len(receipt_rows(KEY_CONC)) == 1, '幂等冲突不产生第二条回执', actual=receipt_rows(KEY_CONC))

    # ============ A4 语义相同但 dict 键序不同 ====================================
    print('\n[A4] 同 requestId + 语义相同但 dict 键序不同')
    KEY_ORDER = 'adv-order-1'
    ordered = copy.deepcopy(base_state)
    ordered['projectMeta']['note'] = '键序测试'
    status, saved = request('POST', '/api/project-save', {'state': ordered, 'revision': revision})
    check(status == 200, '保存键序测试草稿应 200', actual=(status, saved))
    rev_order = saved['revision']
    status, first_order = publish({'state': copy.deepcopy(ordered), 'revision': rev_order,
                                   'requestId': KEY_ORDER})
    check(status == 200 and first_order.get('version') == 'v2', '键序测试首次发布应 v2',
          actual=(status, first_order))
    status, replay_order = publish({'state': reorder_keys(copy.deepcopy(ordered)),
                                    'revision': rev_order, 'requestId': KEY_ORDER})
    if status == 200 and isinstance(replay_order, dict) and replay_order.get('idempotentReplay'):
        note('指纹按 canonical（sort_keys）判定：dict 键序不同仍回放')
        check(replay_order.get('version') == first_order.get('version')
              and replay_order.get('revision') == first_order.get('revision'),
              '键序不同的回放 version/revision 与首次一致', actual=(replay_order, first_order))
    else:
        note(f'指纹按键序敏感：键序改变未被回放（HTTP {status}）—— 报告为观察项')
        check(status == 409, '键序敏感时应为 409 而非其它错误', actual=(status, replay_order))
    check(releases_of(project) == ['v1', 'v2'], '键序探测不新增版本', actual=releases_of(project))

    # ============ A5 数组顺序不同（信息性探测） ==================================
    print('\n[A5] 数组顺序不同（信息性探测：数组不在 canonical 排序范围内）')
    KEY_ARR = 'adv-array-1'
    two_conns = copy.deepcopy(base_state)
    two_conns['connections']['connections'] = [copy.deepcopy(MYSQL_CONN), copy.deepcopy(REDIS_CONN)]
    two_conns['projectMeta']['note'] = '数组顺序探测'
    status, saved = request('POST', '/api/project-save',
                            {'state': two_conns, 'revision': state_revision(project)})
    check(status == 200, '保存双连接草稿应 200', actual=(status, saved))
    rev_arr = saved['revision']
    status, first_arr = publish({'state': copy.deepcopy(two_conns), 'revision': rev_arr,
                                 'requestId': KEY_ARR})
    check(status == 200, '数组探测首次发布应 200', actual=(status, first_arr))
    if status == 200:
        rev_arr = first_arr.get('revision') or rev_arr
    swapped = copy.deepcopy(two_conns)
    swapped['connections']['connections'] = list(reversed(swapped['connections']['connections']))
    status, replay_arr = publish({'state': swapped, 'revision': rev_arr, 'requestId': KEY_ARR})
    replayed = (status == 200 and isinstance(replay_arr, dict) and replay_arr.get('idempotentReplay'))
    note(f'数组顺序不同 → HTTP {status}' + ('（回放）' if replayed else '（未回放，指纹对数组顺序敏感）'))
    check(status in (200, 409), '数组顺序探测响应应为回放或 409', actual=(status, replay_arr))
    if status == 409:
        check(len(receipt_rows(KEY_ARR)) == 1, '数组顺序不同被拒时不新增回执', actual=receipt_rows(KEY_ARR))
        check(isinstance(replay_arr, dict) and replay_arr.get('code') == 'REVISION_CONFLICT',
              '数组顺序不同应给明确 409 REVISION_CONFLICT（而非校验/服务器错误）', actual=replay_arr)
        # 换新 key 提交同一「顺序不同」的内容：应能正常发布，证明 409 只来自指纹不等
        status_new, pub_new = publish({'state': copy.deepcopy(swapped), 'revision': rev_arr,
                                       'requestId': KEY_ARR + '-new'})
        check(status_new == 200, '确认指纹敏感：换新 requestId 提交顺序不同内容应可发布',
              actual=(status_new, pub_new))
        if status_new == 200:
            rev_arr = pub_new.get('revision') or rev_arr
    labels = releases_of(project)
    check(len(labels) == len(set(labels)), '数组顺序探测后版本号无重复', actual=labels)

    # ============ A6 客户端目录漂移 / 运行期字段不破坏幂等 =======================
    print('\n[A6] 客户端携带的 bindings.catalogs 与 _ 运行期字段不参与幂等指纹')
    KEY_CAT = 'adv-cat-1'
    cat_state = copy.deepcopy(two_conns)
    cat_state['projectMeta']['note'] = '目录漂移指纹测试'
    status, saved = request('POST', '/api/project-save',
                            {'state': cat_state, 'revision': state_revision(project)})
    check(status == 200, '保存目录漂移测试草稿应 200', actual=(status, saved))
    rev_cat = saved['revision']
    # 模拟真实客户端：state 来自 GET /api/project-state（已注入 catalogs）
    live = copy.deepcopy(request('GET', f'/api/project-state?project={project}')[1]['state'])
    status, first_cat = publish({'state': copy.deepcopy(live), 'revision': rev_cat,
                                 'requestId': KEY_CAT})
    check(status == 200, '目录漂移测试首次发布应 200', actual=(status, first_cat))
    drifted = copy.deepcopy(live)
    drifted['bindings']['catalogs'] = {
        'adv-conn-01': {'database': '另一个库', 'refreshedAt': '2099-01-01T00:00:00+00:00',
                        'tables': [{'name': '别的表', 'kind': 'table', 'fields': []}]},
        'adv-conn-99': {'database': 'x', 'refreshedAt': '', 'tables': []}}
    drifted['_draft'] = {'seq': 999, 'updatedAt': '2099-01-01T00:00:00+00:00', 'purpose': 'draft'}
    status, replay_cat = publish({'state': drifted, 'revision': rev_cat, 'requestId': KEY_CAT})
    check(status == 200 and isinstance(replay_cat, dict) and replay_cat.get('idempotentReplay'),
          '客户端目录内容/顺序漂移不影响幂等回放（服务端剥离 catalogs）', actual=(status, replay_cat))
    check(isinstance(replay_cat, dict) and replay_cat.get('revision') == first_cat.get('revision'),
          '目录漂移回放的 revision 与首次一致', actual=(replay_cat, first_cat))

    # ============ B 保存边界 ====================================================
    print('\n[B1] 只删被引用连接 → 422 + 零写入（project-state 逐字节不变）')
    boundary_state = copy.deepcopy(two_conns)
    boundary_state['projectMeta']['note'] = '保存边界基线'
    status, saved = request('POST', '/api/project-save',
                            {'state': boundary_state, 'revision': state_revision(project)})
    check(status == 200, '保存边界基线应 200', actual=(status, saved))
    revision = saved['revision']
    before_text = request('GET', f'/api/project-state?project={project}', raw=True)[1]
    drop_conn = copy.deepcopy(boundary_state)
    drop_conn['connections']['connections'] = [c for c in drop_conn['connections']['connections']
                                               if c.get('id') != 'adv-conn-01']
    status, denied = request('POST', '/api/project-save', {'state': drop_conn, 'revision': revision})
    check(status == 422, '只删被引用连接应 422', actual=(status, denied))
    check(isinstance(denied, dict) and denied.get('code') == 'REFERENCE_IN_USE',
          '拒绝码应为 REFERENCE_IN_USE', actual=denied)
    refs = denied.get('references') if isinstance(denied, dict) else None
    check(isinstance(refs, list) and any(r.get('connectionId') == 'adv-conn-01' for r in refs),
          '拒绝响应应列出被引用连接位置', actual=refs)
    after_text = request('GET', f'/api/project-state?project={project}', raw=True)[1]
    check(before_text == after_text, '422 后项目状态逐字节零写入',
          actual={'len_before': len(before_text), 'len_after': len(after_text),
                  'same': before_text == after_text})
    check(state_revision(project) == revision, '422 后草稿 revision 不推进')

    print('\n[B2] 连接与其引用同批移除 → 放行')
    same_batch = copy.deepcopy(boundary_state)
    same_batch['connections']['connections'] = [c for c in same_batch['connections']['connections']
                                                if c.get('id') != 'adv-conn-01']
    same_batch['bindings']['object_bindings'] = [
        b for b in same_batch['bindings']['object_bindings'] if b.get('connection') != 'adv-conn-01']
    status, saved = request('POST', '/api/project-save', {'state': same_batch, 'revision': revision})
    check(status == 200, '连接与引用同批移除应放行', actual=(status, saved))
    if status == 200:
        revision = saved['revision']
        read_back = request('GET', f'/api/project-state?project={project}')[1]['state']
        check([c.get('id') for c in read_back['connections']['connections']] == ['adv-conn-02'],
              '同批移除后连接已删除（仅剩另一连接）', actual=read_back['connections']['connections'])
        check(read_back['bindings']['object_bindings'] == [],
              '同批移除后对象映射已删除', actual=read_back['bindings']['object_bindings'])

    print('\n[B3] 引用藏在 implementations[].connection → 同样 422')
    impl_state = copy.deepcopy(same_batch)
    impl_state['connections']['connections'] = [copy.deepcopy(MYSQL_CONN)]
    impl_state['implementations'] = [{'id': 'impl-adv-01', 'name': '对抗实现',
                                      'connection': 'adv-conn-01', 'outputs': []}]
    status, saved = request('POST', '/api/project-save',
                            {'state': impl_state, 'revision': state_revision(project)})
    check(status == 200, '保存含实现连接引用的草稿应 200', actual=(status, saved))
    revision = saved.get('revision') if status == 200 else state_revision(project)
    impl_drop = copy.deepcopy(impl_state)
    impl_drop['connections']['connections'] = []
    status, denied = request('POST', '/api/project-save', {'state': impl_drop, 'revision': revision})
    check(status == 422 and isinstance(denied, dict) and denied.get('code') == 'REFERENCE_IN_USE',
          '仅被实现引用的连接被删也应 422 REFERENCE_IN_USE', actual=(status, denied))
    kinds = {r.get('kind') for r in (denied.get('references') or [])} if isinstance(denied, dict) else set()
    check('implementation' in kinds, 'references 应标明 implementation 形态', actual=denied)
    check(state_revision(project) == revision, 'B3 422 后草稿 revision 不推进')

    print('\n[B4] 引用藏在 property inlineSql.connection → 同样 422')
    inline_state = copy.deepcopy(impl_state)
    inline_state['implementations'] = []
    inline_state['bindings']['object_bindings'] = [{
        'object_type': obj_type, 'connection': '', 'table': '', 'primary_key': '',
        'properties': {prop_api: {'kind': 'field', 'field': 'id',
                                  'inlineSql': {'connection': 'adv-conn-01', 'mode': 'constant',
                                                'sql': 'SELECT 1'}}}}]
    status, saved = request('POST', '/api/project-save',
                            {'state': inline_state, 'revision': state_revision(project)})
    check(status == 200, '保存含内联 SQL 连接引用的草稿应 200', actual=(status, saved))
    revision = saved.get('revision') if status == 200 else state_revision(project)
    inline_drop = copy.deepcopy(inline_state)
    inline_drop['connections']['connections'] = []
    status, denied = request('POST', '/api/project-save', {'state': inline_drop, 'revision': revision})
    check(status == 422 and isinstance(denied, dict) and denied.get('code') == 'REFERENCE_IN_USE',
          '仅被内联 SQL 引用的连接被删也应 422 REFERENCE_IN_USE', actual=(status, denied))
    check(state_revision(project) == revision, 'B4 422 后草稿 revision 不推进')

    print('\n[B5] 引用藏在 sources[].connection（补充来源）→ 同样 422')
    src_state = copy.deepcopy(inline_state)
    src_state['bindings']['object_bindings'] = [{
        'object_type': obj_type, 'connection': '', 'table': '', 'primary_key': '',
        'properties': {},
        'sources': [{'id': 'src-1', 'name': '补充来源', 'kind': 'db', 'connection': 'adv-conn-01',
                     'table': 't1', 'matchLeft': 'id', 'matchRight': 'id'}]}]
    status, saved = request('POST', '/api/project-save',
                            {'state': src_state, 'revision': state_revision(project)})
    check(status == 200, '保存含补充来源连接引用的草稿应 200', actual=(status, saved))
    revision = saved.get('revision') if status == 200 else state_revision(project)
    src_drop = copy.deepcopy(src_state)
    src_drop['connections']['connections'] = []
    status, denied = request('POST', '/api/project-save', {'state': src_drop, 'revision': revision})
    check(status == 422 and isinstance(denied, dict) and denied.get('code') == 'REFERENCE_IN_USE',
          '仅被补充来源引用的连接被删也应 422 REFERENCE_IN_USE', actual=(status, denied))
    check(any(r.get('kind') == 'objectSource' for r in (denied.get('references') or []))
          if isinstance(denied, dict) else False,
          'references 应标明 objectSource 形态', actual=denied)
    check(state_revision(project) == revision, 'B5 422 后草稿 revision 不推进')

    # ============ C 依赖重验（受控注入，提交窗口内编排 head 变化） ================
    print('\n[C] 依赖重验：两次依赖读取之间改变编排 head → 409 DEPENDENCY_CHANGED 零写入')
    dep_state = copy.deepcopy(inline_state)
    dep_state['connections']['connections'] = [copy.deepcopy(MYSQL_CONN)]
    dep_state['implementations'] = []
    dep_state['bindings']['object_bindings'] = [{
        'object_type': obj_type, 'connection': 'adv-conn-01', 'table': 't1', 'primary_key': 'id',
        'properties': {prop_api: {'kind': 'flow', 'flow': flow_id, 'output': 'out1', 'inputs': {}}}}]
    dep_state['projectMeta']['note'] = '依赖重验基线'
    status, saved = request('POST', '/api/project-save',
                            {'state': dep_state, 'revision': state_revision(project)})
    check(status == 200, '保存依赖重验基线草稿应 200', actual=(status, saved))
    revision = saved['revision']
    status, dep_pub = publish({'state': copy.deepcopy(dep_state), 'revision': revision,
                               'requestId': 'adv-dep-base'})
    check(status == 200, '依赖重验基线发布应 200', actual=(status, dep_pub))
    revision = dep_pub['revision']
    labels_before = releases_of(project)

    bind_fixture_user()
    from workbench import project_routes, projects
    real_probe = projects.dependency_probe
    calls = {'n': 0}

    def probing(conn, probe_state):
        out = real_probe(conn, probe_state)
        calls['n'] += 1
        if calls['n'] == 1:  # 路由快照读取之后、提交复核之前：改变编排 head
            latest = flows.read_draft(flow_id)
            latest['description'] = '窗口内改写'
            flows.save_draft(latest, expected_token=flows.current_token(flow_id))
        return out

    with mock.patch.object(projects, 'dependency_probe', probing):
        denied_body, denied_status = project_routes.post_project_write(
            {'state': copy.deepcopy(dep_state), 'revision': revision, 'requestId': 'adv-dep-1'},
            '/api/project-publish')
    check(calls['n'] >= 2, '注入应命中两次依赖读取（快照 + 事务内复核）', actual=calls['n'])
    check(denied_status == 409, '窗口内编排 head 变化应 409', actual=(denied_status, denied_body))
    check(isinstance(denied_body, dict) and denied_body.get('reason') == 'DEPENDENCY_CHANGED',
          '409 应带 reason=DEPENDENCY_CHANGED', actual=denied_body)
    assert_no_leak(denied_status, denied_body, '依赖重验 409')
    labels_after = releases_of(project)
    check(labels_after == labels_before, '依赖变化拒绝发布不得新增 project-releases',
          actual=(labels_before, labels_after))
    check(projects.current_token(project) == revision, '依赖变化拒绝后草稿 revision 不推进',
          actual=(projects.current_token(project), revision))
    check(len(receipt_rows('adv-dep-1')) == 0, '被拒发布不得留下幂等回执', actual=receipt_rows('adv-dep-1'))
    # 恢复路径：依赖稳定后同内容同 key 重试应成功（被拒不留半成品，回执此时才写入）
    status, retried = publish({'state': copy.deepcopy(dep_state), 'revision': revision,
                               'requestId': 'adv-dep-1'})
    check(status == 200, '依赖稳定后同内容同 key 重试应成功（被拒无残渣）', actual=(status, retried))
    check(len(receipt_rows('adv-dep-1')) == 1, '重试成功后才写入一条回执', actual=receipt_rows('adv-dep-1'))
    if status == 200:
        revision = retried.get('revision') or revision
        labels_after = releases_of(project)
        check(len(labels_after) == len(labels_before) + 1,
              '恢复发布只新增一个版本', actual=(labels_before, labels_after))

    # CAS 冲突 / 校验失败不得写回执（03 分册 §2.3「保存失败、CAS 冲突不写回执」）
    status, cas_conflict = publish({'state': copy.deepcopy(dep_state), 'revision': 'r-stale-token',
                                    'requestId': 'adv-cas-1'})
    check(status == 409, '过期 revision + 新 requestId 应 409', actual=(status, cas_conflict))
    check(len(receipt_rows('adv-cas-1')) == 0, 'CAS 冲突不得写回执', actual=receipt_rows('adv-cas-1'))
    bad_state = copy.deepcopy(dep_state)
    bad_state['bindings']['object_bindings'][0]['properties'][prop_api]['flow'] = 'no-such-flow'
    status, failed = publish({'state': bad_state, 'revision': revision, 'requestId': 'adv-422-1'})
    check(status == 422, '校验失败应 422', actual=(status, failed))
    check(len(receipt_rows('adv-422-1')) == 0, '校验失败不得写回执', actual=receipt_rows('adv-422-1'))
    check(len(releases_of(project)) == len(labels_after), '422 不新增版本',
          actual=releases_of(project))

    # ============ D 跨账号：不得回放他人回执 ====================================
    print('\n[D] 跨账号：B 用 A 的 requestId 不得回放 A 的回执/A 的版本')
    _other, other_cookie = auth_client.register_or_login(BASE, 'advother', 'advother1234')
    check(bool(other_cookie), '第二假账号应能登录')
    status, other_project = request('POST', '/api/projects', {'name': 'B 自己的项目'}, cookie=other_cookie)
    check(status == 201, 'B 应能创建自己的项目', actual=(status, other_project))
    status, other_state = request('GET', f"/api/project-state?project={other_project['id']}",
                                  cookie=other_cookie)
    status, replay_b = publish({'state': other_state['state'], 'revision': other_state['revision'],
                                'requestId': KEY_CONC}, cookie=other_cookie)
    check(not (status == 200 and isinstance(replay_b, dict) and replay_b.get('idempotentReplay')),
          'B 用 A 的 requestId 不得命中 A 的回执', actual=(status, replay_b))
    leaked_text = body_text(replay_b)
    check(str(first_success.get('revision') or 'r-不可能出现') not in leaked_text,
          'B 的响应不得包含 A 的 revision（幂等回执串号）', actual=leaked_text[:400])
    rows = receipt_rows(KEY_CONC)
    owner_a = auth_client.user_id_from_db(TMP)
    check(len(rows) == 1 and str(rows[0][0]).startswith(owner_a + '/'),
          'A 的 requestId 只有一行回执且归属 A 的 owner_key', actual=rows)
    status, borrowed = publish({'state': copy.deepcopy(dep_state), 'revision': revision,
                                'requestId': 'adv-cross-2'}, cookie=other_cookie)
    check(status in (404, 400), 'B 提交 A 的项目应被按不存在拒绝', actual=(status, borrowed))
    check('idempotentReplay' not in body_text(borrowed)
          and str(first_success.get('revision') or '\x00') not in body_text(borrowed),
          '跨账号拒绝响应不得夹带 A 的回执字段', actual=body_text(borrowed)[:300])
    status, borrow_same_key = publish({'state': copy.deepcopy(dep_state), 'revision': revision,
                                       'requestId': 'adv-dep-1'}, cookie=other_cookie)
    check(status in (404, 400), 'B 用 A 的 requestId 提交 A 的项目同样被拒',
          actual=(status, borrow_same_key))
    check(state_revision(project) == revision, 'A 草稿 revision 未被跨账号请求推进',
          actual=(state_revision(project), revision))

    # 同一账号的另一个项目用同一 requestId：owner_key 含项目 id，不得回放他项目的回执
    status, project2 = request('POST', '/api/projects', {'name': 'A 的第二个项目'})
    check(status == 201, 'A 应能创建第二个项目', actual=(status, project2))
    status, st2 = request('GET', f"/api/project-state?project={project2['id']}")
    status, pub2 = publish({'state': st2['state'], 'revision': st2['revision'],
                            'requestId': KEY_CONC})
    check(not (isinstance(pub2, dict) and pub2.get('idempotentReplay')),
          '同账号另一项目复用同 requestId 不得回放他项目的回执', actual=(status, pub2))
    check(isinstance(pub2, dict) and pub2.get('version') in ('', None, 'v1'),
          '同账号另一项目应产出自己的版本（v1）而非借用 A 的版本', actual=(status, pub2))


def report():
    shutdown()
    print('\n================ 汇总 ================')
    print(f'通过 {len(OK)} 项；不符 {len(BAD)} 项')
    for item in BAD:
        print('  不符：' + item)
    if BAD:
        print(f'临时根保留供排查：{TMP}')
        sys.exit(1)
    shutil.rmtree(TMP, ignore_errors=True)
    print('临时根已清理；全部断言通过')


if __name__ == '__main__':
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # 非断言异常：附现场信息
        import traceback
        traceback.print_exc()
        print(f'\n[异常] {type(exc).__name__}: {exc}')
        report()
        sys.exit(3)
    report()
