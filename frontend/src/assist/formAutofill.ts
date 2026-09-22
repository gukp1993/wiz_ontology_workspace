// 整表自动填写：通用填写引擎 / 宿主状态机（T3，2026-09-22 改版）。
// 契约唯一来源：文档/接口文档/04-编排与LLM接口.md §6（autofill/1 冻结协议）；
// 交互基准：文档/需求/20260922_整表自动填写交互/交互原型_v1.html 与需求说明 §4/§5.3。
// 设计要点：
//   * 引擎只认识契约、当前标准化 draft、上下文及用户要求，不引用 Vue 组件名/DOM/业务对象（§5.3）。
//   * 状态：idle → open(抽屉) → generating → applying → done(收起+状态条) | questions(留在抽屉)
//     | empty | error | no-model。
//   * 请求代际 gen：open/close/expand/cancel/notifyDraftChanged/generate 都递增；迟到响应按代际
//     丢弃（零写入），与 app/saveCoordinator.ts 的 epoch 写法同源。
//   * 会话跟踪：sessionId/roundId 透传；续轮握手按 04 §6.2——应用操作 → draft 变 → 必须重新
//     assist-context 取新 token → 携带新 token + sessionId + answers 再 assist-generate。实现上
//     每次 generate 前比对「当前草稿 vs 取上下文时草稿」，漂移即先重取（首轮不漂移则不重复取）。
//   * 整轮撤销：撤销单元 = 一次会话（首轮 + 全部续轮）；单元首次写入前快照草稿，undoRound 恢复；
//     期间宿主手改（notifyDraftChanged 通知）→ 禁用整轮撤销且说明，不覆盖用户改动（§4.5）。
//   * applyOperations：按契约点路径写 draft 副本——普通字段直接 set；组/复杂字段经 host 注入的
//     codec 回调（本期 identity 直写兜底，真实 codec 由 G2/G3 适配器注册）；写回走 applyDraft
//     （新通道，就地原子替换）或退回旧 apply(顶层变更值)。
//   * 回填红线：绝不调用宿主的 form-save/commit-now/touch 保存通道——本接口根本不提供这些方法，
//     引擎只经 draft()/apply|applyDraft/snapshot()/restore() 触达宿主。
import { computed, ref, type ComputedRef, type Ref } from 'vue'
import type {
  AssistContextInfo,
  AssistContextRequest,
  AssistContextResponse,
  AssistGenerateRequest,
  AssistGenerateResponse,
  AssistMode,
  AssistSpace,
  AssistTargetKind,
  AutofillAnswerInput,
  AutofillFillResponse,
  AutofillOperation,
  AutofillQuestion,
  AutofillUnresolved,
} from './types'

// ── 宿主适配接口 ─────────────────────────────────────────────────────────────
// AssistHostBinding：与旧版保持同形（G2/G3 适配器与本文件之外的宿主按此构造）；
// AutofillHostBinding：新版可选扩展（codec 注册、整稿原子写回、契约指纹比对）。
export interface AssistHostBinding {
  space: AssistSpace
  projectId?: string
  /** 本体区必填：当前编辑的本体工作区 id（后端按它构建上下文与指纹；缺省回落默认工作区） */
  ontologyId?: string
  targetKind: AssistTargetKind
  targetId: string
  /** 未取到上下文时的兜底标题（取到后用 context.title） */
  contextTitle: string
  /** 白名单形态快照（每次调用返回当前值；引擎在取上下文/生成/写回前重新取） */
  draft: () => Record<string, unknown>
  /** 旧版回填通道：把一组顶层变更值合并进本地草稿（键类型适配由表单负责） */
  apply: (values: Record<string, unknown>) => void
  /** 快照（用于整轮撤销） */
  snapshot: () => unknown
  /** 恢复快照 */
  restore: (snap: unknown) => void
}

/**
 * 复杂字段 codec：由宿主 binding 注入（G2/G3 适配器注册），key 为契约点路径。
 * set 传入模型 typed value；row.append 传入 {localId, fields}；row.update/remove 传入整条操作。
 * 本期引擎内置 identity 兜底（直接按点路径写），真实 JSON-LD/Redis/参数行 codec 归各适配器。
 */
export type AutofillFieldCodec = (draft: Record<string, unknown>, field: string, value: unknown) => void

