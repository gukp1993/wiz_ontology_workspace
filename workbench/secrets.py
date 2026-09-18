"""Project data-connection credential vault on the workbench database.

连接密码存 wb_credentials（namespace='connection'，owner_key=项目资产 uid，
resource_id=连接 id），经 SecretStore AES-GCM 认证加密，根密钥在库外 0600 文件。
对 API 只写不读：read() 仅供连接探测路径，值永不回传浏览器、不进日志/快照/导出。
旧 vault 目录（ontology/vault/projects）只是迁移输入，在线服务不再读写。
"""
from workbench import storage
from workbench.storage import configuration as config_store
from workbench.storage.engine import read_connection, write_tx, utcnow

NAMESPACE = 'connection'
_ID_MAX = 80
_MAX_SECRET = 512


def _ids(project_id, connection_id):
    from workbench.projects import clean_id
    pid, cid = clean_id(project_id), str(connection_id)
    if not cid or len(cid) > _ID_MAX or '/' in cid or '\\' in cid or cid in ('.', '..'):
        raise ValueError('凭据存储标识无效')
    return pid, cid


def _owner_uid(conn, project_id, create=False):
    """项目资产 uid（凭据 owner_key）；按当前账号归属解析，跨账号项目按不存在处理。"""
    from workbench import auth
    from workbench.storage import assets as store
    owner = auth.require_user_id()
    asset = store.get_asset(conn, 'project', project_id, owner)
    if asset is None:
        if not create:
            raise ValueError('项目不存在，无法保存连接凭据')
        # 与旧 vault 宽松行为一致：写路径允许先于项目草稿登记（资产行无 head，不出现在列表）
        return store.ensure_asset(conn, 'project', project_id, '', None, owner_user_id=owner)
    return asset['asset_uid']


def save(project_id, connection_id, secret):
    secret = str(secret or '')
    if len(secret) > _MAX_SECRET or '\n' in secret or '\r' in secret:
        raise ValueError('密码内容无效（过长或包含换行）')
    project_id, connection_id = _ids(project_id, connection_id)
    storage.ensure_ready()

    def body(conn):
        uid = _owner_uid(conn, project_id, create=True)
        config_store.put_secret(conn, NAMESPACE, uid, connection_id, secret, now=utcnow())

    with write_tx() as tx:
        tx.run(body)


def read(project_id, connection_id):
    """Return the stored secret ('' when absent). Probe path only."""
    project_id, connection_id = _ids(project_id, connection_id)
    storage.ensure_ready()
    with read_connection() as conn:
        try:
            uid = _owner_uid(conn, project_id)
        except ValueError:
            return ''
        return config_store.get_secret(conn, NAMESPACE, uid, connection_id) or ''


def clear(project_id, connection_id):
    project_id, connection_id = _ids(project_id, connection_id)
    storage.ensure_ready()

    def body(conn):
        try:
            uid = _owner_uid(conn, project_id)
        except ValueError:
            return
        config_store.clear_secret(conn, NAMESPACE, uid, connection_id)

    with write_tx() as tx:
        tx.run(body)


def exists(project_id, connection_id):
    project_id, connection_id = _ids(project_id, connection_id)
    storage.ensure_ready()
    with read_connection() as conn:
        try:
            uid = _owner_uid(conn, project_id)
        except ValueError:
            return False
        return config_store.credential_exists(conn, NAMESPACE, uid, connection_id)
