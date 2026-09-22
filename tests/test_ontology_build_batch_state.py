"""D07 纯状态机/指纹/覆盖与容量守卫回归（batch_state.py，2026-09-22）。

被测模块：workbench/ontology_build/batch_state.py（纯函数，契约来源
workbench/ontology_build/batch_contracts.py，D00 冻结提交 87d1473）。

场景（对应整合计划 v3 §8 D07 行验收标准：有效叶集合、unknown 版本、checkpoint 完成预留）：
 1. create_plan 结构完整：schemaVersion=2、targets/pending 一致、budgetProfile 有界
    （只存 errorCodes，不含 errors 全文）、指纹复用契约、未知 codec 拒绝。
 2. 合法迁移链 queued→claimed→attempt_started→attempt_succeeded→job_succeeded 全通过；
    输入 doc 不可变；叶 succeeded、coverage/usageAggregate/pending 联动正确。
 3. 非法迁移：queued 直接 job_succeeded、终态再迁移、running 直接 superseded、
    未知事件类型、attempt 重复 id/终态再迁移 → 一律 ValueError。
 4. split 链：父 split 后子作业入 jobs、父不再计算为叶；覆盖守恒（重/漏拒绝）；
    左右子都 succeeded → final_check ok；只左成功右 failed → final_check 报
    UNFINISHED_JOB（契约 final_state_check 复用）。
 5. attempt_unknown：started→interrupted_unknown；final_check 对 started 残留报
    INFLIGHT_ATTEMPT、对 interrupted_unknown 不报；unknown usage 计 unknown 不计 0。
 6. can_dispatch：1024 attempts 满额拒绝、activeElapsedMs 累计超 3600s 拒绝、
    blocking 非空拒绝、正常放行。
 7. bound_error/bound_id 截断（400/200 字符预算）；calibration_snapshot_bounds
    超 20 采样只留最近 20、count/max 保留。
 8. validate_plan_doc：悬空 parentId / 状态枚举外值 / pending 与 targets 不一致 /
    job 引用不存在的 attempt 各报一条；干净计划零错误。
 9. 与 contracts 一致性：stable_job_id 派生作业 id 可用且确定、coverage_check 与
    completed_target_digests 组合、final_state_check 复用、plan_fingerprint 复用、
    checkpoint 软阈值复用（终态完成预留 16KiB+8KiB）。

隔离（AGENTS.md 测试铁律）：纯内存测试，不 import 存储层、不设 WIZ_WORKBENCH_ROOT、
不访问网络、不写任何文件；全部数据为合成数据。

运行：python3 tests/test_ontology_build_batch_state.py
"""
import copy
import sys
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from workbench.ontology_build import batch_contracts as contracts  # noqa: E402
from workbench.ontology_build import batch_state  # noqa: E402

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


def check_raises(fn, message, actual=None):
    try:
        fn()
    except ValueError as exc:
        return check(True, message + '（ValueError: %s）' % _short(exc, 120))
    except Exception as exc:  # noqa: BLE001 - 必须是 ValueError，其余类型都算失败
        return check(False, message, actual='%s: %s' % (type(exc).__name__, exc),
                     expected='ValueError')
    return check(False, message, actual='未抛出异常', expected='ValueError')


def violation_codes(result):
    return sorted(item.get('code') or '' for item in result.get('violations') or [])


# --- 合成数据 ------------------------------------------------------------------------

PROFILE = {
    'enabled': True, 'providerId': 'prov-1', 'model': 'model-x',
    'contextTokens': 100000, 'outputLimitTokens': 32000, 'requestOutputTokens': 32000,
    'targetRatio': 0.5, 'limitsSource': 'configured',
    'errors': [{'code': 'SOME_CONFIG_ISSUE', 'message': '很长的配置错误全文' * 50}],
    'effective': {'outputCap': 32000, 'targetBudget': 16000, 'inputReserve': 2048},
}
FINGERPRINT_PARTS = ['facts-digest-abc', '3', '5', 'prov-1', 'model-x', 'v3', 'legacy-v1',
                     'profile-parts']


def make_targets(count):
    """count 个合成目标（t-1..t-N，factId f-1..f-N，whole selector）。"""
    return [{'targetId': 't-%d' % n, 'factId': 'f-%d' % n, 'selector': {'type': 'whole'},
             'kind': 'ddl_table', 'subjectKey': 'subject-%d' % n, 'materialId': 'm-1'}
            for n in range(1, count + 1)]


def make_plan(target_count=3, targets=None):
    return batch_state.create_plan('batch-1', 3, 5, targets if targets is not None
                                   else make_targets(target_count), PROFILE,
                                   contracts.CODEC_LEGACY, FINGERPRINT_PARTS)


USAGE_OK = {'prompt_tokens': 100, 'completion_tokens': 200, 'total_tokens': 300}


def event_created(job_id, target_ids, **extra):
    event = {'type': 'job_created', 'jobId': job_id,
             'orderedPrimaryTargetIds': list(target_ids),
             'contextFactIds': ['f-99'], 'estimate': {'slots': len(target_ids),
                                                      'expectedOutputTokens': 4096}}
    event.update(extra)
    return event


def event_started(attempt_id, job_id, sequence=1):
    return {'type': 'attempt_started', 'attemptId': attempt_id, 'jobId': job_id,
            'sequence': sequence, 'runAttempt': 0, 'requestedMaxTokens': 8000,
            'requestFingerprint': 'rf-%s' % attempt_id}


def event_attempt_ok(attempt_id, **extra):
    event = {'type': 'attempt_succeeded', 'attemptId': attempt_id, 'usage': dict(USAGE_OK),
             'finishReason': 'stop', 'bytes': 1024, 'durationMs': 55}
    event.update(extra)
    return event


