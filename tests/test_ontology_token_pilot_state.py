# -*- coding: utf-8 -*-
"""D12 试验 SQLite 适配与隔离缓存回归（experiments/ontology_token_pilot/state.py）。

被测模块：experiments/ontology_token_pilot/state.py（Persistence 契约的实验 SQLite
双实现之一）。契约来源：workbench/ontology_build/batch_contracts.py「持久化接口」节
（D00 冻结）；状态机复用 workbench/ontology_build/batch_state.py（D07 交付）。

场景（整合计划 v3 §8 D12 行验收：复用状态机、独立目录、冷热缓存分开）：
 1. save_plan→load 往返一致；缺失返回 None；异指纹拒绝覆盖、同指纹幂等重存；
    open_state 父目录自动创建。
 2. claim_job：started attempt + job running 先落库（claim 后另开连接读库断言）；
    终态/缺失作业无执行权 → ValueError；requestedMaxTokens 按 estimate 派生。
 3. commit_success 原子：候选+attempt+job 同事务；候选表被 drop → 整体回滚、
    job 仍 running、attempt 仍 started、doc 与提交前逐字节一致（无部分状态）；
    正常路径候选行/usage 规范化/coverage/resultDigest 齐。
 4. 幂等：同一 job 重复 commit_success → 返回 False、候选不重复、doc 不变。
 5. commit_split 父子同事务（父 split+子 queued+在途 attempt 按 length 收口）；
    子 job 可 claim；拆分不守恒 → ValueError 且整体回滚。
 6. mark_interrupted_unknown：started→interrupted_unknown；usage 聚合
    unknownUsageCalls=1、token 不计 0；重复/未知 id 幂等返回 0。
 7. iterate_candidates：501 条候选逐页迭代恰 501、无重无漏、写入序稳定；
    跨 job 按提交顺序排列。
 8. checkpoint 容量：超软阈值计划 save_plan 抛 CheckpointCapacityError 且库内不变；
    commit 路径超限同样整体回滚（注入 MAX_CHECKPOINT_BYTES 后还原）。
 9. 冷热缓存：cache_put/get 往返、覆盖写、ttl=0 与注入 clock 过期返回 None、
    无 ttl 永不过期、跨实例（跨重跑）共用。
 10. 隔离：两个 db_path 互不干扰（计划/缓存/候选互不可见）；模块未 import
    workbench.storage。

隔离（AGENTS.md 测试铁律）：全部 tempfile 临时目录且用完销毁，不触业务 ROOT/真实
数据，不 import 存储层（实验库为独立 sqlite3 文件），不访问网络，全部合成数据。

运行：python3 tests/run.py --test tests/test_ontology_token_pilot_state.py
"""
import copy
import json
import sqlite3
import sys
import tempfile
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from workbench.ontology_build import batch_contracts as contracts  # noqa: E402
from workbench.ontology_build import batch_state  # noqa: E402
from experiments.ontology_token_pilot import state as pilot_state  # noqa: E402

PASSED = []
FAILED = []
SEQ = [0]


def _short(value, limit=400):
    try:
        text = repr(value)
    except Exception:  # noqa: BLE001 - 摘要展示绝不影响断言流程
        text = str(value)
    return text if len(text) <= limit else text[:limit] + '…'


def check(cond, message, actual=None, expected=None):
    SEQ[0] += 1
    if cond:
        PASSED.append(message)
        print('通过 %d) %s' % (SEQ[0], message))
        return True
    FAILED.append(message)
    print('[失败] %d) %s' % (SEQ[0], message))
    if expected is not None:
        print('  预期: ' + _short(expected))
    if actual is not None:
        print('  实际: ' + _short(actual))
    return False


def check_raises(fn, message, expected_type=ValueError):
    try:
        fn()
    except expected_type as exc:
        return check(True, message + '（%s: %s）' % (type(exc).__name__, _short(exc, 120)))
    except Exception as exc:  # noqa: BLE001 - 必须是预期类型，其余都算失败
        return check(False, message, actual='%s: %s' % (type(exc).__name__, exc),
                     expected=expected_type.__name__)
    return check(False, message, actual='未抛出异常', expected=expected_type.__name__)


def capture_exception(fn):
    try:
        fn()
    except Exception as exc:  # noqa: BLE001 - Persistence 抛异常 = 未保存，类型由用例断言
        return exc
    return None


# --- 合成数据 ------------------------------------------------------------------------

