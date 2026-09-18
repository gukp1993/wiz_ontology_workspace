import { createApp, h, ref } from 'vue'
import App from './App.vue'
import LoginView from './app/LoginView.vue'
import { bootstrapAuth, onSessionExpired } from './app/auth'
import './style.css'

// 启动引导（20260918 登录与账号体系）：
// 1) 先读登录态；未登录只渲染登录页，工作台整棵树不挂载（不请求任何业务数据）。
// 2) 登录/注册成功后才挂载 App；会话失效（任意接口 401，由 http.ts 通知）时卸载并回到登录页。
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
