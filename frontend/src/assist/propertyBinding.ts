// 属性/共享属性表单的整表自动填写宿主适配（T6 · O2，2026-09-22 改版）。
// 契约唯一来源：contracts/forms/property.json 与 sharedProperty.json（T1 生成物
// ./formContracts.gen.ts；接口文档 04 §6.1）；引擎对接面 ./formAutofill.ts 的
// AutofillHostBinding（draft()/codecs/applyDraft/snapshot/restore/contractInfo）。
// 设计要点：
//   * draft() 输出契约业务枚举的扁平快照：dataType='timeSeries'|基础类型（xsd: 前缀剥离，
//     如 'double'/'integer'）；obsType 仅时间序列出现（同为无前缀基础类型）；formatting 取
//     mg:formatting['@value']。契约枚举外的历史精确类型（integer/date/…）如实投影——
//     上下文 draft 只按键白名单校验，模型不可把它们作为 dataType 目标值（契约枚举约束）。
//     白名单外内容（@id/domain/apiName 等）一律不出网。
//   * dataTypeTransform codec（dataType/obsType 两键）：校验契约业务枚举后写扁平草稿副本；
//     真正的类型落位统一在 applyDraft——dataType/obsType 是 typeCore 原子组，必须整组应用：
//     进入/保持 timeSeries 需合法 obsType，否则整组拒绝（记 refusal，独立合法字段照常应用）；
//     离开 timeSeries 经组件 setRange（setPropertyDataType 同步清 mg:valueShape，观测类型
//     不残留）。不产生半组。后端 T2 已在服务端拒绝不完整原子组（响应转 unresolved），
//     本层是「不产生半组」的最后防线。
//   * formattingCodec（formatting 键）：仅做结构校验（对象或 null）；kind 与生效数据类型的
//     一致性、写/清时机在 applyDraft——formatting 按 requires 依赖 dataType，必须等类型组
//     应用完再写（setRange 会清 mg:formatting，先写会被联动误删）；本轮未修改的 formatting
//     不重放（类型切换按编辑器联动清理，与手动 UI 一致）。写/清复用 effectiveConfig
//     （无 mode/natural 空规则=未配置删键，与显示格式编辑器完全一致），不退化为纯文本字段。
//     样式白名单/历史样式/mode 受限子集由后端 blocked 校验与既有保存校验兜底。
//   * 只读共享引用保护：共享引用只读态（他人维护的共享定义）writable()=false——codec 拒写
//     （值不进副本，引擎不计数）+ applyDraft 拒绝落笔，原因记入 refusals 供宿主展示。
//   * snapshot()/restore() 对草稿节点整体 JSON 克隆/原位恢复（含白名单外字段，恢复零丢失）。
//   * 本文件绝不调用任何保存函数（form-save/commit-now/touch）——回填只改本地草稿，
//     持久化由用户显式保存（共享高影响确认等原流程不变）。
import { ref, type Ref } from 'vue'
import type { AutofillFieldCodec, AutofillHostBinding } from './useAssistPanel'
import { FORM_PROPERTY, FORM_SHARED_PROPERTY } from './formContracts.gen'
import { effectiveConfig } from '../ontology/formattingOptions'

export type PropertyAssistTargetKind = 'property' | 'sharedProperty'

/** 只读拒绝原因（binding 层与宿主 api 包装共用同一文案） */
export const PROPERTY_ASSIST_READONLY_REASON = '共享引用属性为只读，不能通过自动填写修改；请打开共享定义维护。'

/** 一次写入拒绝（宿主在状态条区域展示） */
export interface PropertyAssistRefusal { field: string; reason: string }

/** propertyAssistBinding 返回的扩展 binding（引擎兼容 + 宿主可读的 formId/refusals） */
export interface PropertyAssistHostBinding extends AutofillHostBinding {
  formId: 'property' | 'sharedProperty'
  /** 本轮打开以来的写入拒绝（只读/原子组非法/formatting 不匹配等）；展示用，上限 8 条 */
  refusals: Ref<PropertyAssistRefusal[]>
  /** 清空拒绝记录（保存成功/重新打开时由宿主调用） */
  resetRefusals(): void
}

export interface PropertyAssistOptions {
  /** 本体区必填：当前本体工作区 id */
  ontologyId?: string
  /** 组件本地草稿节点 getter（reactive draft；返回空值时快照为空对象、applyDraft/restore 无操作） */
  draft: () => any
  targetKind: PropertyAssistTargetKind
  /** 编辑目标稳定 id；新建（尚未入图）传空串，后端按「新建属性定义/共享属性定义」出标题 */
  targetId: string
  /** 是否可写（默认 true）：共享引用只读态传 () => false，binding 拒绝写入并记录原因 */
  writable?: () => boolean
  /** 数据类型切换：组件 setRange 同一写路径（setPropertyDataType 联动清理 valueShape/formatting/decimalPlaces） */
  setType: (v: string) => void
  /** 观测值类型切换：组件 setObservation 同一写路径（仅时间序列语义） */
  setObsType: (v: string) => void
}