export interface AutofillHostBinding extends AssistHostBinding {
  /** 复杂字段 codec 注册表；未注册字段走 identity 兜底（点路径直写） */
  codecs?: Record<string, AutofillFieldCodec>
  /** 新版回填通道：引擎在草稿副本上写好全部操作后整稿落回（宿主就地替换、保引用）；绝不触发保存 */
  applyDraft?: (next: Record<string, unknown>) => void
  /** 本地表单契约生成物（T1 formContracts.gen.ts）指纹；返回 null 跳过比对 */
  contractInfo?: () => { schemaVersion: number; schemaDigest: string } | null
}

/** API 注入点（useAssistPanel 保持导出名；默认实现见 defaultAssistApi） */
export type AssistApi = {
  context(body: AssistContextRequest): Promise<AssistContextResponse>
  generate(body: AssistGenerateRequest): Promise<AssistGenerateResponse | AutofillFillResponse>
}

// ── 状态与摘要类型 ───────────────────────────────────────────────────────────
export type AutofillStatus =
  | 'idle'       // 初始：未打开任何目标
  | 'open'       // 抽屉打开、上下文就绪，可输入/生成
  | 'generating' // 生成中（禁重复提交，可取消）
  | 'applying'   // 应用操作中（同步瞬时态）
  | 'done'       // 一次回填完成（抽屉收起，状态条由宿主渲染）
  | 'questions'  // 有待补问题/未填写项，留在抽屉
  | 'empty'      // 有效响应但无变更
  | 'error'      // 错误（输入与已记录答案保留，可重试）
  | 'no-model'   // 当前账号未配置默认模型（保留输入，引导设置）

export interface AutofillError { code: string; message: string }

/** 逐字段旧值→新值（查看修改用） */
export interface RoundFieldChange {
  field: string
  label: string
  oldText: string
  newText: string
}

/** 一轮（会话）摘要：已填 N 项 / 待补 M 项 / 逐字段变化 */
export interface RoundSummary {
  roundActive: boolean
  appliedCount: number
  questionCount: number
  unresolvedCount: number
  pendingCount: number
  changes: RoundFieldChange[]
  undone: boolean
  canUndo: boolean
}

/** empty 态面板文案（无建议 empty 文案，04 §6.6 / 需求 §4.3） */
export const AUTOFILL_EMPTY_TEXT = '内容已一致，无需修改'

/** 已记录回答（answers 键值中的条目形态；组装 autofill/1 请求时并上 questionId） */
export interface AutofillRecordedAnswer { value?: string; unsure?: boolean }

/** fill 响应回传的契约指纹（§6.1：前端与本地生成物比对，不一致按 CONTEXT_STALE 语义处理） */
export interface AutofillSchemaInfo {
  formId: string
  schemaVersion: number
  schemaDigest: string
}

// ── 纯函数工具（可独立测试）──────────────────────────────────────────────────
export function cloneJson<T>(v: T): T {
  return JSON.parse(JSON.stringify(v)) as T
}

export function makeRequestId(): string {
  const c = (globalThis as { crypto?: { randomUUID?: () => string } }).crypto
  if (c && typeof c.randomUUID === 'function') return c.randomUUID()
  return 'req-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 10)
}

/** undefined 视作 null 后深度等值（键序无关）——等值操作不产生变更。 */
export function deepEqual(a: unknown, b: unknown): boolean {
  const x = a === undefined ? null : a
  const y = b === undefined ? null : b
  if (x === y) return true
  if (x === null || y === null || typeof x !== 'object' || typeof y !== 'object') {
    return JSON.stringify(x) === JSON.stringify(y)
  }
  const ka = Object.keys(x).sort()
  const kb = Object.keys(y).sort()
  if (ka.length !== kb.length || ka.some((k, i) => k !== kb[i])) return false
  const rx = x as Record<string, unknown>
  const ry = y as Record<string, unknown>
  return ka.every(k => deepEqual(rx[k], ry[k]))
}

/** 点路径读取（契约内路径；中间节点缺失返回 undefined） */
export function getByPath(obj: Record<string, unknown>, path: string): unknown {
  let cur: unknown = obj
  for (const k of path.split('.')) {
    if (cur === null || cur === undefined || typeof cur !== 'object') return undefined
    cur = (cur as Record<string, unknown>)[k]
  }
  return cur
}

