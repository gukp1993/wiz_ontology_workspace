"""本体生成控输出 v3 —— D02：完整请求估算与反馈收缩（纯函数模块）。

需求：《本体生成控输出_整合方案与并行开发计划_v3.md》§4.2/§4.3、§8 D02 行
（任务ID auto-build-output-v3）；契约：batch_contracts.py「估算」节（87d1473 冻结，
含后续注释级修订）。接口契约：接口文档 08 §14。

职责与冻结口径：
* estimate_request / cold_output_estimate 的唯一实现在 batch_contracts.py；本模块
  只再导出（函数身份等同，tests/test_ontology_build_budget.py 锁定），行为逐字一致：
  - I = 完整序列化 messages 的 UTF-8 字节总数 + 32×消息条数（estimateKind='utf8_proxy'）；
  - V = max(1024, 256×slots + ceil(0.25×I))。
* 本模块新增（D02 冻结签名）：
  - unit_slots(targets) / target_slots(target)：每目标 slots——普通孤立事实 1，
    信息结构未知（kind='adjacent_window' 或 data 无结构）按 4；同目标身份去重；
  - bucket_key / update_bucket / calibrated_estimate：校准桶。成功且有 usage 才记
    （total completion / slots 比率）；样本 <20 用 max×1.25，≥20 用 P90×1.25
    （线性插值）；截断样本不进桶；unknown usage 不记 0；
  - expected_output(unit, calibration)：E = max(冷启动 V, 校准估算)，永不低于冷启动；
  - shrink_for_feedback：反馈只缩不扩（建议批量 ≤ 原值，绝不自动扩批）；
  - check_input_budget(messages, profile)：I + L + inputReserve ≤ C 核验，
    不满足给出不可容纳原因（供细分决策）；
  - snapshot_bounds(buckets)：按 CALIBRATION_MAX_SAMPLES=20 有界化的校准快照
    （进 checkpoint.calibrationSnapshot 用），保留 count/max。

纪律（与 batch_contracts 一致）：
* 只依赖标准库与 batch_contracts；禁止 import 存储层/管线/路由/semantic_units。
* 纯函数零副作用：不改环境变量、不写任何文件；除 update_bucket 按设计累积写入
  传入的 buckets 外，其余函数不修改任何传入对象。
"""
import math

from workbench.ontology_build import batch_contracts as _contracts

# --- 再导出：契约冻结实现（单一事实来源在 batch_contracts，禁止复制逻辑） --------------

estimate_request = _contracts.estimate_request
cold_output_estimate = _contracts.cold_output_estimate

ESTIMATE_KIND = 'utf8_proxy'      # 契约冻结字面量（estimate_request 返回值/接口08 §14.3 estimateKind）
COLD_OUTPUT_FLOOR = 1024          # 契约 cold_output_estimate 公式的固定下限

# --- slots 口径（契约 §4.2：普通孤立事实 ≥1；信息结构未知按 4） ------------------------

SINGLE_SLOTS = 1                          # 普通孤立事实（结构可确定）
WINDOW_SLOTS = 4                          # 信息结构未知的数组摘要/文本目标
UNSTRUCTURED_KIND = 'adjacent_window'     # 契约 §4.1 kind 枚举值（与 semantic_units.KIND_WINDOW 同值）


# --- 数值守卫（拒绝 bool/NaN/inf；绝不把 unknown 记 0） --------------------------------


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _positive_int(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, float):
        number = _number(value)
        if number is not None and number > 0 and number.is_integer():
            return int(number)
    return None


# --- 目标 slots（D04 装箱与校准 per-slot 归一共用） ------------------------------------


def target_slots(target):
    """单个 Target → slots：adjacent_window / data 无结构 → 4；其余普通孤立事实 → 1。

    Target 为契约 §4.1 冻结形状（{'targetId','factId','selector','kind','subjectKey',
    'materialId','groupingConfidence'}，容忍携带 fact 的 'data' 附加字段）。
    非 dict / kind 缺失按普通事实计 1（不因缺展示字段丢目标）。
    """
    if not isinstance(target, dict):
        return SINGLE_SLOTS
    if str(target.get('kind') or '') == UNSTRUCTURED_KIND:
        return WINDOW_SLOTS
    data = target.get('data')
    if data is not None and not isinstance(data, (dict, list)):
        return WINDOW_SLOTS    # JSON 标量/自由文本等无结构 data：信息结构未知
    return SINGLE_SLOTS


