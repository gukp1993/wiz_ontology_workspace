"""本体生成控输出 v3 —— D01：能力 profile 与配置预检（纯函数公开模块）。

需求：《本体生成控输出_整合方案与并行开发计划_v3.md》§3/§8 D01 行（任务ID
auto-build-output-v3）；对外契约：接口文档 08 §14（同步 422 错误码与
capabilities.generationBudget）。协议冻结基线：batch_contracts.py（提交 87d1473）。

职责边界：
* build_profile / validate_profile / budget_view / input_reserve_tokens 的唯一实现
  在 batch_contracts.py（D00 冻结参考实现）；本模块只**再导出**，不复制任何逻辑。
  函数身份（`budget_profile.build_profile is batch_contracts.build_profile`）由
  tests/test_ontology_build_budget_profile.py 锁定。
* 本模块新增两个供接线层（D16 routes/capabilities）使用的纯函数：
  - request_budget_errors(profile, provider_id=None, model=None) -> [(code, message)]
    复用 validate_profile（profile 自身 errors + provider/model 绑定核对），产出
    中文可读 message（不含密钥），供 generate 相关入口在任何状态变更前组装 422；
  - effective_limits(profile) -> dict：BudgetProfile 的有界 limits 摘要
    （C/L/请求输出/比例 + effective 三值 + planTargets/jobs/attempts/checkpointBytes
    等冻结规模上限），供 capabilities 展示。

纪律（与 batch_contracts 一致）：
* 只依赖标准库与 batch_contracts；禁止 import 存储层/管线/路由。
* 纯函数零副作用：不改环境变量、不写任何文件、不发网络请求、不修改传入对象。
* 环境变量名（WIZ_BUILD_ADAPTIVE_BATCHING 等）与默认值全部以 batch_contracts 为准，
  本模块原样再导出，不另设口径。
"""
from workbench.ontology_build import batch_contracts as _contracts

# --- 再导出：冻结参考实现（单一事实来源在 batch_contracts，禁止复制逻辑） --------------

build_profile = _contracts.build_profile
validate_profile = _contracts.validate_profile
budget_view = _contracts.budget_view
input_reserve_tokens = _contracts.input_reserve_tokens

# --- 再导出：环境变量名与默认值（以 batch_contracts 为准） ----------------------------

ADAPTIVE_ENV = _contracts.ADAPTIVE_ENV                  # WIZ_BUILD_ADAPTIVE_BATCHING
PROFILE_PROVIDER_ENV = _contracts.PROFILE_PROVIDER_ENV  # WIZ_BUILD_PROFILE_PROVIDER_ID
PROFILE_MODEL_ENV = _contracts.PROFILE_MODEL_ENV        # WIZ_BUILD_PROFILE_MODEL
CONTEXT_TOKENS_ENV = _contracts.CONTEXT_TOKENS_ENV      # WIZ_BUILD_CONTEXT_TOKENS
OUTPUT_LIMIT_ENV = _contracts.OUTPUT_LIMIT_ENV          # WIZ_BUILD_OUTPUT_LIMIT_TOKENS
REQUEST_OUTPUT_ENV = _contracts.REQUEST_OUTPUT_ENV      # WIZ_BUILD_REQUEST_OUTPUT_TOKENS
TARGET_RATIO_ENV = _contracts.TARGET_RATIO_ENV          # WIZ_BUILD_OUTPUT_TARGET_RATIO
OUTPUT_CODEC_ENV = _contracts.OUTPUT_CODEC_ENV          # WIZ_BUILD_OUTPUT_CODEC

DEFAULT_REQUEST_OUTPUT_TOKENS = _contracts.DEFAULT_REQUEST_OUTPUT_TOKENS  # 32000
DEFAULT_TARGET_RATIO = _contracts.DEFAULT_TARGET_RATIO                    # 0.50
TARGET_RATIO_MIN = _contracts.TARGET_RATIO_MIN                            # 0.25
TARGET_RATIO_MAX = _contracts.TARGET_RATIO_MAX                            # 0.80

