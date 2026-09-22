<!-- A02 · 物料清单与分片上传（交互原型 `文档/需求/20260920_从物料自动构建本体/交互原型_v1.html` 的 materialPage()）。
     契约：08 分册 §4（分片上传与物料）/ §5（运行轮询）。挂载点由 App 路由决定；
     props {taskId}，emits ['continue','back']。
     上传为真实实现：File.slice 切片 → SHA-256（分片 + 整文件）→ base64 → 顺序上传 → complete；
     失败重试与上传中取消都保留用户已选文件，不丢输入。
     继续条件（与需求一致）：至少一份未排除且解析成功的材料；存在解析缺口时必须勾选确认。 -->
<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  abortUpload, conflictRevision, errorMessage, fetchRun, listMaterials, materialRevisionToken, retryMaterial,
  scanMaterials, setMaterialExcluded, uploadFile,
} from './api'
import {
  KIND_LOCATOR_LABELS, PARSE_STATE_TONE, RUN_STATE_LABELS, RUN_STATE_TONE, STAGE_LABELS, coverageOf,
  formatBytes, labelOf, materialKindLabel, parseStateLabel, runErrorText, toneOf,
  type BuildRun, type Material, type RunStage, type RunState,
} from './types'

const props = defineProps<{ taskId: string }>()
const emit = defineEmits<{ (e: 'continue'): void; (e: 'back'): void }>()

const STEP_NAMES = ['物料', '确定范围', '生成', '评审初稿', '保存新本体']
const STAGES: RunStage[] = ['retrieve', 'align', 'abstract', 'verify', 'adapt']
const TERMINAL: RunState[] = ['succeeded', 'failed', 'cancelled', 'interrupted']
const RUN_KIND_SCAN = 'scan'

// ─── 清单 ────────────────────────────────────────────────────────────────
const materials = ref<Material[]>([])
const materialRevision = ref(0)
const loading = ref(true)
const listError = ref('')
const notice = ref('')
const actionError = ref('')
const expandedId = ref('')
const pendingId = ref('')
const retryingId = ref('')
const ack = ref(false)

const rows = computed(() => materials.value.map(m => {
  const coverage = coverageOf(m)
  return {
    m, coverage,
    kindLabel: materialKindLabel(m.kind),
    locator: labelOf(KIND_LOCATOR_LABELS, m.kind, '待识别'),
    sizeText: formatBytes(m.size),
    stateLabel: parseStateLabel(m.parseState),
    tone: toneOf(PARSE_STATE_TONE, m.parseState, 'info'),
    failedCount: coverage.failedSegments.length,
  }
}))
const activeCount = computed(() => materials.value.filter(m => !m.excluded).length)

async function loadMaterials() {
  loading.value = true; listError.value = ''
  try {
    const r = await listMaterials(props.taskId)
    materials.value = r.items
    materialRevision.value = typeof r.revision === 'number' ? r.revision : 0
  } catch (e) {
    materials.value = []; listError.value = errorMessage(e)
  } finally { loading.value = false }
}

// ─── 上传队列（逐文件进度 + 取消 + 重试） ─────────────────────────────────
type UploadRowState = 'queued' | 'uploading' | 'done' | 'failed' | 'cancelled'
interface UploadRow {
  key: string
  file: File
  relPath: string
  size: number
  state: UploadRowState
  sentBytes: number
  phase: string
  chunkIndex: number
  chunkCount: number
  error: string
  uploadId: string
  controller: AbortController
}
const uploads = ref<UploadRow[]>([])
const busy = ref(false)
const dragging = ref(false)
let uploadSeq = 0

const queue = computed(() => {
  const list = uploads.value
  const total = list.reduce((n, r) => n + r.size, 0)
  const sent = list.reduce((n, r) => n + r.sentBytes, 0)
  const active = list.find(r => r.state === 'uploading') || null
  return {
    total, sent, count: list.length,
    done: list.filter(r => r.state === 'done').length,
    failed: list.filter(r => r.state === 'failed').length,
    finished: list.some(r => r.state === 'done' || r.state === 'failed' || r.state === 'cancelled'),
    active,
    percent: total > 0 ? Math.round(sent / total * 100) : 0,
    hasWork: list.some(r => r.state === 'queued' || r.state === 'uploading'),
  }
})

