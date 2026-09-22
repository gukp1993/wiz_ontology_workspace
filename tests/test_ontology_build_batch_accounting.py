"""批次记账正确性回归（2026-09-22 R4/R5 修复：拆批三态 + 非连续续跑独立落库）。

两个已复现缺陷的端到端与单元回归（合成数据 + 替身 llm.extract_candidates，不访问网络）：

R5（非连续失败批续跑静默丢结果）：
1. 非连续续跑空洞：上轮 done={2}、本轮 pending=[1,3] → 批 1/3 各自完成即独立落库
   （废弃「连续前缀」游标——旧实现批 1 完成后游标卡在 2，批 3 结果永远刷不出、
   调用方也不消费返回值 → 候选静默丢失）。
2. 完成序乱序：并发下批 2 先于批 1 完成 → 批 2 立即落库，不再等前缀推进。
3. 落库写事务失败：content_tx 抛异常 → 该批计入 failed_batches（错误注明「落库失败」）、
   不进 done、候选不落库，其余批次照常，运行以失败收场（绝不假成功）。
4. 收尾兜底：调度器漏报某批结果（既未落库也未计失败）→ 调用方显式计入失败并落检查点。

R4（拆批一半成功一半失败，父批被判成功）：
5. 拆批三态：全成功 / 全失败 / 部分失败（左成右败、右成左败、嵌套四分一败）——
   部分失败 ok=False（父批不进 done、整批可重试），成功半候选保留不浪费，
   failedFactIds 精确携带失败子批事实 id 随 error 上抛。
6. 部分失败整批重试幂等：首轮部分成功候选已落库，重试整批重跑后按批内
   alignedKey/原始 key 去重，不产生重复候选。
7. 429 限流单次调用不重试：撞限流立即返回交调度器降挡重排，单批只调用一次。

隔离（AGENTS.md 测试隔离铁律）：每个场景独立临时根（WIZ_WORKBENCH_ROOT +
该根下 WIZ_DATABASE_URL），只写临时目录；替换 llm.extract_candidates /
runner.content_tx / pipeline._run_batches_adaptive 模块属性控制响应（场景结束
全部恢复）；不访问网络、不连真实库、不依赖其它测试文件；结束销毁全部临时根。

运行：python3 tests/test_ontology_build_batch_accounting.py
"""
import os
import shutil
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path

os.environ['WIZ_BUILD_LLM_CONCURRENCY'] = '1'   # 默认串行抽取（import 前设置，时序才确定）

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from workbench import storage  # noqa: E402
from workbench.ontology_build import llm, pipeline, protocol, runner  # noqa: E402
from workbench.storage import engine as sto  # noqa: E402
from workbench.storage import ontology_build as store  # noqa: E402

protocol.LLM_CONCURRENCY = 1   # 双保险：调度器在调用点读模块属性

UID = 'batch-accounting-owner'
WAIT_SECONDS = 15
JOIN_SECONDS = 60
PROVIDER = {'endpoint': 'mock', 'model': 'mock'}
USAGE = {'calls': 1, 'promptBytes': 16, 'completionBytes': 8, 'durationMs': 1}
FAIL_402 = '模型配额不足（HTTP 402）'          # 非瞬态：批内不重试，确定性失败
TRUNCATED = 'LLM 输出被截断（超出 max_tokens），请简化代码或计算规则后重试'

PASSED = []
FAILED = []
SEQ = [0]
ROOTS = []


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


# --- 每场景独立隔离根 ---------------------------------------------------------------

def new_isolated_root(tag):
    """新临时根 + 该根下的 SQLite；重置引擎后惰性初始化（绝不碰真实 ontology/）。"""
    root = Path(tempfile.mkdtemp(prefix='wiz_batch_accounting_%s_' % tag))
    ROOTS.append(root)
    os.environ['WIZ_WORKBENCH_ROOT'] = str(root)
    os.environ['WIZ_DATABASE_URL'] = 'sqlite:///' + str(root / 'data' / 'workbench.sqlite3')
    sto.reset_engine()
    storage.ensure_ready()
    if str(root) not in sto.resolve_url():
        raise AssertionError('隔离根未生效：%s' % sto.resolve_url())
    return root


