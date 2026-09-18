"""凭据认证加密（AES-256-GCM）与库外根密钥。

* 根密钥：<DATA_ROOT>/keys/wb-root.key（0700 目录、0600 文件、32 字节随机）。
  首次使用自动生成；已存在则必须可用——绝不覆盖重建（丢失后密文无法解密，
  明确报错而非换新 key 掩盖问题）。可用 WIZ_ROOT_KEY_FILE 覆盖位置。
* 每次加密新 nonce；AAD 绑定 namespace|owner_key|resource_id，防跨行调换密文。
* key_id = SHA-256(root key) 前 16 hex；轮换属后续运维流程，本期只记录。
* 明文只经 SecretStore 解密返回给服务端执行器，绝不进响应/日志/导出。
"""
import base64
import hashlib
import os
import secrets as pysecrets
from pathlib import Path

from workbench.paths import DATA_ROOT

KEY_DIR_MODE = 0o700
KEY_FILE_MODE = 0o600


class SecretKeyError(Exception):
    """根密钥缺失/不可读——需要运维介入，不得自动重建。"""


def key_path():
    explicit = os.environ.get('WIZ_ROOT_KEY_FILE', '').strip()
    if explicit:
        return Path(explicit)
    return Path(str(DATA_ROOT)) / 'keys' / 'wb-root.key'


def _load_or_create_root_key():
    path = key_path()
    if path.is_file():
        raw = path.read_bytes()
        try:
            key = base64.b64decode(raw.strip())
        except Exception as exc:
            raise SecretKeyError(f'根密钥文件不可解析：{path}（{exc}）') from exc
        if len(key) != 32:
            raise SecretKeyError(f'根密钥长度无效（{len(key)} 字节，应为 32）：{path}')
        return key
    # 首次生成：目录 0700、文件 0600；写临时文件后原子替换
    key = pysecrets.token_bytes(32)
    path.parent.mkdir(parents=True, exist_ok=True, mode=KEY_DIR_MODE)
    import tempfile
    handle = tempfile.NamedTemporaryFile(dir=str(path.parent), delete=False)
    try:
        with handle:
            handle.write(base64.b64encode(key))
        os.chmod(handle.name, KEY_FILE_MODE)
        os.replace(handle.name, path)
    except Exception:
        try:
            os.unlink(handle.name)
        except OSError:
            pass
        raise
    return key


def key_id():
    return hashlib.sha256(_root_key()).hexdigest()[:16]


_root = None


def _root_key():
    global _root
    if _root is None:
        _root = _load_or_create_root_key()
    return _root


def _aad(namespace, owner_key, resource_id):
    return f'{namespace}|{owner_key}|{resource_id}'.encode('utf-8')


def encrypt(plaintext, namespace, owner_key, resource_id):
    """返回 (key_id, nonce, ciphertext)。明文为空串也返回有效密文。"""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = pysecrets.token_bytes(12)
    box = AESGCM(_root_key())
    blob = box.encrypt(nonce, str(plaintext or '').encode('utf-8'),
                       _aad(namespace, owner_key, resource_id))
    return key_id(), nonce, blob


def decrypt(stored_key_id, nonce, ciphertext, namespace, owner_key, resource_id):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    current = key_id()
    if stored_key_id != current:
        raise SecretKeyError(f'凭据由其他根密钥加密（key_id {stored_key_id}，当前 {current}）；'
                             f'请恢复正确的根密钥文件后再试')
    box = AESGCM(_root_key())
    try:
        return box.decrypt(nonce, bytes(ciphertext), _aad(namespace, owner_key, resource_id)).decode('utf-8')
    except Exception as exc:
        raise SecretKeyError('凭据解密失败：根密钥不匹配或密文损坏') from exc