function addFiles(files: File[]) {
  const seen = new Set<string>([...materials.value.map(m => m.relPath), ...uploads.value.map(u => u.relPath)])
  const added: UploadRow[] = []
  let empty = 0, dup = 0
  for (const file of files) {
    const relPath = file.webkitRelativePath || file.name
    if (!relPath) continue
    if (file.size <= 0) { empty += 1; continue }
    if (seen.has(relPath)) { dup += 1; continue }
    seen.add(relPath)
    uploadSeq += 1
    added.push({
      key: 'u' + uploadSeq, file, relPath, size: file.size, state: 'queued', sentBytes: 0,
      phase: 'hashing', chunkIndex: 0, chunkCount: 0, error: '', uploadId: '', controller: new AbortController(),
    })
  }
  if (added.length) uploads.value = [...uploads.value, ...added]
  const skipped = [empty ? empty + ' 个空文件已跳过' : '', dup ? dup + ' 个重复路径已跳过' : ''].filter(Boolean).join('，')
  notice.value = (added.length ? '已加入 ' + added.length + ' 个文件' : '没有新增文件') + (skipped ? '（' + skipped + '）' : '')
  if (added.length) void startQueue()
}

async function syncMaterialsAfterUpload() {
  try {
    const r = await listMaterials(props.taskId)
    materials.value = r.items
    materialRevision.value = typeof r.revision === 'number' ? r.revision : materialRevision.value
  } catch { /* 清单读取失败不覆盖上传结果；用户可手动刷新 */ }
}

async function runRow(row: UploadRow) {
  row.state = 'uploading'; row.error = ''; row.sentBytes = 0
  row.controller = new AbortController()
  try {
    await uploadFile(props.taskId, row.file, {
      relPath: row.relPath,
      signal: row.controller.signal,
      onUploadId: (id) => { row.uploadId = id },
      onProgress: (p) => {
        row.phase = p.phase
        row.sentBytes = p.sentBytes
        row.chunkIndex = p.chunkIndex
        row.chunkCount = p.chunkCount
      },
    })
    row.state = 'done'; row.sentBytes = row.size
    await syncMaterialsAfterUpload()
  } catch (e) {
    row.state = row.controller.signal.aborted ? 'cancelled' : 'failed'
    row.error = errorMessage(e)
    if (row.state === 'cancelled') row.error = '已取消上传，请在材料清单中确认没有残留的半份材料。'
  }
}

async function startQueue() {
  if (busy.value) return
  busy.value = true
  try {
    // 每轮取当前「队列中」的第一项：上传期间新加入的文件由同一循环继续处理，不会漏传
    for (;;) {
      const row = uploads.value.find(r => r.state === 'queued')
      if (!row) break
      await runRow(row)
      if (row.state === 'queued') break   // 防御：状态未推进时退出，避免死循环
    }
  } finally { busy.value = false }
}

async function retryUpload(row: UploadRow) {
  if (busy.value || row.state === 'uploading') return
  row.state = 'queued'; row.error = ''; row.sentBytes = 0
  await startQueue()
}

async function cancelUpload(row: UploadRow) {
  if (row.state !== 'uploading') return
  row.controller.abort()
  const id = row.uploadId
  if (id) {
    try { await abortUpload(id) } catch { /* 服务端临时内容有界清理；上传已本地取消 */ }
  }
}

function clearFinished() { uploads.value = uploads.value.filter(r => r.state === 'uploading' || r.state === 'queued') }

function onPick(e: Event) {
  const input = e.target as HTMLInputElement
  addFiles(Array.from(input.files || []))
  input.value = ''   // 允许再次选择同一批文件
}
const fileInput = ref<HTMLInputElement | null>(null)
const dirInput = ref<HTMLInputElement | null>(null)
function pickFile() { fileInput.value?.click() }
function pickDir() { dirInput.value?.click() }

