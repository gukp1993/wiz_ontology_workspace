"""Local ontology workbench HTTP entry. Bind to loopback only.

职责（B3 后）：本文件只保留 HTTP 服务与安全边界、路由分派和错误序列化；
本体区业务在 workbench/model_routes.py，项目区业务在 workbench/project_routes.py。
安全边界不变：POST 白名单、Origin 校验、2MB 请求上限、JSON Content-Type；
写操作的 LOCK 在业务模块内使用（workbench.locking 的同一把全局锁）。

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
import traceback
import uuid
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlsplit, parse_qs

from workbench import model_routes, project_routes, projects, versions, workspaces
from workbench import flow_routes, storage
from workbench.paths import CODE_ROOT

STATIC = CODE_ROOT / 'frontend/dist'

# GET 路由表：query 为 parse_qs(keep_blank_values=True) 的结果。
GET_ROUTES = {
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
}

# 已注册但响应体不是 JSON 的端点（ZIP 等二进制）。
# 用独立哨兵而非 None：路由必须能与「键不存在」区分，否则 do_POST 的前置检查会
# 把已注册端点误判为 404（导出功能曾在 /api/export 上因此完全不可达）。
BINARY_ROUTE = object()
_MISSING = object()  # 路由表查不到时的唯一标记（键存在但值为 None 不算缺失）

# POST 路由表：payload 为解析后的 JSON。白名单即本表键集合。
POST_ROUTES = {
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
    '/api/export': BINARY_ROUTE,  # 二进制响应：do_POST 内专用分支处理（哨兵 = 路由已注册）
}


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
        data = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
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
        try:
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
        except projects.ProjectNotFound as exc:
            return self._error(404, 'NOT_FOUND', str(exc))
        except versions.VersionNotFound as exc:
            return self._error(404, 'NOT_FOUND', str(exc))
        except workspaces.WorkspaceNotFound as exc:
            return self._error(404, 'NOT_FOUND', str(exc))
        except storage.StorageUnavailable as exc:
            # 库不可用/锁超时/schema 缺失：明确可重试的基础设施错误，绝不回退文件
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
                data = model_routes.export_bytes(payload)
                self.send_response(200); self.send_header('Content-Type', 'application/zip')
                self.send_header('Content-Disposition', 'attachment; filename=ontology-model.zip')
                self.send_header('X-Request-Id', self._request_id)
                self.end_headers(); self.wfile.write(data)
                return
            return self.respond(*handler(payload))
        except projects.ProjectNotFound as exc:
            return self._error(404, 'NOT_FOUND', str(exc))
        except versions.VersionNotFound as exc:
            return self._error(404, 'NOT_FOUND', str(exc))
        except workspaces.DuplicateName as exc:
            return self._error(409, 'DUPLICATE_NAME', str(exc))
        except workspaces.WorkspaceNotFound as exc:
            return self._error(404, 'NOT_FOUND', str(exc))
        except storage.RevisionConflict as exc:
            # CAS 失败：与既有 409 契约一致（带服务端最新 token）
            return self._error(409, 'REVISION_CONFLICT', str(exc),
                               extra={'currentRevision': exc.current_revision})
        except storage.StorageUnavailable as exc:
            return self._error(503, 'STORAGE_UNAVAILABLE', str(exc), close=True)
        except ValueError as exc:
            # 业务层显式输入校验（既有约定：raise ValueError(<中文消息>) = 客户端错误）
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
        raise SystemExit(str(exc))
    # WIZ_WORKBENCH_PORT 仅用于自动化测试并行实例；生产固定 18765。
    # 8765 保留给机器上其他服务（如 graph_recall_test_server），本工作台绝不经由该端口提供访问。
    port = int(os.environ.get('WIZ_WORKBENCH_PORT') or 18765)
    if port == 8765:
        raise SystemExit('端口 8765 已保留给其他服务；本工作台固定 18765（可用 WIZ_WORKBENCH_PORT 覆盖为其他端口）')
    print('本体工作台 http://127.0.0.1:' + str(port), flush=True)
    ThreadingHTTPServer(('127.0.0.1', port), Handler).serve_forever()