# --- 种子与读取 ----------------------------------------------------------------------

def seed_task_run(fact_count):
    """建任务 + 批次 + 范围 + fact_count 条事实 + generate run（真实存储层短写事务）。

    事实 snippet 各不相同（避免按片段哈希去重），且都含范围 include 词「设备」
    （select_scope 全部判 relevant；批次划分 = 每 protocol.LLM_BATCH_FACTS 条一批）。
    事实 id 为 f-1..f-N：LLM_BATCH_FACTS=20 时左半批含 f-1、右半批含 f-11，
    替身按批内事实 id 定响应（与调用顺序/重试次数无关，并发下确定）。
    """

    def body(conn):
        task_id = store.create_task(conn, UID, '批次记账回归')
        batch_id = store.create_batch(conn, task_id, UID, '', {})
        store.put_scope(conn, task_id, UID,
                        {'goal': '设备管理', 'include': '设备', 'exclude': '', 'relations': '',
                         'coverage': '', 'openQuestions': []}, confirmed=True)
        facts = [{'id': 'f-%d' % n, 'module': 'device', 'locator': {'kind': 'ddl'},
                  'snippet': '设备%d台账 device_table_%d 字段与单位说明' % (n, n),
                  'kind': 'table', 'data': {'table': 'device'}, 'quality': 'high'}
                 for n in range(1, fact_count + 1)]
        store.replace_material_facts(conn, task_id, UID, 'm', facts)
        run_id, _lease = store.create_run(conn, task_id, UID, 'generate', {}, batch_id)
        return task_id, batch_id, run_id

    with sto.write_tx() as tx:
        return tx.run(body)


def read_view(run_id):
    with sto.read_connection() as conn:
        row = store.get_run(conn, run_id, UID)
    return store.run_view(row) if row is not None else None


def read_generate(view):
    return ((view or {}).get('checkpoint') or {}).get('generate') or {}


def read_db_candidates(task_id, batch_id):
    with sto.read_connection() as conn:
        return store.all_candidates(conn, task_id, UID, batch_id=batch_id)


def wait_until(predicate, message):
    deadline = time.time() + POLL_SECONDS
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


POLL_SECONDS = 10


# --- 模型替身 -------------------------------------------------------------------------

def ok_candidate(batch):
    """一个候选/批：证据指向本批首条事实（真实 fact id，复核不降级）。"""
    fact_id = str(batch[0]['id'])
    return {'ok': True, 'usage': dict(USAGE), 'rejectedRefs': 0,
            'candidates': [{'key': 'k-' + fact_id, 'type': 'object',
                            'name': '设备实体 ' + fact_id,
                            'definition': '自动生成的对象定义（' + fact_id + '）',
                            'fields': {}, 'ownerKey': '',
                            'evidence': {'_record': [fact_id]},
                            'evidenceStatus': 'supported', 'conflicts': []}]}


def batch_key(batch):
    """批内首条事实 id（替身按批内容定响应的键）。"""
    return str(batch[0]['id'])


class PatchedModel:
    """替换 llm.extract_candidates 并保证场景结束恢复原函数。"""

    def __init__(self, target):
        self.original = llm.extract_candidates
        llm.extract_candidates = target

    def restore(self):
        llm.extract_candidates = self.original


class Patched:
    """通用模块属性补丁（setattr(target, name, value)，结束恢复）。"""

    def __init__(self, target, name, value):
        self.target, self.name, self.value = target, name, value
        self.original = getattr(target, name)
        setattr(target, name, value)

    def restore(self):
        setattr(self.target, self.name, self.original)


def run_generate_async(task_id, batch_id, run_id, resume_mode='auto'):
    def job(user, run):
        pipeline.run_generate(user, task_id, run, batch_id, PROVIDER,
                              resume_mode=resume_mode)
    return runner.submit(UID, run_id, job)


