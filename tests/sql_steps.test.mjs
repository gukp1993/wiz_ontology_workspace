// V4 sqlSteps：转换与校验（只用内存数据，不执行 SQL）。
import assert from 'node:assert/strict'
import {convertToSqlSteps, sqlStepsRuleErrors, stepOutputCandidates, newStepKey, blankSqlStepsRule} from '../frontend/src/project/sqlSteps.ts'
import {reusableScadaRule, socRule} from '../frontend/src/project/queryRules.ts'
import {toSqlTemplate} from '../frontend/src/project/sqlTemplates.ts'

// 1) V3 结构化三步 → 卡片：SQL 含动态表字段引用与参数，校验通过，入参未变
const src = reusableScadaRule(); src.connection = 'db'
const before = JSON.stringify(src)
const conv = convertToSqlSteps(src)
assert.ok(conv.rule, '结构化规则应转换成功：' + conv.reason)
assert.equal(conv.rule.steps.length, 3)
assert.match(conv.rule.steps[0].sql, /SELECT scada_table AS scadaTable/)
assert.match(conv.rule.steps[1].sql, /FROM \{\{point.scadaTable\}\}/)
assert.match(conv.rule.steps[1].sql, /WHERE id = :point.scadaId/)
assert.match(conv.rule.steps[2].sql, /\{\{storage.sampleField\}\} AS value/)
assert.match(conv.rule.steps[2].sql, /ORDER BY \{\{storage.sampleTable\}\}/.source ? /ORDER BY/ : /ORDER BY/)
assert.deepEqual(sqlStepsRuleErrors(conv.rule), [])
assert.equal(JSON.stringify(src), before, '转换不得修改入参')
assert.ok(conv.rule.extensions.structuredRuleBeforeSql.steps[0].where[0].value === '{{inputs.model_name}}', '原始结构化步骤留档')
assert.equal(conv.rule.id, src.id, '规则 ID 保留')

// 2) V3 sqlTemplate → 卡片：原文留档，切段还原三步
const tpl = JSON.parse(JSON.stringify(conv.rule)); delete tpl.steps; delete tpl.extensions
tpl.schemaVersion = 3; tpl.mode = 'sqlTemplate'; tpl.sqlTemplate = toSqlTemplate(reusableScadaRule())
tpl.connection = 'db'
const conv2 = convertToSqlSteps(tpl)
assert.ok(conv2.rule, '模板应转换成功：' + conv2.reason)
assert.equal(conv2.rule.steps.length, 3)
assert.equal(conv2.rule.steps[1].key, 'storage')
assert.equal(conv2.rule.steps[0].name, '定位测点', '标记行后的中文注释成为步骤名')
assert.deepEqual(sqlStepsRuleErrors(conv2.rule), [])
assert.ok(conv2.rule.steps.every(st=>!/=@step|@step\s/.test(st.sql)),'卡片 SQL 不得包含 @step 标记行')
assert.equal(conv2.rule.extensions.sqlTemplateBeforeSteps, tpl.sqlTemplate, '原文完整留档')

// 3) 字符串/注释内的伪标记与伪引用不干扰切段与校验（A14）
const tricky = JSON.parse(JSON.stringify(tpl))
tricky.sqlTemplate = "-- 头注释 :fake\n" + tpl.sqlTemplate.replace('AND attr_name = :attr_name', "AND note = '伪 :fake -- @step ghost one' AND attr_name = :attr_name")
const conv3 = convertToSqlSteps(tricky)
assert.ok(conv3.rule, '字符串内伪标记不得切段：' + conv3.reason)
assert.equal(conv3.rule.steps.length, 3)
assert.deepEqual(sqlStepsRuleErrors(conv3.rule), [])

// 4) 转换失败：重复段名 → 保留原文并给出原因
const broken = JSON.parse(JSON.stringify(tpl))
broken.sqlTemplate = broken.sqlTemplate.replace('-- @step storage one', '-- @step point one')
const fail = convertToSqlSteps(broken)
assert.equal(fail.rule, null)
assert.match(fail.reason, /重复/)