def run_leaf(doc, job_id, target_ids, attempt_id, candidate_count=2):
    """一个成功叶的完整事件链：created→claimed→started→succeeded→job_succeeded。"""
    doc = batch_state.apply_event(doc, event_created(job_id, target_ids))
    return finish_leaf(doc, job_id, attempt_id, candidate_count=candidate_count)


def finish_leaf(doc, job_id, attempt_id, candidate_count=2):
    """已有 queued 作业的成功链：claimed→started→succeeded→job_succeeded（不重复建作业）。"""
    doc = batch_state.apply_event(doc, {'type': 'job_claimed', 'jobId': job_id,
                                        'attemptId': attempt_id})
    doc = batch_state.apply_event(doc, event_started(attempt_id, job_id))
    doc = batch_state.apply_event(doc, event_attempt_ok(attempt_id))
    return batch_state.apply_event(doc, {'type': 'job_succeeded', 'jobId': job_id,
                                         'candidateCount': candidate_count,
                                         'resultDigest': 'digest-%s' % job_id})


# --- 场景 1：create_plan 结构 ---------------------------------------------------------


def scenario_create_plan_shape():
    tag = '场景1 create_plan结构'
    doc = make_plan(3)
    fingerprint = contracts.plan_fingerprint(FINGERPRINT_PARTS)
    check(doc.get('schemaVersion') == 2 and doc.get('adaptive') is True,
          '%s：schemaVersion=2 且 adaptive=True' % tag, actual=doc.get('schemaVersion'))
    check(doc.get('planId') == 'p-' + fingerprint[:12] and doc.get('fingerprint') == fingerprint,
          '%s：planId=p-+指纹前12位，指纹复用 contracts.plan_fingerprint' % tag,
          actual={'planId': doc.get('planId'), 'fingerprint': doc.get('fingerprint')},
          expected={'planId': 'p-' + fingerprint[:12], 'fingerprint': fingerprint})
    check(list(doc.get('targets') or {}) == ['t-1', 't-2', 't-3']
          and doc.get('pendingTargetIds') == ['t-1', 't-2', 't-3'],
          '%s：targets 注册全量且 pendingTargetIds=全部 targetId' % tag,
          actual={'targets': list(doc.get('targets') or {}),
                  'pending': doc.get('pendingTargetIds')})
    check(doc.get('coverage') == {'targetTotal': 3, 'targetCompleted': 0, 'targetPending': 3}
          and doc.get('jobs') == {} and doc.get('attempts') == {},
          '%s：coverage 初始 pending=全量，jobs/attempts 空' % tag,
          actual={'coverage': doc.get('coverage'), 'jobs': doc.get('jobs')})
    budget = doc.get('budgetProfile') or {}
    check(budget.get('enabled') is True and budget.get('providerId') == 'prov-1'
          and budget.get('contextTokens') == 100000
          and budget.get('effective') == PROFILE['effective'],
          '%s：budgetProfile 存有界子集（enabled/provider/context/effective）' % tag,
          actual=budget)
    check('errors' not in budget and budget.get('errorCodes') == ['SOME_CONFIG_ISSUE'],
          '%s：budgetProfile 不含 errors 全文，只存 errorCodes 列表' % tag,
          actual={key: budget.get(key) for key in ('errors', 'errorCodes')})
    check(doc.get('blocking') is None and doc.get('log') == [] and doc.get('notes') == []
          and doc.get('usageAggregate') == contracts.usage_aggregate([]),
          '%s：blocking=None、log/notes 空、usageAggregate=契约空聚合' % tag,
          actual={'usageAggregate': doc.get('usageAggregate')})
    check(doc.get('outputCodecVersion') == contracts.CODEC_LEGACY
          and doc.get('plannerVersion') == contracts.PLANNER_VERSION
          and doc.get('promptVersion') == contracts.PLAN_PROMPT_VERSION
          and doc.get('planEpoch') == 1,
          '%s：codec/planner/prompt 版本与 planEpoch=1 记入计划' % tag,
          actual={key: doc.get(key) for key in ('outputCodecVersion', 'plannerVersion',
                                                'promptVersion', 'planEpoch')})
    check(isinstance(doc.get('selectedFactsDigest'), str)
          and len(doc.get('selectedFactsDigest')) == 32
          and doc.get('providerFingerprint', {}).get('model') == 'model-x',
          '%s：selectedFactsDigest 32 位、providerFingerprint 非密标识入计划' % tag,
          actual={'digest': doc.get('selectedFactsDigest'),
                  'provider': doc.get('providerFingerprint')})
    check_raises(lambda: batch_state.create_plan('b', 1, 1, make_targets(1), PROFILE,
                                                 'codec-unknown', FINGERPRINT_PARTS),
                 '%s：未知 outputCodecVersion 拒绝' % tag)
    bad = make_targets(1)
    bad[0]['factId'] = ''
    check_raises(lambda: batch_state.create_plan('b', 1, 1, bad, PROFILE,
                                                 contracts.CODEC_LEGACY, FINGERPRINT_PARTS),
                 '%s：目标缺 factId 拒绝' % tag)
    dup = make_targets(2)
    dup[1]['targetId'] = 't-1'
    check_raises(lambda: batch_state.create_plan('b', 1, 1, dup, PROFILE,
                                                 contracts.CODEC_LEGACY, FINGERPRINT_PARTS),
                 '%s：目标 id 重复拒绝' % tag)
    check(len(doc.get('selectedFactsDigest') or '') > 0
          and batch_state.validate_plan_doc(doc) == [],
          '%s：新建计划通过 validate_plan_doc 零错误' % tag)


# --- 场景 2：合法迁移链 ----------------------------------------------------------------


