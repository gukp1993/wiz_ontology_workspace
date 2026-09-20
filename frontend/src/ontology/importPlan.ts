// 本体 Excel 导入 · 计划层（20260917 需求 §3/§4/§5/§6）。纯函数、无网络、无 Vue 依赖。
// 职责：字段校验 → 同名决策（跳过 / 保留并自动重命名）→ 生成固定名称与稳定 ID 的导入定义。
// 随机函数可注入以便确定性测试；预览生成的名称与 ID 在确认时原样使用，不再重新生成。
import type { ParseIssue, RawCell, RawRow, SheetName } from './excelImport'
import { HEADERS, HEADER_ALIASES, NAME_COLUMN, OPTIONAL_HEADERS, REQUIRED_HEADERS, SHEETS } from './excelImport'

export type ImportPolicy = 'skip' | 'rename'
export type ImportKind = SheetName
export type Disposition = 'create' | 'skip' | 'rename' | 'error'

export interface ImportDecision {
  kind: ImportKind
  sheet: string
  row: number
  originalName: string
  finalName: string
  disposition: Disposition
  reason: string
  definition?: Record<string, any>
}

export interface PlanOutcome {
  decisions: ImportDecision[]
  issues: ParseIssue[]
  blocked: boolean          // 任一待导入行有问题 → 整批不写入
  emptyFile: boolean        // 模板没有填写内容
  allSkipped: boolean       // 全部同名被跳过
}

// ── 枚举映射（需求 §4：Excel 文案 → 本体类型） ──
export const DATA_TYPE_MAP: Record<string, { type: string }> = {
  '文本': { type: 'string' },
  '数值': { type: 'double' },
  '是／否': { type: 'boolean' },
  '时间': { type: 'dateTime' },
  '数组': { type: 'array' },
  '结构体': { type: 'struct' },
  '时间序列': { type: 'timeSeries' },
}
/** 观测值类型仅四项；兼容手输半角「是/否」。 */
const OBSERVATION_MAP: Record<string, string> = {
  '文本': 'string', '数值': 'double', '是／否': 'boolean', '是/否': 'boolean', '时间': 'dateTime',
}
const SERIES_VALUE_TYPES = ['string', 'double', 'boolean', 'dateTime']

export interface ExistingNames {
  对象: Set<string>
  属性: Set<string>
  规则: Set<string>
  动作: Set<string>
}

/** 当前草稿各类别已有名称（trim 后、大小写敏感）。属性只与共享属性库比较。 */
export function collectExistingNames(state: any): ExistingNames {
  const graph: any[] = state?.ontology?.['@graph'] || []
  const workflow = state?.workflow || {}
  const trim = (v: any) => String(v ?? '').trim()
  return {
    对象: new Set(graph.filter((n: any) => n['@type'] === 'owl:Class').map((n: any) => trim(n['rdfs:label']))),
    属性: new Set(graph.filter((n: any) => n['@type'] === 'mg:SharedProperty').map((n: any) => trim(n['rdfs:label']))),
    规则: new Set((workflow.businessRules || []).map((r: any) => trim(r.name))),
    动作: new Set((workflow.actions || []).map((a: any) => trim(a.name))),
  }
}

/** 随机六位数字后缀；random 注入以便测试碰撞与占位。 */
function randomSuffix(random: () => number): string {
  return String(100000 + Math.floor(random() * 900000))
}

interface BuildContext {
  policy: ImportPolicy
  existing: ExistingNames
  random: () => number
  newObjectId: () => string
  newPropertyId: () => string
  newRuleId: () => string
  newActionId: () => string
}

// ── 各类定义构造（对齐现有库结构；不新增本体字段） ──

function buildObject(id: string, name: string, comment: string) {
  return { '@id': id, '@type': 'owl:Class', 'rdfs:label': name, 'rdfs:comment': comment }
}

