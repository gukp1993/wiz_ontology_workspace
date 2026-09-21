// 业务规则纯辅助，与 workbench/workflow.py 容错读取镜像：
// workflow.businessRules 规则（2026-09-20 精简：名称/业务定义必填、规则内容选填；历史 output 零丢失）
// + workflow.businessRuleAssociations 单一引用集合。
// 对象类型统一为完整稳定 @id（含 mg: 前缀）；缺失键按空数组读取。
import { collectFieldErrors } from './recordFields'

export interface RuleAssociation { objectTypeId: string; ruleId: string }

// 编辑/新建表单字段（2026-09-20 精简）：不再提供「输出结果」输入；历史 output 见 RULE_LEGACY_FIELDS。
export const RULE_FIELDS: [string, string][] = [['name', '规则名称'], ['description', '业务定义'], ['content', '规则内容']]
/** 历史保留字段（只读展示，不参与编辑与必填校验）。 */
export const RULE_LEGACY_FIELDS: [string, string][] = [['output', '历史补充说明（原输出结果）']]

const fullType = (t: string) => (t.startsWith('mg:') ? t : 'mg:' + t)
export const bareType = (t: string) => String(t || '').replace(/^mg:/, '')
export const ruleComboKey = (objectTypeId: string, ruleId: string) => bareType(objectTypeId) + '::' + ruleId

export function rulesOf(state: any): any[] {
  const rows = state?.workflow?.businessRules
  return Array.isArray(rows) ? rows.filter((r: any) => r && typeof r.id === 'string' && r.id) : []
}

export function ruleAssociationsOf(state: any): RuleAssociation[] {
  const rows = state?.workflow?.businessRuleAssociations
  return Array.isArray(rows) ? rows.filter((r: any) => r && typeof r.objectTypeId === 'string' && typeof r.ruleId === 'string') : []
}

export function ruleById(state: any, ruleId: string): any | null {
  return rulesOf(state).find((r: any) => r.id === ruleId) || null
}

/** 某对象类型引用的规则（typeId 兼容 mg: 前缀与 bare；缺规则定义的悬空引用原样保留供提示）。 */
export function rulesOfObject(state: any, typeId: string): { ruleId: string; rule: any | null }[] {
  if (!typeId) return []
  const bare = bareType(typeId)
  return ruleAssociationsOf(state)
    .filter(r => bareType(r.objectTypeId) === bare)
    .map(r => ({ ruleId: r.ruleId, rule: ruleById(state, r.ruleId) }))
}

/** 某规则当前被哪些对象类型引用（反向引用，返回完整 @id，排序稳定）。 */
export function objectsOfRule(state: any, ruleId: string): string[] {
  return ruleAssociationsOf(state)
    .filter(r => r.ruleId === ruleId)
    .map(r => fullType(r.objectTypeId))
    .sort()
}

/** 写回引用集合：整体替换并按组合去重、丢弃空值（本体草稿 mutate 内使用）。 */
export function commitRuleAssociations(state: any, rows: RuleAssociation[]) {
  const seen = new Set<string>()
  state.workflow.businessRuleAssociations = rows.filter(r => {
    const key = ruleComboKey(r.objectTypeId, r.ruleId)
    if (!r.objectTypeId || !r.ruleId || seen.has(key)) return false
    seen.add(key)
    return true
  })
}

// --- 记录级校验（2026-09-20 字段精简验收修复 A01，与 workflow.py `_business_rule_errors` 镜像） ---

/** 规则字段错误（键 → 文案）：名称/业务定义必填文本，规则内容选填文本。 */
export function ruleFieldErrors(record: any): Record<string, string> {
  return collectFieldErrors([
    ['name', '规则名称', record?.name, true],
    ['description', '业务定义', record?.description, true],
    ['content', '规则内容', record?.content, false],
  ])
}

/** 规则记录是否可以保存（字段错误为空）。 */
export const ruleRecordValid = (record: any) => Object.keys(ruleFieldErrors(record)).length === 0

/** 编辑草稿初始值：文本原样；非文本旧值保留原值（由 ruleFieldErrors 报「必须是文本」，
 *  不静默转成字符串、不清洗——用户在字段里改写后即可保存）。 */
export function ruleDraftOf(rule: any): Record<string, any> {
  const draft: Record<string, any> = {}
  for (const [key] of RULE_FIELDS) draft[key] = rule ? rule[key] ?? '' : ''
  return draft
}
