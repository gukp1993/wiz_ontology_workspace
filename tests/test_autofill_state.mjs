// 整表自动填写状态机测试（T3，2026-09-22 改版）。运行（仓库根）：
//   node --import ./tests/ts_hooks.mjs --test tests/test_autofill_state.mjs
// 用内存 mock API（受控 Promise，不发起网络）驱动 formAutofill/useAssistPanel 状态机，覆盖：
// 打开/关闭/同目标重开/切目标重置、生成中取消、迟到响应代际失效（关闭/手改/切目标）、
// 一次回填摘要计数、续轮握手调用序列（先 context 后 generate）、问题答复转续轮、
// 暂不确定转 unresolved、手改后禁撤销、无手改整轮撤销、empty、错误保留输入、
// protocol:2/sessionId 请求字段、applyOperations 与 codec、applyDraft 通道、契约指纹比对、
// check/explain 只读次要模式、回填绝不触达宿主保存通道。
const { useAssistPanel } = await import('../frontend/src/assist/useAssistPanel.ts')
const { applyOperations, topLevelChanges, AUTOFILL_EMPTY_TEXT } = await import('../frontend/src/assist/formAutofill.ts')
const { SaveRequestError } = await import('../frontend/src/app/http.ts')

const sleep = (ms = 0) => new Promise(r => setTimeout(r, ms))
const results = []
function assert(name, cond, detail = '') { results.push({ name, ok: !!cond }); console.log(`${cond ? '通过' : '失败'}：${name}${cond ? '' : ' — ' + (detail || '断言不成立')}`) }

// ── 假宿主 binding：apply/snapshot/restore 可观察；保存类通道一旦被调即测试失败 ──
function makeHost(initial = { label: '簇', comment: '旧定义' }, opts = {}) {
  let draft = JSON.parse(JSON.stringify(initial))
  const calls = { apply: [], applyDraft: [], snapshot: 0, restore: [], order: [] }
  const host = {
    space: opts.space || 'ontology',
    targetKind: opts.targetKind || 'property',
    targetId: opts.targetId || 'mg:prop_1',
    contextTitle: opts.contextTitle || '属性「SOC采样值」',
    draft: () => JSON.parse(JSON.stringify(draft)),
    apply: (values) => { calls.apply.push(JSON.parse(JSON.stringify(values))); calls.order.push('apply'); Object.assign(draft, values) },
    snapshot: () => { calls.snapshot++; calls.order.push('snapshot'); return JSON.parse(JSON.stringify(draft)) },
    restore: (snap) => { calls.restore.push(JSON.parse(JSON.stringify(snap))); calls.order.push('restore'); draft = JSON.parse(JSON.stringify(snap)) },
    // 保存通道间谍：回填契约规定绝不触达，触发即测试失败
    commitNow: () => { throw new Error('回填不得调用 commitNow') },
    touch: () => { throw new Error('回填不得调用 touch') },
    submitForm: () => { throw new Error('回填不得调用 submitForm') },
  }
  if (opts.codecs) host.codecs = opts.codecs
  if (opts.applyDraft) {
    host.applyDraft = (next) => {
      calls.applyDraft.push(JSON.parse(JSON.stringify(next)))
      calls.order.push('applyDraft')
      draft = JSON.parse(JSON.stringify(next)) // 宿主就地持有新草稿
    }
  }
  if (opts.contractInfo) host.contractInfo = opts.contractInfo
  return { host, calls, draftNow: () => JSON.parse(JSON.stringify(draft)), writeDraft: (patch) => { Object.assign(draft, patch) } }
}

