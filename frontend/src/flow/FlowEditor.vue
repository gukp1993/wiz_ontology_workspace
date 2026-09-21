<!-- FlowEditor — 函数编排编辑页（20260919 配置与调试优化版）：设计 / 测试双视图。
     · 设计视图：头部（返回列表/名称/显式运行项目/检查配置/▷测试编排——唯一测试入口，
     //   片段与单节点范围在测试视图下拉选择）→
       工具栏（节点列表/添加节点/建立绑定/整理/全图/100%）→ 可收起节点列表 / 画布 / 节点配置
       （连续区块：名称与说明/输入/实现/输出/高级）→ 配置问题 dock。
     · 测试视图（FlowTestWorkspace，保持挂载 v-show）：独立左右布局，整条/单节点/连续片段；
       返回设计保留画布视口、选中节点与测试输入/结果（F05）。
     · 保存与撤销重做沿用 App 的 flow Saver；本页 emit before-change/changed；
       测试不触发保存（工作区组件只读）。 -->
<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { prefGet, prefSet } from '../app/auth'
import { appConfirm } from '../shared/appConfirm'
import { FLOW_CATEGORY } from '../shared/graphStyle'
import AppSelect from '../shared/AppSelect.vue'
import FlowCanvas from './FlowCanvas.vue'
import NodeConfig from './NodeConfig.vue'
import BoundaryConfig from './BoundaryConfig.vue'
import BindingEditor from './BindingEditor.vue'
import FlowTestWorkspace from './FlowTestWorkspace.vue'
import { INPUT_NODE, NODE_KIND_LABELS, OUTPUT_NODE, SECTION_LABELS, TYPE_LABELS, clone, configSignature, defaultPosition, fieldOf, nodeRemovalImpact, processingNodes, typeSummary, typesCompatible, uid } from './flowModel'
import { checkFlow } from './api'
import * as llm from '../tools/llm'
import { getJson } from '../app/http'

const props = defineProps<{ state: any; projectConnections?: any[]; projectId?: string; revision?: string; projectName?: string; projects?: any[]; saveCheck?: any; saveCheckSig?: string; restoreTab?: string; restoreNode?: { id: string; token: number } | null; providersRefresh?: number }>()
const emit = defineEmits(['before-change', 'changed', 'navigate', 'back', 'switch-project'])
// 具名撤销（20260918）：emit('before-change', { actionLabel, target?, mergeKey? })
const canvasRef = ref<any>(null)
const testRef = ref<any>(null)
// 设计/测试视图切换（20260919）：测试视图保持挂载，输入/范围/结果跨往返保留（F05）
const viewMode = ref<'design' | 'test'>('design')
// 页头运行项目下拉（原型 3.2）：选项来自项目清单；切换经 App 既有 loadProject（含保存/离开保护）
const projectOptions = computed(() => (props.projects || []).map((p: any) => ({ value: p.id, label: p.name || p.id })))
// 编排改名（原型 ✎ → 模态；取消不改动）
const renameOpen = ref(false)
const renameBuffer = ref('')
function openRename() { renameBuffer.value = String(props.state.name || ''); dialogDirty.value = false; openDialog(); renameOpen.value = true }
function applyRename() {
  const next = renameBuffer.value.trim()
  if (!next) return
  emit('before-change', { actionLabel: '重命名编排「' + next + '」' })
  props.state.name = next
  emit('changed')
  renameOpen.value = false
  closeDialog(true)
}
function openTestView(kind: 'all' | 'single' | 'segment', nodeId?: string) {
  viewMode.value = 'test'
  testRef.value?.openScope(kind, nodeId)
}
const notice = ref('')
// 从模型设置返回：恢复页签与节点选中，刷新可用模型列表（保留原 providerId，不自动重绑/执行）
watch(() => props.restoreTab, t => { viewMode.value = 'design'; if (t) inspectorTab.value = t })
watch(() => props.restoreNode, n => { viewMode.value = 'design'; if (n?.id) { selectedId.value = n.id; inspectorTab.value = inspectorTab.value || 'implementation'; nextTick(() => canvasRef.value?.focusNode(n.id)) } })
watch(() => props.providersRefresh, () => { void loadProviders() })
let noticeTimer: any = null
function warn(text: string) { notice.value = text; clearTimeout(noticeTimer); noticeTimer = setTimeout(() => notice.value = '', 5000) }

// ── 选择 / 模式（绑定模式与查看模式互斥；测试范围在独立测试视图中，不冒充编辑选中） ──
const selectedId = ref('')
const inspectorTab = ref('implementation')
const mode = ref<'inspect' | 'bind'>('inspect')
const bindSource = ref('')
const listOpen = ref(window.innerWidth >= 1440)
const node = computed(() => (props.state.nodes || []).find((n: any) => n.id === selectedId.value))
const selectedKind = computed(() => {
  if (selectedId.value === INPUT_NODE) return 'input'
  if (selectedId.value === OUTPUT_NODE) return 'output'
  return node.value ? node.value.kind : ''
})
const nodeList = computed(() => [
  { id: INPUT_NODE, name: '编排输入', kindLabel: '边界', fixed: true },
  ...processingNodes(props.state).map((n: any) => ({ id: n.id, name: n.name || '未命名节点', kindLabel: NODE_KIND_LABELS[n.kind] || n.kind, fixed: false })),
  { id: OUTPUT_NODE, name: '编排输出', kindLabel: '边界', fixed: true },
])
function select(id: string) {
  selectedId.value = id
  if (id === INPUT_NODE) inspectorTab.value = 'inputs'
  else if (id === OUTPUT_NODE) inspectorTab.value = 'outputs'
  else if (id) inspectorTab.value = 'implementation'
  if (id && id !== INPUT_NODE && id !== OUTPUT_NODE) canvasRef.value?.focusNode(id)
}
function setMode(next: 'inspect' | 'bind') {
  mode.value = mode.value === next ? 'inspect' : next
  bindSource.value = ''
}
watch(() => props.state, () => {
  if (selectedId.value && !nodeList.value.some((n: any) => n.id === selectedId.value)) selectedId.value = ''
})
const modebarText = computed(() => {
  if (mode.value === 'bind') return bindSource.value
    ? `建立绑定 · 来源已选：「${nameOf(bindSource.value)}」，请点击目标节点（处理节点，或「编排输出」）`
    : '建立绑定 · 先点来源（处理节点或「编排输入」），再点目标（处理节点或「编排输出」）'
  return '查看模式 · 点击节点查看配置；测试在「测试调试」视图中进行，不占用画布勾选'
})

