// 「从物料自动构建本体」任务族 API（契约：`文档/接口文档/08-从物料自动构建本体接口.md` §2—§8）。
// 两条硬约束：
//  1. 唯一请求出口 app/http（页面与调用方不自行拼 fetch / 不自行解析错误）；
//     失败一律抛 http 层的 SaveRequestError（409 带 currentRevision），调用方按 e.message 展示，不吞错。
//  2. 分片上传在这里做真实实现（File.slice 切片 + crypto.subtle SHA-256 + base64 + 顺序上传 + 进度回调），
//     页面只订阅进度、渲染状态、决定重试/取消。
import { getJson, postJson, SaveRequestError } from '../../app/http'
import type {
  BuildCapabilities, BuildDelivery, BuildRun, BuildTask, Candidate, CandidateDetail,
  CandidateDiff, CandidateListResult, DeliveryPrecheck, DeliveryResult, Material,
  MaterialFilterView, MaterialGroup, MergePreview, MergeResult, ScopeMessage, ScopeModel,
  TaskFilterSpec,
} from './types'

// ── 端点（08 分册路径；仅此处维护字符串，页面不拼接口地址） ────────────────
const EP = {
  capabilities: '/api/build-capabilities',
  tasks: '/api/build-tasks',
  taskCreate: '/api/build-task-create',
  task: '/api/build-task',
  taskRename: '/api/build-task-rename',
  taskFilter: '/api/build-task-filter',
  taskDelete: '/api/build-task-delete',
  uploadInit: '/api/build-upload-init',
  uploadChunk: '/api/build-upload-chunk',
  uploadComplete: '/api/build-upload-complete',
  uploadAbort: '/api/build-upload-abort',
  materials: '/api/build-materials',
  materialExclude: '/api/build-material-exclude',
  materialRetry: '/api/build-material-retry',
  scan: '/api/build-scan',
  run: '/api/build-run',
  runCancel: '/api/build-run-cancel',
  runResume: '/api/build-run-resume',
  messages: '/api/build-messages',
  message: '/api/build-message',
  scopeSave: '/api/build-scope-save',
  scopeConfirm: '/api/build-scope-confirm',
  candidates: '/api/build-candidates',
  candidate: '/api/build-candidate',
  candidateUpdate: '/api/build-candidate-update',
  candidateDecide: '/api/build-candidate-decide',
  candidatesMerge: '/api/build-candidates-merge',
  reviewUndo: '/api/build-review-undo',
  regenerate: '/api/build-regenerate',
  diff: '/api/build-diff',
  diffResolve: '/api/build-diff-resolve',
  deliverPrecheck: '/api/build-deliver-precheck',
  deliver: '/api/build-deliver',
  delivery: '/api/build-delivery',
} as const

// ── 响应形状（与 08 分册逐条对应） ─────────────────────────────────────────
export interface BuildTaskList { items: BuildTask[]; total: number }
export interface BuildTaskDetail {
  task: BuildTask
  materials: Material[]
  scope: ScopeModel
  run: BuildRun | null
  delivery: BuildDelivery | null
  /** 08 §3 该响应附带能力信息；页面若已单独取过 capabilities 可忽略 */
  capabilities?: BuildCapabilities
  /** 服务端实际返回的最近批次（列表接口不下发；未生成过批次时为 null）。
   *  文档未登记，但 A05 评审页已在消费同名字段；这里补成可选字段供列表页判断「生成成功但状态未推进」。
   *  baseline 为冻结基线（含 materialRevision/scopeRevision），用于判断结果是否仍与当前材料清单一致。 */
  batch?: { id: string; taskId: string; runId: string; stale: boolean; createdAt: string; baseline?: Record<string, unknown> } | null
  /** 服务端实际返回的「当前结果是否过期」（材料/范围在生成后有变化时为 true）。
   *  文档未登记，仅用于避免把已过期的成功结果当成待评审。 */
  stale?: boolean
}
export interface UploadInitResult { uploadId: string; chunkBytes: number; maxChunks: number; received: number }
export interface UploadChunkResult { received: number; nextIndex: number }
export interface MaterialList { items: Material[]; revision: number }
export interface MaterialMutation { material: Material; task: BuildTask }
export interface RunEnvelope { run: BuildRun; taskStatus: string }

function query(params: Record<string, string | number | undefined | null>): string {
  const parts: string[] = []
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === '') continue
    parts.push(encodeURIComponent(key) + '=' + encodeURIComponent(String(value)))
  }
  return parts.length ? '?' + parts.join('&') : ''
}

