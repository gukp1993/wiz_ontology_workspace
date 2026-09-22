# -*- coding: utf-8 -*-
"""ontology_token_pilot 试验评价：用量台账 / 金样评价 / G2 质量门 / 成本对比 / 脱敏报告。

需求：《本体生成控输出_整合方案与并行开发计划_v3.md》§8 D13 行、§11 试验纪律、§12 G2 效果门。
金样来源：tests/fixtures/ontology_token_pilot/golden/（D11 交付，schemaVersion=1）。
usage 口径唯一来自 workbench/ontology_build/batch_contracts.py（D00 冻结契约）：
normalize_usage 幂等（camelCase 回喂不丢）、usage_aggregate 输出
knownCompletionTokens/unknownUsageCalls/calls/prompt/completion/total。

纪律（全部为硬口径，不是风格建议）：
* 父截断、子调用、格式修复、网络重试的**每次** attempt 全部入账；Ledger 只吃列表、
  不去重不丢弃——"同一 attempt 只记一次"的责任在执行器（content_tx 原子提交），不在台账。
* reasoningTokens 只单列展示，**绝不加进 completionTokens**（当前无 provider 文档明确分离）。
* unknown usage 不计 0、不冒充总数；中位数只统计已知样本，unknown 单独列数；
  任一臂有 unknown 时**不得宣称省钱**（compactBenefitGate=None，savingsProvable=False）。
* 候选↔金样匹配是**机械归一化后的精确身份匹配**（camel→snake、大小写、分隔符折叠、
  property 去掉 owner 前缀），一对一消耗；不做同义词/模糊匹配——语义准确率正是被测
  对象，匹配放水等于造假。dataType/单位/枚举等字段级差异进 quality 明细，
  **不改变 precision/recall 分母**。
* 同名异主体：mustNotMerge 组只是判定辅助——匹配本身一对一，两个 key 落同组不会
  误判成一个 matched；未匹配组如实进 mergeGroups，疑似合并（多余候选的归一化键
  恰等于组 rawName）进 mergeSuspects。
* 报告全部字段可 json.dumps；prompt/response 原文不得出现在报告结构（输入侧不带，
  输出侧 redact_report 防御性删除/掩码；64+ 位纯 hex 视为 hash/指纹保留不掩）。

本模块为纯函数/纯数据实现，不 import workbench 存储层/管线/LLM 客户端；
唯一 workbench 依赖是 batch_contracts（自身仅标准库，D00 契约要求各任务复用）。
"""
import re
import statistics

from workbench.ontology_build import batch_contracts as contracts

__all__ = [
    'UsageLedger', 'evaluate_candidates', 'quality_gate', 'cost_compare',
    'redact_report', 'scan_report', 'build_report', 'summarize_eval', 'norm_key',
]

CANDIDATE_TYPES = ('object', 'property', 'link', 'rule', 'action')

# 质量门默认阈值（§12 G2）：precision/recall 较 A 臂基线下降 ≤2pp
DEFAULT_MAX_DECLINE_PP = 2.0
# compact 收益门（§12 G2）：B 较 A 正文 token 中位数降低 ≥20%（非保证，先证未知）
COMPACT_BENEFIT_MIN_DROP_PCT = 20.0


# ---------------------------------------------------------------------------
# 归一化与身份
# ---------------------------------------------------------------------------

_CAMEL_BOUNDARY = re.compile(r'(?<=[a-z0-9])(?=[A-Z])')
_NON_WORD = re.compile(r'[^0-9a-z\u4e00-\u9fff]+')


def norm_key(value):
    """语义身份归一化：camelCase→snake、小写、非字母数字折叠为单下划线。

    只做机械归一化（ratedPower / rated_power / Rated.Power → rated_power），
    不做同义词、词干或编辑距离匹配——那是语义质量问题，必须如实计为未命中。
    """
    text = str(value if value is not None else '').strip()
    if not text:
        return ''
    text = _CAMEL_BOUNDARY.sub('_', text).lower()
    return _NON_WORD.sub('_', text).strip('_')


