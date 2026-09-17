// 显示格式（mg:formatting['@value']）的选项与纯辅助：PropertyFormatting.vue 与测试共用，不依赖 Vue。
// schema 冻结于 文档/显示格式优化需求_v1_20260915.md 第 6 节（阶段 0），不得自行改名：
//   文本 string:      style standard|template|mapping；template:'ESS-{value}'；mappings:[{from,to}]
//   数值 number:      style standard|percent|currency|scientific；decimals 0-20；grouping 默认 true（scientific 不展示）；
//                     prefix/suffix；percentInput 'ratio'|'hundred'；currency 默认 'CNY'
//   布尔 boolean:     style default|custom；custom→trueText/falseText（默认 是/否）
//   日期 date:        style date|custom；date→precision year|month|day；custom→pattern（禁 HH/mm/ss）；无时区
//   时间戳 time:      style date|datetime|time|relative|custom；date→precision year|month|day；datetime/time→'minute'|'second'；
//                     timezone 默认 'Asia/Shanghai'（可输 IANA）；custom→pattern
//   数组 array:       style list|join；maxItems 默认 10（1-100）；join→separator 默认 '、'；
//                     elementType ''|string|number|boolean|date|time + elementFormat 子配置
//   结构体 struct:    style pairs|template；pairs→fields 字段名顺序数组 + separator 默认 '；'；template→'{manufacturer} / {model}'
//   时间序列 timeSeries: style 固定 'series'；timeFormat + valueFormat 子配置
//   公共：            mode 'builtin'|'natural'（历史 code 兼容只读）；emptyText 默认 '—'（仅 null 算空值）
export type StyleOption = { value: string; label: string }

const STYLE_TABLE: Record<string, readonly StyleOption[]> = {
  string: [{ value: 'standard', label: '原样' }, { value: 'template', label: '模板' }, { value: 'mapping', label: '值映射' }],
  number: [{ value: 'standard', label: '普通数字' }, { value: 'percent', label: '百分比' }, { value: 'currency', label: '货币' }, { value: 'scientific', label: '科学计数' }],
  boolean: [{ value: 'default', label: '默认（是／否）' }, { value: 'custom', label: '自定义文字' }],
  date: [{ value: 'date', label: '仅日期' }, { value: 'custom', label: '自定义格式' }],
  time: [{ value: 'date', label: '仅日期' }, { value: 'datetime', label: '日期时间' }, { value: 'time', label: '仅时间' }, { value: 'relative', label: '相对时间' }, { value: 'custom', label: '自定义格式' }],
  array: [{ value: 'list', label: '列表' }, { value: 'join', label: '分隔文本' }],
  struct: [{ value: 'pairs', label: '字段列表' }, { value: 'template', label: '模板' }],
  timeSeries: [{ value: 'series', label: '时间和值' }],
}

/** 当前类型可选的新样式列表（只含新样式，历史样式不进入选项，见 legacyStyleLabel）。 */
export function styleOptions(kind: string): StyleOption[] {
  return (STYLE_TABLE[kind] || []).map(o => ({ value: o.value, label: o.label }))
}

/** 格式化方式恰好两项（A1）；历史 code 不作为新建入口。 */
export const modeOptions: StyleOption[] = [{ value: 'builtin', label: '常用格式' }, { value: 'natural', label: '自然语言' }]

/** 数组元素解释类型：'' 为原样（默认）。 */
export const elementTypeOptions: StyleOption[] = [
  { value: '', label: '原样（默认）' }, { value: 'string', label: '文本' }, { value: 'number', label: '数值' },
  { value: 'boolean', label: '是／否' }, { value: 'date', label: '日期' }, { value: 'time', label: '时间戳' },
]

