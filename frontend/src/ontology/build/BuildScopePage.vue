<!-- ─── A03 确定范围（从物料自动构建本体；契约：接口文档 08 §3/§5/§6）───
     布局基准：需求原型 交互原型_v1.html 的 scopePage()（第 51 行）——
     左侧「范围讨论」对话区（消息气泡 + 自由输入），右侧「本次范围」可编辑摘要 + 依据/未决问题折叠块。
     行为要点（需求 §5）：
     · 助手回复异步生成：发送后每 1.5 秒拉一次 build-messages 增量，直到没有 pending，最长 60 秒；卸载清定时器。
     · 助手 patch 只是建议：显式展示并给「采纳到右侧」，只填人工未改动的字段，绝不自动覆盖人工输入。
     · 人工修改标记「未保存」；保存摘要回传当前 scopeRevision，409 明确提示刷新并重新加载，不静默丢弃输入。
     · 阻断问题（同一模块同时纳入与排除且覆盖说明为空、阻断性未决问题未处理）时禁用确认并列出原因。
     · 「确认范围并生成」回传 providerId（来自 build-capabilities）；无可用模型时禁用并引导去 LLM 配置。
     · 材料/范围变化使旧结果过期：提示「旧结果已过期，仅供回看」，但不阻塞编辑。
     请求一律经 http 层（app/http.ts）；08 §6 的四个端点由本组件内联本地 helper（api.ts 本轮只覆盖到 §5）。 -->
<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import AppError from '../../shared/AppError.vue'
import { getJson, postJson } from '../../app/http'
import { errorMessage, fetchCapabilities, fetchTask, conflictRevision } from './api'
import type { BuildTaskDetail } from './api'
import { coverageOf, materialKindLabel, parseStateLabel } from './types'
import type { BuildRun, ScopeModel, ScopeOpenQuestion } from './types'

const props = defineProps<{ taskId: string }>()
const emit = defineEmits<{ back: []; generated: [runId: string] }>()

// ── 08 §6 范围对话端点（api.ts 未提供，按分工在本组件内联；仍全部经 http 层，绝不自行 fetch） ──
const EP = {
  messages: '/api/build-messages',
  message: '/api/build-message',
  scopeSave: '/api/build-scope-save',
  scopeConfirm: '/api/build-scope-confirm',
} as const

/** 助手给出的范围补丁（08 §1.4 patch，可空；未列出的字段表示没有建议）。 */
interface ScopePatch { goal?: string; include?: string; exclude?: string; relations?: string; coverage?: string }
interface ScopeMessage {
  id: string
  taskId: string
  role: string
  content: string
  patch: ScopePatch | null
  createdAt: string
  /** 助手回复生成失败（消息流内错误，可重试） */
  error?: string
  assistantError?: string
  /** 该条助手消息仍在生成中 */
  pending?: boolean
}
interface MessagePage {
  messages: ScopeMessage[]
  scope: ScopeModel | null
  revision: number
  /** 服务端判定：材料/范围变化后本范围对应的旧结果已过期 */
  stale: boolean
  assistantPending?: boolean
}
interface ScopeConfirmResult { runId: string; batchId: string }
interface ScopeSaveResult { scope: ScopeModel }

/** 可编辑范围字段（openQuestions 不由本页编辑，保存时按 08 §6 原样回传服务端的当前值）。 */
interface ScopeForm { goal: string; include: string; exclude: string; relations: string; coverage: string }
interface ScopePayload extends ScopeForm { openQuestions: ScopeOpenQuestion[] }

/** stale/batch 已由 api.ts 的 BuildTaskDetail 声明（可选，旧服务端不返回即为 undefined）；本页只额外读运行上的 stale。 */
interface RunExtra extends BuildRun { stale?: boolean }

