// 实例识别（P1）与链接映射（P5）整表自动填写接入测试（2026-09-22 T8 改版）。运行（仓库根）：
//   node --import ./tests/ts_hooks.mjs tests/assist_identity_link.test.mjs
// 编译真实 ObjectSources.vue / LinkMappings.vue 与 AssistPanel.vue（其余子组件桩化、无路由无服务、
// 不发真实请求）；assist api 经 provide('assist-api') 注入合成桩（autofill/1 fill 响应内存队列）。
// 覆盖（新交互，旧「建议卡→勾选→采纳」断言随改版移除）：
//   ① binding 工厂新对接面：formId/contractInfo（生成物指纹）/白名单 draft（契约 fieldId，primaryKey↔
//      primary_key）/applyDraft 合并零丢失/快照钩子开撤销单元/undoRound 一次性/手改禁撤销；
//      P1 登记模式只暴露说明类字段（mode/note），不批量生成登记实例、不凭 id 名称推断唯一性；
//   ② A01 默认无 AI 区：实例识别表单页头次要按钮「✦ 自动填写」（aria-expanded/aria-haspopup），
//      点开才挂抽屉；旧建议卡/勾选/采纳 UI 不再出现；
//   ③ 上下文请求携带场景与契约形态草稿（purpose=fill；registered 模式快照只有 mode/note）；
//   ④ 生成→直接回填（无勾选步骤）：connection/table/primaryKey 正常链路、状态条「已填写 N 项，尚未保存」、
//      逐字段旧值→新值、form-save 零调用（回填不触发任何保存通道）；
//   ⑤ 宿主撤销：恢复本轮开始前草稿、一次性、状态条清除；⑥ 手改禁撤销：undoRound 拒绝、保留手改值；
//   ⑦ 续轮（补答）：已填 N 项另有 M 项待补充、整轮（首轮+续轮）一次撤销；
//   ⑧ P5 两端字段映射：sourceId/field/targetSourceId/targetField 按建议写入（字段同名不自动等价——照常
//      提交，服务端核验），未知结构（relation/targetType/legacy）在回填与撤销后零丢失；
//   ⑨ 说明（note）随保存经既有 commitDesc 落盘，回填本身不写。
// 已知边界：SSR 渲染不执行 onMounted（面板自动取上下文）与模板 ref 填充（notify 通道），由「驱动面板
// 暴露的状态机 + 手改计数 + typecheck」覆盖，浏览器实链路留给独立验收。
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

const root = mkdtempSync(join(tmpdir(), 'wiz_assist_il_'))
process.env.WIZ_WORKBENCH_ROOT = root
process.env.WIZ_WORKBENCH_PORT = '18993'
const realFetch = globalThis.fetch
globalThis.fetch = async (url) => { throw new Error('测试不应发起真实请求：' + String(url)) } // 全程禁网：辅助 API 一律走桩

/** 编译真实 SFC（script setup + SSR 模板）。kept：{ 组件名: globalThis 变量名 }，命中的 .vue 导入
 *  改挂到共享变量（用于把真实 AssistPanel 塞进被测组件），其余 .vue 一律桩成 render:null。 */
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
  // 相对导入补 .ts，包名（vue / vue/server-renderer）按 frontend 依赖解析；重写须在拼入模板代码之后。
  code = code.replace(/from (['"])([^'"]+)\1/g, (_, q, spec) => {
    const target = spec.startsWith('.') ? resolve(dirname(absPath), spec + '.ts') : require.resolve(spec)
    return 'from ' + JSON.stringify(pathToFileURL(target).href)
  })
  const file = join(root, id + '.mjs')
  writeFileSync(file, ts.transpileModule(code, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText)
  return (await import(pathToFileURL(file).href)).default
}

// 先编译真实面板，再编译两个项目区表单（其 AssistPanel 导入指向真实面板）。
const Panel = await loadSFC(resolve('frontend/src/assist/AssistPanel.vue'), 'assist_panel_real')
globalThis.__ilAssistPanel = Panel
const SourcesComp = await loadSFC(resolve('frontend/src/project/ObjectSources.vue'), 'obj_sources_assist', { AssistPanel: '__ilAssistPanel' })
const LinksComp = await loadSFC(resolve('frontend/src/project/LinkMappings.vue'), 'link_maps_assist', { AssistPanel: '__ilAssistPanel' })

const { FORM_SCHEMA_DIGESTS } = await import('../frontend/src/assist/formContracts.gen.ts')

// ── 捕获每次挂载的 setup 状态（多场景互不串线）────────────────────────────────
let activeSlot = null
for (const Comp of [SourcesComp, LinksComp]) {
  const origSetup = Comp.setup
  Comp.setup = (props, ctx) => { const api = origSetup(props, ctx); if (activeSlot) activeSlot.api = api; return api }
}
const origPanelSetup = Panel.setup
Panel.setup = (props, ctx) => { const s = origPanelSetup(props, ctx); if (activeSlot) activeSlot.panel = s; return s }

// ── assist-api 桩：autofill/1 fill 响应内存队列；context/generate 记录请求体 ─────────
const setOp = (field, value) => ({ op: 'set', field, value, basis: { kind: 'intent', quote: '测试依据' } })
function makeAssistApi() {
  const calls = { context: [], generate: [] }
  const fillQueue = [] // 队列元素：fill 响应覆盖项；'hang' = 永不返回（在途请求）
  let n = 0
  const lastTarget = { targetKind: 'identity', targetId: '' }
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
      if (over === 'hang') return new Promise(() => {})
      if (!over) throw Object.assign(new Error('测试桩未编排该请求'), { data: { code: 'UNPLANNED' } })
      n++
      const formId = over.formId || 'identity'
      return Object.assign({
        protocol: 'autofill/1', status: 'ok', requestId: 'srv-' + n, formId,
        schemaVersion: 1, schemaDigest: FORM_SCHEMA_DIGESTS[formId],
        target: { space: 'project', targetKind: lastTarget.targetKind, targetId: lastTarget.targetId },
        draftFingerprint: 'dfp-' + n, contextFingerprint: 'cfp-1',
        sessionId: 's-' + n, roundId: 'r-' + n,
        operations: [], questions: [], unresolved: [], summary: '',
        meta: { durationMs: 5, provider: '测试桩', model: 'stub' },
      }, over)
    },
  }
}

