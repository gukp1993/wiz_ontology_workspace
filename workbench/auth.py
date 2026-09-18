"""账号与会话：scrypt 口令、会话令牌、请求级当前用户上下文。

约定（接口文档 06 分册）：
* 口令只存 PBKDF2-HMAC-SHA256 哈希（随机盐，600000 轮；stdlib 全版本可用，
  Python 3.9 无 hashlib.scrypt），明文永不落盘、不进日志、不回传。
* 会话令牌随机 32 字节，库里只存 SHA-256 摘要；Cookie 值为令牌本身（HttpOnly）。
* 当前用户经 bind_request(user) 放进 contextvars；业务/存储层用 require_user_id()
  取归属。CLI 与迁移脚本不设上下文，直接走 engine 层，不受影响。
* 数据完全隔离：所有带归属的查询按当前用户过滤；跨账号数据按不存在处理。
"""
import base64
import contextvars
import hashlib
import hmac
import os
import re
import secrets
import time

from workbench import storage
from workbench.storage.engine import read_connection, utcnow, write_tx
from sqlalchemy import text as sql_text

SESSION_COOKIE = 'wiz_session'
SESSION_TTL_SECONDS = 30 * 24 * 3600          # 30 天
SESSION_RENEW_AFTER = 6 * 3600                # 距上次活动超过 6 小时刷新到期时间
LOGIN_FAIL_DELAY = 0.2                        # 失败路径固定延迟，抑制暴力枚举

_PBKDF2_ROUNDS = 600000          # OWASP 对 PBKDF2-HMAC-SHA256 的建议量级
_PBKDF2_DKLEN = 32
_USERNAME_RE = re.compile(r'^[^\s\x00-\x1f]{1,32}$')
_MIN_PASSWORD, _MAX_PASSWORD = 4, 128

_current = contextvars.ContextVar('wiz_current_user', default=None)


class AuthError(Exception):
    """认证/账号层错误；message 为中文可读文案（HTTP 层映射 400）。"""


class AuthRequired(AuthError):
    """未登录或会话失效（HTTP 401）。"""


class DuplicateUsername(AuthError):
    """用户名已存在（HTTP 409）。"""


class InvalidCredentials(AuthError):
    """用户名或密码不正确（HTTP 401，与未登录同码不同文案）。"""


# --- 当前用户上下文 ---------------------------------------------------------------

def bind_request(user):
    """HTTP 层在鉴权通过后调用；绑定本请求的当前用户（None = 未登录）。"""
    _current.set(user)


def current_user():
    return _current.get()


def current_user_id():
    user = _current.get()
    return user['userId'] if user else ''


def require_user_id():
    """业务/存储层取归属；未登录抛 AuthRequired（HTTP 401）。"""
    user = _current.get()
    if not user:
        raise AuthRequired('请先登录')
    return user['userId']


# --- 口令 -------------------------------------------------------------------------

def hash_password(password):
    salt = os.urandom(16)
    digest = _derive(password, salt, _PBKDF2_ROUNDS, _PBKDF2_DKLEN)
    return 'pbkdf2_sha256${}${}${}'.format(
        _PBKDF2_ROUNDS, base64.b64encode(salt).decode('ascii'),
        base64.b64encode(digest).decode('ascii'))


def _derive(password, salt, rounds, dklen):
    return hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, int(rounds), dklen=dklen)


def verify_password(password, stored):
    if not isinstance(stored, str) or not stored.startswith('pbkdf2_sha256$'):
        return False
    try:
        _, rounds, salt_b64, hash_b64 = stored.split('$')
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        digest = _derive(password, salt, int(rounds), len(expected))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest, expected)


def validate_username(username):
    name = str(username or '').strip()
    if not name:
        raise AuthError('用户名不能为空')
    if not _USERNAME_RE.match(name):
        raise AuthError('用户名需为 1–32 个字符，且不含空白字符')
    return name


