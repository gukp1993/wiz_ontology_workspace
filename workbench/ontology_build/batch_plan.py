"""本体生成控输出 v3 —— D04：装箱 / 拆分 / 稳定ID / 覆盖守恒（纯函数模块）。

契约来源（冻结，不在本模块另立口径）：
* workbench/ontology_build/batch_contracts.py「装箱 / 拆分 / 覆盖」节（D00）：
  stable_job_id / coverage_check / target_digest / canonical_selector、各限额常量
  （MAX_PLAN_TARGETS=4096、MAX_PRIMARY_TARGETS_PER_JOB=64、MAX_SPLIT_DEPTH=8、
  SOFT_PACKING_TARGET_TOKENS=3000）与 checkpoint v2 作业结构；
* workbench/ontology_build/budget.py（D02）：unit_slots / cold_output_estimate /
  expected_output / shrink_for_feedback / estimate_request；
* 需求《本体生成控输出_整合方案与并行开发计划_v3.md》§4.2（冷启动估算与装箱）、
  §4.3（反馈只调整未派发任务）、§5（拆分规则）、§8 D04 行。

职责：对契约 §4.1 形状的 Target 列表做确定性规划——初始计划输入（plan_initial）、
顺序贪心装箱（pack_next）、语义单元二分拆分与受阻判定（split_or_block）、
拆分覆盖守恒校验（verify_split_coverage）、作业定义组装（job_definition）。
只 import 标准库 + batch_contracts + budget；不 import semantic_units（输入按
batch_contracts「目标 / 选择器」节冻结的 Target 形状，测试用 semantic_units 造
真实输入联调）。纯函数、零副作用、零 IO，可被执行器与实验代码单独加载。

装箱规则（冻结，§4.2 + 任务 D04 指令 a–e）：
a) 顺序贪心：目标按 targetId 稳定序、按 subjectKey 聚成语义单元；每放一个单元对
   组装后的整批 messages 重新估算输入 I（estimate_batch_messages 组包 +
   budget.estimate_request 的 utf8_proxy 口径，estimate_fn 可注入）；
b) 硬约束：I + L + inputReserve ≤ C；ΣE(unit) ≤ T=floor(L×ratio)；primary ≤ 64；
c) 软目标：整批可见输出估算 V=cold_output_estimate(整批slots, 整批I) ≤ 3000；
   加入新单元会超软目标时先缩组（收当前批、新单元另起新批）；单单元自身超软但
   硬预算可容纳 → 允许单独派发并记入 softTargetExceeded；
d) 单个语义单元连硬预算都放不下 → 其 targetId 进 infeasible（交 split_or_block
   决定拆分或受阻），不静默丢弃；
e) 反馈收缩：calibration 提供某单元 kind 的校准估算时 E 用 budget.expected_output
   （取冷启动与校准较大者）；组装候选批时以候选单元的 E 调 budget.shrink_for_feedback
   计算建议批量，建议数小于候选批单元数即退回更小批次——只缩不扩。

拆分规则（冻结，§5）：
* depth ≥ MAX_SPLIT_DEPTH(8) → blocked SPLIT_DEPTH_EXCEEDED（不再细分）；
* 二分优先按语义单元（subjectKey 组边界）；单元内部按目标稳定序对半二分
  （属性组/关系组的确定性近似）；
* 只剩一条事实且无可靠字段/段落范围 → blocked OVERSIZED_ATOMIC_TARGET
  （提示补充材料/缩小任务，不伪造范围）；
* 事实 data 为含 ≥2 个字符串键的 dict 时可构造 field selector 子目标
  （{'type':'field','field':…}，按键的稳定序对半二分）；拆分映射
  （父targetId→子 selector 列表）随结果 splitPath 返回，并同时在每个子作业
  定义上带 splitSelectors（本子作业认领的份额）；field 键集合来自真实 data，
  构造期即保证不漏（verify 侧按 selector 验证不重，见下）。

稳定 ID：一律复用 contracts.stable_job_id（planEpoch + 父作业 + splitPath +
目标 digest 集）。splitPath 逐层 'root'/'L'/'R'（单元/目标二分）或
'root'/'f<field>'（字段二分取该子作业首字段）。field 子目标的 targetId 按
semantic_units 冻结公式 't-' + sha256(factId + canonical_selector(selector))[:16]
本地派生（契约口径，非实现依赖）。

覆盖守恒：verify_split_coverage 按 target_digest 校验子目标并集==父目标、
不重不漏；children 的 contextFactIds 允许重叠、不参与比对；selector 拆分按
children 的 splitSelectors 映射按 selector 验证（不重、无 whole/field 混认领；
完整性由 split_or_block 构造期按真实 data 键集合保证）。

job_definition 输出可直接作为 batch_state 事件体：加 {'type':'job_created'} 即
job_created 事件；作为 'job_split' 事件的 children 列表项亦逐字段可受理
（'primaryTargets' 为本模块附加字段，batch_state 只读冻结字段、原样忽略）。

协议变更请求（已上报协调者 C，待并入 batch_contracts/接口文档 08 后生效）：
* field selector 拆分的子目标 targetId 是新派生 id，batch_state 的 job_split
  校验要求子作业主目标全部存在于计划 targets 表且与父目标严格不重不漏——
  现协议没有"向 targets 表登记 selector 子目标"的事件；D09 接线前 field 拆分
  只能到映射层（splitPath/splitSelectors），真正以新目标入状态机需扩协议；
  单元/目标二分拆分不受影响（子目标即父目标子集）。
"""
import copy
import hashlib
import json

