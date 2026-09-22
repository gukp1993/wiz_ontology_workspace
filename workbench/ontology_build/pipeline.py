"""从物料构建本体：阶段编排（scan / generate / dialog），供 runner 在后台调用。

事务纪律（执行指令 §5、AGENTS 架构边界 8）：
* 每个数据库写入都是 `with sto.write_tx() as tx: tx.run(body)` 的**短事务**；
  解析、模型调用、文件读取等外部工作一律在事务之外完成，绝不持全局写锁
  （`workbench.locking.LOCK`）、绝不开长事务。
* 每个阶段边界调用 `runner.check_cancelled(conn, run_id, owner_user_id)`（由
  `runner.stage()` 内部完成），取消后晚结果禁止写入。
* **业务内容写点（候选 / 对话消息 / 材料事实与 parse_state，R4-01）一律经
  `runner.content_tx()` 落库**：同一写事务内先核对取消与执行权，再写内容——
  不能等内容写完之后才在下一次 stage/update_run 里检查（那是先污染后拦截）；
  run 行的状态/进度/usage/checkpoint 写仍走带 `lease=` 条件的 update_run。
* 失败抛 `PipelineError`，由 runner 记录到 run.error（可重试），**不吞异常、不假装成功**；
  已完成材料的解析结果与已完成批次的候选已各自提交，失败后仍然保留。

阶段（protocol.SCAN_STAGES / GENERATE_STAGES，中文标签同源）：
    scan     parse（逐材料解析，单材料失败不阻塞其余）→ index（材料覆盖摘要）
    generate retrieve（本地检索范围事实）→ align（同主体证据分组 + 片段去重）→
             abstract（分批调用模型抽取 + 跨批 alignment.align 合并）→
             verify（证据引用/必填字段/类型枚举校验）→ adapt（协议字段映射与
             definitionOrder 预检，**不创建本体**）
    dialog   取最近消息与范围 → llm.scope_turn → 写助手消息（patch 只是建议：本模块
             从不调用 store.put_scope，绝不自动改写人工保存的范围）

边界：
* 候选只写任务侧 `wb_build_candidates`（batch_id = 本次批次），不创建/修改任何本体、
  项目或发布；候选 → 本体的转换由交付事务负责。
* 发给模型的材料内容是范围命中事实的最小载荷（id/定位/片段/结构化字段），不含原始文件；
  密钥只存在于 provider 配置中，不落库、不回传；LLM trace 只落安全摘要（llm.safe_trace）。
* 单次生成发送给模型的事实总量有上限（MAX_MODEL_FACTS，按相关度优先，并显式登记截断
  数量与说明），不做静默截断。
"""

import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError

from workbench.ontology_build import alignment
from workbench.ontology_build import llm
from workbench.ontology_build import materials as material_store
from workbench.ontology_build import protocol
from workbench.ontology_build import retrieval
from workbench.ontology_build import runner
from workbench.ontology_build.parsers import parse_material
from workbench.storage import engine as sto
from workbench.storage import ontology_build as store

DIALOG_STAGE = 'dialog'
DIALOG_STAGE_LABEL = '范围对话'
MAX_MODEL_FACTS = protocol.LLM_BATCH_FACTS * 25   # 单次生成最多发给模型的事实数

# 批次并发的落库等待预算：单批正常 30–90 秒（推理开销），provider timeout 之上留缓冲；
# 超时只标记该批失败（迟到结果丢弃，与 V2-10 迟到丢弃同一原则），不阻塞其余批次。
# 抽取调用的超时**按批大小动态计算**（2026-09-22 实测）：
# 输出规模随事实条数增长，模型耗时随之上升且受 provider 负载波动影响很大——
# 同一批 20 条事实实测 27.7 秒（健康）到 185 秒（负载高）不等，而 provider
# 配置超时（MiniMax 120s / GLM 60s）是为短请求设的，会在波动时误杀整个批次
# （真实复现：3 批全部「无法连接接口」失败）。这里给出**下限**，取
# max(provider 配置超时, 60 + 10×条数)，上限 900 秒；provider 配更长时用更长的。
_EXTRACT_TIMEOUT_BASE_SECONDS = 60
_EXTRACT_TIMEOUT_PER_FACT_SECONDS = 10
_EXTRACT_TIMEOUT_CAP_SECONDS = 900
_BATCH_WAIT_SLACK_SECONDS = 180


def _extract_call_timeout(provider, fact_count):
    """单次抽取调用的超时（秒）：按事实条数给下限，尊重更长的 provider 配置。"""
    try:
        configured = int((provider or {}).get('timeout') or 60)
    except (TypeError, ValueError):
        configured = 60
    needed = _EXTRACT_TIMEOUT_BASE_SECONDS + _EXTRACT_TIMEOUT_PER_FACT_SECONDS * max(0, int(fact_count or 0))
    return min(_EXTRACT_TIMEOUT_CAP_SECONDS, max(configured, needed))


def _batch_wait_timeout(provider, fact_count=0):
    """批次等待预算：拆批后子批串行执行，按 2×单次超时 + slack 估算。"""
    return 2 * _extract_call_timeout(provider, fact_count) + _BATCH_WAIT_SLACK_SECONDS


def _merge_usage(left, right):
    """合并两次调用的 usage（拆批重试后仍需如实累计调用次数与字节量）。"""
    merged = {}
    for key in ('calls', 'promptBytes', 'completionBytes', 'durationMs'):
        total = int((left or {}).get(key) or 0) + int((right or {}).get(key) or 0)
        if total:
            merged[key] = total
    return merged


def _is_transient_llm_error(error):
    """瞬态错误判定：只含**连接类**故障与限流（可重试）。

    真实复现：并发多路大请求时 provider 偶发「无法连接接口/超时」，串行时同一
    模型连通正常（独立探测 1.2 秒）——这类重试即可。

    刻意**不含** HTTP 5xx：服务端 5xx 与业务错误在测试里都是确定性失败桩，
    重试只会浪费时间与调用额度（实测：把 500 当瞬态导致抽取调用数翻倍、既有
    断言失败）。5xx 是否可重试交由上层「重试失败批次」决定。
    """
    text = str(error or '')
    return ('无法连接接口' in text or 'HTTP 429' in text or 'timed out' in text.lower())


def _rate_limited(error):
    """429 限流：额度级限流需要更长退避（实测 1s/3s 扛不住，账号级限流窗口更长）。"""
    return 'HTTP 429' in str(error or '')


