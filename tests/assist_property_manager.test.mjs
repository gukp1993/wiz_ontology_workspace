// T6 · O2 属性/共享属性编辑接入辅助填写 回归（2026-09-21）。运行（仓库根）：
//   node --import ./tests/ts_hooks.mjs tests/assist_property_manager.test.mjs
// 模式：SFC 编译（mapping_forms 同款）——PropertyManager.vue 挂真实 AssistPanel（T3 状态机），
// EditorField/EditorHead/PropertyFormatting 桩化；采纳语义经 useAssistPanel 驱动组件暴露的
// 同一 binding 对象（面板与状态机的集成缝）验证。
// 覆盖：binding 快照白名单映射、apply 复用 setRange/setObservation 写路径（与手动 UI 等价、
// 联动不残留）、formatting 整组写/清、采纳只改本地草稿（formSave 间谍零调用、不开影响确认）、
// 显式保存与共享高影响确认冒烟、共享引用只读态无入口、手改后面板过期与撤销保护解除；
// 默认勾选门控（需求 §3.5）：ready 建议仅在宿主对应字段旧值为空时默认勾选，替换真实旧值
// 默认 checked=false、显式 setChecked 后才采纳（④b2/⑤f0/⑦a0/⑦e0）。
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { readFileSync, mkdtempSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve, dirname, basename } from 'node:path'
import { pathToFileURL } from 'node:url'

const require = createRequire(new URL('../frontend/package.json', import.meta.url))
const { parse, compileScript, compileTemplate } = require('@vue/compiler-sfc')
const ts = require('typescript'), { createSSRApp, reactive, h } = require('vue'), { renderToString } = require('@vue/server-renderer')

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

const { propertyAssistBinding } = await import('../frontend/src/assist/propertyBinding.ts')
const { useAssistPanel } = await import('../frontend/src/assist/useAssistPanel.ts')

// ── 夹具 ─────────────────────────────────────────────────────────────────────
function baseGraph() {
  return [
    { '@id': 'mg:cluster', '@type': 'owl:Class', 'rdfs:label': '储能簇' },
    { '@id': 'mg:shared_soc', '@type': 'mg:SharedProperty', 'rdfs:label': 'SOC', 'rdfs:comment': '共享SOC统一口径', 'rdfs:range': { '@id': 'xsd:double' }, 'mg:apiName': 'soc' },
    { '@id': 'mg:p_power', '@type': 'owl:DatatypeProperty', 'rdfs:domain': { '@id': 'mg:cluster' }, 'rdfs:label': '额定功率', 'rdfs:comment': '簇级额定功率', 'rdfs:range': { '@id': 'xsd:double' }, 'mg:apiName': 'power', 'mg:decimalPlaces': 2, 'mg:formatting': { '@type': '@json', '@value': { mode: 'builtin', style: 'standard', decimals: 1 } } },
    { '@id': 'mg:p_flow', '@type': 'owl:DatatypeProperty', 'rdfs:domain': { '@id': 'mg:cluster' }, 'rdfs:label': '电压曲线', 'rdfs:comment': '电压时间序列', 'rdfs:range': { '@id': 'xsd:double' }, 'mg:valueShape': 'timeSeries', 'mg:apiName': 'vcurve' },
    { '@id': 'mg:p_ref', '@type': 'owl:DatatypeProperty', 'rdfs:domain': { '@id': 'mg:cluster' }, 'rdfs:label': 'SOC', 'rdfs:comment': '', 'mg:sharedProperty': { '@id': 'mg:shared_soc' }, 'mg:apiName': 'soc_ref' },
  ]
}

/** SSR 挂载 PropertyManager：form-guard/form-save 桩；form-save 记 savedCount 充当间谍。 */
async function mountPM({ kind, propertyId = '', targetTypeId = '', graph = baseGraph() } = {}) {
  let api = null, savedCount = 0
  const state = reactive({ ontology: { '@graph': graph } })
  const propsObj = { state, kind, propertyId, targetTypeId }
  const setup = PMComponent.setup
  PMComponent.setup = (p, c) => { api = setup(p, c); return api }
  const app = createSSRApp(PMComponent, propsObj)
  app.provide('form-guard', { register() {}, unregister() {}, hasDirty: () => false, editing: () => false })
  app.provide('form-save', { async submitForm(area, mutate) { mutate(); savedCount++; return { ok: true, message: '' } } })
  await renderToString(app)
  assert.ok(api && typeof api.openAssist === 'function', 'setup 应暴露辅助填写接线（openAssist）')
  const html = () => renderToString(createSSRApp({ props: PMComponent.props, ssrRender: PMComponent.ssrRender, setup: () => api }, propsObj))
  const nodeOf = (id) => graph.find(n => n['@id'] === id)
  return { api, state, graph, nodeOf, saved: () => savedCount, html }
}

