# -*- coding: utf-8 -*-
"""D13：用量/质量评价与脱敏报告回归（experiments/ontology_token_pilot/evaluate.py）。

需求：《本体生成控输出_整合方案与并行开发计划_v3.md》§8 D13 行、§11 试验纪律、§12 G2。
金样来源：tests/fixtures/ontology_token_pilot/golden/（D11 交付，f391679）。
usage 口径：workbench/ontology_build/batch_contracts.py（normalize_usage 幂等、
usage_aggregate 的 knownCompletionTokens/unknownUsageCalls/calls 口径）。

覆盖场景（任务书 10 项，另加 fixture 集成与幂等/簿记边界）：
1.  金样全命中 precision=recall=1.0；缺一条 → recall 扣减；多余候选 → precision 扣减。
2.  同名异主体：两个 config 候选 vs mustNotMerge 金样 → 一对一匹配下不误判 matched
    （缺失/多余如实计），且正确双键候选可全命中；疑似合并进 mergeSuspects。
3.  父截断+2 子 attempt usage 精确累计；格式修复与网络重试同账；台账不去重不丢弃。
4.  reasoning 不双加（completion=50、reasoning=30 → 聚合 completion=50，reasoning 单列 30）。
5.  unknown usage：unknownUsageCalls=1、completionTokens=None、knownCompletionTokens
    只含已知部分；cost_compare 有 unknown 时不得宣称省钱（gate=None 而非 False）。
6.  中位数收益门：B 较 A 低 25% → 过门；低 10% → 不过门。
7.  质量门：precision 降 3pp → fail；降 1pp → pass；冲突未保留/属性缺失 → 直接 fail。
8.  悬空引用/重复身份/悬空 owner/空键进 structuralIssues。
9.  redact：sk- 键值、Bearer、长随机串被掩码；纯 hex hash 保留；prompt/response 键删除；
    脱敏幂等。
10. build_report 可 JSON 序列化、无 prompt/response 键、防御性删掉 config 里的 prompt。

隔离纪律：纯函数断言，不启服务、不写仓库其他位置、不碰真实 ontology/ 与 data/。

运行：python3 tests/run.py --test tests/test_ontology_token_pilot_metrics.py
（或直接 python3 tests/test_ontology_token_pilot_metrics.py）
"""
import json
import sys
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from experiments.ontology_token_pilot import evaluate as ev  # noqa: E402
from workbench.ontology_build import batch_contracts as contracts  # noqa: E402

GOLDEN_DIR = REPO / 'tests' / 'fixtures' / 'ontology_token_pilot' / 'golden'

PASSED = []
FAILED = []
SEQ = [0]


def check(cond, message):
    SEQ[0] += 1
    if cond:
        PASSED.append(message)
        print('通过 %d) %s' % (SEQ[0], message))
        return True
    FAILED.append(message)
    print('[失败] %d) %s' % (SEQ[0], message))
    return False


def load_json(path):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return None


def api_usage(prompt, completion, reasoning=None, total=None):
    usage = contracts.empty_usage()
    usage.update({'promptTokens': prompt, 'completionTokens': completion,
                  'usageSource': 'api'})
    usage['reasoningTokens'] = reasoning
    usage['totalTokens'] = total if total is not None else (prompt or 0) + (completion or 0)
    return usage


def candidates_from_golden(golden, overrides=None):
    """把金样期望镜像成候选（理想生成器口径）：同 key/type/name/ownerKey/fields。"""
    overrides = overrides or {}
    out = []
    for item in golden.get('expectedCandidates') or []:
        cand = {
            'key': item.get('key'),
            'type': item.get('type'),
            'name': item.get('name'),
            'ownerKey': item.get('ownerKey'),
            'fields': dict(item.get('fields') or {}),
            'evidenceStatus': 'supported',
            'conflicts': [],
            'rejectedRefs': 0,
        }
        if item.get('key') in overrides:
            cand.update(overrides[item['key']])
        out.append(cand)
    return out


