// 对象/链接编辑器辅助填写接入测试（2026-09-21 T5，覆盖 O1/O3）。运行（仓库根）：
//   node --import ./tests/ts_hooks.mjs tests/assist_object_workspace.test.mjs
// 编译真实 ObjectWorkspace.vue 与 AssistPanel.vue（其余子组件桩化、无路由无服务、不发真实请求）；
// assist api 经 provide('assist-api') 注入合成桩（T4 接线前的组件级验证方式）。
// 覆盖：① binding 工厂白名单/合并/快照恢复；② 辅助入口与面板出现、目标标题、上下文请求体；
// ③ 采纳只改本地草稿且绝不触发表单保存、出现「尚未保存」提示；④ 手改后采纳被草稿指纹拦下、
// 撤销失效、手改通知已发出；⑤ 切换编辑目标以新 binding 重开；⑥ 链接编辑器同链路；⑦ 关闭编辑器面板收起。
// 2026-09-22 适配默认勾选门控（useAssistPanel，需求 §3.5）：ready 且宿主草稿旧值为空才默认勾选，
// 替换非空旧值的建议默认不勾——替换类用例改为显式 setChecked 后采纳，并新增门控断言。
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

// ── 捕获每次挂载的 setup 状态（多场景互不串线）────────────────────────────────
let activeSlot = null
const origSetup = Component.setup
Component.setup = (props, ctx) => { const api = origSetup(props, ctx); if (activeSlot) activeSlot.api = api; return api }
const origPanelSetup = Panel.setup
Panel.setup = (props, ctx) => { const s = origPanelSetup(props, ctx); if (activeSlot) activeSlot.panel = s; return s }

