// 本体 Excel 模板下载与导入（20260917）核心回归：解析层 + 计划层。
// 运行（仓库根）：node --import ./tests/ts_hooks.mjs tests/ontology_import.test.mjs
// 不写真实 ontology；XLSX 库经 createRequire 注入（与浏览器端同一实现）。
import { createRequire } from 'node:module'
import assert from 'node:assert/strict'
import { pathToFileURL } from 'node:url'
import { readFileSync } from 'node:fs'

const require = createRequire(new URL('../frontend/package.json', import.meta.url))
const XLSX = require('@e965/xlsx')
const fs = require('node:fs'), path = require('node:path'), os = require('node:os')
const root = path.resolve(import.meta.dirname, '..')

const excel = await import(pathToFileURL(path.join(root, 'frontend/src/ontology/excelImport.ts')).href)
excel.setXlsxModule(XLSX)
const plan = await import(pathToFileURL(path.join(root, 'frontend/src/ontology/importPlan.ts')).href)

const results = []
async function check(name, fn) {
  try { await fn(); results.push({ name, ok: true }); console.log('通过：' + name) }
  catch (e) { results.push({ name, ok: false }); console.log('失败：' + name + ' — ' + e.message) }
}

// 当前上线模板（20260920 字段精简版）与其需求目录原件；旧模板保留在仓库作为回归输入
const TEMPLATE = path.join(root, 'frontend/public/templates/ontology-import-rule-action-v1.xlsx')
const TEMPLATE_SOURCE = path.join(root, '文档/需求/20260920_规则动作字段精简与Excel模板同步/本体模型填写模板_规则动作精简.xlsx')
const LEGACY_TEMPLATE = path.join(root, 'frontend/public/templates/ontology-import-v1.xlsx')
const buf2ab = b => b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength)

/** 当前模板表头（20260920 字段精简：规则三列、动作列名为「预期效果」）；旧表头经 opts.headers 覆盖构造。 */
const NEW_HEADERS = { 对象: ['对象名称', '业务定义'], 属性: ['属性名称', '业务定义', '数据类型', '观测值类型', '显示格式'], 规则: ['规则名称', '业务定义', '规则内容'], 动作: ['动作名称', '业务定义', '预期效果'] }
/** 旧模板表头（历史文件回归输入）：规则第四列「输出结果」，动作第三列「业务效果」。 */
const LEGACY_HEADERS = { 规则: ['规则名称', '业务定义', '规则内容', '输出结果'], 动作: ['动作名称', '业务定义', '业务效果'] }

/** 构造一个四表 xlsx（默认新表头；opts.headers 可替换某表表头，opts.patch 可在写出前改单元格）。 */
function makeXlsx(sheets, opts = {}) {
  const wb = XLSX.utils.book_new()
  const all = { ...NEW_HEADERS, ...(opts.headers || {}) }
  for (const name of (opts.sheetOrder || ['对象', '属性', '规则', '动作'])) {
    const rows = sheets[name] || []
    const aoa = [all[name], ...rows]
    const ws = XLSX.utils.aoa_to_sheet(aoa)
    if (name === '属性') {
      // 模拟模板：H 列隐藏枚举区（2..5 有值）
      ws['H2'] = { t: 's', v: '文本' }; ws['H3'] = { t: 's', v: '数值' }; ws['H4'] = { t: 's', v: '是／否' }; ws['H5'] = { t: 's', v: '时间' }
      ws['!cols'] = []
      ws['!cols'][7] = { hidden: true }
      // 枚举区 H2..H5 与数据行都纳入范围（取两者较大行）——模拟真实模板的 usedRange
      const maxRow = Math.max(XLSX.utils.decode_range(ws['!ref'] || 'A1').e.r + 1, 5)
      const ref = XLSX.utils.encode_range({ s: { r: 0, c: 0 }, e: { r: maxRow - 1, c: 7 } })
      ws['!ref'] = ref
    }
    XLSX.utils.book_append_sheet(wb, ws, name)
  }
  if (opts.extraSheet) XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet([['x'], ['额外数据']]), '说明')
  if (opts.patch) opts.patch(wb)
  return XLSX.write(wb, { type: 'buffer', bookType: 'xlsx' })
}

const makeCtx = (existing, policy, random = () => 0.5) => plan.buildPlan.length ? ({
  policy, existing, random,
  newObjectId: () => 'mg:object_test' + Math.random().toString(16).slice(2, 10),
  newPropertyId: () => 'mg:p_test' + Math.random().toString(16).slice(2, 10),
  newRuleId: () => 'rule_test' + Math.random().toString(16).slice(2, 10),
  newActionId: () => 'action_test' + Math.random().toString(16).slice(2, 10),
}) : null

