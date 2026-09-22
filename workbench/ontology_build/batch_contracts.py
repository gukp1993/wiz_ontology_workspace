"""本体生成控输出 v3：冻结契约（唯一口径，D00 交付）。

需求：《本体生成控输出_整合方案与并行开发计划_v3.md》（任务ID auto-build-output-v3）。
本模块是 20 项任务（D00–D19）共享的**唯一**结构/错误码/函数签名契约来源；
各任务实现必须 import 这里的事件码、状态枚举与校验函数，禁止各自定义同名字段。

纪律：
* 本模块只依赖标准库，不 import 存储层/管线（可被解析器、前端契约测试、实验代码单独加载）。
* 所有结构用 plain dict（与存储视图/HTTP 响应同形），字段名即冻结协议；
  新增字段必须先改本文件 + 接口文档 08，再改实现。
* 「契约」与「实现」分离：函数签名的参数/返回/异常/事务责任写在各节 docstring，
  实现方不得收窄语义（例如把 must-raise 改成返回 None）。

三个独立版本维度（不得混淆）：
* 文档版本 v3 —— 本需求文档；
* checkpoint.generate.schemaVersion —— 1=旧批次计划，2=目标/作业/尝试计划（本契约）；
* outputCodecVersion —— 'legacy-v1'（现行抽取协议）| 'compact-v1'（精简候选协议）。
"""
import hashlib
import json
import math
import os

# --- 版本与编解码标识 --------------------------------------------------------------

PLANNER_VERSION = 'v3'
CHECKPOINT_SCHEMA_VERSION = 2          # 本契约描述的 checkpoint.generate 内部结构版本
CHECKPOINT_SCHEMA_LEGACY = 1           # 旧批次计划（协议上仍存在，旧任务按自身版本运行）
CODEC_LEGACY = 'legacy-v1'
CODEC_COMPACT = 'compact-v1'
CODEC_VERSIONS = (CODEC_LEGACY, CODEC_COMPACT)
PLAN_PROMPT_VERSION = 'v3'             # v2 计划抽取提示词版本（legacy 路径沿用 protocol.PROMPT_VERSION）

# --- 输出编解码错误码（job 内部语义，可进 checkpoint.blocking/errorCode） ------------

INPUT_BUDGET_EXCEEDED = 'INPUT_BUDGET_EXCEEDED'            # provider 明确上下文超限 → 细分主目标
OUTPUT_TRUNCATED = 'OUTPUT_TRUNCATED'                      # finish_reason=length / 输出超限 → split
RESPONSE_TOO_LARGE = 'RESPONSE_TOO_LARGE'                  # 响应体/正文超限（不解析半 JSON）
FORMAT_INVALID = 'FORMAT_INVALID'                          # 非 JSON / schema 不符（每 job 最多 1 次修复）
COVERAGE_INCOMPLETE = 'COVERAGE_INCOMPLETE'                # compact coverage 缺目标 → 整叶不提交
DANGLING_REFERENCE = 'DANGLING_REFERENCE'                  # 悬空别名/引用（compact 校验错误）
OVERSIZED_ATOMIC_TARGET = 'OVERSIZED_ATOMIC_TARGET'        # 不可分目标超限 → blocked
CHECKPOINT_BUDGET_EXCEEDED = 'CHECKPOINT_BUDGET_EXCEEDED'  # checkpoint 容量超限
TARGET_BUDGET_EXCEEDED = 'TARGET_BUDGET_EXCEEDED'          # 目标数 > maxPlanTargets
JOB_BUDGET_EXCEEDED = 'JOB_BUDGET_EXCEEDED'                # 作业数 > maxPlanJobs
ATTEMPT_BUDGET_EXCEEDED = 'ATTEMPT_BUDGET_EXCEEDED'        # 尝试数 > maxPlanAttempts
WALL_TIME_BUDGET_EXCEEDED = 'WALL_TIME_BUDGET_EXCEEDED'    # 活跃耗时 > maxPlanWallSeconds
FINAL_CANDIDATES_EXCEEDED = 'FINAL_CANDIDATES_EXCEEDED'    # 最终候选 > 产品 500 上限 → 显式失败
SPLIT_DEPTH_EXCEEDED = 'SPLIT_DEPTH_EXCEEDED'              # 拆分深度 > maxSplitDepth
JOB_ATTEMPTS_EXHAUSTED = 'JOB_ATTEMPTS_EXHAUSTED'          # 单 job 3 次总额用尽
NETWORK_RETRYABLE = 'NETWORK_RETRYABLE'                    # 超时/连接失败/429/可重试 5xx
UNKNOWN_CHECKPOINT_SCHEMA = 'UNKNOWN_CHECKPOINT_SCHEMA'    # 未知版本拒绝恢复（不删候选兜底）

# --- HTTP 422 同步错误码（generate 相关入口；任何 task/run/candidate 状态变更前返回） --

BUDGET_PROFILE_REQUIRED = 'BUDGET_PROFILE_REQUIRED'   # 启用 v2 但 C/L 缺失（或缺 providerId/model）
BUDGET_PROFILE_MISMATCH = 'BUDGET_PROFILE_MISMATCH'   # 请求 provider/model 与 profile 绑定不符
BUDGET_CONFIG_INVALID = 'BUDGET_CONFIG_INVALID'       # 配置非法（ratio 越界、非正整数等）
BUDGET_PLAN_TOO_LARGE = 'BUDGET_PLAN_TOO_LARGE'       # 同步可判的规模超限；异步发现 → Run.failed+blocking
BUDGET_PLAN_MISMATCH = 'BUDGET_PLAN_MISMATCH'         # auto 恢复时计划指纹不匹配

HTTP_BUDGET_ERROR_CODES = (BUDGET_PROFILE_REQUIRED, BUDGET_PROFILE_MISMATCH, BUDGET_CONFIG_INVALID,
                           BUDGET_PLAN_TOO_LARGE, BUDGET_PLAN_MISMATCH)

# --- 配置默认值（工程试点边界，不是实测结论；capabilities 原样展示生效值） ------------

