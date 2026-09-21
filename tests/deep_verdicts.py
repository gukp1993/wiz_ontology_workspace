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
       诊断未命中（只查约定诊断字段，见 diag_text）→ test_error（拒绝理由与目标无关）
       要求版本零新增但版本不可读        → test_error（证据不足）
       拒绝却已新增版本                  → new_defect_reproduced（拦截未生效，独立缺陷）
       否则 → product_pass
  R5 其他 4xx（401/403/404/413/429…）→ test_error（不能当类型/依赖校验正确）
  R6 2xx 被接受：
       require_no_version_increase：版本不可读 → test_error；版本增加 → 缺陷复现
       否则 → 缺陷复现（未拦截）
       已知缺陷 → known_defect_reproduced，否则 new_defect_reproduced
  R7 其他状态码                      → test_error（无法归因）

诊断匹配（F03）：只从约定诊断字段（errors/diagnostics/error/report/check/items 等）
取文本，不搜索整份响应，避免回显载荷里的 "flow/类型" 等词造成误判；
diagnostic_term_groups 支持「字段定位 + 具体原因」两组都必须命中（组内 OR、组间 AND）。

校验接口观察判定用 classify_validate_observation（F02）：必须先过传输/状态码/响应结构
（errors 必须是数组）再判是否拦截，500 或结构不符一律 test_error，不得按文案记通过。

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


# 诊断结构（信封）键：其子树承载「为什么被拒」，进入后其中的文本都算诊断文案。
_DIAG_ENVELOPE = {
    'error', 'errors', 'message', 'messages', 'issues', 'issue', 'reasons', 'reason',
    'diagnostics', 'problems', 'violations', 'report', 'check', 'warnings',
    'detail', 'details', 'note', 'notes', 'items', 'fields', 'field',
    'sections', 'section', 'parameterId',
}
# 顶层描述键：不在信封里也直接算诊断文案（如顶层 error）。
_DIAG_TEXT_KEYS = _DIAG_ENVELOPE | {'code', 'codes', 'text', 'description', 'hint'}

# 诊断子树内**不参与匹配**的键：它们承载类型/标识/状态等判别值，不是错误说明。
# 例：导入冲突回显里的 `kind:'flow'`、校验条目里的 `kind:'propertySource'`，
# 若被当作诊断文案，仅凭一个 "flow" 词就会把无关错误误判成「针对目标」。
_DIAG_IGNORE_KEYS = {'state', 'data', 'payload', 'revision', 'requestId',
                     'kind', 'type', 'status', 'level', 'id', 'ids', 'key', 'keys',
                     'version', 'templateId', 'packageKey', 'suggestedName'}


def diag_text(response):
    """收集响应中「约定诊断字段」内的文本，供诊断匹配使用。

    规则（F03）：
    - 只进入诊断信封键（errors/diagnostics/error/report/check/items/issues…）的子树；
    - 信封内：字符串列表（`{"errors": ["文案"]}`）与描述键下的标量都算诊断文案，
      但 `_DIAG_IGNORE_KEYS`（类型/标识/回显载荷）一律跳过；
    - 信封外的普通键**不收集**，避免回显载荷里的词造成误判；
    - 完全取不到诊断文本时返回空串，**不再回退为整份响应**（不再搜索整份响应）。
    """
    chunks = []

    def walk(node, in_envelope):
        if isinstance(node, dict):
            for key, value in node.items():
                if key in _DIAG_IGNORE_KEYS:
                    continue
                if isinstance(value, (dict, list)):
                    walk(value, in_envelope or key in _DIAG_ENVELOPE)
                elif in_envelope or key in _DIAG_TEXT_KEYS:
                    chunks.append(str(value))
        elif isinstance(node, list):
            for item in node:
                if isinstance(item, (dict, list)):
                    walk(item, in_envelope)
                elif in_envelope:
                    # 信封内的字符串列表（errors/issues/reasons…）必须收集，
                    # 否则真实响应里的错误文案会被整体漏掉。
                    chunks.append(str(item))

    if response.get('json') is not None:
        walk(response['json'], False)
    # 非 JSON 响应（纯文本错误页）整体按诊断文本处理
    if response.get('json') is None and response.get('text'):
        chunks.append(str(response['text']))
    return ' '.join(chunks)


