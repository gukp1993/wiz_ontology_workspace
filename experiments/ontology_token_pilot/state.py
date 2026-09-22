# -*- coding: utf-8 -*-
"""本体 token 试点：试验 SQLite 持久化适配与隔离缓存（D12 交付）。

契约来源：workbench/ontology_build/batch_contracts.py「持久化接口」节（D00 冻结）；
状态机复用：workbench/ontology_build/batch_state.py（D07，apply_event/validate_plan_doc）；
需求：《本体生成控输出_整合方案与并行开发计划_v3.md》§11（实验独立目录、不连业务
ROOT、不写候选业务表）与 §8 D12 行（复用状态机，独立目录，冷热缓存分开）。

职责边界（Persistence 契约的实验 SQLite 双实现之一，与 D08 在线适配器同一套方法语义）：
* 只依赖标准库 sqlite3 + batch_contracts + batch_state，**绝不 import workbench.storage**
  （实验库是 db_path 指定的独立 SQLite 文件，默认不触碰任何业务 ROOT/业务候选表）；
* 计划与状态：checkpoint['generate']（schemaVersion=2 冻结结构）整体 JSON 文本列存取；
  状态推进一律复用 batch_state.apply_event（深拷贝、非法迁移 ValueError），本模块
  不复制任何状态机/覆盖/用量聚合逻辑；
* 候选：结构化列（type/key/name/fields_json/owner_key/evidence_json/evidence_status/
  origin_json/created_at）+ payload_json 全量列（零信息丢失，iterate 原样回读）；
  幂等键 candidate_id = 'c-' + jobId[:12] + '-' + 局部键（局部键=候选自带 key，
  缺 key 时用候选 canonical JSON 的 sha1 派生——同输入必同 id，重复回调跳过）；
* 冷热缓存分开：cache_get/cache_put 走独立 KV 表（校准快照/估算缓存用）；缓存跟着
  db_path 走、跨重跑共用、换 output-dir 不重置（目录布局归 D14 管，本模块不管）；
* 容量守卫：每次落库前用 batch_contracts.checkpoint_fits 对照软阈值，超限抛本地
  CheckpointCapacityError（同名同语义异常类；与 D08 并行开发各自本地定义，D17 统一）；
* 事务：显式 BEGIN IMMEDIATE…COMMIT，任何异常 ROLLBACK 后原样抛出——
  Persistence 抛异常 = 未保存成功，执行器在 save 成功后才推进内存状态（契约纪律）。

方法语义要点（与契约逐字对齐，供 D17 双适配器参数化对照）：
* load(task_key)：无计划返回 None，有则返回完整 plan_doc；
* save_plan：初次建计划、原子；已存在且指纹不同 → ValueError（拒绝串写），
  同指纹重存幂等覆盖；
* claim_job：先持久化 started attempt + job running（含执行权核验）再返回，失败抛出；
  queued→claimed+started；running（如崩溃恢复后续尝试）→ 直接追加 started；
  其他状态一律 ValueError（无执行权）；
* commit_success：同事务写候选（幂等）+ attempt succeeded/usage + job succeeded +
  resultDigest；重复提交（job/attempt 均已成功）返回 False 跳过，不重复写候选；
  attempt 非 started 或 job 非 running（取消/受阻）→ ValueError（晚结果拒绝、不复活）；
* commit_split：同事务父 split + 全部子 job queued + 在途 attempt 记账（finishReason
  'length'，usage 取 event_usage）；无在途尝试的 queued 父作业也允许拆分（不记账）；
  子作业合法性（非空、覆盖守恒、深度、id 冲突）由 apply_event 的 job_split 校验；
* commit_failure：attempt failed（errorCode/message/usage）+ job failed 同事务；
* mark_interrupted_unknown：started → interrupted_unknown（真实计费可能发生，显示
  未知不计 0）；不存在的/非 started 的 id 跳过，返回实际收口条数；
* iterate_candidates：按写入序（seq）keyset 分页稳定迭代，页 = 候选 dict 列表；
  复验/终态检查必须走这里，绝不用一次性全量查询代替。

本模块不实现：调度/重试/预算判断（D09 执行器）、候选结构业务校验（既有交付校验）、
MAX_FINAL_CANDIDATES 之类产品预算（执行器职责，持久化层不裁数据）。
"""
import contextlib
import hashlib
import json
import os
import sqlite3
import time

