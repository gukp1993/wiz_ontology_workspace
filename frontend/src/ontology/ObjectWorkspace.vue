<!-- ─── 对象建模工作区（P03 / T02 对齐原型 objectWorkspace + editorView）───
     挂载点：App.vue view==='objects'（旧 #model/#links/#graph/#properties 深链均 alias 到此页）。
     协议冻结（任务板 §2/§7，勿改）：props {state:any; focusType?; focusProperty?}；
     emits ['before-change','changed','navigate']。
     浏览态（原型 objectWorkspace）：工具行（对象列表/关系画布 + ＋新建对象）在目录＋详情上方；
     左目录（157px）+ 右详情（名称/业务定义展示 + 「编辑定义」+ 属性/链接两页签）。
     编辑态（原型 render: ui.editor ? editorView() : pageView()）：主内容整体替换为一个完整表单，
     顶栏与主侧栏保留；表单上方「← 返回对象」，底部保存/取消。新建对象/编辑定义/新建属性/
     编辑属性/新建链接/编辑链接/从属性库添加全部一步进入完整表单，保存前不写入 graph
     （D02/D03）。「从属性库添加」为原型 libraryView(true) 挑选态：标题「为【对象名】添加属性」，
     每行「复制为私有」「引用共享」两个动作（D05），完成落盘后返回原对象并定位新增属性。
     链接页签同时展示当前对象作为起点和终点的链接，一行一个三元组，同一链接只维护一份（D06）。
     保存约定（T00 契约，app/formGuard.ts）：对象/链接表单注册 form-guard 离开保护，
     保存走 form-save.submitForm('ontology', mutate) 一次落盘，取消直接丢弃草稿；
     属性表单由 PropertyManager 自带同一契约。画布模式保留 ObjectCanvas 既有能力与
     自动保存（emit('before-change')/'changed' 路径不动）；浏览态删除仍走撤销快照 + changed。 -->
<script setup lang="ts">
import { ref, computed, watch, nextTick, inject, onMounted, onBeforeUnmount } from 'vue'
import ObjectCanvas from './ObjectCanvas.vue'
import PropertyManager from './PropertyManager.vue'
import Field from '../shared/EditorField.vue'
import AppSelect from '../shared/AppSelect.vue'
import BusinessRuleDialog from './BusinessRuleDialog.vue'
import RulePicker from './RulePicker.vue'
import { navIcons } from '../shared/icons'
import { localProperties, effectiveProperty, propertyTypeLabel, valueShapeOf, copyAsPrivate, addReference, shapeConflict } from './propertyModel'
import { appConfirm } from '../shared/appConfirm'
import { graphReferences, shortType } from './editorModel'
import { actionsOf, associationsOf, associationsOfObject, commitAssociations } from './actionModel'
import { commitRuleAssociations, ruleAssociationsOf, rulesOf, rulesOfObject } from './businessRuleModel'
import type { FormGuardAPI, FormSaveAPI } from '../app/formGuard'

// focusType/focusProperty：共享属性库「查看引用」/校验问题/旧深链跳转定位
// （propertyFocusId 存 apiName 或 @id，此处换算为节点 @id；指向共享定义时落到首个引用属性）。
const props = defineProps<{ state: any; focusType?: string; focusProperty?: string; initialTab?: string }>(), emit = defineEmits(['before-change', 'changed', 'navigate'])
const guardApi = inject<FormGuardAPI>('form-guard')!
const formSave = inject<FormSaveAPI>('form-save')!

const mode = ref<'list' | 'canvas'>('list'), selected = ref(''), message = ref('')
const canvasRef = ref<any>(null)
// ObjectCanvas 挂载时会主动 select 首个对象类型（画布初始化，非用户点击），
// 用 booting 标记吞掉这一次以及「在画布查看」的程序性 select，避免刚进画布就被弹回列表模式。
let canvasBooting = false

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
type LinkDraft = { label: string; from: string; to: string; cardinality: string; reverseLabel: string; comment: string }
type Editor =
  | { kind: 'object'; isNew: boolean; id: string; draft: { label: string; comment: string }; original: string; returnTab: Tab }
  | { kind: 'property'; targetTypeId: string; propertyId: string; returnTab: Tab }
  | { kind: 'link'; isNew: boolean; id: string; draft: LinkDraft; original: string; returnTab: Tab }
  | { kind: 'library'; targetTypeId: string }
const editor = ref<Editor | null>(null)
const editorError = ref(''), editorSaving = ref(false)
const objectDraft = computed(() => editor.value?.kind === 'object' ? editor.value.draft : null)
const linkDraft = computed(() => editor.value?.kind === 'link' ? editor.value.draft : null)

// 提示只对当前上下文有效：切换对象、切页签、切换列表/画布、进出编辑器都清空。
// 否则「暂不能删除…」这类提示会在换对象甚至新建对象后继续挂着，与当前对象不再对应。
watch([selected, detailTab, mode, editor], () => { if (message.value) message.value = '' })

