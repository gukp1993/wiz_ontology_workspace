// 唯一请求与错误解析基础层（架构方案 §4：页面不得各自复制 fetch/错误/409 处理）。
// saveCoordinator 与两区 api 模块都从这里取能力；Promise 正常返回即 2xx，
// 非 2xx 一律抛 SaveRequestError，调用者不能静默把失败当成功。
//
// G1（20260917 全局交互评审采纳）：必要读取有界。读取（GET）默认 15 秒超时，
// 从发起请求算到响应体解析完成；写操作（POST）不套用自动超时/重试，慢时继续等待。
// 超时、调用方主动取消、网络失败、服务端错误四类失败必须可区分：
//   failure === 'timeout' | 'aborted' | 'network' | 'http'
// 取消与超时都清理 timer 与监听器；外部 signal（调用方取消）始终保留。
export type RequestFailure = 'timeout' | 'aborted' | 'network' | 'http'

export class SaveRequestError extends Error {
  status: number
  currentRevision: string | null
  data: any
  failure: RequestFailure
  constructor(message: string, status = 0, currentRevision: string | null = null, data: any = null, failure?: RequestFailure) {
    super(message)
    this.status = status
    this.currentRevision = currentRevision
    this.data = data
    this.failure = failure || (status > 0 ? 'http' : 'network')
  }
}

/** 读取（GET）默认超时：集中配置，测试可通过 window.__WIZ_READ_TIMEOUT_MS 注入更短时间。 */
export const READ_TIMEOUT_MS = 15000
export interface RequestOptions { timeoutMs?: number; signal?: AbortSignal | null }
function defaultReadTimeout(): number {
  const injected = Number((globalThis as any).__WIZ_READ_TIMEOUT_MS)
  return Number.isFinite(injected) && injected > 0 ? injected : READ_TIMEOUT_MS
}

/** 通用 JSON 请求：非 2xx 抛 SaveRequestError（409 带 .currentRevision、完整响应体在 .data）。
 * opts.timeoutMs > 0 时启用有界读取；opts.signal 为调用方取消信号（如切换项目/离开页面）。 */
export async function requestJson(url: string, init?: RequestInit, opts: RequestOptions = {}): Promise<any> {
  const timeoutMs = opts.timeoutMs ?? 0
  const external = opts.signal ?? init?.signal ?? null
  const controller = timeoutMs > 0 ? new AbortController() : null
  let timer: ReturnType<typeof setTimeout> | null = null
  const forwardAbort = () => controller?.abort()
  if (controller) {
    if (external?.aborted) controller.abort()
    else external?.addEventListener('abort', forwardAbort)
    timer = setTimeout(() => controller.abort(), timeoutMs)
  }
  try {
    let response: Response
    const abortError = () => {
      const cancelled = !!external?.aborted
      return new SaveRequestError(cancelled ? '请求已取消' : '读取超时', 0, null, null, cancelled ? 'aborted' : 'timeout')
    }
    try {
      const signal = controller?.signal ?? external ?? undefined
      response = await fetch(url, { ...init, ...(signal ? { signal } : {}) })
    } catch {
      if (controller?.signal.aborted || external?.aborted) throw abortError()
      throw new SaveRequestError('网络异常，无法连接服务', 0, null, null, 'network')
    }
    let data: any = null
    let parsed = true
    try { data = await response.json() }
    catch {
      // 读取过程中被超时/取消中断：按超时/取消上报，不能当成“空响应体”
      if (controller?.signal.aborted || external?.aborted) throw abortError()
      parsed = false
    }
    if (!response.ok) throw new SaveRequestError(data?.error || (data?.errors || []).join('；') || `请求失败（${response.status}）`, response.status, data?.currentRevision ?? null, data, 'http')
    // 2xx 但响应体不是合法 JSON：不能静默当成有效数据交给调用方
    if (!parsed) throw new SaveRequestError('服务返回的内容无法解析为有效数据，请稍后重试', response.status, null, null, 'http')
    return data
  } finally {
    if (timer) clearTimeout(timer)
    external?.removeEventListener('abort', forwardAbort)
  }
}

/** GET 且期望 JSON 响应：默认走有界读取（15 秒，可注入）。 */
export async function getJson(url: string, opts: RequestOptions = {}): Promise<any> {
  return requestJson(url, undefined, { timeoutMs: defaultReadTimeout(), ...opts })
}

/** POST JSON 且期望 JSON 响应：写操作不套用读取超时，慢时继续等待结果。 */
export async function postJson(url: string, payload: any, opts: RequestOptions = {}): Promise<any> {
  return requestJson(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }, opts)
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

/** 失败是否「结果不明」：超时/网络中断/主动取消都可能已送达服务端，不能断言未写入。 */
export function isOutcomeUnknown(err: any): boolean {
  return err instanceof SaveRequestError ? err.failure !== 'http' : true
}

/** 服务端 Origin 白名单拒绝（403 · 请求来源不允许）：需要提示改到本机标准地址访问，不能放宽校验。 */
export function isOriginRejected(err: any): boolean {
  return err instanceof SaveRequestError ? err.status === 403 && /来源/.test(err.message) : false
}

/** 当前服务允许的本机访问地址：按页面实际端口推导，不写死测试端口，也不接受任意参数拼接。 */
export function localAccessUrl(hash = ''): string {
  const loc = (globalThis as any).location
  if (!loc) return ''
  const port = loc.port ? ':' + loc.port : ''
  return 'http://127.0.0.1' + port + (loc.pathname || '/') + (loc.search || '') + (hash || '')
}
