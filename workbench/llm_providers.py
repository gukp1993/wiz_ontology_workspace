"""工作台级 LLM 提供方登记（函数编排 Python/计算节点的 LLM 代执行用）。

独立命名空间（不与显示格式化的 format-llm.json、项目 api-credentials 混用）：

    <DATA_ROOT>/ontology/vault/llm-providers/<providerId>.json

边界（对齐 workbench/api_credentials.py）：
* 只做本地配置存储与元数据列举；连通性测试在 llm_client（探测类，不持全局锁）。
* 密钥只写不读回：HTTP 层只暴露 list_metadata 的 id/name/model/isDefault/
  keyConfigured；响应、日志、异常信息中都不出现真实密钥。read() 仅供服务端
  执行器读取，调用方必须保证密钥不外传。
* 文件 0600、目录 0700、临时文件 + os.replace 原子替换；路径逃逸防护与
  api_credentials.py 一致。
"""
import json
import os
import re
import secrets
import tempfile

from workbench.paths import DATA_ROOT

VAULT = DATA_ROOT / 'ontology/vault/llm-providers'
_ID = re.compile(r'llm-[a-z0-9][a-z0-9-]{0,31}')
_MAX_NAME = 60
_MAX_TEXT = 500
MAX_TIMEOUT = 300


def _clean(provider_id):
    if not isinstance(provider_id, str) or not _ID.fullmatch(provider_id):
        raise ValueError('LLM 提供方标识无效')
    return provider_id


def _file(provider_id):
    path = VAULT / (_clean(provider_id) + '.json')
    if path.resolve().parent != VAULT.resolve():
        raise ValueError('LLM 提供方存储路径无效')
    return path


def _new_id():
    for _ in range(20):
        candidate = 'llm-' + secrets.token_hex(5)
        if not (VAULT / (candidate + '.json')).exists():
            return candidate
    raise ValueError('LLM 提供方标识生成失败，请重试')


def _write(payload):
    VAULT.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = _file(payload['id'])
    body = json.dumps(payload, ensure_ascii=False)
    handle = tempfile.NamedTemporaryFile('w', dir=str(path.parent), delete=False, encoding='utf-8')
    try:
        with handle:
            handle.write(body)
        os.chmod(handle.name, 0o600)
        os.replace(handle.name, path)
    except Exception:
        try:
            os.unlink(handle.name)
        except OSError:
            pass
        raise


def _load_all():
    items = []
    try:
        files = sorted(VAULT.glob('*.json'))
    except OSError:
        return items
    for path in files:
        try:
            payload = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError, UnicodeDecodeError):
            continue
        if isinstance(payload, dict) and _ID.fullmatch(str(payload.get('id') or '')):
            items.append(payload)
    return items


def list_metadata():
    """元数据列表（无密钥），默认提供方排最前，其余按名称排序。"""
    items = [{'id': p['id'], 'name': str(p.get('name') or p['id']),
              'model': str(p.get('model') or ''), 'isDefault': bool(p.get('is_default')),
              'keyConfigured': bool(p.get('api_key'))}
             for p in _load_all()]
    items.sort(key=lambda x: (not x['isDefault'], x['name'], x['id']))
    return items


def metadata_ids():
    return {p['id'] for p in _load_all()}


def read(provider_id):
    """完整配置（含密钥）。仅供服务端执行器/连通性测试使用，绝不进响应。"""
    path = _file(provider_id)
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding='utf-8'))


def default_provider():
    """默认提供方完整配置；无任何提供方返回 None。"""
    items = _load_all()
    if not items:
        return None
    for item in items:
        if item.get('is_default'):
            return item
    return items[0]


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


def save(name, endpoint, model, api_key='', timeout=60, temperature=0, is_default=False, provider_id=''):
    """写入或覆盖一条提供方配置，返回元数据（不含密钥）。api_key 留空 = 沿用已存密钥。"""
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
    if provider_id:
        existing = read(provider_id)
        if existing is None:
            raise ValueError('LLM 提供方不存在或已被删除')
    else:
        existing = None
        provider_id = _new_id()
        if not _load_all():  # 第一个提供方自动成为默认，避免节点无可用默认
            is_default = True
    if not api_key:
        # 编辑时留空 = 沿用已保存密钥；首次配置没有可沿用的密钥即报错
        api_key = (existing or {}).get('api_key') or ''
    if not api_key:
        raise ValueError('API Key 不能为空（首次配置必须填写；编辑时留空表示沿用已保存密钥）')
    payload = {'id': provider_id, 'name': name, 'endpoint': endpoint, 'model': model,
               'timeout': timeout, 'temperature': temperature, 'api_key': api_key,
               'is_default': bool(is_default)}
    if is_default:
        for item in _load_all():
            if item['id'] != provider_id and item.get('is_default'):
                item['is_default'] = False
                _write(item)
    _write(payload)
    return {'id': provider_id, 'name': name, 'model': model, 'isDefault': bool(is_default),
            'keyConfigured': bool(api_key)}


def clear(provider_id):
    """删除提供方；缺失静默。删除默认项后自动指派剩余第一个为默认。"""
    path = _file(provider_id)
    was_default = False
    payload = read(provider_id) if path.is_file() else None
    if payload:
        was_default = bool(payload.get('is_default'))
    try:
        path.unlink()
    except FileNotFoundError:
        return
    if was_default:
        remaining = _load_all()
        if remaining:
            remaining[0]['is_default'] = True
            _write(remaining[0])
