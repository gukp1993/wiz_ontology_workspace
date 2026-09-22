"""D01 能力 profile 与配置预检回归（协议基线：batch_contracts.py @ 87d1473）。

被测模块：workbench/ontology_build/budget_profile.py（纯函数公开模块）。
契约来源：batch_contracts.py（D00 冻结参考实现）+ 接口文档 08 §14；
需求：《本体生成控输出_整合方案与并行开发计划_v3.md》§3（冻结预算配置）/ §8 D01 行。

八个场景：
1. 再导出与契约同源：build_profile/validate_profile/budget_view/input_reserve_tokens
   函数身份等同 batch_contracts（单一事实来源，禁止复制逻辑）；环境变量名与默认值一致。
2. 全量合法 env → enabled、errors 空、effective 三值正确
   （outputCap=min(L,limit)、targetBudget=floor(L×ratio)、inputReserve=max(2048,ceil(0.02C))），
   ratio 边界 0.25/0.80 闭区间合法，effective_limits 摘要一致。
3. 启用但缺 WIZ_BUILD_CONTEXT_TOKENS / 缺 WIZ_BUILD_OUTPUT_LIMIT_TOKENS / 缺绑定
   → BUDGET_PROFILE_REQUIRED，effective 全 None，request_budget_errors 可读。
4. 非法值（非整数、≤0、ratio 越界 <0.25 或 >0.80）→ BUDGET_CONFIG_INVALID，
   非法项保持默认值，effective 不产出。
5. 绑定不符：validate_profile(profile, provider_id='其他') → BUDGET_PROFILE_MISMATCH，
   provider 与 model 分别验证；匹配时不追加。
6. 未启用（缺省）→ enabled=False、errors 空、effective 全 None；非法配置也不收集错误。
7. 2 万上限 profile（outputLimitTokens=20000 < requestOutputTokens=32000）
   → effectiveOutputCap=20000（绝不发送 32000）。
8. 零副作用：调用前后 os.environ 浅拷贝不变、注入 env dict 不被修改；
   budget_view 含全部冻结字段且 codec 非法值回退 legacy-v1。

测试不依赖真实环境变量（env 全部注入 dict；tests/run.py 亦会剥除 WIZ_*），
不访问网络、不写任何文件、不连数据库。

运行：python3 tests/test_ontology_build_budget_profile.py
"""
import json
import math
import os
import sys
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from workbench.ontology_build import batch_contracts as contracts  # noqa: E402
from workbench.ontology_build import budget_profile as bp  # noqa: E402

PASSED = []
FAILED = []
SEQ = [0]


def _short(value, limit=300):
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = repr(value)
    return text if len(text) <= limit else text[:limit] + '…'


def check(cond, message, actual=None):
    SEQ[0] += 1
    if cond:
        PASSED.append(message)
        print('通过 %d) %s' % (SEQ[0], message))
        return True
    FAILED.append(message)
    print('[失败] %d) %s' % (SEQ[0], message))
    if actual is not None:
        print('  实际: ' + _short(actual))
    return False


# --- 公共 env -----------------------------------------------------------------------

VALID_ENV = {
    bp.ADAPTIVE_ENV: '1',
    bp.PROFILE_PROVIDER_ENV: 'prov-a',
    bp.PROFILE_MODEL_ENV: 'model-a',
    bp.CONTEXT_TOKENS_ENV: '200000',
    bp.OUTPUT_LIMIT_ENV: '50000',
    bp.REQUEST_OUTPUT_ENV: '40000',
    bp.TARGET_RATIO_ENV: '0.50',
}
# 全量合法 env 的期望值：L=min(40000,50000)=40000；T=floor(40000×0.50)=20000；
# reserve=max(2048, ceil(0.02×200000))=4000。
EXPECT_CAP = min(40000, 50000)
EXPECT_TARGET = int(math.floor(EXPECT_CAP * 0.50))
EXPECT_RESERVE = max(2048, int(math.ceil(0.02 * 200000)))
EMPTY_EFFECTIVE = {'outputCap': None, 'targetBudget': None, 'inputReserve': None}


def codes_of(profile):
    return [str((issue or {}).get('code') or '') for issue in profile.get('errors') or []]


# --- 场景 ----------------------------------------------------------------------------