// ── 假 api：受控应答（plan 每项为函数 d=>d.resolve/reject 或 'manual'；未编排立即拒绝）──
function makeApi() {
  const calls = { context: [], generate: [] }
  const order = []
  const pendings = []
  const plan = { context: [], generate: [] }
  const wire = (kind) => async (body) => {
    calls[kind].push(JSON.parse(JSON.stringify(body)))
    order.push(kind)
    const d = defer()
    pendings.push({ kind, d, body })
    const step = plan[kind].shift()
    if (step === 'manual') { /* 挂起：等待测试直接 resolve/reject（迟到/取消场景） */ }
    else if (typeof step === 'function') step(d)
    else d.reject(new SaveRequestError('测试桩未编排该请求', 500, null, { error: '未编排', code: 'UNPLANNED' }))
    return d.p
  }
  return {
    api: { context: wire('context'), generate: wire('generate') },
    calls, order, pendings, plan,
    okContext: (over = {}) => (d) => d.resolve({
      contextToken: (over && over.contextToken) || 'tok-1',
      contextFingerprint: 'cfp-1',
      context: Object.assign({
        targetKind: 'property', title: '属性「SOC采样值」的取值来源',
        editableFields: [
          { key: 'label', label: '属性名称', kind: 'text', required: true, options: null, group: null, help: '' },
          { key: 'comment', label: '业务定义', kind: 'textarea', required: true, options: null, group: null, help: '' },
        ],
        definitions: [], catalog: [], flows: [], modelReady: true,
      }, (over && over.context) || {}),
    }),
    fail: (err) => (d) => d.reject(err),
  }
}

function defer() { let resolve, reject; const p = new Promise((res, rej) => { resolve = res; reject = rej }); return { p, resolve, reject } }

const fillResp = (over = {}) => Object.assign({
  protocol: 'autofill/1', status: 'ok', requestId: 'srv-1', formId: 'property',
  schemaVersion: 1, schemaDigest: 'dig-1',
  target: { space: 'ontology', targetKind: 'property', targetId: 'mg:prop_1' },
  draftFingerprint: 'dfp-1', contextFingerprint: 'cfp-1',
  sessionId: 's_1', roundId: 'r_1',
  operations: [], questions: [], unresolved: [], summary: '',
  meta: { durationMs: 12, provider: '测试提供方', model: 'stub' },
}, over)
const setOp = (field, value) => ({ op: 'set', field, value, basis: { kind: 'intent', quote: 'x' } })
const genErr = (code, status) => new SaveRequestError(code || '请求失败', status || 500, null, { error: 'x', code })

/** open（context 已编排成功）后的面板，供只需 happy-path 开场的场景使用。 */
async function openedPanel({ host, hostOpts, contextOver } = {}) {
  const made = host ? null : makeHost(hostOpts?.initial, hostOpts)
  const h = host || made.host
  const a = makeApi()
  a.plan.context.push(a.okContext(contextOver || {}))
  const panel = useAssistPanel(a.api)
  await panel.open(h)
  return { panel, a, host: h, calls: made ? made.calls : null, draftNow: made ? made.draftNow : null }
}

// ① 打开/关闭/重新展开；同目标重开保留输入；切目标整卡重置
{
  const { panel, a } = await openedPanel()
  assert('①a open 后进入 open 态（抽屉展开）', panel.status.value === 'open' && panel.collapsed.value === false, `status=${panel.status.value}`)
  assert('①b 记录 contextToken 与目标标题', panel.contextToken.value === 'tok-1' && (panel.contextInfo.value.title || '').includes('SOC'))
  panel.close()
  assert('①c close 收起抽屉', panel.collapsed.value === true)
  panel.expand()
  assert('①d expand 重新展开', panel.collapsed.value === false)

  // 同目标重新 open：保留输入（引擎层保证；面板实例由宿主保持挂载时生效）
  panel.intent.value = '我的要求'
  a.plan.context.push(a.okContext({ contextToken: 'tok-1b' }))
  await panel.open(currentHostOf()) // 同 space/targetKind/targetId 的另一 binding 实例 = 同目标
  assert('①e 同目标重新 open 保留输入并重取上下文', panel.intent.value === '我的要求' && panel.contextToken.value === 'tok-1b')

  // 切目标：输入/会话/撤销整卡重置，不复用另一目标的任何状态
  panel.intent.value = '上一个目标的要求'
  const other = makeHost({ label: 'x', comment: 'y' }, { targetKind: 'link', targetId: 'mg:link_1' })
  a.plan.context.push(a.okContext({ contextToken: 'tok-9' }))
  await panel.open(other.host)
  assert('①f 切目标清空输入', panel.intent.value === '')
  assert('①g 切目标换 token 且抽屉展开', panel.contextToken.value === 'tok-9' && panel.collapsed.value === false)
}
/** 同目标重开：另建一个同 space/targetKind/targetId 的 binding 实例（引擎按目标标识判定同目标）。 */
function currentHostOf() { return makeHost().host }