def scenario_happy_path_chain():
    tag = '场景2 合法迁移链'
    doc = make_plan(3)
    original = copy.deepcopy(doc)
    doc = batch_state.apply_event(doc, event_created('j-a', ['t-1', 't-2']))
    check(doc['jobs']['j-a']['state'] == 'queued'
          and doc['pendingTargetIds'] == ['t-3']
          and doc['coverage'] == {'targetTotal': 3, 'targetCompleted': 0, 'targetPending': 3},
          '%s：job_created 后 queued，主目标离开 pending（coverage 完成数不变）' % tag,
          actual={'state': doc['jobs']['j-a']['state'], 'pending': doc['pendingTargetIds'],
                  'coverage': doc['coverage']})
    doc = batch_state.apply_event(doc, {'type': 'job_claimed', 'jobId': 'j-a',
                                        'attemptId': 'a-1'})
    check(doc['jobs']['j-a']['state'] == 'running',
          '%s：job_claimed queued→running' % tag, actual=doc['jobs']['j-a']['state'])
    doc = batch_state.apply_event(doc, event_started('a-1', 'j-a'))
    attempt = doc['attempts'].get('a-1') or {}
    check(attempt.get('state') == 'started' and attempt.get('requestedMaxTokens') == 8000
          and doc['jobs']['j-a']['attemptIds'] == ['a-1'],
          '%s：attempt_started 登记 started 并挂到作业 attemptIds' % tag, actual=attempt)
    doc = batch_state.apply_event(doc, event_attempt_ok('a-1'))
    attempt = doc['attempts']['a-1']
    check(attempt.get('state') == 'succeeded' and attempt.get('finishReason') == 'stop'
          and attempt.get('usage', {}).get('completionTokens') == 200,
          '%s：attempt_succeeded started→succeeded，usage 冻结形状' % tag, actual=attempt)
    check(doc['usageAggregate'].get('calls') == 1
          and doc['usageAggregate'].get('completionTokens') == 200
          and doc['usageAggregate'].get('unknownUsageCalls') == 0,
          '%s：usageAggregate 按已收口尝试聚合（无双计）' % tag,
          actual=doc['usageAggregate'])
    doc = batch_state.apply_event(doc, {'type': 'job_succeeded', 'jobId': 'j-a',
                                        'candidateCount': 2, 'resultDigest': 'digest-a'})
    job = doc['jobs']['j-a']
    check(job.get('state') == 'succeeded' and job.get('candidateCount') == 2
          and job.get('resultDigest') == 'digest-a',
          '%s：job_succeeded running→succeeded，候选数与摘要入账' % tag, actual=job)
    check(doc['pendingTargetIds'] == ['t-3']
          and doc['coverage'] == {'targetTotal': 3, 'targetCompleted': 2, 'targetPending': 1},
          '%s：成功叶目标计完成（t-1/t-2 完成、t-3 待处理）' % tag,
          actual={'pending': doc['pendingTargetIds'], 'coverage': doc['coverage']})
    check(batch_state.effective_leaf_jobs(doc) == ['j-a'],
          '%s：effective_leaf_jobs 返回该叶' % tag, actual=batch_state.effective_leaf_jobs(doc))
    check(original != doc and batch_state.validate_plan_doc(doc) == [],
          '%s：事件产生新 doc（输入不可变）且最终结构零错误' % tag,
          actual=batch_state.validate_plan_doc(doc))


def scenario_input_immutable():
    tag = '场景2b 输入不可变'
    doc = make_plan(1)
    snapshot = copy.deepcopy(doc)
    step1 = batch_state.apply_event(doc, event_created('j-x', ['t-1']))
    step2 = batch_state.apply_event(step1, {'type': 'job_claimed', 'jobId': 'j-x',
                                            'attemptId': 'a-x'})
    step3 = batch_state.apply_event(step2, {'type': 'elapsed_update', 'ms': 100})
    check(doc == snapshot,
          '%s：apply_event 多次调用后原 doc 保持不变（深拷贝语义）' % tag,
          actual='原 doc 被修改' if doc != snapshot else '一致')
    check(step3['jobs']['j-x']['state'] == 'running' and step3['activeElapsedMs'] == 100,
          '%s：事件链在新 doc 上正常推进' % tag,
          actual={'state': step3['jobs']['j-x']['state'],
                  'activeElapsedMs': step3['activeElapsedMs']})


# --- 场景 3：非法迁移 ------------------------------------------------------------------


