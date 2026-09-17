<!-- ObjectCanvas — 对象/链接关系画布（P01 自旧 App.vue 原样搬运的 cytoscape 编辑器）。
     协议冻结（任务板 §2/§7）：P03 只消费，勿改 props/emits/expose。
     props {state:any}；emits ['before-change','changed','select']；expose {sync,focusNode,create,remove,deleteSelection,layout,setReadonly}
     行为约定（相对旧 App 的差异，均为协议要求）：
     1. zoom/pan 只把布局写回 state.layout，不 emit changed —— 画布缩放/平移绝不触发保存；
     2. 拖动在 grab 时 emit('before-change')（App 侧生成撤销快照=拖动前状态），dragfree 有位移才 emit('changed')；
     3. create/remove/deleteSelection/layout：先 emit('before-change') 再改内存，改完 emit('changed')，由 App 自动持久化；
     4. 撤销/重做按钮由 App 顶栏承担（emits 冻结，不在组件内重复实现）；“维护属性/查看知识模型”等页面级跳转由 ObjectWorkspace 提供；
     5. 属性清单与对象详情工作区属 ObjectWorkspace（P03）；本组件 inspector 只保留名称/业务定义/链接字段/删除。
     组件内提示（标识冲突、引用拦截等）走本地 notice 条，不依赖 App 的全局消息。 -->
