<!-- FlowEditor — 函数编排编辑页（交互评审优化版）：专注布局、画布为中心。
     · 头部：返回编排列表 / 名称 / 说明（收起）/ 检查配置（状态可点击开问题面板）/ 运行全部。
     · 工具栏独立行：节点列表开关 / ＋添加节点 / 建立绑定 / 选择测试节点 / 整理 / 全图 / 100%。
     · 模式栏固定占位：查看、建立绑定、测试范围三种互斥模式与退出方式。
     · 详情按需显示（无选择不占宽），页签：输入/实现/输出/高级；测试此节点/删除在详情头部。
     · 底部面板：问题（带定位 diagnostics）与本次结果（范围/快照/耗时），可收起。
     · 保存与撤销重做沿用 App 的 flow Saver；本页 emit before-change/changed。 -->
<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { appConfirm } from '../shared/appConfirm'
import AppSelect from '../shared/AppSelect.vue'
import FlowCanvas from './FlowCanvas.vue'
import NodeConfig from './NodeConfig.vue'
import BoundaryConfig from './BoundaryConfig.vue'
import BindingEditor from './BindingEditor.vue'
import { INPUT_NODE, NODE_KIND_LABELS, OUTPUT_NODE, SECTION_LABELS, chainExternalInputs, chainOrder, clone, configSignature, defaultPosition, missingUpstreams, nodeRemovalImpact, processingNodes, uid, validateTestValue, writeCapabilities } from './flowModel'
import { checkFlow, runFlow } from './api'
import * as llm from '../tools/llm'
import { getJson } from '../app/http'

const props = defineProps<{ state: any; projectConnections?: any[]; projectId?: string; revision?: string; projectName?: string; saveCheck?: any; saveCheckSig?: string; restoreTab?: string; restoreNode?: { id: string; token: number } | null; providersRefresh?: number }>()
const emit = defineEmits(['before-change', 'changed', 'navigate', 'back'])
const canvasRef = ref<any>(null)
const notice = ref('')
// 从模型设置返回：恢复页签与节点选中，刷新可用模型列表（保留原 providerId，不自动重绑/执行）
watch(() => props.restoreTab, t => { if (t) inspectorTab.value = t })
watch(() => props.restoreNode, n => { if (n?.id) { selectedId.value = n.id; inspectorTab.value = inspectorTab.value || 'implementation'; nextTick(() => canvasRef.value?.focusNode(n.id)) } })
watch(() => props.providersRefresh, () => { void loadProviders() })
let noticeTimer: any = null
function warn(text: string) { notice.value = text; clearTimeout(noticeTimer); noticeTimer = setTimeout(() => notice.value = '', 5000) }

