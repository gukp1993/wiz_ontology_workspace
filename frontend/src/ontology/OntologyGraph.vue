<!-- OntologyGraph — 本体图谱（只读查看器，20260919 平移自 wiz_kq_builder 预览页的图谱能力）。
     展示五类内容：对象 / 共享属性 / 私有属性（颜色区分）/ 规则 / 动作；
     边：引用共享属性、私有属性、链接（名称即关系，动态多个）、关联规则、关联动作。
     数据全部从内存 state 推导，只读不改草稿；zoom/pan/拖动绝不 emit changed
     （与 ObjectCanvas 同款约束），拖动位置只记忆到 localStorage（键含本体 id）。
     能力对齐原型预览页：类型/关系筛选（带计数）、搜索高亮、点击节点看上下游闭包
     （全部/1跳/2跳）、右侧详情面板（可拖宽）、图例、适应窗口/重置视图。 -->
<template>
<div class="og-app">
  <header class="og-top">
    <div class="og-brand">
      <h2>本体图谱</h2>
      <p class="og-stats">{{ statsText }}</p>
    </div>
    <div class="og-search">
      <svg fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.4-3.4"/></svg>
      <input v-model="q" type="text" autocomplete="off" placeholder="搜索节点名称…" aria-label="搜索节点名称"/>
      <button v-show="q" class="og-search-clear" title="清除" aria-label="清除搜索" @click="q = ''">×</button>
    </div>
    <div class="og-actions">
      <div class="og-seg"><button type="button" class="active" @click="showAll">全部显示</button></div>
      <button type="button" class="og-icon" title="适应窗口" aria-label="适应窗口" @click="fitView">
        <svg fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M8 3H3v5M16 3h5v5M8 21H3v-5M16 21h5v-5"/></svg>
      </button>
      <button type="button" class="og-icon" title="重置视图（清除拖动位置）" aria-label="重置视图" @click="resetView">
        <svg fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/></svg>
      </button>
    </div>
  </header>

  <div class="og-body" :style="{ gridTemplateColumns: '216px minmax(420px, 1fr) ' + inspectorW + 'px' }">
    <aside class="og-side og-filters">
      <div class="og-panel-head"><span class="og-eyebrow">FILTERS</span><h3>图谱筛选</h3></div>
      <section class="og-section">
        <h4>节点类型</h4>
        <label v-for="t in NODE_KINDS" :key="t" class="og-check">
          <input type="checkbox" v-model="typeState[t]" @change="applyFilter"/>
          <i class="og-swatch" :style="{ background: KIND_STYLE[t].border }"></i>
          <span>{{ t }}</span><span class="og-count">{{ typeCount[t] || 0 }}</span>
        </label>
      </section>
      <section class="og-section">
        <h4>关系</h4>
        <label v-for="r in relTypes" :key="r" class="og-check">
          <input type="checkbox" v-model="relState[r]" @change="applyFilter"/>
          <i class="og-swatch" style="background:#698780"></i>
          <span class="og-relname">{{ r }}</span><span class="og-count">{{ relCount[r] || 0 }}</span>
        </label>
        <p v-if="!relTypes.length" class="og-note">暂无关系</p>
      </section>
      <section class="og-section">
        <h4>图例</h4>
        <p class="og-note">
          <i class="og-dot" :style="{ background: KIND_STYLE['对象'].bg, borderColor: KIND_STYLE['对象'].border }"></i>对象
          <i class="og-dot" :style="{ background: KIND_STYLE['共享属性'].bg, borderColor: KIND_STYLE['共享属性'].border }"></i>共享属性
          <i class="og-dot" :style="{ background: KIND_STYLE['私有属性'].bg, borderColor: KIND_STYLE['私有属性'].border }"></i>私有属性
          <i class="og-dot" :style="{ background: KIND_STYLE['规则'].bg, borderColor: KIND_STYLE['规则'].border }"></i>规则
          <i class="og-dot" :style="{ background: KIND_STYLE['动作'].bg, borderColor: KIND_STYLE['动作'].border }"></i>动作
          <br/>有向边：源节点 → 目标节点。<br/>点击节点查看上下游关系。
        </p>
      </section>
    </aside>

    <main class="og-center">
      <div class="og-graph-head">
        <div class="og-head-main">
          <div class="og-crumb">{{ breadcrumb }}</div>
          <h3>{{ viewTitle }}</h3>
        </div>
        <div v-show="selNode" class="og-selctl">
          <span class="og-selstatus">{{ statusLabel }}</span>
          <div class="og-seg">
            <button v-for="s in SCOPES" :key="s.v" type="button" :class="{ active: relScope === s.v }" @click="setScope(s.v)">{{ s.label }}</button>
          </div>
          <button type="button" class="og-btn" @click="clearSelection">取消选中</button>
        </div>
      </div>
      <div class="og-viewport">
        <div ref="canvasEl" class="og-canvas"></div>
      </div>
    </main>

    <aside class="og-side og-inspector" :style="{ width: inspectorW + 'px' }">
      <div class="og-resizer" title="拖动调整宽度" @mousedown="startResize"></div>
      <div class="og-panel-head"><span class="og-eyebrow">INSPECTOR</span><h3>图谱详情</h3></div>
      <div v-if="!selected" class="og-empty">
        <strong>选择一个节点或关系</strong>
        <span>点击节点查看详情字段与上下游关系。</span>
      </div>
      <article v-else class="og-details">
        <template v-if="selected.type === 'node'">
          <div class="og-hero">
            <div class="og-hero-kicker">
              <i :style="{ background: KIND_STYLE[selected.node.kind]?.border || '#94a3a3' }"></i>
              <span>{{ selected.node.kind }}</span>
            </div>
            <h3>{{ selected.node.name }}</h3>
          </div>
          <div v-if="entries.length" class="og-dsec">
            <h4>详情字段</h4>
            <div v-for="f in entries" :key="f.label" class="og-field">
              <div class="og-flabel">{{ f.label }}</div>
              <div class="og-fvalue">{{ f.value }}</div>
            </div>
          </div>
          <div class="og-dsec">
            <h4>直接关系</h4>
            <div v-if="relations.length" class="og-rellist">
              <span v-for="r in relations" :key="r.id + r.dir" class="og-rel">
                <template v-if="r.dir === 'out'"><b>{{ r.label }}</b> → {{ r.name }}</template>
                <template v-else>{{ r.name }} → <b>{{ r.label }}</b></template>
              </span>
            </div>
            <p v-else class="og-note">（无关联连线）</p>
          </div>
        </template>
        <template v-else>
          <div class="og-hero">
            <strong>{{ selected.edge.sourceName }} —<b>{{ selected.edge.relation }}</b>→ {{ selected.edge.targetName }}</strong>
          </div>
          <div class="og-dsec">
            <h4>关系说明</h4>
            <p class="og-note">{{ selected.edge.description || '（未填写说明）' }}</p>
          </div>
        </template>
      </article>
    </aside>
  </div>
