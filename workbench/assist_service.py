"""表单辅助填写服务编排（T4）：assist-context / assist-generate 的业务串接。

职责边界：
* 上下文装载/令牌校验在 workbench/assist_context.py（T1）；
* 模型输出解析与校验在 workbench/assist_schema.py（T2）；
* 本模块只做：请求形态校验 → 令牌与权威指纹校验 → 默认模型解析 →
  公共 chat 调用（workbench/llm_client，原始 trace 不返回不落日志）→
  输出解析组装。全程只读业务数据、不持全局写锁。
错误语义（接口文档 04 §5.2）：本模块抛 AssistServiceError（带 status/code），
由 assist_routes 统一转换为 HTTP 响应；ContextStale→409、ModelBadResponse→502
由路由层直接映射。
"""
import time

from workbench import assist_context, assist_fields, assist_schema, llm_client, llm_providers

_MODES = ('fill', 'check', 'explain')
_MAX_ANSWERS = 8
_ALLOWED_ANSWER_KEYS = {'value', 'unsure'}


class AssistServiceError(ValueError):
    """携带 HTTP status 与错误 code 的服务错误（路由层转响应信封）。"""

    def __init__(self, status, code, message):
        super().__init__(message)
        self.status = status
        self.code = code


def _require_str(payload, key, required=True, max_len=None, label=None):
    value = payload.get(key)
    if value is None or value == '':
        if required:
            raise ValueError((label or key) + ' 不能为空')
        return ''
    if not isinstance(value, str):
        raise ValueError((label or key) + ' 必须为字符串')
    value = value.strip()
    if required and not value:
        raise ValueError((label or key) + ' 不能为空')
    if max_len and len(value) > max_len:
        raise ValueError((label or key) + f' 超过 {max_len} 字符上限')
    return value


def _clean_answers(raw):
    """answers：{qid:{value|unsure}} 结构化校验（分别作答，支持暂不确定）。"""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError('answers 必须是 JSON 对象')
    if len(raw) > _MAX_ANSWERS:
        raise ValueError(f'answers 条目不能超过 {_MAX_ANSWERS}')
    cleaned = {}
    for qid, answer in raw.items():
        qid = str(qid or '').strip()
        if not qid:
            raise ValueError('answers 的 questionId 不能为空')
        if not isinstance(answer, dict) or set(answer) - _ALLOWED_ANSWER_KEYS:
            raise ValueError('answers[' + qid + '] 形态无效（仅允许 value/unsure）')
        value = answer.get('value')
        unsure = answer.get('unsure')
        entry = {}
        if value is not None and value != '':
            if not isinstance(value, str):
                raise ValueError('answers[' + qid + '].value 必须为字符串')
            if len(value) > assist_fields.MAX_ANSWER:
                raise ValueError(f'answers[{qid}] 超过 {assist_fields.MAX_ANSWER} 字符上限')
            entry['value'] = value
        if unsure is not None:
            if not isinstance(unsure, bool):
                raise ValueError('answers[' + qid + '].unsure 必须为布尔值')
            if unsure:
                entry['unsure'] = True
        if entry:
            cleaned[qid] = entry
    return cleaned


def handle_context(payload):
    """POST /api/assist-context：形态校验后交 T1 构建只读上下文与签名令牌。"""
    if not isinstance(payload, dict):
        raise ValueError('请求体必须是 JSON 对象')
    space = _require_str(payload, 'space', label='space')
    project_id = _require_str(payload, 'projectId', required=(space == 'project'),
                              max_len=120, label='projectId')
    target_kind = _require_str(payload, 'targetKind', label='targetKind')
    target_id = _require_str(payload, 'targetId', required=False, max_len=200, label='targetId')
    purpose = _require_str(payload, 'purpose', label='purpose')
    draft = payload.get('draft')
    if draft is None:
        draft = {}
    if not isinstance(draft, dict):
        raise ValueError('draft 必须是 JSON 对象')
    # 本体工作区 id 可选：省略/空 = 默认工作区 storage（接口文档 04 §5.1）
    ontology_id = (_require_str(payload, 'ontologyId', required=False, max_len=120,
                                label='ontologyId') or 'storage') if space == 'ontology' else 'storage'
    result, _token_payload = assist_context.build_context(
        space, project_id, target_kind, target_id, purpose, draft, ontology_id=ontology_id)
    return result