async function fetchMessages(taskId: string, after: number): Promise<MessagePage> {
  return await getJson(EP.messages + '?taskId=' + encodeURIComponent(taskId) + '&after=' + after) as MessagePage
}
async function sendMessage(taskId: string, content: string, revision: number): Promise<{ messageId: string; assistantPending: boolean }> {
  return await postJson(EP.message, { taskId, content, revision }) as { messageId: string; assistantPending: boolean }
}
/** 保存范围：08 §6 的 scope 字段齐全（openQuestions 非本页编辑，回传服务端当前值，避免被清空）。 */
async function saveScope(taskId: string, scope: ScopeForm, openQuestions: ScopeOpenQuestion[], revision: number): Promise<ScopeSaveResult> {
  const payload: ScopePayload = { ...scope, openQuestions: openQuestions.map(q => ({ text: q.text, blocking: q.blocking })) }
  return await postJson(EP.scopeSave, { taskId, scope: payload, revision }) as ScopeSaveResult
}
async function confirmScope(taskId: string, revision: number, providerId: string): Promise<ScopeConfirmResult> {
  return await postJson(EP.scopeConfirm, { taskId, revision, providerId }) as ScopeConfirmResult
}

// ── 页面状态 ──────────────────────────────────────────────────────────────
const EMPTY_SCOPE: ScopeForm = { goal: '', include: '', exclude: '', relations: '', coverage: '' }
const PATCH_FIELDS: { key: keyof ScopePatch; field: keyof ScopeForm; label: string }[] = [
  { key: 'goal', field: 'goal', label: '建模目标' },
  { key: 'include', field: 'include', label: '纳入范围' },
  { key: 'exclude', field: 'exclude', label: '排除范围' },
  { key: 'relations', field: 'relations', label: '必要关联与深度' },
  { key: 'coverage', field: 'coverage', label: '覆盖说明' },
]

const taskName = ref('')
const form = ref<ScopeForm>({ ...EMPTY_SCOPE })
/** 最近一次由服务端确认的范围：判断「未保存」与采纳建议时的覆盖边界。 */
const savedForm = ref<ScopeForm>({ ...EMPTY_SCOPE })
const questions = ref<ScopeOpenQuestion[]>([])
const materials = ref<BuildTaskDetail['materials']>([])
const materialRevision = ref<number | null>(null)
const scopeRevision = ref(0)
const staleFromServer = ref(false)
const staleFromMaterial = ref(false)
const deliveredOntologyId = ref('')

const loading = ref(false)
const loadError = ref('')
const notFound = ref(false)
const loaded = ref(false)

const saving = ref(false), saveError = ref(''), conflict = ref(false), notice = ref('')
const confirming = ref(false), confirmError = ref(''), confirmIssues = ref<string[]>([])
const providerId = ref(''), providerName = ref(''), providerError = ref('')

const messages = ref<ScopeMessage[]>([])
const draft = ref('')
const sending = ref(false), chatError = ref(''), chatBusy = ref(false), adviceNote = ref('')
const pending = ref(false)
const chatLog = ref<HTMLElement | null>(null)

const POLL_INTERVAL_MS = 1500
const POLL_MAX_MS = 60000
let pollTimer: ReturnType<typeof setTimeout> | null = null
let pollDeadline = 0
let disposed = false
let cursor = 0

const dirty = computed(() => JSON.stringify(form.value) !== JSON.stringify(savedForm.value))
const stale = computed(() => staleFromServer.value || staleFromMaterial.value)
const staleNote = computed(() => stale.value
  ? '旧结果已过期，仅供回看：材料或范围已经变化，请核对右侧摘要后重新确认并生成新批次。'
  : '')

// 材料覆盖（08 §1.2 coverage）：部分失败、失败、排除分别列出，缺口进入生成与保存摘要。
const activeMaterials = computed(() => materials.value.filter(m => !m.excluded))
const parsedMaterials = computed(() => activeMaterials.value.filter(m => m.parseState === 'success' || m.parseState === 'partial'))
const partialMaterials = computed(() => activeMaterials.value.filter(m => m.parseState === 'partial' || coverageOf(m).failedSegments.length > 0))
const failedMaterials = computed(() => activeMaterials.value.filter(m => m.parseState === 'failed'))
const excludedMaterials = computed(() => materials.value.filter(m => m.excluded))
const coverageGaps = computed(() => materials.value
  .map(m => {
    const c = coverageOf(m)
    const lines: string[] = []
    if (c.failedSegments.length > 0) lines.push(`${c.failedSegments.length} 个片段未解析：${c.failedSegments.slice(0, 5).join('、')}`)
    lines.push(...c.notes)
    return { id: m.id, name: m.relPath, lines }
  })
  .filter(g => g.lines.length > 0))
