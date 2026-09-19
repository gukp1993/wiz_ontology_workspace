<!-- FlowTestWorkspace — 编排独立测试视图（20260919_函数编排配置与调试优化）。
     替换主工作区中的画布与节点详情：左输入（范围/片段起止/外部输入/入口值/执行）、
     右结果（范围与状态、节点切换、输出/本节点输入/执行记录）。长内容各自滚动。
     三种范围：整条（沿用 revision/全量校验/并发约束）、单节点与连续片段（testMode=isolated，
     稳定 ID inputOverrides，范围外节点不执行，预检失败零执行——契约 04 §3.1）。
     结果关联提交快照（配置签名/项目/范围/输入签名/请求代次）：配置或项目变化标旧结果，
     只改输入/范围标「尚未按当前输入/范围运行」；晚回包只写入发起时的运行记录。
     本组件只读不保存：不 emit before-change/changed；测试输入与结果仅在页面内存。 -->
<script setup lang="ts">
import { computed, ref } from 'vue'
import { appConfirm } from '../shared/appConfirm'
import AppSelect from '../shared/AppSelect.vue'
import { NODE_KIND_LABELS, TYPE_LABELS, clone, configSignature, dependencyPaths, nameOfFlowNode, processingNodes, testInputSignature, testScopePlan, validateTestValue, writeCapabilities } from './flowModel'
import { runFlow } from './api'

const props = defineProps<{ state: any; projectId?: string; projectName?: string; revision?: string }>()

// ── 范围选择（返回设计后保留） ────────────────────────────────────────────────
const scopeKind = ref<'all' | 'single' | 'segment'>('segment')
const singleId = ref('')
const segStart = ref('')
const segEnd = ref('')
const explicitIds = ref<string[]>([]) // 多路径时的显式选集（复用已有多选交互）

const procNodes = computed(() => processingNodes(props.state))
const nameOf = (id: string) => nameOfFlowNode(props.state, id)
const nodeOptions = computed(() => procNodes.value.map((n: any) => ({ value: n.id, label: n.name || n.id })))

const segPaths = computed<string[][]>(() => {
  if (scopeKind.value !== 'segment' || !segStart.value || !segEnd.value) return []
  return dependencyPaths(props.state, segStart.value, segEnd.value)
})
function onSegChange() { explicitIds.value = [] }
// 范围下拉（原型形态）：整条 / 连续片段 / 单节点·各节点（单节点并入同一下拉）
const scopeValue = computed(() => scopeKind.value === 'single' ? 'single:' + singleId.value : scopeKind.value)
const scopeOptions = computed(() => [
  { value: 'all', label: '整条编排' },
  { value: 'segment', label: '连续片段' },
  ...procNodes.value.map((n: any) => ({ value: 'single:' + n.id, label: '单节点 · ' + (n.name || n.id) })),
])
function onScopeChange(v: string) {
  if (v === 'all' || v === 'segment') { scopeKind.value = v; singleId.value = ''; onSegChange(); return }
  if (v.startsWith('single:')) { scopeKind.value = 'single'; singleId.value = v.slice(7); onSegChange() }
}
const segRangeIds = computed<string[]>(() => {
  if (scopeKind.value !== 'segment') return []
  if (explicitIds.value.length) return [...explicitIds.value]
  if (!segStart.value || !segEnd.value) return []
  const paths = segPaths.value
  if (!paths.length) return []
  if (paths.length > 1) return [] // 多路径不猜测：等待显式选集
  return paths[0]
})
const plan = computed(() => {
  const targets = scopeKind.value === 'all' ? procNodes.value.map((n: any) => n.id)
    : scopeKind.value === 'single' ? (singleId.value ? [singleId.value] : [])
    : segRangeIds.value
  return testScopePlan(props.state, targets)
})
const planError = computed(() => {
  if (scopeKind.value === 'segment' && !explicitIds.value.length && segStart.value && segEnd.value) {
    const paths = segPaths.value
    if (!paths.length) return `「${nameOf(segStart.value)}」到「${nameOf(segEnd.value)}」没有沿依赖方向的路径（起点必须在终点的上游）`
    if (paths.length > 1) return '存在多条路径，请明确选择节点'
  }
  return plan.value.error
})
function toggleExplicit(id: string) {
  explicitIds.value = explicitIds.value.includes(id) ? explicitIds.value.filter(x => x !== id) : [...explicitIds.value, id]
}
const scopeText = computed(() => {
  if (scopeKind.value === 'all') return '整条编排'
  const ids = plan.value.order
  if (!ids.length) return '未选择范围'
  if (scopeKind.value === 'single') return `单节点 · ${nameOf(ids[0])}`
  return `连续片段（${ids.length} 个节点）`
})

