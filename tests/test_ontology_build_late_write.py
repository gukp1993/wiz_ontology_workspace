"""业务内容晚写入回归（R4-01：取消+重试后旧 worker 的候选/消息不得污染成功结果）。

复刻第 4 轮验收报告 §5/§8 归档探针 `文档/需求/20260920_从物料自动构建本体/
验收报告_第4轮_20260921.md` 的真实时序（真实 runner 线程 + 真实 SQLite 临时根 +
替换 `llm.extract_candidates` / `llm.scope_turn` 模块属性控制响应时序；模型 mock
按调用序号区分新旧 worker：第 1 次调用 = 旧 worker A 的旧响应，之后 = 新 worker B
的新响应；释放顺序用 threading.Event 控制），但断言**修复后的正确行为**——修复
口径：业务内容写点（候选 / 对话消息 / 材料事实）经 `runner.content_tx()` 在同一
写事务内先核对取消与执行权再落库。

五个场景：
1. generate / B 已成功后释放 A：A 阻塞在模型调用 → 取消 A → 重试路径
   （queued + attempt=2 + 清取消标记）→ 提交 B → B 完成成功 → 释放 A。
   断言：批次内候选恰好 1 条且是 B 的新结果；run state='succeeded'；
   B 写入的 progress/checkpoint/usage 等整行未被 A 改动。
2. generate / B 仍在执行时释放 A：两个 worker 都阻塞在模型调用；先释放 A →
   A 的内容写入被拒（库里 0 条候选）；再释放 B → 恰好 1 条 B 的候选；
   state='succeeded'。
3. generate / 仅取消（无重试无 B）：取消 A 后直接释放 A → 0 条候选；
   run 最终 cancelled。
4. dialog / B 已成功后释放 A：消息流只有 B 的一条助手消息（没有 A 的 seq2）。
5. dialog / 仅取消：释放 A 后不新增助手消息。

隔离（AGENTS.md 测试隔离铁律）：每个场景独立临时根（WIZ_WORKBENCH_ROOT +
该根下 WIZ_DATABASE_URL），只写临时目录；不访问网络、不连真实库、不依赖其它
测试文件；结束销毁全部临时根。

运行：python3 tests/test_ontology_build_late_write.py
"""
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
from workbench.ontology_build import llm, pipeline, runner  # noqa: E402
from workbench.storage import engine as sto  # noqa: E402
from workbench.storage import ontology_build as store  # noqa: E402

UID = 'late-write-owner'
WAIT_SECONDS = 15
JOIN_SECONDS = 25
OLD_NAME = '旧结果设备（A 旧 worker）'
NEW_NAME = '新结果设备（B 新 worker）'
PROVIDER = {'endpoint': 'mock', 'model': 'mock'}

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
    root = Path(tempfile.mkdtemp(prefix='wiz_late_write_%s_' % tag))
    ROOTS.append(root)
    os.environ['WIZ_WORKBENCH_ROOT'] = str(root)
    os.environ['WIZ_DATABASE_URL'] = 'sqlite:///' + str(root / 'data' / 'workbench.sqlite3')
    sto.reset_engine()
    storage.ensure_ready()
    if str(root) not in sto.resolve_url():
        raise AssertionError('隔离根未生效：%s' % sto.resolve_url())
    return root


# --- 种子与读取 ----------------------------------------------------------------------

def seed_task_run(kind):
    """建任务 + 批次 + 范围 + 事实 + run（与归档探针同构，真实存储层短写事务）。"""

    def body(conn):
        task_id = store.create_task(conn, UID, '晚写入回归 %s' % kind)
        batch_id = store.create_batch(conn, task_id, UID, '', {})
        store.put_scope(conn, task_id, UID,
                        {'goal': '设备管理', 'include': '设备', 'exclude': '', 'relations': '',
                         'coverage': '', 'openQuestions': []}, confirmed=True)
        store.replace_material_facts(conn, task_id, UID, 'm',
                                     [{'id': 'f1-' + kind, 'module': 'device',
                                       'locator': {'kind': 'ddl'}, 'snippet': '设备台账 device',
                                       'kind': 'table', 'data': {'table': 'device'},
                                       'quality': 'high'}])
        run_id, _lease = store.create_run(conn, task_id, UID, kind, {}, batch_id)
        return task_id, batch_id, run_id

    with sto.write_tx() as tx:
        return tx.run(body)


def read_candidates(task_id, batch_id):
    with sto.read_connection() as conn:
        return store.all_candidates(conn, task_id, UID, batch_id=batch_id)


def read_messages(task_id):
    with sto.read_connection() as conn:
        return store.list_messages(conn, task_id, UID)


