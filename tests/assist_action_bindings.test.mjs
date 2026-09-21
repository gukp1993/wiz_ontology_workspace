// T10 · P6 动作接口映射接入辅助填写 回归（2026-09-21）。运行（仓库根）：
//   node --import ./tests/ts_hooks.mjs tests/assist_action_bindings.test.mjs
// 模式：SFC 编译（assist_property_manager 同款）——ActionBindings.vue 挂真实 AssistPanel（T3 状态机），
// AppSelect/EditorField/MappingDescription 桩化；assist api 经 provide('assist-api') 注入合成桩，
// 全程禁网（fetch 一律抛错）。SSR 不执行 onMounted 与模板 ref 填充，面板由测试显式 open(generate/adopt)
// 驱动（与 assist_object_workspace 同一手法）；手改→notifyDraftChanged 的 watch 接线由源码断言 +
// 状态机同入口直驱覆盖，浏览器实链路留给独立验收。
// 覆盖：① 工厂白名单快照（恰 6 键、绝无 auth、参数行 {name,in,value} 出网结构、undefined 不覆盖）；
// ② apply：method/path/bodyFormat/description/note 写回、parameters 整组替换（newParamId 补齐行 id）、
// 混入的 auth 键被忽略（draft.auth 不变）；③ 弹窗入口与面板出现、上下文请求体（space/projectId/
// targetId/draft 白名单）、采纳只改本地草稿（form-save/commit-now 间谍零调用）、「尚未保存」提示、
// 撤销恢复（含 auth 与参数行 id）、手改后面板过期与撤销保护解除；④ 关闭弹窗收起面板（closeNow 与
// closeEditor 两条路径）；⑤ 切换动作目标重开（未保存新建 targetId 空、只读 flow 无入口、已配置目标重取上下文）。
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { readFileSync, mkdtempSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve, dirname, basename } from 'node:path'
import { pathToFileURL } from 'node:url'

const require = createRequire(new URL('../frontend/package.json', import.meta.url))
const { parse, compileScript, compileTemplate } = require('@vue/compiler-sfc')
const ts = require('typescript'), { createSSRApp, reactive, h } = require('vue'), { renderToString } = require('@vue/server-renderer')

const root = mkdtempSync(join(tmpdir(), 'wiz_assist_ab_'))
const realFetch = globalThis.fetch
globalThis.fetch = async (url) => { throw new Error('测试不应发起真实请求：' + String(url)) } // 全程禁网：辅助/凭据/编排 API 一律走桩

// 组件里可能触达的浏览器 API：SSR 不执行 onMounted，但 watch/nextTick 路径会触达部分对象。
globalThis.window = { matchMedia: () => ({ matches: false, addEventListener() {}, removeEventListener() {} }), addEventListener() {}, removeEventListener() {}, innerHeight: 900, location: { hash: '' } }
globalThis.document = { addEventListener() {}, removeEventListener() {}, querySelector: () => null, querySelectorAll: () => [], activeElement: null }
globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} }
if (!globalThis.crypto?.randomUUID) Object.defineProperty(globalThis, 'crypto', { value: require('node:crypto').webcrypto, configurable: true })

