// 「从物料自动构建本体」任务族前端类型与中文标签表。
// 字段与契约文档 `文档/接口文档/08-从物料自动构建本体接口.md` §1 数据结构一一对应
// （字段名一律按 08 分册，不自行改名/合并）；本文件只有类型、纯映射表与纯展示辅助函数，
// 不发请求、不持有状态（请求见 ./api.ts）。

// ── §1.1 BuildTask ──────────────────────────────────────────────────────────
/** 任务阶段：draft（未开始）→ materials（物料）→ scope（确定范围）→ generating（生成）→ review（评审）→ delivered（已交付）。 */
export type TaskStatus = 'draft' | 'materials' | 'scope' | 'generating' | 'review' | 'delivered'

export interface BuildTask {
  /** bk-uuid */
  id: string
  name: string
  status: TaskStatus
  /** 服务端给出的中文当前阶段（页面优先展示它，标签表仅作兜底） */
  stageLabel: string
  /** 任务级不透明修订 token（r-uuid），重命名等写操作必须回传 */
  revision: string
  /** 材料数量 */
  materials: number
  /** 材料清单修订号（整数）：材料增删/排除/恢复即 +1 */
  materialRevision: number
  /** 范围修订号（整数） */
  scopeRevision: number
  /** 当前批次 id（bb-uuid），无批次为 '' */
  currentBatch: string
  /** 已交付本体的 id，未交付为 '' */
  deliveryOntologyId: string
  /** 任务级过滤设置（08 §13 黑名单三层；'.' 前缀小写后缀；softExts=null=用默认软名单） */
  filter: TaskFilterSpec
  createdAt: string
  updatedAt: string
}

/** 08 §13：任务级过滤设置（黑名单三层中的「可配置」部分；硬黑名单不受影响）。 */
export interface TaskFilterSpec {
  /** 用户后缀白名单：越过软名单与自定义追加，不越过硬黑名单 */
  allowExts: string[]
  /** 软名单整体覆盖；null = 用默认软名单 */
  softExts: string[] | null
  /** 任务级追加排除后缀 */
  excludeExts: string[]
}

// ── §1.2 Material ──────────────────────────────────────────────────────────
/** 材料识别类型（识别后填入；未识别时服务端可能给空串，展示走 materialKindLabel 兜底）。
 *  image 为 V2-2（G18）新增：位图 OCR / SVG 文本。 */
export type MaterialKind = 'code' | 'ddl' | 'docx' | 'pdf' | 'xlsx' | 'md' | 'image' | 'zip' | 'other'
export type UploadState = 'open' | 'complete' | 'aborted'
export type ParseState = 'pending' | 'running' | 'success' | 'partial' | 'failed' | 'excluded'

/** 解析覆盖摘要（§1.2 coverage）。 */
export interface CoverageSummary {
  /** 识别到的业务模块 */
  modules: string[]
  /** 解析缺口/口径说明（页面「查看问题」按条列出） */
  notes: string[]
  /** 未能解析的内容片段（页数/章节/行段），不能当成空内容忽略 */
  failedSegments: string[]
  /** 纳入的证据片段（fact）数量 */
  factCount: number
  /** 默认排除的路径（协议补充项：并非每条材料都有；有则展示，无则不显示） */
  excludedPaths?: string[]
}

export interface Material {
  /** bm-uuid */
  id: string
  taskId: string
  /** 浏览器给出的相对路径（展示用；服务端已校验无 .. / 绝对路径） */
  relPath: string
  kind: MaterialKind
  /** 字节数 */
  size: number
  /** sha256 */
  contentHash: string
  uploadState: UploadState
  parseState: ParseState
  coverage: CoverageSummary
  /** 同源组（内容哈希前缀）：同源重复副本不算独立佐证 */
  sourceGroup: string
  /** 人工排除：排除后不再参与扫描与生成，并推进 materialRevision */
  excluded: boolean
  /** 失败原因（可读文案），无失败为 '' */
  error: string
  /** 材料级修订 token */
  revision: string
}

