<!-- ─── 共享属性库（20260918 本体列表统一设计 §6.5/§5/§7：统一标准表格视图）───
     挂载点：App.vue view==='library'。
     props：state — 当前本体草稿（JSON-LD 内存形态；共享属性为 '@type':'mg:SharedProperty' 记录）。
     emits（协议冻结，勿改）：
       before-change — 写内存前发出（App 撤销快照）；
       changed       — 已写入内存（App 自动保存）；
       navigate(view, focus?:{type?,property?,...}) — 引用位置抽屉跳回具体对象属性：
                       navigate('objects',{type:对象类型id, property:属性id})。
     结构：.ont-lib-head 页头（左侧标题+一句说明；右侧更多操作菜单：粘贴多行/批量复用，
     及「＋ 新建共享属性」主入口）+ OntologyList 统一表格（名称/数据类型/引用情况/操作，
     搜索+数据类型筛选+每页 20 分页）。
     「编辑」「＋ 新建共享属性」进入独立属性表单（PropertyManager kind='shared'，主内容整体
     替换；新建不先插入空记录，保存走 form-save 一次落盘，取消不产生记录——T00 契约）。
     名称点击 → 只读详情抽屉；引用徽标 → 引用位置抽屉（可定位回对象属性行）。
     「为某对象添加属性」的挑选态（原型 libraryView(true)）在 ObjectWorkspace 编辑态内完成，
     本页不再承担；跨对象批量能力：引用到对象/复制为私有为行内操作（20260919 从「⋯」
     菜单平铺），粘贴多行/批量复用为低频项收在页头「更多操作」。修改共享定义的引用影响在表单内如实展示（usage 信息）；
     单值与序列不能引用同一份形态不兼容的共享定义（shapeConflict）。
     删除定义（20260920 统一语义）：契约/接口/项目映射或对象属性引用一律阻断并列出名称+
     定位入口（引用位置抽屉）；无引用才允许确认删除，不自动私有化。
     外部依赖「去处理」（S4，20260920 验收）：跳转携带 returnTo（原共享定义 id/名称），处理完可
     一步返回本页引用位置抽屉继续操作；带 focusId 进入但非编辑时同样打开引用位置抽屉。 -->
<script setup lang="ts">
import { ref, computed, watch, nextTick } from 'vue'
import { appConfirm } from '../shared/appConfirm'
import AppSelect from '../shared/AppSelect.vue'
import RowMenu from '../shared/RowMenu.vue'
import OntologyList from '../shared/OntologyList.vue'
import OntDrawer from '../shared/OntDrawer.vue'
import PropertyManager from './PropertyManager.vue'
import { listShared, referencesOf, shapeConflict, copyAsPrivate, propertyDataType, dataTypeLabel, propertyTypeLabel, makeProperty, localProperties, effectiveProperty, addReference, asShared, parsePropertyRows } from './propertyModel'
import { useOntTable } from './ontList'
import { externalDependencies, externalDependencyTarget, sharedDeleteCheck } from './dependencyModel'

// canvasReturn（20260919 图谱优化）：非空表示从本体图谱「打开定义」跳转而来，页头显示「返回图谱」（前端可选上下文）。
const props = defineProps<{ state: any; canvasReturn?: string; focusId?: string; editFocus?: boolean; openUsages?: boolean }>()
const emit = defineEmits(['before-change', 'changed', 'navigate'])
function backToGraph() { emit('navigate', 'objects', { graph: true, graphFocus: props.canvasReturn || '' } as any) }

// 编辑态：维护定义 / 新建共享定义 → 独立属性表单整体替换主内容（id='' 为新建）。
const editor = ref<null | { id: string }>(null)
const feedback = ref(''), dialog = ref(''), acting = ref<any>(null)
const targets = ref<string[]>([]), copyTarget = ref('')
// 更多操作（低频批量能力收在页头菜单，不占属性表单主流程）
const pasteTarget = ref(''), pasted = ref('')
const batchSource = ref(''), batchProps = ref<string[]>([]), batchTargets = ref<string[]>([])
const highlightId = ref(''), detailId = ref(''), usagesId = ref('')
const typeFilter = ref('全部')

