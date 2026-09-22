"""D08 在线持久化回归：OnlinePersistence 事务适配、分页与恢复（batch_persistence.py）。

被测模块：workbench/ontology_build/batch_persistence.py（契约来源
workbench/ontology_build/batch_contracts.py「持久化接口」节，D00 冻结）；
新增存储函数：workbench/storage/ontology_build.py 的 run_checkpoint_doc /
iterate_candidates_page（只追加，既有函数未改）。

场景（对应整合计划 v3 §8 D08 行 + 执行指令验收清单）：
 1. save_plan→load 往返一致（schema2 完整）；重复 save_plan 拒绝覆盖已有计划。
 2. claim_job 先持久化：claim 后另开只读连接读库断言 job running + attempt started
    （runAttempt/requestedMaxTokens 落库），返回 {'ok','attemptId','requestedMaxTokens'}。
 3. commit_success 原子：lease 失配路径返回 False，库内无部分状态
    （job 仍 running、无候选、attempt 仍 started）。
 4. 晚结果拒绝：lease 轮换后旧 lease 的 commit_success/commit_failure/commit_split
    一律 False；新 lease 持有者正常写另一 job，最终库里只有新基线的结果。
 5. mark_interrupted_unknown：started→interrupted_unknown，usageAggregate
    unknownUsageCalls=1（真实计费未知不计 0）；重复扫描幂等返回 0。
 6. commit_split：父作业在途尝试记账 + 父 split + 子 job 同事务落库；重复回调幂等
    返回 True；子 job 可再 claim（queued→running）。
 7. iterate_candidates：直插 501 候选，keyset 分页迭代读满 501 无重无漏；
    all_candidates(limit=500) 恰 500（证明便利函数不再当全量）。
 8. checkpoint 容量：>1MiB 计划 save_plan 抛 CheckpointCapacityError 且库内状态不变；
    事前种入超限 checkpoint 后 claim 同样抛、job 仍 queued。
 9. 幂等：同一 job 重复 commit_success 不重复插候选、返回 True；候选 id 确定性
    （'bc-'+jobId[2:14]+'-<10hex>'），origin 追加 planEpoch/jobId，usage 聚合正确。

隔离（AGENTS.md 测试隔离铁律）：每个场景独立临时根（WIZ_WORKBENCH_ROOT + 该根下
WIZ_DATABASE_URL），不访问网络、不连真实库、不触碰工作树数据副本；结束销毁全部临时根。

运行：python3 tests/test_ontology_build_budget_storage.py
"""
import json
import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from workbench import storage  # noqa: E402
from workbench.ontology_build import batch_contracts as contracts  # noqa: E402
from workbench.ontology_build import batch_persistence  # noqa: E402
from workbench.ontology_build import batch_state  # noqa: E402
from workbench.storage import engine as sto  # noqa: E402
from workbench.storage import ontology_build as store  # noqa: E402

UID = 'budget-storage-owner'
PASSED = []
FAILED = []
SEQ = [0]
ROOTS = []


def _short(value, limit=400):
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = repr(value)
    return text if len(text) <= limit else text[:limit] + '…'


def check(cond, message, actual=None):
    SEQ[0] += 1
    if cond:
        PASSED.append(message)
        print('通过 %d) %s' % (SEQ[0], message))
        return True
    FAILED.append(message)
    print('[失败] %d) %s' % (SEQ[0], message))
    if actual is not None:
        print('  实际: ' + _short(actual))
    return False


def check_raises(fn, exc_type, message):
    SEQ[0] += 1
    try:
        fn()
    except exc_type as exc:
        PASSED.append(message)
        print('通过 %d) %s（%s）' % (SEQ[0], message, type(exc).__name__))
        return True
    except Exception as exc:  # noqa: BLE001 - 类型不对也算失败
        FAILED.append(message)
        print('[失败] %d) %s  实际: %s: %s' % (SEQ[0], message, type(exc).__name__, _short(exc, 160)))
        return False
    FAILED.append(message)
    print('[失败] %d) %s  实际: 未抛出异常' % (SEQ[0], message))
    return False


