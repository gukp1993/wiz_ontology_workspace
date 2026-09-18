// 本体 Excel 导入 · 解析层（20260917 需求 §4/§5）。
// 职责：懒加载 XLSX 依赖，读取四个业务表的真实内容，处理文件级校验（魔数/损坏/加密/尺寸/结构），
// 输出「原始行」给 importPlan.ts 做字段校验与同名决策。UI 不感知依赖的数据结构。
// 关键约定：
//  - 只读业务列（按表头名称定位列），隐藏辅助列（模板 H 列枚举区）一律忽略；
//  - 不执行公式：f 字段存在即为公式单元格 → 阻断；不使用公式缓存值；
//  - 数据验证（下拉）不算数据；纯格式空单元格（t==='z'）不算记录；
//  - 额外可见列/额外 Sheet 有用户数据 → 报错并列位置，不静默丢弃；
//  - 文本字段必须是文字：数字/布尔/日期单元格指出需填写文字，不擅自改写。
export const SHEETS = ['对象', '属性', '规则', '动作', '链接'] as const
export type SheetName = (typeof SHEETS)[number]

export const HEADERS: Record<SheetName, string[]> = {
  对象: ['对象名称', '业务定义'],
  属性: ['属性名称', '业务定义', '数据类型', '观测值类型', '显示格式', '关联对象'],
  规则: ['规则名称', '业务定义', '规则内容', '输出结果', '关联对象'],
  动作: ['动作名称', '业务定义', '业务效果', '关联对象'],
  链接: ['链接名称', '业务定义', '起始对象', '目标对象', '数量关系', '反向名称'],
}
/** 名称列（每表第一业务列）。 */
export const NAME_COLUMN: Record<SheetName, string> = { 对象: '对象名称', 属性: '属性名称', 规则: '规则名称', 动作: '动作名称', 链接: '链接名称' }

/** 允许缺失的表头/表（20260919 关系导入）：旧模板没有「关联对象」列；整张「链接」表可缺省。 */
export const OPTIONAL_HEADERS = new Set(['关联对象'])
export const OPTIONAL_SHEETS = new Set<SheetName>(['链接'])

/** 数量关系：模板中文文案 ↔ 本体枚举值（与页面 CARDINALITY 一致）。 */
export const CARDINALITY_LABELS: Record<string, string> = { '一对一': 'one-to-one', '一对多': 'one-to-many', '多对一': 'many-to-one', '多对多': 'many-to-many' }
export const LINK_DEFAULT_CARDINALITY = 'many-to-one'

export const MAX_FILE_BYTES = 1024 * 1024
export const MAX_BUSINESS_ROWS = 1000

export interface RawCell { text: string; kind: 'text' | 'number' | 'boolean' | 'date' | 'error' | 'formula' }
export interface RawRow { sheet: SheetName; row: number; cells: Record<string, RawCell> }
export interface ParseIssue { sheet: string; row: number; column: string; message: string }
export interface ParseResult { rows: RawRow[]; issues: ParseIssue[] }

/** 读取结果需要感知隐藏列/额外列：解析层内部结构。 */
interface SheetScan {
  headerRow: number
  headers: { column: string; name: string; hidden: boolean }[]
  dataRows: { row: number; cells: Record<string, { raw: unknown; type: string; hasFormula: boolean; colHidden: boolean }> }[]
}

function colLetter(n: number): string {
  let s = ''
  while (n > 0) { const m = (n - 1) % 26; s = String.fromCharCode(65 + m) + s; n = Math.floor((n - 1) / 26) }
  return s
}

function parseColLetter(ref: string): number {
  let n = 0
  for (const ch of ref) { if (ch >= 'A' && ch <= 'Z') n = n * 26 + (ch.charCodeAt(0) - 64); else break }
  return n
}

/** 非空业务内容判定：数字 0、布尔 false、空白字符串之外都算「有内容」。 */
export function cellHasContent(raw: unknown, type: string): boolean {
  if (type === 'z') return false
  if (raw === undefined || raw === null) return false
  if (typeof raw === 'string' && raw.trim() === '') return false
  return true
}

function cellKind(type: string, hasFormula: boolean): RawCell['kind'] {
  if (hasFormula) return 'formula'
  if (type === 'n') return 'number'
  if (type === 'b') return 'boolean'
  if (type === 'd') return 'date'
  if (type === 'e') return 'error'
  return 'text'
}

