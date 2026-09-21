// 表单辅助填写（AI 建议）前后端协议镜像 —— T0 冻结（2026-09-21）。
// 契约唯一来源：文档/接口文档/04-编排与LLM接口.md §5；后端注册表 workbench/assist_fields.py。
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

/** 补充问题的结构化回答（按 questionId 分别传输） */
export interface AssistAnswer {
  value?: string
  unsure?: boolean
}