def new_isolated_root(tag):
    root = Path(tempfile.mkdtemp(prefix='wiz_budget_storage_%s_' % tag))
    ROOTS.append(root)
    os.environ['WIZ_WORKBENCH_ROOT'] = str(root)
    os.environ['WIZ_DATABASE_URL'] = 'sqlite:///' + str(root / 'data' / 'workbench.sqlite3')
    sto.reset_engine()
    storage.mark_unready()
    storage.ensure_ready()
    if str(root) not in sto.resolve_url():
        raise AssertionError('隔离根未生效：%s' % sto.resolve_url())
    return root


def cleanup_roots():
    for root in ROOTS:
        shutil.rmtree(root, ignore_errors=True)
    del ROOTS[:]


# --- 种子与读取 ----------------------------------------------------------------------

def seed_task_run():
    """建任务 + 批次 + generate 运行，返回 (task_id, batch_id, run_id, lease)。"""

    def body(conn):
        task_id = store.create_task(conn, UID, '预算存储回归')
        batch_id = store.create_batch(conn, task_id, UID, '', {})
        run_id, lease = store.create_run(conn, task_id, UID, 'generate', {}, batch_id)
        return task_id, batch_id, run_id, lease

    with sto.write_tx() as tx:
        return tx.run(body)


def make_plan(batch_id, target_count=2, profile=None):
    """构造合法 schema2 计划：targets + 单个根作业（认领全部目标）。"""
    targets = [{'targetId': 't-%d' % i, 'factId': 'f-%d' % i, 'selector': {'type': 'whole'},
                'subjectKey': 's-%d' % (i % 2), 'kind': 'single_fact'}
               for i in range(target_count)]
    if profile is None:
        profile = {'enabled': True, 'providerId': 'prov', 'model': 'm1',
                   'contextTokens': 8000, 'outputLimitTokens': 2000,
                   'requestOutputTokens': 1500, 'targetRatio': 0.5,
                   'limitsSource': 'configured', 'errors': [],
                   'effective': {'outputCap': 1500, 'targetBudget': 750, 'inputReserve': 2048}}
    doc = batch_state.create_plan(batch_id, 1, 1, targets, profile, contracts.CODEC_LEGACY,
                                  ['part-a', 'part-b'])
    return batch_state.apply_event(doc, {'type': batch_state.EVENT_JOB_CREATED,
                                         'jobId': 'j-root',
                                         'orderedPrimaryTargetIds': [t['targetId'] for t in targets]})


def read_generate_doc(run_id):
    """另开只读连接读库解析 checkpoint_json['generate']（不经过 persistence）。"""
    with sto.read_connection() as conn:
        row = store.get_run(conn, run_id, UID)
    if row is None or not row['checkpoint_json']:
        return None
    checkpoint = json.loads(row['checkpoint_json'])
    generate = checkpoint.get('generate') if isinstance(checkpoint, dict) else None
    return generate if isinstance(generate, dict) else None


def read_raw_checkpoint(run_id):
    with sto.read_connection() as conn:
        row = store.get_run(conn, run_id, UID)
    if row is None or not row['checkpoint_json']:
        return {}
    try:
        return json.loads(row['checkpoint_json'])
    except (ValueError, TypeError):
        return {}


def read_candidates(task_id):
    with sto.read_connection() as conn:
        return store.all_candidates(conn, task_id, UID, include_merged=True)


def rotate_lease(run_id):
    with sto.write_tx() as tx:
        return str(tx.run(lambda conn: store.rotate_run_lease(conn, run_id, UID)) or '')


def save_plan_direct(run_id, doc):
    """测试专用：绕过 persistence 直接种 checkpoint（构造超限/病态前置状态用）。"""

    def body(conn):
        store.update_run(conn, run_id, UID, checkpoint={'generate': doc})

    with sto.write_tx() as tx:
        tx.run(body)


API_USAGE = {'promptTokens': 10, 'completionTokens': 20, 'reasoningTokens': 0,
             'totalTokens': 30, 'usageSource': 'api'}


def make_candidate(key):
    return {'type': 'object', 'key': key, 'name': '候选-' + key, 'definition': 'D08 回归候选',
            'fields': {}, 'ownerKey': '', 'evidence': {'def': ['f-0']},
            'evidenceStatus': 'supported', 'decision': 'include', 'conflicts': [], 'issues': []}


