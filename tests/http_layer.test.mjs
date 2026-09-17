// G1（20260917 全局交互评审采纳）：HTTP 层有界读取与失败分类回归。
// 运行（仓库根）：node --import ./tests/ts_hooks.mjs tests/http_layer.test.mjs
// 用受控 fetch 与假 AbortSignal 驱动，不发起真实网络请求。
import assert from 'node:assert/strict'
const { requestJson, getJson, postJson, SaveRequestError, READ_TIMEOUT_MS, isOutcomeUnknown } = await import('../frontend/src/app/http.ts')

const results = []
async function check(name, fn) {
  try { await fn(); results.push({ name, ok: true }); console.log('通过：' + name) }
  catch (e) { results.push({ name, ok: false }); console.log('失败：' + name + ' — ' + e.message) }
}
const jsonResponse = (data, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => data })
const sleep = ms => new Promise(r => setTimeout(r, ms))
/** 受控 fetch：按 signal 取消，可指定延迟与响应。 */
function stubFetch(handler) { globalThis.fetch = handler }

// ① 正常成功：返回解析后的 JSON，且不产生任何失败分类
await check('① 正常成功返回解析后的 JSON', async () => {
  stubFetch(async () => jsonResponse({ ok: 1 }))
  assert.deepEqual(await getJson('/api/x'), { ok: 1 })
})

// ② 非 JSON 错误响应：不能当成有效数据，保留状态码与原始 data=null
await check('② 非 JSON 错误响应抛出带状态码的错误，不静默当成功', async () => {
  stubFetch(async () => ({ ok: false, status: 500, json: async () => { throw new Error('not json') } }))
  await assert.rejects(() => getJson('/api/x'), err => {
    assert.ok(err instanceof SaveRequestError)
    assert.equal(err.status, 500)
    assert.equal(err.failure, 'http')
    assert.match(err.message, /请求失败（500）/)
    assert.equal(err.data, null)
    return true
  })
})

// ③ 网络异常：failure=network、status=0，区别于服务端错误
await check('③ 网络异常分类为 network 且 status=0', async () => {
  stubFetch(async () => { throw new TypeError('failed to fetch') })
  await assert.rejects(() => getJson('/api/x'), err => {
    assert.equal(err.failure, 'network'); assert.equal(err.status, 0)
    assert.equal(isOutcomeUnknown(err), true)
    return true
  })
})

// ④ 读取超时：从发起算到响应体解析完成；失败分类 timeout，并真的中断在途请求
await check('④ 读取超时按 timeout 分类并中断请求', async () => {
  let aborted = false
  stubFetch((_url, init) => new Promise((_resolve, reject) => {
    init.signal.addEventListener('abort', () => { aborted = true; reject(Object.assign(new Error('aborted'), { name: 'AbortError' })) })
  }))
  const started = Date.now()
  await assert.rejects(() => getJson('/api/slow', { timeoutMs: 60 }), err => {
    assert.equal(err.failure, 'timeout'); assert.equal(err.status, 0); assert.match(err.message, /读取超时/)
    return true
  })
  assert.equal(aborted, true, '超时必须中断在途请求')
  assert.ok(Date.now() - started < 2000)
})

// ⑤ 响应体解析阶段也要受超时约束（服务端先给头、后拖 body）
await check('⑤ 响应体迟迟不返回也算读取超时', async () => {
  stubFetch(async (_url, init) => ({ ok: true, status: 200, json: () => new Promise((_res, rej) => init.signal.addEventListener('abort', () => rej(Object.assign(new Error('aborted'), { name: 'AbortError' })))) }))
  await assert.rejects(() => getJson('/api/body', { timeoutMs: 50 }), err => { assert.equal(err.failure, 'timeout'); return true })
})

// ⑥ 调用方主动取消：分类 aborted，不是失败提示（App 据此不弹错）
await check('⑥ 调用方取消分类为 aborted，与超时区分', async () => {
  const controller = new AbortController()
  stubFetch((_url, init) => new Promise((_resolve, reject) => {
    if (init.signal.aborted) return reject(Object.assign(new Error('aborted'), { name: 'AbortError' }))
    init.signal.addEventListener('abort', () => reject(Object.assign(new Error('aborted'), { name: 'AbortError' })))
  }))
  const pending = getJson('/api/x', { signal: controller.signal, timeoutMs: 5000 })
  controller.abort()
  await assert.rejects(() => pending, err => { assert.equal(err.failure, 'aborted'); assert.match(err.message, /请求已取消/); return true })
})

