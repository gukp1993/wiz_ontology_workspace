<!-- ─── A06 保存新本体（20260920 从物料自动构建本体）────────────────────────────
     契约：文档/接口文档/08-从物料自动构建本体接口.md §8「交付（原子创建新本体）」。
     布局基准：原型 交互原型_v1.html 的 savePage()——左侧「本次纳入」（各类型计数 / 结构检查 /
     未纳入项 / 材料覆盖缺口），右侧「新本体信息」（名称 + 确认勾选 + 创建按钮）。
     进入本页即调 deliverPrecheck：预检有阻断时禁用创建并逐条列出（带 candidateId 定位，
     点击回 A05 评审定位）。交付唯一入口 POST /api/build-deliver（checkToken + requestId）：
     同一 payload（名称 + checkToken）的超时/网络重试沿用同一个 requestId，不会创建第二个本体；
     改名或重新预检后换新 requestId。已交付（fetchDelivery 有值）只读展示结果，不再提供提交入口。
     只创建未发布草稿：不发布、不改动任何已有本体、不生成项目映射/连接/编排。
     请求：优先用 ontology/build/api.ts 的导出（并行开发中交付函数可能尚未提供），缺失时用本文件内
     本地 helper 兜底——仍然只经 app/http 的 getJson/postJson，不自行 fetch。
     props: taskId；emits: back（回评审定位）、saved（已创建本体，携带 ontologyId）。 -->
<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { getJson, postJson, SaveRequestError, isOutcomeUnknown } from '../../app/http'
import type { Candidate } from './types'
import { TYPE_LABELS } from './types'
import * as buildApi from './api'

const props = defineProps<{ taskId: string }>()
const emit = defineEmits<{
  (e: 'back', focus?: { candidateId?: string }): void
  (e: 'saved', ontologyId: string): void
}>()

// ── 宽松取值工具 ─────────────────────────────────────────────────────────────
function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : {}
}
function asArray<T = unknown>(value: unknown): T[] {
  return Array.isArray(value) ? (value as T[]) : []
}
function str(value: unknown): string {
  return typeof value === 'string' ? value : typeof value === 'number' ? String(value) : ''
}
function num(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}
function errorText(error: unknown): string {
  if (error instanceof SaveRequestError) return error.message
  if (error instanceof Error) return error.message
  return String(error || '请求失败')
}
function errorCode(error: unknown): string {
  return error instanceof SaveRequestError ? str(asRecord(error.data).code) : ''
}

// ── 端点：优先走 build/api.ts，缺失时本地 helper（仍经 app/http）───────────────
type RemoteFn = (...args: unknown[]) => Promise<unknown>
function remoteApi(name: string): RemoteFn | null {
  const value = (buildApi as unknown as Record<string, unknown>)[name]
  return typeof value === 'function' ? (value as RemoteFn) : null
}
function query(params: Record<string, string | number>): string {
  const parts: string[] = []
  for (const [key, value] of Object.entries(params)) {
    if (value === '' || value === undefined || value === null) continue
    parts.push(encodeURIComponent(key) + '=' + encodeURIComponent(String(value)))
  }
  return parts.length ? '?' + parts.join('&') : ''
}
async function requestPrecheck(taskId: string): Promise<unknown> {
  const fn = remoteApi('deliverPrecheck')
  if (fn) return await fn(taskId)
  return await postJson('/api/build-deliver-precheck', { taskId })
}
async function requestDeliver(taskId: string, name: string, checkToken: string, requestId: string): Promise<unknown> {
  const fn = remoteApi('deliver')
  if (fn) return await fn(taskId, name, checkToken, requestId)
  return await postJson('/api/build-deliver', { taskId, name, checkToken, requestId })
}
async function requestDelivery(taskId: string): Promise<unknown> {
  const fn = remoteApi('fetchDelivery')
  if (fn) return await fn(taskId)
  return await getJson('/api/build-delivery' + query({ taskId }))
}
async function requestCandidates(taskId: string, params: Record<string, string | number>): Promise<unknown> {
  const fn = remoteApi('fetchCandidates')
  if (fn) return await fn(taskId, params)
  return await getJson('/api/build-candidates' + query({ taskId, ...params }))
}

