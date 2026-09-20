<!-- ─── 对象建模工作区（P03 / T02 对齐原型 objectWorkspace + editorView）───
     挂载点：App.vue view==='objects'（旧 #model/#links/#graph/#properties 深链均 alias 到此页）。
     协议冻结（任务板 §2/§7，勿改）：props {state:any; focusType?; focusProperty?}；
     emits ['before-change','changed','navigate']。
     浏览态：工具行（对象列表/本体图谱 + ＋新建对象）在目录＋详情上方；
     左目录（157px）+ 右详情（名称/业务定义展示 + 「编辑定义」+ 属性/链接两页签）。
     编辑态（原型 render: ui.editor ? editorView() : pageView()）：主内容整体替换为一个完整表单，
     顶栏与主侧栏保留；表单上方「← 返回对象」，底部保存/取消。新建对象/编辑定义/新建属性/
     编辑属性/新建链接/编辑链接/从属性库添加全部一步进入完整表单，保存前不写入 graph
     （D02/D03）。「从属性库添加」为原型 libraryView(true) 挑选态：标题「为【对象名】添加属性」，
     每行「复制为私有」「引用共享」两个动作（D05），完成落盘后返回原对象并定位新增属性。
     链接页签同时展示当前对象作为起点和终点的链接，一行一个三元组，同一链接只维护一份（D06）。
     保存约定（T00 契约，app/formGuard.ts）：对象/链接表单注册 form-guard 离开保护，
     保存走 form-save.submitForm('ontology', mutate) 一次落盘，取消直接丢弃草稿；
     属性表单由 PropertyManager 自带同一契约；浏览态删除仍走撤销快照 + changed。
     20260919：关系画布（ObjectCanvas）按用户要求移除——对象/链接可视化改用「本体图谱」
     只读视图；「在画布查看」入口与画布来源表单（origin='canvas'）一并移除。 -->
<script setup lang="ts">
import { ref, computed, watch, nextTick, inject, onMounted, onBeforeUnmount } from 'vue'
import LegacyGraphHost from './legacyGraph/LegacyGraphHost.vue'
import PropertyManager from './PropertyManager.vue'
import Field from '../shared/EditorField.vue'
import EditorHead from '../shared/EditorHead.vue'
import AppSelect from '../shared/AppSelect.vue'
import RulePicker from './RulePicker.vue'
import PickerDialog, { type PickerRow } from './PickerDialog.vue'
import { navIcons } from '../shared/icons'
import OntologyList from '../shared/OntologyList.vue'
import OntDrawer from '../shared/OntDrawer.vue'
import SearchField from '../shared/SearchField.vue'
import ListPager from '../shared/ListPager.vue'
import { localProperties, effectiveProperty, propertyTypeLabel, propertyDataType, dataTypeLabel, valueShapeOf, copyAsPrivate, addReference, shapeConflict } from './propertyModel'
import { useOntTable, type OntTable } from './ontList'
import { appConfirm } from '../shared/appConfirm'
import { prefGet, prefSet } from '../app/auth'
import { graphReferences, shortType } from './editorModel'
import { actionDeleteCheck, objectDeleteCheck, ruleDeleteCheck, sharedDeleteCheck } from './dependencyModel'
import { actionsOf, associationsOf, associationsOfObject, commitAssociations } from './actionModel'
import { commitRuleAssociations, ruleAssociationsOf, rulesOf, rulesOfObject } from './businessRuleModel'
import type { FormGuardAPI, FormSaveAPI } from '../app/formGuard'

// focusType/focusProperty：共享属性库「查看引用」/校验问题/旧深链跳转定位
// （propertyFocusId 存 apiName 或 @id，此处换算为节点 @id；指向共享定义时落到首个引用属性）。
// graphReturn/graphFocus（20260919 图谱画布优化）：图谱「打开定义」跳转后的返回上下文——
// graphReturn=true 直接落在图谱视图（视图记忆由迁入编辑器 legacyGraph 按账号 + 本体恢复），
// graphFocus 为目标业务稳定 ID，用于返回后在画布中定位（目标已删除则清空选择并提示）。
const props = defineProps<{ state: any; focusType?: string; focusProperty?: string; initialTab?: string; focusDefinition?: string; focusCreate?: boolean; graphReturn?: boolean; graphFocus?: string; canvasReturn?: string; saveState?: { kind: string; text: string }; latestRelease?: string; editFocus?: boolean; returnTo?: { view: string; focus?: Record<string, any>; label: string } }>(), emit = defineEmits(['before-change', 'changed', 'navigate', 'switch-ontology', 'create-ontology'])
// S4（20260920 验收）：从共享属性库「对象属性引用」定位而来时的来源上下文（App 分发）；
// 对象建模只是定位目标页，返回入口让用户处理完引用后回到原共享定义继续操作。
function backToSource() { if (props.returnTo) emit('navigate', props.returnTo.view, { ...(props.returnTo.focus || {}), openUsages: true } as any) }
// 具名撤销（20260918）：emit('before-change', { actionLabel, target?, mergeKey? })；App 侧兼容字符串与对象
const guardApi = inject<FormGuardAPI>('form-guard')!
const formSave = inject<FormSaveAPI>('form-save')!

const mode = ref<'list' | 'graph'>(props.graphReturn ? 'graph' : 'list'), selected = ref(''), message = ref('')
// 图谱返回：直接落在图谱视图（视图记忆由迁入编辑器恢复）；图谱跳转到定义时切回列表/详情。
// 该 watch 必须排在其他定位 watch 之前（Vue 按创建顺序触发），保证定位发生时视图已就位。
watch(() => props.graphReturn, (on) => { if (on) mode.value = 'graph' }, { immediate: true })
watch(() => props.canvasReturn, (id) => { if (id) mode.value = 'list' }, { immediate: true })
function onGraphNavigate(view: string, focus?: Record<string, any>) { emit('navigate', view, focus as any) }
function onSwitchOntology(id: string) { emit('switch-ontology', id) }
function onCreateOntology(name: string) { emit('create-ontology', name) }
function backToGraph() {
  // 2026-09-20 修复：返回图谱必须先关闭编辑态，否则同组件接收 graph:true 时仍渲染表单
  //（表现为停在表单且返回上下文被清成「← 返回对象」）。
  editor.value = null
  mode.value = 'graph'
  emit('navigate', 'objects', { graph: true, graphFocus: props.canvasReturn || '' } as any)
}

const graph = computed(() => props.state?.ontology?.['@graph'] || [])
const objects = computed(() => graph.value.filter((n: any) => n['@type'] === 'owl:Class'))
const current = computed(() => objects.value.find((n: any) => n['@id'] === selected.value) || null)

// 撤销/删除后选中项可能失效：回落到第一个对象；无对象时详情区显示引导空态。
watch(objects, (list: any[]) => { if (!list.some((n: any) => n['@id'] === selected.value)) selected.value = list[0]?.['@id'] || '' }, { immediate: true })

// 提示文案可读化：把校验/拦截消息里出现的稳定 ID 换成业务名称（用户看不到原始 mg:xxx）。
// 先取最长 ID 优先替换，避免短 ID 命中长 ID 的一部分。
const friendlyMessage = (text: string): string => {
  const records: any[] = [...(graph.value || []), ...['functions', 'actions', 'interfaces'].flatMap(k => props.state?.workflow?.[k] || [])]
  return [...records]
    .filter((n: any) => n && (n['@id'] || n.id))
    .sort((a: any, b: any) => String(b['@id'] || b.id).length - String(a['@id'] || a.id).length)
    .reduce((out: string, n: any) => {
      const id = String(n['@id'] || n.id)
      if (!id || !out.includes(id)) return out
      const merged: any = n['@type'] === 'mg:SharedProperty' ? n : effectiveProperty(n, graph.value)
      const label = merged?.['rdfs:label'] || n['mg:apiName'] || n.name || ''
      return label && label !== id ? out.split(id).join('「' + label + '」') : out
    }, String(text || ''))
}

function typeName(id?: string) { const hit: any = objects.value.find((n: any) => n['@id'] === id); return hit?.['rdfs:label'] || id || '—' }

type Tab = 'props' | 'links' | 'actions' | 'rules'
const detailTab = ref<Tab>('props')
// 深链指定页签（动作库/规则库反向引用 → 对象对应页签）：仅接受已知值。
watch(() => props.initialTab, t => { if (t === 'props' || t === 'links' || t === 'actions' || t === 'rules') detailTab.value = t }, { immediate: true })

// ─── 编辑态（原型 editorView）：kind 决定挂载哪种表单；带 draft 的由本组件注册 form-guard ───
// 表单来源：关系画布移除后只剩列表入口；保留字段以便 Editor 结构稳定。
type Origin = 'list'
type LinkDraft = { label: string; from: string; to: string; cardinality: string; reverseLabel: string; comment: string }
type Editor =
  | { kind: 'object'; isNew: boolean; id: string; draft: { label: string; comment: string }; original: string; returnTab: Tab; origin: Origin }
  | { kind: 'property'; targetTypeId: string; propertyId: string; returnTab: Tab }
  | { kind: 'link'; isNew: boolean; id: string; draft: LinkDraft; original: string; returnTab: Tab; origin: Origin }
const editor = ref<Editor | null>(null)
const editorError = ref(''), editorSaving = ref(false)
const objectDraft = computed(() => editor.value?.kind === 'object' ? editor.value.draft : null)
const linkDraft = computed(() => editor.value?.kind === 'link' ? editor.value.draft : null)

// 提示只对当前上下文有效：切换对象、切页签、进出编辑器都清空。
// 否则「暂不能删除…」这类提示会在换对象甚至新建对象后继续挂着，与当前对象不再对应。
watch([selected, detailTab, mode, editor], () => { if (message.value) message.value = '' })

