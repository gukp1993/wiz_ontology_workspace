# -*- coding: utf-8 -*-
"""D14 回归：ontology_token_pilot CLI、模型适配与 A/B/C/D 四臂运行。

需求：《本体生成控输出_整合方案与并行开发计划_v3.md》§8 D14 行、§11（独立试验与共享
核心）、§12（G2 判定）。全部 fake、tempfile 隔离目录，不启服务、不碰真实数据。

覆盖场景（任务书 7 项）：
1.  fake 模式四臂各跑 1 次 simple 样本（子进程真实 CLI 入口）→ 退出 0、report.json
    生成、每臂 results 文件齐、报告与结果脱敏无 key（D13 scan_report 零发现）。
2.  共享核心验证：C/D 结果 meta.planId 存在且 schemaVersion=2；A/B meta.adapterDiffs
    登记「固定20批 planner」；AST 断言 adapter 无第二套调度器（不定义状态机/事务/
    持久化方法，不 import sqlite3，只调用 batch_execution/batch_state）。
3.  注入截断脚本（length）→ job split 发生、meta 记录 split 次数、usage 含父+子。
4.  campaign 预算 maxAttempts=2 跑 2 臂 → 第二臂受阻、退出码 2、报告注明；
    续跑同 output-dir 累计不重置。
5.  --repeats 2 → 结果文件按 repeat 区分；暖缓存生效（第二次 repeat 全部 cache 命中、
    零物理调用）。
6.  compact 臂（B/D）候选带 coverage 且通过对应 codec decode；金样评价跑通不抛。
7.  CLI 参数错误（无 config / 配置不存在 / 非法臂名）→ 退出码 1。

运行：python3 tests/run.py --test tests/test_ontology_token_pilot_cli.py
"""
import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from experiments.ontology_token_pilot import adapter  # noqa: E402
from experiments.ontology_token_pilot import evaluate  # noqa: E402
from experiments.ontology_token_pilot import state as exp_state  # noqa: E402
from workbench.ontology_build import output_codec  # noqa: E402
from workbench.ontology_build import semantic_units  # noqa: E402

GOLDEN_DIR = REPO / 'tests' / 'fixtures' / 'ontology_token_pilot' / 'golden'
PASSED = []
FAILED = []
SCENARIOS = []
ROOTS = []


def _scenario(name):
    def wrap(fn):
        def run():
            root = tempfile.mkdtemp(prefix='pilot-cli-')
            ROOTS.append(root)
            try:
                fn(root)
                PASSED.append(name)
            except Exception:
                FAILED.append(name)
                print('[失败] %s' % name)
                traceback.print_exc()
        SCENARIOS.append(run)
        return run
    return wrap


def _write_config(root, name='config.json', campaign='pilot-test', **overrides):
    config = {
        'providerId': 'fake-prov',
        'campaignId': campaign,
        'profile': {'contextTokens': 131072, 'outputLimitTokens': 32000,
                    'requestOutputTokens': 32000, 'targetRatio': 0.5},
        'scope': {'goal': '合成样本候选抽取（fake 冒烟）'},
        'sampleIds': ['simple', 'amplified', 'schema_conflict'],
        'goldenDir': str(GOLDEN_DIR),
    }
    config.update(overrides)
    path = os.path.join(root, name)
    with open(path, 'w', encoding='utf-8') as handle:
        json.dump(config, handle, ensure_ascii=False)
    return path


def _run_cli(config_path, output_dir, *extra):
    """子进程真实入口（cwd=仓库根，python3 -m experiments.ontology_token_pilot）。"""
    proc = subprocess.run(
        [sys.executable, '-m', 'experiments.ontology_token_pilot',
         '--config', config_path, '--output-dir', output_dir] + list(extra),
        cwd=str(REPO), capture_output=True, text=True, timeout=300)
    return proc


def _load_json(path):
    with open(path, 'r', encoding='utf-8') as handle:
        return json.load(handle)


def _result_path(output_dir, campaign, sample, arm, repeat):
    return os.path.join(output_dir, 'results', campaign,
                        '%s-%s-r%d.json' % (sample, arm, repeat))


def _assert_clean_redaction(report, results):
    assert not evaluate.scan_report(report), '报告脱敏扫描发现疑似泄漏：%s' % (
        evaluate.scan_report(report),)
    for result in results:
        assert not evaluate.scan_report(result), '结果脱敏扫描发现疑似泄漏'
    for text in (json.dumps(report, ensure_ascii=False),
                 json.dumps(results, ensure_ascii=False)):
        assert 'sk-' not in text and 'Bearer ' not in text and 'api_key' not in text, \
            '报告/结果含密钥样式串'


