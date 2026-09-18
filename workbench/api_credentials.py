"""Project API credentials on the workbench database (namespace='api').

API 凭据（动作接口出站调用所需密钥）独立命名空间，与连接密码（namespace=
'connection'）同表不同作用域，owner_key=项目资产 uid、resource_id=凭据 id，
互不覆盖、不把连接标识当凭据标识。密钥只写不读回浏览器：HTTP 层只暴露
list_metadata 的 id/name；密文经 SecretStore AES-GCM 加密，根密钥在库外。
"""
from workbench import auth
from workbench import storage
from workbench.storage import configuration as config_store
from workbench.storage.engine import read_connection, write_tx, utcnow
from uuid import uuid4

NAMESPACE = 'api'
_ID = __import__('re').compile(r'[a-z0-9][a-z0-9_-]{0,63}')
MAX_NAME = 60
MAX_SECRET = 512


def _clean_id(project_id, credential_id):
    if not _ID.fullmatch(str(project_id or '')):
        raise ValueError('凭据存储路径无效')
    if credential_id and not _ID.fullmatch(str(credential_id or '')):
        raise ValueError('凭据标识无效')


def _owner_uid(conn, project_id, create=False):
    from workbench.storage import assets as store
    asset = store.get_asset(conn, 'project', project_id, auth.require_user_id())
    if asset is None:
        if not create:
            raise ValueError('项目不存在，无法保存凭据')
        # 与旧 vault 宽松行为一致：写路径允许先于项目草稿登记（资产行无 head，不出现在列表）
        return store.ensure_asset(conn, 'project', project_id, '', None, owner_user_id=auth.require_user_id())
    return asset['asset_uid']


def list_metadata(project_id):
    """列出凭据元数据 [{'id','name'}]，按 name 排序；不含密钥。"""
    _clean_id(project_id, '')
    storage.ensure_ready()
    with read_connection() as conn:
        try:
            uid = _owner_uid(conn, project_id)
        except ValueError:
            return []
        rows = config_store.list_credentials(conn, NAMESPACE, uid)
    return [{'id': row['resource_id'], 'name': row['display_name'] or row['resource_id']}
            for row in rows if _ID.fullmatch(row['resource_id'] or '')]


def ids(project_id):
    """已登记凭据标识集合（项目校验/引用检查用）。"""
    return {item['id'] for item in list_metadata(project_id)}


def save(project_id, name, secret, credential_id=''):
    """写入或覆盖一条凭据，返回 {'id','name'}（不回传密钥）。"""
    _clean_id(project_id, credential_id)
    name = str(name or '').strip()
    if not name:
        raise ValueError('凭据名称不能为空')
    if len(name) > MAX_NAME:
        raise ValueError('凭据名称过长（最多 %d 字符）' % MAX_NAME)
    secret = str(secret or '')
    if not secret:
        raise ValueError('凭据密钥不能为空')
    if len(secret) > MAX_SECRET:
        raise ValueError('凭据密钥过长（最多 %d 字符）' % MAX_SECRET)
    if '\n' in secret or '\r' in secret:
        raise ValueError('凭据密钥不能包含换行')
    storage.ensure_ready()
    if not credential_id:
        credential_id = 'cred-' + uuid4().hex[:12]
    else:
        credential_id = str(credential_id)

    def body(conn):
        uid = _owner_uid(conn, project_id, create=True)
        config_store.put_secret(conn, NAMESPACE, uid, credential_id, secret,
                                display_name=name, now=utcnow())

    with write_tx() as tx:
        tx.run(body)
    return {'id': credential_id, 'name': name}


def read(project_id, credential_id):
    """完整密钥读取；仅供服务端出站调用使用，绝不进响应。缺失返回 None。"""
    _clean_id(project_id, credential_id)
    storage.ensure_ready()
    with read_connection() as conn:
        try:
            uid = _owner_uid(conn, project_id)
        except ValueError:
            return None
        return config_store.get_secret(conn, NAMESPACE, uid, str(credential_id))


def clear(project_id, credential_id):
    """删除凭据；缺失静默。"""
    _clean_id(project_id, credential_id)
    storage.ensure_ready()

    def body(conn):
        try:
            uid = _owner_uid(conn, project_id)
        except ValueError:
            return
        config_store.clear_secret(conn, NAMESPACE, uid, str(credential_id))

    with write_tx() as tx:
        tx.run(body)


def exists(project_id, credential_id):
    _clean_id(project_id, credential_id)
    storage.ensure_ready()
    with read_connection() as conn:
        try:
            uid = _owner_uid(conn, project_id)
        except ValueError:
            return False
        return config_store.credential_exists(conn, NAMESPACE, uid, str(credential_id))