# --- 场景 ----------------------------------------------------------------------------

def scenario_save_load_roundtrip():
    """场景1：save_plan→load 往返一致；重复 save 拒绝覆盖。"""
    new_isolated_root('s1')
    task_id, batch_id, run_id, lease = seed_task_run()
    plan = make_plan(batch_id, target_count=3)
    p = batch_persistence.OnlinePersistence(task_id, UID, run_id)
    p.save_plan(plan, lease)
    loaded = p.load()
    check(isinstance(loaded, dict) and int(loaded.get('schemaVersion') or 0) == 2,
          '场景1 load 返回 schema2 计划', loaded and loaded.get('schemaVersion'))
    check(json.dumps(loaded, sort_keys=True, ensure_ascii=False)
          == json.dumps(plan, sort_keys=True, ensure_ascii=False),
          '场景1 save→load 往返逐字段一致（targets/jobs/coverage/fingerprint 全保留）')
    check(loaded.get('planId') == plan['planId']
          and loaded.get('budgetProfile', {}).get('effective', {}).get('outputCap') == 1500,
          '场景1 planId 与 budgetProfile 有界子集完整', loaded.get('planId'))
    check_raises(lambda: p.save_plan(make_plan(batch_id, target_count=1), lease),
                 batch_persistence.PersistenceError, '场景1 重复 save_plan 拒绝覆盖已有计划')
    # 无 schema2 计划时 load 返回 None（另一任务空运行）
    _, _, empty_run, _ = seed_task_run()
    check(batch_persistence.OnlinePersistence(task_id, UID, empty_run).load() is None,
          '场景1 无计划运行 load 返回 None')


def scenario_claim_persists_first():
    """场景2：claim_job 先持久化再返回；另开连接读库可见 running + started。"""
    new_isolated_root('s2')
    task_id, batch_id, run_id, lease = seed_task_run()
    save_plan_direct(run_id, make_plan(batch_id, target_count=2))
    p = batch_persistence.OnlinePersistence(task_id, UID, run_id)
    result = p.claim_job(lease, 'j-root', 3)
    check(isinstance(result, dict) and result.get('ok') is True
          and str(result.get('attemptId') or '').startswith('a-'),
          '场景2 claim 返回 ok+attemptId（契约形状）', result)
    check(result.get('requestedMaxTokens') == 1500,
          '场景2 requestedMaxTokens 取 profile.effective.outputCap',
          result.get('requestedMaxTokens'))
    doc = read_generate_doc(run_id)  # 另开的独立只读连接
    job = (doc or {}).get('jobs', {}).get('j-root') or {}
    check(job.get('state') == contracts.JOB_RUNNING, '场景2 库内 job 已 running（先持久化）',
          job.get('state'))
    attempts = (doc or {}).get('attempts') or {}
    attempt = attempts.get(result['attemptId']) or {}
    def _int_or_none(value):
        return int(value) if isinstance(value, int) and not isinstance(value, bool) else None

    check(attempt.get('state') == contracts.ATTEMPT_STARTED
          and _int_or_none(attempt.get('runAttempt')) == 3
          and _int_or_none(attempt.get('requestedMaxTokens')) == 1500
          and _int_or_none(attempt.get('sequence')) == 0,
          '场景2 库内 attempt started 且 runAttempt/sequence/maxTokens 落库', attempt)
    check(job.get('attemptIds') == [result['attemptId']],
          '场景2 作业 attemptIds 登记本次尝试', job.get('attemptIds'))
    check_raises(lambda: p.claim_job(lease, 'j-missing', 1), ValueError,
                 '场景2 claim 不存在作业抛 ValueError')
    check_raises(lambda: p.claim_job('wrong-lease', 'j-root', 1),
                 batch_persistence.LeaseLostError, '场景2 claim lease 失配必须抛出')
    check(len(((read_generate_doc(run_id) or {}).get('attempts') or {})) == 1,
          '场景2 失败 claim 未残留任何 attempt（整体回滚）')