def scenario_illegal_transitions():
    tag = '场景3 非法迁移'
    doc = make_plan(2)
    queued = batch_state.apply_event(doc, event_created('j-q', ['t-1']))
    check_raises(lambda: batch_state.apply_event(
        queued, {'type': 'job_succeeded', 'jobId': 'j-q', 'candidateCount': 0}),
        '%s：queued 直接 job_succeeded 抛 ValueError' % tag)
    running = batch_state.apply_event(queued, {'type': 'job_claimed', 'jobId': 'j-q',
                                               'attemptId': 'a-1'})
    check_raises(lambda: batch_state.apply_event(
        running, {'type': 'job_superseded', 'jobId': 'j-q', 'replacedBy': []}),
        '%s：running 直接 superseded 抛 ValueError（仅 queued 可被替代）' % tag)
    done = run_leaf(make_plan(2), 'j-d', ['t-1'], 'a-1')
    check_raises(lambda: batch_state.apply_event(
        done, {'type': 'job_failed', 'jobId': 'j-d', 'errorCode': 'X', 'message': '再迁移'}),
        '%s：终态 succeeded 再 job_failed 抛 ValueError' % tag)
    check_raises(lambda: batch_state.apply_event(done, {'type': 'mystery_event'}),
                 '%s：未知事件类型抛 ValueError' % tag)
    check_raises(lambda: batch_state.apply_event(done, {}),
                 '%s：缺 type 字段按未知事件抛 ValueError' % tag)
    check_raises(lambda: batch_state.apply_event(
        done, {'type': 'job_created', 'jobId': 'j-d', 'orderedPrimaryTargetIds': ['t-1']}),
        '%s：重复 jobId 拒绝' % tag)
    check_raises(lambda: batch_state.apply_event(
        done, {'type': 'job_created', 'jobId': 'j-new', 'orderedPrimaryTargetIds': ['t-ghost']}),
        '%s：job_created 引用不存在目标拒绝' % tag)
    check_raises(lambda: batch_state.apply_event(
        done, {'type': 'job_created', 'jobId': 'j-new', 'orderedPrimaryTargetIds': ['t-2'],
               'parentId': 'j-missing'}),
        '%s：悬空 parentId 拒绝' % tag)
    claimed = batch_state.apply_event(make_plan(1), event_created('j-c', ['t-1']))
    check_raises(lambda: batch_state.apply_event(
        claimed, event_started('a-early', 'j-c')),
        '%s：未认领（queued）直接 attempt_started 拒绝' % tag)
    started = batch_state.apply_event(
        batch_state.apply_event(claimed, {'type': 'job_claimed', 'jobId': 'j-c',
                                          'attemptId': 'a-1'}),
        event_started('a-1', 'j-c'))
    check_raises(lambda: batch_state.apply_event(
        started, event_started('a-1', 'j-c')),
        '%s：attemptId 重复（每次物理调用唯一）拒绝' % tag)
    settled = batch_state.apply_event(started, event_attempt_ok('a-1'))
    check_raises(lambda: batch_state.apply_event(settled, event_attempt_ok('a-1')),
                 '%s：attempt 终态再迁移拒绝' % tag)
    check_raises(lambda: batch_state.apply_event(
        settled, {'type': 'attempt_unknown', 'attemptId': 'a-1'}),
        '%s：succeeded 尝试不能再转 interrupted_unknown' % tag)
    check_raises(lambda: batch_state.apply_event(
        started, {'type': 'job_succeeded', 'jobId': 'j-c', 'candidateCount': 0}),
        '%s：作业仍有在途尝试（started）时进入终态拒绝' % tag)


# --- 场景 4：split 链与覆盖守恒 ---------------------------------------------------------


