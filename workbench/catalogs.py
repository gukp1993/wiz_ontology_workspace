"""Server-side catalog store on the workbench database (outside drafts).

表结构目录是派生数据（可达数 MB），存 wb_catalog_cache（项目+连接一份，带配置
指纹与代际）。加载时注入项目状态、保存/发布前剥离，绝不进入草稿或发布快照。

2026-09-20 v2 冻结（F05，接口文档 03 §3.3）：
* `config_fingerprint` 排除显示名（`name`，连接 id 亦排除）：仅改名称不使探测
  结果/目录基线失效；主机/端口/库名/schema/TLS/用户名变化使指纹变化。
* 读取失败三态：`CatalogCacheUnreadable` —— 存储层读取失败（`connection_ids` 为空）
  与单条 payload 损坏（`connection_ids` 列出）都绝不返回 `{}` 冒充「无目录」。
* 迟到结果保护：`store_if_current` 在**同一写事务**内复核「连接仍存在 + 当前技术
  配置指纹 == 探测前指纹 + 凭据代际 == 探测前代际 +（可选）目录代际未被他方推进」，
  全部满足才写入并 `generation+1`；任一不满足不写、返回 False。
* 删除连接后的派生数据清理：`clear_if_unreferenced` 仅在连接已不在已保存草稿中
  才清理（读取失败 fail-closed，不清理）。
"""
import hashlib
import json

from workbench import auth
from workbench import dbdrivers
from workbench import storage
from workbench.storage import assets as asset_store
from workbench.storage import configuration as config_store
from workbench.storage.configuration import CatalogCacheUnreadable  # noqa: F401  (对外契约，路由层按此捕获)
from workbench.storage.engine import (head_by_external, read_connection, read_snapshot,
                                      utcnow, write_tx)

KIND = 'project'

#: 参与技术配置指纹的字段之外，明确排除的“非技术配置”：显示名与连接标识。
_FINGERPRINT_SKIP = ('name', 'id')


def _guarded(project_id, connection_id):
    from workbench.projects import clean_id
    pid, cid = clean_id(project_id), str(connection_id)
    if not pid or '/' in cid or '\\' in cid or cid in ('.', '..') or not cid:
        raise ValueError('目录存储标识无效')
    return pid, cid


