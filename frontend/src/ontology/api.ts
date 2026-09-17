// 本体区 API：读取/保存/校验/发布/版本请求。全部复用 app/http 的错误解析；
// 本体 POST 请求体统一经 modelFormat.requestBody（自动 encodeState）。
import { getJson, requestJson, postJson, postBlob } from '../app/http'
import { requestBody, type ModelRecord } from './modelFormat'

export type OntologyPayload = ModelRecord & { state?: any; revision?: string; projectState?: any }

/** 本体区 POST 基础：requestBody 编码 + 统一错误/409 解析；init 可带 signal 等扩展。 */
export function ontologyPost(path: string, payload: OntologyPayload, init: RequestInit = {}): Promise<any> {
  return requestJson('/api/' + path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: requestBody(payload), ...init })
}

export const listOntologies = () => getJson('/api/ontologies')
export const createOntology = (name: string) => requestJson('/api/ontologies', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name }) })
export const loadStateRaw = (ontologyId: string) => getJson('/api/state?ontology=' + encodeURIComponent(ontologyId))
export const listVersions = (ontologyId: string) => getJson('/api/versions?ontology=' + encodeURIComponent(ontologyId))
export const listReleases = (ontologyId: string) => getJson('/api/releases?ontology=' + encodeURIComponent(ontologyId))
export const versionStateRaw = (ontologyId: string, version: string) => getJson(`/api/version-state?ontology=${encodeURIComponent(ontologyId)}&version=${encodeURIComponent(version)}`)
export const sourceReference = () => getJson('/api/source-reference')

export const validateOntology = (p: OntologyPayload) => ontologyPost('validate', p)
export const workflowCheck = (p: OntologyPayload) => ontologyPost('workflow-check', p)
export const runPreview = (p: OntologyPayload) => ontologyPost('preview', p)
export const publishCheck = (p: OntologyPayload) => ontologyPost('publish-check', p)
export const publishOntology = (p: OntologyPayload) => ontologyPost('publish', p)
export const restoreRelease = (p: OntologyPayload) => ontologyPost('restore', p)
/** 发布/恢复：直接提交从服务端读回的 schema 形态草稿（不经 requestBody 二次编码）。 */
export const publishServerState = (payload: any) => postJson('/api/publish', payload)
export const restoreServerState = (payload: any) => postJson('/api/restore', payload)
export const explorerSnapshot = (p: OntologyPayload, init: RequestInit = {}) => ontologyPost('explorer', p, init)
export const demoObjects = (p: OntologyPayload) => ontologyPost('demo-objects', p)
export const functionPreview = (p: OntologyPayload) => ontologyPost('function-preview', p)
export const actionPreview = (p: OntologyPayload) => ontologyPost('action-preview', p)
export const valueTypeCheck = (p: OntologyPayload) => ontologyPost('value-type-check', p)
export const formatPreview = (p: OntologyPayload) => ontologyPost('format-preview', p)
/** 导出 ZIP：二进制响应专用通道，不按 JSON 解析。 */
export function exportDraftBlob(p: OntologyPayload): Promise<Blob> {
  return postBlob('/api/export', JSON.parse(requestBody(p)))
}