// ── LLM 提供方与项目凭据（状态区分：加载中/失败/未配置/就绪） ────────────────────
const providers = ref<any[]>([])
const providersStatus = ref<'loading' | 'ready' | 'failed'>('loading')
const credentials = ref<any[]>([])
const connectionsStatus = computed<'loading' | 'ready' | 'failed'>(() => props.projectConnections ? 'ready' : 'loading')
async function loadProviders() {
  providersStatus.value = 'loading'
  try { providers.value = (await llm.listProviders()).items || []; providersStatus.value = 'ready' }
  catch { providersStatus.value = 'failed' }
}
watch(() => props.projectId, async (pid: string) => {
  credentials.value = []
  if (!pid) return
  try {
    const d = await getJson('/api/api-credentials?project=' + encodeURIComponent(pid))
    credentials.value = d.items || []
  } catch { /* 凭据列表不可读：HTTP 节点认证下拉为空，校验由服务端负责 */ }
}, { immediate: true })
onMounted(loadProviders)

function addNode(kind: string) {
  emit('before-change')
  const fresh = { id: uid(), kind, name: `${NODE_KIND_LABELS[kind] || kind}节点`, description: '', inputs: [], outputs: [],
    implementation: kind === 'python' ? { language: 'python', code: 'def main():\n    return None\n' }
      : kind === 'sql' ? { language: 'sql', sql: '', connectionId: '' }
      : kind === 'redis' ? { language: 'redis', connectionId: '', keyTemplate: '', command: '', args: [] }
      : kind === 'http' ? { language: 'http', method: 'GET', url: '', headers: {}, bodyMode: 'none', body: '', credentialId: '', responsePath: '' }
      : { language: 'calc', mode: 'formula', formulas: {}, llmInstruction: '', providerId: '' } }
  props.state.nodes.push(fresh)
  props.state.layout = props.state.layout || { positions: {}, zoom: 1, pan: { x: 0, y: 0 } }
  props.state.layout.positions = props.state.layout.positions || {}
  props.state.layout.positions[fresh.id] = defaultPosition(props.state)
  emit('changed')
  addMenu.value = false
  select(fresh.id)
  nextTick(() => canvasRef.value?.sync())
}
async function deleteSelected() {
  const id = selectedId.value
  if (!id || id === INPUT_NODE || id === OUTPUT_NODE) return
  const target = props.state.nodes.find((n: any) => n.id === id)
  const impact = nodeRemovalImpact(props.state, id)
  const message = impact.length
    ? `删除节点「${target?.name || id}」？\n受影响的引用：\n${impact.join('\n')}\n\n删除后这些绑定将变为未绑定（不会静默改绑到其他来源）。`
    : `删除节点「${target?.name || id}」？`
  if (!(await appConfirm({ message, danger: true, confirmLabel: '删除' }))) return
  emit('before-change')
  for (const other of props.state.nodes) for (const input of other.inputs || []) {
    const src = input.source
    if (src && (src.kind === 'node' || src.kind === 'nodeField') && src.nodeId === id) input.source = null
  }
  for (const out of props.state.outputs) {
    const b = out.binding
    if (b && (b.kind === 'node' || b.kind === 'nodeField') && b.nodeId === id) out.binding = null
  }
  props.state.nodes = props.state.nodes.filter((n: any) => n.id !== id)
  if (props.state.layout?.positions) delete props.state.layout.positions[id]
  emit('changed')
  select('')
  nextTick(() => canvasRef.value?.sync())
}

// ── 检查与新鲜度（快照签名；晚到旧回包不覆盖；保存回包同规则） ────────────────────
const currentSig = computed(() => configSignature(props.state))
const checkSnap = ref<{ check: any; sig: string } | null>(null)
const checking = ref(false)
const checkStale = computed(() => !!checkSnap.value && checkSnap.value.sig !== currentSig.value)
const checkStateText = computed(() => checking.value ? '检查中…'
  : !checkSnap.value ? '尚未检查'
  : checkStale.value ? '待重新检查'
  : checkSnap.value.check.errors?.length ? `${checkSnap.value.check.errors.length} 个问题`
  : checkSnap.value.check.warnings?.length ? `${checkSnap.value.check.warnings.length} 项待完善` : '检查通过')
