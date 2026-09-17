// 动作库与对象动作关联的纯辅助（20260917 需求），与后端 workbench/workflow.py 镜像：
// 本体侧 workflow.actions（definitionVersion 2 为本期简化动作）+ workflow.actionAssociations
// 单一关联集合；项目侧 bindings.actionBindings 引用项目固定发布版本中的有效关联。
export interface ActionAssociation { objectTypeId: string; actionId: string }

export const isActionV2 = (a: any) => !!a && a.definitionVersion === 2

export function actionsOf(state: any): any[] {
  const rows = state?.workflow?.actions
  return Array.isArray(rows) ? rows.filter((a: any) => a && a.id) : []
}

export function associationsOf(state: any): ActionAssociation[] {
  const rows = state?.workflow?.actionAssociations
  return Array.isArray(rows) ? rows.filter((r: any) => r && typeof r.objectTypeId === 'string' && typeof r.actionId === 'string') : []
}

const fullType = (t: string) => (t.startsWith('mg:') ? t : 'mg:' + t)
const bareType = (t: string) => t.replace(/^mg:/, '')
export const comboKey = (objectTypeId: string, actionId: string) => bareType(objectTypeId) + '::' + actionId

/** 有效关联 = 显式集合 ∪ 历史动作 object_type 推导（对象统一为含 mg: 前缀，排序稳定）。 */
export function effectiveAssociations(state: any): ActionAssociation[] {
  const out = new Map<string, ActionAssociation>()
  for (const r of associationsOf(state)) out.set(comboKey(r.objectTypeId, r.actionId), { objectTypeId: fullType(r.objectTypeId), actionId: r.actionId })
  for (const a of actionsOf(state)) {
    if (isActionV2(a)) continue
    const types = Array.isArray(a.object_types) ? a.object_types : (a.object_type ? [a.object_type] : [])
    for (const t of types) if (typeof t === 'string' && t) {
      const key = comboKey(t, a.id)
      if (!out.has(key)) out.set(key, { objectTypeId: fullType(t), actionId: a.id })
    }
  }
  return [...out.values()].sort((x, y) => x.objectTypeId === y.objectTypeId ? x.actionId.localeCompare(y.actionId) : x.objectTypeId.localeCompare(y.objectTypeId))
}

/** 某对象类型的全部有效关联动作（typeId 兼容 mg: 前缀与 bare）。 */
export function associationsOfObject(state: any, typeId: string): ActionAssociation[] {
  if (!typeId) return []
  const bare = bareType(typeId)
  return effectiveAssociations(state).filter(r => bareType(r.objectTypeId) === bare)
}

/** 某动作当前被哪些对象类型引用（反向引用，返回含 mg: 前缀的对象 id）。 */
export function objectsOfAction(state: any, actionId: string): string[] {
  return effectiveAssociations(state).filter(r => r.actionId === actionId).map(r => r.objectTypeId)
}

/** 项目动作绑定行的容错读取（bindings.actionBindings）。 */
export function actionBindingsOf(projectState: any): any[] {
  const rows = projectState?.bindings?.actionBindings
  return Array.isArray(rows) ? rows.filter((r: any) => r && typeof r === 'object') : []
}

/** 写回关联集合：整体替换为去重后的新集合（本体草稿 mutate 内使用）。 */
export function commitAssociations(state: any, rows: ActionAssociation[]) {
  const seen = new Set<string>()
  state.workflow.actionAssociations = rows.filter(r => {
    const key = comboKey(r.objectTypeId, r.actionId)
    if (!r.objectTypeId || !r.actionId || seen.has(key)) return false
    seen.add(key)
    return true
  })
}
