"""Project data-connection credential vault on the workbench database.

连接密码存 wb_credentials（namespace='connection'，owner_key=项目资产 uid，
resource_id=连接 id），经 SecretStore AES-GCM 认证加密，根密钥在库外 0600 文件。
对 API 只写不读：read() 仅供连接探测路径，值永不回传浏览器、不进日志/快照/导出。
旧 vault 目录（ontology/vault/projects）只是迁移输入，在线服务不再读写。

2026-09-20 v2 冻结（F05）：
* `revision()` 暴露凭据安全代际（只读整型，0 = 无凭据）——探测迟到结果按此丢弃，
  密钥/密文/派生指纹永不回传；项目不存在返回 0，存储错误向上抛（fail-closed）。
* `save()` 不再创建项目资产：项目不存在直接拒绝（不再生成"幽灵项目"资产行）。
* `clear_if_unreferenced()` 仅在连接已不在已保存项目草稿中才清除凭据。
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


def _owner_uid(conn, project_id):
    """项目资产 uid（凭据 owner_key）；按当前账号归属解析，跨账号项目按不存在处理。

    项目资产必须已存在：本模块任何路径都不创建项目资产（拒绝"幽灵项目"，
    与 read/clear 语义一致）。不存在时抛 projects.ProjectNotFound（NotFound 语义，
    路由层可转 404；作为 ValueError 子类也兼容按 400 拒绝）。
    """
    from workbench import auth
    from workbench import projects
    from workbench.storage import assets as store
    owner = auth.require_user_id()
    asset = store.get_asset(conn, 'project', project_id, owner)
    if asset is None:
        raise projects.ProjectNotFound('项目不存在，无法存取连接凭据')
    return asset['asset_uid']


def save(project_id, connection_id, secret):
    secret = str(secret or '')
    if len(secret) > _MAX_SECRET or '\n' in secret or '\r' in secret:
        raise ValueError('密码内容无效（过长或包含换行）')
    project_id, connection_id = _ids(project_id, connection_id)
    storage.ensure_ready()

    def body(conn):
        uid = _owner_uid(conn, project_id)  # 不创建资产：项目不存在即拒绝
        config_store.put_secret(conn, NAMESPACE, uid, connection_id, secret, now=utcnow())

    with write_tx() as tx:
        tx.run(body)
    return True


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


def revision(project_id, connection_id):
    """凭据安全代际（0 = 无凭据）；只返回整型，绝不返回密钥、密文或其派生指纹。

    项目或凭据不存在返回 0；存储不可用向上抛（fail-closed：不把读取失败吞成 0，
    否则「读不到凭据」会被误当成「凭据未变」而放行迟到结果）。
    """
    project_id, connection_id = _ids(project_id, connection_id)
    storage.ensure_ready()
    with read_connection() as conn:
        try:
            uid = _owner_uid(conn, project_id)
        except ValueError:
            return 0
        return config_store.secret_generation(conn, uid, connection_id)


def clear(project_id, connection_id):
    project_id, connection_id = _ids(project_id, connection_id)
    storage.ensure_ready()

    def body(conn):
        try:
            uid = _owner_uid(conn, project_id)
        except ValueError:
            return False
        config_store.clear_secret(conn, NAMESPACE, uid, connection_id)
        return True

    with write_tx() as tx:
        return bool(tx.run(body))


def clear_if_unreferenced(project_id, connection_id):
    """连接确认删除后的凭据清理（C05）：仅当连接已不在已保存草稿中才清除。

    返回 True = 已清理（含原本无凭据）；False = 连接仍在已保存草稿中 / 项目不存在 /
    草稿不可读——一律不清理。读取失败（存储不可用）向上抛，绝不把失败当「无引用」。
    """
    project_id, connection_id = _ids(project_id, connection_id)
    storage.ensure_ready()

    def body(conn):
        from workbench.catalogs import saved_connection, saved_state
        try:
            uid = _owner_uid(conn, project_id)
        except ValueError:
            return False
        if saved_state(conn, project_id) is None:
            return False  # 草稿不可读：保守不清理
        if saved_connection(conn, project_id, connection_id) is not None:
            return False  # 连接仍在已保存草稿中：不能清凭据
        config_store.clear_secret(conn, NAMESPACE, uid, connection_id)
        return True

    with write_tx() as tx:
        return bool(tx.run(body))


def exists(project_id, connection_id):
    project_id, connection_id = _ids(project_id, connection_id)
    storage.ensure_ready()
    with read_connection() as conn:
        try:
            uid = _owner_uid(conn, project_id)
        except ValueError:
            return False
        return config_store.credential_exists(conn, NAMESPACE, uid, connection_id)
