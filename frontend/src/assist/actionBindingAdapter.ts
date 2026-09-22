// 动作接口映射（P6）编辑弹窗的整表自动填写宿主适配（2026-09-22 T8 改版）。
// 契约唯一来源：contracts/forms/actionBinding.json（前端生成物 ./formContracts.gen.ts，codecs=[
// 'actionParamRows']）；引擎对接面 ./formAutofill.ts 的 AutofillHostBinding；写法镜像
// ./ontologyBindings.ts（T5 createRoundMirror）与 ./propertyBinding.ts（refusals 拒绝记录）。
// 设计要点：
//   * 契约白名单：method/path/bodyFormat/description/note 六个顶层字段 + parameters 列表
//     （actionParams 行：name/in/value{from,inputId,property,valueType,value}，rowIdScope=local）。
//     契约里没有 auth——认证与凭据字段（auth.*）按 ai.sensitive 红线在前端 binding 双保险拒绝：
//     draft() 绝不包含 auth（不出网）；apply/applyDraft 遇到 auth 键拒绝写入并记入 refusals
//     （宿主状态条展示原因）。后端契约白名单是第一道，本层是第二道。
//   * 行结构双向映射（契约 ↔ actionHttpModel.ApiParam）：契约 inputId↔组件 inputId/inputName、
//     契约 property↔组件 propertyId、契约 valueType↔组件 type；行 id 是客户端本地稳定 rowId
//     （rowIdScope=local），快照按原样出网供 row.update/remove 定位，不接受模型生成持久 id——
//     row.append 冲突/缺失时由 newParamId 补齐（与组件 addParam 同一机制）。
//   * parameters 经 actionParamRows codec 精确落行：set=显式整组（服务端授权下发）、row.append/
//     row.update/row.remove 按 rowId 定位——未命中行抛错（该操作失败，不中断其余独立操作）；
//     未涉及的行原样保留（A13：修改只落指定行，未知行不丢失）。in/from/valueType 枚举在这里
//     校验，非法行抛错拒绝。
//   * snapshot()/restore() 面向客户端撤销：整草稿 JSON 克隆（含 auth——它只在浏览器内参与撤销
//     保真，永不出网）；restore 原位替换键值，保持草稿对象身份（弹窗模板绑定不换引用）。
//   * 保存边界：apply/applyDraft 只把合法操作写进弹窗本地草稿（「已填写 N 项，尚未保存」由宿主
//     状态条提示），绝不调用 form-save/commit-now/touch；持久化由用户点「保存」走既有
//     saveApi→toImplementation→form-save 链路。绝不执行接口：本适配层没有任何网络调用，
//     组件的「请求示意」为纯文本拼装。
import { ref, type Ref } from 'vue'
import { FORM_ACTION_BINDING } from './formContracts.gen'
import { cloneJson, type AutofillFieldCodec } from './formAutofill'
import type { AutofillHostBinding } from './useAssistPanel'
import { createRoundMirror, type AssistRoundControl, type AssistRoundExpectation, type AssistRoundMirror } from './ontologyBindings'
import { newParamId } from '../project/actionHttpModel'

/** 一次写入拒绝（宿主在状态条区域展示） */
export interface ActionBindingAssistRefusal { field: string; reason: string }

/** auth.* 敏感字段拒绝原因（binding 层与宿主共用同一文案） */
export const ACTION_BINDING_SENSITIVE_REASON = '认证与凭据（auth.*）是敏感字段，不能通过自动填写修改；请在「认证配置」中手动维护。'

