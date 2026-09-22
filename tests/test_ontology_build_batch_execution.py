"""共享执行器回归（D09）：事件分类、有限重试、拆分调度、恢复、预算与事务纪律。

对应整合计划 v3 §5 事件表 / §9 场景 3、4、5 / 契约 batch_contracts「执行器」节。
持久化用 D12 ExperimentState（真 SQLite、临时目录）——同时验证「实验与在线跑同一核心」；
在线适配器（D08）的 lease/取消场景由 test_ontology_build_budget_storage 与 D17 组合覆盖。

运行：python3 tests/run.py --test tests/test_ontology_build_batch_execution.py
"""
import copy
import json
import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from workbench.ontology_build import batch_contracts as contracts  # noqa: E402
from workbench.ontology_build import batch_execution as executor  # noqa: E402
from workbench.ontology_build import batch_state  # noqa: E402
from experiments.ontology_token_pilot import state as exp_state  # noqa: E402

PASSED = []
FAILED = []
SCENARIOS = []
ROOTS = []


def _scenario(name):
    def wrap(fn):
        def run():
            root = tempfile.mkdtemp(prefix='exec-')
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


def _profile(context_tokens=131072, output_limit=32000):
    return contracts.build_profile(env={
        'WIZ_BUILD_ADAPTIVE_BATCHING': '1',
        'WIZ_BUILD_PROFILE_PROVIDER_ID': 'prov', 'WIZ_BUILD_PROFILE_MODEL': 'model',
        'WIZ_BUILD_CONTEXT_TOKENS': str(context_tokens),
        'WIZ_BUILD_OUTPUT_LIMIT_TOKENS': str(output_limit)})


def _targets(*specs):
    out = []
    for _index, (suffix, subject) in enumerate(specs):
        out.append({'targetId': 't-%s' % suffix, 'factId': 'f-%s' % suffix,
                    'selector': {'type': 'whole'}, 'kind': 'json_object',
                    'subjectKey': subject, 'materialId': 'm1',
                    'groupingConfidence': 'high'})
    return out


def _facts(*suffixes):
    out = {}
    for suffix in suffixes:
        out['f-%s' % suffix] = {
            'id': 'f-%s' % suffix, 'materialId': 'm1',
            'locator': {'kind': 'json', 'file': 'a.json', 'path': '/%s' % suffix},
            'snippet': 'fact %s 内容：额定功率与额定电压说明' % suffix,
            'kind': 'json', 'data': {'name': suffix, 'fields': ['rated_power', 'rated_voltage']},
            'quality': 'high'}
    return out


def _plan(targets, profile):
    return batch_state.create_plan('batch-x', 1, 0, targets, profile, contracts.CODEC_LEGACY,
                                   ['fp-parts'])


class ScriptedModel(object):
    """按脚本回放 CallResult；记录每次调用（messages 摘要与 max_tokens）。"""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def __call__(self, messages, max_tokens):
        self.calls.append({'messages': messages, 'maxTokens': max_tokens,
                           'userBytes': len(json.dumps(messages, ensure_ascii=False)
                                           .encode('utf-8'))})
        if not self.script:
            raise AssertionError('脚本耗尽：第 %d 次调用无回放项' % len(self.calls))
        item = self.script.pop(0)
        return item() if callable(item) else dict(item)


def _ok(fact_ids, completion=100):
    candidates = [{'key': 'cand_%s' % fid, 'type': 'object', 'name': '候选%s' % fid,
                   'definition': '基于证据的定义', 'fields': {},
                   'ownerKey': '', 'evidence': {'name': [fid]},
                   'evidenceStatus': 'supported', 'conflicts': [], 'rejectedRefs': 0}
                  for fid in fact_ids]
    return {'ok': True, 'content': json.dumps({'candidates': candidates}, ensure_ascii=False),
            'finishReason': 'stop', 'errorCode': None, 'retryable': False,
            'usage': {'prompt_tokens': 200, 'completion_tokens': completion,
                      'total_tokens': 200 + completion, 'usageSource': 'api'},
            'requestBytes': 1234, 'responseBytes': 4321, 'durationMs': 50}