const ctxFor = (existing, policy, random) => ({
  policy, existing, random,
  newObjectId: () => 'mg:object_test' + Math.random().toString(16).slice(2, 10),
  newPropertyId: () => 'mg:p_test' + Math.random().toString(16).slice(2, 10),
  newRuleId: () => 'rule_test' + Math.random().toString(16).slice(2, 10),
  newActionId: () => 'action_test' + Math.random().toString(16).slice(2, 10),
})

await check('① 空模板：解析 0 行 0 问题；计划报「没有填写内容」', async () => {
  const { result, error } = await excel.parseWorkbook(buf2ab(makeXlsx({})))
  assert.ok(!error)
  assert.equal(result.rows.length, 0)
  assert.equal(result.issues.length, 0, JSON.stringify(result.issues))
  const outcome = plan.buildPlan(result.rows, ctxFor(plan.collectExistingNames({}), 'skip'))
  assert.equal(outcome.emptyFile, true)
})

await check('② 单 Sheet 有数据其余空：只导入该类', async () => {
  const { result } = await excel.parseWorkbook(buf2ab(makeXlsx({ 对象: [['储能簇', '一组电池簇']] })))
  assert.equal(result.rows.length, 1)
  const outcome = plan.buildPlan(result.rows, ctxFor(plan.collectExistingNames({}), 'skip'))
  assert.equal(outcome.blocked, false)
  assert.equal(outcome.decisions[0].disposition, 'create')
  assert.equal(outcome.decisions[0].definition['@type'], 'owl:Class')
})

await check('③ 隐藏 H 列枚举不当记录；C/D 空格式单元格不算内容', async () => {
  const { result } = await excel.parseWorkbook(buf2ab(makeXlsx({ 属性: [['额定功率', '铭牌功率', '数值', '', '']] })))
  assert.equal(result.rows.length, 1, '隐藏 H2..H5 不得生成记录')
  const row = result.rows[0]
  // 空单元格不进入 cells（未填写），计划层按空处理
  assert.equal(row.cells['观测值类型'], undefined)
  assert.equal(row.cells['显示格式'], undefined)
})

await check('④ 真实模板副本解析 0 行（业务区为空）且无结构问题', async () => {
  const buf = fs.readFileSync(TEMPLATE)
  const { result, error } = await excel.parseWorkbook(buf2ab(buf))
  assert.ok(!error, error)
  assert.equal(result.rows.length, 0)
  assert.equal(result.issues.length, 0, JSON.stringify(result.issues))
})

await check('⑤ 模板哈希与原件一致（下载字节一致）', async () => {
  const { createHash } = await import('node:crypto')
  const a = createHash('sha256').update(fs.readFileSync(TEMPLATE)).digest('hex')
  const b = createHash('sha256').update(fs.readFileSync(TEMPLATE_SOURCE)).digest('hex')
  assert.equal(a, b)
})

await check('⑥ 类型映射：普通/时间序列/数组/结构体；显示格式自然语言', async () => {
  const rows = [
    ['P1', '普通', '数值', '', ''],
    ['P2', '时序', '时间序列', '是／否', ''],
    ['P3', '数组', '数组', '', ''],
    ['P4', '结构体', '结构体', '', ''],
    ['P5', '带格式', '文本', '', '保留两位小数'],
  ]
  const { result } = await excel.parseWorkbook(buf2ab(makeXlsx({ 属性: rows })))
  const outcome = plan.buildPlan(result.rows, ctxFor(plan.collectExistingNames({}), 'skip'))
  assert.equal(outcome.blocked, false, JSON.stringify(outcome.issues))
  const defs = outcome.decisions.filter(d => d.disposition === 'create').map(d => d.definition)
  assert.equal(defs.length, 5, 'decisions=' + JSON.stringify(outcome.decisions.map(d => [d.originalName, d.disposition, d.reason])) + ' issues=' + JSON.stringify(outcome.issues))
  const byName = Object.fromEntries(defs.map(d => [d['rdfs:label'], d]))
  assert.equal(byName['P1']['rdfs:range']['@id'], 'xsd:double')
  assert.equal(byName['P2']['rdfs:range']['@id'], 'xsd:boolean')
  assert.equal(byName['P2']['mg:valueShape'], 'timeSeries')
  assert.equal(byName['P3']['rdfs:range']['@id'], 'xsd:array')
  assert.equal(byName['P4']['rdfs:range']['@id'], 'xsd:struct')
  assert.deepEqual(byName['P5']['mg:formatting'], { '@type': '@json', '@value': { mode: 'natural', instruction: '保留两位小数' } })
  assert.equal(byName['P1']['mg:formatting'], undefined)
})

