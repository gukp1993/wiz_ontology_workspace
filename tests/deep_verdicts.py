# -*- coding: utf-8 -*-
"""深度测试统一结果分类与判定函数（20260921「继续验证」修订，独立验收 S2 要求）。

背景：原判定用「响应不是成功响应即 pass」的写法，会把 500、无关 4xx、401、
网络失败、空响应体以及前置失败都写成产品通过；也会把「缺陷复现成功」当成
产品通过。本模块把「一次被期望拦截的操作 + 一个可能存在的缺陷」收口成一个
可测试的判定函数，deep_* 脚本与报告统计共用同一套结果分类。

结果分类（报告必须使用；旧枚举经 LEGACY_MAP 映射，映射表在报告里列明）：

  product_pass             真实业务通过：真实 HTTP、前置成功、期望命中
  known_defect_reproduced  已知基线缺陷复现（不是产品通过）
  new_defect_reproduced    本轮新发现缺陷复现（不是产品通过）
  static_check_pass        静态/白名单/源码核对通过（非动态业务验证）
  test_error               测试工具错误/构造错误/无法归因（结果不可用）
  blocked                  前置失败或环境阻塞，用例未成立
  not_tested               明确未验证（无可达入口/未执行）
  info                     说明性记录，不计入通过数

判定顺序 classify_guard_attempt（R1→R7）：

  R1 前置未成功                     → blocked
  R2 传输失败/无状态码/空响应体      → test_error
  R3 status >= 500                  → test_error
  R4 status == 期望拦截码：
       命中诊断词 → product_pass；否则 → test_error（拒绝理由与目标无关）
  R5 其他 4xx（401/403/404/413/429…）→ test_error（不能当类型/依赖校验正确）
  R6 2xx 被接受：
       require_no_version_increase 且版本增加 → 缺陷复现（未拦截且已发布）
       否则 → 缺陷复现（未拦截）
       已知缺陷 → known_defect_reproduced，否则 new_defect_reproduced
  R7 其他状态码                      → test_error（无法归因）

只依赖标准库；供 tests/deep_* 脚本与 tests/deep_results_index.py 共用。
"""
import sys

PRODUCT_PASS = 'product_pass'
KNOWN_DEFECT = 'known_defect_reproduced'
NEW_DEFECT = 'new_defect_reproduced'
STATIC_PASS = 'static_check_pass'
TEST_ERROR = 'test_error'
BLOCKED = 'blocked'
NOT_TESTED = 'not_tested'
INFO = 'info'

ALL_RESULTS = (PRODUCT_PASS, KNOWN_DEFECT, NEW_DEFECT, STATIC_PASS, TEST_ERROR, BLOCKED, NOT_TESTED, INFO)

# 旧枚举 → 新分类。旧脚本里的 pass/known/fail 含义不唯一（fail 可能是缺陷复现，
# 也可能是工具错误），这里只给「字段级」映射；具体行若 kind 说明为工具错误，
# 报告必须按 test_error 单列（见结果索引的 reclassify 字段）。
LEGACY_MAP = {
    'pass': PRODUCT_PASS,
    'known': KNOWN_DEFECT,
    'fail': NEW_DEFECT,
    'blocked': BLOCKED,
    'untested': NOT_TESTED,
    'info': INFO,
}

# 计入「通过」的分类：动态产品通过与静态核对通过分开统计，不相加。
PASS_RESULTS = (PRODUCT_PASS, STATIC_PASS)

# 不能作为「正确拦截」证据的状态码：要么服务端内部错误，要么与目标校验无关。
UNRELATED_4XX = (400, 401, 403, 404, 405, 409, 413, 415, 422, 429)


def to_result(verdict):
    """旧枚举或新分类 → 新分类（未知值原样返回，便于发现拼写错误）。"""
    return LEGACY_MAP.get(verdict, verdict)


def is_pass(result):
    return result in PASS_RESULTS


def counts(results):
    """按新分类统计（列表或字典计数值）：返回 {分类: 数量}，缺失分类补 0。"""
    out = {key: 0 for key in ALL_RESULTS}
    if isinstance(results, dict):
        for key, value in results.items():
            out[to_result(key)] = out.get(to_result(key), 0) + value
    else:
        for item in results:
            key = to_result(item if isinstance(item, str) else item.get('result') or item.get('verdict'))
            out[key] = out.get(key, 0) + 1
    return out


