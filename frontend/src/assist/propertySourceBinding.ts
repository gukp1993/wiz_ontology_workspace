// 属性取值来源表单（P2 数据库取值 / P3 Redis 取值 / P4 编排取值）的整表自动填写宿主适配
// （2026-09-22 T7 改版，替代旧「建议卡→勾选→采纳」接线）。
// 契约唯一来源：contracts/forms/propertySource.json（T1 生成物 ./formContracts.gen.ts 的
// FORM_PROPERTY_SOURCE；按 draft.kind 分派 field / database / redis / flow 四个变体，
// 列表 id 与 codec 名以契约文件为准）。引擎对接面 ./formAutofill.ts 的 AutofillHostBinding
// （draft()/codecs/applyDraft|apply/snapshot()/restore()/contractInfo()）；状态条镜像复用
// T5 ./ontologyBindings.ts 的 createRoundMirror（引擎仍是唯一权威，镜像只服务宿主渲染）。
// 设计要点：
//   * draft() 输出「kind + 当前变体白名单键 + note」的契约形态扁平快照；复合组（lookup.match /
//     params / inputs）以「带 rowId 的行数组」出网，行字段用契约展平路径（如 value.kind），
//     模型据此按 rowId 定位局部行。view-model 临时字段（field 草稿的 mode/connection/table）与
//     白名单外内容一律不出网（后端 normalize_draft 会直接拒绝白名单外键）。
//   * codecs 注册契约声明的三个行组 codec（lookupMatchRows / redisKeyParams /
//     flowInputBindings），每个同时挂「契约字段路径」（set/clear 用）与「列表 id」（行操作用）
//     两个键：引擎按 op.field 查 codec，行操作的 field 恰为列表 id（与后端同一口径）。行操作只改
//     指定行（按 rowId 定位、按行字段 diff 写回），未涉及行、行内未知键、白名单外结构零丢失（A13）。
//     行组不属于当前变体时 codec 直接拒绝（kind 边界），副本不被修改、不产生伪变更计数。
//   * kind 边界（需求 §3 末段）：用户要求与当前 kind 不符（如当前 database 要求 redis）不是前端
//     切 kind——其他变体的键与 kind 变更一律拒绝并记入 refusals（原因含「请先切换来源类型」），
//     既有配置原样保留；后端按同一契约在服务端同样拒绝（该操作转 unresolved）。绝不隐式切换
//     kind、绝不清空既有配置。
//   * 依赖链（需求 §4.5 / A08「同组合法更新；未知影响先问，不产生旧表旧字段」）：连接→表→
//     取值字段→时间字段/匹配条件逐级核验后写回：
//       - 依赖不满足（如没有表却要给取值字段）：该字段不落，原因记入 refusals（无半组）；
//       - 连接/表变更会留下未提及的旧连接旧表配置：整组不落并说明，请用户确认丢弃范围或先回答
//         追问——不静默丢弃、也不保留旧表旧字段的假象。
//     redis（params 需要 key、hashField 需要 HGET）与 flow（output 需要 flow，inputs 需要 flow，
//     result.* 需要 output/取值字段）同样按组核验。
//   * 写回语义：undefined 不覆盖既有草稿；空串不表示清空；契约 clear → null 为显式清空
//     （仅 nullable 字段：note / redis.hashField / flow.result.*）；复合组按行 diff 局部更新。
//     flow 的 flow/output 只写草稿值，不触发组件 selectFlow 的拉编排详情/清空重置副作用——
//     与用户手选不同是可接受的：建议合并进草稿后由既有保存校验兜底（未知输出、输入缺失等被保存拦截，
//     后端 blocked 校验在生成阶段先行把关）。database 连接/表切换同样只写值，不清空既有匹配条件与
//     结果映射（组件手选会清空重选）；不一致时由表单分组校验与保存前校验拦截。
//   * snapshot()/restore() 对宿主草稿对象整体 JSON 克隆/原位恢复（含白名单外字段，恢复零丢失）；
//     note 一并纳入。round 为状态条镜像，undoRound 恢复本轮开始前草稿（期间手改即失效，§4.5）。
//   * 保存边界：本文件绝不调用 formSave/commit-now/touch/changed——回填只改本地草稿，
//     持久化由用户在表单点「保存」走既有 form-save 链路（含校验与 409 等原流程）。
//   * 只读/探测边界不变：本文件不刷新目录、不执行任何 SQL/Redis 命令、不发起任何网络请求；
//     候选合法性由服务端 assist-context / fill 按目录与来源核验。
import { ref, type Ref } from 'vue'
import { FORM_PROPERTY_SOURCE, type AssistFormId } from './formContracts.gen'
import { cloneJson, deepEqual, type AutofillFieldCodec } from './formAutofill'
import type { AutofillHostBinding } from './useAssistPanel'
import { createRoundMirror, type AssistRoundControl, type AssistRoundExpectation, type AssistRoundMirror } from './ontologyBindings'

export type { AssistRoundMirror }

/** 可辅助的取值来源结构（与契约 variants 的键一致） */
export type PropertySourceAssistKind = 'field' | 'database' | 'redis' | 'flow'

const ASSISTABLE_KINDS: readonly string[] = ['field', 'database', 'redis', 'flow']

