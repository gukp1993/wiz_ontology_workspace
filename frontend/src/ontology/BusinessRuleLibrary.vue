<!-- 业务规则库（20260918 本体列表统一设计 §6.6/§5/§7）：ont-lib-head 页头 + OntologyList 标准表格。
     列：名称（→只读详情抽屉）｜业务定义两行摘要｜引用对象（去重对象类型数徽标 → 引用对象抽屉）｜操作仅「编辑」。
     搜索匹配名称+业务定义；引用情况筛选（全部/有引用/无引用）；名称 zh-CN 排序 + 分页（useOntTable）——
     搜索/筛选/排序/分页只是视图，不触发保存、不产生撤销记录。名称承担查看，不重复放「查看」按钮；
     操作列 编辑｜删除：删除为 20260920 需求 12 的安全删除（有对象/契约依赖阻断并定位，
     无依赖确认后删除当前草稿定义；与图谱、对象页共用 ruleDeleteCheck 同一判断）。
     编辑为内嵌四字段表单（2026-09-20：由弹窗改为与动作/对象/属性页一致的内嵌布局；
     form-save 一次落盘、失败保留表单不假成功），编辑期间注册 T00 表单守卫；
     详情/引用对象为只读抽屉，Esc/遮罩/关闭均可退；无复制、分类、状态与执行。
     20260920 字段精简：编辑为三字段（规则名称/业务定义必填、规则内容选填），
     历史 output 有值时只读展示为「历史补充说明」；不自动迁移、不清空。 -->
<script setup lang="ts">
import { computed, inject, onBeforeUnmount, ref, watch } from 'vue'
import { appConfirm } from '../shared/appConfirm'
import OntologyList from '../shared/OntologyList.vue'
import OntDrawer from '../shared/OntDrawer.vue'
import Field from '../shared/EditorField.vue'
import EditorHead from '../shared/EditorHead.vue'
import { useOntTable } from './ontList'
import { objectsOfRule, RULE_FIELDS, RULE_LEGACY_FIELDS, rulesOf, ruleFieldErrors } from './businessRuleModel'
import { externalDependencies, externalDependencyTarget, ruleDeleteCheck } from './dependencyModel'
import type { FormGuardAPI, FormSaveAPI } from '../app/formGuard'

// canvasReturn（20260919 图谱优化）：非空表示从本体图谱「打开定义」跳转而来，页头显示「返回图谱」（前端可选上下文）。
// returnTo（S4，20260920 验收）：从资产库外部依赖「去处理」跳转而来时的来源定义上下文（App 分发）。
const props = defineProps<{ state: any; focusId?: string; focusOrigin?: { type?: string; tab?: string }; canvasReturn?: string; editFocus?: boolean; returnTo?: { view: string; focus?: Record<string, any>; label: string } }>(), emit = defineEmits(['before-change', 'changed', 'navigate'])
function backToGraph() { emit('navigate', 'objects', { graph: true, graphFocus: props.canvasReturn || '' } as any) }
// S4：返回来源定义（共享属性等）继续操作；openUsages 让共享库回到引用位置抽屉。
function backToSource() { if (props.returnTo) emit('navigate', props.returnTo.view, { ...(props.returnTo.focus || {}), openUsages: true } as any) }
const guardApi = inject<FormGuardAPI>('form-guard')!
const formSave = inject<FormSaveAPI>('form-save')!

const message = ref(''), saving = ref(false)
// dialog: null | {kind:'detail', id} | {kind:'edit', isNew} | {kind:'owners', id}
const dialog = ref<{ kind: 'detail' | 'owners'; id: string } | null>(null)
// 编辑改为内嵌表单（2026-09-20 用户要求，与动作/对象/属性页风格一致）
const mode = ref<'list' | 'edit'>('list')
const editId = ref('')
const isNewRule = ref(false)
const draft = ref<any>(null)
let baseline = ''
const fieldErrors = ref<Record<string, string>>({})

