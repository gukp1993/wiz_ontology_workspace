// 本体图谱投影（20260919 画布能力优化 T1）——纯函数，只读 state，不写草稿。
//
// 五类节点：对象 / 共享属性 / 私有属性 / 业务规则 / 动作；
// 关系：对象链接（定义ID）、共享引用（对象属性定义ID）、私有属性归属、规则关联、动作关联。
// 与旧实现的差异（本轮修正）：
//   1. Cytoscape 内部 ID 命名空间化（obj:/sp:/pp:/rule:/action: 等前缀）并保留 domainId/kind，
//      规则或动作恰好与对象同名同 ID 也不碰撞；不再用「起点+终点+名称」去重，也不再拒绝自关联，
//      平行链接（不同定义 ID、同端点同名）与自关联都如实上图。
//   2. 悬空引用（对象/共享定义/规则/动作被删但引用残留）不绘制假节点，收集为 dangling 计数，
//      每条带可定位到原定义的导航描述；数据层不静默修复。
//   3. 节点/边携带定义导航描述（nav）与详情字段（detail），供「打开定义」复用现有表单。
import { effectiveAssociations } from './actionModel'
import { ruleAssociationsOf } from './businessRuleModel'
import { propertyDataType, dataTypeLabel } from './propertyModel'

export type NodeKind = '对象' | '共享属性' | '私有属性' | '规则' | '动作'
export type EdgeKind = '对象链接' | '共享引用' | '私有属性' | '规则关联' | '动作关联'

export const NODE_KINDS: NodeKind[] = ['对象', '共享属性', '私有属性', '规则', '动作']
export const EDGE_KINDS: EdgeKind[] = ['对象链接', '共享引用', '私有属性', '规则关联', '动作关联']

export interface DetailField { label: string; value: string }
/** 定义导航描述：按现有 focus 协议打开对应定义页面（前端可选返回上下文由调用方附加）。 */
export interface NavTarget { label: string; view: string; focus?: Record<string, string | boolean> }

export interface GraphNode {
  id: string            // 内部 ID（命名空间化，图表专用）；不得写回业务引用
  domainId: string      // 业务定义稳定 ID（@id / 规则 id / 动作 id）
  kind: NodeKind
  name: string
  aliases: string[]
  ownerId?: string      // 私有属性的所属对象（内部 ID）
  detail: DetailField[]
  nav: NavTarget[]
}

export interface GraphEdge {
  id: string
  domainId: string
  kind: EdgeKind
  source: string        // 内部节点 ID
  target: string
  relation: string
  description: string
  detail: DetailField[]
  nav: NavTarget[]
}

export interface DanglingRef {
  key: string
  kind: EdgeKind
  summary: string
  nav: NavTarget[]
}

export interface GraphModel {
  nodes: GraphNode[]
  edges: GraphEdge[]
  dangling: DanglingRef[]
}

const trim = (v: any) => String(v ?? '').trim()
const NODE_PREFIX: Record<NodeKind, string> = { 对象: 'obj:', 共享属性: 'sp:', 私有属性: 'pp:', 规则: 'rule:', 动作: 'action:' }

export const objectNodeId = (domainId: string) => 'obj:' + domainId
export const sharedNodeId = (domainId: string) => 'sp:' + domainId
export const privateNodeId = (domainId: string) => 'pp:' + domainId
export const ruleNodeId = (domainId: string) => 'rule:' + domainId
export const actionNodeId = (domainId: string) => 'action:' + domainId

/** 关联类边（无独立定义 ID）的复合键：类型 + 双方稳定 ID。 */
export const assocEdgeId = (prefix: string, objectDomainId: string, targetId: string) => prefix + objectDomainId + '|' + targetId

function formattingOf(n: any): string {
  const f = n['mg:formatting']
  const v = f && typeof f === 'object' ? (f['@value'] ?? f) : null
  return v && typeof v === 'object' ? trim(v.instruction) : ''
}

function aliasesOf(n: any): string[] {
  const raw = n?.['mg:aliases']
  const v = raw && typeof raw === 'object' && '@value' in raw ? raw['@value'] : raw
  return Array.isArray(v) ? v.map((x: any) => trim(x)).filter(Boolean) : []
}