// ── 文案映射 ─────────────────────────────────────────────────────────────────
const TYPE_FALLBACK: Record<string, string> = { object: '对象', property: '属性', link: '链接', rule: '规则', action: '动作' }
const DECISION_FALLBACK: Record<string, string> = { include: '拟纳入', defer: '暂缓', exclude: '已排除' }
const STATUS_FALLBACK: Record<string, string> = { supported: '有依据', inferred: '推断待确认', conflict: '来源冲突', insufficient: '材料不足' }
const TYPE_KEYS = ['object', 'property', 'link', 'rule', 'action']
const COVERAGE_LABELS: Record<string, string> = {
  materials: '材料数', parsed: '已解析', success: '解析成功', partial: '部分解析', failed: '解析失败',
  excluded: '已排除材料', factCount: '解析事实数', notes: '说明', failedSegments: '失败片段',
  gaps: '覆盖缺口', missing: '缺失内容', warnings: '提示', summary: '摘要', sourceGroups: '同源组',
}
function labelFrom(map: unknown, key: string, fallback: Record<string, string>): string {
  const hit = asRecord(map)[key]
  return typeof hit === 'string' && hit ? hit : fallback[key] || key
}
const typeLabel = (key: string) => labelFrom(TYPE_LABELS, key, TYPE_FALLBACK)
const decisionLabel = (key: string) => DECISION_FALLBACK[key] || key
const statusLabel = (key: string) => STATUS_FALLBACK[key] || key

// ── 视图模型 ─────────────────────────────────────────────────────────────────
interface PrecheckIssue { code: string; message: string; candidateId: string }
interface PrecheckView {
  ok: boolean; counts: Record<string, number>; issues: PrecheckIssue[]
  excluded: number | null; deferred: number | null; checkToken: string
  coverage: Record<string, unknown> | null
}
interface CandidateRow { id: string; name: string; type: string; decision: string; evidenceStatus: string }
interface DeliveryView { ontologyId: string; deliveredAt: string; requestId: string }
interface AttemptState { requestId: string; name: string; checkToken: string }

function toCandidateRow(raw: unknown): CandidateRow {
  const node = asRecord(raw)
  return {
    id: str(node.id),
    name: str(node.name),
    type: str(node.type),
    decision: str(node.decision),
    evidenceStatus: str(node.evidenceStatus),
  }
}

const loading = ref(true)
const precheck = ref<PrecheckView | null>(null)
const precheckError = ref('')
const delivery = ref<DeliveryView | null>(null)
const deliveryError = ref('')
const deferredRows = ref<CandidateRow[]>([])
const deferredTotal = ref(0)
const excludedRows = ref<CandidateRow[]>([])
const excludedTotal = ref(0)
const untakenError = ref('')

const name = ref('')
const ack = ref(false)
const busy = ref(false)
const formError = ref('')
const unknownOutcome = ref(false)
const refreshing = ref(false)

// 同一 payload（名称 + checkToken）复用同一个 requestId：超时/网络重试不会产生第二个本体；
// payload 变化（改名、或重新预检后 checkToken 变化）必须换新 requestId，否则服务端按幂等冲突拒绝。
const attempt = ref<AttemptState | null>(null)

