"""本体生成控输出 v3：Persistence 在线实现（D08 交付，2026-09-22）。

契约来源：workbench/ontology_build/batch_contracts.py「持久化接口」节（D00 冻结）；
状态推进复用 batch_state.apply_event / validate_plan_doc（D07 交付，不复制状态机）；
数据访问复用 storage/ontology_build.py（本模块不拼 SQL）。

职责边界：
* OnlinePersistence(task_id, owner_user_id, run_id) 把契约的 task_key 绑定进实例；
  expected_lease 由调用方逐调用传入（首参），或经 `with_lease(lease)` 取预绑定实例；
* 每个写方法 = 一个 BEGIN IMMEDIATE 短写事务：事务内先核验**归属 + 执行权（lease）+
  取消/终态**，再 batch_state.apply_event 推进从库里现读的计划 doc，最后
  store.update_run(checkpoint=..., lease=...) 持久化——与 runner.content_tx 同一
  「先核权再写内容」纪律（SQLite 写事务串行化，核对通过后到提交前无法插入取消/轮换）；
* checkpoint 容量守卫：每次写 checkpoint 前调 contracts.checkpoint_fits（在途数=新 doc
  中 started 尝试数），超软阈值抛 CheckpointCapacityError，**不截数据、写入不发生**；
* 执行器纪律（冻结）：持久化失败一律向上抛，绝不吞异常后推进内存状态；
  commit_* 的「拒绝」（lease 失配/取消/终态/状态冲突）返回 False，属晚结果裁决而非故障。

幂等约定（契约 §持久化接口）：
* 候选 id 确定性派生：'bc-' + jobId[2:14] + '-' + sha256(局部键)[:10]；同 job 重放
  生成同 id，事务回滚后重试不会产生第二份候选；
* job 已 succeeded 的重复 commit_success 幂等跳过返回 True；已 split 的重复
  commit_split 同理；已 failed 的重复 commit_failure 同理；
* 作业处于其他终态（如 succeeded 后又收到 failure 回调）→ 拒绝返回 False，绝不改写。

候选 origin 追加 {'planEpoch': int, 'jobId': str}（既有 origin 键保留，不破坏语义）。

对冻结签名的有界附加（协议变更请求，待并入契约后为正式协议）：
* commit_success / commit_split 增加可选 kwarg `attempt_meta`（{'finishReason','bytes',
  'durationMs'}，全部可省）：契约签名未给截断/耗时信息通道，而 attempt_succeeded 事件
  支持这些字段；不传时落 None，位置参数与契约逐字一致。
"""
import hashlib
import json

from workbench.ontology_build import batch_contracts as contracts
from workbench.ontology_build import batch_state
from workbench.storage import engine as sto
from workbench.storage import ontology_build as store


class PersistenceError(Exception):
    """持久化失败基类：抛出即未保存成功，调用方绝不推进内存状态。"""


class CheckpointCapacityError(PersistenceError):
    """checkpoint 序列化超过软阈值；数据不截断、本次写入整体不发生。"""


class LeaseLostError(PersistenceError):
    """执行权失配：运行已被重试接管/轮换，晚到写入被拒（claim 场景必须抛出）。"""


class RunCancelledError(PersistenceError):
    """运行已取消/中断：结果不再写入（claim 场景必须抛出）。"""


_RUN_TERMINAL_STATES = ('succeeded', 'failed', 'cancelled', 'interrupted')


def _started_attempts(doc):
    attempts = doc.get('attempts') if isinstance(doc.get('attempts'), dict) else {}
    return sum(1 for attempt in attempts.values()
               if str((attempt or {}).get('state') or '') == contracts.ATTEMPT_STARTED)


def _generate_doc_of(row):
    """run 行 checkpoint_json → schema2 计划 doc；无 checkpoint / 非 schema2 → None。"""
    if row is None:
        return None
    try:
        raw = row['checkpoint_json']
    except (KeyError, IndexError):
        return None
    try:
        checkpoint = json.loads(raw) if raw else {}
    except (ValueError, TypeError):
        return None
    generate = checkpoint.get('generate') if isinstance(checkpoint, dict) else None
    if not isinstance(generate, dict):
        return None
    if int(generate.get('schemaVersion') or 0) != contracts.CHECKPOINT_SCHEMA_VERSION:
        return None
    return generate


