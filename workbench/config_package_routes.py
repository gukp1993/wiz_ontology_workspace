"""配置迁移路由：鉴权后的参数校验与错误映射（业务在 config_packages.py）。

约定（接口文档 07）：错误码 400/404/409/410/413/415/422；
TokenError → 过期 410 TOKEN_EXPIRED、不存在/跨账号 404；
PackageFormatError → 格式不支持 415、结构/预算问题 422；
ImportConflict → 409。上传/预检/压缩不持全局 LOCK；仅 import 持 LOCK（写事务）。
"""

from workbench import auth, locking
from workbench import config_packages as core
from workbench.config_packages import ChunkConflict, TokenError


def _bad(message, code='INVALID_ARGUMENT', status=400):
    return {'error': message, 'code': code}, status


def _token_response(exc: TokenError):
    if exc.expired:
        return {'error': str(exc), 'code': 'TOKEN_EXPIRED'}, 410
    return {'error': str(exc), 'code': 'NOT_FOUND'}, 404


def post_export_preview(payload):
    owner = auth.require_user_id()
    model_ids = payload.get('modelIds')
    project_ids = payload.get('projectIds')
    extra_flow_ids = payload.get('extraFlowIds')
    if model_ids is None and project_ids is None:
        raise ValueError('请至少选择一个本体或项目。')
    result = core.build_export_preview(list(model_ids or []), list(project_ids or []),
                                       list(extra_flow_ids or []), owner)
    if result.get('blockers'):
        return {'blockers': result['blockers'], 'warnings': result.get('warnings', []),
                'assets': {'models': [], 'projects': [], 'flows': [], 'modelConfigs': []},
                'dependencyEdges': [], 'exportToken': None}, 200
    return result, 200


def export_bytes(payload):
    """二进制下载（server.py BINARY_ROUTE 分派）。返回 (filename, bytes)；错误抛 ValueError。"""
    owner = auth.require_user_id()
    token = payload.get('exportToken')
    if not token:
        raise ValueError('缺少 exportToken。')
    entry = core.export_previews.get(token, owner)
    snapshot = entry.get('snapshot') or {}
    if snapshot.get('blockers'):
        raise ValueError('存在阻断问题，无法生成配置包；请先处理预览中的阻断项。')
    stamp = entry['preview']['snapshotAt'][:19].replace(':', '').replace('-', '').replace('T', '-')
    # Content-Disposition 头仅支持 latin-1：ASCII 主名 + RFC 5987 filename* 带中文
    ascii_name = f'config-package-{stamp}.zip'
    return (ascii_name, f"config-package-{stamp}.zip"), core.build_package_zip(snapshot)


def post_stage(payload):
    owner = auth.require_user_id()
    action = payload.get('action')
    if action == 'begin':
        return core.stage_begin(owner, payload.get('filename'), payload.get('bytes'),
                                payload.get('sha256')), 200
    if action == 'chunk':
        try:
            return core.stage_chunk(owner, payload.get('uploadId'), payload.get('index'),
                                    payload.get('base64'), payload.get('chunkHash')), 200
        except ChunkConflict as exc:
            return {'error': str(exc), 'code': 'CHUNK_CONFLICT'}, 409
    raise ValueError('action 必须是 begin 或 chunk。')


def post_import_preview(payload):
    owner = auth.require_user_id()
    upload_id = payload.get('uploadId')
    if not upload_id:
        raise ValueError('缺少 uploadId。')
    return core.build_import_preview(owner, upload_id), 200


def post_import(payload):
    owner = auth.require_user_id()
    preview_token = payload.get('previewToken')
    request_id = payload.get('requestId')
    if not preview_token or not isinstance(request_id, str) or not request_id.strip():
        raise ValueError('缺少 previewToken 或 requestId。')
    request_id = request_id.strip()
    if len(request_id) > 128:
        raise ValueError('requestId 过长（最多 128 字符）。')
    entry = core.import_previews.get(preview_token, owner)
    preview = entry['preview']
    if preview['blockers']:
        return {'error': '配置包存在阻断问题，无法导入。', 'code': 'PACKAGE_INVALID',
                'blockers': preview['blockers']}, 422
    final_names = core.plan_name_overrides(preview, payload.get('nameOverrides') or {})
    with locking.LOCK:  # 仅最终导入持全局写锁；上传/预检/压缩均不持
        result = core.import_transaction(owner, preview, request_id, final_names)
    receipt = result['receipt']
    return {**receipt, 'replayed': bool(result.get('replayed'))}, 200


def post_import_result(payload):
    owner = auth.require_user_id()
    request_id = payload.get('requestId')
    if not isinstance(request_id, str) or not request_id.strip():
        raise ValueError('缺少 requestId。')
    from workbench.storage import configuration as config_store
    from workbench.storage.engine import read_connection
    with read_connection() as conn:
        existing = config_store.get_request_receipt(conn, core.IMPORT_OPERATION, owner,
                                                    request_id.strip())
    if existing is None:
        return {'status': 'none'}, 200
    return {'status': 'completed', 'receipt': existing['response']}, 200


def post_discard(payload):
    owner = auth.require_user_id()
    for key in ('uploadId', 'exportToken', 'previewToken'):
        token = payload.get(key)
        if not token:
            continue
        registry = {'uploadId': core.upload_stages, 'exportToken': core.export_previews,
                    'previewToken': core.import_previews}[key]
        registry.drop(token, owner)
    return {'ok': True}, 200
