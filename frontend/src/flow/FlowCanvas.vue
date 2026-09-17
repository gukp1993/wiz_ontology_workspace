<!-- FlowCanvas — 函数编排画布（cytoscape）。连线从输入绑定派生（唯一业务事实在绑定上）：
     · 表单绑定变更 → sync() 重算派生连线；
     · 连线模式点击源→目标 → emit('link')，由编辑页打开绑定对话框（取消不产生绑定）；
     · 删除连线 → emit('unlink', edgeId)，编辑页解析受影响绑定并确认；
     · 拖动节点位置写 layout.positions（grab 时 before-change，dragfree changed）；
     · 缩放/平移只写 layout.zoom/pan，绝不触发保存（A：布局与业务分开）。
     v2：五类节点配色；运行/测试结果徽标（状态+耗时/边行数）由 results 驱动；
     链选择模式下点选节点 emit('chain-tap')，选中高亮由 chainIds 驱动。 -->
<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import cytoscape from 'cytoscape'
import { GRAPH_STYLE } from '../shared/graphStyle'
import { INPUT_NODE, OUTPUT_NODE, defaultPosition, derivedEdges, ensureLayout, processingNodes } from './flowModel'
const props = defineProps<{ state: any; results?: Record<string, any>; chainIds?: string[]; chainMode?: boolean }>()
const emit = defineEmits(['before-change', 'changed', 'select', 'link', 'unlink', 'chain-tap'])
const canvas = ref<HTMLElement | null>(null)
let cy: any = null, observer: ResizeObserver | null = null
const linking = ref(false), linkSource = ref(''), notice = ref('')
let noticeTimer: any = null
function notify(text: string) { notice.value = text; clearTimeout(noticeTimer); noticeTimer = setTimeout(() => notice.value = '', 3500) }

