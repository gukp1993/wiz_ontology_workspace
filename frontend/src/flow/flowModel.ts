// 函数编排前端协议层（一期）。类型/来源/连线等纯数据与纯函数，与 workbench/flows.py
// 镜像：类型体系（文本/数值/是-否/日期时间/对象/列表，对象嵌套≤3 层）、四种输入来源、
// 连线从绑定派生、相容性预判、环预判、删除影响面。改协议两端必须同步。
// 业务事实只存绑定，画布坐标只存 layout；显示名变化不影响任何引用（引用全部走稳定 ID）。
export const SCALAR_TYPES = ['text', 'number', 'boolean', 'datetime']
export const TYPE_LABELS: Record<string, string> = {
  text: '文本', number: '数值', boolean: '是/否', datetime: '日期时间', object: '对象', list: '列表',
}
export const TYPE_OPTIONS = Object.entries(TYPE_LABELS).map(([value, label]) => ({ value, label }))
export const MAX_OBJECT_DEPTH = 3
export const INPUT_NODE = 'flow-input'
export const OUTPUT_NODE = 'flow-output'
export const NODE_KIND_LABELS: Record<string, string> = { python: 'Python', sql: 'SQL', redis: 'Redis', http: 'HTTP', calc: '计算', input: '编排输入', output: '编排输出' }
export const NODE_KINDS = ['python', 'sql', 'redis', 'http', 'calc']
export const HTTP_METHODS = ['GET', 'POST', 'PUT', 'DELETE']
/** Redis 白名单命令表（与 flows.REDIS_COMMANDS 镜像）：命令 → (最少参数, 最多参数 null=不限, 返回类型)。 */
export const REDIS_COMMANDS: Record<string, { min: number; max: number | null; returns: string }> = {
  GET: { min: 1, max: 1, returns: '文本' }, MGET: { min: 1, max: null, returns: '列表' },
  EXISTS: { min: 1, max: null, returns: '数值' }, TTL: { min: 1, max: 1, returns: '数值' },
  TYPE: { min: 1, max: 1, returns: '文本' }, STRLEN: { min: 1, max: 1, returns: '数值' },
  HGET: { min: 2, max: 2, returns: '文本' }, HGETALL: { min: 1, max: 1, returns: '对象' },
  HMGET: { min: 2, max: null, returns: '列表' }, HKEYS: { min: 1, max: 1, returns: '列表' },
  HVALS: { min: 1, max: 1, returns: '列表' }, HLEN: { min: 1, max: 1, returns: '数值' },
  LRANGE: { min: 3, max: 3, returns: '列表' }, LLEN: { min: 1, max: 1, returns: '数值' },
  SISMEMBER: { min: 2, max: 2, returns: '是/否' }, SMEMBERS: { min: 1, max: 1, returns: '列表' },
  SCARD: { min: 1, max: 1, returns: '数值' }, ZSCORE: { min: 2, max: 2, returns: '数值' },
  ZRANGE: { min: 3, max: 3, returns: '列表' }, ZCARD: { min: 1, max: 1, returns: '数值' },
  SET: { min: 2, max: 3, returns: '文本' }, SETEX: { min: 3, max: 3, returns: '文本' },
  SETNX: { min: 2, max: 2, returns: '数值' }, DEL: { min: 1, max: null, returns: '数值' },
  INCR: { min: 1, max: 1, returns: '数值' }, DECR: { min: 1, max: 1, returns: '数值' },
  HSET: { min: 3, max: null, returns: '数值' }, HMSET: { min: 2, max: null, returns: '文本' },
  LPUSH: { min: 2, max: null, returns: '数值' }, RPUSH: { min: 2, max: null, returns: '数值' },
  SADD: { min: 2, max: null, returns: '数值' }, ZADD: { min: 3, max: null, returns: '数值' },
  EXPIRE: { min: 2, max: 2, returns: '数值' },
}
export const REDIS_COMMAND_OPTIONS = Object.entries(REDIS_COMMANDS).map(([value, spec]) => ({
  value, label: `${value}（${spec.min}${spec.max ? '–' + spec.max : '+'} 参数 → ${spec.returns}）`,
}))
export const EXEC_MAX_TIMEOUT_MS = 300000
export const SQL_MAX_ROWS_MAX = 10000
export const EXEC_DEFAULT_TIMEOUT: Record<string, number> = { python: 60000, sql: 30000, redis: 30000, http: 15000, calc: 30000 }
/** 流类型 → calc-expression 类型；null 表示不可进公式（datetime/object/list）。 */
export const flowTypeToCalcType = (flowType?: string): string | null =>
  flowType === 'number' ? 'number' : flowType === 'text' ? 'string' : flowType === 'boolean' ? 'boolean' : null
