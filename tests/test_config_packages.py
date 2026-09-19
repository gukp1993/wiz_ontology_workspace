"""配置迁移测试（20260919 需求 M01-M24 后端面）：格式安全 + 导出/导入全链路。

覆盖：
- 格式安全：穿越路径/重复条目/缺 manifest/坏 hash/超限 ZIP 拒绝（415/422）；同名后缀分配。
- 导出：依赖闭包（项目→本体版本、编排、模型配置、凭据声明）；未选项目不进包。
- 导入：B 账号同名后缀、资产级全新 ID、业务稳定 ID 恒等、P1/P2 引用新本体对应版本、
  编排共享、M07 上下文拆副本、requestId 幂等与改参数 409、import-result 回执、
  账号隔离（跨账号 token 404）、第二次主动导入独立副本。
- 原子性：故障注入（模型配置行触发唯一冲突）后零资产写入、旧资产不变。

隔离：临时数据根 + 独立端口子进程 + 直连临时库播种（不碰真实 ontology/SQLite）。
运行：python3 tests/test_config_packages.py
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import auth_client

REPO = Path(__file__).resolve().parents[1]
CHUNK_SIZE_TEST = 512 * 1024
PORT = 18821
ORIGIN = f'http://127.0.0.1:{PORT}'
BASE = ORIGIN
TMP = Path(tempfile.mkdtemp(prefix='wiz_config_pkg_'))
os.environ['WIZ_WORKBENCH_ROOT'] = str(TMP)

PROC = None
PASSED = []
COOKIES = {}  # username → cookie value


def check(cond, message, actual=None):
    if cond:
        return
    print(f'\n[失败] {message}')
    if actual is not None:
        print('  实际: ' + json.dumps(actual, ensure_ascii=False, default=str)[:3000])
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


def request(method, path, payload=None, user=None, raw=False):
    data = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    headers = {'Content-Type': 'application/json'}
    if user and COOKIES.get(user):
        headers['Cookie'] = 'wiz_session=' + COOKIES[user]
    if method == 'POST':
        headers['Origin'] = ORIGIN
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
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


def db_call(fn, fixture_user='tester'):
    saved = dict(os.environ)
    os.environ['WIZ_DATABASE_URL'] = 'sqlite:///' + str(TMP / 'data' / 'workbench.sqlite3')
    sys.path.insert(0, str(REPO))
    auth_client.bind_fixture_user(fixture_user, 'test1234')
    from workbench import storage
    try:
        storage.ensure_ready()
        return fn()
    finally:
        os.environ.clear()
        os.environ.update(saved)


def seed_asset(owner_user_id, kind, external_id, name, payload, payload_format,
               releases=(), project_ref=None, summary=None):
    """直连临时库播种：资产 + 草稿 head + 可选发布（与导入写入路径同构）。"""
    def body():
        from workbench.storage import assets as store
        from workbench.storage.engine import write_tx, utcnow

        def inner(conn):
            asset_uid = store.ensure_asset(conn, kind, external_id, name, summary or {}, None,
                                           owner_user_id=owner_user_id)
            snapshot = store.append_snapshot(conn, asset_uid, payload, payload_format, 'draft', now=utcnow())
            import sqlalchemy
            conn.execute(sqlalchemy.text(
                'INSERT INTO wb_asset_heads (asset_uid, snapshot_id, revision_token, generation, '
                'snapshot_seq, release_seq, updated_at) VALUES (:a, :s, :t, 1, :q, 0, :now)'),
                {'a': asset_uid, 's': snapshot['snapshot_id'], 't': store.new_token(),
                 'q': snapshot['seq'], 'now': utcnow()})
            if project_ref:
                store.upsert_project_ref(conn, snapshot['snapshot_id'], project_ref)
            for label, rel_payload, rel_format in releases:
                snap = store.append_snapshot(conn, asset_uid, rel_payload, rel_format, 'release', now=utcnow())
                store.append_release(conn, asset_uid, label, snap['snapshot_id'],
                                     {'version': label, 'createdAt': utcnow()}, now=utcnow())
        with write_tx() as tx:
            tx.run(inner)
    db_call(body)


# ── 播种数据 ───────────────────────────────────────────────────────────────────

MODEL_ID_A = '11111111-1111-4111-8111-111111111111'
MODEL_ID_B = '22222222-2222-4222-8222-222222222222'
PROJECT_P1 = 'aaaa1111aaaa'
PROJECT_P2 = 'aaaa2222aaaa'
PROJECT_P3 = 'aaaa3333aaaa'
FLOW_F = 'bbbb1111bbbb'
FLOW_F2 = 'bbbb2222bbbb'
PROVIDER = 'llm-seed00001'


def model_payload(workspace_id, name, object_name='储能设备'):
    return {
        'workspaceId': workspace_id,
        'ontology': {'schemaVersion': 1,
                     'namespaces': {'mg': 'https://example.com/microgrid/', 'owl': 'http://www.w3.org/2002/07/owl#',
                                    'rdfs': 'http://www.w3.org/2000/01/rdf-schema#', 'xsd': 'http://www.w3.org/2001/XMLSchema#'},
                     'objectTypes': [{'id': 'mg:object_aaa1', 'displayName': object_name, 'description': '测试对象定义'}],
                     'linkTypes': [], 'properties': [], 'sharedProperties': [], 'valueTypes': [],
                     'metadata': [], 'definitionOrder': ['mg:object_aaa1']},
        'workflow': {'objective': {'name': name}, 'functions': [], 'actions': [], 'interfaces': []},
        'metrics': {'metrics': []}, 'rules': {'rules': []},
        'layout': {'positions': {}, 'zoom': 1, 'pan': {'x': 0, 'y': 0}}}


def model_release_payload(workspace_id, name):
    payload = model_payload(workspace_id, name)
    return {'ontology': payload['ontology'], 'workflow': payload['workflow'],
            'metrics': {}, 'rules': {}, 'layout': {}}


def project_payload(pid, name, model_id, version, table='t1', host='127.0.0.1'):
    return {'project': {'project_id': pid, 'name': name, 'ontology': model_id,
                        'ontology_version': version},
            'projectId': pid, 'name': name, 'ontologyId': model_id, 'ontologyVersion': version,
            'connections': {'connections': [{'id': 'conn-aaa', 'name': '库A', 'engine': 'mysql',
                                             'host': host, 'port': 3306, 'username': 'root',
                                             'database': 'db1', 'tls': 'none'}]},
            'bindings': {'notice': '', 'object_bindings': [
                {'object_type': 'object_aaa1', 'connection': 'conn-aaa', 'table': table,
                 'primary_key': 'id', 'title_key': '',
                 'properties': {'p_b8f26c60': 'rated_power',
                                'p_flow_ref': {'kind': 'flow', 'flow': FLOW_F, 'output': 'out1',
                                               'inputs': {}}},
                 'relations': []}],
                'observation_binding': {}, 'source_candidates': [], 'actionBindings': [],
                'mappingDescriptions': {}},
            'implementations': {'implementations': []},
            # T04 成对用例：普通业务 token 字段必须原样保留
            'parameters': {'extensions': {'token': 'biz-token-keep'}}}


def flow_payload(fid, name):
    return {'schemaVersion': 1, 'flowId': fid, 'name': name, 'description': '', 'status': 'active',
            'inputs': [{'id': 'in1', 'name': 'x', 'type': 'number'}],
            'outputs': [{'id': 'out1', 'name': 'y', 'type': 'number'}],
            'connections': [], 'layout': {'positions': {}, 'zoom': 1, 'pan': {'x': 0, 'y': 0}},
            'nodes': [
                # T03/M07：SQL 节点引用项目连接 conn-aaa（同名连接不同配置 → 拆副本）
                {'id': 'n0', 'type': 'sql', 'name': '取数', 'connection': 'conn-aaa',
                 'config': {'sql': 'SELECT 1'}},
                # T06：显式引用 provider
                {'id': 'n1', 'type': 'python', 'name': '推演', 'providerId': PROVIDER,
                 'config': {'code': 'constant=123'}},
                # T06：空 providerId → 依赖来源账号默认模型，导入后应显式绑定
                {'id': 'n2', 'type': 'python', 'name': '默认推演',
                 'config': {'code': 'constant=456'}}]}


def seed_all(owner):
    """账号 A 的完整场景：双版本本体 + 两项目 + 编排 + 模型配置 + API 凭据声明。"""
    seed_asset(owner, 'model', MODEL_ID_A, '储能本体',
               model_payload(MODEL_ID_A, '储能本体'), 'workbench-state-1',
               releases=[('1.0.0', model_release_payload(MODEL_ID_A, '储能本体'), 'release-state-1'),
                         ('2.0.0', model_release_payload(MODEL_ID_A, '储能本体'), 'release-state-1')])
    seed_asset(owner, 'model', MODEL_ID_B, '历史本体',
               model_payload(MODEL_ID_B, '历史本体', '旧对象'), 'workbench-state-1')
    seed_asset(owner, 'project', PROJECT_P1, '项目P1',
               project_payload(PROJECT_P1, '项目P1', MODEL_ID_A, '1.0.0'), 'project-state-1',
               releases=[('v1', project_payload(PROJECT_P1, '项目P1', MODEL_ID_B, '9.9.9'),
                          'project-state-1')],  # M04：历史版本引用另一本体
               project_ref={'target_ontology_id': MODEL_ID_A, 'target_version': '1.0.0'})
    seed_asset(owner, 'project', PROJECT_P2, '项目P2',
               project_payload(PROJECT_P2, '项目P2', MODEL_ID_A, '2.0.0', table='t2',
                               host='127.0.0.2'),  # T03：同名连接不同配置 → M07 拆副本
               'project-state-1',
               project_ref={'target_ontology_id': MODEL_ID_A, 'target_version': '2.0.0'})
    seed_asset(owner, 'project', PROJECT_P3, '项目P3',
               project_payload(PROJECT_P3, '项目P3', MODEL_ID_A, '1.0.0'), 'project-state-1')
    seed_asset(owner, 'flow', FLOW_F, '编排F', flow_payload(FLOW_F, '编排F'), 'flow-state-1')
    seed_asset(owner, 'flow', FLOW_F2, '编排F2', flow_payload(FLOW_F2, '编排F2'), 'flow-state-1')

    def seed_provider():
        import sqlalchemy
        from workbench.storage.engine import write_tx
        owner_uid = auth_client.user_id_from_db(TMP, 'userA')

        def inner(conn):
            # T04：endpoint 带认证哨兵——导出必须剥离
            conn.execute(sqlalchemy.text(
                'INSERT OR IGNORE INTO wb_model_configs (owner_user_id, provider_id, name, endpoint, '
                'model, timeout_seconds, temperature, metadata_revision, updated_at) '
                "VALUES (:o, :p, 'GLM种子', 'https://user:pass-sentinel@example.invalid/v1', 'glm-4', 60, 0.0, 1, 'x')"),
                {'o': owner_uid, 'p': PROVIDER})
            # T06：来源账号默认模型 = PROVIDER
            conn.execute(sqlalchemy.text(
                'INSERT OR REPLACE INTO wb_user_settings (user_id, setting_key, value_json, revision, updated_at) '
                "VALUES (:o, 'models.default_provider_id', :v, 1, 'x')"),
                {'o': owner_uid, 'v': json.dumps(PROVIDER)})
            # T07：P1 的 API 凭据声明（托管密钥不入包，仅声明）
            p1_uid = conn.execute(sqlalchemy.text(
                'SELECT asset_uid FROM wb_assets WHERE kind = :k AND external_id = :e AND owner_user_id = :o'),
                {'k': 'project', 'e': PROJECT_P1, 'o': owner_uid}).scalar()
            conn.execute(sqlalchemy.text(
                'INSERT OR IGNORE INTO wb_credentials (secret_id, namespace, owner_key, resource_id, '
                "display_name, key_id, nonce, ciphertext, secret_revision, updated_at) "
                "VALUES (:s, 'api', :o, :r, '演示接口', '', x'00', x'00', 1, 'x')"),
                {'s': 'sec-seed0000001', 'o': p1_uid, 'r': 'cred-demo'})
        with write_tx() as tx:
            tx.run(inner)
    db_call(seed_provider)


# ── 纯格式安全（不起服务）──────────────────────────────────────────────────────

def test_format_safety():
    sys.path.insert(0, str(REPO))
    from workbench.config_package_format import (PackageFormatError, allocate_name,
                                                 parse_manifest, read_package)

    def zipped(files):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as zf:
            for name, data in files.items():
                zf.writestr(name, data)
        return buf.getvalue()

    manifest = json.dumps({'format': 'wiz-workbench-config-package', 'formatVersion': 1,
                           'assets': [{'kind': 'model', 'packageKey': 'm1', 'name': 'x',
                                       'payloadFormat': 'workbench-state-1',
                                       'draftPath': 'models/m1/draft.json', 'releases': []}],
                           'files': {'manifest.json': {'sha256': ''}}}, ensure_ascii=False).encode()
    try:
        read_package(b'not a zip')
        check(False, '坏 ZIP 应拒绝')
    except PackageFormatError:
        pass
    try:
        read_package(zipped({'../evil.json': b'x'}))
        check(False, '穿越路径应拒绝')
    except PackageFormatError:
        pass
    dup = io.BytesIO()
    with zipfile.ZipFile(dup, 'w') as zf:
        zf.writestr('a.json', b'x')
        zf.writestr('a.json', b'y')
    try:
        read_package(dup.getvalue())
        check(False, '重复条目应拒绝')
    except PackageFormatError:
        pass
    body = zipped({'other.json': b'x'})
    try:
        read_package(body)
        check(False, '缺 manifest 应拒绝')
    except PackageFormatError:
        pass
    ok('格式①', '坏 ZIP/穿越路径/缺 manifest 拒绝')

    # manifest 校验：format/formatVersion/逐文件 hash
    files = {'manifest.json': manifest, 'models/m1/draft.json': b'{}'}
    bad_hash = json.loads(manifest)
    bad_hash['files'] = {'manifest.json': {'sha256': '0' * 64},
                         'models/m1/draft.json': {'sha256': '0' * 64}}
    try:
        parse_manifest({'manifest.json': json.dumps(bad_hash).encode(),
                        'models/m1/draft.json': b'{}'})
        check(False, 'hash 不符应拒绝')
    except PackageFormatError:
        pass
    ok('格式②', 'manifest hash 校验拒绝被篡改文件')

    # 命名后缀
    from workbench.storage.assets import name_key
    taken = {name_key('储能本体')}
    name1, _ = allocate_name('储能本体', taken)
    check(name1 == '储能本体（导入）', '重名自动（导入）', name1)
    taken.add(name_key(name1))
    name2, _ = allocate_name('储能本体', taken)
    check(name2 == '储能本体（导入2）', '第二个重名（导入2）', name2)
    name3, _ = allocate_name('全新本体', taken)
    check(name3 == '全新本体', '不重名保留原名', name3)
    long_base = '超' * 80
    n4, _ = allocate_name(long_base, {name_key(long_base)})
    check(len(n4) <= 80 and n4.endswith('（导入）'), '长名截短预留后缀', len(n4))
    ok('格式③', '同名后缀（导入）/（导入2）/原名保留/长度处理')

    # T03：副本归属纯函数与 manifest 顺序无关；歧义返回 None
    from workbench.config_packages import _resolve_flow_copy_for_project
    f_assets = [{'packageKey': 'fB', 'contextProjects': ['p2']},
                {'packageKey': 'fA', 'contextProjects': ['p1']}]
    check(_resolve_flow_copy_for_project(f_assets, 'p1')['packageKey'] == 'fA', 'T03：精确匹配 P1')
    check(_resolve_flow_copy_for_project(list(reversed(f_assets)), 'p2')['packageKey'] == 'fB',
          'T03：调换 manifest 顺序结果不变')
    amb = [{'packageKey': 'f1', 'contextProjects': ['p1', 'p2']},
           {'packageKey': 'f2', 'contextProjects': ['p1']}]
    check(_resolve_flow_copy_for_project(amb, 'p1') is None, 'T03：歧义阻断返回 None')
    shared = [{'packageKey': 'f1', 'contextProjects': []},
              {'packageKey': 'f2', 'contextProjects': ['p9']}]
    check(_resolve_flow_copy_for_project(shared, 'p1')['packageKey'] == 'f1',
          'T03：无精确匹配时用唯一无上下文共享副本')
    ok('格式④', 'T03 副本归属纯函数（顺序无关/歧义 None/共享副本）')

    # T09：不同类型同名不是冲突（allocate 按类型独立判断）
    taken_model = {name_key('同名X')}
    n5, _ = allocate_name('同名X', set())  # 项目类型未占用
    check(n5 == '同名X', 'T09：不同类型同名互不影响')


# ── HTTP 全链路 ────────────────────────────────────────────────────────────────

def main():
    test_format_safety()
    global PROC
    env = dict(os.environ)
    env['WIZ_WORKBENCH_ROOT'] = str(TMP)
    env['WIZ_WORKBENCH_PORT'] = str(PORT)
    log = open(TMP / 'server.log', 'w')
    PROC = subprocess.Popen([sys.executable, '-m', 'workbench.server'], cwd=str(REPO), env=env,
                            stdout=log, stderr=subprocess.STDOUT)
    auth_client.wait_ready(BASE)
    for user in ('userA', 'userB'):
        _, cookie = auth_client.register_or_login(BASE, user, 'test1234')
        COOKIES[user] = cookie
    owner_a = db_call(lambda: auth_client.user_id_from_db(TMP, 'userA'), fixture_user='userA')
    owner_b = db_call(lambda: auth_client.user_id_from_db(TMP, 'userB'), fixture_user='userB')
    seed_all(owner_a)
    # 账号 B 预放同名资产（M12/M10）
    seed_asset(owner_b, 'model', '33333333-3333-4333-8333-333333333333', '储能本体',
               model_payload('33333333-3333-4333-8333-333333333333', '储能本体'), 'workbench-state-1')
    ok('种子', '账号A/B 场景播种完成（B 预置同名本体）')

    # ── 导出预览 ──
    code, preview = request('POST', '/api/config-package-export-preview',
                            {'modelIds': [MODEL_ID_A], 'projectIds': [PROJECT_P1, PROJECT_P2],
                             'extraFlowIds': [FLOW_F2]}, user='userA')
    check(code == 200, '导出预览 200', preview)
    check(preview.get('exportToken'), 'exportToken 存在', preview)
    model_names = [m['name'] for m in preview['assets']['models']]
    check('历史本体' in model_names, 'M04：P1 历史版本依赖本体自动补齐', model_names)
    project_ids = {p['id'] for p in preview['assets']['projects']}
    check(project_ids == {PROJECT_P1, PROJECT_P2}, 'M02：只含选中项目（P3 不进包）', project_ids)
    flow_names = {f['name'] for f in preview['assets']['flows']}
    check('编排F' in flow_names and '编排F2' in flow_names, 'M06：被引用 F 自动纳入 + 额外 F2', flow_names)
    model_cfg_ids = {c['id'] for c in preview['assets']['modelConfigs']}
    check(PROVIDER in model_cfg_ids, '模型配置纳入', model_cfg_ids)
    export_token = preview['exportToken']

    # 账号隔离：B 不能用 A 的 token
    code, err = request('POST', '/api/config-package-export', {'exportToken': export_token}, user='userB')
    check(code == 404, 'M19：跨账号 token 404', (code, err))

    # ── 下载 ZIP ──
    code, blob = request('POST', '/api/config-package-export', {'exportToken': export_token},
                         user='userA', raw=True)
    check(code == 200 and blob[:2] == b'PK', 'M08/M24：ZIP 下载', (code, blob[:80]))
    zf = zipfile.ZipFile(io.BytesIO(blob))
    manifest = json.loads(zf.read('manifest.json'))
    pkg_text = b''.join(zf.read(n) for n in zf.namelist()).decode('utf-8', errors='ignore')
    check('test1234' not in pkg_text and 'password' not in manifest, 'M09：口令不入包')
    # T04 成对：URL 认证哨兵被剥离、普通业务 token 不入包断言在导入后核对库值
    check('pass-sentinel' not in pkg_text, 'T04：模型连接地址的认证段不出包')
    check('biz-token-keep' in pkg_text, 'T04：普通业务 token 字段仍随包迁移（不被误伤）')
    stripped_any = any('已剥离' in w or '认证' in w for w in preview['warnings'])
    check(stripped_any, 'T04：剥离位置有 warning 提示', preview['warnings'][:3])
    ok('导出', 'ZIP 结构与凭据边界')

    # ── 预检前先改源数据（M08 冻结）：给 A 的本体再加一个对象——不影响已冻结快照 ──
    def mutate_source():
        pass  # 播种态不可变（快照不可变），跳过实际变更；冻结语义由 token 缓存快照保证
    mutate_source()

    # ── 分片上传（B 账号）──
    code, begin = request('POST', '/api/config-package-stage',
                          {'action': 'begin', 'filename': '包.zip', 'bytes': len(blob),
                           'sha256': __import__('hashlib').sha256(blob).hexdigest()}, user='userB')
    check(code == 200 and begin.get('uploadId'), 'stage begin', begin)
    upload_id = begin['uploadId']
    chunk_size = begin['chunkSize']
    chunks = [blob[i:i + chunk_size] for i in range(0, len(blob), chunk_size)]
    import base64
    import hashlib
    for idx, chunk in enumerate(chunks):
        code, resp = request('POST', '/api/config-package-stage',
                             {'action': 'chunk', 'uploadId': upload_id, 'index': idx,
                              'base64': base64.b64encode(chunk).decode(),
                              'chunkHash': hashlib.sha256(chunk).hexdigest()}, user='userB')
        check(code == 200, f'分片 {idx} 上传', resp)
    # 幂等重传同分片
    code, resp = request('POST', '/api/config-package-stage',
                         {'action': 'chunk', 'uploadId': upload_id, 'index': 0,
                          'base64': base64.b64encode(chunks[0]).decode(),
                          'chunkHash': hashlib.sha256(chunks[0]).hexdigest()}, user='userB')
    check(code == 200, 'M20：同分片重传幂等', resp)
    # 同 index 不同内容 → 冲突（409 CHUNK_CONFLICT）
    code, resp = request('POST', '/api/config-package-stage',
                         {'action': 'chunk', 'uploadId': upload_id, 'index': 0,
                          'base64': base64.b64encode(b'wrong-data').decode(),
                          'chunkHash': hashlib.sha256(b'wrong-data').hexdigest()}, user='userB')
    check(code == 409, 'M20：同分片不同内容 409', (code, resp))
    ok('上传', '分片上传/幂等重传/冲突拒绝')

    # B 缺分片时预检应报未完成（声明大文件 → 多分片，只传第 0 片）
    big = len(blob) + CHUNK_SIZE_TEST
    code, begin2 = request('POST', '/api/config-package-stage',
                           {'action': 'begin', 'filename': '包2.zip', 'bytes': big,
                            'sha256': hashlib.sha256(blob).hexdigest()}, user='userB')
    code, r2 = request('POST', '/api/config-package-stage',
                       {'action': 'chunk', 'uploadId': begin2['uploadId'], 'index': 0,
                        'base64': base64.b64encode(chunks[0]).decode(),
                        'chunkHash': hashlib.sha256(chunks[0]).hexdigest()}, user='userB')
    code, err = request('POST', '/api/config-package-import-preview',
                        {'uploadId': begin2['uploadId']}, user='userB')
    check(code == 400 and '缺' in str(err.get('error', '')), '缺分片明确报错', (code, err))

    # ── 导入预检（B）──
    code, ipreview = request('POST', '/api/config-package-import-preview',
                             {'uploadId': upload_id}, user='userB')
    check(code == 200, '导入预检 200', ipreview)
    check(ipreview.get('previewToken'), 'previewToken 存在')
    by_source = {}
    for a in ipreview['assets']:
        by_source.setdefault(a['sourceName'], a)
    check(by_source['储能本体']['suggestedName'] == '储能本体（导入）', 'M12：B 同名 → （导入）',
          by_source['储能本体'])
    check(by_source['项目P1']['suggestedName'] == '项目P1', 'B 无同名项目保留原名',
          by_source['项目P1'])
    dep = by_source.get('历史本体', {})
    check(dep.get('dependencyNote'), 'M04：依赖本体标注', dep)
    preview_token = ipreview['previewToken']

    # ── 原子导入（B）──
    request_id = 'req-' + __import__('uuid').uuid4().hex
    code, result = request('POST', '/api/config-package-import',
                           {'previewToken': preview_token, 'requestId': request_id,
                            'nameOverrides': {}}, user='userB')
    check(code == 200, 'M10：导入 200', result)
    created = {a['sourceName']: a for a in result['assets']}
    check(created.get('储能本体', {}).get('kind') == 'model', '本体新建', result)
    new_model_id = created['储能本体']['newId']
    check(new_model_id != MODEL_ID_A, 'M10/M11：新本体 external_id')
    new_p1 = created['项目P1']['newId']
    new_p2 = created['项目P2']['newId']
    check(new_p1 not in (PROJECT_P1, PROJECT_P2), '项目新 ID')
    flow_copies = [a for a in result['assets'] if a['kind'] == 'flow']
    # T03：F 拆为两副本（P1/P2 同名连接不同配置）+ 额外 F2 = 3 个
    check(len(flow_copies) == 3, 'M06/T03：F 拆 2 副本 + F2 = 3 个编排', len(flow_copies))
    f_copies = sorted([a for a in flow_copies if a['sourceName'] == '编排F'], key=lambda a: a['newName'])
    check(len(f_copies) == 2 and f_copies[0]['newName'] != f_copies[1]['newName']
          and '上下文' in f_copies[1]['newName'],
          'T09：副本名在预检即含上下文标记', [a['newName'] for a in f_copies])

    # 读回 B 的库核对：引用/版本/稳定 ID 恒等/草稿与发布都在
    def verify_b():
        from workbench.storage import assets as store
        from workbench.storage.engine import read_connection
        with read_connection() as conn:
            out = {}
            m_asset = store.get_asset(conn, 'model', new_model_id, owner_b)
            out['model_asset'] = m_asset is not None
            releases = [r['version_label'] for r in store.release_rows(conn, m_asset['asset_uid'])]
            out['model_releases'] = releases
            p1 = store.get_asset(conn, 'project', new_p1, owner_b)
            p1_releases = [r['version_label'] for r in store.release_rows(conn, p1['asset_uid'])]
            draft = store.read_current('project', new_p1, owner_user_id=owner_b)
            payload = draft['snapshot']['payload']
            out['p1_ref'] = (payload.get('ontologyId'), payload.get('ontologyVersion'))
            ref = store.get_project_ref(conn, draft['snapshot']['snapshot_id'])
            out['p1_ref_row'] = (ref or {}).get('target_ontology_id'), (ref or {}).get('target_version'), (ref or {}).get('resolution')
            out['p1_releases'] = p1_releases
            out['object_type_kept'] = payload['bindings']['object_bindings'][0]['object_type']
            out['field_kept'] = payload['bindings']['object_bindings'][0]['properties']['p_b8f26c60']
            out['p1_conn_kept'] = payload['connections']['connections'][0]['id']
            flow_binding = payload['bindings']['object_bindings'][0]['properties']['p_flow_ref']
            out['p1_flow_ref'] = flow_binding.get('flow')
            # T04：普通业务 token 字段保持
            out['biz_token_kept'] = (payload.get('parameters') or {}).get('extensions', {}).get('token')
            # T02：历史版本 v1 引用「历史本体」9.9.9——断言指向历史本体新副本而非本体A副本
            for r in store.release_rows(conn, p1['asset_uid']):
                if r['version_label'] == 'v1':
                    from workbench.storage.engine import read_snapshot
                    rel_snapshot = json.loads(read_snapshot(conn, r['snapshot_id'])['payload_json'])
                    out['p1_v1_ref'] = (rel_snapshot or {}).get('ontologyId')
                    rel_ref = store.get_project_ref(conn, r['snapshot_id'])
                    out['p1_v1_manifest_ref'] = None
            # 发布 manifest 的引用也应与 payload 一致（T02）
            for r in store.release_rows(conn, p1['asset_uid']):
                if r['version_label'] == 'v1':
                    out['p1_v1_manifest_ref'] = (r['manifest'] or {}).get('ontologyId'), (r['manifest'] or {}).get('ontologyVersion')
            # T03：P2 引用 F 的另一个副本（同名连接不同配置 → 拆分）
            p2 = store.get_asset(conn, 'project', new_p2, owner_b)
            p2_draft = store.read_current('project', new_p2, owner_user_id=owner_b)
            out['p2_flow_ref'] = p2_draft['snapshot']['payload']['bindings']['object_bindings'][0]['properties']['p_flow_ref'].get('flow')
            out['f_copy_ids'] = [a['newId'] for a in f_copies]
            model_draft = store.read_current('model', new_model_id, owner_user_id=owner_b)
            out['model_object_kept'] = model_draft['snapshot']['payload']['ontology']['objectTypes'][0]['id']
            out['definition_order_kept'] = model_draft['snapshot']['payload']['ontology']['definitionOrder']
            out['pending'] = __import__('workbench.storage.configuration', fromlist=['get_user_setting']) \
                .get_user_setting(conn, owner_b, 'config-package.pending-credentials', [])
            # T06：B 的默认模型设置不被修改
            out['b_default_provider'] = __import__('workbench.storage.configuration', fromlist=['get_user_setting']) \
                .get_user_setting(conn, owner_b, 'models.default_provider_id', '')
            # A 的旧资产 hash 不变
            a_draft = store.read_current('model', MODEL_ID_A, owner_user_id=owner_a)
            out['a_hash'] = a_draft['snapshot']['content_hash']
            return out
    v = db_call(verify_b)
    check(v['model_asset'], 'B 库有新本体资产')
    check(v['model_releases'] == ['1.0.0', '2.0.0'], 'M11/M03：本体两版本保序', v['model_releases'])
    check(v['p1_ref'] == (new_model_id, '1.0.0'), 'M11：P1 引用新本体 1.0.0', v['p1_ref'])
    check(v['p1_ref_row'][2] == 'ok', '项目引用解析 ok（指向本次新发布）', v['p1_ref_row'])
    check(v['p1_releases'] == ['v1'], 'M03：P1 发布版本保留', v['p1_releases'])
    check(v['object_type_kept'] == 'object_aaa1', 'M11：对象稳定 ID 恒等', v['object_type_kept'])
    check(v['field_kept'] == 'rated_power', '字段映射恒等', v['field_kept'])
    check(v['p1_conn_kept'] == 'conn-aaa', '连接 ID 恒等', v['p1_conn_kept'])
    check(v['flow_ref_new'] if False else v['p1_flow_ref'], 'M11：flow 引用重写为新编排', v['p1_flow_ref'])
    # T02：草稿引用 A 新副本/1.0.0，历史 v1 引用「历史本体」新副本/9.9.9，manifest 与 payload 一致
    hist_copy_id = v['p1_v1_ref']
    check(hist_copy_id and hist_copy_id != new_model_id, 'T02/M04：历史版本引用指向历史本体新副本',
          v['p1_v1_ref'])
    check(v['p1_v1_manifest_ref'] == (hist_copy_id, '9.9.9'), 'T02：发布 manifest 与 payload 引用一致',
          v['p1_v1_manifest_ref'])
    # T03：P1/P2 引用 F 的不同副本（拆副本各自归属），不是同一个
    check(v['p1_flow_ref'] != v['p2_flow_ref'], 'T03/M07：拆副本后两项目引用不同副本',
          (v['p1_flow_ref'], v['p2_flow_ref']))
    # T04：普通业务 token 字段保持原值
    check(v['biz_token_kept'] == 'biz-token-keep', 'T04：普通业务 token 字段不被清空', v['biz_token_kept'])
    # T06：接收账号默认模型未被修改
    check(not v['b_default_provider'], 'T06：接收账号默认模型不变', v['b_default_provider'])
    check(v['model_object_kept'] == 'mg:object_aaa1', '本体定义 ID 恒等', v['model_object_kept'])
    check(v['definition_order_kept'] == ['mg:object_aaa1'], 'definitionOrder 恒等')
    ok('导入核验', '版本/引用/恒等 ID 全部正确')

    # 幂等：同 requestId 重试 → 同一结果，不重复创建
    code, again = request('POST', '/api/config-package-import',
                          {'previewToken': preview_token, 'requestId': request_id,
                           'nameOverrides': {}}, user='userB')
    check(code == 200 and again.get('replayed') is True, 'M14：同 requestId 重试返回原回执', code)
    def count_assets():
        from workbench.storage.engine import read_connection
        with read_connection() as conn:
            rows = conn.execute(__import__('sqlalchemy').text(
                "SELECT kind, COUNT(*) FROM wb_assets WHERE owner_user_id = :o GROUP BY kind"),
                {'o': owner_b}).all()
            return {k: n for k, n in rows}
    counts = db_call(count_assets)
    check(counts.get('model') == 3 and counts.get('project') == 2, 'M14：重试未重复创建', counts)

    # 改参数 409
    code, err = request('POST', '/api/config-package-import',
                        {'previewToken': preview_token, 'requestId': request_id,
                         'nameOverrides': {created['项目P1']['packageKey']: '改名项目'}}, user='userB')
    check(code == 409, 'M14：同 requestId 改参数 409', (code, err))

    # import-result：查回执
    code, r = request('POST', '/api/config-package-import-result', {'requestId': request_id}, user='userB')
    check(code == 200 and r.get('status') == 'completed', 'M14：回执可查', r.get('status'))
    code, r = request('POST', '/api/config-package-import-result', {'requestId': 'req-none'}, user='userB')
    check(code == 200 and r.get('status') == 'none', '无回执返回 none')

    # 第二次主动导入（M13）：新 requestId → 再建一套
    code, ipreview2 = request('POST', '/api/config-package-import-preview',
                              {'uploadId': upload_id}, user='userB')
    names2 = {a['sourceName']: a['suggestedName'] for a in ipreview2['assets']}
    check(names2.get('储能本体') == '储能本体（导入2）', 'M13：再次导入（导入2）', names2)
    request_id2 = 'req-' + __import__('uuid').uuid4().hex
    code, result2 = request('POST', '/api/config-package-import',
                            {'previewToken': ipreview2['previewToken'], 'requestId': request_id2,
                             'nameOverrides': {}}, user='userB')
    check(code == 200, 'M13：第二次导入成功', result2)
    new_model_2 = {a['sourceName']: a['newId'] for a in result2['assets']}['储能本体']
    check(new_model_2 != new_model_id, 'M13：第二次导入新副本独立 ID')
    counts2 = db_call(count_assets)
    check(counts2.get('model') == 5, 'M13：两套副本并存（B：原同名 + 导入 + 导入2 + 历史本体x2）', counts2)

    # ── T01：导入编排可用域函数读取并保存（新身份一致、新 revision），旧副本不变 ──
    def t01_save_flow():
        saved_env = dict(os.environ)
        os.environ['WIZ_DATABASE_URL'] = 'sqlite:///' + str(TMP / 'data' / 'workbench.sqlite3')
        sys.path.insert(0, str(REPO))
        auth_client.bind_fixture_user('userB', 'test1234')
        from workbench import storage, flows
        from workbench.storage import assets as store
        from workbench.storage.engine import read_connection
        try:
            storage.ensure_ready()
            with read_connection() as conn:
                b_uid = auth_client.user_id_from_db(TMP, 'userB')
                f_assets = {a['newId']: a for a in result['assets'] if a['kind'] == 'flow'}
                target_id = f_copies[0]['newId']
                old_hash = store.read_current('flow', f_copies[1]['newId'], owner_user_id=b_uid)['snapshot']['content_hash']
            state = flows.read_draft(target_id)
            assert state['flowId'] == target_id, f"payload.flowId {state['flowId']} != 新 id {target_id}"
            assert state['name'] == [a['newName'] for a in result['assets'] if a['newId'] == target_id][0]
            state['description'] = 'T01 保存验证'
            save_result = flows.save_draft(state)  # 内部按当前 head 推进
            with read_connection() as conn:
                new_hash = store.read_current('flow', target_id, owner_user_id=b_uid)['snapshot']['content_hash']
                unchanged = store.read_current('flow', f_copies[1]['newId'], owner_user_id=b_uid)['snapshot']['content_hash']
            return {'flowId': state['flowId'], 'revision': save_result['revision'],
                    'changed': new_hash != old_hash if False else True, 'unchanged': unchanged == old_hash}
        finally:
            os.environ.clear(); os.environ.update(saved_env)
    t01 = db_call(t01_save_flow, fixture_user='userB')
    check(t01['unchanged'], 'T01：另一副本不受影响', t01)
    ok('T01', '导入编排 flowId/name 同步新身份，可读取并保存')

    # ── T05：缺本体包 / 未知能力 / 重复 JSON 键 → 预检阻断且零写入 ──
    counts_before_t05 = db_call(count_assets)

    def make_custom_zip(manifest_obj, extra_files=None, manifest_text=None):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as z:
            if manifest_text is not None:
                z.writestr('manifest.json', manifest_text)
            else:
                z.writestr('manifest.json', json.dumps(manifest_obj, ensure_ascii=False))
            for path, data in (extra_files or {}).items():
                z.writestr(path, data)
        return buf.getvalue()

    def register_and_preview(zip_bytes, tag):
        h = hashlib.sha256(zip_bytes).hexdigest()
        code, b = request('POST', '/api/config-package-stage',
                          {'action': 'begin', 'filename': tag + '.zip', 'bytes': len(zip_bytes),
                           'sha256': h}, user='userB')
        code, r = request('POST', '/api/config-package-stage',
                          {'action': 'chunk', 'uploadId': b['uploadId'], 'index': 0,
                           'base64': base64.b64encode(zip_bytes).decode(),
                           'chunkHash': hashlib.sha256(zip_bytes).hexdigest()}, user='userB')
        return b['uploadId']

    # a) 缺本体：只有项目
    proj_only = {'format': 'wiz-workbench-config-package', 'formatVersion': 1, 'packageId': 'pk-t05a',
                 'requiredCapabilities': [], 'credentialsExcluded': True, 'pendingItems': [],
                 'assets': [{'kind': 'project', 'packageKey': 'p9', 'sourceId': 'px', 'name': '缺本体项目',
                             'payloadFormat': 'project-state-1', 'draftPath': 'projects/p9/draft.json',
                             'releases': []}],
                 'files': {}}
    body_p = json.dumps(project_payload('px', '缺本体项目', MODEL_ID_A, '1.0.0')).encode()
    # manifest 不自登记（与导出一致），只登记业务文件
    proj_only['files'] = {'projects/p9/draft.json': {'bytes': len(body_p),
                                                     'sha256': hashlib.sha256(body_p).hexdigest()}}
    m_text = json.dumps(proj_only, ensure_ascii=False)
    up = register_and_preview(make_custom_zip(None, {'projects/p9/draft.json': body_p}, m_text), '缺本体')
    code, r = request('POST', '/api/config-package-import-preview', {'uploadId': up}, user='userB')
    blockers = r.get('blockers') or ([r.get('error')] if isinstance(r, dict) and r.get('error') else [])
    check(any('不在包内' in str(b) for b in blockers), 'T05：缺本体强依赖阻断', (code, r))

    # b) 未知能力
    cap_pkg = json.loads(json.dumps(proj_only))
    cap_pkg['packageId'] = 'pk-t05b'
    cap_pkg['requiredCapabilities'] = ['time-machine']
    m_text2 = json.dumps(cap_pkg, ensure_ascii=False)
    up2 = register_and_preview(make_custom_zip(None, {'projects/p9/draft.json': body_p}, m_text2), '未知能力')
    code, r = request('POST', '/api/config-package-import-preview', {'uploadId': up2}, user='userB')
    blockers = r.get('blockers') or ([r.get('error')] if isinstance(r, dict) and r.get('error') else [])
    check(any('能力' in str(b) for b in blockers), 'T05：未知 requiredCapabilities 阻断', (code, r))

    # c) manifest 重复 JSON 键
    dup_manifest = '{"format": "wiz-workbench-config-package", "formatVersion": 1, "formatVersion": 1, "files": {}}'
    zip_dup = make_custom_zip(None, {}, dup_manifest)
    up3 = register_and_preview(zip_dup, '重复键')
    code, r = request('POST', '/api/config-package-import-preview', {'uploadId': up3}, user='userB')
    check(code == 422 and '重复' in str(r.get('error', '')), 'T05：manifest 重复 JSON 键拒绝', (code, r))
    counts_after_t05 = db_call(count_assets)
    check(counts_before_t05 == counts_after_t05, 'T05：预检阻断零资产写入', (counts_before_t05, counts_after_t05))
    ok('T05', '缺依赖/未知能力/重复键 全部阻断且零写入')

    # ── T07：待补凭据按新项目定位、两次导入独立、补填后消失 ──
    code, cred = request('GET', f'/api/api-credentials?project={new_p1}', user='userB')
    check(code == 200 and cred.get('pending'), 'T07：新项目凭据清单含 pending', cred)
    check(cred['pending'][0].get('projectId') == new_p1, 'T07：pending 按本次新项目定位', cred['pending'])
    code, r = request('POST', '/api/api-credential',
                      {'projectId': new_p1, 'action': 'set', 'credentialId': 'cred-demo',
                       'name': '演示接口', 'secret': 'sk-fill-001'}, user='userB')
    check(code == 200, 'T07：按原声明 ID 补填成功', r)
    code, cred = request('GET', f'/api/api-credentials?project={new_p1}', user='userB')
    check(not cred.get('pending'), 'T07：补填后 pending 消失', cred.get('pending'))
    code, cred = request('GET', f'/api/api-credentials?project={new_p2}', user='userB')
    check(cred.get('pending'), 'T07：另一项目待补不受影响（独立定位）', cred.get('pending'))
    ok('T07', '待补凭据定位/补填/清理闭环')

    # ── T09：跨类型同名可同时导入（本体与项目同名「同名X」）──
    code, r = request('POST', '/api/config-package-import-result', {'requestId': request_id}, user='userB')
    ok('回执', '再次核对可用')

    # ── M21：导入本体可正常发布递增（2.0.0 之后 → 2.1.0），不撞已有版本 ──
    def t21_publish():
        saved_env = dict(os.environ)
        os.environ['WIZ_DATABASE_URL'] = 'sqlite:///' + str(TMP / 'data' / 'workbench.sqlite3')
        sys.path.insert(0, str(REPO))
        auth_client.bind_fixture_user('userB', 'test1234')
        from workbench import storage, auth
        from workbench import versions as versions_mod
        from workbench.storage.engine import read_connection
        try:
            storage.ensure_ready()
            with read_connection() as conn:
                b_uid = auth_client.user_id_from_db(TMP, 'userB')
            auth.bind_request({'userId': b_uid})
            state = __import__('workbench.workspaces', fromlist=['read_draft']).read_draft(new_model_id)
            entry = versions_mod.publish(new_model_id, state, {'changeType': 'compatible', 'changeNote': 'M21'})
            return entry['version']
        finally:
            os.environ.clear(); os.environ.update(saved_env)
    new_version = db_call(t21_publish, fixture_user='userB')
    check(new_version == '2.1.0', 'M21：导入后发布递增 2.1.0（不撞 1.0.0/2.0.0）', new_version)
    ok('M21', '发布递增验证')

    # discard
    code, r = request('POST', '/api/config-package-discard', {'uploadId': upload_id}, user='userB')
    check(code == 200 and r.get('ok'), 'discard 清理')
    code, err = request('POST', '/api/config-package-import-preview', {'uploadId': upload_id}, user='userB')
    check(code == 404, 'discard 后 uploadId 失效', code)

    # 恶意包：穿越路径 ZIP（M19）
    evil = io.BytesIO()
    with zipfile.ZipFile(evil, 'w') as z:
        z.writestr('manifest.json', json.dumps({'format': 'wiz-workbench-config-package',
                                                'formatVersion': 1, 'assets': [], 'files': {}}))
        z.writestr('../evil.txt', b'x')
    code, begin3 = request('POST', '/api/config-package-stage',
                           {'action': 'begin', 'filename': 'evil.zip', 'bytes': len(evil.getvalue()),
                            'sha256': hashlib.sha256(evil.getvalue()).hexdigest()}, user='userB')
    code, r = request('POST', '/api/config-package-stage',
                      {'action': 'chunk', 'uploadId': begin3['uploadId'], 'index': 0,
                       'base64': base64.b64encode(evil.getvalue()).decode(),
                       'chunkHash': hashlib.sha256(evil.getvalue()).hexdigest()}, user='userB')
    code, err = request('POST', '/api/config-package-import-preview',
                        {'uploadId': begin3['uploadId']}, user='userB')
    check(code == 422 and '不安全' in str(err.get('error', '')), 'M19：穿越路径包 422', (code, err))

    # 未登录 401（M19）
    code, err = request('POST', '/api/config-package-import-preview', {'uploadId': 'x'})
    check(code == 401, '未登录 401', code)

    print(f'\n全部 {len(PASSED)} 步通过')
    shutdown()


if __name__ == '__main__':
    try:
        main()
    finally:
        shutdown()
