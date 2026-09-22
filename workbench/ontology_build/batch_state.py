"""本体生成控输出 v3：纯状态机 / 指纹 / 覆盖与容量守卫（D07 交付）。

契约来源：workbench/ontology_build/batch_contracts.py（D00 冻结，提交 87d1473）；
需求：《本体生成控输出_整合方案与并行开发计划_v3.md》（§4.3 已派发不可重排/superseded、
§6 持久化协议全文、§8 D07 行）。

职责边界（纯函数模块）：
* 只 import 标准库与 batch_contracts，不 import 存储层 / llm / pipeline；
* 状态推进全部在内存 dict（checkpoint['generate'] 形态，冻结形状见契约注释块）上进行，
  落库与事务由 D08 负责；本模块绝不触碰存储；
* apply_event 深拷贝后变更、输入不可变；任何非法迁移 / 结构缺失抛 ValueError，
  绝不把非法状态写成假成功；
* 本模块只对计划状态负责：候选结构检查由既有交付校验承担
  （与契约 final_state_check 同一边界）。

pendingTargetIds 语义（本模块冻结的实现口径）：
* 目标一旦被任何「非 superseded」作业认领（job_created / job_split 子作业），即离开 pending；
* 作业被 superseded 时其目标回到 pending（等待重打包 / 新作业认领，§4.3）；
* 作业 succeeded 的目标同样不在 pending（完成数由 coverage.targetCompleted 单独计）；
* coverage 在每次结构事件后重算：targetCompleted = 成功叶覆盖目标数、
  targetPending = targetTotal − targetCompleted（"父 split 不算成功"由此保证）；
* usageAggregate 在每次尝试收口（succeeded/failed/interrupted_unknown）后按已收口
  尝试重算（复用 contracts.usage_aggregate）：在途 started 不计 calls，unknown 不计 0。

对冻结形状的有界附加字段（协议变更请求，待 C 并入 batch_contracts.py 后生效为正式协议）：
* attempts[*].errorCode / errorMessage：attempt_failed 事件的错误分类与 ≤400 字符消息
  （D13 用量/质量评价需按 attempt 分类记账；冻结形状只列最小字段集，此处零信息丢失）；
* jobs[*].supersededBy：job_superseded 事件的重打包映射（§4.3 要求持久化该决定）。
另有一处契约口径问题已上报：contracts.usage_aggregate 只识别 snake_case 原始键，
对 normalize_usage 输出形状不幂等——本模块以 _raw_usage 适配输入复用同一实现，
不复制聚合逻辑；建议 C 评估把 usage_aggregate 改为幂等。
两者均为追加字段，不影响既有字段与任何读取方。

拆分登记制（2026-09-22 D00 裁定，已并入 batch_contracts.py 正式协议：
Target.expandedInto 可选字段 + final_state_check 按「有效叶目标集」比对覆盖，
本模块为 batch_state 侧落地）：
* job_split 的 children 项冻结形状扩为 {'jobId','orderedPrimaryTargetIds',
  'contextFactIds','estimate','parentId','rootId','splitPath','splitSelectors'?,
  'primaryTargets'?}；children 携带 'primaryTargets'（selector 子目标定义）时
  apply_event 同步把这些 target 登记进 doc['targets']（'t-' 前缀、同 id 同内容
  幂等、冲突 ValueError），并把父作业每个 primary target 标 'expandedInto' =
  [本次新增子目标 id，按子目标定义顺序、factId+父选择器对应]；
* selector 拆分的守恒按「有效叶」核验（_verify_expanding_split）：每个新增子目标
  恰对应一个父主目标且恰被一个子作业认领，父主目标要么被展开要么被直接认领；
  无 'primaryTargets'（单元级拆分，子作业复用既有目标）行为与现状完全一致；
* 查询/校验配套：effective_leaf_target_ids（未展开目标 id，D09/D16 复用口径）、
  validate_plan_doc 的 EXPANDED_* 错误码（悬空引用/展开链/成功叶直接覆盖已展开目标）。
"""
import copy

from workbench.ontology_build import batch_contracts as contracts

# 可受理的事件类型（apply_event 之外一律 ValueError）。
EVENT_JOB_CREATED = 'job_created'
EVENT_JOB_CLAIMED = 'job_claimed'
EVENT_ATTEMPT_STARTED = 'attempt_started'
EVENT_ATTEMPT_SUCCEEDED = 'attempt_succeeded'
EVENT_ATTEMPT_FAILED = 'attempt_failed'
EVENT_ATTEMPT_UNKNOWN = 'attempt_unknown'
EVENT_JOB_SUCCEEDED = 'job_succeeded'
EVENT_JOB_FAILED = 'job_failed'
EVENT_JOB_REQUEUED = 'job_requeued'
EVENT_JOB_BLOCKED = 'job_blocked'
EVENT_JOB_SPLIT = 'job_split'
EVENT_JOB_SUPERSEDED = 'job_superseded'
EVENT_BLOCKING_SET = 'blocking_set'
EVENT_ELAPSED_UPDATE = 'elapsed_update'

_TERMINAL_JOB_STATES = (contracts.JOB_SPLIT, contracts.JOB_SUCCEEDED, contracts.JOB_FAILED,
                        contracts.JOB_BLOCKED)


# --- 有界化（checkpoint 字段预算：错误文本 ≤400、各标识 ≤200）-------------------------


def bound_error(message):
    """错误文本 → ≤MAX_PLAN_ERROR_CHARS 字符（契约 §6 字段预算）。"""
    return str(message or '')[:contracts.MAX_PLAN_ERROR_CHARS]


def bound_id(value):
    """标识/摘要 → ≤MAX_PLAN_ID_CHARS 字符。"""
    return str(value or '')[:contracts.MAX_PLAN_ID_CHARS]


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _as_int(value):
    """整数化：bool/非数值/非整浮点 → None（绝不猜 0）。"""
    if not _is_number(value):
        return None
    if isinstance(value, float) and not float(value).is_integer():
        return None
    return int(value)


def _optional_int(value):
    return _as_int(value)


def _require_int(value, field, minimum=None):
    number = _as_int(value)
    if number is None or (minimum is not None and number < minimum):
        raise ValueError('字段 %s 需要整数（≥%s），实际 %r' % (field, minimum, value))
    return number


def _require_id(event, key):
    value = event.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError('事件 %s 缺少必填字段 %s' % (event.get('type'), key))
    return value.strip()


# --- 计划构造 -----------------------------------------------------------------------


def _normalize_targets(targets):
    """targets（{targetId: target} 或 [target]）→ 冻结形状 {targetId: 有界 target}。"""
    if isinstance(targets, dict):
        items = list(targets.items())
    else:
        items = [(str(t.get('targetId') or ''), t) for t in (targets or [])
                 if isinstance(t, dict)]
    normalized = {}
    for key, target in items:
        target = target if isinstance(target, dict) else {}
        target_id = str(target.get('targetId') or key or '').strip()
        fact_id = str(target.get('factId') or '').strip()
        if not target_id or not fact_id:
            raise ValueError('目标缺少 targetId/factId：%r' % (key,))
        if target_id in normalized:
            raise ValueError('目标 id 重复：%s' % target_id)
        selector = target.get('selector')
        normalized[target_id] = {
            'factId': bound_id(fact_id),
            'selector': copy.deepcopy(selector) if isinstance(selector, dict)
                        else {'type': 'whole'},
            'subjectKey': bound_id(target.get('subjectKey')),
            'kind': bound_id(target.get('kind')),
        }
    if not normalized:
        raise ValueError('计划至少需要一个目标')
    return normalized