// ── 选择 / 模式 / 测试范围（三种独立状态，互斥模式） ────────────────────────────
const selectedId = ref('')
const inspectorTab = ref('implementation')
const mode = ref<'inspect' | 'bind' | 'test'>('inspect')
const bindSource = ref('')
const testIds = ref<string[]>([])
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
function setMode(next: 'inspect' | 'bind' | 'test') {
  mode.value = mode.value === next ? 'inspect' : next
  bindSource.value = ''
  if (mode.value !== 'test') testIds.value = []
}
function toggleTest(id: string) {
  if (testIds.value.includes(id)) testIds.value = testIds.value.filter(x => x !== id)
  else testIds.value = [...testIds.value, id]
}
const missing = computed(() => mode.value === 'test' ? missingUpstreams(props.state, testIds.value) : [])
const missingNames = computed(() => missing.value.map(id => (props.state.nodes.find((n: any) => n.id === id) || {}).name || id))
function completeUpstreams() { testIds.value = [...new Set([...testIds.value, ...missing.value])] }
watch(() => props.state, () => {
  if (selectedId.value && !nodeList.value.some((n: any) => n.id === selectedId.value)) selectedId.value = ''
  testIds.value = testIds.value.filter(id => nodeList.value.some((n: any) => n.id === id))
})
const modebarText = computed(() => {
  if (mode.value === 'bind') return bindSource.value
    ? `建立绑定 · 来源已选：「${(props.state.nodes.find((n: any) => n.id === bindSource.value) || {}).name || bindSource.value}」，请点击目标节点`
    : '建立绑定 · 先点击来源处理节点，再点击目标节点'
  if (mode.value === 'test') {
    if (!testIds.value.length) return '在列表或画布勾选要测试的节点（点击节点名称仍可查看配置）'
    return missing.value.length
      ? `已选 ${testIds.value.length} 个节点，缺少上游：${missingNames.value.join('、')}`
      : `已选 ${testIds.value.length} 个节点，按依赖顺序执行`
  }
  return '查看模式 · 点击节点查看配置；「建立绑定」「选择测试节点」是独立操作'
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
  testIds.value = testIds.value.filter(x => x !== id)
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
    dockTab.value = 'issues'; dockOpen.value = true
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

// ── 运行 / 测试（快照、等待态、结果面板） ───────────────────────────────────────
const running = ref(false)
const runSnapshot = ref<{ scopeText: string; targets: string[] | null; submittedAt: string; sig: string; status: string; durationMs: number } | null>(null)
const runResults = ref<Record<string, any>>({})
const runStale = computed(() => !!runSnapshot.value && runSnapshot.value.sig !== currentSig.value)
const dockOpen = ref(false), dockTab = ref<'issues' | 'results'>('issues')
const entryOpen = ref(false)
const entryValues = ref<Record<string, any>>({})
const testDialog = ref<{ scope: 'single' | 'group'; targets: string[]; rows: any[]; values: Record<string, any>; writes: string[] } | null>(null)
const dialogDirty = ref(false)
let lastFocused: HTMLElement | null = null
const entryFields = computed(() => (props.state.inputs || []).map((i: any) => ({
  key: i.name || i.id, label: i.label || i.name || i.id, type: i.type || { type: 'text' }, required: true })))

function scopeNodeIds(scope: 'all' | 'single' | 'group'): string[] {
  if (scope === 'all') return processingNodes(props.state).map((n: any) => n.id)
  if (scope === 'single') return [selectedId.value]
  return chainOrder(props.state, testIds.value).order
}
function startRun(scope: 'all' | 'single' | 'group') {
  if (running.value) return
  if (scope === 'single' && (!selectedId.value || selectedId.value === INPUT_NODE || selectedId.value === OUTPUT_NODE)) return
  const targets = scopeNodeIds(scope)
  if (!targets.length) return warn(scope === 'group' ? '请先勾选要测试的节点' : '编排中还没有处理节点')
  if (scope === 'group') {
    // 缺上游 gate 只用于范围测试；单节点测试按规格允许手工提供上游输入（不执行真实上游）
    const missingNow = missingUpstreams(props.state, targets)
    if (missingNow.length) return warn(`缺少上游节点：${missingNow.map(nameOf).join('、')}。请在测试模式中补选后重试。`)
  }
  const rows = scope === 'all'
    ? entryFields.value.map(f => ({ key: f.key, nodeName: '编排输入', input: { name: f.key, label: f.label, type: f.type }, source: null }))
    : chainExternalInputs(props.state, targets)
  const writes = writeCapabilities(props.state, targets)
  if (scope === 'all') openEntryDialog(writes)
  else openTestDialog(scope, targets, rows, writes)
}
function openEntryDialog(writes: string[]) {
  entryValues.value = {}
  pendingWrites.value = writes
  if (!entryFields.value.length && !writes.length) { void executeAll({}); return }
  openDialog()
  entryOpen.value = true
}
const pendingWrites = ref<string[]>([])
function openTestDialog(scope: 'single' | 'group', targets: string[], rows: any[], writes: string[]) {
  const values: Record<string, any> = {}
  for (const row of rows) if (row.fixedValue !== undefined) values[row.key] = row.fixedValue
  pendingWrites.value = writes
  dialogDirty.value = false
  if (!rows.length && !writes.length) { void execute(targets, values, scope, targets); return }
  openDialog()
  testDialog.value = { scope, targets, rows, values, writes }
}
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
  if (entryOpen.value) { entryOpen.value = false }
  testDialog.value = null
  expandDialog.value = null
  dialogDirty.value = false
  onClosed?.()
  lastFocused?.focus?.()
  lastFocused = null
}
function anyModalOpen() { return entryOpen.value || !!testDialog.value || !!expandDialog.value }
async function submitRunAll() {
  const inputs: Record<string, any> = {}
  for (const field of entryFields.value) {
    const verdict = validateTestValue(field.type, entryValues.value[field.key])
    if (!verdict.ok) { warn(`入口参数「${field.label}」：${verdict.error}`); return }
    inputs[field.key] = verdict.value
  }
  await closeDialog(true)
  await executeAll(inputs)
}
async function executeAll(inputs: Record<string, any>) {
  await execute(null, inputs, 'all', [])
}
async function submitTest() {
  const dialog = testDialog.value
  if (!dialog) return
  const inputs: Record<string, any> = {}
  for (const row of dialog.rows) {
    if (row.fixedValue !== undefined) { inputs[row.key] = row.fixedValue; continue }
    const verdict = validateTestValue(row.input.type, dialog.values[row.key])
    if (!verdict.ok) { warn(`「${row.nodeName} · ${row.input.label || row.input.name}」：${verdict.error}`); return }
    inputs[row.key] = verdict.value
  }
  await closeDialog(true)
  await execute(dialog.targets, inputs, dialog.scope, dialog.targets)
}
async function execute(targets: string[] | null, inputs: Record<string, any>, scope: 'all' | 'single' | 'group', scopeIds: string[]) {
  running.value = true
  const scopeText = scopeText2(scope, targets)
  runSnapshot.value = { scopeText, targets, submittedAt: new Date().toLocaleTimeString('zh-CN', { hour12: false }), sig: currentSig.value, status: 'running', durationMs: 0 }
  const affected = targets || processingNodes(props.state).map((n: any) => n.id)
  runResults.value = {}
  for (const id of affected) runResults.value[id] = { status: 'waiting' }
  dockTab.value = 'results'; dockOpen.value = true
  try {
    const payload: any = { state: clone(props.state), projectId: props.projectId || undefined, inputs }
    if (targets) payload.targets = targets
    else payload.revision = props.revision
    const d = await runFlow(payload)
    for (const entry of d.nodeResults || []) {
      runResults.value[entry.nodeId] = {
        status: entry.status, durationMs: entry.durationMs, rowCount: entry.rowCount,
        error: entry.error, logs: entry.logs || [],
        outputs: entry.status === 'success' ? (entry.outputs ?? null) : null,
      }
    }
    const ok = (d.nodeResults || []).filter((r: any) => r.status === 'success').length
    const bad = (d.nodeResults || []).filter((r: any) => r.status === 'failed')
    const skip = (d.nodeResults || []).filter((r: any) => r.status === 'skipped').length
    runSnapshot.value = { ...runSnapshot.value!, status: d.status, durationMs: d.durationMs ?? 0 }
    if (bad.length) {
      select(bad[0].nodeId)
      warn(`「${nameOf(bad[0].nodeId)}」执行失败：${bad[0].error}`)
    }
    void ok; void skip
  } catch (e: any) {
    runSnapshot.value = { ...runSnapshot.value!, status: 'request-error' }
    for (const id of affected) if (runResults.value[id]?.status === 'waiting') delete runResults.value[id]
    warn('运行请求失败：' + (e?.message || e))
  } finally { running.value = false }
}
function scopeText2(scope: 'all' | 'single' | 'group', targets: string[] | null): string {
  if (scope === 'all') return '运行全部（编排整体）'
  if (scope === 'single') return `测试此节点：${nameOf((targets || [])[0])}`
  return `测试所选节点（${(targets || []).length} 个）：${(targets || []).map(nameOf).join('、')}`
}
function clearRun() { runResults.value = {}; runSnapshot.value = null }
function statusText(status: string): string {
  return ({ waiting: '等待结果', success: '成功', failed: '失败', skipped: '跳过' } as Record<string, string>)[status] || status
}

// ── 绑定（唯一 binding 真相；取消零改动；删除有确认） ────────────────────────────
const linkDialog = ref<{ sourceId: string; targetId: string; inputs: any[]; chosenInputId: string; shadow: any } | null>(null)
function onCanvasBind(nodeId: string) {
  if (!bindSource.value) { bindSource.value = nodeId; return }
  if (bindSource.value === nodeId) { bindSource.value = ''; return }
  const sourceId = bindSource.value
  bindSource.value = ''
  const target = props.state.nodes.find((n: any) => n.id === nodeId)
  const source = props.state.nodes.find((n: any) => n.id === sourceId)
  if (!target || !source) return
  if (!target.inputs.length) return warn(`「${target.name}」尚未声明输入参数，请先在详情「输入」页签添加`)
  const firstUnbound = target.inputs.find((i: any) => !i.source)
  openDialog()
  linkDialog.value = { sourceId, targetId: nodeId, inputs: target.inputs, chosenInputId: (firstUnbound || target.inputs[0]).id, shadow: null }
}
const linkShadowOwner = computed(() => {
  if (!linkDialog.value) return null
  const input = linkDialog.value.inputs.find((i: any) => i.id === linkDialog.value!.chosenInputId)
  if (!input) return null
  if (!linkDialog.value.shadow || linkDialog.value.shadow.id !== input.id) {
    linkDialog.value.shadow = { ...input, source: clone(input.source) }
  }
  return { nodeId: linkDialog.value.targetId, input: linkDialog.value.shadow }
})
function applyLinkDialog() {
  if (!linkDialog.value || !linkShadowOwner.value) return closeLinkDialog()
  const shadow = linkDialog.value.shadow
  const src = shadow.source
  if (src && (src.kind === 'node' || src.kind === 'nodeField') && !src.outputId) return closeLinkDialog()
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

// ── 输出检查器（结果详情） ─────────────────────────────────────────────────────
const outView = ref<{ title: string; entry: any; tab: string } | null>(null)
function openOutput(id: string) {
  const entry = runResults.value[id]
  if (!entry) return
  openDialog()
  outView.value = { title: `输出检查器 · ${nameOf(id)}`, entry, tab: entry.status === 'failed' ? 'log' : 'out' }
}
function closeOut() { outView.value = null; lastFocused?.focus?.(); lastFocused = null }
function outJson(): string {
  const entry = outView.value?.entry
  if (!entry) return ''
  if (entry.status === 'failed') return '错误：' + (entry.error || '')
  return JSON.stringify(entry.outputs ?? null, null, 2)
}
function copyOut() { navigator.clipboard?.writeText(outJson()) }

// ── 详情宽度拖拽（360–560，记忆到 localStorage；窄屏覆盖面板不拖拽） ────────────────
const inspectorWidth = ref(Number(localStorage.getItem('wiz-flow-detail-w')) || 480)
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
    localStorage.setItem('wiz-flow-detail-w', String(inspectorWidth.value))
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
  if (outView.value) { e.preventDefault(); closeOut(); return }
  if (addMenu.value) { addMenu.value = false; return }
  if (mode.value !== 'inspect') { e.preventDefault(); setMode(mode.value) }
}
onMounted(() => document.addEventListener('keydown', onKeydown))
onBeforeUnmount(() => document.removeEventListener('keydown', onKeydown))
</script>
<template>
<div class="flow-page">
  <header class="flow-head">
    <div class="head-row">
      <button class="quiet" @click="emit('back')">← 编排列表</button>
      <input class="name-input" :value="state.name" aria-label="编排名称" placeholder="编排名称" @input="emit('before-change');state.name=($event.target as HTMLInputElement).value;emit('changed')"/>
      <details class="desc-details">
        <summary>说明与运行上下文</summary>
        <textarea :value="state.description" rows="2" aria-label="编排说明" placeholder="这个编排做什么（选填）" @input="emit('before-change');state.description=($event.target as HTMLTextAreaElement).value;emit('changed')"/>
        <small>当前项目：{{ projectName || '未选择（连接与凭据按项目上下文解析）' }}</small>
      </details>
      <span class="push"></span>
      <button :disabled="checking||running" @click="runCheck">{{checking?'检查中…':'检查配置'}}</button>
      <button class="quiet" :class="checkStateClass" :title="'打开问题面板'" @click="dockOpen=true;dockTab='issues'">{{checkStateText}}</button>
      <button class="primary-run" :disabled="checking||running" @click="startRun('all')">▶ 运行全部</button>
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
    <button :class="{active: mode==='bind'}" :disabled="running" @click="setMode('bind')">建立绑定</button>
    <button :class="{active: mode==='test'}" :disabled="running" @click="setMode('test')">选择测试节点</button>
    <span class="push"></span>
    <button :disabled="running" @click="canvasRef?.autoLayout()">整理</button>
    <button @click="canvasRef?.fitAll()">全图</button>
    <button @click="canvasRef?.zoom100()">100%</button>
  </div>
  <div class="flow-modebar">
    <strong v-if="mode!=='inspect'">{{mode==='bind'?'建立绑定':'测试范围'}}</strong>
    <span>{{modebarText}}</span>
    <button v-if="mode==='test'&&missing.length" @click="completeUpstreams">补选所需上游</button>
    <button v-if="mode==='test'" :disabled="running||!testIds.length||!!missing.length" @click="startRun('group')">测试所选节点</button>
    <button v-if="mode!=='inspect'" class="quiet" @click="setMode(mode)">退出 · Esc</button>
  </div>
  <p v-if="notice" class="property-feedback flow-notice" role="status">{{notice}}</p>
  <section class="flow-body">
    <aside v-show="listOpen" class="flow-list">
      <h3>节点 <small>{{processingNodes(state).length + 2}}</small></h3>
      <div v-for="item in nodeList" :key="item.id" class="listrow">
        <input v-if="mode==='test' && !item.fixed" type="checkbox" :checked="testIds.includes(item.id)" :aria-label="`测试 ${item.name}`" @change="toggleTest(item.id)"/>
        <button class="list-btn" :class="{active: selectedId===item.id, boundary: item.fixed}" @click="select(item.id)">
          <strong>{{item.name}}</strong>
          <small>{{item.kindLabel}}{{(!checkStale && issueCounts[item.id]) ? ` · ${issueCounts[item.id]} 个问题` : ''}}{{(!checkStale && runResults[item.id]?.status==='failed') ? ' · 运行失败' : ''}}</small>
        </button>
      </div>
    </aside>
    <div class="flow-center">
      <FlowCanvas ref="canvasRef" :state="state" :results="runResults" :issue-counts="issueCounts" :check-stale="checkStale" :mode="mode" :test-ids="testIds" :bind-source-id="bindSource" @before-change="emit('before-change')" @changed="emit('changed')" @select="select" @bind="onCanvasBind" @unlink="handleUnlink" @test-toggle="toggleTest"/>
      <div class="canvas-note">点击节点查看配置 · 选择测试范围不会替代详情选择</div>
    </div>
    <aside v-if="selectedKind" class="flow-inspector" :style="{ width: inspectorWidth + 'px' }">
      <div class="inspector-resizer" :aria-hidden="true" @mousedown="startResize"></div>
      <header class="detail-head">
        <div class="head-row">
          <small class="detail-type">{{selectedKind==='input'?'编排输入':selectedKind==='output'?'编排输出':(NODE_KIND_LABELS[selectedKind]||selectedKind) + (selectedKind==='python'?' · LLM 推演':'')}}</small>
          <span class="push"></span>
          <button class="quiet" aria-label="关闭节点详情" @click="select('')">×</button>
        </div>
        <input v-if="node" class="detail-name" :value="node.name" aria-label="节点名称" @input="emit('before-change');node.name=($event.target as HTMLInputElement).value;emit('changed')"/>
        <div v-if="node" class="head-row">
          <button @click="startRun('single')">测试此节点</button>
          <button class="quiet" @click="deleteSelected">删除节点…</button>
        </div>
      </header>
      <nav v-if="node" class="detail-tabs" aria-label="节点配置页签">
        <button v-for="t in ['inputs','implementation','outputs','advanced']" :key="t" :class="{active: inspectorTab===t}" @click="inspectorTab=t">{{SECTION_LABELS[t]}}</button>
      </nav>
      <div class="detail-content">
        <NodeConfig v-if="node" :state="state" :node="node" :tab="inspectorTab" :connections="projectConnections || []" :credentials="credentials" :providers="providers" :providers-status="providersStatus" :connections-status="connectionsStatus" :focus="focusRequest" @before-change="emit('before-change')" @changed="emit('changed')" @expand-code="onExpandCode" @open-llm-config="emit('navigate','llm',{ tab: inspectorTab, definition: selectedId })" @open-connections="emit('navigate','connections')" @retry-providers="loadProviders"/>
        <BoundaryConfig v-else-if="selectedKind==='input'" :state="state" kind="input" @before-change="emit('before-change')" @changed="emit('changed')"/>
        <BoundaryConfig v-else-if="selectedKind==='output'" :state="state" kind="output" @before-change="emit('before-change')" @changed="emit('changed')"/>
      </div>
    </aside>
  </section>
  <section class="flow-dock">
    <div class="dock-head">
      <button class="quiet" :class="{active: dockTab==='issues'}" @click="dockTab='issues';dockOpen=true">问题{{checkSnap ? `（${issueDiags.length}${checkStale?' · 上次':''}）` : ''}}</button>
      <button class="quiet" :class="{active: dockTab==='results'}" @click="dockTab='results';dockOpen=true">本次结果</button>
      <small class="dock-state">{{!checkSnap ? '尚未检查' : checkStale ? '配置已修改，上次结论待重新检查' : (checkSnap.check.errors?.length ? `检查发现 ${checkSnap.check.errors.length} 个问题` : '检查通过')}}<template v-if="runSnapshot"> · {{runSnapshot.status==='running'?'执行中…':runSnapshot.status==='request-error'?'运行请求失败':(runSnapshot.status==='success'?'本次运行成功':'本次运行失败')}}（{{runSnapshot.scopeText}}）</template></small>
      <button class="quiet push" @click="dockOpen=!dockOpen">{{dockOpen?'收起 ↓':'展开 ↑'}}</button>
      <button v-if="runSnapshot && runSnapshot.status!=='running'" class="quiet" @click="clearRun">清除结果</button>
    </div>
    <div v-if="dockOpen" class="dock-content">
      <template v-if="dockTab==='issues'">
        <div v-if="!checkSnap" class="empty">点击「检查配置」，发现缺失和不匹配的配置；检查不执行任何节点。</div>
        <div v-else-if="!issueDiags.length" class="empty good">检查通过（未执行验证）。配置修改后会标记待重新检查。</div>
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
      </template>
      <template v-else>
        <div v-if="!runSnapshot" class="empty">尚无测试结果。可运行全部、测试此节点或测试所选节点。</div>
        <div v-else>
          <p class="run-meta">
            <strong>{{runSnapshot.scopeText}}</strong> · 提交于 {{runSnapshot.submittedAt}}
            <span class="badge" :class="{amber: runStale}">{{runStale?'上次配置快照':'提交时快照'}}</span>
            <span v-if="runSnapshot.status==='running'" class="badge">执行中…</span>
            <span v-else-if="runSnapshot.status==='request-error'" class="badge error">请求失败</span>
            <span v-else class="badge" :class="runSnapshot.status==='success'?'good':'error'">{{runSnapshot.status==='success'?'成功':'失败'}} · {{runSnapshot.durationMs}}ms</span>
          </p>
          <div v-for="id in Object.keys(runResults)" :key="id" class="issue-row">
            <span :class="runResults[id].status==='success'?'good':(runResults[id].status==='failed'?'error':'muted')">{{statusText(runResults[id].status)}}</span>
            <div class="issue-main">
              <strong>{{nameOf(id)}}</strong>
              <small v-if="runResults[id].status==='success'">耗时 {{runResults[id].durationMs}}ms{{runResults[id].rowCount!=null?` · ${runResults[id].rowCount} 条`:''}}</small>
              <small v-else-if="runResults[id].status==='failed'" class="error">{{runResults[id].error}}</small>
              <small v-else-if="runResults[id].status==='skipped'">上游未成功，本次跳过</small>
              <small v-else>等待运行结果…</small>
            </div>
            <button v-if="runResults[id].status==='success'||runResults[id].status==='failed'" class="mini" @click="openOutput(id)">{{runResults[id].status==='failed'?'查看错误':'查看输出'}}</button>
          </div>
        </div>
      </template>
    </div>
  </section>

  <div v-if="linkDialog" class="modal-backdrop" @click.self="closeLinkDialog">
    <section class="flow-modal" role="dialog" aria-modal="true" aria-label="建立参数绑定">
      <h2>建立参数绑定</h2>
      <p class="field-help">「{{nameOf(linkDialog.sourceId)}}」→「{{nameOf(linkDialog.targetId)}}」。确认才写入目标输入绑定并派生连线；取消不做任何修改。</p>
      <label>目标输入
        <AppSelect v-model="linkDialog.chosenInputId" :options="linkDialog.inputs.map((i:any)=>({value:i.id,label:`${i.label||i.name||'未命名'}（${i.name}）${i.source?' · 已有来源':''}`}))" aria-label="目标输入"/>
      </label>
      <BindingEditor v-if="linkShadowOwner" :state="state" :owner="linkShadowOwner" dialog @apply="applyLinkDialog" @close="closeLinkDialog"/>
    </section>
  </div>

  <div v-if="entryOpen" class="modal-backdrop" @click.self="closeDialog()">
    <section class="flow-modal" role="dialog" aria-modal="true" aria-label="运行全部">
      <h2>运行全部</h2>
      <p class="field-help">范围：编排整体 · 基于提交时的配置快照；检查错误必须为 0 才会执行。</p>
      <div v-if="pendingWrites.length" class="write-summary">
        <strong>本次执行将产生真实外部调用：</strong>
        <div v-for="w in pendingWrites" :key="w">{{w}}</div>
        <small>「不保存草稿」不代表「不会修改外部系统」，请确认后再执行。</small>
      </div>
      <label v-for="field in entryFields" :key="field.key">{{field.label}} · {{field.type.type}}
        <select v-if="field.type.type==='boolean'" v-model="entryValues[field.key]" @change="dialogDirty=true"><option value="">（请选择）</option><option value="true">是</option><option value="false">否</option></select>
        <textarea v-else-if="field.type.type==='object'||field.type.type==='list'" v-model="entryValues[field.key]" rows="3" :placeholder="field.type.type==='object'?'{}':'[]'" @input="dialogDirty=true"/>
        <input v-else v-model="entryValues[field.key]" :type="field.type.type==='number'?'number':(field.type.type==='datetime'?'datetime-local':'text')" @input="dialogDirty=true"/>
      </label>
      <p v-if="!entryFields.length && !pendingWrites.length" class="muted">此编排没有入口参数。</p>
      <div class="dialogtools">
        <button @click="closeDialog()">取消</button>
        <button class="primary" :disabled="running" @click="submitRunAll">开始运行</button>
      </div>
    </section>
  </div>

  <div v-if="testDialog" class="modal-backdrop" @click.self="closeDialog()">
    <section class="flow-modal" role="dialog" aria-modal="true" aria-label="节点测试">
      <h2>{{testDialog.scope==='single'?'测试此节点':'测试所选节点'}}</h2>
      <p class="field-help">范围：{{scopeText2(testDialog.scope, testDialog.targets)}} · 基于提交时的配置快照；测试不保存草稿。执行顺序按依赖拓扑确定。</p>
      <div v-if="testDialog.writes.length" class="write-summary">
        <strong>本次执行将产生真实外部调用：</strong>
        <div v-for="w in testDialog.writes" :key="w">{{w}}</div>
        <small>「不保存草稿」不代表「不会修改外部系统」，请确认后再执行。</small>
      </div>
      <label v-for="row in testDialog.rows" :key="row.key">{{row.nodeName}} · {{row.input.label || row.input.name}} · {{row.input.type?.type}}{{row.input.source?'':'（未绑定，测试必填）'}}
        <input v-if="row.fixedValue !== undefined" :value="String(row.fixedValue)" readonly/>
        <select v-else-if="row.input.type?.type==='boolean'" v-model="testDialog.values[row.key]" @change="dialogDirty=true"><option value="">（请选择）</option><option value="true">是</option><option value="false">否</option></select>
        <textarea v-else-if="row.input.type?.type==='object'||row.input.type?.type==='list'" v-model="testDialog.values[row.key]" rows="3" :placeholder="row.input.type?.type==='object'?'{}':'[]'" @input="dialogDirty=true"/>
        <input v-else v-model="testDialog.values[row.key]" :type="row.input.type?.type==='number'?'number':(row.input.type?.type==='datetime'?'datetime-local':'text')" @input="dialogDirty=true"/>
      </label>
      <p v-if="!testDialog.rows.length && !testDialog.writes.length" class="muted">该范围没有外部输入，确认后直接执行。</p>
      <div class="dialogtools">
        <button @click="closeDialog()">取消</button>
        <button class="primary" :disabled="running" @click="submitTest">开始测试</button>
      </div>
    </section>
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

  <div v-if="outView" class="modal-backdrop" @click.self="closeOut">
    <section class="flow-modal" role="dialog" aria-modal="true" aria-label="输出检查器">
      <h2>{{outView.title}}</h2>
      <p class="field-help">结果仅随本次运行返回，不持久化。LLM 节点日志保留请求与响应摘要。</p>
      <div class="viewer-tabs">
        <button :class="{active: outView.tab==='out'}" @click="outView.tab='out'">输出</button>
        <button :class="{active: outView.tab==='log'}" @click="outView.tab='log'">日志</button>
      </div>
      <pre v-if="outView.tab==='out'" class="viewer-json">{{outJson()}}</pre>
      <pre v-else class="viewer-logs">{{(outView.entry.logs||[]).join('\n')||'（无日志）'}}</pre>
      <div class="dialogtools">
        <button class="mini" @click="copyOut">复制 JSON</button>
        <button @click="closeOut">关闭</button>
      </div>
    </section>
  </div>
</div>
</template>
<style scoped>
.flow-page{display:flex;flex-direction:column;height:100%;min-height:0}
.flow-head{background:var(--paper);border-bottom:1px solid var(--line);padding:10px 16px}
.head-row{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.name-input{max-width:280px;font-weight:600}
.desc-details{flex:1;min-width:200px;font-size:12px;color:var(--muted)}
.desc-details summary{cursor:pointer;user-select:none}
.desc-details textarea{margin-top:6px}
.push{margin-left:auto;flex:none}
.quiet{border-color:transparent;background:transparent}
.quiet:hover{background:var(--paper-2)}
.quiet.active{background:var(--blue-soft);color:var(--blue-ink)}
.quiet.amber{color:var(--warn,#9b660d)}
.quiet.error{color:var(--danger,#b53434)}
.quiet.good{color:var(--ok,#168054)}
.primary-run{background:var(--blue,#2458d5);border-color:var(--blue,#2458d5);color:#fff}
.primary-run:hover{opacity:.92;background:var(--blue,#2458d5);color:#fff}
.flow-toolbar{display:flex;gap:6px;align-items:center;min-height:46px;padding:6px 16px;border-bottom:1px solid var(--line);background:var(--paper);flex-wrap:wrap}
.menuwrap{position:relative}
.menu{position:absolute;top:40px;left:0;background:var(--paper);border:1px solid var(--line);box-shadow:var(--shadow-2,0 8px 24px #162d4d22);min-width:180px;padding:5px;z-index:30;border-radius:7px}
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
.list-btn.boundary{border-left:3px solid var(--purple,#8464d8)}
.list-btn small{color:var(--muted)}
.flow-center{min-width:0;flex:1;position:relative;display:flex}
.flow-center :deep(.flow-canvas){flex:1}
.canvas-note{position:absolute;bottom:10px;left:12px;pointer-events:none;font-size:12px;color:var(--muted);background:#ffffffeb;padding:5px 10px;border-radius:6px;z-index:5}
.flow-inspector{flex-shrink:0;background:var(--paper);border-left:1px solid var(--line);display:flex;flex-direction:column;min-height:0}
.detail-head{padding:10px 14px 8px;border-bottom:1px solid var(--line)}
.detail-type{color:var(--muted);letter-spacing:.5px}
.detail-name{font-weight:600;margin:6px 0 8px}
.detail-tabs{display:flex;border-bottom:1px solid var(--line);padding:0 10px}
.detail-tabs button{flex:1;padding:8px 4px;border:0;border-bottom:2px solid transparent;border-radius:0}
.detail-tabs button.active{border-bottom:2px solid var(--blue,#2458d5);color:var(--blue-ink)}
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
.stale-note{color:var(--warn,#9b660d);font-size:12px;margin:6px 0}
.run-meta{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin:8px 0}
.badge{font-size:11px;border-radius:10px;padding:1px 8px;background:var(--paper-2)}
.badge.good{color:var(--ok,#168054)}
.badge.error{color:var(--danger,#b53434)}
.badge.amber{color:var(--warn,#9b660d)}
.write-summary{background:var(--warn-soft,#fff8e9);border:1px solid var(--warn-line,#efdfba);border-radius:6px;padding:9px 12px;font-size:12px;margin:8px 0;color:var(--ink)}
.write-summary small{color:var(--muted);display:block;margin-top:4px}
.viewer-tabs{display:flex;gap:4px;margin:8px 0}
.viewer-tabs button{font-size:12px;padding:3px 12px;border-radius:6px}
.viewer-tabs button.active{background:var(--blue-soft);border-color:var(--blue-line);color:var(--blue-ink)}
.viewer-json,.viewer-logs{font-family:ui-monospace,Menlo,monospace;font-size:12px;background:var(--paper-2);border-radius:8px;padding:10px 12px;white-space:pre;overflow:auto;max-height:260px;margin:0}
.viewer-logs{white-space:pre-wrap}
.flow-modal{background:var(--paper);border-radius:10px;width:560px;max-width:94vw;max-height:90vh;overflow:auto;padding:20px 22px;box-shadow:0 20px 60px #0f213a44}
.flow-modal.wide{width:760px}
.flow-modal h2{font-size:16px;margin:0 0 8px}
.field-help{font-size:12px;color:var(--muted);margin:0 0 8px}
.code-editor.expand{width:100%;min-height:44vh}
.empty{padding:26px;text-align:center;color:var(--muted)}
.empty.good{color:var(--ok,#168054)}
.amber{color:var(--warn,#9b660d)}
.error{color:var(--danger,#b53434)}
.good{color:var(--ok,#168054)}
@media(max-width:1439px){.flow-list{position:absolute;inset:0 auto 0 0;z-index:18;width:210px;box-shadow:8px 0 20px #1b32531a}}
@media(max-width:1099px){.flow-inspector{position:absolute;right:0;top:0;bottom:0;z-index:20;max-width:100%}.inspector-resizer{display:none}}
@media(max-width:767px){.flow-inspector{width:100%!important}.head-row{gap:6px}.flow-toolbar{gap:4px;padding:6px 10px}}
</style>