/** 统一错误文案：http 层错误对象优先取 message；非 Error 一律给可读兜底（绝不显示 [object Object]）。 */
export function errorMessage(e: unknown): string {
  if (e instanceof SaveRequestError) return e.message || '请求失败'
  if (e instanceof Error) return e.message || '请求失败'
  if (typeof e === 'string' && e) return e
  return '未知错误，请重试'
}

/** 修订冲突（409）：返回服务端当前 revision（可能为空串），非冲突返回 null。 */
export function conflictRevision(e: unknown): string | null {
  if (e instanceof SaveRequestError && e.status === 409) return String(e.currentRevision ?? '')
  return null
}

// ── §2.1 能力与限额 ────────────────────────────────────────────────────────
export async function fetchCapabilities(): Promise<BuildCapabilities> {
  return await getJson(EP.capabilities) as BuildCapabilities
}

// ── §3 任务 ───────────────────────────────────────────────────────────────
export async function listTasks(limit = 20, offset = 0): Promise<{ items: BuildTask[]; total: number }> {
  return await getJson(EP.tasks + query({ limit, offset })) as BuildTaskList
}

export async function createTask(name: string): Promise<{ task: BuildTask }> {
  // 名称允许为空：服务端自动命名为「未命名任务-日期」（08 §3）
  return await postJson(EP.taskCreate, { name }) as { task: BuildTask }
}

export async function fetchTask(taskId: string): Promise<{
  task: BuildTask; materials: Material[]; scope: ScopeModel; run: BuildRun | null; delivery: BuildDelivery | null
}> {
  return await getJson(EP.task + query({ taskId })) as BuildTaskDetail
}

export async function renameTask(taskId: string, name: string, revision: string): Promise<{ task: BuildTask }> {
  return await postJson(EP.taskRename, { taskId, name, revision }) as { task: BuildTask }
}

/** 08 §13.3：保存任务级过滤设置（软名单覆盖/自定义追加/白名单）。revision = 任务 token（CAS）。 */
export async function saveTaskFilter(taskId: string, revision: string, filter: Partial<TaskFilterSpec>): Promise<{ task: BuildTask; filter: TaskFilterSpec }> {
  return await postJson(EP.taskFilter, { taskId, revision, filter }) as { task: BuildTask; filter: TaskFilterSpec }
}

export async function deleteTask(taskId: string, confirmName: string): Promise<{ ok: boolean; note?: string; deleted?: Record<string, number> }> {
  return await postJson(EP.taskDelete, { taskId, confirmName }) as { ok: boolean; note?: string; deleted?: Record<string, number> }
}

// ── §4 分片上传与物料 ─────────────────────────────────────────────────────
export async function initUpload(taskId: string, relPath: string, size: number): Promise<UploadInitResult> {
  return await postJson(EP.uploadInit, { taskId, relPath, size }) as UploadInitResult
}

/** 单分片上传；同 index 同 hash 服务端幂等返回既有结果（重发安全）。 */
export async function putChunk(
  uploadId: string, index: number, hash: string, dataBase64: string,
  opts: { signal?: AbortSignal | null } = {},
): Promise<{ received: number; nextIndex: number }> {
  return await postJson(EP.uploadChunk, { uploadId, index, hash, dataBase64 }, { signal: opts.signal ?? null }) as UploadChunkResult
}

/**
 * 完成上传。08 §4 响应体为单份 `material`；ZIP 会按 §4.1 展开成多份材料（虚拟 relPath），
 * 因此这里把两种形态统一归一为数组返回，调用方只需处理列表。
 */
export async function completeUpload(uploadId: string, finalHash: string): Promise<{ materials: Material[] }> {
  const data = await postJson(EP.uploadComplete, { uploadId, finalHash }) as { material?: Material; materials?: Material[] }
  const materials = Array.isArray(data.materials) ? data.materials : data.material ? [data.material] : []
  return { materials }
}

export async function abortUpload(uploadId: string): Promise<{ ok: boolean }> {
  return await postJson(EP.uploadAbort, { uploadId }) as { ok: boolean }
}

/** 08 §12.1：按顶层目录分组的物料汇总（view=groups）。 */
export async function fetchMaterialGroups(taskId: string): Promise<{ groups: MaterialGroup[]; total: number; revision: number }> {
  return await getJson(EP.materials + query({ taskId, view: 'groups' })) as { groups: MaterialGroup[]; total: number; revision: number }
}