// 历史样式中文名（第 7 节兼容：不主动删除、不静默改成原样，显式选择新样式后才替换）。
const LEGACY_STYLE_LABELS: Record<string, Record<string, string>> = {
  string: { upper: '转大写', lower: '转小写', trim: '去除首尾空白', mask: '脱敏显示', truncate: '截断长文本' },
  array: { count: '元素数量', json: 'JSON' },
  struct: { json: 'JSON缩进', compact: 'JSON紧凑' },
  time: { iso: 'ISO标准时间', short: '年月日时分' },
}
const LEGACY_NOTATION_LABELS: Record<string, string> = { compact: '紧凑（万／亿）', engineering: '工程计数', scientific: '科学计数' }

/**
 * 历史样式检测（builtin 下）：返回中文名，非历史样式返回 ''。
 * number 以 notation 判定（compact/engineering，或旧 standard+scientific）；其余类型按 style 是否属于当前新样式判定，
 * 未知样式回退显示原始值。旧 string standard 带 prefix/suffix 属正常新配置，不判为历史。
 */
export function legacyStyleLabel(kind: string, config: any): string {
  if (!config || typeof config !== 'object') return ''
  if (kind === 'number') return LEGACY_NOTATION_LABELS[String(config.notation || '')] || ''
  const style = String(config.style ?? '')
  if (!style || styleOptions(kind).some(o => o.value === style)) return ''
  return LEGACY_STYLE_LABELS[kind]?.[style] || style
}

/** 样式专属键：历史样式切换到新样式时清除；通用键（decimals/grouping/prefix/suffix/emptyText/timezone/minDecimals/maxDecimals）保留。 */
export const STYLE_SPECIFIC_KEYS: readonly string[] = [
  'template', 'mappings', 'precision', 'pattern', 'trueText', 'falseText', 'currency', 'percentInput',
  'separator', 'maxItems', 'elementFormat', 'fields', 'timeFormat', 'valueFormat', 'notation', 'length',
]

/**
 * 生效配置：null 表示未配置（本地草稿为空，或 natural 且规则为空），此时应删除 mg:formatting；
 * 否则返回去除 undefined 的浅拷贝。历史 code / 历史样式 / 未知扩展键原样保留（零丢失）。
 */
export function effectiveConfig(local: any): Record<string, any> | null {
  if (!local || typeof local !== 'object' || Array.isArray(local)) return null
  if (!local.mode) return null // 无 mode 的残缺配置等同未配置（A2：不落 mg:formatting）
  if (local.mode === 'natural' && !String(local.instruction || '').trim()) return null
  const clean: Record<string, any> = {}
  for (const [key, value] of Object.entries(local)) if (value !== undefined) clean[key] = value
  return clean
}

/** 小数位历史提示：存在 minDecimals/maxDecimals 且未填固定 decimals 时提示原范围；填写 decimals 后统一为固定精度。 */
export function decimalLegacyHint(config: any): string {
  if (!config || typeof config !== 'object') return ''
  if (config.decimals !== undefined && config.decimals !== null) return ''
  const min = config.minDecimals, max = config.maxDecimals
  if (min === undefined && max === undefined) return ''
  return `历史精度范围：最少${typeof min === 'number' ? min : 0}～最多${typeof max === 'number' ? max : 2}位；填写后将统一为固定精度`
}

const DEFAULT_STYLES: Record<string, string> = {
  string: 'standard', number: 'standard', boolean: 'default', date: 'date',
  time: 'datetime', array: 'list', struct: 'pairs', timeSeries: 'series',
}

/** 各类型默认样式：用户首次切到常用格式、且配置里没有可用样式时使用。 */
export function defaultStyle(kind: string): string {
  return DEFAULT_STYLES[kind] || 'standard'
}

const DEFAULT_SAMPLES: Record<string, string> = {
  string: 'ESS-001', number: '70.56', boolean: 'true', date: '2026-09-15',
  time: '2026-09-15T14:30:25+08:00', array: '["告警A","告警B"]', struct: '{"soc":70,"status":"运行"}',
  timeSeries: '[{"timestamp":"2026-09-15T14:30:00+08:00","value":70.56}]',
}

/** 各类型预览样本默认值；时间序列为规范样本形态（需求 3.4）。 */
export function sampleDefault(kind: string): string {
  return DEFAULT_SAMPLES[kind] || 'ESS-001'
}