const materialSummary = computed(() => {
  const parts = [`已纳入材料 ${activeMaterials.value.length} 份`]
  if (parsedMaterials.value.length > 0) parts.push(`解析可用 ${parsedMaterials.value.length} 份`)
  if (partialMaterials.value.length > 0) parts.push(`部分成功 ${partialMaterials.value.length} 份`)
  if (failedMaterials.value.length > 0) parts.push(`失败 ${failedMaterials.value.length} 份`)
  if (excludedMaterials.value.length > 0) parts.push(`已排除 ${excludedMaterials.value.length} 份`)
  return parts.join(' · ')
})

// 纳入/排除矛盾（需求 §5.2、08 §6 的前置条件）：本地先给提示，最终仍以服务端 422 issues 为准。
function tokensOf(text: string): string[] {
  return text.split(/[\s、,，;；/｜|·]+/).map(t => t.trim()).filter(t => t.length >= 2)
}
const overlapTokens = computed(() => {
  const excluded = tokensOf(form.value.exclude).map(t => t.toLowerCase())
  const hit: string[] = []
  for (const token of tokensOf(form.value.include)) {
    const lower = token.toLowerCase()
    if (excluded.includes(lower) && !hit.includes(token)) hit.push(token)
  }
  return hit
})
const blockingIssues = computed(() => {
  const issues: string[] = []
  if (overlapTokens.value.length > 0 && form.value.coverage.trim() === '') {
    issues.push(`同一模块同时出现在纳入与排除：${overlapTokens.value.join('、')}。请在「覆盖说明」写明取舍后再确认。`)
  }
  for (const q of questions.value) {
    if (q.blocking) issues.push(`阻断性未决问题未处理：${q.text}`)
  }
  return issues
})
const disableReasons = computed(() => {
  const reasons = [...blockingIssues.value]
  if (parsedMaterials.value.length === 0) reasons.push('至少需要一份已成功解析的材料，请先回到物料页完成扫描。')
  if (providerId.value === '') reasons.push('当前账号没有可用的模型：请先到「更多工具 → LLM 配置」配置并提供默认模型。')
  return reasons
})
const canConfirm = computed(() => disableReasons.value.length === 0 && !confirming.value && !saving.value)

// ── 装载任务与能力 ────────────────────────────────────────────────────────
function applyScope(scope: ScopeModel | null | undefined, force: boolean) {
  if (!scope || typeof scope !== 'object') return
  questions.value = Array.isArray(scope.openQuestions) ? scope.openQuestions : []
  if (typeof scope.revision === 'number') scopeRevision.value = scope.revision
  // 服务端返回的范围只在本地没有未保存修改时套用，绝不冲掉人工输入。
  if (!force && dirty.value) return
  const next: ScopeForm = {
    goal: scope.goal || '', include: scope.include || '', exclude: scope.exclude || '',
    relations: scope.relations || '', coverage: scope.coverage || '',
  }
  form.value = { ...next }
  savedForm.value = { ...next }
}

async function loadTask(initial: boolean) {
  if (props.taskId === '') { loadError.value = '缺少任务标识，无法加载范围。'; return }
  loading.value = true
  try {
    const detail: BuildTaskDetail = await fetchTask(props.taskId)
    taskName.value = detail.task?.name || ''
    deliveredOntologyId.value = detail.task?.deliveryOntologyId || detail.delivery?.ontologyId || ''
    const nextMaterialRevision = typeof detail.task?.materialRevision === 'number' ? detail.task.materialRevision : null
    if (initial) materialRevision.value = nextMaterialRevision
    else if (nextMaterialRevision !== null && materialRevision.value !== null && nextMaterialRevision !== materialRevision.value) {
      staleFromMaterial.value = true
      materialRevision.value = nextMaterialRevision
    } else if (nextMaterialRevision !== null) materialRevision.value = nextMaterialRevision
    materials.value = Array.isArray(detail.materials) ? detail.materials : []
    applyScope(detail.scope, initial)
    const run = detail.run as RunExtra | null
    if (detail.stale === true || detail.batch?.stale === true || run?.stale === true) staleFromServer.value = true
    loadError.value = ''
    notFound.value = false
    loaded.value = true
  } catch (error) {
    if (isNotFound(error)) { notFound.value = true; loadError.value = '' }
    else loadError.value = errorMessage(error)
  } finally {
    loading.value = false
  }
}

