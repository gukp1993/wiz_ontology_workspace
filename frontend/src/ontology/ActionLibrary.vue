<!-- 动作库（20260917 需求 §3.1；20260918 列表统一设计 §6.7/§5/§7/§8 改版）：
     取消左列表/右固定详情布局，改为全宽标准表格（OntologyList）＋名称点击只读详情抽屉
     ＋关联对象抽屉（OntDrawer）。新格式动作（definitionVersion 2）只有名称/业务定义/业务效果
     三个必填业务字段；不选对象、不配输入参数/提交条件/审批/验收。历史动作只读保留（旧字段
     不丢失、不自动转换），编辑入口显式确认后转换为新格式。编辑/删除走 T00：form-guard
     离开保护 + form-save.submitForm 一次落盘。 -->
<script setup lang="ts">
import { ref, computed, watch, inject, onBeforeUnmount } from 'vue'
import { appConfirm } from '../shared/appConfirm'
import OntologyList from '../shared/OntologyList.vue'
import OntDrawer from '../shared/OntDrawer.vue'
import Field from '../shared/EditorField.vue'
import { actionsOf, isActionV2, objectsOfAction } from './actionModel'
import { useOntTable } from './ontList'
import { actionDeleteCheck } from './dependencyModel'
import type { FormGuardAPI, FormSaveAPI } from '../app/formGuard'

// canvasReturn（20260919 图谱优化）：非空表示从本体图谱「打开定义」跳转而来，页头显示「返回图谱」（前端可选上下文）。
const props = defineProps<{ state: any; focusId?: string; focusOrigin?: { type?: string; tab?: string }; canvasReturn?: string; editFocus?: boolean }>()
const emit = defineEmits(['before-change', 'changed', 'navigate'])
function backToGraph() { emit('navigate', 'objects', { graph: true, graphFocus: props.canvasReturn || '' } as any) }
const guardApi = inject<FormGuardAPI>('form-guard')!
const formSave = inject<FormSaveAPI>('form-save')!

type Mode = 'list' | 'edit'
const mode = ref<Mode>('list')
const editId = ref('') // 编辑表单目标（新建时为预生成 id）；查看详情改由抽屉承担，无列表选中概念
const detailId = ref('') // 只读详情抽屉
const refsId = ref('') // 关联对象抽屉
const refFilter = ref<'all' | 'linked' | 'unlinked'>('all')
const message = ref(''), saving = ref(false)
const draft = ref<{ name: string; description: string; effect: string }>({ name: '', description: '', effect: '' })
let baseline = ''
let converting = false // 历史动作「转换为新格式」进入编辑：保存时替换旧结构

const rows = computed(() => actionsOf(props.state))
const graph = computed(() => props.state?.ontology?.['@graph'] || [])
const typeName = (id: string) => { const n: any = graph.value.find(x => x['@id'] === id || x['@id'] === 'mg:' + id.replace(/^mg:/, '')); return n?.['rdfs:label'] || id }
function actionById(id: string) { return rows.value.find((a: any) => a.id === id) || null }

interface ActionRow { id: string; name: string; desc: string; effect: string; legacy: boolean; refs: string[] }
const refFilterOptions: { value: 'all' | 'linked' | 'unlinked'; label: string }[] = [
  { value: 'all', label: '全部' }, { value: 'linked', label: '有关联' }, { value: 'unlinked', label: '无关联' }]
const tableRows = computed<ActionRow[]>(() => rows.value
  .filter((a: any) => refFilter.value === 'all' || (objectsOfAction(props.state, a.id).length > 0) === (refFilter.value === 'linked'))
  .map((a: any) => ({
    id: a.id,
    name: a.name || '未命名动作',
    desc: a.description || '',
    effect: a.effect || '',
    legacy: !isActionV2(a),
    refs: objectsOfAction(props.state, a.id),
  })))
