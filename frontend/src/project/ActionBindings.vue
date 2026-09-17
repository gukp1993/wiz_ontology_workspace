<!-- 动作接口映射（20260917 需求 §2/§3/§4）：项目对象映射内的「动作绑定」页签。
     有效动作与关联只来自项目固定引用的已发布本体版本（refState），绝不读本体最新草稿。
     按「项目（存储位置）＋对象类型＋动作」保存 bindings.actionBindings，组合唯一；
     本期只配置 HTTP 接口（implementation.kind='api'、schemaVersion=2，规则见 actionHttpModel.ts，
     与 workbench/action_http.py 镜像）：请求方式、接口地址、参数表、认证凭据引用与请求示意文本。
     不发送任何请求、不做连通性测试、不做返回结果映射或设备控制。
     历史 api（无 schemaVersion）保留 path 旧格式，编辑保存后转成 v2；历史 flow 与未知实现只读保留；
     失效关联保留原配置并提示，需用户确认后移除，不自动删除、不静默迁移。
     保存走既有 form-save；取消/关闭/Esc 有脏表单保护（appConfirm），凭据密钥只写项目受保护凭据库。 -->
<script setup lang="ts">
import { computed, inject, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import AppSelect from '../shared/AppSelect.vue'
import Field from '../shared/EditorField.vue'
import { appConfirm } from '../shared/appConfirm'
import { actionBindingsOf, actionsOf, effectiveAssociations } from '../ontology/actionModel'
import { listFlows } from '../flow/api'
import type { FormGuardAPI, FormSaveAPI } from '../app/formGuard'
import { listApiCredentials, saveApiCredential } from './api'
import {
  AUTH_TYPE_OPTIONS, BODY_FORMAT_OPTIONS, CONSTANT_TYPE_OPTIONS, IN_LABELS, IN_OPTIONS, METHOD_OPTIONS,
  SOURCE_LABELS, apiContext, apiView, buildPreview, draftFrom, emptyApi, inheritedTypeText, isApiV2,
  newParamId, sourceOptions, summaryText, validateApi,
} from './actionHttpModel'
import type { ApiContext, ApiIssues, ApiParam } from './actionHttpModel'

const props = defineProps<{ projectState: any; refState: any; objectType: string }>()
const emit = defineEmits(['before-change', 'changed', 'edit-state'])
function before() { emit('before-change') }
function changed() { emit('changed') }
function mutate(fn: () => void) { before(); fn(); changed() }

const graph = computed(() => props.refState?.ontology?.['@graph'] || [])
const typeName = computed(() => { const n: any = graph.value.find(x => x['@id'] === 'mg:' + props.objectType); return n?.['rdfs:label'] || props.objectType })
const bareType = (t: string) => String(t || '').replace(/^mg:/, '')

// 引用版本中的有效关联动作（显式集合 ∪ 历史动作 object_type 推导，与后端镜像）
const validActions = computed(() => effectiveAssociations(props.refState)
  .filter(r => bareType(r.objectTypeId) === bareType(props.objectType)))
const actionsById = computed(() => new Map(actionsOf(props.refState).map((a: any) => [a.id, a])))
const bindings = computed(() => actionBindingsOf(props.projectState).filter((r: any) => bareType(r.objectTypeId) === bareType(props.objectType)))
const bindingFor = (actionId: string) => bindings.value.find((r: any) => r.actionId === actionId)

// ── 列表（三列：动作名称 / 接口地址 / 操作；不设状态列） ──
type RowStatus = 'unconfigured' | 'api' | 'legacy' | 'flow' | 'unknown' | 'stale'
type Row = { key: string; actionId: string; action: any | null; binding: any | null; status: RowStatus }
const implOf = (binding: any): any => (binding?.implementation && typeof binding.implementation === 'object' ? binding.implementation : {})
function statusOf(binding: any): RowStatus {
  const impl = implOf(binding)
  if (!Object.keys(impl).length) return 'unconfigured'
  if (isApiV2(impl)) return 'api'
  if (String(impl.kind || '') === 'api') return 'legacy'
  if (String(impl.kind || '') === 'flow') return 'flow'
  return 'unknown'
}
const rows = computed<Row[]>(() => {
  const out: Row[] = []
  for (const r of validActions.value) {
    const binding = bindingFor(r.actionId) || null
    out.push({ key: 'a:' + r.actionId, actionId: r.actionId, action: actionsById.value.get(r.actionId) || null, binding, status: binding ? statusOf(binding) : 'unconfigured' })
  }
  for (const b of bindings.value) {
    if (validActions.value.some(r => r.actionId === b.actionId)) continue
    out.push({ key: 'stale:' + String(b.id || b.actionId), actionId: String(b.actionId || ''), action: actionsById.value.get(b.actionId) || null, binding: b, status: 'stale' })
  }
  return out.sort((x, y) => (x.status === 'stale' ? 1 : 0) - (y.status === 'stale' ? 1 : 0))
})
const flowName = (id: string) => flows.value.find(f => f.id === id)?.name || id || ''
/** 接口地址列文本：按实现类型给出可读摘要（不做任何改写）。 */
function addressText(row: Row): string {
  const impl = implOf(row.binding)
  if (!Object.keys(impl).length) return ''
  if (isApiV2(impl)) return summaryText(impl)
  if (String(impl.kind || '') === 'api') return String(impl.path || '（未填写地址）')
  if (String(impl.kind || '') === 'flow') return '函数编排 · ' + (flowName(String(impl.flowId || '')) || '（未记录编排）')
  return '历史配置 · ' + String(impl.kind || '（未填写类型）')
}
const isReadonlyRow = (row: Row) => row.status === 'flow' || row.status === 'unknown'
const actionLabel = (row: Row) => !row.binding ? '配置接口' : row.status === 'stale' ? '查看失效配置' : isReadonlyRow(row) ? '查看' : '修改接口'
const rowHint = (row: Row) => row.status === 'stale' ? '关联失效：动作或对象关联不在引用版本中，配置保留不自动删除' : ''

// ── 打开态：编辑（draft）或只读查看（flow/未知实现/失效关联） ──
interface Meta { actionId: string; action: any | null; binding: any | null; bindingId: string; original: any; viewReason: '' | 'flow' | 'unknown' | 'stale' }
const meta = ref<Meta | null>(null)
const draft = ref<any>(null)
let baseline = ''
const editingOpen = computed(() => !!meta.value)
const editable = computed(() => !!draft.value)
const dirty = computed(() => editable.value && JSON.stringify(draft.value) !== baseline)
const editingAction = computed<any>(() => meta.value?.action || null)
const viewReason = computed(() => meta.value?.viewReason || '')

const formGuard = inject<FormGuardAPI | null>('form-guard', null)
const formSave = inject<FormSaveAPI | null>('form-save', null)
const message = ref(''), saving = ref(false)
const issues = ref<ApiIssues | null>(null)
const issueEl = ref<HTMLElement | null>(null)
const flows = ref<{ id: string; name: string }[]>([])
onMounted(async () => { try { flows.value = ((await listFlows()) as any).items || [] } catch { flows.value = [] } })

// ── 项目 API 凭据（元数据只有 id/name；密钥只写不读回） ──
const credentials = ref<{ id: string; name: string }[]>([])
const credentialsLoading = ref(false)
const credentialOpen = ref(false), credentialSaving = ref(false), credentialMessage = ref('')
const credentialDraft = ref({ name: '', secret: '' })
async function loadCredentials() {
  credentialsLoading.value = true
  try { credentials.value = ((await listApiCredentials(props.projectState.projectId)) as any)?.items || [] }
  catch { credentials.value = [] }
  finally { credentialsLoading.value = false }
}

// ── 引用版本 + 项目现状 → 校验/联动上下文（来源选项、继承类型都在契约模块里） ──
const ctx = computed<ApiContext>(() => apiContext(props.refState, props.projectState, props.objectType, meta.value?.actionId || '', credentials.value))
const sourceSelectOptions = computed(() => sourceOptions(ctx.value).map(o => ({
  value: o.value,
  label: o.disabled && o.reason ? o.label + '（' + o.reason + '）' : o.label,
  disabled: !!o.disabled,
})))

function openEditor(row: Row) {
  const impl: any = row.binding ? implOf(row.binding) : null
  const reason: Meta['viewReason'] = row.status === 'stale' ? 'stale' : row.status === 'flow' ? 'flow' : row.status === 'unknown' ? 'unknown' : ''
  meta.value = { actionId: row.actionId, action: row.action, binding: row.binding || null, bindingId: String(row.binding?.id || ''), original: impl, viewReason: reason }
  // 旧的 api 绑定（只有 path）也用 draftFrom 预填：用户保存即显式转成 schemaVersion=2
  draft.value = reason ? null : (impl && Object.keys(impl).length ? draftFrom(impl) : emptyApi())
  baseline = JSON.stringify(draft.value)
  issues.value = null; message.value = ''; credentialOpen.value = false; credentialMessage.value = ''; credentialDraft.value = { name: '', secret: '' }
  if (editable.value) void loadCredentials()
  void nextTick(() => dialogEl.value?.scrollTo({ top: 0 }))
}
function closeNow() { meta.value = null; draft.value = null; issues.value = null; message.value = ''; credentialOpen.value = false; credentialDraft.value = { name: '', secret: '' } }
/** 用户主动关闭（取消/背板/Esc）：有修改先确认放弃。 */
async function closeEditor() {
  if (dirty.value && !(await appConfirm({ message: '放弃尚未保存的接口配置？', title: '放弃修改', confirmLabel: '放弃并关闭', danger: true }))) return
  closeNow()
}
const guard = { isDirty: () => dirty.value, discard: closeNow }
defineExpose({ dirty: () => dirty.value, discard: closeNow, openEditor })

// Esc 与焦点：弹窗打开即把焦点移入卡片（Esc 由卡片上的 keydown 处理，
// 这样 AppSelect 展开时它自己的 Esc 会先 stopPropagation，不会被这里抢走）。
const dialogEl = ref<HTMLElement | null>(null), credDialogEl = ref<HTMLElement | null>(null)
watch(editingOpen, open => {
  emit('edit-state', open)
  if (open) { formGuard?.register(guard); void nextTick(() => dialogEl.value?.focus()) }
  else formGuard?.unregister(guard)
})
watch(credentialOpen, open => { if (open) void nextTick(() => credDialogEl.value?.focus()) })
onBeforeUnmount(() => { formGuard?.unregister(guard) })

// ── 参数表行内联动（切换来源/类型时清空不适用的 value 字段） ──
const BOOLEAN_OPTIONS = [{ value: 'true', label: '是' }, { value: 'false', label: '否' }]
const AUTH_IN_OPTIONS = [{ value: 'header', label: '请求头' }, { value: 'query', label: 'Query' }]
function addParam() { if (draft.value) draft.value.parameters.push({ id: newParamId(), name: '', in: 'body', value: { from: '' } }) }
function removeParam(index: number | string) { if (draft.value) draft.value.parameters.splice(Number(index), 1) }
function setName(param: ApiParam, event: Event) { param.name = (event.target as HTMLInputElement).value }
function setSource(param: ApiParam, from: string) {
  const value: any = { from }
  if (from === 'constant') { value.type = 'string'; value.value = '' }
  param.value = value as any
}
function setConstantType(param: ApiParam, type: string) {
  param.value = { from: 'constant', type, value: type === 'boolean' ? 'true' : '' } as any
}
function setConstantText(param: ApiParam, event: Event) {
  // 固定值保留用户输入原文；数值/布尔在保存时按类型归一（见 toImplementation）
  param.value.value = (event.target as HTMLInputElement).value
}
function setActionInput(param: ApiParam, key: string) {
  const hit = ctx.value.actionInputs.find(i => (i.id || i.name) === key)
  if (hit) param.value = hit.id ? ({ from: 'actionInput', inputId: hit.id } as any) : ({ from: 'actionInput', inputName: hit.name } as any)
  else param.value = { from: 'actionInput', inputName: key } as any
}
const actionInputKey = (param: ApiParam) => String(param.value.inputId || param.value.inputName || '')
function actionInputOptions(param: ApiParam) {
  const out: { value: string; label: string; disabled?: boolean }[] = ctx.value.actionInputs.map(i => ({ value: i.id || i.name, label: i.name }))
  const current = actionInputKey(param)
  if (current && !out.some(o => o.value === current)) out.push({ value: current, label: current + ' · 不在引用版本中', disabled: true })
  return [{ value: '', label: '请选择输入' }, ...out]
}
function setProperty(param: ApiParam, propertyId: string) { param.value = { from: 'property', propertyId } as any }
function propertyOptions(param: ApiParam) {
  // 只列能用的（标量 + 已配置来源）；当前引用已不可用时按只读项显示，避免直接丢失引用
  const out: { value: string; label: string; disabled?: boolean }[] = ctx.value.properties
    .filter(p => p.scalar && p.configured)
    .map(p => ({ value: p.id, label: p.name + '（' + p.typeLabel + '）' }))
  const current = String(param.value.propertyId || '')
  if (current && !out.some(o => o.value === current)) {
    const info = ctx.value.propertyById[current]
    const why = !info ? '不在引用版本中' : info.scalar ? '尚未在属性取值中配置来源' : '时间序列不能作为标量参数'
    out.unshift({ value: current, label: (info?.name || current) + ' · ' + why, disabled: true })
  }
  // 其余不可用属性也按原因列为禁用项（需求 §2：缺少映射/类型不支持时显示原因），
  // 避免用户只看到"少了几项"却不知道原因。
  for (const p of ctx.value.properties) {
    if ((p.scalar && p.configured) || p.id === current) continue
    out.push({ value: p.id, label: p.name + ' · ' + (p.scalar ? '尚未在「属性取值」中配置来源' : '时间序列不能作为标量参数'), disabled: true })
  }
  return [{ value: '', label: '请选择对象属性' }, ...out]
}
/** 当前来源被禁用的原因（显示在行下方；来源未选时不提示）。 */
function sourceReason(param: ApiParam): string {
  const from = String(param.value?.from || '')
  if (!from) return ''
  const opt = sourceOptions(ctx.value).find(o => o.value === from)
  if (!opt) return '取值来源不是本期的来源类型，请重新选择。'
  return opt.disabled ? (opt.reason || '') : ''
}
const hasBodyParam = computed(() => editable.value && draft.value.parameters.some((p: ApiParam) => p.in === 'body'))

// ── 认证 ──
function setAuthType(type: string) {
  if (!draft.value) return
  // 切换认证方式只改类型/位置字段，不清空也不删除已保存的凭据引用与 vault 内的密钥
  draft.value.auth.type = type
}
function credentialName(id: string) { return credentials.value.find(c => c.id === id)?.name || '' }
const credentialOptions = computed(() => {
  const out: { value: string; label: string; disabled?: boolean }[] = credentials.value.map(c => ({ value: c.id, label: c.name }))
  const current = String(draft.value?.auth?.credentialId || '')
  if (current && !out.some(o => o.value === current)) out.unshift({ value: current, label: '已保存的引用不在当前项目凭据中', disabled: true })
  return [{ value: '', label: '请选择凭据' }, ...out]
})
function openCredential() { credentialMessage.value = ''; credentialDraft.value = { name: '', secret: '' }; credentialOpen.value = true }
function closeCredential() { credentialOpen.value = false; credentialMessage.value = ''; credentialDraft.value = { name: '', secret: '' } }
async function submitCredential() {
  if (credentialSaving.value) return
  const name = credentialDraft.value.name.trim()
  const secret = credentialDraft.value.secret
  if (!name) { credentialMessage.value = '请填写凭据名称。'; return }
  if (!secret.trim()) { credentialMessage.value = '请填写密钥。'; return }
  credentialSaving.value = true; credentialMessage.value = ''
  try {
    const result: any = await saveApiCredential({ projectId: props.projectState.projectId, action: 'set', name, secret })
    await loadCredentials()
    const savedId = String(result?.credential?.id || '')
    if (savedId && draft.value) draft.value.auth.credentialId = savedId
    closeCredential()
  } catch (error: any) {
    credentialMessage.value = error?.message || '凭据保存失败'
  } finally { credentialSaving.value = false }
}

// ── 请求示意（纯文本，随字段变化联动；「更新示意」按钮沿用原型） ──
const previewTick = ref(0)
const previewText = computed(() => { void previewTick.value; return buildPreview(draft.value || emptyApi(), ctx.value) })
function refreshPreview() { previewTick.value++ }

// ── 保存：先校验（有错误不保存、不关弹窗），再走既有 form-save 一次持久化 ──
function normalizeValue(value: any): any {
  const out: any = { ...(value && typeof value === 'object' ? value : {}) }
  if (out.from === 'constant') {
    if (out.type === 'number') { const text = String(out.value ?? '').trim(); out.value = text !== '' && Number.isFinite(Number(text)) ? Number(text) : out.value }
    else if (out.type === 'boolean') out.value = out.value === true || String(out.value) === 'true'
    else if (out.value === null || out.value === undefined) out.value = ''
  }
  return out
}
/** 以原记录为底合并：kind/schemaVersion 与本期字段覆盖，roles/paramNotes 等未知字段零丢失。 */
function toImplementation(source: any, original: any): Record<string, any> {
  const base = original && typeof original === 'object' ? { ...original } : {}
  const oldParams = new Map<string, any>((Array.isArray(original?.parameters) ? original.parameters : [])
    .filter((r: any) => r && typeof r === 'object' && r.id).map((r: any) => [String(r.id), r]))
  return {
    ...base,
    kind: 'api',
    schemaVersion: 2,
    method: source.method,
    path: String(source.path || '').trim(),
    bodyFormat: source.bodyFormat,
    description: source.description,
    parameters: source.parameters.map((p: ApiParam) => ({
      ...(oldParams.get(String(p.id)) || {}),
      id: String(p.id),
      name: p.name,
      in: p.in,
      value: normalizeValue(p.value),
    })),
    auth: { ...source.auth },
  }
}
function commit(target: Meta, implementation: Record<string, any>) {
  const list = props.projectState.bindings.actionBindings = Array.isArray(props.projectState.bindings.actionBindings) ? props.projectState.bindings.actionBindings : []
  const hit = list.find((r: any) => r === Object(r) && bareType(r.objectTypeId) === bareType(props.objectType) && r.actionId === target.actionId)
  if (hit) hit.implementation = implementation
  else list.push({
    id: target.bindingId || (globalThis.crypto?.randomUUID?.().replaceAll('-', '') || 'ab' + Math.random().toString(36).slice(2)),
    objectTypeId: bareType(props.objectType), actionId: target.actionId, implementation,
  })
}
async function saveApi() {
  if (saving.value || !draft.value || !meta.value) return
  const check = validateApi(draft.value, ctx.value)
  issues.value = check
  message.value = ''
  if (check.errors.length) { await nextTick(); issueEl.value?.scrollIntoView({ block: 'center', behavior: 'smooth' }); return }
  saving.value = true
  const current = meta.value
  const implementation = toImplementation(draft.value, current.original)
  const result: any = formSave
    ? await formSave.submitForm('project', () => commit(current, implementation))
    : (before(), commit(current, implementation), changed(), { ok: true, message: '' })
  saving.value = false
  if (!result?.ok) { message.value = result?.message || '保存失败'; await nextTick(); issueEl.value?.scrollIntoView({ block: 'center', behavior: 'smooth' }); return }
  closeNow()
}
async function removeBinding(row: Row) {
  const binding: any = row.binding
  if (!binding) return
  const ok = await appConfirm({
    message: '移除这条失效绑定？只删除项目中的这条绑定配置，不影响本体动作定义与关联。',
    title: '移除失效绑定', confirmLabel: '移除', danger: true,
  })
  if (!ok) return
  mutate(() => {
    const list = props.projectState.bindings.actionBindings
    const index = list.indexOf(binding)
    if (index >= 0) list.splice(index, 1)
  })
}

// ── 只读查看（flow / 未知实现 / 失效关联）：按结构化字段展示，不做可编辑形态 ──
const implRaw = computed<any>(() => implOf(meta.value?.binding))
const viewEntries = computed<{ label: string; value: string }[]>(() => {
  const impl = implRaw.value
  if (!Object.keys(impl).length) return [{ label: '实现配置', value: '这条绑定没有实现配置。' }]
  const view = apiView(impl)
  const isApi = isApiV2(impl) || String(impl.kind || '') === 'api'
  const out: { label: string; value: string }[] = [{
    label: '实现方式',
    value: String(impl.kind || '') === 'flow' ? '绑定函数编排'
      : isApiV2(impl) ? 'HTTP 接口（schemaVersion 2）'
        : isApi ? 'HTTP 接口（旧格式，无 schemaVersion）' : '未知实现类型 ' + String(impl.kind || '（未填写）'),
  }]
  if (String(impl.kind || '') === 'flow') out.push({ label: '引用的函数编排', value: flowName(String(impl.flowId || '')) || '（未记录编排）' })
  if (isApi) {
    out.push({ label: '请求方式', value: view.method })
    out.push({ label: '接口地址', value: view.path || '（未填写地址）' })
    if (isApiV2(impl)) out.push({ label: '请求体格式', value: view.bodyFormat === 'form' ? '表单（application/x-www-form-urlencoded）' : 'JSON' })
  }
  if (view.description) out.push({ label: '接口说明', value: view.description })
  if (view.parameters.length) {
    out.push({
      label: '请求参数',
      value: view.parameters.map((p: ApiParam, i: number) => (i + 1) + '. ' + (String(p.name || '').trim() || '（未填写参数名）') + ' · '
        + (IN_LABELS[String(p.in || '')] || '未选位置') + ' · ' + (SOURCE_LABELS[String(p.value?.from || '')] || '未选来源')).join('\n'),
    })
  } else if (isApi) out.push({ label: '请求参数', value: '（无参数）' })
  if (isApiV2(impl)) {
    const auth = view.auth
    out.push({
      label: '认证方式',
      value: auth.type === 'none' ? '无认证'
        : auth.type === 'bearer' ? 'Bearer Token · 凭据引用 ' + (credentialName(auth.credentialId) || (auth.credentialId || '（未选择）'))
          : auth.type === 'apiKey' ? 'API Key · ' + (auth.name || '（未填写参数名）') + ' · ' + (auth.in === 'query' ? 'Query' : '请求头') + ' · 凭据引用 ' + (credentialName(auth.credentialId) || (auth.credentialId || '（未选择）'))
            : '未知认证方式 ' + (auth.type || '（未填写）'),
    })
  }
  const known = new Set(['kind', 'schemaVersion', 'method', 'path', 'bodyFormat', 'description', 'parameters', 'auth'])
  const extra = Object.keys(impl).filter(k => !known.has(k))
  if (extra.length) out.push({ label: '其他历史字段（只读保留）', value: extra.join('、') })
  return out
})
const viewRawJson = computed(() => JSON.stringify(implRaw.value, null, 2))
const viewNotice = computed(() => viewReason.value === 'flow'
  ? '函数编排绑定为历史配置，本期不再新建。以下为此绑定的历史配置，只读保留，不会被自动改写或删除。'
  : viewReason.value === 'unknown'
    ? '此绑定的实现类型不是本期的接口配置，只读保留，不会被自动改写或删除。'
    : '此动作或对象关联不在项目引用的本体版本中（关联失效）。配置保留，不会自动删除；如需重新配置，请先修复引用版本中的关联。')
</script>
<template>
<div class="ab-root">
  <div class="section-head"><div><h3>动作绑定 · {{ typeName }}</h3>
    <p class="muted">动作与关联来自项目引用的本体版本 {{ projectState.ontologyVersion }}；每个「对象类型＋动作」配置一个有效的 HTTP 接口实现，本期只做配置校验，不发送请求。</p></div></div>
  <table v-if="rows.length" class="ab-list">
    <thead><tr><th>动作名称</th><th>接口地址</th><th class="ab-ops-head">操作</th></tr></thead>
    <tbody>
      <tr v-for="row in rows" :key="row.key" :class="{ 'ab-stale': row.status === 'stale' }">
        <td>
          <strong>{{ row.action?.name || '动作不在引用版本中' }}</strong>
          <small class="muted ab-block">{{ row.action ? (row.action.effect || row.action.description || row.actionId) : row.actionId }}</small>
        </td>
        <td>
          <template v-if="row.status === 'unconfigured'"><span class="muted">尚未配置</span></template>
          <template v-else>
            <span>{{ addressText(row) }}</span>
            <small v-if="row.status === 'legacy'" class="muted ab-block">旧格式 · 编辑后可补全</small>
            <small v-else-if="rowHint(row)" class="ab-warn-text ab-block">{{ rowHint(row) }}</small>
          </template>
        </td>
        <td class="ab-tools">
          <button class="row-link" @click="openEditor(row)">{{ actionLabel(row) }}</button>
          <button v-if="row.status === 'stale'" class="row-link danger" @click="removeBinding(row)">移除</button>
        </td>
      </tr>
    </tbody>
  </table>
  <div v-else class="empty-state"><div class="empty-state-ico">◇</div><p>引用版本中此对象类型没有关联动作。请先在本体「对象建模 → 动作」页签关联并发布，再在项目中显式升级引用。</p></div>
  <p class="field-help">未配置动作不阻止发布；已配置但无效（如关联失效、地址或参数错误）会阻止发布。保存仅写项目草稿，不修改本体；配置完成不代表接口可用或动作可执行。</p>

  <!-- 接口配置弹窗（局部草稿：保存才写入，取消不变更） -->
  <div v-if="editingOpen" class="modal-backdrop" @click.self="closeEditor()">
    <section ref="dialogEl" tabindex="-1" class="modal-card ab-dialog" role="dialog" aria-modal="true" :aria-label="editable ? '配置接口 · ' + (editingAction?.name || meta?.actionId) : '查看历史配置'" @keydown.esc.stop="closeEditor()">
      <div class="ab-modalhead">
        <div>
          <span class="eyebrow">{{ editable ? '项目接口配置' : '只读查看' }}</span>
          <h2>{{ (editable ? '配置接口 · ' : '查看配置 · ') + (editingAction?.name || meta?.actionId || '') }}</h2>
        </div>
        <button type="button" @click="closeEditor()">关闭</button>
      </div>
      <div class="ab-modalbody">
        <!-- 1. 只读横幅：本体动作名称与业务定义 -->
        <div class="ab-banner">
          <strong>本体动作：{{ editingAction?.name || '动作不在引用版本中' }}</strong>
          <p v-if="editingAction?.effect">{{ editingAction.effect }}</p>
          <p v-if="editingAction?.description">{{ editingAction.description }}</p>
          <p v-if="!editingAction?.effect && !editingAction?.description" class="muted">本体未填写业务定义。</p>
          <small class="muted">来自项目引用的本体版本，只读；项目里只配置调用它的接口。</small>
        </div>

        <template v-if="editable">
          <!-- 2. 基本信息 -->
          <div class="form-grid">
            <Field label="请求方式" required type="select" :model-value="draft.method" :options="METHOD_OPTIONS" help="新建配置默认 POST。" @update:model-value="draft.method = $event"/>
            <Field label="接口地址" required class="full" :model-value="draft.path" example="https://ems.example.com/api/devices/{deviceId}/stop"
              help="填写含主机的完整地址（http/https）；路径占位符如 {deviceId} 需要在参数表里配同名的路径参数。地址不含账号密码或密钥。"
              @update:model-value="draft.path = $event"/>
            <Field v-if="hasBodyParam" label="请求体格式" required type="select" :model-value="draft.bodyFormat" :options="BODY_FORMAT_OPTIONS"
              help="表单指 application/x-www-form-urlencoded；请求体参数存在时可配置。" @update:model-value="draft.bodyFormat = $event"/>
            <Field label="接口说明" type="textarea" class="full" :model-value="draft.description" example="说明接口用途和返回含义"
              @update:model-value="draft.description = $event"/>
          </div>

          <!-- 3. 请求参数 -->
          <section class="ab-section">
            <div class="ab-sectionhead"><h3>请求参数</h3><button type="button" @click="addParam">＋ 添加参数</button></div>
            <p class="field-help">为接口的每个参数指定来源：动作输入、当前对象实例主键、当前对象属性或固定值。认证密钥在下方认证配置中引用，不填写在固定值里。</p>
            <div class="ab-tablewrap">
              <table class="ab-paramtable">
                <thead><tr><th>参数名</th><th>参数位置</th><th>取值来源</th><th>来源项 / 固定值</th><th>值类型</th><th class="ab-col-remove"></th></tr></thead>
                <tbody>
                  <template v-for="(param, index) in draft.parameters" :key="param.id">
                    <tr>
                      <td><input :value="param.name" aria-label="参数名" placeholder="例如 deviceId" @input="setName(param, $event)"></td>
                      <td><AppSelect :model-value="param.in" aria-label="参数位置" :options="IN_OPTIONS" @update:model-value="param.in = $event"/></td>
                      <td><AppSelect :model-value="param.value.from" aria-label="取值来源" :options="sourceSelectOptions" @update:model-value="setSource(param, $event)"/></td>
                      <td>
                        <span v-if="param.value.from === 'instanceId'" class="ab-readonly-text">当前对象的实例主键</span>
                        <AppSelect v-else-if="param.value.from === 'actionInput'" :model-value="actionInputKey(param)" aria-label="动作输入" :options="actionInputOptions(param)" @update:model-value="setActionInput(param, $event)"/>
                        <AppSelect v-else-if="param.value.from === 'property'" :model-value="String(param.value.propertyId || '')" aria-label="对象属性" :options="propertyOptions(param)" @update:model-value="setProperty(param, $event)"/>
                        <AppSelect v-else-if="param.value.from === 'constant' && String(param.value.type || '') === 'boolean'" :model-value="String(param.value.value ?? 'true')" aria-label="固定布尔值" :options="BOOLEAN_OPTIONS" @update:model-value="param.value.value = $event"/>
                        <input v-else-if="param.value.from === 'constant'" :value="param.value.value === null || param.value.value === undefined ? '' : String(param.value.value)"
                          :inputmode="String(param.value.type || '') === 'number' ? 'decimal' : 'text'" aria-label="固定值"
                          :placeholder="String(param.value.type || '') === 'number' ? '例如 0（0 也是有效固定值）' : '输入固定文本'" @input="setConstantText(param, $event)">
                        <span v-else class="muted">请先选择取值来源</span>
                      </td>
                      <td>
                        <AppSelect v-if="param.value.from === 'constant'" :model-value="String(param.value.type || '')" aria-label="固定值类型" :options="CONSTANT_TYPE_OPTIONS" @update:model-value="setConstantType(param, $event)"/>
                        <small v-else class="muted">{{ inheritedTypeText(param, ctx) }}</small>
                      </td>
                      <td class="ab-col-remove"><button type="button" class="row-link danger" @click="removeParam(index)">移除</button></td>
                    </tr>
                    <tr v-if="sourceReason(param)" class="ab-hintrow"><td colspan="6"><span class="ab-warn-text">该来源当前不可用：{{ sourceReason(param) }}（已选来源保留显示，请改选其他来源或先补齐前提配置）</span></td></tr>
                  </template>
                  <tr v-if="!draft.parameters.length"><td colspan="6" class="ab-empty">尚未添加参数。无参接口可以直接保存。</td></tr>
                </tbody>
              </table>
            </div>
          </section>

          <!-- 4. 认证配置（默认折叠） -->
          <details class="ab-details" :open="draft.auth.type !== 'none'">
            <summary>认证配置（选填）</summary>
            <div class="form-grid">
              <Field label="认证方式" type="select" :model-value="draft.auth.type" :options="AUTH_TYPE_OPTIONS" help="默认无认证；密钥保存在受保护凭据库，项目配置只记录凭据引用。" @update:model-value="setAuthType($event)"/>
              <div v-if="draft.auth.type !== 'none'" class="ab-cred-field">
                <Field v-if="credentials.length" label="凭据引用" required type="select" :model-value="draft.auth.credentialId" :options="credentialOptions"
                  example="选择当前项目已登记的 API 凭据" @update:model-value="draft.auth.credentialId = $event"/>
                <template v-else>
                  <span class="field-title">凭据引用 <b class="required-mark">*</b></span>
                  <p v-if="credentialsLoading" class="field-help">正在读取项目凭据…</p>
                  <p v-else class="field-help">当前项目还没有 API 凭据。登记后这里会列出可引用的凭据名称。</p>
                </template>
                <button type="button" class="row-link" :disabled="credentialsLoading" @click="openCredential()">登记凭据</button>
              </div>
              <template v-if="draft.auth.type === 'apiKey'">
                <Field label="参数位置" type="select" :model-value="draft.auth.in || 'header'" :options="AUTH_IN_OPTIONS" @update:model-value="draft.auth.in = $event"/>
                <Field label="参数名称" required :model-value="draft.auth.name" example="X-API-Key" help="承载密钥的请求头或 Query 参数名。" @update:model-value="draft.auth.name = $event"/>
              </template>
            </div>
            <p class="field-help">切换认证方式不会删除已保存的凭据引用，也不会改动凭据库中的密钥。</p>
          </details>

          <!-- 5. 请求示意 -->
          <section class="ab-section">
            <div class="ab-sectionhead"><h3>请求示意</h3><button type="button" @click="refreshPreview">更新示意</button></div>
            <p class="field-help">仅展示请求结构，变量保留占位符，认证值隐藏，不发送请求。</p>
            <pre class="ab-request">{{ previewText }}</pre>
          </section>
        </template>

        <!-- 只读查看：历史 flow / 未知实现 / 失效关联 -->
        <template v-else>
          <p class="ab-notice">{{ viewNotice }}</p>
          <dl class="ab-readonly">
            <template v-for="entry in viewEntries" :key="entry.label">
              <dt>{{ entry.label }}</dt><dd class="ab-pre">{{ entry.value }}</dd>
            </template>
          </dl>
          <details class="ab-details">
            <summary>完整历史配置（只读）</summary>
            <pre class="ab-request">{{ viewRawJson }}</pre>
          </details>
        </template>

        <!-- 6. 错误区（校验错误逐条展示；警告提示但允许保存） -->
        <div ref="issueEl" class="ab-issues" role="alert">
          <p v-for="(item, i) in (issues?.errors || [])" :key="'e' + i" class="inline-error">{{ item }}</p>
          <p v-for="(item, i) in (issues?.warnings || [])" :key="'w' + i" class="inline-warning">{{ item }}</p>
          <p v-if="message" class="inline-error">{{ message }}</p>
        </div>
      </div>
      <div class="ab-modalfoot">
        <span v-if="editable" class="ab-footnote">保存仅写项目草稿；本期只做配置校验，不发送请求，不存放密钥。</span>
        <button type="button" @click="closeEditor()">{{ editable ? '取消' : '关闭' }}</button>
        <button v-if="editable" type="button" class="primary" :disabled="saving" @click="saveApi()">{{ saving ? '保存中…' : '保存' }}</button>
      </div>
    </section>
  </div>

  <!-- 登记凭据小弹窗（密钥只写受保护凭据库，不回显、不进项目配置） -->
  <div v-if="credentialOpen" class="modal-backdrop" @click.self="closeCredential()">
    <section ref="credDialogEl" tabindex="-1" class="modal-card ab-cred-dialog" role="dialog" aria-modal="true" aria-label="登记 API 凭据" @keydown.esc.stop="closeCredential()">
      <h2>登记 API 凭据</h2>
      <label class="ab-cred-label">名称 <b class="required-mark">*</b>
        <input v-model="credentialDraft.name" autocomplete="off" placeholder="例如：EMS 控制服务凭据">
      </label>
      <label class="ab-cred-label">密钥 <b class="required-mark">*</b>
        <input v-model="credentialDraft.secret" type="password" autocomplete="new-password" placeholder="仅保存到受保护凭据，不回显">
      </label>
      <p class="field-help">密钥写入本项目的受保护凭据库（文件权限 0600），不进入项目配置、版本快照、日志或导出；这里只登记名称与引用。</p>
      <p v-if="credentialMessage" class="inline-error" role="alert">{{ credentialMessage }}</p>
      <div class="dialogtools"><button type="button" @click="closeCredential()">取消</button>
        <button type="button" class="primary" :disabled="credentialSaving" @click="submitCredential()">{{ credentialSaving ? '保存中…' : '保存凭据' }}</button></div>
    </section>
  </div>
</div>
</template>
<style scoped>
.ab-root .section-head{margin-bottom:10px}
.ab-root h3{margin:0 0 4px;font-size:16px}
.ab-block{display:block}
.muted{color:var(--muted)}
.ab-list th:nth-child(1){width:34%}
.ab-list th:nth-child(2){width:50%}
.ab-ops-head,.ab-tools{text-align:right}
.ab-tools{white-space:nowrap}
.ab-tools .row-link{margin-left:10px}
/* 全局 .danger 自带 margin-top，会顶偏表格内的「移除」 */
.ab-root .row-link.danger{margin-top:0;font-size:13px}
.ab-stale td{background:var(--warn-soft)}
.ab-warn-text{font-size:12px;color:var(--warn)}
/* 弹窗：宽度 1160px（参数表需要横向空间），头/脚 sticky，卡片自身滚动 */
.ab-dialog{width:min(1160px,95vw);max-height:92vh;padding:0;overflow:auto}
.ab-modalhead{position:sticky;top:0;z-index:3;background:var(--paper);border-bottom:1px solid var(--line);padding:16px 24px;display:flex;align-items:flex-start;justify-content:space-between;gap:16px}
.ab-modalhead h2{font-size:17px;margin:4px 0 0}
.ab-modalbody{padding:18px 24px 22px}
.ab-modalfoot{position:sticky;bottom:0;z-index:3;background:var(--paper);border-top:1px solid var(--line);padding:12px 24px;display:flex;align-items:center;justify-content:flex-end;gap:10px;flex-wrap:wrap}
.ab-footnote{flex:1;min-width:220px;font-size:12px;color:var(--muted)}
.ab-banner{background:var(--blue-soft);border:1px solid var(--blue-line);border-radius:var(--r-sm);padding:12px 15px;margin-bottom:16px}
.ab-banner strong{display:block}
.ab-banner p{margin:6px 0;font-size:13px;line-height:1.7;color:var(--ink-2)}
.ab-banner small{display:block;margin-top:6px}
.ab-notice{background:var(--warn-soft);border:1px solid var(--warn-line);border-radius:var(--r-sm);padding:12px 15px;font-size:13px;color:var(--warn);margin:0 0 16px;line-height:1.7}
.ab-section{border-top:1px solid var(--line);margin-top:22px;padding-top:16px}
.ab-sectionhead{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-bottom:6px}
.ab-tablewrap{overflow:auto;border:1px solid var(--line);border-radius:var(--r-sm)}
.ab-paramtable{min-width:900px;table-layout:fixed}
.ab-paramtable th:nth-child(1){width:16%}
.ab-paramtable th:nth-child(2){width:14%}
.ab-paramtable th:nth-child(3){width:21%}
.ab-paramtable th:nth-child(4){width:25%}
.ab-paramtable th:nth-child(5){width:16%}
.ab-col-remove{width:8%;text-align:right;white-space:nowrap}
.ab-paramtable td{padding:8px 6px;vertical-align:top}
.ab-paramtable td input{margin:0;min-height:41px}
.ab-paramtable .app-select{margin-top:0}
.ab-paramtable .app-select :deep(.app-select-trigger){min-height:41px}
.ab-hintrow td{background:var(--warn-soft);padding-top:0}
.ab-empty{text-align:center;color:var(--muted);padding:22px 10px}
.ab-readonly-text{display:block;min-height:41px;padding:10px 11px;border:1px solid var(--line);border-radius:var(--r-sm);background:var(--paper-2);color:var(--muted);font-size:13px}
.ab-details{border:1px solid var(--line);border-radius:var(--r-sm);padding:12px 15px;margin-top:22px}
.ab-details summary{cursor:pointer;font-size:13px;color:var(--muted);padding:4px 0}
.ab-details .form-grid{margin-top:10px}
.ab-cred-field{min-width:0;padding-top:14px}
.ab-cred-field .field-title{display:flex;align-items:center;gap:7px;font-weight:600;font-size:14px;color:var(--ink)}
.ab-cred-field .row-link{margin-top:8px}
.ab-dialog :deep(.form-grid .editor-field.full){grid-column:1/-1}
.ab-request{white-space:pre-wrap;overflow-wrap:anywhere;background:var(--paper-2);border:1px solid var(--line);border-radius:var(--r-sm);padding:16px;font:13px/1.8 ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--ink);max-height:320px;overflow:auto;margin:6px 0 0}
.ab-issues{margin-top:18px;scroll-margin-top:80px}
.ab-issues p{margin:6px 0;line-height:1.7}
.ab-readonly{display:grid;grid-template-columns:150px minmax(0,1fr);margin:0;font-size:13px}
.ab-readonly dt,.ab-readonly dd{margin:0;padding:9px 0;border-bottom:1px solid var(--paper-3);overflow-wrap:anywhere}
.ab-readonly dt{color:var(--muted)}
.ab-pre{white-space:pre-wrap}
.ab-cred-dialog{width:min(460px,95vw)}
.ab-cred-label{display:block;margin:16px 0 0;font-size:14px;font-weight:600;color:var(--ink)}
.ab-cred-label input{display:block;width:100%;margin-top:7px;font-weight:400}
.ab-cred-dialog h2{margin:0}
</style>