// 内嵌表单的字段展示元信息（与动作/对象页 Field 风格一致）
const FIELD_LABEL: Record<string, string> = Object.fromEntries(RULE_FIELDS.map(([k, label]) => [k, label]))
const FIELD_HELP: Record<string, string> = {
  description: '这条规则解决什么业务问题、适用范围；范围与例外也写在这里。',
  content: '规则内容（选填）：计算公式、口径、参数等，逐条填写。',
}
const FIELD_EXAMPLE: Record<string, string> = {
  name: '例如：储能SOC计算规则',
  description: '例如：按统一统计范围计算储能设备剩余电量占比。',
  content: '例如：soc = 剩余电量 / 额定容量 × 100。',
}
// 历史 output（20260920 字段精简）：有值时只读展示为历史补充说明；不参与编辑与必填。
// 用独立 ref 承载（draft 只含三字段），避免被 payload 提交或影响 dirty 判定。
const legacyOutputSource = ref('')
const legacyOutput = computed(() => legacyOutputSource.value.trim())
const LEGACY_LABEL = RULE_LEGACY_FIELDS[0][1]

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
// S4（20260920 验收）：阻断删除的规则若还被契约/接口/动作/映射等直接引用，在同一抽屉列出「去处理」定位
//（对象引用在下方已有入口；这里只补外部依赖，无入口的依赖只列名称与原因，不伪造跳转）。
const ownersExternalDeps = computed(() => ownersRule.value ? externalDependencies(props.state, ownersRule.value.id).filter((d: any) => externalDependencyTarget(d)) : [])
function goExternal(dep: any) {
  const target = externalDependencyTarget(dep)
  if (!target) return
  const rule = ownersRule.value
  const returnTo = rule ? { view: 'rules', focus: { definition: rule.id }, label: '规则「' + (rule.name || '未命名规则') + '」' } : undefined
  dialog.value = null
  emit('navigate', target.view, returnTo ? { ...target.focus, returnTo } : target.focus)
}
const _editTargetName = computed(() => draft.value?.name || '')

watch(rows, v => {
  if (dialog.value && !v.some((r: any) => r.id === dialog.value!.id)) dialog.value = null
  if (mode.value === 'edit' && editId.value && !v.some((r: any) => r.id === editId.value) && !isNewRule.value) mode.value = 'list'
}, { immediate: true })
// 图谱「编辑」跳转（editFocus）：直接进编辑弹窗；否则按既有行为开只读详情抽屉。
watch(() => props.focusId, id => {
  if (!id || !rows.value.some((r: any) => r.id === id)) return
  list.q.value = ''; refFilter.value = '全部'
  if (props.editFocus) openEdit(id); else openDetail(id)
}, { immediate: true })

const dirty = computed(() => mode.value === 'edit' && JSON.stringify(draft.value) !== baseline)
const guard = { isDirty: () => dirty.value, discard: () => closeEditor() }
watch(mode, m => { if (m === 'edit') guardApi.register(guard); else guardApi.unregister(guard) })
onBeforeUnmount(() => guardApi.unregister(guard))