/** useAssistPanel 的桩 API：context 固定成功；generate 按编排逐个出结果（T3 测试同款思路）。 */
function stubAssistApi({ suggestions = [], generatePlan = [] } = {}) {
  const calls = { context: [], generate: [] }
  const okGenerate = (over = {}) => ({ requestId: 'srv-t6', status: 'ok', contextFingerprint: 'fp-t6', questions: [], issues: [], explanation: null, meta: { durationMs: 1, provider: 'x', model: 'stub' }, ...over })
  let n = 0
  return {
    calls,
    api: {
      context: async (body) => {
        calls.context.push(clone(body))
        return { contextToken: 'tok-t6', contextFingerprint: 'fp-t6', context: { targetKind: 'property', title: '辅助填写', editableFields: [], definitions: [], catalog: [], flows: [], modelReady: true } }
      },
      generate: async (body) => {
        calls.generate.push(clone(body))
        const step = generatePlan.length ? generatePlan.shift() : okGenerate({ suggestions })
        return typeof step === 'function' ? step(++n) : step
      },
    },
  }
}
const sug = (id, label, proposed) => ({ id, label, fieldKeys: Object.keys(proposed), proposed, state: 'ready' })

/** 打开辅助面板（真实状态机绑到组件暴露的 binding），生成并返回面板。 */
async function adoptedPanel(binding, { suggestions }) {
  const stub = stubAssistApi({ suggestions })
  const panel = useAssistPanel(stub.api)
  await panel.open(binding)
  await panel.generate()
  return { panel, stub }
}

