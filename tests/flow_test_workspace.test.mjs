// FlowTestWorkspace 组件行为回归（20260919_函数编排优化审阅修正 R01–R04）。
// 运行（仓库根）：node --import ./tests/ts_hooks.mjs tests/flow_test_workspace.test.mjs
// 方法：@vue/compiler-sfc 提取 <script setup> 编译并转译，注入真实 Vue 响应性、
// 延迟 runFlow 桩与可控 appConfirm 桩，直接驱动 setup 生命周期：
//   A01 运行防重入与生命周期释放；A02 晚回包不落切换后上下文；
//   A03 多路径勾选保持/继续/缺中段拒绝；A06/A07 入口去重与空值语义；
//   A08 主入口恢复范围与快捷入口覆盖；A09 快照元信息来自提交时。
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const require = createRequire(new URL('../frontend/package.json', import.meta.url))
const ts = require('typescript')
const { parse, compileScript } = require('@vue/compiler-sfc')

let failed = 0
function check(name, cond, detail = '') {
  console.log(`${cond ? '通过' : '失败'}：${name}${cond ? '' : ' — ' + (detail || '断言不成立')}`)
  if (!cond) failed++
}

// ── 合成编排：A→B→C→D→E 主链；D 另外依赖 C2 形成菱形（a→b→d / a→c2→d） ──────
const node = (id, name, upstreamIds, code) => ({
  id: 'nd_' + id, kind: 'calc', name, description: '',
  inputs: upstreamIds.map(u => ({ id: 'in_' + u, name: u, label: u, type: { type: 'number' }, source: { kind: 'node', nodeId: 'nd_' + u, outputId: 'out_' + u } })),
  outputs: [{ id: 'out_' + id, name: id, label: id, type: { type: 'number' } }],
  implementation: { language: 'calc', mode: 'formula', formulas: { [id]: code }, providerId: '' },
})
const flowState = {
  flowId: 'flow-acc', name: '组件回归编排', description: '', status: 'active',
  inputs: [{ id: 'fin_x', name: 'x', label: '初始值', type: { type: 'number' } }],
  outputs: [], connections: [], layout: { positions: {}, zoom: 1, pan: { x: 0, y: 0 } },
  nodes: [
    { ...node('a', 'A 整理', ['fin_x'], '{a}'), inputs: [{ id: 'in_fin_x', name: 'x', label: 'x', type: { type: 'number' }, source: { kind: 'flowInput', inputId: 'fin_x' } }] },
    node('b', 'B 加二', ['a'], '{b}: {a} + 2'),
    node('c', 'C 乘三', ['b'], '{c}: {b} * 3'),
    node('c2', 'C2 乘六', ['a'], '{c2}: {a} * 6'),
    node('d', 'D 汇合', ['c', 'c2'], '{d}: {c}'),
    node('e', 'E 放大', ['d'], '{e}: {d} * 100'),
  ],
}