def resume_queued(run_id):
    """与 run-resume 路由同构：置回 queued + attempt 递增（轮换 lease）+ 清取消标记。"""
    with sto.write_tx() as tx:
        tx.run(lambda conn: store.update_run(conn, run_id, UID, state='queued', attempt=2,
                                             cancel_requested=False, error=''))


def join_workers(thread):
    thread.join(JOIN_SECONDS)
    for item in threading.enumerate():
        if item.name.startswith('build-run-'):
            item.join(JOIN_SECONDS)
    return not thread.is_alive()


# --- 场景 1（R5）：非连续续跑空洞 -------------------------------------------------------

def scenario_noncontiguous_resume():
    tag = '场景1 非连续续跑空洞'
    task_id, batch_id, run_id = seed_task_run(60)   # 3 批 × 20 条
    fail_result = {'ok': False, 'usage': dict(USAGE), 'error': FAIL_402}

    def first_phase(_provider, _scope, batch, timeout=None):
        # 串行抽取下调用序号 = 批次序号：复刻「批 1/3 失败、批 2 成功、批 3 失败」
        first_phase.calls += 1
        return ok_candidate(batch) if first_phase.calls == 2 else dict(fail_result)

    first_phase.calls = 0
    patch_a = PatchedModel(first_phase)
    patch_b = None
    try:
        thread = run_generate_async(task_id, batch_id, run_id)
        check(join_workers(thread), '%s：首轮生成线程已退出' % tag)
        view = read_view(run_id)
        gen = read_generate(view)
        batches = gen.get('batches') or {}
        check(view.get('state') == 'failed'
              and batches.get('done') == [2]
              and [item.get('position') for item in batches.get('failed') or []] == [1, 3],
              '%s：首轮 batch1/3 失败、batch2 成功（done={2}，制造非连续空洞）' % tag,
              expected={'state': 'failed', 'done': [2], 'failed': [1, 3]},
              actual={'state': view.get('state'), 'batches': batches})

        patch_a.restore()
        patch_b = PatchedModel(lambda provider, scope, batch, timeout=None: ok_candidate(batch))
        resume_queued(run_id)
        thread = run_generate_async(task_id, batch_id, run_id)   # pending=[1,3]，中间空洞
        check(join_workers(thread), '%s：续跑线程已退出' % tag)
        view = read_view(run_id)
        gen = read_generate(view)
        batches = gen.get('batches') or {}
        check(view.get('state') == 'succeeded'
              and batches.get('done') == [1, 2, 3] and batches.get('failed') == [],
              '%s：pending=[1,3] 全部落库并记 done，无静默丢失（R5 核心）' % tag,
              expected={'state': 'succeeded', 'done': [1, 2, 3], 'failed': []},
              actual={'state': view.get('state'), 'batches': batches})
        log = gen.get('log') or []
        check(log[-4:] == ['批 1/3 开始抽取（20 条事实）', '批 3/3 开始抽取（20 条事实）',
                           '批 1/3 完成：候选 1 个', '批 3/3 完成：候选 1 个'],
              '%s：非连续两批各自独立刷写（开始/完成行齐备，不依赖连续前缀）' % tag,
              expected=['批 1/3 开始抽取（20 条事实）', '批 3/3 开始抽取（20 条事实）',
                        '批 1/3 完成：候选 1 个', '批 3/3 完成：候选 1 个'],
              actual=log[-4:])
        rows = read_db_candidates(task_id, batch_id)
        names = [item.get('name') for item in rows]
        check(len(rows) == 3 and len(set(names)) == 3,
              '%s：三批候选全部入库且无重复（批 2 候选保留 + 批 1/3 补齐）' % tag,
              expected='3 条互不重复的候选（每批 1 条）', actual=names)
    finally:
        patch_a.restore()
        if patch_b is not None:
            patch_b.restore()


# --- 场景 2（R5）：完成序乱序立即落库 ---------------------------------------------------

