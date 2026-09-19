// 配置迁移 API（20260919 需求；接口文档 07 分册）。
// 全部经 app/http.ts 唯一出口；分片上传逐片 postJson，页面不手写 fetch。
import { postJson, postBlob, SaveRequestError } from '../app/http'

export interface ExportPreviewAsset { packageKey: string; id: string; name: string; releaseCount?: number; includeReason?: string; usedBy?: string[]; shared?: boolean; referencedBy?: string[]; missingApiKey?: boolean }
export interface ExportPreview { exportToken: string | null; expiresAt?: string; snapshotAt?: string; assets: { models: ExportPreviewAsset[]; projects: ExportPreviewAsset[]; flows: ExportPreviewAsset[]; modelConfigs: ExportPreviewAsset[] }; dependencyEdges: { from: string; to: string; kind: string; detail?: string }[]; warnings: string[]; blockers: string[] }
export interface ImportPreviewAsset { kind: 'model' | 'project' | 'flow'; packageKey: string; sourceName: string; suggestedName: string; renameReason: string; releaseCount: number; dependencyNote: string }
export interface ImportPreview { previewToken: string; expiresAt?: string; packageHash: string; assets: ImportPreviewAsset[]; modelConfigs: { packageKey: string; sourceName: string; missingApiKey: boolean }[]; warnings: string[]; blockers: string[] }
export interface ImportReceipt { receiptId: string; requestId: string; assets: { kind: string; packageKey: string; newId: string; newName: string; sourceName: string; releaseCount: number }[]; pendingCredentials: { declarationId: string; name: string; kind?: string; usage?: string[] }[]; warnings: string[]; importedAt?: string; replayed?: boolean }

const CHUNK_SIZE = 512 * 1024

/** 导出预览：blockers 非空时 exportToken 为 null。 */
export function exportPreview(modelIds: string[], projectIds: string[], extraFlowIds: string[]): Promise<ExportPreview> {
  return postJson('/api/config-package-export-preview', { modelIds, projectIds, extraFlowIds })
}

/** 下载配置包 ZIP（触发浏览器保存）。 */
export async function exportDownload(exportToken: string, filename: string): Promise<void> {
  const blob = await postBlob('/api/config-package-export', { exportToken })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 4000)
}

/** 分片上传：返回 uploadId；onProgress(已传分片, 总分片)。同分片重传幂等。 */
export async function stageUpload(file: File, onProgress?: (done: number, total: number) => void): Promise<string> {
  const sha = await sha256Hex(file)
  const begin = await postJson('/api/config-package-stage',
    { action: 'begin', filename: file.name, bytes: file.size, sha256: sha }) as { uploadId: string; chunkSize: number; totalChunks: number }
  const total = begin.totalChunks
  for (let index = 0; index < total; index++) {
    const chunk = await file.slice(index * begin.chunkSize, (index + 1) * begin.chunkSize).arrayBuffer()
    const hash = await sha256Bytes(chunk)
    await postJson('/api/config-package-stage', {
      action: 'chunk', uploadId: begin.uploadId, index,
      base64: arrayBufferToBase64(new Uint8Array(chunk)), chunkHash: hash,
    })
    onProgress?.(index + 1, total)
  }
  return begin.uploadId
}

export function importPreview(uploadId: string): Promise<ImportPreview> {
  return postJson('/api/config-package-import-preview', { uploadId })
}

/** 确认导入：同一 requestId 可安全重试（服务端幂等）；改参数 409。 */
export function importConfirm(previewToken: string, requestId: string, nameOverrides: Record<string, string>): Promise<ImportReceipt> {
  return postJson('/api/config-package-import', { previewToken, requestId, nameOverrides })
}

/** 查询回执：status 'none' 表示尚无回执（不触发新建）。 */
export function importResult(requestId: string): Promise<{ status: 'none' | 'completed'; receipt?: ImportReceipt }> {
  return postJson('/api/config-package-import-result', { requestId })
}

export function discard(tokens: { uploadId?: string; exportToken?: string; previewToken?: string }): Promise<{ ok: boolean }> {
  return postJson('/api/config-package-discard', tokens)
}

/** 409 NAME_CONFLICT 携带的建议名。 */
export function conflictSuggestions(err: unknown): { packageKey: string; suggestedName: string }[] {
  return err instanceof SaveRequestError ? (err.data?.conflicts || []) : []
}

// ── 本地哈希（SubtleCrypto；不可用时退化到省略校验由服务端拒绝）─────────────────
async function sha256Hex(file: File): Promise<string> {
  try {
    const buf = await file.arrayBuffer()
    return await sha256Bytes(buf)
  } catch { return '' }
}

async function sha256Bytes(buf: ArrayBuffer): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', buf)
  return Array.from(new Uint8Array(digest)).map(b => b.toString(16).padStart(2, '0')).join('')
}

function arrayBufferToBase64(bytes: Uint8Array): string {
  let binary = ''
  const step = 0x8000
  for (let i = 0; i < bytes.length; i += step) {
    binary += String.fromCharCode(...bytes.subarray(i, i + step))
  }
  return btoa(binary)
}