PROFILE = {
    'enabled': True, 'providerId': 'prov-1', 'model': 'model-x',
    'contextTokens': 100000, 'outputLimitTokens': 32000, 'requestOutputTokens': 32000,
    'targetRatio': 0.5, 'limitsSource': 'configured', 'errors': [],
    'effective': {'outputCap': 32000, 'targetBudget': 16000, 'inputReserve': 2048},
}
FINGERPRINT_PARTS = ['facts-digest-abc', '1', '1', 'prov-1', 'model-x', 'v3', 'legacy-v1',
                     'pilot-profile']
USAGE_OK = {'promptTokens': 100, 'completionTokens': 200, 'reasoningTokens': 10,
            'totalTokens': 300, 'usageSource': 'api'}


def make_targets(count):
    return [{'targetId': 't-%d' % n, 'factId': 'f-%d' % n, 'selector': {'type': 'whole'},
             'kind': 'ddl_table', 'subjectKey': 'subject-%d' % n, 'materialId': 'm-1'}
            for n in range(1, count + 1)]


def make_plan(target_count=3):
    return batch_state.create_plan('pilot-batch', 1, 1, make_targets(target_count), PROFILE,
                                   contracts.CODEC_LEGACY, FINGERPRINT_PARTS)


def event_created(job_id, target_ids):
    return {'type': 'job_created', 'jobId': job_id,
            'orderedPrimaryTargetIds': list(target_ids), 'contextFactIds': [],
            'estimate': {'slots': len(target_ids), 'expectedOutputTokens': 4096}}


def make_candidate(n):
    """normalized candidate dict（与 output_codec 解码输出同形的最小合成样本）。"""
    return {'key': 'k-%03d' % n, 'type': 'property', 'name': '属性%03d' % n,
            'definition': '定义%03d' % n, 'fields': ['f-%03d' % n], 'ownerKey': 'cell',
            'evidence': {'name': ['f-%03d' % n]}, 'evidenceStatus': 'supported',
            'conflicts': [], 'rejectedRefs': 0}


def seed_plan(st, task_key, job_id, target_count, extra_events=None):
    """建计划并登记一个覆盖全部目标的 queued 作业，返回初始 doc。"""
    doc = make_plan(target_count)
    doc = batch_state.apply_event(doc, event_created(job_id,
                                                     ['t-%d' % n for n in
                                                      range(1, target_count + 1)]))
    for event in (extra_events or []):
        doc = batch_state.apply_event(doc, event)
    st.save_plan(task_key, doc)
    return doc


def claim(st, task_key='task-1', job_id='j-1', run_attempt=0):
    result = st.claim_job(task_key, job_id, run_attempt)
    return result['attemptId']


# --- 场景 1：save_plan→load 往返一致 ---------------------------------------------------


def scenario_save_load_roundtrip():
    tag = '场景1 save_plan/load 往返'
    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / 'nested' / 'dir' / 'pilot.sqlite3')
        st = pilot_state.open_state(db)   # 父目录自动创建
        try:
            doc = seed_plan(st, 'task-1', 'j-1', 3)
            loaded = st.load('task-1')
            check(loaded == doc, '%s：load 与 save 的 plan_doc 完全一致（含 jobs/attempts）'
                  % tag, actual=_short(loaded), expected=_short(doc))
            check(st.load('missing-task') is None, '%s：缺失 task_key 返回 None' % tag)
            check(batch_state.validate_plan_doc(loaded) == [],
                  '%s：往返后的 doc 通过 validate_plan_doc 零错误' % tag)
            # 同指纹幂等重存（崩溃重跑安全）
            st.save_plan('task-1', copy.deepcopy(doc))
            check(st.load('task-1') == doc, '%s：同指纹重存幂等覆盖' % tag)
            # 异指纹拒绝（指纹由 parts 决定，换 parts 即换计划身份）
            other = batch_state.create_plan('other-batch', 1, 1, make_targets(2), PROFILE,
                                            contracts.CODEC_LEGACY,
                                            FINGERPRINT_PARTS + ['other-parts'])
            other = batch_state.apply_event(other, event_created('j-x', ['t-1', 't-2']))
            check(other['fingerprint'] != doc['fingerprint'],
                  '%s：异 parts 构造出不同指纹' % tag)
            check_raises(lambda: st.save_plan('task-1', other),
                         '%s：已存在计划异指纹覆盖 → ValueError' % tag)
            check(st.load('task-1') == doc, '%s：拒绝覆盖后原计划不变' % tag)
            # 非法结构拒绝
            bad = copy.deepcopy(doc)
            bad['jobs']['j-1']['state'] = 'unknown-state'
            check_raises(lambda: st.save_plan('task-1', bad),
                         '%s：结构非法的计划 → ValueError' % tag)
            check(st.load('task-1') == doc, '%s：非法计划未写入（原计划不变）' % tag)
            # 第二个实例（跨连接）读同一库
            st2 = pilot_state.open_state(db)
            try:
                check(st2.load('task-1') == doc, '%s：另开实例读同一 db_path 一致' % tag)
            finally:
                st2.close()
        finally:
            st.close()


