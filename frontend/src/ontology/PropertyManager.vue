<!-- ─── 独立属性表单（T03/D02/D04 对齐原型 editorView 的 property/shared 分支）───
     无目录、无对象切换条、无属性列表与批量按钮：编辑态由父级整体替换主内容后挂载本表单。
     挂载点：ObjectWorkspace（对象属性编辑/新建）与 SharedLibrary（维护/新建共享定义）。
     props：
       state       — 工作台状态（@graph + 格式化预览 API 使用）；
       kind        — 'property'（对象属性）| 'shared'（共享属性库定义）；
       targetTypeId — kind='property' 时归属对象类型 @id（新建与同名检查用）；
       propertyId  — 编辑目标 @id；空 = 新建（保存前不写入 graph，取消不产生记录）。
     emits：
       close — 取消/返回：丢弃本地草稿，父级关闭编辑态（原型行为，无确认框）；
       saved — 保存成功：{ id, kind, targetTypeId }，父级关闭编辑态并定位条目。
     保存约定（T00 契约，见 app/formGuard.ts）：本地草稿 + form-guard 离开保护 +
       form-save.submitForm('ontology', mutate) 一次落盘；!r.ok 留在表单显示 r.message，
       输入不丢；编辑期间绝不 emit('changed')（不走打字自动保存）。
     共享引用属性（mg:sharedProperty）：表单只读 + 「打开共享定义」（就地切换为维护
       共享定义，带引用影响提示，原型 edit-shared）与「转为私有」（detachProperty，
       经 form-save 落盘）；「转为共享属性」（asShared）保留在更多设置中。
     类型保护：数据类型下拉用 editorModel.dataTypeOptionsFor（常用四类 + 数组/结构体），
       已有 integer/dateTime 等精确类型打开再保存不改写；基础类型变化仅清理与旧类型
       绑定的配置（valueType/formatting/valueShape/decimalPlaces），与旧编辑器一致。 -->
<script setup lang="ts">
import { ref, computed, inject, onBeforeUnmount } from 'vue'
import Field from '../shared/EditorField.vue'
import PropertyFormatting from './PropertyFormatting.vue'
import { dataTypeOptionsFor } from './editorModel'
import { makeProperty, effectiveProperty, localProperties, detachProperty, asShared, propertyDataType, setPropertyDataType, referencesOf } from './propertyModel'
import { impactFingerprint, sharedImpactOf, type SharedImpact } from './dependencyModel'
import type { FormGuardAPI, FormSaveAPI } from '../app/formGuard'

const props = withDefaults(defineProps<{ state: any; kind: 'property' | 'shared'; targetTypeId?: string; propertyId?: string; canvasReturn?: string }>(), { targetTypeId: '', propertyId: '', canvasReturn: '' })
const emit = defineEmits<{ close: []; 'back-to-graph': []; saved: [payload: { id: string; kind: 'property' | 'shared'; targetTypeId?: string }] }>()

const guardApi = inject<FormGuardAPI>('form-guard')!
const formSave = inject<FormSaveAPI>('form-save')!

const graph = computed(() => props.state?.ontology?.['@graph'] || [])
const propertyNode = computed(() => props.propertyId ? graph.value.find((n: any) => n['@id'] === props.propertyId) : null)
const sharedRefId = computed(() => propertyNode.value?.['mg:sharedProperty']?.['@id'] || '')
// 就地维护共享定义（原型 edit-shared）：编辑目标切换为共享定义节点；取消仍整体退出表单。
const editingShared = ref(false)
const isNew = computed(() => !props.propertyId)
const readonly = computed(() => props.kind === 'property' && !!sharedRefId.value && !editingShared.value)