def validate_password(password):
    value = str(password or '')
    if len(value) < _MIN_PASSWORD:
        raise AuthError('密码至少 4 个字符')
    if len(value) > _MAX_PASSWORD:
        raise AuthError('密码过长（最多 128 个字符）')
    return value


# --- 用户 -------------------------------------------------------------------------

def _user_public(row):
    return {'username': row['username'], 'isAdmin': bool(row['is_admin']),
            'createdAt': row['created_at']}


def _lookup(conn, username_key):
    return conn.execute(sql_text('SELECT user_id, username, username_key, password_hash, is_admin, '
                                 'created_at, updated_at FROM wb_users WHERE username_key = :k'),
                        {'k': username_key}).mappings().first()


def create_user(username, password, is_admin=False, now=None):
    """创建账号；重名抛 DuplicateUsername。返回公开字段（绝不含口令哈希）。"""
    name = validate_username(username)
    secret = validate_password(password)
    key = name.casefold()
    storage.ensure_ready()
    now = now or utcnow()
    from workbench.storage import engine as sto

    def body(conn):
        if _lookup(conn, key) is not None:
            raise DuplicateUsername('用户名已被使用')
        user_id = sto.new_id()
        conn.execute(sql_text('INSERT INTO wb_users (user_id, username, username_key, password_hash, '
                              'is_admin, created_at, updated_at) VALUES (:u, :n, :k, :h, :a, :c, :c2)'),
                     {'u': user_id, 'n': name, 'k': key, 'h': hash_password(secret),
                      'a': 1 if is_admin else 0, 'c': now, 'c2': now})
        return {'userId': user_id, 'username': name, 'isAdmin': bool(is_admin), 'createdAt': now}

    with write_tx() as tx:
        return tx.run(body)


def set_password(username, password):
    """重置指定账号口令（CLI 用）；账号不存在返回 False。"""
    name = validate_username(username)
    secret = validate_password(password)
    key = name.casefold()
    storage.ensure_ready()

    def body(conn):
        row = _lookup(conn, key)
        if row is None:
            return False
        conn.execute(sql_text('UPDATE wb_users SET password_hash = :h, updated_at = :now '
                              'WHERE user_id = :u'),
                     {'h': hash_password(secret), 'now': utcnow(), 'u': row['user_id']})
        return True

    with write_tx() as tx:
        return tx.run(body)


def authenticate(username, password):
    """校验账号密码；成功返回公开字段，失败抛 InvalidCredentials（统一文案 + 固定延迟）。"""
    storage.ensure_ready()
    name = str(username or '').strip()
    key = name.casefold()
    with read_connection() as conn:
        row = _lookup(conn, key) if key else None
    ok = bool(row) and verify_password(str(password or ''), row['password_hash'])
    if not ok:
        time.sleep(LOGIN_FAIL_DELAY)
        raise InvalidCredentials('用户名或密码不正确')
    return {'userId': row['user_id'], 'username': row['username'],
            'isAdmin': bool(row['is_admin']), 'createdAt': row['created_at']}


def user_by_id(user_id):
    storage.ensure_ready()
    with read_connection() as conn:
        row = conn.execute(sql_text('SELECT user_id, username, is_admin, created_at FROM wb_users '
                                    'WHERE user_id = :u'), {'u': user_id}).mappings().first()
    return ({'userId': row['user_id'], 'username': row['username'],
             'isAdmin': bool(row['is_admin']), 'createdAt': row['created_at']} if row else None)


def list_users():
    storage.ensure_ready()
    with read_connection() as conn:
        rows = conn.execute(sql_text('SELECT username, is_admin, created_at FROM wb_users '
                                     'ORDER BY created_at, username')).mappings().all()
    return [_user_public(row) for row in rows]


# --- 会话 -------------------------------------------------------------------------

def _token_hash(token):
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def _now_ts():
    return time.time()