// T00 离开保护：仅对象/链接草稿注册（属性表单由 PropertyManager 自带；挑选态无本地草稿）。
const wsGuard = { isDirty: () => { const e = editor.value; return !!e && 'draft' in e && JSON.stringify(e.draft) !== e.original }, discard: () => { editor.value = null } }
watch(() => { const e = editor.value; return !!e && (e.kind === 'object' || e.kind === 'link') }, open => { open ? guardApi.register(wsGuard) : guardApi.unregister(wsGuard) }, { immediate: true })
onBeforeUnmount(() => guardApi.unregister(wsGuard))

function closeEditor() { editor.value = null; editorError.value = '' }

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
function openObjectEditor(isNew: boolean) {
  const n: any = isNew ? null : current.value
  if (!isNew && !n) return
  const draft = { label: n?.['rdfs:label'] || '', comment: n?.['rdfs:comment'] || '' }
  editor.value = { kind: 'object', isNew, id: isNew ? 'mg:object_' + crypto.randomUUID().replaceAll('-', '') : n['@id'], draft, original: JSON.stringify(draft), returnTab: detailTab.value }
  editorError.value = ''
}
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
  })
  editorSaving.value = false
  if (!r.ok) { editorError.value = r.message; return }
  editor.value = null
  selected.value = e.id
  detailTab.value = e.isNew ? 'props' : e.returnTab
  locate(e.id)
}

// ─── 属性表单：整体替换主内容，挂载无目录的独立表单 PropertyManager ───
function openPropertyEditor(typeId: string, propertyId: string) {
  selected.value = typeId
  editor.value = { kind: 'property', targetTypeId: typeId, propertyId, returnTab: detailTab.value }
  editorError.value = ''
}
function onPropertySaved(payload: { id: string; targetTypeId?: string }) {
  const e = editor.value
  editor.value = null
  selected.value = payload.targetTypeId || (e?.kind === 'property' ? e.targetTypeId : '') || selected.value
  detailTab.value = e?.kind === 'property' ? e.returnTab : 'props'
  locate(payload.id)
}

// ─── 链接表单（D06：起点/终点/正向名称/数量关系必填直展；反向名称与业务定义折叠） ───
const CARDINALITY: Record<string, string> = { 'one-to-one': '一对一', 'one-to-many': '一对多', 'many-to-one': '多对一', 'many-to-many': '多对多' }
const linkTargetOptions = computed(() => objects.value.map((o: any) => ({ value: o['@id'], label: o['rdfs:label'] || o['@id'] })))
const cardinalityOptions = Object.entries(CARDINALITY).map(([value, label]) => ({ value, label }))
function openLinkEditor(id = '') {
  if (!current.value) return
  const n: any = id ? graph.value.find(x => x['@id'] === id) : null
  const draft: LinkDraft = n
    ? { label: n['rdfs:label'] || '', from: n['rdfs:domain']?.['@id'] || current.value['@id'], to: n['rdfs:range']?.['@id'] || '', cardinality: n['mg:cardinality'] || 'many-to-one', reverseLabel: n['mg:reverseLabel'] || '', comment: n['rdfs:comment'] || '' }
    : { label: '', from: current.value['@id'], to: objects.value.find(o => o['@id'] !== current.value?.['@id'])?.['@id'] || current.value['@id'], cardinality: 'many-to-one', reverseLabel: '', comment: '' }
  editor.value = { kind: 'link', isNew: !id, id: id || 'mg:link_' + crypto.randomUUID().replaceAll('-', ''), draft, original: JSON.stringify(draft), returnTab: detailTab.value === 'links' ? 'links' : detailTab.value }
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
  })
  editorSaving.value = false
  if (!r.ok) { editorError.value = r.message; return }
  editor.value = null
  detailTab.value = e.returnTab
  locate(e.id)
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
function openLibraryEditor() {
  if (!current.value) return
  editor.value = { kind: 'library', targetTypeId: current.value['@id'] }
  editorError.value = ''
}
async function pickShared(s: any, action: 'copy' | 'ref') {
  const e = editor.value
  if (!e || e.kind !== 'library' || editorSaving.value) return
  const name = s['rdfs:label'] || ''
  const existing = localProperties(graph.value, e.targetTypeId)
  if (action === 'ref' && shapeConflict(graph.value, s, e.targetTypeId)) { editorError.value = '「' + name + '」与已有同名属性的数据类型不同，无法引用同一份共享定义；可改用「复制为私有」。'; return }
  if (action === 'ref' && existing.some((p: any) => p['mg:sharedProperty']?.['@id'] === s['@id'])) { editorError.value = '此对象已引用「' + name + '」。'; return }
  if (existing.some((p: any) => effectiveProperty(p, graph.value)['rdfs:label'] === name)) { editorError.value = '「' + typeName(e.targetTypeId) + '」已有同名属性「' + name + '」；如需统一维护请先调整属性。'; return }
  editorSaving.value = true; editorError.value = ''
  let newId = ''
  const r = await formSave.submitForm('ontology', () => {
    const before = new Set(localProperties(graph.value, e.targetTypeId).map((p: any) => p['@id']))
    if (action === 'copy') copyAsPrivate(s, graph.value, e.targetTypeId)
    else addReference(graph.value, s, e.targetTypeId)
    newId = localProperties(graph.value, e.targetTypeId).find((p: any) => !before.has(p['@id']))?.['@id'] || ''
  })
  editorSaving.value = false
  if (!r.ok) { editorError.value = r.message; return }
  editor.value = null
  detailTab.value = 'props'
  locate(newId)
}