/** 当前草稿对应的可辅助 kind；aggregate/computed/registered/none/unknown/空 → ''（不可辅助）。 */
export function propertySourceAssistKind(draft: unknown): PropertySourceAssistKind | '' {
  const kind = String((draft as { kind?: unknown } | null | undefined)?.kind ?? '')
  return ASSISTABLE_KINDS.includes(kind) ? (kind as PropertySourceAssistKind) : ''
}

const str = (v: unknown): string => (typeof v === 'string' ? v : v === undefined || v === null ? '' : String(v))
const isPlainObject = (v: unknown): v is Record<string, unknown> =>
  !!v && typeof v === 'object' && !Array.isArray(v)

// ── 契约标签与拒绝原因（状态条 / 拒绝原因展示共用） ────────────────────────────
/** 契约字段标签（四个变体合并；键取契约 fieldId）。 */
export const PROPERTY_SOURCE_FIELD_LABELS: Record<string, string> = (() => {
  const out: Record<string, string> = {}
  for (const variant of Object.values(FORM_PROPERTY_SOURCE.variants ?? {})) {
    for (const field of variant.fields ?? []) out[field.id] = field.label ?? field.id
  }
  return out
})()

/** 行字段展平路径 → 展示名（行拒绝原因用）。 */
const ROW_FIELD_LABELS: Record<string, string> = {
  'field': '匹配字段', 'operator': '操作符', 'value.kind': '取值方式',
  'value.field': '身份表字段', 'value.property': '引用属性', 'value.value': '常量值', 'value.parameter': '项目参数',
  'token': 'Key 占位符', 'from': '绑定来源', 'property': '引用属性',
  'inputId': '编排输入', 'value': '常量取值',
}

export const propertySourceFieldLabel = (field: string): string =>
  PROPERTY_SOURCE_FIELD_LABELS[field] ?? ROW_FIELD_LABELS[field] ?? field

/** kind 边界拒绝原因（其他变体字段 / kind 变更）：绝不隐式切换来源类型、绝不清空既有配置。 */
export const PROPERTY_SOURCE_KIND_REASON =
  '该内容属于其他来源类型：请先切换来源类型后再填写；本次未应用，既有配置保持不变。'

/** 一次写入拒绝（宿主在状态条区域展示；上限 8 条） */
export interface PropertySourceAssistRefusal { field: string; reason: string }

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

/** 宿主 binding 的新对接面：引擎必需成员 + 状态条镜像 + 宿主撤销 + 拒绝原因出口。 */
export interface PropertySourceAssistHostBinding extends AutofillHostBinding {
  /** autofill/1 表单契约 id（contracts/forms/propertySource.json） */
  formId: AssistFormId
  /** 行组 codec 注册表（契约字段路径与列表 id 双键） */
  codecs: Record<string, AutofillFieldCodec>
  /** 状态条镜像（reactive；模板直接渲染，引擎仍是唯一权威） */
  round: AssistRoundMirror
  /** 本轮写入拒绝（kind 边界 / 依赖链 / 行不完整）；展示用，上限 8 条 */
  refusals: Ref<PropertySourceAssistRefusal[]>
  /** 清空拒绝记录（宿主打开面板/保存成功时调用；新一轮开始由 snapshot() 自动清空） */
  resetRefusals(): void
  /** 宿主「撤销本次填写」：恢复本轮开始前草稿；一次性；期间手改返回 false */
  undoRound(): boolean
  /** 宿主手改登记（配合面板 notifyDraftChanged：引擎作废在途+禁撤销，镜像同步说明） */
  noteManualChange(): void
  /** 组件 api 包装在 generate 返回后回传 fill 响应（维护「另有 M 项待补充」） */
  observeFill(resp: unknown): void
}

// ── 行组（契约 lists）：行读写 + 行操作 codec ───────────────────────────────────
type Row = Record<string, unknown>

const LOOKUP_PATHS: readonly string[] = ['field', 'operator', 'value.kind', 'value.field', 'value.property', 'value.value', 'value.parameter']
const PARAM_PATHS: readonly string[] = ['token', 'from', 'field', 'property']
const INPUT_PATHS: readonly string[] = ['inputId', 'from', 'property', 'value']

/** 行字段写入（展平路径 → 嵌套目标；值 undefined = 删除该键，行内其他键不动）。 */
function setRowField(target: Record<string, unknown>, path: string, value: unknown): void {
  const segs = path.split('.')
  let cur: Record<string, unknown> = target
  for (let i = 0; i < segs.length - 1; i++) {
    const seg = segs[i]
    if (!isPlainObject(cur[seg])) {
      if (value === undefined) return // 删除不存在的中间节点：无操作
      cur[seg] = {}
    }
    cur = cur[seg] as Record<string, unknown>
  }
  const leaf = segs[segs.length - 1]
  if (value === undefined) delete cur[leaf]
  else cur[leaf] = value
}

/** 展平路径读取（中间节点非对象 → undefined）。 */
function getRowField(source: Record<string, unknown>, path: string): unknown {
  let cur: unknown = source
  for (const seg of path.split('.')) {
    if (!isPlainObject(cur)) return undefined
    cur = cur[seg]
  }
  return cur
}

/** 源对象 → 行：按契约行字段展平路径取值（已配置的键含空串照实出网，行形状对模型可见）。 */
function readRowFields(group: RowGroup, source: unknown, row: Row): Row {
  const src = isPlainObject(source) ? source : {}
  for (const p of group.paths) {
    const value = getRowField(src, p)
    if (value === undefined) continue
    row[p] = cloneJson(value)
  }
  return row
}

