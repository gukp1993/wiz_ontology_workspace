// 保存状态机确定性测试（T1/F3）。运行（仓库根）：
//   node --import ./tests/ts_hooks.mjs tests/save_queue.test.mjs
// 用受控 Promise 驱动 createSaver，不依赖真实 HTTP；除 conflict 场景的 debounce 等待外不依赖时间。
// 场景与拆分计划 P2/F3 对应：retry 真发、后继 revision、409 不自动覆盖、失败后重试最新内容、
// beforeunload 拦截、error 后显式保存、串行在途、reload 丢弃过期响应。
globalThis.window = { addEventListener(t, h) { if (t === 'beforeunload') globalThis.__unloadHandler = h } }
const mod = await import('../frontend/src/app/saveCoordinator.ts')
const { createSaver, SaveRequestError } = mod

const deferred = () => { let resolve, reject; const p = new Promise((res, rej) => { resolve = res; reject = rej }); return { p, resolve, reject } }
const sleep = (ms) => new Promise(r => setTimeout(r, ms))
const results = []
function assert(name, cond, detail = '') { results.push({ name, ok: !!cond }); console.log(`${cond ? '通过' : '失败'}：${name}${cond ? '' : ' — ' + (detail || '断言不成立')}`) }

// 受控提交端：calls 记录每次 (snapshot, revision)。
// plan 队列每项：函数 d=>d.resolve({revision}) / d=>d.reject(err) 立即应答；'manual' 挂起等待 answer()。
function harness() {
  const calls = []
  const pendings = []
  const plan = []
  const submit = async (snapshot, revision) => {
    calls.push({ snapshot: JSON.parse(JSON.stringify(snapshot)), revision })
    const d = deferred()
    pendings.push(d)
    const step = plan.shift()
    if (typeof step === 'function') step(d)
    return d.p
  }
  const answer = (i, value) => { const d = pendings[i]; if (!d) throw new Error('no pending #' + i); d.resolve(value) }
  const rejectWith = (i, err) => { const d = pendings[i]; if (!d) throw new Error('no pending #' + i); d.reject(err) }
  const ok = (rev) => (d) => d.resolve({ revision: rev })
  const fail = (err) => (d) => d.reject(err)
  const load = async () => ({ state: { v: 0 }, revision: 'r0' })
  return { calls, pendings, plan, submit, answer, rejectWith, ok, fail, load }
}

// ① retry 在 error 状态必须真正再次发起请求（当前缺陷：drain 在 error 直接 break）
{
  const h = harness()
  const s = createSaver('t1', h.load, h.submit)
  await s.reload()
  h.plan.push(h.fail(new SaveRequestError('网络异常', 0)))
  s.working.value = { v: 1 }
  await s.commitNow().catch(() => {})
  assert('①a 失败后进入 error 状态', s.status.value === 'error', `status=${s.status.value}`)
  h.plan.push(h.ok('r2'))
  await s.retry()
  assert('①b retry 后重发了请求', h.calls.length === 2, `实际 ${h.calls.length} 次`)
}

// ② 在途保存期间继续编辑：后继提交必须用本客户端新确认的 revision（当前缺陷：沿用入队时旧值 → 假 409）
{
  const h = harness()
  const s = createSaver('t2', h.load, h.submit)
  await s.reload()
  h.plan.push('manual')
  const first = s.commitNow()          // submit1 挂起
  await sleep(0)
  s.working.value = { v: 2 }
  s.touch()                            // 入队：携带当时的 revision（r0）
  await sleep(0)
  h.answer(0, { revision: 'r1' })      // submit1 成功：revision 推进到 r1
  await sleep(20)                      // drain 接力 submit2
  h.answer(1, { revision: 'r2' })
  await first
  await sleep(20)
  assert('②a 后继请求使用了新确认 revision r1', h.calls[1] && h.calls[1].revision === 'r1', `实际 revision=${h.calls[1] && h.calls[1].revision}`)
  assert('②b 后继请求提交的是最新内容', h.calls[1] && h.calls[1].snapshot.v === 2, `实际 snapshot=${JSON.stringify(h.calls[1] && h.calls[1].snapshot)}`)
}

// ③ beforeunload 在 error 状态也必须拦截（当前缺陷：只拦 dirty/saving/conflict）
{
  const h = harness()
  const s = createSaver('t3', h.load, h.submit)
  await s.reload()
  h.plan.push(h.fail(new SaveRequestError('网络异常', 0)))
  s.working.value = { v: 1 }
  await s.commitNow().catch(() => {})
  const ev = { prevented: false, preventDefault() { this.prevented = true }, returnValue: undefined }
  globalThis.__unloadHandler(ev)
  assert('③ error 状态下 beforeunload 被拦截', ev.prevented === true || ev.returnValue === '')
}