// ─── 浏览态：列表/详情骨架（对齐 20260917 微软列表详情原型）───
// 搜索覆盖全部对象（不只当前页）；筛选结果不含当前对象时保持选中不变，只在详情提示；
// 定位会清除妨碍定位的筛选并翻到目标页（交互约定见原型需求说明 §交互约定）。
const listQuery = ref(''), starOnly = ref(false), asc = ref(true), page = ref(1), stackedDetail = ref(false)
const pageSize = 12
const ldRef = ref<HTMLElement | null>(null), ldH = ref(560)
// 收藏是本地用户偏好，不写入本体语义模型（原型开发计划 §3：未确定偏好存储机制前不加后端字段）。
const STARS_KEY = 'wiz-object-stars'
const stars = ref<Set<string>>(readStars())
function readStars(): Set<string> {
  try {
    const raw = localStorage.getItem(STARS_KEY)
    const arr = raw ? JSON.parse(raw) : []
    return new Set(Array.isArray(arr) ? arr.filter((x: any) => typeof x === 'string') : [])
  } catch { return new Set<string>() }
}
function toggleStar(id: string) {
  const next = new Set(stars.value)
  if (next.has(id)) next.delete(id); else next.add(id)
  stars.value = next
  try { localStorage.setItem(STARS_KEY, JSON.stringify([...next])) } catch { /* 隐私模式等场景静默降级：收藏不落盘 */ }
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
  const actionCount = associationsOfObject(props.state, id).length
  return { id, label: o['rdfs:label'] || '', meta: `${propCount} 属性 · ${linkCount} 链接 · ${actionCount} 动作`, star: stars.value.has(id) }
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
  nextTick(syncHeight)
})
onBeforeUnmount(() => { stackMq?.removeEventListener('change', syncStacked); window.removeEventListener('resize', syncHeight) })
watch([mode, editor, message, current], () => nextTick(syncHeight))

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

// ─── 浏览态数据 ───
// 属性行：共享引用属性按生效定义展示名称、统一数据类型和单位。
const propRows = computed(() => {
  if (!current.value) return []
  return localProperties(graph.value, current.value['@id']).map((p: any) => {
    const m = effectiveProperty(p, graph.value)
    const shared = !!p['mg:sharedProperty']
    return { id: p['@id'], label: m['rdfs:label'] || '', type: propertyTypeLabel(p, graph.value), unit: m['mg:valueSuffix'] || '', isDisplayName: m['mg:isDisplayName']?.['@value'] === true, reuse: shared ? '共享引用 · ' + (m['rdfs:label'] || '') : '对象私有' }
  })
})
// 链接行（D06）：当前对象作为起点或终点的链接都展示；同一链接只维护一份，从任一端编辑同一节点。
const linkRows = computed(() => {
  if (!current.value) return []
  const cur = current.value['@id']
  return graph.value.filter((n: any) => n['@type'] === 'owl:ObjectProperty' && (n['rdfs:domain']?.['@id'] === cur || n['rdfs:range']?.['@id'] === cur))
    .map((n: any) => ({ id: n['@id'], label: n['rdfs:label'] || '未命名链接', from: typeName(n['rdfs:domain']?.['@id']), to: typeName(n['rdfs:range']?.['@id']), card: CARDINALITY[n['mg:cardinality']] || n['mg:cardinality'] || '', reverse: n['mg:reverseLabel'] || '' }))
})
function pick(id: string) { selected.value = id; message.value = ''; openDetailIfStacked() }

// ─── 动作页签（20260917 需求 §3.2）：可搜索多选关联，确认才保存，移除不删动作定义 ───
const actionById = computed(() => new Map(actionsOf(props.state).map((a: any) => [a.id, a])))
const actionRows = computed(() => {
  if (!current.value) return []
  return associationsOfObject(props.state, current.value['@id']).map(r => {
    const a: any = actionById.value.get(r.actionId)
    return { actionId: r.actionId, name: a?.name || '', effect: a?.effect || '', desc: a?.description || '', missing: !a, legacy: a && a.definitionVersion !== 2 }
  })
})
const pickerOpen = ref(false), pickQuery = ref('')
const chosen = ref(new Set<string>())
const pickRows = computed(() => actionsOf(props.state)
  .filter((a: any) => (a.name || '').includes(pickQuery.value.trim()))
  .map((a: any) => ({ id: a.id, name: a.name || '未命名动作', desc: a.description || '', associated: actionRows.value.some(r => r.actionId === a.id) })))