/**
 * 按 base 行 diff 投影一行：只写本轮真正变化的行字段（行内未知键与未提及字段原样保留）。
 * 判别字段（契约 visibleWhen 的 field，如 value.kind / from）被本轮改写时，同时清理其他分支的
 * 残留字段——否则会出现「来源改成常量却留着旧属性引用」的半行；未提及的判别字段不动。
 * mapKey（params.token / inputs.inputId）不在值对象内重复：它是容器键，行身份由 rowId 承载。
 */
function projectRow(group: RowGroup, target: Record<string, unknown>, row: Row, base: Row): void {
  const paths = group.mapKey ? group.paths.filter(p => p !== group.mapKey) : group.paths
  const changed: string[] = []
  for (const p of paths) {
    const nextValue = row[p]
    if (nextValue === undefined && base[p] === undefined) continue
    if (deepEqual(nextValue, base[p])) continue
    if (nextValue === null) continue // 行字段无 clear 授权（契约 ai.clearable 未开启）
    setRowField(target, p, nextValue)
    changed.push(p)
  }
  const discriminator = group.discriminator
  if (!discriminator || !changed.includes(discriminator)) return
  const activeBranch = str(getRowField(target, discriminator)) // 行内展平路径读取（不是字面键）
  const keep = new Set(group.conditionalFields?.[activeBranch] ?? [])
  for (const [branch, fields] of Object.entries(group.conditionalFields ?? {})) {
    if (branch === activeBranch) continue
    for (const f of fields) if (!keep.has(f)) setRowField(target, f, undefined)
  }
}

interface RowGroup {
  /** 契约字段点路径（快照键；set/clear 的 field） */
  field: string
  /** 契约列表 id（行操作的 field，与后端同一口径） */
  listId: string
  /** 合法行字段（展平路径，与契约 list_def 一致） */
  paths: readonly string[]
  /** 行必填项（新增行缺一不落，避免半行） */
  required: readonly string[]
  /** map 容器（params/inputs）的键字段；数组容器（lookup.match）缺省 */
  mapKey?: string
  /**
   * 行内条件分支的判别字段（契约 visibleWhen 的 field）与各分支专属字段。
   * 判别字段在本次行操作中被改写时，清理其他分支的残留字段（同组内不产生旧字段残留）；
   * 未提及的判别字段与其他未知键一律不动。
   */
  discriminator?: string
  conditionalFields?: Record<string, readonly string[]>
  /** 源草稿 → 行数组（含稳定本地 rowId；只含已配置的键） */
  readRows(d: Record<string, unknown>): Row[]
  /** 行数组 diff 写回源草稿；返回是否发生写入 */
  writeRows(d: Record<string, unknown>, rows: Row[], base: Row[]): boolean
}

const LOOKUP_MATCH: RowGroup = {
  field: 'lookup.match',
  listId: 'lookupMatch',
  paths: LOOKUP_PATHS,
  required: ['field', 'operator', 'value.kind'],
  discriminator: 'value.kind',
  conditionalFields: {
    identityField: ['value.field'], property: ['value.property'],
    constant: ['value.value'], parameter: ['value.parameter'],
  },
  readRows(d) {
    const list = isPlainObject(d.lookup) && Array.isArray(d.lookup.match) ? d.lookup.match as unknown[] : []
    return list.map((raw, i) => readRowFields(LOOKUP_MATCH, raw, { rowId: 'm' + (i + 1) }))
  },
  writeRows(d, rows, base) {
    if (!isPlainObject(d.lookup)) d.lookup = { match: [], timeRange: null }
    const holder = d.lookup as Record<string, unknown>
    const host = Array.isArray(holder.match) ? holder.match as unknown[] : (holder.match = []) as unknown[]
    const byId = new Map(base.map((r, i) => [String(r.rowId), i] as const))
    const out = rows.map(r => {
      const i = byId.get(String(r.rowId))
      const target = (i === undefined ? {} : cloneJson(host[i] ?? {})) as Record<string, unknown>
      projectRow(LOOKUP_MATCH, target, r, i === undefined ? {} : base[i])
      return target
    })
    if (deepEqual(out, host)) return false
    host.splice(0, host.length, ...out) // 原位替换，保持数组引用（reactive 绑定稳定）
    return true
  },
}

const REDIS_PARAMS: RowGroup = {
  field: 'params',
  listId: 'keyParams',
  paths: PARAM_PATHS,
  required: ['token', 'from'],
  mapKey: 'token',
  discriminator: 'from',
  conditionalFields: { identityField: ['field'], property: ['property'] },
  readRows(d) {
    const params = isPlainObject(d.params) ? d.params : {}
    return Object.entries(params).map(([token, raw], i) =>
      readRowFields(REDIS_PARAMS, raw, { rowId: 'k' + (i + 1), token }))
  },
  writeRows(d, rows, base) {
    if (!isPlainObject(d.params)) d.params = {}
    const host = d.params as Record<string, unknown>
    const keyById = new Map(base.map(r => [String(r.rowId), str(r.token)] as const))
    const baseById = new Map(base.map(r => [String(r.rowId), r] as const))
    const out: Record<string, unknown> = {}
    for (const r of rows) {
      const oldKey = keyById.get(String(r.rowId))
      const token = str(r.token) || oldKey || ''
      if (!token) continue // 缺少占位符：不产生无名键（原因由调用方记入 refusals）
      const source = oldKey !== undefined && isPlainObject(host[oldKey]) ? host[oldKey] as Record<string, unknown> : {}
      const target = cloneJson(source)
      projectRow(REDIS_PARAMS, target, r, baseById.get(String(r.rowId)) ?? {})
      out[token] = target
    }
    if (deepEqual(out, host)) return false
    for (const k of Object.keys(host)) delete host[k]
    Object.assign(host, out)
    return true
  },
}

