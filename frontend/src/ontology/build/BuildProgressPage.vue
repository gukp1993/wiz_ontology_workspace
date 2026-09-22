<!-- ─── A04 生成进度（从物料自动构建本体；契约：接口文档 08 §5 运行）───
     布局基准：需求原型 交互原型_v1.html 的 generatePage()（第 52 行）——
     左侧五阶段列表与运行操作，右侧「本次生成依据」（确认范围时冻结的基线，不含密钥）。
     行为要点：
     · fetchRun 每 1.5 秒轮询；只展示服务端给出的真实阶段与 progress.done/total，
       不做假倒计时与百分比动画；到达终态（已完成/失败/已取消/已中断）停止轮询，卸载清定时器。
     · 抽取阶段等待期间服务端每 15 秒写一次批内心跳（waitingPosition/waitingCount/waitedSeconds），
       进度行据此追加「等待第 N 批返回（还有 X 批未完成，已等待 Y）」，超过 60 秒再加一句安心提示；
       心跳字段缺失时展示与改动前完全一致（不推算、不本地计时）。
     · state=interrupted 必须写明「服务重启导致中断，可重试」，绝不显示为运行中。
     · 生成日志区（需求：生成进度实时可观测）：逐行展示 checkpoint.generate.log 与 generate.notes，
       默认展开、可折叠（折叠只显示最新一条）；新行到达自动滚到底部（用户上滚阅读时暂停吸附）；
       字段缺失/为空时整块不渲染，兼容旧数据。批次状态行逐批标 ✓/✗（失败批带原因）。
     · 08 §14.3（生成控输出 v3）：checkpoint.generate 按 schemaVersion 分支显示——schema1 保留旧批次
       展示（批号 chips/批次检查点，仅 !isSchemaV2 时渲染）；schema2 显示目标进度（已处理目标 X/Y、
       成功叶任务、待处理/失败/受阻、因输出超限拆分次数——分母按语义目标解释，splitParents 不算失败）、
       估算方式显式标注、预算三项（空值=未配置）与受阻原因+处置指引（此时重试按钮为「重新规划生成」，
       不把「重试」当万能入口）；log/notes 两个版本都保留（08 §14.3 明确保留）。
     · Run.usage 新增 token 统计（08 §14.3）：completionTokens 可空（null 显示「含未知」，不拿局部和
       冒充总数）、已知部分和、未知用量调用次数（提示外部计费可能已发生）；reasoning 不单独加算显示。
     · 失败保留已完成阶段并展示 run.error 原文；取消/重试分别走 build-run-cancel / build-run-resume。
     · 取消提示：取消不保证立即终止已发出的模型请求，但晚到的结果不会写入当前批次。
     · 用量（calls/promptBytes/completionBytes/durationMs）作为次要信息，没有就不显示。
     请求一律经 ./api（内部走 app/http.ts），错误如实展示、不吞。 -->
<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import AppError from '../../shared/AppError.vue'
import { cancelRun, errorMessage, fetchRun, resumeRun } from './api'
import {
  RUN_STATE_LABELS, STAGE_LABELS, TASK_STATUS_LABELS,
  blockingAdviceText, estimateKindLabel, formatBytes, formatTokenCount, isSchemaV2,
  labelOf, runErrorRetryable, runErrorText,
} from './types'
import type { BuildRun, RunStage } from './types'

const props = defineProps<{ taskId: string; runId?: string }>()
const emit = defineEmits<{ review: []; back: [] }>()

// 五阶段顺序（08 §1.5：generate 用 retrieve/align/abstract/verify/adapt），文案取 types.ts 的 STAGE_LABELS。
const STAGE_ORDER: RunStage[] = ['retrieve', 'align', 'abstract', 'verify', 'adapt']

const run = ref<BuildRun | null>(null)
const taskStatus = ref('')
const activeRunId = ref(props.runId || '')
const loading = ref(true)
const loadError = ref('')
const notFound = ref(false)
const refreshing = ref(false)
const cancelling = ref(false), resuming = ref(false)
const actionError = ref(''), actionNote = ref('')

const POLL_INTERVAL_MS = 1500
const TERMINAL_STATES: BuildRun['state'][] = ['succeeded', 'failed', 'cancelled', 'interrupted']
let pollTimer: ReturnType<typeof setTimeout> | null = null
let disposed = false
let inFlight = false

const isTerminal = (state: string): boolean => TERMINAL_STATES.some(s => s === state)

const badgeTone = computed(() => {
  const state = run.value?.state
  if (state === 'succeeded') return 'pill-ok'
  if (state === 'failed') return 'pill-error'
  if (state === 'cancelled' || state === 'interrupted') return 'pill-warn'
  return ''
})
/** 页面状态徽标（需求 §6 / 原型 generatePage 的措辞；未知状态原样回显，不伪装成运行中）。 */
const STATE_BADGE_LABELS: Record<string, string> = {
  queued: '排队中', running: '生成中', succeeded: '已完成', failed: '阶段失败', cancelled: '已取消', interrupted: '已中断',
}
const stateLabel = computed(() => {
  const state = run.value?.state
  if (!state) return '未开始'
  return STATE_BADGE_LABELS[state] || `未知状态（${labelOf(RUN_STATE_LABELS, state, state)}）`
})

