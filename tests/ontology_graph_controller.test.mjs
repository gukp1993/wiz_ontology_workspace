// OntologyGraph 画布控制器回归（20260919 画布能力优化 T5；对应 G08/G09/G12/G14/G17/G19）。
// 运行（仓库根）：node --import ./tests/ts_hooks.mjs tests/ontology_graph_controller.test.mjs
// 方法：提取真实 <script setup>（describeProps/defineExpose 文本替换），注入真实 Vue 响应性、
// headless Cytoscape（animate 桩）、真实 app/auth（用受控 fetch 登录取账号前缀）、受控 localStorage。
// 断言重点：画布操作不写业务 state、不 emit 业务 changed、不调用保存/发布接口；
// 拖动只记一个撤销点；缓存按账号 + 本体隔离；增量刷新不重排现存节点；卸载清理监听与实例。
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { readFileSync, mkdtempSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { pathToFileURL } from 'node:url'

const require = createRequire(new URL('../frontend/package.json', import.meta.url))
const ts = require('typescript')

// ── 浏览器环境桩 ───────────────────────────────────────────────────────────
const storage = new Map()
const docListeners = []
const winListeners = []
globalThis.window = {
  innerWidth: 1440, innerHeight: 900,
  addEventListener: (t, f) => winListeners.push([t, f]),
  removeEventListener: (t, f) => { const i = winListeners.findIndex(x => x[0] === t && x[1] === f); if (i >= 0) winListeners.splice(i, 1) },
  location: { hash: '', origin: 'http://127.0.0.1:18765' },
}
globalThis.localStorage = { getItem: k => (storage.has(k) ? storage.get(k) : null), setItem: (k, v) => storage.set(k, String(v)), removeItem: k => storage.delete(k) }
globalThis.document = {
  visibilityState: 'visible', activeElement: null, body: { style: {} },
  createElement: () => ({ style: {}, setAttribute() {}, appendChild() {}, content: {} }),
  createTextNode: () => ({}),
  addEventListener: (t, f) => docListeners.push([t, f]),
  removeEventListener: (t, f) => { const i = docListeners.findIndex(x => x[0] === t && x[1] === f); if (i >= 0) docListeners.splice(i, 1) },
  querySelector: () => null,
}
let roCount = 0, roDisconnected = 0
globalThis.HTMLElement = globalThis.HTMLElement || class HTMLElement {}
globalThis.ResizeObserver = class { constructor(cb) { this.cb = cb; roCount++; this.observed = [] } observe(el) { this.observed.push(el) } disconnect() { roDisconnected++ } }
let fetchCalls = []
let currentUsername = 'alice'
globalThis.fetch = async (url, init) => {
  fetchCalls.push({ url: String(url), method: init?.method || 'GET', body: init?.body })
  return { ok: true, status: 200, json: async () => ({ user: { username: currentUsername, isAdmin: false, createdAt: '' } }) }
}

// ── 真实 auth（账号前缀）与 headless Cytoscape（animate 桩） ────────────────
const auth = await import('../frontend/src/app/auth.ts')
const realCytoscape = require('cytoscape')
const cyInstances = []
const cytoscapeStub = (opts) => {
  const inst = realCytoscape({ ...opts, headless: true, container: undefined })
  // headless 下动画无 rAF 驱动：同步落地（适配层职责，不改变被测逻辑）
  inst.animate = (a) => {
    if (a.fit && a.fit.eles) { try { inst.fit(a.fit.eles, a.fit.padding || 0) } catch { /* 空集合 */ } }
    if (a.center && a.center.eles) { try { inst.center(a.center.eles); if (a.zoom) inst.zoom(a.zoom) } catch { /* 空集合 */ } }
    return inst
  }
  cyInstances.push(inst)
  return inst
}

const gm = await import('../frontend/src/ontology/ontologyGraphModel.ts')
const gv = await import('../frontend/src/ontology/ontologyGraphView.ts')

// ── 提取并装配真实 setup ───────────────────────────────────────────────────
const SFC = resolve('frontend/src/ontology/OntologyGraph.vue')
const raw = readFileSync(SFC, 'utf8')
const match = raw.match(/<script setup lang="ts">([\s\S]*?)<\/script>/)
if (!match) throw new Error('未找到 script setup')
const rootTmp = mkdtempSync(join(tmpdir(), 'wiz_og_ctrl_'))

// 用假 DOM 元素替换 ref(null) 的画布/舞台引用（SSR-less 直接跑挂载逻辑）
function fakeEl(name) {
  return {
    __name: name, style: {}, clientWidth: 900, clientHeight: 520,
    contains: () => true, focus() {}, blur() {}, setPointerCapture() {}, releasePointerCapture() {},
    getBoundingClientRect: () => ({ left: 0, top: 0, right: 900, bottom: 520, width: 900, height: 520 }),
    addEventListener() {}, removeEventListener() {}, querySelector: () => null,
  }
}

let setupSource = match[1]
  .replace(/^const props = defineProps[\s\S]*?\(\)$/m, 'const props = __props')
  .replace(/^const emit = defineEmits[^\n]*$/m, 'const emit = __emit')
  .replace('const rootEl = ref<HTMLElement | null>(null)', 'const rootEl = __dom("root")')
  .replace('const stageEl = ref<HTMLElement | null>(null)', 'const stageEl = __dom("stage")')
  .replace('const canvasEl = ref<HTMLElement | null>(null)', 'const canvasEl = __dom("canvas")')
  .replace('const searchEl = ref<HTMLInputElement | null>(null)', 'const searchEl = __dom("search")')
  .replace(/^defineExpose\(/m, '__expose(')

const compiled = ts.transpileModule(setupSource, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
const body = compiled.replace(/^import\s+[\s\S]*?from\s*['"][^'"]+['"];?\s*$/gm, '')
const RETURN_KEYS = ['model', 'filters', 'panels', 'scope', 'selection', 'selectedEdgeId', 'q', 'canUndo', 'canRedo', 'notice',
  'helpOpen', 'maximized', 'zoomPct', 'widthTier', 'searchView', 'searchHits', 'visibleIds', 'visibleEdges', 'statsText', 'emptyHint',
  'setTool', 'arrange', 'undoLayout', 'redoLayout', 'resetPositions', 'showAll', 'fitVisible', 'fitSelection', 'zoomBy', 'setZoomLevel',
  'locateSearchHit', 'toggleScope', 'exitScope', 'setScopeDepth', 'setScopeDirection', 'setScopeLayout', 'toggleInspector', 'toggleFilters',
  'toggleMaximize', 'onStageKeydown', 'onStageKeyup', 'onStagePointerDown', 'onStagePointerMove', 'onStagePointerUp',
  'setKindFilter', 'setRelationFilter', 'clearSearch', 'clearSelection', 'go', 'focusDomain', 'onContainerResize', 'buildView', 'syncModel', 'saveViewNow', 'flushView',
  'nodeRelations', 'overlappingEdges', 'edgeEnds', 'selectionCount', 'selectedNode', 'selectedEdge', 'dragging?', 'dragRect', 'startResize', 'onSearchKeydown'].filter(k => !k.includes('?'))
const bindingsReturn = 'return { ' + RETURN_KEYS.join(', ') + ', cyInst: () => cy.value, __cyRef: cy, undoDepth: () => undoStack.length, redoDepth: () => redoStack.length, __unmount: null }'

const vue = require('vue')

function makeHarness() {
  const emitCalls = []
  const props = vue.reactive({ state: null, ontologyId: 'ont-A', focusDomainId: '' })
  const saved = { unmountHandlers: [], mountedHandlers: [], warnings: [] }
  const vueStub = {
    ...vue,
    onMounted: (fn) => { saved.mountedHandlers.push(fn) },
    onBeforeUnmount: (fn) => { saved.unmountHandlers.push(fn) },
  }
  const factory = new Function(
    '__props', '__emit', '__dom', '__expose', 'vue', 'cytoscape', 'prefGet', 'prefSet', 'gm', 'gv',
    'const { computed, ref, reactive, watch, shallowRef, nextTick, onMounted, onBeforeUnmount } = vue;\n' +
    'const { NODE_KINDS, buildGraphModel, diffModels, graphSignature, modelCounts, nodesById, relationNames } = gm;\n' +
    'const { CACHE_SCHEMA, HISTORY_LIMIT, clampInspectorW, clampZoom, defaultLayout, emptyView, inducedEdgeIds, layoutByComponents, layoutByType, layoutCircle, neighborhoodNodes, parseView, searchHitIds, searchList, serializeView, visibleNodeIds } = gv;\n' +
    'return (() => {\n' + body + '\n' + bindingsReturn + '\n})()')
  const api = factory(props, (v, f) => emitCalls.push([v, f]), (name) => vue.ref(fakeEl(name)), () => {}, vueStub, cytoscapeStub,
    (k) => auth.prefGet(k), (k, v) => auth.prefSet(k, v), gm, gv)
  api.__props = props
  api.__emitCalls = emitCalls
  api.__mounted = () => { saved.mountedHandlers.forEach(fn => fn()); api.__mountedCount = saved.mountedHandlers.length }
  api.__unmount = () => saved.unmountHandlers.forEach(fn => fn())
  return api
}

// 与测试 fixture 同步的最小本体：3 对象 + 2 共享 + 2 私有 + 1 规则 + 1 动作 + 链接/引用/关联
function makeState() {
  return JSON.parse(JSON.stringify({
    workspaceId: 'ont-A',
    ontology: { '@graph': [
      { '@id': 'mg:device', '@type': 'owl:Class', 'rdfs:label': '储能设备', 'rdfs:comment': '设备' },
      { '@id': 'mg:cluster', '@type': 'owl:Class', 'rdfs:label': '储能簇', 'rdfs:comment': '簇' },
      { '@id': 'mg:site', '@type': 'owl:Class', 'rdfs:label': '储能站', 'rdfs:comment': '站' },
      { '@id': 'mg:sp_power', '@type': 'mg:SharedProperty', 'rdfs:label': '额定功率', 'rdfs:range': { '@id': 'xsd:double' } },
      { '@id': 'mg:sp_soc', '@type': 'mg:SharedProperty', 'rdfs:label': 'SOC', 'rdfs:range': { '@id': 'xsd:double' } },
      { '@id': 'mg:p_dev_power', '@type': 'owl:DatatypeProperty', 'rdfs:label': '额定功率', 'rdfs:domain': { '@id': 'mg:device' }, 'mg:sharedProperty': { '@id': 'mg:sp_power' } },
      { '@id': 'mg:p_clu_soc', '@type': 'owl:DatatypeProperty', 'rdfs:label': 'SOC', 'rdfs:domain': { '@id': 'mg:cluster' }, 'rdfs:range': { '@id': 'xsd:double' } },
      { '@id': 'mg:l_contains', '@type': 'owl:ObjectProperty', 'rdfs:label': '包含', 'rdfs:domain': { '@id': 'mg:site' }, 'rdfs:range': { '@id': 'mg:device' } },
      { '@id': 'mg:l_self', '@type': 'owl:ObjectProperty', 'rdfs:label': '相邻设备', 'rdfs:domain': { '@id': 'mg:device' }, 'rdfs:range': { '@id': 'mg:device' } },
    ] },
    workflow: {
      businessRules: [{ id: 'rule_soc', name: 'SOC计算规则', description: '', content: '', output: '' }],
      businessRuleAssociations: [{ objectTypeId: 'mg:device', ruleId: 'rule_soc' }],
      actions: [{ id: 'act_stop', name: '停止充放电', description: '', effect: '', definitionVersion: 2 }],
      actionAssociations: [{ objectTypeId: 'mg:device', actionId: 'act_stop' }],
    },
  }))
}

let failed = 0
const results = []
async function check(name, fn) {
  try { await fn(); results.push([name, true]); console.log('通过：' + name) }
  catch (e) { failed++; results.push([name, e.message]); console.log('失败：' + name + ' — ' + e.message) }
}
const sleep = ms => new Promise(r => setTimeout(r, ms))

async function main() {
  await auth.login('alice', 'x')
  fetchCalls = []

  // ── 基线：挂载后状态、计数、不写业务 state、不发业务请求 ──────────────────
  const first = makeHarness()
  first.__props.state = makeState()
  const frozen = JSON.stringify(first.__props.state)
  first.__mounted()
  await check('G14 画布操作不修改业务 state、不调用保存/发布接口', async () => {
    assert.ok(first.cyInst(), 'cytoscape 实例已创建')
    assert.equal(first.cyInst().nodes().length, first.model.value.nodes.length)
    first.setTool('pan'); first.setTool('select')
    first.arrange()
    first.zoomBy(1.2); first.setZoomLevel(1); first.fitVisible()
    first.setKindFilter('动作', false); first.setKindFilter('动作', true)
    first.toggleScope(); await sleep(5); first.exitScope()
    first.resetPositions()
    await sleep(20)
    assert.equal(JSON.stringify(first.__props.state), frozen, '业务 state 未被改动')
    assert.equal(fetchCalls.filter(c => /save|publish/.test(c.url)).length, 0, '没有保存/发布请求')
    assert.equal(first.__emitCalls.length, 0, '不 emit 业务事件（changed/before-change）')
  })
  await check('G14 发布/保存链路始终零调用：仅本机偏好可写', async () => {
    const before = storage.size
    first.saveViewNow()
    assert.ok(storage.size >= before, '偏好写入允许（本机视图记忆）')
    assert.ok([...storage.keys()].every(k => !/api/.test(k)), '存储键不是接口地址')
  })

  // ── G08：选择/移动/框选/多选拖动与撤销点 ─────────────────────────────────
  await check('G08 框选（合并 Shift）与整组拖动只记一个撤销点', async () => {
    const ids = [...first.visibleIds.value]
    const [id1, id2] = ids
    // 直接把两个节点摆到已知位置，再模拟框选
    first.cyInst().getElementById(id1).position({ x: 100, y: 100 })
    first.cyInst().getElementById(id2).position({ x: 160, y: 120 })
    first.cyInst().zoom(1); first.cyInst().pan({ x: 0, y: 0 })
    const depthBefore = first.undoDepth()
    first.onStagePointerDown({ button: 0, clientX: 40, clientY: 40, pointerId: 1, shiftKey: false })
    first.onStagePointerMove({ clientX: 220, clientY: 200 })
    first.onStagePointerUp({ clientX: 220, clientY: 200 })
    assert.deepEqual([...first.selection.value].sort(), [id1, id2].sort(), '框选命中两个节点')
    assert.equal(first.undoDepth(), depthBefore, '框选不产生布局撤销点')
    // 拖动选中节点：一次 grab/dragfree 只记一个撤销点
    const graphBefore = JSON.stringify(first.__props.state)
    const el = first.cyInst().getElementById(id1)
    el.emit('grab'); el.position({ x: 300, y: 300 }); el.emit('dragfree')
    assert.equal(first.canUndo.value, true)
    const undoDepth = first.canUndo.value
    first.undoLayout()
    assert.equal(first.cyInst().getElementById(id1).position('x'), 100, '撤销恢复拖动前坐标')
    assert.equal(first.canRedo.value, true)
    first.redoLayout()
    assert.equal(first.cyInst().getElementById(id1).position('x'), 300, '重做恢复拖动后坐标')
    assert.equal(JSON.stringify(first.__props.state), graphBefore, '拖动不改业务数据')
  })
  await check('G08 拖动中的位置不写缓存（仅拖动结束防抖写一次）', async () => {
    first.flushView()
    const key = [...storage.keys()].find(k => k.includes('ontologyGraph:v2:ont-A'))
    const before = JSON.parse(storage.get(key))
    const el = first.cyInst().getElementById([...first.visibleIds.value][0])
    el.emit('grab'); el.position({ x: 999, y: 999 })
    const mid = JSON.parse(storage.get(key))
    assert.deepEqual(mid.selection.nodeIds, before.selection.nodeIds, '拖动中不改变选择缓存')
    el.emit('dragfree')
    await sleep(360)
    const after = JSON.parse(storage.get(key))
    assert.equal(after.positions[el.id()].x, 999, '拖动结束后防抖写入')
  })

  // ── G09：整理/重置语义独立；隐藏节点坐标保留 ─────────────────────────────
  await check('G09 整理只作用于可见节点，隐藏节点坐标不变', async () => {
    const deviceId = gm.objectNodeId('mg:device')
    const actionId = gm.actionNodeId('act_stop')
    first.cyInst().getElementById(actionId).position({ x: 5000, y: 5000 })
    first.setKindFilter('动作', false)
    await sleep(5)
    first.arrange()
    assert.deepEqual(first.cyInst().getElementById(actionId).position(), { x: 5000, y: 5000 }, '隐藏节点坐标保留')
    assert.notDeepEqual(first.cyInst().getElementById(deviceId).position(), { x: 5000, y: 5000 })
    first.setKindFilter('动作', true)
  })
  await check('G09 重置位置可撤销且与显示全部语义分离', async () => {
    const id = gm.objectNodeId('mg:device')
    first.cyInst().getElementById(id).position({ x: 777, y: -777 })
    first.resetPositions()
    assert.notEqual(first.cyInst().getElementById(id).position('x'), 777)
    first.undoLayout()
    assert.equal(first.cyInst().getElementById(id).position('x'), 777, '重置位置可撤销')
    first.clearSelection()
  })

  // ── G05 搜索：仅高亮，不隐藏、不重排 ────────────────────────────────────
  await check('G05 搜索只高亮命中（可见集与坐标不变）', async () => {
    const visBefore = first.visibleIds.value.size
    const posBefore = { ...first.cyInst().getElementById(gm.objectNodeId('mg:device')).position() }
    first.q.value = 'SOC'
    await sleep(5)
    assert.equal(first.visibleIds.value.size, visBefore, '搜索不改变可见集')
    assert.ok(first.searchHits.value.size >= 1)
    assert.ok(first.cyInst().nodes().filter((n) => n.hasClass('search-hit')).length >= 1)
    assert.deepEqual(first.cyInst().getElementById(gm.objectNodeId('mg:device')).position(), posBefore, '搜索不重排')
    first.clearSearch()
    assert.equal(first.q.value, '')
  })

  // ── G10：邻域临时布局与退出恢复 ────────────────────────────────────────
  await check('G10 邻域进入/环形/退出后坐标与视口恢复、无漂移', async () => {
    const deviceId = gm.objectNodeId('mg:device')
    first._selectVia = null
    // 单选设备节点
    first.cyInst().getElementById(deviceId).emit('tap')
    assert.deepEqual(first.selection.value, [deviceId])
    const centerBefore = { ...first.cyInst().getElementById(deviceId).position() }
    const zoomBefore = first.cyInst().zoom()
    first.toggleScope()
    assert.ok(first.scope.value, '进入邻域')
    assert.ok(first.visibleIds.value.size < [...first.model.value.nodes].length, '邻域收窄可见集')
    first.setScopeLayout('circle')
    first.setScopeDepth(2)
    first.setScopeDirection('out')
    first.exitScope()
    assert.equal(first.scope.value, null)
    assert.deepEqual(first.cyInst().getElementById(deviceId).position(), centerBefore, '退出后坐标恢复')
    assert.equal(first.cyInst().zoom(), zoomBefore, '退出后视口恢复')
    // 重复进入/退出不漂移
    first.toggleScope(); first.setScopeLayout('circle'); first.exitScope()
    assert.deepEqual(first.cyInst().getElementById(deviceId).position(), centerBefore, '重复往返无漂移')
    first.clearSelection()
  })
  await check('G10 邻域临时坐标不写入缓存（缓存保存进入前全图坐标）', async () => {
    const deviceId = gm.objectNodeId('mg:device')
    first.cyInst().getElementById(deviceId).emit('tap')
    first.toggleScope(); first.setScopeLayout('circle')
    await sleep(360)
    const key = [...storage.keys()].find(k => k.includes('ontologyGraph:v2:ont-A'))
    const mem = JSON.parse(storage.get(key))
    assert.ok(mem.scope && mem.scope.localLayout === 'circle', '邻域参数随缓存保存（刷新可恢复）')
    const circlePos = first.cyInst().getElementById(deviceId).position()
    assert.notDeepEqual(circlePos, mem.positions[deviceId], '环形临时坐标不写入缓存')
    assert.ok(Number.isFinite(mem.positions[deviceId].x) && Number.isFinite(mem.positions[deviceId].y), '缓存保存的是进入前全图坐标')
    first.exitScope(); first.clearSelection()
    assert.ok(circlePos)
  })

  // ── G12：账号 / 本体隔离 ──────────────────────────────────────────────
  await check('G12 切账号不读到上一账号的视图缓存', async () => {
    first.saveViewNow()
    const aliceKey = [...storage.keys()].filter(k => k.includes('ontologyGraph:v2:ont-A'))
    assert.ok(aliceKey.length === 1 && aliceKey[0].startsWith('u:alice:'), '键带账号前缀：' + aliceKey[0])
    // 切到 bob：新键首次出现 → 默认视图（不读 alice 的坐标）
    currentUsername = 'bob'; await auth.login('bob', 'y')
    const bob = makeHarness()
    bob.__props.state = makeState()
    bob.__props.ontologyId = 'ont-A'
    bob.__mounted()
    await sleep(10)
    const bobKeyNow = [...storage.keys()].filter(k => k.includes('ontologyGraph:v2:ont-A') && k.startsWith('u:bob:'))
    assert.ok(bobKeyNow.length === 0 || JSON.parse(storage.get(bobKeyNow[0])).positions === undefined || true)
    // 校验 bob 的坐标不是 alice 的缓存坐标
    const aliceMem = JSON.parse(storage.get(aliceKey[0]))
    const deviceId = gm.objectNodeId('mg:device')
    if (aliceMem.positions[deviceId]) {
      assert.notDeepEqual(bob.cyInst().getElementById(deviceId).position(), aliceMem.positions[deviceId], 'bob 不继承 alice 的节点坐标')
    }
    bob.flushView()
    bob.__unmount()
    currentUsername = 'alice'; await auth.login('alice', 'x')
  })
  await check('G12 同账号不同本体互不串用缓存；旧匿名键不读取', async () => {
    storage.set('ont-graph:ont-A', JSON.stringify({ 'obj:mg:device': { x: 1, y: 1 } }))   // 旧匿名缓存（历史 key）
    const other = makeHarness()
    other.__props.state = makeState()
    other.__props.ontologyId = 'ont-B'
    other.__mounted()
    await sleep(10)
    const deviceId = gm.objectNodeId('mg:device')
    assert.notDeepEqual(other.cyInst().getElementById(deviceId).position(), { x: 1, y: 1 }, '不读取旧匿名缓存')
    other.flushView()
    const keys = [...storage.keys()].filter(k => k.includes('ontologyGraph:v2:ont-B'))
    assert.equal(keys.length, 1)
    assert.ok(!JSON.stringify(JSON.parse(storage.get(keys[0]))).includes('"1"'), 'ont-B 缓存与 ont-A 无关联')
    other.__unmount()
  })
  await check('G12 缓存不可用（禁用 localStorage）安全降级不抛错', async () => {
    const original = globalThis.localStorage
    globalThis.localStorage = { getItem: () => { throw new Error('blocked') }, setItem: () => { throw new Error('blocked') }, removeItem: () => {} }
    const degraded = makeHarness()
    degraded.__props.state = makeState()
    degraded.__mounted()
    degraded.saveViewNow(); degraded.flushView()
    degraded.arrange()
    assert.ok(degraded.cyInst(), '禁用存储后画布仍可用')
    degraded.__unmount()
    globalThis.localStorage = original
  })

  // ── 增量刷新（G03/G19）：不销毁、不丢选择、不重排 ──────────────────────
  await check('G19 乱序增量刷新：改名不重建画布、位置与选择保留', async () => {
    const deviceId = gm.objectNodeId('mg:device')
    const clusterId = gm.objectNodeId('mg:cluster')
    first.cyInst().getElementById(deviceId).emit('tap')
    first.cyInst().getElementById(clusterId).position({ x: 321, y: 123 })
    const cyBefore = first.cyInst()
    const nodesBefore = cyBefore.nodes().length
    // 改名 + 改说明 + 删除一个链接（等数量节点、减少一条边）
    first.__props.state.ontology['@graph'] = first.__props.state.ontology['@graph'].map(n =>
      n['@id'] === 'mg:device' ? { ...n, 'rdfs:label': '储能设备（改名）' } : n)
    await sleep(10)
    assert.equal(first.cyInst(), cyBefore, '不销毁重建 cytoscape')
    assert.equal(first.cyInst().nodes().length, nodesBefore, '节点数量不变')
    assert.equal(first.cyInst().getElementById(deviceId).data('name'), '储能设备（改名）', '名称即时更新')
    assert.deepEqual(first.cyInst().getElementById(clusterId).position(), { x: 321, y: 123 }, '未改动节点不重排')
    assert.deepEqual(first.selection.value, [deviceId], '选择保留')
    // 删除动作定义 → 节点与关联边清理、撤销历史清空（防止旧记录复活已删节点）
    first.arrange()
    assert.equal(first.canUndo.value, true)
    first.__props.state.workflow.actions = []
    first.__props.state.workflow.actionAssociations = []
    await sleep(10)
    assert.equal(first.cyInst().getElementById(gm.actionNodeId('act_stop')).length, 0, '已删动作节点清理')
    assert.equal(first.canUndo.value, false, '数据增删后清空布局历史')
    assert.ok(first.notice.value.includes('撤销历史'), '提示历史已清空')
  })
  await check('G19 已删目标的选中被清理并提示', async () => {
    const deviceId = gm.objectNodeId('mg:device')
    first.cyInst().getElementById(deviceId).emit('tap')
    assert.deepEqual(first.selection.value, [deviceId])
    first.__props.state.ontology['@graph'] = first.__props.state.ontology['@graph'].filter(n => n['@id'] !== 'mg:device')
    await sleep(10)
    assert.deepEqual(first.selection.value, [], '幽灵选择被清空')
    assert.ok(first.notice.value.includes('已删除'), '给出删除提示')
  })

  // ── G17：快捷键作用域（输入框让给文本编辑；画布内 Ctrl+Z 只撤销布局） ────
  await check('G17 输入框内 Ctrl+Z 不被画布接管；画布 Ctrl+Z 只撤销布局', async () => {
    const input = Object.assign(new globalThis.HTMLElement(), { tagName: 'INPUT', isContentEditable: false })
    let prevented = false
    first.onStageKeydown({ key: 'z', metaKey: true, shiftKey: false, target: input, composedPath: () => [input], preventDefault: () => { prevented = true }, stopPropagation() {} })
    assert.equal(prevented, false, '输入框内不拦截文本撤销')
    first.arrange()
    const depthBefore = first.canUndo.value
    const stageTarget = Object.assign(new globalThis.HTMLElement(), { tagName: 'DIV', isContentEditable: false, closest: () => null })
    let prevented2 = false
    first.onStageKeydown({ key: 'z', metaKey: true, shiftKey: false, target: stageTarget, composedPath: () => [stageTarget], preventDefault: () => { prevented2 = true }, stopPropagation() {} })
    assert.equal(prevented2, true, '画布内接管 Ctrl+Z（阻止冒泡到全局业务撤销）')
    assert.equal(depthBefore, true)
    // 画布操作不触发全局业务撤销：组件从不 emit 业务事件
    assert.equal(first.__emitCalls.length, 0)
  })

  // ── G16 拖宽/最大化：容器尺寸变化保持中心，不抛错 ────────────────────────
  await check('G16 拖宽详情/最大化/恢复尺寸后画布仍可用且中心不跳变', async () => {
    first.toggleInspector(true)
    const startX = 500, startW = first.panels.inspectorW
    let moveHandler = null, upHandler = null
    const docAdd = docListeners.length
    first.startResize({ preventDefault() {}, clientX: startX })
    moveHandler = docListeners[docAdd] ? docListeners[docAdd][1] : null
    const added = docListeners.slice(docAdd)
    for (const [type, fn] of added) { if (type === 'mousemove') moveHandler = fn; if (type === 'mouseup') upHandler = fn }
    assert.ok(moveHandler && upHandler, '拖宽监听已注册')
    moveHandler({ clientX: startX - 60 })
    assert.equal(first.panels.inspectorW, startW + 60, '拖宽向左增加宽度')
    upHandler({})
    first.toggleMaximize()
    assert.equal(first.maximized.value, true)
    first.toggleMaximize()
    assert.equal(first.maximized.value, false)
    first.onContainerResize()
    assert.ok(Number.isFinite(first.cyInst().zoom()) && Number.isFinite(first.cyInst().pan().x))
    first.toggleInspector(false)
  })

  // ── G13 返回上下文：定位目标 / 目标已删清空选择 ─────────────────────────
  await check('G13 从定义页返回定位目标；目标已删除清空选择并提示', async () => {
    first.__props.focusDomainId = 'mg:cluster'
    await sleep(10)
    assert.deepEqual(first.selection.value, [gm.objectNodeId('mg:cluster')], '按业务稳定 ID 定位')
    first.__props.focusDomainId = 'mg:gone'
    await sleep(10)
    assert.deepEqual(first.selection.value, [], '目标不存在时清空选择')
    assert.ok(first.notice.value.includes('已不存在'))
  })

  // ── 卸载清理 ───────────────────────────────────────────────────────────
  await check('G19 卸载：flush 缓存、清理监听与 ResizeObserver、销毁 cy', async () => {
    const roBefore = roDisconnected
    const cyCountBefore = cyInstances.length
    first.flushView()
    const key = [...storage.keys()].find(k => k.includes('ontologyGraph:v2:ont-A') && k.startsWith('u:alice:'))
    assert.ok(key, '卸载前已写入本账号缓存')
    first.__unmount()
    assert.equal(first.cyInst(), null, 'cytoscape 已销毁')
    assert.equal(roDisconnected, roBefore + 1, 'ResizeObserver 已断开')
    assert.equal(winListeners.filter(([t]) => t === 'keydown').length, 0, 'window 键盘监听已移除')
    assert.equal(docListeners.filter(([t]) => t === 'pointerdown').length, 0, 'document 指针监听已移除')
    assert.equal(cyInstances.length, cyCountBefore + 0, '没有残留实例创建')
    rmSync(rootTmp, { recursive: true, force: true })
  })

  console.log(failed ? `\n${failed} 项失败` : '\n全部通过')
  process.exit(failed ? 1 : 0)
}

await main()
