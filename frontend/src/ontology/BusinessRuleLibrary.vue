<!-- 业务规则库（20260918 本体列表统一设计 §6.6/§5/§7）：ont-lib-head 页头 + OntologyList 标准表格。
     列：名称（→只读详情抽屉）｜业务定义两行摘要｜引用对象（去重对象类型数徽标 → 引用对象抽屉）｜操作仅「编辑」。
     搜索匹配名称+业务定义；引用情况筛选（全部/有引用/无引用）；名称 zh-CN 排序 + 分页（useOntTable）——
     搜索/筛选/排序/分页只是视图，不触发保存、不产生撤销记录。名称承担查看，不重复放「查看」按钮；
     不显示更多菜单，不新增删除/复制（与一期业务能力一致）。
     编辑仍是四字段 BusinessRuleDialog（校验/saving、form-save 一次落盘、失败保留表单不假成功），
     编辑期间注册 T00 表单守卫；详情/引用对象为只读抽屉，Esc/遮罩/关闭均可退；一期无删除、复制、分类、状态、依赖与执行。 -->
<script setup lang="ts">
import { computed, inject, onBeforeUnmount, ref, watch } from 'vue'
import { appConfirm } from '../shared/appConfirm'
import OntologyList from '../shared/OntologyList.vue'
import OntDrawer from '../shared/OntDrawer.vue'
import BusinessRuleDialog from './BusinessRuleDialog.vue'
import { useOntTable } from './ontList'
import { objectsOfRule, RULE_FIELDS, rulesOf } from './businessRuleModel'
import type { FormGuardAPI, FormSaveAPI } from '../app/formGuard'

const props = defineProps<{ state: any; focusId?: string; focusOrigin?: { type?: string; tab?: string } }>(), emit = defineEmits(['before-change', 'changed', 'navigate'])
const guardApi = inject<FormGuardAPI>('form-guard')!
const formSave = inject<FormSaveAPI>('form-save')!

const message = ref(''), saving = ref(false)
// dialog: null | {kind:'detail', id} | {kind:'edit', isNew} | {kind:'owners', id}
const dialog = ref<{ kind: 'detail' | 'edit' | 'owners'; id: string; isNew?: boolean } | null>(null)
const draft = ref<any>(null)
let baseline = ''
const fieldErrors = ref<Record<string, string>>({})

const graph = computed(() => props.state?.ontology?.['@graph'] || [])
const typeName = (id: string) => { const n: any = graph.value.find(x => x['@id'] === id || x['@id'] === 'mg:' + id.replace(/^mg:/, '')); return n?.['rdfs:label'] || id }
const rows = computed(() => rulesOf(props.state))

// 标准表格视图（§5/§6.6）：搜索（名称+业务定义）与引用情况筛选在【全量记录】上执行，再分页；排序 zh-CN。
// 引用对象数 = objectsOfRule 去重对象类型数（写回已按组合去重，这里再兜底防御悬空数据）。
const refFilter = ref('全部')
const dedupeTypes = (ids: string[]) => [...new Set(ids)]
const tableRows = computed(() => rows.value.map((r: any) => {
  const refs = dedupeTypes(objectsOfRule(props.state, r.id))
  return { id: r.id, name: r.name || '未命名规则', desc: r.description || '', refs }
}))
const filterSource = computed(() => refFilter.value === '有引用' ? tableRows.value.filter(r => r.refs.length > 0)
  : refFilter.value === '无引用' ? tableRows.value.filter(r => !r.refs.length) : tableRows.value)
const list = useOntTable(() => filterSource.value, {
  match: (r, q) => (r.name + ' ' + r.desc).toLowerCase().includes(q),
  sort: (a, b) => a.name.localeCompare(b.name, 'zh-CN', { numeric: true }),
})
const hasViewFilter = computed(() => !!list.q.value || refFilter.value !== '全部')

const currentRule = computed(() => rows.value.find((r: any) => r.id === dialog.value?.id) || null)
const detailRule = computed(() => dialog.value?.kind === 'detail' ? currentRule.value : null)
const ownersRule = computed(() => dialog.value?.kind === 'owners' ? currentRule.value : null)
const ownersNames = computed(() => dialog.value?.id ? dedupeTypes(objectsOfRule(props.state, dialog.value.id)) : [])
const editTargetName = computed(() => draft.value?.name || '')

