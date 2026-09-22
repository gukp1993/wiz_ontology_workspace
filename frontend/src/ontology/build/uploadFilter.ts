// 上传后缀过滤（08 §12.1 第三条）：纯函数，供物料页在选择文件后、上传前过滤。
// 口径：用户输入逗号/空格/分号分隔的扩展名（带点或不带点均可，大小写不敏感）；
// 留空 = 不过滤（全部保留）。

export function parseExtensionFilter(input: string): string[] {
  const raw = String(input ?? '')
  if (!raw.trim()) return []
  const seen = new Set<string>()
  for (const token of raw.split(/[,;；、\s]+/)) {
    if (!token) continue
    // 归一：小写、去掉前导点后统一补一个点（'..' 丢弃，'...java' 视为 '.java'）
    const bare = token.trim().toLowerCase().replace(/^\.+/, '')
    if (bare.length && !bare.includes('.')) seen.add('.' + bare)
  }
  return [...seen]
}

interface NamedFile {
  name: string
  webkitRelativePath?: string
}

/** 返回应上传的文件与跳过数；exts 为空数组时全部保留。 */
export function filterByExtensions<T extends NamedFile>(files: T[], exts: string[]): { keep: T[]; skipped: number } {
  if (!exts.length) return { keep: [...files], skipped: 0 }
  const allowed = new Set(exts)
  const keep: T[] = []
  let skipped = 0
  for (const file of files) {
    const name = file.webkitRelativePath || file.name || ''
    const dot = name.lastIndexOf('.')
    const ext = dot >= 0 ? name.slice(dot).toLowerCase() : ''
    if (allowed.has(ext)) keep.push(file)
    else skipped += 1
  }
  return { keep, skipped }
}

// ── V2-6（G21）大文件夹软引导：按「后缀过滤后计数」判断，超过阈值建议压缩 zip，不硬阻断 ──
/** 阈值常量（需求 §5.3 默认 20，可配：改这里即可；页面与引导层共用单一来源）。 */
export const LARGE_FOLDER_THRESHOLD = 20

/** 后缀过滤后计数是否触发软引导（严格大于阈值；等于阈值不引导）。 */
export function shouldGuideLargeFolder(filteredCount: number): boolean {
  return filteredCount > LARGE_FOLDER_THRESHOLD
}