from workbench.ontology_build import batch_contracts as contracts
from workbench.ontology_build import batch_state

__all__ = ['CheckpointCapacityError', 'ExperimentState', 'open_state', 'candidate_id_for']


class CheckpointCapacityError(Exception):
    """checkpoint 序列化超过软阈值（batch_contracts.checkpoint_fits 口径）。

    与 D08 在线适配器的同名异常同语义；并行开发期各自本地定义，D17 参数化时统一。
    """


# --- schema（实验独立库；pilot_ 前缀避免与同文件其他实验表混淆）-------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pilot_plans (
    task_key    TEXT PRIMARY KEY,
    plan_json   TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS pilot_candidates (
    seq             INTEGER PRIMARY KEY AUTOINCREMENT,
    task_key        TEXT NOT NULL,
    candidate_id    TEXT NOT NULL,
    job_id          TEXT NOT NULL,
    type            TEXT,
    "key"           TEXT,
    name            TEXT,
    fields_json     TEXT,
    owner_key       TEXT,
    evidence_json   TEXT,
    evidence_status TEXT,
    origin_json     TEXT,
    payload_json    TEXT NOT NULL,
    created_at      REAL NOT NULL,
    UNIQUE(task_key, candidate_id)
);
CREATE INDEX IF NOT EXISTS idx_pilot_candidates_task ON pilot_candidates(task_key, seq);
CREATE TABLE IF NOT EXISTS pilot_cache (
    cache_key    TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL,
    created_at   REAL NOT NULL,
    expires_at   REAL
);
"""


def _dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _text_or_none(value):
    return None if value is None else str(value)


def _json_or_none(value):
    return None if value is None else _dumps(value)


def _candidate_local_key(candidate):
    """候选局部键：优先候选自带 key；缺 key 用 canonical JSON 摘要（确定性派生）。"""
    key = candidate.get('key')
    if isinstance(key, str) and key.strip():
        return key.strip()
    return 'h' + hashlib.sha1(_dumps(candidate).encode('utf-8')).hexdigest()[:12]


def candidate_id_for(job_id, candidate):
    """幂等键（契约口径）：'c-' + jobId[:12] + '-' + 局部键；同输入必同 id。"""
    return 'c-%s-%s' % (str(job_id or '')[:12], _candidate_local_key(candidate))


def _requested_max_tokens(doc, job):
    """本次尝试 requestedMaxTokens：job.estimate 显式值优先，退回预算 profile，再退默认。"""
    estimate = job.get('estimate') if isinstance(job.get('estimate'), dict) else {}
    for field in ('requestedMaxTokens', 'requestOutputTokens', 'expectedOutputTokens'):
        value = estimate.get(field)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 1:
            return value
    profile = doc.get('budgetProfile') if isinstance(doc.get('budgetProfile'), dict) else {}
    value = profile.get('requestOutputTokens')
    if isinstance(value, int) and not isinstance(value, bool) and value >= 1:
        return value
    return contracts.DEFAULT_REQUEST_OUTPUT_TOKENS


def _non_empty_str(value, field):
    text = str(value or '').strip()
    if not text:
        raise ValueError('%s 不能为空' % field)
    return text


class ExperimentState(object):
    """试验 SQLite Persistence（鸭子类型实现 batch_contracts「持久化接口」全组方法）。

    db_path 为实验独立库文件（父目录须存在或可创建——用 open_state 自动建）；
    clock 可注入（测试 ttl/时间戳），默认 time.time。
    """

    def __init__(self, db_path, clock=None):
        self.db_path = os.fspath(db_path)
        self._clock = clock if clock is not None else time.time
        self._conn = sqlite3.connect(self.db_path, timeout=5.0, isolation_level=None)
        self._conn.execute('PRAGMA journal_mode=WAL')
        self._conn.execute('PRAGMA busy_timeout=5000')
        self._conn.executescript(_SCHEMA)

    # --- 生命周期 ---------------------------------------------------------------

    def close(self):
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
        return False

    @contextlib.contextmanager
    def _write_tx(self):
        """显式写事务：BEGIN IMMEDIATE…COMMIT；任何异常 ROLLBACK 后原样抛出。"""
        self._conn.execute('BEGIN IMMEDIATE')
        try:
            yield
            self._conn.execute('COMMIT')
        except BaseException:
            try:
                self._conn.execute('ROLLBACK')
            except sqlite3.Error:
                pass
            raise

    # --- 计划整体存取 -------------------------------------------------------------

    def _load_row(self, task_key):
        return self._conn.execute('SELECT plan_json FROM pilot_plans WHERE task_key = ?',
                                  (str(task_key),)).fetchone()

    def _load_doc(self, task_key):
        """事务内读完整计划；不存在 → ValueError（调用方均已有计划前提）。"""
        row = self._load_row(task_key)
        if row is None:
            raise ValueError('计划不存在：%r' % (task_key,))
        return json.loads(row[0])

    def _persist_doc(self, task_key, doc, inflight_calls=0):
        """容量守卫 + 结构校验 + 整体落库（必须在写事务内调用；失败抛出不假成功）。"""
        if not contracts.checkpoint_fits({'generate': doc}, inflight_calls):
            raise CheckpointCapacityError(
                'checkpoint 超过软阈值（inflight=%d，上限 %d 字节）：task=%r'
                % (inflight_calls, contracts.checkpoint_soft_limit_bytes(inflight_calls),
                   task_key))
        errors = batch_state.validate_plan_doc(doc)
        if errors:
            raise ValueError('计划结构校验失败：%s' % ';'.join(
                '%s: %s' % (item.get('code'), item.get('message')) for item in errors[:3]))
        cur = self._conn.execute(
            'UPDATE pilot_plans SET plan_json = ?, fingerprint = ?, updated_at = ? '
            'WHERE task_key = ?',
            (_dumps(doc), str(doc.get('fingerprint') or ''), self._clock(), str(task_key)))
        if cur.rowcount != 1:
            raise ValueError('计划不存在，无法落库：%r' % (task_key,))

    # --- Persistence 契约方法（签名与 batch_contracts「持久化接口」逐字一致）----------

    def load(self, task_key):
        """读完整计划 doc；无计划返回 None。"""
        row = self._load_row(task_key)
        if row is None:
            return None
        return json.loads(row[0])

    def save_plan(self, task_key, plan_doc):
        """初次建计划，原子；容量守卫超限抛 CheckpointCapacityError，库内不变。

        已存在同指纹 → 幂等覆盖（崩溃重跑安全）；指纹不同 → ValueError（拒绝串写）。
        """
        if not isinstance(plan_doc, dict):
            raise ValueError('计划 doc 必须是 dict')
        errors = batch_state.validate_plan_doc(plan_doc)
        if errors:
            raise ValueError('计划结构校验失败：%s' % ';'.join(
                '%s: %s' % (item.get('code'), item.get('message')) for item in errors[:3]))
        if not contracts.checkpoint_fits({'generate': plan_doc}, 0):
            raise CheckpointCapacityError(
                'checkpoint 超过软阈值（inflight=0，上限 %d 字节）：task=%r'
                % (contracts.checkpoint_soft_limit_bytes(0), task_key))
        fingerprint = str(plan_doc.get('fingerprint') or '')
        with self._write_tx():
            row = self._conn.execute(
                'SELECT fingerprint FROM pilot_plans WHERE task_key = ?',
                (str(task_key),)).fetchone()
            if row is not None and str(row[0] or '') != fingerprint:
                raise ValueError('计划 %r 已存在且指纹不同（拒绝覆盖）' % (task_key,))
            self._conn.execute(
                'INSERT OR REPLACE INTO pilot_plans '
                '(task_key, plan_json, fingerprint, updated_at) VALUES (?, ?, ?, ?)',
                (str(task_key), _dumps(plan_doc), fingerprint, self._clock()))

    def amend_plan(self, task_key, plan_doc):
        """计划内事件修正（执行器 apply_event 后落库）：整体替换 doc，容量守卫同一口径。

        与 save_plan 的差异：save_plan 只承认「同指纹重存」为幂等（拒绝异指纹覆盖）；
        amend_plan 供执行器在**已建计划**内推进状态（attempt 记账/重排队/blocking），
        结构与容量校验与 save_plan 同口径，但允许 doc 随状态推进变化。
        """
        if not isinstance(plan_doc, dict):
            raise ValueError('计划 doc 必须是 dict')
        errors = batch_state.validate_plan_doc(plan_doc)
        if errors:
            raise ValueError('计划结构校验失败：%s' % ';'.join(
                '%s: %s' % (item.get('code'), item.get('message')) for item in errors[:3]))
        if not contracts.checkpoint_fits({'generate': plan_doc}, 0):
            raise CheckpointCapacityError(
                'checkpoint 超过软阈值（inflight=0，上限 %d 字节）：task=%r'
                % (contracts.checkpoint_soft_limit_bytes(0), task_key))
        with self._write_tx():
            row = self._conn.execute(
                'SELECT task_key FROM pilot_plans WHERE task_key = ?',
                (str(task_key),)).fetchone()
            if row is None:
                raise ValueError('计划 %r 不存在：amend_plan 不允许初建（用 save_plan）'
                                 % (task_key,))
            self._conn.execute(
                'UPDATE pilot_plans SET plan_json = ?, updated_at = ? WHERE task_key = ?',
                (_dumps(plan_doc), self._clock(), str(task_key)))

    def claim_job(self, task_key, job_id, run_attempt):
        """先持久化 started attempt + job running（含执行权核验）再返回，失败必须抛出。

        返回 {'ok': True, 'attemptId': 'a-…', 'requestedMaxTokens': int}；
        run_attempt 为执行器重启计数（None 视为 0，负数/非整数 ValueError）。
        """
        job_id = _non_empty_str(job_id, 'job_id')
        if run_attempt is None:
            run_attempt = 0
        if isinstance(run_attempt, bool) or not isinstance(run_attempt, int) or run_attempt < 0:
            raise ValueError('run_attempt 需要非负整数，实际 %r' % (run_attempt,))
        attempt_id = contracts.new_attempt_id()
        requested = None
        with self._write_tx():
            doc = self._load_doc(task_key)
            jobs = doc.get('jobs') if isinstance(doc.get('jobs'), dict) else {}
            job = jobs.get(job_id)
            if not isinstance(job, dict):
                raise ValueError('作业不存在：%s' % job_id)
            state = str(job.get('state') or '')
            if state == contracts.JOB_QUEUED:
                doc = batch_state.apply_event(doc, {'type': 'job_claimed', 'jobId': job_id,
                                                    'attemptId': attempt_id})
            elif state != contracts.JOB_RUNNING:
                raise ValueError('作业 %s 状态 %s，无执行权（仅 queued/running 可认领）'
                                 % (job_id, state or '<空>'))
            requested = _requested_max_tokens(doc, job)
            doc = batch_state.apply_event(doc, {
                'type': 'attempt_started', 'attemptId': attempt_id, 'jobId': job_id,
                'sequence': len(job.get('attemptIds') or []), 'runAttempt': run_attempt,
                'requestedMaxTokens': requested, 'requestFingerprint': '',
            })
            self._persist_doc(task_key, doc)
        return {'ok': True, 'attemptId': attempt_id, 'requestedMaxTokens': int(requested)}

    def commit_success(self, task_key, job_id, attempt_id, candidates, usage, result_digest):
        """同事务：候选（幂等）+ attempt succeeded/usage + job succeeded + 摘要。

        返回 True = 本次新提交成功；返回 False = 重复回调（job/attempt 均已成功），
        候选不重复写入。attempt 非 started（晚结果）或 job 非 running（取消/受阻）
        → ValueError，整体回滚。
        """
        job_id = _non_empty_str(job_id, 'job_id')
        attempt_id = _non_empty_str(attempt_id, 'attempt_id')
        candidates = [item for item in (candidates or []) if isinstance(item, dict)]
        normalized_usage = contracts.normalize_usage(usage)
        committed = True
        with self._write_tx():
            doc = self._load_doc(task_key)
            jobs = doc.get('jobs') if isinstance(doc.get('jobs'), dict) else {}
            attempts = doc.get('attempts') if isinstance(doc.get('attempts'), dict) else {}
            job = jobs.get(job_id)
            attempt = attempts.get(attempt_id)
            if not isinstance(job, dict) or not isinstance(attempt, dict):
                raise ValueError('执行权核验失败：job/attempt 不存在（%s/%s）'
                                 % (job_id, attempt_id))
            if str(attempt.get('jobId') or '') != job_id:
                raise ValueError('尝试 %s 不属于作业 %s' % (attempt_id, job_id))
            if str(job.get('state') or '') == contracts.JOB_SUCCEEDED \
                    and str(attempt.get('state') or '') == contracts.ATTEMPT_SUCCEEDED:
                committed = False   # 幂等：已提交过，跳过（不重复写候选）
            else:
                if str(attempt.get('state') or '') != contracts.ATTEMPT_STARTED:
                    raise ValueError('尝试 %s 状态 %s，非 started（晚结果拒绝）'
                                     % (attempt_id, attempt.get('state')))
                if str(job.get('state') or '') != contracts.JOB_RUNNING:
                    raise ValueError('作业 %s 状态 %s，无执行权（取消/受阻不复活）'
                                     % (job_id, job.get('state')))
                epoch = int(doc.get('planEpoch') or 0)
                doc = batch_state.apply_event(doc, {
                    'type': 'attempt_succeeded', 'attemptId': attempt_id,
                    'finishReason': 'stop', 'usage': normalized_usage,
                })
                doc = batch_state.apply_event(doc, {
                    'type': 'job_succeeded', 'jobId': job_id,
                    'candidateCount': len(candidates),
                    'resultDigest': str(result_digest or ''),
                })
                origin = {'planEpoch': epoch, 'jobId': job_id}
                seen = set()
                now = self._clock()
                for candidate in candidates:
                    cid = candidate_id_for(job_id, candidate)
                    if cid in seen:
                        continue   # 同批重复候选跳过（同 job+局部键幂等）
                    seen.add(cid)
                    payload = dict(candidate)
                    payload['candidateId'] = cid
                    payload['jobId'] = job_id
                    payload['planEpoch'] = epoch
                    self._conn.execute(
                        'INSERT OR IGNORE INTO pilot_candidates '
                        '(task_key, candidate_id, job_id, type, "key", name, fields_json, '
                        ' owner_key, evidence_json, evidence_status, origin_json, '
                        ' payload_json, created_at) '
                        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                        (str(task_key), cid, job_id, _text_or_none(candidate.get('type')),
                         _text_or_none(candidate.get('key')), _text_or_none(candidate.get('name')),
                         _json_or_none(candidate.get('fields')),
                         _text_or_none(candidate.get('ownerKey')),
                         _json_or_none(candidate.get('evidence')),
                         _text_or_none(candidate.get('evidenceStatus')),
                         _dumps(origin), _dumps(payload), now))
                self._persist_doc(task_key, doc)
        return committed

    def commit_split(self, task_key, parent_job_id, child_jobs, event_usage):
        """同事务：父 split + 全部子 job queued + 在途 attempt 记账（先落库再派发子 job）。

        child_jobs 为 job_split 事件的子作业定义列表（jobId/orderedPrimaryTargetIds/
        contextFactIds/estimate/splitPath），覆盖守恒等校验由 apply_event 承担；
        在途 started 尝试按 attempt_succeeded + finishReason='length' 收口（usage 取
        event_usage，缺省 absent）；无在途尝试的 queued 父作业直接拆分不记账。
        """
        parent_job_id = _non_empty_str(parent_job_id, 'parent_job_id')
        children = [item for item in (child_jobs or []) if isinstance(item, dict)]
        if not children:
            raise ValueError('commit_split 需要非空子作业列表')
        normalized_usage = contracts.normalize_usage(event_usage)
        with self._write_tx():
            doc = self._load_doc(task_key)
            jobs = doc.get('jobs') if isinstance(doc.get('jobs'), dict) else {}
            job = jobs.get(parent_job_id)
            if not isinstance(job, dict):
                raise ValueError('父作业不存在：%s' % parent_job_id)
            state = str(job.get('state') or '')
            if state not in (contracts.JOB_QUEUED, contracts.JOB_RUNNING):
                raise ValueError('父作业 %s 状态 %s，不可拆分' % (parent_job_id, state))
            attempts = doc.get('attempts') if isinstance(doc.get('attempts'), dict) else {}
            inflight = [aid for aid in (job.get('attemptIds') or [])
                        if str((attempts.get(aid) or {}).get('state') or '')
                        == contracts.ATTEMPT_STARTED]
            if len(inflight) > 1:
                raise ValueError('父作业 %s 有 %d 个在途尝试，拒绝拆分'
                                 % (parent_job_id, len(inflight)))
            if inflight:
                doc = batch_state.apply_event(doc, {
                    'type': 'attempt_succeeded', 'attemptId': inflight[0],
                    'finishReason': 'length', 'usage': normalized_usage,
                })
            doc = batch_state.apply_event(doc, {'type': 'job_split',
                                                'jobId': parent_job_id,
                                                'children': children})
            self._persist_doc(task_key, doc)
        return True

    def commit_failure(self, task_key, job_id, attempt_id, error_code, message, usage):
        """同事务：attempt failed（errorCode/message/usage）+ job failed。"""
        job_id = _non_empty_str(job_id, 'job_id')
        attempt_id = _non_empty_str(attempt_id, 'attempt_id')
        error_code = _non_empty_str(error_code, 'error_code')
        normalized_usage = contracts.normalize_usage(usage)
        with self._write_tx():
            doc = self._load_doc(task_key)
            jobs = doc.get('jobs') if isinstance(doc.get('jobs'), dict) else {}
            attempts = doc.get('attempts') if isinstance(doc.get('attempts'), dict) else {}
            job = jobs.get(job_id)
            attempt = attempts.get(attempt_id)
            if not isinstance(job, dict) or not isinstance(attempt, dict):
                raise ValueError('执行权核验失败：job/attempt 不存在（%s/%s）'
                                 % (job_id, attempt_id))
            if str(attempt.get('jobId') or '') != job_id:
                raise ValueError('尝试 %s 不属于作业 %s' % (attempt_id, job_id))
            if str(attempt.get('state') or '') != contracts.ATTEMPT_STARTED:
                raise ValueError('尝试 %s 状态 %s，非 started（晚结果拒绝）'
                                 % (attempt_id, attempt.get('state')))
            if str(job.get('state') or '') != contracts.JOB_RUNNING:
                raise ValueError('作业 %s 状态 %s，无执行权（取消/受阻不复活）'
                                 % (job_id, job.get('state')))
            doc = batch_state.apply_event(doc, {
                'type': 'attempt_failed', 'attemptId': attempt_id,
                'errorCode': error_code, 'message': str(message or ''),
                'usage': normalized_usage,
            })
            doc = batch_state.apply_event(doc, {
                'type': 'job_failed', 'jobId': job_id, 'errorCode': error_code,
                'message': str(message or ''),
            })
            self._persist_doc(task_key, doc)
        return True

    def mark_interrupted_unknown(self, task_key, attempt_ids):
        """崩溃恢复：started → interrupted_unknown；返回实际收口条数。

        不存在的 id / 非 started id 跳过（幂等，不抛）；全部收口在同一个写事务内。
        """
        wanted = []
        for item in (attempt_ids or []):
            text = str(item or '').strip()
            if text and text not in wanted:
                wanted.append(text)
        if not wanted:
            return 0
        marked = 0
        with self._write_tx():
            doc = self._load_doc(task_key)
            attempts = doc.get('attempts') if isinstance(doc.get('attempts'), dict) else {}
            pending = [aid for aid in wanted
                       if str((attempts.get(aid) or {}).get('state') or '')
                       == contracts.ATTEMPT_STARTED]
            for aid in pending:
                doc = batch_state.apply_event(doc, {'type': 'attempt_unknown',
                                                    'attemptId': aid})
            if pending:
                self._persist_doc(task_key, doc)
            marked = len(pending)
        return marked

    def iterate_candidates(self, task_key, page_size=200):
        """按写入序分页迭代候选；每页为候选 dict 列表（复验/终态检查必须走这里）。"""
        size = int(page_size if page_size is not None else 200)
        if size < 1:
            raise ValueError('page_size 需要正整数，实际 %r' % (page_size,))
        task_key = str(task_key)
        last_seq = 0
        while True:
            rows = self._conn.execute(
                'SELECT seq, payload_json FROM pilot_candidates '
                'WHERE task_key = ? AND seq > ? ORDER BY seq LIMIT ?',
                (task_key, last_seq, size)).fetchall()
            if not rows:
                return
            last_seq = int(rows[-1][0])
            yield [json.loads(row[1]) for row in rows]

    # --- 冷缓存（简单 KV：校准快照/估算缓存；跟 db_path 走，跨重跑共用）--------------

    def cache_put(self, cache_key, payload, ttl_seconds=None):
        """写入/覆盖一个缓存项；ttl_seconds=None 永不过期，负数 ValueError。"""
        key = str(cache_key or '').strip()
        if not key:
            raise ValueError('cache_put 需要 cache_key')
        expires_at = None
        if ttl_seconds is not None:
            ttl = float(ttl_seconds)
            if ttl < 0:
                raise ValueError('ttl_seconds 不能为负，实际 %r' % (ttl_seconds,))
            expires_at = self._clock() + ttl
        with self._write_tx():
            self._conn.execute(
                'INSERT OR REPLACE INTO pilot_cache '
                '(cache_key, payload_json, created_at, expires_at) VALUES (?, ?, ?, ?)',
                (key, _dumps(payload), self._clock(), expires_at))

    def cache_get(self, cache_key):
        """读缓存项；缺失/已过期返回 None（过期项读时惰性清理）。"""
        key = str(cache_key or '').strip()
        if not key:
            return None
        row = self._conn.execute(
            'SELECT payload_json, expires_at FROM pilot_cache WHERE cache_key = ?',
            (key,)).fetchone()
        if row is None:
            return None
        payload_json, expires_at = row
        if expires_at is not None and self._clock() >= float(expires_at):
            with self._write_tx():
                self._conn.execute('DELETE FROM pilot_cache WHERE cache_key = ?', (key,))
            return None
        return json.loads(payload_json)


def open_state(db_path, clock=None):
    """打开实验状态库：父目录自动创建，pragmas（WAL、busy_timeout=5000）。"""
    path = os.fspath(db_path)
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    return ExperimentState(path, clock=clock)