function buildSharedProperty(id: string, name: string, comment: string, dataType: { type: string; valueType?: string }, formatting?: string) {
  const node: Record<string, any> = {
    '@id': id, '@type': 'mg:SharedProperty',
    'rdfs:label': name, 'rdfs:comment': comment,
    // dataType 由 schema 形态的 range/valueShape 组合表达；这里按内存 JSON-LD 形态写：
    // 普通 → rdfs:range=xsd:<type>；时间序列 → range=xsd:<valueType> + mg:valueShape=timeSeries
    'rdfs:range': { '@id': 'xsd:' + (dataType.type === 'timeSeries' ? dataType.valueType : dataType.type) },
    'mg:valueSuffix': '',
    'mg:visibility': 'normal',
  }
  if (dataType.type === 'timeSeries') node['mg:valueShape'] = 'timeSeries'
  if (formatting && formatting.trim()) node['mg:formatting'] = { '@type': '@json', '@value': { mode: 'natural', instruction: formatting.trim() } }
  return node
}

function buildRule(id: string, name: string, description: string, content: string, output: string) {
  return { id, name, description, content, output }
}

function buildAction(id: string, name: string, description: string, effect: string) {
  return { id, name, description, effect, definitionVersion: 2, status: 'experimental' }
}

// ── 单行字段校验（仅对真正待导入行执行；跳过行不再因其他缺项阻断） ──

function validateRow(sheet: SheetName, cells: Record<string, string>, row?: RawRow): ParseIssue[] {
  const issues: ParseIssue[] = []
  // 20260920 字段精简：必填范围按 REQUIRED_HEADERS（规则/动作只要求名称+业务定义；属性前三项）。
  const requireFields = REQUIRED_HEADERS[sheet]
  for (const field of requireFields) {
    // 缺必填提示需点名具体字段（20260920 需求：待导入规则/动作缺业务定义时提示「请填写业务定义」）
    if (!String(cells[field] ?? '').trim()) issues.push({ sheet, row: 0, column: field, message: field + '：必填，请填写' + field + '。' })
  }
  // 新旧效果列冲突（仅动作表且两列都存在时）
  if (sheet === '动作' && row && effectConflict(row)) {
    issues.push({ sheet, row: 0, column: '预期效果', message: '预期效果：新列与旧「业务效果」列都填写且内容不同，无法确定取值；请删除其中一列内容。' })
  }
  // 旧别名列（规则「输出结果」、动作「业务效果」）沿用同样严格校验：公式不执行、错误值与非文字
  // 单元格必须修正；不能因为列名是历史别名就静默采用（尤其公式的缓存值）。
  if (row) {
    const aliasCells: [string, RawCell | undefined][] = sheet === '规则'
      ? [['输出结果', legacyOutputCell(row)]]
      : sheet === '动作' ? [['业务效果', row.cells['业务效果']]] : []
    for (const [field, cell] of aliasCells) {
      if (!cell) continue
      if (cell.kind === 'formula') issues.push({ sheet, row: 0, column: field, message: field + '：业务内容不能使用 Excel 公式，请直接填写文字（不执行公式）。' })
      else if (cell.kind === 'error') issues.push({ sheet, row: 0, column: field, message: field + '：单元格是 Excel 错误值（如 #REF!），请修正为文字。' })
      else if (cell.kind === 'number' || cell.kind === 'boolean' || cell.kind === 'date') {
        issues.push({ sheet, row: 0, column: field, message: field + '：请填写文字，不要填成' + (cell.kind === 'number' ? '数值' : cell.kind === 'boolean' ? '是／否' : '日期') + '格式。' })
      }
    }
  }
  if (sheet === '属性' && cells['数据类型'] !== undefined) {
    const raw = String(cells['数据类型'] ?? '').trim()
    if (raw && !DATA_TYPE_MAP[raw]) {
      issues.push({ sheet, row: 0, column: '数据类型', message: '数据类型：请从下拉选择 文本、数值、是／否、时间、数组、结构体或时间序列。' })
    } else if (raw === '时间序列') {
      const obs = String(cells['观测值类型'] ?? '').trim()
      if (!obs) issues.push({ sheet, row: 0, column: '观测值类型', message: '观测值类型：数据类型为时间序列，请选择文本、数值、是／否或时间。' })
      else if (!SERIES_VALUE_TYPES.includes(OBSERVATION_MAP[obs] || '')) {
        issues.push({ sheet, row: 0, column: '观测值类型', message: '观测值类型：时间序列只能选择 文本、数值、是／否或时间。' })
      }
    } else if (raw && raw !== '时间序列') {
      const obs = String(cells['观测值类型'] ?? '').trim()
      if (obs) issues.push({ sheet, row: 0, column: '观测值类型', message: '观测值类型：数据类型不是时间序列时请留空；请清空该单元格。' })
    }
  }
  return issues
}

