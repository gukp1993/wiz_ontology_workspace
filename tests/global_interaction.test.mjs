// G1/G2/G3（20260917 全局交互评审采纳）行为回归：本体必要读取状态机、重试不重复注册监听、
// 慢加载提示、Origin 拒绝的可读提示与本机地址，以及顶栏保存状态文案派生。
// 运行（仓库根）：node --import ./tests/ts_hooks.mjs tests/global_interaction.test.mjs
// 用真实 App.vue 的 <script setup>（子页面桩化）+ 受控 fetch，不连接服务、不写真实 ontology。
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { readFileSync, mkdtempSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { pathToFileURL } from 'node:url'

const require = createRequire(new URL('../frontend/package.json', import.meta.url))
const { parse, compileScript } = require('@vue/compiler-sfc')
const ts = require('typescript'), { createSSRApp, ref } = require('vue'), { renderToString } = require('@vue/server-renderer')

const storage = new Map()
let listenerLog = []
globalThis.__WIZ_SLOW_HINT_MS = 15 // 慢加载提示阈值注入（默认 3 秒）
globalThis.addEventListener = (t) => { listenerLog.push(t) }
globalThis.removeEventListener = () => {}
globalThis.window = globalThis
globalThis.location = { search: '', hash: '#objects', pathname: '/', port: '18765', origin: 'http://127.0.0.1:18765' }
globalThis.history = { replaceState() {} }
globalThis.localStorage = { getItem: k => (storage.has(k) ? storage.get(k) : null), setItem: (k, v) => storage.set(k, String(v)), removeItem: k => storage.delete(k) }
globalThis.document = { addEventListener() {}, removeEventListener() {}, querySelector: () => null, querySelectorAll: () => [], title: '' }

const root = mkdtempSync(join(tmpdir(), 'wiz_global_ui_'))
const SRC = resolve('frontend/src')

async function loadApp() {
  const filename = resolve('frontend/src/App.vue')
  const { descriptor } = parse(readFileSync(filename, 'utf8'), { filename })
  let code = compileScript(descriptor, { id: 'app-global-test' }).content
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

const OK_STATE = { state: { ontology: { '@graph': [] }, workflow: {} }, revision: 'r1', latestVersion: 'v1' }
function server(overrides = {}) {
  const calls = []
  const stub = async (url) => {
    const path = String(url).split('?')[0]
    calls.push(path)
    if (overrides[path]) return overrides[path]()
    if (path === '/api/state') return { ok: true, status: 200, json: async () => OK_STATE }
    if (path === '/api/ontologies') return { ok: true, status: 200, json: async () => ({ items: [{ id: 'storage', name: '储能本体' }] }) }
    if (path === '/api/projects') return { ok: true, status: 200, json: async () => ({ items: [] }) }
    return { ok: false, status: 404, json: async () => ({ error: 'not found' }) }
  }
  return { calls, stub, countOf: p => calls.filter(c => c === p).length }
}

const results = []
async function check(name, fn) {
  const s = server()
  globalThis.fetch = s.stub
  const app = createSSRApp((await loadApp()).Component, {})
  const { Component, api } = await loadApp()
  const inst = createSSRApp(Component, {})
  await renderToString(inst)
  try { await fn(s, api()); results.push({ name, ok: true }); console.log('通过：' + name) }
  catch (e) { results.push({ name, ok: false }); console.log('失败：' + name + ' — ' + e.message) }
  void app
}

const sleep = ms => new Promise(r => setTimeout(r, ms))

try {
  // ① 本体读取失败：进入 error 态（不是「没有本体」），错误可读、可重试
  await check('① 本体必要读取失败进 error 态且不渲染成没有本体', async (s, a) => {
    s.stub // 默认成功；本用例改用失败响应
    globalThis.fetch = async (url) => {
      const p = String(url).split('?')[0]
      if (p === '/api/state') return { ok: false, status: 500, json: async () => ({ error: '读取草稿失败' }) }
      return s.stub(url)
    }
    await a.loadOntologyData()
    assert.equal(a.ontologyLoad.value, 'error')
    assert.equal(a.state.value, null)
    assert.match(a.ontologyLoadError.value, /读取草稿失败/)
    assert.equal(a.ontologyLoadFailure.value, 'http')
    assert.equal(a.hasOntology.value, true, '本体列表已读到：不能误判为没有本体')
    assert.equal(a.ontologyList.value.length, 1, '本体列表读取不能被草稿失败连带跳过')
    // 重试成功 → ready，且请求确实重新发起
    globalThis.fetch = s.stub
    const before = s.countOf('/api/state')
    await a.retryOntologyLoad()
    assert.equal(a.ontologyLoad.value, 'ready')
    assert.ok(s.countOf('/api/state') > before, '重试必须重新读取')
  })

  // ② 重试不重复注册全局监听器
  await check('② 重试不重复注册事件监听器', async () => {
    listenerLog = []
    const { Component, api } = await loadApp()
    const inst = createSSRApp(Component, {})
    await renderToString(inst)
    const a = api()
    a.bindGlobals(); a.bindGlobals()
    const afterBind = [...listenerLog]
    assert.equal(afterBind.filter(t => t === 'keydown').length, 1, 'keydown 只能注册一次：' + JSON.stringify(afterBind))
    assert.equal(afterBind.filter(t => t === 'hashchange').length, 1, 'hashchange 只能注册一次')
    await a.retryOntologyLoad(); await a.retryOntologyLoad()
    assert.deepEqual(listenerLog, afterBind, '重试不得新增任何全局监听器：' + JSON.stringify(listenerLog))
  })

  // ③ 慢加载提示：阈值内出现「仍在加载」提示
  await check('③ 超过阈值仍未返回时给出仍在加载提示', async () => {
    let release = null
    globalThis.fetch = async (url) => {
      const p = String(url).split('?')[0]
      if (p === '/api/state') return new Promise(r => { release = () => r({ ok: true, status: 200, json: async () => OK_STATE }) })
      return { ok: true, status: 200, json: async () => ({ items: [] }) }
    }
    const { Component, api } = await loadApp()
    const inst = createSSRApp(Component, {})
    await renderToString(inst)
    const a = api()
    const pending = a.loadOntologyData()
    assert.equal(a.ontologySlow.value, false)
    await sleep(40)
    assert.equal(a.ontologySlow.value, true, '超过阈值应给「仍在加载，请稍候」')
    release()
    await pending
    assert.equal(a.ontologySlow.value, false, '加载结束后提示移除')
    assert.equal(a.ontologyLoad.value, 'ready')
  })

  // ④ 读取超时：failure=timeout，提示文案按超时区分（并给重试）
  await check('④ 读取超时进 error 态并区分超时文案', async () => {
    globalThis.__WIZ_READ_TIMEOUT_MS = 40
    globalThis.fetch = (url, init) => {
      const p = String(url).split('?')[0]
      if (p === '/api/state') return new Promise((_res, rej) => init.signal.addEventListener('abort', () => rej(Object.assign(new Error('aborted'), { name: 'AbortError' }))))
      return Promise.resolve({ ok: true, status: 200, json: async () => ({ items: [] }) })
    }
    const { Component, api } = await loadApp()
    const inst = createSSRApp(Component, {})
    await renderToString(inst)
    const a = api()
    await a.loadOntologyData()
    assert.equal(a.ontologyLoad.value, 'error')
    assert.equal(a.ontologyLoadFailure.value, 'timeout')
    assert.match(a.ontologyFailureHint.value, /15 秒|超时/)
    delete globalThis.__WIZ_READ_TIMEOUT_MS
  })

  // ⑤ Origin 被拒（403）：可读提示 + 按当前端口推导的本机地址，不写死端口
  await check('⑤ Origin 被拒时给出本机标准地址提示', async () => {
    globalThis.fetch = async (url) => {
      const p = String(url).split('?')[0]
      if (p === '/api/state') return { ok: false, status: 403, json: async () => ({ error: '请求来源不允许' }) }
      return { ok: true, status: 200, json: async () => ({ items: [] }) }
    }
    const { Component, api } = await loadApp()
    const inst = createSSRApp(Component, {})
    await renderToString(inst)
    const a = api()
    await a.loadOntologyData()
    assert.equal(a.ontologyOriginBlocked.value, true)
    assert.match(a.ontologyFailureHint.value, /未通过服务校验/)
    assert.equal(a.localAccess.value, 'http://127.0.0.1:18765/#objects', '地址必须按当前端口推导：' + a.localAccess.value)
  })

  // ⑥ G3：顶栏文案按真实 guard dirty + Saver 状态派生
  await check('⑥ 顶栏保存状态文案与真实 dirty／Saver 状态一致', async () => {
    const dirtyRef = ref(false)
    const s = server()
    globalThis.fetch = s.stub
    const { Component, api } = await loadApp()
    const inst = createSSRApp(Component, {})
    await renderToString(inst)
    const a = api()
    a.space.value = 'ontology'
    await a.loadOntologyData()
    a.state.value = { ontology: { '@graph': [] }, workflow: {} }
    // ①没有表单：已保存
    assert.equal(a.saveState.value.text, '已保存')
    const guardRef = { isDirty: () => dirtyRef.value, discard() {} }
    a.formGuardApi.register(guardRef)
    // ②打开表单但内容未改：编辑表单（不能标成有未保存修改）
    assert.equal(a.formEditing.value, true)
    assert.equal(a.formDirty.value, false)
    assert.equal(a.saveState.value.text, '编辑表单 · 草稿已保存')
    assert.notEqual(a.saveState.value.kind, 'form')
    // ③表单有未提交修改：明确说未保存，不再并列无范围的绿色「已保存」
    dirtyRef.value = true
    assert.equal(a.saveState.value.text, '表单有未保存修改')
    assert.equal(a.saveState.value.kind, 'form')
    // ④工作区草稿也未保存：两句都要说清
    a.ontologySaver.status.value = 'dirty'
    assert.match(a.saveState.value.text, /工作区草稿有未保存修改；表单修改尚未提交/)
    // ⑤还原输入：不滞留未保存提示
    dirtyRef.value = false
    assert.equal(a.saveState.value.text, '未保存修改')
    // ⑥保存失败（结果未知）优先于表单提示，且不谎报已保存
    a.ontologySaver.status.value = 'error'
    a.ontologySaver.error.value = '未收到保存结果，暂不能确认是否成功'
    a.ontologySaver.unknownOutcome.value = true
    dirtyRef.value = true
    assert.equal(a.saveState.value.kind, 'error')
    assert.match(a.saveState.value.text, /未收到保存结果/)
    // ⑦服务端明确拒绝：保留服务端原因
    a.ontologySaver.unknownOutcome.value = false
    a.ontologySaver.error.value = '草稿校验未通过'
    assert.equal(a.saveState.value.text, '保存失败')
    assert.equal(a.saveState.value.hint, '草稿校验未通过')
    // ⑧冲突优先
    a.ontologySaver.status.value = 'conflict'
    assert.equal(a.saveState.value.text, '版本冲突')
    // ⑨保存中
    a.ontologySaver.status.value = 'saving'
    assert.match(a.saveState.value.text, /请勿重复操作/)
    a.saveSlow.value = true
    assert.match(a.saveState.value.text, /响应较慢，正在等待结果/)
    a.saveSlow.value = false
    // ⑩取消/保存成功后关闭表单：回到已保存
    a.ontologySaver.status.value = 'saved'
    dirtyRef.value = false
    a.formGuardApi.unregister(guardRef)
    assert.equal(a.formDirty.value, false)
    assert.equal(a.saveState.value.text, '已保存')
  })

  // ⑦ G3：打开表单但未修改时显示「编辑表单」，不等于未保存
  await check('⑦ 表单打开未改时显示编辑表单而非未保存', async () => {
    const src = readFileSync(resolve('frontend/src/App.vue'), 'utf8')
    assert.ok(/formDirty\.value\) return \{ kind: 'form'/.test(src), '需要按真实 dirty 派生表单状态')
    assert.ok(/kind: 'editing', text: '编辑表单 · 草稿已保存'/.test(src), '打开未改需明确为编辑表单')
    assert.ok(!/✎ 正在编辑表单/.test(src), '不再单独并列易误读的编辑中标签')
  })
} finally {
  rmSync(root, { recursive: true, force: true })
}

const failed = results.filter(r => !r.ok)
console.log(`\n${results.length - failed.length}/${results.length} 项通过`)
if (failed.length) process.exit(1)