def small_golden():
    return {
        'schemaVersion': 1,
        'goldenFor': ['(内联小金样)'],
        'synthetic': True,
        'expectedCandidates': [
            {'key': 'gateway', 'type': 'object', 'name': '示例网关', 'ownerKey': None,
             'fields': {}},
            {'key': 'gateway.telemetry_interval_seconds', 'type': 'property',
             'name': '遥测周期', 'ownerKey': 'gateway', 'fields': {'dataType': 'number'}},
            {'key': 'gateway.tls_enabled', 'type': 'property', 'name': 'TLS 开关',
             'ownerKey': 'gateway', 'fields': {'dataType': 'boolean'}},
        ],
        'minimumCounts': {'object': 1, 'property': 2},
        'mustNotMerge': [],
        'expectedConflicts': [],
    }


# ---------------------------------------------------------------------------
# 场景 1：全命中 / 缺项 / 多余 / 字段级质量明细不改分母
# ---------------------------------------------------------------------------

def scenario_full_hit():
    print('\n----- 金样全命中与缺项/多余扣减 -----')
    golden = small_golden()
    result = ev.evaluate_candidates(candidates_from_golden(golden), golden)
    check(result['matchedCount'] == 3 and result['missingCount'] == 0
          and result['extraCount'] == 0,
          '镜像候选全命中（matched=3/missing=0/extra=0）')
    check(result['precision'] == 1.0 and result['recall'] == 1.0,
          '全命中 precision=recall=1.0')
    check(result['typeMatched'] == 3, 'typeMatched 计数与命中一致（身份含 type）')

    trimmed = [c for c in candidates_from_golden(golden)
               if c['key'] != 'gateway.tls_enabled']
    result = ev.evaluate_candidates(trimmed, golden)
    check(result['matchedCount'] == 2 and result['missingCount'] == 1
          and result['precision'] == 1.0
          and result['recall'] == round(2 / 3, 4)
          and result['missingByType'].get('property') == 1,
          '缺一条候选：precision 保持 1.0，recall=2/3（4 位舍入），缺失落 property 类')

    padded = candidates_from_golden(golden) + [
        {'key': 'ghost_prop', 'type': 'property', 'ownerKey': 'gateway',
         'fields': {'dataType': 'text'}}]
    result = ev.evaluate_candidates(padded, golden)
    check(result['matchedCount'] == 3 and result['extraCount'] == 1
          and abs(result['precision'] - 0.75) < 1e-6 and result['recall'] == 1.0,
          '多余候选：precision=3/4，recall 保持 1.0')

    drifted = candidates_from_golden(golden, overrides={
        'gateway.telemetry_interval_seconds': {'fields': {'dataType': 'text'}}})
    result = ev.evaluate_candidates(drifted, golden)
    check(result['matchedCount'] == 3 and result['precision'] == 1.0
          and result['recall'] == 1.0,
          'dataType 漂移不影响 precision/recall 分母（字段级差异另列）')
    mism = result['quality']['fieldsMismatched']
    check(len(mism) == 1 and mism[0]['field'] == 'dataType'
          and mism[0]['expected'] == 'number' and mism[0]['actual'] == 'text',
          'quality.fieldsMismatched 记录 dataType 期望值与实际值')


# ---------------------------------------------------------------------------
# 场景 2：同名异主体（mustNotMerge）
# ---------------------------------------------------------------------------