function toDisplay(raw: unknown): string {
  if (raw === undefined || raw === null) return ''
  if (typeof raw === 'string') return raw
  if (typeof raw === 'number') return String(raw)
  if (typeof raw === 'boolean') return raw ? 'TRUE' : 'FALSE'
  if (raw instanceof Date) return raw.toISOString().slice(0, 10)
  return String(raw)
}

export interface ParsedWorkbook { sheets: Record<string, SheetScan>; extraSheets: string[] }

/**
 * XLSX 依赖获取。默认懒加载 @e965/xlsx（SheetJS 0.20.3 维护分支，Apache-2.0，
 * 上游 https://registry.npmjs.org/@e965/xlsx ，版本锁定在 package.json）。
 * 浏览器端由 Vite 打包进懒加载 chunk；Node 测试经 setXlsxLoader 注入同一实现。
 */
let xlsxModule: any = null
let xlsxLoading: Promise<any> | null = null
export function setXlsxModule(mod: any) { xlsxModule = mod }

/** 动态分块加载失败的可读文案（20260918）。
 * 场景：工作台重新构建后，仍在运行的旧页面持有的分块名 404——浏览器只会抛出
 * "Failed to fetch dynamically imported module" 或 TDZ 类 ReferenceError，用户看不懂。
 * 这里统一转成「刷新页面」的明确指引；同时保留原始错误信息便于排查。 */
export const CHUNK_LOAD_HINT = '页面资源已更新或加载失败（可能刚重新构建过），请刷新页面后重试。'
export function chunkLoadFailure(err: any): Error {
  const detail = String((err && (err.message || err)) || '')
  return new Error(CHUNK_LOAD_HINT + (detail ? '（' + detail + '）' : ''))
}

async function getXlsx(): Promise<any> {
  if (xlsxModule) return xlsxModule
  if (!xlsxLoading) {
    xlsxLoading = import('@e965/xlsx').catch(err => {
      xlsxLoading = null            // 允许刷新后重试同一模块
      throw chunkLoadFailure(err)
    })
  }
  xlsxModule = await xlsxLoading
  return xlsxModule
}

/** 解析 xlsx 字节为内部工作簿结构。魔数/加密/损坏在此拒绝。 */
export async function readWorkbook(data: ArrayBuffer): Promise<{ wb: any; error?: string }> {
  const bytes = new Uint8Array(data)
  // ZIP 魔数 PK\x03\x04：.xls（OLE2 D0CF11E0）、CSV、加密 xlsx（OLE2 容器）都到不了这里
  if (!(bytes[0] === 0x50 && bytes[1] === 0x4b && (bytes[2] === 3 || bytes[2] === 5 || bytes[2] === 7))) {
    if (bytes[0] === 0xd0 && bytes[1] === 0xcf) return { wb: null, error: '这是加密或旧版 .xls 格式的文件，请另存为 .xlsx 后重试。' }
    return { wb: null, error: '这不是有效的 .xlsx 文件（仅支持 Excel 工作簿）。' }
  }
  const XLSX = await getXlsx()
  // xlsx 本质是 ZIP 包：校验 End of Central Directory 记录存在，拒绝被当作 CSV/文本解析的非 Excel 文件
  const tail = bytes.subarray(Math.max(0, bytes.length - 65536))
  let eocd = -1
  for (let i = tail.length - 22; i >= 0; i--) {
    if (tail[i] === 0x50 && tail[i + 1] === 0x4b && tail[i + 2] === 0x05 && tail[i + 3] === 0x06) { eocd = i; break }
  }
  if (eocd < 0) return { wb: null, error: '文件已损坏或不是有效的 .xlsx 工作簿，无法读取。' }
  let wb: any
  try { wb = XLSX.read(data, { type: 'array', cellFormula: true, cellStyles: true }) }
  catch { return { wb: null, error: '文件已损坏或不是有效的 .xlsx 工作簿，无法读取。' } }
  const names: string[] = wb.SheetNames || []
  if (!names.length) return { wb: null, error: '文件中没有工作表，请使用下载的模板填写。' }
  return { wb }
}

