// 显示格式优化（任务 C）：frontend/src/ontology/formattingOptions.ts 纯函数契约测试。
// 依据需求第 3 节样式集合与冻结导出契约：
//   styleOptions(kind)→{value,label}[]（仅新样式）、legacyStyleLabel(kind,style)→string|null、
//   effectiveConfig(local)→object|null（natural 空规则→null）、defaultStyle(kind)→string、
//   sampleDefault(kind)→string
// 运行：node --import ./tests/ts_hooks.mjs tests/formatting_options.test.mjs
import assert from 'node:assert/strict'
import * as fo from '../frontend/src/ontology/formattingOptions.ts'

const KINDS=['string','number','boolean','date','time','array','struct','timeSeries']
const NEW_STYLES={
  string:['standard','template','mapping'],
  number:['standard','percent','currency','scientific'],
  boolean:['default','custom'],
  date:['date','custom'],
  time:['date','datetime','time','relative','custom'],
  array:['list','join'],
  struct:['pairs','template'],
  timeSeries:['series'],
}
let passed=0
function ok(name){passed++;console.log('通过  '+name)}

// --- styleOptions：恰好为需求第 3 节新样式集合（A1 侧面：无新建代码入口的旧样式不再出现在新配置下拉）---
for(const kind of KINDS){
  const options=fo.styleOptions(kind)
  assert.ok(Array.isArray(options)&&options.length>0,`styleOptions(${kind}) 应返回非空数组，实际 ${JSON.stringify(options)}`)
  assert.deepEqual(options.map(o=>o.value),NEW_STYLES[kind],`styleOptions(${kind}) 样式集合不符。实际 ${JSON.stringify(options.map(o=>o.value))}，期望 ${JSON.stringify(NEW_STYLES[kind])}`)
  for(const o of options){
    assert.equal(typeof o.label,'string',`styleOptions(${kind}) 中 ${o.value} 缺少文本 label`)
    assert.ok(o.label.trim().length>0,`styleOptions(${kind}) 中 ${o.value} 的 label 为空`)
  }
  assert.equal(new Set(options.map(o=>o.value)).size,options.length,`styleOptions(${kind}) 样式值重复`)
}
ok('styleOptions：8 个 kind 的样式集合恰为第 3 节新样式（值唯一、label 非空）')

// 未知 kind 不崩溃（返回空数组或抛可读错误均可接受的是空数组约定）
assert.deepEqual(fo.styleOptions('unknown-kind')??[],[],"styleOptions('unknown-kind') 应返回空数组（未知类型无新样式）")
ok('styleOptions：未知 kind 返回空数组不崩溃')

// --- legacyStyleLabel：历史样式只读摘要映射（需求第 7 节）--------------------------------
// 集成裁决：实际签名为 (kind, config对象)——number 历史需读 config.notation；空摘要表示非历史。
assert.equal(fo.legacyStyleLabel('string',{style:'upper'}),'转大写',"legacyStyleLabel('string',{style:'upper'}) 应为 '转大写'")
const legacyExpect=[
  ['string',{style:'lower'},'小写'],
  ['string',{style:'trim'},'空白'],
  ['string',{style:'mask'},'脱敏'],
  ['string',{style:'truncate'},'截断'],
  ['array',{style:'count'},'数量'],
  ['array',{style:'json'},'JSON'],
  ['struct',{style:'json'},'JSON'],
  ['struct',{style:'compact'},'紧凑'],
  ['time',{style:'iso'},'ISO'],
  ['time',{style:'short'},'年月日'],
  ['number',{style:'standard',notation:'engineering'},'工程计数'],
]
for(const [kind,cfg,fragment] of legacyExpect){
  const label=fo.legacyStyleLabel(kind,cfg)
  assert.equal(typeof label,'string',`legacyStyleLabel(${kind},${JSON.stringify(cfg)}) 应返回文本摘要，实际 ${JSON.stringify(label)}`)
  assert.ok(label.includes(fragment),`legacyStyleLabel(${kind},${JSON.stringify(cfg)})=${JSON.stringify(label)} 应包含「${fragment}」`)
}
// 仍是新样式的 style 与未知 style 均返回空摘要（不误标为历史）
for(const [kind,cfg] of [['number',{style:'percent'}],['string',{style:'template'}],['struct',{style:'pairs'}],['array',{style:'join'}],['time',{style:'relative'}],['number',{style:'standard'}]]){
  assert.equal(fo.legacyStyleLabel(kind,cfg),'',`legacyStyleLabel(${kind},${JSON.stringify(cfg)}) 对非历史样式应返回空摘要，实际 ${JSON.stringify(fo.legacyStyleLabel(kind,cfg))}`)
}
// 未知样式回退显示原样式名（组件以「历史样式：xxx」只读摘要呈现，合理保留）
assert.equal(fo.legacyStyleLabel('string',{style:'bogus'}),'bogus','未知样式应回退显示原样式名')
ok('legacyStyleLabel：旧样式返回中文摘要，新样式/未知样式返回空摘要')

