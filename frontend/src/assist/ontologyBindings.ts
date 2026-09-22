// 本体区整表自动填写宿主适配（2026-09-22 T5 改版）——对象定义（O1）与业务链接（O3）两个编辑表单的
// AutofillHostBinding 工厂，供 ObjectWorkspace.vue 与组件级测试共用；业务规则/动作适配
// （workflowBindings.ts）复用本文件导出的 createRoundMirror。
// 契约唯一来源：contracts/forms/object.json、link.json（前端生成物 ./formContracts.gen.ts）。
// 字段白名单与契约 fieldId 一致（object: label/comment；link: label/from/to/cardinality/
// reverseLabel/comment），draft() 即「契约形态标准化草稿」——getDraft/normalize 对这四份表单是恒等
// 映射（全部是顶层普通字段，无点路径、无行结构 → 无需 codec，引擎 identity 直写；from/to 等
// ref 候选的合法性由服务端 assist-context 校验，前端不重复白名单）。
// 保存边界：apply/applyDraft 只把合法操作写进宿主本地草稿（「已填写 N 项，尚未保存」由宿主
// 状态条提示），绝不调用 form-save/commit-now/touch；持久化永远由用户在表单上走既有保存链路。
// 状态条（04 §6.6「宿主按 statusBarText/roundSummary 渲染」）：引擎状态由 AssistPanel 持有且
// 不对外暴露，宿主经 binding 回调观测——引擎在撤销单元首次写入前调用 snapshot()、每次落回调用
// applyDraft()，本工厂据此镜像「整轮摘要」（已填 N 项/另有 M 项待补充/逐字段旧值→新值）。
// 引擎仍是唯一权威：迟到丢弃/代际失效/续轮握手全部由引擎负责，镜像只服务状态条渲染与宿主
// 「撤销本次填写」按钮（恢复本轮开始前草稿，期间手改禁用，语义与引擎 undoRound 对齐；
// 待补充计数经组件 api 包装把 fill 响应回传 observeFill 维护）。
import { computed, reactive } from 'vue'
import { FORM_LINK, FORM_OBJECT, type AssistFormId } from './formContracts.gen'
import { cloneJson, deepEqual, fmtValue, type AutofillFieldCodec } from './formAutofill'
import type { AutofillFillResponse } from './types'
import type { AutofillHostBinding, RoundFieldChange } from './useAssistPanel'

/** 对象编辑器草稿形状（ObjectWorkspace editor.draft，kind='object'） */
export type ObjectAssistDraft = { label: string; comment: string }
/** 链接编辑器草稿形状（ObjectWorkspace LinkDraft） */
export type LinkAssistDraft = { label: string; from: string; to: string; cardinality: string; reverseLabel: string; comment: string }

export interface OntologyAssistOptions {
  /** 本体区必填：当前本体工作区 id */
  ontologyId?: string
  /** 编辑目标稳定 id（对象/链接 @id）；新建尚未保存时传 '' */
  targetId?: string
  /** 未取到上下文时的兜底标题（取到后抽屉改用 context.title） */
  contextTitle?: string
}

const OBJECT_KEYS: readonly string[] = ['label', 'comment']
const LINK_KEYS: readonly string[] = ['label', 'from', 'to', 'cardinality', 'reverseLabel', 'comment']

// ── 整轮摘要镜像（ontology 区四表单共用；workflowBindings 复用）───────────────
/** 宿主状态条数据（引擎状态的展示镜像；round 为 reactive，statusBarText 在模板中自动解包） */
export interface AssistRoundMirror {
  /** 本轮是否在途（引擎已开撤销单元） */
  active: boolean
  /** 已填写 N 项：顶层实际改变的字段/复合组数（跨续轮累计，§4.3） */
  appliedCount: number
  /** 另有 M 项待补充（questions+unresolved，来自 fill 响应观测） */
  pendingCount: number
  /** 逐字段旧值→新值（查看修改） */
  changes: RoundFieldChange[]
  /** 整轮撤销可用（期间手改即失效） */
  canUndo: boolean
  /** 撤销不可用时的说明（手改 → 已保留你的手动修改…，§4.5） */
  undoHint: string
  /** 刚撤销过（状态条显示恢复文案） */
  undone: boolean
  /** 状态条主文案（空串=不显示）；口径与引擎 statusBarText 一致 */
  statusBarText: string
}

/** observeFill 的期望值：响应必须命中本表单（formId/target/契约指纹）才计入待补充 */
export interface AssistRoundExpectation {
  formId: string
  targetKind: string
  targetId: string
  schemaVersion: number
  schemaDigest: string
}

