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
 *  image 为 V2-2（G18）新增：位图 OCR / SVG 文本。
 *  json/yaml/properties/csv/ini/toml 为结构化格式解析支持（需求说明_结构化格式解析支持_v1 §2/§5）新增的
 *  六个专用解析器 kind；服务端可能给表外新值，展示一律走 materialKindLabel 兜底。 */
export type MaterialKind =
  | 'code' | 'ddl' | 'docx' | 'pdf' | 'xlsx' | 'md' | 'image' | 'zip' | 'other'
  | 'json' | 'yaml' | 'properties' | 'csv' | 'ini' | 'toml'
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

export interface RunProgress {
  done: number
  total: number
  /** 批次并发等待心跳（仅抽取阶段等待期间出现）：当前等待批号 / 未返回批数 / 已等待秒数 */
  waitingPosition?: number
  waitingCount?: number
  waitedSeconds?: number
}
/** 08 §14.3（生成控输出 v3）：token 统计为新增可空项——任一次调用 usage 未知则整体为 null
 *  （不拿局部和冒充总数）；旧运行/未接线后端可能整组缺省这些字段（可选性即为此容忍，
 *  字段名与「可空性」与 08 逐字一致）。completionTokens 已含推理输出（OpenAI 兼容语义），
 *  展示绝不与 reasoningTokens 双加。 */
export interface RunUsage {
  calls: number
  promptBytes: number
  completionBytes: number
  durationMs: number
  promptTokens?: number | null
  completionTokens?: number | null
  totalTokens?: number | null
  /** 已知完成 token 部分和（未知调用不计 0） */
  knownCompletionTokens?: number
  /** 用量未知的调用次数（外部计费可能已发生） */
  unknownUsageCalls?: number
  /** 统计范围：schema2 计划代次；范围外调用/旧数据为 null */
  planEpoch?: number | null
}
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
  /** V2-8（08 §1.5）：生成检查点摘要——失败入口「第 N 步失败（原因）[从该步重试]」的数据来源 */
  checkpoint?: RunCheckpoint | null
  createdAt: string
  updatedAt: string
}

// ── 08 §14.3（生成控输出 v3）：checkpoint.generate 双版本（schema1 旧批次 / schema2 目标计划） ──
/** schema1（旧批次计划，08 §1.5 原形状）——既有字段逐字保留，永不改动。 */
export interface GenerateCheckpointSchema1 {
  batchId: string
  scopeRevision: number
  materialRevision: number
  /** 确定性阶段（筛选/对齐）产物是否已持久化（重试复用、不重算） */
  planPersisted: boolean
  modelFacts: number
  relevant: number
  related: number
  excluded: number
  batches: {
    size: number
    total: number
    done: number[]
    failed: { position: number; error: string }[]
  }
  /** 生成进度实时可观测日志（08 §1.5；旧运行可缺省，前端按缺失=空处理）。 */
  log?: string[]
  /** 模型说明/管线注记（旧数据可缺省）。 */
  notes?: string[]
}

/** 输出预算三元组（08 §14.3）：C=上下文限制、L=生效单次输出请求上限、T=单次输出规划目标；未配置为 null。 */
export interface GenerateBudgetView {
  contextLimit: number | null
  requestOutputCap: number | null
  targetOutputBudget: number | null
}

/** 作业状态计数（08 §14.3）：splitParents=因输出超限拆分出的父作业数（不算失败，也不算成功叶）。 */
export interface GenerateJobsSummary {
  queued: number
  running: number
  succeeded: number
  failed: number
  blocked: number
  splitParents: number
}

/** 目标覆盖计数（08 §14.3）：分母按语义目标解释，不按旧批号。 */
export interface GenerateCoverage {
  targetTotal: number
  targetCompleted: number
  targetPending: number
}

/** 计划受阻原因（08 §14.2/§14.3）：已接受作业后异步发现超限 → Run.failed + blocking（不伪造 422）。 */
export interface GenerateBlocking {
  code: string
  message: string
}

/** schema2（目标/作业/尝试计划）摘要——与 08 §14.3 JSON 逐字段一致；
 *  不再输出旧 batches/planPersisted/modelFacts，batchId/scopeRevision/materialRevision/log/notes 保留。 */
export interface GenerateCheckpointV2 {
  batchId: string
  scopeRevision: number
  materialRevision: number
  schemaVersion: 2
  adaptive: true
  planId: string
  planEpoch: number
  jobs: GenerateJobsSummary
  coverage: GenerateCoverage
  /** 输入 token 估算方式（utf8_proxy=UTF-8 字节代理，非精确 tokenizer） */
  estimateKind: string
  budget: GenerateBudgetView
  blocking: GenerateBlocking | null
  log?: string[]
  notes?: string[]
}

/** 轮询里 schema2 分支的实际形态（判别联合 v2 臂；schemaVersion 恒为 2）。 */
export type GenerateCheckpointV2View = { schemaVersion: 2 } & GenerateCheckpointV2

/** 检查点摘要（V2-8；不含 factId 明细）：generate 按 schemaVersion 双版本——
 *  schema1 旧批次计划 / schema2 目标计划（08 §14.3，前端按 isSchemaV2 分支显示）；
 *  scan 携带本轮兜底消耗（G19 单任务累计限额的对账来源，08 §4.3）。 */
export interface RunCheckpoint {
  generate?: GenerateCheckpointSchema1 | GenerateCheckpointV2View
  scan?: {
    materials: number
    /** 本轮自身兜底消耗（跨运行求和 = 单任务累计，08 §4.3） */
    fallbackFiles: number
    fallbackBytes: number
  }
}