// ── 测试输入（稳定 ID 覆盖；0/false/空文本有效；固定值只读摘要） ─────────────────
const overrideTexts = ref<Record<string, string>>({}) // key = `${nodeId}\n${inputId}`（稳定 ID）
const entryTexts = ref<Record<string, string>>({}) // key = 入口参数稳定 id
const overrideKey = (nodeId: string, inputId: string) => nodeId + '\u0000' + inputId
const fixedRows = computed(() => {
  const rows: { nodeName: string; label: string; display: string }[] = []
  for (const id of plan.value.order) {
    const node = procNodes.value.find((n: any) => n.id === id)
    for (const input of node?.inputs || []) {
      if (input.source?.kind === 'fixed') {
        const v = input.source.value
        rows.push({ nodeName: node.name, label: input.label || input.name, display: input.source.valueType === 'boolean' ? (v ? '是（true）' : '否（false）') : input.source.value === '' ? '空字符串 ""' : String(v) })
      }
    }
  }
  return rows
})
const outsideNote = computed(() => plan.value.excluded.length ? `本次不执行：${plan.value.excluded.map(n => n.name).join('、')}（范围外节点）` : '')
function sourceHint(item: { nodeId: string; inputId: string; kind: string }): string {
  const node = procNodes.value.find((n: any) => n.id === item.nodeId)
  const input = (node?.inputs || []).find((i: any) => i.id === item.inputId)
  const src = input?.source
  if (src && (src.kind === 'node' || src.kind === 'nodeField')) {
    const upstream = procNodes.value.find((n: any) => n.id === src.nodeId)
    return `原来自「${upstream?.name || '已失效节点'}」的输出，本次手工提供`
  }
  return '此输入未绑定来源；测试值不修复正式绑定'
}

// ── 运行与结果（快照 + 请求代次；晚回包只写入发起时记录） ─────────────────────────
const running = ref(false)
const runGen = ref(0)
const result = ref<null | { gen: number; status: 'running' | 'done' | 'request-error'; error?: string; payload?: any; snapshot: { sig: string; projectId: string; scopeKind: string; targets: string[]; scopeText: string; inputSig: string; submittedAt: string } }>(null)
const resultNode = ref('')
const resultTab = ref<'output' | 'inputs' | 'steps'>('output')
const formError = ref('')
const currentSig = computed(() => configSignature(props.state))
const currentInputSig = computed(() => testInputSignature(
  { kind: scopeKind.value, targets: plan.value.order },
  { o: overrideTexts.value, e: entryTexts.value }, {}))
const staleConfig = computed(() => !!result.value && result.value.status !== 'running' && result.value.snapshot.sig !== currentSig.value)
const staleProject = computed(() => !!result.value && result.value.status !== 'running' && (result.value.snapshot.projectId || '') !== (props.projectId || ''))
const staleInput = computed(() => !!result.value && result.value.status !== 'running' && !staleConfig.value && !staleProject.value && result.value.snapshot.inputSig !== currentInputSig.value)
const freshnessText = computed(() => {
  if (!result.value || result.value.status === 'running') return ''
  if (staleConfig.value) return '配置已修改 · 以下为上次配置的结果'
  if (staleProject.value) return '运行项目已切换 · 以下为上次项目的结果'
  if (staleInput.value) return '上次测试结果，尚未按当前输入/范围运行'
  return ''
})
const writes = computed(() => writeCapabilities(props.state, plan.value.order))

function parseOverrideValue(decl: any, raw: string): { ok: boolean; value: any; error: string } {
  return validateTestValue(decl, raw)
}

