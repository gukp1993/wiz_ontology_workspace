"""Local ontology workbench HTTP entry. Bind to loopback only.

职责（B3 后）：本文件只保留 HTTP 服务与安全边界、路由分派和错误序列化；
本体区业务在 workbench/model_routes.py，项目区业务在 workbench/project_routes.py。
安全边界不变：POST 白名单、Origin 校验、2MB 请求上限、JSON Content-Type；
写操作的 LOCK 在业务模块内使用（workbench.locking 的同一把全局锁）。

认证与账号（2026-09-18）：除 AUTH_FREE_GET/POST 四个接口外，全部 /api/* 要求登录
（未登录 401 UNAUTHENTICATED）；身份来自 Cookie wiz_session（HttpOnly/SameSite=Strict），
经 auth.resolve_session 解出后 auth.bind_request(user) 放进请求上下文，业务与存储层
按 current_user_id 做数据归属过滤。契约见 文档/接口文档/06-认证与账户接口.md。

V3: the ontology area and the project binding area are separate states with
separate drafts and releases. Ontology publishes create immutable, classified
version directories; projects pin one published version and upgrade proactively
after an impact preflight.
Data connections: /api/connection-test and /api/connection-catalog probe real
MySQL/Redis services without holding the global write lock; /api/connection-secret
stores passwords in the protected vault only — never in responses, logs or
project state/snapshots. /api/api-credential 登记项目级 API 凭据（动作接口映射），
独立命名空间 workbench/api_credentials，响应只含 id/name，密钥永不回传。
"""
import json
import os
import threading
import traceback
import uuid
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlsplit, parse_qs

from workbench import auth, auth_routes
from workbench import model_routes, project_routes, projects, versions, workspaces
from workbench import config_package_routes, flow_routes, storage
from workbench import ontology_build_routes as build_routes
from workbench.config_packages import ImportConflict, TokenError
from workbench.config_package_format import PackageFormatError
from workbench.storage.configuration import CatalogCacheUnreadable as _catalog_unreadable
from workbench.paths import CODE_ROOT

STATIC = CODE_ROOT / 'frontend/dist'

# 免登录接口（接口文档 06 §2）：认证自身的四个端点；其余 /api/* 一律要求会话。
AUTH_FREE_GET = {'/api/auth-state'}
AUTH_FREE_POST = {'/api/auth-login', '/api/auth-register', '/api/auth-logout'}

# GET 路由表：query 为 parse_qs(keep_blank_values=True) 的结果。
GET_ROUTES = {
    '/api/auth-state': auth_routes.get_auth_state,
    '/api/state': model_routes.get_state,
    '/api/versions': model_routes.get_versions,
    '/api/version-state': model_routes.get_version_state,
    '/api/releases': model_routes.get_releases,
    '/api/source-reference': model_routes.get_source_reference,
    '/api/projects': project_routes.get_projects,
    '/api/project-state': project_routes.get_project_state,
    '/api/project-releases': project_routes.get_project_releases,
    '/api/project-config': project_routes.get_project_config,
    '/api/api-credentials': project_routes.get_api_credentials,
    '/api/flows': flow_routes.get_flows,
    '/api/flow-state': flow_routes.get_flow_state,
    '/api/llm-providers': flow_routes.get_llm_providers,
    '/api/storage-status': model_routes.get_storage_status,
    '/api/model-definitions': model_routes.get_model_definitions,
    # 从物料自动构建本体（08 分册）
    '/api/build-capabilities': build_routes.get_capabilities,
    '/api/build-tasks': build_routes.get_tasks,
    '/api/build-task': build_routes.get_task,
    '/api/build-materials': build_routes.get_materials,
    '/api/build-run': build_routes.get_run,
    '/api/build-messages': build_routes.get_messages,
    '/api/build-candidates': build_routes.get_candidates,
    '/api/build-candidate': build_routes.get_candidate,
    '/api/build-diff': build_routes.get_diff,
    '/api/build-delivery': build_routes.get_delivery,
}

