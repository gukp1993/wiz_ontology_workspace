// T20 / P06（F07）项目发布幂等与失败语义回归：不连接服务、不写真实 ontology。
//   node --import ./tests/ts_hooks.mjs tests/project_publish_receipt.test.mjs
// 覆盖（冻结协议 03 分册 §2.3）：
//   ① commitNow 之后保存未成功（conflict/error/dirty/saving）不得继续发布；
//   ② 结果未知（超时/网络）的重试：同内容沿用同 requestId，服务端只产生一个版本；
//   ③ 项目内容变化后必须换新 requestId（revision 推进不换 key：成功发布本身推进 revision，
//      响应丢失的重试必须仍能回放，见 ⑧）；
//   ④ 成功、422 后换新 key；DEPENDENCY_CHANGED 清 key 且旧检查结果作废（emit stale）；
//   ⑤ 检查结果过期（reportStale）时不得发布。
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { readFileSync, mkdtempSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve, dirname } from 'node:path'
import { pathToFileURL } from 'node:url'

const require = createRequire(new URL('../frontend/package.json', import.meta.url))
const ts = require('typescript')
const { createRenderer, reactive } = require('vue')
const { parse, compileScript } = require('@vue/compiler-sfc')

const storage = new Map()
globalThis.addEventListener = () => {}
globalThis.removeEventListener = () => {}
globalThis.window = globalThis
globalThis.location = { search: '', hash: '#p-release', pathname: '/', port: '18765', origin: 'http://127.0.0.1:18765' }
globalThis.history = { replaceState() {} }
globalThis.localStorage = { getItem: k => (storage.has(k) ? storage.get(k) : null), setItem: (k, v) => storage.set(k, String(v)), removeItem: k => storage.delete(k) }
globalThis.document = { addEventListener() {}, removeEventListener() {}, querySelector: () => null, querySelectorAll: () => [], title: '' }

const root = mkdtempSync(join(tmpdir(), 'wiz_publish_receipt_'))
const results = []
const settle = async () => { await new Promise(r => setImmediate(r)); await new Promise(r => setImmediate(r)) }
const { SaveRequestError } = await import(pathToFileURL(resolve('frontend/src/app/http.ts')).href)

async function check(name, fn) {
  try { await fn(); results.push({ name, ok: true }); console.log('通过：' + name) }
  catch (e) { results.push({ name, ok: false }); console.log('失败：' + name + ' — ' + e.message) }
}

// ── 被测组件的模块桩：project/api、ontology/api 用受控实现；.vue 子组件不加载 ──
const stubApiFile = join(root, 'projectApiStub.mjs')
writeFileSync(stubApiFile, [
  'export const listProjectReleases = (id) => globalThis.__pubApi.listProjectReleases(id)',
  'export const loadProjectStateRaw = (id) => globalThis.__pubApi.loadProjectStateRaw(id)',
  'export const projectPost = (path, payload) => globalThis.__pubApi.projectPost(path, payload)',
].join('\n'))
const stubOntologyApiFile = join(root, 'ontologyApiStub.mjs')
writeFileSync(stubOntologyApiFile, 'export const versionStateRaw = (id,v) => globalThis.__pubApi.versionStateRaw(id,v)\n')

