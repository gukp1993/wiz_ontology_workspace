<!-- 统一搜索框（20260918 列表统一设计补充）：本体列表共用组件，与 ListPager 配套。
     受控输入：值由父级持有（:value + update:value 事件）；清空按钮先发空值、再发 clear，
     便于父级在清空时一并重置其它筛选条件（如「已收藏」）。占位文案同时作为默认 aria-label。
     图标与内边距对齐交互原型（.search-field 样式见 style.css）。 -->
<script setup lang="ts">
withDefaults(defineProps<{ value: string; placeholder?: string; ariaLabel?: string }>(), { placeholder: '搜索名称或定义', ariaLabel: '' })
const emit = defineEmits(['update:value', 'clear'])
function clear() { emit('update:value', ''); emit('clear') }
</script>
<template>
  <div class="search-field">
    <svg class="search-field-ico" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><circle cx="8" cy="8" r="5.5"/><path d="m12 12 5 5"/></svg>
    <input type="search" :value="value" :placeholder="placeholder" :aria-label="ariaLabel || placeholder"
      @input="emit('update:value', ($event.target as HTMLInputElement).value)">
    <button v-if="value" type="button" class="search-field-clear" aria-label="清空搜索" @click="clear">×</button>
  </div>
</template>