const CARDINALITY: Record<string, string> = { 'one-to-one': '一对一', 'one-to-many': '一对多', 'many-to-one': '多对一', 'many-to-many': '多对多' }

/** 对象类型 id 归一：关联记录里的裸 id 补 mg: 前缀（与 workflow 容错读取一致）。 */
const fullTypeId = (id: any) => { const s = trim(id); return s && !s.startsWith('mg:') ? 'mg:' + s : s }

function reviewNoteField(n: any): DetailField | null {
  const note = trim(n?.['mg:reviewNote'])
  return note ? { label: '待确认提示', value: note } : null
}

/**
 * 全量投影：从本体 @graph 与 workflow（业务规则/动作及其关联）构建图谱。
 * 顺序确定：对象→共享属性→私有属性→规则→动作；边按链接/引用/归属/规则关联/动作关联。
 */
export function buildGraphModel(state: any): GraphModel {
  const graph: any[] = Array.isArray(state?.ontology?.['@graph']) ? state.ontology['@graph'] : []
  const objects = graph.filter(n => n && n['@type'] === 'owl:Class' && n['@id'])
  const sharedProps = graph.filter(n => n && n['@type'] === 'mg:SharedProperty' && n['@id'])
  const propNodes = graph.filter(n => n && n['@type'] === 'owl:DatatypeProperty' && n['@id'])
  const links = graph.filter(n => n && n['@type'] === 'owl:ObjectProperty' && n['@id'])
  const rules: any[] = (Array.isArray(state?.workflow?.businessRules) ? state.workflow.businessRules : []).filter((r: any) => r && r.id)
  const actions: any[] = (Array.isArray(state?.workflow?.actions) ? state.workflow.actions : []).filter((a: any) => a && a.id)

  const nodes: GraphNode[] = []
  const edges: GraphEdge[] = []
  const dangling: DanglingRef[] = []
  const objectIds = new Set(objects.map(o => trim(o['@id'])))
  const _sharedIds = new Set(sharedProps.map(s => trim(s['@id'])))
  const _ruleIds = new Set(rules.map(r => trim(r.id)))
  const _actionIds = new Set(actions.map(a => trim(a.id)))

  for (const o of objects) {
    const id = trim(o['@id'])
    const label = trim(o['rdfs:label']) || id
    const aliases = aliasesOf(o)
    const detail: DetailField[] = [{ label: '业务定义', value: trim(o['rdfs:comment']) }]
    if (aliases.length) detail.push({ label: '别名', value: aliases.join('、') })
    const note = reviewNoteField(o); if (note) detail.push(note)
    nodes.push({ id: objectNodeId(id), domainId: id, kind: '对象', name: label, aliases, detail, nav: [{ label: '打开对象定义', view: 'objects', focus: { type: id } }] })
  }
  for (const s of sharedProps) {
    const id = trim(s['@id'])
    const label = trim(s['rdfs:label']) || id
    const refCount = propNodes.filter(p => trim(p['mg:sharedProperty']?.['@id']) === id).length
    const detail: DetailField[] = [
      { label: '业务定义', value: trim(s['rdfs:comment']) },
      { label: '数据类型', value: dataTypeLabel(propertyDataType(s, graph)) },
      { label: '引用对象属性数', value: String(refCount) },
    ]
    const suffix = trim(s['mg:valueSuffix']); if (suffix) detail.push({ label: '单位', value: suffix })
    const fmt = formattingOf(s); if (fmt) detail.push({ label: '显示格式', value: fmt })
    const note = reviewNoteField(s); if (note) detail.push(note)
    nodes.push({
      id: sharedNodeId(id), domainId: id, kind: '共享属性', name: label,
      aliases: aliasesOf(s),
      detail, nav: [{ label: '打开共享属性定义', view: 'library', focus: { definition: id } }],
    })
  }
  // 私有属性 = 未引用共享定义的数据属性；引用共享的只作为边出现（定义节点在共享属性库）。
  const privateProps: any[] = []
  const sharedRefs: { ownerDomainId: string; prop: any; sharedDomainId: string }[] = []
  for (const p of propNodes) {
    const ownerDomainId = trim(p['rdfs:domain']?.['@id'])
    const sharedDomainId = trim(p['mg:sharedProperty']?.['@id'])
    if (sharedDomainId) { sharedRefs.push({ ownerDomainId, prop: p, sharedDomainId }); continue }
    privateProps.push(p)
  }
  for (const p of privateProps) {
    const id = trim(p['@id'])
    const ownerDomainId = trim(p['rdfs:domain']?.['@id'])
    const name = trim(p['rdfs:label']) || trim(p['mg:apiName']) || id
    const ownerName = objects.find(o => trim(o['@id']) === ownerDomainId)?.['rdfs:label'] || ownerDomainId
    const detail: DetailField[] = [
      { label: '业务定义', value: trim(p['rdfs:comment']) },
      { label: '数据类型', value: dataTypeLabel(propertyDataType(p, graph)) },
      { label: '所属对象', value: trim(ownerName) },
    ]
    const note = reviewNoteField(p); if (note) detail.push(note)
    nodes.push({
      id: privateNodeId(id), domainId: id, kind: '私有属性', name, aliases: aliasesOf(p), ownerId: ownerDomainId ? objectNodeId(ownerDomainId) : undefined,
      detail,
      nav: ownerDomainId ? [{ label: '打开对象属性', view: 'objects', focus: { type: ownerDomainId, property: id, tab: 'props' } }] : [],
    })
  }
  for (const r of rules) {
    const id = trim(r.id)
    const refs = ruleAssociationsOf(state).filter(a => trim(a.ruleId) === id)
    const detail: DetailField[] = [
      { label: '业务定义', value: trim(r.description) },
      { label: '规则内容', value: trim(r.content) || '未填写' },
      { label: '引用对象数', value: String(refs.length) },
    ]
    // 历史 output（20260920 字段精简）：有值才展示为历史补充说明，与资产库/对象页口径一致。
    if (trim(r.output)) detail.splice(2, 0, { label: '历史补充说明（原输出结果）', value: trim(r.output) })
    const nav: NavTarget[] = [{ label: '打开规则定义', view: 'rules', focus: { definition: id } }]
    const owner = refs.map(a => fullTypeId(a.objectTypeId)).find(oid => objectIds.has(oid))
    if (owner) nav.unshift({ label: '打开所属对象规则页签', view: 'objects', focus: { type: owner, tab: 'rules' } })
    nodes.push({ id: ruleNodeId(id), domainId: id, kind: '规则', name: trim(r.name) || id, aliases: [], detail, nav })
  }
  for (const a of actions) {
    const id = trim(a.id)
    const refs = effectiveAssociations(state).filter(r => trim(r.actionId) === id)
    const detail: DetailField[] = [
      { label: '业务定义', value: trim(a.description) },
      { label: '预期效果', value: trim(a.effect) },
      { label: '关联对象数', value: String(refs.length) },
    ]
    const nav: NavTarget[] = [{ label: '打开动作定义', view: 'actions', focus: { definition: id } }]
    const owner = refs.map(r => fullTypeId(r.objectTypeId)).find(oid => objectIds.has(oid))
    if (owner) nav.unshift({ label: '打开所属对象动作页签', view: 'objects', focus: { type: owner, tab: 'actions' } })
    nodes.push({ id: actionNodeId(id), domainId: id, kind: '动作', name: trim(a.name) || id, aliases: [], detail, nav })
  }

  const nodeIds = new Set(nodes.map(n => n.id))
  const pushEdge = (edge: GraphEdge) => { if (edge.source && edge.target && !edges.some(e => e.id === edge.id)) edges.push(edge) }
  // 对象链接：每条业务链接一个边（定义 ID 作身份；改显示名不断链；同名同端点、自关联都保留）
  for (const l of links) {
    const id = trim(l['@id'])
    const sourceDomainId = trim(l['rdfs:domain']?.['@id'])
    const targetDomainId = trim(l['rdfs:range']?.['@id'])
    const name = trim(l['rdfs:label']) || '未命名链接'
    const card = CARDINALITY[trim(l['mg:cardinality'])] || trim(l['mg:cardinality'])
    const reverse = trim(l['mg:reverseLabel'])
    const source = objectNodeId(sourceDomainId), target = objectNodeId(targetDomainId)
    if (!nodeIds.has(source) || !nodeIds.has(target)) {
      dangling.push({
        key: 'link:' + id, kind: '对象链接',
        summary: `对象链接「${name}」的${!nodeIds.has(source) ? '起点' : '终点'}对象已不存在`,
        nav: nodeIds.has(source) ? [{ label: '打开起点对象链接', view: 'objects', focus: { type: sourceDomainId, tab: 'links', definition: id } }] : [],
      })
      continue
    }
    const sourceName = objects.find(o => trim(o['@id']) === sourceDomainId)?.['rdfs:label'] || sourceDomainId
    const targetName = objects.find(o => trim(o['@id']) === targetDomainId)?.['rdfs:label'] || targetDomainId
    const detail: DetailField[] = [
      { label: '来源', value: '对象链接' },
      { label: '关系', value: `${trim(sourceName)} → ${name} → ${trim(targetName)}` },
      { label: '起点 / 终点', value: `${sourceDomainId} → ${targetDomainId}` },
      ...(card ? [{ label: '数量关系', value: card }] : []),
      ...(reverse ? [{ label: '反向名称', value: reverse }] : []),
      { label: '说明', value: trim(l['rdfs:comment']) },
    ]
    pushEdge({
      id: 'link:' + id, domainId: id, kind: '对象链接', source, target, relation: name,
      description: trim(l['rdfs:comment']), detail,
      nav: [{ label: '打开链接定义', view: 'objects', focus: { type: sourceDomainId, tab: 'links', definition: id } }],
    })
  }
  // 共享引用：边保留「对象属性定义 ID」（可定位到具体属性行），定义节点是共享属性。
  for (const ref of sharedRefs) {
    const propId = trim(ref.prop['@id'])
    const source = objectNodeId(ref.ownerDomainId), target = sharedNodeId(ref.sharedDomainId)
    const sharedName = sharedProps.find(s => trim(s['@id']) === ref.sharedDomainId)?.['rdfs:label'] || ref.sharedDomainId
    if (!nodeIds.has(source)) {
      dangling.push({ key: 'sref:' + propId, kind: '共享引用', summary: `属性「${trim(ref.prop['rdfs:label']) || propId}」的所属对象已不存在`, nav: [] })
      continue
    }
    if (!nodeIds.has(target)) {
      dangling.push({
        key: 'sref:' + propId, kind: '共享引用',
        summary: `属性「${trim(ref.prop['rdfs:label']) || propId}」引用的共享定义「${ref.sharedDomainId}」已不存在`,
        nav: [{ label: '打开对象属性', view: 'objects', focus: { type: ref.ownerDomainId, property: propId, tab: 'props' } }],
      })
      continue
    }
    pushEdge({
      id: 'sref:' + propId, domainId: propId, kind: '共享引用', source, target, relation: '引用共享属性',
      description: '对象引用共享属性库中的定义。',
      detail: [
        { label: '来源', value: '共享引用' },
        { label: '对象属性定义 ID', value: propId },
        { label: '共享定义', value: `${trim(sharedName)}（${ref.sharedDomainId}）` },
        { label: '说明', value: trim(ref.prop['rdfs:comment']) },
      ],
      nav: [
        { label: '打开对象属性', view: 'objects', focus: { type: ref.ownerDomainId, property: propId, tab: 'props' } },
        { label: '打开共享定义', view: 'library', focus: { definition: ref.sharedDomainId } },
      ],
    })
  }
  // 私有属性归属边（对象 → 私有属性）
  for (const p of privateProps) {
    const id = trim(p['@id'])
    const ownerDomainId = trim(p['rdfs:domain']?.['@id'])
    const source = objectNodeId(ownerDomainId), target = privateNodeId(id)
    if (!nodeIds.has(source) || !nodeIds.has(target)) {
      dangling.push({
        key: 'own:' + id, kind: '私有属性',
        summary: `私有属性「${trim(p['rdfs:label']) || id}」的所属对象已不存在`,
        nav: ownerDomainId && nodeIds.has(source) ? [{ label: '打开对象属性', view: 'objects', focus: { type: ownerDomainId, property: id, tab: 'props' } }] : [],
      })
      continue
    }
    pushEdge({
      id: 'own:' + id, domainId: id, kind: '私有属性', source, target, relation: '私有属性',
      description: '对象私有的属性定义。',
      detail: [{ label: '来源', value: '私有属性归属' }, { label: '属性定义 ID', value: id }, { label: '说明', value: trim(p['rdfs:comment']) }],
      nav: [{ label: '打开对象属性', view: 'objects', focus: { type: ownerDomainId, property: id, tab: 'props' } }],
    })
  }
  // 规则关联 / 动作关联：无独立定义 ID，用「类型 + 双方 ID」复合键
  for (const assoc of ruleAssociationsOf(state)) {
    const objectDomainId = fullTypeId(assoc.objectTypeId), ruleId = trim(assoc.ruleId)
    const source = objectNodeId(objectDomainId), target = ruleNodeId(ruleId)
    if (!nodeIds.has(source) || !nodeIds.has(target)) {
      dangling.push({
        key: assocEdgeId('ruleassoc:', objectDomainId, ruleId), kind: '规则关联',
        summary: !nodeIds.has(source) ? `规则关联指向的对象「${objectDomainId}」已不存在` : `规则关联引用的规则「${ruleId}」已不存在`,
        nav: nodeIds.has(source) ? [{ label: '打开对象规则页签', view: 'objects', focus: { type: objectDomainId, tab: 'rules' } }] : [],
      })
      continue
    }
    pushEdge({
      id: assocEdgeId('ruleassoc:', objectDomainId, ruleId), domainId: objectDomainId + '|' + ruleId, kind: '规则关联', source, target, relation: '关联规则',
      description: '对象引用此业务规则。',
      detail: [
        { label: '来源', value: '规则关联' },
        { label: '对象', value: `${trim(objects.find(o => trim(o['@id']) === objectDomainId)?.['rdfs:label']) || objectDomainId}（${objectDomainId}）` },
        { label: '规则', value: `${trim(rules.find(r => trim(r.id) === ruleId)?.name) || ruleId}（${ruleId}）` },
      ],
      nav: [
        { label: '打开对象规则页签', view: 'objects', focus: { type: objectDomainId, tab: 'rules' } },
        { label: '打开规则定义', view: 'rules', focus: { definition: ruleId } },
      ],
    })
  }
  for (const assoc of effectiveAssociations(state)) {
    const objectDomainId = fullTypeId(assoc.objectTypeId), actionId = trim(assoc.actionId)
    const source = objectNodeId(objectDomainId), target = actionNodeId(actionId)
    if (!nodeIds.has(source) || !nodeIds.has(target)) {
      dangling.push({
        key: assocEdgeId('actionassoc:', objectDomainId, actionId), kind: '动作关联',
        summary: !nodeIds.has(source) ? `动作关联指向的对象「${objectDomainId}」已不存在` : `动作关联引用的动作「${actionId}」已不存在`,
        nav: nodeIds.has(source) ? [{ label: '打开对象动作页签', view: 'objects', focus: { type: objectDomainId, tab: 'actions' } }] : [],
      })
      continue
    }
    pushEdge({
      id: assocEdgeId('actionassoc:', objectDomainId, actionId), domainId: objectDomainId + '|' + actionId, kind: '动作关联', source, target, relation: '关联动作',
      description: '对象关联此动作。',
      detail: [
        { label: '来源', value: '动作关联' },
        { label: '对象', value: `${trim(objects.find(o => trim(o['@id']) === objectDomainId)?.['rdfs:label']) || objectDomainId}（${objectDomainId}）` },
        { label: '动作', value: `${trim(actions.find(a => trim(a.id) === actionId)?.name) || actionId}（${actionId}）` },
      ],
      nav: [
        { label: '打开对象动作页签', view: 'objects', focus: { type: objectDomainId, tab: 'actions' } },
        { label: '打开动作定义', view: 'actions', focus: { definition: actionId } },
      ],
    })
  }
  return { nodes, edges, dangling }
}

