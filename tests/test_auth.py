"""登录与账号体系回归（20260918 需求）：口令哈希、会话生命周期、注册/登录/退出契约、数据隔离。

纯 python3 标准库直跑；WIZ_WORKBENCH_ROOT 挂临时数据根，不触碰真实 ontology/ 与真实库。
覆盖验收项：A01/A02/A03/A05/A06（A04 由真实库迁移演练覆盖，见 test_storage_transfer.py）。
运行：python3 tests/test_auth.py
"""
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))
TMP = Path(tempfile.mkdtemp(prefix='wiz_auth_'))
os.environ['WIZ_WORKBENCH_ROOT'] = str(TMP)

import auth_client  # noqa: E402
from workbench import auth, storage, workspaces, projects, flows  # noqa: E402  （临时根就位后 import）

PASSED = []
FAILED = []


def check(cond, message, actual=None):
    if cond:
        PASSED.append(message)
        print(f'通过) {message}')
    else:
        FAILED.append(message)
        print(f'[失败] {message}' + (f'\n      实际: {actual!r}' if actual is not None else ''))


def expect_raises(exc_type, fn, message):
    try:
        fn()
    except exc_type as exc:
        PASSED.append(message)
        print(f'通过) {message}（{type(exc).__name__}: {exc}）')
        return exc
    except Exception as exc:  # noqa: BLE001
        FAILED.append(message)
        print(f'[失败] {message}\n      抛出了非预期异常：{type(exc).__name__}: {exc}')
    else:
        FAILED.append(message)
        print(f'[失败] {message}\n      未抛出异常')


# ── 1) 口令哈希（A06：明文永不落盘）──────────────────────────────────────────────
def test_password_hash():
    stored = auth.hash_password('admin')
    check(stored.startswith('pbkdf2_sha256$'), '口令使用 PBKDF2-SHA256 格式存储', stored[:24])
    check('admin' not in stored, '哈希串不含明文口令')
    check(auth.verify_password('admin', stored), '正确口令校验通过')
    check(not auth.verify_password('admin1', stored), '错误口令校验失败')
    stored2 = auth.hash_password('admin')
    check(stored != stored2, '同一口令两次哈希不同（随机盐）')
    check(auth.verify_password('admin', stored2), '第二次哈希同样可校验')
    check(not auth.verify_password('admin', 'garbage'), '损坏的哈希串安全失败（不抛异常）')
    check(not auth.verify_password('admin', ''), '空哈希安全失败')


# ── 2) 用户名与口令规则 ─────────────────────────────────────────────────────────
def test_validation():
    check(auth.validate_username('  admin  ') == 'admin', '用户名两端空白被去除')
    expect_raises(auth.AuthError, lambda: auth.validate_username(''), '空用户名被拒')
    expect_raises(auth.AuthError, lambda: auth.validate_username('a' * 33), '超长用户名（>32）被拒')
    expect_raises(auth.AuthError, lambda: auth.validate_username('有 空格'), '含空白的用户名被拒')
    expect_raises(auth.AuthError, lambda: auth.validate_password('abc'), '过短口令（<4）被拒')
    expect_raises(auth.AuthError, lambda: auth.validate_password('x' * 129), '过长口令（>128）被拒')
    check(auth.validate_password('abcd') == 'abcd', '最短合法口令通过')