export const CALC_TYPE_LABELS: Record<string, string> = { number: '数值', string: '文本', boolean: '是/否' }

export const uid = (): string => crypto.randomUUID()
export const clone = <T,>(value: T): T => JSON.parse(JSON.stringify(value))

export const scalarType = (type: string) => ({ type })
export const textType = () => scalarType('text')

export function typeSummary(decl: any): string {
  if (!decl || typeof decl !== 'object' || !decl.type) return '—'
  if (decl.type === 'object') {
    const count = (decl.fields || []).length
    return `对象${count ? `(${count} 字段)` : '(结构待完善)'}`
  }
  if (decl.type === 'list') return `列表<${typeSummary(decl.elementType)}>`
  return TYPE_LABELS[decl.type] || decl.type
}

/** 新节点：按类型注入实现骨架；之后参数变化不回写正文。 */
export function blankNode(kind: string): any {
  const base = { id: uid(), kind, name: `${NODE_KIND_LABELS[kind] || kind}节点`, description: '', inputs: [], outputs: [] }
  if (kind === 'python') return { ...base, name: '新 Python 节点', implementation: { language: 'python', code: pythonSkeleton([], []) } }
  if (kind === 'sql') return { ...base, name: '新 SQL 节点', implementation: { language: 'sql', sql: '', connectionId: '' } }
  if (kind === 'redis') return { ...base, name: '新 Redis 节点', implementation: { language: 'redis', connectionId: '', keyTemplate: '', command: '', args: [] } }
  if (kind === 'http') return { ...base, name: '新 HTTP 节点', implementation: { language: 'http', method: 'GET', url: '', headers: {}, bodyMode: 'none', body: '', credentialId: '', responsePath: '' } }
  return { ...base, name: '新计算节点', implementation: { language: 'calc', mode: 'formula', formulas: {}, llmInstruction: '', providerId: '' } }
}

export function pythonSkeleton(inputs: any[], outputs: any[]): string {
  const params = (inputs || []).map((i: any) => i.name).filter(Boolean).join(', ')
  const first = (outputs || []).map((o: any) => o.name).find(Boolean)
  const ret = first ? `{"${first}": None}` : 'None'
  return `def main(${params}):\n    # 本工作台不运行代码：保存后由所选 LLM 代为求值\n    return ${ret}\n`
}

export const processingNodes = (state: any): any[] =>
  (state?.nodes || []).filter((n: any) => n && NODE_KINDS.includes(n.kind))

/** 节点显示名（含边界节点）；测试视图与结果展示用。 */
export function nameOfFlowNode(state: any, id: string): string {
  if (id === INPUT_NODE) return '编排输入'
  if (id === OUTPUT_NODE) return '编排输出'
  return (state?.nodes || []).find((n: any) => n.id === id)?.name || id
}

/** 计算节点公式模式：每个数值/文本/是否输出一条公式，引用输入用 {技术名}。 */
export const calcFormulaHint = (state: any, node: any): string => {
  const refs = (node?.inputs || []).filter((i: any) => flowTypeToCalcType(i.type?.type)).map((i: any) => `{${i.name}}`)
  return refs.length ? `可用参数：${refs.join('、')}` : '（尚无数值/文本/是否类型的输入可引用）'
}

/** 对象声明内按稳定 ID 路径取字段类型；路径不存在返回 null。 */
export function fieldOf(decl: any, path: string[]): any | null {
  let current = decl
  for (const id of path || []) {
    if (!current || current.type !== 'object') return null
    const match = (current.fields || []).find((f: any) => f.id === id)
    if (!match) return null
    current = match.type
  }
  return current
}

