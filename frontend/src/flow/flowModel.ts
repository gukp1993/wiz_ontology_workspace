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
export const NODE_KIND_LABELS: Record<string, string> = { python: 'Python', sql: 'SQL', redis: 'Redis', input: '编排输入', output: '编排输出' }

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

/** 新节点：仅创建时注入 Python 骨架；之后参数变化不回写正文。 */
export function blankNode(kind: 'python' | 'sql'): any {
  return {
    id: uid(), kind,
    name: kind === 'python' ? '新 Python 节点' : '新 SQL 节点',
    description: '',
    inputs: [], outputs: [],
    implementation: kind === 'python'
      ? { language: 'python', code: pythonSkeleton([], []) }
      : { language: 'sql', sql: '', connectionId: '' },
  }
}

export function pythonSkeleton(inputs: any[], outputs: any[]): string {
  const params = (inputs || []).map((i: any) => i.name).filter(Boolean).join(', ')
  const first = (outputs || []).map((o: any) => o.name).find(Boolean)
  const ret = first ? `{"${first}": None}` : 'None'
  return `def main(${params}):\n    # 在此编写处理逻辑；本工作台只保存配置，不执行代码\n    return ${ret}\n`
}

export const processingNodes = (state: any): any[] =>
  (state?.nodes || []).filter((n: any) => n && n.kind === 'python' || n && n.kind === 'sql')

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

export function ensureLayout(state: any): void {
  if (!state.layout || typeof state.layout !== 'object') state.layout = {}
  if (!state.layout.positions || typeof state.layout.positions !== 'object') state.layout.positions = {}
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
