// 节点详情字段 schema —— 自旧仓库 shared/fields.js 复制后改为当前工作台五类定义字段。
// 字段 key 与 legacyBridge 的领域映射一一对应（写入本体 JSON-LD / workflow 结构）。
// 优先复用当前表单同名字段语义；list 类型按行拆分为数组。
export const FIELDS = {
  对象: [
    { key: 'displayName', label: '对象名称', type: 'text', required: true },
    { key: 'description', label: '业务定义', type: 'textarea', cls: 'definition', full: true, hint: '说明它是什么，用什么业务边界区分。' },
    { key: 'aliases', label: '别名', type: 'list', hint: '每行一个别名（选填）' },
  ],
  共享属性: [
    { key: 'displayName', label: '属性名称', type: 'text', required: true },
    { key: 'description', label: '业务定义', type: 'textarea', cls: 'definition', full: true },
    { key: 'dataType', label: '数据类型', type: 'select', options: ['文本', '数值', '是／否', '日期', '时间', '数组', '结构体', '时间序列'] },
    { key: 'valueSuffix', label: '单位', type: 'text', hint: '如 kW、%，选填' },
  ],
  私有属性: [
    { key: 'displayName', label: '属性名称', type: 'text', required: true },
    { key: 'description', label: '业务定义', type: 'textarea', cls: 'definition', full: true },
    { key: 'dataType', label: '数据类型', type: 'select', options: ['文本', '数值', '是／否', '日期', '时间', '数组', '结构体', '时间序列'] },
    { key: 'ownerName', label: '所属对象', type: 'text', readonly: true, hint: '创建时确定；当前不支持改变所属对象' },
  ],
  规则: [
    // 20260920 字段精简：名称/业务定义必填、规则内容选填；历史 output 见 LEGACY_FIELDS（只读区）。
    { key: 'name', label: '规则名称', type: 'text', required: true },
    { key: 'description', label: '业务定义', type: 'textarea', cls: 'definition', full: true, required: true },
    { key: 'content', label: '规则内容', type: 'textarea', cls: 'definition', full: true, hint: '计算公式、口径、约束等详细内容（选填）' },
  ],
  动作: [
    { key: 'name', label: '动作名称', type: 'text', required: true },
    { key: 'description', label: '业务定义', type: 'textarea', cls: 'definition', full: true, required: true },
    { key: 'effect', label: '预期效果', type: 'textarea', hint: '操作成功后期望出现的业务状态变化（选填，不会执行任何指令）' },
  ],
}

/** 历史只读字段（20260920 字段精简）：有值才展示，不参与编辑与必填；不进保存 payload。 */
export const LEGACY_FIELDS = {
  规则: [{ key: 'output', label: '历史补充说明（原输出结果）' }],
  动作: [],
}

const DATA_TYPE_KEYS = { 文本: 'string', 数值: 'double', '是／否': 'boolean', 日期: 'date', 时间: 'dateTime', 数组: 'array', 结构体: 'struct' }

/** 领域 dataType 描述 → 表单显示值（与 propertyModel.dataTypeLabel 对齐的子集）。 */
export function dataTypeToLabel(dt) {
  if (!dt) return '文本'
  if (dt.type === 'timeSeries') return '时间序列'
  const hit = Object.entries(DATA_TYPE_KEYS).find(([, v]) => v === dt.type)
  return hit ? hit[0] : dt.type || '文本'
}
export function labelToDataType(label) {
  if (!label || label === '时间序列') return { type: 'timeSeries', valueType: 'double' }
  const key = DATA_TYPE_KEYS[label]
  return { type: key || 'string' }
}

/**
 * 某类型节点的非空详情字段条目（复制自旧 fieldEntries，结构不变）。
 * 历史只读字段（如规则 output）追加在末尾：图谱/预览的关联详情与规则库、对象页口径一致。
 */
export function fieldEntries(type, data = {}) {
  const entries = (FIELDS[type] || [])
    .map((spec) => {
      const v = data[spec.key]
      const isList = spec.type === 'list'
      if (Array.isArray(v)) {
        if (!v.length) return null
        return { key: spec.key, label: spec.label, isList: true, value: v }
      }
      if (v == null || String(v).trim() === '') return null
      return { key: spec.key, label: spec.label, isList: false, value: String(v) }
    })
    .filter(Boolean)
  for (const spec of LEGACY_FIELDS[type] || []) {
    const v = data[spec.key]
    if (v == null || String(v).trim() === '') continue
    entries.push({ key: spec.key, label: spec.label, isList: false, value: String(v), legacy: true })
  }
  return entries
}
