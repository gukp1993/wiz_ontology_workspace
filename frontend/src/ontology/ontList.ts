// 本体列表通用视图状态（20260918 列表统一设计 T1）：搜索与排序在【全量记录】上执行，再分页——
// 不允许只搜当前页。纯视图状态，不发请求、不触发保存、不产生撤销记录。
import { computed, ref, watch, type ComputedRef, type Ref } from 'vue'

export interface OntTableOptions<T> {
  /** 搜索匹配；query 已 trim+小写。 */
  match?: (row: T, query: string) => boolean
  /** 名称排序比较器（方向由 dir 控制）。 */
  sort?: (a: T, b: T) => number
  pageSize?: number
}

export interface OntTable<T> {
  q: Ref<string>
  page: Ref<number>
  dir: Ref<number>
  filtered: ComputedRef<T[]>
  pageCount: ComputedRef<number>
  paged: ComputedRef<T[]>
  reset: () => void
  toggleSort: () => void
}

export function useOntTable<T extends { id: string }>(source: () => T[], opts: OntTableOptions<T> = {}): OntTable<T> {
  const pageSize = opts.pageSize || 20
  const q = ref(''), page = ref(1), dir = ref(1)
  const filtered = computed(() => {
    const query = q.value.trim().toLowerCase()
    let rows = source()
    if (query && opts.match) rows = rows.filter(row => opts.match!(row, query))
    if (opts.sort) rows = rows.slice().sort((a, b) => dir.value * opts.sort!(a, b))
    return rows
  })
  const pageCount = computed(() => Math.max(1, Math.ceil(filtered.value.length / pageSize)))
  const paged = computed(() => {
    const p = Math.min(page.value, pageCount.value)
    return filtered.value.slice((p - 1) * pageSize, p * pageSize)
  })
  // 搜索变化回第一页；数据收缩后页码按合法范围调整（sync：模板/后续断言不会看到越界页码）。
  watch(q, () => { page.value = 1 }, { flush: 'sync' })
  watch(pageCount, n => { if (page.value > n) page.value = 1 }, { flush: 'sync' })
  function reset() { q.value = ''; page.value = 1; dir.value = 1 }
  function toggleSort() {
    dir.value = -dir.value
    if (page.value > pageCount.value) page.value = pageCount.value
  }
  return { q, page, dir, filtered, pageCount, paged, reset, toggleSort }
}
