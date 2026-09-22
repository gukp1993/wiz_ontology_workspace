// T6 · O2 属性/共享属性编辑接入「整表自动填写」回归（2026-09-22 改版重写）。运行（仓库根）：
//   node --import ./tests/ts_hooks.mjs tests/assist_property_manager.test.mjs
// 模式：SFC 编译（mapping_forms 同款）——PropertyManager.vue 挂真实 AssistPanel（T3 状态机），
// EditorField/EditorHead/PropertyFormatting 桩化；回填语义经 useAssistPanel 驱动组件暴露的
// 同一 binding 对象（面板与状态机的集成缝）验证，mock autofill/1 响应（真实契约 digest）。
// 覆盖：binding 契约业务枚举快照（xsd: 前缀剥离/timeSeries+obsType）、contractInfo/codecs 注册、
// dataTypeTransform+formattingCodec 校验、typeCore 原子组整组落位（成组应用/不产生半组/整组拒绝）、
// formatting 依赖序与 effectiveConfig 复用、生成→直接回填（无勾选卡）、状态条/撤销/查看修改、
// 手改禁整轮撤销、回填后保存计数不变、续轮同单元累积、重开新撤销单元、共享影响确认仍只绑显式保存、
// 只读共享引用无入口 + binding 层拒绝写入 + api 包装转 unresolved 展示；非 assist 断言不回退。
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { readFileSync, mkdtempSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve, dirname, basename } from 'node:path'
import { pathToFileURL } from 'node:url'

const require = createRequire(new URL('../frontend/package.json', import.meta.url))
const { parse, compileScript, compileTemplate } = require('@vue/compiler-sfc')
const ts = require('typescript'), { createSSRApp, reactive } = require('vue'), { renderToString } = require('@vue/server-renderer')

const root = mkdtempSync(join(tmpdir(), 'wiz_assist_pm_'))
process.env.WIZ_WORKBENCH_ROOT = root
process.env.WIZ_WORKBENCH_PORT = '18992'
const realFetch = globalThis.fetch
globalThis.fetch = async (url) => { throw new Error('测试不应发起真实请求：' + String(url)) } // 全程禁网：辅助 API 一律走桩

const results = []
const assertName = (name, cond, detail = '') => { results.push({ name, ok: !!cond }); console.log(`${cond ? '通过' : '失败'}：${name}${cond ? '' : ' — ' + (detail || '断言不成立')}`) }
const clone = (x) => JSON.parse(JSON.stringify(x))
const settle = async () => { await new Promise(r => setImmediate(r)); await new Promise(r => setImmediate(r)) }