await check('⑦ 非时间序列带观测值 → 阻断整批；时间序列缺观测值 → 阻断；坏枚举 → 阻断', async () => {
  const { result } = await excel.parseWorkbook(buf2ab(makeXlsx({ 属性: [
    ['A1', '普通带观测', '数值', '文本', ''],
    ['A2', '时序缺观测', '时间序列', '', ''],
    ['A3', '坏枚举', '金子', '', ''],
  ] })))
  const outcome = plan.buildPlan(result.rows, ctxFor(plan.collectExistingNames({}), 'skip'))
  assert.equal(outcome.blocked, true)
  const cols = outcome.issues.map(i => i.column)
  assert.ok(cols.includes('观测值类型') && cols.includes('数据类型'))
  assert.equal(outcome.decisions.filter(d => d.disposition === 'error').length, 3)
})

await check('⑧ 同名策略：跳过 vs 自动重命名；跨类别同名互不影响；批内重复一致', async () => {
  const rows = [['储能簇', '重复'], ['储能站', '新']]
  const mk = () => excel.parseWorkbook(buf2ab(makeXlsx({ 对象: rows })))
  const existing = plan.collectExistingNames({ ontology: { '@graph': [{ '@type': 'owl:Class', 'rdfs:label': '储能簇' }] } })
  const skip = plan.buildPlan((await mk()).result.rows, ctxFor(existing, 'skip'))
  assert.equal(skip.decisions[0].disposition, 'skip')
  assert.equal(skip.decisions[1].disposition, 'create')
  // 跳过策略下重复行不因缺业务定义之外的阻断（这里都有定义，直接确认不产生 issue）
  assert.equal(skip.blocked, false)
  const rename = plan.buildPlan((await mk()).result.rows, ctxFor(existing, 'rename', () => 0.5))
  assert.equal(rename.decisions[0].disposition, 'rename')
  assert.match(rename.decisions[0].finalName, /^储能簇_\d{6}$/)
  assert.equal(rename.decisions[1].disposition, 'create')
  // 跨类别同名互不影响：属性叫「储能簇」也能导入
  const cross = plan.buildPlan((await excel.parseWorkbook(buf2ab(makeXlsx({ 属性: [['储能簇', '同名但不同类', '数值', '', '']] })))).result.rows, ctxFor(existing, 'skip'))
  assert.equal(cross.decisions[0].disposition, 'create')
})

await check('⑨ 随机碰撞重试与原始名占位；确定 ID/名称在计划内固定', async () => {
  const existing = plan.collectExistingNames({ ontology: { '@graph': [{ '@type': 'owl:Class', 'rdfs:label': '储能簇' }, { '@type': 'owl:Class', 'rdfs:label': '储能簇_100000' }] } })
  // 第一次随机命中已存在的「储能簇_500000」→ 碰撞重试
  let calls = 0
  const random = () => { calls++; return calls === 1 ? 0.0 : 0.7 } // 0.0 → 100000，0.7 → 730000
  const { result } = await excel.parseWorkbook(buf2ab(makeXlsx({ 对象: [['储能簇', '重复'], ['储能簇_730000', '占用名']] })))
  const outcome = plan.buildPlan(result.rows, ctxFor(existing, 'rename', random))
  const renamed = outcome.decisions[0]
  assert.equal(renamed.disposition, 'rename')
  assert.notEqual(renamed.finalName, '储能簇_100000', '碰撞后必须重试')
  assert.ok(renamed.finalName.startsWith('储能簇_'))
  // 名称与 ID 在计划里固定（两次读取同一 decision 对象一致）
  assert.equal(renamed.finalName, outcome.decisions[0].finalName)
})

await check('⑩ 名称 trim、大小写敏感；批内重复第一条占位', async () => {
  const existing = plan.collectExistingNames({ ontology: { '@graph': [{ '@type': 'owl:Class', 'rdfs:label': '储能簇' }] } })
  const { result } = await excel.parseWorkbook(buf2ab(makeXlsx({ 对象: [[' 储能簇 ', 'trim 命中'], ['储能簇', '批内重复'], ['storage', '小写不冲突'], ['STORAGE', '大写']] })))
  const outcome = plan.buildPlan(result.rows, ctxFor(existing, 'skip'))
  const by = outcome.decisions.map(d => [d.originalName, d.disposition])
  assert.deepEqual(by, [['储能簇', 'skip'], ['储能簇', 'skip'], ['storage', 'create'], ['STORAGE', 'create']])
})