def scenario_must_not_merge():
    print('\n----- 同名异主体不误判 -----')
    full = load_json(GOLDEN_DIR / 'schema_conflict.golden.json')
    if not check(full is not None, '加载 D11 schema_conflict 金样'):
        return
    trimmed = {
        'schemaVersion': 1,
        'expectedCandidates': [c for c in full['expectedCandidates']
                               if c['key'] in ('inventory_config', 'metering_config')],
        'mustNotMerge': full['mustNotMerge'],
        'expectedConflicts': [],
    }
    merged = [{'key': 'config', 'type': 'object', 'name': '配置', 'ownerKey': None,
               'fields': {}},
              {'key': 'config', 'type': 'object', 'name': '配置2', 'ownerKey': None,
               'fields': {}}]
    result = ev.evaluate_candidates(merged, trimmed)
    check(result['matchedCount'] == 0 and result['missingCount'] == 2
          and result['extraCount'] == 2,
          '两个同名 config 候选：0 命中、金样两键齐 missing（不误判 matched）')
    check(any(i['code'] == 'duplicateIdentity' for i in result['structuralIssues']),
          '同身份候选计 duplicateIdentity 结构问题')
    check(len(result['mergeSuspects']) >= 1
          and result['mergeSuspects'][0]['group'] == ['inventory_config', 'metering_config'],
          '多余候选键=config 落 mustNotMerge 组 rawName → mergeSuspects')
    check(all(g['matchedCount'] == 0 for g in result['mergeGroups']),
          'mergeGroups 如实报告同名组 0 命中')

    split = candidates_from_golden(trimmed)
    result = ev.evaluate_candidates(split, trimmed)
    check(result['matchedCount'] == 2 and result['precision'] == 1.0
          and result['recall'] == 1.0 and not result['mergeSuspects'],
          'inventory_config/metering_config 双键候选：全命中且无合并嫌疑')
    check(all(g['matchedCount'] == 2 for g in result['mergeGroups']),
          '同名组两主体各自命中（matchedCount=2）')


# ---------------------------------------------------------------------------
# 场景 3/4/5：台账（全量入账、reasoning 不双加、unknown）
# ---------------------------------------------------------------------------

def scenario_ledger():
    print('\n----- 台账：父截断+子调用+修复+重试 全部入账 -----')
    ledger = ev.UsageLedger()
    ledger.add_attempt('a-parent', 'j1', usage=api_usage(50, 100), finish_reason='length',
                       duration_ms=1200, prompt_bytes=900, completion_bytes=400)
    ledger.add_attempt('a-left', 'j1-L', usage=api_usage(80, 200), finish_reason='stop',
                       duration_ms=800, prompt_bytes=700, completion_bytes=600)
    ledger.add_attempt('a-right', 'j1-R', usage=api_usage(70, 150), finish_reason='stop',
                       duration_ms=600, prompt_bytes=650, completion_bytes=450)
    agg = ledger.aggregate()
    check(len(ledger) == 3 and agg['calls'] == 3, '台账保留全部 3 个 attempt（不去重不丢弃）')
    check(agg['completionTokens'] == 450 and agg['knownCompletionTokens'] == 450,
          '父截断+2 子 completion 精确累计 100+200+150=450（unknown=0）')
    check(agg['promptTokens'] == 200 and agg['totalTokens'] == 650,
          'prompt=200、total=650 精确累计')
    check(agg['finishReasons'] == {'length': 1, 'stop': 2},
          'finishReason 计数 length=1/stop=2（截断入账）')
    check(agg['promptBytes'] == 2250 and agg['completionBytes'] == 1450
          and agg['durationMs'] == 2600 and agg['maxAttemptDurationMs'] == 1200,
          '字节与时延按 attempt 全量累计')

    ledger.add_attempt('a-parent', 'j1', usage=api_usage(50, 100))
    check(len(ledger) == 4 and ledger.aggregate()['calls'] == 4,
          '台账不识别去重：重复添加同 attemptId 照样计账（责任在执行器不双记）')

    retry_ledger = ev.UsageLedger()
    retry_ledger.add_attempt('a1', 'j', usage=api_usage(10, 100), finish_reason='length')
    retry_ledger.add_attempt('a2', 'j', usage=api_usage(10, 200), finish_reason='stop')
    retry_ledger.add_attempt('a3', 'j', usage=api_usage(10, 150), finish_reason='stop')
    retry_ledger.add_attempt('a4', 'j', usage=api_usage(5, 30), error_code='FORMAT_INVALID',
                             state='failed')
    retry_ledger.add_attempt('a5', 'j', usage=contracts.empty_usage(),
                             error_code='NETWORK_RETRYABLE', state='failed')
    agg = retry_ledger.aggregate()
    check(agg['calls'] == 5 and agg['unknownUsageCalls'] == 1
          and agg['completionTokens'] is None and agg['knownCompletionTokens'] == 480,
          '格式修复+网络重试入账：calls=5、unknown=1、known=480、总数不冒充')
    check(agg['errorCodes'] == {'FORMAT_INVALID': 1, 'NETWORK_RETRYABLE': 1},
          'errorCode 计数（父失败/重试均留痕）')


