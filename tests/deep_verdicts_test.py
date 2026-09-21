# -*- coding: utf-8 -*-
"""判定逻辑自测（deep_verdicts）：正确阻断 / 实际缺陷 / 500 / 无关 4xx / 前置失败。

运行：.runtime/venv/bin/python tests/deep_verdicts_test.py   （纯逻辑，不连服务）
说明：这里只证明「分类函数对给定输入输出正确分类」，模拟响应不能冒充真实 HTTP
业务证据；真实场景结论以 deep_reverify_r02_a01.py 等真实链路运行为准。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import deep_verdicts as V  # noqa: E402

CASES = []


def case(name, got, want, note=''):
    ok = got['result'] == want
    CASES.append({'name': name, 'ok': ok, 'got': got, 'want': want, 'note': note})
    print('%s %-46s -> %-24s %s' % ('✓' if ok else '✗', name, got['result'], got['reason']))


def resp(status, body=None, text='', error=None):
    return {'status': status, 'json': body, 'text': text, 'binary': b'x' if body is not None else b'',
            'error': error}


# 1) 正确阻断：422 + 诊断针对目标 + 版本零新增
case('正确阻断：422+诊断命中+零新增版本',
     V.classify_guard_attempt(True, resp(422, {'errors': ['属性 activePower 引用编排输出未绑定，无法取值']}),
                              {'block_status': 422, 'diagnostic_terms': ['编排', '未绑定'],
                               'require_no_version_increase': True, 'version_before': 2, 'version_after': 2}),
     V.PRODUCT_PASS)

# 2) 被拒绝但诊断与目标无关 → 不能算校验正确
case('拒绝但诊断无关（422 说的是别的字段）',
     V.classify_guard_attempt(True, resp(422, {'errors': ['对象缺少名称']}),
                              {'block_status': 422, 'diagnostic_terms': ['编排', '未绑定']}),
     V.TEST_ERROR)

# 3) 实际缺陷：接受且已发布新版本（已知基线缺陷）
case('实际缺陷(已知)：200 接受且版本 +1',
     V.classify_guard_attempt(True, resp(200, {'version': 'v2'}),
                              {'block_status': 422, 'diagnostic_terms': ['编排'],
                               'known_defect': True, 'require_no_version_increase': True,
                               'version_before': 1, 'version_after': 2}),
     V.KNOWN_DEFECT)

# 4) 实际缺陷：接受，未知基线缺陷 → 新发现
case('实际缺陷(新)：200 接受',
     V.classify_guard_attempt(True, resp(200, {'errors': []}), {'block_status': 422}),
     V.NEW_DEFECT)

# 5) 500 → 工具/服务错误，不能当业务拦截
case('500 不能当产品通过', V.classify_guard_attempt(True, resp(500, {'error': 'Internal'}), {'block_status': 422}),
     V.TEST_ERROR)
case('503 不能当产品通过', V.classify_guard_attempt(True, resp(503, {'error': 'Storage'}), {'block_status': 422}),
     V.TEST_ERROR)

# 6) 无关 4xx：身份/路由/请求形态
for status in (400, 401, 403, 404, 409, 413):
    case('无关 4xx（%d）不能当业务拦截' % status,
         V.classify_guard_attempt(True, resp(status, {'error': 'nope'}), {'block_status': 422}),
         V.TEST_ERROR)

# 7) 前置失败 → blocked（不拿旧状态继续）
case('前置失败（编排保存未成功）',
     V.classify_guard_attempt(False, resp(422, {'errors': ['编排输出未绑定']}), {'block_status': 422},
                              precondition_detail='flow-save HTTP 500'),
     V.BLOCKED)

# 8) 网络失败 / 无状态码
case('网络失败（status=None）', V.classify_guard_attempt(True, resp(None, None, error='URLError: refused'),
                                                     {'block_status': 422}), V.TEST_ERROR)

# 9) 空响应体：2xx 但没有可解析内容 → 无法判定
case('空响应体（200 无内容）', V.classify_guard_attempt(True, resp(200, None, ''), {'block_status': 422}),
     V.TEST_ERROR)
case('空响应体（422 无内容）', V.classify_guard_attempt(True, resp(422, None, ''), {'block_status': 422}),
     V.TEST_ERROR)

# 10) 期望 409 的依赖变化拦截（发布依赖重验），诊断词命中 → 通过
case('期望 409 依赖变化拦截（诊断命中）',
     V.classify_guard_attempt(True, resp(409, {'code': 'REVISION_CONFLICT', 'reason': 'DEPENDENCY_CHANGED'}),
                              {'block_status': 409, 'diagnostic_terms': ['DEPENDENCY_CHANGED']}),
     V.PRODUCT_PASS)

# 11) 前置为 dict 形式 + 静态检查分类
case('前置 dict（ok=False）', V.classify_guard_attempt({'ok': False, 'detail': '项目保存 404'},
                                                   resp(422, {'errors': ['x']}), {'block_status': 422}),
     V.BLOCKED)
case('静态核对通过', V.classify_static_check(True, '白名单无写路径'), V.STATIC_PASS)
case('静态核对发现可达路径', V.classify_static_check(False, '发现 /api/x'), V.NEW_DEFECT)

# ======================================================================
# F01–F04 回归（来自 S1–S3 独立复验记录 20260921，逐条锁定不得回退）
# ======================================================================

# F01-a：拒绝分支同样受「版本零新增」约束 —— 返回 422 但版本已增加 = 拦截不生效
case('F01 拒绝却已新增版本 → 缺陷（不得 product_pass）',
     V.classify_guard_attempt(True, resp(422, {'errors': ['输出未绑定']}),
                              {'block_status': 422, 'diagnostic_terms': ['输出'],
                               'require_no_version_increase': True,
                               'version_before': 1, 'version_after': 2}),
     V.NEW_DEFECT)

# F01-b：要求版本零新增但版本读不到（None）→ 证据不足，不得因「返回 422」就判通过
case('F01 拒绝但版本不可读（after=None）→ test_error',
     V.classify_guard_attempt(True, resp(422, {'errors': ['输出未绑定']}),
                              {'block_status': 422, 'diagnostic_terms': ['输出'],
                               'require_no_version_increase': True,
                               'version_before': 1, 'version_after': None}),
     V.TEST_ERROR)
case('F01 拒绝但版本不可读（before=None）→ test_error',
     V.classify_guard_attempt(True, resp(422, {'errors': ['输出未绑定']}),
                              {'block_status': 422, 'diagnostic_terms': ['输出'],
                               'require_no_version_increase': True,
                               'version_before': None, 'version_after': 1}),
     V.TEST_ERROR)
case('F01 拒绝且版本零新增 → product_pass（对照）',
     V.classify_guard_attempt(True, resp(422, {'errors': ['输出未绑定']}),
                              {'block_status': 422, 'diagnostic_terms': ['输出'],
                               'require_no_version_increase': True,
                               'version_before': 3, 'version_after': 3}),
     V.PRODUCT_PASS)

# F03：不得从整个响应搜索任意词 —— flow 只出现在回显 state 里不算诊断命中
case('F03 泛词只出现在回显 state → test_error',
     V.classify_guard_attempt(True, resp(422, {'error': '名称重复', 'state': {'kind': 'flow'}}),
                              {'block_status': 422, 'diagnostic_terms': ['flow']}),
     V.TEST_ERROR)
case('F03 诊断字段确实含该词 → product_pass（对照）',
     V.classify_guard_attempt(True, resp(422, {'errors': ['引用编排输出未绑定']}),
                              {'block_status': 422, 'diagnostic_terms': ['编排', '未绑定']}),
     V.PRODUCT_PASS)
# F03：两组诊断（字段定位 + 具体原因）必须同时命中
case('F03 分组诊断只命中原因组 → test_error',
     V.classify_guard_attempt(True, resp(422, {'errors': ['输出未绑定']}),
                              {'block_status': 422,
                               'diagnostic_term_groups': [['activePower', '编排'], ['未绑定']]}),
     V.TEST_ERROR)
case('F03 分组诊断两组都命中 → product_pass',
     V.classify_guard_attempt(True, resp(422, {'errors': ['属性 activePower 引用编排输出未绑定']}),
                              {'block_status': 422,
                               'diagnostic_term_groups': [['activePower', '编排'], ['未绑定']]}),
     V.PRODUCT_PASS)

# F02：校验接口观察必须先过传输/状态码/响应结构
case('F02 validate 返回 500（带诊断文案）→ test_error，不得判通过',
     V.classify_validate_observation(True, resp(500, {'errors': ['activePower 编排输出未绑定']}),
                                     {'ok_status': (200,), 'diagnostic_terms': ['未绑定']}),
     V.TEST_ERROR)
case('F02 validate 200 但 errors 非数组 → test_error',
     V.classify_validate_observation(True, resp(200, {'errors': 'oops'}),
                                     {'ok_status': (200,), 'diagnostic_terms': ['未绑定']}),
     V.TEST_ERROR)
case('F02 validate 200 但响应非对象 → test_error',
     V.classify_validate_observation(True, resp(200, None, 'plain text'),
                                     {'ok_status': (200,), 'diagnostic_terms': ['未绑定']}),
     V.TEST_ERROR)
case('F02 validate 200 无错误（未拦截）→ 已知缺陷复现',
     V.classify_validate_observation(True, resp(200, {'errors': []}),
                                     {'ok_status': (200,), 'diagnostic_terms': ['未绑定'],
                                      'known_defect': True}),
     V.KNOWN_DEFECT)
case('F02 validate 200 有目标诊断 → product_pass（对照）',
     V.classify_validate_observation(True, resp(200, {'errors': ['编排输出未绑定']}),
                                     {'ok_status': (200,), 'diagnostic_terms': ['未绑定']}),
     V.PRODUCT_PASS)
case('F02 validate 有错误但诊断无关 → test_error',
     V.classify_validate_observation(True, resp(200, {'errors': ['对象缺少名称']}),
                                     {'ok_status': (200,), 'diagnostic_terms': ['未绑定']}),
     V.TEST_ERROR)
case('F02 validate 网络失败 → test_error',
     V.classify_validate_observation(True, resp(None, None, error='URLError'),
                                     {'ok_status': (200,), 'diagnostic_terms': ['未绑定']}),
     V.TEST_ERROR)

# F03-b：诊断文本必须收集「诊断键下的字符串列表」——真实响应的 errors 就是这种形态
# （实现曾漏收集列表内字符串，导致真实 HTTP 复跑时负对照被误判为 test_error）
case('F03 errors 字符串列表被收集 → product_pass',
     V.classify_guard_attempt(True, resp(422, {'errors': ['属性 activePower 引用编排输出未绑定']}),
                              {'block_status': 422, 'diagnostic_terms': ['activePower', '未绑定']}),
     V.PRODUCT_PASS)
case('F03 嵌套 report.errors/items 被收集 → product_pass',
     V.classify_guard_attempt(True, resp(422, {'error': '校验未通过',
                                               'report': {'errors': ['编排输出未绑定'],
                                                          'items': [{'kind': 'propertySource',
                                                                     'issues': ['未绑定']}]}}),
                              {'block_status': 422, 'diagnostic_terms': ['未绑定']}),
     V.PRODUCT_PASS)
case('F03 validate 顶层无 errors 数组 → 结构不符 test_error（契约要求 errors 为数组）',
     V.classify_validate_observation(True, resp(200, {'report': {'errors': ['未绑定']}}),
                                     {'ok_status': (200,), 'diagnostic_terms': ['未绑定']}),
     V.TEST_ERROR)
case('F03 diag_text 不返回回显载荷 → 仍 test_error',
     V.classify_guard_attempt(True, resp(422, {'error': '名称重复', 'payload': {'type': 'flow'}}),
                              {'block_status': 422, 'diagnostic_terms': ['flow']}),
     V.TEST_ERROR)

# F03-c：内层类型/标识键（kind/type/status…）不作为诊断文案 —— 独立验证曾用真实 409 冲突载荷
# （conflicts[].kind='flow'）证明仅凭 kind 值会把无关错误误判成「针对目标」
case('F03 冲突载荷 kind=flow 不得命中 → test_error',
     V.classify_guard_attempt(True, resp(409, {'error': '部分名称已被占用', 'code': 'DUPLICATE_NAME',
                                              'conflicts': [{'packageKey': 'k', 'kind': 'flow',
                                                             'suggestedName': 'x-flow'}]}),
                              {'block_status': 409, 'diagnostic_terms': ['flow']}),
     V.TEST_ERROR)
case('F03 无诊断字段时不得回退搜索整份响应 → test_error',
     V.classify_guard_attempt(True, resp(422, {'scene': 'x', 'state': {'kind': 'flow'}}),
                              {'block_status': 422, 'diagnostic_terms': ['flow']}),
     V.TEST_ERROR)
# F03-d：反向偏差 —— errors 条目用 name/field 承载定位时不得漏收（否则真拦截被判 test_error）
case('F03 errors[].name 承载字段定位被收集 → product_pass',
     V.classify_guard_attempt(True, resp(422, {'errors': [{'name': 'activePower', 'message': '未绑定'}]}),
                              {'block_status': 422,
                               'diagnostic_term_groups': [['activePower'], ['未绑定']]}),
     V.PRODUCT_PASS)
# F03-e：未提供诊断依据时不得自动放行
case('F03 未提供诊断依据且未声明跳过 → test_error',
     V.classify_guard_attempt(True, resp(422, {'errors': ['任意']}), {'block_status': 422}),
     V.TEST_ERROR)
case('F03 显式 diagnostic_not_required=True → product_pass（对照）',
     V.classify_guard_attempt(True, resp(422, {'errors': ['任意']}),
                              {'block_status': 422, 'diagnostic_not_required': True}),
     V.PRODUCT_PASS)

# 配置自检（独立验证发现的两个潜在陷阱，当前调用点不可达但易被误用）
case('用例配置有误：block_status 与 allow_status 重叠 → test_error',
     V.classify_guard_attempt(True, resp(200, {'errors': ['x']}),
                              {'block_status': 200, 'diagnostic_terms': ['x']}),
     V.TEST_ERROR)
case('用例配置有误：版本计数传字符串 → test_error（字典序比较会静默出错）',
     V.classify_guard_attempt(True, resp(422, {'errors': ['未绑定']}),
                              {'block_status': 422, 'diagnostic_terms': ['未绑定'],
                               'require_no_version_increase': True,
                               'version_before': '9', 'version_after': '10'}),
     V.TEST_ERROR)
case('版本计数用整数 → 正常判定（对照）',
     V.classify_guard_attempt(True, resp(422, {'errors': ['未绑定']}),
                              {'block_status': 422, 'diagnostic_terms': ['未绑定'],
                               'require_no_version_increase': True,
                               'version_before': 9, 'version_after': 10}),
     V.NEW_DEFECT)

# ---- 对抗验证（第二轮）：无关错误不得借「泛词同条命中」被算成正确拦截 ----
# 这些文案取自真实产品模板，曾被判定器误判为 product_pass
_R02 = [['activePower', 'RVCluster', 'fl-shell-123'],
        ['未绑定', '输出未绑定', 'OUTPUT_BINDING_MISSING', '尚未绑定', '未选择输出']]
case('对抗：动作绑定「实现方式未选择（项目接口或函数编排）」不得过 R02 两组',
     V.classify_guard_attempt(True, resp(422, {'errors': [
         '动作绑定 Q03Cluster/启动：实现方式未选择（项目接口或函数编排）']}),
         {'block_status': 422, 'diagnostic_term_groups': _R02}),
     V.TEST_ERROR)
case('对抗：存储故障 fail-closed（「读取失败…暂不能校验该绑定」）不得算正确拦截',
     V.classify_guard_attempt(True, resp(422, {'errors': [
         '属性来源 RVCluster.activePower：引用的函数编排 fl-x：读取失败（StorageUnavailable），'
         '暂不能校验该绑定；请稍后重试']}),
         {'block_status': 422, 'diagnostic_term_groups': _R02}),
     V.TEST_ERROR)
case('对抗：无关编排归属错误不得让负对照成立',
     V.classify_guard_attempt(True, resp(422, {'errors': ['额外编排不存在或不属于当前账号：fl-x']}),
                              {'block_status': 422, 'diagnostic_term_groups':
                               [['flow-does-not-exist-abc', 'activePower', 'RVCluster'],
                                ['不存在', '已删除', '未找到', 'NOT_FOUND', '引用无效']]}),
     V.TEST_ERROR)
case('对抗：真 R02 文案（身份+未绑定同条）仍应通过（对照）',
     V.classify_guard_attempt(True, resp(422, {'errors': [
         '属性来源 RVCluster.activePower：引用编排输出尚未绑定来源节点输出']}),
         {'block_status': 422, 'diagnostic_term_groups': _R02}),
     V.PRODUCT_PASS)
case('对抗：两字段条目（errors[].name+message）同条目成一条消息 → 通过（对照）',
     V.classify_guard_attempt(True, resp(422, {'errors': [{'name': 'activePower', 'message': '未绑定'}]}),
                              {'block_status': 422,
                               'diagnostic_term_groups': [['activePower'], ['未绑定']]}),
     V.PRODUCT_PASS)
case('对抗：接受分支不再借用版本门判通过（2xx + gate 且版本相等 → 仍是缺陷复现）',
     V.classify_guard_attempt(True, resp(200, {'version': 'v1'}),
                              {'block_status': 422, 'known_defect': True,
                               'require_no_version_increase': True,
                               'version_before': 2, 'version_after': 2}),
     V.KNOWN_DEFECT)
case('对抗：状态码非整数（字符串/浮点/布尔）不得按业务状态判定',
     V.classify_guard_attempt(True, resp('422', {'errors': ['未绑定']}),
                              {'block_status': 422, 'diagnostic_terms': ['未绑定']}),
     V.TEST_ERROR)
case('对抗：diagnostic_terms 传字符串不得退化为逐字符匹配',
     V.classify_guard_attempt(True, resp(422, {'errors': ['固定值无效']}),
                              {'block_status': 422, 'diagnostic_terms': '未绑定'}),
     V.TEST_ERROR)
case('对抗：空字符串诊断词不得恒真通过',
     V.classify_guard_attempt(True, resp(422, {'errors': ['任意']}),
                              {'block_status': 422, 'diagnostic_terms': ['']}),
     V.TEST_ERROR)
case('对抗：require_no_version_increase 非布尔不得静默关掉证据门',
     V.classify_guard_attempt(True, resp(422, {'errors': ['未绑定']}),
                              {'block_status': 422, 'diagnostic_terms': ['未绑定'],
                               'require_no_version_increase': 0}),
     V.TEST_ERROR)
case('对抗：版本计数回退（2→1）证据自相矛盾 → test_error',
     V.classify_guard_attempt(True, resp(422, {'errors': ['未绑定']}),
                              {'block_status': 422, 'diagnostic_terms': ['未绑定'],
                               'require_no_version_increase': True,
                               'version_before': 2, 'version_after': 1}),
     V.TEST_ERROR)
case('对抗：顶层 field/text 回显不得当诊断',
     V.classify_guard_attempt(True, resp(422, {'error': '完全无关的错误', 'field': 'activePower'}),
                              {'block_status': 422, 'diagnostic_terms': ['activePower']}),
     V.TEST_ERROR)
case('对抗：状态清单 items[].name 回显不得让校验观察判为已拦截',
     V.classify_validate_observation(
         True, resp(200, {'errors': ['数据来源 主表：来源配置格式无效'],
                          'warnings': ['对象映射 X：显示名称属性未绑定数据字段'],
                          'items': [{'kind': 'propertySource', 'id': 'X.activePower',
                                     'name': '属性来源 · X.activePower', 'issues': []}]}),
         {'ok_status': (200,), 'diagnostic_term_groups': _R02, 'known_defect': True}),
     V.TEST_ERROR)
case('对抗：配置类参数类型不符一律 test_error（block_status 传字符串）',
     V.classify_guard_attempt(True, resp(422, {'errors': ['未绑定']}),
                              {'block_status': '422', 'diagnostic_terms': ['未绑定']}),
     V.TEST_ERROR)
case('对抗：precondition_ok 传非布尔（真值字符串）不得算前置成功',
     V.classify_guard_attempt('false', resp(422, {'errors': ['未绑定']}),
                              {'block_status': 422, 'diagnostic_terms': ['未绑定']}),
     V.TEST_ERROR)

# ======================================================================
# G01（高）：嵌套容器不得把「不同诊断」拼成一条 —— 元素边界必须贯穿 report/check
# 反例来源：独立验收用 {'report': {'errors': ['activePower', '输出未绑定']}} 配两组
# 拿到 product_pass（同一条消息凑出「定位+原因」）。下面同时锁死合法对照：
# 同一条目内的多字段仍必须能联合命中。
# ======================================================================
_G01 = [['activePower'], ['输出未绑定']]

case('G01 顶层 errors 两条不同 → test_error',
     V.classify_guard_attempt(True, resp(422, {'errors': ['activePower', '输出未绑定']}),
                              {'block_status': 422, 'diagnostic_term_groups': _G01}),
     V.TEST_ERROR)
case('G01 report 内两条不同 errors 各含一半 → test_error',
     V.classify_guard_attempt(True, resp(422, {'report': {'errors': ['activePower', '输出未绑定']}}),
                              {'block_status': 422, 'diagnostic_term_groups': _G01}),
     V.TEST_ERROR)
case('G01 report 内同一条 error 含定位+原因 → product_pass（对照）',
     V.classify_guard_attempt(True, resp(422, {'report': {'errors': ['属性 X.activePower 引用编排输出未绑定']}}),
                              {'block_status': 422, 'diagnostic_term_groups': _G01}),
     V.PRODUCT_PASS)
case('G01 check.diagnostics 两条各含一半 → test_error',
     V.classify_guard_attempt(True, resp(422, {'check': {'diagnostics': [
         {'level': 'error', 'message': '字段 activePower 的应用方式未选择'},
         {'level': 'error', 'message': '输出未绑定'}]}}),
         {'block_status': 422, 'diagnostic_term_groups': _G01}),
     V.TEST_ERROR)
case('G01 check.diagnostics 同一条目含定位+原因 → product_pass（对照）',
     V.classify_guard_attempt(True, resp(422, {'check': {'diagnostics': [
         {'level': 'error', 'message': '字段 activePower 的输出未绑定'}]}}),
         {'block_status': 422, 'diagnostic_term_groups': _G01}),
     V.PRODUCT_PASS)
case('G01 定位与原因分居 report/check 两处 → test_error（跨信封不得拼接）',
     V.classify_guard_attempt(True, resp(422, {'report': {'errors': ['activePower 未配置']},
                                               'check': {'errors': ['输出未绑定']}}),
                              {'block_status': 422, 'diagnostic_term_groups': _G01}),
     V.TEST_ERROR)
case('G01 report 内 items[].issues 两条各含一半 → test_error',
     V.classify_guard_attempt(True, resp(422, {'report': {'items': [
         {'kind': 'propertySource', 'id': 'X.activePower', 'issues': ['activePower 未配置']},
         {'kind': 'propertySource', 'id': 'X.y', 'issues': ['输出未绑定']}]}}),
         {'block_status': 422, 'diagnostic_term_groups': _G01}),
     V.TEST_ERROR)
case('G01 深一层 report.errors 同一条仍联合命中 → product_pass（对照）',
     V.classify_guard_attempt(True, resp(422, {'report': {'report': {'errors': ['activePower 输出未绑定']}}}),
                              {'block_status': 422, 'diagnostic_term_groups': _G01}),
     V.PRODUCT_PASS)
# 提取层直接锁定：条目边界不丢、且不跨条目拼接
_msg = V.diag_messages(resp(422, {'report': {'errors': ['activePower', '输出未绑定']}}))
case('G01 diag_messages 保留 report.errors 条目边界', {
    'result': V.PRODUCT_PASS if _msg == ['activePower', '输出未绑定'] else 'mismatch',
    'reason': 'diag_messages=%r' % (_msg,)}, V.PRODUCT_PASS)

# ======================================================================
# G02（高）：warning 不得替无关 error 证明「已阻断 / 拦截针对目标」
# 反例来源：{'errors': ['名称重复'], 'warnings': ['activePower 输出未绑定']} 配两组
# 曾被判 product_pass —— 目标内容只出现在提示里，不能证明目标依赖被阻断。
# ======================================================================

case('G02 errors 无关 + warnings 命中 → test_error',
     V.classify_validate_observation(
         True, resp(200, {'errors': ['名称重复'], 'warnings': ['activePower 输出未绑定']}),
         {'ok_status': (200,), 'diagnostic_term_groups': _G01}),
     V.TEST_ERROR)
case('G02 errors 命中目标 → product_pass（对照）',
     V.classify_validate_observation(
         True, resp(200, {'errors': ['属性 X.activePower 引用编排输出未绑定']}),
         {'ok_status': (200,), 'diagnostic_term_groups': _G01}),
     V.PRODUCT_PASS)
case('G02 只有 warnings 命中、errors 为空 → 缺陷复现（不得 product_pass）',
     V.classify_validate_observation(
         True, resp(200, {'errors': [], 'warnings': ['activePower 输出未绑定']}),
         {'ok_status': (200,), 'diagnostic_term_groups': _G01}),
     V.NEW_DEFECT)
case('G02 只有 warnings 命中（已知基线）→ known_defect_reproduced',
     V.classify_validate_observation(
         True, resp(200, {'errors': [], 'warnings': ['activePower 输出未绑定']}),
         {'ok_status': (200,), 'diagnostic_term_groups': _G01, 'known_defect': True}),
     V.KNOWN_DEFECT)
case('G02 guard：拒绝码但目标只在 warning → test_error',
     V.classify_guard_attempt(True, resp(422, {'errors': ['名称重复'], 'warnings': ['activePower 输出未绑定']}),
                              {'block_status': 422, 'diagnostic_term_groups': _G01}),
     V.TEST_ERROR)
case('G02 guard：errors 命中目标 → product_pass（对照）',
     V.classify_guard_attempt(True, resp(422, {'errors': ['属性 X.activePower 引用编排输出未绑定']}),
                              {'block_status': 422, 'diagnostic_term_groups': _G01}),
     V.PRODUCT_PASS)
case('G02 check.diagnostics level=warning 条目不算阻断证据 → test_error',
     V.classify_guard_attempt(True, resp(422, {'errors': ['名称重复'], 'check': {'diagnostics': [
         {'level': 'warning', 'message': 'activePower 输出未绑定'}]}}),
         {'block_status': 422, 'diagnostic_term_groups': _G01}),
     V.TEST_ERROR)
case('G02 check.diagnostics level=error 同一条目命中 → product_pass（对照）',
     V.classify_guard_attempt(True, resp(422, {'errors': ['名称重复'], 'check': {'diagnostics': [
         {'level': 'error', 'message': 'activePower 输出未绑定'}]}}),
         {'block_status': 422, 'diagnostic_term_groups': _G01}),
     V.PRODUCT_PASS)
# 提取层直接锁定：全量诊断含 warning，阻断诊断不含 warning
_all = V.diag_messages(resp(200, {'errors': ['名称重复'], 'warnings': ['activePower 输出未绑定']}))
_blk = V.blocking_diag_messages(resp(200, {'errors': ['名称重复'], 'warnings': ['activePower 输出未绑定']}))
case('G02 blocking_diag_messages 排除 warning 条目', {
    'result': V.PRODUCT_PASS if (_all == ['名称重复', 'activePower 输出未绑定'] and _blk == ['名称重复'])
              else 'mismatch',
    'reason': 'all=%r blocking=%r' % (_all, _blk)}, V.PRODUCT_PASS)

# ======================================================================
# R1（独立对抗验证）：未登记包装键不得把两条无关诊断重新拼成一条
# 反例来源：条目边界只在**已登记键**上生效，中间夹一层未登记键 / 信封值是 dict /
# dict 里套 list，就退回「整棵子树标量 join」，凑出「定位+原因」假命中。
# 修法：列表每个元素各自成条；只有「全部值都是标量的 dict」才合并成一条；含嵌套
# 容器的一律递归其嵌套容器（穿透任意未登记键），自身标量不再成组。
# ======================================================================

case('R1 信封值直接是 dict（errors:{e1,e2}）不得拼成一条 → test_error',
     V.classify_guard_attempt(True, resp(422, {'errors': {'e1': 'activePower', 'e2': '输出未绑定'}}),
                              {'block_status': 422, 'diagnostic_term_groups': _G01}),
     V.TEST_ERROR)
case('R1 未登记包装键（report.wrapper.errors）不得拼成一条 → test_error',
     V.classify_guard_attempt(True, resp(422, {'report': {'wrapper': {'errors': ['activePower', '输出未绑定']}}}),
                              {'block_status': 422, 'diagnostic_term_groups': _G01}),
     V.TEST_ERROR)
case('R1 同一列表元素内嵌两个条目 dict 不得拼成一条 → test_error',
     V.classify_guard_attempt(True, resp(422, {'errors': [
         {'entry_1': {'msg': 'activePower'}, 'entry_2': {'msg': '输出未绑定'}}]}),
         {'block_status': 422, 'diagnostic_term_groups': _G01}),
     V.TEST_ERROR)
case('R1 同一 dict 内两个 list 不得拼成一条 → test_error',
     V.classify_guard_attempt(True, resp(422, {'errors': [
         {'a_list': ['activePower'], 'b_list': ['输出未绑定']}]}),
         {'block_status': 422, 'diagnostic_term_groups': _G01}),
     V.TEST_ERROR)
case('R1 容器自身两个标量字段不得拼成一条 → test_error',
     V.classify_guard_attempt(True, resp(422, {'report': {'title': 'activePower', 'reason_text': '输出未绑定'}}),
                              {'block_status': 422, 'diagnostic_term_groups': _G01}),
     V.TEST_ERROR)
# 合法对照：只要定位与原因在**同一条**消息里，穿过未登记键仍必须命中
case('R1 未登记包装键 + 全标量叶子条目（name+message）→ product_pass（对照）',
     V.classify_guard_attempt(True, resp(422, {'report': {'wrapper': {'errors': [
         {'name': 'activePower', 'message': '未绑定'}]}},
         'title': '校验未通过'}),
         {'block_status': 422, 'diagnostic_term_groups': [['activePower'], ['未绑定']]}),
     V.PRODUCT_PASS)
case('R1 信封 dict 的单条文案含定位+原因 → product_pass（对照）',
     V.classify_guard_attempt(True, resp(422, {'errors': {'e1': '属性 X.activePower 引用编排输出未绑定'}}),
                              {'block_status': 422, 'diagnostic_term_groups': _G01}),
     V.PRODUCT_PASS)
# 位置限制（合并只允许「信封列表的直接元素 + 全标量」）：再包一层就不得合并
case('R1 双层信封列表 [[{a,b}]] 不得合并 → test_error',
     V.classify_guard_attempt(True, resp(422, {'errors': [[{'a': 'activePower', 'b': '输出未绑定'}]]}),
                              {'block_status': 422, 'diagnostic_term_groups': _G01}),
     V.TEST_ERROR)
case('R1 包装内的全标量 dict（errors.e1:{a,b}）不得合并 → test_error',
     V.classify_guard_attempt(True, resp(422, {'errors': {'e1': {'a': 'activePower', 'b': '输出未绑定'}}}),
                              {'block_status': 422, 'diagnostic_term_groups': _G01}),
     V.TEST_ERROR)
case('R1 check.diagnostics[].内嵌 dict 不得合并 → test_error',
     V.classify_guard_attempt(True, resp(422, {'check': {'diagnostics': [
         {'level': 'error', 'info': {'a': 'activePower', 'b': '输出未绑定'}}]}}),
         {'block_status': 422, 'diagnostic_term_groups': _G01}),
     V.TEST_ERROR)
case('R1 双层信封列表内单条含定位+原因 → product_pass（对照）',
     V.classify_guard_attempt(True, resp(422, {'errors': [[{'a': '属性 X.activePower 引用编排输出未绑定'}]]}),
                              {'block_status': 422, 'diagnostic_term_groups': _G01}),
     V.PRODUCT_PASS)
case('R1 未登记包装键下单条含定位+原因 → product_pass（对照）',
     V.classify_guard_attempt(True, resp(422, {'check': {'diagnostics': [
         {'level': 'error', 'wrapped': {'a': '属性 X.activePower 引用编排输出未绑定'}}]}}),
         {'block_status': 422, 'diagnostic_term_groups': _G01}),
     V.PRODUCT_PASS)
# 提取层直接锁定：未登记包装键下的两条诊断仍是两条，且不丢条目边界
_r1 = V.diag_messages(resp(422, {'report': {'wrapper': {'errors': ['activePower', '输出未绑定']}}}))
case('R1 diag_messages 穿透未登记包装键且保留条目边界', {
    'result': V.PRODUCT_PASS if _r1 == ['activePower', '输出未绑定'] else 'mismatch',
    'reason': 'diag_messages=%r' % (_r1,)}, V.PRODUCT_PASS)

# ======================================================================
# R2（独立对抗验证，真实可达）：items[].status 语义必须纳入阻断判定
# project_validation 用 status 表达严重性：invalid=阻断、unconfigured=待补全、valid=正常。
# 未声明 status 时维持「按阻断处理」，避免把真实拦截判死。
# ======================================================================

_R2_ITEMS = {'errors': ['数据连接名称重复：主数据库'],
             'items': [{'kind': 'propertySource', 'id': 'Station.activePower',
                        'status': 'unconfigured', 'issues': ['activePower 输出未绑定']}]}
case('R2 validate：items[].status=unconfigured 不算阻断证据 → test_error',
     V.classify_validate_observation(True, resp(200, _R2_ITEMS),
                                     {'ok_status': (200,), 'diagnostic_term_groups': _G01}),
     V.TEST_ERROR)
case('R2 guard：items[].status=unconfigured 不算阻断证据 → test_error',
     V.classify_guard_attempt(True, resp(422, _R2_ITEMS),
                              {'block_status': 422, 'diagnostic_term_groups': _G01}),
     V.TEST_ERROR)
case('R2 validate：items[].status=invalid 且 issues 命中 → product_pass（对照）',
     V.classify_validate_observation(
         True, resp(200, {'errors': ['数据连接名称重复：主数据库'],
                          'items': [{'kind': 'propertySource', 'id': 'Station.activePower',
                                     'status': 'invalid', 'issues': ['activePower 输出未绑定']}]}),
         {'ok_status': (200,), 'diagnostic_term_groups': _G01}),
     V.PRODUCT_PASS)
case('R2 validate：items[] 未声明 status 且 issues 命中 → product_pass（对照，不得判死）',
     V.classify_validate_observation(
         True, resp(200, {'errors': ['数据连接名称重复：主数据库'],
                          'items': [{'kind': 'propertySource', 'id': 'Station.activePower',
                                     'issues': ['activePower 输出未绑定']}]}),
         {'ok_status': (200,), 'diagnostic_term_groups': _G01}),
     V.PRODUCT_PASS)
case('R2 guard：items[].status=error 且 issues 命中 → product_pass（对照）',
     V.classify_guard_attempt(
         True, resp(422, {'errors': ['名称重复'],
                          'items': [{'status': 'error', 'issues': ['activePower 输出未绑定']}]}),
         {'block_status': 422, 'diagnostic_term_groups': _G01}),
     V.PRODUCT_PASS)
case('R2 guard：items[].status=VALID（大小写不敏感）不算阻断证据 → test_error',
     V.classify_guard_attempt(
         True, resp(422, {'errors': ['名称重复'],
                          'items': [{'status': 'VALID', 'issues': ['activePower 输出未绑定']}]}),
         {'block_status': 422, 'diagnostic_term_groups': _G01}),
     V.TEST_ERROR)

# ======================================================================
# R3（独立对抗验证）：note/notes/detail/details 是补充说明，不得当阻断证据
# ======================================================================
case('R3 guard：note 里的目标内容不算阻断证据 → test_error',
     V.classify_guard_attempt(True, resp(422, {'errors': ['名称重复'], 'note': 'activePower 输出未绑定'}),
                              {'block_status': 422, 'diagnostic_term_groups': _G01}),
     V.TEST_ERROR)
case('R3 validate：details 里的目标内容不算阻断证据 → test_error',
     V.classify_validate_observation(
         True, resp(200, {'errors': ['名称重复'], 'details': {'note': ['activePower 输出未绑定']}}),
         {'ok_status': (200,), 'diagnostic_term_groups': _G01}),
     V.TEST_ERROR)
_r3_all = V.diag_messages(resp(422, {'errors': ['名称重复'], 'note': 'activePower 输出未绑定'}))
_r3_blk = V.blocking_diag_messages(resp(422, {'errors': ['名称重复'], 'note': 'activePower 输出未绑定'}))
case('R3 diag_messages 保留 note（报告用），blocking 不含它', {
    'result': V.PRODUCT_PASS if (_r3_all == ['名称重复', 'activePower 输出未绑定'] and _r3_blk == ['名称重复'])
              else 'mismatch',
    'reason': 'all=%r blocking=%r' % (_r3_all, _r3_blk)}, V.PRODUCT_PASS)

# ======================================================================
# R4（独立对抗验证）：level/severity/status 要向下找最近一层，但只在同一条目子树内
# ======================================================================
case('R4 条目内嵌 dict 的 level=warning 使该条非阻断 → test_error',
     V.classify_guard_attempt(
         True, resp(422, {'errors': ['名称重复'],
                          'report': {'detail': {'level': 'warning'}, 'message': 'activePower 输出未绑定'}}),
         {'block_status': 422, 'diagnostic_term_groups': _G01}),
     V.TEST_ERROR)
case('R4 条目自身 level=error 压过内嵌 warning → product_pass（对照，就近优先）',
     V.classify_guard_attempt(
         True, resp(422, {'errors': [{'level': 'error', 'report': {'detail': {'level': 'warning'}},
                                      'message': 'activePower 输出未绑定'}],
                          'warnings': ['无关提示']}),
         {'block_status': 422, 'diagnostic_term_groups': _G01}),
     V.PRODUCT_PASS)
case('R4 兄弟条目的 warning 不得否掉本条目 → product_pass（对照，不跨条目生效）',
     V.classify_guard_attempt(
         True, resp(422, {'errors': [{'level': 'error', 'message': 'activePower 输出未绑定'}],
                          'check': {'diagnostics': [{'level': 'warning', 'message': 'activePower 无关提示'}]}}),
         {'block_status': 422, 'diagnostic_term_groups': _G01}),
     V.PRODUCT_PASS)

# 12) 旧枚举映射与汇总（含 crash 行按 test_error 归并）case('旧枚举 pass→product_pass', {'result': V.to_result('pass'), 'reason': ''}, V.PRODUCT_PASS)
case('旧枚举 fail→缺陷复现', {'result': V.to_result('fail'), 'reason': ''}, V.NEW_DEFECT)
rows = [{'case': 'A', 'verdict': 'pass'}, {'case': 'B', 'verdict': 'known'},
        {'case': 'C1', 'verdict': 'fail', 'kind': 'crash'}, {'case': 'D', 'verdict': 'info'}]
counted, _ = V.summarize_rows(rows)
case('汇总：pass/known/crash/info', {'result': 'product_pass' if counted == {'product_pass': 1,
                                                                          'known_defect_reproduced': 1,
                                                                          'test_error': 1, 'info': 1}
                                     else 'mismatch', 'reason': str(counted)},
     V.PRODUCT_PASS)

failed = [c for c in CASES if not c['ok']]
print('\n== deep_verdicts_test: %d 项，通过 %d，失败 %d ==' % (len(CASES), len(CASES) - len(failed), len(failed)))
for item in failed:
    print('  FAIL %s: got=%s want=%s' % (item['name'], item['got'], item['want']))
sys.exit(1 if failed else 0)