def read_run(run_id):
    with sto.read_connection() as conn:
        row = store.get_run(conn, run_id, UID)
    return dict(row) if row is not None else None


# --- 模型 mock：按调用序号区分新旧 worker ---------------------------------------------

class ModelScript:
    """第 1 次调用返回旧 worker A 的旧响应，之后返回新 worker B 的新响应。

    每次调用进入时置位 entered[i]，随后阻塞在 gates[i] 上等待测试放行——
    用来把 worker 钉在「模型调用返回之后、内容写库之前」的位置。
    """

    def __init__(self, kind):
        self.kind = kind
        self.calls = 0
        self._entered = []
        self._gates = []
        self._lock = threading.Lock()

    def _slot(self, index):
        with self._lock:
            while len(self._entered) <= index:
                self._entered.append(threading.Event())
                self._gates.append(threading.Event())
            return self._entered[index], self._gates[index]

    def entered(self, index):
        entered, _gate = self._slot(index)
        return entered

    def release(self, index):
        _entered, gate = self._slot(index)
        gate.set()

    def release_all(self):
        with self._lock:
            gates = list(self._gates)
        for gate in gates:
            gate.set()

    def __call__(self, *args, **kwargs):
        with self._lock:
            index = self.calls
            self.calls += 1
        entered, gate = self._slot(index)
        entered.set()
        if not gate.wait(WAIT_SECONDS):
            raise AssertionError('模型调用 %d 未按时放行' % (index + 1))
        old = (index == 0)
        name = OLD_NAME if old else NEW_NAME
        usage = {'calls': 1, 'promptBytes': 16, 'completionBytes': 8, 'durationMs': 1}
        if self.kind == 'generate':
            return {'ok': True, 'usage': usage, 'rejectedRefs': 0,
                    'candidates': [{'key': 'old' if old else 'new', 'type': 'object',
                                    'name': name,
                                    'definition': '旧 worker 生成的定义' if old else '新 worker 生成的定义',
                                    'fields': {}, 'ownerKey': '',
                                    'evidence': {'_record': ['f1-' + self.kind]},
                                    'evidenceStatus': 'supported', 'conflicts': []}]}
        return {'ok': True, 'usage': usage,
                'questions': [{'text': name}],
                'patch': {}, 'notes': []}


# --- 场景骨架 -------------------------------------------------------------------------

def run_flow(kind, retry):
    """真实时序：A 阻塞在模型调用 → 取消 A（retry 时走重试路径并提交 B）→ 按模式释放。

    retry=True：提交 B 并等 B 阻塞在模型调用，先释放 B（B 成功完成）再释放 A；
    retry=False：无 B，取消后直接释放 A。
    返回 out 快照（线程、事件、数据库断言前的读数）。
    """
    task_id, batch_id, run_id = seed_task_run(kind)
    script = ModelScript(kind)
    fn_name = 'extract_candidates' if kind == 'generate' else 'scope_turn'
    original = getattr(llm, fn_name)
    setattr(llm, fn_name, script)
    out = {'task_id': task_id, 'batch_id': batch_id, 'run_id': run_id,
           'script': script, 'kind': kind, 'retry': retry}

    def job(user, run):
        if kind == 'generate':
            pipeline.run_generate(user, task_id, run, batch_id, PROVIDER)
        else:
            pipeline.run_dialog(user, task_id, run, PROVIDER)

    try:
        out['a'] = runner.submit(UID, run_id, job)
        if not script.entered(0).wait(WAIT_SECONDS):
            raise AssertionError('旧 worker A 未进入模型调用')
        out['a_entered'] = True

        runner.request_cancel(UID, run_id)  # 用户取消（只置标记）
        if retry:
            # 重试路径（post_run_resume 同构）：置回 queued + attempt 递增 + 清取消标记。
            # update_run 传 attempt 会同步轮换 lease，旧 A 的执行权就此作废。
            with sto.write_tx() as tx:
                tx.run(lambda conn: store.update_run(conn, run_id, UID, state='queued',
                                                     attempt=2, cancel_requested=False,
                                                     error=''))
            out['b'] = runner.submit(UID, run_id, job)
            if not script.entered(1).wait(WAIT_SECONDS):
                raise AssertionError('新 worker B 未进入模型调用')
            out['b_entered'] = True
            script.release(1)  # B 的模型响应先返回
            out['b'].join(JOIN_SECONDS)
            out['b_alive'] = out['b'].is_alive()
            out['row_after_b'] = read_run(run_id)

        script.release(0)  # 释放 A：旧响应这才返回
        out['a'].join(JOIN_SECONDS)
        out['a_alive'] = out['a'].is_alive()
        out['candidates_after'] = read_candidates(task_id, batch_id)
        out['messages_after'] = read_messages(task_id)
        out['row_final'] = read_run(run_id)
    finally:
        script.release_all()
        for key in ('a', 'b'):
            thread = out.get(key)
            if isinstance(thread, threading.Thread) and thread.is_alive():
                thread.join(JOIN_SECONDS)
        setattr(llm, fn_name, original)
    return out


