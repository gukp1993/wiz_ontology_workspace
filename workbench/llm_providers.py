"""工作台级 LLM 提供方登记（数据库版：wb_model_configs + wb_credentials + wb_settings）。

* 元数据在 wb_model_configs（不含明文 API Key）；密钥在 wb_credentials
  （namespace='model'，owner_key=账号 user id，resource_id=provider_id）。
  2026-09-18 账号体系：模型配置与密钥按账号隔离（wb_model_configs.owner_user_id
  为主键一部分；旧 'global' 归属由 transfer assign-owner 迁移到目标账号）。
* 默认项记录在 wb_settings 的 models.default_provider_id；首个提供方、设默认、
  删默认的兜底行为与文件版一致，且默认切换与配置写入在同一事务（model-default
  护栏行串行化，防并发出现多个默认）。
* 密钥只写不读回：HTTP 层只暴露 list_metadata 的安全字段；read() 仅供服务端
  执行器/连通性测试，密钥绝不进响应、日志、快照。
"""
import re
import secrets as pysecrets

from workbench import auth
from workbench import storage
from workbench.storage import assets as store
from workbench.storage import configuration as config_store
from workbench.storage.engine import read_connection, write_tx, utcnow
from sqlalchemy import text as sql_text
from workbench.storage import secret_store

VAULT = None  # 旧文件目录已下线；保留名字避免外部误用
_ID = re.compile(r'llm-[a-z0-9][a-z0-9-]{0,31}')
_MAX_NAME = 60
_MAX_TEXT = 500
MAX_TIMEOUT = 300
MODEL_NS = config_store.NAMESPACE_MODEL


def _owner():
    """当前账号的凭据 owner_key；未登录抛 AuthRequired（HTTP 401）。"""
    return auth.require_user_id()


def _clean(provider_id):
    if not isinstance(provider_id, str) or not _ID.fullmatch(provider_id):
        raise ValueError('LLM 提供方标识无效')
    return provider_id


def _rows(conn, owner_user_id):
    rows = conn.execute(
        sql_text('SELECT provider_id, name, endpoint, model, timeout_seconds, temperature, '
                       'secret_id, metadata_revision, updated_at FROM wb_model_configs '
                       'WHERE owner_user_id = :o'), {'o': owner_user_id or ''}).mappings().all()
    return [dict(row) for row in rows]


def _metadata_of(row):
    try:
        temperature = float(row.get('temperature')) if row.get('temperature') is not None else 0
    except (TypeError, ValueError):
        temperature = 0
    try:
        timeout = int(row.get('timeout_seconds')) if row.get('timeout_seconds') is not None else 60
    except (TypeError, ValueError):
        timeout = 60
    return {'id': row['provider_id'], 'name': str(row.get('name') or row['provider_id']),
            'model': str(row.get('model') or ''), 'endpoint': str(row.get('endpoint') or ''),
            'timeout': timeout, 'temperature': temperature,
            'isDefault': False, 'keyConfigured': bool(row.get('secret_id'))}


def _load_all(conn, owner_user_id):
    return _rows(conn, owner_user_id)


def list_metadata():
    """当前账号的提供方元数据（无密钥），默认提供方排最前；api_key 永不出现。"""
    owner = _owner()
    storage.ensure_ready()
    with read_connection() as conn:
        rows = _load_all(conn, owner)
        default_id = config_store.get_user_setting(conn, owner, config_store.DEFAULT_PROVIDER_KEY, '')
    items = [_metadata_of(row) for row in rows]
    for item in items:
        item['isDefault'] = item['id'] == default_id
    items.sort(key=lambda x: (not x['isDefault'], x['name'], x['id']))
    return items


def metadata_ids():
    owner = _owner()
    storage.ensure_ready()
    with read_connection() as conn:
        return {row['provider_id'] for row in _load_all(conn, owner)}


def read(provider_id):
    """完整配置（含解密密钥）。仅供服务端执行器/连通性测试，绝不进响应。"""
    _clean(provider_id)
    owner = _owner()
    storage.ensure_ready()
    with read_connection() as conn:
        row = next((r for r in _load_all(conn, owner) if r['provider_id'] == provider_id), None)
        if row is None:
            return None
        secret = config_store.get_secret(conn, MODEL_NS, owner, provider_id)
    return {'id': provider_id, 'name': row['name'], 'endpoint': row['endpoint'], 'model': row['model'],
            'timeout': int(row['timeout_seconds'] or 60), 'temperature': float(row['temperature'] or 0),
            'api_key': secret or '', 'is_default': False}