def _bounded_budget_profile(profile):
    """BudgetProfile → checkpoint 有界子集：只存 errorCodes，不存 errors 全文。"""
    profile = profile if isinstance(profile, dict) else {}
    effective = profile.get('effective') if isinstance(profile.get('effective'), dict) else {}
    return {
        'enabled': bool(profile.get('enabled')),
        'providerId': bound_id(profile.get('providerId')),
        'model': bound_id(profile.get('model')),
        'contextTokens': _optional_int(profile.get('contextTokens')),
        'outputLimitTokens': _optional_int(profile.get('outputLimitTokens')),
        'requestOutputTokens': _optional_int(profile.get('requestOutputTokens')) or 0,
        'targetRatio': float(profile.get('targetRatio') or 0.0),
        'errorCodes': [bound_id(issue.get('code')) for issue in (profile.get('errors') or [])
                       if isinstance(issue, dict)],
        'effective': {
            'outputCap': _optional_int(effective.get('outputCap')),
            'targetBudget': _optional_int(effective.get('targetBudget')),
            'inputReserve': _optional_int(effective.get('inputReserve')),
        },
    }


def create_plan(batch_id, scope_revision, material_revision, targets, profile, codec_version,
                fingerprint_parts, plan_epoch=1):
    """构造合法 checkpoint['generate'] 结构（契约冻结形状）。

    planId = 'p-' + 指纹[:12]；指纹复用 contracts.plan_fingerprint（密钥绝不入 parts）。
    codec_version 必须是 contracts.CODEC_VERSIONS 之一；targets 至少一个且每项含
    targetId/factId。纯函数，零副作用。
    """
    if codec_version not in contracts.CODEC_VERSIONS:
        raise ValueError('未知 outputCodecVersion：%r' % (codec_version,))
    profile = profile if isinstance(profile, dict) else {}
    normalized = _normalize_targets(targets)
    fingerprint = contracts.plan_fingerprint(fingerprint_parts)
    doc = {
        'schemaVersion': contracts.CHECKPOINT_SCHEMA_VERSION,
        'adaptive': True,
        'batchId': bound_id(batch_id),
        'scopeRevision': _require_int(scope_revision, 'scopeRevision', minimum=0),
        'materialRevision': _require_int(material_revision, 'materialRevision', minimum=0),
        'planId': 'p-' + fingerprint[:12],
        'planEpoch': _require_int(plan_epoch, 'planEpoch', minimum=1),
        'fingerprint': fingerprint,
        'plannerVersion': contracts.PLANNER_VERSION,
        'promptVersion': contracts.PLAN_PROMPT_VERSION,
        'outputCodecVersion': codec_version,
        'selectedFactsDigest': contracts.plan_fingerprint(
            sorted(contracts.target_digest(target) for target in normalized.values())),
        'providerFingerprint': {
            'providerId': bound_id(profile.get('providerId')),
            'model': bound_id(profile.get('model')),
            'limitsSource': str(profile.get('limitsSource') or 'configured'),
        },
        'budgetProfile': _bounded_budget_profile(profile),
        'calibrationSnapshot': {},
        'activeElapsedMs': 0,
        'targets': normalized,
        'pendingTargetIds': [],
        'jobs': {},
        'attempts': {},
        'coverage': {'targetTotal': 0, 'targetCompleted': 0, 'targetPending': 0},
        'usageAggregate': contracts.usage_aggregate([]),
        'blocking': None,
        'log': [],
        'notes': [],
    }
    _recompute_coverage(doc)
    return doc


# --- 覆盖 / 用量重算（结构事件后统一走这里，防漂移）-----------------------------------


def _owned_target_ids(jobs):
    """非 superseded 作业认领的全部目标 id（superseded 作业释放其目标）。"""
    owned = set()
    for job in jobs.values():
        if str(job.get('state') or '') == contracts.JOB_SUPERSEDED:
            continue
        for target_id in job.get('orderedPrimaryTargetIds') or []:
            owned.add(str(target_id))
    return owned


def _derived_pending_ids(doc):
    targets = doc.get('targets') if isinstance(doc.get('targets'), dict) else {}
    owned = _owned_target_ids(doc.get('jobs') if isinstance(doc.get('jobs'), dict) else {})
    return [target_id for target_id in targets if target_id not in owned]


def _recompute_coverage(doc):
    targets = doc.get('targets') if isinstance(doc.get('targets'), dict) else {}
    jobs = doc.get('jobs') if isinstance(doc.get('jobs'), dict) else {}
    doc['pendingTargetIds'] = _derived_pending_ids(doc)
    completed = set()
    for job in jobs.values():
        if str(job.get('state') or '') != contracts.JOB_SUCCEEDED:
            continue
        for target_id in job.get('orderedPrimaryTargetIds') or []:
            completed.add(str(target_id))
    total = len(targets)
    doc['coverage'] = {'targetTotal': total, 'targetCompleted': len(completed),
                       'targetPending': total - len(completed)}


def _settled_attempt_usages(doc):
    """已收口尝试的 usage（转为 usage_aggregate 的输入口径）。"""
    attempts = doc.get('attempts') if isinstance(doc.get('attempts'), dict) else {}
    return [_raw_usage(attempt.get('usage')) for attempt in attempts.values()
            if str(attempt.get('state') or '') != contracts.ATTEMPT_STARTED]


def _raw_usage(usage):
    """normalize_usage 形状 → usage_aggregate 输入口径。

    契约的 usage_aggregate 只识别 snake_case 原始键（对 normalize_usage 输出不幂等，
    已上报协议变更请求）；此处适配输入以复用同一聚合实现，不复制聚合逻辑。
    """
    usage = usage if isinstance(usage, dict) else {}
    return {'prompt_tokens': usage.get('promptTokens'),
            'completion_tokens': usage.get('completionTokens'),
            'reasoning_tokens': usage.get('reasoningTokens'),
            'total_tokens': usage.get('totalTokens')}


def _recompute_usage(doc):
    """usageAggregate 复用 contracts.usage_aggregate 按已收口尝试重算（unknown 不计 0）。"""
    doc['usageAggregate'] = contracts.usage_aggregate(_settled_attempt_usages(doc))


def _append_log(doc, line):
    doc.setdefault('log', []).append(bound_error(line))


# --- 事件应用（深拷贝后变更；非法迁移 ValueError）-------------------------------------


def _get_job(doc, event, key='jobId'):
    job_id = _require_id(event, key)
    job = (doc.get('jobs') or {}).get(job_id)
    if job is None:
        raise ValueError('作业不存在：%s' % job_id)
    return job_id, job


def _get_attempt(doc, event):
    attempt_id = _require_id(event, 'attemptId')
    attempt = (doc.get('attempts') or {}).get(attempt_id)
    if attempt is None:
        raise ValueError('尝试不存在：%s' % attempt_id)
    return attempt_id, attempt


def _require_transition(job_id, job, new_state):
    state = str(job.get('state') or '')
    allowed = contracts.JOB_TRANSITIONS.get(state, ())
    if new_state not in allowed:
        raise ValueError('非法作业迁移 %s：%s → %s（允许：%s）'
                         % (job_id, state or '<空>', new_state, list(allowed)))


