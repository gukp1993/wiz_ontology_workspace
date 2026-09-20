<!-- FlowCanvas — 函数编排画布（cytoscape）。连线由输入绑定派生（唯一业务事实在绑定上）。
     视口规则（评审优化 A03）：只有打开编排初次定位与用户点「全图」才 fit；
     结果返回/检查变化/开关详情/勾选范围都不改变用户的缩放平移。
     状态更新轻量化：拓扑不变时只更新节点 label/class 与连线 label，不销毁重建元素。
     三种交互模式由父组件下发：inspect（点选看详情）/ bind（点源→点目标建绑定）/
     test（画布勾选测试范围，勾选不改变详情选择）。 -->
<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import cytoscape from 'cytoscape'
import { GRAPH_STYLE } from '../shared/graphStyle'
import { INPUT_NODE, OUTPUT_NODE, defaultPosition, derivedEdges, ensureLayout, processingNodes } from './flowModel'
const props = defineProps<{ state: any; results?: Record<string, any>; issueCounts?: Record<string, number>; checkStale?: boolean; mode?: 'inspect' | 'bind' | 'test'; testIds?: string[]; bindSourceId?: string }>()
const emit = defineEmits(['before-change', 'changed', 'select', 'bind', 'unlink', 'test-toggle'])
const canvas = ref<HTMLElement | null>(null)
const overlay = ref<{ id: string; x: number; y: number; checked: boolean; name: string }[]>([])
let cy: any = null, observer: ResizeObserver | null = null
let noticeTimer: any = null
const notice = ref('')
function notify(text: string) { notice.value = text; clearTimeout(noticeTimer); noticeTimer = setTimeout(() => notice.value = '', 3500) }

const BADGES: Record<string, (r: any) => string> = {
  waiting: () => '◌ 等待结果',
  success: r => `✓ ${r.durationMs ?? 0}ms`,
  failed: () => '✗ 失败',
  skipped: () => '⏹ 跳过',
}
function badgeText(id: string): string {
  const r = props.results?.[id]
  if (!r) return ''
  return BADGES[r.status] ? '\n' + BADGES[r.status](r) : ''
}
function issueText(id: string): string {
  if (props.checkStale) return ''
  const n = props.issueCounts?.[id] || 0
  return n ? `\n! ${n} 个问题` : ''
}

