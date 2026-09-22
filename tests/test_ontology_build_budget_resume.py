"""D10 终态/lease/取消/旧版本守卫回归（runner.py v2 收尾与恢复防线）。

被测：workbench/ontology_build/runner.py 追加的 D10 守卫（契约来源
workbench/ontology_build/batch_contracts.py「终态检查」节、整合计划 v3 §6、
接口文档 08 §14.5「恢复与版本语义」）；晚结果拒绝复用 OnlinePersistence（D08）
与既有 lease 条件写，取消语义复用既有 check_cancelled/content_tx（本文件不新增语义）。

场景（对应整合计划 v3 §9 场景 4/5/6 + D10 行验收口径）：
 1. final_state_check 不过（有 failed 叶）→ finish_success_guarded 返回 (False,
    violations)，run 保持 running 绝不变 succeeded、usage 不写、任务不推进。
 2. final_state_check 过 → 成功收尾（succeeded + 任务推进 review + usage 落库 +
    checkpoint 保留），与既有成功语义一致。
 3. plan_doc=None → 退化为既有 finish_success 语义：queued 直达成功被拒（返回
    (True, []) 但状态不动）、running 成功收尾、重复收尾幂等（回归）。
 4. resume_plan_guard 版本语义：无 checkpoint / schema1 → legacy_auto/legacy_abstract；
    schema2 → v2_auto/v2_abstract；schemaVersion=3 / 0 / 'x' → UNKNOWN_CHECKPOINT_SCHEMA
    且**候选未被删**（种 2 条断言仍在）、checkpoint 未被改动；任务不匹配 →
    RUN_NOT_FOUND；resumeMode 非法 → RESUME_MODE_INVALID。
 5. resumable_jobs：左子成功右子 failed 的 split 树 → 只返回右子（成功叶不重做）；
    blocked（须显式新计划）与 superseded（已被替换）不返回；queued 返回；非
    schema2/空入参 → []。
 6. 旧 lease 晚结果：v2 计划轮换 lease 后，旧 lease 的 claim 抛 LeaseLostError、
    commit_success/commit_failure 返回 False、checkpoint 无部分状态；直接
    update_run 的 lease 条件写同样被拒；新 lease 持有者正常推进到 succeeded。
 7. 取消不复活：cancel 后（lease 仍匹配）晚到 commit_success 返回 False、候选不落库；
    content_tx 晚内容写抛 Cancelled；finish_success / finish_success_guarded
    （schema2 与 plan_doc=None 两路）都不把 cancelled 改写，任务保持 scope。
 8. auto 不改旧 plan：schema1 checkpoint 上 guard 返回 legacy_auto，且 checkpoint
    逐字节不变（绝不升级 schemaVersion 伪装 v2）。
 9. abstract 显式新 epoch：guard 返回 v2_abstract 且只读（epoch 不动）；测试内手动
    epoch+1 后 guard 再次通过（epoch 语义由调用方驱动，guard 不拦合法新 epoch）；
    附 plan_fingerprint_matches 四态（相等/不等/缺失/双形态入参）。
10. claim_for_resume 接管：进入即轮换 lease、当前线程取得新执行权（条件写命中）、
    旧 lease 条件写被拒；退出作废执行权、线程本地清空；运行不存在不崩溃。
    （既有回归 test_ontology_build_finish_guard.py / test_ontology_build_runner_isolation.py
    由验证命令单独运行，不在本文件重复。）

隔离（AGENTS.md 测试隔离铁律）：每个场景独立临时根（WIZ_WORKBENCH_ROOT + 该根下
WIZ_DATABASE_URL），schema2 checkpoint 一律用 update_run(checkpoint=...) 直接种入；
不访问网络、不连真实库；结束销毁全部临时根。

运行：python3 tests/test_ontology_build_budget_resume.py
"""
import copy
import json
import os
import shutil
import sys
import tempfile
import threading
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from workbench import storage  # noqa: E402
from workbench.ontology_build import batch_contracts as contracts  # noqa: E402
from workbench.ontology_build import batch_persistence  # noqa: E402
from workbench.ontology_build import batch_state  # noqa: E402
from workbench.ontology_build import runner  # noqa: E402
from workbench.storage import engine as sto  # noqa: E402
from workbench.storage import ontology_build as store  # noqa: E402

UID = 'budget-resume-owner'
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
    root = Path(tempfile.mkdtemp(prefix='wiz_budget_resume_%s_' % tag))
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

def seed_task_run(task_name='恢复守卫回归'):
    """建任务（推进到 generating，收尾守卫要核对任务阶段）+ 批次 + generate 运行。"""

    def body(conn):
        task_id = store.create_task(conn, UID, task_name)
        store.touch_task(conn, task_id, UID, status='generating', stage_label='生成中')
        batch_id = store.create_batch(conn, task_id, UID, '', {})
        run_id, _lease = store.create_run(conn, task_id, UID, 'generate', {}, batch_id)
        return task_id, batch_id, run_id

    with sto.write_tx() as tx:
        return tx.run(body)