def _diag_hit(response, groups, not_required=False):
    """诊断命中判定：每一组至少命中一个词（组内 OR、组间 AND）。

    单一组（旧 diagnostic_terms）等价于「组内任一命中」；多组表示
    「字段/对象定位 + 具体错误原因」须同时成立，避免泛词误收。

    **未提供诊断依据时不放行**：不能因为「返回了期望的错误码」就宣布校验正确。
    确需跳过诊断核对的调用方必须显式传 `diagnostic_not_required=True`。
    """
    groups = [g for g in (groups or []) if g]
    if not groups:
        if not_required:
            return True, '调用方显式声明无需诊断核对（diagnostic_not_required=True）'
        return False, ('未提供诊断依据（diagnostic_terms/diagnostic_term_groups 均为空），'
                       '无法确认拒绝是否针对目标')
    text = diag_text(response)
    if not text:
        return False, '响应中取不到诊断文本（无约定诊断字段），不能判定拒绝是否针对目标'
    missed = []
    for group in groups:
        if not any(term in text for term in group):
            missed.append('|'.join(group))
    if missed:
        return False, '诊断文本未覆盖：%s（诊断文本=%s）' % ('；'.join(missed), text[:300])
    return True, '诊断命中（诊断文本=%s）' % text[:300]


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
        diagnostic_terms        单组诊断词（组内任一命中）；等价于 diagnostic_term_groups 只有一组
        diagnostic_term_groups  多组诊断词：**每组都要命中**（组内 OR、组间 AND），
                                用于「字段/对象定位 + 具体错误原因」同时成立
        diagnostic_not_required 显式声明无需诊断核对（默认 False）。不指定诊断依据且未声明时，
                                拒绝分支记 test_error —— 不得因「返回了期望错误码」就判通过
        known_defect            未拦截时按已知基线缺陷记（默认 False）
        require_no_version_increase  True 时**无论被接受还是被拒绝**都要求版本零新增：
                                版本必须可读且可比，读不到 → test_error；
                                拒绝却新增版本 → 独立新缺陷
        version_before/version_after 版本计数（int）；None 表示读取失败/不可读
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
    groups = expect.get('diagnostic_term_groups')
    if groups is None:
        terms = list(expect.get('diagnostic_terms') or [])
        groups = [terms] if terms else []
    known = bool(expect.get('known_defect'))
    detail = str(expect.get('accepted_detail') or '')
    need_zero_increase = bool(expect.get('require_no_version_increase'))
    version_before = expect.get('version_before')
    version_after = expect.get('version_after')

    # 配置自检：拦截码与接受码重叠会让判定短路（R4 先于 R6），必须由调用方修正用例配置。
    overlap = set(block_set) & set(allow_status)
    if overlap:
        return _mk(TEST_ERROR, '用例配置有误：block_status 与 allow_status 重叠 %s，无法区分拦截与接受'
                   % sorted(overlap), detail)
    # 版本比较必须用整数计数：字符串会按字典序比较（'9' > '10'），静默得出错误结论。
    if need_zero_increase:
        for label, value in (('version_before', version_before), ('version_after', version_after)):
            if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
                return _mk(TEST_ERROR, '用例配置有误：%s 必须是整数计数或 None，实际为 %r'
                           % (label, value), detail)

    if status is None:
        return _mk(TEST_ERROR, '网络失败/无状态码，不能判定为产品通过',
                   '%s %s' % (response.get('error') or '', detail))
    if not _body_present(response):
        return _mk(TEST_ERROR, '响应体为空或不可解析，无法判定结果', 'status=%s %s' % (status, detail))
    if int(status) >= 500:
        return _mk(TEST_ERROR, '服务端 %s，不能作为业务拦截或产品通过' % status, detail)

    # 版本证据门（F01）：要求零新增时，拒绝分支同样必须能证明版本未增加。
    # 读不到版本 → 证据不足（test_error），不得因为「返回了错误码」就宣布校验正确。
    def version_gate(verdict_on_increase, increase_reason, ok_reason, ok_detail):
        if not need_zero_increase:
            return None
        if version_before is None or version_after is None:
            return _mk(TEST_ERROR, '要求核对版本零新增但版本状态不可读（before=%s after=%s）：证据不足'
                       % (version_before, version_after), detail)
        if version_after > version_before:
            return _mk(verdict_on_increase, increase_reason % (version_before, version_after), detail)
        return _mk(PRODUCT_PASS, ok_reason % (version_before, version_after), ok_detail)

    if int(status) in block_set:
        hit, why = _diag_hit(response, groups, expect.get('diagnostic_not_required') is True)
        if not hit:
            return _mk(TEST_ERROR, '被拒绝但诊断与目标字段/依赖无关（HTTP %s），不能算校验正确：%s'
                       % (status, why), detail)
        gated = version_gate(NEW_DEFECT,
                             '拒绝却已新增发布版本（%s→%s）：拦截未生效，独立缺陷',
                             '期望拦截命中且版本零新增（版本 %s→%s），诊断针对目标',
                             'HTTP %s %s' % (status, why))
        if gated is not None:
            return gated
        return _mk(PRODUCT_PASS, '期望拦截命中：HTTP %s 且诊断针对目标' % status, detail)

    if int(status) not in allow_status and 400 <= int(status) < 500:
        return _mk(TEST_ERROR, '无关 4xx（HTTP %s）：身份/路由/请求形态问题，不能当业务拦截' % status, detail)

    if int(status) in allow_status:
        if need_zero_increase:
            if version_before is None or version_after is None:
                return _mk(TEST_ERROR, '要求核对版本零新增但版本状态不可读（before=%s after=%s）：证据不足'
                           % (version_before, version_after), detail)
            if version_after > version_before:
                return _mk(KNOWN_DEFECT if known else NEW_DEFECT,
                           '缺陷复现：未被拦截且已发布新版本（%s→%s）' % (version_before, version_after), detail)
        return _mk(KNOWN_DEFECT if known else NEW_DEFECT, '缺陷复现：操作被接受（HTTP %s），未被拦截' % status, detail)

    return _mk(TEST_ERROR, '状态码 %s 无法归因' % status, detail)