/** 08 §13.4：被过滤文件报告（计数 + 清单 + 逐项命中规则；G20 可见性）。 */
export async function fetchMaterialFilter(taskId: string): Promise<MaterialFilterView> {
  return await getJson(EP.materials + query({ taskId, view: 'filter' })) as MaterialFilterView
}

/** 08 §12.1：按顶层目录分页取物料明细；folder 缺省 = 全量（兼容旧行为）。 */
export async function fetchMaterialsPage(
  taskId: string, folder?: string, offset = 0, limit = 100,
): Promise<{ items: Material[]; total: number; revision: number }> {
  return await getJson(EP.materials + query({ taskId, folder, offset, limit })) as { items: Material[]; total: number; revision: number }
}

export async function listMaterials(taskId: string): Promise<{ items: Material[]; revision: number }> {
  return await getJson(EP.materials + query({ taskId })) as MaterialList
}

/**
 * 物料清单修订（GET /api/build-materials 的整数 `revision`）→ 排除/恢复接口所需的
 * **不透明字符串 token**（08 §4：服务端按 `str(materialRevision)` 比对，数字形态 400）。
 * 缺失（null/undefined）返回空串，调用方据此提示「先刷新清单」而不是发一个注定失败的请求。
 */
export function materialRevisionToken(revision: number | string | null | undefined): string {
  if (revision === null || revision === undefined) return ''
  if (typeof revision === 'string') return revision.trim()
  return Number.isFinite(revision) ? String(revision) : ''
}

/**
 * 排除/恢复材料：推进 materialRevision（旧扫描/范围/结果基线随之失效，由页面提示过期）。
 * revision 是**物料清单修订的不透明字符串 token**（08 §4：`String(materialRevision)`），
 * 后端按 `str(material_revision)` 比对，数字形态会被参数校验拒为 400。
 */
export async function setMaterialExcluded(
  taskId: string, materialId: string, excluded: boolean, revision: string,
): Promise<MaterialMutation> {
  return await postJson(EP.materialExclude, { taskId, materialId, excluded, revision }) as MaterialMutation
}

export async function retryMaterial(taskId: string, materialId: string): Promise<{ runId: string }> {
  return await postJson(EP.materialRetry, { taskId, materialId }) as { runId: string }
}

export async function scanMaterials(taskId: string): Promise<{ runId: string }> {
  return await postJson(EP.scan, { taskId }) as { runId: string }
}

// ── §5 运行 ───────────────────────────────────────────────────────────────
export async function fetchRun(taskId: string, runId?: string): Promise<{ run: BuildRun; taskStatus: string }> {
  return await getJson(EP.run + query({ taskId, runId })) as RunEnvelope
}

export async function cancelRun(taskId: string, runId: string): Promise<{ run: BuildRun }> {
  return await postJson(EP.runCancel, { taskId, runId }) as { run: BuildRun }
}

export async function resumeRun(taskId: string, runId: string): Promise<{ runId: string }> {
  return await postJson(EP.runResume, { taskId, runId }) as { runId: string }
}

// ── §6 范围对话 ───────────────────────────────────────────────────────────
export interface MessageList { messages: ScopeMessage[]; scope: ScopeModel; revision: number; stale: boolean }

/** 增量读取消息（after 为已见最大 seq）；scope 一并返回，避免两次请求。 */
export async function fetchMessages(taskId: string, after = 0): Promise<MessageList> {
  return await getJson(EP.messages + query({ taskId, after })) as MessageList
}

/** 发送用户消息：助手回复异步生成，随后用 fetchMessages 增量轮询获取。 */
export async function sendMessage(taskId: string, content: string, revision: number): Promise<{ messageId: string; assistantPending: boolean }> {
  return await postJson(EP.message, { taskId, content, revision }) as { messageId: string; assistantPending: boolean }
}

/** 保存范围摘要（人工编辑；携带 revision 做 CAS，冲突抛 409 带 currentRevision）。 */
export async function saveScope(taskId: string, scope: Partial<ScopeModel>, revision: number): Promise<{ scope: ScopeModel }> {
  return await postJson(EP.scopeSave, { taskId, scope, revision }) as { scope: ScopeModel }
}

/** 确认范围并生成：冻结输入基线、创建批次与生成运行。range 矛盾时 422 带 issues。 */
export async function confirmScope(taskId: string, revision: number, providerId: string): Promise<{ runId: string; batchId: string }> {
  return await postJson(EP.scopeConfirm, { taskId, revision, providerId }) as { runId: string; batchId: string }
}

