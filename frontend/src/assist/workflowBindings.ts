// 业务规则（O4）与动作定义（O5）编辑表单的宿主适配（2026-09-21 T7）——AssistHostBinding 工厂，
// 供 BusinessRuleLibrary.vue / ActionLibrary.vue 与组件级测试共用。写法镜像 ./ontologyBindings.ts（T5）。
// 契约唯一来源：./useAssistPanel.ts 的 AssistHostBinding；字段白名单与后端 workbench/assist_fields.py
// 一致（rule: name/description/content；action: name/description/effect）。前端不维护第二份场景
// 注册表——可编辑字段以后端 context 响应的 editableFields 为准，这里只做「草稿 ↔ 白名单快照」的
// 形状适配。规则历史 output 等白名单外字段不进快照、不受采纳影响（只读区由宿主单独承载）。
// 保存边界：apply 只把建议值合并进宿主本地草稿（面板提示「已填入，尚未保存」），绝不调用
// form-save/commit-now/touch；持久化永远由用户在表单上走既有保存链路（含校验与撤销记录）。
import type { AssistHostBinding } from './useAssistPanel'

/** 规则编辑表单草稿形状（BusinessRuleLibrary draft，2026-09-20 三字段精简后） */
export type RuleAssistDraft = { name: string; description: string; content: string }
/** 动作编辑表单草稿形状（ActionLibrary draft，definitionVersion 2 三字段） */
export type ActionAssistDraft = { name: string; description: string; effect: string }

export interface WorkflowAssistOptions {
  /** 本体区必填：当前本体工作区 id */
  ontologyId?: string
  /** 编辑目标稳定 id（规则/动作 id）；新建尚未入库时传 ''（动作新建已预生成 id 则直接用之） */
  targetId?: string
  /** 未取到上下文时的兜底标题（取到后面板改用 context.title） */
  contextTitle?: string
}

const RULE_KEYS: readonly string[] = ['name', 'description', 'content']
const ACTION_KEYS: readonly string[] = ['name', 'description', 'effect']

/** 白名单外键一律忽略；undefined 值不写入（不覆盖既有草稿）；其余值按建议原样合并。 */
function mergeWhitelist(draft: Record<string, unknown>, values: Record<string, unknown> | null | undefined, keys: readonly string[]): void {
  if (!values) return
  for (const [k, v] of Object.entries(values)) {
    if (!keys.includes(k) || v === undefined) continue
    draft[k] = v
  }
}

/** 就地恢复快照：宿主持有同一草稿对象引用（draft ref 经 computed 暴露），整体替换会断开绑定。 */
function replaceInPlace(draft: Record<string, unknown>, snap: unknown): void {
  const src = snap && typeof snap === 'object' ? snap as Record<string, unknown> : {}
  for (const k of Object.keys(draft)) delete draft[k]
  Object.assign(draft, src)
}

/** 业务规则编辑表单的宿主适配：draft 参数是编辑表单实时草稿对象（引用须保持稳定）。 */
export function ruleAssistBinding(editorDraft: RuleAssistDraft | null | undefined, options: WorkflowAssistOptions = {}): AssistHostBinding {
  return {
    space: 'ontology',
    ontologyId: options.ontologyId || '',
    targetKind: 'rule',
    targetId: options.targetId || '',
    contextTitle: options.contextTitle || '业务规则辅助填写',
    draft: () => ({ name: editorDraft?.name ?? '', description: editorDraft?.description ?? '', content: editorDraft?.content ?? '' }),
    apply: values => { if (editorDraft) mergeWhitelist(editorDraft, values, RULE_KEYS) },
    snapshot: () => JSON.parse(JSON.stringify(editorDraft ?? {})),
    restore: snap => { if (editorDraft) replaceInPlace(editorDraft, snap) },
  }
}

/** 动作定义编辑表单的宿主适配（新建动作已预生成 id 时 targetId 传该 id）。 */
export function actionAssistBinding(editorDraft: ActionAssistDraft | null | undefined, options: WorkflowAssistOptions = {}): AssistHostBinding {
  return {
    space: 'ontology',
    ontologyId: options.ontologyId || '',
    targetKind: 'action',
    targetId: options.targetId || '',
    contextTitle: options.contextTitle || '动作定义辅助填写',
    draft: () => ({ name: editorDraft?.name ?? '', description: editorDraft?.description ?? '', effect: editorDraft?.effect ?? '' }),
    apply: values => { if (editorDraft) mergeWhitelist(editorDraft, values, ACTION_KEYS) },
    snapshot: () => JSON.parse(JSON.stringify(editorDraft ?? {})),
    restore: snap => { if (editorDraft) replaceInPlace(editorDraft, snap) },
  }
}