@_scenario('fake 四臂 simple 各一次（子进程 CLI）：退出 0、报告与结果齐全且脱敏无 key')
def _(root):
    config_path = _write_config(root)
    out = os.path.join(root, 'out')
    proc = _run_cli(config_path, out, '--mode', 'fake',
                    '--arms', 'A,B,C,D', '--samples', 'simple', '--repeats', '1')
    assert proc.returncode == 0, '退出码 %d\nstdout=%s\nstderr=%s' % (
        proc.returncode, proc.stdout[-2000:], proc.stderr[-2000:])
    report_path = os.path.join(out, 'report.json')
    assert os.path.isfile(report_path), 'report.json 未生成'
    assert os.path.isfile(os.path.join(out, 'report.md')), 'report.md 未生成'
    report = _load_json(report_path)
    results = []
    for arm in 'ABCD':
        path = _result_path(out, 'pilot-test', 'simple', arm, 1)
        assert os.path.isfile(path), '臂 %s 结果文件缺失：%s' % (arm, path)
        result = _load_json(path)
        results.append(result)
        assert result['runState'] == 'succeeded', (arm, result['runState'],
                                                   result.get('blocking'))
        assert result['arm'] == arm and result['sample'] == 'simple' and result['repeat'] == 1
    assert sorted(report.get('arms') or {}) == ['A', 'B', 'C', 'D']
    assert report.get('compare') is not None, 'A/B 齐全时应有 cost_compare'
    _assert_clean_redaction(report, results)


@_scenario('共享核心验证：C/D planId+schema2、A/B adapterDiffs 登记、adapter 无第二套调度器')
def _(root):
    config_path = _write_config(root)
    out = os.path.join(root, 'out')
    proc = _run_cli(config_path, out, '--mode', 'fake',
                    '--arms', 'A,B,C,D', '--samples', 'simple', '--repeats', '1')
    assert proc.returncode == 0, proc.stderr[-2000:]
    by_arm = {}
    for arm in 'ABCD':
        result = _load_json(_result_path(out, 'pilot-test', 'simple', arm, 1))
        by_arm[arm] = result
    for arm in 'CD':
        meta = by_arm[arm]['meta']
        assert by_arm[arm]['planId'], '%s 臂 planId 缺失' % arm
        assert meta['schemaVersion'] == 2, '%s 臂未走 schema2 计划：%r' % (arm, meta)
        assert meta['planner'] == 'v3-pack-next', meta
        assert meta['adapterDiffs'] == [], 'C/D 与核心无差异，不该登记 adapterDiffs'
    for arm in 'AB':
        meta = by_arm[arm]['meta']
        assert meta['planner'] == 'fixed-batch', meta
        assert any('固定20批' in diff for diff in meta['adapterDiffs']), \
            'A/B 未登记固定20批 planner 差异：%r' % meta['adapterDiffs']
        assert any('batch_execution 共享核心' in diff for diff in meta['adapterDiffs'])

    # 无第二套调度器：AST 检查 adapter —— 不定义状态机/事务/持久化/装箱调度方法，
    # 不 import sqlite3，且确实调用 batch_execution.run_plan / batch_state。
    source = (REPO / 'experiments' / 'ontology_token_pilot' / 'adapter.py').read_text(
        encoding='utf-8')
    tree = ast.parse(source)
    forbidden_defs = {'claim_job', 'save_plan', 'commit_success', 'commit_split',
                      'commit_failure', 'mark_interrupted_unknown', 'iterate_candidates',
                      'apply_event', 'create_plan', 'pack_next', 'plan_initial',
                      'split_or_block', 'step', 'run_plan', 'load', 'cache_put',
                      'cache_get', '_write_tx', 'make_context'}
    defined = set()
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            defined.add(node.name)
        elif isinstance(node, ast.Import):
            imported.update(alias.name.split('.')[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split('.')[0])
    clash = sorted(defined & forbidden_defs)
    assert not clash, 'adapter 定义了共享核心/持久化方法（第二套调度器嫌疑）：%s' % clash
    assert 'sqlite3' not in imported, 'adapter 不得直接触碰 SQLite（持久化归 D12）'
    assert 'batch_execution' in source and 'run_plan' in source, \
        'adapter 未走 batch_execution.run_plan 共享核心'
    assert 'batch_state' in source, 'adapter 未复用 batch_state'
    # FakeModel 'crash' 事件：模型层抛异常，call 包装层必须转 HTTP_UNKNOWN 不上抛
    # 执行器，且物理请求计数即时入账（campaign 预算口径）。
    model = adapter.FakeModel(script=['crash'])
    try:
        model([{'role': 'user', 'content': '{}'}], 1000)
        raise AssertionError('crash 事件应在模型层抛出 FakeModelCrash')
    except adapter.FakeModelCrash:
        pass
    wrap_state = exp_state.open_state(os.path.join(root, 'wrap.sqlite3'))
    try:
        stats = {'physicalCalls': 0, 'cacheHits': 0}
        counters = {'attempts': 0, 'knownCompletionTokens': 0, 'activeMs': 0}
        crashing = adapter.FakeModel(script=['crash'])   # 全新脚本：事件未被上面的直接调用消耗
        call = adapter._make_call_fn(crashing, 'legacy-v1', wrap_state, stats, counters)
        converted = call([{'role': 'user', 'content': '{}'}], 1000)
        assert converted['ok'] is False and converted['errorCode'] == 'HTTP_UNKNOWN', converted
        assert stats['physicalCalls'] == 1 and counters['attempts'] == 1, (stats, counters)
    finally:
        wrap_state.close()


@_scenario('注入截断脚本：job split 发生、meta 记录 split 次数、usage 含父+子')
def _(root):
    config_path = _write_config(root, campaign='trunc',
                                fakeScript=['length'])
    out = os.path.join(root, 'out')
    proc = _run_cli(config_path, out, '--mode', 'fake', '--arms', 'C',
                    '--samples', 'simple', '--repeats', '1')
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-2000:]
    result = _load_json(_result_path(out, 'trunc', 'simple', 'C', 1))
    assert result['runState'] == 'succeeded', result.get('blocking')
    assert result['events']['jobSplit'] == 1, '截断应触发一次 split：%r' % result['events']
    attempts = result['ledgerAttempts']
    assert len(attempts) >= 3, '父 + 至少两子的尝试应全部入账：%d' % len(attempts)
    truncated = [item for item in attempts if item.get('finishReason') == 'length']
    assert len(truncated) == 1, '恰好一次父截断：%r' % truncated
    usage = result['usage']
    assert usage['calls'] == len(attempts), 'usage.calls 与尝试数一致'
    assert usage['knownCompletionTokens'] > truncated[0]['usage']['completionTokens'], \
        'usage 应含父截断 + 子调用的累计'