def semantic_identity(item):
    """候选/金样期望 → 语义身份三元组 (type, owner, localKey)。

    * property 的 ownerKey 参与身份（同名属性归属不同宿主是不同候选——同名异主体）；
    * key 形如 '<owner>.<field>' 时去掉 owner 前缀再比对（金样键空间带宿主前缀、
      模型候选键可能不带，两边都要兼容）；
    * rule/action 带 ownerKey 时同样参与身份（站级动作与对象级动作不同身份）。
    """
    ctype = str(item.get('type') or '')
    owner = norm_key(item.get('ownerKey')) if item.get('ownerKey') else ''
    key = norm_key(item.get('key'))
    local = key
    if owner and key.startswith(owner + '_'):
        local = key[len(owner) + 1:]
    return (ctype, owner, local)


def _typeless_identity(item):
    owner, local = semantic_identity(item)[1], semantic_identity(item)[2]
    return (owner, local)


# ---------------------------------------------------------------------------
# 用量台账（D13 核心口径：全量入账、reasoning 单列、unknown 不冒充）
# ---------------------------------------------------------------------------

class UsageLedger:
    """按 attempt 记账的累加器：只追加、不去重、不丢弃。

    add_attempt 的字段对应 §5/§6 契约里的 attempt 摘要：attemptId/jobId/usage/
    finishReason/errorCode/errorMessage/durationMs/bytes（可选 state 透传展示）。
    aggregate() 的 token 口径逐字来自 contracts.usage_aggregate；
    reasoningTokens 只单列（已知样本求和，无已知为 None），绝不并入 completion。
    """

    def __init__(self):
        self.attempts = []

    def add_attempt(self, attempt_id, job_id=None, usage=None, finish_reason=None,
                    error_code=None, error_message=None, duration_ms=None,
                    prompt_bytes=0, completion_bytes=0, state=None):
        self.attempts.append({
            'attemptId': str(attempt_id or ''),
            'jobId': str(job_id or ''),
            'usage': contracts.normalize_usage(usage),
            'finishReason': str(finish_reason) if finish_reason else None,
            'errorCode': str(error_code) if error_code else None,
            'errorMessage': str(error_message) if error_message else None,
            'durationMs': int(duration_ms) if duration_ms is not None else None,
            'promptBytes': int(prompt_bytes or 0),
            'completionBytes': int(completion_bytes or 0),
            'state': str(state) if state else None,
        })
        return self

    def __len__(self):
        return len(self.attempts)

    def known_completions(self):
        """已知样本的 completionTokens 列表（unknown 不进中位数，不冒充 0）。"""
        return [a['usage']['completionTokens'] for a in self.attempts
                if a['usage']['usageSource'] == 'api'
                and a['usage']['completionTokens'] is not None]

    def unknown_count(self):
        return sum(1 for a in self.attempts if a['usage']['usageSource'] != 'api')

    def aggregate(self):
        usages = [a['usage'] for a in self.attempts]
        core = contracts.usage_aggregate(usages)
        reasoning_known = [a['usage']['reasoningTokens'] for a in self.attempts
                           if a['usage']['usageSource'] == 'api'
                           and a['usage']['reasoningTokens'] is not None]
        finish_reasons = {}
        error_codes = {}
        states = {}
        duration_total = 0
        duration_max = 0
        for attempt in self.attempts:
            for key, bucket in ((attempt['finishReason'], finish_reasons),
                                (attempt['errorCode'], error_codes),
                                (attempt['state'], states)):
                if key:
                    bucket[key] = bucket.get(key, 0) + 1
            if attempt['durationMs'] is not None:
                duration_total += attempt['durationMs']
                duration_max = max(duration_max, attempt['durationMs'])
        return {
            'calls': core['calls'],
            'promptTokens': core['promptTokens'],
            'completionTokens': core['completionTokens'],
            'totalTokens': core['totalTokens'],
            'knownCompletionTokens': core['knownCompletionTokens'],
            'unknownUsageCalls': core['unknownUsageCalls'],
            'reasoningTokens': sum(reasoning_known) if reasoning_known else None,
            'finishReasons': finish_reasons,
            'errorCodes': error_codes,
            'states': states,
            'durationMs': duration_total,
            'maxAttemptDurationMs': duration_max,
            'promptBytes': sum(a['promptBytes'] for a in self.attempts),
            'completionBytes': sum(a['completionBytes'] for a in self.attempts),
        }


