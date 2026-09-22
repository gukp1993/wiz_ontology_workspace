<!-- A01 · 从物料生成任务的列表页（交互原型 `文档/需求/20260920_从物料自动构建本体/交互原型_v1.html` 的 tasks()）。
     挂载点由 App 路由决定（本页不注册路由、不依赖 App 改动）：props 无，emits ['open-task','enter-ontology']。
     信息层级对齐原型：能力限额条 → 任务列表（空态为 hero + 四步说明）→ 新建/删除弹窗。
     边界（与需求一致）：本页只做任务与物料入口，不生成项目映射、不覆盖已有本体、不自动发布。 -->
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { cancelRun, createTask, deleteTask, errorMessage, fetchCapabilities, fetchRun, fetchTask, listTasks } from './api'
import type { BuildTaskDetail } from './api'
import {
  TASK_STATUS_LABELS, TASK_STATUS_TONE, formatBytes, formatTime, labelOf, taskStatusLabel, toneOf,
  type BuildCapabilities, type BuildTask, type ParserMatrixItem, type Tone,
} from './types'

// 与 App 的契约：进入任务（A02）与进入对象建模；本页不自行切换路由。
// enter-ontology 允许携带目标本体 id（B.4 回链）：交付行的「查看已创建本体」带上
// row.task.deliveryOntologyId，App 侧据此在必要时先切本体再进对象建模；不带参就是原行为。
const emit = defineEmits<{ (e: 'open-task', taskId: string): void; (e: 'enter-ontology', ontologyId?: string): void }>()

const caps = ref<BuildCapabilities | null>(null)
const capsError = ref('')
const tasks = ref<BuildTask[]>([])
const total = ref(0)
const loading = ref(true)
const loadError = ref('')
const notice = ref('')

const LIST_LIMIT = 50

// ─── 列表行补充数据（详情） ────────────────────────────────────────────────
// 列表接口（08 §3 GET /api/build-tasks）不返回物料数量、也不返回范围摘要与运行状态：
//   · `materials` 只在契约文档里声明，服务端未下发（显示会变成空值 + 单位）；
//   · `scopeRevision` 服务端恒为 0（该列从未写入），显示等于报错数字；
//   · 任务阶段在生成成功后未推进，`stageLabel` 会一直停在「生成中」。
// 这三处只能按行取一次任务详情（GET /api/build-task）来获得真实值；因此这里做
// 有并发上限的逐行详情读取：结果只用于本页展示，不写回、不缓存到服务端。
interface RowDetail {
  /** 材料清单长度（materials.length，真实值） */
  materialCount: number
  /** 范围摘要当前修订（scope.revision；-1 表示服务端未给出，展示时隐藏该字段） */
  scopeRevision: number
  /** 生成已成功且批次仍是任务当前批次：结果已入库，按「待评审」渲染 */
  reviewReady: boolean
}

/**
 * 任务是否「生成已成功、等待评审」——只按接口事实判断，不猜：
 *  1. 列表状态停在 generating（后端状态机在生成成功后未推进时才需要纠正；已是 review/delivered 时以服务端为准）；
 *  2. 详情里的最近运行是 generate 且 state=succeeded；
 *  3. 该运行产出的批次仍有效：batch 存在、batch.stale 不为 true、batch.id 与运行的 batchId 一致；
 *     task.currentBatch 非空时必须与其相等（为空说明是历史数据，不做否决）；
 *  4. 材料清单此后没有变过（batch.baseline.materialRevision === task.materialRevision），否则结果已过期。
 * 说明：不使用详情里的 `stale` 标记——确认范围时会先冻结批次基线再写回范围，范围修订因此 +1，
 * `stale` 对每个生成批次恒为 true（后端问题），拿它做否决会让这类任务永远显示「生成中」。
 */
