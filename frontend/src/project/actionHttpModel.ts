// 动作 HTTP 接口配置（20260917 需求）：与 workbench/action_http.py 镜像的纯模型层。
// 两侧规则必须一致（字段名、校验文案语义、来源类型）；改动务必同步两边并跑 tests/test_action_http.py。
// 本模块不发起任何请求：请求示意只生成文本，变量保留占位符、认证值脱敏。
import { effectiveProperty, localProperties, propertyTypeLabel, valueShapeOf } from '../ontology/propertyModel'
import { actionsOf } from '../ontology/actionModel'

export const METHODS = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE'] as const
export const PARAM_IN = ['path', 'query', 'header', 'body'] as const
export const BODY_FORMATS = ['json', 'form'] as const
export const SOURCES = ['actionInput', 'instanceId', 'property', 'constant'] as const
export const CONSTANT_TYPES = ['string', 'number', 'boolean'] as const
export const AUTH_TYPES = ['none', 'bearer', 'apiKey'] as const

export const IN_LABELS: Record<string, string> = { path: '路径', query: 'Query', header: '请求头', body: '请求体' }
export const SOURCE_LABELS: Record<string, string> = {
  actionInput: '动作输入', instanceId: '当前对象实例主键', property: '当前对象属性', constant: '固定值',
}
export const METHOD_OPTIONS = METHODS.map(v => ({ value: v, label: v }))
export const IN_OPTIONS = PARAM_IN.map(v => ({ value: v, label: IN_LABELS[v] }))
export const BODY_FORMAT_OPTIONS = [{ value: 'json', label: 'JSON' }, { value: 'form', label: '表单（application/x-www-form-urlencoded）' }]
export const CONSTANT_TYPE_OPTIONS = [{ value: 'string', label: '文本' }, { value: 'number', label: '数值' }, { value: 'boolean', label: '是/否' }]
export const AUTH_TYPE_OPTIONS = [{ value: 'none', label: '无认证' }, { value: 'bearer', label: 'Bearer Token' }, { value: 'apiKey', label: 'API Key' }]
export const MASK = '[受保护凭据]'

export type ParamValue = { from: string; inputId?: string; inputName?: string; propertyId?: string; type?: string; value?: any }
export type ApiParam = { id: string; name: string; in: string; value: ParamValue }
export type ApiAuth = { type: string; credentialId?: string; in?: string; name?: string }
export type ApiImplementation = {
  kind: 'api'; schemaVersion: 2; method: string; path: string; bodyFormat: string
  description: string; parameters: ApiParam[]; auth: ApiAuth
}

export const newParamId = () => 'p_' + (globalThis.crypto?.randomUUID?.().replaceAll('-', '') || Math.random().toString(36).slice(2))

/** 新建配置的初始草稿（用户可见默认值；不用于改写旧数据）。 */
export function emptyApi(): ApiImplementation {
  return { kind: 'api', schemaVersion: 2, method: 'POST', path: '', bodyFormat: 'json', description: '', parameters: [], auth: { type: 'none', credentialId: '', in: 'header', name: '' } }
}

export const isApiV2 = (impl: any): boolean => !!impl && String(impl.kind || '') === 'api' && (impl.schemaVersion === 2 || String(impl.schemaVersion || '') === '2')

/** 宽容读取：缺省值只补在内存视图，不回写存储（旧数据不静默升级）。 */
export function apiView(impl: any) {
  const source = impl && typeof impl === 'object' ? impl : {}
  const method = String(source.method || 'POST').toUpperCase()
  const bodyFormat = String(source.bodyFormat || 'json')
  const auth = source.auth && typeof source.auth === 'object' ? source.auth : { type: 'none' }
  const rows = Array.isArray(source.parameters) ? source.parameters.filter((r: any) => r && typeof r === 'object') : []
  return {
    method, path: String(source.path || ''),
    bodyFormat: (BODY_FORMATS as readonly string[]).includes(bodyFormat) ? bodyFormat : 'json',
    description: String(source.description || ''),
    parameters: rows as ApiParam[],
    auth: {
      type: String(auth.type || 'none'), credentialId: String(auth.credentialId || ''),
      in: String(auth.in || 'header'), name: String(auth.name || ''),
    } as ApiAuth,
  }
}