export interface AssistRoundControl {
  round: AssistRoundMirror
  /** 本轮起点快照（宿主 undoRound 用）；null = 不可整轮撤销（未开始/已手改/已撤销） */
  snapshotOf(): unknown
  /** 引擎在撤销单元首次写入前调用宿主 snapshot() 时同步：记录本轮起点并重置镜像 */
  beginRound(snap: unknown): void
  /** 引擎 applyDraft 落回后同步：累计顶层变更数并维护逐字段旧值→新值 */
  recordApply(before: Record<string, unknown>, after: Record<string, unknown>): void
  /** 宿主手改登记：禁整轮撤销（快照作废，与引擎 notifyDraftChanged 同步） */
  noteManualChange(): void
  /** 组件 api 包装回传 fill 响应：核对 formId/target/契约指纹后维护「另有 M 项待补充」 */
  observeFill(expected: AssistRoundExpectation, resp: unknown): void
  /** 宿主整轮撤销完成后的镜像复位 */
  resetAfterUndo(): void
}

export function createRoundMirror(labels: Record<string, string> = {}): AssistRoundControl {
  let snap: unknown = null
  let manual = false // 期间发生过手改：本轮永久禁用整轮撤销（快照不重建，同引擎语义）
  const round = reactive({
    active: false,
    appliedCount: 0,
    pendingCount: 0,
    changes: [] as RoundFieldChange[],
    canUndo: false,
    undoHint: '',
    undone: false,
    statusBarText: computed(() => {
      if (!round.active || round.undone || round.appliedCount === 0) return ''
      const base = `已填写 ${round.appliedCount} 项，尚未保存`
      return round.pendingCount > 0 ? `${base}；另有 ${round.pendingCount} 项待补充` : base
    }),
  })
  return {
    round,
    snapshotOf: () => snap,
    beginRound(next) {
      snap = next
      manual = false
      round.active = true
      round.undone = false
      round.canUndo = false
      round.undoHint = ''
      round.appliedCount = 0
      round.changes = []
      // 待补充计数不清零：observeFill（api 包装）在引擎处理响应「之前」回传，本响应的
      // 「另有 M 项待补充」先于本轮快照落账，这里清零会把刚观察到的值抹掉。
    },
    recordApply(before, after) {
      if (!round.active || (snap === null && !manual)) {
        // 撤销后的续轮写入：以上一次落回前的状态作为新撤销单元起点
        round.active = true
        snap = cloneJson(before)
        round.undone = false
      }
      let changed = 0
      const keys = new Set([...Object.keys(before), ...Object.keys(after)])
      for (const k of keys) {
        if (deepEqual(before[k], after[k])) continue
        changed++
        const entry: RoundFieldChange = { field: k, label: labels[k] ?? k, oldText: fmtValue(before[k]), newText: fmtValue(after[k]) }
        const idx = round.changes.findIndex(c => c.field === k)
        if (idx >= 0) round.changes.splice(idx, 1, entry)
        else round.changes.push(entry)
      }
      if (changed > 0) {
        round.appliedCount += changed
        if (!manual && snap !== null) round.canUndo = true // 手改后快照已作废：按钮保持禁用（不覆盖用户改动）
        round.undone = false
      }
    },
    noteManualChange() {
      if (!round.active) return
      round.canUndo = false
      snap = null
      manual = true
      if (!round.undoHint) round.undoHint = '已保留你的手动修改，本次自动填写不可直接撤销。'
    },
    observeFill(expected, resp) {
      if (!resp || typeof resp !== 'object') return
      const r = resp as Partial<AutofillFillResponse>
      if (r.protocol !== 'autofill/1') return // check/explain 帮助响应不进状态条
      if (r.formId !== expected.formId) return // 非本表单响应（切目标后迟到的旧响应）不计
      if (r.target && (r.target.targetKind !== expected.targetKind || (r.target.targetId ?? '') !== expected.targetId)) return
      if (r.schemaVersion !== expected.schemaVersion || r.schemaDigest !== expected.schemaDigest) return // 契约不符引擎会拒，镜像不显示
      if (r.status === 'empty') { round.pendingCount = 0; return }
      if (r.status === 'ok') round.pendingCount = (r.questions?.length ?? 0) + (r.unresolved?.length ?? 0)
    },
    resetAfterUndo() {
      snap = null
      manual = false
      round.active = false
      round.undone = true
      round.canUndo = false
      round.undoHint = ''
      round.appliedCount = 0
      round.changes = []
      round.pendingCount = 0
    },
  }
}

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

/** 引擎整稿落回的宿主实现：合并进既有草稿（不动白名单外的未知键，零丢失），并同步镜像。 */
function mergePlain(draft: Record<string, unknown>, next: Record<string, unknown>): void {
  Object.assign(draft, cloneJson(next))
}