// ② 一次回填：protocol:2、无 sessionId、apply 兜底通道、N 计数、状态条文案、done 收起、逐字段旧→新
{
  const { panel, a, host, calls } = await openedPanel({ hostOpts: { initial: { label: '', comment: '旧定义' } } })
  panel.intent.value = '新增采样SOC'
  a.plan.generate.push((d) => d.resolve(fillResp({
    operations: [setOp('label', '采样SOC'), setOp('comment', '电池剩余电量占额定容量的百分比')],
  })))
  await panel.generate()
  const body = a.calls.generate[0]
  assert('②a fill 请求携带 protocol:2 且首轮无 sessionId', body.protocol === 2 && body.sessionId === undefined, JSON.stringify(body))
  assert('②b fill 请求 mode/上下文令牌/intent 正确', body.mode === 'fill' && body.contextToken === 'tok-1' && body.intent === '新增采样SOC')
  assert('②c 一次回填直接生效（无勾选步骤）', host.draft().label === '采样SOC' && host.draft().comment === '电池剩余电量占额定容量的百分比')
  assert('②d 先快照后 apply（各一次）', JSON.stringify(calls.order) === '["snapshot","apply"]', JSON.stringify(calls.order))
  assert('②e N=实际改变的顶层字段数（2）且状态条文案', panel.roundSummary.value.appliedCount === 2 && panel.statusBarText.value === '已填写 2 项，尚未保存', panel.statusBarText.value)
  assert('②f 回填完成 done 并收起抽屉', panel.status.value === 'done' && panel.collapsed.value === true)
  assert('②g 逐字段旧值→新值', JSON.stringify(panel.roundSummary.value.changes) === JSON.stringify([
    { field: 'label', label: '属性名称', oldText: '（空）', newText: '采样SOC' },
    { field: 'comment', label: '业务定义', oldText: '旧定义', newText: '电池剩余电量占额定容量的百分比' },
  ]), JSON.stringify(panel.roundSummary.value.changes))
  assert('②h 记录会话/轮次与契约指纹', panel.sessionId.value === 's_1' && panel.roundId.value === 'r_1' && panel.lastSchema.value && panel.lastSchema.value.schemaDigest === 'dig-1')
}

// ③ empty：有效响应无变更 → 状态 empty、零写入、状态条为空
{
  const { panel, a, host } = await openedPanel()
  panel.intent.value = '帮我完善'
  a.plan.generate.push((d) => d.resolve(fillResp({ status: 'empty', operations: [], summary: '无变更' })))
  await panel.generate()
  assert('③a empty 态且文案常量正确', panel.status.value === 'empty' && AUTOFILL_EMPTY_TEXT === '内容已一致，无需修改')
  assert('③b empty 不写草稿不产生撤销', host.draft().comment === '旧定义' && panel.canUndo.value === false && panel.statusBarText.value === '')
}