// ① 工厂单测：快照键与值映射（rdfs:label→label 等；timeSeries 含 obsType；formatting 取 @value；白名单外不出网）
{
  const d = { '@id': 'mg:p_flow', 'rdfs:label': '电压曲线', 'rdfs:comment': '电压时间序列', 'rdfs:range': { '@id': 'xsd:double' }, 'mg:valueShape': 'timeSeries', 'mg:valueSuffix': 'V', 'mg:formatting': { '@type': '@json', '@value': { mode: 'builtin', kind: 'number', style: 'standard', decimals: 1 } } }
  let ts = true
  const calls = { setType: [], setObsType: [] }
  const binding = propertyAssistBinding({
    draft: () => d, targetKind: 'property', targetId: 'mg:p_flow',
    isTimeSeries: () => ts, setType: v => calls.setType.push(v), setObsType: v => calls.setObsType.push(v),
  })
  assertName('①a binding 目标描述正确', binding.space === 'ontology' && binding.targetKind === 'property' && binding.targetId === 'mg:p_flow' && binding.contextTitle === '属性定义', JSON.stringify({ s: binding.space, k: binding.targetKind, id: binding.targetId, t: binding.contextTitle }))
  const snap = binding.draft()
  assertName('①b 快照键恰为白名单（无 @id/valueSuffix/range 等）', JSON.stringify(Object.keys(snap).sort()) === JSON.stringify(['comment', 'dataType', 'formatting', 'label', 'obsType']), JSON.stringify(Object.keys(snap).sort()))
  assertName('①c 键值映射正确（label/comment/dataType/obsType）', snap.label === '电压曲线' && snap.comment === '电压时间序列' && snap.dataType === 'timeSeries' && snap.obsType === 'xsd:double', JSON.stringify(snap))
  assertName('①d formatting 解包 @value 且为克隆', snap.formatting.decimals === 1 && (snap.formatting.decimals = 9) === 9 && d['mg:formatting']['@value'].decimals === 1)

  ts = false
  const scalar = binding.draft()
  assertName('①e 标量态：dataType=range 原值、obsType 不出现', scalar.dataType === 'xsd:double' && !('obsType' in scalar), JSON.stringify(scalar))
  delete d['mg:formatting']
  assertName('①f 未配置 formatting 不出快照', !('formatting' in binding.draft()))
  d['mg:formatting'] = { '@type': '@json', '@value': { mode: 'builtin', style: 'standard', decimals: 1 } }

  binding.apply({ label: '有功功率', comment: '新定义' })
  assertName('①g label/comment 直接写键', d['rdfs:label'] === '有功功率' && d['rdfs:comment'] === '新定义')
  assertName('①h label/comment 不走类型 setter', calls.setType.length === 0 && calls.setObsType.length === 0)

  ts = false
  binding.apply({ dataType: 'timeSeries', obsType: 'xsd:double' })
  assertName('①i dataType 经注入 setType，obsType 在非时间序列下不下发', JSON.stringify(calls.setType) === '["timeSeries"]' && calls.setObsType.length === 0, JSON.stringify(calls))
  ts = true
  binding.apply({ dataType: 'timeSeries', obsType: 'xsd:string' })
  binding.apply({ obsType: '' })
  assertName('①j 原子组：先类型后观测值；空 obsType 不进 setter', JSON.stringify(calls.setType) === '["timeSeries","timeSeries"]' && JSON.stringify(calls.setObsType) === '["xsd:string"]', JSON.stringify(calls))
  ts = false
  binding.apply({ obsType: 'xsd:double' })
  assertName('①k 离开时间序列后 obsType 不再下发', calls.setObsType.length === 1)

  binding.apply({ formatting: { mode: 'builtin', kind: 'number', style: 'percent', percentInput: 'ratio' } })
  assertName('①l formatting 整组按 @json 包装写入', JSON.stringify(d['mg:formatting']) === JSON.stringify({ '@type': '@json', '@value': { mode: 'builtin', kind: 'number', style: 'percent', percentInput: 'ratio' } }), JSON.stringify(d['mg:formatting']))
  binding.apply({ formatting: {} })
  binding.apply({ formatting: { mode: 'natural', instruction: '  ' } })
  binding.apply({ formatting: null })
  assertName('①m formatting 生效配置为空时删除键', !('mg:formatting' in d))

  const snap0 = binding.snapshot()
  assertName('①n snapshot 为草稿节点整体克隆（含白名单外字段）', snap0['@id'] === 'mg:p_flow' && snap0['mg:valueSuffix'] === 'V' && snap0['rdfs:label'] === '有功功率')
  const identity = d
  binding.apply({ label: '再改名' })
  binding.restore(snap0)
  assertName('①o restore 原位恢复（同一对象、内容还原）', d === identity && d['rdfs:label'] === '有功功率')

  const shared = propertyAssistBinding({ draft: () => d, targetKind: 'sharedProperty', targetId: 'mg:shared_soc', isTimeSeries: () => false, setType() {}, setObsType() {} })
  assertName('①p sharedProperty 目标标题', shared.contextTitle === '共享属性定义' && shared.targetKind === 'sharedProperty')
}

