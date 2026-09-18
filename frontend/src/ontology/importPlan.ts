// 本体 Excel 导入 · 计划层（20260917 需求 §3/§4/§5/§6）。纯函数、无网络、无 Vue 依赖。
// 职责：字段校验 → 同名决策（跳过 / 保留并自动重命名）→ 生成固定名称与稳定 ID 的导入定义。
// 随机函数可注入以便确定性测试；预览生成的名称与 ID 在确认时原样使用，不再重新生成。
import type { ParseIssue, RawRow, SheetName } from './excelImport'
import { CARDINALITY_LABELS, HEADERS, LINK_DEFAULT_CARDINALITY, NAME_COLUMN, SHEETS } from './excelImport'

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
  /** 关联对象列解析结果（属性=共享属性引用；规则/动作=对象关联），确认导入时随定义一起落库。 */
  associations?: { objectTypeId: string; objectName: string }[]
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
  /** 链接同名判定用三元组键：`名称::起点对象名::目标对象名`（链接名不唯一，端点参与唯一性）。 */
  链接: Set<string>
  /** 草稿已有对象：名称 → 稳定 @id（关联对象列解析用）。 */
  objectIds: Map<string, string>
}

/** 当前草稿各类别已有名称（trim 后、大小写敏感）。属性只与共享属性库比较。 */
export function collectExistingNames(state: any): ExistingNames {
  const graph: any[] = state?.ontology?.['@graph'] || []
  const workflow = state?.workflow || {}
  const trim = (v: any) => String(v ?? '').trim()
  const objectIds = new Map<string, string>()
  for (const n of graph) {
    if (n['@type'] !== 'owl:Class') continue
    const label = trim(n['rdfs:label'])
    if (label && !objectIds.has(label)) objectIds.set(label, n['@id'])
  }
  const linkKeys = new Set<string>()
  for (const n of graph) {
    if (n['@type'] !== 'owl:ObjectProperty') continue
    const label = trim(n['rdfs:label'])
    if (!label) continue
    linkKeys.add(linkKey(label, objectName(objectIds, n['rdfs:domain']?.['@id']), objectName(objectIds, n['rdfs:range']?.['@id'])))
  }
  return {
    对象: new Set(graph.filter((n: any) => n['@type'] === 'owl:Class').map((n: any) => trim(n['rdfs:label']))),
    属性: new Set(graph.filter((n: any) => n['@type'] === 'mg:SharedProperty').map((n: any) => trim(n['rdfs:label']))),
    规则: new Set((workflow.businessRules || []).map((r: any) => trim(r.name))),
    动作: new Set((workflow.actions || []).map((a: any) => trim(a.name))),
    链接: linkKeys,
    objectIds,
  }
}

/** 链接三元组唯一键。 */
export function linkKey(name: string, from: string, to: string): string {
  return String(name ?? '').trim() + '::' + String(from ?? '').trim() + '::' + String(to ?? '').trim()
}
function objectName(objectIds: Map<string, string>, id: any): string {
  if (!id) return ''
  for (const [name, oid] of objectIds) if (oid === id) return name
  return String(id)
}

/** 随机六位数字后缀；random 注入以便测试碰撞与占位。 */
function randomSuffix(random: () => number): string {
  return String(100000 + Math.floor(random() * 900000))
}

/** 关联对象列拆分：顿号/逗号/分号/换行均可作分隔，去空去重保序。 */
function splitNames(text: string): string[] {
  const out: string[] = []
  for (const part of String(text ?? '').split(/[、,，;；\n]/)) {
    const t = part.trim()
    if (t && !out.includes(t)) out.push(t)
  }
  return out
}

