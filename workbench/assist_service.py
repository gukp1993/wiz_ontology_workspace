"""表单辅助填写服务编排（T4）：assist-context / assist-generate 的业务串接。

职责边界：
* 上下文装载/令牌校验在 workbench/assist_context.py（T1）；
* 模型输出解析与校验在 workbench/assist_schema.py（T2）+ 受限操作校验 assist_ops.py（T2）；
* 本模块只做：请求形态校验 → 令牌与权威指纹校验 → 默认模型解析 →
  公共 chat 调用（workbench/llm_client，原始 trace 不返回不落日志）→
  输出解析组装。全程只读业务数据、不持全局写锁。

两条生成路径（接口文档 04 §5.2 / §6）：
* mode=fill + protocol=2：autofill/1 整表自动填写（2026-09-22 改版）。会话为**进程内存**
  （不落库）：TTL 30 分钟、LRU ≤200，绑定 user+space+projectId+targetKind+targetId+
  formId+schemaDigest；目标/契约/用户不匹配即失效（视同无会话）。契约 digest 签入
  contextToken（assist_context），续轮不匹配 → 409 CONTEXT_STALE。操作引用在本轮权威
  候选集中核验（复用 handle_context 同源候选装配），未命中/无效操作转 unresolved。
  缺省 protocol 的旧 fill → 400 INVALID_ARGUMENT（04 §6.0 冻结，不静默按旧协议处理）。
* mode=check/explain：旧 suggestions 协议，路径与输出结构不变。

错误语义（接口文档 04 §5.2）：本模块抛 AssistServiceError（带 status/code），
由 assist_routes 统一转换为 HTTP 响应；ContextStale→409、ModelBadResponse→502
由路由层直接映射。
"""
import secrets
import time
from collections import OrderedDict

from workbench import assist_context, assist_fields, assist_forms
from workbench import assist_schema, llm_client, llm_providers

_MODES = ('fill', 'check', 'explain')
_MAX_ANSWERS = 8
_ALLOWED_ANSWER_KEYS = {'value', 'unsure'}

# ---- autofill/1 会话与限额（04 §6.2/§6.4/§6.5 冻结） ----------------------------
FILL_PROTOCOL = 2                       # fill 请求必须携带的协议版本常量
SESSION_TTL_SECONDS = 30 * 60           # 会话 TTL 30 分钟
SESSION_MAX = 200                       # 每进程 LRU 上限
_FILL_ANSWER_KEYS = {'questionId', 'value', 'unsure'}
_SESSION_ID_PREFIX, _ROUND_ID_PREFIX = 's_', 'r_'

# refProvider 提供方名 → assist_fields 候选类型（_Candidates 提取；与 T1 契约 refProviders
# 对应，见 contracts/forms/*.json）。引用存在性核验唯一白名单：不在下列映射的提供方
# 走「显式补齐钩子」（context[提供方名] 列表）或 fail-closed（候选不可用 → unresolved）。
_PROVIDER_REF_TYPES = {
    'ontologyObjects': assist_fields.REF_OBJECT,
    'ontologyProperties': assist_fields.REF_PROPERTY,
    'mysqlConnections': assist_fields.REF_CONN_MYSQL,
    'connectionsAndSources': assist_fields.REF_CONN_ANY,
    'catalogTables': assist_fields.REF_TABLE,
    'catalogFields': assist_fields.REF_FIELD,
    'instanceSources': assist_fields.REF_SOURCE,
    'flows': assist_fields.REF_FLOW,
    'flowOutputs': assist_fields.REF_FLOW_OUTPUT,
    'flowFields': assist_fields.REF_FLOW_FIELD,
}
# 特例提供方（上下文未直接装配，按 T1 契约 refProviders 名补齐口径见 _provider_ids）
_PROVIDER_IDENTITY_FIELDS = 'identityTableFields'   # 身份表字段：按草稿所选表核对
_PROVIDER_PARAMETERS = 'projectParameters'
_PROVIDER_ACTION_INPUTS = 'actionInputs'
_PROVIDER_LABELS = {
    'ontologyObjects': '对象', 'ontologyProperties': '属性',
    'mysqlConnections': '数据连接', 'connectionsAndSources': '数据连接/来源',
    'catalogTables': '表', 'catalogFields': '字段', 'instanceSources': '实例来源',
    'flows': '函数编排', 'flowOutputs': '编排输出', 'flowFields': '编排输出字段',
    'identityTableFields': '身份表字段', 'projectParameters': '项目参数',
    'actionInputs': '动作输入',
}


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
    """answers：{qid:{value|unsure}} 结构化校验（分别作答，支持暂不确定）——旧协议。"""
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