def scenario_commit_success_atomic():
    """场景3：lease 失配时 commit_success 拒绝且库内无部分状态。"""
    new_isolated_root('s3')
    task_id, batch_id, run_id, lease = seed_task_run()
    save_plan_direct(run_id, make_plan(batch_id, target_count=1))
    p = batch_persistence.OnlinePersistence(task_id, UID, run_id)
    claim = p.claim_job(lease, 'j-root', 1)
    rotate_lease(run_id)  # 模拟执行权被新接管轮换
    result = p.commit_success(lease, 'j-root', claim['attemptId'], [make_candidate('lost')],
                              API_USAGE, 'digest-old')
    check(result is False, '场景3 lease 失配 commit_success 返回 False（不抛不假成功）', result)
    doc = read_generate_doc(run_id)
    job = (doc or {}).get('jobs', {}).get('j-root') or {}
    attempt = ((doc or {}).get('attempts') or {}).get(claim['attemptId']) or {}
    check(job.get('state') == contracts.JOB_RUNNING, '场景3 job 仍 running（无部分状态）',
          job.get('state'))
    check(attempt.get('state') == contracts.ATTEMPT_STARTED, '场景3 attempt 仍 started',
          attempt.get('state'))
    check(read_candidates(task_id) == [], '场景3 未写入任何候选', read_candidates(task_id))
    check((doc or {}).get('usageAggregate', {}).get('calls') == 0,
          '场景3 usage 聚合未被污染', (doc or {}).get('usageAggregate'))


def scenario_late_results_rejected():
    """场景4：lease 轮换后旧 lease 的一切 commit_* 被拒；新基线写入不受影响。"""
    new_isolated_root('s4')
    task_id, batch_id, run_id, lease = seed_task_run()
    doc = make_plan(batch_id, target_count=2)
    doc = batch_state.apply_event(doc, {'type': batch_state.EVENT_JOB_CREATED,
                                        'jobId': 'j-b',
                                        'orderedPrimaryTargetIds': ['t-1']})
    save_plan_direct(run_id, doc)
    p = batch_persistence.OnlinePersistence(task_id, UID, run_id)
    old = p.claim_job(lease, 'j-root', 1)  # j-root 主目标 t-0
    new_lease = rotate_lease(run_id)
    check(p.commit_success(lease, 'j-root', old['attemptId'], [make_candidate('a')], API_USAGE,
                           'd-a') is False, '场景4 旧 lease commit_success 拒绝')
    check(p.commit_failure(lease, 'j-root', old['attemptId'], 'NETWORK_RETRYABLE', '晚到',
                           API_USAGE) is False, '场景4 旧 lease commit_failure 拒绝')
    check(p.commit_split(lease, 'j-root', [{'jobId': 'j-x',
                                            'orderedPrimaryTargetIds': ['t-0']}],
                         API_USAGE) is False, '场景4 旧 lease commit_split 拒绝')
    # 新 lease 持有者正常推进另一作业
    claim_b = p.claim_job(new_lease, 'j-b', 2)
    ok = p.commit_success(new_lease, 'j-b', claim_b['attemptId'], [make_candidate('b')],
                          API_USAGE, 'd-b')
    check(ok is True, '场景4 新 lease 持有者 commit_success 正常', ok)
    final = read_generate_doc(run_id)
    jobs = (final or {}).get('jobs') or {}
    check(jobs.get('j-root', {}).get('state') == contracts.JOB_RUNNING,
          '场景4 旧作业保持 running（未被晚结果改写）', jobs.get('j-root', {}).get('state'))
    check(jobs.get('j-b', {}).get('state') == contracts.JOB_SUCCEEDED,
          '场景4 新作业 succeeded（新基线生效）', jobs.get('j-b', {}).get('state'))
    names = sorted(c['key'] for c in read_candidates(task_id))
    check(names == ['b'], '场景4 库内只有新基线的候选', names)


