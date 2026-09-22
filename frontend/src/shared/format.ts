// 全站时间显示的唯一出口。刻意不用 toLocaleString：它按浏览器 locale 输出 `2026/9/22 00:09:41`
// 这样的形状，同一份数据在不同机器上宽度与分隔符都不同，既与按字符数定的固定列宽打架，也让各页
// 对不齐（列表页 `2026-09-22 14:12`、编排页 `2026/9/22 00:09:41` 就是同一时间戳的两种写法）。
// 空值给 —；不可解析原样回显，便于排查脏数据而不是静默变成 Invalid Date。
function stamp(value: string, withSeconds: boolean): string {
  if (!value) return '—'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return value
  const p = (n: number) => String(n).padStart(2, '0')
  const hms = `${p(d.getHours())}:${p(d.getMinutes())}${withSeconds ? ':' + p(d.getSeconds()) : ''}`
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${hms}`
}

/** 列表/表格用：`2026-09-22 14:12` */
export function formatDateTime(value: string): string {
  return stamp(value, false)
}

/** 需要秒的调试与刷新提示位点用：`2026-09-22 14:12:07` */
export function formatDateTimeSec(value: string): string {
  return stamp(value, true)
}
