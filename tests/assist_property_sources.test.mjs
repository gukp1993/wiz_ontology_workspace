// T7 · P2 数据库取值 / P3 Redis 取值 / P4 编排取值 的整表自动填写接入回归
// （2026-09-22 改版重写，替代旧「建议卡→勾选→采纳」断言）。运行（仓库根）：
//   node --import ./tests/ts_hooks.mjs tests/assist_property_sources.test.mjs
// 模式与 tests/assist_property_manager.test.mjs 同源：SFC 编译（子组件桩化，PropertySources.vue
// 挂真实 AssistPanel 状态机），回填语义经 useAssistPanel 驱动组件暴露的同一 binding 对象
// （面板与状态机的集成缝）验证，mock autofill/1 响应（真实契约 digest）；form-save 与
// emit('changed') 双间谍 + 全局 fetch 计数锁定「回填≠保存、不触达自动保存、不刷新目录」。
// 覆盖：① binding 契约变体快照（四 kind 白名单键、行组 rowId、view-model 字段不出网）、
// contractInfo/codecs 注册；② 行组 codec（按 rowId 局部更新、未知历史行/未知键保留、未知行与
// 整组替换拒绝、kind 边界拒绝）；③ database 正常链路（连接/表/取值字段/匹配行/说明一次回填）、
// A05 明确改字段名（rated_power→capacity，其余逐字保留）、A08 依赖变化（未同组给全则整组不落并
// 说明；同组给全则合法更新）、无半组（缺 table 整组不落、新增行缺必填项不落半行）；
// ④ redis 链路（command/key/hashField/params 行组按 rowId 局部更新、未知历史行保留、来源形态
// 连接、hashField 依赖 HGET、params 依赖 key）；⑤ flow 链路（输出/输入绑定行组/结果字段、
// 不触发编排详情拉取、缺编排不落）；⑥ kind 边界拒绝展示（不隐式切换、不清空既有配置）；
// ⑦ 组件全链路：页头入口与 aria、状态条/整轮撤销/查看修改、手改禁撤销并作废在途、回填后保存
// 计数不变（含等待超过自动保存窗口）、拒绝原因展示、目标切换与关闭收起、无入口场景，
// 以及非 assist 断言（来源表单校验与保存链路）不回退。
// 已知边界：SSR 渲染不执行 onMounted 与模板 ref 填充（面板自动取上下文、assistTouched 里的
// notifyDraftChanged 面板通道），后者由「组件同一入口 + 源码接线断言 + typecheck」覆盖，
// 浏览器实链路留给独立验收（与 assist_property_manager.test.mjs 同一边界）。
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { readFileSync, mkdtempSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve, dirname } from 'node:path'
import { pathToFileURL } from 'node:url'

const require = createRequire(new URL('../frontend/package.json', import.meta.url))
const { parse, compileScript, compileTemplate } = require('@vue/compiler-sfc')
const ts = require('typescript')
const { createSSRApp, reactive, h, nextTick } = require('vue')
const { renderToString } = require('@vue/server-renderer')

// 组件里用到的浏览器 API：SSR 不执行 onMounted，但模块顶层与 watch(immediate) 会触达它们。
globalThis.window = { matchMedia: () => ({ matches: false, addEventListener() {}, removeEventListener() {} }), addEventListener() {}, removeEventListener() {}, innerHeight: 900, location: { hash: '' } }
globalThis.document = { addEventListener() {}, removeEventListener() {}, querySelector: () => null, querySelectorAll: () => [], activeElement: null }
globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} }
globalThis.requestAnimationFrame = cb => setTimeout(() => cb(Date.now()), 0)
if (!globalThis.crypto?.randomUUID) Object.defineProperty(globalThis.crypto, { value: require('node:crypto').webcrypto, configurable: true })

const root = mkdtempSync(join(tmpdir(), 'wiz_assist_ps_'))
const realFetch = globalThis.fetch
let fetchCount = 0 // 回填期间不得发起任何网络请求（目录刷新/编排详情/保存都算）
globalThis.fetch = async (...a) => { fetchCount++; return realFetch(...a) }

/** 编译真实 SFC（script setup + SSR 模板）。kept：{ 组件名: globalThis 变量名 }，命中的 .vue 导入
 *  改挂到共享变量（用于把真实 AssistPanel 塞进 PropertySources），其余 .vue 一律桩成 render:null。 */
