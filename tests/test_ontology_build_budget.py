"""D02 完整请求估算与反馈收缩回归（协议基线：batch_contracts.py，含 3995586 后注释级修订）。

被测模块：workbench/ontology_build/budget.py（纯函数）。
契约来源：batch_contracts.py「估算」节（estimate_request/cold_output_estimate 冻结实现）
+ 需求《本体生成控输出_整合方案与并行开发计划_v3.md》§4.2/§4.3、§8 D02 行。

九个场景：
1. utf8_proxy 与契约逐字一致（中文/英文/多消息/空输入；函数身份等同）。
2. slots：孤立事实 1、adjacent_window 4、data 无结构 4、同 targetId / 同 factId+selector
   去重（身份只计一次）。
3. 冷启动公式边界（slots=0/None→按 1；V 下限 1024；公式值逐点核对）。
4. 校准桶：19 样本 max×1.25、第 20 样本切换 P90×1.25（线性插值）；截断样本被拒；
   unknown usage（None/非法/负数）拒绝；slots 非法拒绝；混合 kind/provider 维度不污染；
   桶滑窗有界（22 样本保留最近 20）。
5. expected_output 永不低于冷启动（校准更低/缺失/非法均回落冷启动；显式 coldOutput
   仍受 1024 下限收口；结果整 token 向上取整）。
6. shrink 只缩不扩（预算再充足不超原值；无效反馈保持原值；单单元放不下→0）。
7. check_input_budget：I+L+reserve≤C 边界恰好相等通过；超 1 不可容纳+可读原因（含数值）；
   profile 未启用/None 不可核验。
8. snapshot_bounds：超 20 采样裁剪保留 count/max；不改传入 buckets；非法桶/样本剔除；
   快照 JSON 序列化安全。
9. 零副作用：调用前后 os.environ 浅拷贝不变。

测试不依赖真实环境变量、不访问网络、不写任何文件、不连数据库。

运行：python3 tests/test_ontology_build_budget.py
"""
import json
import os
import sys
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from workbench.ontology_build import batch_contracts as contracts  # noqa: E402
from workbench.ontology_build import budget_profile as bp  # noqa: E402
from workbench.ontology_build import budget  # noqa: E402

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


def close(left, right, tolerance=1e-6):
    return left is not None and right is not None and abs(float(left) - float(right)) <= tolerance


# --- 公共构造 -----------------------------------------------------------------------

# 启用 profile：C=200000、requestOutput=40000、limit=50000 → cap=40000、reserve=4000，
# 可容纳输入上限 = 200000 − 40000 − 4000 = 156000。
PROFILE_ENV = {
    bp.ADAPTIVE_ENV: '1',
    bp.PROFILE_PROVIDER_ENV: 'prov-a',
    bp.PROFILE_MODEL_ENV: 'model-a',
    bp.CONTEXT_TOKENS_ENV: '200000',
    bp.OUTPUT_LIMIT_ENV: '50000',
    bp.REQUEST_OUTPUT_ENV: '40000',
    bp.TARGET_RATIO_ENV: '0.50',
}
ALLOWED_INPUT = 200000 - 40000 - 4000     # 156000
FILL_LENGTH = ALLOWED_INPUT - 32          # 单条纯 ASCII 消息：I = 长度 + 32

KEY_BASE = dict(provider_id='prov-a', model='model-a', prompt_version='v3',
                codec_version='legacy-v1', reasoning_digest='digest-x')


def make_key(**overrides):
    parts = dict(KEY_BASE)
    parts.update(overrides)
    return budget.bucket_key(**parts)


def make_target(target_id, fact_id, kind='single_fact', selector=None, **extra):
    target = {'targetId': target_id, 'factId': fact_id,
              'selector': selector or {'type': 'whole'}, 'kind': kind,
              'subjectKey': 'subject-' + fact_id, 'materialId': 'm1',
              'groupingConfidence': 'high'}
    target.update(extra)
    return target


# --- 场景 ----------------------------------------------------------------------------

