// D05（20260920 从物料自动构建本体）：前端 revision 载荷口径回归。
// 运行（仓库根）：node --import ./tests/ts_hooks.mjs tests/ontology_build_frontend.test.mjs
// 契约：文档/接口文档/08-从物料自动构建本体接口.md §4/§7（后端按 _text/_int_arg 校验：
//   物料排除 = String(materialRevision) 不透明 token；合并执行/撤销/差异裁决 = 候选 revision token；
//   重新生成 = 可选整数 scopeRevision，前端省略该字段）。
// 用受控 fetch 抓取真实请求体，不发真实网络请求，也不依赖后端在线。
import assert from 'node:assert/strict'

const api = await import('../frontend/src/ontology/build/api.ts')

const captured = []
globalThis.fetch = async (url, init) => {
  const body = init && init.body ? JSON.parse(init.body) : null
  captured.push({ url: String(url), body })
  return { ok: true, status: 200, json: async () => ({}) }
}
const lastBody = () => captured[captured.length - 1].body
const lastUrl = () => captured[captured.length - 1].url

// ① 物料排除：revision 必须是字符串 token（数字会被后端 _text 拒为 400）
await api.setMaterialExcluded('bk-1', 'bm-1', true, api.materialRevisionToken(7))
assert.equal(typeof lastBody().revision, 'string', 'material-exclude 的 revision 必须是字符串')
assert.equal(lastBody().revision, '7')
assert.equal(lastBody().excluded, true)
assert.equal(lastBody().materialId, 'bm-1')
// ② 清单修订取值：整数/数字字符串都归一为 token；缺失返回空串（页面据此拒绝发送）
assert.equal(api.materialRevisionToken(0), '0')
assert.equal(api.materialRevisionToken('12'), '12')
assert.equal(api.materialRevisionToken(null), '')
assert.equal(api.materialRevisionToken(undefined), '')
assert.equal(api.materialRevisionToken(Number.NaN), '')

// ③ 合并预览是只读：不携带任何 revision；合并执行携带保留项候选 token
await api.mergePreview('bk-1', 'bc-a', ['bc-b'])
assert.equal('revision' in lastBody(), false, 'confirmed=false 不得携带 revision')
assert.equal(lastBody().confirmed, false)
await api.mergeApply('bk-1', 'bc-a', ['bc-b'], 'r-candidate-token')
assert.equal(lastBody().confirmed, true)
assert.equal(lastBody().revision, 'r-candidate-token', 'confirmed=true 携带候选 revision')

// ④ 撤销：候选级 token（保留项当前 revision），不是任务 r-uuid
await api.undoReviewOp('bk-1', 'bo-1', 'r-candidate-after-merge')
assert.equal(lastBody().revision, 'r-candidate-after-merge')
assert.equal(lastBody().opId, 'bo-1')

// ⑤ 重新生成：省略 revision（传任务 token 会被 _int_arg 拒为 400）
await api.regenerate('bk-1')
assert.equal('revision' in lastBody(), false, 'build-regenerate 不得携带 revision')
assert.equal(lastBody().taskId, 'bk-1')
assert.match(lastUrl(), /\/api\/build-regenerate$/)

// ⑥ 差异裁决与编辑/决定：一律候选 token
await api.resolveDiff('bk-1', 'bc-a', 'keepManual', 'r-cand-1')
assert.equal(lastBody().revision, 'r-cand-1')
assert.equal(lastBody().choice, 'keepManual')
await api.updateCandidate('bc-a', { name: '簇' }, 'r-cand-1')
assert.equal(lastBody().revision, 'r-cand-1')
await api.decideCandidate('bc-a', 'include', '弱证据人工确认', 'r-cand-1')
assert.equal(lastBody().revision, 'r-cand-1')
assert.equal(lastBody().reason, '弱证据人工确认')

// ⑦ 范围/物料清单读取的 revision 类型：请求体不参与（GET），仅确认列表端点未拼进 body
await api.listMaterials('bk-1')
assert.equal(captured[captured.length - 1].body, null)
assert.match(lastUrl(), /\/api\/build-materials\?taskId=bk-1$/)

// ⑧ 409 语义：http 层把 currentRevision 透出，页面据此刷新（不自动重发旧 token）
const { SaveRequestError } = await import('../frontend/src/app/http.ts')
globalThis.fetch = async () => ({
  ok: false, status: 409,
  json: async () => ({ error: 'REVISION_CONFLICT', currentRevision: 'r-server-current' }),
})
await assert.rejects(() => api.regenerate('bk-1'), err => {
  assert.ok(err instanceof SaveRequestError)
  assert.equal(err.status, 409)
  assert.equal(err.currentRevision, 'r-server-current')
  assert.equal(api.conflictRevision(err), 'r-server-current')
  return true
})
assert.equal(api.conflictRevision(new Error('普通错误')), null)

console.log('通过：物料排除传字符串清单修订；合并/撤销/差异裁决/编辑/决定传候选 token；重新生成省略 revision；409 透出 currentRevision 供刷新。')
