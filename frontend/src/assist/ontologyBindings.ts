// 本体区辅助填写宿主适配（2026-09-21 T5）——对象定义（O1）与业务链接（O3）两个编辑表单的
// AssistHostBinding 工厂，供 ObjectWorkspace.vue 与组件级测试共用。
// 契约唯一来源：./useAssistPanel.ts 的 AssistHostBinding；字段白名单与后端 workbench/assist_fields.py
// 一致（object: label/comment；link: label/from/to/cardinality/reverseLabel/comment）。前端不维护
// 第二份场景注册表——可编辑字段以后端 context 响应的 editableFields 为准，这里只做「草稿 ↔ 白名单
// 快照」的形状适配。
// 保存边界：apply 只把建议值合并进宿主本地草稿（面板提示「已填入，尚未保存」），绝不调用
// form-save/commit-now/touch；持久化永远由用户在表单上走既有保存链路（含校验与撤销记录）。
import type { AssistHostBinding } from './useAssistPanel'

/** 对象编辑器草稿形状（ObjectWorkspace editor.draft，kind='object'） */
export type ObjectAssistDraft = { label: string; comment: string }
/** 链接编辑器草稿形状（ObjectWorkspace LinkDraft） */
export type LinkAssistDraft = { label: string; from: string; to: string; cardinality: string; reverseLabel: string; comment: string }

export interface OntologyAssistOptions {
  /** 本体区必填：当前本体工作区 id */
  ontologyId?: string
  /** 编辑目标稳定 id（对象/链接 @id）；新建尚未保存时传 '' */
  targetId?: string
  /** 未取到上下文时的兜底标题（取到后面板改用 context.title） */
  contextTitle?: string
}

const OBJECT_KEYS: readonly string[] = ['label', 'comment']
const LINK_KEYS: readonly string[] = ['label', 'from', 'to', 'cardinality', 'reverseLabel', 'comment']

/** 白名单外键一律忽略；undefined 值不写入（不覆盖既有草稿）；其余值按建议原样合并。 */
function mergeWhitelist(draft: Record<string, unknown>, values: Record<string, unknown> | null | undefined, keys: readonly string[]): void {
  if (!values) return
  for (const [k, v] of Object.entries(values)) {
    if (!keys.includes(k) || v === undefined) continue
    draft[k] = v
  }
}

/** 就地恢复快照：宿主持有同一草稿对象引用（editor.draft 经 computed 暴露），整体替换会断开绑定。 */
function replaceInPlace(draft: Record<string, unknown>, snap: unknown): void {
  const src = snap && typeof snap === 'object' ? snap as Record<string, unknown> : {}
  for (const k of Object.keys(draft)) delete draft[k]
  Object.assign(draft, src)
}

/** 对象定义编辑表单的宿主适配：draft 参数是编辑器实时草稿对象（引用须保持稳定）。 */
export function objectAssistBinding(editorDraft: ObjectAssistDraft | null | undefined, options: OntologyAssistOptions = {}): AssistHostBinding {
  return {
    space: 'ontology',
    ontologyId: options.ontologyId || '',
    targetKind: 'object',
    targetId: options.targetId || '',
    contextTitle: options.contextTitle || '对象定义辅助填写',
    draft: () => ({ label: editorDraft?.label ?? '', comment: editorDraft?.comment ?? '' }),
    apply: values => { if (editorDraft) mergeWhitelist(editorDraft, values, OBJECT_KEYS) },
    snapshot: () => JSON.parse(JSON.stringify(editorDraft ?? {})),
    restore: snap => { if (editorDraft) replaceInPlace(editorDraft, snap) },
  }
}

/** 业务链接编辑表单的宿主适配（含起点/终点对象引用与数量关系）。 */
export function linkAssistBinding(linkDraft: LinkAssistDraft | null | undefined, options: OntologyAssistOptions = {}): AssistHostBinding {
  return {
    space: 'ontology',
    ontologyId: options.ontologyId || '',
    targetKind: 'link',
    targetId: options.targetId || '',
    contextTitle: options.contextTitle || '业务链接辅助填写',
    draft: () => ({
      label: linkDraft?.label ?? '', from: linkDraft?.from ?? '', to: linkDraft?.to ?? '',
      cardinality: linkDraft?.cardinality ?? '', reverseLabel: linkDraft?.reverseLabel ?? '', comment: linkDraft?.comment ?? '',
    }),
    apply: values => { if (linkDraft) mergeWhitelist(linkDraft, values, LINK_KEYS) },
    snapshot: () => JSON.parse(JSON.stringify(linkDraft ?? {})),
    restore: snap => { if (linkDraft) replaceInPlace(linkDraft, snap) },
  }
}