function propertyDataType(sheet: SheetName, cells: Record<string, string>): { type: string; valueType?: string } {
  const raw = String(cells['数据类型'] ?? '').trim()
  const base = DATA_TYPE_MAP[raw] || { type: 'string' }
  if (base.type !== 'timeSeries') return base
  const obs = OBSERVATION_MAP[String(cells['观测值类型'] ?? '').trim()] || 'double'
  return { type: 'timeSeries', valueType: obs }
}

// ── 新旧列合并取值（20260920 表头精简）────────────────────────────────────────

/** 旧规则「输出结果」单元格：规范列名优先；解析层对与规范同名的旧列按列位登记时（key 为列字母），
 *  按旧模板固定列位（第 4 列 D）回读，保证旧四列文件的历史 output 零丢失。 */
function legacyOutputCell(row: RawRow): RawCell | undefined {
  return row.cells['输出结果'] ?? row.cells['D']
}

/** 旧规则「输出结果」列值（历史 output，零丢失保留；新模板无此列则为空）。 */
function legacyOutputOf(row: RawRow): string {
  return String(legacyOutputCell(row)?.text ?? '').trim()
}

/** 动作效果：新列「预期效果」与旧列「业务效果」合并——一侧空取另一侧，trim 后相同取一份。 */
function effectOf(row: RawRow): string {
  const canonical = String(row.cells['预期效果']?.text ?? '').trim()
  const alias = String(row.cells['业务效果']?.text ?? '').trim()
  if (canonical && alias && canonical !== alias) return canonical  // 冲突由 validateRow 阻断该批
  return canonical || alias
}

/** 新旧效果列都非空且不同 → 阻断该批（绝不择一丢弃）。 */
function effectConflict(row: RawRow): boolean {
  const canonical = String(row.cells['预期效果']?.text ?? '').trim()
  const alias = String(row.cells['业务效果']?.text ?? '').trim()
  return !!(canonical && alias && canonical !== alias)
}

// ── 计划主入口 ──

/**
 * 生成导入计划。decisions 里 create/rename 行携带完整 definition（含预生成的稳定 ID），
 * 确认时原样合入，不重新生成。issues 的 row 在此处已填真实 Excel 行号。
 */