// ── form-save 间谍：被调用即记录（回填后必须保持 0 次；显式保存才 +1）──────────────
function makeFormSaveSpy() {
  const saves = []
  return { saves, async submitForm(area, apply) { saves.push(area); apply(); return { ok: true, message: '' } } }
}

// ── 项目区夹具（mapping_forms 同源）：簇←belongs→储库，目录含 clusters/samples ──
function fixtures() {
  const b = reactive({ object_type: 'cluster', connection: 'db', table: 'clusters', primary_key: 'id', sources: [], properties: {}, relations: [] })
  const target = reactive({ object_type: 'storage', connection: 'db', table: 'storage', primary_key: 'id', sources: [], properties: {}, relations: [] })
  const fields = (...names) => names.map(name => ({ name, dataType: 'double', key: name === 'id' ? 'pri' : '', comment: '' }))
  const projectState = reactive({
    connections: { connections: [{ id: 'db', name: '测试库', engine: 'mysql' }] },
    bindings: {
      object_bindings: [b, target],
      catalogs: { db: { tables: [{ name: 'clusters', fields: fields('id', 'storage_id', 'rated_power') }, { name: 'samples', fields: fields('cluster_id', 'soc') }, { name: 'storage', fields: fields('id', 'name') }] } },
      mappingDescriptions: { schemaVersion: 1, objects: { 'mg:cluster': '现有说明' } },
    },
    implementations: [], parameters: {},
  })
  const graph = [
    { '@id': 'mg:cluster', '@type': 'owl:Class', 'rdfs:label': '储能簇' },
    { '@id': 'mg:storage', '@type': 'owl:Class', 'rdfs:label': '储能设备' },
    { '@id': 'mg:belongs', '@type': 'owl:ObjectProperty', 'rdfs:label': '所属设备', 'rdfs:domain': { '@id': 'mg:cluster' }, 'rdfs:range': { '@id': 'mg:storage' }, 'mg:cardinality': 'many-to-one' },
    { '@id': 'mg:l_extra', '@type': 'owl:ObjectProperty', 'rdfs:label': '冗余设备', 'rdfs:domain': { '@id': 'mg:cluster' }, 'rdfs:range': { '@id': 'mg:storage' }, 'mg:cardinality': 'one-to-many' },
  ]
  const refState = { ontology: { '@graph': graph }, workflow: {} }
  return { b, target, projectState, graph, refState }
}

/** SSR 挂载组件：form-guard/form-save/assist-api 桩；捕获 setup 状态与面板实例。 */
async function mount(Component, { b, projectState, refState }) {
  const slot = { api: null, panel: null }
  const formSave = makeFormSaveSpy()
  const assist = makeAssistApi()
  activeSlot = slot
  const app = createSSRApp(Component, { projectState, refState, b })
  app.provide('form-guard', { register() {}, unregister() {}, hasDirty: () => false, editing: () => false })
  app.provide('form-save', formSave)
  app.provide('assist-api', assist)
  await renderToString(app)
  return {
    b, projectState, formSave, assist,
    get api() { return slot.api },
    get panel() { return slot.panel },
    // 用同一份 setup 状态重渲染页面（不重跑 setup，等价 mapping_forms 的 html() 手法）
    html: () => { activeSlot = slot; return renderToString(createSSRApp({ render: () => h({ props: Component.props, ssrRender: Component.ssrRender, setup: () => slot.api }, { projectState, refState, b }) })) },
    // 用驱动后的面板状态单独渲染面板（验证抽屉/横幅等 UI 呈现）
    panelHtml: () => renderToString(createSSRApp({ render: () => h({ props: Panel.props, ssrRender: Panel.ssrRender, setup: () => slot.panel }, { binding: slot.api.assistBinding.value }) })),
  }
}

const results = []
async function check(name, fn) { try { await fn(); results.push(true); console.log('通过：' + name) } catch (e) { results.push(false); console.log('失败：' + name + ' — ' + (e && e.message)) } }

