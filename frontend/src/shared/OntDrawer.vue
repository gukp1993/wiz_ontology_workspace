<!-- 只读详情抽屉（20260918 列表统一设计 §7）：右侧约 480px 面板，Esc / 点击遮罩 / 关闭按钮均可关闭。
     打开时记录触发元素，关闭归还焦点；触发元素已不在文档时回到列表搜索框（.ont-search input）。
     纯展示容器：正文与底部按钮由插槽提供，不做任何保存，不影响列表筛选条件。 -->
<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
defineProps<{ title: string; subtitle?: string }>()
const emit = defineEmits(['close'])
const card = ref<HTMLElement | null>(null)
let returnFocus: HTMLElement | null = null
function close() { emit('close') }
// Tab 焦点约束在面板内；Esc 关闭（浏览器原生 dialog 语义的手工等价实现）。
function onKey(e: KeyboardEvent) {
  if (e.key === 'Escape') { e.preventDefault(); close(); return }
  if (e.key !== 'Tab') return
  const items = [...(card.value?.querySelectorAll<HTMLElement>('button:not([disabled]),input:not([disabled]),a[href]')) || []]
  if (!items.length) return
  const first = items[0], last = items[items.length - 1]
  if ((e.shiftKey && document.activeElement === first) || (!e.shiftKey && document.activeElement === last)) {
    e.preventDefault(); (e.shiftKey ? last : first).focus()
  }
}
onMounted(() => {
  returnFocus = (document.activeElement as HTMLElement) || null
  document.addEventListener('keydown', onKey)
  card.value?.querySelector<HTMLElement>('button')?.focus()
})
onBeforeUnmount(() => {
  document.removeEventListener('keydown', onKey)
  if (returnFocus?.isConnected) returnFocus.focus()
  else document.querySelector<HTMLElement>('.ont-search input')?.focus()
})
</script>

<template>
  <div class="ont-overlay" @click.self="close">
    <section ref="card" class="ont-drawer" role="dialog" aria-modal="true" :aria-label="title">
      <div class="ont-drawer-head">
        <div><small>{{ subtitle || '定义详情' }}</small><h2>{{ title }}</h2></div>
        <button type="button" class="ont-drawer-close" aria-label="关闭详情" @click="close">×</button>
      </div>
      <div class="ont-drawer-body"><slot/></div>
      <div v-if="$slots.footer" class="ont-drawer-footer"><slot name="footer"/></div>
    </section>
  </div>
</template>