def scenario_utf8_proxy_matches_contract():
    """场景1：estimate_request 与契约逐字一致（身份等同 + 数值核对）。"""
    check(budget.estimate_request is contracts.estimate_request,
          '场景1 estimate_request 与契约同源（函数身份）')
    check(budget.cold_output_estimate is contracts.cold_output_estimate,
          '场景1 cold_output_estimate 与契约同源（函数身份）')
    check(budget.ESTIMATE_KIND == 'utf8_proxy', '场景1 estimateKind 字面量为契约冻结值')
    cases = [
        ('中文单条', ['你好本体']),
        ('英文单条', ['hello ontology']),
        ('中英混合多消息', ['你好 ontology', {'role': 'user', 'content': '提取 obj-1'},
                            '{"k": "值"}']),
        ('dict 消息含嵌套', [{'role': 'system', 'content': '系统提示'},
                            {'role': 'user', 'content': ['列表', 1, 2.5, None]}]),
    ]
    for label, messages in cases:
        mine = budget.estimate_request(messages)
        theirs = contracts.estimate_request(messages)
        expect_bytes = sum(
            len(json.dumps(message, ensure_ascii=False, default=str).encode('utf-8'))
            if isinstance(message, dict) else len(str(message).encode('utf-8'))
            for message in messages)
        check(mine == theirs and mine['inputTokens'] == expect_bytes + 32 * len(messages)
              and mine['estimateKind'] == 'utf8_proxy',
              '场景1 %s：I=字节+32×条数 与契约逐字一致' % label, (mine, theirs))
    check(budget.estimate_request(None) == {'inputTokens': 0, 'estimateKind': 'utf8_proxy'},
          '场景1 None 输入 → I=0', budget.estimate_request(None))
    check(budget.estimate_request([])['inputTokens'] == 0, '场景1 空列表 → I=0')
    check(budget.estimate_request(['中'])['inputTokens'] == 3 + 32,
          '场景1 单个中文字符按 UTF-8 3 字节计', budget.estimate_request(['中']))


def scenario_unit_slots():
    """场景2：slots 口径——孤立 1、窗口 4、data 无结构 4、同身份去重。"""
    plain = [make_target('t-1', 'f1'), make_target('t-2', 'f2'), make_target('t-3', 'f3')]
    check(budget.unit_slots(plain) == 3, '场景2 三个孤立事实 → slots=3', budget.unit_slots(plain))
    check(budget.target_slots(make_target('t-w', 'fw', kind='adjacent_window')) == 4,
          '场景2 adjacent_window 单目标 → 4')
    check(budget.unit_slots(plain[:2] + [make_target('t-w', 'fw', kind='adjacent_window')]) == 6,
          '场景2 混合（2 孤立 + 1 窗口）→ 6')
    check(budget.unit_slots([make_target('t-1', 'f1'), make_target('t-1', 'f1'),
                             make_target('t-2', 'f2')]) == 2,
          '场景2 同 targetId 重复只计一次', budget.unit_slots(
              [make_target('t-1', 'f1'), make_target('t-1', 'f1'), make_target('t-2', 'f2')]))
    digest_dup = [{'factId': 'f9', 'selector': {'type': 'whole'}, 'kind': 'single_fact'},
                  {'factId': 'f9', 'selector': {'type': 'whole'}, 'kind': 'single_fact'},
                  make_target('t-2', 'f2')]
    check(budget.unit_slots(digest_dup) == 2,
          '场景2 缺 targetId 时按 factId+selector 派生身份去重', budget.unit_slots(digest_dup))
    check(budget.unit_slots([]) == 0 and budget.unit_slots(None) == 0, '场景2 空/None → 0')
    check(budget.unit_slots([make_target('t-1', 'f1'), '非dict项', make_target('t-2', 'f2')]) == 2,
          '场景2 非 dict 项跳过不崩')
    # data 无结构：JSON 标量/自由文本 → 4；结构化 dict/list → 1
    check(budget.target_slots(make_target('t-d1', 'fd1', data='自由文本片段')) == 4,
          '场景2 data 自由文本（无结构）→ 4')
    check(budget.target_slots(make_target('t-d2', 'fd2', data=42)) == 4,
          '场景2 data JSON 标量 → 4')
    check(budget.target_slots(make_target('t-d3', 'fd3', data={'a': 1})) == 1,
          '场景2 data 结构化 dict → 1')
    check(budget.target_slots(make_target('t-d4', 'fd4', data=[1, 2])) == 1,
          '场景2 data 结构化 list → 1')
    check(budget.target_slots(make_target('t-d5', 'fd5')) == 1, '场景2 无 data 字段 → 1')
    check(budget.target_slots({'kind': 'unknown_kind'}) == 1, '场景2 未知 kind 按普通事实 1')


