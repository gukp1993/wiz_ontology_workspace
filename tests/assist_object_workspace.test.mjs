// 对象/链接编辑器整表自动填写接入测试（2026-09-22 T5 改版，覆盖 O1/O3）。运行（仓库根）：
//   node --import ./tests/ts_hooks.mjs tests/assist_object_workspace.test.mjs
// 编译真实 ObjectWorkspace.vue 与 AssistPanel.vue（其余子组件桩化、无路由无服务、不发真实请求）；
// assist api 经 provide('assist-api') 注入合成桩（autofill/1 fill 响应内存队列）。
// 覆盖（新交互，旧「建议卡→勾选→采纳」断言随改版移除）：
//   ① binding 工厂新对接面：formId/contractInfo（生成物指纹）/白名单 draft（契约 fieldId 恒等）/
//      applyDraft 合并零丢失/快照钩子开撤销单元/undoRound 一次性/手改禁撤销；
//   ② A01 默认无 AI 区：页头次要按钮「✦ 自动填写」（aria-expanded/aria-haspopup），点开才挂面板；
//   ③ 上下文请求携带场景与契约形态草稿（purpose=fill）；
//   ④ 生成→直接回填（无勾选步骤）：draft 变化、状态条「已填写 N 项，尚未保存」、逐字段旧值→新值、
//      form-save/changed 零调用（回填绝不保存）；
//   ⑤ 宿主撤销：恢复本轮开始前草稿、一次性、状态条清除；
//   ⑥ 手改禁撤销：undoRound 拒绝、草稿保留手改值、状态条说明；
//   ⑦ 续轮（补答）：已填 N 项另有 M 项待补充、整轮（首轮+续轮）一次撤销；
//   ⑧ 切目标作废：在途生成切目标后零写入、binding 以新目标重开；
//   ⑨ 契约指纹不符（CONTEXT_STALE）与 empty：零写入、镜像不显示；
//   ⑩ 链接编辑器同链路；⑪ 显式保存链路不受影响。
// 已知边界：SSR 渲染不执行 onMounted（面板自动取上下文）与模板 ref 填充（notify/toggle 通道），
// 由「驱动面板暴露的状态机 + 手改计数 + typecheck」覆盖，浏览器实链路留给独立验收。
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
if (!globalThis.crypto?.randomUUID) Object.defineProperty(globalThis, 'crypto', { value: require('node:crypto').webcrypto, configurable: true })

const root = mkdtempSync(join(tmpdir(), 'wiz_assist_ow_'))

/** 编译真实 SFC（script setup + SSR 模板）。kept：{ 组件名: globalThis 变量名 }，命中的 .vue 导入
 *  改挂到共享变量（用于把真实 AssistPanel 塞进 ObjectWorkspace），其余 .vue 一律桩成 render:null。 */