const graph = computed(() => props.state.ontology['@graph'])
const types = computed(() => graph.value.filter((n: any) => n['@type'] === 'owl:Class'))
const definitions = computed(() => listShared(graph.value))
const typeOptions = computed(() => types.value.map((t: any) => ({ value: t['@id'], label: t['rdfs:label'] || t['@id'] })))
const batchSourceProps = computed(() => batchSource.value ? localProperties(graph.value, batchSource.value) : [])
watch(batchSource, () => { batchProps.value = [] })

function before(named?: { actionLabel: string; target?: { kind: string; id: string; ownerId?: string } }) { emit('before-change', named) }
function changed() { emit('changed') }
function mutate(fn: any, actionLabel?: string, target?: { kind: string; id: string; ownerId?: string }) { before(actionLabel ? { actionLabel, target } : undefined); fn(); changed() }
const typeName = (id: string) => types.value.find((n: any) => n['@id'] === id)?.['rdfs:label'] || id
const summary = (s: any) => { const c = s['rdfs:comment'] || ''; return c.length > 60 ? c.slice(0, 60) + '…' : (c || '暂无业务定义') }
// 引用计数口径（§6.5）：referencesOf 的对象属性引用记录数，不是去重对象类型数。
const usageCount = (s: any) => referencesOf(graph.value, s['@id']).length
const totalUsages = computed(() => definitions.value.reduce((n: number, s: any) => n + usageCount(s), 0))

// ── 列表视图状态（§5/§9）：视图行 + 类型筛选 + 搜索（全量上匹配）+ 名称 zh-CN 排序 + 每页 20 ──
interface SharedRow { id: string; label: string; comment: string; dt: any; typeMain: string; valueType: string; refs: number; raw: any }
const rows = computed<SharedRow[]>(() => definitions.value.map((s: any) => {
  const dt = propertyDataType(s, graph.value)
  return {
    id: String(s['@id']), label: s['rdfs:label'] || '', comment: s['rdfs:comment'] || '',
    dt,
    typeMain: dt.type === 'timeSeries' ? '时间序列' : dataTypeLabel(dt),
    valueType: dt.type === 'timeSeries' ? dataTypeLabel({ type: dt.valueType }) : '',
    refs: usageCount(s),
    raw: s,
  }
}))
// 数据类型筛选：以 propertyDataType 规范化类型判定；时间序列只命中「时间序列」，不命中「数值」。
const TYPE_FILTERS: { key: string; test: (dt: any) => boolean }[] = [
  { key: '文本', test: dt => dt.type === 'string' },
  { key: '数值', test: dt => dt.type === 'double' || dt.type === 'decimal' || dt.type === 'integer' },
  { key: '是／否', test: dt => dt.type === 'boolean' },
  { key: '日期', test: dt => dt.type === 'date' },
  { key: '时间', test: dt => dt.type === 'dateTime' || dt.type === 'timestamp' },
  { key: '数组', test: dt => dt.type === 'array' },
  { key: '结构体', test: dt => dt.type === 'struct' },
  { key: '时间序列', test: dt => dt.type === 'timeSeries' },
]
const typeFilterKeys = ['全部', ...TYPE_FILTERS.map(f => f.key)]
const typeFiltered = computed(() => {
  if (typeFilter.value === '全部') return rows.value
  const f = TYPE_FILTERS.find(x => x.key === typeFilter.value)
  return f ? rows.value.filter(r => f.test(r.dt)) : rows.value
})
const list = useOntTable<SharedRow>(() => typeFiltered.value, {
  match: (r, q) => (r.label + ' ' + r.comment).toLowerCase().includes(q),
  sort: (a, b) => a.label.localeCompare(b.label, 'zh-CN', { numeric: true }),
})
function setTypeFilter(key: string) { typeFilter.value = key; list.page.value = 1 }
// 空态（§8）：初始无数据指向右上角唯一主入口；有筛选条件时引导调整而不是重复新建。
const hasFilter = computed(() => typeFilter.value !== '全部' || !!list.q.value.trim())
const emptyTitle = computed(() => hasFilter.value ? '没有匹配的共享定义' : '共享属性库为空')
const emptyHint = computed(() => hasFilter.value ? '调整关键词或筛选条件再试试。' : '点击右上角「＋ 新建共享属性」新建，或在对象属性中选择「转为共享属性」。')

