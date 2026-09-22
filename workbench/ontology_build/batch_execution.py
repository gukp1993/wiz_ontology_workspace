"""本体生成控输出：共享执行器（在线与实验**唯一**调度核心，D09）。

需求：整合计划 v3 §5（事件分类表）/§8 D09；契约 batch_contracts.py（D00 冻结）。
在线（D16 经 OnlinePersistence 适配）与实验（D14 经 ExperimentState）共用本模块，
**禁止任何一方复制调度逻辑**。

纪律（冻结）：
* 每次物理 HTTP 请求一个 attempt；严格「先持久化 started 再请求」。
* 成功候选、用量、叶状态同事务提交（commit_success）；split 先持久化父子再派发。
* 持久化失败必须抛出——执行器在 save 成功后才推进内存状态，绝不先改内存再吞异常。
* 同 job 物理请求总额 ≤3（首调+网络重试≤2+格式修复≤1 共享）；预算不随重试重置。
* 重试/中间失败的 attempt 单独记账（attempt failed + job 保持 running）——走
  「本地 apply_event + save_plan」通道；只有终局失败才走 commit_failure（attempt+job 同败）。
* 只返回有界安全摘要；prompt/response 原文、密钥不进事件与 checkpoint。
* 取消：claim/commit 层核验（RunCancelledError → 运行 'cancelled'）；save 路径以
  lease 条件写兜底（cancel 不轮换 lease 的窗口由下一次 claim/commit 拦截）。
"""
import copy
import hashlib
import json
import time

from workbench.ontology_build import batch_contracts as contracts
from workbench.ontology_build import batch_plan
from workbench.ontology_build import batch_state
from workbench.ontology_build import output_codec

# 单 job 物理请求总额与分类上限（契约冻结值；测试可经 budget 覆盖 plan 级，job 级固定）
MAX_JOB_CALLS = contracts.MAX_JOB_ATTEMPTS
MAX_JOB_FORMAT_REPAIRS = 1
MAX_JOB_NETWORK_RETRIES = contracts.MAX_JOB_NETWORK_RETRIES
BACKOFF_CAP_SECONDS = 8

RETRYABLE_ERROR_CODES = (contracts.NETWORK_RETRYABLE, 'RATE_LIMITED', 'TIMEOUT',
                         'PROVIDER_ERROR')


# --- 持久化门面（统一契约形状；在线/实验两适配器都能接） ------------------------------

