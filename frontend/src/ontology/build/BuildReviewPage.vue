<!-- ─── A05 评审初稿（20260920 从物料自动构建本体）──────────────────────────────
     契约：文档/接口文档/08-从物料自动构建本体接口.md §7「候选评审」。
     布局基准：原型 交互原型_v1.html 的 reviewPage()——三栏 reviewgrid：
       左「候选定义」列表（类型/决定/证据状态筛选 + 名称搜索 + 合并勾选）
       中「定义编辑」（按类型的字段、保存/取消、纳入/暂缓/排除、依赖与问题）
       右「定义依据」（按字段分组的证据卡片）
     宽屏三栏，≤1200px 证据列落到第二行，≤900px 单栏堆叠（与原型断点一致）。
     数据纪律：不做假数据——统计数字取接口 counts，证据/定位/解析质量取服务端解析事实；
     计数缺失显示「—」，定位缺失显示「服务端未返回」，绝不把缺失位置渲染成真实位置。
     请求：优先用 ontology/build/api.ts 的导出（该文件与页面并行开发，评审/交付函数可能尚未提供），
     缺失时用本文件内的本地 helper 兜底——仍然只经 app/http 的 getJson/postJson，不自行 fetch。
     props: taskId；emits: back（回生成范围）、saved（已创建本体，携带 ontologyId）。 -->
<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { appConfirm } from '../../shared/appConfirm'
import { getJson, postJson, SaveRequestError } from '../../app/http'
import type { Candidate } from './types'
import { DECISION_LABELS, EVIDENCE_STATUS_LABELS, TYPE_LABELS } from './types'
import * as buildApi from './api'

const props = defineProps<{ taskId: string }>()
const emit = defineEmits<{ (e: 'back'): void; (e: 'saved', ontologyId: string): void }>()

// ── 宽松取值工具（接口返回未冻结字段时逐处校验，不用 any）──────────────────────
function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : {}
}
function asArray<T = unknown>(value: unknown): T[] {
  return Array.isArray(value) ? (value as T[]) : []
}
function str(value: unknown): string {
  return typeof value === 'string' ? value : typeof value === 'number' ? String(value) : ''
}
function num(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}
function has(record: Record<string, unknown>, key: string): boolean {
  return Object.prototype.hasOwnProperty.call(record, key)
}
function errorText(error: unknown): string {
  if (error instanceof SaveRequestError) return error.message
  if (error instanceof Error) return error.message
  return String(error || '请求失败')
}
function isConflict(error: unknown): boolean {
  return error instanceof SaveRequestError && error.status === 409
}

// ── 端点：优先走 build/api.ts，缺失时本地 helper（仍经 app/http）───────────────
type RemoteFn = (...args: unknown[]) => Promise<unknown>
function remoteApi(name: string): RemoteFn | null {
  const value = (buildApi as unknown as Record<string, unknown>)[name]
  return typeof value === 'function' ? (value as RemoteFn) : null
}
function query(params: Record<string, string | number>): string {
  const parts: string[] = []
  for (const [key, value] of Object.entries(params)) {
    if (value === '' || value === undefined || value === null) continue
    parts.push(encodeURIComponent(key) + '=' + encodeURIComponent(String(value)))
  }
  return parts.length ? '?' + parts.join('&') : ''
}
async function requestTask(taskId: string): Promise<unknown> {
  const fn = remoteApi('fetchTask')
  if (fn) return await fn(taskId)
  return await getJson('/api/build-task' + query({ taskId }))
}
async function requestCandidates(taskId: string, params: Record<string, string | number>): Promise<unknown> {
  const fn = remoteApi('fetchCandidates')
  if (fn) return await fn(taskId, params)
  return await getJson('/api/build-candidates' + query({ taskId, ...params }))
}
async function requestCandidate(candidateId: string): Promise<unknown> {
  const fn = remoteApi('fetchCandidate')
  if (fn) return await fn(candidateId)
  return await getJson('/api/build-candidate' + query({ candidateId }))
}
async function requestUpdateCandidate(candidateId: string, fields: Record<string, unknown>, revision: string): Promise<unknown> {
  const fn = remoteApi('updateCandidate')
  if (fn) return await fn(candidateId, fields, revision)
  return await postJson('/api/build-candidate-update', { candidateId, fields, revision })
}
async function requestDecide(candidateId: string, decision: string, reason: string, revision: string): Promise<unknown> {
  const fn = remoteApi('decideCandidate')
  if (fn) return await fn(candidateId, decision, reason, revision)
  return await postJson('/api/build-candidate-decide', { candidateId, decision, reason, revision })
}
async function requestMergePreview(taskId: string, primaryId: string, mergeIds: string[]): Promise<unknown> {
  const fn = remoteApi('mergePreview')
  if (fn) return await fn(taskId, primaryId, mergeIds)
  return await postJson('/api/build-candidates-merge', { taskId, primaryId, mergeIds, confirmed: false, revision: task.value?.revision || '' })
}
async function requestMergeApply(taskId: string, primaryId: string, mergeIds: string[], revision: string): Promise<unknown> {
  const fn = remoteApi('mergeApply')
  if (fn) return await fn(taskId, primaryId, mergeIds, revision)
  return await postJson('/api/build-candidates-merge', { taskId, primaryId, mergeIds, confirmed: true, revision })
}
async function requestUndo(taskId: string, opId: string, revision: string): Promise<unknown> {
  const fn = remoteApi('undoReviewOp')
  if (fn) return await fn(taskId, opId, revision)
  return await postJson('/api/build-review-undo', { taskId, opId, revision })
}
async function requestRegenerate(taskId: string, revision: string): Promise<unknown> {
  const fn = remoteApi('regenerate')
  if (fn) return await fn(taskId, revision)
  return await postJson('/api/build-regenerate', { taskId, revision })
}
async function requestDiff(taskId: string, batch: string): Promise<unknown> {
  const fn = remoteApi('fetchDiff')
  if (fn) return await fn(taskId, batch || undefined)
  return await getJson('/api/build-diff' + query({ taskId, batch }))
}
async function requestResolveDiff(taskId: string, candidateId: string, choice: 'keepManual' | 'acceptNew', revision: string): Promise<unknown> {
  const fn = remoteApi('resolveDiff')
  if (fn) return await fn(taskId, candidateId, choice, revision)
  return await postJson('/api/build-diff-resolve', { taskId, candidateId, choice, revision })
}

// ── 文案映射（以 types.ts 标签表为准，缺键时回落到协议枚举的中文名）───────────
const TYPE_FALLBACK: Record<string, string> = { object: '对象', property: '属性', link: '链接', rule: '规则', action: '动作' }
const DECISION_FALLBACK: Record<string, string> = { include: '拟纳入', defer: '暂缓', exclude: '已排除' }
const STATUS_FALLBACK: Record<string, string> = { supported: '有依据', inferred: '推断待确认', conflict: '来源冲突', insufficient: '材料不足' }
const DATA_TYPE_LABELS: Record<string, string> = { text: '文本', number: '数值', boolean: '是或否', dateTime: '时间', array: '数组', struct: '结构体', timeSeries: '时间序列' }
const VALUE_TYPE_LABELS: Record<string, string> = { text: '文本', number: '数值', boolean: '是或否', dateTime: '时间' }
const QUALITY_LABELS: Record<string, string> = { high: '高', medium: '中', low: '低' }
const LOCATOR_KIND_LABELS: Record<string, string> = { code: '源码', ddl: 'DDL', docx: '文档', pdf: 'PDF', xlsx: '表格', md: 'Markdown' }
const TYPE_KEYS = Object.keys(TYPE_FALLBACK)
const STATUS_FILTERS = ['supported', 'inferred', 'conflict', 'insufficient']