try {
  // ── ① binding 工厂（新对接面：formId/contractInfo/applyDraft/撤销单元/手改禁撤销）──
  {
    const { identityAssistBinding, linkMappingAssistBinding } = await import('../frontend/src/assist/identityLinkBindings.ts')
    await check('① identity binding：契约指纹、database 快照 5 键（primaryKey↔primary_key）、applyDraft 合并并镜像、撤销单元、手改禁撤销', async () => {
      const d = { mode: 'database', connection: 'db', table: 'clusters', primary_key: 'id', instances: [{ id: 'S1', label: '实例一' }] }
      const note = { value: '旧说明' }
      const b = identityAssistBinding(() => d, note, { targetId: 'cluster', contextTitle: '标题T' })
      assert.equal(b.space, 'project'); assert.equal(b.targetKind, 'identity'); assert.equal(b.targetId, 'cluster'); assert.equal(b.contextTitle, '标题T')
      assert.equal(b.formId, 'identity', 'binding 声明 autofill/1 表单契约 id')
      assert.deepEqual(b.contractInfo(), { schemaVersion: 1, schemaDigest: FORM_SCHEMA_DIGESTS.identity }, 'contractInfo 取自前端契约生成物')
      assert.deepEqual(Object.keys(b.draft()), ['mode', 'connection', 'table', 'primaryKey', 'note'], 'database 模式快照恰为契约 5 键')
      assert.deepEqual(b.draft(), { mode: 'database', connection: 'db', table: 'clusters', primaryKey: 'id', note: '旧说明' }, 'primaryKey 完成组件键 primary_key 的映射')
      assert.deepEqual(b.codecs, {}, '实例识别契约无 codecs：普通字段 identity 直写')

      const snap = b.snapshot()
      assert.deepEqual(snap, { mode: 'database', connection: 'db', table: 'clusters', primaryKey: 'id', note: '旧说明' })
      assert.equal(b.round.canUndo, false, '仅快照未回填：尚不可撤销')
      b.applyDraft({ mode: 'database', connection: 'db2', table: 'samples', primaryKey: 'cluster_id', note: '新说明' })
      assert.equal(d.connection, 'db2'); assert.equal(d.table, 'samples'); assert.equal(d.primary_key, 'cluster_id', 'primaryKey 落回组件键 primary_key')
      assert.equal(note.value, '新说明', 'note 落回 noteDraft')
      assert.deepEqual(d.instances, [{ id: 'S1', label: '实例一' }], '白名单外键（instances）不被落回触碰')
      assert.equal(b.round.appliedCount, 4, '已填写 4 项（顶层实际改变数）')
      assert.equal(b.round.statusBarText, '已填写 4 项，尚未保存')
      assert.deepEqual(b.round.changes.map(c => c.field), ['connection', 'table', 'primaryKey', 'note'], '逐字段旧值→新值（契约标签）')
      assert.deepEqual(b.round.changes.map(c => c.label), ['数据连接', '来源表／视图', '实例主键', '说明'])
      assert.equal(b.undoRound(), true, '宿主整轮撤销')
      assert.equal(d.connection, 'db', '撤销恢复本轮起点'); assert.equal(d.primary_key, 'id')
      assert.equal(note.value, '旧说明', '撤销同步还原说明草稿')
      assert.equal(b.round.statusBarText, ''); assert.equal(b.round.undone, true)
      assert.equal(b.undoRound(), false, '一次性：撤销后不可再撤')

      b.applyDraft({ connection: 'db9' })
      b.noteManualChange()
      assert.equal(b.round.canUndo, false); assert.match(b.round.undoHint, /手动修改/)
      assert.equal(b.undoRound(), false, '手改后整轮撤销被拒')
      assert.equal(d.connection, 'db9', '被拒的撤销不得改动草稿')

      const eb = identityAssistBinding(() => null, undefined) // 空草稿兜底：不抛错
      assert.deepEqual(eb.draft(), { mode: 'database', connection: '', table: '', primaryKey: '', note: '' })
      eb.applyDraft({ connection: 'x' }); eb.restore({}); eb.observeFill(null)
      assert.equal(eb.undoRound(), false, '空草稿 undoRound 安全返回 false')
    })

    await check('①b P1 登记模式：快照只有 {mode,note}——不批量生成登记实例、不凭 id 名称推断唯一性；mode 切换守卫', async () => {
      const d = { mode: 'registered', connection: 'keep_c', table: 'keep_t', primary_key: 'keep_pk', instances: [{ id: 'S1', label: '簇一' }] }
      const note = { value: '' }
      const b = identityAssistBinding(() => d, note) // 缺省：targetId ''、兜底标题非空
      assert.equal(b.targetId, ''); assert.ok(b.contextTitle.length > 0)
      assert.deepEqual(b.draft(), { mode: 'registered', note: '' }, 'registered 快照只有说明类字段')
      assert.ok(!JSON.stringify(b.draft()).includes('instances'), '登记实例清单永不出网')
      b.snapshot()
      b.applyDraft({ mode: 'registered', connection: 'x', table: 'y', primaryKey: 'z', note: '登记实例的业务含义说明。' })
      assert.equal(d.connection, 'keep_c'); assert.equal(d.table, 'keep_t'); assert.equal(d.primary_key, 'keep_pk', 'registered 模式下连接/表/主键不可见即不可填（visibleWhen）')
      assert.equal(note.value, '登记实例的业务含义说明。', '登记模式只填说明类字段')
      assert.deepEqual(d.instances, [{ id: 'S1', label: '簇一' }], '不批量生成登记实例（快照外结构不被落回）')
      assert.equal(b.round.appliedCount, 1, '登记模式只计说明这一项')
      b.applyDraft({ mode: 'database' })
      assert.equal(d.mode, 'database', '契约内 mode 可建议切换（无守卫时）')
      assert.deepEqual(Object.keys(b.draft()), ['mode', 'connection', 'table', 'primaryKey', 'note'], '切回 database 后快照恢复 5 键')

      const g = { mode: 'registered', connection: '', table: '', primary_key: '' }
      const guardCalls = []
      const gb = identityAssistBinding(() => g, { value: '' }, { applyMode: t => { guardCalls.push(t); return false } })
      gb.snapshot()
      gb.applyDraft({ mode: 'database', connection: 'c9' })
      assert.equal(g.mode, 'registered', '守卫拒绝时 mode 不落草稿')
      assert.deepEqual(guardCalls, ['database'])
      assert.equal(g.connection, '', '切换被拦截（仍 registered）：同批连接键也不写入')

      const g2 = { mode: 'registered', connection: '', table: '', primary_key: '' }
      const gb2 = identityAssistBinding(() => g2, { value: '' }, { applyMode: t => { guardCalls.push(t); return true } })
      gb2.snapshot()
      gb2.applyDraft({ mode: 'database', connection: 'c9' })
      assert.equal(g2.mode, 'database'); assert.equal(g2.connection, 'c9', '守卫放行后 mode 落草稿、其余键按新模式写入')

      const bad = { mode: 'registered', connection: '', table: '', primary_key: '' }
      const bb = identityAssistBinding(() => bad, { value: '' })
      bb.snapshot(); bb.applyDraft({ mode: 'sometimes' })
      assert.equal(bad.mode, 'registered', 'mode 只接受两个枚举值')

      // 快照恢复：registered 快照只还原 mode/note，不触碰连接/表/主键（登记实例清单零丢失）
      const rd = { mode: 'registered', connection: 'c', table: 't', primary_key: 'p', instances: [{ id: 'S2', label: '簇二' }] }
      const rnote = { value: 'r0' }
      const rb = identityAssistBinding(() => rd, rnote)
      const rsnap = rb.snapshot()
      assert.deepEqual(rsnap, { mode: 'registered', note: 'r0' })
      rnote.value = '手改说明'; rd.connection = '手改c'
      rb.restore(rsnap)
      assert.equal(rnote.value, 'r0'); assert.equal(rd.mode, 'registered')
      assert.equal(rd.connection, '手改c', 'registered 快照恢复只还原 mode/note，不触碰连接/表/主键')
      assert.deepEqual(rd.instances, [{ id: 'S2', label: '簇二' }], '恢复不删除白名单外结构')
    })

    await check('①c linkMapping binding：契约指纹、四字段+note 映射、rowId 局部更新、未知结构零丢失、observeFill 过滤', async () => {
      const d = { relation: 'belongs', targetType: 'storage', sourceId: '', field: 'f0', targetSourceId: '', targetField: '', legacy: false, membership: undefined }
      const note = { value: '' }
      const lb = linkMappingAssistBinding(() => d, note, { projectId: 'proj-1', targetId: 'cluster.belongs' })
      assert.equal(lb.space, 'project'); assert.equal(lb.targetKind, 'linkMapping'); assert.equal(lb.targetId, 'cluster.belongs'); assert.equal(lb.projectId, 'proj-1')
      assert.equal(lb.formId, 'linkMapping')
      assert.deepEqual(lb.contractInfo(), { schemaVersion: 1, schemaDigest: FORM_SCHEMA_DIGESTS.linkMapping })
      assert.deepEqual(Object.keys(lb.draft()), ['sourceId', 'field', 'targetSourceId', 'targetField', 'note'], 'draft 恰为契约 5 键（行结构白名单）')
      assert.deepEqual(lb.codecs, {}, '链接映射契约无 codecs')
      assert.deepEqual(lb.draft(), { sourceId: '', field: 'f0', targetSourceId: '', targetField: '', note: '' })

      lb.snapshot()
      // 两端字段映射行按 rowId 局部更新：只写目标行（本行）的白名单键，relation/targetType/legacy 不触碰
      lb.applyDraft({ sourceId: 's1', field: 'storage_id', targetSourceId: 'ts1', targetField: 'id', note: '两端字段的等价依据说明。' })
      assert.equal(d.sourceId, 's1'); assert.equal(d.field, 'storage_id')
      assert.equal(d.targetSourceId, 'ts1'); assert.equal(d.targetField, 'id')
      assert.equal(note.value, '两端字段的等价依据说明。')
      assert.equal(d.relation, 'belongs', 'relation 不在契约内，不被回填改写')
      assert.equal(d.targetType, 'storage'); assert.equal(d.legacy, false, 'targetType/legacy 等未知结构零丢失')
      assert.equal(lb.round.appliedCount, 5); assert.equal(lb.round.statusBarText, '已填写 5 项，尚未保存')
      assert.equal(lb.undoRound(), true)
      assert.equal(d.sourceId, ''); assert.equal(d.field, 'f0'); assert.equal(d.targetField, '')
      assert.equal(note.value, ''); assert.equal(d.relation, 'belongs')

      // 字段同名不自动等价：前端照常写建议值（核验归服务端；不通过由后端转 unresolved）
      lb.snapshot()
      lb.applyDraft({ field: 'cluster_id', targetField: 'cluster_id' })
      assert.equal(d.field, 'cluster_id'); assert.equal(d.targetField, 'cluster_id', '同名/异名都照建议原样写入，前端不做业务等价推断')

      // observeFill：只接本表单、本目标、契约指纹一致的 fill 响应
      const ok = over => Object.assign({ protocol: 'autofill/1', status: 'ok', formId: 'linkMapping', schemaVersion: 1, schemaDigest: FORM_SCHEMA_DIGESTS.linkMapping, target: { space: 'project', targetKind: 'linkMapping', targetId: 'cluster.belongs' }, questions: [{ id: 'q1' }], unresolved: [] }, over)
      lb.observeFill(ok({}))
      assert.equal(lb.round.pendingCount, 1, '待补充 = questions + unresolved')
      lb.observeFill(ok({ formId: 'identity' })); assert.equal(lb.round.pendingCount, 1, 'formId 不符不计')
      lb.observeFill(ok({ target: { space: 'project', targetKind: 'linkMapping', targetId: 'cluster.x' } })); assert.equal(lb.round.pendingCount, 1, '目标不符不计')
      lb.observeFill(ok({ schemaDigest: 'bad' })); assert.equal(lb.round.pendingCount, 1, '契约指纹不符不计（引擎会按 CONTEXT_STALE 拒绝）')
      lb.observeFill(ok({ status: 'empty', questions: [] })); assert.equal(lb.round.pendingCount, 0, 'empty 清空待补充')
      lb.observeFill({ protocol: undefined }); assert.ok(true, 'check/explain 帮助响应安全忽略')

      const el = linkMappingAssistBinding(() => null, { value: '' })
      assert.deepEqual(el.draft(), { sourceId: '', field: '', targetSourceId: '', targetField: '', note: '' })
      el.applyDraft({ field: 'x' }); el.restore({}); assert.equal(el.undoRound(), false, '空草稿 apply/restore/undoRound 安全')
    })
  }

  // ── ② P1 ObjectSources：入口、上下文请求、回填零保存、撤销、手改禁撤销 ──
  {
    const fx = fixtures()
    const ctx = await mount(SourcesComp, fx)
    ctx.api.openIdentity()

    await check('② A01 默认无 AI 区：页头次要入口「✦ 自动填写」（aria-expanded/aria-haspopup），未点开不渲染抽屉', async () => {
      let page = await ctx.html()
      assert.match(page, /✦ 自动填写/, '实例识别表单页头应有次要入口')
      assert.match(page, /aria-expanded="false"/)
      assert.match(page, /aria-haspopup="dialog"/)
      assert.ok(page.includes('id="os-assist-trigger-identity"'), '入口按钮 id（面板 triggerId 指向它）')
      assert.ok(!page.includes('assist-drawer'), '默认不渲染任何 AI 区域')
      assert.ok(!page.includes('建议卡') && !page.includes('勾选') && !page.includes('采纳'), '旧建议卡/勾选/采纳 UI 不再出现')
      assert.equal(ctx.api.assistBinding.value.targetKind, 'identity')
      assert.equal(ctx.api.assistBinding.value.targetId, 'cluster', 'targetId 为对象类型标识')
      ctx.api.toggleAssist() // 与按钮 @click 同一函数：首次点击挂载面板
      page = await ctx.html()
      assert.match(page, /aria-expanded="true"/)
      assert.ok(ctx.panel, '面板实例已随页面创建')
      assert.equal(ctx.panel.checked, undefined, '旧勾选/采纳 API 已移除')
      assert.equal(ctx.panel.adopt, undefined); assert.equal(ctx.panel.setChecked, undefined)
      // SSR 不执行 onMounted（自动 open）；直接驱动面板状态机取上下文并展开
      await ctx.panel.open(ctx.api.assistBinding.value)
      const phtml = await ctx.panelHtml()
      assert.match(phtml, /assist-drawer/, '点开后抽屉出现')
      assert.match(phtml, /CTX\[identity:cluster\]/, '目标标题（服务端上下文）')
    })

    await check('②b 上下文请求携带场景与契约形态草稿（note 对齐已保存说明）；抽屉收起后入口 aria-expanded 回落', async () => {
      const body = ctx.assist.calls.context.at(-1)
      assert.equal(body.space, 'project'); assert.equal(body.targetKind, 'identity')
      assert.equal(body.targetId, 'cluster'); assert.equal(body.purpose, 'fill')
      assert.deepEqual(body.draft, { mode: 'database', connection: 'db', table: 'clusters', primaryKey: 'id', note: '现有说明' }, 'draft 是契约形态快照，note 取自对象级说明')
      assert.ok(!JSON.stringify(body.draft).includes('instances'), '登记实例清单不出网')
    })

    await check('②c 生成→直接回填（无勾选步骤）：connection/table/primaryKey 正常链路、状态条、零保存', async () => {
      ctx.assist.fillQueue.push({ formId: 'identity', operations: [setOp('connection', 'db'), setOp('table', 'samples'), setOp('primaryKey', 'cluster_id'), setOp('note', '每行是一套电池簇。')] })
      await ctx.panel.generate()
      assert.equal(ctx.assist.calls.generate.length, 1)
      const body = ctx.assist.calls.generate[0]
      assert.equal(body.mode, 'fill'); assert.equal(body.protocol, 2, 'autofill/1 请求必带 protocol:2')
      assert.equal(body.contextToken, 'tok-1')
      assert.deepEqual(body.draft, { mode: 'database', connection: 'db', table: 'clusters', primaryKey: 'id', note: '现有说明' }, 'generate 携带当前契约草稿')
      assert.equal(ctx.api.identityDraft.value.connection, 'db')
      assert.equal(ctx.api.identityDraft.value.table, 'samples', '来源表按建议回填')
      assert.equal(ctx.api.identityDraft.value.primary_key, 'cluster_id', 'primaryKey 回填组件键 primary_key')
      assert.equal(ctx.api.noteDraft.value, '每行是一套电池簇。', 'note 回填说明草稿')
      assert.equal(ctx.formSave.saves.length, 0, '回填绝不触发表单保存（form-save 零调用）')
      assert.equal(ctx.panel.status.value, 'done')
      assert.equal(ctx.panel.collapsed.value, true, '一次回填完成抽屉自动收起')
      assert.equal(ctx.panel.canUndo, undefined, '撤销入口在宿主状态条（面板不渲染）')
      const b = ctx.api.assistBinding.value
      assert.equal(b.round.statusBarText, '已填写 3 项，尚未保存', 'table/primaryKey/note 三项实际改变（connection 与旧值相同不计）')
      assert.equal(b.round.statusBarText, ctx.panel.statusBarText.value, '宿主镜像与引擎状态条口径一致')
      assert.equal(b.round.canUndo, true)
      assert.deepEqual(b.round.changes.map(c => c.field), ['table', 'primaryKey', 'note'])
      const page = await ctx.html()
      assert.match(page, /已填写 3 项，尚未保存/)
      assert.match(page, /撤销本次填写/)
      assert.match(page, /查看修改/)
      assert.match(page, /来源表／视图<\/strong>：clusters → samples/, '逐字段旧值→新值（安全插值渲染）')
      // SSR 不填模板 ref（面板实例拿不到）：入口的 aria-expanded 由面板挂载态决定；用户关闭面板
      // （×/Esc/遮罩 → @close）后回落 false。浏览器里 done 自动收起经同一 ref 通道即时回落。
      assert.equal(ctx.api.assistOpen.value, true)
      assert.match(page, /aria-expanded="true"/)
      ctx.api.onAssistPanelClosed()
      assert.equal(ctx.api.assistOpen.value, false, '面板关闭后卸载')
      const closed = await ctx.html()
      assert.match(closed, /aria-expanded="false"/)
      assert.ok(!closed.includes('assist-drawer'), '关闭后不再渲染抽屉')
      ctx.api.toggleAssist(); await ctx.html() // 重新打开供后续用例（与按钮同一函数）
      await ctx.panel.open(ctx.api.assistBinding.value)
    })

    await check('②d 宿主撤销本次填写：恢复本轮开始前草稿、一次性、状态条清除、零保存', async () => {
      const b = ctx.api.assistBinding.value
      assert.equal(b.undoRound(), true)
      assert.equal(ctx.api.identityDraft.value.table, 'clusters', '恢复本轮起点')
      assert.equal(ctx.api.identityDraft.value.primary_key, 'id')
      assert.equal(ctx.api.noteDraft.value, '现有说明')
      assert.equal(ctx.formSave.saves.length, 0, '撤销同样不触发表单保存')
      assert.equal(b.round.statusBarText, ''); assert.equal(b.round.undone, true); assert.equal(b.round.changes.length, 0)
      assert.equal(b.undoRound(), false, '一次性')
      const page = await ctx.html()
      assert.match(page, /已撤销本次自动填写，表单已恢复。/)
      assert.ok(!page.includes('已填写 3 项'), '状态条主文案清除')
    })

    await check('②e 手改禁撤销：undoRound 拒绝、草稿保留手改、状态条说明、手改通知计数', async () => {
      await ctx.panel.open(ctx.api.assistBinding.value) // 重渲染后面板实例重建：显式重开
      ctx.assist.fillQueue.push({ formId: 'identity', operations: [setOp('note', '新的业务含义说明。')] })
      await ctx.panel.generate()
      const b = ctx.api.assistBinding.value
      assert.equal(b.round.statusBarText, '已填写 1 项，尚未保存')
      ctx.api.identityTableChanged('samples') // 模板 @update:model-value 的同一写路径
      assert.equal(ctx.api.assistTouchTick.value, 1, '字段输入事件触发手改通知')
      assert.equal(b.round.canUndo, false, '手改后禁整轮撤销')
      assert.equal(b.undoRound(), false, '撤销被拒')
      assert.equal(ctx.api.identityDraft.value.table, 'samples', '手改值保留')
      assert.equal(ctx.api.noteDraft.value, '新的业务含义说明。', '已回填的其他字段不被撤销覆盖')
      assert.match(b.round.undoHint, /已保留你的手动修改/)
      const page = await ctx.html()
      assert.match(page, /已保留你的手动修改，本次自动填写不可直接撤销。/)
      assert.match(page, /<button[^>]*disabled/, '撤销按钮禁用')
    })

    await check('②f 续轮（补答）：已填 N 项另有 M 项待补充；整轮（首轮+续轮）一次撤销', async () => {
      const b = ctx.api.assistBinding.value
      // 复位到本轮起点（②e 的手改值不参与本轮）：本轮撤销应回到这里
      ctx.api.identityDraft.value.table = 'clusters'; ctx.api.identityDraft.value.primary_key = 'id'; ctx.api.noteDraft.value = '现有说明'
      await ctx.panel.open(ctx.api.assistBinding.value)
      ctx.assist.fillQueue.push({
        formId: 'identity',
        operations: [setOp('primaryKey', 'sn')],
        questions: [{ id: 'q1', text: '实例主键用哪个字段？', fields: ['primaryKey'], options: ['id', 'sn'], allowUnsure: true }],
      })
      await ctx.panel.generate()
      assert.equal(ctx.panel.status.value, 'questions', '有待补问题留在抽屉')
      assert.equal(b.round.statusBarText, '已填写 1 项，尚未保存；另有 1 项待补充')
      assert.equal(b.round.statusBarText, ctx.panel.statusBarText.value, '待补充口径与引擎一致')
      assert.equal(b.round.pendingCount, 1)
      ctx.panel.answer('q1', 'sn') // 补答后自动续轮（握手异步完成）
      ctx.assist.fillQueue.push({ formId: 'identity', operations: [setOp('note', '按实例编号识别。')] })
      await new Promise(r => setTimeout(r, 5))
      assert.equal(ctx.panel.status.value, 'done', '全部解决后收起')
      assert.equal(ctx.api.identityDraft.value.primary_key, 'sn')
      assert.equal(ctx.api.noteDraft.value, '按实例编号识别。')
      assert.equal(b.round.statusBarText, '已填写 2 项，尚未保存', '跨续轮累计 N')
      assert.equal(b.round.pendingCount, 0, '全部解决后待补充清零')
      assert.equal(b.undoRound(), true, '整轮（首轮+续轮）一次撤销')
      assert.equal(ctx.api.identityDraft.value.primary_key, 'id', '恢复到本轮开始前')
      assert.equal(ctx.api.noteDraft.value, '现有说明')
      assert.equal(ctx.formSave.saves.length, 0)
    })

    await check('②g 显式保存：form-save 恰一次，回填进来的说明经既有 commitDesc 落盘；编辑器关闭面板收起', async () => {
      ctx.api.identityModeChanged('database'); ctx.api.identityConnChanged('db'); ctx.api.identityTableChanged('clusters'); ctx.api.identityPkChanged('id')
      ctx.api.noteDraft.value = '每行是一套电池簇。'
      await ctx.api.saveIdentity()
      assert.equal(ctx.formSave.saves.length, 1, '只有用户点保存才落盘')
      const objs = ctx.projectState.bindings.mappingDescriptions.objects
      assert.equal(objs['mg:cluster'], '每行是一套电池簇。', '说明随实例识别保存写入 mappingDescriptions')
      assert.equal(ctx.api.assistOpen.value, false, '保存成功关闭编辑器，面板收起')
      assert.equal(ctx.api.assistBinding.value, null, '编辑器关闭后 binding 置空')
      const page = await ctx.html()
      assert.ok(!page.includes('自动填写'), '浏览态无自动填写入口')
    })
  }

  // ── ③ P5 LinkMappings：入口、上下文请求、字段映射回填、撤销、手改过期 ──
  {
    const fx = fixtures()
    const ctx = await mount(LinksComp, fx)
    ctx.api.openNew(fx.graph.find(r => r['@id'] === 'mg:belongs')) // 新建链接映射（表字段关联编辑器）

    // SSR 每次重渲染都会重建面板实例并刷新 ctx.panel：驱动面板前须先 open（与 T5 套件同一手法），
    // 且面板驱动与整页断言交替时每次都重新 open，避免用到未接宿主的空实例。
    let panel = ctx.panel

    await check('③ P5 默认无 AI 区：编辑态页头入口「✦ 自动填写」（aria-expanded/aria-haspopup）；点开后抽屉与上下文请求正确', async () => {
      let page = await ctx.html()
      assert.match(page, /✦ 自动填写/, '链接映射编辑态页头应有次要入口')
      assert.ok(page.includes('id="lm-assist-trigger"'), '入口按钮 id（面板 triggerId 指向它）')
      assert.match(page, /aria-expanded="false"/); assert.match(page, /aria-haspopup="dialog"/)
      assert.ok(!page.includes('assist-drawer'), '未点击前不渲染抽屉')
      assert.ok(!page.includes('勾选'), '旧勾选 UI 不再出现')
      assert.equal(ctx.api.assistBinding.value.targetKind, 'linkMapping')
      assert.equal(ctx.api.assistBinding.value.targetId, 'cluster.belongs', 'targetId=对象类型.关系id')
      ctx.api.toggleAssist()
      page = await ctx.html()
      assert.equal(ctx.api.assistOpen.value, true, '点击后抽屉挂载（页内 AssistPanel 就位）')
      assert.ok(ctx.panel, '面板实例已随页面创建')
      // 展开与取上下文由面板状态机驱动（浏览器里挂载即 open）；SSR 下渲染面板自身状态验证呈现
      const opening = ctx.panel.open(ctx.api.assistBinding.value)
      panel = ctx.panel
      let phtml = await ctx.panelHtml()
      assert.match(phtml, /assist-drawer/, '展开后出现抽屉')
      assert.match(phtml, /维护链接映射「所属设备」/, '未取到上下文时展示目标兜底标题')
      await opening
      const body = ctx.assist.calls.context.at(-1)
      assert.equal(body.space, 'project'); assert.equal(body.targetKind, 'linkMapping'); assert.equal(body.targetId, 'cluster.belongs')
      assert.deepEqual(body.draft, { sourceId: '', field: '', targetSourceId: '', targetField: '', note: '' }, '新建链接映射的契约快照')
      phtml = await ctx.panelHtml()
      assert.match(phtml, /CTX\[linkMapping:cluster.belongs\]/, '取到上下文后改用服务端标题')
      page = await ctx.html()
      assert.match(page, /aria-expanded="true"/)
    })

    await check('③b 两端字段映射按建议写入（同名不自动等价——照常提交由后端核验）：draft 四字段+noteDraft 更新、form-save 零调用', async () => {
      panel = ctx.panel
      await panel.open(ctx.api.assistBinding.value)
      ctx.assist.fillQueue.push({ formId: 'linkMapping', operations: [
        setOp('sourceId', 'src1'), setOp('field', 'storage_id'), setOp('targetSourceId', 'tsrc1'), setOp('targetField', 'id'),
        setOp('note', '两端字段的等价依据说明。'),
      ] })
      await panel.generate()
      const d = ctx.api.draft.value
      assert.equal(d.sourceId, 'src1'); assert.equal(d.field, 'storage_id')
      assert.equal(d.targetSourceId, 'tsrc1'); assert.equal(d.targetField, 'id')
      assert.equal(ctx.api.noteDraft.value, '两端字段的等价依据说明。', 'note 回填写入 noteDraft')
      assert.equal(d.relation, 'belongs', '回填不改链接标识')
      assert.equal(ctx.formSave.saves.length, 0, '回填绝不触发表单保存')
      assert.equal(panel.status.value, 'done')
      assert.equal(panel.collapsed.value, true, '一次回填完成抽屉自动收起')
      const b = ctx.api.assistBinding.value
      assert.equal(b.round.statusBarText, '已填写 5 项，尚未保存')
      assert.equal(b.round.statusBarText, panel.statusBarText.value, '宿主镜像与引擎状态条口径一致')
      assert.equal(b.round.canUndo, true)
      const page = await ctx.html()
      assert.match(page, /已填写 5 项，尚未保存/); assert.match(page, /撤销本次填写/); assert.match(page, /查看修改/)
      assert.match(page, /起点关联字段<\/strong>：f0 → storage_id|起点关联字段<\/strong>：（空） → storage_id/, '逐字段旧值→新值')
    })

    await check('③c 宿主撤销：恢复本轮起点（映射行的未知结构零丢失）、一次性', async () => {
      const b = ctx.api.assistBinding.value
      assert.equal(b.undoRound(), true)
      const d = ctx.api.draft.value
      assert.equal(d.sourceId, ''); assert.equal(d.field, ''); assert.equal(d.targetField, '')
      assert.equal(ctx.api.noteDraft.value, '', '撤销同步还原说明草稿')
      assert.equal(d.relation, 'belongs', '撤销不触碰 relation（未知结构零丢失）')
      assert.equal(ctx.formSave.saves.length, 0)
      assert.equal(b.undoRound(), false, '撤销一次性')
      const page = await ctx.html()
      assert.match(page, /已撤销本次自动填写，表单已恢复。/)
    })

    await check('③d 手改字段：手改通知计数与禁撤销；回填后草稿保留手改值', async () => {
      panel = ctx.panel
      await panel.open(ctx.api.assistBinding.value) // 撤销后重开同目标
      ctx.assist.fillQueue.push({ formId: 'linkMapping', operations: [setOp('field', 'id')] })
      await panel.generate()
      assert.equal(ctx.api.draft.value.field, 'id')
      ctx.api.draftFieldChanged('cluster_id')
      assert.equal(ctx.api.draft.value.field, 'cluster_id')
      assert.equal(ctx.api.assistTouchTick.value, 1, '字段手改触发手改通知')
      const b = ctx.api.assistBinding.value
      assert.equal(b.round.canUndo, false)
      assert.equal(b.undoRound(), false, '手改后撤销被拒')
      assert.equal(ctx.api.draft.value.field, 'cluster_id', '手改值保留')
      assert.match(b.round.undoHint, /已保留你的手动修改/)
      // 手改后仍可继续「自动填写」新一轮（新撤销单元从当前草稿起算）
      await panel.open(ctx.api.assistBinding.value)
      ctx.assist.fillQueue.push({ formId: 'linkMapping', operations: [setOp('targetField', 'id')] })
      await panel.generate()
      assert.equal(ctx.api.draft.value.targetField, 'id')
      assert.equal(ctx.api.draft.value.field, 'cluster_id', '手改值不被新一轮回填覆盖')
      assert.equal(b.round.canUndo, true, '新一单元可撤销')
      assert.equal(b.undoRound(), true)
      assert.equal(ctx.api.draft.value.field, 'cluster_id', '撤销回到新单元起点（保留手改值）')
    })

    await check('③e 切换编辑目标（另一条链接）：binding 以新目标重建并重取上下文；关闭编辑器收起', async () => {
      ctx.api.openNew(fx.graph.find(r => r['@id'] === 'mg:l_extra'))
      assert.equal(ctx.api.assistBinding.value.targetId, 'cluster.l_extra', 'binding 已指向新链接')
      assert.equal(ctx.api.assistBinding.value.round.statusBarText, '', '新目标镜像是干净的')
      await ctx.panel.open(ctx.api.assistBinding.value)
      const last = ctx.assist.calls.context.at(-1)
      assert.equal(last.targetId, 'cluster.l_extra')
      assert.deepEqual(last.draft, { sourceId: '', field: '', targetSourceId: '', targetField: '', note: '' })
      ctx.api.closeEditor()
      assert.equal(ctx.api.assistOpen.value, false)
      assert.equal(ctx.api.assistBinding.value, null)
      const page = await ctx.html()
      assert.ok(!page.includes('assist-drawer'), '收起后不再渲染抽屉')
      assert.ok(!page.includes('自动填写'), '浏览态无自动填写入口')
    })
  }

  // ── ④ 成员模式编辑器不提供辅助入口（成员规则不在契约内）──
  {
    const fx = fixtures()
    fx.b.identity = { kind: 'registered', instances: [{ id: 'S1', label: '簇一' }] }
    fx.b.connection = ''; fx.b.table = ''; fx.b.primary_key = ''
    const ctx = await mount(LinksComp, fx)
    ctx.api.openNew(fx.graph.find(r => r['@id'] === 'mg:l_extra'))
    await check('④ 成员模式编辑器：无辅助入口、binding 为空', async () => {
      // 起点为登记身份 → openNew 进入成员模式编辑器
      assert.equal(ctx.api.editingMembership.value, true, '起点登记身份进入成员模式')
      const page = await ctx.html()
      assert.ok(!page.includes('✦ 自动填写'), '成员模式不提供辅助入口')
      assert.equal(ctx.api.assistBinding.value, null, '成员模式不构造 binding')
    })
  }
} finally {
  globalThis.fetch = realFetch
  rmSync(root, { recursive: true, force: true })
}

const failed = results.filter(r => !r)
console.log(`\n统计：${results.length - failed.length}/${results.length} 项通过`)
if (failed.length) process.exit(1)