const actionsTable = useOntTable<ActionRow>(() => tableRows.value, {
  match: (r, q) => (r.name + ' ' + r.desc).toLowerCase().includes(q),
  sort: (a, b) => a.name.localeCompare(b.name, 'zh-CN'),
})
const hasFilter = computed(() => actionsTable.q.value.trim() !== '' || refFilter.value !== 'all')

const detail = computed(() => actionById(detailId.value))
const refTarget = computed(() => actionById(refsId.value))
const detailRefs = computed(() => detail.value ? objectsOfAction(props.state, detail.value.id) : [])
const refTargetRefs = computed(() => refTarget.value ? objectsOfAction(props.state, refTarget.value.id) : [])
const detailIsLegacy = computed(() => { const a: any = detail.value; return !!a && !isActionV2(a) })
const editingExisting = computed(() => rows.value.some((a: any) => a.id === editId.value))

// 数据变化时保持编辑目标一致性：编辑目标被移除（撤销/外部删除）则退回列表，guard 随 mode 注销。
watch(rows, v => { if (mode.value === 'edit' && !v.some((a: any) => a.id === editId.value)) mode.value = 'list' })
// 深链定位：清空筛选并打开该动作（§7）；图谱「编辑」跳转（editFocus）时直接进编辑表单。
watch(() => props.focusId, id => {
  if (id && rows.value.some((a: any) => a.id === id)) {
    actionsTable.reset()
    refFilter.value = 'all'
    message.value = ''
    refsId.value = ''
    if (props.editFocus) { openEdit(id); return }
    detailId.value = id
    if (mode.value === 'edit') mode.value = 'list'
  }
}, { immediate: true })

const dirty = computed(() => mode.value === 'edit' && JSON.stringify(draft.value) !== baseline)
const guard = { isDirty: () => dirty.value, discard: () => closeEditor() }
watch(mode, m => { m === 'edit' ? guardApi.register(guard) : guardApi.unregister(guard) })
onBeforeUnmount(() => guardApi.unregister(guard))

function closeEditor() { mode.value = 'list' }
function openDetail(id: string) { message.value = ''; detailId.value = id }
function openRefs(id: string) { refsId.value = id }
function goObject(objectTypeId: string) { emit('navigate', 'objects', { type: objectTypeId, tab: 'actions' }) }
function backToOrigin() {
  const origin = props.focusOrigin, t = origin?.type
  if (!t) return
  emit('navigate', 'objects', { type: t, tab: origin?.tab || 'actions' })
}
function setRefFilter(v: 'all' | 'linked' | 'unlinked') { refFilter.value = v; actionsTable.page.value = 1 }
function clearFilters() { actionsTable.q.value = ''; refFilter.value = 'all'; actionsTable.page.value = 1 }

function openNew() {
  editId.value = 'action_' + crypto.randomUUID().replaceAll('-', '')
  draft.value = { name: '', description: '', effect: '' }
  baseline = JSON.stringify(draft.value)
  converting = false
  mode.value = 'edit'
  message.value = ''
  detailId.value = ''
  refsId.value = ''
}
function openEdit(id: string) {
  const a: any = actionById(id)
  if (!a) return
  editId.value = id
  draft.value = { name: a.name || '', description: a.description || '', effect: a.effect || '' }
  baseline = JSON.stringify(draft.value)
  converting = false
  mode.value = 'edit'
  message.value = ''
  detailId.value = ''
  refsId.value = ''
}
async function openConvert(id: string) {
  const a: any = actionById(id)
  if (!a) return
  if (!(await appConfirm({ message: '将此历史动作转换为新格式编辑？保存后旧格式的适用对象、输入参数、提交条件、权限与验收字段会被新结构替代并移除，且需重新发布后项目引用才会更新。继续吗？' }))) return
  editId.value = id
  draft.value = { name: a.name || '', description: a.description || '', effect: a.effect || '' }
  baseline = JSON.stringify(draft.value)
  converting = true
  mode.value = 'edit'
  message.value = ''
  detailId.value = ''
  refsId.value = ''
}
// 行内/抽屉「编辑」入口：V2 直接进表单；历史格式先走既有转换确认。
function editAction(id: string) { const a: any = actionById(id); if (!a) return; isActionV2(a) ? openEdit(id) : openConvert(id) }
function editFromDetail() { if (detail.value) editAction(detail.value.id) }

