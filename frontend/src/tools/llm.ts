// LLM 提供方配置 API（编排 Python/计算节点的 LLM 代执行用；请求经 app/http 统一封装）。
// 密钥只写不读回：列表/保存响应只有元数据与 keyConfigured 状态。
import { getJson, postJson } from '../app/http'

export const listProviders = () => getJson('/api/llm-providers')
export interface ProviderPayload {
  providerId?: string
  name: string
  endpoint: string
  model: string
  apiKey?: string
  timeout?: number
  temperature?: number
  isDefault?: boolean
}
export const saveProvider = (payload: ProviderPayload) => postJson('/api/llm-provider-save', payload)
export const deleteProvider = (providerId: string) => postJson('/api/llm-provider-delete', { providerId })
/** 设为默认：只切换默认项指针，不重写配置（幂等）。 */
export const setDefaultProvider = (providerId: string) => postJson('/api/llm-provider-default', { providerId })
/** 连通性探测：按已存 providerId，或按临时配置（不落盘）测试。 */
export const testProvider = (payload: Partial<ProviderPayload> & { providerId?: string }) =>
  postJson('/api/llm-provider-test', payload)