def scenario_out_of_order_flush():
    tag = '场景2 完成序乱序'
    protocol.LLM_CONCURRENCY = 2   # 两路并发：批 1/2 同时在飞
    entered = {'f-1': threading.Event(), 'f-3': threading.Event(), 'f-5': threading.Event()}
    gates = {'f-1': threading.Event(), 'f-3': threading.Event(), 'f-5': threading.Event()}

    def responder(_provider, _scope, batch, timeout=None):
        key = batch_key(batch)
        entered[key].set()
        gates[key].wait(WAIT_SECONDS)
        return ok_candidate(batch)

    patch_model = PatchedModel(responder)
    flush_order = []
    done, accumulated = set(), []
    batches = [[{'id': 'f-%d' % n} for n in (1, 2)],
               [{'id': 'f-%d' % n} for n in (3, 4)],
               [{'id': 'f-%d' % n} for n in (5, 6)]]
    pending = list(enumerate(batches, start=1))

    def on_result(position, result):
        flush_order.append(position)
        if result.get('ok'):
            done.add(position)
            accumulated.extend(result.get('candidates') or [])

    def scheduler_job():
        pipeline._run_batches_adaptive('u', 'r', 'label', batches, pending, PROVIDER, {},
                                       done, accumulated,
                                       [{'id': 'f-%d' % n} for n in range(1, 7)],
                                       on_result=on_result)

    try:
        thread = threading.Thread(target=scheduler_job, name='accounting-sched')
        thread.start()
        check(entered['f-1'].wait(WAIT_SECONDS) and entered['f-3'].wait(WAIT_SECONDS),
              '%s：批 1/2 已并发进入模型调用' % tag)
        gates['f-3'].set()   # 先放行批 2（乱序完成）；其槽位释放后批 3 才提交
        check(wait_until(lambda: flush_order == [2], '等批 2 刷写'),
              '%s：批 2 先完成即立即回调落库（不再等前缀游标推进到 1）' % tag,
              expected=[2], actual=flush_order)
        gates['f-1'].set()
        check(wait_until(lambda: 1 in flush_order, '等批 1 刷写'),
              '%s：批 1 随后照常落库' % tag, actual=flush_order)
        gates['f-5'].set()
        thread.join(JOIN_SECONDS)
        check(not thread.is_alive() and flush_order == [2, 1, 3]
              and done == {1, 2, 3} and len(accumulated) == 3,
              '%s：乱序完成后全部批次独立落库（旧前缀实现会得到 [1,2,3] 且批 2 延迟）' % tag,
              expected={'order': [2, 1, 3], 'done': [1, 2, 3]},
              actual={'order': flush_order, 'done': sorted(done)})
    finally:
        for gate in gates.values():
            gate.set()
        thread.join(JOIN_SECONDS)
        patch_model.restore()
        protocol.LLM_CONCURRENCY = 1


# --- 场景 3（R5）：落库写事务失败计 failed ----------------------------------------------

def scenario_flush_tx_failure():
    tag = '场景3 落库写事务失败'
    task_id, batch_id, run_id = seed_task_run(60)
    patch_model = PatchedModel(lambda provider, scope, batch, timeout=None: ok_candidate(batch))
    # 内容事务次序（新批非续跑）：#1 批计划重置（删旧候选）→ #2..#4 批 1/2/3 刷写。
    # 让 #2（批 1 刷写）抛异常，模拟写事务失败。
    counter = {'n': 0}
    original_content_tx = runner.content_tx

    def flaky_content_tx(user_id, run, body):
        counter['n'] += 1
        if counter['n'] == 2:
            raise RuntimeError('模拟磁盘写入失败')
        return original_content_tx(user_id, run, body)

    patch_tx = Patched(runner, 'content_tx', flaky_content_tx)
    try:
        thread = run_generate_async(task_id, batch_id, run_id)
        check(join_workers(thread), '%s：生成线程已退出' % tag)
        view = read_view(run_id)
        gen = read_generate(view)
        batches = gen.get('batches') or {}
        failed = {item.get('position'): item for item in batches.get('failed') or []}
        check(view.get('state') == 'failed'
              and batches.get('done') == [2, 3]
              and set(failed) == {1}
              and str(failed.get(1, {}).get('error') or '').startswith('落库失败'),
              '%s：写事务失败批计入 failed（错误注明「落库失败」），其余批照常 done' % tag,
              expected={'state': 'failed', 'done': [2, 3], 'failed': {1: '落库失败…'}},
              actual={'state': view.get('state'), 'batches': batches})
        check('落库失败' in (view.get('error') or ''),
              '%s：运行以失败收场且错误可见（绝不假成功）' % tag,
              actual=view.get('error'))
        rows = read_db_candidates(task_id, batch_id)
        names = [item.get('name') for item in rows]
        check(len(rows) == 2,
              '%s：失败批候选未落库（事务原子），成功两批候选已保留' % tag,
              expected='2 条候选（批 1 的缺失）', actual=names)
        log = gen.get('log') or []
        check(any('批 1/3 落库失败' in line for line in log),
              '%s：批次日志记录落库失败行' % tag, actual=log)
    finally:
        patch_tx.restore()
        patch_model.restore()