// ── 本地草稿：编辑对象节点的深拷贝（PropertyFormatting 在草稿上原位改动），保存时按受控键合入真实节点 ──
const draft = ref<any>(null)
let original = ''
// 表单受控键：合入时“草稿有则覆盖、无则删除”，其余未知字段/稳定 ID 原样保留。
const MANAGED = ['rdfs:label', 'rdfs:comment', 'rdfs:range', 'mg:valueSuffix', 'mg:decimalPlaces', 'mg:valueShape', 'mg:formatting']
const clone = (x: any) => JSON.parse(JSON.stringify(x))
function initDraft() {
  const src = editingShared.value
    ? graph.value.find((n: any) => n['@id'] === sharedRefId.value)
    : readonly.value ? effectiveProperty(propertyNode.value, graph.value)
      : propertyNode.value
  draft.value = src ? clone(src) : makeProperty({ name: '', type: 'string' }, props.kind === 'property' ? props.targetTypeId : null)
  original = JSON.stringify(draft.value)
}
initDraft()

// ── T00 离开保护：草稿相对打开时快照有变化才拦截导航/刷新 ──
const guard = { isDirty: () => !!draft.value && JSON.stringify(draft.value) !== original, discard: () => emit('close') }
guardApi.register(guard)
onBeforeUnmount(() => guardApi.unregister(guard))

// ── 表单字段 ──
const rangeId = computed(() => draft.value?.['rdfs:range']?.['@id'] || 'xsd:string')
const selectedType = computed(() => propertyDataType(draft.value).type === 'timeSeries' ? 'timeSeries' : rangeId.value)
const typeOptions = computed(() => [...dataTypeOptionsFor(rangeId.value), {value:'timeSeries',label:'时间序列'}])
const observationOptions = computed(() => dataTypeOptionsFor(rangeId.value).filter(o=>!['xsd:array','xsd:struct'].includes(o.value)))
function setLabel(v: string) { if (draft.value) draft.value['rdfs:label'] = v }
function setComment(v: string) { if (draft.value) draft.value['rdfs:comment'] = v }
function setRange(v: string) {
 const d=draft.value;if(!d||v===selectedType.value)return
 const base=propertyDataType(d).type==='timeSeries'?rangeId.value.replace('xsd:',''):'double'
 setPropertyDataType(d,v==='timeSeries'?{type:'timeSeries',valueType:base}:{type:v.replace('xsd:','')})
 delete d['mg:valueType'];delete d['mg:formatting']
 if(!['xsd:double','xsd:decimal','xsd:integer'].includes(rangeId.value))delete d['mg:decimalPlaces']
}
function setObservation(v:string){
 if(!draft.value||v===rangeId.value)return
 setPropertyDataType(draft.value,{type:'timeSeries',valueType:v.replace('xsd:','')})
 delete draft.value['mg:valueType'];delete draft.value['mg:formatting']
 if(!['xsd:double','xsd:decimal','xsd:integer'].includes(v))delete draft.value['mg:decimalPlaces']
}
// 显示名称不再经属性表单标记：用户约定单独建“显示名称”属性并绑定名称列；
// 已有 mg:isDisplayName 标记数据与项目侧推导不受影响（保存时不改动标记）。

// ── 共享定义高影响保存确认（20260920 需求 11）──────────────────────────────────
// 仅「数据类型/观测值类型/业务定义」等影响含义的变化需要确认；名称/显示格式变化直接保存。
// 确认绑定「编辑内容 + 当前引用集合」指纹：编辑再变或引用变化都会失效，必须重新确认（不缓存永久布尔）。
const impactOpen = ref(false)
const impact = ref<SharedImpact | null>(null)
const impactAck = ref(false)
const impactError = ref('')
let impactFp = ''

function currentImpact(): SharedImpact | null {
  const id = editingShared.value ? sharedRefId.value : (props.kind === 'shared' ? props.propertyId : '')
  if (!id) return null
  const before = graph.value.find((n: any) => n['@id'] === id)
  if (!before) return null
  return sharedImpactOf(props.state, id, before, draft.value)
}
// 编辑期间引用集合变化（其他标签页/撤销）会让既有确认失效：指纹比对。
function fingerprintNow(id: string): string { return impactFingerprint(props.state, id, draft.value) }

function openImpact(info: SharedImpact, id: string) {
  impact.value = info
  impactAck.value = false
  impactError.value = ''
  impactFp = fingerprintNow(id)
  impactOpen.value = true
}

