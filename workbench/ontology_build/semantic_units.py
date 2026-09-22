"""本体生成控输出 v3：语义目标与来源 selector（D03 交付，纯函数模块）。

契约来源（冻结，不在本模块另立口径）：
* workbench/ontology_build/batch_contracts.py「目标 / 选择器」节（D00，提交 87d1473）：
  Target dict 形状、canonical_selector、target_digest、targetId 稳定派生、单元 kind 枚举；
* 需求文档《本体生成控输出_整合方案与并行开发计划_v3.md》§4.1（目标单元与证据上下文）、§8 D03 行；
* 接口文档 08 §1.3 Fact locator 按材料类型的真实字段形态。

职责：把存储 fact_view 形状的 fact dict 列表（{'id','taskId','materialId','locator',
'snippet','kind','data','quality'}）规划成 Target 与分组。只吃 fact dict 列表，
不 import 存储层/管线/演示模块，零副作用、零 IO，可被解析器与实验代码单独加载。

分组优先级与单元 kind（冻结，batch_contracts「分组优先级」注释的落地）：
① locator.nodeId 非空（JSON-LD 节点） → materialId+nodeId，kind=jsonld_node；
② kind=ddl 且 locator.table → materialId+表，kind=ddl_table；
③ kind=code 且 locator.symbol → materialId+限定符号（symbol 原样），kind=code_symbol；
④ locator.kind ∈ {docx,md,pdf} 且有章节 → materialId+章节，kind=doc_section
   （md/docx 取 locator.section；pdf 无 section 字段，按真实定位字段 locator.page
   记作 'page:<n>' 作为章节粒度）；
⑤ locator.kind ∈ {json,yaml,toml} 且 locator.path 非空 → materialId+最近对象路径
   （数组下标保留），kind=json_object。yaml/toml 与 json 同一遍历口径（接口 08 §1.3），
   共用 json_object 分组；
⑥ 以上都不满足 → 物料内相邻窗口：同 materialId、在输入顺序中连续的未定位事实合并为
   一组，kind=adjacent_window；窗口内只剩一个事实（孤立无组）时 kind=single_fact。
   两种 fallback 分组 groupingConfidence 一律 'low'；①–⑤ 分组为 'high'。

subjectKey 生成规则（分组键就是 subjectKey，冻结字符串格式）：
* jsonld_node    → 'jsonld:<materialId>#<nodeId>'
* ddl_table      → 'ddl:<materialId>#<table>'
* code_symbol    → 'code:<materialId>#<symbol>'        （symbol 为限定符号，原样拼接）
* doc_section    → 'doc:<materialId>#<section>'        （pdf 记 'doc:<materialId>#page:<n>'）
* json_object    → 'json:<materialId>#<最近对象路径>'   （深度 1 记 '<root>'）
* adjacent_window→ 'window:<materialId>#w<序号>'        （序号按该物料内 fallback 窗口
                                                          出现顺序从 1 递增）
* single_fact    → 'single:<materialId>#<factId>'
最近对象路径口径：path 按 '.' 切段（容忍 '$'/'.' 前缀）；末段自带数组下标（如
'items[2]'）时整个 path 原样保留（元素本身即最近对象候选，下标必须保留，否则不同数组
元素会被并成一个主体）；否则去掉最后一段叶子键，剩余部分即最近对象路径（如
'a.b[2].c' → 'a.b[2]'）；只剩一段叶子键时最近对象是根对象，subjectKey 记 '<root>'。

铁律：
* 每个 fact 至少产生一个 target（selector 默认 {'type':'whole'}）；绝不因值短、是数字、
  布尔或「像元数据」丢弃事实。fact 缺 id 属协议违例，抛 ValueError 拒绝静默丢弃。
* targetId = 't-' + sha256(factId + canonical_selector(selector)) 的前 16 个 hex 字符；
  同一 (factId, selector) 恒定，与调用次数、输入顺序无关。
* 本模块 v3 只产出 whole selector；field/range selector 由拆分阶段（D04 split_job 契约）
  派生。canonical_selector/target_digest 原样 re-export 自 batch_contracts，是 job id
  派生与覆盖比对的唯一口径，本模块不复制实现。
* 冻结清单之外的定位形态（xlsx/ini/csv/properties/text/image/llm 等）按⑥落入相邻窗口，
  不发明新单元 kind；要新增分组规则必须先改 batch_contracts + 接口文档 08（协议变更）。
"""
import hashlib

from workbench.ontology_build import batch_contracts as contracts

__all__ = ['canonical_selector', 'target_digest', 'build_targets', 'targets_for_facts',
           'context_fact_ids']

# 覆盖比对唯一口径（batch_contracts 冻结实现，原样转发）
canonical_selector = contracts.canonical_selector
target_digest = contracts.target_digest