# --- 场景 2：claim_job started 先落库 --------------------------------------------------


def scenario_claim_job_persists_first():
    tag = '场景2 claim_job 先持久化'
    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / 'pilot.sqlite3')
        st = pilot_state.open_state(db)
        try:
            seed_plan(st, 'task-1', 'j-1', 3)
            result = st.claim_job('task-1', 'j-1', 2)
            check(result.get('ok') is True and str(result.get('attemptId', '')).startswith('a-')
                  and result.get('attemptId'), '%s：claim 返回 ok=True 与 a- 前缀 attemptId'
                  % tag, actual=result)
            check(result.get('requestedMaxTokens') == 4096,
                  '%s：requestedMaxTokens 按 job.estimate.expectedOutputTokens 派生' % tag,
                  actual=result.get('requestedMaxTokens'), expected=4096)
            # 另开连接读库断言：started attempt + job running 已落库
            raw = sqlite3.connect(db).execute(
                'SELECT plan_json FROM pilot_plans WHERE task_key = ?', ('task-1',)
            ).fetchone()
            persisted = json.loads(raw[0])
            job = persisted['jobs']['j-1']
            attempt = persisted['attempts'][result['attemptId']]
            check(job['state'] == contracts.JOB_RUNNING,
                  '%s：另开连接读库——job 已 running' % tag, actual=job['state'])
            check(attempt['state'] == contracts.ATTEMPT_STARTED
                  and attempt['runAttempt'] == 2 and attempt['jobId'] == 'j-1'
                  and job['attemptIds'] == [result['attemptId']],
                  '%s：另开连接读库——started attempt 已落库且挂到作业' % tag,
                  actual=attempt)
            check(st.load('task-1') == persisted,
                  '%s：内存 load 与库内原始行一致' % tag)
            # running 作业再 claim（崩溃恢复后续尝试）：追加新 attempt
            second = st.claim_job('task-1', 'j-1', 0)
            doc = st.load('task-1')
            check(second['attemptId'] != result['attemptId']
                  and len(doc['jobs']['j-1']['attemptIds']) == 2
                  and doc['jobs']['j-1']['state'] == contracts.JOB_RUNNING,
                  '%s：running 作业可追加新 attempt（恢复场景）' % tag)
            check_raises(lambda: st.claim_job('task-1', 'j-1', -1),
                         '%s：负 run_attempt → ValueError' % tag)
            # 缺失/终态作业无执行权
            check_raises(lambda: st.claim_job('task-1', 'j-none', 0),
                         '%s：不存在的作业 claim → ValueError' % tag)
            # 先收口第一个在途尝试（崩溃恢复口径），再对第二个走 commit_failure——
            # 状态机要求作业进入终态前无任何 started 残留（batch_state._require_no_inflight）
            check(st.mark_interrupted_unknown('task-1', [result['attemptId']]) == 1,
                  '%s：旧在途尝试先按 interrupted_unknown 收口' % tag)
            st.commit_failure('task-1', 'j-1', second['attemptId'], 'NETWORK_RETRYABLE', '超时',
                              None)
            check_raises(lambda: st.claim_job('task-1', 'j-1', 0),
                         '%s：failed 终态作业 claim → ValueError' % tag)
        finally:
            st.close()


# --- 场景 3：commit_success 原子性 ------------------------------------------------------