watch(rows, v => { if (dialog.value && !v.some((r: any) => r.id === dialog.value!.id)) dialog.value = null }, { immediate: true })
watch(() => props.focusId, id => { if (id && rows.value.some((r: any) => r.id === id)) { list.q.value = ''; refFilter.value = '全部'; openDetail(id) } }, { immediate: true })

const dirty = computed(() => dialog.value?.kind === 'edit' && JSON.stringify(draft.value) !== baseline)
const guard = { isDirty: () => dirty.value, discard: () => closeDialog() }
watch(() => dialog.value?.kind === 'edit', open => { open ? guardApi.register(guard) : guardApi.unregister(guard) })
onBeforeUnmount(() => guardApi.unregister(guard))

function openDetail(id: string) { dialog.value = { kind: 'detail', id }; message.value = '' }
function openOwners(id: string) { dialog.value = { kind: 'owners', id }; message.value = '' }
function openEdit(id = '') {
  const rule: any = id ? rows.value.find((r: any) => r.id === id) : null
  if (id && !rule) return
  draft.value = rule
    ? Object.fromEntries(RULE_FIELDS.map(([k]) => [k, rule[k] || '']))
    : Object.fromEntries(RULE_FIELDS.map(([k]) => [k, '']))
  baseline = JSON.stringify(draft.value)
  fieldErrors.value = {}
  dialog.value = { kind: 'edit', id: id || 'rule_' + crypto.randomUUID().replaceAll('-', ''), isNew: !id }
}
// 详情抽屉「编辑」：换入编辑弹窗即关抽屉（同一 dialog 状态机）。
function editFromDrawer() { if (detailRule.value) openEdit(detailRule.value.id) }
// 从来源对象跳入时提供返回路径（§7）：关抽屉后回对象建模「规则」页签。
function backToOrigin() {
  const type = props.focusOrigin?.type
  if (!type) return
  dialog.value = null
  emit('navigate', 'objects', { type, tab: 'rules' })
}
async function closeDialog() {
  if (dialog.value?.kind === 'edit' && dirty.value && !(await appConfirm({ message: '放弃尚未保存的修改？' }))) return
  dialog.value = null
  draft.value = null
}
function onField(key: string, value: string) { if (draft.value) draft.value[key] = value }
function validate(): boolean {
  const errors: Record<string, string> = {}
  for (const [key, label] of RULE_FIELDS) if (!String(draft.value[key] || '').trim()) errors[key] = `请填写${label}`
  fieldErrors.value = errors
  return !Object.keys(errors).length
}
async function saveEdit() {
  if (saving.value || !draft.value) return
  if (!validate()) return
  saving.value = true
  const target = dialog.value!
  const name = draft.value.name.trim()
  const payload = Object.fromEntries(RULE_FIELDS.map(([k]) => [k, draft.value[k].trim()]))
  const list0 = props.state.workflow.businessRules = Array.isArray(props.state.workflow.businessRules) ? props.state.workflow.businessRules : []
  const existed = list0.some((x: any) => x === Object(x) && x.id === target.id)
  const r = await formSave.submitForm('ontology', () => {
    const hit = list0.find((x: any) => x === Object(x) && x.id === target.id)
    if (hit) Object.assign(hit, payload)
    else list0.push({ id: target.id, ...payload })
    void name
  }, { actionLabel: (existed ? '修改规则「' : '新建规则「') + name + '」', target: { kind: 'rule', id: target.id } })
  saving.value = false
  if (!r.ok) { message.value = r.message; return }
  dialog.value = null
  draft.value = null
  message.value = '规则已保存到本体草稿。'
}
function goObject(objectTypeId: string) {
  dialog.value = null
  emit('navigate', 'objects', { type: objectTypeId, tab: 'rules' })
}
</script>
<template>
<section class="card ont-block">
  <div class="ont-lib-head">
    <div>
      <h2>业务规则</h2>
      <p>用自然语言维护通用业务规则；对象建模中引用，项目实现与执行不在本期范围。</p>
    </div>
    <div class="ont-actions">
      <button type="button" class="primary" @click="openEdit()">＋ 新建规则</button>
    </div>
  </div>
  <OntologyList
    :search="list.q.value" @update:search="list.q.value = $event"
    :total="list.filtered.value.length" :page="list.page.value" :page-count="list.pageCount.value"
    :sort-desc="list.dir.value < 0"
    ariaLabel="业务规则列表" search-placeholder="搜索规则名称或业务定义"
    :columns="[{ label: '名称', width: '37%', sort: true }, { label: '业务定义', width: '29%' }, { label: '引用对象', width: '17%' }, { label: '操作', width: '17%' }]"
    :empty-title="hasViewFilter ? '没有匹配的规则' : '还没有业务规则'"
    :empty-hint="hasViewFilter ? '调整关键词或筛选条件再试试。' : '点击右上角「＋ 新建规则」创建第一条业务规则。'"
    @sort="list.toggleSort()" @page="list.page.value += $event" @clear="list.q.value = ''">
    <template #filter>
      <div class="ont-filters" role="group" aria-label="按引用情况筛选">
        <button v-for="f in ['全部', '有引用', '无引用']" :key="f" type="button"
          :class="{ active: refFilter === f }" :aria-pressed="refFilter === f"
          @click="refFilter = f; list.page.value = 1">{{ f }}</button>
      </div>
    </template>
    <tr v-for="row in list.paged.value" :key="row.id">
      <td>
        <button type="button" class="ont-name" @click="openDetail(row.id)">{{ row.name }}</button>
      </td>
      <td><span class="ont-clip" :title="row.desc">{{ row.desc || '—' }}</span></td>
      <td>
        <button type="button" class="ont-badge"
          :title="row.refs.length ? '查看引用「' + row.name + '」的对象类型' : '暂无对象引用此规则'"
          @click="openOwners(row.id)">{{ row.refs.length ? row.refs.length + ' 个对象' : '暂无引用' }}</button>
      </td>
      <td class="ont-ops"><button type="button" class="row-link" @click="openEdit(row.id)">编辑</button></td>
    </tr>
  </OntologyList>
  <p v-if="message" :class="message.startsWith('规则已保存') ? 'inline-success' : 'inline-error'" role="alert" style="margin-top:12px">{{ message }}</p>
