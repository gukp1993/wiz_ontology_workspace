<template>
  <div class="app">
    <header class="topbar">
      <div class="brand">
        <div class="brand-icon">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="6" cy="6" r="2.6" /><circle cx="18" cy="6" r="2.6" /><circle cx="12" cy="18" r="2.6" />
            <path d="M8.4 7.2 10.8 15.6M15.6 7.2 13.2 15.6M8 6h8" />
          </svg>
        </div>
        <div>
          <h1>{{ graphName ? graphName + ' · ' + draft.name : draft.name || '知识图谱' }}</h1>
          <p>{{ statsText }}</p>
        </div>
      </div>
      <div class="search">
        <svg fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
          <circle cx="11" cy="11" r="7"></circle><path d="m20 20-3.4-3.4"></path>
        </svg>
        <input v-model="q" type="text" autocomplete="off" placeholder="搜索节点名称…" />
        <button class="search-clear" :class="{ hidden: !q }" title="清除" @click="q = ''">×</button>
      </div>
      <div class="actions">
        <div class="segmented"><button class="active" @click="showAll">总图谱</button></div>
        <button class="icon-btn" title="适应窗口" @click="fitView">
          <svg fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M8 3H3v5M16 3h5v5M8 21H3v-5M16 21h5v-5"></path></svg>
        </button>
        <button class="icon-btn" title="重置视图" @click="resetView">
          <svg fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M3 12a9 9 0 1 0 3-6.7L3 8"></path><path d="M3 3v5h5"></path></svg>
        </button>
        <i class="action-divider"></i>
        <button class="btn" @click="$emit('back')">← 返回编辑</button>
        <small class="ro-tag">发布版本只读</small>
      </div>
    </header>

    <div class="workspace" :style="{ gridTemplateColumns: gridCols }">
      <aside class="side filters">
        <div class="panel-head"><span class="eyebrow">FILTERS</span><h2>图谱筛选</h2></div>
        <section class="filter-section">
          <div class="filter-title"><h3>节点类型</h3></div>
          <div class="checks">
            <label v-for="t in SEMANTIC_TYPES" :key="t" class="check">
              <input type="checkbox" v-model="typeState[t]" @change="applyFilter" />
              <i class="swatch" :style="{ background: TYPE_COLOR[t] }"></i>
              <span>{{ t }}</span><span class="count">{{ typeCount[t] || 0 }}</span>
            </label>
          </div>
        </section>
        <section class="filter-section">
          <div class="filter-title"><h3>关系</h3></div>
          <div v-if="relTypes.length" class="checks">
            <label v-for="r in relTypes" :key="r" class="check">
              <input type="checkbox" v-model="relState[r]" @change="applyFilter" />
              <i class="swatch" style="background: #698780"></i>
              <span>{{ r }}</span><span class="count">{{ relCount[r] || 0 }}</span>
            </label>
          </div>
          <div v-else class="note">暂无关系</div>
        </section>
        <section class="filter-section">
          <div class="filter-title"><h3>图谱说明</h3></div>
          <div class="note">
            蓝色：实体节点<br />粉红色：属性节点<br />绿色：规则节点<br />
            关系为有向边：源节点 → 目标节点。<br />点击节点查看其上下游关系。
          </div>
        </section>
      </aside>

      <main class="center">
        <div class="graph-head">
          <div class="graph-head-main">
            <div class="breadcrumb">{{ breadcrumb }}</div>
            <h2>{{ viewTitle }}</h2>
          </div>
          <div v-show="selNode" class="selection-controls">
            <span>{{ statusLabel }}</span>
            <div class="segmented">
              <button v-for="s in SCOPES" :key="s.v" type="button" :data-scope="s.v" :class="{ active: relScope === s.v }" @click="setScope(s.v)">
                {{ s.label }}
              </button>
            </div>
            <button class="clear-select-btn" @click="clearSelection">取消选中</button>
          </div>
        </div>
        <div class="viewport">
          <div ref="canvasEl" class="canvas"></div>
          <div class="legend">
            <span><i class="lg-node" style="border-color: #3978c5"></i>对象</span>
            <span><i class="lg-node" style="border-color: #d15e9a"></i>共享属性</span>
            <span><i class="lg-node" style="border-color: #d18f3c"></i>私有属性</span>
            <span><i class="lg-node" style="border-color: #35a167"></i>规则</span>
            <span><i class="lg-node" style="border-color: #7a5fb5"></i>动作</span>
            <span><i class="lg-line"></i>有向关系</span>
          </div>
        </div>
      </main>

      <aside class="side inspector">
        <div class="resizer" title="拖动调整宽度" @mousedown="startResize"></div>
        <div class="panel-head"><span class="eyebrow">INSPECTOR</span><h2>图谱详情</h2></div>
        <div v-if="!selected" class="empty">
          <div><strong>选择一个节点或关系</strong>点击节点可查看详情字段、上下游关系与描述。</div>
        </div>
        <article v-else class="details">
          <template v-if="selected.type === 'node'">
            <div class="detail-hero">
              <div class="detail-kicker">
                <i class="detail-color" :style="{ background: TYPE_COLOR[selected.node.type] }"></i>
                <span>{{ selected.node.type }}节点</span>
              </div>
              <h3>{{ selected.node.name }}</h3>
            </div>
            <template v-if="entries.length">
              <div class="detail-section">
                <h4>详情字段</h4>
                <div v-for="f in entries" :key="f.key" class="field-row">
                  <div class="f-label">{{ f.label }}</div>
                  <div class="f-value">
                    <template v-if="f.isList">
                      <div v-for="(item, i) in f.value" :key="i" class="line">{{ item }}</div>
                    </template>
                    <span v-else>{{ f.value }}</span>
                  </div>
                </div>
              </div>
            </template>
            <div class="detail-section">
              <h4>直接关系</h4>
              <div v-if="relations.length" class="mini-rel-list">
                <span v-for="r in relations" :key="r.id" class="mini-rel">
                  <template v-if="r.dir === 'out'"><span class="rel">{{ r.label }}</span> → {{ r.name }}</template>
                  <template v-else>{{ r.name }} → <span class="rel">{{ r.label }}</span></template>
                </span>
              </div>
              <p v-else class="note">（无关联连线）</p>
            </div>
          </template>
          <template v-else>
            <div class="edge-hero">
              <strong>{{ selected.edge.sourceName }} —<span class="rel">{{ selected.edge.label }}</span>→ {{ selected.edge.targetName }}</strong>
            </div>
            <div class="detail-section">
              <h4>关系说明</h4>
              <p>{{ selected.edge.description || '（未填写描述）' }}</p>
            </div>
            <div class="detail-section">
              <h4>关系属性</h4>
              <dl class="props">
                <dt>源节点</dt><dd>{{ selected.edge.sourceName }}</dd>
                <dt>关系</dt><dd>{{ selected.edge.label }}</dd>
                <dt>目标节点</dt><dd>{{ selected.edge.targetName }}</dd>
              </dl>
            </div>
          </template>
        </article>
      </aside>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, shallowRef, watch, onMounted, onBeforeUnmount } from 'vue'