export function buildPlan(rows: RawRow[], ctx: BuildContext): PlanOutcome {
  const decisions: ImportDecision[] = []
  const issues: ParseIssue[] = []
  // 名称占用：现有名称 + 本批已占位名称（含 rename 生成的），防止随机名抢占文件中另一行的原始名
  const used: ExistingNames = {
    对象: new Set(ctx.existing.对象), 属性: new Set(ctx.existing.属性),
    规则: new Set(ctx.existing.规则), 动作: new Set(ctx.existing.动作),
  }
  const trim = (v: string) => String(v ?? '').trim()
  let nonEmpty = 0

  for (const row of rows) {
    const sheet = row.sheet
    const nameCell = row.cells[NAME_COLUMN[sheet]]
    const nameText = nameCell ? String(nameCell.text ?? '') : ''
    // 文本类型异常（数字/日期/布尔/公式/错误值）在名称列：按问题行处理，不能借跳过隐藏
    if (nameCell && nameCell.kind !== 'text') {
      issues.push({ sheet, row: row.row, column: NAME_COLUMN[sheet], message: NAME_COLUMN[sheet] + '：请填写文字，不要填成' + (nameCell.kind === 'number' ? '数值' : nameCell.kind === 'boolean' ? '是／否' : nameCell.kind === 'date' ? '日期' : '公式或错误值') + '格式。' })
      continue
    }
    const name = trim(nameText)
    if (!name) {
      // 有其他内容但名称为空：非空行缺名称是问题，不能借跳过策略隐藏
      const hasOther = HEADERS[sheet].some(f => f !== NAME_COLUMN[sheet] && trim(String(row.cells[f]?.text ?? '')) !== '')
      if (hasOther) issues.push({ sheet, row: row.row, column: NAME_COLUMN[sheet], message: NAME_COLUMN[sheet] + '：必填，请填写。' })
      continue
    }
    nonEmpty++
    const sameName = used[sheet].has(name)
    if (sameName && ctx.policy === 'skip') {
      // 跳过策略下，已确定不导入的重复行不再因其他缺项阻断，但仍显示原因
      decisions.push({ kind: sheet, sheet, row: row.row, originalName: name, finalName: '', disposition: 'skip', reason: '当前本体已有同类同名定义（或文件内重复），保留原内容，不导入此行。' })
      continue
    }
    // 真正待导入行：先校验全部必填与类型规则
    const cells: Record<string, string> = {}
    for (const f of HEADERS[sheet]) cells[f] = String(row.cells[f]?.text ?? '')
    if (nameCell && nameCell.kind !== 'text') cells[NAME_COLUMN[sheet]] = nameText
    const rowIssues: typeof issues = []
    for (const f of HEADERS[sheet]) {
      const cell = row.cells[f]
      if (!cell) continue
      if (cell.kind === 'formula') rowIssues.push({ sheet, row: row.row, column: f, message: f + '：业务内容不能使用 Excel 公式，请直接填写文字（不执行公式）。' })
      else if (cell.kind === 'error') rowIssues.push({ sheet, row: row.row, column: f, message: f + '：单元格是 Excel 错误值（如 #REF!），请修正为文字。' })
      else if (cell.kind === 'number' || cell.kind === 'boolean' || cell.kind === 'date') {
        rowIssues.push({ sheet, row: row.row, column: f, message: f + '：请填写文字，不要填成' + (cell.kind === 'number' ? '数值' : cell.kind === 'boolean' ? '是／否' : '日期') + '格式。' })
      }
    }
    rowIssues.push(...validateRow(sheet, cells, row).map(i => ({ ...i, row: row.row })))
    if (rowIssues.length) {
      issues.push(...rowIssues)
      decisions.push({ kind: sheet, sheet, row: row.row, originalName: name, finalName: '', disposition: 'error', reason: rowIssues[0].message })
      continue
    }
    let finalName = name
    let disposition: Disposition = 'create'
    let reason = '新增到当前本体草稿。'
    if (sameName && ctx.policy === 'rename') {
      // 随机后缀：在现有名称 + 已占位名称范围内检查唯一，碰撞重试；预留本批原始名
      let ok = false
      for (let attempt = 0; attempt < 100; attempt++) {
        const candidate = name + '_' + randomSuffix(ctx.random)
        if (used[sheet].has(candidate)) continue
        finalName = candidate; disposition = 'rename'
        reason = '作为独立新定义导入，已有同名内容保持不变。'
        ok = true
        break
      }
      if (!ok) {
        issues.push({ sheet, row: row.row, column: NAME_COLUMN[sheet], message: NAME_COLUMN[sheet] + '：自动重命名 100 次仍未找到不冲突的名称，请修改名称后重试。' })
        decisions.push({ kind: sheet, sheet, row: row.row, originalName: name, finalName: '', disposition: 'error', reason: '自动重命名失败：名称冲突次数过多。' })
        continue
      }
    }
    // 旧模板「输出结果」有值：随导入原样保留为历史补充说明（预览可见；不阻断，不并入业务定义/规则内容）
    if (sheet === '规则' && legacyOutputOf(row)) reason += '旧输出结果将保留为历史补充说明。'
    // 构造定义（稳定 ID 在预览时生成并固定）
    let definition: Record<string, any>
    const idFor = () => sheet === '对象' ? ctx.newObjectId() : sheet === '属性' ? ctx.newPropertyId() : sheet === '规则' ? ctx.newRuleId() : ctx.newActionId()
    if (sheet === '对象') definition = buildObject(idFor(), finalName, String(cells['业务定义'] ?? '').trim())
    else if (sheet === '属性') definition = buildSharedProperty(idFor(), finalName, String(cells['业务定义'] ?? '').trim(), propertyDataType(sheet, cells), String(cells['显示格式'] ?? ''))
    else if (sheet === '规则') definition = buildRule(idFor(), finalName, String(cells['业务定义'] ?? '').trim(), String(cells['规则内容'] ?? '').trim(), legacyOutputOf(row))
    else definition = buildAction(idFor(), finalName, String(cells['业务定义'] ?? '').trim(), effectOf(row))
    used[sheet].add(finalName)
    decisions.push({ kind: sheet, sheet, row: row.row, originalName: name, finalName, disposition, reason, definition })
  }

  const createCount = decisions.filter(d => d.disposition === 'create').length
  const renameCount = decisions.filter(d => d.disposition === 'rename').length
  const errorCount = decisions.filter(d => d.disposition === 'error').length
  const skipCount = decisions.filter(d => d.disposition === 'skip').length
  return {
    decisions,
    issues,
    blocked: errorCount > 0,
    emptyFile: nonEmpty === 0,
    allSkipped: nonEmpty > 0 && createCount + renameCount === 0 && errorCount === 0,
  }
}

