"""凭据、目录缓存、模型配置、设置、幂等回执的 Repository。

凭据三层命名空间（namespace）：connection=项目数据连接密码；api=项目 API 凭据；
model=LLM 提供方 API Key。同一 (namespace, owner_key, resource_id) 唯一。
明文密钥只在 secret_store 加解密边界出现；本模块所有返回值不含明文；
凭据代际只经 secret_generation 暴露整型（0 = 无凭据），密钥与密文永不返回。

目录缓存读取（2026-09-20 v2 冻结）：load_catalog_meta 一次读全（payload + 指纹 + 代际 +
损坏标记）；损坏 payload 抛 CatalogCacheUnreadable，绝不静默跳过当作「无目录」。

2026-09-18 账号体系：用户级设置走 wb_user_settings（复合主键 (user_id, setting_key)），
get_user_setting/put_user_setting 必填 user_id；旧 wb_settings 保留工作台全局语义不动。
连接/API 凭据的 owner_key 是项目资产 uid（项目已按账号归属，凭据自然隔离）；
LLM 凭据的 owner_key 用账号 user id（不再固定 'global'）。
"""
import json

from workbench.storage import engine as sto
from workbench.storage import secret_store

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

class CatalogCacheUnreadable(Exception):
    """目录缓存读取失败（fail-closed）：绝不把失败包装成「无目录」。

    `connection_ids` 非空 = 这些连接的 payload 损坏（单条损坏，其他连接仍可正常读取）；
    空列表 = 存储层整体读取失败（调用方转 503，不得降级为 200 + 空结果）。

    2026-09-20 v2 冻结（F05，接口文档 03 §3.3）：调用方必须先按连接 id 区分
    「无缓存」（无该行）与「读取失败」，再决定注入/报错。
    """

    def __init__(self, message='', connection_ids=None):
        self.connection_ids = [str(item) for item in (connection_ids or [])]
        super().__init__(message or '目录缓存读取失败')


def _catalog_text(value):
    if isinstance(value, (bytes, bytearray)):
        return value.decode('utf-8', 'replace')
    return str(value or '')


def _parse_catalog_payload(raw):
    """(payload|None, unreadable)；JSON 损坏或结构不完整都按损坏上报，绝不静默跳过。"""
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None, True
    if not isinstance(data, dict) or not isinstance(data.get('tables'), list):
        return None, True
    return data, False


def load_catalog_meta(conn, project_uid):
    """按连接返回目录缓存元数据（含指纹、代际与损坏标记）；不新增表。

    {connectionId: {'payload': dict|None, 'fingerprint': str, 'generation': int,
                    'unreadable': bool}}——损坏条目 payload=None、unreadable=True。
    存储层错误原样向上抛（由调用方转 CatalogCacheUnreadable）。
    """
    rows = conn.execute(sto.text('SELECT connection_id, config_fingerprint, generation, payload_json '
                                 'FROM wb_catalog_cache WHERE project_uid = :p '
                                 'ORDER BY connection_id'), {'p': project_uid}).all()
    out = {}
    for connection_id, fingerprint, generation, raw in rows:
        payload, unreadable = _parse_catalog_payload(raw)
        out[_catalog_text(connection_id)] = {
            'payload': payload,
            'fingerprint': _catalog_text(fingerprint),
            'generation': int(generation or 0),
            'unreadable': unreadable,
        }
    return out


def store_catalog(conn, project_uid, connection_id, payload, config_fingerprint, now=None,
                  expected_generation=None):
    """写入目录缓存。

    expected_generation=None（迁移/旧调用）：无条件 upsert，返回 True。
    给定整数：条件写入——仅当当前代际等于 expected_generation（无该行按 0 计）时
    才写并 generation+1，返回 True；条件不满足不写、返回 False（迟到结果丢弃）。
    条件判断与写入在同一语句/事务内完成（调用方负责 BEGIN IMMEDIATE 写事务）。
    """
    now = now or sto.utcnow()
    raw = json.dumps(payload, ensure_ascii=False)
    row = conn.execute(sto.text('SELECT generation FROM wb_catalog_cache WHERE project_uid = :p '
                                'AND connection_id = :c'), {'p': project_uid, 'c': connection_id}).first()
    if expected_generation is None:
        if row:
            conn.execute(sto.text('UPDATE wb_catalog_cache SET payload_json = :j, config_fingerprint = :f, '
                                  'generation = generation + 1, refreshed_at = :now '
                                  'WHERE project_uid = :p AND connection_id = :c'),
                         {'j': raw, 'f': config_fingerprint, 'now': now,
                          'p': project_uid, 'c': connection_id})
        else:
            conn.execute(sto.text('INSERT INTO wb_catalog_cache (project_uid, connection_id, '
                                  'config_fingerprint, generation, payload_json, refreshed_at) '
                                  'VALUES (:p, :c, :f, 1, :j, :now)'),
                         {'p': project_uid, 'c': connection_id, 'f': config_fingerprint,
                          'j': raw, 'now': now})
        return True
    expected = int(expected_generation)
    if row is None:
        if expected != 0:
            return False
        conn.execute(sto.text('INSERT INTO wb_catalog_cache (project_uid, connection_id, '
                              'config_fingerprint, generation, payload_json, refreshed_at) '
                              'VALUES (:p, :c, :f, 1, :j, :now)'),
                     {'p': project_uid, 'c': connection_id, 'f': config_fingerprint,
                      'j': raw, 'now': now})
        return True
    if int(row[0] or 0) != expected:
        return False
    result = conn.execute(
        sto.text('UPDATE wb_catalog_cache SET payload_json = :j, config_fingerprint = :f, '
                 'generation = generation + 1, refreshed_at = :now '
                 'WHERE project_uid = :p AND connection_id = :c AND generation = :g'),
        {'j': raw, 'f': config_fingerprint, 'now': now,
         'p': project_uid, 'c': connection_id, 'g': expected})
    return result.rowcount == 1


def read_catalog(conn, project_uid, connection_id):
    """单条目录；不存在返回 None；payload 损坏抛 CatalogCacheUnreadable（绝不当作无缓存）。"""
    row = conn.execute(sto.text('SELECT payload_json FROM wb_catalog_cache WHERE project_uid = :p '
                                'AND connection_id = :c'), {'p': project_uid, 'c': connection_id}).first()
    if row is None:
        return None
    data, unreadable = _parse_catalog_payload(row[0])
    if unreadable:
        raise CatalogCacheUnreadable('目录缓存内容损坏，无法安全读取：' + str(connection_id),
                                     connection_ids=[connection_id])
    return data


def load_catalogs(conn, project_uid):
    """(可读目录 dict, 损坏连接 id 列表)——损坏绝不静默 continue（2026-09-20 C02）。"""
    meta = load_catalog_meta(conn, project_uid)
    data = {cid: entry['payload'] for cid, entry in meta.items() if not entry['unreadable']}
    bad = sorted(cid for cid, entry in meta.items() if entry['unreadable'])
    return data, bad


def secret_generation(conn, project_uid, connection_id, namespace=NAMESPACE_CONNECTION):
    """凭据安全代际（只读整型）：0 = 无凭据；绝不返回密钥或密文（2026-09-20 冻结）。"""
    row = conn.execute(sto.text('SELECT secret_revision FROM wb_credentials WHERE namespace = :ns '
                                'AND owner_key = :o AND resource_id = :r'),
                       {'ns': namespace, 'o': project_uid, 'r': connection_id}).first()
    return int(row[0] or 0) if row else 0


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