const blockingIssues = computed<PrecheckIssue[]>(() => (precheck.value && !precheck.value.ok ? precheck.value.issues : []))
const precheckOk = computed(() => !!precheck.value && precheck.value.ok === true)
const counts = computed<Record<string, number>>(() => precheck.value?.counts || {})
const countOf = (key: string) => (key in counts.value ? String(counts.value[key]) : '—')
const totalSelected = computed(() => TYPE_KEYS.reduce((sum, key) => sum + (counts.value[key] || 0), 0))
const deferredCount = computed(() => (precheck.value && precheck.value.deferred !== null ? String(precheck.value.deferred) : '—'))
const excludedCount = computed(() => (precheck.value && precheck.value.excluded !== null ? String(precheck.value.excluded) : '—'))
const errorRows = computed(() => [...deferredRows.value, ...excludedRows.value])
const coverageRows = computed<Array<{ label: string; text: string }>>(() => {
  const coverage = precheck.value?.coverage
  if (!coverage) return []
  const rows: Array<{ label: string; text: string }> = []
  for (const key of Object.keys(coverage)) {
    const value = coverage[key]
    const label = COVERAGE_LABELS[key] || key
    if (Array.isArray(value)) {
      const parts = value.map(item => (typeof item === 'string' ? item : typeof item === 'number' ? String(item) : Object.values(asRecord(item)).map(str).filter(Boolean).join(' · '))).filter(Boolean)
      if (parts.length) rows.push({ label, text: parts.join('；') })
      continue
    }
    if (typeof value === 'string' && value) { rows.push({ label, text: value }); continue }
    if (typeof value === 'number') { rows.push({ label, text: String(value) }); continue }
    if (value && typeof value === 'object') {
      const node = asRecord(value)
      const parts = Object.keys(node).map(key2 => key2 + '：' + (str(node[key2]) || String(node[key2]))).filter(Boolean)
      if (parts.length) rows.push({ label, text: parts.join('；') })
    }
  }
  return rows
})
const retryHint = computed(() => {
  const previous = attempt.value
  if (!previous) return ''
  const samePayload = !!precheck.value && previous.name === name.value.trim() && previous.checkToken === precheck.value.checkToken
  return samePayload
    ? '将沿用同一 requestId（' + previous.requestId + '）重试：服务端只会创建一个本体。'
    : '名称或预检基线已变化，重试会使用新的 requestId。'
})

watch(() => props.taskId, () => { void bootstrap() })
onMounted(() => { void bootstrap() })

async function bootstrap() {
  loading.value = true
  name.value = ''
  ack.value = false
  formError.value = ''
  unknownOutcome.value = false
  attempt.value = null
  precheck.value = null
  precheckError.value = ''
  delivery.value = null
  deliveryError.value = ''
  deferredRows.value = []
  excludedRows.value = []
  untakenError.value = ''
  await Promise.all([runPrecheck(), loadDelivery(), loadUntaken('defer'), loadUntaken('exclude')])
  loading.value = false
}

async function runPrecheck() {
  precheckError.value = ''
  try {
    const response = asRecord(await requestPrecheck(props.taskId))
    const rawCounts = asRecord(response.counts)
    const nextCounts: Record<string, number> = {}
    for (const key of Object.keys(rawCounts)) {
      const value = num(rawCounts[key])
      if (value !== null) nextCounts[key] = value
    }
    precheck.value = {
      ok: response.ok === true,
      counts: nextCounts,
      issues: asArray(response.issues).map(item => {
        const issue = asRecord(item)
        return { code: str(issue.code), message: str(issue.message), candidateId: str(issue.candidateId) }
      }),
      excluded: num(response.excluded),
      deferred: num(response.deferred),
      checkToken: str(response.checkToken),
      coverage: response.coverage && typeof response.coverage === 'object' ? asRecord(response.coverage) : null,
    }
  } catch (error) {
    precheck.value = null
    precheckError.value = '交付预检失败：' + errorText(error)
  }
}

async function loadDelivery() {
  deliveryError.value = ''
  try {
    const response = asRecord(await requestDelivery(props.taskId))
    const raw = response.delivery
    const node = raw && typeof raw === 'object' ? asRecord(raw) : null
    delivery.value = node && str(node.ontologyId)
      ? { ontologyId: str(node.ontologyId), deliveredAt: str(node.createdAt) || str(node.deliveredAt), requestId: str(node.requestId) }
      : null
  } catch (error) {
    deliveryError.value = '交付记录读取失败：' + errorText(error)
  }
}

async function loadUntaken(decision: 'defer' | 'exclude') {
  untakenError.value = ''
  try {
    const response = asRecord(await requestCandidates(props.taskId, { decision, limit: 100 }))
    const rows = asArray<Candidate>(response.items).map(toCandidateRow).filter(row => row.decision === decision)
    const total = num(response.total) ?? rows.length
    if (decision === 'defer') { deferredRows.value = rows; deferredTotal.value = total }
    else { excludedRows.value = rows; excludedTotal.value = total }
  } catch (error) {
    untakenError.value = '未纳入项读取失败：' + errorText(error)
  }
}

function locate(candidateId: string) {
  emit('back', candidateId ? { candidateId } : undefined)
}
function enterWorkspace() {
  if (delivery.value) emit('saved', delivery.value.ontologyId)
}