async function submit(apply: () => void, action?: { actionLabel: string; target?: { kind: string; id: string } }): Promise<boolean> {
  const r = await formSave.submitForm('ontology', apply, action)
  if (!r.ok) { message.value = r.message; return false }
  return true
}
async function save() {
  if (saving.value) return
  const name = draft.value.name.trim(), desc = draft.value.description.trim(), effect = draft.value.effect.trim()
  if (!name || !desc || !effect) { message.value = '请填写动作名称、业务定义和业务效果。'; return }
  saving.value = true
  const targetId = editId.value
  const isNew = !rows.value.some((a: any) => a.id === targetId)
  const ok = await submit(() => {
    const actions = props.state.workflow.actions
    if (isNew) actions.push({ id: targetId, name, description: desc, effect, definitionVersion: 2, status: 'experimental' })
    else {
      const a: any = actions.find((x: any) => x.id === targetId)
      a.name = name; a.description = desc; a.effect = effect
      if (converting) { // 显式转换：新结构替代历史字段（对象关联改由对象建模的关联集合维护）
        for (const key of ('object_type' in a ? ['object_type'] : []).concat('object_types' in a ? ['object_types'] : [])) delete a[key]
        for (const key of ['inputs', 'criteria', 'permission', 'acceptance', 'relation_ref', 'no_inputs', 'implementation_ref']) delete a[key]
        a.definitionVersion = 2
      }
    }
  }, { actionLabel: (isNew ? '新建动作「' : '修改动作「') + name + '」', target: { kind: 'action', id: targetId } })
  saving.value = false
  if (!ok) return
  converting = false
  mode.value = 'list'
  message.value = '已保存到本体草稿；已发布版本需重新发布后更新。'
  // 来自图谱的「编辑」跳转：保存成功直接回画布（2026-09-20）
  if (props.canvasReturn) backToGraph()
}
// 删除定义（20260920 需求 13 统一语义）：有对象关联或契约/接口等依赖 → 阻断并列出业务名称与原因；
// 无依赖 → 确认后删除。判断与对象页、图谱共用 actionDeleteCheck，不在此处另写规则。
async function remove(id: string) {
  const a: any = actionById(id)
  if (!a) return
  const check = actionDeleteCheck(props.state, a.id)
  if (check.blocked) { message.value = check.message; return }
  if (!(await appConfirm({ message: '删除动作「' + (a.name || a.id) + '」？当前没有对象关联此动作；删除的是当前草稿定义，已发布版本不变。可通过撤销恢复。', danger: true }))) return
  if (await submit(() => { const actions = props.state.workflow.actions; actions.splice(actions.indexOf(a), 1) })) message.value = '已删除动作定义。'
}
</script>
<template>
<section class="card ont-block">
  <div class="ont-lib-head">
    <div>
      <h2>动作定义</h2>
      <p>集中维护共享动作库：只写名称、业务定义、业务效果。对象建模中选择哪些对象支持此动作；具体执行由项目绑定配置。</p>
    </div>
    <div class="ont-actions">
      <button v-if="canvasReturn" type="button" @click="backToGraph">← 返回图谱</button>
      <button type="button" class="primary" @click="openNew">＋ 新建动作</button>
    </div>
  </div>
  <template v-if="mode === 'list'">
    <p v-if="message" :class="message.startsWith('已') ? 'inline-success' : 'inline-error'" role="alert">{{ message }}</p>
    <OntologyList
      :search="actionsTable.q.value" @update:search="actionsTable.q.value = $event"
      :total="actionsTable.filtered.value.length" :page="actionsTable.page.value" :page-count="actionsTable.pageCount.value"
      :sort-desc="actionsTable.dir.value < 0"
      ariaLabel="动作定义列表" search-placeholder="搜索动作名称或业务定义"
      :columns="[{ label: '名称', width: '32%', sort: true }, { label: '业务效果', width: '33%' }, { label: '关联对象', width: '18%' }, { label: '操作', width: '17%' }]"
      :empty-title="hasFilter ? '没有匹配的动作' : '还没有动作定义'"
      :empty-hint="hasFilter ? '调整关键词或筛选条件再试试。' : '使用右上角「＋ 新建动作」创建第一项。'"
      @sort="actionsTable.toggleSort()" @page="actionsTable.page.value += $event" @clear="clearFilters">
      <template #filter>
        <div class="ont-filters" role="group" aria-label="按引用情况筛选">
          <button v-for="f in refFilterOptions" :key="f.value" type="button" :class="{ active: refFilter === f.value }" :aria-pressed="refFilter === f.value" @click="setRefFilter(f.value)">{{ f.label }}</button>
        </div>
      </template>
      <tr v-for="r in actionsTable.paged.value" :key="r.id" :data-row="r.id">
        <td>
          <button type="button" class="ont-name" @click="openDetail(r.id)">{{ r.name }}</button>
          <span v-if="r.desc" class="ont-sub" :title="r.desc">{{ r.desc }}</span>
          <span v-if="r.legacy" class="ont-sub">历史格式 · 只读</span>
        </td>
        <td><span class="ont-clip" :title="r.effect">{{ r.effect || '—' }}</span></td>
        <td>
          <button type="button" class="ont-badge" :title="'查看「' + r.name + '」的关联对象'" @click="openRefs(r.id)">{{ r.refs.length ? r.refs.length + ' 个对象' : '暂未关联' }}</button>
        </td>
        <td class="ont-ops">
          <button type="button" class="row-link" @click="editAction(r.id)">编辑</button>
          <button type="button" class="row-link danger" @click="remove(r.id)">删除</button>
        </td>
      </tr>
    </OntologyList>
    <p class="ont-context">动作描述不会执行任何指令；本期不定义输入参数、提交条件或执行表单。项目实现请在项目映射的「对象映射 → 动作绑定」中配置。</p>
  </template>
  <section v-else class="card detail-card" :key="'edit-' + editId">
    <div class="detail-heading"><div><span class="eyebrow">{{ editingExisting ? '编辑动作' : '新建动作' }}</span><h2>{{ draft.name || '未命名动作' }}</h2></div><span class="status-pill">仅三项业务字段</span></div>
    <div class="form-grid">
      <Field label="动作名称" class="full" :model-value="draft.name" required example="例如：停止充放电" @update:model-value="draft.name = $event"/>
      <Field label="业务定义" type="textarea" class="full" :model-value="draft.description" required example="请求目标对象停止当前充电或放电。" help="这个动作有什么用途。" @update:model-value="draft.description = $event"/>
      <Field label="业务效果" type="textarea" class="full" :model-value="draft.effect" rows="4" example="请求停止充放电，目标功率为 0，以设备反馈确认完成。" help="执行后期望发生什么；用业务语言描述，不会执行指令。" @update:model-value="draft.effect = $event"/>
    </div>
    <p class="field-help">不需要先选择作用对象（在对象建模中关联），也不维护参数清单——修改名称、调整归属等操作所需信息由项目实现配置。</p>
    <p v-if="message" class="inline-error" role="alert">{{ message }}</p>
    <div class="detail-footer"><div class="tools"><button type="button" class="primary" :disabled="saving" @click="save">{{ saving ? '保存中…' : '保存定义' }}</button><button type="button" @click="closeEditor">取消</button></div>
      <span>保存直接写入本体草稿；已发布版本需重新发布后更新。</span></div>
  </section>

  <!-- 只读详情抽屉（§7）：名称点击打开；footer 按格式分流编辑/转换入口 -->
  <OntDrawer v-if="detail" :title="detail.name || '未命名动作'" :subtitle="detailIsLegacy ? '历史动作 · 只读' : '动作定义'" @close="detailId = ''">
    <div class="ont-field"><span class="ont-field-label">业务定义</span><p>{{ detail.description || '尚未填写。' }}</p></div>
    <div class="ont-field"><span class="ont-field-label">业务效果</span><p>{{ detail.effect || '尚未填写。' }}</p></div>
    <div class="ont-field">
      <span class="ont-field-label">关联对象</span>
      <p class="ont-hint">只读反向引用；同一份动作定义可被多个对象共用，点击定位到对象的动作页签。</p>
      <template v-if="detailRefs.length">
        <button v-for="t in detailRefs" :key="t" type="button" class="ont-ref-row" @click="goObject(t)">
          <span><strong>{{ typeName(t) }}</strong><small>查看此对象支持的动作</small></span>→
        </button>
      </template>
      <p v-else class="ont-hint">暂未关联对象。到「对象建模 → 动作」页签添加。</p>
    </div>
    <details v-if="!isActionV2(detail)" class="technical-section"><summary>历史字段（只读保留）</summary>
      <p v-if="detail.object_type || detail.object_types">适用对象：{{ (Array.isArray(detail.object_types) ? detail.object_types : [detail.object_type]).filter(Boolean).map(typeName).join('、') }}</p>
      <p v-if="detail.criteria">提交条件：{{ detail.criteria }}</p>
      <p v-if="detail.permission">权限与审批要求：{{ detail.permission }}</p>
      <p v-if="detail.acceptance">验收案例：{{ detail.acceptance }}</p>
      <div v-for="(p, i) in detail.inputs || []" :key="i" class="param-line">输入参数 {{ Number(i) + 1 }}：{{ p.name || '未命名' }} · {{ p.type }}{{ p.required ? ' · 必填' : '' }}{{ p.description ? ' · ' + p.description : '' }}</div>
      <p class="muted">历史字段不会在读写时丢失；转为新格式需显式确认替代。</p></details>
    <p class="ont-hint">动作描述不会执行任何指令；本期不定义输入参数、提交条件或执行表单。项目实现请在项目映射的「对象映射 → 动作绑定」中配置。</p>
    <template #footer>
      <button v-if="focusOrigin?.type" type="button" @click="backToOrigin">返回来源对象</button>
      <button type="button" class="primary" @click="editFromDetail">{{ detailIsLegacy ? '转为新格式编辑' : '编辑' }}</button>
    </template>
  </OntDrawer>

  <!-- 关联对象抽屉：行内「N 个对象 / 暂未关联」打开；点击对象定位到对象动作页签 -->
  <OntDrawer v-if="refTarget" :title="(refTarget.name || '未命名动作') + ' · 关联对象'" subtitle="关联对象" @close="refsId = ''">
    <template v-if="refTargetRefs.length">
      <p class="ont-hint">关联在「对象建模 → 动作」页签维护，此处只读；点击对象可定位到对应页签。</p>
      <button v-for="t in refTargetRefs" :key="t" type="button" class="ont-ref-row" @click="goObject(t)">
        <span><strong>{{ typeName(t) }}</strong><small>{{ refTarget.name || '此动作' }}</small></span>→
      </button>
    </template>
    <p v-else class="ont-hint">暂未关联对象。到「对象建模 → 动作」页签添加。</p>
    <template #footer><button type="button" @click="refsId = ''">关闭</button></template>
  </OntDrawer>
</section>
</template>
<style scoped>
.ont-filters{display:flex;gap:6px;flex-wrap:wrap}
.ont-filters button{font-size:12px;padding:4px 9px;border-radius:var(--r-pill)}
.ont-filters button.active{background:var(--blue-soft);border-color:var(--blue-line);color:var(--blue-ink);font-weight:600}
.ont-ref-row small{color:var(--muted);font-size:12px;font-weight:400}
.technical-section p{margin:8px 0;line-height:1.7}
.param-line{border-bottom:1px solid var(--line);padding:8px 0;font-size:13px}
</style>
