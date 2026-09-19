// 本体图谱视图纯函数（20260919 画布能力优化 T2/T3）——筛选、搜索、方向 BFS 邻域、诱导子图、
// 确定性布局、视图缓存规范化。不依赖 Cytoscape 与 DOM，可在 Node 中直接回归。
//
// 约定：节点/边为 ontologyGraphModel 的内部 ID；「可见」= 类型勾选 ∧ 关系勾选 ∧ 邻域范围（若有）。
// 邻域：先按方向 BFS 求节点集合，再保留集合内**全部**符合筛选的边（平行边、自关联、环都保留），
// 不把 BFS 树当结果。
import type { GraphEdge, GraphModel, GraphNode, NodeKind } from './ontologyGraphModel'

export interface GraphFilters {
  kinds: Record<string, boolean>
  relations: Record<string, boolean>
}

export interface ScopeState {
  center: string
  depth: 1 | 2 | 99
  direction: 'both' | 'in' | 'out'
  localLayout: 'original' | 'circle'
}

export interface Viewport { zoom: number; pan: { x: number; y: number } }
export interface PanelState { filters: boolean; inspector: boolean; inspectorW: number }
export interface Position { x: number; y: number }

export const ZOOM_MIN = 0.1
export const ZOOM_MAX = 4
export const INSPECTOR_MIN = 280
export const INSPECTOR_MAX = 480
export const HISTORY_LIMIT = 30
export const CACHE_SCHEMA = 1

const finite = (v: any): v is number => typeof v === 'number' && Number.isFinite(v)
export const clampZoom = (z: any) => (finite(z) ? Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, z)) : 1)
export const clampInspectorW = (w: any) => (finite(w) ? Math.min(INSPECTOR_MAX, Math.max(INSPECTOR_MIN, Math.round(w))) : 320)

/** 位置合法性：有限数字；未知 ID 剔除（缓存损坏/资产变化后不产生幽灵位置）。 */
export function normalizePositions(raw: any, validIds: Set<string>): Record<string, Position> {
  const out: Record<string, Position> = {}
  if (!raw || typeof raw !== 'object') return out
  for (const [id, p] of Object.entries<any>(raw)) {
    if (!validIds.has(id)) continue
    if (p && finite(p.x) && finite(p.y)) out[id] = { x: p.x, y: p.y }
  }
  return out
}

// ── 可见集：节点类型 →（可选）邻域范围 → 两端可见/关系勾选的边 ────────────────
/** 可见节点（不含邻域）：类型勾选。 */
export function visibleNodeIds(model: GraphModel, filters: GraphFilters): Set<string> {
  const out = new Set<string>()
  for (const n of model.nodes) if (filters.kinds[n.kind] !== false) out.add(n.id)
  return out
}

/** 关系勾选且两端节点可见的边。 */
export function visibleEdgeIds(model: GraphModel, filters: GraphFilters, nodeIds: Set<string>): Set<string> {
  const out = new Set<string>()
  for (const e of model.edges) {
    if (filters.relations[e.relation] === false) continue
    if (!nodeIds.has(e.source) || !nodeIds.has(e.target)) continue
    out.add(e.id)
  }
  return out
}

/** 邻域节点集合：仅沿「关系勾选且两端类型可见」的边，按方向与跳数做 BFS；99=全部可达（visited 防环）。 */
export function neighborhoodNodes(model: GraphModel, center: string, depth: 1 | 2 | 99, direction: 'both' | 'in' | 'out', filters: GraphFilters): Set<string> {
  const result = new Set<string>([center])
  if (!model.nodes.some(n => n.id === center)) return result
  const kindOk = (id: string) => filters.kinds[model.nodes.find(n => n.id === id)?.kind as NodeKind] !== false
  if (!kindOk(center)) return result
  const usable = (e: GraphEdge) => filters.relations[e.relation] !== false && kindOk(e.source) && kindOk(e.target)
  const maxDepth = depth === 99 ? Infinity : depth
  let frontier = [center]
  let d = 0
  while (frontier.length && d < maxDepth) {
    const next: string[] = []
    for (const cur of frontier) {
      for (const e of model.edges) {
        if (!usable(e)) continue
        let to: string | null = null
        if ((direction === 'both' || direction === 'out') && e.source === cur) to = e.target
        else if ((direction === 'both' || direction === 'in') && e.target === cur) to = e.source
        if (to && !result.has(to)) { result.add(to); next.push(to) }
      }
    }
    frontier = next
    d++
  }
  return result
}