const stateNote = computed(() => {
  const state = run.value?.state
  if (state === 'interrupted') return '服务重启导致中断，可重试：已完成阶段的结果保留，重试从匹配到的检查点继续。'
  if (state === 'cancelled') return '生成已取消，未创建任何本体；可重试继续剩余阶段。'
  if (state === 'failed') {
    // schema2 计划受阻不是普通阶段失败：不把「重试」当万能入口（08 §14.3）
    if (generateV2.value?.blocking) return '生成因计划受阻而停止，未创建正式本体；已成功目标的候选保留，请按受阻原因处理后重新规划生成。'
    return '生成在某个阶段失败，未创建正式本体；已完成阶段的结果保留，可重试当前阶段。'
  }
  if (state === 'queued') return '运行已排队，尚未开始处理。'
  if (state === 'succeeded') return '初稿已就绪，请人工评审候选定义与证据。'
  return ''
})

const running = computed(() => run.value?.state === 'running' || run.value?.state === 'queued')

/** V2-8：批次检查点摘要——失败批次列表（生成运行专用；schema2 无批次字段，恒为空）。 */
const failedBatches = computed(() => {
  const generate = run.value?.checkpoint?.generate
  if (!generate || isSchemaV2(generate)) return []
  const batches = generate.batches
  return batches?.failed?.length ? batches.failed : []
})
const batchTotal = computed(() => {
  const generate = run.value?.checkpoint?.generate
  if (!generate || isSchemaV2(generate)) return 0
  return generate.batches?.total ?? 0
})
const planPersisted = computed(() => {
  const generate = run.value?.checkpoint?.generate
  if (!generate || isSchemaV2(generate)) return false
  return generate.planPersisted === true
})

// ── 08 §14.3：schema2（目标/作业计划）分支——分母按语义目标解释，不出现旧批号口径 ──
/** schema2 检查点摘要；非 schema2 运行（旧批次计划/空）为 null，页面按 schema1 展示。 */
const generateV2 = computed(() => {
  const generate = run.value?.checkpoint?.generate
  return isSchemaV2(generate) ? generate : null
})
const isGenerateV2 = computed(() => generateV2.value !== null)
/** 主进度：X/Y 按语义目标计；计划未给出覆盖计数时不编数字。 */
const v2TargetText = computed(() => {
  const coverage = generateV2.value?.coverage
  if (!coverage || !(coverage.targetTotal > 0)) return '目标进度（服务端尚未给出计数）'
  return `已处理目标 ${coverage.targetCompleted} / ${coverage.targetTotal}`
})
/** 细分行：成功叶任务 N · 待处理/失败/受阻 M · 因输出超限拆分 K 次（splitParents 不算失败）。 */
const v2JobsText = computed(() => {
  const v2 = generateV2.value
  if (!v2) return ''
  return [
    `成功叶任务 ${v2.jobs.succeeded}`,
    `待处理 ${v2.coverage.targetPending}`,
    `失败 ${v2.jobs.failed}`,
    `受阻 ${v2.jobs.blocked}`,
    `因输出超限拆分 ${v2.jobs.splitParents} 次`,
  ].join(' · ')
})
/** 估算方式显式标注（08 §14.3：utf8_proxy 非精确 tokenizer，绝不冒充精确值）。 */
const v2EstimateText = computed(() => (
  generateV2.value ? `估算方式：${estimateKindLabel(generateV2.value.estimateKind)}` : ''
))
/** 预算三项：上下文限制 / 单次输出请求上限 / 单次输出规划目标（空值显示「未配置」）。 */
const v2BudgetText = computed(() => {
  const budget = generateV2.value?.budget
  if (!budget) return ''
  const fmt = (v: number | null): string => (typeof v === 'number' ? `${formatTokenCount(v)} token` : '未配置')
  return `预算：上下文限制 ${fmt(budget.contextLimit)} · 单次输出请求上限 ${fmt(budget.requestOutputCap)} · 单次输出规划目标 ${fmt(budget.targetOutputBudget)}`
})
/** 计划受阻信息（null=未受阻）；处置指引见 blockingAdviceText。 */
const v2Blocking = computed(() => generateV2.value?.blocking || null)
/** 重试入口文案：schema2 受阻时不是「重试」而是「重新规划生成」（当前计划不可继续，08 §14.5）。 */
const retryLabel = computed(() => {
  if (generateV2.value?.blocking) return '重新规划生成'
  return failedBatches.value.length ? '重试失败批次' : '重试 / 继续生成'
})
const retryBusyLabel = computed(() => (generateV2.value?.blocking ? '正在提交重新规划…' : '正在提交重试…'))