/** 编辑既有配置时的草稿：以内存视图为底，保留未知字段在原记录上（保存时再合并）。 */
export function draftFrom(impl: any): ApiImplementation {
  const view = apiView(impl)
  return {
    kind: 'api', schemaVersion: 2, method: view.method, path: view.path, bodyFormat: view.bodyFormat,
    description: view.description,
    parameters: view.parameters.map(r => ({ id: String(r.id || newParamId()), name: String(r.name || ''), in: String(r.in || 'body'), value: { ...(r.value && typeof r.value === 'object' ? r.value : { from: '' }) } })),
    auth: { ...view.auth },
  }
}

export function placeholders(path: string): string[] {
  const out: string[] = []
  const text = String(path || '')
  const re = /\{([^{}]+)\}/g
  let hit: RegExpExecArray | null
  while ((hit = re.exec(text))) if (hit[1] && !out.includes(hit[1])) out.push(hit[1])
  return out
}

// ── 上下文：把引用版本定义与项目现状整理成校验/联动所需的纯数据 ──
export interface PropertyInfo { id: string; name: string; api: string; scalar: boolean; configured: boolean; typeLabel: string }
export interface ApiContext {
  actionInputs: { id?: string; name: string; type?: string }[]
  properties: PropertyInfo[]
  propertyById: Record<string, PropertyInfo>
  identityReady: boolean
  credentials: { id: string; name: string }[]
}

const RECOGNIZED_SOURCE_KINDS = ['field', 'redis', 'computed', 'database']
export function hasConfiguredSource(entry: any): boolean {
  if (!entry) return false
  if (typeof entry === 'string') return entry.trim() !== ''      // 旧格式：直接写字段名
  if (typeof entry !== 'object') return false
  return RECOGNIZED_SOURCE_KINDS.includes(String(entry.kind || ''))
}

/** 该对象在项目里的绑定行（身份 + 属性来源）。 */
export function objectBindingRow(projectState: any, objectType: string): any {
  const rows = projectState?.bindings?.object_bindings
  const bare = String(objectType || '').replace(/^mg:/, '')
  return (Array.isArray(rows) ? rows : []).find((r: any) => String(r?.object_type || '').replace(/^mg:/, '') === bare) || null
}

/** 动作在引用版本中真实存在的输入（新版动作只有三个业务字段，通常为空）。 */
export function actionInputsOf(refState: any, actionId: string): { id?: string; name: string; type?: string }[] {
  const action: any = actionsOf(refState).find((a: any) => a.id === actionId)
  const rows = Array.isArray(action?.inputs) ? action.inputs : []
  const seen = new Set<string>()
  const out: { id?: string; name: string; type?: string }[] = []
  for (const row of rows) {
    if (!row || typeof row !== 'object') continue
    const name = String(row.name || '').trim()
    if (!name || seen.has(name)) continue                 // 只兼容已有非空唯一参数名，不凭空生成 ID
    seen.add(name)
    out.push({ ...(row.id ? { id: String(row.id) } : {}), name, ...(row.type ? { type: String(row.type) } : {}) })
  }
  return out
}

export function apiContext(refState: any, projectState: any, objectType: string, actionId: string, credentials: { id: string; name: string }[]): ApiContext {
  const graph = refState?.ontology?.['@graph'] || []
  const typeId = 'mg:' + String(objectType || '').replace(/^mg:/, '')
  const row = objectBindingRow(projectState, objectType)
  const sources = row && typeof row.properties === 'object' && row.properties ? row.properties : {}
  // 只列项目内配置了可用来源的普通标量属性；时间序列与未配置来源在此标记，供界面说明原因。
  const properties: PropertyInfo[] = localProperties(graph, typeId).map((p: any) => {
    const merged: any = effectiveProperty(p, graph)
    const api = String(p['mg:apiName'] || String(p['@id'] || '').replace(/^mg:/, ''))
    return {
      id: String(p['@id']), name: String(merged?.['rdfs:label'] || api), api,
      scalar: valueShapeOf(p, graph) !== 'timeSeries',
      configured: hasConfiguredSource(sources[api]),
      typeLabel: propertyTypeLabel(p, graph),
    }
  })
  const propertyById: Record<string, PropertyInfo> = {}
  for (const item of properties) propertyById[item.id] = item
  // 身份语义与后端 identity_ready 镜像：registered 视为就绪；未知 identity.kind 不解释为就绪。
  const identityReady = !!row && (() => {
    const identity = row.identity
    if (identity && typeof identity === 'object') return String(identity.kind || '') === 'registered'
    return String(row.primary_key || '').trim() !== ''
  })()
  return { actionInputs: actionInputsOf(refState, actionId), properties, propertyById, identityReady, credentials }
}