await check('⑪ 跳过行的其他缺项不阻断；新行缺必填阻断并逐列定位；动作效果选填', async () => {
  const existing = plan.collectExistingNames({ workflow: { actions: [{ name: '储能簇' }] } })
  // 20260920 字段精简：动作只需名称+业务定义即可导入，预期效果为空不报错
  const { result } = await excel.parseWorkbook(buf2ab(makeXlsx({ 动作: [['储能簇', ''], ['新动作', '有定义无效果']] })))
  const outcome = plan.buildPlan(result.rows, ctxFor(existing, 'skip'))
  assert.equal(outcome.blocked, false, '动作预期效果选填：为空不得阻断')
  assert.equal(outcome.issues.length, 0, JSON.stringify(outcome.issues))
  const created = outcome.decisions.find(d => d.disposition === 'create')
  assert.ok(created && created.definition.effect === '' && created.definition.name === '新动作')
  assert.equal(outcome.decisions[0].disposition, 'skip', '跳过行不因业务定义缺失阻断')
  // 真正缺必填（业务定义）仍必须阻断并定位到列
  const { result: r2 } = await excel.parseWorkbook(buf2ab(makeXlsx({ 动作: [['缺定义动作', '']] })))
  const o2 = plan.buildPlan(r2.rows, ctxFor(plan.collectExistingNames({}), 'skip'))
  assert.equal(o2.blocked, true, '缺业务定义必须阻断')
  const issue = o2.issues.find(i => i.column === '业务定义')
  assert.ok(issue && issue.row === 2, '问题定位到真实行号：' + JSON.stringify(o2.issues))
})

await check('⑫ 隐藏列外的额外可见列/额外 Sheet 有数据 → 明确报错', async () => {
  const { result } = await excel.parseWorkbook(buf2ab(makeXlsx({ 对象: [['A', 'x', '多余列内容']] }, { extraSheet: true })))
  const msgs = result.issues.map(i => i.message).join('；')
  assert.ok(msgs.includes('模板以外内容'), msgs)
  assert.ok(msgs.includes('模板以外的工作表'), msgs)
})

await check('⑬ 业务公式/错误值单元格阻断；非文字类型指出需填写文字', async () => {
  const ws = { '!ref': 'A1:C2' }
  ws['A1'] = { t: 's', v: '动作名称' }; ws['B1'] = { t: 's', v: '业务定义' }; ws['C1'] = { t: 's', v: '业务效果' }
  ws['A2'] = { t: 's', v: '动作A' }; ws['B2'] = { t: 's', v: '定义' }; ws['C2'] = { t: 'n', f: '1+1', v: 2 }
  const wb = { SheetNames: ['对象', '属性', '规则', '动作'], Sheets: { 对象: XLSX.utils.aoa_to_sheet([['对象名称','业务定义']]), 属性: XLSX.utils.aoa_to_sheet([['属性名称']]), 规则: XLSX.utils.aoa_to_sheet([['规则名称']]), 动作: ws } }
  const bin = XLSX.write(wb, { type: 'buffer', bookType: 'xlsx' })
  const { result } = await excel.parseWorkbook(buf2ab(bin))
  const row = result.rows[0]
  assert.equal(row.cells['业务效果'].kind, 'formula')
  const outcome = plan.buildPlan(result.rows, ctxFor(plan.collectExistingNames({}), 'skip'))
  assert.equal(outcome.blocked, true)
  assert.ok(outcome.issues.some(i => i.message.includes('公式')))
  // 数值格式单元格
  const ws2 = { '!ref': 'A1:C2' }
  ws2['A1'] = { t: 's', v: '动作名称' }; ws2['B1'] = { t: 's', v: '业务定义' }; ws2['C1'] = { t: 's', v: '业务效果' }
  ws2['A2'] = { t: 'n', v: 42 }; ws2['B2'] = { t: 's', v: 'd' }; ws2['C2'] = { t: 's', v: 'e' }
  const wb2 = { SheetNames: ['对象','属性','规则','动作'], Sheets: { 对象: XLSX.utils.aoa_to_sheet([['a']]), 属性: XLSX.utils.aoa_to_sheet([['a']]), 规则: XLSX.utils.aoa_to_sheet([['a']]), 动作: ws2 } }
  const bin2 = XLSX.write(wb2, { type: 'buffer', bookType: 'xlsx' })
  const { result: r2 } = await excel.parseWorkbook(buf2ab(bin2))
  const outcome2 = plan.buildPlan(r2.rows, ctxFor(plan.collectExistingNames({}), 'skip'))
  assert.ok(outcome2.issues.some(i => i.message.includes('数值')))
})