/** 点路径写入（中间节点不存在则创建普通对象） */
export function setByPath(obj: Record<string, unknown>, path: string, value: unknown): void {
  const keys = path.split('.')
  let cur: Record<string, unknown> = obj
  for (let i = 0; i < keys.length - 1; i++) {
    const k = keys[i]
    const nxt = cur[k]
    if (nxt === null || nxt === undefined || typeof nxt !== 'object' || Array.isArray(nxt)) {
      cur[k] = {}
    }
    cur = cur[k] as Record<string, unknown>
  }
  cur[keys[keys.length - 1]] = value
}

/** 列表行的本地稳定 id 匹配（rowId/localId/id 任一命中；精确形态由 codec/适配器负责） */
function rowIdOf(row: unknown): string {
  if (row === null || typeof row !== 'object') return ''
  const r = row as Record<string, unknown>
  for (const k of ['rowId', 'localId', 'id']) {
    const v = r[k]
    if (typeof v === 'string') return v
  }
  return ''
}

export interface AppliedOpRecord {
  field: string
  op: string
  oldValue: unknown
  newValue: unknown
  changed: boolean
}

export interface AppliedOpFailure { field: string; op: string; reason: string }

export interface ApplyOperationsResult {
  records: AppliedOpRecord[]
  failures: AppliedOpFailure[]
}

/**
 * 按契约点路径把 operations 写进 draft 副本（调用方负责传副本，实现原子写回）。
 * 普通字段 identity 直写；注册了 codec 的字段经 codec 回调（组/复杂字段、列表行精确形态）。
 * 单条失败不中断其余独立操作（失败由后端转 unresolved，这里兜底记录 reason）。
 */
export function applyOperations(
  draft: Record<string, unknown>,
  operations: AutofillOperation[],
  codecs: Record<string, AutofillFieldCodec> = {},
): ApplyOperationsResult {
  const records: AppliedOpRecord[] = []
  const failures: AppliedOpFailure[] = []
  for (const op of operations) {
    const before = getByPath(draft, op.field)
    try {
      switch (op.op) {
        case 'set': {
          const codec = codecs[op.field]
          if (codec) codec(draft, op.field, op.value)
          else setByPath(draft, op.field, op.value)
          break
        }
        case 'clear': {
          // clear 仅服务端对 nullable+ai.clearable 且用户明确要求时下发；前端照写 null
          setByPath(draft, op.field, null)
          break
        }
        case 'row.append': {
          const codec = codecs[op.field]
          if (codec) { codec(draft, op.field, op.row); break }
          const list = getByPath(draft, op.field)
          if (list !== undefined && !Array.isArray(list)) throw new Error('目标不是列表，无法追加行')
          const arr: unknown[] = Array.isArray(list) ? list : []
          if (!Array.isArray(list)) setByPath(draft, op.field, arr)
          arr.push({ rowId: op.row.localId, ...cloneJson(op.row.fields) })
          break
        }
        case 'row.update': {
          const codec = codecs[op.field]
          if (codec) { codec(draft, op.field, op); break }
          const list = getByPath(draft, op.field)
          const row = Array.isArray(list) ? list.find(r => rowIdOf(r) === op.rowId) : null
          if (row === null || typeof row !== 'object') throw new Error(`未找到行 ${op.rowId}`)
          Object.assign(row as Record<string, unknown>, cloneJson(op.fields))
          break
        }
        case 'row.remove': {
          const codec = codecs[op.field]
          if (codec) { codec(draft, op.field, op); break }
          const list = getByPath(draft, op.field)
          if (!Array.isArray(list)) throw new Error('目标不是列表，无法移除行')
          const idx = list.findIndex(r => rowIdOf(r) === op.rowId)
          if (idx < 0) throw new Error(`未找到行 ${op.rowId}`)
          list.splice(idx, 1)
          break
        }
        default: throw new Error('未知操作类型')
      }
    } catch (err) {
      failures.push({ field: op.field, op: op.op, reason: err instanceof Error ? err.message : String(err) })
      continue
    }
    const after = getByPath(draft, op.field)
    records.push({ field: op.field, op: op.op, oldValue: before, newValue: after, changed: !deepEqual(before, after) })
  }
  return { records, failures }
}

/** 顶层实际发生变化的键（N = 实际改变的表单字段/顶层复合组数，§4.3） */
export function topLevelChanges(before: Record<string, unknown>, after: Record<string, unknown>): string[] {
  const keys = new Set([...Object.keys(before), ...Object.keys(after)])
  return [...keys].filter(k => !deepEqual(before[k], after[k]))
}