import cytoscape from 'cytoscape'
import { PREVIEW_GRAPH_STYLE } from './shared/graphStyle'
import { TYPE_COLOR, SEMANTIC_TYPES } from './shared/constants'
import { fieldEntries } from './shared/fields'
import { nodeW, nodeH } from './shared/layout'
import { toast } from './composables/useToast'
import { projectToDraft } from './legacyBridge'
import { decodeState } from '../modelFormat'
import { versionStateRaw } from '../api'
import { prefGet, prefSet } from '../../app/auth'

// 适配：旧「版本 id」→ 当前「本体 + 发布版本」（只读快照，经 version-state 接口）。
const props = defineProps({
  ontologyId: { type: String, required: true },
  version: { type: String, required: true },
})
const _emit = defineEmits(['back'])
const draft = ref({ name: '', nodes: [], edges: [] })
const graphName = ref('')
const canvasEl = ref(null)
const cy = shallowRef(null)
const selected = ref(null) // { type:'node', node } 或 { type:'edge', edge }

// ---- 筛选 / 作用域状态（对应旧版预览页） ----
const typeState = reactive({ 对象: true, 共享属性: true, 私有属性: true, 规则: true, 动作: true })
const relState = reactive({})
const q = ref('')
const selNode = ref(null) // 选中的节点 id（id 而非名称）
const relScope = ref('all') // 'all' | '1' | '2'
const STORE_KEY = 'kgview:' + (props.ontologyId || 'graph') + ':' + props.version
const INSP_W_KEY = 'kgview:inspW'
// 右侧「图谱详情」面板宽度（可拖动，记忆到 localStorage；限 280px ~ 60% 视口）
const inspectorW = ref(324)
try { inspectorW.value = Math.min(0.6 * window.innerWidth, Math.max(280, Number(prefGet(INSP_W_KEY)) || 324)) } catch (_e) {}
const gridCols = computed(() => `232px minmax(520px, 1fr) ${inspectorW.value}px`)
const SCOPES = [
  { v: 'all', label: '全部关系' },
  { v: '1', label: '1跳' },
  { v: '2', label: '2跳' },
]