function labelFrom(map: unknown, key: string, fallback: Record<string, string>): string {
  const hit = asRecord(map)[key]
  return typeof hit === 'string' && hit ? hit : fallback[key] || key
}
const typeLabel = (key: string) => labelFrom(TYPE_LABELS, key, TYPE_FALLBACK)
const decisionLabel = (key: string) => labelFrom(DECISION_LABELS, key, DECISION_FALLBACK)
const statusLabel = (key: string) => labelFrom(EVIDENCE_STATUS_LABELS, key, STATUS_FALLBACK)
const qualityLabel = (key: string) => QUALITY_LABELS[key] || key

const DATA_TYPE_OPTIONS = Object.keys(DATA_TYPE_LABELS).map(key => ({ value: key, label: DATA_TYPE_LABELS[key] }))
const VALUE_TYPE_OPTIONS = Object.keys(VALUE_TYPE_LABELS).map(key => ({ value: key, label: VALUE_TYPE_LABELS[key] }))
const ONE_MANY_OPTIONS = [
  { value: '', label: '未定（需依据）' },
  { value: 'one', label: '一（one）' },
  { value: 'many', label: '多（many）' },
]

// ── 视图模型 ─────────────────────────────────────────────────────────────────
interface IssueView { code: string; message: string; field: string }
interface CandidateView {
  id: string; type: string; key: string; name: string; definition: string
  ownerKey: string; evidenceStatus: string; decision: string; reviewed: boolean
  reason: string; revision: string; revived: boolean
  fields: Record<string, unknown>
  issues: IssueView[]
  mergedFrom: string[]
  mergedInto: string
}
interface RegistryEntry { id: string; key: string; name: string; type: string; decision: string }
interface EvidenceItemView {
  factId: string; locator: Record<string, unknown> | null; snippet: string
  quality: string; materialRelPath: string; materialRevision: number | null; missing: boolean
}
interface EvidenceGroupView { field: string; items: EvidenceItemView[] }
interface TaskView { name: string; status: string; revision: string; materialRevision: number | null; scopeRevision: number | null; deliveryOntologyId: string }
interface DraftState { name: string; definition: string; dataType: string; valueType: string; content: string; effect: string; cardSource: string; cardTarget: string }
interface MergeFieldView { name: string; primaryValue: string; mergeValues: string[]; diff: boolean }
interface MergePreviewView { primaryId: string; mergeIds: string[]; fields: MergeFieldView[]; evidenceCount: number | null; warnings: string[] }
interface DiffRow { id: string; name: string; detail: string; manualConflict: boolean; revived: boolean }

function toCandidateView(raw: unknown): CandidateView {
  const node = asRecord(raw)
  const origin = asRecord(node.origin)
  return {
    id: str(node.id),
    type: str(node.type),
    key: str(node.key),
    name: str(node.name),
    definition: str(node.definition),
    ownerKey: str(node.ownerKey),
    evidenceStatus: str(node.evidenceStatus),
    decision: str(node.decision) || 'defer',
    reviewed: node.reviewed === true,
    reason: str(node.reason),
    revision: str(node.revision),
    revived: node.revived === true,
    fields: asRecord(node.fields),
    issues: asArray(node.issues).map(item => {
      const issue = asRecord(item)
      return { code: str(issue.code), message: str(issue.message), field: str(issue.field) }
    }),
    mergedFrom: asArray(origin.mergedFrom).map(String),
    mergedInto: str(origin.mergedInto),
  }
}

function toTaskView(raw: unknown): TaskView {
  const body = asRecord(raw)
  // 兼容两种返回形态：08 §3 的 {task,...} 明细封装，或任务对象本身。
  const node = body.task && typeof body.task === 'object' ? asRecord(body.task) : body
  return {
    name: str(node.name),
    status: str(node.status),
    revision: str(node.revision),
    materialRevision: num(node.materialRevision),
    scopeRevision: num(node.scopeRevision),
    deliveryOntologyId: str(node.deliveryOntologyId),
  }
}

function locatorText(locator: Record<string, unknown> | null): string {
  if (!locator) return '（服务端未返回定位信息）'
  if (locator.missing === true) return '引用位置不存在（服务端已剔除）'
  const kind = str(locator.kind)
  const segments: string[] = []
  const push = (label: string, value: unknown, suffix = '') => {
    const text = typeof value === 'number' ? String(value) : str(value)
    if (text) segments.push(label + text + suffix)
  }
  const file = str(locator.file)
  if (file) segments.push(file)
  if (kind === 'pdf') push('第', locator.page, '页')
  else if (kind === 'xlsx') {
    const sheet = str(locator.sheet), cell = str(locator.cell)
    if (sheet || cell) segments.push([sheet, cell].filter(Boolean).join('!'))
  } else if (kind === 'ddl') {
    const table = str(locator.table), column = str(locator.column)
    if (table || column) segments.push([table, column].filter(Boolean).join('.'))
    push('行 ', locator.line)
  } else if (kind === 'code') {
    push('行 ', locator.line)
    const symbol = str(locator.symbol)
    if (symbol) segments.push(symbol)
  } else if (kind === 'docx') {
    const section = str(locator.section)
    if (section) segments.push(section)
    push('块 ', locator.block)
  } else if (kind === 'md') {
    const section = str(locator.section)
    if (section) segments.push(section)
    push('行 ', locator.line)
  } else {
    const fallback = locatorFallback(locator)
    if (fallback) segments.push(fallback)
  }
  const prefix = LOCATOR_KIND_LABELS[kind] || kind
  if (!segments.length) return prefix ? prefix + ' · （定位细节为空）' : '（定位信息为空）'
  return (prefix ? prefix + ' · ' : '') + segments.join(' · ')
}
function locatorFallback(locator: Record<string, unknown>): string {
  const keys: Array<[string, string]> = [['table', '表：'], ['column', '字段：'], ['sheet', '工作表：'], ['cell', '单元格：'], ['section', '章节：'], ['block', '块：'], ['symbol', '符号：'], ['line', '行：'], ['page', '页码：']]
  return keys
    .map(([key, label]) => (typeof locator[key] === 'number' || typeof locator[key] === 'string' ? label + String(locator[key]) : ''))
    .filter(Boolean)
    .join(' · ')
}
function fieldLabel(field: string): string {
  return !field || field === '_record' ? '整体记录' : field
}

// ── 页面状态 ─────────────────────────────────────────────────────────────────
const PAGE_SIZE = 100
const task = ref<TaskView | null>(null)
const taskError = ref('')
const listRaw = ref<Candidate[]>([])
const total = ref(0)
const counts = ref<{ byType: Record<string, number>; byDecision: Record<string, number>; byStatus: Record<string, number> }>({ byType: {}, byDecision: {}, byStatus: {} })
const batch = ref<Record<string, unknown> | null>(null)
const stale = ref(false)
const listLoading = ref(false)
const listError = ref('')
const pageError = ref('')
const busy = ref(false)
const registry = ref<Record<string, RegistryEntry>>({})

const typeFilter = ref('')
const decisionFilter = ref('')
const statusFilter = ref('')
const search = ref('')

const selectedId = ref('')
const detailRaw = ref<Candidate | null>(null)
const detailLoading = ref(false)
const detailError = ref('')
const evidence = ref<EvidenceGroupView[]>([])

const draft = ref<DraftState | null>(null)
const baseline = ref<DraftState | null>(null)
const editError = ref('')
const saving = ref(false)

const reasonDraft = ref('')
const decideError = ref('')

const mergeIds = ref<string[]>([])
const mergeError = ref('')
const mergePreview = ref<MergePreviewView | null>(null)
const lastOpId = ref('')