async function run() {
  if (running.value) return // 单请求等待态：不重复发起（F14）
  formError.value = ''
  const planNow = plan.value
  if (planError.value) { formError.value = planError.value; return }
  if (!planNow.order.length) { formError.value = '请先选择测试范围'; return }
  // 组装覆盖与入口值（后端仍做最终校验；这里先给可读的即时反馈）
  const overrides: Record<string, Record<string, any>> = {}
  for (const item of planNow.externalInputs) {
    const raw = overrideTexts.value[overrideKey(item.nodeId, item.inputId)] ?? ''
    if (item.required && (raw === '' || raw == null)) {
      formError.value = `请填写「${item.nodeName} · ${item.label}」的测试值（0 和 false 是有效值）`
      return
    }
    if (raw === '' || raw == null) continue
    const verdict = parseOverrideValue(item.type, raw)
    if (!verdict.ok) { formError.value = `「${item.nodeName} · ${item.label}」：${verdict.error}`; return }
    ;(overrides[item.nodeId] = overrides[item.nodeId] || {})[item.inputId] = verdict.value
  }
  const inputs: Record<string, any> = {}
  if (scopeKind.value === 'all') {
    for (const decl of props.state.inputs || []) {
      const raw = entryTexts.value[decl.id] ?? ''
      const verdict = validateTestValue(decl.type, raw)
      if (!verdict.ok) { formError.value = `入口参数「${decl.label || decl.name}」：${verdict.error}`; return }
      inputs[decl.id] = verdict.value
      if (decl.name) inputs[decl.name] = verdict.value
    }
  } else {
    for (const need of planNow.entryNeeds) {
      const raw = entryTexts.value[need.inputId] ?? ''
      const decl = (props.state.inputs || []).find((i: any) => i.id === need.inputId)
      const verdict = validateTestValue(decl?.type, raw)
      if (!verdict.ok || raw === '') { formError.value = `入口参数「${need.label}」：${verdict.ok && raw === '' ? '请填写本次入口值' : verdict.error}`; return }
      inputs[need.inputId] = verdict.value
      if (decl?.name) inputs[decl.name] = verdict.value
    }
  }
  if (writes.value.length && !(await appConfirm({
    message: `本次测试将对所选范围执行真实外部调用：\n${writes.value.join('\n')}\n\n范围外节点不会执行。继续？`,
    confirmLabel: '开始测试',
  }))) return

  const isAll = scopeKind.value === 'all'
  const payload: any = { state: clone(props.state), projectId: props.projectId || undefined, inputs }
  if (isAll) { payload.revision = props.revision }
  else { payload.testMode = 'isolated'; payload.targets = planNow.order; payload.inputOverrides = overrides }
  const gen = ++runGen.value
  const snapshot = { sig: currentSig.value, projectId: props.projectId || '', scopeKind: scopeKind.value,
    targets: [...planNow.order], scopeText: scopeText.value,
    inputSig: testInputSignature({ kind: scopeKind.value, targets: planNow.order }, { o: { ...overrideTexts.value }, e: { ...entryTexts.value } }, {}),
    submittedAt: new Date().toLocaleTimeString('zh-CN', { hour12: false }) }
  result.value = { gen, status: 'running', snapshot }
  resultNode.value = planNow.order[0] || ''
  resultTab.value = 'output'
  try {
    const d = await runFlow(payload)
    if (gen !== runGen.value) return // 晚回包：已有更新的运行，不覆盖
    result.value = { gen, status: 'done', payload: d, snapshot }
  } catch (e: any) {
    if (gen !== runGen.value) return
    result.value = { gen, status: 'request-error', error: e?.message || String(e), snapshot }
  }
}

