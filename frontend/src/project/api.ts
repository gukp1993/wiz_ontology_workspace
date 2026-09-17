// 项目区 API：项目、连接、目录与实现相关请求。catalogs 是服务端派生数据，
// 所有出站请求统一剥除（进草稿请求/revision 都会污染哈希）——这是全前端唯一实现点。
import { getJson, postJson } from '../app/http'

const clone = <T,>(value: T): T => JSON.parse(JSON.stringify(value))

/** 深拷贝并剥除 bindings.catalogs；任何把项目状态发给后端的路径都必须先过这里。 */
export function stripCatalogs(state: any): any {
  const out = clone(state)
  if (out?.bindings?.catalogs) delete out.bindings.catalogs
  return out
}

/** 项目区 POST 基础：剥 catalogs + 统一 JSON 编码与错误/409 解析。 */
export function projectPost(path: string, payload: Record<string, any>): Promise<any> {
  const { state, ...rest } = payload
  return postJson('/api/' + path, { ...(state !== undefined ? { state: stripCatalogs(state) } : {}), ...rest })
}

/** 计算函数公式校验/试算（纯计算路由，不写草稿、不持锁）。 */
export const calcEval = (payload: { expression: string; inputs: { id: string; type: string; value?: any }[]; outputType: string; validateOnly?: boolean }) =>
  postJson('/api/calc-eval', payload)

export const listProjects = (ontologyId?: string) => getJson('/api/projects' + (ontologyId ? '?ontology=' + encodeURIComponent(ontologyId) : ''))
export const loadProjectStateRaw = (projectId: string) => getJson('/api/project-state?project=' + encodeURIComponent(projectId))
export const createProject = (payload: { name: string; ontology?: string; version?: string }) => postJson('/api/projects', payload)
export const listProjectReleases = (projectId: string) => getJson('/api/project-releases?project=' + encodeURIComponent(projectId))

export const saveProject = (state: any, revision: string) => projectPost('project-save', { state, revision })
export const validateProjectConfig = (state: any, revision: string) => projectPost('project-validate', { state, revision })
export const publishProject = (state: any, revision: string) => projectPost('project-publish', { state, revision })
export const upgradeCheck = (state: any, revision: string, targetVersion: string) => projectPost('project-upgrade-check', { state, revision, targetVersion })

/** 连接探测：真实网络操作，服务端不持全局写锁；前端也不在保存队列内调用。 */
export const connectionTest = (payload: { projectId: string; connection: any; password?: string; useSaved?: boolean }) => postJson('/api/connection-test', payload)
export const connectionCatalog = (payload: { projectId: string; connection: any; password?: string; useSaved?: boolean }) => postJson('/api/connection-catalog', payload)
export const connectionSecret = (payload: { projectId: string; connectionId: string; action: 'set' | 'clear'; secret?: string }) => postJson('/api/connection-secret', payload)
export const catalogRefresh = (projectId: string, connectionId: string) => postJson('/api/catalog-refresh', { projectId, connectionId })

/** 只读关联取值预览（方案 §3.4/§5）：property 为空表示仅成员预览。
 * 服务端按已保存草稿 + revision 核对执行；前端调用前需确保配置已保存。 */
export const projectPropertyPreview = (payload: { projectId: string; revision: string; objectType: string; instanceId: string; property: string | null }) =>
  postJson('/api/project-property-preview', payload)