const diff = ref<{ against: string; batchId: string; buckets: Record<string, unknown>; items: CandidateView[] } | null>(null)
const diffError = ref('')
const regenerating = ref(false)

const rows = computed(() => listRaw.value.map(toCandidateView))
const current = computed<CandidateView | null>(() => (detailRaw.value ? toCandidateView(detailRaw.value) : null))
const dirty = computed(() => !!draft.value && !!baseline.value && JSON.stringify(draft.value) !== JSON.stringify(baseline.value))
const mergeType = computed(() => {
  const first = rows.value.find(row => mergeIds.value.includes(row.id))
  return first ? first.type : ''
})
const mergePrimary = computed(() => (mergeIds.value.includes(selectedId.value) ? selectedId.value : mergeIds.value[0] || ''))
const hasMore = computed(() => rows.value.length < total.value)
const batchId = computed(() => str(asRecord(batch.value).id))
const stats = computed(() => {
  const byDecision = counts.value.byDecision
  const show = (key: string) => (key in byDecision ? String(byDecision[key]) : '—')
  return { include: show('include'), defer: show('defer'), exclude: show('exclude') }
})
const batchLine = computed(() => {
  const info = asRecord(batch.value)
  const base = asRecord(info.baseline)
  const bits: string[] = []
  if (batchId.value) bits.push('批次 ' + batchId.value)
  const materialRevision = num(base.materialRevision)
  if (materialRevision !== null) bits.push('材料修订 r' + materialRevision)
  if (info.stale === true || stale.value) bits.push('该批次结果已过期')
  return bits.join(' · ')
})
const blockingDeps = computed(() => {
  const item = current.value
  if (!item) return [] as RegistryEntry[]
  const refs: string[] = []
  if (item.type === 'property' && item.ownerKey) refs.push(item.ownerKey)
  if (item.type === 'link') { refs.push(str(item.fields.sourceRef), str(item.fields.targetRef)) }
  const out: RegistryEntry[] = []
  for (const ref of refs) {
    if (!ref) continue
    const hit = registry.value[ref]
    if (hit && hit.decision === 'exclude') out.push(hit)
  }
  return out
})
const unresolvedRefs = computed(() => {
  const item = current.value
  if (!item) return [] as string[]
  const refs: string[] = []
  if (item.type === 'property' && item.ownerKey) refs.push(item.ownerKey)
  if (item.type === 'link') { refs.push(str(item.fields.sourceRef), str(item.fields.targetRef)) }
  return refs.filter(ref => ref && !registry.value[ref])
})
const linkEndpoints = computed(() => {
  const item = current.value
  if (!item || item.type !== 'link') return null
  const sourceRef = str(item.fields.sourceRef), targetRef = str(item.fields.targetRef)
  return { sourceRef, targetRef, sourceName: endpointName(sourceRef), targetName: endpointName(targetRef) }
})
const needsReason = computed(() => !!current.value && current.value.evidenceStatus !== 'supported')
const statusHint = computed(() => {
  const item = current.value
  if (!item) return ''
  if (item.evidenceStatus === 'conflict') return '来源之间存在不同口径。请选择采用的定义并记录理由，或暂缓。'
  if (item.evidenceStatus === 'insufficient') return '材料不足，当前结论不足以直接纳入。纳入前请补充人工确认依据，或暂缓。'
  return '当前结论含推断。纳入前请补充人工确认依据，或暂缓。'
})
const relationText = computed(() => {
  const item = current.value
  if (!item) return ''
  if (item.type === 'property' && item.ownerKey) return '所属对象：' + endpointName(item.ownerKey)
  if (item.type === 'link' && linkEndpoints.value) return '关联：' + linkEndpoints.value.sourceName + ' → ' + linkEndpoints.value.targetName
  return '独立业务概念'
})
const diffGroups = computed<Array<{ key: string; label: string; items: DiffRow[] }>>(() => {
  const state = diff.value
  if (!state) return []
  const byId = new Map(state.items.map(item => [item.id, item]))
  const rowOf = (id: string): DiffRow => {
    const hit = byId.get(id)
    return { id, name: hit?.name || registry.value[id]?.name || id, detail: '', manualConflict: false, revived: hit?.revived === true }
  }
  const buckets = state.buckets
  const changed = asArray(buckets.changed).map(entry => {
    const node = asRecord(entry)
    const id = str(node.id)
    const hit = byId.get(id)
    const fields = asArray(node.fieldsChanged).map(String)
    return {
      id,
      name: hit?.name || registry.value[id]?.name || id,
      detail: fields.length ? '变动字段：' + fields.join('、') : '字段有变动',
      manualConflict: node.manualConflict === true,
      revived: hit?.revived === true,
    }
  })
  return [
    { key: 'added', label: '新增候选', items: asArray(buckets.added).map(String).map(rowOf) },
    { key: 'changed', label: '变动（可逐项裁决）', items: changed },
    { key: 'removed', label: '本次未再提出（消失）', items: asArray(buckets.removed).map(String).map(rowOf) },
    { key: 'excludedProtected', label: '已排除保护（不自动复活）', items: asArray(buckets.excludedProtected).map(String).map(rowOf) },
    { key: 'kept', label: '与人工结果一致（未变）', items: asArray(buckets.kept).map(String).map(rowOf) },
  ]
})

function endpointName(ref: string): string {
  if (!ref) return '依据缺失'
  const hit = registry.value[ref]
  if (hit) return hit.name || hit.key || hit.id
  return ref + '（未在当前列表载入）'
}
function ownerName(row: CandidateView): string {
  return row.ownerKey && registry.value[row.ownerKey] ? registry.value[row.ownerKey].name : ''
}

watch(() => props.taskId, () => { void bootstrap() })
onMounted(() => { void bootstrap() })

async function bootstrap() {
  selectedId.value = ''
  detailRaw.value = null
  evidence.value = []
  listRaw.value = []
  total.value = 0
  counts.value = { byType: {}, byDecision: {}, byStatus: {} }
  batch.value = null
  stale.value = false
  registry.value = {}
  mergeIds.value = []
  mergePreview.value = null
  lastOpId.value = ''
  diff.value = null
  pageError.value = ''
  await loadTask()
  await loadList('replace')
  if (!selectedId.value && rows.value.length) await loadDetail(rows.value[0].id)
}

async function loadTask() {
  taskError.value = ''
  try {
    task.value = toTaskView(await requestTask(props.taskId))
  } catch (error) {
    taskError.value = '任务信息加载失败：' + errorText(error)
  }
}

function readCountMap(value: unknown): Record<string, number> {
  const source = asRecord(value)
  const out: Record<string, number> = {}
  for (const key of Object.keys(source)) {
    const parsed = num(source[key])
    if (parsed !== null) out[key] = parsed
  }
  return out
}

async function loadList(mode: 'replace' | 'append') {
  listLoading.value = true
  listError.value = ''
  const offset = mode === 'append' ? rows.value.length : 0
  const params: Record<string, string | number> = { offset, limit: PAGE_SIZE }
  if (typeFilter.value) params.type = typeFilter.value
  if (decisionFilter.value) params.decision = decisionFilter.value
  if (statusFilter.value) params.evidenceStatus = statusFilter.value
  if (search.value.trim()) params.query = search.value.trim()
  try {
    const response = asRecord(await requestCandidates(props.taskId, params))
    const items = asArray<Candidate>(response.items)
    listRaw.value = mode === 'append' ? [...listRaw.value, ...items] : items
    total.value = num(response.total) ?? listRaw.value.length
    const rawCounts = asRecord(response.counts)
    counts.value = {
      byType: readCountMap(rawCounts.byType),
      byDecision: readCountMap(rawCounts.byDecision),
      byStatus: readCountMap(rawCounts.byStatus),
    }
    if (has(response, 'batch')) batch.value = asRecord(response.batch)
    if (has(response, 'stale')) stale.value = response.stale === true
    register(items.map(toCandidateView))
  } catch (error) {
    listError.value = '候选列表加载失败：' + errorText(error)
  } finally {
    listLoading.value = false
  }
}

