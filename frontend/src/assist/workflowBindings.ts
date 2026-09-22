// 业务规则（O4）与动作定义（O5）编辑表单的宿主适配（2026-09-22 T5 改版）——AutofillHostBinding
// 工厂，供 BusinessRuleLibrary.vue / ActionLibrary.vue 与组件级测试共用。写法镜像 ./ontologyBindings.ts
// （T5），整轮摘要镜像复用其导出的 createRoundMirror。
// 契约唯一来源：contracts/forms/rule.json、action.json（前端生成物 ./formContracts.gen.ts）。
// 字段白名单与契约 fieldId 一致（rule: name/description/content；action: name/description/effect），
// draft() 即「契约形态标准化草稿」（恒等映射；全部是顶层普通字段，无 codec，引擎 identity 直写）。
// 规则历史 output 等白名单外字段不进快照、不受填写影响（只读区由宿主单独承载）。
// 保存边界：apply/applyDraft 只把合法操作写进宿主本地草稿，绝不调用 form-save/commit-now/touch；
// 持久化永远由用户在表单上走既有保存链路（含校验与撤销记录）。状态条镜像语义见 ontologyBindings.ts
// 头注（引擎是唯一权威，镜像只服务宿主状态条渲染与宿主撤销按钮）。
import { FORM_ACTION, FORM_RULE, type AssistFormId } from './formContracts.gen'
import { cloneJson, type AutofillFieldCodec } from './formAutofill'
import type { AutofillHostBinding } from './useAssistPanel'
import { createRoundMirror, type AssistRoundControl, type AssistRoundExpectation, type AssistRoundMirror } from './ontologyBindings'

export type { AssistRoundMirror }

/** 规则编辑表单草稿形状（BusinessRuleLibrary draft，2026-09-20 三字段精简后） */
export type RuleAssistDraft = { name: string; description: string; content: string }
/** 动作编辑表单草稿形状（ActionLibrary draft，definitionVersion 2 三字段） */
export type ActionAssistDraft = { name: string; description: string; effect: string }

export interface WorkflowAssistOptions {
  /** 本体区必填：当前本体工作区 id */
  ontologyId?: string
  /** 编辑目标稳定 id（规则/动作 id）；新建尚未入库时传 ''（动作新建已预生成 id 则按 DEF-02 仍传 ''） */
  targetId?: string
  /** 未取到上下文时的兜底标题（取到后抽屉改用 context.title） */
  contextTitle?: string
}

const RULE_KEYS: readonly string[] = ['name', 'description', 'content']
const ACTION_KEYS: readonly string[] = ['name', 'description', 'effect']

/** 宿主 binding 的新对接面（T5 改版）：成员与 OntologyAutofillBinding 相同。 */
export interface WorkflowAutofillBinding extends AutofillHostBinding {
  formId: AssistFormId
  codecs: Record<string, AutofillFieldCodec>
  round: AssistRoundMirror
  undoRound(): boolean
  noteManualChange(): void
  observeFill(resp: unknown): void
}

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

/** 引擎整稿落回的宿主实现：合并进既有草稿（不动白名单外的未知键，零丢失），并同步镜像。 */
function mergePlain(draft: Record<string, unknown>, next: Record<string, unknown>): void {
  Object.assign(draft, cloneJson(next))
}

/** 业务规则编辑表单的宿主适配：draft 参数是编辑表单实时草稿对象（引用须保持稳定）。 */
export function ruleAssistBinding(editorDraft: RuleAssistDraft | null | undefined, options: WorkflowAssistOptions = {}): WorkflowAutofillBinding {
  const control: AssistRoundControl = createRoundMirror(Object.fromEntries(FORM_RULE.fields.map(f => [f.id, f.label ?? f.id])))
  const expected: AssistRoundExpectation = {
    formId: 'rule', targetKind: 'rule', targetId: options.targetId || '',
    schemaVersion: FORM_RULE.schemaVersion, schemaDigest: FORM_RULE.schemaDigest,
  }
  return {
    space: 'ontology',
    ontologyId: options.ontologyId || '',
    targetKind: 'rule',
    targetId: options.targetId || '',
    contextTitle: options.contextTitle || '业务规则辅助填写',
    formId: 'rule',
    codecs: {},
    contractInfo: () => ({ schemaVersion: FORM_RULE.schemaVersion, schemaDigest: FORM_RULE.schemaDigest }),
    draft: () => ({ name: editorDraft?.name ?? '', description: editorDraft?.description ?? '', content: editorDraft?.content ?? '' }),
    apply: values => { if (editorDraft) mergeWhitelist(editorDraft, values, RULE_KEYS) },
    applyDraft: next => {
      if (!editorDraft) return
      const before = cloneJson(editorDraft)
      mergePlain(editorDraft, next)
      control.recordApply(before, editorDraft)
    },
    snapshot: () => {
      const snap = cloneJson(editorDraft ?? {})
      control.beginRound(snap) // 引擎撤销单元起点 = 本轮开始前草稿（宿主撤销恢复到这里）
      return snap
    },
    restore: snap => { if (editorDraft) replaceInPlace(editorDraft, snap) },
    round: control.round,
    undoRound: () => {
      const snap = control.snapshotOf()
      if (!editorDraft || !control.round.canUndo || snap === null) return false
      replaceInPlace(editorDraft, snap)
      control.resetAfterUndo()
      return true
    },
    noteManualChange: () => control.noteManualChange(),
    observeFill: resp => control.observeFill(expected, resp),
  }
}

/** 动作定义编辑表单的宿主适配（新建动作已预生成 id 时 targetId 按 DEF-02 仍传空串）。 */
export function actionAssistBinding(editorDraft: ActionAssistDraft | null | undefined, options: WorkflowAssistOptions = {}): WorkflowAutofillBinding {
  const control: AssistRoundControl = createRoundMirror(Object.fromEntries(FORM_ACTION.fields.map(f => [f.id, f.label ?? f.id])))
  const expected: AssistRoundExpectation = {
    formId: 'action', targetKind: 'action', targetId: options.targetId || '',
    schemaVersion: FORM_ACTION.schemaVersion, schemaDigest: FORM_ACTION.schemaDigest,
  }
  return {
    space: 'ontology',
    ontologyId: options.ontologyId || '',
    targetKind: 'action',
    targetId: options.targetId || '',
    contextTitle: options.contextTitle || '动作定义辅助填写',
    formId: 'action',
    codecs: {},
    contractInfo: () => ({ schemaVersion: FORM_ACTION.schemaVersion, schemaDigest: FORM_ACTION.schemaDigest }),
    draft: () => ({ name: editorDraft?.name ?? '', description: editorDraft?.description ?? '', effect: editorDraft?.effect ?? '' }),
    apply: values => { if (editorDraft) mergeWhitelist(editorDraft, values, ACTION_KEYS) },
    applyDraft: next => {
      if (!editorDraft) return
      const before = cloneJson(editorDraft)
      mergePlain(editorDraft, next)
      control.recordApply(before, editorDraft)
    },
    snapshot: () => {
      const snap = cloneJson(editorDraft ?? {})
      control.beginRound(snap)
      return snap
    },
    restore: snap => { if (editorDraft) replaceInPlace(editorDraft, snap) },
    round: control.round,
    undoRound: () => {
      const snap = control.snapshotOf()
      if (!editorDraft || !control.round.canUndo || snap === null) return false
      replaceInPlace(editorDraft, snap)
      control.resetAfterUndo()
      return true
    },
    noteManualChange: () => control.noteManualChange(),
    observeFill: resp => control.observeFill(expected, resp),
  }
}
