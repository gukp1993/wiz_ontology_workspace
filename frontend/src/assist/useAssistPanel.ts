// 辅助填写面板状态机（T3，2026-09-21）——纯逻辑组合式函数，可脱离 DOM 独立测试（tests/assist_panel.test.mjs）。
// 契约唯一来源：文档/接口文档/04-编排与LLM接口.md §5；协议镜像 ./types.ts（T0 冻结，只读引用）。
// 设计要点：
//   * 面板只做「取上下文 → 生成建议/检查/解释 → 采纳进宿主草稿」；持久化永远走表单既有保存
//     链路（form-save / commit-now），本文件绝不调用任何保存函数，也不感知表单保存状态。
//   * 请求代际 req：open / refreshContext / generate / cancel / close 都 +1；迟到响应按代际
//     丢弃（不写任何状态），与 app/saveCoordinator.ts 的 epoch 写法同源。
//   * stale：宿主调用 notifyDraftChanged()（手改字段）或草稿指纹相对取上下文时漂移后置真；
//     已有结果标记过期并禁用采纳，提示重新生成。draft 摘要是否匹配最终由后端 409 CONTEXT_STALE 裁定。
//   * adopt：只合并「勾选且 state=ready」的建议；与当前草稿等值的字段剔除；同字段后选覆盖；
//     snapshot → apply，记录撤销快照；宿主手改后撤销保护失效（canUndo=false）。
//   * close 只收起 UI 并作废在途请求，contextToken/intent/结果保留；reopen 恢复展示。
//     宿主切换编辑目标必须重新 open()（open 会整卡重置并重取上下文）。
import { ref, computed, type Ref, type ComputedRef } from 'vue'
import { postJson } from '../app/http'
import type {
  AssistAnswer,
  AssistContextInfo,
  AssistContextResponse,
  AssistGenerateResponse,
  AssistIssue,
  AssistMode,
  AssistQuestion,
  AssistSpace,
  AssistSuggestion,
  AssistTargetKind,
} from './types'

// ── 宿主适配接口：由 T5–T10 的各表单组件提供（面板不直接读写表单内部状态）────────
export interface AssistHostBinding {
  space: AssistSpace
  projectId?: string
  targetKind: AssistTargetKind
  targetId: string
  /** 未取到上下文时的兜底标题（取到后用 context.title） */
  contextTitle: string
  /** 白名单形态快照（各表单适配；每次调用返回当前值，面板在取上下文/生成/采纳时重新取） */
  draft: () => Record<string, unknown>
  /** 把一组 proposed 值合并进本地草稿（键类型适配由表单负责） */
  apply: (values: Record<string, unknown>) => void
  /** 采纳前快照（用于撤销） */
  snapshot: () => unknown
  /** 恢复快照 */
  restore: (snap: unknown) => void
}

// ── API 注入点：默认实现走 http.ts（T4 落地后端）；测试注入桩 ────────────────────
export interface AssistApi {
  context(body: {
    space: AssistSpace
    projectId?: string
    targetKind: AssistTargetKind
    targetId: string
    purpose: AssistMode
    draft: Record<string, unknown>
  }): Promise<AssistContextResponse>
  generate(body: {
    requestId: string
    contextToken: string
    mode: AssistMode
    draft: Record<string, unknown>
    intent?: string
    answers?: Record<string, AssistAnswer>
  }): Promise<AssistGenerateResponse>
}

/** 默认 API 工厂：POST /api/assist-context、/api/assist-generate；错误经 SaveRequestError 透传。 */
export function defaultAssistApi(): AssistApi {
  return {
    context: (body) => postJson('/api/assist-context', body) as Promise<AssistContextResponse>,
    generate: (body) => postJson('/api/assist-generate', body) as Promise<AssistGenerateResponse>,
  }
}

export type AssistPanelStatus = 'idle' | 'loading-context' | 'ready' | 'generating' | 'done' | 'empty' | 'error' | 'no-model'