def _require_no_inflight(doc, job_id, job):
    """running → 终态前，该作业不得仍有 started 在途尝试（先收口再终态）。"""
    attempts = doc.get('attempts') or {}
    for attempt_id in job.get('attemptIds') or []:
        attempt = attempts.get(attempt_id) or {}
        if str(attempt.get('state') or '') == contracts.ATTEMPT_STARTED:
            raise ValueError('作业 %s 仍有在途尝试 %s 未收口，不能进入终态' % (job_id, attempt_id))


def _normalize_target_ids(doc, raw, field):
    """非空、无重复、全部存在于 targets 的目标 id 列表。"""
    if not isinstance(raw, list) or not raw:
        raise ValueError('字段 %s 必须是非空目标 id 列表' % field)
    targets = doc.get('targets') or {}
    seen = []
    for item in raw:
        target_id = str(item or '').strip()
        if not target_id:
            raise ValueError('字段 %s 含空目标 id' % field)
        if target_id not in targets:
            raise ValueError('字段 %s 引用不存在目标：%s' % (field, target_id))
        if target_id in seen:
            raise ValueError('字段 %s 目标重复：%s' % (field, target_id))
        seen.append(target_id)
    return seen


def _job_depth(doc, job):
    """沿 parentId 链到根的深度（根作业为 0；防环，超上限提前退出）。"""
    jobs = doc.get('jobs') if isinstance(doc.get('jobs'), dict) else {}
    depth = 0
    current = job
    seen = set()
    while depth <= contracts.MAX_SPLIT_DEPTH:
        parent_id = str((current or {}).get('parentId') or '')
        if not parent_id or parent_id in seen:
            break
        parent = jobs.get(parent_id)
        if parent is None:
            break
        seen.add(parent_id)
        depth += 1
        current = parent
    return depth


def _new_job_record(doc, definition, job_id, parent_id, root_id, split_path):
    context_ids = definition.get('contextFactIds') or []
    if not isinstance(context_ids, list):
        raise ValueError('contextFactIds 必须是列表')
    estimate = definition.get('estimate')
    return {
        'parentId': bound_id(parent_id),
        'rootId': bound_id(root_id),
        'orderedPrimaryTargetIds': _normalize_target_ids(
            doc, definition.get('orderedPrimaryTargetIds'), 'orderedPrimaryTargetIds'),
        'contextFactIds': [bound_id(item) for item in context_ids],
        'state': contracts.JOB_QUEUED,
        'children': [],
        'attemptIds': [],
        'estimate': copy.deepcopy(estimate) if isinstance(estimate, dict) else None,
        'candidateCount': None,
        'resultDigest': None,
        'errorCode': None,
        'splitPath': bound_id(split_path),
    }


def _event_job_created(doc, event):
    jobs = doc['jobs']
    job_id = bound_id(_require_id(event, 'jobId'))
    if job_id in jobs:
        raise ValueError('作业已存在：%s' % job_id)
    primaries = event.get('orderedPrimaryTargetIds')
    if isinstance(primaries, list) and len(primaries) > contracts.MAX_PRIMARY_TARGETS_PER_JOB:
        raise ValueError('作业 %s 主目标 %d 个超过单作业上限 %d'
                         % (job_id, len(primaries), contracts.MAX_PRIMARY_TARGETS_PER_JOB))
    parent_id = str(event.get('parentId') or '').strip()
    if parent_id:
        parent = jobs.get(parent_id)
        if parent is None:
            raise ValueError('父作业不存在：%s' % parent_id)
        if _job_depth(doc, parent) + 1 > contracts.MAX_SPLIT_DEPTH:
            raise ValueError('%s：作业嵌套深度超过上限 %d'
                             % (contracts.SPLIT_DEPTH_EXCEEDED, contracts.MAX_SPLIT_DEPTH))
    root_id = str(event.get('rootId') or parent_id or job_id).strip()
    if root_id != job_id and root_id not in jobs:
        raise ValueError('根作业不存在：%s' % root_id)
    # 拆分登记制（D04/_plan_more 路径）：装箱不可行的 pending 目标先按字段拆分——
    # 子作业通过 job_created 携带 primaryTargets（新 selector 子目标）与 expandedParentIds
    # （被展开的父目标）；同事件内登记子目标、标父 expandedInto、从 pending 移除父目标。
    raw_targets = event.get('primaryTargets')
    new_ids = []
    if isinstance(raw_targets, list):
        for item in raw_targets:
            _register_split_target(doc, item, new_ids)
    for parent_tid in event.get('expandedParentIds') or []:
        parent_tid = str(parent_tid)
        target = doc.get('targets', {}).get(parent_tid)
        if target is None:
            raise ValueError('expandedParentIds 引用不存在目标：%s' % parent_tid)
        marked = [tid for tid in new_ids if tid not in (target.get('expandedInto') or [])]
        if marked:
            target['expandedInto'] = list(target.get('expandedInto') or []) + marked
        doc['pendingTargetIds'] = [tid for tid in (doc.get('pendingTargetIds') or [])
                                   if tid != parent_tid]
    doc['jobs'][job_id] = _new_job_record(doc, event, job_id, parent_id, root_id,
                                          str(event.get('splitPath') or 'root'))
    _recompute_coverage(doc)


def _event_job_claimed(doc, event):
    job_id, job = _get_job(doc, event)
    _require_id(event, 'attemptId')   # 认领即确定本次尝试 id（记录由 attempt_started 落账）
    _require_transition(job_id, job, contracts.JOB_RUNNING)
    job['state'] = contracts.JOB_RUNNING


def _event_attempt_started(doc, event):
    attempts = doc['attempts']
    attempt_id = bound_id(_require_id(event, 'attemptId'))
    if attempt_id in attempts:
        raise ValueError('尝试 id 已存在（每次物理调用唯一）：%s' % attempt_id)
    job_id, job = _get_job(doc, event)
    if str(job.get('state') or '') != contracts.JOB_RUNNING:
        raise ValueError('作业 %s 状态为 %s，须先 job_claimed 进入 running 才能开始尝试'
                         % (job_id, job.get('state')))
    attempts[attempt_id] = {
        'jobId': job_id,
        'sequence': _require_int(event.get('sequence'), 'sequence', minimum=0),
        'runAttempt': _require_int(event.get('runAttempt'), 'runAttempt', minimum=0),
        'requestFingerprint': bound_id(event.get('requestFingerprint')),
        'requestedMaxTokens': _require_int(event.get('requestedMaxTokens'),
                                           'requestedMaxTokens', minimum=1),
        'state': contracts.ATTEMPT_STARTED,
        'finishReason': None,
        'usage': contracts.empty_usage(),
        'bytes': None,
        'durationMs': None,
    }
    job.setdefault('attemptIds', []).append(attempt_id)


def _require_attempt_state(attempt_id, attempt, expected):
    state = str(attempt.get('state') or '')
    if state != expected:
        raise ValueError('非法尝试迁移 %s：%s（当前 %s）' % (attempt_id, expected, state or '<空>'))


def _event_attempt_succeeded(doc, event):
    attempt_id, attempt = _get_attempt(doc, event)
    _require_attempt_state(attempt_id, attempt, contracts.ATTEMPT_STARTED)
    attempt['state'] = contracts.ATTEMPT_SUCCEEDED
    attempt['finishReason'] = str(event['finishReason']) if event.get('finishReason') else None
    attempt['usage'] = contracts.normalize_usage(event.get('usage'))
    attempt['bytes'] = _optional_int(event.get('bytes'))
    attempt['durationMs'] = _optional_int(event.get('durationMs'))
    _recompute_usage(doc)