const checkStateClass = computed(() => checking.value || !checkSnap.value ? '' : checkStale.value ? 'amber' : checkSnap.value.check.errors?.length ? 'error' : 'good')
async function runCheck() {
  if (checking.value) return
  const sig = currentSig.value
  checking.value = true
  try {
    const d = await checkFlow(clone(props.state), props.projectConnections || [])
    checkSnap.value = { check: d, sig }
    dockOpen.value = true
    if (currentSig.value !== sig) warn('配置在检查期间已修改：结果显示为上次检查，请重新检查确认。')
  } catch (e: any) { warn('配置检查请求失败：' + (e?.message || e)) } finally { checking.value = false }
}
watch(() => [props.saveCheck, props.saveCheckSig], () => {
  if (!props.saveCheck || !props.saveCheckSig) return
  if (props.saveCheckSig === currentSig.value) checkSnap.value = { check: props.saveCheck, sig: props.saveCheckSig }
  // 签名不匹配 = 保存响应对应旧配置：不采纳，避免旧结论冒充当前通过
}, { immediate: true })
const issueDiags = computed(() => (checkSnap.value?.check?.diagnostics || []) as any[])
const issueCounts = computed(() => {
  if (!checkSnap.value || checkStale.value) return {}
  const counts: Record<string, number> = {}
  for (const d of issueDiags.value) counts[d.id] = (counts[d.id] || 0) + 1
  return counts
})
function nameOf(id: string): string {
  if (id === INPUT_NODE) return '编排输入'
  if (id === OUTPUT_NODE) return '编排输出'
  return (props.state.nodes.find((n: any) => n.id === id) || {}).name || id
}
function fieldLabel(d: any): string {
  const section = d.section ? SECTION_LABELS[d.section] || d.section : ''
  const fieldNames: Record<string, string> = { binding: '来源绑定', name: '技术名', type: '类型', sql: 'SQL 模板', code: '代码', keyTemplate: 'Key 模板', command: '命令', args: '命令参数', url: 'URL', body: '请求体', providerId: 'LLM 提供方', connectionId: '数据连接', responsePath: '提取路径', llmInstruction: '计算规则', formulas: '公式', timeoutMs: '超时', maxRows: '行数上限', allowWrite: '允许写', execution: '执行参数', method: '方法', bodyMode: '请求体类型', headers: '请求头', credentialId: '认证凭据', mode: '计算模式', id: '稳定 ID' }
  const field = d.field ? (fieldNames[d.field] || d.field) : ''
  return field ? `${section} · ${field}` : section
}
function locate(d: any) {
  if (d.kind === 'node') select(d.id)
  else if (d.kind === 'flow-input') select(INPUT_NODE)
  else if (d.kind === 'flow-output') select(OUTPUT_NODE)
  inspectorTab.value = d.section && (d.kind !== 'flow-input' && d.kind !== 'flow-output') ? d.section : (d.kind === 'flow-input' ? 'inputs' : d.kind === 'flow-output' ? 'outputs' : inspectorTab.value)
  focusRequest.value = { token: ++focusSeq, parameterId: d.parameterId, field: d.field }
  if (selectedId.value === d.id || (d.kind !== 'node' && selectedId.value)) canvasRef.value?.focusNode(d.kind === 'node' ? d.id : (d.kind === 'flow-input' ? INPUT_NODE : OUTPUT_NODE))
}
const focusRequest = ref<{ token: number; parameterId?: string; field?: string }>({ token: 0 })
let focusSeq = 0

// ── 弹窗基建（展开编辑/绑定对话框共用；离开确认与 Tab 约束） ─────────────────────
const dialogDirty = ref(false)
let lastFocused: HTMLElement | null = null
function openDialog() {
  lastFocused = document.activeElement as HTMLElement
  dialogDirty.value = false
  void nextTick(() => {
    const modal = document.querySelector('.flow-modal')
    const first = modal?.querySelector<HTMLElement>('input:not([disabled]), textarea, select') || modal?.querySelector<HTMLElement>('button')
    first?.focus()
  })
}
async function closeDialog(force = false, onClosed?: () => void) {
  if (dialogDirty.value && !force) {
    if (!(await appConfirm({ message: '弹窗中有未提交的修改，放弃并关闭？', danger: true, confirmLabel: '放弃关闭' }))) return
  }
  expandDialog.value = null
  renameOpen.value = false
  outDialog.value = null
  dialogDirty.value = false
  onClosed?.()
  lastFocused?.focus?.()
  lastFocused = null
}
function anyModalOpen() { return !!expandDialog.value || renameOpen.value || !!outDialog.value }
const dockOpen = ref(false)