function positionOf(id: string): { x: number; y: number } {
  ensureLayout(props.state)
  if (!props.state.layout.positions[id]) {
    if (id === INPUT_NODE) props.state.layout.positions[id] = { x: 30, y: 200 }
    else if (id === OUTPUT_NODE) props.state.layout.positions[id] = { x: 760, y: 200 }
    else props.state.layout.positions[id] = defaultPosition(props.state)
  }
  return props.state.layout.positions[id]
}
function nodeElements() {
  const testSet = new Set(props.testIds || [])
  const bindSrc = props.bindSourceId || ''
  const nodes = [{
    data: { id: INPUT_NODE, name: '编排输入', label: '编排输入', w: 108, h: 52, type: 'input', locked: true },
    position: positionOf(INPUT_NODE), locked: true, classes: 'boundary',
  }, {
    data: { id: OUTPUT_NODE, name: '编排输出', label: '编排输出', w: 108, h: 52, type: 'output', locked: true },
    position: positionOf(OUTPUT_NODE), locked: true, classes: 'boundary',
  }]
  for (const node of processingNodes(props.state)) {
    const status = props.results?.[node.id]?.status
    nodes.push({
      data: { id: node.id, name: node.name || '未命名', label: (node.name || '未命名') + badgeText(node.id) + issueText(node.id), w: 118, h: 54, type: node.kind },
      position: positionOf(node.id), locked: false,
      classes: [status === 'failed' ? 'run-failed' : '', status === 'waiting' ? 'run-waiting' : '',
                testSet.has(node.id) ? 'test-checked' : '',
                bindSrc && node.id === bindSrc ? 'bind-src' : ''].filter(Boolean).join(' '),
    } as any)
  }
  return nodes
}
function edgeElements() {
  // 端点缺失的坏边（导入/旧草稿的悬挂引用）不进画布：cy.add 缺端点会抛错且画布已清空，整块白屏
  const nodeIds = new Set([INPUT_NODE, OUTPUT_NODE, ...processingNodes(props.state).map(n => n.id)])
  const edges = derivedEdges(props.state).map(e => {
    const source = props.results?.[e.source]
    const suffix = source && source.status === 'success' && source.rowCount != null ? ` · ${source.rowCount} 条` : ''
    return { data: { id: e.id, source: e.source, target: e.target, relation: e.label + suffix } }
  })
  for (const out of props.state?.outputs || []) {
    const binding = out.binding
    if (binding && (binding.kind === 'node' || binding.kind === 'nodeField') && binding.nodeId) {
      const source = props.results?.[binding.nodeId]
      const suffix = source && source.status === 'success' && source.rowCount != null ? ` · ${source.rowCount} 条` : ''
      edges.push({ data: { id: `out:${out.id}`, source: binding.nodeId, target: OUTPUT_NODE, relation: (out.label || out.name || '输出') + suffix } } as any)
    }
  }
  return edges.filter((e: any) => nodeIds.has(e.data.source) && nodeIds.has(e.data.target))
}
/** 拓扑签名：节点/连线的增删才触发元素重建；label/class 属于状态，走轻量更新。 */
const topologyOf = () => JSON.stringify([
  nodeElements().map(n => [n.data.id, n.data.type]),
  edgeElements().map(e => [e.data.id, e.data.source, e.data.target]),
])
let lastTopology = ''
function sync(force = false) {
  if (!cy) return
  const topology = topologyOf()
  if (!force && topology === lastTopology) { refreshData(); return }
  lastTopology = topology
  const pan = cy.pan(), zoom = cy.zoom()
  const selectedIds: string[] = cy.$(':selected').map((el: any) => el.id())
  cy.elements().remove()
  cy.add([...nodeElements() as any, ...edgeElements() as any])
  cy.pan(pan); cy.zoom(zoom)
  selectedIds.forEach((id: string) => cy.getElementById(id).select())
  cy.nodes('[type="input"],[type="output"]').ungrabify()
  updateOverlay()
}
/** 轻量状态更新：不销毁元素，只改 label 与 class（保留坐标、视口、选中）。 */
function refreshData() {
  if (!cy) return
  const testSet = new Set(props.testIds || [])
  for (const n of cy.nodes()) {
    const id = n.id()
    if (id === INPUT_NODE || id === OUTPUT_NODE) continue
    const status = props.results?.[id]?.status
    const classes = [status === 'failed' ? 'run-failed' : '', status === 'waiting' ? 'run-waiting' : '',
                     testSet.has(id) ? 'test-checked' : '',
                     props.bindSourceId && id === props.bindSourceId ? 'bind-src' : ''].filter(Boolean).join(' ')
    n.classes(classes)
    n.data('label', (n.data('name') || '未命名') + badgeText(id) + issueText(id))
  }
  for (const e of cy.edges()) {
    const id = e.data('id')
    if (id.startsWith('out:')) {
      const out = (props.state?.outputs || []).find((o: any) => `out:${o.id}` === id)
      const source = out ? props.results?.[out.binding?.nodeId] : null
      const suffix = source && source.status === 'success' && source.rowCount != null ? ` · ${source.rowCount} 条` : ''
      e.data('relation', (out ? (out.label || out.name || '输出') : '?') + suffix)
    } else {
      const dep = derivedEdges(props.state).find(x => x.id === id)
      const source = props.results?.[dep?.source ?? '']
      const suffix = source && source.status === 'success' && source.rowCount != null ? ` · ${source.rowCount} 条` : ''
      e.data('relation', (dep ? dep.label : e.data('relation')) + suffix)
    }
  }
  updateOverlay()
}
/** 测试模式勾选框覆盖层：跟随节点渲染坐标（平移/缩放/拖动后同步）。 */
function updateOverlay() {
  if (!cy || props.mode !== 'test') { overlay.value = []; return }
  const testSet = new Set(props.testIds || [])
  overlay.value = processingNodes(props.state).filter(n => cy.getElementById(n.id).length)
    .map(n => {
      const rp = cy.getElementById(n.id).renderedPosition()
      return { id: n.id, x: rp.x, y: rp.y - 34, checked: testSet.has(n.id), name: n.name || n.id }
    })
}
function focusNode(id: string) {
  if (!cy) return
  cy.getElementById(id).select()
  cy.animate({ center: { eles: cy.getElementById(id) }, zoom: Math.max(cy.zoom(), 1), duration: 220 })
}
function fitAll() { cy?.animate({ fit: { eles: cy.elements(), padding: 60 }, duration: 220 }) }
function zoom100() { cy?.animate({ zoom: 1, duration: 200 }) }
function autoLayout() {
  emit('before-change')
  cy.elements().layout({ name: 'breadthfirst', directed: true, roots: [INPUT_NODE], padding: 60, spacingFactor: 1.5, animate: false }).run()
  for (const node of cy.nodes()) {
    const pos = node.position()
    ensureLayout(props.state)
    props.state.layout.positions[node.id()] = { x: Math.round(pos.x), y: Math.round(pos.y) }
  }
  emit('changed')
  updateOverlay()
}
onMounted(() => {
  if (!props.state || !canvas.value) return
  ensureLayout(props.state)
  cy = cytoscape({
    container: canvas.value,
    elements: [...nodeElements() as any, ...edgeElements() as any],
    style: [...GRAPH_STYLE,
      { selector: 'node', style: { label: 'data(label)', 'font-weight': 600, 'text-wrap': 'wrap' } },
      { selector: '[type = "python"]', style: { 'background-color': '#e9f2ff', 'border-color': '#3978c5', shape: 'round-rectangle' } },
      { selector: '[type = "sql"]', style: { 'background-color': '#edf9f1', 'border-color': '#35a167', shape: 'round-rectangle' } },
      { selector: '[type = "redis"]', style: { 'background-color': '#fff2e5', 'border-color': '#d9832b', shape: 'round-rectangle' } },
      { selector: '[type = "http"]', style: { 'background-color': '#e6f6f8', 'border-color': '#2b9db0', shape: 'round-rectangle' } },
      { selector: '[type = "calc"]', style: { 'background-color': '#fdf0f6', 'border-color': '#c9566e', shape: 'round-rectangle' } },
      { selector: '[type = "input"],[type = "output"]', style: { 'background-color': '#f3f0ff', 'border-color': '#8464d8', shape: 'round-tag' } },
      { selector: 'node.boundary', style: { 'border-style': 'double', 'border-width': 4 } },
      { selector: 'node.run-failed', style: { 'border-color': '#c4534d', 'border-width': 4.5 } },
      { selector: 'node.run-waiting', style: { 'border-style': 'dashed', 'border-color': '#8a8f9f' } },
      { selector: 'node.test-checked', style: { 'border-style': 'dashed', 'border-color': '#2b9db0', 'border-width': 4 } },
      { selector: 'node.bind-src', style: { 'border-style': 'dashed', 'border-color': '#3978c5', 'border-width': 4 } },
    ],
    layout: { name: 'preset', fit: true, padding: 60 }, wheelSensitivity: 0.2, minZoom: 0.15, maxZoom: 3,
  })
  lastTopology = topologyOf()
  cy.on('tap', (event: any) => {
    if (event.target === cy) { if (props.mode === 'inspect') emit('select', ''); return }
    if (event.target.isEdge()) { if (props.mode === 'inspect') emit('select', ''); return }
    const id = event.target.id()
    if (props.mode === 'bind') {
      if (!event.target.isNode()) return
      // 边界节点也交给父组件判定语义（编排输入可作来源、编排输出可作目标）
      emit('bind', id)
      return
    }
    emit('select', id)
  })
  cy.on('grab', 'node', () => emit('before-change'))
  cy.on('dragfree', 'node', (event: any) => {
    const pos = event.target.position()
    ensureLayout(props.state)
    props.state.layout.positions[event.target.id()] = { x: Math.round(pos.x), y: Math.round(pos.y) }
    emit('changed')
    updateOverlay()
  })
  cy.on('zoom pan', () => updateOverlay())
  cy.on('cxttap', 'edge', (event: any) => { if (props.mode === 'inspect') emit('unlink', event.target.id()) })
  let initialFitDone = false
  observer = new ResizeObserver(() => {
    cy?.resize()
    // 仅首次容器就绪时定位一次全图；此后视口完全交还用户（A03）
    if (!initialFitDone && cy && cy.width() >= 60 && cy.nodes().length) {
      initialFitDone = true
      cy.fit(cy.elements(), 60)
    }
  })
  observer.observe(canvas.value)
})
onBeforeUnmount(() => { clearTimeout(noticeTimer); observer?.disconnect(); cy?.destroy() })
// 整体替换 state（撤销/重做/切换/重载）→ 重建元素并保留视口；拓扑变化同理。
watch(() => props.state, () => { lastTopology = ''; sync(true) })
const contentSignature = computed(() => JSON.stringify([
  processingNodes(props.state).map((n: any) => [n.id, n.name, n.kind]),
  derivedEdges(props.state).map((e: any) => [e.id, e.source, e.target, e.label]),
  (props.state?.outputs || []).map((o: any) => [o.id, o.binding?.nodeId || '', o.binding?.outputId || '']),
]))
watch(contentSignature, () => sync())
// 纯状态变化（结果徽标/问题标记/勾选）→ 轻量刷新，不动拓扑与视口
watch(() => JSON.stringify([props.results, props.issueCounts, props.checkStale, props.testIds, props.mode, props.bindSourceId]), () => {
  if (!cy) return
  const topology = topologyOf()
  if (topology !== lastTopology) sync(true)
  else refreshData()
})
defineExpose({ sync, focusNode, fitAll, zoom100, autoLayout })
</script>
<template>
<div class="canvas-wrapper flow-canvas">
  <div ref="canvas" class="cy-canvas"></div>
  <label v-for="o in overlay" :key="o.id" class="test-pick" :style="{ left: o.x + 'px', top: o.y + 'px' }" :title="`测试 ${o.name}`">
    <input type="checkbox" :checked="o.checked" @click.stop @change="emit('test-toggle', o.id)"/>
  </label>
  <span class="canvas-hint">{{mode==='bind'?'建立绑定：点击来源处理节点，再点击目标处理节点':(mode==='test'?'勾选测试范围 · 点击节点名称仍可查看配置':'点击节点查看配置 · 右键连线可删除绑定')}}</span>
  <span v-if="notice" class="canvas-notice" role="status">{{notice}}</span>
</div>
</template>
<style scoped>
.flow-canvas{position:relative;flex:1;min-width:0;height:100%;background-color:var(--paper-2);background-image:radial-gradient(var(--grid) 1px,transparent 1px);background-size:20px 20px;overflow:hidden}
.flow-canvas .cy-canvas{position:absolute;inset:0}
.test-pick{position:absolute;z-index:6;width:24px;height:24px;display:flex;align-items:center;justify-content:center;background:#fffffff2;border:1px solid var(--line);border-radius:6px;cursor:pointer;transform:translateX(-50%)}
.test-pick input{width:15px;height:15px;margin:0}
.canvas-notice{position:absolute;top:14px;right:14px;background:var(--warn-soft,#fff2e5);border:1px solid var(--warn-line,#efdfba);border-radius:6px;padding:5px 12px;font-size:12px;color:var(--warn,#9b660d);z-index:5}
</style>
