<!-- 行内低频操作菜单（G4 · 20260917 全局交互评审采纳）：
     用于把「维护定义／查看引用」等常用操作留在行内，把删除等低频操作收进更多菜单。
     行为：键盘可开（Enter/Space 打开并把焦点移入首项，↑↓/Home/End 巡航，Esc 关闭并归还焦点）、
     点击外部关闭；删除项置底并带分隔与危险样式。只承载已有能力，不新增删除语义。 -->
<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
export interface MenuItem { id: string; label: string; danger?: boolean; disabled?: boolean; note?: string }
const props = withDefaults(defineProps<{ items: MenuItem[]; label?: string; ariaLabel?: string; disabled?: boolean }>(), { label: '更多操作', ariaLabel: '' })
const emit = defineEmits(['pick'])
const open = ref(false)
const wrap = ref<HTMLElement | null>(null)
const trigger = ref<HTMLButtonElement | null>(null)
function openMenu() {
  if (props.disabled) return
  open.value = true
  void nextTick(() => wrap.value?.querySelector<HTMLElement>('[role="menuitem"]:not([disabled])')?.focus())
}
function closeMenu(refocus = false) { if (!open.value) return; open.value = false; if (refocus) trigger.value?.focus() }
function keydown(e: KeyboardEvent) {
  const items = [...(e.currentTarget as HTMLElement).querySelectorAll<HTMLElement>('[role="menuitem"]:not([disabled])')]
  if (!items.length) return
  const i = items.indexOf(document.activeElement as HTMLElement)
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); items[(i + (e.key === 'ArrowDown' ? 1 : items.length - 1)) % items.length]?.focus() }
  else if (e.key === 'Home') { e.preventDefault(); items[0]?.focus() }
  else if (e.key === 'End') { e.preventDefault(); items[items.length - 1]?.focus() }
  else if (e.key === 'Escape') { e.preventDefault(); closeMenu(true) }
  else if (e.key === 'Tab') closeMenu(false)
}
function pick(item: MenuItem) { if (item.disabled) return; closeMenu(true); emit('pick', item.id) }
function onDocumentClick(e: MouseEvent) { if (open.value && !wrap.value?.contains(e.target as Node)) closeMenu(false) }
onMounted(() => document.addEventListener('click', onDocumentClick))
onBeforeUnmount(() => document.removeEventListener('click', onDocumentClick))
</script>
<template>
<div ref="wrap" class="row-menu" @keydown="keydown">
  <button ref="trigger" type="button" class="row-menu-trigger" aria-haspopup="menu" :aria-expanded="open" :aria-label="ariaLabel || label" :disabled="disabled" @click="open ? closeMenu(true) : openMenu()">{{ label }} ⌄</button>
  <div v-if="open" class="row-menu-list" role="menu" :aria-label="ariaLabel || label">
    <button v-for="it in items" :key="it.id" type="button" role="menuitem" :class="{ danger: it.danger }" :disabled="it.disabled" :title="it.note || undefined" @click="pick(it)">{{ it.label }}</button>
  </div>
</div>
</template>