function openDetail(id: string) { dialog.value = { kind: 'detail', id }; message.value = '' }
function openOwners(id: string) { dialog.value = { kind: 'owners', id }; message.value = '' }
function openEdit(id = '') {
  const rule: any = id ? rows.value.find((r: any) => r.id === id) : null
  if (id && !rule) return
  // draft 只承载三字段；历史 output 单独只读展示（legacyOutput），不进 draft、不参与 dirty 比较。
  // A01（20260920 验收修复）：文本原样、非文本旧值保留原值（由 validate 报「必须是文本」），
  // 不再 `rule[k] || ''` 掩盖或直接 trim 崩溃。
  draft.value = rule
    ? Object.fromEntries(RULE_FIELDS.map(([k]) => [k, rule[k] ?? '']))
    : Object.fromEntries(RULE_FIELDS.map(([k]) => [k, '']))
  legacyOutputSource.value = typeof rule?.output === 'string' ? rule.output : ''
  baseline = JSON.stringify(draft.value)
  fieldErrors.value = {}
  isNewRule.value = !id
  editId.value = id || 'rule_' + crypto.randomUUID().replaceAll('-', '')
  mode.value = 'edit'
  dialog.value = null
  message.value = ''
}
function closeEditor() { mode.value = 'list'; draft.value = null }
function onField(key: string, value: string) { if (draft.value) draft.value[key] = value }
async function cancelEdit() {
  if (dirty.value && !(await appConfirm({ message: '放弃尚未保存的修改？' }))) return
  closeEditor()
}
// 保存后回到列表并定位该行
function locate(id: string) {
  if (!id) return
  let idx = list.filtered.value.findIndex((r: any) => r.id === id)
  if (idx < 0) { list.q.value = ''; refFilter.value = '全部'; idx = list.filtered.value.findIndex((r: any) => r.id === id) }
  if (idx >= 0) list.page.value = Math.floor(idx / 20) + 1
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
function validate(): boolean {
  // 20260920 字段精简 + A01 验收修复：名称/业务定义必填文本，规则内容选填文本。
  // 口径在 ruleFieldErrors（与后端 _text_field_errors 镜像）；非文本旧值给字段级原因，
  // 不 String() 掩盖、不自动清洗。
  fieldErrors.value = ruleFieldErrors(draft.value)
  return !Object.keys(fieldErrors.value).length
}
async function saveEdit() {
  if (saving.value || !draft.value) return
  if (!validate()) {
    // 明确报错（R02）：EditorField 的 touched 提示只在失焦后出现，点保存必须给出可见原因。
    message.value = Object.values(fieldErrors.value).join('；') || '请检查必填项。'
    return
  }
  saving.value = true
  const targetId = editId.value
  const name = draft.value.name.trim()
  // 只提交三字段；历史 output 不进入 payload —— 保存时对既有记录原地保留（见下方 mutate）。
  const payload = Object.fromEntries(RULE_FIELDS.map(([k]) => [k, String(draft.value[k] ?? '').trim()]))
  const list0 = props.state.workflow.businessRules = Array.isArray(props.state.workflow.businessRules) ? props.state.workflow.businessRules : []
  const existed = list0.some((x: any) => x === Object(x) && x.id === targetId)
  const r = await formSave.submitForm('ontology', () => {
    const hit = list0.find((x: any) => x === Object(x) && x.id === targetId)
    // 已存在记录：只覆盖三字段，历史 output 与未知扩展原地保留（零丢失，不自动迁移）。
    if (hit) Object.assign(hit, payload)
    else list0.push({ id: targetId, ...payload })
    void name
  }, { actionLabel: (existed ? '修改规则「' : '新建规则「') + name + '」', target: { kind: 'rule', id: targetId } })
  saving.value = false
  if (!r.ok) { message.value = r.message; return }
  const savedId = targetId
  mode.value = 'list'
  draft.value = null
  message.value = '规则已保存到本体草稿。'
  // 来自图谱的「编辑」跳转：保存成功直接回画布并定位原节点（图谱编辑闭环，2026-09-20）
  if (props.canvasReturn) { backToGraph(); return }
  locate(savedId)
}
function goObject(objectTypeId: string) {
  dialog.value = null
  emit('navigate', 'objects', { type: objectTypeId, tab: 'rules' })
}

// ── 安全删除（20260920 需求 12/13）：与图谱/对象页共用同一依赖判断（ruleDeleteCheck）。
// 有对象引用或契约/接口等直接依赖 → 阻断：列出业务名称并打开「引用对象」抽屉作为定位入口；
// 无依赖 → 确认后从当前草稿删除（撤销可恢复；不动已发布版本）。不得提供强制删除。 ──
async function removeRule(id: string) {
  const rule: any = rows.value.find((r: any) => r.id === id)
  if (!rule) return
  const check = ruleDeleteCheck(props.state, id)
  if (check.blocked) {
    message.value = check.message
    dialog.value = { kind: 'owners', id }
    return
  }
  if (!(await appConfirm({ message: `删除规则「${rule.name || id}」？当前没有对象引用此规则；删除的是当前草稿定义，已发布版本不变。可通过顶部撤销恢复。`, danger: true }))) return
  const list0 = props.state.workflow.businessRules as any[]
  const r = await formSave.submitForm('ontology', () => {
    const idx = list0.indexOf(rule)
    if (idx >= 0) list0.splice(idx, 1)
  }, { actionLabel: '删除规则「' + (rule.name || id) + '」', target: { kind: 'rule', id } })
  if (!r.ok) { message.value = r.message; return }
  message.value = '已删除规则定义。'
}
</script>
<template>
<section v-if="mode === 'list'" class="card ont-block">
  <div class="ont-lib-head">
    <div>
      <h2>业务规则</h2>
      <p>用自然语言维护通用业务规则；对象建模中引用，项目实现与执行不在本期范围。</p>
    </div>
    <div class="ont-actions">
      <button v-if="returnTo" type="button" @click="backToSource">← 返回{{ returnTo.label }}</button>
      <button v-if="canvasReturn" type="button" @click="backToGraph">← 返回图谱</button>
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
      <td class="ont-ops"><button type="button" class="row-link" @click="openEdit(row.id)">编辑</button><button type="button" class="row-link danger" @click="removeRule(row.id)">删除</button></td>
    </tr>
  </OntologyList>
  <p v-if="message" :class="message.startsWith('规则已保存') ? 'inline-success' : 'inline-error'" role="alert" style="margin-top:12px">{{ message }}</p>
</section>

<!-- 名称 → 只读详情抽屉（§7）：完整定义展示，不做保存；编辑走原四字段表单。 -->
<OntDrawer v-if="detailRule" :title="detailRule.name || '未命名规则'" subtitle="业务规则详情" @close="dialog = null">
  <div class="ont-field"><span class="ont-field-label">业务定义</span><p>{{ detailRule.description || '暂无业务定义。' }}</p></div>
  <div class="ont-field"><span class="ont-field-label">规则内容</span><p>{{ detailRule.content || '未填写' }}</p></div>
  <!-- 历史 output（20260920 字段精简）：有值才显示，只读补充说明，不要求用户迁移。 -->
  <div v-if="String(detailRule.output || '').trim()" class="ont-field"><span class="ont-field-label">历史补充说明（原输出结果）</span><p>{{ detailRule.output }}</p></div>
  <template #footer>
    <button v-if="returnTo" type="button" @click="backToSource">← 返回{{ returnTo.label }}</button>
    <button v-if="focusOrigin?.type" type="button" @click="backToOrigin">返回来源对象</button>
    <button type="button" class="primary" @click="editFromDrawer">编辑</button>
  </template>
</OntDrawer>

<!-- 引用对象抽屉（原 owners 弹窗）：反查引用对象类型并可跳到对象建模「规则」页签；
     S4（20260920 验收）：同时列出契约/接口/动作/映射等外部依赖的「去处理」入口，
     跳转携带 returnTo，处理完依赖可一步返回本规则继续删除。 -->
<OntDrawer v-if="ownersRule" :title="ownersRule.name || '未命名规则'" subtitle="引用对象" @close="dialog = null">
  <p v-if="!ownersNames.length" class="muted owners-empty">暂无对象引用此规则。</p>
  <template v-else>
    <button v-for="t in ownersNames" :key="t" type="button" class="ont-ref-row" @click="goObject(t)">
      <span><strong>{{ typeName(t) }}</strong></span>
      <span class="ont-ref-go">查看对象规则 →</span>
    </button>
  </template>
  <template v-if="ownersExternalDeps.length">
    <p class="inline-error">此规则还被以下内容直接引用；处理完后可返回本规则继续删除：</p>
    <button v-for="(d, i) in ownersExternalDeps" :key="'ext' + i" type="button" class="ont-ref-row" @click="goExternal(d)">
      <span><strong>{{ d.name }}</strong><small>{{ d.reason }}</small></span>
      <span class="ont-ref-go">去处理 →</span>
    </button>
  </template>
  <p class="ont-hint">引用关系在对象建模的「规则」页签中维护；这里只读反查。处理依赖期间不会自动解除任何引用，也不会替用户删除规则。</p>
</OntDrawer>


<!-- 编辑/新建：内嵌完整表单（2026-09-20 用户要求，与动作/对象/属性页一致，不再用弹窗）。
     显式条件而非 v-else：中间插入了抽屉组件，v-else 会错误链到抽屉上导致列表态也渲染表单。
     头部返回控件走共享 EditorHead（2026-09-20 用户反馈「规则缺少返回图谱」）：
     从图谱跳入时在此给出「← 返回图谱」，与对象/属性/动作编辑表单同一位置同一写法。 -->
<section v-if="mode === 'edit'" class="card detail-card">
  <EditorHead :canvas-return="canvasReturn" back-label="← 返回规则列表" @back-to-graph="backToGraph" @close="cancelEdit"/>
  <div class="detail-heading">
    <div><span class="eyebrow">{{ isNewRule ? '新建规则' : '编辑规则' }}</span><h2>{{ draft?.name || '未命名规则' }}</h2></div>
    <span class="status-pill">业务规则</span>
  </div>
  <div class="form-grid">
    <Field v-for="[key] in RULE_FIELDS" :key="key" :label="FIELD_LABEL[key]"
      :class="key === 'name' ? 'full' : 'full'" :required="key !== 'content'"
      :type="key === 'name' ? 'text' : 'textarea'" :rows="key === 'content' ? 8 : 4"
      :model-value="draft?.[key] || ''"
      :help="FIELD_HELP[key] || ''" :example="FIELD_EXAMPLE[key] || ''"
      @update:model-value="onField(key, $event)"/>
    <!-- 历史 output 只读区：有值时展示，提示可复制到规则内容后自行编辑；不参与保存。 -->
    <div v-if="legacyOutput" class="rule-legacy full">
      <p class="rule-legacy-label">{{ LEGACY_LABEL }}</p>
      <p class="rule-legacy-value">{{ legacyOutput }}</p>
      <small class="muted">历史数据保留展示，不参与校验与保存；如需调整可复制到上方「规则内容」后编辑。</small>
    </div>
  </div>
  <p v-if="message" class="inline-error" role="alert">{{ message }}</p>
  <div class="detail-footer">
    <div class="tools"><button type="button" class="primary" :disabled="saving" @click="saveEdit">{{ saving ? '保存中…' : '保存定义' }}</button><button type="button" @click="cancelEdit">取消</button></div>
    <span>保存直接写入本体草稿；已发布版本需重新发布后更新。</span>
  </div>
</section>
</template>

<style scoped>
/* 引用情况筛选：紧凑分段按钮（§4；.ont-filters 全局未提供，沿用对象建模页签内同款局部样式）。 */
.ont-filters{display:flex;gap:6px;flex-wrap:wrap}
.ont-filters button{font-size:12px;padding:4px 9px;border-radius:var(--r-pill)}
.ont-filters button.active{background:var(--blue-soft);border-color:var(--blue-line);color:var(--blue-ink);font-weight:600}
/* 引用对象抽屉行：名称居左、动作居右（骨架走全局 .ont-ref-row）。 */
.ont-ref-go{flex:none;margin-left:auto;color:var(--blue);font-size:12px;font-weight:500}
/* 外部依赖行（S4）：名称下的原因副标题 */
.ont-ref-row small{display:block;margin-top:2px;font-size:12px;color:var(--muted);overflow-wrap:anywhere}
.owners-empty{padding:18px 0}
/* 历史输出只读区（20260920）：浅底块，与可编辑字段视觉区分 */
.rule-legacy{background:var(--paper-2);border:1px solid var(--line);border-radius:var(--r-sm);padding:10px 12px;margin-top:4px}
.rule-legacy-label{margin:0 0 4px;font-size:12px;font-weight:600;color:var(--muted)}
.rule-legacy-value{margin:0 0 4px;white-space:pre-wrap;overflow-wrap:anywhere}
</style>