function onDrop(e: DragEvent) {
  dragDepth = 0
  dragging.value = false
  const list = e.dataTransfer?.files
  if (!list || !list.length) { notice.value = '未读取到文件；要整目录上传请用「选择文件夹」。'; return }
  addFiles(Array.from(list))
}
// 拖拽高亮用进入/离开深度计数：子元素上触发的 dragleave 不会把高亮提前清掉（原型同款反馈）
let dragDepth = 0
function onDragEnter() { dragDepth += 1; dragging.value = true }
function onDragLeave() { dragDepth = Math.max(0, dragDepth - 1); if (dragDepth === 0) dragging.value = false }
const phaseText = (row: UploadRow): string => {
  if (row.state === 'queued') return '排队中'
  if (row.state === 'done') return '已上传'
  if (row.state === 'failed') return '上传失败'
  if (row.state === 'cancelled') return '已取消'
  if (row.phase === 'hashing') return '计算校验值'
  if (row.phase === 'completing') return '登记材料'
  return row.chunkCount > 0 ? '上传分片 ' + row.chunkIndex + '/' + row.chunkCount : '上传中'
}

// ─── 材料操作：排除/恢复、重试解析 ───────────────────────────────────────
async function toggleExcluded(m: Material) {
  if (pendingId.value) return
  pendingId.value = m.id; actionError.value = ''
  try {
    // 08 §4：排除/恢复携带的是「物料清单修订」的不透明字符串 token，服务端按 str(materialRevision) 比对；
    // 直接传数字会被参数校验拒为 400（D05）。
    const r = await setMaterialExcluded(props.taskId, m.id, !m.excluded, materialRevisionToken(materialRevision.value))
    materials.value = materials.value.map(x => x.id === r.material.id ? r.material : x)
    if (r.task && typeof r.task.materialRevision === 'number') materialRevision.value = r.task.materialRevision
    else materialRevision.value += 1
    notice.value = (r.material.excluded ? '已排除' : '已恢复') + '「' + r.material.relPath + '」；材料清单已变化，此前的扫描与生成结果基线失效。'
    ack.value = false
  } catch (e) {
    if (conflictRevision(e) !== null) {
      actionError.value = '材料清单已被其他操作更新，已刷新最新清单，请重新执行本次操作。'
      await loadMaterials()
    } else actionError.value = errorMessage(e)
  } finally { pendingId.value = '' }
}

async function retryOne(m: Material) {
  if (retryingId.value) return
  retryingId.value = m.id; actionError.value = ''
  try {
    const r = await retryMaterial(props.taskId, m.id)
    notice.value = '已重新提交解析：「' + m.relPath + '」'
    await watchRun(r.runId)
  } catch (e) { actionError.value = errorMessage(e) } finally { retryingId.value = '' }
}

// ─── 扫描与阶段进度（fetchRun 轮询，2 秒一次；页面卸载即停止） ───────────
const runId = ref('')
const run = ref<BuildRun | null>(null)
const runError = ref('')
const scanStarting = ref(false)
let timer: ReturnType<typeof setInterval> | null = null
let failStreak = 0

const isScanning = computed(() => !!run.value && (run.value.state === 'running' || run.value.state === 'queued'))
const visibleStages = computed<RunStage[]>(() => run.value?.kind === RUN_KIND_SCAN ? STAGES.slice(0, 2) : STAGES)
const stageIndex = computed(() => run.value && run.value.stage ? visibleStages.value.indexOf(run.value.stage as RunStage) : -1)
const runKindLabel = computed(() => run.value ? (run.value.kind === RUN_KIND_SCAN ? '材料扫描' : run.value.kind === 'dialog' ? '范围对话' : '生成初稿') : '')

function stopTimer() { if (timer) { clearInterval(timer); timer = null } }
function startTimer() {
  if (timer) return
  timer = setInterval(() => { void refreshRun() }, 2000)
}

/** probe=true 为页面初次探测：没有历史运行是正常情况（不留错误提示）。 */
async function refreshRun(probe = false) {
  try {
    const r = await fetchRun(props.taskId, runId.value || undefined)
    const next = r.run as BuildRun | null
    if (!next) {   // 该任务还没有任何运行：如实显示「尚无运行」，不报错
      run.value = null
      if (probe) runError.value = ''
      stopTimer()
      return
    }
    run.value = next
    runId.value = next.id
    failStreak = 0
    runError.value = ''
    if (TERMINAL.includes(next.state)) {
      stopTimer()
      await loadMaterials()   // 终态后刷新清单，让解析状态与覆盖摘要落地
    }
  } catch (e) {
    if (probe) { run.value = null; runError.value = ''; return }   // 无运行记录时的 404 不打扰用户
    failStreak += 1
    runError.value = errorMessage(e)
    if (failStreak >= 3) { stopTimer(); runError.value += '（已停止自动刷新，可点「刷新状态」重试）' }
  }
}

