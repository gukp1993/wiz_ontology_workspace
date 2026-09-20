"""连接、目录缓存与凭据专项回归（2026-09-20 v2 冻结 · 角色 C）。

覆盖需求矩阵：
* T10 连接显示名变更 vs 技术配置变更的指纹失效范围（`config_fingerprint` 排除 name/id）。
* T11 探测进行中改地址/换凭据/删连接 → `store_if_current` 拒绝写入（迟到结果丢弃）。
* T12 目录 payload 损坏 → `load_all` 抛 `CatalogCacheUnreadable` 带 connection_ids，
      绝不静默 continue 或返回 {} 冒充「无目录」。
* T13 存储/读取失败 fail-closed：读取失败抛类型化异常（不降级为空目录）；
      `clear_if_unreferenced` 在无法确认时不清除派生数据。
另覆盖 C03 凭据安全代际（只读整型）、C05 删除后的派生数据清理、secrets.save 不造幽灵项目。

运行：`python3 tests/test_connection_catalog.py`（临时数据根 + 假账号）；
`workbench.dbdrivers.probe/catalog` 全部替换为记录型假实现——绝不访问真实 MySQL/Redis/外网。
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
TMP = Path(tempfile.mkdtemp(prefix='wiz_conn_catalog_'))
os.environ['WIZ_WORKBENCH_ROOT'] = str(TMP)
os.environ.pop('WIZ_DATABASE_URL', None)

sys.path.insert(0, str(REPO / 'tests'))
import auth_client as _auth_client  # noqa: E402

OWNER = _auth_client.bind_fixture_user()

from workbench import catalogs, dbdrivers, projects, secrets as secrets_store, storage  # noqa: E402
from workbench.storage import configuration as config_store  # noqa: E402
from workbench.storage.engine import StorageUnavailable, read_connection, write_tx  # noqa: E402
from sqlalchemy import text as sql  # noqa: E402

PASSED = []


def check(cond, message, actual=None):
    if cond:
        PASSED.append(message)
        print('通过) ' + message)
        return
    print('[失败] ' + message)
    if actual is not None:
        print('  实际: ' + json.dumps(actual, ensure_ascii=False, default=str)[:1500])
    shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(1)


# --- 假驱动（禁止真实网络） --------------------------------------------------------

CALLS = {'probe': 0, 'catalog': 0}


def fake_probe(cfg, secret):
    CALLS['probe'] += 1
    return {'ok': True, 'category': 'ok', 'message': '假探测：未访问真实网络', 'latencyMs': 1}


def fake_catalog(cfg, secret):
    CALLS['catalog'] += 1
    return {'ok': True, 'database': cfg.get('database', ''),
            'tables': [{'name': 't_device', 'kind': 'table', 'fields': [{'name': 'id'}]}],
            'refreshedAt': '2026-09-20T00:00:00+00:00',
            'message': '假目录：未访问真实网络'}


dbdrivers.probe = fake_probe
dbdrivers.catalog = fake_catalog


# --- 夹具 ---------------------------------------------------------------------------

def mysql_conn(**over):
    base = {'id': 'conn-a', 'name': '业务库', 'engine': 'mysql', 'host': '10.0.0.1', 'port': 3306,
            'username': 'reader', 'tls': 'none', 'database': 'energy', 'credentialRef': ''}
    base.update(over)
    return base


def new_project(tag, conn=None):
    created = projects.create('连接测试项目 ' + tag)
    pid = created['id']
    state, _ = projects.load(pid)
    state['connections']['connections'] = [conn or mysql_conn()]
    projects.save_draft(state, expected_token=projects.current_token(pid))
    return pid


def set_connection(pid, conn):
    state, _ = projects.load(pid)
    state['connections']['connections'] = [conn] if conn else []
    projects.save_draft(state, expected_token=projects.current_token(pid))


def refresh_like_route(pid, cid, during=None):
    """按 C04 接线顺序模拟：探测前取基线 →（外部探测）→ 条件写入。

    与 L 的接线保持一致：指纹/凭据代际/目录代际都在探测前读取；探测（假驱动）之后
    才调用 `store_if_current`，期间可由 `during` 注入并发变更。
    """
    state, _ = projects.load(pid)
    conn = next((c for c in state['connections']['connections'] if c.get('id') == cid), None)
    config = dbdrivers.normalize_config(conn)
    fingerprint = catalogs.config_fingerprint(config)
    secret_revision = secrets_store.revision(pid, cid)
    meta = catalogs.load_all_meta(pid)
    expected_generation = int((meta.get(cid) or {}).get('generation') or 0)
    result = dbdrivers.catalog(config, secrets_store.read(pid, cid))
    if during is not None:
        during()
    return catalogs.store_if_current(pid, cid, result, expected_fingerprint=fingerprint,
                                     expected_secret_revision=secret_revision,
                                     expected_generation=expected_generation)


def catalog_row(pid, cid):
    head = None
    from workbench.storage import assets as asset_store
    from workbench.storage.engine import head_by_external
    with read_connection() as conn:
        head = head_by_external(conn, 'project', pid, owner_user_id=OWNER)
        if head is None:
            return None
        asset = asset_store.get_asset(conn, 'project', pid, OWNER)
        row = conn.execute(sql('SELECT config_fingerprint, generation, payload_json, refreshed_at '
                               'FROM wb_catalog_cache WHERE project_uid = :p AND connection_id = :c'),
                           {'p': asset['asset_uid'], 'c': cid}).first()
    return ({'fingerprint': str(row[0]), 'generation': int(row[1]),
             'payload': json.loads(row[2])} if row else None)


def main():
    # --- T10 指纹范围：仅改名不失效，改技术配置失效 --------------------------------
    base_cfg = dbdrivers.normalize_config(mysql_conn())
    fp_base = catalogs.config_fingerprint(base_cfg)
    renamed = dict(mysql_conn(name='业务库（改名）', id='conn-zzz'))
    check(catalogs.config_fingerprint(dbdrivers.normalize_config(renamed)) == fp_base,
          'T10 仅改显示名（含连接 id 变化）指纹不变')
    for key, value in (('host', '10.0.0.2'), ('port', 3307), ('database', 'energy2'),
                       ('username', 'reader2'), ('tls', 'encrypted')):
        changed = mysql_conn(**{key: value})
        if catalogs.config_fingerprint(dbdrivers.normalize_config(changed)) == fp_base:
            check(False, f'T10 改变技术配置 {key} 后指纹必须变化')
        check(True, f'T10 改变 {key} 指纹变化')
    redis_base = dbdrivers.normalize_config({'id': 'r1', 'name': '缓存', 'engine': 'redis',
                                             'host': '10.0.0.9', 'port': 6379, 'dbIndex': 0})
    redis_moved = dbdrivers.normalize_config({'id': 'r1', 'name': '缓存', 'engine': 'redis',
                                              'host': '10.0.0.9', 'port': 6379, 'dbIndex': 3})
    check(catalogs.config_fingerprint(redis_base) != catalogs.config_fingerprint(redis_moved),
          'T10 Redis DB 索引变化指纹变化')
    check(len(fp_base) == 64 and all(ch in '0123456789abcdef' for ch in fp_base),
          'T10 指纹仍为 64 位 hex（列宽 BinV(64) 不变）')

    # --- 基线写入 + 仅改名不改基线 -------------------------------------------------
    pid = new_project('T10', mysql_conn())
    secrets_store.save(pid, 'conn-a', 'pw-1')
    check(refresh_like_route(pid, 'conn-a') is True, 'T10 首次刷新条件写入成功')
    first = catalog_row(pid, 'conn-a')
    check(first and first['generation'] == 1, 'T10 首次写入 generation=1', first)
    set_connection(pid, mysql_conn(name='仅改名'))
    check(refresh_like_route(pid, 'conn-a') is True, 'T10 仅改显示名后刷新仍写入（基线不失效）')
    second = catalog_row(pid, 'conn-a')
    check(second['generation'] == 2 and second['fingerprint'] == first['fingerprint'],
          'T10 仅改名：指纹不变、代际 +1', second)

    # --- T11 迟到结果：探测期间改地址 / 换凭据 / 删连接 -----------------------------
    set_connection(pid, mysql_conn(host='10.0.0.7'))
    ok = refresh_like_route(pid, 'conn-a', during=lambda: set_connection(pid, mysql_conn(host='10.0.0.8')))
    after = catalog_row(pid, 'conn-a')
    check(ok is False, 'T11 探测期间改地址：store_if_current 返回 False')
    check(after['generation'] == second['generation'] and after['payload'] == second['payload'],
          'T11 探测期间改地址：旧目录未被覆盖、代际未推进', after)

    ok = refresh_like_route(pid, 'conn-a', during=lambda: secrets_store.save(pid, 'conn-a', 'pw-2'))
    after = catalog_row(pid, 'conn-a')
    check(ok is False, 'T11 探测期间换凭据：store_if_current 返回 False')
    check(after['generation'] == second['generation'], 'T11 探测期间换凭据：目录未落库', after)

    # 删除连接期间探测：不写入、不重建目录行
    catalogs.clear(pid, 'conn-a')
    check(catalog_row(pid, 'conn-a') is None, 'T11 删除前目录已清空（夹具准备）')
    ok = refresh_like_route(pid, 'conn-a', during=lambda: set_connection(pid, None))
    check(ok is False and catalog_row(pid, 'conn-a') is None,
          'T11 探测期间删连接：拒绝写入且不产生目录行')

    # 连接被删后即使目录行仍在也不更新
    pid2 = new_project('T11b', mysql_conn())
    check(refresh_like_route(pid2, 'conn-a') is True, 'T11 第二项目基线写入')
    set_connection(pid2, None)
    conn_cfg = mysql_conn()
    ok = catalogs.store_if_current(pid2, 'conn-a', {'database': 'energy', 'tables': []},
                                   expected_fingerprint=catalogs.config_fingerprint(
                                       dbdrivers.normalize_config(conn_cfg)),
                                   expected_secret_revision=secrets_store.revision(pid2, 'conn-a'),
                                   expected_generation=1)
    check(ok is False, 'T11 连接已从草稿删除：条件写入拒绝')

    # 并发刷新：同代际第二个迟到结果被拒
    pid3 = new_project('T11c', mysql_conn())
    check(refresh_like_route(pid3, 'conn-a') is True, 'T11 并发刷新：第一个写入成功')
    config = dbdrivers.normalize_config(mysql_conn())
    stale = catalogs.store_if_current(pid3, 'conn-a', {'database': 'energy', 'tables': []},
                                      expected_fingerprint=catalogs.config_fingerprint(config),
                                      expected_secret_revision=0, expected_generation=0)
    check(stale is False and catalog_row(pid3, 'conn-a')['generation'] == 1,
          'T11 代际已被推进：同代际迟到结果被拒')

    # 项目不存在时条件写入绝不创建幽灵资产行
    ok = catalogs.store_if_current('nope12345678', 'conn-a', {'tables': []},
                                   expected_fingerprint='x', expected_secret_revision=0)
    check(ok is False, 'T11 项目不存在：条件写入返回 False（不创建资产）')

    # --- 凭据安全代际（C03） -------------------------------------------------------
    pid4 = new_project('C03', mysql_conn())
    check(secrets_store.revision(pid4, 'conn-a') == 0, 'C03 无凭据时 revision=0')
    secrets_store.save(pid4, 'conn-a', 'pw-a')
    check(secrets_store.revision(pid4, 'conn-a') == 1, 'C03 首次写入凭据 revision=1')
    secrets_store.save(pid4, 'conn-a', 'pw-b')
    check(secrets_store.revision(pid4, 'conn-a') == 2, 'C03 替换凭据 revision 递增=2')
    secrets_store.clear(pid4, 'conn-a')
    check(secrets_store.revision(pid4, 'conn-a') == 0, 'C03 清除凭据后 revision=0')
    check(secrets_store.read(pid4, 'conn-a') == '', 'C03 清除后读取为空串（不回传密钥）')
    check(secrets_store.revision('nope12345678', 'conn-a') == 0, 'C03 项目不存在 revision=0')

    # --- T12 损坏 payload：类型化异常 + connection_ids -----------------------------
    pid5 = new_project('T12', mysql_conn())
    set_connection(pid5, mysql_conn(id='conn-a', database='energy'))
    state, _ = projects.load(pid5)
    state['connections']['connections'].append(mysql_conn(id='conn-b', name='第二库', database='other'))
    projects.save_draft(state, expected_token=projects.current_token(pid5))
    check(refresh_like_route(pid5, 'conn-a') is True, 'T12 conn-a 目录写入')
    check(refresh_like_route(pid5, 'conn-b') is True, 'T12 conn-b 目录写入')
    from workbench.storage import assets as asset_store
    with write_tx() as tx:
        def corrupt(conn):
            asset = asset_store.get_asset(conn, 'project', pid5, OWNER)
            conn.execute(sql("UPDATE wb_catalog_cache SET payload_json = '{不是 JSON' "
                             "WHERE project_uid = :p AND connection_id = 'conn-b'"),
                         {'p': asset['asset_uid']})
        tx.run(corrupt)
    try:
        catalogs.load_all(pid5)
        check(False, 'T12 损坏 payload：load_all 必须抛 CatalogCacheUnreadable')
    except catalogs.CatalogCacheUnreadable as exc:
        check(exc.connection_ids == ['conn-b'],
              'T12 损坏 payload：异常携带 connection_ids=[conn-b]', exc.connection_ids)
    lax = catalogs.load_all(pid5, strict=False)
    check(list(lax.keys()) == ['conn-a'], 'T12 strict=False 仅供迁移：跳过损坏条目', list(lax.keys()))
    meta = catalogs.load_all_meta(pid5)
    check(meta['conn-b']['unreadable'] is True and meta['conn-b']['payload'] is None
          and meta['conn-a']['unreadable'] is False and int(meta['conn-a']['generation']) == 1
          and len(meta['conn-a']['fingerprint']) == 64,
          'T12 load_all_meta 暴露 payload/指纹/代际/损坏标记')
    try:
        catalogs.read(pid5, 'conn-b')
        check(False, 'T12 read 单条损坏必须抛 CatalogCacheUnreadable')
    except catalogs.CatalogCacheUnreadable as exc:
        check(exc.connection_ids == ['conn-b'], 'T12 read 单条损坏抛出并带 id')
    check(catalogs.load_all(new_project('T12-empty', mysql_conn())) == {},
          'T12 无缓存的项目返回 {}（与「损坏」区分）')

    # --- T13 读取失败 fail-closed --------------------------------------------------
    original_meta = config_store.load_catalog_meta
    try:
        config_store.load_catalog_meta = lambda conn, uid: (_ for _ in ()).throw(
            RuntimeError('注入：数据库读取失败'))
        try:
            catalogs.load_all(pid5)
            check(False, 'T13 存储读取失败：load_all 必须抛 CatalogCacheUnreadable')
        except catalogs.CatalogCacheUnreadable as exc:
            check(exc.connection_ids == [], 'T13 存储级失败：connection_ids 为空（调用方转 503）',
                  exc.connection_ids)
        try:
            catalogs.load_all_meta(pid5)
            check(False, 'T13 存储读取失败：load_all_meta 必须抛 CatalogCacheUnreadable')
        except catalogs.CatalogCacheUnreadable as exc:
            check(exc.connection_ids == [], 'T13 load_all_meta 存储失败 fail-closed')
        try:
            catalogs.load_all(pid5, strict=False)
            check(False, 'T13 strict=False 也不吞存储失败')
        except catalogs.CatalogCacheUnreadable:
            check(True, 'T13 strict=False 也不吞存储失败')
    finally:
        config_store.load_catalog_meta = original_meta

    original_gen = config_store.secret_generation
    try:
        config_store.secret_generation = lambda conn, uid, cid, namespace='connection': (_ for _ in ()).throw(
            StorageUnavailable('注入：凭据库不可用'))
        try:
            secrets_store.revision(pid4, 'conn-a')
            check(False, 'T13 凭据代际读取失败必须上抛（不吞成 0）')
        except StorageUnavailable:
            check(True, 'T13 凭据代际读取失败上抛 StorageUnavailable（fail-closed）')
    finally:
        config_store.secret_generation = original_gen

    # --- C05 删除后的派生数据清理 --------------------------------------------------
    pid6 = new_project('C05', mysql_conn())
    secrets_store.save(pid6, 'conn-a', 'pw-x')
    check(refresh_like_route(pid6, 'conn-a') is True, 'C05 清理前目录已缓存')
    check(catalogs.clear_if_unreferenced(pid6, 'conn-a') is False,
          'C05 连接仍在已保存草稿：clear_if_unreferenced 拒绝清理')
    check(catalog_row(pid6, 'conn-a') is not None and secrets_store.revision(pid6, 'conn-a') == 1,
          'C05 拒绝清理时目录与凭据都保留')
    check(secrets_store.clear_if_unreferenced(pid6, 'conn-a') is False,
          'C05 连接仍在草稿：凭据 clear_if_unreferenced 拒绝清理')
    set_connection(pid6, None)
    check(catalogs.clear_if_unreferenced(pid6, 'conn-a') is True, 'C05 连接已移除：目录清理')
    check(catalog_row(pid6, 'conn-a') is None, 'C05 目录行已清除')
    check(secrets_store.clear_if_unreferenced(pid6, 'conn-a') is True
          and secrets_store.revision(pid6, 'conn-a') == 0, 'C05 连接已移除：凭据清理')

    original_saved = catalogs.saved_state
    try:
        catalogs.saved_state = lambda conn, project_id: None
        check(catalogs.clear_if_unreferenced(pid6, 'conn-a') is False,
              'T13 草稿不可读：不清理派生数据（不把失败当无引用）')
        catalogs.saved_state = lambda conn, project_id: (_ for _ in ()).throw(
            StorageUnavailable('注入：草稿读取失败'))
        try:
            catalogs.clear_if_unreferenced(pid6, 'conn-a')
            check(False, 'T13 清理时存储失败必须上抛')
        except StorageUnavailable:
            check(True, 'T13 清理时存储失败上抛（fail-closed）')
    finally:
        catalogs.saved_state = original_saved

    # --- secrets.save 不创建幽灵项目 -------------------------------------------------
    with read_connection() as conn:
        before = conn.execute(sql("SELECT COUNT(*) FROM wb_assets WHERE kind = 'project'"),
                              {}).scalar()
    try:
        secrets_store.save('ghost1234567', 'conn-a', 'pw')
        check(False, 'secrets.save 对不存在项目必须拒绝')
    except ValueError as exc:
        check('项目不存在' in str(exc), 'secrets.save 不存在项目拒绝（ProjectNotFound）', str(exc))
    with read_connection() as conn:
        after = conn.execute(sql("SELECT COUNT(*) FROM wb_assets WHERE kind = 'project'"), {}).scalar()
        ghost = conn.execute(sql("SELECT COUNT(*) FROM wb_assets WHERE external_id = 'ghost1234567'"),
                             {}).scalar()
    check(after == before and ghost == 0, 'secrets.save 拒绝后未创建幽灵项目资产行（C01 红线）')

    # --- C07 目录变化不改属性绑定（仅确认派生数据不进入草稿） ------------------------
    pid7 = new_project('C07', mysql_conn())
    state, _ = projects.load(pid7)
    state['bindings']['object_bindings'] = [{'object_type': 'device', 'connection': 'conn-a',
                                             'table': 't_device', 'properties': {'p1': 'x'}}]
    projects.save_draft(state, expected_token=projects.current_token(pid7))
    check(refresh_like_route(pid7, 'conn-a') is True, 'C07 刷新目录成功')
    state2, _ = projects.load(pid7)
    check('catalogs' not in (state2.get('bindings') or {}), 'C07 目录不进入已保存草稿')
    check(state2['bindings']['object_bindings'][0]['properties'] == {'p1': 'x'},
          'C07 目录变化不影响属性绑定内容')

    check(CALLS['probe'] == 0 and CALLS['catalog'] == 11,
          '全程使用假驱动（未访问真实 MySQL/Redis/外网）', CALLS)
    print(f'统计：{len(PASSED)} 项全部通过')
    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == '__main__':
    storage.ensure_ready()
    main()