def scenario_reexports_same_source():
    """场景1：再导出与 batch_contracts 同源——函数身份等同，不复制逻辑。"""
    check(bp.build_profile is contracts.build_profile, '场景1 build_profile 与契约同源（函数身份）')
    check(bp.validate_profile is contracts.validate_profile, '场景1 validate_profile 与契约同源')
    check(bp.budget_view is contracts.budget_view, '场景1 budget_view 与契约同源')
    check(bp.input_reserve_tokens is contracts.input_reserve_tokens,
          '场景1 input_reserve_tokens 与契约同源')
    check(bp.ADAPTIVE_ENV == 'WIZ_BUILD_ADAPTIVE_BATCHING', '场景1 开关环境变量名与契约一致',
          bp.ADAPTIVE_ENV)
    for attr, literal in (
        ('ADAPTIVE_ENV', 'WIZ_BUILD_ADAPTIVE_BATCHING'),
        ('PROFILE_PROVIDER_ENV', 'WIZ_BUILD_PROFILE_PROVIDER_ID'),
        ('PROFILE_MODEL_ENV', 'WIZ_BUILD_PROFILE_MODEL'),
        ('CONTEXT_TOKENS_ENV', 'WIZ_BUILD_CONTEXT_TOKENS'),
        ('OUTPUT_LIMIT_ENV', 'WIZ_BUILD_OUTPUT_LIMIT_TOKENS'),
        ('REQUEST_OUTPUT_ENV', 'WIZ_BUILD_REQUEST_OUTPUT_TOKENS'),
        ('TARGET_RATIO_ENV', 'WIZ_BUILD_OUTPUT_TARGET_RATIO'),
        ('OUTPUT_CODEC_ENV', 'WIZ_BUILD_OUTPUT_CODEC'),
    ):
        check(getattr(contracts, attr) == getattr(bp, attr) == literal,
              '场景1 环境变量 %s 以契约为准' % literal)
    check(bp.DEFAULT_REQUEST_OUTPUT_TOKENS == contracts.DEFAULT_REQUEST_OUTPUT_TOKENS == 32000,
          '场景1 默认请求输出 32000 与契约一致')
    check(bp.DEFAULT_TARGET_RATIO == contracts.DEFAULT_TARGET_RATIO == 0.50,
          '场景1 默认比例 0.50 与契约一致')
    check((bp.TARGET_RATIO_MIN, bp.TARGET_RATIO_MAX) == (contracts.TARGET_RATIO_MIN,
                                                         contracts.TARGET_RATIO_MAX) == (0.25, 0.80),
          '场景1 比例边界 0.25–0.80 与契约一致')
    check(bp.BUDGET_PROFILE_REQUIRED == 'BUDGET_PROFILE_REQUIRED'
          and bp.BUDGET_PROFILE_MISMATCH == 'BUDGET_PROFILE_MISMATCH'
          and bp.BUDGET_CONFIG_INVALID == 'BUDGET_CONFIG_INVALID',
          '场景1 三枚预检错误码与契约逐字一致')