@_scenario('campaign 预算 maxAttempts=2 跑 2 臂：第二臂受阻、退出 2、续跑累计不重置')
def _(root):
    config_path = _write_config(root, campaign='budget',
                                campaignBudget={'maxAttempts': 2,
                                                'maxCompletionTokens': 200000,
                                                'maxWallMs': 7200000})
    out = os.path.join(root, 'out')
    extra = ['--mode', 'fake', '--arms', 'A,B', '--samples', 'simple', '--repeats', '1']
    proc = _run_cli(config_path, out, *extra)
    assert proc.returncode == 2, '预算达线应退出 2：%d\n%s' % (proc.returncode, proc.stdout)
    first_a = _load_json(_result_path(out, 'budget', 'simple', 'A', 1))
    first_b = _load_json(_result_path(out, 'budget', 'simple', 'B', 1))
    assert first_a['runState'] == 'blocked', first_a['runState']
    assert first_b['runState'] == 'blocked', '第二臂应受阻：%s' % first_b['runState']
    assert first_b['blocking']['code'] == 'ATTEMPT_BUDGET_EXCEEDED', first_b['blocking']
    report = _load_json(os.path.join(out, 'report.json'))
    notes = json.dumps(report.get('notes') or [], ensure_ascii=False)
    assert 'ATTEMPT_BUDGET_EXCEEDED' in notes, '报告应注明受阻原因：%s' % notes

    def counters():
        state = exp_state.open_state(os.path.join(out, 'states', 'budget', 'campaign.sqlite3'))
        try:
            return adapter._campaign_counters(state)
        finally:
            state.close()
    after_first = counters()
    assert after_first['attempts'] == 2, 'maxAttempts=2 应恰好消费 2 次物理请求：%r' % after_first

    # 续跑同 output-dir：终态结果跳过、累计不重置
    proc2 = _run_cli(config_path, out, *extra)
    assert proc2.returncode == 2, '仍有受阻项，续跑退出码应保持 2'
    after_resume = counters()
    assert after_resume == after_first, 'campaign 累计被重置：%r -> %r' % (
        after_first, after_resume)
    second_a = _load_json(_result_path(out, 'budget', 'simple', 'A', 1))
    assert second_a['meta'].get('physicalCalls') == first_a['meta']['physicalCalls'], \
        '终态 run 不应被重跑派发'
    report2 = _load_json(os.path.join(out, 'report.json'))
    notes2 = json.dumps(report2.get('notes') or [], ensure_ascii=False)
    assert 'ATTEMPT_BUDGET_EXCEEDED' in notes2, '续跑报告仍应注明受阻项：%s' % notes2
    assert '续跑跳过' in notes2, '续跑报告应注明终态结果跳过：%s' % notes2