// T00 离开保护：仅对象/链接草稿注册（属性表单由 PropertyManager 自带；挑选态无本地草稿）。
const wsGuard = { isDirty: () => { const e = editor.value; return !!e && 'draft' in e && JSON.stringify(e.draft) !== e.original }, discard: () => { editor.value = null } }
watch(() => { const e = editor.value; return !!e && (e.kind === 'object' || e.kind === 'link') }, open => { open ? guardApi.register(wsGuard) : guardApi.unregister(wsGuard) }, { immediate: true })
onBeforeUnmount(() => guardApi.unregister(wsGuard))

function closeEditor() {
  editor.value = null; editorError.value = ''
  restoreListScroll()
}

// 列表滚动位置：表单会替换整个主内容导致列表卸载，返回时按原位置还原（R1 §3.2）。
let listScrollTop = 0
function captureListScroll() { listScrollTop = document.querySelector<HTMLElement>('.ld-items')?.scrollTop || 0 }
function restoreListScroll() { void nextTick(() => { const el = document.querySelector<HTMLElement>('.ld-items'); if (el) el.scrollTop = listScrollTop }) }

// 保存成功/取消后返回原对象与原页签，并定位（闪烁 + 滚动到可见）条目。
const highlightId = ref('')
let highlightTimer: ReturnType<typeof setTimeout> | null = null
// 定位（保存后/深链）：目标不在当前筛选结果里时，清除妨碍定位的筛选并翻到目标页——
// 只对「对象类型」生效；属性/链接/动作/规则行只做滚动定位，不动列表筛选。
async function locate(id: string) {
  if (!id) return
  let idx = filteredObjects.value.findIndex((o: any) => o['@id'] === id)
  if (idx < 0 && objects.value.some((o: any) => o['@id'] === id)) {
    clearFilters()
    await nextTick()
    idx = filteredObjects.value.findIndex((o: any) => o['@id'] === id)
  }
  if (idx >= 0) page.value = Math.floor(idx / pageSize) + 1
  highlightId.value = id
  await nextTick()
  document.querySelector(`[data-row="${id}"]`)?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  if (highlightTimer) clearTimeout(highlightTimer)
  highlightTimer = setTimeout(() => { if (highlightId.value === id) highlightId.value = '' }, 2600)
}

// ─── 对象定义表单（D03：新建不再 prompt、不预建占位；名称+业务定义必填） ───
function openObjectEditor(isNew: boolean, origin: Origin = 'list') {
  const n: any = isNew ? null : current.value
  if (!isNew && !n) return
  if (origin === 'list') captureListScroll()
  const draft = { label: n?.['rdfs:label'] || '', comment: n?.['rdfs:comment'] || '' }
  editor.value = { kind: 'object', isNew, id: isNew ? 'mg:object_' + crypto.randomUUID().replaceAll('-', '') : n['@id'], draft, original: JSON.stringify(draft), returnTab: detailTab.value, origin }
  editorError.value = ''
}
// 工作概览「创建第一个对象」：自动打开既有新建对象表单（H02，20260919 概览优化）。
// 必须放在 editor/listScrollTop 声明与本函数定义之后：immediate 回调会经 openObjectEditor
// 触达它们，放前面会踩 TDZ 且错误被 Vue 的 watcher 错误处理吞掉（表单静默打不开）。
// 信号在离开对象建模时由 App 清除，重新进入页面不会重复弹表单。
async function saveObject() {
  const e = editor.value
  if (!e || e.kind !== 'object' || editorSaving.value) return
  const name = e.draft.label.trim(), def = e.draft.comment.trim()
  if (!name) { editorError.value = '请填写对象名称。'; return }
  if (!def) { editorError.value = '请填写业务定义。'; return }
  editorSaving.value = true; editorError.value = ''
  const r = await formSave.submitForm('ontology', () => {
    if (e.isNew) graph.value.push({ '@id': e.id, '@type': 'owl:Class', 'rdfs:label': name, 'rdfs:comment': def })
    else { const n: any = graph.value.find(x => x['@id'] === e.id); if (n) { n['rdfs:label'] = name; n['rdfs:comment'] = def } }
  }, { actionLabel: (e.isNew ? '新建对象「' : '修改对象「') + name + '」', target: { kind: 'object', id: e.id } })
  editorSaving.value = false
  if (!r.ok) { editorError.value = r.message; return }
  editor.value = null
  if (props.canvasReturn) { backToGraph(); return } // 来自图谱：保存后回画布并定位
  selected.value = e.id
  detailTab.value = e.isNew ? 'props' : e.returnTab
  locate(e.id); restoreListScroll()
}

// ─── 属性表单：整体替换主内容，挂载无目录的独立表单 PropertyManager ───
function openPropertyEditor(typeId: string, propertyId: string) {
  captureListScroll()
  selected.value = typeId
  editor.value = { kind: 'property', targetTypeId: typeId, propertyId, returnTab: detailTab.value }
  editorError.value = ''
}
function onPropertySaved(payload: { id: string; targetTypeId?: string }) {
  const e = editor.value
  editor.value = null
  if (props.canvasReturn) { backToGraph(); return } // 来自图谱：保存后直接回画布
  selected.value = payload.targetTypeId || (e?.kind === 'property' ? e.targetTypeId : '') || selected.value
  detailTab.value = e?.kind === 'property' ? e.returnTab : 'props'
  locateRow(payload.id, propsList)
}

// ─── 链接表单（D06：起点/终点/正向名称/数量关系必填直展；反向名称与业务定义折叠） ───
const CARDINALITY: Record<string, string> = { 'one-to-one': '一对一', 'one-to-many': '一对多', 'many-to-one': '多对一', 'many-to-many': '多对多' }
const linkTargetOptions = computed(() => objects.value.map((o: any) => ({ value: o['@id'], label: o['rdfs:label'] || o['@id'] })))
const cardinalityOptions = Object.entries(CARDINALITY).map(([value, label]) => ({ value, label }))
// id：编辑既有链接；缺省新建（起点默认当前对象）。
function openLinkEditor(id = '', origin: Origin = 'list') {
  const n: any = id ? graph.value.find(x => x['@id'] === id) : null
  const fallbackFrom = current.value?.['@id'] || objects.value[0]?.['@id'] || ''
  if (!n && !fallbackFrom) return
  if (origin === 'list') captureListScroll()
  const draft: LinkDraft = n
    ? { label: n['rdfs:label'] || '', from: n['rdfs:domain']?.['@id'] || fallbackFrom, to: n['rdfs:range']?.['@id'] || '', cardinality: n['mg:cardinality'] || 'many-to-one', reverseLabel: n['mg:reverseLabel'] || '', comment: n['rdfs:comment'] || '' }
    : { label: '', from: fallbackFrom, to: objects.value.find(o => o['@id'] !== fallbackFrom)?.['@id'] || fallbackFrom, cardinality: 'many-to-one', reverseLabel: '', comment: '' }
  editor.value = { kind: 'link', isNew: !id, id: id || 'mg:link_' + crypto.randomUUID().replaceAll('-', ''), draft, original: JSON.stringify(draft), returnTab: detailTab.value === 'links' ? 'links' : detailTab.value, origin }
  editorError.value = ''
}
async function saveLink() {
  const e = editor.value
  if (!e || e.kind !== 'link' || editorSaving.value) return
  const d = e.draft, name = d.label.trim()
  if (!name) { editorError.value = '请填写正向名称。'; return }
  if (!d.from) { editorError.value = '请选择起点对象。'; return }
  if (!d.to) { editorError.value = '请选择终点对象。'; return }
  if (!d.cardinality) { editorError.value = '请选择数量关系。'; return }
  const dup = graph.value.find((n: any) => n['@type'] === 'owl:ObjectProperty' && n['@id'] !== e.id && n['rdfs:domain']?.['@id'] === d.from && n['rdfs:label'] === name)
  if (dup) { editorError.value = '起点对象已有同名链接。'; return }
  editorSaving.value = true; editorError.value = ''
  const r = await formSave.submitForm('ontology', () => {
    let n: any = graph.value.find(x => x['@id'] === e.id)
    if (!n) { n = { '@id': e.id, '@type': 'owl:ObjectProperty' }; graph.value.push(n) }
    n['rdfs:label'] = name; n['rdfs:domain'] = { '@id': d.from }; n['rdfs:range'] = { '@id': d.to }
    n['mg:cardinality'] = d.cardinality; n['mg:reverseLabel'] = d.reverseLabel; n['rdfs:comment'] = d.comment
  }, { actionLabel: (e.isNew ? '新建链接「' : '修改链接「') + typeName(d.from) + ' → ' + name + ' → ' + typeName(d.to) + '」', target: { kind: 'link', id: e.id, ownerId: d.from } })
  editorSaving.value = false
  if (!r.ok) { editorError.value = r.message; return }
  editor.value = null
  if (props.canvasReturn) { backToGraph(); return } // 来自图谱：保存后回画布并定位
  detailTab.value = e.returnTab
  locate(e.id); restoreListScroll()
}