def seed_extra_run(task_id, batch_id):
    """同一任务下再建一个 generate 运行（版本矩阵用）。"""
    with sto.write_tx() as tx:
        return str(tx.run(lambda conn: store.create_run(conn, task_id, UID, 'generate', {},
                                                        batch_id)[0]))


def read_run(run_id):
    with sto.read_connection() as conn:
        row = store.get_run(conn, run_id, UID)
    return dict(row) if row is not None else None


def read_task(task_id):
    with sto.read_connection() as conn:
        return store.get_task(conn, task_id, UID)


def read_candidates(task_id, batch_id):
    with sto.read_connection() as conn:
        return store.all_candidates(conn, task_id, UID, batch_id=batch_id)


def read_raw_checkpoint(run_id):
    row = read_run(run_id)
    if row is None or not row['checkpoint_json']:
        return {}
    try:
        return json.loads(row['checkpoint_json'])
    except (TypeError, ValueError):
        return {}


def read_generate_doc(run_id):
    checkpoint = read_raw_checkpoint(run_id)
    generate = checkpoint.get('generate') if isinstance(checkpoint, dict) else None
    return generate if isinstance(generate, dict) else None


def write_checkpoint(run_id, payload):
    """测试专用：直接种 checkpoint（构造 schema1/2/3 各版本前置状态）。"""
    with sto.write_tx() as tx:
        tx.run(lambda conn: store.update_run(conn, run_id, UID, checkpoint=payload))


def rotate_lease(run_id):
    with sto.write_tx() as tx:
        return str(tx.run(lambda conn: store.rotate_run_lease(conn, run_id, UID)) or '')


# --- 计划构造（复用 batch_state 事件机，形状与 D07/D08 回归一致）-----------------------

PROFILE = {'enabled': True, 'providerId': 'prov', 'model': 'm1',
           'contextTokens': 8000, 'outputLimitTokens': 2000,
           'requestOutputTokens': 1500, 'targetRatio': 0.5,
           'limitsSource': 'configured', 'errors': [],
           'effective': {'outputCap': 1500, 'targetBudget': 750, 'inputReserve': 2048}}

API_USAGE = {'promptTokens': 10, 'completionTokens': 20, 'reasoningTokens': 0,
             'totalTokens': 30, 'usageSource': 'api'}


def make_plan(batch_id, target_count=2):
    """合法 schema2 计划：targets + 单个根作业（queued 叶）认领全部目标。"""
    targets = [{'targetId': 't-%d' % i, 'factId': 'f-%d' % i, 'selector': {'type': 'whole'},
                'subjectKey': 's-%d' % (i % 2), 'kind': 'single_fact'}
               for i in range(target_count)]
    doc = batch_state.create_plan(batch_id, 1, 1, targets, PROFILE, contracts.CODEC_LEGACY,
                                  ['part-a', 'part-b'])
    return batch_state.apply_event(doc, {'type': batch_state.EVENT_JOB_CREATED,
                                         'jobId': 'j-root',
                                         'orderedPrimaryTargetIds':
                                             [t['targetId'] for t in targets]})


def apply_all(doc, events):
    for event in events:
        doc = batch_state.apply_event(doc, event)
    return doc


def claim_start(job_id, attempt_id):
    return [
        {'type': batch_state.EVENT_JOB_CLAIMED, 'jobId': job_id, 'attemptId': attempt_id},
        {'type': batch_state.EVENT_ATTEMPT_STARTED, 'jobId': job_id, 'attemptId': attempt_id,
         'sequence': 0, 'runAttempt': 1, 'requestedMaxTokens': 1500, 'requestFingerprint': ''},
    ]


def leaf_success_events(job_id, attempt_id, candidates=1):
    return claim_start(job_id, attempt_id) + [
        {'type': batch_state.EVENT_ATTEMPT_SUCCEEDED, 'attemptId': attempt_id,
         'usage': API_USAGE, 'finishReason': 'stop'},
        {'type': batch_state.EVENT_JOB_SUCCEEDED, 'jobId': job_id,
         'candidateCount': candidates, 'resultDigest': 'digest-' + job_id},
    ]


def leaf_failure_events(job_id, attempt_id, error_code='JOB_ATTEMPTS_EXHAUSTED'):
    return claim_start(job_id, attempt_id) + [
        {'type': batch_state.EVENT_ATTEMPT_FAILED, 'attemptId': attempt_id,
         'errorCode': error_code, 'message': '单作业 3 次总额用尽', 'usage': API_USAGE},
        {'type': batch_state.EVENT_JOB_FAILED, 'jobId': job_id, 'errorCode': error_code,
         'message': '单作业 3 次总额用尽'},
    ]


