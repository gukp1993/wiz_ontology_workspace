"""R3-02 回归：人工排除决定跨批次持续继承（真实临时 SQLite 根，不启动服务）。

被验收报告第三轮点名的缺陷（43dddc8）：`pipeline._excluded_aligned_keys_previous` 只回看
「最近一个有候选的旧批次」，于是人工排除只保护一轮：

    B1 人工 exclude → B2 生成继承为 defer+origin.revived → 用户没在 B2 改决定
    → B3 只读 B2（defer，不是 exclude）→ B3 以 include 复活（报告实测 protection: 0）

本用例锁定修复后的语义（需求说明 §8.3 硬禁令「禁止再次生成自动复活已排除候选」）：
排除决定跨全部批次持续生效，直到用户在评审页对后来批次**显式**重新纳入
（decision=include 且 reviewed=true）。断言覆盖：

1. 连续多批（B1 exclude → B2 继承 → B3 仍保护 → B4 仍保护）；
2. 用户显式重新纳入后解除保护（B2 decide include/reviewed=true → B3 不再保护）；
3. 未经评审的 include（reviewed 非真）不解除保护；从未被排除的键与空 alignedKey 不受影响；
4. 写入落库：函数在生成写事务内被调用，库里的候选行确实是 defer + origin.revived=true；
5. 真实 `pipeline.run_generate` 调用点（本地假 LLM）：新批次候选行落库就是 defer+revived。

隔离（AGENTS.md 测试隔离铁律）：WIZ_WORKBENCH_ROOT 与 WIZ_DATABASE_URL 都指向本次运行新建的
临时目录，结束清理；不连真实业务库、不写 ontology/、不启动任何端口。

运行：.venv/bin/python tests/test_ontology_build_exclusion_inheritance.py
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

TMP = Path(tempfile.mkdtemp(prefix='wiz_exclusion_inheritance_'))
os.environ['WIZ_WORKBENCH_ROOT'] = str(TMP)
os.environ['WIZ_DATABASE_URL'] = 'sqlite:///' + str(TMP / 'data' / 'workbench.sqlite3')

from workbench import storage  # noqa: E402
from workbench.ontology_build import pipeline  # noqa: E402
from workbench.storage import engine as sto  # noqa: E402
from workbench.storage import ontology_build as store  # noqa: E402

OWNER = 'user-r3-02'
PASSED = []
FAILED = []
SEQ = [0]


def check(cond, message, actual=None, expected=None):
    SEQ[0] += 1
    if cond:
        PASSED.append(message)
        print('通过 %d) %s' % (SEQ[0], message))
        return True
    FAILED.append(message)
    print('[失败] %d) %s' % (SEQ[0], message))
    if expected is not None:
        print('  预期: %s' % json.dumps(expected, ensure_ascii=False, default=str))
    if actual is not None:
        print('  实际: %s' % json.dumps(actual, ensure_ascii=False, default=str))
    return False


# --- 真实临时库上的夹具 -----------------------------------------------------------

def new_task(name, tag):
    """建真实任务行；返回 (task_id, owner_id)。"""
    def body(conn):
        return store.create_task(conn, OWNER, '%s-%s' % (name, tag))
    with sto.write_tx() as tx:
        return tx.run(body), OWNER


def new_batch(task_id, seq, tag):
    """建真实批次行；created_at 显式递增，保证历史回放顺序确定（不依赖时钟精度）。"""
    created = '2026-09-20T00:00:%02d+00:00' % seq

    def body(conn):
        return store.create_batch(conn, task_id, OWNER, '', {'tag': tag}, now=created)
    with sto.write_tx() as tx:
        return tx.run(body)


def seed_candidate(task_id, batch_id, aligned_key, decision, reviewed=False, name='设备'):
    """直写真实候选行（人工决定的来源）；返回 candidate_id。"""
    def body(conn):
        return store.create_candidate(conn, task_id, OWNER, batch_id, {
            'type': 'object', 'key': 'obj-device', 'name': name, 'definition': '储能设备台账',
            'fields': {}, 'ownerKey': '', 'evidence': {}, 'evidenceStatus': 'supported',
            'decision': decision, 'alignedKey': aligned_key, 'reviewed': reviewed})
    with sto.write_tx() as tx:
        return tx.run(body)


def generate_items(aligned_key, name='设备'):
    """模拟一条新批次候选（内存结构；decision 为模型默认 include）。"""
    return {'type': 'object', 'key': 'obj-device', 'name': name, 'definition': '储能设备台账',
            'fields': {}, 'ownerKey': '', 'evidence': {}, 'evidenceStatus': 'supported',
            'decision': 'include', 'alignedKey': aligned_key}


def inherit_and_write(task_id, batch_id, items):
    """与 run_generate 的 _final_write 同构：同一个写事务内先继承人工决定再整批落库。"""
    def body(conn):
        protected = pipeline.inherit_manual_exclusions(conn, OWNER, task_id, batch_id, items)
        for item in items:
            store.create_candidate(conn, task_id, OWNER, batch_id, {
                'type': item['type'], 'key': item['key'], 'name': item['name'],
                'definition': item['definition'], 'fields': item['fields'],
                'ownerKey': item['ownerKey'], 'evidence': item['evidence'],
                'evidenceStatus': item['evidenceStatus'], 'decision': item['decision'],
                'alignedKey': item['alignedKey'], 'origin': item.get('origin') or {}})
        return protected
    with sto.write_tx() as tx:
        return tx.run(body)


def rows_of_batch(task_id, batch_id):
    """独立读连接回读批次候选行（证明落库，而不是只改了内存 dict）。"""
    with sto.read_connection() as conn:
        return store.all_candidates(conn, task_id, OWNER, batch_id=batch_id, include_merged=True)


def scan_exclusions(task_id, current_batch_id):
    """直接调用修复后的历史扫描（current_batch_id 是「即将生成」的批次，回放时跳过）。"""
    with sto.read_connection() as conn:
        return pipeline._manual_exclusion_keys(conn, OWNER, task_id, current_batch_id)


def decide_include(task_id, candidate_id, reviewed=True, reason='人工重新纳入'):
    """模拟评审页「显式重新纳入」：decision=include + reviewed=true（真实 UPDATE）。"""
    def body(conn):
        row = store.get_candidate(conn, candidate_id, OWNER)
        return store.update_candidate(conn, candidate_id, OWNER,
                                      expected_revision=row['revision'], decision='include',
                                      reviewed=reviewed, reason=reason)
    with sto.write_tx() as tx:
        return tx.run(body)


# --- 1/2. 连续批次与显式重新纳入 ---------------------------------------------------

def flow_multi_batch_protection():
    task_id, owner = new_task('排除继承', 'multi')
    b1 = new_batch(task_id, 1, 'b1')
    b2 = new_batch(task_id, 2, 'b2')
    b3 = new_batch(task_id, 3, 'b3')
    b4 = new_batch(task_id, 4, 'b4')
    key = 'object:设备'
    seed_candidate(task_id, b1, key, 'exclude', reviewed=True)

    item2 = generate_items(key)
    protected2 = inherit_and_write(task_id, b2, [item2])
    row2 = rows_of_batch(task_id, b2)[0]
    check(protected2 == 1 and item2['decision'] == 'defer'
          and (item2.get('origin') or {}).get('revived') is True,
          'B2 生成：人工排除被继承（保护数=1、defer、origin.revived=true）',
          actual=(protected2, item2['decision'], item2.get('origin')))
    check(row2['decision'] == 'defer' and (row2.get('origin') or {}).get('revived') is True,
          'B2 继承结果在写事务内真实落库（库里是 defer+revived，不是仅内存标记）',
          actual=(row2['decision'], row2.get('origin')))

    item3 = generate_items(key)
    protected3 = inherit_and_write(task_id, b3, [item3])
    row3 = rows_of_batch(task_id, b3)[0]
    check(protected3 == 1 and item3['decision'] == 'defer'
          and (item3.get('origin') or {}).get('revived') is True,
          'B3 再生成仍被保护（R3-02 反例：修复前 protection=0、decision=include）',
          actual=(protected3, item3['decision'], item3.get('origin')))
    check(row3['decision'] == 'defer' and (row3.get('origin') or {}).get('revived') is True,
          'B3 保护结果落库为 defer+revived', actual=(row3['decision'], row3.get('origin')))

    item4 = generate_items(key)
    protected4 = inherit_and_write(task_id, b4, [item4])
    check(protected4 == 1 and item4['decision'] == 'defer',
          'B4 继续被保护（跨批次持续生效，不是只多保护一轮）',
          actual=(protected4, item4['decision']))

    # 显式重新纳入：在 B2 的那条候选上由用户做 include（reviewed=true）
    rows2 = rows_of_batch(task_id, b2)
    decided = decide_include(task_id, rows2[0]['id'])
    check(decided['decision'] == 'include' and bool(decided['reviewed']) is True,
          '用户在 B2 候选上显式重新纳入（include + reviewed=true）',
          actual=(decided['decision'], decided['reviewed']))
    b5 = new_batch(task_id, 5, 'b5')
    item5 = generate_items(key)
    protected5 = inherit_and_write(task_id, b5, [item5])
    row5 = rows_of_batch(task_id, b5)[0]
    check(protected5 == 0 and item5['decision'] == 'include'
          and row5['decision'] == 'include',
          '显式重新纳入后解除保护：B5 生成不再强制 defer（保护数=0、decision=include）',
          actual=(protected5, item5['decision'], row5['decision']))

    # 未经评审的 include 不解除保护（模型自动 include，不是人的决定）
    t2, _ = new_task('排除继承', 'auto-include')
    c1 = new_batch(t2, 1, 'b1')
    c2 = new_batch(t2, 2, 'b2')
    c3 = new_batch(t2, 3, 'b3')
    seed_candidate(t2, c1, key, 'exclude', reviewed=True)
    seed_candidate(t2, c2, key, 'include', reviewed=False)   # 自动 include（未评审）
    item = generate_items(key)
    protected = inherit_and_write(t2, c3, [item])
    check(protected == 1 and item['decision'] == 'defer',
          '自动 include（reviewed=false）不解除保护，B 仍然 defer（保护数=1）',
          actual=(protected, item['decision']))


# --- 3. 无关键与空 alignedKey ------------------------------------------------------

def flow_unrelated_and_empty_keys():
    task_id, _ = new_task('排除继承', 'edges')
    b1 = new_batch(task_id, 1, 'b1')
    b2 = new_batch(task_id, 2, 'b2')
    seed_candidate(task_id, b1, 'object:设备', 'exclude', reviewed=True, name='设备')
    seed_candidate(task_id, b1, '', 'exclude', reviewed=True, name='无名候选')
    seed_candidate(task_id, b1, 'object:站点', 'include', reviewed=True, name='站点')

    never_excluded = generate_items('object:站点', name='站点')   # 从未被排除
    empty_key = generate_items('', name='无名候选')               # 空 alignedKey 不参与保护
    excluded = generate_items('object:设备', name='设备')
    protected = inherit_and_write(task_id, b2, [never_excluded, empty_key, excluded])
    check(protected == 1 and excluded['decision'] == 'defer'
          and (excluded.get('origin') or {}).get('revived') is True,
          '只有被排除的键受保护（保护数=1），其余两条不受影响',
          actual=(protected, excluded['decision']))
    check(never_excluded['decision'] == 'include' and empty_key['decision'] == 'include'
          and not (empty_key.get('origin') or {}).get('revived'),
          '从未被排除的键与空 alignedKey 不参与保护（decision 保持 include、无 revived）',
          actual=(never_excluded['decision'], empty_key['decision'], empty_key.get('origin')))

    def probe(current_batch):
        """以「即将生成」的批次为当前批做历史扫描（当前批被跳过）。"""
        return scan_exclusions(task_id, current_batch)
    check(probe(b2) == {'object:设备'},
          '历史扫描只返回最终仍被排除的键（空 alignedKey 与 include 键都不在内）',
          actual=sorted(probe(b2)), expected=['object:设备'])

    # 用户显式重新纳入后，下一个批次的历史扫描不再包含该键
    rows = rows_of_batch(task_id, b2)
    target = next(row for row in rows if row['alignedKey'] == 'object:设备')
    decide_include(task_id, target['id'])
    b3 = new_batch(task_id, 3, 'b3')
    check(probe(b3) == set(), '显式重新纳入后历史扫描不再返回该键（保护解除）',
          actual=sorted(probe(b3)))

    # 其他任务的历史不得串味（归属隔离）
    other_task, _ = new_task('排除继承', 'other-task')
    b_other = new_batch(other_task, 1, 'b1')
    seed_candidate(other_task, b_other, 'object:设备', 'exclude', reviewed=True)
    check(probe(b3) == set(), '另一任务的历史排除不影响本任务的保护集合',
          actual=sorted(probe(b3)))


# --- 4. 真实 run_generate 调用点（本地假 LLM，真实 SQLite） --------------------------

class FakeLlm(BaseHTTPRequestHandler):
    """候选抽取应答：返回一条与历史排除键同名的对象候选（name=设备 → object:设备）。"""

    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get('Content-Length') or 0)
        body = json.loads(self.rfile.read(length).decode() or '{}')
        messages = body.get('messages') or []
        user_text = str(messages[-1].get('content') or '') if messages else ''
        try:
            fact_ids = [str(item.get('id')) for item in (json.loads(user_text).get('facts') or [])
                        if isinstance(item, dict) and item.get('id')]
        except ValueError:
            fact_ids = []
        payload = {'candidates': [{
            'key': 'obj-device', 'type': 'object', 'name': '设备', 'definition': '储能设备台账',
            'fields': {}, 'ownerKey': '', 'evidence': {'_record': fact_ids[:1]},
            'evidenceStatus': 'supported', 'conflicts': []}]}
        data = json.dumps({'choices': [{'message': {'content': json.dumps(payload, ensure_ascii=False)},
                                        'finish_reason': 'stop'}]}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def start_fake_llm():
    server = ThreadingHTTPServer(('127.0.0.1', 0), FakeLlm)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def flow_run_generate_call_point():
    """真实 run_generate：新批次候选行落库即 defer+revived（证明调用点在写事务内）。"""
    server, thread = start_fake_llm()
    try:
        task_id, _ = new_task('排除继承', 'run-generate')
        b1 = new_batch(task_id, 1, 'b1')
        seed_candidate(task_id, b1, 'object:设备', 'exclude', reviewed=True)
        batch_id = new_batch(task_id, 2, 'b2')

        def seed_inputs(conn):
            store.put_scope(conn, task_id, OWNER, {
                'goal': '设备运行管理', 'include': '设备台账', 'exclude': '', 'relations': '',
                'coverage': '', 'openQuestions': []}, confirmed=True)
            store.replace_material_facts(conn, task_id, OWNER, 'material-1', [{
                'id': 'bf-r302-1', 'module': 'device', 'locator': {'kind': 'ddl', 'table': 'device'},
                'snippet': 'create table device (id bigint, name varchar) -- 设备台账',
                'kind': 'table', 'data': {'table': 'device'}, 'quality': 'high'}])
            return store.create_run(conn, task_id, OWNER, 'generate', {}, batch_id)[0]
        with sto.write_tx() as tx:
            run_id = tx.run(seed_inputs)

        provider = {'id': 'fake', 'name': '本地假 LLM', 'endpoint': 'http://127.0.0.1:%d/v1/chat/completions'
                                                                    % server.server_address[1],
                    'model': 'fake-model', 'api_key': 'k', 'timeout': 10}
        summary = pipeline.run_generate(OWNER, task_id, run_id, batch_id, provider)
        rows = rows_of_batch(task_id, batch_id)
        check(summary['state'] == 'succeeded' and len(rows) == 1 and rows[0]['alignedKey'] == 'object:设备',
              '真实 run_generate 生成新批次候选（object:设备）', actual=(summary['state'], rows))
        check(rows[0]['decision'] == 'defer' and (rows[0].get('origin') or {}).get('revived') is True,
              'run_generate 调用点：库里的新候选行是 defer + revived（写事务内生效）',
              actual=(rows[0]['decision'], rows[0].get('origin')))
        check(any('历史批次人工排除的 1 个候选' in note for note in summary['notes']),
              '生成摘要给出跨批次保护的说明（notes 含保护条数）', actual=summary['notes'])
        with sto.read_connection() as conn:
            run_row = store.run_view(store.get_run(conn, run_id, OWNER))
        # 运行终态由 runner._finish 写入；这里直调管线，只能断言阶段已推进到 adapt、
        # 且没有把运行改写为别的阶段（不把「直调」误当作完整 runner 链路）。
        check(run_row['stage'] == 'adapt' and run_row['state'] in ('queued', 'running'),
              '生成运行推进到 adapt 阶段（真实 runner.stage 写入路径未被破坏）',
              actual=(run_row['state'], run_row['stage']))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def main():
    storage.ensure_ready()
    print('临时数据根：%s' % TMP)
    for name, fn in (('连续批次保护与显式重新纳入', flow_multi_batch_protection),
                     ('无关键与空 alignedKey', flow_unrelated_and_empty_keys),
                     ('run_generate 真实调用点', flow_run_generate_call_point)):
        print('\n--- %s ---' % name)
        fn()
    print('\n统计：%d 项通过，%d 项失败' % (len(PASSED), len(FAILED)))
    for message in FAILED:
        print('  失败: %s' % message)
    sto.reset_engine()
    shutil.rmtree(TMP, ignore_errors=True)
    return 1 if FAILED else 0


if __name__ == '__main__':
    sys.exit(main())