function isNotFound(error: unknown): boolean {
  return error !== null && typeof error === 'object' && (error as { status?: number }).status === 404
}
/** 服务端 422 issues（08 通用错误码）：逐条展示，不吞。 */
function issuesOf(error: unknown): string[] {
  const data = (error as { data?: { issues?: unknown } } | null)?.data
  const list = data?.issues
  if (!Array.isArray(list)) return []
  return list.map(item => {
    if (typeof item === 'string') return item
    const o = item as { message?: string; code?: string }
    return o?.message || o?.code || ''
  }).filter(text => text !== '')
}

async function loadCapabilities() {
  try {
    const caps = await fetchCapabilities()
    providerId.value = caps.provider?.id || ''
    providerName.value = caps.provider ? [caps.provider.name, caps.provider.model].filter(t => !!t).join(' · ') : ''
    providerError.value = ''
  } catch (error) {
    providerError.value = errorMessage(error)
  }
}

// ── 对话增量轮询（1.5 秒；最长 60 秒；卸载清理） ────────────────────────────
function mergeMessages(list: ScopeMessage[]) {
  if (!Array.isArray(list) || list.length === 0) return
  const seen = new Set(messages.value.map(m => m.id || `${m.role}|${m.createdAt}|${m.content}`))
  let added = 0
  for (const m of list) {
    const key = m.id || `${m.role}|${m.createdAt}|${m.content}`
    if (seen.has(key)) continue
    seen.add(key)
    messages.value.push(m)
    added += 1
  }
  if (added > 0) void scrollChatToBottom()
}

/** 助手是否仍在生成：优先服务端标志，其次消息级 pending，最后看是否以未答复的用户消息结尾。 */
function pendingFrom(page: MessagePage, list: ScopeMessage[]): boolean {
  if (typeof page.assistantPending === 'boolean') return page.assistantPending
  if (list.some(m => m.pending === true)) return true
  const last = list[list.length - 1]
  if (!last || last.role !== 'user') return false
  // 已有助手失败记录时不再空转轮询：错误已展示，等用户显式重试。
  return !list.some(m => (m.error || m.assistantError || '') !== '')
}

async function pullMessages(incremental: boolean): Promise<boolean> {
  const from = incremental ? cursor : 0
  if (!incremental) cursor = 0
  const page = await fetchMessages(props.taskId, from)
  const list = Array.isArray(page.messages) ? page.messages : []
  mergeMessages(list)
  cursor = from + list.length
  applyScope(page.scope, false)
  if (page.stale === true) staleFromServer.value = true
  if (typeof page.revision === 'number' && page.revision > 0) scopeRevision.value = page.revision
  chatError.value = ''
  return pendingFrom(page, list.length > 0 ? list : messages.value)
}

function stopPolling() {
  if (pollTimer !== null) { clearTimeout(pollTimer); pollTimer = null }
  pending.value = false
}
function schedulePoll() {
  if (disposed) return
  pollTimer = setTimeout(() => { pollTimer = null; void pollTick() }, POLL_INTERVAL_MS)
}
function startPolling() {
  stopPolling()
  pollDeadline = Date.now() + POLL_MAX_MS
  pending.value = true
  schedulePoll()
}
async function pollTick() {
  if (disposed) return
  chatBusy.value = true
  try {
    const stillPending = await pullMessages(true)
    if (!stillPending || Date.now() >= pollDeadline) { stopPolling(); return }
  } catch (error) {
    chatError.value = errorMessage(error)
    if (Date.now() >= pollDeadline) { stopPolling(); return }
  } finally {
    chatBusy.value = false
  }
  schedulePoll()
}

function scrollChatToBottom(): Promise<void> {
  return Promise.resolve().then(() => {
    const el = chatLog.value
    if (el) el.scrollTop = el.scrollHeight
  })
}

async function onSend() {
  const content = draft.value.trim()
  if (content === '' || sending.value) return
  sending.value = true
  chatError.value = ''
  adviceNote.value = ''
  try {
    const result = await sendMessage(props.taskId, content, scopeRevision.value)
    draft.value = ''
    // 用户消息立即落库；助手回复异步生成，先拉一次增量，再按 1.5 秒继续轮询。
    const stillPending = await pullMessages(true).catch((error: unknown) => { chatError.value = errorMessage(error); return true })
    if (stillPending || result.assistantPending === true) startPolling()
    else await scrollChatToBottom()
  } catch (error) {
    chatError.value = errorMessage(error)
  } finally {
    sending.value = false
  }
}