DEFAULT_REQUEST_OUTPUT_TOKENS = 32000   # WIZ_BUILD_REQUEST_OUTPUT_TOKENS
DEFAULT_TARGET_RATIO = 0.50             # WIZ_BUILD_OUTPUT_TARGET_RATIO；允许 0.25–0.80
TARGET_RATIO_MIN = 0.25
TARGET_RATIO_MAX = 0.80
MAX_PLAN_TARGETS = 4096
MAX_PLAN_JOBS = 512
MAX_PLAN_ATTEMPTS = 1024
MAX_CHECKPOINT_BYTES = 1024 * 1024      # UTF-8 序列化后 1MiB
CHECKPOINT_TERMINAL_RESERVE_BYTES = 16 * 1024   # 终态写入预留区
CHECKPOINT_ATTEMPT_RESERVE_BYTES = 8 * 1024     # 每个在途调用的完成摘要预留
MAX_SPLIT_DEPTH = 8
MAX_JOB_ATTEMPTS = 3                    # 首调+网络重试+格式修复共享总额（格式修复最多 1 次）
MAX_JOB_NETWORK_RETRIES = 2             # 网络重试上限（含在 3 次总额内）
MAX_PLAN_WALL_SECONDS = 3600            # 累计活跃执行耗时；重启后沿已保存耗时继续
MAX_PRIMARY_TARGETS_PER_JOB = 64
MAX_FINAL_CANDIDATES = 500              # 既有产品上限：超限显式受阻，绝不裁前 500 冒充成功
MAX_PLAN_ERROR_CHARS = 400              # checkpoint 内错误文本上限
MAX_PLAN_ID_CHARS = 200                 # checkpoint 内各标识上限

ADAPTIVE_ENV = 'WIZ_BUILD_ADAPTIVE_BATCHING'
PROFILE_PROVIDER_ENV = 'WIZ_BUILD_PROFILE_PROVIDER_ID'
PROFILE_MODEL_ENV = 'WIZ_BUILD_PROFILE_MODEL'
CONTEXT_TOKENS_ENV = 'WIZ_BUILD_CONTEXT_TOKENS'
OUTPUT_LIMIT_ENV = 'WIZ_BUILD_OUTPUT_LIMIT_TOKENS'
REQUEST_OUTPUT_ENV = 'WIZ_BUILD_REQUEST_OUTPUT_TOKENS'
TARGET_RATIO_ENV = 'WIZ_BUILD_OUTPUT_TARGET_RATIO'
OUTPUT_CODEC_ENV = 'WIZ_BUILD_OUTPUT_CODEC'             # 缺省 legacy-v1；在线启用 compact 须先过 G2
CALIBRATION_SAMPLE_MIN = 20             # 校准桶样本数阈值：<20 用最大观测×1.25，≥20 用 P90×1.25
CALIBRATION_SAFETY = 1.25
CALIBRATION_MAX_SAMPLES = 20            # 校准桶保留最多 20 个采样值
SOFT_PACKING_TARGET_TOKENS = 3000       # 整批可见输出软装箱目标（超了先缩组，不是硬 cap）

# --- 作业/尝试状态机 ----------------------------------------------------------------

JOB_QUEUED = 'queued'
JOB_RUNNING = 'running'
JOB_SPLIT = 'split'
JOB_SUCCEEDED = 'succeeded'
JOB_FAILED = 'failed'
JOB_BLOCKED = 'blocked'
JOB_SUPERSEDED = 'superseded'
JOB_STATES = (JOB_QUEUED, JOB_RUNNING, JOB_SPLIT, JOB_SUCCEEDED, JOB_FAILED, JOB_BLOCKED,
              JOB_SUPERSEDED)
# 合法迁移（其余一律拒绝；apply_event 据此校验）：
# queued → running | superseded | blocked（规划期即知不可容纳）
# running → succeeded | failed | split | blocked
# split/succeeded/failed/blocked/superseded → 终态，不再迁移
JOB_TRANSITIONS = {
    JOB_QUEUED: (JOB_RUNNING, JOB_SPLIT, JOB_SUPERSEDED, JOB_BLOCKED),
    JOB_RUNNING: (JOB_SUCCEEDED, JOB_FAILED, JOB_SPLIT, JOB_BLOCKED),
    JOB_SPLIT: (),
    JOB_SUCCEEDED: (),
    JOB_FAILED: (),
    JOB_BLOCKED: (),
    JOB_SUPERSEDED: (),
}

ATTEMPT_STARTED = 'started'
ATTEMPT_SUCCEEDED = 'succeeded'
ATTEMPT_FAILED = 'failed'
ATTEMPT_INTERRUPTED_UNKNOWN = 'interrupted_unknown'
ATTEMPT_STATES = (ATTEMPT_STARTED, ATTEMPT_SUCCEEDED, ATTEMPT_FAILED, ATTEMPT_INTERRUPTED_UNKNOWN)

# --- usage 口径（冻结，防双计）-------------------------------------------------------
# usage dict 形状：{'promptTokens': int|None, 'completionTokens': int|None,
#                   'reasoningTokens': int|None, 'totalTokens': int|None,
#                   'usageSource': 'api'|'absent'}
# * completionTokens 逐字取 provider 返回的 completion_tokens；OpenAI 兼容语义下已含推理输出，
#   **聚合只加 completionTokens，绝不 completionTokens+reasoningTokens 双计**；
#   reasoningTokens 仅单独展示，只有 provider 文档明确分离时才允许相加（当前无此 provider）。
# * usage 缺失（usageSource='absent'）时四个 token 字段全为 None；聚合输出
#   knownCompletionTokens（已知部分和）与 unknownUsageCalls（未知次数），不计 0、不冒充总数。


def empty_usage():
    return {'promptTokens': None, 'completionTokens': None, 'reasoningTokens': None,
            'totalTokens': None, 'usageSource': 'absent'}


def normalize_usage(raw):
    """provider usage → 冻结形状；幂等（回喂本函数输出形状不丢信息）；缺项 None，绝不猜 0。

    同时接受原始 snake_case（prompt_tokens…）与规范化 camelCase（promptTokens…）——
    D08 落库/D13 聚合把已规范化 usage 再喂回来时不得被判 absent。
    """
    raw = raw if isinstance(raw, dict) else {}

    def _int(value):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return int(value) if value >= 0 else None

    prompt = _int(raw.get('prompt_tokens', raw.get('promptTokens')))
    completion = _int(raw.get('completion_tokens', raw.get('completionTokens')))
    reasoning = _int(raw.get('reasoning_tokens', raw.get('reasoningTokens')))
    total = _int(raw.get('total_tokens', raw.get('totalTokens')))
    source = raw.get('usageSource', raw.get('usage_source'))
    present = any(value is not None for value in (prompt, completion, reasoning, total))
    if present and source in (None, '', 'absent'):
        source = 'api'
    elif not present:
        source = 'absent' if source in (None, '') else str(source)
    return {'promptTokens': prompt, 'completionTokens': completion, 'reasoningTokens': reasoning,
            'totalTokens': total, 'usageSource': str(source)}