def scenario_commit_success_atomic():
    tag = '场景3 commit_success 原子'
    # 3a 注入故障：候选表被 drop → 整体回滚，无部分状态
    with tempfile.TemporaryDirectory() as tmp:
        st = pilot_state.open_state(str(Path(tmp) / 'pilot.sqlite3'))
        try:
            seed_plan(st, 'task-1', 'j-1', 3)
            attempt_id = claim(st)
            before = st.load('task-1')
            ext = sqlite3.connect(st.db_path)
            ext.execute('DROP TABLE pilot_candidates')
            ext.commit()
            ext.close()
            raised = capture_exception(lambda: st.commit_success(
                'task-1', 'j-1', attempt_id, [make_candidate(1)], USAGE_OK, 'digest-1'))
            check(raised is not None, '%s：候选表被 drop → commit 抛异常（Persistence 抛异常'
                  ' = 未保存）' % tag, actual=type(raised).__name__ if raised else None)
            after = st.load('task-1')
            check(after == before, '%s：整体回滚——doc 与提交前完全一致'
                  '（job running、attempt started、usageAggregate 无部分状态）' % tag,
                  actual=_short(after), expected=_short(before))
            check(after['jobs']['j-1']['state'] == contracts.JOB_RUNNING
                  and after['attempts'][attempt_id]['state'] == contracts.ATTEMPT_STARTED,
                  '%s：job 仍 running、attempt 仍 started（无部分终态）' % tag)
            raw = sqlite3.connect(st.db_path).execute(
                'SELECT plan_json FROM pilot_plans WHERE task_key = ?',
                ('task-1',)).fetchone()
            check(json.loads(raw[0]) == before, '%s：库内原始行未变' % tag)
        finally:
            st.close()
    # 3b 正常路径：候选+attempt+job 状态齐
    with tempfile.TemporaryDirectory() as tmp:
        st = pilot_state.open_state(str(Path(tmp) / 'pilot.sqlite3'))
        try:
            seed_plan(st, 'task-1', 'j-1', 3)
            attempt_id = claim(st)
            committed = st.commit_success('task-1', 'j-1', attempt_id,
                                          [make_candidate(1), make_candidate(2)],
                                          USAGE_OK, 'digest-ok')
            check(committed is True, '%s：首次 commit_success 返回 True' % tag)
            doc = st.load('task-1')
            job = doc['jobs']['j-1']
            attempt = doc['attempts'][attempt_id]
            check(job['state'] == contracts.JOB_SUCCEEDED
                  and job['candidateCount'] == 2 and job['resultDigest'] == 'digest-ok',
                  '%s：job succeeded + candidateCount + resultDigest 齐' % tag, actual=job)
            check(attempt['state'] == contracts.ATTEMPT_SUCCEEDED
                  and attempt['usage']['completionTokens'] == 200
                  and attempt['usage']['usageSource'] == 'api'
                  and attempt['finishReason'] == 'stop',
                  '%s：attempt succeeded、usage 规范化（reasoning 不双计）、finishReason=stop'
                  % tag, actual=attempt)
            check(doc['coverage'] == {'targetTotal': 3, 'targetCompleted': 3,
                                      'targetPending': 0},
                  '%s：coverage 联动（成功叶覆盖全部目标）' % tag, actual=doc['coverage'])
            check(doc['usageAggregate']['calls'] == 1
                  and doc['usageAggregate']['completionTokens'] == 200
                  and doc['usageAggregate']['unknownUsageCalls'] == 0,
                  '%s：usageAggregate 按已收口尝试重算' % tag, actual=doc['usageAggregate'])
            rows = sqlite3.connect(st.db_path).execute(
                'SELECT candidate_id, job_id, "key", origin_json, payload_json '
                'FROM pilot_candidates ORDER BY seq').fetchall()
            check(len(rows) == 2 and rows[0][1] == 'j-1'
                  and rows[0][2] == 'k-001' and rows[1][2] == 'k-002',
                  '%s：候选结构化列落库（type/key/name/…）' % tag,
                  actual=[(r[0], r[2]) for r in rows])
            origin = json.loads(rows[0][3])
            check(origin == {'planEpoch': doc['planEpoch'], 'jobId': 'j-1'},
                  '%s：候选 origin 带 planEpoch/jobId' % tag, actual=origin)
            payload = json.loads(rows[0][4])
            check(payload.get('candidateId') == rows[0][0]
                  and payload.get('key') == 'k-001' and payload.get('evidenceStatus') ==
                  'supported' and payload.get('definition') == '定义001',
                  '%s：payload_json 全量零丢失且带 candidateId' % tag, actual=payload)
            check(rows[0][0] == 'c-j-1-k-001',
                  '%s：candidate_id = c-<jobId[:12]>-<局部键> 确定性派生' % tag,
                  actual=rows[0][0])
        finally:
            st.close()


# --- 场景 4：同一 job 重复 commit_success 幂等 ------------------------------------------