TARGET_ID_PREFIX = 't-'
TARGET_ID_CHARS = 16
SELECTOR_WHOLE = {'type': 'whole'}
CONFIDENCE_HIGH = 'high'
CONFIDENCE_LOW = 'low'

KIND_JSONLD = 'jsonld_node'
KIND_DDL = 'ddl_table'
KIND_CODE = 'code_symbol'
KIND_DOC = 'doc_section'
KIND_JSON = 'json_object'
KIND_WINDOW = 'adjacent_window'
KIND_SINGLE = 'single_fact'

_DOC_LOCATOR_KINDS = ('docx', 'md', 'pdf')
_PATH_LOCATOR_KINDS = ('json', 'yaml', 'toml')


def _text(value):
    return str(value or '').strip()


def _locator_of(fact):
    locator = fact.get('locator')
    return locator if isinstance(locator, dict) else {}


def _locator_kind(fact, locator):
    """定位家族：locator.kind 优先，缺失时回退 fact.kind（两者都缺 = 未定位）。"""
    return _text(locator.get('kind')) or _text(fact.get('kind'))


def object_path_of(path):
    """键路径 → 最近对象路径（数组下标保留），规则见模块 docstring；空段记 ''。"""
    text = _text(path)
    if text.startswith('$'):
        text = text[1:]
    text = text.strip('.')
    segments = [segment for segment in text.split('.') if segment]
    if not segments:
        return ''
    if '[' in segments[-1]:
        return text            # 末段是数组元素（如 items[2]）：下标保留，原样即最近对象
    if len(segments) >= 2:
        return '.'.join(segments[:-1])   # 去掉最后一段叶子键
    return ''                  # 深度 1：最近对象是根对象


def _classify(fact):
    """fact → (kind, subjectKey, located)；located=False 表示落入 fallback 相邻窗口。"""
    locator = _locator_of(fact)
    material = _text(fact.get('materialId'))
    node_id = _text(locator.get('nodeId'))
    if node_id:                                          # ① JSON-LD：materialId+nodeId
        return KIND_JSONLD, 'jsonld:%s#%s' % (material, node_id), True
    loc_kind = _locator_kind(fact, locator)
    if loc_kind == 'ddl':
        table = _text(locator.get('table'))
        if table:                                        # ② DDL：materialId+表
            return KIND_DDL, 'ddl:%s#%s' % (material, table), True
        return '', '', False
    if loc_kind == 'code':
        symbol = _text(locator.get('symbol'))
        if symbol:                                       # ③ 代码：materialId+限定符号
            return KIND_CODE, 'code:%s#%s' % (material, symbol), True
        return '', '', False
    if loc_kind in _DOC_LOCATOR_KINDS:
        section = _text(locator.get('section'))
        if not section and loc_kind == 'pdf':
            page = _text(locator.get('page'))
            if page:
                section = 'page:%s' % page               # ④ 文档：pdf 按页作章节粒度
        if section:
            return KIND_DOC, 'doc:%s#%s' % (material, section), True
        return '', '', False
    if loc_kind in _PATH_LOCATOR_KINDS:
        path = _text(locator.get('path'))
        if path:                                         # ⑤ 普通键路径：最近对象路径
            return KIND_JSON, 'json:%s#%s' % (material, object_path_of(path) or '<root>'), True
        return '', '', False
    return '', '', False                                 # ⑥ 未定位 → 相邻窗口


def _target_id(fact_id, selector):
    """冻结公式：'t-' + sha256(factId + canonical_selector(selector))[:16]（hex）。"""
    payload = str(fact_id) + contracts.canonical_selector(selector)
    return TARGET_ID_PREFIX + hashlib.sha256(payload.encode('utf-8')).hexdigest()[:TARGET_ID_CHARS]


