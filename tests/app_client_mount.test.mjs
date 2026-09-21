// R01 客户端挂载回归（验收修复批 2026-09-20）：
//   node --import ./tests/ts_hooks.mjs tests/app_client_mount.test.mjs
// 目的：用真正的 Vue 客户端 renderer（createRenderer）mount App.vue。
//   旧 SSR renderToString 路径不会触发 setup 顶层 watch 的 getter 求值，
//   掩盖了「watch 在 flowState 声明前读取它」的 TDZ 初始化异常。
// 覆盖：
//   ① 客户端 mount 成功，setup 完整执行（无 Cannot access 'flowState' before initialization）；
//   ② 依赖 flowState 的「顶栏检查结论新鲜度」watch 在客户端真实生效（配置变动后旧结论作废）；
//   ③ 后续初始化链（loadOntologyData/loadProject/validateProject）仍可正常推进，不因顺序调整回归。
// 不连接服务：fetch 全部由桩接管，且不写真实数据。
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { readFileSync, mkdtempSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve, dirname } from 'node:path'
import { pathToFileURL } from 'node:url'

const require = createRequire(new URL('../frontend/package.json', import.meta.url))
const ts = require('typescript')
const { createRenderer, nextTick } = require('vue')
const { parse, compileScript } = require('@vue/compiler-sfc')

const storage = new Map()
globalThis.__WIZ_SLOW_HINT_MS = 15
globalThis.addEventListener = () => {}
globalThis.removeEventListener = () => {}
globalThis.window = globalThis
globalThis.location = { search: '', hash: '', pathname: '/', port: '18765', origin: 'http://127.0.0.1:18765' }
globalThis.history = { replaceState() {} }
globalThis.innerWidth = 1280
globalThis.localStorage = { getItem: k => (storage.has(k) ? storage.get(k) : null), setItem: (k, v) => storage.set(k, String(v)), removeItem: k => storage.delete(k) }
globalThis.document = {
  addEventListener() {}, removeEventListener() {}, querySelector: () => null, querySelectorAll: () => [],
  title: '', activeElement: null, createElement: () => ({ click() {}, remove() {}, setAttribute() {}, style: {} }),
  body: { appendChild() {} },
}

const root = mkdtempSync(join(tmpdir(), 'wiz_app_mount_'))
const results = []
const settle = async () => { await new Promise(r => setImmediate(r)); await new Promise(r => setImmediate(r)) }
const ok = (data) => ({ ok: true, status: 200, json: async () => data })

async function check(name, fn) {
  try { await fn(); results.push({ name, ok: true }); console.log('通过：' + name) }
  catch (e) { results.push({ name, ok: false }); console.log('失败：' + name + ' — ' + e.message) }
}

// 子组件桩：被测目标是 App 自身的 setup/watch 行为，不渲染真实子页面。
const stubFile = join(root, 'stub.vue.mjs')
writeFileSync(stubFile, 'export default { setup() { return () => null } }\n')

async function loadSfc(path) {
  const filename = resolve(path)
  const { descriptor } = parse(readFileSync(filename, 'utf8'), { filename })
  let code = compileScript(descriptor, { id: 'app-client-mount' }).content
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
    projectId: id, name: '项目一', ontologyId: 'storage', ontologyVersion: '1.0.0',
    bindings: { object_bindings: [{ object_type: 'cluster', table: 'cluster_' + id, properties: {} }] },
  }
}

function harness() {
  const calls = []
  const stub = async (url, init) => {
    const path = String(url).split('?')[0]
    const body = init?.body ? JSON.parse(init.body) : null
    calls.push({ path, method: init?.method || 'GET', body })
    if (path === '/api/auth-state') return ok({ user: { username: 'tester', isAdmin: true, createdAt: '2026-09-20' } })
    if (path === '/api/state') return ok({ state: { ontology: { '@graph': [] }, workflow: {} }, revision: 'r0', latestVersion: '1.0.0' })
    if (path === '/api/ontologies') return ok({ items: [{ id: 'storage', name: '储能本体' }] })
    if (path === '/api/projects') return ok({ items: [{ id: 'p1', name: '项目一' }] })
    if (path === '/api/version-state') return ok({ state: { ontology: { '@graph': [] }, workflow: {} } })
    if (path === '/api/project-state') return ok({ state: projectStateOf('p1'), revision: 'pr-p1' })
    if (path === '/api/project-releases') return ok({ items: [] })
    if (path === '/api/project-validate') return ok({ errors: [], warnings: [], items: [] })
    if (path === '/api/versions') return ok({ items: [] })
    return { ok: false, status: 404, json: async () => ({ error: 'not found' }) }
  }
  return { calls, stub, countOf: p => calls.filter(c => c.path === p).length }
}

// 空渲染器：客户端 mount 但渲染函数为空，不需要真实 DOM。
const emptyRenderer = () => createRenderer({
  createElement: () => ({}), createText: () => ({}), createComment: () => ({}), insert() {}, remove() {},
  setText() {}, setElementText() {}, parentNode() {}, nextSibling() {}, patchProp() {},
})
const mounted = []
async function mountApp(h) {
  globalThis.fetch = h.stub
  const { Component, api } = await loadSfc('frontend/src/App.vue')
  const app = emptyRenderer().createApp(Component, {})
  app.mount({})           // 客户端 mount：setup 同步执行，TDZ 异常会在此抛出
  mounted.push(app)
  await settle()
  return { a: api(), app }
}

try {
  await check('① 客户端挂载 App 成功，setup 完整执行（无 flowState TDZ 初始化异常）', async () => {
    const h = harness()
    const { a, app } = await mountApp(h)   // mount 抛错会带 TDZ 原因进入失败分支
    assert.ok(app, 'App 实例已创建')
    assert.ok(a && a.flowState !== undefined, 'setup 完整执行并暴露内部状态句柄')
  })

  await check('② 依赖 flowState 的检查结论新鲜度 watch 在客户端真实生效', async () => {
    const h = harness()
    const { a } = await mountApp(h)
    // 复现「旧检查结论 + 编排未选中」：watch 必须把不再新鲜的检查结论作废。
    a.flowCheck.value = { errors: ['旧结论'] }
    a.flowCheckSig.value = 'sig-old'
    a.flowState.value = null
    await nextTick()
    assert.equal(a.flowCheck.value, null, '编排清空后旧检查结论必须作废（watch 生效）')
  })

  await check('③ 初始化链可用：加载本体/项目/校验仍正常，不因顺序调整回归', async () => {
    const h = harness()
    const { a } = await mountApp(h)
    await a.loadOntologyData()
    assert.equal(a.ontologyLoad.value, 'ready')
    assert.equal(await a.loadProject('p1', true), true)
    assert.equal(String(a.projectState.value.projectId), 'p1')
    await a.validateProject(false)
    assert.ok(h.countOf('/api/project-validate') >= 1, '校验请求已发出')
  })
} finally {
  for (const app of mounted) { try { app.unmount() } catch { /* 已卸载 */ } }
  try { rmSync(root, { recursive: true, force: true }) } catch { /* 临时目录 */ }
  const failed = results.filter(r => !r.ok).length
  console.log(`\napp_client_mount：${results.length - failed}/${results.length} 通过`)
  if (failed) process.exit(1)
}
