"""Server-side catalog store on the workbench database (outside drafts).

表结构目录是派生数据（可达数 MB），存 wb_catalog_cache（项目+连接一份，带配置
指纹与代际）。加载时注入项目状态、保存/发布前剥离，绝不进入草稿或发布快照。
迟到结果保护：写入携带探测时所用的连接配置指纹；配置已变则由调用方丢弃结果。
"""
import hashlib
import json

from workbench import auth
from workbench import storage
from workbench.storage import assets as asset_store
from workbench.storage import configuration as config_store
from workbench.storage.engine import read_connection, write_tx, utcnow


def _guarded(project_id, connection_id):
    from workbench.projects import clean_id
    pid, cid = clean_id(project_id), str(connection_id)
    if not pid or '/' in cid or '\\' in cid or cid in ('.', '..') or not cid:
        raise ValueError('目录存储标识无效')
    return pid, cid


def config_fingerprint(conn_config):
    """连接配置指纹（不含密码）；用于丢弃配置变更后的迟到目录结果。"""
    raw = json.dumps(conn_config or {}, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def _project_uid(conn, project_id, create=False):
    asset = asset_store.get_asset(conn, 'project', project_id, auth.require_user_id())
    if asset is None:
        if not create:
            raise ValueError('项目中不存在此数据连接，请先在数据连接页保存')
        # 与旧文件版宽松行为一致：写路径允许先于项目草稿登记（资产行无 head，不出现在列表）
        return asset_store.ensure_asset(conn, 'project', project_id, '', None, owner_user_id=auth.require_user_id())
    return asset['asset_uid']


def store(project_id, connection_id, catalog, config_fingerprint_value=''):
    """写入目录缓存；fingerprint 不匹配当前已存指纹 +1 代际，由写事务内完成。"""
    project_id, connection_id = _guarded(project_id, connection_id)
    storage.ensure_ready()
    payload = {'database': catalog.get('database', ''), 'tables': catalog.get('tables', []),
               'refreshedAt': catalog.get('refreshedAt', '')}

    def body(conn):
        config_store.store_catalog(conn, _project_uid(conn, project_id, create=True), connection_id,
                                   payload, config_fingerprint_value, now=utcnow())

    with write_tx() as tx:
        tx.run(body)


def read(project_id, connection_id):
    project_id, connection_id = _guarded(project_id, connection_id)
    storage.ensure_ready()
    with read_connection() as conn:
        try:
            uid = _project_uid(conn, project_id)
        except ValueError:
            return None
        return config_store.read_catalog(conn, uid, connection_id)


def load_all(project_id):
    """某项目全部目录缓存，按连接 id 键控。"""
    project_id, _ = _guarded(project_id, project_id or '')
    storage.ensure_ready()
    with read_connection() as conn:
        try:
            uid = _project_uid(conn, project_id)
        except (ValueError, Exception):
            return {}
        return config_store.load_catalogs(conn, uid)


def clear(project_id, connection_id):
    project_id, connection_id = _guarded(project_id, connection_id)
    storage.ensure_ready()

    def body(conn):
        try:
            uid = _project_uid(conn, project_id)
        except ValueError:
            return
        config_store.clear_catalog(conn, uid, connection_id)

    with write_tx() as tx:
        tx.run(body)
