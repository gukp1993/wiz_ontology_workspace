# -*- coding: utf-8 -*-
"""ontology_token_pilot 试验 CLI（D14；用法冻结，整合计划 v3 §11）。

    python3 -m experiments.ontology_token_pilot --config <路径> --output-dir <目录> --mode fake \
        [--arms A,B,C,D] [--repeats 1] [--samples simple,amplified,schema_conflict]

* --mode fake 默认：零真实调用、零 workbench 存储层依赖；--mode real 需 provider 可用
  （providerId 经 workbench.llm_providers 解析，key 只留内存，绝不进参数/报告/日志），
  且真实调用属显式授权边界（默认 fake；real 实测归 D18）。
* 退出码：0 全部成功；2 部分受阻（campaign 预算/模型不可用/运行失败，报告注明）；
  1 参数错误（缺 config / 配置非法 / 臂或样本名非法）。
* 汇总报告：D13 build_report → <output-dir>/report.json + 可读 report.md
  （G2 三项判定：质量门 / compact 收益门 / 成本取舍；未跑的项如实标注）。
* campaign 预算与暖缓存跨臂/样本/重复/续跑共享（见 adapter.py；目录布局见 README）。
"""
import argparse
import json
import os
import sys
import traceback

from experiments.ontology_token_pilot import adapter
from experiments.ontology_token_pilot import evaluate

EXIT_OK = 0
EXIT_ARGS = 1
EXIT_BLOCKED = 2

_G2_COST_INCREASE_PCT = 10.0   # §12：D 总 token 较 A 增加 >10% → 保留成本取舍待用户决定


class ArgError(Exception):
    """CLI 参数/配置错误（退出码 1）。"""


class _ArgParser(argparse.ArgumentParser):
    """argparse 默认错误退出码是 2，与「部分受阻」冲突——统一改抛 ArgError（退出 1）。"""

    def error(self, message):
        raise ArgError(message)


def _build_parser():
    parser = _ArgParser(prog='python3 -m experiments.ontology_token_pilot',
                        description='本体生成控输出 v3：A/B/C/D 四臂 token 试点（默认 fake）')
    parser.add_argument('--config', required=True, help='试验配置 JSON（非敏感字段，不含 key）')
    parser.add_argument('--output-dir', required=True, help='独立结果目录（states/results/report）')
    parser.add_argument('--mode', choices=('fake', 'real'), default='fake',
                        help='fake=默认假模型；real=真实调用（需授权与可用 provider）')
    parser.add_argument('--arms', default='A,B,C,D', help='逗号分隔臂名（A/B/C/D）')
    parser.add_argument('--repeats', type=int, default=1, help='每臂每样本独立冷跑重复数')
    parser.add_argument('--samples', default=None,
                        help='逗号分隔样本 id（默认取配置 sampleIds 全部）')
    return parser


def _parse_list(text, allowed, field):
    items = []
    for part in str(text or '').split(','):
        item = part.strip()
        if not item:
            continue
        if allowed is not None and item not in allowed:
            raise ArgError('%s 非法：%r（允许 %s）' % (field, item, '/'.join(allowed)))
        if item not in items:
            items.append(item)
    if not items:
        raise ArgError('%s 不能为空' % field)
    return items


def _aggregate_evals(evals):
    """同臂同样本多次评价 → 伪评价 dict（供 evaluate.quality_gate 消费的并集口径）。"""
    matched = missing = extra = 0
    missing_by_type = {}
    conflicts_expected = conflicts_covered = 0
    issues = []
    for item in evals or []:
        matched += int(item.get('matchedCount') or 0)
        missing += int(item.get('missingCount') or 0)
        extra += int(item.get('extraCount') or 0)
        for ctype, count in (item.get('missingByType') or {}).items():
            missing_by_type[ctype] = missing_by_type.get(ctype, 0) + int(count or 0)
        coverage = item.get('conflictCoverage') if isinstance(
            item.get('conflictCoverage'), dict) else {}
        conflicts_expected += int(coverage.get('expected') or 0)
        conflicts_covered += int(coverage.get('covered') or 0)
        issues.extend(item.get('structuralIssues') or [])
    precision = (matched / (matched + extra)) if (matched + extra) else 1.0
    recall = (matched / (matched + missing)) if (matched + missing) else 1.0
    return {'precision': round(precision, 4), 'recall': round(recall, 4),
            'missingByType': missing_by_type,
            'conflictCoverage': {'expected': conflicts_expected,
                                 'covered': conflicts_covered},
            'structuralIssues': issues}


