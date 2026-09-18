// 认证状态与偏好命名空间（20260918 登录与账号体系）。
//
// * 登录态由服务端 Cookie 会话决定，前端不保存令牌；authState 只是本轮渲染依据。
// * 会话失效（任意接口 401）由 http.ts 通知到这里 → 清空状态并回调引导层显示登录页。
// * 本地偏好（收藏、上次项目/编排、界面状态）按账号加前缀存储：换账号不串数据；
//   登录前读到的旧键（无前缀）不迁移、不删除——它属于「未登录时代」的浏览器状态。
import { ref } from 'vue'
import { getJson, postJson, setUnauthenticatedHandler } from './http'

export interface AuthUser { username: string; isAdmin: boolean; createdAt: string }

export const authState = ref<'loading' | 'anonymous' | 'authenticated'>('loading')
export const currentUser = ref<AuthUser | null>(null)
// 登录后置位；偏好模块据此拼接存储键前缀（未登录为空 → 不读写业务偏好）
let prefsNamespace = ''
let onExpired: (() => void) | null = null

/** 注册「会话失效」回调（由引导层设置：清空界面并显示登录页）。 */
export function onSessionExpired(handler: (() => void) | null): void {
  onExpired = handler
}

function applyUser(user: AuthUser | null): void {
  currentUser.value = user
  authState.value = user ? 'authenticated' : 'anonymous'
  prefsNamespace = user ? 'u:' + user.username + ':' : ''
}

/** 启动引导：读取当前登录态（未登录不报错，返回 user: null）。 */
export async function bootstrapAuth(): Promise<AuthUser | null> {
  try {
    const data = await getJson('/api/auth-state')
    applyUser(data?.user || null)
  } catch {
    applyUser(null)  // 服务不可达也先显示登录页；真正的接口错误在登录时反馈
  }
  setUnauthenticatedHandler(() => {
    applyUser(null)
    onExpired?.()
  })
  return currentUser.value
}

export async function login(username: string, password: string): Promise<void> {
  const data = await postJson('/api/auth-login', { username, password })
  applyUser(data?.user || null)
}

export async function register(username: string, password: string): Promise<void> {
  const data = await postJson('/api/auth-register', { username, password })
  applyUser(data?.user || null)
}

export async function logout(): Promise<void> {
  try {
    await postJson('/api/auth-logout', {})
  } finally {
    applyUser(null)
    onExpired?.()   // 退出是「主动的会话结束」：与 401 走同一引导路径，切回登录页
  }
}

/** 当前账号的偏好存储键（带账号前缀）；未登录返回空串，调用方跳过读写。 */
export function prefKey(key: string): string {
  return prefsNamespace ? prefsNamespace + key : ''
}

/** 读偏好：未登录（无命名空间）返回 null，不触碰浏览器存储。 */
export function prefGet(key: string): string | null {
  const full = prefKey(key)
  if (!full) return null
  try { return localStorage.getItem(full) } catch { return null }
}

/** 写偏好：未登录静默忽略；存储不可用（隐私模式等）静默降级。 */
export function prefSet(key: string, value: string): void {
  const full = prefKey(key)
  if (!full) return
  try { localStorage.setItem(full, value) } catch { /* 存储不可用时仅本次会话有效 */ }
}