// ── SFC 加载：指定 .vue 保留为共享变量（真实 AssistPanel），其余 .vue 一律桩成 render:null ──
async function loadSfc(path, vueOverrides = {}) {
  const filename = resolve(path)
  const { descriptor } = parse(readFileSync(filename, 'utf8'), { filename })
  const id = basename(path).replace(/\.vue$/, '')
  const script = compileScript(descriptor, { id })
  const template = compileTemplate({ source: descriptor.template.content, filename, id, ssr: true, ssrCssVars: [], compilerOptions: { bindingMetadata: script.bindings } })
  assert.deepEqual(template.errors, [])
  let code = script.content
  for (const [spec, globalName] of Object.entries(vueOverrides)) {
    const re = new RegExp("import (\\w+) from ['\"]" + spec.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + "['\"]")
    assert.match(code, re, '应存在待保留的子组件导入：' + spec)
    code = code.replace(re, 'const $1 = ' + globalName)
  }
  code = code.replace(/import\s+(\w+)\s+from\s*['"][^'"]*\.vue['"]/g, 'const $1 = { render: () => null }')
  code = code.replace('export default ', 'const Component = ') + '\n' + template.code + '\nComponent.ssrRender=ssrRender; export default Component;'
  code = code.replace(/from (['"])([^'"]+)\1/g, (_, q, spec) => 'from ' + JSON.stringify(pathToFileURL(spec.startsWith('.') ? resolve(dirname(filename), spec + '.ts') : require.resolve(spec)).href))
  const file = join(root, id + '.mjs')
  writeFileSync(file, ts.transpileModule(code, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText)
  return (await import(pathToFileURL(file).href)).default
}

// 先编译真实面板，再编译 ActionBindings（其 AssistPanel 导入指向真实面板）。
const Panel = await loadSfc('frontend/src/assist/AssistPanel.vue')
globalThis.__assistPanelComp = Panel
const ABComponent = await loadSfc('frontend/src/project/ActionBindings.vue', { '../assist/AssistPanel.vue': '__assistPanelComp' })

// ── 捕获每次挂载的 setup 状态（多场景互不串线）────────────────────────────────
let activeSlot = null
const origABSetup = ABComponent.setup
ABComponent.setup = (props, ctx) => { const api = origABSetup(props, ctx); if (activeSlot) activeSlot.api = api; return api }
const origPanelSetup = Panel.setup
Panel.setup = (props, ctx) => { const s = origPanelSetup(props, ctx); if (activeSlot) activeSlot.panel = s; return s }

// ── 夹具：引用版本（对象 + 三动作）与项目（已配置 api / 历史 flow / 未配置 各一） ──
function refFixture() {
  return {
    ontology: { '@graph': [{ '@id': 'mg:cluster', '@type': 'owl:Class', 'rdfs:label': '储能簇', 'rdfs:comment': '一组电池簇' }] },
    workflow: {
      actions: [
        { id: 'act_stop', definitionVersion: 2, name: '停机', description: '停止设备运行', effect: '设备进入停机状态' },
        { id: 'act_reset', definitionVersion: 2, name: '复位', description: '复位设备' },
        { id: 'act_scan', definitionVersion: 2, name: '点检', description: '启动一次点检' },
      ],
      actionAssociations: [
        { objectTypeId: 'mg:cluster', actionId: 'act_stop' },
        { objectTypeId: 'mg:cluster', actionId: 'act_reset' },
        { objectTypeId: 'mg:cluster', actionId: 'act_scan' },
      ],
    },
  }
}

function projectFixture() {
  return {
    projectId: 'p_assist_t10',
    ontologyVersion: 'r1',
    bindings: {
      object_bindings: [{ object_type: 'cluster', identity: { kind: 'registered' }, properties: {} }],
      actionBindings: [
        { id: 'ab_stop', objectTypeId: 'cluster', actionId: 'act_stop', implementation: {
          kind: 'api', schemaVersion: 2, method: 'POST', path: 'https://ems.example.com/api/devices/{deviceId}/stop',
          bodyFormat: 'json', description: '旧接口说明',
          parameters: [{ id: 'p_old1', name: 'deviceId', in: 'path', value: { from: 'instanceId' } }],
          auth: { type: 'bearer', credentialId: 'cred_1' },
        } },
        { id: 'ab_reset', objectTypeId: 'cluster', actionId: 'act_reset', implementation: { kind: 'flow', flowId: 'fl_legacy' } },
      ],
      mappingDescriptions: { schemaVersion: 1, actions: { cluster: { act_stop: '停机接口的项目说明。' } } },
    },
  }
}

// ── assist-api 桩：合成 context/generate；记录请求体；generate 按队列出栈 ────────
function stubAssistApi() {
  const calls = { context: [], generate: [] }
  const genQueue = []
  return {
    calls, genQueue,
    api: {
      async context(body) {
        calls.context.push(JSON.parse(JSON.stringify(body)))
        return { contextToken: 'tok-' + calls.context.length, contextFingerprint: 'fp-' + calls.context.length,
          context: { targetKind: body.targetKind, title: 'CTX[actionBinding:' + body.targetId + ']', editableFields: [], definitions: [], catalog: [], flows: [], modelReady: true } }
      },
      async generate(body) {
        calls.generate.push(JSON.parse(JSON.stringify(body)))
        return { requestId: 'req-' + calls.generate.length, status: 'ok', contextFingerprint: 'fp-1', questions: [], issues: [], explanation: null, meta: { durationMs: 1, provider: '测试桩', model: 'stub' }, ...(genQueue.shift() || {}) }
      },
    },
  }
}

const settle = async () => { await new Promise(r => setImmediate(r)); await new Promise(r => setImmediate(r)) }

/** SSR 挂载 ActionBindings：form-guard/form-save/commit-now 桩（间谍），assist-api 注入桩。 */
async function mountAB({ refState = refFixture(), projectState = projectFixture(), objectType = 'cluster' } = {}) {
  const slot = { api: null, panel: null }
  const saves = [], commitNows = []
  const propsObj = { projectState: reactive(projectState), refState: reactive(refState), objectType }
  const stub = stubAssistApi()
  const app = createSSRApp(ABComponent, propsObj)
  app.provide('form-guard', { register() {}, unregister() {}, hasDirty: () => false, editing: () => false })
  app.provide('form-save', { async submitForm(area, mutate) { mutate(); saves.push(area); return { ok: true, message: '' } } })
  app.provide('commit-now', { submit: () => { commitNows.push(1) } })
  app.provide('assist-api', stub.api)
  activeSlot = slot
  await renderToString(app)
  assert.ok(slot.api && typeof slot.api.openEditor === 'function' && typeof slot.api.toggleAssist === 'function', 'setup 应暴露弹窗与辅助接线（openEditor/toggleAssist）')
  return {
    slot, stub, saves, commitNows,
    // 用同一份 setup 状态重渲染页面（不重跑 setup；面板实例随每次渲染重建并捕获）
    html: () => { activeSlot = slot; return renderToString(createSSRApp({ props: ABComponent.props, ssrRender: ABComponent.ssrRender, setup: () => slot.api }, propsObj)) },
    // 用驱动后的面板状态单独渲染面板（验证「尚未保存」等提示条）
    panelHtml: () => renderToString(createSSRApp({ props: Panel.props, ssrRender: Panel.ssrRender, setup: () => slot.panel }, { binding: slot.api.assistBinding.value })),
    rowOf: actionId => slot.api.rows.value.find(r => r.actionId === actionId),
    get api() { return slot.api },
    get panel() { return slot.panel },
  }
}

const results = []
const n = (name, cond, detail = '') => { results.push({ name, ok: !!cond }); console.log(`${cond ? '通过' : '失败'}：${name}${cond ? '' : ' — ' + (detail || '断言不成立')}`) }
const clone = (x) => JSON.parse(JSON.stringify(x))

try {
  // ── ① 工厂单测：白名单快照 / apply 语义 / 撤销保真 ──────────────────────────
  {
    const { actionBindingAssistBinding } = await import('../frontend/src/assist/actionBindingAdapter.ts')
    const draftObj = {
      kind: 'api', schemaVersion: 2, method: 'POST', path: 'https://a.example.com/x/{id}',
      bodyFormat: 'json', description: '说明A',
      parameters: [
        { id: 'p_old1', name: 'id', in: 'path', value: { from: 'instanceId' } },
        { id: 'p_old2', name: 'verbose', in: 'query', value: { from: 'constant', type: 'boolean', value: 'true' } },
        { id: 'p_old3', name: 'trace', in: 'header', value: { from: 'property', propertyId: 'mg:p_x' } },
      ],
      auth: { type: 'bearer', credentialId: 'cred_1' },
    }
    let note = '说明N'
    const setNoteCalls = []
    const binding = actionBindingAssistBinding({
      projectId: 'p1', targetId: 'cluster:act_stop', contextTitle: '动作「停机」的接口映射',
      draft: () => draftObj, note: () => note, setNote: v => { setNoteCalls.push(v); note = v },
    })
    n('①a binding 目标描述（project/actionBinding/projectId/targetId/标题）', binding.space === 'project' && binding.projectId === 'p1' && binding.targetKind === 'actionBinding' && binding.targetId === 'cluster:act_stop' && binding.contextTitle === '动作「停机」的接口映射')
    const snap = binding.draft()
    n('①b 快照键恰为白名单 6 键且绝无 auth', JSON.stringify(Object.keys(snap).sort()) === JSON.stringify(['bodyFormat', 'description', 'method', 'note', 'parameters', 'path']) && !JSON.stringify(snap).includes('auth'), JSON.stringify(Object.keys(snap).sort()))
    n('①c 参数行出网结构（无行 id、值形按 from 收窄）', JSON.stringify(snap.parameters) === JSON.stringify([
      { name: 'id', in: 'path', value: { from: 'instanceId' } },
      { name: 'verbose', in: 'query', value: { from: 'constant', type: 'boolean', value: 'true' } },
      { name: 'trace', in: 'header', value: { from: 'property', propertyId: 'mg:p_x' } },
    ]), JSON.stringify(snap.parameters))
    snap.parameters[0].name = '篡改'
    n('①d 快照是独立克隆（改快照不影响草稿）', draftObj.parameters[0].name === 'id' && binding.draft().parameters[0].name === 'id')

    binding.apply({ method: 'PUT', path: 'https://b.example.com/', bodyFormat: 'form', description: '说明B', note: '说明N2' })
    n('①e apply：method/path/bodyFormat/description/note 直写', draftObj.method === 'PUT' && draftObj.path === 'https://b.example.com/' && draftObj.bodyFormat === 'form' && draftObj.description === '说明B' && note === '说明N2' && setNoteCalls.length === 1)

    binding.apply({ parameters: [
      { name: 'deviceId', in: 'path', value: { from: 'instanceId' } },
      { name: 'force', in: 'query', value: { from: 'constant', type: 'boolean', value: 'true' } },
      '垃圾行', null,
    ] })
    n('①f apply：parameters 整组替换、行 id 由 newParamId 补齐、非对象行剔除', draftObj.parameters.length === 2
      && draftObj.parameters.every(p => typeof p.id === 'string' && /^p_/.test(p.id) && p.id !== 'p_old1')
      && new Set(draftObj.parameters.map(p => p.id)).size === 2
      && JSON.stringify(draftObj.parameters.map(p => ({ name: p.name, in: p.in, value: p.value }))) === JSON.stringify([
        { name: 'deviceId', in: 'path', value: { from: 'instanceId' } },
        { name: 'force', in: 'query', value: { from: 'constant', type: 'boolean', value: 'true' } },
      ]), JSON.stringify(draftObj.parameters))

    const before = clone(draftObj)
    binding.apply({ method: undefined, path: undefined, bodyFormat: undefined, description: undefined, parameters: undefined, note: undefined })
    n('①g undefined 一律不覆盖', JSON.stringify(draftObj) === JSON.stringify(before) && note === '说明N2')
    binding.apply({ parameters: '不是数组' })
    n('①h parameters 非数组不覆盖', JSON.stringify(draftObj.parameters) === JSON.stringify(before.parameters))

    binding.apply({ auth: { type: 'apiKey', name: 'X-Trace', in: 'header', credentialId: 'cred_hack' }, description: '说明C' })
    n('①i 混入的 auth 键被忽略（draft.auth 不变），同组其他键正常写入', draftObj.description === '说明C' && JSON.stringify(draftObj.auth) === JSON.stringify({ type: 'bearer', credentialId: 'cred_1' }))

    const snap2 = binding.snapshot()
    n('①j snapshot 面向客户端撤销：整草稿克隆（含 auth 与行 id）+ note', snap2.impl.auth.credentialId === 'cred_1' && snap2.impl.parameters[0].id && snap2.impl.kind === 'api' && snap2.note === '说明N2')
    draftObj.path = '手改后的地址'
    const refBefore = draftObj
    binding.restore(snap2)
    n('①k restore 原位恢复（同一对象、内容还原、说明一并恢复）', draftObj === refBefore && draftObj.path === 'https://b.example.com/' && draftObj.auth.credentialId === 'cred_1' && note === '说明N2')

    const readonly = actionBindingAssistBinding({ projectId: 'p1', targetId: '', draft: () => null, note: () => '', setNote() {} })
    n('①l 只读态（草稿 null）兜底：快照空、apply/restore/snapshot 不抛错', JSON.stringify(readonly.draft()) === '{}' && readonly.snapshot() === null && readonly.apply({ path: 'x' }) === undefined && readonly.restore({ impl: { path: 'x' } }) === undefined)
    const fallback = actionBindingAssistBinding({ projectId: 'p1', targetId: '', draft: () => draftObj, note: () => '', setNote() {} })
    n('①m 缺省 contextTitle 非空', typeof fallback.contextTitle === 'string' && fallback.contextTitle.length > 0)
  }

  // ── ②~④ 已配置动作（api 绑定）：入口/上下文/采纳不保存/撤销/手改过期 ──────────
  {
    const ctx = await mountAB()
    let page = await ctx.html()
    n('②a 列表态无辅助入口与弹窗', !page.includes('辅助填写') && !page.includes('ab-dialog'))

    ctx.api.openEditor(ctx.rowOf('act_stop'))
    page = await ctx.html()
    n('②b 编辑弹窗打开且标题行有「✦ 辅助填写」入口', page.includes('ab-dialog') && page.includes('✦ 辅助填写'))
    ctx.api.toggleAssist()
    page = await ctx.html()
    n('②c 点击后面板出现（assist-panel）', page.includes('assist-panel'))
    const binding = ctx.api.assistBinding.value
    n('②d binding 指向已保存动作目标（project/actionBinding/项目与对象:动作 id）', !!binding && binding.space === 'project' && binding.projectId === 'p_assist_t10' && binding.targetKind === 'actionBinding' && binding.targetId === 'cluster:act_stop')
    n('②e 兜底标题带动作名', typeof binding.contextTitle === 'string' && binding.contextTitle.includes('停机'))
    const panel = ctx.panel
    await panel.open(binding)
    const body = ctx.stub.calls.context[0]
    n('②f 上下文请求：space/projectId/targetKind/targetId/purpose', body.space === 'project' && body.projectId === 'p_assist_t10' && body.targetKind === 'actionBinding' && body.targetId === 'cluster:act_stop' && body.purpose === 'fill')
    n('②g 上下文 draft 恰为白名单 6 键', JSON.stringify(Object.keys(body.draft).sort()) === JSON.stringify(['bodyFormat', 'description', 'method', 'note', 'parameters', 'path']), JSON.stringify(Object.keys(body.draft).sort()))
    n('②h 出网快照绝无 auth', !JSON.stringify(body.draft).includes('auth'))
    n('②i 参数行出网结构（无行 id）', JSON.stringify(body.draft.parameters) === JSON.stringify([{ name: 'deviceId', in: 'path', value: { from: 'instanceId' } }]), JSON.stringify(body.draft.parameters))
    n('②j note/当前值随快照出网', body.draft.note === '停机接口的项目说明。' && body.draft.method === 'POST' && body.draft.path === 'https://ems.example.com/api/devices/{deviceId}/stop')

    ctx.stub.genQueue.push({ suggestions: [
      { id: 's1', label: '地址与方式', fieldKeys: ['path', 'method'], proposed: { path: 'https://ems2.example.com/api/v1/devices/{deviceId}/stop', method: 'PUT' }, state: 'ready' },
      { id: 's2', label: '请求参数', fieldKeys: ['parameters'], proposed: { parameters: [
        { name: 'deviceId', in: 'path', value: { from: 'instanceId' } },
        { name: 'force', in: 'query', value: { from: 'constant', type: 'boolean', value: 'true' } },
      ] }, state: 'ready' },
      { id: 's3', label: '项目说明', fieldKeys: ['note'], proposed: { note: '通过 EMS 停机接口下发停机指令。' }, state: 'ready' },
      { id: 's4', label: '越权认证+接口说明', fieldKeys: ['auth', 'description'], proposed: { auth: { type: 'apiKey', name: 'X-Trace', in: 'header', credentialId: 'cred_hack' }, description: '新的接口说明' }, state: 'ready' },
    ] })
    await panel.generate()
    n('③a generate 携带白名单快照与 contextToken', ctx.stub.calls.generate.length === 1 && ctx.stub.calls.generate[0].contextToken === 'tok-1' && !JSON.stringify(ctx.stub.calls.generate[0].draft).includes('auth'))
    const authBeforeAdopt = clone(ctx.api.draft.value.auth) // 弹窗草稿里的认证（draftFrom 视图形态）
    const adopted = panel.adopt()
    n('③b 采纳成功', adopted === true)
    n('③c path/method 写回草稿', ctx.api.draft.value.path === 'https://ems2.example.com/api/v1/devices/{deviceId}/stop' && ctx.api.draft.value.method === 'PUT')
    n('③d description 写回（同组混入越权 auth 键）', ctx.api.draft.value.description === '新的接口说明')
    n('③e 红线：auth 未被建议改写', JSON.stringify(ctx.api.draft.value.auth) === JSON.stringify(authBeforeAdopt) && !JSON.stringify(ctx.api.draft.value.auth).includes('cred_hack') && ctx.api.draft.value.auth.type === 'bearer', JSON.stringify(ctx.api.draft.value.auth))
    n('③f parameters 整组替换且行 id 由 newParamId 补齐', ctx.api.draft.value.parameters.length === 2
      && ctx.api.draft.value.parameters.every(p => typeof p.id === 'string' && /^p_/.test(p.id) && p.id !== 'p_old1')
      && new Set(ctx.api.draft.value.parameters.map(p => p.id)).size === 2, JSON.stringify(ctx.api.draft.value.parameters))
    n('③g 参数行内容与建议一致', JSON.stringify(ctx.api.draft.value.parameters.map(p => ({ name: p.name, in: p.in, value: p.value }))) === JSON.stringify([
      { name: 'deviceId', in: 'path', value: { from: 'instanceId' } },
      { name: 'force', in: 'query', value: { from: 'constant', type: 'boolean', value: 'true' } },
    ]))
    n('③h note 写回说明草稿', ctx.api.noteDraft.value === '通过 EMS 停机接口下发停机指令。')
    n('③i 采纳绝不触发表单保存/commit-now（间谍零调用）', ctx.saves.length === 0 && ctx.commitNows.length === 0)
    n('③j 面板出现「尚未保存」提示且可撤销', ctx.panel.justAdopted.value === true && ctx.panel.canUndo.value === true && (await ctx.panelHtml()).includes('已填入表单，尚未保存'))

    const undone = panel.undo()
    n('③k 撤销恢复采纳前草稿（含参数行 id 与 auth）', undone === true
      && ctx.api.draft.value.path === 'https://ems.example.com/api/devices/{deviceId}/stop' && ctx.api.draft.value.method === 'POST'
      && JSON.stringify(ctx.api.draft.value.parameters) === JSON.stringify([{ id: 'p_old1', name: 'deviceId', in: 'path', value: { from: 'instanceId' } }])
      && JSON.stringify(ctx.api.draft.value.auth) === JSON.stringify(authBeforeAdopt))
    n('③l 撤销同时恢复说明草稿', ctx.api.noteDraft.value === '停机接口的项目说明。')

    // 第二轮生成→采纳：让「已填入」提示与撤销保护处于在位状态，再验证手改过期链路
    ctx.stub.genQueue.push({ suggestions: [
      { id: 's5', label: '接口说明二稿', fieldKeys: ['description'], proposed: { description: '第二次建议的说明' }, state: 'ready' },
    ] })
    await panel.generate()
    n('④-0 再次采纳可用且提示在位', panel.adopt() === true && ctx.api.draft.value.description === '第二次建议的说明' && panel.justAdopted.value === true && panel.canUndo.value === true)

    ctx.api.draft.value.path = 'https://hand.example.com/api/stop' // 等价表单手改（同一草稿对象）
    await settle()
    n('④a 面板读到漂移后的草稿', binding.draft().path === 'https://hand.example.com/api/stop')
    n('④b 指纹漂移：syncStaleness 报过期并解除撤销保护', panel.syncStaleness() === true && panel.canUndo.value === false && panel.undo() === false)
    n('④c 手改后采纳被拦（草稿不被建议值覆盖）', panel.adopt() === false && ctx.api.draft.value.path === 'https://hand.example.com/api/stop')
    n('④d 「已填入」提示等待手改通知清除', panel.justAdopted.value === true)
    panel.notifyDraftChanged() // 与 watch 同一入口（SSR 模板 ref 为 null，这里直驱验证其效果）
    n('④e 显式过期通知：置过期并清「已填入」提示', panel.stale.value === true && panel.justAdopted.value === false)
    const src = readFileSync(resolve('frontend/src/project/ActionBindings.vue'), 'utf8')
    n('④f 源码含手改→notifyDraftChanged 的 watch 接线', src.includes('assistPanel.value?.notifyDraftChanged()'))

    // ── 关闭弹窗（closeNow：确认放弃/守卫丢弃同一路径）：面板一并收起 ──
    ctx.api.closeNow()
    n('⑤a 关闭弹窗：面板与 binding 一并收起', ctx.api.assistVisible.value === false && ctx.api.assistBinding.value === null && ctx.api.meta.value === null)
    page = await ctx.html()
    n('⑤b 收起后无弹窗/面板/入口', !page.includes('ab-dialog') && !page.includes('assist-panel') && !page.includes('辅助填写'))
  }

  // ── ⑥ 切换动作目标重开（新建 / 只读 flow / 已配置目标）──────────────────────
  {
    const ctx = await mountAB()
    ctx.api.openEditor(ctx.rowOf('act_scan')) // 未配置动作：新建接口配置
    let page = await ctx.html()
    n('⑥a 新建配置弹窗同样有辅助入口', page.includes('✦ 辅助填写'))
    ctx.api.toggleAssist()
    page = await ctx.html()
    n('⑥b 新建配置面板出现', page.includes('assist-panel'))
    const bindingNew = ctx.api.assistBinding.value
    n('⑥c 未保存配置目标 id 为空串，按「新建动作接口映射」出兜底标题', bindingNew.targetId === '' && bindingNew.contextTitle === '新建动作接口映射')
    const panelNew = ctx.panel
    await panelNew.open(bindingNew)
    const bodyNew = ctx.stub.calls.context[0]
    n('⑥d 新建目标上下文：targetId 空、草稿为空配置的白名单形态', bodyNew.targetId === '' && JSON.stringify(bodyNew.draft) === JSON.stringify({ method: 'POST', path: '', bodyFormat: 'json', description: '', parameters: [], note: '' }), JSON.stringify(bodyNew.draft))

    await ctx.api.closeEditor() // 干净关闭（无脏表单，不触发确认）：面板随弹窗收起
    n('⑥e closeEditor 关闭弹窗收起面板', ctx.api.assistVisible.value === false && ctx.api.assistBinding.value === null && ctx.api.meta.value === null)
    page = await ctx.html()
    n('⑥f 关闭后无面板与入口', !page.includes('assist-panel') && !page.includes('✦ 辅助填写'))

    ctx.api.openEditor(ctx.rowOf('act_reset')) // 历史 flow：只读查看
    page = await ctx.html()
    n('⑥g 只读查看（历史 flow）无辅助入口、无面板', !page.includes('✦ 辅助填写') && !page.includes('assist-panel') && ctx.api.editable.value === false)
    n('⑥h 只读态未构建辅助 binding', ctx.api.assistBinding.value === null && ctx.api.assistVisible.value === false)

    ctx.api.openEditor(ctx.rowOf('act_stop')) // 切回已配置 api 动作：重开辅助
    ctx.api.toggleAssist()
    await ctx.html()
    const bindingStop = ctx.api.assistBinding.value
    const panelStop = ctx.panel
    await panelStop.open(bindingStop)
    const bodyStop = ctx.stub.calls.context.at(-1)
    n('⑥i 切换动作目标重开：binding 指向新目标（对象：动作）', bindingStop.targetId === 'cluster:act_stop' && bindingStop !== bindingNew)
    n('⑥j 新目标重新取上下文（草稿为该动作现状）', bodyStop.targetId === 'cluster:act_stop' && bodyStop.draft.method === 'POST' && bodyStop.draft.parameters.length === 1 && bodyStop.draft.note === '停机接口的项目说明。')
  }
} finally {
  try { rmSync(root, { recursive: true, force: true }) } catch { /* 临时目录 */ } finally { globalThis.fetch = realFetch }
}

const failed = results.filter(r => !r.ok)
console.log(`\n统计：${results.length - failed.length}/${results.length} 项通过`)
if (failed.length) { console.log('未通过：' + failed.map(f => f.name).join('；')); process.exit(1) }