def _g2_verdicts(results, report):
    """G2 三项判定结论（§12）：质量门 / compact 收益门 / 成本取舍；未跑如实标注。"""
    usage_by_arm = {arm: (entry.get('usage') or {})
                    for arm, entry in (report.get('arms') or {}).items()}
    evals_by_arm_sample = {}
    for result in results:
        evals_by_arm_sample.setdefault((str(result.get('arm')), str(result.get('sample'))),
                                       []).extend(result.get('quality') or [])

    quality_rows = []
    for sample in sorted({str(result.get('sample')) for result in results}):
        agg_a = evals_by_arm_sample.get(('A', sample))
        agg_d = evals_by_arm_sample.get(('D', sample))
        if not agg_a or not agg_d:
            quality_rows.append({'sample': sample, 'verdict': 'not_run',
                                 'detail': 'A 或 D 臂该样本未跑，质量门无法判定'})
            continue
        gate = evaluate.quality_gate(_aggregate_evals(agg_d), _aggregate_evals(agg_a))
        quality_rows.append({'sample': sample,
                             'verdict': 'pass' if gate['ok'] else 'fail',
                             'failedChecks': [check['id'] for check in gate['checks']
                                              if not check['ok']]})

    compare = report.get('compare') if isinstance(report.get('compare'), dict) else None
    if compare:
        gate_value = compare.get('compactBenefitGate')
        compact_verdict = ('pass' if gate_value else 'fail') \
            if gate_value is not None else 'unprovable'
        compact_detail = str(compare.get('note') or '')
    else:
        compact_verdict = 'not_run'
        compact_detail = 'A 或 B 臂未跑，compact 收益门无法判定'

    usage_a = usage_by_arm.get('A') or {}
    usage_d = usage_by_arm.get('D') or {}
    if not usage_a or not usage_d:
        cost_verdict, cost_detail = 'not_run', 'A 或 D 臂未跑，成本取舍无法判定'
    else:
        unknown_a = int(usage_a.get('unknownUsageCalls') or 0)
        unknown_d = int(usage_d.get('unknownUsageCalls') or 0)
        total_a = int(usage_a.get('totalTokens') or 0)
        total_d = int(usage_d.get('totalTokens') or 0)
        if unknown_a + unknown_d:
            cost_verdict = 'unprovable'
            cost_detail = ('存在 unknown usage（A=%d, D=%d）：总量未知，成本取舍不可证'
                           % (unknown_a, unknown_d))
        elif total_a <= 0:
            cost_verdict, cost_detail = 'unprovable', 'A 臂无已知 token 用量，无法比较'
        else:
            delta_pct = (total_d - total_a) / float(total_a) * 100.0
            if delta_pct > _G2_COST_INCREASE_PCT:
                cost_verdict = 'user_decision'
                cost_detail = ('D 总 token 较 A 增加 %.1f%%（超过 %.1f%% 阈值）：'
                               '保留成本取舍待用户决定，不自动启用 compact'
                               % (delta_pct, _G2_COST_INCREASE_PCT))
            else:
                cost_verdict = 'acceptable'
                cost_detail = 'D 总 token 较 A 变化 %+.1f%%（未超 %.1f%% 阈值）' % (
                    delta_pct, _G2_COST_INCREASE_PCT)
    return {'qualityGate': {'rows': quality_rows},
            'compactBenefitGate': {'verdict': compact_verdict, 'detail': compact_detail},
            'costTradeoff': {'verdict': cost_verdict, 'detail': cost_detail}}