// ── 批次状态行 + 生成日志区（需求：生成进度实时可观测 F2/F3；1.5 秒轮询天然实时） ──
/** 已完成批号列表：schema2/旧数据/扫描运行可能没有 batches 字段，一律按空数组兜底。 */
const doneBatches = computed<number[]>(() => {
  const generate = run.value?.checkpoint?.generate
  if (!generate || isSchemaV2(generate)) return []
  const done = generate.batches?.done
  return Array.isArray(done) ? done : []
})
/** 生成日志行：可能不存在/为空/旧数据无此字段，缺失一律按空数组处理（整块不渲染）。 */
const generateLog = computed<string[]>(() => {
  const log = run.value?.checkpoint?.generate?.log
  return Array.isArray(log) ? log : []
})
/** 模型说明/管线注记：与日志同一区块展示（日志行在前、说明在后，服务端各自按时间追加）。 */
const generateNotes = computed<string[]>(() => {
  const notes = run.value?.checkpoint?.generate?.notes
  return Array.isArray(notes) ? notes : []
})
const hasGenerateLog = computed(() => generateLog.value.length > 0 || generateNotes.value.length > 0)
const logTitle = computed(() => `生成日志（${generateLog.value.length + generateNotes.value.length} 条）`)
/** 折叠时只显示最新一条：优先最新日志行，无日志行时取最新说明。 */
const latestLogLine = computed(() => {
  if (generateLog.value.length > 0) return generateLog.value[generateLog.value.length - 1]
  return generateNotes.value[generateNotes.value.length - 1] || ''
})

const logExpanded = ref(true)
const logScrollRef = ref<HTMLElement | null>(null)
/** 用户手动上滚离底时暂停自动吸附；滚回底部附近或重新展开时恢复。 */
const logPinnedToBottom = ref(true)

async function scrollLogToBottom() {
  await nextTick()
  const el = logScrollRef.value
  if (el) el.scrollTop = el.scrollHeight
}
function onLogScroll() {
  const el = logScrollRef.value
  if (!el) return
  logPinnedToBottom.value = el.scrollHeight - el.scrollTop - el.clientHeight < 40
}
function toggleLogExpanded() {
  logExpanded.value = !logExpanded.value
  if (logExpanded.value) {
    logPinnedToBottom.value = true
    void scrollLogToBottom()
  }
}
// 轮询带回新日志行/说明时自动滚到底部（折叠状态或用户上滚阅读时不打扰）
watch(
  () => [generateLog.value.length, generateNotes.value.length, logExpanded.value] as const,
  ([, , expanded]) => {
    if (!expanded || !logPinnedToBottom.value) return
    void scrollLogToBottom()
  },
)


/** 失败入口文案（G23）：「第 N 步失败（原因）」——批次失败给出批号，其余给出阶段。 */
const failureEntry = computed(() => {
  const value = run.value
  if (!value || value.state !== 'failed') return ''
  if (failedBatches.value.length) {
    const first = failedBatches.value[0]
    const others = failedBatches.value.length > 1 ? `（共 ${failedBatches.value.length} 批失败）` : ''
    return `第 ${first.position}/${batchTotal.value || '?'} 批抽取失败${others}：${first.error}`
  }
  const stage = currentStageLabel.value || '处理'
  const reason = runErrorText(value) || '未知原因'
  return `${stage}失败：${reason}`
})
const currentStageIndex = computed(() => {
  const value = run.value
  if (!value || !value.stage) return -1
  return STAGE_ORDER.indexOf(value.stage)
})
type StageStatus = 'done' | 'current' | 'failed' | 'todo'
const STAGE_STATUS_TEXT: Record<StageStatus, string> = { done: '已完成', current: '处理中', failed: '失败', todo: '待处理' }

function stageStatus(index: number): StageStatus {
  const value = run.value
  if (!value) return 'todo'
  if (value.state === 'succeeded') return 'done'
  const current = currentStageIndex.value
  if (current < 0) return 'todo'
  if (index < current) return 'done'
  if (index > current) return 'todo'
  if (value.state === 'failed') return 'failed'
  if (value.state === 'running' || value.state === 'queued') return 'current'
  return 'todo'
}

const currentStageLabel = computed(() => {
  const value = run.value
  if (!value) return ''
  return value.stageLabel || (value.stage ? labelOf(STAGE_LABELS, value.stage, '') : '')
})
/** 批次并发等待心跳（仅抽取阶段等待期间出现；批次完成后服务端不再下发这三个字段）。
 *  字段缺失时返回 null，展示口径与改动前完全一致（向后兼容）。 */
const batchWait = computed(() => {
  const progress = run.value?.progress
  const position = progress?.waitingPosition
  if (typeof position !== 'number' || position <= 0) return null
  const count = typeof progress?.waitingCount === 'number' && progress.waitingCount > 0 ? progress.waitingCount : 0
  const waited = typeof progress?.waitedSeconds === 'number' && progress.waitedSeconds >= 0 ? progress.waitedSeconds : null
  return { position, count, waited }
})
/** 等待文案：秒数一律用服务端心跳值，不做本地倒计时/推算。 */
const waitText = computed(() => {
  const wait = batchWait.value
  if (!wait) return ''
  const parts: string[] = []
  if (wait.count > 0) parts.push(`还有 ${wait.count} 批未完成`)
  if (wait.waited !== null) parts.push(`已等待 ${formatDuration(wait.waited * 1000)}`)
  return parts.length > 0 ? `等待第 ${wait.position} 批返回（${parts.join('，')}）` : `等待第 ${wait.position} 批返回`
})
/** schema2 等待文案：沿用服务端心跳数值，但不用「批」措辞（目标/作业口径，08 §14.3）。 */
const v2WaitText = computed(() => {
  const wait = batchWait.value
  if (!wait) return ''
  const parts: string[] = []
  if (wait.count > 0) parts.push(`还有 ${wait.count} 项未完成`)
  if (wait.waited !== null) parts.push(`已等待 ${formatDuration(wait.waited * 1000)}`)
  return parts.length > 0 ? `正在等待模型返回（${parts.join('，')}）` : '正在等待模型返回'
})
/** 等待超过 60 秒时的安心提示：单批抽取耗时 1–4 分钟属正常，避免用户误判卡死。 */
const waitHint = computed(() => {
  const wait = batchWait.value
  if (!wait || wait.waited === null || wait.waited <= 60) return ''
  return generateV2.value
    ? '模型正在处理较大目标，单项可能耗时 1–4 分钟，请耐心等待；进度每 15 秒刷新一次。'
    : '模型正在处理大批次，单批可能耗时 1–4 分钟，请耐心等待；进度每 15 秒刷新一次。'
})