const FLOW_INPUTS: RowGroup = {
  field: 'inputs',
  listId: 'flowInputs',
  paths: INPUT_PATHS,
  required: ['inputId', 'from'],
  mapKey: 'inputId',
  discriminator: 'from',
  conditionalFields: { property: ['property'], constant: ['value'] },
  readRows(d) {
    const inputs = isPlainObject(d.inputs) ? d.inputs : {}
    return Object.entries(inputs).map(([inputId, raw], i) =>
      readRowFields(FLOW_INPUTS, raw, { rowId: 'i' + (i + 1), inputId }))
  },
  writeRows(d, rows, base) {
    if (!isPlainObject(d.inputs)) d.inputs = {}
    const host = d.inputs as Record<string, unknown>
    const keyById = new Map(base.map(r => [String(r.rowId), str(r.inputId)] as const))
    const baseById = new Map(base.map(r => [String(r.rowId), r] as const))
    const out: Record<string, unknown> = {}
    for (const r of rows) {
      const oldKey = keyById.get(String(r.rowId))
      const inputId = str(r.inputId) || oldKey || ''
      if (!inputId) continue
      const source = oldKey !== undefined && isPlainObject(host[oldKey]) ? host[oldKey] as Record<string, unknown> : {}
      const target = cloneJson(source)
      projectRow(FLOW_INPUTS, target, r, baseById.get(String(r.rowId)) ?? {})
      out[inputId] = target
    }
    if (deepEqual(out, host)) return false
    for (const k of Object.keys(host)) delete host[k]
    Object.assign(host, out)
    return true
  },
}

const ROW_GROUPS: readonly RowGroup[] = [LOOKUP_MATCH, REDIS_PARAMS, FLOW_INPUTS]

/** 各变体的契约字段键（kind 边界判定的单一来源）。 */
const VARIANT_KEYS: Record<PropertySourceAssistKind, readonly string[]> = {
  field: ['field', 'note'],
  database: ['connection', 'table', 'result.valueField', 'result.timestampField', 'lookup.match', 'note'],
  redis: ['connection', 'command', 'key', 'hashField', 'params', 'conversion', 'missing', 'note'],
  flow: ['flow', 'output', 'inputs', 'result.valueField', 'result.timestampField', 'note'],
}

/** 行字段字典 → 部分行（只收合法展平路径；null/undefined 不入行）。 */
function pickRowFields(group: RowGroup, fields: Record<string, unknown>): Row {
  const out: Row = {}
  for (const [key, value] of Object.entries(fields)) {
    if (!group.paths.includes(key)) continue
    if (value === undefined || value === null) continue
    out[key] = cloneJson(value)
  }
  return out
}

/**
 * 行组 codec（契约 codec 名 lookupMatchRows / redisKeyParams / flowInputBindings 的宿主实现）：
 * 行操作按 rowId 局部更新副本（未涉及行与未知键不动、不丢）；行组不属于当前变体 → 拒绝（kind 边界）。
 * 写入用契约列表键（draft[group.field]）而非嵌套路径——与白名单快照/后端 normalize_draft 同形态，
 * 落位时由 applyProjection 按依赖链决定是否真的写进宿主草稿。
 */
function rowGroupCodec(group: RowGroup, onRefuse: RecordRefusal, onKindBoundary: () => void): AutofillFieldCodec {
  return (draft, _field, value) => {
    if (!Array.isArray(draft[group.field])) {
      // 行组不属于当前来源类型（如 database 草稿收到 Redis 参数行）：拒绝并标记本轮为 kind 边界
      onRefuse(group.field, PROPERTY_SOURCE_KIND_REASON)
      onKindBoundary()
      throw new Error(PROPERTY_SOURCE_KIND_REASON)
    }
    const current = cloneJson(draft[group.field]) as Row[]
    const rowIdOf = (v: unknown): string => str(isPlainObject(v) ? v.rowId : '')
    let next: Row[]
    if (Array.isArray(value)) {
      // autofill/1 的列表只能按行修改（§6.3）：整组替换不提供，避免误删未知历史行
      throw new Error('列表内容只能通过 row.append/row.update/row.remove 修改')
    } else if (isPlainObject(value) && value.op === 'row.update') {
      const rowId = rowIdOf(value)
      const idx = current.findIndex(r => String(r.rowId) === rowId)
      if (idx < 0) throw new Error('未找到行 ' + (rowId || '（缺少 rowId）'))
      const patch = pickRowFields(group, isPlainObject(value.fields) ? value.fields : {})
      next = current.map((r, i) => (i === idx ? { ...r, ...patch } : r))
    } else if (isPlainObject(value) && value.op === 'row.remove') {
      const rowId = rowIdOf(value)
      if (!current.some(r => String(r.rowId) === rowId)) throw new Error('未找到行 ' + (rowId || '（缺少 rowId）'))
      next = current.filter(r => String(r.rowId) !== rowId)
    } else if (isPlainObject(value) && typeof value.localId === 'string') {
      const localId = str(value.localId)
      if (!localId || current.some(r => String(r.rowId) === localId)) throw new Error('行标识非法或重复：' + localId)
      next = [...current, { rowId: localId, ...pickRowFields(group, isPlainObject(value.fields) ? value.fields : {}) }]
    } else {
      throw new Error('不支持的行操作结构')
    }
    draft[group.field] = next
  }
}