def _event_attempt_failed(doc, event):
    attempt_id, attempt = _get_attempt(doc, event)
    _require_attempt_state(attempt_id, attempt, contracts.ATTEMPT_STARTED)
    attempt['state'] = contracts.ATTEMPT_FAILED
    attempt['errorCode'] = bound_id(_require_id(event, 'errorCode'))
    attempt['errorMessage'] = bound_error(event.get('message'))
    attempt['finishReason'] = str(event['finishReason']) if event.get('finishReason') else None
    attempt['usage'] = contracts.normalize_usage(event.get('usage'))
    attempt['bytes'] = _optional_int(event.get('bytes'))
    attempt['durationMs'] = _optional_int(event.get('durationMs'))
    _recompute_usage(doc)


def _event_attempt_unknown(doc, event):
    """崩溃恢复：started → interrupted_unknown（真实计费可能发生，显示未知，不计 0）。"""
    attempt_id, attempt = _get_attempt(doc, event)
    _require_attempt_state(attempt_id, attempt, contracts.ATTEMPT_STARTED)
    attempt['state'] = contracts.ATTEMPT_INTERRUPTED_UNKNOWN
    _recompute_usage(doc)


def _event_job_succeeded(doc, event):
    job_id, job = _get_job(doc, event)
    _require_transition(job_id, job, contracts.JOB_SUCCEEDED)
    _require_no_inflight(doc, job_id, job)
    job['state'] = contracts.JOB_SUCCEEDED
    job['candidateCount'] = _require_int(event.get('candidateCount'), 'candidateCount',
                                         minimum=0)
    job['resultDigest'] = bound_id(event.get('resultDigest'))
    _recompute_coverage(doc)


def _event_job_failed(doc, event):
    job_id, job = _get_job(doc, event)
    _require_transition(job_id, job, contracts.JOB_FAILED)
    _require_no_inflight(doc, job_id, job)
    job['state'] = contracts.JOB_FAILED
    job['errorCode'] = bound_id(_require_id(event, 'errorCode'))
    _append_log(doc, '作业 %s 失败（%s）：%s' % (job_id, job['errorCode'],
                                               bound_error(event.get('message'))))


def _event_job_requeued(doc, event):
    """显式 resume 重排队：failed → queued（D00 裁定，仅 run-resume 新 runAttempt 使用）。

    blocked/superseded/succeeded 不动（受阻须新建计划，成功叶不重做）；
    errorCode 保留为上次失败原因供界面追溯，重排队不改写 attempt 历史；
    job 级尝试预算由执行器按「当前 runAttempt 内计数」执行（§6 失败预算不重置指 plan 级）。
    """
    job_id, job = _get_job(doc, event)
    _require_transition(job_id, job, contracts.JOB_QUEUED)
    _require_no_inflight(doc, job_id, job)
    _require_int(event.get('runAttempt'), 'runAttempt', minimum=1)
    job['state'] = contracts.JOB_QUEUED
    _append_log(doc, '作业 %s 重试排队（resume 第 %d 次）'
                % (job_id, int(event['runAttempt'])))


def _event_job_blocked(doc, event):
    job_id, job = _get_job(doc, event)
    _require_transition(job_id, job, contracts.JOB_BLOCKED)
    _require_no_inflight(doc, job_id, job)
    job['state'] = contracts.JOB_BLOCKED
    job['errorCode'] = bound_id(_require_id(event, 'code'))
    _append_log(doc, '作业 %s 受阻（%s）：%s' % (job_id, job['errorCode'],
                                               bound_error(event.get('message'))))


def _register_split_target(doc, raw, new_ids):
    """selector 子目标登记（拆分登记制）：'t-' 前缀、同 id 同内容幂等、冲突 ValueError。

    登记形状与计划 targets 表一致（factId/selector/subjectKey/kind 有界化；
    materialId/groupingConfidence 属展示字段不入表）。selector 内容一致性按契约
    唯一口径 canonical_selector 比对。新增 id 按子目标定义顺序记入 new_ids。
    """
    if not isinstance(raw, dict):
        raise ValueError('primaryTargets 项必须是 dict')
    targets = doc.get('targets')
    if not isinstance(targets, dict):
        raise ValueError('计划缺少 targets 表，无法登记拆分子目标')
    target_id = str(raw.get('targetId') or '').strip()
    fact_id = str(raw.get('factId') or '').strip()
    if not target_id or not fact_id:
        raise ValueError('primaryTargets 项缺少 targetId/factId')
    normalized = {
        'factId': bound_id(fact_id),
        'selector': copy.deepcopy(raw.get('selector')) if isinstance(raw.get('selector'), dict)
                    else {'type': 'whole'},
        'subjectKey': bound_id(raw.get('subjectKey')),
        'kind': bound_id(raw.get('kind')),
    }
    existing = targets.get(target_id)
    if existing is None:
        if not target_id.startswith('t-'):
            raise ValueError('拆分子目标 %s 必须以 t- 开头（登记制派生 id）' % target_id)
        targets[target_id] = normalized
        new_ids.append(target_id)
        return
    same = (str(existing.get('factId') or '') == normalized['factId']
            and contracts.canonical_selector(existing.get('selector'))
            == contracts.canonical_selector(normalized['selector'])
            and str(existing.get('subjectKey') or '') == normalized['subjectKey']
            and str(existing.get('kind') or '') == normalized['kind'])
    if not same:
        raise ValueError('拆分子目标 %s 与现有目标内容冲突（同 id 不同内容拒绝）' % target_id)


def _expanded_into_mapping(doc, parent_primaries, children, new_ids):
    """父主目标 → 本次新增子目标 id 列表（子目标定义顺序；factId+父选择器对应）。

    children 带 'splitSelectors'（父targetId→本子作业认领 selector 列表，D04 冻结
    映射）时按认领 selector 精确对应；未携带时按「同 factId 且 selector 不同」宽松
    对应（调用方应优先携带 splitSelectors）。只统计本次新增登记的 id（new_ids）。
    """
    targets = doc['targets']
    parent_set = set(parent_primaries)
    claims = {}
    for child in children:
        mapping = child.get('splitSelectors')
        if not isinstance(mapping, dict):
            continue
        for parent_id, selectors in mapping.items():
            parent_id = str(parent_id or '')
            if parent_id not in parent_set:
                continue
            bucket = claims.setdefault(parent_id, set())
            for selector in (selectors or []):
                bucket.add(contracts.canonical_selector(selector))
    result = {parent_id: [] for parent_id in parent_primaries}
    for target_id in new_ids:
        target = targets.get(target_id) or {}
        fact_id = str(target.get('factId') or '')
        selector_key = contracts.canonical_selector(target.get('selector'))
        for parent_id in parent_primaries:
            parent = targets.get(parent_id) or {}
            if str(parent.get('factId') or '') != fact_id:
                continue
            claimed = claims.get(parent_id)
            if claimed is not None:
                if selector_key in claimed:
                    result[parent_id].append(target_id)
            elif selector_key != contracts.canonical_selector(parent.get('selector')):
                result[parent_id].append(target_id)
    return result