def _extract_batch_with_split(provider, scope, batch, depth=0):
    """抽取一批候选；截断自动拆批（≤2 层），瞬态网络/限流错误有限重试。

    真实复现：40 条/批时模型为每条事实产出候选 JSON，输出必然超预算 → 整批失败；
    拆小后单次输出量减半。瞬态错误（连接失败/5xx/429/超时）退避重试 2 次
    （并发场景 provider 偶发拒绝；实测并发 3 批大请求时出现过）。
    两条子批都失败才判该批失败（错误取其一并注明已拆批）。
    """
    result = None
    call_timeout = _extract_call_timeout(provider, len(batch))
    rate_limited_hits = 0
    for attempt in range(3):
        result = llm.extract_candidates(provider, scope, batch, timeout=call_timeout)
        if result.get('ok') or not _is_transient_llm_error(result.get('error')):
            break
        # 429 是账号级 RPM 限流（实测：并发 2 稳定、4+ 开始 429、16 路多数失败）——
        # 单批内重试会继续撞限流窗口，改为**立即返回**让批次调度器降挡重排。
        if _rate_limited(result.get('error')):
            rate_limited_hits += 1
            if rate_limited_hits >= 1:
                return result
        if attempt < 2:
            time.sleep(1 + 2 * attempt)   # 连接类短退避
    if result.get('ok'):
        return result
    error = str(result.get('error') or '')
    if depth >= 2 or len(batch) <= 1 or '截断' not in error:
        return result
    middle = max(1, len(batch) // 2)
    left = _extract_batch_with_split(provider, scope, batch[:middle], depth + 1)
    right = _extract_batch_with_split(provider, scope, batch[middle:], depth + 1)
    usage = _merge_usage(left.get('usage'), right.get('usage'))
    if not left.get('ok') and not right.get('ok'):
        return {'ok': False, 'candidates': [], 'usage': usage,
                'error': '截断后拆批重试仍失败：%s' % (left.get('error') or right.get('error') or '')}
    return {'ok': True,
            'candidates': list(left.get('candidates') or []) + list(right.get('candidates') or []),
            'usage': usage,
            'rejectedRefs': int(left.get('rejectedRefs') or 0) + int(right.get('rejectedRefs') or 0),
            'notes': list(left.get('notes') or []) + list(right.get('notes') or [])}

# 人工排除决定回放的有效窗口（D04）：store.list_batches 默认只取最近 20 批，直接用默认值会
# 把更早批次里的人工否决静默丢掉（保护再次失效）。这里放宽到任务级不设限的实用上限
# （单个任务要超过 1000 个生成批次才可能落到窗口之外）。
_DECISION_HISTORY_BATCH_LIMIT = 1000
DEFINITION_ORDER_NOTE = ('definitionOrder 为候选层预检（对象→属性→链接→规则→动作，'
                         '再按对齐键排序）；正式定义 ID 与最终顺序由交付事务分配。')

_STRUCTURAL_CODES = frozenset({
    'TYPE_INVALID', 'NAME_REQUIRED', 'DEFINITION_REQUIRED', 'DATA_TYPE_INVALID',
    'OBSERVATION_VALUE_TYPE_MISSING', 'OBSERVATION_VALUE_TYPE_INVALID',
    'PROPERTY_OWNER_MISSING', 'LINK_ENDPOINT_MISSING',
    'CARDINALITY_INVALID', 'KEY_DUPLICATE',
})
_FIELDS_BY_TYPE = {
    'property': ('dataType', 'valueType'),
    'link': ('sourceRef', 'targetRef', 'cardinality'),
    'rule': ('content',),
    'action': ('effect',),
}
_TYPE_ORDER = ('object', 'property', 'link', 'rule', 'action')
_USAGE_KEYS = ('calls', 'promptBytes', 'completionBytes', 'durationMs')
# 枚举大小写规范化（模型常写成 timeseries/supported 等；只做大小写规范化，不新增取值）
_DATA_TYPES = {value.casefold(): value for value in protocol.PROPERTY_DATA_TYPES}
_VALUE_TYPES = {value.casefold(): value for value in protocol.VALUE_TYPES}
# V2-3 解析三级分派：这些 kind 有专用解析器（第①级）；其余（other）先走 LLM 兜底（第②级），
# 兜底不可用/失败/超限再降级文本线索（第③级）。zip 在上传展开期已拆分，不会进入扫描解析。
# 结构化格式（2026-09-22，需求《结构化格式解析支持_v1》§3/S6）同属第①级：进池内并行解析，
# 不走 _parse_with_dispatch 的 LLM 兜底/文本线索降级（坏文件由其解析器显式失败）。
DEDICATED_KINDS = frozenset({'code', 'ddl', 'docx', 'pdf', 'xlsx', 'md', 'image',
                             'json', 'yaml', 'properties', 'csv', 'ini', 'toml'})


class PipelineError(Exception):
    """管线失败（runner 捕获后写入 run.error + retryable；文案中文可读）。"""


# --- 事务与通用工具 ---------------------------------------------------------------

def _tx(body):
    """短写事务（不持全局写锁）；返回 body(conn) 的结果。"""
    with sto.write_tx() as tx:
        return tx.run(body)


def _usage(**overrides):
    value = {key: 0 for key in _USAGE_KEYS}
    value.update(overrides)
    return value


def _add_usage(total, part):
    part = part if isinstance(part, dict) else {}
    for key in _USAGE_KEYS:
        try:
            total[key] = int(total.get(key) or 0) + int(part.get(key) or 0)
        except (TypeError, ValueError):
            continue
    return total


def _note(notes, text):
    text = str(text or '').strip()
    if text and text not in notes:
        notes.append(text)
    return notes


def _text(value):
    return str(value if value is not None else '').strip()


def _dedupe_keys(items):
    """批内候选 key 唯一化（模型键仅批内使用；空键/重复键自动编号）。"""
    used, out = set(), []
    for index, item in enumerate(items, start=1):
        item = dict(item)
        key = _text(item.get('key'))
        if not key or key in used:
            key = 'c%d' % index
            while key in used:
                key = 'c%d_%d' % (index, len(used))
        used.add(key)
        item['key'] = key
        out.append(item)
    return out


def _fingerprint(provider):
    return protocol.provider_fingerprint(provider if isinstance(provider, dict) else {})


def _counts(items, key):
    out = {}
    for item in items:
        value = _text(item.get(key)) or 'unknown'
        out[value] = out.get(value, 0) + 1
    return out


# --- 扫描：解析材料 → 事实 --------------------------------------------------------

def run_scan(owner_user_id, task_id, run_id, material_ids=None, force=False, provider=None):
    """解析任务内未排除材料并写入事实；单材料失败不阻塞其余材料。

    * 已成功解析且内容未变的材料复用既有事实（不重复解析）；material_ids 可限定子集
      （单材料重试用），缺省为全部未排除材料；force=True 强制重解析（解析器版本升级、
      用户显式重试时用）。
    * 解析三级分派（V2-3）：专用解析器（code/ddl/docx/pdf/xlsx/md/image）→ LLM 兜底
      （kind=other 且可读出文本；provider 缺失/超限/失败则降级文本线索，逐文件注明原因）。
      兜底解析消耗模型调用；限额为**单任务累计**（protocol.LLM_FALLBACK_*，默认 200 个 /
      50MB，可用环境变量 WIZ_BUILD_LLM_FALLBACK_MAX_FILES/_MAX_BYTES 覆盖），已消耗量按
      任务全部 scan 运行检查点累计，重试/再扫描不重置。
    * 无材料 / 全部失败 → 抛 PipelineError（run 记失败），但**已完成材料的结果保留**。
    * 长解析期间不持锁；每个材料完成即推进 progress {'done': n, 'total': m}。
    * 解析并发（V2-10/G25）：第①级专用解析器文件在 run 内线程池并行解析（worker 只解析
      不写库），主线程按物料登记顺序收集结果并逐个短事务落库——facts 内容与顺序同串行版
      完全一致；并发度 protocol.PARSE_CONCURRENCY（默认 min(8, CPU)，env 可覆盖）；
      取消/超时语义见 08 §4。
    """
    owner_id = str(owner_user_id or '')
    wanted = {str(item) for item in material_ids} if material_ids else None
    plan = _tx(lambda conn: _scan_plan(conn, owner_id, task_id, run_id, wanted, force))
    total = len(plan)
    label = protocol.SCAN_STAGE_LABELS['parse']
    runner.stage(owner_id, run_id, 'parse', label, {'done': 0, 'total': total})

    parsed = reused = failed = facts_total = 0
    modules, details = [], []
    usage = _usage()
    # V2-3（G19）：兜底限额是**单任务累计**口径——预算从该任务此前全部 scan 运行的
    # 已消耗量起步（排除本次运行），重试/再扫描不重置；fallback_budget 是累计执行计数器，
    # fallback_used 只记**本次运行**消耗（检查点落库的就是它，跨运行求和才是任务累计）。
    seeded = _tx(lambda conn: store.scan_fallback_usage(conn, task_id, owner_id,
                                                        exclude_run_id=run_id))
    fallback_budget = {'files': int(seeded.get('files') or 0),
                       'bytes': int(seeded.get('bytes') or 0)}
    fallback_used = {'files': 0, 'bytes': 0}

    # V2-10（G25）：解析并发、落库串行——run 内创建线程池（worker 只解析、不写库），
    # 三级分派第①级专用解析器文件按单文件粒度全部提交；主线程按物料登记顺序逐个
    # 收集结果并逐个短事务落库（facts 内容与顺序同串行版完全一致）。黑名单/后缀过滤
    # 在上传登记时已串行完成；LLM 兜底（第②级）不进线程池（主线程串行，预算计数
    # 语义不变）；index 阶段串行。run 结束（完成/失败/取消）必须 shutdown——按
    # shutdown(wait=False, cancel_futures=True) 关闭：未开始任务取消、执行中 worker
    # 完成当前文件（≤单文件超时），超时/迟到结果一律丢弃不再落库。
    executor = ThreadPoolExecutor(max_workers=max(1, int(protocol.PARSE_CONCURRENCY)),
                                  thread_name_prefix='build-parse')
    parse_futures = {}
    try:
        for item in plan:
            if item['reusable'] or not item['path'] or item['kind'] not in DEDICATED_KINDS:
                continue
            parse_futures[item['id']] = executor.submit(parse_material, item['path'], item['id'],
                                                        item['kind'], item['relPath'])
        for index, item in enumerate(plan, start=1):
            if item['reusable']:
                reused += 1
                facts_total += int(item['factCount'])
                for name in item['modules']:
                    if name not in modules:
                        modules.append(name)
                details.append({'id': item['id'], 'relPath': item['relPath'], 'parseState': 'reused',
                                'facts': int(item['factCount']), 'error': ''})
            else:
                # parse_state 是业务内容（R4-01）：同事务核对取消/执行权后再写
                runner.content_tx(owner_id, run_id,
                                  lambda conn, item=item:
                                  _scan_mark_running(conn, owner_id, run_id, item['id']))
                if not item['path']:
                    outcome = {'state': 'failed', 'facts': 0, 'modules': [],
                               'error': '材料文件缺失（已被清理或未登记为 blob）'}
                elif item['kind'] in DEDICATED_KINDS:
                    # 第①级：收集池内已提交的解析结果（主线程，等待带单文件超时）
                    outcome = _collect_pool_result(owner_id, run_id, task_id, item, parse_futures)
                else:
                    # 第②/③级：LLM 兜底或文本线索降级——主线程串行（不进线程池）
                    try:
                        result, usage = _parse_with_dispatch(owner_id, run_id, item, provider,
                                                             fallback_budget, fallback_used, usage)
                    except Exception as exc:  # noqa: BLE001 - 单材料异常降级为该材料失败
                        result = None
                        outcome = {'state': 'failed', 'facts': 0, 'modules': [],
                                   'error': '解析材料失败（%s）：%s' % (exc.__class__.__name__, exc)}
                    else:
                        # 事实与 parse_state/coverage 都是业务内容（R4-01）：同事务核对后再写
                        outcome = runner.content_tx(
                            owner_id, run_id,
                            lambda conn, item=item, result=result:
                            _scan_write(conn, owner_id, run_id, task_id, item, result))
                details.append({'id': item['id'], 'relPath': item['relPath'],
                                'parseState': outcome['state'], 'facts': int(outcome['facts']),
                                'error': outcome.get('error') or ''})
                facts_total += int(outcome['facts'])
                for name in outcome.get('modules') or []:
                    if name not in modules:
                        modules.append(name)
                if outcome['state'] == 'failed':
                    failed += 1
                else:
                    parsed += 1
            runner.stage(owner_id, run_id, 'parse', label, {'done': index, 'total': total})

        runner.stage(owner_id, run_id, 'index', protocol.SCAN_STAGE_LABELS['index'],
                     {'done': total, 'total': total})
        checkpoint = {'scan': {'materials': total, 'parsed': parsed, 'reused': reused,
                           'failed': failed,
                           'facts': facts_total, 'modules': modules,
                           'fallbackFiles': fallback_used['files'],
                           'fallbackBytes': fallback_used['bytes'],
                           'note': '结构索引按材料解析覆盖摘要登记；token 级检索索引在生成阶段的 '
                                   'retrieve 步按需构建（避免把全部事实一次性读进内存）。'}}
        _tx(lambda conn: (runner.check_cancelled(conn, run_id, owner_id),
                          store.update_run(conn, run_id, owner_id, usage=usage,
                                           checkpoint=checkpoint,
                                           lease=runner.lease_of(owner_id, run_id) or None)))

        if total == 0:
            raise PipelineError('没有可解析的材料：请先上传材料并确认未被排除')
        if parsed == 0 and reused == 0:
            first_error = next((item['error'] for item in details if item['error']), '')
            raise PipelineError('全部材料解析失败%s' % ('：%s' % first_error if first_error else ''))
        return {'runId': run_id, 'kind': 'scan', 'state': 'succeeded', 'materials': total,
                'parsed': parsed, 'reused': reused, 'failed': failed, 'facts': facts_total,
                'modules': modules, 'details': details, 'usage': usage}
    finally:
        # 完成/失败/取消都走这里：不等待（可能超时仍在跑的）worker，未开始任务取消
        executor.shutdown(wait=False, cancel_futures=True)


def _parse_with_dispatch(owner_id, run_id, item, provider, fallback_budget, fallback_used, usage):
    """三级分派第②/③级（主线程串行，V2-10 起**不进线程池**）：LLM 兜底 / 文本线索降级。

    仅 kind=other 会走到这里（第①级专用解析器已在池内并行解析，见 run_scan）；
    兜底是远程模型调用并叠加单任务预算计数，串行避免限流与预算竞态。
    返回 (ParseResult, usage)。fallback_budget 是单任务累计执行计数器（含此前运行
    消耗，只用于限额判定）；fallback_used 记本次运行的兜底消耗（落检查点，
    跨运行求和 = 任务累计）。
    """
    result = parse_material(item['path'], item['id'], item['kind'], item['relPath'])
    # kind=other：先尝试 LLM 兜底（可读出文本才有意义），失败/不可用降级文本线索。
    if provider is None:
        _annotate_downgrade(result, 'LLM 兜底解析不可用（未配置模型提供方），已按文本线索降级。')
        return result, usage
    if int(fallback_budget['files']) >= protocol.LLM_FALLBACK_MAX_FILES or \
            int(fallback_budget['bytes']) + int(item.get('size') or 0) > protocol.LLM_FALLBACK_MAX_BYTES:
        _annotate_downgrade(result, '文件超过单任务 LLM 兜底限额（每轮扫描最多 %d 个文件 / %d MB），'
                            '已按文本线索降级并在扫描报告注明。'
                            % (protocol.LLM_FALLBACK_MAX_FILES,
                               protocol.LLM_FALLBACK_MAX_BYTES // (1024 * 1024)))
        return result, usage
    fallback, new_usage = _llm_fallback_result(item, provider)
    usage = _add_usage(usage, new_usage)
    if not isinstance(fallback, dict):   # 成功：返回 ParseResult（弱证据事实）
        size = int(item.get('size') or 0)
        fallback_budget['files'] += 1
        fallback_budget['bytes'] += size
        fallback_used['files'] += 1
        fallback_used['bytes'] += size
        return fallback, usage
    _annotate_downgrade(result, 'LLM 兜底解析失败（%s），已按文本线索降级，可修复模型配置后重试。'
                        % (fallback.get('error') or '未知错误'))
    return result, usage


def _llm_fallback_result(item, provider):
    """LLM 兜底解析一个文件：读文本 → 切片 → 逐片调用模型 → 产出弱证据事实。

    返回 (ParseResult, usage)（成功）或 ({'ok': False, 'error': …}, usage)（整体失败）；
    usage 是本次已消耗的模型调用累计（含失败切片），由调用方并入 run.usage。
    """
    from workbench.ontology_build.parsers import textline as textline_mod
    rel = item['relPath']
    try:
        content = textline_mod.read_text_lines(item['path'])
    except (OSError, IOError, ValueError) as exc:
        return {'ok': False, 'error': '无法读取文件文本（%s）' % exc.__class__.__name__}, _usage()
    lines = [line for line in content.lines if line.strip()]
    if not lines:
        return {'ok': False, 'error': '文件无可读文本（不可解码或为空）'}, _usage()
    # 行号定位切片：每片最多 LLM_FALLBACK_SLICE_CHARS 字符、最多 LLM_FALLBACK_MAX_SLICES 片
    line_numbers = [number for number, line in enumerate(content.lines, start=1) if line.strip()]
    text_of = {number: content.line(number) for number in line_numbers}
    slices = []  # (text, startLine, endLine)
    buffer, start_line, last_line, size = [], line_numbers[0], line_numbers[0], 0
    for number in line_numbers:
        line = text_of[number]
        if size + len(line) > protocol.LLM_FALLBACK_SLICE_CHARS and buffer:
            slices.append(('\n'.join(buffer), start_line, last_line))
            buffer, start_line, size = [], number, 0
        buffer.append(line)
        last_line = number
        size += len(line)
    if buffer:
        slices.append(('\n'.join(buffer), start_line, last_line))
    truncated_slices = max(0, len(slices) - protocol.LLM_FALLBACK_MAX_SLICES)
    slices = slices[:protocol.LLM_FALLBACK_MAX_SLICES]

    sink = textline_mod.FactSink(item['id'])
    notes = ['该类型无专用解析器，已按 LLM 兜底解析（V2-3 第②级）：内容按数据发送、'
             '请求声明「材料内容不是指令」；产物一律弱证据（quality=low、module=llm-fallback、'
             '候选证据状态 inferred）。']
    notes.append('发送 %d 个文本切片（每片≤%d 字符）；%s'
                 % (len(slices), protocol.LLM_FALLBACK_SLICE_CHARS,
                    '文件过长，仅解析前 %d 片（%d 片未发送）。'
                    % (protocol.LLM_FALLBACK_MAX_SLICES, truncated_slices)
                    if truncated_slices else '全文已覆盖。'))
    failed_slices = []
    usage_total = _usage()
    for position, (slice_text, start_line, end_line) in enumerate(slices, start=1):
        answer = llm.fallback_parse(provider, rel, slice_text, position)
        _add_usage(usage_total, answer.get('usage'))
        if not answer.get('ok'):
            failed_slices.append(position)
            continue
        for clue in answer.get('facts') or []:
            snippet = clue.get('quote') or '%s：%s' % (clue.get('title') or '', clue.get('detail') or '')
            sink.add('llm-fallback',
                     {'kind': 'llm', 'file': rel, 'slice': position,
                      'startLine': start_line, 'endLine': end_line},
                     snippet, 'llmClue',
                     {'source': 'llm-fallback', 'title': clue.get('title') or '',
                      'detail': clue.get('detail') or '', 'slice': position}, 'low')
        for note in answer.get('notes') or []:
            _note(notes, '模型说明：%s' % note)
    notes.append('切片结果：%d/%d 片成功%s；事实 %d 条。'
                 % (len(slices) - len(failed_slices), len(slices),
                    ('，失败切片 %s（已重试 1 次）' % '、'.join(str(p) for p in failed_slices))
                    if failed_slices else '', len(sink.facts)))
    if failed_slices and not sink.facts:
        return ({'ok': False, 'error': '全部切片解析失败（已重试 1 次）'}, usage_total)
    coverage = {
        'modules': ['llm-fallback'],
        'notes': notes,
        'failedSegments': [{'kind': 'slice', 'locator': {'kind': 'llm', 'file': rel, 'slice': p},
                            'reason': 'LLM 兜底解析该切片失败（已重试 1 次）'}
                           for p in failed_slices],
        'locatorKind': 'llm', 'fallback': {'slices': len(slices), 'failedSlices': len(failed_slices)},
        'encoding': content.encoding,
    }
    from workbench.ontology_build.parsers.base import ParseResult
    return (ParseResult(facts=sink.facts, coverage=coverage,
                        partial=bool(failed_slices or truncated_slices or sink.truncated)),
            usage_total)


def _annotate_downgrade(result, reason):
    """把降级原因写入文本线索结果（notes + failedSegments），保证可见、不静默。"""
    result.coverage['notes'] = list(result.coverage.get('notes') or []) + [reason]
    result.coverage['failedSegments'] = list(result.coverage.get('failedSegments') or []) + [
        {'kind': 'file', 'locator': {'kind': 'text', 'file': item_rel(result)}, 'reason': reason}]
    result.warnings = list(result.warnings or []) + [reason]
    result.partial = True


def item_rel(result):
    """从结果事实取相对路径（无事实时返回空串）。"""
    for fact in result.facts or []:
        locator = getattr(fact, 'locator', None)
        if isinstance(locator, dict) and locator.get('file'):
            return str(locator['file'])
    return ''


def _scan_plan(conn, owner_id, task_id, run_id, wanted, force=False):
    """短事务：读任务与材料清单，给出本次要处理的材料（含 blob 真实路径与字节量）。"""
    runner.check_cancelled(conn, run_id, owner_id)
    if store.require_task(conn, task_id, owner_id) is None:
        raise sto.NotFound('生成任务不存在')
    items = []
    for material in store.list_materials(conn, task_id, owner_id):
        if material['excluded'] or (wanted is not None and material['id'] not in wanted):
            continue
        coverage = material.get('coverage') if isinstance(material.get('coverage'), dict) else {}
        fact_count = int(coverage.get('factCount') or 0)
        path = material_store.material_blob_path(conn, owner_id, material['id'])
        # 解析器集合会演进（如 2026-09-22 新增结构化格式解析器）：上传时检测并存库的 kind
        # 只是缓存，扫描时按**实际内容**重新检测（后缀 + 头 8 字节魔数），
        # 否则升级前上传的材料重扫也到不了新解析器（存量材料无法受益）。
        kind = material['kind']
        if path:
            try:
                with open(str(path), 'rb') as handle:
                    head = handle.read(8)
            except OSError:
                head = b''
            try:
                kind = protocol.detect_kind(material['relPath'], head=head)
            except Exception:
                kind = material['kind']
        items.append({
            'id': material['id'], 'relPath': material['relPath'], 'kind': kind,
            'path': str(path) if path else '', 'factCount': fact_count,
            'size': int(material.get('size') or 0),
            'modules': list(coverage.get('modules') or []),
            # 只有完全成功（success）且已有事实的材料才复用；partial 视为可重试，重新解析
            'reusable': (not force) and material['parseState'] == 'success' and fact_count > 0,
        })
    return items


def _scan_mark_running(conn, owner_id, run_id, material_id):
    """短事务：进入解析前把材料标为 running（晚结果不会写进新基线前先做取消检查）。"""
    runner.check_cancelled(conn, run_id, owner_id)
    store.update_material(conn, material_id, owner_id, parse_state='running')
    return material_id


def _scan_write(conn, owner_id, run_id, task_id, item, result):
    """短事务：替换该材料事实 + 更新 parseState/coverage/error（写入前复查取消/fencing）。"""
    runner.check_cancelled(conn, run_id, owner_id)
    facts = [fact.to_dict() for fact in (result.facts or [])]
    store.replace_material_facts(conn, task_id, owner_id, item['id'], facts)
    coverage = result.coverage_payload()
    state = coverage.get('parseState') or ('success' if facts else 'failed')
    store.update_material(conn, item['id'], owner_id, parse_state=state, coverage=coverage,
                          error=result.error or '')
    return {'state': state, 'facts': len(facts), 'modules': coverage.get('modules') or [],
            'error': result.error or ''}


def _collect_pool_result(owner_id, run_id, task_id, item, parse_futures):
    """主线程收集池内解析结果（V2-10）：按登记顺序、单文件等待超时、迟到结果丢弃。

    * 等待按 0.5s 短片轮询（总时长 ≤ protocol.PARSE_TIMEOUT_SECONDS）：**本函数不阻塞
      取消判定**——轮询期间每片都回到主线程，`runner.stage()/content_tx()` 的事务内
      取消检查照常执行；但「取消在多长时间内生效」由**当前文件的解析速度**决定：
      若排在前面的文件仍在解析，主线程会一直等到它的结果或单文件超时（最坏 ≤120s）
      才推进到取消检查点（需求 §5.7：执行中的 worker 让其完成当前文件）。
    * 超时 → 该文件经 content_tx 标 failed（原因「解析超时」）并清空既有事实；
      **迟到的解析结果一律丢弃、不再落库**（防重复 facts），需要内容走单文件重试。
    * worker 异常（含适配器返回 None 等契约外返回值在后处理期抛出的异常，如
      AttributeError）→ 该文件经 content_tx 标 failed 并清空既有事实（与超时路径同口径），
      **只影响该文件、不传染其他文件、不杀死 run**（G25c）。
    """
    future = parse_futures[item['id']]
    deadline = time.monotonic() + protocol.PARSE_TIMEOUT_SECONDS
    result = None
    timed_out = True
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            result = future.result(timeout=min(remaining, 0.5))
            timed_out = False
            break
        except FutureTimeoutError:
            continue
        except Exception as exc:  # noqa: BLE001 - worker 异常只影响该文件（G25c）
            # worker 内异常（注入失败/适配器契约外返回值触发的后处理异常等）：
            # 与超时同口径落库——failed + 清空既有事实 + 原因含异常摘要。
            return runner.content_tx(
                owner_id, run_id,
                lambda conn, item=item, exc=exc:
                _scan_write_pool_failure(conn, owner_id, run_id, task_id, item, exc))
    if timed_out:
        return runner.content_tx(owner_id, run_id,
                                 lambda conn, item=item:
                                 _scan_write_timeout(conn, owner_id, run_id, task_id, item))
    try:
        outcome = runner.content_tx(owner_id, run_id,
                                    lambda conn, item=item, result=result:
                                    _scan_write(conn, owner_id, run_id, task_id, item, result))
    except Exception as exc:  # noqa: BLE001 - 结果落库阶段的异常也只影响该文件
        outcome = {'state': 'failed', 'facts': 0, 'modules': [],
                   'error': '解析材料失败（%s）：%s' % (exc.__class__.__name__, exc)}
    return outcome


def _pool_failure_message(exc):
    """worker 异常摘要（截断到既有 notes 长度口径，避免异常原文撑爆 coverage）。"""
    detail = str(exc).replace('\n', ' ').strip()
    if len(detail) > 300:
        detail = detail[:300] + '…'
    return ('解析失败（%s：%s）：该文件已按失败处理并清空事实，其余材料继续解析；'
            '原因定位后可对该文件执行「重试解析」。' % (exc.__class__.__name__, detail))


def _scan_write_pool_failure(conn, owner_id, run_id, task_id, item, exc):
    """worker 异常的文件级落库（同一写事务内核对取消/fencing）：failed + 清空既有事实。

    与超时路径同口径：清空既有事实（不保留旧 facts、不把失败当空内容继续），
    failedSegments 留异常原因；迟到/无效结果永不落库。
    """
    runner.check_cancelled(conn, run_id, owner_id)
    message = _pool_failure_message(exc)
    store.replace_material_facts(conn, task_id, owner_id, item['id'], [])
    coverage = {'modules': [],
                'notes': [message],
                'failedSegments': [{'kind': 'file',
                                    'locator': {'kind': 'text', 'file': item['relPath']},
                                    'reason': '解析失败（%s）' % exc.__class__.__name__}],
                'factCount': 0}
    store.update_material(conn, item['id'], owner_id, parse_state='failed', coverage=coverage,
                          error='解析失败（%s）：%s' % (exc.__class__.__name__,
                                                      str(exc).strip()[:120]))
    return {'state': 'failed', 'facts': 0, 'modules': [],
            'error': '解析失败（%s）：%s' % (exc.__class__.__name__, str(exc).strip()[:120])}


def _scan_write_timeout(conn, owner_id, run_id, task_id, item):
    """解析超时的落库（同一写事务内核对取消/fencing）：failed + 清空既有事实。

    迟到结果永不落库：超时后不再读取该 future（结果随 executor 丢弃）；
    清空既有事实与「失败不当空内容继续」口径一致（failedSegments 留超时原因）。
    """
    runner.check_cancelled(conn, run_id, owner_id)
    message = ('解析超时（超过 %d 秒）：已放弃等待；该文件迟到的解析结果将被丢弃、'
               '不再落库，如需内容请对该文件执行「重试解析」。' % protocol.PARSE_TIMEOUT_SECONDS)
    store.replace_material_facts(conn, task_id, owner_id, item['id'], [])
    coverage = {'modules': [],
                'notes': [message],
                'failedSegments': [{'kind': 'file',
                                    'locator': {'kind': 'text', 'file': item['relPath']},
                                    'reason': '解析超时'}],
                'factCount': 0}
    store.update_material(conn, item['id'], owner_id, parse_state='failed', coverage=coverage,
                          error='解析超时')
    return {'state': 'failed', 'facts': 0, 'modules': [], 'error': '解析超时'}


# --- 候选校验与协议适配（确定性，不调用模型） -------------------------------------

def _issue(code, field, message):
    return {'code': code, 'field': field, 'message': message}


def verify_candidates(candidates, fact_ids, weak_fact_ids=None):
    """校验候选：证据引用、必填字段、类型枚举；剔除幻造引用并重算证据状态与默认决定。

    返回 (候选列表, 报告)。报告含 issues / droppedRefs / structural 计数与说明。
    证据状态只降不升：无证据 → insufficient；引用被剔除 → inferred；
    结构性问题不会保留 supported（避免“有依据”假象误导后续拟纳入）。
    `weak_fact_ids`（V2-3）：LLM 兜底解析产物（module=llm-fallback）的 factId 集合——
    证据命中任一弱证据事实的候选不得为 supported，一律降级 inferred 并默认暂缓（§9.1）。
    同一候选在不同阶段会被校验两次（逐批 + 合并后），完全相同的 issue 只保留一条。
    """
    known = {_text(item) for item in (fact_ids or []) if _text(item)}
    weak = {str(item) for item in (weak_fact_ids or []) if _text(item)}
    out, report = [], {'issues': 0, 'droppedRefs': 0, 'structural': 0,
                       'fallbackDowngraded': 0, 'notes': []}
    for raw in candidates or []:
        if not isinstance(raw, dict):
            continue
        candidate = dict(raw)
        issues = []

        def push(code, field, message):
            """登记 issue（完全相同的项只保留一条：候选会被逐批与合并后各校验一次）。"""
            item = _issue(code, field, message)
            if item not in issues:
                issues.append(item)
            return item

        for item in (candidate.get('issues') or []):
            if isinstance(item, dict) and item not in issues:
                issues.append(item)
        rejected = candidate.pop('rejectedRefs', 0)
        try:
            rejected = int(rejected or 0)
        except (TypeError, ValueError):
            rejected = 0
        if rejected:
            # llm 层已剔除越界引用；这里把“发生了剔除”固化为可展示的问题项
            push('EVIDENCE_REF_UNKNOWN', 'evidence',
                 '模型给出的 %d 处证据位置不在本次材料事实内，已剔除（该候选按推断处理）' % rejected)
            report['droppedRefs'] += rejected
        evidence, dropped = {}, 0
        for field, refs in (candidate.get('evidence') or {}).items():
            keep = []
            for ref in refs if isinstance(refs, list) else []:
                fact_id = _text(ref)
                if not fact_id:
                    continue
                if fact_id in known:
                    if fact_id not in keep:
                        keep.append(fact_id)
                else:
                    dropped += 1
            if keep:
                evidence[_text(field)] = keep
        candidate['evidence'] = evidence
        if dropped:
            push('EVIDENCE_REF_UNKNOWN', 'evidence',
                 '模型引用的 %d 个证据位置不在本次材料事实内，已剔除' % dropped)
            report['droppedRefs'] += dropped

        ctype = _text(candidate.get('type')).lower()
        candidate['type'] = ctype
        name = _text(candidate.get('name'))
        candidate['name'] = name
        definition = _text(candidate.get('definition'))
        candidate['definition'] = definition
        if not name:
            push('NAME_REQUIRED', 'name', '候选缺少名称')
        if not definition:
            push('DEFINITION_REQUIRED', 'definition', '候选缺少业务定义')
        if ctype not in protocol.CANDIDATE_TYPES:
            push('TYPE_INVALID', 'type', '候选类型不在协议枚举内：%s' % (ctype or '空'))
        fields = candidate.get('fields') if isinstance(candidate.get('fields'), dict) else {}
        fields = dict(fields)
        if ctype == 'property':
            data_type = _canonical(fields.get('dataType'), _DATA_TYPES)
            if data_type:
                fields['dataType'] = data_type
            if data_type not in protocol.PROPERTY_DATA_TYPES:
                push('DATA_TYPE_INVALID', 'dataType',
                     '属性「%s」的数据类型不受支持：%s' % (name or '未命名', data_type or '空'))
            elif data_type == 'timeSeries':
                value_type = _canonical(fields.get('valueType'), _VALUE_TYPES)
                if value_type:
                    fields['valueType'] = value_type
                if value_type not in protocol.VALUE_TYPES:
                    push('OBSERVATION_VALUE_TYPE_MISSING' if not value_type
                         else 'OBSERVATION_VALUE_TYPE_INVALID', 'valueType',
                         '时间序列属性「%s」%s（可选：%s）'
                         % (name or '未命名', '缺少观测值类型' if not value_type
                            else '的观测值类型「%s」不在枚举内' % value_type,
                            '/'.join(protocol.VALUE_TYPES)))
            if not _text(candidate.get('ownerKey')):
                push('PROPERTY_OWNER_MISSING', 'ownerKey',
                     '属性「%s」未给出所属对象' % (name or '未命名'))
        elif ctype == 'link':
            for slot, role in (('sourceRef', '源端'), ('targetRef', '目标端')):
                if not _text(fields.get(slot)):
                    push('LINK_ENDPOINT_MISSING', slot,
                         '链接「%s」缺少%s对象' % (name or '未命名', role))
        candidate['fields'] = fields

        status = _text(candidate.get('evidenceStatus')).lower()
        if status not in protocol.EVIDENCE_STATUSES:
            status = 'inferred'
        has_structural = any(_text(item.get('code')) in _STRUCTURAL_CODES for item in issues)
        refs = {ref for refs_list in (candidate.get('evidence') or {}).values()
                for ref in (refs_list if isinstance(refs_list, list) else [])}
        weak_hits = bool(refs & weak) if weak else False
        if weak_hits and status == 'supported':
            status = 'inferred'
        if not evidence and status != 'conflict':
            status = 'insufficient'
        if dropped and status != 'conflict':
            status = 'inferred'
        if has_structural and status == 'supported':
            status = 'inferred'
        candidate['evidenceStatus'] = status
        if weak_hits:
            issues.append(_issue('LLM_FALLBACK_EVIDENCE', 'evidence',
                                 '候选证据包含 LLM 兜底解析产物（弱证据，module=llm-fallback）：'
                                 '一律按「推断待确认」处理并默认暂缓，不进入有依据初稿选择集'))
            report['fallbackDowngraded'] += 1
        candidate['issues'] = issues[:40]
        candidate['decision'] = _text(candidate.get('decision')) or \
            protocol.default_decision(status, has_structural)
        report['issues'] += len(candidate['issues'])
        report['structural'] += 1 if has_structural else 0
        out.append(candidate)
    if report['droppedRefs']:
        report['notes'].append('剔除幻造证据引用 %d 处（对应候选已降级，绝不展示虚构位置）。'
                               % report['droppedRefs'])
    return out, report


def _canonical(value, table):
    """枚举取值大小写规范化：命中枚举返回规范写法，未命中保留原文（不新增取值）。"""
    text = _text(value)
    return table.get(text.casefold(), text)


def adapt_candidates(candidates):
    """协议字段映射与 definitionOrder 预检（**不创建本体、不分配定义 ID**）。

    返回 {'candidates': [...], 'definitionOrder': [...], 'issues': [...], 'notes': [...]}；
    重复的定义键会在候选上追加 KEY_DUPLICATE 问题并降级为「推断待确认」。
    """
    mapped = []
    for raw in candidates or []:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        ctype = _text(item.get('type')).lower()
        item['type'] = ctype
        fields = item.get('fields') if isinstance(item.get('fields'), dict) else {}
        keep = _FIELDS_BY_TYPE.get(ctype)
        clean = {}
        for key in (keep if keep is not None else tuple(fields.keys())):
            value = fields.get(key)
            if value in (None, ''):
                continue
            clean[key] = value.strip() if isinstance(value, str) else value
        item['fields'] = clean
        if ctype not in protocol.CANDIDATE_TYPES:
            # 未知类型不进入定义顺序，交由人工处理（问题已在 verify 阶段登记）
            mapped.append(item)
            continue
        item['definitionKey'] = _text(item.get('alignedKey')) or '%s:%s' % (
            ctype, ' '.join(_text(item.get('name')).casefold().split()))
        mapped.append(item)

    order, seen, issues = [], {}, []
    for ctype in _TYPE_ORDER:
        bucket = [item for item in mapped if item.get('type') == ctype]
        for item in sorted(bucket, key=lambda node: (node.get('definitionKey') or '',
                                                     node.get('name') or '')):
            key = item.get('definitionKey') or ''
            order.append(key)
            if key in seen:
                item_issues = [entry for entry in (item.get('issues') or []) if isinstance(entry, dict)]
                item_issues.append(_issue('KEY_DUPLICATE', 'key',
                                          '定义键「%s」与另一候选重复，交付前必须人工合并或改名' % key))
                item['issues'] = item_issues[:40]
                if item.get('evidenceStatus') == 'supported':
                    item['evidenceStatus'] = 'inferred'
                item['decision'] = protocol.default_decision(item.get('evidenceStatus') or 'inferred',
                                                             True)
                issues.append(item_issues[-1])
            else:
                seen[key] = item
    notes = [DEFINITION_ORDER_NOTE]
    if issues:
        notes.append('发现 %d 个重复定义键：已在候选上登记 KEY_DUPLICATE 并降级为待确认。'
                     % len(issues))
    return {'candidates': mapped, 'definitionOrder': order, 'issues': issues, 'notes': notes}


def _candidate_payload(candidate, batch_id):
    """候选（内存结构）→ store.create_candidate 载荷（origin 记录批次与来源键）。

    origin 以候选自带的 origin 字典为底（D04 继承标记 revived 等随批次写入），
    再覆盖批次/来源键等系统字段；不认识的键原样保留，不静默丢人工继承信息。
    """
    origin = candidate.get('origin') if isinstance(candidate.get('origin'), dict) else {}
    merged_from = candidate.get('mergedFromKeys')
    payload = {
        'type': _text(candidate.get('type')) or 'object',
        'key': _text(candidate.get('key')),
        'name': _text(candidate.get('name')),
        'definition': _text(candidate.get('definition')),
        'fields': candidate.get('fields') if isinstance(candidate.get('fields'), dict) else {},
        'ownerKey': _text(candidate.get('ownerKey')),
        'evidence': candidate.get('evidence') if isinstance(candidate.get('evidence'), dict) else {},
        'evidenceStatus': _text(candidate.get('evidenceStatus')) or 'inferred',
        'conflicts': candidate.get('conflicts') if isinstance(candidate.get('conflicts'), list) else [],
        'decision': _text(candidate.get('decision')) or 'defer',
        'issues': candidate.get('issues') if isinstance(candidate.get('issues'), list) else [],
        'alignedKey': _text(candidate.get('alignedKey')),
    }
    origin_payload = dict(origin)
    origin_payload.update({
        'batch': batch_id,
        'key': _text(candidate.get('key')),
        'mergedFrom': [str(item) for item in (merged_from or []) if _text(item)],
        'mergedInto': origin.get('mergedInto'),
    })
    payload['origin'] = origin_payload
    return payload


def _write_candidates(conn, owner_id, task_id, batch_id, items):
    """短事务内：整批替换候选（先删本批次旧行，再逐条创建）。"""
    store.delete_candidates_of_batch(conn, task_id, owner_id, batch_id)
    created = []
    for item in _dedupe_keys(items):
        created.append(store.create_candidate(conn, task_id, owner_id, batch_id,
                                              _candidate_payload(item, batch_id)))
    return created


def _append_candidates(conn, owner_id, task_id, batch_id, items):
    created = []
    for item in _dedupe_keys(items):
        created.append(store.create_candidate(conn, task_id, owner_id, batch_id,
                                              _candidate_payload(item, batch_id)))
    return created


def _manual_exclusion_keys(conn, owner_id, task_id, batch_id):
    """跨批次的人工排除决定：回放全部旧批次后**仍**被排除的 alignedKey 集合（D04）。

    需求 §8.3 硬禁令是「人工否决不得被再次生成自动复活」；只回看最近一个有候选的旧批次
    会让保护只生效一轮（B1 排除 → B2 继承为 defer → B3 直接复活）。因此这里取该任务的
    全部批次（`store.list_batches`，上限见 _DECISION_HISTORY_BATCH_LIMIT，避免默认 20 条
    截断掉更早的决定），按 created_at 从旧到新回放，跳过当前批次；每批用
    `all_candidates(..., include_merged=True)` 读取（被合并掉的候选同样承载过人工决定，
    漏掉它们会让保护凭空失效）。逐 alignedKey 维护状态，后发生的批次覆盖先前的：

    * decision=exclude                  → 状态 'exclude'（人工否决，持续生效）；
    * decision=include 且 reviewed 为真  → 状态 'include'（用户显式重新纳入，解除保护）；
    * 其余（defer，或未经评审的 include/未知取值）→ 不改变已有状态；此前没有任何状态时
      记为 'defer'（继承产生的 defer 与人工暂缓都不构成保护，也不解除保护）。

    返回最终状态为 'exclude' 的键集合（空 alignedKey 不参与保护）。
    """
    state = {}
    history = store.list_batches(conn, task_id, owner_id, limit=_DECISION_HISTORY_BATCH_LIMIT)
    # 仓储按 created_at DESC 返回（只取最近 N 批）；这里显式排成从旧到新，同秒批次按 id
    # 定序，保证「后发生的批次覆盖先前的」有确定结果而不是数据库返回顺序。
    ordered = sorted(history,
                     key=lambda batch: (_text(batch.get('createdAt')), _text(batch.get('id'))))
    for previous in ordered:
        previous_id = _text(previous.get('id'))
        if not previous_id or previous_id == _text(batch_id):
            continue
        for item in store.all_candidates(conn, task_id, owner_id, batch_id=previous_id,
                                         include_merged=True):
            key = _text(item.get('alignedKey'))
            if not key:
                continue
            decision = _text(item.get('decision'))
            if decision == 'exclude':
                state[key] = 'exclude'
            elif decision == 'include' and bool(item.get('reviewed')):
                state[key] = 'include'
            else:
                state.setdefault(key, 'defer')
    return {key for key, value in state.items() if value == 'exclude'}


def inherit_manual_exclusions(conn, owner_id, task_id, batch_id, items):
    """人工决定继承（D04，需求 §8.3 硬禁令）：排除决定**跨批次持续继承**，直到用户显式重新纳入。

    新批次按 alignedKey 命中**历史任一旧批次**人工排除（decision=exclude）的候选时，强制
    defer 并在 origin 标记 `revived=true`（提示人工重新处理）；保护不会因为再次生成而失效，
    只在用户对后来批次显式决定 include（reviewed=true）时解除。
    必须在生成写事务内执行（候选行随后由调用方整批落库）。返回被保护的候选数。
    """
    excluded_keys = _manual_exclusion_keys(conn, owner_id, task_id, batch_id)
    protected = 0
    if not excluded_keys:
        return protected
    for item in items:
        if not isinstance(item, dict):
            continue
        if str(item.get('alignedKey') or '') not in excluded_keys:
            continue
        item['decision'] = 'defer'
        origin = item.get('origin') if isinstance(item.get('origin'), dict) else {}
        origin = dict(origin)
        origin['revived'] = True
        item['origin'] = origin
        protected += 1
    return protected


# --- 生成：检索 → 对齐 → 抽象 → 校验 → 适配 ---------------------------------------

def run_generate(owner_user_id, task_id, run_id, batch_id, provider, resume_mode='auto'):
    """按冻结基线生成候选定义（只写任务侧候选，不创建本体）。

    V2-8 / G23 断点续跑：
    * 基线约束：重试沿用同一 scope revision 与材料基线——运行冻结基线与当前修订不一致
      时直接失败（修改范围/材料不属于重试，须重新确认生成新批次）。
    * 阶段检查点：retrieve/align（确定性阶段）产物按 modelFactIds 有序清单持久化；
      LLM 阶段失败重试直接复用（不重算确定性阶段），清单中的事实缺失时自动降级为重算。
    * 批次检查点：逐批落库 + 逐批持久化 done/failed；某批失败只标记该批，重试
      （run-resume, resumeMode=auto）只跑失败批次，成功批次候选保留；resumeMode=abstract
      表示复用确定性产物但重跑全部抽象批次。
    * resume_mode='auto'（默认）按检查点续跑；'abstract' 强制重跑全部批次。

    返回运行摘要（含 usage / counts / definitionOrder / notes）；失败抛 PipelineError，
    已完成批次的候选与已解析材料保持可用。
    """
    owner_id = str(owner_user_id or '')
    if not isinstance(provider, dict) or not provider.get('endpoint') or not provider.get('model'):
        raise PipelineError('尚未配置可用的 LLM 提供方，请先到「更多工具 → LLM 配置」添加')
    context = _tx(lambda conn: _generate_context(conn, owner_id, task_id, run_id))
    scope, facts, task = context['scope'], context['facts'], context['task']
    saved = (context['checkpoint'] or {}).get('generate') or {}
    baseline = context['baseline'] or {}
    notes = []
    # V2-3（G19）：LLM 兜底解析产物一律按弱证据处理——命中其证据的候选不得 supported。
    weak_fact_ids = {str(fact.get('id')) for fact in facts
                     if str(fact.get('module') or '') == 'llm-fallback'}

    # 基线约束（G23）：重试沿用同一次范围/材料基线；修改范围或材料是重新生成。
    if baseline.get('scopeRevision') is not None and \
            int(baseline.get('scopeRevision')) != int(scope.get('revision') or 0):
        raise PipelineError('范围已在生成后修改（基线 revision %s，当前 %s）：修改范围属于重新生成，'
                            '请重新确认范围生成新批次，不能沿用旧批次重试。'
                            % (baseline.get('scopeRevision'), scope.get('revision')))
    if baseline.get('materialRevision') is not None and task is not None and \
            int(baseline.get('materialRevision')) != int(task.get('materialRevision') or 0):
        raise PipelineError('材料已在生成后变化（基线修订 %s，当前 %s）：请重新确认范围生成新批次。'
                            % (baseline.get('materialRevision'), task.get('materialRevision')))
    by_id = {fact['id']: fact for fact in facts}

    # 1)+2) retrieve/align：确定性阶段——优先复用已持久化产物，缺失时重算
    label = protocol.GENERATE_STAGE_LABELS['retrieve']
    saved_plan = saved.get('plan') if isinstance(saved.get('plan'), dict) else {}
    plan_ids = [str(item) for item in (saved_plan.get('modelFactIds') or [])]
    # 阶段检查点（V2-8）：auto 与 abstract 都复用已持久化的确定性产物（筛选/对齐不重算）；
    # 两者只差在批次——auto 只补失败批，abstract 重跑全部抽象批。
    plan_reusable = (plan_ids
                     and int(saved_plan.get('scopeRevision') or -1) == int(scope.get('revision') or 0)
                     and all(item in by_id for item in plan_ids))
    if plan_reusable:
        if resume_mode == 'abstract':
            _note(notes, '从「抽象本体定义」阶段重试：复用上次运行持久化的筛选/对齐产物'
                         '（%d 条事实，确定性阶段未重算），重跑全部 %d 个抽象批次。'
                         % (len(saved_plan.get('modelFactIds') or []),
                            -(-len(saved_plan.get('modelFactIds') or []) // protocol.LLM_BATCH_FACTS)))
        model_facts = [by_id[item] for item in plan_ids if item in by_id]
        relevant_n = int(saved_plan.get('relevant') or 0)
        related_n = int(saved_plan.get('related') or 0)
        excluded_n = int(saved_plan.get('excluded') or 0)
        duplicates_n = int(saved_plan.get('duplicates') or 0)
        groups_count = int(saved_plan.get('groups') or 0)
        truncated = max(0, len(plan_ids) - MAX_MODEL_FACTS)
        pool = model_facts
        _note(notes, '复用上次运行持久化的筛选/对齐产物（%d 条事实、材料与范围基线一致），'
                     '确定性阶段未重算。' % len(model_facts))
        runner.stage(owner_id, run_id, 'retrieve', label,
                     {'done': len(model_facts), 'total': len(model_facts),
                      'relevant': relevant_n, 'related': related_n, 'excluded': excluded_n,
                      'resumed': 1})
        runner.stage(owner_id, run_id, 'align', protocol.GENERATE_STAGE_LABELS['align'],
                     {'done': len(model_facts), 'total': len(model_facts),
                      'groups': int(saved_plan.get('groups') or 0), 'duplicates': duplicates_n,
                      'resumed': 1})
    else:
        runner.stage(owner_id, run_id, 'retrieve', label, {'done': 0, 'total': len(facts)})
        index = retrieval.build_index(facts)
        selection = retrieval.select_scope(facts, scope, index)
        pool = [by_id[fact_id] for fact_id in selection['relevant'] + selection['related']
                if fact_id in by_id]
        if not pool:
            raise PipelineError('没有可用于生成的事实：请先扫描材料并确认范围')
        relevant_n, related_n = len(selection['relevant']), len(selection['related'])
        excluded_n = len(selection['excluded'])
        runner.stage(owner_id, run_id, 'retrieve', label,
                     {'done': len(pool), 'total': len(pool), 'relevant': relevant_n,
                      'related': related_n, 'excluded': excluded_n})

        # align：同主体证据分组 + 同一片段哈希去重（重复副本不算独立佐证）
        label = protocol.GENERATE_STAGE_LABELS['align']
        runner.stage(owner_id, run_id, 'align', label, {'done': 0, 'total': len(pool)})
        grouped = alignment.group_evidence(facts, selection['relevant'] + selection['related'])
        seen_digest, model_facts = set(), []
        for fact in pool:  # 保持“相关优先”顺序，重复片段只保留首个
            digest = retrieval.snippet_digest(fact)
            if digest in seen_digest:
                continue
            seen_digest.add(digest)
            model_facts.append(fact)
        duplicates_n = grouped['stats']['duplicates']
        groups_count = len(grouped['groups'])
        truncated = max(0, len(model_facts) - MAX_MODEL_FACTS)
        if truncated:
            model_facts = model_facts[:MAX_MODEL_FACTS]
            _note(notes, '事实总量超过单次生成上限 %d，按相关度优先保留前 %d 条，'
                         '其余 %d 条未发送给模型（可缩小范围后重跑）。'
                         % (MAX_MODEL_FACTS, len(model_facts), truncated))
        _note(notes, '证据分组 %d 组，重复片段 %d 条只计一次佐证。'
                     % (len(grouped['groups']), duplicates_n))
        runner.stage(owner_id, run_id, 'align', label,
                     {'done': len(model_facts), 'total': len(model_facts),
                      'groups': len(grouped['groups']), 'duplicates': duplicates_n})

    plan_snapshot = {
        'modelFactIds': [str(fact.get('id')) for fact in model_facts],
        'relevant': relevant_n, 'related': related_n, 'excluded': excluded_n,
        'duplicates': duplicates_n, 'groups': groups_count,
        'scopeRevision': int(scope.get('revision') or 0),
        'materialRevision': int(task.get('materialRevision') or 0) if task else 0,
    }

    def _checkpoint(done_positions, failed_batches, extra=None):
        payload = {'generate': {
            'batchId': batch_id,
            'scopeRevision': plan_snapshot['scopeRevision'],
            'materialRevision': plan_snapshot['materialRevision'],
            'plan': plan_snapshot,
            'batches': {'size': protocol.LLM_BATCH_FACTS, 'total': len(batches),
                        'done': sorted(done_positions), 'failed': failed_batches},
        }}
        if extra:
            payload['generate'].update(extra)
        return payload

    # 3) abstract：分批调用模型抽取，逐批落库 + 逐批检查点（失败/取消时已完成批次保留）
    label = protocol.GENERATE_STAGE_LABELS['abstract']
    batches = [model_facts[start:start + protocol.LLM_BATCH_FACTS]
               for start in range(0, len(model_facts), protocol.LLM_BATCH_FACTS)]
    runner.stage(owner_id, run_id, 'abstract', label,
                 {'done': 0, 'total': len(batches), 'facts': len(model_facts)})
    saved_batches = saved.get('batches') if isinstance(saved.get('batches'), dict) else {}
    done_positions, failed_batches = set(), []
    if plan_reusable and resume_mode != 'abstract' \
            and int(saved_batches.get('total') or 0) == len(batches):
        done_positions = {int(p) for p in (saved_batches.get('done') or [])
                          if str(p).lstrip('-').isdigit() and 1 <= int(p) <= len(batches)}
        failed_batches = [item for item in (saved_batches.get('failed') or [])
                          if isinstance(item, dict)
                          and 1 <= int(item.get('position') or 0) <= len(batches)]
    else:
        # 批次计划与上次不一致（重算/无检查点）：整批重来——先作废本批旧候选（R4-01，
        # 同事务核对取消/执行权后再删）。
        runner.content_tx(owner_id, run_id,
                          lambda conn: store.delete_candidates_of_batch(conn, task_id, owner_id,
                                                                        batch_id))
    accumulated = []
    if done_positions:
        # 成功批次的候选已在库中（逐批落库）：载入为合并/校验的种子，绝不重跑这些批次
        accumulated = list(_tx(lambda conn: store.all_candidates(conn, task_id, owner_id,
                                                                batch_id=batch_id)))
        if failed_batches:
            _note(notes, '检测到上次失败的批次（%s），本次只重跑失败批次，成功批次候选保留。'
                         % '、'.join(str(item.get('position')) for item in failed_batches))
    usage = _usage()
    rejected_total = 0
    # 批次级并发（2026-09-22）：批次之间无依赖，串行等待是纯浪费（实测 2 批 190 秒、
    # 单批 57.7 秒）。池内并发调用模型，**主线程按批次序号逐个收集并落库**——
    # facts/候选顺序与串行版完全一致（与 V2-10「解析并发、落库串行」同一确定性原则）。
    pending_batches = [(position, batch) for position, batch in enumerate(batches, start=1)
                       if position not in done_positions]
    for position, _batch in enumerate(batches, start=1):
        if position in done_positions:
            runner.stage(owner_id, run_id, 'abstract', label,
                         {'done': position, 'total': len(batches), 'facts': len(model_facts),
                          'candidates': len(accumulated), 'resumed': 1})
    # 自适应并发调度（2026-09-22）：批次间无依赖，但 provider 有账号级 RPM 限流
    # （实测 GLM：并发 2 稳定、4 路零星 429、16 路多数失败）——固定并发要么浪费
    # 要么大批失败。调度器按结果动态调档（连续成功升一路、撞 429 降一路并冷却），
    # 并在等待期间每 15 秒写心跳（waitingPosition/waitedSeconds），
    # 解决「页面几分钟只显示处理中」；结果按批次序号返回，落库顺序与串行版一致。
    adaptive_results = _run_batches_adaptive(
        owner_id, run_id, label, batches, pending_batches, provider, scope,
        done_positions, accumulated, model_facts)
    try:
        for position, _batch in pending_batches:
            result = adaptive_results.get(position) or {
                'ok': False, 'candidates': [], 'error': '批次未返回结果（可「重试失败批次」）'}
            _add_usage(usage, result.get('usage'))
            failed_batches = [item for item in failed_batches
                              if int(item.get('position') or 0) != position]
            if not result.get('ok'):
                failed_batches.append({'position': position,
                                       'error': str(result.get('error') or '未知错误')[:400]})
            else:
                done_positions.add(position)
                rejected_total += int(result.get('rejectedRefs') or 0)
                verified, _report = verify_candidates(result.get('candidates') or [], by_id.keys(),
                                                       weak_fact_ids=weak_fact_ids)
                if verified:
                    accumulated.extend(verified)
                    # 候选是业务内容（R4-01）：同事务核对取消/执行权后再追加，
                    # 已取消/已被接管的旧 worker 在这里被拒绝，绝不污染新 attempt 的批次。
                    runner.content_tx(owner_id, run_id,
                                      lambda conn, items=verified:
                                      _append_candidates(conn, owner_id, task_id, batch_id, items))
            # 批次检查点（V2-8）：成功/失败都持久化——崩溃或失败后重试只补缺口
            _tx(lambda conn, done=set(done_positions), failed=list(failed_batches):
                (runner.check_cancelled(conn, run_id, owner_id),
                 store.update_run(conn, run_id, owner_id, checkpoint=_checkpoint(done, failed),
                                  lease=runner.lease_of(owner_id, run_id) or None)))
            runner.stage(owner_id, run_id, 'abstract', label,
                         {'done': position, 'total': len(batches), 'facts': len(model_facts),
                          'candidates': len(accumulated)})
    finally:
        # 抽取池在 _run_batches_adaptive 内已 shutdown(wait=False, cancel_futures=True)：
        # 未开始的批次取消，执行中的不等待（迟到结果一律丢弃，与 V2-10 同口径）。
        pass
    errors = ['第 %d 批抽取失败：%s' % (item['position'], item.get('error') or '未知错误')
              for item in failed_batches]
    for message in errors:
        _note(notes, message)
    if not accumulated:
        if errors:
            raise PipelineError('全部批次抽取失败：%s；已完成批次候选保留，可「重试失败批次」。'
                                % errors[0])
        raise PipelineError('模型未产出任何候选定义，请检查范围与材料后重试或更换模型')
    if failed_batches:
        # G23：某批失败只标记该批——成功批次候选保留，运行失败并给出失败入口；
        # 重试（resumeMode=auto）只重跑失败批次。
        first = failed_batches[0]
        raise PipelineError('第 %d/%d 批抽取失败（%s）：其余 %d 批候选已保留，'
                            '请在生成页「重试失败批次」只补跑失败批次。'
                            % (first.get('position'), len(batches),
                               first.get('error') or '未知错误',
                               max(0, len(batches) - len(failed_batches))))

    # 跨批合并：同名同类型（属性还要求 dataType 一致）才合并，差异字段保留两侧
    aligned = alignment.align(accumulated)
    for message in aligned['notes']:
        _note(notes, message)

    # 4) verify：证据引用 / 必填字段 / 类型枚举
    label = protocol.GENERATE_STAGE_LABELS['verify']
    runner.stage(owner_id, run_id, 'verify', label, {'done': 0, 'total': len(aligned['candidates'])})
    verified, report = verify_candidates(aligned['candidates'], by_id.keys(),
                                         weak_fact_ids=weak_fact_ids)
    for message in report['notes']:
        _note(notes, message)
    runner.stage(owner_id, run_id, 'verify', label,
                 {'done': len(verified), 'total': len(verified), 'issues': report['issues']})

    # 5) adapt：协议字段映射与 definitionOrder 预检（不创建本体）
    label = protocol.GENERATE_STAGE_LABELS['adapt']
    runner.stage(owner_id, run_id, 'adapt', label, {'done': 0, 'total': len(verified)})
    adapted = adapt_candidates(verified)
    for message in adapted['notes']:
        _note(notes, message)
    final = _dedupe_keys(adapted['candidates'])[:protocol.MAX_CANDIDATES_PER_BATCH]
    if len(adapted['candidates']) > protocol.MAX_CANDIDATES_PER_BATCH:
        _note(notes, '候选数超过单批上限 %d，仅登记前 %d 条（其余请缩小范围后重跑）。'
                     % (protocol.MAX_CANDIDATES_PER_BATCH, len(final)))
    runner.stage(owner_id, run_id, 'adapt', label, {'done': len(final), 'total': len(final)})

    def _final_write(conn):
        # 取消/执行权核对由 content_tx 在同一写事务内完成（R4-01）：
        # 人工排除继承 + 本批次整批替换（先删后写）必须作为一个不可拆的内容事务。
        protected = inherit_manual_exclusions(conn, owner_id, task_id, batch_id, final)
        if protected:
            _note(notes, '历史批次人工排除的 %d 个候选在新批次未自动复活：已强制暂缓并标记 '
                         'revived，需人工确认后重新纳入。' % protected)
        return _write_candidates(conn, owner_id, task_id, batch_id, final)
    runner.content_tx(owner_id, run_id, _final_write)
    summary = {
        'runId': run_id, 'kind': 'generate', 'state': 'succeeded', 'batchId': batch_id,
        'facts': {'total': len(facts), 'pool': len(pool), 'sent': len(model_facts),
                  'batches': len(batches), 'truncated': truncated},
        'stages': list(protocol.GENERATE_STAGES),
        'resumedPlan': bool(plan_reusable),
        'candidates': len(final), 'counts': {'byType': _counts(final, 'type'),
                                             'byStatus': _counts(final, 'evidenceStatus'),
                                             'byDecision': _counts(final, 'decision')},
        'issues': report['issues'], 'conflicts': sum(len(item.get('conflicts') or [])
                                                     for item in final),
        'droppedRefs': report['droppedRefs'] + rejected_total,
        'alignment': aligned['stats'],
        'definitionOrder': adapted['definitionOrder'], 'notes': notes,
        'errors': errors, 'provider': _fingerprint(provider), 'usage': usage,
    }
    checkpoint = _checkpoint(done_positions, failed_batches, extra={
        'facts': summary['facts'], 'counts': summary['counts'], 'issues': report['issues'],
        'droppedRefs': summary['droppedRefs'],
        'definitionOrder': adapted['definitionOrder'][:100],
        'truncatedOrder': max(0, len(adapted['definitionOrder']) - 100)})
    _tx(lambda conn: store.update_run(conn, run_id, owner_id, usage=usage, checkpoint=checkpoint,
                                      lease=runner.lease_of(owner_id, run_id) or None))
    return summary


def _generate_context(conn, owner_id, task_id, run_id):
    """短事务：取消检查 + 任务/范围/事实/运行检查点快照（LLM 调用必须在事务外）。"""
    runner.check_cancelled(conn, run_id, owner_id)
    task_row = store.require_task(conn, task_id, owner_id)
    if task_row is None:
        raise sto.NotFound('生成任务不存在')
    run_row = store.get_run(conn, run_id, owner_id)
    if run_row is None:
        raise sto.NotFound('运行不存在')
    return {'task': store.task_view(task_row),
            'scope': store.get_scope(conn, task_id, owner_id),
            'facts': store.list_facts(conn, task_id, owner_id),
            'checkpoint': store._loads(run_row['checkpoint_json'] or '{}', {}),
            'baseline': store._loads(run_row['baseline_json'] or '{}', {})}


# --- 范围对话 ---------------------------------------------------------------------

def run_dialog(owner_user_id, task_id, run_id, provider):
    """一轮范围澄清对话：取最近消息与范围 → 模型 → 写助手消息（patch 只是建议）。

    * 助手 patch 绝不自动写入范围（本函数不调用 put_scope，人工已保存的字段不被覆盖）。
    * 模型失败也写一条 assistantError 消息（保留用户消息与已有摘要，可重试），
      随后抛 PipelineError 让运行状态如实显示失败。
    """
    owner_id = str(owner_user_id or '')
    context = _tx(lambda conn: _dialog_context(conn, owner_id, task_id, run_id))
    label = DIALOG_STAGE_LABEL
    runner.stage(owner_id, run_id, DIALOG_STAGE, label, {'done': 0, 'total': 1})
    result = llm.scope_turn(provider, context['scope'], context['materials'], context['history'])
    content = llm.assistant_text(result)
    error = '' if result.get('ok') else _text(result.get('error')) or 'LLM 调用失败'
    patch = result.get('patch') if isinstance(result.get('patch'), dict) else {}
    # 助手消息是业务内容（R4-01）：同事务核对取消/执行权后再追加，
    # 晚到的旧 worker 回答不得排进新 attempt 的消息流。
    message_id, seq = runner.content_tx(owner_id, run_id, lambda conn: store.append_message(
        conn, task_id, owner_id, 'assistant', content, patch=patch, error=error,
        scope_revision=int(context['scope'].get('revision') or 0)))
    usage = result.get('usage') if isinstance(result.get('usage'), dict) else _usage()
    checkpoint = {'dialog': {'messageId': message_id, 'seq': int(seq),
                             'questions': len(result.get('questions') or []),
                             'patchKeys': sorted(patch.keys()),
                             'scopeRevision': int(context['scope'].get('revision') or 0),
                             'note': '助手 patch 仅为建议，不会自动覆盖人工保存的范围。'}}
    _tx(lambda conn: store.update_run(conn, run_id, owner_id, usage=usage, checkpoint=checkpoint,
                                      lease=runner.lease_of(owner_id, run_id) or None))
    if not result.get('ok'):
        raise PipelineError(error)
    return {'runId': run_id, 'kind': 'dialog', 'state': 'succeeded', 'messageId': message_id,
            'seq': int(seq), 'questions': result.get('questions') or [],
            'patchKeys': sorted(patch.keys()), 'notes': result.get('notes') or [],
            'scopeRevision': int(context['scope'].get('revision') or 0), 'usage': usage}


def _dialog_context(conn, owner_id, task_id, run_id):
    """短事务：取消检查 + 范围 + 最近消息 + 未排除材料摘要（只发明细，不发送原文）。"""
    runner.check_cancelled(conn, run_id, owner_id)
    if store.require_task(conn, task_id, owner_id) is None:
        raise sto.NotFound('生成任务不存在')
    materials = [item for item in store.list_materials(conn, task_id, owner_id)
                 if not item['excluded']]
    messages = store.list_messages(conn, task_id, owner_id)
    return {'scope': store.get_scope(conn, task_id, owner_id), 'materials': materials,
            'history': messages[-llm.HISTORY_LIMIT:]}