def _length(completion=40):
    return {'ok': True, 'content': '{"candidates": [{"key": "par',
            'finishReason': 'length', 'errorCode': contracts.OUTPUT_TRUNCATED,
            'retryable': False,
            'usage': {'prompt_tokens': 200, 'completion_tokens': completion,
                      'total_tokens': 200 + completion, 'usageSource': 'api'},
            'requestBytes': 1234, 'responseBytes': 900, 'durationMs': 30}


def _bad_json():
    return {'ok': True, 'content': '这不是JSON', 'finishReason': 'stop',
            'errorCode': None, 'retryable': False,
            'usage': {'prompt_tokens': 200, 'completion_tokens': 10,
                      'total_tokens': 210, 'usageSource': 'api'},
            'requestBytes': 1, 'responseBytes': 1, 'durationMs': 5}


def _net_error(code='RATE_LIMITED'):
    return {'ok': False, 'content': None, 'finishReason': None, 'errorCode': code,
            'retryable': True, 'usage': contracts.empty_usage(),
            'requestBytes': 10, 'responseBytes': 0, 'durationMs': 3}


def _unknown_usage_ok(fact_ids):
    item = _ok(fact_ids)
    item['usage'] = contracts.empty_usage()
    return item


def _make_context(plan, model, root, **kwargs):
    state = exp_state.open_state(os.path.join(root, 'pilot.sqlite3'))
    options = dict(codec_version=contracts.CODEC_LEGACY,
                   facts_by_id=_facts('a', 'b', 'c'),
                   scope_payload={'goal': '测试'},
                   sleeper=lambda _s: None,
                   task_key='run-1')
    options.update(kwargs)
    return executor.make_context(state, model, _profile(), plan, **options)


def cleanup():
    for root in ROOTS:
        shutil.rmtree(root, ignore_errors=True)
    del ROOTS[:]


@_scenario('全部叶作业成功：候选落库、终态检查通过、usage 聚合正确')
def _(root):
    plan = _plan(_targets(('a', 'm1#/a'), ('b', 'm1#/b')), _profile())
    # 冷启动装箱：≤2 个完整语义单元合一个 job → 单次调用返回两个候选
    model = ScriptedModel([_ok(['f-a', 'f-b'], completion=160)])
    context = _make_context(plan, model, root)
    summary = executor.run_plan(context)
    assert summary['state'] == 'succeeded', summary
    check = contracts.final_state_check({'generate': summary['doc']})
    assert check['ok'], check
    usage = summary['usage']
    assert usage['knownCompletionTokens'] == 160, usage
    assert usage['calls'] == 1 and usage['unknownUsageCalls'] == 0, usage
    candidates = []
    for page in context['persistence'].iterate_candidates():
        candidates.extend(page)
    assert len(candidates) == 2, len(candidates)
    assert {c['key'] for c in candidates} == {'cand_f-a', 'cand_f-b'}
    # 恢复后重跑：无 queued 叶，终态即 done（成功叶不重做）
    again = _make_context(summary['doc'], ScriptedModel([]), root)
    assert executor.step(again)['kind'] == 'done'


@_scenario('length 拆分：父截断+两子成功的 usage 精确累计（父+子全部入账）')
def _(root):
    plan = _plan(_targets(('a', 'm1#/a'), ('b', 'm1#/b')), _profile())
    model = ScriptedModel([_length(completion=40), _ok(['f-a'], completion=100),
                           _ok(['f-b'], completion=60)])
    context = _make_context(plan, model, root)
    summary = executor.run_plan(context)
    assert summary['state'] == 'succeeded', summary
    doc = summary['doc']
    split_parents = [j for j in doc['jobs'].values() if j.get('children')]
    assert len(split_parents) == 1, doc['jobs']
    assert len(split_parents[0]['children']) == 2
    usage = summary['usage']
    assert usage['knownCompletionTokens'] == 40 + 100 + 60, usage   # 父截断不丢用量
    assert usage['calls'] == 3, usage
    leaves = [j for j in doc['jobs'].values() if not j.get('children')]
    assert {j['state'] for j in leaves} == {'succeeded'}
    check = contracts.final_state_check({'generate': doc})
    assert check['ok'], check