// ── SFC 加载（子组件桩化可覆盖；未覆盖的 .vue 一律报错，防止静默漏桩）────────
async function loadSfc(path, vueOverrides = {}) {
  const filename = resolve(path)
  const { descriptor } = parse(readFileSync(filename, 'utf8'), { filename })
  const id = basename(path).replace(/\.vue$/, '')
  const script = compileScript(descriptor, { id })
  const template = compileTemplate({ source: descriptor.template.content, filename, id, ssr: true, ssrCssVars: [], compilerOptions: { bindingMetadata: script.bindings } })
  assert.deepEqual(template.errors, [])
  let code = script.content
  for (const [spec, globalName] of Object.entries(vueOverrides)) {
    const re = new RegExp("import (\\w+) from ['\"]" + spec.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + "['\"]")
    assert.match(code, re, '应存在待桩化的子组件导入：' + spec)
    code = code.replace(re, 'const $1 = ' + globalName)
  }
  code = code.replace('export default ', 'const Component = ') + '\n' + template.code + '\nComponent.ssrRender=ssrRender; export default Component;'
  code = code.replace(/from (['"])([^'"]+)\1/g, (_, q, spec) => 'from ' + JSON.stringify(pathToFileURL(spec.startsWith('.') ? resolve(dirname(filename), spec + '.ts') : require.resolve(spec)).href))
  const file = join(root, id + '.mjs')
  writeFileSync(file, ts.transpileModule(code, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText)
  return (await import(pathToFileURL(file).href)).default
}

// 惰性子组件：只占渲染位，不参与断言（字段/头部/格式化的行为由各自回归覆盖）。
globalThis.__assistStub = { setup: () => () => null }
// AssistPanel 真实编译（辅助入口的挂载与渲染参与断言；其 .ts 依赖经通用重写解析）
globalThis.__assistPanel = await loadSfc('frontend/src/assist/AssistPanel.vue')
const PMComponent = await loadSfc('frontend/src/ontology/PropertyManager.vue', {
  '../shared/EditorField.vue': '__assistStub',
  '../shared/EditorHead.vue': '__assistStub',
  './PropertyFormatting.vue': '__assistStub',
  '../assist/AssistPanel.vue': '__assistPanel',
})

const { propertyAssistBinding, PROPERTY_ASSIST_READONLY_REASON } = await import('../frontend/src/assist/propertyBinding.ts')
const { useAssistPanel } = await import('../frontend/src/assist/useAssistPanel.ts')
const { applyOperations } = await import('../frontend/src/assist/formAutofill.ts')
const { FORM_PROPERTY, FORM_SHARED_PROPERTY } = await import('../frontend/src/assist/formContracts.gen.ts')
const { setPropertyDataType, propertyDataType } = await import('../frontend/src/ontology/propertyModel.ts')

// ── 夹具 ─────────────────────────────────────────────────────────────────────
function baseGraph() {
  return [
    { '@id': 'mg:cluster', '@type': 'owl:Class', 'rdfs:label': '储能簇' },
    { '@id': 'mg:shared_soc', '@type': 'mg:SharedProperty', 'rdfs:label': 'SOC', 'rdfs:comment': '共享SOC统一口径', 'rdfs:range': { '@id': 'xsd:double' }, 'mg:apiName': 'soc' },
    { '@id': 'mg:p_power', '@type': 'owl:DatatypeProperty', 'rdfs:domain': { '@id': 'mg:cluster' }, 'rdfs:label': '额定功率', 'rdfs:comment': '簇级额定功率', 'rdfs:range': { '@id': 'xsd:double' }, 'mg:apiName': 'power', 'mg:decimalPlaces': 2, 'mg:formatting': { '@type': '@json', '@value': { mode: 'builtin', kind: 'number', style: 'standard', decimals: 1 } } },
    { '@id': 'mg:p_flow', '@type': 'owl:DatatypeProperty', 'rdfs:domain': { '@id': 'mg:cluster' }, 'rdfs:label': '电压曲线', 'rdfs:comment': '电压时间序列', 'rdfs:range': { '@id': 'xsd:double' }, 'mg:valueShape': 'timeSeries', 'mg:apiName': 'vcurve' },
    { '@id': 'mg:p_ref', '@type': 'owl:DatatypeProperty', 'rdfs:domain': { '@id': 'mg:cluster' }, 'rdfs:label': 'SOC', 'rdfs:comment': '', 'mg:sharedProperty': { '@id': 'mg:shared_soc' }, 'mg:apiName': 'soc_ref' },
  ]
}

/** SSR 挂载 PropertyManager：form-guard/form-save 桩；form-save 记 savedCount 充当间谍；
    assistApi 可注入（验证 PM 的只读 api 包装），缺省走 PM 内部 defaultAssistApi（fetch 已禁）。 */
async function mountPM({ kind, propertyId = '', targetTypeId = '', graph = baseGraph(), assistApi = null } = {}) {
  let api = null, savedCount = 0
  const state = reactive({ ontology: { '@graph': graph } })
  const propsObj = { state, kind, propertyId, targetTypeId }
  const setup = PMComponent.setup
  PMComponent.setup = (p, c) => { api = setup(p, c); return api }
  const app = createSSRApp(PMComponent, propsObj)
  app.provide('form-guard', { register() {}, unregister() {}, hasDirty: () => false, editing: () => false })
  app.provide('form-save', { async submitForm(area, mutate) { mutate(); savedCount++; return { ok: true, message: '' } } })
  if (assistApi) app.provide('assist-api', assistApi)
  await renderToString(app)
  assert.ok(api && typeof api.openAssist === 'function', 'setup 应暴露辅助填写接线（openAssist）')
  const html = () => renderToString(createSSRApp({ props: PMComponent.props, ssrRender: PMComponent.ssrRender, setup: () => api }, propsObj))
  const nodeOf = (id) => graph.find(n => n['@id'] === id)
  return { api, state, graph, nodeOf, saved: () => savedCount, html }
}

/** autofill/1 桩：context 固定成功；generate 按编排逐个出 fill 响应（可注入函数动态应答）。 */
function stubApi({ fillPlan = [] } = {}) {
  const calls = { context: [], generate: [] }
  const editableFields = [
    { key: 'label', label: '属性名称', kind: 'text', required: true, options: null, group: null, help: '' },
    { key: 'comment', label: '业务定义', kind: 'textarea', required: true, options: null, group: null, help: '' },
    { key: 'dataType', label: '数据类型', kind: 'select', required: true, options: ['string', 'double', 'boolean', 'dateTime', 'array', 'struct', 'timeSeries'], group: null, help: '' },
    { key: 'obsType', label: '观测值类型', kind: 'select', required: false, options: ['string', 'double', 'boolean', 'dateTime'], group: 'dataType', help: '' },
    { key: 'formatting', label: '显示格式', kind: 'composite', required: false, options: null, group: 'formatting', help: '' },
  ]
  return {
    calls,
    fillPlan,
    api: {
      context: async (body) => {
        calls.context.push(clone(body))
        return { contextToken: 'tok-t6', contextFingerprint: 'cfp-t6', context: { targetKind: body.targetKind, title: '属性「测试」', editableFields, definitions: [], catalog: [], flows: [], modelReady: true } }
      },
      generate: async (body) => {
        calls.generate.push(clone(body))
        const step = fillPlan.shift()
        if (step === undefined) throw new Error('测试桩未编排该请求')
        return typeof step === 'function' ? step(body) : step
      },
    },
  }
}
/** autofill/1 fill 响应：真实契约 digest（前端 contractInfo 比对通过） */
const fillResp = (over = {}) => ({
  protocol: 'autofill/1', status: 'ok', requestId: 'srv-t6', formId: 'property',
  schemaVersion: FORM_PROPERTY.schemaVersion, schemaDigest: FORM_PROPERTY.schemaDigest,
  target: { space: 'ontology', targetKind: 'property', targetId: '' },
  draftFingerprint: 'dfp-t6', contextFingerprint: 'cfp-t6',
  sessionId: 's_1', roundId: 'r_1',
  operations: [], questions: [], unresolved: [], summary: '',
  ...over,
})
const setOp = (field, value) => ({ op: 'set', field, value, basis: { kind: 'intent', quote: '填写' } })

/** 打开面板（真实状态机绑到组件暴露的 binding），准备生成并返回面板。 */
async function drive(binding, stub, { intent = '填写' } = {}) {
  const panel = useAssistPanel(stub.api)
  await panel.open(binding)
  panel.intent.value = intent
  return panel
}

/** binding 工厂快捷方式：注入与 PropertyManager.setRange/setObservation 完全一致的写路径
    （propertyModel.setPropertyDataType + 联动清理），使单元断言与手动 UI 等价；并记录调用序。 */
function makeBinding(d, over = {}) {
  const calls = { setType: [], setObsType: [] }
  const selectedTypeOf = (n) => propertyDataType(n).type === 'timeSeries' ? 'timeSeries' : (n?.['rdfs:range']?.['@id'] || 'xsd:string')
  const setType = (v) => {
    calls.setType.push(v)
    if (!d || v === selectedTypeOf(d)) return
    const base = propertyDataType(d).type === 'timeSeries' ? String(d['rdfs:range']?.['@id'] || 'xsd:string').replace('xsd:', '') : 'double'
    setPropertyDataType(d, v === 'timeSeries' ? { type: 'timeSeries', valueType: base } : { type: String(v).replace('xsd:', '') })
    delete d['mg:valueType']; delete d['mg:formatting']
    if (!['xsd:double', 'xsd:decimal', 'xsd:integer'].includes(d['rdfs:range']?.['@id'] || 'xsd:string')) delete d['mg:decimalPlaces']
  }
  const setObsType = (v) => {
    calls.setObsType.push(v)
    if (!d || v === (d['rdfs:range']?.['@id'] || 'xsd:string')) return
    setPropertyDataType(d, { type: 'timeSeries', valueType: String(v).replace('xsd:', '') })
    delete d['mg:valueType']; delete d['mg:formatting']
    if (!['xsd:double', 'xsd:decimal', 'xsd:integer'].includes(v)) delete d['mg:decimalPlaces']
  }
  const binding = propertyAssistBinding({
    draft: () => d, targetKind: 'property', targetId: String(d['@id'] || ''),
    setType, setObsType,
    ...over,
  })
  return { binding, calls }
}

// ① 工厂单测：契约业务枚举快照（xsd: 前缀剥离）、contractInfo、codecs 注册
{
  const d = { '@id': 'mg:p_flow', 'rdfs:label': '电压曲线', 'rdfs:comment': '电压时间序列', 'rdfs:range': { '@id': 'xsd:double' }, 'mg:valueShape': 'timeSeries', 'mg:valueSuffix': 'V', 'mg:formatting': { '@type': '@json', '@value': { mode: 'builtin', kind: 'number', style: 'standard', decimals: 1 } } }
  const { binding } = makeBinding(d)
  assertName('①a binding 目标描述正确', binding.space === 'ontology' && binding.targetKind === 'property' && binding.targetId === 'mg:p_flow' && binding.contextTitle === '属性定义', JSON.stringify({ s: binding.space, k: binding.targetKind, t: binding.contextTitle }))
  const snap = binding.draft()
  assertName('①a2 快照键恰为白名单（无 @id/valueSuffix/range 等）', JSON.stringify(Object.keys(snap).sort()) === JSON.stringify(['comment', 'dataType', 'formatting', 'label', 'obsType']), JSON.stringify(Object.keys(snap).sort()))
  assertName('①b dataType/obsType 为契约业务枚举（无 xsd: 前缀）', snap.dataType === 'timeSeries' && snap.obsType === 'double', JSON.stringify({ dataType: snap.dataType, obsType: snap.obsType }))
  assertName('①c label/comment 映射正确、formatting 解包 @value 且为克隆', snap.label === '电压曲线' && snap.comment === '电压时间序列' && snap.formatting.decimals === 1 && ((snap.formatting.decimals = 9) === 9 && d['mg:formatting']['@value'].decimals === 1))

  d['mg:valueShape'] = undefined; delete d['mg:valueShape']
  const scalar = binding.draft()
  assertName('①d 标量态：dataType=业务基础类型、obsType 不出现', scalar.dataType === 'double' && !('obsType' in scalar), JSON.stringify(scalar))
  d['rdfs:range'] = { '@id': 'xsd:integer' }
  assertName('①e 历史精确类型如实投影（模型不可经契约枚举改写）', binding.draft().dataType === 'integer', binding.draft().dataType)
  d['rdfs:range'] = { '@id': 'xsd:double' }
  delete d['mg:formatting']
  assertName('①f 未配置 formatting 不出快照', !('formatting' in binding.draft()))

  assertName('①g contractInfo=本地生成物指纹（property）', binding.formId === 'property' && binding.contractInfo().schemaVersion === FORM_PROPERTY.schemaVersion && binding.contractInfo().schemaDigest === FORM_PROPERTY.schemaDigest, JSON.stringify(binding.contractInfo()))
  const shared = propertyAssistBinding({ draft: () => d, targetKind: 'sharedProperty', targetId: 'mg:shared_soc', setType() {}, setObsType() {} })
  assertName('①h sharedProperty：formId/contractInfo/标题', shared.formId === 'sharedProperty' && shared.contractInfo().schemaDigest === FORM_SHARED_PROPERTY.schemaDigest && shared.contextTitle === '共享属性定义')

  assertName('①i codecs 覆盖五个契约字段', JSON.stringify(Object.keys(binding.codecs).sort()) === JSON.stringify(['comment', 'dataType', 'formatting', 'label', 'obsType']), JSON.stringify(Object.keys(binding.codecs).sort()))
}

// ② codec 校验：dataTypeTransform 契约枚举、formatting 结构；非法值拒绝（防御层，后端主责）
{
  const d = { '@id': 'mg:p_power', 'rdfs:label': '额定功率', 'rdfs:comment': '', 'rdfs:range': { '@id': 'xsd:double' } }
  const { binding } = makeBinding(d)
  const run = (ops) => applyOperations(clone(binding.draft()), ops, binding.codecs)
  const ok = run([setOp('dataType', 'string'), setOp('obsType', ''), setOp('label', '有功功率')])
  assertName('②a 合法枚举通过（dataType 业务值/obsType 空串清空标记/label）', ok.failures.length === 0 && ok.records.every(r => r.changed), JSON.stringify(ok.failures))
  const bad = run([setOp('dataType', 'xsd:double'), setOp('obsType', 'xsd:string'), setOp('obsType', 'integer'), setOp('formatting', '模板'), setOp('label', 42)])
  assertName('②b 非契约枚举/非对象 formatting/非文本 label 拒绝', bad.failures.length === 5, JSON.stringify(bad.failures))
  const fmt = run([setOp('formatting', { mode: 'builtin', kind: 'number', style: 'percent', percentInput: 'ratio' }), { op: 'clear', field: 'formatting', basis: { kind: 'intent', quote: '清' } }])
  assertName('②c formatting 合法对象与 clear(null) 均放行（结构校验在 codec，落位在 applyDraft）', fmt.failures.length === 0, JSON.stringify(fmt.failures))
}

// ③ typeCore 原子组落位：成组应用/离开清理/不完整拒绝/独立合法内容照填
{
  const d = { '@id': 'mg:p_power', 'rdfs:label': '额定功率', 'rdfs:comment': '簇级额定功率', 'rdfs:range': { '@id': 'xsd:double' }, 'mg:apiName': 'power' }
  const { binding, calls } = makeBinding(d)
  const proj = () => binding.draft()
  const applyNext = (patch) => binding.applyDraft({ ...proj(), ...patch })

  applyNext({ dataType: 'timeSeries', obsType: 'double' })
  assertName('③a 进入时间序列：dataType+obsType 成组落位（经组件写路径）', d['mg:valueShape'] === 'timeSeries' && d['rdfs:range']['@id'] === 'xsd:double', JSON.stringify(d))
  assertName('③b setter 调用序：先类型后观测值', JSON.stringify(calls.setType) === '["timeSeries"]' && JSON.stringify(calls.setObsType) === '["xsd:double"]', JSON.stringify(calls))

  applyNext({ dataType: 'timeSeries', obsType: 'string' })
  assertName('③c 时间序列内换观测类型：range 跟随、valueShape 保留', d['rdfs:range']['@id'] === 'xsd:string' && d['mg:valueShape'] === 'timeSeries')

  applyNext({ dataType: 'double', obsType: '' })
  assertName('③d 离开时间序列：valueShape 同步清除、无 obsType 残留', !('mg:valueShape' in d) && d['rdfs:range']['@id'] === 'xsd:double' && !('obsType' in proj()))

  applyNext({ label: '有功功率', dataType: 'timeSeries' }) // 组不完整（无 obsType）
  assertName('③e 不完整原子组整组拒绝：类型未动、无半组', !('mg:valueShape' in d) && d['rdfs:range']['@id'] === 'xsd:double', JSON.stringify(d))
  assertName('③f 独立合法字段照常填写（§4.3 只填独立合法组）', d['rdfs:label'] === '有功功率')
  assertName('③g 拒绝原因记入 refusals', binding.refusals.value.length === 1 && binding.refusals.value[0].field === 'dataType' && /观测值类型/.test(binding.refusals.value[0].reason), JSON.stringify(binding.refusals.value))

  const before = clone(d) // 以「当前态」为基线
  binding.resetRefusals()
  applyNext({ dataType: 'double', obsType: 'double' }) // 标量目标却带观测值类型：矛盾组
  assertName('③h 标量目标携带观测值类型的矛盾组整组拒绝', JSON.stringify(d) === JSON.stringify(before), JSON.stringify(d))
  assertName('③i 矛盾组拒绝原因', binding.refusals.value.length === 1 && binding.refusals.value[0].field === 'dataType', JSON.stringify(binding.refusals.value))

  const ts = { '@id': 'mg:p_flow', 'rdfs:label': '电压曲线', 'rdfs:comment': '', 'rdfs:range': { '@id': 'xsd:double' }, 'mg:valueShape': 'timeSeries' }
  const tsb = makeBinding(ts)
  tsb.binding.applyDraft({ ...tsb.binding.draft(), dataType: 'double' }) // 离开组缺 obsType 清空操作（投影仍带旧观测值）
  assertName('③j 离开时间序列但缺 obsType 清空操作：整组拒绝、无半组（后端本应转 unresolved，此为最后防线）', ts['mg:valueShape'] === 'timeSeries' && ts['rdfs:range']['@id'] === 'xsd:double' && tsb.binding.refusals.value.length === 1, JSON.stringify({ d: ts, r: tsb.binding.refusals.value }))
  tsb.binding.applyDraft({ ...tsb.binding.draft(), dataType: 'double', obsType: '' })
  assertName('③k 组完整（显式清空标记）：离开时间序列同步清除、无残留', !('mg:valueShape' in ts) && ts['rdfs:range']['@id'] === 'xsd:double' && !('obsType' in tsb.binding.draft()), JSON.stringify(ts))
}

// ④ formatting：依赖 dataType 的写序、effectiveConfig 复用、kind 一致性、未修改不重放
{
  const d = { '@id': 'mg:p_power', 'rdfs:label': '额定功率', 'rdfs:comment': '', 'rdfs:range': { '@id': 'xsd:double' } }
  const { binding } = makeBinding(d)
  const applyNext = (patch) => binding.applyDraft({ ...binding.draft(), ...patch })

  applyNext({ formatting: { mode: 'builtin', kind: 'number', style: 'percent', percentInput: 'ratio' } })
  assertName('④a formatting 整组按 @json 包装写入', JSON.stringify(d['mg:formatting']) === JSON.stringify({ '@type': '@json', '@value': { mode: 'builtin', kind: 'number', style: 'percent', percentInput: 'ratio' } }), JSON.stringify(d['mg:formatting']))
  applyNext({ formatting: { mode: 'builtin', kind: 'number', style: 'standard' } })
  assertName('④b formatting 整组替换（非合并）', d['mg:formatting']['@value'].style === 'standard' && !('percentInput' in d['mg:formatting']['@value']))
  applyNext({ formatting: {} }) // 无 mode：等同未配置（A2），删键
  assertName('④c 无 mode 的 formatting 视为未配置并删键（复用 effectiveConfig）', !('mg:formatting' in d))
  applyNext({ formatting: null })
  assertName('④d formatting clear 落为删键', !('mg:formatting' in d))

  binding.resetRefusals()
  applyNext({ formatting: { mode: 'builtin', kind: 'string', style: 'template', template: 'V-{value}' } })
  assertName('④e formatting kind 与数据类型不匹配：拒绝写入并记录', !('mg:formatting' in d) && binding.refusals.value.length === 1 && binding.refusals.value[0].field === 'formatting', JSON.stringify(binding.refusals.value))

  // 类型+formatting 同轮：formatting 在类型联动清理之后写入（requires 依赖序），不被误删
  binding.resetRefusals()
  applyNext({ dataType: 'timeSeries', obsType: 'double', formatting: { mode: 'builtin', kind: 'timeSeries', style: 'series' } })
  assertName('④f 同轮类型切换后 formatting 仍按新类型落位', d['mg:valueShape'] === 'timeSeries' && d['mg:formatting']?.['@value']?.kind === 'timeSeries' && binding.refusals.value.length === 0, JSON.stringify({ d, r: binding.refusals.value }))

  // 本轮未修改 formatting：不重放（类型切换按编辑器联动清理，与手动 UI 一致，且不误报拒绝）
  const d2 = { '@id': 'mg:p_flow', 'rdfs:label': '电压曲线', 'rdfs:comment': '', 'rdfs:range': { '@id': 'xsd:double' }, 'mg:valueShape': 'timeSeries', 'mg:formatting': { '@type': '@json', '@value': { mode: 'builtin', kind: 'timeSeries', style: 'series' } } }
  const b2 = makeBinding(d2)
  b2.binding.applyDraft({ ...b2.binding.draft(), dataType: 'double', obsType: '' })
  assertName('④g 未修改的 formatting 不重放：类型切回标量时随联动清理且无误报', !('mg:formatting' in d2) && !('mg:valueShape' in d2) && !b2.binding.refusals.value.length, JSON.stringify({ d: d2, r: b2.binding.refusals.value }))
}

// ⑤ snapshot/restore 节点级零丢失；只读共享引用 binding 层拒绝写入
{
  const d = { '@id': 'mg:p_flow', 'rdfs:label': '电压曲线', 'rdfs:comment': '', 'rdfs:range': { '@id': 'xsd:double' }, 'mg:valueShape': 'timeSeries', 'mg:valueSuffix': 'V' }
  const { binding } = makeBinding(d)
  const snap0 = binding.snapshot()
  assertName('⑤a snapshot 为草稿节点整体克隆（含白名单外字段）', snap0['@id'] === 'mg:p_flow' && snap0['mg:valueSuffix'] === 'V')
  const identity = d
  binding.applyDraft({ ...binding.draft(), label: '再改名' })
  binding.restore(snap0)
  assertName('⑤b restore 原位恢复（同一对象、内容还原）', d === identity && d['rdfs:label'] === '电压曲线')

  const ro = makeBinding(clone(d), { writable: () => false })
  const frozen = clone(ro.binding.draft().label)
  ro.binding.applyDraft({ ...ro.binding.draft(), label: '越权改名', dataType: 'string', obsType: '' })
  assertName('⑤c 只读 binding 拒绝 applyDraft 落笔', ro.binding.draft().label === frozen && ro.binding.draft().dataType === 'timeSeries', JSON.stringify(ro.binding.draft()))
  assertName('⑤d 拒绝原因 = 共享引用只读文案', ro.binding.refusals.value.length >= 1 && ro.binding.refusals.value[0].reason === PROPERTY_ASSIST_READONLY_REASON, JSON.stringify(ro.binding.refusals.value))
  const fail = applyOperations(clone(ro.binding.draft()), [setOp('label', '越权'), setOp('dataType', 'string')], ro.binding.codecs)
  assertName('⑤e codec 层拒写：值不进副本（引擎不计数）', fail.failures.length === 2 && fail.failures.every(f => f.reason === PROPERTY_ASSIST_READONLY_REASON), JSON.stringify(fail.failures))
  ro.binding.apply({ label: '越权2' })
  assertName('⑤f 旧 apply 通道同样拒绝', ro.binding.draft().label === frozen)
}

// ⑥ 组件接线：入口/aria、binding 组装、生成→直接回填→状态条→撤销→手改禁撤销→保存计数
{
  const m = await mountPM({ kind: 'property', propertyId: 'mg:p_power', targetTypeId: 'mg:cluster' })
  assertName('⑥a 默认无 AI 区（入口按钮存在但面板未开）', (await m.html()).includes('pm-assist-trigger') && !(await m.html()).includes('assist-statusbar'))
  m.api.openAssist()
  const openHtml = await m.html()
  assertName('⑥b 入口 aria-expanded 随面板切换', /aria-expanded="true"/.test(openHtml), openHtml.slice(0, 200))
  const binding = m.api.assistBinding.value
  assertName('⑥c 私有属性 binding：formId/目标/契约指纹/refusals 出口', binding.targetKind === 'property' && binding.targetId === 'mg:p_power' && binding.formId === 'property' && binding.contractInfo().schemaDigest === FORM_PROPERTY.schemaDigest && binding.refusals !== undefined)

  const stub = stubApi({ fillPlan: [fillResp({ operations: [
    setOp('label', '有功功率'), setOp('comment', '储能簇可充放功率上限'),
    setOp('dataType', 'timeSeries'), setOp('obsType', 'double'),
    setOp('formatting', { mode: 'builtin', kind: 'timeSeries', style: 'series' }),
  ] })] })
  const panel = await drive(binding, stub)
  await panel.generate()
  assertName('⑥e 合法 operations 直接回填（含 timeSeries 原子组成组落位）', m.api.draft.value['rdfs:label'] === '有功功率' && m.api.draft.value['mg:valueShape'] === 'timeSeries' && m.api.draft.value['rdfs:range']['@id'] === 'xsd:double' && m.api.draft.value['mg:formatting']?.['@value']?.style === 'series', JSON.stringify(m.api.draft.value))
  assertName('⑥f 引擎状态条：已填写 5 项，尚未保存', panel.statusBarText.value === '已填写 5 项，尚未保存', panel.statusBarText.value)
  const filledHtml = await m.html()
  assertName('⑥g 宿主状态条渲染：N 项文案 + 撤销 + 查看修改（逐字段旧→新）', filledHtml.includes('已填写 5 项，尚未保存') && filledHtml.includes('撤销本次填写') && filledHtml.includes('查看修改') && filledHtml.includes('额定功率 → 有功功率'), filledHtml.slice(0, 400))
  assertName('⑥h 回填绝不触发表单保存（间谍零调用）且不开影响确认', m.saved() === 0 && m.api.impactOpen.value === false)
  assertName('⑥i 图内节点未写（保存前不动权威草稿）', m.nodeOf('mg:p_power')['rdfs:label'] === '额定功率' && !('mg:valueShape' in m.nodeOf('mg:p_power')))

  assertName('⑥j 撤销本次填写：恢复整轮起点草稿（含原 formatting/无 valueShape）', m.api.undoAssist() === true && m.api.draft.value['rdfs:label'] === '额定功率' && !('mg:valueShape' in m.api.draft.value) && m.api.draft.value['mg:formatting']?.['@value']?.decimals === 1)
  assertName('⑥k 撤销后状态条消失、保存计数仍为 0', !(await m.html()).includes('已填写 5 项') && m.saved() === 0)
}

// ⑦ 手改通知/禁撤销、续轮同单元、重开新单元、回填后显式保存
{
  const m = await mountPM({ kind: 'property', propertyId: 'mg:p_power', targetTypeId: 'mg:cluster' })
  m.api.openAssist()
  assertName('⑦a0 无可撤销单元时 undoAssist 守卫拒绝', m.api.undoAssist() === false)
  const binding = m.api.assistBinding.value
  const stub = stubApi({ fillPlan: [fillResp({ operations: [setOp('label', '有功功率')] })] })
  const panel = await drive(binding, stub)
  await panel.generate()
  assertName('⑦a 回填后可整轮撤销', m.api.assistCanUndo.value === true)
  m.api.setLabel('手改名称') // UI 写路径改草稿（面板写入之外）
  await settle()
  // SSR harness 不运行 watch（renderToString 后实例作用域停止）：手改→通知引擎+禁整轮撤销的行为以源码锁定
  const pmSrc = readFileSync(resolve('frontend/src/ontology/PropertyManager.vue'), 'utf8')
  assertName('⑦b 手改接线（源码锁定）：watch 检测非面板写入 → notifyDraftChanged + 禁整轮撤销并说明', /json !== assistBaseline[\s\S]{0,260}notifyDraftChanged[\s\S]{0,260}assistCanUndo\.value = false[\s\S]{0,160}不可直接撤销/.test(pmSrc))
  assertName('⑦d 手改值保留（不覆盖用户改动）', m.api.draft.value['rdfs:label'] === '手改名称')

  // 续轮同撤销单元：首轮+补答轮合计一次撤销；状态条随轮累计（去重按字段）
  const m2 = await mountPM({ kind: 'property', propertyId: 'mg:p_power', targetTypeId: 'mg:cluster' })
  m2.api.openAssist()
  const stub2 = stubApi({ fillPlan: [
    fillResp({ sessionId: 's_c', operations: [setOp('label', '有功功率')], questions: [{ id: 'q_1', text: '观测值类型用哪个？', fields: ['obsType'], options: ['double', 'string'], allowUnsure: true }] }),
    fillResp({ sessionId: 's_c', roundId: 'r_2', operations: [setOp('dataType', 'timeSeries'), setOp('obsType', 'double')] }),
  ] })
  const panel2 = await drive(m2.api.assistBinding.value, stub2)
  await panel2.generate()
  assertName('⑦e 有待补问题：引擎留抽屉并提示已填 N 项另有 M 项待补充', panel2.status.value === 'questions' && panel2.statusBarText.value === '已填写 1 项，尚未保存；另有 1 项待补充', panel2.statusBarText.value)
  panel2.answer('q_1', 'double') // 全部已答自动续轮
  await settle()
  assertName('⑦f 续轮补答直接回填（原子组成组）', m2.api.draft.value['mg:valueShape'] === 'timeSeries' && m2.api.draft.value['rdfs:label'] === '有功功率')
  // N=按实际草稿变化去重：label/dataType/obsType + 类型联动清理的 formatting（与手动 UI 等价，如实计入）
  assertName('⑦g 续轮同单元累计：实际变化去重 N=4（含联动清理的 formatting）', m2.api.assistApplied.value === 4 && m2.api.assistChanges.value.some(c => c.field === 'formatting' && c.newText === '（空）'), String(m2.api.assistApplied.value))
  assertName('⑦h 整轮撤销恢复首轮起点（含续轮写入）', m2.api.undoAssist() === true && m2.api.draft.value['rdfs:label'] === '额定功率' && !('mg:valueShape' in m2.api.draft.value))

  // 重开面板再填 = 新撤销单元（引擎 snapshot 钩子对齐宿主轮次，只支持最近一轮）
  m2.api.closeAssist()
  m2.api.openAssist()
  const stub3 = stubApi({ fillPlan: [fillResp({ operations: [setOp('label', '第二次命名')] })] })
  const panel3 = await drive(m2.api.assistBinding.value, stub3)
  await panel3.generate()
  assertName('⑦i 新单元回填生效', m2.api.draft.value['rdfs:label'] === '第二次命名')
  assertName('⑦j 新单元撤销只回到本轮起点（⑦h 已撤销到原始态），不越单元', m2.api.undoAssist() === true && m2.api.draft.value['rdfs:label'] === '额定功率', m2.api.draft.value['rdfs:label'])

  // 回填后显式保存：走原校验与 form-save，保存成功后状态条结束
  const m3 = await mountPM({ kind: 'property', propertyId: 'mg:p_power', targetTypeId: 'mg:cluster' })
  m3.api.openAssist()
  const stub4 = stubApi({ fillPlan: [fillResp({ operations: [setOp('label', '有功功率'), setOp('dataType', 'timeSeries'), setOp('obsType', 'double')] })] })
  const panel4 = await drive(m3.api.assistBinding.value, stub4)
  await panel4.generate()
  await m3.api.save()
  assertName('⑦k 显式保存落盘：受控键合入节点（回填不落盘，保存才落盘）', m3.saved() === 1 && m3.nodeOf('mg:p_power')['rdfs:label'] === '有功功率' && m3.nodeOf('mg:p_power')['mg:valueShape'] === 'timeSeries' && m3.nodeOf('mg:p_power')['mg:apiName'] === 'power' && m3.nodeOf('mg:p_power')['rdfs:domain']['@id'] === 'mg:cluster')
  assertName('⑦l 保存成功后「尚未保存」状态条结束', !(await m3.html()).includes('已填写 3 项'))
  assertName('⑦m 回填后等待（无自动保存触发）保存计数不变', m3.saved() === 1 && m3.api.impactOpen.value === false)
}

// ⑧ 共享场景：就地维护/共享库面向 sharedProperty；采纳语义已废除；影响确认只绑显式保存
{
  const s = await mountPM({ kind: 'shared', propertyId: 'mg:shared_soc' })
  assertName('⑧a 共享属性库入口存在', /✦ 自动填写/.test(await s.html()))
  s.api.openAssist()
  assertName('⑧b 共享库 binding 面向 sharedProperty', s.api.assistBinding.value.formId === 'sharedProperty' && s.api.assistBinding.value.targetKind === 'sharedProperty' && s.api.assistBinding.value.targetId === 'mg:shared_soc')
  const stub = stubApi({ fillPlan: [fillResp({ formId: 'sharedProperty', schemaDigest: FORM_SHARED_PROPERTY.schemaDigest, target: { space: 'ontology', targetKind: 'sharedProperty', targetId: 'mg:shared_soc' }, operations: [setOp('comment', '共享SOC统一口径v2')] })] })
  const panel = await drive(s.api.assistBinding.value, stub)
  await panel.generate()
  assertName('⑧c 共享定义回填只改本地草稿（无勾选/采纳步骤）', s.api.draft.value['rdfs:comment'] === '共享SOC统一口径v2' && s.saved() === 0 && panel.checked === undefined && panel.adopt === undefined)
  await s.api.save() // 高影响（业务定义变化）：先弹确认，不落盘
  assertName('⑧d 显式保存触发共享影响确认', s.api.impactOpen.value === true && s.saved() === 0)
  s.api.impactAck.value = true
  s.api.confirmImpact()
  await settle()
  assertName('⑧e 确认后保存落盘', s.saved() === 1 && s.nodeOf('mg:shared_soc')['rdfs:comment'] === '共享SOC统一口径v2')

  const p = await mountPM({ kind: 'property', propertyId: 'mg:p_ref', targetTypeId: 'mg:cluster' })
  assertName('⑧f 共享引用只读态：无自动填写入口（按钮不渲染）', p.api.readonly.value === true && !/pm-assist-trigger/.test(await p.html()))
  p.api.openAssist()
  assertName('⑧g 只读态 openAssist 无操作（不组装 binding）', p.api.assistVisible.value === false && p.api.assistBinding.value === null)

  const n1 = await mountPM({ kind: 'property', targetTypeId: 'mg:cluster' })
  n1.api.openAssist()
  const n2 = await mountPM({ kind: 'shared' })
  n2.api.openAssist()
  assertName('⑧h 新建（未入图）目标 id 为空串，仍区分 property/sharedProperty', n1.api.assistBinding.value.targetId === '' && n1.api.assistBinding.value.formId === 'property' && n2.api.assistBinding.value.targetId === '' && n2.api.assistBinding.value.formId === 'sharedProperty')
}

// ⑨ 只读 api 包装：请求期只读 → fill 响应 operations 转 unresolved（面板内如实展示原因）
{
  let genCalls = 0
  const inner = stubApi()
  const stub = {
    calls: inner.calls,
    api: {
      context: inner.api.context,
      generate: async (body) => { genCalls++; return inner.api.generate(body) },
    },
  }
  const m = await mountPM({ kind: 'property', propertyId: 'mg:p_power', targetTypeId: 'mg:cluster', assistApi: stub.api })
  m.api.openAssist()
  const body = { requestId: 'r', contextToken: 't', mode: 'fill', protocol: 2, draft: m.api.assistBinding.value.draft(), intent: '填写' }
  inner.fillPlan.push(fillResp({ operations: [setOp('label', '越权写入')] }))
  const r1 = await m.api.assistApi.generate(body)
  assertName('⑨a 可写态直通：operations 原样返回', Array.isArray(r1.operations) && r1.operations.length === 1, JSON.stringify(r1.operations))
  m.state.ontology['@graph'].find(n => n['@id'] === 'mg:p_power')['mg:sharedProperty'] = { '@id': 'mg:shared_soc' } // 运行中变只读（如引用来自其他标签页）；经 reactive 代理触发
  await settle()
  // SSR harness 不运行 watch：只读翻转自动收起面板的接线以源码锁定；此处验证 computed 翻转与 api 包装行为
  assertName('⑨b 只读翻转（computed 生效；watch 收起接线源码锁定）', m.api.readonly.value === true && /watch\(readonly,[\s\S]{0,120}closeAssist\(\)/.test(readFileSync(resolve('frontend/src/ontology/PropertyManager.vue'), 'utf8')))
  inner.fillPlan.push(fillResp({ operations: [setOp('label', '越权写入'), setOp('comment', '越权定义')] }))
  const r2 = await m.api.assistApi.generate(body)
  assertName('⑨c 只读态 operations 全部转 unresolved 并给出原因', r2.operations.length === 0 && r2.unresolved.length === 2 && r2.unresolved.every(u => u.reason === PROPERTY_ASSIST_READONLY_REASON), JSON.stringify(r2.unresolved))
}

// ⑩ 非 assist 回归：校验、受控键合入保留未知字段、上下文请求边界
{
  const m = await mountPM({ kind: 'property', propertyId: 'mg:p_power', targetTypeId: 'mg:cluster' })
  m.api.setLabel('电压曲线') // 与同对象既有属性重名
  await m.api.save()
  assertName('⑩a 同名属性校验仍生效', m.saved() === 0 && /同名属性/.test(m.api.error.value))

  const stub = stubApi()
  const m2 = await mountPM({ kind: 'property', propertyId: 'mg:p_power', targetTypeId: 'mg:cluster', assistApi: stub.api })
  m2.api.openAssist()
  const panelB = await drive(m2.api.assistBinding.value, stub) // 经同一 binding 取上下文（SSR 不触发面板内引擎）
  await settle()
  const ctx = stub.calls.context[0]
  assertName('⑩b context 请求携带 space/ontologyId/target 与白名单快照', ctx && ctx.space === 'ontology' && ctx.ontologyId === 'storage' && ctx.targetKind === 'property' && ctx.targetId === 'mg:p_power' && ctx.purpose === 'fill' && JSON.stringify(Object.keys(ctx.draft).sort()) === JSON.stringify(['comment', 'dataType', 'formatting', 'label']), JSON.stringify(ctx))
  panelB.close()
  m2.api.setLabel('有功功率')
  await m2.api.save()
  assertName('⑩c 保存合入保留非受控键（apiName/domain/decimalPlaces）', m2.saved() === 1 && m2.nodeOf('mg:p_power')['mg:apiName'] === 'power' && m2.nodeOf('mg:p_power')['rdfs:domain']['@id'] === 'mg:cluster' && m2.nodeOf('mg:p_power')['mg:decimalPlaces'] === 2)
}

const failed = results.filter(r => !r.ok)
console.log(`\n统计：${results.length - failed.length}/${results.length} 项通过`)
try { rmSync(root, { recursive: true, force: true }) } catch { /* 临时目录 */ } finally { globalThis.fetch = realFetch }
if (failed.length) { console.log('未通过：' + failed.map(f => f.name).join('；')); process.exit(1) }
