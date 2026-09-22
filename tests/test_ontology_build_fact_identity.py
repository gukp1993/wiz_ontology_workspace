# -*- coding: utf-8 -*-
"""事实身份去重回归（R3 修复：不同主体/字段的相同取值不再误判为重复副本）。

缺陷（已复现）：snippet_digest 只哈希 snippet，pipeline 全池去重把
battery.voltage=220（a.json）与 motor.power=220（b.json）判为同一片段，
第二条永久不进模型；且 duplicates 对外报告借用 alignment 组内口径，与
全池实际剔除量不一致（实际剔 1 报 0）。

修复后语义：
* 事实身份 = snippet + kind + locator 稳定字段 + data 语义键（field/path/value），
  由 retrieval.snippet_digest 单一实现（alignment / pipeline 共用同一入口）；
* 真副本（同文件同字段同值）哈希仍相同、照旧判重——去重的本来目的不变；
* pipeline 全池去重自计 duplicates_n（实际剔除数），并把 {被剔id: 保留id} 记入
  checkpoint plan.duplicateOf（重复文件副本只降佐证计数、不破坏来源追溯）。

覆盖：
1. 单元（纯 retrieval，无存储）：异文件同值身份不同 / 真副本身份相同 / kind 与
   data.value 参与身份 / locator 缺失兜底（不炸、按 snippet+kind 判）/ 空事实稳定。
2. 管线（隔离临时根 + mock 模型，真实跑 run_generate）：异文件同值两条都进模型；
   同文件同字段副本被剔且 plan.duplicateOf 映射正确；duplicates 报告数 = 实际剔除数；
   align notes 如实报告剔除数。
3. 规模冒烟：retrieve_fixture 合成夹具 5 万条单趟哈希 < 5 秒（27.6 万实测规模的
   O(n) 代理口径，不引入逐对比较）。

隔离（AGENTS.md 测试隔离铁律）：管线场景独立临时根（WIZ_WORKBENCH_ROOT +
该根下 WIZ_DATABASE_URL），只写临时目录；替换 llm.extract_candidates 控制模型
响应（不访问网络、不连真实库、不碰真实 ontology/）；结束销毁全部临时根。

运行：python3 tests/test_ontology_build_fact_identity.py
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path

os.environ.setdefault('WIZ_BUILD_LLM_CONCURRENCY', '1')   # 串行抽取：批次序确定（import 前设置）

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / 'tests'))

from workbench import storage  # noqa: E402
from workbench.ontology_build import alignment, llm, pipeline, retrieval, runner  # noqa: E402
from workbench.storage import engine as sto  # noqa: E402
from workbench.storage import ontology_build as store  # noqa: E402

UID = 'fact-identity-owner'
JOIN_SECONDS = 60
PROVIDER = {'endpoint': 'mock', 'model': 'mock'}
USAGE = {'calls': 1, 'promptBytes': 16, 'completionBytes': 8, 'durationMs': 1}

PASSED = []
FAILED = []
SEQ = [0]
ROOTS = []


def check(cond, message, actual=None, expected=None):
    SEQ[0] += 1
    if cond:
        PASSED.append(message)
        print('通过 %d) %s' % (SEQ[0], message))
        return True
    FAILED.append(message)
    print('[失败] %d) %s' % (SEQ[0], message))
    if expected is not None:
        print('  预期: %r' % (expected,))
    if actual is not None:
        print('  实际: %r' % (actual,))
    return False


# --- 单元场景：事实身份（retrieval.snippet_digest 单一实现入口） -----------------------

FACT_A = {'id': 'bf-a', 'kind': 'jsonLeaf', 'module': 'json',
          'locator': {'kind': 'json', 'file': 'a.json', 'path': '$.battery.voltage'},
          'snippet': '220', 'data': {'field': 'battery.voltage', 'value': '220'},
          'quality': 'high'}
FACT_B = {'id': 'bf-b', 'kind': 'jsonLeaf', 'module': 'json',
          'locator': {'kind': 'json', 'file': 'b.json', 'path': '$.motor.power'},
          'snippet': '220', 'data': {'field': 'motor.power', 'value': '220'},
          'quality': 'high'}
COPY_OF_A = dict(FACT_A, id='bf-c')   # 真副本：同文件同字段同值，仅 id 不同


def scenario_identity_unit():
    tag = '单元 事实身份语义'
    digest_a = retrieval.snippet_digest(FACT_A)
    digest_b = retrieval.snippet_digest(FACT_B)

    check(digest_a != digest_b,
          '%s：异文件同值（battery.voltage=220 / motor.power=220）身份不同，不再互判重复' % tag,
          actual=[digest_a[:16], digest_b[:16]])
    check(retrieval.snippet_digest(COPY_OF_A) == digest_a,
          '%s：真副本（同文件同字段同值）身份相同，照旧判重' % tag)

    other_value = dict(FACT_A, data={'field': 'battery.voltage', 'value': '221'})
    check(retrieval.snippet_digest(other_value) != digest_a,
          '%s：同位置不同取值（data.value）身份不同' % tag)
    other_kind = dict(FACT_A, kind='table')
    check(retrieval.snippet_digest(other_kind) != digest_a,
          '%s：kind 参与身份（同片段不同 kind 不判重）' % tag)
    no_semantic = dict(FACT_A, data={'type': 'string', 'chars': 3})
    check(retrieval.snippet_digest(no_semantic) == retrieval.snippet_digest(
        dict(FACT_A, data={'type': 'string'})),
        '%s：data 无 field/path/value 语义键时不参与身份（只取稳定标识字段）' % tag)

    # locator 缺失兜底：不炸、身份退化为 snippet+kind
    bare = {'id': 'bf-x', 'kind': 'table', 'snippet': '设备台账'}
    check(retrieval.snippet_digest(bare) == retrieval.snippet_digest(dict(bare)),
          '%s：locator 缺失时哈希稳定可判重（同 snippet+kind 同身份）' % tag)
    check(retrieval.snippet_digest(dict(bare, kind='code')) != retrieval.snippet_digest(bare),
          '%s：locator 缺失时 kind 不同则身份不同' % tag)
    check(retrieval.snippet_digest({}) == retrieval.snippet_digest(None)
          and len(retrieval.snippet_digest(None)) == 40,
          '%s：空事实/None 不炸，返回稳定 sha1' % tag)

    # 对齐分组沿用同一入口：真副本在组内照旧判重，异文件同值不算重复
    grouped = alignment.group_evidence([FACT_A, COPY_OF_A], ['bf-a', 'bf-c'])
    check(grouped['stats']['duplicates'] == 1
          and grouped['duplicates'].get('bf-c') == 'bf-a',
          '%s：alignment 组内对真副本照旧判重（bf-c → bf-a，同一实现入口）' % tag,
          actual=grouped['stats'])
    grouped_ab = alignment.group_evidence([FACT_A, FACT_B], ['bf-a', 'bf-b'])
    check(grouped_ab['stats']['duplicates'] == 0,
          '%s：alignment 对异文件同值不再判重（与全池口径一致）' % tag,
          actual=grouped_ab['stats'])


# --- 管线场景：全池去重 + duplicateOf 溯源 + 统计一致 -----------------------------------

def new_isolated_root(tag):
    """新临时根 + 该根下的 SQLite；重置引擎后惰性初始化（绝不碰真实 ontology/）。"""
    root = Path(tempfile.mkdtemp(prefix='wiz_fact_identity_%s_' % tag))
    ROOTS.append(root)
    os.environ['WIZ_WORKBENCH_ROOT'] = str(root)
    os.environ['WIZ_DATABASE_URL'] = 'sqlite:///' + str(root / 'data' / 'workbench.sqlite3')
    sto.reset_engine()
    storage.ensure_ready()
    if str(root) not in sto.resolve_url():
        raise AssertionError('隔离根未生效：%s' % sto.resolve_url())
    return root


def seed_pool_facts():
    """建任务 + 批次 + 范围 + 3 条事实 + generate run（真实存储层短写事务）。

    事实：bf-a（a.json battery.voltage=220）、bf-b（b.json motor.power=220）、
    bf-c（bf-a 的真副本）。范围 include=220 使三条全部 relevant、按输入顺序进池。
    """

    def body(conn):
        task_id = store.create_task(conn, UID, '事实身份去重回归')
        batch_id = store.create_batch(conn, task_id, UID, '', {})
        store.put_scope(conn, task_id, UID,
                        {'goal': '取值核对', 'include': '220', 'exclude': '', 'relations': '',
                         'coverage': '', 'openQuestions': []}, confirmed=True)
        facts = [FACT_A, FACT_B, COPY_OF_A]
        store.replace_material_facts(conn, task_id, UID, 'm', facts)
        run_id, _lease = store.create_run(conn, task_id, UID, 'generate', {}, batch_id)
        return task_id, batch_id, run_id

    with sto.write_tx() as tx:
        return tx.run(body)


def read_view(run_id):
    with sto.read_connection() as conn:
        row = store.get_run(conn, run_id, UID)
    return store.run_view(row) if row is not None else None


def read_raw_checkpoint(run_id):
    """持久化 checkpoint 原文（run_view 只透传摘要，plan.modelFactIds/duplicateOf
    明细按 V2-8 约定不在轮询视图里——这里直读库内 JSON 验证溯源字段确实落库）。"""
    with sto.read_connection() as conn:
        row = store.get_run(conn, run_id, UID)
    return json.loads(row['checkpoint_json'] or '{}') if row is not None else {}


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


def scenario_pool_dedup_pipeline():
    tag = '管线 全池去重/溯源/统计'
    task_id, batch_id, run_id = seed_pool_facts()
    seen_batches = []
    original = llm.extract_candidates

    def responder(_provider, _scope, batch, timeout=None):
        seen_batches.append([str(fact.get('id')) for fact in batch])
        return ok_candidate(batch)

    llm.extract_candidates = responder
    try:
        def job(user, run):
            pipeline.run_generate(user, task_id, run, batch_id, PROVIDER, resume_mode='auto')
        thread = runner.submit(UID, run_id, job)
        thread.join(JOIN_SECONDS)
        check(not thread.is_alive(), '%s：生成线程已退出' % tag)

        view = read_view(run_id)
        gen = ((view or {}).get('checkpoint') or {}).get('generate') or {}
        plan = ((read_raw_checkpoint(run_id) or {}).get('generate') or {}).get('plan') or {}
        check(view.get('state') == 'succeeded',
              '%s：生成成功收尾' % tag, actual=view.get('state'))
        check(gen.get('modelFacts') == 2 and gen.get('relevant') == 1
              and gen.get('related') == 2,
              '%s：轮询视图摘要一致（同一 snippet 哈希只计 1 条 relevant，副本留 related；'
              '池 3 条、剔除后发送 2 条）' % tag,
              expected={'modelFacts': 2, 'relevant': 1, 'related': 2},
              actual={'modelFacts': gen.get('modelFacts'), 'relevant': gen.get('relevant'),
                      'related': gen.get('related')})

        check(plan.get('modelFactIds') == ['bf-a', 'bf-b'],
              '%s：异文件同值两条都保留，真副本 bf-c 被剔（保持相关优先顺序）' % tag,
              expected=['bf-a', 'bf-b'], actual=plan.get('modelFactIds'))
        check(plan.get('duplicateOf') == {'bf-c': 'bf-a'},
              '%s：checkpoint plan.duplicateOf 记录 {被剔id: 保留id}（来源可追溯）' % tag,
              expected={'bf-c': 'bf-a'}, actual=plan.get('duplicateOf'))
        check(plan.get('duplicates') == 1,
              '%s：plan.duplicates = 全池实际剔除数 1（不再借用组内口径冒充）' % tag,
              expected=1, actual=plan.get('duplicates'))
        pool_n, kept_n = 3, len(plan.get('modelFactIds') or [])
        check(plan.get('duplicates') == pool_n - kept_n,
              '%s：统计数一致：duplicates(%s) = 池大小(%d) - 实际发送(%d)'
              % (tag, plan.get('duplicates'), pool_n, kept_n))

        notes = ' '.join(gen.get('notes') or [])
        check('证据分组' in notes and '全池剔除同身份重复副本 1 条' in notes,
              '%s：align notes 如实报告全池剔除数（含「证据分组」前缀，兼容既有透传断言）' % tag,
              actual=gen.get('notes'))
        check(seen_batches == [['bf-a', 'bf-b']],
              '%s：模型只见过保留事实（被剔副本不发送、不重复发送）' % tag,
              actual=seen_batches)
    finally:
        llm.extract_candidates = original
        for item in threading.enumerate():
            if item.name.startswith('build-run-'):
                item.join(JOIN_SECONDS)


# --- 规模冒烟：单趟哈希 O(n)（27.6 万实测规模的 5 万条代理） ----------------------------

def scenario_perf_smoke():
    tag = '规模 5万条单趟哈希'
    import retrieve_fixture
    count = 50000
    start = time.perf_counter()
    done = 0
    for fact in retrieve_fixture.iter_facts(count, retrieve_fixture.DEFAULT_SEED):
        retrieval.snippet_digest(fact)
        done += 1
    elapsed = time.perf_counter() - start
    check(done == count,
          '%s：%d 条夹具全部完成单趟哈希（纯逐条，无逐对比较）' % (tag, count),
          actual=done)
    check(elapsed < 5.0,
          '%s：单趟哈希耗时 %.2fs < 5s（O(n) 红线；27.6 万按此速率量级可行）' % (tag, elapsed),
          expected='< 5s', actual='%.2fs' % elapsed)


# --- 入口 -----------------------------------------------------------------------------

SCENARIOS = [
    ('identity_unit', scenario_identity_unit),
    ('pool_dedup_pipeline', scenario_pool_dedup_pipeline),
    ('perf_smoke', scenario_perf_smoke),
]


def main():
    for tag, fn in SCENARIOS:
        print('\n----- %s -----' % tag)
        if fn is scenario_pool_dedup_pipeline:
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