// ④ 续轮握手：先填独立内容+问题留在抽屉 → 答复转续轮（先 context 后 generate，带 sessionId+answers）→ 完成收起 → 整轮撤销
{
  const { panel, a, host } = await openedPanel({ hostOpts: { initial: { label: '', comment: '', source: '' } } })
  panel.intent.value = '额定容量从 m_storage_cluster_phase.capacity 取值'
  a.plan.generate.push((d) => d.resolve(fillResp({
    sessionId: 's_a', roundId: 'r_a',
    operations: [setOp('label', '额定容量'), setOp('comment', '读取来源表 capacity 字段')],
    questions: [{ id: 'q_1', text: '使用哪个数据连接？', fields: ['source'], options: ['创智园业务库', '储能历史库'], allowUnsure: true }],
  })))
  await panel.generate()
  assert('④a 独立内容先填（2 项）且留在抽屉（另有 1 项待补充）', host.draft().label === '额定容量' && panel.status.value === 'questions' && panel.collapsed.value === false)
  assert('④b 状态条文案含已填/待补充', panel.statusBarText.value === '已填写 2 项，尚未保存；另有 1 项待补充', panel.statusBarText.value)
  assert('④c 第一轮请求不带 sessionId/answers', a.calls.generate[0].sessionId === undefined && a.calls.generate[0].answers === undefined)

  a.plan.context.push(a.okContext({ contextToken: 'tok-2' })) // 续轮握手：重取上下文
  a.plan.generate.push((d) => d.resolve(fillResp({
    sessionId: 's_a', roundId: 'r_b',
    operations: [setOp('source', '创智园业务库')],
  })))
  panel.answer('q_1', '创智园业务库') // 全部问题已答 → 自动续轮
  await sleep(5)
  assert('④d 续轮握手调用序列：先 context 后 generate', JSON.stringify(a.order.slice(-2)) === '["context","generate"]', JSON.stringify(a.order))
  const cont = a.calls.generate[1]
  assert('④e 续轮请求带 sessionId+answers 数组+protocol:2', cont.sessionId === 's_a' && JSON.stringify(cont.answers) === JSON.stringify([{ questionId: 'q_1', value: '创智园业务库' }]) && cont.protocol === 2, JSON.stringify(cont))
  assert('④f 续轮 draft 为应用前序操作后的最新草稿', cont.draft.label === '额定容量' && cont.draft.comment === '读取来源表 capacity 字段')
  assert('④g 续轮前重取了上下文（握手重取，共 2 次 context，新 token）', a.calls.context.length === 2 && cont.contextToken === 'tok-2', `context calls=${a.calls.context.length} token=${cont.contextToken}`)
  await sleep(5)
  assert('④h 补答内容填入且摘要累计（3 项）', host.draft().source === '创智园业务库' && panel.roundSummary.value.appliedCount === 3)
  assert('④i 全部解决后 done 收起', panel.status.value === 'done' && panel.collapsed.value === true && panel.statusBarText.value === '已填写 3 项，尚未保存')

  // 无手改整轮撤销：恢复本轮起点（首轮+续轮全部回退），一次性
  const okUndo = panel.undoRound()
  assert('④j 整轮撤销恢复本轮起点', okUndo === true && host.draft().label === '' && host.draft().source === '' && host.draft().comment === '')
  assert('④k 撤销后摘要清空、状态条为空、不可再撤', panel.roundSummary.value.appliedCount === 0 && panel.statusBarText.value === '' && panel.canUndo.value === false && panel.undoRound() === false)
}