// ── 结果展示 ─────────────────────────────────────────────────────────────────
const nodeResults = computed<any[]>(() => result.value?.status === 'done' ? (result.value.payload?.nodeResults || []) : [])
const currentNode = computed<any>(() => nodeResults.value.find((r: any) => r.nodeId === resultNode.value))
const statusLabel = computed(() => {
  if (!result.value) return ''
  if (result.value.status === 'running') return '执行中 / 等待响应…'
  if (result.value.status === 'request-error') return '请求失败'
  return result.value.payload?.status === 'success' ? '本次测试成功' : '本次测试失败'
})
const statusClass = computed(() => !result.value ? '' : result.value.status === 'running' ? 'run' : result.value.status === 'request-error' || result.value.payload?.status !== 'success' ? 'bad' : 'ok')
function pickResultNode(id: string) { resultNode.value = id; resultTab.value = 'output' }
function previewValue(value: any): string {
  try { return JSON.stringify(value, null, 2) } catch { return String(value) }
}
function tableRows(value: any): { keys: string[]; rows: any[]; truncated: boolean } | null {
  if (!Array.isArray(value)) return null
  const keys = [...new Set(value.flatMap((x: any) => (x && typeof x === 'object' && !Array.isArray(x)) ? Object.keys(x) : []))]
  return { keys, rows: value.slice(0, 100), truncated: value.length > 100 }
}
const typeText = (decl: any) => TYPE_LABELS[decl?.type] || decl?.type || '?'