// ── 绑定（唯一 binding 真相；取消零改动；删除有确认） ────────────────────────────
// 边界语义：编排输入（INPUT_NODE）可作来源 —— 目标输入预选「编排入口参数」；
// 编排输出（OUTPUT_NODE）可作目标 —— 弹「绑定到编排输出」选输出声明与来源输出端口。
const linkDialog = ref<{ sourceId: string; targetId: string; inputs: any[]; chosenInputId: string; shadow: any; fromInput?: boolean } | null>(null)
const outDialog = ref<{ sourceId: string; chosenOutputId: string; chosenPortId: string; issue: string } | null>(null)
function onCanvasBind(nodeId: string) {
  if (!bindSource.value) {
    if (nodeId === OUTPUT_NODE && !(props.state.nodes || []).length) return warn('编排还没有处理节点：先「＋ 添加节点」')
    if (nodeId === OUTPUT_NODE) return warn('「编排输出」只能作为目标：请先点击来源节点，再点击「编排输出」')
    bindSource.value = nodeId
    return
  }
  if (bindSource.value === nodeId) { bindSource.value = ''; return }
  const sourceId = bindSource.value
  bindSource.value = ''
  if (sourceId === OUTPUT_NODE) return warn('「编排输出」只能作为目标节点，不能作为来源')
  if (nodeId === INPUT_NODE) return warn('「编排输入」只能作为来源节点，不能作为目标')
  if (nodeId === OUTPUT_NODE) return openOutputBind(sourceId)
  const target = props.state.nodes.find((n: any) => n.id === nodeId)
  // 来源是「编排输入」边界节点时不要求出现在处理节点列表里
  const source = sourceId === INPUT_NODE ? true : props.state.nodes.find((n: any) => n.id === sourceId)
  if (!target || !source) return
  if (!target.inputs.length) return warn(`「${target.name}」尚未声明输入参数，请先在详情「输入」页签添加`)
  const firstUnbound = target.inputs.find((i: any) => !i.source)
  const chosen = (firstUnbound || target.inputs[0]).id
  let shadow = null
  if (sourceId === INPUT_NODE) {
    const entries = (props.state.inputs || []).filter((i: any) => i && typeof i === 'object')
    if (!entries.length) return warn('编排尚未声明入口参数：请先点击画布上的「编排输入」节点添加')
    shadow = { ...target.inputs.find((i: any) => i.id === chosen), source: { kind: 'flowInput', inputId: entries[0].id } }
  }
  openDialog()
  linkDialog.value = { sourceId, targetId: nodeId, inputs: target.inputs, chosenInputId: chosen, shadow, fromInput: sourceId === INPUT_NODE }
}
function openOutputBind(sourceId: string) {
  const source = props.state.nodes.find((n: any) => n.id === sourceId)
  if (!source) return
  if (!(source.outputs || []).length) return warn(`「${source.name}」尚未声明输出参数，请先在详情「输出」页签添加`)
  const decls = (props.state.outputs || []).filter((o: any) => o && typeof o === 'object')
  if (!decls.length) return warn('编排尚未声明输出：请先点击画布上的「编排输出」节点添加输出参数')
  const usedPorts = new Set(decls.filter((o: any) => o.binding && o.binding.kind === 'node').map((o: any) => `${o.binding.nodeId}/${o.binding.outputId}`))
  const free = (source.outputs || []).find((p: any) => !usedPorts.has(`${source.id}/${p.id}`)) || source.outputs[0]
  openDialog()
  outDialog.value = { sourceId, chosenOutputId: decls[0].id, chosenPortId: free.id, issue: '' }
}
const _outDialogType = computed(() => {
  if (!outDialog.value) return ''
  const poll = props.state.inputs // 仅触发响应性重算（state 变化后重算选项）
  void poll
  const decl = (props.state.outputs || []).find((o: any) => o.id === outDialog.value!.chosenOutputId)
  return decl ? typeSummary(decl.type) : ''
})
function outPreview() {
  if (!outDialog.value) return null
  const node = props.state.nodes.find((n: any) => n.id === outDialog.value!.sourceId)
  const port = (node?.outputs || []).find((p: any) => p.id === outDialog.value!.chosenPortId)
  if (!node || !port) return { node: null, port: null, decl: null }
  const decl = (props.state.outputs || []).find((o: any) => o.id === outDialog.value!.chosenOutputId)
  return { node, port, decl }
}
function recomputeOutIssue() {
  if (!outDialog.value) return
  const { port, decl } = outPreview() || {}
  if (!port || !decl) { outDialog.value.issue = '输出声明或来源输出无效'; return }
  const verdict = typesCompatible(decl.type, port.type)
  outDialog.value.issue = verdict.ok ? '' : `类型不相容：${verdict.reason}`
}
function applyOutDialog() {
  if (!outDialog.value) return closeDialog()
  recomputeOutIssue()
  if (outDialog.value.issue) return
  const { node, port, decl } = outPreview() || {}
  if (!node || !port || !decl) return closeDialog()
  emit('before-change')
  decl.binding = { kind: 'node', nodeId: node.id, outputId: port.id }
  emit('changed')
  nextTick(() => canvasRef.value?.sync())
  closeDialog()
}
// 弹窗阴影初始化/重置（弹窗打开或切换所选输入时）：从「编排输入」发起则目标输入
// 预置为该入口参数的引用（用户仍可在弹窗内改来源）。抽到 watcher 保证 computed 无副作用。
watch(() => linkDialog.value && linkDialog.value.chosenInputId, () => {
  const dialog = linkDialog.value
  if (!dialog) return
  const input = dialog.inputs.find((i: any) => i.id === dialog.chosenInputId)
  if (!input) return
  if (!dialog.shadow || dialog.shadow.id !== input.id) {
    const preset = dialog.fromInput
      ? { kind: 'flowInput', inputId: (props.state.inputs || []).find((i: any) => i && typeof i === 'object')?.id || '' }
      : null
    dialog.shadow = { ...input, source: preset && preset.inputId ? preset : clone(input.source) }
  }
})
const linkShadowOwner = computed(() => {
  if (!linkDialog.value) return null
  const input = linkDialog.value.inputs.find((i: any) => i.id === linkDialog.value!.chosenInputId)
  if (!input) return null
  if (!linkDialog.value.shadow || linkDialog.value.shadow.id !== input.id) return null  // 影子尚未同步（watcher 同帧内补齐）
  return { nodeId: linkDialog.value.targetId, input: linkDialog.value.shadow }
})
/** 弹窗内即时可读问题（不阻断选择）：来源/目标类型不相容、来源为空等，确认时以它为准。 */
const linkIssue = computed(() => {
  if (!linkDialog.value) return ''
  const input = linkDialog.value.inputs.find((i: any) => i.id === linkDialog.value!.chosenInputId)
  const shadow = linkDialog.value.shadow
  if (!input || !shadow) return ''
  const src = shadow.source
  if (!src) return '尚未选择来源：确认不会写入任何绑定'
  if (src.kind === 'node' || src.kind === 'nodeField') {
    const node = props.state.nodes.find((n: any) => n.id === src.nodeId)
    if (!node) return '来源节点不存在'
    const port = (node.outputs || []).find((p: any) => p.id === src.outputId)
    if (!port) return '请选择来源节点的输出端口'
    const declared = src.kind === 'nodeField' && Array.isArray(src.fieldPath) ? fieldOf(port.type, src.fieldPath) : port.type
    if (!declared) return '字段引用路径无效，请重新选择'
    const verdict = typesCompatible(input.type, declared)
    return verdict.ok ? '' : `类型不相容：${verdict.reason}`
  }
  if (src.kind === 'flowInput') {
    const decl = (props.state.inputs || []).find((i: any) => i.id === src.inputId)
    if (!decl) return '入口参数不存在（可能已被删除）'
    const verdict = typesCompatible(input.type, decl.type)
    return verdict.ok ? '' : `类型不相容：${verdict.reason}`
  }
  if (src.kind === 'fixed') {
    if (src.valueType && src.valueType !== input.type?.type) return `固定值类型（${TYPE_LABELS[src.valueType] || src.valueType}）与目标输入类型不一致`
    return ''
  }
  return ''
})
function applyLinkDialog() {
  if (!linkDialog.value || !linkShadowOwner.value) return closeLinkDialog()
  const shadow = linkDialog.value.shadow
  const src = shadow.source
  if (!src) { warn('尚未选择来源，未写入绑定'); return closeLinkDialog() }
  if ((src.kind === 'node' || src.kind === 'nodeField') && !src.outputId) { warn('请选择来源节点的输出端口'); return closeLinkDialog() }
  if (linkIssue.value) { warn(linkIssue.value); return } // 类型不相容等：不静默写入，保留弹窗让用户改
  const real = linkDialog.value.inputs.find((i: any) => i.id === shadow.id)
  if (!real) return closeLinkDialog()
  emit('before-change')
  real.source = shadow.source
  emit('changed')
  nextTick(() => canvasRef.value?.sync())
  closeLinkDialog()
}
function closeLinkDialog() { linkDialog.value = null; lastFocused?.focus?.(); lastFocused = null }
async function handleUnlink(edgeId: string) {
  if (edgeId.startsWith('dep:')) {
    const targetNodeId = edgeId.slice(4).split(':')[0]
    const target = props.state.nodes.find((n: any) => n.id === targetNodeId)
    const input = target?.inputs?.find((i: any) => i.id === edgeId.slice(4).split(':')[1])
    if (!input) return
    const siblings = (target.inputs || []).filter((i: any) => i.source?.nodeId === input.source?.nodeId && i.source?.kind !== 'fixed' && i.source?.kind !== 'flowInput')
    const context = siblings.length > 1 ? `\n（这两个节点之间共 ${siblings.length} 条绑定，本次只删除下面这一条）` : ''
    if (!(await appConfirm({ message: `删除这条绑定？\n节点「${target.name}」的输入「${input.label || input.name}」← ${input.source ? '来源' : ''}${context}`, danger: true, confirmLabel: '删除绑定' }))) return
    emit('before-change')
    input.source = null
    emit('changed')
    nextTick(() => canvasRef.value?.sync())
  } else if (edgeId.startsWith('out:')) {
    const out = props.state.outputs.find((o: any) => o.id === edgeId.slice(4))
    if (!out || !out.binding) return
    if (!(await appConfirm({ message: `删除编排输出「${out.label || out.name}」的来源绑定？`, danger: true, confirmLabel: '删除绑定' }))) return
    emit('before-change')
    out.binding = null
    emit('changed')
    nextTick(() => canvasRef.value?.sync())
  }
}

