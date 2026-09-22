"""生成进度实时可观测回归（需求 §3 C1/C4、§4 V1/V2/V6：需求说明_生成进度实时可观测_v1.md）。

断言批次日志（checkpoint.generate.log）与 notes 随每次 _checkpoint 同事务写库、
运行中轮询 run_view() 即可看到：

1. 3 批抽取（串行门控）：抽取开跑前初始检查点已带 3 条「开始抽取」行与已有 notes
   （长抽取期间轮询可见）；每批完成即刻前缀刷写「完成：候选 N 个」行（批 N 完成后、
   批 N+1 仍在抽取时即可见——运行中逐批实时追加）；成功收尾的最终检查点不丢日志与
   notes（B1 教训：最终 checkpoint 必须携带 log/notes）。
2. 失败 + 续跑：批 2/3 失败 → 日志记「失败」行、checkpoint.batches.failed 带原因；
   resume 只跑失败批，日志承接上次持久化记录继续追加（不抹掉已完成的批次记录）。
3. 截断拆批：批内首调返回「截断」→ 自动拆 1+1 重试 → 日志记「已自动拆批重试（2→1+1）」。
4. log 上限 500 条：502 批（每批 1 条事实，共 1004 行）→ checkpoint 恰保留最近 500 条
   （最早两批「完成」行与全部「开始」行被移除）；notes 不受日志上限影响。

隔离（AGENTS.md 测试隔离铁律）：每个场景独立临时根（WIZ_WORKBENCH_ROOT +
该根下 WIZ_DATABASE_URL），只写临时目录；替换 llm.extract_candidates 模块属性控制
模型响应（不访问网络、不连真实库、不依赖其它测试文件）；结束销毁全部临时根。

运行：python3 tests/test_ontology_build_progress_log.py
"""
import os
import shutil
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path

os.environ['WIZ_BUILD_LLM_CONCURRENCY'] = '1'   # 串行抽取：门控时序才确定（import 前设置）

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from workbench import storage  # noqa: E402
from workbench.ontology_build import llm, pipeline, protocol, runner  # noqa: E402
from workbench.storage import engine as sto  # noqa: E402
from workbench.storage import ontology_build as store  # noqa: E402

protocol.LLM_CONCURRENCY = 1   # 双保险：调度器在调用点读模块属性

