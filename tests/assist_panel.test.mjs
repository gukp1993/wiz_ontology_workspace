// 整表自动填写面板交互契约测试（T3，2026-09-22 改版重写）。运行（仓库根）：
//   node --import ./tests/ts_hooks.mjs tests/assist_panel.test.mjs
// 旧「建议卡→勾选→采纳」断言已随改版移除；本套件锁定新交互契约：
//   ① 回填直接生效：生成→一次填入草稿，无勾选步骤（控制器不再有 checked/adopt/setChecked）；
//   ② 状态条文案由状态机交给宿主渲染（已填 N 项/另有 M 项待补充/empty 文案）；
//   ③ 补问卡数据形态（问题文本/选项/暂不确定）与答复转续轮；
//   ④ 撤销：整轮一次、期间手改禁用；
//   ⑤ 回填绝不触发表单保存通道；请求经 AssistApi 携带 autofill/1 扩展字段（protocol:2/sessionId/answers）。
// 状态机代际/握手/codec 细节见 tests/test_autofill_state.mjs；两者共用同一引擎，桩各自自足。
const { useAssistPanel } = await import('../frontend/src/assist/useAssistPanel.ts')
const { AUTOFILL_EMPTY_TEXT } = await import('../frontend/src/assist/formAutofill.ts')
const { SaveRequestError } = await import('../frontend/src/app/http.ts')

const sleep = (ms = 0) => new Promise(r => setTimeout(r, ms))
const results = []
function assert(name, cond, detail = '') { results.push({ name, ok: !!cond }); console.log(`${cond ? '通过' : '失败'}：${name}${cond ? '' : ' — ' + (detail || '断言不成立')}`) }

// ── 假宿主 binding（apply 可观察；保存类通道即测试失败）─────────────────────────
function makeHost(initial = { label: '', comment: '旧定义' }) {
  let draft = JSON.parse(JSON.stringify(initial))
  const calls = { apply: [], snapshot: 0, restore: [] }
  const host = {
    space: 'ontology', targetKind: 'property', targetId: 'mg:prop_1', contextTitle: '属性「SOC采样值」',
    draft: () => JSON.parse(JSON.stringify(draft)),
    apply: (values) => { calls.apply.push(JSON.parse(JSON.stringify(values))); Object.assign(draft, values) },
    snapshot: () => { calls.snapshot++; return JSON.parse(JSON.stringify(draft)) },
    restore: (snap) => { calls.restore.push(JSON.parse(JSON.stringify(snap))); draft = JSON.parse(JSON.stringify(snap)) },
    commitNow: () => { throw new Error('面板不得调用 commitNow') },
    touch: () => { throw new Error('面板不得调用 touch') },
  }
  return { host, calls, draftNow: () => draft }
}