// ── 提交（原子创建草稿，幂等）────────────────────────────────────────────────
function requestIdFor(payloadName: string, token: string): string {
  const previous = attempt.value
  if (previous && previous.name === payloadName && previous.checkToken === token) return previous.requestId
  const requestId = crypto.randomUUID()
  attempt.value = { requestId, name: payloadName, checkToken: token }
  return requestId
}

async function submit() {
  if (busy.value || delivery.value) return
  const payloadName = name.value.trim()
  if (!payloadName) { formError.value = '请填写本体名称。'; return }
  if (!precheckOk.value) { formError.value = '当前预检未通过，请先回到评审处理阻断问题后再创建。'; return }
  if (!ack.value) { formError.value = '请先勾选确认：你已核对本次选定集合。'; return }
  busy.value = true
  formError.value = ''
  unknownOutcome.value = false
  const token = precheck.value ? precheck.value.checkToken : ''
  const requestId = requestIdFor(payloadName, token)
  try {
    const response = asRecord(await requestDeliver(props.taskId, payloadName, token, requestId))
    const ontologyId = str(response.ontologyId)
    if (!ontologyId) throw new Error('服务端未返回新本体 ID')
    delivery.value = { ontologyId, deliveredAt: str(response.deliveredAt), requestId }
    attempt.value = null
    await loadDelivery()
    await runPrecheck()
  } catch (error) {
    const status = error instanceof SaveRequestError ? error.status : 0
    const code = errorCode(error)
    if (status === 409 || status === 422) {
      if (code === 'DUPLICATE_NAME' || /同名|已存在/.test(errorText(error))) {
        formError.value = '当前账号已有同名本体：请修改名称后重新创建（不会覆盖或合并已有本体）。'
      } else if (/已交付|只能交付一次|幂等|已处理/.test(errorText(error))) {
        await loadDelivery()
        formError.value = '该提交已处理，请刷新查看结果。'
      } else {
        await loadDelivery()
        if (delivery.value) {
          formError.value = '该提交已处理，请刷新查看结果：本任务已有交付记录，不会重复创建本体。'
        } else {
          await runPrecheck()
          formError.value = '提交被拒绝：候选集合或批次基线已变化，checkToken 已失效。已重新预检，请核对后重试；输入与选择保留。'
        }
      }
    } else if (isOutcomeUnknown(error)) {
      unknownOutcome.value = true
      formError.value = '提交结果未知（' + errorText(error) + '）：请先「刷新交付结果」确认未创建；确认未创建后可「用同一 requestId 重试」。'
      await loadDelivery()
      if (delivery.value) formError.value = '该提交已处理，请刷新查看结果：本任务已有交付记录，不会重复创建本体。'
    } else {
      formError.value = '创建失败：' + errorText(error)
    }
  } finally {
    busy.value = false
  }
}

async function refreshDelivery() {
  refreshing.value = true
  await loadDelivery()
  await runPrecheck()
  refreshing.value = false
  formError.value = delivery.value ? '该任务已有交付记录，不会重复创建本体。' : '服务端暂无交付记录，可重试提交。'
}
</script>