def scenario_reasoning():
    print('\n----- reasoning 不双加 -----')
    ledger = ev.UsageLedger()
    ledger.add_attempt('a1', 'j', usage=api_usage(100, 50, reasoning=30, total=180),
                       finish_reason='stop')
    agg = ledger.aggregate()
    check(agg['completionTokens'] == 50,
          'completion=50 逐字保留（50+30=80 的双计不存在）')
    check(agg['reasoningTokens'] == 30, 'reasoning=30 只单列展示')
    check(agg['totalTokens'] == 180, 'total 逐字取 provider 值')
    no_reasoning = ev.UsageLedger()
    no_reasoning.add_attempt('a1', 'j', usage=api_usage(10, 20))
    check(no_reasoning.aggregate()['reasoningTokens'] is None,
          '无已知 reasoning 时单列值为 None（不猜 0）')


def scenario_unknown():
    print('\n----- unknown usage 不算省钱 -----')
    arm_a = ev.UsageLedger()
    arm_a.add_attempt('a1', 'j', usage=api_usage(10, 200))
    arm_a.add_attempt('a2', 'j', usage=api_usage(10, 200))
    arm_b = ev.UsageLedger()
    arm_b.add_attempt('b1', 'j', usage=api_usage(10, 100))
    arm_b.add_attempt('b2', 'j', usage=contracts.empty_usage())
    agg = arm_b.aggregate()
    check(agg['unknownUsageCalls'] == 1 and agg['completionTokens'] is None
          and agg['knownCompletionTokens'] == 100 and agg['promptTokens'] is None,
          'unknown 聚合：unknownUsageCalls=1、completion=None、known 只含已知 100')
    compare = ev.cost_compare(arm_a, arm_b)
    check(abs(compare['medianCompletionDeltaPct'] - (-50.0)) < 1e-6,
          '已知样本中位数差 -50% 如实计算（100 vs 200）')
    check(compare['savingsProvable'] is False and compare['compactBenefitGate'] is None,
          'B 臂有 unknown：不得宣称省钱（gate=None，不是 False 通过）')
    check('unknown' in compare['note'], 'note 明示 unknown 阻断结论')


# ---------------------------------------------------------------------------
# 场景 6：中位数收益门
# ---------------------------------------------------------------------------

def scenario_median_gate():
    print('\n----- compact 收益门（中位数 -20% 阈值） -----')
    arm_a = ev.UsageLedger()
    for index, value in enumerate([100, 100, 100, 100]):
        arm_a.add_attempt('a%d' % index, 'j', usage=api_usage(10, value))
    arm_b25 = ev.UsageLedger()
    for index, value in enumerate([75, 75]):
        arm_b25.add_attempt('b%d' % index, 'j', usage=api_usage(10, value))
    arm_b10 = ev.UsageLedger()
    for index, value in enumerate([90, 90]):
        arm_b10.add_attempt('b%d' % index, 'j', usage=api_usage(10, value))
    gate25 = ev.cost_compare(arm_a, arm_b25)
    check(gate25['medianCompletionA'] == 100.0 and gate25['medianCompletionB'] == 75.0
          and gate25['medianCompletionDeltaPct'] == -25.0,
          '中位数 A=100、B=75、delta=-25%')
    check(gate25['compactBenefitGate'] is True and gate25['savingsProvable'] is True,
          '降 25% ≥ 20% 收益门且无 unknown → 过门')
    gate10 = ev.cost_compare(arm_a, arm_b10)
    check(gate10['medianCompletionDeltaPct'] == -10.0
          and gate10['compactBenefitGate'] is False,
          '降 10% < 20% → 不过门（False）')
    empty = ev.cost_compare(ev.UsageLedger(), arm_a)
    check(empty['medianCompletionDeltaPct'] is None
          and empty['compactBenefitGate'] is None,
          '无已知样本时 delta/gate 均为 None（不冒充）')