/** actionBindingAssistBinding 返回的扩展 binding（引擎兼容 + 状态条镜像 + 拒绝记录） */
export interface ActionBindingAssistBinding extends AutofillHostBinding {
  formId: 'actionBinding'
  /** parameters 列表的 actionParamRows codec（其余顶层字段 identity 直写） */
  codecs: Record<string, AutofillFieldCodec>
  /** 状态条镜像（reactive；模板直接渲染，引擎仍是唯一权威） */
  round: AssistRoundMirror
  /** 本轮打开以来的写入拒绝（auth.* 敏感/不可清空字段等）；展示用，上限 8 条 */
  refusals: Ref<ActionBindingAssistRefusal[]>
  /** 清空拒绝记录（保存成功/重新打开时由宿主调用） */
  resetRefusals(): void
  /** 宿主「撤销本次填写」：恢复本轮开始前草稿；一次性；期间手改返回 false */
  undoRound(): boolean
  /** 宿主手改登记（配合面板 notifyDraftChanged：引擎作废在途+禁撤销，镜像同步说明） */
  noteManualChange(): void
  /** 组件 api 包装在 generate 返回后回传 fill 响应（维护「另有 M 项待补充」） */
  observeFill(resp: unknown): void
}

export interface ActionBindingAssistOptions {
  /** 项目 id（项目区场景必填，随 context 请求出网） */
  projectId: string
  /** 编辑目标：已保存绑定为 '<对象类型裸id>:<动作id>'（后端据此出标题并核验归属）；
   *  未保存的新配置传 ''（后端按「新建动作接口映射」出标题） */
  targetId: string
  /** 未取到上下文时的兜底标题 */
  contextTitle?: string
  /** 弹窗本地接口草稿 getter（actionHttpModel.ApiImplementation 形态；null=只读态：快照为空、apply/restore 无操作） */
  draft: () => any
  /** 「项目说明」本地草稿 getter（弹窗 noteDraft） */
  note: () => string
  /** 「项目说明」写回（弹窗 noteDraft） */
  setNote: (v: string) => void
}

// 契约枚举（单一来源：T1 生成物；后端按同一契约校验 operations，这里做落行前的最后校验）
const METHOD_ENUM = new Set(FORM_ACTION_BINDING.fields.find(f => f.id === 'method')?.enum ?? [])
const BODY_FORMAT_ENUM = new Set(FORM_ACTION_BINDING.fields.find(f => f.id === 'bodyFormat')?.enum ?? [])
const ITEM_FIELDS = FORM_ACTION_BINDING.lists.find(l => l.id === 'actionParams')?.item.fields ?? []
const IN_ENUM = new Set(ITEM_FIELDS.find(f => f.id === 'in')?.enum ?? [])
const VALUE_GROUP = ITEM_FIELDS.find(f => f.id === 'value')?.fields ?? []
const FROM_ENUM = new Set(VALUE_GROUP.find(f => f.id === 'from')?.enum ?? [])
const VALUE_TYPE_ENUM = new Set(VALUE_GROUP.find(f => f.id === 'valueType')?.enum ?? [])

const asString = (v: unknown): v is string => typeof v === 'string'

/** 组件参数行 → 契约行（出网快照形态：{rowId,name,in,value}；绝不包含 auth）。 */
function rowOut(row: any): Record<string, unknown> {
  const r = row && typeof row === 'object' ? row : {}
  const v = r.value && typeof r.value === 'object' ? r.value : {}
  const from = String(v.from ?? '')
  const value: Record<string, unknown> = { from }
  if (from === 'actionInput') {
    const refId = String(v.inputId || v.inputName || '')
    if (refId) value.inputId = refId
  } else if (from === 'property') {
    if (v.propertyId) value.property = String(v.propertyId)
  } else if (from === 'constant') {
    if (v.type) value.valueType = String(v.type)
    if (v.value !== undefined && v.value !== null) value.value = v.value
  }
  return { rowId: String(r.id ?? ''), name: String(r.name ?? ''), in: String(r.in ?? ''), value }
}

/**
 * 契约 value 组 → 组件 ParamValue（inputId↔inputId、property→propertyId、valueType→type）。
 * 两种键形都接受（契约键 property/valueType 与组件键 propertyId/type）：codec 先把操作字段转成
 * 组件键形，随后整稿落回时还要再走一次本函数做逐行规整——只认契约键会把刚转好的组件键丢掉。
 */
