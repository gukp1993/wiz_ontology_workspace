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
       诊断未命中（只查**阻断错误**，见 blocking_diag_messages）→ test_error（拒绝理由与目标无关）
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

诊断消息边界（G01/R1）：**列表的每个元素各自成条**（元素是标量→该标量即一条；
元素是 dict/list→递归）；**只有「信封列表的直接元素」且其全部值都是标量的 dict**
（叶子条目）才把这些标量合并成一条消息——这是为了 errors[].name +
errors[].message 这类同一条目内的定位与原因能联合命中；含任何嵌套 dict/list 的
dict、以及嵌套列表里的元素/未登记键下的元素，一律不得合并自身标量，改为递归其
嵌套容器；递归穿过**任意未登记键**（未知键不阻断下钻，也不会退化成「整棵子树合
一条」）。若按已登记键名划边界，中间夹一层未登记键就会退回「整棵子树标量 join」，
把两条无关诊断拼成同一条，凑出「定位+原因」的假命中。

阻断证据（G02/R2/R3/R4）：warnings/warning、note/notes/detail/details 只是提示或
补充说明，可提取（报告用）但**不得**作为「已阻断/拦截针对目标」的证据；条目
level/severity/status 显式声明非阻断取值的条目同样不算。判定路径统一用
blocking_diag_messages（只取阻断信封与阻断级别条目）；diag_messages 仍返回全量诊断
供报告使用。级别取值按「**同一条目子树内最近一层**」向下查找（R4：条目 dict 内嵌
dict 里的 level 也算），不跨条目生效——其它条目的提示不会把这条真实错误说成非阻断。

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


# ---------------------------------------------------------------------------
# 诊断文本提取（F03 + 对抗验证加固 + G01/G02 修订 + R1–R4 修订）
#
# 只有「诊断信封」（errors/diagnostics/reasons/issues…）内的文本才算错误说明。
# 顶层其它键（field/text/code/description…）与状态清单 items[] 的字段一律**不收集**：
# 真实接口会把每个条目（无论有无问题）都回显 items[].name/id/kind，把它们当诊断，
# 会让「定位组」无条件命中，从而把无关错误误判成正确拦截。
#
# 消息边界（R1，取代「按已登记键名划边界」）：
#   **列表的每个元素各自成条**（标量→该标量即一条；dict/list→递归）；
#   **只有「信封列表的直接元素」且其全部值都是标量**时才把这些标量合并成一条消息——
#   这是 errors[].name + errors[].message 能联合命中的唯一合法形态；
#   含任何嵌套 dict/list 的 dict 不合并自身标量，改为递归其嵌套容器；
#   嵌套列表里的元素、未登记键下的元素同样不合并（否则双层包装仍可拼接）；
#   递归穿过**任意未登记键**：遇到未知键继续下钻，绝不把整棵子树拼成一条。
#
# 阻断 / 非阻断（G02/R2/R3/R4）：warnings/warning、note/notes/detail/details 只是
# 提示或补充说明，可以提取（报告用），但**不得**作为「检查已阻断」或「拦截针对目标」
# 的证据；条目的 level/severity/status 声明非阻断取值时同理（R2 的 items[].status）。
# 判定函数一律走 blocking_diag_messages。
# ---------------------------------------------------------------------------

