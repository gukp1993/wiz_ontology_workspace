// 属性内 SQL 取值（computed/mode=inlineSql）：参数扫描与校验（纯前端逻辑，与后端
// project_validation 的内联分支消息镜像）。只描述模板，绝不执行 SQL。
// 词法边界：MySQL 字符串（'' "" 加倍 + 反斜杠转义）、反引号标识符、--（后随空白）／#／块注释。
export interface InlineSqlDraft { connection: string; sql: string; params: Record<string, any> }
export type InlineParamBinding =
  { from: 'constant'; dataType: 'string' | 'double' | 'boolean' | 'dateTime'; value: any } |
  { from: 'projectParameter'; key: string } |
  { from: 'instanceId' }

const NAME_START = (c: string) => (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || c === '_'
const NAME_CHAR = (c: string) => NAME_START(c) || (c >= '0' && c <= '9')

// MySQL 语义的词法剥离：字符串／反引号标识符／注释替换为空白（保分隔符语义），供
// SELECT／分号／{{}}／:参数 检查使用；字符串注释内的伪参数、伪分号不参与判定。
export function stripMysqlNoise(code: string): string {
  let out = '', i = 0
  const n = code.length
  while (i < n) {
    const ch = code[i]
    if (ch === "'" || ch === '"') {
      i++
      while (i < n) {
        if (code[i] === '\\') { i += 2; continue }
        if (code[i] === ch) { if (code[i + 1] === ch) { i += 2; continue } i++; break }
        i++
      }
      out += ' '
    } else if (ch === '`') {
      i++
      while (i < n) {
        if (code[i] === '`') { if (code[i + 1] === '`') { i += 2; continue } i++; break }
        i++
      }
      out += ' '
    } else if (ch === '-' && code[i + 1] === '-') {
      const nxt = code[i + 2] || ''
      if (nxt === '' || nxt === ' ' || nxt === '\t' || nxt === '\r' || nxt === '\n') while (i < n && code[i] !== '\n') i++
      else { out += ch; i++ }
    } else if (ch === '#') {
      while (i < n && code[i] !== '\n') i++
      out += ' '
    } else if (ch === '/' && code[i + 1] === '*') {
      i += 2
      while (i < n && code.slice(i, i + 2) !== '*/') i++
      i += 2
      out += ' '
    } else { out += ch; i++ }
  }
  return out
}

// 识别 SQL 模板中的 :参数（字符串、反引号、注释之外；名称字母／下划线起始）；去重保序。
export function scanSqlParams(sql: string): string[] {
  const code = stripMysqlNoise(String(sql || ''))
  const out: string[] = []
  const re = /(?<![A-Za-z0-9_])(?<!:):([A-Za-z_][A-Za-z0-9_]*)/g
  for (const m of code.matchAll(re)) if (!out.includes(m[1])) out.push(m[1])
  return out
}

export type InlineSqlCtx = { connections: { id: string; engine: string }[]; parameters: Record<string, unknown>; identityReady: boolean }

// 校验（消息与后端内联分支一致）；返回中文错误列表，空数组＝配置校验通过。
export function inlineSqlErrors(inline: InlineSqlDraft | null | undefined, ctx: InlineSqlCtx): string[] {
  const errors: string[] = []
  if (!inline || typeof inline !== 'object') return ['内联 SQL 结构无效']
  const conn = String(inline.connection || '').trim()
  const found = ctx.connections.find(c => c.id === conn)
  if (!conn || !found) errors.push('内联 SQL 引用的数据连接不存在')
  else if (found.engine !== 'mysql') errors.push('内联 SQL 需要 MySQL 连接')
  const sql = typeof inline.sql === 'string' ? inline.sql : ''
  if (!sql.trim()) { errors.push('未填写 SQL 模板'); return errors }
  const code = stripMysqlNoise(sql)
  if (/\{\{[^{}]*\}\}/.test(code)) errors.push('内联 SQL 不支持动态表名／字段名与步骤引用，请改用「引用已有规则」方式')
  if (!/^\s*SELECT\b/i.test(code)) errors.push(/^\s*WITH\b/i.test(code) ? '内联 SQL 不支持 WITH 开头语句，请改用「引用已有规则」方式' : '内联 SQL 须以 SELECT 开始')
  if (code.split(';').filter(s => s.trim()).length !== 1) errors.push('内联 SQL 只能填写一条 SELECT')
  const names = scanSqlParams(sql)
  const params = inline.params && typeof inline.params === 'object' && !Array.isArray(inline.params) ? inline.params : {}
  for (const name of names) {
    const b = params[name]
    if (!b || typeof b !== 'object' || Array.isArray(b)) { errors.push(`参数 :${name} 未绑定取值`); continue }
    if (b.from === 'constant') {
      if (!['string', 'double', 'boolean', 'dateTime'].includes(b.dataType)) { errors.push(`参数 :${name} 的常量数据类型无效`); continue }
      if (!constantValueOk(b.dataType, b.value)) errors.push(`参数 :${name} 的常量值无效`)
    } else if (b.from === 'projectParameter') {
      const key = String(b.key || '').trim()
      if (!key || !(key in ctx.parameters)) errors.push(`参数 :${name} 引用的项目参数「${key || '未选择'}」不存在或已失效`)
    } else if (b.from === 'instanceId') {
      if (!ctx.identityReady) errors.push(`当前对象未配置实例身份，不能绑定实例编号参数 :${name}`)
    } else errors.push(`参数 :${name} 的绑定来源无效`)
  }
  return errors
}

// 明确填写的 0／false／空字符串是有效常量；只有缺失（null/undefined）或类型不符无效。
function constantValueOk(dataType: string, value: any): boolean {
  if (value === undefined || value === null) return false
  if (dataType === 'double') { const s = String(value).trim(); return s !== '' && Number.isFinite(Number(s)) }
  if (dataType === 'boolean') return value === true || value === false || value === 'true' || value === 'false'
  return true
}

// 提交裁剪：只保留 SQL 中仍存在的参数（移除的参数不进生效配置；保留项原样深拷贝）。
export function effectiveParams(params: Record<string, any>, sql: string): Record<string, any> {
  const out: Record<string, any> = {}
  for (const name of scanSqlParams(sql)) {
    const b = params?.[name]
    if (b && typeof b === 'object' && !Array.isArray(b)) out[name] = JSON.parse(JSON.stringify(b))
  }
  return out
}

export function blankInlineSql(): InlineSqlDraft { return {connection: '', sql: '', params: {}} }
