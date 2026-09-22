// P6 动作接口映射整表自动填写接入测试（2026-09-22 T8 改版）。运行（仓库根）：
//   node --import ./tests/ts_hooks.mjs tests/assist_action_bindings.test.mjs
// 模式：SFC 编译（assist_property_manager 同款）——ActionBindings.vue 挂真实 AssistPanel（T3 状态机），
// AppSelect/EditorField/MappingDescription 桩化；assist api 经 provide('assist-api') 注入合成桩，
// 全程禁网（fetch 一律抛错，且额外统计实际网络调用次数，断言自动填写链路绝不执行接口）。
// 覆盖（新交互，旧「建议卡→勾选→采纳」断言随改版移除）：
//   ① 工厂白名单快照（恰 6 键、绝无 auth、参数行 {rowId,name,in,value} 出网结构、applyDraft 合并）；
//   ② parameters 经 actionParamRows codec 精确落行：row.append（localId 冲突/缺失由 newParamId 补齐）、
//      row.update/row.remove 按 rowId 定位、未命中行抛错不中断其余操作、未涉及行与未知键零丢失（A13）；
//   ③ auth.* 敏感字段：draft 绝不出网 + 写入被拒并记入 refusals（契约 ai.sensitive 双保险）；
//   ④ 弹窗入口与抽屉、上下文请求体（space/projectId/targetId/draft 白名单）、回填只改本地草稿
//      （form-save/commit-now 间谍零调用、无任何网络调用）、状态条「已填写 N 项，尚未保存」、
//      整轮撤销（含 auth 与参数行 id 保真）、手改禁撤销、关闭弹窗收起；
//   ⑤ 切换动作目标重开（未保存新建 targetId 空、只读 flow 无入口、已配置目标重取上下文）。
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
const networkCalls = [] // 全程禁网：辅助/凭据/编排 API 一律走桩；此数组用于断言自动填写链路零网络调用
globalThis.fetch = async (url) => { networkCalls.push(String(url)); throw new Error('测试不应发起真实请求：' + String(url)) }
globalThis.XMLHttpRequest = function () { return { open() { networkCalls.push('xhr'); }, send() { throw new Error('测试不应发起真实请求') } } }

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

const { FORM_SCHEMA_DIGESTS } = await import('../frontend/src/assist/formContracts.gen.ts')
const { descTextOf: descTextOfOf } = await import('../frontend/src/project/bindingModel.ts')

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
    projectId: 'p_assist_t8',
    ontologyVersion: 'r1',
    bindings: {
      object_bindings: [{ object_type: 'cluster', identity: { kind: 'registered' }, properties: {} }],
      actionBindings: [
        { id: 'ab_stop', objectTypeId: 'cluster', actionId: 'act_stop', implementation: {
          kind: 'api', schemaVersion: 2, method: 'POST', path: 'https://ems.example.com/api/devices/{deviceId}/stop',
          bodyFormat: 'json', description: '旧接口说明',
          parameters: [
            { id: 'p_old1', name: 'deviceId', in: 'path', value: { from: 'instanceId' } },
            { id: 'p_old2', name: 'trace', in: 'header', value: { from: 'property', propertyId: 'mg:p_x' }, paramNote: '历史备注' },
            { id: 'p_old3', name: 'verbose', in: 'query', value: { from: 'constant', type: 'boolean', value: 'true' } },
          ],
          auth: { type: 'bearer', credentialId: 'cred_1' },
          roles: ['历史字段'],
        } },
        { id: 'ab_reset', objectTypeId: 'cluster', actionId: 'act_reset', implementation: { kind: 'flow', flowId: 'fl_legacy' } },
      ],
      mappingDescriptions: { schemaVersion: 1, actions: { cluster: { act_stop: '停机接口的项目说明。' } } },
    },
  }
}