function reviewReadyOf(item: BuildTask, detail: BuildTaskDetail): boolean {
  if (item.status !== 'generating') return false
  const run = detail.run
  if (!run || run.kind !== 'generate' || run.state !== 'succeeded') return false
  const batch = detail.batch || null
  if (!batch || batch.stale) return false
  const runBatchId = String(run.batchId || '')
  if (runBatchId && batch.id !== runBatchId) return false
  const currentBatch = String(detail.task?.currentBatch || item.currentBatch || '')
  if (currentBatch && currentBatch !== batch.id) return false
  const baseline = batch.baseline || ({} as Record<string, unknown>)
  const baselineMaterialRevision = Number(baseline.materialRevision)
  const materialRevision = Number(detail.task?.materialRevision ?? item.materialRevision)
  if (Number.isFinite(baselineMaterialRevision) && Number.isFinite(materialRevision)
      && baselineMaterialRevision !== materialRevision) return false
  return true
}
const details = ref<Record<string, RowDetail>>({})
const detailsLoading = ref(false)
const detailsFailed = ref(0)
/** 详情读取并发上限：既避免首屏一次性发出几十个请求，也不让单行失败拖住整页。 */
const DETAIL_CONCURRENCY = 4

async function loadRowDetails(items: BuildTask[]) {
  details.value = {}
  detailsFailed.value = 0
  const queue = [...items]
  if (!queue.length) { detailsLoading.value = false; return }
  detailsLoading.value = true
  const worker = async () => {
    for (;;) {
      const item = queue.shift()
      if (!item) return
      try {
        const detail = await fetchTask(item.id)
        const scopeRevision = Number(detail.scope?.revision)
        details.value = {
          ...details.value,
          [item.id]: {
            materialCount: Array.isArray(detail.materials) ? detail.materials.length : 0,
            scopeRevision: Number.isFinite(scopeRevision) ? scopeRevision : -1,
            reviewReady: reviewReadyOf(item, detail),
          },
        }
      } catch {
        // 单行详情读取失败：该行退回只显示列表确实提供的字段（不显示 0 或单位占位）
        detailsFailed.value += 1
      }
    }
  }
  await Promise.all(Array.from({ length: Math.min(DETAIL_CONCURRENCY, queue.length) }, () => worker()))
  detailsLoading.value = false
}

/** 表格行：任务（列表字段） + 详情（取到才有） + 当前阶段展示（生成成功待评审时纠正文案与色调）。 */
interface TaskRow { task: BuildTask; detail: RowDetail | null; statusLabel: string; statusTone: Tone }
const rows = computed<TaskRow[]>(() => tasks.value.map(task => {
  const detail = details.value[task.id] || null
  const reviewReady = !!detail && detail.reviewReady
  return {
    task,
    detail,
    statusLabel: reviewReady ? labelOf(TASK_STATUS_LABELS, 'review', '待评审') : taskStatusLabel(task),
    statusTone: reviewReady ? toneOf(TASK_STATUS_TONE, 'review') : toneOf(TASK_STATUS_TONE, task.status),
  }
}))

async function loadCaps() {
  capsError.value = ''
  try { caps.value = await fetchCapabilities() } catch (e) { capsError.value = errorMessage(e) }
}
async function loadTasks() {
  loading.value = true; loadError.value = ''
  details.value = {}
  try {
    const r = await listTasks(LIST_LIMIT, 0)
    tasks.value = r.items
    total.value = r.total
  } catch (e) {
    // 失败必须让用户看到（不静默变空列表）
    tasks.value = []; total.value = 0; loadError.value = errorMessage(e)
  } finally { loading.value = false }
  // 列表拿到后补详情（不阻塞列表渲染；失败只影响对应行的展示）
  await loadRowDetails(tasks.value)
}
onMounted(() => { void loadCaps(); void loadTasks() })

const providerConfigured = computed(() => !!caps.value?.provider)
const providerText = computed(() => {
  const p = caps.value?.provider
  return p ? p.name + '（' + p.model + '）' : '未配置'
})
const ocrText = computed(() => {
  const ocr = caps.value?.ocr
  if (!ocr) return '未知'
  return ocr.available ? '可用' : '不可用' + (ocr.reason ? '：' + ocr.reason : '')
})
const limitText = (pick: (c: BuildCapabilities) => number) => caps.value ? formatBytes(pick(caps.value)) : '—'

// ─── 解析器支持矩阵（08 §2.1 `parserMatrix` / 需求 §5） ─────────────────────
// 字段为服务端可选下发（未接线的旧后端没有该字段）：为空/未定义时整个区块不渲染。
// 只做展示映射，不推断矩阵内容、不硬编码后端口径。
const parserMatrix = computed<ParserMatrixItem[]>(() => {
  const list = caps.value?.parserMatrix
  return Array.isArray(list) ? list.filter(item => !!item) : []
})
/** 后缀列：exts 以「、」拼接（服务端给的标准形态含前导点；缺数组时不显示 undefined）。 */
const matrixExts = (item: ParserMatrixItem): string =>
  Array.isArray(item.exts) && item.exts.length ? item.exts.join('、') : '—'