// ---- 统计与筛选选项 ----
const typeCount = computed(() => {
  const c = {}
  draft.value.nodes.forEach((n) => { c[n.type] = (c[n.type] || 0) + 1 })
  return c
})
const relTypes = computed(() => {
  const seen = {}
  draft.value.edges.forEach((e) => { seen[e.relation] = true })
  return Object.keys(seen).sort((a, b) => a.localeCompare(b, 'zh-CN'))
})
const relCount = computed(() => {
  const c = {}
  draft.value.edges.forEach((e) => { c[e.relation] = (c[e.relation] || 0) + 1 })
  return c
})
const statsText = computed(() => {
  const s = typeCount.value
  return `对象 ${s['对象'] || 0} · 共享属性 ${s['共享属性'] || 0} · 私有属性 ${s['私有属性'] || 0} · 规则 ${s['规则'] || 0} · 动作 ${s['动作'] || 0} · 关系 ${draft.value.edges.length}`
})

// ---- 面包屑 / 标题 / 状态 ----
const idToName = computed(() => Object.fromEntries(draft.value.nodes.map((n) => [n.id, n.name])))
const selName = computed(() => (selNode.value ? idToName.value[selNode.value] || selNode.value : ''))
const modeText = computed(() => (relScope.value === 'all' ? '全部关系' : relScope.value + '跳'))
const breadcrumb = computed(() => (selNode.value ? `${props.ontologyId ? '发布版本' : '图谱'} › ${selName.value}` : '发布版本预览'))
const viewTitle = computed(() =>
  selNode.value ? `${selName.value} · ${modeText.value}（上游 + 下游）` : `${props.version || ''} · 全量节点与关系`,
)
const statusLabel = computed(() => `已选中：${selName.value} · ${modeText.value}`)

// ---- 详情面板（含富字段） ----
const entries = computed(() =>
  selected.value?.type === 'node' ? fieldEntries(selected.value.node.type, selected.value.node.data) : [],
)
const relations = computed(() => {
  if (selected.value?.type !== 'node') return []
  const id = selected.value.node.id
  return draft.value.edges
    .filter((e) => e.source === id || e.target === id)
    .map((e) => ({
      id: e.id,
      label: e.relation,
      name: idToName.value[e.source === id ? e.target : e.source],
      dir: e.source === id ? 'out' : 'in',
    }))
})