def _clean_fill_answers(raw):
    """autofill/1 answers：[{questionId, value?, unsure?}] 数组形态校验（04 §6.2）。

    只做形态/上限校验；questionId 归属（本会话未答集合）在会话校验阶段进行。
    """
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError('answers 必须是数组（autofill/1：[{questionId,value,unsure}]）')
    if len(raw) > _MAX_ANSWERS:
        raise ValueError(f'answers 条目不能超过 {_MAX_ANSWERS}')
    cleaned, seen = [], set()
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError('answers 元素必须是对象')
        extra = set(str(k) for k in item) - _FILL_ANSWER_KEYS
        if extra:
            raise ValueError('answers 携带未知键：' + '、'.join(sorted(extra)[:3]))
        qid = str(item.get('questionId') or '').strip()
        if not qid:
            raise ValueError('answers 的 questionId 不能为空')
        if qid in seen:
            raise ValueError('answers 中问题 ' + qid + ' 重复作答')
        seen.add(qid)
        entry = {'questionId': qid}
        value = item.get('value')
        unsure = item.get('unsure')
        if unsure is not None:
            if not isinstance(unsure, bool):
                raise ValueError('answers[' + qid + '].unsure 必须为布尔值')
            if unsure:
                entry['unsure'] = True
        if value is not None and value != '':
            if not isinstance(value, str):
                raise ValueError('answers[' + qid + '].value 必须为字符串')
            if len(value) > assist_fields.MAX_ANSWER:
                raise ValueError(f'answers[{qid}] 超过 {assist_fields.MAX_ANSWER} 字符上限')
            entry['value'] = value
        if 'value' not in entry and not entry.get('unsure'):
            raise ValueError('answers[' + qid + '] 缺少 value 或 unsure')
        cleaned.append(entry)
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

    mode=fill + protocol=2 走 autofill/1 分支（_generate_fill）；缺失 protocol 的旧
    fill → 400（04 §6.0 冻结）。check/explain 沿用旧协议路径，行为不变。

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
    draft = payload.get('draft')
    if draft is None:
        draft = {}
    if not isinstance(draft, dict):
        raise ValueError('draft 必须是 JSON 对象')
    token_raw = payload.get('contextToken')
    if not isinstance(token_raw, str) or not token_raw.strip():
        raise ValueError('contextToken 不能为空')

    if mode == 'fill':
        if payload.get('protocol') != FILL_PROTOCOL or isinstance(payload.get('protocol'), bool):
            raise ValueError('mode=fill 必须携带 protocol: 2（autofill/1 协议）；'
                             '旧 fill 协议已停用，请升级客户端后重新发起')
        fill_answers = _clean_fill_answers(payload.get('answers'))
        token_payload = assist_context.verify_token(token_raw.strip())
        return _generate_fill(payload, request_id, token_payload, draft, intent,
                              fill_answers, started)

    answers = _clean_answers(payload.get('answers'))
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


# ---- autofill/1（mode=fill + protocol=2）会话存储（进程内存，不落库） --------------

_SESSIONS = OrderedDict()   # sessionId → record（LRU：move_to_end 触碰）


def _session_binding(token_payload, contract):
    """会话绑定键：user+space+projectId+targetKind+targetId+formId+schemaDigest（04 §6.4）。"""
    return {'uid': str(token_payload.get('uid') or ''),
            'space': str(token_payload.get('space') or ''),
            'projectId': str(token_payload.get('projectId') or ''),
            'targetKind': str(token_payload.get('targetKind') or ''),
            'targetId': str(token_payload.get('targetId') or ''),
            'formId': contract.form_id,
            'digest': contract.digest}


def _session_lookup(session_id, binding):
    """有效会话（未过期且绑定完全匹配）→ record；否则 None（失效视同无会话）。"""
    record = _SESSIONS.get(session_id)
    if record is None:
        return None
    if record['expiresAt'] <= time.time() or record['binding'] != binding:
        _SESSIONS.pop(session_id, None)
        return None
    _SESSIONS.move_to_end(session_id)
    return record


