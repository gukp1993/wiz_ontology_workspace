<!-- 共用最小错误呈现（G2 · 20260917 全局交互评审采纳）：
     统一结构 = 操作标题 + 可理解的原因/当前状态 + 真正可用的下一步；技术详情默认折叠。
     只用于「需要用户处理」的错误，短反馈仍走页面内 inline-success/inline-error 或全局提示条；
     保存/发布等关键失败必须持续可见，不能替换成自动消失的 toast。 -->
<script setup lang="ts">
import { ref } from 'vue'
withDefaults(defineProps<{
  title: string
  reason?: string
  hint?: string
  retryLabel?: string
  secondaryLabel?: string
  fixHref?: string          // 可用的替代访问地址（如 Origin 被拒时的本机地址），独立标签页打开
  fixLabel?: string
  details?: string          // 技术详情（默认折叠）：仅状态码/脱敏端点/原因，绝不放凭据或请求正文
  compact?: boolean
}>(), {})
defineEmits(['retry', 'secondary'])
const open = ref(false)
</script>
<template>
<section class="app-error" :class="{ 'app-error-compact': compact }" role="alert">
  <h3 class="app-error-title">{{ title }}</h3>
  <p v-if="reason" class="app-error-reason">{{ reason }}</p>
  <p v-if="hint" class="muted app-error-hint">{{ hint }}</p>
  <p v-if="fixHref" class="app-error-fix">可用的访问地址：<a :href="fixHref" target="_blank" rel="noopener">{{ fixLabel || fixHref }}</a>（在新标签页打开；当前页面的未保存输入会保留，可复制后再切换）</p>
  <div v-if="retryLabel || secondaryLabel || details" class="tools app-error-actions">
    <button v-if="retryLabel" type="button" class="primary" @click="$emit('retry')">{{ retryLabel }}</button>
    <button v-if="secondaryLabel" type="button" @click="$emit('secondary')">{{ secondaryLabel }}</button>
    <button v-if="details" type="button" class="row-link" :aria-expanded="open" @click="open = !open">{{ open ? '收起技术详情' : '技术详情' }}</button>
  </div>
  <pre v-if="details && open" class="app-error-details">{{ details }}</pre>
</section>
</template>

<style scoped>
.app-error{border:1px solid var(--danger-line);background:var(--danger-soft);border-radius:var(--r-md);padding:16px 18px;margin-bottom:16px}
.app-error-compact{padding:12px 14px;margin-bottom:10px}
.app-error-title{margin:0 0 6px;font-size:15px;font-weight:650;color:var(--ink)}
.app-error-reason{margin:0 0 6px;font-size:13px;color:var(--ink-2);overflow-wrap:anywhere}
.app-error-hint{margin:0 0 10px;line-height:1.7}
.app-error-fix{margin:0 0 10px;font-size:13px;color:var(--ink-2);overflow-wrap:anywhere;line-height:1.7}
.app-error-actions{flex-wrap:wrap;row-gap:8px}
.app-error-actions button{white-space:nowrap}
.app-error-details{margin:10px 0 0;max-height:220px;overflow:auto}
</style>