</div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, shallowRef, watch, onMounted, onBeforeUnmount } from 'vue'
import cytoscape from 'cytoscape'
import { ruleAssociationsOf } from './businessRuleModel'
import { effectiveAssociations } from './actionModel'
import { propertyDataType, dataTypeLabel } from './propertyModel'

const props = defineProps<{ state: any; ontologyId?: string }>()

// ── 节点种类与视觉（对象=原型实体蓝；共享属性=原型属性粉；私有属性=橙；规则=绿；动作=紫） ──
const NODE_KINDS = ['对象', '共享属性', '私有属性', '规则', '动作'] as const
type NodeKind = (typeof NODE_KINDS)[number]
const KIND_STYLE: Record<NodeKind, { border: string; bg: string; shape: string }> = {
  对象: { border: '#3978c5', bg: '#e9f2ff', shape: 'round-rectangle' },
  共享属性: { border: '#d15e9a', bg: '#fff0f7', shape: 'ellipse' },
  私有属性: { border: '#d18f3c', bg: '#fdf3e4', shape: 'ellipse' },
  规则: { border: '#35a167', bg: '#edf9f1', shape: 'round-rectangle' },
  动作: { border: '#7a5fb5', bg: '#f2eeff', shape: 'round-rectangle' },
}
const REL_COLOR = '#4d7770'
const SCOPES = [
  { v: 'all', label: '全部关系' },
  { v: '1', label: '1跳' },
  { v: '2', label: '2跳' },
] as const

interface GNode { id: string; kind: NodeKind; name: string; data: Record<string, string> }
interface GEdge { id: string; source: string; target: string; relation: string; description: string }