# --- 五个场景的断言 -------------------------------------------------------------------

def scenario_generate_success_then_release():
    """场景 1：generate / B 已成功后释放 A → 候选恰好 1 条（B 的新结果），run 整行未被 A 改动。"""
    out = run_flow('generate', retry=True)
    tag = '场景1 generate/B已成功后释放A'
    row_b, row_final = out.get('row_after_b'), out.get('row_final')
    candidates = out.get('candidates_after') or []
    check(out.get('a_entered') and out.get('b_entered'),
          '%s：A、B 先后阻塞在模型调用（第 1 次调用=旧响应，第 2 次调用=新响应）' % tag)
    check(out.get('b_alive') is False and (row_b or {}).get('state') == 'succeeded',
          '%s：B 先成功完成（释放 A 之前 run 已 succeeded）' % tag,
          actual={'b_alive': out.get('b_alive'), 'state': (row_b or {}).get('state')})
    check(out.get('a_alive') is False,
          '%s：释放 A 后 A 线程退出（Cancelled 被 wrapper 收敛）' % tag,
          actual=out.get('a_alive'))
    check(len(candidates) == 1,
          '%s：批次内候选恰好 1 条（A 的旧候选晚写入被拒，不出现新旧并存）' % tag,
          expected=1, actual=len(candidates))
    check(bool(candidates) and candidates[0].get('name') == NEW_NAME,
          '%s：唯一候选是 B 写入的新结果' % tag,
          expected=NEW_NAME, actual=[item.get('name') for item in candidates])
    check((row_final or {}).get('state') == 'succeeded',
          '%s：run 终态仍是 succeeded（A 的 cancelled 终态写回被 lease 条件丢弃）' % tag,
          expected='succeeded', actual=(row_final or {}).get('state'))
    changed = sorted(key for key in (row_b or {})
                     if row_final is None or row_b[key] != row_final.get(key))
    check(row_b is not None and row_final is not None and not changed,
          '%s：B 写入的 progress/checkpoint/usage/stage 等整行未被 A 改动' % tag,
          expected='与 B 完成时逐字段一致',
          actual={'changed': changed} if changed else '一致')


def scenario_generate_release_while_b_running():
    """场景 2：generate / 两个 worker 都阻塞，先释放 A → A 被拒（0 条），再释放 B → 恰好 1 条。"""
    task_id, batch_id, run_id = seed_task_run('generate')
    script = ModelScript('generate')
    original = llm.extract_candidates
    llm.extract_candidates = script
    out = {'task_id': task_id}

    def job(user, run):
        pipeline.run_generate(user, task_id, run, batch_id, PROVIDER)

    try:
        out['a'] = runner.submit(UID, run_id, job)
        if not script.entered(0).wait(WAIT_SECONDS):
            raise AssertionError('旧 worker A 未进入模型调用')
        runner.request_cancel(UID, run_id)
        with sto.write_tx() as tx:
            tx.run(lambda conn: store.update_run(conn, run_id, UID, state='queued',
                                                 attempt=2, cancel_requested=False, error=''))
        out['b'] = runner.submit(UID, run_id, job)
        if not script.entered(1).wait(WAIT_SECONDS):
            raise AssertionError('新 worker B 未进入模型调用')

        script.release(0)  # 先释放 A（B 仍在模型调用中阻塞）
        out['a'].join(JOIN_SECONDS)
        out['a_alive'] = out['a'].is_alive()
        out['candidates_after_a'] = read_candidates(task_id, batch_id)
        out['row_after_a'] = read_run(run_id)

        script.release(1)  # 再释放 B
        out['b'].join(JOIN_SECONDS)
        out['b_alive'] = out['b'].is_alive()
        out['candidates_final'] = read_candidates(task_id, batch_id)
        out['row_final'] = read_run(run_id)
    finally:
        script.release_all()
        for key in ('a', 'b'):
            thread = out.get(key)
            if isinstance(thread, threading.Thread) and thread.is_alive():
                thread.join(JOIN_SECONDS)
        llm.extract_candidates = original

    tag = '场景2 generate/B仍在执行时释放A'
    check(out.get('a_alive') is False,
          '%s：先释放 A 后 A 线程退出' % tag, actual=out.get('a_alive'))
    check(not out.get('candidates_after_a'),
          '%s：B 完成前库里 0 条候选（A 的内容写被同事务核对拒绝）' % tag,
          expected=0, actual=len(out.get('candidates_after_a') or []))
    check((out.get('row_after_a') or {}).get('state') == 'running',
          '%s：A 退出后 run 仍是 B 的 running（A 的 cancelled 终态写回被丢弃）' % tag,
          expected='running', actual=(out.get('row_after_a') or {}).get('state'))
    final = out.get('candidates_final') or []
    check(len(final) == 1 and final[0].get('name') == NEW_NAME,
          '%s：再释放 B 后恰好 1 条候选且是 B 的新结果' % tag,
          expected=[NEW_NAME], actual=[item.get('name') for item in final])
    check((out.get('row_final') or {}).get('state') == 'succeeded',
          '%s：最终 state=succeeded' % tag,
          expected='succeeded', actual=(out.get('row_final') or {}).get('state'))