// ── §7 候选评审 ───────────────────────────────────────────────────────────
export interface CandidateQuery {
  batch?: string
  type?: string
  decision?: string
  evidenceStatus?: string
  query?: string
  offset?: number
  limit?: number
}

export async function fetchCandidates(taskId: string, params: CandidateQuery = {}): Promise<CandidateListResult> {
  return await getJson(EP.candidates + query({ taskId, ...params })) as CandidateListResult
}

export async function fetchCandidate(candidateId: string): Promise<CandidateDetail> {
  return await getJson(EP.candidate + query({ candidateId })) as CandidateDetail
}

/** 候选级写操作（08 §7）：revision 一律传该候选自己的 `revision` token（r-uuid），不是任务 revision。 */
export async function updateCandidate(candidateId: string, fields: Record<string, unknown>, revision: string): Promise<{ candidate: Candidate }> {
  return await postJson(EP.candidateUpdate, { candidateId, fields, revision }) as { candidate: Candidate }
}

export async function decideCandidate(candidateId: string, decision: string, reason: string, revision: string): Promise<{ candidate: Candidate }> {
  return await postJson(EP.candidateDecide, { candidateId, decision, reason, revision }) as { candidate: Candidate }
}

/**
 * 合并候选（08 §7）：
 * - 预览 `confirmed=false` 只读，不携带 revision（服务端不做 CAS）；
 * - 执行 `confirmed=true` 是**候选级 CAS**，revision 传保留项（primaryId）候选的 `revision` token。
 */
export async function mergePreview(taskId: string, primaryId: string, mergeIds: string[]): Promise<MergePreview> {
  return await postJson(EP.candidatesMerge, { taskId, primaryId, mergeIds, confirmed: false }) as MergePreview
}

export async function mergeApply(taskId: string, primaryId: string, mergeIds: string[], revision: string): Promise<MergeResult> {
  return await postJson(EP.candidatesMerge, { taskId, primaryId, mergeIds, confirmed: true, revision }) as MergeResult
}

/** 撤销评审操作（合并）：revision 传**保留项候选**的当前 `revision` token（候选级 CAS）。 */
export async function undoReviewOp(taskId: string, opId: string, revision: string): Promise<{ ok: boolean; candidate: Candidate }> {
  return await postJson(EP.reviewUndo, { taskId, opId, revision }) as { ok: boolean; candidate: Candidate }
}

/**
 * 重新生成：以当前材料/范围基线重跑，产生新批次（旧批次保留只读）。
 * 08 §7 的 revision 是**可选整数**（范围修订），不是任务 token；这里按最简一致行为省略该字段。
 */
export async function regenerate(taskId: string): Promise<{ runId: string; batchId: string }> {
  return await postJson(EP.regenerate, { taskId }) as { runId: string; batchId: string }
}

export async function fetchDiff(taskId: string, batch?: string): Promise<CandidateDiff> {
  return await getJson(EP.diff + query({ taskId, batch })) as CandidateDiff
}

/** 差异逐项裁决：revision 传**该候选**的 `revision` token（候选级 CAS）。 */
export async function resolveDiff(taskId: string, candidateId: string, choice: 'keepManual' | 'acceptNew', revision: string): Promise<{ candidate: Candidate }> {
  return await postJson(EP.diffResolve, { taskId, candidateId, choice, revision }) as { candidate: Candidate }
}

// ── §8 交付（原子创建新本体） ─────────────────────────────────────────────
export async function deliverPrecheck(taskId: string): Promise<DeliveryPrecheck> {
  return await postJson(EP.deliverPrecheck, { taskId }) as DeliveryPrecheck
}

/** 创建新本体草稿。同一 requestId 的网络重试返回原结果；同 requestId 不同内容 409。 */
export async function deliver(taskId: string, name: string, checkToken: string, requestId: string): Promise<DeliveryResult> {
  return await postJson(EP.deliver, { taskId, name, checkToken, requestId }) as DeliveryResult
}

export async function fetchDelivery(taskId: string): Promise<{ delivery: BuildDelivery | null }> {
  return await getJson(EP.delivery + query({ taskId })) as { delivery: BuildDelivery | null }
}

// ── 分片上传执行器（真实实现） ────────────────────────────────────────────
export type UploadPhase = 'hashing' | 'uploading' | 'completing'