// ⑤ 暂不确定：unsure 请求发出、关联字段转 unresolved，不生成默认口径
{
  const { panel, a, host } = await openedPanel({ hostOpts: { initial: { label: '', comment: '' } } })
  a.plan.generate.push((d) => d.resolve(fillResp({
    sessionId: 's_u', roundId: 'r_u',
    operations: [setOp('label', '额定容量')],
    questions: [{ id: 'q_u', text: '匹配方式用哪种？', fields: ['lookupMatch'], options: [], allowUnsure: true }],
  })))
  await panel.generate()
  a.plan.context.push(a.okContext({ contextToken: 'tok-u2' }))
  a.plan.generate.push((d) => d.resolve(fillResp({
    sessionId: 's_u', roundId: 'r_u2',
    operations: [],
    questions: [],
    unresolved: [{ field: 'lookupMatch', reason: '匹配方式尚未确定，请人工补充' }],
  })))
  panel.answer('q_u', undefined, true)
  await sleep(5)
  const body = a.calls.generate[1]
  assert('⑤a unsure 答案按 {questionId, unsure:true} 提交', JSON.stringify(body.answers) === JSON.stringify([{ questionId: 'q_u', unsure: true }]), JSON.stringify(body.answers || null))
  assert('⑤b unsure 后关联字段转 unresolved 且留在抽屉', panel.status.value === 'questions' && panel.unresolved.value.length === 1 && panel.unresolved.value[0].field === 'lookupMatch')
  assert('⑤c 不生成默认口径（草稿未被写入 lookupMatch）', host.draft().lookupMatch === undefined)
  assert('⑤d 状态条含待补充（unresolved 计入 M）', panel.statusBarText.value === '已填写 1 项，尚未保存；另有 1 项待补充', panel.statusBarText.value)
}

// ⑥ 生成中取消：回到前态、迟到响应零写入；提示不声称上游已停止
{
  const { panel, a, host } = await openedPanel()
  a.plan.generate.push('manual')
  const genPromise = panel.generate()
  await sleep(0)
  assert('⑥a 生成中状态', panel.status.value === 'generating')
  panel.cancel()
  assert('⑥b cancel 回到 open 并提示已取消', panel.status.value === 'open' && (panel.notice.value || '').includes('已取消'))
  assert('⑥c 提示不声称上游已停止', !(panel.notice.value || '').includes('已停止'))
  a.pendings.filter(x => x.kind === 'generate').at(-1).d.resolve(fillResp({ operations: [setOp('label', '迟到值')] }))
  await genPromise
  await sleep(0)
  assert('⑥d 取消后迟到响应零写入', host.draft().label === '簇' && panel.canUndo.value === false)
}

// ⑦ 迟到响应代际失效：关闭抽屉 / 生成中手改 / 切目标
{
  // 7a 关闭抽屉
  {
    const { panel, a, host } = await openedPanel()
    a.plan.generate.push('manual')
    const gp = panel.generate()
    await sleep(0)
    panel.close()
    assert('⑦a close 作废在途请求且回到 open', panel.status.value === 'open' && panel.collapsed.value === true)
    a.pendings.filter(x => x.kind === 'generate').at(-1).d.resolve(fillResp({ operations: [setOp('comment', '迟到写入')] }))
    await gp
    await sleep(0)
    assert('⑦a2 关闭后迟到响应零写入', host.draft().comment === '旧定义')
  }
  // 7b 生成中手改（宿主通知通道）
  {
    const { panel, a, host } = await openedPanel()
    a.plan.generate.push('manual')
    const gp = panel.generate()
    await sleep(0)
    host.apply({ comment: '我手改的' })
    panel.notifyDraftChanged()
    assert('⑦b 手改后生成状态可恢复（不卡在 generating）', panel.status.value === 'open', `status=${panel.status.value}`)
    a.pendings.filter(x => x.kind === 'generate').at(-1).d.resolve(fillResp({ operations: [setOp('comment', '迟到写入')] }))
    await gp
    await sleep(0)
    assert('⑦b2 手改后迟到响应零写入且保留手改', host.draft().comment === '我手改的')
  }
  // 7c 生成中切目标（open 新 binding）
  {
    const { panel, a, host } = await openedPanel()
    a.plan.generate.push('manual')
    const gp = panel.generate()
    await sleep(0)
    const other = makeHost({ label: '别的', comment: '别的' }, { targetKind: 'object', targetId: 'obj_1' })
    a.plan.context.push(a.okContext({ contextToken: 'tok-2' }))
    await panel.open(other.host)
    a.pendings.filter(x => x.kind === 'generate').at(-1).d.resolve(fillResp({ operations: [setOp('comment', '串台写入')] }))
    await gp
    await sleep(0)
    assert('⑦c 切目标后旧响应零写入，新目标草稿不受影响', other.draftNow().comment === '别的' && host.draft().comment === '旧定义')
  }
}