// ── 维护定义 / 新建：独立表单（PropertyManager kind='shared'），保存成功后定位行 ──
function openEditor(id = '') { editor.value = { id }; feedback.value = '' }
// 图谱「编辑」跳转（2026-09-20）：按 focusId 直接打开指定共享定义的编辑表单
//（保存/取消后经页头「← 返回图谱」回画布并定位原节点）。
// S4（20260920 验收）：外部依赖「去处理」处理完依赖后带 focus:{definition}+openUsages 跳回本页，
// 直接恢复引用位置抽屉——用户可继续看剩余依赖、继续去处理，或按提示删除该定义。
// 仅显式 openUsages 才开抽屉：普通进入（含菜单重入）不带该标记时不受历史 focusId 影响。
watch(() => [props.focusId, props.editFocus, props.openUsages], ([id, edit, usages]) => {
  if (!id) return
  if (edit) openEditor(String(id))
  else if (usages) usagesId.value = String(id)
}, { immediate: true })
function onSaved(payload: { id: string }) { editor.value = null; locate(payload.id) }
// 来自图谱的编辑：保存/返回图谱直接回画布并定位（不再停留在库页）
function onSharedSaved(payload: { id: string }) { if (props.canvasReturn) { editor.value = null; backToGraph(); return } onSaved(payload) }
// 保存后定位行：必要时清筛选并翻到目标行所在页，再高亮滚动（data-lib-row 在 <tr> 上）。
async function locate(id: string) {
  if (!id) return
  let idx = list.filtered.value.findIndex(r => r.id === id)
  if (idx < 0) { typeFilter.value = '全部'; list.reset(); idx = list.filtered.value.findIndex(r => r.id === id) }
  if (idx >= 0) list.page.value = Math.floor(idx / 20) + 1
  highlightId.value = id
  await nextTick()
  document.querySelector(`[data-lib-row="${id}"]`)?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  setTimeout(() => { if (highlightId.value === id) highlightId.value = '' }, 2600)
}

// ── 只读详情抽屉（§7）：名称入口；引用位置抽屉（D05 反向链路）：引用徽标入口 ──
const detail = computed(() => detailId.value ? definitions.value.find((s: any) => s['@id'] === detailId.value) || null : null)
const usageTarget = computed(() => usagesId.value ? definitions.value.find((s: any) => s['@id'] === usagesId.value) || null : null)
const usageRows = computed(() => usageTarget.value ? referencesOf(graph.value, usageTarget.value['@id']).map((p: any) => ({ id: p['@id'], domain: p['rdfs:domain']?.['@id'] || '', name: typeName(p['rdfs:domain']?.['@id']), api: p['mg:apiName'] || String(p['@id']).replace(/^mg:/, '') })) : [])
// R5（20260920 验收）：契约/接口/动作/规则/项目映射等外部依赖列出并给「去处理」定位入口；
// 无入口的依赖只列名称与原因（不丢失信息，也不伪造跳转）。
// S4（20260920 验收）：去处理时携带 returnTo（原共享定义 id + 名称），目标页显示「← 返回共享属性「x」」，
// 处理完依赖可一步跳回本页的引用位置抽屉继续操作；不自动替用户删除。
const externalDeps = computed(() => usageTarget.value ? externalDependencies(props.state, usageTarget.value['@id']) : [])
function goExternal(dep: any) {
  const target = externalDependencyTarget(dep)
  if (!target) return
  const s = usageTarget.value
  const returnTo = s ? { view: 'library', focus: { definition: s['@id'] }, label: '共享属性「' + (s['rdfs:label'] || '未命名共享属性') + '」' } : undefined
  usagesId.value = ''
  emit('navigate', target.view, returnTo ? { ...target.focus, returnTo } : target.focus)
}
function editFromDetail() { const id = detailId.value; detailId.value = ''; openEditor(id) }
// S4：对象属性引用行同样携带返回上下文——对象建模处理完可一步回到本共享定义继续操作。
function openRef(r: { domain: string; id: string }) {
  const label = '共享属性「' + (usageTarget.value?.['rdfs:label'] || '未命名共享属性') + '」'
  usagesId.value = ''
  emit('navigate', 'objects', { type: r.domain, property: r.id,
    returnTo: { view: 'library', focus: { definition: usageTarget.value?.['@id'] || '' }, label } })
}

