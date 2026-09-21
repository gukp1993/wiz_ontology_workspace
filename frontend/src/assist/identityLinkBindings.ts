// 项目区辅助填写宿主适配（2026-09-21 T8）——实例识别（P1）与链接映射（P5）两个编辑表单的
// AssistHostBinding 工厂，供 ObjectSources.vue / LinkMappings.vue 与组件级测试共用。
// 契约唯一来源：./useAssistPanel.ts 的 AssistHostBinding；字段白名单与后端 workbench/assist_fields.py
// 一致（identity：mode/connection/table/primaryKey/note；linkMapping：sourceId/field/targetSourceId/
// targetField/note）。前端不维护第二份场景注册表——可编辑字段以后端 context 响应的 editableFields
// 为准，这里只做「草稿 ↔ 白名单快照」的形状适配（primaryKey ↔ 组件键 primary_key、note ↔ 组件
// noteDraft；draft 是组件 ref 的结构兼容引用，测试可用普通 {value} 对象）。
// 保存边界：apply 只把建议值合并进宿主本地草稿（面板提示「已填入，尚未保存」），绝不调用
// form-save/commit-now/touch；持久化永远由用户在表单上走既有保存链路（含校验与撤销记录）。
// 范围边界：登记实例清单（identityDraft.instances）与成员规则（membership）、pendingLinks 新建
// 选择不在辅助范围，快照/apply 一律不触达。
import type { AssistHostBinding } from './useAssistPanel'

/** 实例识别草稿形状（ObjectSources identityDraft 的白名单子集；instances 不在其中） */
export interface IdentityAssistDraft { mode: 'database' | 'registered'; connection: string; table: string; primary_key: string }
/** 链接映射草稿形状（LinkMappings draft(RelationView) 的白名单子集） */
export interface LinkMappingAssistDraft { sourceId: string; field: string; targetSourceId: string; targetField: string }
/** 组件说明草稿引用（Vue Ref<string> 的结构兼容最小形状） */
export interface NoteDraftRef { value: string }

const IDENTITY_KEYS: readonly string[] = ['mode', 'connection', 'table', 'primaryKey', 'note']
const LINK_KEYS: readonly string[] = ['sourceId', 'field', 'targetSourceId', 'targetField', 'note']

/** 白名单外键一律忽略；undefined 值不写入（不覆盖既有草稿）；write 返回 false 表示该键未采纳。 */
function mergeWhitelist(write: (k: string, v: unknown) => boolean, values: Record<string, unknown> | null | undefined, keys: readonly string[]): void {
  if (!values) return
  for (const [k, v] of Object.entries(values)) {
    if (!keys.includes(k) || v === undefined) continue
    write(k, v)
  }
}

const asString = (v: unknown): v is string => typeof v === 'string'
const snapOf = (snap: unknown): Record<string, unknown> => (snap && typeof snap === 'object' ? snap as Record<string, unknown> : {})
const readNote = (note: NoteDraftRef | null | undefined): string => (note && asString(note.value) ? note.value : '')

export interface IdentityAssistOptions {
  projectId?: string
  /** 编辑目标：对象类型标识（如 'o_cluster'）；对象尚未启用映射时传 '' */
  targetId?: string
  /** 未取到上下文时的兜底标题（取到后面板改用 context.title） */
  contextTitle?: string
  /**
   * mode 写入守卫（可选）：组件用它复用既有切换拦截（登记引用未清理时禁止切回 database 并给出
   * 表单内提示）；返回 false 时 mode 不落草稿。缺省直接写入。
   */
  applyMode?: (target: 'database' | 'registered') => boolean
}

/**
 * 实例识别编辑表单的宿主适配（P1）。draft 参数是读取 identityDraft 的 getter（每次调用取当前
 * 对象）；registered 模式快照只含 {mode, note}（连接/表/主键不适用，不进快照、apply 也忽略）；
 * database 模式快照为 5 键，primaryKey 完成组件键 primary_key 的映射。
 */