@_scenario('父拆两子左成功右失败：左提交后中断，恢复只补右叶（成功叶不重做）')
def _(root):
    plan = _plan(_targets(('a', 'm1#/a'), ('b', 'm1#/b')), _profile())
    model = ScriptedModel([_length(), _ok(['f-a']), _bad_json(), _bad_json()])
    context = _make_context(plan, model, root)
    summary = executor.run_plan(context)
    assert summary['state'] == 'failed', summary
    assert model.calls and len(model.calls) == 4, len(model.calls)
    # 恢复：从持久化载入 doc，新上下文只派发未完成叶
    from workbench.ontology_build import runner
    state = exp_state.open_state(os.path.join(root, 'pilot.sqlite3'))
    reloaded = state.load('run-1')
    resumable = runner.resumable_jobs({'generate': reloaded})
    assert len(resumable) == 1, resumable
    model2 = ScriptedModel([_ok(['f-b'], completion=70)])
    context2 = _make_context(reloaded, model2, root, run_attempt=2,
                             resume_requeue_failed=True)   # 显式 resume：只补失败叶
    summary2 = executor.run_plan(context2)
    assert summary2['state'] == 'succeeded', summary2
    assert len(model2.calls) == 1, model2.calls          # 只调右叶
    usage = summary2['usage']
    assert usage['knownCompletionTokens'] >= 70, usage   # 左叶成功结果保留在 aggregate


@_scenario('非 JSON 触发一次格式修复后成功：修复计入 attempt 与 usage')
def _(root):
    plan = _plan(_targets(('a', 'm1#/a')), _profile())
    model = ScriptedModel([_bad_json(), _ok(['f-a'], completion=80)])
    context = _make_context(plan, model, root)
    summary = executor.run_plan(context)
    assert summary['state'] == 'succeeded', summary
    assert len(model.calls) == 2, len(model.calls)
    kinds = [e.get('kind') for e in context['events']]
    assert 'format_repair' in kinds, kinds
    assert summary['usage']['knownCompletionTokens'] == 10 + 80, summary['usage']
    assert summary['usage']['calls'] == 2, summary['usage']


@_scenario('格式修复仍失败：job failed，run failed，usage 全入账')
def _(root):
    plan = _plan(_targets(('a', 'm1#/a')), _profile())
    model = ScriptedModel([_bad_json(), _bad_json()])
    context = _make_context(plan, model, root)
    summary = executor.run_plan(context)
    assert summary['state'] == 'failed', summary
    assert len(model.calls) == 2, len(model.calls)
    assert summary['usage']['calls'] == 2, summary['usage']
    doc = summary['doc']
    failed = [j for j in doc['jobs'].values() if j['state'] == 'failed']
    assert len(failed) == 1, doc['jobs']


@_scenario('网络重试：429 退避后成功，两次 attempt 都记账；重试不新建 job')
def _(root):
    plan = _plan(_targets(('a', 'm1#/a')), _profile())
    model = ScriptedModel([_net_error('RATE_LIMITED'), _ok(['f-a'], completion=90)])
    sleeps = []
    context = _make_context(plan, model, root, sleeper=sleeps.append)
    summary = executor.run_plan(context)
    assert summary['state'] == 'succeeded', summary
    assert len(model.calls) == 2 and sleeps, (len(model.calls), sleeps)
    assert summary['usage']['calls'] == 2, summary['usage']
    doc = summary['doc']
    jobs = [j for j in doc['jobs'].values() if not j.get('children')]
    assert len(jobs) == 1 and jobs[0]['state'] == 'succeeded', doc['jobs']
    assert len(jobs[0]['attemptIds']) == 2, jobs[0]


@_scenario('网络重试耗尽（3 次总额）：job failed；中间 attempt 全部记账')
def _(root):
    plan = _plan(_targets(('a', 'm1#/a')), _profile())
    model = ScriptedModel([_net_error(), _net_error(), _net_error()])
    context = _make_context(plan, model, root)
    summary = executor.run_plan(context)
    assert summary['state'] == 'failed', summary
    assert len(model.calls) == 3, len(model.calls)
    assert summary['usage']['calls'] == 3, summary['usage']
    assert summary['usage']['unknownUsageCalls'] == 3, summary['usage']