export interface UploadProgress {
  phase: UploadPhase
  /** 浏览器相对路径（目录选择时为 目录/子目录/文件） */
  relPath: string
  sentBytes: number
  totalBytes: number
  chunkIndex: number
  chunkCount: number
}

export interface UploadFileOptions {
  /** 显式相对路径；缺省取 file.webkitRelativePath（目录选择）或 file.name */
  relPath?: string
  onProgress?: (p: UploadProgress) => void
  /** 拿到 uploadId 即回调：页面据此支持上传中取消（abortUpload(uploadId)） */
  onUploadId?: (uploadId: string) => void
  /** 取消信号：中断后本次上传作废（服务端临时内容由 abortUpload 清理） */
  signal?: AbortSignal | null
}

function hexOf(buffer: ArrayBuffer): string {
  return [...new Uint8Array(buffer)].map(b => b.toString(16).padStart(2, '0')).join('')
}

/** SHA-256（hex）。crypto.subtle 只在安全上下文可用；不可用时明确报错，不退化成“无校验上传”。 */
async function sha256Hex(buffer: ArrayBuffer): Promise<string> {
  const subtle = globalThis.crypto?.subtle
  if (!subtle) {
    throw new SaveRequestError('当前浏览器环境不支持本地文件校验（需要 127.0.0.1 或 https 安全上下文），已停止上传以保护材料完整性', 0)
  }
  return hexOf(await subtle.digest('SHA-256', buffer))
}

/** ArrayBuffer → base64：分块拼接二进制字符串，避免大数组一次性展开爆栈。 */
function toBase64(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer)
  const BLOCK = 0x8000
  let binary = ''
  for (let i = 0; i < bytes.length; i += BLOCK) {
    binary += String.fromCharCode(...bytes.subarray(i, i + BLOCK))
  }
  return btoa(binary)
}

/**
 * 上传一个文件：init → 顺序上传分片 → complete。
 * - 分片用 File.slice 按服务端 chunkBytes 切；分片 hash 与整文件 hash 均为 SHA-256 hex；
 * - 分片内容 base64（请求体 2MB 上限内，chunkBytes 由服务端给定）；
 * - 全部从 index 0 顺序发送：服务端对同 index 同 hash 幂等，重试/续传不会产生重复内容；
 * - 失败即 dispose：调用 abortUpload 清理服务端临时内容后把原始错误抛给调用方（页面决定重试/取消）。
 */
export async function uploadFile(
  taskId: string, file: File, opts: UploadFileOptions = {},
): Promise<{ materials: Material[] }> {
  const relPath = opts.relPath || file.webkitRelativePath || file.name
  const totalBytes = file.size
  const report = (phase: UploadPhase, sentBytes: number, chunkIndex: number, chunkCount: number) => {
    opts.onProgress?.({ phase, relPath, sentBytes, totalBytes, chunkIndex, chunkCount })
  }
  report('hashing', 0, 0, 0)
  const init = await initUpload(taskId, relPath, totalBytes)
  opts.onUploadId?.(init.uploadId)
  const chunkBytes = init.chunkBytes > 0 ? init.chunkBytes : Math.max(1, totalBytes)
  const chunkCount = Math.max(1, Math.ceil(totalBytes / chunkBytes))
  try {
    let sent = 0
    for (let index = 0; index < chunkCount; index += 1) {
      if (opts.signal?.aborted) throw new SaveRequestError('上传已取消', 0, null, null, 'aborted')
      const blob = file.slice(index * chunkBytes, Math.min((index + 1) * chunkBytes, totalBytes))
      const buffer = await blob.arrayBuffer()
      const hash = await sha256Hex(buffer)
      report('uploading', sent, index, chunkCount)
      await putChunk(init.uploadId, index, hash, toBase64(buffer), { signal: opts.signal })
      sent += blob.size
      report('uploading', sent, index + 1, chunkCount)
    }
    report('completing', totalBytes, chunkCount, chunkCount)
    const finalHash = await sha256Hex(await file.arrayBuffer())
    const done = await completeUpload(init.uploadId, finalHash)
    report('completing', totalBytes, chunkCount, chunkCount)
    return done
  } catch (e) {
    // 失败/取消：清理服务端临时内容；清理本身失败不覆盖原始错误（页面看到的是真正原因）
    if (!opts.signal?.aborted) {
      try { await abortUpload(init.uploadId) } catch { /* 临时内容由服务端有界清理兜底 */ }
    }
    throw e
  }
}
