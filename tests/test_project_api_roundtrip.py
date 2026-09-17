"""API 级集成测试：新属性来源结构（database 直选表 / Redis 直连）经真实 HTTP 服务的完整链路。

覆盖需求验收：A3（保存/校验/发布往返）、A11（往返无损）、A14（并发 409 保护）、
目录注入与草稿剥离（catalogs 不进 bindings.yaml）、merged() unsupportedSources。

纯 python3 标准库（无 pytest）。隔离规则：
- 真实 ontology/ 只读（复制 releases/models 下已发布版本作为种子）；一切写入只发生在
  mktemp 临时根；服务以 WIZ_WORKBENCH_ROOT=<临时根> WIZ_WORKBENCH_PORT=18800 子进程运行。
- 临时根 releases/models/storage 无版本时：优先用真实基础文件调 ensure_base_release；
  本仓库真实数据没有 models/storage 基础文件、只有工作区发布版本，因此再退一步
  （参照 versions.py 逻辑）直接登记真实发布目录为 storage 的版本。
运行：python3 tests/test_project_api_roundtrip.py
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

import yaml

REPO = Path(__file__).resolve().parents[1]
PORT = 18800
ORIGIN = f'http://127.0.0.1:{PORT}'
BASE = f'http://127.0.0.1:{PORT}'

TMP = Path(tempfile.mkdtemp(prefix='wiz_api_roundtrip_'))
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


# --- 种子（真实 ontology/ 只读，写入仅在临时根）----------------------------------

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
        # 退路：真实基础文件 + ensure_base_release（需要 WIZ_WORKBENCH_ROOT 已指向临时根）
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
    """在临时根副本上把 SOC 属性标为 timeSeries（真实本体只读；本功能发布早于该真实版本）。

    mg:valueShape 只能写在共享定义上：properties.effective() 会丢弃带 sharedPropertyId
    的对象属性自身 FIELDS，写对象属性上不生效，且会违反"继承内容不可覆盖"。
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


# --- 步骤 -----------------------------------------------------------------------

MYSQL_CONN = {'id': 'conn-mysql-01', 'name': '储能业务库', 'engine': 'mysql',
              'host': '127.0.0.1', 'port': 3306, 'database': 'semp_demo'}
REDIS_CONN = {'id': 'conn-redis-01', 'name': '实时缓存', 'engine': 'redis',
              'host': '127.0.0.1', 'port': 6379}


def write_catalog(pid):
    """严格对齐 catalogs.store 的存储结构 {database, tables, refreshedAt}。"""
    directory = TMP / 'ontology/catalogs/projects' / pid
    directory.mkdir(parents=True, exist_ok=True)
    catalog = {'database': 'semp_demo', 'refreshedAt': '2026-09-14T02:00:00+00:00', 'tables': [
        {'name': 'm_storage_cluster_phase', 'kind': 'table', 'fields': [
            {'name': 'id', 'dataType': 'bigint', 'key': 'pri', 'comment': '主键'},
            {'name': 'rated_power', 'dataType': 'double', 'key': '', 'comment': '额定功率'},
            {'name': 'asset_code', 'dataType': 'varchar', 'key': '', 'comment': '资产编码'}]},
        {'name': 'telemetry_sample', 'kind': 'table', 'fields': [
            {'name': 'id', 'dataType': 'bigint', 'key': 'pri', 'comment': '主键'},
            {'name': 'object_id', 'dataType': 'bigint', 'key': '', 'comment': '对象主键'},
            {'name': 'object_type', 'dataType': 'varchar', 'key': '', 'comment': '对象类型编码'},
            {'name': 'sampled_at', 'dataType': 'datetime', 'key': '', 'comment': '采样时间'},
            {'name': 'metric_code', 'dataType': 'varchar', 'key': '', 'comment': '指标编码'},
            {'name': 'value', 'dataType': 'double', 'key': '', 'comment': '采样值'}]}]}
    (directory / 'conn-mysql-01.json').write_text(
        json.dumps(catalog, ensure_ascii=False), encoding='utf-8')