/** 解析输入来源的实际类型；返回 decl=null 表示无法解析（error 说明原因）。 */
export function resolveSourceType(state: any, src: any): { decl: any | null; error: string; nodeId: string | null } {
  if (!src || typeof src !== 'object') return { decl: null, error: '输入来源声明无效', nodeId: null }
  if (src.kind === 'flowInput') {
    const inp = (state.inputs || []).find((i: any) => i.id === src.inputId)
    if (!inp) return { decl: null, error: '引用了不存在的编排入口参数', nodeId: null }
    return { decl: inp.type, error: '', nodeId: null }
  }
  if (src.kind === 'fixed') {
    if (!SCALAR_TYPES.includes(src.valueType)) return { decl: null, error: '固定值数据类型无效', nodeId: null }
    return { decl: { type: src.valueType }, error: '', nodeId: null }
  }
  if (src.kind === 'node' || src.kind === 'nodeField') {
    const node = (state.nodes || []).find((n: any) => n.id === src.nodeId)
    if (!node) return { decl: null, error: '引用了不存在的节点', nodeId: null }
    const output = (node.outputs || []).find((o: any) => o.id === src.outputId)
    if (!output) return { decl: null, error: '引用了该节点不存在的输出', nodeId: null }
    if (src.kind === 'node') return { decl: output.type, error: '', nodeId: node.id }
    const decl = fieldOf(output.type, src.fieldPath || [])
    if (!decl) return { decl: null, error: '引用了输出中不存在的对象字段', nodeId: node.id }
    return { decl, error: '', nodeId: node.id }
  }
  return { decl: null, error: '输入来源类型未知', nodeId: null }
}

/** 与 flows.types_compatible 镜像：类型名一致；对象按字段技术名逐一同名匹配（稳定 ID 用于
 * 绑定路径；独立声明的同结构对象视为相容，显示名不影响判断）。 */
export function typesCompatible(target: any, source: any): { ok: boolean; reason: string } {
  if (!target || !source) return { ok: false, reason: '类型声明无效' }
  if (target.type !== source.type) return { ok: false, reason: `来源 ${TYPE_LABELS[source.type] || '?'} 与目标 ${TYPE_LABELS[target.type] || '?'} 不一致` }
  if (target.type === 'object') {
    const sourceFields = new Map<string, any>((source.fields || []).filter((f: any) => f.name).map((f: any) => [f.name, f]))
    for (const field of target.fields || []) {
      if (!field?.name) continue
      const match = sourceFields.get(field.name)
      if (!match) return { ok: false, reason: `来源缺少字段「${field.label || field.name}」` }
      const nested = typesCompatible(field.type, match.type)
      if (!nested.ok) return { ok: false, reason: `字段「${field.label || field.name}」${nested.reason}` }
    }
  } else if (target.type === 'list') {
    return typesCompatible(target.elementType, source.elementType)
  }
  return { ok: true, reason: '' }
}

export function fixedValueDisplay(src: any): string {
  if (src.valueType === 'boolean') return src.value ? '是（true）' : '否（false）'
  if (src.value === '') return '空字符串 ""'
  return String(src.value)
}

export function sourceSummary(state: any, src: any): string {
  if (!src) return '未绑定'
  if (src.kind === 'flowInput') {
    const inp = (state.inputs || []).find((i: any) => i.id === src.inputId)
    return `入口参数 · ${inp ? (inp.label || inp.name) : '已失效'}`
  }
  if (src.kind === 'fixed') return `固定值 · ${fixedValueDisplay(src)}`
  if (src.kind === 'node' || src.kind === 'nodeField') {
    const node = (state.nodes || []).find((n: any) => n.id === src.nodeId)
    const output = node && (node.outputs || []).find((o: any) => o.id === src.outputId)
    let text = `「${node ? node.name : '已失效节点'}」· ${output ? (output.label || output.name) : '已失效输出'}`
    if (src.kind === 'nodeField') {
      const decl = output ? fieldOf(output.type, src.fieldPath || []) : null
      const pathLabels = (src.fieldPath || []).map((fid: string) => {
        for (const f of collectFields(output?.type)) if (f.id === fid) return f.label || f.name
        return '?'
      })
      text += '.' + pathLabels.join('.') + (decl ? '' : '（已失效）')
    }
    return text
  }
  return '未知来源'
}

