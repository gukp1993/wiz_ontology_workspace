// 本体列表统一（20260918 需求 §5/§6/§7）新增风险用例：
//   ① 搜索/筛选在【全量记录】上执行后再分页（不是只搜当前页），页码随结果收缩回到合法范围；
//   ② 链接行显示定义中的真实方向：从任一端查看同一行、数量关系不交换，自链接不重复两行；
//   ③ 行内更多菜单不改删除语义：共享引用是「移除引用」且共享定义保留，私有是「删除属性」；
//     对象动作移除关联只删当前对象关联、动作定义与其它对象关联保留；
//   ④ 对象规则只读详情按稳定 ID 取到完整四字段（名称/业务定义/规则内容/输出结果）。
// 运行（仓库根）：node --import ./tests/ts_hooks.mjs tests/ont_list_unified.test.mjs
// 用真实组件 <script setup>（.vue 子组件桩化），不发请求、不写真实 ontology。
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { readFileSync, mkdtempSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { pathToFileURL } from 'node:url'

const require = createRequire(new URL('../frontend/package.json', import.meta.url))
const { parse, compileScript } = require('@vue/compiler-sfc')
const ts = require('typescript'), { createSSRApp, reactive } = require('vue'), { renderToString } = require('@vue/server-renderer')

globalThis.window = { matchMedia: () => ({ matches: false, addEventListener() {}, removeEventListener() {} }), addEventListener() {}, removeEventListener() {}, innerHeight: 900, location: { hash: '' } }
globalThis.document = { addEventListener() {}, removeEventListener() {}, querySelector: () => null, querySelectorAll: () => [], activeElement: null }
globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} }
globalThis.requestAnimationFrame = cb => setTimeout(() => cb(Date.now()), 0)
if (!globalThis.crypto?.randomUUID) Object.defineProperty(globalThis, 'crypto', { value: require('node:crypto').webcrypto, configurable: true })

const SRC = resolve('frontend/src/ontology')
const root = mkdtempSync(join(tmpdir(), 'wiz_ont_list_'))
// 自动确认桩：确认框在无 DOM 环境不可交互；测试聚焦「确认之后」的模型行为。
const CONFIRM_STUB = join(root, 'appConfirm.stub.mjs')
writeFileSync(CONFIRM_STUB, 'export const appConfirm = async () => true\nexport default appConfirm\n')