# --- 场景 4（R5）：调度器漏报批次的收尾兜底 ---------------------------------------------

def scenario_scheduler_gap_safety_net():
    tag = '场景4 调度漏报收尾兜底'
    task_id, batch_id, run_id = seed_task_run(60)
    patch_model = PatchedModel(lambda provider, scope, batch, timeout=None: ok_candidate(batch))

    def lossy_scheduler(owner_id, r_id, label, batches, pending, provider, scope,
                        done_positions, accumulated, model_facts, on_result=None):
        # 模拟调度异常：只回报第一个 pending 批，其余批结果既不落库也不计失败
        for position, batch in pending[:1]:
            on_result(position, pipeline._extract_batch_with_split(provider, scope, batch))
        return {}

    patch_sched = Patched(pipeline, '_run_batches_adaptive', lossy_scheduler)
    try:
        thread = run_generate_async(task_id, batch_id, run_id)
        check(join_workers(thread), '%s：生成线程已退出' % tag)
        view = read_view(run_id)
        gen = read_generate(view)
        batches = gen.get('batches') or {}
        failed_positions = sorted(item.get('position') for item in batches.get('failed') or [])
        check(view.get('state') == 'failed' and failed_positions == [2, 3]
              and batches.get('done') == [1],
              '%s：漏报批次被收尾兜底显式计入 failed（绝不静默）' % tag,
              expected={'state': 'failed', 'done': [1], 'failed': [2, 3]},
              actual={'state': view.get('state'), 'batches': batches})
        check(all('已按失败处理' in str(item.get('error') or '')
                  for item in batches.get('failed') or []),
              '%s：兜底失败条目注明「已按失败处理，可重试补跑」' % tag,
              actual=batches.get('failed'))
        check('显式计入失败' in ' '.join(gen.get('notes') or []),
              '%s：notes 登记兜底事件' % tag, actual=gen.get('notes'))
        rows = read_db_candidates(task_id, batch_id)
        check(len(rows) == 1,
              '%s：仅已回报批次候选落库' % tag, actual=[r.get('name') for r in rows])
    finally:
        patch_sched.restore()
        patch_model.restore()


# --- 场景 5（R4）：拆批三态 -------------------------------------------------------------