# ---------------------------------------------------------------------------
# 候选 ↔ 金样评价（语义身份一对一匹配）
# ---------------------------------------------------------------------------

def _check_fields(candidate, expected, quality):
    """金样 fields 子集断言（含 dataType/valueType/单位/枚举等任意字段键）。

    差异只进 quality.fieldsMismatched 明细，不影响 precision/recall 分母。
    """
    expected_fields = expected.get('fields') if isinstance(expected.get('fields'), dict) else {}
    candidate_fields = candidate.get('fields') if isinstance(candidate.get('fields'), dict) else {}
    for name in sorted(expected_fields):
        quality['fieldsChecked'] += 1
        if name not in candidate_fields:
            quality['fieldsMismatched'].append({
                'key': expected.get('key'), 'field': name,
                'expected': expected_fields[name], 'actual': None,
                'note': '候选缺少金样声明字段',
            })
        elif candidate_fields[name] != expected_fields[name]:
            quality['fieldsMismatched'].append({
                'key': expected.get('key'), 'field': name,
                'expected': expected_fields[name], 'actual': candidate_fields[name],
                'note': '字段值与金样不一致（dataType/单位/枚举等，评价明细不扣分母）',
            })


def _conflict_coverage(matched_pairs, golden, quality):
    """冲突保留判定：金样 expectedConflicts 与候选 conflicts 对得上才算 covered。"""
    candidate_by_golden_key = {}
    for pair in matched_pairs:
        candidate_by_golden_key[norm_key(pair['key'])] = pair['candidate']
    details = []
    for conflict in golden.get('expectedConflicts') or []:
        if not isinstance(conflict, dict):
            continue
        field = norm_key(conflict.get('field'))
        matched_candidate = candidate_by_golden_key.get(norm_key(conflict.get('candidateKey')))
        covered = False
        note = ''
        if matched_candidate is None:
            note = '金样冲突属性未被匹配（候选缺失或未命中）'
        else:
            expects = [norm_key(side.get('expect'))
                       for side in conflict.get('sides') or [] if side.get('expect')]
            for entry in matched_candidate.get('conflicts') or []:
                if norm_key(entry.get('field')) != field:
                    continue
                sides = entry.get('sides') or []
                if len(sides) < 2:
                    continue
                values = {norm_key(side.get('value')) for side in sides
                          if side.get('value') is not None}
                # 两侧冲突在位即保留；expect 值可核时必须包含期望值，缺失时不硬造
                if expects and values:
                    if set(expects) <= values:
                        covered = True
                        break
                else:
                    covered = True
                    note = '候选冲突两侧在位但未带值，按保留计（不臆造取值）'
                    break
            if not covered and not note:
                note = '候选 conflicts 未包含该字段的两侧冲突'
        details.append({'id': conflict.get('id'), 'field': conflict.get('field'),
                        'candidateKey': conflict.get('candidateKey'),
                        'covered': covered, 'note': note})
    quality['conflictDetails'] = details
    return {'expected': len(details),
            'covered': sum(1 for d in details if d['covered']),
            'uncovered': [d['id'] for d in details if not d['covered']]}