def add_usage(left, right):
    """聚合两份 usage（None 传播；已知+未知=已知部分保留、unknown 次数累加）。"""
    def _sum(a, b):
        if a is None and b is None:
            return None
        return int(a or 0) + int(b or 0)

    left = normalize_usage(left)
    right = normalize_usage(right)
    known_left = left['usageSource'] == 'api'
    known_right = right['usageSource'] == 'api'
    merged_unknown = (0 if known_left else 1) + (0 if known_right else 1)
    return {'promptTokens': _sum(left['promptTokens'], right['promptTokens']),
            'completionTokens': _sum(left['completionTokens'], right['completionTokens']),
            'reasoningTokens': _sum(left['reasoningTokens'], right['reasoningTokens']),
            'totalTokens': _sum(left['totalTokens'], right['totalTokens']),
            'usageSource': 'api' if merged_unknown == 0 else
                           ('partial' if min(known_left, known_right) else 'absent'),
            'unknownUsageCalls': merged_unknown}


def usage_aggregate(attempts_usage):
    """按 attempt 序列聚合：返回 {'promptTokens','completionTokens','totalTokens',
    'knownCompletionTokens','unknownUsageCalls','calls'}。unknown 不计 0。"""
    prompt = completion = total = 0
    known_completion = 0
    unknown_calls = 0
    calls = 0
    for usage in attempts_usage or []:
        usage = normalize_usage(usage)
        calls += 1
        if usage['usageSource'] == 'api':
            prompt += int(usage['promptTokens'] or 0)
            completion += int(usage['completionTokens'] or 0)
            total += int(usage['totalTokens'] or 0)
            known_completion += int(usage['completionTokens'] or 0)
        else:
            unknown_calls += 1
    return {'promptTokens': prompt if not unknown_calls else None,
            'completionTokens': completion if not unknown_calls else None,
            'totalTokens': total if not unknown_calls else None,
            'knownCompletionTokens': known_completion,
            'unknownUsageCalls': unknown_calls,
            'calls': calls}


# --- 预算 profile（D01 budget_profile.py 实现；此处冻结形状与校验规则） ---------------
# BudgetProfile dict：
# {'enabled': bool, 'providerId': str, 'model': str,
#  'contextTokens': int|None, 'outputLimitTokens': int|None,
#  'requestOutputTokens': int, 'targetRatio': float,
#  'limitsSource': 'configured'|'verified_adapter',
#  'errors': [{'code': str, 'message': str}],          # 非空 = 拒绝启用（422 依据）
#  'effective': {'outputCap': int|None, 'targetBudget': int|None, 'inputReserve': int|None}}
# 推导：outputCap L = min(requestOutputTokens, outputLimitTokens)；
#       targetBudget T = floor(L * targetRatio)；inputReserve = max(2048, ceil(0.02 * C))。
# 输入约束：I + L + inputReserve ≤ contextTokens。
# limitsSource 只能是 'configured'（env 显式提供）或 'verified_adapter'（已验证 provider 适配），
# 不从模型名推断能力。


def input_reserve_tokens(context_tokens):
    return max(2048, int(math.ceil(0.02 * int(context_tokens))))


def _env_str(name):
    return str(os.environ.get(name) or '').strip()


def _env_positive_int(name):
    raw = _env_str(name)
    if not raw:
        return None, None
    try:
        value = int(raw)
    except ValueError:
        return None, BUDGET_CONFIG_INVALID
    return (value, None) if value > 0 else (None, BUDGET_CONFIG_INVALID)


def _env_ratio(name):
    raw = _env_str(name)
    if not raw:
        return DEFAULT_TARGET_RATIO, None
    try:
        value = float(raw)
    except ValueError:
        return None, BUDGET_CONFIG_INVALID
    if not (TARGET_RATIO_MIN <= value <= TARGET_RATIO_MAX):
        return None, BUDGET_CONFIG_INVALID
    return value, None


def _env_bool(name):
    return str(os.environ.get(name) or '').strip().lower() in ('1', 'true', 'yes', 'on')


def build_profile(env=None):
    """读环境 → BudgetProfile dict（D01 实现，签名冻结）。

    env 缺省取 os.environ（测试注入 dict）。只读、零副作用、不写任何存储。
    enabled=False 时其余字段仍尽量填充（capabilities 展示用），errors 只在 enabled 时收集。
    """
    source = os.environ if env is None else env

    def _get(name):
        return str(source.get(name) or '').strip()

    enabled = _get(ADAPTIVE_ENV).lower() in ('1', 'true', 'yes', 'on')
    profile = {
        'enabled': enabled,
        'providerId': _get(PROFILE_PROVIDER_ENV),
        'model': _get(PROFILE_MODEL_ENV),
        'contextTokens': None,
        'outputLimitTokens': None,
        'requestOutputTokens': DEFAULT_REQUEST_OUTPUT_TOKENS,
        'targetRatio': DEFAULT_TARGET_RATIO,
        'limitsSource': 'configured',
        'errors': [],
        'effective': {'outputCap': None, 'targetBudget': None, 'inputReserve': None},
    }
    context_tokens, context_err = _positive(_get(CONTEXT_TOKENS_ENV))
    output_limit, output_err = _positive(_get(OUTPUT_LIMIT_ENV))
    request_output, request_err = _positive(_get(REQUEST_OUTPUT_ENV))
    ratio, ratio_err = _ratio(_get(TARGET_RATIO_ENV))
    if request_err is None and request_output is not None:
        profile['requestOutputTokens'] = request_output
    if ratio_err is None and ratio is not None:
        profile['targetRatio'] = ratio
    if not enabled:
        return profile
    errors = []
    if context_err or context_tokens is None:
        errors.append(_issue(BUDGET_PROFILE_REQUIRED if context_tokens is None and not context_err
                             else BUDGET_CONFIG_INVALID, CONTEXT_TOKENS_ENV))
    else:
        profile['contextTokens'] = context_tokens
    if output_err or output_limit is None:
        errors.append(_issue(BUDGET_PROFILE_REQUIRED if output_limit is None and not output_err
                             else BUDGET_CONFIG_INVALID, OUTPUT_LIMIT_ENV))
    else:
        profile['outputLimitTokens'] = output_limit
    if not profile['providerId'] or not profile['model']:
        errors.append(_issue(BUDGET_PROFILE_REQUIRED, 'provider binding'))
    if request_err:
        errors.append(_issue(BUDGET_CONFIG_INVALID, REQUEST_OUTPUT_ENV))
    if ratio_err:
        errors.append(_issue(BUDGET_CONFIG_INVALID, TARGET_RATIO_ENV))
    profile['errors'] = errors
    if not errors and profile['contextTokens'] and profile['outputLimitTokens']:
        cap = min(profile['requestOutputTokens'], profile['outputLimitTokens'])
        profile['effective'] = {
            'outputCap': cap,
            'targetBudget': int(math.floor(cap * profile['targetRatio'])),
            'inputReserve': input_reserve_tokens(profile['contextTokens']),
        }
    return profile