def build_targets(facts):
    """fact dict 列表 → {'targets':[Target], 'groups':[...], 'stats':{...}}（签名冻结）。

    Target dict 与 batch_contracts「目标 / 选择器」节逐字段一致：
    {'targetId','factId','selector','kind','subjectKey','materialId','groupingConfidence'}。
    groups 条目：{'subjectKey','targetIds','confidence'}，按 subjectKey 首次出现排序；
    stats：{'factCount','targetCount','lowConfidenceGroups'}。

    保证：targets 的 factId 集合 == 输入 fact id 集合（每个 fact 恰一个 whole target）；
    fact 非 dict 或缺 id 时抛 ValueError（拒绝静默丢弃）。纯函数：同输入恒同输出。
    """
    items = []
    for position, fact in enumerate(facts or []):
        if not isinstance(fact, dict):
            raise ValueError('fact[%d] 不是 dict：拒绝静默丢弃事实' % position)
        fact_id = _text(fact.get('id'))
        if not fact_id:
            raise ValueError('fact[%d] 缺 id：拒绝静默丢弃事实' % position)
        kind, subject_key, located = _classify(fact)
        items.append({'fact': fact, 'factId': fact_id, 'kind': kind,
                      'subjectKey': subject_key, 'located': located})

    # ⑥ fallback 相邻窗口：同 materialId 且输入顺序连续的未定位事实合并为一个窗口。
    runs = []
    run_material = None
    run_indexes = []
    for idx, item in enumerate(items):
        material = _text(item['fact'].get('materialId'))
        if item['located']:
            if run_indexes:
                runs.append((run_material, run_indexes))
                run_material, run_indexes = None, []
            continue
        if run_indexes and material == run_material:
            run_indexes.append(idx)
        else:
            if run_indexes:
                runs.append((run_material, run_indexes))
            run_material, run_indexes = material, [idx]
    if run_indexes:
        runs.append((run_material, run_indexes))

    window_seq = {}
    for material, indexes in runs:
        if len(indexes) >= 2:
            window_seq[material] = window_seq.get(material, 0) + 1
            subject_key = 'window:%s#w%d' % (material, window_seq[material])
            for idx in indexes:
                items[idx]['kind'] = KIND_WINDOW
                items[idx]['subjectKey'] = subject_key
        else:
            item = items[indexes[0]]
            item['kind'] = KIND_SINGLE
            item['subjectKey'] = 'single:%s#%s' % (material, item['factId'])

    targets = []
    groups = []
    group_by_key = {}
    for item in items:
        material = _text(item['fact'].get('materialId'))
        confidence = CONFIDENCE_HIGH if item['located'] else CONFIDENCE_LOW
        target = {
            'targetId': _target_id(item['factId'], SELECTOR_WHOLE),
            'factId': item['factId'],
            'selector': dict(SELECTOR_WHOLE),
            'kind': item['kind'],
            'subjectKey': item['subjectKey'],
            'materialId': material,
            'groupingConfidence': confidence,
        }
        targets.append(target)
        group = group_by_key.get(item['subjectKey'])
        if group is None:
            group = {'subjectKey': item['subjectKey'], 'targetIds': [], 'confidence': confidence}
            group_by_key[item['subjectKey']] = group
            groups.append(group)
        group['targetIds'].append(target['targetId'])
    return {
        'targets': targets,
        'groups': groups,
        'stats': {'factCount': len(items), 'targetCount': len(targets),
                  'lowConfidenceGroups': sum(1 for group in groups
                                             if group['confidence'] == CONFIDENCE_LOW)},
    }


def targets_for_facts(facts, fact_ids):
    """过滤辅助（签名冻结）：只保留 factId 落在给定集合内的 targets（保持输入顺序）。

    内部走 build_targets，保证与计划目标同源同形；fact_ids 可传任意可迭代（集合/列表），
    未知 id 静默无匹配（返回 []），不报错——调用方用它做"只取这些事实的目标"。
    """
    wanted = {str(fact_id) for fact_id in (fact_ids or [])}
    if not wanted:
        return []
    result = build_targets(facts)
    return [target for target in result['targets'] if target['factId'] in wanted]


def context_fact_ids(targets, groups, max_context=8):
    """为一组主目标选可复用上下文 factId（签名冻结）。

    参数口径（groups 冻结形状只有 targetIds、无 factId，故两者必须这样配合）：
    * targets —— 同一次 build_targets 输出的**全量** targets，是 targetId→factId/subjectKey
      的唯一解析来源；
    * groups —— 本叶任务的主目标组（"一组 target"）：组 dict 形状与 build_targets 输出一致，
      targetIds 即本轮主目标（典型是 primaryTargets 所在组，split 后可为该组的子集）。
    规则：主 subjectKey = groups 各组的 subjectKey；上下文 = 全量 targets 中 subjectKey
    落在主 subjectKey、且 targetId 不属于任何主组的"同主体邻近事实"，按 targets 顺序
    去重收集，最多 max_context 个。主组已覆盖整个主体时上下文为空（不为已覆盖部分造上下文）。
    纪律：上下文只供理解（进叶 job 的 contextFactIds），**不算覆盖**——调用方不得把返回的
    factId 计入 primaryTargets/coverage；本函数机械保证返回值与主组 factId 不相交。
    """
    limit = max(0, int(max_context or 0))
    if limit == 0 or not groups:
        return []
    primary_target_ids = set()
    primary_keys = set()
    for group in groups:
        if not isinstance(group, dict):
            continue
        primary_keys.add(group.get('subjectKey'))
        primary_target_ids.update(group.get('targetIds') or [])
    selected = []
    seen = set()
    for target in targets or []:
        if len(selected) >= limit:
            break
        if not isinstance(target, dict) or target.get('subjectKey') not in primary_keys:
            continue
        if target.get('targetId') in primary_target_ids:
            continue
        fact_id = target.get('factId')
        if not fact_id or fact_id in seen:
            continue
        seen.add(fact_id)
        selected.append(fact_id)
    return selected