def _iso(ts):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def open_session(user_id, now_ts=None):
    """签发会话：返回 (token, 会话元数据)。令牌只在本次返回中出现，库里只有摘要。"""
    storage.ensure_ready()
    from workbench.storage import engine as sto
    now_ts = now_ts if now_ts is not None else _now_ts()
    token = secrets.token_urlsafe(32)
    session_id = sto.new_id()
    expires = now_ts + SESSION_TTL_SECONDS

    def body(conn):
        conn.execute(sql_text('DELETE FROM wb_sessions WHERE user_id = :u AND expires_at < :now'),
                     {'u': user_id, 'now': _iso(now_ts)})  # 顺手清理过期会话
        conn.execute(sql_text('INSERT INTO wb_sessions (session_id, user_id, token_hash, created_at, '
                              'last_seen_at, expires_at) VALUES (:s, :u, :h, :c, :c2, :e)'),
                     {'s': session_id, 'u': user_id, 'h': _token_hash(token),
                      'c': _iso(now_ts), 'c2': _iso(now_ts), 'e': _iso(expires)})
        return {'sessionId': session_id, 'expiresAt': _iso(expires),
                'maxAge': SESSION_TTL_SECONDS}

    with write_tx() as tx:
        return token, tx.run(body)


def resolve_session(token, now_ts=None):
    """校验会话令牌：有效返回当前用户 dict（含 userId），否则 None；顺带滑动续期。"""
    if not token or not isinstance(token, str) or len(token) > 512:
        return None
    storage.ensure_ready()
    now_ts = now_ts if now_ts is not None else _now_ts()
    th = _token_hash(token)
    with read_connection() as conn:
        row = conn.execute(sql_text(
            'SELECT s.session_id, s.user_id, s.last_seen_at, s.expires_at, u.username, u.is_admin, '
            'u.created_at FROM wb_sessions s JOIN wb_users u ON u.user_id = s.user_id '
            'WHERE s.token_hash = :h'), {'h': th}).mappings().first()
    if row is None:
        return None
    try:
        expires_ts = _parse_iso(row['expires_at'])
        last_ts = _parse_iso(row['last_seen_at'])
    except ValueError:
        return None
    if expires_ts <= now_ts:
        revoke_session(token)
        return None
    if now_ts - last_ts > SESSION_RENEW_AFTER:
        _touch_session(row['session_id'], now_ts)
    return {'userId': row['user_id'], 'username': row['username'],
            'isAdmin': bool(row['is_admin']), 'createdAt': row['created_at']}


def _parse_iso(value):
    from datetime import datetime
    return datetime.fromisoformat(str(value)).timestamp()


def _touch_session(session_id, now_ts):
    def body(conn):
        conn.execute(sql_text('UPDATE wb_sessions SET last_seen_at = :l, expires_at = :e '
                              'WHERE session_id = :s'),
                     {'l': _iso(now_ts), 'e': _iso(now_ts + SESSION_TTL_SECONDS), 's': session_id})

    with write_tx() as tx:
        tx.run(body)


def revoke_session(token):
    """顶级退出：删除该令牌的会话行（缺失静默）。"""
    if not token:
        return
    storage.ensure_ready()

    def body(conn):
        conn.execute(sql_text('DELETE FROM wb_sessions WHERE token_hash = :h'),
                     {'h': _token_hash(str(token))})

    with write_tx() as tx:
        tx.run(body)


def purge_expired(now_ts=None):
    """清理全部过期会话（启动或维护时可调用）；返回删除行数。"""
    storage.ensure_ready()
    now_ts = now_ts if now_ts is not None else _now_ts()

    def body(conn):
        result = conn.execute(sql_text('DELETE FROM wb_sessions WHERE expires_at < :now'),
                              {'now': _iso(now_ts)})
        return int(result.rowcount or 0)

    with write_tx() as tx:
        return tx.run(body)