def scenario_split_chain():
    tag = '场景4 split链'
    doc = make_plan(2)
    doc = batch_state.apply_event(doc, event_created('j-root', ['t-1', 't-2'],
                                                     splitPath='root'))
    doc = batch_state.apply_event(doc, {'type': 'job_claimed', 'jobId': 'j-root',
                                        'attemptId': 'a-0'})
    doc = batch_state.apply_event(doc, event_started('a-0', 'j-root'))
    doc = batch_state.apply_event(doc, {
        'type': 'attempt_failed', 'attemptId': 'a-0', 'errorCode': contracts.OUTPUT_TRUNCATED,
        'message': '输出被截断' * 100, 'finishReason': 'length',
        'usage': {'prompt_tokens': 500, 'completion_tokens': 8000, 'total_tokens': 8500},
        'bytes': 900000, 'durationMs': 4000})
    children = [
        {'jobId': 'j-left', 'orderedPrimaryTargetIds': ['t-1'], 'splitPath': 'root/l',
         'estimate': {'slots': 1, 'expectedOutputTokens': 2048}},
        {'jobId': 'j-right', 'orderedPrimaryTargetIds': ['t-2'], 'splitPath': 'root/r'},
    ]
    doc = batch_state.apply_event(doc, {'type': 'job_split', 'jobId': 'j-root',
                                        'children': children})
    parent = doc['jobs']['j-root']
    check(parent.get('state') == 'split' and parent.get('children') == ['j-left', 'j-right'],
          '%s：job_split running→split，children 入账' % tag, actual=parent)
    check(doc['jobs']['j-left']['state'] == 'queued'
          and doc['jobs']['j-right']['state'] == 'queued'
          and doc['jobs']['j-left']['parentId'] == 'j-root'
          and doc['jobs']['j-right']['rootId'] == 'j-root',
          '%s：子作业 queued 入 jobs，parentId/rootId 指回父链' % tag,
          actual={jid: doc['jobs'][jid] for jid in ('j-left', 'j-right')})
    check(batch_state.effective_leaf_jobs(doc) == ['j-left', 'j-right'],
          '%s：父 split 后不再计算为叶，叶=两个子作业' % tag,
          actual=batch_state.effective_leaf_jobs(doc))
    check(doc['pendingTargetIds'] == []
          and doc['coverage']['targetCompleted'] == 0
          and doc['coverage']['targetPending'] == 2,
          '%s：split 后目标由子作业认领（父 split 不算成功）' % tag,
          actual={'pending': doc['pendingTargetIds'], 'coverage': doc['coverage']})
    check(doc['usageAggregate'].get('completionTokens') == 8000
          and doc['attempts']['a-0']['errorCode'] == contracts.OUTPUT_TRUNCATED
          and len(doc['attempts']['a-0']['errorMessage']) <= 400,
          '%s：截断 attempt 记账（usage 累计、errorCode/有界 message 随 attempt 保存）' % tag,
          actual={'aggregate': doc['usageAggregate'], 'attempt': doc['attempts']['a-0']})
    check(batch_state.validate_plan_doc(doc) == [],
          '%s：split 后结构校验零错误' % tag, actual=batch_state.validate_plan_doc(doc))
    # 覆盖守恒与规划期拆分
    planned_split = batch_state.apply_event(doc, {
        'type': 'job_split', 'jobId': 'j-left',
        'children': [{'jobId': 'j-x', 'orderedPrimaryTargetIds': ['t-1'],
                      'splitPath': 'root/l/0'}]})
    check(planned_split['jobs']['j-left']['state'] == 'split'
          and planned_split['jobs']['j-x']['state'] == 'queued',
          '%s：queued 作业可规划期 split（queued→split 合法迁移）' % tag,
          actual={jid: planned_split['jobs'][jid]['state']
                  for jid in ('j-left', 'j-x')})
    check_raises(lambda: batch_state.apply_event(doc, {
        'type': 'job_split', 'jobId': 'j-right',
        'children': [{'jobId': 'j-y', 'orderedPrimaryTargetIds': ['t-1']}]}),
        '%s：子目标不属于本作业（拆分漏目标）拒绝' % tag)
    half = copy.deepcopy(doc)
    half['jobs']['j-left']['state'] = 'running'
    half['jobs']['j-left']['attemptIds'] = []
    check_raises(lambda: batch_state.apply_event(half, {
        'type': 'job_split', 'jobId': 'j-left',
        'children': [{'jobId': 'j-o1', 'orderedPrimaryTargetIds': ['t-1']},
                     {'jobId': 'j-o2', 'orderedPrimaryTargetIds': ['t-1']}]}),
        '%s：子目标重复认领（拆分不重）拒绝' % tag)
    check_raises(lambda: batch_state.apply_event(doc, {
        'type': 'job_split', 'jobId': 'j-right', 'children': []}),
        '%s：children 为空拒绝' % tag)
    check_raises(lambda: batch_state.apply_event(doc, {
        'type': 'job_split', 'jobId': 'j-right',
        'children': [{'jobId': 'j-z', 'orderedPrimaryTargetIds': ['t-1']},
                     {'jobId': 'j-z2', 'orderedPrimaryTargetIds': ['t-1', 't-2']}]}),
        '%s：子目标并集超出父目标（拆分不漏也不得多占）拒绝' % tag)
    # 左右子都成功 → final_check ok
    both = finish_leaf(doc, 'j-left', 'a-1')
    both = finish_leaf(both, 'j-right', 'a-2')
    final = batch_state.final_check(both)
    check(final.get('ok') is True and batch_state.validate_plan_doc(both) == [],
          '%s：左右子都 succeeded → final_check ok、结构零错误' % tag,
          actual={'final': final, 'errors': batch_state.validate_plan_doc(both)})
    digests = batch_state.completed_target_digests(both, both['targets'])
    planned = [contracts.target_digest(both['targets'][tid]) for tid in ('t-1', 't-2')]
    check(sorted(digests) == sorted(planned)
          and contracts.coverage_check(planned, digests).get('ok') is True,
          '%s：completed_target_digests 与计划目标集合守恒（contracts.coverage_check ok）' % tag,
          actual={'completed': digests, 'planned': planned})
    check(both['coverage'] == {'targetTotal': 2, 'targetCompleted': 2, 'targetPending': 0}
          and both['usageAggregate'].get('completionTokens') == 8000 + 400,
          '%s：全叶成功后 coverage 满、usage 累计父截断+子调用（8000+200×2）' % tag,
          actual={'coverage': both['coverage'], 'usage': both['usageAggregate']})
    # 只左成功右失败 → UNFINISHED_JOB
    partial = finish_leaf(doc, 'j-left', 'a-1')
    partial = batch_state.apply_event(partial, {'type': 'job_claimed', 'jobId': 'j-right',
                                                'attemptId': 'a-2'})
    partial = batch_state.apply_event(partial, event_started('a-2', 'j-right', sequence=1))
    partial = batch_state.apply_event(partial, {
        'type': 'attempt_failed', 'attemptId': 'a-2', 'errorCode': contracts.JOB_ATTEMPTS_EXHAUSTED,
        'message': '三次总额用尽', 'usage': {}})
    partial = batch_state.apply_event(partial, {
        'type': 'job_failed', 'jobId': 'j-right', 'errorCode': contracts.JOB_ATTEMPTS_EXHAUSTED,
        'message': '右半失败，左半候选保留'})
    final = batch_state.final_check(partial)
    check(final.get('ok') is False and 'UNFINISHED_JOB' in violation_codes(final),
          '%s：左成功右 failed → final_check 报 UNFINISHED_JOB（父不成功）' % tag,
          actual={'ok': final.get('ok'), 'codes': violation_codes(final)})
    check(partial['coverage']['targetCompleted'] == 1,
          '%s：部分完成时 coverage 精确计 1（不冒充整体成功）' % tag,
          actual=partial['coverage'])


# --- 场景 5：崩溃恢复 attempt_unknown ---------------------------------------------------


def scenario_attempt_unknown():
    tag = '场景5 attempt_unknown'
    doc = make_plan(1)
    doc = batch_state.apply_event(doc, event_created('j-u', ['t-1']))
    doc = batch_state.apply_event(doc, {'type': 'job_claimed', 'jobId': 'j-u',
                                        'attemptId': 'a-1'})
    doc = batch_state.apply_event(doc, event_started('a-1', 'j-u'))
    inflight = batch_state.final_check(doc)
    check(inflight.get('ok') is False and 'INFLIGHT_ATTEMPT' in violation_codes(inflight),
          '%s：started 残留 → final_check 报 INFLIGHT_ATTEMPT（在途未收口）' % tag,
          actual={'ok': inflight.get('ok'), 'codes': violation_codes(inflight)})
    recovered = batch_state.apply_event(doc, {'type': 'attempt_unknown', 'attemptId': 'a-1'})
    attempt = recovered['attempts']['a-1']
    check(attempt.get('state') == contracts.ATTEMPT_INTERRUPTED_UNKNOWN,
          '%s：attempt_unknown started→interrupted_unknown' % tag, actual=attempt)
    after = batch_state.final_check(recovered)
    check('INFLIGHT_ATTEMPT' not in violation_codes(after),
          '%s：interrupted_unknown 不算 started 残留（不计 INFLIGHT_ATTEMPT）' % tag,
          actual=violation_codes(after))
    check(recovered['usageAggregate'].get('unknownUsageCalls') == 1
          and recovered['usageAggregate'].get('calls') == 1
          and recovered['usageAggregate'].get('completionTokens') is None,
          '%s：unknown usage 计 unknownUsageCalls=1、calls=1，不冒充总数（不计 0）' % tag,
          actual=recovered['usageAggregate'])
    check(batch_state.validate_plan_doc(recovered) == [],
          '%s：恢复后结构零错误' % tag, actual=batch_state.validate_plan_doc(recovered))