def legacy_generate_doc(batch_id):
    """旧批次计划 checkpoint（与 pipeline._checkpoint 同形，无 schemaVersion 键）。"""
    return {'batchId': batch_id, 'scopeRevision': 1, 'materialRevision': 1,
            'plan': {'modelFactIds': ['f-1'], 'relevant': 1, 'related': 0, 'excluded': 0},
            'batches': {'size': 3, 'total': 1, 'done': [0], 'failed': []}}


def make_candidate(key):
    return {'type': 'object', 'key': key, 'name': '候选-' + key,
            'definition': 'D10 恢复守卫回归候选', 'evidenceStatus': 'supported',
            'decision': 'include', 'alignedKey': 'object:' + key}


# --- 场景 ----------------------------------------------------------------------------

def scenario_final_state_rejects_failed_leaf():
    """场景1：终态检查不过（failed 叶）→ 拒绝收尾，run 绝不变 succeeded。"""
    new_isolated_root('s1')
    task_id, batch_id, run_id = seed_task_run()
    doc = apply_all(make_plan(batch_id, 2), leaf_failure_events('j-root', 'a-1'))
    check(contracts.final_state_check({'generate': doc})['ok'] is False,
          '场景1 前置：final_state_check 确认不过', contracts.final_state_check({'generate': doc}))
    runner.stage(UID, run_id, 'abstract', '生成中')
    write_checkpoint(run_id, {'generate': doc})
    result = runner.finish_success_guarded(UID, run_id, usage={'calls': 9},
                                           plan_doc={'generate': doc})
    ok, violations = result
    check(ok is False, '场景1 finish_success_guarded 拒绝（返回 False）', result)
    check(any(v.get('code') == 'UNFINISHED_JOB' for v in violations),
          '场景1 violations 指出未完成叶（UNFINISHED_JOB）', violations)
    row = read_run(run_id)
    check(row['state'] == 'running', '场景1 run 保持 running（绝不变 succeeded）', row)
    check(json.loads(row['usage_json'] or '{}') == {},
          '场景1 拒绝收尾时 usage 不被写入', row['usage_json'])
    check(read_task(task_id)['status'] == 'generating', '场景1 任务未被推进',
          read_task(task_id)['status'])
    doc_db = read_generate_doc(run_id)
    check(doc_db is not None and doc_db['jobs']['j-root']['state'] == 'failed',
          '场景1 checkpoint 未被守卫改动（failed 叶原样保留）',
          doc_db and doc_db['jobs']['j-root']['state'])


def scenario_final_state_pass_finishes():
    """场景2：终态检查过 → 成功收尾 + 任务推进 review，与既有成功语义一致。"""
    new_isolated_root('s2')
    task_id, batch_id, run_id = seed_task_run()
    doc = apply_all(make_plan(batch_id, 2), leaf_success_events('j-root', 'a-1', candidates=2))
    check(contracts.final_state_check({'generate': doc})['ok'] is True,
          '场景2 前置：final_state_check 通过（叶全 succeeded、覆盖齐、无 blocking）',
          contracts.final_state_check({'generate': doc}))
    runner.stage(UID, run_id, 'abstract', '生成中', {'done': 1, 'total': 1})
    write_checkpoint(run_id, {'generate': doc})
    usage = {'calls': 3, 'promptBytes': 120, 'completionBytes': 456, 'durationMs': 900}
    result = runner.finish_success_guarded(UID, run_id, usage=usage,
                                           plan_doc={'generate': doc})
    check(result == (True, []), '场景2 终态检查过 → 收尾成功（返回 (True, [])）', result)
    row = read_run(run_id)
    check(row['state'] == 'succeeded', '场景2 run 标记 succeeded', row['state'])
    check(json.loads(row['usage_json'] or '{}').get('calls') == 3,
          '场景2 usage 落库（既有 finish_success 通道）', row['usage_json'])
    check(read_task(task_id)['status'] == 'review', '场景2 generate 任务推进「评审初稿」',
          read_task(task_id)['status'])
    doc_db = read_generate_doc(run_id)
    check(doc_db is not None and doc_db.get('planId') == doc['planId'],
          '场景2 成功收尾保留 checkpoint（不抹计划历史）')


def scenario_plan_doc_none_legacy():
    """场景3：plan_doc=None → 退化为既有 finish_success 语义（schema1 兼容回归）。"""
    new_isolated_root('s3')
    task_id, batch_id, run_id = seed_task_run()
    result = runner.finish_success_guarded(UID, run_id)
    check(result == (True, []), '场景3 plan_doc=None 返回 (True, [])（无终态检查）', result)
    check(read_run(run_id)['state'] == 'queued',
          '场景3 queued 直达成功被拒（与既有 finish_success 行为一致）',
          read_run(run_id)['state'])
    check(read_task(task_id)['status'] == 'generating', '场景3 拒绝收尾时任务不动',
          read_task(task_id)['status'])
    runner.stage(UID, run_id, 'abstract', '生成中')
    result = runner.finish_success_guarded(UID, run_id)
    check(result == (True, []), '场景3 running 收尾成功', result)
    check(read_run(run_id)['state'] == 'succeeded', '场景3 run 标记 succeeded',
          read_run(run_id)['state'])
    check(read_task(task_id)['status'] == 'review', '场景3 任务推进 review',
          read_task(task_id)['status'])
    result = runner.finish_success_guarded(UID, run_id)
    check(result == (True, []) and read_run(run_id)['state'] == 'succeeded',
          '场景3 重复收尾幂等（终态不改写）', (result, read_run(run_id)['state']))