def _session_create(binding):
    """新建会话（顺带淘汰过期与 LRU 超限的最旧会话）。"""
    now = time.time()
    for sid in [sid for sid, rec in _SESSIONS.items() if rec['expiresAt'] <= now]:
        _SESSIONS.pop(sid, None)
    while len(_SESSIONS) >= SESSION_MAX:
        _SESSIONS.popitem(last=False)
    session_id = _SESSION_ID_PREFIX + secrets.token_hex(8)
    _SESSIONS[session_id] = {'binding': binding, 'pending': {}, 'issued': set(),
                             'answered': [], 'expiresAt': now + SESSION_TTL_SECONDS}
    return session_id


def _session_commit(session_id, record):
    _SESSIONS[session_id] = record
    _SESSIONS.move_to_end(session_id)


# ---- autofill/1 引用存在性核验（本轮权威候选集） -----------------------------------

def _provider_ids(context, cand, provider, draft):
    """refProvider 提供方名 → 本轮权威候选 id 集（04 §6.3 引用核验依据）。

    已知提供方走 _Candidates（与 handle_context 候选装配同源）；上下文未装配的提供方
    按 T1 契约 refProviders 名补齐：身份表字段取草稿所选表的目录字段，参数/动作输入取
    扩展键，其余未知提供方走 context[提供方名] 显式列表钩子；仍无 → 空集（fail-closed）。
    """
    ref_type = _PROVIDER_REF_TYPES.get(provider)
    if ref_type is not None:
        return cand.ids(ref_type)
    if provider == _PROVIDER_IDENTITY_FIELDS:
        table = draft.get('table') if isinstance(draft, dict) else None
        fields = cand.table_fields(table) if table else None
        return set(fields) if fields is not None else set()
    if provider == _PROVIDER_PARAMETERS:
        return set(cand.parameters)
    if provider == _PROVIDER_ACTION_INPUTS:
        return set(cand.action_inputs)
    extra = context.get(provider) if isinstance(context, dict) else None
    if isinstance(extra, list):
        ids = set()
        for entry in extra:
            if isinstance(entry, dict):
                text = entry.get('id') or entry.get('name') or entry.get('value')
            else:
                text = entry
            if isinstance(text, str) and text.strip():
                ids.add(text.strip())
            elif isinstance(text, (int, float)) and not isinstance(text, bool):
                ids.add(str(text))
        return ids
    return set()


def _ref_unresolved_reason(provider, available):
    label = _PROVIDER_LABELS.get(provider, provider)
    if not available:
        return ('引用候选（%s）在当前上下文中不可用，请刷新目录或人工选择' % label)
    return '引用的%s不存在或不在当前候选范围' % label


def _operation_ref_reason(operation, contract, context, cand, draft):
    """单操作引用核验：ref 值必须在本轮权威候选集中。返回 None=通过，否则 unresolved 原因。"""
    op = operation.get('op')
    if op == 'clear' or op == 'row.remove':
        return None
    if op == 'set':
        try:
            spec = contract.field_def(operation.get('field'))
        except KeyError:
            return None  # 契约内字段已在 assist_ops 校验，防御性放行
        if spec.get('type') != 'ref':
            return None
        provider = spec.get('ref') or ''
        ids = _provider_ids(context, cand, provider, draft)
        value = operation.get('value')
        if isinstance(value, str) and value in ids:
            return None
        return _ref_unresolved_reason(provider, bool(ids))
    if op in ('row.append', 'row.update'):
        try:
            ldef = contract.list_def(operation.get('field'))
        except KeyError:
            return None
        cells = operation.get('row', {}).get('fields') if op == 'row.append' \
            else operation.get('fields')
        if not isinstance(cells, dict):
            return None
        for key, value in cells.items():
            spec = (ldef.get('fields') or {}).get(key)
            if not spec or spec.get('type') != 'ref':
                continue
            provider = spec.get('ref') or ''
            ids = _provider_ids(context, cand, provider, draft)
            if isinstance(value, str) and value in ids:
                continue
            return _ref_unresolved_reason(provider, bool(ids))
    return None


