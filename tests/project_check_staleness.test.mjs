// T05 / P01（F03）前端检查结果时效性回归：不连接服务、不写真实 ontology。
//   node --import ./tests/ts_hooks.mjs tests/project_check_staleness.test.mjs
// 覆盖：
//   ① App.validateProject 响应落地前的三重校验：请求代际 / 项目 ID / 项目内容签名；
//      检查中编辑、切项目、旧响应乱序完成一律丢弃，不写入 projectReport（不显示通过）；
//   ② ProjectHome 引用版本读取失败 = 可见失败态，不能当成「已就绪空图」（零问题）；
//   ③ ProjectHome 待处理计数把 items 里 status==='unconfigured' 计入（未配置 ≠ 无问题）；
//   ④ 概览结论按项目内容签名失效：配置变化后旧结论不再作为当前结论显示，并重新检查。
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { readFileSync, mkdtempSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve, dirname } from 'node:path'
import { pathToFileURL } from 'node:url'

const require = createRequire(new URL('../frontend/package.json', import.meta.url))
const ts = require('typescript')
const { createSSRApp, createRenderer, reactive } = require('vue')
const { renderToString } = require('@vue/server-renderer')
const { parse, compileScript } = require('@vue/compiler-sfc')

const storage = new Map()
globalThis.__WIZ_SLOW_HINT_MS = 15
globalThis.addEventListener = () => {}
globalThis.removeEventListener = () => {}
globalThis.window = globalThis
globalThis.location = { search: '', hash: '#p-release', pathname: '/', port: '18765', origin: 'http://127.0.0.1:18765' }
globalThis.history = { replaceState() {} }
globalThis.localStorage = { getItem: k => (storage.has(k) ? storage.get(k) : null), setItem: (k, v) => storage.set(k, String(v)), removeItem: k => storage.delete(k) }
globalThis.document = { addEventListener() {}, removeEventListener() {}, querySelector: () => null, querySelectorAll: () => [], title: '', createElement: () => ({ click() {}, remove() {}, setAttribute() {} }), body: { appendChild() {} } }

const root = mkdtempSync(join(tmpdir(), 'wiz_check_stale_'))
const results = []
const defer = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b }); return { promise, resolve, reject } }
const settle = async () => { await new Promise(r => setImmediate(r)); await new Promise(r => setImmediate(r)) }
const ok = (data) => ({ ok: true, status: 200, json: async () => data })

async function check(name, fn) {
  try { await fn(); results.push({ name, ok: true }); console.log('通过：' + name) }
  catch (e) { results.push({ name, ok: false }); console.log('失败：' + name + ' — ' + e.message) }
}

// 子组件桩：被测组件只验证自身状态与派生结论，不渲染真实子页面
const stubFile = join(root, 'stub.vue.mjs')
writeFileSync(stubFile, 'export default { setup() { return () => null } }\n')