def scenario_resume_guard_versions():
    """场景4：resume_plan_guard 版本矩阵；未知版本拒绝恢复且候选不被删。"""
    new_isolated_root('s4')
    task_id, batch_id, run_id = seed_task_run()

    def body(conn):
        store.create_candidate(conn, task_id, UID, batch_id, make_candidate('keep-1'))
        store.create_candidate(conn, task_id, UID, batch_id, make_candidate('keep-2'))

    with sto.write_tx() as tx:
        tx.run(body)

    # 无 checkpoint → legacy
    check(runner.resume_plan_guard(UID, task_id, run_id, 'auto')
          == {'ok': True, 'mode': 'legacy_auto'},
          '场景4 无 checkpoint + auto → legacy_auto（沿用既有批次续跑语义）',
          runner.resume_plan_guard(UID, task_id, run_id, 'auto'))
    check(runner.resume_plan_guard(UID, task_id, run_id, 'abstract')
          == {'ok': True, 'mode': 'legacy_abstract'},
          '场景4 无 checkpoint + abstract → legacy_abstract（全部抽象重跑）')

    # schema1（无 schemaVersion 键，与 pipeline 写库同形）→ legacy
    run_schema1 = seed_extra_run(task_id, batch_id)
    write_checkpoint(run_schema1, {'generate': legacy_generate_doc(batch_id)})
    check(runner.resume_plan_guard(UID, task_id, run_schema1, 'auto')
          == {'ok': True, 'mode': 'legacy_auto'},
          '场景4 schema1 + auto → legacy_auto', )
    check(runner.resume_plan_guard(UID, task_id, run_schema1, 'abstract')['mode']
          == 'legacy_abstract',
          '场景4 schema1 + abstract → legacy_abstract')

    # schema2 → v2
    run_schema2 = seed_extra_run(task_id, batch_id)
    doc2 = make_plan(batch_id, 1)
    write_checkpoint(run_schema2, {'generate': doc2})
    check(runner.resume_plan_guard(UID, task_id, run_schema2, 'auto')
          == {'ok': True, 'mode': 'v2_auto'},
          '场景4 schema2 + auto → v2_auto')
    check(runner.resume_plan_guard(UID, task_id, run_schema2, 'abstract')['mode']
          == 'v2_abstract',
          '场景4 schema2 + abstract → v2_abstract')

    # schemaVersion=3 → 拒绝恢复，候选保留
    run_schema3 = seed_extra_run(task_id, batch_id)
    doc3 = copy.deepcopy(make_plan(batch_id, 1))
    doc3['schemaVersion'] = 3
    write_checkpoint(run_schema3, {'generate': doc3})
    result = runner.resume_plan_guard(UID, task_id, run_schema3, 'auto')
    check(result.get('ok') is False and result.get('code') == 'UNKNOWN_CHECKPOINT_SCHEMA',
          '场景4 schemaVersion=3 → UNKNOWN_CHECKPOINT_SCHEMA（拒绝恢复）', result)
    check(bool(str(result.get('message') or '')), '场景4 拒绝时带可读 message', result)
    check(len(read_candidates(task_id, batch_id)) == 2,
          '场景4 未知版本绝不删候选兜底（种 2 条仍在）',
          [c['name'] for c in read_candidates(task_id, batch_id)])
    check((read_generate_doc(run_schema3) or {}).get('schemaVersion') == 3,
          '场景4 守卫只读：checkpoint 未被改动')
    check(runner.resume_plan_guard(UID, task_id, run_schema3, 'abstract')['code']
          == 'UNKNOWN_CHECKPOINT_SCHEMA',
          '场景4 未知版本 + abstract 同样拒绝（不借机删候选）')

    # 非法版本（0 / 'x'）→ 同样拒绝
    run_v0 = seed_extra_run(task_id, batch_id)
    doc0 = copy.deepcopy(make_plan(batch_id, 1))
    doc0['schemaVersion'] = 0
    write_checkpoint(run_v0, {'generate': doc0})
    check(runner.resume_plan_guard(UID, task_id, run_v0, 'auto')['code']
          == 'UNKNOWN_CHECKPOINT_SCHEMA',
          '场景4 schemaVersion=0（非法）→ UNKNOWN_CHECKPOINT_SCHEMA')
    run_vx = seed_extra_run(task_id, batch_id)
    docx = copy.deepcopy(make_plan(batch_id, 1))
    docx['schemaVersion'] = 'x'
    write_checkpoint(run_vx, {'generate': docx})
    check(runner.resume_plan_guard(UID, task_id, run_vx, 'auto')['code']
          == 'UNKNOWN_CHECKPOINT_SCHEMA',
          '场景4 schemaVersion=\'x\'（非整数）→ UNKNOWN_CHECKPOINT_SCHEMA')

    # 防御性：任务不匹配 / resumeMode 非法
    result = runner.resume_plan_guard(UID, 'other-task', run_schema2, 'auto')
    check(result.get('ok') is False and result.get('code') == 'RUN_NOT_FOUND',
          '场景4 运行不属于该任务 → RUN_NOT_FOUND（fail-closed）', result)
    result = runner.resume_plan_guard(UID, task_id, run_schema2, 'rebuild')
    check(result.get('ok') is False and result.get('code') == 'RESUME_MODE_INVALID',
          '场景4 resumeMode 非法 → RESUME_MODE_INVALID', result)