// ─── 新建：名称可留空（服务端自动命名），创建成功后立即进入任务 ───
const newOpen = ref(false), newName = ref(''), creating = ref(false), newError = ref('')
function openNew() { newName.value = ''; newError.value = ''; newOpen.value = true }
function closeNew() { if (!creating.value) newOpen.value = false }
async function submitNew() {
  if (creating.value) return
  creating.value = true; newError.value = ''
  try {
    const r = await createTask(newName.value.trim())
    newOpen.value = false
    notice.value = '已创建生成任务「' + r.task.name + '」，接下来添加物料。'
    await loadTasks()
    emit('open-task', r.task.id)
  } catch (e) { newError.value = errorMessage(e) } finally { creating.value = false }
}

// ─── 停止生成任务（08 §12.3）：对最近一次运行调用取消，后端会把任务回退到「确定范围」 ───
const stoppingId = ref(''), stopError = ref('')
async function stopTask(t: BuildTask) {
  if (stoppingId.value) return
  stoppingId.value = t.id; stopError.value = ''
  try {
    const { run } = await fetchRun(t.id)
    if (!run || !run.id) throw new Error('该任务没有可停止的运行记录')
    if (run.state !== 'queued' && run.state !== 'running') {
      notice.value = '任务「' + t.name + '」的最近运行已是「' + run.state + '」状态，无需停止。'
      return
    }
    await cancelRun(t.id, run.id)
    notice.value = '已请求停止任务「' + t.name + '」的生成；已完成的解析与候选保留，可稍后重试或重新确认范围。'
    await loadTasks()
  } catch (e) {
    stopError.value = '停止失败：' + errorMessage(e)
  } finally { stoppingId.value = '' }
}

// ─── 删除：必须输入与任务名一致的确认名（服务端同样校验 confirmName）──
// 08 §12.2：删除为级联物理清理——任务行、物料、解析证据、对话、运行与批次、候选、blob 文件全部删除；
// 已创建的本体草稿不删除。确认弹层展示物料数量，删除后展示服务端返回的级联计数。
const delTarget = ref<BuildTask | null>(null), delInput = ref(''), deleting = ref(false), delError = ref('')
const delReady = computed(() => !!delTarget.value && delInput.value.trim() === delTarget.value.name)
function openDelete(t: BuildTask) { delTarget.value = t; delInput.value = ''; delError.value = '' }
function closeDelete() { if (!deleting.value) delTarget.value = null }
async function submitDelete() {
  const target = delTarget.value
  if (!target || !delReady.value || deleting.value) return
  deleting.value = true; delError.value = ''
  try {
    const r = await deleteTask(target.id, delInput.value.trim())
    const counts = (r.deleted || {}) as Record<string, number>
    notice.value = '任务「' + target.name + '」已删除并级联清理：'
      + '物料 ' + (counts.wb_build_materials ?? 0) + ' 份、解析证据 ' + (counts.wb_build_facts ?? 0)
      + ' 条、生成记录 ' + (counts.wb_build_runs ?? 0) + ' 次。'
      + (r.note ? r.note + ' ' : '') + '删除后原始证据不可用。'
    delTarget.value = null
    await loadTasks()
  } catch (e) { delError.value = errorMessage(e) } finally { deleting.value = false }
}
</script>