# ---------------------------------------------------------------------------
# 场景 7：G2 质量门
# ---------------------------------------------------------------------------

def make_gate_result(precision, recall, missing_by_type=None, conflicts=None, issues=None):
    return {
        'precision': precision,
        'recall': recall,
        'matchedByType': {'object': 2, 'property': 4, 'link': 1},
        'missingByType': missing_by_type or {},
        'conflictCoverage': conflicts or {'expected': 0, 'covered': 0, 'uncovered': []},
        'structuralIssues': issues or [],
        'goldenMinimumCounts': {},
    }


def scenario_quality_gate():
    print('\n----- G2 质量门（2pp 回归阈值 + 100% 保留检查） -----')
    baseline = make_gate_result(1.0, 1.0)
    drop3 = make_gate_result(0.97, 1.0)
    gate = ev.quality_gate(drop3, baseline)
    check(gate['ok'] is False, 'precision 降 3pp → 门失败')
    check(any(c['id'] == 'precisionNotRegressed' and not c['ok'] for c in gate['checks']),
          'precisionNotRegressed 检查项为不通过')
    drop1 = make_gate_result(0.99, 1.0)
    gate = ev.quality_gate(drop1, baseline)
    check(gate['ok'] is True, 'precision 降 1pp（≤2pp）→ 门通过')
    recall_drop = make_gate_result(1.0, 0.97)
    gate = ev.quality_gate(recall_drop, baseline)
    check(gate['ok'] is False and any(c['id'] == 'recallNotRegressed' and not c['ok']
                                      for c in gate['checks']),
          'recall 降 3pp → 门失败（两项独立检查）')
    conflict_open = make_gate_result(1.0, 1.0, conflicts={'expected': 2, 'covered': 1,
                                                          'uncovered': ['x']})
    gate = ev.quality_gate(conflict_open, baseline)
    check(gate['ok'] is False and any(c['id'] == 'conflictsPreserved' and not c['ok']
                                      for c in gate['checks']),
          '冲突保留 1/2 → 门失败（冲突保留必须 100%）')
    prop_missing = make_gate_result(1.0, 0.9, missing_by_type={'property': 1})
    gate = ev.quality_gate(prop_missing, baseline)
    check(gate['ok'] is False and any(c['id'] == 'ownershipComplete' and not c['ok']
                                      for c in gate['checks']),
          'property 缺失 1 条 → ownershipComplete 失败（属性归属 100%）')
    issue = make_gate_result(1.0, 1.0, issues=[{'code': 'danglingRef',
                                                'candidateKey': 'l1', 'detail': 'x'}])
    gate = ev.quality_gate(issue, baseline)
    check(gate['ok'] is False and any(c['id'] == 'noStructuralIssues' and not c['ok']
                                      for c in gate['checks']),
          '存在悬空引用 → noStructuralIssues 失败')


# ---------------------------------------------------------------------------
# 场景 8：结构问题（悬空引用/重复身份/悬空 owner/空键）
# ---------------------------------------------------------------------------

