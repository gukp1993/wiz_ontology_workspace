"""隔离片段测试的纯范围/输入计划（20260919_函数编排配置与调试优化）。

`POST /api/flow-run` 带 `testMode="isolated"` 时的请求预处理：不执行任何节点、
不访问网络/数据库/凭据，只做——
* plan_scope()：请求结构与节点集合校验（存在、无重复、处理节点、无环、连通）、
  范围子图规划（拓扑序 orderedTargets、范围外 excludedIds）、外部输入分类、
  inputOverrides 合法性（禁止覆盖内部依赖/固定来源/入口参数来源，未知项拒绝）。
* precheck_values()：入口参数值与边界覆盖值按目标输入声明校验；缺值/类型不符
  在任一节点真实执行前拒绝（422）。
* bounded_preview()：节点实际输入的有界预览（展示用，不改变执行值，绝不抛异常）。

契约见 文档/接口文档/04-编排与LLM接口.md §3.1。PlanError.status 由路由层映射
状态码（400 结构层 → 校验报告 422 → precheck 422 的分层见需求 §10）；本模块
保持纯函数，便于直接单测与前端镜像语义。
"""
import json

from workbench import flows

MAX_LIST_PREVIEW_ITEMS = 100
PREVIEW_BYTES = 64 * 1024


class PlanError(Exception):
    """计划/预检失败：status 为应返回的 HTTP 状态码（400/422）。"""

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def _is_processing(node):
    return isinstance(node, dict) and node.get('kind') in flows.NODE_KINDS


def _has_override(overrides, node_id, input_id):
    return node_id in overrides and input_id in overrides[node_id]