def _verify_expanding_split(job_id, parent_primaries, covered, new_ids, mapping):
    """selector 拆分（有新增子目标）的守恒核验：不重不漏、父子一一衔接。

    * 子作业认领不重复、不超出（父主目标 ∪ 本次新增）；
    * 每个新增子目标恰对应一个父主目标（展开映射恰好一次，防止拆分外新增覆盖）；
    * 每个父主目标要么被展开（对应新增子目标非空）、要么被子作业直接认领，
      不得两者兼有（兼有 → 终态有效叶覆盖重算）。
    """
    if len(set(covered)) != len(covered):
        raise ValueError('拆分不守恒：子作业重复认领同一目标（父作业 %s）' % job_id)
    claim_set = set(covered)
    unclaimed = sorted(set(new_ids) - claim_set)
    if unclaimed:
        raise ValueError('拆分不守恒：新增子目标未被任何子作业认领：%s' % ', '.join(unclaimed))
    extra = sorted(claim_set - set(new_ids) - set(parent_primaries))
    if extra:
        raise ValueError('拆分不守恒：子作业认领了非本拆分范围的目标：%s' % ', '.join(extra))
    expansion_counts = {}
    for kids in mapping.values():
        for kid in kids:
            expansion_counts[kid] = expansion_counts.get(kid, 0) + 1
    for kid in new_ids:
        count = expansion_counts.get(kid, 0)
        if count != 1:
            raise ValueError('拆分不守恒：新增子目标 %s 对应 %d 个父主目标（应恰为 1）'
                             % (kid, count))
    for parent_id in parent_primaries:
        kids = mapping.get(parent_id) or []
        claimed = parent_id in claim_set
        if kids and claimed:
            raise ValueError('拆分不守恒：目标 %s 同时被展开与被直接认领（父作业 %s）'
                             % (parent_id, job_id))
        if not kids and not claimed:
            raise ValueError('拆分不守恒：父主目标 %s 未被展开也未被子作业认领（父作业 %s）'
                             % (parent_id, job_id))


def _event_job_split(doc, event):
    """running|queued → split；子作业随同一事件入 jobs，目标不重不漏覆盖父目标。

    拆分登记制（2026-09-22 D00 裁定）：children 项可携带 'primaryTargets'（selector
    子目标定义，Target dict 列表）——先把这些 target 登记进 doc['targets']（_register_
    split_target），再校验子作业主目标存在性，并把父作业每个 primary target 标
    'expandedInto'（_expanded_into_mapping + _verify_expanding_split）。无
    'primaryTargets'（单元级拆分，子作业复用既有目标）行为与现状一致（不登记不标）。
    """
    job_id, job = _get_job(doc, event)
    _require_transition(job_id, job, contracts.JOB_SPLIT)
    _require_no_inflight(doc, job_id, job)
    children = event.get('children')
    if not isinstance(children, list) or not children:
        raise ValueError('job_split 事件缺少子作业定义（children 非空列表）')
    if _job_depth(doc, job) + 1 > contracts.MAX_SPLIT_DEPTH:
        raise ValueError('%s：拆分深度超过上限 %d'
                         % (contracts.SPLIT_DEPTH_EXCEEDED, contracts.MAX_SPLIT_DEPTH))
    parent_primaries = [str(item) for item in (job.get('orderedPrimaryTargetIds') or [])]
    prepared = []
    child_ids = set()
    new_ids = []
    for child in children:
        if not isinstance(child, dict):
            raise ValueError('子作业定义必须是 dict')
        child_id = bound_id(_require_id(child, 'jobId'))
        if child_id == job_id or child_id in doc['jobs'] or child_id in child_ids:
            raise ValueError('子作业 id 冲突：%s' % child_id)
        declared_parent = str(child.get('parentId') or '').strip()
        if declared_parent and declared_parent != job_id:
            raise ValueError('子作业 %s 的 parentId 必须是父作业 %s' % (child_id, job_id))
        raw_targets = child.get('primaryTargets')
        if isinstance(raw_targets, list):
            for item in raw_targets:
                _register_split_target(doc, item, new_ids)
        child_ids.add(child_id)
        prepared.append((child_id, child))
    covered = []
    with_primaries = []
    for child_id, child in prepared:
        primaries = _normalize_target_ids(
            doc, child.get('orderedPrimaryTargetIds'), '子作业 %s 主目标' % child_id)
        if len(primaries) > contracts.MAX_PRIMARY_TARGETS_PER_JOB:
            raise ValueError('子作业 %s 主目标 %d 个超过单作业上限 %d'
                             % (child_id, len(primaries), contracts.MAX_PRIMARY_TARGETS_PER_JOB))
        covered.extend(primaries)
        with_primaries.append((child_id, child, primaries))
    if new_ids:
        # selector 拆分：父目标标展开映射，守恒按「有效叶」核验（父目标可被子目标顶替）
        mapping = _expanded_into_mapping(doc, parent_primaries, children, new_ids)
        _verify_expanding_split(job_id, parent_primaries, covered, new_ids, mapping)
        for parent_id, kids in mapping.items():
            doc['targets'][parent_id]['expandedInto'] = list(kids)
    elif sorted(covered) != sorted(parent_primaries):
        # 覆盖守恒（契约 §5）：子目标集合 == 父目标集合，不重不漏；context 允许重叠不在此列。
        raise ValueError('拆分不守恒：子作业目标并集与父作业 %s 主目标不一致（重复或缺失）' % job_id)
    parent_root = str(job.get('rootId') or job_id)
    parent_split_path = str(job.get('splitPath') or 'root')
    created = []
    for index, (child_id, child, primaries) in enumerate(with_primaries):
        definition = dict(child)
        definition['orderedPrimaryTargetIds'] = primaries
        record = _new_job_record(doc, definition, child_id, job_id, parent_root,
                                 str(child.get('splitPath')
                                     or '%s/%d' % (parent_split_path, index)))
        doc['jobs'][child_id] = record
        created.append(child_id)
    job['state'] = contracts.JOB_SPLIT
    job['children'] = created
    _recompute_coverage(doc)


def _event_job_superseded(doc, event):
    """queued → superseded（§4.3 重打包：同事务建新作业，旧作业不可再派发）。"""
    job_id, job = _get_job(doc, event)
    _require_transition(job_id, job, contracts.JOB_SUPERSEDED)
    replaced_by_raw = event.get('replacedBy')
    replaced_by = []
    if replaced_by_raw is not None:
        if not isinstance(replaced_by_raw, list):
            raise ValueError('replacedBy 必须是新作业 id 列表')
        for item in replaced_by_raw:
            replacement_id = str(item or '').strip()
            if not replacement_id:
                raise ValueError('replacedBy 含空作业 id')
            if replacement_id not in doc['jobs']:
                raise ValueError('replacedBy 引用不存在作业：%s' % replacement_id)
            replaced_by.append(bound_id(replacement_id))
    job['state'] = contracts.JOB_SUPERSEDED
    job['supersededBy'] = replaced_by
    mapping = event.get('targetMapping')
    mapping_note = ''
    if isinstance(mapping, dict) and mapping:
        mapping_note = '，重打包映射 %d 项' % len(mapping)
    _append_log(doc, '作业 %s 已被 superseded（替代：%s%s）'
                % (job_id, ' ,'.join(replaced_by) if replaced_by else '无', mapping_note))
    _recompute_coverage(doc)


def _event_blocking_set(doc, event):
    code = bound_id(_require_id(event, 'code'))
    message = bound_error(event.get('message'))
    doc['blocking'] = {'code': code, 'message': message}
    _append_log(doc, '运行受阻（%s）：%s' % (code, message))


def _event_elapsed_update(doc, event):
    raw = event.get('ms', event.get('deltaMs'))
    if not _is_number(raw) or raw < 0:
        raise ValueError('elapsed_update 需要非负毫秒数，实际 %r' % (raw,))
    doc['activeElapsedMs'] = int(doc.get('activeElapsedMs') or 0) + int(raw)