def evaluate_candidates(candidates, golden, allowed_owner_keys=None):
    """候选清单 vs 金样（schemaVersion=1）→ 匹配/缺失/多余/质量/结构问题。

    candidates：normalized candidate dict 列表（batch_contracts 编解码输出形状；
    允许缺字段，缺什么按什么评，不补造）。
    golden：D11 金样 dict（expectedCandidates/mustNotMerge/expectedConflicts/minimumCounts）。
    allowed_owner_keys：可选的"允许引用的已有稳定对象键"集合（§10：battery 必须来自
    本批对象或允许引用的已有稳定对象）；缺省 None 表示不做 owner 悬空检查
    （实验调用方拿得到跨批稳定对象集时才传）。

    返回 dict（全部字段可 json.dumps）：
    matched/missing/extra 明细 + precision/recall（机械身份匹配口径）+
    matchedByType/missingByType + typeMismatched（键同类型不同的近失，另列）+
    quality（字段级 dataType/单位/枚举差异明细，不改分母）+
    conflictCoverage（冲突保留判定）+ mergeGroups/mergeSuspects（同名异主体）+
    structuralIssues（重复身份/悬空引用/悬空 owner/空键/非法类型/rejectedRefs）。
    """
    golden = golden if isinstance(golden, dict) else {}
    candidate_list = [c for c in (candidates or []) if isinstance(c, dict)]
    golden_items = [g for g in (golden.get('expectedCandidates') or [])
                    if isinstance(g, dict)]

    golden_by_identity = {}
    for item in golden_items:
        golden_by_identity.setdefault(semantic_identity(item), []).append(item)

    candidate_norm_keys = {norm_key(c.get('key')) for c in candidate_list}
    object_norm_keys = {norm_key(c.get('key')) for c in candidate_list
                        if c.get('type') == 'object'}
    allowed_norm = {norm_key(k) for k in (allowed_owner_keys or [])}

    matched = []
    extra = []
    issues = []
    quality = {'fieldsChecked': 0, 'fieldsMismatched': []}
    suspects = []
    type_mismatched = []
    consumed = {}
    seen_identities = {}

    for candidate in candidate_list:
        ctype = candidate.get('type')
        raw_key = candidate.get('key')
        ident = semantic_identity(candidate)
        if ctype not in CANDIDATE_TYPES:
            issues.append({'code': 'invalidType', 'candidateKey': raw_key,
                           'detail': 'type=%r 不在候选类型枚举' % (ctype,)})
        if not str(raw_key or '').strip():
            issues.append({'code': 'emptyKey', 'candidateKey': raw_key,
                           'detail': '候选缺少 key'})
        if ident in seen_identities:
            issues.append({'code': 'duplicateIdentity', 'candidateKey': raw_key,
                           'detail': '与候选 %r 语义身份相同（type+owner+key）'
                                     % seen_identities[ident]})
        else:
            seen_identities[ident] = raw_key
        fields = candidate.get('fields') if isinstance(candidate.get('fields'), dict) else {}
        if ctype == 'link':
            for ref_field in ('sourceRef', 'targetRef'):
                ref = fields.get(ref_field)
                if not str(ref or '').strip() or norm_key(ref) not in candidate_norm_keys:
                    issues.append({'code': 'danglingRef', 'candidateKey': raw_key,
                                   'detail': 'link.%s=%r 在本批候选中无对应键'
                                             % (ref_field, ref)})
        if (allowed_owner_keys is not None and ctype in ('property', 'rule', 'action')
                and candidate.get('ownerKey')):
            owner_norm = norm_key(candidate['ownerKey'])
            if owner_norm not in object_norm_keys and owner_norm not in allowed_norm:
                issues.append({'code': 'danglingOwner', 'candidateKey': raw_key,
                               'detail': 'ownerKey=%r 既不是本批对象候选，也不在允许引用'
                                         '的稳定对象集' % (candidate['ownerKey'],)})
        rejected = candidate.get('rejectedRefs')
        if isinstance(rejected, int) and rejected > 0:
            issues.append({'code': 'rejectedRefs', 'candidateKey': raw_key,
                           'detail': '编解码层已拒绝 %d 个引用' % rejected})

        available = golden_by_identity.get(ident) or []
        index = consumed.get(ident, 0)
        if index < len(available):
            item = available[index]
            consumed[ident] = index + 1
            matched.append({'key': item.get('key'), 'type': item.get('type'),
                            'name': item.get('name'), 'candidateKey': raw_key,
                            'candidate': candidate})
            _check_fields(candidate, item, quality)
        else:
            if ident in golden_by_identity:
                reason = 'identity-consumed'
            else:
                reason = 'no-golden-match'
                typeless = _typeless_identity(candidate)
                if any(typeless == _typeless_identity(item) for item in golden_items):
                    type_mismatched.append({'candidateKey': raw_key, 'type': ctype,
                                            'note': '键与金样一致但 type 不同'})
            extra.append({'key': raw_key, 'type': ctype, 'reason': reason})
            raw_norm = norm_key(raw_key)
            for group in golden.get('mustNotMerge') or []:
                if raw_norm and raw_norm == norm_key(group.get('rawName')):
                    suspects.append({'group': group.get('keys'),
                                     'candidateKey': raw_key,
                                     'note': '多余候选的归一化键恰为同名组 rawName，'
                                             '疑似同名异主体被合并为单一候选'})

    missing = []
    for ident, items in sorted(golden_by_identity.items(), key=lambda kv: str(kv[0])):
        for offset, item in enumerate(items):
            if consumed.get(ident, 0) <= offset:
                missing.append({'key': item.get('key'), 'type': item.get('type'),
                                'name': item.get('name')})

    matched_pairs = [{'key': m['key'], 'type': m['type'], 'name': m['name'],
                      'candidateKey': m['candidateKey'], 'candidate': m['candidate']}
                     for m in matched]
    conflict_coverage = _conflict_coverage(matched_pairs, golden, quality)

    matched_golden_norms = {norm_key(m['key']) for m in matched}
    merge_groups = []
    for group in golden.get('mustNotMerge') or []:
        keys = group.get('keys') or []
        merge_groups.append({
            'keys': keys,
            'rawName': group.get('rawName'),
            'matchedCount': sum(1 for k in keys if norm_key(k) in matched_golden_norms),
            'reason': group.get('reason'),
        })

    matched_by_type = {}
    missing_by_type = {}
    for entry in matched:
        matched_by_type[entry['type']] = matched_by_type.get(entry['type'], 0) + 1
    for entry in missing:
        missing_by_type[entry['type']] = missing_by_type.get(entry['type'], 0) + 1

    matched_count = len(matched)
    extra_count = len(extra)
    missing_count = len(missing)
    precision = (matched_count / (matched_count + extra_count)
                 if (matched_count + extra_count) else 1.0)
    recall = (matched_count / (matched_count + missing_count)
              if (matched_count + missing_count) else 1.0)

    for entry in matched + missing + extra:
        entry.pop('candidate', None)

    return {
        'goldenFor': golden.get('goldenFor'),
        'goldenCount': len(golden_items),
        'candidateCount': len(candidate_list),
        'matched': matched,
        'missing': missing,
        'extra': extra,
        'matchedCount': matched_count,
        'missingCount': missing_count,
        'extraCount': extra_count,
        'precision': round(precision, 4),
        'recall': round(recall, 4),
        'typeMatched': matched_count,      # 身份含 type：命中即类型一致
        'typeMismatched': type_mismatched,
        'matchedByType': matched_by_type,
        'missingByType': missing_by_type,
        'quality': {'fieldsChecked': quality['fieldsChecked'],
                    'fieldsMismatched': quality['fieldsMismatched']},
        'conflictCoverage': conflict_coverage,
        'conflictDetails': quality['conflictDetails'],
        'mergeGroups': merge_groups,
        'mergeSuspects': suspects,
        'structuralIssues': issues,
        'goldenMinimumCounts': golden.get('minimumCounts') if isinstance(
            golden.get('minimumCounts'), dict) else {},
    }


