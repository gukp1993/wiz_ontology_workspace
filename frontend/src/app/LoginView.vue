<!-- 登录 / 注册页（20260918 登录与账号体系）：
     未登录时整个工作台替换为本页；登录或注册成功即进入工作区。
     同一表单切换两种模式（登录 / 注册），提交走 app/auth.ts（唯一请求出口）。
     错误就地显示（用户名或密码不正确 / 用户名已被使用 / 规则提示）；密码不落任何本地存储。 -->
<script setup lang="ts">
import { computed, ref } from 'vue'
import { login, register } from './auth'

const emit = defineEmits(['done'])
const mode = ref<'login' | 'register'>('login')
const username = ref(''), password = ref(''), busy = ref(false), error = ref('')

const _title = computed(() => (mode.value === 'login' ? '登录' : '注册账号'))
const submitLabel = computed(() => (mode.value === 'login' ? '登录' : '注册并进入'))
const canSubmit = computed(() => !!username.value.trim() && !!password.value && !busy.value)

function switchMode() {
  mode.value = mode.value === 'login' ? 'register' : 'login'
  error.value = ''
  password.value = ''
}

async function submit() {
  if (!canSubmit.value) return
  busy.value = true
  error.value = ''
  try {
    if (mode.value === 'login') await login(username.value.trim(), password.value)
    else await register(username.value.trim(), password.value)
    password.value = ''
    emit('done')
  } catch (e: any) {
    error.value = e?.message || (mode.value === 'login' ? '登录失败' : '注册失败')
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="login-page">
    <section class="login-card" aria-label="登录与注册">
      <div class="login-brand">
        <span class="login-mark" aria-hidden="true">◈</span>
        <div>
          <h1>本体工作台</h1>
          <p class="muted">数据按账号隔离：登录后只看到本账号的本体、项目与编排。</p>
        </div>
      </div>
      <form class="login-form" @submit.prevent="submit">
        <label class="editor-field">
          <span class="field-title">用户名</span>
          <input v-model="username" name="username" autocomplete="username" :disabled="busy"
                 placeholder="例如：admin" aria-label="用户名"/>
        </label>
        <label class="editor-field">
          <span class="field-title">密码</span>
          <input v-model="password" name="password" type="password" :autocomplete="mode === 'login' ? 'current-password' : 'new-password'"
                 :disabled="busy" placeholder="至少 4 个字符" aria-label="密码"/>
        </label>
        <p v-if="error" class="inline-error" role="alert">{{ error }}</p>
        <div class="login-actions">
          <button type="submit" class="primary" :disabled="!canSubmit">{{ busy ? '请稍候…' : submitLabel }}</button>
          <button type="button" class="row-link" :disabled="busy" @click="switchMode">
            {{ mode === 'login' ? '没有账号？注册一个' : '已有账号？去登录' }}
          </button>
        </div>
      </form>
      <p class="login-hint muted">
        {{ mode === 'login'
          ? '初始账号由管理员创建（存量数据归属 admin）。忘记密码需由管理员重置。'
          : '注册后即进入空白工作空间；账号之间数据完全隔离。' }}
      </p>
    </section>
  </div>
</template>

<style scoped>
/* 居中卡片：沿用工作台视觉（浅灰底、白色面板、蓝色主操作），独立于业务布局。 */
.login-page{min-height:100vh;display:flex;align-items:center;justify-content:center;background:var(--bg);padding:24px}
.login-card{width:min(420px,100%);background:var(--paper);border:1px solid var(--line);border-radius:var(--r-md);padding:28px;box-shadow:0 1px 2px rgba(24,44,62,.06)}
.login-brand{display:flex;gap:12px;align-items:flex-start;margin-bottom:20px}
.login-brand h1{font-size:18px;margin:0 0 4px}
.login-brand p{margin:0;font-size:13px;line-height:1.7}
.login-mark{width:34px;height:34px;border-radius:50%;background:var(--blue-soft);color:var(--blue);display:inline-flex;align-items:center;justify-content:center;font-size:17px;flex:none}
.login-form .editor-field{margin:0 0 14px}
.login-actions{display:flex;align-items:center;gap:12px;margin-top:18px;flex-wrap:wrap}
.login-actions .primary{min-width:120px;height:38px}
.login-hint{margin:18px 0 0;font-size:12px;line-height:1.7}
</style>
