"""凭据、目录缓存、模型配置、设置、幂等回执的 Repository。

凭据三层命名空间（namespace）：connection=项目数据连接密码；api=项目 API 凭据；
model=LLM 提供方 API Key。同一 (namespace, owner_key, resource_id) 唯一。
明文密钥只在 secret_store 加解密边界出现；本模块所有返回值不含明文。

2026-09-18 账号体系：用户级设置走 wb_user_settings（复合主键 (user_id, setting_key)），
get_user_setting/put_user_setting 必填 user_id；旧 wb_settings 保留工作台全局语义不动。
连接/API 凭据的 owner_key 是项目资产 uid（项目已按账号归属，凭据自然隔离）；
LLM 凭据的 owner_key 用账号 user id（不再固定 'global'）。
"""
import json

from workbench.storage import engine as sto
from workbench.storage import secret_store
from workbench.storage import assets

NAMESPACE_CONNECTION = 'connection'
NAMESPACE_API = 'api'
NAMESPACE_MODEL = 'model'

DEFAULT_PROVIDER_KEY = 'models.default_provider_id'


# --- 凭据 ------------------------------------------------------------------------

def put_secret(conn, namespace, owner_key, resource_id, plaintext, display_name='', now=None):
    now = now or sto.utcnow()
    key_id, nonce, blob = secret_store.encrypt(plaintext, namespace, owner_key, resource_id)
    row = conn.execute(sto.text('SELECT secret_id, secret_revision FROM wb_credentials '
                                'WHERE namespace = :ns AND owner_key = :o AND resource_id = :r'),
                       {'ns': namespace, 'o': owner_key, 'r': resource_id}).first()
    if row:
        conn.execute(sto.text('UPDATE wb_credentials SET display_name = :dn, key_id = :k, nonce = :n, '
                              'ciphertext = :c, secret_revision = :sr, updated_at = :now '
                              'WHERE secret_id = :s'),
                     {'dn': display_name, 'k': key_id, 'n': nonce, 'c': blob,
                      'sr': int(row[1]) + 1, 'now': now, 's': row[0]})
        return row[0]
    secret_id = sto.new_id()
    conn.execute(sto.text('INSERT INTO wb_credentials (secret_id, namespace, owner_key, resource_id, '
                          'display_name, key_id, nonce, ciphertext, secret_revision, updated_at) '
                          'VALUES (:s, :ns, :o, :r, :dn, :k, :n, :c, 1, :now)'),
                 {'s': secret_id, 'ns': namespace, 'o': owner_key, 'r': resource_id,
                  'dn': display_name, 'k': key_id, 'n': nonce, 'c': blob, 'now': now})
    return secret_id


def get_secret(conn, namespace, owner_key, resource_id):
    """解密返回明文；缺失返回 None。仅供服务端执行/探测路径调用。"""
    row = conn.execute(sto.text('SELECT secret_id, key_id, nonce, ciphertext FROM wb_credentials '
                                'WHERE namespace = :ns AND owner_key = :o AND resource_id = :r'),
                       {'ns': namespace, 'o': owner_key, 'r': resource_id}).first()
    if row is None:
        return None
    return secret_store.decrypt(row[1], row[2], row[3], namespace, owner_key, resource_id)


def clear_secret(conn, namespace, owner_key, resource_id):
    conn.execute(sto.text('DELETE FROM wb_credentials WHERE namespace = :ns AND owner_key = :o '
                          'AND resource_id = :r'), {'ns': namespace, 'o': owner_key, 'r': resource_id})


def credential_exists(conn, namespace, owner_key, resource_id):
    return conn.execute(sto.text('SELECT 1 FROM wb_credentials WHERE namespace = :ns AND '
                                 'owner_key = :o AND resource_id = :r'),
                        {'ns': namespace, 'o': owner_key, 'r': resource_id}).first() is not None


def list_credentials(conn, namespace, owner_key):
    rows = conn.execute(sto.text('SELECT resource_id, display_name, secret_id, updated_at '
                                 'FROM wb_credentials WHERE namespace = :ns AND owner_key = :o '
                                 'ORDER BY display_name, resource_id'),
                        {'ns': namespace, 'o': owner_key}).mappings().all()
    return [dict(row) for row in rows]


# --- 目录缓存 --------------------------------------------------------------------

def store_catalog(conn, project_uid, connection_id, payload, config_fingerprint, now=None):
    now = now or sto.utcnow()
    row = conn.execute(sto.text('SELECT generation FROM wb_catalog_cache WHERE project_uid = :p '
                                'AND connection_id = :c'), {'p': project_uid, 'c': connection_id}).first()
    if row:
        conn.execute(sto.text('UPDATE wb_catalog_cache SET payload_json = :j, config_fingerprint = :f, '
                              'generation = generation + 1, refreshed_at = :now '
                              'WHERE project_uid = :p AND connection_id = :c'),
                     {'j': json.dumps(payload, ensure_ascii=False), 'f': config_fingerprint,
                      'now': now, 'p': project_uid, 'c': connection_id})
    else:
        conn.execute(sto.text('INSERT INTO wb_catalog_cache (project_uid, connection_id, '
                              'config_fingerprint, generation, payload_json, refreshed_at) '
                              'VALUES (:p, :c, :f, 1, :j, :now)'),
                     {'p': project_uid, 'c': connection_id, 'f': config_fingerprint,
                      'j': json.dumps(payload, ensure_ascii=False), 'now': now})