/** 兼容 SaveRequestError（status/data.code/message）与测试桩抛出的普通对象。 */
export function describeError(err: unknown): AutofillError {
  const e = (err ?? {}) as { status?: unknown; message?: unknown; code?: unknown; data?: { code?: unknown; error?: unknown } }
  const code = String(e.data?.code ?? e.code ?? '')
  const message = String(e.message ?? e.data?.error ?? '请求失败，请稍后重试')
  return { code, message }
}

function isFillResponse(resp: AssistGenerateResponse | AutofillFillResponse): resp is AutofillFillResponse {
  return !!resp && typeof resp === 'object' && (resp as { protocol?: unknown }).protocol === 'autofill/1'
}

/** 展示格式化（逐字段旧值→新值用；对象 JSON 化，其余 String） */
export function fmtValue(v: unknown): string {
  if (v === undefined || v === null || v === '') return '（空）'
  if (typeof v === 'string') return v
  if (typeof v === 'object') return JSON.stringify(v)
  return String(v)
}

// ── 引擎 ────────────────────────────────────────────────────────────────────
export interface AutofillEngine {
  status: Ref<AutofillStatus>
  /** 抽屉收起（done 后自动收起；close() 收起；expand() 重新展开） */
  collapsed: Ref<boolean>
  loadingContext: Ref<boolean>
  contextInfo: Ref<AssistContextInfo | null>
  contextToken: Ref<string>
  /** 用户输入的当前目标（同目标内存保留；切目标清空；刷新丢弃，不持久保存） */
  intent: Ref<string>
  error: Ref<AutofillError | null>
  /** 瞬时提示（已取消/已撤销等）；面板内渲染，表单状态条由宿主按 statusBarText 渲染 */
  notice: Ref<string>
  /** 当前待补问题（最新一轮响应） */
  questions: Ref<AutofillQuestion[]>
  /** 已记录回答（questionId → 条目；questionId 即键，组装请求时才并成 AutofillAnswerInput） */
  answers: Ref<Record<string, AutofillRecordedAnswer>>
  /** 最近一轮响应的未填写项及原因 */
  unresolved: Ref<AutofillUnresolved[]>
  /** 服务端会话/轮次标识（透传） */
  sessionId: Ref<string>
  roundId: Ref<string>
  /** 最近一次 fill 响应的契约指纹（前端与本地生成物比对；T1 生成物落地前仅记录） */
  lastSchema: Ref<AutofillSchemaInfo | null>
  /** 整轮撤销可用（期间手改即失效） */
  canUndo: Ref<boolean>
  /** 整轮撤销不可用时的原因说明（手改 → 明确说明，§4.5） */
  undoHint: Ref<string>
  roundSummary: ComputedRef<RoundSummary>
  /** 给宿主状态条的文案：已填 N 项，尚未保存／另有 M 项待补充；空串 = 不显示 */
  statusBarText: ComputedRef<string>
  // ── 动作 ──
  open(binding: AutofillHostBinding, opts?: { reveal?: boolean }): Promise<void>
  /** 关闭抽屉：作废在途请求（代际失效），不撤销已回填草稿，不清会话/输入 */
  close(): void
  /** 重新展开：作废在途请求（代际失效） */
  expand(): void
  /** 生成中取消：作废在途请求并回到前态；不承诺上游停止 */
  cancel(): void
  /** 自动填写（fill, protocol:2）；草稿漂移时先重取上下文（04 §6.2 握手） */
  generate(): Promise<void>
  /** 记录问题回答；全部问题已答后自动发起续轮 */
  answer(questionId: string, value?: string, unsure?: boolean): void
  /** 显式提交已记录答案并续轮（幂等：生成中拒绝重复） */
  submitAnswers(): Promise<void>
  /** 整轮撤销：恢复本轮开始前草稿；一次性；期间手改则拒绝 */
  undoRound(): boolean
  /** 宿主手改通知：作废在途请求 + 禁用整轮撤销 */
  notifyDraftChanged(): void
  /** 重取上下文（打开失败/CONTEXT_STALE 恢复入口）；保留输入 */
  refreshContext(): Promise<void>
  // ── check/explain 次要帮助（只读，不提供写入）──
  helpMode: Ref<AssistMode>
  helpStatus: Ref<'idle' | 'running' | 'done' | 'error'>
  helpResult: Ref<AssistGenerateResponse | null>
  helpError: Ref<AutofillError | null>
  runHelp(mode: 'check' | 'explain'): Promise<void>
  clearHelp(): void
}