function valueIn(raw: unknown): Record<string, any> {
  const v = raw && typeof raw === 'object' ? raw as Record<string, unknown> : {}
  const from = String(v.from ?? '')
  if (from === 'actionInput') {
    const out: Record<string, any> = { from }
    const refId = String(v.inputId ?? v.inputName ?? '')
    if (refId) out.inputId = refId
    return out
  }
  if (from === 'property') {
    const out: Record<string, any> = { from }
    const p = String(v.property ?? v.propertyId ?? '')
    if (p) out.propertyId = p
    return out
  }
  if (from === 'constant') {
    const out: Record<string, any> = { from, type: String(v.valueType ?? v.type ?? 'string') }
    out.value = v.value === undefined || v.value === null ? '' : v.value
    return out
  }
  return { from } // instanceId 与未知来源原样保留，交保存校验
}

/** 契约行字段校验（枚举非法抛错 → 该操作失败，其余独立操作照常）。 */
function normFields(fields: Record<string, unknown>): { name: string; in: string; value: Record<string, any> } {
  const name = fields.name
  if (name !== undefined && !asString(name)) throw new Error('参数名必须是文本')
  const inVal = fields.in
  if (inVal !== undefined && !IN_ENUM.has(String(inVal))) throw new Error('未知参数位置：' + String(inVal))
  const value = fields.value
  if (value !== undefined) {
    const from = (value && typeof value === 'object') ? String((value as Record<string, unknown>).from ?? '') : ''
    if (!FROM_ENUM.has(from)) throw new Error('未知取值来源：' + String(from))
    const vt = (value && typeof value === 'object') ? (value as Record<string, unknown>).valueType : undefined
    if (vt !== undefined && !VALUE_TYPE_ENUM.has(String(vt))) throw new Error('未知固定值类型：' + String(vt))
  }
  return {
    name: asString(name) ? name : '',
    in: inVal === undefined ? '' : String(inVal),
    value: value === undefined ? { from: '' } : valueIn(value),
  }
}

/** 行 id 去重：localId 冲突/缺失时由 newParamId 补齐（不接受模型生成持久 id）。 */
function uniqueRowId(preferred: string, taken: Set<string>): string {
  let id = preferred && !taken.has(preferred) ? preferred : ''
  while (!id) {
    const candidate = newParamId()
    if (!taken.has(candidate)) id = candidate
  }
  taken.add(id)
  return id
}

/**
 * actionParamRows codec：在引擎草稿副本（契约行形态 {rowId,name,in,value}）上精确落行。
 * 引擎按操作类型传入三种值（formAutofill.applyOperations）：
 *   * set → 整组数组（服务端授权下发的显式整组；行 rowId 缺失/冲突按本地机制补齐）；
 *   * row.append → {localId, fields}（**没有 op 键**，不能与行操作混判）；
 *   * row.update/row.remove → 整条操作（含 op 与 rowId）。
 * row.update/remove 必须命中草稿现有行（rowId 由宿主快照提供，不接受模型生成的持久 id）；
 * 未命中即抛错（该操作失败，由引擎记 failures，不中断其余独立操作）；未涉及的行原样保留（A13）。
 */