async function loadSFC(absPath, id, kept = {}) {
  const { descriptor } = parse(readFileSync(absPath, 'utf8'), { filename: absPath })
  const script = compileScript(descriptor, { id })
  let code = script.content
  for (const [name, globalName] of Object.entries(kept)) {
    code = code.replace(new RegExp('import\\s+' + name + '\\s+from\\s*[\'"][^\'"]+\\.vue[\'"]'), 'const ' + name + ' = globalThis.' + globalName)
  }
  code = code.replace(/import\s+(\w+)\s+from\s*['"][^'"]*\.vue['"]/g, 'const $1 = { render: () => null }')
  const tpl = compileTemplate({ source: descriptor.template.content, filename: absPath, id, ssr: true, ssrCssVars: [], compilerOptions: { bindingMetadata: script.bindings } })
  assert.deepEqual(tpl.errors, [])
  code = code.replace('export default ', 'const Component = ') + '\n' + tpl.code + '\nComponent.ssrRender=ssrRender; export default Component;'
  // 注意：重写 from '…' 必须在拼入 SSR 模板代码之后；相对导入补 .ts，包名按 frontend 依赖解析。
  code = code.replace(/from (['"])([^'"]+)\1/g, (_, q, spec) => {
    const target = spec.startsWith('.') ? resolve(dirname(absPath), spec + '.ts') : require.resolve(spec)
    return 'from ' + JSON.stringify(pathToFileURL(target).href)
  })
  const file = join(root, id + '.mjs')
  writeFileSync(file, ts.transpileModule(code, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText)
  return (await import(pathToFileURL(file).href)).default
}

// 先编译真实面板，再编译 PropertySources（其 AssistPanel 导入指向真实面板）。
const Panel = await loadSFC(resolve('frontend/src/assist/AssistPanel.vue'), 'assist_panel_real_ps')
globalThis.__assistPanelCompPs = Panel
const Component = await loadSFC(resolve('frontend/src/project/PropertySources.vue'), 'prop_sources_assist', { AssistPanel: '__assistPanelCompPs' })

// ── 捕获每次挂载的 setup 状态与表单变更事件（emit('changed'|'before-change')）────────
let activeSlot = null
let emitSpy = { changed: 0, beforeChange: 0 }
const origSetup = Component.setup
Component.setup = (props, ctx) => {
  const realEmit = ctx.emit
  const wrapped = { ...ctx, emit: (name, ...rest) => { if (name === 'changed') emitSpy.changed++; if (name === 'before-change') emitSpy.beforeChange++; return realEmit(name, ...rest) } }
  const api = origSetup(props, wrapped)
  if (activeSlot) activeSlot.api = api
  return api
}
const origPanelSetup = Panel.setup
Panel.setup = (props, ctx) => { const s = origPanelSetup(props, ctx); if (activeSlot) activeSlot.panel = s; return s }

// ── 模块与契约 ───────────────────────────────────────────────────────────────
const {
  propertySourceBinding, propertySourceAssistKind,
  PROPERTY_SOURCE_KIND_REASON, propertySourceFieldLabel,
} = await import('../frontend/src/assist/propertySourceBinding.ts')
const { useAssistPanel, ASSIST_ENTRY_ENABLED } = await import('../frontend/src/assist/useAssistPanel.ts')
const { applyOperations } = await import('../frontend/src/assist/formAutofill.ts')
const { FORM_PROPERTY_SOURCE } = await import('../frontend/src/assist/formContracts.gen.ts')

const clone = (x) => JSON.parse(JSON.stringify(x))
const sortedKeys = (o) => Object.keys(o).sort()

/** autofill/1 fill 响应（真实契约 digest：前端 contractInfo 比对通过） */
const fillResp = (over = {}) => ({
  protocol: 'autofill/1', status: 'ok', requestId: 'srv-t7', formId: 'propertySource',
  schemaVersion: FORM_PROPERTY_SOURCE.schemaVersion, schemaDigest: FORM_PROPERTY_SOURCE.schemaDigest,
  target: { space: 'project', targetKind: 'propertySource', targetId: '' },
  draftFingerprint: 'dfp-t7', contextFingerprint: 'cfp-t7',
  sessionId: 's_1', roundId: 'r_1',
  operations: [], questions: [], unresolved: [], summary: '',
  ...over,
})
const setOp = (field, value, quote = '填写') => ({ op: 'set', field, value, basis: { kind: 'intent', quote } })
const clearOp = (field, quote = '清空') => ({ op: 'clear', field, basis: { kind: 'intent', quote } })
const rowUpdate = (field, rowId, fields) => ({ op: 'row.update', field, rowId, fields })
const rowAppend = (field, localId, fields) => ({ op: 'row.append', field, row: { localId, fields } })
const rowRemove = (field, rowId) => ({ op: 'row.remove', field, rowId, basis: { kind: 'intent', quote: '删除' } })

// ── assist-api 桩：合成 context；generate 按队列出栈（记录请求体）────────────────
function makeAssistApi() {
  const calls = { context: [], generate: [] }
  const genQueue = []
  return {
    calls, genQueue,
    async context(body) {
      calls.context.push(clone(body))
      return {
        contextToken: 'tok-' + calls.context.length, contextFingerprint: 'fp-' + calls.context.length,
        context: {
          targetKind: body.targetKind, title: 'CTX[' + body.targetKind + ':' + body.targetId + ']',
          editableFields: [{ key: 'connection', label: '数据连接', kind: 'ref', required: true, options: null, group: null, help: '' }],
          definitions: [], catalog: [], flows: [], modelReady: true,
        },
      }
    },
    async generate(body) {
      calls.generate.push(clone(body))
      const step = genQueue.shift()
      if (step === undefined) throw new Error('测试桩未编排该请求')
      return typeof step === 'function' ? step(body) : step
    },
  }
}

/** 打开面板（真实状态机绑到宿主 binding），准备生成并返回面板。 */
async function drive(binding, stub, { intent = '填写' } = {}) {
  const panel = useAssistPanel(stub)
  await panel.open(binding)
  panel.intent.value = intent
  return panel
}

/** form-save 间谍：被调用即记录（回填/撤销后必须保持 0 次）。 */
function makeFormSaveSpy() {
  const saves = []
  return { saves, async submitForm(area, apply) { saves.push(area); apply(); return { ok: true, message: '' } } }
}

function fields(...names) { return names.map(name => ({ name, dataType: 'double', key: name === 'id' ? 'pri' : '', comment: '' })) }

function graphFixture() {
  return [
    { '@id': 'mg:cluster', '@type': 'owl:Class', 'rdfs:label': '储能簇' },
    ...[['power', 'xsd:double'], ['soc', 'xsd:double'], ['history', 'xsd:double', 'ts'], ['note', 'xsd:string']].map(([id, range, shape]) => ({
      '@id': 'mg:' + id, '@type': 'owl:DatatypeProperty', 'rdfs:domain': { '@id': 'mg:cluster' }, 'rdfs:range': { '@id': range }, 'rdfs:label': id, 'mg:apiName': id, ...(shape === 'ts' ? { 'mg:valueShape': 'timeSeries' } : {}),
    })),
  ]
}

function makeFixtures() {
  const b = reactive({ object_type: 'cluster', connection: 'db', table: 'clusters', primary_key: 'id', sources: [], properties: {}, relations: [] })
  const projectState = reactive({
    projectId: 'p1',
    connections: { connections: [{ id: 'db', name: '测试库', engine: 'mysql' }, { id: 'db2', name: '备用库', engine: 'mysql' }, { id: 'redis', name: '缓存', engine: 'redis' }] },
    bindings: {
      object_bindings: [b],
      catalogs: {
        db: { tables: [{ name: 'clusters', fields: fields('id', 'cluster_id', 'rated_power', 'capacity') }, { name: 'samples', fields: fields('cluster_id', 'soc', 'sampled_at') }] },
        db2: { tables: [{ name: 'clusters', fields: fields('id', 'cluster_id', 'rated_power', 'capacity') }] },
      },
    },
    implementations: [], parameters: { timezone: 'Asia/Shanghai' },
  })
  const refState = { ontology: { '@graph': graphFixture() }, workflow: { functions: [] } }
  return { b, projectState, refState }
}

function mountPropertySources() {
  const slot = { api: null, panel: null }
  const fx = makeFixtures()
  const formSave = makeFormSaveSpy()
  const assist = makeAssistApi()
  emitSpy = { changed: 0, beforeChange: 0 }
  const emits = emitSpy
  const app = createSSRApp(Component, { projectState: fx.projectState, refState: fx.refState, b: fx.b })
  app.provide('form-guard', { register() {}, unregister() {} })
  app.provide('form-save', formSave)
  app.provide('assist-api', assist)
  activeSlot = slot
  const initial = renderToString(app)
  return {
    ...fx, formSave, assist, initial, emits,
    get api() { return slot.api },
    get panel() { return slot.panel },
    async tick() { await nextTick(); await nextTick() },
    // 用同一份 setup 状态重渲染页面（不重跑 setup；面板子组件会重新实例化——需要面板状态时先重开）
    html: () => { activeSlot = slot; return renderToString(createSSRApp({ render: () => h({ props: Component.props, ssrRender: Component.ssrRender, setup: () => slot.api }, { projectState: fx.projectState, refState: fx.refState, b: fx.b }) })) },
  }
}

/** 用面板自身的 setup 状态独立渲染抽屉（SSR 下父级重渲染会新建实例、onMounted 不执行）。 */
const renderPanel = (ctx) => renderToString(createSSRApp({
  render: () => h({ props: Panel.props, ssrRender: Panel.ssrRender, setup: () => ctx.panel },
    { binding: ctx.api.assistBinding.value, api: ctx.assist, triggerId: 'ps-assist-trigger' }),
}))

const results = []
async function check(name, fn) { try { await fn(); results.push(true); console.log('通过：' + name) } catch (e) { results.push(false); console.log('失败：' + name + ' — ' + (e && e.message)) } }

try {
  // ── ① 工厂：契约变体快照 + contractInfo/codecs ──
  await check('① 工厂快照：四 kind 变体白名单键、行组 rowId、view-model 字段不出网、不可辅助空快照', async () => {
    for (const k of ['field', 'database', 'redis', 'flow']) assert.equal(propertySourceAssistKind({ kind: k }), k)
    for (const k of ['aggregate', 'computed', 'registered', 'none', 'unknown', undefined, null]) assert.equal(propertySourceAssistKind({ kind: k }), '')
    assert.equal(propertySourceAssistKind(null), '')

    // field：只有 field+note，mode/connection/table 等 view-model 字段不出网
    const fd = { kind: 'field', mode: 'identity', source: '', field: 'rated_power', connection: 'db', table: 'clusters' }
    const fb = propertySourceBinding({ draft: () => fd, noteDraft: () => '说明N', setNote: () => {} })
    assert.equal(fb.space, 'project'); assert.equal(fb.targetKind, 'propertySource'); assert.equal(fb.formId, 'propertySource')
    assert.deepEqual(fb.draft(), { kind: 'field', field: 'rated_power', note: '说明N' })
    assert.equal(fb.contractInfo().schemaVersion, FORM_PROPERTY_SOURCE.schemaVersion)
    assert.equal(fb.contractInfo().schemaDigest, FORM_PROPERTY_SOURCE.schemaDigest)

    // database：连接/表/result.*/lookup.match（行组带 rowId，行内路径展平）；timeRange 等白名单外不出网
    const dd = {
      kind: 'database', mode: 'direct', connection: 'db', table: 'samples',
      lookup: { match: [{ field: 'cluster_id', operator: 'eq', value: { kind: 'identityKey' } }], timeRange: { field: 'sampled_at' } },
      result: { valueField: 'soc', timestampField: 'sampled_at', timestampEncoding: 'datetime', timezone: 'X', selection: '', missing: 'null' },
    }
    const db = propertySourceBinding({ draft: () => dd, noteDraft: () => '说明N', setNote: () => {} })
    assert.deepEqual(sortedKeys(db.draft()), ['connection', 'kind', 'lookup.match', 'note', 'result.timestampField', 'result.valueField', 'table'])
    assert.deepEqual(db.draft()['lookup.match'], [{ rowId: 'm1', field: 'cluster_id', operator: 'eq', 'value.kind': 'identityKey' }])
    assert.ok(!JSON.stringify(db.draft()).includes('timeRange'), 'lookup.timeRange 不在白名单')
    assert.ok(!('mode' in db.draft()) && !('result' in db.draft()) && !('lookup' in db.draft()), 'view-model/非白名单键不出网')
    const snapMatch = db.draft()['lookup.match']
    dd.lookup.match.push({ field: 'hack', operator: 'eq', value: { kind: 'identityKey' } })
    assert.equal(snapMatch.length, 1, '行组快照是克隆，不随草稿后续修改漂移')

    // redis：来源形态还原为纯 id，连接形态原样；params 行组带 token 与 rowId；缺省键给展示默认
    const rd = { kind: 'redis', source: 'srcA', connection: '', command: 'HGET', key: 'm-{id}', hashField: 'soc', params: { id: { from: 'primary' } }, conversion: 'number', missing: 'error' }
    const rb = propertySourceBinding({ draft: () => rd, noteDraft: () => '说明N', setNote: () => {} })
    assert.deepEqual(sortedKeys(rb.draft()), ['command', 'connection', 'conversion', 'hashField', 'key', 'kind', 'missing', 'note', 'params'])
    assert.equal(rb.draft().connection, 'srcA', '来源形态快照成来源纯 id（无 src: 前缀）')
    assert.deepEqual(rb.draft().params, [{ rowId: 'k1', token: 'id', from: 'primary' }])
    rd.source = ''; rd.connection = 'redis1'
    assert.equal(rb.draft().connection, 'redis1', '连接形态快照成连接纯 id')
    const bare = { kind: 'redis', source: '', connection: '', command: '', key: '', hashField: '', params: {}, conversion: '', missing: '' }
    const bb = propertySourceBinding({ draft: () => bare, noteDraft: () => '', setNote: () => {} })
    assert.deepEqual([bb.draft().command, bb.draft().conversion, bb.draft().missing], ['GET', 'number', 'null'], '缺省键按表单展示默认出快照')

    // flow：flow/output/inputs/result.*
    const ld = { kind: 'flow', flow: 'f1', output: 'o1', inputs: { in1: { from: 'property', property: 'soc' } }, result: { valueField: 'v1', timestampField: 't1' } }
    const lb = propertySourceBinding({ draft: () => ld, noteDraft: () => '说明N', setNote: () => {} })
    assert.deepEqual(sortedKeys(lb.draft()), ['flow', 'inputs', 'kind', 'note', 'output', 'result.timestampField', 'result.valueField'])
    assert.deepEqual(lb.draft().inputs, [{ rowId: 'i1', inputId: 'in1', from: 'property', property: 'soc' }])

    // 不可辅助/空草稿：空快照、不抛错
    const nb = propertySourceBinding({ draft: () => ({ kind: 'none' }), noteDraft: () => '', setNote: () => {} })
    assert.deepEqual(nb.draft(), {})
    const gb = propertySourceBinding({ draft: () => null, noteDraft: () => '', setNote: () => {} })
    assert.deepEqual(gb.draft(), {}); gb.apply({ field: 'x' }); gb.restore({})
    assert.ok(true, '空草稿 apply/restore 安全')

    // codecs 注册：三个行组以「契约字段路径 + 列表 id」双键注册；契约 codecs 名与行组一一对应
    assert.deepEqual(sortedKeys(lb.codecs), [
      'command', 'connection', 'conversion', 'field', 'flow', 'flowInputs', 'hashField', 'inputs', 'key',
      'keyParams', 'kind', 'lookup.match', 'lookupMatch', 'missing', 'note', 'output', 'params',
      'result.timestampField', 'result.valueField', 'table',
    ], '契约字段（含 kind）与行组双键都注册 codec：kind 边界与扁平键在写副本前统一收口')
    const contractLeaves = new Set()
    for (const variant of Object.values(FORM_PROPERTY_SOURCE.variants)) {
      for (const f of variant.fields) contractLeaves.add(f.id)
      for (const l of variant.lists) contractLeaves.add(l.id)
    }
    for (const key of contractLeaves) assert.ok(lb.codecs[key], '契约键 ' + key + ' 已注册 codec')
    assert.deepEqual(sortedKeys(FORM_PROPERTY_SOURCE.variants).sort(), ['database', 'field', 'flow', 'redis'], '契约四变体与四个可辅助 kind 一致')
    assert.deepEqual((FORM_PROPERTY_SOURCE.codecs ?? []).slice().sort(), ['flowInputBindings', 'lookupMatchRows', 'redisKeyParams'], '契约 codecs 名与三个行组一一对应')
    const listIds = Object.values(FORM_PROPERTY_SOURCE.variants ?? {}).flatMap(v => (v.lists ?? []).map(l => l.id)).sort()
    assert.deepEqual([...new Set(listIds)], ['flowInputs', 'keyParams', 'lookupMatch'], '行组列表 id 取自契约 lists')
  })

  // ── ② 行组 codec：局部行更新 / 未知行拒绝 / 未知键保留 / 整组替换拒绝 / kind 边界 ──
  await check('② 行组 codec：按 rowId 局部更新、未知历史行与未知键保留、未知行/整组替换拒绝', async () => {
    const dd = {
      kind: 'database', connection: 'db', table: 'samples',
      lookup: {
        match: [
          { field: 'cluster_id', operator: 'eq', value: { kind: 'identityKey' } },
          { field: 'park', operator: 'eq', value: { kind: 'constant', value: 'P001' }, legacyNote: '历史行未知键' },
        ],
        timeRange: { field: 'sampled_at' },
      },
      result: { valueField: 'soc', timestampField: 'sampled_at' },
    }
    const db = propertySourceBinding({ draft: () => dd, noteDraft: () => '', setNote: () => {} })
    const rows = db.draft()['lookup.match']
    assert.deepEqual(rows.map(r => r.rowId), ['m1', 'm2'], '行 rowId 按草稿顺序稳定分配')

    // 副本上执行并按契约字段路径读回（codec 把行组写在「lookup.match」扁平键上）
    const runOn = (ops) => { const copy = db.draft(); const result = applyOperations(copy, ops, db.codecs); return { result, copy } }
    // 局部更新第 2 行（列表 id 与契约字段路径两个键都可用；行操作 field 用列表 id）
    const moved = runOn([rowUpdate('lookupMatch', 'm2', { 'value.kind': 'parameter', 'value.parameter': 'park_id' })])
    assert.equal(moved.result.failures.length, 0, JSON.stringify(moved.result.failures))
    const after = moved.copy['lookup.match']
    assert.deepEqual(after.map(r => r.rowId), ['m1', 'm2'], '未涉及行保留')
    assert.deepEqual(after[0], { rowId: 'm1', field: 'cluster_id', operator: 'eq', 'value.kind': 'identityKey' }, '未涉及行内容不变')
    assert.equal(after[1]['value.kind'], 'parameter'); assert.equal(after[1]['value.parameter'], 'park_id')
    assert.equal(after[1]['value.value'], 'P001', '未提及的行字段保留（不整行重写）')
    assert.ok(!('legacyNote' in moved.copy['lookup.match'][1]), '行内未知键不出网（白名单快照只含契约路径）')
    // 落回宿主草稿：未知键与未涉及行零丢失（applyDraft 只改行字段 diff）
    const dbLand = propertySourceBinding({ draft: () => dd, noteDraft: () => '', setNote: () => {} })
    dbLand.applyDraft(moved.copy)
    assert.equal(dd.lookup.match[1].legacyNote, '历史行未知键', '行内未知键在宿主草稿零丢失')
    assert.deepEqual(dd.lookup.match.map(r => r.field), ['cluster_id', 'park'], '未涉及行保留')
    assert.equal(dd.lookup.match[1].legacyNote, '历史行未知键', '行内未知键保留')
    assert.equal(dd.lookup.match[1].operator, 'eq', '未提及的行字段保留')
    assert.ok(!('value' in dd.lookup.match[1]) || !('value' in dd.lookup.match[1].value ?? {}) || dd.lookup.match[1].value.value === undefined,
      '取值方式改为 parameter 后旧常量分支字段被清理（同组内不留旧分支残留）')
    assert.equal(dd.lookup.match[1].value.parameter, 'park_id', '新分支字段落位')
    assert.deepEqual(dd.lookup.timeRange, { field: 'sampled_at' }, '同组白名单外结构保留')
    const byPathKey = runOn([rowUpdate('lookup.match', 'm1', { operator: 'eq' })])
    assert.equal(byPathKey.result.failures.length, 0, '契约字段路径键同样命中 codec')

    // 新增行（row.append）与指定行删除（row.remove）
    const appended = runOn([rowAppend('lookupMatch', 'r1', { field: 'site', operator: 'eq', 'value.kind': 'constant', 'value.value': 'S1' })])
    const rows3 = appended.copy['lookup.match']
    assert.equal(rows3.length, 3)
    assert.deepEqual(rows3[2], { rowId: 'r1', field: 'site', operator: 'eq', 'value.kind': 'constant', 'value.value': 'S1' })
    const removed = runOn([rowRemove('lookupMatch', 'm1')])
    assert.deepEqual(removed.copy['lookup.match'].map(r => r.rowId), ['m2'])
    // 未知 rowId / 整组替换：拒绝且不产生写入
    const unknownRow = runOn([rowUpdate('lookupMatch', 'm9', { field: 'x' })])
    assert.equal(unknownRow.result.failures.length, 1); assert.match(unknownRow.result.failures[0].reason, /未找到行/)
    assert.deepEqual(unknownRow.copy['lookup.match'], db.draft()['lookup.match'], '被拒行操作不改副本')
    const wholeReplace = runOn([{ op: 'set', field: 'lookup.match', value: [{ field: 'ai', operator: 'eq', 'value.kind': 'identityKey' }] }])
    assert.equal(wholeReplace.result.failures.length, 1); assert.match(wholeReplace.result.failures[0].reason, /只能通过 row\.(append|update|remove)/)
    assert.equal(wholeReplace.result.records.length, 0, '被拒操作不产生记录')

    // kind 边界：database 草稿上的 redis params 行组 codec 拒绝（不隐式切换）
    const rb = propertySourceBinding({ draft: () => ({ kind: 'database', connection: 'db', table: 't', lookup: { match: [] }, result: {} }), noteDraft: () => '', setNote: () => {} })
    const rr = applyOperations(rb.draft(), [rowAppend('keyParams', 'k1', { token: 'id', from: 'primary' })], rb.codecs)
    assert.equal(rr.failures.length, 1); assert.equal(rr.failures[0].reason, PROPERTY_SOURCE_KIND_REASON)
  })

  // ── ③ database：正常链路 / A05 改字段名 / A08 依赖变化 / 无半组 ──
  await check('③ database 正常链路：连接/表/取值字段/匹配行/说明一次回填（无勾选步骤）', async () => {
    const dd = { kind: 'database', connection: 'db', table: 'clusters', lookup: { match: [], timeRange: null }, result: { valueField: '', timestampField: '' } }
    let note = ''
    const binding = propertySourceBinding({ draft: () => dd, noteDraft: () => note, setNote: v => { note = v } })
    const stub = makeAssistApi()
    stub.genQueue.push(fillResp({ operations: [
      setOp('table', 'samples', '用 samples 表取样'), setOp('result.valueField', 'soc', '取 soc 字段'),
      rowAppend('lookupMatch', 'r1', { field: 'cluster_id', operator: 'eq', 'value.kind': 'identityKey' }),
      setOp('note', '来自采样表的 SOC 观测值。', '加一句说明'),
    ] }))
    const panel = await drive(binding, stub, { intent: '用 samples 表取样，取 soc 字段' })
    await panel.generate()
    assert.equal(dd.connection, 'db'); assert.equal(dd.table, 'samples')
    assert.equal(dd.result.valueField, 'soc')
    assert.deepEqual(dd.lookup.match, [{ field: 'cluster_id', operator: 'eq', value: { kind: 'identityKey' } }], '新增匹配行落位')
    assert.equal(note, '来自采样表的 SOC 观测值。', '说明经 setNote 写进说明草稿')
    assert.equal(binding.refusals.value.length, 0, JSON.stringify(binding.refusals.value))
    assert.match(binding.round.statusBarText, /^已填写 \d+ 项，尚未保存/)
    assert.equal(binding.round.statusBarText, panel.statusBarText.value, '宿主镜像与引擎状态条口径一致')
    assert.ok(binding.round.changes.some(c => c.field === 'result.valueField'), JSON.stringify(binding.round.changes))
    assert.equal(typeof panel.adopt, 'undefined', '旧「采纳」入口已移除')
    assert.equal(typeof panel.checked, 'undefined', '旧勾选状态已移除')
    assert.equal(panel.status.value, 'done'); assert.equal(panel.collapsed.value, true, '无待补：抽屉收起')
  })

  await check('③b A05：明确改字段名 rated_power→capacity，其余配置逐字保留', async () => {
    const dd = {
      kind: 'database', connection: 'db', table: 'clusters',
      lookup: { match: [{ field: 'cluster_id', operator: 'eq', value: { kind: 'identityField', field: 'dev_id' } }], timeRange: { field: 'sampled_at' } },
      result: { valueField: 'rated_power', timestampField: '' },
    }
    const before = clone(dd)
    const binding = propertySourceBinding({ draft: () => dd, noteDraft: () => '', setNote: () => {} })
    const stub = makeAssistApi()
    stub.genQueue.push(fillResp({ operations: [setOp('result.valueField', 'capacity', '把取值字段改成 capacity')] }))
    const panel = await drive(binding, stub, { intent: '把取值字段改成 capacity' })
    await panel.generate()
    assert.equal(dd.result.valueField, 'capacity', '明确要求的字段替换生效')
    assert.equal(dd.result.timestampField, before.result.timestampField, '未提及的时间字段保留')
    assert.deepEqual(dd.lookup.match, before.lookup.match, '未提及的匹配条件逐字保留')
    assert.deepEqual(dd.lookup.timeRange, before.lookup.timeRange, '白名单外同组结构零改动')
    assert.equal(dd.connection, before.connection); assert.equal(dd.table, before.table)
    assert.equal(binding.refusals.value.length, 0, JSON.stringify(binding.refusals.value))
    assert.ok(binding.round.changes.some(c => c.field === 'result.valueField' && c.newText === 'capacity'), JSON.stringify(binding.round.changes))
  })

  await check('③c A08：改连接/改表留下旧表旧字段时不静默丢弃——未同组给全则整组不落并说明', async () => {
    const dd = {
      kind: 'database', connection: 'db', table: 'clusters',
      lookup: { match: [{ field: 'cluster_id', operator: 'eq', value: { kind: 'identityKey' } }], timeRange: null },
      result: { valueField: 'rated_power', timestampField: '' },
    }
    const before = clone(dd)
    const binding = propertySourceBinding({ draft: () => dd, noteDraft: () => '', setNote: () => {} })
    const stub = makeAssistApi()
    stub.genQueue.push(fillResp({ operations: [setOp('connection', 'db2', '改用备用库'), setOp('table', 'samples', '改用 samples 表')] }))
    const panel = await drive(binding, stub, { intent: '改用备用库的 samples 表' })
    await panel.generate()
    assert.deepEqual(dd, before, '依赖变化未同组给全：连接/表整体不落（草稿零改动）')
    assert.ok(binding.refusals.value.some(r => /取值字段|匹配条件/.test(r.reason)), JSON.stringify(binding.refusals.value))
    assert.equal(binding.round.appliedCount, 0, '未产生写入：已填写计数为 0')
  })

  await check('③c2 A08 续：同组合法更新（连接/表＋取值字段＋匹配条件一次给全）', async () => {
    const dd = {
      kind: 'database', connection: 'db', table: 'clusters',
      lookup: { match: [{ field: 'cluster_id', operator: 'eq', value: { kind: 'identityKey' } }], timeRange: null },
      result: { valueField: 'rated_power', timestampField: '' },
    }
    const binding = propertySourceBinding({ draft: () => dd, noteDraft: () => '', setNote: () => {} })
    const stub = makeAssistApi()
    stub.genQueue.push(fillResp({ operations: [
      setOp('connection', 'db2', '改用备用库'), setOp('table', 'samples', '改用 samples 表'),
      setOp('result.valueField', 'soc', '取值字段改为 soc'),
      rowUpdate('lookupMatch', 'm1', { field: 'site_id', 'value.kind': 'identityKey' }),
    ] }))
    const panel = await drive(binding, stub, { intent: '改用备用库的 samples 表，取值字段改为 soc，匹配条件改用 site_id' })
    await panel.generate()
    assert.equal(dd.connection, 'db2'); assert.equal(dd.table, 'samples'); assert.equal(dd.result.valueField, 'soc')
    assert.deepEqual(dd.lookup.match, [{ field: 'site_id', operator: 'eq', value: { kind: 'identityKey' } }], '匹配条件按行落位（无旧表旧字段残留）')
    assert.equal(binding.refusals.value.length, 0, JSON.stringify(binding.refusals.value))
  })

  await check('③d 无半组：database 组缺 table 整组不落；新增行缺必填项不落半行', async () => {
    const dd = { kind: 'database', connection: '', table: '', lookup: { match: [], timeRange: null }, result: { valueField: '', timestampField: '' } }
    const binding = propertySourceBinding({ draft: () => dd, noteDraft: () => '', setNote: () => {} })
    const stub = makeAssistApi()
    stub.genQueue.push(fillResp({ operations: [
      setOp('result.valueField', 'soc', '取值字段 soc'),
      rowAppend('lookupMatch', 'r1', { field: 'cluster_id', operator: 'eq', 'value.kind': 'identityKey' }),
    ] }))
    const panel = await drive(binding, stub, { intent: '取值字段 soc' })
    await panel.generate()
    assert.equal(dd.result.valueField, '', '缺表：取值字段不落（无半组）')
    assert.deepEqual(dd.lookup.match, [], '缺表：匹配条件行不落（无半组）')
    assert.equal(dd.table, ''); assert.equal(dd.connection, '')
    assert.deepEqual(binding.refusals.value.map(r => r.field).sort(), ['lookup.match', 'result.valueField'])
    // 新增行缺必填项：该行不落（无半行），既有行不受影响
    const dd2 = { kind: 'database', connection: 'db', table: 'samples', lookup: { match: [{ field: 'cluster_id', operator: 'eq', value: { kind: 'identityKey' } }], timeRange: null }, result: { valueField: '', timestampField: '' } }
    const b2 = propertySourceBinding({ draft: () => dd2, noteDraft: () => '', setNote: () => {} })
    const stub2 = makeAssistApi()
    stub2.genQueue.push(fillResp({ operations: [rowAppend('lookupMatch', 'r1', { operator: 'eq', 'value.kind': 'identityKey' })] }))
    const p2 = await drive(b2, stub2, { intent: '加一条匹配条件' })
    await p2.generate()
    assert.equal(dd2.lookup.match.length, 1, '新增行缺匹配字段：不落半行，既有行保留')
    assert.ok(b2.refusals.value.some(r => /新增行缺少/.test(r.reason)), JSON.stringify(b2.refusals.value))
  })

  // ── ④ redis 链路 ──
  await check('④ redis 链路：command/key/hashField/params 局部行更新、未知历史行保留、来源形态连接', async () => {
    const rd = {
      kind: 'redis', source: '', connection: 'redis', command: 'GET', key: 'soc-{id}',
      params: { id: { from: 'primary' }, dev: { from: 'identityField', field: 'dev_id' }, legacy: { from: 'property', property: 'soc', unknown: 'x' } },
      hashField: '', conversion: 'number', missing: 'null',
    }
    const binding = propertySourceBinding({
      draft: () => rd, noteDraft: () => '', setNote: () => {},
      setRedisTarget: v => { if (v.startsWith('conn:')) { rd.connection = v.slice(5); rd.source = '' } else if (v.startsWith('src:')) { rd.source = v.slice(4); rd.connection = '' } },
      redisSourceIds: () => ['srcX'],
    })
    const stub = makeAssistApi()
    stub.genQueue.push(fillResp({ operations: [
      setOp('command', 'HGET', '按哈希读取'), setOp('key', 'cluster:{id}:soc', 'Key 模板用 cluster:{id}:soc'),
      setOp('hashField', 'soc_field', 'Hash 字段用 soc_field'),
      rowUpdate('keyParams', 'k2', { 'from': 'property', property: 'power' }),
      rowAppend('keyParams', 'r1', { token: 'site', from: 'identityField', field: 'site_id' }),
    ] }))
    const panel = await drive(binding, stub, { intent: '按哈希读取 cluster:{id}:soc' })
    await panel.generate()
    assert.equal(rd.command, 'HGET'); assert.equal(rd.key, 'cluster:{id}:soc'); assert.equal(rd.hashField, 'soc_field')
    assert.deepEqual(rd.params.id, { from: 'primary' }, '未涉及行逐字保留')
    assert.deepEqual(rd.params.dev, { from: 'property', property: 'power' }, '指定行按 rowId 局部更新（换来源时旧字段名清除）')
    assert.deepEqual(rd.params.legacy, { from: 'property', property: 'soc', unknown: 'x' }, '未知历史行（含未知键）保留')
    assert.deepEqual(rd.params.site, { from: 'identityField', field: 'site_id' }, '新增行落位')
    assert.equal(binding.refusals.value.length, 0, JSON.stringify(binding.refusals.value))

    // 来源形态：建议连接值命中已登记来源 → src: 形态写回（不进协议的前缀只在表单交互层）
    const stub2 = makeAssistApi()
    stub2.genQueue.push(fillResp({ operations: [setOp('connection', 'srcX', '用已登记来源')] }))
    const p2 = await drive(binding, stub2, { intent: '用已登记来源' })
    await p2.generate()
    assert.equal(rd.source, 'srcX'); assert.equal(rd.connection, '', '来源形态下连接清空')
  })

  await check('④b redis 依赖链：hashField 仅 HGET 可用、params 依赖 Key 模板', async () => {
    const rd = { kind: 'redis', source: '', connection: 'redis', command: 'GET', key: 'k', hashField: '', params: {}, conversion: 'number', missing: 'null' }
    const binding = propertySourceBinding({ draft: () => rd, noteDraft: () => '', setNote: () => {} })
    const stub = makeAssistApi()
    stub.genQueue.push(fillResp({ operations: [setOp('hashField', 'h', '哈希字段 h')] }))
    const panel = await drive(binding, stub, { intent: '哈希字段 h' })
    await panel.generate()
    assert.equal(rd.hashField, '', 'GET 下不写 Hash 字段')
    assert.match(binding.refusals.value.map(r => r.reason).join(' '), /HGET/)
    // 无 Key 模板：参数行不落（依赖链缺上游）
    const rdKeyless = { kind: 'redis', source: '', connection: 'redis', command: 'GET', key: '', hashField: '', params: {}, conversion: 'number', missing: 'null' }
    const bKeyless = propertySourceBinding({ draft: () => rdKeyless, noteDraft: () => '', setNote: () => {} })
    const stubKeyless = makeAssistApi()
    stubKeyless.genQueue.push(fillResp({ operations: [rowAppend('keyParams', 'r1', { token: 'id', from: 'primary' })] }))
    const pKeyless = await drive(bKeyless, stubKeyless, { intent: '绑定 id 占位符' })
    await pKeyless.generate()
    assert.deepEqual(rdKeyless.params, {}, '缺 Key 模板：参数行不落')
    assert.match(bKeyless.refusals.value.map(r => r.reason).join(' '), /Key 模板/)
    // HGET 下 hashField 放行；key 已有模板时 params 放行
    const rd2 = { kind: 'redis', source: '', connection: 'redis', command: 'HGET', key: 'm-{id}', hashField: '', params: {}, conversion: 'number', missing: 'null' }
    const b2 = propertySourceBinding({ draft: () => rd2, noteDraft: () => '', setNote: () => {} })
    const stub2 = makeAssistApi()
    stub2.genQueue.push(fillResp({ operations: [
      setOp('hashField', 'soc_field', '哈希字段'), rowAppend('keyParams', 'r1', { token: 'id', from: 'primary' }),
      clearOp('conversion', '清空结果转换'),
    ] }))
    const p2 = await drive(b2, stub2, { intent: '哈希字段 soc_field' })
    await p2.generate()
    assert.equal(rd2.hashField, 'soc_field')
    assert.deepEqual(rd2.params, { id: { from: 'primary' } })
    assert.equal(b2.refusals.value.length, 0, JSON.stringify(b2.refusals.value))
  })

  // ── ⑤ flow 链路 ──
  await check('⑤ flow 链路：输出/输入绑定行组/结果字段；缺编排不落；不触发编排详情拉取', async () => {
    const ld = { kind: 'flow', flow: 'f1', output: 'o1', inputs: { in1: { from: 'property', property: 'power' }, legacy: { from: 'instanceId', extra: 'keep' } }, result: { valueField: '', timestampField: '' } }
    const fetchBefore = fetchCount
    const binding = propertySourceBinding({ draft: () => ld, noteDraft: () => '', setNote: () => {} })
    const stub = makeAssistApi()
    stub.genQueue.push(fillResp({ operations: [
      rowUpdate('flowInputs', 'i1', { 'from': 'constant', value: 'P001' }),
      rowAppend('flowInputs', 'r1', { inputId: 'in2', 'from': 'instanceId' }),
      setOp('result.valueField', 'v1', '取值字段 v1'), setOp('result.timestampField', 't1', '时间字段 t1'),
    ] }))
    const panel = await drive(binding, stub, { intent: '输入绑定并取 v1/t1' })
    await panel.generate()
    assert.equal(ld.flow, 'f1'); assert.equal(ld.output, 'o1', '未提及的编排/输出保留')
    assert.deepEqual(ld.inputs.in1, { from: 'constant', value: 'P001' }, '指定行走 row.update（旧来源字段被替换）')
    assert.deepEqual(ld.inputs.legacy, { from: 'instanceId', extra: 'keep' }, '未知历史行保留')
    assert.deepEqual(ld.inputs.in2, { from: 'instanceId' }, '新增输入绑定行落位')
    assert.equal(ld.result.valueField, 'v1'); assert.equal(ld.result.timestampField, 't1')
    assert.equal(binding.refusals.value.length, 0, JSON.stringify(binding.refusals.value))
    assert.equal(fetchCount, fetchBefore, '回填写 flow/output 不触发编排详情/目录等任何网络请求')
    // 依赖链：flow 未定时 output/inputs/result.* 不落（无半组）
    const ld2 = { kind: 'flow', flow: '', output: '', inputs: {}, result: { valueField: '', timestampField: '' } }
    const b2 = propertySourceBinding({ draft: () => ld2, noteDraft: () => '', setNote: () => {} })
    const stub2 = makeAssistApi()
    stub2.genQueue.push(fillResp({ operations: [
      setOp('output', 'o1', '输出 o1'), rowAppend('flowInputs', 'r1', { inputId: 'in1', from: 'instanceId' }), setOp('result.valueField', 'v1', '取值字段 v1'),
    ] }))
    const p2 = await drive(b2, stub2, { intent: '输出 o1' })
    await p2.generate()
    assert.equal(ld2.output, ''); assert.deepEqual(ld2.inputs, {}); assert.equal(ld2.result.valueField, '', '缺编排：输出/输入/取值字段均不落')
    assert.deepEqual(b2.refusals.value.map(r => r.field).sort(), ['inputs', 'output', 'result.valueField'])
  })

  // ── ⑥ kind 边界 ──
  await check('⑥ kind 边界：其他来源类型字段/kind 变更一律拒绝并给原因，既有配置零改动', async () => {
    const dd = { kind: 'database', connection: 'db', table: 'samples', lookup: { match: [{ field: 'cluster_id', operator: 'eq', value: { kind: 'identityKey' } }], timeRange: null }, result: { valueField: 'soc', timestampField: '' } }
    const before = clone(dd)
    let note = '原说明'
    const binding = propertySourceBinding({ draft: () => dd, noteDraft: () => note, setNote: v => { note = v } })
    const stub = makeAssistApi()
    stub.genQueue.push(fillResp({ operations: [
      setOp('kind', 'redis', '改成 Redis'), setOp('key', 'soc-{id}', 'Key 模板 soc-{id}'),
      setOp('command', 'GET', '用 GET'), setOp('connection', 'redis', '连接缓存'),
    ] }))
    const panel = await drive(binding, stub, { intent: '改成 Redis 取值' })
    await panel.generate()
    assert.deepEqual(dd, before, 'kind 边界：数据库配置零改动（不隐式切换、不清空、连接也不动）')
    assert.equal(note, '原说明', 'note 未出现在响应中：保持原样')
    assert.ok(binding.refusals.value.length >= 2, JSON.stringify(binding.refusals.value))
    assert.ok(binding.refusals.value.some(r => r.field === 'kind'), JSON.stringify(binding.refusals.value))
    assert.ok(binding.refusals.value.some(r => r.field === 'key' && r.reason === PROPERTY_SOURCE_KIND_REASON), JSON.stringify(binding.refusals.value))
    assert.ok(binding.refusals.value.some(r => r.field === 'command' && r.reason === PROPERTY_SOURCE_KIND_REASON), JSON.stringify(binding.refusals.value))
    assert.ok(binding.refusals.value.every(r => r.field !== 'connection' || r.reason === PROPERTY_SOURCE_KIND_REASON),
      '同名共享键（connection）也不落：本轮整体是另一种来源类型的请求')
    assert.equal(binding.round.appliedCount, 0, '被拒操作不计入已填写项数')
    assert.equal(binding.round.statusBarText, '', '宿主状态条不显示已填写（零实际写入）')
    // 边界说明：真实后端会把契约外的 kind/key/command 操作直接转 unresolved（不进 operations），
    // 这里模拟的是更严格的对抗输入。此时同名共享键（connection）会经 codec 进入引擎副本但不落宿主草稿，
    // 引擎自报的 appliedCount 可能偏乐观（引擎按副本顶层 diff 计数）；宿主渲染的状态条与撤销单元
    // 一律以 binding 镜像（recordApply）为准，页面不会出现无依据的成功提示。
    assert.equal(propertySourceFieldLabel('key'), 'Key 模板', '拒绝原因展示用契约标签')

    // 混入其他来源类型内容但带合法 note：说明属于四变体共有字段，照常落位（§4.3 只填独立合法组）
    const b2 = propertySourceBinding({ draft: () => dd, noteDraft: () => '原说明', setNote: () => {} })
    const stub2 = makeAssistApi()
    stub2.genQueue.push(fillResp({ operations: [setOp('key', 'x', 'Key'), setOp('note', '补充说明：本属性来自 SOC 采样。', '加说明')] }))
    const p2 = await drive(b2, stub2, { intent: '加说明并改成 Redis' })
    await p2.generate()
    assert.deepEqual(dd, before, '变体键仍未落位')
    assert.equal(b2.round.appliedCount, 0, '仅说明变化：不产生已填写计数')
  })

  // ── ⑦ 组件全链路 ──
  await check('⑦ 组件：列表态/none 无入口；field 草稿出现页头入口、aria 接线与面板', async () => {
    const ctx = mountPropertySources()
    await ctx.initial
    let page = await ctx.html()
    assert.ok(!page.includes('✦ 自动填写'), '列表态无自动填写入口')
    ctx.api.openEditor('power')
    page = await ctx.html()
    assert.match(page, /尚未配置取值来源/, '未配置属性进入 none 态')
    assert.ok(!page.includes('✦ 自动填写'), 'kind=none 无入口')
    ctx.api.draft.value = ctx.api.freshDbDraft()
    page = await ctx.html()
    if (ASSIST_ENTRY_ENABLED) {
      assert.match(page, /✦ 自动填写/, 'field 草稿出现入口（页头次要按钮）')
      assert.match(page, /id="ps-assist-trigger"/, '入口按钮 id 供面板 aria-controls 反向接线')
      assert.match(page, /aria-expanded="false"/, '未打开时 aria-expanded=false')
    } else {
      assert.ok(!page.includes('✦ 自动填写') && !page.includes('ps-assist-trigger'), '功能开关关闭：来源表单不渲染入口')
    }
    assert.ok(!page.includes('ps-assist-panel'), '未点击前不渲染面板')
    ctx.api.toggleAssist()
    page = await ctx.html()
    assert.ok(ctx.panel, '点击后面板实例已挂载（SSR 不跑 onMounted，新实例抽屉根默认收起）')
    assert.equal(ctx.api.assistVisible.value, true, '入口切换后面板可见标记为真')
    if (ASSIST_ENTRY_ENABLED) assert.match(page, /aria-expanded="true"/, '打开后 aria-expanded=true')
    ctx.panel.expand()
    const drawer = await renderPanel(ctx)
    assert.match(drawer, /assist-drawer/, '展开后抽屉渲染')
    assert.match(drawer, /配置「power」的取值来源/, '未取到上下文时展示目标兜底标题（contextTitle）')
    assert.match(drawer, /aria-controls="ps-assist-trigger"/, '抽屉经 trigger-id 与入口按钮反向接线')
    const hostSrc = readFileSync(resolve('frontend/src/project/PropertySources.vue'), 'utf8')
    // 2026-09-22 T10 F2 修复：宿主常驻挂载（输入保留）——可见性由 :open 驱动，binding 为挂载前提。
    assert.match(hostSrc, /<AssistPanel v-if="assistBinding" :open="assistVisible"[^>]*class="ps-assist-panel"[^>]*trigger-id="ps-assist-trigger"/, '面板常驻挂载点带样式类与 trigger-id（:open 受控）')
    assert.ok(!/建议卡|勾选|采纳/.test(page), '旧建议卡/勾选/采纳 UI 已移除')
  })

  await check('⑦b 上下文请求：project 空间 + propertySource 目标 + 契约形态快照（行组带 rowId）', async () => {
    const ctx = mountPropertySources()
    await ctx.initial
    ctx.api.openEditor('soc')
    ctx.api.draft.value = ctx.api.emptyDatabaseDraft()
    ctx.api.dbConnChanged('db')
    ctx.api.dbTableChanged('clusters')
    ctx.api.addMatch()
    ctx.api.toggleAssist()
    await ctx.html()
    const panel = await drive(ctx.api.assistBinding.value, ctx.assist)
    assert.equal(ctx.assist.calls.context.length, 1)
    const body = ctx.assist.calls.context[0]
    assert.equal(body.space, 'project'); assert.equal(body.projectId, 'p1')
    assert.equal(body.targetKind, 'propertySource'); assert.equal(body.targetId, 'cluster.soc')
    assert.equal(body.purpose, 'fill')
    assert.deepEqual(body.draft, {
      kind: 'database', connection: 'db', table: 'clusters', 'result.valueField': '', 'result.timestampField': '',
      'lookup.match': [{ rowId: 'm1', field: '', operator: 'eq', 'value.kind': 'identityKey' }], note: '',
    }, 'draft 是契约形态快照（行组带 rowId）')
    panel.close()
    assert.ok(!ctx.assist.calls.context.some(c => typeof c.draft?.['lookup'] !== 'undefined'), '上下文草稿不含白名单外结构')
    assert.equal(ctx.assist.calls.generate.length, 0, '未点击生成前不发 fill 请求')
  })

  await check('⑦c 回填→状态条→撤销：database 一次回填、保存零触达、整轮撤销恢复起点', async () => {
    const ctx = mountPropertySources()
    await ctx.initial
    ctx.api.openEditor('power')
    ctx.api.draft.value = ctx.api.emptyDatabaseDraft()
    ctx.api.dbConnChanged('db')
    ctx.api.dbTableChanged('clusters')
    ctx.api.toggleAssist()
    await ctx.html()
    const stub = makeAssistApi()
    stub.genQueue.push(fillResp({ operations: [setOp('result.valueField', 'rated_power', '取额定功率')] }))
    const panel = await drive(ctx.api.assistBinding.value, stub, { intent: '取额定功率' })
    await panel.generate()
    assert.equal(ctx.api.draft.value.result.valueField, 'rated_power')
    assert.equal(ctx.formSave.saves.length, 0, '回填绝不触发表单保存（form-save 零调用）')
    assert.equal(ctx.emits.changed, 0, '回填不触发 changed（不进入自动保存/脏状态链路）')
    assert.equal(ctx.emits.beforeChange, 0, '回填不触发 before-change（不产生撤销快照与冲突标记）')
    assert.deepEqual(ctx.b.properties.power, undefined, '回填不写已保存配置')
    const filledHtml = await ctx.html()
    assert.match(filledHtml, /已填写 \d+ 项，尚未保存/, '宿主状态条渲染 N 项文案')
    assert.match(filledHtml, /撤销本次填写/, '状态条含撤销本次填写')
    assert.match(filledHtml, /查看修改/, '状态条含查看修改')
    assert.match(filledHtml, /值字段|取值字段/, '查看修改列出字段标签（契约标签）')
    // 整轮撤销：恢复本轮起点
    ctx.api.undoAssist()
    assert.equal(ctx.api.draft.value.result.valueField, '', '撤销恢复本轮开始前草稿')
    assert.equal(ctx.formSave.saves.length, 0, '撤销不保存')
    const undoneHtml = await ctx.html()
    assert.match(undoneHtml, /已撤销本次自动填写/, '撤销后状态条说明')
    assert.ok(!/已填写 \d+ 项，尚未保存/.test(undoneHtml), '撤销后不再显示已填写文案')
    assert.equal(ctx.api.assistBinding.value.round.canUndo, false)

    // 回填后等待超过自动保存窗口：仍是本地草稿，未落盘
    const stub2 = makeAssistApi()
    stub2.genQueue.push(fillResp({ operations: [setOp('result.valueField', 'rated_power', '取额定功率')] }))
    const panel2 = await drive(ctx.api.assistBinding.value, stub2, { intent: '取额定功率' })
    await panel2.generate()
    await ctx.tick(); await new Promise(r => setTimeout(r, 950))
    assert.equal(ctx.formSave.saves.length, 0, '等待超过自动保存窗口：保存计数仍为 0')
    assert.equal(ctx.emits.changed, 0)
    assert.deepEqual(ctx.b.properties.power, undefined)

    // 回填后显式保存：走既有校验与 form-save 落库，状态条结束
    await ctx.api.saveDraft()
    assert.equal(ctx.formSave.saves.length, 1, '显式保存才落盘')
    assert.equal(ctx.b.properties.power, 'rated_power', '回填值经既有保存编码落库')
  })

  await check('⑦d 手改禁整轮撤销（不覆盖用户改动）＋ 手改/kind 失效接线', async () => {
    const ctx = mountPropertySources()
    await ctx.initial
    ctx.api.openEditor('power')
    ctx.api.draft.value = ctx.api.emptyDatabaseDraft()
    ctx.api.dbConnChanged('db'); ctx.api.dbTableChanged('clusters')
    ctx.api.toggleAssist()
    await ctx.html()
    const stub = makeAssistApi()
    stub.genQueue.push(fillResp({ operations: [setOp('result.valueField', 'rated_power', '取额定功率')] }))
    const panel = await drive(ctx.api.assistBinding.value, stub, { intent: '取额定功率' })
    await panel.generate()
    assert.equal(ctx.api.assistBinding.value.round.canUndo, true, '回填后可整轮撤销')
    // 手改：走组件同一入口（SSR harness 不跑 watch；浏览器实链路即 watch 调用同一函数）
    ctx.api.draft.value.result.valueField = '手改字段'
    ctx.api.assistTouched()
    assert.equal(ctx.api.assistBinding.value.round.canUndo, false, '手改后禁整轮撤销')
    assert.match(ctx.api.assistBinding.value.round.undoHint, /已保留你的手动修改/)
    ctx.api.undoAssist()
    assert.equal(ctx.api.draft.value.result.valueField, '手改字段', '被拒撤销不得覆盖用户改动')
    const page = await ctx.html()
    assert.match(page, /已保留你的手动修改/, '状态条说明手改后不可直接撤销')

    // 源码接线锁定（SSR 下 watch 不触发）：手改 → binding 登记 + 面板通知；kind 失效收起
    const src = readFileSync(resolve('frontend/src/project/PropertySources.vue'), 'utf8')
    assert.match(src, /watch\(assistFingerprint/, '草稿/说明指纹 watch 已接线')
    assert.match(src, /assistBinding\.value\?\.noteManualChange\(\)/, '手改登记经 binding 回传（禁整轮撤销）')
    assert.match(src, /assistPanelRef\.value\?\.notifyDraftChanged\(\)/, '手改通知经面板模板 ref（作废在途请求）')
    assert.match(src, /watch\(assistAvailable,ok=>\{if\(!ok\)closeAssist\(\)\}\)/, 'kind 失效收起 watch 已接线')
    const assistRegion = src.slice(src.indexOf('整表自动填写（T7'), src.indexOf('// ---------- 计算函数'))
      .split('\n').filter(line => !line.trim().startsWith('//')).join('\n')
    assert.ok(!/formSave|commitProperty|mutate\(|commit-now/.test(assistRegion), '回填写回区没有任何保存/变更触达（不含 form-save/commit-now/mutate）')
    assert.ok(!/emit\(/.test(assistRegion), '回填写回区不 emit 任何表单事件（changed/before-change 不触发）')
  })

  await check('⑦e 拒绝原因在状态条展示（kind 边界经真实组件链路）', async () => {
    const ctx = mountPropertySources()
    await ctx.initial
    ctx.api.openEditor('power')
    ctx.api.draft.value = ctx.api.emptyDatabaseDraft()
    ctx.api.dbConnChanged('db'); ctx.api.dbTableChanged('clusters')
    ctx.api.draft.value.result.valueField = 'rated_power'
    ctx.api.toggleAssist()
    await ctx.html()
    const stub = makeAssistApi()
    stub.genQueue.push(fillResp({ operations: [setOp('key', 'soc-{id}', 'Key 模板 soc-{id}')] }))
    const panel = await drive(ctx.api.assistBinding.value, stub, { intent: '改成 Redis 取值' })
    await panel.generate()
    assert.equal(ctx.api.draft.value.result.valueField, 'rated_power', '既有配置零改动')
    const page = await ctx.html()
    assert.match(page, /assist-refusals/, '拒绝原因区渲染')
    assert.match(page, /请先切换来源类型/, '拒绝原因含「请先切换来源类型」')
    assert.match(page, /Key 模板/, '拒绝原因用契约标签定位字段')
  })

  await check('⑦f 无入口与收起：aggregate/computed 无入口；目标切换与关闭收起', async () => {
    const ctx = mountPropertySources()
    await ctx.initial
    ctx.b.properties.note = { kind: 'aggregate', relation: 'belongs', property: 'power', operator: 'sum' } // operator 必须 sum，否则按 unknown 解码
    ctx.api.openEditor('note')
    let page = await ctx.html()
    assert.match(page, /历史聚合配置/, 'aggregate 只读分支')
    assert.ok(!page.includes('✦ 自动填写'), 'aggregate 无自动填写入口')
    ctx.b.properties.note = { kind: 'computed', implementation: 'impl', output: 'out' }
    ctx.api.openEditor('note')
    page = await ctx.html()
    assert.match(page, /旧的取值规则/, 'computed 只读分支')
    assert.ok(!page.includes('✦ 自动填写'), 'computed 无自动填写入口')
    // 目标切换（直接替换编辑目标路径）：面板收起
    ctx.api.openEditor('power')
    ctx.api.draft.value = ctx.api.freshDbDraft()
    ctx.api.toggleAssist()
    assert.equal(ctx.api.assistVisible.value, true, '入口打开面板')
    ctx.api.openEditor('soc')
    assert.equal(ctx.api.assistVisible.value, false, '目标切换面板收起')
    page = await ctx.html()
    assert.ok(!page.includes('ps-assist-panel'), '收起后不再渲染面板')
    // 关闭编辑器：面板收起且列表态无入口
    ctx.api.openEditor('power')
    ctx.api.draft.value = ctx.api.freshDbDraft()
    ctx.api.toggleAssist()
    assert.equal(ctx.api.assistVisible.value, true)
    ctx.api.closeEditor()
    assert.equal(ctx.api.assistVisible.value, false)
    page = await ctx.html()
    assert.ok(!page.includes('✦ 自动填写'), '返回列表态无自动填写入口')
  })

  await check('⑦g 非 assist 回归：来源表单校验与保存链路不回退（保存仍写项目草稿）', async () => {
    const ctx = mountPropertySources()
    await ctx.initial
    ctx.api.openEditor('power')
    ctx.api.draft.value = ctx.api.freshDbDraft()
    ctx.api.draft.value.field = ''
    await ctx.api.saveDraft()
    assert.equal(ctx.formSave.saves.length, 0, '缺必填字段：保存前校验拦截')
    assert.match(String(ctx.api.editError.value), /取值字段/)
    ctx.api.draft.value.field = 'rated_power'
    await ctx.api.saveDraft()
    assert.equal(ctx.formSave.saves.length, 1, '合法字段：走既有 form-save 落库')
    assert.equal(ctx.b.properties.power, 'rated_power', 'identity 字符串编码写入项目映射')
  })
} finally {
  globalThis.fetch = realFetch
  rmSync(root, { recursive: true, force: true })
}

const failed = results.filter(r => !r)
console.log(`\n统计：${results.length - failed.length}/${results.length} 项通过`)
if (failed.length) process.exit(1)