// ---- 画布构建 ----
// 发布快照不含坐标（坐标是视图状态、不入领域）：节点全堆叠时按五类分列铺开，
// 保证预览有可读的初始视图（旧预览依赖版本内坐标，此处等价补足）。
function applyInitialLayoutIfStacked() {
  if (!cy.value || !cy.value.nodes().length) return
  const ps = cy.value.nodes().map((n) => n.position())
  const stacked = ps.every((p) => Math.abs(p.x - ps[0].x) < 6 && Math.abs(p.y - ps[0].y) < 6)
  if (!stacked) return
  const ORDER = SEMANTIC_TYPES
  const colGap = 160, rowGap = 92
  const cols = ORDER.map((t) => cy.value.nodes(`[type = "${t}"]`))
  let x = 0
  cols.forEach((col) => {
    if (!col.length) return
    col.forEach((n, i) => { n.position({ x, y: (i - (col.length - 1) / 2) * rowGap }) })
    x += Math.max(220, ...col.map((n) => n.width())) + colGap
  })
}
function initCy() {
  let savedPos = null
  try { savedPos = JSON.parse(prefGet(STORE_KEY) || 'null') } catch (_e) {}
  const elements = draft.value.nodes.map((n) => {
    const saved = (savedPos && savedPos[n.id]) || {}
    return {
      data: { id: n.id, name: n.name, type: n.type, w: nodeW(n.name), h: nodeH(n.name), data: n.data || {} },
      classes: n.type,
      position: { x: saved.x != null ? saved.x : n.x || 0, y: saved.y != null ? saved.y : n.y || 0 },
    }
  })
  draft.value.edges.forEach((e) => {
    elements.push({ data: { id: e.id, source: e.source, target: e.target, relation: e.relation, description: e.description } })
  })
  // 初始化关系筛选状态
  relTypes.value.forEach((r) => { if (!(r in relState)) relState[r] = true })

  cy.value = cytoscape({
    container: canvasEl.value,
    elements,
    wheelSensitivity: 0.2,
    minZoom: 0.1,
    maxZoom: 4,
    autoungrabify: false, // 允许拖动，位置记忆到 localStorage
    boxSelectionEnabled: false,
    layout: { name: 'preset' },
    style: PREVIEW_GRAPH_STYLE,
  })
  window.__cy = cy.value // 调试 / 测试用（旧版预览页同款）
  cy.value.on('tap', onTap)
  cy.value.on('mouseover', 'edge', (evt) => evt.target.addClass('hovered'))
  cy.value.on('mouseout', 'edge', (evt) => evt.target.removeClass('hovered'))
  let saveTimer = null
  cy.value.on('dragfree', () => {
    clearTimeout(saveTimer)
    saveTimer = setTimeout(persistPositions, 300)
  })
  applyInitialLayoutIfStacked()
  applyFilter()
}

function onTap(evt) {
  if (evt.target === cy.value) {
    selected.value = null
    clearSelection()
    return
  }
  if (evt.target.isNode()) {
    const el = evt.target
    selectNode(el.id())
    selected.value = { type: 'node', node: { id: el.id(), name: el.data('name'), type: el.data('type'), data: el.data('data') || {} } }
  } else if (evt.target.isEdge()) {
    const el = evt.target
    clearSelection()
    selected.value = {
      type: 'edge',
      edge: {
        id: el.id(),
        label: el.data('relation'),
        description: el.data('description'),
        sourceName: el.source().data('name'),
        targetName: el.target().data('name'),
      },
    }
  }
}

// ---- 筛选（类型 + 关系 + 搜索） ----
function applyFilter() {
  if (!cy.value) return
  const query = q.value.trim().toLowerCase()
  cy.value.nodes().forEach((n) => {
    n.data('baseVis', typeState[n.data('type')] !== false && (!query || n.data('name').toLowerCase().includes(query)))
  })
  cy.value.edges().forEach((e) => {
    e.data('baseVis', relState[e.data('relation')] !== false)
  })
  applyScope()
}

// ---- 关系作用域（全部 / 1跳 / 2跳） ----
// 闭包：dir='in' 沿边反向追溯上游，dir='out' 追踪下游；depth=null 不限跳数
function closure(id, depth, dir) {
  const nset = new Set([id]), eset = new Set()
  let frontier = new Set([id]), d = 0
  while (frontier.size && (depth == null || d < depth)) {
    const next = new Set()
    frontier.forEach((cur) => {
      cy.value.edges().forEach((e) => {
        if (!e.data('baseVis') || !e.source().data('baseVis') || !e.target().data('baseVis')) return
        if (dir === 'in' && e.target().id() === cur && !nset.has(e.source().id())) {
          nset.add(e.source().id()); next.add(e.source().id()); eset.add(e.id())
        } else if (dir === 'out' && e.source().id() === cur && !nset.has(e.target().id())) {
          nset.add(e.target().id()); next.add(e.target().id()); eset.add(e.id())
        }
      })
    })
    frontier = next; d++
  }
  return { nodes: nset, edges: eset }
}

