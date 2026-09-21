// 属性取值来源表单（T9 · P2/P3/P4，2026-09-21）的辅助填写宿主适配。
// 契约唯一来源：./useAssistPanel.ts 的 AssistHostBinding；字段白名单唯一来源
// workbench/assist_fields.py 的 propertySource.kinds —— 按 draft.kind 分派四份子白名单
// （field / database / redis / flow）；aggregate/computed 是只读遗留结构、registered/none/unknown
// 非可辅助的取值来源结构，均不在辅助范围（宿主不提供入口，本工厂对它们输出空快照、apply 无操作）。
// 设计要点：
//   * draft() 输出「kind + 当前 kind 白名单键 + note」的扁平快照：复合组（lookup.match / params /
//     inputs）整组出现；view-model 临时字段（field 草稿的 mode/connection/table 等）与白名单外
//     内容一律不出网——后端 normalize_draft 会直接拒绝白名单外键，快照多给即 400。
//   * redis 目标两种形态（draft.connection=项目连接 id 或 draft.source=已登记 Redis 来源 id）。
//     快照选择「还原为纯 id」：来源形态把 draft.source 本身放进 connection 键。理由：后端
//     REF_CONN_ANY 候选集同时收连接 id 与来源 id，均为无前缀纯 id（assist_schema.conn_any）；
//     组件 UI 的 'redis:'/'conn:'/'src:' 前缀只属于表单交互层，不进协议。apply 时按注入的
//     redisSourceIds 区分：命中来源集合走 'src:' 形态写回，否则走 'conn:' 连接形态。
//   * apply 只把建议值合并进宿主本地草稿：undefined 不覆盖既有草稿；空串不写回（清空是用户
//     显式操作，建议不提供清空语义）；kind 不可改；当前 kind 白名单外的键忽略；复合组整组替换。
//     flow 的 flow/output 只写草稿值，不触发组件 selectFlow 的拉编排详情/清空重置副作用——与
//     用户手选行为不同是可接受的：建议合并进草稿后由既有保存校验兜底（output 不属于该编排
//     输出集合、输入缺失等都会被保存拦截；后端 blocked 校验在生成阶段先行把关）。
//   * database 连接/表切换同样只写值，不清空既有匹配条件与结果映射（组件手选会清空重选）；
//     建议值与旧条件不一致时由表单分组校验与保存前校验拦截。
//   * snapshot()/restore() 对宿主草稿对象整体 JSON 克隆/原位恢复（含白名单外字段，恢复零丢失）；
//     note 一并纳入（说明与取值配置同属本表单草稿）。
//   * 保存边界：本文件绝不调用 formSave/commit-now/touch——持久化由用户在表单点「保存」走
//     既有 form-save 链路（含校验与撤销记录）。
import type { AssistHostBinding } from './useAssistPanel'

/** 可辅助的取值来源结构（与 assist_fields.propertySource.kinds 的键一致） */
export type PropertySourceAssistKind = 'field' | 'database' | 'redis' | 'flow'

const ASSISTABLE_KINDS: readonly string[] = ['field', 'database', 'redis', 'flow']

/** 当前草稿对应的可辅助 kind；aggregate/computed/registered/none/unknown/空 → ''（不可辅助）。 */
export function propertySourceAssistKind(draft: unknown): PropertySourceAssistKind | '' {
  const kind = String((draft as { kind?: unknown } | null | undefined)?.kind ?? '')
  return ASSISTABLE_KINDS.includes(kind) ? (kind as PropertySourceAssistKind) : ''
}