# 信封键：其子树承载「为什么被拒」
_ENVELOPE_KEYS = {
    'error', 'errors', 'message', 'messages', 'issues', 'issue', 'reasons', 'reason',
    'diagnostics', 'diagnostic', 'problems', 'problem', 'violations', 'violation',
    'warnings', 'warning', 'detail', 'details', 'note', 'notes', 'report', 'check',
}
# 非阻断信封键：只是提示/补充说明，可提取但不得作为「已阻断/拦截针对目标」的证据
# （G02 warnings；R3 note/notes/detail/details）
_NON_BLOCKING_KEYS = {'warnings', 'warning', 'note', 'notes', 'detail', 'details'}
# 判定「条目是否阻断」时向下查找的字段（R4：最近一层）
_MARKER_KEYS = ('level', 'severity', 'status')
# 明确表示「非错误」的取值：条目声明这些取值时不算阻断证据（G02 级别 + R2 状态）
#   level/severity：warning/warn/info… 只是提示
#   status（project_validation 的 items[]）：unconfigured=待补全、valid=正常
_NON_BLOCKING_LEVELS = {
    'warning', 'warn', 'info', 'information', 'notice', 'hint', 'debug', 'trace', 'none',
    'unconfigured', 'valid', 'pending', 'ok',
}
# 明确表示「阻断」的取值：同一层与其它取值冲突时优先（保守：不因冲突声明漏掉真实阻断）
_BLOCKING_LEVELS = {'invalid', 'error', 'failed', 'fail', 'fatal', 'critical', 'blocked', 'unhealthy'}
# 状态清单键：其条目是「逐项状态回显」，只下钻其中真正的错误字段
_STATUS_LIST_KEYS = {'items', 'results', 'rows', 'entries', 'checks'}
# 诊断子树内不参与匹配的键（大小写不敏感）：类型/标识/状态/回显载荷
_IGNORE_KEYS = {
    'state', 'data', 'payload', 'revision', 'requestid', 'kind', 'type',
    'status', 'level', 'id', 'ids', 'key', 'keys', 'version', 'templateid', 'packagekey',
    'suggestedname', 'scene', 'outputs', 'output', 'nodes', 'connections', 'layout',
    'canvas', 'props', 'effects', 'sceneid', 'flow', 'flowid', 'objecttype', 'objecttypeid',
    'prop', 'properties', 'index', 'count', 'total', 'generation', 'fingerprint',
}


def _is_ignored(key):
    return str(key).lower() in _IGNORE_KEYS


def _declared_marker(node):
    """节点**自身**直接声明的 level/severity/status 取值（小写）；未声明返回 None。

    同一节点同时声明多个取值时：阻断取值优先（如 status=invalid 与 level=warning 并存
    按阻断处理，保守取「不放过真实阻断」）；声明了取值但取值未知的，按阻断处理。
    """
    values = []
    for key in _MARKER_KEYS:
        value = node.get(key)
        if isinstance(value, str) and value.strip():
            values.append(value.strip().lower())
    if not values:
        return None
    for value in values:
        if value in _BLOCKING_LEVELS:
            return value
    for value in values:
        if value in _NON_BLOCKING_LEVELS:
            return value
    return values[0]


def _nearest_marker(node):
    """在**同一条目子树内**按层（BFS）查找最近的 level/severity/status 声明（R4）。

    就近优先：条目自身声明的 status=invalid 压过更深处的 level=warning；返回最近一层
    的取值（可能是内嵌 dict 里的 level），找不到返回 None（调用方按「未声明 → 阻断」
    处理）。忽略键（kind/state/payload…）的子树不下钻，与文本提取口径一致。
    只在本条目子树内查找：兄弟条目 / 远端后代的提示不会把这条真实错误说成非阻断。
    """
    queue = [node]
    while queue:
        nxt = []
        for item in queue:
            if isinstance(item, dict):
                marker = _declared_marker(item)
                if marker is not None:
                    return marker
                for key, value in item.items():
                    if _is_ignored(key):
                        continue
                    if isinstance(value, (dict, list)):
                        nxt.append(value)
            elif isinstance(item, list):
                for sub in item:
                    if isinstance(sub, (dict, list)):
                        nxt.append(sub)
        queue = nxt
    return None


def _entry_level(node):
    """条目声明的严重级别（level/severity/status，小写）；未声明返回空串。

    R4：向下找**最近一层**的声明（条目 dict 内嵌 dict 里的 level 也算），但只在
    **同一条目子树内**生效——别处条目的 warning 不会把这条真实错误说成非阻断。
    """
    if not isinstance(node, (dict, list)):
        return ''
    return _nearest_marker(node) or ''


def _is_blocking_entry(node):
    """条目是否算阻断错误：只有**显式声明**非阻断取值才排除。

    R2：除 level/severity 外同时认 status —— project_validation 的 items[] 用 status
    表达严重性：'invalid'=阻断、'unconfigured'=待补全（不阻断）、'valid'=正常；
    flows.check_flow 的 diagnostics[] 仍是 level。大小写不敏感。
    未声明任何取值（或声明了未知取值）时按阻断处理，避免把真实阻断当成提示漏掉。
    """
    return _entry_level(node) not in _NON_BLOCKING_LEVELS