async function watchRun(id: string) {
  runId.value = id
  failStreak = 0
  await refreshRun()
  if (run.value && !TERMINAL.includes(run.value.state)) startTimer()
}

async function startScan() {
  if (scanStarting.value || isScanning.value) return
  scanStarting.value = true; actionError.value = ''
  try {
    const r = await scanMaterials(props.taskId)
    notice.value = '已开始扫描材料：单份材料失败不会阻塞其余材料。'
    await watchRun(r.runId)
  } catch (e) { actionError.value = errorMessage(e) } finally { scanStarting.value = false }
}

async function init() {
  await loadMaterials()
  // runId 省略即取最近一次运行：服务端重启丢执行会返回 interrupted，页面如实显示（不假称仍在运行）
  await refreshRun(true)
  if (run.value && !TERMINAL.includes(run.value.state)) startTimer()
}
onMounted(() => {
  if (dirInput.value) dirInput.value.webkitdirectory = true
  void init()
})
onBeforeUnmount(stopTimer)
watch(() => props.taskId, () => {
  stopTimer()
  // 换任务：中止仍在进行的上传（避免旧任务的文件被登记进新任务），并清空页面上下文
  for (const row of uploads.value) { if (row.state === 'uploading' || row.state === 'queued') row.controller.abort() }
  run.value = null; runId.value = ''; runError.value = ''; ack.value = false; expandedId.value = ''
  uploads.value = []
  void init()   // 上传循环（若有）结束后会从新队列继续，无需重置 busy
})

// ─── 继续条件 ────────────────────────────────────────────────────────────
const successCount = computed(() => materials.value.filter(m => !m.excluded && m.parseState === 'success').length)
const gapCount = computed(() => materials.value.filter(m => !m.excluded && (m.parseState === 'partial' || m.parseState === 'failed')).length)
const scannedAnything = computed(() => materials.value.some(m => m.parseState !== 'pending'))
const hasGap = computed(() => gapCount.value > 0)
const canContinue = computed(() => successCount.value > 0 && (!hasGap.value || ack.value))
const blockedReason = computed(() => {
  if (listError.value) return '材料清单读取失败，请先重试。'
  if (!materials.value.length) return '还没有材料：先上传材料并扫描。'
  if (successCount.value === 0) return hasGap.value ? '至少需要一份未排除且解析成功的材料才能继续。' : '还没有解析成功的材料：先点击「扫描物料」。'
  if (hasGap.value && !ack.value) return '存在解析缺口：请先勾选「我了解材料覆盖缺口」，或重试/排除有问题的材料。'
  return ''
})
</script>