def scenario_resumable_jobs_split_tree():
    """场景5：split 树左成功右失败 → 只返回右子；blocked/superseded 不返回。"""
    new_isolated_root('s5')
    doc = apply_all(make_plan('b-split', 2), claim_start('j-root', 'a-root') + [
        {'type': batch_state.EVENT_ATTEMPT_SUCCEEDED, 'attemptId': 'a-root',
         'usage': API_USAGE, 'finishReason': 'length'},  # 父截断记账后再拆分
        {'type': batch_state.EVENT_JOB_SPLIT, 'jobId': 'j-root',
         'children': [{'jobId': 'j-left', 'orderedPrimaryTargetIds': ['t-0']},
                      {'jobId': 'j-right', 'orderedPrimaryTargetIds': ['t-1']}]},
    ])
    doc = apply_all(doc, leaf_success_events('j-left', 'a-left'))
    doc = apply_all(doc, leaf_failure_events('j-right', 'a-right'))
    check(runner.resumable_jobs({'generate': doc}) == ['j-right'],
          '场景5 左成功右失败的 split 树 → 只返回右子（成功叶不重做）',
          runner.resumable_jobs({'generate': doc}))
    check(runner.resumable_jobs(doc) == ['j-right'],
          '场景5 resumable_jobs 也接受 generate doc 本身')

    # blocked 叶不返回：预算受阻属终态，续跑须显式新计划
    doc_b = apply_all(make_plan('b-blocked', 1), claim_start('j-root', 'a-b') + [
        {'type': batch_state.EVENT_ATTEMPT_SUCCEEDED, 'attemptId': 'a-b', 'usage': API_USAGE},
        {'type': batch_state.EVENT_JOB_BLOCKED, 'jobId': 'j-root',
         'code': 'WALL_TIME_BUDGET_EXCEEDED', 'message': '活跃耗时达上限'},
    ])
    check(runner.resumable_jobs(doc_b) == [],
          '场景5 blocked 叶不返回（不能用重试无限追加额度）',
          runner.resumable_jobs(doc_b))

    # superseded 叶不返回；queued 叶返回
    doc_s = apply_all(make_plan('b-super', 1), [
        {'type': batch_state.EVENT_JOB_CREATED, 'jobId': 'j-old',
         'orderedPrimaryTargetIds': ['t-0']},
        {'type': batch_state.EVENT_JOB_SUPERSEDED, 'jobId': 'j-old'},
    ])
    check(runner.resumable_jobs(doc_s) == ['j-root'],
          '场景5 superseded 叶不返回、queued 叶返回（§4.3 旧作业不可再派发）',
          runner.resumable_jobs(doc_s))

    # 非 schema2 / 空入参 → []
    check(runner.resumable_jobs(None) == [], '场景5 None → []')
    check(runner.resumable_jobs({}) == [], '场景5 空 dict → []')
    check(runner.resumable_jobs({'generate': legacy_generate_doc('b-x')}) == [],
          '场景5 schema1 计划 → []（没有 v2 叶状态可列）')
    doc3 = copy.deepcopy(make_plan('b-x', 1))
    doc3['schemaVersion'] = 9
    check(runner.resumable_jobs({'generate': doc3}) == [],
          '场景5 未知 schemaVersion → []（不为未知版本出主意）')