function register(items: CandidateView[]) {
  const next = { ...registry.value }
  for (const item of items) {
    const entry: RegistryEntry = { id: item.id, key: item.key, name: item.name, type: item.type, decision: item.decision }
    if (item.key) next[item.key] = entry
    if (item.id) next[item.id] = entry
  }
  registry.value = next
}

async function loadMore() {
  if (listLoading.value || !hasMore.value) return
  await loadList('append')
}

async function applyFilters() {
  await loadList('replace')
}

async function select(id: string) {
  if (id === selectedId.value && detailRaw.value) return
  if (dirty.value) {
    editError.value = '有未保存的编辑，请先「保存编辑」或「取消编辑」再切换候选。'
    return
  }
  selectedId.value = id
  await loadDetail(id)
}

async function loadDetail(id: string) {
  detailLoading.value = true
  detailError.value = ''
  try {
    const response = asRecord(await requestCandidate(id))
    const raw = response.candidate
    detailRaw.value = (raw && typeof raw === 'object' ? raw : null) as Candidate | null
    const item = current.value
    if (!item) throw new Error('服务端未返回候选内容')
    evidence.value = parseEvidence(response.evidence)
    register([item])
    resetDraft(item)
    reasonDraft.value = item.reason
    decideError.value = ''
  } catch (error) {
    detailRaw.value = null
    evidence.value = []
    draft.value = null
    baseline.value = null
    detailError.value = '候选详情加载失败：' + errorText(error)
  } finally {
    detailLoading.value = false
  }
}

function parseEvidence(raw: unknown): EvidenceGroupView[] {
  // 明细端点返回 [{field, items:[{factId,locator,snippet,quality,materialRelPath}]}]；
  // 若服务端给的是 §1.6 的映射形态（字段 → factId 列表）也照实展示，但不编造位置。
  if (Array.isArray(raw)) {
    return raw.map(group => {
      const node = asRecord(group)
      const items = asArray(node.items).map(item => toEvidenceItem(asRecord(item)))
      return { field: str(node.field) || '_record', items }
    })
  }
  return Object.keys(asRecord(raw)).map(field => ({
    field,
    items: asArray(asRecord(raw)[field]).map(id => ({ factId: String(id), locator: null, snippet: '', quality: '', materialRelPath: '', materialRevision: null, missing: false })),
  }))
}

function toEvidenceItem(entry: Record<string, unknown>): EvidenceItemView {
  const locatorRaw = entry.locator
  const locator = locatorRaw && typeof locatorRaw === 'object' && !Array.isArray(locatorRaw) ? (locatorRaw as Record<string, unknown>) : null
  const missing = entry.missing === true || (locator ? locator.missing === true : false)
  return {
    factId: str(entry.factId),
    locator,
    snippet: str(entry.snippet),
    quality: str(entry.quality),
    materialRelPath: str(entry.materialRelPath),
    materialRevision: num(entry.materialRevision),
    missing,
  }
}

// ── 编辑（按候选类型显示字段）────────────────────────────────────────────────
function readDataType(fields: Record<string, unknown>): { type: string; valueType: string } {
  const raw = fields.dataType
  if (typeof raw === 'string') return { type: raw, valueType: '' }
  const node = asRecord(raw)
  return { type: str(node.type), valueType: str(node.valueType) }
}

function resetDraft(item: CandidateView) {
  const dataType = readDataType(item.fields)
  const cardinality = asRecord(item.fields.cardinality)
  const next: DraftState = {
    name: item.name,
    definition: item.definition,
    dataType: dataType.type,
    valueType: dataType.valueType,
    content: str(item.fields.content),
    effect: str(item.fields.effect),
    cardSource: str(cardinality.source),
    cardTarget: str(cardinality.target),
  }
  draft.value = { ...next }
  baseline.value = { ...next }
  editError.value = ''
}

function cancelEdit() {
  if (baseline.value) draft.value = { ...baseline.value }
  editError.value = ''
}

async function saveEdit() {
  const item = current.value, form = draft.value
  if (!item || !form || saving.value) return
  if (!form.name.trim()) { editError.value = '请填写名称。'; return }
  if (!form.definition.trim()) { editError.value = '请填写业务定义。'; return }
  if (item.type === 'property' && form.dataType === 'timeSeries' && !form.valueType) {
    editError.value = '时间序列必须选择观测值类型。'
    return
  }
  const fields: Record<string, unknown> = { name: form.name.trim(), definition: form.definition.trim() }
  if (item.type === 'property') {
    if (form.dataType === 'timeSeries') fields.dataType = { type: 'timeSeries', valueType: form.valueType }
    else if (form.dataType) fields.dataType = form.dataType
  }
  if (item.type === 'rule') fields.content = form.content
  if (item.type === 'action') fields.effect = form.effect
  if (item.type === 'link' && (form.cardSource || form.cardTarget)) fields.cardinality = { source: form.cardSource, target: form.cardTarget }
  saving.value = true
  editError.value = ''
  try {
    const response = asRecord(await requestUpdateCandidate(item.id, fields, item.revision))
    const updated = response.candidate
    if (updated && typeof updated === 'object') {
      const view = toCandidateView(updated)
      detailRaw.value = updated as Candidate
      register([view])
      syncRow(view)
      resetDraft(view)
      reasonDraft.value = view.reason
    } else {
      await loadDetail(item.id)
    }
    mergePreview.value = null
  } catch (error) {
    // 保存失败保留输入：不动 draft/baseline，用户可修正后重试。
    editError.value = isConflict(error)
      ? '保存被拒绝：候选已被其他操作更新，请先重新载入核对；当前输入仍保留在表单中。'
      : '保存失败：' + errorText(error)
  } finally {
    saving.value = false
  }
}

function syncRow(item: CandidateView) {
  const index = listRaw.value.findIndex(row => toCandidateView(row).id === item.id)
  if (index < 0) return
  const next = [...listRaw.value]
  const merged: Record<string, unknown> = { ...asRecord(next[index]) }
  merged.name = item.name
  merged.definition = item.definition
  merged.decision = item.decision
  merged.evidenceStatus = item.evidenceStatus
  merged.reviewed = item.reviewed
  merged.reason = item.reason
  merged.fields = item.fields
  merged.revision = item.revision
  next[index] = merged as unknown as Candidate
  listRaw.value = next
}

// ── 决定（纳入/暂缓/排除）────────────────────────────────────────────────────
async function decide(decision: 'include' | 'defer' | 'exclude') {
  const item = current.value
  if (!item || busy.value) return
  decideError.value = ''
  if (dirty.value) { decideError.value = '有未保存的编辑，请先保存编辑再设置决定（决定使用已保存字段）。'; return }
  if (decision === 'include' && needsReason.value && !reasonDraft.value.trim()) {
    decideError.value = '该候选的证据状态不是「有依据」，转为纳入前必须填写「人工确认依据」。'
    return
  }
  busy.value = true
  try {
    const response = asRecord(await requestDecide(item.id, decision, reasonDraft.value.trim(), item.revision))
    const updated = response.candidate
    if (updated && typeof updated === 'object') {
      const view = toCandidateView(updated)
      detailRaw.value = updated as Candidate
      register([view])
      syncRow(view)
      reasonDraft.value = view.reason
    } else {
      await loadDetail(item.id)
    }
    await loadList('replace')
  } catch (error) {
    decideError.value = isConflict(error)
      ? '决定被拒绝：候选已被其他操作更新，请重新载入后再试。'
      : '决定失败：' + errorText(error)
  } finally {
    busy.value = false
  }
}

