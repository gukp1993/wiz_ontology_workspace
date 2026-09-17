<!-- 动作库（20260917 需求 §3.1）：本体主菜单「动作定义」。
     新格式动作（definitionVersion 2）只有名称/业务定义/业务效果三个必填业务字段；
     不选对象、不配输入参数/提交条件/审批/验收。已关联对象只读反向展示，点击进入
     对象建模的动作页签。历史动作只读展示（旧字段不丢失），显式确认后可转换为新格式编辑。
     编辑/删除走 T00：form-guard 离开保护 + form-save.submitForm 一次落盘。 -->
<script setup lang="ts">
import { ref, computed, watch, inject, onBeforeUnmount } from 'vue'
import { appConfirm } from '../shared/appConfirm'
import EditorLayout from '../shared/EditorLayout.vue'
import Field from '../shared/EditorField.vue'
import { actionsOf, isActionV2, objectsOfAction } from './actionModel'
import { graphReferences } from './editorModel'
import type { FormGuardAPI, FormSaveAPI } from '../app/formGuard'

const props = defineProps<{ state: any; focusId?: string }>(), emit = defineEmits(['before-change', 'changed', 'navigate'])
const guardApi = inject<FormGuardAPI>('form-guard')!
const formSave = inject<FormSaveAPI>('form-save')!

const query = ref(''), selected = ref(''), message = ref(''), saving = ref(false)
type Mode = 'view' | 'edit'
const mode = ref<Mode>('view')
const draft = ref<{ name: string; description: string; effect: string }>({ name: '', description: '', effect: '' })
let baseline = ''
let converting = false // 历史动作「转换为新格式」进入编辑：保存时替换旧结构

const rows = computed(() => actionsOf(props.state))
const item = computed(() => rows.value.find(a => a.id === selected.value) || null)
const graph = computed(() => props.state?.ontology?.['@graph'] || [])
const typeName = (id: string) => { const n: any = graph.value.find(x => x['@id'] === id || x['@id'] === 'mg:' + id.replace(/^mg:/, '')); return n?.['rdfs:label'] || id }
const refNames = computed(() => item.value ? objectsOfAction(props.state, item.value.id) : [])

const list = computed(() => rows.value
  .filter((a: any) => (a.name || '').includes(query.value.trim()))
  .map((a: any) => ({
    id: a.id,
    name: a.name || '未命名动作',
    meta: objectsOfAction(props.state, a.id).map(typeName).join('、') || '暂未关联',
    note: isActionV2(a) ? undefined : '历史格式 · 只读',
  })))

watch(rows, v => { if (!v.some((a: any) => a.id === selected.value)) { selected.value = v[0]?.id || ''; mode.value = 'view' } }, { immediate: true })
watch(() => props.focusId, id => { if (id && rows.value.some((a: any) => a.id === id)) { query.value = ''; selected.value = id; mode.value = 'view' } }, { immediate: true })

const dirty = computed(() => mode.value === 'edit' && JSON.stringify(draft.value) !== baseline)
const guard = { isDirty: () => dirty.value, discard: () => closeEditor() }
watch(mode, m => { m === 'edit' ? guardApi.register(guard) : guardApi.unregister(guard) })
onBeforeUnmount(() => guardApi.unregister(guard))

async function select(id: string) { if (dirty.value && !(await appConfirm({ message: '当前表单有未保存的修改，离开将放弃本次修改。继续吗？' }))) return; selected.value = id; mode.value = 'view'; message.value = '' }
function closeEditor() { mode.value = 'view'; if (!rows.value.some((a: any) => a.id === selected.value)) selected.value = rows.value[0]?.id || '' }

function openNew() {
  selected.value = 'action_' + crypto.randomUUID().replaceAll('-', '')
  draft.value = { name: '', description: '', effect: '' }
  baseline = JSON.stringify(draft.value)
  converting = false
  mode.value = 'edit'
  message.value = ''
}
function openEdit() {
  const a: any = item.value
  if (!a) return
  draft.value = { name: a.name || '', description: a.description || '', effect: a.effect || '' }
  baseline = JSON.stringify(draft.value)
  converting = false
  mode.value = 'edit'
  message.value = ''
}
async function openConvert() {
  const a: any = item.value
  if (!a) return
  if (!(await appConfirm({ message: '将此历史动作转换为新格式编辑？保存后旧格式的适用对象、输入参数、提交条件、权限与验收字段会被新结构替代并移除，且需重新发布后项目引用才会更新。继续吗？' }))) return
  draft.value = { name: a.name || '', description: a.description || '', effect: a.effect || '' }
  baseline = JSON.stringify(draft.value)
  converting = true
  mode.value = 'edit'
  message.value = ''
}