def scenario_cold_start_boundaries():
    """场景3：冷启动公式边界——slots 收 1、V 下限 1024、逐点核对。"""
    check(budget.cold_output_estimate(0, 0) == 1024, '场景3 slots=0 按 1 且触发下限 1024',
          budget.cold_output_estimate(0, 0))
    check(budget.cold_output_estimate(None, None) == 1024, '场景3 slots=None 按 1 → 1024')
    check(budget.cold_output_estimate(1, 0) == 1024, '场景3 slots=1、I=0 → 下限 1024')
    check(budget.cold_output_estimate(1, 3) == 1024, '场景3 256+ceil(0.75)=257 仍被下限抬到 1024',
          budget.cold_output_estimate(1, 3))
    check(budget.cold_output_estimate(10, 1000) == 256 * 10 + 250,
          '场景3 slots=10、I=1000 → 2560+ceil(250)=2810', budget.cold_output_estimate(10, 1000))
    check(budget.cold_output_estimate(10, 101) == 256 * 10 + 26,
          '场景3 ceil(0.25×101)=26', budget.cold_output_estimate(10, 101))
    check(budget.cold_output_estimate(50, 10000) == 12800 + 2500,
          '场景3 slots=50、I=10000 → 15300', budget.cold_output_estimate(50, 10000))


def scenario_calibration_buckets():
    """场景4：校准桶——19→max×1.25、20→P90×1.25；截断/unknown/非法拒绝；维度隔离。"""
    key = make_key(unit_kind='single_fact')
    buckets = {}
    results = [budget.update_bucket(buckets, key, 1000, 10) for _ in range(19)]
    check(all(results) and buckets[key]['count'] == 19,
          '场景4 19 个成功样本全部记入（completion/slots 比率）', buckets.get(key))
    estimate19 = budget.calibrated_estimate(buckets, key, 10)
    check(close(estimate19, 100 * 1.25 * 10),
          '场景4 count=19 < 20 → max(100)×1.25×slots=1250', estimate19)
    check(budget.update_bucket(buckets, key, 3000, 10) is True, '场景4 第 20 个样本记入')
    check(budget.update_bucket(buckets, key, 3000, 10) is True, '场景4 第 21 个样本记入')
    # 窗口保留最近 20：18×100 + 2×300；P90 线性插值 position=0.9×19=17.1 →
    # idx17=100、idx18=300 → 100+0.1×200=120 → ×1.25×slots=1500
    estimate20 = budget.calibrated_estimate(buckets, key, 10)
    check(close(estimate20, 120 * 1.25 * 10)
          and not close(estimate20, 300 * 1.25 * 10)
          and not close(estimate20, 100 * 1.25 * 10),
          '场景4 count≥20 切换 P90×1.25（插值 120×1.25×10=1500，非 max 的 3750 也非纯低值 1250）',
          estimate20)
    # 截断样本被拒（双保险：执行器必须不喂或传 truncated=True）
    before = dict(buckets[key])
    check(budget.update_bucket(buckets, key, 9999, 10, truncated=True) is False,
          '场景4 截断样本被拒（truncated=True → False）')
    check(buckets[key] == before, '场景4 截断拒绝后桶不变', buckets[key])
    # unknown usage 不记 0：None/非数值/负数/slots 非法
    for label, args in (('completion=None', (None, 10)), ('completion=非数值', ('x', 10)),
                        ('completion=负数', (-5, 10)), ('slots=0', (1000, 0)),
                        ('slots=None', (1000, None)), ('key 为空', (1000, 10))):
        if label == 'key 为空':
            check(budget.update_bucket(buckets, '', 1000, 10) is False,
                  '场景4 %s 被拒' % label)
        else:
            check(budget.update_bucket(buckets, key, args[0], args[1]) is False,
                  '场景4 %s 被拒（unknown usage 不记 0）' % label, buckets[key])
    check(buckets[key] == before, '场景4 全部拒绝后桶仍不变', buckets[key])
    check(budget.calibrated_estimate({}, key, 10) is None
          and budget.calibrated_estimate(buckets, '不存在', 10) is None,
          '场景4 无桶/无键 → None（调用方回落冷启动）')
    # 滑窗有界：22 个样本保留最近 20，count 累计
    rolling = {}
    for rate in range(1, 23):
        budget.update_bucket(rolling, key, rate * 10, 10)
    check(len(rolling[key]['samples']) == 20 and rolling[key]['count'] == 22
          and close(rolling[key]['max'], 22.0),
          '场景4 桶滑窗保留最近 20 个采样，count=22/max=22 累计', rolling[key])
    check(rolling[key]['samples'][0] == 3.0 and rolling[key]['samples'][-1] == 22.0,
          '场景4 裁掉最旧 2 个采样（保留 3..22）', rolling[key]['samples'][:2])
    # 混合 kind / provider 维度不污染（六维 key 逐维不同桶）
    kind_a = make_key(unit_kind='single_fact')
    kind_b = make_key(unit_kind='doc_section')
    provider_b = make_key(provider_id='prov-b', unit_kind='single_fact')
    split = {}
    budget.update_bucket(split, kind_a, 1000, 10)
    check(kind_b not in split and provider_b not in split,
          '场景4 不同 unit_kind/provider 落不同桶（不污染）', sorted(split))
    check(budget.calibrated_estimate(split, kind_b, 10) is None,
          '场景4 未喂样本的桶无估算')
    # 快照口径件下的 tuple 形式集成：expected_output((buckets, key)) 见场景5
    check(make_key(unit_kind='single_fact') == key, '场景4 bucket_key 六维拼接确定性')