function setVis(ele, v) { if (v) ele.show(); else ele.hide() }

function applyScope() {
  if (!cy.value) return
  cy.value.nodes().removeClass('anc desc dim')
  cy.value.edges().removeClass('trace down dim')
  if (!selNode.value) {
    cy.value.nodes().forEach((n) => setVis(n, !!n.data('baseVis')))
    cy.value.edges().forEach((e) => setVis(e, !!e.data('baseVis') && e.source().visible() && e.target().visible()))
    return
  }
  const depth = relScope.value === 'all' ? null : Number(relScope.value)
  const up = closure(selNode.value, depth, 'in')
  const down = closure(selNode.value, depth, 'out')
  const scopeNodes = new Set([selNode.value])
  up.nodes.forEach((x) => scopeNodes.add(x))
  down.nodes.forEach((x) => scopeNodes.add(x))
  cy.value.nodes().forEach((n) => {
    if (n.id() === selNode.value) return
    if (up.nodes.has(n.id())) n.addClass('anc')
    else if (down.nodes.has(n.id())) n.addClass('desc')
    else n.addClass('dim')
  })
  cy.value.edges().forEach((e) => {
    if (up.edges.has(e.id())) e.addClass('trace')
    else if (down.edges.has(e.id())) e.addClass('down')
    else e.addClass('dim')
  })
  cy.value.nodes().forEach((n) => {
    setVis(n, !!n.data('baseVis') && (relScope.value === 'all' || scopeNodes.has(n.id())))
  })
  cy.value.edges().forEach((e) => {
    if (!e.data('baseVis')) { setVis(e, false); return }
    if (relScope.value === 'all') { setVis(e, true); return }
    setVis(e, scopeNodes.has(e.source().id()) && scopeNodes.has(e.target().id()))
  })
  const els = cy.value.nodes().filter((n) => scopeNodes.has(n.id()) && n.data('baseVis'))
  if (els.length) { try { cy.value.fit(els, 60) } catch (_e) {} }
}

function selectNode(id) { selNode.value = id; relScope.value = 'all'; applyScope() }
function clearSelection() {
  if (!selNode.value) return
  selNode.value = null
  relScope.value = 'all'
  applyScope()
}
function setScope(v) {
  if (!selNode.value || !['all', '1', '2'].includes(v)) return
  relScope.value = v
  applyScope()
}

// ---- 视图控制 ----
function showAll() {
  clearSelection()
  Object.keys(typeState).forEach((k) => { typeState[k] = true })
  Object.keys(relState).forEach((k) => { relState[k] = true })
  applyFilter()
  fitView()
}
function fitView() { if (cy.value) try { cy.value.fit(undefined, 60) } catch (_e) {} }
function resetView() {
  try { prefSet(STORE_KEY, '') } catch (_e) {}
  draft.value.nodes.forEach((n) => {
    const el = cy.value.getElementById(n.id)
    if (el) el.position({ x: n.x || 0, y: n.y || 0 })
  })
  fitView()
}

// 三列图谱默认视图：实体/属性/规则三列中心对齐画布 1/6、3/6、5/6（三等分），不改变节点坐标
function fitColumnThirds() {
  if (!cy.value) return
  try {
    const cols = {}
    let missing = false
    SEMANTIC_TYPES.forEach((t) => {
      const c = cy.value.nodes('[type = "' + t + '"]')
      if (!c.length) { missing = true; return }
      const xs = c.map((n) => n.position('x'))
      cols[t] = (Math.min(...xs) + Math.max(...xs)) / 2
    })
    if (missing || cols['对象'] == null || cols['共享属性'] == null || cols['规则'] == null) { cy.value.fit(undefined, 60); return }
    const span = cols['规则'] - cols['对象']
    if (!(span > 0)) { cy.value.fit(undefined, 60); return }
    const bb = cy.value.elements().boundingBox()
    const cw = cy.value.width(), ch = cy.value.height()
    const mid = (cols['对象'] + cols['规则']) / 2
    const zoom = Math.min(2 * cw / (3 * span), ch / bb.h)
    const panx = cw / 2 - mid * zoom
    const pany = ch / 2 - ((bb.y1 + bb.y2) / 2) * zoom
    cy.value.viewport({ zoom, pan: { x: panx, y: pany } })
  } catch (_e) { try { cy.value.fit(undefined, 60) } catch (_e2) {} }
}