# 已注册但响应体不是 JSON 的端点（ZIP 等二进制）。
# 用独立哨兵而非 None：路由必须能与「键不存在」区分，否则 do_POST 的前置检查会
# 把已注册端点误判为 404（导出功能曾在 /api/export 上因此完全不可达）。
BINARY_ROUTE = object()
_MISSING = object()  # 路由表查不到时的唯一标记（键存在但值为 None 不算缺失）

# POST 路由表：payload 为解析后的 JSON。白名单即本表键集合。
POST_ROUTES = {
    '/api/auth-login': auth_routes.post_login,
    '/api/auth-register': auth_routes.post_register,
    '/api/auth-logout': lambda payload: auth_routes.post_logout(payload, _current_token()),
    '/api/ontologies': model_routes.post_ontologies,
    '/api/projects': project_routes.post_create_project,
    '/api/format-preview': model_routes.post_format_preview,
    '/api/explorer': model_routes.post_explorer,
    '/api/workflow-check': model_routes.post_workflow_check,
    '/api/demo-objects': model_routes.post_demo_objects,
    '/api/function-preview': model_routes.post_function_preview,
    '/api/action-preview': model_routes.post_action_preview,
    '/api/value-type-check': model_routes.post_value_type_check,
    '/api/validate': model_routes.post_validate,
    '/api/preview': model_routes.post_preview,
    '/api/restore': model_routes.post_restore,
    '/api/publish-check': model_routes.post_publish_check,
    '/api/save': model_routes.post_save,
    '/api/publish': model_routes.post_publish,
    '/api/project-save': lambda payload: project_routes.post_project_write(payload, '/api/project-save'),
    '/api/project-validate': lambda payload: project_routes.post_project_write(payload, '/api/project-validate'),
    '/api/project-publish': lambda payload: project_routes.post_project_write(payload, '/api/project-publish'),
    '/api/project-upgrade-check': lambda payload: project_routes.post_project_write(payload, '/api/project-upgrade-check'),
    '/api/connection-test': lambda payload: project_routes.post_connection_probe(payload, '/api/connection-test'),
    '/api/connection-catalog': lambda payload: project_routes.post_connection_probe(payload, '/api/connection-catalog'),
    '/api/catalog-refresh': project_routes.post_catalog_refresh,
    '/api/calc-eval': project_routes.post_calc_eval,
    '/api/connection-secret': project_routes.post_connection_secret,
    '/api/api-credential': project_routes.post_api_credential,
    '/api/project-property-preview': project_routes.post_property_preview,
    '/api/flows': flow_routes.post_create_flow,
    '/api/flow-save': flow_routes.post_flow_save,
    '/api/flow-check': flow_routes.post_flow_check,
    '/api/flow-copy': flow_routes.post_flow_copy,
    '/api/flow-delete': flow_routes.post_flow_delete,
    '/api/flow-run': flow_routes.post_flow_run,
    '/api/llm-provider-save': flow_routes.post_llm_provider_save,
    '/api/llm-provider-delete': flow_routes.post_llm_provider_delete,
    '/api/llm-provider-default': flow_routes.post_llm_provider_default,
    '/api/llm-provider-test': flow_routes.post_llm_provider_test,
    # 从物料自动构建本体（08 分册）：长任务一律返回 runId，后台执行不持锁
    '/api/build-task-create': build_routes.post_task_create,
    '/api/build-task-rename': build_routes.post_task_rename,
    '/api/build-task-filter': build_routes.post_task_filter,
    '/api/build-task-delete': build_routes.post_task_delete,
    '/api/build-upload-init': build_routes.post_upload_init,
    '/api/build-upload-chunk': build_routes.post_upload_chunk,
    '/api/build-upload-complete': build_routes.post_upload_complete,
    '/api/build-upload-abort': build_routes.post_upload_abort,
    '/api/build-material-exclude': build_routes.post_material_exclude,
    '/api/build-material-retry': build_routes.post_material_retry,
    '/api/build-scan': build_routes.post_scan,
    '/api/build-run-cancel': build_routes.post_run_cancel,
    '/api/build-run-resume': build_routes.post_run_resume,
    '/api/build-message': build_routes.post_message,
    '/api/build-scope-save': build_routes.post_scope_save,
    '/api/build-scope-confirm': build_routes.post_scope_confirm,
    '/api/build-candidate-update': build_routes.post_candidate_update,
    '/api/build-candidate-decide': build_routes.post_candidate_decide,
    '/api/build-candidates-merge': build_routes.post_candidates_merge,
    '/api/build-review-undo': build_routes.post_review_undo,
    '/api/build-regenerate': build_routes.post_regenerate,
    '/api/build-diff-resolve': build_routes.post_diff_resolve,
    '/api/build-deliver-precheck': build_routes.post_deliver_precheck,
    '/api/build-deliver': build_routes.post_deliver,
    '/api/config-package-export-preview': config_package_routes.post_export_preview,
    '/api/config-package-stage': config_package_routes.post_stage,
    '/api/config-package-import-preview': config_package_routes.post_import_preview,
    '/api/config-package-import': config_package_routes.post_import,
    '/api/config-package-import-result': config_package_routes.post_import_result,
    '/api/config-package-discard': config_package_routes.post_discard,
    '/api/config-package-export': BINARY_ROUTE,  # 配置包下载：do_POST 专用二进制分支（07 §2.2）
    '/api/export': BINARY_ROUTE,  # 二进制响应：do_POST 内专用分支处理（哨兵 = 路由已注册）
}