def database_series_config(cluster_api, soc_api):
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
    """通过 /api/version-state 动态读取 storage 1.0.0 的类与属性 apiName。"""
    status, payload = request('GET', f'/api/version-state?ontology=storage&version={version}')
    check(status == 200, f'GET /api/version-state 应 200', actual=(status, payload), expected=200)
    ont = payload['state']['ontology']
    check('objectTypes' in ont, 'version-state 应返回 schema 形态本体', actual=list(ont),
          expected='含 objectTypes/properties')
    classes = ont.get('objectTypes') or []
    cluster = next((c for c in classes if '储能簇' in str(c.get('displayName', ''))), None) or classes[0]
    shared = {s['id']: s for s in ont.get('sharedProperties') or []}

    def shared_of(prop):
        return shared.get(prop.get('sharedPropertyId') or '', {})

    def shape_of(prop):
        value = prop.get('valueShape') or shared_of(prop).get('valueShape')
        if (prop.get('dataType') or shared_of(prop).get('dataType') or {}).get('type') == 'timeSeries':return 'timeSeries'
        return value if value in ('scalar', 'timeSeries') else 'scalar'

    props = [p for p in ont.get('properties') or [] if p.get('objectTypeId') == cluster['id']]
    series = [p for p in props if shape_of(p) == 'timeSeries']
    soc = next((p for p in series
                if 'soc' in str(shared_of(p).get('displayName', '')).casefold()), None)
    if soc is None and series:
        soc = series[0]
    check(soc is not None, '储能簇下应存在 timeSeries 形态属性（种子已标记 SOC）',
          actual=[(p.get('apiName'), shape_of(p)) for p in props], expected='至少一个 timeSeries')
    scalars = [p for p in props if p.get('id') != soc.get('id') and shape_of(p) == 'scalar']
    rated = next((p for p in scalars
                  if '额定' in str(shared_of(p).get('displayName', '') + str(p.get('displayName', '')))), None)
    if rated is None and scalars:
        rated = scalars[0]
    check(rated is not None, '储能簇下应另有 scalar 属性用于 Redis 直连', actual=len(props),
          expected='>=2 个属性')
    print(f'  [读取] 对象类型(储能簇) id={cluster["id"]}')
    print(f'  [读取] timeSeries 属性 apiName={soc["apiName"]} (id={soc["id"]})')
    print(f'  [读取] scalar 属性 apiName={rated["apiName"]} (id={rated["id"]})')
    return cluster['id'].removeprefix('mg:'), soc['apiName'], rated['apiName']


# --- 主流程 ----------------------------------------------------------------------