def scenario_valid_full_env():
    """场景2：全量合法 env → enabled、errors 空、effective 三值按冻结公式。"""
    profile = bp.build_profile(dict(VALID_ENV))
    check(profile['enabled'] is True, '场景2 全量合法 env 时 enabled=True', profile)
    check(profile['errors'] == [], '场景2 errors 为空', profile['errors'])
    check(profile['providerId'] == 'prov-a' and profile['model'] == 'model-a',
          '场景2 绑定标识原样读入', profile)
    check(profile['contextTokens'] == 200000 and profile['outputLimitTokens'] == 50000,
          '场景2 C/L 原样读入', profile)
    check(profile['requestOutputTokens'] == 40000 and profile['targetRatio'] == 0.50,
          '场景2 请求输出与比例原样读入', profile)
    check(profile['limitsSource'] == 'configured', '场景2 缺省来源 configured', profile)
    effective = profile['effective']
    check(effective['outputCap'] == EXPECT_CAP == 40000,
          '场景2 outputCap=min(request 40000, limit 50000)=40000', effective)
    check(effective['targetBudget'] == EXPECT_TARGET == 20000,
          '场景2 targetBudget=floor(40000×0.50)=20000', effective)
    check(effective['inputReserve'] == EXPECT_RESERVE == 4000,
          '场景2 inputReserve=max(2048, ceil(0.02×200000))=4000', effective)
    check(bp.request_budget_errors(profile) == [], '场景2 request_budget_errors 为空')
    check(bp.request_budget_errors(profile, provider_id='prov-a', model='model-a') == [],
          '场景2 绑定匹配时 request_budget_errors 为空')
    limits = bp.effective_limits(profile)
    check(limits['outputCap'] == 40000 and limits['targetBudget'] == 20000
          and limits['inputReserve'] == 4000, '场景2 effective_limits 三值与 profile.effective 一致',
          limits)
    check(limits['contextTokens'] == 200000 and limits['outputLimitTokens'] == 50000
          and limits['requestOutputTokens'] == 40000 and limits['targetRatio'] == 0.50,
          '场景2 effective_limits 携带 C/L/请求输出/比例', limits)
    check(limits['planTargets'] == 4096 and limits['planJobs'] == 512
          and limits['planAttempts'] == 1024 and limits['checkpointBytes'] == 1048576,
          '场景2 effective_limits 携带冻结规模上限（4096/512/1024/1MiB）', limits)
    # ratio 边界闭区间合法（契约：TARGET_RATIO_MIN <= value <= TARGET_RATIO_MAX）
    for raw, expect in (('0.25', 0.25), ('0.80', 0.80)):
        edge = bp.build_profile(dict(VALID_ENV, **{bp.TARGET_RATIO_ENV: raw}))
        check(edge['errors'] == [] and abs(edge['targetRatio'] - expect) < 1e-9,
              '场景2 ratio 边界 %s 合法（闭区间）' % raw, edge)
    # 启用值解析变体（契约：1/true/yes/on）
    for raw in ('1', 'true', 'yes', 'on'):
        variant = dict(VALID_ENV)
        variant[bp.ADAPTIVE_ENV] = raw
        check(bp.build_profile(variant)['enabled'] is True,
              '场景2 开关值 %s 解析为启用' % raw)


def scenario_missing_required():
    """场景3：启用但缺 C / 缺 L / 缺绑定 → BUDGET_PROFILE_REQUIRED，effective 全 None。"""
    env = {key: value for key, value in VALID_ENV.items() if key != bp.CONTEXT_TOKENS_ENV}
    profile = bp.build_profile(env)
    check(codes_of(profile) == ['BUDGET_PROFILE_REQUIRED'],
          '场景3 缺 WIZ_BUILD_CONTEXT_TOKENS → BUDGET_PROFILE_REQUIRED', profile['errors'])
    check(profile['contextTokens'] is None and profile['effective'] == EMPTY_EFFECTIVE,
          '场景3 缺 C 时 effective 全 None', profile['effective'])
    errors = bp.request_budget_errors(profile)
    check(len(errors) == 1 and errors[0][0] == 'BUDGET_PROFILE_REQUIRED'
          and bp.CONTEXT_TOKENS_ENV in errors[0][1],
          '场景3 request_budget_errors 指出缺失项 WIZ_BUILD_CONTEXT_TOKENS（中文可读）', errors)

    env = {key: value for key, value in VALID_ENV.items() if key != bp.OUTPUT_LIMIT_ENV}
    profile = bp.build_profile(env)
    check(codes_of(profile) == ['BUDGET_PROFILE_REQUIRED'],
          '场景3 缺 WIZ_BUILD_OUTPUT_LIMIT_TOKENS → BUDGET_PROFILE_REQUIRED', profile['errors'])
    check(profile['outputLimitTokens'] is None and profile['effective'] == EMPTY_EFFECTIVE,
          '场景3 缺 L 时 effective 全 None（缺失不自动套 128K）', profile['effective'])
    errors = bp.request_budget_errors(profile)
    check(len(errors) == 1 and errors[0][0] == 'BUDGET_PROFILE_REQUIRED'
          and bp.OUTPUT_LIMIT_ENV in errors[0][1],
          '场景3 request_budget_errors 指出缺失项 WIZ_BUILD_OUTPUT_LIMIT_TOKENS', errors)

    env = {key: value for key, value in VALID_ENV.items()
           if key not in (bp.PROFILE_PROVIDER_ENV, bp.PROFILE_MODEL_ENV)}
    profile = bp.build_profile(env)
    check(codes_of(profile) == ['BUDGET_PROFILE_REQUIRED'],
          '场景3 缺 provider/model 绑定 → BUDGET_PROFILE_REQUIRED', profile['errors'])
    errors = bp.request_budget_errors(profile)
    check(len(errors) == 1 and 'provider' in errors[0][1],
          '场景3 request_budget_errors 指出绑定缺失', errors)