const parametersCodec: AutofillFieldCodec = (d, field, value) => {
  if (field !== 'parameters') throw new Error('actionParamRows 只服务 parameters 字段')
  if (!Array.isArray(d.parameters)) d.parameters = []
  const list = d.parameters as Record<string, unknown>[]
  const obj = value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, any> : null
  const op = obj && typeof obj.op === 'string' ? obj.op : ''
  if (op === 'row.remove' || op === 'row.update') {
    const rowId = String(obj?.rowId ?? '')
    const idx = list.findIndex(r => String(r.rowId ?? '') === rowId)
    if (idx < 0) throw new Error('未找到参数行 ' + (rowId || '（空）'))
    if (op === 'row.remove') { list.splice(idx, 1); return }
    const fields = obj?.fields && typeof obj.fields === 'object' ? obj.fields as Record<string, unknown> : {}
    const norm = normFields(fields)
    const row = list[idx]
    if (fields.name !== undefined) row.name = norm.name
    if (fields.in !== undefined) row.in = norm.in
    if (fields.value !== undefined) row.value = norm.value
    return
  }
  if (op) throw new Error('未知行操作：' + op)
  if (obj && ('localId' in obj || 'fields' in obj)) { // row.append：{localId, fields}
    const fields = obj.fields && typeof obj.fields === 'object' ? obj.fields as Record<string, unknown> : {}
    const norm = normFields(fields)
    const taken = new Set(list.map(r => String(r.rowId ?? '')))
    list.push({ rowId: uniqueRowId(String(obj.localId ?? ''), taken), name: norm.name, in: norm.in || 'body', value: norm.value })
    return
  }
  if (Array.isArray(value)) { // set：显式整组（服务端授权下发；行 id 缺失/冲突按本地机制补齐）
    const taken = new Set<string>()
    d.parameters = value.map(raw => {
      const r = raw && typeof raw === 'object' ? raw as Record<string, unknown> : {}
      const norm = normFields({ name: r.name, in: r.in, value: r.value })
      return { rowId: uniqueRowId(String(r.rowId ?? ''), taken), name: norm.name, in: norm.in || 'body', value: norm.value }
    })
    return
  }
  throw new Error('parameters 仅接受行操作或显式整组')
}

/** 契约行 → 组件 ApiParam（id 缺失/组内重复由 newParamId 补齐）。 */
function componentRowOf(raw: unknown, taken: Set<string>): Record<string, any> {
  const r = raw && typeof raw === 'object' ? raw as Record<string, unknown> : {}
  const norm = normFields({ name: r.name, in: r.in, value: r.value })
  return { id: uniqueRowId(String(r.rowId ?? ''), taken), name: norm.name, in: norm.in || 'body', value: norm.value }
}