def scenario_stale_lease_v2_rejected():
    """场景6：v2 计划轮换 lease 后旧 lease 晚结果一律被拒；新 lease 正常推进。"""
    new_isolated_root('s6')
    task_id, batch_id, run_id = seed_task_run()
    lease0 = str(read_run(run_id)['lease_token'])
    doc = make_plan(batch_id, 2)
    p_old = batch_persistence.OnlinePersistence(task_id, UID, run_id).with_lease(lease0)
    p_old.save_plan(doc)
    lease1 = rotate_lease(run_id)
    check(bool(lease1) and lease1 != lease0, '场景6 执行权已轮换（模拟重试接管/submit）',
          (lease0, lease1))

    # 旧 lease：claim 必须抛（契约：claim 场景失败抛出，绝不静默返回）
    check_raises(lambda: p_old.claim_job(None, 'j-root', 1),
                 batch_persistence.LeaseLostError,
                 '场景6 旧 lease 的 claim_job 抛 LeaseLostError')
    # 旧 lease：commit 一律拒绝返回 False（晚结果裁决，不抛不写）
    check(p_old.commit_success(None, 'j-root', 'a-stale', [make_candidate('late')],
                               API_USAGE, 'digest') is False,
          '场景6 旧 lease 的 commit_success 返回 False')
    check(p_old.commit_failure(None, 'j-root', 'a-stale', 'NETWORK_RETRYABLE', '超时',
                               API_USAGE) is False,
          '场景6 旧 lease 的 commit_failure 返回 False')
    doc_after = read_generate_doc(run_id)
    check((doc_after['jobs']['j-root']['state']) == 'queued',
          '场景6 checkpoint 无部分状态（作业仍 queued）',
          doc_after['jobs']['j-root'])
    check(doc_after['attempts'] == {} and doc_after['usageAggregate']['calls'] == 0,
          '场景6 旧 lease 晚写入不产生 attempt/用量',
          doc_after['attempts'])

    # 直接 update_run 的 lease 条件写同样被拒（晚结果拒绝的原语）
    result = {}

    def stale_body(conn):
        result['hit'] = store.update_run(conn, run_id, UID, stage='STALE', lease=lease0)

    with sto.write_tx() as tx:
        tx.run(stale_body)
    check(result.get('hit') is False, '场景6 update_run(lease=旧值) 未命中（False）')
    check(read_run(run_id)['stage'] == '', '场景6 stage 未被旧 lease 改写',
          read_run(run_id)['stage'])

    # 新 lease 持有者正常推进：claim → commit_success
    p_new = batch_persistence.OnlinePersistence(task_id, UID, run_id).with_lease(lease1)
    claim = p_new.claim_job(None, 'j-root', 1)
    check(claim.get('ok') is True and bool(claim.get('attemptId')),
          '场景6 新 lease 的 claim_job 成功（先持久化再返回）', claim)
    check(p_new.commit_success(None, 'j-root', claim['attemptId'], [], API_USAGE,
                               'digest-new') is True,
          '场景6 新 lease 的 commit_success 成功')
    doc_final = read_generate_doc(run_id)
    check(doc_final['jobs']['j-root']['state'] == 'succeeded',
          '场景6 最终由新执行权写成 succeeded', doc_final['jobs']['j-root']['state'])


def scenario_cancel_no_revival_v2():
    """场景7：cancel 后晚到的 commit/内容写/收尾都不复活 cancelled。"""
    new_isolated_root('s7')
    task_id, batch_id, run_id = seed_task_run()
    lease0 = str(read_run(run_id)['lease_token'])
    doc = make_plan(batch_id, 2)
    p = batch_persistence.OnlinePersistence(task_id, UID, run_id).with_lease(lease0)
    p.save_plan(doc)
    claim = p.claim_job(None, 'j-root', 1)
    runner.stage(UID, run_id, 'abstract', '生成中')
    runner.request_cancel(UID, run_id)
    row = read_run(run_id)
    check(row['state'] == 'cancelled' and int(row['cancel_requested'] or 0) == 1,
          '场景7 取消已提交（cancelled + 标记）', row)
    check(read_task(task_id)['status'] == 'scope',
          '场景7 任务由 generating 回退 scope（08 §12.3）', read_task(task_id)['status'])

    # 取消不轮换 lease：晚到 commit 的 lease 仍匹配 DB，但取消核对在同一事务先行 → False
    check(p.commit_success(None, 'j-root', claim['attemptId'], [make_candidate('late')],
                           API_USAGE, 'digest') is False,
          '场景7 取消后晚到 commit_success 返回 False（取消不等待调用结束，但不复活）')
    doc_after = read_generate_doc(run_id)
    check(doc_after['jobs']['j-root']['state'] == 'running',
          '场景7 计划内作业状态未被晚到回调改写', doc_after['jobs']['j-root']['state'])
    check(read_candidates(task_id, batch_id) == [],
          '场景7 晚到候选未落库', read_candidates(task_id, batch_id))

    # 晚内容写：content_tx 在同一写事务核对取消 → Cancelled，任何内容都不写
    def late_body(conn):
        store.create_candidate(conn, task_id, UID, batch_id, make_candidate('late2'))

    check_raises(lambda: runner.content_tx(UID, run_id, late_body), runner.Cancelled,
                 '场景7 content_tx 晚内容写抛 Cancelled')
    check(read_candidates(task_id, batch_id) == [], '场景7 晚内容写零落库')

    # 晚收尾：既有 finish_success 与 guarded（两路）都不改写 cancelled
    runner.finish_success(UID, run_id)
    check(read_run(run_id)['state'] == 'cancelled',
          '场景7 晚到 finish_success 不把 cancelled 覆盖成 succeeded')
    result = runner.finish_success_guarded(UID, run_id,
                                           plan_doc={'generate': doc_after})
    check(result[0] is False and
          any(v.get('code') == 'ACTIVE_JOB_REMAINS' for v in result[1]),
          '场景7 guarded（schema2 路径）先被终态检查拒绝（仍有 running 叶）', result)
    result = runner.finish_success_guarded(UID, run_id)
    check(result == (True, []), '场景7 guarded（plan_doc=None）退化既有语义', result)
    row = read_run(run_id)
    check(row['state'] == 'cancelled' and int(row['cancel_requested'] or 0) == 1,
          '场景7 最终保持 cancelled + 标记（晚收尾零复活）', row)
    check(read_task(task_id)['status'] == 'scope',
          '场景7 任务保持 scope（未被晚收尾推进 review）', read_task(task_id)['status'])
    check(read_candidates(task_id, batch_id) == [], '场景7 全程无晚到候选')