<template>
<section class="bt-page">
  <div class="bt-intro">
    <div>
      <h2>添加物料</h2>
      <p class="muted">材料可以覆盖多个业务。先扫描识别内容，再通过对话确定本次范围。</p>
    </div>
    <button type="button" @click="emit('back')">← 生成任务</button>
  </div>

  <div class="steps-bar" role="list" aria-label="生成流程">
    <span v-for="(name, i) in STEP_NAMES" :key="name" class="step" :class="{ active: i === 0 }" role="listitem">
      <span class="step-dot">{{ i + 1 }}</span>{{ name }}
    </span>
  </div>

  <p v-if="notice" class="bt-note" role="status">{{ notice }}</p>
  <p v-if="actionError" class="inline-error" role="alert">{{ actionError }}</p>

  <!-- 上传区：拖拽或选择文件/文件夹；逐文件进度、失败重试、上传中取消 -->
  <section class="card">
    <div class="panelhead">
      <h2>上传材料 <span class="muted">单文件与任务总量上限见任务页「能力与限额」</span></h2>
      <div class="tools">
        <button type="button" @click="pickDir">选择文件夹</button>
        <button type="button" class="primary" @click="pickFile">选择文件</button>
      </div>
    </div>
    <div
      class="bt-drop" :class="{ 'bt-drop-on': dragging }"
      @dragenter.prevent="onDragEnter" @dragover.prevent="dragging = true"
      @dragleave.prevent="onDragLeave" @drop.prevent="onDrop">
      <strong>前后端代码 · 数据库表结构 · 需求文档</strong>
      <p class="muted">文件夹 / ZIP、SQL、DOCX、PDF、XLSX、MD；旧版 Office 格式建议先转换。可直接把文件拖到这里。</p>
      <p class="bt-sub">分片上传：按服务端给定分片大小切片并逐片校验 SHA-256，整文件哈希一致才会登记为材料。</p>
    </div>
    <input ref="fileInput" class="bt-hidden-input" type="file" multiple accept=".zip,.sql,.docx,.pdf,.xlsx,.md,.java,.py,.ts,.tsx,.js,.jsx,.vue,.json,.xml,.yaml,.yml,.txt,.csv" @change="onPick">
    <input ref="dirInput" class="bt-hidden-input" type="file" multiple @change="onPick">

    <template v-if="queue.count">
      <div class="bt-qhead">
        <div>
          <strong>上传进度</strong>
          <span class="muted">共 {{ queue.count }} 个文件 · 已完成 {{ queue.done }} · 失败 {{ queue.failed }}</span>
        </div>
        <div class="bt-qahead">
          <span class="bt-sub">{{ formatBytes(queue.sent) }} / {{ formatBytes(queue.total) }}（{{ queue.percent }}%）</span>
          <button v-if="queue.finished" type="button" class="mini" @click="clearFinished">清理已结束</button>
        </div>
      </div>
      <div class="bt-bar" role="progressbar" :aria-valuenow="queue.percent" aria-valuemin="0" aria-valuemax="100" :aria-label="'上传总进度 ' + queue.percent + '%'">
        <i :style="{ width: queue.percent + '%' }"></i>
      </div>
      <p class="bt-sub">
        <template v-if="queue.active">当前文件：{{ queue.active.relPath }} · {{ phaseText(queue.active) }}</template>
        <template v-else-if="queue.hasWork">正在准备上传…</template>
        <template v-else>上传已结束。</template>
      </p>
      <div class="bt-tablewrap">
        <table class="bt-table">
          <thead><tr><th scope="col">文件</th><th scope="col">大小</th><th scope="col">进度</th><th scope="col" class="bt-col-ops">操作</th></tr></thead>
          <tbody>
            <tr v-for="row in uploads" :key="row.key">
              <td>
                <strong>{{ row.relPath }}</strong>
                <small v-if="row.error" class="bt-sub bt-err-text">{{ row.error }}</small>
              </td>
              <td>{{ formatBytes(row.size) }}</td>
              <td>
                <span class="bt-sub">{{ phaseText(row) }}</span>
                <div class="bt-bar bt-bar-sm"><i :style="{ width: (row.size ? Math.round(row.sentBytes / row.size * 100) : 0) + '%' }"></i></div>
              </td>
              <td class="bt-ops">
                <button v-if="row.state === 'uploading'" type="button" class="row-link" @click="cancelUpload(row)">取消</button>
                <button v-else-if="row.state === 'failed' || row.state === 'cancelled'" type="button" class="row-link" :disabled="busy" @click="retryUpload(row)">重试</button>
                <span v-else-if="row.state === 'done'" class="muted bt-sub">已完成</span>
                <span v-else class="muted bt-sub">等待中</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
  </section>

  <!-- 扫描与阶段进度 -->
  <section v-if="run || runError" class="card">
    <div class="panelhead">
      <h2>{{ runKindLabel || '运行状态' }} <span class="muted">{{ run?.stageLabel || '' }}</span></h2>
      <div class="tools">
        <span v-if="run" class="status-pill" :class="'bt-pill-' + toneOf(RUN_STATE_TONE, run.state)">{{ labelOf(RUN_STATE_LABELS, run.state, '未知状态') }}</span>
        <button type="button" class="mini" @click="refreshRun(false)">刷新状态</button>
      </div>
    </div>
    <p v-if="runError" class="inline-error" role="alert">读取运行状态失败：{{ runError }}</p>
    <ol v-if="run" class="bt-stagebar">
      <li v-for="(stage, i) in visibleStages" :key="stage" :class="{ done: stageIndex > i, current: stageIndex === i && !['succeeded'].includes(run.state) }">
        <span class="bt-dot">{{ stageIndex > i ? '✓' : i + 1 }}</span>
        <span>{{ labelOf(STAGE_LABELS, stage, stage) }}</span>
      </li>
    </ol>
    <p v-if="run" class="bt-sub">
      进度 {{ run.progress?.done ?? 0 }} / {{ run.progress?.total ?? 0 }} · 第 {{ run.attempt }} 次尝试
      <template v-if="run.usage?.calls"> · 模型调用 {{ run.usage.calls }} 次</template>
    </p>
    <p v-if="run && run.state === 'interrupted'" class="bt-note warn">服务重启导致本次执行中断，不会自动重发；可重新扫描。</p>
    <p v-if="run && (run.state === 'failed' || run.state === 'cancelled')" class="bt-note bad">
      {{ runErrorText(run) || '本次运行未完成。' }}
    </p>
    <p v-if="run && run.state === 'succeeded'" class="bt-note" :class="{ warn: hasGap }">
      扫描结束：{{ successCount }} 份材料解析成功，{{ gapCount }} 份存在覆盖缺口{{ hasGap ? '（缺口未确认前不能继续）' : '' }}。
    </p>
  </section>

  <!-- 物料清单 -->
  <section class="card">
    <div class="panelhead">
      <h2>物料清单 <span class="muted">{{ materials.length }} 份 · 参与建模 {{ activeCount }} 份 · 清单修订 {{ materialRevision }}</span></h2>
      <button type="button" class="mini" @click="loadMaterials">刷新</button>
    </div>
    <p v-if="listError" class="inline-error" role="alert">
      读取物料清单失败：{{ listError }} <button type="button" class="mini" @click="loadMaterials">重试</button>
    </p>
    <p v-else-if="loading" class="muted">读取中…</p>
    <div v-else-if="!materials.length" class="empty-state">
      <p><strong>还没有材料。</strong></p>
      <p>先在上方添加代码、表结构或需求文档（支持整个文件夹与 ZIP），再点击「扫描物料」识别内容。</p>
      <button type="button" class="primary" @click="pickFile">选择文件</button>
      <button type="button" @click="pickDir">选择文件夹</button>
    </div>
    <div v-else class="bt-tablewrap">
      <table class="bt-table bt-table-wide">
        <thead>
          <tr>
            <th scope="col">文件</th><th scope="col">类型</th><th scope="col">大小</th><th scope="col">解析状态</th>
            <th scope="col">识别模块</th><th scope="col">定位方式</th><th scope="col">纳入片段</th><th scope="col">失败片段</th>
            <th scope="col" class="bt-col-ops">操作</th>
          </tr>
        </thead>
        <tbody>
          <template v-for="row in rows" :key="row.m.id">
            <tr :class="{ 'bt-row-excluded': row.m.excluded }">
              <td>
                <strong>{{ row.m.relPath }}</strong>
                <small v-if="row.m.sourceGroup" class="bt-sub">同源组 {{ row.m.sourceGroup }}</small>
              </td>
              <td>{{ row.kindLabel }}</td>
              <td>{{ row.sizeText }}</td>
              <td><span class="status-pill" :class="'bt-pill-' + row.tone">{{ row.stateLabel }}</span></td>
              <td>{{ row.coverage.modules.length ? row.coverage.modules.join('、') : '待识别' }}</td>
              <td>{{ row.m.excluded ? '—' : row.locator }}</td>
              <td>{{ row.coverage.factCount }}</td>
              <td :class="{ 'bt-err-text': row.failedCount > 0 }">{{ row.failedCount }}</td>
              <td class="bt-ops">
                <button type="button" class="row-link" :disabled="pendingId === row.m.id" @click="toggleExcluded(row.m)">
                  {{ row.m.excluded ? '恢复' : '排除' }}
                </button>
                <button type="button" class="row-link" :disabled="retryingId === row.m.id || row.m.excluded" @click="retryOne(row.m)">重试解析</button>
                <button type="button" class="row-link" :aria-expanded="expandedId === row.m.id" @click="expandedId = expandedId === row.m.id ? '' : row.m.id">
                  {{ expandedId === row.m.id ? '收起问题' : '查看问题' }}
                </button>
              </td>
            </tr>
            <tr v-if="expandedId === row.m.id" class="bt-gap-row">
              <td colspan="9">
                <div class="bt-gap">
                  <p v-if="row.m.error" class="inline-error" role="alert">解析错误：{{ row.m.error }}</p>
                  <p v-if="!row.coverage.notes.length && !row.coverage.failedSegments.length && !(row.coverage.excludedPaths || []).length && !row.m.error" class="muted">
                    服务端未报告解析缺口{{ row.m.parseState === 'success' ? '（该材料已完整解析）' : row.m.excluded ? '（该材料已排除）' : '' }}。
                  </p>
                  <div v-if="row.coverage.notes.length">
                    <strong>解析说明</strong>
                    <ul><li v-for="(n, ni) in row.coverage.notes" :key="'note' + ni">{{ n }}</li></ul>
                  </div>
                  <div v-if="row.coverage.failedSegments.length">
                    <strong>未解析片段（不能当成空内容忽略）</strong>
                    <ul><li v-for="(seg, si) in row.coverage.failedSegments" :key="'seg' + si">{{ seg }}</li></ul>
                  </div>
                  <div v-if="(row.coverage.excludedPaths || []).length">
                    <strong>默认排除的路径</strong>
                    <ul><li v-for="(p, pi) in row.coverage.excludedPaths" :key="'p' + pi">{{ p }}</li></ul>
                  </div>
                  <details class="bt-policy">
                    <summary>默认排除与解析边界</summary>
                    <p>依赖目录、构建产物、凭据文件默认排除（例如 node_modules、dist、.git、大体积二进制与密钥文件）；被排除的路径不计入证据，也不视为已分析。</p>
                    <p>材料不会被“执行”：代码与 SQL 只做结构与语义识别，不运行、不连库；未识别出的技术栈不得宣称结构分析完整。</p>
                    <p>解析成功不等于业务定义正确：所有候选定义仍需人工评审；覆盖缺口必须显式确认后才能进入确定范围。</p>
                  </details>
                </div>
              </td>
            </tr>
          </template>
        </tbody>
      </table>
    </div>

    <!-- 缺口确认与继续 -->
    <template v-if="hasGap">
      <p class="bt-note warn">
        有 {{ gapCount }} 份材料存在覆盖缺口（未解析片段）。缺口内容不会被当成空内容，也不会自动纳入定义。
      </p>
      <label class="check-option">
        <input v-model="ack" type="checkbox">
        <span>我了解材料覆盖缺口，允许先用已解析部分继续。</span>
      </label>
    </template>
    <p v-if="materials.length && !scannedAnything" class="bt-note">还没有扫描：先点击下方「扫描物料」识别材料内容。</p>
  </section>

  <div class="bt-footer">
    <small class="muted">至少一份未排除且解析成功的材料才允许继续；失败不能当成空内容忽略。</small>
    <div class="bt-footer-tools">
      <button type="button" :disabled="!activeCount || isScanning || scanStarting" @click="startScan">{{ isScanning ? '扫描中…' : scanStarting ? '提交中…' : '扫描物料' }}</button>
      <button type="button" class="primary" :disabled="!canContinue" :title="blockedReason || '进入确定范围'" @click="emit('continue')">继续确定范围 →</button>
    </div>
  </div>
  <p v-if="blockedReason" class="bt-sub" role="status">{{ blockedReason }}</p>