// ── 展开编辑代码（共享同一缓冲，应用后落盘；Esc/取消不改动） ───────────────────────
const expandDialog = ref<{ field: string; title: string; nodeName: string; buffer: string } | null>(null)
watch(() => props.state?.nodes, () => {
  if (expandDialog.value) {
    const node = props.state.nodes.find((n: any) => n.id === selectedId.value)
    const impl = node?.implementation || {}
    const f = expandDialog.value.field
    if (f.startsWith('formula:')) { /* 公式缓冲保持 */ } else expandDialog.value.buffer = impl[f] || ''
  }
})
function onExpandCode(payload: { field: string; title: string; value: string }) {
  openDialog()
  expandDialog.value = { field: payload.field, title: payload.title, nodeName: nameOf(selectedId.value), buffer: payload.value }
  dialogDirty.value = false
}
function applyExpand() {
  const dialog = expandDialog.value
  if (!dialog) return
  emit('before-change')
  const node = props.state.nodes.find((n: any) => n.id === selectedId.value)
  if (node) {
    if (dialog.field.startsWith('formula:')) {
      const name = dialog.field.slice('formula:'.length)
      const formulas = node.implementation.formulas || (node.implementation.formulas = {})
      formulas[name] = dialog.buffer
    } else node.implementation[dialog.field] = dialog.buffer
  }
  emit('changed')
  closeDialog(true)
}

// ── 详情宽度拖拽（360–560，记忆到 localStorage；窄屏覆盖面板不拖拽） ────────────────
const inspectorWidth = ref(Number(prefGet('wiz-flow-detail-w')) || 480)
let resizing = false
function startResize(e: MouseEvent) {
  if (window.innerWidth < 1100) return
  resizing = true
  const move = (ev: MouseEvent) => {
    if (!resizing) return
    const width = Math.min(560, Math.max(360, window.innerWidth - ev.clientX))
    inspectorWidth.value = width
  }
  const up = () => {
    resizing = false
    prefSet('wiz-flow-detail-w', String(inspectorWidth.value))
    document.removeEventListener('mousemove', move); document.removeEventListener('mouseup', up)
  }
  document.addEventListener('mousemove', move); document.addEventListener('mouseup', up)
  e.preventDefault()
}