def scenario_expected_output_floor():
    """场景5：E = max(冷启动, 校准)，永不低于冷启动。"""
    unit = {'slots': 50, 'inputTokens': 10000}          # V = 12800 + 2500 = 15300
    cold = contracts.cold_output_estimate(50, 10000)
    check(cold == 15300, '场景5 前置：冷启动 V=15300', cold)
    check(budget.expected_output(unit, None) == 15300, '场景5 校准缺失 → 冷启动')
    check(budget.expected_output(unit, 10.0) == 15300, '场景5 校准 10 低于冷启动 → 仍冷启动')
    check(budget.expected_output(unit, {}) == 15300, '场景5 非法校准 → 冷启动')
    check(budget.expected_output(unit, ({}, '无此桶')) == 15300,
          '场景5 空桶 tuple → 回落冷启动')
    check(budget.expected_output(unit, 20000.4) == 20001,
          '场景5 校准 20000.4 高于冷启动 → 取校准并整 token 向上取整',
          budget.expected_output(unit, 20000.4))
    check(budget.expected_output(unit, 15300.0) == 15300, '场景5 校准恰等于冷启动 → 相等')
    buckets = {}
    key = make_key(unit_kind='single_fact')
    budget.update_bucket(buckets, key, 1000, 10)   # 比率 100 → 估算 100×1.25×50=6250
    check(budget.expected_output(unit, (buckets, key)) == 15300,
          '场景5 tuple 校准 6250 低于冷启动 → 冷启动（经 calibrated_estimate）',
          budget.expected_output(unit, (buckets, key)))
    check(budget.expected_output({'slots': 1, 'inputTokens': 0}, None) == 1024,
          '场景5 冷启动下限 1024 传导到 E')
    check(budget.expected_output({'coldOutput': 500}, None) == 1024,
          '场景5 显式 coldOutput=500 仍受契约下限 1024 收口')
    check(budget.expected_output(None, None) == 1024, '场景5 unit 非法 → 按冷启动兜底')
    # 校准高于冷启动且带小数：向上取整，绝不低于校准
    high = budget.expected_output({'slots': 1, 'inputTokens': 0}, 1025.3)
    check(high == 1026 and high >= 1025.3, '场景5 结果向上取整且不低于校准', high)