// ─── 从属性库添加（D05：原型 libraryView(true) 挑选态，不再跳库页重选对象） ───
const sharedDefs = computed(() => graph.value.filter((n: any) => n['@type'] === 'mg:SharedProperty'))
function sharedRow(s: any) {
  return {
    id: s['@id'], label: s['rdfs:label'] || '未命名共享属性',
    type: propertyTypeLabel(s, graph.value),
    unit: s['mg:valueSuffix'] || '', desc: s['rdfs:comment'] || '',
    usage: graph.value.filter((n: any) => n['@type'] === 'owl:DatatypeProperty' && n['mg:sharedProperty']?.['@id'] === s['@id']).length,
    conflict: !!current.value && !!shapeConflict(graph.value, s, current.value['@id'])
  }
}
// 从属性库添加：统一走 PickerDialog（行内两个动作：复制为私有 / 引用共享）。
const libraryOpen = ref(false), libraryError = ref('')
const libraryItems = computed<PickerRow[]>(() => sharedDefs.value.map((s: any) => {
  const row = sharedRow(s)
  return {
    id: row.id,
    label: row.label + '（' + row.type + (row.unit ? ' · 单位 ' + row.unit : '') + '）',
    note: (row.desc || '暂无业务定义') + ' · ' + row.usage + ' 处引用'
      + (row.conflict ? ' · 已有同名但数据类型不同的属性，无法引用，可复制为私有' : ''),
    actions: [{ label: '复制为私有', value: 'copy' }, { label: '引用共享', value: 'ref', disabled: row.conflict }],
  }
}))
function openLibraryEditor() { if (!current.value) return; libraryError.value = ''; libraryOpen.value = true }
async function pickShared(id: string, action: string) {
  const target = current.value
  const s: any = sharedDefs.value.find((x: any) => x['@id'] === id)
  if (!target || !s || editorSaving.value) return
  const typeId = target['@id']
  const name = s['rdfs:label'] || ''
  const existing = localProperties(graph.value, typeId)
  if (action === 'ref' && shapeConflict(graph.value, s, typeId)) { libraryError.value = '「' + name + '」与已有同名属性的数据类型不同，无法引用同一份共享定义；可改用「复制为私有」。'; return }
  if (action === 'ref' && existing.some((p: any) => p['mg:sharedProperty']?.['@id'] === s['@id'])) { libraryError.value = '此对象已引用「' + name + '」。'; return }
  if (existing.some((p: any) => effectiveProperty(p, graph.value)['rdfs:label'] === name)) { libraryError.value = '「' + typeName(typeId) + '」已有同名属性「' + name + '」；如需统一维护请先调整属性。'; return }
  editorSaving.value = true; libraryError.value = ''
  let newId = ''
  const r = await formSave.submitForm('ontology', () => {
    const before = new Set(localProperties(graph.value, typeId).map((p: any) => p['@id']))
    if (action === 'copy') copyAsPrivate(s, graph.value, typeId)
    else addReference(graph.value, s, typeId)
    newId = localProperties(graph.value, typeId).find((p: any) => !before.has(p['@id']))?.['@id'] || ''
  })
  editorSaving.value = false
  if (!r.ok) { libraryError.value = r.message; return }
  libraryOpen.value = false
  detailTab.value = 'props'
  locateRow(newId, propsList)
}

// ─── 浏览态：列表/详情骨架（对齐 20260917 微软列表详情原型）───
// 搜索覆盖全部对象（不只当前页）；筛选结果不含当前对象时保持选中不变，只在详情提示；
// 定位会清除妨碍定位的筛选并翻到目标页（交互约定见原型需求说明 §交互约定）。
const listQuery = ref(''), starOnly = ref(false), asc = ref(true), page = ref(1), stackedDetail = ref(false)
const pageSize = 12
const ldRef = ref<HTMLElement | null>(null), ldH = ref(560)
// 收藏是本地用户偏好（按账号命名空间存储，20260918），不写入本体语义模型。
const STARS_KEY = 'wiz-object-stars'
const stars = ref<Set<string>>(readStars())
function readStars(): Set<string> {
  try {
    const raw = prefGet(STARS_KEY)
    const arr = raw ? JSON.parse(raw) : []
    return new Set(Array.isArray(arr) ? arr.filter((x: any) => typeof x === 'string') : [])
  } catch { return new Set<string>() }
}
function toggleStar(id: string) {
  const next = new Set(stars.value)
  if (next.has(id)) next.delete(id); else next.add(id)
  stars.value = next
  prefSet(STARS_KEY, JSON.stringify([...next]))  // 按账号命名空间；未登录/存储不可用静默降级
}
function clearFilters() { listQuery.value = ''; starOnly.value = false; page.value = 1 }
function onSearch(v: string) { listQuery.value = v; page.value = 1 }
function setFilter(onlyStar: boolean) { starOnly.value = onlyStar; page.value = 1 }

const filteredObjects = computed(() => {
  const q = listQuery.value.trim().toLowerCase(), dir = asc.value ? 1 : -1
  return objects.value
    .filter((o: any) => (!starOnly.value || stars.value.has(o['@id'])) && (!q || ((o['rdfs:label'] || '') + ' ' + (o['rdfs:comment'] || '')).toLowerCase().includes(q)))
    .slice().sort((a: any, b: any) => dir * String(a['rdfs:label'] || '').localeCompare(String(b['rdfs:label'] || ''), 'zh-CN', { numeric: true }))
})
const pageCount = computed(() => Math.max(1, Math.ceil(filteredObjects.value.length / pageSize)))
const pageRows = computed(() => { const p = Math.min(page.value, pageCount.value); return filteredObjects.value.slice((p - 1) * pageSize, p * pageSize) })
const showPager = computed(() => filteredObjects.value.length > pageSize)
watch(pageCount, () => { if (page.value > pageCount.value) page.value = 1 })
const listRows = computed(() => pageRows.value.map((o: any) => {
  const id = o['@id']
  const propCount = localProperties(graph.value, id).length
  const linkCount = graph.value.filter((n: any) => n['@type'] === 'owl:ObjectProperty' && (n['rdfs:domain']?.['@id'] === id || n['rdfs:range']?.['@id'] === id)).length
  return { id, label: o['rdfs:label'] || '', meta: `${propCount} 属性 · ${linkCount} 链接`, star: stars.value.has(id) }
}))
// 筛选结果不含当前对象时保持选中不变，只在详情提示（不静默换对象）。
const selectedHidden = computed(() => !!current.value && !filteredObjects.value.some((o: any) => o['@id'] === selected.value))
const starred = computed(() => !!current.value && stars.value.has(current.value['@id']))

// 窄屏（≤1000px：侧栏 224 + 列表 290 + 详情最小可读宽度）退化为单栏，列表与详情互斥显示。
const STACK_MQ = '(max-width: 1000px)'
const isStacked = ref(false)
let stackMq: MediaQueryList | null = null
function syncStacked() { isStacked.value = !!stackMq?.matches }
function openDetailIfStacked() { if (isStacked.value) stackedDetail.value = true }
// 两区独立滚动：按元素实际位置测算高度，顶栏提示条出现/工具栏换行时自动跟随。
function syncHeight() {
  const el = ldRef.value
  if (!el) return
  ldH.value = Math.max(440, Math.round(window.innerHeight - el.getBoundingClientRect().top - 20))
}
onMounted(() => {
  stackMq = window.matchMedia(STACK_MQ); syncStacked(); stackMq.addEventListener('change', syncStacked)
  window.addEventListener('resize', syncHeight)
  document.addEventListener('click', onDocumentClick)
  nextTick(syncHeight)
})
onBeforeUnmount(() => { stackMq?.removeEventListener('change', syncStacked); window.removeEventListener('resize', syncHeight); document.removeEventListener('click', onDocumentClick) })
watch([mode, editor, message, current], () => { closeMoreMenu(false); nextTick(syncHeight) })

function backToList() {
  stackedDetail.value = false
  nextTick(() => {
    const rows = document.querySelectorAll<HTMLElement>('.ld-row')
    for (const el of rows) if (el.dataset.row === selected.value) { el.focus(); return }
  })
}
// 列表方向键巡航（原生按钮上的 roving focus）；Enter/Space 由按钮原生行为触发选择。
function listKeydown(e: KeyboardEvent) {
  const rows = [...(e.currentTarget as HTMLElement).querySelectorAll<HTMLElement>('.ld-row')]
  const i = rows.indexOf(document.activeElement as HTMLElement)
  if (i < 0) return
  const n = e.key === 'ArrowDown' ? Math.min(i + 1, rows.length - 1) : e.key === 'ArrowUp' ? Math.max(i - 1, 0)
    : e.key === 'Home' ? 0 : e.key === 'End' ? rows.length - 1 : -1
  if (n >= 0) { e.preventDefault(); rows[n].focus() }
}

// ─── 深链定位（共享库「查看引用」/校验问题/旧 hash）：进入即打开对应属性表单 ───
watch(() => [props.focusType, props.focusProperty], ([t, p]: any[]) => {
  if (!p) {
    if (t && objects.value.some((n: any) => n['@id'] === t)) { selected.value = t; openDetailIfStacked() }
    editor.value = null
    return
  }
  const typeId = t || selected.value
  let target: any = graph.value.find((n: any) => n['@id'] === p) || graph.value.find((n: any) => n['@type'] === 'owl:DatatypeProperty' && n['rdfs:domain']?.['@id'] === typeId && n['mg:apiName'] === p)
  if (!target) target = graph.value.find((n: any) => n['@type'] === 'owl:DatatypeProperty' && n['@id'] === (String(p).startsWith('mg:') ? p : 'mg:' + p))
  // 深链指向共享定义本身：落到第一个引用它的对象属性（沿用旧 PropertyManager 行为）。
  if (target?.['@type'] === 'mg:SharedProperty') { const first = graph.value.find((n: any) => n['mg:sharedProperty']?.['@id'] === target['@id']); if (first) target = first }
  const owner = target?.['rdfs:domain']?.['@id'] || typeId
  mode.value = 'list'
  if (owner && objects.value.some((n: any) => n['@id'] === owner)) { selected.value = owner; openDetailIfStacked() }
  detailTab.value = 'props'
  // 悬空或非属性引用不打开编辑器（与旧组件行为一致：仅定位对象）。
  editor.value = target?.['@type'] === 'owl:DatatypeProperty' ? { kind: 'property', targetTypeId: owner, propertyId: target['@id'], returnTab: 'props' } : null
  editorError.value = ''
}, { immediate: true })

// 工作概览「创建第一个对象」：自动打开既有新建对象表单（H02，20260919 概览优化）。
// 必须放在 focusType/focusProperty 深链 watch 之后：那个 immediate 回调会无条件清空 editor，
// 本 watch 若先执行会被它抹掉（表单静默打不开）。信号在离开对象建模时由 App 清除，
// 重新进入页面不会重复弹表单。
watch(() => props.focusCreate, v => { if (v) openObjectEditor(true) }, { immediate: true })