/** 诱导子图边集合：两端都在集合内且符合关系筛选的所有边（平行/自关联/环全部保留）。 */
export function inducedEdgeIds(model: GraphModel, nodeIds: Set<string>, filters: GraphFilters): Set<string> {
  const out = new Set<string>()
  for (const e of model.edges) {
    if (filters.relations[e.relation] === false) continue
    if (nodeIds.has(e.source) && nodeIds.has(e.target)) out.add(e.id)
  }
  return out
}

// ── 搜索（仅高亮 + 结果列表；不隐藏、不重排、不改筛选） ──────────────────────
export interface SearchItem { id: string; name: string; kind: NodeKind; ownerName: string; hidden: boolean }

export function searchHitIds(model: GraphModel, query: string): Set<string> {
  const q = String(query || '').trim().toLowerCase()
  const out = new Set<string>()
  if (!q) return out
  for (const n of model.nodes) {
    const hay = [n.name, ...n.aliases].map(s => s.toLowerCase())
    if (hay.some(s => s.includes(q))) out.add(n.id)
  }
  return out
}

/** 结果列表：按类型序 + 名称排序，标注「被筛选隐藏」（未在可见集内）。limit 之外的用 total 计数。 */
export function searchList(model: GraphModel, query: string, visibleIds: Set<string>, limit = 20): { total: number; items: SearchItem[] } {
  const hits = searchHitIds(model, query)
  const ownerNameOf = (n: GraphNode) => {
    if (n.kind === '私有属性' && n.ownerId) return model.nodes.find(x => x.id === n.ownerId)?.name || ''
    return ''
  }
  const all = model.nodes.filter(n => hits.has(n.id))
    .sort((a, b) => a.kind === b.kind ? a.name.localeCompare(b.name, 'zh-CN') : a.kind.localeCompare(b.kind, 'zh-CN'))
    .map(n => ({ id: n.id, name: n.name, kind: n.kind, ownerName: ownerNameOf(n), hidden: !visibleIds.has(n.id) }))
  return { total: all.length, items: all.slice(0, Math.max(0, limit)) }
}

// ── 确定性布局（不依赖随机；同一输入结果稳定） ────────────────────────────────
const KIND_ORDER: NodeKind[] = ['对象', '共享属性', '私有属性', '规则', '动作']
const kindRank = (k: NodeKind) => { const i = KIND_ORDER.indexOf(k); return i < 0 ? KIND_ORDER.length : i }
const byKey = (a: GraphNode, b: GraphNode) => a.name.localeCompare(b.name, 'zh-CN') || a.id.localeCompare(b.id)
const COLUMN_GAP = 520, SUBCOLUMN_GAP = 260, ROW_GAP = 96, OBJECT_ROW_GAP = 124, MAX_ROWS = 20

/** 与主对象直接相连的对象序号（用于属性/规则贴近所属对象排序）；无关联返回 Infinity。 */
function relatedObjectRank(model: GraphModel, node: GraphNode, objectRank: Map<string, number>, objectIds: Set<string>): number {
  let best = Infinity
  for (const e of model.edges) {
    const other = e.source === node.id ? e.target : e.target === node.id ? e.source : ''
    if (!other || !objectIds.has(other)) continue
    const rank = objectRank.get(other)
    if (rank != null && rank < best) best = rank
  }
  return best
}

/**
 * 按类型排列（默认）：对象列 / 属性列 / 规则·动作列；组内按直接关联对象序号、名称、稳定 ID 排序；
 * 每列最多 20 个节点，超出增加子列；规则与动作分带；孤立节点排在各组尾部（不散到远处）。
 */
