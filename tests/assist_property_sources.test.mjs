// 属性取值来源辅助填写接入测试（2026-09-21 T9，覆盖 P2/P3/P4）。运行（仓库根）：
//   node --import ./tests/ts_hooks.mjs tests/assist_property_sources.test.mjs
// 编译真实 PropertySources.vue 与 AssistPanel.vue（子组件桩化、无路由无服务、不发真实请求——
// 全局 fetch 仅计数，编排/目录请求全部失败并被组件捕获）；assist api 经 provide('assist-api')
// 注入合成桩。写法与 tests/assist_object_workspace.test.mjs 同源。
// 覆盖：① propertySourceBinding 工厂（四类 kind 白名单快照、undefined/空串不覆盖、kind 不可改、
// 复合组整组替换、redis 连接/来源形态、快照就地恢复）；② 配置态入口出现与目标上下文请求体；
// ③ 采纳只改本地草稿（draft+说明）且 form-save 零调用、保存直通落库；④ 手改通知与草稿指纹拦截；
// ⑤ database/redis/flow 三类 kind 的采纳结构断言（flow 写 flow/output 不触发编排详情拉取）；
// ⑥ aggregate/computed/registered/none 无入口、目标切换与关闭收起。
// 已知边界：SSR 渲染不执行 onMounted 与模板 ref 填充（面板自动取上下文、assistTouched 里的
// notifyDraftChanged 通道），后者由「手改计数 + 面板状态机指纹自检 + typecheck」覆盖，浏览器
// 实链路留给独立验收（与 assist_object_workspace.test.mjs 同一边界）。
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

// ── 捕获每次挂载的 setup 状态 ────────────────────────────────────────────────
let activeSlot = null
const origSetup = Component.setup
Component.setup = (props, ctx) => { const api = origSetup(props, ctx); if (activeSlot) activeSlot.api = api; return api }
const origPanelSetup = Panel.setup
Panel.setup = (props, ctx) => { const s = origPanelSetup(props, ctx); if (activeSlot) activeSlot.panel = s; return s }

// ── assist-api 桩：合成 context/generate；记录请求体；generate 按队列出栈 ────────
function makeAssistApi() {
  const calls = { context: [], generate: [] }
  const genQueue = []
  return {
    calls, genQueue,
    async context(body) {
      calls.context.push(JSON.parse(JSON.stringify(body)))
      return {
        contextToken: 'tok-' + calls.context.length,
        contextFingerprint: 'fp-' + calls.context.length,
        context: { targetKind: body.targetKind, title: 'CTX[' + body.targetKind + ':' + body.targetId + ']', editableFields: [{ key: 'field', label: '取值字段', kind: 'ref', required: true, options: null, group: null, help: '' }], definitions: [], catalog: [], flows: [], modelReady: true },
      }
    },
    async generate(body) {
      calls.generate.push(JSON.parse(JSON.stringify(body)))
      return Object.assign({ requestId: 'req-' + calls.generate.length, status: 'ok', contextFingerprint: 'fp-1', questions: [], suggestions: [], issues: [], explanation: null, meta: { durationMs: 5, provider: '测试桩', model: 'stub' } }, genQueue.shift() || {})
    },
  }
}

// ── form-save 间谍：被调用即记录（采纳后必须保持 0 次）──────────────────────────
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
    connections: { connections: [{ id: 'db', name: '测试库', engine: 'mysql' }, { id: 'redis', name: '缓存', engine: 'redis' }] },
    bindings: { object_bindings: [b], catalogs: { db: { tables: [{ name: 'clusters', fields: fields('id', 'cluster_id', 'rated_power') }, { name: 'samples', fields: fields('cluster_id', 'soc', 'sampled_at') }] } } },
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
  const app = createSSRApp(Component, { projectState: fx.projectState, refState: fx.refState, b: fx.b })
  app.provide('form-guard', { register() {}, unregister() {} })
  app.provide('form-save', formSave)
  app.provide('assist-api', assist)
  activeSlot = slot
  const initial = renderToString(app)
  return {
    ...fx, formSave, assist, initial,
    get api() { return slot.api },
    get panel() { return slot.panel },
    async tick() { await nextTick(); await nextTick() },
    // 用同一份 setup 状态重渲染页面（不重跑 setup；面板子组件会重新实例化——需要面板状态时先重开）
    html: () => { activeSlot = slot; return renderToString(createSSRApp({ render: () => h({ props: Component.props, ssrRender: Component.ssrRender, setup: () => slot.api }, { projectState: fx.projectState, refState: fx.refState, b: fx.b }) })) },
    // 用驱动后的面板状态单独渲染面板（验证横幅/提示等 UI 呈现）
    panelHtml: () => renderToString(createSSRApp({ render: () => h({ props: Panel.props, ssrRender: Panel.ssrRender, setup: () => slot.panel }, { binding: slot.api.assistBinding.value }) })),
  }
}