// ② apply 经组件 setter 路径：与手动 UI 操作（下拉联动）最终本地草稿完全一致（保存前不动图节点）
{
  const viaAssist = await mountPM({ kind: 'property', propertyId: 'mg:p_power', targetTypeId: 'mg:cluster' })
  viaAssist.api.openAssist()
  viaAssist.api.assistBinding.value.apply({ dataType: 'timeSeries', obsType: 'xsd:double' })

  const viaUi = await mountPM({ kind: 'property', propertyId: 'mg:p_power', targetTypeId: 'mg:cluster' })
  viaUi.api.setRange('timeSeries')        // UI：数据类型选「时间序列」
  viaUi.api.setObservation('xsd:double')  // UI：观测值类型（与默认一致时组件 early-return）
  assertName('②a 进入时间序列：与手动 UI 本地草稿一致', JSON.stringify(viaAssist.api.draft.value) === JSON.stringify(viaUi.api.draft.value), JSON.stringify(viaAssist.api.draft.value) + ' vs ' + JSON.stringify(viaUi.api.draft.value))
  assertName('②a2 采纳前图节点不动', JSON.stringify(viaAssist.nodeOf('mg:p_power')) === JSON.stringify(baseGraph().find(n => n['@id'] === 'mg:p_power')))

  const viaAssist2 = await mountPM({ kind: 'property', propertyId: 'mg:p_power', targetTypeId: 'mg:cluster' })
  viaAssist2.api.openAssist()
  viaAssist2.api.assistBinding.value.apply({ dataType: 'timeSeries', obsType: 'xsd:string' })
  const viaUi2 = await mountPM({ kind: 'property', propertyId: 'mg:p_power', targetTypeId: 'mg:cluster' })
  viaUi2.api.setRange('timeSeries')
  viaUi2.api.setObservation('xsd:string')
  assertName('②b 指定观测类型：与手动 UI 本地草稿一致', JSON.stringify(viaAssist2.api.draft.value) === JSON.stringify(viaUi2.api.draft.value))

  const flow = await mountPM({ kind: 'property', propertyId: 'mg:p_flow', targetTypeId: 'mg:cluster' })
  flow.api.openAssist()
  flow.api.assistBinding.value.apply({ dataType: 'xsd:string' })
  const flowUi = await mountPM({ kind: 'property', propertyId: 'mg:p_flow', targetTypeId: 'mg:cluster' })
  flowUi.api.setRange('xsd:string')
  const draft = flow.api.draft.value
  assertName('②c 离开时间序列：与手动 UI 一致且联动不残留', JSON.stringify(draft) === JSON.stringify(flowUi.api.draft.value) && !('mg:valueShape' in draft) && draft['rdfs:range']['@id'] === 'xsd:string', JSON.stringify(draft))
  assertName('②d 离开时间序列后快照不再含 obsType', !('obsType' in flow.api.assistBinding.value.draft()) && flow.api.assistBinding.value.draft().dataType === 'xsd:string')
}

// ③ formatting 整组：写入/更新/清空；与 dataType 同组采纳时在类型联动清理之后写入（不被误删）
{
  const m = await mountPM({ kind: 'property', propertyId: 'mg:p_flow', targetTypeId: 'mg:cluster' })
  m.api.openAssist()
  const b = m.api.assistBinding.value
  b.apply({ formatting: { mode: 'builtin', kind: 'number', style: 'percent', percentInput: 'ratio' } })
  assertName('③a formatting 写入 @json 包装', JSON.stringify(m.api.draft.value['mg:formatting']) === JSON.stringify({ '@type': '@json', '@value': { mode: 'builtin', kind: 'number', style: 'percent', percentInput: 'ratio' } }), JSON.stringify(m.api.draft.value['mg:formatting']))
  b.apply({ formatting: { mode: 'natural', instruction: '千分位展示' } })
  assertName('③b formatting 整组替换（非合并）', m.api.draft.value['mg:formatting']['@value'].instruction === '千分位展示' && !('style' in m.api.draft.value['mg:formatting']['@value']))
  b.apply({ formatting: {} }) // 无 mode：等同未配置（A2），删键
  assertName('③c 无 mode 的 formatting 视为未配置并删键', !('mg:formatting' in m.api.draft.value))

  const m2 = await mountPM({ kind: 'property', propertyId: 'mg:p_flow', targetTypeId: 'mg:cluster' })
  m2.api.openAssist()
  m2.api.assistBinding.value.apply({ dataType: 'xsd:string', formatting: { mode: 'builtin', kind: 'string', style: 'template', template: 'V-{value}' } })
  const draft2 = m2.api.draft.value
  assertName('③d dataType+formatting 同组采纳：类型联动后 formatting 仍在', draft2['rdfs:range']['@id'] === 'xsd:string' && !('mg:valueShape' in draft2) && draft2['mg:formatting']['@value'].template === 'V-{value}', JSON.stringify(draft2))
  assertName('③d2 采纳前图节点不动', JSON.stringify(m2.nodeOf('mg:p_flow')) === JSON.stringify(baseGraph().find(n => n['@id'] === 'mg:p_flow')))
}