export function createAutofillEngine(api: AssistApi): AutofillEngine {
  const status = ref<AutofillStatus>('idle')
  const collapsed = ref(true)
  const loadingContext = ref(false)
  const contextInfo = ref<AssistContextInfo | null>(null)
  const contextToken = ref('')
  const intent = ref('')
  const error = ref<AutofillError | null>(null)
  const notice = ref('')
  const questions = ref<AutofillQuestion[]>([])
  const answers = ref<Record<string, AutofillRecordedAnswer>>({})
  const unresolved = ref<AutofillUnresolved[]>([])
  const sessionId = ref('')
  const roundId = ref('')
  const lastSchema = ref<AutofillSchemaInfo | null>(null)
  const canUndo = ref(false)
  const undoHint = ref('')
  const appliedCount = ref(0)
  const fieldChanges = ref<RoundFieldChange[]>([])
  const roundUndone = ref(false)
  const roundActive = ref(false)
  const helpMode = ref<AssistMode>('check')
  const helpStatus = ref<'idle' | 'running' | 'done' | 'error'>('idle')
  const helpResult = ref<AssistGenerateResponse | null>(null)
  const helpError = ref<AutofillError | null>(null)

  const roundSummary = computed<RoundSummary>(() => ({
    roundActive: roundActive.value,
    appliedCount: appliedCount.value,
    questionCount: questions.value.length,
    unresolvedCount: unresolved.value.length,
    pendingCount: questions.value.length + unresolved.value.length,
    changes: fieldChanges.value,
    undone: roundUndone.value,
    canUndo: canUndo.value,
  }))

  const statusBarText = computed(() => {
    if (!roundActive.value || roundUndone.value || appliedCount.value === 0) return ''
    const pending = questions.value.length + unresolved.value.length
    const base = `已填写 ${appliedCount.value} 项，尚未保存`
    return pending > 0 ? `${base}；另有 ${pending} 项待补充` : base
  })

  let host: AutofillHostBinding | null = null
  let gen = 0                    // 请求代际：任何失效事件 +1，迟到响应按代际丢弃
  let contextDraftJson = 'null'  // 最近一次取上下文时的草稿快照（漂移 → 生成前先重取）
  let preGenStatus: AutofillStatus = 'open'
  let roundSnapshot: unknown = null  // 撤销单元（一次会话：首轮+全部续轮）起点快照
  let awaitingAnswers = false    // 上一轮响应带回了待补问题 → 下一轮是续轮

  function sameTargetAs(a: AssistHostBinding, b: AssistHostBinding): boolean {
    return a.space === b.space && a.targetKind === b.targetKind && a.targetId === b.targetId
      && (a.projectId ?? '') === (b.projectId ?? '') && (a.ontologyId ?? '') === (b.ontologyId ?? '')
  }

  function resetRoundUnit(): void {
    roundActive.value = false
    roundSnapshot = null
    sessionId.value = ''
    roundId.value = ''
    awaitingAnswers = false
    canUndo.value = false
    undoHint.value = ''
    appliedCount.value = 0
    fieldChanges.value = []
    roundUndone.value = false
    questions.value = []
    answers.value = {}
    unresolved.value = []
  }

  /** 草稿相对最近取上下文时是否漂移（回填/手改/撤销都会漂移） */
  function draftDrifted(): boolean {
    if (!host) return false
    return JSON.stringify(host.draft()) !== contextDraftJson
  }

  /** 04 §6.2 握手：草稿漂移或无令牌时先 assist-context；返回可用令牌（代际失效返回 ''） */
  async function ensureContext(my: number): Promise<string> {
    if (host && contextToken.value && !draftDrifted()) return contextToken.value
    if (!host) return ''
    const draftSnapshot = cloneJson(host.draft())
    const resp = await api.context({
      space: host.space,
      projectId: host.projectId,
      ontologyId: host.ontologyId,
      targetKind: host.targetKind,
      targetId: host.targetId,
      purpose: 'fill',
      draft: draftSnapshot,
    })
    if (my !== gen) return ''
    contextToken.value = resp.contextToken
    contextInfo.value = resp.context
    contextDraftJson = JSON.stringify(draftSnapshot)
    if (!resp.context.modelReady) status.value = 'no-model'
    return resp.contextToken
  }

  async function loadContext(): Promise<void> {
    if (!host) return
    const my = ++gen
    loadingContext.value = true
    error.value = null
    const draftSnapshot = cloneJson(host.draft())
    contextDraftJson = JSON.stringify(draftSnapshot)
    try {
      const resp = await api.context({
        space: host.space,
        projectId: host.projectId,
        ontologyId: host.ontologyId,
        targetKind: host.targetKind,
        targetId: host.targetId,
        purpose: 'fill',
        draft: draftSnapshot,
      })
      if (my !== gen) return
      contextInfo.value = resp.context
      contextToken.value = resp.contextToken
      status.value = resp.context.modelReady ? 'open' : 'no-model'
    } catch (err) {
      if (my !== gen) return
      error.value = describeError(err)
      status.value = 'error'
    } finally {
      if (my === gen) loadingContext.value = false
    }
  }

  /** 打开面板：切目标整卡重置（不复用另一目标的输入/答案/结果）；同目标保留输入并重取上下文校验一致性。
   *  opts.reveal=false：只取上下文不展开（受控宿主的常驻挂载，T10 F1/F2 修复）——面板保持收起，
   *  展开交宿主的 :open 驱动；缺省保持旧行为（挂载即展开）。 */
  async function open(binding: AutofillHostBinding, opts?: { reveal?: boolean }): Promise<void> {
    gen++ // 作废在途请求（含切目标/重新打开）
    if (status.value === 'generating' || status.value === 'applying') status.value = 'open'
    const sameTarget = !!host && sameTargetAs(host, binding)
    host = binding
    collapsed.value = opts?.reveal === false ? true : false
    error.value = null
    notice.value = ''
    clearHelp()
    if (!sameTarget) {
      intent.value = ''
      resetRoundUnit()
      status.value = 'idle'
      lastSchema.value = null
      contextToken.value = ''
      contextInfo.value = null
    }
    await loadContext()
  }

  function close(): void {
    gen++ // 关闭 → 代际失效：迟到响应零写入
    if (status.value === 'generating' || status.value === 'applying') {
      status.value = preGenStatus === 'generating' || preGenStatus === 'applying' ? 'open' : preGenStatus
    }
    collapsed.value = true
  }

  function expand(): void {
    gen++ // 重新打开 → 在途请求作废
    collapsed.value = false
    notice.value = ''
  }

  function cancel(): void {
    gen++
    if (status.value === 'generating' || status.value === 'applying') {
      status.value = preGenStatus === 'generating' || preGenStatus === 'applying' ? 'open' : preGenStatus
      notice.value = '已取消本次生成；已发出的服务端调用可能仍在进行，其结果将被忽略。'
    } else if (loadingContext.value) {
      loadingContext.value = false
      notice.value = '已取消获取上下文。'
    }
  }

  /** fill 请求体（protocol:2；续轮带 sessionId + answers） */
  function buildFillBody(token: string, draft: Record<string, unknown>): AssistGenerateRequest {
    const body: AssistGenerateRequest = {
      requestId: makeRequestId(),
      contextToken: token,
      mode: 'fill',
      draft,
      protocol: 2,
    }
    const text = intent.value.trim()
    if (text) body.intent = text
    if (awaitingAnswers && sessionId.value) {
      body.sessionId = sessionId.value
      const ans: AutofillAnswerInput[] = []
      for (const q of questions.value) {
        const a = answers.value[q.id]
        if (!a) continue
        const item: AutofillAnswerInput = { questionId: q.id }
        if (typeof a.value === 'string' && a.value !== '') item.value = a.value
        if (a.unsure) item.unsure = true
        if (item.value !== undefined || item.unsure) ans.push(item)
      }
      if (ans.length) body.answers = ans
    }
    return body
  }

  async function generate(): Promise<void> {
    if (!host || collapsed.value) return
    if (status.value === 'generating' || status.value === 'applying') return // 禁重复提交
    if (status.value === 'no-model') return
    const my = ++gen
    error.value = null
    notice.value = ''
    preGenStatus = status.value
    status.value = 'generating'
    try {
      const token = await ensureContext(my) // 草稿漂移 → 先重取上下文（续轮握手前半）
      if (my !== gen || !token) return
      const stNow = status.value as AutofillStatus // 显式放宽（await 后字面量收窄会误判）
      if (stNow === 'no-model') return // 重取后发现未配置模型：不发生成请求
      const resp = await api.generate(buildFillBody(token, cloneJson(host.draft())))
      if (my !== gen) return // 迟到响应：零写入
      handleFillResponse(resp)
    } catch (err) {
      if (my !== gen) return
      const e = describeError(err)
      // 错误保留输入与已记录答案（需求 §4.3：不假称成功，可重试）
      if (e.code === 'MODEL_NOT_CONFIGURED') status.value = 'no-model'
      else {
        error.value = e
        status.value = 'error'
      }
    }
  }

  function labelOf(field: string): string {
    const list = contextInfo.value?.editableFields ?? []
    const exact = list.find(f => f.key === field)
    if (exact) return exact.label
    const lastSeg = field.split('.').pop() ?? field
    return list.find(f => f.key === lastSeg)?.label ?? field
  }

  function handleFillResponse(resp: AssistGenerateResponse | AutofillFillResponse): void {
    if (!isFillResponse(resp)) {
      error.value = { code: 'MODEL_BAD_RESPONSE', message: '响应协议不符（缺少 autofill/1 标识），已丢弃本次结果。' }
      status.value = 'error'
      return
    }
    status.value = 'applying'
    lastSchema.value = { formId: resp.formId, schemaVersion: resp.schemaVersion, schemaDigest: resp.schemaDigest }
    // 契约指纹比对（本地生成物；T1 落地前 binding 未注入 contractInfo 则跳过）
    const ci = host?.contractInfo?.() ?? null
    if (ci && (ci.schemaVersion !== resp.schemaVersion || ci.schemaDigest !== resp.schemaDigest)) {
      error.value = { code: 'CONTEXT_STALE', message: '表单契约已更新，本次结果已失效，请重试。' }
      status.value = 'error'
      return
    }
    if (resp.status === 'empty') {
      questions.value = []
      answers.value = {}
      awaitingAnswers = false
      // 服务端 empty 分两种（T10 F3，需求 §4.3/A14）：真的内容一致 / 无可填内容时
      // unresolved 为空，显示「内容已一致，无需修改」；若 empty 同时带回 unresolved
      // （模型明确拒绝并给了原因，如原子组不完整），必须如实展示这些原因，不能吞掉
      // 换一句误导性的「无需修改」——面板据 unresolved 非空展示「未能填写」清单。
      unresolved.value = Array.isArray(resp.unresolved) ? resp.unresolved : []
      status.value = 'empty'
      return
    }
    const h = host as AutofillHostBinding
    // 与 buildFillBody 同一判定：非续轮（无待答问题/无会话）→ 本次写入开启新撤销单元。
    // 失败的生成不在此路径（无写操作），上一轮撤销单元保留。
    const continuing = awaitingAnswers && !!sessionId.value
    if (!continuing) resetRoundUnit()
    const pre = cloneJson(h.draft())
    const next = cloneJson(pre)
    const applied = applyOperations(next, resp.operations ?? [], h.codecs ?? {})
    const topKeys = topLevelChanges(pre, next)
    if (!roundActive.value) {
      // 撤销单元开始：快照本轮起点（首次写入前）。走 host.snapshot()/restore() 对称通道，
      // 快照完整编辑器状态（draft() 可能只是白名单投影，直接用它恢复会丢未投影字段）。
      roundActive.value = true
      roundSnapshot = cloneJson(h.snapshot())
      appliedCount.value = 0
      fieldChanges.value = []
      roundUndone.value = false
      canUndo.value = false
      undoHint.value = ''
    }
    sessionId.value = resp.sessionId || sessionId.value
    roundId.value = resp.roundId || roundId.value
    if (topKeys.length) {
      writeBack(h, next, topKeys) // 回填只改本地草稿，绝不触发表单保存
      if (!canUndo.value) canUndo.value = true
      appliedCount.value += topKeys.length
      for (const rec of applied.records) {
        if (!rec.changed) continue
        fieldChanges.value = fieldChanges.value.filter(c => c.field !== rec.field).concat({
          field: rec.field,
          label: labelOf(rec.field),
          oldText: fmtValue(rec.oldValue),
          newText: fmtValue(rec.newValue),
        })
      }
    }
    questions.value = resp.questions ?? []
    unresolved.value = resp.unresolved ?? []
    answers.value = {} // 已提交/已消费
    awaitingAnswers = questions.value.length > 0
    if (awaitingAnswers || unresolved.value.length > 0) {
      status.value = 'questions' // 留在抽屉：说明「已填 N 项，另有 M 项待补充」
    } else {
      status.value = 'done'
      collapsed.value = true // 一次回填：抽屉收起，状态条交宿主渲染
    }
  }

  /** 写回：优先 applyDraft（整稿原子替换）；旧适配退回 apply(顶层变更值)。 */
  function writeBack(h: AutofillHostBinding, next: Record<string, unknown>, topKeys: string[]): void {
    if (h.applyDraft) {
      h.applyDraft(cloneJson(next))
      return
    }
    const values: Record<string, unknown> = {}
    for (const k of topKeys) values[k] = next[k]
    h.apply(values)
  }

  function allAnswered(): boolean {
    return questions.value.length > 0 && questions.value.every(q => !!answers.value[q.id])
  }

  function answer(questionId: string, value?: string, unsure?: boolean): void {
    if (!questions.value.some(q => q.id === questionId)) return // 只接当前轮问题（旧问题 ID 拒绝）
    const entry: AutofillRecordedAnswer = {}
    if (typeof value === 'string' && value !== '') entry.value = value
    if (unsure) entry.unsure = true
    if (entry.value !== undefined || entry.unsure) answers.value = { ...answers.value, [questionId]: entry }
    else {
      const next = { ...answers.value }
      delete next[questionId]
      answers.value = next
    }
    if (allAnswered()) void submitAnswers() // 补答后继续完成（§4.3）
  }

  async function submitAnswers(): Promise<void> {
    if (status.value === 'generating' || status.value === 'applying') return
    if (!questions.value.length) return
    await generate()
  }

  function undoRound(): boolean {
    if (!host || !canUndo.value || roundSnapshot === null) return false // 手改后拒绝：不覆盖用户改动
    host.restore(cloneJson(roundSnapshot))
    roundSnapshot = null
    canUndo.value = false
    roundActive.value = false
    roundUndone.value = true
    appliedCount.value = 0
    fieldChanges.value = []
    questions.value = []
    unresolved.value = []
    answers.value = {}
    awaitingAnswers = false
    notice.value = '已撤销本次自动填写，表单已恢复。'
    return true
  }

  function notifyDraftChanged(): void {
    gen++ // 生成中手改 → 在途请求作废，迟到响应零写入
    if (status.value === 'generating' || status.value === 'applying') {
      status.value = preGenStatus === 'generating' || preGenStatus === 'applying' ? 'open' : preGenStatus
      notice.value = '检测到手动修改，本次生成已作废；迟到结果不会写入。'
    }
    if (loadingContext.value) loadingContext.value = false
    if (canUndo.value) {
      canUndo.value = false
      roundSnapshot = null
      undoHint.value = '已保留你的手动修改，本次自动填写不可直接撤销。'
    } else if (roundActive.value && !undoHint.value) {
      undoHint.value = '已保留你的手动修改，本次自动填写不可直接撤销。'
    }
  }

  async function refreshContext(): Promise<void> {
    if (!host || collapsed.value) return
    await loadContext()
  }

  // ── check/explain 次要帮助：只读渲染（issues/explanation/suggestions），不提供任何写入 ──
  async function runHelp(mode: 'check' | 'explain'): Promise<void> {
    if (!host || collapsed.value) return
    if (status.value === 'generating' || status.value === 'applying') return // 不与主流程并发
    const my = ++gen
    helpMode.value = mode
    helpStatus.value = 'running'
    helpError.value = null
    try {
      const token = await ensureContext(my)
      if (my !== gen || !token) return
      const body: AssistGenerateRequest = {
        requestId: makeRequestId(),
        contextToken: token,
        mode,
        draft: cloneJson(host.draft()),
      }
      const text = intent.value.trim()
      if (text) body.intent = text
      const resp = await api.generate(body)
      if (my !== gen) return
      if (isFillResponse(resp)) {
        helpError.value = { code: 'MODEL_BAD_RESPONSE', message: '帮助响应协议不符，已丢弃。' }
        helpStatus.value = 'error'
        return
      }
      helpResult.value = resp
      helpStatus.value = 'done'
    } catch (err) {
      if (my !== gen) return
      helpError.value = describeError(err)
      helpStatus.value = 'error'
    }
  }

  function clearHelp(): void {
    helpStatus.value = 'idle'
    helpResult.value = null
    helpError.value = null
  }

  return {
    status, collapsed, loadingContext, contextInfo, contextToken, intent, error, notice,
    questions, answers, unresolved, sessionId, roundId, lastSchema,
    canUndo, undoHint, roundSummary, statusBarText,
    open, close, expand, cancel, generate, answer, submitAnswers, undoRound, notifyDraftChanged, refreshContext,
    helpMode, helpStatus, helpResult, helpError, runHelp, clearHelp,
  }
}
