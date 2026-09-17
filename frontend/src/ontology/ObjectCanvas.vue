<!-- ObjectCanvas — 对象/链接关系画布（P01 自旧 App.vue 原样搬运的 cytoscape 编辑器）。
     协议（任务板 §2/§7 + 20260917 对象建模交互评审采纳 R1）：
     props {state:any}；emits ['before-change','changed','select','create-object','edit-definition','create-link']；
     expose {sync,focusNode,remove,deleteSelection,layout,setReadonly}。
     行为约定：
     1. zoom/pan 只把布局写回 state.layout，不 emit changed —— 画布缩放/平移绝不触发保存；
     2. 拖动在 grab 时 emit('before-change')（App 侧生成撤销快照=拖动前状态），dragfree 有位移才 emit('changed')；
     3. remove/deleteSelection/layout：先 emit('before-change') 再改内存，改完 emit('changed')，由 App 自动持久化；
     4. 撤销/重做按钮由 App 顶栏承担（emits 冻结，不在组件内重复实现）；
     5. 侧栏 inspector 只读展示名称／业务定义／链接端点与数量关系，不再就地改字段；
        新建对象、编辑定义、连线创建统一 emit 给 ObjectWorkspace，由列表/画布共用的表单承接
        （R1：保存前不插入模型记录，取消不产生链接）。
     组件内提示（标识冲突、引用拦截等）走本地 notice 条，不依赖 App 的全局消息。 -->
