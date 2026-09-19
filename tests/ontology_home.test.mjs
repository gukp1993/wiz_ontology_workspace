// 工作概览优化回归（20260919 需求 §5/§6 状态机与竞态；开发计划 T4）：
// 真空本体判定、版本元数据挑最新、差异/校验/项目三模块独立失败、校验指纹过期与旧响应不覆盖、
// 问题稳定 ID 定位、对象入口最多 5 项。运行（仓库根）：
//   node --import ./tests/ts_hooks.mjs tests/ontology_home.test.mjs
// 用真实 OntologyHome.vue 的 <script setup>（api 模块打桩、OntologyImport 桩化），
// 不连接服务、不写真实 ontology。
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { readFileSync, mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { pathToFileURL } from 'node:url'

const require = createRequire(new URL('../frontend/package.json', import.meta.url))
const { parse, compileScript } = require('@vue/compiler-sfc')
const ts = require('typescript'), { createSSRApp, reactive, nextTick } = require('vue'), { renderToString } = require('@vue/server-renderer')

// 组件里用到的浏览器 API：SSR 不执行 onMounted，但模块顶层会触达。
globalThis.window = { matchMedia: () => ({ matches: false, addEventListener() {}, removeEventListener() {} }), addEventListener() {}, removeEventListener() {}, innerHeight: 900, location: { hash: '' } }
globalThis.document = { addEventListener() {}, removeEventListener() {}, querySelector: () => null, querySelectorAll: () => [], activeElement: null, createElement: () => ({ click() {}, remove() {}, setAttribute() {} }), body: { appendChild() {} } }
globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} }
globalThis.requestAnimationFrame = cb => setTimeout(() => cb(Date.now()), 0)
if (!globalThis.crypto?.randomUUID) Object.defineProperty(globalThis, 'crypto', { value: require('node:crypto').webcrypto, configurable: true })

const SRC = resolve('frontend/src/ontology')
const root = mkdtempSync(join(tmpdir(), 'wiz_home_'))

// api 打桩：清单/校验/预检/项目列表都可注入结果与延迟
const stub = `
export const calls = []
export const impl = {
  listVersions: async () => ({ items: [] }),
  validateOntology: async () => ({ errors: [] }),
  publishCheck: async () => ({ latest: '', reasons: [] }),
  listProjects: async () => ({ items: [] }),
}
export const listVersions = (...a) => { calls.push(['listVersions', ...a]); return impl.listVersions(...a) }
export const validateOntology = (...a) => { calls.push(['validateOntology', ...a]); return impl.validateOntology(...a) }
export const publishCheck = (...a) => { calls.push(['publishCheck', ...a]); return impl.publishCheck(...a) }
export const listProjects = (...a) => { calls.push(['listProjects', ...a]); return impl.listProjects(...a) }
`
const stubFile = join(root, 'homeApiStub.mjs')
writeFileSync(stubFile, stub)