<template>
<div class="build-save bs-root">
  <header class="bs-head">
    <div>
      <span class="eyebrow">从物料构建 · 保存新本体</span>
      <h2>保存为新本体</h2>
      <p class="muted">只写入本次选定的定义；暂缓与排除项继续留在生成任务中，可随时回来继续评审。</p>
    </div>
    <div class="tools">
      <button type="button" @click="locate('')">返回评审并处理</button>
    </div>
  </header>

  <p v-if="loading" class="muted">正在读取预检结果…</p>
  <p v-if="precheckError" class="inline-error" role="alert">{{ precheckError }}</p>
  <p v-if="deliveryError" class="inline-error" role="alert">{{ deliveryError }}</p>

  <!-- 已交付：只读展示，禁止再次提交（后端也会拒绝） -->
  <section v-if="delivery" class="card bs-delivered">
    <span class="eyebrow">已交付</span>
    <h3>已创建本体 {{ delivery.ontologyId }}</h3>
    <p class="muted">
      交付时间：{{ delivery.deliveredAt || '服务端未返回时间' }}
      · 本任务只能交付一次；需要新的资产请把任务复制为新任务后重新确认。
    </p>
    <p class="muted">它是未发布草稿：不修改任何已有本体，不含项目映射/连接/编排；来源依据保留在本次任务。</p>
    <div class="tools">
      <button type="button" class="primary" @click="enterWorkspace">进入对象建模</button>
    </div>
  </section>

  <template v-else>
    <div class="bs-grid">
      <!-- 左：本次纳入与检查 -->
      <section class="card bs-col" aria-label="本次纳入">
        <h3>本次纳入</h3>
        <div class="bs-metrics">
          <div v-for="key in TYPE_KEYS" :key="key" class="bs-metric">
            <small>{{ typeLabel(key) }}</small>
            <strong>{{ countOf(key) }}</strong>
          </div>
        </div>
        <p class="muted">合计选定 {{ totalSelected }} 项 · 暂缓 {{ deferredCount }} 项 · 已排除 {{ excludedCount }} 项</p>

        <div v-if="precheckError" class="notice bs-block bs-block-warn">预检未完成，创建入口保持禁用；请先处理预检读取失败（可重试或联系维护）。</div>
        <div v-else-if="!precheck" class="notice bs-block bs-block-warn">正在预检…</div>
        <div v-else-if="precheck.ok" class="notice bs-block bs-block-ok">预检通过（选定集合结构检查）；这不等于业务内容已验证，生成结果仍需人工完善。</div>
        <div v-else class="notice bs-block bs-block-bad">有 {{ blockingIssues.length }} 项阻断，不能创建本体。</div>

        <ul v-if="blockingIssues.length" class="bs-issues">
          <li v-for="(issue, index) in blockingIssues" :key="index">
            <span>{{ issue.message || issue.code || '服务端未给出说明' }}<small v-if="issue.candidateId" class="bs-issue-ref">候选 {{ issue.candidateId }}</small></span>
            <button v-if="issue.candidateId" type="button" class="row-link" @click="locate(issue.candidateId)">回到评审定位</button>
          </li>
        </ul>

        <h4 class="bs-sub">未纳入项（暂缓 / 排除）</h4>
        <div class="bs-tablewrap">
          <table>
            <thead><tr><th>定义</th><th>类型</th><th>证据状态</th><th>决定</th></tr></thead>
            <tbody>
              <tr v-for="row in errorRows" :key="row.id">
                <td>{{ row.name || row.id }}</td>
                <td>{{ typeLabel(row.type) }}</td>
                <td>{{ statusLabel(row.evidenceStatus) }}</td>
                <td>{{ decisionLabel(row.decision) }}</td>
              </tr>
              <tr v-if="!errorRows.length"><td colspan="4" class="muted">没有暂缓或排除项。</td></tr>
            </tbody>
          </table>
        </div>
        <p class="muted">暂缓 {{ deferredTotal }} 项 · 已排除 {{ excludedTotal }} 项；表格显示已载入的 {{ errorRows.length }} 项，完整内容在评审页查看。</p>
        <p v-if="untakenError" class="inline-error">{{ untakenError }}</p>

        <h4 class="bs-sub">材料覆盖</h4>
        <dl v-if="coverageRows.length" class="bs-coverage">
          <template v-for="row in coverageRows" :key="row.label">
            <dt>{{ row.label }}</dt>
            <dd>{{ row.text }}</dd>
          </template>
        </dl>
        <p v-else class="muted">本次预检未返回材料覆盖明细；材料解析缺口以任务与物料页的实际状态为准，不在此处补写推测内容。</p>

        <div class="tools bs-back">
          <button type="button" @click="locate('')">返回评审并处理</button>
        </div>
      </section>

      <!-- 右：新本体信息与提交 -->
      <section class="card bs-col" aria-label="新本体信息">
        <h3>新本体信息</h3>
        <label class="editor-field">
          <span class="field-title">本体名称 <b class="required-mark">*</b></span>
          <input v-model="name" type="text" aria-label="本体名称" placeholder="例如：储能本体" :disabled="busy">
          <small class="field-help">同名时服务端会拒绝（409 DUPLICATE_NAME），请改名后重试；不会覆盖或合并已有本体。</small>
        </label>

        <div class="notice bs-block bs-scope">
          仅创建未发布草稿；不修改任何已有本体；不生成项目映射/连接/编排；来源依据保留在本次任务。
        </div>

        <label class="check-option">
          <input v-model="ack" type="checkbox" :disabled="busy">
          <span>我已核对本次选定集合，了解生成结果仍需人工完善，暂缓项不会写入本体。</span>
        </label>

        <p v-if="formError" class="inline-error bs-error" role="alert">{{ formError }}</p>
        <p v-else-if="attempt" class="note">{{ retryHint }}</p>

        <div class="tools bs-submit">
          <button type="button" class="primary" :disabled="busy || !precheckOk || !ack || !name.trim()" @click="submit">
            {{ busy ? '正在创建…' : '创建新本体草稿' }}
          </button>
          <button v-if="attempt" type="button" :disabled="busy" @click="submit">用同一 requestId 重试</button>
          <button type="button" :disabled="busy || refreshing" @click="refreshDelivery">{{ refreshing ? '刷新中…' : '刷新交付结果' }}</button>
          <button type="button" :disabled="busy" @click="runPrecheck">重新预检</button>
        </div>

        <ul class="bs-hint">
          <li>提交前服务端会重新校验基线、选定集合与结构；失败全部回滚，不会先建空本体。</li>
          <li>重复点击、超时重试只创建一个本体；任务修订过期时保留你的选择与输入。</li>
          <li>成功后继续编辑由现有对象建模负责，任务结果不再自动同步到本体。</li>
        </ul>
      </section>
    </div>
  </template>