def scenario_mark_interrupted_unknown():
    """场景5：崩溃恢复 started→interrupted_unknown；unknown usage 计次不计 0。"""
    new_isolated_root('s5')
    task_id, batch_id, run_id, lease = seed_task_run()
    save_plan_direct(run_id, make_plan(batch_id, target_count=1))
    p = batch_persistence.OnlinePersistence(task_id, UID, run_id)
    claim = p.claim_job(lease, 'j-root', 1)
    changed = p.mark_interrupted_unknown([claim['attemptId']])
    check(changed == 1, '场景5 返回实际收口尝试数 1', changed)
    doc = read_generate_doc(run_id)
    attempt = ((doc or {}).get('attempts') or {}).get(claim['attemptId']) or {}
    check(attempt.get('state') == contracts.ATTEMPT_INTERRUPTED_UNKNOWN,
          '场景5 attempt 状态 interrupted_unknown', attempt.get('state'))
    aggregate = (doc or {}).get('usageAggregate') or {}
    check(int(aggregate.get('unknownUsageCalls') or 0) == 1
          and aggregate.get('completionTokens') is None,
          '场景5 usageAggregate unknownUsageCalls=1 且 token 不冒充 0', aggregate)
    check(p.mark_interrupted_unknown([claim['attemptId']]) == 0,
          '场景5 重复扫描幂等返回 0（非 started 不再收口）')
    check(p.mark_interrupted_unknown([]) == 0 and p.mark_interrupted_unknown(['a-nope']) == 0,
          '场景5 空表/未知 attempt 返回 0 且不写库')


def scenario_commit_split():
    """场景6：commit_split 父子同事务；父尝试 usage 入总账；子 job 可再 claim。"""
    new_isolated_root('s6')
    task_id, batch_id, run_id, lease = seed_task_run()
    save_plan_direct(run_id, make_plan(batch_id, target_count=2))
    p = batch_persistence.OnlinePersistence(task_id, UID, run_id)
    claim = p.claim_job(lease, 'j-root', 1)
    truncated = {'prompt_tokens': 5, 'completion_tokens': 7, 'total_tokens': 12,
                 'usage_source': 'api'}
    children = [{'jobId': 'j-left', 'orderedPrimaryTargetIds': ['t-0'], 'splitPath': 'root/0'},
                {'jobId': 'j-right', 'orderedPrimaryTargetIds': ['t-1'], 'splitPath': 'root/1'}]
    ok = p.commit_split(lease, 'j-root', children, truncated, finish_reason='length')
    check(ok is True, '场景6 commit_split 返回 True', ok)
    doc = read_generate_doc(run_id)
    parent = (doc or {}).get('jobs', {}).get('j-root') or {}
    check(parent.get('state') == contracts.JOB_SPLIT
          and sorted(parent.get('children') or []) == ['j-left', 'j-right'],
          '场景6 父作业 split 且子作业同事务入计划', parent)
    attempt = ((doc or {}).get('attempts') or {}).get(claim['attemptId']) or {}
    check(attempt.get('state') == contracts.ATTEMPT_SUCCEEDED
          and attempt.get('finishReason') == 'length',
          '场景6 父截断尝试已记账（succeeded + finishReason）', attempt)
    aggregate = (doc or {}).get('usageAggregate') or {}
    check(int(aggregate.get('completionTokens') or 0) == 7
          and int(aggregate.get('calls') or 0) == 1,
          '场景6 父截断 usage 入总账（completion=7, calls=1）', aggregate)
    check(int((doc or {}).get('coverage', {}).get('targetPending') or 0) == 2,
          '场景6 父 split 不算成功（pending 保持 2）', (doc or {}).get('coverage'))
    check(p.commit_split(lease, 'j-root', children, truncated) is True,
          '场景6 重复 commit_split 幂等返回 True')
    child_claim = p.claim_job(lease, 'j-left', 1)
    check(child_claim.get('ok') is True, '场景6 子 job 可再 claim（queued→running）', child_claim)
    left = (read_generate_doc(run_id) or {}).get('jobs', {}).get('j-left') or {}
    check(left.get('state') == contracts.JOB_RUNNING
          and left.get('parentId') == 'j-root' and left.get('splitPath') == 'root/0',
          '场景6 子作业亲缘与 splitPath 落库', left)