export function identityAssistBinding(draft: () => IdentityAssistDraft | null | undefined, note: NoteDraftRef | null | undefined, options: IdentityAssistOptions = {}): AssistHostBinding {
  const setMode = (target: unknown): boolean => {
    if (target !== 'database' && target !== 'registered') return false
    const d = draft()
    if (!d || d.mode === target) return true // 幂等：已是目标态按成功处理（不重复写）
    if (options.applyMode && !options.applyMode(target)) return false
    d.mode = target
    return true
  }
  /** 白名单快照：模式决定形状；primaryKey 映射组件键 primary_key。 */
  const snapshotOf = (): Record<string, unknown> => {
    const d = draft()
    if (d?.mode === 'registered') return { mode: 'registered', note: readNote(note) }
    return { mode: 'database', connection: d?.connection ?? '', table: d?.table ?? '', primaryKey: d?.primary_key ?? '', note: readNote(note) }
  }
  return {
    space: 'project',
    projectId: options.projectId || '',
    targetKind: 'identity',
    targetId: options.targetId || '',
    contextTitle: options.contextTitle || '实例识别辅助填写',
    draft: snapshotOf,
    apply: values => {
      const d = draft()
      if (!d) return
      const entries = Object.entries(values || {}).filter(([k, v]) => IDENTITY_KEYS.includes(k) && v !== undefined)
      // mode 先行（与白名单键序一致，且与键顺序无关）：同批建议先切模式，其余键按「切换后的模式」
      // 决定是否适用——切换到 registered（或切换被拦截仍为 registered）时，连接/表/主键一律不写入。
      for (const [k, v] of entries) if (k === 'mode') setMode(v)
      for (const [k, v] of entries) {
        if (k === 'mode') continue
        if (k === 'note') { if (asString(v)) note && (note.value = v); continue }
        if (d.mode === 'registered') continue // registered 模式：连接/表/主键不适用，一律忽略
        if (!asString(v)) continue
        if (k === 'connection') d.connection = v
        else if (k === 'table') d.table = v
        else if (k === 'primaryKey') d.primary_key = v
      }
    },
    snapshot: () => JSON.parse(JSON.stringify(snapshotOf())),
    restore: snap => {
      const d = draft()
      if (!d) return
      const s = snapOf(snap)
      if (asString(s.note)) note && (note.value = s.note)
      if (s.mode !== undefined) setMode(s.mode)
      if (d.mode === 'registered') return // registered 快照只含 mode/note；连接/表/主键保持现状
      if (asString(s.connection)) d.connection = s.connection
      if (asString(s.table)) d.table = s.table
      if (asString(s.primaryKey)) d.primary_key = s.primaryKey
    },
  }
}

export interface LinkMappingAssistOptions {
  projectId?: string
  /** 编辑目标：链接标识（本体关系 id，如 'l_belong'） */
  targetId?: string
  /** 未取到上下文时的兜底标题 */
  contextTitle?: string
}

/**
 * 链接映射编辑表单的宿主适配（P5）。draft 参数是读取组件 draft（RelationView）的 getter；
 * 快照只含白名单四字段 + note（relation/targetType/membership/legacy 不在辅助范围）；
 * note 与组件 noteDraft（两端共用的链接说明）互读互写。
 */
export function linkMappingAssistBinding(draft: () => LinkMappingAssistDraft | null | undefined, note: NoteDraftRef | null | undefined, options: LinkMappingAssistOptions = {}): AssistHostBinding {
  const snapshotOf = (): Record<string, unknown> => {
    const d = draft()
    return { sourceId: d?.sourceId ?? '', field: d?.field ?? '', targetSourceId: d?.targetSourceId ?? '', targetField: d?.targetField ?? '', note: readNote(note) }
  }
  return {
    space: 'project',
    projectId: options.projectId || '',
    targetKind: 'linkMapping',
    targetId: options.targetId || '',
    contextTitle: options.contextTitle || '链接映射辅助填写',
    draft: snapshotOf,
    apply: values => {
      const d = draft()
      if (!d) return
      mergeWhitelist((k, v) => {
        if (k === 'note') { if (asString(v)) note && (note.value = v); return true }
        if (!asString(v)) return false
        if (k === 'sourceId') d.sourceId = v
        else if (k === 'field') d.field = v
        else if (k === 'targetSourceId') d.targetSourceId = v
        else if (k === 'targetField') d.targetField = v
        else return false
        return true
      }, values, LINK_KEYS)
    },
    snapshot: () => JSON.parse(JSON.stringify(snapshotOf())),
    restore: snap => {
      const d = draft()
      if (!d) return
      const s = snapOf(snap)
      if (asString(s.note)) note && (note.value = s.note)
      if (asString(s.sourceId)) d.sourceId = s.sourceId
      if (asString(s.field)) d.field = s.field
      if (asString(s.targetSourceId)) d.targetSourceId = s.targetSourceId
      if (asString(s.targetField)) d.targetField = s.targetField
    },
  }
}
