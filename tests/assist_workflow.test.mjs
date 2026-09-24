// 业务规则（O4）与动作定义（O5）整表自动填写接入测试（2026-09-22 T5 改版）。运行（仓库根）：
//   node --import ./tests/ts_hooks.mjs tests/assist_workflow.test.mjs
// 编译真实 BusinessRuleLibrary.vue / ActionLibrary.vue 与 AssistPanel.vue（T3 抽屉），
// 其余子组件桩化、无路由无服务、不发真实请求；assist api 经 provide('assist-api') 注入合成桩
// （autofill/1 fill 响应内存队列，同 assist_object_workspace.test.mjs）。
// 覆盖（新交互，旧「建议卡→勾选→采纳」断言随改版移除）：
//   ① binding 工厂新对接面：formId/contractInfo（生成物指纹）/白名单 draft（契约 fieldId 恒等，
//      历史 output 不出网）/applyDraft 合并零丢失并镜像/撤销单元/手改禁撤销；
//   ② A01 默认无 AI 区：页头「✦ 自动填写」（aria-expanded/aria-haspopup）；新建规则 targetId 空串；
//   ③ 生成→直接回填：draft 变化、状态条「已填写 N 项，尚未保存」、form-save/changed 零调用；
//   ④ 宿主撤销（恢复本轮起点、一次性）与手改禁撤销；
//   ⑤ 规则历史 output 只读区不受自动填写影响；
//   ⑥ 切换编辑目标以新 binding 重开、关闭编辑器面板卸载；
//   ⑦ 动作新建（DEF-02：预生成 id 但 targetId 空串）回填同链路；
//   ⑧ 动作编辑已有（targetId=该 id）、setField 手改通知；
//   ⑨ 全程零保存兜底。
// 已知边界：SSR 渲染不执行 onMounted（面板自动取上下文）与模板 ref 填充（notify/toggle/二次
// 点击收起通道），由「驱动面板暴露的状态机 + 手改计数 + typecheck」覆盖，浏览器实链路留给独立验收。
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { readFileSync, mkdtempSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve, dirname } from 'node:path'
import { pathToFileURL } from 'node:url'

const require = createRequire(new URL('../frontend/package.json', import.meta.url))
const { parse, compileScript, compileTemplate } = require('@vue/compiler-sfc')
const ts = require('typescript')
const { createSSRApp, reactive, h } = require('vue')
const { renderToString } = require('@vue/server-renderer')

// 组件里用到的浏览器 API：SSR 不执行 onMounted，但模块顶层与 watch(immediate) 会触达它们。
globalThis.window = { matchMedia: () => ({ matches: false, addEventListener() {}, removeEventListener() {} }), addEventListener() {}, removeEventListener() {}, innerHeight: 900, location: { hash: '' } }
globalThis.document = { addEventListener() {}, removeEventListener() {}, querySelector: () => null, querySelectorAll: () => [], activeElement: null }
globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} }
globalThis.requestAnimationFrame = cb => setTimeout(() => cb(Date.now()), 0)
if (!globalThis.crypto?.randomUUID) Object.defineProperty(globalThis.crypto, 'randomUUID', { value: require('node:crypto').webcrypto.randomUUID.bind(require('node:crypto').webcrypto), configurable: true })

const root = mkdtempSync(join(tmpdir(), 'wiz_assist_wf_'))

/** 编译真实 SFC（script setup + SSR 模板）。kept：{ 组件名: globalThis 变量名 }，命中的 .vue 导入
 *  改挂到共享变量（用于把真实 AssistPanel 塞进两个库页），其余 .vue 一律桩成 render:null。 */