# ---------------------------------------------------------------------------
# G2 质量门（§12：关键金样 100%、回归 ≤2pp）
# ---------------------------------------------------------------------------

def quality_gate(result, baseline_result=None, max_decline_pp=DEFAULT_MAX_DECLINE_PP):
    """对单个评价结果做 G2 判定辅助；baseline_result 提供 A 臂基线时加回归检查。

    检查项：
    * criticalObjects / ownershipComplete / endpointsComplete——金样里 object/property/
      link 三类必须全命中（缺一即假；'端点'在金样语境下以链接关系端点代替，
      HTTP 端点门归 D16/D17 的 E2E，不在此冒充）；
    * conflictsPreserved——expectedConflicts 全部 covered（冲突保留 100%）；
    * noStructuralIssues——无重复身份/悬空引用/悬空 owner（G2"已接受结果无非法引用/
      重复ID/悬空引用"）；
    * baseline precision/recall 下降 ≤ max_decline_pp 个百分点（默认 2pp）。
    返回 {'ok', 'checks': [{'id','ok','detail'}], 'maxDeclinePp'}；ok=全部检查通过。
    """
    result = result if isinstance(result, dict) else {}
    checks = []
    missing_by_type = result.get('missingByType') if isinstance(
        result.get('missingByType'), dict) else {}
    labels = {'object': 'criticalObjects', 'property': 'ownershipComplete',
              'link': 'endpointsComplete'}
    for ctype in ('object', 'property', 'link'):
        gap = missing_by_type.get(ctype, 0)
        checks.append({'id': labels[ctype], 'ok': gap == 0,
                       'detail': '金样 %s 类缺失 %d 条（必须为 0）' % (ctype, gap)})
    coverage = result.get('conflictCoverage') if isinstance(
        result.get('conflictCoverage'), dict) else {}
    if coverage.get('expected'):
        checks.append({'id': 'conflictsPreserved',
                       'ok': coverage.get('covered') == coverage.get('expected'),
                       'detail': '冲突保留 %s/%s' % (coverage.get('covered'),
                                                    coverage.get('expected'))})
    issues = result.get('structuralIssues') or []
    checks.append({'id': 'noStructuralIssues', 'ok': not issues,
                   'detail': '结构问题 %d 个%s' % (len(issues), (
                       '：' + '; '.join(sorted({str(i.get('code')) for i in issues}))
                       if issues else ''))})
    if baseline_result is not None:
        for metric in ('precision', 'recall'):
            base = float(baseline_result.get(metric) or 0.0)
            current = float(result.get(metric) or 0.0)
            drop_pp = (base - current) * 100.0
            checks.append({'id': metric + 'NotRegressed',
                           'ok': drop_pp <= float(max_decline_pp) + 1e-9,
                           'detail': '%s 基线 %.4f → 本次 %.4f（下降 %.2fpp，阈值 %.1fpp）'
                                     % (metric, base, current, drop_pp, max_decline_pp)})
    return {'ok': all(check['ok'] for check in checks), 'checks': checks,
            'maxDeclinePp': float(max_decline_pp)}