<script setup lang="ts">
import { ref, computed, nextTick, onMounted, onBeforeUnmount, watch } from 'vue'
import { appConfirm } from '../shared/appConfirm'
import cytoscape from 'cytoscape'
import { shortcutAction } from '../app/shortcuts'
import { graphReferences } from './editorModel'
import { associationsOf, associationsOfObject, commitAssociations } from './actionModel'
import { ruleAssociationsOf } from './businessRuleModel'
import { GRAPH_STYLE } from '../shared/graphStyle'
import { toElements, captureLayout, applyElements, importedElements } from './graphAdapter'
const props = defineProps<{ state: any }>(), emit = defineEmits(['before-change', 'changed', 'select', 'create-object', 'edit-definition', 'create-link'])
const canvas = ref<any>(null), selected = ref(''), search = ref(''), linking = ref(false), source = ref<any>(null), helpOpen = ref(false)
const inspectorOpen = ref(true), catalogOpen = ref(false), readOnly = ref(false), selectionCount = ref(0), hop = ref(false), searchCount = ref(0), sourceMode = ref(false), notice = ref('')
let noticeTimer: any = null, searchIndex = -1
let cy: any = null, dragSnapshot: any = null, observer: any = null
function notifyCanvas(text: string) { notice.value = text; if (noticeTimer) clearTimeout(noticeTimer); noticeTimer = setTimeout(() => notice.value = '', 4000) }
const graph = computed(() => props.state?.ontology['@graph'] || []), types = computed(() => graph.value.filter((n: any) => n['@type'] === 'owl:Class')), relations = computed(() => graph.value.filter((n: any) => n['@type'] === 'owl:ObjectProperty')), node = computed(() => graph.value.find((n: any) => n['@id'] === selected.value))
const nodeIsLink = computed(() => node.value?.['@type'] === 'owl:ObjectProperty')
const CARDINALITY: Record<string, string> = { 'one-to-one': '一对一', 'one-to-many': '一对多', 'many-to-one': '多对一', 'many-to-many': '多对多' }
const typeLabel = (id: string) => types.value.find((t: any) => t['@id'] === id)?.['rdfs:label'] || id || '—'
const importedNode = computed(() => props.state?.sourceGraph?.nodes.find((n: any) => n.id === selected.value)), importedEdge = computed(() => props.state?.sourceGraph?.edges.find((n: any) => n.id === selected.value))
const fieldNames: Record<string, string> = { definition: '业务定义', description: '补充说明', subtype: '分类', instance_table: '实例表（待核实）', aggregate_of: '聚合来源', data_scope: '项目取数范围', applicableObject: '适用对象', unit: '单位', fetch_method: '取数说明（未执行）', subject: '所属主体', rule_type: '规则分类', rule_content: '规则正文（待审核）', param_intro: '参数说明' }
// 同步模型到画布：位置取画布当前实际位置，避免仅改名称时把节点按默认网格重新摆放（R1）。
function sync() { if (!cy || sourceMode.value) return; const live = captureLayout(cy); applyElements(cy, toElements({ ...props.state, layout: live })); cy.pan(live.pan); cy.zoom(live.zoom); if (selected.value) cy.getElementById(selected.value).select(); applySearch() }
function select(id: string) { selected.value = id; emit('select', id); cy?.elements().unselect(); cy?.getElementById(id).select() }
function applySearch() { if (!cy) return; cy.nodes().removeClass('search-hit'); const q = search.value.trim().toLowerCase(); searchIndex = -1; searchCount.value = 0; if (q) { const hits = cy.nodes().filter((n: any) => (n.data('name') + ' ' + n.id()).toLowerCase().includes(q)); hits.addClass('search-hit'); searchCount.value = hits.length } }
function focusSearch(next = false) { const hits = cy.nodes('.search-hit'); if (hits.length) { searchIndex = next ? (searchIndex + 1) % hits.length : 0; cy.nodes().removeClass('search-focus'); hits[searchIndex].addClass('search-focus'); cy.animate({ center: { eles: hits[searchIndex] }, zoom: Math.max(cy.zoom(), 1.2), duration: 250 }) } }
function layout() { emit('before-change'); cy.elements().removeClass('dim'); cy.layout({ name: 'breadthfirst', directed: true, padding: 80, spacingFactor: 1.6, animate: false }).run(); props.state.layout = captureLayout(cy); emit('changed') }
function toggleLinking() { if (readOnly.value) return; linking.value = !linking.value; source.value = null; cy?.nodes().removeClass('link-src') }
function neighborhood() { if (hop.value) { hop.value = false; return showAll() } if (!selected.value || !cy.getElementById(selected.value).isNode()) return notifyCanvas('请先选择一个对象'); hop.value = true; let keep = cy.getElementById(selected.value).closedNeighborhood(); cy.elements().addClass('dim'); keep.removeClass('dim'); cy.fit(keep, 80) }
function showAll() { hop.value = false; cy.elements().removeClass('dim search-focus'); cy.fit(undefined, 70) }
function ruleRefCount(ids: Set<string>): number {
  const bare = (t: string) => String(t || '').replace(/^mg:/, '')
  return ruleAssociationsOf(props.state).filter((a: any) => [...ids].some(id => bare(a.objectTypeId) === bare(id))).length
}
async function remove(id: string) {
  const refs = graphReferences(props.state, id); if (refs.length) return notifyCanvas('暂不能删除：请先处理引用（' + refs.join('、') + '）。')
  const ruleRefs = ruleRefCount(new Set([id])); if (ruleRefs) return notifyCanvas(`暂不能删除：此对象仍引用 ${ruleRefs} 条业务规则；请先移除规则引用。`)
  const assocCount = associationsOfObject(props.state, id).length
  if (!(await appConfirm({ message: '删除这个定义？可通过撤销恢复。' + (assocCount ? `此对象有 ${assocCount} 条动作关联，将同时移除（动作定义与项目绑定保留）。` : ''), danger: true }))) return
  emit('before-change'); props.state.ontology['@graph'] = graph.value.filter((n: any) => n['@id'] !== id); if (selected.value === id) { selected.value = ''; emit('select', '') }
  purgeAssociations(new Set([id]))
  sync(); emit('changed')
}
function focusSelected() { const sel = cy?.nodes(':selected'); if (!sel?.length) return; cy.stop(); cy.animate({ center: { eles: sel[0] }, zoom: Math.min(4, cy.zoom() * 1.6), duration: 260 }); cy.nodes().removeClass('search-focus'); sel[0].addClass('search-focus') }
function focusNode(id: string) { if (id) select(id); focusSelected() }
function zoomOutSelected() { if (!cy?.nodes().length) return; const bb = cy.nodes().boundingBox(), fit = Math.min((cy.width() - 100) / bb.w, (cy.height() - 100) / bb.h), next = cy.zoom() / 1.6; if (next <= fit) return showAll(); const sel = cy.nodes(':selected'); cy.stop(); cy.animate(sel.length ? { center: { eles: sel[0] }, zoom: next, duration: 260 } : { zoom: next, duration: 260 }) }
async function deleteSelection() {
  if (readOnly.value) return
  const sel = cy?.elements(':selected'); if (!sel?.length) return notifyCanvas('请先选择节点或连线')
  const ids = new Set<string>(sel.map((e: any) => e.id()))
  const refs = [...new Set([...ids].flatMap((id: string) => graphReferences(props.state, id)))]; if (refs.length) return notifyCanvas('暂不能删除：请先处理引用（' + refs.join('、') + '）。')
  const ruleRefs = ruleRefCount(ids); if (ruleRefs) return notifyCanvas(`暂不能删除：选中对象仍引用 ${ruleRefs} 条业务规则；请先移除规则引用。`)
  if (!(await appConfirm({ message: `删除选中的 ${ids.size} 项定义？可通过撤销恢复。`, danger: true }))) return
  emit('before-change'); props.state.ontology['@graph'] = graph.value.filter((n: any) => !ids.has(n['@id'])); selected.value = ''; emit('select', ''); purgeAssociations(ids); sync(); emit('changed')
}
// 删除对象时只清理该对象草稿中的动作关联（需求 §5）；连线等其他定义不受影响。
function purgeAssociations(ids: Set<string>) {
  const stale = associationsOf(props.state).filter((a: any) => ids.has(a.objectTypeId) || ids.has('mg:' + String(a.objectTypeId).replace(/^mg:/, '')))
  if (stale.length) commitAssociations(props.state, associationsOf(props.state).filter((a: any) => !stale.includes(a)))
}
function setReadonly(v: boolean) { readOnly.value = v; if (!v) { linking.value = false; source.value = null; cy?.nodes().removeClass('link-src') } if (cy) { v ? cy.nodes().ungrabify() : cy.nodes().grabify() } }
async function toggleInspector() { inspectorOpen.value = !inspectorOpen.value; await nextTick(); cy.resize() }
async function toggleCatalog() { catalogOpen.value = !catalogOpen.value; await nextTick(); cy.resize() }
function togglePreview() { if (sourceMode.value) return; readOnly.value = !readOnly.value; linking.value = false; source.value = null; cy.nodes().removeClass('link-src'); readOnly.value ? cy.nodes().ungrabify() : cy.nodes().grabify() }
async function switchGraphMode() {
  if (!sourceMode.value) props.state.layout = captureLayout(cy)
  sourceMode.value = !sourceMode.value; readOnly.value = sourceMode.value; selected.value = ''; emit('select', '')
  cy.elements().remove(); cy.add(sourceMode.value ? importedElements(props.state) : toElements(props.state))
  if (sourceMode.value) { cy.nodes().ungrabify(); inspectorOpen.value = true; selected.value = 'czy:entity:04' } else { cy.nodes().grabify() }
  cy.fit(undefined, 60); search.value = ''; applySearch(); await nextTick(); cy.resize()
}
// 画布快捷键（shortcuts.ts 不改）：save/undo/redo 由 App 顶栏统一处理，这里只接画布专属键。
function keydown(e: KeyboardEvent) {
  const action = shortcutAction(e, { modal: helpOpen.value, graph: true })
  if (!action || !['focus', 'zoomOut', 'delete', 'help', 'escape'].includes(action)) return
  e.preventDefault()
  ;({ focus: focusSelected, zoomOut: zoomOutSelected, delete: deleteSelection, help: () => helpOpen.value = !helpOpen.value, escape: () => { helpOpen.value = false; linking.value = false; source.value = null; cy?.nodes().removeClass('link-src') } } as any)[action]?.()
}
onMounted(() => {
  if (!props.state || !canvas.value) return
  cy = cytoscape({ container: canvas.value, elements: sourceMode.value ? importedElements(props.state) : toElements(props.state), style: [...GRAPH_STYLE, { selector: 'node', style: { 'font-size': 15 } }, { selector: 'edge', style: { 'font-size': 13, 'line-opacity': 0.8 } }], layout: { name: 'preset' }, wheelSensitivity: .2, minZoom: .1, maxZoom: 4, boxSelectionEnabled: true })
  if (sourceMode.value) { cy.nodes().ungrabify(); cy.fit(undefined, 60) } else if (props.state.layout?.zoom) { cy.zoom(props.state.layout.zoom); cy.pan(props.state.layout.pan) } else cy.fit(undefined, 90)
  cy.on('tap', (e: any) => { if (e.target === cy) { selected.value = ''; emit('select', ''); return } if (linking.value && e.target.isNode()) { if (!source.value) { source.value = e.target.id(); cy.nodes().removeClass('link-src'); e.target.addClass('link-src') } else if (source.value !== e.target.id()) { emit('create-link', { from: source.value, to: e.target.id() }); linking.value = false; source.value = null; cy.nodes().removeClass('link-src'); notifyCanvas('请在链接表单中确认并保存，取消不会建立链接') } return } selected.value = e.target.id(); emit('select', selected.value) })
  cy.on('select unselect', () => { selectionCount.value = cy.elements(':selected').length })
  cy.on('dbltap', 'node', async (e: any) => { if (!linking.value) { selected.value = e.target.id(); emit('select', selected.value); inspectorOpen.value = true; await nextTick(); cy.resize() } })
  // 拖动：grab 时先让 App 快照拖动前状态（撤销语义），dragfree 有位移才 changed；zoom/pan 只记录布局不触发保存。
  cy.on('grab', 'node', () => { dragSnapshot = captureLayout(cy); emit('before-change') })
  cy.on('dragfree', 'node', () => { const now = captureLayout(cy); if (dragSnapshot && JSON.stringify(dragSnapshot.positions) !== JSON.stringify(now.positions)) emit('changed'); dragSnapshot = null; props.state.layout = now })
  cy.on('zoom pan', () => { if (props.state && !sourceMode.value) props.state.layout = captureLayout(cy) })
  observer = new ResizeObserver(() => cy.resize()); observer.observe(canvas.value)
  selected.value = types.value[0]?.['@id'] || ''; select(selected.value)
  window.addEventListener('keydown', keydown)
})
onBeforeUnmount(() => { observer?.disconnect(); cy?.destroy(); window.removeEventListener('keydown', keydown) })
// 撤销/重做会整体替换 state 对象：同步画布内容（保留视口与选中）。
watch(() => props.state, () => { if (cy) sync() })
defineExpose({ sync, focusNode, remove, deleteSelection, layout, setReadonly })
</script>