/** 类型守卫：checkpoint.generate 是否为 schema2（目标/作业计划）摘要；非 v2（含空值）一律 false。 */
export function isSchemaV2(
  generate: RunCheckpoint['generate'] | null,
): generate is GenerateCheckpointV2View {
  return (generate as { schemaVersion?: unknown } | null | undefined)?.schemaVersion === 2
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
  /** V2-10（G25）：扫描解析并发度（默认 min(8, CPU)，env 可覆盖） */
  parseConcurrency: number
}
export interface OcrCapability { available: boolean; reason: string }
export interface BuildProviderRef { id: string; name: string; model: string }

/** 08 §13：能力接口公开的黑名单枚举（硬层完整；软层为默认值，任务可覆盖）。 */
export interface BlacklistInfo {
  hard: { exts: string[]; dirs: string[]; note: string }
  softDefaults: string[]
}

/** 解析器支持矩阵条目（08 §2.1 `parserMatrix`；需求 §5 的矩阵即本结构：
 *  支持项（后缀/解析层/定位粒度/说明）与排除项（硬黑名单 .env*、软黑名单二进制格式）同列表呈现）。 */
export interface ParserMatrixItem {
  /** 适用后缀（含点，如 ['.json', '.jsonld']；页面以「、」拼接展示） */
  exts: string[]
  /** 支持方式 / 说明（如「专用（json）」「硬黑名单排除」） */
  label: string
  /** 定位粒度（如「JSON 路径 / 节点 @id」；排除项为「—」） */
  locator: string
  /** 备注（采样/降级等口径说明，可为空串） */
  note: string
}

export interface BuildCapabilities {
  limits: BuildLimits
  ocr: OcrCapability
  /** 为 null 时范围对话/生成不可启动（422），页面必须引导先去配置模型 */
  provider: BuildProviderRef | null
  /** 黑名单三层枚举（08 §13；2026-09-21 新增） */
  blacklist?: BlacklistInfo
  /** 解析器支持矩阵（08 §2.1 / 需求 §5；**可选**：未接线的旧后端不下发，页面整块隐藏） */
  parserMatrix?: ParserMatrixItem[]
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
  // 结构化格式（需求 §5 矩阵与标签表同步项）
  json: 'JSON / JSON-LD',
  yaml: 'YAML',
  properties: 'Properties',
  csv: 'CSV / TSV',
  ini: 'INI / CONF',
  toml: 'TOML',
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
  // 结构化格式定位粒度（需求 §5 矩阵「定位粒度」列）
  json: 'JSON 路径 / 节点 @id',
  yaml: '键路径',
  properties: '行号',
  csv: '行号 + 列统计',
  ini: '节 + 键路径',
  toml: '键路径',
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

/** token 计数展示：千分位分组；空值语义由调用方表达（如「未配置」「含未知」），此处只收数字。 */
export function formatTokenCount(value: number): string {
  if (!Number.isFinite(value)) return String(value)
  return value.toLocaleString('zh-CN')
}

/** 08 §14.3 estimateKind 中文标注：估算方式必须显式标明非精确；未知值原样回显并注明，不冒充精确值。 */
export function estimateKindLabel(kind: string): string {
  if (kind === 'utf8_proxy') return 'UTF-8 字节代理（非精确 tokenizer）'
  if (kind === 'tokenizer') return '本地 tokenizer（精确）'
  return kind ? `${kind}（未知估算方式）` : '未知估算方式'
}

/** 08 §14.2/§14.3：blocking.code → 中文处置指引。
 *  配置问题指到具体配置项；计划/预算耗尽说明「需新建计划」；未知代码给保守通用指引——
 *  不把「重试」当作所有情况的万能入口。 */
export function blockingAdviceText(code: string): string {
  if (code === 'BUDGET_PROFILE_REQUIRED' || code === 'BUDGET_PROFILE_MISMATCH' || code === 'BUDGET_CONFIG_INVALID') {
    return '属生成控输出配置问题：请先在服务端环境配置中核对上下文限制（WIZ_BUILD_CONTEXT_TOKENS）、'
      + '已核实输出上限（WIZ_BUILD_OUTPUT_LIMIT_TOKENS）与模型绑定（WIZ_BUILD_PROFILE_PROVIDER_ID / WIZ_BUILD_PROFILE_MODEL），'
      + '修正后重新发起生成。'
  }
  if (code === 'BUDGET_PLAN_TOO_LARGE' || code === 'BUDGET_PLAN_MISMATCH' || code === 'UNKNOWN_CHECKPOINT_SCHEMA') {
    return '当前生成计划无法继续复用：需新建生成计划（返回重新发起生成）；已成功目标的候选会保留。'
  }
  if (
    code === 'TARGET_BUDGET_EXCEEDED' || code === 'JOB_BUDGET_EXCEEDED' || code === 'ATTEMPT_BUDGET_EXCEEDED'
    || code === 'WALL_TIME_BUDGET_EXCEEDED' || code === 'CHECKPOINT_BUDGET_EXCEEDED' || code === 'SPLIT_DEPTH_EXCEEDED'
    || code === 'FINAL_CANDIDATES_EXCEEDED' || code === 'JOB_ATTEMPTS_EXHAUSTED' || code === 'OVERSIZED_ATOMIC_TARGET'
  ) {
    return '生成预算或规模上限已耗尽：需新建生成计划才能继续；已成功目标的候选保留，不会被丢弃。'
  }
  return '请先按上方原因处理（配置问题先修正配置；计划预算耗尽则新建生成计划），再重新发起生成。'
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