// ─── 浏览态数据 ───
// 属性行：共享引用属性按生效定义展示名称、统一数据类型和单位；复用方式只标「共享/私有」（R5）。
const propRows = computed(() => {
  if (!current.value) return []
  return localProperties(graph.value, current.value['@id']).map((p: any) => {
    const m = effectiveProperty(p, graph.value)
    const sharedId = String(p['mg:sharedProperty']?.['@id'] || '')
    const dt = propertyDataType(p, graph.value)
    return {
      id: p['@id'], label: m['rdfs:label'] || '', desc: m['rdfs:comment'] || '',
      type: propertyTypeLabel(p, graph.value),
      typeMain: dt.type === 'timeSeries' ? '时间序列' : dataTypeLabel(dt),
      valueType: dt.type === 'timeSeries' ? dataTypeLabel({ type: dt.valueType }) : '',
      unit: m['mg:valueSuffix'] || '', isDisplayName: m['mg:isDisplayName']?.['@value'] === true, sharedId,
    }
  })
})
// 共享来源详情（R5）：按稳定 ID 解析（重名不串联）；引用失效时只说明「共享定义不存在」，
// 不回填、不转为私有、不提供编辑入口。
const sharedDetailId = ref('')
let sharedTrigger: HTMLElement | null = null
const sharedDetail = computed(() => {
  const id = sharedDetailId.value
  const def: any = graph.value.find((n: any) => n['@id'] === id && n['@type'] === 'mg:SharedProperty')
  if (!def) return { missing: true, id, name: '', comment: '', type: '', unit: '', usage: 0 }
  return {
    missing: false, id,
    name: def['rdfs:label'] || '未命名共享属性',
    comment: def['rdfs:comment'] || '暂无业务定义',
    type: propertyTypeLabel(def, graph.value),
    unit: String(def['mg:valueSuffix'] || ''),
    usage: graph.value.filter((n: any) => n['@type'] === 'owl:DatatypeProperty' && n['mg:sharedProperty']?.['@id'] === id).length,
  }
})
function openShared(id: string, event?: MouseEvent) {
  sharedTrigger = (event?.currentTarget as HTMLElement) || null
  sharedDetailId.value = id
}
function closeShared() { sharedDetailId.value = ''; sharedTrigger?.focus?.(); sharedTrigger = null }
// 链接行（D06）：当前对象作为起点或终点的链接都展示；同一链接只维护一份，从任一端编辑同一节点。
const linkRows = computed(() => {
  if (!current.value) return []
  const cur = current.value['@id']
  return graph.value.filter((n: any) => n['@type'] === 'owl:ObjectProperty' && (n['rdfs:domain']?.['@id'] === cur || n['rdfs:range']?.['@id'] === cur))
    .map((n: any) => ({ id: n['@id'], label: n['rdfs:label'] || '未命名链接', from: typeName(n['rdfs:domain']?.['@id']), to: typeName(n['rdfs:range']?.['@id']), card: CARDINALITY[n['mg:cardinality']] || n['mg:cardinality'] || '', reverse: n['mg:reverseLabel'] || '' }))
})
function pick(id: string) { selected.value = id; message.value = ''; openDetailIfStacked() }

// ─── 详情头「更多操作」（R3）：删除对象收进菜单；键盘可用、Escape 关闭并归还焦点 ───
const moreOpen = ref(false)
const moreWrap = ref<HTMLElement | null>(null)
const moreTrigger = ref<HTMLButtonElement | null>(null)
function openMoreMenu() {
  moreOpen.value = true
  void nextTick(() => moreWrap.value?.querySelector<HTMLElement>('[role="menuitem"]')?.focus())
}
function closeMoreMenu(refocus = false) { if (!moreOpen.value) return; moreOpen.value = false; if (refocus) moreTrigger.value?.focus() }
// 菜单内上下键巡航（原生按钮 + roving focus），Enter/Space 由按钮原生行为触发。
function moreKeydown(e: KeyboardEvent) {
  const items = [...(e.currentTarget as HTMLElement).querySelectorAll<HTMLElement>('[role="menuitem"]')]
  const i = items.indexOf(document.activeElement as HTMLElement)
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); items[(i + (e.key === 'ArrowDown' ? 1 : items.length - 1)) % items.length]?.focus() }
  else if (e.key === 'Home') { e.preventDefault(); items[0]?.focus() }
  else if (e.key === 'End') { e.preventDefault(); items[items.length - 1]?.focus() }
  else if (e.key === 'Escape') { e.preventDefault(); closeMoreMenu(true) }
}
function onDocumentClick(e: MouseEvent) { if (moreOpen.value && !moreWrap.value?.contains(e.target as Node)) closeMoreMenu(false) }
async function moreRemove() {
  const target = current.value
  closeMoreMenu(false)
  if (target) await removeNode(target['@id'], target['rdfs:label'])
}

// ─── 动作页签（20260917 需求 §3.2）：可搜索多选关联，确认才保存，移除不删动作定义 ───
const actionById = computed(() => new Map(actionsOf(props.state).map((a: any) => [a.id, a])))
const actionRows = computed(() => {
  if (!current.value) return []
  return associationsOfObject(props.state, current.value['@id']).map(r => {
    const a: any = actionById.value.get(r.actionId)
    // id：统一行标识（表格定位/高亮按稳定行 id 匹配）；actionId 保留给既有动作语义调用
    return { id: r.actionId, actionId: r.actionId, name: a?.name || '', effect: a?.effect || '', desc: a?.description || '', missing: !a, legacy: a && a.definitionVersion !== 2 }
  })
})
// 添加动作：统一走 PickerDialog（多选 + 确认），已关联动作按禁用项列出，不重复关联。
const pickerOpen = ref(false)
const pickItems = computed<PickerRow[]>(() => actionsOf(props.state).map((a: any) => ({
  id: a.id, label: a.name || '未命名动作', note: a.description || '',
  disabled: actionRows.value.some(r => r.actionId === a.id),
})))
function openPicker() { pickerOpen.value = true }
async function confirmPick(ids: string[]) {
  if (!ids.length || !current.value) return
  const r = await formSave.submitForm('ontology', () => {
    commitAssociations(props.state, [...associationsOf(props.state), ...ids.map(id => ({ objectTypeId: current.value['@id'], actionId: id }))])
  }, { actionLabel: '关联动作到「' + (current.value['rdfs:label'] || '对象') + '」（' + ids.length + ' 项）', target: { kind: 'object', id: current.value['@id'] } })
  if (!r.ok) { message.value = r.message; return }
  pickerOpen.value = false
  locateRow(ids[0], actionsList)
}
async function removeAssociation(actionId: string) {
  if (!current.value) return
  const actionName = actionById.value.get(actionId)?.name || '此动作'
  if (!(await appConfirm({ message: '删除「' + actionName + '」在当前对象上的关联？动作定义保留，其他对象的关联不受影响；项目里已有的绑定会显示关联失效并保留配置。' }))) return
  const typeId = current.value['@id']
  const r = await formSave.submitForm('ontology', () => {
    commitAssociations(props.state, associationsOf(props.state).filter(a => !(a.actionId === actionId && (a.objectTypeId === typeId || a.objectTypeId === 'mg:' + typeId.replace(/^mg:/, '')))))
  }, { actionLabel: '删除对象「' + (current.value['rdfs:label'] || '') + '」的动作关联', target: { kind: 'object', id: typeId } })
  if (!r.ok) message.value = r.message
}

// ─── 规则页签（20260917 业务规则一期）：多选引用已有规则、查看、删除规则关联 ───
const ruleRows = computed(() => {
  if (!current.value) return []
  return rulesOfObject(props.state, current.value['@id']).map(r => ({
    id: r.ruleId, ruleId: r.ruleId, name: r.rule?.name || '', desc: r.rule?.description || '',
    content: r.rule?.content || '', output: r.rule?.output || '', missing: !r.rule,
  }))
})
const rulePickerOpen = ref(false)
const ruleDetailId = ref('')
const ruleDetail = computed(() => ruleDetailId.value ? rulesOf(props.state).find((r: any) => r.id === ruleDetailId.value) || null : null)
const pickerAvailable = computed(() => {
  const mine = new Set(ruleRows.value.map(r => r.ruleId))
  return rulesOf(props.state).filter((r: any) => !mine.has(r.id))
})
async function addRules(ids: string[]) {
  if (!ids.length || !current.value) return
  const typeId = current.value['@id']
  const r = await formSave.submitForm('ontology', () => {
    commitRuleAssociations(props.state, [...ruleAssociationsOf(props.state), ...ids.map(id => ({ objectTypeId: typeId, ruleId: id }))])
  }, { actionLabel: '引用规则到「' + (current.value['rdfs:label'] || '对象') + '」（' + ids.length + ' 项）', target: { kind: 'object', id: typeId } })
  if (!r.ok) { message.value = r.message; return }
  rulePickerOpen.value = false
  locateRow(ids[0], rulesList)
}
async function removeRuleRef(ruleId: string) {
  if (!current.value) return
  const typeId = current.value['@id']
  const r = await formSave.submitForm('ontology', () => {
    commitRuleAssociations(props.state, ruleAssociationsOf(props.state).filter(a => !(a.ruleId === ruleId && (a.objectTypeId === typeId || a.objectTypeId === 'mg:' + typeId.replace(/^mg:/, '')))))
  }, { actionLabel: '删除对象「' + (current.value['rdfs:label'] || '') + '」的规则引用', target: { kind: 'object', id: typeId } })
  if (!r.ok) message.value = r.message
}

