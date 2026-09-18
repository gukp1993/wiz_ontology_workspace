"""测试用认证客户端（20260918 登录与账号体系）：注册/登录并返回带会话 Cookie 的请求头。

HTTP 集成测试先调 login_headers() 拿到含 Cookie 的头部，再拼进各请求；
register_or_login 幂等：首次注册，已存在（409）则登录。仅用标准库，不发真实网络请求
（只连测试隔离实例）。
"""
import json
import time
import urllib.error
import urllib.request

DEFAULT_USER = 'tester'
DEFAULT_PASSWORD = 'test1234'


def _raw(base, path, payload=None, method='POST', headers=None):
    data = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    hdrs = {'Content-Type': 'application/json'}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(base + path, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode()
            return resp.status, (json.loads(body) if body else {}), resp.headers
    except urllib.error.HTTPError as exc:
        body = exc.read().decode()
        try:
            parsed = json.loads(body) if body else {}
        except ValueError:
            parsed = body
        return exc.code, parsed, exc.headers


def wait_ready(base, timeout=30, interval=0.3):
    """等隔离服务就绪：用免登录的 /api/auth-state 探测（不依赖会话）。"""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            code, body, _ = _raw(base, '/api/auth-state', method='GET')
            if code == 200:
                return body
            last = f'{code} {body}'
        except Exception as exc:  # 连接被拒等：继续等
            last = str(exc)
        time.sleep(interval)
    raise RuntimeError(f'服务未在 {timeout} 秒内就绪：{last}')


def register_or_login(base, username=DEFAULT_USER, password=DEFAULT_PASSWORD):
    """返回 (user_dict, cookie_value)：首次注册（201）或已存在时登录（200）。"""
    code, body, headers = _raw(base, '/api/auth-register',
                               {'username': username, 'password': password})
    if code == 409:
        code, body, headers = _raw(base, '/api/auth-login',
                                   {'username': username, 'password': password})
    if code not in (200, 201):
        raise RuntimeError(f'测试账号登录失败：HTTP {code} {body}')
    token = _cookie_from(headers, body)
    if not token:
        raise RuntimeError('登录响应未携带会话 Cookie')
    return body.get('user'), token


def _cookie_from(headers, body):
    cookie = headers.get('Set-Cookie') if headers is not None else None
    if not cookie:
        return ''
    for part in cookie.split(';'):
        name, _, value = part.strip().partition('=')
        if name == 'wiz_session':
            return value
    return ''


def auth_headers(base, username=DEFAULT_USER, password=DEFAULT_PASSWORD, user_headers=None):
    """注册/登录并返回可直接拼进请求的头部（含 Cookie 与会话路径所需字段）。

    user_headers：额外的业务头部（如 Origin）；本函数只补 Content-Type 与 Cookie。
    """
    _, token = register_or_login(base, username, password)
    headers = {'Content-Type': 'application/json', 'Cookie': 'wiz_session=' + token}
    if user_headers:
        headers.update(user_headers)
    return headers


def user_id_from_db(root, username=DEFAULT_USER):
    """从临时根库解析账号 id（测试进程内直调存储层时用）。"""
    import sqlite3
    db = str(root) + '/data/workbench.sqlite3'
    con = sqlite3.connect(db)
    try:
        row = con.execute('SELECT user_id FROM wb_users WHERE username_key = ?',
                          (str(username).strip().casefold(),)).fetchone()
    finally:
        con.close()
    if not row:
        raise RuntimeError('测试账号不存在：' + str(username))
    return row[0]


def bind_test_user(root, username=DEFAULT_USER):
    """把测试账号绑定为「当前用户」，供测试进程内直调域函数（等价请求上下文）。

    仅测试用：真实请求路径由 server.py 鉴权后绑定，测试没有请求对象。
    返回 userId。
    """
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from workbench import auth
    uid = user_id_from_db(root, username)
    auth.bind_request({'userId': uid, 'username': username, 'isAdmin': False, 'createdAt': ''})
    return uid


def bind_fixture_user(username='tester', password='test1234'):
    """域级测试的通用夹具：确保测试账号存在并绑定为当前用户。

    适用：测试进程内直调域函数（workspaces/projects/flows/llm_providers 等）。
    前提：调用方已设置 WIZ_WORKBENCH_ROOT 为临时根（storage.ensure_ready 会惰性建库）。
    返回 userId；重复调用幂等。
    """
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from workbench import auth
    from workbench import storage
    storage.ensure_ready()
    existing = next((u for u in auth.list_users()
                     if u['username'].casefold() == username.casefold()), None)
    if existing is None:
        created = auth.create_user(username, password)
        uid = created['userId']
    else:
        from workbench.storage.engine import read_connection
        from sqlalchemy import text as _sql
        with read_connection() as conn:
            uid = conn.execute(_sql('SELECT user_id FROM wb_users WHERE username_key = :k'),
                               {'k': username.casefold()}).first()[0]
    auth.bind_request({'userId': uid, 'username': username, 'isAdmin': False, 'createdAt': ''})
    return uid