const canvasEl = ref<HTMLElement | null>(null)
const cy = shallowRef<any>(null)
const selected = ref<{ type: 'node'; node: GNode } | { type: 'edge'; edge: GEdge & { sourceName: string; targetName: string } } | null>(null)
const q = ref('')
const selNode = ref('')      // 选中节点 id
const relScope = ref<'all' | '1' | '2'>('all')
const typeState = reactive<Record<string, boolean>>({})
const relState = reactive<Record<string, boolean>>({})
const inspectorW = ref(324)
const STORE_KEY = 'ont-graph:' + (props.ontologyId || 'default')
const INSP_W_KEY = 'ont-graph:inspW'
try { inspectorW.value = Math.min(0.6 * window.innerWidth, Math.max(280, Number(localStorage.getItem(INSP_W_KEY)) || 324)) } catch {}

// ── 从内存 state 推导节点与边（与页面各处读取口径一致） ──
const graph = computed<any[]>(() => props.state?.ontology?.['@graph'] || [])
const objects = computed(() => graph.value.filter((n: any) => n['@type'] === 'owl:Class'))
const sharedProps = computed(() => graph.value.filter((n: any) => n['@type'] === 'mg:SharedProperty'))
const propNodes = computed(() => graph.value.filter((n: any) => n['@type'] === 'owl:DatatypeProperty'))
const links = computed(() => graph.value.filter((n: any) => n['@type'] === 'owl:ObjectProperty'))
const rules = computed<any[]>(() => (Array.isArray(props.state?.workflow?.businessRules) ? props.state.workflow.businessRules : []).filter((r: any) => r && r.id))
const actions = computed<any[]>(() => (Array.isArray(props.state?.workflow?.actions) ? props.state.workflow.actions : []).filter((a: any) => a && a.id))
const ruleAssocs = computed(() => ruleAssociationsOf(props.state))
const actionAssocs = computed(() => effectiveAssociations(props.state))

const trim = (v: any) => String(v ?? '').trim()
const typeName = computed(() => { const m = new Map<string, string>(); for (const o of objects.value) m.set(o['@id'], trim(o['rdfs:label']) || o['@id']); return m })

function formattingOf(n: any): string {
  const f = n['mg:formatting']
  const v = f && typeof f === 'object' ? (f['@value'] ?? f) : null
  return v && typeof v === 'object' ? trim(v.instruction) : ''
}
function nodeW(name: string) { const l = [...(name || '')].length; return Math.min(205, Math.max(112, 76 + l * 9.2)) }
function nodeH(name: string) { return (name || '').length > 12 ? 46 : 40 }

/** 全量节点与边（每次重建重算；纯函数便于派生统计）。 */
function buildModel(): { nodes: GNode[]; edges: GEdge[] } {
  const nodes: GNode[] = []
  const edges: GEdge[] = []
  const pushEdge = (source: string, target: string, relation: string, description = '') => {
    if (!source || !target || source === target) return
    const key = source + '→' + target + '·' + relation
    if (edges.some((e) => e.id === key)) return
    edges.push({ id: key, source, target, relation, description })
  }
  for (const o of objects.value) {
    nodes.push({ id: o['@id'], kind: '对象', name: trim(o['rdfs:label']) || o['@id'], data: { 业务定义: trim(o['rdfs:comment']) } })
  }
  for (const s of sharedProps.value) {
    const dt = propertyDataType(s, graph.value)
    nodes.push({ id: s['@id'], kind: '共享属性', name: trim(s['rdfs:label']) || s['@id'],
      data: { 业务定义: trim(s['rdfs:comment']), 数据类型: dataTypeLabel(dt), 单位: trim(s['mg:valueSuffix']), 显示格式: formattingOf(s) } })
  }
  for (const p of propNodes.value) {
    const ownerId = p['rdfs:domain']?.['@id'] || ''
    const sharedId = p['mg:sharedProperty']?.['@id'] || ''
    if (sharedId) { pushEdge(ownerId, sharedId, '引用共享属性', '对象引用共享属性库中的定义。'); continue }
    const name = trim(p['rdfs:label']) || trim(p['mg:apiName']) || p['@id']
    nodes.push({ id: p['@id'], kind: '私有属性', name, data: { 业务定义: trim(p['rdfs:comment']), 数据类型: dataTypeLabel(propertyDataType(p, graph.value)) } })
    pushEdge(ownerId, p['@id'], '私有属性', '对象私有的属性定义。')
  }
  const CARD: Record<string, string> = { 'one-to-one': '一对一', 'one-to-many': '一对多', 'many-to-one': '多对一', 'many-to-many': '多对多' }
  for (const l of links.value) {
    const name = trim(l['rdfs:label']) || '链接'
    const card = CARD[trim(l['mg:cardinality'])] || ''
    const desc = [trim(l['rdfs:comment']), card ? '数量关系：' + card : '', trim(l['mg:reverseLabel']) ? '反向名称：' + trim(l['mg:reverseLabel']) : ''].filter(Boolean).join('；')
    pushEdge(l['rdfs:domain']?.['@id'] || '', l['rdfs:range']?.['@id'] || '', name, desc)
  }
  for (const r of rules.value) {
    nodes.push({ id: r.id, kind: '规则', name: trim(r.name) || r.id, data: { 业务定义: trim(r.description), 规则内容: trim(r.content), 输出结果: trim(r.output) } })
  }
  for (const a of ruleAssocs.value) {
    const objId = String(a.objectTypeId || '').startsWith('mg:') ? a.objectTypeId : 'mg:' + a.objectTypeId
    pushEdge(objId, a.ruleId, '关联规则', '对象引用此业务规则。')
  }
  for (const a of actions.value) {
    nodes.push({ id: a.id, kind: '动作', name: trim(a.name) || a.id, data: { 业务定义: trim(a.description), 业务效果: trim(a.effect) } })
  }
  for (const a of actionAssocs.value) {
    const objId = String(a.objectTypeId || '').startsWith('mg:') ? a.objectTypeId : 'mg:' + a.objectTypeId
    pushEdge(objId, a.actionId, '关联动作', '对象关联此动作。')
  }
  // 悬空端点（对象已删但关联残留）不建边也不建节点：按当前草稿可见内容展示
  const ids = new Set(nodes.map((n) => n.id))
  return { nodes, edges: edges.filter((e) => ids.has(e.source) && ids.has(e.target)) }
}

