// 图谱工具栏布局回归（2026-09-20 用户反馈「选中节点之后，工具栏样式有问题」）：
// 选中单个节点会多出「1跳/2跳」，工具行内容变宽；此前 .tool-row 为 nowrap 且无横向滚动，
// 右端的「最大化」被挤出视口右缘裁切。修复 = 状态与视图按钮合成 .tool-right 整组，
// 工具行允许换行、整组整体落到第二行，窄屏（≤1100）改单行横向滚动。
// 同时锁定：工具栏布局只在一处定义（scoped），legacy.css 不再重复写 nowrap/wrap 打架。
// 运行（仓库根）：node --import ./tests/ts_hooks.mjs tests/graph_toolbar_layout.test.mjs
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const editor = readFileSync(resolve('frontend/src/ontology/legacyGraph/EditorView.vue'), 'utf8')
const css = readFileSync(resolve('frontend/src/ontology/legacyGraph/legacy.css'), 'utf8')

let passed = 0
function check(name, fn) {
  try { fn(); passed++; console.log('  通过：' + name) }
  catch (err) { console.error('  失败：' + name + '\n    ' + err.message); process.exitCode = 1 }
}

console.log('== 图谱工具栏布局 ==')

// 模板：状态 + 视图按钮在同一 .tool-right 组内（选中后新增的 1跳/2跳 也在组内）
check('视图操作簇把状态与按钮合成一组', () => {
  const group = editor.slice(editor.indexOf('<div class="tool-right">'), editor.indexOf('</div>', editor.indexOf('inspector-max')))
  for (const needle of ['class="status"', 'inspector-fit-all', 'inspector-toggle', 'inspector-max', 'singleNodeSelected'])
    assert.ok(group.includes(needle), 'tool-right 组内应包含 ' + needle)
})
check('1跳/2跳 邻域按钮属于视图操作簇（不再单独追加到行尾）', () => {
  const group = editor.slice(editor.indexOf('<div class="tool-right">'), editor.indexOf('</div>', editor.indexOf('inspector-max')))
  assert.match(group, /setHop\(hopMode === 1/, '1跳应在组内')
  assert.match(group, /setHop\(hopMode === 2/, '2跳应在组内')
})

// 样式：宽屏允许换行、组内不换行；窄屏回退横向滚动
check('.tool-row 宽屏允许换行（不再把右端按钮挤出容器）', () => {
  const rule = editor.slice(editor.indexOf('.tool-row {'), editor.indexOf('}', editor.indexOf('.tool-row {')))
  assert.match(rule, /flex-wrap:\s*wrap/, '应允许换行')
})
check('.tool-right 整组不换行且右对齐', () => {
  const rule = editor.slice(editor.indexOf('.tool-right {'), editor.indexOf('}', editor.indexOf('.tool-right {')))
  assert.match(rule, /margin-left:\s*auto/, '应右对齐')
  assert.match(rule, /flex-wrap:\s*nowrap/, '组内应保持一排')
})
check('窄屏（≤1100）工具行改横向滚动，画布不被换行挤到 0 高', () => {
  const media = editor.slice(editor.indexOf('@media (max-width: 1100px)'))
  assert.match(media, /\.tool-row\s*\{[^}]*flex-wrap:\s*nowrap/, '窄屏应收回单行')
  assert.match(media, /overflow-x:\s*auto/, '窄屏应可横向滚动')
})
check('状态区不再单独 sticky/竖线（并入组后由组间距承担）', () => {
  const rule = editor.slice(editor.indexOf('/* 状态并入'), editor.indexOf('}', editor.indexOf('/* 状态并入')))
  assert.ok(!/position:\s*sticky/.test(rule), '组内状态不应再 sticky')
  assert.ok(!/border-left/.test(rule), '组内状态不应再有分隔竖线')
})

// 布局单点定义：legacy.css 不再与 scoped 重复声明工具行 nowrap/wrap
check('legacy.css 不再重复声明工具行布局（避免同优先级互相覆盖）', () => {
  const toolbarRules = css.match(/\.legacy-editor-root \.tool-row\s*\{[^}]*\}/g) || []
  for (const rule of toolbarRules)
    assert.ok(!/flex-wrap|overflow-x/.test(rule), 'legacy.css 不应再定义工具行换行/滚动：' + rule)
})
check('legacy.css 的窄屏段不再覆盖工具行（只保留 topbar 与快捷键提示）', () => {
  const media = css.slice(css.indexOf('@media (max-width: 1100px)'))
  assert.ok(!/\.tool-row/.test(media.slice(0, media.indexOf('}') + 1)), '窄屏段不应再写 .tool-row')
})

console.log('\n统计：' + passed + ' 项通过' + (process.exitCode ? '（存在失败）' : ''))
