// 唯一请求与错误解析基础层（架构方案 §4：页面不得各自复制 fetch/错误/409 处理）。
// saveCoordinator 与两区 api 模块都从这里取能力；Promise 正常返回即 2xx，
// 非 2xx 一律抛 SaveRequestError，调用者不能静默把失败当成功。
export class SaveRequestError extends Error {
  status: number
  currentRevision: string | null
  data: any
  constructor(message: string, status = 0, currentRevision: string | null = null, data: any = null) {
    super(message)
    this.status = status
    this.currentRevision = currentRevision
    this.data = data
  }
}

/** 通用 JSON 请求：非 2xx 抛 SaveRequestError（409 带 .currentRevision、完整响应体在 .data；网络断开 status=0）。 */
export async function requestJson(url: string, init?: RequestInit): Promise<any> {
  let response: Response
  try { response = await fetch(url, init) } catch { throw new SaveRequestError('网络异常，无法连接服务', 0) }
  let data: any = null
  try { data = await response.json() } catch { /* 空响应体或非 JSON */ }
  if (!response.ok) throw new SaveRequestError(data?.error || (data?.errors || []).join('；') || `请求失败（${response.status}）`, response.status, data?.currentRevision ?? null, data)
  return data
}

/** GET 且期望 JSON 响应。 */
export async function getJson(url: string): Promise<any> {
  return requestJson(url)
}

/** POST JSON 且期望 JSON 响应。 */
export async function postJson(url: string, payload: any): Promise<any> {
  return requestJson(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })
}

/** POST JSON 且期望二进制响应（导出 ZIP 等专用，不强行按 JSON 解析）。 */
export async function postBlob(url: string, payload: any): Promise<Blob> {
  let response: Response
  try { response = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }) } catch { throw new SaveRequestError('网络异常，无法连接服务', 0) }
  if (!response.ok) {
    let data: any = null
    try { data = await response.json() } catch { /* 非 JSON 错误体 */ }
    throw new SaveRequestError(data?.error || `请求失败（${response.status}）`, response.status, data?.currentRevision ?? null)
  }
  return response.blob()
}