const model = computed(() => buildModel())
const nodesById = computed(() => new Map(model.value.nodes.map((n) => [n.id, n])))
const typeCount = computed(() => { const c: Record<string, number> = {}; for (const n of model.value.nodes) c[n.kind] = (c[n.kind] || 0) + 1; return c })
const relTypes = computed(() => [...new Set(model.value.edges.map((e) => e.relation))].sort((a, b) => a.localeCompare(b, 'zh-CN')))
const relCount = computed(() => { const c: Record<string, number> = {}; for (const e of model.value.edges) c[e.relation] = (c[e.relation] || 0) + 1; return c })
const statsText = computed(() => {
  const s = typeCount.value
  return `对象 ${s['对象'] || 0} · 共享属性 ${s['共享属性'] || 0} · 私有属性 ${s['私有属性'] || 0} · 规则 ${s['规则'] || 0} · 动作 ${s['动作'] || 0} · 关系 ${model.value.edges.length}`
})

// ── 确定性分层布局：对象 | 属性（共享+私有） | 规则+动作，列内名称排序垂直均布 ──
function layoutPositions(nodes: GNode[]): Record<string, { x: number; y: number }> {
  const zh = (a: GNode, b: GNode) => a.name.localeCompare(b.name, 'zh-CN')
  const column = (list: GNode[], x: number, gap: number) => {
    list.sort(zh)
    const y0 = -((list.length - 1) * gap) / 2
    list.forEach((n, i) => { pos[n.id] = { x, y: y0 + i * gap } })
  }
  const pos: Record<string, { x: number; y: number }> = {}
  column(nodes.filter((n) => n.kind === '对象'), 0, 130)
  column(nodes.filter((n) => n.kind === '共享属性' || n.kind === '私有属性'), 470, 96)
  column(nodes.filter((n) => n.kind === '规则' || n.kind === '动作'), 940, 96)
  return pos
}