// ④ 409 冲突：不自动覆盖；touch 不触发提交；retryFromLocal 换服务端 revision 重提当前内容
{
  const h = harness()
  const s = createSaver('t4', h.load, h.submit)
  await s.reload()
  h.plan.push(h.fail(new SaveRequestError('此草稿已有新版本', 409, 'r9')))
  s.working.value = { v: 1 }
  await s.commitNow().catch(() => {})
  assert('④a 409 进入 conflict 状态', s.status.value === 'conflict', `status=${s.status.value}`)
  s.working.value = { v: 2 }
  s.touch()
  await sleep(1100)                    // 超过 debounce：冲突期间不得自动提交
  assert('④b conflict 期间没有自动提交', h.calls.length === 1, `实际 ${h.calls.length} 次`)
  h.plan.push(h.ok('r9'))
  await s.retryFromLocal()             // 用户显式选择以当前内容覆盖
  assert('④c retryFromLocal 用服务端 revision 重提当前内容', h.calls[1] && h.calls[1].revision === 'r9' && h.calls[1].snapshot.v === 2, `实际 ${JSON.stringify(h.calls[1])}`)
  assert('④d 覆盖重提后回到 saved', s.status.value === 'saved', `status=${s.status.value}`)
}

// ⑤ 失败后又编辑：retry 必须提交最新确认的内容，不能静默换成旧失败快照（当前缺陷）
{
  const h = harness()
  const s = createSaver('t5', h.load, h.submit)
  await s.reload()
  h.plan.push(h.fail(new SaveRequestError('服务器错误', 500)))
  s.working.value = { v: 1 }
  await s.commitNow().catch(() => {})
  s.working.value = { v: 5 }           // 失败后继续编辑
  h.plan.push(h.ok('r3'))
  await s.retry()
  assert('⑤ retry 提交的是失败后编辑的最新内容', h.calls[1] && h.calls[1].snapshot.v === 5, `实际 snapshot=${JSON.stringify(h.calls[1] && h.calls[1].snapshot)}`)
  assert('⑤ retry 后保存成功', s.status.value === 'saved', `status=${s.status.value}`)
}

// ⑥ error 状态下显式保存（commitNow）必须真正发起请求（当前缺陷：drain break 吞掉显式保存）
{
  const h = harness()
  const s = createSaver('t6', h.load, h.submit)
  await s.reload()
  h.plan.push(h.fail(new SaveRequestError('网络异常', 0)))
  s.working.value = { v: 1 }
  await s.commitNow().catch(() => {})
  s.working.value = { v: 3 }
  h.plan.push(h.ok('r4'))
  await s.commitNow()
  assert('⑥ error 后显式保存重新发起请求', h.calls.length === 2 && h.calls[1].snapshot.v === 3, `实际 ${h.calls.length} 次 ${JSON.stringify(h.calls[1])}`)
}

// ⑦ 串行：保存中再次 touch 不会并发第二个请求；全部完成后回到 saved
{
  const h = harness()
  const s = createSaver('t7', h.load, h.submit)
  await s.reload()
  h.plan.push('manual')
  const first = s.commitNow()
  await sleep(0)
  s.working.value = { v: 9 }
  s.touch()
  await sleep(5)
  assert('⑦a 在途期间没有第二个请求', h.calls.length === 1 && s.status.value === 'saving', `calls=${h.calls.length} status=${s.status.value}`)
  h.answer(0, { revision: 'r5' })
  await sleep(20)
  h.answer(1, { revision: 'r6' })
  await first
  await sleep(5)
  assert('⑦b 全部落盘后 saved 且 revision 为最新', s.status.value === 'saved' && s.revision.value === 'r6', `status=${s.status.value} rev=${s.revision.value}`)
}

// ⑧ reload 丢弃在途旧响应：旧请求迟到返回不得覆盖新载入内容
{
  const h = harness()
  const s = createSaver('t8', (...a) => h.load(...a), h.submit) // load 经包装引用，后续替换生效
  await s.reload()
  h.plan.push('manual')
  const stale = s.commitNow()
  await sleep(0)
  h.load = async () => ({ state: { v: 100 }, revision: 'fresh' })
  const rl = s.reload()
  await sleep(0)
  h.answer(0, { revision: 'stale-r' }) // 旧请求此刻才成功
  await Promise.allSettled([stale, rl])
  assert('⑧ 过期响应未覆盖新载入状态', s.working.value.v === 100 && s.revision.value === 'fresh', `working=${JSON.stringify(s.working.value)} rev=${s.revision.value}`)
}

const failed = results.filter(r => !r.ok)
console.log(`\n统计：${results.length - failed.length}/${results.length} 项通过`)
if (failed.length) { console.log('未通过：' + failed.map(f => f.name).join('；')); process.exit(1) }