def _is_container(node):
    """dict 是否为「容器/包装」：含**任何**嵌套 dict/list（非忽略键）。

    R1 修订：不再按已登记键名（report/check…）划边界——中间夹一层未登记键就会退回
    「整棵子树标量 join」。容器自身不是诊断条目：其标量字段逐条成消息、绝不与兄弟
    字段合并，内部嵌套容器继续递归；列表元素边界在递归中一路保留（G01）。
    全部值都是标量且位于**信封列表直接元素**位置的 dict 才是可合并的叶子条目
    （{"name":"activePower","message":"未绑定"}），整条拼成**一条**消息——同条目内的
    定位与原因必须能联合命中（合法对照）；位置限制的理由见 walk_entry。
    """
    if not isinstance(node, dict):
        return False
    for key, value in node.items():
        if _is_ignored(key):
            continue
        if isinstance(value, (dict, list)):
            return True
    return False


def _collect_diag_messages(response, blocking_only=False):
    """诊断文案提取主逻辑；blocking_only=True 时只取阻断错误（G02/R2/R3/R4）。"""
    messages = []

    def emit(text, blocking):
        """append 一条消息；blocking_only 时非阻断消息（提示/待补全）不进入证据。"""
        text = str(text).strip()
        if text and (not blocking_only or blocking):
            messages.append(text)

    def item_text(node):
        """把一个诊断条目内的全部标量拼成一条消息（忽略键整棵跳过）。"""
        out = []

        def rec(n):
            if isinstance(n, dict):
                for k, v in n.items():
                    if _is_ignored(k):
                        continue
                    rec(v)
            elif isinstance(n, list):
                for x in n:
                    rec(x)
            else:
                out.append(str(n))

        rec(node)
        return ' '.join(out).strip()

    def is_blocking(node, ctx):
        """条目/包装的阻断判定：子树内最近一层声明决定，未声明沿用 ctx（默认阻断）。"""
        marker = _nearest_marker(node) if isinstance(node, (dict, list)) else None
        if marker is None:
            return ctx
        return marker not in _NON_BLOCKING_LEVELS

    def walk_entry(node, ctx, entry=False):
        """一个诊断条目（信封列表的元素 / 包装内的值）→ 若干条消息。

        R1：只有**信封列表的直接元素**且其**全部值都是标量**时才合并成一条
        （errors[].name+message / diagnostics[].message 形态——同条目内定位与原因
        必须能联合命中）。含嵌套 dict/list 的一律按包装递归；不在该位置的 dict
        （嵌套列表里的元素、未登记键下的元素）同样不合并，自身标量逐条成消息、
        绝不与兄弟字段合并——否则 `{'errors': [[{'a':…,'b':…}]]}` 这类双层包装
        仍能把两条无关诊断拼成一条，凑出「定位+原因」假命中。
        """
        if isinstance(node, list):
            for sub in node:
                walk_entry(sub, ctx)
            return
        if isinstance(node, dict):
            blocking = is_blocking(node, ctx)
            if entry and not _is_container(node):
                emit(item_text(node), blocking)
            else:
                walk_mapping(node, blocking)
            return
        emit(node, ctx)

    def walk_mapping(node, ctx):
        """包装/映射（含嵌套容器的 dict）：递归其值，自身标量与兄弟字段绝不合并。

        R1：穿过**任意未登记键**继续下钻；遇到信封键按条目边界递归（列表元素各自成条）。
        """
        blocking = is_blocking(node, ctx)
        for key, value in node.items():
            if _is_ignored(key):
                continue
            low = str(key).lower()
            if low in _NON_BLOCKING_KEYS:
                if not blocking_only:
                    walk_collection(value, False)  # 提示/补充说明：只进报告用全量诊断
            elif low in _ENVELOPE_KEYS:
                walk_envelope(value, blocking)
            elif low in _STATUS_LIST_KEYS:
                from_status_list(value)
            else:
                walk_collection(value, blocking)

    def walk_collection(value, ctx):
        """未登记键下的值：列表元素各自成条、dict 继续当包装下钻、标量自成一条。"""
        if isinstance(value, list):
            for sub in value:
                walk_entry(sub, ctx)  # 未登记键下的条目不在信封列表位置：不合并自身标量
        elif isinstance(value, dict):
            walk_mapping(value, ctx)
        else:
            emit(value, ctx)

    def walk_envelope(value, ctx):
        """信封值 → 消息：列表的每个元素各自成条（G01/R1 的边界约束就在这里）。

        信封值本身是 dict 时按包装递归（R1 复现：{'errors': {'e1':…,'e2':…}} 必须拆成
        两条，不能合并成一条）——只有信封列表的直接元素才可能是可合并的叶子条目
        （entry=True）；嵌套列表里的元素不算，见 walk_entry。
        """
        if isinstance(value, list):
            for item in value:
                walk_entry(item, ctx, True)
        elif isinstance(value, dict):
            walk_mapping(value, ctx)
        else:
            emit(value, ctx)

    def from_status_list(value):
        if not isinstance(value, list):
            return
        for item in value:
            if not isinstance(item, dict):
                continue
            blocking = is_blocking(item, True)
            if blocking_only and not blocking:
                continue  # R2：items[].status='unconfigured'/'valid'（或 level=warning）只是待补全/提示
            for k, v in item.items():
                if _is_ignored(k):
                    continue
                low = str(k).lower()
                if low in _NON_BLOCKING_KEYS:
                    if not blocking_only:
                        walk_collection(v, False)
                elif low in _ENVELOPE_KEYS:
                    walk_envelope(v, blocking)
                elif low in _STATUS_LIST_KEYS:
                    from_status_list(v)

    payload = response.get('json')
    if isinstance(payload, dict):
        for key, value in payload.items():
            if _is_ignored(key):
                continue
            low = str(key).lower()
            if low in _NON_BLOCKING_KEYS:
                if not blocking_only:
                    walk_collection(value, False)
            elif low in _ENVELOPE_KEYS:
                walk_envelope(value, True)
            elif low in _STATUS_LIST_KEYS:
                from_status_list(value)
            # 其它顶层键是载荷回显：整棵跳过，不作兜底搜索
    elif isinstance(payload, list):
        for item in payload:
            walk_entry(item, True, True)  # 根就是信封列表：元素即条目
    return messages