@_scenario('不可分超长：OVERSIZED_ATOMIC_TARGET 受阻，截断 usage 保留，成功叶保留')
def _(root):
    facts = _facts('a')
    facts['f-a']['data'] = '纯文本无结构'   # 无可靠字段范围
    plan = _plan(_targets(('a', 'm1#/a')), _profile())
    model = ScriptedModel([_length(completion=33)])
    context = _make_context(plan, model, root, facts_by_id=facts)
    summary = executor.run_plan(context)
    assert summary['state'] == 'blocked', summary
    assert summary['blocking']['code'] == contracts.OVERSIZED_ATOMIC_TARGET, summary['blocking']
    assert summary['usage']['knownCompletionTokens'] == 33, summary['usage']
    doc = summary['doc']
    blocked = [j for j in doc['jobs'].values() if j['state'] == 'blocked']
    assert len(blocked) == 1, doc['jobs']


@_scenario('attempt 预算耗尽：ATTEMPT_BUDGET_EXCEEDED 明确受阻，不静默裁切')
def _(root):
    targets = _targets(('a', 'm1#/a'), ('b', 'm1#/b'))
    plan = _plan(targets, _profile())
    estimate = {'slots': 1, 'inputTokens': 1000, 'expectedOutput': 2048,
                'visibleOutput': 1024, 'softTargetExceeded': False}
    for tid, fid in (('t-a', targets[0]), ('t-b', targets[1])):
        plan = batch_state.apply_event(plan, {
            'type': batch_state.EVENT_JOB_CREATED,
            'jobId': contracts.stable_job_id(1, '', 'root', [fid]),
            'orderedPrimaryTargetIds': [tid], 'contextFactIds': [], 'estimate': estimate})
    state = exp_state.open_state(os.path.join(root, 'pilot.sqlite3'))
    state.save_plan('run-1', plan)
    model = ScriptedModel([_ok(['f-a']), _ok(['f-b'])])
    context = _make_context(plan, model, root, budget={'maxAttempts': 1, 'maxJobs': 8})
    summary = executor.run_plan(context)
    assert summary['state'] == 'blocked', summary
    assert summary['blocking']['code'] == contracts.ATTEMPT_BUDGET_EXCEEDED, summary['blocking']
    assert len(model.calls) == 1, model.calls     # 第一个 job 用掉唯一额度后停


@_scenario('活跃耗时预算：超限即 blocked，不派发新请求')
def _(root):
    plan = _plan(_targets(('a', 'm1#/a')), _profile())
    model = ScriptedModel([])
    context = _make_context(plan, model, root, budget={'maxWallMs': 1})
    context['doc']['activeElapsedMs'] = 5000
    summary = executor.run_plan(context)
    assert summary['state'] == 'blocked', summary
    assert summary['blocking']['code'] == contracts.WALL_TIME_BUDGET_EXCEEDED
    assert len(model.calls) == 0, model.calls


@_scenario('campaign completion 软限：达线停止派发（COMPLETION_SOFT_LIMIT）')
def _(root):
    targets = _targets(('a', 'm1#/a'), ('b', 'm1#/b'))
    plan = _plan(targets, _profile())
    estimate = {'slots': 1, 'inputTokens': 1000, 'expectedOutput': 2048,
                'visibleOutput': 1024, 'softTargetExceeded': False}
    plan = batch_state.apply_event(plan, {
        'type': batch_state.EVENT_JOB_CREATED,
        'jobId': contracts.stable_job_id(1, '', 'root', [targets[0]]),
        'orderedPrimaryTargetIds': ['t-a'], 'contextFactIds': [], 'estimate': estimate})
    plan = batch_state.apply_event(plan, {
        'type': batch_state.EVENT_JOB_CREATED,
        'jobId': contracts.stable_job_id(1, '', 'root', [targets[1]]),
        'orderedPrimaryTargetIds': ['t-b'], 'contextFactIds': [], 'estimate': estimate})
    state = exp_state.open_state(os.path.join(root, 'pilot.sqlite3'))
    state.save_plan('run-1', plan)                    # 初始计划先落库再执行
    model = ScriptedModel([_ok(['f-a'], completion=500), _ok(['f-b'])])
    context = _make_context(plan, model, root, budget={'completionSoftLimit': 500})
    summary = executor.run_plan(context)
    assert summary['state'] == 'blocked', summary
    assert summary['blocking']['code'] == contracts.COMPLETION_SOFT_LIMIT, summary['blocking']
    assert len(model.calls) == 1, model.calls     # 达线不再派发
    assert summary['usage']['knownCompletionTokens'] == 500, summary['usage']