class PersistenceFacade(object):
    """把两类适配器统一到「task_key 形」契约签名；执行器只经这里触达持久化。

    * ExperimentState（D12）：本身就是契约形状（task_key 首参）→ 直接透传。
    * OnlinePersistence（D08）：方法带 expected_lease、无 task_key → 经 lease_fn()
      每次解析当前执行权（生产传 runner.lease_of；测试传固定值）。
    """

    def __init__(self, persistence, task_key=None, lease_fn=None):
        self._inner = persistence
        self._task_key = task_key
        self._lease_fn = lease_fn
        self._online = hasattr(persistence, 'with_lease')

    def _lease(self):
        if not self._online:
            return None
        return str(self._lease_fn() or '') if self._lease_fn else ''

    def load(self):
        if self._online:
            return self._inner.load()
        return self._inner.load(self._task_key)

    def save(self, doc):
        """计划落库（apply_event 后）：不存在则初建（save_plan），已存在则修正（amend_plan）。

        在线实现的 save_plan 会拒绝二次覆盖（防串写），执行器在计划已建后的事件
        （attempt 记账/重排队/blocking）必须走 amend_plan；这里按「先试 amend、
        计划不存在再初建」的顺序保持两种适配器语义一致（实验 save_plan 同指纹幂等）。
        """
        if self._online:
            try:
                return self._inner.amend_plan(doc, self._lease())
            except Exception as exc:   # noqa: BLE001 - 按异常语义判别「计划不存在」
                if '不存在' not in str(exc):
                    raise
                return self._inner.save_plan(doc, self._lease())
        if hasattr(self._inner, 'amend_plan'):
            try:
                return self._inner.amend_plan(self._task_key, doc)
            except ValueError as exc:
                if '不存在' not in str(exc):
                    raise
        return self._inner.save_plan(self._task_key, doc)

    def claim(self, job_id, run_attempt):
        if self._online:
            return self._inner.claim_job(self._lease(), job_id, run_attempt)
        return self._inner.claim_job(self._task_key, job_id, run_attempt)

    def commit_success(self, job_id, attempt_id, candidates, usage, digest, meta=None):
        if self._online:
            return self._inner.commit_success(self._lease(), job_id, attempt_id, candidates,
                                              usage, digest, attempt_meta=meta)
        return self._inner.commit_success(self._task_key, job_id, attempt_id, candidates,
                                          usage, digest)

    def commit_split(self, job_id, children, usage, finish_reason=None):
        if self._online:
            return self._inner.commit_split(self._lease(), job_id, children, usage,
                                            finish_reason=finish_reason)
        return self._inner.commit_split(self._task_key, job_id, children, usage)

    def commit_failure(self, job_id, attempt_id, error_code, message, usage):
        if self._online:
            return self._inner.commit_failure(self._lease(), job_id, attempt_id, error_code,
                                              message, usage)
        return self._inner.commit_failure(self._task_key, job_id, attempt_id, error_code,
                                          message, usage)

    def mark_unknown(self, attempt_ids):
        if self._online:
            return self._inner.mark_interrupted_unknown(attempt_ids)
        return self._inner.mark_interrupted_unknown(self._task_key, attempt_ids)

    def iterate_candidates(self, page_size=200):
        if self._online:
            return self._inner.iterate_candidates(page_size=page_size)
        return self._inner.iterate_candidates(self._task_key, page_size=page_size)


# --- 上下文 -------------------------------------------------------------------------

DEFAULT_BUDGET = {
    'maxAttempts': contracts.MAX_PLAN_ATTEMPTS,
    'maxWallMs': contracts.MAX_PLAN_WALL_SECONDS * 1000,
    'completionSoftLimit': None,      # 实验 campaign 已知 completion 软限；在线 None
    'maxJobs': contracts.MAX_PLAN_JOBS,
}


def make_context(persistence, call_fn, profile, plan_doc, codec_version=contracts.CODEC_LEGACY,
                 facts_by_id=None, scope_payload=None, planner=None, clock=None, sleeper=None,
                 run_attempt=1, budget=None, task_key=None, lease_fn=None,
                 resume_requeue_failed=False, candidate_transform=None, progress_fn=None):
    """组装执行上下文。

    persistence：OnlinePersistence 或 ExperimentState（自动适配）。
    call_fn(messages, max_tokens) -> CallResult（D05 chat_once_result 形状）。
    planner(context) -> {'jobs': [...]}（可选；队列空且仍有 pending 时补装箱——
    缺省用 batch_plan.pack_next + 全量 targets）。
    clock：单调秒表（默认 time.monotonic）；sleeper：退避睡眠（默认 time.sleep，测试注入）。
    resume_requeue_failed：显式 resume（run-resume 新 runAttempt）置 True——把 failed 叶
    重排队（failed→queued），只补未成功叶；blocked/superseded 不动（须新计划）。
    candidate_transform(candidates, job_id) -> candidates：解码成功后、commit_success 前
    的候选加工钩子（在线路径走 verify/命名空间化，与 legacy 批次同链路；缺省直通）。
    返回列表即落库内容（可少不可多）；抛异常=该次不提交，原样上抛。
    progress_fn(event, doc)：每个 step 后回调（在线路径推进 stage/进度；抛 Cancelled
    会从 run_plan 原样上抛，用于取消）。
    """
    facade = persistence if isinstance(persistence, PersistenceFacade) \
        else PersistenceFacade(persistence, task_key=task_key, lease_fn=lease_fn)
    return {
        'persistence': facade,
        'call_fn': call_fn,
        'profile': profile or {},
        'doc': copy.deepcopy(plan_doc),
        'codec': codec_version if codec_version in contracts.CODEC_VERSIONS
                 else contracts.CODEC_LEGACY,
        'facts_by_id': facts_by_id if isinstance(facts_by_id, dict) else {},
        'scope_payload': scope_payload if isinstance(scope_payload, dict) else {},
        'planner': planner,
        'clock': clock or time.monotonic,
        'sleeper': sleeper or time.sleep,
        'run_attempt': int(run_attempt or 1),
        'budget': dict(DEFAULT_BUDGET, **(budget or {})),
        'events': [],
        'resume_requeue_failed': bool(resume_requeue_failed),
        '_requeued': False,
        'candidate_transform': candidate_transform,
        'progress_fn': progress_fn,
    }