// ── 键盘：Esc 优先级（弹窗 → 菜单 → 模式），Tab 焦点约束在弹窗内 ────────────────────
const addMenu = ref(false)
function onKeydown(e: KeyboardEvent) {
  if (e.key !== 'Escape' && e.key !== 'Tab') return
  if (e.key === 'Tab' && anyModalOpen()) {
    const modal = document.querySelector('.flow-modal')
    if (!modal) return
    const focusables = [...modal.querySelectorAll<HTMLElement>('button,input,textarea,select,[tabindex]')].filter(x => !(x as HTMLButtonElement).disabled && x.offsetParent !== null)
    if (!focusables.length) return
    const i = focusables.indexOf(document.activeElement as HTMLElement)
    if (e.shiftKey && i <= 0) { e.preventDefault(); focusables[focusables.length - 1].focus() }
    else if (!e.shiftKey && (i === focusables.length - 1 || i < 0)) { e.preventDefault(); focusables[0].focus() }
    return
  }
  if (e.key !== 'Escape') return
  if (anyModalOpen()) { e.preventDefault(); void closeDialog(); return }
  if (addMenu.value) { addMenu.value = false; return }
  if (mode.value !== 'inspect') { e.preventDefault(); setMode(mode.value) }
}
onMounted(() => document.addEventListener('keydown', onKeydown))
onBeforeUnmount(() => document.removeEventListener('keydown', onKeydown))
</script>
<template>
<div class="flow-page">
  <!-- 测试视图：独立主工作区，保持挂载（输入/范围/结果跨设计↔测试往返保留，F05） -->
  <FlowTestWorkspace v-show="viewMode==='test'" ref="testRef" class="ftw-host" :state="state" :project-id="projectId" :project-name="projectName" :revision="revision" @back="viewMode='design'"/>
  <div v-show="viewMode==='design'" class="design-root">
  <header class="flow-head">
    <div class="head-row">
      <button class="quiet" @click="emit('back')">← 编排列表</button>
      <h1 class="flow-title">{{ state.name || '未命名编排' }}<button class="quiet rename-btn" title="修改编排名称" aria-label="修改编排名称" @click="openRename">✎</button></h1>
      <span class="push"></span>
      <span class="context" title="连接与凭据按此项目上下文解析；切换走既有项目加载（保存/离开保护），不静默重绑">运行项目
        <AppSelect :model-value="projectId || ''" :options="projectOptions" :disabled="!projectOptions.length" placeholder="未选择项目" aria-label="运行项目" @update:model-value="id => emit('switch-project', id)"/>
      </span>
      <button :disabled="checking" @click="runCheck">{{checking?'检查中…':'检查配置'}}</button>
      <button class="primary" :disabled="checking" @click="openTestView('all')">▷ 测试编排</button>
    </div>
    <div class="head-sub">
      <input class="desc-inline" :value="state.description" aria-label="编排说明" placeholder="这个编排做什么（选填）" @input="emit('before-change');state.description=($event.target as HTMLInputElement).value;emit('changed')"/>
    </div>
  </header>
  <div class="flow-toolbar">
    <button :class="{active: listOpen}" :aria-expanded="listOpen" @click="listOpen=!listOpen">节点列表</button>
    <div class="menuwrap">
      <button :class="{active: addMenu}" :aria-expanded="addMenu" @click="addMenu=!addMenu">＋ 添加节点</button>
      <div v-if="addMenu" class="menu" role="menu">
        <button v-for="(label,kindKey) in { sql: 'SQL 查询', python: 'Python（LLM 推演）', calc: '计算公式', redis: 'Redis 取数', http: 'HTTP 请求' }" :key="kindKey" role="menuitem" @click="addNode(kindKey)">{{label}}</button>
      </div>
    </div>
    <button :class="{active: mode==='bind'}" @click="setMode('bind')">建立绑定</button>
    <span class="push"></span>
    <button class="quiet check-tag" :class="checkStateClass" :title="'打开问题面板'" @click="dockOpen=true">{{checkStateText}}</button>
    <button @click="canvasRef?.autoLayout()">整理</button>
    <button @click="canvasRef?.fitAll()">全图</button>
    <button @click="canvasRef?.zoom100()">100%</button>
  </div>
  <div v-if="mode==='bind'" class="flow-modebar">
    <strong>建立绑定</strong>
    <span>{{modebarText}}</span>
    <button class="quiet" @click="setMode(mode)">退出 · Esc</button>
  </div>
  <p v-if="notice" class="property-feedback flow-notice" role="status">{{notice}}</p>
  <section class="flow-body">
    <aside v-show="listOpen" class="flow-list">
      <h3>节点 <small>{{processingNodes(state).length + 2}}</small></h3>
      <div v-for="item in nodeList" :key="item.id" class="listrow">
        <button class="list-btn" :class="{active: selectedId===item.id, boundary: item.fixed}" @click="select(item.id)">
          <strong>{{item.name}}</strong>
          <small>{{item.kindLabel}}{{(!checkStale && issueCounts[item.id]) ? ` · ${issueCounts[item.id]} 个问题` : ''}}</small>
        </button>
      </div>
    </aside>
    <div class="flow-center">
      <FlowCanvas ref="canvasRef" :state="state" :issue-counts="issueCounts" :check-stale="checkStale" :mode="mode" :bind-source-id="bindSource" @before-change="emit('before-change')" @changed="emit('changed')" @select="select" @bind="onCanvasBind" @unlink="handleUnlink"/>
      <div class="canvas-note">点击节点查看配置 · 测试在「测试调试」视图进行 · 连线由参数来源派生</div>
    </div>
    <aside v-if="selectedKind" class="flow-inspector" :style="{ width: inspectorWidth + 'px' }">
      <div class="inspector-resizer" :aria-hidden="true" @mousedown="startResize"></div>
      <header class="detail-head">
        <div class="head-row">
          <small class="detail-type">{{selectedKind==='input'?'编排输入':selectedKind==='output'?'编排输出':(NODE_KIND_LABELS[selectedKind]||selectedKind) + (selectedKind==='python'?' · LLM 推演':'')}}</small>
          <span class="push"></span>
          <button class="quiet" aria-label="关闭节点详情" @click="select('')">×</button>
        </div>
        <div v-if="node" class="head-row">
          <button @click="openTestView('single', selectedId)">▷ 测试此节点</button>
          <button class="quiet" @click="deleteSelected">删除节点…</button>
        </div>
      </header>
      <div class="detail-content">
        <NodeConfig v-if="node" :state="state" :node="node" :anchor="inspectorTab" :connections="projectConnections || []" :credentials="credentials" :providers="providers" :providers-status="providersStatus" :connections-status="connectionsStatus" :has-project="!!projectId" :focus="focusRequest" @before-change="emit('before-change')" @changed="emit('changed')" @expand-code="onExpandCode" @open-llm-config="emit('navigate','llm',{ tab: inspectorTab, definition: selectedId })" @open-connections="emit('navigate','connections')" @retry-providers="loadProviders"/>
        <BoundaryConfig v-else-if="selectedKind==='input'" :state="state" kind="input" @before-change="emit('before-change')" @changed="emit('changed')"/>
        <BoundaryConfig v-else-if="selectedKind==='output'" :state="state" kind="output" @before-change="emit('before-change')" @changed="emit('changed')"/>
      </div>
    </aside>
  </section>
  <section class="flow-dock">
    <div class="dock-head">
      <strong>配置问题</strong>
      <small class="dock-state">{{!checkSnap ? '尚未检查' : checkStale ? '配置已修改，上次结论待重新检查' : (checkSnap.check.errors?.length ? `检查发现 ${checkSnap.check.errors.length} 个问题` : '检查通过')}}</small>
      <button class="quiet push" @click="dockOpen=!dockOpen">{{dockOpen?'收起 ↓':'展开 ↑'}}</button>
    </div>
    <div v-if="dockOpen" class="dock-content">
      <div v-if="!checkSnap" class="empty empty-dense">点击「检查配置」，发现缺失和不匹配的配置；检查不执行任何节点。</div>
      <div v-else-if="!issueDiags.length" class="empty empty-dense good">检查通过（未执行验证）。配置修改后会标记待重新检查。</div>
      <div v-else>
        <p v-if="checkStale" class="stale-note">以下为上次检查结果；配置已修改，请重新检查。</p>
        <div v-for="(d,i) in issueDiags" :key="i" class="issue-row">
          <span :class="d.level==='error'?'error':'amber'">{{d.level==='error'?'错误':'警告'}}</span>
          <div class="issue-main">
            <strong>{{nameOf(d.id)}} · {{fieldLabel(d)}}</strong>
            <small>{{d.message}}{{checkStale?'（上次检查，需重新确认）':''}}</small>
          </div>
          <button class="mini" @click="locate(d)">去处理 →</button>
        </div>
      </div>
    </div>
  </section>

  <div v-if="linkDialog" class="modal-backdrop" @click.self="closeLinkDialog">
    <section class="flow-modal" role="dialog" aria-modal="true" aria-label="建立参数绑定">
      <h2>建立参数绑定</h2>
      <p class="field-help">「{{nameOf(linkDialog.sourceId)}}」→「{{nameOf(linkDialog.targetId)}}」。确认才写入目标输入绑定并派生连线；取消不做任何修改。</p>
      <p v-if="linkDialog.fromInput" class="field-help">来源是「编排输入」：下方来源类型已预选「编排入口参数」，选择具体入口参数后确认。</p>
      <label>目标输入
        <AppSelect v-model="linkDialog.chosenInputId" :options="linkDialog.inputs.map((i:any)=>({value:i.id,label:`${i.label||i.name||'未命名'}（${i.name}）${i.source?' · 已有来源':''}`}))" aria-label="目标输入"/>
      </label>
      <BindingEditor v-if="linkShadowOwner" :state="state" :owner="linkShadowOwner" dialog @apply="applyLinkDialog" @close="closeLinkDialog"/>
      <p v-if="linkIssue" class="inline-warning" role="status">{{linkIssue}}</p>
    </section>
  </div>

  <div v-if="outDialog" class="modal-backdrop" @click.self="closeDialog()">
    <section class="flow-modal" role="dialog" aria-modal="true" aria-label="绑定到编排输出">
      <h2>绑定到编排输出</h2>
      <p class="field-help">把「{{nameOf(outDialog.sourceId)}}」的输出接到「编排输出」的声明：选择输出声明与来源输出端口，确认后派生连线并保存。</p>
      <label>编排输出声明
        <AppSelect v-model="outDialog.chosenOutputId" :options="(state.outputs||[]).filter((o:any)=>o&&typeof o==='object').map((o:any)=>({value:o.id,label:`${o.label||o.name||o.id} · ${typeSummary(o.type)}${o.binding?' · 已绑定':''}`}))" aria-label="编排输出声明" @update:model-value="outDialog!.chosenOutputId=$event as string; recomputeOutIssue()"/>
      </label>
      <label>来源输出端口
        <AppSelect v-model="outDialog.chosenPortId" :options="((state.nodes||[]).find((n:any)=>n.id===outDialog!.sourceId)?.outputs||[]).map((p:any)=>({value:p.id,label:`${p.label||p.name||p.id} · ${typeSummary(p.type)}`}))" aria-label="来源输出端口" @update:model-value="outDialog!.chosenPortId=$event as string; recomputeOutIssue()"/>
      </label>
      <p v-if="outDialog.issue" class="inline-error" role="alert">{{outDialog.issue}}</p>
      <div class="dialogtools">
        <button type="button" @click="closeDialog()">取消</button>
        <button type="button" class="primary" :disabled="!!outDialog.issue" @click="applyOutDialog">确认绑定</button>
      </div>
    </section>
  </div>

  <div v-if="renameOpen" class="modal-backdrop" @click.self="closeDialog()">
    <form class="flow-modal" role="dialog" aria-modal="true" aria-label="编排名称" @submit.prevent="applyRename">
      <h2>编排名称</h2>
      <label>名称<input v-model="renameBuffer" required maxlength="80" aria-label="编排名称" @input="dialogDirty=true"/></label>
      <div class="dialogtools">
        <button type="button" @click="closeDialog()">取消</button>
        <button type="submit" class="primary">确定</button>
      </div>
    </form>
  </div>

  <div v-if="expandDialog" class="modal-backdrop" @click.self="closeDialog()">
    <section class="flow-modal wide" role="dialog" aria-modal="true" :aria-label="expandDialog.title">
      <h2>{{expandDialog.title}} · {{expandDialog.nodeName}}</h2>
      <p class="field-help">展开编辑与面板编辑共用同一内容；取消不改动。</p>
      <textarea v-model="expandDialog.buffer" class="code-editor expand" rows="18" spellcheck="false" :aria-label="expandDialog.title" @input="dialogDirty=true"/>
      <div class="dialogtools">
        <button @click="closeDialog()">取消</button>
        <button class="primary" @click="applyExpand">应用到节点</button>
      </div>
    </section>
  </div>
  </div>