def scenario_commit_success_idempotent():
    tag = '场景4 commit_success 幂等'
    with tempfile.TemporaryDirectory() as tmp:
        st = pilot_state.open_state(str(Path(tmp) / 'pilot.sqlite3'))
        try:
            seed_plan(st, 'task-1', 'j-1', 3)
            attempt_id = claim(st)
            st.commit_success('task-1', 'j-1', attempt_id, [make_candidate(1)],
                              USAGE_OK, 'digest-1')
            before = st.load('task-1')
            again = st.commit_success('task-1', 'j-1', attempt_id,
                                      [make_candidate(1), make_candidate(2)],
                                      USAGE_OK, 'digest-1')
            check(again is False, '%s：重复 commit_success 返回 False（跳过）' % tag,
                  actual=again)
            rows = sqlite3.connect(st.db_path).execute(
                'SELECT COUNT(*) FROM pilot_candidates').fetchone()[0]
            check(rows == 1, '%s：候选不重复（仍 1 条，不是 3 条）' % tag, actual=rows)
            check(st.load('task-1') == before, '%s：重复回调后 doc 不变' % tag)
            # 另一 attempt 对已成功作业的晚结果 → 拒绝而非吞掉
            check_raises(lambda: st.commit_success('task-1', 'j-1', attempt_id + 'x',
                                                   [], USAGE_OK, 'd'),
                         '%s：非本作业在途 attempt 的晚结果 → ValueError' % tag)
        finally:
            st.close()


# --- 场景 5：commit_split 父子同事务 -----------------------------------------------------


def scenario_commit_split():
    tag = '场景5 commit_split 父子同事务'
    with tempfile.TemporaryDirectory() as tmp:
        st = pilot_state.open_state(str(Path(tmp) / 'pilot.sqlite3'))
        try:
            seed_plan(st, 'task-1', 'j-1', 4)
            attempt_id = claim(st)
            children = [
                {'jobId': 'j-1a', 'orderedPrimaryTargetIds': ['t-1', 't-2'],
                 'contextFactIds': [], 'estimate': {'slots': 2}},
                {'jobId': 'j-1b', 'orderedPrimaryTargetIds': ['t-3', 't-4'],
                 'contextFactIds': [], 'estimate': {'slots': 2}},
            ]
            usage = {'completionTokens': 77, 'usageSource': 'api'}
            committed = st.commit_split('task-1', 'j-1', children, usage)
            check(committed is True, '%s：commit_split 返回 True' % tag)
            doc = st.load('task-1')
            parent = doc['jobs']['j-1']
            check(parent['state'] == contracts.JOB_SPLIT
                  and parent['children'] == ['j-1a', 'j-1b'],
                  '%s：父作业 split 且 children 落库' % tag, actual=parent)
            check(doc['attempts'][attempt_id]['state'] == contracts.ATTEMPT_SUCCEEDED
                  and doc['attempts'][attempt_id]['finishReason'] == 'length'
                  and doc['attempts'][attempt_id]['usage']['completionTokens'] == 77,
                  '%s：在途 attempt 按 length 收口并记账 usage' % tag,
                  actual=doc['attempts'][attempt_id])
            check(doc['jobs']['j-1a']['state'] == contracts.JOB_QUEUED
                  and doc['jobs']['j-1b']['state'] == contracts.JOB_QUEUED
                  and doc['jobs']['j-1a']['parentId'] == 'j-1'
                  and doc['jobs']['j-1b']['rootId'] == 'j-1',
                  '%s：子作业 queued 且 parentId/rootId 正确（同事务）' % tag,
                  actual={'a': doc['jobs']['j-1a'], 'b': doc['jobs']['j-1b']})
            check(doc['coverage'] == {'targetTotal': 4, 'targetCompleted': 0,
                                      'targetPending': 4}
                  and doc['pendingTargetIds'] == [],
                  '%s：父 split 不算成功（完成 0）；目标全被子作业认领（pending 列表空）'
                  % tag,
                  actual={'coverage': doc['coverage'],
                          'pending': doc['pendingTargetIds']})
            check(batch_state.validate_plan_doc(doc) == [],
                  '%s：split 后计划结构零错误' % tag)
            # 子 job 可 claim
            child_attempt = claim(st, job_id='j-1a', run_attempt=0)
            doc = st.load('task-1')
            check(doc['jobs']['j-1a']['state'] == contracts.JOB_RUNNING
                  and doc['attempts'][child_attempt]['jobId'] == 'j-1a',
                  '%s：子 job 可 claim（running + started attempt）' % tag)
        finally:
            st.close()
    # 拆分不守恒 → ValueError 且整体回滚
    with tempfile.TemporaryDirectory() as tmp:
        st = pilot_state.open_state(str(Path(tmp) / 'pilot.sqlite3'))
        try:
            seed_plan(st, 'task-1', 'j-1', 4)
            attempt_id = claim(st)
            before = st.load('task-1')
            bad_children = [{'jobId': 'j-1a', 'orderedPrimaryTargetIds': ['t-1'],
                             'contextFactIds': []}]
            check_raises(lambda: st.commit_split('task-1', 'j-1', bad_children, None),
                         '%s：子目标不覆盖父目标 → ValueError' % tag)
            check(st.load('task-1') == before,
                  '%s：非法拆分整体回滚（父仍 running、attempt 仍 started）' % tag)
        finally:
            st.close()