await check('⑭ 文件级校验：非 xlsx 后缀、超大、坏魔数、非四表结构', async () => {
  assert.equal(excel.checkFileMeta('a.xls', 100).ok, false)
  assert.equal(excel.checkFileMeta('a.xlsx', excel.MAX_FILE_BYTES + 1).ok, false)
  assert.match((await excel.readWorkbook(new TextEncoder().encode('plain text').buffer)).error, /不是有效的/)
  const ole = new ArrayBuffer(8); new Uint8Array(ole).set([0xd0, 0xcf, 0x11, 0xe0, 0, 0, 0, 0])
  assert.match((await excel.readWorkbook(ole)).error, /加密或旧版/)
  // 缺 Sheet
  const wb = XLSX.utils.book_new()
  XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet([['对象名称', '业务定义']]), '对象')
  const { result } = await excel.parseWorkbook(buf2ab(XLSX.write(wb, { type: 'buffer', bookType: 'xlsx' })))
  assert.ok(result.issues.some(i => i.message.includes('缺少工作表')))
})

await check('⑮ 缺表头/重复表头报错；空表也要完整表头', async () => {
  const wb = XLSX.utils.book_new()
  XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet([['名称'], ['x']]), '对象') // 表头错误
  const wsProp = XLSX.utils.aoa_to_sheet([]) // 空属性表（无表头）
  XLSX.utils.book_append_sheet(wb, wsProp, '属性')
  XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet([['规则名称','业务定义','规则内容','输出结果']]), '规则')
  XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet([['动作名称','业务定义','业务效果']]), '动作')
  const { result } = await excel.parseWorkbook(buf2ab(XLSX.write(wb, { type: 'buffer', bookType: 'xlsx' })))
  const msgs = result.issues.map(i => i.message).join('；')
  assert.ok(msgs.includes('缺少表头「对象名称」'), msgs)
  assert.ok(msgs.includes('缺少表头「业务定义」'), msgs)
  // 重复表头
  const wb2 = XLSX.utils.book_new()
  XLSX.utils.book_append_sheet(wb2, XLSX.utils.aoa_to_sheet([['对象名称','对象名称','业务定义']]), '对象')
  XLSX.utils.book_append_sheet(wb2, XLSX.utils.aoa_to_sheet([['属性名称']]), '属性')
  XLSX.utils.book_append_sheet(wb2, XLSX.utils.aoa_to_sheet([['规则名称']]), '规则')
  XLSX.utils.book_append_sheet(wb2, XLSX.utils.aoa_to_sheet([['动作名称']]), '动作')
  const r2 = await excel.parseWorkbook(buf2ab(XLSX.write(wb2, { type: 'buffer', bookType: 'xlsx' })))
  assert.ok(r2.result.issues.some(i => i.message.includes('重复')))
})

await check('⑯ 业务行超 1000 条拒绝；applyPlan 合入后可被 verifyImported 核对', async () => {
  const big = Array.from({ length: 1001 }, (_, i) => ['对象' + i, '定义' + i])
  const { result, error } = await excel.parseWorkbook(buf2ab(makeXlsx({ 对象: big })))
  assert.match(error, /超过 1000 条/)
  // applyPlan + verify
  const state = { ontology: { '@graph': [] }, workflow: {} }
  const oneParsed = await excel.parseWorkbook(buf2ab(makeXlsx({ 对象: [['储能簇', 'x']], 属性: [['soc', 'y', '数值', '', '']], 规则: [['R1', 'd', 'c']], 动作: [['A1', 'd', 'e']] })))
  assert.ok(!oneParsed.error, oneParsed.error)
  const { result: one } = oneParsed
  const outcome = plan.buildPlan(one.rows, ctxFor(plan.collectExistingNames(state), 'skip'))
  void outcome
  plan.applyPlan(state, outcome.decisions)
  assert.equal(plan.verifyImported(state, outcome.decisions), 'all-present')
  // 拿掉属性 → partial
  state.ontology['@graph'] = state.ontology['@graph'].filter(n => n['@type'] !== 'mg:SharedProperty')
  assert.equal(plan.verifyImported(state, outcome.decisions), 'partial')
})

// ── ⑲～㉖ 20260920 字段精简：新表头导入、旧列别名兼容、选填列、缺定义定位、旧列严格校验 ──

await check('⑲ 新表头三列规则与新动作（预期效果）可导入，定义字段正确', async () => {
  const { result, error } = await excel.parseWorkbook(buf2ab(makeXlsx({
    规则: [['R新', '新规则定义', '规则内容文本']],
    动作: [['A新', '动作定义', '预期效果文本']],
  })))
  assert.ok(!error, error)
  const outcome = plan.buildPlan(result.rows, ctxFor(plan.collectExistingNames({}), 'skip'))
  assert.equal(outcome.blocked, false, JSON.stringify(outcome.issues))
  const rule = outcome.decisions.find(d => d.sheet === '规则').definition
  assert.equal(rule.name, 'R新')
  assert.equal(rule.description, '新规则定义')
  assert.equal(rule.content, '规则内容文本')
  assert.equal(rule.output, '', '新表头无输出结果列 → output 为空字符串，不制造历史值')
  const action = outcome.decisions.find(d => d.sheet === '动作').definition
  assert.equal(action.name, 'A新')
  assert.equal(action.description, '动作定义')
  assert.equal(action.effect, '预期效果文本')
})

