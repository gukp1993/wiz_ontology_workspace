"""「保存一次直通」服务端回归测试（并行任务板 P11）。

覆盖（任务板 §4 错误与状态消费协议 + P11 包交付）：
1. 项目绑定保存 → 服务进程重启 → GET project-state 深度比对配置与 revision 一致。
2. 本体草稿保存（含 valueShape:'timeSeries' 属性）→ 重启 → GET /api/state 深度比对。
3. 项目旧 revision 保存 → 409 且响应体携带 currentRevision，值等于服务端当前 revision。
4. 本体旧 revision 保存 → 409 且响应体携带 currentRevision（§4：本体与项目两处）。
5. 旧格式项目（字符串直取属性 + related_sources）保存→读回：不迁移、不丢失。
6. workbench.projects.save_draft 在 drafts 目录只读时不再假成功：返回 error 或抛异常，
   且旧指针 current.json 完好可读（此前异常被吞掉、返回假成功，P02 修复项）。
7. save_draft 在 revisions 目录只读（外层 mkdir 失败路径）同样不假成功且旧指针完好。

纯 python3 标准库（无 pytest）。隔离规则（AGENTS.md 测试隔离铁律）：
- 真实 ontology/ 只读（复制 releases/models 下已发布版本作为种子）；一切写入只发生在
  mktemp 临时根；服务以 WIZ_WORKBENCH_ROOT=<临时根> WIZ_WORKBENCH_PORT=18820 子进程运行，
  中途 terminate 后用同一临时根重启，验证落盘数据存活。
- 单元级行为（步骤 6/7）在服务子进程停止后就地调用 workbench.projects.save_draft。
运行：python3 tests/test_save_iteration.py
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PORT = 18820
ORIGIN = f'http://127.0.0.1:{PORT}'
BASE = f'http://127.0.0.1:{PORT}'

TMP = Path(tempfile.mkdtemp(prefix='wiz_save_iteration_'))
os.environ['WIZ_WORKBENCH_ROOT'] = str(TMP)  # 后备路径里 import workbench 时生效

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
    """启动隔离服务子进程（重启场景下同一临时根、同一端口复用）。"""
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


# --- 种子（真实 ontology/ 只读，写入仅在临时根；与 test_project_api_roundtrip 同思路）---

def seed_release():
    """把真实已发布版本复制为临时根 storage 的版本；无版本时按 versions.py 逻辑登记 1.0.0。"""
    releases_models = TMP / 'ontology/releases/models/storage'
    src = None
    real_models = REPO / 'ontology/releases/models'
    if (real_models / 'storage').is_dir():
        src = real_models / 'storage'
    elif real_models.is_dir():
        for candidate in sorted(real_models.iterdir()):
            if candidate.is_dir() and (candidate / 'index.json').is_file():
                src = candidate
                break
    if src is not None:
        shutil.copytree(src, releases_models)
    index_path = releases_models / 'index.json'
    if not index_path.is_file() or not json.loads(index_path.read_text()).get('versions'):
        base = REPO / 'ontology/models/storage'
        needed = ['ontology.json', 'workflow.json', 'metrics.yaml', 'rules.yaml']
        check(all((base / name).is_file() for name in needed),
              '真实 ontology/ 中既无已发布版本也无基础文件，无法播种 storage 版本',
              actual=[(name, (base / name).is_file()) for name in needed],
              expected='任一来源可读')
        dest = TMP / 'ontology/models/storage'
        dest.mkdir(parents=True, exist_ok=True)
        for name in needed:
            shutil.copyfile(base / name, dest / name)
        sys.path.insert(0, str(REPO))
        from workbench import versions
        versions.ensure_base_release('storage')
    entries = json.loads(index_path.read_text())['versions']
    check(bool(entries), '临时根 releases/models/storage 应至少登记一个版本', actual=entries,
          expected='非空 versions 列表')
    return entries[-1]['version']


def patch_time_series(version):
    """在临时根副本上把 SOC 共享属性标为 timeSeries（真实本体只读）。

    mg:valueShape 只能写在共享定义上：properties.effective() 会丢弃带 sharedPropertyId
    的对象属性自身 FIELDS（同 test_project_api_roundtrip.patch_time_series）。
    """
    path = TMP / 'ontology/releases/models/storage' / version / 'ontology.json'
    data = json.loads(path.read_text())
    shared = {s['id']: s for s in data.get('sharedProperties') or []}
    target = None
    for record in data.get('sharedProperties') or []:
        if 'soc' in str(record.get('displayName', '')).casefold():
            target = record
            break
    if target is None:
        cluster = next((c for c in data.get('objectTypes') or []), None)
        props = [p for p in data.get('properties') or []
                 if cluster and p.get('objectTypeId') == cluster.get('id')]
        refs = {p.get('sharedPropertyId') for p in props if p.get('sharedPropertyId')}
        target = next((shared[rid] for rid in refs if rid in shared), None)
    check(target is not None, '未找到可标记 timeSeries 的共享属性', actual=data.get('sharedProperties'),
          expected='存在共享属性定义')
    target['valueShape'] = 'timeSeries'
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    return target


# --- 配置样例（复用 test_project_api_roundtrip）------------------------------------

MYSQL_CONN = {'id': 'conn-mysql-01', 'name': '储能业务库', 'engine': 'mysql',
              'host': '127.0.0.1', 'port': 3306, 'database': 'semp_demo'}
REDIS_CONN = {'id': 'conn-redis-01', 'name': '实时缓存', 'engine': 'redis',
              'host': '127.0.0.1', 'port': 6379}


def database_series_config():
    return {'kind': 'database', 'connection': MYSQL_CONN['id'], 'table': 'telemetry_sample',
            'lookup': {'match': [
                {'field': 'object_id', 'operator': 'eq', 'value': {'kind': 'identityKey'}},
                {'field': 'object_type', 'operator': 'eq',
                 'value': {'kind': 'constant', 'value': 'storage_cluster'}},
                {'field': 'metric_code', 'operator': 'eq',
                 'value': {'kind': 'constant', 'value': 'soc'}}],
                'timeRange': {'field': 'sampled_at',
                              'start': {'kind': 'context', 'name': 'startTime'},
                              'end': {'kind': 'context', 'name': 'endTime'},
                              'bounds': '[start,end)'}},
            'result': {'valueField': 'value', 'timestampField': 'sampled_at',
                       'timestampEncoding': 'datetime', 'timezone': 'Asia/Shanghai',
                       'order': 'ascending', 'duplicateTimestamp': 'error'}}


def redis_scalar_config():
    return {'kind': 'redis', 'connection': REDIS_CONN['id'], 'command': 'GET',
            'key': 'm_storage_cluster-{id}-soc',
            'params': {'id': {'from': 'primary'}}, 'conversion': 'number'}


def discover(version):
    """通过 /api/version-state 动态读取引用版本里储能簇与两个属性的 apiName。"""
    status, payload = request('GET', f'/api/version-state?ontology=storage&version={version}')
    check(status == 200, 'GET /api/version-state 应 200', actual=(status, payload), expected=200)
    ont = payload['state']['ontology']
    classes = ont.get('objectTypes') or []
    cluster = next((c for c in classes if '储能簇' in str(c.get('displayName', ''))), None) or classes[0]
    shared = {s['id']: s for s in ont.get('sharedProperties') or []}

    def shape_of(prop):
        value = prop.get('valueShape') or (shared.get(prop.get('sharedPropertyId') or '') or {}).get('valueShape')
        if (prop.get('dataType') or (shared.get(prop.get('sharedPropertyId') or '') or {}).get('dataType') or {}).get('type') == 'timeSeries':return 'timeSeries'
        return value if value in ('scalar', 'timeSeries') else 'scalar'

    props = [p for p in ont.get('properties') or [] if p.get('objectTypeId') == cluster['id']]
    series = [p for p in props if shape_of(p) == 'timeSeries']
    soc = next((p for p in series if 'soc' in str(
        (shared.get(p.get('sharedPropertyId') or '') or {}).get('displayName', '')).casefold()), None)
    if soc is None and series:
        soc = series[0]
    scalars = [p for p in props if p.get('id') != soc.get('id') and shape_of(p) == 'scalar']
    rated = next((p for p in scalars if '额定' in str(
        (shared.get(p.get('sharedPropertyId') or '') or {}).get('displayName', '')
        + str(p.get('displayName', '')))), None) if scalars else None
    if rated is None and scalars:
        rated = scalars[0]
    check(soc is not None and rated is not None,
          '储能簇下应有一个 timeSeries 属性与一个 scalar 属性',
          actual=[(p.get('apiName'), shape_of(p)) for p in props], expected='各至少一个')
    print(f'  [读取] 对象类型(储能簇) id={cluster["id"]}')
    print(f'  [读取] timeSeries 属性 apiName={soc["apiName"]}；scalar 属性 apiName={rated["apiName"]}')
    return cluster['id'].removeprefix('mg:'), soc['apiName'], rated['apiName']


def strip_volatile(state):
    """剔除服务端注入/派生的易变键：_draft（指针元数据）与 bindings.catalogs（目录注入）。"""
    out = json.loads(json.dumps(state, ensure_ascii=False))
    out.pop('_draft', None)
    bindings = out.get('bindings')
    if isinstance(bindings, dict):
        bindings.pop('catalogs', None)
    return out


def create_project(name, version):
    status, created = request('POST', '/api/projects',
                              {'name': name, 'ontology': 'storage', 'version': version})
    check(status == 201, f'POST /api/projects（{name}）应 201', actual=(status, created), expected=201)
    return created['id']


# --- 主流程 ----------------------------------------------------------------------

def import_seed_into_db():
    """库化后：把临时根文件树（已复制的发布 + 补丁）导入存储库，服务才可见。"""
    import os as _os
    _os.environ['WIZ_DATABASE_URL'] = 'sqlite:///' + str(TMP / 'data' / 'workbench.sqlite3')
    sys.path.insert(0, str(REPO))
    from workbench.storage import transfer as _transfer
    rc = _transfer.main(['import', '--source', str(TMP)])
    check(rc == 0, '种子发布导入存储库', actual=rc, expected=0)
    _os.environ.pop('WIZ_DATABASE_URL', None)


def main():
    version = seed_release()
    patch_time_series(version)
    import_seed_into_db()

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
    ok('1', f'隔离服务已就绪 WIZ_WORKBENCH_ROOT={TMP} 端口 {PORT}')

    # --- 步骤 2：项目绑定保存 → 重启 → 仍在 -----------------------------------------
    pid = create_project('保存重启回归测试', version)
    status, payload = request('GET', f'/api/project-state?project={pid}')
    check(status == 200 and payload.get('state', {}).get('projectId') == pid,
          'GET /api/project-state 应返回项目状态', actual=(status, payload.get('project')), expected=200)
    state, revision = payload['state'], payload['revision']
    initial_revision = revision  # 保存前留档，供步骤 4 当作过期 revision 使用

    object_type, soc_api, rated_api = discover(version)
    state['connections'] = {'connections': [dict(MYSQL_CONN), dict(REDIS_CONN)]}
    binding = {'object_type': object_type, 'connection': MYSQL_CONN['id'],
               'table': 'm_storage_cluster_phase', 'primary_key': 'id', 'title_key': '',
               'properties': {soc_api: database_series_config(), rated_api: redis_scalar_config()},
               'relations': []}
    state['bindings']['object_bindings'] = [binding]
    status, report = request('POST', '/api/project-validate', {'state': state})
    check(status == 200 and report.get('errors') == [],
          'database+redis 完整配置应通过项目校验（0 errors）',
          actual={'status': status, 'errors': report.get('errors')}, expected={'errors': []})
    status, saved = request('POST', '/api/project-save', {'state': state, 'revision': revision})
    check(status == 200 and saved.get('revision'), 'project-save 应 200 并返回新 revision',
          actual=(status, saved), expected=200)
    revision = saved['revision']
    status, after_save = request('GET', f'/api/project-state?project={pid}')
    check(status == 200 and after_save.get('revision') == revision,
          '保存后 GET project-state 的 revision 应与保存响应一致',
          actual={'get': after_save.get('revision'), 'save': revision}, expected='一致')
    snapshot = strip_volatile(after_save['state'])

    shutdown()
    start_service()
    status, after_restart = request('GET', f'/api/project-state?project={pid}')
    check(status == 200, '重启后 GET project-state 应 200', actual=status, expected=200)
    check(strip_volatile(after_restart['state']) == snapshot,
          '重启后项目配置应与保存时深度一致（剔除 _draft/catalogs 派生键后）',
          actual=strip_volatile(after_restart['state']), expected=snapshot)
    check(after_restart.get('revision') == revision,
          '重启后 revision 应与保存响应一致', actual=after_restart.get('revision'), expected=revision)
    read_back = after_restart['state']['bindings']['object_bindings'][0]['properties']
    check(read_back == {soc_api: database_series_config(), rated_api: redis_scalar_config()},
          '重启后 database/redis 属性来源配置应与发送内容完全一致',
          actual=read_back, expected={soc_api: database_series_config(), rated_api: redis_scalar_config()})
    ok('2', f'项目绑定（database timeSeries + redis scalar）保存→重启→配置与 revision 一致（rev={revision[:12]}…）')

    # --- 步骤 3：本体草稿保存（timeSeries 属性）→ 重启 → 仍在 ---------------------------
    status, before_o = request('GET', '/api/state')
    check(status == 200, 'GET /api/state 应 200', actual=status, expected=200)
    o_state, o_revision = before_o['state'], before_o['revision']
    class_record = {'id': 'mg:demo_device', 'displayName': '演示设备'}
    prop_record = {'id': 'mg:series_power', 'displayName': '演示功率序列', 'apiName': 'series_power',
                   'objectTypeId': 'mg:demo_device', 'dataType': {'type': 'timeSeries', 'valueType': 'double'}}
    o_state['ontology']['objectTypes'].append(class_record)
    o_state['ontology']['properties'].append(prop_record)
    o_state['ontology']['definitionOrder'] += [class_record['id'], prop_record['id']]
    status, o_saved = request('POST', '/api/save', {'state': o_state, 'revision': o_revision})
    check(status == 200 and o_saved.get('revision'), 'POST /api/save 应 200（草稿允许带校验错误）',
          actual={'status': status, 'body': o_saved}, expected=200)
    if o_saved.get('errors'):
        print(f'  [提示] 保存响应携带 {len(o_saved["errors"])} 项校验错误（不阻塞草稿保存）')
    o_revision_new = o_saved['revision']
    status, o_after_save = request('GET', '/api/state')
    check(status == 200 and o_after_save.get('revision') == o_revision_new,
          '本体保存后 GET /api/state 的 revision 应一致',
          actual={'get': o_after_save.get('revision'), 'save': o_revision_new}, expected='一致')
    o_snapshot = o_after_save['state']

    shutdown()
    start_service()
    status, o_after_restart = request('GET', '/api/state')
    check(status == 200, '重启后 GET /api/state 应 200', actual=status, expected=200)
    check(o_after_restart.get('revision') == o_revision_new,
          '重启后本体 revision 应与保存响应一致',
          actual=o_after_restart.get('revision'), expected=o_revision_new)
    check(o_after_restart['state'] == o_snapshot,
          '重启后本体状态应与保存后深度一致', actual=o_after_restart['state'], expected=o_snapshot)
    restored = next((p for p in o_after_restart['state'].get('ontology', {}).get('properties', [])
                     if p.get('id') == prop_record['id']), None)
    check(restored == prop_record, '重启后 timeSeries 属性定义应与保存内容完全一致',
          actual=restored, expected=prop_record)
    ok('3', f'本体草稿（含 valueShape=timeSeries 属性）保存→重启→属性与 revision 一致（rev={o_revision_new[:12]}…）')

    # --- 步骤 4：项目旧 revision 保存 → 409 + currentRevision -------------------------
    status, fresh = request('GET', f'/api/project-state?project={pid}')
    current_revision = fresh['revision']
    stale_state = strip_volatile(fresh['state'])
    stale_state['name'] = '过期并发写入'
    status, conflict = request('POST', '/api/project-save',
                               {'state': stale_state, 'revision': initial_revision})
    check(status == 409, '项目用过期 revision 保存应 409', actual=(status, conflict), expected=409)
    check(isinstance(conflict, dict) and 'currentRevision' in conflict,
          '409 响应体应携带 currentRevision（任务板 §4 / P02）',
          actual=conflict, expected='含 currentRevision 字段')
    check(conflict.get('currentRevision') == current_revision,
          'currentRevision 应等于服务端当前 revision',
          actual={'currentRevision': conflict.get('currentRevision'), '服务端': current_revision},
          expected='相等')
    ok('4', f'项目 409 携带 currentRevision={str(conflict.get("currentRevision"))[:12]}…（与 GET 一致）')

    # --- 步骤 5：本体旧 revision 保存 → 409 + currentRevision -------------------------
    status, fresh_o = request('GET', '/api/state')
    current_o_revision = fresh_o['revision']
    stale_o = json.loads(json.dumps(fresh_o['state'], ensure_ascii=False))
    status, o_conflict = request('POST', '/api/save', {'state': stale_o, 'revision': o_revision})
    check(status == 409, '本体用过期 revision 保存应 409', actual=(status, o_conflict), expected=409)
    check(isinstance(o_conflict, dict) and 'currentRevision' in o_conflict,
          '本体 409 响应体应携带 currentRevision（任务板 §4：本体与项目两处）',
          actual=o_conflict, expected='含 currentRevision 字段')
    check(o_conflict.get('currentRevision') == current_o_revision,
          '本体 currentRevision 应等于服务端当前 revision',
          actual={'currentRevision': o_conflict.get('currentRevision'), '服务端': current_o_revision},
          expected='相等')
    ok('5', f'本体 409 携带 currentRevision={str(o_conflict.get("currentRevision"))[:12]}…（与 GET 一致）')

    # --- 步骤 6：旧格式（字符串直取 + related_sources）往返抽查 -------------------------
    legacy_pid = create_project('旧格式往返抽查项目', version)
    status, payload = request('GET', f'/api/project-state?project={legacy_pid}')
    legacy_state, legacy_revision = payload['state'], payload['revision']
    legacy_binding = {'object_type': object_type, 'connection': MYSQL_CONN['id'],
                      'table': 'm_storage_cluster_phase', 'primary_key': 'id', 'title_key': '',
                      'properties': {rated_api: 'rated_power_direct'},
                      'related_sources': [{'id': 'legacy-src-01', 'name': '历史辅助来源',
                                           'table': 'm_storage_cluster_phase',
                                           'source_field': 'id', 'target_field': 'asset_code'}],
                      'relations': []}
    legacy_state['connections'] = {'connections': [dict(MYSQL_CONN)]}
    legacy_state['bindings']['object_bindings'] = [legacy_binding]
    status, legacy_saved = request('POST', '/api/project-save',
                                   {'state': legacy_state, 'revision': legacy_revision})
    check(status == 200 and legacy_saved.get('revision'), '旧格式项目保存应 200',
          actual=(status, legacy_saved), expected=200)
    status, legacy_back = request('GET', f'/api/project-state?project={legacy_pid}')
    read_binding = legacy_back['state']['bindings']['object_bindings'][0]
    check(read_binding.get('properties', {}).get(rated_api) == 'rated_power_direct'
          and isinstance(read_binding.get('properties', {}).get(rated_api), str),
          '字符串直取属性应原样保留（不迁移不丢失）',
          actual=read_binding.get('properties'), expected={rated_api: 'rated_power_direct'})
    check(read_binding.get('related_sources') == legacy_binding['related_sources'],
          'related_sources 应原样保留（不迁移不丢失）',
          actual=read_binding.get('related_sources'), expected=legacy_binding['related_sources'])
    ok('6', f'旧格式往返：字符串直取属性 + related_sources 均原样保留（pid={legacy_pid}）')

    # --- 步骤 7/8：save_draft 异常不再假成功（单元级，服务已停止）-----------------------
    shutdown()
    sys.path.insert(0, str(REPO))
    from workbench import projects as projects_store

    unit = projects_store.create('保存失败单测项目', 'storage', version)
    unit_id = unit['id']
    unit_state, _ = projects_store.load(unit_id)
    first = projects_store.save_draft(unit_state)
    check('error' not in first, '首次正常保存不应返回 error', actual=first, expected="无 'error' 键")
    token_before = projects_store.current_token(unit_id)
    unit_state['name'] = '存储不可用时的修改'

    # 步骤 7（库化版）：存储不可用 → save_draft 不假成功（返回 error 或抛异常），head 不动。
    _real_url = os.environ.get('WIZ_DATABASE_URL', '')
    os.environ['WIZ_DATABASE_URL'] = 'sqlite:////nonexistent-readonly-path/wiz-fail.db'
    try:
        outcome = {}
        try:
            result = projects_store.save_draft(unit_state)
            outcome = {'raised': False, 'result': result}
        except Exception as exc:  # noqa: BLE001 — 允许实现选择抛异常
            outcome = {'raised': True, 'type': type(exc).__name__, 'message': str(exc)[:120]}
        check(outcome.get('raised') or (isinstance(outcome.get('result'), dict)
                                        and 'error' in outcome['result']),
              '存储不可用时 save_draft 不得假成功：应返回 error 或抛异常',
              actual=outcome, expected="raised 或 result 含 'error'")
    finally:
        if _real_url:
            os.environ['WIZ_DATABASE_URL'] = _real_url
        else:
            os.environ.pop('WIZ_DATABASE_URL', None)
        check(projects_store.current_token(unit_id) == token_before,
              '保存失败后 head revision token 不得移动',
              actual=projects_store.current_token(unit_id), expected=token_before)
        reloaded, _ = projects_store.load(unit_id)
        check(reloaded.get('name') == '保存失败单测项目',
              '保存失败后草稿仍应读出首次保存的内容', actual=reloaded.get('name'),
              expected='保存失败单测项目')
        ok('7', f'存储不可用不假成功（{outcome.get("type", "返回 error")}），head 完好可读')

    # 步骤 8：恢复后再次保存 → 成功且 token 前进（回归保护：故障后可恢复写入）。
    result = projects_store.save_draft(unit_state)
    check('error' not in result and result.get('revision', '').startswith('r-'),
          '故障恢复后保存成功且返回新 token', actual=result, expected='r- 前缀 token')
    ok('8', f'恢复后保存正常（token={result.get("revision", "")[:10]}…）')


if __name__ == '__main__':
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # 意外异常也要停服并留现场
        import traceback
        traceback.print_exc()
        shutdown()
        print(f'\n（临时根保留供排查：{TMP}）')
        sys.exit(1)
    finally:
        shutdown()
    shutil.rmtree(TMP, ignore_errors=True)
    print(f'\n统计：{len(PASSED)} 个步骤全部通过（1–8）')
    print('临时根已清理')