def _positive(raw):
    if not raw:
        return None, None
    try:
        value = int(raw)
    except ValueError:
        return None, BUDGET_CONFIG_INVALID
    return (value, None) if value > 0 else (None, BUDGET_CONFIG_INVALID)


def _ratio(raw):
    if not raw:
        return None, None
    try:
        value = float(raw)
    except ValueError:
        return None, BUDGET_CONFIG_INVALID
    return (value, None) if TARGET_RATIO_MIN <= value <= TARGET_RATIO_MAX \
        else (None, BUDGET_CONFIG_INVALID)


def _issue(code, field):
    return {'code': code, 'message': field}


def validate_profile(profile, provider_id=None, model=None):
    """启用前校验（D01 实现可复用）：返回错误列表；非空 = 422 依据，不启动 worker。

    provider_id/model 传入时核验与 profile 绑定一致（BUDGET_PROFILE_MISMATCH），
    禁止把 A 模型的能力 profile 套给 B。纯函数，零副作用。
    """
    errors = list((profile or {}).get('errors') or [])
    if (profile or {}).get('enabled'):
        if provider_id is not None and str(provider_id) != str(profile.get('providerId')):
            errors.append(_issue(BUDGET_PROFILE_MISMATCH, 'providerId'))
        if model is not None and str(model) != str(profile.get('model')):
            errors.append(_issue(BUDGET_PROFILE_MISMATCH, 'model'))
    return errors


# --- 目标 / 选择器（D03 semantic_units.py 实现）--------------------------------------
# Target dict（冻结）：
# {'targetId': str,                       # 计划内稳定 id（'t-' 前缀，内容派生）
#  'factId': str,                         # 来源事实 id（库内真实 fact_id）
#  'selector': {'type': 'whole'} |
#              {'type': 'field', 'field': str} |
#              {'type': 'range', 'start': int, 'end': int},
#  'kind': str,                           # 单元类型（校准桶维度）：
#                                         # jsonld_node / ddl_table / code_symbol / doc_section /
#                                         # json_object / adjacent_window / single_fact
#  'subjectKey': str,                     # 主体分组键（materialId+nodeId / 表 / 符号 / 章节 …）
#  'materialId': str,
#  'groupingConfidence': 'high'|'low'}
# 分组优先级：JSON-LD 按 materialId+nodeId；DDL 按 materialId+表；代码按 materialId+限定符号；
# 文档按 materialId+章节；普通 JSON 按最近对象路径（数组下标保留）；缺定位 → 物料内相邻窗口，
# groupingConfidence='low'。**每个范围内事实至少产生一个 target**：不因值短/像元数据删除事实。
# build_targets(facts) -> {'targets': [Target], 'groups': [{'subjectKey','targetIds','confidence'}],
#                          'stats': {'factCount': n, 'targetCount': n, 'lowConfidenceGroups': n}}
# context_fact_ids(targets, groups, max_context=8)（D03 冻结语义）：
#   targets=同次 build_targets 全量 targets（targetId→factId 解析表）；
#   groups=本叶任务主目标所在组（可为主组子集）；
#   上下文=同 subjectKey 且不属于主目标组的邻近事实，机械保证与主目标 factId 不相交（不算覆盖）。


def canonical_selector(selector):
    """selector → 规范化字符串（jobId 派生与覆盖比对唯一口径）。"""
    if not isinstance(selector, dict) or selector.get('type') in (None, '', 'whole'):
        return 'whole'
    kind = str(selector.get('type') or '')
    if kind == 'field':
        return 'field:' + str(selector.get('field') or '')
    if kind == 'range':
        return 'range:%s:%s' % (int(selector.get('start') or 0), int(selector.get('end') or 0))
    return 'unknown:' + json.dumps(selector, ensure_ascii=False, sort_keys=True)[:100]


def target_digest(target):
    """Target → 覆盖比对用的身份串（factId+selector；不含 subjectKey 等展示字段）。"""
    return '%s#%s' % (str(target.get('factId') or ''), canonical_selector(target.get('selector')))


# --- 估算（D02 budget.py 实现）-------------------------------------------------------
# estimate_request(messages) -> {'inputTokens': int, 'estimateKind': 'utf8_proxy'}
#   I = 完整序列化 messages 的 UTF-8 字节总数 + 32×消息条数（消息开销）。
#   字节当 token 的保守代理（中文约 1.5–2 字节/token，英文约 4）：宁可高估触发细分，
#   不低估导致输出截断。有本地已验证 tokenizer 时才允许 estimateKind='tokenizer'（本期无）。
# cold_output_estimate(slots, input_tokens) -> V = max(1024, 256×slots + ceil(0.25×I))
#   slots：可确定目标数；普通孤立事实 ≥1；信息结构未知的数组摘要/文本目标按 4 计。
# expected_output(unit, calibration) -> E = max(冷启动 V, 校准估算)；永不低于冷启动值。
#   校准桶 key = (provider配置标识, model, promptVersion, outputCodecVersion, 推理摘要, unitKind)；
#   成功且有 usage 的样本以 total completion / slots 更新桶；
#   样本 <20 → max(观测)×1.25；≥20 → P90×1.25；截断样本不进桶；unknown usage 不记 0。
# 反馈只缩不扩：v3 只允许保守系数上调与缩小后续批次；不自动扩大上一批单元数。