def scenario_split_three_state():
    tag = '场景5 拆批三态'
    facts20 = [{'id': 'f-%d' % n} for n in range(1, 21)]

    # 按批大小 + 递归调用序定响应（串行、截断/402 均非瞬态不重试，调用序确定；
    # 不依赖事实 id 与拆批切分点的关系——select_scope 会按相关度重排事实顺序）：
    # 调用1=整批20（截断）→ 调用2/3=两半10（都截断）→ 调用4..7=四个四分批5，
    # 其中调用6（右半的左四分）失败，其余成功。
    nested_calls = {'n': 0}
    failed_quarter_ids = []

    def nested_responder(_provider, _scope, batch, timeout=None):
        nested_calls['n'] += 1
        if len(batch) >= 10:
            return {'ok': False, 'candidates': [], 'usage': dict(USAGE), 'error': TRUNCATED}
        if nested_calls['n'] == 6:   # 右半的左四分批：确定性失败
            failed_quarter_ids.extend(str(f['id']) for f in batch)
            return {'ok': False, 'candidates': [], 'usage': dict(USAGE), 'error': FAIL_402}
        return dict(ok_candidate(batch), usage=dict(USAGE))

    patch = PatchedModel(None)
    try:
        # 左成右败：整批截断 → 左半成功、右半失败（调用序：整批→左半→右半）
        lr_calls = {'n': 0}

        def left_ok_right_fail(_provider, _scope, batch, timeout=None):
            lr_calls['n'] += 1
            if len(batch) >= 20:
                return {'ok': False, 'candidates': [], 'usage': dict(USAGE), 'error': TRUNCATED}
            if lr_calls['n'] == 3:   # 右半（递归先左后右）
                return {'ok': False, 'candidates': [], 'usage': dict(USAGE), 'error': FAIL_402}
            return dict(ok_candidate(batch), usage=dict(USAGE))

        llm.extract_candidates = left_ok_right_fail
        try:
            r = pipeline._extract_batch_with_split(PROVIDER, {}, facts20)
        finally:
            llm.extract_candidates = patch.original
        check(r.get('ok') is False and len(r.get('candidates') or []) == 1
              and '拆批后部分失败' in str(r.get('error'))
              and '已保留 1 条候选' in str(r.get('error')),
              '%s：左成右败 → ok=False（父批不进 done），成功半候选保留，error 注明部分失败' % tag,
              expected={'ok': False, 'candidates': 1, 'error': '拆批后部分失败…'},
              actual={'ok': r.get('ok'), 'candidates': len(r.get('candidates') or []),
                      'error': r.get('error')})
        check(len(r.get('failedFactIds') or []) == 10,
              '%s：failedFactIds 携带失败半批全部事实 id' % tag,
              expected='10 个失败事实 id', actual=r.get('failedFactIds'))
        check(isinstance(r.get('split'), dict) and r['split'].get('halves') == [10, 10],
              '%s：最外层携带拆批信息（日志记「已自动拆批重试」行）' % tag,
              actual=r.get('split'))

        # 右成左败：镜像场景（调用序不变，失败半换成左半）
        rl_calls = {'n': 0}

        def right_ok_left_fail(_provider, _scope, batch, timeout=None):
            rl_calls['n'] += 1
            if len(batch) >= 20:
                return {'ok': False, 'candidates': [], 'usage': dict(USAGE), 'error': TRUNCATED}
            if rl_calls['n'] == 2:   # 左半
                return {'ok': False, 'candidates': [], 'usage': dict(USAGE), 'error': FAIL_402}
            return dict(ok_candidate(batch), usage=dict(USAGE))

        llm.extract_candidates = right_ok_left_fail
        try:
            r2 = pipeline._extract_batch_with_split(PROVIDER, {}, facts20)
        finally:
            llm.extract_candidates = patch.original
        check(r2.get('ok') is False and len(r2.get('candidates') or []) == 1
              and len(r2.get('failedFactIds') or []) == 10
              and '拆批后部分失败' in str(r2.get('error')),
              '%s：右成左败 → 同样部分失败记账，failedFactIds 指向左半' % tag,
              expected={'ok': False, 'candidates': 1, 'failed': '10 个失败事实 id'},
              actual={'ok': r2.get('ok'), 'candidates': len(r2.get('candidates') or []),
                      'failed': r2.get('failedFactIds')})

        # 全部失败：两半都失败 → ok=False、无候选、错误为「拆批重试仍失败」
        def all_fail(_provider, _scope, batch, timeout=None):
            if len(batch) >= 20:
                return {'ok': False, 'candidates': [], 'usage': dict(USAGE), 'error': TRUNCATED}
            return {'ok': False, 'candidates': [], 'usage': dict(USAGE), 'error': FAIL_402}

        llm.extract_candidates = all_fail
        try:
            r3 = pipeline._extract_batch_with_split(PROVIDER, {}, facts20)
        finally:
            llm.extract_candidates = patch.original
        check(r3.get('ok') is False and not (r3.get('candidates') or [])
              and '拆批重试仍失败' in str(r3.get('error')),
              '%s：全失败 → ok=False 无候选（原语义保留）' % tag,
              actual={'ok': r3.get('ok'), 'candidates': r3.get('candidates'),
                      'error': r3.get('error')})

        # 嵌套拆批（depth≥1 三态传递）：整批与两半都截断 → 四分；右半的左四分失败
        llm.extract_candidates = nested_responder
        try:
            r4 = pipeline._extract_batch_with_split(PROVIDER, {}, facts20)
        finally:
            llm.extract_candidates = patch.original
        check(r4.get('ok') is False and len(r4.get('candidates') or []) == 3
              and (r4.get('failedFactIds') or []) == failed_quarter_ids,
              '%s：嵌套拆批部分失败 → 3/4 成功候选保留，failedFactIds 精确到失败四分批' % tag,
              expected={'ok': False, 'candidates': 3, 'failed': failed_quarter_ids},
              actual={'ok': r4.get('ok'), 'candidates': len(r4.get('candidates') or []),
                      'failed': r4.get('failedFactIds')})

        # 全成功基线：拆批合并后 ok=True（既有语义不回归）
        llm.extract_candidates = lambda provider, scope, batch, timeout=None: (
            {'ok': False, 'candidates': [], 'usage': dict(USAGE), 'error': TRUNCATED}
            if len(batch) >= 20
            else dict(ok_candidate(batch), usage=dict(USAGE)))
        try:
            r5 = pipeline._extract_batch_with_split(PROVIDER, {}, facts20)
        finally:
            llm.extract_candidates = patch.original
        check(r5.get('ok') is True and len(r5.get('candidates') or []) == 2,
              '%s：全成功 → ok=True 两半候选合并（未拆批语义不回归）' % tag,
              actual={'ok': r5.get('ok'), 'candidates': len(r5.get('candidates') or [])})
    finally:
        patch.restore()