function collectFields(decl: any, out: any[] = []): any[] {
  if (!decl || typeof decl !== 'object') return out
  if (decl.type === 'object') {
    for (const f of decl.fields || []) { out.push(f); collectFields(f.type, out) }
  } else if (decl.type === 'list') collectFields(decl.elementType, out)
  return out
}

/** 连线 = 绑定的派生视图：绑定到节点输出/其字段 → 节点间依赖边；绑定到编排入口参数 → 编排输入边界边。 */
export function derivedEdges(state: any): { id: string; source: string; target: string; label: string; nodeId: string; inputId: string }[] {
  const edges: any[] = []
  for (const node of state?.nodes || []) {
    for (const input of node.inputs || []) {
      const src = input.source
      if (!src) continue
      if (src.kind === 'flowInput') {
        const inp = (state.inputs || []).find((i: any) => i.id === src.inputId)
        if (!inp) continue
        edges.push({ id: `dep:${node.id}:${input.id}`, source: INPUT_NODE, target: node.id,
                     label: inp.label || inp.name || '?', nodeId: node.id, inputId: input.id })
        continue
      }
      if (src.kind !== 'node' && src.kind !== 'nodeField') continue
      // 引用失效（来源节点/输出已不存在）时不生成边：cy.add 遇到缺失端点会抛错且中断整个画布
      const srcNode = (state.nodes || []).find((n: any) => n.id === src.nodeId)
      if (!srcNode) continue
      const output = (srcNode.outputs || []).find((o: any) => o.id === src.outputId)
      if (!output) continue
      let label = output.label || output.name || '?'
      if (src.kind === 'nodeField') {
        const path = (src.fieldPath || []).map((fid: string) => {
          const f = collectFields(output?.type).find((x: any) => x.id === fid)
          return f ? (f.label || f.name) : '?'
        })
        label += '.' + path.join('.')
      }
      edges.push({ id: `dep:${node.id}:${input.id}`, source: src.nodeId, target: node.id, label, nodeId: node.id, inputId: input.id })
    }
  }
  return edges
}

/** 依赖图（target 依赖 source）。加入 source→target 的边是否成环。 */
export function wouldCreateCycle(state: any, sourceId: string, targetId: string): boolean {
  if (sourceId === targetId) return true
  const deps = new Map<string, Set<string>>()
  for (const edge of derivedEdges(state)) {
    if (!deps.has(edge.target)) deps.set(edge.target, new Set())
    deps.get(edge.target)!.add(edge.source)
  }
  const seen = new Set<string>()
  const stack = [sourceId]
  while (stack.length) {
    const current = stack.pop()!
    if (current === targetId) return true
    if (seen.has(current)) continue
    seen.add(current)
    for (const next of deps.get(current) || []) stack.push(next)
  }
  return false
}

/** 可作为来源的节点输出（排除自身节点）；列表整体传递，不展开元素；对象输出附字段。 */
export function outputCandidates(state: any, excludeNodeId: string | null): { nodeId: string; nodeName: string; output: any }[] {
  const out: any[] = []
  for (const node of processingNodes(state)) {
    if (excludeNodeId && node.id === excludeNodeId) continue
    for (const output of node.outputs || []) out.push({ nodeId: node.id, nodeName: node.name, output })
  }
  return out
}

/** 删除节点的影响面（人读文案），供确认对话框列出。 */
export function nodeRemovalImpact(state: any, nodeId: string): string[] {
  const impact: string[] = []
  for (const node of state?.nodes || []) {
    if (node.id === nodeId) continue
    for (const input of node.inputs || []) {
      if (input.source && (input.source.kind === 'node' || input.source.kind === 'nodeField') && input.source.nodeId === nodeId)
        impact.push(`节点「${node.name}」的输入「${input.label || input.name}」将变为未绑定`)
    }
  }
  for (const out of state?.outputs || []) {
    if (out.binding && (out.binding.kind === 'node' || out.binding.kind === 'nodeField') && out.binding.nodeId === nodeId)
      impact.push(`编排输出「${out.label || out.name}」将失去来源`)
  }
  return impact
}