# ---------------------------------------------------------------------------
# 成本对比（B vs A 正文 token 中位数；unknown 不算省钱）
# ---------------------------------------------------------------------------

def cost_compare(ledger_a, ledger_b, benefit_min_drop_pct=COMPACT_BENEFIT_MIN_DROP_PCT):
    """两臂正文 token 中位数对比。

    * 中位数只用已知样本（usageSource=api 且 completionTokens 非 None）；
    * unknown 单独列数；任一臂存在 unknown 时不得宣称省钱：
      savingsProvable=False 且 compactBenefitGate=None（None=无法判定，不是 False 通过）；
    * compactBenefitGate 只在可证明时给 bool：medianB ≤ medianA×(1-20%) 才 True。
    """
    known_a = ledger_a.known_completions() if ledger_a is not None else []
    known_b = ledger_b.known_completions() if ledger_b is not None else []
    median_a = float(statistics.median(known_a)) if known_a else None
    median_b = float(statistics.median(known_b)) if known_b else None
    unknown_a = ledger_a.unknown_count() if ledger_a is not None else 0
    unknown_b = ledger_b.unknown_count() if ledger_b is not None else 0
    delta_pct = None
    if median_a and median_b is not None:
        delta_pct = round((median_b - median_a) / median_a * 100.0, 2)
    savings_provable = unknown_a == 0 and unknown_b == 0 and delta_pct is not None
    if delta_pct is None:
        gate = None
        note = '缺已知中位数（无 api usage 样本），无法比较'
    elif not savings_provable:
        gate = None
        note = ('存在 unknown usage（A=%d, B=%d）：中位数只代表已知样本，'
                '未知调用可能改变结论，不得宣称省钱' % (unknown_a, unknown_b))
    else:
        gate = delta_pct <= -float(benefit_min_drop_pct)
        note = ('B 较 A 中位数降低 %.2f%%，%s %.1f%% 收益门'
                % (abs(delta_pct) if delta_pct < 0 else 0, '达到' if gate else '未达到',
                   benefit_min_drop_pct))
    return {
        'medianCompletionA': median_a,
        'medianCompletionB': median_b,
        'medianCompletionDeltaPct': delta_pct,
        'compactBenefitGate': gate,
        'benefitMinDropPct': float(benefit_min_drop_pct),
        'savingsProvable': savings_provable,
        'unknownUsageCallsA': unknown_a,
        'unknownUsageCallsB': unknown_b,
        'knownSamplesA': len(known_a),
        'knownSamplesB': len(known_b),
        'callsA': len(ledger_a) if ledger_a is not None else 0,
        'callsB': len(ledger_b) if ledger_b is not None else 0,
        'note': note,
    }


