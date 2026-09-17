<!-- FlowEditor — 函数编排编辑页（v2）：顶部（名称/说明/检查配置/运行与测试）、左侧节点列表、
     中间派生连线画布、右侧当前节点配置。运行与测试经 /api/flow-run：
     · ▶ 运行全部：入口参数表单 → 全图拓扑执行（revision + errors gate）；
     · 链选择 + 测试选中：单节点/连续链测试（指定输入 → 输出，不落盘）；
     · 结果徽标（状态/耗时/边行数）经 results 下发画布；输出检查器查看 JSON 与日志。
     保存状态与撤销由 App 顶栏承担（flow Saver + inject('commit-now')），本页只 emit before-change/changed。 -->
<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { appConfirm } from '../shared/appConfirm'
import AppSelect from '../shared/AppSelect.vue'
import FlowCanvas from './FlowCanvas.vue'
import NodeConfig from './NodeConfig.vue'
import BoundaryConfig from './BoundaryConfig.vue'
import BindingEditor from './BindingEditor.vue'
import { INPUT_NODE, NODE_KIND_LABELS, OUTPUT_NODE, chainExternalInputs, chainOrder, clone, defaultPosition, nodeRemovalImpact, processingNodes, sourceSummary, uid } from './flowModel'
import { checkFlow, runFlow } from './api'
import * as llm from '../tools/llm'
import { getJson } from '../app/http'

const props = defineProps<{ state: any; check: any; projectConnections?: any[]; projectId?: string; revision?: string }>()
const emit = defineEmits(['before-change', 'changed', 'update:check', 'locate'])
const selectedId = ref('')
const canvasRef = ref<any>(null)
const notice = ref('')
let noticeTimer: any = null
function warn(text: string) { notice.value = text; clearTimeout(noticeTimer); noticeTimer = setTimeout(() => notice.value = '', 6000) }
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
  if (id && id !== INPUT_NODE && id !== OUTPUT_NODE) canvasRef.value?.focusNode(id)
}
watch(() => props.state, () => { if (selectedId.value && !nodeList.value.some((n: any) => n.id === selectedId.value)) selectedId.value = '' })

// --- LLM 提供方与项目 API 凭据（NodeConfig 下拉与提示用） ---
const providers = ref<any[]>([])
const credentials = ref<any[]>([])
onMounted(async () => {
  try { providers.value = (await llm.listProviders()).items || [] } catch { providers.value = [] }
})
watch(() => props.projectId, async (pid: string) => {
  credentials.value = []
  if (!pid) return
  try {
    const d = await getJson('/api/api-credentials?project=' + encodeURIComponent(pid))
    credentials.value = d.items || []
  } catch { /* 凭据列表不可读：HTTP 节点认证下拉为空，校验由服务端负责 */ }
}, { immediate: true })

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
  selectedId.value = fresh.id
  nextTick(() => canvasRef.value?.sync())
}
async function deleteNode(id: string) {
  if (id === INPUT_NODE || id === OUTPUT_NODE) return warn('编排输入/编排输出是边界节点，不可删除')
  const target = props.state.nodes.find((n: any) => n.id === id)
  const impact = nodeRemovalImpact(props.state, id)
  const message = impact.length
    ? `删除节点「${target?.name || id}」？\n受影响的引用：\n${impact.join('\n')}\n\n删除后这些绑定将变为未绑定（不会静默改绑到其他来源）。`
    : `删除节点「${target?.name || id}」？`
  if (!(await appConfirm({ message }))) return
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
  chainIds.value = chainIds.value.filter(x => x !== id)
  emit('changed')
  if (selectedId.value === id) selectedId.value = ''
  nextTick(() => canvasRef.value?.sync())
}