def scenario_shrink_only_shrinks():
    """场景6：反馈只缩不扩——建议批量 ≤ 原值，绝不自动扩批。"""
    check(budget.shrink_for_feedback(4, 3000, 10000) == 3,
          '场景6 E=3000、预算 10000、原 4 → 缩为 3')
    check(budget.shrink_for_feedback(4, 100, 10 ** 9) == 4,
          '场景6 预算再充足也 ≤ 原值 4（绝不扩到更多）',
          budget.shrink_for_feedback(4, 100, 10 ** 9))
    check(budget.shrink_for_feedback(4, 5000, 1000) == 0, '场景6 单单元都放不下 → 0（走细分）')
    check(budget.shrink_for_feedback(4, None, 1000) == 4, '场景6 无有效估算反馈 → 保持原值')
    check(budget.shrink_for_feedback(4, 3000, None) == 4, '场景6 无预算 → 保持原值')
    check(budget.shrink_for_feedback(4, -1, 1000) == 4, '场景6 非法估算 → 保持原值')
    check(budget.shrink_for_feedback(1, 1, 1) == 1, '场景6 恰好容纳 → 1')
    check(budget.shrink_for_feedback(6, 250.5, 2000) == 6,
          '场景6 floor(2000/250.5)=7 > 原值 → 收口为 6',
          budget.shrink_for_feedback(6, 250.5, 2000))
    check(budget.shrink_for_feedback(None, 100, 1000) == 0
          and budget.shrink_for_feedback(0, 100, 1000) == 0,
          '场景6 非法/零原值 → 0')
    # 与校准联动：校准显示更低成本也不放大
    buckets = {}
    key = make_key(unit_kind='single_fact')
    for _ in range(20):
        budget.update_bucket(buckets, key, 100, 10)      # 比率 10 → 估算 10×1.25×slots
    cheap = budget.calibrated_estimate(buckets, key, 5)
    check(close(cheap, 10 * 1.25 * 5), '场景6 前置：校准估算 62.5', cheap)
    check(budget.shrink_for_feedback(2, cheap, 10 ** 9) == 2,
          '场景6 校准显示低成本也不扩批（保持 2）',
          budget.shrink_for_feedback(2, cheap, 10 ** 9))