const results = []
async function check(name, fn) { try { await fn(); results.push(true); console.log('通过：' + name) } catch (e) { results.push(false); console.log('失败：' + name + ' — ' + (e && e.message)) } }

try {
  // ── ① 工厂：四类 kind 白名单快照 ──
  {
    const { propertySourceBinding, propertySourceAssistKind } = await import('../frontend/src/assist/propertySourceBinding.ts')
    await check('① 工厂快照：四类 kind 白名单键正确、克隆独立、不可辅助 kind 输出空快照', async () => {
      // kind 判定
      for (const k of ['field', 'database', 'redis', 'flow']) assert.equal(propertySourceAssistKind({ kind: k }), k)
      for (const k of ['aggregate', 'computed', 'registered', 'none', 'unknown', undefined, null]) assert.equal(propertySourceAssistKind({ kind: k }), '')
      assert.equal(propertySourceAssistKind(null), '')

      const note = () => '说明N'
      // field：只有 field+note，mode/connection/table 等 view-model 字段不出网
      const fd = { kind: 'field', mode: 'identity', source: '', field: 'rated_power', connection: 'db', table: 'clusters' }
      const fb = propertySourceBinding({ draft: () => fd, noteDraft: note, setNote: () => {} })
      assert.equal(fb.space, 'project'); assert.equal(fb.targetKind, 'propertySource')
      assert.deepEqual(Object.keys(fb.draft()), ['kind', 'field', 'note'])
      assert.deepEqual(fb.draft(), { kind: 'field', field: 'rated_power', note: '说明N' })

      // database：含 lookup.match 与 result.*（白名单内两个叶子），timeRange/编码等白名单外不出网
      const dd = { kind: 'database', mode: 'direct', connection: 'db', table: 'samples', lookup: { match: [{ field: 'cluster_id', operator: 'eq', value: { kind: 'identityKey' } }], timeRange: { field: 'sampled_at' } }, result: { valueField: 'soc', timestampField: 'sampled_at', timestampEncoding: 'datetime', timezone: 'X', order: 'ascending', duplicateTimestamp: 'error', secondarySortField: '', secondarySortOrder: 'ascending', selection: '', missing: 'null' } }
      const db = propertySourceBinding({ draft: () => dd, noteDraft: note, setNote: () => {} })
      assert.deepEqual(Object.keys(db.draft()), ['kind', 'connection', 'table', 'result.valueField', 'result.timestampField', 'lookup.match', 'note'])
      assert.deepEqual(db.draft()['lookup.match'], [{ field: 'cluster_id', operator: 'eq', value: { kind: 'identityKey' } }])
      assert.ok(!('timeRange' in db.draft()), 'lookup.timeRange 不在白名单')
      const snapBefore = JSON.stringify(dd.lookup.match)
      const snapMatch = db.draft()['lookup.match']
      dd.lookup.match.push({ field: 'hack', operator: 'eq', value: { kind: 'identityKey' } })
      assert.equal(JSON.stringify(snapMatch), snapBefore, '复合组快照是克隆，不随草稿后续修改漂移')

      // redis：来源形态还原为纯 id（写明的选择），连接形态原样；缺省键给展示默认
      const rd = { kind: 'redis', source: 'srcA', connection: '', command: 'HGET', key: 'm-{id}', hashField: 'soc', params: { id: { from: 'primary' } }, conversion: 'number', missing: 'error' }
      const rb = propertySourceBinding({ draft: () => rd, noteDraft: note, setNote: () => {} })
      assert.deepEqual(Object.keys(rb.draft()), ['kind', 'connection', 'command', 'key', 'hashField', 'params', 'conversion', 'missing', 'note'])
      assert.equal(rb.draft().connection, 'srcA', '来源形态快照成来源纯 id（无 src: 前缀）')
      rd.source = ''; rd.connection = 'redis1'
      assert.equal(rb.draft().connection, 'redis1', '连接形态快照成连接纯 id')
      const bare = { kind: 'redis', source: '', connection: '', command: '', key: '', hashField: '', params: {}, conversion: '', missing: '' }
      const bb = propertySourceBinding({ draft: () => bare, noteDraft: () => '', setNote: () => {} })
      assert.deepEqual([bb.draft().command, bb.draft().conversion, bb.draft().missing], ['GET', 'number', 'null'], '缺省键按表单展示默认出快照')

      // flow：flow/output/inputs/result.*
      const ld = { kind: 'flow', flow: 'f1', output: 'o1', inputs: { in1: { from: 'property', property: 'soc' } }, result: { valueField: 'v1', timestampField: 't1' } }
      const lb = propertySourceBinding({ draft: () => ld, noteDraft: note, setNote: () => {} })
      assert.deepEqual(Object.keys(lb.draft()), ['kind', 'flow', 'output', 'inputs', 'result.valueField', 'result.timestampField', 'note'])

      // 不可辅助/空草稿：空快照、不抛错
      const nb = propertySourceBinding({ draft: () => ({ kind: 'none' }), noteDraft: () => '', setNote: () => {} })
      assert.deepEqual(nb.draft(), {})
      const gb = propertySourceBinding({ draft: () => null, noteDraft: () => '', setNote: () => {} })
      assert.deepEqual(gb.draft(), {}); gb.apply({ field: 'x' }); gb.restore({})
      assert.ok(true, '空草稿 apply/restore 安全')
    })
  }

  // ── ② 工厂 apply：复合组整组替换 / undefined 与空串不覆盖 / kind 不可改 / 连接形态 ──
  {
    const { propertySourceBinding } = await import('../frontend/src/assist/propertySourceBinding.ts')
    await check('② 工厂 apply：redis params、database lookup.match、flow inputs 整组替换；note→setNote', async () => {
      // redis params 整组替换（旧键清除）
      const rd = { kind: 'redis', source: '', connection: 'r1', command: 'GET', key: 'k-{id}-{old}', hashField: '', params: { id: { from: 'primary' }, old: { from: 'property', property: 'soc' } }, conversion: 'number', missing: 'null' }
      let notedValue = ''
      const rb = propertySourceBinding({ draft: () => rd, noteDraft: () => notedValue, setNote: v => { notedValue = v } })
      rb.apply({ params: { dev: { from: 'identityField', field: 'dev_id' } } })
      assert.deepEqual(rd.params, { dev: { from: 'identityField', field: 'dev_id' } }, 'params 整组替换，旧绑定清除')
      rb.apply({ key: 'soc-{id}', hashField: 'soc', conversion: 'text', missing: 'error', note: '缓存说明' })
      assert.equal(rd.key, 'soc-{id}'); assert.equal(rd.hashField, 'soc'); assert.equal(rd.conversion, 'text'); assert.equal(rd.missing, 'error')
      assert.equal(notedValue, '缓存说明', 'note 经 setNote 写回说明草稿')

      // database lookup.match 整组替换；timeRange 同组其他键保留
      const dd = { kind: 'database', connection: 'db', table: 'samples', lookup: { match: [{ field: 'old', operator: 'eq', value: { kind: 'identityKey' } }], timeRange: { field: 'sampled_at' } }, result: { valueField: '', timestampField: '' } }
      const db = propertySourceBinding({ draft: () => dd, noteDraft: () => '', setNote: () => {} })
      db.apply({ 'lookup.match': [{ field: 'cluster_id', operator: 'eq', value: { kind: 'identityKey' } }, { field: 'park', operator: 'eq', value: { kind: 'constant', value: 'P001' } }] })
      assert.deepEqual(dd.lookup.match, [{ field: 'cluster_id', operator: 'eq', value: { kind: 'identityKey' } }, { field: 'park', operator: 'eq', value: { kind: 'constant', value: 'P001' } }])
      assert.equal(dd.lookup.timeRange.field, 'sampled_at', 'timeRange 保留')
      db.apply({ connection: 'db2', table: 'samples2', 'result.valueField': 'soc', 'result.timestampField': 'sampled_at' })
      assert.equal(dd.connection, 'db2'); assert.equal(dd.table, 'samples2')
      assert.deepEqual(dd.result.valueField, 'soc'); assert.equal(dd.result.timestampField, 'sampled_at')

      // flow inputs 整组替换；flow/output 只写值不重置其他键（无 selectFlow 副作用）
      const ld = { kind: 'flow', flow: 'f1', output: '', inputs: { in1: { from: 'property', property: 'power' } }, result: { valueField: '', timestampField: '' } }
      const lb = propertySourceBinding({ draft: () => ld, noteDraft: () => '', setNote: () => {} })
      lb.apply({ flow: 'f2', output: 'o9', inputs: { in1: { from: 'instanceId' } }, 'result.valueField': 'v1' })
      assert.equal(ld.flow, 'f2'); assert.equal(ld.output, 'o9')
      assert.deepEqual(ld.inputs, { in1: { from: 'instanceId' } }, 'inputs 整组替换')
      assert.deepEqual(ld.result, { valueField: 'v1', timestampField: '' }, 'result.valueField 写入、其余键不动')
    })

    await check('②b 工厂 apply 防御：undefined/空串不覆盖、kind 不可改、白名单外忽略、redis 连接形态', async () => {
      const rd = { kind: 'redis', source: '', connection: 'r1', command: 'GET', key: 'keep', hashField: 'h', params: {}, conversion: 'number', missing: 'null' }
      const setterCalls = []
      const rb = propertySourceBinding({
        draft: () => rd, noteDraft: () => '', setNote: () => {},
        setRedisTarget: v => { setterCalls.push(v); if (v.startsWith('conn:')) { rd.connection = v.slice(5); rd.source = '' } else if (v.startsWith('src:')) { rd.source = v.slice(4); rd.connection = '' } },
        redisSourceIds: () => ['srcA'],
      })
      rb.apply({ kind: 'flow', key: undefined, hashField: '', source: 'hack', mode: 'x', result: { valueField: '直写 result 键' }, command: 'GET', connection: 'srcA' })
      assert.equal(rd.kind, 'redis', 'kind 不可由建议修改')
      assert.equal(rd.key, 'keep', 'undefined 不覆盖')
      assert.equal(rd.hashField, 'h', '空串不提供清空语义')
      assert.equal(rd.source, 'srcA', '建议连接值命中来源集合 → src: 形态写回')
      assert.equal(rd.connection, '', '来源形态下连接清空')
      assert.ok(!('mode' in rd), '白名单外键忽略')
      assert.equal(rd.result, undefined, '裸 result 键不在白名单（白名单是 result.valueField）')
      assert.deepEqual(setterCalls, ['src:srcA'])
      rb.apply({ connection: 'redis9' }); assert.equal(rd.connection, 'redis9'); assert.equal(rd.source, '', '普通 id 按连接形态写回')
      rb.apply({ connection: 'src:srcB' }); assert.equal(rd.source, 'srcB', '已带前缀的建议值原样转发')

      // database 连接：'db:' UI 前缀剥掉
      const dd = { kind: 'database', connection: '', table: '' }
      const db = propertySourceBinding({ draft: () => dd, noteDraft: () => '', setNote: () => {} })
      db.apply({ connection: 'db:dbx' }); assert.equal(dd.connection, 'dbx')
      // 无 setRedisTarget 注入：退化直写连接形态
      const rd2 = { kind: 'redis', source: '', connection: '' }
      const rb2 = propertySourceBinding({ draft: () => rd2, noteDraft: () => '', setNote: () => {} })
      rb2.apply({ connection: 'r2' }); assert.equal(rd2.connection, 'r2'); assert.equal(rd2.source, '')
      // field kind：connection/table 不在该 kind 白名单，忽略
      const fd = { kind: 'field', mode: 'identity', field: '', connection: 'db', table: 'clusters' }
      const fb = propertySourceBinding({ draft: () => fd, noteDraft: () => '', setNote: () => {} })
      fb.apply({ connection: 'x', table: 'y', field: 'rated_power' })
      assert.equal(fd.connection, 'db'); assert.equal(fd.table, 'clusters'); assert.equal(fd.field, 'rated_power')
      // 不可辅助 kind：apply 无操作
      const ad = { kind: 'aggregate', relation: 'r', property: 'p' }
      const ab = propertySourceBinding({ draft: () => ad, noteDraft: () => '', setNote: () => {} })
      ab.apply({ field: 'x', note: 'n' }); assert.deepEqual(ad, { kind: 'aggregate', relation: 'r', property: 'p' })
    })

    await check('②c 工厂快照/恢复：草稿引用稳定、白名单外零丢失、note 一并恢复', async () => {
      const fd = { kind: 'field', mode: 'identity', field: 'a', connection: 'db', table: 'clusters' }
      let note = 'n1'
      const fb = propertySourceBinding({ draft: () => fd, noteDraft: () => note, setNote: v => { note = v } })
      const snap = fb.snapshot()
      assert.deepEqual(snap, { draft: { kind: 'field', mode: 'identity', field: 'a', connection: 'db', table: 'clusters' }, note: 'n1' })
      fd.field = 'b'; fd.mode = 'registered'; note = 'n2'
      const refBefore = fd
      fb.restore(snap)
      assert.ok(fd === refBefore, 'restore 就地恢复（宿主草稿引用保持稳定）')
      assert.equal(fd.field, 'a'); assert.equal(fd.mode, 'identity', '白名单外字段一并恢复')
      assert.equal(note, 'n1', '说明一并恢复')
    })
  }

  // ── ③~⑥ 组件级全链路 ──
  await check('③ 组件：列表态与未配置（none）无入口；field 草稿出现入口，面板与兜底标题出现', async () => {
    const ctx = mountPropertySources()
    await ctx.initial
    let page = await ctx.html()
    assert.ok(!page.includes('✦ 辅助填写'), '列表态无辅助入口')
    ctx.api.openEditor('power')
    page = await ctx.html()
    assert.match(page, /尚未配置取值来源/, '未配置属性进入 none 态')
    assert.ok(!page.includes('✦ 辅助填写'), 'kind=none 无辅助入口')
    ctx.api.draft.value = ctx.api.freshDbDraft() // 等价「＋ 取值配置」按钮：进入 field 草稿
    page = await ctx.html()
    assert.match(page, /✦ 辅助填写/, 'field 草稿出现辅助入口')
    assert.ok(!page.includes('ps-assist-panel'), '未点击前不渲染面板')
    ctx.api.toggleAssist()
    page = await ctx.html()
    assert.match(page, /ps-assist-panel/, '点击后面板出现')
    assert.match(page, /配置「power」的取值来源/, '未取到上下文时展示目标兜底标题')
    assert.ok(ctx.panel, '面板实例已随页面创建')
  })

  await check('④ 上下文请求：project 空间 + propertySource 目标 + 白名单草稿（field/note）', async () => {
    const ctx = mountPropertySources()
    await ctx.initial
    ctx.api.openEditor('power')
    ctx.api.draft.value = ctx.api.freshDbDraft()
    ctx.api.toggleAssist()
    await ctx.html()
    await ctx.panel.open(ctx.api.assistBinding.value)
    assert.equal(ctx.assist.calls.context.length, 1)
    const body = ctx.assist.calls.context[0]
    assert.equal(body.space, 'project'); assert.equal(body.projectId, 'p1')
    assert.equal(body.targetKind, 'propertySource'); assert.equal(body.targetId, 'cluster.power')
    assert.equal(body.purpose, 'fill')
    assert.deepEqual(body.draft, { kind: 'field', field: '', note: '' }, 'draft 是白名单形态快照')
  })

  await check('⑤ 采纳（field+note）：草稿与说明更新、form-save 零调用、已保存配置不动、提示尚未保存；手改被指纹拦下', async () => {
    const ctx = mountPropertySources()
    await ctx.initial
    ctx.api.openEditor('power')
    ctx.api.draft.value = ctx.api.freshDbDraft()
    ctx.api.toggleAssist()
    await ctx.html()
    await ctx.panel.open(ctx.api.assistBinding.value)
    ctx.assist.genQueue.push({ suggestions: [
      { id: 's1', label: '取值字段', fieldKeys: ['field'], proposed: { field: 'rated_power' }, state: 'ready' },
      { id: 's2', label: '说明', fieldKeys: ['note'], proposed: { note: '来自实例来源表的额定功率字段。' }, state: 'ready' },
    ] })
    await ctx.panel.generate()
    assert.equal(ctx.assist.calls.generate.length, 1)
    assert.equal(ctx.panel.adopt(), true)
    assert.equal(ctx.api.draft.value.field, 'rated_power')
    assert.equal(ctx.api.noteDraft.value, '来自实例来源表的额定功率字段。', 'note 建议写进说明草稿')
    assert.equal(ctx.formSave.saves.length, 0, '采纳绝不触发表单保存（form-save 零调用）')
    assert.deepEqual(ctx.b.properties.power, undefined, '采纳不写已保存配置')
    assert.equal(ctx.panel.canUndo.value, true)
    assert.equal(ctx.panel.justAdopted.value, true)
    const phtml = await ctx.panelHtml()
    assert.match(phtml, /已填入表单，尚未保存/)
    // 手改：采纳先被草稿指纹拦下（白名单快照随 field 变化漂移）；面板侧过期由手改通知入口触发
    ctx.api.draft.value.field = '手改字段'
    assert.equal(ctx.panel.adopt(), false, '手改后草稿指纹漂移，采纳被拒')
    assert.equal(ctx.api.draft.value.field, '手改字段', '被拒的采纳不得改动草稿')
    ctx.panel.notifyDraftChanged() // PropertySources 深度 watch 手改时调用的同一入口（SSR 下 watch 不触发，浏览器实链路由 assistTouched 调用）
    assert.equal(ctx.panel.stale.value, true)
    assert.equal(ctx.panel.canUndo.value, false)
    assert.equal(ctx.panel.justAdopted.value, false, '手改清除「已填入」提示')
    ctx.api.closeEditor()
    assert.equal(ctx.formSave.saves.length, 0, '取消不保存')
    assert.deepEqual(ctx.b.properties.power, undefined)
  })

  await check('⑤b 采纳后用户保存：走既有 form-save 链路落库（identity 字符串编码）', async () => {
    const ctx = mountPropertySources()
    await ctx.initial
    ctx.api.openEditor('power')
    ctx.api.draft.value = ctx.api.freshDbDraft()
    ctx.api.toggleAssist()
    await ctx.html()
    await ctx.panel.open(ctx.api.assistBinding.value)
    ctx.assist.genQueue.push({ suggestions: [{ id: 's1', label: '取值字段', fieldKeys: ['field'], proposed: { field: 'rated_power' }, state: 'ready' }] })
    await ctx.panel.generate()
    assert.equal(ctx.panel.adopt(), true)
    await ctx.api.saveDraft() // 用户显式保存（既有校验链路）
    assert.equal(ctx.formSave.saves.length, 1)
    assert.equal(ctx.b.properties.power, 'rated_power', '建议值经既有保存编码落库')
  })

  await check('⑥ database：上下文快照含 lookup.match/result.*；采纳整组替换匹配条件与结果字段；零保存', async () => {
    const ctx = mountPropertySources()
    await ctx.initial
    ctx.api.openEditor('soc')
    ctx.api.draft.value = ctx.api.emptyDatabaseDraft()
    ctx.api.dbConnChanged('db')
    ctx.api.dbTableChanged('clusters')
    ctx.api.addMatch() // 旧匹配条件一行（采纳应整组替换掉）
    let page = await ctx.html()
    assert.match(page, /✦ 辅助填写/, 'database 草稿有入口')
    ctx.api.toggleAssist()
    await ctx.html()
    await ctx.panel.open(ctx.api.assistBinding.value)
    const body = ctx.assist.calls.context.at(-1)
    assert.deepEqual(body.draft, { kind: 'database', connection: 'db', table: 'clusters', 'result.valueField': '', 'result.timestampField': '', 'lookup.match': [{ field: '', operator: 'eq', value: { kind: 'identityKey' } }], note: '' })
    ctx.assist.genQueue.push({ suggestions: [
      { id: 'd1', label: '匹配条件', fieldKeys: ['lookup.match'], proposed: { 'lookup.match': [{ field: 'cluster_id', operator: 'eq', value: { kind: 'identityKey' } }] }, state: 'ready' },
      { id: 'd2', label: '取值字段', fieldKeys: ['result.valueField'], proposed: { 'result.valueField': 'rated_power' }, state: 'ready' },
    ] })
    await ctx.panel.generate()
    assert.equal(ctx.panel.adopt(), true)
    assert.deepEqual(ctx.api.draft.value.lookup.match, [{ field: 'cluster_id', operator: 'eq', value: { kind: 'identityKey' } }], '匹配条件整组替换（旧行清除）')
    assert.equal(ctx.api.draft.value.result.valueField, 'rated_power')
    assert.equal(ctx.formSave.saves.length, 0)
  })

  await check('⑥b redis：来源/连接形态快照纯 id；params 整组替换；零保存', async () => {
    const ctx = mountPropertySources()
    await ctx.initial
    ctx.api.openEditor('soc')
    ctx.api.dataConnectionChanged('redis:redis') // 组件既有路径：switchKind('redis') + setRedisTarget('conn:redis')
    let page = await ctx.html()
    assert.match(page, /✦ 辅助填写/, 'redis 草稿有入口')
    ctx.api.toggleAssist()
    await ctx.html()
    await ctx.panel.open(ctx.api.assistBinding.value)
    let body = ctx.assist.calls.context.at(-1)
    assert.deepEqual(body.draft, { kind: 'redis', connection: 'redis', command: 'GET', key: '', hashField: '', params: {}, conversion: 'number', missing: 'null', note: '' }, '连接形态快照纯 id（无 conn: 前缀）')
    ctx.assist.genQueue.push({ suggestions: [
      { id: 'r1', label: 'Key 模板', fieldKeys: ['key'], proposed: { key: 'soc-{id}' }, state: 'ready' },
      { id: 'r2', label: '参数绑定', fieldKeys: ['params'], proposed: { params: { id: { from: 'primary' } } }, state: 'ready' },
    ] })
    await ctx.panel.generate()
    assert.equal(ctx.panel.adopt(), true)
    assert.equal(ctx.api.draft.value.key, 'soc-{id}')
    assert.deepEqual(ctx.api.draft.value.params, { id: { from: 'primary' } }, 'params 整组替换')
    assert.equal(ctx.formSave.saves.length, 0)
    // 来源形态：快照还原为来源纯 id
    ctx.api.draft.value.source = 'srcX'; ctx.api.draft.value.connection = ''
    await ctx.panel.open(ctx.api.assistBinding.value)
    body = ctx.assist.calls.context.at(-1)
    assert.equal(body.draft.connection, 'srcX', '来源形态快照成来源纯 id')
  })

  await check('⑥c flow：inputs/output/result 采纳整组替换；写 flow/output 不触发编排详情拉取（fetch 计数不增）', async () => {
    const realFetch = globalThis.fetch
    let fetchCount = 0
    globalThis.fetch = async (...a) => { fetchCount++; return realFetch(...a) }
    try {
      const ctx = mountPropertySources()
      await ctx.initial
      await ctx.tick()
      ctx.api.openEditor('history')
      assert.equal(ctx.api.draftShape.value, 'timeSeries')
      ctx.api.switchKind('flow')
      ctx.api.draft.value.flow = 'f1' // 直接写值（不经 selectFlow，避免拉取编排详情）
      let page = await ctx.html()
      assert.match(page, /✦ 辅助填写/, 'flow 草稿有入口')
      ctx.api.toggleAssist()
      await ctx.html()
      await ctx.panel.open(ctx.api.assistBinding.value)
      const body = ctx.assist.calls.context.at(-1)
      assert.deepEqual(body.draft, { kind: 'flow', flow: 'f1', output: '', inputs: {}, 'result.valueField': '', 'result.timestampField': '', note: '' })
      ctx.assist.genQueue.push({ suggestions: [
        { id: 'f1', label: '取值输出', fieldKeys: ['output'], proposed: { output: 'o1' }, state: 'ready' },
        { id: 'f2', label: '输入绑定', fieldKeys: ['inputs'], proposed: { inputs: { in1: { from: 'instanceId' } } }, state: 'ready' },
        { id: 'f3', label: '取值字段', fieldKeys: ['result.valueField'], proposed: { 'result.valueField': 'v1' }, state: 'ready' },
      ] })
      await ctx.panel.generate()
      const countBeforeAdopt = fetchCount
      assert.equal(ctx.panel.adopt(), true)
      assert.equal(ctx.api.draft.value.output, 'o1')
      assert.deepEqual(ctx.api.draft.value.inputs, { in1: { from: 'instanceId' } }, 'inputs 整组替换')
      assert.equal(ctx.api.draft.value.result.valueField, 'v1')
      assert.equal(fetchCount, countBeforeAdopt, '采纳写 flow/output 不触发编排详情/目录等任何网络请求')
      assert.equal(ctx.formSave.saves.length, 0)
    } finally { globalThis.fetch = realFetch }
  })

  await check('⑦ 无入口与收起：aggregate/computed 无入口；目标切换与关闭收起；手改/收起接线在源码中', async () => {
    const ctx = mountPropertySources()
    await ctx.initial
    // aggregate（历史遗留只读结构）
    ctx.b.properties.note = { kind: 'aggregate', relation: 'belongs', property: 'power', operator: 'sum' } // operator 必须 sum，否则按 unknown 解码
    ctx.api.openEditor('note')
    let page = await ctx.html()
    assert.match(page, /历史聚合配置/, 'aggregate 只读分支')
    assert.ok(!page.includes('✦ 辅助填写'), 'aggregate 无辅助入口')
    // computed（旧取值规则/函数，只读遗留结构）
    ctx.b.properties.note = { kind: 'computed', implementation: 'impl', output: 'out' }
    ctx.api.openEditor('note')
    page = await ctx.html()
    assert.match(page, /旧的取值规则/, 'computed 只读分支')
    assert.ok(!page.includes('✦ 辅助填写'), 'computed 无辅助入口')
    // 目标切换（直接替换编辑目标路径）：面板收起（openEditor 内同步 closeAssist）
    ctx.api.openEditor('power')
    ctx.api.draft.value = ctx.api.freshDbDraft()
    ctx.api.toggleAssist()
    page = await ctx.html()
    assert.match(page, /ps-assist-panel/, '面板已展开')
    ctx.api.openEditor('soc') // 换目标
    assert.equal(ctx.api.assistVisible.value, false, '目标切换面板收起')
    page = await ctx.html()
    assert.ok(!page.includes('ps-assist-panel'), '收起后不再渲染面板')
    // 关闭编辑器：面板收起且列表态无入口
    ctx.api.openEditor('power')
    ctx.api.draft.value = ctx.api.freshDbDraft()
    ctx.api.toggleAssist()
    page = await ctx.html()
    assert.match(page, /ps-assist-panel/)
    ctx.api.closeEditor()
    assert.equal(ctx.api.assistVisible.value, false)
    page = await ctx.html()
    assert.ok(!page.includes('✦ 辅助填写'), '返回列表态无辅助入口')
    // SSR 下 watch 回调不触发（渲染后作用域不可靠）：手改通知与 kind 失效收起的接线以源码断言，
    // 浏览器实链路由独立验收覆盖（与 assist_property_manager.test.mjs ⑦d 同一手势）。
    const source = readFileSync(resolve('frontend/src/project/PropertySources.vue'), 'utf8')
    assert.match(source, /watch\(assistFingerprint/, '手改→面板通知的深度 watch 已接线')
    assert.match(source, /assistPanelRef\.value\?\.notifyDraftChanged/, '手改通知经面板模板 ref')
    assert.match(source, /watch\(assistAvailable/, 'kind 失效→面板收起的 watch 已接线')
  })
} finally {
  rmSync(root, { recursive: true, force: true })
}

const failed = results.filter(r => !r)
console.log(`\n统计：${results.length - failed.length}/${results.length} 项通过`)
if (failed.length) process.exit(1)