// --- 画布连线 → 绑定对话框（取消不产生绑定，A4） ---
const linkDialog = ref<{ sourceId: string; targetId: string; inputs: any[]; chosenInputId: string; shadow: any } | null>(null)
function openLinkDialog(payload: { sourceId: string; targetId: string }) {
  const target = props.state.nodes.find((n: any) => n.id === payload.targetId)
  const source = props.state.nodes.find((n: any) => n.id === payload.sourceId)
  if (!target || !source) return
  if (!target.inputs.length) return warn(`「${target.name}」尚未声明输入参数，请先在右侧配置中添加输入`)
  const firstUnbound = target.inputs.find((i: any) => !i.source)
  linkDialog.value = {
    sourceId: payload.sourceId, targetId: payload.targetId, inputs: target.inputs,
    chosenInputId: (firstUnbound || target.inputs[0]).id,
    shadow: null,
  }
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
function closeLinkDialog() { linkDialog.value = null }

// --- 删除连线：定位受影响绑定并确认 ---
async function handleUnlink(edgeId: string) {
  if (edgeId.startsWith('dep:')) {
    const targetNodeId = edgeId.slice(4).split(':')[0]
    const target = props.state.nodes.find((n: any) => n.id === targetNodeId)
    const input = target?.inputs?.find((i: any) => i.id === edgeId.slice(4).split(':')[1])
    if (!input) return
    const siblings = (target.inputs || []).filter((i: any) => i.source?.nodeId === input.source?.nodeId && i.source?.kind !== 'fixed' && i.source?.kind !== 'flowInput')
    const context = siblings.length > 1 ? `\n（这两个节点之间共 ${siblings.length} 条绑定，本次只删除下面这一条）` : ''
    if (!(await appConfirm({ message: `删除这条绑定？\n节点「${target.name}」的输入「${input.label || input.name}」← ${sourceSummary(props.state, input.source)}${context}`, danger: true }))) return
    emit('before-change')
    input.source = null
    emit('changed')
    nextTick(() => canvasRef.value?.sync())
  } else if (edgeId.startsWith('out:')) {
    const out = props.state.outputs.find((o: any) => o.id === edgeId.slice(4))
    if (!out || !out.binding) return
    if (!(await appConfirm({ message: `删除编排输出「${out.label || out.name}」的来源绑定？`, danger: true }))) return
    emit('before-change')
    out.binding = null
    emit('changed')
    nextTick(() => canvasRef.value?.sync())
  }
}

// --- 配置检查（只读，不保存、不执行） ---
const checking = ref(false)
async function runCheck() {
  checking.value = true
  try { emit('update:check', await checkFlow(clone(props.state), props.projectConnections || [])) }
  catch (e: any) { warn('配置检查请求失败：' + (e?.message || e)) }
  finally { checking.value = false }
}
function locateItem(item: any) {
  if (item.kind === 'node') select(item.id)
  else if (item.kind === 'flow-input') select(INPUT_NODE)
  else if (item.kind === 'flow-output') select(OUTPUT_NODE)
  else if (item.kind === 'connection') warn(`逻辑连接问题：${item.name}`)
  emit('locate', item)
}

// --- 运行 / 测试（/api/flow-run） ------------------------------------------------
const runResults = ref<Record<string, any>>({})
const runSummary = ref<{ status: string; text: string } | null>(null)
const running = ref(false)
const entryDialog = ref(false)
const entryValues = ref<Record<string, any>>({})
const testDialog = ref<{ order: string[]; rows: any[]; values: Record<string, any>; single: boolean } | null>(null)
const outView = ref<{ title: string; entry: any; tab: string } | null>(null)

const entryFields = computed(() => (props.state.inputs || []).map((i: any) => ({
  key: i.name || i.id, label: i.label || i.name || i.id, type: i.type || { type: 'text' } })))

function buildValue(field: any, raw: any): any {
  const t = field.type?.type
  if (t === 'number') return raw === '' || raw == null ? null : Number(raw)
  if (t === 'boolean') return raw === true || raw === 'true'
  if (t === 'object' || t === 'list') return raw === '' ? null : JSON.parse(String(raw))
  return raw
}
function openRunAll() {
  if (running.value) return
  entryValues.value = {}
  entryDialog.value = true
}
async function submitRunAll() {
  const inputs: Record<string, any> = {}
  for (const field of entryFields.value) {
    try { inputs[field.key] = buildValue(field, entryValues.value[field.key]) }
    catch { return warn(`入口参数「${field.label}」不是合法 JSON`) }
  }
  entryDialog.value = false
  await execute({ projectId: props.projectId || undefined, state: clone(props.state), revision: props.revision, inputs })
}
function toggleChainMode() {
  chainMode.value = !chainMode.value
  if (!chainMode.value) chainIds.value = []
}
const chainMode = ref(false)
const chainIds = ref<string[]>([])
function onChainTap(id: string) {
  if (chainIds.value.includes(id)) { chainIds.value = chainIds.value.filter(x => x !== id); return }
  const probe = chainOrder(props.state, [...chainIds.value, id])
  if (probe.missing.length) return warn('不构成连续链：' + probe.missing.join('；'))
  chainIds.value = probe.order
}
function testSelected() {
  if (!chainIds.value.length || running.value) return
  const { order, missing } = chainOrder(props.state, chainIds.value)
  if (missing.length) return warn('不构成连续链：' + missing.join('；'))
  openTestForm(order)
}
function testSingleNode() {
  if (!selectedId.value || running.value || selectedId.value === INPUT_NODE || selectedId.value === OUTPUT_NODE) return
  openTestForm([selectedId.value])
}
function openTestForm(order: string[]) {
  const rows = chainExternalInputs(props.state, order)
  const values: Record<string, any> = {}
  for (const row of rows) if (row.fixedValue !== undefined) values[row.key] = row.fixedValue
  testDialog.value = { order, rows, values, single: order.length === 1 }
}
async function submitTest() {
  const dialog = testDialog.value
  if (!dialog) return
  const inputs: Record<string, any> = {}
  for (const row of dialog.rows) {
    if (row.fixedValue !== undefined) { inputs[row.key] = row.fixedValue; continue }
    const field = { key: row.key, label: row.input.label || row.input.name, type: row.input.type || { type: 'text' } }
    try { inputs[row.key] = buildValue(field, dialog.values[row.key]) }
    catch { return warn(`「${row.nodeName} · ${field.label}」不是合法 JSON`) }
    if (inputs[row.key] == null && !row.input.source) return warn(`「${row.nodeName} · ${field.label}」未绑定来源，测试必须给值`)
  }
  testDialog.value = null
  await execute({ projectId: props.projectId || undefined, state: clone(props.state), targets: dialog.order, inputs })
}
async function execute(payload: any) {
  running.value = true
  runSummary.value = { status: 'running', text: '运行中…（基于提交时的草稿快照）' }
  const scope: string[] = payload.targets || processingNodes(props.state).map((n: any) => n.id)
  for (const id of scope) runResults.value[id] = { status: 'running' }
  try {
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
    runSummary.value = {
      status: d.status,
      text: `运行${d.status === 'success' ? '成功' : '失败'}：${ok} 成功 · ${bad.length} 失败 · ${skip} 跳过（${d.durationMs ?? 0}ms）`,
    }
    if (bad.length) {
      const first = bad[0]
      select(first.nodeId)
      warn(`「${(props.state.nodes.find((n: any) => n.id === first.nodeId) || {}).name || first.nodeId}」执行失败：${first.error}`)
    }
  } catch (e: any) {
    for (const id of scope) if (runResults.value[id]?.status === 'running') delete runResults.value[id]
    runSummary.value = { status: 'error', text: '运行请求失败：' + (e?.message || e) }
  } finally { running.value = false }
}
function clearRun() {
  runResults.value = {}
  runSummary.value = null
}
const runResultOfSelected = computed(() => runResults.value[selectedId.value])
function openOutput(id: string) {
  const entry = runResults.value[id]
  if (!entry) return
  const name = (props.state.nodes.find((n: any) => n.id === id) || {}).name || id
  outView.value = { title: `输出检查器 · ${name}`, entry, tab: entry.status === 'failed' ? 'log' : 'out' }
}
function outJson(): string {
  const entry = outView.value?.entry
  if (!entry) return ''
  if (entry.status === 'failed') return '错误：' + (entry.error || '')
  return JSON.stringify(entry.outputs ?? null, null, 2)
}
function copyOut() { navigator.clipboard?.writeText(outJson()) }
</script>
<template>
<div class="flow-editor">
  <div class="flow-head">
    <label class="flow-name">名称 *<input :value="state.name" :aria-label="'编排名称'" @input="emit('before-change');state.name=($event.target as HTMLInputElement).value;emit('changed')"/></label>
    <label class="flow-desc">说明<textarea :value="state.description" rows="1" :aria-label="'编排说明'" @input="emit('before-change');state.description=($event.target as HTMLTextAreaElement).value;emit('changed')"/></label>
    <div class="flow-head-actions">
      <button class="primary" :disabled="checking||running" @click="openRunAll">{{running?'运行中…':'▶ 运行全部'}}</button>
      <button :class="{active:chainMode}" :disabled="running" :title="'点选连续节点后做链测试'" @click="toggleChainMode">⛓ 链选择{{chainIds.length?`（${chainIds.length}）`:''}}</button>
      <button :disabled="running||!chainIds.length" @click="testSelected">测试选中</button>
      <button class="primary" :disabled="checking||running" @click="runCheck">{{checking?'检查中…':'检查配置'}}</button>
      <button v-if="runSummary||Object.keys(runResults).length" :disabled="running" @click="clearRun">清除状态</button>
      <span v-if="runSummary" :class="runSummary.status==='running'?'muted':(runSummary.status==='success'?'inline-success':'inline-error')">{{runSummary.text}}</span>
      <span v-else-if="check" :class="check.errors.length?'inline-error':(check.warnings.length?'inline-warning':'inline-success')">
        {{check.errors.length?`发现 ${check.errors.length} 个配置问题`:(check.warnings.length?`已检查：${check.warnings.length} 项待完善`:'配置检查通过')}}
      </span>
      <span v-else class="muted">尚未检查</span>
    </div>
  </div>
  <p v-if="notice" class="property-feedback" role="status">{{notice}}</p>
  <div class="flow-workspace">
    <aside class="flow-side">
      <div class="flow-node-list">
        <button v-for="item in nodeList" :key="item.id" class="flow-node-item" :class="{active:selectedId===item.id,boundary:item.fixed,chain:chainIds.includes(item.id)}" @click="chainMode&&!item.fixed?onChainTap(item.id):select(item.id)">
          <strong>{{item.name}}</strong><small>{{item.kindLabel}}</small>
        </button>
      </div>
      <div class="flow-side-actions">
        <button class="primary" @click="addNode('python')">＋ Python 节点</button>
        <button class="primary" @click="addNode('sql')">＋ SQL 节点</button>
        <button class="primary" @click="addNode('calc')">＋ 计算节点</button>
        <button class="primary" @click="addNode('redis')">＋ Redis 节点</button>
        <button class="primary" @click="addNode('http')">＋ HTTP 节点</button>
        <button v-if="selectedId&&!chainMode" class="primary" :disabled="running||selectedId===INPUT_NODE||selectedId===OUTPUT_NODE" @click="testSingleNode">▶ 测试选中节点</button>
        <button v-if="selectedId&&selectedId!==INPUT_NODE&&selectedId!==OUTPUT_NODE" class="danger" @click="deleteNode(selectedId)">删除选中节点</button>
      </div>
    </aside>
    <FlowCanvas ref="canvasRef" :state="state" :results="runResults" :chain-ids="chainIds" :chain-mode="chainMode" @before-change="emit('before-change')" @changed="emit('changed')" @select="select" @chain-tap="onChainTap" @link="openLinkDialog" @unlink="handleUnlink"/>
    <div class="flow-detail">
      <div v-if="runResultOfSelected" class="run-strip">
        <span :class="runResultOfSelected.status==='success'?'inline-success':(runResultOfSelected.status==='failed'?'inline-error':'muted')">
          {{runResultOfSelected.status==='success'?`本次运行成功（${runResultOfSelected.durationMs ?? 0}ms）`:runResultOfSelected.status==='failed'?'本次运行失败':runResultOfSelected.status==='running'?'运行中…':'已跳过'}}
        </span>
        <button class="mini" @click="openOutput(selectedId)">{{runResultOfSelected.status==='failed'?'查看错误':'查看输出'}}</button>
      </div>
      <template v-if="selectedKind==='python'||selectedKind==='sql'||selectedKind==='redis'||selectedKind==='http'||selectedKind==='calc'">
        <NodeConfig v-if="node" :state="state" :node="node" :connections="projectConnections || []" :credentials="credentials" :providers="providers" @before-change="emit('before-change')" @changed="emit('changed')" @test="testSingleNode"/>
      </template>
      <BoundaryConfig v-else-if="selectedKind==='input'" :state="state" kind="input" @before-change="emit('before-change')" @changed="emit('changed')"/>
      <BoundaryConfig v-else-if="selectedKind==='output'" :state="state" kind="output" @before-change="emit('before-change')" @changed="emit('changed')"/>
      <p v-else class="empty">在左侧选择节点，或从画布点击节点查看配置。</p>
      <section v-if="check && (check.errors.length||check.warnings.length)" class="flow-check">
        <h3>配置检查结果</h3>
        <div v-for="item in check.items" :key="item.kind+item.id" class="issue-row" :class="item.level==='error'?'error':'warn'">
          <span><strong>{{item.name}}</strong> · {{item.issues.join('；')}}</span>
          <button class="go-fix" @click="locateItem(item)">去处理</button>
        </div>
      </section>
    </div>
  </div>

  <div v-if="linkDialog" class="modal-backdrop" @click.self="closeLinkDialog">
    <section class="modal-card" role="dialog" aria-modal="true" aria-label="建立输入绑定">
      <h2>绑定节点输入</h2>
      <p class="field-help">从画布连线发起。选择目标输入与来源；取消不会产生任何绑定。</p>
      <label>目标输入
        <AppSelect v-model="linkDialog.chosenInputId" :options="linkDialog.inputs.map((i:any)=>({value:i.id,label:`${i.label||i.name||'未命名'}（${i.name}）${i.source?' · 已有来源':''}`}))" aria-label="目标输入"/>
      </label>
      <BindingEditor v-if="linkShadowOwner" :state="state" :owner="linkShadowOwner" dialog @apply="applyLinkDialog" @close="closeLinkDialog"/>
    </section>
  </div>

  <div v-if="entryDialog" class="modal-backdrop" @click.self="entryDialog=false">
    <section class="modal-card" role="dialog" aria-modal="true" aria-label="填写编排入口参数">
      <h2>填写编排入口参数</h2>
      <p class="field-help">入口参数即函数入参；运行前配置检查错误必须为 0（服务端校验）。对象/列表参数请输入 JSON。</p>
      <label v-for="field in entryFields" :key="field.key">{{field.label}} · {{field.type.type}}
        <select v-if="field.type.type==='boolean'" v-model="entryValues[field.key]"><option value="true">是</option><option value="false">否</option></select>
        <textarea v-else-if="field.type.type==='object'||field.type.type==='list'" v-model="entryValues[field.key]" rows="3" placeholder="{}"/>
        <input v-else v-model="entryValues[field.key]" :type="field.type.type==='number'?'number':(field.type.type==='datetime'?'datetime-local':'text')"/>
      </label>
      <p v-if="!entryFields.length" class="muted">此编排没有声明入口参数。</p>
      <div class="dialogtools">
        <button @click="entryDialog=false">取消</button>
        <button class="primary" :disabled="running" @click="submitRunAll">开始运行</button>
      </div>
    </section>
  </div>

  <div v-if="testDialog" class="modal-backdrop" @click.self="testDialog=null">
    <section class="modal-card" role="dialog" aria-modal="true" aria-label="节点测试">
      <h2>{{testDialog.single?'节点测试':'链测试'}} · {{testDialog.order.length}} 个节点</h2>
      <p v-if="!testDialog.single" class="field-help">执行顺序：{{testDialog.order.map(id=>(state.nodes.find((n:any)=>n.id===id)||{}).name||id).join(' → ')}}</p>
      <p class="field-help">按声明类型填写全部输入；测试不落盘、不影响草稿。</p>
      <label v-for="row in testDialog.rows" :key="row.key">{{row.nodeName}} · {{row.input.label || row.input.name}} · {{row.input.type?.type}}{{row.input.source?'':'（未绑定，必填）'}}
        <select v-if="row.input.type?.type==='boolean'" v-model="testDialog.values[row.key]"><option value="true">是</option><option value="false">否</option></select>
        <textarea v-else-if="row.input.type?.type==='object'||row.input.type?.type==='list'" v-model="testDialog.values[row.key]" rows="3" placeholder="{}"/>
        <input v-else v-model="testDialog.values[row.key]" :type="row.input.type?.type==='number'?'number':(row.input.type?.type==='datetime'?'datetime-local':'text')"/>
      </label>
      <p v-if="!testDialog.rows.length" class="muted">该节点/链没有外部输入，直接开始测试。</p>
      <div class="dialogtools">
        <button @click="testDialog=null">取消</button>
        <button class="primary" :disabled="running" @click="submitTest">开始测试</button>
      </div>
    </section>
  </div>

  <div v-if="outView" class="modal-backdrop" @click.self="outView=null">
    <section class="modal-card output-viewer" role="dialog" aria-modal="true" aria-label="输出检查器">
      <h2>{{outView.title}}</h2>
      <p class="field-help">结果仅随本次运行返回，不持久化。LLM 节点的日志保留请求与响应摘要，供核查非确定性结果。</p>
      <div class="viewer-tabs">
        <button :class="{active:outView.tab==='out'}" @click="outView.tab='out'">输出</button>
        <button :class="{active:outView.tab==='log'}" @click="outView.tab='log'">日志</button>
      </div>
      <pre v-if="outView.tab==='out'" class="viewer-json">{{outJson()}}</pre>
      <pre v-else class="viewer-logs">{{(outView.entry.logs||[]).join('\n')||'（无日志）'}}</pre>
      <div class="dialogtools">
        <button class="mini" @click="copyOut">复制 JSON</button>
        <button @click="outView=null">关闭</button>
      </div>
    </section>
  </div>
</div>
</template>
<style scoped>
.flow-editor{display:flex;flex-direction:column;gap:12px;min-height:0}
.flow-head{display:flex;gap:14px;align-items:end;flex-wrap:wrap;background:var(--paper);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.flow-head label{flex:1;margin:0}
.flow-head-actions{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.flow-head-actions button.active{background:var(--blue-soft);border-color:var(--blue-line);color:var(--blue-ink)}
.flow-workspace{display:flex;gap:12px;height:calc(100vh - 320px);min-height:480px}
.flow-side{flex:0 0 190px;display:flex;flex-direction:column;gap:8px;background:var(--paper);border:1px solid var(--line);border-radius:10px;padding:10px;overflow:auto}
.flow-node-list{display:flex;flex-direction:column;gap:3px}
.flow-node-item{display:block;width:100%;text-align:left;border:1px solid transparent;border-radius:7px;padding:9px 10px;background:transparent}
.flow-node-item:hover{background:var(--paper-2)}
.flow-node-item.active{background:var(--blue-soft);border-color:var(--blue-line);color:var(--blue-ink)}
.flow-node-item.chain{border-style:dashed;border-color:var(--teal,#2b9db0);border-width:2px}
.flow-node-item strong{display:block;font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.flow-node-item small{display:block;color:var(--muted);font-size:11px;margin-top:2px}
.flow-node-item.boundary{border-style:dashed}
.flow-side-actions{display:flex;flex-direction:column;gap:6px;padding-top:8px;border-top:1px solid var(--line)}
.flow-detail{flex:0 0 400px;overflow:auto;background:var(--paper);border:1px solid var(--line);border-radius:10px;padding:16px 18px}
.flow-check{border-top:1px solid var(--line);margin-top:18px;padding-top:12px}
.flow-check h3{margin:0 0 8px}
.run-strip{display:flex;align-items:center;gap:10px;margin-bottom:12px}
.viewer-json,.viewer-logs{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12px;background:var(--paper-2);border-radius:8px;padding:10px 12px;white-space:pre;overflow:auto;max-height:300px}
.viewer-logs{background:#20242e;color:#c8d0e0;white-space:pre-wrap}
.viewer-tabs{display:flex;gap:4px;margin:10px 0 8px}
.viewer-tabs button{font-size:12px;padding:3px 12px;border-radius:6px}
.viewer-tabs button.active{background:var(--blue-soft);border-color:var(--blue-line);color:var(--blue-ink)}
@media(max-width:1100px){.flow-workspace{flex-wrap:wrap;height:auto}.flow-detail{flex:1 1 100%;max-height:60vh}.flow-canvas{height:420px}}
</style>