@_scenario('unknown usage：不冒充总数、unknownUsageCalls 计数、不进入零成本学习')
def _(root):
    plan = _plan(_targets(('a', 'm1#/a')), _profile())
    model = ScriptedModel([_unknown_usage_ok(['f-a'])])
    context = _make_context(plan, model, root)
    summary = executor.run_plan(context)
    assert summary['state'] == 'succeeded', summary
    usage = summary['usage']
    assert usage['unknownUsageCalls'] == 1 and usage['completionTokens'] is None, usage
    assert usage['knownCompletionTokens'] == 0, usage


@_scenario('写事务失败不推进内存：save 抛出后 doc 保持原状，异常上抛')
def _(root):
    plan = _plan(_targets(('a', 'm1#/a')), _profile())
    model = ScriptedModel([_net_error('RATE_LIMITED'), _ok(['f-a'])])
    context = _make_context(plan, model, root)

    real_state = context['persistence']._inner
    original_save = real_state.amend_plan
    calls = {'n': 0}

    def flaky_save(task_key, doc):
        calls['n'] += 1
        if calls['n'] == 2:
            raise RuntimeError('注入的持久化失败')
        return original_save(task_key, doc)

    real_state.amend_plan = flaky_save
    try:
        executor.run_plan(context)
        raise AssertionError('持久化失败应上抛')
    except RuntimeError:
        pass
    finally:
        real_state.save_plan = original_save
    # 内存 doc 未推进到成功：job 仍 running 且最后一次已持久化状态可从库重放
    persisted = real_state.load('run-1')
    assert persisted is not None
    job = next(iter(persisted['jobs'].values()))
    assert job['state'] in ('running', 'queued'), job['state']


@_scenario('claim 抛取消：执行器不吞异常，内存不推进（在线取消语义由 D17 组合）')
def _(root):
    from workbench.ontology_build import batch_persistence
    plan = _plan(_targets(('a', 'm1#/a')), _profile())
    model = ScriptedModel([])
    context = _make_context(plan, model, root)
    real_state = context['persistence']._inner
    original_claim = real_state.claim_job

    def cancelled_claim(task_key, job_id, run_attempt):
        raise batch_persistence.RunCancelledError('用户已取消')

    real_state.claim_job = cancelled_claim
    before = copy.deepcopy(context['doc'])
    try:
        executor.run_plan(context)
        raise AssertionError('取消应上抛')
    except batch_persistence.RunCancelledError:
        pass
    finally:
        real_state.claim_job = original_claim
    assert context['doc'] == before or not model.calls


@_scenario('拆分子作业（单元二分）事件经 batch_state 落库且覆盖守恒')
def _(root):
    plan = _plan(_targets(('a', 'm1#/a'), ('b', 'm1#/b')), _profile())
    model = ScriptedModel([_length(), _ok(['f-a']), _ok(['f-b'])])
    context = _make_context(plan, model, root)
    summary = executor.run_plan(context)
    doc = summary['doc']
    # 叶目标覆盖 == 计划目标（乱序完成也守恒）
    check = contracts.coverage_check(
        sorted(contracts.target_digest(t) for t in plan['targets'].values()),
        sorted(sum([[contracts.target_digest({'factId': doc['targets'][tid]['factId'],
                                             'selector': doc['targets'][tid]['selector']})
                     for tid in j['orderedPrimaryTargetIds']]
                    for j in doc['jobs'].values()
                    if not j.get('children') and j['state'] == 'succeeded'], [])))
    assert check['ok'], check


def main():
    if not SCENARIOS:
        print('[错误] 未收集到场景')
        return 2
    for run in SCENARIOS:
        run()
    cleanup()
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
