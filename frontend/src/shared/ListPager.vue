<!-- 统一分页条（20260918 列表统一设计补充）：本体列表共用组件，与 SearchField 配套。
     只做展示与翻页事件：update:page 传【绝对页码】，越界防护由按钮禁用承担；
     「第 x–y 项，共 N 项 / 每页 P 项 / 页码」文案与原型一致。表格常驻显示（少于一页时按钮禁用，
     §5.2）；紧凑列表（对象导航）由父级 v-if 控制显隐。 -->
<script setup lang="ts">
withDefaults(defineProps<{ total: number; page: number; pageCount: number; pageSize?: number }>(), { pageSize: 20 })
const emit = defineEmits(['update:page'])
function go(next: number) { emit('update:page', next) }
</script>
<template>
  <div class="list-pager">
    <span>{{ total ? '第 ' + ((page - 1) * pageSize + 1) + '–' + Math.min(page * pageSize, total) + ' 项，共 ' + total + ' 项' : '共 0 项' }}</span>
    <div class="list-pager-ctl">
      <span>每页 {{ pageSize }} 项</span>
      <button type="button" :disabled="page <= 1" aria-label="上一页" @click="go(page - 1)">‹</button>
      <span>{{ page }} / {{ pageCount }}</span>
      <button type="button" :disabled="page >= pageCount" aria-label="下一页" @click="go(page + 1)">›</button>
    </div>
  </div>
</template>