def scenario_legacy_auto_keeps_plan():
    """场景8：auto 不改旧 plan——schema1 上 guard 只读，绝不升级伪装 v2。"""
    new_isolated_root('s8')
    task_id, batch_id, run_id = seed_task_run()
    payload = {'generate': legacy_generate_doc(batch_id)}
    write_checkpoint(run_id, payload)
    before = read_raw_checkpoint(run_id)
    result = runner.resume_plan_guard(UID, task_id, run_id, 'auto')
    check(result == {'ok': True, 'mode': 'legacy_auto'},
          '场景8 schema1 + auto → legacy_auto（保持原冻结批次路径）', result)
    after = read_raw_checkpoint(run_id)
    check(json.dumps(after, sort_keys=True, ensure_ascii=False)
          == json.dumps(before, sort_keys=True, ensure_ascii=False),
          '场景8 auto 不改旧 plan：checkpoint 逐字节不变')
    check('schemaVersion' not in after['generate'],
          '场景8 未偷偷把旧计划升级为 v2（绝不伪装叶状态）')
    result = runner.resume_plan_guard(UID, task_id, run_id, 'abstract')
    check(result == {'ok': True, 'mode': 'legacy_abstract'},
          '场景8 schema1 + abstract → legacy_abstract（维持全部抽象重跑语义）', result)
    check(json.dumps(read_raw_checkpoint(run_id), sort_keys=True, ensure_ascii=False)
          == json.dumps(before, sort_keys=True, ensure_ascii=False),
          '场景8 abstract 查询同样零改写')


def scenario_abstract_new_epoch_caller_driven():
    """场景9：abstract 显式新 epoch——epoch 由调用方推进，guard 不拦合法新 epoch。"""
    new_isolated_root('s9')
    task_id, batch_id, run_id = seed_task_run()
    doc = make_plan(batch_id, 1)
    write_checkpoint(run_id, {'generate': doc})
    result = runner.resume_plan_guard(UID, task_id, run_id, 'abstract')
    check(result == {'ok': True, 'mode': 'v2_abstract'},
          '场景9 schema2 + abstract → v2_abstract（调用方负责候选清理+计划替换同事务）',
          result)
    check((read_generate_doc(run_id) or {}).get('planEpoch') == 1,
          '场景9 guard 只读：planEpoch 未被守卫推进')
    check(runner.resume_plan_guard(UID, task_id, run_id, 'auto')
          == {'ok': True, 'mode': 'v2_auto'}, '场景9 同一计划 auto → v2_auto')

    # 指纹辅助：auto 恢复的一致性判断数据（期望指纹由 D16 组装传入）
    check(runner.plan_fingerprint_matches({'generate': doc}, doc['fingerprint']) is True,
          '场景9 指纹一致 → True（checkpoint 形态）')
    check(runner.plan_fingerprint_matches(doc, doc['fingerprint']) is True,
          '场景9 指纹辅助也接受 generate doc 形态')
    check(runner.plan_fingerprint_matches({'generate': doc}, 'another-fingerprint') is False,
          '场景9 指纹不一致 → False（BUDGET_PLAN_MISMATCH 依据）')
    no_fp = copy.deepcopy(doc)
    del no_fp['fingerprint']
    check(runner.plan_fingerprint_matches({'generate': no_fp}, doc['fingerprint']) is False,
          '场景9 存储指纹缺失 → False（绝不放行空指纹）')

    # 调用方手动推进 epoch（真实实现为候选清理与计划替换同事务；此处证明 guard 不拦）
    doc2 = copy.deepcopy(read_generate_doc(run_id))
    doc2['planEpoch'] = 2
    doc2.setdefault('log', []).append('abstract 重跑：新 epoch 2')
    write_checkpoint(run_id, {'generate': doc2})
    result = runner.resume_plan_guard(UID, task_id, run_id, 'abstract')
    check(result == {'ok': True, 'mode': 'v2_abstract'},
          '场景9 epoch+1 后 guard 再次通过（epoch 语义由调用方驱动）', result)
    check((read_generate_doc(run_id) or {}).get('planEpoch') == 2,
          '场景9 新 epoch 计划原样保留', (read_generate_doc(run_id) or {}).get('planEpoch'))