# --- 场景 6（R4）：部分失败整批重试幂等 -------------------------------------------------

def scenario_partial_retry_idempotent():
    tag = '场景6 部分失败整批重试幂等'
    task_id, batch_id, run_id = seed_task_run(20)   # 1 批 × 20 条
    # 调用序定响应（串行、截断/402 均非瞬态不重试，序确定；不依赖事实 id 与切分点关系）：
    # 每轮 调用1=整批20（截断 → 拆批）→ 调用2=左半10（恒成功）→ 调用3=右半10
    # （首轮 402 失败，重试轮成功）。两轮候选键一致（同计划同批内容）。
    calls = {'n': 0}
    state = {'right_ok': False}

    def responder(_provider, _scope, batch, timeout=None):
        calls['n'] += 1
        if len(batch) >= 20:
            return {'ok': False, 'candidates': [], 'usage': dict(USAGE), 'error': TRUNCATED}
        if calls['n'] == 3 and not state['right_ok']:
            return {'ok': False, 'candidates': [], 'usage': dict(USAGE), 'error': FAIL_402}
        return dict(ok_candidate(batch), usage=dict(USAGE))

    patch = PatchedModel(responder)
    try:
        thread = run_generate_async(task_id, batch_id, run_id)
        check(join_workers(thread), '%s：首轮生成线程已退出' % tag)
        view = read_view(run_id)
        gen = read_generate(view)
        batches = gen.get('batches') or {}
        failed = batches.get('failed') or []
        check(view.get('state') == 'failed' and batches.get('done') == []
              and len(failed) == 1 and failed[0].get('position') == 1
              and '拆批后部分失败' in str(failed[0].get('error') or ''),
              '%s：拆批部分失败 → 父批记失败（不进 done），error 注明可重试' % tag,
              expected={'state': 'failed', 'done': [], 'failed[0].error': '拆批后部分失败…'},
              actual={'state': view.get('state'), 'batches': batches})
        rows = read_db_candidates(task_id, batch_id)
        first_round_names = sorted(item.get('name') for item in rows)
        check(len(rows) == 1,
              '%s：成功半候选已落库（不浪费已成功的调用）' % tag,
              expected='1 条（左半）', actual=first_round_names)
        check(any('部分成功：已保留 1 条候选' in line for line in gen.get('log') or []),
              '%s：批次日志记「部分成功：已保留 N 条候选」行' % tag, actual=gen.get('log'))

        state['right_ok'] = True
        calls['n'] = 0
        resume_queued(run_id)
        thread = run_generate_async(task_id, batch_id, run_id)   # 整批重跑（done 为空）
        check(join_workers(thread), '%s：重试线程已退出' % tag)
        view = read_view(run_id)
        gen = read_generate(view)
        batches = gen.get('batches') or {}
        rows = read_db_candidates(task_id, batch_id)
        names = sorted(item.get('name') for item in rows)
        check(view.get('state') == 'succeeded' and batches.get('done') == [1]
              and batches.get('failed') == [],
              '%s：整批重跑后成功收尾' % tag,
              actual={'state': view.get('state'), 'batches': batches})
        check(len(rows) == 2 and set(first_round_names) <= set(names),
              '%s：批内去重生效——左半候选不双写，共 2 条无重复（R4 幂等配套）' % tag,
              expected='首轮 1 条保留 + 右半新增 1 条', actual=names)
    finally:
        patch.restore()


