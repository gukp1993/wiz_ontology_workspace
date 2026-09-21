// 实例识别（P1）与链接映射（P5）辅助填写接入测试（2026-09-21 T8）。运行（仓库根）：
//   node --import ./tests/ts_hooks.mjs tests/assist_identity_link.test.mjs
// 编译真实 ObjectSources.vue / LinkMappings.vue 与 AssistPanel.vue（其余子组件桩化、无路由无服务、
// 不发真实请求）；assist api 经 provide('assist-api') 注入合成桩（与 assist_object_workspace 同款）。
// 覆盖：① identityAssistBinding/linkMappingAssistBinding 工厂（白名单快照、primaryKey↔primary_key
// 与 note↔noteDraft 映射、undefined 不覆盖、白名单外忽略、registered 模式只暴露 {mode,note} 且
// apply 忽略连接/表/主键、mode 守卫、快照就地恢复且不触达 instances）；② P1 入口与面板、上下文
// 请求体、采纳只改本地草稿（form-save 零调用）、手改过期、registered 采纳拦截、说明随保存落盘；
// ③ P5 入口与面板、采纳四字段+说明、撤销、手改过期；④ 切换编辑目标以新 binding 重开；⑤ 关闭编辑器收起。
// 已知边界：SSR 渲染不执行 onMounted（面板自动取上下文）与模板 ref 填充（notify 通道），
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

// ── 捕获每次挂载的 setup 状态（多场景互不串线）────────────────────────────────
let activeSlot = null
for (const Comp of [SourcesComp, LinksComp]) {
  const origSetup = Comp.setup
  Comp.setup = (props, ctx) => { const api = origSetup(props, ctx); if (activeSlot) activeSlot.api = api; return api }
}
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
      const fields = body.targetKind === 'identity'
        ? ['mode', 'connection', 'table', 'primaryKey', 'note']
        : ['sourceId', 'field', 'targetSourceId', 'targetField', 'note']
      return {
        contextToken: 'tok-' + calls.context.length,
        contextFingerprint: 'fp-' + calls.context.length,
        context: { targetKind: body.targetKind, title: 'CTX[' + body.targetKind + ':' + body.targetId + ']', editableFields: fields.map(k => ({ key: k, label: k, kind: 'text', required: false, options: null, group: null, help: '' })), definitions: [], catalog: [], flows: [], modelReady: true },
      }
    },
    async generate(body) {
      calls.generate.push(JSON.parse(JSON.stringify(body)))
      return Object.assign({ requestId: 'req-' + calls.generate.length, status: 'ok', contextFingerprint: 'fp-1', questions: [], suggestions: [], issues: [], explanation: null, meta: { durationMs: 5, provider: '测试桩', model: 'stub' } }, genQueue.shift() || {})
    },
  }
}

// ── form-save 间谍：被调用即记录（采纳后必须保持 0 次；显式保存才 +1）──────────────
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
    // 用驱动后的面板状态单独渲染面板（验证横幅/禁用态等 UI 呈现）
    panelHtml: () => renderToString(createSSRApp({ render: () => h({ props: Panel.props, ssrRender: Panel.ssrRender, setup: () => slot.panel }, { binding: slot.api.assistBinding.value }) })),
  }
}

const results = []
async function check(name, fn) { try { await fn(); results.push(true); console.log('通过：' + name) } catch (e) { results.push(false); console.log('失败：' + name + ' — ' + (e && e.message)) } }

