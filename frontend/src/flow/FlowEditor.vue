<!-- FlowEditor — 函数编排编辑页（一期）：顶部（名称/说明/检查配置）、左侧节点列表 +
     逻辑连接管理、中间派生连线画布、右侧当前节点配置。保存状态与撤销由 App 顶栏承担
     （flow Saver + inject('commit-now')），本页只 emit before-change/changed。
     连线对话框取消不产生绑定；删除连线/节点先确认并说明受影响引用。 -->
<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import FlowCanvas from './FlowCanvas.vue'
import NodeConfig from './NodeConfig.vue'
import BoundaryConfig from './BoundaryConfig.vue'
import BindingEditor from './BindingEditor.vue'
import AppSelect from '../shared/AppSelect.vue'
import { INPUT_NODE, NODE_KIND_LABELS, OUTPUT_NODE, defaultPosition, nodeRemovalImpact, outputCandidates, processingNodes, sourceSummary, uid, clone } from './flowModel'
import { checkFlow } from './api'
const props = defineProps<{ state: any; check: any; projectConnections?: any[] }>()
const emit = defineEmits(['before-change', 'changed', 'update:check', 'locate'])
const selectedId = ref('')
const canvasRef = ref<any>(null)
const notice = ref('')
let noticeTimer: any = null
function warn(text: string) { notice.value = text; clearTimeout(noticeTimer); noticeTimer = setTimeout(() => notice.value = '', 5000) }
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

function addNode(kind: 'python' | 'sql' | 'redis') {
  emit('before-change')
  const fresh = { id: uid(), kind, name: kind === 'python' ? 'Python 处理' : kind === 'sql' ? 'SQL 查询' : 'Redis 取数', description: '', inputs: [], outputs: [],
    implementation: kind === 'python' ? { language: 'python', code: 'def main():\n    # 在此编写处理逻辑；本工作台只保存配置，不执行代码\n    return None\n' }
      : kind === 'sql' ? { language: 'sql', sql: '', connectionId: '' }
      : { language: 'redis', connectionId: '', keyTemplate: '' } }
  props.state.nodes.push(fresh)
  props.state.layout = props.state.layout || { positions: {}, zoom: 1, pan: { x: 0, y: 0 } }
  props.state.layout.positions = props.state.layout.positions || {}
  const position = defaultPosition(props.state)
  props.state.layout.positions[fresh.id] = position
  emit('changed')
  selectedId.value = fresh.id
  nextTick(() => canvasRef.value?.sync())
}
function deleteNode(id: string) {
  if (id === INPUT_NODE || id === OUTPUT_NODE) return warn('编排输入/编排输出是边界节点，不可删除')
  const target = props.state.nodes.find((n: any) => n.id === id)
  const impact = nodeRemovalImpact(props.state, id)
  const message = impact.length
    ? `删除节点「${target?.name || id}」？\n受影响的引用：\n${impact.join('\n')}\n\n删除后这些绑定将变为未绑定（不会静默改绑到其他来源）。`
    : `删除节点「${target?.name || id}」？`
  if (!confirm(message)) return
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
  if (selectedId.value === id) selectedId.value = ''
  nextTick(() => canvasRef.value?.sync())
}

// 逻辑连接管理已内嵌到 SQL 节点配置面板（NodeConfig），编排级 connections 数组保持共享数据源不变。

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
  // 防御：不完整的来源（如缺输出引用）不落盘，交给配置检查定位
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