const emptyRenderer = () => createRenderer({
  createElement: () => ({}), createText: () => ({}), createComment: () => ({}), insert() {}, remove() {},
  setText() {}, setElementText() {}, parentNode() {}, nextSibling() {}, patchProp() {},
})
const mounted = []
async function loadValidation(propsOverrides = {}) {
  const filename = resolve('frontend/src/project/ProjectValidation.vue')
  const { descriptor } = parse(readFileSync(filename, 'utf8'), { filename })
  let code = compileScript(descriptor, { id: 'publish-receipt' }).content
  code = code.replace(/import\s+(\w+)\s+from\s+['"][^'"]*\.vue['"]/g, 'const $1 = {}')
  code = code.replace(/from (['"])([^'"]+)\1/g, (_, q, spec) => {
    if (spec === './api') return 'from ' + JSON.stringify(pathToFileURL(stubApiFile).href)
    if (spec === '../ontology/api') return 'from ' + JSON.stringify(pathToFileURL(stubOntologyApiFile).href)
    const target = spec === 'vue' ? require.resolve('vue') : resolve(dirname(filename), spec + '.ts')
    return 'from ' + JSON.stringify(pathToFileURL(target).href)
  })
  const file = join(root, 'ProjectValidation.mjs')
  writeFileSync(file, ts.transpileModule(code, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText)
  const Component = (await import(pathToFileURL(file).href + '?t=' + Date.now())).default
  const setup = Component.setup
  const events = []
  let api = null
  Component.setup = (props, ctx) => {
    const wrapped = { ...ctx, emit: (name, ...args) => { events.push(name); return ctx.emit(name, ...args) } }
    api = setup(props, wrapped)
    return api
  }
  Component.render = () => null
  const props = reactive({
    report: { errors: [], warnings: [], items: [] }, busy: false,
    projectState: { projectId: 'p1', name: '项目一', ontologyId: 'storage', ontologyVersion: '1.0.0' },
    validateError: '', reportStale: false, ...propsOverrides,
  })
  const app = emptyRenderer().createApp(Component, props)
  const provided = globalThis.__provide
  app.provide('form-guard', provided.guard)
  app.provide('form-save', { async submitForm() { return { ok: true, message: '' } } })
  app.provide('commit-now', provided.commitNow)
  app.mount({})
  mounted.push(app)
  await settle()
  return { api, props, events }
}

/** 受控发布端点：记录每次 payload；respond 决定本轮响应。 */
function backend(respond) {
  const calls = { publish: [], stateReads: 0 }
  let serverState = { projectId: 'p1', name: '项目一', ontologyId: 'storage', ontologyVersion: '1.0.0', bindings: { object_bindings: [] } }
  let serverRevision = 'r1'
  return {
    calls,
    setServer(next, revision) { serverState = next; if (revision) serverRevision = revision },
    api: {
      async listProjectReleases() { return { items: [] } },
      async loadProjectStateRaw() { calls.stateReads++; return { state: JSON.parse(JSON.stringify(serverState)), revision: serverRevision } },
      async projectPost(path, payload) { calls.publish.push({ path, payload }); return respond(path, payload, calls) },
      async versionStateRaw() { return { state: { ontology: { '@graph': [] }, workflow: {} } } },
    },
  }
}

function guardStub(statusRef) {
  return {
    register() {}, unregister() {}, hasDirty: () => false, editing: () => false,
    projectSaveStatus: () => statusRef.value,
  }
}
const { ref } = require('vue')

try {
  await check('① 保存未成功不得继续发布（conflict/error/dirty/saving 全部拦住）', async () => {
    const status = ref('saved')
    globalThis.__provide = { guard: guardStub(status), commitNow: async () => { /* 模拟 commitNow 完成但保存失败 */ } }
    const be = backend(() => { throw new Error('不应发出发布请求') })
    globalThis.__pubApi = be.api
    const { api } = await loadValidation()
    for (const bad of ['conflict', 'error', 'dirty', 'saving']) {
      status.value = bad
      api.publishError.value = ''
      await api.doPublish()
      assert.equal(be.calls.publish.length, 0, `${bad} 状态不得继续发布`)
      assert.ok(api.publishError.value, `${bad} 状态必须给出可见原因`)
      assert.equal(api.lastPublish.value, '', '不得显示发布成功')
    }
    // 保存确认成功（saved）才放行：同组件切换状态后发布真正发出
    status.value = 'saved'
    const okBackend = backend(() => ({ revision: 'r2', version: 'v1' }))
    globalThis.__pubApi = okBackend.api
    await api.doPublish()
    assert.equal(okBackend.calls.publish.length, 1, 'saved 状态放行发布')
    assert.equal(api.lastPublish.value, 'v1')
  })

  await check('② 结果未知（超时）重试沿用同 requestId；同内容只提交一个 key', async () => {
    const status = ref('saved')
    globalThis.__provide = { guard: guardStub(status), commitNow: async () => {} }
    let attempt = 0
    const be = backend((path) => {
      if (path !== 'project-publish') throw new Error('unexpected ' + path)
      attempt++
      if (attempt === 1) throw new SaveRequestError('读取超时', 0, null, null, 'timeout')
      return { revision: 'r2', version: 'v1' }
    })
    globalThis.__pubApi = be.api
    const { api } = await loadValidation()
    await api.doPublish()
    assert.equal(api.publishUnknown.value, true, '超时不能断言未写入')
    assert.match(api.publishError.value, /未收到发布结果/)
    assert.ok(api.pendingPublish.value, '结果未知：key 保留')
    const firstKey = be.calls.publish[0].payload.requestId
    // 内容与 revision 未变 → 重试沿用同 key
    await api.doPublish()
    assert.equal(be.calls.publish[1].payload.requestId, firstKey, '同内容重试必须复用同 requestId')
    assert.equal(api.lastPublish.value, 'v1')
    assert.equal(api.pendingPublish.value, null, '成功后台账清空')
  })

  await check('③ 项目内容变化后必须换新 requestId（revision 推进不换 key）', async () => {
    const status = ref('saved')
    globalThis.__provide = { guard: guardStub(status), commitNow: async () => {} }
    let attempt = 0
    const be = backend(() => {
      attempt++
      if (attempt === 1) throw new SaveRequestError('网络异常，无法连接服务', 0, null, null, 'network')
      return { revision: 'r9', version: 'v2' }
    })
    globalThis.__pubApi = be.api
    const { api } = await loadValidation()
    await api.doPublish()
    const firstKey = be.calls.publish[0].payload.requestId
    assert.equal(api.pendingPublish.value.id, firstKey)
    // 用户在重试前改了项目配置（服务器草稿/revision 已变）
    be.setServer({ projectId: 'p1', name: '项目一', ontologyId: 'storage', ontologyVersion: '1.0.0', bindings: { object_bindings: [{ object_type: 'cluster', table: 't2', properties: {} }] } }, 'r2')
    await api.doPublish()
    const secondKey = be.calls.publish[1].payload.requestId
    assert.notEqual(secondKey, firstKey, 'payload 变化必须换新 key（同 key 异内容会被 409）')
    assert.equal(api.pendingPublish.value, null)
  })

  await check('④ 422：换新 key、展示服务端报告；不显示成功', async () => {
    const status = ref('saved')
    globalThis.__provide = { guard: guardStub(status), commitNow: async () => {} }
    const be = backend(() => { throw new SaveRequestError('项目配置校验未通过：x', 422, null, { error: '项目配置校验未通过：x', report: { errors: ['x'], warnings: [], items: [] } }, 'http') })
    globalThis.__pubApi = be.api
    const { api } = await loadValidation()
    await api.doPublish()
    assert.equal(api.pendingPublish.value, null, '422 是确定未发布：清 key')
    assert.equal(api.publishUnknown.value, false)
    assert.equal(api.lastPublish.value, '')
    assert.equal(api.publishReport.value.errors.length, 1, '保留服务端返回的报告')
    assert.match(api.publishError.value, /校验未通过/)
  })

  await check('⑤ DEPENDENCY_CHANGED：清 key、报告作废、不显示成功', async () => {
    const status = ref('saved')
    globalThis.__provide = { guard: guardStub(status), commitNow: async () => {} }
    const be = backend(() => { throw new SaveRequestError('发布提交前检测到依赖发生变化', 409, 'r3', { error: '发布提交前检测到依赖发生变化（编排或目录已更新），请重新检查后再发布', code: 'REVISION_CONFLICT', reason: 'DEPENDENCY_CHANGED', currentRevision: 'r3' }, 'http') })
    globalThis.__pubApi = be.api
    const { api, events } = await loadValidation()
    await api.doPublish()
    assert.equal(api.pendingPublish.value, null, '依赖变化：结果确定未发布，清 key')
    assert.equal(api.publishUnknown.value, false, '不是「结果未知」，不得提示去核对已发布版本')
    assert.equal(api.lastPublish.value, '', '不得当作发布成功')
    assert.ok(events.includes('stale'), '依赖变化必须通知 App 作废旧检查结果')
    assert.match(api.publishError.value, /依赖|重新检查/)
  })

  await check('⑥ 检查结果过期（reportStale）时不得发布', async () => {
    const status = ref('saved')
    globalThis.__provide = { guard: guardStub(status), commitNow: async () => {} }
    const be = backend(() => { throw new Error('不应发出发布请求') })
    globalThis.__pubApi = be.api
    const { api } = await loadValidation({ reportStale: true })
    assert.equal(api.checkReady.value, false, '过期报告不构成发布依据')
    await api.doPublish()
    assert.equal(be.calls.publish.length, 0)
    assert.match(api.publishError.value, /重新检查/)
  })

  await check('⑧ 内容不变、revision 推进（发布成功但响应丢失）→ 重试复用同 key', async () => {
    const status = ref('saved')
    globalThis.__provide = { guard: guardStub(status), commitNow: async () => {} }
    let attempt = 0
    const be = backend(() => {
      attempt++
      if (attempt === 1) throw new SaveRequestError('未收到发布结果', 0, null, null, 'timeout')
      return { revision: 'r9', version: 'v1' }
    })
    globalThis.__pubApi = be.api
    const { api } = await loadValidation()
    await api.doPublish()
    const firstKey = be.calls.publish[0].payload.requestId
    assert.equal(api.pendingPublish.value.id, firstKey, '结果未知必须保留 key')
    // 那次发布其实已成功：服务端草稿 revision 已前移，但**内容未变**
    be.setServer(be.calls.lastState || { projectId: 'p1', name: '项目一', ontologyId: 'storage', ontologyVersion: '1.0.0', bindings: { object_bindings: [] } }, 'r-advanced-by-first-success')
    await api.doPublish()
    assert.equal(be.calls.publish[1].payload.requestId, firstKey,
      '内容不变时重试必须复用同 key（否则服务端看不到回执会重复出版本）')
    assert.equal(api.lastPublish.value, 'v1')
  })

  await check('⑦ 未打开表单但有未保存编辑：commitNow 后保存状态仍为 saved 才发布', async () => {
    const status = ref('saved')
    let committed = 0
    globalThis.__provide = { guard: guardStub(status), commitNow: async () => { committed++; status.value = 'saved' } }
    const be = backend(() => ({ revision: 'r2', version: 'v3' }))
    globalThis.__pubApi = be.api
    const { api, events } = await loadValidation()
    await api.doPublish()
    assert.equal(committed, 1, '发布前必须先落盘（commitNow）')
    assert.equal(be.calls.publish.length, 1)
    assert.equal(api.lastPublish.value, 'v3')
    assert.ok(events.includes('published'), '成功后通知 App 刷新')
    assert.equal(api.publishUnknown.value, false)
  })
} finally {
  for (const app of mounted) { try { app.unmount() } catch { /* 已卸载 */ } }
  delete globalThis.__pubApi
  delete globalThis.__provide
  rmSync(root, { recursive: true, force: true })
}

const failed = results.filter(r => !r.ok)
console.log(`\n${results.length - failed.length}/${results.length} 项通过`)
if (failed.length) process.exit(1)