function openPicker() { pickerOpen.value = true; pickQuery.value = ''; chosen.value = new Set() }
function cancelPick() { pickerOpen.value = false; chosen.value = new Set() }
function togglePick(id: string, event: Event) {
  const next = new Set(chosen.value)
  ;(event.target as HTMLInputElement).checked ? next.add(id) : next.delete(id)
  chosen.value = next
}
async function confirmPick() {
  if (!chosen.value.size || !current.value) return
  const add = [...chosen.value]
  const r = await formSave.submitForm('ontology', () => {
    commitAssociations(props.state, [...associationsOf(props.state), ...add.map(id => ({ objectTypeId: current.value['@id'], actionId: id }))])
  })
  if (!r.ok) { message.value = r.message; return }
  pickerOpen.value = false
  chosen.value = new Set()
  locate(add[0])
}
async function removeAssociation(actionId: string) {
  if (!current.value) return
  if (!(await appConfirm({ message: '仅移除当前对象与该动作的关联？动作定义保留，其他对象的关联不受影响；项目里已有的绑定会显示关联失效并保留配置。' }))) return
  const typeId = current.value['@id']
  const r = await formSave.submitForm('ontology', () => {
    commitAssociations(props.state, associationsOf(props.state).filter(a => !(a.actionId === actionId && (a.objectTypeId === typeId || a.objectTypeId === 'mg:' + typeId.replace(/^mg:/, '')))))
  })
  if (!r.ok) message.value = r.message
}

// ─── 规则页签（20260917 业务规则一期）：多选引用已有规则、查看、移除引用 ───
const ruleRows = computed(() => {
  if (!current.value) return []
  return rulesOfObject(props.state, current.value['@id']).map(r => ({ ruleId: r.ruleId, name: r.rule?.name || '', missing: !r.rule }))
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
  })
  if (!r.ok) { message.value = r.message; return }
  rulePickerOpen.value = false
  locate(ids[0])
}
async function removeRuleRef(ruleId: string) {
  if (!current.value) return
  const typeId = current.value['@id']
  const r = await formSave.submitForm('ontology', () => {
    commitRuleAssociations(props.state, ruleAssociationsOf(props.state).filter(a => !(a.ruleId === ruleId && (a.objectTypeId === typeId || a.objectTypeId === 'mg:' + typeId.replace(/^mg:/, '')))))
  })
  if (!r.ok) message.value = r.message
}

// 引用检查与 EntityManager/画布同一套：有引用先提示，不静默断链；confirm 后删除，可撤销。
// 动作关联随对象删除一并清理（需求 §5）；规则引用走引用保护——须先移除引用（业务规则一期 §5）。
async function removeNode(id: string, label: string) {
  const refs = graphReferences(props.state, id)
  if (refs.length) { message.value = '暂不能删除：请先处理引用（' + refs.join('、') + '）。'; return }
  const ruleRefs = ruleAssociationsOf(props.state).filter(a => a.objectTypeId === id || a.objectTypeId === 'mg:' + id.replace(/^mg:/, ''))
  if (ruleRefs.length) { message.value = `暂不能删除：此对象仍引用 ${ruleRefs.length} 条业务规则；请先到「规则」页签移除引用。`; return }
  const assocCount = associationsOfObject(props.state, id).length
  const extra = assocCount ? `此对象有 ${assocCount} 条动作关联，删除对象将同时移除这些关联（动作定义与项目绑定保留）。` : ''
  if (!(await appConfirm({ message: '删除「' + (label || id) + '」？可通过撤销恢复。' + extra }))) return
  emit('before-change')
  props.state.ontology['@graph'] = graph.value.filter((n: any) => n['@id'] !== id)
  if (assocCount) commitAssociations(props.state, associationsOf(props.state).filter(a => a.objectTypeId !== id && a.objectTypeId !== 'mg:' + id.replace(/^mg:/, '')))
  emit('changed')
}

// ─── 模式切换与画布联动 ───
function showCanvas() { mode.value = 'canvas'; canvasBooting = true }
async function showInCanvas() {
  const id = selected.value
  mode.value = 'canvas'; canvasBooting = true
  await nextTick()
  canvasBooting = true // 挂载初始化的 select 已被消费；focusNode 引发的 select 同样不切列表
  canvasRef.value?.focusNode?.(id)
}
// 画布点击节点：只对对象类型生效（连线属链接类型，留在画布 inspector 查看），切回列表模式定位。
function selectById(id: string) {
  if (canvasBooting) { canvasBooting = false; if (id && objects.value.some((n: any) => n['@id'] === id)) selected.value = id; return }
  if (!id || !objects.value.some((n: any) => n['@id'] === id)) return
  selected.value = id; mode.value = 'list'
}
</script>