def scenario_invalid_values():
    """场景4：非整数 / ≤0 / ratio 越界 → BUDGET_CONFIG_INVALID；非法项保持默认。"""
    cases = [
        ({bp.CONTEXT_TOKENS_ENV: 'abc'}, 'C 非整数'),
        ({bp.CONTEXT_TOKENS_ENV: '0'}, 'C 为 0'),
        ({bp.CONTEXT_TOKENS_ENV: '-5'}, 'C 为负'),
        ({bp.CONTEXT_TOKENS_ENV: '12.5'}, 'C 为小数字符串'),
        ({bp.OUTPUT_LIMIT_ENV: 'abc'}, 'L 非整数'),
        ({bp.OUTPUT_LIMIT_ENV: '0'}, 'L 为 0'),
        ({bp.REQUEST_OUTPUT_ENV: '0'}, '请求输出为 0'),
        ({bp.REQUEST_OUTPUT_ENV: '-1'}, '请求输出为负'),
        ({bp.TARGET_RATIO_ENV: '0.10'}, 'ratio 低于 0.25'),
        ({bp.TARGET_RATIO_ENV: '0.95'}, 'ratio 高于 0.80'),
        ({bp.TARGET_RATIO_ENV: 'abc'}, 'ratio 非数字'),
    ]
    for override, label in cases:
        env = dict(VALID_ENV)
        env.update(override)
        profile = bp.build_profile(env)
        check(codes_of(profile) == ['BUDGET_CONFIG_INVALID'],
              '场景4 %s → BUDGET_CONFIG_INVALID' % label, profile['errors'])
        check(profile['effective'] == EMPTY_EFFECTIVE,
              '场景4 %s 时 effective 不产出' % label, profile['effective'])
        errors = bp.request_budget_errors(profile)
        check(len(errors) == 1 and errors[0][0] == 'BUDGET_CONFIG_INVALID'
              and '预算配置非法' in errors[0][1],
              '场景4 %s 的 request_budget_errors 可读' % label, errors)
    # 非法项回落默认值，其余派生不受污染
    profile = bp.build_profile(dict(VALID_ENV, **{bp.TARGET_RATIO_ENV: '9.9'}))
    check(profile['targetRatio'] == 0.50, '场景4 非法 ratio 保持默认 0.50', profile['targetRatio'])
    profile = bp.build_profile(dict(VALID_ENV, **{bp.REQUEST_OUTPUT_ENV: 'oops'}))
    check(profile['requestOutputTokens'] == 32000,
          '场景4 非法请求输出保持默认 32000', profile['requestOutputTokens'])


def scenario_binding_mismatch():
    """场景5：绑定不符 → BUDGET_PROFILE_MISMATCH；provider 与 model 分别验证。"""
    profile = bp.build_profile(dict(VALID_ENV))
    check(profile['errors'] == [], '场景5 前置：profile 本身合法')
    errors = contracts.validate_profile(profile, provider_id='prov-b')
    check([issue['code'] for issue in errors] == ['BUDGET_PROFILE_MISMATCH']
          and errors[0]['message'] == 'providerId',
          '场景5 validate_profile：provider 不符 → MISMATCH（指明 providerId）', errors)
    errors = contracts.validate_profile(profile, model='model-b')
    check([issue['code'] for issue in errors] == ['BUDGET_PROFILE_MISMATCH']
          and errors[0]['message'] == 'model',
          '场景5 validate_profile：model 不符 → MISMATCH（指明 model）', errors)
    errors = contracts.validate_profile(profile, provider_id='prov-b', model='model-b')
    check([issue['message'] for issue in errors] == ['providerId', 'model'],
          '场景5 provider 与 model 分别验证（两条独立问题）', errors)
    wrapped = bp.request_budget_errors(profile, provider_id='prov-b')
    check(len(wrapped) == 1 and wrapped[0][0] == 'BUDGET_PROFILE_MISMATCH'
          and wrapped[0][1].endswith('providerId'),
          '场景5 request_budget_errors：provider 不符可读', wrapped)
    wrapped = bp.request_budget_errors(profile, provider_id='prov-a', model='model-b')
    check(len(wrapped) == 1 and wrapped[0][0] == 'BUDGET_PROFILE_MISMATCH'
          and wrapped[0][1].endswith('model'),
          '场景5 request_budget_errors：model 不符可读', wrapped)
    check(bp.request_budget_errors(profile, provider_id='prov-a', model='model-a') == [],
          '场景5 绑定完全匹配时不追加错误')
    disabled = bp.build_profile({})
    check(bp.request_budget_errors(disabled, provider_id='x', model='y') == [],
          '场景5 未启用 profile 不核对绑定（契约：仅 enabled 时核对）')


