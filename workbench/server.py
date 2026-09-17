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
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlsplit, parse_qs

from workbench import model_routes, project_routes, projects, versions, workspaces
from workbench import flow_routes
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
}

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
    '/api/llm-provider-test': flow_routes.post_llm_provider_test,
    '/api/export': None,  # 二进制响应：do_POST 内专用分支处理
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
        if close:
            self.close_connection = True
            self.send_header('Connection', 'close')
        self.end_headers(); self.wfile.write(data)

    def do_GET(self):
        url = urlsplit(self.path); path = url.path
        if not path.startswith('/api/'):
            return super().do_GET()
        try:
            if path == '/api/ontologies':
                return self.respond(*model_routes.get_ontologies(parse_qs(url.query, keep_blank_values=True)))
            query = parse_qs(url.query, keep_blank_values=True)
            # 原实现：/api/* 统一先按 ontology 参数定位工作区（标识非法即 404/400），保持该副作用。
            workspaces.describe(query.get('ontology', ['storage'])[0])
            handler = GET_ROUTES.get(path)
            if handler is None:
                return self.respond({'error': '接口不存在'}, 404)
            if path == '/api/projects':
                return self.respond(*handler(query, bool(url.query)))
            return self.respond(*handler(query))
        except projects.ProjectNotFound as exc:
            return self.respond({'error': str(exc)}, 404)
        except versions.VersionNotFound as exc:
            return self.respond({'error': str(exc)}, 404)
        except workspaces.WorkspaceNotFound as exc:
            return self.respond({'error': str(exc)}, 404)
        except (ValueError, KeyError, TypeError, OSError) as exc:
            return self.respond({'error': str(exc)}, 400)

    def do_POST(self):
        path = urlsplit(self.path).path
        handler = POST_ROUTES.get(path)
        if handler is None:
            return self.respond({'error': '接口不存在'}, 404, close=True)
        allowed = {None, f'http://127.0.0.1:{self.server.server_port}', f'http://localhost:{self.server.server_port}'}
        if self.headers.get('Origin') not in allowed:
            return self.respond({'error': '请求来源不允许'}, 403)
        if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
            return self.respond({'error': '需要JSON请求'}, 415)
        try:
            length = int(self.headers.get('Content-Length', 0))
            if length > 2_000_000:
                return self.respond({'error': '请求超过大小限制（最大 2 MB）'}, 413, close=True)
            if length <= 0:
                raise ValueError('请求为空或长度无效')
            payload = json.loads(self.rfile.read(length))
            if path == '/api/export':
                data = model_routes.export_bytes(payload)
                self.send_response(200); self.send_header('Content-Type', 'application/zip')
                self.send_header('Content-Disposition', 'attachment; filename=ontology-model.zip')
                self.end_headers(); self.wfile.write(data)
                return
            return self.respond(*handler(payload))
        except projects.ProjectNotFound as exc:
            return self.respond({'error': str(exc)}, 404)
        except versions.VersionNotFound as exc:
            return self.respond({'error': str(exc)}, 404)
        except workspaces.DuplicateName as exc:
            return self.respond({'error': str(exc)}, 409)
        except workspaces.WorkspaceNotFound as exc:
            return self.respond({'error': str(exc)}, 404)
        except Exception as exc:
            return self.respond({'error': str(exc)}, 400)


if __name__ == '__main__':
    # WIZ_WORKBENCH_PORT 仅用于自动化测试并行实例；生产固定 18765。
    # 8765 保留给机器上其他服务（如 graph_recall_test_server），本工作台绝不经由该端口提供访问。
    port = int(os.environ.get('WIZ_WORKBENCH_PORT') or 18765)
    if port == 8765:
        raise SystemExit('端口 8765 已保留给其他服务；本工作台固定 18765（可用 WIZ_WORKBENCH_PORT 覆盖为其他端口）')
    print('本体工作台 http://127.0.0.1:' + str(port), flush=True)
    ThreadingHTTPServer(('127.0.0.1', port), Handler).serve_forever()