// ── 画布构建 / 重建（数据变化时保留位置记忆） ──
function initCy() {
  if (!canvasEl.value) return
  if (cy.value) { cy.value.destroy(); cy.value = null }
  let savedPos: Record<string, { x: number; y: number }> | null = null
  try { savedPos = JSON.parse(localStorage.getItem(STORE_KEY) || 'null') } catch {}
  const positions = layoutPositions(model.value.nodes)
  const elements = [
    ...model.value.nodes.map((n) => {
      const p = (savedPos && savedPos[n.id]) || positions[n.id] || { x: 0, y: 0 }
      return { data: { id: n.id, name: n.name, kind: n.kind, w: nodeW(n.name), h: nodeH(n.name), data: n.data }, position: { x: p.x, y: p.y } }
    }),
    ...model.value.edges.map((e) => ({ data: { id: e.id, source: e.source, target: e.target, relation: e.relation, description: e.description } })),
  ]
  cy.value = cytoscape({
    container: canvasEl.value, elements, wheelSensitivity: 0.2, minZoom: 0.1, maxZoom: 4,
    autoungrabify: false, boxSelectionEnabled: false, layout: { name: 'preset' }, style: CY_STYLE,
  })
  ;(window as any).__ogCy = cy.value // 调试/自动化测试用：取节点 renderedPosition 做精确点击（原型预览页同款惯例）
  cy.value.on('tap', onTap)
  cy.value.on('mouseover', 'edge', (evt: any) => evt.target.addClass('hovered'))
  cy.value.on('mouseout', 'edge', (evt: any) => evt.target.removeClass('hovered'))
  let saveTimer: any = null
  cy.value.on('dragfree', () => { clearTimeout(saveTimer); saveTimer = setTimeout(persistPositions, 300) })
  for (const t of NODE_KINDS) if (!(t in typeState)) typeState[t] = true
  for (const r of relTypes.value) if (!(r in relState)) relState[r] = true
  selected.value = null; selNode.value = ''; relScope.value = 'all'
  applyFilter()
  setTimeout(() => fitView(), 50)
}

const modelStamp = computed(() => model.value.nodes.length + '|' + model.value.edges.length + '|' + relTypes.value.join(','))
watch(modelStamp, () => { if (cy.value) initCy() })

function onTap(evt: any) {
  if (evt.target === cy.value) { selected.value = null; clearSelection(); return }
  if (evt.target.isNode()) {
    const el = evt.target
    const node = nodesById.value.get(el.id())
    if (!node) return
    selectNode(el.id())
    selected.value = { type: 'node', node }
  } else if (evt.target.isEdge()) {
    const el = evt.target
    clearSelection()
    selected.value = { type: 'edge', edge: { id: el.id(), source: el.source().id(), target: el.target().id(), relation: el.data('relation'), description: el.data('description'), sourceName: el.source().data('name'), targetName: el.target().data('name') } }
  }
}

// ── 筛选（类型 + 关系 + 搜索） ──
function applyFilter() {
  if (!cy.value) return
  const query = q.value.trim().toLowerCase()
  cy.value.nodes().forEach((n: any) => {
    n.data('baseVis', typeState[n.data('kind')] !== false && (!query || String(n.data('name')).toLowerCase().includes(query)))
  })
  cy.value.edges().forEach((e: any) => { e.data('baseVis', relState[e.data('relation')] !== false) })
  applyScope()
}
watch(q, applyFilter)

// ── 关系作用域（全部 / 1跳 / 2跳）：上下游闭包高亮，其余淡出 ──
function closure(id: string, depth: number | null, dir: 'in' | 'out') {
  const nset = new Set([id]), eset = new Set<string>()
  let frontier = new Set([id]), d = 0
  while (frontier.size && (depth == null || d < depth)) {
    const next = new Set<string>()
    frontier.forEach((cur) => {
      cy.value.edges().forEach((e: any) => {
        if (!e.data('baseVis') || !e.source().data('baseVis') || !e.target().data('baseVis')) return
        if (dir === 'in' && e.target().id() === cur && !nset.has(e.source().id())) { nset.add(e.source().id()); next.add(e.source().id()); eset.add(e.id()) }
        else if (dir === 'out' && e.source().id() === cur && !nset.has(e.target().id())) { nset.add(e.target().id()); next.add(e.target().id()); eset.add(e.id()) }
      })
    })
    frontier = next; d++
  }
  return { nodes: nset, edges: eset }
}

