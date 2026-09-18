// 编辑历史管理器回归（20260918 撤销范围优化）：具名条目/候选事务/合并/上限/scope。
// 运行（仓库根）：node --import ./tests/ts_hooks.mjs tests/undo_history.test.mjs
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { pathToFileURL } from 'node:url'
import { resolve as resolvePath } from 'node:path'
const require = createRequire(new URL('../frontend/package.json', import.meta.url))
const { ref } = require('vue')
const { createUndoArea } = await import(pathToFileURL(resolvePath('frontend/src/app/workspace.ts')).href)

const results = []
async function check(name, fn) {
  try { await fn(); results.push({ name, ok: true }); console.log('通过：' + name) }
  catch (e) { results.push({ name, ok: false }); console.log('失败：' + name + ' — ' + e.message) }
}

await check('① 具名条目：push 记录 actionLabel，undo/redo 标签可读', async () => {
  const cur = ref({ v: 1 })
  const area = createUndoArea(cur)
  const before = { v: 1 }
  area.push('新建对象「储能簇」')  // before-change：先快照
  cur.value = { v: 2 }            // 再变更
  assert.equal(area.peekUndoLabel(), '新建对象「储能簇」')
  const restored = area.undo()
  assert.deepEqual(restored, before)
  assert.equal(area.peekRedoLabel(), '新建对象「储能簇」')
  assert.equal(area.counts.value[0], 0)
  assert.equal(area.counts.value[1], 1)
})

await check('② 候选事务：commit 记录一条；cancel 不入栈且不清 redo', async () => {
  const cur = ref({ list: [] })
  const area = createUndoArea(cur)
  // 先建立一步已入栈历史（验证 redo 保留）
  cur.value = { list: ['a'] }
  area.push('加入 a')
  area.undo()
  assert.equal(area.counts.value[1], 1, 'redo 有一项')
  // 候选事务：成功路径
  const tx = area.begin({ mergeKey: 'list:add' })
  cur.value = { list: ['a', 'b'] }
  tx.commit('加入 b', cur.value)
  assert.equal(area.counts.value[0], 1, 'commit 入栈一条')
  assert.equal(area.counts.value[1], 0, '新修改清空 redo')
  // 失败路径：cancel 不入栈、不清 redo
  const tx2 = area.begin()
  cur.value = { list: ['a', 'b', 'x'] }
  tx2.cancel()
  assert.equal(area.counts.value[0], 1, 'cancel 不入栈')
  // mutate 异常等价 cancel
  const tx3 = area.begin()
  cur.value = { list: ['a', 'b', 'y'] }
  tx3.cancel()
  assert.equal(area.counts.value[0], 1)
})

await check('③ 空操作不记录（before/after 相同丢弃）', async () => {
  const cur = ref({ v: 1 })
  const area = createUndoArea(cur)
  const tx = area.begin()
  tx.commit('无变化', cur.value)
  assert.equal(area.counts.value[0], 0)
})

await check('④ mergeKey：同 key 连续编辑合并为一步（before 保留首次）', async () => {
  const cur = ref({ name: 'A' })
  const area = createUndoArea(cur)
  const tx1 = area.begin({ mergeKey: 'node:n1:name' })
  cur.value = { name: 'AB' }
  tx1.commit('修改名称', cur.value, { mergeKey: 'node:n1:name' })
  const tx2 = area.begin({ mergeKey: 'node:n1:name' })
  cur.value = { name: 'ABC' }
  tx2.commit('修改名称', cur.value, { mergeKey: 'node:n1:name' })
  assert.equal(area.counts.value[0], 1, '同字段连续编辑合并为一步：' + area.counts.value[0])
  const restored = area.undo()
  assert.deepEqual(restored, { name: 'A' }, '撤销回到首次编辑前')
  area.redo()
  // 不同 key 不合并
  const tx3 = area.begin({ mergeKey: 'node:n1:code' })
  cur.value = { name: 'ABC', code: 'x' }
  tx3.commit('修改代码', cur.value, { mergeKey: 'node:n1:code' })
  assert.equal(area.counts.value[0], 2, '不同字段分开')
})

await check('⑤ 上限 60 步：超出丢弃最旧', async () => {
  const cur = ref({ n: 0 })
  const area = createUndoArea(cur)
  for (let i = 1; i <= 65; i++) { cur.value = { n: i }; area.push('step ' + i) }
  assert.equal(area.counts.value[0], 60)
})

await check('⑥ reset 清空两栈（离开编辑页/切换资产）', async () => {
  const cur = ref({ v: 1 })
  const area = createUndoArea(cur)
  cur.value = { v: 2 }
  area.push('x')
  area.reset()
  assert.deepEqual(area.counts.value, [0, 0])
  assert.equal(area.undo(), null)
})

await check('⑦ 兼容：不带名的旧 push 仍可用（无名步骤兜底）', async () => {
  const cur = ref({ v: 1 })
  const area = createUndoArea(cur)
  // 生产顺序：emit('before-change')（push，快照当前）→ mutate
  area.push()
  cur.value = { v: 2 }
  assert.equal(area.counts.value[0], 1)
  assert.deepEqual(area.undo(), { v: 1 })
})

const failed = results.filter(r => !r.ok)
console.log(`\n${results.length - failed.length}/${results.length} 项通过`)
if (failed.length) process.exit(1)