async function retryAssistant() {
  if (sending.value) return
  sending.value = true
  chatError.value = ''
  try {
    const stillPending = await pullMessages(true)
    if (stillPending) startPolling()
    else notice.value = '助手消息已更新。'
  } catch (error) {
    chatError.value = errorMessage(error)
  } finally {
    sending.value = false
  }
}

function patchText(message: ScopeMessage, key: keyof ScopePatch): string {
  const value = message.patch ? message.patch[key] : ''
  return typeof value === 'string' ? value : ''
}
function patchSummary(message: ScopeMessage): string {
  return PATCH_FIELDS.map(item => {
    const value = patchText(message, item.key)
    return value === '' ? '' : `${item.label}=${value}`
  }).filter(text => text !== '').join('；')
}
/** 采纳建议：只填人工未改动的字段（空值或仍等于服务端基线），已人工填写的一律保留。 */
function adoptPatch(message: ScopeMessage) {
  const applied: string[] = [], kept: string[] = []
  for (const item of PATCH_FIELDS) {
    const value = patchText(message, item.key)
    if (value === '') continue
    const current = form.value[item.field]
    const untouched = current.trim() === '' || current === savedForm.value[item.field]
    if (untouched) { form.value[item.field] = value; applied.push(item.label) }
    else kept.push(item.label)
  }
  if (applied.length === 0 && kept.length === 0) { adviceNote.value = '这条建议没有可采纳的文本字段。'; return }
  const parts: string[] = []
  if (applied.length > 0) parts.push(`已采纳：${applied.join('、')}（保存摘要后生效）`)
  if (kept.length > 0) parts.push(`你已手工填写，未被覆盖：${kept.join('、')}`)
  adviceNote.value = parts.join('；')
}

async function reloadAll() {
  stopPolling()
  conflict.value = false
  saveError.value = ''
  notice.value = ''
  messages.value = []
  cursor = 0
  scopeRevision.value = 0
  staleFromServer.value = false
  staleFromMaterial.value = false
  await loadTask(true)
  try {
    const stillPending = await pullMessages(false)
    if (stillPending) startPolling()
  } catch (error) {
    chatError.value = errorMessage(error)
  }
}

// ── 保存与确认 ────────────────────────────────────────────────────────────
async function onSaveScope() {
  if (saving.value) return
  saving.value = true
  saveError.value = ''
  conflict.value = false
  notice.value = ''
  try {
    const result = await saveScope(props.taskId, { ...form.value }, questions.value, scopeRevision.value)
    const scope = result.scope
    if (scope) {
      const next: ScopeForm = {
        goal: scope.goal || '', include: scope.include || '', exclude: scope.exclude || '',
        relations: scope.relations || '', coverage: scope.coverage || '',
      }
      form.value = { ...next }
      savedForm.value = { ...next }
      questions.value = Array.isArray(scope.openQuestions) ? scope.openQuestions : questions.value
      if (typeof scope.revision === 'number') scopeRevision.value = scope.revision
    } else {
      savedForm.value = { ...form.value }
    }
    adviceNote.value = ''
    notice.value = '范围摘要已保存，将作为本次生成的正式依据。'
  } catch (error) {
    if (conflictRevision(error) !== null) {
      conflict.value = true
      saveError.value = '范围摘要已被其他会话修改（revision 冲突）。请刷新并重新加载最新范围后再保存；你的输入仍保留在表单中。'
    } else {
      saveError.value = errorMessage(error)
    }
  } finally {
    saving.value = false
  }
}

function discardEdits() {
  form.value = { ...savedForm.value }
  saveError.value = ''
  conflict.value = false
}

async function onConfirm() {
  if (!canConfirm.value) return
  confirming.value = true
  confirmError.value = ''
  confirmIssues.value = []
  try {
    const result = await confirmScope(props.taskId, scopeRevision.value, providerId.value)
    if (!result.runId) throw new Error('服务未返回生成批次标识，请稍后重试。')
    emit('generated', result.runId)
  } catch (error) {
    confirmError.value = errorMessage(error)
    confirmIssues.value = issuesOf(error)
  } finally {
    confirming.value = false
  }
}

watch(() => props.taskId, () => { void reloadAll() })
onMounted(() => {
  void reloadAll()
  void loadCapabilities()
})
onUnmounted(() => {
  disposed = true
  stopPolling()
})
</script>