def unit_slots(targets):
    """目标列表 → 本单元总 slots；同一目标身份只计一次（绝不因列表重复放大）。

    身份口径：优先 targetId（'t-' 稳定派生 id）；缺失时回退契约 target_digest
    （factId+canonical_selector，与覆盖比对同口径）。纯函数。
    """
    total = 0
    seen = set()
    for target in targets or []:
        if not isinstance(target, dict):
            continue
        identity = str(target.get('targetId') or '') or _contracts.target_digest(target)
        if identity in seen:
            continue
        seen.add(identity)
        total += target_slots(target)
    return total


# --- 校准桶（契约 §4.2/§4.3：比率入桶；<20 max×1.25，≥20 P90×1.25） -------------------


def bucket_key(provider_id, model, prompt_version, codec_version, reasoning_digest, unit_kind):
    """校准桶 key（冻结六维）：provider 配置标识+model+promptVersion+outputCodecVersion
    +推理配置摘要+unitKind。返回 '|'.join 规范字符串（checkpoint 快照 JSON 安全）。"""
    return '|'.join(str(part or '') for part in (provider_id, model, prompt_version,
                                                 codec_version, reasoning_digest, unit_kind))


def update_bucket(buckets, key, completion_tokens, slots, truncated=False):
    """记入一个成功样本（total completion / slots 比率）；成功且有 usage 才记。

    拒绝（返回 False，桶不变）：
    * truncated=True —— 截断样本只说明完整成本更高，绝不作为成功样本进桶
      （执行器对 finish_reason=length 必须传 True 或不调用，本函数双保险）；
    * completion_tokens 为 None/非法/负 —— unknown usage 不记 0（契约 §4.3）；
    * slots 非法（<1 或非整数）；
    * key 为空。
    桶形状（JSON 安全）：{'samples': [比率…], 'count': 历史累计接受数, 'max': 最大比率}；
    samples 保留最近 CALIBRATION_MAX_SAMPLES=20 个（滑窗，超出裁最旧）。
    按设计累积写入传入的 buckets（其余 budget 函数不修改传入对象）。返回是否已记入。
    """
    buckets = buckets if isinstance(buckets, dict) else None
    if buckets is None or not str(key or ''):
        return False
    if truncated:
        return False
    completion = _number(completion_tokens)
    slot_count = _positive_int(slots)
    if completion is None or completion < 0 or slot_count is None:
        return False
    value = completion / slot_count
    bucket = buckets.get(key)
    bucket = bucket if isinstance(bucket, dict) else {'samples': [], 'count': 0, 'max': 0.0}
    samples = [float(item) for item in (bucket.get('samples') or [])
               if _number(item) is not None]
    samples.append(value)
    overflow = len(samples) - _contracts.CALIBRATION_MAX_SAMPLES
    if overflow > 0:
        samples = samples[overflow:]
    bucket['samples'] = samples
    bucket['count'] = int(_positive_int(bucket.get('count')) or 0) + 1
    bucket['max'] = max(float(bucket.get('max') or 0.0), value)
    buckets[key] = bucket
    return True