from workbench.ontology_build import batch_contracts as contracts
from workbench.ontology_build import budget

__all__ = [
    'SOFT_PACKING_TARGET_TOKENS', 'MAX_PRIMARY_TARGETS_PER_JOB', 'MAX_SPLIT_DEPTH',
    'MAX_PLAN_TARGETS', 'CONTEXT_MAX_FACTS', 'INITIAL_MAX_UNITS',
    'TARGET_BUDGET_EXCEEDED', 'OVERSIZED_ATOMIC_TARGET', 'SPLIT_DEPTH_EXCEEDED',
    'sorted_targets', 'semantic_units_of', 'targets_with_content',
    'estimate_batch_messages', 'context_fact_ids_for', 'job_definition',
    'plan_initial', 'pack_next', 'split_or_block', 'verify_split_coverage',
]

# --- 契约常量（单一事实来源 batch_contracts，原样再导出便于调用方单点引用） -----------

SOFT_PACKING_TARGET_TOKENS = contracts.SOFT_PACKING_TARGET_TOKENS
MAX_PRIMARY_TARGETS_PER_JOB = contracts.MAX_PRIMARY_TARGETS_PER_JOB
MAX_SPLIT_DEPTH = contracts.MAX_SPLIT_DEPTH
MAX_PLAN_TARGETS = contracts.MAX_PLAN_TARGETS
TARGET_BUDGET_EXCEEDED = contracts.TARGET_BUDGET_EXCEEDED
OVERSIZED_ATOMIC_TARGET = contracts.OVERSIZED_ATOMIC_TARGET
SPLIT_DEPTH_EXCEEDED = contracts.SPLIT_DEPTH_EXCEEDED

CONTEXT_MAX_FACTS = 8            # 契约 §4.1 context 上限（semantic_units.context_fact_ids 冻结默认）
INITIAL_MAX_UNITS = 2            # §4.2 冷启动先试最多 2 个完整语义单元

_ESTIMATE_SYSTEM_HEADER = ('本体生成控输出 v3 估算消息：只为本次主目标生成定义，'
                           '背景对象只作引用（utf8_proxy 估算组包，不直接发送）')
_TARGET_ID_PREFIX = 't-'
_TARGET_ID_CHARS = 16
_GUARD_REASON = '预算 profile 未启用或缺 contextTokens/outputCap/targetBudget/inputReserve，无法装箱'


# --- 基础规整（稳定序是全部决定可复现的前提） -----------------------------------------