def verdict_line(results):
    """一行可粘贴的统计文案，顺序固定。"""
    stat = counts(results)
    parts = ['%s=%d' % (key, stat.get(key, 0)) for key in ALL_RESULTS if stat.get(key)]
    return ' '.join(parts) if parts else '(空)'


def _body_present(response):
    if response.get('json') is not None:
        return True
    return bool(response.get('binary')) or bool(str(response.get('text') or '').strip())


def _diag_hit(response, terms):
    if not terms:
        return True
    blob = ''
    if response.get('json') is not None:
        import json
        blob += json.dumps(response['json'], ensure_ascii=False, default=str)
    blob += ' ' + str(response.get('text') or '')
    return any(term in blob for term in terms)


def _mk(result, reason, detail=''):
    return {'result': result, 'reason': reason, 'detail': detail}


def classify_prereq(name, ok, detail=''):
    """前置步骤判定：成功 → product_pass；失败 → blocked（用例不成立，不用旧状态继续）。"""
    if ok:
        return _mk(PRODUCT_PASS, '前置成功：' + name, detail)
    return _mk(BLOCKED, '前置未成功：' + name + '；该用例不成立，不用旧状态继续', detail)


def classify_static_check(ok, detail='', defect_kind=NEW_DEFECT):
    """静态/白名单/源码核对：通过记 static_check_pass（不是动态业务验证）。"""
    if ok:
        return _mk(STATIC_PASS, '静态核对通过（非动态业务验证）', detail)
    return _mk(defect_kind, '静态核对发现可达路径（需人工确认是否为缺陷）', detail)


def classify_guard_attempt(precondition_ok, response, expect=None, precondition_detail=''):
    """一次「期望被拦截」的操作判定。

    precondition_ok : bool|dict  前置是否全部成功（dict 需含 ok；False 一律 blocked）
    response        : dict       客户端 call() 结果 {status,json,text,binary,error}
    expect          : dict
        block_status            期望拦截状态码（int 或 tuple，默认 422）
        diagnostic_terms        诊断词列表（任一子串命中才算「针对目标字段/依赖」）
        known_defect            True 表示未拦截时按已知基线缺陷记（默认 False）
        require_no_version_increase  True 时接受分支同时检查版本是否新增
        version_before/version_after 版本计数（int 或可比较 token 列表长度）
        allow_status            接受态状态码（默认 (200, 201)）
        accepted_detail         接受时写进 evidence 的补充说明
    """
    expect = dict(expect or {})
    if isinstance(precondition_ok, dict):
        precondition_detail = precondition_detail or precondition_ok.get('detail', '')
        precondition_ok = bool(precondition_ok.get('ok'))
    if not precondition_ok:
        return _mk(BLOCKED, '前置未成功；用例不成立，不用旧状态继续该操作', precondition_detail)

    response = response or {}
    status = response.get('status')
    block_status = expect.get('block_status', 422)
    block_set = tuple(block_status) if isinstance(block_status, (list, tuple, set)) else (block_status,)
    allow_status = tuple(expect.get('allow_status', (200, 201)))
    terms = list(expect.get('diagnostic_terms') or [])
    known = bool(expect.get('known_defect'))
    detail = str(expect.get('accepted_detail') or '')

    if status is None:
        return _mk(TEST_ERROR, '网络失败/无状态码，不能判定为产品通过',
                   '%s %s' % (response.get('error') or '', detail))
    if not _body_present(response):
        return _mk(TEST_ERROR, '响应体为空或不可解析，无法判定结果', 'status=%s %s' % (status, detail))
    if int(status) >= 500:
        return _mk(TEST_ERROR, '服务端 %s，不能作为业务拦截或产品通过' % status, detail)

    if int(status) in block_set:
        if _diag_hit(response, terms):
            return _mk(PRODUCT_PASS, '期望拦截命中：HTTP %s 且诊断针对目标' % status, detail)
        return _mk(TEST_ERROR, '被拒绝但诊断与目标字段/依赖无关（HTTP %s），不能算校验正确' % status, detail)

    if int(status) not in allow_status and 400 <= int(status) < 500:
        return _mk(TEST_ERROR, '无关 4xx（HTTP %s）：身份/路由/请求形态问题，不能当业务拦截' % status, detail)

    if int(status) in allow_status:
        if expect.get('require_no_version_increase'):
            before, after = expect.get('version_before'), expect.get('version_after')
            if before is not None and after is not None and after > before:
                return _mk(KNOWN_DEFECT if known else NEW_DEFECT,
                           '缺陷复现：未被拦截且已发布新版本（%s→%s）' % (before, after), detail)
        return _mk(KNOWN_DEFECT if known else NEW_DEFECT, '缺陷复现：操作被接受（HTTP %s），未被拦截' % status, detail)

    return _mk(TEST_ERROR, '状态码 %s 无法归因' % status, detail)


