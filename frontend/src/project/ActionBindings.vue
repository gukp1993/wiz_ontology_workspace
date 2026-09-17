<!-- 动作绑定（20260917 需求 §3.3）：项目对象映射内的「动作绑定」页签。
     有效动作与关联只来自项目固定引用的已发布本体版本（refState），绝不读本体最新草稿。
     按「项目（存储位置）＋对象类型＋动作」保存 bindings.actionBindings，组合唯一；
     实现方式 = 调用项目接口 | 绑定函数编排（引用既有编排，不新建编辑器）；
     项目角色与参数映射是说明性文本。配置完成只显示「已配置 · 未执行验证」，无真实执行。
     升级引用后失效的绑定保留并标「关联失效」，可查看只读详情后确认移除，不自动删除。 -->
<script setup lang="ts">
import { computed, inject, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import AppSelect from '../shared/AppSelect.vue'
import Field from '../shared/EditorField.vue'
import { actionBindingsOf, actionsOf, effectiveAssociations } from '../ontology/actionModel'
import { listFlows } from '../flow/api'
import type { FormGuardAPI, FormSaveAPI } from '../app/formGuard'

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

type Row = { actionId: string; action: any | null; binding: any | null; status: 'configured' | 'pending' | 'stale' }
const rows = computed<Row[]>(() => {
  const out: Row[] = []
  for (const r of validActions.value) {
    const binding = bindingFor(r.actionId) || null
    out.push({ actionId: r.actionId, action: actionsById.value.get(r.actionId) || null, binding, status: binding ? 'configured' : 'pending' })
  }
  for (const b of bindings.value) {
    if (validActions.value.some(r => r.actionId === b.actionId)) continue
    out.push({ actionId: b.actionId, action: actionsById.value.get(b.actionId) || null, binding: b, status: 'stale' })
  }
  return out.sort((x, y) => (x.status === 'stale' ? 1 : 0) - (y.status === 'stale' ? 1 : 0))
})

// ── 绑定编辑（局部草稿，保存走 form-save 直通；打开期间注册 T00 表单守卫） ──
const formGuard = inject<FormGuardAPI | null>('form-guard', null)
const formSave = inject<FormSaveAPI | null>('form-save', null)
const draft = ref<any>(null)
let baseline = ''
const message = ref(''), saving = ref(false)
const flows = ref<{ id: string; name: string }[]>([])
const flowOptions = computed(() => flows.value.map(f => ({ value: f.id, label: f.name || f.id })))
onMounted(async () => { try { flows.value = ((await listFlows()) as any).items || [] } catch { flows.value = [] } })

const editingOpen = computed(() => draft.value !== null)
const dirty = computed(() => editingOpen.value && JSON.stringify(draft.value) !== baseline)
const guard = { isDirty: () => dirty.value, discard: () => closeEditor() }
watch(editingOpen, open => { emit('edit-state', open); open ? formGuard?.register(guard) : formGuard?.unregister(guard) })
onBeforeUnmount(() => formGuard?.unregister(guard))

const editingAction = computed(() => draft.value ? actionsById.value.get(draft.value.actionId) || null : null)
function implSummary(b: any): string {
  const impl = b?.implementation || {}
  const mode = impl.kind === 'flow' ? '绑定函数编排' : impl.kind === 'api' ? '调用项目接口' : String(impl.kind || '未选择')
  const target = impl.kind === 'flow' ? (flows.value.find(f => f.id === impl.flowId)?.name || impl.flowId || '未选择') : (impl.path || '未填写')
  return mode + ' · ' + target + (impl.roles ? ' · 角色：' + impl.roles : '')
}
function openEditor(actionId: string) {
  const existing = bindingFor(actionId)
  const impl = { ...(existing?.implementation || {}) }
  draft.value = {
    id: existing?.id || '', actionId,
    kind: impl.kind === 'flow' ? 'flow' : 'api',
    path: String(impl.path || ''), flowId: String(impl.flowId || ''),
    roles: String(impl.roles || ''), paramNotes: String(impl.paramNotes || ''),
    original: existing?.implementation || null,
  }
  baseline = JSON.stringify(draft.value)
  message.value = ''
}
function closeEditor() { draft.value = null }
async function saveBinding() {
  if (saving.value || !draft.value) return
  const d = draft.value
  if (d.kind === 'api' && !d.path.trim()) { message.value = '请填写项目接口路径。'; return }
  if (d.kind === 'flow' && !d.flowId) { message.value = '请选择函数编排。'; return }
  saving.value = true
  // implementation 以旧记录为底：切换方式时保留另一方式的字段值（零丢失），仅覆盖当前方式必填项
  const original = (d.original && typeof d.original === 'object') ? { ...d.original } : {}
  delete original.kind
  const implementation = { ...original, kind: d.kind }
  if (d.kind === 'api') implementation.path = d.path.trim()
  else implementation.flowId = d.flowId
  if (d.roles.trim()) implementation.roles = d.roles.trim()
  else delete implementation.roles
  if (d.paramNotes.trim()) implementation.paramNotes = d.paramNotes.trim()
  else delete implementation.paramNotes
  const ok = await (async () => {
    const r = formSave
      ? await formSave.submitForm('project', () => commit(d, implementation))
      : (before(), commit(d, implementation), changed(), { ok: true, message: '' })
    if (!r.ok) { message.value = r.message; return false }
    return true
  })()
  saving.value = false
  if (ok) closeEditor()
}
function commit(d: any, implementation: Record<string, any>) {
  const rows = props.projectState.bindings.actionBindings = Array.isArray(props.projectState.bindings.actionBindings) ? props.projectState.bindings.actionBindings : []
  const hit = rows.find((r: any) => r === Object(r) && bareType(r.objectTypeId) === bareType(props.objectType) && r.actionId === d.actionId)
  if (hit) hit.implementation = implementation
  else rows.push({ id: d.id || crypto.randomUUID().replaceAll('-', ''), objectTypeId: bareType(props.objectType), actionId: d.actionId, implementation })
}
async function removeBinding(row: Row) {
  const b: any = row.binding
  if (!b) return
  if (!confirm('移除这条失效绑定？只删除项目中的这条绑定配置，不影响本体动作定义与关联。')) return
  mutate(() => {
    const rows = props.projectState.bindings.actionBindings
    const index = rows.indexOf(b)
    if (index >= 0) rows.splice(index, 1)
  })
}
function switchKind(kind: string) { if (draft.value) draft.value.kind = kind === 'flow' ? 'flow' : 'api' }
</script>
<template>
<div class="ab-root">
  <div class="section-head"><div><h3>动作绑定 · {{ typeName }}</h3>
    <p class="muted">动作与关联来自项目引用的本体版本 {{ projectState.ontologyVersion }}；每个「对象类型＋动作」单独绑定实现，互不覆盖。</p></div></div>
  <table v-if="rows.length">
    <thead><tr><th>动作</th><th>业务效果</th><th>状态</th><th></th></tr></thead>
    <tbody>
      <tr v-for="row in rows" :key="row.actionId" :class="{ 'ab-stale': row.status === 'stale' }">
        <td><strong>{{ row.action?.name || '动作不在引用版本中' }}</strong><small class="muted" style="display:block">{{ row.action?.description || row.actionId }}</small></td>
        <td class="ab-effect">{{ row.action?.effect || '—' }}</td>
        <td><span v-if="row.status === 'configured'" class="status-pill ok-pill">已配置 · 未执行验证</span>
          <span v-else-if="row.status === 'pending'" class="tag">待绑定</span>
          <span v-else class="status-pill stale-pill">关联失效 · 历史配置保留</span></td>
        <td class="ab-tools">
          <button v-if="row.status !== 'stale'" class="row-link" @click="openEditor(row.actionId)">{{ row.status === 'configured' ? '修改绑定' : '配置绑定' }}</button>
          <template v-else>
            <button class="row-link" @click="openEditor(row.actionId)">查看失效配置</button>
            <button class="row-link danger" @click="removeBinding(row)">移除</button>
          </template>
        </td>
      </tr>
    </tbody>
  </table>
  <div v-else class="empty-state"><div class="empty-state-ico">◇</div><p>引用版本中此对象类型没有关联动作。请先在本体「对象建模 → 动作」页签关联并发布，再在项目中显式升级引用。</p></div>
  <p class="field-help">未绑定动作不阻止发布；已填写的绑定若关联失效会阻止发布。保存仅写项目草稿，不修改本体；配置完成不代表已具备执行能力。</p>

  <!-- 绑定表单（局部草稿：保存才写入，取消不变更） -->
  <section v-if="draft" class="card ab-editor">
    <div class="detail-heading"><div><span class="eyebrow">项目实现</span><h3>{{ typeName }} / {{ editingAction?.name || draft.actionId }}</h3></div>
      <button v-if="editingAction" @click="closeEditor">← 返回绑定列表</button><button v-else @click="closeEditor">关闭</button></div>
    <div v-if="editingAction" class="preview ab-def"><strong>{{ editingAction.name }}</strong><p class="pre">{{ editingAction.effect || editingAction.description || '' }}</p>
      <small class="muted">以上为本体定义（引用版本），具体接口与参数转换在项目中配置。</small></div>
    <p v-if="!editingAction" class="inline-warning">此动作不在项目引用版本中（关联失效）；仅可查看或移除历史配置。</p>
    <div class="form-grid">
      <label>实现方式 *<AppSelect :model-value="draft.kind" aria-label="实现方式" :options="[{ value: 'api', label: '调用项目接口' }, { value: 'flow', label: '绑定函数编排' }]" @update:model-value="switchKind($event)"/></label>
      <Field label="允许操作的项目角色" :model-value="draft.roles" example="例如：设备运维人员" help="维护项目权限要求说明；未接入账号权限系统时只是说明文本。" @update:model-value="draft.roles = $event"/>
      <template v-if="draft.kind === 'api'">
        <Field label="项目接口路径" class="full" :model-value="draft.path" required example="/api/devices/{id}/stop" help="本期作为配置说明；请求方法、认证与执行链路由项目实施确认。" @update:model-value="draft.path = $event"/>
      </template>
      <template v-else>
        <label class="full">函数编排 *<AppSelect :model-value="draft.flowId" aria-label="选择函数编排" :options="[{ value: '', label: flows.length ? '请选择编排' : '没有可用编排' }, ...flowOptions]" @update:model-value="draft.flowId = $event"/>
          <small class="muted">引用项目可用的既有函数编排；不在这里新建编排。</small></label>
      </template>
      <Field label="接口／函数参数映射（按需）" type="textarea" class="full" :model-value="draft.paramNotes" example="storageId ← 当前操作设备的项目标识；power ← 固定值 0。无参数接口可留空。" help="说明参数来源：当前操作对象、映射字段、固定值或执行时补充的信息；不生成机器可执行的签名。" @update:model-value="draft.paramNotes = $event"/>
    </div>
    <p v-if="message" class="inline-error" role="alert">{{ message }}</p>
    <div class="detail-footer"><div class="tools"><button class="primary" :disabled="saving" @click="saveBinding">{{ saving ? '保存中…' : '保存绑定' }}</button><button @click="closeEditor">取消</button></div>
      <span>保存仅写项目草稿；不存放密码或 token。</span></div>
  </section>
</div>
</template>
<style scoped>
.ab-root .section-head{margin-bottom:10px}
.ab-root h3{margin:0 0 4px;font-size:16px}
.ab-effect{max-width:420px;white-space:pre-wrap;font-size:13px;color:var(--ink-2)}
.ab-tools{white-space:nowrap;text-align:right}
.ab-tools .row-link{margin-left:10px}
.ab-stale td{background:var(--warn-soft)}
.stale-pill{background:var(--warn-soft);color:var(--warn)}
.ok-pill{background:var(--ok-soft);color:var(--ok)}
.ab-def{margin-bottom:14px}
.ab-def p{margin:6px 0}
.pre{white-space:pre-wrap}
.ab-editor{margin-top:14px}
</style>