export interface AssistPanelError { code: string; message: string }

export interface AssistPanel {
  status: Ref<AssistPanelStatus>
  contextInfo: Ref<AssistContextInfo | null>
  result: Ref<AssistGenerateResponse | null>
  error: Ref<AssistPanelError | null>
  tab: Ref<AssistMode>
  intent: Ref<string>
  answers: Ref<Record<string, AssistAnswer>>
  checked: Ref<Record<string, boolean>>
  stale: Ref<boolean>
  canUndo: Ref<boolean>
  /** 刚采纳过（展示「已填入表单，尚未保存」提示条）；手改字段后清除 */
  justAdopted: Ref<boolean>
  /** 瞬时提示（已取消等）；下一次 open/generate/refreshContext/clearNotice 清除 */
  notice: Ref<string>
  /** close() 后为真：只收起 UI，不丢 contextToken 与结果；reopen 恢复 */
  closed: Ref<boolean>
  contextToken: Ref<string>
  questions: ComputedRef<AssistQuestion[]>
  suggestions: ComputedRef<AssistSuggestion[]>
  issues: ComputedRef<AssistIssue[]>
  explanation: ComputedRef<AssistGenerateResponse['explanation']>
  hasContext: ComputedRef<boolean>
  /** 勾选且 state=ready 的建议数 */
  selectedCount: ComputedRef<number>
  /** 宿主切换目标/初次挂载：整卡重置并重取上下文（intent 一并清空） */
  open(binding: AssistHostBinding): Promise<void>
  /** 重走 open（保留 intent，清掉旧结果/回答）；CONTEXT_STALE 后的恢复入口 */
  refreshContext(): Promise<void>
  /** 按当前页签模式生成；重新生成会使在途旧请求失效；需要先取到 contextToken */
  generate(): Promise<void>
  /** 采纳勾选且 ready 的建议：等值剔除 → snapshot → apply → 记录撤销；绝不触发表单保存 */
  adopt(): boolean
  /** 撤销上次采纳：restore 快照；一次性（撤销后 canUndo=false） */
  undo(): boolean
  /** 宿主手改字段后调用：结果过期、撤销保护失效（采纳后的手改不能被 undo 无声覆盖） */
  notifyDraftChanged(): void
  /** 草稿指纹相对取上下文时是否漂移；漂移则置 stale（返回最新 stale 值） */
  syncStaleness(): boolean
  /** 作废在途请求并回到前态；提示已取消（不声称上游已停止） */
  cancel(): void
  /** 收起面板：清瞬时 UI 状态与在途请求，contextToken/intent/结果保留 */
  close(): void
  /** 重新展开：目标与草稿指纹一致时复用既有上下文与结果 */
  reopen(): void
  /** 勾选控制：pending/blocked 拒绝勾选 */
  setChecked(suggestionId: string, on: boolean): void
  /** 记录/清除一条回答（value 与 unsure 都为空即视为未回答） */
  answerQuestion(questionId: string, answer: AssistAnswer | undefined): void
  clearNotice(): void
}

// ── 内部工具 ─────────────────────────────────────────────────────────────────
const cloneJson = <T,>(v: T): T => JSON.parse(JSON.stringify(v))

function makeRequestId(): string {
  const c = (globalThis as { crypto?: { randomUUID?: () => string } }).crypto
  if (c && typeof c.randomUUID === 'function') return c.randomUUID()
  return 'req-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 10)
}

/** undefined 视作 null 后做深度等值比较（对象键序无关）——等值建议不产生写操作。 */
function deepEqual(a: unknown, b: unknown): boolean {
  const x = a === undefined ? null : a
  const y = b === undefined ? null : b
  if (x === y) return true
  if (x === null || y === null || typeof x !== 'object' || typeof y !== 'object') {
    return JSON.stringify(x) === JSON.stringify(y)
  }
  const ka = Object.keys(x).sort()
  const kb = Object.keys(y).sort()
  if (ka.length !== kb.length) return false
  if (ka.some((k, i) => k !== kb[i])) return false
  const rx = x as Record<string, unknown>
  const ry = y as Record<string, unknown>
  return ka.every(k => deepEqual(rx[k], ry[k]))
}