function applyScope() {
  if (!cy.value) return
  cy.value.nodes().removeClass('anc desc dim')
  cy.value.edges().removeClass('trace down dim')
  if (!selNode.value) {
    cy.value.nodes().forEach((n: any) => { n.data('baseVis') ? n.show() : n.hide() })
    cy.value.edges().forEach((e: any) => { e.data('baseVis') && e.source().visible() && e.target().visible() ? e.show() : e.hide() })
    return
  }
  const depth = relScope.value === 'all' ? null : Number(relScope.value)
  const up = closure(selNode.value, depth, 'in')
  const down = closure(selNode.value, depth, 'out')
  const scope = new Set<string>([selNode.value, ...up.nodes, ...down.nodes])
  cy.value.nodes().forEach((n: any) => {
    if (n.id() === selNode.value) return
    if (up.nodes.has(n.id())) n.addClass('anc')
    else if (down.nodes.has(n.id())) n.addClass('desc')
    else n.addClass('dim')
  })
  cy.value.edges().forEach((e: any) => {
    if (up.edges.has(e.id())) e.addClass('trace')
    else if (down.edges.has(e.id())) e.addClass('down')
    else e.addClass('dim')
  })
  cy.value.nodes().forEach((n: any) => { n.data('baseVis') && (relScope.value === 'all' || scope.has(n.id())) ? n.show() : n.hide() })
  cy.value.edges().forEach((e: any) => {
    if (!e.data('baseVis')) { e.hide(); return }
    if (relScope.value === 'all') { e.show(); return }
    scope.has(e.source().id()) && scope.has(e.target().id()) ? e.show() : e.hide()
  })
}

function selectNode(id: string) { selNode.value = id; relScope.value = 'all'; applyScope() }
function clearSelection() { if (!selNode.value) return; selNode.value = ''; relScope.value = 'all'; applyScope() }
function setScope(v: 'all' | '1' | '2') { if (!selNode.value) return; relScope.value = v; applyScope() }

// ── 视图控制 ──
function showAll() {
  clearSelection()
  Object.keys(typeState).forEach((k) => { typeState[k] = true })
  Object.keys(relState).forEach((k) => { relState[k] = true })
  applyFilter(); fitView()
}
function fitView() { if (cy.value) try { cy.value.fit(undefined, 60) } catch {} }
function resetView() {
  try { localStorage.removeItem(STORE_KEY) } catch {}
  const positions = layoutPositions(model.value.nodes)
  for (const n of model.value.nodes) { const el = cy.value?.getElementById(n.id); if (el && el.nonempty()) el.position(positions[n.id] || { x: 0, y: 0 }) }
  fitView()
}
function persistPositions() {
  if (!cy.value) return
  const pos: Record<string, { x: number; y: number }> = {}
  cy.value.nodes().forEach((n: any) => { pos[n.id()] = { x: n.position('x'), y: n.position('y') } })
  try { localStorage.setItem(STORE_KEY, JSON.stringify(pos)) } catch {}
}

// ── 详情面板 ──
const entries = computed(() => {
  if (selected.value?.type !== 'node') return []
  return Object.entries(selected.value.node.data)
    .filter(([, v]) => String(v ?? '').trim() !== '')
    .map(([label, value]) => ({ label, value: String(value) }))
})
const relations = computed(() => {
  if (selected.value?.type !== 'node') return []
  const id = selected.value.node.id
  const nameOf = (nid: string) => nodesById.value.get(nid)?.name || nid
  return model.value.edges
    .filter((e) => e.source === id || e.target === id)
    .map((e) => ({ id: e.id, label: e.relation, name: nameOf(e.source === id ? e.target : e.source), dir: e.source === id ? 'out' : 'in' }))
})

const idToName = computed(() => new Map(model.value.nodes.map((n) => [n.id, n.name])))
const selName = computed(() => (selNode.value ? idToName.value.get(selNode.value) || selNode.value : ''))
const modeText = computed(() => (relScope.value === 'all' ? '全部关系' : relScope.value + '跳'))
const breadcrumb = computed(() => (selNode.value ? `本体图谱 › ${selName.value}` : '本体图谱'))
const viewTitle = computed(() => (selNode.value ? `${selName.value} · ${modeText.value}（上游 + 下游）` : '全量节点与关系'))
const statusLabel = computed(() => `已选中：${selName.value} · ${modeText.value}`)

// ── 详情面板拖宽 ──
function startResize(e: MouseEvent) {
  e.preventDefault()
  const startX = e.clientX, startW = inspectorW.value
  const onMove = (ev: MouseEvent) => { inspectorW.value = Math.min(0.6 * window.innerWidth, Math.max(280, startW - (ev.clientX - startX))) }
  const onUp = () => {
    document.removeEventListener('mousemove', onMove); document.removeEventListener('mouseup', onUp)
    document.body.style.cursor = ''; document.body.style.userSelect = ''
    try { localStorage.setItem(INSP_W_KEY, String(inspectorW.value)) } catch {}
  }
  document.addEventListener('mousemove', onMove); document.addEventListener('mouseup', onUp)
  document.body.style.cursor = 'col-resize'; document.body.style.userSelect = 'none'
}