// ---- 右侧详情面板拖拽调宽 ----
function startResize(e) {
  e.preventDefault()
  const startX = e.clientX
  const startW = inspectorW.value
  function onMove(ev) {
    const w = startW - (ev.clientX - startX) // 往左拖（clientX 变小）→ 面板变宽
    inspectorW.value = Math.min(0.6 * window.innerWidth, Math.max(280, w))
  }
  function onUp() {
    document.removeEventListener('mousemove', onMove)
    document.removeEventListener('mouseup', onUp)
    document.body.style.cursor = ''
    document.body.style.userSelect = ''
    try { prefSet(INSP_W_KEY, String(inspectorW.value)) } catch (_err) {}
  }
  document.addEventListener('mousemove', onMove)
  document.addEventListener('mouseup', onUp)
  document.body.style.cursor = 'col-resize'
  document.body.style.userSelect = 'none'
}

// ---- 位置记忆 ----
function persistPositions() {
  if (!cy.value) return
  const pos = {}
  cy.value.nodes().forEach((n) => { pos[n.id()] = n.position() })
  try { prefSet(STORE_KEY, JSON.stringify(pos)) } catch (_e) {}
}


watch(q, applyFilter)

onMounted(async () => {
  try {
    // 发布版本只读快照：version-state 返回 schema 形态 → decode → 复用编辑器投影
    const raw = await versionStateRaw(props.ontologyId, props.version)
    const state = decodeState(raw.state)
    state.workflow = state.workflow || {}
    state.workflow.objective = { name: (state.workflow.objective?.name || '') + ' · ' + props.version }
    const d = projectToDraft(state, {})
    draft.value = { name: props.version, nodes: d.nodes, edges: d.edges }
    graphName.value = (state.workflow.objective?.name || '').replace(' · ', ' · ')
    initCy()
    let hasSaved = false
    try { hasSaved = !!prefGet(STORE_KEY) } catch (_e) {}
    if (!hasSaved) {
      setTimeout(() => fitColumnThirds(), 50)
      if (document.fonts && document.fonts.ready) document.fonts.ready.then(() => fitColumnThirds())
    }
  } catch (e) {
    toast('加载失败：' + e.message, true)
  }
})
onBeforeUnmount(() => {
  if (cy.value) cy.value.destroy()
})
</script>