def plan_scope(state, targets, overrides):
    """结构层计划（400 层）：范围、顺序、外部输入声明与覆盖合法性。

    externalInputs 元素：{nodeId, nodeIdName, inputId, label, type, kind, required}
    kind ∈ external（来源节点在范围外，required=True）/ unbound（未绑定，可选提供）。
    """
    index = {n['id']: n for n in state.get('nodes', []) if isinstance(n, dict)}
    if not isinstance(targets, list) or not targets:
        raise PlanError(400, 'isolated 测试必须提供非空 targets（节点 ID 列表）')
    if not all(isinstance(t, str) and t for t in targets):
        raise PlanError(400, 'targets 必须是非空字符串节点 ID 列表')
    if len(set(targets)) != len(targets):
        raise PlanError(400, 'targets 存在重复节点 ID')
    unknown = [t for t in targets if t not in index]
    if unknown:
        raise PlanError(400, '被测节点不存在：' + '、'.join(unknown))
    not_processing = [t for t in targets if not _is_processing(index[t])]
    if not_processing:
        raise PlanError(400, '被测节点必须是处理节点：' + '、'.join(not_processing))
    if overrides is None:
        overrides = {}
    if not isinstance(overrides, dict) or not all(
            isinstance(node_id, str) and isinstance(items, dict) and all(isinstance(k, str) for k in items)
            for node_id, items in overrides.items()):
        raise PlanError(400, 'inputOverrides 必须是 {节点ID: {输入ID: 值}} 形式的对象')
    unknown_override_nodes = [node_id for node_id in overrides if node_id not in targets]
    if unknown_override_nodes:
        raise PlanError(400, 'inputOverrides 引用了范围外节点：' + '、'.join(unknown_override_nodes))

    selected = set(targets)
    deps = {}
    for node_id in targets:
        deps[node_id] = []
        for inp in index[node_id].get('inputs', []) or []:
            src = inp.get('source') if isinstance(inp, dict) else None
            if isinstance(src, dict) and src.get('kind') in ('node', 'nodeField') and src.get('nodeId') in selected:
                deps[node_id].append(src['nodeId'])

    # 环检测：Kahn 拓扑排序不完全 = 选中集合内有环
    pending = {node_id: set(deps[node_id]) for node_id in targets}
    order = []
    ready = sorted(node_id for node_id in targets if not pending[node_id])
    while ready:
        node_id = ready.pop(0)
        order.append(node_id)
        for other, waiting in pending.items():
            if node_id in waiting:
                waiting.discard(node_id)
                if not waiting and other not in order and other not in ready:
                    ready.append(other)
                    ready.sort()
    if len(order) != len(targets):
        stuck = sorted(node_id for node_id in targets if node_id not in order)
        raise PlanError(400, '被测节点集合存在循环依赖：' +
                        '、'.join(index[node_id].get('name') or node_id for node_id in stuck))

    # 连通性：选中集合按内部依赖边的无向连通必须只有一个分量
    adjacency = {node_id: set() for node_id in targets}
    for node_id, sources in deps.items():
        for src in sources:
            adjacency[node_id].add(src)
            adjacency[src].add(node_id)
    seen, components = set(), []
    for node_id in targets:
        if node_id in seen:
            continue
        component, stack = [], [node_id]
        seen.add(node_id)
        while stack:
            current = stack.pop()
            component.append(current)
            for nxt in adjacency[current]:
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        components.append(sorted(component, key=order.index))
    if len(components) > 1:
        parts = [' → '.join(index[node_id].get('name') or node_id for node_id in comp) for comp in components]
        raise PlanError(400, '被测节点集合不连通，请选择依赖相连的连续片段：' + '；'.join(parts))

    # 外部输入分类 + 覆盖合法性（400 层：未知输入/固定/内部/入口参数来源的覆盖一律拒绝）
    external_inputs = []
    entry_requirements = []
    for node_id in order:
        node = index[node_id]
        for inp in node.get('inputs', []) or []:
            if not isinstance(inp, dict) or not inp.get('id'):
                continue
            input_id = inp['id']
            label = inp.get('label') or inp.get('name') or input_id
            src = inp.get('source')
            kind = src.get('kind') if isinstance(src, dict) else None
            if kind == 'fixed':
                if _has_override(overrides, node_id, input_id):
                    raise PlanError(400, f'节点「{node.get("name") or node_id}」的输入「{label}」是固定来源，不允许测试覆盖')
                continue
            if kind == 'flowInput':
                if _has_override(overrides, node_id, input_id):
                    raise PlanError(400, f'节点「{node.get("name") or node_id}」的输入「{label}」由编排入口参数提供，'
                                         '请通过入口值赋值；入口值与本覆盖同用构成歧义')
                entry_param = next((i for i in state.get('inputs', []) or []
                                    if isinstance(i, dict) and i.get('id') == src.get('inputId')), None) or {}
                entry_requirements.append({'entryId': src.get('inputId'), 'entryName': entry_param.get('name') or '',
                                           'label': entry_param.get('label') or entry_param.get('name') or '入口参数',
                                           'nodeIdName': node.get('name') or node_id, 'inputLabel': label})
                continue
            if kind in ('node', 'nodeField'):
                if src.get('nodeId') in selected:
                    if _has_override(overrides, node_id, input_id):
                        raise PlanError(400, f'节点「{node.get("name") or node_id}」的输入「{label}」'
                                             '依赖范围内上游输出，不允许测试值覆盖')
                    continue
                external_inputs.append({'nodeId': node_id, 'nodeIdName': node.get('name') or node_id,
                                        'inputId': input_id, 'label': label, 'type': inp.get('type'),
                                        'kind': 'external', 'required': True})
                continue
            if kind is None:
                if _has_override(overrides, node_id, input_id):
                    external_inputs.append({'nodeId': node_id, 'nodeIdName': node.get('name') or node_id,
                                            'inputId': input_id, 'label': label, 'type': inp.get('type'),
                                            'kind': 'unbound', 'required': False})
                continue
            raise PlanError(400, f'节点「{node.get("name") or node_id}」的输入「{label}」来源类型未知')

    # 多余覆盖项：出现的 (节点,输入) 必须都已在 external_inputs 收录
    allowed = {(item['nodeId'], item['inputId']) for item in external_inputs}
    for node_id, items in overrides.items():
        for input_id in items:
            if (node_id, input_id) not in allowed:
                node_name = index.get(node_id, {}).get('name') or node_id
                inp = next((i for i in index.get(node_id, {}).get('inputs', []) or []
                            if isinstance(i, dict) and i.get('id') == input_id), None)
                label = (inp or {}).get('label') or (inp or {}).get('name') or input_id
                raise PlanError(400, f'inputOverrides 包含无效覆盖项：节点「{node_name}」的输入「{label}」'
                                     '（未知输入、范围内内部依赖或固定来源不允许覆盖）')

    excluded = [n['id'] for n in state.get('nodes', []) if _is_processing(n) and n['id'] not in selected]
    return {'orderedTargets': order, 'externalInputs': external_inputs,
            'entryRequirements': entry_requirements,
            'excludedIds': excluded, 'selected': sorted(selected)}


def validate_test_value(decl, value, path=''):
    """按输入声明验证测试值（后端最终决定）。返回错误文案，None 表示通过。

    区分缺字段与显式空值：对象声明字段缺失 → 错误；显式 null 视为显式空值放行。
    数值不接受 NaN/Infinity 与布尔；对象/列表按声明递归验证字段类型。
    """
    kind = (decl or {}).get('type') if isinstance(decl, dict) else None
    where = f'（{path}）' if path else ''
    if value is None:
        return None
    if kind == 'number':
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return f'需要数值{where}'
        if isinstance(value, float) and (value != value or value in (float('inf'), float('-inf'))):
            return f'数值不能是 NaN 或无穷{where}'
        return None
    if kind == 'boolean':
        return None if isinstance(value, bool) else f'需要是/否（布尔）{where}'
    if kind == 'text':
        return None if isinstance(value, str) else f'需要文本{where}'
    if kind == 'datetime':
        return None if isinstance(value, str) else f'需要日期时间文本{where}'
    if kind == 'object':
        if not isinstance(value, dict):
            return f'需要对象（JSON object）{where}'
        for field in decl.get('fields') or []:
            if not isinstance(field, dict) or not field.get('name'):
                continue
            if field['name'] not in value:
                return f'对象缺少声明字段「{field.get("label") or field["name"]}」{where}'
            sub = validate_test_value(field.get('type'), value[field['name']],
                                      (path + '.' if path else '') + (field.get('label') or field['name']))
            if sub:
                return sub
        return None
    if kind == 'list':
        if not isinstance(value, list):
            return f'需要列表（JSON array）{where}'
        for i, element in enumerate(value):
            sub = validate_test_value(decl.get('elementType'), element, f'{path or "列表"}[{i + 1}]')
            if sub:
                return sub
        return None
    return f'输入数据类型未知{where}' if kind else None