const cloneJson = <T>(v: T): T => JSON.parse(JSON.stringify(v))
/** 观测值类型 = 草稿 rdfs:range（与组件 rangeId 计算属性同一取值） */
const rangeIdOf = (d: any): string => d?.['rdfs:range']?.['@id'] || 'xsd:string'
const isTimeSeriesOf = (d: any): boolean => d?.['mg:valueShape'] === 'timeSeries'
/** xsd:double → double（契约业务枚举口径）；非 xsd: 前缀原样保留 */
const businessOf = (range: string): string => range.startsWith('xsd:') ? range.slice(4) : range

// 契约枚举（单一来源：T1 生成物；后端按同一契约校验 operations）
const dataTypeEnumOf = (formId: 'property' | 'sharedProperty'): string[] =>
  (formId === 'sharedProperty' ? FORM_SHARED_PROPERTY : FORM_PROPERTY).fields.find(f => f.id === 'dataType')?.enum ?? []
const obsTypeEnumOf = (formId: 'property' | 'sharedProperty'): string[] =>
  (formId === 'sharedProperty' ? FORM_SHARED_PROPERTY : FORM_PROPERTY).fields.find(f => f.id === 'obsType')?.enum ?? []

/** 契约业务数据类型 → 显示格式 kind（formattingOptions 的样式表键，与编辑器/后端镜像） */
const FORMAT_KIND_BY_DATA: Record<string, string> = {
  string: 'string', double: 'number', decimal: 'number', integer: 'number',
  boolean: 'boolean', date: 'date', dateTime: 'time', timestamp: 'time',
  timeSeries: 'timeSeries', array: 'array', struct: 'struct',
}