def scenario_iterate_candidates_pages():
    """场景7：501 候选 keyset 分页无重无漏；all_candidates 恰 500。"""
    new_isolated_root('s7')
    task_id, batch_id, run_id, _lease = seed_task_run()

    def body(conn):
        for i in range(501):
            store.create_candidate(conn, task_id, UID, batch_id,
                                   {'type': 'object', 'key': 'k-%03d' % i,
                                    'name': '候选%03d' % i, 'definition': '分页回归'})

    with sto.write_tx() as tx:
        tx.run(body)
    p = batch_persistence.OnlinePersistence(task_id, UID, run_id)
    collected = []
    for page in p.iterate_candidates(page_size=200):
        collected.extend(page)
    ids = [c['id'] for c in collected]
    check(len(collected) == 501, '场景7 迭代读满 501 条', len(collected))
    check(len(set(ids)) == 501, '场景7 无重复（无重）', len(set(ids)))
    with sto.read_connection() as conn:
        expected_ids = {row[0] for row in conn.execute(
            sto.text('SELECT candidate_id FROM wb_build_candidates '
                     'WHERE task_id = :t AND owner_user_id = :o'),
            {'t': task_id, 'o': UID}).all()}
    check(set(ids) == expected_ids, '场景7 与直插集合完全一致（无漏）',
          len(expected_ids - set(ids)))
    sizes = [len(page) for page in p.iterate_candidates(page_size=200)]
    check(sizes == [200, 200, 101], '场景7 页大小 200/200/101（keyset 收敛）', sizes)
    with sto.read_connection() as conn:
        convenience = store.all_candidates(conn, task_id, UID)
    check(len(convenience) == 500, '场景7 all_candidates(limit=500) 恰 500 条（不再当全量）',
          len(convenience))


def scenario_checkpoint_capacity():
    """场景8：>1MiB 计划 save/claim 抛 CheckpointCapacityError 且库内状态不变。"""
    new_isolated_root('s8')
    task_id, batch_id, run_id, lease = seed_task_run()
    huge = make_plan(batch_id, target_count=1)
    huge['log'] = ['x' * 1024] * 1100  # 约 1.13MiB 合法填充（log 不参与结构校验）
    p = batch_persistence.OnlinePersistence(task_id, UID, run_id)
    check_raises(lambda: p.save_plan(huge, lease), batch_persistence.CheckpointCapacityError,
                 '场景8 超限计划 save_plan 抛 CheckpointCapacityError')
    check(read_generate_doc(run_id) is None and read_raw_checkpoint(run_id) == {},
          '场景8 save 失败后库内无任何计划（状态不变）', read_raw_checkpoint(run_id))
    # 事前直接种入超限 checkpoint（模拟历史病态数据），claim 同样被容量守卫拦截
    bloated = make_plan(batch_id, target_count=1)
    bloated['log'] = ['y' * 1024] * 1100
    save_plan_direct(run_id, bloated)
    check_raises(lambda: p.claim_job(lease, 'j-root', 1),
                 batch_persistence.CheckpointCapacityError, '场景8 超限 checkpoint 下 claim 抛容量错误')
    doc = read_generate_doc(run_id)
    check((doc or {}).get('jobs', {}).get('j-root', {}).get('state') == contracts.JOB_QUEUED,
          '场景8 claim 失败后 job 仍 queued', (doc or {}).get('jobs', {}).get('j-root'))
    check((doc or {}).get('attempts') == {}, '场景8 claim 失败未残留尝试',
          (doc or {}).get('attempts'))