UID = 'progress-log-owner'
WAIT_SECONDS = 15
JOIN_SECONDS = 60
POLL_SECONDS = 10
PROVIDER = {'endpoint': 'mock', 'model': 'mock'}
USAGE = {'calls': 1, 'promptBytes': 16, 'completionBytes': 8, 'durationMs': 1}

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
    root = Path(tempfile.mkdtemp(prefix='wiz_progress_log_%s_' % tag))
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

    事实 snippet 各不相同（避免对齐阶段按片段哈希去重），且都含范围 include 词「设备」
    （select_scope 全部判 relevant，批次划分 = 每 protocol.LLM_BATCH_FACTS 条一批）。
    """

    def body(conn):
        task_id = store.create_task(conn, UID, '进度日志回归')
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
    """run_view() 同形读取（轮询入口与前端一致：checkpoint 经 run_checkpoint_view 透传）。"""
    with sto.read_connection() as conn:
        row = store.get_run(conn, run_id, UID)
    return store.run_view(row) if row is not None else None


def read_generate(view):
    return ((view or {}).get('checkpoint') or {}).get('generate') or {}


def wait_until(predicate, message):
    deadline = time.time() + POLL_SECONDS
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


# --- 模型 mock -----------------------------------------------------------------------

def ok_candidate(batch):
    """一个候选/批：证据指向本批首条事实（真实 fact id，复核不降级）。"""
    fact_id = str(batch[0]['id'])
    return {'ok': True, 'usage': dict(USAGE), 'rejectedRefs': 0,
            'candidates': [{'key': 'device-' + fact_id, 'type': 'object',
                            'name': '设备实体 ' + fact_id,
                            'definition': '自动生成的对象定义（' + fact_id + '）',
                            'fields': {}, 'ownerKey': '',
                            'evidence': {'_record': [fact_id]},
                            'evidenceStatus': 'supported', 'conflicts': []}]}


class GatedModel:
    """每次调用进入时置位 entered[i] 并阻塞在 gates[i]，测试逐批放行（串行抽取）。"""

    def __init__(self, responder):
        self.responder = responder   # fn(call_index, batch) -> result dict
        self.calls = 0
        self._lock = threading.Lock()
        self._entered = []
        self._gates = []

    def _slot(self, index):
        with self._lock:
            while len(self._entered) <= index:
                self._entered.append(threading.Event())
                self._gates.append(threading.Event())
            return self._entered[index], self._gates[index]

    def entered(self, index):
        return self._slot(index)[0]

    def release(self, index):
        self._slot(index)[1].set()

    def release_all(self):
        with self._lock:
            gates = list(self._gates)
        for gate in gates:
            gate.set()

    def __call__(self, provider, scope, batch, timeout=None):
        with self._lock:
            index = self.calls
            self.calls += 1
        entered, gate = self._slot(index)
        entered.set()
        if not gate.wait(WAIT_SECONDS):
            raise AssertionError('模型调用 %d 未按时放行' % (index + 1))
        return self.responder(index, batch)


class PatchedModel:
    """替换 llm.extract_candidates 并保证场景结束恢复原函数。"""

    def __init__(self, target):
        self.original = llm.extract_candidates
        llm.extract_candidates = target

    def restore(self):
        llm.extract_candidates = self.original


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


# --- 场景 1：运行中可见 + 成功收尾不丢 -------------------------------------------------

def scenario_midrun_log_visible():
    tag = '场景1 运行中日志可见/成功收尾不丢'
    task_id, batch_id, run_id = seed_task_run(60)   # 3 批 × 20 条
    model = GatedModel(lambda index, batch: ok_candidate(batch))
    patch = PatchedModel(model)
    try:
        thread = run_generate_async(task_id, batch_id, run_id)
        check(model.entered(0).wait(WAIT_SECONDS), '%s：批 1 已进入模型调用' % tag)

        def gen():
            return read_generate(read_view(run_id))

        check(wait_until(lambda: len(gen().get('log') or []) == 3,
                         '等初始检查点'), '%s：抽取开跑前初始检查点已落库' % tag)
        log = gen().get('log') or []
        check(log == ['批 1/3 开始抽取（20 条事实）', '批 2/3 开始抽取（20 条事实）',
                      '批 3/3 开始抽取（20 条事实）'],
              '%s：长抽取期间轮询可见 3 条「开始抽取」行（含批号与事实数）' % tag,
              expected=['批 1/3 开始抽取（20 条事实）', '批 2/3 开始抽取（20 条事实）',
                        '批 3/3 开始抽取（20 条事实）'], actual=log)
        notes = gen().get('notes') or []
        check(bool(notes) and '证据分组' in notes[0],
              '%s：运行中 notes 已随检查点可见（对齐摘要透传，V2）' % tag,
              actual=notes)
        check((gen().get('batches') or {}).get('total') == 3,
              '%s：批次检查点摘要正常（total=3）' % tag)

        model.release(0)   # 批 1 返回 → 前缀刷写落库批 1 → 串行调度批 2
        check(model.entered(1).wait(WAIT_SECONDS), '%s：批 2 进入模型调用' % tag)
        log = gen().get('log') or []
        check(log == ['批 1/3 开始抽取（20 条事实）', '批 2/3 开始抽取（20 条事实）',
                      '批 3/3 开始抽取（20 条事实）', '批 1/3 完成：候选 1 个'],
              '%s：批 1 完成后运行中即可见其「完成：候选 N 个」行（逐批实时追加，V1）' % tag,
              expected='前 3 行开始 + 批 1/3 完成：候选 1 个', actual=log)

        model.release(1)
        check(model.entered(2).wait(WAIT_SECONDS), '%s：批 3 进入模型调用' % tag)
        log = gen().get('log') or []
        check(len(log) == 5 and log[-1] == '批 2/3 完成：候选 1 个',
              '%s：批 2 完成后日志继续增长（批 3 仍在抽取）' % tag, actual=log)
        model.release(2)
        thread.join(JOIN_SECONDS)
        check(not thread.is_alive(), '%s：生成线程已退出' % tag)

        view = read_view(run_id)
        gen_view = read_generate(view)
        expected_log = ['批 1/3 开始抽取（20 条事实）', '批 2/3 开始抽取（20 条事实）',
                        '批 3/3 开始抽取（20 条事实）', '批 1/3 完成：候选 1 个',
                        '批 2/3 完成：候选 1 个', '批 3/3 完成：候选 1 个']
        check(view.get('state') == 'succeeded' and gen_view.get('log') == expected_log,
              '%s：成功收尾后日志完整保留且逐批追加「完成：候选 N 个」（B1：最终 checkpoint 不抹日志）' % tag,
              expected={'state': 'succeeded', 'log': expected_log},
              actual={'state': (view or {}).get('state'), 'log': gen_view.get('log')})
        final_notes = gen_view.get('notes') or []
        check('证据分组' in ' '.join(final_notes),
              '%s：最终 checkpoint 的 notes 仍可查看（不丢失）' % tag, actual=final_notes)
    finally:
        model.release_all()
        patch.restore()
        for item in threading.enumerate():
            if item.name.startswith('build-run-'):
                item.join(JOIN_SECONDS)


# --- 场景 2：失败行 + 续跑承接追加 -----------------------------------------------------

def scenario_failure_resume_log_append():
    tag = '场景2 失败日志/续跑承接追加'
    task_id, batch_id, run_id = seed_task_run(60)
    fail_result = {'ok': False, 'usage': dict(USAGE), 'error': '模型配额不足（HTTP 402）'}

    def first_phase(_provider, _scope, batch, timeout=None):
        # 串行抽取下调用序号 = 批次序号：首个调用成功，其后失败
        # → 复刻「批 1 成功、批 2/3 失败」。
        first_phase.calls += 1
        return ok_candidate(batch) if first_phase.calls == 1 else dict(fail_result)

    first_phase.calls = 0
    patch = PatchedModel(first_phase)
    patch_b = None
    thread = None
    try:
        thread = run_generate_async(task_id, batch_id, run_id)
        thread.join(JOIN_SECONDS)
        check(not thread.is_alive(), '%s：首轮生成线程已退出' % tag)
        view = read_view(run_id)
        gen_view = read_generate(view)
        expected_log = ['批 1/3 开始抽取（20 条事实）', '批 2/3 开始抽取（20 条事实）',
                        '批 3/3 开始抽取（20 条事实）', '批 1/3 完成：候选 1 个',
                        '批 2/3 失败：模型配额不足（HTTP 402）',
                        '批 3/3 失败：模型配额不足（HTTP 402）']
        check(view.get('state') == 'failed' and gen_view.get('log') == expected_log,
              '%s：失败批记录「失败：原因」行且随批次检查点持久化' % tag,
              expected={'state': 'failed', 'log': expected_log},
              actual={'state': view.get('state'), 'log': gen_view.get('log')})
        batches = gen_view.get('batches') or {}
        check(batches.get('done') == [1]
              and [item.get('position') for item in batches.get('failed') or []] == [2, 3]
              and all('模型配额不足' in (item.get('error') or '')
                      for item in batches.get('failed') or []),
              '%s：checkpoint.batches 记录成功/失败批与具体原因（V4 数据源）' % tag,
              actual=batches)
        patch.restore()

        patch_b = PatchedModel(lambda provider, scope, batch, timeout=None: ok_candidate(batch))
        resume_queued(run_id)
        thread = run_generate_async(task_id, batch_id, run_id)   # resumeMode=auto 只补失败批
        thread.join(JOIN_SECONDS)
        check(not thread.is_alive(), '%s：续跑线程已退出' % tag)
        view = read_view(run_id)
        gen_view = read_generate(view)
        expected_resumed = expected_log + ['批 2/3 开始抽取（20 条事实）',
                                           '批 3/3 开始抽取（20 条事实）',
                                           '批 2/3 完成：候选 1 个', '批 3/3 完成：候选 1 个']
        check(view.get('state') == 'succeeded'
              and gen_view.get('log') == expected_resumed,
              '%s：续跑承接上次日志并只追加失败批的记录（不抹掉已完成的批次记录）' % tag,
              expected={'state': 'succeeded', 'log': expected_resumed},
              actual={'state': view.get('state'), 'log': gen_view.get('log')})
        final_notes = ' '.join(gen_view.get('notes') or [])
        check('检测到上次失败的批次' in final_notes
              and '复用上次运行持久化的筛选/对齐产物' in final_notes,
              '%s：续跑 notes 透传（失败批检测 + 确定性阶段复用）' % tag,
              actual=gen_view.get('notes'))
        check((gen_view.get('batches') or {}).get('done') == [1, 2, 3]
              and (gen_view.get('batches') or {}).get('failed') == [],
              '%s：续跑后批次检查点全绿' % tag, actual=gen_view.get('batches'))
    finally:
        patch.restore()
        if patch_b is not None:
            patch_b.restore()
        for item in threading.enumerate():
            if item.name.startswith('build-run-'):
                item.join(JOIN_SECONDS)


# --- 场景 3：截断拆批记日志 ------------------------------------------------------------

def scenario_split_logged():
    tag = '场景3 截断拆批记日志'
    task_id, batch_id, run_id = seed_task_run(2)   # 1 批 × 2 条
    original_batch_facts = protocol.LLM_BATCH_FACTS
    protocol.LLM_BATCH_FACTS = 2
    try:

        def responder(_provider, _scope, batch, timeout=None):
            if len(batch) >= 2:   # 整批首调：输出截断（非瞬态，不重试直接进拆批分支）
                return {'ok': False, 'usage': dict(USAGE), 'error': '输出被截断：超出单次输出预算'}
            return ok_candidate(batch)

        patch = PatchedModel(responder)
        try:
            thread = run_generate_async(task_id, batch_id, run_id)
            thread.join(JOIN_SECONDS)
            view = read_view(run_id)
            gen_view = read_generate(view)
            expected_log = ['批 1/1 开始抽取（2 条事实）',
                            '批 1 输出截断，已自动拆批重试（2→1+1）',
                            '批 1/1 完成：候选 2 个']
            check(view.get('state') == 'succeeded' and gen_view.get('log') == expected_log,
                  '%s：截断自动拆批后日志记「已自动拆批重试（2→1+1）」与合并后的候选数' % tag,
                  expected={'state': 'succeeded', 'log': expected_log},
                  actual={'state': view.get('state'), 'log': gen_view.get('log')})
        finally:
            patch.restore()
    finally:
        protocol.LLM_BATCH_FACTS = original_batch_facts
        for item in threading.enumerate():
            if item.name.startswith('build-run-'):
                item.join(JOIN_SECONDS)


# --- 场景 4：log 上限 500 条 -----------------------------------------------------------

def scenario_log_cap_500():
    tag = '场景4 log上限500条'
    total_batches = 502
    task_id, batch_id, run_id = seed_task_run(total_batches)   # 每批 1 条事实
    original_batch_facts = protocol.LLM_BATCH_FACTS
    original_max_facts = pipeline.MAX_MODEL_FACTS
    protocol.LLM_BATCH_FACTS = 1
    pipeline.MAX_MODEL_FACTS = 600   # 不触发总量截断，全部 502 条进批次
    try:
        patch = PatchedModel(lambda provider, scope, batch, timeout=None: ok_candidate(batch))
        try:
            thread = run_generate_async(task_id, batch_id, run_id)
            thread.join(300)
            check(not thread.is_alive(), '%s：%d 批生成线程已退出' % (tag, total_batches))
            view = read_view(run_id)
            gen_view = read_generate(view)
            log = gen_view.get('log') or []
            # 全程 502 条「开始」+ 502 条「完成」= 1004 行，保留最近 500 行
            # = 批 3..502 的「完成」行（最早的开始行与批 1/2 完成行被移除）。
            expected_first = '批 %d/%d 完成：候选 1 个' % (total_batches - 499, total_batches)
            expected_last = '批 %d/%d 完成：候选 1 个' % (total_batches, total_batches)
            check(view.get('state') == 'succeeded' and len(log) == 500,
                  '%s：%d 批（%d 行）后 checkpoint 恰保留最近 500 条' % (tag, total_batches,
                                                                    total_batches * 2),
                  expected={'state': 'succeeded', 'len': 500},
                  actual={'state': view.get('state'), 'len': len(log)})
            check(bool(log) and log[0] == expected_first and log[-1] == expected_last,
                  '%s：超限移除最早（保留批 %d..%d 的完成行）' % (tag, total_batches - 499,
                                                             total_batches),
                  expected=[expected_first, '…', expected_last],
                  actual=[log[0] if log else None, '…', log[-1] if log else None])
            check(all('完成' in line for line in log),
                  '%s：全部「开始抽取」行已被移出窗口' % tag)
            notes = gen_view.get('notes') or []
            check(bool(notes),
                  '%s：notes 不受日志上限影响（对齐/校验摘要仍在）' % tag, actual=notes[:3])
        finally:
            patch.restore()
    finally:
        protocol.LLM_BATCH_FACTS = original_batch_facts
        pipeline.MAX_MODEL_FACTS = original_max_facts
        for item in threading.enumerate():
            if item.name.startswith('build-run-'):
                item.join(JOIN_SECONDS)


# --- 入口 -----------------------------------------------------------------------------

SCENARIOS = [
    ('midrun_log_visible', scenario_midrun_log_visible),
    ('failure_resume_log_append', scenario_failure_resume_log_append),
    ('split_logged', scenario_split_logged),
    ('log_cap_500', scenario_log_cap_500),
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