async function loadSFC(absPath, id, kept = {}) {
  const { descriptor } = parse(readFileSync(absPath, 'utf8'), { filename: absPath })
  const script = compileScript(descriptor, { id })
  let code = script.content
  for (const [name, globalName] of Object.entries(kept)) {
    code = code.replace(new RegExp('import\\s+' + name + '\\s+from\\s*[\'"][^\'"]+\\.vue[\'"]'), 'const ' + name + ' = globalThis.' + globalName)
  }
  code = code.replace(/import\s+PickerDialog\s*,\s*\{\s*type\s+PickerRow\s*\}\s*from\s*['"][^'"]+['"]/, 'const PickerDialog = { render: () => null }')
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

// 先编译真实面板，再编译 ObjectWorkspace（其 AssistPanel 导入指向真实面板）。
const Panel = await loadSFC(resolve('frontend/src/assist/AssistPanel.vue'), 'assist_panel_real')
globalThis.__assistPanelComp = Panel
const Component = await loadSFC(resolve('frontend/src/ontology/ObjectWorkspace.vue'), 'object_ws_assist', { AssistPanel: '__assistPanelComp' })

const { FORM_SCHEMA_DIGESTS } = await import('../frontend/src/assist/formContracts.gen.ts')
const { ASSIST_ENTRY_ENABLED } = await import('../frontend/src/assist/useAssistPanel.ts')

// ── 捕获每次挂载的 setup 状态（多场景互不串线）────────────────────────────────
let activeSlot = null
const origSetup = Component.setup
Component.setup = (props, ctx) => { const api = origSetup(props, ctx); if (activeSlot) activeSlot.api = api; return api }
const origPanelSetup = Panel.setup
Panel.setup = (props, ctx) => { const s = origPanelSetup(props, ctx); if (activeSlot) activeSlot.panel = s; return s }

// ── assist-api 桩：autofill/1 fill 响应内存队列；context/generate 记录请求体 ──────
const setOp = (field, value) => ({ op: 'set', field, value, basis: { kind: 'intent', quote: '测试依据' } })
function makeAssistApi() {
  const calls = { context: [], generate: [] }
  const fillQueue = [] // 队列元素：fill 响应覆盖项（含 formId/operations/questions/…）；'hang' = 永不返回
  let n = 0
  const lastTarget = { targetKind: 'object', targetId: '' } // 服务端会话绑定最近一次 context 的目标（回显用）
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
      if (over === 'hang') return new Promise(() => {}) // 模拟在途请求：切目标/关面板后迟到即作废
      if (!over) throw Object.assign(new Error('测试桩未编排该请求'), { data: { code: 'UNPLANNED' } })
      n++
      const formId = over.formId || 'object'
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

// ── form-save 间谍：被调用即记录并执行落盘回调（回填后必须保持 0 次；显式保存恰好 1 次）──
function makeFormSaveSpy() {
  const saves = []
  return { saves, async submitForm(area, mutate) { saves.push(area); if (typeof mutate === 'function') mutate(); return { ok: true, message: '' } } }
}

function graphFixture() {
  return [
    { '@id': 'mg:cluster', '@type': 'owl:Class', 'rdfs:label': '储能簇', 'rdfs:comment': '一组电池簇' },
    { '@id': 'mg:site', '@type': 'owl:Class', 'rdfs:label': '储能站', 'rdfs:comment': '场站' },
    { '@id': 'mg:l_belong', '@type': 'owl:ObjectProperty', 'rdfs:label': '所属场站', 'rdfs:domain': { '@id': 'mg:cluster' }, 'rdfs:range': { '@id': 'mg:site' }, 'mg:cardinality': 'many-to-one', 'mg:reverseLabel': '包含储能簇' },
  ]
}

function mountWorkspace() {
  const slot = { api: null, panel: null }
  const state = reactive({ ontology: { '@graph': graphFixture() }, workflow: {}, layout: {} })
  const formSave = makeFormSaveSpy()
  const assist = makeAssistApi()
  const app = createSSRApp(Component, { state })
  app.provide('form-guard', { register() {}, unregister() {}, hasDirty: () => false, editing: () => false })
  app.provide('form-save', formSave)
  app.provide('assist-api', assist)
  activeSlot = slot
  const initial = renderToString(app)
  return {
    state, formSave, assist, initial,
    get api() { return slot.api },
    get panel() { return slot.panel },
    // 用同一份 setup 状态重渲染页面（面板实例随每次渲染重建；整页断言只看宿主状态条/入口按钮）
    html: () => { activeSlot = slot; return renderToString(createSSRApp({ props: Component.props, ssrRender: Component.ssrRender, setup: () => slot.api }, { state })) },
    // 用驱动后的面板状态单独渲染抽屉（验证标题/横幅/问题卡等 UI 呈现）
    panelHtml: () => renderToString(createSSRApp({ props: Panel.props, ssrRender: Panel.ssrRender, setup: () => slot.panel }, { binding: slot.api.assistBinding.value })),
  }
}

const results = []
async function check(name, fn) { try { await fn(); results.push(true); console.log('通过：' + name) } catch (e) { results.push(false); console.log('失败：' + name + ' — ' + (e && e.message)) } }

try {
  // ── ① binding 工厂（新对接面：formId/contractInfo/applyDraft/撤销单元/手改禁撤销）──
  {
    const { objectAssistBinding, linkAssistBinding } = await import('../frontend/src/assist/ontologyBindings.ts')
    await check('① 对象 binding：formId/契约指纹、白名单 draft（契约 fieldId）、applyDraft 合并零丢失并镜像、撤销单元、手改禁撤销', async () => {
      const d = { label: '储能簇', comment: '旧定义' }
      const b = objectAssistBinding(d, { targetId: 'mg:cluster', contextTitle: '标题T' })
      assert.equal(b.space, 'ontology'); assert.equal(b.targetKind, 'object'); assert.equal(b.targetId, 'mg:cluster'); assert.equal(b.contextTitle, '标题T')
      assert.equal(b.formId, 'object', 'binding 声明 autofill/1 表单契约 id')
      assert.deepEqual(b.contractInfo(), { schemaVersion: 1, schemaDigest: FORM_SCHEMA_DIGESTS.object }, 'contractInfo 取自前端契约生成物')
      assert.deepEqual(Object.keys(b.draft()), ['label', 'comment'], 'draft 即契约形态标准化草稿（fieldId 恒等映射）')
      assert.deepEqual(b.draft(), { label: '储能簇', comment: '旧定义' })
      assert.deepEqual(b.codecs, {}, '对象表单无复杂 codec：普通字段 identity 直写')

      // 快照钩子 = 引擎撤销单元起点；applyDraft = 引擎整稿落回（含镜像记录）
      const snap = b.snapshot()
      assert.deepEqual(snap, { label: '储能簇', comment: '旧定义' })
      assert.equal(b.round.canUndo, false, '仅快照未回填：尚不可撤销')
      b.applyDraft({ label: '新名称', comment: '旧定义' })
      assert.equal(d.label, '新名称'); assert.equal(d.comment, '旧定义')
      assert.ok(!('extra' in d), '落回只含契约键，白名单外不落入草稿')
      assert.equal(b.round.appliedCount, 1, '已填写 1 项（顶层实际改变数）')
      assert.equal(b.round.canUndo, true)
      assert.equal(b.round.statusBarText, '已填写 1 项，尚未保存')
      assert.deepEqual(b.round.changes.map(c => [c.field, c.label, c.oldText, c.newText]), [['label', '对象名称', '储能簇', '新名称']], '逐字段 旧值→新值（契约标签）')

      // 宿主整轮撤销：恢复本轮开始前草稿，一次性
      assert.equal(b.undoRound(), true)
      assert.equal(d.label, '储能簇', '撤销恢复本轮起点')
      assert.equal(b.round.statusBarText, ''); assert.equal(b.round.undone, true)
      assert.equal(b.undoRound(), false, '一次性：撤销后不可再撤')

      // 手改禁撤销：undoRound 拒绝且不覆盖手改
      b.applyDraft({ label: 'A' })
      b.noteManualChange()
      assert.equal(b.round.canUndo, false); assert.match(b.round.undoHint, /手动修改/)
      assert.equal(b.undoRound(), false, '手改后整轮撤销被拒')
      assert.equal(d.label, 'A', '被拒的撤销不得改动草稿')
      // 手改后的续轮落回不再产生可撤销快照（不覆盖用户改动）
      b.applyDraft({ comment: 'B' })
      assert.equal(b.round.canUndo, false); assert.equal(b.round.appliedCount, 2)
      assert.equal(b.undoRound(), false)

      // 空草稿兜底
      const eb = objectAssistBinding(null)
      assert.deepEqual(eb.draft(), { label: '', comment: '' }); assert.equal(eb.undoRound(), false)
      eb.applyDraft({ label: 'x' }); eb.observeFill(null); assert.ok(true, '空草稿安全')
    })

    await check('①b 链接 binding：formId/契约指纹、六字段白名单、from/to/cardinality 直写、observeFill 按 formId/target/指纹过滤', async () => {
      const ld = { label: '所属场站', from: 'mg:cluster', to: 'mg:site', cardinality: 'many-to-one', reverseLabel: '', comment: '' }
      const lb = linkAssistBinding(ld) // 缺省：targetId ''、兜底标题非空
      assert.equal(lb.targetKind, 'link'); assert.equal(lb.targetId, ''); assert.equal(lb.formId, 'link')
      assert.deepEqual(lb.contractInfo(), { schemaVersion: 1, schemaDigest: FORM_SCHEMA_DIGESTS.link })
      assert.deepEqual(Object.keys(lb.draft()), ['label', 'from', 'to', 'cardinality', 'reverseLabel', 'comment'])
      lb.snapshot()
      lb.applyDraft({ label: '接入储能站', reverseLabel: '包含储能簇' })
      assert.equal(ld.label, '接入储能站'); assert.equal(ld.reverseLabel, '包含储能簇')
      assert.equal(ld.from, 'mg:cluster', '未提及字段逐字保留')
      assert.equal(lb.round.statusBarText, '已填写 2 项，尚未保存')
      assert.equal(lb.undoRound(), true); assert.equal(ld.label, '所属场站')

      // observeFill：只接本表单、本目标、契约指纹一致的 fill 响应
      const ok = over => Object.assign({ protocol: 'autofill/1', status: 'ok', formId: 'link', schemaVersion: 1, schemaDigest: FORM_SCHEMA_DIGESTS.link, target: { space: 'ontology', targetKind: 'link', targetId: '' }, questions: [{ id: 'q1' }], unresolved: [] }, over)
      lb.observeFill(ok({}))
      assert.equal(lb.round.pendingCount, 1, '待补充 = questions + unresolved')
      lb.observeFill(ok({ formId: 'object' })); assert.equal(lb.round.pendingCount, 1, 'formId 不符不计')
      lb.observeFill(ok({ target: { space: 'ontology', targetKind: 'link', targetId: 'mg:x' } })); assert.equal(lb.round.pendingCount, 1, '目标不符不计')
      lb.observeFill(ok({ schemaDigest: 'bad' })); assert.equal(lb.round.pendingCount, 1, '契约指纹不符不计（引擎会按 CONTEXT_STALE 拒绝）')
      lb.observeFill(ok({ status: 'empty', questions: [] })); assert.equal(lb.round.pendingCount, 0, 'empty 清空待补充')
      lb.observeFill({ protocol: undefined }); assert.ok(true, 'check/explain 帮助响应安全忽略')
    })
  }

  // ── ②~⑧ 对象编辑器全链路（共用同一份编辑状态）──
  {
    const ctx = mountWorkspace()
    await ctx.initial
    ctx.api.selected.value = 'mg:cluster'
    ctx.api.openObjectEditor(false)

    await check('② A01 默认无 AI 区：页头次要按钮「✦ 自动填写」（aria-expanded/aria-haspopup），未点开不渲染抽屉', async () => {
      let page = await ctx.html()
      if (ASSIST_ENTRY_ENABLED) {
        assert.match(page, /✦ 自动填写/, '页头次要入口存在')
        assert.match(page, /aria-expanded="false"/)
        assert.match(page, /aria-haspopup="dialog"/)
        assert.ok(page.includes('id="ow-assist-trigger-object"'), '入口按钮 id（面板 triggerId 指向它）')
      } else {
        assert.ok(!page.includes('✦ 自动填写'), '功能开关关闭：页头不渲染自动填写入口')
      }
      assert.ok(!page.includes('assist-drawer'), '默认不渲染任何 AI 区域')
      assert.ok(!page.includes('建议'), '旧建议卡 UI 不再出现')
      ctx.api.toggleAssist() // 与按钮 @click 同一函数：首次点击挂载面板
      page = await ctx.html()
      if (ASSIST_ENTRY_ENABLED) assert.match(page, /aria-expanded="true"/)
      assert.ok(ctx.panel, '面板实例已随页面创建')
      assert.equal(ctx.panel.checked, undefined, '旧勾选/采纳 API 已移除')
      assert.equal(ctx.panel.adopt, undefined); assert.equal(ctx.panel.setChecked, undefined)
      // SSR 不执行 onMounted（自动 open）；直接驱动面板状态机取上下文并展开
      await ctx.panel.open(ctx.api.assistBinding.value)
      const phtml = await ctx.panelHtml()
      assert.match(phtml, /assist-drawer/, '点开后抽屉出现')
      assert.match(phtml, /CTX\[object:mg:cluster\]/, '目标标题（服务端上下文）')
    })

    await check('③ 上下文请求：space/targetKind/targetId/purpose=fill 与契约形态草稿', async () => {
      const body = ctx.assist.calls.context.at(-1)
      assert.equal(body.space, 'ontology'); assert.equal(body.targetKind, 'object')
      assert.equal(body.targetId, 'mg:cluster'); assert.equal(body.purpose, 'fill')
      assert.deepEqual(body.draft, { label: '储能簇', comment: '一组电池簇' }, 'draft 是契约形态快照（出网白名单）')
    })

    await check('④ 生成→直接回填（无勾选步骤）：draft 变化、状态条、逐字段摘要、零保存', async () => {
      ctx.assist.fillQueue.push({ formId: 'object', operations: [setOp('label', '储能单元'), setOp('comment', '由电池簇、汇流与监测组件构成的储能单元。')] })
      await ctx.panel.generate()
      assert.equal(ctx.assist.calls.generate.length, 1)
      const body = ctx.assist.calls.generate[0]
      assert.equal(body.mode, 'fill'); assert.equal(body.protocol, 2, 'autofill/1 请求必带 protocol:2')
      assert.equal(body.contextToken, 'tok-1')
      assert.deepEqual(body.draft, { label: '储能簇', comment: '一组电池簇' }, 'generate 携带当前草稿')
      // 直接回填：无 checked/采纳步骤
      assert.equal(ctx.api.objectDraft.value.label, '储能单元')
      assert.equal(ctx.api.objectDraft.value.comment, '由电池簇、汇流与监测组件构成的储能单元。')
      assert.equal(ctx.formSave.saves.length, 0, '回填绝不触发表单保存（form-save 零调用）')
      assert.equal(ctx.panel.status.value, 'done')
      assert.equal(ctx.panel.collapsed.value, true, '一次回填完成抽屉自动收起')
      assert.equal(ctx.panel.canUndo, undefined, '撤销入口在宿主状态条（面板不渲染）')
      // 状态条（宿主渲染）与引擎口径一致
      const b = ctx.api.assistBinding.value
      assert.equal(b.round.statusBarText, '已填写 2 项，尚未保存')
      assert.equal(b.round.statusBarText, ctx.panel.statusBarText.value, '宿主镜像与引擎状态条口径一致')
      assert.equal(b.round.canUndo, true)
      assert.deepEqual(b.round.changes.map(c => c.oldText), ['储能簇', '一组电池簇'])
      assert.deepEqual(b.round.changes.map(c => c.newText), ['储能单元', '由电池簇、汇流与监测组件构成的储能单元。'])
      const page = await ctx.html()
      assert.match(page, /已填写 2 项，尚未保存/)
      assert.match(page, /撤销本次填写/)
      assert.match(page, /查看修改/)
      assert.match(page, /对象名称<\/strong>：储能簇 → 储能单元/, '逐字段旧值→新值（安全插值渲染）')
      const phtml = await ctx.panelHtml()
      assert.ok(!phtml.includes('assist-drawer'), 'done 后抽屉保持收起')
      assert.ok(!phtml.includes('勾选'), '无勾选卡')
    })

    await check('⑤ 宿主撤销本次填写：恢复本轮开始前草稿、一次性、状态条清除、零保存', async () => {
      const b = ctx.api.assistBinding.value
      assert.equal(b.undoRound(), true)
      assert.deepEqual(ctx.api.objectDraft.value, { label: '储能簇', comment: '一组电池簇' }, '恢复本轮起点')
      assert.equal(ctx.formSave.saves.length, 0, '撤销同样不触发表单保存')
      assert.equal(b.round.statusBarText, ''); assert.equal(b.round.undone, true)
      assert.equal(b.round.changes.length, 0)
      assert.equal(b.undoRound(), false, '一次性')
      const page = await ctx.html()
      assert.match(page, /已撤销本次自动填写，表单已恢复。/)
      assert.ok(!page.includes('已填写 2 项'), '状态条主文案清除')
    })

    await check('⑥ 手改禁撤销：undoRound 拒绝、草稿保留手改、状态条说明、手改通知计数', async () => {
      // ⑤ 的整页渲染重建了面板实例（SSR 无 onMounted 自动 open）：浏览器由挂载自动 open，
      // 这里显式重开同一目标（open 亦会展开抽屉）
      await ctx.panel.open(ctx.api.assistBinding.value)
      ctx.assist.fillQueue.push({ formId: 'object', operations: [setOp('comment', '新的业务定义。')] })
      await ctx.panel.generate()
      const b = ctx.api.assistBinding.value
      assert.equal(b.round.statusBarText, '已填写 1 项，尚未保存')
      ctx.api.setObjectField('label', '手改名称') // 模板 @update:model-value 的同一写路径
      assert.equal(ctx.api.assistTouchTick.value, 1, '字段输入事件触发手改通知')
      assert.equal(b.round.canUndo, false, '手改后禁整轮撤销')
      assert.equal(b.undoRound(), false, '撤销被拒')
      assert.equal(ctx.api.objectDraft.value.label, '手改名称', '手改值保留')
      assert.equal(ctx.api.objectDraft.value.comment, '新的业务定义。', '已回填的其他字段不被撤销覆盖')
      assert.match(b.round.undoHint, /已保留你的手动修改/)
      ctx.panel.notifyDraftChanged() // 宿主 ref 通道（浏览器里由 assistTouched 经模板 ref 调用）
      assert.ok(ctx.panel.undoHint.value.length > 0, '引擎侧同步禁撤销')
      const page = await ctx.html()
      assert.match(page, /已保留你的手动修改，本次自动填写不可直接撤销。/)
      assert.match(page, /<button[^>]*disabled/, '撤销按钮禁用')
    })

    await check('⑦ 续轮（补答）：已填 N 项另有 M 项待补充；整轮（首轮+续轮）一次撤销', async () => {
      const b = ctx.api.assistBinding.value
      // ⑥ 结束草稿含手改值；新一轮（新撤销单元）在引擎 resetRoundUnit 后从当前草稿重新开始
      ctx.api.objectDraft.value.label = '储能簇'; ctx.api.objectDraft.value.comment = '一组电池簇'
      const contextCallsBefore = ctx.assist.calls.context.length
      const genCallsBefore = ctx.assist.calls.generate.length
      await ctx.panel.open(ctx.api.assistBinding.value) // 渲染后面板实例重建：显式重开
      ctx.assist.fillQueue.push({
        formId: 'object',
        operations: [setOp('label', '储能单元')],
        questions: [{ id: 'q1', text: '业务定义按哪个口径？', fields: ['comment'], options: ['口径A', '口径B'], allowUnsure: true }],
      })
      await ctx.panel.generate()
      assert.equal(ctx.panel.status.value, 'questions', '有待补问题留在抽屉')
      assert.equal(ctx.panel.collapsed.value, false)
      assert.equal(b.round.statusBarText, '已填写 1 项，尚未保存；另有 1 项待补充')
      assert.equal(b.round.statusBarText, ctx.panel.statusBarText.value, '待补充口径与引擎一致')
      assert.equal(b.round.pendingCount, 1)
      ctx.panel.answer('q1', '口径A') // 补答后自动续轮（握手异步完成）
      ctx.assist.fillQueue.push({ formId: 'object', operations: [setOp('comment', '口径A的定义。')] })
      await new Promise(r => setTimeout(r, 5))
      assert.equal(ctx.assist.calls.generate.length, genCallsBefore + 2)
      const body = ctx.assist.calls.generate.at(-1)
      assert.equal(body.sessionId, 's-' + (genCallsBefore + 1), '续轮携带首轮响应签发的 sessionId')
      assert.deepEqual(body.answers, [{ questionId: 'q1', value: '口径A' }])
      assert.equal(ctx.assist.calls.context.length, contextCallsBefore + 2, '重开与续轮握手（草稿漂移）各取一次上下文')
      assert.equal(ctx.panel.status.value, 'done', '全部解决后收起')
      assert.equal(ctx.panel.collapsed.value, true)
      assert.equal(ctx.api.objectDraft.value.label, '储能单元')
      assert.equal(ctx.api.objectDraft.value.comment, '口径A的定义。')
      assert.equal(b.round.statusBarText, '已填写 2 项，尚未保存', '跨续轮累计 N')
      assert.equal(b.round.pendingCount, 0, '全部解决后待补充清零')
      assert.equal(b.undoRound(), true, '整轮（首轮+续轮）一次撤销')
      assert.deepEqual(ctx.api.objectDraft.value, { label: '储能簇', comment: '一组电池簇' }, '恢复到本轮开始前')
      assert.equal(ctx.formSave.saves.length, 0)
    })

    await check('⑧ 切目标作废：在途生成零写入，binding 以新目标重开、镜像重置', async () => {
      ctx.panel.expand()
      ctx.assist.fillQueue.push('hang') // 在途请求
      const hanging = ctx.panel.generate()
      assert.equal(ctx.panel.status.value, 'generating')
      ctx.api.selected.value = 'mg:site'
      ctx.api.openObjectEditor(false) // 直接替换编辑目标（binding 变化 → 浏览器由面板 watch 重开）
      const nb = ctx.api.assistBinding.value
      assert.equal(nb.targetId, 'mg:site', 'binding 已指向新目标')
      assert.equal(nb.formId, 'object')
      assert.equal(nb.round.statusBarText, '', '新目标镜像是干净的')
      assert.deepEqual(nb.draft(), { label: '储能站', comment: '场站' })
      await ctx.panel.open(nb) // 切目标重开（作废在途请求）
      assert.equal(ctx.panel.status.value, 'open', '生成状态已复位')
      assert.ok(ctx.assist.calls.context.at(-1).targetId === 'mg:site')
      await Promise.race([hanging, new Promise(r => setTimeout(r, 10))])
      assert.deepEqual(ctx.api.objectDraft.value, { label: '储能站', comment: '场站' }, '迟到响应零写入')
      assert.equal(ctx.formSave.saves.length, 0)
    })
  }

  // ── ⑨ 契约指纹不符 / empty：零写入、状态条不显示 ──
  {
    const ctx = mountWorkspace()
    await ctx.initial
    ctx.api.selected.value = 'mg:cluster'
    ctx.api.openObjectEditor(false)
    ctx.api.toggleAssist()
    await ctx.html()
    await ctx.panel.open(ctx.api.assistBinding.value)

    await check('⑨ 契约指纹不符（CONTEXT_STALE）与 empty：零写入、镜像不显示待补', async () => {
      ctx.assist.fillQueue.push({ formId: 'object', schemaDigest: 'bad00000000000000000000000000000000000000000000000000000000000000', operations: [setOp('label', '越权写入')] })
      await ctx.panel.generate()
      assert.equal(ctx.panel.status.value, 'error')
      assert.equal(ctx.panel.error.value.code, 'CONTEXT_STALE')
      assert.deepEqual(ctx.api.objectDraft.value, { label: '储能簇', comment: '一组电池簇' }, '契约不符零写入')
      assert.equal(ctx.api.assistBinding.value.round.statusBarText, '')

      ctx.assist.fillQueue.push({ formId: 'object', status: 'empty', operations: [] })
      await ctx.panel.generate()
      assert.equal(ctx.panel.status.value, 'empty')
      assert.deepEqual(ctx.api.objectDraft.value, { label: '储能簇', comment: '一组电池簇' }, 'empty 零写入')
      assert.equal(ctx.api.assistBinding.value.round.statusBarText, '', 'empty 无状态条')
      assert.equal(ctx.formSave.saves.length, 0)
    })

    await check('⑩ 错误后重试同轮：输入与草稿保留，成功回填照常', async () => {
      ctx.assist.fillQueue.push({ formId: 'object', operations: [setOp('comment', '重试后的定义。')] })
      await ctx.panel.generate()
      assert.equal(ctx.panel.status.value, 'done')
      assert.equal(ctx.api.objectDraft.value.comment, '重试后的定义。')
      assert.equal(ctx.api.assistBinding.value.round.statusBarText, '已填写 1 项，尚未保存')
    })
  }

  // ── ⑪ 链接编辑器（新建 → 回填不保存 → 撤销 → 手改拦截 → 关闭）──
  {
    const ctx = mountWorkspace()
    await ctx.initial
    ctx.api.selected.value = 'mg:cluster'
    ctx.api.openLinkEditor() // 新建链接：起点默认当前对象

    await check('⑪ 链接编辑器：入口、上下文请求（新建 targetId 空串）、回填只改草稿、撤销、手改禁撤销', async () => {
      let page = await ctx.html()
      if (ASSIST_ENTRY_ENABLED) {
        assert.match(page, /✦ 自动填写/, '链接表单头同样有次要入口')
        assert.ok(page.includes('id="ow-assist-trigger-link"'))
      } else {
        assert.ok(!page.includes('✦ 自动填写'), '功能开关关闭：链接表单头不渲染入口')
      }
      ctx.api.toggleAssist()
      await ctx.html()
      await ctx.panel.open(ctx.api.assistBinding.value)
      const body = ctx.assist.calls.context.at(-1)
      assert.equal(body.targetKind, 'link'); assert.equal(body.targetId, '', '新建链接尚未保存，targetId 为空')
      assert.deepEqual(body.draft, { label: '', from: 'mg:cluster', to: 'mg:site', cardinality: 'many-to-one', reverseLabel: '', comment: '' })
      const b = ctx.api.assistBinding.value
      assert.equal(b.formId, 'link')
      assert.deepEqual(b.contractInfo(), { schemaVersion: 1, schemaDigest: FORM_SCHEMA_DIGESTS.link })

      ctx.assist.fillQueue.push({ formId: 'link', operations: [setOp('label', '接入储能站'), setOp('reverseLabel', '包含储能簇'), setOp('cardinality', 'one-to-many')] })
      await ctx.panel.generate()
      assert.equal(ctx.api.linkDraft.value.label, '接入储能站')
      assert.equal(ctx.api.linkDraft.value.reverseLabel, '包含储能簇')
      assert.equal(ctx.api.linkDraft.value.cardinality, 'one-to-many')
      assert.equal(ctx.api.linkDraft.value.from, 'mg:cluster', '未提及字段逐字保留')
      assert.equal(ctx.formSave.saves.length, 0, '回填绝不触发表单保存')
      assert.equal(b.round.statusBarText, '已填写 3 项，尚未保存')
      assert.equal(b.round.statusBarText, ctx.panel.statusBarText.value)

      assert.equal(b.undoRound(), true, '整轮撤销')
      assert.equal(ctx.api.linkDraft.value.label, '', '恢复本轮起点')
      assert.equal(ctx.api.linkDraft.value.cardinality, 'many-to-one')

      ctx.panel.expand() // done 自动收起后重新展开（浏览器里由 ✦ 入口触发）
      ctx.assist.fillQueue.push({ formId: 'link', operations: [setOp('label', '第二次回填')] })
      await ctx.panel.generate()
      ctx.api.setLinkField('label', '手改链接名')
      assert.equal(ctx.api.assistTouchTick.value, 1, '链接字段输入触发手改通知')
      assert.equal(b.round.canUndo, false)
      assert.equal(b.undoRound(), false)
      assert.equal(ctx.api.linkDraft.value.label, '手改链接名')
    })

    await check('⑪b 编辑器关闭：面板卸载，浏览态无 AI 入口', async () => {
      ctx.api.closeEditor()
      assert.equal(ctx.api.assistOpen.value, false, 'closeEditor 卸载面板')
      assert.equal(ctx.api.assistBinding.value, null, '编辑器关闭后 binding 置空')
      const page = await ctx.html()
      assert.ok(!page.includes('assist-drawer'), '不再渲染抽屉')
      assert.ok(!page.includes('✦ 自动填写'), '浏览态无 AI 入口')
    })
  }

  // ── ⑫ 显式保存链路不受影响（非 assist 回归）──
  await check('⑫ 显式保存：草稿经 setObjectField 修改后保存照常走 form-save（恰好 1 次）', async () => {
    const ctx = mountWorkspace()
    await ctx.initial
    ctx.api.selected.value = 'mg:cluster'
    ctx.api.openObjectEditor(false)
    ctx.api.setObjectField('label', '手工改名')
    ctx.api.setObjectField('comment', '手工定义')
    await ctx.api.saveObject()
    assert.equal(ctx.formSave.saves.length, 1, '用户显式保存照常落盘')
    assert.deepEqual(JSON.parse(JSON.stringify(ctx.state.ontology['@graph'].find(n => n['@id'] === 'mg:cluster'))), { '@id': 'mg:cluster', '@type': 'owl:Class', 'rdfs:label': '手工改名', 'rdfs:comment': '手工定义' })
  })
} finally {
  rmSync(root, { recursive: true, force: true })
}

const failed = results.filter(r => !r)
console.log(`\n统计：${results.length - failed.length}/${results.length} 项通过`)
if (failed.length) process.exit(1)