# ── 3) 账号创建与会话（A02/A05）──────────────────────────────────────────────────
def test_account_and_session():
    storage.ensure_ready()
    created = auth.create_user('Admin', 'admin')
    check(created['username'] == 'Admin', '账号创建成功（保留原始大小写）', created['username'])
    with sqlite3.connect(str(TMP / 'data' / 'workbench.sqlite3')) as con:
        row = con.execute('SELECT password_hash FROM wb_users WHERE username_key = :k',
                          {'k': 'admin'}).fetchone()
    check(row is not None and 'admin' not in row[0], '库里只存哈希，不含明文')
    expect_raises(auth.DuplicateUsername, lambda: auth.create_user('admin', 'other'),
                  '大小写不敏感的用户名重复被拒（Admin vs admin）')

    user = auth.authenticate('ADMIN', 'admin')   # 大小写不敏感登录
    check(user['username'] == 'Admin', '登录用户名大小写不敏感')
    expect_raises(auth.InvalidCredentials, lambda: auth.authenticate('Admin', 'wrong'), '错误口令登录失败')
    expect_raises(auth.InvalidCredentials, lambda: auth.authenticate('nobody', 'x'), '不存在的用户名登录失败')

    token, meta = auth.open_session(user['userId'])
    check(len(token) > 20 and meta['maxAge'] == auth.SESSION_TTL_SECONDS, '会话令牌签发（30 天）')
    with sqlite3.connect(str(TMP / 'data' / 'workbench.sqlite3')) as con:
        row = con.execute('SELECT token_hash FROM wb_sessions').fetchone()
    check(row is not None and token not in row[0], '库里只存令牌摘要，不存令牌本身')
    resolved = auth.resolve_session(token)
    check(resolved and resolved['username'] == 'Admin', '会话可解析回用户')
    auth.revoke_session(token)
    check(auth.resolve_session(token) is None, '注销后会话失效')
    check(auth.resolve_session('bogus') is None, '无效令牌安全失败')


def test_require_user_id():
    auth.bind_request(None)
    expect_raises(auth.AuthRequired, auth.require_user_id, '未登录时 require_user_id 抛 AuthRequired')
    auth.bind_request({'userId': 'u-1', 'username': 'x', 'isAdmin': False, 'createdAt': ''})
    check(auth.require_user_id() == 'u-1', '绑定后返回当前用户 id')


# ── 4) 数据隔离（A03：账号之间完全隔离）──────────────────────────────────────────
def test_data_isolation():
    auth.create_user('alice', 'alice1234')
    auth.create_user('bob', 'bob1234')
    from workbench.storage.engine import read_connection
    from sqlalchemy import text as sql

    def uid_of(name):
        with read_connection() as conn:
            return conn.execute(sql('SELECT user_id FROM wb_users WHERE username_key = :k'),
                                {'k': name}).fetchone()[0]

    alice, bob = uid_of('alice'), uid_of('bob')

    def bind(uid):
        auth.bind_request({'userId': uid, 'username': uid, 'isAdmin': False, 'createdAt': ''})

    bind(alice)
    a_ws = workspaces.create('爱丽丝本体', workspaces_blank())
    a_proj = projects.create('爱丽丝项目')
    a_flow = flows.create('爱丽丝编排')
    check(workspaces.listing() and projects.listing() and flows.listing(), 'alice 能看到自己的三类数据')

    bind(bob)
    check(workspaces.listing() == [], 'bob 看不到 alice 的本体', workspaces.listing())
    check(projects.listing() == [], 'bob 看不到 alice 的项目', projects.listing())
    check(flows.listing() == [], 'bob 看不到 alice 的编排', flows.listing())
    check(workspaces.current_token(a_ws['id']) is None, 'bob 用 alice 的本体 id 取 token → None（按不存在）')
    check(projects.current_token(a_proj['id']) is None, 'bob 用 alice 的项目 id 取 token → None')
    check(flows.current_token(a_flow['id']) is None, 'bob 用 alice 的编排 id 取 token → None')
    expect_raises(ValueError, lambda: projects.load(a_proj['id']), 'bob 读取 alice 的项目 → 项目不存在')

    # 同名不冲突（各账号独立命名空间）
    b_ws = workspaces.create('爱丽丝本体', workspaces_blank())
    check(b_ws['id'] != a_ws['id'], '不同账号可用同名本体（唯一性按账号）')
    b_proj = projects.create('爱丽丝项目')
    check(b_proj['id'] != a_proj['id'], '不同账号可用同名项目')

    bind(alice)
    check([w['id'] for w in workspaces.listing()] == [a_ws['id']], 'alice 仍只看到自己的本体')
    check([p['id'] for p in projects.listing()] == [a_proj['id']], 'alice 仍只看到自己的项目')
    # 跨账号写入：bob 用 alice 的 id 写入时，语义是「该 id 在 bob 名下不存在」——
    # 可以创建 bob 自己的资产（同 id 不同归属），但绝不能改动 alice 的内容。
    bind(alice)
    alice_before = workspaces.read_draft(a_ws['id'])['ontology']
    bind(bob)
    check(workspaces.read_draft(a_ws['id']) is None, 'bob 视角下 alice 的本体 id 不存在（读不到内容）')
    bob_state = workspaces_blank()
    bob_state['workspaceId'] = a_ws['id']
    workspaces.write_draft(bob_state, expected_token=None)
    bind(alice)
    alice_after = workspaces.read_draft(a_ws['id'])['ontology']
    check(alice_after == alice_before, 'bob 的同 id 写入不影响 alice 的内容（各账号独立命名空间）')