def default_provider():
    """当前账号的默认提供方完整配置；无任何提供方返回 None。"""
    owner = _owner()
    storage.ensure_ready()
    with read_connection() as conn:
        rows = _load_all(conn, owner)
        if not rows:
            return None
        default_id = config_store.get_user_setting(conn, owner, config_store.DEFAULT_PROVIDER_KEY, '')
        row = next((r for r in rows if r['provider_id'] == default_id), rows[0])
        secret = config_store.get_secret(conn, MODEL_NS, owner, row['provider_id'])
    return {'id': row['provider_id'], 'name': row['name'], 'endpoint': row['endpoint'], 'model': row['model'],
            'timeout': int(row['timeout_seconds'] or 60), 'temperature': float(row['temperature'] or 0),
            'api_key': secret or '', 'is_default': True}


def resolve(provider_id):
    """节点指定的 providerId → 配置；空 → 默认提供方。找不到抛 ValueError。"""
    if provider_id:
        payload = read(provider_id)
        if payload is None:
            raise ValueError('LLM 提供方不存在或已被删除')
        return payload
    payload = default_provider()
    if payload is None:
        raise ValueError('尚未配置 LLM 提供方：请到「更多工具 → LLM 配置」添加')
    return payload


def _validate(name, endpoint, model, timeout, temperature, api_key):
    name = str(name or '').strip()
    endpoint = str(endpoint or '').strip()
    model = str(model or '').strip()
    if not name:
        raise ValueError('LLM 提供方名称不能为空')
    if len(name) > _MAX_NAME:
        raise ValueError('LLM 提供方名称过长')
    if not endpoint.lower().startswith(('http://', 'https://')):
        raise ValueError('接口地址仅支持 http(s)')
    if len(endpoint) > _MAX_TEXT:
        raise ValueError('接口地址过长')
    if not model:
        raise ValueError('模型名称不能为空')
    if len(model) > _MAX_TEXT:
        raise ValueError('模型名称过长')
    try:
        timeout = int(timeout)
    except (TypeError, ValueError):
        raise ValueError('超时无效') from None
    if not 1 <= timeout <= MAX_TIMEOUT:
        raise ValueError(f'超时须为 1–{MAX_TIMEOUT} 秒')
    try:
        temperature = float(temperature)
    except (TypeError, ValueError):
        raise ValueError('温度无效') from None
    if not 0 <= temperature <= 2:
        raise ValueError('温度须为 0–2')
    api_key = str(api_key or '')
    if len(api_key) > _MAX_TEXT:
        raise ValueError('API Key 过长')
    if api_key and ('\n' in api_key or '\r' in api_key):
        raise ValueError('API Key 不能包含换行')
    return name, endpoint, model, timeout, temperature, api_key


