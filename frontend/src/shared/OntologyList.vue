<!-- 本体区统一标准表格容器（20260918 列表统一设计 T1/§5）：
     工具栏（搜索 → 筛选插槽 → 总数 → 操作插槽）+ 语义 table（列定义 + 行插槽）+ 空态 + 分页外壳。
     只负责显示与事件：不发请求、不保存、不产生撤销记录；行内容与业务操作由各页面以插槽提供。
     表头 40px、行最小 64px、每页 20 项；样式走全局 .ont-*（style.css 本体列表段），不改全站 table/button。
     初始无数据的唯一主入口在工具栏（§8），空态不放第二个新建按钮。 -->
<script setup lang="ts">
export interface OntColumn { label: string; width?: string; sort?: boolean }
withDefaults(defineProps<{
  columns: OntColumn[]
  ariaLabel: string
  search: string
  total: number
  page: number
  pageCount: number
  searchPlaceholder?: string
  totalText?: string
  pageSize?: number
  sortDesc?: boolean
  emptyTitle: string
  emptyHint?: string
  minWidth?: string
  hasFilter?: boolean
}>(), { searchPlaceholder: '搜索名称或定义', totalText: '', pageSize: 20, sortDesc: false, emptyHint: '', minWidth: '620px', hasFilter: false })
const emit = defineEmits(['update:search', 'sort', 'page', 'clear'])
</script>

<template>
  <div class="ont-tools">
    <div class="ont-search">
      <input type="search" :value="search" :placeholder="searchPlaceholder" :aria-label="searchPlaceholder"
        @input="emit('update:search', ($event.target as HTMLInputElement).value)">
      <button v-if="search" type="button" class="ont-search-clear" aria-label="清空搜索" @click="emit('update:search', '')">×</button>
    </div>
    <slot name="filter"/>
    <span class="ont-total">{{ totalText || (total + ' 项') }}</span>
    <div class="ont-actions"><slot name="actions"/></div>
  </div>
  <div class="ont-scroll">
    <table v-if="total > 0" class="ont-table" :aria-label="ariaLabel" :style="{ minWidth }">
      <colgroup><col v-for="c in columns" :key="c.label" :style="c.width ? { width: c.width } : undefined"></colgroup>
      <thead><tr>
        <th v-for="c in columns" :key="c.label" :aria-sort="c.sort ? (sortDesc ? 'descending' : 'ascending') : undefined">
          <button v-if="c.sort" type="button" class="ont-sort" :title="sortDesc ? '当前按名称降序，点击改为升序' : '当前按名称升序，点击改为降序'" @click="emit('sort')">{{ c.label }} {{ sortDesc ? '↓' : '↑' }}</button>
          <template v-else>{{ c.label }}</template>
        </th>
      </tr></thead>
      <tbody><slot/></tbody>
    </table>
    <div v-else class="ont-empty">
      <div class="ont-empty-ico" aria-hidden="true">≡</div>
      <h3>{{ emptyTitle }}</h3>
      <p v-if="emptyHint">{{ emptyHint }}</p>
      <button v-if="search || hasFilter" type="button" @click="emit('clear')">清空筛选</button>
    </div>
  </div>
  <div class="ont-pager">
    <span>{{ total ? '第 ' + ((page - 1) * pageSize + 1) + '–' + Math.min(page * pageSize, total) + ' 项，共 ' + total + ' 项' : '共 0 项' }}</span>
    <div class="ont-pager-ctl">
      <span>每页 {{ pageSize }} 项</span>
      <button type="button" :disabled="page <= 1" aria-label="上一页" @click="emit('page', -1)">‹</button>
      <span>{{ page }} / {{ pageCount }}</span>
      <button type="button" :disabled="page >= pageCount" aria-label="下一页" @click="emit('page', 1)">›</button>
    </div>
  </div>
</template>