<script setup lang="ts">
import { ref, computed, nextTick, onMounted, onBeforeUnmount, watch } from 'vue'
import AppSelect from '../shared/AppSelect.vue'
import EditorField from '../shared/EditorField.vue'
import cytoscape from 'cytoscape'
import { shortcutAction } from '../app/shortcuts'
import { graphReferences } from './editorModel'
import { associationsOf, associationsOfObject, commitAssociations } from './actionModel'
import { ruleAssociationsOf } from './businessRuleModel'
import { GRAPH_STYLE } from '../shared/graphStyle'
import { toElements, captureLayout, applyElements, importedElements } from './graphAdapter'
const props = defineProps<{ state: any }>(), emit = defineEmits(['before-change', 'changed', 'select'])
const canvas = ref<any>(null), selected = ref(''), search = ref(''), linking = ref(false), source = ref<any>(null), modal = ref<any>(null), newId = ref(''), newName = ref(''), newSource = ref(''), newTarget = ref(''), helpOpen = ref(false)
const inspectorOpen = ref(true), catalogOpen = ref(false), readOnly = ref(false), selectionCount = ref(0), hop = ref(false), searchCount = ref(0), sourceMode = ref(false), notice = ref('')
let noticeTimer: any = null, searchIndex = -1
let cy: any = null, dragSnapshot: any = null, observer: any = null
function notifyCanvas(text: string) { notice.value = text; if (noticeTimer) clearTimeout(noticeTimer); noticeTimer = setTimeout(() => notice.value = '', 4000) }
const graph = computed(() => props.state?.ontology['@graph'] || []), types = computed(() => graph.value.filter((n: any) => n['@type'] === 'owl:Class')), relations = computed(() => graph.value.filter((n: any) => n['@type'] === 'owl:ObjectProperty')), node = computed(() => graph.value.find((n: any) => n['@id'] === selected.value))
const importedNode = computed(() => props.state?.sourceGraph?.nodes.find((n: any) => n.id === selected.value)), importedEdge = computed(() => props.state?.sourceGraph?.edges.find((n: any) => n.id === selected.value))
const fieldNames: Record<string, string> = { definition: '业务定义', description: '补充说明', subtype: '分类', instance_table: '实例表（待核实）', aggregate_of: '聚合来源', data_scope: '项目取数范围', applicableObject: '适用对象', unit: '单位', fetch_method: '取数说明（未执行）', subject: '所属主体', rule_type: '规则分类', rule_content: '规则正文（待审核）', param_intro: '参数说明' }
function sync() { if (!cy || sourceMode.value) return; const pan = cy.pan(), zoom = cy.zoom(); applyElements(cy, toElements(props.state)); cy.pan(pan); cy.zoom(zoom); if (selected.value) cy.getElementById(selected.value).select(); applySearch() }
function select(id: string) { selected.value = id; emit('select', id); cy?.elements().unselect(); cy?.getElementById(id).select() }
function edit() { emit('changed'); sync() }
function selectField(record: Record<string, any>, key: string, value: string) { record[key] = value; edit() }
function applySearch() { if (!cy) return; cy.nodes().removeClass('search-hit'); const q = search.value.trim().toLowerCase(); searchIndex = -1; searchCount.value = 0; if (q) { const hits = cy.nodes().filter((n: any) => (n.data('name') + ' ' + n.id()).toLowerCase().includes(q)); hits.addClass('search-hit'); searchCount.value = hits.length } }
function focusSearch(next = false) { const hits = cy.nodes('.search-hit'); if (hits.length) { searchIndex = next ? (searchIndex + 1) % hits.length : 0; cy.nodes().removeClass('search-focus'); hits[searchIndex].addClass('search-focus'); cy.animate({ center: { eles: hits[searchIndex] }, zoom: Math.max(cy.zoom(), 1.2), duration: 250 }) } }
function layout() { emit('before-change'); cy.elements().removeClass('dim'); cy.layout({ name: 'breadthfirst', directed: true, padding: 80, spacingFactor: 1.6, animate: false }).run(); props.state.layout = captureLayout(cy); emit('changed') }
function neighborhood() { if (hop.value) { hop.value = false; return showAll() } if (!selected.value || !cy.getElementById(selected.value).isNode()) return notifyCanvas('先选择一个对象类型'); hop.value = true; let keep = cy.getElementById(selected.value).closedNeighborhood(); cy.elements().addClass('dim'); keep.removeClass('dim'); cy.fit(keep, 80) }
function showAll() { hop.value = false; cy.elements().removeClass('dim search-focus'); cy.fit(undefined, 70) }
function startNew(kind: string, from = '', to = '') { modal.value = kind; newId.value = kind + '_' + crypto.randomUUID().replaceAll('-', ''); newName.value = ''; newSource.value = from || types.value[0]?.['@id']; newTarget.value = to || types.value[1]?.['@id'] || types.value[0]?.['@id'] }
function create() {
  if (!/^[A-Za-z][A-Za-z0-9_]*$/.test(newId.value) || !newName.value.trim()) return notifyCanvas('请填写名称，标识由系统自动生成')
  const id = 'mg:' + newId.value; if (graph.value.some((n: any) => n['@id'] === id)) return notifyCanvas('标识已存在')
  emit('before-change')
  let n: any = { '@id': id, '@type': ({ type: 'owl:Class', relation: 'owl:ObjectProperty', property: 'owl:DatatypeProperty' } as any)[modal.value], 'rdfs:label': newName.value.trim() }
  if (modal.value === 'relation') { n['rdfs:domain'] = { '@id': newSource.value }; n['rdfs:range'] = { '@id': newTarget.value }; n['mg:reverseLabel'] = ''; n['mg:cardinality'] = 'many-to-one' }
  if (modal.value === 'property') { n['rdfs:domain'] = { '@id': selected.value }; n['rdfs:range'] = { '@id': 'xsd:string' } }
  graph.value.push(n); if (modal.value !== 'property') selected.value = id; modal.value = null; source.value = null; sync(); select(selected.value); emit('changed')
}
function ruleRefCount(ids: Set<string>): number {
  const bare = (t: string) => String(t || '').replace(/^mg:/, '')
  return ruleAssociationsOf(props.state).filter((a: any) => [...ids].some(id => bare(a.objectTypeId) === bare(id))).length
}
function remove(id: string) {
  const refs = graphReferences(props.state, id); if (refs.length) return notifyCanvas('暂不能删除：请先处理引用（' + refs.join('、') + '）。')
  const ruleRefs = ruleRefCount(new Set([id])); if (ruleRefs) return notifyCanvas(`暂不能删除：此对象仍引用 ${ruleRefs} 条业务规则；请先移除规则引用。`)
  const assocCount = associationsOfObject(props.state, id).length
  if (!confirm('删除这个定义？可通过撤销恢复。' + (assocCount ? `此对象有 ${assocCount} 条动作关联，将同时移除（动作定义与项目绑定保留）。` : ''))) return
  emit('before-change'); props.state.ontology['@graph'] = graph.value.filter((n: any) => n['@id'] !== id); if (selected.value === id) { selected.value = ''; emit('select', '') }
  purgeAssociations(new Set([id]))
  sync(); emit('changed')
}
function focusSelected() { const sel = cy?.nodes(':selected'); if (!sel?.length) return; cy.stop(); cy.animate({ center: { eles: sel[0] }, zoom: Math.min(4, cy.zoom() * 1.6), duration: 260 }); cy.nodes().removeClass('search-focus'); sel[0].addClass('search-focus') }
function focusNode(id: string) { if (id) select(id); focusSelected() }
function zoomOutSelected() { if (!cy?.nodes().length) return; const bb = cy.nodes().boundingBox(), fit = Math.min((cy.width() - 100) / bb.w, (cy.height() - 100) / bb.h), next = cy.zoom() / 1.6; if (next <= fit) return showAll(); const sel = cy.nodes(':selected'); cy.stop(); cy.animate(sel.length ? { center: { eles: sel[0] }, zoom: next, duration: 260 } : { zoom: next, duration: 260 }) }
function deleteSelection() {
  if (readOnly.value) return
  const sel = cy?.elements(':selected'); if (!sel?.length) return notifyCanvas('请先选择节点或连线')
  const ids = new Set<string>(sel.map((e: any) => e.id()))
  const refs = [...new Set([...ids].flatMap((id: string) => graphReferences(props.state, id)))]; if (refs.length) return notifyCanvas('暂不能删除：请先处理引用（' + refs.join('、') + '）。')
  const ruleRefs = ruleRefCount(ids); if (ruleRefs) return notifyCanvas(`暂不能删除：选中对象仍引用 ${ruleRefs} 条业务规则；请先移除规则引用。`)
  if (!confirm(`删除选中的 ${ids.size} 项定义？可通过撤销恢复。`)) return
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
  const action = shortcutAction(e, { modal: !!modal.value || helpOpen.value, graph: true })
  if (!action || !['focus', 'zoomOut', 'delete', 'help', 'escape'].includes(action)) return
  e.preventDefault()
  ;({ focus: focusSelected, zoomOut: zoomOutSelected, delete: deleteSelection, help: () => helpOpen.value = !helpOpen.value, escape: () => { modal.value = null; helpOpen.value = false; linking.value = false; source.value = null; cy?.nodes().removeClass('link-src') } } as any)[action]?.()
}
onMounted(() => {
  if (!props.state || !canvas.value) return
  cy = cytoscape({ container: canvas.value, elements: sourceMode.value ? importedElements(props.state) : toElements(props.state), style: [...GRAPH_STYLE, { selector: 'node', style: { 'font-size': 15 } }, { selector: 'edge', style: { 'font-size': 13, 'line-opacity': 0.8 } }], layout: { name: 'preset' }, wheelSensitivity: .2, minZoom: .1, maxZoom: 4, boxSelectionEnabled: true })
  if (sourceMode.value) { cy.nodes().ungrabify(); cy.fit(undefined, 60) } else if (props.state.layout?.zoom) { cy.zoom(props.state.layout.zoom); cy.pan(props.state.layout.pan) } else cy.fit(undefined, 90)
  cy.on('tap', (e: any) => { if (e.target === cy) { selected.value = ''; emit('select', ''); return } if (linking.value && e.target.isNode()) { if (!source.value) { source.value = e.target.id(); cy.nodes().removeClass('link-src'); e.target.addClass('link-src') } else if (source.value !== e.target.id()) { startNew('relation', source.value, e.target.id()); cy.nodes().removeClass('link-src') } return } selected.value = e.target.id(); emit('select', selected.value) })
  cy.on('select unselect', () => { selectionCount.value = cy.elements(':selected').length })
  cy.on('dbltap', 'node', async (e: any) => { if (!linking.value) { selected.value = e.target.id(); emit('select', selected.value); inspectorOpen.value = true; await nextTick(); cy.resize(); document.querySelector<HTMLInputElement>('.inspector-fields input:not([readonly])')?.focus() } })
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
defineExpose({ sync, focusNode, create, remove, deleteSelection, layout, setReadonly })
</script>

<template>
<!-- 工具条三组分隔：模式（连线/只读预览）· 操作（新建/整理/删除选中）· 视图（全图/详情/目录/1跳/？）；
     搜索框放最后撑满。组间分隔线由全局 .tool-group border-right 提供。 -->
<div class="canvas-tools"><div class="tool-group"><button :disabled="readOnly" :class="{active:linking}" @click="linking=!linking;source=null;cy.nodes().removeClass('link-src')">↗ 连线模式</button><button :disabled="sourceMode" :class="{active:readOnly}" @click="togglePreview">{{readOnly?'返回编辑':'只读预览'}}</button></div><div class="tool-group"><button class="primary" :disabled="readOnly" @click="startNew('type')">＋ 新建节点</button><button :disabled="readOnly" @click="layout">整理节点</button><button :disabled="readOnly||!selectionCount" @click="deleteSelection">删除选中 {{selectionCount||''}}</button></div><div class="tool-group"><button @click="showAll">全图</button><button :class="{active:inspectorOpen}" @click="toggleInspector">详情</button><button :class="{active:catalogOpen}" @click="toggleCatalog">目录</button><button :class="{active:hop}" @click="neighborhood">1跳</button><button title="快捷键说明 (?)" @click="helpOpen=true">?</button></div><div class="graph-search"><input v-model="search" aria-label="搜索节点名称" placeholder="搜索节点名称…" @input="applySearch" @keydown.enter.prevent="focusSearch(false)" @keydown.tab="searchCount && ($event.preventDefault(),focusSearch(true))"><span v-if="search">{{searchCount}} 个匹配</span><button v-if="search" @click="search='';applySearch()">×</button></div><span v-if="notice" class="canvas-notice" role="status">{{notice}}</span></div>
<div class="graph-workspace" :class="{'with-catalog':catalogOpen,'with-inspector':inspectorOpen}"><div v-if="catalogOpen" class="catalog"><h3>对象类型 · {{types.length}}</h3><button v-for="t in types" :key="t['@id']" class="item" :class="{selected:selected===t['@id']}" @click="select(t['@id'])">◇ {{t['rdfs:label']}}<small>{{t['@id']}}</small></button><div class="subsection"><h3>关系 · {{relations.length}}</h3><button v-for="r in relations" :key="r['@id']" class="item" @click="select(r['@id'])">↗ {{r['rdfs:label']}}</button></div></div>
<div class="canvas-wrapper"><div ref="canvas" class="cy-canvas"></div><span class="canvas-hint">{{linking?(source?'已选起点，请点击终点':'请选择起点节点'):'F 聚焦 · Shift+F 缩小 · Shift+拖动框选 · ? 快捷键'}}</span></div>
<div class="editor imported-inspector" v-if="inspectorOpen && sourceMode"><template v-if="importedNode"><div class="badge">{{importedNode.type}} · 修订教学定义</div><h2>{{importedNode.name}}</h2><small>{{importedNode.id}}</small><p class="review-issue" v-for="issue in state.sourceGraph.issues.filter((i:any)=>i.nodes.includes(importedNode.id))">{{issue.text}}</p><section v-for="(value,key) in importedNode.data" :key="key" class="source-field"><h3>{{fieldNames[key]||key}}</h3><p>{{Array.isArray(value)?value.join('、'):(value||'未填写')}}</p></section></template><template v-else-if="importedEdge"><div class="badge">关系 · 修订版</div><h2>{{importedEdge.relation}}</h2><p>{{importedEdge.source}} → {{importedEdge.target}}</p><p>{{importedEdge.description||'未填写说明'}}</p></template><p v-else>点击节点或连线查看修订定义。按F放大，1跳查看关联。</p></div><div class="editor" v-else-if="inspectorOpen && node"><fieldset class="inspector-fields" :disabled="readOnly"><div class="badge">{{node['@type']==='owl:Class'?'对象类型':'链接类型'}}</div><h2>{{node['rdfs:label']}}</h2><EditorField label="名称" :model-value="node['rdfs:label']" :disabled="readOnly" @before-change="emit('before-change')" @update:model-value="selectField(node,'rdfs:label',($event as any))"/><EditorField label="业务定义" type="textarea" :model-value="node['rdfs:comment']" :disabled="readOnly" @before-change="emit('before-change')" @update:model-value="selectField(node,'rdfs:comment',($event as any))"/>
<template v-if="node['@type']==='owl:ObjectProperty'"><label>数量关系<AppSelect :model-value="node['mg:cardinality']" :disabled="readOnly" aria-label="数量关系" :options="[{value:'one-to-one',label:'一对一'},{value:'one-to-many',label:'一对多'},{value:'many-to-one',label:'多对一'},{value:'many-to-many',label:'多对多'}]" @before-change="emit('before-change')" @update:model-value="selectField(node,'mg:cardinality',($event as any))"/></label><details><summary>更多信息（选填）</summary><EditorField label="反向显示名称" :model-value="node['mg:reverseLabel']" :disabled="readOnly" example="包含设备" @before-change="emit('before-change')" @update:model-value="selectField(node,'mg:reverseLabel',($event as any))"/></details><label>起点<AppSelect :model-value="node['rdfs:domain']['@id']" :disabled="readOnly" aria-label="起点" :options="types.map((t:any)=>({value:t['@id'],label:t['rdfs:label']}))" @before-change="emit('before-change')" @update:model-value="selectField(node['rdfs:domain'],'@id',($event as any))"/></label><label>终点<AppSelect :model-value="node['rdfs:range']['@id']" :disabled="readOnly" aria-label="终点" :options="types.map((t:any)=>({value:t['@id'],label:t['rdfs:label']}))" @before-change="emit('before-change')" @update:model-value="selectField(node['rdfs:range'],'@id',($event as any))"/></label></template>
<button class="danger" @click="remove(node['@id'])">删除定义</button></fieldset></div><div v-else-if="inspectorOpen" class="editor muted">点击节点或连线查看定义</div></div>
<div v-if="helpOpen" class="modal-backdrop" @click.self="helpOpen=false"><section class="modal-card" role="dialog" aria-modal="true" aria-label="快捷键"><h2>画布快捷键</h2><table><tbody><tr v-for="row in [['Ctrl/⌘ + Z','撤销'],['Ctrl/⌘ + Shift + Z','重做'],['Ctrl/⌘ + S','保存草稿（自动保存已开启）'],['Delete / Backspace','删除选中节点或连线'],['F','聚焦选中节点，连续放大'],['Shift + F','缩小至全图'],['Tab（搜索框）','循环定位搜索结果'],['Esc','退出连线 / 关闭弹窗'],['?','打开快捷键说明']]" :key="row[0]"><td><kbd>{{row[0]}}</kbd></td><td>{{row[1]}}</td></tr></tbody></table><p class="muted">输入框内保留文字编辑快捷键。只读预览禁用画布修改。</p><button class="primary" @click="helpOpen=false">关闭</button></section></div>
<div v-if="modal" class="modal-backdrop" @click.self="modal=null"><form class="modal-card" @submit.prevent="create"><h2>新增{{{type:'对象类型',relation:'关系',property:'属性'}[modal]}}</h2><p class="field-help">填写名称即可创建；业务定义等内容可在对应管理页补充。</p><label>名称<input v-model="newName" required autofocus placeholder="例如：储能设备"></label><template v-if="modal==='relation'"><label>起点<AppSelect v-model="newSource" aria-label="起点" :options="types.map((t:any)=>({value:t['@id'],label:t['rdfs:label']}))"/></label><label>终点<AppSelect v-model="newTarget" aria-label="终点" :options="types.map((t:any)=>({value:t['@id'],label:t['rdfs:label']}))"/></label></template><div class="dialogtools"><button type="button" @click="modal=null">取消</button><button class="primary">创建</button></div></form></div>
</template>