class OnlinePersistence(object):
    """契约 Persistence 的在线实现（wb_build_runs.checkpoint_json['generate'] 单计划）。"""

    def __init__(self, task_id, owner_user_id, run_id, _bound_lease=''):
        self._task_id = str(task_id or '')
        self._owner = str(owner_user_id or '')
        self._run_id = str(run_id or '')
        self._bound_lease = str(_bound_lease or '')

    # --- 绑定与辅助 -----------------------------------------------------------------

    def with_lease(self, lease):
        """返回预绑定执行权的同任务实例（调用方不再逐调用传 expected_lease）。"""
        return OnlinePersistence(self._task_id, self._owner, self._run_id,
                                 _bound_lease=str(lease or ''))

    def _resolve_lease(self, expected_lease):
        """未显式传（None）用绑定 lease；显式传 '' 表示不启用 lease 条件。"""
        if expected_lease is None:
            return self._bound_lease
        return str(expected_lease or '')

    def _reject_reason(self, row, expected_lease):
        """事务内晚写入裁决：返回拒绝原因；None = 放行（归属由 get_run 保证）。"""
        lease = str(expected_lease or '')
        if lease and str(row['lease_token'] or '') != lease:
            return 'LEASE_LOST'
        if row['cancel_requested'] or str(row['state'] or '') in ('cancelled', 'interrupted'):
            return 'CANCELLED'
        if str(row['state'] or '') in ('succeeded', 'failed'):
            return 'RUN_TERMINAL'
        return None

    @staticmethod
    def _raise_for_reason(reason):
        if reason == 'LEASE_LOST':
            raise LeaseLostError('执行权已失配（运行被重试接管或轮换），claim 终止')
        if reason == 'CANCELLED':
            raise RunCancelledError('运行已取消/中断，claim 终止')
        raise PersistenceError('运行处于终态 %s，claim 终止' % reason)

    @staticmethod
    def _requested_max_tokens(doc):
        """attempt requestedMaxTokens：profile effective.outputCap → requestOutputTokens → 默认。"""
        profile = doc.get('budgetProfile') if isinstance(doc.get('budgetProfile'), dict) else {}
        effective = profile.get('effective') if isinstance(profile.get('effective'), dict) else {}
        for value in (effective.get('outputCap'), profile.get('requestOutputTokens')):
            if isinstance(value, (int, float)) and not isinstance(value, bool) and int(value) >= 1:
                return int(value)
        return contracts.DEFAULT_REQUEST_OUTPUT_TOKENS

    @staticmethod
    def _ensure_fits(doc):
        """容量守卫：超软阈值抛 CheckpointCapacityError（不截数据，写入不发生）。"""
        inflight = _started_attempts(doc)
        if not contracts.checkpoint_fits({'generate': doc}, inflight):
            raise CheckpointCapacityError(
                'checkpoint 序列化超过软阈值（%d 字节，在途 %d，软限 %d）；停止派发而非截数据'
                % (len(json.dumps({'generate': doc}, ensure_ascii=False, default=str)
                       .encode('utf-8')),
                   inflight, contracts.checkpoint_soft_limit_bytes(inflight)))

    @staticmethod
    def _candidate_payload(candidate, job_id, plan_epoch, index):
        """候选 payload 组装：确定性 id + origin 追加 planEpoch/jobId（既有 origin 保留）。"""
        payload = dict(candidate)
        local = str(candidate.get('key') or candidate.get('name') or '').strip()
        if not local:
            local = 'slot-%d' % int(index)
        # planEpoch 入哈希：跨 epoch（abstract 重建/新计划）候选 id 不冲突；
        # 同 epoch 内确定性不变（重复回调幂等依据）。
        digest = hashlib.sha256(('%s|%s' % (int(plan_epoch or 0), local)).encode('utf-8')
                                ).hexdigest()[:10]
        payload['id'] = 'bc-' + str(job_id or '')[2:14] + '-' + digest
        origin = candidate.get('origin')
        origin = dict(origin) if isinstance(origin, dict) else {}
        origin['planEpoch'] = int(plan_epoch or 0)
        origin['jobId'] = str(job_id or '')
        payload['origin'] = origin
        return payload

    def _update_checkpoint(self, conn, doc, expected_lease):
        """容量守卫 + update_run 持久化；未命中行（异常竞争）必须抛，绝不假成功。"""
        self._ensure_fits(doc)
        hit = store.update_run(conn, self._run_id, self._owner,
                               checkpoint={'generate': doc},
                               lease=str(expected_lease or '') or None)
        if not hit:
            raise PersistenceError('checkpoint 写入未命中运行行（执行权在事务内意外变更）')

    # --- 读 -------------------------------------------------------------------------

    def load(self):
        """读完整 schema2 计划；无 checkpoint / 非 schema2 / 运行不存在 → None。"""
        with sto.read_connection() as conn:
            row = store.get_run(conn, self._run_id, self._owner)
        return _generate_doc_of(row)

    # --- 写：契约方法 ---------------------------------------------------------------

    def save_plan(self, plan_doc, expected_lease=None):
        """初次建计划（原子）；已有 schema2 计划拒绝覆盖；结构/容量非法绝不落库。"""
        expected_lease = self._resolve_lease(expected_lease)
        if not isinstance(plan_doc, dict):
            raise PersistenceError('save_plan 需要 schema2 计划 dict')
        errors = batch_state.validate_plan_doc(plan_doc)
        if errors:
            raise PersistenceError('计划结构校验未通过：%s' % errors[0].get('message'))
        self._ensure_fits(plan_doc)

        def body(conn):
            row = store.get_run(conn, self._run_id, self._owner)
            if row is None:
                raise PersistenceError('运行不存在或不属于当前账号：%s' % self._run_id)
            reason = self._reject_reason(row, expected_lease)
            if reason:
                self._raise_for_reason(reason)
            if _generate_doc_of(row) is not None:
                raise PersistenceError('schema2 计划已存在（save_plan 只用于初次建计划，'
                                       '重排/重建须新建运行）')
            self._update_checkpoint(conn, plan_doc, expected_lease)

        with sto.write_tx() as tx:
            tx.run(body)

    def amend_plan(self, plan_doc, expected_lease=None):
        """计划内事件修正（attempt 记账/重排队/blocking）：核验执行权后整体替换。

        与 save_plan 的差异：save_plan 是「初建、已有计划拒绝覆盖」；amend_plan 用于已存在
        计划内的合法状态推进（执行器 apply_event 后落库的唯一通道）。结构/容量校验与
        save_plan 同一口径；拒绝（lease 失配/取消/终态）抛异常，绝不静默丢弃事件。
        """
        expected_lease = self._resolve_lease(expected_lease)
        if not isinstance(plan_doc, dict):
            raise PersistenceError('amend_plan 需要 schema2 计划 dict')
        errors = batch_state.validate_plan_doc(plan_doc)
        if errors:
            raise PersistenceError('计划结构校验未通过：%s' % errors[0].get('message'))
        self._ensure_fits(plan_doc)

        def body(conn):
            row = store.get_run(conn, self._run_id, self._owner)
            if row is None:
                raise PersistenceError('运行不存在或不属于当前账号：%s' % self._run_id)
            reason = self._reject_reason(row, expected_lease)
            if reason:
                self._raise_for_reason(reason)
            self._update_checkpoint(conn, plan_doc, expected_lease)

        with sto.write_tx() as tx:
            tx.run(body)

    def claim_job(self, expected_lease, job_id, run_attempt):
        """先持久化（job running + attempt started）再返回；任何失败必须抛出。

        事务内核验 lease 与取消 → apply_event(job_claimed → attempt_started) →
        容量守卫 → update_run；返回 {'ok': True, 'attemptId', 'requestedMaxTokens'}。
        """
        expected_lease = self._resolve_lease(expected_lease)
        attempt_id = contracts.new_attempt_id()

        def body(conn):
            row = store.get_run(conn, self._run_id, self._owner)
            if row is None:
                raise PersistenceError('运行不存在或不属于当前账号：%s' % self._run_id)
            reason = self._reject_reason(row, expected_lease)
            if reason:
                self._raise_for_reason(reason)
            doc = _generate_doc_of(row)
            if doc is None:
                raise PersistenceError('无 schema2 计划，无法认领作业')
            job = (doc.get('jobs') or {}).get(str(job_id or ''))
            if job is None:
                raise ValueError('作业不存在：%s' % job_id)
            max_tokens = self._requested_max_tokens(doc)
            sequence = len(job.get('attemptIds') or [])
            doc = batch_state.apply_event(doc, {'type': batch_state.EVENT_JOB_CLAIMED,
                                                'jobId': job_id, 'attemptId': attempt_id})
            doc = batch_state.apply_event(doc, {'type': batch_state.EVENT_ATTEMPT_STARTED,
                                                'jobId': job_id, 'attemptId': attempt_id,
                                                'sequence': sequence,
                                                'runAttempt': int(run_attempt or 0),
                                                'requestedMaxTokens': max_tokens,
                                                'requestFingerprint': ''})
            self._update_checkpoint(conn, doc, expected_lease)
            return {'ok': True, 'attemptId': attempt_id, 'requestedMaxTokens': max_tokens}

        with sto.write_tx() as tx:
            return tx.run(body)

    def commit_success(self, expected_lease, job_id, attempt_id, candidates, usage,
                       result_digest, attempt_meta=None):
        """同事务：候选（幂等 id）+ attempt succeeded/usage + job succeeded + 摘要。

        拒绝（lease 失配/取消/终态/状态冲突）返回 False；重复回调（job 已 succeeded）
        幂等跳过返回 True；持久化失败抛 PersistenceError，绝不返回假 ok。
        """
        expected_lease = self._resolve_lease(expected_lease)
        items = [item for item in (candidates or []) if isinstance(item, dict)]
        meta = attempt_meta if isinstance(attempt_meta, dict) else {}

        def body(conn):
            row = store.get_run(conn, self._run_id, self._owner)
            if row is None:
                raise PersistenceError('运行不存在或不属于当前账号：%s' % self._run_id)
            doc = _generate_doc_of(row)
            job = (doc.get('jobs') or {}).get(str(job_id or '')) if doc else None
            if job is not None and str(job.get('state') or '') == contracts.JOB_SUCCEEDED:
                # 幂等跳过（候选与状态已原子落库）：返回 False = 本次未新提交（契约冻结
                # bool 语义 True=新提交 / False=幂等跳过；与 ExperimentState 口径一致）。
                return False
            reason = self._reject_reason(row, expected_lease)
            if reason:
                return False
            if doc is None:
                raise PersistenceError('无 schema2 计划，无法提交结果')
            if job is None:
                raise ValueError('作业不存在：%s' % job_id)
            if str(job.get('state') or '') != contracts.JOB_RUNNING:
                return False  # 作业处于其他终态（failed/blocked/split…）：拒绝改写
            batch_id = str(row['batch_id'] or '')
            plan_epoch = doc.get('planEpoch')
            for index, candidate in enumerate(items):
                payload = self._candidate_payload(candidate, job_id, plan_epoch, index)
                store.create_candidate(conn, self._task_id, self._owner, batch_id, payload)
            doc = batch_state.apply_event(doc, {'type': batch_state.EVENT_ATTEMPT_SUCCEEDED,
                                                'attemptId': attempt_id, 'usage': usage,
                                                'finishReason': meta.get('finishReason'),
                                                'bytes': meta.get('bytes'),
                                                'durationMs': meta.get('durationMs')})
            doc = batch_state.apply_event(doc, {'type': batch_state.EVENT_JOB_SUCCEEDED,
                                                'jobId': job_id,
                                                'candidateCount': len(items),
                                                'resultDigest': str(result_digest or '')})
            self._update_checkpoint(conn, doc, expected_lease)
            return True

        with sto.write_tx() as tx:
            return tx.run(body)

    def commit_split(self, expected_lease, parent_job_id, child_jobs, usage,
                     finish_reason=None):
        """同事务：父作业在途尝试记账（usage 入总账）+ 父 split + 全部子 job 入计划。

        先持久化再派发子 job（契约 ③）；重复回调（父已 split）幂等返回 True；
        拒绝（lease 失配/取消/终态）返回 False；拆分不守恒由 batch_state 拒绝并抛出。
        """
        expected_lease = self._resolve_lease(expected_lease)
        children = [child for child in (child_jobs or []) if isinstance(child, dict)]

        def body(conn):
            row = store.get_run(conn, self._run_id, self._owner)
            if row is None:
                raise PersistenceError('运行不存在或不属于当前账号：%s' % self._run_id)
            reason = self._reject_reason(row, expected_lease)
            if reason:
                return False
            doc = _generate_doc_of(row)
            if doc is None:
                raise PersistenceError('无 schema2 计划，无法提交拆分')
            job = (doc.get('jobs') or {}).get(str(parent_job_id or ''))
            if job is None:
                raise ValueError('作业不存在：%s' % parent_job_id)
            if str(job.get('state') or '') == contracts.JOB_SPLIT:
                return False  # 幂等跳过（冻结 bool 语义：True=新提交 / False=跳过）
            if str(job.get('state') or '') != contracts.JOB_RUNNING:
                return False  # 其他终态：拒绝改写
            attempts = doc.get('attempts') if isinstance(doc.get('attempts'), dict) else {}
            for attempt_id in job.get('attemptIds') or []:
                attempt = attempts.get(str(attempt_id)) or {}
                if str(attempt.get('state') or '') == contracts.ATTEMPT_STARTED:
                    doc = batch_state.apply_event(
                        doc, {'type': batch_state.EVENT_ATTEMPT_SUCCEEDED,
                              'attemptId': attempt_id, 'usage': usage,
                              'finishReason': finish_reason})
            doc = batch_state.apply_event(doc, {'type': batch_state.EVENT_JOB_SPLIT,
                                                'jobId': parent_job_id, 'children': children})
            self._update_checkpoint(conn, doc, expected_lease)
            return True

        with sto.write_tx() as tx:
            return tx.run(body)

    def commit_failure(self, expected_lease, job_id, attempt_id, error_code, message, usage):
        """同事务：attempt failed（错误分类+有界消息+usage）+ job failed。"""
        expected_lease = self._resolve_lease(expected_lease)

        def body(conn):
            row = store.get_run(conn, self._run_id, self._owner)
            if row is None:
                raise PersistenceError('运行不存在或不属于当前账号：%s' % self._run_id)
            reason = self._reject_reason(row, expected_lease)
            if reason:
                return False
            doc = _generate_doc_of(row)
            if doc is None:
                raise PersistenceError('无 schema2 计划，无法提交失败')
            job = (doc.get('jobs') or {}).get(str(job_id or ''))
            if job is None:
                raise ValueError('作业不存在：%s' % job_id)
            if str(job.get('state') or '') == contracts.JOB_FAILED:
                return False  # 幂等跳过（冻结 bool 语义：True=新提交 / False=跳过）
            if str(job.get('state') or '') != contracts.JOB_RUNNING:
                return False  # 其他终态（如已 succeeded）：拒绝改写
            doc = batch_state.apply_event(doc, {'type': batch_state.EVENT_ATTEMPT_FAILED,
                                                'attemptId': attempt_id,
                                                'errorCode': error_code,
                                                'message': message, 'usage': usage})
            doc = batch_state.apply_event(doc, {'type': batch_state.EVENT_JOB_FAILED,
                                                'jobId': job_id, 'errorCode': error_code,
                                                'message': message})
            self._update_checkpoint(conn, doc, expected_lease)
            return True

        with sto.write_tx() as tx:
            return tx.run(body)

    def mark_interrupted_unknown(self, attempt_ids):
        """崩溃恢复扫描：started → interrupted_unknown；返回实际收口的尝试数。

        原执行权已随崩溃消失，本写入不带 lease 条件（带旧 lease 必然失配、无法恢复）；
        usage 聚合按 unknown 记次（真实计费可能发生，显示未知，不计 0）。
        """
        def body(conn):
            row = store.get_run(conn, self._run_id, self._owner)
            if row is None:
                return 0
            doc = _generate_doc_of(row)
            if doc is None:
                return 0
            attempts = doc.get('attempts') if isinstance(doc.get('attempts'), dict) else {}
            changed = 0
            for attempt_id in (attempt_ids or []):
                attempt = attempts.get(str(attempt_id or '')) or {}
                if str(attempt.get('state') or '') != contracts.ATTEMPT_STARTED:
                    continue
                doc = batch_state.apply_event(doc, {'type': batch_state.EVENT_ATTEMPT_UNKNOWN,
                                                    'attemptId': attempt_id})
                changed += 1
            if changed:
                self._update_checkpoint(conn, doc, '')
            return changed

        with sto.write_tx() as tx:
            return tx.run(body)

    # --- 候选分页迭代 ----------------------------------------------------------------

    def iterate_candidates(self, conn=None, page_size=200):
        """keyset 分页迭代任务全部候选（按 candidate_id 升序；含已合并）。

        产出 list[candidate dict]（每页 ≤page_size 条）；复验/终态检查必须走这里，
        不复用 all_candidates(limit=500) 当全量。conn 传入时复用（不关闭），
        缺省自开只读连接（迭代结束关闭）。
        """
        return self._candidate_pages(conn, page_size)

    def _candidate_pages(self, conn, page_size):
        size = max(1, min(int(page_size or 200), 2000))
        own = conn is None
        active = sto.read_connection() if own else conn
        try:
            after = ''
            while True:
                page = store.iterate_candidates_page(active, self._task_id, self._owner,
                                                     after, size)
                if not page:
                    return
                yield page
                if len(page) < size:
                    return
                after = str(page[-1].get('id') or '')
        finally:
            if own:
                active.close()
