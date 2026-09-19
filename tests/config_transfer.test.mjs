// 配置迁移前端（20260919 需求 T4）回归：导航注册、api 层分片行为、页面渲染与红线。
// 运行（仓库根）：node --import ./tests/ts_hooks.mjs tests/config_transfer.test.mjs
import assert from 'node:assert/strict'
import { pathToFileURL } from 'node:url'
import { createRequire } from 'node:module'
import { readFileSync, mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'

const require = createRequire(new URL('../frontend/package.json', import.meta.url))
const ts = require('typescript'), { createSSRApp } = require('vue'), { renderToString } = require('@vue/server-renderer')
const { parse, compileScript } = require('@vue/compiler-sfc')

const storage = new Map()
globalThis.addEventListener = () => {}
globalThis.removeEventListener = () => {}
globalThis.window = globalThis
globalThis.location = { search: '', hash: '#settings-transfer' }
globalThis.history = { replaceState() {} }
globalThis.localStorage = { getItem: k => (storage.has(k) ? storage.get(k) : null), setItem: (k, v) => storage.set(k, String(v)), removeItem: k => storage.delete(k) }
globalThis.document = { addEventListener() {}, removeEventListener() {}, querySelector: () => null, querySelectorAll: () => [], title: '', createElement: () => ({ click() {}, remove() {}, setAttribute() {} }), body: { appendChild() {} } }

const root = mkdtempSync(join(tmpdir(), 'wiz_config_transfer_'))

const results = []
async function check(name, fn) {
  try { await fn(); results.push({ name, ok: true }); console.log('通过：' + name) }
  catch (e) { results.push({ name, ok: false }); console.log('失败：' + name + ' — ' + e.message) }
}

const calls = []
let responder = () => ({ ok: true, status: 200 })
const httpModuleFile = join(root, 'httpmock.mjs')
writeFileSync(httpModuleFile, ts.transpileModule(
  readFileSync(resolve('frontend/src/app/http.ts'), 'utf8'),
  { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText)

/** 加载 http 模块并替换全局 fetch（mock 保持到测试进程结束；调用记录在 calls）。 */
async function loadHttp() {
  globalThis.fetch = async (url, init) => {
    calls.push({ url: String(url), body: init?.body ? JSON.parse(init.body) : null, method: init?.method })
    return responder(url, init)
  }
  return await import(pathToFileURL(httpModuleFile).href)
}

function jsonOk(data) { return { ok: true, status: 200, json: async () => data } }

async function loadSfc(path, moduleOverrides) {
  const filename = resolve(path)
  const raw = readFileSync(filename, 'utf8')
  let code
  if (filename.endsWith('.vue')) {
    const { descriptor } = parse(raw, { filename })
    code = compileScript(descriptor, { id: 'ct-test' }).content
  } else {
    code = raw
  }
  code = code.replace(/from (['"])([^'"]+)\1/g, (_, q, spec) => {
    if (moduleOverrides[spec]) return 'from ' + JSON.stringify(moduleOverrides[spec])
    const target = spec === 'vue' ? require.resolve('vue') : resolve('frontend/src', spec + '.ts')
    return 'from ' + JSON.stringify(pathToFileURL(target).href)
  })
  const file = join(root, path.replace(/[^\w]/g, '_') + '.mjs')
  writeFileSync(file, ts.transpileModule(code, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText)
  return await import(pathToFileURL(file).href)
}

// ── ① 导航注册 ──
await check('① settings-transfer 注册：白名单/设置分类/全局视图', async () => {
  const nav = await import(pathToFileURL(resolve('frontend/src/app/navigation.ts')).href)
  assert.equal(nav.normalizeView('settings-transfer'), 'settings-transfer')
  assert.ok('settings-transfer' in nav.pages)
  assert.ok(nav.isGlobalView('settings-transfer'))
  assert.ok(!nav.projectSpaceViews.includes('settings-transfer'))
  const cat = nav.settingsCategories.find(c => c.id === 'settings-transfer')
  assert.ok(cat, '设置分类注册')
  assert.equal(cat.group, '数据管理')
})

// ── ② transferApi：分片切片与 hash ──
await check('② stageUpload 分片切片/base64/sha256 分片哈希', async () => {
  await loadHttp()
  const api = await loadSfc('frontend/src/settings/transferApi.ts', { '../app/http': httpModuleFile })
  calls.length = 0
  responder = (_url, init) => {
    const body = JSON.parse(init.body)
    if (body.action === 'begin') return jsonOk({ uploadId: 'up-1', chunkSize: 4, totalChunks: 2 })
    return jsonOk({ received: body.index, totalChunks: 2 })
  }
  const bytes = [1, 2, 3, 4, 5, 6]
  const file = { name: '包.zip', size: 6,
    slice: (a, b) => ({ arrayBuffer: async () => new Uint8Array(bytes.slice(a, b)).buffer }) }
  const uploadId = await api.stageUpload(file)
  assert.equal(uploadId, 'up-1')
  assert.equal(calls.length, 3, 'begin + 2 chunks')
  assert.equal(calls[0].body.action, 'begin')
  assert.equal(calls[1].body.index, 0)
  assert.equal(calls[2].body.index, 1)
  assert.equal(calls[1].body.base64, Buffer.from([1, 2, 3, 4]).toString('base64'))
  assert.match(calls[1].body.chunkHash, /^[0-9a-f]{64}$/)
})

// ── ③ importConfirm：请求体含 requestId 与 nameOverrides ──
await check('③ importConfirm 请求体结构', async () => {
  await loadHttp()
  const api = await loadSfc('frontend/src/settings/transferApi.ts', { '../app/http': httpModuleFile })
  calls.length = 0
  responder = () => jsonOk({ receiptId: 'rcpt-1', requestId: 'req-1', assets: [], pendingCredentials: [], warnings: [] })
  await api.importConfirm('pv-1', 'req-1', { m1: '自定义名' })
  assert.equal(calls[0].url.endsWith('/api/config-package-import'), true)
  assert.equal(calls[0].body.requestId, 'req-1')
  assert.deepEqual(calls[0].body.nameOverrides, { m1: '自定义名' })
})

// ── ④ 页面渲染：双页签/主按钮/固定横幅 ──
await check('④ 页面渲染：导出/导入双页签与「不会覆盖」横幅', async () => {
  const stubPath = await stubTransferApi()
  const mod = await loadSfc('frontend/src/settings/ConfigurationTransfer.vue', {
    './transferApi': stubPath,
    '../shared/AppError.vue': await stubAppError(),
  })
  const comp = mod.default
  // mapping_forms 同款 SSR 模式：compileTemplate(ssr:true) + script.bindings 产 ssrRender
  const { parse: sfcParse, compileTemplate } = require('@vue/compiler-sfc')
  const { descriptor } = sfcParse(readFileSync(resolve('frontend/src/settings/ConfigurationTransfer.vue'), 'utf8'), { filename: 'ConfigurationTransfer.vue' })
  const script = compileScript(descriptor, { id: 'ct-page' })
  const template = compileTemplate({ source: descriptor.template.content, filename: 'ct.vue', id: 'ct',
                                     ssr: true, ssrCssVars: [],
                                     compilerOptions: { bindingMetadata: script.bindings } })
  assert.deepEqual(template.errors, [])
  const resolvedVue = pathToFileURL(resolve('frontend/node_modules/vue/dist/vue.runtime.esm-bundler.js')).href
  const resolvedSsr = pathToFileURL(resolve('frontend/node_modules/@vue/server-renderer/dist/server-renderer.esm-bundler.js')).href
  const renderCode = template.code
    .replace(/from ["']vue\/server-renderer["']/g, `from ${JSON.stringify(resolvedSsr)}`)
    .replace(/from ["']vue["']/g, `from ${JSON.stringify(resolvedVue)}`)
  const renderFile = join(root, 'ct_ssr_render.mjs')
  writeFileSync(renderFile, ts.transpileModule(renderCode, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText)
  const renderMod = await import(pathToFileURL(renderFile).href)
  comp.ssrRender = renderMod.ssrRender
  const html = await renderToString(createSSRApp(comp, { preselect: {} }))
  assert.ok(html.includes('导出配置') && html.includes('导入配置'), '双页签')
  assert.ok(html.includes('查看导出内容'), '导出主按钮')
  assert.ok(html.includes('导入配置'), '导入页签按钮（初始页签为导出，导入区不渲染）')
  assert.ok(html.includes('冻结快照'), '预览冻结说明')
})

// ── ⑤ 无覆盖/合并类选项（需求红线）──
await check('⑤ 除固定横幅外无覆盖/合并/同步类文案', async () => {
  const src = readFileSync(resolve('frontend/src/settings/ConfigurationTransfer.vue'), 'utf8')
  const stripped = src.replaceAll('不会覆盖已有内容', '').replace(/每次主动导入都新建资产；无覆盖\/合并选项（需求红线）。/, '')
  assert.ok(!/覆盖|合并|同步/.test(stripped), '发现违禁文案')
})

async function stubTransferApi() {
  const file = join(root, 'transferApi_stub.mjs')
  writeFileSync(file, ts.transpileModule(`
export function exportPreview() { return Promise.resolve({ exportToken: null, assets: { models: [], projects: [], flows: [], modelConfigs: [] }, dependencyEdges: [], warnings: [], blockers: [] }) }
export async function exportDownload() {}
export async function stageUpload() { return 'up-stub' }
export function importPreview() { return Promise.resolve({ previewToken: 'pv', packageHash: 'h', assets: [], modelConfigs: [], warnings: [], blockers: [] }) }
export function importConfirm() { return Promise.resolve({ receiptId: 'r', requestId: 'q', assets: [], pendingCredentials: [], warnings: [] }) }
export function importResult() { return Promise.resolve({ status: 'none' }) }
export function discard() { return Promise.resolve({ ok: true }) }
export function conflictSuggestions() { return [] }
`, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText)
  return file
}

async function stubAppError() {
  const file = join(root, 'apperror_stub.mjs')
  writeFileSync(file, ts.transpileModule(`
export default { props: ['title', 'reason', 'hint', 'retryLabel'], template: '<div class="stub-error">{{title}}</div>' }
`, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText)
  return file
}

const failed = results.filter(r => !r.ok)
console.log(`\n${results.length - failed.length}/${results.length} 项通过`)
if (failed.length) process.exit(1)