def diag_messages(response):
    """返回全部诊断文案：**一条消息 = 一个诊断条目**（不是每个标量各成一条）。

    为什么按「条目」而不是按「标量」：真实接口里一个错误条目的定位与原因是同一对象的
    两个字段（如 {"name":"activePower","message":"未绑定"}），必须能在同一条消息内同时
    命中「定位」与「原因」两组诊断词；而 {"errors":["消息甲","消息乙"]} 这种多条目形态
    必须各自独立成条，防止两条互不相关的消息各贡献一半拼出假命中。

    只从「诊断信封」取文本：根层与状态清单（items[]）里只下钻信封键；状态清单条目的
    name/id/kind/status 等回显字段不收集（真实接口会为每个条目回显这些字段，收集它们
    会让定位组无条件命中）。不使用整体文本兜底，非 JSON 响应不参与诊断匹配。

    嵌套容器（report/check/未登记包装键）进入后同样按条目成条，不跨条目拼接
    （G01/R1）：列表元素各自成条，只有全标量 dict 才合并，穿透任意未登记键。
    本函数**包含 warning/note/detail** 等提示（报告用）；判定「是否被阻断」请用
    blocking_diag_messages。
    """
    return _collect_diag_messages(response, blocking_only=False)


def blocking_diag_messages(response):
    """只取「阻断错误」的诊断文案（G02/R2/R3/R4）。

    warning 只说明「有个提示」，note/detail 只是补充说明，items[].status='unconfigured'
    只说明「配置待补全」：都不能证明目标依赖已被拒绝/阻断，因此不参与通过判定。
    只有 errors（数组元素各自成条）、error、diagnostics 等信封里的阻断项，以及状态
    清单中未声明非阻断取值的条目，才算阻断证据（级别取值在**同一条目子树内**就近查找，
    未声明按阻断处理）。
    """
    return _collect_diag_messages(response, blocking_only=True)