/** 真实进度文案：只回报服务端给出的阶段名与 done/total，不推算百分比、不做倒计时；
 *  等待期间追加批次心跳（已完成批数 / 等待批号 / 已等待秒数），不覆盖既有 done/total。 */
const progressText = computed(() => {
  const value = run.value
  if (!value) return ''
  // schema2：分母按语义目标解释，不出现「批」口径（08 §14.3）
  if (generateV2.value) {
    return batchWait.value ? `${v2TargetText.value} · ${v2WaitText.value}` : v2TargetText.value
  }
  const stage = currentStageLabel.value || '阶段处理中'
  const done = value.progress?.done, total = value.progress?.total
  const wait = batchWait.value
  if (typeof done !== 'number' || typeof total !== 'number') {
    return wait ? `${stage}（服务端尚未给出计数）· ${waitText.value}` : `${stage}（服务端尚未给出计数）`
  }
  if (!wait) return `${stage} ${done} / ${total}`
  return `${stage}：已完成 ${done} / ${total} 批 · ${waitText.value}`
})
const progressPercent = computed(() => {
  // 仅用于进度条宽度；schema2 比例按目标权重计算（整合计划 §7），只信覆盖计数、不用旧批号。
  const value = run.value
  if (!value) return null
  const coverage = generateV2.value?.coverage
  if (coverage) {
    if (!(coverage.targetTotal > 0)) return null
    return Math.min(100, Math.max(0, Math.round((coverage.targetCompleted / coverage.targetTotal) * 100)))
  }
  const done = value.progress?.done, total = value.progress?.total
  if (typeof done !== 'number' || typeof total !== 'number' || total <= 0) return null
  return Math.min(100, Math.max(0, Math.round((done / total) * 100)))
})

const baselineRows = computed(() => {
  const value = run.value
  if (!value) return [] as { label: string; value: string }[]
  const base = value.baseline
  const hashes = Array.isArray(base?.materialHashes) ? base.materialHashes : []
  const rows = [
    { label: '材料数', value: `${hashes.length} 份` },
    { label: '材料修订', value: String(base?.materialRevision ?? '—') },
    { label: '范围修订', value: String(base?.scopeRevision ?? '—') },
    { label: '解析器版本', value: base?.parserVersion || '—' },
    { label: '提示词版本', value: base?.promptVersion || '—' },
    { label: '模型指纹', value: base?.providerFingerprint || '—' },
  ]
  // schema2（08 §14.5）：显示计划标识与代次，便于理解「新建计划/重新规划」后的分母与候选变化
  const v2 = generateV2.value
  if (v2) {
    rows.push({ label: '计划 ID', value: v2.planId || '—' })
    rows.push({ label: '计划代次', value: v2.planEpoch > 0 ? `第 ${v2.planEpoch} 代` : '—' })
  }
  return rows
})
const usageRows = computed(() => {
  const usage = run.value?.usage
  if (!usage) return [] as { label: string; value: string }[]
  const rows: { label: string; value: string }[] = []
  if (typeof usage.calls === 'number' && usage.calls > 0) rows.push({ label: '模型调用', value: `${usage.calls} 次` })
  if (typeof usage.durationMs === 'number' && usage.durationMs > 0) rows.push({ label: '累计耗时', value: formatDuration(usage.durationMs) })
  if (typeof usage.promptBytes === 'number' && usage.promptBytes > 0) rows.push({ label: '输入字节', value: formatBytes(usage.promptBytes) })
  if (typeof usage.completionBytes === 'number' && usage.completionBytes > 0) rows.push({ label: '输出字节', value: formatBytes(usage.completionBytes) })
  // 08 §14.3 token 统计：completionTokens=null 表示有调用用量未知（显示「含未知」，不拿局部和冒充总数）；
  // completionTokens 已含推理输出，reasoning 不单独显示加算。字段缺省（旧数据/未接线）则整组不显示。
  if (typeof usage.completionTokens === 'number') rows.push({ label: '输出 token', value: formatTokenCount(usage.completionTokens) })
  else if (usage.completionTokens === null) rows.push({ label: '输出 token', value: '含未知' })
  if (typeof usage.knownCompletionTokens === 'number' && usage.knownCompletionTokens > 0) {
    rows.push({ label: '已知输出 token（部分和）', value: formatTokenCount(usage.knownCompletionTokens) })
  }
  if (typeof usage.unknownUsageCalls === 'number' && usage.unknownUsageCalls > 0) {
    rows.push({ label: '未知用量调用', value: `${usage.unknownUsageCalls} 次（外部计费可能已发生）` })
  }
  return rows
})
function formatDuration(ms: number): string {
  const seconds = Math.max(0, Math.round(ms / 1000))
  if (seconds < 60) return `${seconds} 秒`
  const minutes = Math.floor(seconds / 60), rest = seconds % 60
  return rest === 0 ? `${minutes} 分` : `${minutes} 分 ${rest} 秒`
}