await check('⑳ 旧四列规则可导入：输出结果保留为 output，预览说明含历史补充提示', async () => {
  const { result } = await excel.parseWorkbook(buf2ab(makeXlsx(
    { 规则: [['R旧', '旧规则定义', '旧内容', '旧输出文本']] },
    { headers: { 规则: LEGACY_HEADERS.规则 } },
  )))
  const outcome = plan.buildPlan(result.rows, ctxFor(plan.collectExistingNames({}), 'skip'))
  assert.equal(outcome.blocked, false, JSON.stringify(outcome.issues))
  const d = outcome.decisions[0]
  assert.equal(d.disposition, 'create')
  assert.equal(d.definition.output, '旧输出文本', '旧输出结果必须保留（零丢失）')
  assert.equal(d.definition.content, '旧内容')
  assert.equal(d.definition.description, '旧规则定义')
  assert.match(d.reason, /旧输出结果将保留为历史补充说明/, '预览/决策说明需给出历史补充提示')
  // 新表头规则不带提示（没有旧输出，不制造噪声）
  const { result: r2 } = await excel.parseWorkbook(buf2ab(makeXlsx({ 规则: [['R新列', '定义', '内容']] })))
  const o2 = plan.buildPlan(r2.rows, ctxFor(plan.collectExistingNames({}), 'skip'))
  assert.equal(o2.decisions[0].definition.output, '')
  assert.ok(!/历史补充/.test(o2.decisions[0].reason))
})

await check('㉑ 旧动作表头「业务效果」作为别名写入 effect', async () => {
  const { result } = await excel.parseWorkbook(buf2ab(makeXlsx(
    { 动作: [['A旧', '动作定义', '旧效果文本']] },
    { headers: { 动作: LEGACY_HEADERS.动作 } },
  )))
  const outcome = plan.buildPlan(result.rows, ctxFor(plan.collectExistingNames({}), 'skip'))
  assert.equal(outcome.blocked, false, JSON.stringify(outcome.issues))
  const d = outcome.decisions[0].definition
  assert.equal(d.name, 'A旧')
  assert.equal(d.description, '动作定义')
  assert.equal(d.effect, '旧效果文本')
})

await check('㉒ 新旧效果列并存：一侧空取另一侧、trim 相同取一份、都非空不同阻断整批', async () => {
  // 已知遗留（20260920 收口记录，不影响本用例结论）：两列并存时 scanSheets 会把「业务效果」计成
  // 「预期效果」的重复表头并给出一条结构提示，仅供主代理决策是否调整；导入结论按需求逐行合并处理。
  const BOTH = { 动作: ['动作名称', '业务定义', '预期效果', '业务效果'] }
  const plan1 = async rows => {
    const { result } = await excel.parseWorkbook(buf2ab(makeXlsx({ 动作: rows }, { headers: BOTH })))
    return plan.buildPlan(result.rows, ctxFor(plan.collectExistingNames({}), 'skip'))
  }
  // 新列空 → 取旧列
  const o1 = await plan1([['A1', '定义', '', '旧侧效果']])
  assert.equal(o1.blocked, false, JSON.stringify(o1.issues))
  assert.equal(o1.decisions[0].definition.effect, '旧侧效果')
  // 旧列空 → 取新列
  const o2 = await plan1([['A2', '定义', '新侧效果', '']])
  assert.equal(o2.blocked, false, JSON.stringify(o2.issues))
  assert.equal(o2.decisions[0].definition.effect, '新侧效果')
  // 去首尾空白后相同 → 取一份
  const o3 = await plan1([['A3', '定义', '  效果X  ', '效果X']])
  assert.equal(o3.blocked, false, JSON.stringify(o3.issues))
  assert.equal(o3.decisions[0].definition.effect, '效果X')
  // 都非空且不同 → 阻断该批，错误定位到冲突行，不擅自择一
  const o4 = await plan1([['A4', '定义', '新效果', '旧效果'], ['A5', '定义', '同效', '同效']])
  assert.equal(o4.blocked, true, '冲突必须阻断整批')
  const err = o4.decisions.find(d => d.disposition === 'error')
  assert.ok(err && err.row === 2 && err.definition === undefined, '冲突行定位到第 2 行且不生成定义')
  const issue = o4.issues.find(i => i.column === '预期效果')
  assert.ok(issue && issue.row === 2 && issue.message.includes('业务效果'), JSON.stringify(o4.issues))
})