export interface PropertySourceAssistOptions {
  /** 配置草稿 getter（reactive draft.value；引用会被 switchKind/提升等整体更换，必须每次取最新） */
  draft: () => unknown
  /** 项目说明草稿 getter（快照的 note 来源） */
  noteDraft: () => string
  /** 项目说明写回（apply 的 note 键经此进组件 noteDraft，与说明编辑器同一存储） */
  setNote: (value: string) => void
  /** Redis 目标写回（组件 setRedisTarget 同形：'conn:<id>' | 'src:<sourceId>'）；缺省退化为直写 connection */
  setRedisTarget?: (value: string) => void
  /** 已登记 Redis 来源 id 集合（区分建议的 connection 值是连接还是来源形态） */
  redisSourceIds?: () => string[]
  /** 项目 id（project 空间必填：assist-context 按它装配目录/编排/来源候选） */
  projectId?: string
  /** 目标标识：对象.属性（如 cluster.power） */
  targetId?: string
  /** 未取到上下文时的兜底标题 */
  contextTitle?: string
}

const cloneJson = <T,>(v: T): T => JSON.parse(JSON.stringify(v))
const str = (v: unknown): string => (typeof v === 'string' ? v : v === undefined || v === null ? '' : String(v))
const isPlainObject = (v: unknown): v is Record<string, unknown> =>
  !!v && typeof v === 'object' && !Array.isArray(v)

/** 各 kind 的建议字段键（含 note；与 assist_fields.propertySource.kinds 对齐，仅作写回分发） */
const KIND_KEYS: Record<PropertySourceAssistKind, readonly string[]> = {
  field: ['field', 'note'],
  database: ['connection', 'table', 'result.valueField', 'result.timestampField', 'lookup.match', 'note'],
  redis: ['connection', 'command', 'key', 'hashField', 'params', 'conversion', 'missing', 'note'],
  flow: ['flow', 'output', 'inputs', 'result.valueField', 'result.timestampField', 'note'],
}
const COMPOSITE_KEYS: readonly string[] = ['lookup.match', 'params', 'inputs']

/** 白名单快照：kind + 当前 kind 键 + note；view-model 临时字段不出网。 */
function draftSnapshot(d: any, kind: PropertySourceAssistKind, note: string): Record<string, unknown> {
  const snap: Record<string, unknown> = { kind }
  if (kind === 'field') {
    snap.field = str(d.field)
  } else if (kind === 'database') {
    snap.connection = str(d.connection)
    snap.table = str(d.table)
    snap['result.valueField'] = str(d.result?.valueField)
    snap['result.timestampField'] = str(d.result?.timestampField)
    snap['lookup.match'] = Array.isArray(d.lookup?.match) ? cloneJson(d.lookup.match) : []
  } else if (kind === 'redis') {
    // 目标快照「还原为纯 id」：来源形态出 draft.source，连接形态出 connection（选择见文件头）
    snap.connection = str(d.source || d.connection)
    snap.command = str(d.command || 'GET')          // 与表单展示默认一致
    snap.key = str(d.key)
    snap.hashField = str(d.hashField)
    snap.params = isPlainObject(d.params) ? cloneJson(d.params) : {}
    snap.conversion = str(d.conversion || 'number') // 与表单展示默认一致
    snap.missing = str(d.missing || 'null')
  } else {
    snap.flow = str(d.flow)
    snap.output = str(d.output)
    snap.inputs = isPlainObject(d.inputs) ? cloneJson(d.inputs) : {}
    snap['result.valueField'] = str(d.result?.valueField)
    snap['result.timestampField'] = str(d.result?.timestampField)
  }
  snap.note = note
  return snap
}

/** 复合组整组替换：lookup.match 进 lookup（保留 timeRange 等同组其他键），params/inputs 直写。 */
function setComposite(d: any, key: string, value: unknown): void {
  if (key === 'lookup.match') {
    if (!Array.isArray(value)) return
    if (!isPlainObject(d.lookup)) d.lookup = { match: [], timeRange: null }
    d.lookup.match = cloneJson(value)
    return
  }
  if (!isPlainObject(value)) return
  d[key] = cloneJson(value)
}