// ── assist-api 桩：合成 context/generate；记录请求体；generate 按队列出栈 ─────────
function makeAssistApi() {
  const calls = { context: [], generate: [] }
  const genQueue = []
  return {
    calls, genQueue,
    async context(body) {
      calls.context.push(JSON.parse(JSON.stringify(body)))
      const fields = body.targetKind === 'object'
        ? [{ key: 'label', label: '对象名称', kind: 'text', required: true, options: null, group: null, help: '' }, { key: 'comment', label: '业务定义', kind: 'textarea', required: true, options: null, group: null, help: '' }]
        : [{ key: 'label', label: '正向名称', kind: 'text', required: true, options: null, group: null, help: '' }]
      return {
        contextToken: 'tok-' + calls.context.length,
        contextFingerprint: 'fp-' + calls.context.length,
        context: { targetKind: body.targetKind, title: 'CTX[' + body.targetKind + ':' + body.targetId + ']', editableFields: fields, definitions: [], catalog: [], flows: [], modelReady: true },
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
    // 用同一份 setup 状态重渲染页面（不重跑 setup，等价 mapping_forms 的 html() 手法）
    html: () => { activeSlot = slot; return renderToString(createSSRApp({ render: () => h({ props: Component.props, ssrRender: Component.ssrRender, setup: () => slot.api }, { state }) })) },
    // 用驱动后的面板状态单独渲染面板（验证横幅/禁用态等 UI 呈现）
    panelHtml: () => renderToString(createSSRApp({ render: () => h({ props: Panel.props, ssrRender: Panel.ssrRender, setup: () => slot.panel }, { binding: slot.api.assistBinding.value }) })),
  }
}

const results = []
async function check(name, fn) { try { await fn(); results.push(true); console.log('通过：' + name) } catch (e) { results.push(false); console.log('失败：' + name + ' — ' + (e && e.message)) } }

try {
  // ── ① binding 工厂（白名单、合并、快照恢复）──
  {
    const { objectAssistBinding, linkAssistBinding } = await import('../frontend/src/assist/ontologyBindings.ts')
    await check('① binding 工厂：白名单快照、undefined 不覆盖、白名单外忽略、快照就地恢复', async () => {
      const d = { label: '储能簇', comment: '旧定义' }
      const b = objectAssistBinding(d, { targetId: 'mg:cluster', contextTitle: '标题T' })
      assert.equal(b.space, 'ontology'); assert.equal(b.targetKind, 'object'); assert.equal(b.targetId, 'mg:cluster'); assert.equal(b.contextTitle, '标题T')
      assert.deepEqual(Object.keys(b.draft()), ['label', 'comment'], '快照只含白名单键')
      assert.deepEqual(b.draft(), { label: '储能簇', comment: '旧定义' })
      b.apply({ label: '新名称', comment: undefined, extraKey: '越权', nested: { a: 1 } })
      assert.equal(d.label, '新名称')
      assert.equal(d.comment, '旧定义', 'undefined 值不得覆盖既有草稿')
      assert.ok(!('extraKey' in d) && !('nested' in d), '白名单外键不得落入草稿')
      const snap = b.snapshot()
      assert.deepEqual(snap, { label: '新名称', comment: '旧定义' })
      d.label = '手改'
      assert.deepEqual(snap, { label: '新名称', comment: '旧定义' }, '快照是 JSON 克隆，不随草稿后续修改漂移')
      const refBefore = d
      b.restore(snap)
      assert.ok(d === refBefore, 'restore 必须就地恢复（宿主草稿引用保持稳定）')
      assert.equal(d.label, '新名称'); assert.equal(d.comment, '旧定义')

      const ld = { label: '所属场站', from: 'mg:cluster', to: 'mg:site', cardinality: 'many-to-one', reverseLabel: '', comment: '' }
      const lb = linkAssistBinding(ld) // 缺省：targetId ''、兜底标题非空
      assert.equal(lb.targetKind, 'link'); assert.equal(lb.targetId, ''); assert.ok(lb.contextTitle.length > 0)
      assert.deepEqual(Object.keys(lb.draft()), ['label', 'from', 'to', 'cardinality', 'reverseLabel', 'comment'])
      lb.apply({ cardinality: 'one-to-many', reverseLabel: '包含储能簇', flow: 'f_x' })
      assert.equal(ld.cardinality, 'one-to-many'); assert.equal(ld.reverseLabel, '包含储能簇')
      assert.ok(!('flow' in ld), '链接白名单外键忽略')
      const lsnap = lb.snapshot(); ld.from = 'mg:z'; lb.restore(lsnap); assert.equal(ld.from, 'mg:cluster')

      const eb = objectAssistBinding(null), el = linkAssistBinding(undefined) // 空草稿兜底：不抛错
      assert.deepEqual(eb.draft(), { label: '', comment: '' })
      assert.deepEqual(el.draft(), { label: '', from: '', to: '', cardinality: '', reverseLabel: '', comment: '' })
      eb.apply({ label: 'x' }); eb.restore({}); assert.ok(true, '空草稿 apply/restore 安全')
    })
  }

  // ── ②~⑤ 对象编辑器全链路（共用同一份编辑状态）──
  {
    const ctx = mountWorkspace()
    await ctx.initial
    ctx.api.selected.value = 'mg:cluster'
    ctx.api.openObjectEditor(false)

    await check('② 对象编辑器：辅助入口存在；点击后面板出现且目标标题正确', async () => {
      let page = await ctx.html()
      assert.match(page, /✦ 辅助填写/, '对象表单头应有辅助入口')
      assert.ok(!page.includes('assist-panel'), '未点击前不渲染面板')
      ctx.api.toggleAssist() // 与按钮 @click 同一函数
      page = await ctx.html()
      assert.match(page, /assist-panel/, '点击后面板出现')
      assert.match(page, /维护「储能簇」的对象定义/, '未取到上下文时展示目标兜底标题')
      assert.ok(ctx.panel, '面板实例已随页面创建')
    })

    await check('②b 上下文请求携带场景与白名单草稿；取到后展示服务端标题', async () => {
      await ctx.panel.open(ctx.api.assistBinding.value)
      assert.equal(ctx.assist.calls.context.length, 1)
      const body = ctx.assist.calls.context[0]
      assert.equal(body.space, 'ontology'); assert.equal(body.targetKind, 'object')
      assert.equal(body.targetId, 'mg:cluster'); assert.equal(body.purpose, 'fill')
      assert.deepEqual(body.draft, { label: '储能簇', comment: '一组电池簇' }, 'draft 是白名单形态快照')
      const phtml = await ctx.panelHtml()
      assert.match(phtml, /CTX\[object:mg:cluster\]/, '取到上下文后面板标题切换为服务端标题')
    })

    await check('③ 采纳建议：editor.draft 更新；form-save 与任何保存通道零调用；出现「尚未保存」提示', async () => {
      ctx.assist.genQueue.push({ suggestions: [
        { id: 's1', label: '对象名称', fieldKeys: ['label'], proposed: { label: '储能单元', comment: '越权字段不应落入' }, state: 'ready' },
        { id: 's2', label: '业务定义', fieldKeys: ['comment'], proposed: { comment: '由电池簇、汇流与监测组件构成的储能单元。' }, state: 'ready' },
      ] })
      await ctx.panel.generate()
      assert.equal(ctx.assist.calls.generate.length, 1)
      assert.equal(ctx.assist.calls.generate[0].contextToken, 'tok-1')
      assert.deepEqual(ctx.assist.calls.generate[0].draft, { label: '储能簇', comment: '一组电池簇' }, 'generate 携带当前草稿')
      // 默认勾选门控（需求 §3.5）：label/comment 旧值非空 → ready 建议默认不勾；替换已有值须显式勾选
      assert.equal(ctx.panel.checked.value['s1'], false, 'label 旧值非空 → s1 默认 checked=false')
      assert.equal(ctx.panel.checked.value['s2'], false, 'comment 旧值非空 → s2 默认 checked=false')
      assert.equal(ctx.panel.selectedCount.value, 0, '建议全部替换非空旧值时默认选中数为 0')
      ctx.panel.setChecked('s1', true) // 替换已有值：经勾选路径后再采纳
      ctx.panel.setChecked('s2', true)
      assert.equal(ctx.panel.adopt(), true)
      assert.equal(ctx.api.objectDraft.value.label, '储能单元')
      assert.equal(ctx.api.objectDraft.value.comment, '由电池簇、汇流与监测组件构成的储能单元。')
      assert.equal(ctx.formSave.saves.length, 0, '采纳绝不触发表单保存（form-save 零调用）')
      assert.equal(ctx.panel.canUndo.value, true)
      assert.equal(ctx.panel.justAdopted.value, true)
      const phtml = await ctx.panelHtml()
      assert.match(phtml, /已填入表单，尚未保存/, '面板提示尚未保存')
    })

    await check('④ 手改字段：手改通知已发出；采纳被草稿指纹拦下；撤销失效', async () => {
      ctx.api.setObjectField('label', '手改名称')
      assert.equal(ctx.api.objectDraft.value.label, '手改名称')
      assert.equal(ctx.api.assistTouchTick.value, 1, '字段输入事件触发了手改通知')
      assert.equal(ctx.panel.adopt(), false, '手改后草稿指纹漂移，采纳被拒（不写入建议值）')
      assert.equal(ctx.api.objectDraft.value.label, '手改名称', '被拒的采纳不得改动草稿')
      assert.equal(ctx.panel.stale.value, true)
      assert.equal(ctx.panel.canUndo.value, false, '手改后撤销保护解除')
      assert.equal(ctx.panel.canAdopt.value, false, '采纳按钮禁用')
      assert.equal(ctx.panel.undo(), false, '撤销不可用')
      ctx.api.setObjectField('comment', '手改定义')
      ctx.panel.notifyDraftChanged() // 宿主 ref 通道（浏览器里由 assistTouched 经模板 ref 调用）
      assert.equal(ctx.panel.justAdopted.value, false, '手改清除「已填入」提示')
      const phtml = await ctx.panelHtml()
      assert.match(phtml, /建议已失效：表单已修改/, '面板展示过期横幅')
    })

    await check('⑤ 切换编辑目标：面板以新 binding 重开（targetId 变化、重新取上下文）', async () => {
      const panelBefore = ctx.panel
      ctx.api.selected.value = 'mg:site'
      ctx.api.openObjectEditor(false) // 直接替换编辑目标（深链/编辑跳转同路径）
      assert.equal(ctx.api.assistOpen.value, true, '面板保持展开')
      assert.equal(ctx.api.assistBinding.value.targetKind, 'object')
      assert.equal(ctx.api.assistBinding.value.targetId, 'mg:site', 'binding 已指向新目标')
      const page = await ctx.html()
      assert.match(page, /维护「储能站」的对象定义/, '面板标题随新目标更新')
      assert.ok(ctx.panel && ctx.panel !== panelBefore, '面板实例已重建（以新目标重开）')
      await ctx.panel.open(ctx.api.assistBinding.value)
      const last = ctx.assist.calls.context.at(-1)
      assert.equal(last.targetId, 'mg:site')
      assert.deepEqual(last.draft, { label: '储能站', comment: '场站' }, '新目标的草稿快照')
    })
  }

  // ── ⑥ 链接编辑器（新建 → 建议 → 采纳不保存 → 手改拦截）──
  {
    const ctx = mountWorkspace()
    await ctx.initial
    ctx.api.selected.value = 'mg:cluster'
    ctx.api.openLinkEditor() // 新建链接：起点默认当前对象

    await check('⑥ 链接编辑器：辅助入口、上下文请求、采纳只改草稿且不自动保存', async () => {
      let page = await ctx.html()
      assert.match(page, /✦ 辅助填写/, '链接表单头同样有辅助入口')
      ctx.api.toggleAssist()
      page = await ctx.html()
      assert.match(page, /assist-panel/)
      assert.match(page, /定义业务链接/, '新建链接的兜底标题')
      await ctx.panel.open(ctx.api.assistBinding.value)
      const body = ctx.assist.calls.context[0]
      assert.equal(body.targetKind, 'link'); assert.equal(body.targetId, '', '新建链接尚未保存，targetId 为空')
      assert.deepEqual(body.draft, { label: '', from: 'mg:cluster', to: 'mg:site', cardinality: 'many-to-one', reverseLabel: '', comment: '' })
      ctx.assist.genQueue.push({ suggestions: [
        { id: 'l1', label: '正向名称与数量关系', fieldKeys: ['label', 'cardinality'], proposed: { label: '接入储能站', cardinality: 'one-to-many' }, state: 'ready' },
        { id: 'l2', label: '反向名称', fieldKeys: ['reverseLabel'], proposed: { reverseLabel: '包含储能簇' }, state: 'ready' },
        { id: 'l3', label: '越权改起点', fieldKeys: ['from'], proposed: { from: 'mg:site' }, state: 'ready' },
      ] })
      await ctx.panel.generate()
      // 默认勾选门控（需求 §3.5）：reverseLabel 旧值为空 → 默认勾选；cardinality/from 旧值非空 → 默认不勾
      assert.equal(ctx.panel.checked.value['l1'], false, 'cardinality 旧值非空 → l1 默认 checked=false')
      assert.equal(ctx.panel.checked.value['l2'], true, 'reverseLabel 旧值为空 → l2（ready）默认勾选')
      assert.equal(ctx.panel.checked.value['l3'], false, 'from 旧值非空 → l3 默认 checked=false')
      ctx.panel.setChecked('l1', true) // 替换已有值：经勾选路径后再采纳
      ctx.panel.setChecked('l3', false) // 只采纳部分建议
      assert.equal(ctx.panel.selectedCount.value, 2)
      assert.equal(ctx.panel.adopt(), true)
      assert.equal(ctx.api.linkDraft.value.label, '接入储能站')
      assert.equal(ctx.api.linkDraft.value.cardinality, 'one-to-many')
      assert.equal(ctx.api.linkDraft.value.reverseLabel, '包含储能簇')
      assert.equal(ctx.api.linkDraft.value.from, 'mg:cluster', '未勾选的建议不得改动草稿')
      assert.equal(ctx.formSave.saves.length, 0, '采纳绝不触发表单保存')
      assert.equal(ctx.panel.justAdopted.value, true)
      const phtml = await ctx.panelHtml()
      assert.match(phtml, /已填入表单，尚未保存/)
    })

    await check('⑥b 链接表单手改字段：通知发出、再次采纳被拦、撤销失效', async () => {
      ctx.api.setLinkField('label', '手改链接名')
      assert.equal(ctx.api.assistTouchTick.value, 1, '链接字段输入事件触发手改通知')
      assert.equal(ctx.panel.adopt(), false, '手改后采纳被草稿指纹拦下')
      assert.equal(ctx.api.linkDraft.value.label, '手改链接名')
      assert.equal(ctx.panel.canUndo.value, false)
      assert.equal(ctx.panel.stale.value, true)
    })
  }

  await check('⑦ 编辑器关闭：面板收起，浏览态无辅助入口', async () => {
    const ctx = mountWorkspace()
    await ctx.initial
    ctx.api.selected.value = 'mg:cluster'
    ctx.api.openObjectEditor(false)
    ctx.api.toggleAssist()
    let page = await ctx.html()
    assert.match(page, /assist-panel/, '面板已展开')
    ctx.api.closeEditor()
    assert.equal(ctx.api.assistOpen.value, false, 'closeEditor 收起面板')
    assert.equal(ctx.api.assistBinding.value, null, '编辑器关闭后 binding 置空')
    page = await ctx.html()
    assert.ok(!page.includes('assist-panel'), '收起后不再渲染面板')
    assert.ok(!page.includes('辅助填写'), '浏览态无辅助入口')
  })
} finally {
  rmSync(root, { recursive: true, force: true })
}

const failed = results.filter(r => !r)
console.log(`\n统计：${results.length - failed.length}/${results.length} 项通过`)
if (failed.length) process.exit(1)