_REQUEST_CTX = threading.local()  # 每请求线程独立（ThreadingHTTPServer 一线程一请求）


def _current_token():
    """从当前请求的 Cookie 取会话令牌（供 auth_routes.post_logout 等模块级调用）。"""
    handler = getattr(_REQUEST_CTX, 'handler', None)
    return _cookie_token(handler) if handler is not None else ''


def _cookie_token(handler):
    """解析 Cookie 头里的 wiz_session；缺失返回空串。"""
    if handler is None:
        return ''
    header = handler.headers.get('Cookie', '') or ''
    for part in header.split(';'):
        name, _, value = part.strip().partition('=')
        if name == auth.SESSION_COOKIE:
            return value.strip()
    return ''


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC), **kwargs)

    def end_headers(self):
        path = self.path.split('?')[0]
        if path in ('/', '/index.html'):
            self.send_header('Cache-Control', 'no-store')
        elif path.startswith('/assets/'):
            # 带内容 hash 的静态资源允许缓存，但必须每次协商校验，防止发版后浏览器用旧副本
            self.send_header('Cache-Control', 'no-cache')
        super().end_headers()

    def respond(self, payload, status=200, close=False):
        # Cookie 指令（登录/注册/退出）由响应层消费：写成 Set-Cookie，响应体不含该键
        cookie = payload.pop('cookie', None) if isinstance(payload, dict) else None
        data = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        if cookie:
            max_age = int(cookie.get('maxAge') or 0)
            parts = [f"{cookie['name']}={cookie.get('value', '')}", 'Path=/', 'HttpOnly',
                     'SameSite=Strict', f'Max-Age={max_age}']
            self.send_header('Set-Cookie', '; '.join(parts))
        request_id = getattr(self, '_request_id', None)
        if request_id:
            self.send_header('X-Request-Id', request_id)
        if close:
            self.close_connection = True
            self.send_header('Connection', 'close')
        self.end_headers(); self.wfile.write(data)

    def _error(self, status, code, message, extra=None, close=False):
        payload = {'error': message, 'code': code}
        if extra:
            payload.update(extra)
        return self.respond(payload, status, close=close)

    def _resolve_user(self, path, free):
        """解出当前会话用户（None=未登录）。

        免登录路径也要解会话：/api/auth-state 必须能报告"带着有效 Cookie 的已登录用户"
        （否则刷新页面会误判未登录、被弹回登录页）。是否**强制**要求会话由调用处按
        free 决定（免登录接口允许 None）。
        """
        return auth.resolve_session(_cookie_token(self))

    def _unauthorized(self):
        return self._error(401, 'UNAUTHENTICATED', '请先登录')

    def _internal_error(self, exc):
        """未知程序错误：客户端只见通用消息 + requestId；完整堆栈只写服务端日志。

        绝不把 str(exc) 原文回传（SQLAlchemy 异常可能携带参数、路径或 DSN 片段）。
        """
        request_id = getattr(self, '_request_id', '-')
        self.log_error('INTERNAL_ERROR requestId=%s %s: %s', request_id,
                       type(exc).__name__, exc)
        traceback.print_exc()  # 完整堆栈走默认 stderr 日志通道，与访问日志同源可关联
        return self._error(500, 'INTERNAL_ERROR',
                           f'服务器内部错误，请稍后重试（requestId: {request_id}）', close=True)

    def do_GET(self):
        url = urlsplit(self.path); path = url.path
        if not path.startswith('/api/'):
            return super().do_GET()
        self._request_id = uuid.uuid4().hex[:12]
        _REQUEST_CTX.handler = self
        try:
            # 鉴权门：除免登录白名单外一律要求有效会话；未登录 401（接口文档 06 §1）
            user = self._resolve_user(path, free=path in AUTH_FREE_GET)
            auth.bind_request(user)
            if user is None and path not in AUTH_FREE_GET:
                return self._unauthorized()
            if path == '/api/ontologies':
                return self.respond(*model_routes.get_ontologies(parse_qs(url.query, keep_blank_values=True)))
            # 先判路由是否存在：未知端点稳定 404，不受后续任何校验影响（R4）。
            handler = GET_ROUTES.get(path, _MISSING)
            if handler is _MISSING:
                return self._error(404, 'NOT_FOUND', '接口不存在')
            # R4（2026-09-18）：不再对全部 GET 统一做本体工作区定位（原实现把无关
            # query 当 ontology 用，独立接口被本体有效性绑架）。本体/项目/编排接口
            # 均已在自己的边界校验：本体区 GET 自行 describe；项目区按 project 参数
            # 及其固定引用校验；flows/llm-providers/storage-status 与本体无关。
            return self.respond(*handler(parse_qs(url.query, keep_blank_values=True)))
        except auth.AuthRequired:
            return self._unauthorized()
        except projects.ProjectNotFound as exc:
            return self._error(404, 'NOT_FOUND', str(exc))
        except versions.VersionNotFound as exc:
            return self._error(404, 'NOT_FOUND', str(exc))
        except workspaces.WorkspaceNotFound as exc:
            return self._error(404, 'NOT_FOUND', str(exc))
        except storage.NotFound as exc:
            # 存储层「不存在」（含跨账号按不存在处理）：GET 必须 404，不能落 500
            return self._error(404, 'NOT_FOUND', str(exc))
        except storage.StorageUnavailable as exc:
            # 库不可用/锁超时/schema 缺失：明确可重试的基础设施错误，绝不回退文件
            return self._error(503, 'STORAGE_UNAVAILABLE', str(exc), close=True)
        except _catalog_unreadable as exc:
            # 目录缓存读取失败（与 do_POST 同口径）：GET 也必须是 503（可重试的基础设施错误），
            # 不能落到 500 让前端按程序错误处理。
            return self._error(503, 'STORAGE_UNAVAILABLE', str(exc), close=True)
        except ValueError as exc:
            # 业务层显式输入校验（既有约定：raise ValueError(<中文消息>) = 客户端错误）
            return self._error(400, 'INVALID_ARGUMENT', str(exc))
        except (KeyError, TypeError) as exc:
            # 请求形态不符（缺参数/类型不对）：消息泛化，细节进日志
            self.log_error('INVALID_ARGUMENT requestId=%s %s: %s',
                           getattr(self, '_request_id', '-'), type(exc).__name__, exc)
            return self._error(400, 'INVALID_ARGUMENT', '请求参数缺失或格式不正确')
        except Exception as exc:
            return self._internal_error(exc)

    def do_POST(self):
        self._request_id = uuid.uuid4().hex[:12]
        _REQUEST_CTX.handler = self
        path = urlsplit(self.path).path
        # 用哨兵区分「路由键不存在」与「路由已注册但值为可调用/二进制」，
        # 避免 None 既是「未注册」又是「二进制占位」导致已注册端点被判 404。
        handler = POST_ROUTES.get(path, _MISSING)
        if handler is _MISSING:
            return self._error(404, 'NOT_FOUND', '接口不存在', close=True)
        allowed = {None, f'http://127.0.0.1:{self.server.server_port}', f'http://localhost:{self.server.server_port}'}
        if self.headers.get('Origin') not in allowed:
            return self._error(403, 'ORIGIN_REJECTED', '请求来源不允许')
        if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
            return self._error(415, 'UNSUPPORTED_MEDIA_TYPE', '需要JSON请求')
        try:
            # 鉴权门：免登录白名单（login/register/logout）之外一律要求有效会话
            user = self._resolve_user(path, free=path in AUTH_FREE_POST)
            auth.bind_request(user)
            if user is None and path not in AUTH_FREE_POST:
                return self._unauthorized()
            length = int(self.headers.get('Content-Length', 0))
            if length > 2_000_000:
                return self._error(413, 'PAYLOAD_TOO_LARGE', '请求超过大小限制（最大 2 MB）', close=True)
            if length <= 0:
                return self._error(400, 'INVALID_ARGUMENT', '请求为空或长度无效')
            raw = self.rfile.read(length)
            try:
                payload = json.loads(raw)
            except ValueError:
                return self._error(400, 'INVALID_ARGUMENT', '请求体不是有效的 JSON')
            if not isinstance(payload, dict):
                return self._error(400, 'INVALID_ARGUMENT', '请求体必须是 JSON 对象')
            if handler is BINARY_ROUTE:
                if path == '/api/config-package-export':
                    (filename, filename_utf8), data = config_package_routes.export_bytes(payload)
                    disposition = f"attachment; filename=\"{filename}\"; filename*=UTF-8''{filename_utf8}"
                else:
                    filename_utf8 = None
                    data = model_routes.export_bytes(payload)
                    disposition = 'attachment; filename="ontology-model.zip"'
                self.send_response(200); self.send_header('Content-Type', 'application/zip')
                self.send_header('Content-Disposition', disposition)
                self.send_header('X-Request-Id', self._request_id)
                self.end_headers(); self.wfile.write(data)
                return
            return self.respond(*handler(payload))
        except auth.DuplicateUsername as exc:
            return self._error(409, 'DUPLICATE_NAME', str(exc))
        except auth.InvalidCredentials as exc:
            return self._error(401, 'UNAUTHENTICATED', str(exc))
        except auth.AuthRequired:
            return self._unauthorized()
        except auth.AuthError as exc:
            # 账号/口令规则校验（用户名或口令不符合规则）：客户端错误
            return self._error(400, 'INVALID_ARGUMENT', str(exc))
        except projects.ProjectNotFound as exc:
            return self._error(404, 'NOT_FOUND', str(exc))
        except versions.VersionNotFound as exc:
            return self._error(404, 'NOT_FOUND', str(exc))
        except workspaces.DuplicateName as exc:
            return self._error(409, 'DUPLICATE_NAME', str(exc))
        except workspaces.WorkspaceNotFound as exc:
            return self._error(404, 'NOT_FOUND', str(exc))
        except storage.NotFound as exc:
            # 存储层「不存在」（含跨账号按不存在处理）：POST 同样 404
            return self._error(404, 'NOT_FOUND', str(exc))
        except TokenError as exc:
            return self._error(*(lambda r: (r[1], r[0]['code'], r[0]['error']))(config_package_routes._token_response(exc)))
        except PackageFormatError as exc:
            # 包损坏/预算超限 422；版本/格式不支持 415（按消息归类，07 §1）
            message = str(exc)
            is_format = ('版本不支持' in message) or ('format 不符' in message) or ('payloadFormat 不支持' in message)
            return self._error(415 if is_format else 422,
                               'UNSUPPORTED_FORMAT' if is_format else 'PACKAGE_INVALID', message)
        except ImportConflict as exc:
            return self._error(409, exc.code, str(exc), extra=exc.extra)
        except storage.RevisionConflict as exc:
            # CAS 失败：与既有 409 契约一致（带服务端最新 token）
            return self._error(409, 'REVISION_CONFLICT', str(exc),
                               extra={'currentRevision': exc.current_revision})
        except storage.StorageUnavailable as exc:
            return self._error(503, 'STORAGE_UNAVAILABLE', str(exc), close=True)
        except _catalog_unreadable as exc:
            # 目录缓存读取失败（2026-09-20 v2 冻结，03 §3.3）：绝不降级为「无目录/零问题」。
            # 路由层正常已按连接 id 处理损坏条目；到达此处按存储不可用 fail-closed。
            return self._error(503, 'STORAGE_UNAVAILABLE', str(exc), close=True)
        except ValueError as exc:
            # 域层用 issues 表达「结构/依赖阻断」（08 §8），用 code 表达细分错误码。
            # 携带 issues 的异常是 422（可定位到具体候选/定义），不是 400 形态错误。
            issues = getattr(exc, 'issues', None)
            code = getattr(exc, 'code', None) or 'INVALID_ARGUMENT'
            if issues:
                return self._error(422, code, str(exc), extra={'issues': issues})
            status = getattr(exc, 'status', None)
            if status:
                return self._error(status, code, str(exc))
            return self._error(400, 'INVALID_ARGUMENT', str(exc))
        except (KeyError, TypeError) as exc:
            # 请求形态不符（如缺 state 字段）：消息泛化，细节进日志，不回传异常文本
            self.log_error('INVALID_ARGUMENT requestId=%s %s: %s',
                           self._request_id, type(exc).__name__, exc)
            return self._error(400, 'INVALID_ARGUMENT', '请求参数缺失或格式不正确')
        except Exception as exc:
            # R3（2026-09-18）：未知程序错误不再吞成 400——那会掩盖 5xx、误导客户端
            # 重试语义，并把 str(exc) 原文泄露给客户端。
            return self._internal_error(exc)