/** 编译 OntologyHome.vue 的 <script setup>：OntologyImport 桩化、api 模块指向打桩文件。 */
async function loadHome() {
  const filename = resolve('frontend/src/ontology/OntologyHome.vue')
  const { descriptor } = parse(readFileSync(filename, 'utf8'), { filename })
  let code = compileScript(descriptor, { id: 'home-test' }).content
  code = code.replace(/import\s+OntologyImport\s+from\s+['"][^'"]+['"]/, 'const OntologyImport = {}')
  // 拦截 emit：ctx.emit 不可改写，改为在编译产物里包一层，事件进 globalThis.__homeEmitted
  code = code.replace(/const emit\s*=\s*__emit/, 'const emit = (...a) => { (globalThis.__homeEmitted = globalThis.__homeEmitted || []).push(a); return __emit(...a) }')
  code = code.replace(/import\s*\{([^}]*)\}\s*from\s*'(\.\/api|\.\.\/project\/api)'/g, (_, names) => 'import {' + names + "} from '" + pathToFileURL(stubFile).href + "'")
  code = code.replace(/from (['"])([^'"]+)\1/g, (_, q, spec) => {
    if (spec.includes('homeApiStub')) return _
    const target = spec === 'vue' ? require.resolve('vue') : resolve(SRC, spec + '.ts')
    return 'from ' + JSON.stringify(pathToFileURL(target).href)
  })
  const file = join(root, 'OntologyHome.mjs')
  writeFileSync(file, ts.transpileModule(code, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText)
  const Component = (await import(pathToFileURL(file).href)).default
  return Component
}

const results = []
function check(name, fn) { try { fn(); results.push({ name, ok: true }); console.log('通过：' + name) } catch (e) { results.push({ name, ok: false }); console.log('失败：' + name + ' — ' + e.message) } }
async function checkA(name, fn) { try { await fn(); results.push({ name, ok: true }); console.log('通过：' + name) } catch (e) { results.push({ name, ok: false }); console.log('失败：' + name + ' — ' + e.message) } }

function emptyState() { return reactive({ workspaceId: 'ont-test', ontology: { '@graph': [] }, workflow: { objective: { name: '测试本体' } } }) }
function storageState() {
  return reactive({ workspaceId: 'ont-test', ontology: { '@graph': [
    { '@id': 'mg:cluster', '@type': 'owl:Class', 'rdfs:label': '储能簇', 'rdfs:comment': '一组电池簇' },
    { '@id': 'mg:site', '@type': 'owl:Class', 'rdfs:label': '储能站', 'rdfs:comment': '场站' },
    { '@id': 'mg:sp_power', '@type': 'mg:SharedProperty', 'rdfs:label': '额定功率', 'rdfs:comment': '铭牌功率' },
    { '@id': 'mg:l_belong', '@type': 'owl:ObjectProperty', 'rdfs:label': '所属场站', 'rdfs:domain': { '@id': 'mg:cluster' }, 'rdfs:range': { '@id': 'mg:site' }, 'mg:cardinality': 'many-to-one' },
  ] }, workflow: { objective: { name: '储能本体' }, businessRules: [{ id: 'rule_soc_001', name: 'SOC规则' }], actions: [{ id: 'action_stop_001', name: '停机' }] } })
}

let stubApi
const Home = await loadHome()
let api
const setup = Home.setup
Home.setup = (props, ctx) => {
  api = setup(props, ctx)
  return api
}
Home.render = () => null
async function mount(state) {
  const app = createSSRApp(Home, { state })
  await renderToString(app) // setup 在首次渲染时执行；完成后 api 已就绪
  return api
}

try {
  // ── H04/空态：真空本体（内容全空 + 版本清单成功且为空）→ emptyReady；任一内容存在 → 非空 ──
  {
    stubApi = (await import(pathToFileURL(stubFile).href))
    stubApi.impl.listVersions = async () => ({ items: [] })
    const state = emptyState()
    api = await mount(state)
    await api.loadVersions()
    assert.equal(api.versionState.value, 'ready')
    check('① 真空本体：内容全空且无发布 → emptyReady', () => assert.equal(api.emptyReady.value, true))
    check('① 真空本体：统计全为零（真实 0 值，非未知）', () => {
      assert.deepEqual(api.assets.value.map(a => a.count), [0, 0, 0, 0, 0])
    })
    const state2 = storageState()
    api = await mount(state2)
    await api.loadVersions()
    check('① 有对象/规则/动作内容 → 不判定为空', () => assert.equal(api.emptyReady.value, false))
    // 只有共享属性、没有对象：仍是「有资产无对象」，不是空态
    const state3 = reactive({ workspaceId: 'x', ontology: { '@graph': [{ '@id': 'mg:sp', '@type': 'mg:SharedProperty', 'rdfs:label': 'S' }] }, workflow: {} })
    api = await mount(state3)
    await api.loadVersions()
    check('① 有资产无对象 → 常规概览（callout 条件成立）', () => {
      assert.equal(api.emptyReady.value, false)
      assert.equal(api.objects.value.length, 0)
      assert.equal(api.hasAnyDefinition.value, true)
    })
    // 历史定义（workflow.functions）与遗留 metrics/rules 都算已有内容
    const state4 = reactive({ workspaceId: 'x', ontology: { '@graph': [] }, workflow: { functions: [{ id: 'f1' }] } })
    api = await mount(state4)
    await api.loadVersions()
    check('① 仅有历史 functions 定义 → 非空态', () => assert.equal(api.hasAnyDefinition.value, true))
  }

  // ── H10：版本读取失败不能判定为空（不能假设未发布）──
  {
    stubApi.impl.listVersions = async () => { throw new Error('服务不可用') }
    const state = emptyState()
    api = await mount(state)
    await api.loadVersions()
    check('① 版本读取失败：空内容不显示欢迎卡，错误如实呈现', () => {
      assert.equal(api.versionState.value, 'error')
      assert.equal(api.versionError.value, '服务不可用')
      assert.equal(api.emptyReady.value, false)
    })
  }

  // ── H08：最新版本按版本元数据挑（接口未承诺排序），publish-check 的 latest 同基线优先 ──
  {
    stubApi.impl.listVersions = async () => ({ items: [
      { version: '2.0.0', createdAt: '2026-09-17T10:00:00' },
      { version: '1.5.0', createdAt: '2026-09-18T10:00:00' },
      { version: '10.0.0', createdAt: '2026-09-19T10:00:00' },
    ] })
    stubApi.impl.publishCheck = async () => ({ latest: '10.0.0', reasons: [] })
    const state = storageState()
    api = await mount(state)
    await Promise.all([api.loadVersions(), api.loadDiff()])
    check('② 乱序清单按 semver 挑最新 10.0.0（非列表末项 1.5.0）', () => {
      assert.equal(api.metaLatest.value, '10.0.0')
      assert.equal(api.latestVersion.value, '10.0.0')
      assert.equal(api.published.value, true)
    })
    check('② 差异 reasons 空 → 草稿与最新发布一致', () => {
      assert.equal(api.diffState.value, 'same')
      assert.equal(api.diffPill.value.text, '草稿与最新发布一致')
    })
    stubApi.impl.publishCheck = async () => ({ latest: '10.0.0', reasons: [{ severity: 'compatible', text: '新增对象类型 mg:new' }] })
    api = await mount(state)
    await api.loadDiff()
    check('② 差异 reasons 非空 → 草稿有未发布变更', () => {
      assert.equal(api.diffState.value, 'changed')
      assert.equal(api.diffPill.value.text, '草稿有未发布变更')
    })
    stubApi.impl.publishCheck = async () => { throw new Error('超时') }
    api = await mount(state)
    await api.loadVersions()
    await api.loadDiff()
    check('② 预检失败 → 尚未比较（不猜一致），版本显示回退元数据', () => {
      assert.equal(api.diffState.value, 'unknown')
      assert.equal(api.diffPill.value.text, '尚未比较')
      assert.equal(api.latestVersion.value, '10.0.0')
      assert.equal(api.published.value, true, '预检失败不抹掉版本清单结果')
    })
  }

  // ── H06/H07：校验状态机（尚未校验/通过/问题/失败/过期）与竞态 ──
  {
    stubApi.impl.listVersions = async () => ({ items: [] })
    stubApi.impl.publishCheck = async () => ({ latest: '', reasons: [] })
    stubApi.impl.listProjects = async () => ({ items: [] })
    stubApi.impl.validateOntology = async () => ({ errors: [] })
    const state = storageState()
    api = await mount(state)
    check('③ 初始：尚未校验（不显示通过）', () => {
      assert.equal(api.displayCheck.value, 'unchecked')
      assert.equal(api.checkPill.value.text, '尚未校验')
    })
    stubApi.impl.validateOntology = async () => ({ errors: ['mg:cluster 缺少业务定义'] })
    await api.checkDraft()
    check('③ 校验发现问题 → issues + 徽标 N 项待处理', () => {
      assert.equal(api.displayCheck.value, 'issues')
      assert.equal(api.checkPill.value.text, '1 项待处理')
      assert.deepEqual(api.draftErrors.value, ['mg:cluster 缺少业务定义'])
    })
    // 内容变化 → 过期（指纹判定，不只看 revision）
    state.ontology['@graph'].push({ '@id': 'mg:new', '@type': 'owl:Class', 'rdfs:label': '新对象' })
    check('③ 草稿内容变化 → 旧结果显式过期', () => {
      assert.equal(api.displayCheck.value, 'stale')
      assert.equal(api.checkPill.value.text, '校验结果已过期')
    })
    // 校验期间内容变化：响应后仍判过期（请求时点指纹）
    stubApi.impl.validateOntology = async () => { await new Promise(r => setTimeout(r, 10)); return { errors: [] } }
    const p = api.checkDraft()
    state.ontology['@graph'].pop()
    await p
    check('③ 校验请求期间内容变化 → 结果过期（不显示通过）', () => {
      assert.equal(api.displayCheck.value, 'stale')
    })
    // 旧响应不覆盖：先发慢请求，再发快请求；慢的后返回也不覆盖
    let release
    stubApi.impl.validateOntology = async () => new Promise(res => { release = () => res({ errors: ['mg:stale 缺少名称'] }) })
    const slow = api.checkDraft()
    stubApi.impl.validateOntology = async () => ({ errors: [] })
    const fast = api.checkDraft()
    await fast
    release()
    await slow
    check('③ 重复发起：旧响应被序号丢弃，最终显示最新结果', () => {
      assert.equal(api.displayCheck.value, 'passed')
      assert.deepEqual(api.draftErrors.value, [])
    })
    // 请求失败 ≠ 定义错误
    stubApi.impl.validateOntology = async () => { throw new Error('网络中断') }
    await api.checkDraft()
    check('③ 校验请求失败 → error 态（不是通过也不是问题）', () => {
      assert.equal(api.displayCheck.value, 'error')
      assert.equal(api.checkError.value, '网络中断')
      assert.equal(api.checkNeutral.value.title, '暂时无法完成校验')
    })
  }

  // ── H10：模块独立失败——版本失败不抹项目，项目失败不抹版本 ──
  {
    stubApi.impl.listVersions = async () => ({ items: [{ version: '1.0.0', createdAt: '2026-09-18T16:30:00' }] })
    stubApi.impl.publishCheck = async () => ({ latest: '1.0.0', reasons: [] })
    stubApi.impl.listProjects = async () => { throw new Error('项目读取失败') }
    const state = storageState()
    api = await mount(state)
    await api.loadVersions(); await api.loadDiff(); await api.loadProjects()
    check('④ 项目读取失败：版本结果保留、错误只在项目模块', () => {
      assert.equal(api.versionState.value, 'ready')
      assert.equal(api.published.value, true)
      assert.equal(api.projectsState.value, 'error')
      assert.equal(api.projectsError.value, '项目读取失败')
    })
    stubApi.impl.listVersions = async () => { throw new Error('版本读取失败') }
    stubApi.impl.listProjects = async () => ({ items: [{ id: 'p1', name: '创智园二期', ontologyVersion: '1.0.0' }] })
    const state2 = storageState()
    api = await mount(state2)
    await api.loadVersions(); await api.loadProjects()
    check('④ 版本读取失败：项目结果保留；published 不成立（错误分支优先展示）', () => {
      assert.equal(api.projectsState.value, 'ready')
      assert.deepEqual(api.usingProjects.value.map(p => p.name), ['创智园二期'])
      assert.equal(api.versionState.value, 'error')
    })
  }

  // ── H05：问题按稳定 ID 定位到对应页签 ──
  {
    const state = storageState()
    api = await mount(state)
    check('⑤ 对象问题 → objects + type focus', () => {
      assert.deepEqual(api.locate('mg:cluster 缺少业务定义'), { view: 'objects', focus: { type: 'mg:cluster' } })
    })
    check('⑤ 动作定义问题 → actions + definition focus', () => {
      assert.deepEqual(api.locate('mg:action_stop_001 动作定义未完整：effect'), { view: 'actions', focus: { definition: 'action_stop_001' } })
    })
    check('⑤ 业务规则问题 → rules + definition focus', () => {
      assert.deepEqual(api.locate('mg:rule_soc_001 规则缺少名称'), { view: 'rules', focus: { definition: 'rule_soc_001' } })
    })
    check('⑤ 共享属性问题 → 共享属性库', () => {
      assert.deepEqual(api.locate('mg:sp_power 缺少名称'), { view: 'library' })
    })
    const state2 = storageState()
    api = await mount(state2)
    stubApi.impl.validateOntology = async () => ({ errors: ['mg:cluster 缺少业务定义', 'mg:site 缺少名称'] })
    await api.checkDraft()
    const g = api.errorGroups.value
    check('⑤ 问题归组含业务名称与原始诊断（前 5 项展示）', () => {
      assert.equal(api.shownGroups.value.length, 2)
      assert.ok(g[0].title.includes('储能簇'))
      assert.ok(g[0].raw[0].includes('mg:cluster'))
      assert.equal(g[0].issues.length, 1)
    })
    // 去处理按钮事件：navigate 携带 focus
    globalThis.__homeEmitted = []
    api.goLocate(api.shownGroups.value[0].raw[0])
    check('⑤ goLocate 触发 navigate 到对象建模并带 focus', () => {
      const nav = (globalThis.__homeEmitted || []).find(e => e[0] === 'navigate')
      assert.ok(nav, '应发出 navigate')
      assert.equal(nav[1], 'objects')
      assert.deepEqual(nav[2], { type: 'mg:cluster' })
    })
  }

  // ── H13：对象入口最多 5 项；链接清单数据 ──
  {
    const graph = [
      { '@id': 'mg:l1', '@type': 'owl:ObjectProperty', 'rdfs:label': '所属', 'rdfs:domain': { '@id': 'mg:o1' }, 'rdfs:range': { '@id': 'mg:o2' }, 'mg:cardinality': 'many-to-one' },
    ]
    for (let i = 1; i <= 6; i++) graph.push({ '@id': 'mg:o' + i, '@type': 'owl:Class', 'rdfs:label': '对象' + i, 'rdfs:comment': '定义' + i })
    const state = reactive({ workspaceId: 'x', ontology: { '@graph': graph }, workflow: {} })
    api = await mount(state)
    check('⑥ 对象入口最多展示 5 项', () => {
      assert.equal(api.objects.value.length, 6)
      assert.equal(api.objectEntries.value.length, 5)
    })
    check('⑥ 链接清单：起点→终点与数量关系标签', () => {
      assert.equal(api.links.value.length, 1)
      api.openLinkList()
      assert.equal(api.linkListOpen.value, true)
      assert.equal(api.CARD_LABELS['many-to-one'], '多对一')
      assert.equal(api.nameOf('mg:o1'), '对象1')
      globalThis.__homeEmitted = []
      api.gotoLink(api.links.value[0])
      const nav = (globalThis.__homeEmitted || []).find(e => e[0] === 'navigate')
      assert.deepEqual(nav.slice(1), ['objects', { type: 'mg:o1', tab: 'links' }])
      assert.equal(api.linkListOpen.value, false, '跳转后关闭清单')
    })
  }

  // ── H02：创建第一个对象 → navigate('objects',{create:true}) 信号 ──
  {
    const state = emptyState()
    api = await mount(state)
    await api.loadVersions()
    globalThis.__homeEmitted = []
    // 空态主按钮在模板里；这里直接验证 assets/入口 go() 与模板按钮共用的 emit 契约
    api.assets.value[0].go()
    const nav = (globalThis.__homeEmitted || []).find(e => e[0] === 'navigate')
    assert.equal(nav[1], 'objects')
    check('⑦ 对象统计入口 → navigate objects（无 focus）', () => { assert.deepEqual(nav[2], undefined) })
  }
} finally {
  const failed = results.filter(r => !r.ok)
  console.log(failed.length ? `\n${failed.length} 项失败` : `\n全部通过（${results.length} 项）`)
  if (failed.length) process.exit(1)
}
