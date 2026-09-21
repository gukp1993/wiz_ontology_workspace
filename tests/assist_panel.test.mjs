// 辅助填写面板状态机测试（T3，2026-09-21）。运行（仓库根）：
//   node --import ./tests/ts_hooks.mjs tests/assist_panel.test.mjs
// 用受控 Promise 驱动 useAssistPanel（注入假 api 桩与假宿主 binding），不依赖真实 HTTP 与时间。
// 覆盖：open/context、no-model、迟到响应代际丢弃、CONTEXT_STALE 恢复、采纳合并与等值剔除、
// 撤销与撤销保护、取消、intent/answers 结构化传输、采纳绝不触发表单保存。
const { useAssistPanel } = await import('../frontend/src/assist/useAssistPanel.ts')
const { SaveRequestError } = await import('../frontend/src/app/http.ts')

const deferred = () => { let resolve, reject; const p = new Promise((res, rej) => { resolve = res; reject = rej }); return { p, resolve, reject } }
const sleep = (ms) => new Promise(r => setTimeout(r, ms))
const results = []
function assert(name, cond, detail = '') { results.push({ name, ok: !!cond }); console.log(`${cond ? '通过' : '失败'}：${name}${cond ? '' : ' — ' + (detail || '断言不成立')}`) }

// ── 假宿主 binding：apply/snapshot/restore 真实可观察；commitNow/touch 一旦被调用即测试失败 ──
function makeHost(initial = { comment: '旧定义', name: 'soc' }) {
  let draft = JSON.parse(JSON.stringify(initial))
  const calls = { apply: [], snapshot: 0, restore: [], order: [] }
  const host = {
    space: 'ontology',
    targetKind: 'property',
    targetId: 'mg:prop_1',
    contextTitle: '属性「SOC采样值」',
    draft: () => JSON.parse(JSON.stringify(draft)),
    apply: (values) => { calls.apply.push(JSON.parse(JSON.stringify(values))); calls.order.push('apply'); Object.assign(draft, values) },
    snapshot: () => { calls.snapshot++; calls.order.push('snapshot'); return JSON.parse(JSON.stringify(draft)) },
    restore: (snap) => { calls.restore.push(JSON.parse(JSON.stringify(snap))); calls.order.push('restore'); draft = JSON.parse(JSON.stringify(snap)) },
    // 保存通道间谍：面板契约规定绝不触发表单保存，触发即测试失败
    commitNow: () => { throw new Error('面板不得调用 commitNow') },
    touch: () => { throw new Error('面板不得调用 touch') },
    submitForm: () => { throw new Error('面板不得调用 submitForm') },
  }
  return { host, calls, draftNow: () => draft }
}

// ── 假 api：受控应答（plan 每项为函数 d=>d.resolve/d.reject；未编排的请求立即拒绝，防止误通过）──
function makeApi() {
  const calls = { context: [], generate: [] }
  const pendings = []
  const plan = { context: [], generate: [] }
  const wire = (kind) => async (body) => {
    calls[kind].push(JSON.parse(JSON.stringify(body)))
    const d = deferred()
    pendings.push({ kind, d })
    const step = plan[kind].shift()
    if (step === 'manual') { /* 挂起：等待测试直接 resolve/reject（迟到/取消场景） */ }
    else if (typeof step === 'function') step(d)
    else d.reject(new SaveRequestError('测试桩未编排该请求', 500, null, { error: '未编排', code: 'UNPLANNED' }))
    return d.p
  }
  return {
    api: { context: wire('context'), generate: wire('generate') },
    calls, pendings, plan,
    okContext: (over = {}) => (d) => d.resolve({
      contextToken: over.contextToken ?? 'tok-1',
      contextFingerprint: over.contextFingerprint ?? 'fp-1',
      context: Object.assign({
        targetKind: 'property', title: '属性「SOC采样值」的辅助填写',
        editableFields: [{ key: 'comment', label: '业务定义', kind: 'textarea', required: true, options: null, group: null, help: '' }],
        definitions: [], catalog: [], flows: [], modelReady: true,
      }, over.context || {}),
    }),
    okGenerate: (over = {}) => (d) => d.resolve(Object.assign({
      requestId: 'srv-1', status: 'ok', contextFingerprint: 'fp-1',
      questions: [], suggestions: [], issues: [], explanation: null,
      meta: { durationMs: 12, provider: '测试提供方', model: 'stub' },
    }, over.body || {})),
    fail: (err) => (d) => d.reject(err),
  }
}