// ⑧ 手改后禁整轮撤销（不覆盖用户改动，附说明）
{
  const { panel, a, host } = await openedPanel({ hostOpts: { initial: { label: '', comment: '旧定义' } } })
  a.plan.generate.push((d) => d.resolve(fillResp({ operations: [setOp('comment', '新定义')] })))
  await panel.generate()
  assert('⑧a 回填后可整轮撤销', panel.canUndo.value === true)
  host.apply({ comment: '我手改的' }) // 期间手改
  panel.notifyDraftChanged()
  assert('⑧b 期间手改 → 禁用整轮撤销且说明', panel.canUndo.value === false && (panel.undoHint.value || '').includes('手动修改'), panel.undoHint.value)
  assert('⑧c 手改后 undoRound 拒绝且不覆盖用户改动', panel.undoRound() === false && host.draft().comment === '我手改的')
}

// ⑨ 新撤销单元：done 后再次生成 → 首轮请求（无 sessionId）、摘要重置、只恢复最近一轮
{
  const { panel, a, host } = await openedPanel({ hostOpts: { initial: { label: '', comment: '' } } })
  a.plan.generate.push((d) => d.resolve(fillResp({ sessionId: 's_1', roundId: 'r_1', operations: [setOp('label', '第一轮')] })))
  await panel.generate()
  panel.expand() // done 自动收起后，用户经入口重新展开再发起下一次自动填写
  a.plan.context.push(a.okContext({ contextToken: 'tok-n' })) // 第二次生成前草稿漂移 → 握手重取
  a.plan.generate.push((d) => d.resolve(fillResp({ sessionId: 's_2', roundId: 'r_2', operations: [setOp('comment', '第二轮')] })))
  await panel.generate()
  assert('⑨a done 后再次生成是首轮请求（不带旧 sessionId）', a.calls.generate[1].sessionId === undefined && a.calls.generate[1].protocol === 2)
  assert('⑨b 摘要重置为新单元（1 项，不含上一轮）', panel.roundSummary.value.appliedCount === 1 && panel.roundSummary.value.changes.length === 1 && panel.roundSummary.value.changes[0].field === 'comment')
  const okUndo = panel.undoRound()
  assert('⑨c 整轮撤销只恢复最近一轮起点（label 保留、comment 回退）', okUndo === true && host.draft().label === '第一轮' && host.draft().comment === '')
}

// ⑩ 错误保留输入与答案、可重试；失败的生成不清上一轮撤销；no-model 拒绝生成
{
  const { panel, a, host } = await openedPanel({ hostOpts: { initial: { label: '', comment: '旧定义' } } })
  a.plan.generate.push((d) => d.resolve(fillResp({ operations: [setOp('comment', '第一轮已填')] })))
  await panel.generate()
  panel.expand() // done 自动收起后重新展开，再次发起自动填写
  a.plan.generate.push(a.fail(genErr('MODEL_TIMEOUT', 504))) // 上一轮完成后再次生成的请求失败
  panel.intent.value = '保留我的输入'
  await panel.generate()
  assert('⑩a 错误进 error 且输入保留', panel.status.value === 'error' && panel.intent.value === '保留我的输入')
  assert('⑩b 失败的生成不清上一轮撤销单元（仍可恢复本轮起点）', panel.canUndo.value === true && panel.undoRound() === true && host.draft().comment === '旧定义', `canUndo=${panel.canUndo.value} comment=${host.draft().comment}`)

  const { panel: p1, a: a1 } = await openedPanel()
  p1.intent.value = 'x'
  a1.plan.generate.push(a1.fail(genErr('MODEL_TIMEOUT', 504)))
  await p1.generate()
  a1.plan.generate.push((d) => d.resolve(fillResp({ operations: [setOp('comment', '重试成功')] })))
  await p1.generate()
  assert('⑩c 错误后重试成功恢复 done', p1.status.value === 'done')

  const { panel: p2, a: a2 } = await openedPanel({ contextOver: { context: { modelReady: false } } })
  p2.intent.value = 'x'
  await p2.generate()
  assert('⑩d 未配置模型进 no-model 且不发生生成请求', p2.status.value === 'no-model' && a2.calls.generate.length === 0 && p2.intent.value === 'x')
}