<template>
<!-- 工具条三组分隔：模式（连线/只读预览）· 操作（新建对象/自动布局/删除选中）· 视图（显示全部/详情/目录/直接关联/？）；
     搜索框放最后撑满。组间分隔线由全局 .tool-group border-right 提供。文案见 20260917 R6。 -->
<div class="canvas-tools"><div class="tool-group"><button :disabled="readOnly" :class="{active:linking}" @click="toggleLinking">↗ 连线模式</button><button :disabled="sourceMode" :class="{active:readOnly}" @click="togglePreview">{{readOnly?'返回编辑':'只读预览'}}</button></div><div class="tool-group"><button class="primary" :disabled="readOnly" @click="emit('create-object')">＋ 新建对象</button><button :disabled="readOnly" @click="layout">自动布局</button><button :disabled="readOnly||!selectionCount" @click="deleteSelection">删除选中 {{selectionCount||''}}</button></div><div class="tool-group"><button @click="showAll">显示全部</button><button :class="{active:inspectorOpen}" @click="toggleInspector">详情</button><button :class="{active:catalogOpen}" @click="toggleCatalog">目录</button><button :class="{active:hop}" title="只看与选中对象直接关联的节点" @click="neighborhood">直接关联</button><button title="快捷键说明 (?)" @click="helpOpen=true">?</button></div><div class="graph-search"><input v-model="search" aria-label="搜索节点名称" placeholder="搜索节点名称…" @input="applySearch" @keydown.enter.prevent="focusSearch(false)" @keydown.tab="searchCount && ($event.preventDefault(),focusSearch(true))"><span v-if="search">{{searchCount}} 个匹配</span><button v-if="search" @click="search='';applySearch()">×</button></div><span v-if="notice" class="canvas-notice" role="status">{{notice}}</span></div>
<p v-if="linking" class="canvas-mode-note" role="status">{{source?'已选起点「'+typeLabel(source)+'」，请点击终点节点建立链接；按 Esc 退出连线模式':'连线模式：依次选择起点和终点；按 Esc 退出'}}</p>
<p v-else-if="readOnly && !sourceMode" class="canvas-mode-note readonly" role="status">只读预览：画布内容不可修改；点「返回编辑」恢复。</p>
<div class="graph-workspace" :class="{'with-catalog':catalogOpen,'with-inspector':inspectorOpen}"><div v-if="catalogOpen" class="catalog"><h3>对象类型 · {{types.length}}</h3><button v-for="t in types" :key="t['@id']" class="item" :class="{selected:selected===t['@id']}" @click="select(t['@id'])">◇ {{t['rdfs:label']}}<small>{{t['@id']}}</small></button><div class="subsection"><h3>关系 · {{relations.length}}</h3><button v-for="r in relations" :key="r['@id']" class="item" @click="select(r['@id'])">↗ {{r['rdfs:label']}}</button></div></div>
<div class="canvas-wrapper"><div ref="canvas" class="cy-canvas"></div><span class="canvas-hint">{{linking?(source?'已选起点，请点击终点':'请选择起点节点'):'F 聚焦 · Shift+F 缩小 · Shift+拖动框选 · ? 快捷键'}}</span></div>
<div class="editor imported-inspector" v-if="inspectorOpen && sourceMode"><template v-if="importedNode"><div class="badge">{{importedNode.type}} · 修订教学定义</div><h2>{{importedNode.name}}</h2><small>{{importedNode.id}}</small><p class="review-issue" v-for="issue in state.sourceGraph.issues.filter((i:any)=>i.nodes.includes(importedNode.id))">{{issue.text}}</p><section v-for="(value,key) in importedNode.data" :key="key" class="source-field"><h3>{{fieldNames[key]||key}}</h3><p>{{Array.isArray(value)?value.join('、'):(value||'未填写')}}</p></section></template><template v-else-if="importedEdge"><div class="badge">关系 · 修订版</div><h2>{{importedEdge.relation}}</h2><p>{{importedEdge.source}} → {{importedEdge.target}}</p><p>{{importedEdge.description||'未填写说明'}}</p></template><p v-else>点击节点或连线查看修订定义。按F放大，1跳查看关联。</p></div><div class="editor" v-else-if="inspectorOpen && node"><fieldset class="inspector-fields" :disabled="readOnly"><div class="badge">{{nodeIsLink?'链接类型':'对象类型'}}</div><h2>{{node['rdfs:label'] || '未命名'}}</h2><p class="inspector-def">{{node['rdfs:comment'] || '暂无业务定义。'}}</p><dl class="inspector-read"><template v-if="nodeIsLink"><dt>起点</dt><dd>{{typeLabel(node['rdfs:domain']?.['@id'])}}</dd><dt>终点</dt><dd>{{typeLabel(node['rdfs:range']?.['@id'])}}</dd><dt>数量关系</dt><dd>{{CARDINALITY[node['mg:cardinality']]||node['mg:cardinality']||'—'}}</dd><template v-if="node['mg:reverseLabel']"><dt>反向名称</dt><dd>{{node['mg:reverseLabel']}}</dd></template></template></dl><p class="muted">此处只读预览；改名称、业务定义与端点请用「编辑定义」。</p>
<div class="tools"><button class="primary" @click="emit('edit-definition', node['@id'])">编辑定义</button><button class="danger" @click="remove(node['@id'])">删除定义</button></div></fieldset></div><div v-else-if="inspectorOpen" class="editor muted">点击节点或连线查看定义</div></div>
<div v-if="helpOpen" class="modal-backdrop" @click.self="helpOpen=false"><section class="modal-card" role="dialog" aria-modal="true" aria-label="快捷键"><h2>画布快捷键</h2><table><tbody><tr v-for="row in [['Ctrl/⌘ + Z','撤销'],['Ctrl/⌘ + Shift + Z','重做'],['Ctrl/⌘ + S','保存草稿（自动保存已开启）'],['Delete / Backspace','删除选中节点或连线'],['F','聚焦选中节点，连续放大'],['Shift + F','缩小至全图'],['Tab（搜索框）','循环定位搜索结果'],['Esc','退出连线 / 关闭弹窗'],['?','打开快捷键说明']]" :key="row[0]"><td><kbd>{{row[0]}}</kbd></td><td>{{row[1]}}</td></tr></tbody></table><p class="muted">输入框内保留文字编辑快捷键。只读预览禁用画布修改。</p><button class="primary" @click="helpOpen=false">关闭</button></section></div>
</template>

<style scoped>
/* 侧栏只读详情（R1）：名称/业务定义/端点只展示，编辑统一走「编辑定义」 */
.inspector-def{font-size:13px;color:var(--ink-2);white-space:pre-wrap;margin:6px 0 10px}
.inspector-read{display:grid;grid-template-columns:80px minmax(0,1fr);gap:2px 10px;font-size:13px;margin:0 0 10px}
.inspector-read dt{color:var(--muted)}
.inspector-read dd{margin:0;overflow-wrap:anywhere}
.inspector-fields .tools{margin-top:14px}
</style>