def handle_generate(payload):
    """POST /api/assist-generate：令牌校验 → 默认模型 → 公共 chat → 输出校验组装。

    权威上下文在 generate 时重取一次：与令牌指纹一致（否则 CONTEXT_STALE），
    并直接作为模型候选与输出核验依据（上下文与校验同源，防止基线错位）。
    """
    started = time.monotonic()
    if not isinstance(payload, dict):
        raise ValueError('请求体必须是 JSON 对象')
    request_id = _require_str(payload, 'requestId', max_len=64, label='requestId')
    mode = _require_str(payload, 'mode', label='mode')
    if mode not in _MODES:
        raise ValueError('mode 必须为 fill、check 或 explain')
    intent = _require_str(payload, 'intent', required=False,
                          max_len=assist_fields.MAX_INTENT, label='intent')
    answers = _clean_answers(payload.get('answers'))
    draft = payload.get('draft')
    if draft is None:
        draft = {}
    if not isinstance(draft, dict):
        raise ValueError('draft 必须是 JSON 对象')
    token_raw = payload.get('contextToken')
    if not isinstance(token_raw, str) or not token_raw.strip():
        raise ValueError('contextToken 不能为空')

    token_payload = assist_context.verify_token(token_raw.strip())
    space = str(token_payload.get('space') or '')
    project_id = str(token_payload.get('projectId') or '')
    target_kind = str(token_payload.get('targetKind') or '')

    ontology_id = str(token_payload.get('ontologyId') or 'storage')
    context_payload, _tp = assist_context.build_context(
        space, project_id, target_kind, str(token_payload.get('targetId') or ''),
        mode, draft, ontology_id=ontology_id)
    normalized = assist_context.check_generate(
        token_payload, space, project_id, target_kind,
        str(token_payload.get('targetId') or ''), draft,
        expected_fingerprint_fn=lambda: str(context_payload.get('contextFingerprint') or ''),
        ontology_id=ontology_id)
    context = context_payload['context']
    if not context.get('modelReady'):
        raise AssistServiceError(422, 'MODEL_NOT_CONFIGURED',
                                 '尚未配置默认模型：请到「模型设置」添加提供方后重试')
    draft_kind = context.get('draftKind') or (normalized.get('kind') if isinstance(normalized, dict) else None)

    user_payload = assist_schema.build_user_payload(context, normalized, intent, answers, mode)
    messages = [{'role': 'system', 'content': assist_schema.ASSIST_SYSTEM_PROMPT},
                {'role': 'user', 'content': user_payload}]
    try:
        content, _trace = llm_client.chat(provider=llm_providers.default_provider(),
                                          messages=messages,
                                          max_tokens=assist_fields.MAX_MODEL_TOKENS)
    except llm_client.LlmError as exc:
        message = str(exc)
        if '超时' in message:
            raise AssistServiceError(504, 'MODEL_TIMEOUT', message) from None
        raise AssistServiceError(502, 'MODEL_BAD_RESPONSE', message) from None
    parsed = assist_schema.parse_model_output(content, target_kind, draft_kind, context,
                                              normalized, answers, mode=mode)
    suggestions = parsed.get('suggestions') or []
    has_content = bool(suggestions or parsed.get('questions') or parsed.get('issues')
                       or parsed.get('explanation'))
    duration_ms = int((time.monotonic() - started) * 1000)
    provider_meta = llm_providers.default_provider() or {}
    return {'requestId': request_id,
            'status': 'ok' if has_content else 'empty',
            'contextFingerprint': str(context_payload.get('contextFingerprint') or ''),
            'questions': parsed.get('questions') or [],
            'suggestions': suggestions,
            'issues': parsed.get('issues') or [],
            'explanation': parsed.get('explanation'),
            'meta': {'durationMs': duration_ms,
                     'provider': str(provider_meta.get('name') or ''),
                     'model': str(provider_meta.get('model') or '')}}