def precheck_values(state, plan, entry_inputs, overrides):
    """取值层预检（422 层）：入口值与边界覆盖值按目标输入声明校验。

    返回 (entryValues, resolvedOverrides)：entryValues 同时按入口参数 id 与技术名
    提供键（兼容执行器既有解析）；resolvedOverrides 保持 {nodeId: {inputId: value}}。
    """
    if entry_inputs is None:
        entry_inputs = {}
    if not isinstance(entry_inputs, dict):
        raise PlanError(422, 'inputs（入口参数取值）必须是对象')
    flow_inputs = {i.get('id'): i for i in state.get('inputs', []) if isinstance(i, dict)}
    entry_values, known_keys = {}, set()
    for input_id, decl in flow_inputs.items():
        if not isinstance(decl, dict):
            continue
        name = decl.get('name')
        key_candidates = [k for k in (input_id, name) if k]
        present = any(k in entry_inputs for k in key_candidates)
        provided = next((entry_inputs[k] for k in key_candidates if k in entry_inputs), None)
        label = decl.get('label') or name or input_id
        if present:
            error = validate_test_value(decl.get('type'), provided, label)
            if error:
                raise PlanError(422, f'入口参数「{label}」{error}')
            entry_values[input_id] = provided
            if name:
                entry_values[name] = provided
            known_keys.update(key_candidates)
    stray = [k for k in entry_inputs if k not in known_keys]
    if stray:
        raise PlanError(422, 'inputs 包含本编排不存在的入口参数：' + '、'.join(map(str, stray[:5])))
    # 被测节点引用的入口参数必须提供（可多个输入复用同一入口值）
    for req in plan.get('entryRequirements', []):
        present = any(k in entry_inputs and k for k in (req['entryId'], req['entryName']))
        if not present:
            raise PlanError(422, f'节点「{req["nodeIdName"]}」的输入「{req["inputLabel"]}」'
                                 f'引用入口参数「{req["label"]}」，请提供该入口值')

    index = {n['id']: n for n in state.get('nodes', []) if isinstance(n, dict)}
    resolved = {}
    for item in plan['externalInputs']:
        node_overrides = overrides.get(item['nodeId'], {}) if isinstance(overrides, dict) else {}
        provided = node_overrides.get(item['inputId'])
        present = item['inputId'] in node_overrides
        if item['required'] and not present:
            raise PlanError(422, f'节点「{item["nodeIdName"]}」的输入「{item["label"]}」依赖范围外节点输出，'
                                 '请提供本次测试值')
        error = validate_test_value(item.get('type'), provided, f'节点「{item["nodeIdName"]}」输入「{item["label"]}」')
        if error:
            raise PlanError(422, error)
        resolved.setdefault(item['nodeId'], {})[item['inputId']] = provided
    return entry_values, resolved


def _safe_text(value):
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        try:
            return str(value)
        except Exception:
            return '（无法序列化）'


def bounded_preview(values):
    """节点实际输入的有界预览（展示用，绝不改变执行值，绝不抛异常）。

    列表每输入最多 100 条；整体序列化约 64 KiB，超限按字符截短并标记。
    返回 (preview, truncated)；preview 保持结构化值（截短后），不含任何凭据。
    """
    preview, truncated = {}, False
    budget = PREVIEW_BYTES
    for name, value in (values or {}).items():
        shown = value
        if isinstance(shown, list) and len(shown) > MAX_LIST_PREVIEW_ITEMS:
            shown = shown[:MAX_LIST_PREVIEW_ITEMS]
            truncated = True
        text = _safe_text(shown)
        encoded = len(text.encode('utf-8', 'replace'))
        if encoded > budget:
            limit = max(0, budget)
            while limit > 0 and len(text[:limit].encode('utf-8', 'replace')) > budget:
                limit = int(limit * 0.9)
            text = text[:limit] + '…（输入预览超限截断）'
            truncated = True
            budget = 0
        else:
            budget -= encoded
        preview[str(name)] = shown
    return preview, truncated