# --- 场景 6：mark_interrupted_unknown ----------------------------------------------------


def scenario_mark_interrupted_unknown():
    tag = '场景6 mark_interrupted_unknown'
    with tempfile.TemporaryDirectory() as tmp:
        st = pilot_state.open_state(str(Path(tmp) / 'pilot.sqlite3'))
        try:
            seed_plan(st, 'task-1', 'j-1', 3)
            attempt_id = claim(st)
            marked = st.mark_interrupted_unknown('task-1', [attempt_id])
            check(marked == 1, '%s：started 收口 1 条' % tag, actual=marked)
            doc = st.load('task-1')
            attempt = doc['attempts'][attempt_id]
            check(attempt['state'] == contracts.ATTEMPT_INTERRUPTED_UNKNOWN,
                  '%s：attempt 状态 interrupted_unknown' % tag, actual=attempt)
            aggregate = doc['usageAggregate']
            check(aggregate['unknownUsageCalls'] == 1 and aggregate['calls'] == 1
                  and aggregate['completionTokens'] is None
                  and aggregate['knownCompletionTokens'] == 0,
                  '%s：usage 聚合 unknownUsageCalls=1、token 不计 0' % tag,
                  actual=aggregate)
            check(batch_state.validate_plan_doc(doc) == [],
                  '%s：收口后计划结构零错误' % tag)
            check(st.mark_interrupted_unknown('task-1', [attempt_id]) == 0,
                  '%s：重复收口幂等返回 0' % tag)
            check(st.mark_interrupted_unknown('task-1', ['a-missing', '', None]) == 0,
                  '%s：未知/空 id 跳过返回 0' % tag)
            check(st.mark_interrupted_unknown('task-1', []) == 0,
                  '%s：空列表返回 0' % tag)
        finally:
            st.close()


# --- 场景 7：iterate_candidates 分页迭代 --------------------------------------------------


def scenario_iterate_candidates():
    tag = '场景7 iterate_candidates 分页'
    with tempfile.TemporaryDirectory() as tmp:
        st = pilot_state.open_state(str(Path(tmp) / 'pilot.sqlite3'))
        try:
            seed_plan(st, 'task-1', 'j-1', 2)
            attempt_id = claim(st)
            candidates = [make_candidate(n) for n in range(501)]
            st.commit_success('task-1', 'j-1', attempt_id, candidates, USAGE_OK, 'digest-501')
            pages = list(st.iterate_candidates('task-1', page_size=200))
            all_items = [item for page in pages for item in page]
            check([len(p) for p in pages] == [200, 200, 101],
                  '%s：页大小 200/200/101，共 3 页' % tag,
                  actual=[len(p) for p in pages])
            check(len(all_items) == 501, '%s：恰 501 条候选' % tag, actual=len(all_items))
            keys = [item['key'] for item in all_items]
            check(keys == ['k-%03d' % n for n in range(501)],
                  '%s：写入序稳定、无重无漏（k-000..k-500 严格有序）' % tag)
            ids = [item['candidateId'] for item in all_items]
            check(len(set(ids)) == 501, '%s：candidateId 无重复' % tag)
            check(all(item['jobId'] == 'j-1' and item['planEpoch'] == 1
                      and item['evidenceStatus'] == 'supported' for item in all_items),
                  '%s：回读候选带 jobId/planEpoch 且字段零丢失' % tag)
            pages_again = [item for page in st.iterate_candidates('task-1', page_size=97)
                           for item in page]
            check(pages_again == all_items,
                  '%s：换页大小后迭代结果完全一致（稳定序）' % tag)
            check(list(st.iterate_candidates('no-such-task')) == [],
                  '%s：无候选 task 迭代为空' % tag)
        finally:
            st.close()
    # 跨 job 按提交顺序排列：两目标计划 + 两个作业各认领一个目标
    with tempfile.TemporaryDirectory() as tmp:
        st = pilot_state.open_state(str(Path(tmp) / 'pilot.sqlite3'))
        try:
            doc = make_plan(2)
            doc = batch_state.apply_event(doc, event_created('j-A', ['t-1']))
            doc = batch_state.apply_event(doc, event_created('j-B', ['t-2']))
            st.save_plan('task-1', doc)
            a_id = claim(st, job_id='j-A')
            b_id = claim(st, job_id='j-B')
            st.commit_success('task-1', 'j-A', a_id, [make_candidate(1), make_candidate(2)],
                              USAGE_OK, 'da')
            st.commit_success('task-1', 'j-B', b_id, [make_candidate(3)], USAGE_OK, 'db')
            keys = [item['key'] for page in st.iterate_candidates('task-1', page_size=2)
                    for item in page]
            check(keys == ['k-001', 'k-002', 'k-003'],
                  '%s：跨 job 按提交顺序排列' % tag, actual=keys)
        finally:
            st.close()