def scenario_disabled_default():
    """场景6：未启用（缺省）→ enabled=False、errors 空、effective 全 None。"""
    profile = bp.build_profile({})
    check(profile['enabled'] is False, '场景6 缺省未启用', profile)
    check(profile['errors'] == [], '场景6 未启用时 errors 为空', profile['errors'])
    check(profile['effective'] == EMPTY_EFFECTIVE, '场景6 未启用时 effective 全 None',
          profile['effective'])
    check(profile['contextTokens'] is None and profile['outputLimitTokens'] is None,
          '场景6 未启用时 C/L 为 None', profile)
    check(profile['requestOutputTokens'] == 32000 and profile['targetRatio'] == 0.50,
          '场景6 未启用仍填充默认值（capabilities 展示用）', profile)
    check(bp.build_profile({bp.ADAPTIVE_ENV: '0'})['enabled'] is False, '场景6 显式 0 为关闭')
    check(bp.build_profile({bp.ADAPTIVE_ENV: 'off'})['enabled'] is False, '场景6 非白名单值为关闭')
    # errors 只在 enabled 时收集：未启用即使配置非法也不产生错误
    noisy = bp.build_profile({bp.CONTEXT_TOKENS_ENV: 'abc', bp.TARGET_RATIO_ENV: '9.9',
                              bp.REQUEST_OUTPUT_ENV: '-1'})
    check(noisy['enabled'] is False and noisy['errors'] == [],
          '场景6 未启用时非法配置不收集错误（契约）', noisy['errors'])
    check(bp.request_budget_errors(noisy) == [], '场景6 未启用 profile 预检通过（调用方按开关判定）')
    limits = bp.effective_limits(noisy)
    check(limits['contextTokens'] is None and limits['outputCap'] is None
          and limits['targetBudget'] is None and limits['inputReserve'] is None,
          '场景6 未启用时 effective_limits 派生值为 None', limits)
    check(limits['planTargets'] == 4096 and limits['checkpointBytes'] == 1048576,
          '场景6 未启用时静态规模上限仍展示', limits)


def scenario_output_cap_clamped():
    """场景7：2 万上限 profile → effectiveOutputCap=20000，绝不发送缺省 32000。"""
    env = {key: value for key, value in VALID_ENV.items()
           if key not in (bp.REQUEST_OUTPUT_ENV, bp.OUTPUT_LIMIT_ENV)}
    env[bp.OUTPUT_LIMIT_ENV] = '20000'   # L 上限 2 万 < 缺省请求输出 32000
    profile = bp.build_profile(env)
    check(profile['requestOutputTokens'] == 32000, '场景7 请求输出取缺省 32000', profile)
    check(profile['outputLimitTokens'] == 20000, '场景7 已核实输出上限 20000', profile)
    effective = profile['effective']
    check(effective['outputCap'] == 20000,
          '场景7 effectiveOutputCap=min(32000,20000)=20000（绝不发送 32000）', effective)
    check(effective['targetBudget'] == int(math.floor(20000 * 0.50)) == 10000,
          '场景7 targetBudget=floor(20000×0.50)=10000', effective)
    check(effective['inputReserve'] == max(2048, int(math.ceil(0.02 * 200000))) == 4000,
          '场景7 inputReserve=max(2048, ceil(0.02×200000))=4000', effective)
    view = bp.budget_view(profile, 'legacy-v1')
    check(view['effectiveOutputCap'] == 20000, '场景7 budget_view.effectiveOutputCap=20000', view)
    limits = bp.effective_limits(profile)
    check(limits['outputCap'] == 20000 and limits['requestOutputTokens'] == 32000
          and limits['outputLimitTokens'] == 20000,
          '场景7 effective_limits 同口径（cap=20000，请求输出原值展示）', limits)