def read_catalog(conn, project_uid, connection_id):
    row = conn.execute(sto.text('SELECT payload_json FROM wb_catalog_cache WHERE project_uid = :p '
                                'AND connection_id = :c'), {'p': project_uid, 'c': connection_id}).first()
    return json.loads(row[0]) if row else None


def load_catalogs(conn, project_uid):
    rows = conn.execute(sto.text('SELECT connection_id, payload_json FROM wb_catalog_cache '
                                 'WHERE project_uid = :p'), {'p': project_uid}).all()
    out = {}
    for connection_id, raw in rows:
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        if isinstance(data, dict) and 'tables' in data:
            out[connection_id] = data
    return out


def clear_catalog(conn, project_uid, connection_id):
    conn.execute(sto.text('DELETE FROM wb_catalog_cache WHERE project_uid = :p AND connection_id = :c'),
                 {'p': project_uid, 'c': connection_id})


# --- 用户级设置（2026-09-18 账号体系）----------------------------------------------

def get_user_setting(conn, user_id, key, default=None):
    row = conn.execute(sto.text('SELECT value_json FROM wb_user_settings '
                                'WHERE user_id = :u AND setting_key = :k'),
                       {'u': user_id or '', 'k': key}).first()
    if row is None:
        return default
    try:
        return json.loads(row[0])
    except ValueError:
        return default


def put_user_setting(conn, user_id, key, value, now=None):
    now = now or sto.utcnow()
    row = conn.execute(sto.text('SELECT revision FROM wb_user_settings '
                                'WHERE user_id = :u AND setting_key = :k'),
                       {'u': user_id or '', 'k': key}).first()
    raw = json.dumps(value, ensure_ascii=False)
    if row:
        conn.execute(sto.text('UPDATE wb_user_settings SET value_json = :v, revision = revision + 1, '
                              'updated_at = :now WHERE user_id = :u AND setting_key = :k'),
                     {'v': raw, 'now': now, 'u': user_id or '', 'k': key})
    else:
        conn.execute(sto.text('INSERT INTO wb_user_settings (user_id, setting_key, value_json, '
                              'revision, updated_at) VALUES (:u, :k, :v, 1, :now)'),
                     {'u': user_id or '', 'k': key, 'v': raw, 'now': now})


# --- 模型配置与设置（默认项与密钥同事务） ------------------------------------------

def get_setting(conn, key, default=None):
    row = conn.execute(sto.text('SELECT value_json FROM wb_settings WHERE setting_key = :k'),
                       {'k': key}).first()
    if row is None:
        return default
    try:
        return json.loads(row[0])
    except ValueError:
        return default


def put_setting(conn, key, value, now=None):
    now = now or sto.utcnow()
    row = conn.execute(sto.text('SELECT revision FROM wb_settings WHERE setting_key = :k'),
                       {'k': key}).first()
    raw = json.dumps(value, ensure_ascii=False)
    if row:
        conn.execute(sto.text('UPDATE wb_settings SET value_json = :v, revision = revision + 1, '
                              'updated_at = :now WHERE setting_key = :k'),
                     {'v': raw, 'now': now, 'k': key})
    else:
        conn.execute(sto.text('INSERT INTO wb_settings (setting_key, value_json, revision, updated_at) '
                              'VALUES (:k, :v, 1, :now)'), {'k': key, 'v': raw, 'now': now})


def get_request_receipt(conn, operation, owner_key, request_key):
    row = conn.execute(sto.text('SELECT request_hash, response_json FROM wb_requests '
                                'WHERE operation = :op AND owner_key = :o AND request_key = :k'),
                       {'op': operation, 'o': owner_key, 'k': request_key}).first()
    if row is None:
        return None
    return {'request_hash': row[0], 'response': json.loads(row[1])}


def put_request_receipt(conn, operation, owner_key, request_key, request_hash, response, now=None):
    now = now or sto.utcnow()
    conn.execute(sto.text('INSERT INTO wb_requests (request_id, operation, owner_key, request_key, '
                          'request_hash, response_json, created_at) VALUES (:i, :op, :o, :k, :h, :r, :c)'),
                 {'i': sto.new_id(), 'op': operation, 'o': owner_key, 'k': request_key,
                  'h': request_hash, 'r': json.dumps(response, ensure_ascii=False), 'c': now})
