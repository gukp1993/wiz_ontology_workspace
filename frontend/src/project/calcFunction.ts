// 计算函数（kind=calculationFunction）：前端编辑辅助。
// 公式的解析/类型检查/试算求值的权威实现在后端 workbench/calc_functions.py，
// 本模块只做编辑态转换（显示用参数名 ↔ 规范稳定标识）与轻量本地检查。
export const CALC_LANGUAGE = 'calc-expression-1'
export const CALC_TYPES = ['number', 'string', 'boolean'] as const
export type CalcType = typeof CALC_TYPES[number]
export const CALC_TYPE_NAMES: Record<string, string> = { number: '数值', string: '文本', boolean: '是/否' }

export const isCalcFunction = (v: any): boolean => v?.kind === 'calculationFunction'

export function newCalcInput(_index: number): { id: string; name: string; type: CalcType } {
  return { id: 'inp-' + crypto.randomUUID().replaceAll('-', '').slice(0, 12), name: '', type: 'number' }
}

export function blankCalcFunction(): any {
  return { kind: 'calculationFunction', schemaVersion: 1, id: 'cf-' + crypto.randomUUID().replaceAll('-', '').slice(0, 12),
           name: '', description: '',
           inputs: [newCalcInput(0)],
           implementation: { language: CALC_LANGUAGE, expression: '' },
           output: { id: 'out-' + crypto.randomUUID().replaceAll('-', '').slice(0, 12), name: '', type: 'number' } }
}

// 引号外的 {…} 记号扫描（公式内文本字面量里的花括号不参与参数引用）。
function scanBraceTokens(formula: string): { token: string; start: number }[] {
  const out: { token: string; start: number }[] = []
  let quote: string | null = null, i = 0
  const n = formula.length
  while (i < n) {
    const ch = formula[i]
    if (quote) {
      if (ch === quote) { if (formula[i + 1] === quote) { i += 2; continue } quote = null }
      i++; continue
    }
    if (ch === '"' || ch === "'") { quote = ch; i++; continue }
    if (ch === '{') {
      const end = formula.indexOf('}', i + 1)
      if (end < 0) { out.push({ token: formula.slice(i + 1), start: i }); break }
      out.push({ token: formula.slice(i + 1, end), start: i })
      i = end + 1; continue
    }
    i++
  }
  return out
}

/** 规范公式（{inp_x}）→ 显示公式（{参数名}）；未知标识原样保留。 */
export function toDisplayFormula(expr: string, inputs: { id: string; name: string }[]): string {
  const byId = new Map(inputs.map(p => [p.id, p.name]))
  return String(expr || '').replace(/\{([A-Za-z0-9_-]+)\}/g, (m, id) => byId.has(id) ? '{' + byId.get(id) + '}' : m)
}

export type CanonicalResult = { expr: string; unknown: string[] }

/** 显示公式（{参数名}）→ 规范公式（{inp_x}）。unknown = 引用了不存在参数的名称列表。 */
export function toCanonicalFormula(formula: string, inputs: { id: string; name: string }[]): CanonicalResult {
  const byName = new Map(inputs.map(p => [p.name, p.id]))
  const unknown: string[] = []
  let quote: string | null = null
  const n = formula.length
  let out = ''
  let i = 0
  while (i < n) {
    const ch = formula[i]
    if (quote) {
      out += ch
      if (ch === quote) { if (formula[i + 1] === quote) { out += formula[i + 1]; i += 2; continue } quote = null }
      i++; continue
    }
    if (ch === '"' || ch === "'") { quote = ch; out += ch; i++; continue }
    if (ch === '{') {
      const end = formula.indexOf('}', i + 1)
      const token = end < 0 ? formula.slice(i + 1) : formula.slice(i + 1, end)
      const id = byName.get(token)
      if (id) { out += '{' + id + '}' } else { unknown.push(token); out += formula.slice(i, end < 0 ? n : end + 1) }
      i = end < 0 ? n : end + 1
      continue
    }
    out += ch; i++
  }
  return { expr: out, unknown: [...new Set(unknown)] }
}

/** 显示公式引用到的参数名（去重），供“删除被引用参数”检查。 */
export function formulaRefNames(formula: string): string[] {
  return [...new Set(scanBraceTokens(String(formula || '')).map(t => t.token.trim()).filter(Boolean))]
}

/** 编辑态本地检查（完整语法/类型校验以服务端 /api/calc-eval 为准）。 */
export function calcFunctionErrors(draft: any, displayFormula?: string): string[] {
  const errors: string[] = []
  if (!String(draft?.name || '').trim()) errors.push('请填写函数名称')
  const inputs: any[] = Array.isArray(draft?.inputs) ? draft.inputs : []
  if (!inputs.length) errors.push('至少需要一个输入参数')
  const names = new Set<string>()
  for (const p of inputs) {
    const name = String(p?.name || '').trim()
    if (!name) errors.push('请填写参数名称')
    else if (name.includes('{') || name.includes('}')) errors.push('参数名称不能包含大括号')
    else if (names.has(name)) errors.push(`参数名称「${name}」重复`)
    else names.add(name)
    if (!CALC_TYPES.includes(p?.type)) errors.push(`参数「${name || p?.id}」数据类型无效`)
  }
  if (!String(draft?.output?.name || '').trim()) errors.push('请填写输出名称')
  if (!CALC_TYPES.includes(draft?.output?.type)) errors.push('输出数据类型无效')
  const formula = displayFormula !== undefined ? String(displayFormula) : String(draft?.implementation?.expression || '')
  if (!formula.trim()) errors.push('公式未填写')
  else {
    const canonical = toCanonicalFormula(formula, inputs)
    for (const u of canonical.unknown) errors.push(`公式引用的参数「${u}」不存在或已被删除`)
  }
  return errors
}

/** 计算函数参数类型 ↔ 本体属性 range 的相容（与后端 _calc_range_ok 镜像）。 */
export function calcRangeOk(calcType: string, propertyRange: string): boolean {
  const compat: Record<string, string[]> = { number: ['double', 'decimal', 'integer'], string: ['string'], boolean: ['boolean'] }
  return (compat[calcType] || []).includes(propertyRange)
}

/** 固定值有效性（与后端 constant_value_ok 镜像）：0/false/空串是有效值。 */
export function calcConstantOk(calcType: string, value: any): boolean {
  if (value === undefined || value === null) return false
  if (calcType === 'number') {
    if (typeof value === 'boolean') return false
    const s = String(value).trim()
    return s !== '' && Number.isFinite(Number(s))
  }
  if (calcType === 'boolean') return value === true || value === false || value === 'true' || value === 'false'
  return true
}

/** 试算入参组装：按参数类型把表单文本转换为后端取值（空文本 → null＝未提供）。 */
export function trialPayload(draft: any, values: Record<string, any>) {
  const inputs = (draft.inputs || []).map((p: any) => {
    const raw = values[p.id]
    let value: any = null
    if (typeof raw === 'string' ? raw.trim() !== '' : raw !== undefined && raw !== null) {
      if (p.type === 'number') value = Number(String(raw).trim())
      else if (p.type === 'boolean') value = raw === 'true' || raw === true
      else value = raw
    }
    return { id: p.id, type: p.type, value }
  })
  return { expression: draft.implementation?.expression || '', inputs, outputType: draft.output?.type || 'number' }
}