/**
 * 契约字段 codec 注册表（引擎按 op.field 查；行组以「契约字段路径 + 列表 id」双键注册）。
 * 除行组外的字段也注册，原因有两条：
 *   1) 引擎对未注册字段走 identity 点路径直写（setByPath 建嵌套对象），而契约与后端白名单的口径是
 *      「result.valueField」这样的**扁平键**（normalize_draft 按键匹配）。注册后统一写契约键，
 *      快照与落位同形态（读取侧仍兼容引擎 identity/clear 产生的嵌套形态）。
 *   2) kind 边界必须在**写副本之前**拦住：其他来源类型的键该条操作即失败、记录原因并置本轮
 *      kind 边界标记（副本该键零改动），applyProjection 随后整轮不落任何键——不隐式切换来源类型、
 *      不清空既有配置（需求 §3 末段）。后端按同一契约在服务端同样拒绝该操作（转 unresolved）。
 */
function buildCodecs(onRefuse: RecordRefusal, onKindBoundary: () => void): Record<string, AutofillFieldCodec> {
  const out: Record<string, AutofillFieldCodec> = {}
  for (const g of ROW_GROUPS) {
    const codec = rowGroupCodec(g, onRefuse, onKindBoundary)
    out[g.field] = codec
    out[g.listId] = codec
  }
  const contractFields = new Set<string>(['kind', ...ROW_GROUPS.map(g => g.field)])
  for (const keys of Object.values(VARIANT_KEYS)) for (const k of keys) contractFields.add(k)
  for (const field of contractFields) {
    if (out[field]) continue
    out[field] = (draft, _field, value) => {
      if (field === 'kind') { // kind 不由建议修改（需求 §3 末段）：切换由用户在表单显式操作
        onRefuse('kind', PROPERTY_SOURCE_KIND_REASON)
        onKindBoundary()
        throw new Error(PROPERTY_SOURCE_KIND_REASON)
      }
      const own = VARIANT_KEYS[propertySourceAssistKind(draft) as PropertySourceAssistKind] ?? []
      if (!own.includes(field)) {
        onRefuse(field, PROPERTY_SOURCE_KIND_REASON)
        onKindBoundary()
        throw new Error(PROPERTY_SOURCE_KIND_REASON)
      }
      draft[field] = value // 契约扁平键；依赖链核验在 applyProjection
    }
  }
  return out
}

/** 契约键读取：引擎 identity 点路径写入的嵌套形态优先，其后回退到契约扁平键。 */
function contractValue(source: Record<string, unknown>, field: string): unknown {
  let cur: unknown = source
  let nested = true
  for (const seg of field.split('.')) {
    if (!isPlainObject(cur) || !(seg in cur)) { nested = false; break }
    cur = cur[seg]
  }
  if (nested) return cur
  return field in source ? source[field] : undefined
}