def _verify_operation_refs(operations, contract, context, cand, draft):
    """合法操作引用核验：未命中候选集的操作整条转 unresolved（04 §6.3 引用不存在）。"""
    kept, invalid = [], []
    for operation in operations:
        reason = _operation_ref_reason(operation, contract, context, cand, draft)
        if reason is None:
            kept.append(operation)
        else:
            invalid.append({'op': str(operation.get('op') or ''),
                            'field': str(operation.get('field') or ''),
                            'reason': reason})
    return kept, invalid


# ---- autofill/1 主流程 -------------------------------------------------------------

def _generate_fill(payload, request_id, token_payload, draft, intent, raw_answers, started):
    """autofill/1 生成（04 §6）：契约 digest 核对 → 会话/答案归属 → 模型 → 解析组装。"""
    space = str(token_payload.get('space') or '')
    project_id = str(token_payload.get('projectId') or '')
    target_kind = str(token_payload.get('targetKind') or '')
    target_id = str(token_payload.get('targetId') or '')
    ontology_id = str(token_payload.get('ontologyId') or 'storage')

    normalized, draft_kind = assist_fields.normalize_draft(target_kind, draft)
    contract = assist_forms.FormContract(target_kind, draft_kind)
    # 续轮握手（04 §6.1/§6.2）：契约 digest 变化 → 在途会话与令牌一并失效（409）
    if str(token_payload.get('fd') or '') != contract.digest:
        raise assist_context.ContextStale('表单契约已变化，请重新获取上下文')
    context_payload, _tp = assist_context.build_context(
        space, project_id, target_kind, target_id, 'fill', draft, ontology_id=ontology_id)
    normalized = assist_context.check_generate(
        token_payload, space, project_id, target_kind, target_id, draft,
        expected_fingerprint_fn=lambda: str(context_payload.get('contextFingerprint') or ''),
        ontology_id=ontology_id)
    context = context_payload['context']
    if not context.get('modelReady'):
        raise AssistServiceError(422, 'MODEL_NOT_CONFIGURED',
                                 '尚未配置默认模型：请到「模型设置」添加提供方后重试')

    # ---- 会话与答案归属（先只读校验，模型成功后才提交会话变更） -------------------
    binding = _session_binding(token_payload, contract)
    session_id_raw = payload.get('sessionId')
    if session_id_raw is not None and not isinstance(session_id_raw, str):
        raise ValueError('sessionId 必须为字符串')
    session_id_raw = (session_id_raw or '').strip()
    session = _session_lookup(session_id_raw, binding) if session_id_raw else None
    if raw_answers and session is None:
        # 会话失效/不存在时携答案续答：问题必然不在未答集合（04 §6.4）
        raise AssistServiceError(400, 'INVALID_ARGUMENT', '问题已过期，请重新发起')
    answered_now = []
    for answer in raw_answers:
        qid = answer['questionId']
        info = session['pending'].get(qid) if session else None
        if info is None:
            raise AssistServiceError(400, 'INVALID_ARGUMENT', '问题已过期，请重新发起')
        answered_now.append((qid, info, answer))
    model_answers = list(session['answered']) if session else []
    unsure_unresolved = []
    for qid, info, answer in answered_now:
        record = {'id': qid, 'question': str(info.get('text') or '')}
        if answer.get('unsure'):
            record['unsure'] = True
            reason = ('问题「%s」您选择暂不确定，请人工确认后手动填写'
                      % (str(info.get('text') or '')[:120] or qid))
            for field_path in info.get('fields') or ():
                unsure_unresolved.append({'field': str(field_path), 'reason': reason})
        else:
            record['value'] = str(answer.get('value') or '')
        model_answers.append(record)

    # ---- 模型调用与输出解析（引用核验依据 = 本轮权威上下文候选） -------------------
    valid_question_ids = set(session['issued']) if session else set()
    user_payload = assist_schema.build_fill_user_payload(
        context, normalized, intent, model_answers, contract)
    messages = [{'role': 'system', 'content': assist_schema.FILL_SYSTEM_PROMPT},
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
    parsed = assist_schema.parse_fill_output(content, intent, valid_question_ids, contract)
    cand = assist_schema._Candidates(context)
    operations, ref_invalid = _verify_operation_refs(
        parsed['operations'], contract, context, cand, normalized)
    # 等值过滤（A14）：与当前草稿完全相同的 set/clear/row.update 不算有效变更，
    # 剔除后由 status=empty + 摘要如实告知（不产生前端「已填写 N 项」的假成功）。
    operations = [op for op in operations if not _fill_unchanged(op, contract, normalized)]

    # ---- 会话提交（答案消费 + 新问题归属本轮） -------------------------------------
    round_id = _ROUND_ID_PREFIX + secrets.token_hex(8)
    if session is None:
        session_id = _session_create(binding)
        session = _SESSIONS[session_id]
    else:
        session_id = session_id_raw
    answered_before = len(session['answered'])
    for qid, _info, _answer in answered_now:
        session['pending'].pop(qid, None)
    session['answered'].extend(model_answers[answered_before:])
    for question in parsed['questions']:
        session['pending'][question['id']] = {'roundId': round_id,
                                              'text': question['text'],
                                              'fields': list(question['fields'])}
        session['issued'].add(question['id'])
    session['expiresAt'] = time.time() + SESSION_TTL_SECONDS
    _session_commit(session_id, session)

    # ---- unresolved 组装（unsure 字段 + 无效操作 + 引用未命中 + 模型自报）----------
    merged = list(unsure_unresolved)
    for item in parsed['invalid_operations']:
        merged.append({'field': str(item.get('field') or ''),
                       'reason': str(item.get('reason') or '')})
    merged.extend(ref_invalid)
    merged.extend(parsed['unresolved'])
    deduped, seen = [], set()
    for item in merged:
        key = (item.get('field'), item.get('reason'))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    unresolved = deduped[:assist_schema.MAX_UNRESOLVED]

    status = 'ok' if (operations or parsed['questions']) else 'empty'
    return {'protocol': 'autofill/1',
            'status': status,
            'requestId': request_id,
            'formId': contract.form_id,
            'schemaVersion': contract.schema_version,
            'schemaDigest': contract.digest,
            'target': {'space': space, 'targetKind': target_kind, 'targetId': target_id},
            'draftFingerprint': assist_fields.canonical_hash(normalized),
            'contextFingerprint': str(context_payload.get('contextFingerprint') or ''),
            'sessionId': session_id,
            'roundId': round_id,
            'operations': operations,
            'questions': parsed['questions'],
            'unresolved': unresolved,
            'summary': _fill_summary(operations, parsed['questions'], unresolved),
            'meta': _provider_meta(started)}


def _fill_unchanged(operation, contract, draft):
    """等值操作判定（需求 §4.5/A14：无有效变更须明确 empty，不假称成功）。

    set：值与当前草稿同值（含 None ≡ 缺失）；row.update：行内字段全部同值；
    clear：目标当前已为空（无值/空串）；row.remove 一律视为有效变更。
    """
    if not isinstance(draft, dict):
        return False
    op = operation.get('op')
    if op == 'set':
        spec_ok = True
        try:
            contract.field_def(operation.get('field'))
        except KeyError:
            spec_ok = False
        if not spec_ok:
            return False
        return draft.get(operation.get('field')) == operation.get('value')
    if op == 'clear':
        value = draft.get(operation.get('field'))
        return value is None or value == ''
    if op == 'row.update':
        rows = draft.get(operation.get('field'))
        if not isinstance(rows, list):
            return False
        for row in rows:
            if isinstance(row, dict) and row.get('rowId') == operation.get('rowId'):
                fields = operation.get('fields') or {}
                if isinstance(fields, dict) and fields:
                    return all(row.get(key) == value for key, value in fields.items())
        return False
    return False


def _fill_summary(operations, questions, unresolved):
    parts = []
    if operations:
        parts.append('已生成 %d 项变更' % len(operations))
    if questions:
        parts.append('%d 个问题待确认' % len(questions))
    if unresolved:
        parts.append('%d 项待补充' % len(unresolved))
    return '，'.join(parts) if parts else '本次没有可自动填写的内容'


def _provider_meta(started):
    provider_meta = llm_providers.default_provider() or {}
    return {'durationMs': int((time.monotonic() - started) * 1000),
            'provider': str(provider_meta.get('name') or ''),
            'model': str(provider_meta.get('model') or '')}