export function layoutByType(model: GraphModel, ids: Set<string>): Record<string, Position> {
  const list = model.nodes.filter(n => ids.has(n.id))
  const objects = list.filter(n => n.kind === '对象').sort(byKey)
  const objectRank = new Map(objects.map((n, i) => [n.id, i]))
  const objectIdSet = new Set(objects.map(n => n.id))
  const props = list.filter(n => n.kind === '共享属性' || n.kind === '私有属性')
    .map(n => ({ n, rank: relatedObjectRank(model, n, objectRank, objectIdSet) }))
    .sort((a, b) => a.rank - b.rank || byKey(a.n, b.n))
  const tails = list.filter(n => n.kind === '规则' || n.kind === '动作')
    .map(n => ({ n, band: n.kind === '规则' ? 0 : 1, rank: relatedObjectRank(model, n, objectRank, objectIdSet) }))
    .sort((a, b) => a.band - b.band || a.rank - b.rank || byKey(a.n, b.n))
  const pos: Record<string, Position> = {}
  const placeColumn = (nodes: GraphNode[], x: number, gap: number) => {
    const count = nodes.length
    nodes.forEach((n, i) => {
      const col = Math.floor(i / MAX_ROWS)
      const row = i % MAX_ROWS
      const rowsInCol = Math.min(MAX_ROWS, count - col * MAX_ROWS)
      pos[n.id] = { x: x + col * SUBCOLUMN_GAP, y: (row - (rowsInCol - 1) / 2) * gap }
    })
  }
  placeColumn(objects, 0, OBJECT_ROW_GAP)
  placeColumn(props.map(p => p.n), COLUMN_GAP, ROW_GAP)
  placeColumn(tails.map(t => t.n), COLUMN_GAP * 2, ROW_GAP)
  return pos
}

/** 按关联聚集：连通分量（可见节点 + 可见边），分量内确定性分层 BFS，分量按（节点数、最小键）排序后网格排布。 */
export function layoutByComponents(model: GraphModel, ids: Set<string>): Record<string, Position> {
  const nodes = model.nodes.filter(n => ids.has(n.id))
  const inSet = new Map(nodes.map(n => [n.id, n]))
  const adj = new Map<string, string[]>()
  for (const n of nodes) adj.set(n.id, [])
  for (const e of model.edges) {
    if (!inSet.has(e.source) || !inSet.has(e.target)) continue
    if (e.source === e.target) continue
    adj.get(e.source)!.push(e.target)
    adj.get(e.target)!.push(e.source)
  }
  const seen = new Set<string>()
  const components: { nodes: GraphNode[]; min: GraphNode }[] = []
  for (const n of [...nodes].sort(byKey)) {
    if (seen.has(n.id)) continue
    const stack = [n.id], comp: GraphNode[] = []
    seen.add(n.id)
    while (stack.length) {
      const cur = stack.pop() as string
      comp.push(inSet.get(cur) as GraphNode)
      for (const nextId of adj.get(cur) || []) if (!seen.has(nextId)) { seen.add(nextId); stack.push(nextId) }
    }
    components.push({ nodes: comp, min: [...comp].sort(byKey)[0] })
  }
  components.sort((a, b) => a.nodes.length - b.nodes.length || byKey(a.min, b.min))
  const pos: Record<string, Position> = {}
  let offsetX = 0, offsetY = 0, rowHeight = 0
  const MAX_WIDTH = 2600
  for (const comp of components) {
    // 分层 BFS：根 = 分量内最小键节点；同层按（父层序号、名称、ID）稳定
    const root = [...comp.nodes].sort(byKey)[0]
    const layer = new Map<string, number>([[root.id, 0]])
    const order: string[][] = [[root.id]]
    let frontier = [root.id]
    while (frontier.length) {
      const next: string[] = []
      for (const cur of frontier) {
        for (const nb of (adj.get(cur) || []).slice().sort((a, b) => byKey(inSet.get(a) as GraphNode, inSet.get(b) as GraphNode))) {
          if (layer.has(nb)) continue
          layer.set(nb, (layer.get(cur) as number) + 1)
          next.push(nb)
        }
      }
      if (next.length) order.push(next)
      frontier = next
    }
    let compWidth = 0
    order.forEach((layerIds, depth) => {
      layerIds.forEach((id, i) => { pos[id] = { x: offsetX + depth * 300, y: offsetY + i * 110 } })
      compWidth = Math.max(compWidth, depth * 300)
      rowHeight = Math.max(rowHeight, layerIds.length * 110)
    })
    offsetX += compWidth + 360
    if (offsetX > MAX_WIDTH) { offsetX = 0; offsetY += rowHeight + 220; rowHeight = 0 }
  }
  return pos
}