// ── 契约形态白名单快照（按 kind 分派） ─────────────────────────────────────────
/** 白名单快照：kind + 当前变体键 + note；view-model 临时字段与白名单外内容不出网。 */
function draftSnapshot(d: any, kind: PropertySourceAssistKind, note: string): Record<string, unknown> {
  const snap: Record<string, unknown> = { kind }
  if (kind === 'field') {
    snap.field = str(d?.field)
  } else if (kind === 'database') {
    snap.connection = str(d?.connection)
    snap.table = str(d?.table)
    snap['result.valueField'] = str(d?.result?.valueField)
    snap['result.timestampField'] = str(d?.result?.timestampField)
    snap['lookup.match'] = LOOKUP_MATCH.readRows(d)
  } else if (kind === 'redis') {
    // 目标快照「还原为纯 id」：来源形态出 draft.source，连接形态出 connection（见 applyConnection）
    snap.connection = str(d?.source || d?.connection)
    snap.command = str(d?.command || 'GET')            // 与表单展示默认一致
    snap.key = str(d?.key)
    snap.hashField = str(d?.hashField)
    snap.params = REDIS_PARAMS.readRows(d)
    snap.conversion = str(d?.conversion || 'number')   // 与表单展示默认一致
    snap.missing = str(d?.missing || 'null')
  } else {
    snap.flow = str(d?.flow)
    snap.output = str(d?.output)
    snap.inputs = FLOW_INPUTS.readRows(d)
    snap['result.valueField'] = str(d?.result?.valueField)
    snap['result.timestampField'] = str(d?.result?.timestampField)
  }
  snap.note = note
  return snap
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

/** 结果映射容器（database/flow 的 result.*）按需建骨架，保持既有键不动。 */
function ensureResult(d: any): void {
  if (!isPlainObject(d.result)) d.result = { valueField: '', timestampField: '' }
}

// ── 依赖链写回（kind 分派） ───────────────────────────────────────────────────
const CONN_NEEDS_TABLE = '切换数据连接会留下未提及的旧表与字段：本次未应用连接切换，请先确认要使用的表（或先回答补问）。'
const anchorNeedsDeps = (names: string[]): string =>
  `切换连接或表需要同时给出${names.join('、')}，否则会保留旧连接/旧表下已失效的配置；本次未应用切换，请补齐后重试。`
const NEEDS_TABLE = '需要先确定数据连接与表，本次未写入该字段。'
const NEEDS_VALUE_FIELD = '需要先确定取值字段，本次未写入该字段。'
const NEEDS_KEY = '需要先确定 Key 模板，本次未写入参数绑定。'
const NEEDS_FLOW = '需要先确定函数编排，本次未写入该字段。'
const NEEDS_OUTPUT = '需要先确定取值输出，本次未写入该字段。'
const HASH_NEEDS_HGET = 'Hash 字段仅在读取方式为 HGET 时可用，本次未写入。'

type RecordRefusal = (field: string, reason: string) => void

/** 行组写回：先按行校验（不完整行不落、既有行原样保留），再 diff 写回宿主草稿。 */
function applyRowGroup(d: any, group: RowGroup, nextRows: Row[], record: RecordRefusal): void {
  const base = group.readRows(d)
  const baseIds = new Set(base.map(r => String(r.rowId)))
  const rows: Row[] = []
  for (const row of nextRows) {
    const isNew = !baseIds.has(String(row.rowId))
    let reason: string | null = null
    if (isNew) {
      for (const p of group.required) {
        if (!str(row[p])) { reason = `新增行缺少「${ROW_FIELD_LABELS[p] ?? p}」，本次未写入该行。`; break }
      }
    }
    if (!reason && group.mapKey && !str(row[group.mapKey])) {
      reason = `行缺少「${ROW_FIELD_LABELS[group.mapKey] ?? group.mapKey}」，无法定位该行，本次未写入。`
    }
    if (reason) { record(group.field, reason); continue }
    rows.push(row)
  }
  group.writeRows(d, rows, base)
}

function applyDatabase(next: Record<string, unknown>, before: Record<string, unknown>, d: any, opts: PropertySourceAssistOptions, record: RecordRefusal): void {
  const curConnection = str(d?.connection)
  const curTable = str(d?.table)
  const curValueField = str(d?.result?.valueField)
  const curTimestampField = str(d?.result?.timestampField)
  const curMatch = LOOKUP_MATCH.readRows(d)
  const pConnection = str(next.connection)
  const pTable = str(next.table)
  const rawValueField = contractValue(next, 'result.valueField')
  const rawTimestampField = contractValue(next, 'result.timestampField')
  const pValueField = typeof rawValueField === 'string' ? rawValueField : ''
  const pTimestampField = typeof rawTimestampField === 'string' ? rawTimestampField : ''
  const rawMatchValue = contractValue(next, 'lookup.match')
  const pMatch = Array.isArray(rawMatchValue) ? rawMatchValue as Row[] : undefined
  const connChanged = !!pConnection && pConnection !== curConnection
  const tableChanged = !!pTable && pTable !== curTable
  /** 本轮是否真的更新了该依赖字段（与响应前的快照比较；仅回显旧值不算更新）。 */
  const updated = (field: string): boolean => !deepEqual(contractValue(next, field), contractValue(before, field))
  let blocked = ''
  if (connChanged && curTable && !pTable) {
    blocked = CONN_NEEDS_TABLE
  } else if (connChanged || tableChanged) {
    // 改连接/改表会让未提及的旧表旧字段失效：依赖字段必须同组更新（给出新表下的值），
    // 否则整组不落并说明——不静默丢弃、也不保留「新表配旧字段」的假象（A08）。
    const stale: string[] = []
    if (curValueField && !updated('result.valueField')) stale.push('取值字段')
    if (curTimestampField && !updated('result.timestampField')) stale.push('时间字段')
    if (curMatch.length && !updated('lookup.match')) stale.push('匹配条件')
    if (stale.length) blocked = anchorNeedsDeps(stale)
  }
  if (blocked) {
    record(connChanged ? 'connection' : 'table', blocked)
    return
  }
  if (connChanged) applyConnection(d, 'database', pConnection, opts)
  if (tableChanged) d.table = pTable
  const effTable = str(d?.table)
  if (pValueField) {
    if (!effTable) record('result.valueField', NEEDS_TABLE)          // 无半组：缺表不落字段
    else if (pValueField !== curValueField) { ensureResult(d); d.result.valueField = pValueField }
  } else if (rawValueField === null) {
    ensureResult(d); d.result.valueField = ''                        // 显式清空（nullable）
  }
  if (pTimestampField) {
    const valueField = pValueField && effTable ? pValueField : str(d?.result?.valueField)
    if (!valueField) record('result.timestampField', NEEDS_VALUE_FIELD)
    else if (pTimestampField !== curTimestampField) { ensureResult(d); d.result.timestampField = pTimestampField }
  } else if (rawTimestampField === null) {
    ensureResult(d); d.result.timestampField = ''
  }
  if (pMatch) {
    if (!effTable) record('lookup.match', NEEDS_TABLE)
    else applyRowGroup(d, LOOKUP_MATCH, pMatch, record)
  }
}

function applyRedis(next: Record<string, unknown>, d: any, opts: PropertySourceAssistOptions, record: RecordRefusal): void {
  const pConnection = str(next.connection)
  const pCommand = str(next.command)
  const pKey = str(next.key)
  const pHashField = typeof next.hashField === 'string' ? next.hashField : ''
  const pParams = Array.isArray(next.params) ? next.params as Row[] : undefined
  const pConversion = str(next.conversion)
  const pMissing = str(next.missing)
  if (pConnection) applyConnection(d, 'redis', pConnection, opts)
  if (pCommand && pCommand !== str(d?.command)) d.command = pCommand
  if (pKey && pKey !== str(d?.key)) d.key = pKey
  if (pHashField) {
    if (str(d?.command || 'GET') === 'HGET') { if (pHashField !== str(d?.hashField)) d.hashField = pHashField }
    else record('hashField', HASH_NEEDS_HGET)
  } else if (next.hashField === null) {
    d.hashField = '' // 显式清空（nullable+clearable）
  }
  if (pConversion && pConversion !== str(d?.conversion)) d.conversion = pConversion
  if (pMissing && pMissing !== str(d?.missing)) d.missing = pMissing
  if (pParams) {
    if (!str(d?.key)) record('params', NEEDS_KEY)
    else applyRowGroup(d, REDIS_PARAMS, pParams, record)
  }
}

function applyFlow(next: Record<string, unknown>, d: any, record: RecordRefusal): void {
  const pFlow = str(next.flow)
  const pOutput = str(next.output)
  const pInputs = Array.isArray(next.inputs) ? next.inputs as Row[] : undefined
  const rawValueField = contractValue(next, 'result.valueField')
  const rawTimestampField = contractValue(next, 'result.timestampField')
  const pValueField = typeof rawValueField === 'string' ? rawValueField : ''
  const pTimestampField = typeof rawTimestampField === 'string' ? rawTimestampField : ''
  // 只写草稿值：不触发组件 selectFlow 的拉取编排详情/清空重置副作用
  if (pFlow && pFlow !== str(d?.flow)) d.flow = pFlow
  const effFlow = str(d?.flow)
  if (pOutput) {
    if (!effFlow) record('output', NEEDS_FLOW)
    else if (pOutput !== str(d?.output)) d.output = pOutput
  }
  if (pInputs) {
    if (!effFlow) record('inputs', NEEDS_FLOW)
    else applyRowGroup(d, FLOW_INPUTS, pInputs, record)
  }
  const effOutput = str(d?.output)
  if (pValueField) {
    if (!effOutput) record('result.valueField', NEEDS_OUTPUT)
    else if (pValueField !== str(d?.result?.valueField)) { ensureResult(d); d.result.valueField = pValueField }
  } else if (rawValueField === null) {
    ensureResult(d); d.result.valueField = ''                        // 显式清空（nullable+clearable）
  }
  if (pTimestampField) {
    const valueField = pValueField && effOutput ? pValueField : str(d?.result?.valueField)
    if (!valueField) record('result.timestampField', NEEDS_VALUE_FIELD)
    else if (pTimestampField !== str(d?.result?.timestampField)) { ensureResult(d); d.result.timestampField = pTimestampField }
  } else if (rawTimestampField === null) {
    ensureResult(d); d.result.timestampField = ''
  }
}

/** 其他变体字段出现在本轮 → kind 边界键（拒绝并给原因，绝不隐式切换）。 */
function foreignKeyOf(kind: PropertySourceAssistKind, next: Record<string, unknown>): string {
  const own = new Set(VARIANT_KEYS[kind])
  for (const [key, value] of Object.entries(next)) {
    if (key === 'kind' || value === undefined || own.has(key)) continue
    if (Object.values(VARIANT_KEYS).some(keys => keys.includes(key))) return key
  }
  return ''
}

/**
 * 整稿落位：note → kind 边界 → 依赖链写回（只改本地草稿，绝不触发保存）。
 * `kindBoundaryHit`：本轮 codec 阶段已判定为「另一种来源类型」的请求（kind 变更或他变体字段）——
 * 此时连同名共享键（如 database/redis 都有 connection）也不落：那属于另一种来源类型的语义，
 * 照写会把用户没要的改动混进当前配置（需求 §3 末段「不能在模型响应中隐式切换并清除整份已有配置」）。
 */
function applyProjection(next: unknown, before: Record<string, unknown>, opts: PropertySourceAssistOptions, record: RecordRefusal, kindBoundaryHit: () => boolean): void {
  const d = opts.draft() as Record<string, unknown> | null | undefined
  if (!d || !next || typeof next !== 'object') return
  const values = next as Record<string, unknown>
  const kind = propertySourceAssistKind(d)
  if (!kind) return
  // note 是四变体共有的说明字段：即使本轮混入其他来源类型的内容也照常落位（§4.3「只填独立合法组」）
  if (typeof values.note === 'string') opts.setNote(values.note)
  else if (values.note === null) opts.setNote('')
  const kindMismatch = values.kind !== undefined && str(values.kind) !== kind
  const foreign = foreignKeyOf(kind, values)
  if (kindMismatch) record('kind', PROPERTY_SOURCE_KIND_REASON)
  if (foreign) record(foreign, PROPERTY_SOURCE_KIND_REASON)
  // kind 边界命中：本轮是按另一种来源类型提的要求 → 该类型的键一个都不落（不隐式切换、不清空既有配置），
  // 「请先切换来源类型」由拒绝原因如实展示；用户切换 kind 后重发即可（服务端同样按契约拒绝）。
  if (kindMismatch || foreign || kindBoundaryHit()) return
  if (kind === 'field') {
    const field = str(values.field)
    if (field && field !== str(d.field)) d.field = field
    return
  }
  if (kind === 'database') applyDatabase(values, before, d, opts, record)
  else if (kind === 'redis') applyRedis(values, d, opts, record)
  else applyFlow(values, d, record)
}

/** 属性取值来源表单的宿主 binding 工厂：契约变体快照 + 行组 codec + 依赖链写回 + 节点级快照/恢复。 */
export function propertySourceBinding(opts: PropertySourceAssistOptions): PropertySourceAssistHostBinding {
  const refusals = ref<PropertySourceAssistRefusal[]>([])
  const record: RecordRefusal = (field, reason) => {
    if (refusals.value.some(r => r.field === field && r.reason === reason)) return
    if (refusals.value.length >= 8) refusals.value = refusals.value.slice(-7)
    refusals.value = [...refusals.value, { field, reason }]
  }
  let kindBoundaryHit = false // 本轮 codec 阶段命中 kind 边界（他变体字段/kind 变更）
  const control: AssistRoundControl = createRoundMirror(PROPERTY_SOURCE_FIELD_LABELS)
  const expected: AssistRoundExpectation = {
    formId: 'propertySource', targetKind: 'propertySource', targetId: opts.targetId || '',
    schemaVersion: FORM_PROPERTY_SOURCE.schemaVersion, schemaDigest: FORM_PROPERTY_SOURCE.schemaDigest,
  }
  /** 当前草稿的契约形态快照（不可辅助 kind → 空快照） */
  const snapshotOf = (): Record<string, unknown> => {
    const d = opts.draft()
    const kind = propertySourceAssistKind(d)
    if (!kind) return {}
    return draftSnapshot(d, kind, str(opts.noteDraft()))
  }
  /** 恢复快照：原位替换键值，保持 draft 对象身份（reactive 引用与子组件绑定稳定） */
  const restoreFrom = (snap: unknown): void => {
    const d = opts.draft()
    if (!d || !snap || typeof snap !== 'object') return
    const s = snap as { draft?: unknown; note?: unknown }
    const src = isPlainObject(s.draft) ? s.draft : {}
    const target = d as Record<string, unknown>
    for (const k of Object.keys(target)) delete target[k]
    Object.assign(target, cloneJson(src))
    if (typeof s.note === 'string') opts.setNote(s.note)
  }
  return {
    space: 'project',
    projectId: opts.projectId || '',
    targetKind: 'propertySource',
    targetId: opts.targetId || '',
    contextTitle: opts.contextTitle || '属性取值来源辅助填写',
    formId: 'propertySource',
    codecs: buildCodecs(record, () => { kindBoundaryHit = true }),
    contractInfo: () => ({ schemaVersion: FORM_PROPERTY_SOURCE.schemaVersion, schemaDigest: FORM_PROPERTY_SOURCE.schemaDigest }),
    refusals,
    resetRefusals: () => { refusals.value = [] },
    round: control.round,
    draft: snapshotOf,
    /** 新版整稿通道：依赖链 + kind 边界 + 行组 diff 都在这里落位 */
    applyDraft(next) {
      if (!next || typeof next !== 'object') return
      const before = snapshotOf()
      const boundary = kindBoundaryHit // 由 codec 阶段置位；本轮落位后复位
      kindBoundaryHit = false
      applyProjection(next, before, opts, record, () => boundary)
      control.recordApply(before, snapshotOf())
    },
    /** 旧版通道（顶层变更值）：并上当前快照后走同一落位路径 */
    apply(values) {
      if (!values || typeof values !== 'object') return
      const before = snapshotOf()
      const boundary = kindBoundaryHit
      kindBoundaryHit = false
      applyProjection({ ...before, ...values as Record<string, unknown> }, before, opts, record, () => boundary)
      control.recordApply(before, snapshotOf())
    },
    /**
     * 回填前快照：宿主草稿整体 JSON 克隆（含白名单外字段）+ 说明，恢复零丢失。
     * 注意不在此时清空 refusals：引擎在 applyOperations（codec 阶段）**之后**才调 snapshot()，
     * 在这里清空会把同一轮刚记录的拒绝原因抹掉。本轮拒绝由宿主在打开面板时经 resetRefusals() 清空。
     */
    snapshot() {
      const snap = { draft: cloneJson(opts.draft() ?? {}), note: str(opts.noteDraft()) }
      control.beginRound(snap) // 引擎撤销单元起点 = 本轮开始前草稿（宿主撤销恢复到这里）
      return snap
    },
    restore: restoreFrom,
    undoRound: () => {
      const snap = control.snapshotOf()
      if (!opts.draft() || !control.round.canUndo || snap === null) return false
      restoreFrom(snap)
      control.resetAfterUndo()
      return true
    },
    noteManualChange: () => control.noteManualChange(),
    observeFill: resp => control.observeFill(expected, resp),
  }
}