def estimate_request(messages):
    """冻结口径（D02 实现须逐字一致）：UTF-8 字节代理 + 每消息 32 开销。"""
    total_bytes = 0
    count = 0
    for message in messages or []:
        count += 1
        if isinstance(message, dict):
            total_bytes += len(json.dumps(message, ensure_ascii=False, default=str)
                               .encode('utf-8'))
        else:
            total_bytes += len(str(message).encode('utf-8'))
    return {'inputTokens': total_bytes + 32 * count, 'estimateKind': 'utf8_proxy'}


def cold_output_estimate(slots, input_tokens):
    slots = max(1, int(slots or 1))
    return max(1024, 256 * slots + int(math.ceil(0.25 * int(input_tokens or 0))))


# --- 装箱 / 拆分 / 覆盖（D04 batch_plan.py 实现）--------------------------------------
# pack_next(plan_doc, estimate_fn, profile) -> {'dispatchable': [jobId], 'created': [job],
#                                               'softTargetExceeded': [jobId], 'blocked': [..]}
#   规则：冷启动先试最多 2 个完整语义单元；顺序贪心装箱；每步以整批 messages 重新估算 I，
#   同时满足 I+L+reserve≤C、ΣE(unit)≤T、primary≤64；整批可见输出估算 V≤3000 软目标，
#   超过先缩组；单原子目标硬预算容纳不下 → split 或 blocked（记录 softTargetExceeded 不算失败）。
#   已建 job 不可重排；只对未建 job 的 pending targets 规划下一批（首版）。
# split_job(job, targets_data, depth) -> {'children': [job], 'errorCode': None} |
#                                         {'children': [], 'errorCode': OVERSIZED_ATOMIC_TARGET|
#                                          SPLIT_DEPTH_EXCEEDED}
#   优先按语义单元二分；单元内再按属性组/关系组；必须验证子目标不重不漏覆盖父目标
#   （selector 拆分按 selector 验证；context 允许重叠）；拆分映射持久化后再派发。
# stable_job_id(plan_epoch, parent_job_id, split_path, targets) -> 'j-<sha1[:20]>'
#   由 planEpoch+稳定 target 集合及 selector+splitPath 派生，绝不按完成顺序生成。
# coverage_check(planned, completed) -> {'ok': bool, 'missing': [...], 'duplicates': [...],
#                                        'extra': [...]}
#   叶目标集合 == 计划目标集合且不重复（终态检查的一部分）。


def stable_job_id(plan_epoch, parent_job_id, split_path, targets):
    payload = '|'.join([
        str(int(plan_epoch or 0)),
        str(parent_job_id or ''),
        str(split_path or 'root'),
        '|'.join(sorted(target_digest(target) for target in (targets or []))),
    ])
    return 'j-' + hashlib.sha256(payload.encode('utf-8')).hexdigest()[:20]


def coverage_check(planned_digests, completed_digests):
    planned = [str(item) for item in (planned_digests or [])]
    completed = [str(item) for item in (completed_digests or [])]
    planned_set = set(planned)
    completed_set = set(completed)
    missing = sorted(planned_set - completed_set)
    seen = set()
    duplicates = sorted({item for item in completed
                         if item in seen or seen.add(item)})
    extra = sorted(completed_set - planned_set)
    return {'ok': not missing and not duplicates and not extra,
            'missing': missing, 'duplicates': duplicates, 'extra': extra}


# --- checkpoint v2 内部结构（D07/D08 持久化；轮询对外只回摘要）-------------------------
# checkpoint['generate'] = {
#   'schemaVersion': 2, 'adaptive': True,
#   'batchId': str, 'scopeRevision': int, 'materialRevision': int,      # 旧字段保留
#   'planId': str, 'planEpoch': int, 'fingerprint': str,
#   'plannerVersion': 'v3', 'promptVersion': 'v3', 'outputCodecVersion': 'legacy-v1',
#   'selectedFactsDigest': str, 'providerFingerprint': {...非密},
#   'budgetProfile': {冻结 BudgetProfile 的有界子集}, 'calibrationSnapshot': {有界桶摘要},
#   'activeElapsedMs': int,
#   'targets': {targetId: {'factId','selector','subjectKey','kind'}},
#   'pendingTargetIds': [targetId],
#   'jobs': {jobId: {'parentId','rootId','orderedPrimaryTargetIds','contextFactIds','state',
#                    'children','attemptIds','estimate','candidateCount','resultDigest',
#                    'errorCode','splitPath'}},
#   'attempts': {attemptId: {'jobId','sequence','runAttempt','requestFingerprint',
#                            'requestedMaxTokens','state','finishReason','usage','bytes',
#                            'durationMs','errorCode','errorMessage'}},
#   （jobs[*] 另有 'supersededBy'：queued 被重打包时指向新 job，持久化重打包映射 §4.3）
#   'coverage': {'targetTotal': n, 'targetCompleted': n, 'targetPending': n},
#   'usageAggregate': {usage_aggregate 输出},
#   'blocking': {'code': str, 'message': str} | None,
#   'log': [str], 'notes': [str]}
# 容量闭环（D07 守卫 / D08 持久化前检查）：
#   软阈值 = 1MiB − 16KiB − 8KiB×(在途调用数+1)；不足 → 停止派发并记
#   CHECKPOINT_BUDGET_EXCEEDED；在途响应仍可原子保存候选与安全 usage。


def checkpoint_soft_limit_bytes(inflight_calls):
    return (MAX_CHECKPOINT_BYTES - CHECKPOINT_TERMINAL_RESERVE_BYTES
            - CHECKPOINT_ATTEMPT_RESERVE_BYTES * (int(inflight_calls or 0) + 1))


def checkpoint_fits(checkpoint, inflight_calls):
    """序列化后对照软阈值；超限返回 False（调用方停止派发，不截数据）。"""
    size = len(json.dumps(checkpoint, ensure_ascii=False, default=str).encode('utf-8'))
    return size <= checkpoint_soft_limit_bytes(inflight_calls)


def new_attempt_id():
    return 'a-' + os.urandom(16).hex()


