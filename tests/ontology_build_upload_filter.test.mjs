// 上传后缀过滤纯函数回归（08 §12.1 第三条）。
// 运行：node --import ./tests/ts_hooks.mjs tests/ontology_build_upload_filter.test.mjs
import assert from 'node:assert'
import { parseExtensionFilter, filterByExtensions, LARGE_FOLDER_THRESHOLD, shouldGuideLargeFolder } from '../frontend/src/ontology/build/uploadFilter.ts'

const results = []
function check(name, fn) {
  try { fn(); results.push(['通过', name]) } catch (exc) { results.push(['失败', name + ' :: ' + exc.message]) }
}

check('混合分隔与大小写归一', () => {
  assert.deepEqual(parseExtensionFilter('.java, .SQL；md；  .MD .Txt'), ['.java', '.sql', '.md', '.txt'])
})
check('不带点自动补点', () => {
  assert.deepEqual(parseExtensionFilter('java py'), ['.java', '.py'])
})
check('空输入返回空数组（不过滤）', () => {
  assert.deepEqual(parseExtensionFilter('   '), [])
})
check('孤点与纯点号被丢弃', () => {
  assert.deepEqual(parseExtensionFilter('. .. ...java'), ['.java'])
})
check('空过滤器全部保留', () => {
  const files = [{ name: 'a.bin' }, { name: 'b.java' }]
  const { keep, skipped } = filterByExtensions(files, [])
  assert.equal(keep.length, 2); assert.equal(skipped, 0)
})
check('白名单命中保留、其余跳过', () => {
  const files = [{ name: 'A.JAVA' }, { name: 'b.class' }, { name: 'c.sql' }]
  const { keep, skipped } = filterByExtensions(files, ['.java', '.sql'])
  assert.equal(keep.length, 2); assert.equal(skipped, 1)
  assert.deepEqual(keep.map(f => f.name), ['A.JAVA', 'c.sql'])
})
check('目录选择时 webkitRelativePath 参与匹配', () => {
  const files = [
    { name: 'index.js', webkitRelativePath: 'proj/src/index.js' },
    { name: 'README', webkitRelativePath: 'proj/README' },
  ]
  const { keep, skipped } = filterByExtensions(files, ['.js'])
  assert.equal(keep.length, 1); assert.equal(skipped, 1)
})
check('无扩展名文件在白名单模式下被跳过', () => {
  const { keep, skipped } = filterByExtensions([{ name: 'LICENSE' }], ['.md'])
  assert.equal(keep.length, 0); assert.equal(skipped, 1)
})

let failed = 0
for (const [status, name] of results) {
  console.log(status + ' ' + name)
  if (status === '失败') failed += 1
}
console.log(`汇总：通过 ${results.length - failed} / ${results.length}`)
if (failed) process.exit(1)

// ── V2-6（G21）大文件夹软引导：阈值常量与触发判断 ──────────────────────────
check('阈值为 20（需求 §5.3 默认值）', () => {
  assert.equal(LARGE_FOLDER_THRESHOLD, 20)
})
check('严格大于阈值才引导（等于不引导，不硬阻断）', () => {
  assert.equal(shouldGuideLargeFolder(20), false)
  assert.equal(shouldGuideLargeFolder(21), true)
  assert.equal(shouldGuideLargeFolder(0), false)
})