BUDGET_PROFILE_REQUIRED = _contracts.BUDGET_PROFILE_REQUIRED
BUDGET_PROFILE_MISMATCH = _contracts.BUDGET_PROFILE_MISMATCH
BUDGET_CONFIG_INVALID = _contracts.BUDGET_CONFIG_INVALID
HTTP_BUDGET_ERROR_CODES = _contracts.HTTP_BUDGET_ERROR_CODES


# --- D01 新增：HTTP 422 预检 ----------------------------------------------------------
# message 模板只覆盖 contracts 冻结的预检错误码；字段名部分来自 _issue 的 message
# （环境变量名 / 'provider binding' / 'providerId' / 'model'），均为非敏感标识。

_CODE_TEXTS = {
    BUDGET_PROFILE_REQUIRED: '启用自适应分批（v2 计划）必需的预算配置缺失（不猜模型窗口，缺失即拒绝）',
    BUDGET_PROFILE_MISMATCH: '请求的 provider/model 与能力 profile 绑定不符（禁止把 A 模型的能力套给 B）',
    BUDGET_CONFIG_INVALID: '预算配置非法（须为正整数；比例须在 0.25–0.80）',
}


def request_budget_errors(profile, provider_id=None, model=None):
    """启用前配置预检：返回 [(code, message)]；非空 = generate 入口应返回 422。

    复用 batch_contracts.validate_profile（签名冻结）：profile 自身 errors 原样计入，
    provider_id/model 传入时核对与 profile 绑定一致（BUDGET_PROFILE_MISMATCH）。
    message 为中文可读文案 + 非敏感字段标识，绝不含密钥。未启用（enabled=False）
    的 profile 按契约返回空列表——是否要求 profile 由调用方按开关判定。
    纯函数，零副作用，不抛异常。
    """
    errors = []
    for issue in validate_profile(profile, provider_id=provider_id, model=model):
        code = str((issue or {}).get('code') or '')
        field = str((issue or {}).get('message') or '')
        text = _CODE_TEXTS.get(code, '预算配置校验未通过')
        errors.append((code, '%s：%s' % (text, field) if field else text))
    return errors


def effective_limits(profile):
    """BudgetProfile → 有界 limits 摘要 dict（capabilities/日志展示用）。

    只含数值与枚举：C/L/请求输出/比例 + effective 三值（outputCap/targetBudget/
    inputReserve）+ batch_contracts 冻结的规模上限（planTargets/planJobs/
    planAttempts/checkpointBytes/拆分深度/单 job 尝试与主目标/活跃耗时/最终候选）。
    不含密钥、provider 凭据或 fact 清单。profile 非法/未启用时派生值为 None，
    规模上限仍原样展示（与 budget_view 同口径）。纯函数，零副作用。
    """
    profile = profile if isinstance(profile, dict) else {}
    effective = profile.get('effective')
    effective = effective if isinstance(effective, dict) else {}
    return {
        'contextTokens': profile.get('contextTokens'),
        'outputLimitTokens': profile.get('outputLimitTokens'),
        'requestOutputTokens': int(profile.get('requestOutputTokens') or 0),
        'targetRatio': profile.get('targetRatio'),
        'outputCap': effective.get('outputCap'),
        'targetBudget': effective.get('targetBudget'),
        'inputReserve': effective.get('inputReserve'),
        'planTargets': _contracts.MAX_PLAN_TARGETS,
        'planJobs': _contracts.MAX_PLAN_JOBS,
        'planAttempts': _contracts.MAX_PLAN_ATTEMPTS,
        'checkpointBytes': _contracts.MAX_CHECKPOINT_BYTES,
        'maxSplitDepth': _contracts.MAX_SPLIT_DEPTH,
        'maxJobAttempts': _contracts.MAX_JOB_ATTEMPTS,
        'maxPlanWallSeconds': _contracts.MAX_PLAN_WALL_SECONDS,
        'maxPrimaryTargetsPerJob': _contracts.MAX_PRIMARY_TARGETS_PER_JOB,
        'maxFinalCandidates': _contracts.MAX_FINAL_CANDIDATES,
    }