def main():
    version = seed_release()
    patch_time_series(version)

    # 端口占用预检（SO_REUSEADDR 与服务端 allow_reuse_address 一致，避免 TIME_WAIT 误报）
    probe = socket.socket()
    try:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(('127.0.0.1', PORT))
    except OSError as exc:
        print(f'[失败] 端口 {PORT} 已被占用，无法启动测试实例：{exc}')
        sys.exit(1)
    finally:
        probe.close()

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
            status, payload = request('GET', '/api/ontologies')
            if status == 200:
                ready = True
                break
            time.sleep(0.2)
        except (urllib.error.URLError, ConnectionError, OSError):
            time.sleep(0.2)
    check(ready, '服务未在 20 秒内就绪（/api/ontologies）', actual='未就绪', expected='200')
    ok('0', f'隔离服务已就绪 WIZ_WORKBENCH_ROOT={TMP} 端口 {PORT}')

    # a) 建项目（绑定 storage + 版本）
    status, created = request('POST', '/api/projects',
                              {'name': 'API属性来源往返测试', 'ontology': 'storage', 'version': version})
    check(status == 201, 'POST /api/projects 应 201', actual=(status, created), expected=201)
    pid = created['id']
    check(bool(pid) and created.get('ontologyId') == 'storage'
          and created.get('ontologyVersion') == version,
          '项目应绑定 storage 与引用版本', actual=created, expected={'ontologyId': 'storage', 'ontologyVersion': version})
    ok('a', f'创建项目 pid={pid}（storage @ {version}）')

    # b) 取项目状态与 revision
    status, payload = request('GET', f'/api/project-state?project={pid}')
    check(status == 200 and payload.get('state', {}).get('projectId') == pid,
          'GET /api/project-state 应返回项目状态', actual=(status, payload.get('project')), expected=200)
    state, revision = payload['state'], payload['revision']
    check(bool(revision), '初始 revision 应非空', actual=revision, expected='sha256 十六进制')
    ok('b', f'取得初始 revision={revision[:12]}…')

    # c) 假连接 + 手工目录文件 + 目录注入确认
    state['connections'] = {'connections': [dict(MYSQL_CONN), dict(REDIS_CONN)]}
    status, saved = request('POST', '/api/project-save', {'state': state, 'revision': revision})
    check(status == 200 and saved.get('revision'), '保存连接应 200 并返回新 revision',
          actual=(status, saved), expected=200)
    write_catalog(pid)
    status, payload = request('GET', f'/api/project-state?project={pid}')
    state, revision = payload['state'], payload['revision']
    catalogs = state.get('bindings', {}).get('catalogs', {})
    names = sorted(t.get('name') for t in catalogs.get(MYSQL_CONN['id'], {}).get('tables', []))
    check(MYSQL_CONN['id'] in catalogs and names == ['m_storage_cluster_phase', 'telemetry_sample'],
          '目录文件应被服务端注入 project-state（两表齐全）',
          actual={'连接': list(catalogs), '表': names},
          expected={'连接': [MYSQL_CONN['id']], '表': ['m_storage_cluster_phase', 'telemetry_sample']})
    ok('c', f'假连接已保存（engine=mysql/redis，不发起真实探测）；目录已注入：{names}')

    # d) 从引用版本动态读取类与属性
    object_type, soc_api, rated_api = discover(version)

    # e) 完整配置保存 + 往返无损比对
    db_config = database_series_config(object_type, soc_api)
    redis_config = redis_scalar_config()
    binding = {'object_type': object_type, 'connection': MYSQL_CONN['id'],
               'table': 'm_storage_cluster_phase', 'primary_key': 'id', 'title_key': '',
               'properties': {soc_api: db_config, rated_api: redis_config}, 'relations': []}
    state['bindings']['object_bindings'] = [binding]
    status, report = request('POST', '/api/project-validate', {'state': state})
    check(status == 200 and report.get('errors') == [],
          'project-validate 对完整 database+redis 配置应 0 errors',
          actual={'errors': report.get('errors'), 'warnings': report.get('warnings')},
          expected={'errors': []})
    check(not report.get('warnings'), '目录齐全时不应有元数据 warning（如有请核对目录字段）',
          actual=report.get('warnings'), expected=[])
    status_map = {i['id']: i['status'] for i in report.get('items', [])
                  if i.get('kind') == 'propertySource'}
    check(status_map.get(f'{object_type}.{soc_api}') == 'valid'
          and status_map.get(f'{object_type}.{rated_api}') == 'valid',
          '两个属性来源的校验项均应为 valid', actual=status_map,
          expected={f'{object_type}.{soc_api}': 'valid', f'{object_type}.{rated_api}': 'valid'})
    rev_before_save = revision
    status, saved = request('POST', '/api/project-save', {'state': state, 'revision': revision})
    check(status == 200 and saved.get('revision'), 'project-save 应 200',
          actual=(status, saved), expected=200)
    revision = saved['revision']  # 后续 publish 需携带保存后的最新 revision
    status, payload = request('GET', f'/api/project-state?project={pid}')
    read_back = payload['state']['bindings']['object_bindings'][0]['properties']
    check(read_back == {soc_api: db_config, rated_api: redis_config},
          '往返后 properties 两字段结构应完全一致（A11 无损）',
          actual=read_back, expected={soc_api: db_config, rated_api: redis_config})
    ok('e', f'validate 0 errors → save → 读回深度比对一致（{soc_api}=database timeSeries, {rated_api}=redis scalar）')

    # f) 篡改场景（各自 validate 一次，均应报错）
    def tampered(mutate):
        copied = json.loads(json.dumps(db_config))
        mutate(copied)
        trial = json.loads(json.dumps(state))
        trial['bindings']['object_bindings'][0]['properties'] = {soc_api: copied, rated_api: redis_config}
        return trial

    scenarios = [
        ('f1', '缺当前实例绑定的 match',
         lambda cfg: cfg['lookup'].__setitem__('match',
             [c for c in cfg['lookup']['match'] if c['value']['kind'] != 'identityKey']),
         '绑定当前实例'),
        ('f2', 'timeSeries 漏 timestampField',
         lambda cfg: cfg['result'].pop('timestampField'), '时间字段'),
        ('f3', 'redis 绑 timeSeries 目标属性',
         lambda cfg: None, None),
    ]
    for step, desc, mutate, fragment in scenarios:
        if step == 'f3':
            trial = json.loads(json.dumps(state))
            trial['bindings']['object_bindings'][0]['properties'] = {
                soc_api: redis_config, rated_api: redis_config}
        else:
            trial = tampered(mutate)
        status, report = request('POST', '/api/project-validate', {'state': trial})
        errors = report.get('errors', []) if isinstance(report, dict) else []
        check(status == 200 and errors, f'{desc}：project-validate errors 应非空',
              actual=(status, errors), expected='非空 errors')
        if fragment:
            check(any(fragment in e for e in errors), f'{desc}：错误应包含「{fragment}」',
                  actual=errors, expected=f'包含「{fragment}」')
        ok(step, f'{desc} → validate 拒绝（{errors[0][:40]}…）')
    status, report = request('POST', '/api/project-validate', {'state': state})
    check(status == 200 and report.get('errors') == [], '恢复正确配置后 validate 应重新为 0 errors',
          actual=report.get('errors'), expected=[])
    ok('f', '三种篡改均被校验拒绝，恢复后重新通过')

    # g) 发布 → v1
    status, published = request('POST', '/api/project-publish', {'state': state, 'revision': revision})
    check(status == 200 and published.get('version') == 'v1', 'project-publish 应成功产出 v1',
          actual=(status, published), expected={'version': 'v1'})
    status, releases = request('GET', f'/api/project-releases?project={pid}')
    versions_listed = [item.get('version') for item in releases.get('items', [])]
    check(status == 200 and 'v1' in versions_listed, 'project-releases 应出现 v1',
          actual=versions_listed, expected="包含 'v1'")
    ok('g', f'发布成功：v1（revision={published.get("revision", "")[:12]}…）；releases={versions_listed}')

    # h) 旧 revision 保存 → 409
    status, conflict = request('POST', '/api/project-save', {'state': state, 'revision': rev_before_save})
    check(status == 409, '用过期 revision 保存应 409（A14 并发保护）',
          actual=(status, conflict), expected=409)
    ok('h', f'过期 revision 被拒绝：409 {conflict.get("error", "") if isinstance(conflict, dict) else conflict}')

    # i) catalogs 不进草稿
    revisions_dir = TMP / 'ontology/drafts/projects' / pid / 'revisions'
    files = sorted(revisions_dir.glob('*/bindings.yaml'))
    check(bool(files), '草稿修订目录应存在 bindings.yaml', actual=str(revisions_dir),
          expected='至少一个修订')
    leaked = []
    for path in files:
        data = yaml.safe_load(path.read_text()) or {}
        if isinstance(data, dict) and 'catalogs' in data:
            leaked.append(str(path))
    check(not leaked, '草稿 bindings.yaml 不应包含 catalogs 键', actual=leaked, expected='无 catalogs')
    ok('i', f'{len(files)} 份草稿 bindings.yaml 均无 catalogs 键（目录只存服务端文件）')

    # j) merged() unsupportedSources（demo-objects 未就绪时用 /api/explorer 兜底）
    status, ont_payload = request('GET', '/api/state')
    check(status == 200, 'GET /api/state 应 200', actual=status, expected=200)
    demo_note = ''
    status, demo = request('POST', '/api/demo-objects',
                           {'state': ont_payload['state'], 'projectState': state})
    if status == 422:
        demo_note = 'demo-objects 422（临时根无演示数据：rules/metrics/observation_binding 未配置，demo 未就绪），按预案改用 /api/explorer'
        print(f'  [跳过] {demo_note}')
    source = None
    if status == 200 and isinstance(demo, dict) and demo.get('bindings', {}).get('unsupportedSources'):
        source = demo['bindings']['unsupportedSources']
    else:
        status, snap = request('POST', '/api/explorer',
                               {'state': ont_payload['state'], 'projectState': state})
        check(status == 200, '/api/explorer 应 200', actual=(status, snap), expected=200)
        source = (snap.get('bindings') or {}).get('unsupportedSources')
    kinds = sorted({entry.get('kind') for entry in (source or [])})
    check(kinds == ['database', 'redis'], 'unsupportedSources 应同时包含 database 与 redis 两类',
          actual=source, expected={'kind': ['database', 'redis']})
    ok('j', f'merged() 过滤 dict 来源并上报 unsupportedSources（kinds={kinds}）'
           + (f'；{demo_note}' if demo_note else ''))


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
    print(f'\n统计：{len(PASSED)} 个步骤全部通过（a–j）')
    print('临时根已清理')