function isNotFound(error: unknown): boolean {
  return error !== null && typeof error === 'object' && (error as { status?: number }).status === 404
}

// ── 轮询（1.5 秒；终态停止；卸载清理） ──────────────────────────────────────
function stopPolling() {
  if (pollTimer !== null) { clearTimeout(pollTimer); pollTimer = null }
}
function schedulePoll() {
  if (disposed) return
  pollTimer = setTimeout(() => { pollTimer = null; void poll() }, POLL_INTERVAL_MS)
}
async function poll() {
  if (disposed || inFlight) return
  if (props.taskId === '') { loading.value = false; loadError.value = '缺少任务标识，无法读取生成进度。'; return }
  inFlight = true
  try {
    const envelope = await fetchRun(props.taskId, activeRunId.value || undefined)
    run.value = envelope.run
    taskStatus.value = envelope.taskStatus || ''
    if (!activeRunId.value && envelope.run?.id) activeRunId.value = envelope.run.id
    notFound.value = false
    loadError.value = ''
    loading.value = false
    if (envelope.run && isTerminal(envelope.run.state)) { stopPolling(); return }
  } catch (error) {
    loading.value = false
    if (isNotFound(error)) { notFound.value = true; loadError.value = '' }
    else loadError.value = errorMessage(error)
    if (run.value && isTerminal(run.value.state)) { stopPolling(); return }
  } finally {
    inFlight = false
  }
  schedulePoll()
}
async function refreshNow() {
  refreshing.value = true
  await poll()
  refreshing.value = false
}

// ── 操作 ──────────────────────────────────────────────────────────────────
async function onCancel() {
  if (cancelling.value || !run.value) return
  const id = run.value.id || activeRunId.value
  if (!id) { actionError.value = '未取得运行标识，无法取消；请刷新后重试。'; return }
  cancelling.value = true
  actionError.value = ''
  actionNote.value = ''
  try {
    const result = await cancelRun(props.taskId, id)
    run.value = result.run
    if (!activeRunId.value && result.run?.id) activeRunId.value = result.run.id
    actionNote.value = '已请求取消。取消不保证立即终止已发出的模型请求，但晚到的结果不会写入当前批次。'
    stopPolling()
    if (!isTerminal(run.value.state)) schedulePoll()
  } catch (error) {
    actionError.value = errorMessage(error)
  } finally {
    cancelling.value = false
  }
}

async function onResume(resumeMode: 'auto' | 'abstract' = 'auto') {
  if (resuming.value || !run.value) return
  const id = run.value.id || activeRunId.value
  if (!id) { actionError.value = '未取得运行标识，无法重试；请返回范围页重新确认后生成。'; return }
  resuming.value = true
  actionError.value = ''
  actionNote.value = ''
  try {
    const result = await resumeRun(props.taskId, id, resumeMode)
    if (result.runId) activeRunId.value = result.runId
    // 重试是服务端行为：本地只回到「排队中」，attempt、阶段与错误都以轮询返回的服务端值为准。
    run.value = { ...run.value, state: 'queued', error: null }
    actionNote.value = resumeMode === 'abstract'
      ? `已从「${STAGE_LABELS.abstract}」重新生成：沿用上次筛选与对齐的结果，所有抽象批次重新执行。`
      : (generateV2.value?.blocking
        ? '已提交重新规划生成：以服务端计划校验结果为准；已成功目标的候选保留。'
        : (failedBatches.value.length
          ? '已提交「重试失败批次」：只重跑失败的批次，成功批次候选保留。'
          : '已提交重试：已完成阶段的结果保留，运行从匹配到的检查点继续。'))
    stopPolling()
    schedulePoll()
  } catch (error) {
    actionError.value = errorMessage(error)
  } finally {
    resuming.value = false
  }
}

watch(() => props.runId, next => {
  const value = next || ''
  if (value === activeRunId.value) return
  activeRunId.value = value
  run.value = null
  loading.value = true
  stopPolling()
  void poll()
})
watch(() => props.taskId, () => {
  run.value = null
  taskStatus.value = ''
  activeRunId.value = props.runId || ''
  loading.value = true
  notFound.value = false
  loadError.value = ''
  stopPolling()
  void poll()
})

onMounted(() => { void poll() })
onUnmounted(() => {
  disposed = true
  stopPolling()
})
</script>