</section>
</template>

<style scoped>
/* 复用全局令牌与通用类（.card/.panelhead/.steps-bar/.status-pill/.check-option/.empty-state/.modal-*），
   其余为局部 scoped 样式，不新增全局规则 */
.bt-page{display:block}
.bt-intro{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;flex-wrap:wrap;margin-bottom:12px}
/* 页内标题降为 h2（顶栏已有页面级 h1）：字号/字重/行距逐值保持原 h1 渲染结果，
   font-weight 显式写 700 是因为全局 h2 规则会把 UA 的 bold 拉到 650 */
.bt-intro h2{font-size:22px;font-weight:700;margin:0 0 4px}
.bt-intro p{margin:0}
.bt-sub{display:block;font-size:12px;color:var(--muted);margin-top:4px}
.bt-note{padding:11px 14px;border:1px solid var(--blue-line);background:var(--blue-soft);border-radius:var(--r-sm);font-size:13px;color:var(--ink-2);margin:12px 0}
.bt-note.warn{border-color:var(--warn-line);background:var(--warn-soft);color:var(--warn)}
.bt-note.bad{border-color:var(--danger-line);background:var(--danger-soft);color:var(--danger)}
.bt-err-text{color:var(--danger)}
.bt-drop{border:1px dashed var(--line-2);background:var(--paper-2);border-radius:var(--r-md);text-align:center;padding:22px;margin:14px 0}
.bt-drop-on{border-color:var(--blue);background:var(--blue-soft)}
.bt-drop p{margin:6px 0}
/* 原生文件选择控件：视觉隐藏但可被按钮程序化点击（display:none 在部分浏览器会拒绝触发选择框） */
.bt-hidden-input{position:absolute;width:1px;height:1px;padding:0;margin:-1px;border:0;opacity:0;pointer-events:none}
.bt-qhead{display:flex;align-items:flex-end;justify-content:space-between;gap:12px;flex-wrap:wrap;margin:16px 0 8px}
.bt-qhead strong{margin-right:8px}
.bt-qahead{display:flex;align-items:center;gap:10px}
.bt-bar{height:6px;background:var(--paper-3);border-radius:var(--r-pill);overflow:hidden;margin:6px 0}
.bt-bar>i{display:block;height:100%;background:var(--blue);transition:width var(--dur) var(--ease)}
.bt-bar-sm{height:4px;margin:4px 0 0}
.bt-tablewrap{overflow:auto}
.bt-table{min-width:760px}
.bt-table-wide{min-width:1180px}
.bt-col-ops{width:22%}
.bt-ops{white-space:nowrap}
.bt-ops .row-link+.row-link{margin-left:8px}
.bt-row-excluded td{background:var(--paper-2);color:var(--muted)}
.bt-gap-row td{background:var(--paper-2)}
.bt-gap{font-size:13px}
.bt-gap strong{display:block;margin:10px 0 4px}
.bt-gap ul{margin:0;padding-left:20px}
.bt-gap li{margin:2px 0;overflow-wrap:anywhere}
.bt-policy{margin-top:12px}
.bt-policy summary{cursor:pointer;color:var(--muted);font-size:13px}
.bt-policy p{margin:6px 0;color:var(--ink-2);font-size:13px}
.bt-stagebar{list-style:none;display:flex;gap:16px;padding:0;margin:6px 0 4px;flex-wrap:wrap}
.bt-stagebar li{display:flex;align-items:center;gap:8px;font-size:13px;color:var(--muted)}
.bt-stagebar li.current{color:var(--ink);font-weight:600}
.bt-stagebar li.done{color:var(--ok)}
.bt-dot{display:inline-flex;align-items:center;justify-content:center;width:22px;height:22px;border-radius:50%;border:1px solid var(--line);background:var(--paper);font-size:12px;flex:none}
.bt-stagebar li.current .bt-dot{border-color:var(--blue);background:var(--blue);color:var(--paper)}
.bt-stagebar li.done .bt-dot{border-color:var(--ok-line);background:var(--ok-soft);color:var(--ok)}
.bt-pill-ok{background:var(--ok-soft);border-color:var(--ok-line);color:var(--ok)}
.bt-pill-warn{background:var(--warn-soft);border-color:var(--warn-line);color:var(--warn)}
.bt-pill-bad{background:var(--danger-soft);border-color:var(--danger-line);color:var(--danger)}
.bt-pill-info{background:var(--blue-soft);border-color:var(--blue-line);color:var(--blue-ink)}
.bt-footer{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;background:var(--paper);border:1px solid var(--line);border-radius:var(--r-md);padding:14px 18px;margin-top:16px}
.bt-footer-tools{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
</style>