# ---------------------------------------------------------------------------
# 脱敏（报告结构防御）
# ---------------------------------------------------------------------------

_SECRET_KEY_SINGLE = {'secret', 'password', 'passwd', 'authorization', 'credential',
                      'credentials', 'bearer'}
_SECRET_KEY_SEQS = [('api', 'key'), ('api', 'token'), ('access', 'token'),
                    ('refresh', 'token'), ('private', 'key'), ('client', 'secret'),
                    ('auth', 'token'), ('signing', 'key')]
_FORBIDDEN_KEYS = {'prompt', 'promptmessages', 'messages', 'response', 'responsetext',
                   'completiontext', 'rawcontent', 'rawresponse', 'raw'}
_SK_RE = re.compile(r'\bsk[-_][A-Za-z0-9_-]{6,}')
_BEARER_RE = re.compile(r'(?i)\bbearer\s+[A-Za-z0-9._~+/-]{8,}')
_BLOB_RE = re.compile(r'[A-Za-z0-9+/=_.-]{40,}')
_HEX_RE = re.compile(r'[0-9a-fA-F]+\Z')


def _key_words(name):
    return [w for w in _CAMEL_BOUNDARY.sub('_', str(name)).lower().split('_') if w]


def _is_secret_key(name):
    words = _key_words(name)
    if not words:
        return False
    if any(w in _SECRET_KEY_SINGLE for w in words):
        return True
    return any(tuple(words[i:i + len(seq)]) == seq
               for seq in _SECRET_KEY_SEQS
               for i in range(len(words) - len(seq) + 1))


def _is_forbidden_key(name):
    return ''.join(_key_words(name)) in _FORBIDDEN_KEYS


def _mask_string(text):
    masked = _SK_RE.sub('[REDACTED_KEY]', text)
    masked = _BEARER_RE.sub('[REDACTED_BEARER]', masked)

    def _blob(match):
        token = match.group(0)
        # 纯 hex 长串按 hash/指纹保留（golden sha256、planFingerprint 等），不掩
        if _HEX_RE.match(token) and len(token) >= 40:
            return token
        return '[REDACTED_BLOB]'

    return _BLOB_RE.sub(_blob, masked)


def redact_report(report):
    """深拷贝并脱敏：删 prompt/response 类键，掩 sk-/Bearer/长随机串，掩密钥形键值。

    纯 hex 长串（sha256/指纹）保留；输出结构里不再有 prompt/response 键与明文密钥样式。
    """
    dropped = {'count': 0}

    def walk(value):
        if isinstance(value, dict):
            out = {}
            for key, item in value.items():
                if _is_forbidden_key(key):
                    dropped['count'] += 1
                    continue
                if _is_secret_key(key):
                    out[key] = '[REDACTED]'
                else:
                    out[key] = walk(item)
            return out
        if isinstance(value, list):
            return [walk(item) for item in value]
        if isinstance(value, tuple):
            return [walk(item) for item in value]
        if isinstance(value, str):
            return _mask_string(value)
        return value

    cleaned = walk(report if isinstance(report, dict) else (report or {}))
    if isinstance(cleaned, dict) and dropped['count']:
        cleaned['redaction'] = {'droppedPromptResponseKeys': dropped['count']}
    return cleaned


def scan_report(report, path='$'):
    """防御性校验辅助：列出报告中疑似未脱敏的位置（键名/密钥样式/禁用键）。"""
    findings = []
    if isinstance(report, dict):
        for key, item in report.items():
            here = '%s.%s' % (path, key)
            if _is_forbidden_key(key):
                findings.append({'path': here, 'code': 'forbiddenKey', 'detail': str(key)})
            if _is_secret_key(key):
                findings.append({'path': here, 'code': 'secretKey', 'detail': str(key)})
            findings.extend(scan_report(item, here))
    elif isinstance(report, list):
        for index, item in enumerate(report):
            findings.extend(scan_report(item, '%s[%d]' % (path, index)))
    elif isinstance(report, str):
        if _SK_RE.search(report) or _BEARER_RE.search(report):
            findings.append({'path': path, 'code': 'secretLiteral',
                             'detail': '疑似密钥样式串'})
    return findings


