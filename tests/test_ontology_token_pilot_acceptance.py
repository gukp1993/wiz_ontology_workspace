# -*- coding: utf-8 -*-
"""D17 双适配器参数化恢复契约（整合计划 v3 §11；契约来源 batch_contracts.py「持久化接口」节）。

同一组恢复契约用例对两个 Persistence 实现都跑一遍，**两个适配器都通过才算通过**：
  * online      —— workbench/ontology_build/batch_persistence.py（D08 OnlinePersistence，
                   真实隔离存储根：临时 WIZ_WORKBENCH_ROOT/WIZ_DATABASE_URL + 任务/批次/run 种子，
                   种子写法与 tests/test_ontology_build_budget_storage.py 同一模式）；
  * experiment  —— experiments/ontology_token_pilot/state.py（D12 ExperimentState，
                   tempfile 独立 SQLite，不连业务 ROOT）。

契约清单（每个契约 online/experiment 各跑一遍）：
 1. save/load 往返一致 + 二次保存（异指纹计划）被拒且旧状态不变。
 2. claim 后崩溃（不 commit）→ mark_interrupted_unknown → usage 聚合
    unknownUsageCalls=1（真实计费未知不计 0）；重复收口幂等。
 3. commit_success 后重复回调幂等：候选不重复、doc/usage 聚合不被改写
    （返回值冻结口径：True=新提交 / False=幂等跳过，两适配器一致）。
 4. commit_split：父 split + 子 job 同事务落库、父截断 usage 入总账、覆盖计数不漂移
    （父 split 不算成功）、子 job 可再 claim。
 5. checkpoint 容量：超软阈值写入抛 CheckpointCapacityError（各适配器等价异常）
    且旧状态不变；另验各侧超限路径（在线=种入超限 checkpoint 后 claim 拒绝；
    实验=注入 MAX_CHECKPOINT_BYTES 后 commit 整体回滚）。
 6. iterate_candidates 分页：501 条无重无漏（在线按 candidate_id 集合对账、
    实验按写入序 key 有序对账）。

隔离（AGENTS.md 测试铁律）：在线侧每个契约独立临时根（用完销毁），不访问网络、
不连真实库；实验侧 tempfile 独立库；全部合成数据。不 import 业务 llm/pipeline。

运行：python3 tests/run.py --test tests/test_ontology_token_pilot_acceptance.py
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
from experiments.ontology_token_pilot import state as pilot_state  # noqa: E402

PASSED = []
FAILED = []
ROOTS = []
OWNER = 'pilot-acceptance-online'

# --- 共享合成数据（两适配器同构输入）-----------------------------------------------------

PROFILE = {
    'enabled': True, 'providerId': 'prov-1', 'model': 'model-x',
    'contextTokens': 100000, 'outputLimitTokens': 32000, 'requestOutputTokens': 32000,
    'targetRatio': 0.5, 'limitsSource': 'configured', 'errors': [],
    'effective': {'outputCap': 32000, 'targetBudget': 16000, 'inputReserve': 2048},
}
FINGERPRINT_PARTS = ['facts-digest-d17', '1', '1', 'prov-1', 'model-x', 'v3', 'legacy-v1',
                     'acceptance-profile']
OTHER_PARTS = FINGERPRINT_PARTS + ['conflicting-plan']
USAGE_OK = {'promptTokens': 10, 'completionTokens': 20, 'reasoningTokens': 0,
            'totalTokens': 30, 'usageSource': 'api'}
USAGE_TRUNCATED = {'promptTokens': 5, 'completionTokens': 77, 'totalTokens': 82,
                   'usageSource': 'api'}


def _short(value, limit=300):
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = repr(value)
    return text if len(text) <= limit else text[:limit] + '…'


def check(cond, message, actual=None):
    if cond:
        PASSED.append(message)
        return True
    FAILED.append(message)
    print('  [失败] %s  实际: %s' % (message, _short(actual)))
    return False


def require(cond, detail):
    """契约断言：失败抛 AssertionError，由契约运行器记为该侧 FAIL。"""
    if not cond:
        raise AssertionError(detail)


def make_targets(count):
    return [{'targetId': 't-%d' % n, 'factId': 'f-%d' % n, 'selector': {'type': 'whole'},
             'kind': 'single_fact', 'subjectKey': 'subject-%d' % n, 'materialId': 'm-1'}
            for n in range(1, count + 1)]


def make_plan(target_count=2, parts=None):
    """schema2 计划：targets + 单个根作业认领全部目标（两适配器同构输入）。"""
    doc = batch_state.create_plan('acceptance-batch', 1, 1, make_targets(target_count),
                                  PROFILE, contracts.CODEC_LEGACY, parts or FINGERPRINT_PARTS)
    return batch_state.apply_event(doc, {
        'type': batch_state.EVENT_JOB_CREATED, 'jobId': 'j-root',
        'orderedPrimaryTargetIds': ['t-%d' % n for n in range(1, target_count + 1)],
        'contextFactIds': [], 'estimate': {'slots': target_count}})


def make_candidate(n):
    return {'key': 'k-%03d' % n, 'type': 'property', 'name': '属性%03d' % n,
            'definition': 'D17 契约候选%03d' % n, 'fields': {'dataType': 'number'},
            'ownerKey': 'cell', 'evidence': {'name': ['f-%d' % n]},
            'evidenceStatus': 'supported', 'conflicts': [], 'rejectedRefs': 0}


SPLIT_CHILDREN = [
    {'jobId': 'j-left', 'orderedPrimaryTargetIds': ['t-1', 't-2'],
     'contextFactIds': [], 'estimate': {'slots': 2}},
    {'jobId': 'j-right', 'orderedPrimaryTargetIds': ['t-3', 't-4'],
     'contextFactIds': [], 'estimate': {'slots': 2}},
]


# --- 适配器 A：在线 OnlinePersistence（真实隔离存储根）-----------------------------------

def new_isolated_root(tag):
    root = Path(tempfile.mkdtemp(prefix='wiz_pilot_accept_%s_' % tag))
    ROOTS.append(root)
    os.environ['WIZ_WORKBENCH_ROOT'] = str(root)
    os.environ['WIZ_DATABASE_URL'] = 'sqlite:///' + str(root / 'data' / 'workbench.sqlite3')
    sto.reset_engine()
    storage.mark_unready()
    storage.ensure_ready()
    if str(root) not in sto.resolve_url():
        raise AssertionError('隔离根未生效：%s' % sto.resolve_url())
    return root


def seed_task_run():
    """建任务 + 批次 + generate 运行行（OnlinePersistence 需要 run 行），返回 (task, batch, run, lease)。"""

    def body(conn):
        task_id = store.create_task(conn, OWNER, 'D17 双适配器验收')
        batch_id = store.create_batch(conn, task_id, OWNER, '', {})
        run_id, lease = store.create_run(conn, task_id, OWNER, 'generate', {}, batch_id)
        return task_id, batch_id, run_id, lease

    with sto.write_tx() as tx:
        return tx.run(body)


class OnlineAdapter(object):
    name = 'online'
    capacity_exc = batch_persistence.CheckpointCapacityError
    conflict_save_exc = batch_persistence.PersistenceError
    repeat_commit_returns = False  # 冻结口径：True=新提交 / False=幂等跳过（两适配器统一）

    def __init__(self, tag):
        self.root = new_isolated_root(tag)
        self.task_id, self.batch_id, self.run_id, self.lease = seed_task_run()
        self.persistence = batch_persistence.OnlinePersistence(self.task_id, OWNER, self.run_id)

    # 契约操作
    def save(self, doc):
        self.persistence.save_plan(doc, self.lease)

    def load(self):
        return self.persistence.load()

    def claim(self, job_id='j-root', run_attempt=1):
        result = self.persistence.claim_job(self.lease, job_id, run_attempt)
        return str(result['attemptId'])

    def commit_success(self, job_id, attempt_id, candidates, usage, digest):
        return self.persistence.commit_success(self.lease, job_id, attempt_id, candidates,
                                               usage, digest)

    def commit_split(self, job_id, children, usage):
        return self.persistence.commit_split(self.lease, job_id, children, usage,
                                             finish_reason='length')

    def mark_unknown(self, attempt_ids):
        return self.persistence.mark_interrupted_unknown(attempt_ids)

    def iterate_pages(self, page_size=200):
        return [page for page in self.persistence.iterate_candidates(page_size=page_size)]

    # 观测
    def candidates(self):
        with sto.read_connection() as conn:
            return store.all_candidates(conn, self.task_id, OWNER, include_merged=True)

    def raw_generate_doc(self):
        with sto.read_connection() as conn:
            row = store.get_run(conn, self.run_id, OWNER)
        if row is None or not row['checkpoint_json']:
            return None
        checkpoint = json.loads(row['checkpoint_json'])
        generate = checkpoint.get('generate') if isinstance(checkpoint, dict) else None
        return generate if isinstance(generate, dict) else None

    def seed_checkpoint_direct(self, doc):
        """测试专用：绕过 persistence 直接种 checkpoint（构造超限/病态前置状态）。"""

        def body(conn):
            store.update_run(conn, self.run_id, OWNER, checkpoint={'generate': doc})

        with sto.write_tx() as tx:
            tx.run(body)

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)
        if self.root in ROOTS:
            ROOTS.remove(self.root)


# --- 适配器 B：实验 ExperimentState（tempfile 独立 SQLite）-------------------------------

class ExperimentAdapter(object):
    name = 'experiment'
    capacity_exc = pilot_state.CheckpointCapacityError
    conflict_save_exc = ValueError
    repeat_commit_returns = False  # 冻结口径：重复 commit_success 返回 False 跳过（D12 实现）

    TASK = 'task-acceptance'

    def __init__(self, tag):
        self.tmp = tempfile.mkdtemp(prefix='wiz_pilot_accept_exp_%s_' % tag)
        self.state = pilot_state.open_state(str(Path(self.tmp) / 'pilot.sqlite3'))

    def save(self, doc):
        self.state.save_plan(self.TASK, doc)

    def load(self):
        return self.state.load(self.TASK)

    def claim(self, job_id='j-root', run_attempt=1):
        result = self.state.claim_job(self.TASK, job_id, run_attempt)
        return str(result['attemptId'])

    def commit_success(self, job_id, attempt_id, candidates, usage, digest):
        return self.state.commit_success(self.TASK, job_id, attempt_id, candidates, usage, digest)

    def commit_split(self, job_id, children, usage):
        return self.state.commit_split(self.TASK, job_id, children, usage)

    def mark_unknown(self, attempt_ids):
        return self.state.mark_interrupted_unknown(self.TASK, attempt_ids)

    def iterate_pages(self, page_size=200):
        return [page for page in self.state.iterate_candidates(self.TASK, page_size=page_size)]

    def candidates(self):
        return [item for page in self.iterate_pages() for item in page]

    def raw_generate_doc(self):
        return self.load()

    def cleanup(self):
        self.state.close()
        shutil.rmtree(self.tmp, ignore_errors=True)


# --- 共享契约断言（对 adapter 实例运行；失败抛 AssertionError）----------------------------


def contract_1_roundtrip_and_second_save_rejected(a):
    doc = make_plan(3)
    a.save(doc)
    loaded = a.load()
    require(int((loaded or {}).get('schemaVersion') or 0) == 2, 'load 应返回 schema2 计划')
    require(json.dumps(loaded, sort_keys=True, ensure_ascii=False)
            == json.dumps(doc, sort_keys=True, ensure_ascii=False),
            'save→load 往返应逐字段一致（targets/jobs/coverage/fingerprint 全保留）')
    conflicting = make_plan(1, parts=OTHER_PARTS)
    require(conflicting['fingerprint'] != doc['fingerprint'], '前置：冲突计划指纹必须不同')
    raised = None
    try:
        a.save(conflicting)
    except a.conflict_save_exc as exc:
        raised = exc
    except Exception as exc:  # noqa: BLE001 - 异常类型不对也算失败
        raise AssertionError('二次保存应抛 %s，实际 %s: %s'
                             % (a.conflict_save_exc.__name__, type(exc).__name__, exc)) from None
    require(raised is not None, '二次保存（异指纹计划）应被拒绝，实际未抛异常')
    require(a.load() == doc, '被拒二次保存后原计划应保持不变')


def contract_2_crash_mark_unknown(a):
    a.save(make_plan(2))
    attempt_id = a.claim()
    doc = a.load()
    require((doc['attempts'].get(attempt_id) or {}).get('state') == contracts.ATTEMPT_STARTED,
            'claim 后 attempt 应 started（崩溃前状态）')
    require((doc['jobs']['j-root']).get('state') == contracts.JOB_RUNNING,
            'claim 后 job 应 running')
    marked = a.mark_unknown([attempt_id])
    require(marked == 1, 'mark_interrupted_unknown 应收口 1 条，实际 %r' % (marked,))
    doc = a.load()
    attempt = doc['attempts'].get(attempt_id) or {}
    require(attempt.get('state') == contracts.ATTEMPT_INTERRUPTED_UNKNOWN,
            'attempt 应为 interrupted_unknown，实际 %r' % (attempt.get('state'),))
    aggregate = doc.get('usageAggregate') or {}
    require(int(aggregate.get('unknownUsageCalls') or 0) == 1,
            'usage 聚合 unknownUsageCalls 应为 1（真实计费未知不计 0）')
    require(aggregate.get('completionTokens') is None,
            'unknown 时 token 总量不得冒充已知（completionTokens 应为 None）')
    require(a.mark_unknown([attempt_id]) == 0, '重复收口应幂等返回 0')


def contract_3_commit_success_idempotent(a):
    a.save(make_plan(2))
    attempt_id = a.claim()
    first = a.commit_success('j-root', attempt_id, [make_candidate(1), make_candidate(2)],
                             USAGE_OK, 'digest-c3')
    require(first is True, '首次 commit_success 应返回 True')
    require(len(a.candidates()) == 2, '首次提交应落 2 条候选')
    repeat = a.commit_success('j-root', attempt_id, [make_candidate(1), make_candidate(2)],
                              USAGE_OK, 'digest-c3')
    require(repeat is a.repeat_commit_returns,
            '重复回调返回值应按适配器冻结口径（%s 期望 %r，实际 %r）'
            % (a.name, a.repeat_commit_returns, repeat))
    stored = a.candidates()
    require(len(stored) == 2, '重复回调不得重复插候选（应仍 2 条，实际 %d）' % len(stored))
    doc = a.load()
    aggregate = doc.get('usageAggregate') or {}
    require(int(aggregate.get('calls') or 0) == 1
            and int(aggregate.get('completionTokens') or 0) == 20,
            '重复回调后 usage 聚合不得漂移（calls=1, completion=20）')
    require((doc['jobs']['j-root']).get('state') == contracts.JOB_SUCCEEDED
            and int(doc['jobs']['j-root'].get('candidateCount') or 0) == 2,
            'job 保持 succeeded 且 candidateCount 不变')
    # 成功叶的晚到失败回调一律拒绝改写终态
    rejected = None
    try:
        rejected = a.commit_success('j-root', attempt_id, [make_candidate(9)],
                                    USAGE_OK, 'digest-late')
    except a.conflict_save_exc:
        rejected = 'rejected-by-exception'
    except Exception as exc:  # noqa: BLE001
        raise AssertionError('成功叶晚到回调不应抛未预期异常：%s: %s' % (type(exc).__name__, exc)) from None
    require(rejected is not True,
            '成功叶晚到结果不得再次提交（online=False/异常、experiment=False，实际 %r）' % (rejected,))
    require(len(a.candidates()) == 2, '晚到回调不得新增候选')


def contract_4_commit_split_children_and_coverage(a):
    a.save(make_plan(4))
    attempt_id = a.claim()
    ok = a.commit_split('j-root', SPLIT_CHILDREN, USAGE_TRUNCATED)
    require(ok is True, 'commit_split 应返回 True')
    doc = a.load()
    parent = doc['jobs']['j-root']
    require(parent.get('state') == contracts.JOB_SPLIT
            and sorted(parent.get('children') or []) == ['j-left', 'j-right'],
            '父作业应 split 且子作业同事务入计划')
    attempt = doc['attempts'].get(attempt_id) or {}
    require(attempt.get('state') == contracts.ATTEMPT_SUCCEEDED
            and attempt.get('finishReason') == 'length',
            '父截断在途尝试应按 length 收口记账')
    aggregate = doc.get('usageAggregate') or {}
    require(int(aggregate.get('completionTokens') or 0) == 77
            and int(aggregate.get('calls') or 0) == 1,
            '父截断 usage 应入总账（completion=77, calls=1），覆盖计数不漂移')
    require(doc.get('coverage') == {'targetTotal': 4, 'targetCompleted': 0, 'targetPending': 4},
            '父 split 不算成功：coverage.targetCompleted 应保持 0，实际 %r' % (doc.get('coverage'),))
    require(doc.get('pendingTargetIds') == [], '子作业认领全部目标后 pending 列表应为空')
    require(batch_state.validate_plan_doc(doc) == [], 'split 后计划结构应零错误')
    child_attempt = a.claim('j-left', run_attempt=1)
    doc = a.load()
    require(doc['jobs']['j-left'].get('state') == contracts.JOB_RUNNING,
            '子 job 应可再 claim（queued→running）')
    require((doc['attempts'].get(child_attempt) or {}).get('jobId') == 'j-left',
            '子 job 新 attempt 应挂到子作业')


def contract_5_checkpoint_capacity(a):
    huge = make_plan(1)
    huge['log'] = ['x' * 1024] * 1100   # 约 1.13MiB 合法填充（log 不参与结构校验）
    raised = None
    try:
        a.save(huge)
    except a.capacity_exc:
        raised = True
    except Exception as exc:  # noqa: BLE001
        raise AssertionError('超限保存应抛 %s，实际 %s: %s'
                             % (a.capacity_exc.__name__, type(exc).__name__, exc)) from None
    require(raised is True, '超限计划 save 应抛 CheckpointCapacityError（等价异常）')
    require(a.load() is None, '超限保存失败后旧状态不变（无计划）')
    capacity_after_write_failure(a)


def capacity_after_write_failure(a):
    """写入中途超限同样拒绝且状态不变：在线=种入超限 checkpoint 后 claim 抛；
    实验=注入 MAX_CHECKPOINT_BYTES 后 commit 整体回滚。"""
    small = make_plan(2)
    a.save(small)
    if a.name == 'online':
        bloated = make_plan(2)
        bloated['log'] = ['y' * 1024] * 1100
        a.seed_checkpoint_direct(bloated)
        raised = None
        try:
            a.claim()
        except a.capacity_exc:
            raised = True
        except Exception as exc:  # noqa: BLE001
            raise AssertionError('超限 checkpoint 下 claim 应抛容量异常，实际 %s: %s'
                                 % (type(exc).__name__, exc)) from None
        require(raised is True, '在线：种入超限 checkpoint 后 claim 应抛 CheckpointCapacityError')
        doc = a.raw_generate_doc()
        require((doc.get('jobs', {}).get('j-root') or {}).get('state') == contracts.JOB_QUEUED,
                'claim 被拒后 job 应仍 queued')
        require(doc.get('attempts') == {}, 'claim 被拒不得残留尝试')
    else:
        attempt_id = a.claim()
        before = a.load()
        original = contracts.MAX_CHECKPOINT_BYTES
        contracts.MAX_CHECKPOINT_BYTES = 512
        try:
            raised = None
            try:
                a.commit_success('j-root', attempt_id, [make_candidate(1)], USAGE_OK, 'd')
            except a.capacity_exc:
                raised = True
        finally:
            contracts.MAX_CHECKPOINT_BYTES = original
        require(raised is True, '实验：commit 路径超限应抛 CheckpointCapacityError')
        require(a.load() == before, 'commit 超限应整体回滚（job running / attempt started 不变）')


def contract_6_iterate_candidates_501(a):
    if a.name == 'online':
        def body(conn):
            for i in range(501):
                store.create_candidate(conn, a.task_id, OWNER, a.batch_id,
                                       {'type': 'object', 'key': 'k-%03d' % i,
                                        'name': '候选%03d' % i, 'definition': 'D17 分页契约'})
        with sto.write_tx() as tx:
            tx.run(body)
        with sto.read_connection() as conn:
            expected_ids = {row[0] for row in conn.execute(
                sto.text('SELECT candidate_id FROM wb_build_candidates '
                         'WHERE task_id = :t AND owner_user_id = :o'),
                {'t': a.task_id, 'o': OWNER}).all()}
        pages = a.iterate_pages(page_size=200)
        items = [item for page in pages for item in page]
        ids = [str(item['id']) for item in items]
        require([len(page) for page in pages] == [200, 200, 101],
                '页大小应为 200/200/101（keyset 收敛），实际 %r' % ([len(p) for p in pages],))
        require(len(items) == 501 and len(set(ids)) == 501,
                '应恰读 501 条且无重复，实际 %d 条 / %d 个唯一 id' % (len(items), len(set(ids))))
        require(set(ids) == expected_ids, '迭代集合应与库内直插集合完全一致（无漏）')
    else:
        a.save(make_plan(1))
        attempt_id = a.claim()
        committed = a.commit_success('j-root', attempt_id, [make_candidate(n) for n in range(501)],
                                     USAGE_OK, 'digest-501')
        require(committed is True, '前置：501 候选提交应成功')
        pages = a.iterate_pages(page_size=200)
        items = [item for page in pages for item in page]
        keys = [item.get('key') for item in items]
        require([len(page) for page in pages] == [200, 200, 101],
                '页大小应为 200/200/101（写入序收敛），实际 %r' % ([len(p) for p in pages],))
        require(keys == ['k-%03d' % n for n in range(501)],
                '写入序稳定、无重无漏（k-000..k-500 严格有序）')
        ids = [item.get('candidateId') for item in items]
        require(len(set(ids)) == 501, 'candidateId 无重复')


# --- 契约运行器 -------------------------------------------------------------------------

CONTRACTS = (
    (1, 'save/load 往返 + 二次保存被拒',
     lambda a: contract_1_roundtrip_and_second_save_rejected(a)),
    (2, 'claim 崩溃 → mark_interrupted_unknown → unknown 计次不计 0',
     lambda a: contract_2_crash_mark_unknown(a)),
    (3, 'commit_success 重复回调幂等（候选不重复）',
     lambda a: contract_3_commit_success_idempotent(a)),
    (4, 'commit_split 子 job 可 claim / 父 split / 覆盖计数不漂移',
     lambda a: contract_4_commit_split_children_and_coverage(a)),
    (5, 'checkpoint 容量超限拒绝且旧状态不变',
     lambda a: contract_5_checkpoint_capacity(a)),
    (6, 'iterate_candidates 分页 501 条无重无漏',
     lambda a: contract_6_iterate_candidates_501(a)),
)


def run_contract(index, title, contract_fn):
    results = {}
    for side, adapter_cls in (('online', OnlineAdapter), ('experiment', ExperimentAdapter)):
        adapter = None
        try:
            adapter = adapter_cls('c%d' % index)
            contract_fn(adapter)
            results[side] = 'PASS'
        except AssertionError as exc:
            results[side] = 'FAIL'
            print('  [%s 失败] 契约%d %s：%s' % (side, index, title, exc))
            FAILED.append('契约%d[%s] %s：%s' % (index, side, title, exc))
        except Exception as exc:  # noqa: BLE001 - 适配器侧未预期异常 = 该侧 FAIL
            results[side] = 'FAIL'
            print('  [%s 异常] 契约%d %s：%s: %s' % (side, index, title,
                                                    type(exc).__name__, exc))
            traceback.print_exc()
            FAILED.append('契约%d[%s] %s：未预期异常 %s: %s' % (index, side, title,
                                                              type(exc).__name__, exc))
        finally:
            if adapter is not None:
                adapter.cleanup()
    line = '契约%d %s：online=%s experiment=%s' % (index, title,
                                                   results.get('online'),
                                                   results.get('experiment'))
    print(line)
    if results.get('online') == 'PASS' and results.get('experiment') == 'PASS':
        PASSED.append(line)


def cleanup_roots():
    for root in list(ROOTS):
        shutil.rmtree(root, ignore_errors=True)
    del ROOTS[:]


def main():
    for index, title, contract_fn in CONTRACTS:
        run_contract(index, title, contract_fn)
    cleanup_roots()
    total = len(PASSED) + len(FAILED)
    print('========== 汇总 ==========')
    print('通过 %d / %d（每条契约需 online 与 experiment 双侧通过）' % (len(PASSED), total))
    for name in FAILED:
        print('  失败: ' + name)
    if total == 0:
        print('[错误] 未执行任何契约——不算通过')
        return 2
    return 0 if not FAILED else 1


if __name__ == '__main__':
    code = 1
    try:
        code = main()
    finally:
        cleanup_roots()
    sys.exit(code)