def plan_fingerprint(parts):
    """计划指纹：facts 有序内容摘要、scope/material revision、provider 非密配置标识、模型、
    提示/协议/估算版本、预算 profile、codec。任一变化 → auto 拒绝（BUDGET_PLAN_MISMATCH）。
    parts 为有序字符串列表（调用方按冻结顺序组装）；密钥绝不进 parts。"""
    payload = '|'.join(str(part) for part in (parts or []))
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()[:32]


# --- 终态检查（D07 实现；runner.finish_success v2 防线复用）---------------------------
# final_state_check(plan_doc) -> {'ok': bool, 'violations': [{'code','message'}]}
#   同时校验：pendingTargetIds 为空；所有有效叶 job succeeded；叶目标集合==计划目标且不重复；
#   无 blocked/failed/running/queued；未发生未确认的规模裁切（blocking 为 None）。
#   候选结构检查由既有交付校验承担；本函数只对计划状态负责。


def final_state_check(plan_doc):
    violations = []
    generate = (plan_doc or {}).get('generate') if isinstance(plan_doc, dict) else None
    if not isinstance(generate, dict) or int(generate.get('schemaVersion') or 0) != 2:
        return {'ok': False, 'violations': [{'code': UNKNOWN_CHECKPOINT_SCHEMA,
                                             'message': '缺少 schemaVersion=2 计划'}]}
    jobs = generate.get('jobs') if isinstance(generate.get('jobs'), dict) else {}
    targets = generate.get('targets') if isinstance(generate.get('targets'), dict) else {}
    pending = generate.get('pendingTargetIds') or []
    if pending:
        violations.append({'code': 'PENDING_TARGETS', 'message': '仍有未处理目标 %d 个' % len(pending)})
    covered = []
    for job_id, job in jobs.items():
        state = str(job.get('state') or '')
        children = job.get('children') or []
        if children:
            if state != JOB_SPLIT:
                violations.append({'code': 'PARENT_NOT_SPLIT',
                                   'message': '父作业 %s 状态为 %s 但存在子作业' % (job_id, state)})
            continue
        if state == JOB_SUCCEEDED:
            covered.extend(job.get('orderedPrimaryTargetIds') or [])
        elif state in (JOB_QUEUED, JOB_RUNNING):
            violations.append({'code': 'ACTIVE_JOB_REMAINS', 'message': '作业 %s 仍在 %s'
                               % (job_id, state)})
        else:
            violations.append({'code': 'UNFINISHED_JOB', 'message': '作业 %s 状态 %s（%s）'
                               % (job_id, state, str(job.get('errorCode') or ''))})
    check = coverage_check([target_digest({'factId': (targets.get(tid) or {}).get('factId'),
                                           'selector': (targets.get(tid) or {}).get('selector')})
                            for tid in targets],
                           [target_digest({'factId': (targets.get(tid) or {}).get('factId'),
                                           'selector': (targets.get(tid) or {}).get('selector')})
                            for tid in covered])
    if not check['ok']:
        violations.append({'code': 'COVERAGE_MISMATCH',
                           'message': '叶覆盖与计划不一致（缺 %d / 重 %d / 多 %d）'
                                      % (len(check['missing']), len(check['duplicates']),
                                         len(check['extra']))})
    if generate.get('blocking'):
        violations.append({'code': str(generate['blocking'].get('code') or 'BLOCKED'),
                           'message': str(generate['blocking'].get('message') or '')})
    return {'ok': not violations, 'violations': violations}


# --- 单次调用结果（D05 llm_client.chat_once_result 实现）------------------------------
# CallResult dict（冻结）：
# {'ok': bool,            # HTTP 2xx 且响应可解析为 chat/completions 结构（与 finishReason 无关）
#  'content': str|None,   # ok 时助手正文（length 时也给出已返回部分，仅诊断；decode 层拒收）
#  'finishReason': str|None,   # 'stop'|'length'|其他 provider 原值
#  'errorCode': str|None, # 'NETWORK_RETRYABLE'|'RATE_LIMITED'|'PROVIDER_ERROR'|'TIMEOUT'|
#                         # 'RESPONSE_TOO_LARGE'|'RESPONSE_MALFORMED'|'CONFIG_INVALID'|
#                         # 'HTTP_UNKNOWN'（未知 HTTP 错误不猜类别）
#  'retryable': bool,     # 网络/429/可重试 5xx → True；配置/格式/超大响应 → False
#  'usage': usage dict（normalize_usage 形状）,
#  'requestBytes': int, 'responseBytes': int, 'durationMs': int}
# 纪律：绝不抛异常（网络失败返回 ok=False）；HTTP 成功先提取 usage 再判 finish_reason，
# 截断不丢用量；正文/响应原文不进日志；reasoning 原文不保存。
# 旧 chat() 签名与行为保持兼容（build 之外的调用方不受影响）。


# --- 编解码（D06 output_codec.py 实现）------------------------------------------------
# encode_request(codec_version, job_targets, context_facts, scope_payload,
#                property_data_types=None, value_types=None)
#   -> {'messages': [...], 'aliasMap': {alias(f0..): factId}, 'unitIds': [targetId..]}
#   job_targets：本叶 job 主目标（targetId/factId/selector/kind/subjectKey）；
#   context_facts：背景事实 dict 列表（只供理解，不算覆盖，不带 id、不可被引用）。
#   别名只在本调用内有效，程序保留 alias→factId/selector 映射，不跨批复用。
#   主目标与背景必须区分（提示词声明"只为本次主目标生成定义，背景对象只作引用"）。
#   证据范围 = aliasMap 值 = 本次主目标事实；背景事实不可作为证据引用。
# decode_response(codec_version, content, alias_map, expected_unit_ids)
#   -> {'ok': bool, 'candidates': [normalized dict], 'coverage': [{'unit','status'}],
#       'errors': [{'code','message'}], 'notes': [str], 'rejectedRefs': int}
#   legacy-v1：现行 SYSTEM_EXTRACT 输出结构（candidates 全字段）；无 coverage 字段，
#   完成依据=请求目标+严格响应协议（finishReason==stop 由调用方判定）。
#   compact-v1：顶层 codecVersion/candidates/coverage；coverage 漏目标 → ok=False+
#   COVERAGE_INCOMPLETE（整叶不提交，最多 1 次覆盖修复，与 maxJobAttempts 共用）；
#   未知别名 → 丢候选+DANGLING_REFERENCE；缺字段不补造（supported/owner/单位/基数/定义
#   禁止程序默认）；截断内容（调用方传入 finishReason='length' 标记）直接拒绝不解析。
# normalized candidate dict：与现行 _sanitize_candidates 输出同形
#   {key,type,name,definition,fields,ownerKey,evidence{字段:[factId]},evidenceStatus,
#    conflicts,rejectedRefs}（evidence 用还原后的真实 factId，不含别名）。