@_scenario('--repeats 2：结果按 repeat 区分；暖缓存跨 repeat 生效（第二次全命中）')
def _(root):
    config_path = _write_config(root, campaign='warm')
    out = os.path.join(root, 'out')
    proc = _run_cli(config_path, out, '--mode', 'fake', '--arms', 'A',
                    '--samples', 'simple', '--repeats', '2')
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-2000:]
    first = _load_json(_result_path(out, 'warm', 'simple', 'A', 1))
    second = _load_json(_result_path(out, 'warm', 'simple', 'A', 2))
    for result in (first, second):
        assert result['runState'] == 'succeeded', result.get('blocking')
    assert first['repeat'] == 1 and second['repeat'] == 2
    assert os.path.isfile(_result_path(out, 'warm', 'simple', 'A', 1))
    assert os.path.isfile(_result_path(out, 'warm', 'simple', 'A', 2))
    physical_first = first['meta']['physicalCalls']
    assert physical_first > 0 and first['meta']['cacheHits'] == 0, first['meta']
    assert second['meta']['physicalCalls'] == 0, \
        '第二次 repeat 应零物理调用：%r' % second['meta']
    assert second['meta']['cacheHits'] == physical_first, \
        '第二次 repeat 应全部命中缓存：%r vs %r' % (second['meta']['cacheHits'],
                                             physical_first)


@_scenario('compact 臂：候选带 coverage 且通过 decode；金样评价跑通不抛')
def _(root):
    config_path = _write_config(root, campaign='compact')
    out = os.path.join(root, 'out')
    proc = _run_cli(config_path, out, '--mode', 'fake', '--arms', 'B,D',
                    '--samples', 'simple', '--repeats', '1')
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-2000:]
    for arm in 'BD':
        result = _load_json(_result_path(out, 'compact', 'simple', arm, 1))
        assert result['runState'] == 'succeeded', (arm, result.get('blocking'))
        assert result['meta']['codec'] == 'compact-v1'
        # compact 通过 = coverage 全命中（缺任一单元整叶不提交）；
        # 金样评价已随 run_arm 执行且未抛（结构齐全、可 JSON 化）。
        assert result['quality'], '金样评价结果缺失'
        for item in result['quality']:
            json.dumps(item, ensure_ascii=False)
            assert 'conflictCoverage' in item and 'precision' in item
        json.dumps(result, ensure_ascii=False)
    # 直接验证 FakeModel compact 响应：coverage 逐单元命中 + decode ok。
    facts, facts_by_id = adapter.load_sample_facts(
        adapter.load_config(config_path), 'simple')
    targets = semantic_units.build_targets(facts)['targets'][:5]
    encoded = output_codec.encode_request(
        'compact-v1',
        [dict(target, content={'snippet': '示例', 'data': {'name': 'x', 'rated': 1}})
         for target in targets], [], {'goal': '直接验证'})
    response = adapter.FakeModel()(encoded['messages'], 1000)
    assert response['ok'] and response['finishReason'] == 'stop'
    data = json.loads(response['content'])
    assert data['codecVersion'] == 'compact-v1'
    covered = {entry['unit'] for entry in data['coverage']}
    assert covered == set(encoded['unitIds']), 'coverage 必须逐单元命中'
    decoded = output_codec.decode_response('compact-v1', response['content'],
                                           encoded['aliasMap'], encoded['unitIds'],
                                           finish_reason='stop')
    assert decoded['ok'] and decoded['candidates'], decoded['errors']


@_scenario('CLI 参数错误（无 config / 配置不存在 / 非法臂名 / 非法样本）→ 退出码 1')
def _(root):
    import importlib
    cli = importlib.import_module('experiments.ontology_token_pilot.__main__')
    out = os.path.join(root, 'out')
    assert cli.main(['--output-dir', out, '--mode', 'fake']) == 1, '缺 --config 应退出 1'
    assert cli.main(['--config', os.path.join(root, 'missing.json'),
                     '--output-dir', out]) == 1, '配置不存在应退出 1'
    config_path = _write_config(root)
    assert cli.main(['--config', config_path, '--output-dir', out,
                     '--arms', 'A,X']) == 1, '非法臂名应退出 1'
    assert cli.main(['--config', config_path, '--output-dir', out,
                     '--samples', 'nope']) == 1, '样本不在配置内应退出 1'
    assert cli.main(['--config', config_path, '--output-dir', out,
                     '--repeats', '0']) == 1, 'repeats<1 应退出 1'
    assert not os.path.exists(os.path.join(out, 'report.json')), \
        '参数错误不应产出报告'


def main():
    if not SCENARIOS:
        print('[错误] 未收集到场景')
        return 2
    for run in SCENARIOS:
        run()
    for root in ROOTS:
        shutil.rmtree(root, ignore_errors=True)
    del ROOTS[:]
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