async function loadSfc(path) {
  const filename = resolve(path)
  const { descriptor } = parse(readFileSync(filename, 'utf8'), { filename })
  let code = compileScript(descriptor, { id: 'check-staleness' }).content
  code = code.replace(/import\s+(\w+)\s+from\s+['"][^'"]*\.vue['"]/g, 'const $1 = {}')
  code = code.replace(/from (['"])([^'"]+)\1/g, (_, q, spec) => {
    const target = spec === 'vue' ? require.resolve('vue') : resolve(dirname(filename), spec + '.ts')
    return 'from ' + JSON.stringify(pathToFileURL(target).href)
  })
  const file = join(root, path.replace(/[^\w]/g, '_') + '.mjs')
  writeFileSync(file, ts.transpileModule(code, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText)
  const Component = (await import(pathToFileURL(file).href + '?t=' + Date.now())).default
  const setup = Component.setup
  let api = null
  Component.setup = (props, ctx) => { api = setup(props, ctx); return api }
  Component.render = () => null
  return { Component, api: () => api }
}

function projectStateOf(id) {
  return {
    projectId: id, name: id === 'p2' ? '项目二' : '项目一', ontologyId: 'storage', ontologyVersion: '1.0.0',
    bindings: { object_bindings: [{ object_type: 'cluster', table: 'cluster_' + id, properties: {} }] },
  }
}

// 受控 fetch 桩：GET 端点给最小响应；POST 由用例按需挂起/应答
function harness(postPlan = {}) {
  const calls = []
  const stub = async (url, init) => {
    const path = String(url).split('?')[0]
    const body = init?.body ? JSON.parse(init.body) : null
    calls.push({ path, method: init?.method || 'GET', body })
    if (postPlan[path]) return postPlan[path]({ path, body, url })
    if (path === '/api/state') return ok({ state: { ontology: { '@graph': [] }, workflow: {} }, revision: 'r0', latestVersion: '1.0.0' })
    if (path === '/api/ontologies') return ok({ items: [{ id: 'storage', name: '储能本体' }] })
    if (path === '/api/projects') return ok({ items: [{ id: 'p1', name: '项目一' }, { id: 'p2', name: '项目二' }] })
    if (path === '/api/version-state') return ok({ state: { ontology: { '@graph': [] }, workflow: {} } })
    if (path === '/api/project-state') return ok({ state: projectStateOf(String(url).split('project=')[1]), revision: 'pr-' + String(url).split('project=')[1] })
    if (path === '/api/project-save') return ok({ revision: 'pr-saved' })
    if (path === '/api/project-releases') return ok({ items: [] })
    if (path === '/api/project-validate') return ok({ errors: [], warnings: [], items: [] })
    if (path === '/api/versions') return ok({ items: [] })
    return { ok: false, status: 404, json: async () => ({ error: 'not found' }) }
  }
  return { calls, stub, countOf: p => calls.filter(c => c.path === p).length }
}

async function loadApp() {
  globalThis.fetch = harness().stub
  const { Component, api } = await loadSfc('frontend/src/App.vue')
  const inst = createSSRApp(Component, {})
  await renderToString(inst)
  return api()
}

// ProjectHome 需要真实的组件实例（watch 依赖调度）；用空渲染器 mount，不渲染 DOM。
const emptyRenderer = () => createRenderer({
  createElement: () => ({}), createText: () => ({}), createComment: () => ({}), insert() {}, remove() {},
  setText() {}, setElementText() {}, parentNode() {}, nextSibling() {}, patchProp() {},
})
const mounted = []
async function loadProjectHome() {
  const { Component, api } = await loadSfc('frontend/src/project/ProjectHome.vue')
  const props = reactive({
    defaultOntologyId: 'storage', ontologyOptions: [{ value: 'storage', label: '储能本体' }],
    projects: [], projectState: projectStateOf('p1'), projectDirty: false, migrationTodos: [], createSignal: 0,
  })
  const app = emptyRenderer().createApp(Component, props)
  app.provide('form-guard', { register() {}, unregister() {}, hasDirty: () => false, editing: () => false })
  app.provide('form-save', { async submitForm() { return { ok: true, message: '' } } })
  app.mount({})
  mounted.push(app)
  await settle()
  return { api: api(), props }
}

try {
  // ── ① App.validateProject ──
  await check('①a 检查中编辑：旧响应丢弃，不写入报告', async () => {
    const gate = defer()
    const h = harness({ '/api/project-validate': () => gate.promise })
    const a = await loadApp()
    globalThis.fetch = h.stub
    await a.loadOntologyData()
    assert.equal(await a.loadProject('p1', true), true)
    const pendingCheck = a.validateProject(false)
    await settle()
    assert.equal(h.countOf('/api/project-validate'), 1, '检查请求已发出')
    // 检查进行中：用户编辑项目配置（内容签名 + 代际同时变化）
    a.projectState.value.bindings.object_bindings[0].table = 'changed_during_check'
    a.projectChanged()
    gate.resolve(ok({ errors: [], warnings: [], items: [], baseline: { projectId: 'p1', revision: 'pr-p1' } }))
    await pendingCheck
    assert.equal(a.projectReport.value, null, '检查中编辑后旧响应不得写入报告')
    assert.match(a.message.value, /已丢弃/)
  })

  await check('①b 检查中切项目：旧响应丢弃，不落到新项目', async () => {
    const gate = defer()
    const h = harness({ '/api/project-validate': () => gate.promise })
    const a = await loadApp()
    globalThis.fetch = h.stub
    await a.loadOntologyData()
    await a.loadProject('p1', true)
    const pendingCheck = a.validateProject(false)
    await settle()
    assert.equal(await a.loadProject('p2', true), true, '检查期间切换项目')
    gate.resolve(ok({ errors: [], warnings: [], items: [] }))
    await pendingCheck
    assert.equal(a.projectReport.value, null, '切项目后旧检查结果不得写入')
    assert.equal(String(a.projectState.value.projectId), 'p2')
  })

  await check('①c 旧响应乱序完成：后发请求结果胜出，旧结果不覆盖', async () => {
    const gates = []
    const h = harness({ '/api/project-validate': () => { const d = defer(); gates.push(d); return d.promise } })
    const a = await loadApp()
    globalThis.fetch = h.stub
    await a.loadOntologyData()
    await a.loadProject('p1', true)
    const first = a.validateProject(false)
    await settle()
    a.busy.value = false // 模拟其它操作 finally 释放全局 busy，让第二个检查真的发出（busy 只是 UI 闸门，不是正确性保证）
    const second = a.validateProject(false)
    await settle()
    assert.equal(gates.length, 2, '两个检查请求都在途')
    gates[1].resolve(ok({ errors: ['第二个请求的问题'], warnings: [], items: [] })) // 后发先完成
    await second
    assert.deepEqual(a.projectReport.value.errors, ['第二个请求的问题'], '后发请求结果被接受')
    gates[0].resolve(ok({ errors: [], warnings: [], items: [] })) // 先发后完成（乱序）
    await first
    assert.deepEqual(a.projectReport.value.errors, ['第二个请求的问题'], '乱序完成的旧响应不得覆盖新结果')
  })

  await check('①d 检查代际变化（内容未变）：同样丢弃，不显示通过', async () => {
    const gate = defer()
    const h = harness({ '/api/project-validate': () => gate.promise })
    const a = await loadApp()
    globalThis.fetch = h.stub
    await a.loadOntologyData()
    await a.loadProject('p1', true)
    const pendingCheck = a.validateProject(false)
    await settle()
    a.projectEditGeneration.value++ // 只改代际：内容签名不变也必须丢弃
    gate.resolve(ok({ errors: [], warnings: [], items: [] }))
    await pendingCheck
    assert.equal(a.projectReport.value, null)
  })

  await check('①e 检查期间无变化：结果接受并绑定内容签名；随后编辑即作废', async () => {
    const h = harness({ '/api/project-validate': () => ok({ errors: [], warnings: [], items: [], baseline: { projectId: 'p1', revision: 'pr-p1', flows: [], catalogs: [] } }) })
    const a = await loadApp()
    globalThis.fetch = h.stub
    await a.loadOntologyData()
    await a.loadProject('p1', true)
    await a.validateProject(false)
    assert.ok(a.projectReport.value, '报告已写入')
    assert.equal(a.projectReportStale.value, false, '当前内容与报告对应：不过期')
    assert.equal(a.projectCheckBaseline.value.projectId, 'p1', '服务端基线随报告保存')
    a.projectChanged()
    assert.equal(a.projectReport.value, null, '编辑后旧报告不再保留（不显示通过）')
    assert.equal(a.projectReportStale.value, false)
  })

  // ── ② ProjectHome 失败态与计数 ──
  await check('②a 引用版本读取失败：可见失败态，不是已就绪空图', async () => {
    const h = harness()
    globalThis.fetch = async (url, init) => {
      if (String(url).split('?')[0] === '/api/version-state') return { ok: false, status: 500, json: async () => ({ error: '读取版本失败' }) }
      return h.stub(url, init)
    }
    const { api } = await loadProjectHome()
    await settle()
    assert.equal(api.refGraphError.value, '读取版本失败', '读取失败必须进入可见失败态')
    assert.equal(api.guideReady.value, false, '读取失败不得标为已就绪')
    assert.equal(api.recommendation.value, null, '读不到定义时不给出「没有待配置属性」结论')
    // 重试成功后可恢复
    globalThis.fetch = h.stub
    api.refreshGuide()
    await settle()
    assert.equal(api.refGraphError.value, '')
    assert.equal(api.guideReady.value, true)
  })

  await check('②b 未配置项计入待处理（未配置 ≠ 无问题）', async () => {
    const h = harness({
      '/api/project-validate': () => ok({
        errors: ['一个问题'], warnings: [],
        items: [
          { kind: 'objectBinding', id: 'a', status: 'unconfigured', issues: ['尚未配置'] },
          { kind: 'objectBinding', id: 'b', status: 'valid', issues: [] },
          { kind: 'objectBinding', id: 'c', status: 'unconfigured', issues: ['尚未配置'] },
        ],
      }),
    })
    globalThis.fetch = h.stub
    const { api } = await loadProjectHome()
    await settle()
    assert.equal(api.step3.value.done, true)
    assert.equal(api.step3.value.pending, 3, '1 个错误 + 2 个未配置 = 3 项待处理')
    assert.match(api.step3Text.value, /3 项待处理/)
  })

  await check('②c 检查请求失败：状态为「检查未完成」，不是无问题', async () => {
    const h = harness({ '/api/project-validate': () => ({ ok: false, status: 500, json: async () => ({ error: '校验服务不可用' }) }) })
    globalThis.fetch = h.stub
    const { api } = await loadProjectHome()
    await settle()
    assert.equal(api.step3.value.error, '校验服务不可用')
    assert.match(api.step3Text.value, /检查未完成/)
    assert.ok(!/无待处理问题/.test(api.step3Text.value), '失败不能渲染成零问题')
  })

  await check('②d 概览结论按项目内容签名失效并重取', async () => {
    const h = harness()
    globalThis.fetch = h.stub
    const { api, props } = await loadProjectHome()
    await settle()
    const before = h.countOf('/api/project-validate')
    assert.equal(api.step3.value.pending, 0)
    props.projectState.bindings.object_bindings[0].table = 'other_table'
    await settle()
    assert.equal(h.countOf('/api/project-validate'), before + 1, '配置变化后重新请求检查，而不是沿用旧结论')
  })
} finally {
  for (const app of mounted) { try { app.unmount() } catch { /* 已卸载 */ } }
  rmSync(root, { recursive: true, force: true })
}

const failed = results.filter(r => !r.ok)
console.log(`\n${results.length - failed.length}/${results.length} 项通过`)
if (failed.length) process.exit(1)