def diag_text(response):
    """诊断文案的整串形式（保留给报告与单测；匹配请用 diag_messages + _diag_hit）。"""
    return ' | '.join(diag_messages(response))


def _diag_hit(response, groups, not_required=False, blocking_only=False):
    """诊断命中判定：**必须由同一条诊断消息**同时满足所有组（组内 OR、组间 AND）。

    对抗验证发现：早期实现把所有诊断文案拼成一整串再找词，两条互不相关的消息
    可以各贡献一半，拼出「字段定位 + 具体原因」的假命中。因此这里按消息独立判定：
    存在一条消息，它对每一组都至少命中一个词，才算命中。

    blocking_only=True（G02）：只拿阻断错误当证据——目标内容只出现在 warning 里，
    不能证明「检查已阻断」或「拦截针对目标」，此时按未命中处理。

    未提供诊断依据时不放行；确需跳过必须显式 `diagnostic_not_required=True`。
    """
    groups = [list(g) for g in (groups or []) if g]
    for group in groups:
        if not all(isinstance(g, str) and g.strip() for g in group):
            return False, '用例配置有误：诊断词必须是非空字符串（收到 %r）' % (group,)
    if not groups:
        if not_required:
            return True, '调用方显式声明无需诊断核对（diagnostic_not_required=True）'
        return False, ('未提供诊断依据（diagnostic_terms/diagnostic_term_groups 均为空），'
                       '无法确认拒绝是否针对目标')
    messages = blocking_diag_messages(response) if blocking_only else diag_messages(response)
    if not messages:
        if blocking_only and diag_messages(response):
            return False, ('响应只见非阻断诊断（warning 等），不能证明拒绝/阻断针对目标：'
                           '目标内容未出现在任何阻断错误里')
        return False, '响应中取不到诊断文案（无约定诊断字段），不能判定拒绝是否针对目标'
    best = None
    for message in messages:
        missing = [g for g in groups if not any(term in message for term in g)]
        if not missing:
            return True, '同一条诊断消息命中全部诊断组：%s' % message[:300]
        if best is None or len(missing) < len(best[1]):
            best = (message, missing)
    message, missing = best
    return False, ('没有任何单条诊断消息覆盖全部诊断组；最接近的是「%s」，'
                   '其中未覆盖：%s' % (message[:160], '；'.join('|'.join(g) for g in missing)))


def _mk(result, reason, detail=''):
    return {'result': result, 'reason': reason, 'detail': detail}


def _as_status_tuple(value, field):
    """状态码集合归一：整数或整数序列；返回 (tuple|None, 错误说明)。"""
    if value is None:
        return None, '用例配置有误：%s 不能为空' % field
    items = value if isinstance(value, (list, tuple, set, frozenset)) else (value,)
    out = []
    for item in items:
        if isinstance(item, bool) or not isinstance(item, int):
            return None, '用例配置有误：%s 必须是整数状态码，收到 %r' % (field, item)
        out.append(item)
    if not out:
        return None, '用例配置有误：%s 为空集合' % field
    return tuple(out), ''


def _as_bool(value, field):
    if isinstance(value, bool):
        return value, ''
    return None, '用例配置有误：%s 必须是布尔值，收到 %r' % (field, value)


def _as_count(value, field):
    if value is None:
        return value, ''
    if isinstance(value, bool) or not isinstance(value, int):
        return None, '用例配置有误：%s 必须是整数计数或 None，收到 %r' % (field, value)
    return value, ''


def _precondition_ok(precondition_ok):
    """前置归一：仅接受 bool 或 {'ok': bool, 'detail': str}；其它类型按配置错误处理。"""
    if isinstance(precondition_ok, bool):
        return precondition_ok, ''
    if isinstance(precondition_ok, dict):
        ok = precondition_ok.get('ok')
        if isinstance(ok, bool):
            return ok, str(precondition_ok.get('detail') or '')
        return None, '用例配置有误：前置字典的 ok 必须是布尔值，收到 %r' % (ok,)
    return None, '用例配置有误：precondition_ok 必须是布尔值或字典，收到 %r' % (precondition_ok,)