// ── 页头更多操作：粘贴多行 / 批量复用（功能不删，入口从页面底部 details 收进菜单）──
const pageMenuItems = [{ id: 'paste', label: '粘贴多行属性到对象' }, { id: 'batch', label: '批量复用对象属性' }]
function onPageMenu(id: string) { openDialog(id) }

function openDialog(name: string, s: any = null) { dialog.value = name; acting.value = s; targets.value = []; copyTarget.value = ''; pasted.value = ''; batchProps.value = []; batchTargets.value = []; feedback.value = '' }

// ── 引用到对象：形态一致性检查 + addReference（同 label / 已有引用则跳过并提示） ──
function referenceMany() {
  const s = acting.value
  if (!s || !targets.value.length) return
  let ok = 0, skip = 0
  const blocked: string[] = []
  mutate(() => {
    for (const t of targets.value) {
      if (shapeConflict(graph.value, s, t)) { blocked.push(typeName(t)); continue }
      if (addReference(graph.value, s, t)) ok++; else skip++
    }
  }, '引用共享属性「' + (s['rdfs:label'] || '') + '」到 ' + ok + ' 个对象', { kind: 'sharedProperty', id: s['@id'] })
  dialog.value = ''
  let msg = `已引用到 ${ok} 个对象`
  if (skip) msg += `，跳过 ${skip} 个（已有同名属性或引用）`
  if (blocked.length) msg += `；${blocked.join('、')}：单值与时间序列不能引用同一份完整共享定义`
  feedback.value = msg
}

// ── 复制为私有：目标对象已有同名属性则提示，否则生成带完整元数据的新本地属性 ──
function copyOne() {
  const s = acting.value, t = copyTarget.value
  if (!s || !t) return
  const dup = localProperties(graph.value, t).find((p: any) => effectiveProperty(p, graph.value)['rdfs:label'] === (s['rdfs:label'] || ''))
  if (dup) { dialog.value = ''; feedback.value = `${typeName(t)} 已有同名属性「${s['rdfs:label']}」，为避免重复未复制；如需统一维护请改用「引用到对象」。`; return }
  mutate(() => copyAsPrivate(s, graph.value, t), '复制共享属性「' + (s['rdfs:label'] || '') + '」为 ' + typeName(t) + ' 的私有属性', { kind: 'sharedProperty', id: String(s['@id'] || ''), ownerId: t })
  dialog.value = ''; copyTarget.value = ''
  feedback.value = `已在 ${typeName(t)} 创建私有副本（含完整元数据与新属性标识），此后可独立修改，不再跟随共享定义。`
}

// ── 行内操作（20260919 按用户要求从「⋯」更多菜单平铺到行内）：引用到对象/复制为私有仍走原有弹窗，删除沿用保护语义 ──