/** 扫描四个业务表：表头列位、隐藏列、数据行原始值。 */
export function scanSheets(wb: any): { scan: Record<string, SheetScan>; issues: ParseIssue[] } {
  const scan: Record<string, SheetScan> = {} as any
  const issues: ParseIssue[] = []
  for (const name of SHEETS) {
    const ws = wb.Sheets[name]
    if (!ws) {
      // 「链接」表可缺省：旧模板/只填定义的文件没有这张表不算错
      if (OPTIONAL_SHEETS.has(name)) continue
      issues.push({ sheet: name, row: 0, column: '', message: '缺少工作表「' + name + '」。请使用下载的模板填写。' })
      continue
    }
    const ref = String(ws['!ref'] || 'A1:A1')
    const range = decodeRange(ref)
    // 列隐藏信息（模板 H 列等辅助列）
    const hiddenCols = new Set<number>()
    for (const col of ws['!cols'] || []) {
      // !cols 是稀疏数组，下标即列号-1
      const idx = (ws['!cols'] as any[]).indexOf(col)
      if (col && col.hidden) hiddenCols.add(idx + 1)
    }
    // 表头：第 1 行（模板固定），按名称识别
    const headerRow = range.s.r + 1
    const headers: SheetScan['headers'] = []
    const seen = new Map<string, number>()
    for (let c = range.s.c; c <= range.e.c; c++) {
      const cell = ws[colLetter(c + 1) + headerRow]
      const name = cell && typeof cell.v === 'string' ? cell.v.trim() : ''
      if (!name) continue
      const hidden = hiddenCols.has(c + 1)
      headers.push({ column: colLetter(c + 1), name, hidden })
      if (!hidden && name) seen.set(name, (seen.get(name) || 0) + 1)
    }
    const expect = HEADERS[name]
    for (const h of expect) {
      if (OPTIONAL_HEADERS.has(h) && (seen.get(h) || 0) === 0) continue // 旧模板允许没有「关联对象」列
      if ((seen.get(h) || 0) === 0) issues.push({ sheet: name, row: headerRow, column: '', message: '缺少表头「' + h + '」。无内容的表也要保留完整表头。' })
      else if ((seen.get(h) || 0) > 1) issues.push({ sheet: name, row: headerRow, column: h, message: '表头「' + h + '」重复出现，无法识别业务列。' })
    }
    const businessCols = new Map(headers.filter(h => !h.hidden && expect.includes(h.name)).map(h => [h.name, h.column]))
    const knownCols = new Set(headers.map(h => h.column))
    // 数据行：从表头下一行到 usedRange 结束；只关注业务列与「额外可见列」
    const dataRows: SheetScan['dataRows'] = []
    for (let r = headerRow + 1; r <= range.e.r + 1; r++) {
      const cells: SheetScan['dataRows'][number]['cells'] = {}
      let hasAny = false
      for (let c = range.s.c; c <= range.e.c; c++) {
        const col = colLetter(c + 1)
        const cell = ws[col + r]
        const type = cell ? String(cell.t) : 'z'
        const hasFormula = !!(cell && typeof cell.f === 'string' && cell.f.length)
        const hidden = hiddenCols.has(c + 1)
        const hasContent = cellHasContent(cell?.v, type)
        if (!hasContent) continue
        // 额外可见列（非隐藏、不在任何表头里）有内容 → 报错不丢弃
        if (!hidden && !knownCols.has(col)) {
          issues.push({ sheet: name, row: r, column: col, message: '存在模板以外内容（可见列 ' + col + '），本次无法导入；请删除该列内容或使用模板。' })
          hasAny = true
          continue
        }
        // 隐藏辅助列（模板 H 列枚举区）一律忽略：不算记录内容
        if (hidden) continue
        hasAny = true
        const fieldName = [...businessCols.entries()].find(([, column]) => column === col)?.[0]
        cells[fieldName || col] = { raw: cell.v, type, hasFormula, colHidden: hidden }
      }
      if (hasAny) dataRows.push({ row: r, cells })
    }
    scan[name] = { headerRow, headers, dataRows }
  }
  const extraSheets = (wb.SheetNames || []).filter((n: string) => !(SHEETS as readonly string[]).includes(n))
  return { scan, issues }
}