/** 取值来源下拉：不可用的来源禁用并说明原因（不伪造选项）。 */
export function sourceOptions(ctx: ApiContext): { value: string; label: string; disabled?: boolean; reason?: string }[] {
  const inputReason = ctx.actionInputs.length ? '' : '此动作在引用版本中没有输入参数'
  const identityReason = ctx.identityReady ? '' : '尚未完成实例识别的对象，请先配置主键'
  return [
    { value: 'instanceId', label: SOURCE_LABELS.instanceId, ...(identityReason ? { disabled: true, reason: identityReason } : {}) },
    { value: 'property', label: SOURCE_LABELS.property, ...(ctx.properties.some(p => p.scalar && p.configured) ? {} : { disabled: true, reason: '当前对象暂无已配置来源的标量属性' }) },
    { value: 'constant', label: SOURCE_LABELS.constant },
    { value: 'actionInput', label: SOURCE_LABELS.actionInput, ...(inputReason ? { disabled: true, reason: inputReason } : {}) },
  ]
}

/** 非固定值行的「值类型」列：继承来源类型（只读展示）。 */
export function inheritedTypeText(param: ApiParam, ctx: ApiContext): string {
  const value = param.value || ({} as ParamValue)
  if (value.from === 'instanceId') return '按实际主键类型 · 继承来源'
  if (value.from === 'property') {
    const info = ctx.propertyById[String(value.propertyId || '')]
    return (info ? info.typeLabel : '请选择属性') + ' · 继承来源'
  }
  if (value.from === 'actionInput') {
    const input = ctx.actionInputs.find(i => (value.inputId ? i.id === value.inputId : i.name === value.inputName))
    const label = input?.type ? ({ string: '文本', number: '数值', boolean: '是/否', object: '对象引用' } as Record<string, string>)[input.type] || input.type : ''
    return (label || '请选择输入') + ' · 继承来源'
  }
  return '请选择来源'
}

// ── 请求示意（纯文本；不查询设备或属性真实值） ──
function queryEncode(text: string): string { return encodeURIComponent(text) }

export function buildPreview(impl: any, ctx: ApiContext): string {
  const view = apiView(impl)
  const valueOf = (param: ApiParam): any => {
    const value: any = param.value || {}
    if (value.from === 'instanceId') return '${当前对象.实例主键}'
    if (value.from === 'actionInput') {
      const input = ctx.actionInputs.find(i => (value.inputId ? i.id === value.inputId : i.name === value.inputName))
      return '${动作输入.' + (input?.name || '待选择') + '}'
    }
    if (value.from === 'property') {
      const info = ctx.propertyById[String(value.propertyId || '')]
      return '${当前对象.' + (info?.name || '待选择') + '}'
    }
    if (value.from === 'constant') {
      if (value.type === 'number') {
        const text = value.value === null || value.value === undefined ? '' : String(value.value).trim()
        return text !== '' && Number.isFinite(Number(text)) ? Number(text) : '待填写数值'
      }
      if (value.type === 'boolean') return value.value === true || value.value === 'true'
      return value.value === null || value.value === undefined ? '' : String(value.value)
    }
    return '待选择来源'
  }
  const headers: string[] = []
  const query: string[] = []
  const body: Record<string, any> = {}
  let url = view.path || '（待填写接口地址）'
  let hasBody = false
  for (const param of view.parameters) {
    const name = String(param.name || '').trim() || '待填写参数名'
    const value = valueOf(param)
    const known = (param.value || {}).from === 'constant' && value !== '待填写数值'
    if (param.in === 'path') url = url.split('{' + name + '}').join(String(value))
    if (param.in === 'query') query.push(name + '=' + (known ? queryEncode(String(value)) : String(value)))
    if (param.in === 'header') headers.push(name + ': ' + String(value))
    if (param.in === 'body') { hasBody = true; body[name] = value }
  }
  const auth = view.auth
  if (auth.type === 'bearer') headers.push('Authorization: Bearer ' + MASK)
  if (auth.type === 'apiKey') {
    const name = auth.name || '待填写认证参数名'
    if (auth.in === 'query') query.push(name + '=' + queryEncode(MASK))
    else headers.push(name + ': ' + MASK)
  }
  if (query.length) url += (url.includes('?') ? '&' : '?') + query.join('&')
  if (hasBody) headers.push('Content-Type: ' + (view.bodyFormat === 'form' ? 'application/x-www-form-urlencoded' : 'application/json'))
  const head = view.method + ' ' + url + '\n' + headers.join('\n')
  if (!hasBody) return head
  const text = view.bodyFormat === 'form'
    ? Object.entries(body).map(([k, v]) => k + '=' + queryEncode(String(v))).join('&')
    : JSON.stringify(body, null, 2)
  return head + '\n\n' + text
}

