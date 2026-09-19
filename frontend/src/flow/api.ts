// 函数编排区 API：全部请求经 app/http 的统一封装（错误/409 解析一致）。
// 编排与本体/项目是完全独立的状态线，互不携带对方数据。
import { getJson, postJson } from '../app/http'

export const listFlows = (includeDeleted = false) =>
  getJson('/api/flows' + (includeDeleted ? '?includeDeleted=1' : ''))
export const loadFlowStateRaw = (flowId: string) => getJson('/api/flow-state?flow=' + encodeURIComponent(flowId))
export const createFlow = (payload: { name: string; description?: string }) => postJson('/api/flows', payload)
/** connections：当前项目的 MySQL 数据连接（仅 id/name/engine 元数据），供配置检查校验引用。 */
export const saveFlow = (state: any, revision: string, connections?: any[]) =>
  postJson('/api/flow-save', { state, revision, ...(connections ? { connections } : {}) })
export const checkFlow = (state: any, connections?: any[]) =>
  postJson('/api/flow-check', { state, ...(connections ? { connections } : {}) })
export const copyFlow = (flowId: string, name?: string) => postJson('/api/flow-copy', { flowId, ...(name ? { name } : {}) })
export const deleteFlow = (flowId: string) => postJson('/api/flow-delete', { flowId })
/** 运行/测试：targets 省略 = 全图运行（需 revision + 入口参数）；targets = 单节点/链测试（不落盘）。
 *  testMode:'isolated' + inputOverrides（稳定 ID）= 隔离片段测试（契约 04 §3.1，20260919）。 */
export const runFlow = (payload: { state: any; revision?: string; projectId?: string; inputs?: Record<string, any>; targets?: string[]; testMode?: 'isolated'; inputOverrides?: Record<string, Record<string, any>> }) =>
  postJson('/api/flow-run', payload)