// ── 删除定义（20260920 需求 12/13 统一语义）：有对象属性引用或契约/接口/项目映射引用 → 阻断并列出名称与定位；
// 无引用 → 确认后删除。不再自动私有化：想保留内容请用对象属性行的「转为私有」再删除。 ──
async function removeShared(s: any) {
  if (!s) return
  const check = sharedDeleteCheck(props.state, s['@id'])
  if (check.blocked) {
    feedback.value = check.message
    usagesId.value = s['@id']  // 定位入口：引用位置抽屉（可跳回对象属性）
    return
  }
  if (!(await appConfirm({ message: `删除共享定义「${s['rdfs:label'] || '未命名'}」？当前没有任何对象引用此定义；删除后需重新创建。可通过顶部撤销恢复。`, danger: true }))) return
  mutate(() => {
    props.state.ontology['@graph'] = graph.value.filter((n: any) => n['@id'] !== s['@id'])
  }, '删除共享属性「' + (s['rdfs:label'] || '未命名') + '」', { kind: 'sharedProperty', id: s['@id'] })
  feedback.value = '已删除共享定义。'
}

// ── 更多操作 · 粘贴多行（原对象属性页能力移入，功能不删） ──
const typeLabels: Record<string, string> = { string: '文本', double: '数值', decimal: '数值', integer: '数值', boolean: '是／否', date: '日期', dateTime: '日期', array: '数组', struct: '结构体' }
const importPreview = computed(() => { try { return { rows: parsePropertyRows(pasted.value), error: '' } } catch (e: any) { return { rows: [], error: e.message } } })
function importRows() {
  if (importPreview.value.error || !importPreview.value.rows.length || !pasteTarget.value) return
  let count = 0, skip = 0
  const t = pasteTarget.value
  mutate(() => {
    const names = new Set(localProperties(graph.value, t).map((p: any) => effectiveProperty(p, graph.value)['rdfs:label']))
    for (const row of importPreview.value.rows) {
      if (names.has(row.name)) { skip++; continue }
      graph.value.push(makeProperty(row, t)); names.add(row.name); count++
    }
  }, '粘贴多行属性到「' + typeName(t) + '」（' + count + ' 项）', { kind: 'sharedProperty', id: String(t || '') })
  dialog.value = ''; pasted.value = ''
  feedback.value = `已为 ${typeName(t)} 添加 ${count} 项属性${skip ? `，跳过 ${skip} 项同名属性` : ''}`
}

// ── 更多操作 · 批量复用：选源对象属性 → 转共享定义 → 引用到多个目标对象（原能力移入） ──
function distribute() {
  if (!batchSource.value || !batchProps.value.length || !batchTargets.value.length) return
  let count = 0, skip = 0
  mutate(() => {
    for (const pid of batchProps.value) {
      const p = graph.value.find((n: any) => n['@id'] === pid)
      if (!p) continue
      const s = asShared(p, graph.value)
      for (const t of batchTargets.value) { if (addReference(graph.value, s, t)) count++; else skip++ }
    }
  }, '批量复用 ' + batchProps.value.length + ' 项属性到 ' + batchTargets.value.length + ' 类对象', { kind: 'sharedProperty', id: String(batchSource.value || '') })
  dialog.value = ''
  feedback.value = `已新增 ${count} 个共享引用${skip ? `，跳过 ${skip} 个已有引用或同名属性` : ''}`
}
</script>