function confirmImpact() {
  const id = editingShared.value ? sharedRefId.value : (props.kind === 'shared' ? props.propertyId : '')
  if (!id || !impact.value) return
  if (!impactAck.value) { impactError.value = '请先勾选「已查看字段变化与受影响对象」。'; return }
  if (fingerprintNow(id) !== impactFp) {  // 内容或引用已变化：确认失效
    impactError.value = '当前编辑内容或引用关系已变化，本次确认已失效；请重新核对后再次确认。'
    impact.value = currentImpact()
    impactAck.value = false
    impactFp = fingerprintNow(id)
    return
  }
  impactOpen.value = false
  void doSave()
}
function cancelImpact() { impactOpen.value = false; impactError.value = ''; impactAck.value = false }

// ── 文案与上下文 ──
const error = ref(''), saving = ref(false)
const typeName = (id: string) => graph.value.find((n: any) => n['@type'] === 'owl:Class' && n['@id'] === id)?.['rdfs:label'] || id || '—'
const contextName = computed(() => props.kind === 'property' ? typeName(props.targetTypeId) : '共享属性库')
const title = computed(() => {
  if (readonly.value) return '共享引用属性'
  if (editingShared.value || props.kind === 'shared') return isNew.value && props.kind === 'shared' ? '新建共享属性' : '维护共享定义'
  return isNew.value ? '新增属性' : '维护 · ' + (draft.value?.['rdfs:label'] || '未命名属性')
})

// 引用影响：共享定义被多少对象属性引用（修改前如实展示，不靠解除引用规避检查）。
const usageCount = computed(() => {
  const id = props.kind === 'shared' ? props.propertyId : sharedRefId.value
  return id ? referencesOf(graph.value, id).length : 0
})
const usageText = computed(() => usageCount.value ? `当前被 ${usageCount.value} 个对象属性引用，修改会同步到全部引用` : '当前没有对象引用此定义')

// ── 共享引用动作（沿用 effective/attach/detach 逻辑；经 form-save 落盘，成功后表单就位） ──
function openSharedDef() { editingShared.value = true; initDraft() }
async function runSave(mutate: () => void, after?: () => void) {
  if (saving.value) return false
  saving.value = true; error.value = ''
  const r = await formSave.submitForm('ontology', mutate, {
    actionLabel: (isNew.value ? '新建' : '修改') + (props.kind === 'shared' ? '共享属性「' : '属性「') + (draft.value?.['rdfs:label'] || '') + '」',
    target: { kind: 'property', id: String(props.propertyId || draft.value?.['@id'] || '') },
  })
  saving.value = false
  if (!r.ok) { error.value = r.message; return false }
  after?.(); return true
}
async function detach() {
  if (!propertyNode.value) return
  const ok = await runSave(() => { detachProperty(propertyNode.value, graph.value) })
  if (ok) { editingShared.value = false; initDraft() }  // 已拷贝完整共享内容为本地定义，可继续编辑
}
async function toShared() {
  if (!propertyNode.value) return
  const ok = await runSave(() => { asShared(propertyNode.value, graph.value) })
  if (ok) { editingShared.value = false; initDraft() }  // 转为共享引用：表单切只读，定义已入共享库
}

// ── 校验与保存 ──
function validate(): string {
  const d = draft.value
  if (!String(d?.['rdfs:label'] || '').trim()) return '请填写属性名称。'
  if (!String(d?.['rdfs:comment'] || '').trim()) return '请填写业务定义。'
  if (props.kind === 'property' && !editingShared.value) {
    const name = String(d['rdfs:label']).trim()
    const dup = localProperties(graph.value, props.targetTypeId).find((p: any) => p['@id'] !== props.propertyId && effectiveProperty(p, graph.value)['rdfs:label'] === name)
    if (dup) return '该对象已有同名属性。'
  }
  return ''
}
async function save() {
  if (readonly.value) { emit('close'); return }  // 只读共享引用：主按钮=返回属性清单
  const problem = validate()
  if (problem) { error.value = problem; return }
  // 共享定义（含就地维护）高影响修改：先打开影响确认（需求 11）
  const info = currentImpact()
  if (info?.highImpact) {
    const id = editingShared.value ? sharedRefId.value : (props.kind === 'shared' ? props.propertyId : '')
    openImpact(info, id)
    return
  }
  await doSave()
}