_EVENT_HANDLERS = {
    EVENT_JOB_CREATED: _event_job_created,
    EVENT_JOB_CLAIMED: _event_job_claimed,
    EVENT_ATTEMPT_STARTED: _event_attempt_started,
    EVENT_ATTEMPT_SUCCEEDED: _event_attempt_succeeded,
    EVENT_ATTEMPT_FAILED: _event_attempt_failed,
    EVENT_ATTEMPT_UNKNOWN: _event_attempt_unknown,
    EVENT_JOB_SUCCEEDED: _event_job_succeeded,
    EVENT_JOB_FAILED: _event_job_failed,
    EVENT_JOB_REQUEUED: _event_job_requeued,
    EVENT_JOB_BLOCKED: _event_job_blocked,
    EVENT_JOB_SPLIT: _event_job_split,
    EVENT_JOB_SUPERSEDED: _event_job_superseded,
    EVENT_BLOCKING_SET: _event_blocking_set,
    EVENT_ELAPSED_UPDATE: _event_elapsed_update,
}


def apply_event(doc, event):
    """深拷贝 doc 后应用一个事件并返回新 doc；输入不可变。

    非法迁移 / 结构缺失 / 未知事件类型一律抛 ValueError（绝不假成功）。
    作业迁移严格按 contracts.JOB_TRANSITIONS 校验。
    """
    if not isinstance(doc, dict):
        raise ValueError('计划 doc 必须是 dict')
    if not isinstance(event, dict):
        raise ValueError('事件必须是 dict')
    event_type = str(event.get('type') or '')
    handler = _EVENT_HANDLERS.get(event_type)
    if handler is None:
        raise ValueError('未知事件类型：%r' % (event.get('type'),))
    new_doc = copy.deepcopy(doc)
    handler(new_doc, event)
    return new_doc


# --- 查询 / 校验 ---------------------------------------------------------------------


def effective_leaf_jobs(doc):
    """叶作业 id 列表（无 children 的作业，含 queued/running/终态；父 split 不算叶）。"""
    jobs = doc.get('jobs') if isinstance(doc, dict) and isinstance(doc.get('jobs'), dict) else {}
    return [job_id for job_id, job in jobs.items() if not (job.get('children') or [])]


def effective_leaf_target_ids(doc):
    """有效叶目标 id 列表（expandedInto 非空的目标不算叶；D09/D16 复用口径）。

    与 contracts.final_state_check 的「有效叶目标集」同口径：拆分登记制下父目标
    expandedInto 非空 → 由其子目标顶替，父目标自身不再参与叶覆盖比对。
    """
    if not isinstance(doc, dict):
        return []
    targets = doc.get('targets') if isinstance(doc.get('targets'), dict) else {}
    return [target_id for target_id, target in targets.items()
            if not (target.get('expandedInto') if isinstance(target, dict) else None)]


def completed_target_digests(doc, targets=None):
    """已 succeeded 叶作业覆盖目标的 digest 列表（复用 contracts.target_digest）。

    拆分登记制下 expandedInto 目标若直接被成功叶作业覆盖属异常形态（validate_plan_doc
    EXPANDED_LEAF_PRIMARY 拦截）；本函数按冻结口径只算成功叶作业主目标 digest，不改。
    """
    if not isinstance(doc, dict):
        return []
    if not isinstance(targets, dict):
        targets = doc.get('targets') if isinstance(doc.get('targets'), dict) else {}
    jobs = doc.get('jobs') if isinstance(doc.get('jobs'), dict) else {}
    digests = []
    for job_id, job in jobs.items():
        if str(job.get('state') or '') != contracts.JOB_SUCCEEDED:
            continue
        if job.get('children'):
            continue
        for target_id in job.get('orderedPrimaryTargetIds') or []:
            target = targets.get(str(target_id))
            if target is None:
                raise ValueError('成功作业 %s 引用不存在目标：%s' % (job_id, target_id))
            digests.append(contracts.target_digest(target))
    return digests


def final_check(doc):
    """终态检查：复用 contracts.final_state_check，叠加「无 started 在途尝试残留」。

    interrupted_unknown 不算残留（已收口为未知）；started 残留说明有在途调用未收口。
    返回 {'ok': bool, 'violations': [{'code','message'}]}。
    """
    violations = list(contracts.final_state_check({'generate': doc}).get('violations') or [])
    attempts = doc.get('attempts') if isinstance(doc, dict) and isinstance(doc.get('attempts'),
                                                                          dict) else {}
    for attempt_id, attempt in attempts.items():
        if str(attempt.get('state') or '') == contracts.ATTEMPT_STARTED:
            violations.append({'code': 'INFLIGHT_ATTEMPT',
                               'message': '尝试 %s 仍在 started（在途未收口）' % attempt_id})
    return {'ok': not violations, 'violations': violations}


# --- 指纹 ---------------------------------------------------------------------------


def plan_fingerprint_for(parts):
    """计划指纹（复用 contracts.plan_fingerprint；parts 为调用方按冻结顺序组装的有序列表）。"""
    return contracts.plan_fingerprint(parts)


def fingerprint_mismatch(stored, parts):
    """auto 恢复时的指纹比对：不一致（含 stored 缺失）→ True（422 BUDGET_PLAN_MISMATCH 依据）。"""
    stored = str(stored or '')
    return (not stored) or stored != contracts.plan_fingerprint(parts)


# --- 容量守卫 ------------------------------------------------------------------------


def checkpoint_soft_limit(inflight_calls):
    """软阈值 = 1MiB − 16KiB 终态保留 − 8KiB×(在途调用数+1)（复用契约口径）。"""
    return contracts.checkpoint_soft_limit_bytes(inflight_calls)


def checkpoint_capacity_ok(checkpoint, inflight_calls):
    """checkpoint 容量守卫（D07 / D08 持久化前共用）：序列化超软阈值 → False。

    调用方据此停止派发并记 CHECKPOINT_BUDGET_EXCEEDED（在途响应仍可原子保存候选与
    安全 usage）；本函数只测量，不截数据。checkpoint 为完整 checkpoint dict（含 generate）。
    """
    return contracts.checkpoint_fits(checkpoint, inflight_calls)


def can_dispatch(doc, limits=None):
    """派发前守卫：→ (ok, reason)。

    检查：尝试总数 < maxPlanAttempts（默认 1024）、累计 activeElapsedMs < 墙钟上限
    （默认 3600×1000ms，重启沿已保存耗时继续）、无 run 级 blocking。
    limits 可覆盖 {'maxPlanAttempts': int, 'maxWallMs': int}。
    """
    limits = limits if isinstance(limits, dict) else {}
    blocking = doc.get('blocking') if isinstance(doc, dict) else None
    if blocking:
        return False, str((blocking.get('code') if isinstance(blocking, dict) else '')
                          or 'BLOCKED')
    attempts = doc.get('attempts') if isinstance(doc, dict) and isinstance(doc.get('attempts'),
                                                                          dict) else {}
    max_attempts = limits.get('maxPlanAttempts')
    max_attempts = contracts.MAX_PLAN_ATTEMPTS if max_attempts is None else _as_int(max_attempts)
    if max_attempts is not None and len(attempts) >= max_attempts:
        return False, contracts.ATTEMPT_BUDGET_EXCEEDED
    active_ms = _as_int(doc.get('activeElapsedMs')) or 0
    max_wall_ms = limits.get('maxWallMs')
    max_wall_ms = contracts.MAX_PLAN_WALL_SECONDS * 1000 if max_wall_ms is None \
        else _as_int(max_wall_ms)
    if max_wall_ms is not None and active_ms >= max_wall_ms:
        return False, contracts.WALL_TIME_BUDGET_EXCEEDED
    return True, None