<template>
<!-- 编辑态：主内容整体替换为独立共享属性表单 -->
<PropertyManager v-if="editor" :key="editor.id || 'new'" :state="state" kind="shared" :property-id="editor.id" :canvas-return="canvasReturn" @back-to-graph="backToGraph" @close="editor = null" @saved="onSharedSaved"/>
<template v-else>
<!-- 页头（§3.2 资产库）：标题+一句说明在左，更多操作与唯一主入口在右 -->
<section class="ont-lib-head">
  <div>
    <h2>共享属性库</h2>
    <p>集中维护跨对象复用的属性定义：一处修改，所有引用同步。复制后独立维护；引用时沿用共享定义。各项目的取值实现按对象分别配置。</p>
  </div>
  <div class="ont-actions">
    <button v-if="canvasReturn" type="button" @click="backToGraph">← 返回图谱</button>
    <RowMenu :items="pageMenuItems" aria-label="页面更多操作" @pick="onPageMenu"/>
    <button type="button" class="primary" @click="openEditor('')">＋ 新建共享属性</button>
  </div>
</section>
<!-- 统一标准表格（§5/§6.5）：单个白色内容面板；工具栏/表头/空态/分页由 OntologyList 渲染 -->
<section class="card">
  <OntologyList
    :search="list.q.value" @update:search="list.q.value = $event"
    :total="list.filtered.value.length" :page="list.page.value" :page-count="list.pageCount.value"
    :sort-desc="list.dir.value < 0"
    ariaLabel="共享属性列表" search-placeholder="搜索名称或业务定义"
    :columns="[{ label: '名称', width: '36%', sort: true }, { label: '数据类型', width: '17%' }, { label: '引用情况', width: '15%' }, { label: '操作', width: '32%' }]"
    :empty-title="emptyTitle" :empty-hint="emptyHint"
    @sort="list.toggleSort()" @page="list.page.value += $event" @clear="list.q.value = ''">
    <template #filter>
      <div class="ont-filters" role="group" aria-label="按数据类型筛选">
        <button v-for="f in typeFilterKeys" :key="f" type="button" :class="{ active: typeFilter === f }" :aria-pressed="typeFilter === f" @click="setTypeFilter(f)">{{ f }}</button>
      </div>
    </template>
    <tr v-for="r in list.paged.value" :key="r.id" :data-lib-row="r.id" :class="{ 'lib-flash': highlightId === r.id }">
      <td>
        <button type="button" class="ont-name" @click="detailId = r.id">{{ r.label || '未命名共享属性' }}</button>
        <span class="ont-sub" :title="r.comment">{{ summary(r.raw) }}</span>
      </td>
      <td>
        <span class="ont-type">{{ r.typeMain }}</span>
        <span v-if="r.valueType" class="ont-sub">观测值：{{ r.valueType }}</span>
      </td>
      <td><button type="button" class="ont-badge" :aria-label="'查看引用位置 · ' + (r.label || '未命名共享属性')" @click="usagesId = r.id">{{ r.refs }} 处引用</button></td>
      <td class="ont-ops">
        <button type="button" class="row-link" @click="openEditor(r.id)">编辑</button>
        <button type="button" class="row-link" @click="openDialog('reference', r.raw)">引用到对象</button>
        <button type="button" class="row-link" @click="openDialog('copy', r.raw)">复制为私有</button>
        <button type="button" class="row-link danger" @click="removeShared(r.raw)">删除</button>
      </td>
    </tr>
  </OntologyList>
  <p v-if="definitions.length" class="ont-context">共 {{ definitions.length }} 项共享定义 · {{ totalUsages }} 处引用。引用属性的名称、类型、单位随共享定义统一维护；数据来源在项目映射中配置。</p>
</section>
<p v-if="feedback" :class="feedback.startsWith('已') ? 'inline-success' : 'inline-error'" role="status">{{ feedback }}</p>