/** 删除编排入口参数的影响面。 */
export function flowInputRemovalImpact(state: any, inputId: string): string[] {
  const impact: string[] = []
  for (const node of state?.nodes || []) {
    for (const input of node.inputs || []) {
      if (input.source && input.source.kind === 'flowInput' && input.source.inputId === inputId)
        impact.push(`节点「${node.name}」的输入「${input.label || input.name}」将变为未绑定`)
    }
  }
  return impact
}

/** 删除节点输出（或编排入口参数）对绑定到该输出的引用的影响。 */
export function outputRemovalImpact(state: any, nodeId: string, outputId: string): string[] {
  const impact: string[] = []
  for (const node of state?.nodes || []) {
    if (node.id === nodeId) continue
    for (const input of node.inputs || []) {
      if (input.source && (input.source.kind === 'node' || input.source.kind === 'nodeField') &&
          input.source.nodeId === nodeId && input.source.outputId === outputId)
        impact.push(`节点「${node.name}」的输入「${input.label || input.name}」将变为未绑定`)
    }
  }
  for (const out of state?.outputs || []) {
    if (out.binding && (out.binding.kind === 'node' || out.binding.kind === 'nodeField') &&
        out.binding.nodeId === nodeId && out.binding.outputId === outputId)
      impact.push(`编排输出「${out.label || out.name}」将失去来源`)
  }
  return impact
}

/** 删除逻辑连接的影响面。 */
export function connectionRemovalImpact(state: any, connectionId: string): string[] {
  const impact: string[] = []
  for (const node of processingNodes(state)) {
    if (node.kind === 'sql' && node.implementation && node.implementation.connectionId === connectionId)
      impact.push(`SQL 节点「${node.name}」将失去逻辑连接`)
  }
  return impact
}

// --- 节点/链测试（/api/flow-run {targets}）辅助 ---------------------------------

/** 配置签名：包含影响校验与执行的配置，排除 layout（缩放/坐标/视口）。
 * 用于检查/运行结果的新鲜度判定——签名变了即“待重新检查/上次快照”。 */
export function configSignature(state: any): string {
  if (!state || typeof state !== 'object') return ''
  return JSON.stringify([
    state.flowId, state.name, state.description, state.inputs, state.outputs,
    (state.nodes || []).map((n: any) => [n.id, n.kind, n.name, n.description, n.inputs, n.outputs, n.implementation, n.execution]),
    state.connections,
  ])
}

/** 需要补选的上游闭包：ids 执行所缺的全部传递上游（不包含 ids 自身）。 */
export function missingUpstreams(state: any, ids: string[]): string[] {
  const nodes = new Map<string, any>((state?.nodes || []).map((n: any) => [n.id, n]))
  const have = new Set(ids.filter(id => nodes.has(id)))
  const required: string[] = []
  const queue = [...have]
  while (queue.length) {
    const id = queue.shift()!
    const node = nodes.get(id)
    for (const input of node?.inputs || []) {
      const src = input?.source
      if (src && (src.kind === 'node' || src.kind === 'nodeField') && src.nodeId && !have.has(src.nodeId)) {
        have.add(src.nodeId)
        required.push(src.nodeId)
        queue.push(src.nodeId)
      }
    }
  }
  return required
}

/** 范围内会产生真实外部副作用的节点摘要（写 SQL/写 Redis/非 GET HTTP/LLM 推演）。 */
export function writeCapabilities(state: any, ids: string[]): string[] {
  const out: string[] = []
  for (const id of ids) {
    const node = (state?.nodes || []).find((n: any) => n.id === id)
    if (!node) continue
    const impl = node.implementation || {}
    const exec = node.execution || {}
    if (node.kind === 'sql') {
      if (exec.allowWrite) out.push(`SQL 节点「${node.name}」将执行写操作（DML）`)
    } else if (node.kind === 'redis') {
      const writeCommands = new Set(['SET', 'SETEX', 'SETNX', 'DEL', 'INCR', 'DECR', 'HSET', 'HMSET', 'LPUSH', 'RPUSH', 'SADD', 'ZADD', 'EXPIRE'])
      if (writeCommands.has(String(impl.command || '').toUpperCase())) out.push(`Redis 节点「${node.name}」将执行 ${impl.command} 写命令`)
    } else if (node.kind === 'http') {
      if (String(impl.method || 'GET').toUpperCase() !== 'GET') out.push(`HTTP 节点「${node.name}」将发送 ${impl.method} 请求`)
    } else if (node.kind === 'python') {
      out.push(`Python 节点「${node.name}」由大模型推演（非本地执行）`)
    }
  }
  return out
}