/** 连接键写回：redis 走来源/连接形态判定，database 直写纯 id（'db:' 前缀只在 UI 层）。 */
function applyConnection(d: any, kind: PropertySourceAssistKind, value: string, opts: PropertySourceAssistOptions): void {
  const v = value.trim()
  if (!v) return
  if (kind === 'redis') {
    if (opts.setRedisTarget) {
      if (v.startsWith('conn:') || v.startsWith('src:')) { opts.setRedisTarget(v); return }
      const isSource = (opts.redisSourceIds?.() ?? []).includes(v)
      opts.setRedisTarget((isSource ? 'src:' : 'conn:') + v)
      return
    }
    d.source = ''; d.connection = v // 退化路径：无注入 setter 时按连接形态直写
    return
  }
  d.connection = v.startsWith('db:') ? v.slice(3) : v
}

/** 建议值合并：undefined/空串不覆盖；kind 不可改；白名单外忽略；复合组整组替换；标量仅收字符串。 */
function applyValues(d: any, kind: PropertySourceAssistKind, values: Record<string, unknown>, opts: PropertySourceAssistOptions): void {
  const keys = KIND_KEYS[kind]
  for (const [rawKey, value] of Object.entries(values)) {
    if (rawKey === 'kind' || rawKey === 'note') continue // kind 不可由建议修改；note 已单独处理
    if (!keys.includes(rawKey)) continue
    if (value === undefined) continue                    // undefined 不覆盖既有草稿
    if (typeof value === 'string' && value.trim() === '') continue // 空串不提供清空语义
    if (COMPOSITE_KEYS.includes(rawKey)) { setComposite(d, rawKey, value); continue }
    if (rawKey.startsWith('result.')) {
      const leaf = rawKey.slice('result.'.length)
      if (!isPlainObject(d.result)) d.result = { valueField: '', timestampField: '' }
      if (typeof value !== 'string') continue
      d.result[leaf] = value
      continue
    }
    if (rawKey === 'connection') { applyConnection(d, kind, value as string, opts); continue }
    if (typeof value !== 'string') continue
    d[rawKey] = value // field/table/command/key/hashField/conversion/missing/flow/output
  }
}

/** 属性取值来源表单的宿主 binding 工厂：kind 分派白名单快照 + 草稿直写合并 + 节点级快照/恢复。 */
export function propertySourceBinding(opts: PropertySourceAssistOptions): AssistHostBinding {
  return {
    space: 'project',
    projectId: opts.projectId || '',
    targetKind: 'propertySource',
    targetId: opts.targetId || '',
    contextTitle: opts.contextTitle || '属性取值来源辅助填写',
    draft(): Record<string, unknown> {
      const d = opts.draft()
      const kind = propertySourceAssistKind(d)
      if (!kind) return {}
      return draftSnapshot(d, kind, str(opts.noteDraft()))
    },
    apply(values) {
      const d = opts.draft()
      if (!d || !values || typeof values !== 'object') return
      const kind = propertySourceAssistKind(d)
      if (!kind) return
      if (typeof values.note === 'string') opts.setNote(values.note)
      applyValues(d, kind, values, opts)
    },
    /** 采纳前快照：宿主草稿整体 JSON 克隆（含白名单外字段）+ 说明，恢复零丢失 */
    snapshot() {
      return { draft: cloneJson(opts.draft() ?? {}), note: str(opts.noteDraft()) }
    },
    /** 恢复快照：原位替换键值，保持 draft 对象身份（reactive 引用与子组件绑定稳定） */
    restore(snap) {
      const d = opts.draft()
      if (!d || !snap || typeof snap !== 'object') return
      const s = snap as { draft?: unknown; note?: unknown }
      const src = isPlainObject(s.draft) ? s.draft : {}
      const target = d as Record<string, unknown>
      for (const k of Object.keys(target)) delete target[k]
      Object.assign(target, cloneJson(src))
      if (typeof s.note === 'string') opts.setNote(s.note)
    },
  }
}
