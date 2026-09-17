// V4 sqlSteps：卡片式 SQL 步骤规则的转换与校验（纯前端逻辑，与后端 query_rules.sql_steps_errors 消息镜像）。
// 只描述查询模板，绝不执行 SQL；字符串/注释内的伪引用、伪分号不参与判定。
export type SqlStepView = { id: string; key: string; name: string; cardinality: 'one' | 'many'; sql: string }

// ---------- 词法剥离：字符串（'..'／".."，含 '' "" 转义）、-- 行注释、块注释 ----------
export function stripSqlNoise(code: string): string {
  let out = '', i = 0
  const n = code.length
  while (i < n) {
    const ch = code[i]
    if (ch === "'" || ch === '"') {
      i++
      while (i < n) {
        if (code[i] === ch) {
          if (code[i + 1] === ch) { i += 2; continue }
          i++; break
        }
        i++
      }
      out += ' '
    } else if (code.slice(i, i + 2) === '--') {
      while (i < n && code[i] !== '\n') i++
      out += ' '
    } else if (code.slice(i, i + 2) === '/*') {
      i += 2
      while (i < n && code.slice(i, i + 2) !== '*/') i++
      i += 2
      out += ' '
    } else { out += ch; i++ }
  }
  return out
}

const IDENT = /^[A-Za-z_][A-Za-z0-9_]*$/

