# -*- coding: utf-8 -*-
"""结果索引核对器自测（deep_results_index）：替代关系、覆盖、拆分语义（F04 锁定）。

运行：.runtime/venv/bin/python tests/deep_results_index_test.py   （纯逻辑，不连服务）
说明：只验证「核对器对给定输入是否正确判定」，用的是内存构造的批次，不代表真实日志结论；
真实批次结论以 tests/deep_results_index.py 对 .runtime/test-evidence 的重算为准。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import deep_results_index as M  # noqa: E402

CASES = []


def case(name, fn, want, note=''):
    try:
        got = fn()
        ok = got == want
        detail = str(got)
    except AssertionError as exc:
        got = 'AssertionError'
        ok = (want == 'reject')
        detail = str(exc)[:160]
    CASES.append({'name': name, 'ok': ok, 'want': want, 'got': got, 'note': note})
    print('%s %-52s -> %s %s' % ('✓' if ok else '✗', name, got, ('| ' + detail) if detail else ''))


def batches(superseded_cases, valid_cases, file='x.jsonl', kind_s='biz', kind_v='biz',
            result_v='product_pass', mixed=False):
    """构造 [被替代批次, 有效批次]。

    有效批次条目传 dict 时按逐行覆盖默认值（G03：需要一条真实业务行 + 一条 info 行共存），
    其余行为与原先一致（传字符串即用 kind_v/result_v 默认值）。
    """
    def sup_rows():
        rows = []
        for c in superseded_cases:
            if mixed and isinstance(c, dict):
                rows.append(dict(c))
            else:
                rows.append({'caseId': c, 'kind': kind_s})
        return rows

    def val_rows():
        rows = []
        for c in valid_cases:
            if isinstance(c, dict):
                row = {'result': result_v, 'kind': kind_v}
                row.update(c)
                rows.append(row)
            else:
                rows.append({'caseId': c, 'result': result_v, 'kind': kind_v})
        return rows

    return [
        {'file': file, 'runLabel': 'sup', 'status': 'superseded', 'cases': sup_rows()},
        {'file': file, 'runLabel': 'val', 'status': 'valid', 'cases': val_rows()},
    ]


def uncovered():
    return [b['uncovered'] for b in M.validate_superseded_coverage(batches(['A'], ['A']))]


def split_missing(cid, valid_cases, split_of=None):
    """返回某拆分编号在给定有效批次下缺失的子用例（用于断言缺失细节可读）。"""
    saved = M.CASE_ID_SPLITS.get(cid)
    if split_of is not None:
        M.CASE_ID_SPLITS[cid] = set(split_of)
    try:
        try:
            M.validate_superseded_coverage(batches([cid], valid_cases))
            return []
        except AssertionError as exc:
            text = str(exc)
            return [part for part in sorted((split_of or saved or ())) if part in text]
    finally:
        if split_of is not None:
            if saved is None:
                M.CASE_ID_SPLITS.pop(cid, None)
            else:
                M.CASE_ID_SPLITS[cid] = saved


# --- 基本覆盖 ---------------------------------------------------------------
case('普通场景已覆盖 → 通过', lambda: [b['uncovered'] for b in
                                    M.validate_superseded_coverage(batches(['A'], ['A']))], [[]])
case('普通场景未覆盖 → 报错拒绝',
     lambda: M.validate_superseded_coverage(batches(['A', 'LOST'], ['A'])), 'reject')
case('crash 行：记录声明为工具错误时允许无覆盖且被列出',
     lambda: M.validate_superseded_coverage(
         batches([{'caseId': 'A', 'kind': 'biz'},
                  {'caseId': 'I9-crash', 'kind': '', 'title': 'I9 套件异常中断',
                   'evidence': 'Traceback (most recent call last): ...'}],
                 ['A'], mixed=True))[0]['toolErrorCrashRows'],
     ['I9-crash'])

# --- F04：一拆多必须全部子用例都在 ------------------------------------------
case('F04 拆分只保留 a → 报错拒绝',
     lambda: M.validate_superseded_coverage(batches(['O4-07'], ['O4-07a'])), 'reject')
case('F04 拆分只保留 b → 报错拒绝',
     lambda: M.validate_superseded_coverage(batches(['O4-07'], ['O4-07b'])), 'reject')
case('F04 拆分 a+b 都在 → 通过',
     lambda: M.validate_superseded_coverage(batches(['O4-07'], ['O4-07a', 'O4-07b']))[0]['splitCoverage'],
     [{'superseded': 'O4-07', 'required': ['O4-07a', 'O4-07b'], 'missing': []}])
case('F04 缺项细节可读（缺 O4-07b）', lambda: split_missing('O4-07', ['O4-07a']), ['O4-07b'])

# --- 独立验证发现的残余豁免通道（已加固并锁定） --------------------------------
case('业务场景名含 crash 但记录未声明工具错误 → 报错拒绝（不得按名字豁免）',
     lambda: M.validate_superseded_coverage(
         batches([{'caseId': 'BizCrashRecovery', 'kind': 'biz', 'title': '业务场景',
                   'evidence': '正常证据'}], [], mixed=True)), 'reject')
case('名字含 crash 且 kind=异常中断 → 允许无覆盖并列出',
     lambda: M.validate_superseded_coverage(
         batches(['I7-crashRecovery'], [], kind_s='异常中断'))[0]['toolErrorCrashRows'],
     ['I7-crashRecovery'])
case('名字平常但有真堆栈证据 → 允许无覆盖（按证据而非名字）',
     lambda: M.validate_superseded_coverage(
         batches([{'caseId': 'SomeFailure', 'kind': '', 'title': '套件异常中断',
                   'evidence': 'Traceback (most recent call last): ...'}], [], mixed=True))[0]['toolErrorCrashRows'],
     ['SomeFailure'])
case('有效批次仅有 info 说明行 → 不得算业务覆盖',
     lambda: M.validate_superseded_coverage(
         batches(['X-01'], ['X-01'], kind_v='info', result_v='info')), 'reject')
case('有效批次是真实业务判定行 → 通过（对照）',
     lambda: M.validate_superseded_coverage(batches(['X-01'], ['X-01']))[0]['uncovered'], [])

# --- 任选其一语义仍可用 ------------------------------------------------------
M.CASE_ID_ALTERNATIVES['ALT'] = {'X1', 'X2'}
case('等价编号任选其一 → 通过',
     lambda: M.validate_superseded_coverage(batches(['ALT'], ['X1']))[0]['uncovered'], [])
case('等价编号全部缺失 → 报错拒绝',
     lambda: M.validate_superseded_coverage(batches(['ALT'], ['X9'])), 'reject')

# --- G03：info 不能冒充业务覆盖（三条路径共用同一过滤，独立验收反例锁定） --------
def missing_parts(named, fn):
    """执行 fn，从 AssertionError 文本里挑出被点名的编号（检查报错可读性）；未报错返回 []。"""
    try:
        fn()
    except AssertionError as exc:
        text = str(exc)
        return [part for part in named if part in text]
    return []


G03_BOTH_INFO = batches(['O4-07'], ['O4-07a', 'O4-07b'], kind_v='info', result_v='info')
G03_A_BIZ_B_INFO = batches(['O4-07'], [{'caseId': 'O4-07a'},
                                       {'caseId': 'O4-07b', 'kind': 'info', 'result': 'info'}])
G03_BOTH_BIZ = batches(['O4-07'], [{'caseId': 'O4-07a', 'result': 'product_pass', 'kind': 'biz'},
                                   {'caseId': 'O4-07b', 'result': 'known_defect_reproduced', 'kind': 'biz'}])
M.CASE_ID_ALTERNATIVES['ALT-INFO'] = {'Y1', 'Y2'}

case('G03 拆分路径：a+b 两行都是 info → 报错拒绝',
     lambda: M.validate_superseded_coverage(G03_BOTH_INFO), 'reject')
case('G03 拆分路径：a 真实 + b info → 报错拒绝（缺真业务证据）',
     lambda: M.validate_superseded_coverage(G03_A_BIZ_B_INFO), 'reject')
case('G03 拆分路径：a 真实 + b info → 报错点名缺 O4-07b',
     lambda: missing_parts(['O4-07b'], lambda: M.validate_superseded_coverage(G03_A_BIZ_B_INFO)),
     ['O4-07b'])
case('G03 任选其一路径：只有 info 行命中 → 报错拒绝',
     lambda: M.validate_superseded_coverage(
         batches(['ALT-INFO'], ['Y1'], kind_v='info', result_v='info')), 'reject')
case('G03 对照：拆分 a+b 都是真实业务行 → 通过',
     lambda: M.validate_superseded_coverage(G03_BOTH_BIZ)[0]['uncovered'], [])
case('G03 对照：同编号真实业务行 → 通过（主路径未改坏）',
     lambda: M.validate_superseded_coverage(batches(['X-01'], ['X-01']))[0]['uncovered'], [])
case('G03 对照：同编号既有 info 又有业务行 → 通过（只过滤 info，不误伤）',
     lambda: M.validate_superseded_coverage(
         batches(['X-01'], [{'caseId': 'X-01', 'kind': 'info', 'result': 'info'},
                            {'caseId': 'X-01', 'kind': 'biz', 'result': 'product_pass'}]))[0]['uncovered'],
     [])
case('G03 对照：被替代批次自身的 info 说明行 → 列入 infoOnlyRows 且不报错',
     lambda: [(b['uncovered'], b['infoOnlyRows']) for b in M.validate_superseded_coverage(
         batches([{'caseId': 'O7-00', 'kind': 'info', 'legacyVerdict': 'info'},
                  {'caseId': 'O7-01', 'kind': 'biz'}], ['O7-01'], mixed=True))],
     [([], ['O7-00'])])
case('G03 对照：被替代批次 info 行不豁免真业务场景（info + 未覆盖业务行 → 报错）',
     lambda: M.validate_superseded_coverage(
         batches([{'caseId': 'O7-00', 'kind': 'info', 'legacyVerdict': 'info'},
                  {'caseId': 'O7-99', 'kind': 'biz'}], ['O7-00'], mixed=True)), 'reject')

# --- 引用行号核对（M03 曾实测有效，一并锁定） --------------------------------
case('引用 caseId 与实际不一致 → 报错拒绝',
     lambda: M._resolve_ref({'f.jsonl': [{'case': 'REAL'}]}, 'f.jsonl#1(EXPECT)'), 'reject')
case('引用行号越界 → 报错拒绝',
     lambda: M._resolve_ref({'f.jsonl': [{'case': 'A'}]}, 'f.jsonl#999(A)'), 'reject')
case('引用正确 → 返回该行',
     lambda: M._resolve_ref({'f.jsonl': [{'case': 'REAL'}]}, 'f.jsonl#1(REAL)').get('case'), 'REAL')


# --- S1：业务证据必须是「真实业务判定行」（独立对抗验证发现的绕过通道） -------------
def row(cid, kind='biz', result='product_pass', **extra):
    """构造一条有效批次行（含 kind/result），与对抗验证给的复现写法一致。"""
    item = {'caseId': cid, 'kind': kind, 'result': result}
    item.update(extra)
    return item


def raw_batches(sup_rows, val_rows, file='x.jsonl', sup_label='sup', val_label='val'):
    """直接给出两侧原始行（不填充默认值），用于验证「缺键」等边界。"""
    return [{'file': file, 'runLabel': sup_label, 'status': 'superseded', 'cases': sup_rows},
            {'file': file, 'runLabel': val_label, 'status': 'valid', 'cases': val_rows}]


def outcome(fn):
    """执行 fn：报错（AssertionError）返回 'reject'，否则返回结果本身。"""
    try:
        return fn()
    except AssertionError:
        return 'reject'


REAL_SPLIT_FILE = 'q02/o3o4o5-rules-publish.jsonl'
REAL_SPLIT_LABEL = 'q02-o3o4o5-1'


def real_split_rows(a_result, b_result, a_kind='blocked', b_kind='biz'):
    """真实数据形态：被替代批次 O4-07，有效批次 O4-07a / O4-07b 两行。"""
    return raw_batches([row('O4-07')],
                       [row('O4-07a', a_kind, a_result), row('O4-07b', b_kind, b_result)],
                       file=REAL_SPLIT_FILE, sup_label=REAL_SPLIT_LABEL)


case('S1 复现①：拆分 b 是工具错误行（异常中断/test_error）→ 不得算证据，报错拒绝',
     lambda: M.validate_superseded_coverage(
         raw_batches([row('O4-07')], [row('O4-07a'), row('O4-07b', '异常中断', 'test_error')])),
     'reject')
case('S1 复现②：有效行缺 result 键 → 不得当业务证据，报错拒绝',
     lambda: M.validate_superseded_coverage(
         raw_batches([row('X-01')], [{'caseId': 'X-01', 'kind': 'biz'}])), 'reject')
case('S1 复现③：kind 前导空格变体（" info"）→ 不得当业务证据，报错拒绝',
     lambda: M.validate_superseded_coverage(
         raw_batches([row('X-01')], [row('X-01', ' info')])), 'reject')
case('S1 大小写变体（"INFO "）→ 不得当业务证据，报错拒绝',
     lambda: M.validate_superseded_coverage(
         raw_batches([row('X-01')], [row('X-01', 'INFO ')])), 'reject')
case('S1 对照：有效行缺 kind 键但 result 正常 → 仍算业务证据（通过，不误伤）',
     lambda: M.validate_superseded_coverage(
         raw_batches([row('X-01')], [{'caseId': 'X-01', 'result': 'product_pass'}]))[0]['uncovered'],
     [])
case('S1 result=blocked（场景没跑完）→ 不得当业务证据，报错拒绝',
     lambda: M.validate_superseded_coverage(
         raw_batches([row('X-01')], [row('X-01', 'biz', 'blocked')])), 'reject')
case('S1 result 为 test_error/not_tested/空串/未知取值 → 一律不得当业务证据',
     lambda: [outcome(lambda bad=bad: M.validate_superseded_coverage(
         raw_batches([row('X-01')], [row('X-01', 'biz', bad)])))
         for bad in ('test_error', 'not_tested', '', 'pass', 'PRODUCT_PASS')],
     ['reject', 'reject', 'reject', 'reject', 'reject'])
case('S1 对照：四类真实业务判定结果算证据，其余取值不算',
     lambda: [M.is_business_evidence_row(row('X-01', 'biz', v)) for v in
              ('product_pass', 'known_defect_reproduced', 'new_defect_reproduced',
               'static_check_pass', 'test_error', 'blocked', 'not_tested', 'info')],
     [True, True, True, True, False, False, False, False])
case('S1 对照：有 result 但缺 caseId → 不得当业务证据',
     lambda: M.is_business_evidence_row({'kind': 'biz', 'result': 'product_pass'}), False)
case('S1 被替代侧用严格相等：kind="information" 不得被当成说明行豁免覆盖 → 报错拒绝',
     lambda: M.validate_superseded_coverage(
         raw_batches([{'caseId': 'O7-99', 'kind': 'information'}], [row('O7-01')])), 'reject')
case('S1 有效侧保守判定：kind="information" 不算业务证据 → 报错拒绝（只报错不放过）',
     lambda: M.validate_superseded_coverage(
         raw_batches([row('X-01')], [row('X-01', 'information')])), 'reject')
case('S1 被替代批次的 "infO " 变体说明行 → 列入 infoOnlyRows 且不报错',
     lambda: [(b['uncovered'], b['infoOnlyRows']) for b in M.validate_superseded_coverage(
         raw_batches([{'caseId': 'O7-00', 'kind': 'infO ', 'legacyVerdict': 'info'},
                      {'caseId': 'O7-01', 'kind': 'biz'}], [row('O7-01')]))],
     [([], ['O7-00'])])
M.CASE_ID_ALTERNATIVES['ALT-BLOCKED'] = {'Z1', 'Z2'}
case('S1 别名路径：命中的等价编号只有 blocked 行 → 不得算证据，报错拒绝',
     lambda: M.validate_superseded_coverage(
         raw_batches([row('ALT-BLOCKED')], [row('Z1', 'biz', 'blocked')])), 'reject')

# --- S2：fail 行不得因 kind 含 info 被降级成说明行 ---------------------------------
case('S2 fail 行 + kind=info → 不得降级为 info（判为缺陷复现）',
     lambda: M.classify_row({'case': 'X-01', 'verdict': 'fail', 'kind': 'info'}, 'x.jsonl', 1),
     'new_defect_reproduced')
case('S2 blocked 行 + kind=info → 仍判 blocked（不得降级为 info）',
     lambda: M.classify_row({'case': 'X-01', 'verdict': 'blocked', 'kind': 'info'}, 'x.jsonl', 1),
     'blocked')
case('S2 known 行 + kind=info → 仍判已知缺陷复现（不得降级为 info）',
     lambda: M.classify_row({'case': 'X-01', 'verdict': 'known', 'kind': 'info'}, 'x.jsonl', 1),
     'known_defect_reproduced')
case('S2 真正的说明行（verdict=info）→ 仍判 info（对照，语义未被改坏）',
     lambda: M.classify_row({'case': 'X-01', 'verdict': 'info', 'kind': 'biz'}, 'x.jsonl', 1), 'info')
case('S2 kind=info 且 verdict=pass → 仍按说明行计 info（兼容分支保留）',
     lambda: M.classify_row({'case': 'X-01', 'verdict': 'pass', 'kind': 'info'}, 'x.jsonl', 1), 'info')
case('S2 未知 verdict → 报错退出（枚举外不得悄悄算通过）',
     lambda: M.classify_row({'case': 'X-01', 'verdict': 'wat', 'kind': 'biz'}, 'x.jsonl', 1), 'reject')

# --- S3：显式登记的「未确认」清单（F04「无法归属项标未确认」口径） ------------------
case('S3 登记表可审计：真实 O4-07a 缺口键存在，原因写明阻塞行非业务证据与 O4-07b 已覆盖',
     lambda: [M.COVERAGE_UNCONFIRMED.get(
                 (REAL_SPLIT_FILE, REAL_SPLIT_LABEL, 'O4-07', 'O4-07a')) is not None,
              all(word in M.COVERAGE_UNCONFIRMED.get(
                  (REAL_SPLIT_FILE, REAL_SPLIT_LABEL, 'O4-07', 'O4-07a'), '')
                  for word in ('blocked', 'O4-07b', '未确认'))],
     [True, True])
case('S3 真实数据形态（a=blocked、b=pass）：b 已覆盖、a 列为未确认、splitCoverage 标出 missing',
     lambda: [(b['uncovered'], b['splitCoverage'], [(u['case'], u['missing']) for u in b['unconfirmed']])
              for b in M.validate_superseded_coverage(
                  real_split_rows('blocked', 'product_pass'))],
     [([], [{'superseded': 'O4-07', 'required': ['O4-07a', 'O4-07b'], 'missing': ['O4-07a']}],
       [('O4-07', 'O4-07a')])])
case('S3 同一形态但未登记（换文件名）→ 报错拒绝（未确认无法由数据自动产生）',
     lambda: M.validate_superseded_coverage(
         raw_batches([row('O4-07')], [row('O4-07a', 'blocked', 'blocked'), row('O4-07b')],
                     file='unregistered.jsonl')), 'reject')
case('S3 登记的键不匹配（同文件但 runLabel 不同）→ 仍报错拒绝',
     lambda: M.validate_superseded_coverage(
         raw_batches([row('O4-07')], [row('O4-07a', 'blocked', 'blocked'), row('O4-07b')],
                     file=REAL_SPLIT_FILE, sup_label='q02-o3o4o5-OTHER')), 'reject')
case('S3 登记只豁免被登记的那个子用例：缺 b 而 a 有证据 → 报错拒绝',
     lambda: M.validate_superseded_coverage(
         raw_batches([row('O4-07')], [row('O4-07a'), row('O4-07b', 'blocked', 'blocked')],
                     file=REAL_SPLIT_FILE, sup_label=REAL_SPLIT_LABEL)), 'reject')
case('S3 未确认与「全部已覆盖」区分：§2.2 覆盖表列出未确认项与原因',
     lambda: (lambda md: [('未确认' in md), ('O4-07a' in md), ('全部已覆盖' in md),
                          ('阻塞' in md)])(
         M.render_markdown({
             'generatedAt': 'T', 'businessSha': 'x', 'supersededRawRows': 1,
             'totals': {'assertionRecords': 0, 'scenarioUniqueCases': 0, 'defectRootCauseCount': 0},
             'batches': [], 'caseReplacements': [], 'perSuite': {}, 'defectRoots': [],
             'supersededCoverage': M.validate_superseded_coverage(real_split_rows('blocked', 'product_pass')),
             'results': {'product_pass': 0}, 'reverifyRun': []})),
     [True, True, False, True])

failed = [c for c in CASES if not c['ok']]
print('\n== deep_results_index_test: %d 项，通过 %d，失败 %d =='
      % (len(CASES), len(CASES) - len(failed), len(failed)))
for item in failed:
    print('  FAIL %s: want=%s got=%s' % (item['name'], item['want'], item['got']))
sys.exit(1 if failed else 0)
