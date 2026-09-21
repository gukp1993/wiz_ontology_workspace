// 「从物料自动构建本体」候选属性字段（dataType / valueType）的前端协议层（20260920，验收 R3-04）。
// 后端已冻结的平铺口径（workbench/ontology_build/review.py update_candidate）：
//   · fields.dataType 是**平铺字符串**，取值 ∈ protocol.PROPERTY_DATA_TYPES；
//   · fields.valueType 只在 dataType === 'timeSeries' 时有意义，取值 ∈ protocol.VALUE_TYPES
//     （= workbench/model_format.py 的 SERIES_VALUE_TYPES，即工作台本体协议口径）；
//   · **不接受**嵌套对象形态 { type:'timeSeries', valueType:'double' }——服务端会报「不支持的数据类型」；
//   · dataType 为空表示「未确定」：buildPropertyFields('') 返回空 patch（服务端合并保留原值）。
//     「已有数据类型的属性禁止清空为未确定」这一口径由评审页 BuildReviewPage.vue 的 saveEdit
//     执行（P2-1，20260921 验收）；本文件保持纯函数，不做页面级校验、行为不变。
// 本文件只有纯常量与纯函数：不发请求、不持有状态、不依赖 Vue，Node 测试可直接 import。

/**
 * 属性数据类型（7 项，顺序即下拉顺序；与后端 protocol.PROPERTY_DATA_TYPES 逐字一致）。
 */
export const PROPERTY_DATA_TYPES = ['text', 'number', 'boolean', 'dateTime', 'array', 'struct', 'timeSeries'] as const

/** 数据类型中文标签（文本 / 数值 / 是或否 / 时间 / 数组 / 结构体 / 时间序列）。 */
export const PROPERTY_DATA_TYPE_LABELS: Record<string, string> = {
  text: '文本',
  number: '数值',
  boolean: '是或否',
  dateTime: '时间',
  array: '数组',
  struct: '结构体',
  timeSeries: '时间序列',
}

/**
 * 时间序列观测值类型（7 项；与后端 protocol.VALUE_TYPES / model_format.SERIES_VALUE_TYPES 逐字一致）。
 * 注意：这里不是工作台属性面板的 xsd 类型名，也不是旧界面的 text/number/boolean/dateTime。
 */
export const VALUE_TYPES = ['string', 'double', 'decimal', 'integer', 'boolean', 'date', 'dateTime'] as const

/** 观测值类型中文标签：double 与 decimal 都属数值类，但枚举分列，标签也分开以免选错。 */
export const VALUE_TYPE_LABELS: Record<string, string> = {
  string: '文本',
  double: '数值',
  decimal: '小数',
  integer: '整数',
  boolean: '是或否',
  date: '日期',
  dateTime: '日期时间',
}

/** 编辑器读出的数据类型（一律归一为字符串，绝不回传嵌套对象）。 */
export interface PropertyFields {
  /** 空串 = 未确定（页面显示「未确定（需依据）」） */
  dataType: string
  /** 仅 dataType==='timeSeries' 时有值；其余情况恒为空串 */
  valueType: string
}

export function isPropertyDataType(value: string): boolean {
  return (PROPERTY_DATA_TYPES as readonly string[]).includes(value)
}

export function isValueType(value: string): boolean {
  return (VALUE_TYPES as readonly string[]).includes(value)
}

/** 标签兜底：服务端给了枚举外的新值时原样回显，不显示 undefined。 */
export function propertyDataTypeLabel(key: string): string {
  return PROPERTY_DATA_TYPE_LABELS[key] || key
}

export function valueTypeLabel(key: string): string {
  return VALUE_TYPE_LABELS[key] || key
}

function text(value: unknown): string {
  if (typeof value === 'string') return value.trim()
  if (typeof value === 'number' && Number.isFinite(value)) return String(value)
  return ''
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : {}
}

/**
 * 读取候选 fields 里的数据类型（编辑器回填 + 变更基线）。
 *  · 平铺形态（当前协议，**必须先读平铺的 fields.valueType**）：{ dataType:'timeSeries', valueType:'double' }；
 *  · 嵌套形态（历史数据**只读**兼容，避免打不开编辑器）：{ dataType:{ type:'timeSeries', valueType:'double' } }；
 * 忠实读取：读不出就是空串（绝不用 double 之类的默认值臆造），也不替服务端做取值裁剪——
 * 取值是否合法由页面保存前的校验负责（saving 时只写 buildPropertyFields 的平铺结果）。
 */
export function readPropertyFields(fields: unknown): PropertyFields {
  const node = asRecord(fields)
  const raw = node.dataType
  let dataType = ''
  let valueType = ''
  if (typeof raw === 'string') {
    dataType = raw.trim()
    valueType = text(node.valueType)
  } else if (raw && typeof raw === 'object' && !Array.isArray(raw)) {
    const nested = asRecord(raw)
    dataType = text(nested.type)
    valueType = text(nested.valueType) || text(node.valueType)
  }
  return { dataType, valueType }
}

/**
 * 生成写回服务端的**平铺**字段：{ dataType }，仅 timeSeries 追加 { valueType }。
 * 绝不输出嵌套对象；dataType 非 timeSeries（或观测值类型为空）时不输出 valueType，
 * 保证 dataType='number' 时不会残留观测值类型。dataType 为空表示「未确定」：返回空对象，
 * 调用方不发送该键，服务端保留原值。
 */
export function buildPropertyFields(dataType: string, valueType: string): Record<string, unknown> {
  const type = text(dataType)
  const out: Record<string, unknown> = {}
  if (!type) return out
  out.dataType = type
  if (type === 'timeSeries') {
    const observation = text(valueType)
    if (observation) out.valueType = observation
  }
  return out
}