// ---------- 校验（消息与后端 sql_steps_errors 一致） ----------
export function sqlStepsRuleErrors(rule: any): string[] {
  const errors: string[] = [], names = new Set<string>(), known = new Set<string>()
  // 三个集合分开维护：ids=全部步骤 ID（非空且唯一）；keys=全部技术名（合法且唯一）；
  // known=可被后续引用的前置 one 步骤 key。ID 是系统生成的 UUID/旧标识，不按 SQL 标识符规则校验。
  const ids = new Set<string>(), keys = new Set<string>()
  const labelOf = (s: any) => String(s?.name || '').trim() || String(s?.key || '')
  if (!String(rule?.name || '').trim()) errors.push('请填写规则名称')
  if (!rule?.connection) errors.push('请选择数据连接')
  for (const p of rule?.inputs || []) {
    if (!IDENT.test(p?.name) || names.has(p.name)) errors.push('输入参数名称无效或重复')
    names.add(String(p?.name))
    if (!['string', 'double', 'boolean', 'dateTime'].includes(p?.type)) errors.push('输入参数数据类型无效')
    if (!['binding', 'runtime'].includes(p?.source)) errors.push('输入参数传入方式无效')
  }
  if (rule?.mode !== 'sqlSteps') errors.push('取值规则版本不受支持')
  const steps: any[] = Array.isArray(rule?.steps) ? rule.steps : []
  const stepNames = new Map(steps.map(s => [s?.key, labelOf(s)]))
  if (!steps.length) errors.push('至少需要一个查询步骤')
  steps.forEach(step => {
    const title = `步骤「${labelOf(step)}」：`
    if (!String(step?.name || '').trim()) errors.push(title + '请填写步骤名称')
    const sid = step?.id
    if (typeof sid !== 'string' || !sid.trim()) errors.push(title + '步骤标识缺失')
    else if (ids.has(sid)) errors.push(title + '步骤标识重复')
    else ids.add(sid)
    if (!IDENT.test(step?.key) || keys.has(step?.key)) errors.push(title + '步骤技术名无效或重复')
    if (typeof step?.key === 'string' && IDENT.test(step.key)) keys.add(step.key)
    if (step?.cardinality !== 'one' && step?.cardinality !== 'many') errors.push(title + '预期记录数无效')
    const sql = step?.sql
    if (typeof sql !== 'string' || !sql.trim()) {
      errors.push(title + 'SQL 未填写')
    } else {
      const code = stripSqlNoise(sql)
      if (!/^\s*SELECT\b/i.test(code)) errors.push(title + (/^\s*WITH\b/i.test(code) ? '本期不支持 WITH 开头的语句' : '须以 SELECT 开始'))
      if (code.split(';').filter(s => s.trim()).length !== 1) errors.push(title + '每张卡片只填写一条 SELECT')
      for (const m of code.matchAll(/:([A-Za-z_][A-Za-z0-9_]*)(?:\.([A-Za-z_][A-Za-z0-9_]*))?/g)) {
        if (m[2]) {
          if (!known.has(m[1])) errors.push(title + `引用 ${m[1]}.${m[2]}，但「${stepNames.get(m[1]) || m[1]}」不存在、返回多条或尚未执行`)
        } else if (!names.has(m[1])) errors.push(title + '引用了未声明的输入参数 :' + m[1])
      }
      for (const m of code.matchAll(/\{\{([^{}]*)\}\}/g)) {
        if (/^[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*$/.test(m[1])) {
          if (!known.has(m[1].split('.')[0])) errors.push(title + `动态标识 ${m[1]} 须引用前置唯一记录步骤的输出`)
        } else errors.push(title + '引用格式无效')
      }
    }
    if (typeof step?.key === 'string' && IDENT.test(step.key) && step?.cardinality === 'one') known.add(step.key)
  })
  const result = rule?.result || {}
  const outStep = steps.find(s => s?.id === result.step) || null
  if (!outStep) errors.push('返回步骤不存在')
  if (!['scalar', 'timeSeries'].includes(result.type) || !['string', 'double', 'boolean', 'dateTime'].includes(result.valueType)) errors.push('返回类型无效')
  const timestamp = String(result.timestamp || '').trim(), value = String(result.value || '').trim()
  if (result.type === 'scalar') {
    if (!outStep || outStep.cardinality !== 'one' || !value) errors.push('单值返回须选择一条记录的步骤并填写值列')
  } else if (result.type === 'timeSeries') {
    if (!outStep || outStep.cardinality !== 'many' || !timestamp || !value || timestamp === value) errors.push('时间序列返回须选择多条记录的步骤，填写不同的时间列与值列')
  }
  // 输出步骤非最后一步仅为界面提示（QueryRuleManager 的 outNotLast 内联展示），不阻断保存。
  return [...new Set(errors)]
}

// ---------- 旧格式 → V4 无损转换（内存，绝不修改入参、绝不落盘） ----------
const OPS: Record<string, string> = { eq: '=', ne: '<>', gt: '>', gte: '>=', lt: '<', lte: '<=' }
const toIdentifier = (v: string) => String(v).replace(/^\{\{steps\.(\w+)\.(\w+)\}\}$/, '{{$1.$2}}')
const toValue = (v: any) => {
  const s = String(v)
  if (s.startsWith('{{inputs.')) return s.replace(/^\{\{inputs\.(\w+)\}\}$/, ':$1')
  if (s.startsWith('{{steps.')) return s.replace(/^\{\{steps\.(\w+)\.(\w+)\}\}$/, ':$1.$2')
  return "'" + s.replaceAll("'", "''") + "'"
}
function structuredStepSql(rule: any, s: any): string {
  const cols = (s.select || []).map((f: any) => `${toIdentifier(f.field)} AS ${f.as}`).join(',\n       ')
  const conds = (s.where || []).map((w: any) => `${toIdentifier(w.field)} ${OPS[w.op] || w.op} ${toValue(w.value)}`).join('\n  AND ')
  const sorts = (s.orderBy && s.orderBy.length ? s.orderBy
    : rule?.result?.type === 'timeSeries' && rule.result.step === s.id
      ? [{ field: (s.select || []).find((f: any) => f.as === rule.result.timestamp)?.field, direction: 'ascending' }]
      : []).filter((x: any) => x?.field)
  return `SELECT ${cols}\nFROM ${toIdentifier(s.table)}${conds ? `\nWHERE ${conds}` : ''}${sorts.length ? `\nORDER BY ${sorts.map((x: any) => toIdentifier(x.field) + (x.direction === 'descending' ? ' DESC' : ' ASC')).join(', ')}` : ''}`
}

// 词法安全地定位行首 `-- @step key one|many` 标记（字符串／块注释内的不算）。
// lineStart=标记行行首位置（上一段的 bodyEnd）；bodyStart=标记行之后正文起点。
function findStepMarkers(text: string): { key: string; cardinality: string; lineStart: number; bodyStart: number }[] {
  const markers: { key: string; cardinality: string; lineStart: number; bodyStart: number }[] = []
  let i = 0, lineStart = true, lineAt = 0
  const n = text.length
  while (i < n) {
    const ch = text[i]
    if (ch === "'" || ch === '"') {           // 字符串：内部标记不算
      i++
      while (i < n) {
        if (text[i] === ch) { if (text[i + 1] === ch) { i += 2; continue } i++; break }
        i++
      }
      lineStart = false
    } else if (text.slice(i, i + 2) === '--') {
      if (lineStart) {
        const lineEnd = text.indexOf('\n', i) < 0 ? n : text.indexOf('\n', i)
        const m = /^--\s*@step\s+([A-Za-z_]\w*)\s+(one|many)\s*$/.exec(text.slice(i, lineEnd))
        if (m) {
          let j = i + m[0].length
          if (text[j] === '\n') j++
          markers.push({ key: m[1], cardinality: m[2], lineStart: lineAt, bodyStart: j })
        }
      }
      while (i < n && text[i] !== '\n') i++
    } else if (text.slice(i, i + 2) === '/*') { // 块注释：内部标记不算
      i += 2
      while (i < n && text.slice(i, i + 2) !== '*/') i++
      i += 2
      lineStart = false
    } else {
      if (ch === '\n') { lineStart = true; lineAt = i + 1 }
      else if (ch !== ' ' && ch !== '\t' && ch !== '\r') lineStart = false
      i++
    }
  }
  return markers
}

// V1/V2 无显式 inputs：按旧语义合成默认参数声明（镜像旧 editableRule 的注入规则）。
const LEGACY_INPUT_META: Record<string, { type: string; source: string; description: string }> = {
  model_name: { type: 'string', source: 'binding', description: '模型表名' },
  attr_name: { type: 'string', source: 'binding', description: '属性名' },
  model_id: { type: 'double', source: 'binding', description: '当前实例主键，绑定时填写 {id}' },
  instanceId: { type: 'string', source: 'binding', description: '当前实例主键' },
  startTime: { type: 'dateTime', source: 'runtime', description: '查询开始时间' },
  endTime: { type: 'dateTime', source: 'runtime', description: '查询结束时间' }
}
function legacyInputsOf(oldSteps: any[]): any[] {
  const used = new Set<string>()
  for (const s of oldSteps || []) {
    const text = JSON.stringify([s.table, s.where, s.select, s.orderBy])
    for (const m of (text || '').matchAll(/\{\{inputs\.(\w+)\}\}/g)) used.add(m[1])
  }
  return [...used].map(name => LEGACY_INPUT_META[name]
    ? { name, ...LEGACY_INPUT_META[name] }
    : { name, type: 'string', description: '', source: 'binding' })
}

export function convertToSqlSteps(source: any): { rule: any | null; reason: string } {
  const rule = source ? JSON.parse(JSON.stringify(source)) : null
  if (!rule || rule.kind !== 'queryRule') return { rule: null, reason: '无法识别的旧格式' }
  if (rule.schemaVersion === 4) return { rule, reason: '' }
  const structured = Array.isArray(rule.steps) && rule.steps.length > 0 && rule.steps.every((s: any) => s && typeof s.table === 'string')
  if (structured) {
    // V1/V2/V3 结构化 → 卡片：SQL 由原步骤生成，id/key=旧步骤 id，别名与引用原样保留
    const oldSteps = JSON.parse(JSON.stringify(rule.steps)), oldResult = JSON.parse(JSON.stringify(rule.result || {}))
    const steps: SqlStepView[] = rule.steps.map((s: any) => ({
      id: String(s.id), key: String(s.id), name: String(s.name || s.id || ''),
      cardinality: s.cardinality === 'many' ? 'many' : 'one', sql: structuredStepSql(rule, s)
    }))
    delete rule.steps
    delete rule.objectType
    if (!Array.isArray(rule.inputs) || !rule.inputs.length) rule.inputs = legacyInputsOf(oldSteps)
    rule.schemaVersion = 4
    rule.mode = 'sqlSteps'
    rule.steps = steps
    rule.result = { ...oldResult, step: oldResult.step || steps[steps.length - 1].id }
    rule.extensions = { ...(rule.extensions || {}), structuredRuleBeforeSql: { steps: oldSteps, result: oldResult } }
    return { rule, reason: '' }
  }
  if (rule.mode === 'sqlTemplate' && typeof rule.sqlTemplate === 'string') {
    const text = rule.sqlTemplate
    const markers = findStepMarkers(text)
    if (!markers.length) return { rule: null, reason: '未找到 -- @step 查询段' }
    const keys = markers.map(m => m.key)
    if (new Set(keys).size !== keys.length) {
      const dup = keys.find((k, i) => keys.indexOf(k) !== i)
      return { rule: null, reason: `查询段重复：${dup}` }
    }
    const bounds = markers.map((m, i) => ({ ...m, bodyEnd: i + 1 < markers.length ? markers[i + 1].lineStart : text.length }))
    // 首个真实标记之前的内容用词法标记位置截取：注释/字符串里的伪 '-- @step' 不算段界，
    // 其后的 SQL 也不得静默丢弃——前置存在真实 SQL 即转换失败回退旧模板。
    const before = text.slice(0, markers[0].lineStart)
    if (stripSqlNoise(before).trim()) return { rule: null, reason: '首段之前存在 SQL 内容，无法按段切分' }
    for (const b of bounds) {
      const body = text.slice(b.bodyStart, b.bodyEnd)
      if (!/^\s*SELECT\b/i.test(stripSqlNoise(body))) return { rule: null, reason: `查询段 ${b.key} 不是 SELECT 查询` }
    }
    const steps: SqlStepView[] = bounds.map(b => {
      const body = text.slice(b.bodyStart, b.bodyEnd).replace(/\s+$/, '')
      const nameMatch = body.match(/^--[ \t]*(.+)$/m)
      return { id: b.key, key: b.key, name: nameMatch ? nameMatch[1].trim() : b.key,
               cardinality: b.cardinality as 'one' | 'many', sql: body }
    })
    const oldResult = JSON.parse(JSON.stringify(rule.result || {}))
    delete rule.sqlTemplate
    delete rule.steps
    delete rule.objectType
    rule.schemaVersion = 4
    rule.mode = 'sqlSteps'
    rule.steps = steps
    rule.result = { ...oldResult, step: steps[steps.length - 1].id }
    rule.extensions = { ...(rule.extensions || {}), sqlTemplateBeforeSteps: text }
    return { rule, reason: '' }
  }
  return { rule: null, reason: '无法识别的旧格式' }
}

// ---------- 输出列候选（仅识别 AS 别名与裸标识符，全部“未验证”） ----------
export function stepOutputCandidates(sql: string): string[] {
  const code = stripSqlNoise(String(sql || ''))
  const m = /\bSELECT\b([\s\S]*?)\bFROM\b/i.exec(code)
  if (!m) return []
  const cols: string[] = []
  let depth = 0, cur = ''
  for (const ch of m[1]) {
    if (ch === '(') { depth++; cur += ch }
    else if (ch === ')') { depth--; cur += ch }
    else if (ch === ',' && depth === 0) { cols.push(cur); cur = '' }
    else cur += ch
  }
  cols.push(cur)
  const out: string[] = []
  for (let col of cols) {
    col = col.trim()
    const alias = /\bAS\s+([A-Za-z_]\w*)\s*$/i.exec(col)
    const name = alias ? alias[1] : (IDENT.test(col) ? col : '')
    if (name && !out.includes(name)) out.push(name)
  }
  return out
}

export function newStepKey(steps: any[]): string {
  const used = new Set((steps || []).map((s: any) => s?.key))
  let key = 'step', n = 1
  while (used.has(key)) { n++; key = 'step' + n }
  return key
}

export function blankSqlStepsRule(): any {
  const step = { id: crypto.randomUUID(), key: 'step', name: '', cardinality: 'one', sql: '' }
  return { id: crypto.randomUUID(), kind: 'queryRule', schemaVersion: 4, mode: 'sqlSteps',
           name: '', connection: '', inputs: [], steps: [step],
           result: { step: step.id, type: 'scalar', valueType: 'double', value: '' } }
}