// --- effectiveConfig：null 语义（natural 空规则＝未配置；历史 code 仍有效）----------------
assert.equal(fo.effectiveConfig(undefined),null,'effectiveConfig(undefined) 应为 null')
assert.equal(fo.effectiveConfig(null),null,'effectiveConfig(null) 应为 null')
assert.equal(fo.effectiveConfig({}),null,'effectiveConfig({}) 应为 null')
assert.equal(fo.effectiveConfig({mode:'natural'}),null,'空规则的自然语言应视为未配置（null）')
assert.equal(fo.effectiveConfig({mode:'natural',instruction:''}),null,'空字符串规则应为 null')
assert.equal(fo.effectiveConfig({mode:'natural',instruction:'   '}),null,'纯空白规则应为 null')
const natural=fo.effectiveConfig({mode:'natural',instruction:'保留一位小数并加百分号'})
assert.ok(natural&&typeof natural==='object','填写规则的自然语言配置应返回对象，实际 '+JSON.stringify(natural))
const builtin=fo.effectiveConfig({mode:'builtin',style:'percent',percentInput:'hundred',decimals:1})
assert.ok(builtin&&typeof builtin==='object','builtin 配置应返回对象，实际 '+JSON.stringify(builtin))
const code=fo.effectiveConfig({mode:'code',code:'return String(value).toUpperCase()'})
assert.ok(code&&typeof code==='object','历史 code 配置仍有效（非 null），实际 '+JSON.stringify(code))
ok('effectiveConfig：空/空规则 natural→null；有规则 natural、builtin、历史 code 均→对象')

// --- defaultStyle：每个 kind 的默认样式必须来自该类型的新样式集合 -------------------------
for(const kind of KINDS){
  const style=fo.defaultStyle(kind)
  assert.equal(typeof style,'string',`defaultStyle(${kind}) 应返回文本`)
  assert.ok(NEW_STYLES[kind].includes(style),`defaultStyle(${kind})=${JSON.stringify(style)} 必须属于新样式集合 ${JSON.stringify(NEW_STYLES[kind])}`)
}
assert.equal(fo.defaultStyle('string'),'standard',"defaultStyle('string') 应为 'standard'")
assert.equal(fo.defaultStyle('number'),'standard',"defaultStyle('number') 应为 'standard'")
assert.equal(fo.defaultStyle('boolean'),'default',"defaultStyle('boolean') 应为 'default'")
assert.equal(fo.defaultStyle('timeSeries'),'series',"defaultStyle('timeSeries') 应为 'series'（唯一样式）")
ok('defaultStyle：默认样式均在新样式集合内（string/number=standard、boolean=default、timeSeries=series）')

// --- sampleDefault：默认样本形态（timeSeries 用规范样本；date 纯日期；time 带时区）---------
for(const kind of KINDS){
  const sample=fo.sampleDefault(kind)
  assert.equal(typeof sample,'string',`sampleDefault(${kind}) 应返回文本`)
  assert.ok(sample.length>0,`sampleDefault(${kind}) 不应为空`)
}
assert.ok(Number.isFinite(Number(fo.sampleDefault('number'))),`number 样本应为可解析数值，实际 ${JSON.stringify(fo.sampleDefault('number'))}`)
assert.equal(JSON.parse(fo.sampleDefault('boolean')),true,`boolean 样本应可解析为布尔，实际 ${JSON.stringify(fo.sampleDefault('boolean'))}`)
assert.ok(Array.isArray(JSON.parse(fo.sampleDefault('array'))),`array 样本应为 JSON 数组，实际 ${JSON.stringify(fo.sampleDefault('array'))}`)
assert.equal(typeof JSON.parse(fo.sampleDefault('struct')),'object',`struct 样本应为 JSON 对象，实际 ${JSON.stringify(fo.sampleDefault('struct'))}`)
assert.match(fo.sampleDefault('date'),/^\d{4}-\d{2}-\d{2}$/,'date 样本应为纯日期（无时分秒），实际 '+JSON.stringify(fo.sampleDefault('date')))
assert.match(fo.sampleDefault('time'),/T\d{2}:\d{2}(:\d{2})?(Z|[+-]\d{2}:\d{2})$/,`time 样本应为带时区的 ISO 时间戳，实际 ${JSON.stringify(fo.sampleDefault('time'))}`)
assert.ok(fo.sampleDefault('string').length>0,'string 样本非空')
const seriesSample=JSON.parse(fo.sampleDefault('timeSeries'))
assert.ok(Array.isArray(seriesSample),`timeSeries 样本应为 JSON 数组，实际 ${JSON.stringify(seriesSample)}`)
for(const point of seriesSample){
  assert.ok(point&&typeof point==='object','timeSeries 样本元素应为对象')
  assert.match(String(point.timestamp),/T\d{2}:\d{2}(:\d{2})?(Z|[+-]\d{2}:\d{2})$/,`timeSeries 样本 timestamp 应带时区，实际 ${JSON.stringify(point)}`)
  assert.equal(typeof point.value,'number',`timeSeries 样本 value 应为数值，实际 ${JSON.stringify(point)}`)
}
assert.ok(seriesSample.length>=1,'timeSeries 规范样本至少一条记录')
ok('sampleDefault：8 个 kind 样本形态可解析（timeSeries=[{timestamp(带时区),value(数值)}]、date 纯日期、time 带时区）')

console.log(`\n全部通过（${passed} 组断言）`)