<template>
<section class="bt-page">
  <div class="bt-intro">
    <div>
      <h1>从物料生成本体</h1>
      <p class="muted">从已有系统（代码、表结构、需求文档）中提取业务概念，先得到一份有依据的初稿，再由你裁剪。</p>
    </div>
    <div class="bt-intro-tools">
      <button type="button" @click="emit('enter-ontology')">进入对象建模</button>
      <button type="button" class="primary" @click="openNew">＋ 新建生成任务</button>
    </div>
  </div>

  <p v-if="notice" class="bt-note" role="status">{{ notice }}</p>

  <!-- 能力限额：页面原样展示服务端限额，不承诺更大容量 -->
  <section class="card">
    <div class="panelhead">
      <h2>能力与限额</h2>
      <button type="button" class="mini" @click="loadCaps">刷新</button>
    </div>
    <p v-if="capsError" class="inline-error" role="alert">读取能力限额失败：{{ capsError }} <button type="button" class="mini" @click="loadCaps">重试</button></p>
    <p v-else-if="!caps" class="muted">读取中…</p>
    <template v-else>
      <dl class="bt-limits">
        <div><dt>单文件</dt><dd>{{ limitText(c => c.limits.fileBytes) }}</dd></div>
        <div><dt>单任务总量</dt><dd>{{ limitText(c => c.limits.taskBytes) }}</dd></div>
        <div><dt>分片大小</dt><dd>{{ limitText(c => c.limits.chunkBytes) }}</dd></div>
        <div><dt>压缩包展开上限</dt><dd>{{ limitText(c => c.limits.zipExpandedBytes) }} · {{ caps.limits.zipMaxEntries }} 个条目</dd></div>
        <div><dt>单份材料解析超时</dt><dd>{{ caps.limits.parseTimeoutSeconds }} 秒</dd></div>
        <div><dt>解析并发</dt><dd>{{ caps.limits.parseConcurrency }} 线程</dd></div>
        <div><dt>扫描页识别（OCR）</dt><dd :class="{ 'bt-warn-text': !caps.ocr.available }">{{ ocrText }}</dd></div>
        <div><dt>模型服务</dt><dd :class="{ 'bt-warn-text': !providerConfigured }">{{ providerText }}</dd></div>
        <div><dt>解析器 / 提示词版本</dt><dd>{{ caps.parserVersion }} / {{ caps.promptVersion }}</dd></div>
      </dl>
      <!-- 解析器支持矩阵（08 §2.1 / 需求 §5 S7）：可折叠；服务端未下发时整块不渲染 -->
      <details v-if="parserMatrix.length" class="bt-matrix">
        <summary>解析器支持矩阵 <span class="bt-sub">（{{ parserMatrix.length }} 项 · 含排除项）</span></summary>
        <p class="bt-sub">各后缀使用的解析器与定位粒度；排除项（硬黑名单凭据、软黑名单二进制）不可配置，如实列出。</p>
        <div class="bt-tablewrap">
          <table class="bt-table bt-table-matrix">
            <thead>
              <tr><th scope="col">后缀</th><th scope="col">支持方式 / 说明</th><th scope="col">定位粒度</th><th scope="col">备注</th></tr>
            </thead>
            <tbody>
              <tr v-for="(item, i) in parserMatrix" :key="'pm' + i">
                <td><strong>{{ matrixExts(item) }}</strong></td>
                <td>{{ item.label || '—' }}</td>
                <td>{{ item.locator || '—' }}</td>
                <td><span class="muted bt-sub">{{ item.note || '—' }}</span></td>
              </tr>
            </tbody>
          </table>
        </div>
      </details>
      <p v-if="!providerConfigured" class="bt-note warn">
        尚未配置模型服务：可以创建任务并上传材料，但「确定范围」的对话与「生成初稿」无法启动。
        请先到「设置 → 模型设置」添加一条可用的模型配置，再回到这里继续。
      </p>
      <p v-if="!caps.ocr.available" class="bt-note warn">
        扫描件（图片页）当前无法识别，会被逐页记为解析缺口；这不影响其它材料的解析。
      </p>
    </template>
  </section>

  <!-- 列表态 / 空态 -->
  <section class="card">
    <div class="panelhead">
      <h2>生成任务 <span class="muted">共 {{ total }} 个</span></h2>
      <button type="button" class="mini" @click="loadTasks">刷新</button>
    </div>
    <p v-if="loadError" class="inline-error" role="alert">
      读取任务列表失败：{{ loadError }} <button type="button" class="mini" @click="loadTasks">重试</button>
    </p>
    <p v-else-if="loading" class="muted">读取中…</p>
    <div v-else-if="!tasks.length" class="bt-hero">
      <h3>让已有材料成为建模的起点</h3>
      <p>代码、表结构、需求文档共同提供依据；不要求每种材料都齐全。</p>
      <ol class="bt-steps">
        <li><span>01</span><strong>添加物料</strong><small>上传代码、表结构、文档；先扫描识别内容，再确定范围。</small></li>
        <li><span>02</span><strong>对话确定范围</strong><small>不用一次说清楚，结合材料逐步确认本次建模边界。</small></li>
        <li><span>03</span><strong>评审定义与依据</strong><small>每条定义都带来源位置，冲突与推断需要人工确认。</small></li>
        <li><span>04</span><strong>保存新本体</strong><small>只写入本次选定的定义，创建一份未发布草稿。</small></li>
      </ol>
      <p class="bt-note">仅生成本体草稿：不生成项目映射，不覆盖已有本体，不自动发布。</p>
      <div class="bt-hero-tools">
        <button type="button" class="primary" @click="openNew">＋ 新建生成任务</button>
        <button type="button" @click="emit('enter-ontology')">先看看现有对象建模</button>
      </div>
    </div>
    <div v-else class="bt-tablewrap">
      <table class="bt-table">
        <thead>
          <tr><th scope="col" class="bt-col-name">任务</th><th scope="col">当前阶段</th><th scope="col">物料</th><th scope="col">更新时间</th><th scope="col" class="bt-col-ops">操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="row in rows" :key="row.task.id">
            <td>
              <strong>{{ row.task.name }}</strong>
              <small class="bt-sub">清单修订 {{ row.task.materialRevision }}<template v-if="row.detail && row.detail.scopeRevision >= 0"> · 范围修订 {{ row.detail.scopeRevision }}</template><template v-if="row.task.deliveryOntologyId"> · 已创建本体 {{ row.task.deliveryOntologyId }}</template></small>
            </td>
            <td>
              <span class="status-pill" :class="'bt-pill-' + row.statusTone">{{ row.statusLabel }}</span>
            </td>
            <td>
              <template v-if="row.detail">{{ row.detail.materialCount }} 份</template>
              <span v-else class="muted" :title="detailsLoading ? '正在读取该任务的材料数量' : '未能读取该任务的详情'">{{ detailsLoading ? '读取中…' : '—' }}</span>
            </td>
            <td>{{ formatTime(row.task.updatedAt) }}</td>
            <td class="bt-ops">
              <button type="button" class="row-link" @click="emit('open-task', row.task.id)">{{ row.task.status === 'delivered' ? '查看任务' : '继续' }}</button>
              <!-- 08 §12.3：任务级停止——对最近一次运行调用取消，后端同事务把任务回退到「确定范围」 -->
              <button v-if="row.task.status === 'generating'" type="button" class="row-link" :disabled="stoppingId === row.task.id" @click="stopTask(row.task)">{{ stoppingId === row.task.id ? '停止中…' : '停止' }}</button>
              <!-- B.4 回链：必须带上该任务交付的本体 id，否则点了任务 B 仍会停在当前选中的本体 A -->
              <button v-if="row.task.deliveryOntologyId" type="button" class="row-link" @click="emit('enter-ontology', row.task.deliveryOntologyId)">查看已创建本体</button>
              <button type="button" class="row-link danger" @click="openDelete(row.task)">删除</button>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-if="detailsFailed" class="muted bt-sub">{{ detailsFailed }} 个任务的详情读取失败：这些行只显示列表字段，材料数量显示为「—」。<button type="button" class="mini" @click="loadRowDetails(tasks)">重试</button></p>
      <p v-if="tasks.length < total" class="muted bt-sub">仅显示最近 {{ tasks.length }} 个任务（共 {{ total }} 个）。</p>
    </div>
  </section>

  <!-- 新建任务 -->
  <div v-if="newOpen" class="modal-backdrop" @click.self="closeNew">
    <section class="modal-card" role="dialog" aria-modal="true" aria-label="新建生成任务">
      <h2>新建生成任务</h2>
      <p class="muted">任务只组织材料与范围，尚未创建本体；物料和范围稍后在任务内维护。</p>
      <label>任务名称
        <input v-model="newName" type="text" maxlength="80" placeholder="留空则自动命名为「未命名任务-日期」" @keydown.enter="submitNew">
      </label>
      <p class="field-help">填写业务上能认出的名字，例如「储能运行管理本体」；留空时由服务端自动命名。</p>
      <p v-if="newError" class="inline-error" role="alert">{{ newError }}</p>
      <div class="dialogtools">
        <button type="button" :disabled="creating" @click="closeNew">取消</button>
        <button type="button" class="primary" :disabled="creating" @click="submitNew">{{ creating ? '创建中…' : '创建并添加物料' }}</button>
      </div>
    </section>
  </div>

  <!-- 删除任务：必须输入任务名（服务端同样校验 confirmName） -->
  <div v-if="delTarget" class="modal-backdrop" @click.self="closeDelete">
    <section class="modal-card" role="dialog" aria-modal="true" aria-label="删除生成任务">
      <h2>删除生成任务</h2>
      <p>将物理删除任务「{{ delTarget.name }}」及其全部关联数据：
        <template v-if="details[delTarget.id]">物料 {{ details[delTarget.id].materialCount }} 份、</template>
        解析证据、范围对话、生成与评审记录，均不可恢复。</p>
      <p class="bt-note warn">已创建的本体草稿不随任务删除；删除后本任务的原始证据不可用。</p>
      <label>请输入任务名以确认删除
        <input v-model="delInput" type="text" :placeholder="delTarget.name" @keydown.enter="submitDelete">
      </label>
      <p class="field-help">名称必须与「{{ delTarget.name }}」完全一致。</p>
      <p v-if="delError" class="inline-error" role="alert">{{ delError }}</p>
      <div class="dialogtools">
        <button type="button" :disabled="deleting" @click="closeDelete">取消</button>
        <button type="button" class="danger bt-danger-btn" :disabled="!delReady || deleting" @click="submitDelete">{{ deleting ? '删除中…' : '删除任务' }}</button>
      </div>
    </section>
  </div>