export const SECTION_LABELS: Record<string, string> = { inputs: '输入', implementation: '实现', outputs: '输出', advanced: '高级' }

/** 测试输入的类型校验与取值。布尔未选不算 false；对象/数组需合法 JSON。 */
export function validateTestValue(decl: any, raw: any): { ok: boolean; value: any; error: string } {
  const t = decl?.type
  if (t === 'number') {
    if (raw === '' || raw == null) return { ok: true, value: null, error: '' }
    const num = Number(raw)
    return Number.isFinite(num) ? { ok: true, value: num, error: '' } : { ok: false, value: null, error: '需要数值' }
  }
  if (t === 'boolean') {
    if (raw === '' || raw == null) return { ok: false, value: null, error: '必填的是/否尚未选择' }
    return { ok: true, value: raw === true || raw === 'true', error: '' }
  }
  if (t === 'object' || t === 'list') {
    const sample = t === 'object' ? '{}' : '[]'
    if (raw === '' || raw == null) return { ok: false, value: null, error: `必填的${t === 'object' ? '对象' : '数组'}尚未填写（示例 ${sample}）` }
    try { return { ok: true, value: JSON.parse(String(raw)), error: '' } }
    catch { return { ok: false, value: null, error: '不是合法 JSON' } }
  }
  if (raw === '' || raw == null) return { ok: true, value: '', error: '' }
  return { ok: true, value: String(raw), error: '' }
}

export function ensureLayout(state: any): void {
  if (!state.layout || typeof state.layout !== 'object') state.layout = {}
  if (!state.layout.positions || typeof state.layout.positions !== 'object') state.layout.positions = {}
}

// --- 节点/链测试（/api/flow-run {targets}）辅助 ---------------------------------

/** 被测集合按依赖拓扑排序；集合不成链（依赖缺失）返回 null 并给出缺失项。 */
export function chainOrder(state: any, ids: string[]): { order: string[]; missing: string[] } {
  const nodes = new Map<string, any>((state?.nodes || []).map((n: any) => [n.id, n]))
  const keep = new Set(ids.filter(id => nodes.has(id)))
  const missing: string[] = []
  for (const id of keep) {
    const node = nodes.get(id)
    for (const input of node?.inputs || []) {
      const src = input?.source
      if (src && (src.kind === 'node' || src.kind === 'nodeField') && src.nodeId && !keep.has(src.nodeId))
        missing.push(`「${node.name || id}」依赖未选中的「${nodes.get(src.nodeId)?.name || src.nodeId}」`)
    }
  }
  if (missing.length) return { order: [], missing: [...new Set(missing)] }
  const deps = new Map<string, string[]>()
  for (const id of keep) {
    const node = nodes.get(id)
    deps.set(id, (node?.inputs || []).flatMap((i: any) => {
      const src = i?.source
      return src && (src.kind === 'node' || src.kind === 'nodeField') && keep.has(src.nodeId) ? [src.nodeId] : []
    }))
  }
  const order: string[] = []
  const ready = [...keep].filter(id => !(deps.get(id) || []).length).sort()
  const waiting = new Map([...keep].map(id => [id, new Set(deps.get(id))]))
  while (ready.length) {
    const id = ready.shift()!
    order.push(id)
    for (const [other, pending] of waiting) {
      pending.delete(id)
      if (!pending.size && !order.includes(other) && !ready.includes(other)) ready.push(other)
    }
  }
  return { order: order.length === keep.size ? order : [...keep], missing: [] }
}

/** 链/节点的“外部输入”：来源不在被测集合内的输入（fixed 自动带值；其余需用户给值）。 */
export function chainExternalInputs(state: any, ids: string[]): { nodeId: string; nodeName: string; input: any; key: string; fixedValue?: any }[] {
  const rows: { nodeId: string; nodeName: string; input: any; key: string; fixedValue?: any }[] = []
  for (const id of ids) {
    const node = (state?.nodes || []).find((n: any) => n.id === id)
    if (!node) continue
    for (const input of node.inputs || []) {
      const src = input?.source
      if (src && src.kind === 'fixed') {
        rows.push({ nodeId: id, nodeName: node.name, input, key: `${id}.${input.name}`, fixedValue: src.value })
        continue
      }
      const inside = src && (src.kind === 'node' || src.kind === 'nodeField') && ids.includes(src.nodeId)
      if (!inside) rows.push({ nodeId: id, nodeName: node.name, input, key: `${id}.${input.name}` })
    }
  }
  return rows
}