/** 环形布局（邻域临时阅读）：中心在原点，其余节点按稳定次序均匀分布。 */
export function layoutCircle(model: GraphModel, ids: Set<string>, center: string): Record<string, Position> {
  const rest = [...ids].filter(id => id !== center).sort((a, b) => {
    const na = model.nodes.find(n => n.id === a), nb = model.nodes.find(n => n.id === b)
    if (!na || !nb) return a.localeCompare(b)
    return kindRank(na.kind) - kindRank(nb.kind) || byKey(na, nb)
  })
  const pos: Record<string, Position> = {}
  if (ids.has(center)) pos[center] = { x: 0, y: 0 }
  const r = 300, ry = 220
  rest.forEach((id, i) => {
    const angle = (i / Math.max(1, rest.length)) * Math.PI * 2
    pos[id] = { x: Math.round(r * Math.cos(angle)), y: Math.round(ry * Math.sin(angle)) }
  })
  return pos
}

/** 初始布局：按类型排列（新节点/无缓存时的默认位置）。 */
export function defaultLayout(model: GraphModel, ids: Set<string>): Record<string, Position> {
  return layoutByType(model, ids)
}

// ── 视图缓存（账号 + 本体隔离由调用方用 prefGet/prefSet 完成；此处只做结构与数值校验） ──
export interface ViewMemory {
  schemaVersion: number
  positions: Record<string, Position>
  viewport: Viewport
  filters: GraphFilters
  panels: PanelState
  selection: { nodeIds: string[]; edgeId: string }
  scope: ScopeState | null
  nodeNames?: Record<string, string>
}

export function serializeView(mem: ViewMemory): string { return JSON.stringify(mem) }

/** 解析缓存：结构/数值不合法或版本不符 → null（调用方退到会话内并提示一次）。 */
export function parseView(raw: string | null, validNodeIds: Set<string>, validEdgeIds: Set<string>): ViewMemory | null {
  if (!raw) return null
  let data: any
  try { data = JSON.parse(raw) } catch { return null }
  if (!data || typeof data !== 'object' || data.schemaVersion !== CACHE_SCHEMA) return null
  const positions = normalizePositions(data.positions, validNodeIds)
  const vp = data.viewport || {}
  const zoom = clampZoom(finite(vp.zoom) ? vp.zoom : 1)
  const pan = vp.pan && finite(vp.pan.x) && finite(vp.pan.y) ? { x: vp.pan.x, y: vp.pan.y } : { x: 0, y: 0 }
  const kinds: Record<string, boolean> = {}
  const relations: Record<string, boolean> = {}
  if (data.filters && typeof data.filters === 'object') {
    for (const [k, v] of Object.entries<any>(data.filters.kinds || {})) if (typeof v === 'boolean') kinds[k] = v
    for (const [k, v] of Object.entries<any>(data.filters.relations || {})) if (typeof v === 'boolean') relations[k] = v
  }
  const panelRaw = data.panels || {}
  const panels: PanelState = {
    filters: panelRaw.filters !== false,
    inspector: panelRaw.inspector === true,
    inspectorW: clampInspectorW(panelRaw.inspectorW),
  }
  const selRaw = data.selection || {}
  const nodeIds = Array.isArray(selRaw.nodeIds) ? selRaw.nodeIds.filter((id: any) => typeof id === 'string' && validNodeIds.has(id)) : []
  const edgeId = typeof selRaw.edgeId === 'string' && validEdgeIds.has(selRaw.edgeId) ? selRaw.edgeId : ''
  let scope: ScopeState | null = null
  const sc = data.scope
  if (sc && typeof sc.center === 'string' && validNodeIds.has(sc.center)) {
    scope = {
      center: sc.center,
      depth: sc.depth === 2 || sc.depth === 99 ? sc.depth : 1,
      direction: sc.direction === 'in' || sc.direction === 'out' ? sc.direction : 'both',
      localLayout: sc.localLayout === 'circle' ? 'circle' : 'original',
    }
  }
  return { schemaVersion: CACHE_SCHEMA, positions, viewport: { zoom, pan }, filters: { kinds, relations }, panels, selection: { nodeIds, edgeId }, scope }
}

export function emptyView(): ViewMemory {
  return {
    schemaVersion: CACHE_SCHEMA, positions: {}, viewport: { zoom: 1, pan: { x: 0, y: 0 } },
    filters: { kinds: {}, relations: {} }, panels: { filters: true, inspector: false, inspectorW: 320 },
    selection: { nodeIds: [], edgeId: '' }, scope: null,
  }
}

/** 端点变化（move）用：返回源/目标确实改变的边。 */
export function movedEdges(prev: Map<string, GraphEdge>, next: GraphEdge[]): GraphEdge[] {
  return next.filter(e => { const p = prev.get(e.id); return !!p && (p.source !== e.source || p.target !== e.target) })
}