interface BuildContext {
  policy: ImportPolicy
  existing: ExistingNames
  random: () => number
  newObjectId: () => string
  newPropertyId: () => string
  newRuleId: () => string
  newActionId: () => string
  newLinkId: () => string
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

/** 链接定义（端点在关联解析阶段补齐：rdfs:domain/range + 数量关系枚举值）。 */
function buildLink(id: string, name: string, comment: string, cardinality: string, reverseLabel: string) {
  const node: Record<string, any> = {
    '@id': id, '@type': 'owl:ObjectProperty',
    'rdfs:label': name, 'rdfs:comment': comment,
    'mg:cardinality': cardinality,
  }
  if (reverseLabel.trim()) node['mg:reverseLabel'] = reverseLabel.trim()
  return node
}

/** 每表必填列：链接的 业务定义/数量关系/反向名称 可空（默认值兜底），关联对象列全表可选。 */
const REQUIRED_HEADERS: Record<SheetName, string[]> = {
  对象: ['对象名称', '业务定义'],
  属性: ['属性名称', '业务定义', '数据类型'],
  规则: ['规则名称', '业务定义', '规则内容', '输出结果'],
  动作: ['动作名称', '业务定义', '业务效果'],
  链接: ['链接名称', '起始对象', '目标对象'],
}

// ── 单行字段校验（仅对真正待导入行执行；跳过行不再因其他缺项阻断） ──

function validateRow(sheet: SheetName, cells: Record<string, string>): ParseIssue[] {
  const issues: ParseIssue[] = []
  for (const field of REQUIRED_HEADERS[sheet]) {
    if (!String(cells[field] ?? '').trim()) issues.push({ sheet, row: 0, column: field, message: field + '：必填，请填写。' })
  }
  if (sheet === '链接' && String(cells['数量关系'] ?? '').trim() && !CARDINALITY_LABELS[String(cells['数量关系']).trim()]) {
    issues.push({ sheet, row: 0, column: '数量关系', message: '数量关系：请从下拉选择 一对一、一对多、多对一或多对多。' })
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
    链接: new Set(ctx.existing.链接),
    objectIds: new Map(ctx.existing.objectIds),
  }
  const trim = (v: string) => String(v ?? '').trim()
  let nonEmpty = 0
  // 关联解析挂起项（第二阶段在全部定义决策完成后统一解析）
  const assocPending: { d: ImportDecision; names: string[] }[] = []
  const linkPending: { d: ImportDecision; from: string; to: string }[] = []

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
    // 链接的唯一性是「名称+起点+目标」三元组（链接名可重名，端点不同即不同链接）；其余表只看名称
    const isLink = sheet === '链接'
    const linkFrom = trim(String(row.cells['起始对象']?.text ?? ''))
    const linkTo = trim(String(row.cells['目标对象']?.text ?? ''))
    const usedKey = (n: string) => (isLink ? linkKey(n, linkFrom, linkTo) : n)
    const sameName = used[sheet].has(usedKey(name))
    if (sameName && ctx.policy === 'skip') {
      // 跳过策略下，已确定不导入的重复行不再因其他缺项阻断，但仍显示原因
      const hasAssocCol = !!trim(String(row.cells['关联对象']?.text ?? ''))
      decisions.push({ kind: sheet, sheet, row: row.row, originalName: name, finalName: '', disposition: 'skip', reason: '当前本体已有同类同名定义（或文件内重复），保留原内容，不导入此行。' + (hasAssocCol ? '本行「关联对象」列不生效。' : '') })
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
    rowIssues.push(...validateRow(sheet, cells).map(i => ({ ...i, row: row.row })))
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
        if (used[sheet].has(usedKey(candidate))) continue
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
    // 构造定义（稳定 ID 在预览时生成并固定）
    let definition: Record<string, any>
    const idFor = () => sheet === '对象' ? ctx.newObjectId() : sheet === '属性' ? ctx.newPropertyId() : sheet === '规则' ? ctx.newRuleId() : sheet === '链接' ? ctx.newLinkId() : ctx.newActionId()
    if (sheet === '对象') definition = buildObject(idFor(), finalName, String(cells['业务定义'] ?? '').trim())
    else if (sheet === '属性') definition = buildSharedProperty(idFor(), finalName, String(cells['业务定义'] ?? '').trim(), propertyDataType(sheet, cells), String(cells['显示格式'] ?? ''))
    else if (sheet === '规则') definition = buildRule(idFor(), finalName, String(cells['业务定义'] ?? '').trim(), String(cells['规则内容'] ?? '').trim(), String(cells['输出结果'] ?? '').trim())
    else if (sheet === '链接') {
      definition = buildLink(idFor(), finalName, String(cells['业务定义'] ?? '').trim(), cells['数量关系']?.trim() ? CARDINALITY_LABELS[cells['数量关系'].trim()] : LINK_DEFAULT_CARDINALITY, String(cells['反向名称'] ?? ''))
      reason = '新增链接（' + linkFrom + ' → ' + linkTo + '）。'
    }
    else definition = buildAction(idFor(), finalName, String(cells['业务定义'] ?? '').trim(), String(cells['业务效果'] ?? '').trim())
    used[sheet].add(usedKey(finalName))
    const decision: ImportDecision = { kind: sheet, sheet, row: row.row, originalName: name, finalName, disposition, reason, definition }
    decisions.push(decision)
    // 关联对象列挂起：等全部对象定义决策完成后统一解析（对象行可能在属性/规则/动作行之后）
    const assocText = trim(String(cells['关联对象'] ?? ''))
    if ((sheet === '属性' || sheet === '规则' || sheet === '动作') && assocText) assocPending.push({ d: decision, names: splitNames(assocText) })
    if (sheet === '链接') linkPending.push({ d: decision, from: linkFrom, to: linkTo })
  }

  // ── 第二阶段：关联对象/链接端点解析。本批对象优先（Excel 引用原始名），其次草稿已有。 ──
  const objectIdByName = new Map<string, string>()
  for (const [n, id] of ctx.existing.objectIds) objectIdByName.set(n, id)
  for (const d of decisions) {
    if (d.sheet === '对象' && (d.disposition === 'create' || d.disposition === 'rename') && d.definition) objectIdByName.set(d.originalName, d.definition['@id'])
  }
  for (const item of assocPending) {
    if (item.d.disposition !== 'create' && item.d.disposition !== 'rename') continue
    const resolved: { objectTypeId: string; objectName: string }[] = []
    const bad: string[] = []
    for (const n of item.names) {
      const id = objectIdByName.get(n)
      if (!id) { if (!bad.includes(n)) bad.push(n); continue }
      resolved.push({ objectTypeId: id, objectName: n })
    }
    if (bad.length) {
      const msg = '关联对象：找不到对象「' + bad.join('」「') + '」。请核对名称（需为本批或当前草稿中已有的对象）。'
      issues.push({ sheet: item.d.sheet, row: item.d.row, column: '关联对象', message: msg })
      item.d.disposition = 'error'
      item.d.reason = msg
      continue
    }
    item.d.associations = resolved
  }
  for (const item of linkPending) {
    if (item.d.disposition !== 'create' && item.d.disposition !== 'rename') continue
    const from = objectIdByName.get(item.from)
    const to = objectIdByName.get(item.to)
    const miss: string[] = []
    if (!from) miss.push('起始对象「' + item.from + '」')
    if (!to) miss.push('目标对象「' + item.to + '」')
    if (miss.length) {
      const msg = '找不到对象：' + miss.join('、') + '。请核对名称（需为本批或当前草稿中已有的对象）。'
      issues.push({ sheet: item.d.sheet, row: item.d.row, column: !from ? '起始对象' : '目标对象', message: msg })
      item.d.disposition = 'error'
      item.d.reason = msg
      continue
    }
    item.d.definition!['rdfs:domain'] = { '@id': from }
    item.d.definition!['rdfs:range'] = { '@id': to }
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

/** 把计划的定义合入本体内存状态（mutate 内同步执行）。追加五类定义与关联；不动既有内容。 */
export function applyPlan(state: any, decisions: ImportDecision[]): void {
  const graph: any[] = state.ontology['@graph'] = Array.isArray(state.ontology['@graph']) ? state.ontology['@graph'] : []
  const workflow = state.workflow = (state.workflow && typeof state.workflow === 'object') ? state.workflow : {}
  for (const d of decisions) {
    if ((d.disposition !== 'create' && d.disposition !== 'rename') || !d.definition) continue
    if (d.sheet === '对象' || d.sheet === '属性' || d.sheet === '链接') graph.push(d.definition)
    else if (d.sheet === '规则') { workflow.businessRules = Array.isArray(workflow.businessRules) ? workflow.businessRules : []; workflow.businessRules.push(d.definition) }
    else if (d.sheet === '动作') { workflow.actions = Array.isArray(workflow.actions) ? workflow.actions : []; workflow.actions.push(d.definition) }
  }
  // 关联落库（与页面行为一致）：
  // 属性 → 对象端引用节点（owl:DatatypeProperty + mg:sharedProperty，同 addReference）；
  // 规则/动作 → workflow.businessRuleAssociations / actionAssociations 单一关联集合。
  for (const d of decisions) {
    if ((d.disposition !== 'create' && d.disposition !== 'rename') || !d.associations?.length) continue
    if (d.sheet === '属性') {
      const sharedId = d.definition!['@id']
      for (const a of d.associations) {
        const dup = graph.find((n: any) => n['@type'] === 'owl:DatatypeProperty' && n['mg:sharedProperty']?.['@id'] === sharedId && n['rdfs:domain']?.['@id'] === a.objectTypeId)
        if (dup) continue // 幂等：对象已引用同一共享属性则跳过
        const id = 'mg:p_' + crypto.randomUUID().replaceAll('-', '')
        graph.push({ '@id': id, '@type': 'owl:DatatypeProperty', 'mg:apiName': id.slice(3), 'rdfs:domain': { '@id': a.objectTypeId }, 'mg:sharedProperty': { '@id': sharedId } })
      }
    } else if (d.sheet === '规则') {
      workflow.businessRuleAssociations = Array.isArray(workflow.businessRuleAssociations) ? workflow.businessRuleAssociations : []
      for (const a of d.associations) {
        if (workflow.businessRuleAssociations.some((r: any) => r.objectTypeId === a.objectTypeId && r.ruleId === d.definition!.id)) continue
        workflow.businessRuleAssociations.push({ objectTypeId: a.objectTypeId, ruleId: d.definition!.id })
      }
    } else if (d.sheet === '动作') {
      workflow.actionAssociations = Array.isArray(workflow.actionAssociations) ? workflow.actionAssociations : []
      for (const a of d.associations) {
        if (workflow.actionAssociations.some((r: any) => r.objectTypeId === a.objectTypeId && r.actionId === d.definition!.id)) continue
        workflow.actionAssociations.push({ objectTypeId: a.objectTypeId, actionId: d.definition!.id })
      }
    }
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