# --- 场景 8：checkpoint 容量守卫 ----------------------------------------------------------


def scenario_checkpoint_capacity():
    tag = '场景8 checkpoint 容量'
    with tempfile.TemporaryDirectory() as tmp:
        st = pilot_state.open_state(str(Path(tmp) / 'pilot.sqlite3'))
        try:
            big = batch_state.create_plan('big-batch', 1, 1, make_targets(12000), PROFILE,
                                          contracts.CODEC_LEGACY, FINGERPRINT_PARTS)
            size = len(json.dumps({'generate': big}, ensure_ascii=False).encode('utf-8'))
            check(size > contracts.MAX_CHECKPOINT_BYTES,
                  '%s：构造超 1MiB 计划（实际 %d 字节）' % (tag, size), actual=size)
            raised = capture_exception(lambda: st.save_plan('big-task', big))
            check(isinstance(raised, pilot_state.CheckpointCapacityError),
                  '%s：save_plan 超限 → CheckpointCapacityError' % tag,
                  actual=type(raised).__name__ if raised else None)
            check(st.load('big-task') is None,
                  '%s：超限保存后库内不变（无计划行）' % tag)
            rows = sqlite3.connect(st.db_path).execute(
                'SELECT COUNT(*) FROM pilot_plans').fetchone()[0]
            check(rows == 0, '%s：pilot_plans 无任何行' % tag, actual=rows)
        finally:
            st.close()
    # commit 路径容量守卫：注入 tiny 上限后还原
    with tempfile.TemporaryDirectory() as tmp:
        st = pilot_state.open_state(str(Path(tmp) / 'pilot.sqlite3'))
        try:
            seed_plan(st, 'task-1', 'j-1', 3)
            attempt_id = claim(st)
            before = st.load('task-1')
            original = contracts.MAX_CHECKPOINT_BYTES
            contracts.MAX_CHECKPOINT_BYTES = 512
            try:
                raised = capture_exception(lambda: st.commit_success(
                    'task-1', 'j-1', attempt_id, [make_candidate(1)], USAGE_OK, 'd'))
            finally:
                contracts.MAX_CHECKPOINT_BYTES = original
            check(isinstance(raised, pilot_state.CheckpointCapacityError),
                  '%s：commit 路径超限 → CheckpointCapacityError' % tag,
                  actual=type(raised).__name__ if raised else None)
            check(st.load('task-1') == before,
                  '%s：commit 超限整体回滚（job 仍 running、attempt 仍 started）' % tag)
        finally:
            st.close()


# --- 场景 9：冷热缓存 ----------------------------------------------------------------------


def scenario_cache_roundtrip_and_ttl():
    tag = '场景9 冷缓存 cache_put/get'
    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / 'pilot.sqlite3')
        clock = {'now': 1000.0}

        def fake_clock():
            return clock['now']

        st = pilot_state.open_state(db, clock=fake_clock)
        try:
            payload = {'buckets': {'prov-1|model-x|v3|legacy-v1|ddl_table':
                                   {'count': 3, 'max': 512.0, 'samples': [300.0, 512.0]}}}
            st.cache_put('calibration-snapshot', payload)
            check(st.cache_get('calibration-snapshot') == payload,
                  '%s：cache_put→cache_get 往返一致' % tag)
            st.cache_put('calibration-snapshot', {'v': 2})
            check(st.cache_get('calibration-snapshot') == {'v': 2},
                  '%s：同 key 覆盖写' % tag)
            check(st.cache_get('missing-key') is None, '%s：缺失 key 返回 None' % tag)
            # ttl 过期（注入 clock，确定性）
            st.cache_put('ttl-key', {'n': 1}, ttl_seconds=100)
            clock['now'] = 1099.0
            check(st.cache_get('ttl-key') == {'n': 1},
                  '%s：ttl 内可读（t=1099 < expires=1100）' % tag)
            clock['now'] = 1100.0
            check(st.cache_get('ttl-key') is None, '%s：到期返回 None' % tag)
            check(st.cache_get('ttl-key') is None, '%s：过期后再读仍 None（惰性清理）' % tag)
            # ttl=0 立即过期
            st.cache_put('ttl-zero', {'n': 0}, ttl_seconds=0)
            clock['now'] += 1.0
            check(st.cache_get('ttl-zero') is None, '%s：ttl=0 立即过期返回 None' % tag)
            # 无 ttl 永不过期（跨重跑/跨实例共用——缓存跟 db_path 走）
            st.cache_put('forever', {'stable': True})
            clock['now'] += 10 ** 9
            check_raises(lambda: st.cache_put('bad-ttl', {}, ttl_seconds=-1),
                         '%s：负 ttl → ValueError' % tag)
            st.close()
            st2 = pilot_state.open_state(db)   # 新实例（模拟重跑；真实时钟也不该过期）
            try:
                check(st2.cache_get('forever') == {'stable': True},
                      '%s：跨实例（跨重跑）缓存共用，换实例不重置' % tag)
            finally:
                st2.close()
        finally:
            st.close()