<template>
<div class="bp-scope">
  <header class="bp-head">
    <div>
      <h1>确定建模范围</h1>
      <p class="muted">
        不用一次说清楚：结合材料逐步确认，右侧摘要是本次生成的正式依据。
        <template v-if="taskName">当前任务：{{ taskName }}</template>
      </p>
    </div>
    <div class="bp-head-actions">
      <span v-if="dirty" class="status-pill pill-warn">范围摘要未保存</span>
      <span v-else-if="loaded" class="status-pill pill-ok">已与服务端一致</span>
      <button type="button" @click="emit('back')">← 返回物料</button>
    </div>
  </header>

  <AppError
    v-if="notFound"
    title="未找到该生成任务"
    reason="任务可能已被删除，或不属于当前账号。"
    hint="返回任务列表后可以新建任务；已交付的本体不会被删除。"
    retry-label="重新加载"
    secondary-label="返回物料"
    @retry="reloadAll()"
    @secondary="emit('back')"
  />
  <AppError
    v-else-if="loadError !== ''"
    title="范围页加载失败"
    :reason="loadError"
    hint="请重试；范围草稿在服务端，重新加载不会丢失已保存内容。"
    retry-label="重新加载"
    @retry="reloadAll()"
  />

  <p v-if="staleNote !== ''" class="bp-banner">{{ staleNote }}</p>

  <div class="bp-grid">
    <!-- 左：范围讨论 -->
    <section class="card bp-chat">
      <div class="bp-card-head">
        <h2>范围讨论</h2>
        <span v-if="pending" class="status-pill">助手回复生成中…</span>
        <span v-else-if="chatBusy" class="status-pill">正在读取新消息</span>
      </div>
      <div ref="chatLog" class="bp-log">
        <p v-if="messages.length === 0 && !loading" class="muted bp-log-empty">
          还没有对话。可以直接在下方说明建模目标；助手会基于已解析材料，每轮提出 1–2 个影响建模边界的问题。
        </p>
        <p v-else-if="loading && messages.length === 0" class="muted bp-log-empty">正在读取对话…</p>
        <article
          v-for="(m, i) in messages" :key="m.id || i"
          class="bp-msg"
          :class="{ 'bp-msg-you': m.role === 'user', 'bp-msg-system': m.role !== 'user' && m.role !== 'assistant' }"
        >
          <strong>{{ m.role === 'user' ? '你' : m.role === 'assistant' ? '建模助手' : '系统消息' }}</strong>
          <p class="bp-msg-body">{{ m.content }}</p>
          <div v-if="patchSummary(m) !== ''" class="bp-suggest">
            <p class="bp-suggest-title">助手建议（仅供你确认，不会自动写入右侧）：{{ patchSummary(m) }}</p>
            <button type="button" class="mini" @click="adoptPatch(m)">采纳到右侧</button>
          </div>
          <p v-if="(m.error || m.assistantError)" class="inline-error">
            助手回复生成失败：{{ m.error || m.assistantError }}
            <button type="button" class="row-link" :disabled="sending" @click="retryAssistant()">重试拉取</button>
          </p>
        </article>
      </div>
      <div class="bp-composer">
        <label class="sr-only" for="bp-chat-input">范围补充</label>
        <textarea
          id="bp-chat-input" v-model="draft" class="bp-input" :disabled="sending"
          placeholder="输入你的想法：业务目标、要纳入或排除的模块、关联概念保留到什么程度。"
        ></textarea>
        <div class="bp-composer-foot">
          <small class="muted">每轮聚焦关键歧义，不要求回答所有细节；你手工改过的摘要不会被助手回复覆盖。</small>
          <button type="button" class="primary" :disabled="sending || draft.trim() === ''" @click="onSend()">
            {{ sending ? '发送中…' : '发送' }}
          </button>
        </div>
        <p v-if="adviceNote !== ''" class="inline-success bp-advice">{{ adviceNote }}</p>
        <p v-if="chatError !== ''" class="inline-error">{{ chatError }}</p>
      </div>
    </section>

    <!-- 右：本次范围 -->
    <section class="card bp-scope-form">
      <div class="bp-card-head">
        <h2>本次范围</h2>
        <span v-if="dirty" class="status-pill pill-warn">未保存</span>
        <span v-else class="status-pill pill-ok">已保存</span>
      </div>

      <label class="bp-field">建模目标
        <textarea v-model="form.goal" placeholder="这次要建模的业务与用途，例如储能设备运行监测与充放电计划。"></textarea>
      </label>
      <label class="bp-field">纳入范围
        <textarea v-model="form.include" placeholder="要生成的业务模块、对象范围及需要的深度。"></textarea>
      </label>
      <label class="bp-field">排除范围
        <textarea v-model="form.exclude" placeholder="明确不生成的业务或技术内容。"></textarea>
      </label>
      <label class="bp-field">必要关联与深度
        <textarea v-model="form.relations" placeholder="公共概念（园区、电表等）保留到什么程度、关系到第几层。"></textarea>
      </label>
      <label class="bp-field">覆盖说明
        <textarea v-model="form.coverage" placeholder="材料缺口、版本差异，以及同一模块取舍的解释（存在纳入与排除冲突时必须填写）。"></textarea>
      </label>

      <div class="bp-form-actions">
        <button type="button" :disabled="saving || !dirty" @click="onSaveScope()">{{ saving ? '保存中…' : '保存摘要' }}</button>
        <button type="button" class="row-link" :disabled="saving || !dirty" @click="discardEdits()">放弃未保存修改</button>
        <small class="muted">已扫描 {{ materials.length }} 份材料 · 范围修订 {{ scopeRevision }}</small>
      </div>
      <p v-if="notice !== ''" class="inline-success">{{ notice }}</p>
      <p v-if="saveError !== ''" class="inline-error">
        {{ saveError }}
        <button v-if="conflict" type="button" class="row-link" @click="reloadAll()">刷新并重新加载</button>
      </p>

      <details class="bp-details" open>
        <summary>材料依据与未决问题</summary>
        <p class="muted">{{ materialSummary }}</p>

        <div v-if="materials.length > 0">
          <p class="eyebrow">材料清单</p>
          <ul class="bp-list">
            <li v-for="m in materials" :key="m.id">
              {{ m.relPath }}
              <span class="status-pill">{{ materialKindLabel(m.kind) }}</span>
              <span class="status-pill">{{ parseStateLabel(m.parseState) }}</span>
              <span v-if="m.excluded" class="status-pill">已排除</span>
              <span v-if="coverageOf(m).factCount > 0" class="muted"> · {{ coverageOf(m).factCount }} 条事实</span>
            </li>
          </ul>
        </div>

        <div v-if="partialMaterials.length > 0 || failedMaterials.length > 0">
          <p class="eyebrow">部分失败/失败材料</p>
          <ul class="bp-list">
            <li v-for="m in partialMaterials" :key="'p-' + m.id">
              部分成功：{{ m.relPath }}（{{ parseStateLabel(m.parseState) }}，{{ coverageOf(m).failedSegments.length }} 个失败片段）
            </li>
            <li v-for="m in failedMaterials" :key="'f-' + m.id" class="bp-li-error">
              解析失败：{{ m.relPath }}<template v-if="m.error"> · {{ m.error }}</template>（可在物料页重试或排除）
            </li>
          </ul>
        </div>

        <div v-if="coverageGaps.length > 0">
          <p class="eyebrow">覆盖缺口</p>
          <ul class="bp-list">
            <li v-for="gap in coverageGaps" :key="gap.id">{{ gap.name }}：{{ gap.lines.join('；') }}</li>
          </ul>
        </div>

        <div v-if="questions.length > 0">
          <p class="eyebrow">未决问题</p>
          <ul class="bp-list">
            <li v-for="(q, i) in questions" :key="'q-' + i">
              {{ q.text }}
              <span v-if="q.blocking" class="status-pill pill-error">阻断范围确认</span>
              <span v-else class="status-pill">可留到候选评审</span>
            </li>
          </ul>
        </div>
        <p v-else class="muted">当前没有登记未决问题。</p>
        <p class="muted">材料与范围变化会使已生成结果过期；过期结果只读回看，不影响继续编辑与再次确认。</p>
      </details>
    </section>
  </div>

  <footer class="card bp-footer">
    <div class="bp-blockers">
      <p v-if="disableReasons.length === 0" class="muted">确认后开始抽象本体；非关键疑问可以留到候选评审。</p>
      <template v-else>
        <p class="inline-warning">暂不能确认范围并生成：</p>
        <ul class="bp-list">
          <li v-for="(reason, i) in disableReasons" :key="'r-' + i">{{ reason }}</li>
        </ul>
      </template>
      <p v-if="providerName !== ''" class="muted">本次生成使用模型：{{ providerName }}</p>
      <p v-if="providerError !== ''" class="inline-error">模型能力查询失败：{{ providerError }}</p>
      <p v-if="deliveredOntologyId !== ''" class="muted">该任务已交付过本体（{{ deliveredOntologyId }}）；再次生成不会覆盖已交付内容。</p>
    </div>
    <div class="bp-footer-actions">
      <button type="button" @click="emit('back')">返回物料</button>
      <button type="button" class="primary" :disabled="!canConfirm" @click="onConfirm()">
        {{ confirming ? '正在启动生成…' : '确认范围并生成 →' }}
      </button>
    </div>
    <p v-if="confirmError !== ''" class="inline-error bp-confirm-error">
      {{ confirmError }}
      <template v-if="confirmIssues.length > 0"><br />服务端校验未通过：{{ confirmIssues.join('；') }}</template>
    </p>
  </footer>