def workspaces_blank():
    from workbench.model_format import decode_state
    return decode_state({'ontology': {'schemaVersion': 1, 'namespaces': {
        'mg': 'https://example.com/microgrid/', 'owl': 'http://www.w3.org/2002/07/owl#',
        'rdfs': 'http://www.w3.org/2000/01/rdf-schema#', 'xsd': 'http://www.w3.org/2001/XMLSchema#'},
        'objectTypes': [{'id': 'mg:c1', 'displayName': '对象一', 'description': 'd'}],
        'linkTypes': [], 'properties': [], 'sharedProperties': [], 'valueTypes': [], 'metadata': [],
        'definitionOrder': ['mg:c1']}, 'workflow': {'objective': {}, 'functions': [], 'actions': []},
        'metrics': {'metrics': []}, 'rules': {'rules': []}, 'layout': {}})


# ── 5) 模型配置隔离 ─────────────────────────────────────────────────────────────
def test_llm_isolation():
    from workbench import llm_providers
    from workbench.storage.engine import read_connection
    from sqlalchemy import text as sql

    def uid_of(name):
        with read_connection() as conn:
            return conn.execute(sql('SELECT user_id FROM wb_users WHERE username_key = :k'),
                                {'k': name}).fetchone()[0]

    def bind(uid):
        auth.bind_request({'userId': uid, 'username': uid, 'isAdmin': False, 'createdAt': ''})

    bind(uid_of('alice'))
    saved = llm_providers.save('爱丽丝模型', 'https://api.example.com/v1/chat/completions',
                               'm-a', 'sk-alice', timeout=30)
    check([m['id'] for m in llm_providers.list_metadata()] == [saved['id']],
          'alice 只看到自己的模型配置')
    check(llm_providers.read(saved['id'])['api_key'] == 'sk-alice', 'alice 能读回自己的密钥')

    bind(uid_of('bob'))
    check(llm_providers.list_metadata() == [], 'bob 看不到 alice 的模型配置', llm_providers.list_metadata())
    check(llm_providers.read(saved['id']) is None, 'bob 用 alice 的 providerId 读取 → None')
    expect_raises(ValueError, lambda: llm_providers.resolve(saved['id']),
                  'bob 解析 alice 的提供方 → 报不存在')
    bob_saved = llm_providers.save('鲍勃模型', 'https://api.example.com/v1/chat/completions',
                                   'm-b', 'sk-bob', timeout=30)
    check(bob_saved['id'] != saved['id'], 'bob 的配置是独立行')

    bind(uid_of('alice'))
    check(llm_providers.default_provider()['id'] == saved['id'], 'alice 的默认仍指向自己的配置')
    check(llm_providers.default_provider()['api_key'] == 'sk-alice', 'alice 的密钥未被 bob 覆盖')