def classify_validate_observation(precondition_ok, response, expect=None, precondition_detail=''):
    """校验接口（/api/project-validate 等只读检查）的观察判定（F02）。

    与 classify_guard_attempt 的区别：这里不是「期望被拦截的状态码」，而是
    「检查通过与否」——必须先过传输 / 状态码 / 响应结构，再看正式诊断字段：

      状态码非期望（如 500）        → test_error（绝不按诊断文案判通过）
      响应结构非法（errors 非数组） → test_error
      出现针对目标的阻断诊断        → product_pass（检查确实拦住了）
      无阻断诊断                    → 缺陷复现（known/new，取决于 known_defect）

    expect:
      ok_status        期望成功状态码，默认 200
      requirement      'blocked' 表示期望出现阻断诊断（默认）
      diagnostic_terms / diagnostic_term_groups  同 classify_guard_attempt（组间 AND）
      known_defect     未拦截时记已知缺陷复现
    """
    expect = dict(expect or {})
    if isinstance(precondition_ok, dict):
        precondition_detail = precondition_detail or precondition_ok.get('detail', '')
        precondition_ok = bool(precondition_ok.get('ok'))
    if not precondition_ok:
        return _mk(BLOCKED, '前置未成功；该检查观察不成立', precondition_detail)
    response = response or {}
    status = response.get('status')
    ok_status = tuple(expect.get('ok_status', (200,)))
    groups = expect.get('diagnostic_term_groups')
    if groups is None:
        terms = list(expect.get('diagnostic_terms') or [])
        groups = [terms] if terms else []
    detail = str(expect.get('detail') or '')
    known = bool(expect.get('known_defect'))
    if status is None:
        return _mk(TEST_ERROR, '检查接口网络失败/无状态码，不能据此判断是否拦截：%s'
                   % (response.get('error') or ''), detail)
    if int(status) >= 500:
        return _mk(TEST_ERROR, '检查接口 HTTP %s（服务端错误）：不能按响应文案判断是否拦截' % status, detail)
    if int(status) not in ok_status:
        return _mk(TEST_ERROR, '检查接口 HTTP %s 非期望成功码 %s：不能据此判断' % (status, list(ok_status)), detail)
    body = response.get('json')
    if not isinstance(body, dict):
        return _mk(TEST_ERROR, '检查接口响应结构非法（非对象，text=%s）：不能据此判断'
                   % str(response.get('text') or '')[:120], detail)
    errs = body.get('errors')
    if not isinstance(errs, list):
        return _mk(TEST_ERROR, '检查接口响应缺少 errors 数组（结构不符）：不能据此判断', detail)

    hit, why = _diag_hit(response, groups, expect.get('diagnostic_not_required') is True)
    if errs and hit:
        return _mk(PRODUCT_PASS, '检查已阻断且诊断针对目标（errors=%d 条）：%s' % (len(errs), why), detail)
    if errs and not hit:
        return _mk(TEST_ERROR, '检查有错误但诊断与目标无关（不能算拦住了该依赖）：%s' % why, detail)
    return _mk(KNOWN_DEFECT if known else NEW_DEFECT,
               '检查未拦截（HTTP %s errors=[]）：未出现针对目标的阻断诊断%s'
               % (status, '' if not groups else '（要求的诊断：%s）'
                  % '；'.join('|'.join(g) for g in groups)), detail)


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