def _record(context, event):
    context['events'].append(event)
    return event


# --- 守卫 ---------------------------------------------------------------------------

def _plan_attempts(doc):
    return len(doc.get('attempts') or {})


def _plan_guard(context):
    """派发前计划级守卫；命中返回 (code, message)，否则 None。内存不落库（由调用方处理）。"""
    doc = context['doc']
    budget = context['budget']
    if doc.get('blocking'):
        return str(doc['blocking'].get('code') or 'BLOCKED'), \
            str(doc['blocking'].get('message') or '')
    attempts = _plan_attempts(doc)
    if attempts >= int(budget['maxAttempts']):
        return contracts.ATTEMPT_BUDGET_EXCEEDED, \
            '计划尝试数已达上限 %d' % int(budget['maxAttempts'])
    if int(doc.get('activeElapsedMs') or 0) >= int(budget['maxWallMs']):
        return contracts.WALL_TIME_BUDGET_EXCEEDED, \
            '活跃执行耗时已达上限 %d 秒' % (int(budget['maxWallMs']) // 1000)
    soft = budget.get('completionSoftLimit')
    if soft is not None:
        known = int((doc.get('usageAggregate') or {}).get('knownCompletionTokens') or 0)
        if known >= int(soft):
            return contracts.COMPLETION_SOFT_LIMIT, \
                '已知 completion 累计 %d 达软限 %d，停止派发' % (known, int(soft))
    if len(doc.get('jobs') or {}) >= int(budget['maxJobs']):
        return contracts.JOB_BUDGET_EXCEEDED, '作业数已达上限 %d' % int(budget['maxJobs'])
    if not contracts.checkpoint_fits(doc, 0):
        return contracts.CHECKPOINT_BUDGET_EXCEEDED, \
            'checkpoint 序列化超过软阈值，停止派发（在途响应仍可原子保存）'
    return None


def _next_queued_leaf(doc):
    for job_id, job in (doc.get('jobs') or {}).items():
        if job.get('children'):
            continue
        if str(job.get('state') or '') == contracts.JOB_QUEUED:
            return job_id
    return None


def _job_total_attempts(doc, job_id):
    job = (doc.get('jobs') or {}).get(job_id) or {}
    return len(job.get('attemptIds') or [])


def _job_run_attempts(doc, job_id, run_attempt):
    """当前 runAttempt 内的尝试数（job 级 3 次预算口径；跨 resume 只按本轮计数）。"""
    attempts = doc.get('attempts') or {}
    job = (doc.get('jobs') or {}).get(job_id) or {}
    return sum(1 for aid in (job.get('attemptIds') or [])
               if int((attempts.get(aid) or {}).get('runAttempt') or 0) == int(run_attempt))


def _requeue_failed_leaves(context):
    """显式 resume：failed 叶重排队（blocked/superseded 不动——须新建计划，§6）。"""
    if context.get('_requeued') or not context.get('resume_requeue_failed'):
        return
    context['_requeued'] = True
    events = []
    for job_id, job in (context['doc'].get('jobs') or {}).items():
        if job.get('children'):
            continue
        if str(job.get('state') or '') == contracts.JOB_FAILED:
            events.append({'type': getattr(batch_state, 'EVENT_JOB_REQUEUED', 'job_requeued'),
                           'jobId': job_id, 'runAttempt': context['run_attempt']})
    if events:
        _apply_persisted(context, events)
        _record(context, {'kind': 'resume_requeue', 'jobs': len(events)})


def _job_attempts(doc, job_id):
    job = (doc.get('jobs') or {}).get(job_id) or {}
    attempts = doc.get('attempts') or {}
    return [attempts.get(aid) or {} for aid in (job.get('attemptIds') or [])]


def _apply_persisted(context, events):
    """本地镜像 + save_plan 持久化（save 路径专用）。

    先在副本上 apply，save 成功才替换内存 doc——持久化失败不推进内存状态（冻结纪律）。
    """
    candidate = copy.deepcopy(context['doc'])
    for event in events:
        candidate = batch_state.apply_event(candidate, event)
    context['persistence'].save(candidate)
    context['doc'] = candidate
    return candidate


def _mirror(context, events):
    """镜像已由持久化层落库的事件（claim/commit_* 成功返回后同步内存 doc）。"""
    doc = context['doc']
    for event in events:
        doc = batch_state.apply_event(doc, event)
    context['doc'] = doc
    return doc


# --- 编码与摘要 ---------------------------------------------------------------------

def _job_target_entries(context, job):
    targets = context['doc'].get('targets') or {}
    facts = context['facts_by_id']
    primary = [dict(targets.get(tid) or {}, targetId=tid)
               for tid in (job.get('orderedPrimaryTargetIds') or [])]
    return batch_plan.targets_with_content(primary, facts)


def _job_context_facts(context, job):
    facts = context['facts_by_id']
    return [dict(facts.get(fid) or {}, id=fid)
            for fid in (job.get('contextFactIds') or []) if facts.get(fid)]


def _encode(context, job):
    encoded = output_codec.encode_request(
        context['codec'], _job_target_entries(context, job),
        _job_context_facts(context, job), context['scope_payload'])
    return encoded['messages'], encoded['aliasMap'], encoded['unitIds']


def _repair_messages(messages, decode_errors):
    reason = '；'.join('%s：%s' % (e.get('code'), e.get('message')) for e in decode_errors[:3])
    return messages + [
        {'role': 'assistant', 'content': '（上次输出无效）'},
        {'role': 'user', 'content':
            '上面的输出不符合输出契约（%s）。请重新输出：只输出一个 JSON 对象本体，'
            '不要 Markdown 代码块与任何解释文字；coverage 必须覆盖全部给定单元，'
            '不得引用未提供的来源标识。' % (reason or '格式无效')}]


def _digest(candidates):
    payload = json.dumps(candidates, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()[:16]


# --- 单作业驱动（一次 step = 一个 job 到终态/预算耗尽） --------------------------------

def _drive_job(context, job_id):
    doc = context['doc']
    job = (doc.get('jobs') or {}).get(job_id) or {}
    format_repairs = 0
    network_retries = 0
    attempt_id = ''
    max_tokens = int((context['profile'].get('effective') or {}).get('outputCap')
                     or contracts.DEFAULT_REQUEST_OUTPUT_TOKENS)
    messages = alias_map = unit_ids = None

    while True:
        guard = _plan_guard(context)
        if guard:
            return _block_run(context, guard[0], guard[1])

        if not attempt_id:
            # ① 先持久化 started（claim 内部：核验 lease/取消 → attempt started → job running）
            pre_claim_sequence = _job_total_attempts(context['doc'], job_id)
            claimed = context['persistence'].claim(job_id, context['run_attempt'])
            attempt_id = str(claimed.get('attemptId') or '')
            max_tokens = int(claimed.get('requestedMaxTokens') or max_tokens)
            _mirror(context, [
                {'type': batch_state.EVENT_JOB_CLAIMED, 'jobId': job_id,
                 'attemptId': attempt_id},
                {'type': batch_state.EVENT_ATTEMPT_STARTED, 'jobId': job_id,
                 'attemptId': attempt_id, 'sequence': pre_claim_sequence,
                 'runAttempt': context['run_attempt'],
                 'requestedMaxTokens': max_tokens, 'requestFingerprint': ''}])
        else:
            # 重试路径：新 attempt 先持久化再请求（attempt-only 失败已记，job 仍 running）
            attempt_id = contracts.new_attempt_id()
            _apply_persisted(context, [
                {'type': batch_state.EVENT_ATTEMPT_STARTED, 'jobId': job_id,
                 'attemptId': attempt_id,
                 'sequence': _job_total_attempts(context['doc'], job_id),
                 'runAttempt': context['run_attempt'],
                 'requestedMaxTokens': max_tokens, 'requestFingerprint': ''}])

        if messages is None:
            messages, alias_map, unit_ids = _encode(context, job)

        result = context['call_fn'](messages, max_tokens)
        usage = result.get('usage') if isinstance(result.get('usage'), dict) \
            else contracts.empty_usage()
        duration_ms = int(result.get('durationMs') or 0)
        _mirror(context, [{'type': batch_state.EVENT_ELAPSED_UPDATE,
                           'deltaMs': max(0, duration_ms)}])

        if not result.get('ok'):
            code = str(result.get('errorCode') or 'HTTP_UNKNOWN')
            total = _job_run_attempts(context['doc'], job_id, context['run_attempt'])
            if result.get('retryable') and code in RETRYABLE_ERROR_CODES \
                    and network_retries < MAX_JOB_NETWORK_RETRIES and total < MAX_JOB_CALLS:
                network_retries += 1
                _apply_persisted(context, [
                    {'type': batch_state.EVENT_ATTEMPT_FAILED, 'attemptId': attempt_id,
                     'errorCode': code,
                     'message': batch_state.bound_error(str(result.get('content') or ''
                                                          or '网络错误')),
                     'usage': usage}])
                context['sleeper'](min(2 ** network_retries, BACKOFF_CAP_SECONDS))
                _record(context, {'kind': 'network_retry', 'jobId': job_id,
                                  'attemptId': attempt_id, 'errorCode': code,
                                  'retry': network_retries})
                continue
            if code == contracts.FORMAT_INVALID and format_repairs < MAX_JOB_FORMAT_REPAIRS \
                    and total < MAX_JOB_CALLS:
                format_repairs += 1
                _apply_persisted(context, [
                    {'type': batch_state.EVENT_ATTEMPT_FAILED, 'attemptId': attempt_id,
                     'errorCode': code,
                     'message': batch_state.bound_error('响应结构不符合契约'),
                     'usage': usage}])
                messages = _repair_messages(messages, [{'code': code, 'message': '响应不是合法契约结构'}])
                _record(context, {'kind': 'format_repair', 'jobId': job_id,
                                  'attemptId': attempt_id, 'retry': format_repairs})
                continue
            hit = context['persistence'].commit_failure(job_id, attempt_id, code,
                                                        batch_state.bound_error(
                                                            str(result.get('content')
                                                            or '调用失败')),
                                                        usage)
            if not hit:
                raise PersistenceRejected('commit_failure 未提交（lease/取消/终态冲突）')
            _mirror(context, [
                {'type': batch_state.EVENT_ATTEMPT_FAILED, 'attemptId': attempt_id,
                 'errorCode': code, 'message': batch_state.bound_error(str(code)),
                 'usage': usage},
                {'type': batch_state.EVENT_JOB_FAILED, 'jobId': job_id, 'errorCode': code,
                 'message': batch_state.bound_error(str(code))}])
            _record(context, {'kind': 'job_failed', 'jobId': job_id, 'errorCode': code})
            return {'kind': 'job_failed', 'jobId': job_id, 'errorCode': code}

        finish = result.get('finishReason')
        if finish == 'length':
            return _split_after_truncation(context, job_id, usage, attempt_id, finish)

        decoded = output_codec.decode_response(context['codec'], result.get('content'),
                                               alias_map or {}, unit_ids or [],
                                               finish_reason=finish)
        if decoded.get('ok'):
            to_commit = decoded.get('candidates') or []
            transform = context.get('candidate_transform')
            if callable(transform):
                to_commit = transform(to_commit, job_id) or []
            digest = _digest(to_commit)
            hit = context['persistence'].commit_success(
                job_id, attempt_id, to_commit, usage, digest,
                {'finishReason': finish, 'bytes': int(result.get('responseBytes') or 0),
                 'durationMs': duration_ms})
            if not hit:
                raise PersistenceRejected('commit_success 未提交（lease/取消/终态冲突）')
            _mirror(context, [
                {'type': batch_state.EVENT_ATTEMPT_SUCCEEDED, 'attemptId': attempt_id,
                 'usage': usage, 'finishReason': finish,
                 'bytes': int(result.get('responseBytes') or 0), 'durationMs': duration_ms},
                {'type': batch_state.EVENT_JOB_SUCCEEDED, 'jobId': job_id,
                 'candidateCount': len(to_commit),
                 'resultDigest': digest}])
            _record(context, {'kind': 'job_succeeded', 'jobId': job_id,
                              'candidates': len(to_commit)})
            return {'kind': 'job_succeeded', 'jobId': job_id,
                    'candidates': len(to_commit)}

        errors = decoded.get('errors') or [{'code': contracts.FORMAT_INVALID, 'message': '解码失败'}]
        code = str(errors[0].get('code') or contracts.FORMAT_INVALID)
        total = _job_run_attempts(context['doc'], job_id, context['run_attempt'])
        if format_repairs < MAX_JOB_FORMAT_REPAIRS and total < MAX_JOB_CALLS:
            format_repairs += 1
            _apply_persisted(context, [
                {'type': batch_state.EVENT_ATTEMPT_FAILED, 'attemptId': attempt_id,
                 'errorCode': code,
                 'message': batch_state.bound_error('；'.join(
                     str(e.get('message') or '') for e in errors[:2])),
                 'usage': usage}])
            messages = _repair_messages(messages, errors)
            _record(context, {'kind': 'format_repair', 'jobId': job_id,
                              'attemptId': attempt_id, 'errorCode': code,
                              'retry': format_repairs})
            continue
        hit = context['persistence'].commit_failure(
            job_id, attempt_id, code,
            batch_state.bound_error('；'.join(str(e.get('message') or '') for e in errors[:2])),
            usage)
        if not hit:
            raise PersistenceRejected('commit_failure 未提交（lease/取消/终态冲突）')
        _mirror(context, [
            {'type': batch_state.EVENT_ATTEMPT_FAILED, 'attemptId': attempt_id,
             'errorCode': code,
             'message': batch_state.bound_error('；'.join(str(e.get('message') or '')
                                                       for e in errors[:2])),
             'usage': usage},
            {'type': batch_state.EVENT_JOB_FAILED, 'jobId': job_id, 'errorCode': code,
             'message': batch_state.bound_error('；'.join(str(e.get('message') or '')
                                                       for e in errors[:2]))}])
        _record(context, {'kind': 'job_failed', 'jobId': job_id, 'errorCode': code})
        return {'kind': 'job_failed', 'jobId': job_id, 'errorCode': code}


class PersistenceRejected(Exception):
    """commit_* 返回 False（lease/取消/终态冲突拒绝）——执行器停止，不推进内存。"""


def _split_after_truncation(context, job_id, usage, attempt_id, finish):
    """length → 拆分决策；children 原子落库后才派发（契约③）。"""
    doc = context['doc']
    job = (doc.get('jobs') or {}).get(job_id) or {}
    depth = str(job.get('splitPath') or 'root').count('/')
    decision = batch_plan.split_or_block(_job_target_entries(context, job),
                                         context['facts_by_id'], depth,
                                         profile=context['profile'],
                                         plan_epoch=int(doc.get('planEpoch') or 1),
                                         parent_job_id=job_id,
                                         parent_split_path=str(job.get('splitPath') or 'root'))
    if decision.get('action') == 'blocked':
        code = str(decision.get('code') or contracts.OVERSIZED_ATOMIC_TARGET)
        message = batch_state.bound_error(str(decision.get('message') or code))
        # 一次落库：截断 attempt 记账 + job blocked + run 级 blocking（避免双重迁移）
        _apply_persisted(context, [
            {'type': batch_state.EVENT_ATTEMPT_SUCCEEDED, 'attemptId': attempt_id,
             'usage': usage, 'finishReason': finish},
            {'type': batch_state.EVENT_JOB_BLOCKED, 'jobId': job_id, 'code': code,
             'message': message},
            {'type': batch_state.EVENT_BLOCKING_SET, 'code': code, 'message': message}])
        _record(context, {'kind': 'blocked', 'code': code, 'message': message})
        return {'kind': 'blocked', 'code': code, 'message': message}
    children = decision.get('children') or []
    hit = context['persistence'].commit_split(job_id, children, usage, finish_reason=finish)
    if not hit:
        raise PersistenceRejected('commit_split 未提交（lease/取消/终态冲突）')
    mirror = [{'type': batch_state.EVENT_ATTEMPT_SUCCEEDED, 'attemptId': attempt_id,
               'usage': usage, 'finishReason': finish},
              {'type': batch_state.EVENT_JOB_SPLIT, 'jobId': job_id, 'children': children}]
    _mirror(context, mirror)
    _record(context, {'kind': 'job_split', 'jobId': job_id, 'children': len(children)})
    return {'kind': 'job_split', 'jobId': job_id, 'children': len(children)}


def _block_run(context, code, message, job_id=None):
    events = [{'type': batch_state.EVENT_BLOCKING_SET,
               'code': code, 'message': batch_state.bound_error(message)}]
    if job_id:
        events.append({'type': batch_state.EVENT_JOB_BLOCKED, 'jobId': job_id,
                       'code': code, 'message': batch_state.bound_error(message)})
    _apply_persisted(context, events)
    _record(context, {'kind': 'blocked', 'code': code, 'message': message})
    return {'kind': 'blocked', 'code': code, 'message': message}


def _plan_more(context):
    """队列空但仍有 pending → 装箱补充作业（只对未建 job 的 pending，§4.3）。

    planner 可注入（实验对照臂）；缺省用 batch_plan.pack_next + 全量 targets。
    """
    doc = context['doc']
    pending_ids = list(doc.get('pendingTargetIds') or [])
    if not pending_ids:
        return None
    targets = doc.get('targets') or {}
    pending = [dict(targets[tid], targetId=tid) for tid in pending_ids if targets.get(tid)]
    if callable(context['planner']):
        result = context['planner'](context)
    else:
        pool = [dict(targets[tid], targetId=tid) for tid in sorted(targets)]
        result = batch_plan.pack_next(pending, pool,
                                      context['facts_by_id'], context['profile'],
                                      plan_epoch=int(doc.get('planEpoch') or 1),
                                      scope_payload=context['scope_payload'])
    jobs = [job for job in ((result or {}).get('jobs') or []) if isinstance(job, dict)]
    infeasible = list((result or {}).get('infeasible') or [])
    if jobs:
        events = [{'type': batch_state.EVENT_JOB_CREATED, **job} for job in jobs]
        _apply_persisted(context, events)
        _record(context, {'kind': 'planned', 'jobs': len(jobs)})
        return {'kind': 'planned', 'jobs': len(jobs)}
    if infeasible:
        # 装箱放不下的目标先尝试拆分（硬输入超限 → 细分主目标，§5）；不可分才受阻。
        targets = doc.get('targets') or {}
        depth_cap = contracts.MAX_SPLIT_DEPTH
        for tid in infeasible:
            target = dict(targets.get(tid) or {})
            if not target:
                continue
            if target.get('expandedInto'):
                continue   # 已展开：子目标已登记，等待其自身处理（不再重复拆分）
            decision = batch_plan.split_or_block(
                [dict(target, targetId=tid)], context['facts_by_id'], 0,
                profile=context['profile'],
                plan_epoch=int(doc.get('planEpoch') or 1),
                parent_job_id='', parent_split_path='root')
            if decision.get('action') == 'split':
                events = [{'type': batch_state.EVENT_JOB_CREATED, **child}
                          for child in (decision.get('children') or [])]
                if events:
                    _apply_persisted(context, events)
                    _record(context, {'kind': 'planned', 'jobs': len(events),
                                      'fromSplit': tid})
                    return {'kind': 'planned', 'jobs': len(events)}
            if decision.get('action') == 'blocked' \
                    and str(decision.get('code')) == contracts.SPLIT_DEPTH_EXCEEDED:
                return _block_run(context, depth_cap and contracts.SPLIT_DEPTH_EXCEEDED,
                                  str(decision.get('message') or '拆分深度超限'))
        return _block_run(context, contracts.OVERSIZED_ATOMIC_TARGET,
                          '存在无法装箱且不可拆分的目标（需补充材料或缩小任务）：%d 个'
                          % len(infeasible))
    return None


# --- 步进与整跑 ---------------------------------------------------------------------

TERMINAL_KINDS = ('done', 'blocked', 'job_failed', 'stalled', 'cancelled', 'exhausted')


def step(context):
    """推进一步：取一个可派发叶 job 并驱动到终态（含有限重试）；返回事件。

    返回 kind：job_succeeded / job_split / job_failed / blocked / planned /
    done（终态检查已过）/ exhausted（预算/容量守卫）/ stalled（无可派发且无法补充）。
    """
    doc = context['doc']
    check = contracts.final_state_check({'generate': doc})
    if check.get('ok'):
        return {'kind': 'done'}
    _requeue_failed_leaves(context)
    guard = _plan_guard(context)
    if guard:
        return _block_run(context, guard[0], guard[1])
    job_id = _next_queued_leaf(doc)
    if job_id is None:
        planned = _plan_more(context)
        if planned:
            return planned
        pending = doc.get('pendingTargetIds') or []
        if pending:
            return _block_run(context, contracts.JOB_BUDGET_EXCEEDED,
                              '仍有 %d 个待处理目标但无法继续装箱' % len(pending))
        return {'kind': 'stalled', 'violations': check.get('violations') or []}
    return _drive_job(context, job_id)


def run_plan(context):
    """循环 step 直到终态；返回 {'state','blocking','usage','events'}。

    state：succeeded（终态检查通过）/ failed（存在失败叶，成功叶保留）/
    blocked（预算/容量/不可分，blocking 可读）/ stalled（内部不一致，防死循环）。
    持久化异常原样上抛（调用方决定终态；内存 doc 未推进）。
    """
    seen_stalls = 0
    while True:
        event = step(context)
        kind = event.get('kind')
        doc = context['doc']
        progress = context.get('progress_fn')
        if callable(progress):
            progress(event, doc)   # Cancelled 等异常原样上抛（取消即中止）
        if kind == 'done':
            return _summary(context, 'succeeded', None)
        if kind == 'blocked' or kind == 'exhausted':
            blocking = doc.get('blocking') or {'code': event.get('code') or 'BLOCKED',
                                               'message': event.get('message') or ''}
            return _summary(context, 'blocked', blocking)
        if kind == 'job_failed':
            return _summary(context, 'failed',
                            {'code': str(event.get('errorCode') or 'JOB_FAILED'),
                             'message': '作业 %s 失败（%s）；已成功结果保留，可重试只补未成功部分'
                                        % (event.get('jobId'), event.get('errorCode'))})
        if kind == 'stalled':
            seen_stalls += 1
            if seen_stalls >= 2:
                return _summary(context, 'stalled',
                                {'code': 'INTERNAL', 'message': '调度停滞（无可派发且未终态）'})
        if kind in ('job_succeeded', 'job_split', 'planned', 'network_retry',
                    'format_repair'):
            seen_stalls = 0


def _summary(context, state, blocking):
    doc = context['doc']
    usage = dict(doc.get('usageAggregate') or {})
    usage.setdefault('knownCompletionTokens', 0)
    usage.setdefault('unknownUsageCalls', 0)
    return {'state': state, 'blocking': blocking, 'usage': usage,
            'events': list(context['events']),
            'doc': doc, 'planId': str(doc.get('planId') or ''),
            'planEpoch': int(doc.get('planEpoch') or 0)}