// ── §1.4 Scope ─────────────────────────────────────────────────────────────
export interface ScopeOpenQuestion {
  text: string
  /** 是否阻断确认（阻断项未处理时服务端 422） */
  blocking: boolean
}

/** 建模范围（范围对话产物；A01/A02 只读取，不修改）。 */
export interface ScopeModel {
  goal: string
  include: string
  exclude: string
  /** 必要关联与深度 */
  relations: string
  /** 材料覆盖与冲突说明 */
  coverage: string
  openQuestions: ScopeOpenQuestion[]
  confirmed: boolean
  /** 范围修订号（整数） */
  revision: number
  confirmedAt: string
  /** 冻结基线用的模型指纹（providerId/model，不含密钥） */
  providerFingerprint: string
}

// ── §1.5 Run ───────────────────────────────────────────────────────────────
export type RunKind = 'scan' | 'dialog' | 'generate'
export type RunState = 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled' | 'interrupted'
/** scan: retrieve/align；generate: retrieve/align/abstract/verify/adapt。 */
export type RunStage = 'retrieve' | 'align' | 'abstract' | 'verify' | 'adapt'

export interface RunProgress { done: number; total: number }
export interface RunUsage { calls: number; promptBytes: number; completionBytes: number; durationMs: number }
export interface RunError { message: string; retryable: boolean }
export interface RunBaseline {
  materialRevision: number
  materialHashes: string[]
  scopeRevision: number
  providerFingerprint: string
  promptVersion: string
  parserVersion: string
}

export interface BuildRun {
  /** br-uuid */
  id: string
  taskId: string
  kind: RunKind
  state: RunState
  stage: RunStage | ''
  /** 服务端给出的中文阶段文案 */
  stageLabel: string
  progress: RunProgress
  baseline: RunBaseline
  attempt: number
  /** 失败信息：服务端可能返回 {message,retryable} 或纯文案，展示统一走 runErrorText() */
  error: RunError | string | null
  usage: RunUsage
  batchId: string
  createdAt: string
  updatedAt: string
}

// ── §1.8 Delivery ──────────────────────────────────────────────────────────
export interface BuildDelivery {
  id: string
  taskId: string
  requestId: string
  payloadDigest: string
  ontologyId: string
  createdAt: string
}

// ── §2.1 能力与限额 ────────────────────────────────────────────────────────
export interface BuildLimits {
  chunkBytes: number
  fileBytes: number
  taskBytes: number
  zipExpandedBytes: number
  zipMaxEntries: number
  parseTimeoutSeconds: number
}
export interface OcrCapability { available: boolean; reason: string }
export interface BuildProviderRef { id: string; name: string; model: string }

/** 08 §13：能力接口公开的黑名单枚举（硬层完整；软层为默认值，任务可覆盖）。 */
export interface BlacklistInfo {
  hard: { exts: string[]; dirs: string[]; note: string }
  softDefaults: string[]
}

export interface BuildCapabilities {
  limits: BuildLimits
  ocr: OcrCapability
  /** 为 null 时范围对话/生成不可启动（422），页面必须引导先去配置模型 */
  provider: BuildProviderRef | null
  /** 黑名单三层枚举（08 §13；2026-09-21 新增） */
  blacklist?: BlacklistInfo
  parserVersion: string
  promptVersion: string
}

// ── 中文标签表（只做展示映射，不参与请求） ─────────────────────────────────
// ── §1.3 / §1.6 / §1.7（候选评审页使用：类型 + 中文标签，A01/A02 不直接消费） ──
/** 候选定义类型。 */
export type CandidateType = 'object' | 'property' | 'link' | 'rule' | 'action'
/** 证据状态：有依据 / 推断待确认 / 来源冲突 / 材料不足。 */
export type EvidenceStatus = 'supported' | 'inferred' | 'conflict' | 'insufficient'
/** 人工决定：拟纳入 / 暂缓 / 已排除。 */
export type CandidateDecision = 'include' | 'defer' | 'exclude'
export type FactQuality = 'high' | 'medium' | 'low'