# --- 场景 6：can_dispatch 容量守卫 ------------------------------------------------------


def scenario_can_dispatch():
    tag = '场景6 can_dispatch'
    doc = run_leaf(make_plan(1), 'j-1', ['t-1'], 'a-1')
    ok, reason = batch_state.can_dispatch(doc)
    check(ok is True and reason is None,
          '%s：正常计划放行 (True, None)' % tag, actual=(ok, reason))
    full = copy.deepcopy(doc)
    full['attempts'] = {'a-%d' % n: {'jobId': 'j-1', 'state': 'succeeded'}
                        for n in range(contracts.MAX_PLAN_ATTEMPTS)}
    ok, reason = batch_state.can_dispatch(full)
    check(ok is False and reason == contracts.ATTEMPT_BUDGET_EXCEEDED,
          '%s：1024 attempts 满额拒绝（ATTEMPT_BUDGET_EXCEEDED）' % tag,
          actual=(ok, reason))
    ok2, reason2 = batch_state.can_dispatch(doc, {'maxPlanAttempts': 1})
    check(ok2 is False and reason2 == contracts.ATTEMPT_BUDGET_EXCEEDED,
          '%s：limits 收紧后同样拒绝（守卫可注入）' % tag, actual=(ok2, reason2))
    timed = batch_state.apply_event(doc, {'type': 'elapsed_update', 'ms': 1800 * 1000})
    timed = batch_state.apply_event(timed, {'type': 'elapsed_update', 'ms': 1800 * 1000})
    check(timed['activeElapsedMs'] == 3600 * 1000,
          '%s：elapsed_update 累计 activeElapsedMs' % tag, actual=timed['activeElapsedMs'])
    ok3, reason3 = batch_state.can_dispatch(timed)
    check(ok3 is False and reason3 == contracts.WALL_TIME_BUDGET_EXCEEDED,
          '%s：activeElapsedMs 达 3600s 拒绝（WALL_TIME_BUDGET_EXCEEDED）' % tag,
          actual=(ok3, reason3))
    ok3b, _ = batch_state.can_dispatch(timed, {'maxWallMs': 1800 * 1000})
    check(ok3b is False, '%s：墙钟上限可经 limits 覆盖（更严上限同样拒绝）' % tag)
    blocked = batch_state.apply_event(doc, {
        'type': 'blocking_set', 'code': contracts.CHECKPOINT_BUDGET_EXCEEDED,
        'message': 'checkpoint 软阈值不足，停止派发'})
    ok4, reason4 = batch_state.can_dispatch(blocked)
    check(ok4 is False and reason4 == contracts.CHECKPOINT_BUDGET_EXCEEDED
          and blocked['blocking']['code'] == contracts.CHECKPOINT_BUDGET_EXCEEDED,
          '%s：blocking_set 后拒绝且回带 blocking code' % tag, actual=(ok4, reason4))
    check_raises(lambda: batch_state.apply_event(doc, {'type': 'elapsed_update',
                                                       'ms': -5}),
                 '%s：负耗时增量拒绝' % tag)


# --- 场景 7：字段预算与校准快照 ---------------------------------------------------------


def scenario_bounds_and_calibration():
    tag = '场景7 字段预算与校准'
    check(len(batch_state.bound_error('x' * 5000)) == 400
          and len(batch_state.bound_id('y' * 5000)) == 200,
          '%s：bound_error ≤400、bound_id ≤200 字符' % tag,
          actual=(len(batch_state.bound_error('x' * 5000)),
                  len(batch_state.bound_id('y' * 5000))))
    check(batch_state.bound_error(None) == '' and batch_state.bound_id(None) == '',
          '%s：空输入归空串（不产生 None）' % tag)
    buckets = {
        'prov-1|model-x|v3|legacy-v1|nr|ddl_table': {
            'count': 25, 'max': 99.5, 'samples': list(range(25))},
        'thin-bucket': {'samples': [3, 1, 2]},
        'empty-bucket': {},
        'bad-bucket': {'samples': 'not-a-list', 'count': 'x'},
    }
    snapshot = batch_state.calibration_snapshot_bounds(buckets)
    bounded = snapshot['prov-1|model-x|v3|legacy-v1|nr|ddl_table']
    check(len(bounded['samples']) == contracts.CALIBRATION_MAX_SAMPLES
          and bounded['samples'] == list(range(5, 25)),
          '%s：超 20 采样只保留最近 20 个' % tag,
          actual={'n': len(bounded['samples']), 'head': bounded['samples'][:2]})
    check(bounded['count'] == 25 and bounded['max'] == 99.5,
          '%s：count/max 保留全量观测（不随采样截断）' % tag,
          actual={'count': bounded['count'], 'max': bounded['max']})
    thin = snapshot['thin-bucket']
    check(thin == {'count': 3, 'max': 3, 'samples': [3, 1, 2]},
          '%s：缺 count/max 时由采样推导' % tag, actual=thin)
    check(snapshot['empty-bucket'] == {'count': 0, 'max': None, 'samples': []}
          and snapshot['bad-bucket'] == {'count': 0, 'max': None, 'samples': []},
          '%s：空桶/非法桶归零（绝不抛异常）' % tag,
          actual={'empty': snapshot['empty-bucket'], 'bad': snapshot['bad-bucket']})


