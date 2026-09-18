"""认证 HTTP 边界：登录态、登录、注册、退出（接口文档 06 分册）。

* 契约：请求/响应字段、状态码、Cookie 属性以 06 分册为准；本模块不做安全边界
  之外的业务（Origin/大小限制仍在 server.py）。
* Cookie：wiz_session；HttpOnly + SameSite=Strict + Path=/，30 天；退出下发 Max-Age=0。
* 失败路径：用户名不存在与密码错误同文案（auth.authenticate 内已有固定延迟）。
"""
from workbench import auth

COOKIE_NAME = auth.SESSION_COOKIE


def _public(user):
    return {'username': user['username'], 'isAdmin': bool(user.get('isAdmin')),
            'createdAt': user.get('createdAt', '')}


def get_auth_state(query):
    """GET /api/auth-state：未登录返回 {\"user\": null}（不报错，便于启动引导）。"""
    user = auth.current_user()
    return {'user': _public(user) if user else None}, 200


def post_login(payload):
    """POST /api/auth-login：成功 → 用户 + Set-Cookie；失败 → 401 统一文案。"""
    username = payload.get('username')
    password = payload.get('password')
    if not isinstance(username, str) or not isinstance(password, str):
        raise ValueError('请填写用户名和密码')
    user = auth.authenticate(username, password)
    token, meta = auth.open_session(user['userId'])
    return {'user': _public(user), 'cookie': {'name': COOKIE_NAME, 'value': token,
                                              'maxAge': meta['maxAge']}}, 200


def post_register(payload):
    """POST /api/auth-register：开放注册；成功即登录（201）。重名 → DuplicateUsername(409)。"""
    username = payload.get('username')
    password = payload.get('password')
    if not isinstance(username, str) or not isinstance(password, str):
        raise ValueError('请填写用户名和密码')
    created = auth.create_user(username, password)
    user = {'userId': created['userId'], 'username': created['username'],
            'isAdmin': created['isAdmin'], 'createdAt': created['createdAt']}
    token, meta = auth.open_session(user['userId'])
    return {'user': _public(user), 'cookie': {'name': COOKIE_NAME, 'value': token,
                                              'maxAge': meta['maxAge']}}, 201


def post_logout(payload, token=''):
    """POST /api/auth-logout：吊销当前会话并清 Cookie；无会话幂等成功。

    token 由 HTTP 层从请求 Cookie 解出后传入（本模块不读请求对象）。
    """
    if token:
        auth.revoke_session(token)
    return {'ok': True, 'cookie': {'name': COOKIE_NAME, 'value': '', 'maxAge': 0}}, 200
