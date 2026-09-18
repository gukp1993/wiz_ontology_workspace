// 本体/项目按需加载（20260917 R2）回归：本体冷启动不依赖项目数据、项目深链加载后再判定、
// 无项目与加载失败分开、旧项目响应不覆盖新项目、同一项目并发加载去重。
// 运行（仓库根）：node --import ./tests/ts_hooks.mjs tests/ontology_lazy_load.test.mjs
// 用真实 App.vue 的 <script setup>（子页面桩化）+ 受控 fetch，不连接服务、不写真实 ontology。
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { readFileSync, mkdtempSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { pathToFileURL } from 'node:url'

const require = createRequire(new URL('../frontend/package.json', import.meta.url))
const { parse, compileScript } = require('@vue/compiler-sfc')
const ts = require('typescript'), { createSSRApp } = require('vue'), { renderToString } = require('@vue/server-renderer')

const storage = new Map()
globalThis.addEventListener = () => {}
globalThis.removeEventListener = () => {}
globalThis.window = globalThis
globalThis.location = { search: '', hash: '#objects' }
globalThis.history = { replaceState() {} }
globalThis.localStorage = { getItem: k => (storage.has(k) ? storage.get(k) : null), setItem: (k, v) => storage.set(k, String(v)), removeItem: k => storage.delete(k) }
globalThis.document = { addEventListener() {}, removeEventListener() {}, querySelector: () => null, querySelectorAll: () => [], title: '' }

const root = mkdtempSync(join(tmpdir(), 'wiz_lazy_load_'))
const SRC = resolve('frontend/src')

/** 编译 App.vue 的 <script setup>：所有子页面桩化，只测启动/导航的项目加载编排。 */
async function loadApp() {
  const filename = resolve('frontend/src/App.vue')
  const { descriptor } = parse(readFileSync(filename, 'utf8'), { filename })
  let code = compileScript(descriptor, { id: 'app-test' }).content
  code = code.replace(/import\s+(\w+)\s+from\s+['"][^'"]*\.vue['"]/g, 'const $1 = {}')
  code = code.replace(/from (['"])([^'"]+)\1/g, (_, q, spec) => {
    const target = spec === 'vue' ? require.resolve('vue') : resolve(SRC, spec + '.ts')
    return 'from ' + JSON.stringify(pathToFileURL(target).href)
  })
  const file = join(root, 'App.mjs')
  writeFileSync(file, ts.transpileModule(code, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText)
  const Component = (await import(pathToFileURL(file).href)).default
  const setup = Component.setup
  let api = null
  Component.setup = (props, ctx) => { api = setup(props, ctx); return api }
  Component.render = () => null
  return { Component, api: () => api }
}

// ── 受控服务端：按 URL 路径应答；fail/delay 控制失败与乱序 ──
const PROJECT_A = { projectId: 'A', name: '项目A', ontologyId: '', ontologyVersion: '', bindings: {} }
const PROJECT_B = { projectId: 'B', name: '项目B', ontologyId: '', ontologyVersion: '', bindings: {} }
function makeServer() {
  const calls = []
  let failPaths = [], items = [{ id: 'A', name: '项目A' }, { id: 'B', name: '项目B' }]
  const routes = {
    '/api/state': () => ({ state: { ontology: { '@graph': [] }, workflow: {} }, revision: 'r1', latestVersion: 'v1' }),
    '/api/ontologies': () => ({ items: [{ id: 'storage', name: '储能本体' }] }),
    '/api/projects': () => ({ items }),
    '/api/project-state': url => ({ state: url.includes('project=B') ? PROJECT_B : PROJECT_A, revision: 'pr1' }),
    '/api/version-state': () => ({ state: { ontology: { '@graph': [] } } }),
  }
  const fetchStub = async (url) => {
    const path = String(url).split('?')[0]
    calls.push(String(url))
    if (failPaths.includes(path)) return { ok: false, status: 500, json: async () => ({ error: '服务不可用' }) }
    const hit = routes[path]
    if (!hit) return { ok: false, status: 404, json: async () => ({ error: 'not found: ' + path }) }
    return { ok: true, status: 200, json: async () => hit(String(url)) }
  }
  return { calls, fetchStub, setFail: (...p) => { failPaths = p }, setItems: v => { items = v }, countOf: p => calls.filter(c => c.startsWith(p)).length }
}

const results = []
const { Component, api } = await loadApp()

/** 每个场景一个全新的 App 实例（setup 重新执行），fetch 指向当前场景的受控服务端。 */
async function scenario(server, fn) {
  globalThis.fetch = server.fetchStub
  const app = createSSRApp(Component, {})
  await renderToString(app)
  await fn(api())
}
async function check(name, fn) {
  const server = makeServer()
  try { await scenario(server, a => fn(server, a)); results.push({ name, ok: true }); console.log('通过：' + name) }
  catch (e) { results.push({ name, ok: false }); console.log('失败：' + name + ' — ' + e.message) }
}

try {
  // ── A5：本体冷启动不请求项目数据；项目接口延迟/失败都不影响本体 ──
  await check('A5 本体页启动不请求项目列表/项目状态（项目接口延迟也不阻塞本体）', async (server, a) => {
    a.space.value = 'ontology'
    a.view.value = 'objects'
    await a.settleInitialView()
    assert.equal(server.countOf('/api/projects'), 0, '本体页不应请求项目列表')
    assert.equal(server.countOf('/api/project-state'), 0, '本体页不应请求项目状态')
    assert.equal(a.projectListState.value, 'idle', '本体页不需要加载项目列表')
    assert.equal(a.message.value, '', '本体页不得出现无关的项目失败横幅')
  })

  // ── A5：函数编排不依赖项目 ──
  await check('A5 函数编排页面不被项目加载阻塞（列表后台加载，完整状态不请求）', async (server, a) => {
    a.space.value = 'project'
    a.view.value = 'f-home'
    await a.settleInitialView()
    // 20260918 起（1fed1d4）：编排页侧栏需要项目选择器，列表后台加载（fire-and-forget）；
    // 阻断性断言保留在「不请求完整项目状态」——编排页自身不依赖项目状态。
    assert.equal(server.countOf('/api/project-state'), 0, '编排页不需要项目状态')
    assert.ok(server.countOf('/api/projects') <= 1, '列表至多后台加载一次：' + server.countOf('/api/projects'))
  })

  // ── A6：项目深链按需加载后再判定（不能因懒加载未完成就退回概览） ──
  await check('A6 项目深链在必要加载完成后仍停在深链页，并按记忆/默认规则恢复项目', async (server, a) => {
    storage.clear()
    a.space.value = 'project'
    a.view.value = 'binding'
    await a.settleInitialView()
    assert.equal(a.projectState.value?.projectId, 'A', '无有效记忆时沿用默认选择规则（第一个项目）')
    assert.equal(a.view.value, 'binding', '有项目时必须停在深链页面')
    assert.equal(server.countOf('/api/projects'), 1)
    assert.equal(server.countOf('/api/project-state'), 1)
  })

  await check('A6 有效记忆项目优先于默认选择', async (server, a) => {
    storage.clear()
    storage.set('wiz-last-project', 'B')
    a.space.value = 'project'
    a.view.value = 'binding'
    await a.settleInitialView()
    assert.equal(a.projectState.value?.projectId, 'B', '记忆项目有效时应恢复它')
    assert.equal(a.view.value, 'binding')
  })

  await check('A6 加载失败与「没有项目」分开：失败退回概览并保留重试入口', async (server, a) => {
    storage.clear()
    server.setFail('/api/projects')
    a.space.value = 'project'
    a.view.value = 'binding'
    await a.settleInitialView()
    assert.equal(a.projectListState.value, 'error')
    assert.match(a.projectListError.value, /服务不可用/)
    assert.equal(a.projectAreaFailed.value, true, '失败要在项目上下文呈现，而不是「还没有项目」')
    assert.equal(a.projectAreaWaiting.value, false, '失败态与等待骨架互斥（否则错误面板被骨架挡住）')
    assert.equal(a.view.value, 'p-home')
    server.setFail()
    await a.retryProjectContext()
    assert.equal(a.projectListState.value, 'ready')
    assert.equal(a.projectLoadError.value, '')
    assert.equal(a.projects.value.length, 2)
    assert.equal(a.projectAreaFailed.value, false)
  })

  await check('A6 确实没有项目时按空态处理（不是加载失败）', async (server, a) => {
    storage.clear()
    server.setItems([])
    a.space.value = 'project'
    a.view.value = 'binding'
    await a.settleInitialView()
    assert.equal(a.projectListState.value, 'ready')
    assert.equal(a.projects.value.length, 0)
    assert.equal(a.projectState.value, null)
    assert.equal(a.projectLoadError.value, '', '没有项目不是加载失败')
    assert.equal(a.projectAreaFailed.value, false)
    assert.equal(a.projectAreaWaiting.value, false)
    assert.equal(a.view.value, 'p-home', '无项目回到概览显示创建/选择入口')
  })

  await check('A6 进入项目菜单会按需加载并阻断无项目深链（提示而非空白页）', async (server, a) => {
    storage.clear()
    server.setItems([])
    a.space.value = 'ontology'
    a.view.value = 'objects'
    await a.navigate('binding')
    assert.equal(server.countOf('/api/projects'), 1, '进入项目菜单才加载项目列表')
    assert.equal(a.view.value, 'objects', '没有项目时不进入对象映射')
    assert.equal(a.error.value, true)
    assert.match(a.message.value, /请先在项目概览中选择或新建项目/)
  })

  // ── A7：旧响应不覆盖新项目、同一项目并发去重、离开项目区不被抢回 ──
  await check('A7 项目A慢响应晚于B返回时不覆盖B', async (server, a) => {
    let resolveA = null
    globalThis.fetch = async (url) => {
      const path = String(url).split('?')[0]
      if (path === '/api/project-state' && String(url).includes('project=A')) {
        return new Promise(r => { resolveA = () => r({ ok: true, status: 200, json: async () => ({ state: PROJECT_A, revision: 'prA' }) }) })
      }
      if (path === '/api/project-state') return { ok: true, status: 200, json: async () => ({ state: PROJECT_B, revision: 'prB' }) }
      return server.fetchStub(url)
    }
    a.space.value = 'project'
    const pendingA = a.loadProject('A', true)
    const pendingB = a.loadProject('B', true)
    await pendingB
    assert.equal(a.projectState.value?.projectId, 'B', 'B 已加载')
    resolveA()
    await pendingA
    assert.equal(a.projectState.value?.projectId, 'B', 'A 的迟到响应不得覆盖 B')
    assert.equal(a.projectId.value, 'B')
    assert.equal(a.projectLoading.value, false)
  })

  await check('A7 同一项目并发进入只发一次请求', async (server, a) => {
    storage.clear()
    a.space.value = 'project'
    await Promise.all([a.ensureProjectContext(), a.ensureProjectContext(), a.ensureProjectContext()])
    assert.equal(server.countOf('/api/projects'), 1, '项目列表并发去重')
    assert.equal(server.countOf('/api/project-state'), 1, '同一项目并发加载去重')
    assert.equal(a.projectState.value?.projectId, 'A')
  })

  await check('A7 用户离开项目区后，在途项目响应不把页面抢回来', async (server, a) => {
    let resolveState = null
    globalThis.fetch = async (url) => {
      const path = String(url).split('?')[0]
      if (path === '/api/project-state') return new Promise(r => { resolveState = () => r({ ok: true, status: 200, json: async () => ({ state: PROJECT_A, revision: 'prA' }) }) })
      return server.fetchStub(url)
    }
    a.space.value = 'project'
    a.view.value = 'binding'
    const pending = a.settleInitialView()
    await new Promise(r => setTimeout(r, 0))
    a.space.value = 'ontology'
    a.view.value = 'objects'
    resolveState()
    await pending
    assert.equal(a.view.value, 'objects', '旧响应不得把用户抢回项目页')
    assert.equal(a.space.value, 'ontology')
  })

  await check('A7 项目加载失败只在项目上下文提示，不污染本体页', async (server, a) => {
    storage.clear()
    server.setFail('/api/project-state')
    a.space.value = 'project'
    a.view.value = 'binding'
    await a.settleInitialView()
    assert.equal(a.projectState.value, null)
    assert.match(a.projectLoadError.value, /服务不可用/)
    assert.equal(a.projectAreaFailed.value, true)
    assert.equal(a.view.value, 'p-home')
  })
} finally {
  rmSync(root, { recursive: true, force: true })
}

const failed = results.filter(r => !r.ok)
console.log(`\n${results.length - failed.length}/${results.length} 项通过`)
if (failed.length) process.exit(1)