# --- 场景 8：validate_plan_doc 结构校验 --------------------------------------------------


def scenario_validate_plan_doc():
    tag = '场景8 validate_plan_doc'
    succeeded_doc = run_leaf(make_plan(1), 'j-v', ['t-1'], 'a-1')
    claimed_doc = batch_state.apply_event(
        batch_state.apply_event(make_plan(1), event_created('j-w', ['t-1'])),
        {'type': 'job_claimed', 'jobId': 'j-w', 'attemptId': 'a-w'})
    fresh_doc = make_plan(1)
    # (注入方式, 期望错误码)；基底 doc 单列，保证单处损坏恰报一条
    cases = [
        (succeeded_doc, lambda d: d['jobs']['j-v'].__setitem__('parentId', 'j-ghost'),
         'JOB_PARENT_DANGLING', '悬空 parentId'),
        (claimed_doc, lambda d: d['jobs']['j-w'].__setitem__('state', 'weird'),
         'JOB_STATE_INVALID', '状态枚举外值'),
        (fresh_doc, lambda d: d.__setitem__('pendingTargetIds', []),
         'PENDING_MISMATCH', 'pending 与 targets 不一致'),
        (claimed_doc, lambda d: d['jobs']['j-w'].__setitem__('attemptIds', ['a-ghost']),
         'JOB_ATTEMPT_DANGLING', 'job 引用不存在的 attempt'),
        (succeeded_doc, lambda d: d['attempts']['a-1'].__setitem__('state', 'flying'),
         'ATTEMPT_STATE_INVALID', '尝试状态枚举外值'),
        (succeeded_doc, lambda d: d['attempts'].__setitem__(
            'a-moved', d['attempts'].pop('a-1')),
         'JOB_ATTEMPT_DANGLING', '作业登记的尝试 id 失联'),
        (succeeded_doc, lambda d: d.__setitem__('schemaVersion', 7),
         contracts.UNKNOWN_CHECKPOINT_SCHEMA, '未知 schemaVersion'),
        (succeeded_doc, lambda d: d['coverage'].__setitem__('targetCompleted', 99),
         'COVERAGE_MISMATCH', 'coverage 与重算不一致'),
        (succeeded_doc, lambda d: d['jobs'].__setitem__(
            'j-v2', {'parentId': '', 'rootId': 'j-v2', 'orderedPrimaryTargetIds': ['t-1'],
                     'state': 'queued', 'children': [], 'attemptIds': []}),
         'TARGET_OWNER_CONFLICT', '目标被无父子关系的两个作业认领'),
    ]
    for base_doc, mutate, expected_code, name in cases:
        broken = copy.deepcopy(base_doc)
        mutate(broken)
        errors = batch_state.validate_plan_doc(broken)
        check(len(errors) == 1 and errors[0].get('code') == expected_code,
              '%s：%s 恰报一条（%s）' % (tag, name, expected_code),
              actual=errors, expected=expected_code)
    check(batch_state.validate_plan_doc(succeeded_doc) == []
          and batch_state.validate_plan_doc(claimed_doc) == [],
          '%s：干净计划（成功叶/running 作业）零错误' % tag,
          actual=batch_state.validate_plan_doc(succeeded_doc))


# --- 场景 9：与 contracts 一致性 --------------------------------------------------------


def scenario_contracts_consistency():
    tag = '场景9 contracts一致性'
    targets = make_targets(2)
    job_id = contracts.stable_job_id(1, '', 'root', targets)
    again = contracts.stable_job_id(1, '', 'root', targets)
    doc = make_plan(2, targets=targets)
    doc = batch_state.apply_event(doc, event_created(job_id, ['t-1', 't-2']))
    check(job_id == again and job_id in doc['jobs'],
          '%s：stable_job_id 派生 id 确定，且可直接作为 jobId 建作业' % tag,
          actual={'jobId': job_id, 'deterministic': job_id == again})
    planned = [contracts.target_digest(doc['targets'][tid]) for tid in ('t-1', 't-2')]
    completed_now = batch_state.completed_target_digests(doc, doc['targets'])
    not_yet = contracts.coverage_check(planned, completed_now)
    check(completed_now == [] and not_yet.get('ok') is False and not_yet.get('missing'),
          '%s：未完成时 coverage_check 报 missing（复用契约实现）' % tag,
          actual=not_yet)
    full = batch_state.apply_event(doc, {'type': 'job_claimed', 'jobId': job_id,
                                         'attemptId': 'a-1'})
    full = batch_state.apply_event(full, event_started('a-1', job_id))
    full = batch_state.apply_event(full, event_attempt_ok('a-1'))
    full = batch_state.apply_event(full, {'type': 'job_succeeded', 'jobId': job_id,
                                          'candidateCount': 1, 'resultDigest': 'digest-root'})
    digests = batch_state.completed_target_digests(full, full['targets'])
    check(sorted(digests) == sorted(planned)
          and contracts.coverage_check(planned, digests).get('ok') is True,
          '%s：全叶成功后叶覆盖与计划集合守恒（contracts.coverage_check ok）' % tag,
          actual={'completed': digests, 'planned': planned})
    base = contracts.final_state_check({'generate': full})
    wrapped = batch_state.final_check(full)
    check(base == wrapped,
          '%s：final_check 与 contracts.final_state_check 输出一致（无在途叠加时）' % tag,
          actual={'contracts': base, 'batch_state': wrapped})
    check(batch_state.plan_fingerprint_for(FINGERPRINT_PARTS)
          == contracts.plan_fingerprint(FINGERPRINT_PARTS),
          '%s：plan_fingerprint_for 复用契约实现' % tag)
    check(batch_state.fingerprint_mismatch(full['fingerprint'], FINGERPRINT_PARTS) is False
          and batch_state.fingerprint_mismatch(full['fingerprint'],
                                               FINGERPRINT_PARTS[:-1] + ['changed']) is True
          and batch_state.fingerprint_mismatch(None, FINGERPRINT_PARTS) is True,
          '%s：fingerprint_mismatch 同 parts=False / 改任一部分=True / 缺失=True' % tag)
    # stored usage 是 normalize_usage 形状；usage_aggregate 需 snake_case 原始键
    # （契约非幂等，D07 适配输入复用），此处用原始 USAGE_OK 直接对照期望聚合。
    usage = contracts.usage_aggregate([USAGE_OK])
    check(full['usageAggregate'] == usage,
          '%s：usageAggregate 与 contracts.usage_aggregate 逐字节一致' % tag,
          actual={'stored': full['usageAggregate'], 'expected': usage})
    soft = batch_state.checkpoint_soft_limit(2)
    check(soft == contracts.checkpoint_soft_limit_bytes(2)
          and soft == contracts.MAX_CHECKPOINT_BYTES - contracts.CHECKPOINT_TERMINAL_RESERVE_BYTES
          - contracts.CHECKPOINT_ATTEMPT_RESERVE_BYTES * 3,
          '%s：checkpoint 软阈值复用契约口径（1MiB−16KiB−8KiB×(在途+1)）' % tag, actual=soft)
    fits = {'generate': full}
    check(batch_state.checkpoint_capacity_ok(fits, 0) is True,
          '%s：小计划容量守卫通过' % tag)
    big_target = {'targetId': 't-big', 'factId': 'f-big',
                  'selector': {'type': 'whole'}, 'kind': 'doc_section',
                  'subjectKey': 's', 'materialId': 'm'}
    huge = make_plan(1, targets=[big_target])
    huge['targets']['t-big']['factId'] = 'f-' + 'x' * 1100000   # 注入超软阈值的超大字段
    check(batch_state.checkpoint_capacity_ok({'generate': huge}, 0) is False,
          '%s：超软阈值容量守卫拒绝（终态完成预留按完整序列化测量）' % tag)
    many = make_plan(65)
    check_raises(lambda: batch_state.apply_event(
        many, event_created('j-65', ['t-%d' % n for n in range(1, 66)])),
        '%s：单作业主目标超 64 上限拒绝' % tag)
    ok65, _ = batch_state.can_dispatch(many)
    check(ok65 is True, '%s：65 目标注册本身不受单作业上限限制（4096 内合法）' % tag)