# --- 持久化接口（D08 在线 / D12 实验 SQLite 双实现；契约冻结）--------------------------
# class Persistence（鸭子类型契约；两实现 + 执行器共享，禁止复制调度器）：
#   load(task_key) -> plan_doc|None                      # 读完整计划（在线=checkpoint，实验=行）
#   save_plan(task_key, plan_doc) -> None                # 初次建计划，原子；容量守卫在调用方
#   claim_job(task_key, job_id, run_attempt) ->
#       {'ok': bool, 'attemptId': str, 'requestedMaxTokens': int}
#       ①先持久化 started attempt + job running（含执行权核验）再返回，失败必须抛出。
#   commit_success(task_key, job_id, attempt_id, candidates, usage, result_digest,
#                  attempt_meta=None) -> bool
#       ②同事务：候选（幂等：job+局部键）+ attempt succeeded/usage + job succeeded + 摘要。
#       attempt_meta 可选 {'finishReason','bytes','durationMs'}（截断/耗时通道，缺省 None）。
#       返回 bool 语义（冻结）：True=本次新提交；False=幂等跳过（job 已终态的重复回调）。
#       候选 origin 带 planEpoch/jobId。事务内先核验执行权/取消（content_tx 语义）。
#   commit_split(task_key, parent_job_id, child_jobs, usage, finish_reason=None) -> bool
#       ③同事务：父 split + 全部子 job queued + attempt 记账；先持久化再派发子 job。
#       父作业的全部 started 尝试以同一份 usage 收口入总账（当前状态机每 job 至多一个
#       started 尝试，语义无损；finish_reason 记 'length' 通道）。
#   commit_failure(task_key, job_id, attempt_id, error_code, message, usage) -> bool
#   mark_interrupted_unknown(task_key, attempt_ids) -> int
#       崩溃恢复：started → interrupted_unknown（真实计费可能发生，显示未知，不计 0）。
#       **不带 lease 条件**（冻结口径）：崩溃后旧 lease 必然失配，带条件反而无法恢复；
#       归属核验（owner/run）仍然必须。
#   iterate_candidates(task_key, page_size=200) -> iterator[list[candidate dict]]
#       内部分页迭代（不复用 all_candidates(limit=500) 当全量）；复验/终态检查必须走这里。
# 两实现（storage/ontology_build.py 扩展 + experiments/.../state.py）通过同一组恢复契约
# 参数化用例（D17 组合）：晚结果拒绝、取消不复活、崩溃 unknown、成功叶不重做。
# 执行器纪律：Persistence 抛异常 = 未保存成功；执行器在 save 成功后才推进内存状态，
# 绝不先改内存 done 再吞事务异常。


# --- 执行器（D09 batch_execution.py 实现；在线与实验唯一共享核心）----------------------
# make_context(persistence, call_fn, codec_version, profile, plan_doc, clock=None,
#              sleeper=None, calibration=None) -> context dict
#   call_fn(messages, max_tokens) -> CallResult（D05）；clock/sleeper 可注入（测试）。
# step(context) -> event dict
#   单步推进：取一个可派发 job → checkpoint 容量守卫 → persistence.claim_job（先持久化
#   started）→ call_fn → 事件分类（§5 表）→ persistence.commit_* → 返回 event。
#   严格：持久化失败不推进内存 done；预算（attempts/wall/软限）到线 → blocked/failed。
# run_plan(context, budget) -> {'state': 'succeeded'|'failed'|'blocked'|'cancelled',
#                               'blocking': {...}|None, 'usage': aggregate, ...}
#   循环调 step 直到终态检查通过或预算耗尽；取消由 persistence/content_tx 层核验。
# 事件分类（冻结）：
#   ok+stop+schema有效 → commit_success（候选空也算该目标完成，不宣称无业务概念）
#   length/字节超限 → 记账 attempt → commit_split（子 job 落库后再派发）
#   INPUT_BUDGET_EXCEEDED → 细分主目标（不删 primary）
#   非JSON/schema → 同 job 1 次格式修复（共享 3 次总额）→ 仍错 commit_failure
#   网络/429/可重试5xx → 同 job ≤2 次退避重试（总额≤3）→ 仍错 commit_failure
#   不可分/深度/任务/耗时/次数/候选数预算耗尽 → commit blocked（可读原因，成功叶保留）


# --- 对外 HTTP 摘要（D15/D16 契约；轮询只回摘要，不回全量 jobs/attempts/factIds/正文）---
# capabilities.generationBudget = {
#   'enabled': bool, 'configured': bool,          # configured = profile 无 errors 且已启用
#   'configErrors': [{'code','message'}],
#   'contextTokens': int|None, 'requestOutputTokens': int, 'outputLimitTokens': int|None,
#   'effectiveOutputCap': int|None, 'targetRatio': float, 'limitsSource': str,
#   'planTargets': 4096, 'planJobs': 512, 'planAttempts': 1024, 'checkpointBytes': 1048576,
#   'codec': 'legacy-v1'|'compact-v1'}            # effectiveCodec；不给密钥/fact 清单
# Run.checkpoint.generate（schema2 运行）在旧字段基础上新增：
#   {'schemaVersion': 2, 'adaptive': True, 'planId','planEpoch',
#    'jobs': {'queued':n,'running':n,'succeeded':n,'failed':n,'blocked':n,'splitParents':n},
#    'coverage': {'targetTotal':n,'targetCompleted':n,'targetPending':n},
#    'estimateKind': str,
#    'budget': {'contextLimit':C,'requestOutputCap':L,'targetOutputBudget':T},
#    'blocking': {'code','message'}|None}
#   schema2 运行不再输出旧 batches 字段（前端按 schemaVersion 分支显示目标进度）；
#   旧字段 batchId/scopeRevision/materialRevision/log/notes 保留。
# Run.usage 新增（与旧 bytes/calls 并存，token 可为 null）：
#   {'calls', 'promptBytes', 'completionBytes', 'durationMs',
#    'promptTokens'|None, 'completionTokens'|None, 'totalTokens'|None,
#    'knownCompletionTokens', 'unknownUsageCalls', 'planEpoch'|None}
# 同步 422 时机：scope-confirm/regenerate/resume 先完成 profile 校验、provider 绑定、
#   旧计划指纹与有界轻量数量检查（全部在状态变更事务之前），事务内重核 revision；
#   422 拒绝时 task/run/candidates 零变更。已接受作业后异步规划发现超限 → Run.failed +
#   checkpoint.generate.blocking，不伪造 HTTP 422。