const staleErr = () => new SaveRequestError('上下文已变化', 409, null, { error: '上下文已变化', code: 'CONTEXT_STALE' })
const sug = (id, label, proposed, state = 'ready', extra = {}) => ({ id, label, fieldKeys: Object.keys(proposed), proposed, state, ...extra })

/** open（context 已编排成功）后的面板，供只需 happy-path 开场的场景使用。 */
async function openedPanel({ host, contextOver } = {}) {
  const h = host || makeHost().host
  const a = makeApi()
  a.plan.context.push(a.okContext(contextOver))
  const panel = useAssistPanel(a.api)
  await panel.open(h)
  return { panel, a, host: h }
}

// ① open → context 成功 → ready；modelReady=false → no-model（保留输入）
{
  const { panel } = await openedPanel()
  assert('①a open 后进入 ready', panel.status.value === 'ready', `status=${panel.status.value}`)
  assert('①b 记录了 contextToken 与标题', panel.contextToken.value === 'tok-1' && panel.contextInfo.value.title.includes('SOC'), `token=${panel.contextToken.value}`)
  assert('①c 无错误无结果', panel.error.value === null && panel.result.value === null)

  const { panel: p2 } = await openedPanel({ contextOver: { context: { modelReady: false } } })
  assert('①d modelReady=false → no-model', p2.status.value === 'no-model', `status=${p2.status.value}`)
  p2.intent.value = '想让它写清楚口径'
  assert('①e no-model 保留输入', p2.intent.value.includes('口径'))
}

// ② generate 迟到响应：close 后才 resolve → 按代际丢弃，不写状态；reopen 复原展示
{
  const { host } = makeHost()
  const a = makeApi()
  a.plan.context.push(a.okContext())
  const panel = useAssistPanel(a.api)
  await panel.open(host)
  a.plan.generate.push('manual')
  const gen = panel.generate()
  await sleep(0)
  assert('②a 生成中状态', panel.status.value === 'generating', `status=${panel.status.value}`)
  panel.close()
  assert('②b close 后收起且回到前态', panel.closed.value === true && panel.status.value === 'ready', `closed=${panel.closed.value} status=${panel.status.value}`)
  a.pendings.filter(x => x.kind === 'generate').at(-1).d.resolve({
    requestId: 'late', status: 'ok', contextFingerprint: 'fp-1', questions: [], issues: [], explanation: null,
    suggestions: [sug('s_late', '迟到建议', { comment: '迟到值' })], meta: { durationMs: 1, provider: 'x', model: 'x' },
  })
  await gen
  await sleep(0)
  assert('②c 迟到响应未写入结果', panel.result.value === null, `result=${JSON.stringify(panel.result.value)}`)
  assert('②d 迟到响应未写 checked', Object.keys(panel.checked.value).length === 0)
  panel.reopen()
  assert('②e reopen 恢复展示（closed=false 且上下文保留）', panel.closed.value === false && panel.hasContext.value === true)
}

// ③ CONTEXT_STALE 409 → error 态；refreshContext 重取后恢复且保留 intent
{
  const { panel, a } = await openedPanel()
  panel.intent.value = '关注口径'
  a.plan.generate.push(a.fail(staleErr()))
  await panel.generate()
  assert('③a 409 CONTEXT_STALE 进入 error', panel.status.value === 'error' && panel.error.value.code === 'CONTEXT_STALE', `status=${panel.status.value} code=${panel.error.value && panel.error.value.code}`)
  assert('③b 错误信息提示重新获取上下文', (panel.error.value.message || '').includes('重新获取'))
  assert('③c intent 保留', panel.intent.value === '关注口径')
  a.plan.context.push(a.okContext({ contextToken: 'tok-2' }))
  await panel.refreshContext()
  assert('③d refreshContext 恢复 ready 并换新 token', panel.status.value === 'ready' && panel.contextToken.value === 'tok-2', `status=${panel.status.value} token=${panel.contextToken.value}`)
  assert('③e refreshContext 清除错误', panel.error.value === null)
}