# ---------------------------------------------------------------------------
# 汇总报告
# ---------------------------------------------------------------------------

def summarize_eval(evals):
    """单臂评价汇总：evals 为 evaluate_candidates 结果 dict 或其列表。"""
    results = ([evals] if isinstance(evals, dict)
               else [r for r in (evals or []) if isinstance(r, dict)])
    matched = missing = extra = 0
    conflicts_expected = conflicts_covered = 0
    issue_count = suspect_count = 0
    matched_by_type = {}
    missing_by_type = {}
    for result in results:
        matched += int(result.get('matchedCount') or 0)
        missing += int(result.get('missingCount') or 0)
        extra += int(result.get('extraCount') or 0)
        coverage = result.get('conflictCoverage') if isinstance(
            result.get('conflictCoverage'), dict) else {}
        conflicts_expected += int(coverage.get('expected') or 0)
        conflicts_covered += int(coverage.get('covered') or 0)
        issue_count += len(result.get('structuralIssues') or [])
        suspect_count += len(result.get('mergeSuspects') or [])
        for bucket, source in ((matched_by_type, result.get('matchedByType')),
                               (missing_by_type, result.get('missingByType'))):
            for ctype, count in (source or {}).items():
                bucket[ctype] = bucket.get(ctype, 0) + int(count or 0)
    precision = (matched / (matched + extra)) if (matched + extra) else 1.0
    recall = (matched / (matched + missing)) if (matched + missing) else 1.0
    return {
        'sampleCount': len(results),
        'matchedCount': matched,
        'missingCount': missing,
        'extraCount': extra,
        'precision': round(precision, 4),
        'recall': round(recall, 4),
        'matchedByType': matched_by_type,
        'missingByType': missing_by_type,
        'conflictsExpected': conflicts_expected,
        'conflictsCovered': conflicts_covered,
        'structuralIssueCount': issue_count,
        'mergeSuspectCount': suspect_count,
    }


def build_report(config, ledgers_by_arm, evals_by_arm, notes=None):
    """汇总试验报告：每臂 usage/质量/时延/未完成项 + 双臂成本对比 + 备注。

    config：campaign 级非敏感配置（providerId/model/各臂 codec/金样 hash 等）；
    密钥本来就不该进 config——这里仍做防御性脱敏。输出全部字段可 json.dumps。
    """
    config = config if isinstance(config, dict) else {}
    ledgers_by_arm = ledgers_by_arm if isinstance(ledgers_by_arm, dict) else {}
    evals_by_arm = evals_by_arm if isinstance(evals_by_arm, dict) else {}
    arm_configs = config.get('arms') if isinstance(config.get('arms'), dict) else {}

    arms = {}
    for arm in sorted(set(ledgers_by_arm) | set(evals_by_arm)):
        ledger = ledgers_by_arm.get(arm)
        summary = summarize_eval(evals_by_arm.get(arm))
        usage = ledger.aggregate() if ledger is not None else None
        arms[str(arm)] = {
            'config': redact_report(arm_configs.get(arm)
                                    if isinstance(arm_configs.get(arm), dict) else {}),
            'usage': usage,
            'quality': summary,
            'openItems': {
                'missingGolden': summary['missingCount'],
                'extraCandidates': summary['extraCount'],
                'unknownUsageCalls': usage['unknownUsageCalls'] if usage else 0,
                'structuralIssues': summary['structuralIssueCount'],
                'mergeSuspects': summary['mergeSuspectCount'],
            },
        }

    compare = None
    if 'A' in ledgers_by_arm and 'B' in ledgers_by_arm:
        compare = cost_compare(ledgers_by_arm['A'], ledgers_by_arm['B'])

    report = {
        'schemaVersion': 1,
        'reportKind': 'ontology-token-pilot',
        'config': redact_report(config),
        'arms': arms,
        'compare': compare,
        'notes': [str(note) for note in (notes or [])],
    }
    return redact_report(report)