# --- 校准快照有界化 -------------------------------------------------------------------


def calibration_snapshot_bounds(buckets):
    """校准桶 → 有界快照：每桶只留 count / max / 最多 CALIBRATION_MAX_SAMPLES 个采样值。

    count/max 缺失时由采样推导；samples 保留最近的若干个（校准以近期观测为准）。
    非法输入一律归零/忽略，绝不抛异常（摘要规范化不改业务数据）。
    """
    bounded = {}
    for key, bucket in (buckets or {}).items():
        bucket = bucket if isinstance(bucket, dict) else {}
        raw_samples = bucket.get('samples')
        samples = [item for item in (raw_samples if isinstance(raw_samples, list) else [])
                   if _is_number(item)]
        count = _as_int(bucket.get('count'))
        count = len(samples) if count is None else count
        maximum = bucket.get('max')
        maximum = max(samples) if (not _is_number(maximum) and samples) else maximum
        bounded[str(key)] = {
            'count': count,
            'max': maximum,
            'samples': samples[-contracts.CALIBRATION_MAX_SAMPLES:],
        }
    return bounded


# --- 结构校验（validate_plan_doc）-----------------------------------------------------

_MAX_VALIDATE_ERRORS = 200   # 有界输出：损坏严重时也不无限膨胀错误列表


def _add_error(errors, code, message):
    if len(errors) < _MAX_VALIDATE_ERRORS:
        errors.append({'code': code, 'message': message})


def _is_ancestor(jobs, ancestor_id, job_id):
    current = job_id
    seen = set()
    while True:
        current = str(((jobs.get(current) or {}).get('parentId')) or '')
        if not current or current in seen:
            return False
        if current == ancestor_id:
            return True
        seen.add(current)


def _owners_form_one_family(jobs, owner_ids):
    """同一目标的多个认领作业必须全部位于同一父子链上（split 树内允许共享）。"""
    first = owner_ids[0]
    return all(_is_ancestor(jobs, first, other) or _is_ancestor(jobs, other, first)
               for other in owner_ids[1:])