// ⑪ applyOperations 引擎单测：直写/点路径/codec/clear/行操作/失败记录/等值/顶层 diff
{
  const codecCalls = []
  const codecs = {
    'dataType.type': (draft, field, value) => { codecCalls.push([field, value]); draft.dataType = { type: value, valueType: value === 'timeSeries' ? 'double' : undefined } },
  }
  const d = { label: 'x', nested: { a: 1 }, rows: [{ rowId: 'r1', left: 'id' }] }
  const r = applyOperations(d, [
    setOp('label', 'y'),
    setOp('dataType.type', 'timeSeries'),           // 组/复杂字段 → codec
    setOp('deep.path.value', '新'),                  // 点路径中间节点自动创建
    { op: 'clear', field: 'nested.a' },              // clear 写 null
    { op: 'row.append', field: 'rows', row: { localId: 'r2', fields: { left: 'name' } } },
    { op: 'row.update', field: 'rows', rowId: 'r1', fields: { left: 'code' } },
    { op: 'row.remove', field: 'rows', rowId: 'r9' }, // 未命中 → 失败记录，不影响其他操作
  ], codecs)
  assert('⑪a 普通字段/点路径直写、中间节点自动创建', d.label === 'y' && d.deep.path.value === '新')
  assert('⑪b 注册 codec 的字段走 codec 回调（引擎不直接写）', JSON.stringify(d.dataType) === JSON.stringify({ type: 'timeSeries', valueType: 'double' }) && codecCalls.length === 1)
  assert('⑪c clear 写 null', d.nested.a === null)
  assert('⑪d row.append 默认实现带本地 rowId，row.update 按 rowId 命中', JSON.stringify(d.rows) === JSON.stringify([{ rowId: 'r1', left: 'code' }, { rowId: 'r2', left: 'name' }]), JSON.stringify(d.rows))
  assert('⑪e 未命中行记入 failures，其余独立操作照常生效', r.failures.length === 1 && r.failures[0].op === 'row.remove' && r.failures[0].field === 'rows')
  assert('⑪f 等值操作不计入 changed', applyOperations({ a: 1 }, [setOp('a', 1)]).records[0].changed === false)
  assert('⑪g 顶层变更检测（diff before/after）', JSON.stringify(topLevelChanges({ a: 1, b: 2 }, { a: 1, b: 3 })) === '["b"]')
}

// ⑫ applyDraft 优先通道 + contractInfo 契约指纹不符 → 零写入
{
  {
    const { panel, a, host, calls } = await openedPanel({
      hostOpts: {
        initial: { label: '', dataType: null },
        applyDraft: true,
        codecs: { 'dataType.type': (draft, field, value) => { draft.dataType = { type: value } } },
      },
    })
    a.plan.generate.push((d) => d.resolve(fillResp({ operations: [setOp('dataType.type', 'timeSeries')] })))
    await panel.generate()
    assert('⑫a 提供 applyDraft 时走整稿通道且不再调 apply', calls.applyDraft.length === 1 && calls.apply.length === 0, JSON.stringify(calls.order))
    assert('⑫b applyDraft 收到的草稿已含 codec 写入结果', calls.applyDraft[0].dataType.type === 'timeSeries')
    assert('⑫c 宿主草稿已被整稿替换', host.draft().dataType.type === 'timeSeries')
  }
  {
    let ciCalled = 0
    const { panel, a, host } = await openedPanel({
      hostOpts: {
        initial: { label: '' },
        contractInfo: () => { ciCalled++; return { schemaVersion: 2, schemaDigest: 'dig-NEW' } },
      },
    })
    a.plan.generate.push((d) => d.resolve(fillResp({ schemaVersion: 1, schemaDigest: 'dig-1', operations: [setOp('label', '不该写入')] })))
    await panel.generate()
    assert('⑫d 契约指纹不符 → 结果作废零写入', host.draft().label === '' && panel.status.value === 'error' && panel.error.value && panel.error.value.code === 'CONTEXT_STALE')
    assert('⑫e 比对读取了宿主契约信息', ciCalled > 0)
  }
}