def config_fingerprint(conn_config):
    """连接技术配置指纹（排除显示名与连接 id；不含密码）。

    用于丢弃配置变更后的迟到目录结果、以及目录基线比较：仅改显示名不失效；
    主机/端口/库名或 schema/TLS/用户名变化使指纹变化。返回 64 位 hex（列宽不变）。
    """
    config = conn_config if isinstance(conn_config, dict) else {}
    raw = {key: value for key, value in config.items() if key not in _FINGERPRINT_SKIP}
    dumped = json.dumps(raw, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(dumped.encode('utf-8')).hexdigest()


def _project_uid(conn, project_id, create=False):
    """项目资产 uid；create=True 仅旧写入路径保留（与旧文件版宽松行为一致）。"""
    asset = asset_store.get_asset(conn, KIND, project_id, auth.require_user_id())
    if asset is None:
        if not create:
            raise ValueError('项目中不存在此数据连接，请先在数据连接页保存')
        return asset_store.ensure_asset(conn, KIND, project_id, '', None, owner_user_id=auth.require_user_id())
    return asset['asset_uid']


def _lookup_uid(conn, project_id):
    """项目资产 uid；不存在返回 None（绝不创建资产行）。"""
    asset = asset_store.get_asset(conn, KIND, project_id, auth.require_user_id())
    return asset['asset_uid'] if asset else None


def saved_state(conn, project_id):
    """同一事务内读取当前已保存的项目草稿；无 head/不可解析返回 None。

    供条件写入与删除后清理复核连接存在性（与写操作共享同一连接，不留复核窗口）。
    """
    from workbench import projects
    head = head_by_external(conn, KIND, project_id, owner_user_id=auth.require_user_id())
    if head is None or not head.get('snapshot_id'):
        return None
    snapshot = read_snapshot(conn, head['snapshot_id'])
    if snapshot is None:
        return None
    try:
        payload = json.loads(snapshot['payload_json'])
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    return projects._state_from_payload(payload, project_id, snapshot.get('payload_format', ''))


def saved_connection(conn, project_id, connection_id):
    """当前已保存草稿中的连接配置；项目/连接不存在返回 None。"""
    state = saved_state(conn, project_id)
    if state is None:
        return None
    for item in (state.get('connections') or {}).get('connections') or []:
        if isinstance(item, dict) and str(item.get('id') or '') == str(connection_id):
            return item
    return None


def _payload_of(catalog):
    catalog = catalog if isinstance(catalog, dict) else {}
    tables = catalog.get('tables')
    return {'database': catalog.get('database', ''), 'tables': tables if isinstance(tables, list) else [],
            'refreshedAt': catalog.get('refreshedAt', '')}


def _storage_failure(exc):
    return CatalogCacheUnreadable('目录缓存读取失败：' + str(exc)[:200], connection_ids=[])


def store(project_id, connection_id, catalog, config_fingerprint_value=''):
    """无条件写入目录缓存（迁移/测试用；线上探测路径用 store_if_current）。"""
    project_id, connection_id = _guarded(project_id, connection_id)
    storage.ensure_ready()
    payload = _payload_of(catalog)

    def body(conn):
        config_store.store_catalog(conn, _project_uid(conn, project_id, create=True), connection_id,
                                   payload, config_fingerprint_value, now=utcnow())

    with write_tx() as tx:
        tx.run(body)
    return True


def store_if_current(project_id, connection_id, catalog, *, expected_fingerprint,
                     expected_secret_revision, expected_generation=None):
    """条件写入目录缓存（迟到结果保护，2026-09-20 v2 冻结）。

    在同一个写事务内复核，全部满足才写入并 generation+1：
      1. 项目资产与连接仍存在（连接已从已保存草稿删除 → 拒绝，绝不重建项目资产）；
      2. 当前已保存连接的技术配置指纹 == expected_fingerprint（探测期间改地址/库等 → 拒绝）；
      3. 凭据代际 == expected_secret_revision（探测期间换密码/清密码 → 拒绝）；
      4. expected_generation 给定时代际未被他方推进（同内容并发刷新按先到先得）。

    返回 True = 已写入；False = 条件不满足、未写入（调用方按「迟到结果丢弃」处理，
    不得报成功）。存储层失败向上抛（fail-closed）。
    """
    project_id, connection_id = _guarded(project_id, connection_id)
    storage.ensure_ready()
    payload = _payload_of(catalog)
    fingerprint = str(expected_fingerprint or '')
    secret_revision = int(expected_secret_revision or 0)
    generation = None if expected_generation is None else int(expected_generation)

    def body(conn):
        uid = _lookup_uid(conn, project_id)
        if uid is None:
            return False
        current = saved_connection(conn, project_id, connection_id)
        if current is None:
            return False
        try:
            current_fingerprint = config_fingerprint(dbdrivers.normalize_config(current))
        except ValueError:
            return False  # 当前连接配置不可解析：无法确认探测结果仍对应当前配置
        if current_fingerprint != fingerprint:
            return False
        if config_store.secret_generation(conn, uid, connection_id) != secret_revision:
            return False
        return config_store.store_catalog(conn, uid, connection_id, payload, fingerprint,
                                          now=utcnow(), expected_generation=generation)

    with write_tx() as tx:
        return bool(tx.run(body))


def read(project_id, connection_id):
    """单条目录；项目/缓存不存在返回 None；payload 损坏抛 CatalogCacheUnreadable。"""
    project_id, connection_id = _guarded(project_id, connection_id)
    storage.ensure_ready()
    with read_connection() as conn:
        uid = _lookup_uid(conn, project_id)
        if uid is None:
            return None
        return config_store.read_catalog(conn, uid, connection_id)


def load_all(project_id, strict=True):
    """某项目全部目录缓存，按连接 id 键控。

    strict=True（默认，线上路径）：任何读取失败都抛 `CatalogCacheUnreadable` ——
    存储层失败 `connection_ids=[]`（调用方转 503）；单条 payload 损坏
    `connection_ids=[...]`（调用方出 error 阻断发布 / GET 路径跳过该连接注入）。
    绝不静默 continue、绝不返回 `{}` 冒充「无目录」；「项目不存在/无缓存」才返回 `{}`。
    strict=False：仅供迁移/测试，跳过损坏条目（仍不吞存储错误）。
    """
    project_id, _ = _guarded(project_id, project_id or '')
    try:
        storage.ensure_ready()
        with read_connection() as conn:
            uid = _lookup_uid(conn, project_id)
            if uid is None:
                return {}
            meta = config_store.load_catalog_meta(conn, uid)
    except CatalogCacheUnreadable:
        raise
    except Exception as exc:  # 存储/快照读取失败：绝不降级为「无目录」
        raise _storage_failure(exc) from exc
    unreadable = sorted(cid for cid, entry in meta.items() if entry['unreadable'])
    if unreadable and strict:
        raise CatalogCacheUnreadable('目录缓存内容损坏，无法安全读取：' + '、'.join(unreadable),
                                     connection_ids=unreadable)
    return {cid: entry['payload'] for cid, entry in meta.items() if not entry['unreadable']}


def load_all_meta(project_id):
    """目录缓存元数据（供校验基线 baseline.catalogs 与发布前重验读取），不新增表。

    `{connectionId: {'payload': dict|None, 'fingerprint': str, 'generation': int,
    'unreadable': bool}}`；损坏条目 payload=None、unreadable=True（不抛，交由调用方定策略）。
    存储层读取失败抛 CatalogCacheUnreadable（connection_ids 为空）；项目不存在返回 {}。
    """
    project_id, _ = _guarded(project_id, project_id or '')
    try:
        storage.ensure_ready()
        with read_connection() as conn:
            uid = _lookup_uid(conn, project_id)
            if uid is None:
                return {}
            return config_store.load_catalog_meta(conn, uid)
    except CatalogCacheUnreadable:
        raise
    except Exception as exc:
        raise _storage_failure(exc) from exc


def clear(project_id, connection_id):
    """无条件清除目录缓存（兼容旧调用；删除连接请用 clear_if_unreferenced）。"""
    project_id, connection_id = _guarded(project_id, connection_id)
    storage.ensure_ready()

    def body(conn):
        uid = _lookup_uid(conn, project_id)
        if uid is None:
            return False
        config_store.clear_catalog(conn, uid, connection_id)
        return True

    with write_tx() as tx:
        return bool(tx.run(body))


def clear_if_unreferenced(project_id, connection_id):
    """连接确认删除后的派生数据清理（C05）：仅当连接已不在已保存草稿中才清目录缓存。

    返回 True = 已清理（含原本无缓存）；False = 连接仍在已保存草稿中 / 项目不存在 /
    草稿不可读——一律不清理。读取失败（存储不可用）向上抛，绝不把失败当「无引用」。
    """
    project_id, connection_id = _guarded(project_id, connection_id)
    storage.ensure_ready()

    def body(conn):
        uid = _lookup_uid(conn, project_id)
        if uid is None:
            return False
        state = saved_state(conn, project_id)
        if state is None:
            return False  # 草稿不可读：保守不清理
        current = saved_connection(conn, project_id, connection_id)
        if current is not None:
            return False  # 连接仍在已保存草稿中：不能清派生数据
        config_store.clear_catalog(conn, uid, connection_id)
        return True

    with write_tx() as tx:
        return bool(tx.run(body))
