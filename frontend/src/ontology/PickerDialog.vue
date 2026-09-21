<!-- 统一的「选择已有定义并关联」弹窗外壳（2026-09 交互统一）。
     用于：从属性库添加 / 添加动作 / 添加规则 —— 三处入口此前分别是整页挑选态、页签内联面板、
     独立弹窗，交互不一致；现统一为同一套：标题 + 搜索 + 可滚列表 + 底部（取消 / 确认）。
     「新建定义」类（新增属性 / 新增链接）仍走整页表单，不在此列。
     两种行模式：
     - mode='multi'：行内复选框，多选后点确认 → emit('confirm', ids)
     - mode='action'：行内按钮组（如 复制为私有 / 引用共享），点击即执行 → emit('action', id, value)
     关闭：Esc / 点背板 / 取消；multi 模式下已有勾选时先确认放弃。 -->
<script setup lang="ts">
import { computed, nextTick, onMounted, onBeforeUnmount, ref } from 'vue'
import { appConfirm } from '../shared/appConfirm'

export interface PickerRow {
  id: string
  label: string
  note?: string
  disabled?: boolean
  actions?: { label: string; value: string; disabled?: boolean; danger?: boolean }[]
}
const props = withDefaults(defineProps<{
  title: string
  hint?: string
  items: PickerRow[]
  mode?: 'multi' | 'action'
  searchPlaceholder?: string
  confirmLabel?: string
  emptyText?: string
  emptyHint?: string
  assignLabel?: string
  error?: string
}>(), {
  hint: '', mode: 'multi', searchPlaceholder: '搜索名称', confirmLabel: '',
  emptyText: '没有可选项。', emptyHint: '', assignLabel: '名称', error: '',
})
const emit = defineEmits<{ close: []; confirm: [ids: string[]]; action: [id: string, value: string] }>()

const query = ref('')
const chosen = ref<string[]>([])
const card = ref<HTMLElement | null>(null)
const visible = computed(() => {
  const q = query.value.trim().toLowerCase()
  return props.items.filter(item => !q || (item.label + ' ' + (item.note || '')).toLowerCase().includes(q))
})
const multi = computed(() => props.mode === 'multi')

function toggle(id: string, checked: boolean) {
  const next = new Set(chosen.value)
  if (checked) next.add(id)
  else next.delete(id)
  chosen.value = [...next]
}
async function requestClose() {
  if (multi.value && chosen.value.length && !(await appConfirm({
    title: '放弃已勾选的内容？', message: '已勾选的内容尚未添加，关闭将放弃这些勾选。', danger: false,
  }))) return
  emit('close')
}
function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') { event.stopPropagation(); requestClose() }
}
onMounted(() => nextTick(() => card.value?.focus()))
let previousFocus: HTMLElement | null = null
onMounted(() => { previousFocus = document.activeElement as HTMLElement | null })
onBeforeUnmount(() => previousFocus?.focus?.())
</script>
<template>
<div class="modal-backdrop" @click.self="requestClose">
  <section ref="card" class="modal-card dialog-md picker-card" role="dialog" aria-modal="true"
           :aria-label="title" tabindex="-1" @keydown="onKeydown">
    <h2>{{ title }}</h2>
    <p v-if="hint" class="field-help picker-hint">{{ hint }}</p>
    <p v-if="error" class="inline-error" role="alert">{{ error }}</p>
    <input type="search" :value="query" :placeholder="searchPlaceholder" :aria-label="searchPlaceholder"
           @input="query = ($event.target as HTMLInputElement).value">
    <div class="picker-list">
      <template v-if="visible.length">
        <!-- 多选：整行可点，复选框表示已勾选 -->
        <label v-for="row in multi ? visible : []" :key="row.id" class="picker-row" :class="{ disabled: row.disabled }">
          <input type="checkbox" :disabled="row.disabled" :checked="chosen.includes(row.id)"
                 :aria-label="row.label" @change="toggle(row.id, ($event.target as HTMLInputElement).checked)">
          <span class="picker-row-main">
            <strong>{{ row.label }}</strong>
            <small v-if="row.note" class="muted">{{ row.note }}</small>
            <small v-if="row.disabled" class="muted">已在列表中，无需重复添加</small>
          </span>
        </label>
        <!-- 行内动作：每行一组按钮，点击即执行 -->
        <div v-for="row in multi ? [] : visible" :key="row.id" class="picker-row" :class="{ disabled: row.disabled }">
          <span class="picker-row-main">
            <strong>{{ row.label }}</strong>
            <small v-if="row.note" class="muted">{{ row.note }}</small>
          </span>
          <span class="picker-row-actions">
            <button v-for="action in (row.actions || [])" :key="action.value" type="button"
                    :class="{ 'danger-btn': action.danger }" :disabled="action.disabled || row.disabled"
                    @click="emit('action', row.id, action.value)">{{ action.label }}</button>
          </span>
        </div>
      </template>
      <div v-else class="empty-state">
        <span class="empty-state-ico" aria-hidden="true">◇</span>
        <p>{{ query ? '没有匹配的' + assignLabel + '。' : emptyText }}</p>
        <small v-if="query ? true : emptyHint">{{ query ? '换个关键词试试。' : emptyHint }}</small>
      </div>
    </div>
    <div class="dialogtools">
      <button type="button" @click="requestClose">取消</button>
      <button v-if="multi && confirmLabel" type="button" class="primary" :disabled="!chosen.length"
              @click="emit('confirm', chosen)">{{ confirmLabel }}{{ chosen.length ? '（已选 ' + chosen.length + ' 项）' : '' }}</button>
    </div>
  </section>
</div>
</template>
<style scoped>
.picker-card{width:min(620px,95vw)}
.picker-hint{margin-top:0}
.picker-list{max-height:340px;overflow:auto;margin:12px 0;display:flex;flex-direction:column;gap:2px}
.picker-row{display:flex;align-items:flex-start;gap:10px;padding:10px;border:1px solid transparent;border-radius:var(--r-sm)}
.picker-row:hover{background:var(--paper-2)}
.picker-row.disabled{opacity:.6}
.picker-row input[type=checkbox]{width:17px;min-width:17px;height:17px;margin:3px 0 0;accent-color:var(--blue)}
.picker-row-main{min-width:0;flex:1}
.picker-row-main strong{display:block;font-size:14px}
.picker-row-main small{display:block;font-size:12px;margin-top:3px;overflow-wrap:anywhere}
.picker-row-actions{display:flex;gap:8px;flex:none;flex-wrap:wrap}
.picker-row-actions button{font-size:12px;padding:4px 10px}
</style>
