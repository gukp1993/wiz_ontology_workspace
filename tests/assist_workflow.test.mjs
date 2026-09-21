// 业务规则（O4）与动作定义（O5）辅助填写接入测试（2026-09-21 T7）。运行（仓库根）：
//   node --import ./tests/ts_hooks.mjs tests/assist_workflow.test.mjs
// 编译真实 BusinessRuleLibrary.vue / ActionLibrary.vue 与 AssistPanel.vue（T3 状态机），
// 其余子组件桩化、无路由无服务、不发真实请求；assist api 经 provide('assist-api') 注入合成桩
// （T4 接线后的组件级验证方式，同 assist_object_workspace.test.mjs）。
// 覆盖：① binding 工厂（workflowBindings）白名单快照/undefined 不覆盖/白名单外忽略/快照恢复；
// ② 规则编辑打开 → 入口与面板出现、目标标题、上下文请求体（新建 targetId 空串）；
// ③ 采纳只改本地草稿且绝不触发表单保存 / 'changed' emit、出现「尚未保存」提示；
// ④ 手改后采纳被草稿指纹拦下、撤销失效、手改通知已发出；⑤ 规则历史 output 只读区不受辅助影响；
// ⑥ 规则切换编辑目标以新 binding 重开、关闭编辑器面板收起；⑦ 动作新建（预生成 id）targetId 非空；
// ⑧ 动作编辑已有（targetId=该 id）采纳同上、setField 手改通知；⑨ 动作切换目标与关闭收起。
// 已知边界：SSR 渲染不执行 onMounted（面板自动取上下文）与模板 ref 填充（notify 通道），
// 这两处由「驱动面板暴露的状态机 + 手改计数 + typecheck」覆盖，浏览器实链路留给独立验收。
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

// ── assist-api 桩：合成 context/generate；记录请求体；generate 按队列出栈 ─────────
const fieldSpec = (key, label, kind) => ({ key, label, kind, required: false, options: null, group: null, help: '' })
const FIELDS_BY_KIND = {
  rule: [fieldSpec('name', '规则名称', 'text'), fieldSpec('description', '业务定义', 'textarea'), fieldSpec('content', '规则内容', 'textarea')],
  action: [fieldSpec('name', '动作名称', 'text'), fieldSpec('description', '业务定义', 'textarea'), fieldSpec('effect', '预期效果', 'textarea')],
}
function makeAssistApi() {
  const calls = { context: [], generate: [] }
  const genQueue = []
  return {
    calls, genQueue,
    async context(body) {
      calls.context.push(JSON.parse(JSON.stringify(body)))
      return {
        contextToken: 'tok-' + calls.context.length,
        contextFingerprint: 'fp-' + calls.context.length,
        context: { targetKind: body.targetKind, title: 'CTX[' + body.targetKind + ':' + body.targetId + ']', editableFields: FIELDS_BY_KIND[body.targetKind] || [], definitions: [], catalog: [], flows: [], modelReady: true },
      }
    },
    async generate(body) {
      calls.generate.push(JSON.parse(JSON.stringify(body)))
      return Object.assign({ requestId: 'req-' + calls.generate.length, status: 'ok', contextFingerprint: 'fp-1', questions: [], suggestions: [], issues: [], explanation: null, meta: { durationMs: 5, provider: '测试桩', model: 'stub' } }, genQueue.shift() || {})
    },
  }
}

// ── form-save 间谍：被调用即记录（采纳后必须保持 0 次）──────────────────────────
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
  const propsObj = { state, onChanged: () => { changedCount++ } } // 'changed' emit 间谍（采纳后必须 0 次）
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
    // 用同一份 setup 状态重渲染页面（不重跑 setup，等价 assist_object_workspace 的 html() 手法）
    html: () => { activeSlot = slot; return renderToString(createSSRApp({ props: Comp.props, ssrRender: Comp.ssrRender, setup: () => slot.api }, propsObj)) },
    // 用驱动后的面板状态单独渲染面板（验证横幅/标题等 UI 呈现）
    panelHtml: () => renderToString(createSSRApp({ props: Panel.props, ssrRender: Panel.ssrRender, setup: () => slot.panel }, { binding: slot.api.assistBinding.value })),
  }
}