def scenario_check_input_budget():
    """场景7：I + L + inputReserve ≤ C；边界相等通过；超限给可读原因。"""
    profile = bp.build_profile(dict(PROFILE_ENV))
    effective = profile['effective']
    check(effective['outputCap'] == 40000 and effective['inputReserve'] == 4000,
          '场景7 前置：cap=40000、reserve=4000', effective)
    exact = ['a' * FILL_LENGTH]                       # I = 155968 + 32 = 156000
    ok, tokens, reason = budget.check_input_budget(exact, profile)
    check(ok is True and tokens == ALLOWED_INPUT and reason == '',
          '场景7 恰好等于边界（I=156000）通过', (ok, tokens, reason))
    multi = ['a' * (FILL_LENGTH // 2 - 16), 'b' * (FILL_LENGTH // 2 - 16)]  # 字节和相同，条数 2
    ok2, tokens2, reason2 = budget.check_input_budget(multi, profile)
    check(ok2 is True and tokens2 == ALLOWED_INPUT,
          '场景7 多消息边界（字节+32×2 恰好=156000）通过', (ok2, tokens2, reason2))
    over = ['a' * (FILL_LENGTH + 1)]                  # I = 156001
    ok3, tokens3, reason3 = budget.check_input_budget(over, profile)
    check(ok3 is False and tokens3 == ALLOWED_INPUT + 1
          and '156001' in reason3 and '40000' in reason3 and '4000' in reason3
          and '200000' in reason3 and '细分' in reason3,
          '场景7 超 1 不可容纳，原因含三值与超出量（中文可读）', (ok3, tokens3, reason3))
    ok4, _, reason4 = budget.check_input_budget(exact, bp.build_profile({}))
    check(ok4 is False and '未启用' in reason4, '场景7 未启用 profile → 不可核验', reason4)
    ok5, _, reason5 = budget.check_input_budget(exact, None)
    check(ok5 is False and reason5, '场景7 profile=None → 不可核验（不可派发）', reason5)
    check(budget.check_input_budget([], profile) == (True, 0, ''),
          '场景7 空消息 I=0 可容纳', budget.check_input_budget([], profile))


def scenario_snapshot_bounds():
    """场景8：snapshot_bounds——超 20 裁剪保留 count/max；不改传入；非法剔除。"""
    raw_samples = [float(value) for value in range(1, 26)]        # 25 个采样
    source = {'k1': {'samples': list(raw_samples), 'count': 25, 'max': 25.0}}
    frozen = json.dumps(source, sort_keys=True)
    snapshot = budget.snapshot_bounds(source)
    check(json.dumps(source, sort_keys=True) == frozen, '场景8 传入 buckets 不被修改')
    bounded = snapshot['k1']
    check(len(bounded['samples']) == contracts.CALIBRATION_MAX_SAMPLES == 20,
          '场景8 快照 samples 有界化到 20', len(bounded['samples']))
    check(bounded['samples'][0] == 6.0 and bounded['samples'][-1] == 25.0,
          '场景8 裁最旧保留最近 20（6..25）', bounded['samples'][:2] + bounded['samples'][-2:])
    check(bounded['count'] == 25 and bounded['max'] == 25.0,
          '场景8 count/max 原样保留（历史累计不受窗口裁剪影响）', bounded)
    check(budget.snapshot_bounds({}) == {}, '场景8 空 buckets → 空快照')
    mixed = {'ok': {'samples': [1.0, 2.0], 'count': 2, 'max': 2.0},
             'bad': '不是dict',
             'dirty': {'samples': [1.0, 'x', None, True, float('nan'), 3.0],
                       'count': None, 'max': None}}
    cleaned = budget.snapshot_bounds(mixed)
    check('ok' in cleaned and 'bad' not in cleaned, '场景8 非法桶剔除', sorted(cleaned))
    check(cleaned['dirty']['samples'] == [1.0, 3.0] and cleaned['dirty']['count'] == 2
          and cleaned['dirty']['max'] == 3.0,
          '场景8 非法样本（字符串/None/bool/NaN）剔除；缺 count/max 回落样本统计',
          cleaned.get('dirty'))
    check(json.dumps(snapshot) and json.dumps(cleaned), '场景8 快照 JSON 序列化安全')
    # 与 update_bucket 滑窗叠加：桶本身已有界，快照保持一致
    buckets = {}
    key = make_key(unit_kind='single_fact')
    for rate in range(1, 23):
        budget.update_bucket(buckets, key, rate * 10, 10)
    from_bucket = budget.snapshot_bounds(buckets)[key]
    check(len(from_bucket['samples']) == 20 and from_bucket['count'] == 22,
          '场景8 update_bucket 滑窗桶经快照保持 20/count=22', from_bucket)


def scenario_no_side_effects():
    """场景9：零副作用——调用前后 os.environ 浅拷贝不变。"""
    before = dict(os.environ)
    profile = bp.build_profile(dict(PROFILE_ENV))
    budget.estimate_request(['消息'])
    budget.cold_output_estimate(5, 100)
    buckets = {}
    budget.update_bucket(buckets, make_key(unit_kind='single_fact'), 1000, 10)
    budget.calibrated_estimate(buckets, make_key(unit_kind='single_fact'), 10)
    budget.expected_output({'slots': 5, 'inputTokens': 100}, (buckets, make_key(unit_kind='single_fact')))
    budget.shrink_for_feedback(4, 3000, 10000)
    budget.check_input_budget(['消息'], profile)
    budget.snapshot_bounds(buckets)
    budget.unit_slots([make_target('t-1', 'f1')])
    after = dict(os.environ)
    check(before == after, '场景9 os.environ 调用前后不变（浅拷贝对比）',
          {key: (before.get(key), after.get(key)) for key in set(before) | set(after)
           if before.get(key) != after.get(key)})
    check(sorted(before.keys()) == sorted(after.keys()), '场景9 os.environ 键集合不变')
    # 除 update_bucket 按设计累积外，其余函数不修改传入对象
    unit = {'slots': 5, 'inputTokens': 100}
    unit_before = dict(unit)
    budget.expected_output(unit, None)
    check(unit == unit_before, '场景9 expected_output 不修改 unit')
    frozen_buckets = {'k': {'samples': [1.0], 'count': 1, 'max': 1.0}}
    frozen_before = json.dumps(frozen_buckets, sort_keys=True)
    budget.calibrated_estimate(frozen_buckets, 'k', 5)
    budget.snapshot_bounds(frozen_buckets)
    check(json.dumps(frozen_buckets, sort_keys=True) == frozen_before,
          '场景9 calibrated_estimate/snapshot_bounds 不修改 buckets')


SCENARIOS = (
    ('utf8_proxy_matches_contract', scenario_utf8_proxy_matches_contract),
    ('unit_slots', scenario_unit_slots),
    ('cold_start_boundaries', scenario_cold_start_boundaries),
    ('calibration_buckets', scenario_calibration_buckets),
    ('expected_output_floor', scenario_expected_output_floor),
    ('shrink_only_shrinks', scenario_shrink_only_shrinks),
    ('check_input_budget', scenario_check_input_budget),
    ('snapshot_bounds', scenario_snapshot_bounds),
    ('no_side_effects', scenario_no_side_effects),
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