<template>
<div class="bp-progress">
  <header class="bp-head">
    <div>
      <h1>构建本体初稿</h1>
      <p class="muted">按确认范围分析证据。此阶段不会创建正式本体。</p>
    </div>
    <div class="bp-head-actions">
      <span class="status-pill" :class="badgeTone">{{ stateLabel }}</span>
      <button type="button" :disabled="refreshing" @click="refreshNow()">{{ refreshing ? '刷新中…' : '刷新状态' }}</button>
      <button type="button" @click="emit('back')">← 返回范围</button>
    </div>
  </header>

  <p
    v-if="stateNote !== ''"
    class="bp-state-note"
    :class="{ 'bp-state-bad': run?.state === 'failed' || run?.state === 'interrupted' }"
  >{{ stateNote }}</p>

  <AppError
    v-if="notFound"
    title="未找到该生成批次"
    reason="任务或运行可能已被删除、不属于当前账号，或还没有启动过生成。"
    hint="请返回上一步确认范围后重新启动生成；已有材料与范围不会丢失。"
    retry-label="重新加载"
    secondary-label="返回范围"
    @retry="refreshNow()"
    @secondary="emit('back')"
  />
  <AppError
    v-else-if="loadError !== ''"
    title="读取生成进度失败"
    :reason="loadError"
    hint="任务在服务端后台继续执行，离开页面不会中断；恢复读取后再评估结果。"
    retry-label="重新加载"
    @retry="refreshNow()"
  />

  <div class="bp-grid">
    <!-- 左：处理阶段与操作 -->
    <section class="card bp-stages-card">
      <h2>处理阶段</h2>
      <p v-if="loading && !run" class="muted">正在读取运行状态…</p>
      <template v-else-if="run">
        <p class="bp-progress-line">
          <strong>{{ progressText }}</strong>
          <span v-if="run.attempt > 1" class="muted"> · 第 {{ run.attempt }} 次尝试</span>
          <span v-if="taskStatus" class="muted"> · 任务阶段：{{ labelOf(TASK_STATUS_LABELS, taskStatus, taskStatus) }}</span>
        </p>
        <div v-if="progressPercent !== null" class="bp-bar" role="progressbar" :aria-valuenow="progressPercent" aria-valuemin="0" aria-valuemax="100">
          <span :style="{ width: progressPercent + '%' }"></span>
        </div>
        <p v-if="waitHint !== ''" class="muted bp-hint">{{ waitHint }}</p>
      </template>

      <ol class="bp-stages">
        <li v-for="(stage, i) in STAGE_ORDER" :key="stage" class="bp-stage" :class="'bp-' + stageStatus(i)">
          <span class="bp-circle">{{ stageStatus(i) === 'done' ? '✓' : i + 1 }}</span>
          <div class="bp-stage-body">
            <strong>{{ labelOf(STAGE_LABELS, stage, stage) }}</strong>
            <p class="muted">
              {{ stageStatus(i) === 'failed' ? '在此阶段失败，已完成阶段的结果保留'
                : stageStatus(i) === 'current' ? '当前处理中'
                : stageStatus(i) === 'done' ? '已完成' : '待处理' }}
            </p>
          </div>
          <span class="status-pill bp-stage-badge" :class="stageStatus(i) === 'failed' ? 'pill-error' : stageStatus(i) === 'done' ? 'pill-ok' : ''">
            {{ STAGE_STATUS_TEXT[stageStatus(i)] }}
          </span>
        </li>
      </ol>

      <div v-if="failureEntry !== ''" class="bp-error-text">
        <span class="eyebrow">第 N 步失败（原因）</span>
        {{ failureEntry }}
      </div>

      <div v-if="run && runErrorText(run) !== ''" class="bp-error-text">
        <span class="eyebrow">失败原因原文</span>
        {{ runErrorText(run) }}
        <span v-if="!runErrorRetryable(run)" class="muted">
          （服务端标记为不可重试的结构性失败：请先修正范围或材料，再重新确认生成）
        </span>
      </div>

      <!-- 批次检查点/批号 chips：schema1（旧批次计划）专用展示，schema2 按目标口径显示、不再用批号 -->
      <div v-if="run && run.kind === 'generate' && !isGenerateV2 && failedBatches.length" class="bp-checkpoint">
        <span class="eyebrow">批次检查点</span>
        <span class="muted">
          共 {{ batchTotal }} 批 · 已完成 {{ doneBatches.length }} 批 ·
          失败 {{ failedBatches.length }} 批（第
          {{ failedBatches.map(b => b.position).join('、') }} 批）·
          {{ planPersisted ? '筛选/对齐产物已持久化，重试不重算确定性阶段' : '无持久化产物，重试将重算确定性阶段' }}
        </span>
      </div>

      <!-- 批次状态行：逐批标 ✓/✗，不只给汇总数字（空数据不渲染；仅 schema1） -->
      <div v-if="!isGenerateV2 && (doneBatches.length || failedBatches.length)" class="bp-batch-chips" role="list" aria-label="批次状态">
        <span
          v-for="position in doneBatches" :key="'ok-' + position" role="listitem"
          class="bp-batch-chip bp-batch-ok" :title="`批 ${position} 已完成`"
        >✓ 批 {{ position }}</span>
        <span
          v-for="failure in failedBatches" :key="'bad-' + failure.position" role="listitem"
          class="bp-batch-chip bp-batch-bad" :title="failure.error"
        >✗ 批 {{ failure.position }}：{{ failure.error }}</span>
      </div>

      <!-- 08 §14.3：schema2 目标计划进度——分母按语义目标解释，splitParents 不算失败；
           log/notes 未到达时整块不渲染（v-if 分支，属预期） -->
      <div v-if="generateV2" class="bp-target" role="group" aria-label="目标计划进度">
        <span class="eyebrow">目标进度（按语义目标计数，非旧批号）</span>
        <p class="bp-target-main">{{ v2TargetText }}</p>
        <p class="bp-target-sub">{{ v2JobsText }}</p>
        <p class="bp-target-sub">{{ v2EstimateText }}</p>
        <p v-if="v2BudgetText !== ''" class="bp-target-sub">{{ v2BudgetText }}</p>
      </div>

      <!-- schema2 计划受阻：原因 code+message 与处置指引（配置问题指配置项；预算耗尽需新建计划） -->
      <div v-if="v2Blocking" class="bp-error-text">
        <span class="eyebrow">生成受阻（{{ v2Blocking.code }}）</span>
        {{ v2Blocking.message }}
        <span class="bp-block-advise">{{ blockingAdviceText(v2Blocking.code) }}</span>
      </div>

      <!-- 生成日志区：逐行渲染 checkpoint.generate.log（+notes），最新在下；旧数据无字段时整块不渲染 -->
      <div v-if="hasGenerateLog" class="bp-genlog">
        <div class="bp-genlog-head">
          <span class="eyebrow">{{ logTitle }}</span>
          <button type="button" class="bp-genlog-toggle" :aria-expanded="logExpanded" @click="toggleLogExpanded()">
            {{ logExpanded ? '收起' : '展开' }}
          </button>
        </div>
        <p v-if="!logExpanded" class="bp-genlog-latest">{{ latestLogLine }}</p>
        <div v-else ref="logScrollRef" class="bp-genlog-body" @scroll.passive="onLogScroll">
          <p v-for="(line, i) in generateLog" :key="'log-' + i + '-' + line" class="bp-genlog-line">{{ line }}</p>
          <template v-if="generateNotes.length > 0">
            <p class="bp-genlog-group">模型说明</p>
            <p v-for="(note, i) in generateNotes" :key="'note-' + i + '-' + note" class="bp-genlog-line bp-genlog-note">{{ note }}</p>
          </template>
        </div>
      </div>

      <div class="bp-actions">
        <button v-if="running" type="button" class="danger-ghost" :disabled="cancelling" @click="onCancel()">
          {{ cancelling ? '正在取消…' : '取消生成' }}
        </button>
        <button
          v-if="run && isTerminal(run.state) && run.state !== 'succeeded'"
          type="button" class="primary" :disabled="resuming" @click="onResume('auto')"
        >
          {{ resuming ? retryBusyLabel : retryLabel }}
        </button>
        <button
          v-if="run && isTerminal(run.state) && run.state !== 'succeeded' && failedBatches.length"
          type="button" :disabled="resuming" @click="onResume('abstract')"
          :title="`沿用已完成的筛选与对齐，只重新执行「${STAGE_LABELS.abstract}」阶段`"
        >
          从「{{ STAGE_LABELS.abstract }}」重新生成
        </button>
        <button v-if="run && run.state === 'succeeded'" type="button" class="primary" @click="emit('review')">评审初稿 →</button>
      </div>
      <p v-if="running" class="muted bp-hint">取消不保证立即终止已发出的模型请求，但晚到的结果不会写入当前批次。</p>
      <p v-if="actionNote !== ''" class="inline-success">{{ actionNote }}</p>
      <p v-if="actionError !== ''" class="inline-error">{{ actionError }}</p>
    </section>

    <!-- 右：本次生成依据 -->
    <section class="card bp-baseline">
      <h2>本次生成依据</h2>
      <p class="muted">确认范围时冻结的输入基线（不含任何密钥）：</p>
      <dl class="bp-rows">
        <template v-for="row in baselineRows" :key="row.label">
          <dt>{{ row.label }}</dt>
          <dd>{{ row.value }}</dd>
        </template>
        <template v-if="run && run.batchId">
          <dt>批次</dt>
          <dd>{{ run.batchId }}</dd>
        </template>
      </dl>
      <p class="muted bp-note">此阶段不会创建正式本体：生成结果只是任务内的候选初稿，需人工评审后才可保存为新本体。</p>
      <details class="bp-details">
        <summary>运行与重试边界</summary>
        <p class="muted">任务在服务端后台执行，离开页面不会中断；服务重启后运行会标记为中断并允许重试，不会伪装成运行中。</p>
        <p class="muted">重试复用已匹配的检查点，不重复已完成的阶段；已发出的模型请求无法撤回，但晚到的结果不会写入当前批次。</p>
      </details>
      <div v-if="usageRows.length > 0" class="bp-usage">
        <p class="eyebrow">任务用量（次要信息）</p>
        <ul class="bp-usage-list">
          <li v-for="row in usageRows" :key="row.label">{{ row.label }}：{{ row.value }}</li>
        </ul>
      </div>
    </section>
  </div>
