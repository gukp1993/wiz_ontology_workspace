// 全局设置中心导航（20260918）回归：settings/settings-models/llm 三种入口归一、
// 全局页不属于两区、initialSpace 不被设置页改写、lastOntologyView 不含设置页。
// 运行（仓库根）：node --import ./tests/ts_hooks.mjs tests/global_settings_nav.test.mjs
import assert from 'node:assert/strict'
import { pathToFileURL } from 'node:url'
import { createRequire } from 'node:module'
import { readFileSync, mkdtempSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'

const require = createRequire(new URL('../frontend/package.json', import.meta.url))
const ts = require('typescript'), { createSSRApp } = require('vue'), { renderToString } = require('@vue/server-renderer')
const { parse, compileScript } = require('@vue/compiler-sfc')

const storage = new Map()
globalThis.addEventListener = () => {}
globalThis.removeEventListener = () => {}
globalThis.window = globalThis
globalThis.location = { search: '', hash: '#objects' }
globalThis.history = { replaceState() {} }
globalThis.localStorage = { getItem: k => (storage.has(k) ? storage.get(k) : null), setItem: (k, v) => storage.set(k, String(v)), removeItem: k => storage.delete(k) }
globalThis.document = { addEventListener() {}, removeEventListener() {}, querySelector: () => null, querySelectorAll: () => [], title: '' }

const root = mkdtempSync(join(tmpdir(), 'wiz_settings_nav_'))
const SRC = resolve('frontend/src')

const nav = await import(pathToFileURL(resolve('frontend/src/app/navigation.ts')).href)
const { normalizeView, pages, projectSpaceViews, isGlobalView, initialSpace, initialView, settingsCategories } = nav

const results = []
async function check(name, fn) {
  try { await fn(); results.push({ name, ok: true }); console.log('通过：' + name) }
  catch (e) { results.push({ name, ok: false }); console.log('失败：' + name + ' — ' + e.message) }
}

/** 编译 App.vue 的 <script setup>（子页面桩化），测 navigate 不污染 lastOntologyView。 */
async function loadApp() {
  const filename = resolve('frontend/src/App.vue')
  const { descriptor } = parse(readFileSync(filename, 'utf8'), { filename })
  let code = compileScript(descriptor, { id: 'settings-nav-test' }).content
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

function server() {
  const routes = {
    '/api/state': () => ({ state: { ontology: { '@graph': [] }, workflow: {} }, revision: 'r1', latestVersion: 'v1' }),
    '/api/ontologies': () => ({ items: [{ id: 'storage', name: '储能本体' }] }),
  }
  return async (url) => {
    const p = String(url).split('?')[0]
    if (routes[p]) return { ok: true, status: 200, json: async () => routes[p]() }
    return { ok: true, status: 200, json: async () => ({ items: [] }) }
  }
}

try {
  // ① 三种入口归一到 settings-models
  await check('① settings / settings-models / llm 三入口归一且在白名单', async () => {
    assert.equal(normalizeView('settings'), 'settings-models')
    assert.equal(normalizeView('llm'), 'settings-models')
    assert.equal(normalizeView('settings-models'), 'settings-models')
    assert.ok('settings-models' in pages)
    assert.ok(isGlobalView('settings-models'))
    assert.ok(!projectSpaceViews.includes('settings-models'))
  })

  // ② initialSpace：设置深链不改变空间语义；分类注册表只有已实现项
  await check('② 设置深链不归本体/项目两区；分类注册表仅含已实现项', async () => {
    // 深链 #settings-models：initialSpace 不应因此声明属于本体区（沿用 saved）
    const sp = initialSpace('#settings-models', null)
    assert.equal(sp, 'ontology') // 无记忆时的兜底
    assert.equal(initialSpace('#settings-models', 'project'), 'project', '有项目记忆时保持项目空间供返回')
    // 20260919 配置迁移需求：设置中心新增「数据管理 / 配置迁移」分类
    assert.deepEqual(settingsCategories.map(c => c.id), ['settings-models', 'settings-transfer'])
    assert.equal(settingsCategories[0].title, '模型设置')
    assert.equal(settingsCategories[0].group, '基础设置')
    // 旧 llm 不再是独立白名单键
    assert.ok(!('llm' in pages))
  })

  // ③ initialView：#settings 深链落到 settings-models
  await check('③ #settings 深链归一为 settings-models', async () => {
    globalThis.location.hash = '#settings'
    storage.clear()
    assert.equal(initialView(), 'settings-models')
    globalThis.location.hash = '#llm'
    assert.equal(initialView(), 'settings-models')
    globalThis.location.hash = '#objects'
    assert.equal(initialView(), 'objects')
  })

  // ④ navigate('settings-models') 不改写 lastOntologyView、不切 space；返回协议恢复原 view
  await check('④ 进入设置不污染 lastOntologyView；返回恢复来源页', async () => {
    const s = server()
    globalThis.fetch = s
    const { Component, api } = await loadApp()
    const inst = createSSRApp(Component, {})
    await renderToString(inst)
    const a = api()
    a.space.value = 'ontology'
    a.view.value = 'objects'
    a.lastOntologyView.value = 'objects'
    await a.openSettings()
    assert.equal(a.view.value, 'settings-models')
    assert.equal(a.lastOntologyView.value, 'objects', '设置页不得写 lastOntologyView')
    assert.equal(a.space.value, 'ontology', '设置页不改写 space')
    assert.deepEqual(a.settingsReturn.value, { view: 'objects' }, '来源应被记录')
    // 已在设置内再次进入（菜单/齿轮）：不覆盖最初来源
    await a.openSettings()
    assert.deepEqual(a.settingsReturn.value, { view: 'objects' }, '重复进入不覆盖返回来源')
    await a.backToWorkspace()
    assert.equal(a.view.value, 'objects', '返回应回到来源页')
    assert.equal(a.settingsReturn.value, null)
    // 深链直达无来源：回最近有效业务页
    await a.backToWorkspace()
    assert.equal(a.view.value, 'objects')
  })

  // ⑤ 从编排进入：来源为 f-editor；返回恢复 f-editor（节点/页签恢复由 FlowEditor props 承接）
  await check('⑤ 从编排进入设置：返回协议指向 f-editor', async () => {
    const s = server()
    globalThis.fetch = s
    const { Component, api } = await loadApp()
    const inst = createSSRApp(Component, {})
    await renderToString(inst)
    const a = api()
    a.space.value = 'project'
    a.view.value = 'f-editor'
    a.flowState.value = { name: '测试编排', nodes: [] } // 来源编排仍在内存（真实场景）
    await a.navigate('llm', { tab: 'implementation', definition: 'node1' }) // 旧内部调用（带编排上下文）
    assert.equal(a.view.value, 'settings-models')
    assert.equal(a.lastOntologyView.value !== 'settings-models', true)
    assert.equal(a.settingsReturn.value?.view, 'f-editor')
    await a.backToWorkspace()
    assert.equal(a.view.value, 'f-editor')
  })

  // ⑥ 设置页 undo/redo 与 Saver 隔离：activeSaver 为 null（模板不显示业务保存状态）
  await check('⑥ 设置页不挂业务 Saver 状态', async () => {
    const s = server()
    globalThis.fetch = s
    const { Component, api } = await loadApp()
    const inst = createSSRApp(Component, {})
    await renderToString(inst)
    const a = api()
    await a.navigate('settings-models')
    assert.equal(a.activeSaver.value, null, '设置页不得展示业务 Saver')
    assert.equal(a.onGlobalView.value, true)
    assert.equal(a.saveState.value.text, '设置')
  })
  // ⑦ 用户菜单（20260918 修裁切）：菜单渲染到 body 浮层、定位按视口收敛、Esc 关闭并归还焦点
  await check('⑦ 用户菜单浮层定位与 Esc 关闭', async () => {
    const s = server()
    globalThis.fetch = s
    const { Component, api } = await loadApp()
    const inst = createSSRApp(Component, {})
    await renderToString(inst)
    const a = api()
    // 触发器贴近右下：菜单应右对齐收敛在视口内、向上弹出（不越过视口顶部）
    const trigger = { getBoundingClientRect: () => ({ left: 120, right: 220, top: 800, bottom: 840 }) }
    a.userMenuTrigger.value = trigger
    a.userMenuCard.value = { offsetWidth: 280, offsetHeight: 180, querySelector: () => null }
    globalThis.innerWidth = 1440
    globalThis.innerHeight = 900
    a.userMenuOpen.value = true
    a.placeUserMenu()
    const style = a.userMenuStyle.value
    assert.equal(style.left, '120px', '左边缘取触发器左缘（视口内不收敛）')
    assert.equal(style.top, '612px', '向上弹出：顶部 = 触发器顶 - 高度 - 间距')
    // Esc：关闭并把焦点还给触发器
    const focused = []
    a.userMenuTrigger.value = { ...trigger, focus: () => focused.push('trigger') }
    a.userMenuKeydown({ key: 'Escape', preventDefault() {}, currentTarget: { querySelectorAll: () => [] } })
    assert.equal(a.userMenuOpen.value, false, 'Esc 必须关闭菜单')
    assert.deepEqual(focused, ['trigger'], 'Esc 关闭后焦点回到触发器')
  })
} finally {
  rmSync(root, { recursive: true, force: true })
}

const failed = results.filter(r => !r.ok)
console.log(`\n${results.length - failed.length}/${results.length} 项通过`)
if (failed.length) process.exit(1)