def _render_markdown(report, g2, results, notes):
    arms = report.get('arms') or {}
    config = report.get('config') or {}
    planner_of = {}
    for result in results:
        meta = result.get('meta') or {}
        planner_of[str(result.get('arm'))] = str(meta.get('planner') or '')
    lines = ['# ontology_token_pilot 试验报告', '']
    lines.append('- campaign：%s（mode=%s，repeats=%s）' % (
        config.get('campaignId'), config.get('mode'), config.get('repeats')))
    lines.append('- samples：%s' % '、'.join(str(s) for s in (config.get('samples') or [])))
    lines.append('- providerId：%s（密钥不入报告）' % config.get('providerId'))
    lines.append('')
    lines.append('## G2 判定结论（§12）')
    lines.append('')
    lines.append('### 1) 质量门（金样对象/属性/链接全命中 + D 较 A precision/recall 不降超 2pp）')
    for row in g2['qualityGate']['rows']:
        if row['verdict'] == 'not_run':
            lines.append('- %s：未跑（%s）' % (row['sample'], row['detail']))
        else:
            lines.append('- %s：%s%s' % (
                row['sample'], '通过' if row['verdict'] == 'pass' else '未过',
                ('（未过项：%s）' % '、'.join(row.get('failedChecks') or [])
                 if row.get('failedChecks') else '')))
    lines.append('')
    lines.append('### 2) compact 收益门（B 较 A 正文 token 中位数降低 ≥20%；unknown 不可证）')
    lines.append('- 判定：%s' % g2['compactBenefitGate']['verdict'])
    lines.append('- 说明：%s' % g2['compactBenefitGate']['detail'])
    lines.append('')
    lines.append('### 3) 成本取舍（D 总 token 较 A；+10% 以上保留待用户决定）')
    lines.append('- 判定：%s' % g2['costTradeoff']['verdict'])
    lines.append('- 说明：%s' % g2['costTradeoff']['detail'])
    lines.append('')
    lines.append('## 各臂汇总')
    lines.append('')
    lines.append('| 臂 | codec | planner | calls | 已知 completion | unknown | precision | recall'
                 ' | 结构问题 |')
    lines.append('|---|---|---|---|---|---|---|---|---|')
    for arm in sorted(arms):
        entry = arms[arm]
        usage = entry.get('usage') or {}
        quality = entry.get('quality') or {}
        lines.append('| %s | %s | %s | %s | %s | %s | %s | %s | %s |' % (
            arm,
            (entry.get('config') or {}).get('codec') or adapter.ARM_CODEC.get(arm, ''),
            planner_of.get(arm, ''),
            usage.get('calls', 0), usage.get('knownCompletionTokens', 0),
            usage.get('unknownUsageCalls', 0),
            quality.get('precision'), quality.get('recall'),
            quality.get('structuralIssueCount', 0)))
    lines.append('')
    if notes:
        lines.append('## 备注 / 未覆盖项')
        lines.append('')
        for note in notes:
            lines.append('- %s' % note)
        lines.append('')
    return '\n'.join(lines) + '\n'


def _fake_model_factory(script):
    def factory():
        return adapter.FakeModel(script=script)
    return factory


def _real_model_factory(provider):
    def factory():
        return adapter.RealModel(provider)
    return factory