</div>
</template>

<style scoped>
.bp-progress{display:flex;flex-direction:column;gap:14px}
.bp-head{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;flex-wrap:wrap}
.bp-head h1{font-size:21px;margin:0 0 6px;display:flex;align-items:center;gap:8px}
.bp-head-actions{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.bp-state-note{border:1px solid var(--line);background:var(--paper-2);border-radius:var(--r-md);padding:10px 14px;font-size:13px;margin:0}
.bp-state-bad{border-color:var(--warn-line);background:var(--warn-soft);color:var(--warn)}
.bp-grid{display:grid;grid-template-columns:minmax(0,1.15fr) minmax(330px,.85fr);gap:16px;align-items:start}
.bp-stages-card,.bp-baseline{margin-bottom:0}
.bp-progress-line{margin:0 0 8px;font-size:14px}
.bp-bar{height:6px;border-radius:var(--r-pill);background:var(--paper-3);overflow:hidden;margin:0 0 12px}
.bp-bar span{display:block;height:100%;background:var(--blue)}
.bp-stages{list-style:none;margin:0;padding:0}
.bp-stage{display:flex;gap:14px;align-items:flex-start;padding:15px 0;border-bottom:1px solid var(--line)}
.bp-stage:last-child{border-bottom:0}
.bp-circle{width:28px;height:28px;flex:none;display:flex;align-items:center;justify-content:center;border-radius:50%;background:var(--paper-3);color:var(--muted);font-size:13px}
.bp-done .bp-circle{background:var(--ok-soft);color:var(--ok)}
.bp-current .bp-circle{background:var(--blue);color:var(--paper)}
.bp-failed .bp-circle{background:var(--danger-soft);color:var(--danger)}
.bp-stage-body{flex:1;min-width:0}
.bp-stage-body strong{display:block;font-size:14px}
.bp-stage-body p{margin:2px 0 0;font-size:13px}
.bp-stage-badge{margin-left:auto}
.bp-error-text{margin:14px 0 0;padding:10px 12px;border:1px solid var(--danger-line);background:var(--danger-soft);color:var(--danger);border-radius:var(--r-sm);font-size:13px;white-space:pre-wrap;overflow-wrap:anywhere}
.bp-error-text .eyebrow{display:block;color:var(--danger);margin-bottom:4px}
.bp-checkpoint{margin:14px 0 0;padding:10px 12px;border:1px solid var(--blue-line);background:var(--blue-soft);border-radius:var(--r-sm);font-size:13px}
.bp-checkpoint .eyebrow{display:block;color:var(--blue-ink);margin-bottom:4px}
.bp-target{margin:14px 0 0;padding:10px 12px;border:1px solid var(--blue-line);background:var(--blue-soft);border-radius:var(--r-sm);font-size:13px}
.bp-target .eyebrow{display:block;color:var(--blue-ink);margin-bottom:4px}
.bp-target-main{margin:0 0 4px;font-weight:650}
.bp-target-sub{margin:0 0 4px;color:var(--muted)}
.bp-block-advise{display:block;margin-top:4px}
.bp-batch-chips{display:flex;flex-wrap:wrap;align-items:flex-start;gap:6px;margin:14px 0 0}
.bp-batch-chip{font-size:12px;line-height:1.6;padding:2px 9px;border-radius:var(--r-pill);border:1px solid var(--line);background:var(--paper-2);max-width:100%;overflow-wrap:anywhere;white-space:pre-wrap}
.bp-batch-ok{color:var(--ok);border-color:var(--ok-line);background:var(--ok-soft)}
.bp-batch-bad{color:var(--danger);border-color:var(--danger-line);background:var(--danger-soft)}
.bp-genlog{margin:14px 0 0;border:1px solid var(--line);background:var(--paper-2);border-radius:var(--r-sm);padding:10px 12px}
.bp-genlog-head{display:flex;align-items:center;justify-content:space-between;gap:10px}
.bp-genlog-toggle{font-size:12px;padding:2px 10px}
.bp-genlog-latest{margin:8px 0 0;font-size:12px;line-height:1.7;color:var(--muted);white-space:pre-wrap;overflow-wrap:anywhere}
.bp-genlog-body{margin:8px 0 0;overflow-y:auto;max-height:200px}
.bp-genlog-line{margin:0;font-size:12px;line-height:1.7;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;white-space:pre-wrap;overflow-wrap:anywhere;color:var(--ink)}
.bp-genlog-group{margin:8px 0 0;font-size:11px;letter-spacing:1px;font-weight:650;color:var(--muted)}
.bp-genlog-note{color:var(--muted)}
.bp-actions{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-top:16px}
.bp-hint{margin:10px 0 0}
.danger-ghost{color:var(--danger);border-color:var(--danger-line)}
.danger-ghost:hover{background:var(--danger-soft);border-color:var(--danger-line);color:var(--danger)}
.bp-rows{display:grid;grid-template-columns:auto minmax(0,1fr);gap:8px 14px;margin:12px 0}
.bp-rows dt{font-size:12px;color:var(--muted)}
.bp-rows dd{margin:0;font-size:13px;overflow-wrap:anywhere}
.bp-note{margin-top:6px}
.bp-details{margin-top:14px;border-top:1px solid var(--line);padding-top:12px}
.bp-details p{margin:0 0 8px}
.bp-usage{margin-top:14px;border-top:1px solid var(--line);padding-top:12px}
.bp-usage-list{margin:6px 0 0;padding-left:20px;font-size:12px;color:var(--muted);line-height:1.8}
.pill-warn{background:var(--warn-soft);border-color:var(--warn-line);color:var(--warn)}
@media (max-width:1100px){.bp-grid{grid-template-columns:1fr}}
</style>
