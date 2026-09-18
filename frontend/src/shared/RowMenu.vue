<!-- 行内低频操作菜单（G4 · 20260917 全局交互评审采纳；20260918 列表统一改为浮层）：
     用于把「维护定义／查看引用」等常用操作留在行内，把删除等低频操作收进更多菜单。
     行为：键盘可开（Enter/Space 打开并把焦点移入首项，↑↓/Home/End 巡航，Esc 关闭并归还焦点）、
     点击外部关闭；删除项置底并带分隔与危险样式。只承载已有能力，不新增删除语义。
     20260918：菜单渲染到 body 并用 fixed 定位——表格有独立滚动容器（.ont-scroll），
     行内 absolute 菜单会被裁切；开合仍由本组件维护，键鼠行为不变。 -->
<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
export interface MenuItem { id: string; label: string; danger?: boolean; disabled?: boolean; note?: string }
const props = withDefaults(defineProps<{ items: MenuItem[]; label?: string; ariaLabel?: string; disabled?: boolean; compact?: boolean }>(), { label: '更多操作', ariaLabel: '', compact: false })
const emit = defineEmits(['pick'])
const open = ref(false)
const wrap = ref<HTMLElement | null>(null)
const trigger = ref<HTMLButtonElement | null>(null)
const menu = ref<HTMLElement | null>(null)
const menuStyle = ref<Record<string, string>>({})
// 浮层定位：右对齐触发器，下方优先；下方放不下时贴到视口底（原型同款边界收敛）。
function placeMenu() {
  const el = trigger.value
  if (!el) return
  const r = el.getBoundingClientRect()
  const width = menu.value?.offsetWidth || 180
  const height = menu.value?.offsetHeight || props.items.length * 36 + 12
  const left = Math.max(8, Math.min(window.innerWidth - width - 8, r.right - width))
  const below = r.bottom + 4
  const top = below + height > window.innerHeight - 8 ? Math.max(8, r.top - height - 4) : below
  menuStyle.value = { left: left + 'px', top: top + 'px', minWidth: width ? width + 'px' : '' }
}
function openMenu() {
  if (props.disabled) return
  open.value = true
  void nextTick(() => { placeMenu(); menu.value?.querySelector<HTMLElement>('[role="menuitem"]:not([disabled])')?.focus() })
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
function onDocumentClick(e: MouseEvent) {
  if (!open.value) return
  const t = e.target as Node
  if (wrap.value?.contains(t) || menu.value?.contains(t)) return
  closeMenu(false)
}
// 滚动/缩放后浮层与触发器会错位：直接关闭（菜单本身很短，重新打开成本极低）。
function onViewportChange() { closeMenu(false) }
onMounted(() => {
  document.addEventListener('click', onDocumentClick)
  window.addEventListener('resize', onViewportChange)
  window.addEventListener('scroll', onViewportChange, true)
})
onBeforeUnmount(() => {
  document.removeEventListener('click', onDocumentClick)
  window.removeEventListener('resize', onViewportChange)
  window.removeEventListener('scroll', onViewportChange, true)
})
</script>
<template>
<div ref="wrap" class="row-menu" @keydown="keydown">
  <button ref="trigger" type="button" class="row-menu-trigger" aria-haspopup="menu" :aria-expanded="open" :aria-label="ariaLabel || label" :disabled="disabled" @click="open ? closeMenu(true) : openMenu()">{{ compact ? '⋯' : label + ' ⌄' }}</button>
  <Teleport to="body">
    <div v-if="open" ref="menu" class="row-menu-list row-menu-float" role="menu" :aria-label="ariaLabel || label" :style="menuStyle" @keydown="keydown">
      <button v-for="it in items" :key="it.id" type="button" role="menuitem" :class="{ danger: it.danger }" :disabled="it.disabled" :title="it.note || undefined" @click="pick(it)">{{ it.label }}</button>
    </div>
  </Teleport>
</div>
</template>