// ④ 采纳：只合并勾选且 ready 的建议；pending/blocked 拒绝勾选；同字段后者覆盖；先 snapshot 后 apply
{
  const { host, calls } = makeHost({ comment: '', name: 'soc' }) // 旧值为空 → ready 默认勾选
  const a = makeApi()
  a.plan.context.push(a.okContext())
  const panel = useAssistPanel(a.api)
  await panel.open(host)
  a.plan.generate.push(a.okGenerate({ body: { suggestions: [
    sug('s1', '业务定义A', { comment: '新定义A', unit: 'kW' }),
    sug('s2', '业务定义B', { comment: '新定义B' }),
    sug('s3', '待补充建议', { comment: '待定C' }, 'pending', { pendingReason: '等待问题 q1 回答' }),
    sug('s4', '被阻断建议', { name: '非法X' }, 'blocked', { blockedReason: '引用对象不存在' }),
  ] } }))
  await panel.generate()
  assert('④a 生成完成进入 done', panel.status.value === 'done', `status=${panel.status.value}`)
  assert('④b ready 默认全勾选，pending/blocked 不勾', panel.checked.value.s1 === true && panel.checked.value.s2 === true && panel.checked.value.s3 === false && panel.checked.value.s4 === false, JSON.stringify(panel.checked.value))
  panel.setChecked('s3', true)
  panel.setChecked('s4', true)
  assert('④c setChecked 拒绝 pending/blocked', panel.checked.value.s3 === false && panel.checked.value.s4 === false)
  panel.setChecked('s2', false)
  assert('④d 可取消勾选 ready', panel.checked.value.s2 === false && panel.selectedCount.value === 1)
  panel.setChecked('s2', true)

  const ok = panel.adopt()
  assert('④e adopt 成功', ok === true)
  const d4 = host.draft()
  assert('④f apply 收到勾选 ready 的合并值（同字段后者覆盖）', d4.comment === '新定义B' && d4.unit === 'kW' && d4.name === 'soc', `draft=${JSON.stringify(d4)}`)
  assert('④g 先快照后落草稿（各一次）', JSON.stringify(calls.order) === '["snapshot","apply"]' && calls.snapshot === 1 && calls.apply.length === 1, JSON.stringify(calls.order))
  assert('④h 记录可撤销与「已填入」提示', panel.canUndo.value === true && panel.justAdopted.value === true)
  assert('④i 采纳不改完成态、不置过期（提交仍由用户在表单上完成）', panel.status.value === 'done' && panel.stale.value === false)
}

// ⑤ 撤销与撤销保护：undo 恢复快照；采纳后手改 → canUndo=false 且 undo 拒绝；重新生成后按指纹恢复可用
{
  const { host, calls } = makeHost({ comment: '旧定义', name: 'soc' })
  const a = makeApi()
  a.plan.context.push(a.okContext())
  const panel = useAssistPanel(a.api)
  await panel.open(host)
  a.plan.generate.push(a.okGenerate({ body: { suggestions: [sug('s1', '业务定义', { comment: '新定义' })] } }))
  await panel.generate()
  assert('⑤0 替换真实旧值的建议默认不勾选（需求 §3.5）', panel.checked.value.s1 === false, JSON.stringify(panel.checked.value))
  panel.setChecked('s1', true) // 用户显式勾选才替换
  panel.adopt()
  assert('⑤a 采纳后宿主草稿已更新', host.draft().comment === '新定义')
  const okUndo = panel.undo()
  assert('⑤b undo 恢复到采纳前快照', okUndo === true && host.draft().comment === '旧定义' && calls.restore.length === 1)
  assert('⑤c undo 后不可再次撤销（一次性）', panel.canUndo.value === false && panel.undo() === false)

  // 再采纳一次，然后宿主手改 → 撤销保护失效 + 结果过期
  a.plan.generate.push(a.okGenerate({ body: { suggestions: [sug('s2', '业务定义2', { comment: '再定义' })] } }))
  await panel.generate()
  panel.setChecked('s2', true)
  panel.adopt()
  assert('⑤d 二次采纳成功', host.draft().comment === '再定义' && panel.canUndo.value === true)
  host.apply({ comment: '我手改的' })   // 模拟宿主表单手改（不经面板）
  panel.notifyDraftChanged()
  assert('⑤e 手改后 stale=true 且 canUndo=false', panel.stale.value === true && panel.canUndo.value === false)
  assert('⑤f 手改后 undo 拒绝且不再调用 restore', panel.undo() === false && calls.restore.length === 1)
  a.plan.generate.push(a.okGenerate({ body: { suggestions: [sug('s3', '过期后再来', { comment: '又定义' })] } }))
  await panel.generate() // 手改后草稿指纹相对「取上下文时」仍漂移：结果保持过期（真实后端此处应为 409 CONTEXT_STALE，由桩越过）
  assert('⑤g 手改后重新生成：结果保持过期直到重取上下文', panel.stale.value === true && panel.status.value === 'done', `stale=${panel.stale.value} status=${panel.status.value}`)
  assert('⑤h 过期期间采纳被拒绝', panel.adopt() === false && host.draft().comment === '我手改的')
  a.plan.context.push(a.okContext({ contextToken: 'tok-2' }))
  await panel.refreshContext() // 重取上下文：令牌绑定手改后的草稿
  a.plan.generate.push(a.okGenerate({ body: { suggestions: [sug('s3', '过期后再来', { comment: '又定义' })] } }))
  await panel.generate()
  panel.setChecked('s3', true)
  const ok5i = panel.adopt()
  assert('⑤i 重取上下文并重新生成后可再次采纳', panel.stale.value === false && ok5i === true && host.draft().comment === '又定义', JSON.stringify({ stale: panel.stale.value, ok: ok5i, checked: panel.checked.value, comment: host.draft().comment }))
}