// --- 隔离片段测试（20260919）：范围计划纯函数，与 workbench/flow_test_plan.py 镜像 ----

export interface TestExternalInput { nodeId: string; nodeName: string; inputId: string; label: string; type: any; kind: 'external' | 'unbound'; required: boolean }
export interface TestScopePlan { order: string[]; externalInputs: TestExternalInput[]; entryNeeds: { inputId: string; label: string }[]; excluded: { id: string; name: string }[]; error: string }

/** 被测集合的隔离测试计划（客户端镜像，服务端 flow_test_plan 仍是权威）：
 * 拓扑序、环/连通校验、范围外输入分类（external 必填 / unbound 可选）、入口参数需求。
 * options.requireConnected（默认 true）：连续片段要求无向连通；整条/单节点允许并列分支
 * （合法 DAG 语义），不做连通限制（R02b）。 */
export function testScopePlan(state: any, targets: string[], options?: { requireConnected?: boolean }): TestScopePlan {
  const requireConnected = options?.requireConnected !== false
  const empty: TestScopePlan = { order: [], externalInputs: [], entryNeeds: [], excluded: [], error: '' }
  const index = new Map<string, any>((state?.nodes || []).map((n: any) => [n.id, n]))
  if (!targets.length) return { ...empty, error: '请先选择要测试的节点' }
  if (new Set(targets).size !== targets.length) return { ...empty, error: '被测节点存在重复' }
  const missing = targets.filter(id => !index.has(id))
  if (missing.length) return { ...empty, error: '被测节点不存在：' + missing.join('、') }
  const selected = new Set(targets)
  const deps = new Map<string, string[]>()
  for (const id of targets) {
    deps.set(id, [])
    for (const input of index.get(id)!.inputs || []) {
      const src = input?.source
      if (src && (src.kind === 'node' || src.kind === 'nodeField') && selected.has(src.nodeId)) deps.get(id)!.push(src.nodeId)
    }
  }
  // 拓扑序（含环检测）
  const pending = new Map<string, Set<string>>(targets.map(id => [id, new Set(deps.get(id))]))
  const order: string[] = []
  let ready = targets.filter(id => !pending.get(id)!.size).sort()
  while (ready.length) {
    const id = ready.shift()!
    order.push(id)
    for (const [other, waiting] of pending) {
      if (waiting.delete(id) && !waiting.size && other !== id && !order.includes(other) && !ready.includes(other)) ready.push(other)
    }
    ready = ready.filter(x => !order.includes(x))
  }
  if (order.length !== targets.length) return { ...empty, error: '被测节点集合存在循环依赖' }
  // 连通性（无向）
  const adjacency = new Map<string, Set<string>>(targets.map(id => [id, new Set()]))
  for (const [id, sources] of deps) for (const src of sources) { adjacency.get(id)!.add(src); adjacency.get(src)!.add(id) }
  const seen = new Set<string>()
  let components = 0
  for (const id of targets) {
    if (seen.has(id)) continue
    components++
    const stack = [id]
    seen.add(id)
    while (stack.length) {
      const cur = stack.pop()!
      for (const nxt of adjacency.get(cur) || []) if (!seen.has(nxt)) { seen.add(nxt); stack.push(nxt) }
    }
  }
  if (requireConnected && components > 1) return { ...empty, error: '被测节点集合不连通，请选择依赖相连的连续片段' }
  // 外部输入分类与入口需求
  const externalInputs: TestExternalInput[] = []
  const entryNeeds: { inputId: string; label: string }[] = []
  const entrySeen = new Set<string>()
  for (const id of order) {
    const node = index.get(id)!
    for (const input of node.inputs || []) {
      const src = input?.source
      const kind = src?.kind
      if (kind === 'fixed' || kind === 'flowInput') {
        if (kind === 'flowInput' && !entrySeen.has(src.inputId)) {
          const decl = (state.inputs || []).find((i: any) => i.id === src.inputId)
          if (decl) { entryNeeds.push({ inputId: decl.id, label: decl.label || decl.name || decl.id }); entrySeen.add(decl.id) }
        }
        continue
      }
      if (kind === 'node' || kind === 'nodeField') {
        if (selected.has(src.nodeId)) continue
        externalInputs.push({ nodeId: id, nodeName: node.name || id, inputId: input.id, label: input.label || input.name || input.id, type: input.type, kind: 'external', required: true })
        continue
      }
      externalInputs.push({ nodeId: id, nodeName: node.name || id, inputId: input.id, label: input.label || input.name || input.id, type: input.type, kind: 'unbound', required: false })
    }
  }
  const excluded = processingNodes(state).filter((n: any) => !selected.has(n.id)).map((n: any) => ({ id: n.id, name: n.name || n.id }))
  return { order, externalInputs, entryNeeds, excluded, error: '' }
}