// ④ 采纳建议：draft 更新、formSave 间谍零调用、共享影响确认未弹出；显式保存仍走原校验
{
  const m = await mountPM({ kind: 'property', propertyId: 'mg:p_power', targetTypeId: 'mg:cluster' })
  m.api.openAssist()
  const binding = m.api.assistBinding.value
  assertName('④a 私有属性 binding 指向本体属性目标', binding.targetKind === 'property' && binding.targetId === 'mg:p_power')
  const { panel, stub } = await adoptedPanel(binding, { suggestions: [sug('s1', '名称与定义', { label: '有功功率', comment: '储能簇可充放功率上限' })] })
  assertName('④b 生成请求携带白名单快照', JSON.stringify(Object.keys(stub.calls.generate[0].draft).sort()) === JSON.stringify(['comment', 'dataType', 'formatting', 'label']), JSON.stringify(stub.calls.generate[0].draft))
  assertName('④b2 门控：建议字段宿主旧值非空时 ready 建议默认 checked=false（替换真实旧值需显式勾选）', panel.checked.value['s1'] === false, JSON.stringify(panel.checked.value))
  panel.setChecked('s1', true) // 用户显式勾选才替换旧值
  assertName('④c 采纳成功且草稿更新', panel.adopt() === true && m.api.draft.value['rdfs:label'] === '有功功率' && m.api.draft.value['rdfs:comment'] === '储能簇可充放功率上限')
  assertName('④d 采纳绝不触发表单保存（间谍零调用）', m.saved() === 0)
  assertName('④e 采纳不开共享影响确认、无错误', m.api.impactOpen.value === false && m.api.error.value === '')
  assertName('④f 图内节点未写（保存前不动权威草稿）', m.nodeOf('mg:p_power')['rdfs:label'] === '额定功率')

  await m.api.save() // 显式保存冒烟：走原校验 + formSave 桩
  assertName('④g 显式保存走桩并把受控键合入节点', m.saved() === 1 && m.nodeOf('mg:p_power')['rdfs:label'] === '有功功率' && m.nodeOf('mg:p_power')['rdfs:comment'] === '储能簇可充放功率上限')
  assertName('④h 保存合入保留非受控键', m.nodeOf('mg:p_power')['mg:apiName'] === 'power' && m.nodeOf('mg:p_power')['rdfs:domain']['@id'] === 'mg:cluster')
  assertName('④i 撤销恢复采纳前草稿', panel.undo() === true && m.api.draft.value['rdfs:label'] === '额定功率')
}

// ⑤ 共享定义：就地维护与共享属性库编辑均面向 sharedProperty；采纳不开影响确认，显式保存弹确认
{
  const m = await mountPM({ kind: 'property', propertyId: 'mg:p_ref', targetTypeId: 'mg:cluster' })
  assertName('⑤a 共享引用只读态无辅助入口（按钮不渲染）', m.api.readonly.value === true && !/✦ 辅助填写/.test(await m.html()))
  m.api.openSharedDef()
  assertName('⑤b 就地维护共享定义：入口出现', m.api.editingShared.value === true && /✦ 辅助填写/.test(await m.html()))
  m.api.openAssist()
  const binding = m.api.assistBinding.value
  assertName('⑤c 就地维护面向 sharedProperty 目标', binding.targetKind === 'sharedProperty' && binding.targetId === 'mg:shared_soc' && binding.draft().label === 'SOC')

  const s = await mountPM({ kind: 'shared', propertyId: 'mg:shared_soc' })
  assertName('⑤d 共享属性库编辑有入口', /✦ 辅助填写/.test(await s.html()))
  s.api.openAssist()
  assertName('⑤e 共享属性库 binding 同样面向 sharedProperty', s.api.assistBinding.value.targetKind === 'sharedProperty' && s.api.assistBinding.value.targetId === 'mg:shared_soc')
  const { panel } = await adoptedPanel(s.api.assistBinding.value, { suggestions: [sug('s1', '业务定义', { comment: '共享SOC统一口径v2' })] })
  assertName('⑤f0 门控：替换非空共享定义（旧 comment 非空）默认不勾', panel.checked.value['s1'] === false)
  panel.setChecked('s1', true)
  assertName('⑤f 共享定义采纳只改本地草稿', panel.adopt() === true && s.api.draft.value['rdfs:comment'] === '共享SOC统一口径v2' && s.saved() === 0)
  assertName('⑤g 采纳本身不开影响确认', s.api.impactOpen.value === false)
  await s.api.save() // 高影响（业务定义变化）：打开确认弹窗，不落盘
  assertName('⑤h 显式保存触发共享影响确认', s.api.impactOpen.value === true && s.saved() === 0)
  s.api.impactAck.value = true
  s.api.confirmImpact()
  await settle()
  assertName('⑤i 确认后保存落盘', s.saved() === 1 && s.nodeOf('mg:shared_soc')['rdfs:comment'] === '共享SOC统一口径v2')

  const n1 = await mountPM({ kind: 'property', targetTypeId: 'mg:cluster' })
  n1.api.openAssist()
  const n2 = await mountPM({ kind: 'shared' })
  n2.api.openAssist()
  assertName('⑤j 新建（未入图）目标 id 为空串，仍区分 property/sharedProperty', n1.api.assistBinding.value.targetId === '' && n1.api.assistBinding.value.targetKind === 'property' && n2.api.assistBinding.value.targetId === '' && n2.api.assistBinding.value.targetKind === 'sharedProperty')
}