<div v-if="dialog" class="modal-backdrop" @click.self="dialog = ''" @keydown.esc.stop="dialog = ''">
  <section class="modal-card sheet-dialog dialog-lg" role="dialog" aria-modal="true" :aria-label="dialog === 'reference' ? '引用共享属性到对象' : dialog === 'copy' ? '复制为私有属性' : dialog === 'paste' ? '粘贴多行属性' : '批量复用对象属性'">
    <div class="panelhead"><h2>{{ { reference: '引用共享属性到对象', copy: '复制为私有属性', paste: '粘贴多行属性到对象', batch: '批量复用对象属性' }[dialog] }}</h2><button aria-label="关闭" @click="dialog = ''">×</button></div>
    <template v-if="dialog === 'reference'">
      <p>把「{{ acting?.['rdfs:label'] }}」引用到以下对象；引用属性的名称、类型、单位跟随此定义统一维护。已有同名属性或引用的对象会跳过。</p>
      <label v-for="t in types" :key="t['@id']" class="check-option"><input v-model="targets" type="checkbox" :value="t['@id']"><span>{{ t['rdfs:label'] }}<small v-if="shapeConflict(graph, acting, t['@id'])" class="field-help">已有同名但形态不同的属性，无法引用</small></span></label>
      <p v-if="!types.length" class="field-help">还没有对象类型，请先到「对象建模」创建。</p>
      <button class="primary" :disabled="!targets.length || !types.length" @click="referenceMany">引用到 {{ targets.length }} 个对象</button>
    </template>
    <template v-else-if="dialog === 'copy'">
      <p>在目标对象上创建一份独立副本：包含完整的名称、类型、单位等元数据，使用新的属性标识，之后可单独修改，不再跟随共享定义。</p>
      <label>目标对象类型<AppSelect :model-value="copyTarget" :options="typeOptions" placeholder="选择对象类型" aria-label="选择目标对象类型" @update:model-value="copyTarget = $event"/></label>
      <button class="primary" :disabled="!copyTarget" @click="copyOne">创建私有副本</button>
    </template>
    <template v-else-if="dialog === 'paste'">
      <p>从 Excel 复制四列：名称、业务描述、类型、单位。支持列标题，目标对象已有同名属性会跳过。</p>
      <pre>SOC　荷电状态，70表示70%　数值　%</pre>
      <label>目标对象类型<AppSelect :model-value="pasteTarget" :options="typeOptions" placeholder="选择对象类型" aria-label="选择目标对象类型" @update:model-value="pasteTarget = $event"/></label>
      <textarea v-model="pasted" aria-label="粘贴属性表格" placeholder="请粘贴以制表符分列的内容"/>
      <p v-if="importPreview.error" class="inline-error" role="alert">{{ importPreview.error }}</p>
      <div v-if="importPreview.rows.length" class="scroll sheet-import-preview"><table><thead><tr><th>名称</th><th>业务描述</th><th>类型</th><th>单位</th></tr></thead><tbody><tr v-for="(r, i) in importPreview.rows" :key="i"><td>{{ r.name }}</td><td>{{ r.description }}</td><td>{{ typeLabels[r.type] }}</td><td>{{ r.suffix }}</td></tr></tbody></table></div>
      <button class="primary" :disabled="!!importPreview.error || !importPreview.rows.length || !pasteTarget" @click="importRows">添加 {{ importPreview.rows.length }} 项</button>
    </template>
    <template v-else>
      <p>选择源对象的属性转为共享定义，再引用到其他对象；已有引用或同名属性的目标会跳过。</p>
      <label>源对象类型<AppSelect :model-value="batchSource" :options="typeOptions" placeholder="选择源对象类型" aria-label="选择源对象类型" @update:model-value="batchSource = $event"/></label>
      <template v-if="batchSource">
        <div class="batch-panel">
          <strong>选择要复用的属性</strong>
          <label v-for="p in batchSourceProps" :key="p['@id']" class="check-option"><input v-model="batchProps" type="checkbox" :value="p['@id']"><span>{{ effectiveProperty(p, graph)['rdfs:label'] || '未命名' }}{{ p['mg:sharedProperty'] ? '（已是共享引用）' : '' }}</span></label>
        </div>
        <div class="batch-panel">
          <strong>引用到以下对象</strong>
          <label v-for="t in types.filter((x: any) => x['@id'] !== batchSource)" :key="t['@id']" class="check-option"><input v-model="batchTargets" type="checkbox" :value="t['@id']"><span>{{ t['rdfs:label'] }}</span></label>
        </div>
      </template>
      <button class="primary" :disabled="!batchSource || !batchProps.length || !batchTargets.length" @click="distribute">复用 {{ batchProps.length }} 项到 {{ batchTargets.length }} 类对象</button>
    </template>
  </section>