def main(argv=None):
    parser = _build_parser()
    try:
        args = parser.parse_args(list(argv) if argv is not None else None)
    except ArgError as exc:
        print('[参数错误] %s' % exc)
        print('用法：python3 -m experiments.ontology_token_pilot --config <路径> '
              '--output-dir <目录> --mode fake [--arms A,B,C,D] [--repeats 1] [--samples ...]')
        return EXIT_ARGS

    try:
        config = adapter.load_config(args.config)
    except adapter.ConfigError as exc:
        print('[参数错误] %s' % exc)
        return EXIT_ARGS

    try:
        arms = _parse_list(args.arms, adapter.ARMS, '--arms')
        samples = _parse_list(args.samples if args.samples is not None
                              else ','.join(config['sampleIds']),
                              set(config['sampleIds']), '--samples')
        if args.repeats < 1:
            raise ArgError('--repeats 需要 ≥1，实际 %r' % args.repeats)
    except ArgError as exc:
        print('[参数错误] %s' % exc)
        return EXIT_ARGS

    notes = []
    blocked = False
    model_factory = None
    if args.mode == 'real':
        try:
            provider = adapter.resolve_real_provider(config['providerId'])
            model_factory = _real_model_factory(provider)
            notes.append('real 模式：provider 已解析（model=%s）；key 只留内存，不入报告'
                         % adapter.RealModel(provider).model_name)
        except Exception as exc:   # 模型不可用 → 受阻（退出 2），报告仍输出
            blocked = True
            notes.append('real 模式 provider 不可用，未派发任何真实调用：%s' % exc)
    else:
        model_factory = _fake_model_factory(list(config.get('fakeScript') or []))

    results = []
    for sample in samples:
        for arm in arms:
            for repeat in range(1, args.repeats + 1):
                label = '%s/%s/r%d' % (sample, arm, repeat)
                if model_factory is None:
                    notes.append('%s：未派发（provider 不可用）' % label)
                    continue
                try:
                    result = adapter.run_arm(config, sample, arm, repeat,
                                             model_factory(), args.output_dir)
                except Exception as exc:   # 持久化/执行器异常：记受阻，不中断剩余矩阵
                    blocked = True
                    traceback.print_exc()
                    notes.append('%s：运行异常受阻：%s' % (label, exc))
                    continue
                results.append(result)
                meta = result.get('meta') or {}
                if meta.get('skippedExisting'):
                    notes.append('%s：已有终态结果，续跑跳过（campaign 累计不重置）' % label)
                    if result.get('runState') != 'succeeded':
                        # 续跑报告必须如实保留历史受阻/失败结论（不因跳过而消失）
                        blocked = True
                        message = result.get('blocking') or {}
                        notes.append('%s：历史终态 %s%s' % (
                            label, result.get('runState'),
                            '（%s：%s）' % (message.get('code'), message.get('message'))
                            if message.get('code') else ''))
                    continue
                print('[run] %s -> %s（物理调用 %s，缓存命中 %s）'
                      % (label, result.get('runState'), meta.get('physicalCalls'),
                         meta.get('cacheHits')))
                if result.get('runState') != 'succeeded':
                    blocked = True
                    message = result.get('blocking') or {}
                    notes.append('%s：%s%s' % (
                        label, result.get('runState'),
                        '（%s：%s）' % (message.get('code'), message.get('message'))
                        if message.get('code') else ''))

    ledgers_by_arm = {}
    evals_by_arm = {}
    for result in results:
        arm = str(result.get('arm'))
        ledger = ledgers_by_arm.setdefault(arm, evaluate.UsageLedger())
        for item in (result.get('ledgerAttempts') or []):
            ledger.add_attempt(item.get('attemptId'), job_id=item.get('jobId'),
                               usage=item.get('usage'), finish_reason=item.get('finishReason'),
                               error_code=item.get('errorCode'),
                               error_message=item.get('errorMessage'),
                               duration_ms=item.get('durationMs'),
                               prompt_bytes=item.get('promptBytes') or 0,
                               state=item.get('state'))
        evals_by_arm.setdefault(arm, []).extend(result.get('quality') or [])

    report_config = {
        'campaignId': config['campaignId'],
        'mode': args.mode,
        'repeats': args.repeats,
        'samples': samples,
        'providerId': config['providerId'],
        'profile': config['profile'],
        'campaignBudget': config['campaignBudget'],
        'arms': {arm: {'codec': adapter.ARM_CODEC[arm],
                       'planner': adapter.ARM_PLANNER[arm]} for arm in arms},
    }
    report = evaluate.build_report(report_config, ledgers_by_arm, evals_by_arm, notes)
    g2 = evaluate.redact_report(_g2_verdicts(results, report))
    report['g2'] = g2
    os.makedirs(args.output_dir, exist_ok=True)
    report_path = os.path.join(args.output_dir, 'report.json')
    with open(report_path, 'w', encoding='utf-8') as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    md_path = os.path.join(args.output_dir, 'report.md')
    with open(md_path, 'w', encoding='utf-8') as handle:
        handle.write(_render_markdown(report, g2, results, notes))

    succeeded = sum(1 for result in results if result.get('runState') == 'succeeded')
    print('[报告] %s' % report_path)
    print('[报告] %s' % md_path)
    print('[汇总] 成功 %d / %d 次 run' % (succeeded, len(results)))
    if blocked or not results or succeeded != len(results):
        return EXIT_BLOCKED
    return EXIT_OK


if __name__ == '__main__':
    sys.exit(main())