def validate_plan_doc(doc):
    """计划结构校验 → [{'code','message'}]；空列表 = 结构完好。

    覆盖：schemaVersion=2（未知版本拒绝恢复）、状态枚举、悬空 parentId/rootId/子作业/
    尝试引用、pendingTargetIds 与 targets/作业认领一致、拆分父子一致、终态作业无在途、
    目标认领冲突、深度超限、expandedInto 拆分登记（悬空引用/展开链/成功叶直接覆盖
    已展开目标）、coverage/usageAggregate 与重算一致、blocking 形状。
    """
    errors = []
    if not isinstance(doc, dict):
        return [{'code': 'PLAN_INVALID', 'message': '计划不是 dict'}]
    if _as_int(doc.get('schemaVersion')) != contracts.CHECKPOINT_SCHEMA_VERSION:
        return [{'code': contracts.UNKNOWN_CHECKPOINT_SCHEMA,
                 'message': '缺少 schemaVersion=2 计划（实际 %r）' % (doc.get('schemaVersion'),)}]
    targets = doc.get('targets')
    if not isinstance(targets, dict) or not targets:
        return [{'code': 'TARGETS_INVALID', 'message': 'targets 缺失或为空'}]
    for target_id, target in targets.items():
        if not isinstance(target, dict) or not str(target.get('factId') or '').strip():
            _add_error(errors, 'TARGET_INVALID', '目标 %s 缺少 factId' % target_id)
    jobs = doc.get('jobs')
    if not isinstance(jobs, dict):
        _add_error(errors, 'JOBS_INVALID', 'jobs 缺失或不是 dict')
        jobs = {}
    attempts = doc.get('attempts')
    if not isinstance(attempts, dict):
        _add_error(errors, 'ATTEMPTS_INVALID', 'attempts 缺失或不是 dict')
        attempts = {}

    # pending 与 targets / 作业认领一致
    pending = doc.get('pendingTargetIds')
    if not isinstance(pending, list):
        _add_error(errors, 'PENDING_INVALID', 'pendingTargetIds 缺失或不是列表')
        pending = []
    seen_pending = set()
    for target_id in pending:
        target_id = str(target_id or '')
        if target_id not in targets:
            _add_error(errors, 'PENDING_DANGLING', 'pending 引用不存在目标：%s' % target_id)
        if target_id in seen_pending:
            _add_error(errors, 'PENDING_DUPLICATE', 'pending 目标重复：%s' % target_id)
        seen_pending.add(target_id)
    derived_pending = set(_derived_pending_ids(doc))
    if seen_pending != derived_pending:
        _add_error(errors, 'PENDING_MISMATCH',
                   'pendingTargetIds 与作业认领状态不一致（存 %d / 应 %d）'
                   % (len(seen_pending), len(derived_pending)))

    # 逐作业
    owners = {}
    for job_id, job in jobs.items():
        if not str(job_id or '').strip():
            _add_error(errors, 'JOB_ID_INVALID', '存在空作业 id')
            continue
        job = job if isinstance(job, dict) else {}
        state = str(job.get('state') or '')
        if state not in contracts.JOB_STATES:
            _add_error(errors, 'JOB_STATE_INVALID',
                       '作业 %s 状态不在枚举内：%r' % (job_id, job.get('state')))
        primaries = job.get('orderedPrimaryTargetIds')
        if not isinstance(primaries, list) or not primaries:
            _add_error(errors, 'JOB_TARGETS_INVALID', '作业 %s 主目标缺失或为空' % job_id)
            primaries = []
        seen_targets = set()
        for target_id in primaries:
            target_id = str(target_id or '')
            if target_id not in targets:
                _add_error(errors, 'JOB_TARGET_DANGLING',
                           '作业 %s 引用不存在目标：%s' % (job_id, target_id))
            if target_id in seen_targets:
                _add_error(errors, 'JOB_TARGET_DUPLICATE',
                           '作业 %s 主目标重复：%s' % (job_id, target_id))
            seen_targets.add(target_id)
            if state != contracts.JOB_SUPERSEDED:
                # superseded 作业已释放目标（§4.3），不参与认领冲突判定
                owners.setdefault(target_id, []).append(str(job_id))
        if len(primaries) > contracts.MAX_PRIMARY_TARGETS_PER_JOB:
            _add_error(errors, 'JOB_PRIMARY_OVER_LIMIT',
                       '作业 %s 主目标 %d 超过单作业上限 %d'
                       % (job_id, len(primaries), contracts.MAX_PRIMARY_TARGETS_PER_JOB))
        parent_id = str(job.get('parentId') or '')
        if parent_id and parent_id not in jobs:
            _add_error(errors, 'JOB_PARENT_DANGLING',
                       '作业 %s 的父作业不存在：%s' % (job_id, parent_id))
        root_id = str(job.get('rootId') or '')
        if root_id and root_id != job_id and root_id not in jobs:
            _add_error(errors, 'JOB_ROOT_DANGLING',
                       '作业 %s 的根作业不存在：%s' % (job_id, root_id))
        children = job.get('children') or []
        if not isinstance(children, list):
            _add_error(errors, 'JOB_CHILDREN_INVALID', '作业 %s children 不是列表' % job_id)
            children = []
        for child_id in children:
            child_id = str(child_id or '')
            if child_id not in jobs:
                _add_error(errors, 'JOB_CHILD_DANGLING',
                           '作业 %s 的子作业不存在：%s' % (job_id, child_id))
            elif str((jobs.get(child_id) or {}).get('parentId') or '') != str(job_id):
                _add_error(errors, 'JOB_PARENT_CHILD_MISMATCH',
                           '子作业 %s 的 parentId 与父作业 %s 不一致' % (child_id, job_id))
        if children and state != contracts.JOB_SPLIT:
            _add_error(errors, 'PARENT_NOT_SPLIT',
                       '作业 %s 状态 %s 但存在子作业' % (job_id, state))
        if state == contracts.JOB_SPLIT and not children:
            _add_error(errors, 'SPLIT_WITHOUT_CHILDREN', '作业 %s 状态 split 但无子作业' % job_id)
        attempt_ids = job.get('attemptIds') or []
        if not isinstance(attempt_ids, list):
            _add_error(errors, 'JOB_ATTEMPTS_INVALID', '作业 %s attemptIds 不是列表' % job_id)
            attempt_ids = []
        seen_attempt_ids = set()
        for attempt_id in attempt_ids:
            attempt_id = str(attempt_id or '')
            if attempt_id not in attempts:
                _add_error(errors, 'JOB_ATTEMPT_DANGLING',
                           '作业 %s 引用不存在的尝试：%s' % (job_id, attempt_id))
            elif str((attempts.get(attempt_id) or {}).get('jobId') or '') != str(job_id):
                _add_error(errors, 'ATTEMPT_JOB_MISMATCH',
                           '尝试 %s 的 jobId 与作业 %s 不一致' % (attempt_id, job_id))
            if attempt_id in seen_attempt_ids:
                _add_error(errors, 'JOB_ATTEMPT_DUPLICATE',
                           '作业 %s 尝试重复登记：%s' % (job_id, attempt_id))
            seen_attempt_ids.add(attempt_id)
        if state == contracts.JOB_QUEUED and any(
                str((attempts.get(aid) or {}).get('state') or '') ==
                contracts.ATTEMPT_STARTED for aid in attempt_ids):
            # 重排队（显式 resume）的作业保留历史尝试（failed/interrupted_unknown）；
            # queued 且仍有 started 在途尝试才是真损坏。
            _add_error(errors, 'QUEUED_WITH_INFLIGHT_ATTEMPT',
                       '作业 %s 尚在 queued 却有 started 在途尝试' % job_id)
        if state in _TERMINAL_JOB_STATES:
            for attempt_id in attempt_ids:
                attempt = attempts.get(str(attempt_id)) or {}
                if str(attempt.get('state') or '') == contracts.ATTEMPT_STARTED:
                    _add_error(errors, 'TERMINAL_WITH_INFLIGHT',
                               '终态作业 %s 仍有在途尝试 %s' % (job_id, attempt_id))
        if _job_depth(doc, job) > contracts.MAX_SPLIT_DEPTH:
            _add_error(errors, 'JOB_DEPTH_EXCEEDED',
                       '作业 %s 嵌套深度超过上限 %d' % (job_id, contracts.MAX_SPLIT_DEPTH))

    # 同一目标的多个认领作业必须在同一父子链上（split 之外不重）
    for target_id, owner_ids in owners.items():
        if len(owner_ids) > 1 and not _owners_form_one_family(jobs, owner_ids):
            _add_error(errors, 'TARGET_OWNER_CONFLICT',
                       '目标 %s 被无父子关系的多个作业认领：%s' % (target_id, owner_ids))

    # 拆分登记制（2026-09-22 D00 裁定）：expandedInto 引用存在、无展开链、
    # 已展开目标不得同时是任何 succeeded 叶作业的 primary
    expanded = {}
    for target_id, target in targets.items():
        refs = target.get('expandedInto') if isinstance(target, dict) else None
        if refs is None:
            continue
        if not isinstance(refs, list):
            _add_error(errors, 'EXPANDED_INVALID',
                       '目标 %s 的 expandedInto 不是列表' % target_id)
            continue
        refs = [str(item or '') for item in refs]
        seen_refs = set()
        for ref in refs:
            if ref not in targets:
                _add_error(errors, 'EXPANDED_DANGLING',
                           '目标 %s 的 expandedInto 引用不存在目标：%s' % (target_id, ref))
            if ref in seen_refs:
                _add_error(errors, 'EXPANDED_DUPLICATE',
                           '目标 %s 的 expandedInto 重复引用：%s' % (target_id, ref))
            seen_refs.add(ref)
        if refs:
            expanded[str(target_id)] = refs
    for parent_id in sorted(expanded):
        for ref in expanded[parent_id]:
            if ref in expanded:
                _add_error(errors, 'EXPANDED_CHAIN',
                           '展开链不允许嵌套：%s 展开为 %s，而后者自身也带 expandedInto'
                           % (parent_id, ref))
    for job_id, job in jobs.items():
        job = job if isinstance(job, dict) else {}
        if str(job.get('state') or '') != contracts.JOB_SUCCEEDED or (job.get('children') or []):
            continue
        for target_id in job.get('orderedPrimaryTargetIds') or []:
            target_id = str(target_id or '')
            if target_id in expanded:
                _add_error(errors, 'EXPANDED_LEAF_PRIMARY',
                           '已展开目标 %s 被成功叶作业 %s 直接覆盖' % (target_id, job_id))

    # 逐尝试
    for attempt_id, attempt in attempts.items():
        attempt = attempt if isinstance(attempt, dict) else {}
        state = str(attempt.get('state') or '')
        if state not in contracts.ATTEMPT_STATES:
            _add_error(errors, 'ATTEMPT_STATE_INVALID',
                       '尝试 %s 状态不在枚举内：%r' % (attempt_id, attempt.get('state')))
        job_id = str(attempt.get('jobId') or '')
        if job_id not in jobs:
            _add_error(errors, 'ATTEMPT_JOB_DANGLING',
                       '尝试 %s 引用不存在作业：%s' % (attempt_id, job_id))

    # coverage / usageAggregate 与重算一致
    completed = set()
    for job in jobs.values():
        if str(job.get('state') or '') == contracts.JOB_SUCCEEDED:
            for target_id in job.get('orderedPrimaryTargetIds') or []:
                completed.add(str(target_id))
    expected_coverage = {'targetTotal': len(targets), 'targetCompleted': len(completed),
                         'targetPending': len(targets) - len(completed)}
    coverage = doc.get('coverage')
    coverage_ok = isinstance(coverage, dict) and all(
        _as_int(coverage.get(key)) == value for key, value in expected_coverage.items())
    if not coverage_ok:
        _add_error(errors, 'COVERAGE_MISMATCH',
                   'coverage 与重算不一致（存 %r / 应 %r）' % (coverage, expected_coverage))
    expected_usage = contracts.usage_aggregate(_settled_attempt_usages(doc))
    if doc.get('usageAggregate') != expected_usage:
        _add_error(errors, 'USAGE_AGGREGATE_MISMATCH',
                   'usageAggregate 与按已收口尝试重算不一致')

    # blocking 形状
    blocking = doc.get('blocking')
    if blocking is not None and (not isinstance(blocking, dict)
                                 or not str(blocking.get('code') or '').strip()):
        _add_error(errors, 'BLOCKING_INVALID', 'blocking 必须是含 code 的 dict 或 None')
    return errors