// ⑦ 已取消的信号立即取消，不发起长等待
await check('⑦ 传入已取消的信号立即失败', async () => {
  const controller = new AbortController(); controller.abort()
  stubFetch((_url, init) => new Promise((_resolve, reject) => { if (init.signal.aborted) reject(Object.assign(new Error('aborted'), { name: 'AbortError' })) }))
  await assert.rejects(() => getJson('/api/x', { signal: controller.signal, timeoutMs: 5000 }), err => { assert.equal(err.failure, 'aborted'); return true })
})

// ⑧ 409：保留 currentRevision 与完整响应体（调用方冲突处置依赖它们）
await check('⑧ 409 保留 currentRevision 与 data', async () => {
  stubFetch(async () => jsonResponse({ error: '草稿已有新版本', currentRevision: 'rev-2', extra: 'x' }, 409))
  await assert.rejects(() => getJson('/api/x'), err => {
    assert.equal(err.status, 409)
    assert.equal(err.currentRevision, 'rev-2')
    assert.deepEqual(err.data, { error: '草稿已有新版本', currentRevision: 'rev-2', extra: 'x' })
    assert.equal(isOutcomeUnknown(err), false, '409 是明确结果，不算结果未知')
    return true
  })
})

// ⑨ 写操作不套用读取超时：慢 POST 继续等待结果
await check('⑨ postJson 不套用读取超时（慢写继续等待）', async () => {
  globalThis.__WIZ_READ_TIMEOUT_MS = 10
  stubFetch(async () => { await sleep(60); return jsonResponse({ saved: true }) })
  assert.deepEqual(await postJson('/api/save', {}), { saved: true })
  delete globalThis.__WIZ_READ_TIMEOUT_MS
})

// ⑩ 读取超时可集中注入更短时间（测试用），默认值仍是 15 秒
await check('⑩ 读取超时可注入且默认 15 秒', async () => {
  assert.equal(READ_TIMEOUT_MS, 15000)
  globalThis.__WIZ_READ_TIMEOUT_MS = 40
  stubFetch((_url, init) => new Promise((_resolve, reject) => init.signal.addEventListener('abort', () => reject(Object.assign(new Error('aborted'), { name: 'AbortError' })))))
  await assert.rejects(() => getJson('/api/slow'), err => { assert.equal(err.failure, 'timeout'); return true })
  delete globalThis.__WIZ_READ_TIMEOUT_MS
})

// ⑪ 每次请求都清理监听器：外部 signal 上的 abort 监听不会累积
await check('⑪ 请求结束清理外部取消监听（不泄漏）', async () => {
  const listeners = new Set()
  const signal = {
    aborted: false,
    addEventListener: (_t, fn) => listeners.add(fn),
    removeEventListener: (_t, fn) => listeners.delete(fn),
  }
  stubFetch(async () => jsonResponse({ ok: 1 }))
  for (let i = 0; i < 3; i++) await getJson('/api/x', { signal, timeoutMs: 1000 })
  assert.equal(listeners.size, 0, '外部 signal 上的监听必须全部移除')
  // 超时路径同样清理
  stubFetch((_url, init) => new Promise((_resolve, reject) => init.signal.addEventListener('abort', () => reject(Object.assign(new Error('aborted'), { name: 'AbortError' })))))
  await assert.rejects(() => getJson('/api/slow', { signal, timeoutMs: 30 }))
  assert.equal(listeners.size, 0, '超时后也必须移除外部监听')
})

// ⑫ 非 2xx 但 body 是 errors[] 时给出可读原因（沿用原行为）
await check('⑫ 非 2xx 的 errors[] 作为失败原因', async () => {
  stubFetch(async () => jsonResponse({ errors: ['缺少身份来源', '表不存在'] }, 400))
  await assert.rejects(() => getJson('/api/x'), err => { assert.match(err.message, /缺少身份来源/); return true })
})

const failed = results.filter(r => !r.ok)
console.log(`\n${results.length - failed.length}/${results.length} 项通过`)
if (failed.length) process.exit(1)