def _percentile(sorted_values, quantile):
    """线性插值分位（sorted_values 升序、非空）；P90 用法见 calibrated_estimate。"""
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = quantile * (len(sorted_values) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return sorted_values[lower]
    weight = position - lower
    return sorted_values[lower] * (1.0 - weight) + sorted_values[upper] * weight


def calibrated_estimate(buckets, key, slots):
    """桶 → 该单元（slots 个目标）的校准输出估算；无可用校准返回 None（回落冷启动）。

    桶内是每目标比率：count < CALIBRATION_SAMPLE_MIN(20) 用 max(观测)×1.25；
    count ≥ 20 用 P90（线性插值）×1.25；再按本单元 slots 放大。
    纯函数，不修改 buckets。返回 float（token），None=无样本。
    """
    bucket = (buckets or {}).get(key) if isinstance(buckets, dict) else None
    if not isinstance(bucket, dict):
        return None
    slot_count = _positive_int(slots)
    if slot_count is None:
        return None
    samples = sorted(float(item) for item in (bucket.get('samples') or [])
                     if _number(item) is not None)
    count = int(_positive_int(bucket.get('count')) or 0) or len(samples)
    if not samples or count <= 0:
        return None
    if count < _contracts.CALIBRATION_SAMPLE_MIN:
        rate = max(samples) * _contracts.CALIBRATION_SAFETY
    else:
        rate = _percentile(samples, 0.90) * _contracts.CALIBRATION_SAFETY
    return rate * slot_count


def expected_output(unit, calibration=None):
    """单元期望输出 E = max(冷启动 V, 校准估算)；校准缺失/非法回落冷启动，永不低于冷启动。

    unit：{'slots': int, 'inputTokens': int}（本单元目标数与请求输入估算）；兼容显式
    {'coldOutput': int} 覆盖（仍按契约下限 1024 收口）。
    calibration：None（无校准）| 数值（已算好的校准估算）| (buckets, key) 元组
    （内部按 unit.slots 调 calibrated_estimate）。
    返回 int（token，向上取整——估算宁高估不低估）。纯函数。
    """
    unit = unit if isinstance(unit, dict) else {}
    cold = _number(unit.get('coldOutput'))
    if cold is None:
        cold = _contracts.cold_output_estimate(unit.get('slots'), unit.get('inputTokens'))
    else:
        cold = max(COLD_OUTPUT_FLOOR, int(math.ceil(cold)))
    calibrated = None
    if isinstance(calibration, tuple) and len(calibration) == 2:
        calibrated = calibrated_estimate(calibration[0], calibration[1],
                                         unit.get('slots'))
    else:
        calibrated = _number(calibration)
    estimate = cold if calibrated is None else max(cold, calibrated)
    return int(math.ceil(estimate))


# --- 反馈收缩（契约 §4.3：只允许缩小后续批次，绝不自动扩批） ---------------------------


def shrink_for_feedback(current_units, estimated_output, budget_tokens):
    """按校准后估算重算建议批量；结果 ≤ current_units（有界反馈只缩不扩）。

    current_units：上一批实际单元数；estimated_output：单单元估算输出 E（token）；
    budget_tokens：本批可用输出预算（如 targetBudget T）。
    建议批量 = min(current_units, floor(budget / E))；预算再充足也绝不返回
    > current_units。E/预算非法或 ≤0（无有效反馈）→ 保持 current_units 不变。
    单单元都放不下 → 0（调用方走细分/blocked，不是静默丢弃）。纯函数。
    """
    current = _positive_int(current_units)
    if current is None:
        return 0
    estimate = _number(estimated_output)
    budget = _number(budget_tokens)
    if estimate is None or budget is None or estimate <= 0 or budget <= 0:
        return current
    affordable = int(math.floor(budget / estimate))
    return max(0, min(current, affordable))


# --- 输入预算核验（契约 §3：I + L + inputReserve ≤ C；不满足先细分） --------------------


def check_input_budget(messages, profile):
    """核验完整请求输入是否可容纳：I + outputCap + inputReserve ≤ contextTokens。

    返回 (ok, input_tokens, reason)：ok=True 时 reason=''；不满足给出中文可读原因
    （含三值与超出量，供细分决策与日志；不含密钥）。profile 未启用或缺
    C/L/inputReserve 冻结值 → ok=False（无法核验即不可派发，由调用方走预检 422）。
    I 用契约 estimate_request（utf8_proxy）。纯函数，零副作用。
    """
    input_tokens = int(estimate_request(messages)['inputTokens'])
    profile = profile if isinstance(profile, dict) else {}
    effective = profile.get('effective')
    effective = effective if isinstance(effective, dict) else {}
    context_tokens = _positive_int(profile.get('contextTokens'))
    output_cap = _positive_int(effective.get('outputCap'))
    reserve = _number(effective.get('inputReserve'))
    if context_tokens is None or output_cap is None or reserve is None:
        return False, input_tokens, '预算 profile 未启用或缺 C/L/inputReserve，无法核验输入预算'
    reserve_int = int(math.ceil(reserve))
    total = input_tokens + output_cap + reserve_int
    if total <= context_tokens:
        return True, input_tokens, ''
    reason = ('输入估算 %d + 输出上限 %d + 输入预留 %d = %d 超过上下文限制 %d（超出 %d），'
              '需细分主目标后再派发' % (input_tokens, output_cap, reserve_int, total,
                                       context_tokens, total - context_tokens))
    return False, input_tokens, reason


# --- 校准快照有界化（进 checkpoint.calibrationSnapshot；容量闭环前置） -----------------


def snapshot_bounds(buckets):
    """buckets → 有界快照（新 dict，不修改传入）：每桶 samples 最多保留最近
    CALIBRATION_MAX_SAMPLES=20 个（超出裁最旧），count（历史累计接受数）与 max
    原样保留；非法桶/非法样本剔除；键转 str。纯函数，JSON 序列化安全。"""
    snapshot = {}
    for key, bucket in (buckets or {}).items():
        if not isinstance(bucket, dict):
            continue
        samples = [float(item) for item in (bucket.get('samples') or [])
                   if _number(item) is not None]
        if len(samples) > _contracts.CALIBRATION_MAX_SAMPLES:
            samples = samples[-_contracts.CALIBRATION_MAX_SAMPLES:]
        count = int(_positive_int(bucket.get('count')) or 0) or len(samples)
        max_value = max(samples) if samples else 0.0
        raw_max = _number(bucket.get('max'))
        if raw_max is not None:
            max_value = max(max_value, raw_max)
        snapshot[str(key)] = {'samples': samples, 'count': count, 'max': max_value}
    return snapshot