def scenario_structural_issues():
    print('\n----- structuralIssues -----')
    golden = {
        'schemaVersion': 1,
        'expectedCandidates': [
            {'key': 'battery_ess', 'type': 'object', 'name': '电池储能单元',
             'ownerKey': None, 'fields': {}},
            {'key': 'battery_ess.voltage', 'type': 'property', 'name': '电压',
             'ownerKey': 'battery_ess', 'fields': {'dataType': 'number'}},
        ],
        'minimumCounts': {},
        'mustNotMerge': [],
        'expectedConflicts': [],
    }
    candidates = [
        {'key': 'battery_ess', 'type': 'object', 'name': 'x', 'ownerKey': None,
         'fields': {}},
        {'key': 'voltage', 'type': 'property', 'name': 'v', 'ownerKey': 'battery_ess',
         'fields': {'dataType': 'number'}},
        {'key': 'voltage', 'type': 'property', 'name': 'v2', 'ownerKey': 'ghost_owner',
         'fields': {'dataType': 'number'}},
        {'key': 'l1', 'type': 'link', 'name': 'l', 'ownerKey': None,
         'fields': {'sourceRef': 'battery_ess', 'targetRef': 'nope'}},
        {'key': 'dup', 'type': 'object', 'name': 'd1', 'ownerKey': None, 'fields': {}},
        {'key': 'dup', 'type': 'object', 'name': 'd2', 'ownerKey': None, 'fields': {}},
        {'key': '', 'type': 'object', 'name': 'e', 'ownerKey': None, 'fields': {}},
        {'key': 'weird', 'type': 'portal', 'name': 'w', 'ownerKey': None, 'fields': {}},
    ]
    result = ev.evaluate_candidates(candidates, golden, allowed_owner_keys=set())
    codes = {issue['code'] for issue in result['structuralIssues']}
    check({'danglingRef', 'danglingOwner', 'duplicateIdentity', 'emptyKey',
           'invalidType'} <= codes,
          '悬空引用/悬空 owner/重复身份/空键/非法类型 全部落 structuralIssues: %s'
          % sorted(codes))
    check(result['matchedCount'] == 2 and result['recall'] == 1.0
          and result['precision'] == round(2 / 8, 4) and result['extraCount'] == 6,
          '结构问题不改匹配结论：合法两条仍命中、recall=1.0；6 条问题候选进 extra'
          '（precision=2/8 如实扣减）')
    check(len(result['extra']) == 6 and result['extra'][0]['reason'] == 'no-golden-match',
          '未匹配候选全部进 extra（重复身份的第二条不再消耗金样）')
    allowed = ev.evaluate_candidates(
        candidates[:3], golden, allowed_owner_keys={'meter_point'})
    check(any(i['code'] == 'danglingOwner' and 'ghost_owner' in i['detail']
              for i in allowed['structuralIssues']),
          'danglingOwner 对允许引用集做核验（ghost_owner 不在集内仍报）')


# ---------------------------------------------------------------------------
# 场景 9：脱敏
# ---------------------------------------------------------------------------

def scenario_redact():
    print('\n----- redact_report 脱敏 -----')
    fake = {
        'arm': 'B',
        'apiKey': 'sk-abc123XYZDEF456ghi',
        'note': 'call failed with bearer Abcd1234EfgH5678 token',
        'sha256': 'a1' * 32,
        'blob': 'Z' * 48,
        'prompt': 'should disappear',
        'nested': {'response': 'x', 'Authorization': 'Bearer zzz',
                   'promptMessages': ['m1'], 'count': 3},
    }
    red = ev.redact_report(fake)
    dumped = json.dumps(red, ensure_ascii=False)
    check('sk-' not in dumped and red['apiKey'] == '[REDACTED]',
          'sk- 键值被整体掩码为 [REDACTED]')
    check('[REDACTED_BEARER]' in red['note'] and 'Abcd1234' not in red['note'],
          'Bearer 样式串被掩码')
    check(red['sha256'] == 'a1' * 32, '64 位纯 hex（hash/指纹）保留不掩')
    check(red['blob'] == '[REDACTED_BLOB]', '长随机串被掩码为 [REDACTED_BLOB]')
    check('prompt' not in red and 'response' not in red['nested']
          and 'promptMessages' not in red['nested'],
          'prompt/response/promptMessages 键被删除')
    check(red['nested']['Authorization'] == '[REDACTED]' and red['nested']['count'] == 3,
          '密钥形键值被掩码、普通值不受影响')
    check(ev.redact_report(red) == red, '脱敏幂等（redact(redact(x)) == redact(x)）')
    findings = ev.scan_report(fake)
    codes = {finding['code'] for finding in findings}
    check({'forbiddenKey', 'secretKey', 'secretLiteral'} <= codes,
          'scan_report 识别未脱敏项（禁用键/密钥形键/密钥字面量）')
    residual = [finding['code'] for finding in ev.scan_report(red)]
    check('forbiddenKey' not in residual and 'secretLiteral' not in residual,
          '脱敏后 scan 不再报禁用键与密钥字面量（密钥形键名保留但值已掩）')