/** 模型签名：节点/边的身份与全部展示字段（名称、别名、端点、说明、悬空计数）变化都可感知。 */
export function graphSignature(model: GraphModel): string {
  const nodePart = model.nodes
    .map(n => [n.id, n.kind, n.name, n.aliases.join('|'), n.ownerId || '', n.detail.map(d => d.label + '=' + d.value).join('\u0001'), n.nav.map(t => t.label).join('|')].join('\u0002'))
    .sort().join('\u0003')
  const edgePart = model.edges
    .map(e => [e.id, e.kind, e.source, e.target, e.relation, e.description, e.detail.map(d => d.label + '=' + d.value).join('\u0001')].join('\u0002'))
    .sort().join('\u0003')
  const danglingPart = model.dangling.map(d => d.key + '\u0002' + d.summary).sort().join('\u0003')
  return nodePart + '\u0004' + edgePart + '\u0004' + danglingPart
}

export interface ModelDiff {
  addedNodes: GraphNode[]
  removedNodeIds: string[]
  updatedNodes: GraphNode[]
  addedEdges: GraphEdge[]
  removedEdgeIds: string[]
  updatedEdges: GraphEdge[]
  danglingChanged: boolean
}

const displayKey = (row: { name?: string; aliases?: string[]; detail: DetailField[]; nav: NavTarget[]; relation?: string; description?: string; source?: string; target?: string }): string =>
  JSON.stringify([row.name || '', (row.aliases || []).join('|'), row.relation || '', row.description || '', row.source || '', row.target || '',
    row.detail.map(d => d.label + '=' + d.value), row.nav.map(t => t.label + ':' + t.view + ':' + JSON.stringify(t.focus || {}))])