async function doSave() {
  const d = draft.value
  const ok = await runSave(() => {
    let node: any
    if (editingShared.value) node = graph.value.find((n: any) => n['@id'] === sharedRefId.value)
    else if (props.propertyId) node = graph.value.find((n: any) => n['@id'] === props.propertyId)
    else { graph.value.push(d); node = d }
    if (node) {
      for (const k of MANAGED) { if (k in d) node[k] = clone(d[k]); else delete node[k] }
      // 显示名称标记不在表单维护（用户以独立“显示名称”属性承担）；保存时保留节点既有标记。
    }
  })
  if (!ok) {
    // 失败/409：确认随基线失效，重新保存必须重新核对影响（需求 §5）
    impactFp = ''
    return
  }
  impactFp = ''
  emit('saved', { id: props.propertyId || d['@id'], kind: editingShared.value ? 'shared' : props.kind, targetTypeId: props.targetTypeId || undefined })
}
</script>

<template>
<section class="card detail-card prop-form">
  <div class="prop-form-head">
    <button v-if="props.canvasReturn" type="button" @click="emit('back-to-graph')">← 返回图谱</button>
    <button type="button" @click="emit('close')">{{ props.canvasReturn ? '关闭' : '← ' + (kind === 'property' ? '返回对象' : '返回共享属性库') }}</button>
  </div>
  <div class="detail-heading">
    <div><p class="prop-form-context">{{ kind === 'shared' || editingShared ? '共享属性库' : contextName }}</p><h2>{{ title }}</h2></div>
  </div>
  <p v-if="error" class="inline-error prop-error" role="alert">{{ error }}</p>

  <!-- 共享引用：只读 + 打开共享定义 / 解除为私有（原型 editorView property 分支） -->
  <div v-if="readonly || editingShared" class="shared-notice">
    <template v-if="readonly">
      <p>此属性引用共享定义，名称、类型、单位与格式化跟随共享定义统一维护（{{ usageText }}）。</p>
      <div class="tools">
        <button type="button" @click="openSharedDef">打开共享定义</button>
        <button type="button" :disabled="saving" @click="detach">{{ saving ? '处理中…' : '转为私有' }}</button>
      </div>
      <small>转为私有会保留本对象属性及生效内容（ID 不变），解除共享关系；此后独立维护，不再跟随共享定义更新。</small>
    </template>
    <template v-else>
      <p>正在维护共享定义：{{ usageText }}；保存后全部引用同步生效。</p>
      <small>引用此定义的对象属性在各自对象中保持只读；数据类型与时间序列观测值类型跟随共享定义，不可本地覆盖。</small>
    </template>
  </div>

  <fieldset class="prop-fields" :disabled="readonly">
    <div class="form-grid">
      <Field label="属性名称" class="full" :model-value="draft?.['rdfs:label'] || ''" required example="例如：额定功率" :disabled="readonly" @update:model-value="setLabel"/>
      <Field label="业务定义" type="textarea" class="full" :model-value="draft?.['rdfs:comment'] || ''" required example="用一句话说明这个属性描述什么。" help="说明含义和口径；具体表字段放在项目映射，公式放在计算定义。" :disabled="readonly" @update:model-value="setComment"/>
      <Field label="数据类型" type="select" :model-value="selectedType" :options="typeOptions" required :disabled="readonly" @update:model-value="setRange"/>
      <Field v-if="selectedType === 'timeSeries'" label="观测值类型" type="select" :model-value="rangeId" :options="observationOptions" required help="选择每个时刻记录的值是什么类型；时标由时间序列提供，来源在项目映射中配置。" :disabled="readonly" @update:model-value="setObservation"/>
    </div>
  </fieldset>

  <!-- 选填仅保留显示格式；旧单位/小数位由格式化组件兼容读取。 -->
  <details class="technical-section prop-more">
    <summary>显示格式（选填）</summary>
    <PropertyFormatting v-if="draft" :property="draft" :state="state" :disabled="readonly"/>
  </details>

  <div class="detail-footer prop-footer">
    <div class="tools">
      <button type="button" class="primary" :disabled="saving" @click="save">{{ readonly ? '返回属性清单' : saving ? '保存中…' : '保存' }}</button>
      <button type="button" @click="emit('close')">取消</button>
      <button v-if="kind === 'property' && !readonly && propertyId" type="button" :disabled="saving" @click="toShared">转为共享属性</button>
    </div>
    <span>只影响当前本体草稿；已发布版本不变。</span>
  </div>

  <!-- 影响确认（20260920 需求 11）：字段前后值 + 受影响对象 → 属性；勾选后才提交当前草稿。 -->
  <div v-if="impactOpen" class="modal-backdrop" @click.self="cancelImpact">
    <section class="modal-card prop-impact" role="dialog" aria-modal="true" aria-label="确认共享修改影响">
      <h2>确认共享修改影响</h2>
      <p class="muted">修改「{{ draft?.['rdfs:label'] || '未命名' }}」的{{ impact?.highImpact ? '数据类型或业务定义' : '内容' }}会同步到全部引用对象；仅修改当前本体草稿，已发布版本不变。</p>
      <table class="impact-table">
        <thead><tr><th>字段</th><th>修改前</th><th>修改后</th></tr></thead>
        <tbody>
          <tr v-for="f in impact?.fields || []" :key="f.key">
            <td>{{ f.label }}</td><td class="diff-old">{{ f.old }}</td><td class="diff-new">{{ f.new }}</td>
          </tr>
          <tr v-if="!(impact?.fields || []).length"><td colspan="3" class="muted">未检测到影响含义的字段变化。</td></tr>
        </tbody>
      </table>
      <h3 class="impact-sub">受影响对象（{{ (impact?.usages || []).length }} 处引用）</h3>
      <ul v-if="(impact?.usages || []).length" class="impact-usages">
        <li v-for="u in impact!.usages" :key="(u.objectId || '') + '#' + (u.propertyId || '')">
          <strong>{{ u.name }}</strong><span class="muted"> · {{ u.reason }}</span>
        </li>
      </ul>
      <p v-else class="muted">当前没有对象引用此共享定义。</p>
      <label class="check-option impact-ack">
        <input type="checkbox" v-model="impactAck">
        <span>我已查看字段变化与受影响对象。此次保存不修改已发布版本。</span>
      </label>
      <p v-if="impactError" class="inline-error" role="alert">{{ impactError }}</p>
      <div class="tools impact-actions">
        <button type="button" @click="cancelImpact">返回编辑</button>
        <button type="button" class="primary" :disabled="saving" @click="confirmImpact">{{ saving ? '保存中…' : '确认并保存' }}</button>
      </div>
    </section>
  </div>