/** 编译指定 .vue 的 <script setup>：.vue 子组件全部桩化，仅测页面自身状态机与派生数据。 */
async function loadComponent(file) {
  const filename = resolve(file)
  const { descriptor } = parse(readFileSync(filename, 'utf8'), { filename })
  let code = compileScript(descriptor, { id: 'ont-test' }).content
  code = code.replace(/import\s+PickerDialog\s*,\s*\{\s*type\s+PickerRow\s*\}\s+from\s*['"][^'"]+['"]/, 'const PickerDialog = {}')
  code = code.replace(/import\s+(\w+)\s+from\s*['"][^'"]*\.vue['"]/g, 'const $1 = {}')
  code = code.replace(/from (['"])([^'"]+)\1/g, (_, q, spec) => {
    const target = spec === 'vue' ? require.resolve('vue')
      : spec.endsWith('appConfirm') ? CONFIRM_STUB
      : resolve(SRC, spec + '.ts')
    return 'from ' + JSON.stringify(pathToFileURL(target).href)
  })
  const out = join(root, file.split('/').pop().replace('.vue', '') + '.mjs')
  writeFileSync(out, ts.transpileModule(code, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText)
  return (await import(pathToFileURL(out).href)).default
}

/** 模拟 App 的 form-save：apply 后按 box.fail 决定提交或整树回滚。 */
function makeFormSave(state, box) {
  const restore = (target, snap) => { for (const k of Object.keys(target)) delete target[k]; Object.assign(target, snap) }
  return { async submitForm(area, apply) { assert.equal(area, 'ontology'); const snap = JSON.parse(JSON.stringify(state)); apply(); if (box.fail) { restore(state, snap); return { ok: false, message: box.fail } } return { ok: true, message: '' } } }
}
function mount(Component, state, props = {}, box = {}) {
  const app = createSSRApp(Component, { state, ...props })
  app.provide('form-guard', { register() {}, unregister() {}, hasDirty: () => false, editing: () => false })
  app.provide('form-save', makeFormSave(state, box))
  return app
}
// 捕获 setup 返回的 api，便于直接驱动内部函数。
function capture(Component) {
  let api
  const setup = Component.setup
  Component.setup = (props, ctx) => { api = setup(props, ctx); return api }
  Component.render = () => null
  return () => api
}

const results = []
function check(name, fn) { try { fn(); results.push(true); console.log('通过：' + name) } catch (e) { results.push(false); console.log('失败：' + name + ' — ' + e.message) } }

try {
  // ontList.ts 是 TS：转译时把 'vue' 换成绝对路径（临时目录解析不到包）
  const ontListSrc = readFileSync(resolve(SRC, 'ontList.ts'), 'utf8')
  const ontListOut = join(root, 'ontList.mjs')
  writeFileSync(ontListOut, ts.transpileModule(ontListSrc.replace(/from 'vue'/g, "from " + JSON.stringify(require.resolve('vue'))), { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText)
  const { useOntTable: useTable } = await import(pathToFileURL(ontListOut).href)

  // ── ① 全量过滤后再分页（含页码合法化）──
  {
    const all = Array.from({ length: 45 }, (_, i) => ({ id: 'r' + i, name: i === 44 ? '目标记录' : '普通 ' + String(i).padStart(2, '0') }))
    const src = reactive({ rows: all })
    const t = useTable(() => src.rows, { match: (r, q) => r.name.toLowerCase().includes(q), sort: (a, b) => a.name.localeCompare(b.name, 'zh-CN') })
    assert.equal(t.pageCount.value, 3, '45 条应分 3 页')
    assert.equal(t.paged.value.length, 20)
    t.q.value = '目标记录'
    assert.equal(t.filtered.value.length, 1, '搜索必须命中第 45 条（全量而不是当前页）')
    assert.equal(t.page.value, 1, '搜索后回到第一页')
    assert.equal(t.paged.value[0].id, 'r44')
    t.q.value = ''
    t.page.value = 3
    assert.equal(t.paged.value.length, 5, '末页 5 条')
    src.rows = all.slice(0, 10) // 数据收缩：页码回到合法范围
    assert.equal(t.pageCount.value, 1)
    assert.equal(t.page.value, 1, '页码随结果收缩回到 1')
    check('① 搜索/筛选基于全量记录再分页，页码合法化', () => assert.equal(0, 0))
  }

  // ── ②③ 对象建模：链接真实方向、自链接不重复；共享/私有菜单语义；移除关联保留定义 ──
  const OW = await loadComponent('frontend/src/ontology/ObjectWorkspace.vue')
  const owApi = capture(OW)
  {
    const Shared = { '@id': 'mg:sp', '@type': 'mg:SharedProperty', 'rdfs:label': '额定功率', 'rdfs:comment': 'x', 'rdfs:range': { '@id': 'xsd:double' } }
    const cluster = { '@id': 'mg:cluster', '@type': 'owl:Class', 'rdfs:label': '储能簇', 'rdfs:comment': 'a' }
    const device = { '@id': 'mg:device', '@type': 'owl:Class', 'rdfs:label': '储能设备', 'rdfs:comment': 'b' }
    const link = { '@id': 'mg:l', '@type': 'owl:ObjectProperty', 'rdfs:label': '所属设备', 'rdfs:domain': { '@id': 'mg:cluster' }, 'rdfs:range': { '@id': 'mg:device' }, 'mg:cardinality': 'many-to-one', 'mg:reverseLabel': '包含储能簇' }
    const selfLink = { '@id': 'mg:ls', '@type': 'owl:ObjectProperty', 'rdfs:label': '关联簇', 'rdfs:domain': { '@id': 'mg:cluster' }, 'rdfs:range': { '@id': 'mg:cluster' }, 'mg:cardinality': 'many-to-many' }
    const sharedProp = { '@id': 'mg:p1', '@type': 'owl:DatatypeProperty', 'rdfs:label': '额定功率', 'rdfs:domain': { '@id': 'mg:cluster' }, 'mg:sharedProperty': { '@id': 'mg:sp' } }
    const ownProp = { '@id': 'mg:p2', '@type': 'owl:DatatypeProperty', 'rdfs:label': 'SOC', 'rdfs:domain': { '@id': 'mg:cluster' }, 'rdfs:range': { '@id': 'xsd:double' } }
    const action = { id: 'act1', name: '停止充放电', description: 'd', effect: 'e', definitionVersion: 2 }
    const rule = { id: 'rule1', name: '规则一', description: 'RD', content: 'RC', output: 'RO' }
    const state = reactive({
      ontology: { '@graph': [Shared, cluster, device, link, selfLink, sharedProp, ownProp] },
      workflow: { actions: [action], actionAssociations: [{ objectTypeId: 'mg:cluster', actionId: 'act1' }], businessRules: [rule], businessRuleAssociations: [{ objectTypeId: 'mg:cluster', ruleId: 'rule1' }] },
      layout: {},
    })
    const app = mount(OW, state)
    await renderToString(app)
    owApi().selected.value = 'mg:cluster'

    // 从起点看：真实方向 cluster → 设备，多对一；自链接一行
    const fromCluster = owApi().linkRows.value
    assert.equal(fromCluster.length, 2, '起点侧应见 2 行（含自链接一行）')
    const main = fromCluster.find(l => l.id === 'mg:l')
    assert.deepEqual([main.from, main.to, main.card], ['储能簇', '储能设备', '多对一'], '起点侧方向与数量关系不得交换')
    assert.equal(fromCluster.filter(l => l.id === 'mg:ls').length, 1, '自链接不得重复两行')
    // 从终点看：方向仍是定义中的真实方向，不因当前对象是终点而反转为「储能簇 ← 所属设备」
    owApi().selected.value = 'mg:device'
    const fromDevice = owApi().linkRows.value.find(l => l.id === 'mg:l')
    assert.deepEqual([fromDevice.from, fromDevice.to, fromDevice.card], ['储能簇', '储能设备', '多对一'], '终点侧同样按定义真实方向显示')
    owApi().selected.value = 'mg:cluster'

    // 菜单语义：共享引用→移除引用；私有→删除属性；动作→移除关联；规则→移除引用
    assert.equal(owApi().propMenuItems({ sharedId: 'mg:sp' })[0].label, '移除引用')
    assert.equal(owApi().propMenuItems({ sharedId: '' })[0].label, '删除属性')

    // 共享引用「移除引用」只删当前对象上的引用记录，共享定义保留
    const before = state.ontology['@graph'].length
    assert.ok(owApi().propMenuItems({ sharedId: 'mg:sp' })[0].danger, '移除/删除项必须是危险样式')
    await owApi().removeNode('mg:p1', '额定功率', '移除当前对象对共享属性「额定功率」的引用？')
    assert.equal(state.ontology['@graph'].length, before - 1, '引用记录被移除')
    assert.ok(state.ontology['@graph'].some(n => n['@id'] === 'mg:sp'), '共享定义必须保留')
    assert.equal(state.ontology['@graph'].filter(n => n['type'] === 'owl:DatatypeProperty' && n['id'] === 'mg:p1').length, 0)

    // 动作移除关联：定义与其他对象关联保留（先给 device 也加一条关联）
    state.workflow.actionAssociations.push({ objectTypeId: 'mg:device', actionId: 'act1' })
    await owApi().removeAssociation('act1')
    assert.equal(state.workflow.actionAssociations.filter(a => a.actionId === 'act1').length, 1, '只移除当前对象的关联')
    assert.equal(state.workflow.actionAssociations[0].objectTypeId, 'mg:device', '其它对象关联保留')
    assert.ok(state.workflow.actions.some(a => a.id === 'act1'), '动作定义保留')

    // 规则只读详情按稳定 ID 带出完整四字段
    owApi().ruleDetailId.value = 'rule1'
    assert.equal(owApi().ruleDetail.value.content, 'RC')
    assert.equal(owApi().ruleDetail.value.output, 'RO')
    check('② 链接真实方向/自链接不重复；③ 菜单语义与移除边界正确', () => assert.equal(0, 0))
  }

  // ── ⑤ 从资产库返回对象：focusDefinition + initialTab 应定位并高亮目标行 ──
  {
    const cluster = { '@id': 'mg:cluster', '@type': 'owl:Class', 'rdfs:label': '储能簇', 'rdfs:comment': 'a' }
    const action = { id: 'act1', name: '停止充放电', description: 'd', effect: 'e', definitionVersion: 2 }
    const state = reactive({
      ontology: { '@graph': [cluster] },
      workflow: { actions: [action], actionAssociations: [{ objectTypeId: 'mg:cluster', actionId: 'act1' }] },
      layout: {},
    })
    const app = mount(OW, state, { initialTab: 'actions', focusDefinition: 'act1' })
    await renderToString(app)
    assert.equal(owApi().detailTab.value, 'actions', '初始页签应为动作')
    assert.equal(owApi().highlightId.value, 'act1', '返回来源应高亮目标动作行（实际：' + owApi().highlightId.value + '）')
    check('⑤ 跨库返回：focusDefinition + initialTab 定位高亮', () => assert.equal(0, 0))
  }

  // ── ④ 三个资产库：行菜单不改语义 + 规则库无更多菜单 ──
  {
    const SL = await loadComponent('frontend/src/ontology/SharedLibrary.vue')
    const slApi = capture(SL)
    const s = { '@id': 'mg:sp', '@type': 'mg:SharedProperty', 'rdfs:label': '功率', 'rdfs:comment': 'c', 'rdfs:range': { '@id': 'xsd:double' } }
    const state = reactive({ ontology: { '@graph': [{ '@id': 'mg:c', '@type': 'owl:Class', 'rdfs:label': '簇', 'rdfs:comment': 'd' }, s] }, workflow: {} })
    await renderToString(mount(SL, state))
    const items = slApi().rowMenuItems(s)
    assert.deepEqual(items.map(i => i.id), ['reference', 'copy', 'delete'], '共享库行菜单=引用到对象/复制为私有/删除定义')
    assert.equal(items[2].label, '删除定义')
    assert.ok(items[2].danger, '删除定义危险色')

    const AL = await loadComponent('frontend/src/ontology/ActionLibrary.vue')
    const alApi = capture(AL)
    const a2 = { id: 'a1', name: '动作', description: 'd', effect: 'e', definitionVersion: 2 }
    const alState = reactive({ ontology: { '@graph': [{ '@id': 'mg:c', '@type': 'owl:Class', 'rdfs:label': '簇', 'rdfs:comment': 'd' }] }, workflow: { actions: [a2], actionAssociations: [{ objectTypeId: 'mg:c', actionId: 'a1' }] } })
    await renderToString(mount(AL, alState))
    // 被引用动作删除必须被拦截（提示而不是删除）
    await alApi().remove('a1')
    assert.ok(alState.workflow.actions.some(a => a.id === 'a1'), '被引用动作删除必须被拦截')
    assert.match(alApi().message.value, /仍被/, '应给出引用拦截原因：' + alApi().message.value)

    const BRL = readFileSync(resolve(SRC, 'BusinessRuleLibrary.vue'), 'utf8')
    assert.ok(!/RowMenu/.test(BRL), '规则库不得出现更多菜单（不新增删除/复制）')
    assert.ok(BRL.includes('RULE_FIELDS'), '规则编辑仍走既有四字段')
    check('④ 共享/动作菜单语义不变，规则库无更多菜单', () => assert.equal(0, 0))
  }
} finally {
  rmSync(root, { recursive: true, force: true })
}

const failed = results.filter(r => !r).length
console.log(`\n${results.length - failed}/${results.length} 项通过`)
if (failed) process.exit(1)