def budget_view(profile, codec):
    """capabilities.generationBudget 组装（D16 使用；D15 按此渲染）。"""
    profile = profile or {}
    errors = list(profile.get('errors') or [])
    enabled = bool(profile.get('enabled'))
    effective = profile.get('effective') or {}
    return {
        'enabled': enabled,
        'configured': enabled and not errors,
        'configErrors': errors,
        'contextTokens': profile.get('contextTokens'),
        'requestOutputTokens': int(profile.get('requestOutputTokens') or 0),
        'outputLimitTokens': profile.get('outputLimitTokens'),
        'effectiveOutputCap': effective.get('outputCap'),
        'targetRatio': profile.get('targetRatio'),
        'limitsSource': str(profile.get('limitsSource') or 'configured'),
        'planTargets': MAX_PLAN_TARGETS,
        'planJobs': MAX_PLAN_JOBS,
        'planAttempts': MAX_PLAN_ATTEMPTS,
        'checkpointBytes': MAX_CHECKPOINT_BYTES,
        'codec': codec if codec in CODEC_VERSIONS else CODEC_LEGACY,
    }


def generate_checkpoint_view(generate):
    """checkpoint.generate 内部结构 → 轮询摘要（D16 使用）。不回全量 jobs/attempts/factIds。"""
    if not isinstance(generate, dict):
        return None
    if int(generate.get('schemaVersion') or 0) >= 2:
        jobs = generate.get('jobs') if isinstance(generate.get('jobs'), dict) else {}
        counts = {'queued': 0, 'running': 0, 'succeeded': 0, 'failed': 0, 'blocked': 0,
                  'splitParents': 0}
        for job in jobs.values():
            state = str(job.get('state') or '')
            if job.get('children'):
                counts['splitParents'] += 1
            if state in counts:
                counts[state] += 1
        coverage = generate.get('coverage') if isinstance(generate.get('coverage'), dict) else {}
        targets_count = len(generate.get('targets') or {})
        pending = len(generate.get('pendingTargetIds') or [])
        profile = generate.get('budgetProfile') if isinstance(generate.get('budgetProfile'), dict) else {}
        effective = profile.get('effective') if isinstance(profile.get('effective'), dict) else {}
        view = {
            'batchId': str(generate.get('batchId') or ''),
            'scopeRevision': int(generate.get('scopeRevision') or 0),
            'materialRevision': int(generate.get('materialRevision') or 0),
            'schemaVersion': 2,
            'adaptive': True,
            'planId': str(generate.get('planId') or ''),
            'planEpoch': int(generate.get('planEpoch') or 0),
            'jobs': counts,
            'coverage': {'targetTotal': int(coverage.get('targetTotal') or targets_count),
                         'targetCompleted': int(coverage.get('targetCompleted')
                                                or (targets_count - pending)),
                         'targetPending': int(coverage.get('targetPending') or pending)},
            'estimateKind': str(generate.get('estimateKind') or 'utf8_proxy'),
            'budget': {'contextLimit': profile.get('contextTokens'),
                       'requestOutputCap': effective.get('outputCap'),
                       'targetOutputBudget': effective.get('targetBudget')},
            'blocking': generate.get('blocking') if isinstance(generate.get('blocking'), dict) else None,
            'log': [str(line) for line in (generate.get('log') or []) if isinstance(line, str)],
            'notes': [str(line) for line in (generate.get('notes') or []) if isinstance(line, str)],
        }
        return view
    # schema1：旧摘要原样（与 storage.run_checkpoint_view 的 generate 分支同形）
    return _legacy_view(generate)


def _legacy_view(generate):
    """schema1 旧摘要的本地实现（与 storage.run_checkpoint_view 同形，避免存储层依赖）。"""
    plan = generate.get('plan') if isinstance(generate.get('plan'), dict) else {}
    batches = generate.get('batches') if isinstance(generate.get('batches'), dict) else {}
    failed = [item for item in batches.get('failed') or [] if isinstance(item, dict)]

    def _string_list(value):
        if not isinstance(value, list):
            return []
        return [item for item in value if isinstance(item, str)]

    return {
        'batchId': str(generate.get('batchId') or ''),
        'scopeRevision': int(generate.get('scopeRevision') or 0),
        'materialRevision': int(generate.get('materialRevision') or 0),
        'planPersisted': bool(plan.get('modelFactIds')),
        'modelFacts': len(plan.get('modelFactIds') or []),
        'relevant': int(plan.get('relevant') or 0),
        'related': int(plan.get('related') or 0),
        'excluded': int(plan.get('excluded') or 0),
        'batches': {'size': int(batches.get('size') or 0), 'total': int(batches.get('total') or 0),
                    'done': [int(p) for p in batches.get('done') or []],
                    'failed': [{'position': int(item.get('position') or 0),
                                'error': str(item.get('error') or '')} for item in failed]},
        'log': _string_list(generate.get('log')),
        'notes': _string_list(generate.get('notes')),
    }


def usage_view(aggregated, plan_epoch=None):
    """usage 聚合 → Run.usage 增量字段（D16 使用；token 缺失为 None，不冒充 0）。"""
    aggregated = aggregated or {}
    return {
        'promptTokens': aggregated.get('promptTokens'),
        'completionTokens': aggregated.get('completionTokens'),
        'totalTokens': aggregated.get('totalTokens'),
        'knownCompletionTokens': int(aggregated.get('knownCompletionTokens') or 0),
        'unknownUsageCalls': int(aggregated.get('unknownUsageCalls') or 0),
        'planEpoch': int(plan_epoch) if plan_epoch is not None else None,
    }