// ── assist-api 桩：autofill/1 fill 响应内存队列；context/generate 记录请求体 ────────
const setOp = (field, value) => ({ op: 'set', field, value, basis: { kind: 'intent', quote: '测试依据' } })
function stubAssistApi() {
  const calls = { context: [], generate: [] }
  const fillQueue = []
  let n = 0
  const lastTarget = { targetKind: 'actionBinding', targetId: '' }
  return {
    calls, fillQueue,
    api: {
      async context(body) {
        calls.context.push(JSON.parse(JSON.stringify(body)))
        lastTarget.targetKind = body.targetKind
        lastTarget.targetId = body.targetId
        return { contextToken: 'tok-' + calls.context.length, contextFingerprint: 'fp-' + calls.context.length,
          context: { targetKind: body.targetKind, title: 'CTX[actionBinding:' + body.targetId + ']', editableFields: [], definitions: [], catalog: [], flows: [], modelReady: true } }
      },
      async generate(body) {
        calls.generate.push(JSON.parse(JSON.stringify(body)))
        const over = fillQueue.shift()
        if (!over) throw Object.assign(new Error('测试桩未编排该请求'), { data: { code: 'UNPLANNED' } })
        n++
        return Object.assign({
          protocol: 'autofill/1', status: 'ok', requestId: 'srv-' + n, formId: 'actionBinding',
          schemaVersion: 1, schemaDigest: FORM_SCHEMA_DIGESTS.actionBinding,
          target: { space: 'project', targetKind: lastTarget.targetKind, targetId: lastTarget.targetId },
          draftFingerprint: 'dfp-' + n, contextFingerprint: 'cfp-1',
          sessionId: 's-' + n, roundId: 'r-' + n,
          operations: [], questions: [], unresolved: [], summary: '',
          meta: { durationMs: 1, provider: '测试桩', model: 'stub' },
        }, over)
      },
    },
  }
}

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
  assert.ok(slot.api && typeof slot.api.openEditor === 'function' && typeof slot.api.toggleAssist === 'function', 'setup 应暴露弹窗与自动填写接线（openEditor/toggleAssist）')
  return {
    slot, stub, saves, commitNows, projectState: propsObj.projectState, refState: propsObj.refState,
    // 用同一份 setup 状态重渲染页面（不重跑 setup；面板实例随每次渲染重建并捕获）
    html: () => { activeSlot = slot; return renderToString(createSSRApp({ props: ABComponent.props, ssrRender: ABComponent.ssrRender, setup: () => slot.api }, propsObj)) },
    // 用驱动后的面板状态单独渲染抽屉（验证标题/问题卡等 UI 呈现）
    panelHtml: () => renderToString(createSSRApp({ props: Panel.props, ssrRender: Panel.ssrRender, setup: () => slot.panel }, { binding: slot.api.assistBinding.value })),
    rowOf: actionId => slot.api.rows.value.find(r => r.actionId === actionId),
    get api() { return slot.api },
    get panel() { return slot.panel },
  }
}

const results = []
async function check(name, fn) { try { await fn(); results.push(true); console.log('通过：' + name) } catch (e) { results.push(false); console.log('失败：' + name + ' — ' + (e && e.message)) } }
const clone = (x) => JSON.parse(JSON.stringify(x))