// 由 FlowEditor 调用：从设计页头部（测试片段/测试编排）或节点详情（测试此节点）进入
function openScope(kind: 'all' | 'single' | 'segment', nodeId?: string) {
  scopeKind.value = kind
  if (kind === 'single' && nodeId) singleId.value = nodeId
  if (kind === 'segment' && (!segStart.value || !segEnd.value) && procNodes.value.length) {
    segStart.value = procNodes.value[0].id
    segEnd.value = procNodes.value[procNodes.value.length - 1].id
  }
  onSegChange()
}
defineExpose({ openScope })
</script>
<template>
<div class="ftw-root">
  <header class="ftw-head">
    <button class="quiet" @click="$emit('back')">← 返回编排</button>
    <h2>{{ state.name }} <span class="ftw-tag">测试调试</span></h2>
    <small class="ftw-sub">输入与结果并排展示；返回编排保留本次输入、范围与结果</small>
    <span class="push"></span>
    <span class="ftw-project">运行项目：{{ projectName || '未选择（连接与凭据按项目上下文解析）' }}</span>
  </header>
  <div class="ftw-grid">
    <!-- 左：范围与输入 -->
    <aside class="ftw-input" aria-label="测试输入">
      <h3>测试范围</h3>
      <AppSelect :model-value="scopeValue" :options="scopeOptions" aria-label="测试范围" @update:model-value="v => onScopeChange(v as string)"/>
      <template v-if="scopeKind === 'segment'">
        <template v-if="!explicitIds.length">
          <label class="ftw-field">起点（上游）
            <AppSelect :model-value="segStart" placeholder="选择起点节点" :options="nodeOptions" aria-label="测试起点" @update:model-value="v => { segStart = v as string; onSegChange() }"/>
          </label>
          <label class="ftw-field">终点（下游）
            <AppSelect :model-value="segEnd" placeholder="选择终点节点" :options="nodeOptions" aria-label="测试终点" @update:model-value="v => { segEnd = v as string; onSegChange() }"/>
          </label>
        </template>
        <p v-if="planError" class="ftw-error" role="alert">{{ planError }}</p>
        <div v-if="segPaths.length > 1 && !explicitIds.length" class="ftw-note">
          存在多条依赖路径，请勾选要测试的节点集合：
          <div class="ftw-checks">
            <label v-for="n in procNodes" :key="n.id" class="ftw-check">
              <input type="checkbox" :checked="explicitIds.includes(n.id)" @change="toggleExplicit(n.id)"/> {{ n.name }}
            </label>
          </div>
        </div>
        <div v-else-if="plan.order.length" class="ftw-path" aria-label="执行顺序">
          <span v-for="id in plan.order" :key="id">{{ nameOf(id) }}</span>
        </div>
        <button v-if="explicitIds.length" class="linklike" @click="onSegChange">改为起止选择</button>
      </template>
      <p v-if="outsideNote" class="ftw-outside">{{ outsideNote }}</p>

      <h3 class="ftw-sec">测试输入</h3>
      <p class="ftw-hint">{{ scopeKind === 'all' ? '填写编排入口参数，按依赖顺序执行全部处理节点。' : '只为范围外来源提供本次测试值；范围内上游输出自动传递，不允许覆盖。测试值不修改节点绑定。' }}</p>
      <label v-for="need in plan.entryNeeds" :key="need.inputId" class="ftw-field">入口参数 · {{ need.label }}
        <input :value="entryTexts[need.inputId] || ''" :aria-label="'入口参数 ' + need.label" placeholder="本次测试的入口值" @input="entryTexts[need.inputId] = ($event.target as HTMLInputElement).value"/>
      </label>
      <label v-for="decl in (scopeKind === 'all' ? (state.inputs || []) : [])" :key="decl.id" class="ftw-field">入口参数 · {{ decl.label || decl.name }} · {{ typeText(decl.type) }}
        <select v-if="decl.type?.type === 'boolean'" :value="entryTexts[decl.id] ?? ''" :aria-label="'入口参数 ' + (decl.label || decl.name)" @change="entryTexts[decl.id] = ($event.target as HTMLSelectElement).value">
          <option value="">（请选择）</option><option value="true">是</option><option value="false">否</option>
        </select>
        <textarea v-else-if="decl.type?.type === 'object' || decl.type?.type === 'list'" :value="entryTexts[decl.id] || ''" rows="3" :placeholder="decl.type?.type === 'object' ? '{}' : '[]'" :aria-label="'入口参数 ' + (decl.label || decl.name)" @input="entryTexts[decl.id] = ($event.target as HTMLTextAreaElement).value"/>
        <input v-else :value="entryTexts[decl.id] || ''" :aria-label="'入口参数 ' + (decl.label || decl.name)" placeholder="本次测试的入口值" @input="entryTexts[decl.id] = ($event.target as HTMLInputElement).value"/>
      </label>
      <div v-for="item in plan.externalInputs" :key="item.nodeId + '/' + item.inputId" class="ftw-ext">
        <label class="ftw-field">{{ item.nodeName }} · {{ item.label }} · {{ typeText(item.type) }}<small class="ftw-src">{{ sourceHint(item) }}{{ item.kind === 'unbound' ? '（可选）' : '' }}</small>
          <select v-if="item.type?.type === 'boolean'" :value="overrideTexts[overrideKey(item.nodeId, item.inputId)] ?? ''" :aria-label="item.nodeName + ' ' + item.label" @change="overrideTexts[overrideKey(item.nodeId, item.inputId)] = ($event.target as HTMLSelectElement).value">
            <option value="">（请选择）</option><option value="true">是</option><option value="false">否</option>
          </select>
          <textarea v-else-if="item.type?.type === 'object' || item.type?.type === 'list'" :value="overrideTexts[overrideKey(item.nodeId, item.inputId)] || ''" rows="3" :placeholder="item.type?.type === 'object' ? '{}' : '[]'" :aria-label="item.nodeName + ' ' + item.label" @input="overrideTexts[overrideKey(item.nodeId, item.inputId)] = ($event.target as HTMLTextAreaElement).value"/>
          <input v-else :value="overrideTexts[overrideKey(item.nodeId, item.inputId)] || ''" :aria-label="item.nodeName + ' ' + item.label" :placeholder="item.kind === 'unbound' ? '可选：为未绑定输入提供本次测试值' : '本次测试值'" @input="overrideTexts[overrideKey(item.nodeId, item.inputId)] = ($event.target as HTMLInputElement).value"/>
        </label>
      </div>
      <div v-if="fixedRows.length" class="ftw-fixed">
        <p class="ftw-hint">固定来源输入（沿用节点配置，不单独覆盖）：</p>
        <p v-for="(row, i) in fixedRows" :key="i" class="ftw-fixed-row">{{ row.nodeName }} · {{ row.label }} = <b>{{ row.display }}</b></p>
      </div>
      <p v-if="!plan.entryNeeds.length && !plan.externalInputs.length && scopeKind !== 'all' && !fixedRows.length && plan.order.length" class="ftw-hint">该范围没有需要填写的外部输入，可直接运行。</p>
      <p v-if="formError" class="ftw-error" role="alert">{{ formError }}</p>
      <div class="ftw-actions">
        <button class="primary-run" :disabled="running || !!planError || !plan.order.length" @click="run">{{ running ? '执行中…' : '▷ 运行测试' }}</button>
      </div>
      <p v-if="writes.length" class="ftw-writes">本次范围包含真实外部调用：{{ writes.length }} 项（运行前会再次确认）；范围外写节点不会执行。</p>
    </aside>

    <!-- 右：结果 -->
    <section class="ftw-output" aria-label="测试结果">
      <div class="ftw-result-head">
        <h3>{{ scopeKind === 'segment' ? '片段测试结果' : scopeKind === 'single' ? '节点测试结果' : '编排测试结果' }}</h3>
        <span v-if="result" class="ftw-badge" :class="statusClass">{{ statusLabel }}</span>
        <span v-if="staleConfig || staleProject" class="ftw-badge warn">旧{{ staleConfig ? '配置' : '项目' }}结果</span>
        <span v-else-if="staleInput" class="ftw-badge warn">尚未按当前输入/范围运行</span>
      </div>
      <p class="ftw-scope">{{ result ? `范围：${result.snapshot.scopeText} · 提交于 ${result.snapshot.submittedAt}` : '选择范围、填写输入后运行；范围内自动传递，范围外不执行。' }}</p>
      <p class="ftw-meta">{{ projectName || '未选择项目' }} · 结果仅随本次响应返回，不落盘、不写编排草稿</p>
      <p v-if="freshnessText" class="ftw-fresh">{{ freshnessText }}</p>

      <template v-if="result && result.status === 'request-error'">
        <div class="ftw-error-note">运行请求失败：{{ result.error }}<br><small>这是请求失败，不代表编排配置有误，也不代表测试通过。</small></div>
      </template>
      <template v-else-if="result && result.status === 'running'">
        <div class="ftw-empty">正在执行，请等待响应（一次性接口，不伪造逐节点进度）。</div>
      </template>
      <template v-else-if="!result">
        <div class="ftw-empty">尚无测试结果。左侧选择范围并运行后，这里展示每个被测节点的实际输入、输出与执行记录。</div>
      </template>
      <template v-else>
        <div class="ftw-chips" role="tablist" aria-label="被测节点结果切换">
          <button v-for="r in nodeResults" :key="r.nodeId" role="tab" :aria-selected="resultNode === r.nodeId" :class="{ active: resultNode === r.nodeId }" @click="pickResultNode(r.nodeId)">
            {{ nameOf(r.nodeId) }}
            <small>{{ r.status === 'success' ? '✓' : r.status === 'failed' ? '失败' : '未执行' }}</small>
          </button>
        </div>
        <template v-if="currentNode">
          <div class="ftw-tabs" role="tablist" aria-label="结果视图">
            <button role="tab" :aria-selected="resultTab === 'output'" :class="{ active: resultTab === 'output' }" @click="resultTab = 'output'">输出</button>
            <button role="tab" :aria-selected="resultTab === 'inputs'" :class="{ active: resultTab === 'inputs' }" @click="resultTab = 'inputs'">本节点输入</button>
            <button role="tab" :aria-selected="resultTab === 'steps'" :class="{ active: resultTab === 'steps' }" @click="resultTab = 'steps'">执行记录</button>
          </div>
          <div class="ftw-result-body">
            <template v-if="resultTab === 'output'">
              <div v-if="currentNode.status === 'skipped'" class="ftw-empty">上游失败，此节点未执行；没有输出。</div>
              <div v-else-if="currentNode.status === 'failed'" class="ftw-error-note">{{ currentNode.error }}<br><small>后续所选节点已停止；范围外节点始终不执行。</small></div>
              <template v-else>
                <template v-for="(out, name) in currentNode.outputs" :key="name">
                  <p class="ftw-out-name">{{ name }}</p>
                  <div v-if="tableRows(out)" class="ftw-table">
                    <table>
                      <thead><tr><th v-for="k in tableRows(out)!.keys" :key="k">{{ k }}</th></tr></thead>
                      <tbody><tr v-for="(row, i) in tableRows(out)!.rows" :key="i"><td v-for="k in tableRows(out)!.keys" :key="k">{{ row[k] ?? '—' }}</td></tr></tbody>
                    </table>
                    <p v-if="tableRows(out)!.truncated" class="ftw-hint">表格仅预览前 100 行；传给下游的实际数据未截断。</p>
                  </div>
                  <template v-else-if="out !== null && typeof out === 'object'">
                    <pre class="ftw-pre">{{ previewValue(out) }}</pre>
                  </template>
                  <template v-else>
                    <p class="ftw-scalar">{{ out === null ? '—' : out }}</p>
                    <pre class="ftw-pre">{{ previewValue(currentNode.outputs) }}</pre>
                  </template>
                </template>
                <p v-if="!Object.keys(currentNode.outputs || {}).length" class="ftw-hint">节点输出为空。</p>
              </template>
            </template>
            <template v-else-if="resultTab === 'inputs'">
              <div v-if="currentNode.status === 'skipped'" class="ftw-empty">此节点未执行，没有本次输入。</div>
              <template v-else-if="currentNode.inputs">
                <p v-if="currentNode.inputsTruncated" class="ftw-hint">输入预览已截短（仅影响展示，节点收到的数据未截断）。</p>
                <pre class="ftw-pre">{{ previewValue(currentNode.inputs) }}</pre>
              </template>
              <div v-else class="ftw-empty">未获得本次输入（输入解析前失败）。</div>
            </template>
            <template v-else>
              <p class="ftw-step-line">状态：{{ currentNode.status === 'success' ? '成功' : currentNode.status === 'failed' ? '失败' : '因上游失败未执行' }}<template v-if="currentNode.durationMs != null"> · 耗时 {{ currentNode.durationMs }}ms</template><template v-if="currentNode.rowCount != null"> · {{ currentNode.rowCount }} 条</template></p>
              <pre class="ftw-pre">{{ (currentNode.logs || []).join('\n') || '（无日志）' }}</pre>
            </template>
          </div>
        </template>
        <div v-else class="ftw-empty">本次运行没有节点结果。</div>
      </template>
    </section>
  </div>