# ── 6) HTTP 契约（A01/A05/A06 + 注册重名 409）────────────────────────────────────
def test_http_contract():
    import subprocess
    import socket

    probe = socket.socket()
    try:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        port = 18831
        probe.bind(('127.0.0.1', port))
    finally:
        probe.close()
    base = f'http://127.0.0.1:{port}'
    proc = subprocess.Popen([sys.executable, '-m', 'workbench.server'], cwd=str(REPO),
                            env=dict(os.environ, WIZ_WORKBENCH_ROOT=str(TMP),
                                     WIZ_WORKBENCH_PORT=str(port)),
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        auth_client.wait_ready(base, timeout=25)
        # A01：未登录访问业务接口 → 401
        code, body, _ = auth_client._raw(base, '/api/ontologies', method='GET')
        check(code == 401 and body.get('code') == 'UNAUTHENTICATED',
              '未登录访问 /api/ontologies → 401 UNAUTHENTICATED', (code, body))
        code, body, _ = auth_client._raw(base, '/api/auth-state', method='GET')
        check(code == 200 and body.get('user') is None, 'auth-state 未登录返回 user=null', (code, body))

        # 注册：201 + Cookie；重名 409；非法用户名 400
        code, body, headers = auth_client._raw(base, '/api/auth-register',
                                               {'username': 'carol', 'password': 'carol1234'})
        check(code == 201 and body['user']['username'] == 'carol', '注册新账号 → 201', (code, body))
        token = auth_client._cookie_from(headers, body)
        check(bool(token), '注册响应带会话 Cookie')
        code, body, _ = auth_client._raw(base, '/api/auth-register',
                                         {'username': 'Carol', 'password': 'x12345'})
        check(code == 409 and body.get('code') == 'DUPLICATE_NAME', '重名注册 → 409 DUPLICATE_NAME', (code, body))
        code, body, _ = auth_client._raw(base, '/api/auth-register', {'username': 'x', 'password': '12'})
        check(code == 400, '过短口令注册 → 400', (code, body))

        # 登录：错误口令 401 同文案；正确口令 200
        code, body, _ = auth_client._raw(base, '/api/auth-login',
                                         {'username': 'carol', 'password': 'wrong'})
        check(code == 401 and body.get('code') == 'UNAUTHENTICATED', '错误口令登录 → 401', (code, body))
        code, body, headers = auth_client._raw(base, '/api/auth-login',
                                               {'username': 'carol', 'password': 'carol1234'})
        check(code == 200, '正确口令登录 → 200', (code, body))
        cookie = 'wiz_session=' + auth_client._cookie_from(headers, body)
        # 刷新场景回归：带着有效 Cookie 访问 auth-state 必须报告已登录用户
        # （曾经：auth-state 在免登录名单里被跳过解会话 → 永远 user=null → 刷新即被弹回登录页）
        code, body, _ = auth_client._raw(base, '/api/auth-state', method='GET',
                                         headers={'Cookie': cookie})
        check(code == 200 and (body.get('user') or {}).get('username') == 'carol',
              '带有效会话访问 auth-state 报告已登录用户（刷新不丢登录态）', (code, body))

        # 登录后：业务接口可用；跨账号 id 按不存在
        code, body, _ = auth_client._raw(base, '/api/ontologies', method='GET',
                                         headers={'Cookie': cookie})
        check(code == 200 and all(o['name'] != '爱丽丝本体' for o in body['items']),
              '登录后本体列表只含本账号数据', (code, body))
        code, body, _ = auth_client._raw(base, '/api/state?ontology=storage', method='GET',
                                         headers={'Cookie': cookie})
        check(code == 200, '登录后读取默认本体草稿 → 200', (code, body))

        # 退出：清 Cookie，之后 401
        code, body, headers = auth_client._raw(base, '/api/auth-logout', {}, headers={'Cookie': cookie})
        check(code == 200 and body.get('ok') is True, '退出登录 → 200', (code, body))
        code, body, _ = auth_client._raw(base, '/api/ontologies', method='GET', headers={'Cookie': cookie})
        check(code == 401, '退出后原会话失效 → 401', (code, body))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def main():
    test_password_hash()
    test_validation()
    test_account_and_session()
    test_require_user_id()
    test_data_isolation()
    test_llm_isolation()
    test_http_contract()
    print(f'\n统计：{len(PASSED)} 项通过' + (f'，{len(FAILED)} 项失败' if FAILED else '，全部通过'))
    if FAILED:
        for item in FAILED:
            print('  失败：' + item)
        sys.exit(1)


if __name__ == '__main__':
    main()