def scenario_generate_cancel_only():
    """场景 3：generate / 仅取消（无重试无 B）→ 0 条候选，run 最终 cancelled。"""
    out = run_flow('generate', retry=False)
    tag = '场景3 generate/仅取消无重试'
    check(out.get('a_alive') is False,
          '%s：取消后释放 A，A 线程退出' % tag, actual=out.get('a_alive'))
    check(not out.get('candidates_after'),
          '%s：0 条候选（取消标记在同一写事务内拦下旧响应的内容写）' % tag,
          expected=0, actual=len(out.get('candidates_after') or []))
    row = out.get('row_final') or {}
    check(row.get('state') == 'cancelled' and row.get('error') == '已取消',
          '%s：run 最终 cancelled（A 的晚终态写不覆盖也不改写取消结论）' % tag,
          expected={'state': 'cancelled', 'error': '已取消'},
          actual={'state': row.get('state'), 'error': row.get('error')})


def scenario_dialog_success_then_release():
    """场景 4：dialog / B 已成功后释放 A → 消息流只有 B 的一条助手消息（没有 A 的 seq2）。"""
    out = run_flow('dialog', retry=True)
    tag = '场景4 dialog/B已成功后释放A'
    messages = out.get('messages_after') or []
    assistants = [item for item in messages if item.get('role') == 'assistant']
    row_final = out.get('row_final') or {}
    check(out.get('a_entered') and out.get('b_entered') and out.get('b_alive') is False,
          '%s：A、B 先后阻塞在模型调用，B 先成功完成' % tag)
    check(len(assistants) == 1,
          '%s：助手消息恰好 1 条（A 的旧回答没有追加成 seq2）' % tag,
          expected=1, actual=len(assistants))
    check(bool(assistants) and NEW_NAME in assistants[0].get('content', '')
          and OLD_NAME not in assistants[0].get('content', ''),
          '%s：唯一助手消息是 B 的新回答' % tag,
          expected='内容含「%s」' % NEW_NAME,
          actual=[item.get('content', '')[:80] for item in assistants])
    check(bool(assistants) and assistants[0].get('seq') == 1,
          '%s：消息流 seq 连续（seq1 即 B 的回答，其后无追加）' % tag,
          expected=1, actual=[item.get('seq') for item in assistants])
    check(row_final.get('state') == 'succeeded',
          '%s：run 终态 succeeded（A 的晚终态写回被丢弃）' % tag,
          expected='succeeded', actual=row_final.get('state'))


def scenario_dialog_cancel_only():
    """场景 5：dialog / 仅取消 → 释放 A 后不新增助手消息。"""
    out = run_flow('dialog', retry=False)
    tag = '场景5 dialog/仅取消'
    assistants = [item for item in (out.get('messages_after') or [])
                  if item.get('role') == 'assistant']
    check(out.get('a_alive') is False,
          '%s：取消后释放 A，A 线程退出' % tag, actual=out.get('a_alive'))
    check(not assistants,
          '%s：不新增助手消息（旧回答的内容写被同事务核对拒绝）' % tag,
          expected=0, actual=len(assistants))
    check((out.get('row_final') or {}).get('state') == 'cancelled',
          '%s：run 最终 cancelled' % tag,
          expected='cancelled', actual=(out.get('row_final') or {}).get('state'))


# --- 入口 -----------------------------------------------------------------------------

SCENARIOS = [
    ('generate_success_then_release', scenario_generate_success_then_release),
    ('generate_release_while_b_running', scenario_generate_release_while_b_running),
    ('generate_cancel_only', scenario_generate_cancel_only),
    ('dialog_success_then_release', scenario_dialog_success_then_release),
    ('dialog_cancel_only', scenario_dialog_cancel_only),
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