</section>
</template>

<style scoped>
/* 局部样式，只借用全局令牌与通用类（.card/.panelhead/.muted/.status-pill/.modal-*），不新增全局规则 */
.bt-page{display:block}
.bt-intro{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;flex-wrap:wrap;margin-bottom:16px}
.bt-intro h1{font-size:22px;margin:0 0 4px}
.bt-intro p{margin:0}
.bt-intro-tools{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.bt-limits{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:8px 18px;margin:0}
.bt-limits div{min-width:0}
.bt-limits dt{font-size:12px;color:var(--muted)}
.bt-limits dd{margin:2px 0 0;font-size:13px;overflow-wrap:anywhere}
.bt-warn-text{color:var(--warn)}
.bt-note{padding:11px 14px;border:1px solid var(--blue-line);background:var(--blue-soft);border-radius:var(--r-sm);font-size:13px;color:var(--ink-2);margin:12px 0}
.bt-note.warn{border-color:var(--warn-line);background:var(--warn-soft);color:var(--warn)}
.bt-hero{padding:8px 0 4px}
.bt-hero h3{font-size:16px;margin:0 0 6px}
.bt-steps{list-style:none;display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;padding:0;margin:14px 0}
.bt-steps li{padding:14px;background:var(--paper-2);border-radius:var(--r-sm)}
.bt-steps span{display:block;font-size:12px;color:var(--muted)}
.bt-steps strong{display:block;font-size:14px;margin:4px 0}
.bt-steps small{display:block;font-size:12px;color:var(--muted)}
.bt-hero-tools{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.bt-tablewrap{overflow:auto}
.bt-table{min-width:820px}
/* 解析器支持矩阵：沿用既有 token 的可折叠区块，不新增全局规则 */
.bt-matrix{margin-top:14px;border-top:1px solid var(--line);padding-top:12px}
.bt-matrix summary{cursor:pointer;font-size:13px;color:var(--ink-2)}
.bt-matrix .bt-sub{margin:6px 0 0}
.bt-table-matrix{min-width:640px;margin-top:8px}
.bt-table-matrix td,.bt-table-matrix th{font-size:13px}
.bt-col-name{width:34%}
.bt-col-ops{width:24%}
.bt-sub{display:block;font-size:12px;color:var(--muted);margin-top:4px}
.bt-ops{white-space:nowrap}
.bt-ops .row-link+.row-link{margin-left:10px}
.bt-pill-ok{background:var(--ok-soft);border-color:var(--ok-line);color:var(--ok)}
.bt-pill-warn{background:var(--warn-soft);border-color:var(--warn-line);color:var(--warn)}
.bt-pill-bad{background:var(--danger-soft);border-color:var(--danger-line);color:var(--danger)}
.bt-pill-info{background:var(--blue-soft);border-color:var(--blue-line);color:var(--blue-ink)}
/* 弹窗里的危险按钮：全局 .danger 是行内文字样式，这里拉回按钮外观 */
.bt-danger-btn{color:var(--danger);border-color:var(--danger-line);background:var(--paper)}
.bt-danger-btn:hover:not(:disabled){background:var(--danger-soft);border-color:var(--danger);color:var(--danger)}
.bt-danger-btn:disabled{opacity:.5;cursor:not-allowed}
</style>