<template>
<div class="object-workspace ow-root" :class="{ 'ow-canvas-grid': mode === 'canvas' }">
  <!-- 画布模式：复用 ObjectCanvas 内置工具条/目录/inspector，自动保存路径不动 -->
  <template v-if="mode === 'canvas'">
    <div class="ow-mode-tabs ow-canvas-tabs" role="tablist" aria-label="对象建模模式">
      <button role="tab" :aria-selected="false" @click="mode = 'list'">对象列表</button>
      <button role="tab" class="active" :aria-selected="true">关系画布</button>
    </div>
    <ObjectCanvas ref="canvasRef" :state="state" @before-change="emit('before-change')" @changed="emit('changed')" @select="selectById"/>
  </template>
  <!-- 编辑态：主内容整体替换为一个完整表单（原型 ui.editor ? editorView() : pageView()） -->
  <template v-else-if="editor">
    <PropertyManager v-if="editor.kind === 'property'" :key="editor.propertyId || 'new'" :state="state" kind="property" :target-type-id="editor.targetTypeId" :property-id="editor.propertyId" @close="closeEditor" @saved="onPropertySaved"/>
    <section v-else-if="editor.kind === 'object'" class="card detail-card ow-editor">
      <div class="ow-editor-head"><button type="button" @click="closeEditor">← 返回对象</button></div>
      <div class="detail-heading"><div><span class="eyebrow">对象类型</span><h2>{{ editor.isNew ? '新建对象类型' : '维护对象定义' }}</h2></div></div>
      <p v-if="editorError" class="inline-error" role="alert">{{ editorError }}</p>
      <div class="form-grid">
        <Field label="对象名称" class="full" :model-value="objectDraft?.label || ''" required example="例如：储能簇" @update:model-value="objectDraft && (objectDraft.label = $event)"/>
        <Field label="业务定义" type="textarea" class="full" :model-value="objectDraft?.comment || ''" required example="说明它是什么，用什么业务边界区分。" help="只需名称和业务定义即可保存；属性与链接在对象内补充。" @update:model-value="objectDraft && (objectDraft.comment = $event)"/>
      </div>
      <div class="detail-footer">
        <div class="tools"><button type="button" class="primary" :disabled="editorSaving" @click="saveObject">{{ editorSaving ? '保存中…' : '保存' }}</button><button type="button" @click="closeEditor">取消</button></div>
        <span>只影响当前本体草稿；已发布版本不变。</span>
      </div>
    </section>
    <section v-else-if="editor.kind === 'link'" class="card detail-card ow-editor">
      <div class="ow-editor-head"><button type="button" @click="closeEditor">← 返回对象</button></div>
      <div class="detail-heading"><div><span class="eyebrow">业务链接</span><h2>{{ editor.isNew ? '定义业务链接' : '维护 · ' + (linkDraft?.label || '未命名链接') }}</h2></div><span class="status-pill">同一链接正反两个阅读方向</span></div>
      <p v-if="editorError" class="inline-error" role="alert">{{ editorError }}</p>
      <div class="form-grid">
        <label>起点对象 *<AppSelect :model-value="linkDraft?.from || ''" aria-label="起点对象" :options="linkTargetOptions" @update:model-value="linkDraft && (linkDraft.from = $event)"/></label>
        <label>终点对象 *<AppSelect :model-value="linkDraft?.to || ''" aria-label="终点对象" :options="linkTargetOptions" @update:model-value="linkDraft && (linkDraft.to = $event)"/></label>
        <Field label="正向名称" :model-value="linkDraft?.label || ''" required example="例如：所属设备" help="从起点读到终点的业务含义。" @update:model-value="linkDraft && (linkDraft.label = $event)"/>
        <Field label="数量关系" type="select" :model-value="linkDraft?.cardinality || 'many-to-one'" :options="cardinalityOptions" required @update:model-value="linkDraft && (linkDraft.cardinality = $event)"/>
      </div>
      <details class="technical-section">
        <summary>反向阅读名称（选填）</summary>
        <Field label="反向名称" :model-value="linkDraft?.reverseLabel || ''" example="例如：包含储能簇" help="同一条链接的反向表达，不重复建立另一条链接。" @update:model-value="linkDraft && (linkDraft.reverseLabel = $event)"/>
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
    <section v-else-if="editor.kind === 'library'" class="card detail-card ow-editor">
      <div class="ow-editor-head"><button type="button" @click="closeEditor">← 返回对象</button></div>
      <div class="detail-heading"><div><span class="eyebrow">从属性库添加</span><h2>为「{{ typeName(editor.targetTypeId) }}」添加属性</h2></div></div>
      <p class="fill-hint">复制生成私有定义（新属性标识，此后独立修改）；引用保留共享关联（名称、类型、单位跟随共享定义统一维护）。完成后返回原对象并定位新增属性。</p>
      <p v-if="editorError" class="inline-error" role="alert">{{ editorError }}</p>
      <div v-if="sharedDefs.length">
        <div v-for="row in sharedDefs.map(sharedRow)" :key="row.id" class="ow-pick-row">
          <div class="row-main">
            <strong>{{ row.label }}</strong>
            <small>{{ row.type }}{{ row.unit ? ' · 单位 ' + row.unit : '' }} · {{ row.usage }} 处引用</small>
            <small class="muted">{{ row.desc || '暂无业务定义' }}</small>
            <small v-if="row.conflict" class="inline-warning">已有同名但数据类型不同的属性，无法引用；可复制为私有。</small>
          </div>
          <div class="ow-pick-actions">
            <button type="button" :disabled="editorSaving" @click="pickShared(sharedDefs.find((s: any) => s['@id'] === row.id), 'copy')">复制为私有</button>
            <button type="button" :disabled="editorSaving || row.conflict" @click="pickShared(sharedDefs.find((s: any) => s['@id'] === row.id), 'ref')">引用共享</button>
          </div>
        </div>
      </div>
      <div v-else class="empty">
        <p>共享属性库为空。可先在对象中建立私有属性，再到「共享属性库」转为共享定义。</p>
        <button type="button" @click="emit('navigate', 'library')">前往共享属性库 →</button>
      </div>
    </section>
  </template>
  <!-- 浏览态：列表/详情骨架（20260917 原型）：左紧凑列表 + 右详情，两区独立滚动 -->
  <template v-else>
    <div class="ow-toolbar">
      <div class="ow-mode-tabs" role="tablist" aria-label="对象建模模式">
        <button role="tab" class="active" :aria-selected="true">对象列表</button>
        <button role="tab" :aria-selected="false" @click="showCanvas">关系画布</button>
      </div>
      <button class="primary" @click="openObjectEditor(true)">＋ 新建对象</button>
    </div>
    <div ref="ldRef" class="ld" :class="{ 'ld-stacked': isStacked, 'ld-detail-open': stackedDetail }" :style="{ '--ld-h': ldH + 'px' }">
      <!-- 列表区：搜索覆盖全部对象；筛选不丢失选中；分页保留详情 -->
      <section class="ld-list" aria-label="对象类型列表">
        <div class="ld-list-head"><h2>对象类型 <span class="muted">{{ filteredObjects.length }} / {{ objects.length }}</span></h2></div>
        <div class="ld-search">
          <input type="search" :value="listQuery" placeholder="搜索全部对象类型…" aria-label="搜索全部对象类型（名称与业务定义）" @input="onSearch(($event.target as HTMLInputElement).value)">
          <button v-if="listQuery" type="button" class="ld-search-clear" aria-label="清空搜索" @click="clearFilters">×</button>
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
            <p v-else>还没有对象类型。</p>
            <button v-if="listQuery || starOnly" type="button" @click="clearFilters">清空筛选</button>
            <button v-else type="button" class="primary" @click="openObjectEditor(true)">＋ 新建对象</button>
          </div>
        </div>
        <div v-if="showPager" class="ld-pager">
          <span>{{ (page - 1) * pageSize + 1 }}–{{ Math.min(page * pageSize, filteredObjects.length) }} / {{ filteredObjects.length }}</span>
          <span class="ld-pager-ctl">
            <button type="button" :disabled="page <= 1" aria-label="上一页" @click="page--">‹</button>
            <span>{{ page }} / {{ pageCount }}</span>
            <button type="button" :disabled="page >= pageCount" aria-label="下一页" @click="page++">›</button>
          </span>
        </div>
      </section>
      <!-- 详情区：页签在滚动容器内吸顶；筛选不含当前对象时如实提示，不静默换对象 -->
      <section class="ld-detail" aria-label="对象详情">
        <button type="button" class="ld-back" @click="backToList">← 返回对象列表</button>
        <div v-if="!current" class="empty-state">
          <span class="empty-state-ico" aria-hidden="true"><svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path :d="navIcons.objects"/></svg></span>
          <p><strong>从第一个对象开始。</strong></p>
          <p>只需名称和业务定义，随后在对象内补充属性与链接。<br>也可以切换到关系画布，直接新建节点并连线。</p>
          <button type="button" class="primary" @click="openObjectEditor(true)">＋ 新建对象</button>
        </div>
        <template v-else>
          <div class="ld-detail-head">
            <div class="ow-head-row">
              <h2 class="ow-h2-name">{{ current['rdfs:label'] || '未命名对象' }}</h2>
              <div class="ow-head-side">
                <button type="button" :aria-pressed="starred" :title="starred ? '取消收藏（仅保存在本机浏览器，不写入本体）' : '收藏（仅保存在本机浏览器，不写入本体）'" @click="toggleStar(current['@id'])">{{ starred ? '★ 已收藏' : '☆ 收藏' }}</button>
                <button type="button" @click="showInCanvas" title="在关系画布中定位此对象">在画布查看</button>
                <button type="button" @click="openObjectEditor(false)">编辑定义</button>
                <button type="button" class="danger" @click="removeNode(current['@id'], current['rdfs:label'])">删除对象</button>
              </div>
            </div>
            <p class="ow-h2-def">{{ current['rdfs:comment'] || '暂无业务定义。' }}</p>
            <div class="ld-meta">
              <span>属性 {{ propRows.length }}</span><span>链接 {{ linkRows.length }}</span><span>动作 {{ actionRows.length }}</span><span>规则 {{ ruleRows.length }}</span>
              <span v-if="selectedHidden" class="ld-meta-warn">此对象不在当前筛选结果中，已保持选中<button type="button" class="row-link" @click="clearFilters">清除筛选</button></span>
            </div>
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
            <div class="section-heading"><span class="muted">定义属性的含义与数据类型</span><span class="ow-head-actions"><button @click="openLibraryEditor">从属性库添加</button><button @click="openPropertyEditor(current['@id'], '')">＋ 新增属性</button></span></div>
            <table v-if="propRows.length">
              <thead><tr><th>属性</th><th>数据类型</th><th>复用方式</th><th></th></tr></thead>
              <tbody>
                <tr v-for="p in propRows" :key="p.id" :data-row="p.id" :class="{ 'ow-flash-row': highlightId === p.id }">
                  <td><strong>{{ p.label || '未命名属性' }}</strong><small v-if="p.isDisplayName || p.unit" class="muted" style="display:block">{{ [p.isDisplayName ? '显示名称' : '', p.unit ? '单位 ' + p.unit : ''].filter(Boolean).join(' · ') }}</small></td>
                  <td>{{ p.type }}</td>
                  <td>{{ p.reuse }}</td>
                  <td class="ow-row-tools">
                    <button class="row-link" @click="openPropertyEditor(current['@id'], p.id)">编辑</button>
                    <button class="row-link danger" @click="removeNode(p.id, p.label)">删除</button>
                  </td>
                </tr>
              </tbody>
            </table>
            <!-- 空态只做说明，操作入口统一在右上角（避免同一组按钮出现两处） -->
            <div v-else class="empty">
              <p>给「{{ current['rdfs:label'] || '此对象' }}」添加第一个属性，例如名称、额定功率或 SOC；用右上角的「＋ 新增属性」新建，或「从属性库添加」复用共享定义。</p>
            </div>
            <p class="field-help">属性值从哪里来（数据字段或计算结果）属于项目实现，在项目映射的「属性取值」中维护。</p>
          </template>
          <template v-else-if="detailTab === 'links'">
            <div class="section-heading"><span class="muted">一份链接定义，正反两个阅读方向</span><button @click="openLinkEditor()">＋ 新增链接</button></div>
            <div v-if="linkRows.length" class="ow-link-rows">
              <div v-for="l in linkRows" :key="l.id" class="ow-link-row" :data-row="l.id" :class="{ 'ow-flash-row': highlightId === l.id }">
                <div class="row-main">
                  <strong>{{ l.from }} → {{ l.label }} → {{ l.to }}</strong>
                  <small>{{ l.card }}{{ l.reverse ? ' · 反向：' + l.reverse : '' }}</small>
                </div>
                <div class="ow-row-tools"><button class="row-link" @click="openLinkEditor(l.id)">编辑</button><button class="row-link danger" @click="removeNode(l.id, l.label)">删除</button></div>
              </div>
            </div>
            <p v-else class="muted">暂无与「{{ current['rdfs:label'] || '此对象' }}」关联的链接。点击「＋ 新增链接」，或在关系画布中用连线模式创建。</p>
          </template>
          <template v-else-if="detailTab === 'actions'">
            <div class="section-heading"><span class="muted">选择此类对象支持的动作；共享定义在动作库中维护，不复制为对象私有。</span><button @click="openPicker">＋ 添加动作</button></div>
            <div v-if="pickerOpen" class="ow-action-picker">
              <input type="search" v-model="pickQuery" placeholder="搜索动作名称" aria-label="搜索可关联动作">
              <div class="ow-picker-list">
                <label v-for="row in pickRows" :key="row.id" class="check-option ow-pick-option">
                  <input type="checkbox" :disabled="row.associated" :checked="row.associated || chosen.has(row.id)" :aria-label="row.name" @change="togglePick(row.id, $event)">
                  <span><strong>{{ row.name }}</strong><small class="muted" style="display:block">{{ row.desc || '暂无业务定义' }}</small><small v-if="row.associated" class="muted">已关联，不能重复添加</small></span>
                </label>
                <p v-if="!pickRows.length" class="muted">没有匹配动作。请先到「动作定义」创建。</p>
              </div>
              <div class="tools"><button :disabled="!chosen.size" @click="confirmPick">确认关联{{ chosen.size ? '（已选 ' + chosen.size + ' 项）' : '' }}</button><button @click="cancelPick">取消</button></div>
              <p class="field-help">勾选尚未保存；点击「确认关联」一次保存，取消不变更已保存关联。</p>
            </div>
            <table v-if="actionRows.length">
              <thead><tr><th>动作</th><th>业务效果</th><th></th></tr></thead>
              <tbody>
                <tr v-for="row in actionRows" :key="row.actionId" :data-row="row.actionId" :class="{ 'ow-flash-row': highlightId === row.actionId }">
                  <td><strong>{{ row.name || '未命名动作' }}</strong><small v-if="row.missing" class="muted" style="display:block">动作定义不存在（悬空引用）</small><small v-else-if="row.legacy" class="muted" style="display:block">历史格式</small><small v-else class="muted" style="display:block">{{ row.desc }}</small></td>
                  <td class="ow-effect-cell">{{ row.effect || '—' }}</td>
                  <td class="ow-row-tools">
                    <button v-if="!row.missing" class="row-link" @click="emit('navigate', 'actions', { definition: row.actionId })">查看定义</button>
                    <button class="row-link danger" @click="removeAssociation(row.actionId)">移除关联</button>
                  </td>
                </tr>
              </tbody>
            </table>
            <div v-else class="empty">
              <p>尚未关联动作。点击「＋ 添加动作」从动作库选择此类对象支持的操作。</p>
            </div>
            <p class="field-help">多对象关联表示一个动作可分别作用于这些类型，不表示批量执行。项目实现在「对象映射 → 动作绑定」中按对象分别配置。</p>
          </template>
          <template v-else-if="detailTab === 'rules'">
            <div class="section-heading"><span class="muted">引用规则库中的通用业务规则；移除引用不会删除规则。</span><button @click="rulePickerOpen = true">＋ 添加规则</button></div>
            <table v-if="ruleRows.length">
              <thead><tr><th>名称</th><th></th></tr></thead>
              <tbody>
                <tr v-for="row in ruleRows" :key="row.ruleId" :data-row="row.ruleId" :class="{ 'ow-flash-row': highlightId === row.ruleId }">
                  <td><strong>{{ row.name || '未命名规则' }}</strong><small v-if="row.missing" class="muted" style="display:block">规则不存在（悬空引用）</small></td>
                  <td class="ow-row-tools">
                    <button v-if="!row.missing" class="row-link" @click="ruleDetailId = row.ruleId">查看</button>
                    <button class="row-link danger" @click="removeRuleRef(row.ruleId)">移除引用</button>
                  </td>
                </tr>
              </tbody>
            </table>
            <div v-else class="empty">
              <p>还没有引用规则。点击「＋ 添加规则」从规则库选择。</p>
            </div>
            <p class="field-help">一条规则可被多个对象类型引用；引用使用稳定标识，规则重命名后引用保持。规则的正文维护在「业务规则」页。</p>
          </template>

          </div>
        </template>
      </section>
    </div>
  </template>
  <!-- 规则引用弹窗：多选添加（仅可选未引用规则）+ 只读查看 -->
  <RulePicker v-if="rulePickerOpen" :rules="pickerAvailable" :object-name="current?.['rdfs:label'] || ''" @close="rulePickerOpen = false" @confirm="addRules"/>
  <BusinessRuleDialog v-if="ruleDetail" mode="detail" :rule="ruleDetail" :allow-edit="false" @close="ruleDetailId = ''"/>