</div>
</template>
<style scoped>
.flow-page{display:flex;flex-direction:column;height:calc(100vh - 96px);min-height:480px}
.design-root{display:flex;flex-direction:column;flex:1;min-height:0}
.ftw-host{flex:1;min-height:0}
.run-project{font-size:12px;color:var(--muted);white-space:nowrap}
.run-project b{color:var(--ink)}
.linklike{border:0;background:none;color:var(--blue);font-size:12px;padding:2px 0;cursor:pointer}
.flow-head{background:var(--paper);border-bottom:1px solid var(--line);padding:10px 16px}
.head-row{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.flow-title{font-size:19px;font-weight:650;margin:0;display:flex;align-items:center;gap:4px;min-width:0}
.rename-btn{font-size:13px;padding:2px 6px}
.context{display:flex;align-items:center;gap:8px;font-size:12px;color:var(--muted);white-space:nowrap}
.context .app-select{min-width:170px}
.head-sub{margin-top:6px}
.desc-inline{border:1px solid transparent;background:transparent;padding:3px 6px;font-size:12px;color:var(--muted);max-width:640px}
.desc-inline:hover{border-color:var(--line)}
.check-tag{font-size:12px;white-space:nowrap}
.desc-details{flex:1;min-width:200px;font-size:12px;color:var(--muted)}
.desc-details summary{cursor:pointer;user-select:none}
.desc-details textarea{margin-top:6px}
.push{margin-left:auto;flex:none}
.quiet{border-color:transparent;background:transparent}
.quiet:hover{background:var(--paper-2)}
.quiet.active{background:var(--blue-soft);color:var(--blue-ink)}
.quiet.amber{color:var(--warn)}
.quiet.error{color:var(--danger)}
.quiet.good{color:var(--ok)}
.flow-toolbar{display:flex;gap:6px;align-items:center;min-height:46px;padding:6px 16px;border-bottom:1px solid var(--line);background:var(--paper);flex-wrap:wrap}
.menuwrap{position:relative}
.menu{position:absolute;top:40px;left:0;background:var(--paper);border:1px solid var(--line-2);box-shadow:var(--shadow-2);min-width:180px;padding:4px;z-index:30;border-radius:var(--r-sm)}
.menu button{display:block;border:0;width:100%;text-align:left}
.flow-modebar{min-height:38px;display:flex;align-items:center;gap:10px;padding:4px 16px;background:var(--paper-2);border-bottom:1px solid var(--line);font-size:12px;color:var(--muted);flex-wrap:wrap}
.flow-modebar strong{color:var(--ink)}
.flow-notice{margin:0;padding:6px 16px;border-radius:0}
.flow-body{display:flex;min-height:0;flex:1;position:relative;background:var(--paper)}
.flow-list{width:190px;flex-shrink:0;border-right:1px solid var(--line);overflow:auto;padding:10px 8px;background:var(--paper)}
.flow-list h3{font-size:12px;margin:2px 8px 10px;color:var(--muted)}
.listrow{display:flex;align-items:flex-start;gap:5px;margin:3px 0}
.listrow input[type="checkbox"]{width:16px;height:16px;margin-top:12px;flex:none}
.list-btn{flex:1;min-width:0;text-align:left;white-space:normal;display:flex;flex-direction:column;border-color:transparent;padding:8px 10px}
.list-btn:hover{background:var(--paper-2)}
.list-btn.active{background:var(--blue-soft);color:var(--blue-ink)}
.list-btn.boundary{border-left:3px solid v-bind("FLOW_CATEGORY.boundary.border")}
.list-btn small{color:var(--muted)}
.flow-center{min-width:0;flex:1;position:relative;display:flex}
.flow-center :deep(.flow-canvas){flex:1}
.canvas-note{position:absolute;bottom:14px;left:16px;pointer-events:none;font-size:12px;color:var(--muted);background:var(--paper-float);padding:5px 12px;border:1px solid var(--line);border-radius:var(--r-sm);z-index:5}
.flow-inspector{flex-shrink:0;background:var(--paper);border-left:1px solid var(--line);display:flex;flex-direction:column;min-height:0}
.detail-head{padding:10px 14px 8px;border-bottom:1px solid var(--line)}
.detail-type{color:var(--muted);letter-spacing:.5px}
.detail-name{font-weight:600;margin:6px 0 8px}
.detail-tabs{display:flex;border-bottom:1px solid var(--line);padding:0 10px}
.detail-tabs button{flex:1;padding:8px 4px;border:0;border-bottom:2px solid transparent;border-radius:0}
.detail-tabs button.active{border-bottom:2px solid var(--blue);color:var(--blue-ink)}
.detail-content{padding:12px 14px;overflow:auto;flex:1;min-height:0}
.inspector-resizer{position:absolute;left:-3px;top:0;bottom:0;width:6px;cursor:col-resize;z-index:9}
.flow-inspector{position:relative}
.flow-dock{background:var(--paper);border-top:1px solid var(--line);flex-shrink:0;max-height:42%}
.dock-head{display:flex;align-items:center;padding:5px 16px;gap:10px}
.dock-state{color:var(--muted);font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.dock-content{height:190px;overflow:auto;padding:4px 16px 12px;border-top:1px solid var(--line)}
.issue-row{display:flex;gap:12px;align-items:center;border-top:1px solid var(--line);padding:8px 0}
.issue-row:first-child{border-top:0}
.issue-main{flex:1;min-width:0}
.issue-main small{display:block;color:var(--muted)}
.stale-note{color:var(--warn);font-size:12px;margin:6px 0}
.run-meta{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin:8px 0}
.write-summary{background:var(--warn-soft);border:1px solid var(--warn-line);border-radius:var(--r-sm);padding:9px 12px;font-size:12px;margin:8px 0;color:var(--ink)}
.write-summary small{color:var(--muted);display:block;margin-top:4px}
.viewer-tabs{display:flex;gap:4px;margin:8px 0}
.viewer-tabs button{font-size:12px;padding:3px 12px;border-radius:var(--r-sm)}
.viewer-tabs button.active{background:var(--blue-soft);border-color:var(--blue-line);color:var(--blue-ink)}
.viewer-json,.viewer-logs{font-family:ui-monospace,Menlo,monospace;font-size:12px;background:var(--paper-2);border-radius:var(--r-sm);padding:10px 12px;white-space:pre;overflow:auto;max-height:260px;margin:0}
.viewer-logs{white-space:pre-wrap}
.flow-modal{background:var(--paper);border-radius:var(--r-md);width:560px;max-width:94vw;max-height:90vh;overflow:auto;padding:20px 22px;box-shadow:var(--shadow-2)}
.flow-modal.wide{width:760px}
.flow-modal h2{font-size:16px;margin:0 0 8px}
.code-editor.expand{width:100%;min-height:44vh}
.empty-dense{padding:26px}
.empty.good{color:var(--ok)}
.amber{color:var(--warn)}
.error{color:var(--danger)}
.good{color:var(--ok)}
@media(max-width:1439px){.flow-list{position:absolute;inset:0 auto 0 0;z-index:18;width:210px;box-shadow:var(--shadow-2)}}
@media(max-width:1099px){.flow-inspector{position:absolute;right:0;top:0;bottom:0;z-index:20;max-width:100%}.inspector-resizer{display:none}}
@media(max-width:767px){.flow-inspector{width:100%!important}.head-row{gap:6px}.flow-toolbar{gap:4px;padding:6px 10px}}
</style>
