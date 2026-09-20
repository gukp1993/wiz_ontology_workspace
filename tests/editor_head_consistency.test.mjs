// 编辑表单头部一致性回归（2026-09-20 用户三项修正 ①②）：
// 五类编辑表单（对象/链接/私有属性/共享属性/业务规则/动作）的「返回图谱」控件必须来自同一共享组件
// EditorHead，并且都挂在表单卡内的同一位置（卡片第一子元素）——此前规则表单完全没有该控件、
// 动作的返回入口留在页头右上角，与其他页不一致（用户反馈「规则缺少返回图谱」「动作返回图谱样式不统一」）。
// 同时锁定：编辑态不再渲染列表页头（动作列表头此前在编辑态也显示，返回按钮位置因此不同）。
// 运行（仓库根）：node --import ./tests/ts_hooks.mjs tests/editor_head_consistency.test.mjs
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const read = p => readFileSync(resolve(p), 'utf8')
const SRC = 'frontend/src/ontology'

let passed = 0
function check(name, fn) {
  try { fn(); passed++; console.log('  通过：' + name) }
  catch (err) { console.error('  失败：' + name + '\n    ' + err.message); process.exitCode = 1 }
}

console.log('== 编辑表单头部一致性 ==')

// 共享组件本身：canvasReturn 非空显示「返回图谱」+「关闭」，否则显示 backLabel
const head = read('frontend/src/shared/EditorHead.vue')
check('EditorHead 在 canvasReturn 时渲染「← 返回图谱」', () => {
  assert.match(head, /v-if="canvasReturn"[\s\S]{0,80}← 返回图谱/, '应含返回图谱按钮')
})
check('EditorHead 从图谱进入时另给「关闭」入口', () => {
  assert.match(head, /closeLabel \|\| '关闭'/, '关闭按钮文案应有默认值')
})
check('EditorHead 无 canvasReturn 时回落到 backLabel 返回入口', () => {
  assert.match(head, /v-else[^>]*>\{\{ backLabel \}\}/, '非图谱来源应渲染 backLabel')
})

// 四类页面级编辑表单：都必须 import 并渲染 EditorHead（不再各自复制按钮行）
const CALLERS = [
  ['对象与链接编辑表单', SRC + '/ObjectWorkspace.vue'],
  ['业务规则编辑表单', SRC + '/BusinessRuleLibrary.vue'],
  ['动作编辑表单', SRC + '/ActionLibrary.vue'],
]
for (const [label, file] of CALLERS) {
  const src = read(file)
  check(label + ' 引用共享 EditorHead', () => {
    assert.match(src, /import EditorHead from '\.\.\/shared\/EditorHead\.vue'/, '应 import EditorHead')
    assert.match(src, /<EditorHead[^>]*:canvas-return="canvasReturn"/, '应把 canvasReturn 透传给 EditorHead')
    assert.match(src, /@back-to-graph="backToGraph"/, '应接住返回图谱事件')
  })
  check(label + ' 不再残留自复制的头部按钮行', () => {
    assert.ok(!/ow-editor-head|prop-form-head/.test(src), '旧独立头部类名应已清理')
  })
  check(label + ' 返回图谱事件接到既有 backToGraph', () => {
    assert.match(src, /function backToGraph\(\)/, '应保留 backToGraph 实现')
    assert.match(src, /emit\('navigate', 'objects', \{ graph: true/, '回图谱应走既有导航契约')
  })
}

// 属性表单是子组件：自己不做导航，把返回图谱事件交给宿主（对象页/共享库各自 backToGraph）
const propMgr = read(SRC + '/PropertyManager.vue')
check('属性编辑表单引用共享 EditorHead 并上抛返回事件', () => {
  assert.match(propMgr, /import EditorHead from '\.\.\/shared\/EditorHead\.vue'/, '应 import EditorHead')
  assert.match(propMgr, /<EditorHead[^>]*:canvas-return="props\.canvasReturn"/, '应把 props.canvasReturn 透传')
  assert.match(propMgr, /@back-to-graph="emit\('back-to-graph'\)"/, '应上抛 back-to-graph 交给宿主处理')
  assert.ok(!/prop-form-head/.test(propMgr), '旧独立头部类名应已清理')
})
check('属性表单宿主（对象页/共享库）接住 back-to-graph 并走既有导航契约', () => {
  for (const file of [SRC + '/ObjectWorkspace.vue', SRC + '/SharedLibrary.vue']) {
    const src = read(file)
    assert.match(src, /@back-to-graph="backToGraph"/, file + ' 应接住属性表单的返回事件')
    assert.match(src, /function backToGraph\(\)/, file + ' 应保留 backToGraph 实现')
  }
})

// 规则表单此前完全没有返回图谱：现在必须无条件渲染 EditorHead（不再包 v-if）
const rule = read(SRC + '/BusinessRuleLibrary.vue')
check('规则编辑表单无条件渲染 EditorHead（此前完全缺失）', () => {
  const card = rule.slice(rule.indexOf('v-if="mode === \'edit\'"'))
  assert.match(card.slice(0, 400), /<EditorHead/, '编辑卡首部应是 EditorHead')
})
check('规则编辑表单提供列表返回入口', () => {
  assert.match(rule, /back-label="← 返回规则列表"/, '非图谱来源应有返回列表入口')
})

// 动作：页头只在列表态渲染（此前编辑态也显示，返回按钮留在页头右上角）
const action = read(SRC + '/ActionLibrary.vue')
check('动作列表页头限定列表态渲染', () => {
  assert.match(action, /<section v-if="mode === 'list'" class="card ont-block">[\s\S]{0,600}ont-lib-head/, '页头应在列表态卡片内')
})
check('动作编辑表单头部与其它页同构（EditorHead + 返回动作列表）', () => {
  assert.match(action, /<EditorHead[^>]*back-label="← 返回动作列表"/, '编辑卡应渲染 EditorHead')
})
check('动作编辑卡是列表卡的 else 分支（两态互斥渲染）', () => {
  assert.match(action, /<section v-else class="card detail-card/, '编辑卡应为 v-else')
})

console.log('\n统计：' + passed + ' 项通过' + (process.exitCode ? '（存在失败）' : ''))
