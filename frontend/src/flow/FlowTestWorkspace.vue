<!-- FlowTestWorkspace — 编排独立测试视图（20260919_函数编排优化审阅修正 定点修正版）。
     R01 运行生命周期：同步校验通过后先置 pendingConfirm 防重入，确认与请求共用同一份
         冻结提交对象（payload/snapshot 在弹窗前构建）；取消/成功/失败/晚回包均正确释放，
         v-show 挂载下返回设计再进入不绕过等待态。
     R02 范围选择：多路径勾选区按「存在多条路径」显示（不因已选数量消失）；整条/单节点
         不做连通限制（合法并列分支），仅连续片段要求连通；禁用原因对所有模式可见。
     R03 一套测试输入表单：入口稳定 ID 只渲染一次（整条=入口声明；片段/单节点=所需入口），
         类型控件一致（布尔下拉/JSON 文本域/数值/文本），「使用空文本」显式区分空串与未提供。
     R04 范围保留与结果归属：主入口首次默认整条，之后恢复上次范围/输入/结果；节点快捷入口
         才显式切换；快照带 projectName/nodeNames，旧结果标题不随当前上下文变化；输入缓存按
         编排/项目隔离，声明签名变化即失效。
     R05 结果呈现：标量(0/false/null 可见)/对象 JSON/对象列表表格/标量列表序号表/混合列表
         JSON/空列表明确提示；表格≤100 行预览，键只扫描一次；原始 JSON 可切换。
     本组件只读不保存：不 emit before-change/changed；测试输入与结果仅在页面内存。 -->
<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { appConfirm } from '../shared/appConfirm'
import AppSelect from '../shared/AppSelect.vue'
import { TYPE_LABELS, clone, configSignature, dependencyPaths, describeOutput, missingUpstreams, nameOfFlowNode, processingNodes, testInputSignature, testScopePlan, validateTestValue, writeCapabilities } from './flowModel'
import { runFlow } from './api'

const props = defineProps<{ state: any; projectId?: string; projectName?: string; revision?: string }>()

// ── 范围选择（返回设计后保留；主入口首次默认整条，之后恢复上次范围） ──────────────
const scopeKind = ref<'all' | 'single' | 'segment'>('segment')
const singleId = ref('')
const segStart = ref('')
const segEnd = ref('')
const explicitIds = ref<string[]>([]) // 多路径时的显式选集
let scopeInitialized = false

const procNodes = computed(() => processingNodes(props.state))
const nameOf = (id: string) => nameOfFlowNode(props.state, id)
const nodeOptions = computed(() => procNodes.value.map((n: any) => ({ value: n.id, label: n.name || n.id })))