def save(name, endpoint, model, api_key='', timeout=60, temperature=0, is_default=False, provider_id=''):
    """写入或覆盖当前账号的一条提供方配置，返回元数据（不含密钥）。api_key 留空 = 沿用已存密钥。

    元数据、密钥与默认项设置在同一事务内完成（model-default 护栏串行化默认切换）；
    配置与模型密钥都按账号隔离。
    """
    name, endpoint, model, timeout, temperature, api_key = _validate(name, endpoint, model,
                                                                     timeout, temperature, api_key)
    owner = _owner()
    storage.ensure_ready()
    if provider_id:
        _clean(provider_id)
        existing = read(provider_id)
        if existing is None:
            raise ValueError('LLM 提供方不存在或已被删除')
    else:
        existing = None
        for _ in range(20):
            provider_id = 'llm-' + pysecrets.token_hex(5)
            if read(provider_id) is None:
                break
        else:
            raise ValueError('LLM 提供方标识生成失败，请重试')
    if not api_key:
        # 编辑时留空 = 沿用已保存密钥；首次配置没有可沿用的密钥即报错
        with read_connection() as conn:
            api_key = config_store.get_secret(conn, MODEL_NS, owner, provider_id) or ''
    if not api_key:
        raise ValueError('API Key 不能为空（首次配置必须填写；编辑时留空表示沿用已保存密钥）')

    def body(conn):
        store.bump_guard(conn, 'model-default')
        existing_rows = _load_all(conn, owner)
        first = not existing_rows
        secret_id = config_store.put_secret(conn, MODEL_NS, owner, provider_id, api_key,
                                            display_name=name, now=utcnow())
        row = next((r for r in existing_rows if r['provider_id'] == provider_id), None)
        revision = (row['metadata_revision'] + 1) if row else 1
        if row is not None:
            conn.execute(sql_text(
                'UPDATE wb_model_configs SET name = :n, endpoint = :e, model = :m, '
                'timeout_seconds = :t, temperature = :tp, secret_id = :s, '
                'metadata_revision = :r, updated_at = :now '
                'WHERE provider_id = :p AND owner_user_id = :o'),
                {'n': name, 'e': endpoint, 'm': model, 't': timeout, 'tp': temperature,
                 's': secret_id, 'r': revision, 'now': utcnow(), 'p': provider_id, 'o': owner})
        else:
            conn.execute(sql_text(
                'INSERT INTO wb_model_configs (provider_id, owner_user_id, name, endpoint, model, '
                'timeout_seconds, temperature, secret_id, metadata_revision, updated_at) '
                'VALUES (:p, :o, :n, :e, :m, :t, :tp, :s, :r, :now)'),
                {'p': provider_id, 'o': owner, 'n': name, 'e': endpoint, 'm': model, 't': timeout,
                 'tp': temperature, 's': secret_id, 'r': revision, 'now': utcnow()})
        want_default = bool(is_default) or first
        current_default = config_store.get_user_setting(conn, owner, config_store.DEFAULT_PROVIDER_KEY, '')
        if want_default and current_default != provider_id:
            config_store.put_user_setting(conn, owner, config_store.DEFAULT_PROVIDER_KEY,
                                          provider_id, now=utcnow())
        return {'id': provider_id, 'name': name, 'model': model,
                'isDefault': bool(want_default or current_default == provider_id),
                'keyConfigured': True}

    with write_tx() as tx:
        return tx.run(body)


def set_default(provider_id):
    """把已存在的提供方设为默认；幂等（已是默认时不重复写）。返回元数据。

    只改设置表指针，不触碰 wb_model_configs 与密钥（metadata_revision 不变）。
    标识非法抛 ValueError；提供方不存在返回 None（调用方按 404 处理）。
    """
    _clean(provider_id)
    owner = _owner()
    storage.ensure_ready()
    with read_connection() as conn:
        if not any(r['provider_id'] == provider_id for r in _load_all(conn, owner)):
            return None

    def body(conn):
        store.bump_guard(conn, 'model-default')
        row = next(r for r in _load_all(conn, owner) if r['provider_id'] == provider_id)
        if config_store.get_user_setting(conn, owner, config_store.DEFAULT_PROVIDER_KEY, '') != provider_id:
            config_store.put_user_setting(conn, owner, config_store.DEFAULT_PROVIDER_KEY,
                                          provider_id, now=utcnow())
        return {'id': provider_id, 'name': row['name'], 'model': row['model'],
                'isDefault': True, 'keyConfigured': bool(row.get('secret_id'))}

    with write_tx() as tx:
        return tx.run(body)


def clear(provider_id):
    """删除提供方；缺失静默。删除默认项后自动指派剩余第一个为默认。"""
    _clean(provider_id)
    owner = _owner()
    storage.ensure_ready()

    def body(conn):
        row = next((r for r in _load_all(conn, owner) if r['provider_id'] == provider_id), None)
        if row is None:
            return
        store.bump_guard(conn, 'model-default')
        conn.execute(sql_text('DELETE FROM wb_model_configs WHERE provider_id = :p '
                              'AND owner_user_id = :o'), {'p': provider_id, 'o': owner})
        config_store.clear_secret(conn, MODEL_NS, owner, provider_id)
        current_default = config_store.get_user_setting(conn, owner, config_store.DEFAULT_PROVIDER_KEY, '')
        if current_default == provider_id:
            remaining = _load_all(conn, owner)
            config_store.put_user_setting(conn, owner, config_store.DEFAULT_PROVIDER_KEY,
                                          remaining[0]['provider_id'] if remaining else '',
                                          now=utcnow())

    with write_tx() as tx:
        tx.run(body)
