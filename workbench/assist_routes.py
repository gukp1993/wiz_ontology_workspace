"""表单辅助填写 HTTP 路由（T4）：assist-context / assist-generate。

薄封装：请求形态/服务错误统一转 HTTP 信封（接口文档 04 §5）。
* ContextStale → 409 CONTEXT_STALE（客户端重取上下文）；
* ModelBadResponse → 502 MODEL_BAD_RESPONSE；
* LlmError 兜底（理论上已在服务层转 AssistServiceError，这里保底）→ 502/504；
* AssistServiceError → 按 status/code；
* 其余 ValueError → server 层 400 INVALID_ARGUMENT；
* storage.NotFound / projects.ProjectNotFound → server 层 404；
* CatalogCacheUnreadable → server 层 503。
两接口均为只读语义：不写任何本体/项目修订，不持全局写锁。
"""
from workbench import assist_context, assist_schema, assist_service, llm_client


def _map(exc):
    """已知异常 → (payload, status)；未知返回 None 交 server 层既有映射。"""
    if isinstance(exc, assist_context.ContextStale):
        return {'error': str(exc), 'code': 'CONTEXT_STALE'}, 409
    if isinstance(exc, assist_schema.ModelBadResponse):
        return {'error': str(exc), 'code': 'MODEL_BAD_RESPONSE'}, 502
    if isinstance(exc, assist_service.AssistServiceError):
        return {'error': str(exc), 'code': exc.code}, exc.status
    if isinstance(exc, llm_client.LlmError):
        message = str(exc)
        if '超时' in message:
            return {'error': message, 'code': 'MODEL_TIMEOUT'}, 504
        return {'error': message, 'code': 'MODEL_BAD_RESPONSE'}, 502
    return None


def post_assist_context(payload):
    try:
        return assist_service.handle_context(payload), 200
    except (assist_context.ContextStale, assist_schema.ModelBadResponse,
             assist_service.AssistServiceError, llm_client.LlmError) as exc:
        return _map(exc)
    # ValueError / NotFound / CatalogCacheUnreadable 等交 server.py 统一映射


def post_assist_generate(payload):
    try:
        return assist_service.handle_generate(payload), 200
    except (assist_context.ContextStale, assist_schema.ModelBadResponse,
             assist_service.AssistServiceError, llm_client.LlmError) as exc:
        return _map(exc)