await check('㉓ 选填列缺失可导入：规则/动作仅名称+业务定义两列', async () => {
  const { result } = await excel.parseWorkbook(buf2ab(makeXlsx({
    规则: [['R瘦', '定义']],
    动作: [['A瘦', '定义']],
  }, { headers: { 规则: ['规则名称', '业务定义'], 动作: ['动作名称', '业务定义'] } })))
  assert.equal(result.issues.length, 0, JSON.stringify(result.issues))
  const outcome = plan.buildPlan(result.rows, ctxFor(plan.collectExistingNames({}), 'skip'))
  assert.equal(outcome.blocked, false, JSON.stringify(outcome.issues))
  assert.equal(plan.planCounts(outcome.decisions).importable, 2)
  const rule = outcome.decisions.find(d => d.sheet === '规则').definition
  assert.equal(rule.content, '')
  assert.equal(rule.output, '')
  assert.equal(outcome.decisions.find(d => d.sheet === '动作').definition.effect, '')
})

await check('㉔ 待导入规则/动作缺业务定义：定位 Sheet/行/列并提示请填写业务定义', async () => {
  const { result } = await excel.parseWorkbook(buf2ab(makeXlsx({ 规则: [['R缺', '']], 动作: [['A缺', ' ']] })))
  const outcome = plan.buildPlan(result.rows, ctxFor(plan.collectExistingNames({}), 'skip'))
  assert.equal(outcome.blocked, true)
  const ri = outcome.issues.find(i => i.sheet === '规则' && i.column === '业务定义')
  assert.ok(ri && ri.row === 2 && /请填写业务定义/.test(ri.message), JSON.stringify(outcome.issues))
  const ai = outcome.issues.find(i => i.sheet === '动作' && i.column === '业务定义')
  assert.ok(ai && ai.row === 2 && /请填写业务定义/.test(ai.message), JSON.stringify(outcome.issues))
  assert.equal(plan.planCounts(outcome.decisions).importable, 0, '缺必填不得部分写入')
})

await check('㉕ 旧别名列/旧输出列的公式与错误值同样阻断', async () => {
  // 旧规则「输出结果」列公式（D2）
  const formulaRule = makeXlsx({ 规则: [['R式', '定义', '内容', '占位']] },
    { headers: { 规则: LEGACY_HEADERS.规则 }, patch: wb => { wb.Sheets['规则'].D2 = { t: 'n', f: '1+1', v: 2 } } })
  const { result: r1 } = await excel.parseWorkbook(buf2ab(formulaRule))
  assert.equal(r1.rows[0].cells['输出结果'] ? r1.rows[0].cells['输出结果'].kind : r1.rows[0].cells['D'].kind, 'formula')
  const o1 = plan.buildPlan(r1.rows, ctxFor(plan.collectExistingNames({}), 'skip'))
  assert.equal(o1.blocked, true, '旧输出列公式必须阻断，不得使用公式缓存值')
  assert.ok(o1.issues.some(i => i.column === '输出结果' && /公式/.test(i.message)), JSON.stringify(o1.issues))
  // 旧动作「业务效果」列错误值（C2 = #REF!）
  const errAction = makeXlsx({ 动作: [['A错', '定义', '占位']] },
    { headers: { 动作: LEGACY_HEADERS.动作 }, patch: wb => { wb.Sheets['动作'].C2 = { t: 'e', v: 0x17, w: '#REF!' } } })
  const { result: r2 } = await excel.parseWorkbook(buf2ab(errAction))
  const o2 = plan.buildPlan(r2.rows, ctxFor(plan.collectExistingNames({}), 'skip'))
  assert.equal(o2.blocked, true, '旧别名列错误值必须阻断')
  assert.ok(o2.issues.some(i => i.column === '业务效果' && /错误值/.test(i.message)), JSON.stringify(o2.issues))
  // 旧动作「业务效果」列公式同样阻断
  const formulaAction = makeXlsx({ 动作: [['A式', '定义', '占位']] },
    { headers: { 动作: LEGACY_HEADERS.动作 }, patch: wb => { wb.Sheets['动作'].C2 = { t: 'n', f: '1+1', v: 2 } } })
  const { result: r3 } = await excel.parseWorkbook(buf2ab(formulaAction))
  const o3 = plan.buildPlan(r3.rows, ctxFor(plan.collectExistingNames({}), 'skip'))
  assert.equal(o3.blocked, true)
  assert.ok(o3.issues.some(i => i.column === '业务效果' && /公式/.test(i.message)), JSON.stringify(o3.issues))
  assert.equal(plan.planCounts(o3.decisions).importable, 0)
})