# --- 场景 10：隔离与零存储层依赖 -----------------------------------------------------------


def scenario_isolation_and_no_storage_import():
    tag = '场景10 隔离与零存储层依赖'
    check('workbench.storage' not in sys.modules,
          '%s：导入试点状态模块后 sys.modules 无 workbench.storage' % tag)
    source = Path(pilot_state.__file__).resolve().read_text(encoding='utf-8')
    import_lines = [line.strip() for line in source.splitlines()
                    if line.strip().startswith(('from workbench.storage',
                                                'import workbench.storage'))]
    check(import_lines == [], '%s：state.py 源码无 workbench.storage import 语句' % tag,
          actual=import_lines)
    with tempfile.TemporaryDirectory() as tmp_a, tempfile.TemporaryDirectory() as tmp_b:
        st_a = pilot_state.open_state(str(Path(tmp_a) / 'a.sqlite3'))
        st_b = pilot_state.open_state(str(Path(tmp_b) / 'b.sqlite3'))
        try:
            seed_plan(st_a, 'task-1', 'j-1', 3)
            st_a.cache_put('shared', {'who': 'A'})
            attempt_id = claim(st_a)
            st_a.commit_success('task-1', 'j-1', attempt_id, [make_candidate(1)],
                                USAGE_OK, 'da')
            check(st_b.load('task-1') is None, '%s：B 库看不到 A 库计划' % tag)
            check(st_b.cache_get('shared') is None, '%s：B 库看不到 A 库缓存' % tag)
            check(list(st_b.iterate_candidates('task-1')) == [],
                  '%s：B 库看不到 A 库候选' % tag)
            st_b.cache_put('shared', {'who': 'B'})
            check(st_a.cache_get('shared') == {'who': 'A'}
                  and st_b.cache_get('shared') == {'who': 'B'},
                  '%s：同名缓存 key 互不干扰' % tag)
        finally:
            st_a.close()
            st_b.close()


SCENARIOS = [
    ('save_load_roundtrip', scenario_save_load_roundtrip),
    ('claim_job_persists_first', scenario_claim_job_persists_first),
    ('commit_success_atomic', scenario_commit_success_atomic),
    ('commit_success_idempotent', scenario_commit_success_idempotent),
    ('commit_split', scenario_commit_split),
    ('mark_interrupted_unknown', scenario_mark_interrupted_unknown),
    ('iterate_candidates', scenario_iterate_candidates),
    ('checkpoint_capacity', scenario_checkpoint_capacity),
    ('cache_roundtrip_and_ttl', scenario_cache_roundtrip_and_ttl),
    ('isolation_and_no_storage_import', scenario_isolation_and_no_storage_import),
]


def main():
    for tag, fn in SCENARIOS:
        print('\n----- %s -----' % tag)
        fn()
    return 0


if __name__ == '__main__':
    code = 1
    try:
        code = main()
    except Exception as exc:  # noqa: BLE001 - 顶层兜底：未预期异常计入失败并退非零
        traceback.print_exc()
        FAILED.append('未预期异常: %s: %s' % (type(exc).__name__, exc))
    total = len(PASSED) + len(FAILED)
    print('\n========== 汇总 ==========')
    print('通过 %d / %d' % (len(PASSED), total))
    for name in FAILED:
        print('  失败: ' + name)
    if total == 0:
        print('[错误] 未执行任何断言——不算通过')
        sys.exit(2)
    sys.exit(0 if code == 0 and not FAILED else 1)