/** 兼容 SaveRequestError（status/data.code/message）与测试桩抛出的普通对象。 */
function describeError(err: unknown): { status: number; code: string; message: string } {
  const e = (err ?? {}) as { status?: unknown; message?: unknown; code?: unknown; data?: { code?: unknown; error?: unknown } }
  const status = typeof e.status === 'number' ? e.status : 0
  const code = String(e.data?.code ?? e.code ?? '')
  const message = String(e.message ?? e.data?.error ?? '请求失败，请稍后重试')
  return { status, code, message }
}

/** answers 只传有内容的条目：{qid:{value|unsure}}（结构化传输，T0 冻结）。 */
function cleanAnswers(source: Record<string, AssistAnswer>): Record<string, AssistAnswer> | undefined {
  const out: Record<string, AssistAnswer> = {}
  for (const [qid, a] of Object.entries(source || {})) {
    const item: AssistAnswer = {}
    if (typeof a?.value === 'string' && a.value !== '') item.value = a.value
    if (a?.unsure) item.unsure = true
    if (item.value !== undefined || item.unsure) out[qid] = item
  }
  return Object.keys(out).length ? out : undefined
}

// ── 状态机 ───────────────────────────────────────────────────────────────────
export function useAssistPanel(api: AssistApi = defaultAssistApi()): AssistPanel {
  const status = ref<AssistPanelStatus>('idle')
  const contextInfo = ref<AssistContextInfo | null>(null)
  const result = ref<AssistGenerateResponse | null>(null)
  const error = ref<AssistPanelError | null>(null)
  const tab = ref<AssistMode>('fill')
  const intent = ref('')
  const answers = ref<Record<string, AssistAnswer>>({})
  const checked = ref<Record<string, boolean>>({})
  const stale = ref(false)
  const canUndo = ref(false)
  const justAdopted = ref(false)
  const notice = ref('')
  const closed = ref(false)
  const contextToken = ref('')

  const questions = computed(() => result.value?.questions ?? [])
  const suggestions = computed(() => result.value?.suggestions ?? [])
  const issues = computed(() => result.value?.issues ?? [])
  const explanation = computed(() => result.value?.explanation ?? null)
  const hasContext = computed(() => contextToken.value !== '')
  const selectedCount = computed(() => {
    let n = 0
    for (const s of suggestions.value) if (s.state === 'ready' && checked.value[s.id]) n++
    return n
  })

  let host: AssistHostBinding | null = null
  let req = 0                    // 请求代际：open/refreshContext/generate/cancel/close 递增
  let contextDraftJson = 'null'  // 取上下文（或最近采纳/撤销）时的草稿快照，用于指纹漂移检测
  let undoSnapshot: unknown = null
  let preGenerateStatus: AssistPanelStatus = 'ready' // generating 被打断（cancel/close）后的回退态
  let closedFromStatus: AssistPanelStatus = 'idle'   // close 时的展开展示态，reopen 恢复

  /** 草稿指纹漂移 → stale（与 notifyDraftChanged 同效；采纳/撤销会重对齐快照，不自伤）。 */
  function syncStaleness(): boolean {
    if (!host || !contextToken.value) return stale.value
    if (JSON.stringify(host.draft()) !== contextDraftJson) {
      stale.value = true
      if (canUndo.value) { canUndo.value = false; undoSnapshot = null } // 手改后的采纳不能被 undo 无声回滚
    }
    return stale.value
  }

  function resetCard() {
    result.value = null
    error.value = null
    checked.value = {}
    answers.value = {}
    stale.value = false
    canUndo.value = false
    justAdopted.value = false
    undoSnapshot = null
    contextToken.value = ''
    contextInfo.value = null
    intent.value = ''
    tab.value = 'fill'
    notice.value = ''
  }

  async function loadContext(): Promise<void> {
    if (!host) return
    const my = ++req
    status.value = 'loading-context'
    const draftSnapshot = cloneJson(host.draft())
    contextDraftJson = JSON.stringify(draftSnapshot)
    try {
      const resp = await api.context({
        space: host.space,
        projectId: host.projectId,
        targetKind: host.targetKind,
        targetId: host.targetId,
        purpose: tab.value,
        draft: draftSnapshot,
      })
      if (my !== req) return // 期间目标切换/关闭/重取：丢弃
      contextInfo.value = resp.context
      contextToken.value = resp.contextToken
      error.value = null
      stale.value = false
      status.value = resp.context.modelReady ? 'ready' : 'no-model' // no-model：保留输入，引导配置模型
    } catch (err) {
      if (my !== req) return
      const e = describeError(err)
      error.value = { code: e.code, message: e.message }
      status.value = 'error'
    }
  }

  /** 宿主切换目标/初次挂载：整卡重置并重取上下文。 */
  async function open(binding: AssistHostBinding): Promise<void> {
    req++ // 作废在途请求
    host = binding
    closed.value = false
    resetCard()
    await loadContext()
  }

  /** 重走 open：保留 intent（用户输入不丢），旧结果/回答作废。 */
  async function refreshContext(): Promise<void> {
    if (!host || closed.value) return
    req++
    result.value = null
    checked.value = {}
    answers.value = {}
    justAdopted.value = false
    canUndo.value = false
    undoSnapshot = null
    notice.value = ''
    await loadContext()
  }

  /** 按当前页签模式生成；重复调用使在途旧请求失效（最后发出者胜出）。 */
  async function generate(): Promise<void> {
    if (closed.value || !host || !contextToken.value) return
    if (status.value !== 'generating') preGenerateStatus = status.value
    syncStaleness() // 漂移只置标志；摘要是否匹配由后端 409 CONTEXT_STALE 裁定
    const my = ++req
    notice.value = ''
    error.value = null
    status.value = 'generating'
    const text = intent.value.trim()
    const ans = cleanAnswers(answers.value)
    const body: Parameters<AssistApi['generate']>[0] = {
      requestId: makeRequestId(),
      contextToken: contextToken.value,
      mode: tab.value,
      draft: cloneJson(host.draft()),
    }
    if (text) body.intent = text
    if (ans) body.answers = ans
    try {
      const resp = await api.generate(body)
      if (my !== req) return // 迟到响应：按代际丢弃
      result.value = resp
      checked.value = {}
      for (const s of resp.suggestions) checked.value[s.id] = s.state === 'ready' // 默认 ready 全勾选
      justAdopted.value = false
      // 结果对应「发出时刻」的草稿；期间草稿若又变了按指纹置过期
      stale.value = host ? JSON.stringify(host.draft()) !== contextDraftJson : stale.value
      status.value = resp.status === 'empty' ? 'empty' : 'done'
    } catch (err) {
      if (my !== req) return
      const e = describeError(err)
      if (e.code === 'CONTEXT_STALE' || (e.status === 409 && !e.code)) {
        error.value = { code: 'CONTEXT_STALE', message: e.code === 'CONTEXT_STALE' ? '上下文已变化，请重新获取上下文后重试。' : e.message }
      } else {
        error.value = { code: e.code, message: e.message }
      }
      // 未配置模型：进 no-model（保留输入 + 引导），其余进 error（可重试/可重取）
      status.value = e.code === 'MODEL_NOT_CONFIGURED' ? 'no-model' : 'error'
    }
  }

  /** 采纳勾选且 ready 的建议；等值字段剔除；同字段后选覆盖；绝不触发表单保存。 */
  function adopt(): boolean {
    if (!host || closed.value || stale.value) return false
    if (status.value !== 'done' || !result.value) return false
    if (syncStaleness()) return false // 指纹已漂移：结果过期，禁用采纳
    const current = host.draft()
    const merged: Record<string, unknown> = {}
    const adoptedIds: string[] = []
    for (const s of result.value.suggestions) {
      if (s.state !== 'ready' || !checked.value[s.id]) continue
      adoptedIds.push(s.id)
      for (const [k, v] of Object.entries(s.proposed || {})) merged[k] = v // 同字段：后选覆盖
    }
    const values: Record<string, unknown> = {}
    let changed = false
    for (const [k, v] of Object.entries(merged)) {
      if (!deepEqual(current ? current[k] : undefined, v)) { values[k] = v; changed = true } // 等值剔除
    }
    if (!changed) return false // 全部等值：无操作（不产生撤销快照、不改 canUndo）
    const snap = host.snapshot() // 先存快照再落草稿
    host.apply(values)
    undoSnapshot = snap
    canUndo.value = true
    justAdopted.value = true
    contextDraftJson = JSON.stringify(host.draft()) // 采纳本身不算手改：不触发 stale
    return true
  }

  /** 撤销上次采纳：恢复快照；一次性。 */
  function undo(): boolean {
    if (!host || !canUndo.value || undoSnapshot === null) return false
    host.restore(cloneJson(undoSnapshot))
    undoSnapshot = null
    canUndo.value = false
    justAdopted.value = false
    contextDraftJson = JSON.stringify(host.draft())
    return true
  }

  /** 宿主手改字段：结果过期 + 撤销保护失效 + 清「已填入」提示。 */
  function notifyDraftChanged(): void {
    stale.value = true
    if (canUndo.value) { canUndo.value = false; undoSnapshot = null }
    justAdopted.value = false
  }

  function cancel(): void {
    req++ // 在途响应一律按代际丢弃；不声称上游已停止
    if (status.value === 'generating' || status.value === 'loading-context') {
      status.value = status.value === 'generating' ? preGenerateStatus : (contextToken.value ? 'ready' : 'idle')
      notice.value = status.value === 'generating'
        ? '已取消本次生成；已发出的服务端调用可能仍在进行，其结果将被忽略。'
        : '已取消获取上下文。'
    }
  }

  function close(): void {
    req++
    if (status.value === 'generating') status.value = preGenerateStatus
    else if (status.value === 'loading-context') status.value = contextToken.value ? 'ready' : 'idle'
    closedFromStatus = status.value
    notice.value = ''
    closed.value = true
  }

  function reopen(): void {
    if (!closed.value) return
    closed.value = false
    notice.value = ''
    status.value = closedFromStatus
  }

  /** 勾选控制：pending/blocked 拒绝勾选（面板与状态机双保险）。 */
  function setChecked(suggestionId: string, on: boolean): void {
    const s = suggestions.value.find(x => x.id === suggestionId)
    if (!s || s.state !== 'ready') return
    checked.value[suggestionId] = on
  }

  /** 记录/清除一条回答（answerQuestion(qid, undefined) 即清除）。 */
  function answerQuestion(questionId: string, answer: AssistAnswer | undefined): void {
    if (!answer || (answer.value === undefined && !answer.unsure)) delete answers.value[questionId]
    else answers.value[questionId] = { value: answer.value, unsure: answer.unsure }
  }

  function clearNotice(): void { notice.value = '' }

  return {
    status, contextInfo, result, error, tab, intent, answers, checked,
    stale, canUndo, justAdopted, notice, closed, contextToken,
    questions, suggestions, issues, explanation, hasContext, selectedCount,
    open, refreshContext, generate, adopt, undo, notifyDraftChanged, syncStaleness,
    cancel, close, reopen, setChecked, answerQuestion, clearNotice,
  }
}