const segPaths = computed<string[][]>(() => {
  if (scopeKind.value !== 'segment' || !segStart.value || !segEnd.value) return []
  return dependencyPaths(props.state, segStart.value, segEnd.value)
})
function onSegChange() { explicitIds.value = [] }
const scopeValue = computed(() => scopeKind.value === 'single' ? 'single:' + singleId.value : scopeKind.value)
const scopeOptions = computed(() => [
  { value: 'all', label: '整条编排' },
  { value: 'segment', label: '连续片段' },
  ...procNodes.value.map((n: any) => ({ value: 'single:' + n.id, label: '单节点 · ' + (n.name || n.id) })),
])
function onScopeChange(v: string) {
  if (v === 'all' || v === 'segment') { scopeKind.value = v; singleId.value = ''; onSegChange(); scopeInitialized = true; return }
  if (v.startsWith('single:')) { scopeKind.value = 'single'; singleId.value = v.slice(7); onSegChange(); scopeInitialized = true }
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
// R02b：整条/单节点允许并列分支（不连通合法）；连续片段保持连通要求
const plan = computed(() => {
  const targets = scopeKind.value === 'all' ? procNodes.value.map((n: any) => n.id)
    : scopeKind.value === 'single' ? (singleId.value ? [singleId.value] : [])
    : segRangeIds.value
  return testScopePlan(props.state, targets, { requireConnected: scopeKind.value === 'segment' })
})
const planError = computed(() => {
  if (scopeKind.value === 'segment' && !explicitIds.value.length && segStart.value && segEnd.value) {
    const paths = segPaths.value
    if (!paths.length) return `「${nameOf(segStart.value)}」到「${nameOf(segEnd.value)}」没有沿依赖方向的路径（起点必须在终点的上游）`
    if (paths.length > 1) return '存在多条路径，请在下方勾选要测试的节点'
  }
  return plan.value.error
})
function toggleExplicit(id: string) {
  explicitIds.value = explicitIds.value.includes(id) ? explicitIds.value.filter(x => x !== id) : [...explicitIds.value, id]
}
const missingForAll = computed(() => scopeKind.value === 'all' ? missingUpstreams(props.state, plan.value.order) : [])
const scopeText = computed(() => {
  if (scopeKind.value === 'all') return '整条编排'
  const ids = plan.value.order
  if (!ids.length) return '未选择范围'
  if (scopeKind.value === 'single') return `单节点 · ${nameOf(ids[0])}`
  return `连续片段（${ids.length} 个节点）`
})

// ── 测试输入（稳定 ID 去重；0/false/显式空文本有效；固定值只读摘要） ─────────────
const overrideTexts = ref<Record<string, string>>({})
const overrideEmpty = ref<Record<string, boolean>>({})
const entryTexts = ref<Record<string, string>>({})
const entryEmpty = ref<Record<string, boolean>>({})
const overrideKey = (nodeId: string, inputId: string) => nodeId + '\u0000' + inputId
// R04：输入缓存按 编排/项目 隔离；切项目/编排清空输入并使在途回包失效（晚回包不落新上下文）
const cacheContext = computed(() => (props.state?.flowId || '') + '|' + (props.projectId || ''))
watch(cacheContext, () => {
  overrideTexts.value = {}; overrideEmpty.value = {}
  entryTexts.value = {}; entryEmpty.value = {}
  runGen.value++ // 在途响应作废（切编排/项目后晚回包不落新上下文）
  result.value = null
  scopeInitialized = false
  resultNode.value = ''
})
// 声明改型/删除 → 对应缓存失效（粗粒度：签名变化即清空，避免同名错配）
const declSig = computed(() => JSON.stringify([
  (props.state?.inputs || []).map((d: any) => [d.id, d.type]),
  ...procNodes.value.map((n: any) => [n.id, (n.inputs || []).map((i: any) => [i.id, i.type])]),
]))
watch(declSig, (next, prev) => { if (next !== prev && prev !== undefined) {
  overrideTexts.value = {}; overrideEmpty.value = {}
  entryTexts.value = {}; entryEmpty.value = {}
} })

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
// R03：入口表单统一（整条=入口声明；片段/单节点=所需入口），每稳定 ID 只渲染一次
const entryRows = computed(() => {
  if (scopeKind.value === 'all') return (props.state.inputs || []).map((d: any) => ({ key: d.id, label: d.label || d.name || d.id, type: d.type || { type: 'text' }, required: true, source: null as any }))
  return plan.value.entryNeeds.map(need => {
    const decl = (props.state.inputs || []).find((i: any) => i.id === need.inputId)
    return { key: need.inputId, label: need.label, type: decl?.type || { type: 'text' }, required: true, source: null as any }
  })
})

// ── 运行与结果（R01：pendingConfirm 防重入；确认与请求共用冻结快照） ─────────────
const running = ref(false)
const pendingConfirm = ref(false)
const runBusy = computed(() => running.value || pendingConfirm.value)
const runGen = ref(0)
const result = ref<null | { gen: number; status: 'running' | 'done' | 'request-error'; error?: string; payload?: any; snapshot: { sig: string; projectId: string; projectName: string; scopeKind: string; targets: string[]; scopeText: string; inputSig: string; submittedAt: string; nodeNames: Record<string, string> } }>(null)
const resultNode = ref('')
const resultTab = ref<'output' | 'inputs' | 'steps'>('output')
const formError = ref('')
const currentSig = computed(() => configSignature(props.state))
const currentInputSig = computed(() => testInputSignature(
  { kind: scopeKind.value, targets: plan.value.order },
  { o: overrideTexts.value, pe: overrideEmpty.value, e: entryTexts.value, ee: entryEmpty.value }, {}))
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
const runDisableReason = computed(() => {
  if (planError.value) return planError.value
  if (!plan.value.order.length) return scopeKind.value === 'segment' ? '请选择片段起止或勾选要测试的节点' : '请先选择测试范围'
  if (scopeKind.value === 'all' && missingForAll.value.length) return `缺少上游节点：${missingForAll.value.map(nameOf).join('、')}`
  return ''
})

function collectFieldValue(row: { key: string; label: string; type: any; required: boolean }, texts: Record<string, string>, providedEmpty: Record<string, boolean>): { ok: boolean; skip?: boolean; value?: any; error?: string } {
  const raw = texts[row.key]
  const t = row.type?.type
  if (t === 'boolean') {
    if (raw === '' || raw == null) return { ok: false, error: '请选择是/否（不会默认为否）' }
    return { ok: true, value: raw === 'true' }
  }
  if (t === 'object' || t === 'list') {
    if (raw == null || raw === '') return { ok: false, error: `请填写 JSON（示例 ${t === 'object' ? '{}' : '[]'}）` }
    const verdict = validateTestValue(row.type, raw)
    return verdict.ok ? { ok: true, value: verdict.value } : { ok: false, error: verdict.error }
  }
  if (providedEmpty[row.key]) return { ok: true, value: '' } // 显式空文本（R03）
  if (raw == null || raw === '') return { ok: false, error: '尚未填写；如需提交空字符串请勾选「使用空文本」' }
  if (t === 'number') {
    const n = Number(raw)
    return Number.isFinite(n) ? { ok: true, value: n } : { ok: false, error: '需要数值' }
  }
  return { ok: true, value: String(raw) }
}

async function run() {
  if (runBusy.value) return // R01：同步锁定，防连点与键盘重复（确认与执行都算忙）
  formError.value = ''
  const planNow = plan.value
  if (planError.value) { formError.value = planError.value; return }
  if (!planNow.order.length) { formError.value = '请先选择测试范围'; return }
  // 组装输入（后端仍做权威校验；这里先给可读的即时反馈）
  const overrides: Record<string, Record<string, any>> = {}
  for (const item of planNow.externalInputs) {
    const row = { key: overrideKey(item.nodeId, item.inputId), label: `${item.nodeName} · ${item.label}`, type: item.type, required: item.required }
    const verdict = collectFieldValue(row, overrideTexts.value, overrideEmpty.value)
    if (!verdict.ok) {
      if (item.kind === 'unbound' && verdict.error?.startsWith('尚未填写')) continue // 可选输入未提供：跳过
      formError.value = `「${row.label}」：${verdict.error}`
      return
    }
    (overrides[item.nodeId] = overrides[item.nodeId] || {})[item.inputId] = verdict.value
  }
  const inputs: Record<string, any> = {}
  for (const row of entryRows.value) {
    const verdict = collectFieldValue(row, entryTexts.value, entryEmpty.value)
    if (!verdict.ok) { formError.value = `入口参数「${row.label}」：${verdict.error}`; return }
    inputs[row.key] = verdict.value
    const decl = (props.state.inputs || []).find((i: any) => i.id === row.key)
    if (decl?.name) inputs[decl.name] = verdict.value
  }
  // R01：冻结提交对象（确认与请求、界面快照共用同一份；确认期间配置/范围变化不影响本次提交）
  const isAll = scopeKind.value === 'all'
  const payload: any = { state: clone(props.state), projectId: props.projectId || undefined, inputs }
  if (isAll) { payload.revision = props.revision }
  else { payload.testMode = 'isolated'; payload.targets = [...planNow.order]; payload.inputOverrides = overrides }
  const snapshot = { sig: currentSig.value, projectId: props.projectId || '',
    projectName: props.projectName || '', scopeKind: scopeKind.value,
    targets: [...planNow.order], scopeText: scopeText.value,
    inputSig: testInputSignature({ kind: scopeKind.value, targets: planNow.order },
      { o: { ...overrideTexts.value }, pe: { ...overrideEmpty.value }, e: { ...entryTexts.value }, ee: { ...entryEmpty.value } }, {}),
    submittedAt: new Date().toLocaleTimeString('zh-CN', { hour12: false }),
    nodeNames: Object.fromEntries(procNodes.value.map((n: any) => [n.id, n.name || n.id])) }
  const writesNow = writes.value
  // 同步锁定先于异步确认：连点/键盘重复在确认期间也无法再次进入
  pendingConfirm.value = true
  let confirmed = true
  try {
    if (writesNow.length) {
      confirmed = await appConfirm({
        message: `本次测试将对所选范围执行真实外部调用：\n${writesNow.join('\n')}\n\n范围外节点不会执行。继续？`,
        confirmLabel: '开始测试',
      })
    }
  } finally {
    if (!confirmed) pendingConfirm.value = false // 取消确认：立即释放，可再次发起
  }
  if (!confirmed) return
  pendingConfirm.value = false // 进入执行阶段：忙状态由 running 表达
  running.value = true
  const gen = ++runGen.value
  result.value = { gen, status: 'running', snapshot }
  resultNode.value = planNow.order[0] || ''
  resultTab.value = 'output'
  try {
    const d = await runFlow(payload)
    if (gen !== runGen.value) return // 晚回包：上下文已切换（编排/项目），不覆盖
    result.value = { gen, status: 'done', payload: d, snapshot }
  } catch (e: any) {
    if (gen !== runGen.value) return
    result.value = { gen, status: 'request-error', error: e?.message || String(e), snapshot }
  } finally {
    running.value = false // 成功/失败都释放；视图隐藏期间请求继续，返回后仍显示状态
  }
}

// ── 结果展示（R05：分类渲染；快照元信息来自提交时） ─────────────────────────────
const nodeResults = computed<any[]>(() => result.value?.status === 'done' ? (result.value.payload?.nodeResults || []) : [])
const currentNode = computed<any>(() => nodeResults.value.find((r: any) => r.nodeId === resultNode.value))
const statusLabel = computed(() => {
  if (!result.value) return ''
  if (result.value.status === 'running') return '执行中 / 等待响应…'
  if (result.value.status === 'request-error') return '请求失败'
  return result.value.payload?.status === 'success' ? '本次测试成功' : '本次测试失败'
})
const statusClass = computed(() => !result.value ? '' : result.value.status === 'running' ? 'run' : result.value.status === 'request-error' || result.value.payload?.status !== 'success' ? 'bad' : 'ok')
function snapName(id: string): string { return result.value?.snapshot.nodeNames?.[id] || id }
function pickResultNode(id: string) { resultNode.value = id; resultTab.value = 'output' }
function previewValue(value: any): string {
  try { return JSON.stringify(value, null, 2) } catch { return String(value) }
}
const outputViews = computed(() => {
  const outputs = currentNode.value?.outputs || {}
  return Object.entries(outputs).map(([name, value]: [string, any]) => ({ name, view: describeOutput(value) }))
})
const outputJsonMode = ref<Record<string, boolean>>({})
function toggleJson(name: string) { outputJsonMode.value[name] = !outputJsonMode.value[name] }
const typeText = (decl: any) => TYPE_LABELS[decl?.type] || decl?.type || '?'

// 由 FlowEditor 调用：主入口（恢复上次范围；首次默认整条）或节点快捷入口（显式切到该节点）
function openScope(kind: 'all' | 'single' | 'segment', nodeId?: string) {
  if (kind === 'single' && nodeId) {
    scopeKind.value = 'single'; singleId.value = nodeId; onSegChange(); scopeInitialized = true
    return
  }
  if (kind === 'segment') {
    scopeKind.value = 'segment'
    if ((!segStart.value || !segEnd.value) && procNodes.value.length) {
      segStart.value = procNodes.value[0].id
      segEnd.value = procNodes.value[procNodes.value.length - 1].id
    }
    onSegChange(); scopeInitialized = true
    return
  }
  // R04 主入口：首次默认整条；之后恢复当前范围/输入/结果（不强制重置）
  if (!scopeInitialized) { scopeKind.value = 'all'; singleId.value = ''; onSegChange(); scopeInitialized = true }
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
        <!-- R02a：多路径时勾选区持续可见，勾选/取消/清空都不消失 -->
        <div v-if="segPaths.length > 1" class="ftw-note">
          存在多条依赖路径，请勾选要测试的节点集合：
          <div class="ftw-checks">
            <label v-for="n in procNodes" :key="n.id" class="ftw-check">
              <input type="checkbox" :checked="explicitIds.includes(n.id)" @change="toggleExplicit(n.id)"/> {{ n.name }}
            </label>
          </div>
          <button v-if="explicitIds.length" class="linklike" @click="onSegChange">清空勾选，改为起止选择</button>
        </div>
        <div v-else-if="plan.order.length" class="ftw-path" aria-label="执行顺序">
          <span v-for="id in plan.order" :key="id">{{ nameOf(id) }}</span>
        </div>
      </template>
      <p v-if="outsideNote" class="ftw-outside">{{ outsideNote }}</p>

      <h3 class="ftw-sec">测试输入</h3>
      <p class="ftw-hint">{{ scopeKind === 'all' ? '填写编排入口参数，按依赖顺序执行全部处理节点（允许并列分支）。' : '只为范围外来源提供本次测试值；范围内上游输出自动传递，不允许覆盖。测试值不修改节点绑定。' }}</p>
      <!-- R03：统一入口表单——每稳定 ID 只渲染一次，类型控件与范围无关 -->
      <label v-for="row in entryRows" :key="row.key" class="ftw-field">入口参数 · {{ row.label }} · {{ typeText(row.type) }}
        <select v-if="row.type?.type === 'boolean'" :value="entryTexts[row.key] ?? ''" :aria-label="'入口参数 ' + row.label" @change="entryTexts[row.key] = ($event.target as HTMLSelectElement).value">
          <option value="">（请选择）</option><option value="true">是</option><option value="false">否</option>
        </select>
        <textarea v-else-if="row.type?.type === 'object' || row.type?.type === 'list'" :value="entryTexts[row.key] || ''" rows="3" :placeholder="row.type?.type === 'object' ? '{}' : '[]'" :aria-label="'入口参数 ' + row.label" @input="entryTexts[row.key] = ($event.target as HTMLTextAreaElement).value"/>
        <template v-else>
          <input :value="overrideEmpty[row.key] || entryEmpty[row.key] ? '' : (entryTexts[row.key] || '')" :disabled="!!entryEmpty[row.key]" :aria-label="'入口参数 ' + row.label" placeholder="本次测试的入口值" @input="entryTexts[row.key] = ($event.target as HTMLInputElement).value"/>
          <span class="ftw-empty-opt"><input type="checkbox" :checked="!!entryEmpty[row.key]" @change="entryEmpty[row.key] = ($event.target as HTMLInputElement).checked"/> 使用空文本 ""（作为本次提交值）</span>
        </template>
      </label>
      <div v-for="item in plan.externalInputs" :key="item.nodeId + '/' + item.inputId" class="ftw-ext">
        <label class="ftw-field">{{ item.nodeName }} · {{ item.label }} · {{ typeText(item.type) }}<small class="ftw-src">{{ sourceHint(item) }}{{ item.kind === 'unbound' ? '（可选）' : '' }}</small>
          <select v-if="item.type?.type === 'boolean'" :value="overrideTexts[overrideKey(item.nodeId, item.inputId)] ?? ''" :aria-label="item.nodeName + ' ' + item.label" @change="overrideTexts[overrideKey(item.nodeId, item.inputId)] = ($event.target as HTMLSelectElement).value">
            <option value="">（请选择）</option><option value="true">是</option><option value="false">否</option>
          </select>
          <textarea v-else-if="item.type?.type === 'object' || item.type?.type === 'list'" :value="overrideTexts[overrideKey(item.nodeId, item.inputId)] || ''" rows="3" :placeholder="item.type?.type === 'object' ? '{}' : '[]'" :aria-label="item.nodeName + ' ' + item.label" @input="overrideTexts[overrideKey(item.nodeId, item.inputId)] = ($event.target as HTMLTextAreaElement).value"/>
          <template v-else>
            <input :value="overrideEmpty[overrideKey(item.nodeId, item.inputId)] ? '' : (overrideTexts[overrideKey(item.nodeId, item.inputId)] || '')" :disabled="!!overrideEmpty[overrideKey(item.nodeId, item.inputId)]" :aria-label="item.nodeName + ' ' + item.label" :placeholder="item.kind === 'unbound' ? '可选：为未绑定输入提供本次测试值' : '本次测试值'" @input="overrideTexts[overrideKey(item.nodeId, item.inputId)] = ($event.target as HTMLInputElement).value"/>
            <span class="ftw-empty-opt"><input type="checkbox" :checked="!!overrideEmpty[overrideKey(item.nodeId, item.inputId)]" @change="overrideEmpty[overrideKey(item.nodeId, item.inputId)] = ($event.target as HTMLInputElement).checked"/> 使用空文本 ""（作为本次提交值）</span>
          </template>
        </label>
      </div>
      <div v-if="fixedRows.length" class="ftw-fixed">
        <p class="ftw-hint">固定来源输入（沿用节点配置，不单独覆盖）：</p>
        <p v-for="(row, i) in fixedRows" :key="i" class="ftw-fixed-row">{{ row.nodeName }} · {{ row.label }} = <b>{{ row.display }}</b></p>
      </div>
      <p v-if="!entryRows.length && !plan.externalInputs.length && !fixedRows.length && plan.order.length" class="ftw-hint">该范围没有需要填写的外部输入，可直接运行。</p>
      <p v-if="runDisableReason" class="ftw-error" role="alert">无法运行：{{ runDisableReason }}</p>
      <p v-if="formError" class="ftw-error" role="alert">{{ formError }}</p>
      <div class="ftw-actions">
        <button class="primary-run" :disabled="runBusy || !!runDisableReason" :title="runDisableReason || ''" @click="run">{{ pendingConfirm ? '等待确认…' : running ? '执行中…' : '▷ 运行测试' }}</button>
      </div>
      <p v-if="writes.length" class="ftw-writes">本次范围包含真实外部调用：{{ writes.length }} 项（运行前会再次确认）；范围外写节点不会执行。</p>
    </aside>

    <!-- 右：结果 -->
    <section class="ftw-output" aria-label="测试结果">
      <div class="ftw-result-head">
        <h3>{{ result ? (result.snapshot.scopeKind === 'segment' ? '片段测试结果' : result.snapshot.scopeKind === 'single' ? '节点测试结果' : '编排测试结果') : '测试结果' }}</h3>
        <span v-if="result" class="ftw-badge" :class="statusClass">{{ statusLabel }}</span>
        <span v-if="staleConfig || staleProject" class="ftw-badge warn">旧{{ staleConfig ? '配置' : '项目' }}结果</span>
        <span v-else-if="staleInput" class="ftw-badge warn">尚未按当前输入/范围运行</span>
      </div>
      <p class="ftw-scope">{{ result ? `范围：${result.snapshot.scopeText} · 提交于 ${result.snapshot.submittedAt}` : '选择范围、填写输入后运行；范围内自动传递，范围外不执行。' }}</p>
      <p class="ftw-meta">结果项目：{{ result ? (result.snapshot.projectName || '未选择项目') : (projectName || '未选择项目') }}（当前项目：{{ projectName || '未选择' }}） · 结果仅随本次响应返回，不落盘、不写编排草稿</p>
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
            {{ snapName(r.nodeId) }}
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
                <template v-for="out in outputViews" :key="out.name">
                  <p class="ftw-out-name">{{ out.name }}
                    <button v-if="out.view.kind !== 'scalar'" class="linklike" @click="toggleJson(out.name)">{{ outputJsonMode[out.name] ? '表格/突出值' : '原始 JSON' }}</button>
                  </p>
                  <!-- 空列表：明确状态，可看 [] -->
                  <template v-if="out.view.kind === 'empty'">
                    <p class="ftw-scalar ftw-empty-list">空列表（0 项）</p>
                    <pre class="ftw-pre">{{ previewValue(out.view.value) }}</pre>
                  </template>
                  <!-- 对象列表：表格预览（键只扫描一次），可切原始 JSON -->
                  <div v-else-if="out.view.kind === 'object-table' && !outputJsonMode[out.name]" class="ftw-table">
                    <table>
                      <thead><tr><th v-for="k in out.view.keys" :key="k">{{ k }}</th></tr></thead>
                      <tbody><tr v-for="(row, i) in out.view.rows" :key="i"><td v-for="k in out.view.keys" :key="k">{{ row[k] === null ? 'null' : row[k] === undefined ? '—' : (typeof row[k] === 'object' ? previewValue(row[k]) : String(row[k])) }}</td></tr></tbody>
                    </table>
                    <p v-if="out.view.truncated" class="ftw-hint">表格仅预览前 100 行（共 {{ out.view.total }} 行）；传给下游的实际数据未截断。</p>
                  </div>
                  <!-- 标量列表：序号 / 值 表格，不空白 -->
                  <div v-else-if="out.view.kind === 'index-table' && !outputJsonMode[out.name]" class="ftw-table">
                    <table>
                      <thead><tr><th>序号</th><th>值</th></tr></thead>
                      <tbody><tr v-for="(item, i) in out.view.rows" :key="i"><td>{{ i + 1 }}</td><td>{{ item === null ? 'null' : (typeof item === 'object' ? previewValue(item) : String(item)) }}</td></tr></tbody>
                    </table>
                    <p v-if="out.view.truncated" class="ftw-hint">表格仅预览前 100 行（共 {{ out.view.total }} 行）；传给下游的实际数据未截断。</p>
                  </div>
                  <!-- 对象 / 混合嵌套列表 / 勾选 JSON：格式化 JSON -->
                  <pre v-else class="ftw-pre">{{ previewValue(out.view.value) }}</pre>
                  <!-- 标量：突出显示 + JSON 对照；false/0/null 不显示为空 -->
                  <template v-if="out.view.kind === 'scalar'">
                    <p class="ftw-scalar">{{ out.view.value === null ? 'null' : out.view.value === '' ? '（空文本 ""）' : String(out.view.value) }}</p>
                    <pre class="ftw-pre">{{ previewValue(currentNode.outputs) }}</pre>
                  </template>
                </template>
                <p v-if="!Object.keys(currentNode.outputs || {}).length" class="ftw-hint">节点输出为空。</p>
              </template>
            </template>
            <template v-else-if="resultTab === 'inputs'">
              <div v-if="currentNode.status === 'skipped'" class="ftw-empty">此节点未执行，没有本次输入。</div>
              <template v-else-if="currentNode.inputs">
                <p v-if="currentNode.inputsTruncated" class="ftw-hint">输入预览已按展示预算截短（仅影响展示，节点收到的数据未截断；截短字段带 previewTruncated 标记）。</p>
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
.ftw-empty-opt{display:flex;align-items:center;gap:5px;font-size:11px;color:var(--muted);margin-top:3px}
.ftw-empty-opt input{width:auto}
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
.ftw-empty-list{font-size:14px;font-weight:500;color:var(--muted)}
.ftw-out-name{font-size:12px;color:var(--muted);margin:12px 0 4px;display:flex;align-items:center;gap:10px}
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