/** 属性/共享属性表单的宿主 binding 工厂：契约枚举快照 + codec 校验 + typeCore 原子组落位 + 节点级快照/恢复。 */
export function propertyAssistBinding(opts: PropertyAssistOptions): PropertyAssistHostBinding {
  const writable = opts.writable ?? (() => true)
  const refusals = ref<PropertyAssistRefusal[]>([])
  const record = (field: string, reason: string): void => {
    if (refusals.value.length >= 8) refusals.value = refusals.value.slice(-7)
    refusals.value = [...refusals.value, { field, reason }]
  }
  const refuseWritable = (field: string): void => {
    record(field, PROPERTY_ASSIST_READONLY_REASON)
    throw new Error(PROPERTY_ASSIST_READONLY_REASON)
  }

  const dataTypeEnum = new Set(dataTypeEnumOf(opts.targetKind))
  const obsTypeEnum = new Set(obsTypeEnumOf(opts.targetKind))

  /** draft 副本键 → 快照投影键一致（label/comment/dataType/obsType/formatting） */
  const codecs: Record<string, AutofillFieldCodec> = {
    label: (d, field, value) => {
      if (!writable()) refuseWritable(field)
      if (typeof value !== 'string') throw new Error('属性名称必须是文本')
      d[field] = value
    },
    comment: (d, field, value) => {
      if (!writable()) refuseWritable(field)
      if (typeof value !== 'string') throw new Error('业务定义必须是文本')
      d[field] = value
    },
    dataType: (d, field, value) => {
      if (!writable()) refuseWritable(field)
      if (typeof value !== 'string' || !dataTypeEnum.has(value)) throw new Error('未知的数据类型：' + String(value))
      d[field] = value
    },
    obsType: (d, field, value) => {
      if (!writable()) refuseWritable(field)
      // '' 是「离开时间序列」的清空标记（服务端组语义），合法枚举值照常放行
      if (value !== '' && (typeof value !== 'string' || !obsTypeEnum.has(value))) {
        throw new Error('未知的观测值类型：' + String(value))
      }
      d[field] = value
    },
    formatting: (d, field, value) => {
      if (!writable()) refuseWritable(field)
      if (value !== null && (typeof value !== 'object' || Array.isArray(value))) {
        throw new Error('显示格式必须是对象')
      }
      d[field] = value === null ? null : cloneJson(value)
    },
  }

  /** 原子组 + formatting 的落位（label/comment 直写）。next 为引擎写好操作后的完整扁平草稿。 */
  const applyProjection = (next: Record<string, unknown>): void => {
    const d = opts.draft()
    if (!d || !next || typeof next !== 'object') return
    if (!writable()) { record('*', PROPERTY_ASSIST_READONLY_REASON); return }

    // 1. 文本字段直写
    if (typeof next.label === 'string') d['rdfs:label'] = next.label
    if (typeof next.comment === 'string') d['rdfs:comment'] = next.comment

    // 2. typeCore 原子组（dataType+obsType 整组应用，不产生半组）
    const preFmt = d['mg:formatting']?.['@value']
    const curTs = isTimeSeriesOf(d)
    const curBase = businessOf(rangeIdOf(d))
    const curType = curTs ? 'timeSeries' : curBase
    const nextType = typeof next.dataType === 'string' && next.dataType ? next.dataType : curType
    const typeOp = nextType !== curType
    const obsRaw = next.obsType === undefined || next.obsType === null ? undefined : String(next.obsType)
    // 生效观测值：未出现 obsType 操作时按目标类型回退（当前时间序列沿用现值；标量目标=空）
    const targetObs = nextType === 'timeSeries'
      ? (obsRaw !== undefined ? obsRaw : (curTs ? curBase : ''))
      : (obsRaw !== undefined ? obsRaw : '')
    const groupInvalid = nextType === 'timeSeries'
      ? !obsTypeEnum.has(targetObs)                       // 时间序列必须配套合法观测值类型
      : targetObs !== ''                                  // 标量类型不允许残留观测值类型
    if (groupInvalid) {
      if (typeOp || obsRaw !== undefined) {
        record('dataType', '数据类型与观测值类型必须成组合法：时间序列需同时给出观测值类型，标量类型不携带观测值类型；本次未应用该组合。')
      }
    } else {
      if (typeOp) opts.setType(nextType === 'timeSeries' ? 'timeSeries' : 'xsd:' + nextType)
      // setObservation 自带同值 early-return；离开时间序列时 setPropertyDataType 已同步清观测类型
      if (nextType === 'timeSeries' && targetObs && (typeOp || obsRaw !== undefined)) {
        opts.setObsType('xsd:' + targetObs)
      }
    }

    // 3. formatting（requires dataType：等类型组落位后再写/清；本轮未修改则不重放）
    if (next.formatting !== undefined) {
      const unchanged = JSON.stringify(next.formatting ?? null) === JSON.stringify(preFmt ?? null)
      if (!unchanged) {
        const fmt = next.formatting
        if (fmt === null) {
          delete d['mg:formatting']
        } else if (typeof fmt === 'object' && !Array.isArray(fmt)) {
          const effType = groupInvalid ? curType : nextType // 组被拒时按当前生效类型核对
          const want = FORMAT_KIND_BY_DATA[effType]
          const kind = typeof (fmt as any).kind === 'string' ? (fmt as any).kind : ''
          if (want && kind && kind !== want) {
            record('formatting', `显示格式类型「${kind}」与数据类型不匹配（当前为「${want}」），本次未应用。`)
          } else {
            const clean = effectiveConfig(fmt)
            if (clean) d['mg:formatting'] = { '@type': '@json', '@value': cloneJson(clean) }
            else delete d['mg:formatting']
          }
        }
      }
    }
  }

  return {
    space: 'ontology',
    ontologyId: opts.ontologyId || '',
    targetKind: opts.targetKind,
    targetId: opts.targetId,
    formId: opts.targetKind,
    contextTitle: opts.targetKind === 'sharedProperty' ? '共享属性定义' : '属性定义',
    refusals,
    resetRefusals: () => { refusals.value = [] },
    contractInfo: () => {
      const c = opts.targetKind === 'sharedProperty' ? FORM_SHARED_PROPERTY : FORM_PROPERTY
      return { schemaVersion: c.schemaVersion, schemaDigest: c.schemaDigest }
    },
    codecs,
    /** 白名单快照：label/comment/dataType（+obsType 仅时间序列）（+formatting 仅已配置），契约业务枚举口径 */
    draft() {
      const d = opts.draft()
      if (!d) return {}
      const ts = isTimeSeriesOf(d)
      const base = businessOf(rangeIdOf(d))
      const snap: Record<string, unknown> = {
        label: d['rdfs:label'] ?? '',
        comment: d['rdfs:comment'] ?? '',
        dataType: ts ? 'timeSeries' : base,
      }
      if (ts) snap.obsType = base
      const fmt = d['mg:formatting']?.['@value']
      if (fmt !== undefined && fmt !== null) snap.formatting = cloneJson(fmt)
      return snap
    },
    /** 新版整稿通道：typeCore 原子组 + formatting 依赖序 + 只读保护都在这里落位 */
    applyDraft(next) {
      applyProjection(next)
    },
    /** 旧版通道（顶层变更值）：并上当前投影后走同一落位路径 */
    apply(values) {
      if (!values || typeof values !== 'object') return
      applyProjection({ ...this.draft(), ...values })
    },
    /** 快照：草稿节点整体克隆（含白名单外字段，恢复零丢失） */
    snapshot() {
      const d = opts.draft()
      return d ? cloneJson(d) : null
    },
    /** 恢复快照：原位替换键值，保持 draft 对象身份（reactive 引用不换） */
    restore(snap) {
      const d = opts.draft()
      if (!d || !snap || typeof snap !== 'object') return
      for (const k of Object.keys(d)) delete d[k]
      Object.assign(d, cloneJson(snap))
    },
  }
}
