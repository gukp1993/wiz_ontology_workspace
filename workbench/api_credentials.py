"""项目级 API 凭据登记（动作接口映射 P3，最小实现）。

这是 API 凭据（动作接口出站调用所需的密钥）的**独立命名空间**：

    <DATA_ROOT>/ontology/vault/projects/<projectId>/api-credentials/<credentialId>.json

目录与 secrets.py 的数据库连接密码 vault 同根（ontology/vault/projects）但
**不同子目录**：连接密码写 <projectId>/<connectionId>，本模块只写
<projectId>/api-credentials/，两者互不覆盖，也不会把连接标识当凭据标识。
本模块不修改也不依赖 secrets.py 的读写路径。

边界（刻意保持最小）：
* 只做本地配置存储与元数据列举，**不做任何网络调用**，不解析凭据用途，
  不提供全局凭据管理产品。
* 密钥只写不读回浏览器：HTTP 层只暴露 list_metadata 的 id/name；响应、
  日志、异常信息与项目配置/快照/导出中都不出现真实密钥。
* 文件 0600、目录 0700、临时文件 + os.replace 原子替换；路径逃逸防护与
  secrets.py 一致（正则校验 + resolve() 父目录比较）。

read() 仅供未来服务端出站调用读取密钥，调用方必须保证密钥不外传。
"""
import json
import os
import re
import secrets
import tempfile

from workbench.paths import DATA_ROOT

VAULT = DATA_ROOT / 'ontology/vault/projects'
SUBDIR = 'api-credentials'
_ID = re.compile(r'[a-z0-9][a-z0-9_-]{0,63}')
MAX_NAME = 60
MAX_SECRET = 512


def _clean_id(project_id, credential_id):
    if not _ID.fullmatch(str(project_id or '')):
        raise ValueError('凭据存储路径无效')
    if not _ID.fullmatch(str(credential_id or '')):
        raise ValueError('凭据标识无效')


def _dir(project_id):
    """返回（并校验）该项目的凭据目录，逃逸时抛 ValueError('凭据存储路径无效')。"""
    if not _ID.fullmatch(str(project_id or '')):
        raise ValueError('凭据存储路径无效')
    expected = VAULT.resolve() / str(project_id) / SUBDIR
    path = VAULT / str(project_id) / SUBDIR
    if path.resolve() != expected:
        raise ValueError('凭据存储路径无效')
    return path


def _file(project_id, credential_id):
    _clean_id(project_id, credential_id)
    directory = _dir(project_id)
    path = directory / (str(credential_id) + '.json')
    if path.resolve().parent != directory.resolve():
        raise ValueError('凭据存储路径无效')
    return path


def _new_id(directory):
    for _ in range(20):
        candidate = 'cred-' + secrets.token_hex(6)
        if not (directory / (candidate + '.json')).exists():
            return candidate
    raise ValueError('凭据标识生成失败，请重试')


def list_metadata(project_id):
    """列出凭据元数据 [{'id','name'}]，按 name 排序；不含密钥；目录不存在返回 []。

    单个损坏/不可读文件被跳过，避免一个坏文件让整表 500。
    """
    directory = _dir(project_id)
    items = []
    try:
        files = sorted(directory.glob('*.json'))
    except OSError:
        return items
    for path in files:
        try:
            payload = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError, UnicodeDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        credential_id = str(payload.get('id') or '')
        if not _ID.fullmatch(credential_id):
            continue
        name = str(payload.get('name') or credential_id)
        items.append({'id': credential_id, 'name': name})
    items.sort(key=lambda item: (item['name'], item['id']))
    return items


def ids(project_id):
    """已登记凭据标识集合（项目校验/引用检查用）。"""
    return {item['id'] for item in list_metadata(project_id)}


def save(project_id, name, secret, credential_id=''):
    """写入或覆盖一条凭据，返回 {'id','name'}（不回传密钥）。"""
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
    credential_id = str(credential_id or '')
    if credential_id:
        _clean_id(project_id, credential_id)
        path = _file(project_id, credential_id)
    else:
        directory = _dir(project_id)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        credential_id = _new_id(directory)
        path = _file(project_id, credential_id)
    body = json.dumps({'id': credential_id, 'name': name, 'secret': secret}, ensure_ascii=False)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
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
    return {'id': credential_id, 'name': name}



def clear(project_id, credential_id):
    """删除凭据；缺失静默。"""
    path = _file(project_id, credential_id)
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def exists(project_id, credential_id):
    return _file(project_id, credential_id).is_file()
