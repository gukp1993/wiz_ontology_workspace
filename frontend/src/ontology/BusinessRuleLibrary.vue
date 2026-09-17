<!-- 业务规则库（20260917 一期，原型 ruleList/detail/editRule/showOwners）：
     列表仅名称、引用对象、操作三列；名称搜索即筛；新建与编辑为四字段弹窗（名称单行，
     业务定义/规则内容/输出结果多行，全部必填，保存时提示并聚焦首个缺失）；
     查看为只读详情弹窗；引用对象弹窗反查对象类型并可跳到对象建模的规则页签。
     保存走 inject('form-save') 一次落盘（失败保留表单不假成功）；编辑期间注册 T00 表单守卫。
     一期不提供删除、复制、分类、状态、依赖与执行。 -->
<script setup lang="ts">
import { computed, inject, onBeforeUnmount, ref, watch } from 'vue'
import BusinessRuleDialog from './BusinessRuleDialog.vue'
import { objectsOfRule, RULE_FIELDS, rulesOf } from './businessRuleModel'
import type { FormGuardAPI, FormSaveAPI } from '../app/formGuard'

const props = defineProps<{ state: any; focusId?: string }>(), emit = defineEmits(['before-change', 'changed', 'navigate'])
const guardApi = inject<FormGuardAPI>('form-guard')!
const formSave = inject<FormSaveAPI>('form-save')!

const query = ref(''), message = ref(''), saving = ref(false)
// dialog: null | {kind:'detail', id} | {kind:'edit', isNew} | {kind:'owners', id}
const dialog = ref<{ kind: 'detail' | 'edit' | 'owners'; id: string; isNew?: boolean } | null>(null)
const draft = ref<any>(null)
let baseline = ''
const fieldErrors = ref<Record<string, string>>({})

const graph = computed(() => props.state?.ontology?.['@graph'] || [])
const typeName = (id: string) => { const n: any = graph.value.find(x => x['@id'] === id || x['@id'] === 'mg:' + id.replace(/^mg:/, '')); return n?.['rdfs:label'] || id }
const rows = computed(() => rulesOf(props.state))
const visible = computed(() => rows.value
  .filter((r: any) => (r.name || '').toLowerCase().includes(query.value.trim().toLowerCase()))
  .map((r: any) => ({ id: r.id, name: r.name || '未命名规则', refs: objectsOfRule(props.state, r.id) })))
const currentRule = computed(() => rows.value.find((r: any) => r.id === dialog.value?.id) || null)
const detailRule = computed(() => dialog.value?.kind === 'detail' ? currentRule.value : null)
const ownersRule = computed(() => dialog.value?.kind === 'owners' ? currentRule.value : null)
const ownersNames = computed(() => dialog.value?.id ? objectsOfRule(props.state, dialog.value.id) : [])
const editTargetName = computed(() => draft.value?.name || '')

watch(rows, v => { if (dialog.value && !v.some((r: any) => r.id === dialog.value!.id)) dialog.value = null }, { immediate: true })
watch(() => props.focusId, id => { if (id && rows.value.some((r: any) => r.id === id)) { query.value = ''; openDetail(id) } }, { immediate: true })

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
function closeDialog() {
  if (dialog.value?.kind === 'edit' && dirty.value && !confirm('放弃尚未保存的修改？')) return
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
  const r = await formSave.submitForm('ontology', () => {
    const list = props.state.workflow.businessRules = Array.isArray(props.state.workflow.businessRules) ? props.state.workflow.businessRules : []
    const hit = list.find((x: any) => x === Object(x) && x.id === target.id)
    if (hit) Object.assign(hit, payload)
    else list.push({ id: target.id, ...payload })
    void name
  })
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
<section class="card brl-card">
  <div class="detail-heading"><div><h2>业务规则</h2>
    <p class="muted">用自然语言维护通用业务规则；对象建模中引用，项目实现与执行不在本期范围。</p></div>
    <button class="primary" @click="openEdit()">＋ 新建规则</button></div>
  <input type="search" v-model="query" class="brl-search" placeholder="搜索规则名称" aria-label="搜索规则名称">
  <div class="table-wrap">
    <table>
      <thead><tr><th>名称</th><th>引用对象</th><th>操作</th></tr></thead>
      <tbody>
        <tr v-for="row in visible" :key="row.id">
          <td class="brl-name"><button class="row-link" @click="openDetail(row.id)">{{ row.name }}</button></td>
          <td><button class="row-link" @click="openOwners(row.id)">{{ row.refs.length ? row.refs.length + ' 个对象类型' : '暂无引用' }}</button></td>
          <td class="brl-ops"><button class="row-link" @click="openDetail(row.id)">查看</button><button class="row-link" @click="openEdit(row.id)">编辑</button></td>
        </tr>
        <tr v-if="!visible.length"><td colspan="3">
          <div class="empty-state">
            <span class="empty-state-ico">◇</span>
            <p>{{ query ? '没有匹配的规则。' : '还没有业务规则。' }}</p>
            <button v-if="!query" type="button" class="primary" @click="openEdit()">＋ 新建规则</button>
          </div>
        </td></tr>
      </tbody>
    </table>
  </div>
  <p v-if="message" :class="message.startsWith('规则已保存') ? 'inline-success' : 'inline-error'" role="alert" style="margin-top:12px">{{ message }}</p>
</section>

<BusinessRuleDialog v-if="detailRule" mode="detail" :rule="detailRule" :allow-edit="true" @close="dialog = null" @edit="openEdit(detailRule.id)"/>
<BusinessRuleDialog v-if="dialog?.kind === 'edit'" mode="edit" :rule="draft" :is-new="dialog.isNew" :saving="saving" :errors="fieldErrors" @close="closeDialog" @field="onField" @save="saveEdit"/>

<div v-if="ownersRule" class="modal-backdrop" @click.self="dialog = null">
  <section class="modal-card brl-card" role="dialog" aria-modal="true" aria-label="引用对象">
    <div class="brc-head"><h2>引用对象</h2><button type="button" aria-label="关闭" @click="dialog = null">关闭</button></div>
    <div class="brc-body">
      <p class="owners-rule-name">{{ ownersRule.name || '未命名规则' }}</p>
      <table v-if="ownersNames.length">
        <thead><tr><th>对象类型</th><th>操作</th></tr></thead>
        <tbody><tr v-for="t in ownersNames" :key="t"><td>{{ typeName(t) }}</td><td class="brl-ops"><button class="row-link" @click="goObject(t)">查看对象规则</button></td></tr></tbody>
      </table>
      <p v-else class="muted owners-empty">暂无对象引用此规则</p>
      <p class="field-help">引用关系在对象建模的「规则」页签中维护；这里只读反查。</p>
    </div>
  </section>
</div>
</template>
<style scoped>
.brl-card{max-width:1200px}
.brl-search{max-width:380px;margin:14px 0 10px}
.table-wrap{overflow:auto}
.table-wrap th{text-align:left;background:var(--paper-2);color:var(--muted);font-weight:500}
.table-wrap td,.table-wrap th{padding:12px 10px;border-bottom:1px solid var(--line,#e4eaf2);text-align:left;vertical-align:top}
.table-wrap tr:last-child td{border-bottom:0}
.brl-name{width:50%}
.brl-ops{white-space:nowrap}
.brl-ops .row-link + .row-link{margin-left:12px}
.owners-rule-name{font-weight:600;margin:0 0 12px}
.owners-empty{padding:18px 0}
.detail-heading{align-items:flex-start}
</style>
