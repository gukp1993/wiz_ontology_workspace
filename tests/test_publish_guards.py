"""v2 冻结协议 HTTP 集成测试（L 角色；20260920 需求 本体与项目统一维护体验改版）。

覆盖（03 分册 §2.1/§2.2/§2.3/§2.4）：
- T18 发布提交窗口：校验后依赖（编排修订/目录）变化 → 409 DEPENDENCY_CHANGED，零写入；
- T19 发布幂等：同 requestId 同内容回放（不新增版本）、同 key 异内容 409；
- T20 组件级 pendingRequestId 契约由 D 的组件测试覆盖；此处验证服务端不产生半成品；
- T21 跨账号隔离：他人 requestId/项目按不存在处理；
- 保存边界：删仍被引用的连接 422 REFERENCE_IN_USE（同批移除放行）；引用版本必须已发布；
- 校验基线：/api/project-validate 返回 baseline（flows/catalogs）；upgrade-check 的 revision 基线。

纯 python3 标准库（无 pytest）。隔离：WIZ_WORKBENCH_ROOT=<mktemp> + 独立
WIZ_DATABASE_URL + 固定测试端口（HTTP 组由协调者串行运行，勿并行）。
运行：python3 tests/test_publish_guards.py
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

from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parent))
import auth_client
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PORT = 18841  # 新增测试专用端口（与既有固定端口错开，见 tests/run.py 说明）
ORIGIN = f'http://127.0.0.1:{PORT}'
BASE = f'http://127.0.0.1:{PORT}'

TMP = Path(tempfile.mkdtemp(prefix='wiz_publish_guards_'))
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


AUTH_COOKIE = {'value': ''}


def request(method, path, payload=None, cookie=None):
    data = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    headers = {'Content-Type': 'application/json'}
    token = cookie if cookie is not None else AUTH_COOKIE['value']
    if token:
        headers['Cookie'] = 'wiz_session=' + token
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


def wait_port(port, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.2)
    return False


def seed_release():
    """把真实已发布版本复制为临时根 storage 版本（与既有 HTTP 测试同法；真实 ontology 只读）。"""
    releases_models = TMP / 'ontology/releases/models/storage'
    real_models = REPO / 'ontology/releases/models'
    src = None
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
              '真实 ontology/ 无已发布版本也无基础文件，无法播种 storage 版本')
        dest = TMP / 'ontology/models/storage'
        dest.mkdir(parents=True, exist_ok=True)
        for name in needed:
            shutil.copyfile(base / name, dest / name)
        sys.path.insert(0, str(REPO))
        from workbench import versions
        versions.ensure_base_release('storage')
    entries = json.loads(index_path.read_text())['versions']
    check(bool(entries), '临时根应有 storage 版本', actual=entries)
    return entries[-1]['version']


def import_seed_into_db():
    """库化后：把临时根文件树导入存储库并归属测试账号（服务才可见；同既有 HTTP 测试）。"""
    os.environ['WIZ_DATABASE_URL'] = 'sqlite:///' + str(TMP / 'data' / 'workbench.sqlite3')
    sys.path.insert(0, str(REPO))
    from workbench.storage import transfer as _transfer
    rc = _transfer.main(['import', '--source', str(TMP)])
    check(rc == 0, '种子发布导入存储库', actual=rc, expected=0)
    check(_transfer.main(['create-user', '--username', auth_client.DEFAULT_USER,
                          '--password', auth_client.DEFAULT_PASSWORD]) == 0, '创建测试账号')
    check(_transfer.main(['assign-owner', '--username', auth_client.DEFAULT_USER]) == 0, '种子数据归属测试账号')
    os.environ.pop('WIZ_DATABASE_URL', None)


def start_server():
    global PROC
    env = dict(os.environ)
    env['WIZ_WORKBENCH_ROOT'] = str(TMP)
    env['WIZ_WORKBENCH_PORT'] = str(PORT)
    env['WIZ_DATABASE_URL'] = 'sqlite:///' + str(TMP / 'data' / 'workbench.sqlite3')
    PROC = subprocess.Popen([sys.executable, '-m', 'workbench.server'], cwd=str(REPO),
                            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    check(wait_port(PORT), f'测试实例未能启动（端口 {PORT}）')
    auth_client.wait_ready(BASE)
    # 与进程内直调（_write_catalog/_deps_probe_payload 的 bind_fixture_user）同一账号：
    # 否则目录写入会落到另一个 owner 的幽灵资产上
    _user, cookie = auth_client.register_or_login(BASE)
    AUTH_COOKIE['value'] = cookie
    check(bool(AUTH_COOKIE['value']), '测试账号应取得会话 cookie')


def main():
    version = seed_release()
    import_seed_into_db()
    start_server()
    print(f'临时根：{TMP}；storage 版本：{version}')

    # 建项目（绑定本体版本）
    status, created = request('POST', '/api/projects',
                              {'name': '守卫测试项目', 'ontology': 'storage', 'version': version})
    check(status == 201, '创建项目应 201', actual=(status, created))
    pid = created['id']

    status, st = request('GET', f'/api/project-state?project={pid}')
    check(status == 200, '读取项目状态应 200', actual=status)
    state = st['state']
    revision = st['revision']

    MYSQL_CONN = {'id': 'conn-mysql-01', 'name': '业务库', 'engine': 'mysql',
                  'host': '127.0.0.1', 'port': 3306, 'database': 'semp_demo'}
    state['connections'] = {'connections': [MYSQL_CONN]}
    state['projectMeta']['note'] = '守卫测试'

    # a) 保存连接 → 记录 revision
    status, saved = request('POST', '/api/project-save', {'state': state, 'revision': revision})
    check(status == 200, '保存连接应 200', actual=(status, saved))
    revision = saved['revision']
    ok('a', '保存含连接的草稿成功')

    # b) 校验响应带 baseline（flows/catalogs/项目修订）
    status, report = request('POST', '/api/project-validate', {'state': state, 'revision': revision})
    check(status == 200, '校验应 200', actual=(status, report))
    baseline = report.get('baseline') if isinstance(report, dict) else None
    check(isinstance(baseline, dict), '校验响应应包含 baseline（03 分册 §2.2）',
          actual=(status, list(report.keys()) if isinstance(report, dict) else report))
    check(baseline.get('projectId') == pid and baseline.get('revision') == revision,
          'baseline 应携带项目 id 与草稿修订', actual=baseline)
    check('flows' in baseline and 'catalogs' in baseline,
          'baseline 应含 flows 与 catalogs 依赖', actual=baseline)
    ok('b', f"校验基线：revision={baseline.get('revision','')[:12]}… flows={len(baseline.get('flows',[]))} catalogs={len(baseline.get('catalogs',[]))}")

    # c) 发布 → v1（无 requestId，行为不变）
    status, published = request('POST', '/api/project-publish', {'state': state, 'revision': revision})
    check(status == 200 and published.get('version') == 'v1', '首次发布应产出 v1', actual=(status, published))
    revision = published['revision']
    ok('c', '首次发布 v1（旧客户端路径不带 requestId 不回归）')

    # d) 幂等：同 requestId + 同内容（重读最新 revision 后重试）→ 回放，不新增版本
    state_latest = json.loads(json.dumps(state))
    time.sleep(0.05)  # 让快照时间戳与上一版区分（非竞态依赖）
    status, again = request('POST', '/api/project-publish',
                            {'state': state_latest, 'revision': revision, 'requestId': 'req-abc-1'})
    check(status == 200 and again.get('version') == 'v2', '第二次发布（新 requestId 之前）应产出 v2',
          actual=(status, again))
    revision = again['revision']
    # 幂等：同一 requestId、同一内容（携带当前 revision）重试
    status, replay = request('POST', '/api/project-publish',
                             {'state': state_latest, 'revision': revision, 'requestId': 'req-abc-1'})
    check(status == 200 and replay.get('version') == 'v2', '同 requestId 同内容应回放 v2', actual=(status, replay))
    check(replay.get('idempotentReplay') is True, '回放响应应带 idempotentReplay',
          actual=replay)
    status, releases = request('GET', f'/api/project-releases?project={pid}')
    labels = [item.get('version') for item in releases.get('items', [])]
    check(labels == ['v1', 'v2'], '回放不应新增版本（仍为 v1/v2）', actual=labels)
    ok('d', f'幂等回放：同 requestId 同内容 → v2 不重复出版本（releases={labels}）')

    # e) 幂等冲突：同 requestId + 不同内容 → 409，不新增版本
    state_changed = json.loads(json.dumps(state_latest))
    state_changed['projectMeta']['note'] = '内容已变化'
    status, conflict = request('POST', '/api/project-publish',
                               {'state': state_changed, 'revision': revision, 'requestId': 'req-abc-1'})
    check(status == 409, '同 requestId 不同内容应 409', actual=(status, conflict))
    status, releases = request('GET', f'/api/project-releases?project={pid}')
    labels = [item.get('version') for item in releases.get('items', [])]
    check(labels == ['v1', 'v2'], '幂等冲突不应新增版本', actual=labels)
    ok('e', f'幂等冲突被拒绝：409（{conflict.get("error", "")[:40]}…），releases 不变')

    # f) 发布依赖重验（T18）：提交窗口内依赖变化 → 409 DEPENDENCY_CHANGED 零写入
    #    窗口位于同一请求内的两次依赖读取之间（路由快照 → 事务内复核），跨进程无法
    #    确定性交错；按计划 §7「受控并发屏障」在进程内以同一入口注入（不使用 sleep）。
    state_changed['projectMeta']['note'] = '依赖窗口测试'
    status, saved = request('POST', '/api/project-save', {'state': state_changed, 'revision': revision})
    check(status == 200, '保存待发布草稿应 200', actual=(status, saved))
    revision = saved['revision']
    import unittest.mock as _mock
    _inprocess_bind()
    from workbench import project_routes as _routes
    from workbench import projects as _projects
    _write_catalog(pid, tables=[{'name': 't1', 'kind': 'table', 'fields': [
        {'name': 'id', 'dataType': 'bigint', 'key': 'pri', 'comment': ''}]}])
    real_probe = _projects.dependency_probe
    calls = {'n': 0}

    def probing(conn, state):
        result = real_probe(conn, state)
        calls['n'] += 1
        if calls['n'] == 1:  # 路由快照读取之后、提交复核之前改变目录缓存 → 精确命中窗口
            _write_catalog(pid, tables=[{'name': 't1', 'kind': 'table', 'fields': [
                {'name': 'id', 'dataType': 'bigint', 'key': 'pri', 'comment': ''},
                {'name': 'extra', 'dataType': 'double', 'key': '', 'comment': ''}]}])
        return result

    with _mock.patch.object(_projects, 'dependency_probe', probing):
        denied, status = _routes.post_project_write(
            {'state': json.loads(json.dumps(state_changed)), 'revision': revision,
             'requestId': 'req-dep-1'}, '/api/project-publish')
    check(status == 409 and isinstance(denied, dict) and denied.get('reason') == 'DEPENDENCY_CHANGED',
          '提交窗口内依赖变化应 409 DEPENDENCY_CHANGED', actual=(status, denied))
    check(calls['n'] >= 2, '窗口注入应命中两次依赖读取（路由快照 + 事务复核）', actual=calls['n'])
    status, releases = request('GET', f'/api/project-releases?project={pid}')
    labels = [item.get('version') for item in releases.get('items', [])]
    check(labels == ['v1', 'v2'], '依赖变化拒绝发布不得写入任何版本', actual=labels)
    # 撤销注入后以新 requestId 发布 → 成功（零写入的首答拒绝不代表草稿被改动）
    published2, status = _routes.post_project_write(
        {'state': json.loads(json.dumps(state_changed)), 'revision': revision,
         'requestId': 'req-dep-2'}, '/api/project-publish')
    check(status == 200 and published2.get('version') == 'v3',
          '依赖稳定后发布应成功产出 v3', actual=(status, published2))
    revision = published2['revision']
    ok('f', f"发布依赖重验：窗口内目录变化 → 409 DEPENDENCY_CHANGED 零写入（复核 {calls['n']} 次）；随后 v3 成功")

    # o) R03（2026-09-20 验收修复）：目录在「校验 payload 已读、目录依赖令牌未读」之间更新
    #    → 必须 409 DEPENDENCY_CHANGED 零写入；绝不允许用旧 payload 校验、按新代际提交。
    #    注入点在路由依赖探测的第一次调用里、且在实际读取之前（即原实现第二次读目录的时刻）：
    #    修复前实现会因「校验用旧 payload + 提交按新代际」返回 200 并新增版本。
    base_cat = [{'name': 't1', 'kind': 'table', 'fields': [
        {'name': 'id', 'dataType': 'bigint', 'key': 'pri', 'comment': ''}]}]
    changed_cat = [{'name': 't1', 'kind': 'table', 'fields': [
        {'name': 'id', 'dataType': 'bigint', 'key': 'pri', 'comment': ''},
        {'name': 'extra', 'dataType': 'double', 'key': '', 'comment': ''}]}]
    _write_catalog(pid, tables=base_cat)
    state_r03 = json.loads(json.dumps(state_changed))
    state_r03['projectMeta']['note'] = 'R03 窗口测试'
    status, saved = request('POST', '/api/project-save', {'state': state_r03, 'revision': revision})
    check(status == 200, '保存 R03 用例草稿应 200', actual=(status, saved))
    revision = saved['revision']
    labels_before = [i.get('version') for i in
                     request('GET', f'/api/project-releases?project={pid}')[1].get('items', [])]
    calls_o = {'n': 0}

    def probing_before(conn, state):
        calls_o['n'] += 1
        if calls_o['n'] == 1:
            # payload 与目录代际的同一次读取已完成；此刻改写目录 = 命中 R03 窗口
            _write_catalog(pid, tables=changed_cat)
        return real_probe(conn, state)

    with _mock.patch.object(_projects, 'dependency_probe', probing_before):
        denied_r03, status = _routes.post_project_write(
            {'state': json.loads(json.dumps(state_r03)), 'revision': revision,
             'requestId': 'req-r03-1'}, '/api/project-publish')
    check(status == 409 and isinstance(denied_r03, dict)
          and denied_r03.get('reason') == 'DEPENDENCY_CHANGED',
          'R03：payload 读取后、提交复核前目录变化必须 409（不得返回 200 出版本）',
          actual=(status, denied_r03))
    labels_after = [i.get('version') for i in
                    request('GET', f'/api/project-releases?project={pid}')[1].get('items', [])]
    check(labels_after == labels_before, 'R03 拒绝路径不得写入任何版本（release 列表不变）',
          actual=(labels_before, labels_after))
    status, back = request('GET', f'/api/project-state?project={pid}')
    check(back['revision'] == revision, 'R03 拒绝路径不得推进草稿 revision（零部分写入）',
          actual=(back['revision'], revision))
    ok('o', 'R03：payload 与依赖令牌读取窗口内目录变化 → 409 零写入（修复前为 200 新增版本）')

    # p) R03 对照：目录在 payload 读取**之前**更新 → 校验所用 payload 与记录的代际来自
    #    同一次读取（新内容），不得报 DEPENDENCY_CHANGED，也不得沿用旧 payload 校验。
    real_meta = _routes._catalog_meta
    seen = {}
    real_validate = _routes._validate_with_degraded

    def meta_after_update(project_id):
        # 模拟「本次校验读取之前」目录已被刷新：payload 与代际同一次读取，均为新内容
        _write_catalog(pid, tables=changed_cat)
        return real_meta(project_id)

    def capturing_validate(state_in, ontology_state, degraded):
        seen['catalogs'] = json.loads(json.dumps(
            (state_in.get('bindings') or {}).get('catalogs') or {}))
        return real_validate(state_in, ontology_state, degraded)

    with _mock.patch.object(_routes, '_catalog_meta', meta_after_update), \
         _mock.patch.object(_routes, '_validate_with_degraded', capturing_validate):
        control, status = _routes.post_project_write(
            {'state': json.loads(json.dumps(state_r03)), 'revision': revision,
             'requestId': 'req-r03-2'}, '/api/project-publish')
    check(status == 200 and isinstance(control, dict) and control.get('version'),
          'R03 对照：payload 读取前更新目录 → 同基线校验后正常发布（不得误报 409）',
          actual=(status, control))
    seen_fields = [f.get('name') for f in
                   (seen.get('catalogs', {}).get('conn-mysql-01') or {}).get('tables', [{}])[0]
                   .get('fields', [])] if seen.get('catalogs') else []
    check('extra' in seen_fields,
          'R03 对照：校验使用读取前已更新的新 payload（与记录代际同一读取基线）',
          actual={'seen_catalogs': str(seen)[:200], 'fields': seen_fields})
    revision = control['revision']
    ok('p', f"R03 对照：payload 读取前更新的目录按同一基线校验并发布 {control.get('version')}")

    # g) 保存边界：删除仍被引用的连接 → 422 REFERENCE_IN_USE
    state_ref = json.loads(json.dumps(state_changed))
    state_ref['bindings']['object_bindings'] = [{
        'object_type': 'storage_cluster', 'connection': 'conn-mysql-01', 'table': 't1',
        'primary_key': 'id', 'properties': {}}]
    status, saved = request('POST', '/api/project-save', {'state': state_ref, 'revision': revision})
    check(status == 200, '保存含引用连接的对象映射应 200', actual=(status, saved))
    revision = saved['revision']
    state_del = json.loads(json.dumps(state_ref))
    state_del['connections'] = {'connections': []}  # 只删连接、引用仍在
    status, denied = request('POST', '/api/project-save', {'state': state_del, 'revision': revision})
    check(status == 422 and denied.get('code') == 'REFERENCE_IN_USE',
          '删除仍被引用的连接应 422 REFERENCE_IN_USE', actual=(status, denied))
    refs = denied.get('references') if isinstance(denied, dict) else None
    check(isinstance(refs, list) and refs, '拒绝响应应列出引用位置', actual=refs)
    status, back = request('GET', f'/api/project-state?project={pid}')
    check(back['state']['connections']['connections'] != [], '被拒绝的保存不得零写入（连接仍在）',
          actual=back['state']['connections'])
    # 连接与其引用同批移除 = 显式删除 → 放行
    state_all_del = json.loads(json.dumps(state_ref))
    state_all_del['connections'] = {'connections': []}
    state_all_del['bindings']['object_bindings'] = []
    status, saved = request('POST', '/api/project-save', {'state': state_all_del, 'revision': revision})
    check(status == 200, '连接与其引用同批移除应放行', actual=(status, saved))
    revision = saved['revision']
    ok('g', f'保存边界：删被引用连接 422 且零写入（references={len(refs)} 项）；同批移除放行')

    # h) 引用版本变更：目标必须已发布
    state_bad = json.loads(json.dumps(state_all_del))
    state_bad['ontologyVersion'] = '9.9.9-does-not-exist'
    status, denied = request('POST', '/api/project-save', {'state': state_bad, 'revision': revision})
    check(status == 404, '变更引用版本到不存在版本应 404', actual=(status, denied))
    ok('h', f'引用版本变更保护：目标不存在 → 404（{denied.get("error", "")[:40] if isinstance(denied, dict) else denied}）')

    # i) upgrade-check 的 revision 基线：过期 revision → 409
    status, up = request('POST', '/api/project-upgrade-check',
                         {'state': state_all_del, 'revision': 'r-not-current', 'targetVersion': version})
    check(status == 409, '过期 revision 的升级比较应 409', actual=(status, up))
    status, up = request('POST', '/api/project-upgrade-check',
                         {'state': state_all_del, 'revision': revision, 'targetVersion': version})
    check(status == 200, '当前 revision 的升级比较应 200', actual=(status, up))
    ok('i', '升级比较绑定草稿修订：过期 409、当前 revision 200')

    # l) 目录刷新迟到结果：探测期间连接被改 → 服务端不落缓存且响应带 stale
    status, saved = request('POST', '/api/project-save', {'state': state_changed, 'revision': revision})
    check(status == 200, '恢复含连接草稿应 200', actual=(status, saved))
    revision = saved['revision']
    _write_catalog(pid, tables=[{'name': 'stale_probe', 'kind': 'table', 'fields': [
        {'name': 'id', 'dataType': 'bigint', 'key': 'pri', 'comment': ''}]}])
    _inprocess_bind()
    from workbench import catalogs as _cats
    from workbench import dbdrivers as _drv
    fake_catalog = {'ok': True, 'database': 'semp_demo', 'refreshedAt': '2026-09-20T00:00:00+00:00',
                    'tables': [{'name': 'stale_probe', 'kind': 'table', 'fields': []}],
                    'message': '已读取 1 张表／视图的结构目录（不含数据内容）'}
    # fake driver：探测成功但条件写被拒（模拟「探测期间连接/凭据已变」）→ 服务端不得落缓存
    with _mock.patch.object(_drv, 'catalog', lambda config, secret: dict(fake_catalog)), \
         _mock.patch.object(_cats, 'store_if_current', lambda *a, **k: False):
        stale, status = _routes.post_catalog_refresh({'projectId': pid, 'connectionId': 'conn-mysql-01'})
    check(status == 200 and isinstance(stale, dict) and stale.get('stale') is True,
          '条件写被拒（迟到结果）应返回 stale=true，不得伪装成功落缓存', actual=(status, stale))
    ok('l', '目录迟到结果：条件写被拒 → 响应 stale=true（客户端不得按成功提示）')

    # k) flow 依赖纳入基线与发布重验（T18 的编排侧）：改编排 → 旧快照发布被拒
    flow_id = _create_flow('依赖测试编排')
    state_flow = json.loads(json.dumps(state_all_del))
    state_flow['bindings']['object_bindings'] = []
    state_flow['projectMeta']['note'] = 'flow 依赖测试'
    status, saved = request('POST', '/api/project-save', {'state': state_flow, 'revision': revision})
    check(status == 200, '保存 flow 依赖草稿应 200', actual=(status, saved))
    revision = saved['revision']
    _inprocess_bind()
    from workbench import flows as _flows
    _flows.save_draft({'schemaVersion': 1, 'flowId': flow_id, 'name': '依赖测试编排',
                       'description': '', 'status': 'active', 'inputs': [], 'outputs': [],
                       'connections': [], 'nodes': [],
                       'layout': {'positions': {}, 'zoom': 1, 'pan': {'x': 0, 'y': 0}}})
    with _mock.patch.object(_projects, 'referenced_flow_ids', lambda state: {flow_id}):
        with _mock.patch.object(_projects, 'dependency_probe', probing):
            calls['n'] = 0
            denied, status = _routes.post_project_write(
                {'state': json.loads(json.dumps(state_flow)), 'revision': revision,
                 'requestId': 'req-flow-1'}, '/api/project-publish')
    check(status == 409 and isinstance(denied, dict) and denied.get('reason') == 'DEPENDENCY_CHANGED',
          '编排依赖在提交窗口内变化应 409 DEPENDENCY_CHANGED', actual=(status, denied))
    ok('k', '编排依赖重验：窗口内编排 head 变化 → 409 拒绝（依赖快照含 flows）')

    # m) 损坏目录缓存：校验必须出 error、发布必须阻断（F 独立 QA 复现的 P0 回归）
    _write_catalog(pid, tables=[{'name': 'broken_probe', 'kind': 'table', 'fields': []}])
    _inprocess_bind()
    labels_m_before = [item.get('version') for item in
                       request('GET', f'/api/project-releases?project={pid}')[1].get('items', [])]
    _corrupt_catalog(pid, 'conn-mysql-01')
    state_ok = json.loads(json.dumps(state_all_del))
    status, report_broken = request('POST', '/api/project-validate',
                                    {'state': state_ok, 'revision': revision})
    errs = report_broken.get('errors', []) if isinstance(report_broken, dict) else []
    check(status == 200 and any('目录缓存读取失败' in e for e in errs),
          '损坏目录缓存 → 校验必须报错（不得静默跳过、不得显示 valid）',
          actual=(status, errs))
    status, denied_pub = request('POST', '/api/project-publish',
                                 {'state': state_ok, 'revision': revision, 'requestId': 'req-broken-1'})
    check(status == 422, '损坏目录缓存 → 发布必须 422 阻断', actual=(status, denied_pub))
    status, releases = request('GET', f'/api/project-releases?project={pid}')
    labels = [item.get('version') for item in releases.get('items', [])]
    check(labels == labels_m_before, '损坏目录下不得产出任何新版本', actual=(labels_m_before, labels))
    ok('m', f'损坏目录缓存：校验报 error（{errs[0][:30]}…）、发布 422 阻断、零新版本')

    # n) 存储层目录读取失败：GET 与 POST 同为 503（F 独立 QA 复现的 P0：GET 曾落 500）
    _break_catalog_table()
    status, get_fail = request('GET', f'/api/project-state?project={pid}')
    check(status == 503 and isinstance(get_fail, dict) and get_fail.get('code') == 'STORAGE_UNAVAILABLE',
          '存储层目录读取失败 → GET project-state 应 503 STORAGE_UNAVAILABLE（不是 500）',
          actual=(status, get_fail))
    ok('n', '目录存储读取失败：GET 与 POST 同为 503（可重试语义，不落 500 程序错误）')

    # j) 跨账号隔离：他人用同 requestId 不串号（新建账号，看不到本项目）
    _other_user, other_cookie = auth_client.register_or_login(BASE, 'otheruser', 'other1234')
    check(bool(other_cookie), '第二账号应能登录')
    status, denied = request('POST', '/api/project-publish',
                             {'state': state_all_del, 'revision': revision, 'requestId': 'req-abc-1'},
                             cookie=other_cookie)
    check(status in (404, 400, 409), '跨账号发布他人项目应被拒（按不存在处理）', actual=(status, denied))
    check(not (status == 200 and denied.get('idempotentReplay')), '跨账号不得回放他人幂等回执', actual=denied)
    ok('j', f'跨账号隔离：他人 requestId 不串号（{status}）')

    shutdown()
    print(f'\n统计：{len(PASSED)} 个步骤全部通过（a–n）')
    shutil.rmtree(TMP, ignore_errors=True)
    print('临时根已清理')


def _write_catalog(pid, tables):
    """经线上同路径写目录缓存（进程内直调 catalogs.store；先绑定测试账号）。"""
    env_backup = os.environ.get('WIZ_DATABASE_URL')
    os.environ['WIZ_DATABASE_URL'] = 'sqlite:///' + str(TMP / 'data' / 'workbench.sqlite3')
    sys.path.insert(0, str(REPO))
    auth_client.bind_fixture_user()
    from workbench import catalogs as _catalogs
    _catalogs.store(pid, 'conn-mysql-01',
                    {'database': 'semp_demo', 'refreshedAt': '2026-09-20T00:00:00+00:00', 'tables': tables})
    if env_backup is None:
        os.environ.pop('WIZ_DATABASE_URL', None)
    else:
        os.environ['WIZ_DATABASE_URL'] = env_backup


def _corrupt_catalog(pid, connection_id):
    """把某连接的目录缓存 payload_json 写坏（模拟存储层损坏；进程内直调）。"""
    _inprocess_bind()
    import sqlalchemy as _sa
    from workbench.storage import assets as _assets
    from workbench import auth as _auth
    from workbench.storage.engine import read_connection as _rc, write_tx as _wtx
    with _rc() as conn:
        asset = _assets.get_asset(conn, 'project', pid, _auth.require_user_id())
        uid = asset['asset_uid']

    def body(conn):
        conn.execute(_sa.text('UPDATE wb_catalog_cache SET payload_json = :v '
                              'WHERE project_uid = :p AND connection_id = :c'),
                     {'v': '{broken', 'p': uid, 'c': connection_id})

    with _wtx() as tx:
        tx.run(body)


def _break_catalog_table():
    """重命名目录缓存表：模拟存储层整体读取失败（测试末尾执行，之后不再依赖该表）。"""
    _inprocess_bind()
    import sqlalchemy as _sa
    from workbench.storage.engine import write_tx as _wtx
    with _wtx() as tx:
        tx.run(lambda conn: conn.execute(_sa.text(
            'ALTER TABLE wb_catalog_cache RENAME TO wb_catalog_cache_broken')))


def _create_flow(name):
    """建一个空编排（进程内直调；返回 flowId）。"""
    _inprocess_bind()
    from workbench import flows as _flows
    return _flows.create(name)['id']


def _inprocess_bind():
    """进程内直调域函数的前提：指向同一测试库并绑定同一测试账号（HTTP 会话同账号）。"""
    os.environ['WIZ_DATABASE_URL'] = 'sqlite:///' + str(TMP / 'data' / 'workbench.sqlite3')
    sys.path.insert(0, str(REPO))
    auth_client.bind_fixture_user()


if __name__ == '__main__':
    main()