// ⑥ 面板挂载/收起：入口开关面板；关闭后卸载（SSR 会输出模板注释，断言以面板实际内容为准）
{
  const m = await mountPM({ kind: 'property', propertyId: 'mg:p_power', targetTypeId: 'mg:cluster' })
  assertName('⑥a 默认无面板', !(await m.html()).includes('assist-panel'))
  m.api.openAssist()
  const openHtml = await m.html()
  assertName('⑥b 打开后真实 AssistPanel 渲染（idle 引导可见）', openHtml.includes('assist-panel') && openHtml.includes('获取上下文') && openHtml.includes('本次参考内容'))
  m.api.closeAssist()
  assertName('⑥c 收起后面板卸载', !(await m.html()).includes('assist-panel'))
}

// ⑦ 手改后面板 stale、撤销保护解除（组件经 notifyDraftChanged/syncStaleness 与面板联动）
{
  const m = await mountPM({ kind: 'property', propertyId: 'mg:p_power', targetTypeId: 'mg:cluster' })
  m.api.openAssist()
  const binding = m.api.assistBinding.value
  const genResp = (suggestions) => ({ requestId: 'srv-t6', status: 'ok', contextFingerprint: 'fp-t6', questions: [], issues: [], explanation: null, meta: { durationMs: 1, provider: 'x', model: 'stub' }, suggestions })
  const stub = stubAssistApi({ generatePlan: [genResp([sug('s1', '业务定义', { comment: '建议定义' })]), genResp([sug('s2', '业务定义2', { comment: '再定义' })])] })
  const panel = useAssistPanel(stub.api)
  await panel.open(binding)
  await panel.generate()
  assertName('⑦a0 门控：替换非空 comment 的 ready 建议默认不勾', panel.checked.value['s1'] === false)
  panel.setChecked('s1', true)
  assertName('⑦a 采纳后可撤销且未过期', panel.adopt() === true && panel.canUndo.value === true && panel.stale.value === false)
  m.api.setLabel('手改名称') // UI 写路径改草稿（面板写入之外）
  assertName('⑦b 面板读到漂移后的草稿', binding.draft().label === '手改名称')
  assertName('⑦c 指纹漂移：syncStaleness 报过期并解除撤销保护', panel.syncStaleness() === true && panel.canUndo.value === false && panel.undo() === false)
  assertName('⑦d 源码含手改→notifyDraftChanged 的 watch 接线', /notifyDraftChanged/.test(readFileSync(resolve('frontend/src/ontology/PropertyManager.vue'), 'utf8')))

  await panel.refreshContext() // 重取上下文：令牌绑定手改后的草稿
  await panel.generate()       // 消费编排的第二批建议（s2）
  assertName('⑦e0 门控：重生成后替换非空 comment 仍默认不勾', panel.checked.value['s2'] === false)
  panel.setChecked('s2', true)
  assertName('⑦e 重取上下文并重新生成后可再采纳', panel.stale.value === false && panel.adopt() === true && panel.canUndo.value === true && m.api.draft.value['rdfs:comment'] === '再定义')
  m.api.setComment('手改定义')
  panel.notifyDraftChanged() // PropertyManager 深度 watch 手改时调用的同一入口
  assertName('⑦f 显式过期通知：stale 且撤销保护解除', panel.stale.value === true && panel.canUndo.value === false && panel.justAdopted.value === false)
}

const failed = results.filter(r => !r.ok)
console.log(`\n统计：${results.length - failed.length}/${results.length} 项通过`)
try { rmSync(root, { recursive: true, force: true }) } catch { /* 临时目录 */ } finally { globalThis.fetch = realFetch }
if (failed.length) { console.log('未通过：' + failed.map(f => f.name).join('；')); process.exit(1) }
