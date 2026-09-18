import { createApp, h, ref } from 'vue'
import App from './App.vue'
import LoginView from './app/LoginView.vue'
import { bootstrapAuth, onSessionExpired } from './app/auth'
import './style.css'

// 启动引导（20260918 登录与账号体系）：
// 1) 先读登录态；未登录只渲染登录页，工作台整棵树不挂载（不请求任何业务数据）。
// 2) 登录/注册成功后才挂载 App；会话失效（任意接口 401，由 http.ts 通知）时卸载并回到登录页。

// ── 旧页面兜底（20260918）───────────────────────────────────────────────────────
// 工作台重新构建后，仍在运行的旧页面持有已删除的入口/分块名（Vite 构建会清空
// frontend/dist，文件名带内容 hash）——此时浏览器抛出的是
// "Failed to fetch dynamically imported module" 或压缩后的 TDZ 类 ReferenceError，
// 用户完全看不懂。这里在入口统一识别这类错误并给出「刷新页面」的明确指引，
// 覆盖所有懒加载路径（当前为 Excel 解析分块的动态 import），不让它只留在控制台。
const STALE_PAGE_HINT = '页面资源已更新（工作台可能刚重新构建过），请刷新页面后重试。'
const STALE_PATTERNS = [
  /dynamically imported module/i,
  /cannot access '?[A-Za-z_$][\w$]*'? before initialization/i,
  /importModule is not defined/i,
  /loading chunk \d+ failed/i,
  /failed to load module script/i,
]

function isStalePageError(value: unknown): boolean {
  const text = String((value as any)?.message || value || '')
  return STALE_PATTERNS.some(re => re.test(text))
}

/** 页面顶部横幅提示（幂等：同一错误只弹一次；不依赖任何业务组件）。 */
let staleBannerShown = false
function showStaleBanner() {
  if (staleBannerShown) return
  staleBannerShown = true
  const bar = document.createElement('div')
  bar.className = 'stale-page-banner'
  bar.setAttribute('role', 'alert')
  bar.textContent = STALE_PAGE_HINT
  const reload = document.createElement('button')
  reload.type = 'button'
  reload.textContent = '刷新页面'
  reload.onclick = () => location.reload()
  bar.appendChild(reload)
  document.body.appendChild(bar)
}

window.addEventListener('error', event => {
  if (isStalePageError(event.error || event.message)) showStaleBanner()
})
window.addEventListener('unhandledrejection', event => {
  if (isStalePageError((event as PromiseRejectionEvent).reason)) showStaleBanner()
})

const authed = ref(false)
const booting = ref(true)

const Root = {
  setup() {
    onSessionExpired(() => { authed.value = false })
    bootstrapAuth().then(user => { authed.value = !!user; booting.value = false })
    return () => {
      if (booting.value) return h('div', { class: 'app-boot' })
      return authed.value ? h(App) : h(LoginView, { onDone: () => { authed.value = true } })
    }
  },
}

createApp(Root).mount('#app')