</section>

<!-- 名称 → 只读详情抽屉（§7）：完整定义展示，不做保存；编辑走原四字段表单。 -->
<OntDrawer v-if="detailRule" :title="detailRule.name || '未命名规则'" subtitle="业务规则详情" @close="dialog = null">
  <div class="ont-field"><span class="ont-field-label">业务定义</span><p>{{ detailRule.description || '暂无业务定义。' }}</p></div>
  <div class="ont-field"><span class="ont-field-label">规则内容</span><p>{{ detailRule.content || '暂无规则内容。' }}</p></div>
  <div class="ont-field"><span class="ont-field-label">输出结果</span><p>{{ detailRule.output || '暂无输出说明。' }}</p></div>
  <template #footer>
    <button v-if="focusOrigin?.type" type="button" @click="backToOrigin">返回来源对象</button>
    <button type="button" class="primary" @click="editFromDrawer">编辑</button>
  </template>
</OntDrawer>

<!-- 引用对象抽屉（原 owners 弹窗）：反查引用对象类型并可跳到对象建模「规则」页签。 -->
<OntDrawer v-if="ownersRule" :title="ownersRule.name || '未命名规则'" subtitle="引用对象" @close="dialog = null">
  <p v-if="!ownersNames.length" class="muted owners-empty">暂无对象引用此规则。</p>
  <template v-else>
    <button v-for="t in ownersNames" :key="t" type="button" class="ont-ref-row" @click="goObject(t)">
      <span><strong>{{ typeName(t) }}</strong></span>
      <span class="ont-ref-go">查看对象规则 →</span>
    </button>
  </template>
  <p class="ont-hint">引用关系在对象建模的「规则」页签中维护；这里只读反查。</p>
</OntDrawer>

<BusinessRuleDialog v-if="dialog?.kind === 'edit'" mode="edit" :rule="draft" :is-new="dialog.isNew" :saving="saving" :errors="fieldErrors" @close="closeDialog" @field="onField" @save="saveEdit"/>
</template>

<style scoped>
/* 引用情况筛选：紧凑分段按钮（§4；.ont-filters 全局未提供，沿用对象建模页签内同款局部样式）。 */
.ont-filters{display:flex;gap:6px;flex-wrap:wrap}
.ont-filters button{font-size:12px;padding:4px 9px;border-radius:var(--r-pill)}
.ont-filters button.active{background:var(--blue-soft);border-color:var(--blue-line);color:var(--blue-ink);font-weight:600}
/* 引用对象抽屉行：名称居左、动作居右（骨架走全局 .ont-ref-row）。 */
.ont-ref-go{flex:none;margin-left:auto;color:var(--blue);font-size:12px;font-weight:500}
.owners-empty{padding:18px 0}
</style>