def classify_prereq(name, ok, detail=''):
    """前置步骤判定：成功 → product_pass；失败 → blocked（用例不成立，不用旧状态继续）。"""
    if not isinstance(ok, bool):
        return _mk(TEST_ERROR, '用例配置有误：前置判定必须是布尔值，收到 %r' % (ok,), detail)
    if ok:
        return _mk(PRODUCT_PASS, '前置成功：' + name, detail)
    return _mk(BLOCKED, '前置未成功：' + name + '；该用例不成立，不用旧状态继续', detail)


def classify_static_check(ok, detail='', defect_kind=NEW_DEFECT):
    """静态/白名单/源码核对：通过记 static_check_pass（不是动态业务验证）。"""
    if not isinstance(ok, bool):
        return _mk(TEST_ERROR, '用例配置有误：静态核对结果必须是布尔值，收到 %r' % (ok,), detail)
    if ok:
        return _mk(STATIC_PASS, '静态核对通过（非动态业务验证）', detail)
    return _mk(defect_kind, '静态核对发现可达路径（需人工确认是否为缺陷）', detail)


def classify_guard_attempt(precondition_ok, response, expect=None, precondition_detail=''):
    """一次「期望被拦截」的操作判定。

    precondition_ok : bool | {'ok': bool, 'detail': str}   非布尔一律 test_error
    response        : dict  客户端 call() 结果 {status:int|None, json, text, binary, error}
    expect          : dict
        block_status               期望拦截状态码（int 或 int 序列，默认 422）
        allow_status               接受态状态码（int 或 int 序列，默认 (200, 201)）
        diagnostic_terms           单组诊断词（组内任一命中）；等价于只给一组
        diagnostic_term_groups     多组：**必须在同一条诊断消息内**全部命中
        diagnostic_not_required    显式声明跳过诊断核对（必须严格 True）
        known_defect               必须是布尔；未拦截时记已知/新缺陷
        require_no_version_increase 必须是布尔；为 True 时**接受与拒绝分支都**要求
                                   版本可读且严格等于 before（回退视为证据矛盾 → test_error）
        version_before/version_after 整数计数或 None（None = 读不到 = 证据不足）

    安全取向：配置错误、类型不符、证据不足一律 test_error —— 宁可判「不可用」，
    绝不把无关错误或服务端故障算成产品通过。
    """
    expect = dict(expect or {})
    ok, pres_detail = _precondition_ok(precondition_ok)
    precondition_detail = precondition_detail or pres_detail
    if ok is None:
        return _mk(TEST_ERROR, '前置配置无效：' + pres_detail, precondition_detail)
    if not ok:
        return _mk(BLOCKED, '前置未成功；用例不成立，不用旧状态继续该操作', precondition_detail)

    response = response or {}
    detail = str(expect.get('accepted_detail') or '')

    block_set, err = _as_status_tuple(expect.get('block_status', 422), 'block_status')
    if block_set is None:
        return _mk(TEST_ERROR, err, detail)
    allow_set, err = _as_status_tuple(expect.get('allow_status', (200, 201)), 'allow_status')
    if allow_set is None:
        return _mk(TEST_ERROR, err, detail)
    if set(block_set) & set(allow_set):
        return _mk(TEST_ERROR, '用例配置有误：block_status 与 allow_status 重叠 %s，无法区分拦截与接受'
                   % sorted(set(block_set) & set(allow_set)), detail)

    known = expect.get('known_defect', False)
    known, err = _as_bool(known, 'known_defect')
    if known is None:
        return _mk(TEST_ERROR, err, detail)
    need_gate = expect.get('require_no_version_increase', False)
    need_gate, err = _as_bool(need_gate, 'require_no_version_increase')
    if need_gate is None:
        return _mk(TEST_ERROR, err, detail)
    if expect.get('diagnostic_not_required') not in (None, False, True):
        return _mk(TEST_ERROR, '用例配置有误：diagnostic_not_required 必须是布尔值，收到 %r'
                   % (expect.get('diagnostic_not_required'),), detail)

    version_before, err = _as_count(expect.get('version_before'), 'version_before')
    if err:
        return _mk(TEST_ERROR, err, detail)
    version_after, err = _as_count(expect.get('version_after'), 'version_after')
    if err:
        return _mk(TEST_ERROR, err, detail)

    groups = expect.get('diagnostic_term_groups')
    if groups is None:
        terms = expect.get('diagnostic_terms') or []
        if isinstance(terms, str):
            return _mk(TEST_ERROR, '用例配置有误：diagnostic_terms 必须是字符串列表，收到字符串 %r' % terms, detail)
        groups = [list(terms)] if terms else []

    status = response.get('status')
    if status is None:
        return _mk(TEST_ERROR, '网络失败/无状态码，不能判定为产品通过',
                   '%s %s' % (response.get('error') or '', detail))
    if isinstance(status, bool) or not isinstance(status, int):
        return _mk(TEST_ERROR, '响应状态码类型异常（%r）：不能按业务状态判定' % (status,), detail)
    if not _body_present(response):
        return _mk(TEST_ERROR, '响应体为空或不可解析，无法判定结果', 'status=%s %s' % (status, detail))
    if status >= 500:
        return _mk(TEST_ERROR, '服务端 %s，不能作为业务拦截或产品通过' % status, detail)

    def version_gate(on_increase, on_increase_reason, ok_reason):
        """要求零新增时的统一版本证据门（接受与拒绝分支共用）。"""
        if not need_gate:
            return None
        if version_before is None or version_after is None:
            return _mk(TEST_ERROR, '要求核对版本零新增但版本状态不可读（before=%s after=%s）：证据不足'
                       % (version_before, version_after), detail)
        if version_after > version_before:
            return _mk(on_increase, on_increase_reason % (version_before, version_after), detail)
        if version_after < version_before:
            return _mk(TEST_ERROR, '版本计数回退（%s→%s）：证据自相矛盾，不能判定'
                       % (version_before, version_after), detail)
        return _mk(PRODUCT_PASS, ok_reason % (version_before, version_after), detail)

    if status in block_set:
        # G02：拒绝理由必须来自阻断错误；只出现在 warning 里的目标内容不能证明拦截针对目标
        hit, why = _diag_hit(response, groups, expect.get('diagnostic_not_required') is True,
                             blocking_only=True)
        if not hit:
            return _mk(TEST_ERROR, '被拒绝但诊断与目标无关（HTTP %s），不能算校验正确：%s'
                       % (status, why), detail)
        gated = version_gate(NEW_DEFECT, '拒绝却已新增发布版本（%s→%s）：拦截未生效，独立缺陷',
                             '期望拦截命中且版本零新增（版本 %s→%s）')
        if gated is not None:
            return gated
        return _mk(PRODUCT_PASS, '期望拦截命中：HTTP %s 且诊断针对目标' % status, detail)

    if status not in allow_set and 400 <= status < 500:
        return _mk(TEST_ERROR, '无关 4xx（HTTP %s）：身份/路由/请求形态问题，不能当业务拦截' % status, detail)

    if status in allow_set:
        note = ''
        if need_gate:
            if version_before is None or version_after is None:
                note = '（版本状态不可读，未能确认是否新增版本）'
            elif version_after > version_before:
                note = '（且已发布新版本 %s→%s）' % (version_before, version_after)
            elif version_after == version_before:
                note = '（版本未增加 %s→%s）' % (version_before, version_after)
            else:
                note = '（版本计数回退 %s→%s，证据自相矛盾）' % (version_before, version_after)
        return _mk(KNOWN_DEFECT if known else NEW_DEFECT,
                   '缺陷复现：操作被接受（HTTP %s），未被拦截%s' % (status, note), detail)

    return _mk(TEST_ERROR, '状态码 %s 无法归因' % status, detail)


