// 属性内 SQL 取值：参数扫描、校验镜像、提交裁剪（只用内存数据，不执行 SQL）。
// node --import ./tests/ts_hooks.mjs tests/inline_sql.test.mjs
import assert from 'node:assert/strict'
import {scanSqlParams, inlineSqlErrors, effectiveParams, blankInlineSql} from '../frontend/src/project/inlineSql.ts'

const CTX = {connections: [{id: 'db', engine: 'mysql'}, {id: 'r', engine: 'redis'}], parameters: {park_id: 'P001', tz: 'Asia/Shanghai'}, identityReady: true}

// 1) 参数扫描：字符串（含 \' 与 \\" 转义）、反引号、--／#／块注释内的 :fake 不算；重复去重保序
assert.deepEqual(scanSqlParams("SELECT :b, ':fake', :a FROM t WHERE c = :b AND d = 'it\\'s :x'"), ['b', 'a'])
assert.deepEqual(scanSqlParams('SELECT :v FROM `we:ird` WHERE s = "double \\" :fake"'), ['v'])
assert.deepEqual(scanSqlParams("-- :head\nSELECT :v /* :block */ FROM t # :hash\n--notacomment: ok"), ['v'])
assert.deepEqual(scanSqlParams('SELECT 1'), [])
assert.deepEqual(scanSqlParams("SELECT 'a:b' -- trailing"), [])
// :: Postgres 风格转换与字母前的冒号不当参数
assert.deepEqual(scanSqlParams('SELECT x::text, a - -1 FROM t'), [])

// 2) 校验镜像
const good = {connection: 'db', sql: 'SELECT SUM(p) AS value FROM t WHERE park = :park_id', params: {park_id: {from: 'projectParameter', key: 'park_id'}}}
assert.deepEqual(inlineSqlErrors(good, CTX), [])
let e = inlineSqlErrors({...good, connection: ''}, CTX)
assert.match(e.join(), /数据连接不存在/)
e = inlineSqlErrors({...good, connection: 'r'}, CTX)
assert.match(e.join(), /MySQL/)
e = inlineSqlErrors({...good, sql: '  '}, CTX)
assert.match(e.join(), /未填写 SQL 模板/)
e = inlineSqlErrors({...good, sql: 'WITH a AS (SELECT 1) SELECT * FROM a'}, CTX)
assert.match(e.join(), /WITH.*引用已有规则/)
e = inlineSqlErrors({...good, sql: 'DELETE FROM t'}, CTX)
assert.match(e.join(), /须以 SELECT 开始/)
e = inlineSqlErrors({...good, sql: 'SELECT 1; SELECT 2'}, CTX)
assert.match(e.join(), /只能填写一条 SELECT/)
e = inlineSqlErrors({...good, sql: 'SELECT x FROM {{steps.a.b}}'}, CTX)
assert.match(e.join(), /动态表名／字段名与步骤引用.*引用已有规则/)
e = inlineSqlErrors({...good, sql: 'SELECT x FROM `{{a.b}}` WHERE y = :p'}, CTX)
assert.match(e.join(), /参数 :p 未绑定取值/)
e = inlineSqlErrors({...good, params: {park_id: {from: 'magic'}}}, CTX)
assert.match(e.join(), /绑定来源无效/)
e = inlineSqlErrors({...good, params: {park_id: {from: 'projectParameter', key: 'ghost'}}}, CTX)
assert.match(e.join(), /park_id.*不存在或已失效/)
e = inlineSqlErrors({...good, params: {park_id: {from: 'instanceId'}}}, CTX)
assert.deepEqual(e, [], '实例编号在身份就绪时可用')
e = inlineSqlErrors({...good, params: {park_id: {from: 'instanceId'}}}, {...CTX, identityReady: false})
assert.match(e.join(), /实例身份.*实例编号/)
// 常量：0/false/明确空字符串有效；未填值、类型非法、数值列填非数无效
e = inlineSqlErrors({...good, params: {park_id: {from: 'constant', dataType: 'string', value: ''}}}, CTX)
assert.deepEqual(e, [], '明确空字符串是有效常量')
e = inlineSqlErrors({...good, params: {park_id: {from: 'constant', dataType: 'double', value: '0'}}}, CTX)
assert.deepEqual(e, [])
e = inlineSqlErrors({...good, params: {park_id: {from: 'constant', dataType: 'boolean', value: 'false'}}}, CTX)
assert.deepEqual(e, [])
e = inlineSqlErrors({...good, params: {park_id: {from: 'constant', dataType: 'double', value: ''}}}, CTX)
assert.match(e.join(), /常量值无效/)
e = inlineSqlErrors({...good, params: {park_id: {from: 'constant', dataType: 'double', value: undefined}}}, CTX)
assert.match(e.join(), /常量值无效/)
e = inlineSqlErrors({...good, params: {park_id: {from: 'constant', dataType: 'color', value: 'x'}}}, CTX)
assert.match(e.join(), /常量数据类型无效/)

// 3) 提交裁剪：只保留 SQL 中仍存在的参数；0/false/空串绑定原样保留
const scanned = effectiveParams(
  {park_id: {from: 'projectParameter', key: 'park_id'}, gone: {from: 'constant', dataType: 'string', value: 'x'}},
  'SELECT :park_id FROM t')
assert.deepEqual(scanned, {park_id: {from: 'projectParameter', key: 'park_id'}})
const zero = effectiveParams({flag: {from: 'constant', dataType: 'boolean', value: 'false'}}, 'SELECT :flag FROM t')
assert.deepEqual(zero, {flag: {from: 'constant', dataType: 'boolean', value: 'false'}})
assert.deepEqual(blankInlineSql(), {connection: '', sql: '', params: {}})

console.log('属性内 SQL：参数扫描（字符串/反引号/注释免疫、去重保序、:: 不误判）、校验镜像、常量 0/false/空串有效、提交裁剪全部通过')