# --- 场景 7：429 限流单批单次调用 -------------------------------------------------------

def scenario_rate_limited_single_call():
    tag = '场景7 429单次调用不重试'
    calls = {'n': 0}

    def rate_limited(provider, scope, batch, timeout=None):
        calls['n'] += 1
        return {'ok': False, 'candidates': [], 'usage': dict(USAGE),
                'error': 'HTTP 429：请求过于频繁，请稍后再试'}

    patch = PatchedModel(rate_limited)
    try:
        batch = [{'id': 'f-%d' % n} for n in range(1, 21)]
        result = pipeline._extract_batch_with_split(PROVIDER, {}, batch)
        check(calls['n'] == 1,
              '%s：撞 429 单次调用立即返回（不批内重试，交调度器降挡重排）' % tag,
              expected=1, actual=calls['n'])
        check(result.get('ok') is False and 'HTTP 429' in str(result.get('error') or ''),
              '%s：429 结果原样上抛（调度器据此降挡并冷却）' % tag,
              actual={'ok': result.get('ok'), 'error': result.get('error')})
    finally:
        patch.restore()


# --- 入口 -----------------------------------------------------------------------------

SCENARIOS = [
    ('noncontiguous_resume', scenario_noncontiguous_resume),
    ('out_of_order_flush', scenario_out_of_order_flush),
    ('flush_tx_failure', scenario_flush_tx_failure),
    ('scheduler_gap_safety_net', scenario_scheduler_gap_safety_net),
    ('split_three_state', scenario_split_three_state),
    ('partial_retry_idempotent', scenario_partial_retry_idempotent),
    ('rate_limited_single_call', scenario_rate_limited_single_call),
]


def main():
    for tag, fn in SCENARIOS:
        print('\n----- %s -----' % tag)
        new_isolated_root(tag)
        fn()
    return 0


def shutdown():
    """收尾：确认没有遗留 worker 线程，再销毁全部临时根（绝不碰真实 ontology/）。"""
    leftover = [t for t in threading.enumerate() if t.name.startswith('build-run-')]
    for thread in leftover:
        thread.join(10)
    alive = [t.name for t in leftover if t.is_alive()]
    if alive:
        FAILED.append('worker 线程未退出: %s' % alive)
    if runner._workers:
        FAILED.append('worker 登记残留: %s' % sorted(runner._workers))
    sto.reset_engine()
    for root in ROOTS:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == '__main__':
    code = 1
    try:
        code = main()
    except Exception as exc:
        traceback.print_exc()
        FAILED.append('未预期异常: %s: %s' % (type(exc).__name__, exc))
    finally:
        shutdown()
    total = len(PASSED) + len(FAILED)
    print('\n========== 汇总 ==========')
    print('通过 %d / %d' % (len(PASSED), total))
    for name in FAILED:
        print('  失败: ' + name)
    if total == 0:
        print('[错误] 未执行任何断言——不算通过')
        sys.exit(2)
    sys.exit(0 if code == 0 and not FAILED else 1)