export interface CandidateConflictSide { factId: string; value: unknown }
export interface CandidateConflict { field: string; sides: CandidateConflictSide[]; note: string }
export interface CandidateIssue { code: string; message: string; field: string }
export interface CandidateOrigin { batch: string; key: string; mergedFrom: string[]; mergedInto: string | null }

export interface Candidate {
  /** bc-uuid，服务端稳定 ID */
  id: string
  taskId: string
  batchId: string
  type: CandidateType
  /** 模型临时键（仅批内有效） */
  key: string
  name: string
  definition: string
  /** 按类型：property→dataType，link→sourceRef/targetRef/cardinality，rule→content，action→effect */
  fields: Record<string, unknown>
  /** property 的所属对象 key */
  ownerKey: string
  /** 证据字段名 → factId 列表（含 '_record'） */
  evidence: Record<string, string[]>
  evidenceStatus: EvidenceStatus
  conflicts: CandidateConflict[]
  decision: CandidateDecision
  reviewed: boolean
  reason: string
  issues: CandidateIssue[]
  origin: CandidateOrigin
  /** 候选级修订 token（r-uuid） */
  revision: string
  /** 新批次重新提出且此前已排除（默认 defer，需人工处理） */
  revived?: boolean
}

// ── 中文标签表（只做展示映射，不参与请求） ─────────────────────────────────
export const TYPE_LABELS: Record<CandidateType, string> = {
  object: '对象', property: '属性', link: '链接', rule: '规则', action: '动作',
}
export const DECISION_LABELS: Record<CandidateDecision, string> = {
  include: '拟纳入', defer: '暂缓', exclude: '已排除',
}
export const EVIDENCE_STATUS_LABELS: Record<EvidenceStatus, string> = {
  supported: '有依据', inferred: '推断待确认', conflict: '来源冲突', insufficient: '材料不足',
}

export const TASK_STATUS_LABELS: Record<TaskStatus, string> = {
  draft: '未开始',
  materials: '添加物料',
  scope: '确定范围',
  generating: '生成初稿',
  review: '待评审',
  delivered: '已创建本体',
}

export const PARSE_STATE_LABELS: Record<ParseState, string> = {
  pending: '待扫描',
  running: '解析中',
  success: '已解析',
  partial: '部分解析',
  failed: '解析失败',
  excluded: '已排除',
}

export const KIND_LABELS: Record<MaterialKind, string> = {
  code: '源代码',
  ddl: '数据库表结构',
  docx: 'Word 文档',
  pdf: 'PDF',
  xlsx: 'Excel',
  md: 'Markdown',
  image: '图片（OCR）',
  zip: '压缩包',
  other: '其他',
}

/** 阶段中文名（scan 只用 retrieve/align，generate 用全部五项）。 */
export const STAGE_LABELS: Record<RunStage, string> = {
  retrieve: '检索相关材料',
  align: '对齐跨来源证据',
  abstract: '抽象本体定义',
  verify: '检查冲突与引用',
  adapt: '适配工作台格式',
}

export const RUN_STATE_LABELS: Record<RunState, string> = {
  queued: '排队中',
  running: '进行中',
  succeeded: '已完成',
  failed: '失败',
  cancelled: '已取消',
  interrupted: '已中断',
}

export const UPLOAD_STATE_LABELS: Record<UploadState, string> = {
  open: '上传中',
  complete: '已上传',
  aborted: '已取消',
}

/** 各材料类型在解析事实里使用的定位方式（原型「定位方式」列的正式来源）。 */
export const KIND_LOCATOR_LABELS: Record<MaterialKind, string> = {
  code: '类 / 方法 / SQL 映射',
  ddl: '表 / 字段 / 约束',
  docx: '章节 / 段落 / 表格',
  pdf: '页码 / 原文区域',
  xlsx: '工作表 / 单元格',
  md: '标题路径 / 行号',
  image: '整图 / 图片区域',
  zip: '压缩包内路径',
  other: '待识别',
}