def scenario_commit_success_idempotent():
    """场景9：重复 commit_success 幂等；候选 id 确定性、origin 追加、usage 聚合正确。"""
    new_isolated_root('s9')
    task_id, batch_id, run_id, lease = seed_task_run()
    save_plan_direct(run_id, make_plan(batch_id, target_count=1))
    p = batch_persistence.OnlinePersistence(task_id, UID, run_id)
    claim = p.claim_job(lease, 'j-root', 1)
    candidates = [make_candidate('k1'), make_candidate('k2')]
    ok1 = p.commit_success(lease, 'j-root', claim['attemptId'], candidates, API_USAGE,
                           'digest-1', attempt_meta={'finishReason': 'stop', 'bytes': 2048,
                                                     'durationMs': 1200})
    check(ok1 is True, '场景9 首次 commit_success 返回 True', ok1)
    ok2 = p.commit_success(lease, 'j-root', claim['attemptId'], candidates, API_USAGE,
                           'digest-1', attempt_meta={'finishReason': 'stop', 'bytes': 2048,
                                                     'durationMs': 1200})
    check(ok2 is True, '场景9 重复 commit_success 幂等返回 True', ok2)
    stored = read_candidates(task_id)
    check(len(stored) == 2, '场景9 重复提交不重复插候选（仍 2 条）', len(stored))
    prefix = 'bc-' + 'j-root'[2:14] + '-'
    check(all(str(c['id']).startswith(prefix) and len(str(c['id'])) == len(prefix) + 10
              for c in stored), '场景9 候选 id 确定性派生 bc-<jobId[2:14]>-<10hex>',
          [c['id'] for c in stored])
    origins = [c['origin'] for c in stored]
    check(all(o.get('planEpoch') == 1 and o.get('jobId') == 'j-root' for o in origins),
          '场景9 origin 追加 planEpoch/jobId', origins)
    doc = read_generate_doc(run_id)
    job = (doc or {}).get('jobs', {}).get('j-root') or {}
    attempt = ((doc or {}).get('attempts') or {}).get(claim['attemptId']) or {}
    check(job.get('state') == contracts.JOB_SUCCEEDED and job.get('candidateCount') == 2
          and job.get('resultDigest') == 'digest-1',
          '场景9 job succeeded + candidateCount/resultDigest 落库', job)
    check(attempt.get('state') == contracts.ATTEMPT_SUCCEEDED
          and attempt.get('finishReason') == 'stop' and attempt.get('bytes') == 2048
          and attempt.get('durationMs') == 1200,
          '场景9 attempt succeeded + attempt_meta 记账', attempt)
    aggregate = (doc or {}).get('usageAggregate') or {}
    check(int(aggregate.get('completionTokens') or 0) == 20
          and int(aggregate.get('promptTokens') or 0) == 10
          and int(aggregate.get('calls') or 0) == 1
          and int(aggregate.get('unknownUsageCalls') or 0) == 0,
          '场景9 usage 聚合正确（camelCase 回喂不判 absent）', aggregate)
    check(int((doc or {}).get('coverage', {}).get('targetCompleted') or 0) == 1,
          '场景9 成功叶计入 coverage.targetCompleted', (doc or {}).get('coverage'))
    # 已 succeeded 的作业再收到 failure 回调：拒绝改写（返回 False，终态保持）
    check(p.commit_failure(lease, 'j-root', claim['attemptId'], 'NETWORK_RETRYABLE', '晚到失败',
                           API_USAGE) is False, '场景9 succeeded 后 commit_failure 拒绝改写')
    check((read_generate_doc(run_id) or {}).get('jobs', {}).get('j-root', {}).get('state')
          == contracts.JOB_SUCCEEDED, '场景9 终态保持 succeeded')


SCENARIOS = (
    ('save_load_roundtrip', scenario_save_load_roundtrip),
    ('claim_persists_first', scenario_claim_persists_first),
    ('commit_success_atomic', scenario_commit_success_atomic),
    ('late_results_rejected', scenario_late_results_rejected),
    ('mark_interrupted_unknown', scenario_mark_interrupted_unknown),
    ('commit_split', scenario_commit_split),
    ('iterate_candidates_pages', scenario_iterate_candidates_pages),
    ('checkpoint_capacity', scenario_checkpoint_capacity),
    ('commit_success_idempotent', scenario_commit_success_idempotent),
)


def main():
    for name, func in SCENARIOS:
        print('--- %s ---' % name)
        try:
            func()
        except Exception as exc:  # noqa: BLE001 - 场景自身异常不吞：计失败并打印堆栈
            traceback.print_exc()
            check(False, '场景 %s 自身异常：%s' % (name, type(exc).__name__), str(exc))
        print('')
    cleanup_roots()
    total = len(PASSED) + len(FAILED)
    print('========== 汇总 ==========')
    print('通过 %d / %d' % (len(PASSED), total))
    for name in FAILED:
        print('  失败: ' + name)
    if total == 0:
        print('[错误] 未执行任何断言——不算通过')
        return 2
    return 0 if not FAILED else 1


if __name__ == '__main__':
    sys.exit(main())