// ─── 四页签标准表格（20260918 列表统一 §5/§6）：搜索/排序/分页只是视图，不触发保存 ───
// 切换对象或页签即重置视图状态（不残留上一个上下文的筛选）；定位会清除妨碍定位的筛选。
const reuseFilter = ref('全部')
const propsView = computed(() => reuseFilter.value === '全部' ? propRows.value : propRows.value.filter(p => reuseFilter.value === '共享' ? !!p.sharedId : !p.sharedId))
const byName = (get: (r: any) => string) => (a: any, b: any) => get(a).localeCompare(get(b), 'zh-CN', { numeric: true })
const propsList = useOntTable(() => propsView.value, { match: (r, q) => (r.label + ' ' + r.desc).toLowerCase().includes(q), sort: byName(r => r.label) })
const linksList = useOntTable(() => linkRows.value, { match: (r, q) => (r.label + ' ' + r.reverse + ' ' + r.from + ' ' + r.to + ' ' + r.card).toLowerCase().includes(q), sort: byName(r => r.label) })
const actionsList = useOntTable(() => actionRows.value, { match: (r, q) => (r.name + ' ' + r.desc + ' ' + r.effect).toLowerCase().includes(q), sort: byName(r => r.name) })
const rulesList = useOntTable(() => ruleRows.value, { match: (r, q) => (r.name + ' ' + r.desc).toLowerCase().includes(q), sort: byName(r => r.name) })
watch([selected, detailTab], () => {
  reuseFilter.value = '全部'
  for (const l of [propsList, linksList, actionsList, rulesList]) l.reset()
})

// 名称/查看 → 只读详情抽屉（§7）：只展示完整定义，不保存；编辑仍走原表单。
const propDetailId = ref(''), linkDetailId = ref(''), actionDetailId = ref('')
const propDetail = computed(() => propDetailId.value ? propRows.value.find(p => p.id === propDetailId.value) || null : null)
const linkDetail = computed(() => linkDetailId.value ? linkRows.value.find(l => l.id === linkDetailId.value) || null : null)
const actionDetail = computed(() => actionDetailId.value ? actionRows.value.find(r => r.actionId === actionDetailId.value) || null : null)

function editPropFromDrawer() { if (propDetail.value && current.value) { propDetailId.value = ''; openPropertyEditor(current.value['@id'], propDetail.value.id) } }
function editLinkFromDrawer() { if (linkDetail.value) { linkDetailId.value = ''; openLinkEditor(linkDetail.value.id) } }
function goActionLibrary() { if (actionDetail.value && current.value) emit('navigate', 'actions', { definition: actionDetail.value.actionId, type: current.value['@id'], tab: 'actions' }) }
function goRuleLibrary() { if (ruleDetail.value && current.value) emit('navigate', 'rules', { definition: ruleDetail.value.id, type: current.value['@id'], tab: 'rules' }) }
// 行内更多（§6）：危险/低频操作收纳进菜单；只承载已有语义，不改引用检查与撤销。
// 行内删除文案（20260918 用户变更：四页签删除/移除不再收进更多菜单，直接显示在操作列）：
// 共享引用是「移除引用」（共享定义保留），私有是「删除属性」。两者都走 removeNode 的引用检查与撤销。
const propRemoveLabel = (p: { sharedId: string }) => p.sharedId ? '移除引用' : '删除属性'
async function onPropMenu(p: { id: string; label: string; sharedId: string }) {
  const msg = p.sharedId
    ? '移除当前对象对共享属性「' + (p.label || '未命名属性') + '」的引用？共享定义与其他对象的引用不受影响；可通过撤销恢复。'
    : '删除属性「' + (p.label || '未命名属性') + '」？可通过撤销恢复。'
  await removeNode(p.id, p.label, msg)
}
async function removeLinkRow(l: { id: string; label: string; from: string; to: string }) {
  await removeNode(l.id, l.label, '删除链接「' + l.from + ' → ' + (l.label || '未命名链接') + ' → ' + l.to + '」？该定义会从两端对象同时移除；可通过撤销恢复。')
}

// 行定位（保存/回跳后）：目标在过滤结果中时翻到所在页并滚动+闪烁；不在结果中则不动用户筛选。
async function locateRow<T extends { id: string }>(id: string, list: OntTable<T>) {
  if (!id) return
  const idx = list.filtered.value.findIndex(r => r.id === id)
  if (idx < 0) return
  list.page.value = Math.floor(idx / 20) + 1
  highlightId.value = id
  await nextTick()
  document.querySelector(`[data-row="${id}"]`)?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  if (highlightTimer) clearTimeout(highlightTimer)
  highlightTimer = setTimeout(() => { if (highlightId.value === id) highlightId.value = '' }, 2600)
}
// 从资产库返回原对象（App 传 focusDefinition）：定位到对应页签的行并闪烁，不自动打开抽屉。
// 联表监听 detailTab：返回导航可能先设 focusDefinition 再切页签（或反之），单看一个信号会漏触发。
watch([() => props.focusDefinition, detailTab], ([id, tab]) => {
  if (!id) return
  if (tab === 'actions') void locateRow(id, actionsList)
  else if (tab === 'rules') void locateRow(id, rulesList)
  else if (tab === 'links') void locateRow(id, linksList)
}, { immediate: true })

// 图谱「编辑」跳转（2026-09-20，用户要求）：详情「编辑」按钮携 edit:true 进入本页时，
// 直接打开对应编辑表单（对象 / 链接）；属性表单已由 focusProperty 分支打开，此处不重复。
// 必须排在前面的定位 watch 之后（Vue 按创建顺序触发，前面的分支会清空 editor）。
watch(() => props.editFocus, (v) => {
  if (!v) return
  if (props.focusDefinition && detailTab.value === 'links'
      && graph.value.some((n: any) => n['@id'] === props.focusDefinition && n['@type'] === 'owl:ObjectProperty')) {
    openLinkEditor(props.focusDefinition)
    return
  }
  if (props.focusProperty) return // 属性：既有 focusProperty 分支已打开表单
  if (props.focusType && objects.value.some((o: any) => o['@id'] === props.focusType)) openObjectEditor(false)
}, { immediate: true })

// 引用检查与 EntityManager 同一套：有引用先提示，不静默断链；confirm 后删除，可撤销。
// 动作关联随对象删除一并清理（需求 §5）；规则引用走引用保护——须先移除引用（业务规则一期 §5）。
// confirmText：行语义化确认文案（删除属性/删除链接/删除动作各说各的影响），缺省沿用对象删除文案。
async function removeNode(id: string, label: string, confirmText?: string) {
  // 20260920 需求 13 统一语义：
  //  - 对象（owl:Class）：阻断式删除，完整列出属性/链接/规则/动作关联的名称与原因（objectDeleteCheck）；
  //  - 属性/链接：契约、接口、项目映射等直接依赖时同步阻断（graphReferences）；
  //    共享引用属性是「移除引用」（共享定义与其他对象不动），私有属性是「删除属性」。
  const node: any = graph.value.find((n: any) => n['@id'] === id)
  if (node?.['@type'] === 'owl:Class') {
    const check = objectDeleteCheck(props.state, id)
    if (check.blocked) { message.value = check.message; return }
  } else {
    const refs = graphReferences(props.state, id)
    if (refs.length) { message.value = '暂不能删除：请先处理引用（' + refs.join('、') + '）。'; return }
  }
  if (!(await appConfirm({ message: confirmText || '删除「' + (label || id) + '」？可通过撤销恢复。' }))) return
  emit('before-change', { actionLabel: '删除「' + (label || id) + '」', target: { kind: 'object', id } })
  props.state.ontology['@graph'] = graph.value.filter((n: any) => n['@id'] !== id)
  emit('changed')
}
</script>

