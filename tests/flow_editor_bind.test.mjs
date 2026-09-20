// FlowEditor 绑定语义回归（20260920 用户反馈：建立绑定不能选编排输入节点）。
// 运行（仓库根）：node --import ./tests/ts_hooks.mjs tests/flow_editor_bind.test.mjs
// 方法：@vue/compiler-sfc 提取 <script setup> 转译后注入真实 Vue 响应性 + 桩依赖，
// 直接驱动 onCanvasBind/applyLinkDialog/applyOutDialog 生命周期：
//   E01 编排输入可作来源（预选 flowInput 来源，确认后写入目标输入绑定）
//   E02 编排输出可作目标（输出绑定弹窗选声明+端口，确认写入 binding 并派生连线）
//   E03 语义守卫：输入不能作目标、输出不能作来源、输出不能作第一个点击的来源
//   E04 类型不相容不静默写入（弹窗保留 + 提示）
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const require = createRequire(new URL('../frontend/package.json', import.meta.url))
const ts = require('typescript')
const { parse } = require('@vue/compiler-sfc')

let failed = 0
function check(name, cond, detail = '') {
  console.log(`${cond ? '通过' : '失败'}：${name}${cond ? '' : ' — ' + (detail || '断言不成立')}`)
  if (!cond) failed++
}

// 合成编排：入口 x（number）→ A（number×2）→ B（text 输出，用于类型不相容用例）
const flowState = {
  flowId: 'flow-bind', name: '绑定语义编排', description: '', status: 'active',
  inputs: [{ id: 'fin_x', name: 'x', label: '初始值', type: { type: 'number' } }],
  outputs: [{ id: 'fout_1', name: 'result', label: '结果', type: { type: 'number' }, binding: null }],
  connections: [], layout: { positions: {}, zoom: 1, pan: { x: 0, y: 0 } },
  nodes: [
    { id: 'nd_a', kind: 'calc', name: 'A 放大', description: '',
      inputs: [{ id: 'in_a', name: 'a', label: '输入', type: { type: 'number' }, source: { kind: 'flowInput', inputId: 'fin_x' } }],
      outputs: [{ id: 'out_a', name: 'v', label: '输出', type: { type: 'number' } }],
      implementation: { mode: 'formula', formulas: { v: '{a} * 2' } } },
    { id: 'nd_b', kind: 'calc', name: 'B 文本', description: '',
      inputs: [{ id: 'in_b', name: 'b', label: '输入', type: { type: 'text' } }],
      outputs: [{ id: 'out_b', name: 't', label: '文本输出', type: { type: 'text' } }],
      implementation: { mode: 'formula', formulas: {} } },
  ],
}