<style scoped>
.app { height: 100%; display: grid; grid-template-rows: 64px minmax(0, 1fr); }
.topbar {
  display: grid;
  grid-template-columns: minmax(200px, 370px) minmax(180px, 1fr) auto;
  gap: 12px;
  align-items: center;
  padding: 9px 14px;
  background: #fff;
  border-bottom: 1px solid var(--line);
  z-index: 20;
}
.brand { display: flex; gap: 11px; align-items: center; min-width: 0; }
.brand-icon {
  width: 39px; height: 39px; display: grid; place-items: center; border-radius: 7px;
  background: var(--accent); color: #fff; flex: 0 0 auto;
}
.brand-icon svg { width: 24px; height: 24px; }
.brand h1 { font-size: 16px; margin: 0 0 4px; max-width: 40vw; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.brand p { margin: 0; color: var(--muted); font-size: 11px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.search { position: relative; }
.search input {
  width: 100%; height: 40px; border: 1px solid transparent; border-radius: 7px; background: #f1f4f3;
  padding: 0 39px 0 39px; outline: none; font-size: 12px;
}
.search input:focus { background: #fff; border-color: var(--accent); box-shadow: 0 0 0 3px var(--soft); }
.search svg { position: absolute; left: 13px; top: 12px; width: 17px; height: 17px; color: var(--muted); }
.search-clear {
  position: absolute; right: 5px; top: 5px; width: 30px; height: 30px; border: 0; background: transparent;
  border-radius: 5px; cursor: pointer; color: var(--muted); font-size: 16px; line-height: 1;
}
.search-clear:hover { background: #e7ecea; }
.actions { display: flex; align-items: center; justify-content: flex-end; gap: 7px; min-width: 0; flex-wrap: nowrap; white-space: nowrap; }
.ro-tag { white-space: nowrap; font-size: 11px; color: var(--muted); border: 1px solid var(--line); border-radius: 4px; padding: 2px 7px; flex: 0 0 auto; }
@media (max-width: 1024px) {
  .topbar { grid-template-columns: minmax(150px, 1fr) minmax(140px, 0.8fr) auto; gap: 8px; }
  .brand p { display: none; }
}
.action-divider { width: 1px; height: 22px; background: var(--line); margin: 0 2px; flex: 0 0 auto; }
.segmented { display: flex; padding: 2px; border: 1px solid var(--line); border-radius: 7px; background: #f3f6f5; }
.segmented button {
  height: 31px; min-width: 64px; padding: 0 13px; border: 0; background: transparent; border-radius: 5px;
  color: var(--muted); cursor: pointer; font-size: 12px;
}
.segmented button.active { background: var(--accent); color: #fff; font-weight: 700; }
.icon-btn {
  width: 36px; height: 36px; border: 1px solid var(--line); background: #fff; border-radius: 6px;
  display: grid; place-items: center; cursor: pointer; color: #42514e; padding: 0;
}
.icon-btn:hover { background: #f3f6f5; border-color: #bdc8ca; }
.icon-btn svg { width: 17px; height: 17px; }

.workspace { min-height: 0; display: grid; grid-template-columns: 232px minmax(520px, 1fr) 324px; }
.side { min-width: 0; overflow: auto; background: var(--panel); }
.filters { border-right: 1px solid var(--line); }
.inspector { position: relative; border-left: 1px solid var(--line); background: #fff; }
.resizer {
  position: absolute; top: 0; bottom: 0; left: -4px; width: 8px; z-index: 40;
  cursor: col-resize;
}
.resizer:hover { background: rgba(29, 89, 77, 0.14); }
.panel-head { position: sticky; top: 0; z-index: 5; padding: 15px 15px 12px; background: inherit; border-bottom: 1px solid var(--line); }
.eyebrow { display: block; color: var(--accent); font-size: 9px; font-weight: 800; letter-spacing: 0.04em; margin-bottom: 3px; }
.panel-head h2 { margin: 0; font-size: 15px; }
.filter-section { padding: 14px 15px; border-bottom: 1px solid var(--line); }
.filter-title { display: flex; justify-content: space-between; align-items: center; margin-bottom: 9px; }
.filter-title h3 { font-size: 12px; margin: 0; }
.checks { display: grid; gap: 4px; }
.check {
  display: grid; grid-template-columns: 15px 10px minmax(0, 1fr) auto; gap: 7px; align-items: center;
  min-height: 29px; font-size: 11px; color: #334147; cursor: pointer;
}
.check input { width: 14px; height: 14px; margin: 0; accent-color: var(--accent); }
.swatch { width: 9px; height: 9px; border-radius: 2px; background: var(--c, #999); }
.count { color: var(--muted); font-variant-numeric: tabular-nums; }
.note { font-size: 10px; color: var(--muted); line-height: 1.8; }

.center { min-width: 0; min-height: 0; display: grid; grid-template-rows: 58px minmax(0, 1fr); }
.graph-head {
  display: flex; align-items: center; gap: 12px; padding: 8px 13px;
  background: rgba(255, 255, 255, 0.95); border-bottom: 1px solid var(--line);
}
.graph-head-main { min-width: 0; flex: 1; }
.breadcrumb {
  display: flex; align-items: center; gap: 5px; color: var(--muted); font-size: 10px;
  margin-bottom: 3px; white-space: nowrap; overflow: hidden;
}
.graph-head h2 { margin: 0; font-size: 15px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.selection-controls { display: flex; align-items: center; gap: 8px; white-space: nowrap; }
.selection-controls > span { color: var(--muted); font-size: 10px; }
.selection-controls .segmented button { min-width: 44px; height: 27px; padding: 0 9px; font-size: 10px; }
.clear-select-btn {
  height: 30px; padding: 0 10px; border: 1px solid #d2dcda; border-radius: 6px; background: #fff;
  color: #43524f; font-size: 10px; font-weight: 700; cursor: pointer;
}
.clear-select-btn:hover { border-color: #8ba49e; background: #f1f7f5; color: var(--accent2); }

.viewport {
  position: relative; min-width: 0; min-height: 0; overflow: hidden; cursor: grab;
  background-color: var(--canvas);
  background-image: linear-gradient(#e0e8e5 1px, transparent 1px), linear-gradient(90deg, #e0e8e5 1px, transparent 1px);
  background-size: 24px 24px;
}
.viewport:active { cursor: grabbing; }
.canvas { width: 100%; height: 100%; }
.canvas canvas { display: block; }
.legend {
  position: absolute; left: 10px; bottom: 10px; display: flex; gap: 10px; flex-wrap: wrap;
  padding: 6px 8px; background: rgba(255, 255, 255, 0.93); border: 1px solid var(--line);
  border-radius: 5px; color: var(--muted); font-size: 9px; pointer-events: none; z-index: 10;
}
.legend span { display: flex; align-items: center; gap: 5px; }
.lg-node { width: 17px; height: 11px; border-radius: 4px; border: 2px solid transparent; background: #fff; background-clip: padding-box; }
.lg-line { width: 20px; border-top: 1px solid #4d7770; }

.empty { min-height: 300px; display: grid; place-content: center; text-align: center; padding: 25px; color: var(--muted); font-size: 11px; line-height: 1.8; }
.empty strong { display: block; color: var(--ink); font-size: 13px; margin-bottom: 5px; }
.details { padding: 15px; }
.detail-hero { padding-bottom: 14px; border-bottom: 1px solid var(--line); }
.detail-kicker { display: flex; gap: 7px; align-items: center; color: var(--muted); font-size: 10px; }
.detail-color { width: 9px; height: 9px; border-radius: 2px; background: var(--c, #999); }
.detail-hero h3 { font-size: 20px; line-height: 1.35; margin: 8px 0 4px; overflow-wrap: anywhere; }
.detail-section { padding: 13px 0; border-bottom: 1px solid var(--line); }
.detail-section h4 { font-size: 10px; color: var(--muted); margin: 0 0 8px; }
.detail-section p { font-size: 12px; line-height: 1.75; margin: 0; }
.field-row { display: grid; grid-template-columns: 82px 1fr; gap: 7px 8px; font-size: 11px; padding: 3px 0; }
.f-label { color: var(--muted); }
.f-value { overflow-wrap: anywhere; white-space: pre-wrap; } /* 描述等 textarea 字段保留换行 */
.f-value .line { line-height: 1.7; }
.mini-rel-list { display: flex; flex-wrap: wrap; gap: 5px; }
.mini-rel {
  display: inline-block; border: 1px solid var(--line); background: #fafcfc; border-radius: 5px;
  padding: 4px 6px; font-size: 9.5px; color: #38504e;
}
.mini-rel .rel { font-weight: 700; color: #1d594d; }
.edge-hero { padding: 12px; border-left: 4px solid var(--accent); border-radius: 6px; background: #edf7f3; margin-bottom: 12px; }
.edge-hero strong { font-size: 13px; line-height: 1.6; word-break: break-all; }
.edge-hero .rel { color: #87500f; }
.props { display: grid; grid-template-columns: 82px 1fr; gap: 7px 8px; font-size: 11px; }
.props dt { color: var(--muted); }
.props dd { margin: 0; overflow-wrap: anywhere; }
.hidden { display: none !important; }
</style>
