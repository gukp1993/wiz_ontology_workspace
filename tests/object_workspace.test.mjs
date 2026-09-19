// 对象建模交互回归（20260917 评审采纳项；20260919 关系画布移除后表单统一列表入口）：取消不落库、
// 保存失败不丢输入也不重复新增、共享来源按稳定 ID 解析、更多操作菜单与画布工具文案。
// 运行（仓库根）：node --import ./tests/ts_hooks.mjs tests/object_workspace.test.mjs
// 用真实 ObjectWorkspace.vue 的 <script setup>（子组件桩化），不连接服务、不写真实 ontology。
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { readFileSync, readdirSync, mkdtempSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { pathToFileURL } from 'node:url'

const require = createRequire(new URL('../frontend/package.json', import.meta.url))
const { parse, compileScript } = require('@vue/compiler-sfc')
const ts = require('typescript'), { createSSRApp, reactive, nextTick } = require('vue'), { renderToString } = require('@vue/server-renderer')

// 组件里用到的浏览器 API：SSR 不执行 onMounted，但模块顶层与 watch(immediate) 会触达它们。
globalThis.window = { matchMedia: () => ({ matches: false, addEventListener() {}, removeEventListener() {} }), addEventListener() {}, removeEventListener() {}, innerHeight: 900, location: { hash: '' } }
globalThis.document = { addEventListener() {}, removeEventListener() {}, querySelector: () => null, querySelectorAll: () => [], activeElement: null }
globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} }
globalThis.requestAnimationFrame = cb => setTimeout(() => cb(Date.now()), 0)
// Node 25 已内置 globalThis.crypto（randomUUID 直接可用）：仅在缺失时兜底
if (!globalThis.crypto?.randomUUID) Object.defineProperty(globalThis, 'crypto', { value: require('node:crypto').webcrypto, configurable: true })

const SRC = resolve('frontend/src/ontology')
const root = mkdtempSync(join(tmpdir(), 'wiz_object_ws_'))