// ⑥ 等值建议：宿主草稿已含相同值 → 等值剔除后 adopted 集为空 → adopt 无操作（不快照、不 apply、不产生撤销）
{
  const { host, calls } = makeHost({ comment: '完全一致', name: 'soc' })
  const a = makeApi()
  a.plan.context.push(a.okContext())
  const panel = useAssistPanel(a.api)
  await panel.open(host)
  a.plan.generate.push(a.okGenerate({ body: { suggestions: [sug('s1', '等值建议', { comment: '完全一致' })] } }))
  await panel.generate()
  const ok = panel.adopt()
  assert('⑥a 等值采纳为无操作', ok === false)
  assert('⑥b 未调用 snapshot/apply，未产生撤销与「已填入」提示', calls.snapshot === 0 && calls.apply.length === 0 && panel.canUndo.value === false && panel.justAdopted.value === false)
}

// ⑦ intent/answers 结构化传输：请求体按 questionId 分别携带（含 unsure），空回答不传
{
  const { panel, a, host } = await openedPanel()
  panel.intent.value = '  重点写 SOC 的采样口径  '
  a.plan.generate.push(a.okGenerate({ body: { questions: [
    { id: 'q1', prompt: '口径按哪个标准？', kind: 'text', options: [], allowUnsure: true },
    { id: 'q2', prompt: '适用车型？', kind: 'choice', options: [{ value: 'ev', label: '纯电动' }, { value: 'phev', label: '插混' }], allowUnsure: true },
    { id: 'q3', prompt: '留空的问题', kind: 'text', options: [], allowUnsure: false },
  ] } }))
  await panel.generate()
  panel.answerQuestion('q1', { value: 'GB/T 27930' })
  panel.answerQuestion('q2', { unsure: true })
  panel.answerQuestion('q3', { value: '' }) // 空回答：视为未回答
  a.plan.generate.push(a.okGenerate())
  await panel.generate()
  const bodies = a.calls.generate
  const first = bodies[0], second = bodies[1]
  assert('⑦a requestId 非空且 mode/draft/token 正确', typeof first.requestId === 'string' && first.requestId.length > 0 && first.mode === 'fill' && first.contextToken === 'tok-1' && first.draft.comment === '旧定义' && first.draft.name === 'soc')
  assert('⑦b intent 去首尾空白后携带', first.intent === '重点写 SOC 的采样口径', `intent=${first.intent}`)
  assert('⑦c answers 按 questionId 分别携带（含 unsure，空值剔除）', JSON.stringify(second.answers) === JSON.stringify({ q1: { value: 'GB/T 27930' }, q2: { unsure: true } }), `answers=${JSON.stringify(second.answers)}`)
  assert('⑦d 无回答时请求不带 answers 字段', first.answers === undefined)
  // 页签模式跟随：check 模式问题与建议分列
  panel.tab.value = 'check'
  a.plan.generate.push(a.okGenerate({ body: { issues: [{ fieldKey: 'comment', message: '口径不完整' }] } }))
  await panel.generate()
  assert('⑦e check 模式按当前页签传输', a.calls.generate[2].mode === 'check')
  assert('⑦f 结果 issues 可读', panel.issues.value.length === 1 && panel.issues.value[0].fieldKey === 'comment')
  assert('⑦g generate 每次重新取宿主 draft', host.draft && a.calls.generate.every(b => b.draft && typeof b.draft === 'object'))
}