</div>
</template>

<style scoped>
.bp-scope{display:flex;flex-direction:column;gap:14px}
.bp-head{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;flex-wrap:wrap}
.bp-head h1{font-size:21px;margin:0 0 6px;display:flex;align-items:center;gap:8px}
.bp-head-actions{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.bp-banner{border:1px solid var(--warn-line);background:var(--warn-soft);color:var(--warn);border-radius:var(--r-md);padding:10px 14px;font-size:13px;margin:0}
.bp-grid{display:grid;grid-template-columns:minmax(0,1.15fr) minmax(340px,.85fr);gap:16px;align-items:start}
.bp-chat{display:flex;flex-direction:column;min-height:520px;max-height:720px;margin-bottom:0}
.bp-card-head{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:12px}
.bp-card-head h2{margin:0}
.bp-log{flex:1;overflow:auto;min-height:200px;padding-right:6px}
.bp-log-empty{margin:8px 0}
.bp-msg{border:1px solid var(--line);background:var(--paper-2);border-radius:var(--r-md);padding:11px 14px;margin:10px 0;max-width:96%}
.bp-msg strong{display:block;font-size:12px;color:var(--muted);margin-bottom:4px}
.bp-msg-body{margin:0;white-space:pre-wrap;overflow-wrap:anywhere;font-size:14px;line-height:1.7}
.bp-msg-you{margin-left:auto;background:var(--blue-soft);border-color:var(--blue-line)}
.bp-msg-system{border-style:dashed}
.bp-suggest{margin-top:10px;padding:9px 11px;border-left:3px solid var(--blue-line);background:var(--paper);border-radius:0 var(--r-sm) var(--r-sm) 0}
.bp-suggest-title{margin:0 0 7px;font-size:13px;color:var(--blue-ink);overflow-wrap:anywhere}
.bp-composer{border-top:1px solid var(--line);margin-top:12px;padding-top:12px}
.bp-input{width:100%;min-height:78px;resize:vertical}
.bp-composer-foot{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-top:10px;flex-wrap:wrap}
.bp-advice{margin-top:8px}
.bp-scope-form{margin-bottom:0}
.bp-field{display:block;margin:14px 0;color:var(--muted);font-size:13px}
.bp-field textarea{width:100%;min-height:64px;resize:vertical;margin-top:5px}
.bp-form-actions{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-top:6px}
.bp-details{margin-top:16px;border-top:1px solid var(--line);padding-top:12px}
.bp-list{margin:6px 0 10px;padding-left:20px;font-size:13px;line-height:1.7;overflow-wrap:anywhere}
.bp-list .status-pill{margin-left:6px}
.bp-li-error{color:var(--danger)}
.bp-footer{display:flex;justify-content:space-between;align-items:center;gap:16px;flex-wrap:wrap;margin-bottom:0}
.bp-blockers{flex:1;min-width:260px}
.bp-blockers p{margin:0 0 6px}
.bp-footer-actions{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.bp-confirm-error{flex-basis:100%;margin:0}
.pill-warn{background:var(--warn-soft);border-color:var(--warn-line);color:var(--warn)}
@media (max-width:1100px){
  .bp-grid{grid-template-columns:1fr}
  .bp-chat{min-height:420px;max-height:none}
  .bp-msg{max-width:100%}
}
</style>