export function summaryText(impl: any): string {
  const view = apiView(impl)
  return (view.method || 'POST') + ' ' + (view.path || '（未填写地址）')
}

// ── 校验（与 workbench/action_http.py 的 api_issues 规则一致） ──
export interface ApiIssues { errors: string[]; warnings: string[] }

function urlIssues(path: string): string[] {
  const issues: string[] = []
  const text = String(path || '').trim()
  if (!text) return ['请填写接口地址。']
  if (!/^https?:\/\//i.test(text)) return ['接口地址必须是 http:// 或 https:// 开头的完整地址（一期没有项目 API 主机配置）。']
  if (/^https?:\/\/[^/?#]*@/i.test(text)) issues.push('接口地址不能包含账号密码（userinfo），认证请通过凭据引用维护。')
  if (text.includes('#')) issues.push('接口地址中的 #fragment 不参与 HTTP 请求，请移除。')
  return issues
}

function constantIssues(label: string, value: ParamValue): string[] {
  const kind = String(value.type || '')
  if (!(CONSTANT_TYPES as readonly string[]).includes(kind)) return [label + '：固定值类型必须是文本、数值或是否。']
  const raw = value.value
  if (kind === 'number') {
    const text = raw === null || raw === undefined ? '' : String(raw).trim()
    if (text === '' || !Number.isFinite(Number(text))) return [label + '：请输入有效数值（0 也是有效固定值）。']
  }
  if (kind === 'boolean' && !(raw === true || raw === false || raw === 'true' || raw === 'false')) return [label + '：固定布尔值必须是「是」或「否」。']
  if (kind === 'string' && (raw === null || raw === undefined)) return [label + '：固定文本值无效（空串请显式保存为空文本）。']
  return []
}

function sourceIssues(label: string, value: ParamValue, ctx: ApiContext): string[] {
  const origin = String(value.from || '')
  if (!(SOURCES as readonly string[]).includes(origin)) return [label + '：请选择取值来源。']
  if (origin === 'instanceId') return ctx.identityReady ? [] : [label + '：当前对象尚未完成实例识别（主键）配置，请先在「实例识别」中配置后再选此来源。']
  if (origin === 'actionInput') {
    if (!ctx.actionInputs.length) return [label + '：此动作在项目引用的本体版本中没有输入参数，请改用实例主键、对象属性或固定值。']
    const hit = ctx.actionInputs.some(i => (value.inputId ? i.id === value.inputId : !!value.inputName && i.name === value.inputName))
    return hit ? [] : [label + '：选择的动作输入不在引用版本中，请重新选择。']
  }
  if (origin === 'property') {
    if (!value.propertyId) return [label + '：请选择对象属性。']
    const info = ctx.propertyById[String(value.propertyId)]
    if (!info) return [label + '：引用的对象属性不在项目引用版本中，请重新选择。']
    if (!info.scalar) return [label + '：时间序列属性不能作为本期的标量参数（如需要请改用其他属性）。']
    if (!info.configured) return [label + '：该属性尚未在「属性取值」中配置来源，无法作为参数取值。']
    return []
  }
  return constantIssues(label, value)
}

const paramLabel = (index: number, param: ApiParam): string => {
  const name = String(param.name || '').trim()
  const position = IN_LABELS[String(param.in || '')] || '未选位置'
  return '参数 ' + index + '（' + (name || '未填写参数名') + ' · ' + position + '）'
}

export function validateApi(impl: any, ctx: ApiContext): ApiIssues {
  const view = apiView(impl)
  const errors: string[] = []
  const warnings: string[] = []
  if (!(METHODS as readonly string[]).includes(view.method)) errors.push('请求方式无效：' + (view.method || '未填写') + '；可选 ' + METHODS.join('、') + '。')
  errors.push(...urlIssues(view.path))

  const holders = placeholders(view.path)
  const declared = view.parameters.filter(p => p.in === 'path').map(p => String(p.name || '').trim())
  for (const name of holders) if (!declared.includes(name)) errors.push('路径占位符 {' + name + '} 尚未配置对应的路径参数。')
  for (const name of declared) if (name && !holders.includes(name)) errors.push('参数（' + name + ' · 路径）：地址中不存在对应的 {' + name + '} 占位符。')

  const queryStart = view.path.indexOf('?')
  const knownQuery: string[] = queryStart >= 0
    ? view.path.slice(queryStart + 1).split('&').filter(Boolean).map(pair => decodeURIComponent(pair.split('=')[0]))
    : []

  const seen: Record<string, string> = {}
  const headerNames = new Set<string>()
  let hasBody = false
  view.parameters.forEach((param, index) => {
    const label = paramLabel(index + 1, param)
    const name = String(param.name || '').trim()
    const position = String(param.in || '')
    if (!name) errors.push(label + '：请填写参数名。')
    if (!(PARAM_IN as readonly string[]).includes(position)) { errors.push(label + '：参数位置无效，请选择路径、Query、请求头或请求体。'); return }
    if (position === 'body') hasBody = true
    const key = position + ':' + (position === 'header' ? name.toLowerCase() : name)
    if (name && seen[key]) errors.push(label + '：同一位置的参数名重复（与' + seen[key] + '冲突）。')
    else if (name) seen[key] = label
    if (position === 'query' && name && knownQuery.includes(name)) warnings.push(label + '：地址中已有同名 Query 参数，请在地址或参数表中统一维护一处。')
    if (position === 'header' && name) headerNames.add(name.toLowerCase())
    errors.push(...sourceIssues(label, param.value || ({} as ParamValue), ctx))
  })

  if (view.method === 'GET' && hasBody) errors.push('GET 请求不允许请求体参数，请把参数改到 Query 或路径。')
  if (headerNames.has('content-type')) errors.push('请求头参数重复声明 Content-Type；请求体格式会自动生成，请移除该请求头参数。')

  const auth = view.auth
  if (!(AUTH_TYPES as readonly string[]).includes(auth.type)) errors.push('认证方式无效：' + (auth.type || '未填写') + '。')
  if (auth.type !== 'none') {
    if (!auth.credentialId) errors.push('请选择当前项目的 API 凭据引用。')
    else if (!ctx.credentials.some(c => c.id === auth.credentialId)) errors.push('选择的 API 凭据不在当前项目中（可能已被清理），请重新选择或登记。')
  }
  if (auth.type === 'bearer' && headerNames.has('authorization')) errors.push('Authorization 已由认证配置提供，请移除参数表中重复的请求头参数。')
  if (auth.type === 'apiKey') {
    if (!String(auth.name || '').trim()) errors.push('请填写 API Key 的参数名称（例如 X-API-Key）。')
    if (auth.in !== 'header' && auth.in !== 'query') errors.push('API Key 参数位置只能是请求头或 Query。')
    if (auth.name) {
      const key = String(auth.in) + ':' + (auth.in === 'header' ? auth.name.toLowerCase() : auth.name)
      if (seen[key]) errors.push('API Key 参数与参数表中的 ' + seen[key] + ' 位置和名称重复，请统一维护一处。')
    }
  }
  return { errors, warnings }
}