// ⑬ check/explain 次要模式：旧结构响应、不带 protocol、只读不写
{
  const { panel, a, host } = await openedPanel()
  a.plan.generate.push((d) => d.resolve({
    requestId: 'srv-c', status: 'ok', contextFingerprint: 'cfp-1',
    questions: [], suggestions: [{ id: 's1', label: '参考', fieldKeys: ['comment'], proposed: { comment: '只读建议' }, state: 'ready' }],
    issues: [{ fieldKey: 'comment', message: '定义不完整' }], explanation: null,
    meta: { durationMs: 5, provider: 'x', model: 'x' },
  }))
  await panel.runHelp('check')
  const body = a.calls.generate[0]
  assert('⑬a check 请求沿用旧结构（无 protocol/无 sessionId）', body.mode === 'check' && body.protocol === undefined && body.sessionId === undefined)
  assert('⑬b check 结果只读可读（issues）', panel.helpStatus.value === 'done' && panel.helpResult.value.issues.length === 1)
  assert('⑬c 帮助模式不触达主流程草稿写入', host.draft().comment === '旧定义' && panel.roundSummary.value.appliedCount === 0 && panel.canUndo.value === false)

  a.plan.generate.push((d) => d.resolve({
    requestId: 'srv-e', status: 'ok', contextFingerprint: 'cfp-1',
    questions: [], suggestions: [], issues: [],
    explanation: { title: '怎么填', body: '分三步：命名、定义、类型。' },
    meta: { durationMs: 6, provider: 'x', model: 'x' },
  }))
  await panel.runHelp('explain')
  assert('⑬d explain 只读结果可读', panel.helpStatus.value === 'done' && panel.helpResult.value.explanation && panel.helpResult.value.explanation.title === '怎么填')
}

// ⑭ 保存红线：整套流程（回填+续轮+撤销）后宿主保存类通道零调用
{
  let tripped = ''
  const guard = (name) => () => { tripped = name; throw new Error('回填不得调用 ' + name) }
  const { panel, a, host } = await openedPanel({
    hostOpts: {
      initial: { label: '', comment: '', source: '' },
      commitNow: guard('commitNow'), touch: guard('touch'), submitForm: guard('submitForm'), saveDraft: guard('saveDraft'),
    },
  })
  a.plan.generate.push((d) => d.resolve(fillResp({
    sessionId: 's_g', operations: [setOp('comment', '只改草稿')],
    questions: [{ id: 'q_g', text: '连接？', fields: ['source'], options: ['a', 'b'], allowUnsure: true }],
  })))
  await panel.generate()
  a.plan.context.push(a.okContext({ contextToken: 'tok-g2' }))
  a.plan.generate.push((d) => d.resolve(fillResp({ sessionId: 's_g', operations: [setOp('source', 'a')] })))
  panel.answer('q_g', 'a')
  await sleep(5)
  panel.undoRound()
  await sleep(0)
  assert('⑭ 回填/续轮/撤销全程不触发表单保存通道', tripped === '' && host.draft().comment === '' && host.draft().source === '')
}

const failed = results.filter(r => !r.ok)
console.log(`\n统计：${results.length - failed.length}/${results.length} 项通过`)
if (failed.length) {
  console.log('未通过：' + failed.map(f => f.name).join('；'))
  process.exit(1)
}