onMounted(initCy)
onBeforeUnmount(() => { if (cy.value) { cy.value.destroy(); cy.value = null } })

// ── cytoscape 样式（平移自原型 PREVIEW_GRAPH_STYLE，节点类型换成本体五类） ──
const CY_STYLE: any[] = [
  { selector: 'node', style: {
    width: 'data(w)', height: 'data(h)', 'background-color': '#fff', 'border-width': 2.2, 'border-color': '#94a3a3', 'border-style': 'solid',
    label: 'data(name)', color: '#172126', 'font-size': 12.5, 'font-weight': 700, 'text-valign': 'center', 'text-halign': 'center',
    'text-wrap': 'wrap', 'text-max-width': 'data(w)', 'overlay-opacity': 0 } },
  ...NODE_KINDS.map((k) => ({ selector: `[kind = "${k}"]`, style: { 'background-color': KIND_STYLE[k].bg, 'border-color': KIND_STYLE[k].border, shape: KIND_STYLE[k].shape as any } })),
  { selector: 'node:selected', style: { 'border-color': '#f59b23', 'border-width': 4.2 } },
  { selector: 'node.anc', style: { 'border-color': '#0c6b58', 'border-width': 3.6 } },
  { selector: 'node.desc', style: { 'border-color': '#e8801a', 'border-width': 3.6 } },
  { selector: 'node.dim', style: { opacity: 0.09 } },
  { selector: 'node.search-hit', style: { 'border-color': '#0b7ec2', 'border-width': 4.5 } },
  { selector: 'edge', style: {
    width: 1.5, 'curve-style': 'bezier', 'line-color': REL_COLOR, 'line-opacity': 0.45,
    'target-arrow-shape': 'triangle', 'target-arrow-color': '#657d78', 'target-arrow-fill': 'filled', 'arrow-scale': 0.7,
    label: 'data(relation)', 'font-size': 10.5, color: '#87500f', 'font-weight': 600,
    'text-outline-color': '#fff', 'text-outline-width': 3, 'text-outline-opacity': 1, 'overlay-opacity': 0 } },
  { selector: 'edge:selected', style: { 'line-color': '#0c6b58', 'line-opacity': 0.98, 'target-arrow-color': '#0c6b58', width: 2.6 } },
  { selector: 'edge.hovered', style: { 'line-color': '#0c6b58', 'line-opacity': 0.98, 'target-arrow-color': '#0c6b58', width: 2.6 } },
  { selector: 'edge.trace', style: { 'line-color': '#0c6b58', 'line-opacity': 0.95, 'target-arrow-color': '#0c6b58', width: 2.4 } },
  { selector: 'edge.down', style: { 'line-color': '#e8801a', 'line-opacity': 0.95, 'target-arrow-color': '#e8801a', width: 2.4 } },
  { selector: 'edge.dim', style: { opacity: 0.08 } },
]
</script>

