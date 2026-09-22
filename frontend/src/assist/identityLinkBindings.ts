// 项目区整表自动填写宿主适配（2026-09-22 T8 改版）——实例识别（P1）与链接映射（P5）两个编辑表单的
// AutofillHostBinding 工厂，供 ObjectSources.vue / LinkMappings.vue 与组件级测试共用；写法镜像
// ./ontologyBindings.ts（T5），整轮摘要镜像复用其导出的 createRoundMirror。
// 契约唯一来源：contracts/forms/identity.json、linkMapping.json（前端生成物 ./formContracts.gen.ts）。
// 字段白名单与契约 fieldId 一致（identity: mode/connection/table/primaryKey/note；linkMapping:
// sourceId/field/targetSourceId/targetField/note），两契约 lists=[]、codecs=[]——draft() 即「契约形态
// 标准化草稿」，组件键映射只有两处：primaryKey↔primary_key、note↔noteDraft；ref 候选（连接/表/字段/
// 来源）的合法性由服务端 assist-context / fill 校验，前端不做同名校验、更不做「字段同名＝业务等价」
// 的推断（同名不自动等价——照常提交，服务端核验不过即转 unresolved）。
// P1 边界（需求 §3）：登记实例模式（mode='registered'）只暴露 {mode, note}——说明类字段可填，连接/表/
// 主键不可见即不可填（visibleWhen mode=database）；登记实例清单（instances）不在契约内，永不出网、
// 不被批量生成、不凭 id 名称推断唯一性。mode 建议切换经 options.applyMode 守卫（组件复用既有拦截：
// 登记引用未清理时禁止切回 database），被拦截时整批中的数据库键也不落草稿。
// P5 边界：映射行按目标定位局部更新——binding 以 targetId='<对象类型>.<关系id>' 绑定当前编辑的
// 那一条链接映射，applyDraft 只写该行草稿的白名单键；relation/targetType/membership/legacy 等白名单
// 外结构不在快照里、applyDraft 不读、restore 不触碰（未知行/未知字段零丢失，A13）。
// 保存边界：apply/applyDraft 只把合法操作写进宿主本地草稿（「已填写 N 项，尚未保存」由宿主状态条
// 提示），绝不调用 form-save/commit-now/touch；持久化永远由用户在表单上走既有保存链路（说明随既有
// commitDesc 落盘）。状态条镜像语义见 ontologyBindings.ts 头注（引擎是唯一权威，镜像只服务宿主
// 状态条渲染与宿主「撤销本次填写」按钮）。
import { FORM_IDENTITY, FORM_LINK_MAPPING, type AssistFormId } from './formContracts.gen'
import { cloneJson, type AutofillFieldCodec } from './formAutofill'
import type { AutofillHostBinding } from './useAssistPanel'
import { createRoundMirror, type AssistRoundControl, type AssistRoundExpectation, type AssistRoundMirror } from './ontologyBindings'

export type { AssistRoundMirror }

/** 实例识别草稿形状（ObjectSources identityDraft 的白名单子集；instances 不在其中） */
export interface IdentityAssistDraft { mode: 'database' | 'registered'; connection: string; table: string; primary_key: string }
/** 链接映射草稿形状（LinkMappings draft(RelationView) 的白名单子集） */
export interface LinkMappingAssistDraft { sourceId: string; field: string; targetSourceId: string; targetField: string }
/** 组件说明草稿引用（Vue Ref<string> 的结构兼容最小形状） */
export interface NoteDraftRef { value: string }

const asString = (v: unknown): v is string => typeof v === 'string'

