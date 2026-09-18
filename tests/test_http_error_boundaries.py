"""批次 B 回归：HTTP 错误分类（R3）、GET 工作区依赖解除（R4）、项目列表筛选语义（R5）。

对应《代码审查修改意见_20260918.md》三、接口边界修正，全部经真实隔离 HTTP 服务断言：
- R3：400/404/409/503/500 分类 + 稳定 code 字段 + X-Request-Id 响应头 +
      未知异常不回传 str(exc)（信息不泄露）；
- R4：未知端点稳定 404；flows / llm-providers / storage-status 等独立接口
      不依赖当前本体有效（无效 ontology 参数不影响）；本体专属接口仍校验；
- R5：/api/projects 四类请求的显式筛选契约。

隔离规则同 test_export_restore_http.py：临时根 + 独立端口子进程服务，
直接写库时显式设置 WIZ_DATABASE_URL，不碰真实业务数据。

运行：python3 tests/test_http_error_boundaries.py
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parent))
import auth_client
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PORT = 18812
ORIGIN = f'http://127.0.0.1:{PORT}'
BASE = f'http://127.0.0.1:{PORT}'

TMP = Path(tempfile.mkdtemp(prefix='wiz_error_boundary_'))
os.environ['WIZ_WORKBENCH_ROOT'] = str(TMP)

PROC = None
PASSED = []


def check(cond, message, actual=None, expected=None):
    if cond:
        return
    print(f'\n[失败] {message}')
    if expected is not None:
        print('  预期: ' + json.dumps(expected, ensure_ascii=False, default=str)[:2000])
    if actual is not None:
        print('  实际: ' + json.dumps(actual, ensure_ascii=False, default=str)[:2000])
    shutdown()
    print(f'\n（临时根保留供排查：{TMP}）')
    sys.exit(1)


def ok(step, desc):
    PASSED.append(step)
    print(f'通过 {step}) {desc}')


def shutdown():
    global PROC
    if PROC is not None:
        PROC.terminate()
        try:
            PROC.wait(timeout=10)
        except subprocess.TimeoutExpired:
            PROC.kill()
        PROC = None


AUTH_COOKIE = {'value': ''}  # 登录后写入（20260918 账号体系）


def request(method, path, payload=None, origin=ORIGIN, raw_body=None, want_headers=False):
    """raw_body：发送非 JSON 原始字节（测 400 解析分支），优先于 payload。"""
    data = raw_body if raw_body is not None else (
        json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None)
    headers = {'Content-Type': 'application/json'}

    if AUTH_COOKIE['value']:

        headers['Cookie'] = 'wiz_session=' + AUTH_COOKIE['value']
    if method == 'POST':
        headers['Origin'] = origin
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read()
            result = (resp.status, json.loads(body.decode()), dict(resp.headers))
            return result if want_headers else result[:2]
    except urllib.error.HTTPError as exc:
        body = exc.read()
        try:
            parsed = json.loads(body.decode())
        except ValueError:
            parsed = body.decode(errors='replace')
        result = (exc.code, parsed, dict(exc.headers))
        return result if want_headers else result[:2]


def start_server():
    global PROC
    probe = socket.socket()
    try:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(('127.0.0.1', PORT))
    except OSError as exc:
        print(f'[失败] 端口 {PORT} 已被占用：{exc}')
        sys.exit(1)
    finally:
        probe.close()

    env = dict(os.environ, WIZ_WORKBENCH_ROOT=str(TMP), WIZ_WORKBENCH_PORT=str(PORT))
    PROC = subprocess.Popen([sys.executable, '-m', 'workbench.server'], cwd=str(REPO), env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    deadline = time.time() + 20
    ready = False
    while time.time() < deadline:
        if PROC.poll() is not None:
            print('[失败] 服务进程提前退出，输出如下：')
            print(PROC.stdout.read().decode(errors='replace'))
            sys.exit(1)
        try:
            status, _ = request('GET', '/api/auth-state')
            if status == 200:
                ready = True
                break
            time.sleep(0.2)
        except (urllib.error.URLError, ConnectionError, OSError):
            time.sleep(0.2)
    check(ready, '服务未在 20 秒内就绪', actual='未就绪', expected=200)
    if ready:
        AUTH_COOKIE['value'] = auth_client.auth_headers(BASE)['Cookie'].split('=', 1)[1]
    ok('0', f'隔离服务已就绪 WIZ_WORKBENCH_ROOT={TMP} 端口 {PORT}')


def unit_internal_error_injection():
    """500 分类注入验证（进程内）：把一个路由替换为必抛 RuntimeError 的函数，
    断言响应为 INTERNAL_ERROR + requestId 且不泄露异常细节（路径/SQL/堆栈）。"""
    import threading
    sys.path.insert(0, str(REPO))
    from workbench import server as wb_server
    from http.server import ThreadingHTTPServer

    def boom(query):
        raise RuntimeError('秘密细节 /etc/passwd 与 SELECT * FROM users WHERE pwd=1')

    saved = wb_server.GET_ROUTES['/api/flows']
    wb_server.GET_ROUTES['/api/flows'] = boom
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), wb_server.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        # 账号体系（20260918）：该注入实例独立端口，需要自己的会话 Cookie
        _, token = auth_client.register_or_login(f'http://127.0.0.1:{port}')
        req = urllib.request.Request(f'http://127.0.0.1:{port}/api/flows',
                                     headers={'Cookie': 'wiz_session=' + token})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                status, body, headers = resp.status, json.loads(resp.read().decode()), dict(resp.headers)
        except urllib.error.HTTPError as exc:
            body = json.loads(exc.read().decode())
            status, headers = exc.code, dict(exc.headers)
        check(status == 500 and body.get('code') == 'INTERNAL_ERROR',
              '注入内部异常应 500 INTERNAL_ERROR', actual=(status, body),
              expected=(500, 'INTERNAL_ERROR'))
        leaked = str(body)
        check(('秘密' not in leaked) and ('SELECT' not in leaked)
              and ('/etc/' not in leaked) and ('Traceback' not in leaked),
              '500 响应不得回传异常细节（路径/SQL/堆栈）', actual=body, expected='通用消息 + requestId')
        check('requestId' in str(body.get('error')), '500 消息应含 requestId 供排障关联',
              actual=body.get('error'), expected='含 requestId')
        check(headers.get('X-Request-Id'), '500 响应头应含 X-Request-Id',
              actual=headers.get('X-Request-Id'), expected='非空')
        ok('9', '注入 RuntimeError → 500 INTERNAL_ERROR：通用消息 + requestId，无细节泄露')
    finally:
        wb_server.GET_ROUTES['/api/flows'] = saved
        httpd.shutdown()


def main():
    start_server()

    # --- R4：路由存在性与独立接口 ----------------------------------------------
    status, body = request('GET', '/api/not-exist-endpoint')
    check(status == 404 and body.get('code') == 'NOT_FOUND',
          'GET 未知端点应 404 + code NOT_FOUND', actual=(status, body), expected=(404, 'NOT_FOUND'))

    # 独立接口：无 ontology、无效 ontology 参数都不影响（原来会被统一前置 describe 拦成 404）
    for path in ('/api/flows', '/api/llm-providers', '/api/storage-status'):
        for suffix in ('', '?ontology=not-exist-id'):
            status, body = request('GET', path + suffix)
            check(status == 200 and isinstance(body, dict),
                  f'独立接口 {path}{suffix} 应 200（不依赖本体有效性）',
                  actual=(status, body), expected=200)
    ok('1', '未知端点稳定 404；flows/llm-providers/storage-status 不受 ontology 参数影响')

    # 本体专属接口仍校验：格式非法 → 400；格式合法但不存在 → 404
    status, body = request('GET', '/api/state?ontology=not-exist-id')
    check(status == 400 and body.get('code') == 'INVALID_ARGUMENT',
          '本体接口传格式非法的标识应 400', actual=(status, body), expected=(400, 'INVALID_ARGUMENT'))
    status, body = request('GET', '/api/state?ontology=11111111-2222-4333-8444-555555555555')
    check(status == 404 and body.get('code') == 'NOT_FOUND',
          '本体接口传不存在的本体应 404', actual=(status, body), expected=(404, 'NOT_FOUND'))
    status, body = request('GET', '/api/state?ontology=storage')
    check(status == 200 and 'state' in body, '本体接口传有效本体应 200',
          actual=(status, list(body)), expected=200)
    ok('2', '本体专属接口仍按自身契约校验 ontology')

    # --- R5：项目列表筛选语义 ----------------------------------------------------
    status, body = request('GET', '/api/projects')
    check(status == 200 and 'items' in body, 'GET /api/projects（无参数）应 200',
          actual=(status, body), expected=200)
    total = len(body['items'])

    # 建一个无本体绑定的项目（空白临时根无已发布版本，无法绑定本体）：
    # ontologyId='' 的项目恰好可验证筛选语义——它必须出现在「全部」里、
    # 且被 ?ontology=storage 排除。
    status, created = request('POST', '/api/projects', {'name': '边界测试项目'})
    check(status == 201, '创建项目应 201', actual=(status, created), expected=201)

    status, body = request('GET', '/api/projects')
    check(status == 200 and len(body['items']) == total + 1
          and any(p['id'] == created['id'] for p in body['items']),
          '无参数应返回全部（新建项目后 +1）', actual=len(body['items']), expected=total + 1)

    status, body = request('GET', '/api/projects?includeDeleted=1')
    check(status == 200 and len(body['items']) == total + 1,
          '无关参数不得隐式改变范围（原实现会按 ontology=storage 过滤掉无本体项目）',
          actual=len(body['items']), expected=total + 1)

    status, body = request('GET', '/api/projects?ontology=storage')
    check(status == 200 and all(p['id'] != created['id'] for p in body['items'])
          and all(p.get('ontologyId') == 'storage' for p in body['items']),
          '?ontology=storage 应只含 storage 的项目（无本体绑定的新项目被排除）',
          actual=[(p['id'], p.get('ontologyId')) for p in body['items']], expected='仅 ontologyId=storage')

    status, body = request('GET', '/api/projects?ontology=')
    check(status == 400 and body.get('code') == 'INVALID_ARGUMENT',
          '空 ontology 应 400 INVALID_ARGUMENT', actual=(status, body), expected=(400, 'INVALID_ARGUMENT'))

    status, body = request('GET', '/api/projects?ontology=11111111-2222-4333-8444-555555555555')
    check(status == 404 and body.get('code') == 'NOT_FOUND',
          '不存在的本体应 404', actual=(status, body), expected=(404, 'NOT_FOUND'))
    ok('3', '项目列表筛选契约：全部/指定本体/无关参数/空值/无效本体 五类行为符合文档')

    # --- R3：错误分类与信封 ------------------------------------------------------
    status, _body, headers = request('GET', '/api/state?ontology=storage', want_headers=True)
    check(headers.get('X-Request-Id'), '响应应携带 X-Request-Id 头',
          actual=headers.get('X-Request-Id'), expected='非空')
    request_id_get = headers['X-Request-Id']
    status, payload, headers = request('POST', '/api/validate', {'x': 1}, want_headers=True)
    check(headers.get('X-Request-Id') and headers['X-Request-Id'] != request_id_get,
          '每个请求的 X-Request-Id 应独立生成', actual=headers.get('X-Request-Id'), expected='新值')
    ok('4', 'X-Request-Id：GET/POST 均返回且每请求独立')

    # 400 INVALID_ARGUMENT：非法 JSON
    status, body = request('POST', '/api/validate', raw_body=b'{not-json')
    check(status == 400 and body.get('code') == 'INVALID_ARGUMENT',
          '非法 JSON 应 400 INVALID_ARGUMENT', actual=(status, body), expected=(400, 'INVALID_ARGUMENT'))

    # 400 INVALID_ARGUMENT：缺必填字段（KeyError 路径，消息泛化不回传异常文本）
    status, body = request('POST', '/api/validate', {'unexpected': True})
    check(status == 400 and body.get('code') == 'INVALID_ARGUMENT',
          '缺 state 字段应 400', actual=(status, body), expected=(400, 'INVALID_ARGUMENT'))
    check("'state'" not in str(body.get('error')),
          'KeyError 响应不得回传异常原文（如 KeyError: state）',
          actual=body.get('error'), expected='泛化中文消息')

    # 400 INVALID_ARGUMENT：业务层显式校验（ValueError 路径，保留可读文本）
    status, body = request('POST', '/api/restore',
                           {'state': {'workspaceId': 'storage'}, 'revision': 'x', 'release': '../evil.zip'})
    check(status == 400 and body.get('code') == 'INVALID_ARGUMENT',
          '非法快照名应 400 INVALID_ARGUMENT', actual=(status, body), expected=(400, 'INVALID_ARGUMENT'))
    ok('5', '400 三条路径（非法 JSON / 缺字段 / 业务校验）均带稳定 code，KeyError 不泄露原文')

    # 404：POST 到存在校验的端点（完整 state 但本体不存在）
    status, payload = request('GET', '/api/state?ontology=storage')
    ghost_state = dict(payload['state'])
    ghost_state['workspaceId'] = '11111111-2222-4333-8444-555555555555'
    status, body = request('POST', '/api/save', {'state': ghost_state, 'revision': 'x'})
    check(status == 404 and body.get('code') == 'NOT_FOUND',
          'POST 目标本体不存在应 404', actual=(status, body), expected=(404, 'NOT_FOUND'))

    # 409 REVISION_CONFLICT：旧 revision 保存
    status, payload = request('GET', '/api/state?ontology=storage')
    state, revision = payload['state'], payload['revision']
    status, saved = request('POST', '/api/save', {'state': state, 'revision': revision})
    check(status == 200 and saved.get('revision'), '正常保存应 200', actual=(status, saved), expected=200)
    status, body = request('POST', '/api/save', {'state': state, 'revision': revision})
    check(status == 409 and body.get('code') == 'REVISION_CONFLICT' and body.get('currentRevision'),
          '旧 revision 应 409 + code + currentRevision', actual=(status, body),
          expected=(409, 'REVISION_CONFLICT'))
    ok('6', '404（POST 资源不存在）与 409（CAS 冲突带 currentRevision）分类正确')

    # 415 / 403：媒体类型与来源（headers 由 request() 固定注入，这里单独构造）
    req = urllib.request.Request(BASE + '/api/validate', data=b'{}',
                                 headers={'Content-Type': 'text/plain', 'Origin': ORIGIN}, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            status, body = resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        status, body = exc.code, json.loads(exc.read().decode())
    check(status == 415 and body.get('code') == 'UNSUPPORTED_MEDIA_TYPE',
          '非 JSON Content-Type 应 415', actual=(status, body), expected=(415, 'UNSUPPORTED_MEDIA_TYPE'))
    status, body = request('POST', '/api/validate', {'state': state}, origin='http://evil.example')
    check(status == 403 and body.get('code') == 'ORIGIN_REJECTED',
          '非法 Origin 应 403 ORIGIN_REJECTED', actual=(status, body), expected=(403, 'ORIGIN_REJECTED'))
    ok('7', '415 与 403 均带稳定 code')

    # 500 INTERNAL_ERROR：畸形 state 触发未分类异常（不得回传 str(exc) / 路径 / SQL）
    status, body = request('POST', '/api/explorer',
                           {'state': {'workspaceId': 'storage', 'ontology': None}})
    if status == 500:
        check(body.get('code') == 'INTERNAL_ERROR' and 'requestId' in str(body.get('error')),
              '500 应为 INTERNAL_ERROR + requestId', actual=(status, body),
              expected=(500, 'INTERNAL_ERROR'))
        leaked = str(body)
        check(('.py' not in leaked) and ('Traceback' not in leaked) and ('sqlalchemy' not in leaked.lower()),
              '500 响应不得泄露文件路径 / 堆栈 / SQL 细节', actual=body, expected='通用消息')
        ok('8', '500 内部错误：通用消息 + requestId，无内部细节泄露')
    else:
        # 畸形输入走了 400（decode 层显式校验）也符合契约：分类为客户端错误即可
        check(status == 400 and body.get('code') == 'INVALID_ARGUMENT',
              '畸形 state 应落入 400 或 500 之一（不得是其他状态码）',
              actual=(status, body), expected='400 INVALID_ARGUMENT 或 500 INTERNAL_ERROR')
        ok('8', '畸形 state 被 decode 层显式拦截为 400（500 路径由步骤 9 注入验证）')

    shutdown()
    unit_internal_error_injection()
    print(f'\n全部通过（{len(PASSED)} 步）：R3/R4/R5 契约全部符合。')
    print(f'临时根：{TMP}')


if __name__ == '__main__':
    try:
        main()
    finally:
        shutdown()