<template>
<div class="object-workspace ow-root">
  <!-- 本体图谱（20260919）：只读全量视图，五类内容上图；独立于编辑保存路径 -->
  <div v-if="mode === 'graph' && !editor" class="ow-graph-wrap">
    <div class="ow-mode-tabs ow-sub-tabs" role="tablist" aria-label="对象建模模式">
      <button role="tab" :aria-selected="false" @click="mode = 'list'">对象列表</button>
      <button role="tab" class="active" :aria-selected="true">本体图谱</button>
    </div>
    <button v-if="returnTo" type="button" class="ow-return-src" @click="backToSource">← 返回{{ returnTo.label }}</button>
    <LegacyGraphHost
      :state="state"
      :ontology-id="state?.workspaceId || ''"
      :save-state="props.saveState"
      :latest-release="props.latestRelease"
      :focus-target="props.graphFocus || ''"
      @navigate="onGraphNavigate"
      @before-change="p => emit('before-change', p)"
      @changed="() => emit('changed')"
      @switch-ontology="onSwitchOntology"
      @create-ontology="onCreateOntology"
    />
  </div>
  <!-- 编辑态：主内容整体替换为一个完整表单（原型 ui.editor ? editorView() : pageView()） -->
  <template v-if="editor">
    <PropertyManager v-if="editor.kind === 'property'" :key="editor.propertyId || 'new'" :state="state" kind="property" :target-type-id="editor.targetTypeId" :property-id="editor.propertyId" :canvas-return="canvasReturn" @back-to-graph="backToGraph" @close="closeEditor" @saved="onPropertySaved"/>
    <section v-else-if="editor.kind === 'object'" class="card detail-card ow-editor">
      <EditorHead :canvas-return="canvasReturn" back-label="← 返回对象" @back-to-graph="backToGraph" @close="closeEditor"/>
      <div class="detail-heading"><div><span class="eyebrow">对象类型</span><h2>{{ editor.isNew ? '新建对象类型' : '维护对象定义' }}</h2></div></div>
      <p v-if="editorError" class="inline-error" role="alert">{{ editorError }}</p>
      <div class="form-grid">
        <Field label="对象名称" class="full" :model-value="objectDraft?.label || ''" required example="储能簇" @update:model-value="objectDraft && (objectDraft.label = $event)"/>
        <Field label="业务定义" type="textarea" class="full" :model-value="objectDraft?.comment || ''" required example="说明它是什么，用什么业务边界区分。" help="只需名称和业务定义即可保存；属性与链接在对象内补充。" @update:model-value="objectDraft && (objectDraft.comment = $event)"/>
      </div>
      <div class="detail-footer">
        <div class="tools"><button type="button" class="primary" :disabled="editorSaving" @click="saveObject">{{ editorSaving ? '保存中…' : '保存' }}</button><button type="button" @click="closeEditor">取消</button></div>
        <span>只影响当前本体草稿；已发布版本不变。</span>
      </div>
    </section>
    <section v-else-if="editor.kind === 'link'" class="card detail-card ow-editor">
      <EditorHead :canvas-return="canvasReturn" back-label="← 返回对象" @back-to-graph="backToGraph" @close="closeEditor"/>
      <div class="detail-heading"><div><span class="eyebrow">业务链接</span><h2>{{ editor.isNew ? '定义业务链接' : '维护 · ' + (linkDraft?.label || '未命名链接') }}</h2></div></div>
      <p v-if="editorError" class="inline-error" role="alert">{{ editorError }}</p>
      <div class="form-grid">
        <label>起点对象 *<AppSelect :model-value="linkDraft?.from || ''" aria-label="起点对象" :options="linkTargetOptions" @update:model-value="linkDraft && (linkDraft.from = $event)"/></label>
        <label>终点对象 *<AppSelect :model-value="linkDraft?.to || ''" aria-label="终点对象" :options="linkTargetOptions" @update:model-value="linkDraft && (linkDraft.to = $event)"/></label>
        <Field label="正向名称" :model-value="linkDraft?.label || ''" required example="所属设备" help="从起点读到终点的业务含义。" @update:model-value="linkDraft && (linkDraft.label = $event)"/>
        <Field label="数量关系" type="select" :model-value="linkDraft?.cardinality || 'many-to-one'" :options="cardinalityOptions" required @update:model-value="linkDraft && (linkDraft.cardinality = $event)"/>
      </div>
      <details class="technical-section">
        <summary>反向阅读名称（选填）</summary>
        <Field label="反向名称" :model-value="linkDraft?.reverseLabel || ''" example="包含储能簇" help="同一条链接的反向表达，不重复建立另一条链接。" @update:model-value="linkDraft && (linkDraft.reverseLabel = $event)"/>
      </details>
      <details class="technical-section">
        <summary>更多信息（选填）</summary>
        <Field label="业务定义" type="textarea" :model-value="linkDraft?.comment || ''" example="用业务语言说明这条关系的含义" @update:model-value="linkDraft && (linkDraft.comment = $event)"/>
      </details>
      <p class="relation-sentence">{{ typeName(linkDraft?.from) }} → {{ linkDraft?.label || '链接名称' }} → {{ typeName(linkDraft?.to) }}</p>
      <div class="detail-footer">
        <div class="tools"><button type="button" class="primary" :disabled="editorSaving" @click="saveLink">{{ editorSaving ? '保存中…' : '保存' }}</button><button type="button" @click="closeEditor">取消</button></div>
        <span>只影响当前本体草稿；已发布版本不变。</span>
      </div>
    </section>
  </template>
  <!-- 浏览态：列表/详情骨架（20260917 原型）：左紧凑列表 + 右详情，两区独立滚动 -->
  <template v-else-if="mode === 'list'">
    <div class="ow-toolbar">
      <div class="ow-mode-tabs" role="tablist" aria-label="对象建模模式">
        <button role="tab" class="active" :aria-selected="true">对象列表</button>
        <button role="tab" :aria-selected="false" @click="mode = 'graph'">本体图谱</button>
      </div>
      <button v-if="canvasReturn" type="button" @click="backToGraph">← 返回图谱</button>
      <button v-else-if="returnTo" type="button" @click="backToSource">← 返回{{ returnTo.label }}</button>
      <button class="primary" @click="openObjectEditor(true)">＋ 新建对象</button>
    </div>
    <div ref="ldRef" class="ld" :class="{ 'ld-stacked': isStacked, 'ld-detail-open': stackedDetail }" :style="{ '--ld-h': ldH + 'px' }">
      <!-- 列表区：搜索覆盖全部对象；筛选不丢失选中；分页保留详情 -->
      <section class="ld-list" aria-label="对象类型列表">
        <div class="ld-list-head"><h2>对象类型 <span class="muted">{{ filteredObjects.length }} / {{ objects.length }}</span></h2></div>
        <div class="ld-search">
          <SearchField :value="listQuery" placeholder="搜索全部对象类型…" ariaLabel="搜索全部对象类型（名称与业务定义）" @update:value="onSearch" @clear="clearFilters"/>
        </div>
        <div class="ld-filters" role="group" aria-label="筛选与排序">
          <button type="button" :class="{ active: !starOnly }" :aria-pressed="!starOnly" @click="setFilter(false)">全部</button>
          <button type="button" :class="{ active: starOnly }" :aria-pressed="starOnly" @click="setFilter(true)">已收藏</button>
          <button type="button" class="ld-sort" :title="asc ? '当前按名称升序，点击改为降序' : '当前按名称降序，点击改为升序'" @click="asc = !asc">名称 {{ asc ? '↑' : '↓' }}</button>
        </div>
        <div class="ld-items" @keydown="listKeydown">
          <button v-for="it in listRows" :key="it.id" type="button" class="ld-row" :class="{ active: it.id === selected, 'ow-flash': highlightId === it.id }" :data-row="it.id" :aria-current="it.id === selected ? 'true' : undefined" @click="pick(it.id)">
            <span class="ld-row-main"><strong>{{ it.label || '未命名对象' }}</strong><small>{{ it.meta }}<template v-if="it.star"> · ★</template></small></span>
            <span class="ld-row-arrow" aria-hidden="true">›</span>
          </button>
          <div v-if="!listRows.length" class="empty-state">
            <p v-if="listQuery || starOnly">没有匹配的对象。试试更短的关键词，或清空筛选。</p>
            <p v-else>还没有对象类型。用上方「＋ 新建对象」创建第一个。</p>
            <button v-if="listQuery || starOnly" type="button" @click="clearFilters">清空筛选</button>
            <p v-else class="muted">用上方「＋ 新建对象」创建第一个对象类型。</p>
          </div>
        </div>
        <ListPager v-if="showPager" class="ld-pager" :total="filteredObjects.length" :page="page" :page-count="pageCount" :page-size="pageSize" @update:page="page = $event"/>
      </section>
      <!-- 详情区：页签在滚动容器内吸顶；筛选不含当前对象时如实提示，不静默换对象 -->
      <section class="ld-detail" aria-label="对象详情">
        <button type="button" class="ld-back" @click="backToList">← 返回对象列表</button>
        <div v-if="!current" class="empty-state">
          <span class="empty-state-ico" aria-hidden="true"><svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path :d="navIcons.objects"/></svg></span>
          <p><strong>从第一个对象开始。</strong></p>
          <p>只需名称和业务定义，随后在对象内补充属性与链接。</p>
          <button type="button" class="primary" @click="openObjectEditor(true)">＋ 新建对象</button>
        </div>
        <template v-else>
          <div class="ld-detail-head">
            <div class="ow-head-row">
              <h2 class="ow-h2-name">{{ current['rdfs:label'] || '未命名对象' }}</h2>
              <div class="ow-head-side">
                <button type="button" :aria-pressed="starred" :title="starred ? '取消收藏（仅保存在本机浏览器，不写入本体）' : '收藏（仅保存在本机浏览器，不写入本体）'" @click="toggleStar(current['@id'])">{{ starred ? '★ 已收藏' : '☆ 收藏' }}</button>
                <button type="button" @click="openObjectEditor(false)">编辑定义</button>
                <!-- R3：删除等破坏性操作收进「更多操作」，避免与常用动作并列误点 -->
                <div ref="moreWrap" class="ow-more" @keydown="moreKeydown">
                  <button ref="moreTrigger" type="button" class="ow-more-trigger" aria-haspopup="menu" :aria-expanded="moreOpen" aria-label="更多操作" @click="moreOpen ? closeMoreMenu(true) : openMoreMenu()">更多操作 ⌄</button>
                  <div v-if="moreOpen" class="ow-more-menu" role="menu" aria-label="对象更多操作" @keydown.tab="closeMoreMenu(false)">
                    <button type="button" role="menuitem" class="danger" @click="moreRemove">删除对象</button>
                  </div>
                </div>
              </div>
            </div>
            <p class="ow-h2-def">{{ current['rdfs:comment'] || '暂无业务定义。' }}</p>
            <!-- 数量只在页签上出现一次（R5），此处仅保留筛选态提示 -->
            <p v-if="selectedHidden" class="ld-meta-warn">此对象不在当前筛选结果中，已保持选中<button type="button" class="row-link" @click="clearFilters">清除筛选</button></p>
          </div>
          <p v-if="message" class="inline-error ow-alert" role="alert">{{ friendlyMessage(message) }}</p>
          <!-- 四页签：属性 / 链接 / 动作 / 规则 -->
          <div class="ld-tabs" role="tablist" aria-label="对象详情内容">
            <button role="tab" :class="{ active: detailTab === 'props' }" :aria-selected="detailTab === 'props'" @click="detailTab = 'props'">属性 · {{ propRows.length }}</button>
            <button role="tab" :class="{ active: detailTab === 'links' }" :aria-selected="detailTab === 'links'" @click="detailTab = 'links'">链接 · {{ linkRows.length }}</button>
            <button role="tab" :class="{ active: detailTab === 'actions' }" :aria-selected="detailTab === 'actions'" @click="detailTab = 'actions'">动作 · {{ actionRows.length }}</button>
            <button role="tab" :class="{ active: detailTab === 'rules' }" :aria-selected="detailTab === 'rules'" @click="detailTab = 'rules'">规则 · {{ ruleRows.length }}</button>
          </div>
          <div class="ld-body" role="tabpanel" aria-label="对象详情内容">
          <template v-if="detailTab === 'props'">
            <OntologyList
              :search="propsList.q.value" @update:search="propsList.q.value = $event"
              :total="propsList.filtered.value.length" :page="propsList.page.value" :page-count="propsList.pageCount.value"
              :sort-desc="propsList.dir.value < 0"
              ariaLabel="对象属性列表" search-placeholder="搜索属性名称或定义"
              :columns="[{ label: '名称', width: '42%', sort: true }, { label: '数据类型', width: '20%' }, { label: '复用方式', width: '20%' }, { label: '操作', width: '18%' }]"
              :empty-title="propsList.q.value || reuseFilter !== '全部' ? '没有匹配的属性' : '还没有属性'"
              :empty-hint="propsList.q.value || reuseFilter !== '全部' ? '调整关键词或筛选条件再试试。' : '用右上角「从属性库添加」复用共享定义，或「＋ 新增属性」新建。'"
              @sort="propsList.toggleSort()" @page="propsList.page.value += $event" :has-filter="reuseFilter !== '全部'" @clear="propsList.q.value = ''; reuseFilter = '全部'">
              <template #filter>
                <div class="ont-filters" role="group" aria-label="按复用方式筛选">
                  <button v-for="f in ['全部', '共享', '私有']" :key="f" type="button" :class="{ active: reuseFilter === f }" :aria-pressed="reuseFilter === f" @click="reuseFilter = f; propsList.page.value = 1">{{ f }}</button>
                </div>
              </template>
              <template #actions>
                <button type="button" @click="openLibraryEditor">从属性库添加</button>
                <button type="button" class="primary" @click="openPropertyEditor(current['@id'], '')">＋ 新增属性</button>
              </template>
              <tr v-for="p in propsList.paged.value" :key="p.id" :data-row="p.id" :class="{ 'ow-flash-row': highlightId === p.id }">
                <td>
                  <button type="button" class="ont-name" @click="propDetailId = p.id">{{ p.label || '未命名属性' }}</button>
                  <span v-if="p.desc" class="ont-sub" :title="p.desc">{{ p.desc }}</span>
                  <span v-else-if="p.isDisplayName || p.unit" class="ont-sub">{{ [p.isDisplayName ? '显示名称' : '', p.unit ? '单位 ' + p.unit : ''].filter(Boolean).join(' · ') }}</span>
                </td>
                <td><span class="ont-type">{{ p.typeMain }}</span><span v-if="p.valueType" class="ont-sub">观测值：{{ p.valueType }}</span></td>
                <td>
                  <button v-if="p.sharedId" type="button" class="ont-badge" :title="'查看共享属性「' + (p.label || '') + '」的来源定义'" @click="openShared(p.sharedId, $event)">共享引用 ↗</button>
                  <span v-else class="ont-badge">私有属性</span>
                </td>
                <td class="ont-ops">
                  <button type="button" class="row-link" @click="openPropertyEditor(current['@id'], p.id)">编辑</button>
                  <button type="button" class="row-link danger" @click="onPropMenu(p)">{{ propRemoveLabel(p) }}</button>
                </td>
              </tr>
            </OntologyList>
            <p class="ont-context">共享引用的名称、类型、单位随共享定义统一维护；数据来源在项目映射中配置。</p>
          </template>
          <template v-else-if="detailTab === 'links'">
            <OntologyList
              :search="linksList.q.value" @update:search="linksList.q.value = $event"
              :total="linksList.filtered.value.length" :page="linksList.page.value" :page-count="linksList.pageCount.value"
              :sort-desc="linksList.dir.value < 0"
              ariaLabel="对象链接列表" search-placeholder="搜索链接名称、两端对象或反向名称"
              :columns="[{ label: '链接名称', width: '29%', sort: true }, { label: '起点对象', width: '20%' }, { label: '终点对象', width: '20%' }, { label: '数量关系', width: '15%' }, { label: '操作', width: '16%' }]"
              :empty-title="linksList.q.value ? '没有匹配的链接' : '还没有链接'"
              :empty-hint="linksList.q.value ? '调整关键词再试试。' : '使用右上角「＋ 新增链接」添加。'"
              @sort="linksList.toggleSort()" @page="linksList.page.value += $event" @clear="linksList.q.value = ''">
              <template #actions>
                <button type="button" class="primary" @click="openLinkEditor()">＋ 新增链接</button>
              </template>
              <tr v-for="l in linksList.paged.value" :key="l.id" :data-row="l.id" :class="{ 'ow-flash-row': highlightId === l.id }">
                <td>
                  <button type="button" class="ont-name" @click="linkDetailId = l.id">{{ l.label || '未命名链接' }}</button>
                  <span v-if="l.reverse" class="ont-sub">反向：{{ l.reverse }}</span>
                </td>
                <td>{{ l.from }}</td>
                <td>{{ l.to }}</td>
                <td>{{ l.card }}</td>
                <td class="ont-ops">
                  <button type="button" class="row-link" @click="openLinkEditor(l.id)">编辑</button>
                  <button type="button" class="row-link danger" @click="removeLinkRow(l)">删除链接</button>
                </td>
              </tr>
            </OntologyList>
            <p class="ont-context">一行是一份链接定义；正反向名称在同一行展示，不重复建两条链接。</p>
          </template>
          <template v-else-if="detailTab === 'actions'">
            <OntologyList
              :search="actionsList.q.value" @update:search="actionsList.q.value = $event"
              :total="actionsList.filtered.value.length" :page="actionsList.page.value" :page-count="actionsList.pageCount.value"
              :sort-desc="actionsList.dir.value < 0"
              ariaLabel="对象动作列表" search-placeholder="搜索动作名称、定义或效果"
              :columns="[{ label: '名称', width: '43%', sort: true }, { label: '业务效果', width: '39%' }, { label: '操作', width: '18%' }]"
              :empty-title="actionsList.q.value ? '没有匹配的动作' : '还没有动作'"
              :empty-hint="actionsList.q.value ? '调整关键词再试试。' : '尚未添加动作，可从动作库选择此对象支持的动作。'"
              @sort="actionsList.toggleSort()" @page="actionsList.page.value += $event" @clear="actionsList.q.value = ''">
              <template #actions>
                <button type="button" class="primary" @click="openPicker">＋ 添加动作</button>
              </template>
              <tr v-for="row in actionsList.paged.value" :key="row.actionId" :data-row="row.actionId" :class="{ 'ow-flash-row': highlightId === row.actionId }">
                <td>
                  <button type="button" class="ont-name" :disabled="row.missing" @click="actionDetailId = row.actionId">{{ row.name || '未命名动作' }}</button>
                  <span v-if="row.missing" class="ont-sub">动作定义不存在（悬空引用）</span>
                  <span v-else-if="row.legacy" class="ont-sub">历史格式</span>
                  <span v-else-if="row.desc" class="ont-sub" :title="row.desc">{{ row.desc }}</span>
                </td>
                <td><span class="ont-clip" :title="row.effect">{{ row.effect || '—' }}</span></td>
                <td class="ont-ops">
                  <button v-if="!row.missing" type="button" class="row-link" @click="actionDetailId = row.actionId">查看</button>
                  <button type="button" class="row-link danger" @click="removeAssociation(row.actionId)">删除动作</button>
                </td>
              </tr>
            </OntologyList>
            <p class="ont-context">删除动作仅移除当前对象的关联；动作定义与其他对象的关联仍保留。</p>
          </template>
          <template v-else-if="detailTab === 'rules'">
            <OntologyList
              :search="rulesList.q.value" @update:search="rulesList.q.value = $event"
              :total="rulesList.filtered.value.length" :page="rulesList.page.value" :page-count="rulesList.pageCount.value"
              :sort-desc="rulesList.dir.value < 0"
              ariaLabel="对象规则列表" search-placeholder="搜索规则名称或业务定义"
              :columns="[{ label: '名称', width: '45%', sort: true }, { label: '业务定义', width: '37%' }, { label: '操作', width: '18%' }]"
              :empty-title="rulesList.q.value ? '没有匹配的规则' : '还没有规则'"
              :empty-hint="rulesList.q.value ? '调整关键词再试试。' : '点击右上角「＋ 添加规则」引用规则库中的通用业务规则。'"
              @sort="rulesList.toggleSort()" @page="rulesList.page.value += $event" @clear="rulesList.q.value = ''">
              <template #actions>
                <button type="button" class="primary" @click="rulePickerOpen = true">＋ 添加规则</button>
              </template>
              <tr v-for="row in rulesList.paged.value" :key="row.ruleId" :data-row="row.ruleId" :class="{ 'ow-flash-row': highlightId === row.ruleId }">
                <td>
                  <button type="button" class="ont-name" :disabled="row.missing" @click="ruleDetailId = row.ruleId">{{ row.name || '未命名规则' }}</button>
                  <span v-if="row.missing" class="ont-sub">规则不存在（悬空引用）</span>
                </td>
                <td><span class="ont-clip" :title="row.desc">{{ row.desc || '—' }}</span></td>
                <td class="ont-ops">
                  <button v-if="!row.missing" type="button" class="row-link" @click="ruleDetailId = row.ruleId">查看</button>
                  <button type="button" class="row-link danger" @click="removeRuleRef(row.ruleId)">删除规则</button>
                </td>
              </tr>
            </OntologyList>
            <p class="ont-context">删除规则仅移除当前对象的引用；规则正文在业务规则库中维护，其他对象的引用不受影响。</p>
          </template>

          </div>
        </template>
      </section>
    </div>
  </template>
  <!-- 规则引用弹窗：多选添加（仅可选未引用规则）+ 只读查看 -->
  <PickerDialog v-if="pickerOpen" :title="'为「' + (current?.['rdfs:label'] || '此对象') + '」添加动作'"
    hint="从动作库选择此类对象支持的操作；动作定义集中维护，不复制为对象私有。"
    :items="pickItems" search-placeholder="搜索动作名称" confirm-label="确认关联" assign-label="动作"
    empty-text="没有可添加的动作。" empty-hint="先到「动作定义」创建动作，再回到这里关联。"
    @close="pickerOpen = false" @confirm="confirmPick"/>
  <PickerDialog v-if="libraryOpen" title="从属性库添加"
    hint="复制生成私有定义（此后独立修改）；引用保留共享关联（名称、类型、单位跟随共享定义统一维护）。"
    :items="libraryItems" mode="action" :error="libraryError" search-placeholder="搜索共享属性名称" assign-label="共享属性"
    empty-text="共享属性库为空。" empty-hint="先在对象中建立私有属性，再到「共享属性库」转为共享定义。"
    @close="libraryOpen = false" @action="pickShared"/>
  <RulePicker v-if="rulePickerOpen" :rules="pickerAvailable" :object-name="current?.['rdfs:label'] || ''" @close="rulePickerOpen = false" @confirm="addRules"/>
  <!-- 共享属性来源（R5）：只读详情抽屉，按稳定 ID 解析；引用失效时只说明，不回填也不转为私有 -->
  <OntDrawer v-if="sharedDetailId" :title="sharedDetail.missing ? '共享属性来源（引用失效）' : (sharedDetail.name || '共享属性来源')" subtitle="共享属性来源" @close="closeShared">
    <template v-if="sharedDetail.missing">
      <p class="inline-warning">共享定义不存在（引用失效）。该引用不会被回填或转为对象私有属性；可移除此引用后重新建立。</p>
      <p class="muted">引用标识：{{ sharedDetail.id }}</p>
    </template>
    <template v-else>
      <div class="ont-field"><span class="ont-field-label">业务定义</span><p>{{ sharedDetail.comment }}</p></div>
      <div class="ont-field"><span class="ont-field-label">数据类型</span><p>{{ sharedDetail.type }}</p></div>
      <div v-if="sharedDetail.unit" class="ont-field"><span class="ont-field-label">单位</span><p>{{ sharedDetail.unit }}</p></div>
      <div class="ont-field"><span class="ont-field-label">引用情况</span><p>{{ sharedDetail.usage }} 个对象属性引用</p></div>
      <p class="ont-hint">名称、数据类型与单位随共享定义统一维护；本对象只保留引用关系。需要独立修改请改用「复制为私有」。</p>
    </template>
    <template #footer><button type="button" class="primary" @click="closeShared">关闭</button></template>
  </OntDrawer>
  <!-- 属性 / 链接 / 动作 / 规则：行名称与「查看」共用的只读详情抽屉（§7） -->
  <OntDrawer v-if="propDetail" :title="propDetail.label || '未命名属性'" @close="propDetailId = ''">
    <div class="ont-field"><span class="ont-field-label">业务定义</span><p>{{ propDetail.desc || '暂无业务定义。' }}</p></div>
    <div class="ont-field"><span class="ont-field-label">数据类型</span><p>{{ propDetail.type }}</p></div>
    <div v-if="propDetail.unit" class="ont-field"><span class="ont-field-label">单位</span><p>{{ propDetail.unit }}</p></div>
    <div class="ont-field"><span class="ont-field-label">复用方式</span><p>{{ propDetail.sharedId ? '共享引用（名称与类型由共享库统一维护）' : '对象私有属性' }}</p></div>
    <template #footer><button type="button" class="primary" @click="editPropFromDrawer">编辑属性</button></template>
  </OntDrawer>
  <OntDrawer v-if="linkDetail" :title="linkDetail.label || '未命名链接'" @close="linkDetailId = ''">
    <div class="ont-field"><span class="ont-field-label">正向关系</span><p>{{ linkDetail.from }} → {{ linkDetail.label }} → {{ linkDetail.to }}</p></div>
    <div class="ont-field"><span class="ont-field-label">反向名称</span><p>{{ linkDetail.reverse || '未定义反向阅读名称。' }}</p></div>
    <div class="ont-field"><span class="ont-field-label">数量关系</span><p>{{ linkDetail.card }}</p></div>
    <template #footer><button type="button" class="primary" @click="editLinkFromDrawer">编辑链接</button></template>
  </OntDrawer>
  <OntDrawer v-if="actionDetail" :title="actionDetail.name || '未命名动作'" @close="actionDetailId = ''">
    <p v-if="actionDetail.missing" class="inline-warning">动作定义不存在（悬空引用）；只能删除本对象的关联，或到动作库重建。</p>
    <div class="ont-field"><span class="ont-field-label">业务定义</span><p>{{ actionDetail.desc || '暂无业务定义。' }}</p></div>
    <div class="ont-field"><span class="ont-field-label">业务效果</span><p>{{ actionDetail.effect || '暂未填写。' }}</p></div>
    <p v-if="actionDetail.legacy" class="ont-hint">历史格式动作：字段只读保留，可在动作定义库显式转换后编辑。</p>
    <template #footer>
      <button v-if="!actionDetail.missing" type="button" class="primary" @click="goActionLibrary">到动作定义维护</button>
    </template>
  </OntDrawer>
  <OntDrawer v-if="ruleDetail" :title="ruleDetail.name || '未命名规则'" @close="ruleDetailId = ''">
    <div class="ont-field"><span class="ont-field-label">业务定义</span><p>{{ ruleDetail.description || '暂无业务定义。' }}</p></div>
    <div class="ont-field"><span class="ont-field-label">规则内容</span><p>{{ ruleDetail.content || '暂无规则内容。' }}</p></div>
    <div class="ont-field"><span class="ont-field-label">输出结果</span><p>{{ ruleDetail.output || '暂无输出说明。' }}</p></div>
    <template #footer>
      <button type="button" class="primary" @click="goRuleLibrary">到业务规则维护</button>
    </template>
  </OntDrawer>
