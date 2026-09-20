// 项目检查基线辅助（P01/P06/P07，2026-09-20 v2 冻结协议）。
// 只做纯计算，不持状态、不发请求、不订阅任何外部变化：
//   ① 把「报告/比较结果对应哪份项目内容」变成可比较的本地内容签名——签名不一致或项目切换即视为过期；
//   ② 与服务端 /api/project-validate 返回的 baseline 做稳定相等比较（依赖变动的本地可判断部分）；
//   ③ 发布幂等 requestId 的生命周期判定：同内容重试保留 key，内容（含 revision）变化必须换新 key；
//   ④ 检查报告的待处理项计数（未配置 ≠ 无问题）。
// 组件（App.vue / ProjectHome.vue / ProjectValidation.vue / ProjectVersion.vue）只负责在
// 既有请求路径上调用这些判定，不另建缓存或轮询。

/** 稳定序列化：对象键排序（数组顺序保留）。undefined 按 null 处理，保证输出总是字符串。 */
export function canonical(value: any): string {
  function sorted(v: any): any {
    if (Array.isArray(v)) return v.map(sorted)
    if (v && typeof v === 'object') return Object.fromEntries(Object.keys(v).sort().map(k => [k, sorted(v[k])]))
    return v === undefined ? null : v
  }
  const text = JSON.stringify(sorted(value))
  return text === undefined ? 'null' : text
}

/** 项目内容签名：参与校验/发布的项目业务内容（含 projectId、引用版本、绑定、实现、连接、参数）。
 *  bindings.catalogs 是服务端派生的目录缓存（不进请求体、不参与服务端内容哈希），必须排除，
 *  否则目录刷新会制造无意义的“报告过期”。 */
export function projectContentSignature(state: any): string {
  if (!state || typeof state !== 'object') return ''
  const view: any = { ...state }
  if (view.bindings && typeof view.bindings === 'object') {
    const bindings: any = { ...view.bindings }
    delete bindings.catalogs
    view.bindings = bindings
  }
  return canonical(view)
}

/** 服务端检查基线的稳定签名；无 baseline（旧服务端/旧协议）时返回空串 = 未知，不做任何“相等”结论。 */
export function baselineSignature(baseline: any): string {
  if (!baseline || typeof baseline !== 'object') return ''
  return canonical(baseline)
}

/** 两份服务端基线是否指向同一依赖事实（缺失任一侧都返回 false：未知不等同于相同）。 */
export function baselineEquals(a: any, b: any): boolean {
  const sa = baselineSignature(a), sb = baselineSignature(b)
  return !!sa && sa === sb
}

/** 发布幂等指纹（客户端侧）：只按即将提交的**项目内容**。
 *  不含 revision：成功发布本身会推进草稿 revision，若把 revision 计入签名，「响应丢失 →
 *  重读最新草稿 → 重试」会换新 key，服务端看不到旧回执而重复出版本（T20）。
 *  与服务端冻结口径一致（request_hash = 规范化项目内容，回放优先于 CAS）。 */
export function publishPayloadSignature(state: any): string {
  return projectContentSignature(state)
}

export interface PendingPublishRequest { id: string; signature: string }

/** 幂等 key 轮换：内容签名未变且已有 key → 复用（结果未知的重试不重复出版本）；
 *  内容变化或尚无 key → 换新 key（同 key 异内容服务端会 409）。 */
export function requestKeyFor(pending: PendingPublishRequest | null, signature: string, mint: () => string): PendingPublishRequest {
  if (pending && pending.id && pending.signature === signature) return pending
  return { id: mint(), signature }
}

/** 检查报告的待处理项计数：阻断错误 + 逐项清单中的未配置项。未配置不等于无问题。 */
export function outstandingIssueCount(report: any): number {
  if (!report || typeof report !== 'object') return 0
  const errors = Array.isArray(report.errors) ? report.errors.length : 0
  const items = Array.isArray(report.items) ? report.items : []
  const unconfigured = items.filter((i: any) => i && i.status === 'unconfigured').length
  return errors + unconfigured
}