try {
  // ── ① 工厂单测：白名单快照 / 参数行 codec / auth 拒绝 / 撤销保真 ──────────────
  {
    const { actionBindingAssistBinding, ACTION_BINDING_SENSITIVE_REASON } = await import('../frontend/src/assist/actionBindingAdapter.ts')
    await check('①a binding 目标描述与契约指纹（project/actionBinding/formId/contractInfo）', async () => {
      const draftObj = { kind: 'api', schemaVersion: 2, method: 'POST', path: '', bodyFormat: 'json', description: '', parameters: [], auth: { type: 'none' } }
      const binding = actionBindingAssistBinding({ projectId: 'p1', targetId: 'cluster:act_stop', contextTitle: '动作「停机」的接口映射', draft: () => draftObj, note: () => '', setNote() {} })
      assert.equal(binding.space, 'project'); assert.equal(binding.projectId, 'p1'); assert.equal(binding.targetKind, 'actionBinding')
      assert.equal(binding.targetId, 'cluster:act_stop'); assert.equal(binding.contextTitle, '动作「停机」的接口映射')
      assert.equal(binding.formId, 'actionBinding')
      assert.deepEqual(binding.contractInfo(), { schemaVersion: 1, schemaDigest: FORM_SCHEMA_DIGESTS.actionBinding })
      assert.deepEqual(Object.keys(binding.codecs), ['parameters'], 'parameters 由 actionParamRows codec 托管')
      const fallback = actionBindingAssistBinding({ projectId: 'p1', targetId: '', draft: () => draftObj, note: () => '', setNote() {} })
      assert.ok(typeof fallback.contextTitle === 'string' && fallback.contextTitle.length > 0, '缺省 contextTitle 非空')
    })

    // 已配置绑定的完整参数集：出网快照 / 行操作 / auth 拒绝 / 撤销保真共用这一份草稿
    const draftObj = {
      kind: 'api', schemaVersion: 2, method: 'POST', path: 'https://a.example.com/x/{id}',
      bodyFormat: 'json', description: '说明A',
      parameters: [
        { id: 'p_old1', name: 'id', in: 'path', value: { from: 'instanceId' } },
        { id: 'p_old2', name: 'verbose', in: 'query', value: { from: 'constant', type: 'boolean', value: 'true' } },
        { id: 'p_old3', name: 'trace', in: 'header', value: { from: 'property', propertyId: 'mg:p_x' } },
      ],
      auth: { type: 'bearer', credentialId: 'cred_1' },
      roles: ['历史字段'],
    }
    let note = '说明N'
    const setNoteCalls = []
    const binding = actionBindingAssistBinding({
      projectId: 'p1', targetId: 'cluster:act_stop', contextTitle: '动作「停机」的接口映射',
      draft: () => draftObj, note: () => note, setNote: v => { setNoteCalls.push(v); note = v },
    })

    await check('①b 快照键恰为契约 6 键、绝无 auth；参数行带本地 rowId（row.update/remove 定位用）', async () => {
      const snap = binding.draft()
      assert.deepEqual(Object.keys(snap).sort(), ['bodyFormat', 'description', 'method', 'note', 'parameters', 'path'])
      assert.ok(!JSON.stringify(snap).includes('auth'), '认证凭据永不出网')
      assert.deepEqual(snap.parameters.map(r => r.rowId), ['p_old1', 'p_old2', 'p_old3'], '行带客户端本地稳定 rowId')
      assert.deepEqual(snap.parameters[2].value, { from: 'property', property: 'mg:p_x' }, '契约键 property（组件键 propertyId 的映射）')
      const snap2 = binding.draft()
      snap2.parameters[0].name = '篡改'
      assert.equal(draftObj.parameters[0].name, 'id', '快照是独立克隆（改快照不影响草稿）')
      assert.ok(!('roles' in snap), '非契约字段（roles 等）不出网')
    })

    await check('①c 引擎通道（applyOperations + applyDraft）：顶层字段回填、未涉及行与未知键零丢失、N 计实际改变数', async () => {
      const { applyOperations } = await import('../frontend/src/assist/formAutofill.ts')
      binding.snapshot()
      // 复刻引擎写回：在草稿副本上跑 operations（含 codec），再整稿 applyDraft
      const pre = clone(binding.draft())
      const next = clone(pre)
      const applied = applyOperations(next, [
        setOp('method', 'PUT'), setOp('path', 'https://b.example.com/'), setOp('bodyFormat', 'form'),
        setOp('description', '说明B'), setOp('note', '说明N2'),
        // row.update 的 fields 是**契约行形态**（服务端下发）：组件键 propertyId ↔ 契约键 property
        { op: 'row.update', field: 'parameters', rowId: 'p_old1', fields: { name: 'deviceId', in: 'header', value: { from: 'property', property: 'mg:p_x' } }, basis: { kind: 'intent', quote: '测试依据' } },
      ], binding.codecs)
      assert.deepEqual(applied.failures, [])
      binding.applyDraft(next)
      assert.equal(draftObj.method, 'PUT'); assert.equal(draftObj.path, 'https://b.example.com/'); assert.equal(draftObj.bodyFormat, 'form')
      assert.equal(draftObj.description, '说明B'); assert.equal(note, '说明N2')
      assert.equal(draftObj.parameters.length, 3, '未被提及的行不丢失')
      assert.deepEqual(draftObj.parameters.map(p => [p.id, p.name]), [['p_old1', 'deviceId'], ['p_old2', 'verbose'], ['p_old3', 'trace']])
      assert.equal(draftObj.parameters[0].in, 'header', '指定行按 rowId 更新（只改该行的字段）')
      assert.deepEqual(draftObj.parameters[0].value, { from: 'property', propertyId: 'mg:p_x' }, '契约键 property 落回组件键 propertyId')
      assert.deepEqual(draftObj.parameters[2].value, { from: 'property', propertyId: 'mg:p_x' }, '未涉及行原样保留（键形不变）')
      assert.deepEqual(draftObj.roles, ['历史字段'], '弹窗草稿的未知顶层键零丢失')
      assert.equal(draftObj.auth.type, 'bearer', 'auth 不被回填触碰')
      assert.equal(binding.round.statusBarText.replace(/；另有.*/, ''), '已填写 6 项，尚未保存', 'N=实际改变的顶层字段数（method/path/bodyFormat/description/note/parameters）')
      assert.deepEqual([...binding.round.changes.map(c => c.field)].sort(), ['bodyFormat', 'description', 'method', 'note', 'parameters', 'path'], '逐字段旧值→新值（覆盖实际改变的 6 个顶层字段）')
      const labels = Object.fromEntries(binding.round.changes.map(c => [c.field, c.label]))
      assert.deepEqual(labels, { method: '请求方式', path: '接口地址', bodyFormat: '请求体格式', description: '接口说明', parameters: '请求参数', note: '说明' }, '字段标签取自契约')
    })

    await check('①d 参数行 codec：row.update/row.remove 按 rowId 精确落行、row.append（localId 补齐/去重）、未命中行失败不中断', async () => {
      const { applyOperations } = await import('../frontend/src/assist/formAutofill.ts')
      const d = {
        method: 'POST', path: '', bodyFormat: 'json', description: '', note: '',
        parameters: [
          { rowId: 'p_a', name: 'a', in: 'body', value: { from: 'instanceId' } },
          { rowId: 'p_b', name: 'b', in: 'query', value: { from: 'constant', type: 'string', value: 'x' } },
        ],
      }
      const out = applyOperations(d, [
        { op: 'row.update', field: 'parameters', rowId: 'p_b', fields: { name: 'b2', in: 'header' }, basis: { kind: 'intent', quote: 'x' } },
        { op: 'row.append', field: 'parameters', row: { localId: 'r_new', fields: { name: 'c', in: 'body', value: { from: 'constant', valueType: 'number', value: '1' } } }, basis: { kind: 'intent', quote: 'x' } },
        { op: 'row.append', field: 'parameters', row: { localId: 'p_a', fields: { name: 'dup', in: 'body', value: { from: 'instanceId' } } }, basis: { kind: 'intent', quote: 'x' } },
        { op: 'row.remove', field: 'parameters', rowId: 'p_zzz', basis: { kind: 'intent', quote: 'x' } },
        { op: 'row.update', field: 'parameters', rowId: 'p_zzz', fields: { name: 'nope' }, basis: { kind: 'intent', quote: 'x' } },
      ], binding.codecs)
      assert.equal(d.parameters.length, 4, '两次 append 各落一行；失败的行操作不产生变更')
      assert.deepEqual(d.parameters.map(p => [p.rowId, p.name, p.in]), [['p_a', 'a', 'body'], ['p_b', 'b2', 'header'], ['r_new', 'c', 'body'], [d.parameters[3].rowId, 'dup', 'body']])
      assert.equal(d.parameters[1].in, 'header', 'row.update 只改指定行')
      assert.deepEqual(d.parameters[2].value, { from: 'constant', type: 'number', value: '1' }, 'valueType 落回组件键 type')
      assert.notEqual(d.parameters[3].rowId, 'p_a', 'row.append 的 localId 与现有行冲突：由 newParamId 补齐，不复用模型给的 id')
      assert.ok(String(d.parameters[3].rowId).startsWith('p_'), '补齐后的行 id 与组件 addParam 同一机制')
      assert.equal(out.failures.length, 2, '未命中 rowId 的 row.remove/row.update 各记一条失败（不中断其他操作）')
      assert.match(out.failures[0].reason, /未找到参数行/)

      // row.remove 命中：精确删行，剩余行与行 id 不变
      const out2 = applyOperations(d, [{ op: 'row.remove', field: 'parameters', rowId: 'p_b', basis: { kind: 'intent', quote: 'x' } }], binding.codecs)
      assert.deepEqual(out2.failures, [])
      assert.deepEqual(d.parameters.map(p => p.rowId), ['p_a', 'r_new', d.parameters[2].rowId], '按 rowId 精确删行')
      // set 整组：显式整组替换（服务端授权下发），行 id 缺失时按本地机制补齐
      applyOperations(d, [{ op: 'set', field: 'parameters', value: [{ name: 'only', in: 'path', value: { from: 'instanceId' } }], basis: { kind: 'intent', quote: 'x' } }], binding.codecs)
      assert.equal(d.parameters.length, 1)
      assert.equal(d.parameters[0].name, 'only')
      assert.ok(String(d.parameters[0].rowId).startsWith('p_'), '整组替换的行 id 由组件机制补齐')
    })

    await check('①e auth.* 敏感字段：草稿拒绝写入并记入 refusals（契约 ai.sensitive 双保险），其余键照常', async () => {
      binding.resetRefusals()
      const authBefore = clone(draftObj.auth)
      binding.snapshot()
      binding.applyDraft({ auth: { type: 'apiKey', name: 'X-Trace', in: 'header', credentialId: 'cred_hack' }, description: '说明C' })
      assert.deepEqual(draftObj.auth, authBefore, 'auth 未被写入（draft.auth 不变）')
      assert.equal(draftObj.description, '说明C', '同批其他键正常写入')
      assert.equal(binding.refusals.value.length, 1)
      assert.equal(binding.refusals.value[0].field, 'auth')
      assert.equal(binding.refusals.value[0].reason, ACTION_BINDING_SENSITIVE_REASON)
      binding.apply({ 'auth.credentialId': 'cred_hack2' })
      assert.equal(binding.refusals.value.length, 2, '点路径形态的 auth.* 同样拒绝并记录')
      assert.notEqual(draftObj.auth.credentialId, 'cred_hack2')
      assert.deepEqual(Object.keys(binding.draft()).includes('auth'), false, '快照仍无 auth')
      binding.resetRefusals()
      assert.equal(binding.refusals.value.length, 0, 'resetRefusals 清空记录')
    })

    await check('①f 撤销保真：整草稿克隆（含 auth、行 id、未知键）+ 说明；undoRound 一次性、手改禁撤销', async () => {
      const snap = binding.snapshot()
      assert.equal(snap.impl.auth.credentialId, 'cred_1'); assert.ok(snap.impl.parameters[0].id)
      assert.equal(snap.impl.kind, 'api'); assert.equal(snap.note, '说明N2')
      draftObj.path = '手改后的地址'
      const refBefore = draftObj
      binding.restore(snap)
      assert.ok(draftObj === refBefore, 'restore 原位恢复（同一对象）')
      assert.equal(draftObj.path, 'https://b.example.com/'); assert.equal(draftObj.auth.credentialId, 'cred_1'); assert.equal(note, '说明N2')

      // 宿主整轮撤销：回到本轮起点（快照在写入前 cloneJson，起点保真）
      const startPath = draftObj.path
      const startNames = draftObj.parameters.map(p => p.name)
      binding.snapshot()
      binding.applyDraft({ path: 'https://c.example.com/', parameters: [{ name: 'only', in: 'path', valueType: undefined, value: undefined, rowId: 'r_only' }] })
      assert.equal(draftObj.path, 'https://c.example.com/')
      assert.deepEqual(draftObj.parameters.map(p => p.name), ['only'])
      assert.equal(binding.round.canUndo, true)
      assert.equal(binding.undoRound(), true)
      assert.equal(draftObj.path, startPath, '恢复本轮开始前草稿')
      assert.deepEqual(draftObj.parameters.map(p => p.name), startNames, '参数行（含行 id）一并恢复')
      assert.deepEqual(draftObj.parameters.map(p => p.id), ['p_old1', 'p_old2', 'p_old3'])

      binding.snapshot()
      binding.applyDraft({ description: '新一轮' })
      binding.noteManualChange()
      assert.equal(binding.round.canUndo, false)
      assert.equal(binding.undoRound(), false, '手改后整轮撤销被拒（不覆盖用户改动）')
      assert.equal(draftObj.description, '新一轮')

      const readonly = actionBindingAssistBinding({ projectId: 'p1', targetId: '', draft: () => null, note: () => '', setNote() {} })
      assert.deepEqual(readonly.draft(), {}); assert.equal(readonly.snapshot(), null)
      assert.equal(readonly.undoRound(), false)
      readonly.applyDraft({ path: 'x' }); readonly.restore({ impl: { path: 'x' } }); readonly.observeFill(null)
      assert.ok(true, '只读态（草稿 null）全部通道安全')
    })
  }

  // ── ②~④ 已配置动作（api 绑定）：入口/上下文/回填不保存/撤销/手改 ─────────────
  // 注意：SSR 每次 ctx.html() 重渲染都会重建面板实例（模板 ref 拿不到），所以「驱动面板」必须先
  // 捕获当前实例并在渲染前完成；需要再次驱动时重新 open（与 T5 套件同一手法）。
  {
    const ctx = await mountAB()
    let page = await ctx.html()
    await check('②a 列表态无入口与弹窗；打开弹窗后页头出现「✦ 自动填写」次要入口（aria）', async () => {
      assert.ok(!page.includes('自动填写') && !page.includes('ab-dialog'), '列表态无自动填写入口与弹窗')
      ctx.api.openEditor(ctx.rowOf('act_stop'))
      page = await ctx.html()
      assert.ok(page.includes('ab-dialog'), '弹窗打开')
      assert.match(page, /✦ 自动填写/)
      assert.match(page, /aria-expanded="false"/); assert.match(page, /aria-haspopup="dialog"/)
      assert.ok(page.includes('id="ab-assist-trigger"'), '入口按钮 id（面板 triggerId 指向它）')
      assert.ok(!page.includes('assist-drawer'), '默认不渲染任何 AI 区域')
      assert.ok(!page.includes('勾选') && !page.includes('采纳'), '旧勾选/采纳 UI 不再出现')
    })

    await check('②b 点开入口：抽屉挂载、上下文请求体正确、草稿白名单且绝无 auth', async () => {
      ctx.api.toggleAssist()
      page = await ctx.html()
      assert.equal(ctx.api.assistVisible.value, true, '入口点击后抽屉挂载')
      const binding = ctx.api.assistBinding.value
      assert.ok(binding, '挂载后 binding 就位')
      assert.equal(binding.space, 'project'); assert.equal(binding.targetKind, 'actionBinding')
      assert.equal(binding.projectId, 'p_assist_t8'); assert.equal(binding.targetId, 'cluster:act_stop')
      assert.match(binding.contextTitle, /停机/, '兜底标题带动作名')
      const panel = ctx.panel
      await panel.open(binding) // 浏览器里挂载即取上下文；SSR 下由测试显式驱动
      assert.match(await ctx.panelHtml(), /CTX\[actionBinding:cluster:act_stop\]/, '展开后抽屉出现并显示服务端标题')
      const body = ctx.stub.calls.context[0]
      assert.equal(body.space, 'project'); assert.equal(body.projectId, 'p_assist_t8')
      assert.equal(body.targetKind, 'actionBinding'); assert.equal(body.targetId, 'cluster:act_stop'); assert.equal(body.purpose, 'fill')
      assert.deepEqual(Object.keys(body.draft).sort(), ['bodyFormat', 'description', 'method', 'note', 'parameters', 'path'], '上下文 draft 恰为契约 6 键')
      assert.ok(!JSON.stringify(body.draft).includes('auth'), '出网快照绝无 auth')
      assert.deepEqual(body.draft.parameters.map(r => r.rowId), ['p_old1', 'p_old2', 'p_old3'], '参数行带本地 rowId 供 row.update/remove 定位')
      assert.equal(body.draft.note, '停机接口的项目说明。')
      assert.equal(body.draft.path, 'https://ems.example.com/api/devices/{deviceId}/stop')
      page = await ctx.html()
      assert.match(page, /aria-expanded="true"/, '抽屉展开时入口 aria-expanded=true（trigger-id 反向接线）')
      assert.ok(page.includes('id="ab-assist-trigger"'), '入口按钮 id 与面板 aria-controls 反向接线')
    })

    await check('②c 生成→直接回填：URL/方法回填、参数行按 rowId 更新（未涉及行保留）、form-save/commit-now 零调用、无任何网络调用', async () => {
      const panel = ctx.panel
      await panel.open(ctx.api.assistBinding.value)
      const token = 'tok-' + ctx.stub.calls.context.length // 桩按调用次数签发：最近一次签发的令牌即此值
      const netBefore = networkCalls.length
      ctx.stub.fillQueue.push({ operations: [
        setOp('method', 'PUT'),
        setOp('path', 'https://ems2.example.com/api/v1/devices/{deviceId}/stop'),
        { op: 'row.update', field: 'parameters', rowId: 'p_old1', fields: { name: 'deviceId', in: 'path', value: { from: 'instanceId' } }, basis: { kind: 'intent', quote: '测试依据' } },
        { op: 'row.append', field: 'parameters', row: { localId: 'r_force', fields: { name: 'force', in: 'query', value: { from: 'constant', valueType: 'boolean', value: 'true' } } }, basis: { kind: 'intent', quote: '测试依据' } },
        setOp('description', '通过 EMS 停机接口下发停机指令。'),
        setOp('note', '通过 EMS 停机接口下发停机指令。'),
      ] })
      await panel.generate()
      const gen1 = ctx.stub.calls.generate.at(-1)
      assert.ok(gen1, 'generate 已发出')
      assert.equal(gen1.mode, 'fill'); assert.equal(gen1.protocol, 2, 'autofill/1 请求必带 protocol:2')
      assert.equal(gen1.contextToken, token, 'generate 携带最近一次上下文令牌')
      assert.ok(!JSON.stringify(gen1.draft).includes('auth'), 'generate 草稿同样无 auth')
      const d = ctx.api.draft.value
      assert.equal(d.method, 'PUT', '请求方式回填')
      assert.equal(d.path, 'https://ems2.example.com/api/v1/devices/{deviceId}/stop', '接口地址回填')
      assert.equal(d.description, '通过 EMS 停机接口下发停机指令。')
      assert.equal(ctx.api.noteDraft.value, '通过 EMS 停机接口下发停机指令。', '说明回填写入 noteDraft')
      assert.deepEqual(d.parameters.map(p => [p.id, p.name, p.in]), [['p_old1', 'deviceId', 'path'], ['p_old2', 'trace', 'header'], ['p_old3', 'verbose', 'query'], ['r_force', 'force', 'query']], '指定行按 rowId 更新、追加行在末尾、未涉及行原样保留（A13）')
      assert.equal(d.parameters[1].value.propertyId, 'mg:p_x', '未涉及行的取值来源未被改写')
      assert.equal(d.parameters[3].id, 'r_force', '追加行沿用服务端下发的 localId（续轮 row.update/remove 定位稳定；与现有行冲突时才由组件机制补齐）')
      assert.equal(d.auth.type, 'bearer'); assert.equal(d.auth.credentialId, 'cred_1', '认证未被回填改写')
      assert.equal(ctx.saves.length, 0, '回填绝不触发表单保存（form-save 零调用）')
      assert.equal(ctx.commitNows.length, 0, '回填绝不触发 commit-now')
      assert.equal(networkCalls.length, netBefore, '自动填写链路绝不执行接口/发起网络请求')
      assert.equal(panel.status.value, 'done')
      assert.equal(panel.collapsed.value, true, '一次回填完成抽屉自动收起（浏览器里入口 aria-expanded 随 ref 回落）')
      const b = ctx.api.assistBinding.value
      assert.equal(b.round.statusBarText, '已填写 5 项，尚未保存', 'N=实际改变的顶层字段数：method/path/parameters/description/note')
      assert.equal(b.round.statusBarText, panel.statusBarText.value, '宿主镜像与引擎状态条口径一致')
      assert.equal(b.round.canUndo, true)
      page = await ctx.html()
      assert.match(page, /已填写 5 项，尚未保存/); assert.match(page, /撤销本次填写/); assert.match(page, /查看修改/)
    })

    await check('②d 宿主撤销：恢复本轮开始前草稿（含参数行 id 与 auth），一次性、零保存', async () => {
      const b = ctx.api.assistBinding.value
      assert.equal(b.undoRound(), true)
      const d = ctx.api.draft.value
      assert.equal(d.method, 'POST'); assert.equal(d.path, 'https://ems.example.com/api/devices/{deviceId}/stop')
      assert.deepEqual(d.parameters.map(p => [p.id, p.name]), [['p_old1', 'deviceId'], ['p_old2', 'trace'], ['p_old3', 'verbose']])
      assert.equal(d.auth.type, 'bearer'); assert.equal(d.auth.credentialId, 'cred_1', '认证引用未被回填/撤销改写')
      assert.equal(ctx.api.noteDraft.value, '停机接口的项目说明。')
      assert.equal(ctx.saves.length, 0); assert.equal(ctx.commitNows.length, 0)
      assert.equal(b.round.undone, true); assert.equal(b.undoRound(), false, '一次性')
      page = await ctx.html()
      assert.match(page, /已撤销本次自动填写，表单已恢复。/)
    })

    await check('②e 手改禁撤销：noteManualChange 生效、undoRound 拒绝、草稿保留手改值', async () => {
      const panel = ctx.panel
      await panel.open(ctx.api.assistBinding.value) // 渲染后面板实例重建：显式重开
      ctx.stub.fillQueue.push({ operations: [setOp('description', '第二次填写的说明')] })
      await panel.generate()
      const b = ctx.api.assistBinding.value
      assert.equal(b.round.statusBarText, '已填写 1 项，尚未保存')
      // 等价表单手改（同一草稿对象）+ 宿主手改入口（浏览器里由草稿指纹 watch 触发同一函数）
      ctx.api.draft.value.path = 'https://hand.example.com/api/stop'
      ctx.api.assistTouched()
      assert.equal(b.round.canUndo, false, '手改后禁整轮撤销')
      assert.equal(b.undoRound(), false, '撤销被拒')
      assert.equal(ctx.api.draft.value.path, 'https://hand.example.com/api/stop', '手改值保留')
      assert.equal(ctx.api.draft.value.description, '第二次填写的说明', '已回填的其他字段不被撤销覆盖')
      assert.match(b.round.undoHint, /已保留你的手动修改/)
      page = await ctx.html()
      assert.match(page, /已保留你的手动修改，本次自动填写不可直接撤销。/)
      assert.match(page, /<button[^>]*disabled/, '撤销按钮禁用')
    })

    await check('②f auth 拒绝经宿主状态条逐条展示（不与回填成功混淆）', async () => {
      const b = ctx.api.assistBinding.value
      b.resetRefusals()
      ctx.stub.fillQueue.push({ operations: [
        setOp('path', 'https://ems3.example.com/api/stop'),
        { op: 'set', field: 'auth', value: { type: 'apiKey', name: 'X-Trace', in: 'header', credentialId: 'cred_hack' }, basis: { kind: 'intent', quote: '测试依据' } },
      ] })
      const panel = ctx.panel
      await panel.open(b)
      await panel.generate()
      assert.equal(ctx.api.draft.value.path, 'https://ems3.example.com/api/stop', '独立合法内容照常回填')
      assert.equal(ctx.api.draft.value.auth.type, 'bearer')
      assert.equal(ctx.api.draft.value.auth.credentialId, 'cred_1', 'auth 未被写入')
      assert.equal(b.refusals.value.length, 1, '拒绝写入被记录')
      page = await ctx.html()
      assert.match(page, /assist-bar-refusal[^>]*>认证与凭据（auth\.\*）是敏感字段/, '状态条逐条展示拒绝原因（不假称全部完成）')
      assert.match(page, /请在「认证配置」中手动维护/)
      assert.equal(ctx.saves.length, 0, '拒绝路径同样不触发表单保存')
      b.resetRefusals()
      page = await ctx.html()
      assert.ok(!page.includes('assist-bar-refusal'), 'resetRefusals 后不再展示拒绝条目')
      assert.match(page, /自动填写不会修改认证与凭据/, '认证区始终保留只读说明（与拒绝条目区分）')
    })

    await check('③ 关闭弹窗：面板与 binding 一并收起，入口消失（列表态无 AI 区）', async () => {
      ctx.api.closeNow()
      assert.equal(ctx.api.assistVisible.value, false)
      assert.equal(ctx.api.assistBinding.value, null)
      assert.equal(ctx.api.meta.value, null)
      page = await ctx.html()
      assert.ok(!page.includes('ab-dialog') && !page.includes('assist-drawer') && !page.includes('自动填写'))
    })
  }

  // ── ⑤ 切换动作目标重开（新建 / 只读 flow / 已配置目标）──────────────────────
  {
    const ctx = await mountAB()
    await check('④a 未配置动作：新建配置同样有入口，targetId 空串、草稿为空白名单、回填不保存', async () => {
      ctx.api.openEditor(ctx.rowOf('act_scan'))
      let page = await ctx.html()
      assert.match(page, /✦ 自动填写/, '新建配置弹窗同样有入口')
      ctx.api.toggleAssist()
      page = await ctx.html()
      assert.equal(ctx.api.assistVisible.value, true)
      const bindingNew = ctx.api.assistBinding.value
      assert.equal(bindingNew.targetId, '', '未保存配置 targetId 为空串')
      assert.equal(bindingNew.contextTitle, '新建动作接口映射')
      const panel = ctx.panel
      await panel.open(bindingNew)
      const bodyNew = ctx.stub.calls.context[0]
      assert.equal(bodyNew.targetId, '')
      assert.deepEqual(bodyNew.draft, { method: 'POST', path: '', bodyFormat: 'json', description: '', parameters: [], note: '' }, '空白配置的契约形态快照')
      ctx.stub.fillQueue.push({ operations: [setOp('path', 'https://new.example.com/scan')] })
      await panel.generate()
      assert.equal(ctx.api.draft.value.path, 'https://new.example.com/scan')
      assert.equal(ctx.saves.length, 0, '回填不保存')
    })

    await check('④b 只读查看（历史 flow）无入口、未构建 binding；切回已配置动作重开取新上下文', async () => {
      ctx.api.closeNow() // 草稿已被回填（脏）：直接走关闭通道，不等确认
      assert.equal(ctx.api.assistVisible.value, false); assert.equal(ctx.api.assistBinding.value, null)
      ctx.api.openEditor(ctx.rowOf('act_reset'))
      let page = await ctx.html()
      assert.equal(ctx.api.editable.value, false, '历史 flow 为只读查看')
      assert.ok(!page.includes('✦ 自动填写'), '只读查看无入口')
      assert.equal(ctx.api.assistBinding.value, null, '只读态未构建 binding')
      ctx.api.toggleAssist()
      assert.equal(ctx.api.assistVisible.value, false, '只读态 toggleAssist 不生效（双保险）')

      const ctx2 = await mountAB()
      ctx2.api.openEditor(ctx2.rowOf('act_stop'))
      ctx2.api.toggleAssist()
      await ctx2.html()
      const bindingStop = ctx2.api.assistBinding.value
      await ctx2.panel.open(bindingStop)
      const bodyStop = ctx2.stub.calls.context.at(-1)
      assert.equal(bodyStop.targetId, 'cluster:act_stop')
      assert.equal(bindingStop.targetId, 'cluster:act_stop')
      assert.equal(bodyStop.targetId, 'cluster:act_stop', '切换动作目标后按新目标重取上下文')
      assert.equal(bodyStop.draft.method, 'POST'); assert.equal(bodyStop.draft.path, 'https://ems.example.com/api/devices/{deviceId}/stop')
      assert.equal(bodyStop.draft.note, '停机接口的项目说明。')
      page = await ctx2.html()
      assert.match(page, /aria-expanded="true"/)
    })

    await check('④c 显式保存链路不受影响：保存写项目草稿恰一次，回填内容随保存落盘；删除参数行后未知键仍保留', async () => {
      const ctx3 = await mountAB()
      ctx3.api.openEditor(ctx3.rowOf('act_stop'))
      await new Promise(r => setImmediate(r)) // 等 openEditor 发起的凭据读取失败落定（测试禁网 → 空列表）
      // onMounted 的项目凭据读取（SSR 不执行）：按挂载后会得到的元数据补齐，让认证校验能通过
      ctx3.api.credentials.value = [{ id: 'cred_1', name: 'EMS 控制服务凭据' }]
      ctx3.api.toggleAssist()
      await ctx3.html()
      await ctx3.panel.open(ctx3.api.assistBinding.value)
      ctx3.stub.fillQueue.push({ operations: [
        setOp('path', 'https://ems.example.com/api/v2/devices/{deviceId}/stop'),
        setOp('note', '停机接口说明（v2）。'),
        { op: 'row.remove', field: 'parameters', rowId: 'p_old2', basis: { kind: 'intent', quote: '测试依据' } },
      ] })
      await ctx3.panel.generate()
      assert.equal(ctx3.api.draft.value.path, 'https://ems.example.com/api/v2/devices/{deviceId}/stop')
      assert.deepEqual(ctx3.api.draft.value.parameters.map(p => p.id), ['p_old1', 'p_old3'], '参数行按 rowId 局部删除')
      assert.equal(ctx3.saves.length, 0, '回填本身不保存')
      await ctx3.api.saveApi()
      assert.equal(ctx3.saves.length, 1, '用户点保存才落盘（form-save 恰一次）')
      const row = ctx3.projectState.bindings.actionBindings.find(r => r.actionId === 'act_stop')
      assert.equal(row.implementation.path, 'https://ems.example.com/api/v2/devices/{deviceId}/stop')
      assert.equal(row.implementation.kind, 'api'); assert.equal(row.implementation.schemaVersion, 2)
      assert.deepEqual(row.implementation.parameters.map(p => p.id), ['p_old1', 'p_old3'], '删除的行不再落盘')
      assert.deepEqual(row.implementation.roles, ['历史字段'], '非契约历史字段经保存链路零丢失')
      assert.equal(row.implementation.auth.credentialId, 'cred_1', '认证引用随保存保留（未被自动填写触碰）')
      // 说明按既有 commitDesc 语义落盘（写入键带 mg: 前缀，读取侧 descTextOf 兼容两种形态）
      assert.equal(ctx3.projectState.bindings.mappingDescriptions.actions['mg:cluster']['mg:act_stop'], '停机接口说明（v2）。', '说明随保存落盘')
      assert.equal(descTextOfOf(ctx3.projectState, 'actions', 'mg:cluster', 'mg:act_stop'), '停机接口说明（v2）。', '读取侧按 mg: 键取到新说明')
      assert.equal(ctx3.api.meta.value, null, '保存成功关闭弹窗')
    })
  }
} finally {
  try { rmSync(root, { recursive: true, force: true }) } catch { /* 临时目录 */ } finally { globalThis.fetch = realFetch }
}

const failed = results.filter(r => !r)
console.log(`\n统计：${results.length - failed.length}/${results.length} 项通过`)
if (failed.length) process.exit(1)