</div>
</template>
<style scoped>
.ftw-root{display:flex;flex-direction:column;height:100%;min-height:0;background:var(--paper)}
.ftw-head{display:flex;align-items:center;gap:12px;padding:10px 16px;border-bottom:1px solid var(--line);flex-wrap:wrap;flex:none;background:var(--paper)}
.ftw-head h2{font-size:16px;margin:0;display:flex;align-items:center;gap:8px}
.ftw-tag{font-size:11px;background:var(--blue-soft);color:var(--blue-ink);border:1px solid var(--blue-line);border-radius:4px;padding:2px 8px}
.ftw-sub{color:var(--muted)}
.push{margin-left:auto}
.quiet{border-color:transparent;background:transparent}
.quiet:hover{background:var(--paper-2)}
.ftw-project{font-size:12px;color:var(--muted)}
.ftw-grid{display:grid;grid-template-columns:330px minmax(0,1fr);flex:1;min-height:0}
.ftw-input{border-right:1px solid var(--line);overflow:auto;padding:16px 18px;background:var(--paper-2);min-height:0}
.ftw-output{overflow:auto;padding:18px 24px;min-height:0}
.ftw-input h3,.ftw-result-head h3{font-size:14px;margin:0 0 10px}
.ftw-sec{margin-top:18px}
.ftw-field{display:block;font-size:12px;color:var(--muted);margin:10px 0}
.ftw-field input,.ftw-field textarea,.ftw-field select{margin-top:4px}
.ftw-src{display:block;font-size:11px;margin-top:2px}
.ftw-hint{font-size:12px;color:var(--muted);margin:6px 0}
.ftw-error{color:var(--danger);font-size:12px;margin:8px 0}
.ftw-note{background:var(--warn-soft);border:1px solid var(--warn-line);border-radius:6px;padding:9px 11px;font-size:12px;color:var(--warn);margin:8px 0}
.ftw-checks{display:flex;flex-direction:column;gap:4px;margin-top:6px}
.ftw-check{display:flex;align-items:center;gap:6px;font-size:12px;color:var(--ink)}
.ftw-check input{width:auto}
.ftw-path{display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin:10px 0}
.ftw-path span{background:var(--blue-soft);color:var(--blue-ink);border:1px solid var(--blue-line);border-radius:5px;padding:2px 9px;font-size:12px}
.ftw-path span:not(:last-child)::after{content:' →';color:var(--faint)}
.ftw-outside{background:var(--paper-3);border-radius:5px;color:var(--muted);font-size:11px;padding:7px 9px;margin:8px 0}
.ftw-fixed-row{font-size:12px;color:var(--muted);margin:3px 0}
.ftw-fixed b{color:var(--ink)}
.linklike{border:0;background:none;color:var(--blue);font-size:12px;padding:2px 0;cursor:pointer}
.ftw-actions{margin-top:14px}
.primary-run{background:var(--blue);border-color:var(--blue);color:#fff}
.primary-run:hover{background:var(--blue-deep);color:#fff}
.primary-run:disabled{opacity:.5}
.ftw-writes{font-size:11px;color:var(--warn);margin-top:8px}
.ftw-result-head{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.ftw-badge{font-size:11px;border-radius:10px;padding:2px 10px;background:var(--paper-2);border:1px solid var(--line)}
.ftw-badge.ok{color:var(--ok);background:var(--ok-soft);border-color:var(--ok-line)}
.ftw-badge.bad{color:var(--danger);background:var(--danger-soft);border-color:var(--danger-line)}
.ftw-badge.run{color:var(--blue);background:var(--blue-soft);border-color:var(--blue-line)}
.ftw-badge.warn{color:var(--warn);background:var(--warn-soft);border-color:var(--warn-line)}
.ftw-scope{font-size:12px;color:var(--muted);margin:6px 0}
.ftw-meta{font-size:11px;color:var(--faint);margin:2px 0 0}
.ftw-fresh{font-size:12px;color:var(--warn);margin:4px 0}
.ftw-chips{display:flex;gap:7px;flex-wrap:wrap;margin:12px 0}
.ftw-chips button{font-size:12px}
.ftw-chips button.active{color:var(--blue);background:var(--blue-soft);border-color:var(--blue-line)}
.ftw-chips button small{display:block;font-size:10px;color:var(--muted)}
.ftw-tabs{display:flex;gap:4px;border-bottom:1px solid var(--line);margin:6px 0 12px}
.ftw-tabs button{border:0;border-bottom:2px solid transparent;border-radius:0;padding:7px 4px;font-size:13px;background:none}
.ftw-tabs button.active{border-bottom-color:var(--blue);color:var(--blue)}
.ftw-result-body{min-height:0}
.ftw-pre{font-family:ui-monospace,Menlo,monospace;font-size:12px;background:var(--paper-2);border-radius:8px;padding:12px;white-space:pre-wrap;overflow-wrap:anywhere;margin:8px 0}
.ftw-scalar{font-size:34px;font-weight:600;margin:14px 0}
.ftw-out-name{font-size:12px;color:var(--muted);margin:12px 0 4px}
.ftw-table{overflow:auto;border:1px solid var(--line);border-radius:8px}
.ftw-table table{margin:0}
.ftw-table th,.ftw-table td{padding:9px 12px}
.ftw-empty{border:1px dashed var(--line-2);border-radius:8px;padding:22px;color:var(--muted);font-size:13px;margin-top:16px}
.ftw-error-note{background:var(--danger-soft);border:1px solid var(--danger-line);border-radius:8px;padding:12px 14px;color:var(--danger);font-size:13px;margin-top:12px}
.ftw-error-note small{color:var(--muted)}
.ftw-step-line{font-size:13px;margin:8px 0}
@media(max-width:1100px){.ftw-grid{grid-template-columns:260px minmax(0,1fr)}.ftw-input{padding:12px}.ftw-output{padding:12px 14px}}
@media(max-width:768px){.ftw-grid{grid-template-columns:230px minmax(0,1fr)}}
</style>
