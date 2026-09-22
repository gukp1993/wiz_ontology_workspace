// 表单辅助填写（AI 建议）前后端协议镜像 —— T0 冻结（2026-09-21；2026-09-22 扩展 autofill/1）。
// 契约唯一来源：文档/接口文档/04-编排与LLM接口.md §5（check/explain 旧协议）与 §6（autofill/1 整表
// 自动填写）；后端注册表 workbench/assist_fields.py。
// 本文件只放类型与常量，不放逻辑；字段白名单以后端 context 响应的 editableFields 为准，
// 前端不维护第二份场景注册表。

export type AssistSpace = 'ontology' | 'project'
export type AssistMode = 'fill' | 'check' | 'explain'
export type AssistTargetKind =
  | 'object' | 'property' | 'sharedProperty' | 'link' | 'rule' | 'action'
  | 'identity' | 'propertySource' | 'linkMapping' | 'actionBinding'

/** 字段种类（与后端 assist_fields.py 一致） */
export type AssistFieldKind = 'text' | 'textarea' | 'select' | 'url' | 'ref' | 'composite'

/** context 响应中的可编辑字段说明 */
export interface AssistFieldSpec {
  key: string
  label: string
  kind: AssistFieldKind
  required: boolean
  options: string[] | null
  group: string | null
  help: string
}

/** context 响应中经裁剪的只读参考信息 */
export interface AssistContextInfo {
  targetKind: AssistTargetKind
  title: string
  editableFields: AssistFieldSpec[]
  /** 本体：对象/属性/链接摘要；项目：目录、编排签名、来源、属性摘要等（已按权限与场景裁剪） */
  definitions: { kind: string; id: string; label: string; hint?: string }[]
  catalog: { connection: string; table: string; fields: { name: string; comment?: string; dataType?: string }[] }[]
  flows: { id: string; name: string; inputs: { id: string; label: string; type: string }[]; outputs: { id: string; label: string; kind: string }[] }[]
  modelReady: boolean
}

/** assist-context 响应 */
export interface AssistContextResponse {
  contextToken: string
  contextFingerprint: string
  context: AssistContextInfo
}

/** 补充问题（分别作答，支持“暂不确定”） */
export interface AssistQuestion {
  id: string
  prompt: string
  kind: 'text' | 'choice'
  options: { value: string; label: string }[]
  allowUnsure: boolean
}

export type SuggestionState = 'ready' | 'pending' | 'blocked'

/** 单条建议：受限字段键（或原子组）+ 类型化建议值；不含执行语义 */
export interface AssistSuggestion {
  id: string
  label: string
  fieldKeys: string[]
  proposed: Record<string, unknown>
  state: SuggestionState
  reason?: string
  pendingReason?: string
  blockedReason?: string
  evidenceRefs?: string[]
}

/** 检查模式的问题条目（定位到字段键） */
export interface AssistIssue {
  fieldKey: string
  message: string
}

/** 解释模式（无 patch） */
export interface AssistExplanation {
  title: string
  body: string
}

/** assist-generate 响应（200：status ok | empty） */
export interface AssistGenerateResponse {
  requestId: string
  status: 'ok' | 'empty'
  contextFingerprint: string
  questions: AssistQuestion[]
  suggestions: AssistSuggestion[]
  issues: AssistIssue[]
  explanation: AssistExplanation | null
  meta: { durationMs: number; provider: string; model: string }
}

/** 补充问题的结构化回答（按 questionId 分别传输；旧协议 §5.2 键值形态） */
export interface AssistAnswer {
  value?: string
  unsure?: boolean
}

// ── 请求体（assist-context / assist-generate；§5.1 §5.2 与 §6.2 合并描述）────────

/** assist-context 请求体 */
export interface AssistContextRequest {
  space: AssistSpace
  projectId?: string
  ontologyId?: string
  targetKind: AssistTargetKind
  targetId: string
  purpose: AssistMode
  draft: Record<string, unknown>
}

/**
 * assist-generate 请求体。
 * - `mode=fill` 必须携带 `protocol: 2`（04 §6.0），`answers` 用数组形态（§6.2）；
 * - `mode=check|explain` 沿用 §5.2 旧结构，不带 protocol，`answers` 用键值形态。
 */
export interface AssistGenerateRequest {
  requestId: string
  contextToken: string
  mode: AssistMode
  draft: Record<string, unknown>
  intent?: string
  /** 答案：旧协议键值形态（check/explain）或 autofill/1 数组形态（fill），按 protocol 区分 */
  answers?: Record<string, AssistAnswer> | AutofillAnswersBody
  /** autofill/1：fill 请求协议版本常量（缺失或非 2 → 400） */
  protocol?: 2
  /** autofill/1：首轮不传（服务端签发）；续轮必带回传 */
  sessionId?: string
}
/** autofill/1 续轮答案（§6.2 数组形态，与 §5.2 键值形态共用 `answers` 键，按 protocol 区分） */
export type AutofillAnswersBody = AutofillAnswerInput[]

// ── autofill/1 整表自动填写（04 §6，2026-09-22 T0 冻结）────────────────────────

/** autofill/1 续轮答案条目（数组形态，§6.2） */
export interface AutofillAnswerInput {
  questionId: string
  value?: string
  unsure?: boolean
}

/** 操作依据（§6.3 basis）：模型必须给出原话片段或本会话问题 id，防自报授权 */
export interface AutofillOperationBasis {
  kind: 'intent' | 'question'
  quote?: string
  questionId?: string
}

/** 行规格（row.append）：localId 客户端本地生成，服务端按内容去重 */
export interface AutofillRowSpec {
  localId: string
  fields: Record<string, unknown>
}

/**
 * 受限字段操作（§6.3）。`field` 是契约内的点路径（如 `result.valueField`）；
 * rowId 必须命中草稿现有行；不提供任意 JSON 路径、脚本或持久 ID。
 */
export type AutofillOperation =
  | { op: 'set'; field: string; value: unknown; basis?: AutofillOperationBasis; atomicGroup?: string }
  | { op: 'clear'; field: string; basis?: AutofillOperationBasis }
  | { op: 'row.append'; field: string; row: AutofillRowSpec; basis?: AutofillOperationBasis }
  | { op: 'row.update'; field: string; rowId: string; fields: Record<string, unknown>; basis?: AutofillOperationBasis }
  | { op: 'row.remove'; field: string; rowId: string; basis?: AutofillOperationBasis }

/** autofill/1 补充问题（§6.3）：options 为受控取值字符串；无 options 时自由文本作答 */
export interface AutofillQuestion {
  id: string
  text: string
  fields: string[]
  options: string[]
  allowUnsure: boolean
}

/** 未填写项及原因（单操作违规转 unresolved 后随响应返回） */
export interface AutofillUnresolved {
  field: string
  reason: string
}

/** fill 响应的编辑目标回显（前端核对目标一致性） */
export interface AutofillTarget {
  space: AssistSpace
  targetKind: AssistTargetKind
  targetId: string
}

/** assist-generate mode=fill（protocol:2）响应，200：status ok | empty（§6.3） */
export interface AutofillFillResponse {
  protocol: 'autofill/1'
  status: 'ok' | 'empty'
  requestId: string
  formId: string
  schemaVersion: number
  schemaDigest: string
  target: AutofillTarget
  draftFingerprint: string
  contextFingerprint: string
  sessionId: string
  roundId: string
  operations: AutofillOperation[]
  questions: AutofillQuestion[]
  unresolved: AutofillUnresolved[]
  summary: string
  meta?: { durationMs: number; provider: string; model: string }
}