def _int_like(value, minimum=0):
    """整数化守卫：bool/非数值/非整浮点/低于下限 → None（绝不猜 0）。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= minimum else None
    if isinstance(value, float) and float(value).is_integer():
        number = int(value)
        return number if number >= minimum else None
    return None


def _plan_epoch(value):
    number = _int_like(value, 0)
    return 1 if number is None else number


def sorted_targets(targets):
    """Target 列表 → 按 targetId 升序的稳定序（同 id 去重保首）；缺关键 id 抛 ValueError。"""
    cleaned = []
    seen = set()
    for position, target in enumerate(targets or []):
        if not isinstance(target, dict):
            raise ValueError('target[%d] 不是 dict：拒绝静默丢弃目标' % position)
        target_id = str(target.get('targetId') or '').strip()
        fact_id = str(target.get('factId') or '').strip()
        if not target_id or not fact_id:
            raise ValueError('target[%d] 缺 targetId/factId：拒绝静默丢弃目标' % position)
        if target_id in seen:
            continue
        seen.add(target_id)
        cleaned.append(target)
    return sorted(cleaned, key=lambda item: str(item.get('targetId')))


def semantic_units_of(ordered_targets):
    """稳定序目标 → 语义单元列表（按 subjectKey 分组；组序=首成员位置，成员保持稳定序）。"""
    units = []
    index = {}
    for target in ordered_targets:
        key = str(target.get('subjectKey') or '')
        unit = index.get(key)
        if unit is None:
            unit = []
            index[key] = unit
            units.append(unit)
        unit.append(target)
    return units


# --- 估算组包（§4.2 utf8_proxy 的等价输入；与 output_codec 同构但零依赖） --------------


def _target_content(target, facts_by_id):
    """target → 其按 selector 切片后的内容视图（估算须反映真实载荷大小）。"""
    fact = facts_by_id.get(str(target.get('factId') or ''))
    fact = fact if isinstance(fact, dict) else {}
    selector = target.get('selector')
    selector = selector if isinstance(selector, dict) else {}
    kind = str(selector.get('type') or 'whole')
    data = fact.get('data')
    snippet = fact.get('snippet')
    content = {'factKind': fact.get('kind'), 'locator': fact.get('locator')}
    if kind == 'field':
        field = str(selector.get('field') or '')
        content['field'] = field
        content['data'] = data.get(field) if isinstance(data, dict) and field in data else None
        content['snippet'] = None
    elif kind == 'range':
        start = _int_like(selector.get('start'), 0) or 0
        end = _int_like(selector.get('end'), 0) or 0
        content['snippet'] = snippet[start:end] if isinstance(snippet, str) else None
        content['data'] = None
    else:
        content['data'] = data
        content['snippet'] = snippet
    return content


def targets_with_content(targets, facts_by_id):
    """Target 列表 → 附加 'content' 内容视图的副本（估算输入；不改传入对象）。"""
    merged = []
    for target in targets or []:
        item = dict(target)
        item['content'] = _target_content(target, facts_by_id)
        merged.append(item)
    return merged


def _context_contents(context_fact_ids, facts_by_id):
    contents = []
    for fact_id in context_fact_ids or []:
        fact = facts_by_id.get(str(fact_id))
        fact = fact if isinstance(fact, dict) else {}
        contents.append({'factId': str(fact_id), 'factKind': fact.get('kind'),
                         'locator': fact.get('locator'), 'snippet': fact.get('snippet'),
                         'data': fact.get('data'), 'contextRole': 'background'})
    return contents


def estimate_batch_messages(job_targets, context_facts, scope_payload):
    """组装整批估算 messages（§4.2：I = 完整序列化字节数 + 32×消息条数）。

    job_targets：本叶 job 主目标（契约 Target，宜先过 targets_with_content 附内容）；
    context_facts：背景事实内容 dict 列表（只供理解，不算覆盖）；scope_payload：
    范围说明（JSON 可序列化，可为 None）。主目标与背景分开成两个数组，对应
    §4.1「只为本次主目标生成定义，背景对象只作引用」。纯函数、确定性输出。
    """
    primary = []
    for target in job_targets or []:
        content = target.get('content')
        primary.append({
            'targetId': str(target.get('targetId') or ''),
            'factId': str(target.get('factId') or ''),
            'selector': copy.deepcopy(target.get('selector')) if isinstance(target.get('selector'),
                                                                            dict) else {'type': 'whole'},
            'kind': str(target.get('kind') or ''),
            'subjectKey': str(target.get('subjectKey') or ''),
            'content': copy.deepcopy(content) if isinstance(content, dict) else None,
        })
    payload = {
        'primaryTargets': primary,
        'contextFacts': [copy.deepcopy(item) for item in (context_facts or [])
                         if isinstance(item, dict)],
        'scope': scope_payload,
    }
    return [
        {'role': 'system', 'content': _ESTIMATE_SYSTEM_HEADER},
        {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                               default=str)},
    ]


def _input_tokens(messages, estimate_fn):
    result = estimate_fn(messages)
    value = result.get('inputTokens') if isinstance(result, dict) else result
    number = _int_like(value, 0)
    if number is None:
        raise ValueError('estimate_fn 返回缺少非负整数 inputTokens：%r' % (result,))
    return number


def _limits(profile):
    """BudgetProfile → (C, L, T, inputReserve)；缺任一冻结值 → None（无法核验即不可派发）。"""
    profile = profile if isinstance(profile, dict) else {}
    effective = profile.get('effective') if isinstance(profile.get('effective'), dict) else {}
    context = _int_like(profile.get('contextTokens'), 1)
    cap = _int_like(effective.get('outputCap'), 1)
    target = _int_like(effective.get('targetBudget'), 1)
    reserve = _int_like(effective.get('inputReserve'), 0)
    if context is None or cap is None or target is None or reserve is None:
        return None
    return context, cap, target, reserve


def _hard_fit(input_tokens, expected_output, primary_count, context_cap, output_cap,
              target_budget, reserve):
    """规则 b 的硬约束：I+L+reserve≤C、ΣE≤T、primary≤64。返回 (ok, 违反维度)。"""
    if input_tokens + output_cap + reserve > context_cap:
        return False, 'input'
    if expected_output > target_budget:
        return False, 'output'
    if primary_count > MAX_PRIMARY_TARGETS_PER_JOB:
        return False, 'primary'
    return True, None


def context_fact_ids_for(primary_targets, context_pool, max_context=CONTEXT_MAX_FACTS):
    """契约 §4.1 context 语义的装箱侧实现：与主目标同 subjectKey 的邻近事实 id。

    context_pool 为候选全量 targets（内部先稳定排序）；排除主目标自身（按 targetId
    与 factId 双重排除）、按 factId 去重、上限 max_context（默认 8）。主目标已覆盖
    整个主体时返回空；结果不算覆盖，仅供理解。
    """
    limit = _int_like(max_context, 0)
    limit = CONTEXT_MAX_FACTS if limit is None else limit
    if limit <= 0:
        return []
    primary_ids = set()
    primary_facts = set()
    primary_keys = set()
    for target in primary_targets or []:
        primary_ids.add(str(target.get('targetId') or ''))
        primary_facts.add(str(target.get('factId') or ''))
        primary_keys.add(str(target.get('subjectKey') or ''))
    selected = []
    seen = set()
    for target in sorted_targets(context_pool or []):
        if len(selected) >= limit:
            break
        if str(target.get('targetId') or '') in primary_ids:
            continue
        if str(target.get('subjectKey') or '') not in primary_keys:
            continue
        fact_id = str(target.get('factId') or '')
        if not fact_id or fact_id in primary_facts or fact_id in seen:
            continue
        seen.add(fact_id)
        selected.append(fact_id)
    return selected


# --- 作业定义（可直接喂 batch_state 'job_created' / 'job_split' 事件） -----------------


def job_definition(job_id, primary_targets, context_fact_ids, estimate, parent_id=None,
                   root_id=None, split_path='root'):
    """组装作业定义 dict（冻结字段 + 本模块附加 'primaryTargets'）。

    加 {'type': 'job_created'} 即 batch_state.job_created 事件体；作为 job_split
    事件的 children 项亦逐字段可受理（batch_state 以父作业 rootId 为准，这里的
    rootId 仅为展示一致性）。estimate 为 JSON 安全 dict（slots/inputTokens/
    expectedOutput/visibleOutput/softTargetExceeded）。纯函数。
    """
    targets = []
    ids = []
    for target in primary_targets or []:
        if not isinstance(target, dict):
            raise ValueError('作业 %s 的主目标不是 dict' % (job_id,))
        target_id = str(target.get('targetId') or '').strip()
        if not target_id:
            raise ValueError('作业 %s 的主目标缺 targetId' % (job_id,))
        ids.append(target_id)
        targets.append(copy.deepcopy(target))
    if not ids:
        raise ValueError('作业 %s 至少需要一个主目标' % (job_id,))
    if len(ids) > MAX_PRIMARY_TARGETS_PER_JOB:
        raise ValueError('作业 %s 主目标 %d 个超过单作业上限 %d'
                         % (job_id, len(ids), MAX_PRIMARY_TARGETS_PER_JOB))
    return {
        'jobId': str(job_id),
        'parentId': str(parent_id or ''),
        'rootId': str(root_id or job_id),
        'orderedPrimaryTargetIds': ids,
        'primaryTargets': targets,
        'contextFactIds': [str(item) for item in (context_fact_ids or [])],
        'estimate': copy.deepcopy(estimate) if isinstance(estimate, dict) else None,
        'splitPath': str(split_path or 'root'),
    }


def _build_job(specs, ctx_ids, estimate, plan_epoch, parent_id, split_path, root_id=None):
    targets = [target for spec in specs for target in spec['targets']]
    job_id = contracts.stable_job_id(plan_epoch, parent_id, split_path, targets)
    return job_definition(job_id, targets, ctx_ids, estimate, parent_id=parent_id,
                          root_id=root_id, split_path=split_path)


def _unit_estimate(unit_targets, ctx_ids, facts, calibrated_of, est_fn, scope_payload):
    """单语义单元估算：slots、自身输入 I、E=max(冷启动, 校准)（规则 e）。"""
    slots = budget.unit_slots(unit_targets)
    messages = estimate_batch_messages(targets_with_content(unit_targets, facts),
                                       _context_contents(ctx_ids, facts), scope_payload)
    tokens = _input_tokens(messages, est_fn)
    kind = str(unit_targets[0].get('kind') or '') if unit_targets else ''
    calibrated = calibrated_of(kind) if calibrated_of is not None else None
    expected = budget.expected_output({'slots': slots, 'inputTokens': tokens}, calibrated)
    return {'slots': int(slots), 'inputTokens': tokens, 'expectedOutput': int(expected),
            'visibleOutput': None, 'softTargetExceeded': False}


def _empty_plan(reason):
    return {'jobs': [], 'softTargetExceeded': [], 'infeasible': [], 'reason': reason}


# --- 初始计划输入（§4.2：冷启动先试最多 2 个完整语义单元一个 job） ---------------------


def plan_initial(targets, facts_by_id, profile, estimate_inputs=None):
    """全量目标 → 初始计划输入：前最多 INITIAL_MAX_UNITS(2) 个完整语义单元一个 job。

    estimate_inputs（dict，均可选）：'scopePayload'（范围说明）、'calibration'
    （{unitKind: 校准估算数值或 (buckets,key) 元组}）、'estimateFn'（注入估算函数，
    默认 budget.estimate_request）、'planEpoch'（稳定 jobId 派生代，默认 1）。

    返回：{'ok', 'code', 'message', 'initialJob', 'jobs', 'pendingTargetIds',
    'infeasible', 'softTargetExceeded', 'reason'}。
    * len(targets) > MAX_PLAN_TARGETS(4096) → ok=False + code=TARGET_BUDGET_EXCEEDED
      （明确不可规划原因，全部目标保持 pending，不做任何装箱）；
    * profile 缺冻结值 → ok=False（无法核验即不可派发，全部 pending）；
    * 首个语义单元连硬预算都放不下 → 其目标进 infeasible（交 split_or_block），
      不向前跳单元（顺序保守）；第二个单元放不下 → 保持首单元，其余 pending；
    * 冷启动同样遵守软目标与反馈收缩（只缩不扩）。
    targets 为空抛 ValueError（与 batch_state.create_plan 同口径）。纯函数。
    """
    inputs = estimate_inputs if isinstance(estimate_inputs, dict) else {}
    ordered = sorted_targets(targets)
    if not ordered:
        raise ValueError('targets 为空：计划至少需要一个目标')
    all_ids = [target['targetId'] for target in ordered]

    def result(ok, code, message, initial_job, pending, infeasible, soft, reason):
        return {'ok': ok, 'code': code, 'message': message, 'initialJob': initial_job,
                'jobs': [initial_job] if initial_job else [], 'pendingTargetIds': pending,
                'infeasible': infeasible, 'softTargetExceeded': soft, 'reason': reason}

    if len(ordered) > MAX_PLAN_TARGETS:
        reason = '%s：目标 %d 个超过单计划上限 %d，拒绝规划' % (
            TARGET_BUDGET_EXCEEDED, len(ordered), MAX_PLAN_TARGETS)
        return result(False, TARGET_BUDGET_EXCEEDED, reason, None, all_ids, [], [], reason)
    limits = _limits(profile)
    if limits is None:
        return result(False, None, _GUARD_REASON, None, all_ids, [], [], _GUARD_REASON)
    _context_cap, output_cap, target_budget, _reserve = limits
    est_fn = inputs.get('estimateFn')
    est_fn = budget.estimate_request if est_fn is None else est_fn
    facts = facts_by_id if isinstance(facts_by_id, dict) else {}
    buckets = inputs.get('calibration')
    buckets = buckets if isinstance(buckets, dict) else None
    scope = inputs.get('scopePayload')
    epoch = _plan_epoch(inputs.get('planEpoch'))
    units = semantic_units_of(ordered)

    def calibrated_of(kind):
        return buckets.get(kind) if buckets is not None else None

    chosen = []
    infeasible = []
    for unit in units[:INITIAL_MAX_UNITS]:
        ctx = context_fact_ids_for(unit, ordered)
        spec = {'targets': unit,
                'estimate': _unit_estimate(unit, ctx, facts, calibrated_of, est_fn, scope)}
        candidate = chosen + [spec]
        cand_targets = [target for item in candidate for target in item['targets']]
        cand_tokens = _input_tokens(
            estimate_batch_messages(targets_with_content(cand_targets, facts),
                                    _context_contents(context_fact_ids_for(cand_targets, ordered),
                                                      facts), scope), est_fn)
        cand_expected = sum(int(item['estimate']['expectedOutput']) for item in candidate)
        fits, _why = _hard_fit(cand_tokens, cand_expected, len(cand_targets), *limits)
        if not fits:
            if not chosen:
                infeasible = [target['targetId'] for target in unit]
            break                      # 冷启动只缩不扩：保持已选，其余留待后续批次
        visible = budget.cold_output_estimate(budget.unit_slots(cand_targets), cand_tokens)
        if visible > SOFT_PACKING_TARGET_TOKENS and chosen:
            break                      # 软目标先缩组
        calibrated = calibrated_of(str(unit[0].get('kind') or ''))
        if calibrated is not None:
            cap = budget.shrink_for_feedback(len(candidate),
                                             int(spec['estimate']['expectedOutput']),
                                             target_budget)
            if cap < len(candidate):
                break                  # 反馈收缩：建议批量小于候选批 → 不并入
        chosen.append(spec)

    chosen_ids = {target['targetId'] for spec in chosen for target in spec['targets']}
    pending = [target_id for target_id in all_ids if target_id not in chosen_ids]
    initial_job = None
    soft = []
    if chosen:
        chosen_targets = [target for spec in chosen for target in spec['targets']]
        ctx = context_fact_ids_for(chosen_targets, ordered)
        tokens = _input_tokens(
            estimate_batch_messages(targets_with_content(chosen_targets, facts),
                                    _context_contents(ctx, facts), scope), est_fn)
        slots = budget.unit_slots(chosen_targets)
        expected = sum(int(spec['estimate']['expectedOutput']) for spec in chosen)
        visible = budget.cold_output_estimate(slots, tokens)
        estimate = {'slots': int(slots), 'inputTokens': tokens, 'expectedOutput': expected,
                    'visibleOutput': visible,
                    'softTargetExceeded': visible > SOFT_PACKING_TARGET_TOKENS}
        initial_job = _build_job(chosen, ctx, estimate, epoch, None, 'root')
        if estimate['softTargetExceeded']:
            soft.append(initial_job['jobId'])
    reason = '初始作业 %d 个语义单元 / %d 个目标；待处理 %d 个目标；不可容纳 %d 个目标' % (
        len(chosen), len(chosen_ids), len(pending), len(infeasible))
    return result(True, None, reason, initial_job, pending, infeasible, soft, reason)


# --- 顺序贪心装箱（§4.2 / §4.3；规则 a–e） --------------------------------------------


def pack_next(pending_targets, context_targets, facts_by_id, profile, calibration=None,
              estimate_fn=None, plan_epoch=1, scope_payload=None):
    """未建 job 的 pending 目标 → 下一批作业定义列表（已建 job 不可重排，§4.3）。

    pending_targets：待装箱目标（内部按 targetId 稳定排序、按 subjectKey 聚单元）；
    context_targets：上下文候选池（典型为全量计划目标）；facts_by_id：{factId: fact}；
    profile：BudgetProfile（缺冻结值 → 不装箱，reason 说明）；calibration：
    {unitKind: 校准估算}（规则 e，缺省 None=纯冷启动）；estimate_fn：注入估算
    （默认 budget.estimate_request）；plan_epoch：稳定 jobId 派生代。

    返回 {'jobs': [job定义...], 'softTargetExceeded': [jobId...],
          'infeasible': [targetId...], 'reason': str}。
    作业顺序、划分与 jobId 对同一输入集合与输入顺序无关（乱序稳定）。纯函数。
    """
    limits = _limits(profile)
    if limits is None:
        return _empty_plan(_GUARD_REASON)
    context_cap, output_cap, target_budget, reserve = limits
    est_fn = estimate_fn if estimate_fn is not None else budget.estimate_request
    facts = facts_by_id if isinstance(facts_by_id, dict) else {}
    buckets = calibration if isinstance(calibration, dict) else None
    ordered = sorted_targets(pending_targets)
    pool = sorted_targets(context_targets) if context_targets else []
    units = semantic_units_of(ordered)
    epoch = _plan_epoch(plan_epoch)

    def calibrated_of(kind):
        return buckets.get(kind) if buckets is not None else None

    def measure(specs):
        targets = [target for spec in specs for target in spec['targets']]
        ctx_ids = context_fact_ids_for(targets, pool)
        messages = estimate_batch_messages(targets_with_content(targets, facts),
                                           _context_contents(ctx_ids, facts), scope_payload)
        return targets, ctx_ids, _input_tokens(messages, est_fn)

    jobs = []
    soft_exceeded = []
    infeasible = []
    buffer = []

    def flush():
        nonlocal buffer
        if not buffer:
            return
        targets, ctx_ids, tokens = measure(buffer)
        slots = budget.unit_slots(targets)
        expected = sum(int(spec['estimate']['expectedOutput']) for spec in buffer)
        visible = budget.cold_output_estimate(slots, tokens)
        estimate = {'slots': int(slots), 'inputTokens': tokens, 'expectedOutput': expected,
                    'visibleOutput': visible,
                    'softTargetExceeded': visible > SOFT_PACKING_TARGET_TOKENS}
        job = _build_job(buffer, ctx_ids, estimate, epoch, None, 'root')
        if estimate['softTargetExceeded']:
            soft_exceeded.append(job['jobId'])
        jobs.append(job)
        buffer = []

    for unit in units:
        unit_ctx = context_fact_ids_for(unit, pool)
        spec = {'targets': unit,
                'estimate': _unit_estimate(unit, unit_ctx, facts, calibrated_of, est_fn,
                                           scope_payload)}
        unit_estimate = spec['estimate']
        candidate = buffer + [spec]
        cand_targets, _cand_ctx, cand_tokens = measure(candidate)
        cand_expected = sum(int(item['estimate']['expectedOutput']) for item in candidate)
        fits, _why = _hard_fit(cand_tokens, cand_expected, len(cand_targets), context_cap,
                               output_cap, target_budget, reserve)
        if fits:
            calibrated = calibrated_of(str(unit[0].get('kind') or ''))
            if calibrated is not None:
                cap = budget.shrink_for_feedback(len(candidate),
                                                 int(unit_estimate['expectedOutput']),
                                                 target_budget)
                if cap < len(candidate):
                    fits = False        # 规则 e：反馈只缩不扩，本批退回更小批次
        if not fits:
            if buffer:
                flush()
            alone_fits, _why = _hard_fit(int(unit_estimate['inputTokens']),
                                         int(unit_estimate['expectedOutput']), len(unit),
                                         context_cap, output_cap, target_budget, reserve)
            buffer = [spec] if alone_fits else []
            if not alone_fits:
                infeasible.extend(target['targetId'] for target in unit)   # 规则 d
            continue
        visible = budget.cold_output_estimate(budget.unit_slots(cand_targets), cand_tokens)
        if visible > SOFT_PACKING_TARGET_TOKENS and buffer:      # 规则 c：先缩组
            flush()
            buffer = [spec]
            continue
        buffer.append(spec)
    flush()

    packed = sum(len(job['orderedPrimaryTargetIds']) for job in jobs)
    reason = ('装箱 %d 个作业（%d 个目标）；软目标超限作业 %d 个；不可容纳目标 %d 个'
              '（交 split_or_block 决定拆分或受阻）'
              % (len(jobs), packed, len(soft_exceeded), len(infeasible)))
    return {'jobs': jobs, 'softTargetExceeded': soft_exceeded, 'infeasible': infeasible,
            'reason': reason}


# --- 拆分与受阻（§5） -----------------------------------------------------------------


def _field_target_id(fact_id, selector):
    """契约冻结 targetId 公式（semantic_units docstring 同源）：t- + sha256(factId +
    canonical_selector)[:16]。field selector 子目标的派生口径，本地实现以避免对
    semantic_units 的实现依赖。"""
    payload = str(fact_id) + contracts.canonical_selector(selector)
    return _TARGET_ID_PREFIX + hashlib.sha256(payload.encode('utf-8')).hexdigest()[
        :_TARGET_ID_CHARS]


def _splittable_fields(target, facts):
    """事实 data 为含 ≥2 个字符串键的 dict → 稳定序键列表；否则 []（不伪造范围）。"""
    fact = facts.get(str(target.get('factId') or ''))
    fact = fact if isinstance(fact, dict) else {}
    data = fact.get('data')
    if not isinstance(data, dict):
        return []
    fields = sorted(str(key) for key in data if isinstance(key, str))
    return fields if len(fields) >= 2 else []


def _sibling_context(group, other):
    """二分拆分子作业的上下文：对侧事实 id（主体摘要作 context，§5），上限 8。"""
    own = {str(target.get('factId') or '') for target in group}
    selected = []
    seen = set()
    for target in other:
        fact_id = str(target.get('factId') or '')
        if not fact_id or fact_id in own or fact_id in seen:
            continue
        seen.add(fact_id)
        selected.append(fact_id)
        if len(selected) >= CONTEXT_MAX_FACTS:
            break
    return selected


def _child_estimate(targets, ctx_ids, facts):
    slots = budget.unit_slots(targets)
    messages = estimate_batch_messages(targets_with_content(targets, facts),
                                       _context_contents(ctx_ids, facts), None)
    tokens = _input_tokens(messages, budget.estimate_request)
    expected = budget.expected_output({'slots': slots, 'inputTokens': tokens}, None)
    return {'slots': int(slots), 'inputTokens': tokens, 'expectedOutput': int(expected),
            'visibleOutput': None, 'softTargetExceeded': False}


def _binary_split(left, right, facts, plan_epoch, parent_job_id, parent_path, root_id, depth):
    """单元级/目标级二分：两个子作业定义 + 父targetId→whole selector 映射。"""
    children = []
    mapping = {}
    for tag, group, other in (('L', left, right), ('R', right, left)):
        path = '%s/%s' % (parent_path, tag)
        ctx = _sibling_context(group, other)
        estimate = _child_estimate(group, ctx, facts)
        job_id = contracts.stable_job_id(plan_epoch, parent_job_id, path, group)
        child = job_definition(job_id, group, ctx, estimate, parent_id=parent_job_id,
                               root_id=root_id, split_path=path)
        children.append(child)
        for target in group:
            mapping[target['targetId']] = [dict(target.get('selector')
                                                if isinstance(target.get('selector'), dict)
                                                else {'type': 'whole'})]
    return {'action': 'split', 'children': children, 'splitPath': mapping,
            'parentSplitPath': parent_path, 'splitDepth': depth + 1,
            'message': '二分拆分：左 %d 个目标 / 右 %d 个目标（splitPath %s/L、%s/R）'
                       % (len(left), len(right), parent_path, parent_path)}


def _field_split(target, fields, facts, plan_epoch, parent_job_id, parent_path, root_id, depth):
    """字段二分：父目标按 data 真实键集合对半；子目标为 field selector 新目标。"""
    fact_id = str(target.get('factId') or '')
    half = (len(fields) + 1) // 2
    children = []
    mapping = {str(target.get('targetId') or ''): []}
    for _tag, group in (('L', fields[:half]), ('R', fields[half:])):
        selectors = [{'type': 'field', 'field': str(field)} for field in group]
        sub_targets = []
        for selector in selectors:
            sub_targets.append({
                'targetId': _field_target_id(fact_id, selector),
                'factId': fact_id,
                'selector': dict(selector),
                'kind': target.get('kind'),
                'subjectKey': target.get('subjectKey'),
                'materialId': target.get('materialId'),
                'groupingConfidence': target.get('groupingConfidence'),
            })
        path = '%s/f%s' % (parent_path, str(group[0]))
        estimate = _child_estimate(sub_targets, [], facts)
        job_id = contracts.stable_job_id(plan_epoch, parent_job_id, path, sub_targets)
        child = job_definition(job_id, sub_targets, [], estimate, parent_id=parent_job_id,
                               root_id=root_id, split_path=path)
        # 本子作业对父目标的 selector 认领份额（覆盖守恒按 selector 验证的依据）
        child['splitSelectors'] = {str(target.get('targetId') or ''):
                                   [dict(selector) for selector in selectors]}
        mapping[str(target.get('targetId') or '')].extend(
            copy.deepcopy(child['splitSelectors'][str(target.get('targetId') or '')]))
        children.append(child)
    return {'action': 'split', 'children': children, 'splitPath': mapping,
            'parentSplitPath': parent_path, 'splitDepth': depth + 1,
            'message': '字段二分拆分：事实 %s 按 data 键集合 %s 对半（不伪造范围）'
                       % (fact_id, ','.join(fields))}


def split_or_block(job_targets, facts_by_id, depth, profile=None, plan_epoch=1,
                   parent_job_id=None, parent_split_path='root', root_job_id=None):
    """不可容纳的作业主目标 → 拆分为两个子作业定义，或明确受阻。

    depth：当前作业深度（根为 0）；depth ≥ MAX_SPLIT_DEPTH(8) → blocked
    SPLIT_DEPTH_EXCEEDED。二分优先按语义单元（subjectKey 组边界），单元内部按
    目标稳定序对半；只剩一条事实时：data 为含 ≥2 字符串键的 dict → field selector
    二分（拆分映射随 splitPath 返回、子作业带 splitSelectors 份额），否则 blocked
    OVERSIZED_ATOMIC_TARGET（提示补充材料/缩小任务，不伪造范围）。

    返回 {'action': 'split', 'children': [子作业定义×2], 'splitPath': {父targetId:
    [selector...]}, 'parentSplitPath': str, 'splitDepth': int, 'message': str} 或
    {'action': 'blocked', 'code': OVERSIZED_ATOMIC_TARGET|SPLIT_DEPTH_EXCEEDED,
    'message': str}。子 jobId 用 contracts.stable_job_id(epoch, 父作业, 子路径, 子目标)
    派生——同输入恒同 id。profile 为预留参数（拆分决策只依赖结构，不再核算预算，
    子作业可行性由调用方 pack_next 复核）。纯函数。
    """
    ordered = sorted_targets(job_targets)
    if not ordered:
        raise ValueError('拆分需要非空主目标')
    depth_value = _int_like(depth, 0)
    if depth_value is None:
        raise ValueError('depth 需要非负整数，实际 %r' % (depth,))
    if depth_value >= MAX_SPLIT_DEPTH:
        return {'action': 'blocked', 'code': SPLIT_DEPTH_EXCEEDED,
                'message': '拆分深度 %d 已达上限 %d，不再细分；请核对材料或新建更高预算计划'
                           % (depth_value, MAX_SPLIT_DEPTH)}
    facts = facts_by_id if isinstance(facts_by_id, dict) else {}
    epoch = _plan_epoch(plan_epoch)
    parent_path = str(parent_split_path or 'root')
    parent_id = str(parent_job_id or '') or None
    root_id = str(root_job_id or '') or parent_id
    units = semantic_units_of(ordered)
    if len(units) >= 2:
        half = (len(units) + 1) // 2
        left = [target for unit in units[:half] for target in unit]
        right = [target for unit in units[half:] for target in unit]
        return _binary_split(left, right, facts, epoch, parent_id, parent_path, root_id,
                             depth_value)
    if len(ordered) >= 2:
        half = (len(ordered) + 1) // 2
        return _binary_split(ordered[:half], ordered[half:], facts, epoch, parent_id,
                             parent_path, root_id, depth_value)
    target = ordered[0]
    fields = _splittable_fields(target, facts)
    if not fields:
        return {'action': 'blocked', 'code': OVERSIZED_ATOMIC_TARGET,
                'message': '目标 %s（事实 %s）只有一条事实且无可靠字段/段落范围，无法构造'
                           '不伪造范围的子目标；请补充完整材料或缩小任务后重试'
                           % (target['targetId'], target['factId'])}
    return _field_split(target, fields, facts, epoch, parent_id, parent_path, root_id,
                        depth_value)


# --- 覆盖守恒（契约 §5：子目标不重不漏覆盖父目标；context 允许重叠） -------------------


def verify_split_coverage(parent_targets, children):
    """(ok, problems)：子目标并集==父目标（按 target_digest），不重不漏。

    children 为拆分结果/作业定义列表，每项须带 'primaryTargets'（目标 dict 列表）；
    'contextFactIds' 允许重叠，不参与比对。plain 覆盖=子目标 digest 与父目标逐一
    对应（同 digest 出现两次即重复）；field/range selector 拆分按 children 的
    'splitSelectors'（父targetId→本子作业认领 selector）验证：认领 selector 两两
    互异、不与 whole 整体认领混用；完整性由 split_or_block 构造期按真实 data 键
    集合保证（本函数无事实内容，不重复猜测完整性）。problems 为可读中文列表。
    """
    parents = sorted_targets(parent_targets) if parent_targets else []
    if not parents:
        return False, ['父目标为空']
    if not isinstance(children, list) or not children:
        return False, ['children 为空（拆分必须产生至少一个子作业）']
    problems = []
    parent_ids = {str(target.get('targetId') or ''): target for target in parents}
    parent_digests = {contracts.target_digest(target) for target in parents}
    # selector 映射索引：子 selector digest → (父targetId, selector)
    selector_index = {}
    for child in children:
        if not isinstance(child, dict):
            continue
        split_selectors = child.get('splitSelectors')
        if not isinstance(split_selectors, dict):
            continue
        for parent_id, selectors in split_selectors.items():
            parent_id = str(parent_id or '')
            parent = parent_ids.get(parent_id)
            if parent is None:
                problems.append('splitSelectors 引用未知父目标：%s' % parent_id)
                continue
            for selector in selectors or []:
                digest = contracts.target_digest({'factId': parent.get('factId'),
                                                  'selector': selector})
                owner = selector_index.get(digest)
                if owner is not None and owner[0] != parent_id:
                    problems.append('同一子 selector 被映射到多个父目标：%s' % digest)
                selector_index[digest] = (parent_id, copy.deepcopy(selector))
    plain_counts = {}
    mapped_claims = []
    for index, child in enumerate(children):
        if not isinstance(child, dict):
            problems.append('children[%d] 不是 dict' % index)
            continue
        primaries = child.get('primaryTargets')
        if not isinstance(primaries, list) or not primaries:
            problems.append('children[%d] 缺 primaryTargets（无法核对覆盖）' % index)
            continue
        for target in primaries:
            if not isinstance(target, dict):
                problems.append('children[%d] 含非 dict 主目标' % index)
                continue
            digest = contracts.target_digest(target)
            if digest in parent_digests:
                plain_counts[digest] = plain_counts.get(digest, 0) + 1
            elif digest in selector_index:
                owner = selector_index[digest]
                mapped_claims.append({'parentId': owner[0], 'selector': copy.deepcopy(owner[1])})
            else:
                problems.append('额外目标 %s（digest %s）不属于父目标集合，也不在拆分映射内'
                                % (target.get('targetId'), digest))
    for parent in parents:
        parent_id = str(parent.get('targetId') or '')
        digest = contracts.target_digest(parent)
        plain = plain_counts.get(digest, 0)
        claims = [claim for claim in mapped_claims if claim['parentId'] == parent_id]
        if plain > 1:
            problems.append('目标 %s 被重复覆盖 %d 次' % (parent_id, plain))
        if plain == 1 and claims:
            problems.append('目标 %s 同时被整体覆盖与 selector 映射认领（混用）' % parent_id)
        if plain == 0 and not claims:
            problems.append('目标 %s 未被任何子作业覆盖（漏）' % parent_id)
        if claims:
            canonical = [contracts.canonical_selector(claim['selector']) for claim in claims]
            if len(set(canonical)) != len(canonical):
                problems.append('目标 %s 的 selector 拆分存在重复认领' % parent_id)
    return (not problems), problems