// --- 删除连线：定位受影响绑定并确认（同两节点多绑定时逐条明确选择，不全部清除） ---
function handleUnlink(edgeId: string) {
  if (edgeId.startsWith('dep:')) {
    const targetNodeId = edgeId.slice(4).split(':')[0]
    const target = props.state.nodes.find((n: any) => n.id === targetNodeId)
    const input = target?.inputs?.find((i: any) => i.id === edgeId.slice(4).split(':')[1])
    if (!input) return
    const siblings = (target.inputs || []).filter((i: any) => i.source?.nodeId === input.source?.nodeId && i.source?.kind !== 'fixed' && i.source?.kind !== 'flowInput')
    const context = siblings.length > 1 ? `\n（这两个节点之间共 ${siblings.length} 条绑定，本次只删除下面这一条）` : ''
    if (!confirm(`删除这条绑定？\n节点「${target.name}」的输入「${input.label || input.name}」← ${sourceSummary(props.state, input.source)}${context}`)) return
    emit('before-change')
    input.source = null
    emit('changed')
    nextTick(() => canvasRef.value?.sync())
  } else if (edgeId.startsWith('out:')) {
    const out = props.state.outputs.find((o: any) => o.id === edgeId.slice(4))
    if (!out || !out.binding) return
    if (!confirm(`删除编排输出「${out.label || out.name}」的来源绑定？`)) return
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
</script>
<template>
<div class="flow-editor">
  <div class="flow-head">
    <label class="flow-name">名称 *<input :value="state.name" :aria-label="'编排名称'" @input="emit('before-change');state.name=($event.target as HTMLInputElement).value;emit('changed')"/></label>
    <label class="flow-desc">说明<textarea :value="state.description" rows="1" :aria-label="'编排说明'" @input="emit('before-change');state.description=($event.target as HTMLTextAreaElement).value;emit('changed')"/></label>
    <div class="flow-head-actions">
      <button class="primary" :disabled="checking" @click="runCheck">{{checking?'检查中…':'检查配置'}}</button>
      <span v-if="check" :class="check.errors.length?'inline-error':(check.warnings.length?'inline-warning':'inline-success')">
        {{check.errors.length?`发现 ${check.errors.length} 个配置问题`:(check.warnings.length?`已检查：${check.warnings.length} 项待完善`:'配置检查通过（未执行验证）')}}
      </span>
      <span v-else class="muted">尚未检查</span>
    </div>
  </div>
  <p v-if="notice" class="property-feedback" role="status">{{notice}}</p>
  <div class="flow-workspace">
    <aside class="flow-side">
      <div class="flow-node-list">
        <button v-for="item in nodeList" :key="item.id" class="flow-node-item" :class="{active:selectedId===item.id,boundary:item.fixed}" @click="select(item.id)">
          <strong>{{item.name}}</strong><small>{{item.kindLabel}}</small>
        </button>
      </div>
      <div class="flow-side-actions">
        <button class="primary" @click="addNode('python')">＋ Python 节点</button>
        <button class="primary" @click="addNode('sql')">＋ SQL 节点</button>
        <button class="primary" @click="addNode('redis')">＋ Redis 节点</button>
        <button v-if="selectedId&&selectedId!==INPUT_NODE&&selectedId!==OUTPUT_NODE" class="danger" @click="deleteNode(selectedId)">删除选中节点</button>
      </div>
    </aside>
    <FlowCanvas ref="canvasRef" :state="state" @before-change="emit('before-change')" @changed="emit('changed')" @select="select" @link="openLinkDialog" @unlink="handleUnlink"/>
    <div class="flow-detail">
      <template v-if="selectedKind==='python'||selectedKind==='sql'||selectedKind==='redis'">
        <NodeConfig v-if="node" :state="state" :node="node" :connections="projectConnections || []" @before-change="emit('before-change')" @changed="emit('changed')"/>
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
</div>
</template>
<style scoped>
.flow-editor{display:flex;flex-direction:column;gap:12px;min-height:0}
.flow-head{display:flex;gap:14px;align-items:end;flex-wrap:wrap;background:var(--paper);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.flow-head label{flex:1;margin:0}
.flow-head-actions{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.flow-workspace{display:flex;gap:12px;height:calc(100vh - 320px);min-height:480px}
.flow-side{flex:0 0 190px;display:flex;flex-direction:column;gap:8px;background:var(--paper);border:1px solid var(--line);border-radius:10px;padding:10px;overflow:auto}
.flow-node-list{display:flex;flex-direction:column;gap:3px}
.flow-node-item{display:block;width:100%;text-align:left;border:1px solid transparent;border-radius:7px;padding:9px 10px;background:transparent}
.flow-node-item:hover{background:var(--paper-2)}
.flow-node-item.active{background:var(--blue-soft);border-color:var(--blue-line);color:var(--blue-ink)}
.flow-node-item strong{display:block;font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.flow-node-item small{display:block;color:var(--muted);font-size:11px;margin-top:2px}
.flow-node-item.boundary{border-style:dashed}
.flow-side-actions{display:flex;flex-direction:column;gap:6px;padding-top:8px;border-top:1px solid var(--line)}
.flow-detail{flex:0 0 400px;overflow:auto;background:var(--paper);border:1px solid var(--line);border-radius:10px;padding:16px 18px}
.flow-check{border-top:1px solid var(--line);margin-top:18px;padding-top:12px}
.flow-check h3{margin:0 0 8px}
@media(max-width:1100px){.flow-workspace{flex-wrap:wrap;height:auto}.flow-detail{flex:1 1 100%;max-height:60vh}.flow-canvas{height:420px}}
</style>