if __name__ == '__main__':
    # 启动预检（单一策略：storage.ensure_ready）——真实根未初始化时明确报错退出；
    # 隔离数据根（WIZ_WORKBENCH_ROOT，仅测试/并行实例）允许惰性初始化空库。
    # 绝不在启动或请求中隐式建真实库、绝不自动导入旧文件。
    try:
        storage.ensure_ready()
    except storage.StorageUnavailable as exc:
        raise SystemExit(str(exc)) from exc
    # WIZ_WORKBENCH_PORT 仅用于自动化测试并行实例；生产固定 18765。
    # 8765 保留给机器上其他服务（如 graph_recall_test_server），本工作台绝不经由该端口提供访问。
    port = int(os.environ.get('WIZ_WORKBENCH_PORT') or 18765)
    if port == 8765:
        raise SystemExit('端口 8765 已保留给其他服务；本工作台固定 18765（可用 WIZ_WORKBENCH_PORT 覆盖为其他端口）')
    # 后台任务：上次进程遗留的 queued/running 一律标 interrupted（绝不显示假运行中），
    # 并把超时未完成的上传临时内容做有界清理（解析/上传都不持有全局锁）。
    try:
        from workbench.ontology_build import runner as build_runner
        from workbench.ontology_build import materials as build_materials
        from workbench.storage.engine import write_tx as _write_tx
        build_runner.interrupt_stale_runs()
        with _write_tx() as _tx:
            _tx.run(lambda conn: build_materials.cleanup_expired(conn))
    except Exception:
        pass
    # 过期会话清理（维护型，失败不影响启动：下次请求仍会按过期判定拒绝）
    try:
        auth.purge_expired()
    except Exception:
        pass
    print('本体工作台 http://127.0.0.1:' + str(port), flush=True)
    ThreadingHTTPServer(('127.0.0.1', port), Handler).serve_forever()