/** 动作接口映射编辑弹窗的宿主 binding 工厂：契约快照 + 行 codec + auth 双保险 + 草稿级快照/恢复。 */
export function actionBindingAssistBinding(opts: ActionBindingAssistOptions): ActionBindingAssistBinding {
  const control: AssistRoundControl = createRoundMirror(Object.fromEntries(FORM_ACTION_BINDING.fields.map(f => [f.id, f.label ?? f.id])))
  const expected: AssistRoundExpectation = {
    formId: 'actionBinding', targetKind: 'actionBinding', targetId: opts.targetId || '',
    schemaVersion: FORM_ACTION_BINDING.schemaVersion, schemaDigest: FORM_ACTION_BINDING.schemaDigest,
  }
  const refusals = ref<ActionBindingAssistRefusal[]>([])
  const record = (field: string, reason: string): void => {
    if (refusals.value.length >= 8) refusals.value = refusals.value.slice(-7)
    refusals.value = [...refusals.value, { field, reason }]
  }
  /** 契约草稿 → 弹窗草稿投影：顶层字段直写/清空，parameters 整组替换；auth.* 一律拒绝。 */
  const applyProjection = (next: Record<string, unknown>): void => {
    const d = opts.draft()
    if (!d || !next || typeof next !== 'object') return
    // 敏感双保险：契约白名单外出现的 auth 键（含点路径）拒绝写入并记录（后端校验是第一道）
    if (Object.keys(next).some(k => k === 'auth' || k.startsWith('auth.'))) record('auth', ACTION_BINDING_SENSITIVE_REASON)
    if (next.method !== undefined) {
      if (asString(next.method) && METHOD_ENUM.has(next.method)) d.method = next.method
      else if (next.method !== null) record('method', '未知请求方式：' + String(next.method))
      else record('method', '请求方式不可清空。')
    }
    if (next.path !== undefined) {
      if (asString(next.path)) d.path = next.path
      else if (next.path !== null) record('path', '接口地址必须是文本')
      else record('path', '接口地址不可清空。')
    }
    if (next.bodyFormat !== undefined) {
      if (asString(next.bodyFormat) && BODY_FORMAT_ENUM.has(next.bodyFormat)) d.bodyFormat = next.bodyFormat
      else if (next.bodyFormat !== null) record('bodyFormat', '未知请求体格式：' + String(next.bodyFormat))
      else record('bodyFormat', '请求体格式不可清空。')
    }
    // 接口说明/项目说明 nullable+clearable：字符串直写；null=用户明确要求清空 → 置空串
    if (asString(next.description)) d.description = next.description
    else if (next.description === null) d.description = ''
    else if (next.description !== undefined) record('description', '接口说明必须是文本')
    if (asString(next.note)) opts.setNote(next.note)
    else if (next.note === null) opts.setNote('')
    else if (next.note !== undefined) record('note', '说明必须是文本')
    if (next.parameters !== undefined) {
      if (Array.isArray(next.parameters)) {
        const taken = new Set<string>()
        d.parameters = next.parameters.map(r => componentRowOf(r, taken))
      } else if (next.parameters === null) record('parameters', '请求参数不可清空；如需删除某行请明确指出该行。')
      else record('parameters', '请求参数必须是列表')
    }
  }
  /** 恢复快照（宿主撤销与引擎 restore 同一实现）：原位替换键值，保持草稿对象身份（reactive 引用不换）。 */
  const restoreSnap = (snap: unknown): void => {
    const d = opts.draft()
    const s = snap && typeof snap === 'object' ? snap as { impl?: unknown; note?: unknown } : null
    if (!d || !s || !s.impl || typeof s.impl !== 'object') return
    for (const k of Object.keys(d)) delete d[k]
    Object.assign(d, cloneJson(s.impl))
    if (typeof s.note === 'string') opts.setNote(s.note)
  }
  return {
    space: 'project',
    projectId: opts.projectId,
    targetKind: 'actionBinding',
    targetId: opts.targetId,
    contextTitle: opts.contextTitle || '动作接口映射辅助填写',
    formId: 'actionBinding',
    codecs: { parameters: parametersCodec },
    contractInfo: () => ({ schemaVersion: FORM_ACTION_BINDING.schemaVersion, schemaDigest: FORM_ACTION_BINDING.schemaDigest }),
    refusals,
    resetRefusals: () => { refusals.value = [] },
    /** 白名单快照：契约 6 键；参数行带本地 rowId（row.update/remove 定位用）；绝不包含 auth。 */
    draft() {
      const d = opts.draft()
      if (!d) return {}
      return {
        method: String(d.method ?? ''),
        path: String(d.path ?? ''),
        bodyFormat: String(d.bodyFormat ?? ''),
        description: String(d.description ?? ''),
        parameters: (Array.isArray(d.parameters) ? d.parameters : []).map(rowOut),
        note: String(opts.note() ?? ''),
      }
    },
    /** 新版整稿通道：auth 拒绝/枚举校验/参数行落位都在这里投影。 */
    applyDraft(next) {
      if (!next || typeof next !== 'object') return
      const before = this.draft()
      applyProjection(next)
      control.recordApply(before, this.draft())
    },
    /** 旧版通道（顶层变更值）：并上当前投影后走同一落位路径 */
    apply(values) {
      if (!values || typeof values !== 'object') return
      applyProjection({ ...this.draft(), ...Object(values) })
    },
    /** 快照：弹窗草稿整体克隆（含 auth/kind/schemaVersion 等非白名单字段，撤销零丢失）+ 说明。 */
    snapshot() {
      const d = opts.draft()
      if (!d) return null
      const snap = cloneJson(d)
      control.beginRound({ impl: snap, note: String(opts.note() ?? '') })
      return { impl: snap, note: String(opts.note() ?? '') }
    },
    /** 恢复快照：原位替换键值，保持草稿对象身份（reactive 引用不换）；说明一并恢复。 */
    restore: restoreSnap,
    round: control.round,
    undoRound: () => {
      const snap = control.snapshotOf()
      if (!opts.draft() || !control.round.canUndo || snap === null) return false // 期间手改：不覆盖用户改动
      restoreSnap(snap)
      control.resetAfterUndo()
      return true
    },
    noteManualChange: () => control.noteManualChange(),
    observeFill: resp => control.observeFill(expected, resp),
  }
}