async function loadSFC(absPath, id, kept = {}) {
  const { descriptor } = parse(readFileSync(absPath, 'utf8'), { filename: absPath })
  const script = compileScript(descriptor, { id })
  let code = script.content
  for (const [name, globalName] of Object.entries(kept)) {
    code = code.replace(new RegExp('import\\s+' + name + '\\s+from\\s*[\'"][^\'"]+\\.vue[\'"]'), 'const ' + name + ' = globalThis.' + globalName)
  }
  code = code.replace(/import\s+(\w+)\s+from\s*['"][^'"]*\.vue['"]/g, 'const $1 = { render: () => null }')
  const tpl = compileTemplate({ source: descriptor.template.content, filename: absPath, id, ssr: true, ssrCssVars: [], compilerOptions: { bindingMetadata: script.bindings } })
  assert.deepEqual(tpl.errors, [])
  code = code.replace('export default ', 'const Component = ') + '\n' + tpl.code + '\nComponent.ssrRender=ssrRender; export default Component;'
  // 注意：重写 from '…' 必须在拼入 SSR 模板代码之后（模板代码同样 from "vue" 引辅助函数）；
  // 相对导入补 .ts，包名（vue / vue/server-renderer）按 frontend 依赖解析。
  code = code.replace(/from (['"])([^'"]+)\1/g, (_, q, spec) => {
    const target = spec.startsWith('.') ? resolve(dirname(absPath), spec + '.ts') : require.resolve(spec)
    return 'from ' + JSON.stringify(pathToFileURL(target).href)
  })
  const file = join(root, id + '.mjs')
  writeFileSync(file, ts.transpileModule(code, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText)
  return (await import(pathToFileURL(file).href)).default
}

// 先编译真实面板，再编译两个库页（其 AssistPanel 导入指向真实面板）。
const Panel = await loadSFC(resolve('frontend/src/assist/AssistPanel.vue'), 'assist_panel_real')
globalThis.__assistPanelComp = Panel
const RuleLib = await loadSFC(resolve('frontend/src/ontology/BusinessRuleLibrary.vue'), 'rule_lib_assist', { AssistPanel: '__assistPanelComp' })
const ActionLib = await loadSFC(resolve('frontend/src/ontology/ActionLibrary.vue'), 'action_lib_assist', { AssistPanel: '__assistPanelComp' })

const { FORM_SCHEMA_DIGESTS } = await import('../frontend/src/assist/formContracts.gen.ts')
const { ASSIST_ENTRY_ENABLED } = await import('../frontend/src/assist/useAssistPanel.ts')

// ── 捕获每次挂载的 setup 状态（多场景互不串线）────────────────────────────────
let activeSlot = null
function captureSetup(Comp) {
  const orig = Comp.setup
  Comp.setup = (props, ctx) => { const api = orig(props, ctx); if (activeSlot) activeSlot.api = api; return api }
}
captureSetup(RuleLib)
captureSetup(ActionLib)
const origPanelSetup = Panel.setup
Panel.setup = (props, ctx) => { const s = origPanelSetup(props, ctx); if (activeSlot) activeSlot.panel = s; return s }

// ── assist-api 桩：autofill/1 fill 响应内存队列；context/generate 记录请求体 ──────
const setOp = (field, value) => ({ op: 'set', field, value, basis: { kind: 'intent', quote: '测试依据' } })
function makeAssistApi() {
  const calls = { context: [], generate: [] }
  const fillQueue = []
  let n = 0
  const lastTarget = { targetKind: 'rule', targetId: '' } // 服务端会话绑定最近一次 context 的目标（回显用）
  return {
    calls, fillQueue,
    async context(body) {
      calls.context.push(JSON.parse(JSON.stringify(body)))
      lastTarget.targetKind = body.targetKind
      lastTarget.targetId = body.targetId
      return {
        contextToken: 'tok-' + calls.context.length,
        contextFingerprint: 'fp-' + calls.context.length,
        context: { targetKind: body.targetKind, title: 'CTX[' + body.targetKind + ':' + body.targetId + ']', editableFields: [], definitions: [], catalog: [], flows: [], modelReady: true },
      }
    },
    async generate(body) {
      calls.generate.push(JSON.parse(JSON.stringify(body)))
      const over = fillQueue.shift()
      if (!over) throw Object.assign(new Error('测试桩未编排该请求'), { data: { code: 'UNPLANNED' } })
      n++
      const formId = over.formId || 'rule'
      return Object.assign({
        protocol: 'autofill/1', status: 'ok', requestId: 'srv-' + n, formId,
        schemaVersion: 1, schemaDigest: FORM_SCHEMA_DIGESTS[formId],
        target: { space: 'ontology', targetKind: lastTarget.targetKind, targetId: lastTarget.targetId },
        draftFingerprint: 'dfp-' + n, contextFingerprint: 'cfp-1',
        sessionId: 's-' + n, roundId: 'r-' + n,
        operations: [], questions: [], unresolved: [], summary: '',
        meta: { durationMs: 5, provider: '测试桩', model: 'stub' },
      }, over)
    },
  }
}

// ── form-save 间谍：被调用即记录（回填后必须保持 0 次）──────────────────────────
function makeFormSaveSpy() {
  const saves = []
  return { saves, async submitForm(area) { saves.push(area); return { ok: true, message: '' } } }
}

function fixtureState() {
  return reactive({
    ontology: { '@graph': [{ '@id': 'mg:cluster', '@type': 'owl:Class', 'rdfs:label': '储能簇' }] },
    workflow: {
      businessRules: [
        { id: 'rule_1', name: '储能SOC计算规则', description: '按统一统计范围计算储能设备剩余电量占比。', content: 'soc = 剩余电量 / 额定容量 × 100' },
        { id: 'rule_2', name: '效率折算规则', description: '按额定效率折算可用功率。', content: '', output: '历史输出：折算系数 0.95' },
      ],
      actions: [
        { id: 'action_1', name: '停止充放电', description: '请求目标对象停止当前充电或放电。', effect: '设备退出充放电运行状态。', definitionVersion: 2, status: 'experimental' },
        { id: 'action_2', name: '启动充放电', description: '请求目标对象开始充电或放电。', effect: '', definitionVersion: 2, status: 'experimental' },
      ],
    },
  })
}

function mountLibrary(Comp) {
  const slot = { api: null, panel: null }
  const state = fixtureState()
  const formSave = makeFormSaveSpy()
  const assist = makeAssistApi()
  let changedCount = 0
  const propsObj = { state, onChanged: () => { changedCount++ } } // 'changed' emit 间谍（回填后必须 0 次）
  const app = createSSRApp(Comp, propsObj)
  app.provide('form-guard', { register() {}, unregister() {}, hasDirty: () => false, editing: () => false })
  app.provide('form-save', formSave)
  app.provide('assist-api', assist)
  activeSlot = slot
  const initial = renderToString(app)
  return {
    state, formSave, assist, initial,
    changed: () => changedCount,
    get api() { return slot.api },
    get panel() { return slot.panel },
    // 用同一份 setup 状态重渲染页面（面板实例随每次渲染重建；整页断言只看宿主状态条/入口按钮）
    html: () => { activeSlot = slot; return renderToString(createSSRApp({ props: Comp.props, ssrRender: Comp.ssrRender, setup: () => slot.api }, propsObj)) },
    // 用驱动后的面板状态单独渲染抽屉（验证标题/横幅等问题卡 UI 呈现）
    panelHtml: () => renderToString(createSSRApp({ props: Panel.props, ssrRender: Panel.ssrRender, setup: () => slot.panel }, { binding: slot.api.assistBinding.value })),
  }
}

const results = []
async function check(name, fn) { try { await fn(); results.push(true); console.log('通过：' + name) } catch (e) { results.push(false); console.log('失败：' + name + ' — ' + (e && e.message)) } }

try {
  // ── ① binding 工厂（新对接面）──
  {
    const { ruleAssistBinding, actionAssistBinding } = await import('../frontend/src/assist/workflowBindings.ts')
    await check('① 规则 binding：formId/契约指纹、白名单 draft（历史 output 不出网）、applyDraft 镜像、撤销单元、手改禁撤销', async () => {
      const d = { name: '储能SOC计算规则', description: '旧定义', content: '旧内容' }
      const b = ruleAssistBinding(d, { targetId: 'rule_1', contextTitle: '标题T' })
      assert.equal(b.space, 'ontology'); assert.equal(b.targetKind, 'rule'); assert.equal(b.targetId, 'rule_1'); assert.equal(b.contextTitle, '标题T')
      assert.equal(b.formId, 'rule', 'binding 声明 autofill/1 表单契约 id')
      assert.deepEqual(b.contractInfo(), { schemaVersion: 1, schemaDigest: FORM_SCHEMA_DIGESTS.rule }, 'contractInfo 取自前端契约生成物')
      assert.deepEqual(Object.keys(b.draft()), ['name', 'description', 'content'], 'draft 即契约形态标准化草稿（历史 output 等不出网）')
      assert.deepEqual(b.codecs, {}, '规则表单无复杂 codec：普通字段 identity 直写')

      const snap = b.snapshot()
      assert.deepEqual(snap, { name: '储能SOC计算规则', description: '旧定义', content: '旧内容' })
      b.applyDraft({ name: '储能SOC计算规则（修订）', content: '统一口径后的公式。' })
      assert.equal(d.name, '储能SOC计算规则（修订）'); assert.equal(d.description, '旧定义', '未提及字段逐字保留')
      assert.equal(b.round.appliedCount, 2, '已填写 2 项（顶层实际改变数）')
      assert.equal(b.round.statusBarText, '已填写 2 项，尚未保存')
      assert.deepEqual(b.round.changes.map(c => [c.field, c.label, c.oldText, c.newText]), [
        ['name', '规则名称', '储能SOC计算规则', '储能SOC计算规则（修订）'],
        ['content', '规则内容', '旧内容', '统一口径后的公式。'],
      ])
      assert.equal(b.round.canUndo, true)
      assert.equal(b.undoRound(), true)
      assert.equal(d.name, '储能SOC计算规则', '撤销恢复本轮起点')
      assert.equal(b.round.statusBarText, ''); assert.equal(b.round.undone, true)
      assert.equal(b.undoRound(), false, '一次性')

      b.applyDraft({ name: 'A' }); b.noteManualChange()
      assert.equal(b.round.canUndo, false); assert.match(b.round.undoHint, /手动修改/)
      assert.equal(b.undoRound(), false, '手改后整轮撤销被拒'); assert.equal(d.name, 'A')
      b.applyDraft({ description: 'B' })
      assert.equal(b.round.canUndo, false, '手改后的续轮落回不再产生可撤销快照')
      assert.equal(b.round.appliedCount, 2); assert.equal(b.undoRound(), false)

      const er = ruleAssistBinding(null) // 空草稿兜底
      assert.deepEqual(er.draft(), { name: '', description: '', content: '' }); assert.equal(er.undoRound(), false)
      er.applyDraft({ name: 'x' }); er.observeFill(undefined); assert.ok(true, '空草稿安全')
    })

    await check('①b 动作 binding：formId/契约指纹、三字段白名单、observeFill 按 formId/target/指纹过滤', async () => {
      const ad = { name: '', description: '', effect: '' }
      const ab = actionAssistBinding(ad) // 缺省：targetId ''、兜底标题非空
      assert.equal(ab.targetKind, 'action'); assert.equal(ab.targetId, ''); assert.equal(ab.formId, 'action')
      assert.deepEqual(ab.contractInfo(), { schemaVersion: 1, schemaDigest: FORM_SCHEMA_DIGESTS.action })
      assert.deepEqual(Object.keys(ab.draft()), ['name', 'description', 'effect'])
      ab.snapshot()
      ab.applyDraft({ name: '停止充放电', effect: '设备退出充放电运行状态。' })
      assert.equal(ad.name, '停止充放电'); assert.equal(ad.effect, '设备退出充放电运行状态。')
      assert.equal(ab.round.statusBarText, '已填写 2 项，尚未保存')
      assert.equal(ab.undoRound(), true); assert.equal(ad.name, '')

      const ok = over => Object.assign({ protocol: 'autofill/1', status: 'ok', formId: 'action', schemaVersion: 1, schemaDigest: FORM_SCHEMA_DIGESTS.action, target: { space: 'ontology', targetKind: 'action', targetId: '' }, questions: [{ id: 'q1' }], unresolved: [{ field: 'effect', reason: '待补' }] }, over)
      ab.observeFill(ok({}))
      assert.equal(ab.round.pendingCount, 2, '待补充 = questions + unresolved')
      ab.observeFill(ok({ formId: 'rule' })); assert.equal(ab.round.pendingCount, 2, 'formId 不符不计')
      ab.observeFill(ok({ target: { space: 'ontology', targetKind: 'action', targetId: 'action_1' } })); assert.equal(ab.round.pendingCount, 2, '目标不符不计')
      ab.observeFill(ok({ schemaDigest: 'bad' })); assert.equal(ab.round.pendingCount, 2, '契约指纹不符不计')
      ab.observeFill(ok({ status: 'empty', questions: [], unresolved: [] })); assert.equal(ab.round.pendingCount, 0, 'empty 清空待补充')
    })
  }

  // ── ②~⑥ 规则编辑表单全链路（共用同一份编辑状态）──
  {
    const ctx = mountLibrary(RuleLib)
    await ctx.initial

    await check('② A01 默认无 AI 区：新建规则编辑表单页头「✦ 自动填写」；新建 targetId 空串', async () => {
      ctx.api.openEdit() // 新建：isNewRule=true
      assert.equal(ctx.api.isNewRule.value, true)
      const b = ctx.api.assistBinding.value
      assert.equal(b.targetKind, 'rule'); assert.equal(b.targetId, '', '新建规则尚未入库，targetId 传空串')
      assert.equal(b.formId, 'rule')
      let page = await ctx.html()
      if (ASSIST_ENTRY_ENABLED) {
        assert.match(page, /✦ 自动填写/, '编辑表单页头次要入口')
        assert.match(page, /aria-expanded="false"/)
        assert.match(page, /aria-haspopup="dialog"/)
        assert.ok(page.includes('id="rule-assist-trigger"'), '入口按钮 id（面板 triggerId 指向它）')
      } else {
        assert.ok(!page.includes('✦ 自动填写'), '功能开关关闭：编辑表单页头不渲染入口')
      }
      assert.ok(!page.includes('assist-drawer'), '默认不渲染任何 AI 区域')
      ctx.api.toggleAssist() // 与按钮 @click 同一函数：首次点击挂载面板
      page = await ctx.html()
      if (ASSIST_ENTRY_ENABLED) assert.match(page, /aria-expanded="true"/)
      assert.ok(ctx.panel, '面板实例已随页面创建')
      assert.equal(ctx.panel.checked, undefined, '旧勾选/采纳 API 已移除')
      assert.equal(ctx.panel.adopt, undefined); assert.equal(ctx.panel.setChecked, undefined)
      await ctx.panel.open(ctx.api.assistBinding.value)
      const phtml = await ctx.panelHtml()
      assert.match(phtml, /assist-drawer/, '点开后抽屉出现')
      assert.match(phtml, /CTX\[rule:\]/, '新建规则兜底目标')
    })

    await check('②b 切到编辑既有规则：上下文请求携带场景与契约形态草稿（历史 output 不出网）', async () => {
      ctx.api.openEdit('rule_1')
      assert.equal(ctx.api.assistOpen.value, true, '切换目标后面板保持挂载')
      const b = ctx.api.assistBinding.value
      assert.equal(b.targetId, 'rule_1')
      assert.equal(b.contextTitle, '维护「储能SOC计算规则」的业务规则', '兜底标题随目标更新')
      await ctx.panel.open(b)
      const body = ctx.assist.calls.context.at(-1)
      assert.equal(body.space, 'ontology'); assert.equal(body.targetKind, 'rule')
      assert.equal(body.targetId, 'rule_1'); assert.equal(body.purpose, 'fill')
      assert.deepEqual(body.draft, { name: '储能SOC计算规则', description: '按统一统计范围计算储能设备剩余电量占比。', content: 'soc = 剩余电量 / 额定容量 × 100' }, 'draft 是契约形态快照')
      const phtml = await ctx.panelHtml()
      assert.match(phtml, /CTX\[rule:rule_1\]/, '取到上下文后展示服务端标题')
    })

    await check('③ 生成→直接回填（无勾选步骤）：draft 变化、状态条、零保存零 changed', async () => {
      ctx.assist.fillQueue.push({ formId: 'rule', operations: [
        setOp('name', '储能SOC计算规则（修订）'),
        setOp('description', '按统一统计范围计算储能设备剩余电量占比，含冰损修正。'),
        setOp('content', 'soc = 剩余电量 / 额定容量 × 100（统一口径）。'),
      ] })
      await ctx.panel.generate()
      assert.equal(ctx.assist.calls.generate.length, 1)
      const body = ctx.assist.calls.generate[0]
      assert.equal(body.mode, 'fill'); assert.equal(body.protocol, 2, 'autofill/1 请求必带 protocol:2')
      assert.equal(body.contextToken, 'tok-' + ctx.assist.calls.context.length, '携带最近一次上下文令牌')
      assert.deepEqual(body.draft, { name: '储能SOC计算规则', description: '按统一统计范围计算储能设备剩余电量占比。', content: 'soc = 剩余电量 / 额定容量 × 100' })
      assert.equal(ctx.api.draft.value.name, '储能SOC计算规则（修订）')
      assert.equal(ctx.api.draft.value.description, '按统一统计范围计算储能设备剩余电量占比，含冰损修正。')
      assert.equal(ctx.api.draft.value.content, 'soc = 剩余电量 / 额定容量 × 100（统一口径）。')
      assert.ok(!('output' in ctx.api.draft.value), '白名单外字段不得落入草稿')
      assert.equal(ctx.formSave.saves.length, 0, '回填绝不触发表单保存（form-save 零调用）')
      assert.equal(ctx.changed(), 0, "回填绝不触发 'changed' emit")
      assert.equal(ctx.panel.status.value, 'done')
      assert.equal(ctx.panel.collapsed.value, true, '一次回填完成抽屉自动收起')
      const b = ctx.api.assistBinding.value
      assert.equal(b.round.statusBarText, '已填写 3 项，尚未保存')
      assert.equal(b.round.statusBarText, ctx.panel.statusBarText.value, '宿主镜像与引擎状态条口径一致')
      assert.equal(b.round.canUndo, true)
      const page = await ctx.html()
      assert.match(page, /已填写 3 项，尚未保存/)
      assert.match(page, /撤销本次填写/)
      assert.match(page, /查看修改/)
      assert.match(page, /规则名称<\/strong>：储能SOC计算规则 → 储能SOC计算规则（修订）/, '逐字段旧值→新值')
      const phtml = await ctx.panelHtml()
      assert.ok(!phtml.includes('assist-drawer'), 'done 后抽屉保持收起')
    })

    await check('④ 宿主撤销本次填写与手改禁撤销', async () => {
      const b = ctx.api.assistBinding.value
      assert.equal(b.undoRound(), true)
      assert.deepEqual(ctx.api.draft.value, { name: '储能SOC计算规则', description: '按统一统计范围计算储能设备剩余电量占比。', content: 'soc = 剩余电量 / 额定容量 × 100' }, '恢复本轮起点')
      assert.equal(ctx.formSave.saves.length, 0); assert.equal(ctx.changed(), 0)
      assert.equal(b.round.statusBarText, ''); assert.equal(b.round.undone, true)
      assert.equal(b.undoRound(), false, '一次性')

      // ③ 的整页渲染重建了面板实例（SSR 无 onMounted 自动 open）：显式重开（open 亦会展开抽屉）
      await ctx.panel.open(ctx.api.assistBinding.value)
      ctx.assist.fillQueue.push({ formId: 'rule', operations: [setOp('content', '第二次回填的规则内容。')] })
      await ctx.panel.generate()
      assert.equal(b.round.statusBarText, '已填写 1 项，尚未保存')
      ctx.api.onField('name', '手改规则名') // 模板 @update:model-value 的同一写路径
      assert.equal(ctx.api.assistTouchTick.value, 1, '字段输入事件触发手改通知')
      assert.equal(b.round.canUndo, false)
      assert.equal(b.undoRound(), false, '撤销被拒')
      assert.equal(ctx.api.draft.value.name, '手改规则名', '手改值保留')
      assert.equal(ctx.api.draft.value.content, '第二次回填的规则内容。', '已回填字段不被撤销覆盖')
      assert.match(b.round.undoHint, /已保留你的手动修改/)
      ctx.panel.notifyDraftChanged() // 宿主 ref 通道（浏览器里由 assistTouched 经模板 ref 调用）
      assert.ok(ctx.panel.undoHint.value.length > 0, '引擎侧同步禁撤销')
      const page = await ctx.html()
      assert.match(page, /已保留你的手动修改，本次自动填写不可直接撤销。/)
      assert.match(page, /<button[^>]*disabled/, '撤销按钮禁用')
    })

    await check('⑤ 规则历史 output 只读区不受自动填写影响（不在草稿白名单、回填不动只读区）', async () => {
      const ctx2 = mountLibrary(RuleLib)
      await ctx2.initial
      ctx2.api.openEdit('rule_2') // 该规则带历史 output
      ctx2.api.toggleAssist()
      let page = await ctx2.html()
      assert.match(page, /历史补充说明/, '历史 output 只读区照常展示')
      assert.match(page, /历史输出：折算系数 0\.95/)
      const b = ctx2.api.assistBinding.value
      assert.deepEqual(Object.keys(b.draft()), ['name', 'description', 'content'], '快照恰为三字段白名单，历史 output 不出网')
      await ctx2.panel.open(b)
      ctx2.assist.fillQueue.push({ formId: 'rule', operations: [setOp('content', '可用功率 = 额定功率 × 0.95')] })
      await ctx2.panel.generate()
      assert.equal(ctx2.api.draft.value.content, '可用功率 = 额定功率 × 0.95')
      assert.equal(ctx2.formSave.saves.length, 0)
      assert.equal(b.round.statusBarText, '已填写 1 项，尚未保存')
      assert.equal(b.undoRound(), true)
      assert.equal(ctx2.api.draft.value.content, '', '撤销恢复本轮起点')
      page = await ctx2.html()
      assert.match(page, /历史输出：折算系数 0\.95/, '历史 output 只读区原样保留')
    })

    await check('⑥ 切换编辑目标以新 binding 重开；关闭编辑器面板卸载', async () => {
      const ctx3 = mountLibrary(RuleLib)
      await ctx3.initial
      ctx3.api.openEdit('rule_1')
      ctx3.api.toggleAssist()
      await ctx3.html()
      assert.ok(ctx3.panel, '面板实例已创建')
      ctx3.api.openEdit('rule_2') // 直接切换编辑目标
      assert.equal(ctx3.api.assistOpen.value, true, '面板保持挂载')
      const nb = ctx3.api.assistBinding.value
      assert.equal(nb.targetKind, 'rule')
      assert.equal(nb.targetId, 'rule_2', 'binding 已指向新目标')
      assert.equal(nb.contextTitle, '维护「效率折算规则」的业务规则', '兜底标题随新目标更新')
      await ctx3.panel.open(nb)
      const last = ctx3.assist.calls.context.at(-1)
      assert.equal(last.targetId, 'rule_2')
      assert.equal(last.draft.name, '效率折算规则', '新目标的草稿快照')
      ctx3.api.closeEditor()
      assert.equal(ctx3.api.assistOpen.value, false, 'closeEditor 卸载面板')
      assert.equal(ctx3.api.assistBinding.value, null, '编辑器关闭后 binding 置空')
      const closed = await ctx3.html()
      assert.ok(!closed.includes('assist-drawer'), '卸载后不再渲染抽屉')
      assert.ok(!closed.includes('✦ 自动填写'), '列表态无 AI 入口')
    })
  }

  // ── ⑦~⑧ 动作编辑表单全链路 ──
  {
    const actx = mountLibrary(ActionLib)
    await actx.initial

    await check('⑦ 动作新建（DEF-02：预生成 id 但 targetId 空串）；回填只改草稿且零保存', async () => {
      actx.api.openNew()
      assert.match(actx.api.editId.value, /^action_/, 'openNew 已预生成动作 id')
      const b = actx.api.assistBinding.value
      assert.equal(b.targetKind, 'action'); assert.equal(b.formId, 'action')
      assert.equal(b.targetId, '', '新建动作未入库，targetId 按 DEF-02 传空串')
      let page = await actx.html()
      if (ASSIST_ENTRY_ENABLED) assert.match(page, /✦ 自动填写/, '动作编辑表单页头次要入口')
      else assert.ok(!page.includes('✦ 自动填写'), '功能开关关闭：动作编辑表单不渲染入口')
      assert.ok(!page.includes('assist-drawer'), '默认无 AI 区')
      actx.api.toggleAssist()
      await actx.html()
      await actx.panel.open(b)
      const body = actx.assist.calls.context.at(-1)
      assert.equal(body.space, 'ontology'); assert.equal(body.targetKind, 'action')
      assert.equal(body.targetId, '', '上下文请求按 DEF-02 携带空 targetId')
      assert.deepEqual(body.draft, { name: '', description: '', effect: '' })
      actx.assist.fillQueue.push({ formId: 'action', operations: [
        setOp('name', '停止充放电'),
        setOp('description', '请求目标对象停止当前充电或放电。'),
        setOp('effect', '设备退出充放电运行状态。'),
      ] })
      await actx.panel.generate()
      assert.equal(actx.assist.calls.generate[0].protocol, 2)
      assert.equal(actx.api.draft.value.name, '停止充放电')
      assert.equal(actx.api.draft.value.description, '请求目标对象停止当前充电或放电。')
      assert.equal(actx.api.draft.value.effect, '设备退出充放电运行状态。')
      assert.ok(!('input' in actx.api.draft.value), '白名单外字段不得落入草稿')
      assert.equal(actx.formSave.saves.length, 0, '回填绝不触发表单保存')
      assert.equal(actx.changed(), 0, "回填绝不触发 'changed' emit")
      assert.equal(b.round.statusBarText, '已填写 3 项，尚未保存')
      assert.equal(b.round.statusBarText, actx.panel.statusBarText.value)
      assert.equal(actx.panel.status.value, 'done'); assert.equal(actx.panel.collapsed.value, true)
      const phtml = await actx.panelHtml()
      assert.ok(!phtml.includes('assist-drawer'))
    })

    await check('⑧ 动作编辑已有：targetId=该 id、上下文携带既有草稿、setField 手改通知与禁撤销', async () => {
      actx.api.openEdit('action_1') // 从新建切到编辑既有动作
      assert.equal(actx.api.assistOpen.value, true, '面板保持挂载')
      const b = actx.api.assistBinding.value
      assert.equal(b.targetId, 'action_1', '编辑既有动作 targetId 为该动作 id')
      assert.equal(b.contextTitle, '维护「停止充放电」的动作定义', '兜底标题随目标更新')
      await actx.panel.open(b)
      const body = actx.assist.calls.context.at(-1)
      assert.equal(body.targetKind, 'action'); assert.equal(body.targetId, 'action_1')
      assert.deepEqual(body.draft, { name: '停止充放电', description: '请求目标对象停止当前充电或放电。', effect: '设备退出充放电运行状态。' })
      actx.assist.fillQueue.push({ formId: 'action', operations: [setOp('description', '急停指令：请求目标对象立即停止充放电。')] })
      await actx.panel.generate()
      assert.equal(actx.api.draft.value.description, '急停指令：请求目标对象立即停止充放电。')
      assert.equal(actx.formSave.saves.length, 0)
      assert.equal(b.round.statusBarText, '已填写 1 项，尚未保存')
      assert.equal(b.round.canUndo, true)
      assert.equal(b.undoRound(), true, '整轮撤销')
      assert.equal(actx.api.draft.value.description, '请求目标对象停止当前充电或放电。')

      actx.panel.expand()
      actx.assist.fillQueue.push({ formId: 'action', operations: [setOp('effect', '设备进入停机状态。')] })
      await actx.panel.generate()
      actx.api.setField('name', '手改动作名') // 模板 @update:model-value 的同一写路径
      assert.equal(actx.api.assistTouchTick.value, 1, '字段输入事件触发了手改通知')
      assert.equal(b.round.canUndo, false, '手改后禁整轮撤销')
      assert.equal(b.undoRound(), false)
      assert.equal(actx.api.draft.value.name, '手改动作名')
      assert.equal(actx.api.draft.value.effect, '设备进入停机状态。', '已回填字段不被撤销覆盖')
    })

    await check('⑧b 动作切换目标（切回新建）与关闭编辑器：面板重开、卸载', async () => {
      actx.api.openNew() // 切回新建（新的预生成 id）
      assert.equal(actx.api.assistOpen.value, true, '面板保持挂载')
      const newId = actx.api.editId.value
      assert.ok(newId !== 'action_1' && newId.startsWith('action_'), '新目标为另一个预生成 id')
      assert.equal(actx.api.assistBinding.value.targetId, '', '新建动作 targetId 为空串（DEF-02）')
      await actx.panel.open(actx.api.assistBinding.value)
      const last = actx.assist.calls.context.at(-1)
      assert.equal(last.targetId, '')
      assert.deepEqual(last.draft, { name: '', description: '', effect: '' }, '新建目标草稿为空')
      actx.api.closeEditor()
      assert.equal(actx.api.assistOpen.value, false, 'closeEditor 卸载面板')
      assert.equal(actx.api.assistBinding.value, null, '编辑器关闭后 binding 置空')
      const page = await actx.html()
      assert.ok(!page.includes('assist-drawer'), '卸载后不再渲染抽屉')
      assert.ok(!page.includes('✦ 自动填写'), '列表态无 AI 入口')
    })
  }

  // ── ⑨ 全程零保存兜底 ──
  await check('⑨ 全程零保存：回填/手改/切换均不落盘（form-save 与 changed 保持 0 次）', async () => {
    const r = mountLibrary(RuleLib)
    await r.initial
    r.api.openEdit('rule_1'); r.api.toggleAssist()
    await r.html() // 面板实例随本次渲染创建（SSR 无 onMounted 自动挂载）
    await r.panel.open(r.api.assistBinding.value)
    r.assist.fillQueue.push({ formId: 'rule', operations: [setOp('name', '零保存确认')] })
    await r.panel.generate()
    assert.equal(r.api.draft.value.name, '零保存确认')
    assert.equal(r.api.assistBinding.value.round.statusBarText, '已填写 1 项，尚未保存')
    assert.equal(r.formSave.saves.length, 0)
    assert.equal(r.changed(), 0)
  })
} finally {
  rmSync(root, { recursive: true, force: true })
}

const failed = results.filter(r => !r)
console.log(`\n统计：${results.length - failed.length}/${results.length} 项通过`)
if (failed.length) process.exit(1)