// 5) 校验错误族
const bad = () => JSON.parse(JSON.stringify(conv.rule))
let r = bad(); r.steps[1].sql = 'SELECT x FROM {{samples.sample_table}}'
assert.match(sqlStepsRuleErrors(r).join(), /动态标识 samples.sample_table 须引用前置唯一记录步骤的输出/)
r = bad(); r.steps[1].sql = 'SELECT x FROM {{storage.missing}} WHERE id = :ghost.step'
assert.match(sqlStepsRuleErrors(r).join(), /ghost.step.*不存在、返回多条或尚未执行/)
r = bad(); r.steps[1].sql = 'SELECT x FROM t WHERE id = :samples.value'
assert.match(sqlStepsRuleErrors(r).join(), /samples.value/)
r = bad(); r.result = {step: 'samples', type: 'scalar', valueType: 'double', value: 'value'}
assert.match(sqlStepsRuleErrors(r).join(), /单值返回/)
r = bad(); r.steps[2].key = 'storage'
assert.match(sqlStepsRuleErrors(r).join(), /步骤技术名无效或重复/)
r = bad(); r.steps[0].sql = 'WITH a AS (SELECT 1) SELECT * FROM a'
assert.match(sqlStepsRuleErrors(r).join(), /WITH/)
r = bad(); r.steps[0].sql = 'SELECT 1 AS a; SELECT 2 AS b'
assert.match(sqlStepsRuleErrors(r).join(), /一条 SELECT/)
r = bad(); r.steps[0].sql = "SELECT 'a:b;:ghost' AS note, scada_table FROM s WHERE note = 'x:yy' AND model_id = :model_id"
assert.deepEqual(sqlStepsRuleErrors(r), [], '字符串内伪引用不误判')
r = bad(); r.result.step = 'ghost'
assert.match(sqlStepsRuleErrors(r).join(), /返回步骤不存在/)
r = bad(); r.steps.push({id: 'extra', key: 'extra', name: '多余', cardinality: 'one', sql: 'SELECT 1 AS v'}); r.result.step = 'samples'
assert.doesNotMatch(sqlStepsRuleErrors(r).join(), /不参与返回/, '输出步骤非最后仅为UI提示，不阻断')
r = bad(); r.steps[0].sql = 'SELECT scada_table FROM s WHERE x = :missing'
assert.match(sqlStepsRuleErrors(r).join(), /未声明的输入参数 :missing/)

// 6) 输出列候选与工具函数
assert.deepEqual(stepOutputCandidates('SELECT record_time AS timestamp, {{storage.sample_field_name}} AS value FROM t'), ['timestamp', 'value'])
assert.deepEqual(stepOutputCandidates('SELECT a+b, count(*) FROM t'), [])
assert.deepEqual(stepOutputCandidates('SELECT id, name FROM t'), ['id', 'name'])
assert.equal(newStepKey([{key: 'step'}, {key: 'step2'}]), 'step3')
const blank = blankSqlStepsRule()
assert.equal(blank.steps.length, 1)
assert.match(sqlStepsRuleErrors(blank).join(), /请填写规则名称/)
assert.match(sqlStepsRuleErrors(blank).join(), /SQL 未填写/)

// 7) 步骤唯一性与记录数（审阅缺陷3）：many 重复 key、重复/空 ID、非法 cardinality 必须拒绝；UUID 合法
let u = bad()
u.steps.push({id: 's5', key: 'samples', name: '再采样', cardinality: 'many', sql: 'SELECT 2 AS v FROM t'})
assert.match(sqlStepsRuleErrors(u).join(), /步骤「再采样」：步骤技术名无效或重复/, '两个 many 步骤同 key 必须检出')
u = bad(); u.steps[1].id = u.steps[0].id
assert.match(sqlStepsRuleErrors(u).join(), /步骤「定位采样存储」：步骤标识重复/, '不同 key 相同 ID 必须检出')
u = bad(); u.steps[1].id = ''
assert.match(sqlStepsRuleErrors(u).join(), /步骤「定位采样存储」：步骤标识缺失/, '空 ID 必须检出')
u = bad(); u.steps[1].cardinality = 'single'
assert.match(sqlStepsRuleErrors(u).join(), /步骤「定位采样存储」：预期记录数无效/, 'cardinality 只能为 one/many')
u = bad(); u.steps[1].id = crypto.randomUUID()
assert.deepEqual(sqlStepsRuleErrors(u), [], '系统生成的 UUID 步骤 ID 不按 SQL 标识符规则校验')

// 8) 模板首段前置内容（审阅缺陷4）：词法识别首个真实标记，注释/字符串内伪标记不参与
const lead = JSON.parse(JSON.stringify(tpl))
lead.sqlTemplate = '/* -- @step ignored */\nSELECT 42;\n' + tpl.sqlTemplate
const convLead = convertToSqlSteps(lead)
assert.equal(convLead.rule, null, '块注释伪标记 + 前置 SQL 不得静默丢弃 SELECT 42')
assert.match(convLead.reason, /首段之前存在 SQL 内容/)
const leadStr = JSON.parse(JSON.stringify(tpl))
leadStr.sqlTemplate = "SELECT 0 FROM dual WHERE x = '-- @step fake one';\n" + tpl.sqlTemplate
const convStr = convertToSqlSteps(leadStr)
assert.equal(convStr.rule, null, '字符串内伪标记前的真实 SQL 不得丢弃')
assert.match(convStr.reason, /首段之前存在 SQL 内容/)
const leadNote = JSON.parse(JSON.stringify(tpl))
leadNote.sqlTemplate = '/* -- @step ignored */\n-- 头部说明：分段语法见下\n' + tpl.sqlTemplate
const convNote = convertToSqlSteps(leadNote)
assert.ok(convNote.rule, '前置只有注释/空白时正常转换：' + convNote.reason)
assert.equal(convNote.rule.steps.length, 3)
assert.deepEqual(sqlStepsRuleErrors(convNote.rule), [])

console.log('V4 sqlSteps：结构化/模板无损转换、失败回退原因、引用·顺序·数量关系·返回形态校验、字符串注释噪声免疫、步骤唯一性、首段前置判定、输出候选识别全部通过')