// ── 提取 <script setup> 并包装为可调用 setup ─────────────────────────────────
const sfcSource = readFileSync(resolve('frontend/src/flow/FlowTestWorkspace.vue'), 'utf8')
const { descriptor } = parse(sfcSource, { filename: 'FlowTestWorkspace.vue' })
// 取原始 setup 体（不经 compileScript 包装，避免默认导出外壳）：compiler 宏以运行时替身替换
const rawSetup = descriptor.scriptSetup.content
  .replace(/^const props = defineProps<.*>\(\)$/m, 'const props = __props')
  .replace(/^defineExpose\(/m, '__expose(')
const js = ts.transpileModule(rawSetup, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
const body = js
  .replace(/^import\s+[\s\S]*?from\s*['"][^'"]+['"];?\s*$/gm, '')
const bindingsReturn = 'return { scopeKind, singleId, segStart, segEnd, explicitIds, plan, planError, segPaths, entryRows, overrideTexts, overrideEmpty, entryTexts, entryEmpty, run, running, pendingConfirm, runBusy, result, formError, snapName, openScope, toggleExplicit, onSegChange, onScopeChange }'
const vue = require('vue')
const flowModel = await import('../frontend/src/flow/flowModel.ts')

async function buildWorkspace({ projectId = 'proj-1', projectName = '验收项目', revision = 'rev-1' } = {}) {
  const runCalls = []
  let respond = () => ({ status: 'success', nodeResults: [] })
  const runFlow = async payload => {
    runCalls.push(payload)
    return await new Promise((res, rej) => {
      setTimeout(() => {
        try { res(respond(payload)) } catch (e) { rej(e) }
      }, 0)
    })
  }
  let confirmAnswer = true
  let confirmCalls = 0
  let deferMode = false
  let pendingResolve = null
  const appConfirm = async () => {
    confirmCalls++
    if (deferMode) return await new Promise(r => { pendingResolve = r })
    return confirmAnswer
  }
  function deferConfirm() { deferMode = true; pendingResolve = null }
  function resolveConfirm(v) { const r = pendingResolve; pendingResolve = null; deferMode = false; r && r(v) }
  const props = vue.reactive({ state: JSON.parse(JSON.stringify(flowState)), projectId, projectName, revision })
  const factory = new Function(
    '__props', '__expose', 'vue', 'fm', 'appConfirm', 'runFlow', 'AppSelect',
    'const { computed, ref, watch } = vue;\n' +
    'const {\n' +
    '  TYPE_LABELS, clone, configSignature, dependencyPaths, describeOutput, missingUpstreams,\n' +
    '  nameOfFlowNode, processingNodes, testInputSignature, testScopePlan, validateTestValue, writeCapabilities,\n' +
    '} = fm;\n' +
    'return (async () => {\n' + body + '\n' + bindingsReturn + '\n})()')
  const bindings = await factory(props, () => {}, vue, flowModel, appConfirm, runFlow, {})
  bindings.__props = props // 便于模拟上下文切换（props 为受控普通对象）
  return {
    bindings, runCalls,
    setRespond(fn) { respond = fn },
    deferConfirm() { deferMode = true; pendingResolve = null },
    resolveConfirm(v) { const r = pendingResolve; pendingResolve = null; deferMode = false; r && r(v) },
    setConfirm(answer) { confirmAnswer = answer; confirmCalls = 0; return () => confirmCalls },
    confirmCount: () => confirmCalls,
  }
}

const sleep = ms => new Promise(r => setTimeout(r, ms))

async function main() {
  // ── A01：运行防重入 + 生命周期释放 ────────────────────────────────────────────
  {
    const ws = await buildWorkspace()
    const b = ws.bindings
    b.openScope('all')
    b.entryTexts.value['fin_x'] = '8'
    let release
    ws.setRespond(() => new Promise(res => { release = () => res({ status: 'success', nodeResults: [] }) }))
    b.run()
    await sleep(5)
    console.log('[keys]', Object.keys(b).join(','))
    check('A01 连点：请求只发一次', ws.runCalls.length === 1, ws.runCalls.length + ' formError=' + (b.formError && b.formError.value))
    check('A01 运行中 running=true', b.running.value === true)
    b.run(); b.run()
    await sleep(10)
    check('A01 运行中连点不重复提交', ws.runCalls.length === 1, ws.runCalls.length)
    check('A01 按钮等待态 runBusy', b.runBusy.value === true)
    release()
    await sleep(10)
    check('A01 完成后 running 释放且结果就绪', b.running.value === false && b.result.value?.status === 'done')
    ws.setRespond(() => { throw new Error('网络断开') })
    b.run()
    await sleep(10)
        console.log('[诊断A01]', 'running=' + b.running.value, 'runCalls=' + ws.runCalls.length, 'result=' + JSON.stringify(b.result.value && { status: b.result.value.status, error: b.result.value.error }), 'formError=' + b.formError.value, 'runBusy=' + b.runBusy.value)
    check('A01 失败后 running 释放且状态可读', b.running.value === false && b.result.value?.status === 'request-error')
    ws.setRespond(() => ({ status: 'success', nodeResults: [] }))
    b.run()
    await sleep(10)
    check('A01 失败后可再次运行', ws.runCalls.length === 3)
    await sleep(10)
  }

  // ── A01b：写操作确认取消零请求；确认与请求使用同一冻结快照 ─────────────────────
  {
    const ws = await buildWorkspace()
    const b = ws.bindings
    // 把 A 节点改为允许写的 SQL 以触发执行摘要确认（写能力分类沿用现有实现）
    b.__props.state.nodes[0].kind = 'sql'
    b.__props.state.nodes[0].implementation = { language: 'sql', connectionId: 'c1', sql: 'UPDATE t SET v = 1 WHERE id = :x' }
    b.__props.state.nodes[0].execution = { allowWrite: true }
    b.openScope('all')
    b.entryTexts.value['fin_x'] = '8'
    ws.deferConfirm() // 挂起确认弹窗
    b.run()
    await sleep(10)
    check('A01b 确认挂起：零请求且 busy', ws.runCalls.length === 0 && b.runBusy.value === true && b.pendingConfirm.value === true)
    b.entryTexts.value['fin_x'] = '999' // 弹窗挂起期间修改输入
    ws.resolveConfirm(true)
    await sleep(20)
    check('A01b 确认与请求使用冻结快照（入口仍为 8，未带入 999）', ws.runCalls.length === 1 && ws.runCalls[0].inputs['fin_x'] === 8,
      JSON.stringify(ws.runCalls[0]?.inputs))
    // 取消路径：再次运行并取消确认
    ws.setConfirm(false)
    b.run()
    await sleep(10)
    check('A01b 确认取消：零请求且立即释放', ws.runCalls.length === 1 && b.runBusy.value === false)
  }

  // ── A02：切项目后在途回包不落新上下文 ────────────────────────────────────────
  {
    const ws = await buildWorkspace({ projectId: 'proj-A', projectName: '项目A' })
    const b = ws.bindings
    b.openScope('all')
    b.entryTexts.value['fin_x'] = '1'
    let release
    ws.setRespond(() => new Promise(res => { release = () => res({ status: 'success', nodeResults: [] }) }))
    b.run()
    await sleep(5)
    check('A02 运行中', b.running.value === true)
    b.__props.projectId = 'proj-B' // 切项目（cacheContext 变化 → 清结果并作废在途回包）
    await sleep(5)
    release()
    await sleep(10)
    check('A02 切项目后旧回包不落新上下文（result 已被清空）', b.result.value === null)
  }

  // ── A03/A08：范围保留、多路径勾选、并列全图 ──────────────────────────────────
  {
    const ws = await buildWorkspace()
    const b = ws.bindings
    b.openScope('all')
    check('A08 首次主入口默认整条', b.scopeKind.value === 'all')
    b.scopeKind.value = 'segment'; b.segStart.value = 'nd_a'; b.segEnd.value = 'nd_e'; b.onSegChange()
    b.openScope('all')
    check('A08 主入口恢复上次范围（片段保留）', b.scopeKind.value === 'segment' && b.segStart.value === 'nd_a')
    b.openScope('single', 'nd_c')
    check('A08 快捷入口显式切节点', b.scopeKind.value === 'single' && b.singleId.value === 'nd_c')
    b.scopeKind.value = 'segment'; b.segStart.value = 'nd_a'; b.segEnd.value = 'nd_d'; b.onSegChange()
    check('A03 菱形检出多条路径', b.segPaths.value.length === 2, b.segPaths.value.length)
    const middle = b.segPaths.value[0][1]
    b.toggleExplicit(middle)
    check('A03 勾选一个后显式集合保持', b.explicitIds.value.length === 1 && b.explicitIds.value.includes(middle), 'explicitIds=' + JSON.stringify(b.explicitIds.value))
    b.toggleExplicit('nd_d')
    check('A03 继续勾选不消失', b.explicitIds.value.length === 2)
    b.explicitIds.value = ['nd_b', 'nd_d']
    check('A03 片段 B+D 不连通被拒绝（校验不放松）', b.plan.value.error.includes('不连通'))
    b.onScopeChange('all')
    check('R02b 整条计划允许并列分支（连通检查不适用于全图）', !b.plan.value.error && b.plan.value.order.length === 6, b.plan.value.error)
  }

  // ── A06/A07：入口去重与空值语义 ──────────────────────────────────────────────
  {
    const ws = await buildWorkspace()
    const b = ws.bindings
    b.openScope('all')
    check('A06 整条入口只渲染一次（1 个稳定 ID）', b.entryRows.value.length === 1 && b.entryRows.value[0].key === 'fin_x', JSON.stringify(b.entryRows.value))
    b.entryRows.value[0].type = { type: 'boolean' }
    b.run()
    check('A07 非法布尔不静默转 false（未选即报错）', b.formError.value.includes('是/否'))
    b.entryRows.value[0].type = { type: 'text' }
    b.run()
    check('A07 文本未填写明确报错（区分未提供）', b.formError.value.includes('使用空文本'))
    b.entryEmpty.value['fin_x'] = true
    ws.setRespond(() => ({ status: 'success', nodeResults: [] }))
    b.run()
    await sleep(10)
    check('A07 显式空文本作为合法值提交', ws.runCalls.at(-1)?.inputs['fin_x'] === '')
  }

  // ── A09：快照元信息（节点名/项目名来自提交时） ────────────────────────────────
  {
    const ws = await buildWorkspace({ projectName: '提交时项目' })
    const b = ws.bindings
    b.openScope('all')
    b.entryTexts.value['fin_x'] = '8'
    ws.setRespond(() => ({ status: 'success', nodeResults: [{ nodeId: 'nd_a', status: 'success', outputs: { a: 8 }, outputNames: {}, rowCount: 1 }] }))
    b.run()
    await sleep(10)
    check('A09 快照保存提交时项目名', b.result.value.snapshot.projectName === '提交时项目')
    check('A09 快照保存节点名映射', b.result.value.snapshot.nodeNames?.nd_a === 'A 整理')
    check('A09 snapName 读取快照', b.snapName('nd_a') === 'A 整理')
  }

  // ── A15：打磨轮修复（可选布尔/JSON 输入可跳过；切项目释放忙闸；空文本限文本；formError 即时清除；删节点回退范围） ──
  {
    // A15a：未绑定布尔输入不填值也能运行（不再被迫“请选择是/否”）
    const ws = await buildWorkspace()
    const b = ws.bindings
    b.__props.state.nodes[1].inputs.push({ id: 'in_opt', name: 'opt', label: '可选开关', type: { type: 'boolean' } }) // 无 source → unbound
    await sleep(5) // 声明变更的失效 watcher 先落地
    b.openScope('single', 'nd_b')
    b.overrideTexts.value['nd_b\u0000in_a'] = '5' // 上游 a 的外部输入（必填）
    await b.run()
    await sleep(10)
    check('A15a 未绑定布尔输入留空可运行', ws.runCalls.length === 1 && !b.formError.value, b.formError.value)
    // 填了值则按类型校验并提交
    b.overrideTexts.value['nd_b\u0000in_opt'] = 'true'
    await b.run()
    await sleep(10)
    const optVal = ws.runCalls[1]?.inputOverrides?.nd_b?.in_opt
    check('A15a 布尔可选项填 true 按布尔提交', optVal === true, JSON.stringify({ calls: ws.runCalls.length, ovr: ws.runCalls[1]?.inputOverrides, err: b.formError.value }))
  }
  {
    // A15b：切项目立即释放运行忙闸；孤儿回包不落新上下文
    const ws = await buildWorkspace()
    const b = ws.bindings
    b.openScope('all')
    b.entryTexts.value['fin_x'] = '8'
    let release
    ws.setRespond(() => new Promise(res => { release = () => res({ status: 'success', nodeResults: [] }) }))
    b.run()
    await sleep(5)
    check('A15b 运行中 running=true', b.running.value === true)
    b.__props.projectId = 'proj-2' // 切项目 → cacheContext watch
    await sleep(5)
    check('A15b 切项目后 running 立即释放', b.running.value === false && b.runBusy.value === false)
    release()
    await sleep(10)
    check('A15b 孤儿回包不写新上下文结果', b.result.value === null, JSON.stringify(b.result.value && b.result.value.status))
  }
  {
    // A15c：数值入口勾「使用空文本」被即时拦截，不发请求
    const ws = await buildWorkspace()
    const b = ws.bindings
    b.openScope('all')
    b.entryEmpty.value['fin_x'] = true
    await b.run()
    await sleep(5)
    check('A15c 数值空文本即时报错不发请求', ws.runCalls.length === 0 && b.formError.value.includes('不支持提交空文本'), b.formError.value)
    // A15d：错误随输入修正清除
    b.entryEmpty.value['fin_x'] = false
    b.entryTexts.value['fin_x'] = '8'
    await sleep(5)
    check('A15d 修正输入后 formError 自动清除', b.formError.value === '', b.formError.value)
  }
  {
    // A15e：被测节点被删除后范围回退整条并提示（不显示裸 ID）
    const ws = await buildWorkspace()
    const b = ws.bindings
    b.openScope('single', 'nd_c')
    check('A15e 前置：单节点范围生效', b.scopeKind.value === 'single')
    b.__props.state.nodes = b.__props.state.nodes.filter(n => n.id !== 'nd_c')
    await sleep(5)
    check('A15e 删除被测节点后回退整条', b.scopeKind.value === 'all' && b.singleId.value === '', b.scopeKind.value)
  }

  console.log(failed ? `\n${failed} 项失败` : '\n全部通过')
  process.exit(failed ? 1 : 0)
}

main()