const sfcSource = readFileSync(resolve('frontend/src/flow/FlowEditor.vue'), 'utf8')
const { descriptor } = parse(sfcSource, { filename: 'FlowEditor.vue' })
const rawSetup = descriptor.scriptSetup.content
  .replace(/^const props = defineProps<.*>\(\)$/m, 'const props = __props')
  .replace(/^const emit = defineEmits\(.*\)$/m, 'const emit = __emit')
  .replace(/^defineExpose\(/m, '__expose(')
const js = ts.transpileModule(rawSetup, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
const body = js.replace(/^import\s+[\s\S]*?from\s*['"][^'"]+['"];?\s*$/gm, '')
const bindingsReturn = `return { onCanvasBind, applyLinkDialog, applyOutDialog, recomputeOutIssue, linkDialog, outDialog, linkIssue, bindSource, mode, notice, state: props.state }`
const vue = require('vue')
const flowModel = await import('../frontend/src/flow/flowModel.ts')
// Node 环境桩：编辑器脚本读取 window.innerWidth / document（仅用于布局与快捷键注册）
globalThis.window = globalThis.window || { innerWidth: 1440, addEventListener() {}, removeEventListener() {} }
globalThis.document = globalThis.document || {
  addEventListener() {}, removeEventListener() {},
  querySelector: () => null, querySelectorAll: () => [],
  activeElement: null,
}

async function buildEditor() {
  const props = vue.reactive({
    state: JSON.parse(JSON.stringify(flowState)),
    projectConnections: [], projectId: 'p1', revision: 'r1', projectName: '验收项目', projects: [],
    saveCheck: null, saveCheckSig: '', restoreTab: '', restoreNode: null, providersRefresh: 0,
  })
  const emitCalls = []
  const emit = (name, payload) => emitCalls.push([name, payload])
  const stub = () => ({})
  const factory = new Function(
    '__props', '__emit', 'vue', 'fm', 'appConfirm', 'AppSelect', 'FlowCanvas', 'NodeConfig',
    'BoundaryConfig', 'BindingEditor', 'FlowTestWorkspace', 'checkFlow', 'llm', 'getJson', 'prefGet', 'prefSet',
    'const { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } = vue;\n' +
    'const { INPUT_NODE, NODE_KIND_LABELS, OUTPUT_NODE, SECTION_LABELS, TYPE_LABELS, clone, configSignature, defaultPosition, fieldOf, nodeRemovalImpact, processingNodes, typeSummary, typesCompatible, uid } = fm;\n' +
    'return (async () => {\n' + body + '\n' + bindingsReturn + '\n})()')
  const bindings = await factory(props, emit, vue, flowModel, async () => true,
    stub, stub, stub, stub, stub, stub, async () => ({ errors: [], warnings: [], items: [], diagnostics: [] }),
    { listProviders: async () => ({ items: [] }) }, async () => ({}), () => '', () => {})
  bindings.__props = props
  bindings.__emitCalls = emitCalls
  return bindings
}
const sleep = ms => new Promise(r => setTimeout(r, ms))

async function main() {
  // ── E01：编排输入作为来源 ─────────────────────────────────────────────────
  {
    const b = await buildEditor()
    b.mode.value = 'bind'
    b.onCanvasBind('flow-input')      // 点「编排输入」→ 成为来源
    check('E01 编排输入可被选为来源', b.bindSource.value === 'flow-input', b.bindSource.value)
    b.onCanvasBind('nd_a')              // 再点目标处理节点（number 输入）→ 弹绑定窗，来源预选入口参数
    await sleep(5)
    check('E01 打开绑定弹窗', !!b.linkDialog.value)
    check('E01 来源预选为编排入口参数', b.linkDialog.value?.shadow?.source?.kind === 'flowInput'
      && b.linkDialog.value?.shadow?.source?.inputId === 'fin_x', JSON.stringify(b.linkDialog.value?.shadow))
    check('E01 标记 fromInput 提示', b.linkDialog.value?.fromInput === true)
    b.applyLinkDialog()
    await sleep(5)
    const real = b.__props.state.nodes.find(n => n.id === 'nd_a').inputs[0]
    check('E01 确认后写入目标输入绑定', real.source?.kind === 'flowInput' && real.source.inputId === 'fin_x', JSON.stringify(real.source))
    check('E01 关闭弹窗并派发 changed', !b.linkDialog.value && b.__emitCalls.some(c => c[0] === 'changed'))
  }

  // ── E02：编排输出作为目标 ─────────────────────────────────────────────────
  {
    const b = await buildEditor()
    b.mode.value = 'bind'
    b.onCanvasBind('nd_a')              // 来源=处理节点
    b.onCanvasBind('flow-output')     // 目标=编排输出 → 打开输出绑定弹窗
    await sleep(5)
    check('E02 输出绑定弹窗打开且预选端口', !!b.outDialog.value && b.outDialog.value.chosenPortId === 'out_a', JSON.stringify(b.outDialog.value))
    b.recomputeOutIssue()
    check('E02 number→number 无类型问题', b.outDialog.value.issue === '', b.outDialog.value.issue)
    b.applyOutDialog()
    await sleep(5)
    const decl = b.__props.state.outputs[0]
    check('E02 确认后写入编排输出 binding', decl.binding?.kind === 'node' && decl.binding.nodeId === 'nd_a' && decl.binding.outputId === 'out_a', JSON.stringify(decl.binding))
    check('E02 关闭弹窗', !b.outDialog.value)
  }

  // ── E03：语义守卫 ─────────────────────────────────────────────────────────
  {
    const b = await buildEditor()
    b.mode.value = 'bind'
    b.onCanvasBind('flow-output')     // 第一下点输出：只提示不做来源
    check('E03 编排输出不能作为来源', b.bindSource.value === '' && b.notice.value.includes('只能作为目标'), b.notice.value)
    b.onCanvasBind('nd_a')
    b.onCanvasBind('flow-input')      // 输出→输入：非法
    check('E03 编排输入不能作为目标', b.notice.value.includes('只能作为来源') && !b.linkDialog.value, b.notice.value)
    b.onCanvasBind('flow-output')     // 输入→输出：非法
    check('E03 编排输出不能作为来源（第二下）', b.notice.value.includes('只能作为目标'), b.notice.value)
  }

  // ── E04：类型不相容不静默写入 ─────────────────────────────────────────────
  {
    const b = await buildEditor()
    b.mode.value = 'bind'
    b.onCanvasBind('flow-input')
    b.onCanvasBind('nd_b')              // B 是 text 输入，入口 x 是 number → 不相容
    await sleep(5)
    check('E04 number 入口×text 目标提示不相容', b.linkIssue.value.includes('类型不相容'), b.linkIssue.value || '(空)')
    b.applyLinkDialog()
    await sleep(5)
    const real = b.__props.state.nodes.find(n => n.id === 'nd_b').inputs[0]
    check('E04 不相容不写入（保持原状）', !real.source, JSON.stringify(real.source))
    check('E04 弹窗保留让用户修正', !!b.linkDialog.value)
    // 弹窗内改来源为文本固定值 → 相容
    b.linkDialog.value.shadow.source = { kind: 'fixed', valueType: 'text', value: 'x' }
    await sleep(5)
    check('E04 相容来源问题清空', b.linkIssue.value === '', b.linkIssue.value)
    b.applyLinkDialog()
    await sleep(5)
    check('E04 修正后可写入', b.__props.state.nodes.find(n => n.id === 'nd_b').inputs[0].source?.kind === 'fixed')
  }

  console.log(failed ? `\n${failed} 项失败` : '\n全部通过')
  process.exit(failed ? 1 : 0)
}

main()