# --- 场景 10：superseded 重打包与 blocked ------------------------------------------------


def scenario_supersede_and_block():
    tag = '场景10 superseded与blocked'
    doc = make_plan(1)
    doc = batch_state.apply_event(doc, event_created('j-old', ['t-1']))
    check(doc['pendingTargetIds'] == [], '%s：建作业后目标离开 pending' % tag,
          actual=doc['pendingTargetIds'])
    doc = batch_state.apply_event(doc, {'type': 'job_superseded', 'jobId': 'j-old',
                                        'replacedBy': [], 'targetMapping': {'t-1': 'j-new'}})
    check(doc['jobs']['j-old']['state'] == 'superseded'
          and doc['pendingTargetIds'] == ['t-1'],
          '%s：superseded 释放目标回 pending（等待重打包）' % tag,
          actual={'state': doc['jobs']['j-old']['state'], 'pending': doc['pendingTargetIds']})
    doc = batch_state.apply_event(doc, event_created('j-new', ['t-1']))
    check(doc['pendingTargetIds'] == [] and doc['coverage']['targetCompleted'] == 0,
          '%s：替代作业认领后 pending 清空、完成数不变' % tag,
          actual={'pending': doc['pendingTargetIds'], 'coverage': doc['coverage']})
    doc = batch_state.apply_event(doc, {'type': 'job_blocked', 'jobId': 'j-new',
                                        'code': contracts.OVERSIZED_ATOMIC_TARGET,
                                        'message': '不可分目标超限'})
    check(doc['jobs']['j-new']['state'] == 'blocked'
          and doc['jobs']['j-new']['errorCode'] == contracts.OVERSIZED_ATOMIC_TARGET
          and doc['jobs']['j-old'].get('supersededBy') == [],
          '%s：queued 作业可直接 blocked（规划期即知不可容纳），code 入 errorCode' % tag,
          actual=doc['jobs']['j-new'])
    final = batch_state.final_check(doc)
    check(final.get('ok') is False and 'UNFINISHED_JOB' in violation_codes(final),
          '%s：superseded/blocked 作业存在时 final_check 不通过（contracts 口径）' % tag,
          actual=violation_codes(final))
    check(batch_state.validate_plan_doc(doc) == [],
          '%s：superseded+blocked 计划结构零错误' % tag,
          actual=batch_state.validate_plan_doc(doc))
    queued_old = batch_state.apply_event(make_plan(1), event_created('j-old2', ['t-1']))
    check_raises(lambda: batch_state.apply_event(
        queued_old, {'type': 'job_superseded', 'jobId': 'j-old2',
                     'replacedBy': ['j-ghost']}),
        '%s：replacedBy 引用不存在作业拒绝' % tag)


# --- 入口 -----------------------------------------------------------------------------

SCENARIOS = [
    ('create_plan_shape', scenario_create_plan_shape),
    ('happy_path_chain', scenario_happy_path_chain),
    ('input_immutable', scenario_input_immutable),
    ('illegal_transitions', scenario_illegal_transitions),
    ('split_chain', scenario_split_chain),
    ('attempt_unknown', scenario_attempt_unknown),
    ('can_dispatch', scenario_can_dispatch),
    ('bounds_and_calibration', scenario_bounds_and_calibration),
    ('validate_plan_doc', scenario_validate_plan_doc),
    ('contracts_consistency', scenario_contracts_consistency),
    ('supersede_and_block', scenario_supersede_and_block),
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