// ── 依赖定位 ─────────────────────────────────────────────────────────────────
async function jumpTo(id: string) {
  if (!id) return
  if (dirty.value) { editError.value = '有未保存的编辑，请先保存或取消再跳转。'; return }
  if (!rows.value.some(row => row.id === id)) {
    typeFilter.value = ''
    decisionFilter.value = ''
    statusFilter.value = ''
    search.value = ''
    await loadList('replace')
  }
  selectedId.value = id
  await loadDetail(id)
}

// ── 合并（预览 / 确认 / 撤销）────────────────────────────────────────────────
function toggleMerge(row: CandidateView, checked: boolean) {
  mergeError.value = ''
  if (checked) {
    if (mergeType.value && mergeType.value !== row.type) {
      mergeError.value = '只能合并同类型候选：已勾选的是「' + typeLabel(mergeType.value) + '」，不能加入「' + typeLabel(row.type) + '」。'
      return
    }
    if (!mergeIds.value.includes(row.id)) mergeIds.value = [...mergeIds.value, row.id]
  } else {
    mergeIds.value = mergeIds.value.filter(id => id !== row.id)
    if (mergePreview.value) { mergePreview.value = null; mergeError.value = '勾选已变化，请重新「合并预览」。' }
  }
}

function clearMerge() {
  mergeIds.value = []
  mergePreview.value = null
  mergeError.value = ''
}

async function previewMerge() {
  if (busy.value) return
  if (mergeIds.value.length < 2) { mergeError.value = '请至少勾选两项同类型候选（保留项 + 被并入项）。'; return }
  busy.value = true
  mergeError.value = ''
  try {
    const response = asRecord(await requestMergePreview(props.taskId, mergePrimary.value, mergeIds.value))
    const preview = asRecord(response.preview)
    mergePreview.value = {
      primaryId: mergePrimary.value,
      mergeIds: [...mergeIds.value],
      fields: asArray(preview.fields).map(field => {
        const node = asRecord(field)
        return {
          name: str(node.name),
          primaryValue: str(node.primaryValue),
          mergeValues: asArray(node.mergeValues).map(String),
          diff: node.diff === true,
        }
      }),
      evidenceCount: num(preview.evidenceCount),
      warnings: asArray(preview.warnings).map(String),
    }
  } catch (error) {
    mergePreview.value = null
    mergeError.value = isConflict(error) ? '合并预览被拒绝：请重新载入候选后再试。' : '合并预览失败：' + errorText(error)
  } finally {
    busy.value = false
  }
}

async function confirmMerge() {
  const preview = mergePreview.value
  if (!preview || busy.value) return
  busy.value = true
  mergeError.value = ''
  try {
    const response = asRecord(await requestMergeApply(props.taskId, preview.primaryId, preview.mergeIds, task.value?.revision || ''))
    lastOpId.value = str(response.opId)
    clearMerge()
    await loadList('replace')
    if (preview.primaryId) {
      selectedId.value = preview.primaryId
      await loadDetail(preview.primaryId)
    }
  } catch (error) {
    mergeError.value = isConflict(error)
      ? '合并被拒绝：保留项或候选已被其他操作更新，请重新预览后再试。'
      : '合并失败：' + errorText(error)
  } finally {
    busy.value = false
  }
}

async function undoLastMerge() {
  if (!lastOpId.value || busy.value) return
  const ok = await appConfirm({
    title: '撤销上次合并',
    message: '将恢复被合并候选的字段与关联；若保留项已有后续人工编辑，服务端会拒绝，不会静默丢弃。继续？',
    confirmLabel: '撤销合并',
  })
  if (!ok) return
  busy.value = true
  mergeError.value = ''
  try {
    await requestUndo(props.taskId, lastOpId.value, task.value?.revision || '')
    lastOpId.value = ''
    await loadList('replace')
    if (selectedId.value) await loadDetail(selectedId.value)
  } catch (error) {
    mergeError.value = isConflict(error)
      ? '撤销被拒绝：保留项已有后续人工编辑，请先核对受影响字段。'
      : '撤销失败：' + errorText(error)
  } finally {
    busy.value = false
  }
}

// ── 再生成与差异 ─────────────────────────────────────────────────────────────
async function runRegenerate() {
  if (regenerating.value || busy.value) return
  if (dirty.value) { pageError.value = '有未保存的编辑，请先保存或取消再重新生成。'; return }
  const ok = await appConfirm({
    title: '重新生成',
    message: '将按当前材料与范围基线完整重跑：旧批次结果保留、人工决定与编辑保留、已排除候选不会自动复活。继续？',
    confirmLabel: '开始重新生成',
  })
  if (!ok) return
  regenerating.value = true
  pageError.value = ''
  diffError.value = ''
  try {
    const response = asRecord(await requestRegenerate(props.taskId, task.value?.revision || ''))
    pageError.value = '已提交重新生成（运行 ' + (str(response.runId) || '—') + ' · 批次 ' + (str(response.batchId) || '—') + '）：生成完成后点「查看差异」查看新增/变动/消失与人工修改冲突。'
    await loadTask()
    await loadList('replace')
  } catch (error) {
    pageError.value = '重新生成失败：' + errorText(error)
  } finally {
    regenerating.value = false
  }
}

async function loadDiff() {
  diffError.value = ''
  busy.value = true
  try {
    const response = asRecord(await requestDiff(props.taskId, batchId.value))
    diff.value = {
      against: str(response.against),
      batchId: batchId.value,
      buckets: asRecord(response.buckets),
      items: asArray(response.items).map(toCandidateView),
    }
  } catch (error) {
    diffError.value = '差异加载失败：' + errorText(error)
  } finally {
    busy.value = false
  }
}

async function resolveDiff(candidateId: string, choice: 'keepManual' | 'acceptNew') {
  if (busy.value) return
  busy.value = true
  diffError.value = ''
  try {
    await requestResolveDiff(props.taskId, candidateId, choice, task.value?.revision || '')
    await loadDiff()
    await loadList('replace')
    if (selectedId.value === candidateId) await loadDetail(candidateId)
  } catch (error) {
    diffError.value = isConflict(error) ? '裁决被拒绝：请重新载入差异后再试。' : '裁决失败：' + errorText(error)
  } finally {
    busy.value = false
  }
}
</script>

