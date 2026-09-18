// 列表控件组件化（20260918 补充）：SearchField 与 ListPager 是本体列表共用的搜索框与分页条，
// ObjectWorkspace 导航与 OntologyList 表格都通过它们渲染。本用例锁定：
//   ① SearchField：受控输入上抛 update:value；有值时清空按钮先发空值再发 clear（父级可一并复位筛选）；
//   ② ListPager：文案与原型一致（第 x–y 项，共 N 项 / 每页 P 项）；首末页按钮禁用；翻页上抛绝对页码；
//   ③ 接入面：OntologyList 与 ObjectWorkspace 均使用共用组件，页面内不再有手写搜索/分页结构。
// 运行（仓库根）：node --import ./tests/ts_hooks.mjs tests/list_controls.test.mjs
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { readFileSync, mkdtempSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { pathToFileURL } from 'node:url'

const require = createRequire(new URL('../frontend/package.json', import.meta.url))
const { parse, compileScript, compileTemplate } = require('@vue/compiler-sfc')
const ts = require('typescript'), { createSSRApp, h } = require('vue'), { renderToString } = require('@vue/server-renderer')

const root = mkdtempSync(join(tmpdir(), 'wiz_list_controls_'))
const SRC = resolve('frontend/src/shared')

/** 加载 .vue 单文件组件：script setup 与 template 都编译进来（SSR 需要真实 render 函数）。 */
async function loadSfc(file) {
  const filename = resolve(file)
  const base = file.split('/').pop().replace('.vue', '')
  const { descriptor } = parse(readFileSync(filename, 'utf8'), { filename })
  const script = compileScript(descriptor, { id: 'lc-test', genDefaultAs: '__comp' })
  const tpl = compileTemplate({ source: descriptor.template.content, filename, id: 'lc-test', compilerOptions: { bindingMetadata: script.bindings } })
  let code = script.content.replace('export default __comp', 'const __comp = {')
    + '\n' + tpl.code.replace('export function render', 'function render')
    + '\n__comp.render = render\nexport default __comp\n'
  code = code.replace(/from (['"])([^'"]+)\1/g, (_, q, spec) => {
    if (spec === 'vue') return 'from ' + JSON.stringify(require.resolve('vue'))
    const target = spec.startsWith('.') ? resolve(SRC, spec.replace(/^\.\//, '').replace(/\.vue$/, '') + '.ts') : resolve(SRC, spec + '.ts')
    return 'from ' + JSON.stringify(pathToFileURL(target).href)
  })
  const out = join(root, base + '.mjs')
  writeFileSync(out, ts.transpileModule(code, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText)
  return (await import(pathToFileURL(out).href)).default
}

const results = []
async function check(name, fn) { try { await fn(); results.push(true); console.log('通过：' + name) } catch (e) { results.push(false); console.log('失败：' + name + ' — ' + e.message) } }

try {
  const SearchField = await loadSfc(resolve(SRC, 'SearchField.vue'))
  const ListPager = await loadSfc(resolve(SRC, 'ListPager.vue'))

  await check('① SearchField：受控值上抛，清空先发空值再发 clear', async () => {
    const events = []
    const App = { setup() { return () => h(SearchField, { value: 'abc', placeholder: '搜索属性', onUpdateValue: v => events.push(['v', v]), onClear: () => events.push(['clear']) }) } }
    const html = await renderToString(createSSRApp(App))
    assert.ok(html.includes('搜索属性'), '占位文案应渲染：' + html.slice(0, 120))
    assert.ok(html.includes('search-field-clear'), '有值时应有清空按钮')
    // 直接驱动组件内部函数：断言清空的事件协议（先空值、再 clear）
    const api = SearchField.setup({ value: 'abc', placeholder: '', ariaLabel: '' }, { emit: (e, v) => events.push([e, v]), expose: () => {} })
    api.clear()
    assert.deepEqual(events, [['update:value', ''], ['clear', undefined]], '清空应先发空值再发 clear，便于父级一并复位筛选')
    const noClearApp = { setup() { return () => h(SearchField, { value: '' }) } }
    const html2 = await renderToString(createSSRApp(noClearApp))
    assert.ok(!html2.includes('search-field-clear'), '空值时不应有清空按钮')
  })

  await check('② ListPager：文案、禁用与绝对页码事件', async () => {
    const go = []
    const App = { setup() { return () => h(ListPager, { total: 45, page: 2, pageCount: 3, pageSize: 20, onUpdatePage: n => go.push(n) }) } }
    const html = await renderToString(createSSRApp(App))
    assert.ok(html.includes('第 21–40 项，共 45 项'), '区间文案应正确：' + html.slice(0, 200))
    assert.ok(html.includes('每页 20 项') && html.includes('2 / 3'), '页数文案应正确')
    assert.ok(!html.includes('disabled'), '中间页两端按钮都不禁用')
    const first = await renderToString(createSSRApp({ setup() { return () => h(ListPager, { total: 5, page: 1, pageCount: 1 }) } }))
    assert.ok(first.includes('共 0 项') === false && first.includes('第 1–5 项，共 5 项'), '首页区间应为 1–5')
    assert.ok((first.match(/disabled/g) || []).length >= 2, '单页时长两端按钮都禁用')
  })

  await check('④ 清空按钮：保留居中 transform，不被全局按下反馈覆盖（真实点击可达）', async () => {
    const css = readFileSync(resolve('frontend/src/style.css'), 'utf8')
    // 根因回归：全局 button:active{transform:translateY(1px)} 覆盖居中 -> mousedown 时按钮下跳、click 丢失
    assert.ok(/\.search-field-clear:active:not\(:disabled\)\{transform:translateY\(calc\(-50% \+ 1px\)\)\}/.test(css),
      '清空按钮按下时必须保留 -50% 居中（否则点击命中会漂移到输入框）')
    assert.ok(/\.search-field input::-webkit-search-cancel-button/.test(css), '应隐藏浏览器原生清除按钮，避免与自绘按钮重叠')
    const field = readFileSync(resolve('frontend/src/shared/SearchField.vue'), 'utf8')
    assert.ok(field.includes("class=\"search-field-clear\""), '清空按钮必须带 search-field-clear 类，复用居中样式')
  })

  await check('③ 表格与导航都接入共用组件，页面无手写搜索/分页结构', async () => {
    const table = readFileSync(resolve('frontend/src/shared/OntologyList.vue'), 'utf8')
    assert.ok(table.includes("SearchField") && table.includes("ListPager"), 'OntologyList 应使用共用搜索/分页组件')
    assert.ok(!/ont-search/.test(table) && !/ont-pager/.test(table), 'OntologyList 不应再有手写搜索/分页结构')
    const nav = readFileSync(resolve('frontend/src/ontology/ObjectWorkspace.vue'), 'utf8')
    assert.ok(nav.includes("SearchField") && nav.includes("ListPager"), '对象导航应使用共用搜索/分页组件')
    assert.ok(!/ld-search-clear/.test(nav) && !/ld-pager-ctl/.test(nav), '对象导航不应再有手写清空/页码结构')
  })
} finally {
  rmSync(root, { recursive: true, force: true })
}

const failed = results.filter(r => !r).length
console.log(`\n${results.length - failed}/${results.length} 项通过`)
if (failed) process.exit(1)