/** 汇总数量（N = 新增 + 自动重命名，重命名不重复计数）。 */
export function planCounts(decisions: ImportDecision[]) {
  const by = (d: Disposition) => decisions.filter(x => x.disposition === d).length
  return {
    create: by('create'), skip: by('skip'), rename: by('rename'), error: by('error'),
    importable: by('create') + by('rename'),
  }
}

/** 把计划的定义合入本体内存状态（mutate 内同步执行）。只追加四类定义，不动关联与既有内容。 */
export function applyPlan(state: any, decisions: ImportDecision[]): void {
  const graph: any[] = state.ontology['@graph'] = Array.isArray(state.ontology['@graph']) ? state.ontology['@graph'] : []
  const workflow = state.workflow = (state.workflow && typeof state.workflow === 'object') ? state.workflow : {}
  for (const d of decisions) {
    if ((d.disposition !== 'create' && d.disposition !== 'rename') || !d.definition) continue
    if (d.sheet === '对象' || d.sheet === '属性') graph.push(d.definition)
    else if (d.sheet === '规则') { workflow.businessRules = Array.isArray(workflow.businessRules) ? workflow.businessRules : []; workflow.businessRules.push(d.definition) }
    else if (d.sheet === '动作') { workflow.actions = Array.isArray(workflow.actions) ? workflow.actions : []; workflow.actions.push(d.definition) }
  }
}

/** 结果不明后的核对：用固定新增 ID 集合对比最新草稿。 */
export type VerifyOutcome = 'all-present' | 'none-present' | 'partial'
export function verifyImported(state: any, decisions: ImportDecision[]): VerifyOutcome {
  const wanted = new Map<string, any>()
  for (const d of decisions) {
    if ((d.disposition === 'create' || d.disposition === 'rename') && d.definition) {
      const id = d.sheet === '对象' || d.sheet === '属性' ? d.definition['@id'] : d.definition.id
      wanted.set(String(id), d)
    }
  }
  if (!wanted.size) return 'all-present'
  const graph: any[] = state?.ontology?.['@graph'] || []
  const workflow = state?.workflow || {}
  let found = 0
  for (const [id, d] of wanted) {
    let hit: any = null
    if (d.sheet === '对象') hit = graph.find((n: any) => n['@id'] === id)
    else if (d.sheet === '属性') hit = graph.find((n: any) => n['@id'] === id && n['@type'] === 'mg:SharedProperty')
    else if (d.sheet === '规则') hit = (workflow.businessRules || []).find((r: any) => r.id === id)
    else hit = (workflow.actions || []).find((a: any) => a.id === id)
    if (hit) found++
  }
  return found === wanted.size ? 'all-present' : found === 0 ? 'none-present' : 'partial'
}