<template>
<div class="build-review br-root">
  <header class="br-head">
    <div class="br-head-main">
      <span class="eyebrow">从物料构建 · 评审初稿</span>
      <h2>评审本体初稿<template v-if="task && task.name"> · {{ task.name }}</template></h2>
      <p class="muted">
        拟纳入 {{ stats.include }} 项 · 暂缓 {{ stats.defer }} 项 · 已排除 {{ stats.exclude }} 项。
        名称与定义可修改，依据始终保留；暂缓与排除项不会写入本体。
      </p>
    </div>
    <div class="tools">
      <button type="button" @click="emit('back')">修改生成范围</button>
      <button type="button" :disabled="busy" @click="loadDiff">{{ diff ? '刷新差异' : '查看差异' }}</button>
      <button type="button" :disabled="regenerating || busy" @click="runRegenerate">{{ regenerating ? '正在提交…' : '重新生成' }}</button>
    </div>
  </header>

  <p v-if="stale" class="notice br-banner br-banner-warn" role="status">结果已过期，请重新确认范围并生成；人工决定会保留。</p>
  <p v-if="taskError" class="inline-error br-banner" role="alert">{{ taskError }}</p>
  <p v-if="pageError" class="notice br-banner" role="status">{{ pageError }}</p>
  <p v-if="diffError" class="inline-error br-banner" role="alert">{{ diffError }}</p>

  <section v-if="diff" class="card br-diff">
    <div class="detail-heading">
      <div>
        <span class="eyebrow">重新生成结果对照</span>
        <h3>当前批次 {{ diff.batchId || '—' }}<template v-if="diff.against"> · 对齐旧批次 {{ diff.against }}</template></h3>
      </div>
      <button type="button" @click="diff = null">收起</button>
    </div>
    <p class="muted">旧批次结果与人工决定全部保留；「已排除保护」中的候选不会被自动复活，重新提出时默认暂缓。</p>
    <div class="br-diffgrid">
      <div v-for="group in diffGroups" :key="group.key" class="br-diffbox">
        <h4>{{ group.label }} · {{ group.items.length }}</h4>
        <ul v-if="group.items.length">
          <li v-for="item in group.items" :key="item.id">
            <button type="button" class="row-link" @click="jumpTo(item.id)">{{ item.name }}</button>
            <small v-if="item.detail" class="br-diffnote">{{ item.detail }}</small>
            <small v-if="item.revived" class="br-diffnote">新证据再次提出（默认暂缓）</small>
            <span v-if="item.manualConflict" class="br-conflict">
              <em>人工修改冲突</em>
              <button type="button" :disabled="busy" @click="resolveDiff(item.id, 'keepManual')">保留人工修改</button>
              <button type="button" :disabled="busy" @click="resolveDiff(item.id, 'acceptNew')">采用新结果</button>
            </span>
          </li>
        </ul>
        <p v-else class="muted">无</p>
      </div>
    </div>
  </section>

  <div class="reviewgrid">
    <!-- ① 候选列表 -->
    <section class="card br-col br-col-list" aria-label="候选定义列表">
      <h3>候选定义</h3>
      <input v-model="search" type="search" placeholder="搜索名称或定义" aria-label="搜索候选名称或定义" @keydown.enter="applyFilters">
      <div class="br-filters">
        <select v-model="typeFilter" aria-label="类型筛选" @change="applyFilters">
          <option value="">全部类型</option>
          <option v-for="key in TYPE_KEYS" :key="key" :value="key">{{ typeLabel(key) }}</option>
        </select>
        <select v-model="decisionFilter" aria-label="决定状态筛选" @change="applyFilters">
          <option value="">全部决定</option>
          <option value="include">拟纳入</option>
          <option value="defer">暂缓</option>
          <option value="exclude">已排除</option>
        </select>
        <select v-model="statusFilter" aria-label="证据状态筛选" @change="applyFilters">
          <option value="">全部证据状态</option>
          <option v-for="key in STATUS_FILTERS" :key="key" :value="key">{{ statusLabel(key) }}</option>
        </select>
      </div>
      <div class="list-count">已载入 {{ rows.length }} / {{ total }} 项<span v-if="batchId">本页按当前批次过滤</span></div>
      <p v-if="listError" class="inline-error">{{ listError }}</p>
      <div class="br-list scroll">
        <div v-for="row in rows" :key="row.id" class="br-item" :class="{ active: row.id === selectedId }">
          <label v-if="!mergeType || mergeType === row.type" class="br-pick" title="勾选以参与合并">
            <input type="checkbox" :checked="mergeIds.includes(row.id)" :aria-label="'参与合并：' + (row.name || row.id)" @change="toggleMerge(row, ($event.target as HTMLInputElement).checked)">
          </label>
          <button type="button" class="br-item-main" :aria-current="row.id === selectedId ? 'true' : undefined" @click="select(row.id)">
            <small>{{ typeLabel(row.type) }}<template v-if="ownerName(row)"> · {{ ownerName(row) }}</template></small>
            <strong>{{ row.name || '未命名（待填写）' }}</strong>
            <span class="br-badges">
              <span class="br-badge" :class="'br-badge-' + (row.evidenceStatus || 'insufficient')">{{ statusLabel(row.evidenceStatus) }}</span>
              <span class="br-decision">{{ decisionLabel(row.decision) }}</span>
              <span v-if="row.reviewed" class="br-reviewed">已人工审阅</span>
              <span v-if="row.revived" class="br-badge br-badge-inferred">新证据再次提出</span>
            </span>
          </button>
        </div>
        <div v-if="!rows.length" class="empty">{{ listLoading ? '加载中…' : '没有匹配项' }}</div>
      </div>
      <div class="br-listfoot">
        <button v-if="hasMore" type="button" :disabled="listLoading" @click="loadMore">{{ listLoading ? '加载中…' : '加载更多' }}</button>
        <button v-if="mergeIds.length" type="button" @click="clearMerge">清除勾选</button>
      </div>
      <div class="br-mergebar">
        <span class="muted">已勾选 {{ mergeIds.length }} 项<template v-if="mergeType">（{{ typeLabel(mergeType) }}）</template></span>
        <button type="button" :disabled="busy || mergeIds.length < 2" @click="previewMerge">合并预览</button>
        <button v-if="lastOpId" type="button" :disabled="busy" title="撤销本次评审会话内最近一次合并" @click="undoLastMerge">撤销上次合并</button>
      </div>
      <p v-if="mergeError" class="field-error">{{ mergeError }}</p>
      <p class="note">合并只处理同类型重复定义；被并入候选的证据与来源全部保留，且可撤销。跨对象属性转共享需另行确认语义一致。</p>
    </section>

    <!-- ② 定义编辑 -->
    <section class="card br-col br-col-edit" aria-label="定义编辑">
      <p v-if="detailError" class="inline-error" role="alert">{{ detailError }}</p>
      <div v-if="detailLoading && !current" class="empty">加载候选详情…</div>

      <template v-if="current && draft">
        <div class="detail-heading">
          <div>
            <span class="eyebrow">{{ typeLabel(current.type) }}<template v-if="current.reviewed"> · 已人工审阅</template></span>
            <h3>{{ current.name || '未命名（待填写）' }}</h3>
          </div>
          <span class="br-badge" :class="'br-badge-' + (current.evidenceStatus || 'insufficient')">{{ statusLabel(current.evidenceStatus) }}</span>
        </div>
        <p class="muted">{{ relationText }} · {{ current.reviewed ? '已人工审阅' : '待人工审阅' }}</p>

        <div v-if="blockingDeps.length" class="notice br-banner" :class="current.decision === 'include' ? 'br-banner-bad' : 'br-banner-warn'" role="alert">
          <strong>{{ current.decision === 'include' ? '该定义依赖已排除的对象，不能最终保存。' : '该定义依赖已排除的对象；当前决定不是「拟纳入」，不会写入本体。' }}</strong>
          <ul class="br-dep-list">
            <li v-for="dep in blockingDeps" :key="dep.id">
              <button type="button" class="row-link" @click="jumpTo(dep.id)">{{ dep.name || dep.key || dep.id }}</button>
              <span class="muted">（{{ typeLabel(dep.type) }} · 已排除）</span>
            </li>
          </ul>
          <span class="muted">可恢复该对象，或把本定义改为暂缓/排除；不会静默级联删除。</span>
        </div>
        <p v-if="unresolvedRefs.length" class="note">依赖对象未在当前已载入候选中：{{ unresolvedRefs.join('、') }}。可用筛选或搜索定位后核对。</p>

        <ul v-if="current.issues.length" class="br-issues">
          <li v-for="(issue, index) in current.issues" :key="index">
            {{ issue.message || issue.code || '服务端未给出说明' }}
            <small v-if="issue.field">字段：{{ issue.field }}</small>
          </li>
        </ul>

        <label class="editor-field">
          <span class="field-title">名称 <b class="required-mark">*</b></span>
          <input v-model="draft.name" type="text" aria-label="候选名称" placeholder="请填写名称">
        </label>
        <label class="editor-field">
          <span class="field-title">业务定义 <b class="required-mark">*</b></span>
          <textarea v-model="draft.definition" aria-label="业务定义" placeholder="说明它是什么、用什么业务边界区分；不补写没有依据的单位或参数"></textarea>
        </label>

        <template v-if="current.type === 'property'">
          <label class="editor-field">
            <span class="field-title">数据类型</span>
            <select v-model="draft.dataType" aria-label="数据类型">
              <option value="">未确定（需依据）</option>
              <option v-for="option in DATA_TYPE_OPTIONS" :key="option.value" :value="option.value">{{ option.label }}</option>
            </select>
          </label>
          <label v-if="draft.dataType === 'timeSeries'" class="editor-field">
            <span class="field-title">观测值类型 <b class="required-mark">*</b></span>
            <select v-model="draft.valueType" aria-label="观测值类型">
              <option value="">请选择观测值类型</option>
              <option v-for="option in VALUE_TYPE_OPTIONS" :key="option.value" :value="option.value">{{ option.label }}</option>
            </select>
            <small class="field-help">时间序列必须明确观测值类型；未确认时先暂缓，不按字段名或单位推测。</small>
          </label>
        </template>

        <template v-else-if="current.type === 'link' && linkEndpoints">
          <div class="br-endpoints">
            <div><small class="muted">起点对象（只读）</small><strong>{{ linkEndpoints.sourceName }}</strong></div>
            <span class="br-arrow" aria-hidden="true">→</span>
            <div><small class="muted">终点对象（只读）</small><strong>{{ linkEndpoints.targetName }}</strong></div>
          </div>
          <div class="br-cardinality">
            <label class="editor-field">
              <span class="field-title">起点数量关系</span>
              <select v-model="draft.cardSource" aria-label="起点数量关系">
                <option v-for="option in ONE_MANY_OPTIONS" :key="'s' + option.value" :value="option.value">{{ option.label }}</option>
              </select>
            </label>
            <label class="editor-field">
              <span class="field-title">终点数量关系</span>
              <select v-model="draft.cardTarget" aria-label="终点数量关系">
                <option v-for="option in ONE_MANY_OPTIONS" :key="'t' + option.value" :value="option.value">{{ option.label }}</option>
              </select>
            </label>
          </div>
          <p v-if="!draft.cardSource || !draft.cardTarget" class="notice br-banner br-banner-warn">
            数量关系缺少依据：无依据不能默认多对一，也不能由页面替材料下判断。请按材料补齐，或暂缓该链接。
          </p>
        </template>

        <label v-else-if="current.type === 'rule'" class="editor-field">
          <span class="field-title">规则内容 <span class="optional-label">选填</span></span>
          <textarea v-model="draft.content" aria-label="规则内容" placeholder="不确定时留空，不推断未证实的口径"></textarea>
        </label>

        <label v-else-if="current.type === 'action'" class="editor-field">
          <span class="field-title">预期效果 <span class="optional-label">选填</span></span>
          <textarea v-model="draft.effect" aria-label="预期效果" placeholder="不确定时留空，不推断未证实效果"></textarea>
        </label>

        <div class="detail-footer">
          <span :class="dirty ? 'inline-warning' : 'muted'">{{ dirty ? '有未保存的编辑' : '与服务端最新值一致' }}</span>
          <div class="tools">
            <button type="button" class="primary" :disabled="saving || !dirty" @click="saveEdit">{{ saving ? '保存中…' : '保存编辑' }}</button>
            <button type="button" :disabled="saving || !dirty" @click="cancelEdit">取消编辑</button>
          </div>
        </div>
        <p v-if="editError" class="field-error" role="alert">{{ editError }}</p>

        <div class="br-decide">
          <h4>本次决定：{{ decisionLabel(current.decision) }}</h4>
          <template v-if="needsReason">
            <p class="notice br-banner br-banner-warn">{{ statusHint }}</p>
            <label class="editor-field">
              <span class="field-title">人工确认依据<template v-if="current.decision !== 'include'">（纳入时必填）</template></span>
              <textarea v-model="reasonDraft" aria-label="人工确认依据" placeholder="说明采用哪份依据，或补充你的业务确认；不能以模型自评分代替证据"></textarea>
            </label>
          </template>
          <div class="tools">
            <button type="button" :class="{ primary: current.decision === 'include' }" :disabled="busy" @click="decide('include')">纳入</button>
            <button type="button" :class="{ primary: current.decision === 'defer' }" :disabled="busy" @click="decide('defer')">暂缓</button>
            <button type="button" class="br-danger" :class="{ 'br-danger-on': current.decision === 'exclude' }" :disabled="busy" @click="decide('exclude')">排除</button>
          </div>
          <p v-if="decideError" class="field-error" role="alert">{{ decideError }}</p>
          <p class="note">排除可恢复；排除对象不会自动删除其属性或链接，依赖未处理的定义不能最终保存。</p>
        </div>

        <section v-if="mergePreview" class="br-merge">
          <h4>合并预览（保留项：{{ mergePrimary || mergePreview.primaryId }}）</h4>
          <div class="br-tablewrap">
            <table>
              <thead><tr><th>字段</th><th>保留值</th><th>被并入值</th><th>是否不同</th></tr></thead>
              <tbody>
                <tr v-for="field in mergePreview.fields" :key="field.name">
                  <td>{{ field.name }}</td>
                  <td>{{ field.primaryValue || '—' }}</td>
                  <td>{{ field.mergeValues.join('；') || '—' }}</td>
                  <td>{{ field.diff ? '不同' : '相同' }}</td>
                </tr>
                <tr v-if="!mergePreview.fields.length"><td colspan="4" class="muted">服务端未返回字段差异明细。</td></tr>
              </tbody>
            </table>
          </div>
          <p class="muted">
            证据数：{{ mergePreview.evidenceCount === null ? '服务端未返回' : mergePreview.evidenceCount }}
            · 合并后双方证据与来源全部保留到保留项。
          </p>
          <ul v-if="mergePreview.warnings.length" class="br-issues">
            <li v-for="(warning, index) in mergePreview.warnings" :key="index">{{ warning }}</li>
          </ul>
          <div class="tools">
            <button type="button" class="primary" :disabled="busy" @click="confirmMerge">确认合并</button>
            <button type="button" :disabled="busy" @click="mergePreview = null">取消</button>
          </div>
        </section>
      </template>

      <div v-else-if="!detailLoading" class="empty-state">
        <p>从左侧候选列表选择一项，查看并裁剪生成结果。</p>
        <p class="muted">候选为空时可返回生成范围重新确认后重跑；暂缓项与排除项都会保留。</p>
      </div>
    </section>

    <!-- ③ 定义依据 -->
    <section class="card br-col br-col-evidence" aria-label="定义依据">
      <h3>定义依据</h3>
      <p class="muted">按字段定位原文；文件、位置、片段与解析质量全部来自服务端解析事实，人工编辑不改写来源原文。</p>
      <p v-if="batchLine" class="muted br-batch">{{ batchLine }}</p>
      <div class="scroll br-evidence">
        <template v-if="evidence.length">
          <article v-for="group in evidence" :key="group.field" class="br-evfield">
            <h4>{{ fieldLabel(group.field) }}</h4>
            <div v-for="(item, index) in group.items" :key="item.factId || index" class="br-evitem">
              <small class="br-evfile">{{ item.materialRelPath || '（服务端未返回材料路径）' }}</small>
              <small v-if="item.missing" class="br-evwarn">引用位置不存在（服务端已剔除）</small>
              <small v-else class="br-evloc">{{ locatorText(item.locator) }}</small>
              <small class="br-evmeta">
                解析质量：{{ item.quality ? qualityLabel(item.quality) : '服务端未返回' }}
                <template v-if="item.materialRevision !== null"> · 材料修订 r{{ item.materialRevision }}</template>
              </small>
              <pre v-if="item.snippet">{{ item.snippet }}</pre>
              <small v-else class="br-evloc">（该条未返回原文片段）</small>
            </div>
          </article>
        </template>
        <div v-else class="empty">
          <template v-if="detailLoading">证据加载中…</template>
          <template v-else-if="current">该候选暂无字段级证据（服务端未返回）。没有依据不能以推断替代证据：请补充人工确认依据或暂缓。</template>
          <template v-else>选择候选后在此查看字段级证据。</template>
        </div>
      </div>
    </section>
  </div>