/** 编译 ObjectWorkspace.vue 的 <script setup>：子组件与弹窗全部桩化，只测页面自身状态机。 */
async function loadWorkspace() {
  const filename = resolve('frontend/src/ontology/ObjectWorkspace.vue')
  const { descriptor } = parse(readFileSync(filename, 'utf8'), { filename })
  let code = compileScript(descriptor, { id: 'ow-test' }).content
  code = code.replace(/import\s+PickerDialog\s*,\s*\{\s*type\s+PickerRow\s*\}\s+from\s+['"][^'"]+['"]/, 'const PickerDialog = {}')
  code = code.replace(/import\s+(\w+)\s+from\s+['"][^'"]*\.vue['"]/g, 'const $1 = {}')
  code = code.replace(/from (['"])([^'"]+)\1/g, (_, q, spec) => {
    const target = spec === 'vue' ? require.resolve('vue') : resolve(SRC, spec + '.ts')
    return 'from ' + JSON.stringify(pathToFileURL(target).href)
  })
  const file = join(root, 'ObjectWorkspace.mjs')
  writeFileSync(file, ts.transpileModule(code, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText)
  const Component = (await import(pathToFileURL(file).href)).default
  return Component
}

function graphFixture() {
  const Shared = { '@id': 'mg:sp_power', '@type': 'mg:SharedProperty', 'rdfs:label': '额定功率', 'rdfs:comment': '设备铭牌功率', 'rdfs:range': { '@id': 'xsd:double' }, 'mg:valueSuffix': 'kW' }
  const cluster = { '@id': 'mg:cluster', '@type': 'owl:Class', 'rdfs:label': '储能簇', 'rdfs:comment': '一组电池簇' }
  const site = { '@id': 'mg:site', '@type': 'owl:Class', 'rdfs:label': '储能站', 'rdfs:comment': '场站' }
  const sharedProp = { '@id': 'mg:p_shared', '@type': 'owl:DatatypeProperty', 'rdfs:label': '额定功率', 'rdfs:domain': { '@id': 'mg:cluster' }, 'mg:sharedProperty': { '@id': 'mg:sp_power' } }
  const ownProp = { '@id': 'mg:p_own', '@type': 'owl:DatatypeProperty', 'rdfs:label': 'SOC', 'rdfs:domain': { '@id': 'mg:cluster' }, 'rdfs:range': { '@id': 'xsd:double' } }
  const dangling = { '@id': 'mg:p_dangling', '@type': 'owl:DatatypeProperty', 'rdfs:label': '退役字段', 'rdfs:domain': { '@id': 'mg:cluster' }, 'rdfs:range': { '@id': 'xsd:string' }, 'mg:sharedProperty': { '@id': 'mg:sp_missing' } }
  const link = { '@id': 'mg:l_belong', '@type': 'owl:ObjectProperty', 'rdfs:label': '所属场站', 'rdfs:domain': { '@id': 'mg:cluster' }, 'rdfs:range': { '@id': 'mg:site' }, 'mg:cardinality': 'many-to-one' }
  return [Shared, cluster, site, sharedProp, ownProp, dangling, link]
}

/** 模拟 App 的 form-save：mutate 后按 saveResult 决定提交或回滚（回滚＝就地恢复快照，等价于 Saver 回滚 working）。 */
function makeFormSave(state, box) {
  const saved = { count: 0 }
  const restore = (target, snap) => { for (const k of Object.keys(target)) delete target[k]; Object.assign(target, snap) }
  const api = {
    saved,
    async submitForm(area, apply) {
      assert.equal(area, 'ontology')
      saved.count++
      const snap = JSON.parse(JSON.stringify(state))
      apply()
      if (box.fail) { restore(state, snap); return { ok: false, message: box.fail } }
      return { ok: true, message: '' }
    },
  }
  return api
}

const results = []
function check(name, fn) { try { fn(); results.push({ name, ok: true }); console.log('通过：' + name) } catch (e) { results.push({ name, ok: false }); console.log('失败：' + name + ' — ' + e.message) } }

const Component = await loadWorkspace()
let api
const setup = Component.setup
Component.setup = (props, ctx) => { api = setup(props, ctx); return api }
Component.render = () => null

function mount(state, box = {}) {
  box.fail = box.fail || ''
  const app = createSSRApp(Component, { state })
  const guards = { list: [] }
  app.provide('form-guard', { register: g => guards.list.push(g), unregister: g => { guards.list = guards.list.filter(x => x !== g) }, hasDirty: () => false, editing: () => false })
  app.provide('form-save', makeFormSave(state, box))
  return { app, guards }
}

try {
  // ── A2：表单新建对象/链接（关系画布已移除，统一列表入口），取消后模型没有新记录，且填写过程中不产生保存请求 ──
  {
    const state = reactive({ ontology: { '@graph': graphFixture() }, workflow: {}, layout: {} })
    const box = {}
    const { app } = mount(state, box)
    await renderToString(app)
    const before = state.ontology['@graph'].length
    api.openObjectEditor(true)
    assert.equal(api.editor.value.kind, 'object')
    assert.equal(api.editor.value.isNew, true)
    assert.equal(api.editor.value.origin, 'list')
    api.objectDraft.value.label = '写了一半就放弃'
    assert.equal(state.ontology['@graph'].length, before, '填写过程中不得插入占位记录')
    api.closeEditor()
    assert.equal(api.editor.value, null)
    assert.equal(state.ontology['@graph'].length, before, '取消后模型不应有新记录')

    api.openLinkEditor()
    api.linkDraft.value.from = 'mg:cluster'
    api.linkDraft.value.to = 'mg:site'
    assert.equal(api.editor.value.kind, 'link')
    assert.equal(api.editor.value.origin, 'list')
    assert.equal(api.linkDraft.value.from, 'mg:cluster')
    assert.equal(api.linkDraft.value.to, 'mg:site')
    api.closeEditor()
    assert.equal(state.ontology['@graph'].length, before, '取消连线不产生链接')
    check('A2 表单新建/链接取消后模型无新记录，编辑期间无保存请求', () => assert.equal(0, 0))
  }

  // ── A1/A3：画布来源保存失败保留输入、不重复新增；成功保存恰好新增一条 ──
  {
    const state = reactive({ ontology: { '@graph': graphFixture() }, workflow: {}, layout: {} })
    const box = { fail: '草稿版本冲突，请先在顶栏处理后再保存表单' }
    const { app } = mount(state, box)
    await renderToString(app)
    const before = state.ontology['@graph'].length
    api.openObjectEditor(true)
    const id = api.editor.value.id
    api.objectDraft.value.label = '储能变流器'
    api.objectDraft.value.comment = '功率变换设备'
    await api.saveObject()
    assert.equal(api.editorError.value, box.fail, '失败必须把服务端/保存层原因显示出来')
    assert.equal(api.editor.value !== null, true, '保存失败不得退出表单')
    assert.equal(api.objectDraft.value.label, '储能变流器', '保存失败必须保留输入')
    assert.equal(state.ontology['@graph'].length, before, '保存失败不得留下半成品记录')
    box.fail = ''
    await api.saveObject()
    assert.equal(api.editor.value, null, '成功后退出表单')
    assert.equal(state.ontology['@graph'].length, before + 1, '重试保存只新增一条')
    const created = state.ontology['@graph'].find(n => n['@id'] === id)
    assert.equal(created['rdfs:label'], '储能变流器')
    assert.equal(created['rdfs:comment'], '功率变换设备')
    check('A1/A3 对象表单失败保留输入且不重复新增，重试成功恰好新增一条', () => assert.equal(0, 0))
  }

  // ── A1：对象/链接必填一致，列表与画布同一套校验 ──
  {
    const state = reactive({ ontology: { '@graph': graphFixture() }, workflow: {}, layout: {} })
    const { app } = mount(state, {})
    await renderToString(app)
    api.openObjectEditor(true)
    api.objectDraft.value.label = '只有名称'
    await api.saveObject()
    assert.equal(api.editorError.value, '请填写业务定义。')
    api.objectDraft.value.comment = '补上定义'
    assert.equal(api.editor.value.kind, 'object')
    api.closeEditor()
    // 链接：起点/终点/正向名称/数量关系必填
    api.openLinkEditor()
    api.linkDraft.value.from = 'mg:cluster'
    api.linkDraft.value.to = 'mg:site'
    api.linkDraft.value.label = ''
    await api.saveLink()
    assert.equal(api.editorError.value, '请填写正向名称。')
    api.linkDraft.value.label = '所属场站'
    api.linkDraft.value.to = ''
    await api.saveLink()
    assert.equal(api.editorError.value, '请选择终点对象。')
    api.linkDraft.value.to = 'mg:site'
    api.linkDraft.value.cardinality = ''
    await api.saveLink()
    assert.equal(api.editorError.value, '请选择数量关系。')
    check('A1 对象/链接必填校验与列表一致且逐项提示', () => assert.equal(0, 0))
  }

  // ── R5：共享来源按稳定 ID 解析；失效引用不假冒正常 ──
  {
    const state = reactive({ ontology: { '@graph': graphFixture() }, workflow: {}, layout: {} })
    const { app } = mount(state, {})
    await renderToString(app)
    api.selected.value = 'mg:cluster'
    const rows = api.propRows.value
    const shared = rows.find(r => r.id === 'mg:p_shared')
    const own = rows.find(r => r.id === 'mg:p_own')
    const dangling = rows.find(r => r.id === 'mg:p_dangling')
    assert.equal(shared.sharedId, 'mg:sp_power')
    assert.equal(shared.label, '额定功率', '共享引用显示生效定义名称')
    assert.equal(shared.unit, 'kW')
    assert.equal(own.sharedId, '', '私有属性没有共享引用')
    api.openShared(shared.sharedId)
    assert.equal(api.sharedDetail.value.missing, false)
    assert.equal(api.sharedDetail.value.name, '额定功率')
    assert.equal(api.sharedDetail.value.type, '数值', '类型按共享定义解析：' + api.sharedDetail.value.type)
    assert.equal(api.sharedDetail.value.usage, 1)
    api.closeShared()
    api.openShared(dangling.sharedId)
    assert.equal(api.sharedDetail.value.missing, true, '失效引用必须明确共享定义不存在')
    assert.equal(api.sharedDetail.value.name, '')
    api.closeShared()
    check('R5 共享/私有按稳定 ID 解析，失效引用不假冒正常', () => assert.equal(0, 0))
  }

  // ── R3：更多操作菜单的打开/关闭与焦点归还（键盘可用性） ──
  {
    const state = reactive({ ontology: { '@graph': graphFixture() }, workflow: {}, layout: {} })
    const { app } = mount(state, {})
    await renderToString(app)
    const focused = []
    api.moreTrigger.value = { focus: () => focused.push('trigger') }
    api.moreWrap.value = { querySelector: () => null, contains: () => false }
    api.openMoreMenu()
    assert.equal(api.moreOpen.value, true)
    api.closeMoreMenu(true)
    assert.equal(api.moreOpen.value, false)
    assert.deepEqual(focused, ['trigger'], 'Escape/关闭后焦点回到触发器')
    // 菜单打开时点击外部关闭
    api.openMoreMenu()
    api.onDocumentClick({ target: {} })
    assert.equal(api.moreOpen.value, false)
    check('R3 更多操作菜单可开关、点击外部关闭、关闭后焦点归还', () => assert.equal(0, 0))
  }

  // ── R4（按用户 2026-09-17 追加要求调整）：对象/链接表单撑满右侧工作区，不设宽度上限；
  //    画布与数据表宽度不受影响 ──
  {
    const src = readFileSync(resolve('frontend/src/ontology/ObjectWorkspace.vue'), 'utf8')
    assert.ok(!/\.ow-editor\s*\{[^}]*max-width/.test(src), '对象/链接表单不得再设宽度上限')
    assert.ok(!/\.ld-detail\s*\{[^}]*max-width/.test(src), '不得缩窄详情/数据表')
    assert.ok(!/\.graph-workspace\s*\{[^}]*max-width/.test(src), '不得缩窄画布')
    check('R4 表单撑满右侧工作区且不限制画布与数据表', () => assert.equal(0, 0))
  }

  // ── R5/R6：详情不重复数量行、动作说明不常驻堆叠 ──
  {
    const src = readFileSync(resolve('frontend/src/ontology/ObjectWorkspace.vue'), 'utf8')
    assert.ok(!/属性 \{\{ propRows\.length \}\}/.test(src), '详情头不应再重复数量行（数量只在页签）')
    assert.ok(src.includes('尚未添加动作，可从动作库选择此对象支持的动作。'), '动作空态文案不符')
    assert.ok(!src.includes('多对象关联表示一个动作可分别作用于这些类型'), '动作页不应常驻项目实现说明')
    check('R5/R6 详情数量只在页签、动作空态单句且无重复说明', () => assert.equal(0, 0))
  }
} finally {
  rmSync(root, { recursive: true, force: true })
}

const failed = results.filter(r => !r.ok)
console.log(`\n${results.length - failed.length}/${results.length} 项通过`)
if (failed.length) process.exit(1)