def scenario_claim_for_resume_takeover():
    """场景10：claim_for_resume 显式接管——进入即轮换、旧 lease 晚写被拒、退出作废。"""
    new_isolated_root('s10')
    task_id, batch_id, run_id = seed_task_run()
    lease0 = str(read_run(run_id)['lease_token'])
    runner._leases[(UID, run_id)] = lease0  # 白盒兼容表：模拟旧 worker 仍持 lease0
    try:
        with runner.claim_for_resume(UID, run_id) as lease1:
            check(bool(lease1) and lease1 != lease0,
                  '场景10 claim_for_resume 进入即轮换出新 lease（复用 submit 轮换语义）',
                  (lease0, lease1))
            check(runner.lease_of(UID, run_id) == lease1,
                  '场景10 当前线程取得新执行权（lease_of 解析到本次这一份）',
                  runner.lease_of(UID, run_id))
            check(str(read_run(run_id)['lease_token']) == lease1,
                  '场景10 DB lease 已轮换为本次这一份')
            runner.stage(UID, run_id, 'abstract', '接管后推进')
            check(read_run(run_id)['stage'] == 'abstract',
                  '场景10 新执行权下的条件写命中')
            result = {}

            def stale_body(conn):
                result['hit'] = store.update_run(conn, run_id, UID, stage='STALE',
                                                 lease=lease0)

            with sto.write_tx() as tx:
                tx.run(stale_body)
            check(result.get('hit') is False, '场景10 旧 lease 的条件写被拒（晚结果不写入）')
            check(read_run(run_id)['stage'] == 'abstract',
                  '场景10 阶段未被旧 lease 改写')
        check(str(getattr(runner._worker_local, 'token', '') or '') == '',
              '场景10 退出作用域：线程本地 worker token 已清空',
              str(getattr(runner._worker_local, 'token', '')))
        check(runner.lease_of(UID, run_id) != lease1,
              '场景10 退出作用域：不再解析到本次执行权（兼容表仍持旧值属模拟旧 worker）',
              runner.lease_of(UID, run_id))
        check(str(read_run(run_id)['lease_token']) not in ('', lease1),
              '场景10 退出时执行权被作废轮换（不留活执行权）')
        with runner.claim_for_resume(UID, 'no-such-run') as missing:
            check(missing == '', '场景10 运行不存在：lease 为空（不启用条件写、不崩溃）',
                  missing)
    finally:
        runner._leases.pop((UID, run_id), None)
    check(runner.lease_of(UID, run_id) == '',
          '场景10 兼容表清除后：非 worker 线程无执行权（lease_of 为空）',
          runner.lease_of(UID, run_id))


SCENARIOS = (
    ('final_state_rejects_failed_leaf', scenario_final_state_rejects_failed_leaf),
    ('final_state_pass_finishes', scenario_final_state_pass_finishes),
    ('plan_doc_none_legacy', scenario_plan_doc_none_legacy),
    ('resume_guard_versions', scenario_resume_guard_versions),
    ('resumable_jobs_split_tree', scenario_resumable_jobs_split_tree),
    ('stale_lease_v2_rejected', scenario_stale_lease_v2_rejected),
    ('cancel_no_revival_v2', scenario_cancel_no_revival_v2),
    ('legacy_auto_keeps_plan', scenario_legacy_auto_keeps_plan),
    ('abstract_new_epoch_caller_driven', scenario_abstract_new_epoch_caller_driven),
    ('claim_for_resume_takeover', scenario_claim_for_resume_takeover),
)


def main():
    for name, func in SCENARIOS:
        print('--- %s ---' % name)
        try:
            func()
        except Exception as exc:  # 场景自身异常不吞：计失败并打印堆栈
            traceback.print_exc()
            check(False, '场景 %s 自身异常：%s' % (name, type(exc).__name__), str(exc))
        print('')
    leftover = [t for t in threading.enumerate() if t.name.startswith('build-run-')]
    for thread in leftover:
        thread.join(10)
    if runner._workers:
        check(False, '收尾：worker 登记无残留', sorted(runner._workers))
    else:
        check(True, '收尾：worker 登记无残留')
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