</div>
</template>

<style scoped>
/* 三栏评审（原型 .reviewgrid）：宽屏 3 栏，≤1200px 证据列落到第二行，≤900px 单栏。 */
.br-root{display:block}
.br-head{display:flex;align-items:flex-start;justify-content:space-between;gap:14px;flex-wrap:wrap;margin-bottom:12px}
.br-head-main{flex:1;min-width:280px}
.br-head-main h2{margin:4px 0 0}
.br-head-main p{margin:6px 0 0}
.br-banner{margin:12px 0;border-radius:var(--r-sm);padding:12px 15px;font-size:13px}
.br-banner-warn{background:var(--warn-soft);border:1px solid var(--warn-line);color:var(--warn)}
.br-banner-bad{background:var(--danger-soft);border:1px solid var(--danger-line);color:var(--danger)}
.reviewgrid{display:grid;grid-template-columns:270px minmax(320px,1fr) minmax(290px,.9fr);gap:14px;align-items:start}
.br-col{padding:16px;min-width:0;margin-bottom:0}
.br-col h3{margin-bottom:10px}
.br-col-list input[type=search]{margin:0 0 10px}
.br-filters{display:grid;gap:6px;margin-bottom:8px}
.br-list{max-height:560px;overflow:auto;margin:2px 0 8px}
.br-item{display:flex;align-items:flex-start;gap:6px;border:1px solid transparent;border-radius:var(--r-sm);margin:4px 0}
.br-item.active{border-color:var(--blue-line);background:var(--blue-soft)}
.br-pick{display:flex;align-items:center;padding:14px 0 0 8px}
.br-pick input{width:16px;height:16px;min-width:16px;margin:0}
.br-item-main{display:block;width:100%;text-align:left;border:0;background:transparent;padding:11px 10px;white-space:normal}
.br-item-main strong{display:block;font-size:14px;font-weight:600}
.br-item-main small{display:block;font-size:12px;color:var(--muted);margin-bottom:3px;overflow-wrap:anywhere}
.br-badges{display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin-top:6px}
.br-badge{display:inline-block;font-size:11px;padding:2px 8px;border-radius:var(--r-pill);border:1px solid var(--line);background:var(--paper-2);color:var(--muted);white-space:nowrap}
.br-badge-supported{background:var(--ok-soft);border-color:var(--ok-line);color:var(--ok)}
.br-badge-inferred{background:var(--warn-soft);border-color:var(--warn-line);color:var(--warn)}
.br-badge-conflict{background:var(--danger-soft);border-color:var(--danger-line);color:var(--danger)}
.br-badge-insufficient{background:var(--paper-2);border-color:var(--line-2);color:var(--muted)}
.br-decision{font-size:12px;color:var(--ink-2);white-space:nowrap}
.br-reviewed{font-size:11px;color:var(--ok);white-space:nowrap}
.br-listfoot{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:8px}
.br-mergebar{display:flex;align-items:center;gap:8px;flex-wrap:wrap;border-top:1px solid var(--line);padding-top:10px}
.br-mergebar .muted{margin-right:auto;font-size:12px}
.br-diffgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:12px}
.br-diffbox{border:1px solid var(--line);border-radius:var(--r-sm);padding:12px}
.br-diffbox h4{font-size:13px;margin:0 0 8px}
.br-diffbox ul{list-style:none;margin:0;padding:0}
.br-diffbox li{padding:5px 0;border-bottom:1px solid var(--paper-3);font-size:13px}
.br-diffbox li:last-child{border-bottom:0}
.br-diffnote{display:block;font-size:12px;color:var(--muted)}
.br-conflict{display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin-top:4px}
.br-conflict em{font-style:normal;font-size:12px;color:var(--warn)}
.br-endpoints{display:flex;align-items:center;gap:10px;flex-wrap:wrap;background:var(--paper-2);border-radius:var(--r-sm);padding:12px 14px;margin:14px 0}
.br-endpoints div{flex:1;min-width:120px}
.br-endpoints strong{display:block;font-size:14px;margin-top:3px;overflow-wrap:anywhere}
.br-arrow{color:var(--muted)}
.br-cardinality{display:grid;grid-template-columns:1fr 1fr;gap:0 14px}
.br-issues{list-style:none;margin:10px 0;padding:0}
.br-issues li{border-left:3px solid var(--warn-line);background:var(--warn-soft);padding:8px 11px;margin:6px 0;font-size:13px;color:var(--warn)}
.br-issues small{display:block;color:var(--muted);font-size:12px;margin-top:2px}
.br-dep-list{list-style:none;margin:6px 0;padding:0}
.br-decide{border-top:1px solid var(--line);margin-top:20px;padding-top:16px}
.br-decide h4{font-size:14px;margin:0 0 8px}
.br-decide .tools{margin-top:10px;flex-wrap:wrap}
.br-danger{color:var(--danger)}
.br-danger-on{background:var(--danger-soft);border-color:var(--danger-line);color:var(--danger);font-weight:600}
.br-merge{border-top:1px solid var(--line);margin-top:20px;padding-top:16px}
.br-merge h4{font-size:14px;margin:0 0 10px}
.br-merge .tools{margin-top:12px;gap:8px}
.br-tablewrap{overflow:auto}
.br-tablewrap table{font-size:13px}
.br-batch{font-size:12px}
.br-evidence{max-height:640px;overflow:auto;padding-right:4px}
.br-evfield{padding:8px 0 12px;border-bottom:1px solid var(--line)}
.br-evfield:last-child{border-bottom:0}
.br-evfield h4{font-size:13px;margin:0 0 6px}
.br-evitem{padding:8px 0 10px}
.br-evfile{display:block;font-size:12px;color:var(--ink-2);overflow-wrap:anywhere}
.br-evloc{display:block;font-size:12px;color:var(--muted);overflow-wrap:anywhere}
.br-evwarn{display:block;font-size:12px;color:var(--danger);overflow-wrap:anywhere}
.br-evmeta{display:block;font-size:12px;color:var(--muted);margin-top:2px}
.br-evitem pre{margin:7px 0 0;font-size:12px;white-space:pre-wrap;overflow-wrap:anywhere;background:var(--paper-2);border-radius:var(--r-sm);padding:10px}
@media(max-width:1200px){
  .reviewgrid{grid-template-columns:240px minmax(0,1fr)}
  .br-col-evidence{grid-column:2}
}
@media(max-width:900px){
  .reviewgrid{grid-template-columns:1fr}
  .br-col-evidence{grid-column:auto}
  .br-list{max-height:260px}
  .br-cardinality{grid-template-columns:1fr}
}
</style>