</div>

<!-- 只读详情抽屉（§7）：名称入口，不做任何保存；「编辑定义」进入原属性表单 -->
<OntDrawer v-if="detail" :title="detail['rdfs:label'] || '未命名共享属性'" subtitle="共享属性定义" @close="detailId = ''">
  <div class="ont-field"><span class="ont-field-label">业务定义</span><p>{{ detail['rdfs:comment'] || '暂无业务定义。' }}</p></div>
  <div class="ont-field"><span class="ont-field-label">数据类型</span><p>{{ propertyTypeLabel(detail, graph) }}</p></div>
  <div v-if="detail['mg:valueSuffix']" class="ont-field"><span class="ont-field-label">单位</span><p>{{ detail['mg:valueSuffix'] }}</p></div>
  <div class="ont-field"><span class="ont-field-label">引用情况</span><p>{{ usageCount(detail) }} 处引用</p></div>
  <p class="ont-hint">修改此定义后所有引用同步更新；需要独立维护请用行内「复制为私有」。</p>
  <template #footer><button type="button" class="primary" @click="editFromDetail">编辑定义</button></template>
</OntDrawer>
<!-- 引用位置抽屉（D05）：对象类型 + 属性 apiName，点击定位回对象属性行 -->
<OntDrawer v-if="usageTarget" :title="'引用位置 · ' + (usageTarget['rdfs:label'] || '未命名共享属性')" subtitle="引用情况" @close="usagesId = ''">
  <p v-if="!usageRows.length" class="ont-hint">当前没有共享引用；对象上复制的私有副本不跟随此定义更新。</p>
  <button v-for="r in usageRows" :key="r.id" type="button" class="ont-ref-row" @click="openRef(r)">
    <span><strong>{{ r.name }}</strong><small class="code-like">{{ r.api }}</small></span>→
  </button>
  <template v-if="externalDeps.length">
    <p class="inline-error">此定义还被以下内容直接引用，删除前需先处理：</p>
    <button v-for="(d, i) in externalDeps" :key="'ext' + i" type="button" class="ont-ref-row" :disabled="!externalDependencyTarget(d)" @click="goExternal(d)">
      <span><strong>{{ d.name }}</strong><small>{{ d.reason }}</small></span>
      <template v-if="externalDependencyTarget(d)">去处理 →</template><template v-else>—</template>
    </button>
  </template>
</OntDrawer>
</template>
</template>

<style scoped>
/* 结构样式走全局 .ont-*（style.css 本体列表段），这里只补本页细节。 */
.ont-filters{display:flex;gap:6px;flex-wrap:wrap}
.ont-filters button{font-size:12px;padding:4px 9px;border-radius:var(--r-pill)}
.ont-filters button.active{background:var(--blue-soft);border-color:var(--blue-line);color:var(--blue-ink);font-weight:600}
/* 保存/操作后的行定位高亮（data-lib-row 在 <tr> 上，locate 滚动定位） */
.ont-table tr.lib-flash td{background:var(--blue-soft)}
/* 引用位置抽屉：对象名下的属性 apiName 副标题 */
.ont-ref-row small{display:block;margin-top:2px;font-size:12px;color:var(--muted);overflow-wrap:anywhere}
.code-like{font-family:ui-monospace,SFMono-Regular,monospace}
.batch-panel{border-top:1px solid var(--line);margin-top:12px;padding-top:10px}
.batch-panel .check-option{font-size:13px}
</style>