const BADGES: Record<string, (r: any) => string> = {
  running: () => '⏳ 运行中…',
  success: r => `✓ 成功 ${r.durationMs ?? 0}ms`,
  failed: () => '✗ 失败（点击查看）',
  skipped: () => '⏹ 跳过',
}
function badgeText(id: string): string {
  const r = props.results?.[id]
  if (!r) return ''
  return BADGES[r.status] ? '\n' + BADGES[r.status](r) : ''
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
  const chain = new Set(props.chainIds || [])
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
      data: { id: node.id, name: node.name || '未命名', label: (node.name || '未命名') + badgeText(node.id), w: 118, h: 54, type: node.kind },
      position: positionOf(node.id), locked: false,
      classes: [status === 'failed' ? 'run-failed' : '', status === 'running' ? 'run-running' : '', chain.has(node.id) ? 'chain-in' : ''].filter(Boolean).join(' '),
    } as any)
  }
  return nodes
}
function edgeElements() {
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
  return edges
}
function sync() {
  if (!cy) return
  const pan = cy.pan(), zoom = cy.zoom()
  const selectedIds: string[] = cy.$(':selected').map((el: any) => el.id())
  cy.elements().remove()
  // 先加节点、后加边，且跳过端点缺失的坏边（失效引用由配置检查报错定位，不中断画布）
  cy.add([...nodeElements() as any, ...edgeElements() as any].filter((el: any) => {
    if (el.group !== 'edges') return true
    return !!cy.getElementById(el.data.source).length && !!cy.getElementById(el.data.target).length
  }))
  cy.pan(pan); cy.zoom(zoom)
  selectedIds.forEach((id: string) => cy.getElementById(id).select())
  cy.nodes('[type="input"],[type="output"]').ungrabify()
  ensureWholeGraphVisible()
}
/** 兜底：全图包围盒超出可视区时自动适配（打开编排/内容变化后第一眼必见全貌）。 */
function ensureWholeGraphVisible() {
  if (!cy || !cy.nodes().length) return
  const w = cy.width(), h = cy.height()
  if (w < 60 || h < 60) return
  const bb = cy.nodes().renderedBoundingBox({ includeLabels: false })
  if (bb.x1 < 0 || bb.y1 < 0 || bb.x2 > w || bb.y2 > h) cy.fit(cy.elements(), 60)
}
/** 打开编排时无条件适配全图（容器尚无尺寸则稍后重试），保证第一眼看到完整流程。 */
function ensureFullFit(attempt = 0) {
  if (!cy) return
  if (cy.width() < 60 || cy.height() < 60) {
    if (attempt < 20) setTimeout(() => ensureFullFit(attempt + 1), 200)
    return
  }
  cy.fit(cy.elements(), 60)
}
function focusNode(id: string) {
  if (!cy) return
  cy.getElementById(id).select()
  cy.animate({ center: { eles: cy.getElementById(id) }, zoom: Math.max(cy.zoom(), 1.1), duration: 260 })
}
function fitAll() { cy?.animate({ fit: { eles: cy.elements(), padding: 60 }, duration: 260 }) }
function autoLayout() {
  emit('before-change')
  cy.elements().layout({ name: 'breadthfirst', directed: true, roots: [INPUT_NODE], padding: 60, spacingFactor: 1.5, animate: false }).run()
  for (const node of cy.nodes()) {
    const pos = node.position()
    props.state.layout.positions[node.id()] = { x: Math.round(pos.x), y: Math.round(pos.y) }
  }
  emit('changed')
}
function toggleLink() {
  linking.value = !linking.value
  linkSource.value = ''
  cy?.nodes().removeClass('link-src')
  if (linking.value) notify('连线模式：先点击来源节点，再点击目标节点')
}
onMounted(() => {
  if (!props.state || !canvas.value) return
  ensureLayout(props.state)
  cy = cytoscape({
    container: canvas.value,
    elements: [...nodeElements() as any, ...edgeElements() as any],
    style: [...GRAPH_STYLE,
      { selector: 'node', style: { label: 'data(label)', 'font-weight': 600 } },
      { selector: '[type = "python"]', style: { 'background-color': '#e9f2ff', 'border-color': '#3978c5', shape: 'round-rectangle' } },
      { selector: '[type = "sql"]', style: { 'background-color': '#edf9f1', 'border-color': '#35a167', shape: 'round-rectangle' } },
      { selector: '[type = "redis"]', style: { 'background-color': '#fff2e5', 'border-color': '#d9832b', shape: 'round-rectangle' } },
      { selector: '[type = "http"]', style: { 'background-color': '#e6f6f8', 'border-color': '#2b9db0', shape: 'round-rectangle' } },
      { selector: '[type = "calc"]', style: { 'background-color': '#fdf0f6', 'border-color': '#c9566e', shape: 'round-rectangle' } },
      { selector: '[type = "input"],[type = "output"]', style: { 'background-color': '#f3f0ff', 'border-color': '#8464d8', shape: 'round-tag' } },
      { selector: 'node.boundary', style: { 'border-style': 'double', 'border-width': 4 } },
      { selector: 'node.run-failed', style: { 'border-color': '#c4534d', 'border-width': 4.5 } },
      { selector: 'node.run-running', style: { 'border-style': 'dashed', 'border-color': '#3978c5' } },
      { selector: 'node.chain-in', style: { 'border-style': 'dashed', 'border-color': '#2b9db0', 'border-width': 4 } },
    ],
    // 打开编排一律全图适配第一眼（缩放/平移仍会记录，但不恢复——避免恢复到指向空白的历史视口）
    layout: { name: 'preset', fit: true, padding: 60 }, wheelSensitivity: 0.2, minZoom: 0.15, maxZoom: 3,
  })
  // preset 布局是异步完成的：挂载后绝不能立即 sync()（会重建元素并把视口退回适配前），
  // 只在布局完成后再检查一次"全图是否越界"，越界才适配。
  ;[400, 1200].forEach(d => setTimeout(() => { try { ensureWholeGraphVisible() } catch { /* 忽略 */ } }, d))
  cy.on('tap', (event: any) => {
    if (event.target === cy) { if (!props.chainMode) emit('select', ''); return }
    if (event.target.isEdge()) { if (!props.chainMode) emit('select', ''); return }
    const id = event.target.id()
    if (props.chainMode) {
      if (id !== INPUT_NODE && id !== OUTPUT_NODE) emit('chain-tap', id)
      return
    }
    if (linking.value && event.target.isNode()) {
      if (!linkSource.value) { linkSource.value = id; event.target.addClass('link-src'); notify('已选起点，请点击目标节点') }
      else if (linkSource.value === id) { linkSource.value = ''; cy.nodes().removeClass('link-src') }
      else {
        emit('link', { sourceId: linkSource.value, targetId: id })
        linking.value = false; linkSource.value = ''; cy.nodes().removeClass('link-src')
      }
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
  })
  cy.on('zoom pan', () => {
    ensureLayout(props.state)
    props.state.layout.zoom = cy.zoom()
    props.state.layout.pan = { x: Math.round(cy.pan().x), y: Math.round(cy.pan().y) }
  })
  cy.on('cxttap', 'edge', (event: any) => emit('unlink', event.target.id()))
  let initialFitDone = false
  observer = new ResizeObserver(() => {
    cy?.resize()
    // 首个回调 = 容器尺寸就绪：此时才能可靠判断"全图是否可见"，越界则适配一次（此后交还用户）
    if (!initialFitDone && cy && cy.width() >= 60 && cy.nodes().length) {
      initialFitDone = true
      const bb = cy.nodes().renderedBoundingBox({ includeLabels: false })
      if (bb.x1 < 0 || bb.y1 < 0 || bb.x2 > cy.width() || bb.y2 > cy.height()) cy.fit(cy.elements(), 60)
    }
  })
  observer.observe(canvas.value)
})
onBeforeUnmount(() => { observer?.disconnect(); cy?.destroy() })
// 撤销/重做/重载会整体替换 state 对象：同步画布（保留视口与选中）。
watch(() => props.state, () => sync())
// 表单里的绑定/改名是原地变更：按派生连线与节点签名自动同步画布（布局写回不触发保存）。
const contentSignature = computed(() => JSON.stringify([
  processingNodes(props.state).map((n: any) => [n.id, n.name, n.kind]),
  derivedEdges(props.state).map((e: any) => [e.id, e.source, e.target, e.label]),
  (props.state?.outputs || []).map((o: any) => [o.id, o.binding?.nodeId || '', o.binding?.outputId || '']),
]))
watch(contentSignature, () => sync())
// 运行结果/链选择变化 → 刷新徽标与高亮（轻量：重建元素但保留视口）
watch(() => JSON.stringify([props.results, props.chainIds, props.chainMode]), () => sync())
defineExpose({ sync, focusNode, fitAll, autoLayout, toggleLink, linking })
</script>
<template>
<div class="canvas-wrapper flow-canvas">
  <div ref="canvas" class="cy-canvas"></div>
  <div class="flow-canvas-tools">
    <button :class="{active:linking}" @click="toggleLink">↗ 连线</button>
    <button @click="autoLayout">整理</button>
    <button @click="fitAll">全图</button>
  </div>
  <span class="canvas-hint">{{chainMode?'链选择：点选按执行顺序的连续节点':'连线从输入绑定派生 · 右键连线可删除绑定'}}</span>
  <span v-if="notice" class="canvas-notice" role="status">{{notice}}</span>
</div>
</template>
<style scoped>
.flow-canvas{position:relative;flex:1;min-width:0;height:100%;background-color:var(--paper-2);background-image:radial-gradient(var(--grid) 1px,transparent 1px);background-size:20px 20px}
.flow-canvas .cy-canvas{position:absolute;inset:0}
.flow-canvas-tools{position:absolute;top:12px;left:12px;display:flex;gap:6px;z-index:5}
.flow-canvas-tools button{font-size:12px;padding:5px 10px;background:#fffffff0}
.flow-canvas-tools button.active{background:var(--blue-soft);border-color:var(--blue-line);color:var(--blue)}
.canvas-notice{position:absolute;top:14px;right:14px;background:var(--warn-soft);border:1px solid var(--warn-line);border-radius:6px;padding:5px 12px;font-size:12px;color:var(--warn);z-index:5}
</style>
