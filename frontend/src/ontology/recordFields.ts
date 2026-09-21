// 记录级文本字段校验（2026-09-20 字段精简验收修复 A01，与 workbench/workflow.py 的
// `_text_field_errors`/`_json_type_name` 镜像）：
//   必填（规则/动作名称与业务定义）：去首尾空白后非空**文本**；
//   选填（规则内容 content、动作预期效果 effect）：缺失/null/空串/纯空白按未填处理；
//   任何非文本值（对象/数组/数值/布尔）一律受控报错，绝不 String() 掩盖、不自动清洗。
// 规则库、动作库与图谱保存共用这一份口径；错误文案定位到字段，供表单逐字段提示。

/** 报错文案里的 JSON 类型名（布尔先于数值判断——typeof 数值判断要排除 boolean）。 */
export function jsonTypeName(value: unknown): string {
  if (typeof value === 'boolean') return '布尔值'
  if (typeof value === 'number') return '数值'
  if (value === null) return '空值'
  if (Array.isArray(value)) return '数组'
  if (typeof value === 'object') return '对象'
  return typeof value
}

/** 单字段文本校验：返回错误文案（无错误返回 ''）。required=false 时缺失/blank 均合法。 */
export function textFieldError(value: unknown, title: string, required: boolean): string {
  if (value === undefined || value === null) return required ? `请填写${title}` : ''
  if (typeof value !== 'string') return `${title}必须是文本（当前为${jsonTypeName(value)}）`
  if (required && !value.trim()) return `请填写${title}`
  return ''
}

/** 字段名 → 错误文案（仅含出错字段）；library/图谱据此显示字段级原因并保留用户输入。 */
export function collectFieldErrors(entries: [string, string, unknown, boolean][]): Record<string, string> {
  const errors: Record<string, string> = {}
  for (const [key, title, value, required] of entries) {
    const error = textFieldError(value, title, required)
    if (error) errors[key] = error
  }
  return errors
}