</div>
</template>

<style scoped>
/* 浏览态的列表/详情骨架在全局 .ld-*（style.css）：两区独立滚动，窄屏单栏。 */
.ow-root{display:block}
/* 对象导航底部分页：沿用共用 ListPager，仅此处收紧内边距（贴近原 ld-pager 的 9px 12px） */
.ld-pager{padding:9px 12px}
/* 对象详情页签内的统一表格：ld-detail 整体滚动，表头不再单独吸顶（避免钻到页签下面）；
   复用方式筛选沿用紧凑分段按钮（§4）。 */
.ld-body :deep(.ont-table th){position:static}
.ont-filters{display:flex;gap:6px;flex-wrap:wrap}
.ont-filters button{font-size:12px;padding:4px 9px;border-radius:var(--r-pill)}
.ont-filters button.active{background:var(--blue-soft);border-color:var(--blue-line);color:var(--blue-ink);font-weight:600}
.ow-sub-tabs{max-width:360px}
.ow-toolbar{display:flex;align-items:center;gap:10px;margin-bottom:12px;flex-wrap:wrap}
.ow-toolbar .ow-mode-tabs{display:flex;gap:6px;margin-bottom:0}
.ow-toolbar .ow-mode-tabs button{flex:none}
.ow-toolbar .primary{margin-left:auto}
.ow-editor :deep(.form-grid .editor-field.full){grid-column:1/-1}
.ow-row-tools{white-space:nowrap;text-align:right}
.ow-row-tools .row-link + .row-link{margin-left:12px}
.ow-flash{outline:2px solid var(--focus);outline-offset:-2px}
.ow-flash-row td{background:var(--blue-soft)}
.ow-link-rows{border:1px solid var(--line);border-radius:var(--r-md);padding:4px 12px}
.ow-link-row{display:flex;justify-content:space-between;align-items:center;gap:14px;padding:12px 2px;border-bottom:1px solid var(--line);flex-wrap:wrap}
.ow-link-row:last-child{border-bottom:0}
.ow-link-row .row-main{flex:1;min-width:0}
.ow-link-row .row-main strong{display:block;font-size:14px;font-weight:600}
.ow-link-row .row-main small{display:block;margin-top:3px;font-size:12px;color:var(--muted);overflow-wrap:anywhere}