</div>
</template>

<style scoped>
/* 浏览态的列表/详情骨架在全局 .ld-*（style.css）：两区独立滚动，窄屏单栏。 */
.ow-root{display:block}
.ow-canvas-tabs{max-width:360px}
.ow-toolbar{display:flex;align-items:center;gap:10px;margin-bottom:12px;flex-wrap:wrap}
.ow-toolbar .ow-mode-tabs{display:flex;gap:6px;margin-bottom:0}
.ow-toolbar .ow-mode-tabs button{flex:none}
.ow-toolbar .primary{margin-left:auto}
.ow-editor-head{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:14px;flex-wrap:wrap}
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
.ow-pick-row{display:flex;justify-content:space-between;align-items:center;gap:14px;padding:13px 4px;border-bottom:1px solid var(--line);flex-wrap:wrap}
.ow-pick-row:last-child{border-bottom:0}
.ow-pick-row .row-main{flex:1;min-width:0}
.ow-pick-row .row-main strong{font-size:14px}
.ow-pick-row .row-main small{display:block;margin-top:3px;font-size:12px;color:var(--muted);overflow-wrap:anywhere}
.ow-pick-actions{display:flex;gap:8px;flex:none;flex-wrap:wrap}
.ow-action-picker{border:1px solid var(--line);border-radius:var(--r-md);padding:14px;margin-bottom:14px;background:var(--paper-2)}
.ow-picker-list{max-height:260px;overflow:auto;margin:10px 0}
.ow-pick-option{padding:8px 4px;border-bottom:1px solid var(--line)}
.ow-pick-option:last-child{border-bottom:0}
.ow-effect-cell{max-width:420px;white-space:pre-wrap;font-size:13px;color:var(--ink-2)}
/* 详情头：名称+操作同一行，业务定义与元信息各自一行。 */
.ow-head-row{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}
.ow-head-row .ow-h2-name{margin:0}
.ow-h2-def{margin:6px 0 0}
.ow-head-side .danger{margin:0}
.relation-sentence{margin:18px 0 0}
/* 删除/引用拦截等提示：紧贴详情头，点击操作处即可看到；
   左右内边距与 .ld-detail-head/.ld-tabs/.ld-body 一致（24px），否则会顶到面板两侧且比正文左移 */
.ow-alert{margin:12px 24px 0}
@media(max-width:1000px){
  .ow-toolbar .primary{margin-left:0}
}
</style>