/** 增量差异：只比稳定 ID 与展示字段；未变化元素保持原位（不重排），删除项清理，端点变化得到 updatedEdges。 */
export function diffModels(prev: GraphModel | null, next: GraphModel): ModelDiff {
  if (!prev) return {
    addedNodes: [...next.nodes], removedNodeIds: [], updatedNodes: [],
    addedEdges: [...next.edges], removedEdgeIds: [], updatedEdges: [], danglingChanged: next.dangling.length > 0,
  }
  const prevNodes = new Map(prev.nodes.map(n => [n.id, n]))
  const nextNodes = new Map(next.nodes.map(n => [n.id, n]))
  const prevEdges = new Map(prev.edges.map(e => [e.id, e]))
  const nextEdges = new Map(next.edges.map(e => [e.id, e]))
  return {
    addedNodes: next.nodes.filter(n => !prevNodes.has(n.id)),
    removedNodeIds: prev.nodes.filter(n => !nextNodes.has(n.id)).map(n => n.id),
    updatedNodes: next.nodes.filter(n => { const p = prevNodes.get(n.id); return !!p && displayKey(p) !== displayKey(n) }),
    addedEdges: next.edges.filter(e => !prevEdges.has(e.id)),
    removedEdgeIds: prev.edges.filter(e => !nextEdges.has(e.id)).map(e => e.id),
    updatedEdges: next.edges.filter(e => { const p = prevEdges.get(e.id); return !!p && (displayKey(p) !== displayKey(e) || p.source !== e.source || p.target !== e.target) }),
    danglingChanged: JSON.stringify(prev.dangling.map(d => d.key + d.summary).sort()) !== JSON.stringify(next.dangling.map(d => d.key + d.summary).sort()),
  }
}

/** 节点/边计数（筛选面板与状态条共用）。 */
export function modelCounts(model: GraphModel) {
  const kinds: Record<string, number> = {}
  for (const n of model.nodes) kinds[n.kind] = (kinds[n.kind] || 0) + 1
  const relations: Record<string, number> = {}
  for (const e of model.edges) relations[e.relation] = (relations[e.relation] || 0) + 1
  return { kinds, relations, nodeTotal: model.nodes.length, edgeTotal: model.edges.length }
}

/** 关系名列表（筛选面板顺序：按出现频次稳定；同名合并展示）。 */
export function relationNames(model: GraphModel): string[] {
  const seen: string[] = []
  for (const e of model.edges) if (!seen.includes(e.relation)) seen.push(e.relation)
  return seen.sort((a, b) => a.localeCompare(b, 'zh-CN'))
}

export function nodesById(model: GraphModel): Map<string, GraphNode> {
  return new Map(model.nodes.map(n => [n.id, n]))
}

export function edgesById(model: GraphModel): Map<string, GraphEdge> {
  return new Map(model.edges.map(e => [e.id, e]))
}

export { NODE_PREFIX }