async function submit(apply: () => void): Promise<boolean> {
  const r = await formSave.submitForm('ontology', apply)
  if (!r.ok) { message.value = r.message; return false }
  return true
}
async function save() {
  if (saving.value) return
  const name = draft.value.name.trim(), desc = draft.value.description.trim(), effect = draft.value.effect.trim()
  if (!name || !desc || !effect) { message.value = '请填写动作名称、业务定义和业务效果。'; return }
  saving.value = true
  const targetId = selected.value
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
  })
  saving.value = false
  if (!ok) return
  converting = false
  mode.value = 'view'
  message.value = '已保存到本体草稿；已发布版本需重新发布后更新。'
}
async function remove() {
  const a: any = item.value
  if (!a) return
  const users = objectsOfAction(props.state, a.id)
  if (isActionV2(a) && users.length) { message.value = '暂不能删除：此动作仍被 ' + users.map(typeName).join('、') + ' 引用；请先到对象建模移除关联。'; return }
  const refs = graphReferences(props.state, a.id)
  if (refs.length) { message.value = '暂不能删除：请先处理引用（' + refs.join('、') + '）。'; return }
  if (!(await appConfirm({ message: '删除动作「' + (a.name || a.id) + '」？可通过撤销恢复。', danger: true }))) return
  if (await submit(() => { const actions = props.state.workflow.actions; actions.splice(actions.indexOf(a), 1) })) message.value = '已删除动作定义。'
}
function goObject(objectTypeId: string) { emit('navigate', 'objects', { type: objectTypeId, tab: 'actions' }) }
</script>
<template>
<EditorLayout title="动作定义" subtitle="集中维护共享动作库：只写名称、业务定义、业务效果。对象建模中选择哪些对象支持此动作；具体执行由项目绑定配置。"
  :items="list" :selected="selected" v-model:search="query" create-label="新建动作" @create="openNew" @select="select">
  <section v-if="item && mode === 'view'" class="card detail-card" :key="item.id">
    <div class="detail-heading"><div><span class="eyebrow">{{ isActionV2(item) ? '动作定义' : '历史动作 · 只读' }}</span><h2>{{ item.name || '未命名动作' }}</h2></div>
      <div class="tools"><button @click="isActionV2(item) ? openEdit() : openConvert()">{{ isActionV2(item) ? '编辑' : '转为新格式编辑' }}</button><button class="danger" @click="remove">删除</button></div></div>
    <div class="def-block"><h3>业务定义</h3><p class="pre">{{ item.description || '尚未填写。' }}</p></div>
    <div class="def-block"><h3>业务效果</h3><p class="pre effect">{{ item.effect || '尚未填写。' }}</p></div>
    <div class="def-block"><h3>已关联对象 <small class="muted">只读反向引用；同一份动作定义被多个对象共用</small></h3>
      <div v-if="refNames.length" class="tools ref-row"><button v-for="t in refNames" :key="t" class="row-link" @click="goObject(t)">{{ typeName(t) }} →</button></div>
      <p v-else class="muted">暂未关联对象。到「对象建模 → 动作」页签添加。</p></div>
    <details v-if="!isActionV2(item)" class="technical-section"><summary>历史字段（只读保留）</summary>
      <p v-if="item.object_type || item.object_types">适用对象：{{ (Array.isArray(item.object_types) ? item.object_types : [item.object_type]).filter(Boolean).map(typeName).join('、') }}</p>
      <p v-if="item.criteria">提交条件：{{ item.criteria }}</p>
      <p v-if="item.permission">权限与审批要求：{{ item.permission }}</p>
      <p v-if="item.acceptance">验收案例：{{ item.acceptance }}</p>
      <div v-for="(p, i) in item.inputs || []" :key="i" class="param-line">输入参数 {{ Number(i) + 1 }}：{{ p.name || '未命名' }} · {{ p.type }}{{ p.required ? ' · 必填' : '' }}{{ p.description ? ' · ' + p.description : '' }}</div>
      <p class="muted">历史字段不会在读写时丢失；转为新格式需显式确认替代。</p></details>
    <p class="field-help">动作描述不会执行任何指令；本期不定义输入参数、提交条件或执行表单。项目实现请在项目映射的「对象映射 → 动作绑定」中配置。</p>
    <p v-if="message" :class="message.startsWith('已') ? 'inline-success' : 'inline-error'" role="alert">{{ message }}</p>
  </section>
  <section v-else-if="mode === 'edit'" class="card detail-card" :key="'edit-' + selected">
    <div class="detail-heading"><div><span class="eyebrow">{{ rows.some((a: any) => a.id === selected) ? '编辑动作' : '新建动作' }}</span><h2>{{ draft.name || '未命名动作' }}</h2></div><span class="status-pill">仅三项业务字段</span></div>
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
  <section v-else class="card">
    <div class="empty-state">
      <span class="empty-state-ico">◇</span>
      <p>选择一项动作，或新建动作定义。</p>
    </div>
  </section>
</EditorLayout>
</template>
<style scoped>
.def-block{margin:18px 0}
.def-block h3{font-size:14px;margin:0 0 6px}
.def-block h3 small{font-weight:400;font-size:12px}
.pre{white-space:pre-wrap;margin:0;line-height:1.8}
.effect{background:var(--blue-soft);border:1px solid var(--blue-line);border-radius:8px;padding:14px}
.ref-row{flex-wrap:wrap}
.technical-section p{margin:8px 0;line-height:1.7}
.param-line{border-bottom:1px solid var(--line);padding:8px 0;font-size:13px}
.detail-heading .tools{display:flex;gap:8px}
.detail-heading .danger{margin:0}
</style>