/** 徽章色调：ok=绿、warn=橙、bad=红、info=蓝（页面按自己的 scoped 样式渲染）。 */
export type Tone = 'ok' | 'warn' | 'bad' | 'info'
export const TASK_STATUS_TONE: Record<TaskStatus, Tone> = {
  draft: 'info', materials: 'info', scope: 'info', generating: 'info', review: 'warn', delivered: 'ok',
}
export const PARSE_STATE_TONE: Record<ParseState, Tone> = {
  pending: 'info', running: 'info', success: 'ok', partial: 'warn', failed: 'bad', excluded: 'info',
}
export const RUN_STATE_TONE: Record<RunState, Tone> = {
  queued: 'info', running: 'info', succeeded: 'ok', failed: 'bad', cancelled: 'warn', interrupted: 'warn',
}

// ── 纯展示辅助 ────────────────────────────────────────────────────────────
/** 查标签表：键可能是服务端新值/空串，一律兜底，绝不显示 undefined。 */
export function labelOf<T extends string>(table: Record<T, string>, key: string, fallback = '未知'): string {
  const hit = (table as unknown as Record<string, string>)[key]
  return hit || fallback
}
export const taskStatusLabel = (t: BuildTask): string => t.stageLabel || labelOf(TASK_STATUS_LABELS, t.status, '未开始')
export const materialKindLabel = (kind: string): string => labelOf(KIND_LABELS, kind, '待识别')
export const parseStateLabel = (state: string): string => labelOf(PARSE_STATE_LABELS, state, '未知状态')
export const stageLabel = (stage: string): string => labelOf(STAGE_LABELS, stage, '')
export const runStateLabel = (state: string): string => labelOf(RUN_STATE_LABELS, state, '未知状态')
export const toneOf = <T extends string>(table: Record<T, Tone>, key: string, fallback: Tone = 'info'): Tone =>
  (table as unknown as Record<string, Tone>)[key] || fallback

/** 材料覆盖摘要兜底：老数据/未解析材料可能没有 coverage 字段，页面不做空指针访问。 */
export function coverageOf(m: Material): CoverageSummary {
  const c = m.coverage
  return {
    modules: Array.isArray(c?.modules) ? c.modules : [],
    notes: Array.isArray(c?.notes) ? c.notes : [],
    failedSegments: Array.isArray(c?.failedSegments) ? c.failedSegments : [],
    factCount: typeof c?.factCount === 'number' ? c.factCount : 0,
    excludedPaths: Array.isArray(c?.excludedPaths) ? c.excludedPaths : [],
  }
}

/** 运行失败文案：兼容 {message,retryable} 与纯字符串两种返回形态。 */
export function runErrorText(run: BuildRun | null): string {
  const e = run?.error
  if (!e) return ''
  if (typeof e === 'string') return e
  return e.message || ''
}
/** 运行失败是否可重试（服务端判定；缺省 true，允许页面继续尝试）。 */
export function runErrorRetryable(run: BuildRun | null): boolean {
  const e = run?.error
  if (!e) return false
  return typeof e === 'string' ? true : e.retryable !== false
}

export function formatBytes(size: number): string {
  if (!Number.isFinite(size) || size < 0) return '—'
  const units = ['B', 'KB', 'MB', 'GB']
  let value = size, unit = 0
  while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit += 1 }
  const text = unit === 0 ? String(Math.round(value)) : value >= 100 ? value.toFixed(0) : value.toFixed(1)
  return text + ' ' + units[unit]
}