/** 宿主 binding 的新对接面（T5 改版）：引擎必需成员 + 状态条镜像与宿主撤销。 */
export interface OntologyAutofillBinding extends AutofillHostBinding {
  /** autofill/1 表单契约 id（contracts/forms/<formId>.json） */
  formId: AssistFormId
  /** 本体四表单全是顶层普通字段：恒为空（引擎 identity 直写，点路径=顶层键） */
  codecs: Record<string, AutofillFieldCodec>
  /** 状态条镜像（reactive；模板直接渲染，引擎仍是唯一权威） */
  round: AssistRoundMirror
  /** 宿主「撤销本次填写」：恢复本轮开始前草稿；一次性；期间手改返回 false */
  undoRound(): boolean
  /** 宿主手改登记（配合面板 notifyDraftChanged：引擎作废在途+禁撤销，镜像同步说明） */
  noteManualChange(): void
  /** 组件 api 包装在 generate 返回后回传 fill 响应（维护「另有 M 项待补充」） */
  observeFill(resp: unknown): void
}

/** 对象定义编辑表单的宿主适配：draft 参数是编辑器实时草稿对象（引用须保持稳定）。 */
export function objectAssistBinding(editorDraft: ObjectAssistDraft | null | undefined, options: OntologyAssistOptions = {}): OntologyAutofillBinding {
  const control = createRoundMirror(Object.fromEntries(FORM_OBJECT.fields.map(f => [f.id, f.label ?? f.id])))
  const expected: AssistRoundExpectation = {
    formId: 'object', targetKind: 'object', targetId: options.targetId || '',
    schemaVersion: FORM_OBJECT.schemaVersion, schemaDigest: FORM_OBJECT.schemaDigest,
  }
  return {
    space: 'ontology',
    ontologyId: options.ontologyId || '',
    targetKind: 'object',
    targetId: options.targetId || '',
    contextTitle: options.contextTitle || '对象定义辅助填写',
    formId: 'object',
    codecs: {},
    contractInfo: () => ({ schemaVersion: FORM_OBJECT.schemaVersion, schemaDigest: FORM_OBJECT.schemaDigest }),
    draft: () => ({ label: editorDraft?.label ?? '', comment: editorDraft?.comment ?? '' }),
    apply: values => { if (editorDraft) mergeWhitelist(editorDraft, values, OBJECT_KEYS) },
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

/** 业务链接编辑表单的宿主适配（含起点/终点对象引用与数量关系）。 */
export function linkAssistBinding(linkDraft: LinkAssistDraft | null | undefined, options: OntologyAssistOptions = {}): OntologyAutofillBinding {
  const control = createRoundMirror(Object.fromEntries(FORM_LINK.fields.map(f => [f.id, f.label ?? f.id])))
  const expected: AssistRoundExpectation = {
    formId: 'link', targetKind: 'link', targetId: options.targetId || '',
    schemaVersion: FORM_LINK.schemaVersion, schemaDigest: FORM_LINK.schemaDigest,
  }
  return {
    space: 'ontology',
    ontologyId: options.ontologyId || '',
    targetKind: 'link',
    targetId: options.targetId || '',
    contextTitle: options.contextTitle || '业务链接辅助填写',
    formId: 'link',
    codecs: {},
    contractInfo: () => ({ schemaVersion: FORM_LINK.schemaVersion, schemaDigest: FORM_LINK.schemaDigest }),
    draft: () => ({
      label: linkDraft?.label ?? '', from: linkDraft?.from ?? '', to: linkDraft?.to ?? '',
      cardinality: linkDraft?.cardinality ?? '', reverseLabel: linkDraft?.reverseLabel ?? '', comment: linkDraft?.comment ?? '',
    }),
    apply: values => { if (linkDraft) mergeWhitelist(linkDraft, values, LINK_KEYS) },
    applyDraft: next => {
      if (!linkDraft) return
      const before = cloneJson(linkDraft)
      mergePlain(linkDraft, next)
      control.recordApply(before, linkDraft)
    },
    snapshot: () => {
      const snap = cloneJson(linkDraft ?? {})
      control.beginRound(snap)
      return snap
    },
    restore: snap => { if (linkDraft) replaceInPlace(linkDraft, snap) },
    round: control.round,
    undoRound: () => {
      const snap = control.snapshotOf()
      if (!linkDraft || !control.round.canUndo || snap === null) return false
      replaceInPlace(linkDraft, snap)
      control.resetAfterUndo()
      return true
    },
    noteManualChange: () => control.noteManualChange(),
    observeFill: resp => control.observeFill(expected, resp),
  }
}