def scenario_purity_and_budget_view():
    """场景8：零副作用（os.environ/注入 env 均不被修改）+ budget_view 冻结字段与 codec 回退。"""
    before = dict(os.environ)
    profile = bp.build_profile()                       # 缺省读 os.environ（只读）
    bp.request_budget_errors(profile, provider_id='x', model='y')
    bp.effective_limits(profile)
    bp.budget_view(profile, 'compact-v1')
    bp.input_reserve_tokens(123456)
    after = dict(os.environ)
    check(before == after, '场景8 os.environ 调用前后不变（浅拷贝对比）',
          {key: (before.get(key), after.get(key)) for key in set(before) | set(after)
           if before.get(key) != after.get(key)})
    check(sorted(before.keys()) == sorted(after.keys()), '场景8 os.environ 键集合不变')
    injected = dict(VALID_ENV)
    snapshot = dict(injected)
    bp.build_profile(injected)
    check(injected == snapshot, '场景8 注入 env dict 不被修改', injected)

    # budget_view：全部冻结字段（接口文档 08 §14.4）
    good = bp.build_profile(dict(VALID_ENV))
    view = bp.budget_view(good, 'legacy-v1')
    expected_keys = {'enabled', 'configured', 'configErrors', 'contextTokens',
                     'requestOutputTokens', 'outputLimitTokens', 'effectiveOutputCap',
                     'targetRatio', 'limitsSource', 'planTargets', 'planJobs',
                     'planAttempts', 'checkpointBytes', 'codec'}
    check(set(view.keys()) == expected_keys, '场景8 budget_view 含全部冻结字段（不多不少）',
          sorted(view.keys()))
    check(view['enabled'] is True and view['configured'] is True and view['configErrors'] == [],
          '场景8 合法 profile：enabled/configured=True、无 configErrors', view)
    check(view['effectiveOutputCap'] == 40000 and view['contextTokens'] == 200000
          and view['outputLimitTokens'] == 50000 and view['requestOutputTokens'] == 40000
          and view['targetRatio'] == 0.50 and view['limitsSource'] == 'configured',
          '场景8 预算数值字段与 profile 一致', view)
    check(view['planTargets'] == 4096 and view['planJobs'] == 512
          and view['planAttempts'] == 1024 and view['checkpointBytes'] == 1048576,
          '场景8 规模上限字段为冻结值（4096/512/1024/1MiB）', view)
    check(view['codec'] == 'legacy-v1', '场景8 合法 codec 原样保留')
    check(bp.budget_view(good, 'compact-v1')['codec'] == 'compact-v1',
          '场景8 compact-v1 合法保留')
    for bad_codec in ('bogus-codec', '', None, 'LEGACY-V1'):
        check(bp.budget_view(good, bad_codec)['codec'] == 'legacy-v1',
              '场景8 codec 非法值 %r 回退 legacy-v1' % (bad_codec,))
    # 带 errors 的 profile：configured=False，configErrors 原样透出
    broken = bp.build_profile({bp.ADAPTIVE_ENV: '1'})
    view_bad = bp.budget_view(broken, 'legacy-v1')
    check(view_bad['enabled'] is True and view_bad['configured'] is False
          and len(view_bad['configErrors']) > 0,
          '场景8 有 errors 时 configured=False 且原样透出', view_bad)
    off = bp.budget_view(bp.build_profile({}), 'legacy-v1')
    check(off['enabled'] is False and off['configured'] is False
          and off['effectiveOutputCap'] is None,
          '场景8 未启用：enabled/configured=False、cap=None', off)


SCENARIOS = (
    ('reexports_same_source', scenario_reexports_same_source),
    ('valid_full_env', scenario_valid_full_env),
    ('missing_required', scenario_missing_required),
    ('invalid_values', scenario_invalid_values),
    ('binding_mismatch', scenario_binding_mismatch),
    ('disabled_default', scenario_disabled_default),
    ('output_cap_clamped', scenario_output_cap_clamped),
    ('purity_and_budget_view', scenario_purity_and_budget_view),
)


def main():
    for name, func in SCENARIOS:
        print('--- %s ---' % name)
        try:
            func()
        except Exception as exc:  # 场景自身异常不吞：计失败并打印堆栈
            traceback.print_exc()
            check(False, '场景 %s 自身异常：%s' % (name, type(exc).__name__), str(exc))
        print('')
    total = len(PASSED) + len(FAILED)
    print('========== 汇总 ==========')
    print('通过 %d / %d' % (len(PASSED), total))
    for name in FAILED:
        print('  失败: ' + name)
    if total == 0:
        print('[错误] 未执行任何断言——不算通过')
        return 2
    return 0 if not FAILED else 1


if __name__ == '__main__':
    sys.exit(main())