/** ISO 时间 → 本地 YYYY-MM-DD HH:mm（不可解析时原样回显，便于排查）。 */
export function formatTime(value: string): string {
  if (!value) return '—'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return value
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

// ── §6 范围对话 ───────────────────────────────────────────────────────────
export type MessageRole = 'user' | 'assistant'

/** 助手回复里的范围补丁（建议，不自动覆盖人工输入）。 */
export interface ScopePatch {
  goal?: string
  include?: string
  exclude?: string
  relations?: string
  coverage?: string
}

export interface ScopeMessage {
  id: string
  seq: number
  role: MessageRole
  content: string
  /** 助手给出的范围建议；为空对象表示无建议 */
  patch: ScopePatch | Record<string, never>
  /** 助手调用失败时的可读文案（用户消息恒为空） */
  error: string
  /** 生成该消息时的范围基线修订（用于识别晚到响应） */
  scopeRevision: number
  createdAt: string
}

export interface ScopeSaveResult {
  scope: ScopeModel
}

// ── §7 候选评审 ───────────────────────────────────────────────────────────
export interface CandidateListCounts {
  byType: Record<string, number>
  byDecision: Record<string, number>
  byStatus: Record<string, number>
}

export interface CandidateListResult {
  items: Candidate[]
  total: number
  counts: CandidateListCounts
  batch?: unknown
  stale: boolean
}

/** 证据项：locator 来自真实解析；missing=true 表示该 factId 在当前任务里已不存在。 */
export interface EvidenceItem {
  factId: string
  locator: Record<string, unknown>
  snippet: string
  quality: string
  materialRelPath: string
  missing?: boolean
}

export interface EvidenceGroup {
  field: string
  items: EvidenceItem[]
}

export interface CandidateDetail {
  candidate: Candidate
  evidence: EvidenceGroup[]
}

export interface MergeFieldDiff {
  name: string
  primaryValue: unknown
  mergeValues: unknown[]
  diff: boolean
}

export interface MergePreview {
  fields: MergeFieldDiff[]
  evidenceCount: number
  warnings: string[]
}

export interface MergeResult {
  candidate: Candidate
  opId: string
}

/** 再生成差异：与旧批次按 alignedKey 对齐后的分桶结果。 */
export interface DiffChangedItem {
  id: string
  fieldsChanged: string[]
  manualConflict: boolean
}

export interface CandidateDiff {
  against: string | null
  buckets: {
    added: string[]
    changed: DiffChangedItem[]
    removed: string[]
    kept: string[]
    excludedProtected: string[]
  }
  items: Candidate[]
}

// ── §8 交付 ───────────────────────────────────────────────────────────────
export interface DeliveryIssue {
  code: string
  message: string
  candidateId?: string
}

export interface DeliveryPrecheck {
  ok: boolean
  counts: Record<string, number>
  issues: DeliveryIssue[]
  excluded: number
  deferred: number
  coverage: Record<string, unknown>
  /** 绑定当前选定集合与批次的令牌；用户修改后失效 */
  checkToken: string
}

export interface DeliveryResult {
  ontologyId: string
  taskId: string
  deliveredAt: string
}

// ── 08 §12.1 物料分组（顶层目录汇总） ─────────────────────────────────────
export interface MaterialGroup {
  /** 顶层目录名；空串 = 根目录文件（页面显示「(根目录)」） */
  folder: string
  total: number
  byParseState: Record<string, number>
  bytes: number
}

// ── 08 §13 黑名单三层：过滤事件与报告 ─────────────────────────────────────
/** 被过滤文件事件（layer：hard=安全边界 / soft=默认软名单 / custom=任务追加）。 */
export interface FilterEvent {
  path: string
  layer: 'hard' | 'soft' | 'custom'
  rule: string
  size: number
}

export interface FilterReport {
  items: FilterEvent[]
  counts: { hard: number; soft: number; custom: number; total: number }
  truncated: boolean
}

/** GET /api/build-materials?view=filter 响应（08 §13.4）。 */
export interface MaterialFilterView {
  filter: TaskFilterSpec
  softDefaults: string[]
  report: FilterReport
  revision: number
}