</div>
</template>

<style scoped>
.bs-root{display:block}
.bs-head{display:flex;align-items:flex-start;justify-content:space-between;gap:14px;flex-wrap:wrap;margin-bottom:14px}
.bs-head h2{margin:4px 0 0}
.bs-head p{margin:6px 0 0}
.bs-grid{display:grid;grid-template-columns:minmax(0,1.2fr) minmax(330px,.8fr);gap:18px;align-items:start}
.bs-col{margin-bottom:0;min-width:0}
.bs-metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(96px,1fr));gap:10px;margin-bottom:10px}
.bs-metric{background:var(--paper-2);border-radius:var(--r-sm);padding:11px 13px}
.bs-metric small{display:block;font-size:12px;color:var(--muted)}
.bs-metric strong{font-size:21px;display:block;font-variant-numeric:tabular-nums}
.bs-block{margin:12px 0;padding:11px 14px;border-radius:var(--r-sm);font-size:13px}
.bs-block-ok{background:var(--ok-soft);border:1px solid var(--ok-line);color:var(--ok)}
.bs-block-warn{background:var(--warn-soft);border:1px solid var(--warn-line);color:var(--warn)}
.bs-block-bad{background:var(--danger-soft);border:1px solid var(--danger-line);color:var(--danger)}
.bs-scope{background:var(--blue-soft);border:1px solid var(--blue-line);color:var(--muted)}
.bs-issues{list-style:none;margin:10px 0;padding:0}
.bs-issues li{display:flex;align-items:center;gap:8px;flex-wrap:wrap;border-left:3px solid var(--danger-line);background:var(--danger-soft);padding:9px 12px;margin:6px 0;font-size:13px;color:var(--danger)}
.bs-issue-ref{display:block;font-size:11px;color:var(--muted);margin-top:2px}
.bs-tablewrap{overflow:auto;margin:8px 0}
.bs-tablewrap table{font-size:13px}
.bs-sub{font-size:14px;margin:18px 0 8px}
.bs-coverage{display:grid;grid-template-columns:150px 1fr;margin:0;font-size:13px}
.bs-coverage dt{color:var(--muted);padding:7px 0;border-bottom:1px solid var(--paper-3)}
.bs-coverage dd{margin:0;padding:7px 0;border-bottom:1px solid var(--paper-3);overflow-wrap:anywhere}
.bs-back{margin-top:16px}
.bs-delivered{margin-bottom:16px}
.bs-delivered h3{margin:6px 0}
.bs-delivered .tools{margin-top:6px}
.bs-error{margin:10px 0}
.bs-submit{margin-top:14px;flex-wrap:wrap}
.bs-hint{margin:14px 0 0;padding-left:18px;color:var(--muted);font-size:12px;line-height:1.8}
@media(max-width:1000px){
  .bs-grid{grid-template-columns:1fr}
}
</style>