# ---------------------------------------------------------------------------
# 场景 10：build_report 汇总
# ---------------------------------------------------------------------------

def scenario_build_report():
    print('\n----- build_report 汇总与序列化 -----')
    golden = small_golden()
    eval_a = ev.evaluate_candidates(candidates_from_golden(golden), golden)
    eval_a2 = ev.evaluate_candidates(
        candidates_from_golden(golden)[:2], golden)
    eval_b = ev.evaluate_candidates(
        candidates_from_golden(golden)[:2] + [
            {'key': 'ghost', 'type': 'property', 'ownerKey': 'gateway',
             'fields': {'dataType': 'text'}}], golden)

    arm_a = ev.UsageLedger()
    for index, value in enumerate([100, 100, 100, 100]):
        arm_a.add_attempt('a%d' % index, 'j', usage=api_usage(10, value),
                          finish_reason='stop', duration_ms=100 + index)
    arm_b = ev.UsageLedger()
    for index, value in enumerate([75, 75]):
        arm_b.add_attempt('b%d' % index, 'j', usage=api_usage(10, value),
                          finish_reason='stop', duration_ms=90 + index)
    arm_b.add_attempt('b-unknown', 'j', usage=contracts.empty_usage(),
                      error_code='NETWORK_RETRYABLE')

    config = {
        'campaignId': 'camp-syn-1',
        'providerId': 'synthetic-provider',
        'model': 'syn-model-x',
        'goldenSha256': 'a1' * 32,
        'arms': {
            'A': {'codec': 'legacy-v1', 'providerId': 'synthetic-provider'},
            'B': {'codec': 'compact-v1', 'providerId': 'synthetic-provider',
                  'prompt': 'leak-attempt'},
        },
    }
    report = ev.build_report(
        config,
        {'A': arm_a, 'B': arm_b},
        {'A': [eval_a, eval_a2], 'B': eval_b},
        notes=['合成双臂', 'fake provider'])
    serialized = json.dumps(report, ensure_ascii=False)
    check(bool(serialized) and report['schemaVersion'] == 1,
          'build_report 输出可 json.dumps')
    forbidden = []

    def walk_keys(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if str(key).strip().lower() in ('prompt', 'response', 'messages'):
                    forbidden.append(str(key))
                walk_keys(item)
        elif isinstance(value, list):
            for item in value:
                walk_keys(item)

    walk_keys(report)
    check(not forbidden, '报告结构无 prompt/response/messages 键（含防御性删 config 泄漏）')
    check('leak-attempt' not in serialized, 'config 中的 prompt 值未进入报告')
    check(report['config']['goldenSha256'] == 'a1' * 32, 'config hash 保留（纯 hex）')
    arms = report['arms']
    check(set(arms) == {'A', 'B'}, '双臂汇总（A/B）')
    for arm in ('A', 'B'):
        entry = arms[arm]
        check(bool(entry.get('usage')) and bool(entry.get('quality'))
              and isinstance(entry.get('openItems'), dict)
              and 'durationMs' in (entry.get('usage') or {}),
              '%s 臂含 usage/quality/openItems/时延' % arm)
    check(arms['A']['quality']['sampleCount'] == 2
          and arms['A']['quality']['matchedCount'] == 5
          and arms['A']['quality']['missingCount'] == 1,
          'A 臂多样本评价汇总（2 样本 matched=5 missing=1）')
    check(arms['A']['openItems']['missingGolden'] == 1
          and arms['A']['openItems']['unknownUsageCalls'] == 0,
          'A 臂未完成项：缺金样 1、unknown 0')
    check(arms['B']['openItems']['unknownUsageCalls'] == 1
          and arms['B']['openItems']['extraCandidates'] == 1,
          'B 臂未完成项：unknown 1、多余候选 1')
    compare = report['compare']
    check(compare is not None and compare['medianCompletionDeltaPct'] == -25.0
          and compare['compactBenefitGate'] is None
          and compare['savingsProvable'] is False,
          'compare：中位数 -25% 但 B 有 unknown → 收益门 None（不算省钱）')
    check(report['notes'] == ['合成双臂', 'fake provider'], 'notes 原样保留')
    gate = ev.quality_gate(eval_b, eval_a)
    check(gate['ok'] is False,
          '联动：B 臂评价对 A 臂基线过质量门 → 因缺失+多余失败（正常判定路径）')


# ---------------------------------------------------------------------------
# 场景 11：真实 D11 金样集成（G2 关键金样 100% 路径）
# ---------------------------------------------------------------------------

def scenario_real_goldens():
    print('\n----- 真实 D11 金样集成 -----')
    for name in ('schema_conflict.golden.json', 'amplified_device_instances.golden.json',
                 'simple_gateway_config.golden.json', 'simple_station_topology.golden.json'):
        golden = load_json(GOLDEN_DIR / name)
        if not check(golden is not None, '加载金样 %s' % name):
            continue
        overrides = {}
        for conflict in golden.get('expectedConflicts') or []:
            overrides[conflict.get('candidateKey')] = {
                'conflicts': [{'field': conflict.get('field'),
                               'sides': [{'factId': 'f1', 'value': side.get('expect')}
                                         for side in conflict.get('sides') or []],
                               'note': '合成冲突'}]}
        result = ev.evaluate_candidates(candidates_from_golden(golden, overrides), golden)
        label = name.replace('.golden.json', '')
        check(result['matchedCount'] == result['goldenCount']
              and result['precision'] == 1.0 and result['recall'] == 1.0,
              '%s：镜像候选全命中（G2 关键金样 100%% 路径可达成）' % label)
        check(not result['structuralIssues'] and not result['mergeSuspects'],
              '%s：无结构问题与合并嫌疑' % label)
        coverage = result['conflictCoverage']
        check(coverage['covered'] == coverage['expected'],
              '%s：冲突保留判定 covered==expected（%s）'
              % (label, coverage['expected'] or '无冲突金样'))
        gate = ev.quality_gate(result)
        check(gate['ok'], '%s：G2 质量门通过（类型/归属/端点/冲突/结构全过）' % label)


# ---------------------------------------------------------------------------

def main():
    print('========== D13 用量/质量评价与脱敏报告回归 ==========')
    print('evaluate: experiments/ontology_token_pilot/evaluate.py')
    check(hasattr(ev, 'UsageLedger') and hasattr(ev, 'evaluate_candidates')
          and hasattr(ev, 'quality_gate') and hasattr(ev, 'cost_compare')
          and hasattr(ev, 'redact_report') and hasattr(ev, 'build_report'),
          '模块暴露冻结入口（UsageLedger/evaluate_candidates/quality_gate/'
          'cost_compare/redact_report/build_report）')
    scenario_full_hit()
    scenario_must_not_merge()
    scenario_ledger()
    scenario_reasoning()
    scenario_unknown()
    scenario_median_gate()
    scenario_quality_gate()
    scenario_structural_issues()
    scenario_redact()
    scenario_build_report()
    scenario_real_goldens()
    return 0


if __name__ == '__main__':
    code = 1
    try:
        code = main()
    except Exception as exc:
        traceback.print_exc()
        FAILED.append('未预期异常: %s: %s' % (type(exc).__name__, exc))
    total = len(PASSED) + len(FAILED)
    print('\n========== 汇总 ==========')
    print('通过 %d / %d' % (len(PASSED), total))
    for name in FAILED:
        print('  失败: ' + name)
    if total == 0:
        print('[错误] 未执行任何断言——不算通过')
        sys.exit(2)
    sys.exit(0 if code == 0 and not FAILED else 1)