const results = []
async function check(name, fn) { try { await fn(); results.push(true); console.log('通过：' + name) } catch (e) { results.push(false); console.log('失败：' + name + ' — ' + (e && e.message)) } }

try {
  // ── ① binding 工厂（白名单、合并、快照恢复）──
  {
    const { ruleAssistBinding, actionAssistBinding } = await import('../frontend/src/assist/workflowBindings.ts')
    await check('① binding 工厂：白名单快照、undefined 不覆盖、白名单外忽略、快照就地恢复', async () => {
      const d = { name: '储能SOC计算规则', description: '旧定义', content: '旧内容' }
      const b = ruleAssistBinding(d, { targetId: 'rule_1', contextTitle: '标题T' })
      assert.equal(b.space, 'ontology'); assert.equal(b.targetKind, 'rule'); assert.equal(b.targetId, 'rule_1'); assert.equal(b.contextTitle, '标题T')
      assert.deepEqual(Object.keys(b.draft()), ['name', 'description', 'content'], '快照只含白名单键（历史 output 等不出网）')
      assert.deepEqual(b.draft(), { name: '储能SOC计算规则', description: '旧定义', content: '旧内容' })
      b.apply({ name: '新名称', description: undefined, extraKey: '越权', nested: { a: 1 }, content: '新内容' })
      assert.equal(d.name, '新名称')
      assert.equal(d.description, '旧定义', 'undefined 值不得覆盖既有草稿')
      assert.ok(!('extraKey' in d) && !('nested' in d), '白名单外键不得落入草稿')
      assert.equal(d.content, '新内容')
      const snap = b.snapshot()
      assert.deepEqual(snap, { name: '新名称', description: '旧定义', content: '新内容' })
      d.name = '手改'
      assert.deepEqual(snap, { name: '新名称', description: '旧定义', content: '新内容' }, '快照是 JSON 克隆，不随草稿后续修改漂移')
      const refBefore = d
      b.restore(snap)
      assert.ok(d === refBefore, 'restore 必须就地恢复（宿主草稿引用保持稳定）')
      assert.equal(d.name, '新名称'); assert.equal(d.description, '旧定义')

      const ad = { name: '', description: '', effect: '' }
      const ab = actionAssistBinding(ad) // 缺省：targetId ''、兜底标题非空
      assert.equal(ab.targetKind, 'action'); assert.equal(ab.targetId, ''); assert.ok(ab.contextTitle.length > 0)
      assert.deepEqual(Object.keys(ab.draft()), ['name', 'description', 'effect'])
      ab.apply({ effect: '设备退出充放电运行状态。', name: '停止充放电', input: '越权' })
      assert.equal(ad.effect, '设备退出充放电运行状态。'); assert.equal(ad.name, '停止充放电')
      assert.ok(!('input' in ad), '动作白名单外键忽略')
      const asnap = ab.snapshot(); ad.description = 'x'; ab.restore(asnap); assert.equal(ad.description, '')

      const er = ruleAssistBinding(null), ea = actionAssistBinding(undefined) // 空草稿兜底：不抛错
      assert.deepEqual(er.draft(), { name: '', description: '', content: '' })
      assert.deepEqual(ea.draft(), { name: '', description: '', effect: '' })
      er.apply({ name: 'x' }); er.restore({}); ea.apply({ effect: 'y' }); ea.restore({}); assert.ok(true, '空草稿 apply/restore 安全')
    })
  }

  // ── ②~⑥ 规则编辑表单全链路（共用同一份编辑状态）──
  {
    const ctx = mountLibrary(RuleLib)
    await ctx.initial

    await check('② 规则编辑打开（新建）：入口存在、面板出现且兜底标题正确；新建 targetId 为空串', async () => {
      ctx.api.openEdit() // 新建：isNewRule=true
      assert.equal(ctx.api.isNewRule.value, true)
      assert.equal(ctx.api.assistBinding.value.targetKind, 'rule')
      assert.equal(ctx.api.assistBinding.value.targetId, '', '新建规则尚未入库，targetId 传空串')
      let page = await ctx.html()
      assert.match(page, /✦ 辅助填写/, '编辑表单头应有辅助入口')
      assert.ok(!page.includes('assist-panel'), '未点击前不渲染面板')
      ctx.api.toggleAssist() // 与按钮 @click 同一函数
      page = await ctx.html()
      assert.match(page, /assist-panel/, '点击后面板出现')
      assert.match(page, /新建业务规则/, '未取到上下文时展示目标兜底标题')
      assert.ok(ctx.panel, '面板实例已随页面创建')
      ctx.api.toggleAssist()
      page = await ctx.html()
      assert.ok(!page.includes('assist-panel'), '再次点击收起面板')
    })

    await check('②b 切到编辑既有规则：目标标题正确；上下文请求携带场景与白名单草稿', async () => {
      ctx.api.toggleAssist() // 重新展开（openEdit 不收起面板，G2 同款）
      ctx.api.openEdit('rule_1')
      assert.equal(ctx.api.assistOpen.value, true, '切换目标后面板保持展开')
      assert.equal(ctx.api.assistBinding.value.targetId, 'rule_1')
      const page = await ctx.html()
      assert.match(page, /维护「储能SOC计算规则」的业务规则/, '未取到上下文时展示目标兜底标题')
      assert.ok(ctx.panel && ctx.panel !== null, '面板实例随重渲染存在')
      await ctx.panel.open(ctx.api.assistBinding.value)
      assert.equal(ctx.assist.calls.context.length, 1)
      const body = ctx.assist.calls.context[0]
      assert.equal(body.space, 'ontology'); assert.equal(body.targetKind, 'rule')
      assert.equal(body.targetId, 'rule_1'); assert.equal(body.purpose, 'fill')
      assert.deepEqual(body.draft, { name: '储能SOC计算规则', description: '按统一统计范围计算储能设备剩余电量占比。', content: 'soc = 剩余电量 / 额定容量 × 100' }, 'draft 是白名单形态快照')
      const phtml = await ctx.panelHtml()
      assert.match(phtml, /CTX\[rule:rule_1\]/, '取到上下文后面板标题切换为服务端标题')
    })

    await check('③ 采纳建议：draft 更新；form-save 与 changed emit 零调用；出现「尚未保存」提示', async () => {
      ctx.assist.genQueue.push({ suggestions: [
        { id: 's1', label: '规则名称与业务定义', fieldKeys: ['name', 'description'], proposed: { name: '储能SOC计算规则（修订）', description: '新业务定义。', output: '越权字段不应落入' }, state: 'ready' },
        { id: 's2', label: '规则内容', fieldKeys: ['content'], proposed: { content: 'soc = 剩余电量 / 额定容量 × 100（统一口径）。' }, state: 'ready' },
      ] })
      await ctx.panel.generate()
      assert.equal(ctx.assist.calls.generate.length, 1)
      assert.equal(ctx.assist.calls.generate[0].contextToken, 'tok-1')
      assert.deepEqual(ctx.assist.calls.generate[0].draft, { name: '储能SOC计算规则', description: '按统一统计范围计算储能设备剩余电量占比。', content: 'soc = 剩余电量 / 额定容量 × 100' }, 'generate 携带当前草稿')
      assert.equal(ctx.panel.adopt(), true)
      assert.equal(ctx.api.draft.value.name, '储能SOC计算规则（修订）')
      assert.equal(ctx.api.draft.value.description, '新业务定义。')
      assert.equal(ctx.api.draft.value.content, 'soc = 剩余电量 / 额定容量 × 100（统一口径）。')
      assert.ok(!('output' in ctx.api.draft.value), '白名单外建议值不得落入草稿')
      assert.equal(ctx.formSave.saves.length, 0, '采纳绝不触发表单保存（form-save 零调用）')
      assert.equal(ctx.changed(), 0, "采纳绝不触发 'changed' emit")
      assert.equal(ctx.panel.canUndo.value, true)
      assert.equal(ctx.panel.justAdopted.value, true)
      const phtml = await ctx.panelHtml()
      assert.match(phtml, /已填入表单，尚未保存/, '面板提示尚未保存')
    })

    await check('④ 规则手改字段：手改通知已发出；采纳被草稿指纹拦下；撤销失效', async () => {
      ctx.api.onField('name', '手改名称')
      assert.equal(ctx.api.draft.value.name, '手改名称')
      assert.equal(ctx.api.assistTouchTick.value, 1, '字段输入事件触发了手改通知')
      assert.equal(ctx.panel.adopt(), false, '手改后草稿指纹漂移，采纳被拒（不写入建议值）')
      assert.equal(ctx.api.draft.value.name, '手改名称', '被拒的采纳不得改动草稿')
      assert.equal(ctx.panel.stale.value, true)
      assert.equal(ctx.panel.canUndo.value, false, '手改后撤销保护解除')
      assert.equal(ctx.panel.canAdopt.value, false, '采纳按钮禁用')
      assert.equal(ctx.panel.undo(), false, '撤销不可用')
      ctx.api.onField('description', '手改定义')
      ctx.panel.notifyDraftChanged() // 宿主 ref 通道（浏览器里由 assistTouched 经模板 ref 调用）
      assert.equal(ctx.panel.justAdopted.value, false, '手改清除「已填入」提示')
      const phtml = await ctx.panelHtml()
      assert.match(phtml, /建议已失效：表单已修改/, '面板展示过期横幅')
    })

    await check('⑤ 规则历史 output 只读区不受辅助影响（不在草稿白名单、采纳不动只读区）', async () => {
      const ctx2 = mountLibrary(RuleLib)
      await ctx2.initial
      ctx2.api.openEdit('rule_2') // 该规则带历史 output
      ctx2.api.toggleAssist()
      let page = await ctx2.html()
      assert.match(page, /历史补充说明/, '历史 output 只读区照常展示')
      assert.match(page, /历史输出：折算系数 0\.95/)
      assert.deepEqual(Object.keys(ctx2.api.assistBinding.value.draft()), ['name', 'description', 'content'], '快照恰为三字段白名单，历史 output 不出网')
      await ctx2.panel.open(ctx2.api.assistBinding.value)
      ctx2.assist.genQueue.push({ suggestions: [
        { id: 'c1', label: '规则内容', fieldKeys: ['content'], proposed: { content: '可用功率 = 额定功率 × 0.95' }, state: 'ready' },
      ] })
      await ctx2.panel.generate()
      assert.equal(ctx2.panel.adopt(), true)
      assert.equal(ctx2.api.draft.value.content, '可用功率 = 额定功率 × 0.95')
      assert.equal(ctx2.formSave.saves.length, 0, '采纳绝不触发表单保存')
      page = await ctx2.html()
      assert.match(page, /历史输出：折算系数 0\.95/, '采纳后历史 output 只读区原样保留')
    })

    await check('⑥ 规则切换编辑目标：面板以新 binding 重开；关闭编辑器面板收起', async () => {
      const ctx3 = mountLibrary(RuleLib)
      await ctx3.initial
      ctx3.api.openEdit('rule_1')
      ctx3.api.toggleAssist()
      let page = await ctx3.html()
      assert.match(page, /assist-panel/, '面板已展开')
      ctx3.api.openEdit('rule_2') // 直接切换编辑目标
      assert.equal(ctx3.api.assistOpen.value, true, '面板保持展开')
      assert.equal(ctx3.api.assistBinding.value.targetKind, 'rule')
      assert.equal(ctx3.api.assistBinding.value.targetId, 'rule_2', 'binding 已指向新目标')
      page = await ctx3.html()
      assert.match(page, /维护「效率折算规则」的业务规则/, '面板标题随新目标更新')
      await ctx3.panel.open(ctx3.api.assistBinding.value)
      const last = ctx3.assist.calls.context.at(-1)
      assert.equal(last.targetId, 'rule_2')
      assert.equal(last.draft.name, '效率折算规则', '新目标的草稿快照')
      ctx3.api.closeEditor()
      assert.equal(ctx3.api.assistOpen.value, false, 'closeEditor 收起面板')
      assert.equal(ctx3.api.assistBinding.value, null, '编辑器关闭后 binding 置空')
      page = await ctx3.html()
      assert.ok(!page.includes('assist-panel'), '收起后不再渲染面板')
      assert.ok(!page.includes('辅助填写'), '列表态无辅助入口')
    })
  }

  // ── ⑦~⑨ 动作编辑表单全链路 ──
  {
    const actx = mountLibrary(ActionLib)
    await actx.initial

    await check('⑦ 动作新建（预生成 id）：targetId 非空；采纳只改草稿且零保存', async () => {
      actx.api.openNew()
      assert.match(actx.api.editId.value, /^action_/, 'openNew 已预生成动作 id')
      assert.equal(actx.api.assistBinding.value.targetKind, 'action')
      assert.equal(actx.api.assistBinding.value.targetId, actx.api.editId.value, '预生成 id 直接作为面板 targetId')
      assert.ok(actx.api.assistBinding.value.targetId !== '', 'targetId 非空')
      let page = await actx.html()
      assert.match(page, /✦ 辅助填写/, '动作编辑表单头应有辅助入口')
      actx.api.toggleAssist()
      page = await actx.html()
      assert.match(page, /assist-panel/)
      assert.match(page, /新建动作定义/, '未取到上下文时展示目标兜底标题')
      await actx.panel.open(actx.api.assistBinding.value)
      const body = actx.assist.calls.context.at(-1)
      assert.equal(body.space, 'ontology'); assert.equal(body.targetKind, 'action')
      assert.equal(body.targetId, actx.api.editId.value, '上下文请求携带预生成 id')
      assert.deepEqual(body.draft, { name: '', description: '', effect: '' })
      actx.assist.genQueue.push({ suggestions: [
        { id: 'a1', label: '动作名称与业务定义', fieldKeys: ['name', 'description'], proposed: { name: '停止充放电', description: '请求目标对象停止当前充电或放电。', input: '越权字段不应落入' }, state: 'ready' },
        { id: 'a2', label: '预期效果', fieldKeys: ['effect'], proposed: { effect: '设备退出充放电运行状态。' }, state: 'ready' },
      ] })
      await actx.panel.generate()
      assert.equal(actx.panel.adopt(), true)
      assert.equal(actx.api.draft.value.name, '停止充放电')
      assert.equal(actx.api.draft.value.description, '请求目标对象停止当前充电或放电。')
      assert.equal(actx.api.draft.value.effect, '设备退出充放电运行状态。')
      assert.ok(!('input' in actx.api.draft.value), '白名单外建议值不得落入草稿')
      assert.equal(actx.formSave.saves.length, 0, '采纳绝不触发表单保存')
      assert.equal(actx.changed(), 0, "采纳绝不触发 'changed' emit")
      assert.equal(actx.panel.canUndo.value, true)
      const phtml = await actx.panelHtml()
      assert.match(phtml, /已填入表单，尚未保存/)
    })

    await check('⑧ 动作编辑已有：targetId=该 id；上下文携带既有草稿；采纳与手改通知同规则', async () => {
      actx.api.openEdit('action_1') // 从新建切到编辑既有动作
      assert.equal(actx.api.assistOpen.value, true, '面板保持展开')
      assert.equal(actx.api.assistBinding.value.targetId, 'action_1', '编辑既有动作 targetId 为该动作 id')
      const page = await actx.html()
      assert.match(page, /维护「停止充放电」的动作定义/, '面板标题随目标更新')
      await actx.panel.open(actx.api.assistBinding.value)
      const body = actx.assist.calls.context.at(-1)
      assert.equal(body.targetKind, 'action'); assert.equal(body.targetId, 'action_1')
      assert.deepEqual(body.draft, { name: '停止充放电', description: '请求目标对象停止当前充电或放电。', effect: '设备退出充放电运行状态。' })
      actx.assist.genQueue.push({ suggestions: [
        { id: 'a3', label: '业务定义', fieldKeys: ['description'], proposed: { description: '急停指令：请求目标对象立即停止充放电。' }, state: 'ready' },
      ] })
      await actx.panel.generate()
      assert.equal(actx.panel.adopt(), true)
      assert.equal(actx.api.draft.value.description, '急停指令：请求目标对象立即停止充放电。')
      assert.equal(actx.formSave.saves.length, 0, '采纳绝不触发表单保存')
      actx.api.setField('name', '手改动作名') // 模板 @update:model-value 的同一写路径
      assert.equal(actx.api.assistTouchTick.value, 1, '字段输入事件触发了手改通知')
      assert.equal(actx.panel.adopt(), false, '手改后采纳被草稿指纹拦下')
      assert.equal(actx.api.draft.value.name, '手改动作名')
      assert.equal(actx.panel.stale.value, true)
      assert.equal(actx.panel.canUndo.value, false)
    })

    await check('⑨ 动作切换目标（切回新建）与关闭编辑器：面板重开、收起', async () => {
      actx.api.openNew() // 切回新建（新的预生成 id）
      assert.equal(actx.api.assistOpen.value, true, '面板保持展开')
      const newId = actx.api.editId.value
      assert.ok(newId !== 'action_1' && newId.startsWith('action_'), '新目标为另一个预生成 id')
      assert.equal(actx.api.assistBinding.value.targetId, newId)
      await actx.panel.open(actx.api.assistBinding.value)
      const last = actx.assist.calls.context.at(-1)
      assert.equal(last.targetId, newId)
      assert.deepEqual(last.draft, { name: '', description: '', effect: '' }, '新建目标草稿为空')
      actx.api.closeEditor()
      assert.equal(actx.api.assistOpen.value, false, 'closeEditor 收起面板')
      assert.equal(actx.api.assistBinding.value, null, '编辑器关闭后 binding 置空')
      const page = await actx.html()
      assert.ok(!page.includes('assist-panel'), '收起后不再渲染面板')
      assert.ok(!page.includes('辅助填写'), '列表态无辅助入口')
    })
  }

  await check('⑩ 全程零保存：两表单 form-save 间谍保持 0 次（采纳/手改/切换均不落盘）', async () => {
    // 上两个用例的 ctx/actx 已各自累计；此处用全新挂载快速再走一遍「打开+采纳」最小链路兜底确认。
    const r = mountLibrary(RuleLib)
    await r.initial
    r.api.openEdit('rule_1'); r.api.toggleAssist()
    await r.html() // 面板实例随本次渲染创建（SSR 无 onMounted 自动挂载）
    await r.panel.open(r.api.assistBinding.value)
    r.assist.genQueue.push({ suggestions: [{ id: 'z1', label: '名称', fieldKeys: ['name'], proposed: { name: '零保存确认' }, state: 'ready' }] })
    await r.panel.generate()
    assert.equal(r.panel.adopt(), true)
    assert.equal(r.formSave.saves.length, 0)
    assert.equal(r.changed(), 0)
  })
} finally {
  rmSync(root, { recursive: true, force: true })
}

const failed = results.filter(r => !r)
console.log(`\n统计：${results.length - failed.length}/${results.length} 项通过`)
if (failed.length) process.exit(1)