// ⑧ cancel：作废在途请求并回前态；迟到响应不写入；提示不声称上游已停止
{
  const { host } = makeHost()
  const a = makeApi()
  a.plan.context.push(a.okContext())
  const panel = useAssistPanel(a.api)
  await panel.open(host)
  a.plan.generate.push('manual')
  const gen = panel.generate()
  await sleep(0)
  panel.cancel()
  assert('⑧a cancel 回到前态 ready 并有提示', panel.status.value === 'ready' && (panel.notice.value || '').includes('已取消'), `status=${panel.status.value} notice=${panel.notice.value}`)
  assert('⑧b 提示不声称上游已停止', !(panel.notice.value || '').includes('已停止'))
  a.pendings.filter(x => x.kind === 'generate').at(-1).d.resolve({
    requestId: 'late2', status: 'ok', contextFingerprint: 'fp-1', questions: [], issues: [], explanation: null,
    suggestions: [sug('s_late', '取消后迟到', { comment: 'x' })], meta: { durationMs: 1, provider: 'x', model: 'x' },
  })
  await gen
  await sleep(0)
  assert('⑧c 取消后迟到响应不写入', panel.result.value === null && panel.status.value === 'ready')
}

// ⑨ 网络失败/模型错误：输入保留可重试；MODEL_NOT_CONFIGURED → no-model
{
  const { host } = makeHost()
  const a = makeApi()
  a.plan.context.push(a.okContext())
  const panel = useAssistPanel(a.api)
  await panel.open(host)
  panel.intent.value = '保留我'
  a.plan.generate.push(a.fail(new SaveRequestError('网络异常，无法连接服务', 0)))
  await panel.generate()
  assert('⑨a 网络失败进 error 且输入保留', panel.status.value === 'error' && panel.intent.value === '保留我')
  a.plan.generate.push(a.fail(new SaveRequestError('当前账号未配置可用模型', 422, null, { error: '未配置模型', code: 'MODEL_NOT_CONFIGURED' })))
  await panel.generate()
  assert('⑨b MODEL_NOT_CONFIGURED → no-model（保留输入）', panel.status.value === 'no-model' && panel.intent.value === '保留我', `status=${panel.status.value}`)
  a.plan.generate.push(a.fail(new SaveRequestError('模型输出无法解析', 502, null, { error: '模型输出无效', code: 'MODEL_BAD_RESPONSE' })))
  await panel.generate()
  assert('⑨c 502 MODEL_BAD_RESPONSE → error 可重试', panel.status.value === 'error' && panel.error.value.code === 'MODEL_BAD_RESPONSE')
  // 错误后重试一次成功：恢复 done
  a.plan.generate.push(a.okGenerate({ body: { suggestions: [sug('s_ok', '重试建议', { comment: '重试后的值' })] } }))
  await panel.generate()
  assert('⑨d 错误后重试成功恢复 done', panel.status.value === 'done' && panel.error.value === null)
}

// ⑩ 指纹漂移：绕过 notifyDraftChanged 直接改宿主草稿 → adopt 前被指纹检查拦下
{
  const { host, calls } = makeHost()
  const a = makeApi()
  a.plan.context.push(a.okContext())
  const panel = useAssistPanel(a.api)
  await panel.open(host)
  a.plan.generate.push(a.okGenerate({ body: { suggestions: [sug('s1', '业务定义', { comment: '面板建议' })] } }))
  await panel.generate()
  host.apply({ comment: '绕过通知的手改' }) // 宿主忘记调 notifyDraftChanged
  const ok = panel.adopt()
  assert('⑩a 指纹漂移时采纳被拒绝（草稿保持手改值，面板未写入建议）', ok === false && host.draft().comment === '绕过通知的手改' && host.draft().name === 'soc')
  assert('⑩b syncStaleness 报告过期', panel.syncStaleness() === true && panel.stale.value === true)
}

const failed = results.filter(r => !r.ok)
console.log(`\n统计：${results.length - failed.length}/${results.length} 项通过`)
if (failed.length) { console.log('未通过：' + failed.map(f => f.name).join('；')); process.exit(1) }