def classify_validate_observation(precondition_ok, response, expect=None, precondition_detail=''):
    """校验接口（/api/project-validate 等只读检查）的观察判定（F02）。

    必须先过传输 / 状态码 / 响应结构，再看正式诊断字段：

      status 非 int / 非期望码 / >=500  → test_error
      json 非对象 / errors 字段缺失或非数组 → test_error（结构不符）
      出现**同一条阻断错误内**命中全部诊断组 → product_pass（检查确实拦住了）
      有 error 但诊断无关 / 无 error  → 缺陷复现或 test_error（见下）

    G02 修订：通过判定只用阻断错误当证据（errors 数组，或 error/diagnostics 里
    level=error 的条目）——目标内容只出现在 warnings 里不能证明目标依赖被阻断；
    按 error 条数走的结构检查仍以 `errors` 数组为准（沿用 03 分册契约）。

    expect: ok_status(int|序列, 默认 200)、diagnostic_terms / diagnostic_term_groups、
            known_defect(bool)、detail(str)
    """
    expect = dict(expect or {})
    ok, pres_detail = _precondition_ok(precondition_ok)
    precondition_detail = precondition_detail or pres_detail
    if ok is None:
        return _mk(TEST_ERROR, '前置配置无效：' + pres_detail, precondition_detail)
    if not ok:
        return _mk(BLOCKED, '前置未成功；该检查观察不成立', precondition_detail)

    response = response or {}
    detail = str(expect.get('detail') or '')
    ok_set, err = _as_status_tuple(expect.get('ok_status', (200,)), 'ok_status')
    if ok_set is None:
        return _mk(TEST_ERROR, err, detail)
    known = expect.get('known_defect', False)
    known, err = _as_bool(known, 'known_defect')
    if known is None:
        return _mk(TEST_ERROR, err, detail)
    groups = expect.get('diagnostic_term_groups')
    if groups is None:
        terms = expect.get('diagnostic_terms') or []
        if isinstance(terms, str):
            return _mk(TEST_ERROR, '用例配置有误：diagnostic_terms 必须是字符串列表，收到字符串 %r' % terms, detail)
        groups = [list(terms)] if terms else []

    status = response.get('status')
    if status is None:
        return _mk(TEST_ERROR, '检查接口网络失败/无状态码，不能据此判断是否拦截：%s'
                   % (response.get('error') or ''), detail)
    if isinstance(status, bool) or not isinstance(status, int):
        return _mk(TEST_ERROR, '检查接口状态码类型异常（%r）：不能据此判断' % (status,), detail)
    if status >= 500:
        return _mk(TEST_ERROR, '检查接口 HTTP %s（服务端错误）：不能按响应文案判断是否拦截' % status, detail)
    if status not in ok_set:
        return _mk(TEST_ERROR, '检查接口 HTTP %s 非期望成功码 %s：不能据此判断' % (status, list(ok_set)), detail)
    body = response.get('json')
    if not isinstance(body, dict):
        return _mk(TEST_ERROR, '检查接口响应结构非法（非对象）：不能据此判断', detail)
    errs = body.get('errors')
    if not isinstance(errs, list) or not all(isinstance(e, str) for e in errs):
        return _mk(TEST_ERROR, '检查接口 errors 必须是字符串数组（结构不符）：不能据此判断', detail)

    has_error = bool([e for e in errs if e.strip()])
    if not has_error:
        return _mk(KNOWN_DEFECT if known else NEW_DEFECT,
                   '检查未拦截（HTTP %s errors 为空）：未出现针对目标的阻断诊断%s'
                   % (status, '' if not groups else '（要求的诊断：%s）'
                      % '；'.join('|'.join(g) for g in groups)), detail)
    # G02：通过判定只认阻断错误（errors / level=error 的诊断）；warnings 只是提示，
    # 不能证明「检查确实拦住了目标依赖」——否则无关 error + 目标 warning 会假通过。
    hit, why = _diag_hit(response, groups, expect.get('diagnostic_not_required') is True,
                         blocking_only=True)
    if hit:
        return _mk(PRODUCT_PASS, '检查已阻断且诊断针对目标（errors=%d 条）：%s' % (len(errs), why), detail)
    return _mk(TEST_ERROR, '检查有错误但诊断与目标无关（不能算拦住了该依赖）：%s' % why, detail)



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