/** 节点输出的分类渲染模型（R05）：标量/对象 JSON/对象列表表格/标量列表序号表/空列表。
 * 键只扫描一次（最多看前 100 行）；混合或嵌套列表一律 JSON，不强凑表格。 */
export function describeOutput(value: any): { kind: 'scalar' | 'json' | 'object-table' | 'index-table' | 'empty'; value: any; keys?: string[]; rows?: any[]; truncated?: boolean; total?: number } {
  if (value === null || typeof value !== 'object') return { kind: 'scalar', value }
  if (!Array.isArray(value)) return { kind: 'json', value }
  if (!value.length) return { kind: 'empty', value }
  const head = value.slice(0, 100)
  const allPlain = value.every((x: any) => x && typeof x === 'object' && !Array.isArray(x))
  if (allPlain) {
    const keys: string[] = []
    for (const item of head) for (const k of Object.keys(item)) if (!keys.includes(k)) keys.push(k)
    return { kind: 'object-table', value, keys, rows: head, truncated: value.length > 100, total: value.length }
  }
  const allScalar = value.every((x: any) => x === null || ['number', 'string', 'boolean'].includes(typeof x))
  if (allScalar) return { kind: 'index-table', value, rows: head, truncated: value.length > 100, total: value.length }
  return { kind: 'json', value }
}

/** start→end 沿依赖方向的全部简单路径（用于片段起止选择：多条路径时不猜测，要求显式选集）。 */
export function dependencyPaths(state: any, start: string, end: string): string[][] {
  if (start === end) return [[start]]
  // 正向邻接：来源节点 → 引用其输出的下游节点
  const forward = new Map<string, string[]>()
  for (const node of state?.nodes || []) {
    for (const i of node.inputs || []) {
      const src = i?.source
      if (src && (src.kind === 'node' || src.kind === 'nodeField') && src.nodeId) {
        if (!forward.has(src.nodeId)) forward.set(src.nodeId, [])
        if (!forward.get(src.nodeId)!.includes(node.id)) forward.get(src.nodeId)!.push(node.id)
      }
    }
  }
  const paths: string[][] = []
  const walk = (current: string, trail: string[]) => {
    if (paths.length > 32) return // 防御：路径数上限，超出按多路径处理
    for (const next of forward.get(current) || []) {
      if (trail.includes(next) || next === current) continue
      if (next === end) { paths.push([...trail, current, end]); continue }
      walk(next, [...trail, current])
    }
  }
  walk(start, [])
  return paths
}

/** 测试输入/范围的快照签名（结果新鲜度判定的一部分；不含画布布局）。 */
export function testInputSignature(scope: { kind: string; targets: string[] }, values: Record<string, any>, entryValues: Record<string, any>): string {
  return JSON.stringify([scope, values, entryValues])
}

/** 新节点的默认画布位置：按现有节点数排成网格，边界节点固定两端。 */
export function defaultPosition(state: any): { x: number; y: number } {
  ensureLayout(state)
  const count = processingNodes(state).length
  const column = count % 4, row = Math.floor(count / 4)
  return { x: 160 + column * 210, y: 120 + row * 150 }
}

export function formatTime(iso: string): string {
  if (!iso) return '—'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  return date.toLocaleString('zh-CN', { hour12: false })
}