<style scoped>
/* 整体框架（白底浅色，对齐原型预览页；og- 前缀避免与工作台全局类冲突） */
.og-app{height:calc(100vh - 214px);min-height:540px;display:grid;grid-template-rows:auto minmax(0,1fr);background:#fff;border:1px solid var(--line);border-radius:var(--r-md);overflow:hidden}
.og-top{display:flex;align-items:center;gap:16px;padding:10px 14px;border-bottom:1px solid var(--line);background:#fff;flex-wrap:wrap}
.og-brand{min-width:0}
.og-brand h2{margin:0;font-size:16px}
.og-stats{margin:2px 0 0;font-size:12px;color:#708198;font-variant-numeric:tabular-nums}
.og-search{position:relative;display:flex;align-items:center;gap:8px;flex:1;min-width:220px;max-width:460px;border:1px solid var(--line-2);border-radius:999px;padding:7px 14px;background:var(--bg)}
.og-search svg{width:15px;height:15px;color:#8aa;flex:none}
.og-search input{display:block;width:100%;min-width:0;margin:0;padding:0;border:0;background:transparent;font-size:13px;color:var(--ink)}
.og-search input:focus{outline:none}
.og-search-clear{margin:0;padding:0 4px;border:0;background:none;font-size:15px;color:#8aa;cursor:pointer}
.og-actions{display:flex;align-items:center;gap:8px;margin-left:auto}
.og-seg{display:inline-flex;border:1px solid var(--line-2);border-radius:8px;overflow:hidden}
.og-seg button{margin:0;border:0;background:#fff;padding:6px 12px;font-size:12.5px;color:var(--ink-2);cursor:pointer}
.og-seg button + button{border-left:1px solid var(--line-2)}
.og-seg button.active{background:var(--blue-soft);color:var(--blue);font-weight:600}
.og-icon{margin:0;border:1px solid var(--line-2);background:#fff;border-radius:8px;padding:6px 8px;cursor:pointer;color:var(--ink-2)}
.og-icon svg{width:15px;height:15px;display:block}
.og-icon:hover,.og-seg button:hover{background:var(--paper-2)}
.og-body{display:grid;min-height:0}
.og-side{background:#fafbfb;border-right:1px solid var(--line);overflow:auto;padding:14px 12px;min-width:0}
.og-inspector{border-right:0;border-left:1px solid var(--line);position:relative;background:#fff}
.og-panel-head{margin-bottom:12px}
.og-eyebrow{display:block;font-size:10px;letter-spacing:1.6px;color:#9ab;font-weight:700}
.og-panel-head h3{margin:2px 0 0;font-size:14px}
.og-section{margin-bottom:16px}
.og-section h4{margin:0 0 6px;font-size:12px;color:#576d82}
.og-check{display:flex;align-items:center;gap:7px;padding:3px 0;font-size:12.5px;cursor:pointer}
.og-check input{display:inline-block;width:14px;height:14px;min-width:14px;margin:0;padding:0;accent-color:var(--blue)}
.og-swatch{width:11px;height:11px;border-radius:3px;flex:none}
.og-count{margin-left:auto;color:#9ab;font-variant-numeric:tabular-nums}
.og-relname{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:110px}
.og-note{margin:0;font-size:12px;color:#708198;line-height:1.7;overflow-wrap:anywhere}
.og-dot{display:inline-block;width:10px;height:10px;border-radius:50%;border:1.6px solid;margin:0 4px 0 0;vertical-align:-1px}
.og-center{display:grid;grid-template-rows:auto minmax(0,1fr);min-width:0}
.og-graph-head{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:9px 16px;border-bottom:1px solid var(--line);flex-wrap:wrap}
.og-crumb{font-size:11px;color:#9ab}
.og-head-main h3{margin:1px 0 0;font-size:14px}
.og-selctl{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.og-selstatus{font-size:12px;color:#576d82}
.og-btn{margin:0;border:1px solid var(--line-2);background:#fff;border-radius:7px;padding:5px 10px;font-size:12px;color:var(--ink-2);cursor:pointer}
.og-viewport{position:relative;min-height:0}
.og-canvas{position:absolute;inset:0}
.og-resizer{position:absolute;left:0;top:0;bottom:0;width:5px;cursor:col-resize;background:transparent;z-index:5}
.og-resizer:hover{background:var(--blue-soft)}
.og-empty{display:grid;gap:6px;font-size:13px;color:#708198;padding:6px 2px}
.og-details{padding:2px}
.og-hero{padding:10px 12px;background:var(--bg);border:1px solid var(--line);border-radius:8px;margin-bottom:12px}
.og-hero h3{margin:4px 0 0;font-size:15px;overflow-wrap:anywhere}
.og-hero-kicker{display:flex;align-items:center;gap:6px;font-size:11px;color:#576d82}
.og-hero-kicker i{width:10px;height:10px;border-radius:3px}
.og-hero b{color:#87500f}
.og-dsec{margin-bottom:14px}
.og-dsec h4{margin:0 0 6px;font-size:12px;color:#576d82}
.og-field{padding:5px 0;border-bottom:1px dashed var(--line)}
.og-flabel{font-size:11px;color:#9ab;margin-bottom:1px}
.og-fvalue{font-size:13px;color:var(--ink);white-space:pre-wrap;overflow-wrap:anywhere}
.og-rellist{display:grid;gap:5px}
.og-rel{font-size:12.5px;color:var(--ink-2);overflow-wrap:anywhere}
.og-rel b{color:#87500f;font-weight:600}
@media(max-width:1100px){.og-app{height:auto}.og-body{grid-template-columns:1fr!important}.og-side{border-right:0;border-bottom:1px solid var(--line)}.og-inspector{border-left:0;border-top:1px solid var(--line)}.og-viewport{height:520px}}
</style>