// ── 假 api：受控应答 ────────────────────────────────────────────────────────
function makeApi() {
  const calls = { context: [], generate: [] }
  const pendings = []
  const plan = { context: [], generate: [] }
  const wire = (kind) => async (body) => {
    calls[kind].push(JSON.parse(JSON.stringify(body)))
    const d = defer()
    pendings.push({ kind, d })
    const step = plan[kind].shift()
    if (step === 'manual') { /* 挂起 */ }
    else if (typeof step === 'function') step(d)
    else d.reject(new SaveRequestError('测试桩未编排该请求', 500, null, { error: '未编排', code: 'UNPLANNED' }))
    return d.p
  }
  return {
    api: { context: wire('context'), generate: wire('generate') },
    calls, pendings, plan,
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

async function openedPanel({ host, contextOver } = {}) {
  const h = host || makeHost().host
  const a = makeApi()
  a.plan.context.push(a.okContext(contextOver || {}))
  const panel = useAssistPanel(a.api)
  await panel.open(h)
  return { panel, a, host: h }
}

// ① 新交互契约：回填直接生效，勾选/采纳 API 已随旧建议卡移除
{
  const { panel, a, host } = await openedPanel({ host: makeHost({ label: '', comment: '旧定义' }).host })
  assert('①a 控制器不再暴露 checked/adopt/setChecked/suggestions（无勾选卡）',
    panel.checked === undefined && panel.adopt === undefined && panel.setChecked === undefined && panel.suggestions === undefined,
    JSON.stringify(Object.keys(panel).sort()))
  assert('①b 保留新交互入口', typeof panel.generate === 'function' && typeof panel.answer === 'function' && typeof panel.undoRound === 'function'
    && typeof panel.open === 'function' && typeof panel.close === 'function' && typeof panel.expand === 'function'
    && typeof panel.runHelp === 'function' && typeof panel.notifyDraftChanged === 'function')
  assert('①c 新增 autofill 摘要/状态条/会话出口', !!panel.roundSummary && !!panel.statusBarText && !!panel.sessionId && !!panel.lastSchema && !!panel.unresolved && !!panel.questions)

  panel.intent.value = '新增采样SOC'
  a.plan.generate.push((d) => d.resolve(fillResp({
    operations: [setOp('label', '采样SOC'), setOp('comment', '电池剩余电量占额定容量的百分比')],
  })))
  await panel.generate()
  assert('①d 生成后一次回填直接生效（不经勾选/采纳）', host.draft().label === '采样SOC' && host.draft().comment === '电池剩余电量占额定容量的百分比')
  assert('①e done 并收起抽屉；草稿改动交宿主高亮/保存', panel.status.value === 'done' && panel.collapsed.value === true)
}

// ② 状态条文案由状态机给宿主渲染（面板不渲染表单状态条）
{
  // 同轮「独立内容 + 待补问题」：已填 N 项，另有 M 项待补充
  const { panel, a } = await openedPanel({ host: makeHost({ label: '', comment: '', source: '' }).host })
  panel.intent.value = '填写'
  a.plan.generate.push((d) => d.resolve(fillResp({
    sessionId: 's_1',
    operations: [setOp('label', 'A'), setOp('comment', 'B')],
    questions: [{ id: 'q1', text: '用哪个连接？', fields: ['source'], options: ['c1', 'c2'], allowUnsure: true }],
  })))
  await panel.generate()
  assert('②a 已填 N 项，另有 M 项待补充', panel.statusBarText.value === '已填写 2 项，尚未保存；另有 1 项待补充', panel.statusBarText.value)
  assert('②b 摘要含逐字段旧值→新值', panel.roundSummary.value.changes.length === 2
    && panel.roundSummary.value.changes[0].oldText === '（空）' && panel.roundSummary.value.changes[0].newText === 'A')

  // 无待补的纯回填：已填 N 项，尚未保存
  const { panel: p3, a: a3 } = await openedPanel({ host: makeHost({ label: '', comment: '' }).host })
  a3.plan.generate.push((d) => d.resolve(fillResp({ operations: [setOp('label', 'A')] })))
  await p3.generate()
  assert('②c 已填 N 项，尚未保存', p3.statusBarText.value === '已填写 1 项，尚未保存', p3.statusBarText.value)

  // empty 文案：内容已一致，无需修改（面板与宿主共用常量）
  const { panel: p2, a: a2 } = await openedPanel()
  a2.plan.generate.push((d) => d.resolve(fillResp({ status: 'empty' })))
  await p2.generate()
  assert('②d empty 明确文案，不假称成功', p2.status.value === 'empty' && AUTOFILL_EMPTY_TEXT === '内容已一致，无需修改' && p2.statusBarText.value === '')
}

// ③ 补问卡数据形态与答复转续轮
{
  const { panel, a, host } = await openedPanel({ host: makeHost({ label: '', comment: '', source: '' }).host })
  a.plan.generate.push((d) => d.resolve(fillResp({
    sessionId: 's_q',
    operations: [setOp('label', '额定容量')],
    questions: [{ id: 'q_1', text: '使用哪个数据连接？', fields: ['source'], options: ['创智园业务库', '储能历史库'], allowUnsure: true }],
  })))
  await panel.generate()
  assert('③a 补问卡：问题文本/字段/选项/允许暂不确定', panel.questions.value.length === 1
    && panel.questions.value[0].text === '使用哪个数据连接？'
    && JSON.stringify(panel.questions.value[0].options) === JSON.stringify(['创智园业务库', '储能历史库'])
    && panel.questions.value[0].allowUnsure === true)
  assert('③b 留在抽屉等补答（不收起）', panel.status.value === 'questions' && panel.collapsed.value === false)

  a.plan.context.push(a.okContext({ contextToken: 'tok-q2' }))
  a.plan.generate.push((d) => d.resolve(fillResp({ sessionId: 's_q', operations: [setOp('source', '创智园业务库')] })))
  panel.answer('q_1', '创智园业务库') // 答复分字段提交 → 自动续轮
  await sleep(5)
  const cont = a.calls.generate[1]
  assert('③c 答复按 questionId 携带并续轮', JSON.stringify(cont.answers) === JSON.stringify([{ questionId: 'q_1', value: '创智园业务库' }]) && cont.sessionId === 's_q')
  assert('③d 续轮完成收起', panel.status.value === 'done' && panel.collapsed.value === true && host.draft().source === '创智园业务库')
  assert('③e 拒绝不属于当前轮的问题答复（防 q_id 串轮）', (() => { panel.answer('q_old', 'x'); return Object.keys(panel.answers.value).length === 0 })())
}

// ④ 撤销：整轮一次；期间手改禁用并说明
{
  const made = makeHost({ label: '', comment: '旧定义' })
  const { panel, a, host } = await openedPanel({ host: made.host })
  const calls = made.calls
  a.plan.generate.push((d) => d.resolve(fillResp({ operations: [setOp('comment', '新定义')] })))
  await panel.generate()
  assert('④a 回填后 canUndo（撤销按钮可用性由宿主按它渲染）', panel.canUndo.value === true)
  assert('④b undoRound 恢复本轮起点', panel.undoRound() === true && host.draft().comment === '旧定义')
  assert('④c 一次性：撤销后不可再撤', panel.canUndo.value === false && panel.undoRound() === false)

  // 再填一轮，期间手改 → 禁用整轮撤销
  panel.expand() // done 自动收起后重新展开
  a.plan.context.push(a.okContext({ contextToken: 'tok-r2' }))
  a.plan.generate.push((d) => d.resolve(fillResp({ operations: [setOp('comment', '第二轮')] })))
  await panel.generate()
  host.apply({ comment: '我手改的' }) // 期间手改（不经面板）
  panel.notifyDraftChanged()
  assert('④d 期间手改 → 禁用整轮撤销并说明', panel.canUndo.value === false && (panel.undoHint.value || '').includes('手动修改'))
  assert('④e 手改值不被撤销覆盖', panel.undoRound() === false && host.draft().comment === '我手改的')
}

// ⑤ 保存边界与 autofill/1 请求字段
{
  const made = makeHost({ label: '', comment: '' })
  const { panel, a, host } = await openedPanel({ host: made.host })
  const calls = made.calls
  panel.intent.value = 'x'
  a.plan.generate.push((d) => d.resolve(fillResp({ operations: [setOp('label', 'L')] })))
  await panel.generate()
  assert('⑤a 回填只调宿主 apply（不触 save 通道）', JSON.stringify(calls.apply) === JSON.stringify([{ label: 'L' }]))
  assert('⑤b fill 请求带 protocol:2/mode:fill/draft 快照', a.calls.generate[0].protocol === 2 && a.calls.generate[0].mode === 'fill' && typeof a.calls.generate[0].draft === 'object')
  assert('⑤c 回填后等待（超过自动保存窗口）引擎无任何额外写入', await Promise.resolve(panel.statusBarText.value) === '已填写 1 项，尚未保存' && calls.apply.length === 1)
}

// ⑥ 主按钮样式不变量（2026-09-22 浏览器验收缺陷回归锁）：
//    `.assist-actions button`(0,1,1) 只写 background/border，若主操作变体是裸 .assist-primary(0,1,0)，
//    它的 color:#fff 会压不住前者的……不，是反过来：前者压住后者的背景而后者仍给白字 → 白字白底空框。
//    断言：主操作变体必须挂在 `.assist-actions button.assist-primary`（后代+元素抬特异性）而非裸单类。
{
  const { readFileSync } = await import('node:fs')
  const src = readFileSync('frontend/src/assist/AssistPanel.vue', 'utf8')
  assert('⑥a 主按钮声明用 .assist-actions button.assist-primary 抬特异性',
    /\.assist-actions button\.assist-primary\s*\{/.test(src),
    '缺抬特异性的主按钮声明（会渲染成空框）')
  assert('⑥b hover 也带同样特异性（压过 .assist-actions button:hover）',
    /\.assist-actions button\.assist-primary:hover:not\(:disabled\)\s*\{/.test(src))
  assert('⑥c 不再存在裸 .assist-primary 单类声明',
    !/(^|\n)\.assist-primary\s*\{/.test(src))
}

const failed = results.filter(r => !r.ok)
console.log(`\n统计：${results.length - failed.length}/${results.length} 项通过`)
if (failed.length) {
  console.log('未通过：' + failed.map(f => f.name).join('；'))
  process.exit(1)
}
