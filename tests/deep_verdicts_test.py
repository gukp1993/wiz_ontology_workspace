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

# 12) 旧枚举映射与汇总（含 crash 行按 test_error 归并）
case('旧枚举 pass→product_pass', {'result': V.to_result('pass'), 'reason': ''}, V.PRODUCT_PASS)
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