try {
  // ── ① binding 工厂（白名单快照、键映射、registered 分支、mode 守卫、快照恢复）──
  {
    const { identityAssistBinding, linkMappingAssistBinding } = await import('../frontend/src/assist/identityLinkBindings.ts')
    await check('① identityAssistBinding：database 快照 5 键（primaryKey 映射）；undefined 不覆盖；白名单外忽略', async () => {
      const d = { mode: 'database', connection: 'db', table: 'clusters', primary_key: 'id' }
      const note = { value: '旧说明' }
      const b = identityAssistBinding(() => d, note, { targetId: 'o_cluster', contextTitle: '标题T' })
      assert.equal(b.space, 'project'); assert.equal(b.targetKind, 'identity'); assert.equal(b.targetId, 'o_cluster'); assert.equal(b.contextTitle, '标题T')
      assert.deepEqual(Object.keys(b.draft()), ['mode', 'connection', 'table', 'primaryKey', 'note'], 'database 模式快照恰为白名单 5 键')
      assert.deepEqual(b.draft(), { mode: 'database', connection: 'db', table: 'clusters', primaryKey: 'id', note: '旧说明' }, 'primaryKey 完成组件键 primary_key 的映射')
      b.apply({ primaryKey: 'sn', connection: 'db2', note: '新说明', extraKey: '越权', table: undefined })
      assert.equal(d.primary_key, 'sn', 'primaryKey 正确写回组件键 primary_key')
      assert.equal(d.connection, 'db2')
      assert.equal(note.value, '新说明', 'note 写入 noteDraft')
      assert.equal(d.table, 'clusters', 'undefined 值不得覆盖既有草稿')
      assert.ok(!('extraKey' in d), '白名单外键不得落入草稿')
    })

    await check('①b registered 模式：快照只有 {mode,note}；apply 忽略连接/表/主键；mode 可建议切换且守卫生效', async () => {
      const d = { mode: 'registered', connection: 'keep_c', table: 'keep_t', primary_key: 'keep_pk' }
      const note = { value: '' }
      const b = identityAssistBinding(() => d, note) // 缺省：targetId ''、兜底标题非空
      assert.equal(b.targetId, ''); assert.ok(b.contextTitle.length > 0)
      assert.deepEqual(b.draft(), { mode: 'registered', note: '' }, 'registered 快照只有 mode 与 note')
      b.apply({ connection: 'x', table: 'y', primaryKey: 'z', note: '登记说明' })
      assert.equal(d.connection, 'keep_c'); assert.equal(d.table, 'keep_t'); assert.equal(d.primary_key, 'keep_pk', 'registered 模式下连接/表/主键一律忽略')
      assert.equal(note.value, '登记说明')
      b.apply({ mode: 'database' })
      assert.equal(d.mode, 'database', '白名单内 mode 可由建议切换（无守卫时）')
      assert.deepEqual(Object.keys(b.draft()), ['mode', 'connection', 'table', 'primaryKey', 'note'], '切回 database 后快照恢复 5 键')

      const g = { mode: 'registered', connection: '', table: '', primary_key: '' }
      const guardCalls = []
      const gb = identityAssistBinding(() => g, { value: '' }, { applyMode: t => { guardCalls.push(t); return false } })
      gb.apply({ mode: 'database', connection: 'c9' })
      assert.equal(g.mode, 'registered', '守卫拒绝时 mode 不落草稿')
      assert.deepEqual(guardCalls, ['database'])
      assert.equal(g.connection, '', '切换被拦截（仍 registered）：同批连接键也不写入')

      const g2 = { mode: 'registered', connection: '', table: '', primary_key: '' }
      const gb2 = identityAssistBinding(() => g2, { value: '' }, { applyMode: t => { guardCalls.push(t); return true } })
      gb2.apply({ mode: 'database', connection: 'c9' })
      assert.equal(g2.mode, 'database'); assert.equal(g2.connection, 'c9', '守卫放行后 mode 落草稿、其余键按新模式写入')

      const bad = { mode: 'registered', connection: '', table: '', primary_key: '' }
      identityAssistBinding(() => bad, { value: '' }).apply({ mode: 'sometimes' })
      assert.equal(bad.mode, 'registered', 'mode 只接受两个枚举值')
    })

    await check('①c identity 快照/恢复：JSON 克隆、就地恢复（引用稳定）、instances 等白名单外键不丢', async () => {
      const d = { mode: 'database', connection: 'db', table: 'clusters', primary_key: 'id', instances: [{ id: 'S1', label: '实例一' }] }
      const note = { value: 'n0' }
      const b = identityAssistBinding(() => d, note)
      const snap = b.snapshot()
      assert.deepEqual(snap, { mode: 'database', connection: 'db', table: 'clusters', primaryKey: 'id', note: 'n0' })
      d.connection = '手改'
      assert.deepEqual(snap.connection, 'db', '快照是 JSON 克隆，不随草稿漂移')
      const refBefore = d
      b.restore(snap)
      assert.ok(d === refBefore, 'restore 必须就地恢复（宿主草稿引用保持稳定）')
      assert.equal(d.connection, 'db')
      assert.equal(note.value, 'n0')
      assert.deepEqual(d.instances, [{ id: 'S1', label: '实例一' }], '恢复不删除白名单外的 instances（登记实例不在辅助范围）')

      const rd = { mode: 'registered', connection: 'c', table: 't', primary_key: 'p' }
      const rnote = { value: 'r0' }
      const rb = identityAssistBinding(() => rd, rnote)
      const rsnap = rb.snapshot()
      assert.deepEqual(rsnap, { mode: 'registered', note: 'r0' })
      rnote.value = '手改说明'; rd.connection = '手改c'
      rb.restore(rsnap)
      assert.equal(rnote.value, 'r0'); assert.equal(rd.mode, 'registered')
      assert.equal(rd.connection, '手改c', 'registered 快照恢复只还原 mode/note，不触碰连接/表/主键')

      const eb = identityAssistBinding(() => null, undefined) // 空草稿兜底：不抛错
      assert.deepEqual(eb.draft(), { mode: 'database', connection: '', table: '', primaryKey: '', note: '' })
      eb.apply({ connection: 'x' }); eb.restore({}); assert.ok(true, '空草稿 apply/restore 安全')
    })

    await check('①d linkMappingAssistBinding：四字段+note 映射、白名单外忽略、快照恢复、空草稿安全', async () => {
      const d = { relation: 'belongs', targetType: 'storage', sourceId: '', field: 'f0', targetSourceId: '', targetField: '', legacy: false }
      const note = { value: '' }
      const lb = linkMappingAssistBinding(() => d, note, { projectId: 'proj-1', targetId: 'cluster.belongs' })
      assert.equal(lb.space, 'project'); assert.equal(lb.targetKind, 'linkMapping'); assert.equal(lb.targetId, 'cluster.belongs'); assert.equal(lb.projectId, 'proj-1')
      assert.deepEqual(Object.keys(lb.draft()), ['sourceId', 'field', 'targetSourceId', 'targetField', 'note'])
      assert.deepEqual(lb.draft(), { sourceId: '', field: 'f0', targetSourceId: '', targetField: '', note: '' })
      lb.apply({ sourceId: 's1', field: 'f1', targetSourceId: 'ts1', targetField: 'tf1', note: '两端依据', relation: '越权', membership: [], legacy: true, field2: 123 })
      assert.equal(d.sourceId, 's1'); assert.equal(d.field, 'f1'); assert.equal(d.targetSourceId, 'ts1'); assert.equal(d.targetField, 'tf1')
      assert.equal(note.value, '两端依据')
      assert.equal(d.relation, 'belongs', 'relation 不在白名单，不被建议改写')
      assert.ok(!('membership' in d) && d.legacy === false, 'membership/legacy 不被建议触达')
      lb.apply({ field: undefined })
      assert.equal(d.field, 'f1', 'undefined 不覆盖')
      const lsnap = lb.snapshot()
      d.field = '手改'; d.targetField = '手改'
      const refBefore = d
      lb.restore(lsnap)
      assert.ok(d === refBefore, 'restore 就地恢复')
      assert.equal(d.field, 'f1'); assert.equal(d.targetField, 'tf1'); assert.equal(note.value, '两端依据')

      const el = linkMappingAssistBinding(() => null, { value: '' })
      assert.deepEqual(el.draft(), { sourceId: '', field: '', targetSourceId: '', targetField: '', note: '' })
      el.apply({ field: 'x' }); el.restore({}); assert.ok(true, '空草稿 apply/restore 安全')
    })
  }

  // ── ② P1 ObjectSources：入口、上下文请求、采纳零保存、手改过期、registered 拦截、说明随保存落盘 ──
  {
    const fx = fixtures()
    const ctx = await mount(SourcesComp, fx)
    ctx.api.openIdentity()

    await check('② P1 编辑态：辅助入口存在；点击后面板出现且兜底标题正确', async () => {
      let page = await ctx.html()
      assert.match(page, /✦ 辅助填写/, '实例识别编辑态页头应有辅助入口')
      assert.ok(!page.includes('assist-panel'), '未点击前不渲染面板')
      ctx.api.toggleAssist()
      page = await ctx.html()
      assert.match(page, /assist-panel/, '点击后面板出现')
      assert.match(page, /配置「储能簇」的实例识别/, '未取到上下文时展示目标兜底标题')
      assert.ok(ctx.panel, '面板实例已随页面创建')
      assert.equal(ctx.api.assistBinding.value.targetKind, 'identity')
      assert.equal(ctx.api.assistBinding.value.targetId, 'cluster', 'targetId 为对象类型标识')
    })

    await check('②b 上下文请求携带场景与白名单草稿（note 对齐已保存说明）；取到后展示服务端标题', async () => {
      await ctx.panel.open(ctx.api.assistBinding.value)
      assert.equal(ctx.assist.calls.context.length, 1)
      const body = ctx.assist.calls.context[0]
      assert.equal(body.space, 'project'); assert.equal(body.targetKind, 'identity')
      assert.equal(body.targetId, 'cluster'); assert.equal(body.purpose, 'fill')
      assert.deepEqual(body.draft, { mode: 'database', connection: 'db', table: 'clusters', primaryKey: 'id', note: '现有说明' }, 'draft 是白名单形态快照，note 取自对象级说明')
      const phtml = await ctx.panelHtml()
      assert.match(phtml, /CTX\[identity:cluster\]/, '取到上下文后面板标题切换为服务端标题')
    })

    await check('②c 采纳建议：identityDraft/noteDraft 更新；form-save 零调用；出现「尚未保存」提示', async () => {
      ctx.assist.genQueue.push({ suggestions: [
        { id: 's1', label: '连接与主键', fieldKeys: ['connection', 'primaryKey'], proposed: { connection: 'db2', primaryKey: 'sn' }, state: 'ready' },
        { id: 's2', label: '对象说明', fieldKeys: ['note'], proposed: { note: '每行是一套电池簇。' }, state: 'ready' },
      ] })
      await ctx.panel.generate()
      assert.equal(ctx.assist.calls.generate.length, 1)
      assert.equal(ctx.assist.calls.generate[0].contextToken, 'tok-1')
      assert.equal(ctx.panel.adopt(), true)
      assert.equal(ctx.api.identityDraft.value.connection, 'db2')
      assert.equal(ctx.api.identityDraft.value.primary_key, 'sn', 'primaryKey 采纳写回 primary_key')
      assert.equal(ctx.api.noteDraft.value, '每行是一套电池簇。', 'note 采纳写入 noteDraft')
      assert.equal(ctx.formSave.saves.length, 0, '采纳绝不触发表单保存（form-save 零调用）')
      assert.equal(ctx.panel.canUndo.value, true)
      assert.equal(ctx.panel.justAdopted.value, true)
      const phtml = await ctx.panelHtml()
      assert.match(phtml, /已填入表单，尚未保存/, '面板提示尚未保存')
      assert.equal(ctx.b.connection, 'db', '采纳只改本地草稿：绑定的已保存连接未变')
    })

    await check('②d 手改字段：手改通知已发出；采纳被草稿指纹拦下；撤销失效', async () => {
      ctx.api.identityTableChanged('samples')
      assert.equal(ctx.api.identityDraft.value.table, 'samples')
      assert.equal(ctx.api.assistTouchTick.value, 1, '字段变更触发了手改通知')
      ctx.assist.genQueue.push({ suggestions: [{ id: 'sx', label: '越权建议', fieldKeys: ['connection'], proposed: { connection: 'db9' }, state: 'ready' }] })
      await ctx.panel.generate()
      assert.equal(ctx.panel.adopt(), false, '手改后草稿指纹漂移，采纳被拒')
      assert.equal(ctx.api.identityDraft.value.connection, 'db2', '被拒的采纳不得改动草稿')
      assert.equal(ctx.panel.stale.value, true)
      assert.equal(ctx.panel.canUndo.value, false, '手改后撤销保护解除')
      assert.equal(ctx.panel.undo(), false, '撤销不可用')
      ctx.panel.notifyDraftChanged() // 宿主 ref 通道（浏览器里由 assistTouched 经模板 ref 调用）
      assert.equal(ctx.panel.justAdopted.value, false, '手改清除「已填入」提示')
      const phtml = await ctx.panelHtml()
      assert.match(phtml, /建议已失效：表单已修改/, '面板展示过期横幅')
    })

    await check('②e 切换 registered：快照只剩 {mode,note}；同批采纳忽略连接键、说明生效', async () => {
      await ctx.panel.refreshContext() // 手改后重取上下文（浏览器里由面板按钮触发）
      ctx.api.identityModeChanged('registered')
      assert.equal(ctx.api.identityDraft.value.mode, 'registered')
      assert.equal(ctx.api.assistTouchTick.value, 2, '模式切换属手改，再次通知')
      assert.deepEqual(ctx.api.assistBinding.value.draft(), { mode: 'registered', note: '每行是一套电池簇。' }, 'registered 模式快照只有 mode 与 note')
      await ctx.panel.open(ctx.api.assistBinding.value) // 以新模式草稿重开（重取上下文）
      const body = ctx.assist.calls.context.at(-1)
      assert.deepEqual(body.draft, { mode: 'registered', note: '每行是一套电池簇。' }, 'registered 模式上下文快照不含连接/表/主键')
      ctx.assist.genQueue.push({ suggestions: [
        { id: 'r1', label: '登记模式下的连接建议', fieldKeys: ['connection'], proposed: { connection: 'dbX', note: '登记实例的业务含义说明。' }, state: 'ready' },
      ] })
      await ctx.panel.generate()
      assert.equal(ctx.panel.adopt(), true)
      assert.equal(ctx.api.identityDraft.value.connection, 'db2', 'registered 模式采纳忽略连接键')
      assert.equal(ctx.api.noteDraft.value, '登记实例的业务含义说明。')
      assert.equal(ctx.formSave.saves.length, 0, 'registered 采纳同样零保存')
    })

    await check('②f 显式保存：form-save 恰一次，采纳说明经既有 commitDesc 落盘；编辑器关闭面板收起', async () => {
      await ctx.api.saveIdentity()
      assert.equal(ctx.formSave.saves.length, 1, '只有用户点保存才落盘')
      const objs = ctx.projectState.bindings.mappingDescriptions.objects
      assert.equal(objs['mg:cluster'], '登记实例的业务含义说明。', '采纳说明随实例识别保存写入 mappingDescriptions')
      assert.equal(ctx.api.assistOpen.value, false, '保存成功关闭编辑器，面板收起')
      assert.equal(ctx.api.assistBinding.value, null, '编辑器关闭后 binding 置空')
      const page = await ctx.html()
      assert.ok(!page.includes('辅助填写'), '浏览态无辅助入口')
    })
  }

  // ── ③ P5 LinkMappings：入口、上下文请求、采纳零保存、撤销、手改过期 ──
  {
    const fx = fixtures()
    const ctx = await mount(LinksComp, fx)
    ctx.api.openNew(fx.graph.find(r => r['@id'] === 'mg:belongs')) // 新建链接映射（表字段关联编辑器）

    await check('③ P5 编辑态：辅助入口存在；面板与上下文请求正确（targetId=链接标识）', async () => {
      let page = await ctx.html()
      assert.match(page, /✦ 辅助填写/, '链接映射编辑态页头应有辅助入口')
      assert.ok(!page.includes('assist-panel'), '未点击前不渲染面板')
      ctx.api.toggleAssist()
      page = await ctx.html()
      assert.match(page, /assist-panel/, '点击后面板出现')
      assert.match(page, /维护链接映射「所属设备」/, '兜底标题含链接名称')
      assert.equal(ctx.api.assistBinding.value.targetKind, 'linkMapping')
      assert.equal(ctx.api.assistBinding.value.targetId, 'cluster.belongs')
      await ctx.panel.open(ctx.api.assistBinding.value)
      const body = ctx.assist.calls.context.at(-1)
      assert.equal(body.space, 'project'); assert.equal(body.targetKind, 'linkMapping'); assert.equal(body.targetId, 'cluster.belongs')
      assert.deepEqual(body.draft, { sourceId: '', field: '', targetSourceId: '', targetField: '', note: '' }, '新建链接映射的白名单快照')
    })

    await check('③b 采纳建议：draft 四字段+noteDraft 更新；form-save 零调用；撤销可整体回滚', async () => {
      ctx.assist.genQueue.push({ suggestions: [
        { id: 'l1', label: '起点字段', fieldKeys: ['sourceId', 'field'], proposed: { sourceId: 'src1', field: 'storage_id' }, state: 'ready' },
        { id: 'l2', label: '终点匹配与说明', fieldKeys: ['targetSourceId', 'targetField', 'note'], proposed: { targetSourceId: 'tsrc1', targetField: 'id', note: '两端字段的等价依据说明。' }, state: 'ready' },
      ] })
      await ctx.panel.generate()
      assert.equal(ctx.panel.adopt(), true)
      const d = ctx.api.draft.value
      assert.equal(d.sourceId, 'src1'); assert.equal(d.field, 'storage_id')
      assert.equal(d.targetSourceId, 'tsrc1'); assert.equal(d.targetField, 'id')
      assert.equal(ctx.api.noteDraft.value, '两端字段的等价依据说明。', 'note 采纳写入 noteDraft')
      assert.equal(d.relation, 'belongs', '采纳不改链接标识')
      assert.equal(ctx.formSave.saves.length, 0, '采纳绝不触发表单保存')
      assert.equal(ctx.panel.justAdopted.value, true)
      const phtml = await ctx.panelHtml()
      assert.match(phtml, /已填入表单，尚未保存/)
      assert.equal(ctx.panel.undo(), true, '撤销：恢复快照')
      assert.deepEqual(ctx.api.draft.value.sourceId, ''); assert.equal(ctx.api.draft.value.field, '')
      assert.equal(ctx.api.draft.value.targetField, '')
      assert.equal(ctx.api.noteDraft.value, '', '撤销同步还原说明草稿')
      assert.equal(ctx.panel.canUndo.value, false, '撤销一次性')
    })

    await check('③c 重新生成后手改：通知发出、采纳被拦、撤销失效', async () => {
      await ctx.panel.open(ctx.api.assistBinding.value) // 重取上下文（撤销后指纹已对齐，此处显式重开同目标）
      ctx.assist.genQueue.push({ suggestions: [{ id: 'l3', label: '起点字段建议', fieldKeys: ['field'], proposed: { field: 'id' }, state: 'ready' }] })
      await ctx.panel.generate()
      ctx.api.draftFieldChanged('cluster_id')
      assert.equal(ctx.api.draft.value.field, 'cluster_id')
      assert.equal(ctx.api.assistTouchTick.value, 1, '字段手改触发手改通知')
      assert.equal(ctx.panel.adopt(), false, '手改后采纳被草稿指纹拦下')
      assert.equal(ctx.api.draft.value.field, 'cluster_id')
      assert.equal(ctx.panel.canUndo.value, false)
      assert.equal(ctx.panel.stale.value, true)
      const phtml = await ctx.panelHtml()
      assert.match(phtml, /建议已失效：表单已修改/)
    })

    await check('③d 切换编辑目标（另一条链接）：binding 以新目标重建并重取上下文', async () => {
      ctx.api.openNew(fx.graph.find(r => r['@id'] === 'mg:l_extra'))
      assert.equal(ctx.api.assistOpen.value, true, '面板保持展开')
      assert.equal(ctx.api.assistBinding.value.targetId, 'cluster.l_extra', 'binding 已指向新链接')
      await ctx.panel.open(ctx.api.assistBinding.value)
      const last = ctx.assist.calls.context.at(-1)
      assert.equal(last.targetId, 'cluster.l_extra')
      assert.deepEqual(last.draft, { sourceId: '', field: '', targetSourceId: '', targetField: '', note: '' })
      ctx.api.closeEditor()
    })

    await check('③e 关闭编辑器：面板收起、binding 置空、浏览态无辅助入口', async () => {
      assert.equal(ctx.api.assistOpen.value, false)
      assert.equal(ctx.api.assistBinding.value, null)
      const page = await ctx.html()
      assert.ok(!page.includes('assist-panel'), '收起后不再渲染面板')
      assert.ok(!page.includes('辅助填写'), '浏览态无辅助入口')
    })
  }

  // ── ④ 成员模式编辑器不提供辅助入口（成员规则不在辅助范围）──
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
      assert.ok(!page.includes('✦ 辅助填写'), '成员模式不提供辅助入口')
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