/** 额外 Sheet 有内容则报错；纯空 Sheet 忽略。 */
export function checkExtraSheets(wb: any): ParseIssue[] {
  const issues: ParseIssue[] = []
  for (const name of wb.SheetNames || []) {
    if ((SHEETS as readonly string[]).includes(name)) continue
    const ws = wb.Sheets[name]
    const ref = String(ws?.['!ref'] || '')
    if (!ref || ref === 'A1:A1') continue
    const range = decodeRange(ref)
    let hasData = false
    for (let r = range.s.r; r <= range.e.r && !hasData; r++) {
      for (let c = range.s.c; c <= range.e.c; c++) {
        const cell = ws[colLetter(c + 1) + (r + 1)]
        if (cellHasContent(cell?.v, cell ? String(cell.t) : 'z')) { hasData = true; break }
      }
    }
    if (hasData) issues.push({ sheet: name, row: 0, column: '', message: '存在模板以外的工作表「' + name + '」且含有内容，本次无法导入；请删除该工作表或使用模板。' })
  }
  return issues
}

function decodeRange(ref: string): { s: { r: number; c: number }; e: { r: number; c: number } } {
  const m = /^([A-Z]+)(\d+):([A-Z]+)(\d+)$/.exec(ref) || /^([A-Z]+)(\d+)$/.exec(ref)
  if (!m) return { s: { r: 0, c: 0 }, e: { r: 0, c: 0 } }
  if (m.length === 3) { const c = parseColLetter(m[1]); return { s: { r: Number(m[2]) - 1, c: c - 1 }, e: { r: Number(m[2]) - 1, c: c - 1 } } }
  return { s: { r: Number(m[2]) - 1, c: parseColLetter(m[1]) - 1 }, e: { r: Number(m[4]) - 1, c: parseColLetter(m[3]) - 1 } }
}

/** 文本列取值：必须是文字；数字/布尔/日期给出具体字段错误（不擅改写）。 */
export function textField(cell: RawCell | undefined, sheet: string, row: number, field: string): { text: string; issue?: ParseIssue } {
  if (!cell) return { text: '' }
  if (cell.kind === 'formula') return { text: '', issue: { sheet, row, column: field, message: field + '：业务内容不能使用 Excel 公式，请直接填写文字（不执行公式）。' } }
  if (cell.kind === 'error') return { text: '', issue: { sheet, row, column: field, message: field + '：单元格是 Excel 错误值（如 #REF!），请修正为文字。' } }
  if (cell.kind === 'number' || cell.kind === 'boolean' || cell.kind === 'date') {
    return { text: cell.text, issue: { sheet, row, column: field, message: field + '：请填写文字，不要填成' + (cell.kind === 'number' ? '数值' : cell.kind === 'boolean' ? '是／否' : '日期') + '格式。' } }
  }
  return { text: cell.text }
}

export interface FileCheck { ok: boolean; error?: string }

/** 文件级约束：.xlsx、≤1MiB。选择阶段即检查，给出明确原因。 */
export function checkFileMeta(fileName: string, size: number): FileCheck {
  if (!/\.xlsx$/i.test(fileName)) return { ok: false, error: '请选择 .xlsx 文件（不支持 .xls、.xlsm、CSV 或其他格式）。' }
  if (size > MAX_FILE_BYTES) return { ok: false, error: '文件超过 1 MiB 限制（当前 ' + (size / 1024 / 1024).toFixed(2) + ' MiB）。请拆分后分批导入。' }
  return { ok: true }
}

/** 解析完整入口：字节 → 原始行 + 结构问题。业务行超限在此拦截。 */
export async function parseWorkbook(data: ArrayBuffer): Promise<{ result?: ParseResult; error?: string }> {
  const { wb, error } = await readWorkbook(data)
  if (error) return { error }
  const { scan, issues } = scanSheets(wb)
  issues.push(...checkExtraSheets(wb))
  const rows: RawRow[] = []
  for (const name of SHEETS) {
    const s = scan[name]
    if (!s) continue
    for (const dataRow of s.dataRows) {
      const cells: Record<string, RawCell> = {}
      for (const [field, c] of Object.entries(dataRow.cells)) cells[field] = { text: toDisplay(c.raw), kind: cellKind(c.type, c.hasFormula) }

      rows.push({ sheet: name, row: dataRow.row, cells })
    }
  }
  if (rows.length > MAX_BUSINESS_ROWS) return { error: '四个表合计 ' + rows.length + ' 条内容，超过 1000 条上限。请拆分后分批导入。' }
  return { result: { rows, issues } }
}