</section>
</template>

<style scoped>
.prop-form-head{display:flex;align-items:center;gap:12px;margin-bottom:14px;flex-wrap:wrap}
.prop-form-context{margin:0 0 3px;font-size:14px;font-weight:600;color:var(--ink-2)}
.prop-error{margin:0 0 12px}
.prop-fields{border:0;padding:0;margin:0;min-width:0}
.prop-form :deep(.form-grid .editor-field.full){grid-column:1/-1}
.prop-more button{margin-top:10px}
.prop-footer .tools{flex-wrap:wrap}
.prop-impact{width:min(640px,94vw);max-height:88vh;overflow:auto}
.prop-impact h2{margin:0 0 8px}
.impact-table{width:100%;border-collapse:collapse;margin:12px 0}
.impact-table th,.impact-table td{border-bottom:1px solid var(--line);padding:8px 10px;text-align:left;font-size:13px;vertical-align:top}
.impact-table th{color:var(--muted);font-weight:500;background:var(--paper-2)}
.diff-old{color:var(--muted);text-decoration:line-through}
.diff-new{color:var(--blue-ink);font-weight:600}
.impact-sub{font-size:14px;margin:14px 0 6px}
.impact-usages{margin:0;padding-left:18px;max-height:180px;overflow:auto}
.impact-usages li{margin:4px 0;font-size:13px}
.impact-ack{margin:14px 0 6px}
.impact-actions{justify-content:flex-end}
</style>