.ow-pick-actions{display:flex;gap:8px;flex:none;flex-wrap:wrap}




.ow-effect-cell{max-width:420px;white-space:pre-wrap;font-size:13px;color:var(--ink-2)}
/* 详情头：名称+操作同一行，业务定义各自一行。 */
.ow-head-row{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}
.ow-head-row .ow-h2-name{margin:0}
.ow-h2-def{margin:6px 0 0}
.ow-head-side .danger{margin:0}
/* 更多操作（R3）：删除对象收进菜单，触发器与菜单项均可键盘操作 */
.ow-more{position:relative;display:inline-block}
.ow-more-menu{position:absolute;right:0;top:calc(100% + 4px);z-index:20;min-width:150px;background:var(--paper);border:1px solid var(--line-2);border-radius:var(--r-sm);box-shadow:var(--shadow-2);padding:4px;display:flex;flex-direction:column}
.ow-more-menu button{border:0;background:none;text-align:left;padding:8px 10px;border-radius:var(--r-sm);font-size:13px}
.ow-more-menu button:hover{background:var(--paper-2)}
.ow-more-menu .danger{color:var(--danger)}
.relation-sentence{margin:18px 0 0}
/* 对象/链接独立编辑表单：沿用原有版式，撑满右侧工作区（不设宽度上限，也不缩窄画布与数据表） */
.ow-graph-wrap{display:block}
.shared-def{margin-top:4px}
/* 删除/引用拦截等提示：紧贴详情头，点击操作处即可看到；
   左右内边距与 .ld-detail-head/.ld-tabs/.ld-body 一致（24px），否则会顶到面板两侧且比正文左移 */
.ow-alert{margin:12px 24px 0}
@media(max-width:1000px){
  .ow-toolbar .primary{margin-left:0}
}
</style>