/** 宿主 binding 的新对接面（2026-09-22 改版）：引擎必需成员 + 状态条镜像与宿主撤销。 */
export interface IdentityLinkAutofillBinding extends AutofillHostBinding {
  /** autofill/1 表单契约 id（contracts/forms/<formId>.json） */
  formId: AssistFormId
  /** 两表单契约无 lists/codecs：恒为空（引擎 identity 直写，点路径=顶层键） */
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

export interface IdentityAssistOptions {
  projectId?: string
  /** 编辑目标：对象类型标识（如 'cluster'）；对象尚未启用映射时传 '' */
  targetId?: string
  /** 未取到上下文时的兜底标题（取到后抽屉改用 context.title） */
  contextTitle?: string
  /**
   * mode 写入守卫（可选）：组件用它复用既有切换拦截（登记引用未清理时禁止切回 database 并给出
   * 表单内提示）；返回 false 时 mode 不落草稿，且同批的连接/表/主键也一并忽略。
   */
  applyMode?: (target: 'database' | 'registered') => boolean
}

/**
 * 实例识别编辑表单的宿主适配（P1）。draft 参数是读取 identityDraft 的 getter（每次调用取当前
 * 对象）；registered 模式快照只含 {mode, note}（连接/表/主键 visibleWhen=false，不进快照、
 * applyDraft 也忽略）；database 模式快照为 5 键，primaryKey 完成组件键 primary_key 的映射。
 */
export function identityAssistBinding(draft: () => IdentityAssistDraft | null | undefined, note: NoteDraftRef | null | undefined, options: IdentityAssistOptions = {}): IdentityLinkAutofillBinding {
  const control: AssistRoundControl = createRoundMirror(Object.fromEntries(FORM_IDENTITY.fields.map(f => [f.id, f.label ?? f.id])))
  const expected: AssistRoundExpectation = {
    formId: 'identity', targetKind: 'identity', targetId: options.targetId || '',
    schemaVersion: FORM_IDENTITY.schemaVersion, schemaDigest: FORM_IDENTITY.schemaDigest,
  }
  const readNote = (): string => (note && asString(note.value) ? note.value : '')
  /** 白名单快照：模式决定形状；primaryKey 映射组件键 primary_key。 */
  const snapshotOf = (): Record<string, unknown> => {
    const d = draft()
    if (d?.mode === 'registered') return { mode: 'registered', note: readNote() }
    return { mode: 'database', connection: d?.connection ?? '', table: d?.table ?? '', primaryKey: d?.primary_key ?? '', note: readNote() }
  }
  /** 合法化模式目标值；非枚举返回 null（不写入）。 */
  const modeValueOf = (v: unknown): 'database' | 'registered' | null =>
    v === 'database' || v === 'registered' ? v : null
  /** 契约草稿 → 宿主草稿投影：mode 先行（守卫），registered 时连接/表/主键一律不写。 */
  const applyProjection = (next: Record<string, unknown>): void => {
    const d = draft()
    if (!d) return
    const target = next.mode === undefined ? null : modeValueOf(next.mode)
    let modeBlocked = false // 守卫拒绝切换：mode 不落草稿，同批连接/表/主键也一并忽略
    if (target !== null && target !== d.mode) {
      if (options.applyMode && !options.applyMode(target)) modeBlocked = true
      else d.mode = target
    }
    if (d.mode === 'database' && !modeBlocked) {
      if (asString(next.connection)) d.connection = next.connection
      if (asString(next.table)) d.table = next.table
      if (asString(next.primaryKey)) d.primary_key = next.primaryKey
    }
    // 说明（nullable+clearable）：字符串直写；null=用户明确要求清空（引擎 clear 语义）→ 置空串
    if (asString(next.note)) { if (note) note.value = next.note }
    else if (next.note === null && note) note.value = ''
  }
  /**
   * 快照恢复（宿主「撤销本次填写」与引擎 restore 同一实现）：白名单键就地写回，白名单外键
   * （instances 等）不触碰——登记实例清单不在辅助范围，撤销不得丢失。快照先 cloneJson，
   * 恢复后宿主继续手改不会反向污染镜像里保存的本轮起点。
   */
  const restoreSnap = (snap: unknown): void => {
    const d = draft()
    if (!d) return
    const s: Record<string, unknown> = snap && typeof snap === 'object' ? cloneJson(snap as Record<string, unknown>) : {}
    if (asString(s.note) && note) note.value = s.note
    else if (s.note === null && note) note.value = ''
    const target = modeValueOf(s.mode)
    if (target !== null) d.mode = target // 恢复本轮开始前的既有状态，不走建议切换守卫
    if (d.mode === 'database') {
      if (asString(s.connection)) d.connection = s.connection
      if (asString(s.table)) d.table = s.table
      if (asString(s.primaryKey)) d.primary_key = s.primaryKey
    }
  }
  return {
    space: 'project',
    projectId: options.projectId || '',
    targetKind: 'identity',
    targetId: options.targetId || '',
    contextTitle: options.contextTitle || '实例识别辅助填写',
    formId: 'identity',
    codecs: {},
    contractInfo: () => ({ schemaVersion: FORM_IDENTITY.schemaVersion, schemaDigest: FORM_IDENTITY.schemaDigest }),
    draft: snapshotOf,
    apply: values => { applyProjection({ ...snapshotOf(), ...Object(values) }) },
    applyDraft: next => {
      if (!next || typeof next !== 'object') return
      const before = snapshotOf()
      applyProjection(next)
      control.recordApply(before, snapshotOf())
    },
    snapshot: () => {
      const snap = cloneJson(snapshotOf())
      control.beginRound(snap) // 引擎撤销单元起点 = 本轮开始前草稿（宿主撤销恢复到这里）
      return snap
    },
    restore: restoreSnap,
    round: control.round,
    undoRound: () => {
      const snap = control.snapshotOf()
      if (!draft() || !control.round.canUndo || snap === null) return false // 期间手改：快照已作废，不覆盖用户改动
      restoreSnap(snap)
      control.resetAfterUndo()
      return true
    },
    noteManualChange: () => control.noteManualChange(),
    observeFill: resp => control.observeFill(expected, resp),
  }
}

export interface LinkMappingAssistOptions {
  projectId?: string
  /** 编辑目标：'<对象类型>.<关系id>'（当前映射行的稳定定位，服务端据此出标题） */
  targetId?: string
  /** 未取到上下文时的兜底标题 */
  contextTitle?: string
}

/**
 * 链接映射编辑表单的宿主适配（P5）。draft 参数是读取组件 draft（RelationView）的 getter；
 * 快照只含白名单四字段 + note（relation/targetType/membership/legacy 等不在契约内：不出网、
 * 不被填写、restore 不触碰——同一条映射行的未知结构零丢失）。note 与组件 noteDraft（两端共用
 * 的链接说明）互读互写；两端来源/字段值照建议原样写入，前端不做同名等价判断。
 */
export function linkMappingAssistBinding(draft: () => LinkMappingAssistDraft | null | undefined, note: NoteDraftRef | null | undefined, options: LinkMappingAssistOptions = {}): IdentityLinkAutofillBinding {
  const control: AssistRoundControl = createRoundMirror(Object.fromEntries(FORM_LINK_MAPPING.fields.map(f => [f.id, f.label ?? f.id])))
  const expected: AssistRoundExpectation = {
    formId: 'linkMapping', targetKind: 'linkMapping', targetId: options.targetId || '',
    schemaVersion: FORM_LINK_MAPPING.schemaVersion, schemaDigest: FORM_LINK_MAPPING.schemaDigest,
  }
  const readNote = (): string => (note && asString(note.value) ? note.value : '')
  const snapshotOf = (): Record<string, unknown> => {
    const d = draft()
    return { sourceId: d?.sourceId ?? '', field: d?.field ?? '', targetSourceId: d?.targetSourceId ?? '', targetField: d?.targetField ?? '', note: readNote() }
  }
  const applyProjection = (next: Record<string, unknown>): void => {
    const d = draft()
    if (!d) return
    if (asString(next.sourceId)) d.sourceId = next.sourceId
    if (asString(next.field)) d.field = next.field
    if (asString(next.targetSourceId)) d.targetSourceId = next.targetSourceId
    if (asString(next.targetField)) d.targetField = next.targetField
    if (asString(next.note)) { if (note) note.value = next.note }
    else if (next.note === null && note) note.value = ''
  }
  /** 快照恢复（宿主撤销与引擎 restore 共用）：只回写本行白名单键；未知结构（relation/membership 等）不触碰。 */
  const restoreSnap = (snap: unknown): void => {
    const d = draft()
    if (!d) return
    const s: Record<string, unknown> = snap && typeof snap === 'object' ? cloneJson(snap as Record<string, unknown>) : {}
    if (asString(s.note) && note) note.value = s.note
    else if (s.note === null && note) note.value = ''
    if (asString(s.sourceId)) d.sourceId = s.sourceId
    if (asString(s.field)) d.field = s.field
    if (asString(s.targetSourceId)) d.targetSourceId = s.targetSourceId
    if (asString(s.targetField)) d.targetField = s.targetField
  }
  return {
    space: 'project',
    projectId: options.projectId || '',
    targetKind: 'linkMapping',
    targetId: options.targetId || '',
    contextTitle: options.contextTitle || '链接映射辅助填写',
    formId: 'linkMapping',
    codecs: {},
    contractInfo: () => ({ schemaVersion: FORM_LINK_MAPPING.schemaVersion, schemaDigest: FORM_LINK_MAPPING.schemaDigest }),
    draft: snapshotOf,
    apply: values => { applyProjection({ ...snapshotOf(), ...Object(values) }) },
    applyDraft: next => {
      if (!next || typeof next !== 'object') return
      const before = snapshotOf()
      applyProjection(next)
      control.recordApply(before, snapshotOf())
    },
    snapshot: () => {
      const snap = cloneJson(snapshotOf())
      control.beginRound(snap)
      return snap
    },
    restore: restoreSnap,
    round: control.round,
    undoRound: () => {
      const snap = control.snapshotOf()
      if (!draft() || !control.round.canUndo || snap === null) return false // 期间手改：不覆盖用户改动
      restoreSnap(snap)
      control.resetAfterUndo()
      return true
    },
    noteManualChange: () => control.noteManualChange(),
    observeFill: resp => control.observeFill(expected, resp),
  }
}