await check('㉗ 无缓存值的公式单元格同样阻断（openpyxl 等工具写出的 t=z + f）', async () => {
  // Excel 会写缓存值（㉕ 覆盖）；openpyxl 写出的公式没有缓存值，SheetJS 读作 t='z' + f。
  // 若按「纯格式空单元格」跳过，公式校验永远不会触发，用户的公式内容被静默丢弃。
  const noCache = makeXlsx({ 规则: [['R式', '定义', '内容', '占位']] },
    { headers: { 规则: LEGACY_HEADERS.规则 }, patch: wb => { wb.Sheets['规则'].D2 = { t: 'z', f: '1+1', v: 0 } } })
  const { result: r1 } = await excel.parseWorkbook(buf2ab(noCache))
  const cell = r1.rows[0].cells['输出结果'] || r1.rows[0].cells['D']
  assert.ok(cell, '无缓存值的公式单元格不得被当作空单元格整格丢弃')
  assert.equal(cell.kind, 'formula')
  const o1 = plan.buildPlan(r1.rows, ctxFor(plan.collectExistingNames({}), 'skip'))
  assert.equal(o1.blocked, true, '无缓存值公式必须阻断该批')
  assert.ok(o1.issues.some(i => /公式/.test(i.message)), JSON.stringify(o1.issues))
  assert.equal(plan.planCounts(o1.decisions).importable, 0, '不得部分写入')
  // 规范列（业务定义）同样处理，不是别名列特例
  const noCacheDef = makeXlsx({ 规则: [['R式2', '定义', '内容']] },
    { patch: wb => { wb.Sheets['规则'].B2 = { t: 'z', f: 'CONCAT("a","b")', v: 0 } } })
  const { result: r2 } = await excel.parseWorkbook(buf2ab(noCacheDef))
  const o2 = plan.buildPlan(r2.rows, ctxFor(plan.collectExistingNames({}), 'skip'))
  assert.equal(o2.blocked, true)
  assert.ok(o2.issues.some(i => i.column === '业务定义' && /公式/.test(i.message)), JSON.stringify(o2.issues))
  // 额外 Sheet 内只有无缓存值公式（无其他内容）时，同样不能当作空表忽略
  const extraFormula = makeXlsx({}, { patch: wb => {
    const ws = { '!ref': 'B2:B2', B2: { t: 'z', f: '1+1', v: 0 } }
    XLSX.utils.book_append_sheet(wb, ws, '公式表')
  } })
  const { result: r3 } = await excel.parseWorkbook(buf2ab(extraFormula))
  assert.ok(r3.issues.some(i => /模板以外的工作表/.test(i.message)), JSON.stringify(r3.issues))
})

await check('㉖ 旧下载模板（ontology-import-v1.xlsx）仍可解析：别名兼容、零业务行、无结构问题', async () => {
  const buf = fs.readFileSync(LEGACY_TEMPLATE)
  const { result, error } = await excel.parseWorkbook(buf2ab(buf))
  assert.ok(!error, error)
  assert.equal(result.rows.length, 0, '空模板不得产生业务行')
  assert.equal(result.issues.length, 0, JSON.stringify(result.issues))
})

// ── ⑰ 旧页面兜底（20260918）：动态分块加载失败要有可读指引，不能抛裸错误 ──
await check('⑰ 分块加载失败转可读文案（重新构建后旧页面场景）', async () => {
  const mod = await import(pathToFileURL(path.join(root, 'frontend/src/ontology/excelImport.ts')).href)
  assert.equal(typeof mod.CHUNK_LOAD_HINT, 'string')
  assert.match(mod.CHUNK_LOAD_HINT, /刷新页面/)
  const err = mod.chunkLoadFailure(new TypeError('Failed to fetch dynamically imported module'))
  assert.match(err.message, /刷新页面/)
  assert.match(err.message, /Failed to fetch dynamically imported module/, '保留原始信息便于排查')
  assert.match(mod.chunkLoadFailure('').message, /刷新页面/, '无原始信息也要给出指引')
})

// ── ⑱ 入口兜底：main.ts 注册了旧页面错误捕获与横幅 ──
await check('⑱ 入口注册旧页面错误兜底（横幅 + 刷新按钮）', async () => {
  const src = readFileSync(path.join(root, 'frontend/src/main.ts'), 'utf8')
  assert.match(src, /stale-page-banner/, '需有横幅元素')
  assert.match(src, /before initialization/, '需识别 TDZ 类错误文案')
  assert.match(src, /dynamically imported module/, '需识别分块加载失败')
  assert.match(src, /location\.reload\(\)/, '需提供刷新动作')
  assert.match(src, /addEventListener\('unhandledrejection'/, '需覆盖未捕获的 promise 失败')
})

const failed = results.filter(r => !r.ok)
console.log(`\n${results.length - failed.length}/${results.length} 项通过`)
if (failed.length) process.exit(1)
