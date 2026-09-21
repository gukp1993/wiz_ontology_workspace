// D05/R3-04（20260920 从物料自动构建本体）：候选属性字段（dataType/valueType）平铺口径回归
//   + A01「查看已创建本体」回链（B.4）。
// 运行（仓库根）：node --import ./tests/ts_hooks.mjs tests/ontology_build_review_edit.test.mjs
// 契约：后端 review.update_candidate 只接受**平铺** fields.dataType（字符串枚举）与
//   fields.valueType（仅 timeSeries，取值 = protocol.VALUE_TYPES = model_format.SERIES_VALUE_TYPES）；
//   嵌套对象 {type:'timeSeries',valueType:'double'} 会被拒为「不支持的数据类型」。
// 本测试不联网、不写库：
//   ① 纯函数（candidateFields.ts）枚举与读写口径；
//   ② 真实 BuildReviewPage.vue 的 <script setup>（不再渲染模板）：读取回填 + 保存载荷真实抓取；
//   ③ 真实 App.vue 的 enterBuildOntology（子页面桩化）：切本体 / 同本体 / 缺 id 三条分支；
//   ④ 源文本断言：页面不再自带类型表、不回退嵌套载荷，任务行回链带本体 id。
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { readFileSync, mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { pathToFileURL } from 'node:url'

const require = createRequire(new URL('../frontend/package.json', import.meta.url))
const { parse, compileScript } = require('@vue/compiler-sfc')
const ts = require('typescript'), { createSSRApp } = require('vue'), { renderToString } = require('@vue/server-renderer')

const SRC = resolve('frontend/src')
const results = []
async function check(name, fn) {
  try { await fn(); results.push({ name, ok: true }); console.log('通过：' + name) }
  catch (e) { results.push({ name, ok: false }); console.log('失败：' + name + ' — ' + (e && e.message)) }
  finally { globalThis.fetch = realFetch }
}
function report() {
  const failed = results.filter(r => !r.ok)
  console.log(failed.length ? '失败 ' + failed.length + '/' + results.length + ' 项' : '全部通过：' + results.length + ' 项。')
  if (failed.length) process.exitCode = 1
}

// ── 运行环境桩：SFC 编译产物与 app/* 模块都需要浏览器全局（不连服务） ─────────────
const storage = new Map()
const assignCalls = [], replaceCalls = []
const fakeEl = () => ({
  style: {}, dataset: {}, classList: { add() {}, remove() {} },
  setAttribute() {}, appendChild() {}, addEventListener() {},
  querySelector: () => null, querySelectorAll: () => [], focus() {}, remove() {},
})
globalThis.window = globalThis
globalThis.localStorage = {
  getItem: k => (storage.has(k) ? storage.get(k) : null),
  setItem: (k, v) => storage.set(k, String(v)),
  removeItem: k => storage.delete(k),
}
globalThis.location = {
  search: '?ontology=oa', hash: '#build', pathname: '/', port: '18765', origin: 'http://127.0.0.1:18765',
  assign(url) { assignCalls.push(String(url)) },
}
globalThis.history = { replaceState(_s, _t, url) { replaceCalls.push(String(url)) } }
globalThis.document = {
  addEventListener() {}, removeEventListener() {}, title: '', activeElement: null,
  querySelector: () => null, querySelectorAll: () => [],
  createElement: () => fakeEl(), body: { appendChild() {}, append() {} },
}
globalThis.addEventListener = () => {}
globalThis.removeEventListener = () => {}
const realFetch = async () => ({ ok: false, status: 404, json: async () => ({ error: 'test stub: 未预期的请求' }) })
globalThis.fetch = realFetch

const root = mkdtempSync(join(tmpdir(), 'wiz_build_review_'))

/** 取真实 .vue 的 <script setup> 绑定（子 .vue 组件桩化；不渲染模板，避免依赖子页面）。 */
async function loadSetup(vueFile, props = {}) {
  const filename = resolve(SRC, vueFile)
  const dir = resolve(SRC, vueFile, '..')
  const { descriptor } = parse(readFileSync(filename, 'utf8'), { filename })
  let code = compileScript(descriptor, { id: 'test-' + vueFile.replace(/\W+/g, '-') }).content
  code = code.replace(/import\s+(\w+)\s+from\s+['"][^'"]*\.vue['"]/g, 'const $1 = {}')
  code = code.replace(/from (['"])([^'"]+)\1/g, (_, q, spec) => {
    const target = spec === 'vue' ? require.resolve('vue') : resolve(dir, spec + '.ts')
    return 'from ' + JSON.stringify(pathToFileURL(target).href)
  })
  const file = join(root, vueFile.replace(/\W+/g, '-') + '.mjs')
  writeFileSync(file, ts.transpileModule(code, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText)
  const Component = (await import(pathToFileURL(file).href)).default
  const setup = Component.setup
  let api = null
  Component.setup = (p, ctx) => { api = setup(p, ctx); return api }
  Component.render = () => null
  const inst = createSSRApp(Component, props)
  await renderToString(inst)
  assert.ok(api, vueFile + ' 未能取得 setup 绑定')
  return api
}

// ── ① 纯函数口径（candidateFields.ts）────────────────────────────────────────
const cf = await import('../frontend/src/ontology/build/candidateFields.ts')

await check('① VALUE_TYPES 与后端枚举逐字一致且都有中文标签', () => {
  assert.deepEqual([...cf.VALUE_TYPES], ['string', 'double', 'decimal', 'integer', 'boolean', 'date', 'dateTime'])
  assert.deepEqual([...cf.PROPERTY_DATA_TYPES], ['text', 'number', 'boolean', 'dateTime', 'array', 'struct', 'timeSeries'])
  for (const key of cf.VALUE_TYPES) {
    const label = cf.VALUE_TYPE_LABELS[key]
    assert.ok(label && /[\u4e00-\u9fa5]/.test(label), '观测值类型缺中文标签：' + key)
  }
  for (const key of cf.PROPERTY_DATA_TYPES) {
    const label = cf.PROPERTY_DATA_TYPE_LABELS[key]
    assert.ok(label && /[\u4e00-\u9fa5]/.test(label), '数据类型缺中文标签：' + key)
  }
  assert.deepEqual(Object.keys(cf.VALUE_TYPE_LABELS).sort(), [...cf.VALUE_TYPES].sort(), '标签表不得多出协议之外的键')
  assert.deepEqual(Object.keys(cf.PROPERTY_DATA_TYPE_LABELS).sort(), [...cf.PROPERTY_DATA_TYPES].sort())
})

await check('② 读取平铺字段：timeSeries 必须读出平铺 valueType（当前缺陷项）', () => {
  assert.deepEqual(cf.readPropertyFields({ dataType: 'timeSeries', valueType: 'double' }), { dataType: 'timeSeries', valueType: 'double' })
  assert.deepEqual(cf.readPropertyFields({ dataType: 'timeSeries', valueType: 'decimal' }), { dataType: 'timeSeries', valueType: 'decimal' })
  assert.deepEqual(cf.readPropertyFields({ dataType: 'number' }), { dataType: 'number', valueType: '' })
})

await check('③ 缺观测值类型读出空串（不崩、不臆造 double）', () => {
  assert.deepEqual(cf.readPropertyFields({ dataType: 'timeSeries', valueType: undefined }), { dataType: 'timeSeries', valueType: '' })
  assert.deepEqual(cf.readPropertyFields({ dataType: 'timeSeries' }), { dataType: 'timeSeries', valueType: '' })
  assert.deepEqual(cf.readPropertyFields({ dataType: 'timeSeries', valueType: '' }), { dataType: 'timeSeries', valueType: '' })
  assert.deepEqual(cf.readPropertyFields(null), { dataType: '', valueType: '' })
  assert.deepEqual(cf.readPropertyFields({}), { dataType: '', valueType: '' })
})

await check('④ 旧嵌套形态只读兼容：仍能读出 type/valueType', () => {
  assert.deepEqual(cf.readPropertyFields({ dataType: { type: 'timeSeries', valueType: 'double' } }), { dataType: 'timeSeries', valueType: 'double' })
  assert.deepEqual(cf.readPropertyFields({ dataType: { type: 'timeSeries' }, valueType: 'integer' }), { dataType: 'timeSeries', valueType: 'integer' })
  assert.deepEqual(cf.readPropertyFields({ dataType: { type: 'number' } }), { dataType: 'number', valueType: '' })
  // 读取是忠实还原（不臆造也不改写）；写回时由 buildPropertyFields 收敛为协议形态
  assert.deepEqual(cf.readPropertyFields({ dataType: 'number', valueType: 'double' }), { dataType: 'number', valueType: 'double' })
})

await check('⑤ 保存载荷平铺：timeSeries 带 valueType，绝不输出嵌套对象', () => {
  const fields = cf.buildPropertyFields('timeSeries', 'double')
  assert.equal(typeof fields.dataType, 'string', 'dataType 必须是平铺字符串')
  assert.deepEqual(fields, { dataType: 'timeSeries', valueType: 'double' })
  assert.equal(Object.keys(fields).sort().join(','), 'dataType,valueType')
})

await check('⑥ 非 timeSeries 不残留 valueType；未确定类型不发送 dataType', () => {
  assert.deepEqual(cf.buildPropertyFields('number', ''), { dataType: 'number' })
  assert.deepEqual(cf.buildPropertyFields('number', 'double'), { dataType: 'number' })
  assert.deepEqual(cf.buildPropertyFields('text', 'integer'), { dataType: 'text' })
  assert.deepEqual(cf.buildPropertyFields('timeSeries', ''), { dataType: 'timeSeries' })
  assert.deepEqual(cf.buildPropertyFields('', 'double'), {})
  assert.deepEqual(cf.buildPropertyFields('  ', ''), {})
})

// ── ② 真实 BuildReviewPage.vue：读取回填与保存载荷 ────────────────────────────
function candidateRaw(overrides = {}) {
  return {
    id: 'bc-1', taskId: 'bk-1', batchId: 'bb-1', type: 'property', key: 'p1',
    name: '有功功率', definition: '机组实时有功功率', ownerKey: 'obj-1',
    fields: {}, evidence: {}, evidenceStatus: 'supported', conflicts: [], decision: 'include',
    reviewed: false, reason: '', issues: [], origin: { batch: 'bb-1', key: 'p1', mergedFrom: [], mergedInto: null },
    revision: 'r-cand-1', ...overrides,
  }
}
const page = await loadSetup('ontology/build/BuildReviewPage.vue', { taskId: 'bk-1' })

await check('⑦ 评审页回填：平铺 timeSeries+double 读进表单（旧实现读不到观测值类型）', () => {
  page.detailRaw.value = candidateRaw({ fields: { dataType: 'timeSeries', valueType: 'double' } })
  page.resetDraft(page.current.value)
  assert.equal(page.draft.value.dataType, 'timeSeries')
  assert.equal(page.draft.value.valueType, 'double', '平铺 fields.valueType 必须被读到')
  assert.equal(page.dirty.value, false, '刚回填不得显示为已修改')
})

await check('⑧ 评审页回填：嵌套历史数据仍可打开编辑器（只读兼容）', () => {
  page.detailRaw.value = candidateRaw({ fields: { dataType: { type: 'timeSeries', valueType: 'decimal' } } })
  page.resetDraft(page.current.value)
  assert.equal(page.draft.value.dataType, 'timeSeries')
  assert.equal(page.draft.value.valueType, 'decimal')
})

/** 抓取一次真实 POST /api/build-candidate-update 的请求体。 */
async function captureSave(fieldsRaw, mutate) {
  let body = null
  globalThis.fetch = async (url, init) => {
    body = init && init.body ? JSON.parse(init.body) : null
    return { ok: true, status: 200, json: async () => ({ candidate: candidateRaw({ fields: body && body.fields ? body.fields : fieldsRaw, revision: 'r-cand-2' }) }) }
  }
  page.detailRaw.value = candidateRaw({ fields: fieldsRaw })
  page.resetDraft(page.current.value)
  mutate(page.draft.value)
  await page.saveEdit()
  assert.ok(body, '未发出保存请求')
  return body
}

await check('⑨ 评审页保存：timeSeries 发平铺 {dataType:' + "'timeSeries'" + ', valueType}，不含嵌套对象', async () => {
  const body = await captureSave({ dataType: 'timeSeries', valueType: 'double' }, draft => { draft.valueType = 'integer' })
  assert.equal(typeof body.fields.dataType, 'string', 'dataType 不得是对象')
  assert.equal(body.fields.dataType, 'timeSeries')
  assert.equal(body.fields.valueType, 'integer')
  assert.equal(JSON.stringify(body).includes('"type":"timeSeries"'), false, '请求体不得出现嵌套 type 形态')
  assert.equal(body.revision, 'r-cand-1', '候选级 CAS token 必须回传')
})

await check('⑩ 评审页保存：普通类型只发 {dataType}，不残留 valueType', async () => {
  const body = await captureSave({ dataType: 'timeSeries', valueType: 'double' }, draft => { draft.dataType = 'number'; draft.valueType = '' })
  assert.deepEqual(body.fields.dataType, 'number')
  assert.equal('valueType' in body.fields, false, '非时间序列不得发送 valueType')
})

await check('⑪ 评审页校验：时间序列未选观测值类型时拒绝保存并提示', async () => {
  let called = false
  globalThis.fetch = async () => { called = true; return { ok: true, status: 200, json: async () => ({}) } }
  page.detailRaw.value = candidateRaw({ fields: { dataType: 'timeSeries', valueType: '' } })
  page.resetDraft(page.current.value)
  page.draft.value.valueType = ''
  page.draft.value.name = '有功功率·改'
  await page.saveEdit()
  assert.equal(called, false, '校验不过不得发出请求')
  assert.match(page.editError.value, /时间序列必须选择观测值类型/)
})

// ── ③ 真实 App.vue：enterBuildOntology 三条分支（B.4）────────────────────────
const app = await loadSetup('App.vue')

await check('⑫ 回链：目标本体与当前不同 → 先切本体（带未保存守卫）再进对象建模', async () => {
  const before = assignCalls.length
  await app.enterBuildOntology('ob')
  assert.equal(assignCalls.length, before + 1, '必须走一次切本体重载')
  assert.match(assignCalls[assignCalls.length - 1], /^\?ontology=ob#objects$/, '切到目标本体并落在对象建模页')
})

await check('⑬ 回链：目标就是当前本体 → 直接进对象建模，不重载', async () => {
  const beforeAssign = assignCalls.length, beforeReplace = replaceCalls.length
  await app.enterBuildOntology('oa')
  assert.equal(assignCalls.length, beforeAssign, '同 id 不应触发整页重载')
  assert.equal(app.view.value, 'objects')
  assert.equal(replaceCalls.length, beforeReplace + 1)
  assert.match(replaceCalls[replaceCalls.length - 1], /#objects$/)
})

await check('⑭ 回链：id 缺失 → 退回原行为并给出可读提示', async () => {
  const fresh = await loadSetup('App.vue')
  const beforeAssign = assignCalls.length
  await fresh.enterBuildOntology('')
  assert.equal(fresh.view.value, 'objects')
  assert.match(fresh.message.value, /没有可用的已创建本体 id/, '缺 id 必须给出可读提示')
  assert.equal(assignCalls.length, beforeAssign, '缺 id 不得切本体')
  // 不带参（页头「进入对象建模」）仍是原行为，且不显示提示
  const plain = await loadSetup('App.vue')
  await plain.enterBuildOntology()
  assert.equal(plain.view.value, 'objects')
  assert.equal(plain.message.value, '', '无参调用不应产生提示噪声')
})

// ── ④ 源文本断言：页面不得回退旧口径 ─────────────────────────────────────────
await check('⑮ 评审页不再自带类型表、不再发送嵌套载荷；任务行回链带本体 id', () => {
  const review = readFileSync(resolve(SRC, 'ontology/build/BuildReviewPage.vue'), 'utf8')
  assert.match(review, /from '\.\/candidateFields'/, '评审页必须复用 candidateFields 协议层')
  assert.match(review, /buildPropertyFields\(/)
  assert.match(review, /readPropertyFields\(/)
  assert.equal(/VALUE_TYPE_LABELS\s*:\s*Record/.test(review), false, '评审页不得再自带观测值类型表')
  assert.equal(/dataType\s*=\s*\{\s*type:\s*'timeSeries'/.test(review), false, '不得再写嵌套 dataType')
  const tasks = readFileSync(resolve(SRC, 'ontology/build/BuildTasksPage.vue'), 'utf8')
  assert.match(tasks, /emit\('enter-ontology',\s*row\.task\.deliveryOntologyId\)/, '回链按钮必须携带目标本体 id')
})

report()