def run_metadata(repo, script_path=None, run_id=None):
    """本轮运行元数据：runId/时间/业务SHA/脚本哈希/工作区差异（结果索引必需字段）。

    业务 SHA 取「最后一次改动 workbench 或 frontend 的提交」——测试分支上业务代码
    零修改时它等于被验基线；同时记录 HEAD 与业务工作区是否脏，避免用测试提交冒充业务基线。
    """
    import hashlib
    import os
    import subprocess
    from datetime import datetime, timezone

    repo = str(repo)

    def git(*args):
        try:
            out = subprocess.run(['git', '-C', repo] + list(args), stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL, timeout=20)
            return out.stdout.decode('utf-8', 'replace').strip()
        except Exception:  # noqa: BLE001  测试元数据缺失不影响用例本身
            return ''

    script = str(script_path) if script_path else (sys.argv[0] if sys.argv else '')
    script_sha = ''
    if script:
        try:
            with open(script, 'rb') as fh:
                script_sha = hashlib.sha256(fh.read()).hexdigest()
        except OSError:
            script_sha = ''
    stamp = datetime.now(timezone.utc)
    return {
        'runId': run_id or os.environ.get('DEEP_RUN_ID') or 'adhoc-' + stamp.strftime('%Y%m%dT%H%M%SZ'),
        'ts': stamp.isoformat(),
        'repoHead': git('rev-parse', 'HEAD'),
        'businessSha': git('log', '-1', '--format=%H', '--', 'workbench', 'frontend'),
        'businessDirty': bool(git('status', '--porcelain', '--', 'workbench', 'frontend', 'tests')),
        'scriptPath': script,
        'scriptSha256': script_sha,
    }


def summarize_rows(rows):
    """对一批 JSONL 记录按「新分类」汇总：{'result': n} + 分组明细。

    行内 result 字段优先；没有时按 verdict + kind 推断（kind 含 error/crash 视为 test_error）。
    """
    out = {}
    detail = []
    for row in rows:
        result = row.get('result')
        if not result:
            kind = str(row.get('kind') or '')
            if row.get('verdict') == 'fail' and ('error' in kind or 'crash' in kind):
                result = TEST_ERROR
            else:
                result = to_result(row.get('verdict'))
        out[result] = out.get(result, 0) + 1
        detail.append({'case': row.get('case'), 'result': result, 'kind': row.get('kind') or ''})
    return out, detail


def record(run_meta, case_id, result, title, evidence='', kind='', analysis_unit='scenario',
           root_cause='', replacement='', line_no=None):
    """构造一条带运行元数据的证据行（每次新跑写独立 runId 文件，不追加覆盖旧 JSONL）。"""
    return {
        'runId': run_meta.get('runId'), 'ts': run_meta.get('ts'),
        'businessSha': run_meta.get('businessSha'), 'repoHead': run_meta.get('repoHead'),
        'businessDirty': run_meta.get('businessDirty'),
        'scriptPath': run_meta.get('scriptPath'), 'scriptSha256': run_meta.get('scriptSha256'),
        'case': case_id, 'result': result, 'title': title, 'evidence': str(evidence)[:4000],
        'kind': kind, 'analysisUnit': analysis_unit, 'rootCause': root_cause,
        'replaces': replacement, 'sourceLine': line_no,
    }


def write_run(path, rows):
    """写独立 runId 证据文件（覆盖同名文件，不做追加），返回路径。"""
    import json
    import os
    path = str(path)
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + '\n')
    return path
