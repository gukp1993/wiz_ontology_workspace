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
    def sup_rows():
        rows = []
        for c in superseded_cases:
            if mixed and isinstance(c, dict):
                rows.append(dict(c))
            else:
                rows.append({'caseId': c, 'kind': kind_s})
        return rows

    return [
        {'file': file, 'runLabel': 'sup', 'status': 'superseded', 'cases': sup_rows()},
        {'file': file, 'runLabel': 'val', 'status': 'valid',
         'cases': [{'caseId': c, 'result': result_v, 'kind': kind_v} for c in valid_cases]},
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

# --- 引用行号核对（M03 曾实测有效，一并锁定） --------------------------------
case('引用 caseId 与实际不一致 → 报错拒绝',
     lambda: M._resolve_ref({'f.jsonl': [{'case': 'REAL'}]}, 'f.jsonl#1(EXPECT)'), 'reject')
case('引用行号越界 → 报错拒绝',
     lambda: M._resolve_ref({'f.jsonl': [{'case': 'A'}]}, 'f.jsonl#999(A)'), 'reject')
case('引用正确 → 返回该行',
     lambda: M._resolve_ref({'f.jsonl': [{'case': 'REAL'}]}, 'f.jsonl#1(REAL)').get('case'), 'REAL')

failed = [c for c in CASES if not c['ok']]
print('\n== deep_results_index_test: %d 项，通过 %d，失败 %d =='
      % (len(CASES), len(CASES) - len(failed), len(failed)))
for item in failed:
    print('  FAIL %s: want=%s got=%s' % (item['name'], item['want'], item['got']))
sys.exit(1 if failed else 0)
