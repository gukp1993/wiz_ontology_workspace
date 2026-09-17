import assert from 'node:assert/strict'
import {toSqlTemplate,sqlTemplateErrors} from '../frontend/src/project/sqlTemplates.ts'
import {editableRule,reusableScadaRule,queryRuleErrors} from '../frontend/src/project/queryRules.ts'
const r=editableRule(reusableScadaRule());r.connection='db';r.mode='sqlTemplate';r.sqlTemplate=toSqlTemplate(r)
assert.match(r.sqlTemplate,/FROM \{\{point.scadaTable\}\}/)
assert.match(r.sqlTemplate,/WHERE id = :point.scadaId/)
assert.deepEqual(queryRuleErrors(r),[])
const original=r.sqlTemplate
r.sqlTemplate=r.sqlTemplate.replace(':model_id',':missing');assert.match(sqlTemplateErrors(r).join(),/missing/)
r.sqlTemplate=original.replace('{{point.scadaTable}}','{{samples.value}}');assert.match(sqlTemplateErrors(r).join(),/动态标识/)
r.sqlTemplate=original.replace('SELECT scada_table','DELETE scada_table');assert.match(sqlTemplateErrors(r).join(),/SELECT/)
r.sqlTemplate=original;delete r.steps
assert.equal(toSqlTemplate(editableRule(r)),original)
assert.deepEqual(queryRuleErrors(r),[])
r.sqlTemplate=original.replace("-- @step storage one","-- @step point one");assert.match(sqlTemplateErrors(r).join(),/重复/)
console.log('SQL 模板：旧三步转换、参数、前向引用、查询段限制、保存后重开通过')
