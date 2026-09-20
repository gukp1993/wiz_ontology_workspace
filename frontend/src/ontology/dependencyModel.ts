// 当前草稿统一依赖检查（20260920 需求 11～13，开发计划 P1）：
// 对象详情、资产库（共享属性/规则/动作）、本体图谱与保留底层命令共用同一判断，
// 阻断时给出业务名称 + 原因 + 定位所需的稳定 id，避免各入口各写一套规则。
// 纯函数：不写状态、不触网络；只读当前草稿（state）与图（graph）。
import { graphReferenceEntries } from './editorModel'
import { objectsOfRule } from './businessRuleModel'

export interface DependencyEntry {
  /** 依赖方业务名称（对象名/属性名/契约名…） */
  name: string
  /** 依赖原因（对象属性引用/对象关联/接口要求属性…） */
  reason: string
  /** 定位类别：对象 / 共享定义 / 规则 / 动作 / 其他草稿内容 */
  kind: 'object' | 'property' | 'rule' | 'action' | 'other'
  /** 定位用稳定 id（对象类型 @id 或访问对象属性所在对象） */
  objectId?: string
  /** 对象属性（property）时的属性稳定 id */
  propertyId?: string
}

export interface DeleteCheck {
  blocked: boolean
  deps: DependencyEntry[]
  /** 阻断文案：包含名称与原因，末尾给出去处 */
  message: string
}

const fullType = (t: string) => (t.startsWith('mg:') ? t : 'mg:' + t)
const bare = (t: string) => String(t || '').replace(/^mg:/, '')
const label = (node: any, fallback: string) => node?.['rdfs:label'] || fallback

export function typeNameOf(state: any, typeId: string): string {
  const graph = state?.ontology?.['@graph'] || []
  const node = graph.find((n: any) => n['@id'] === typeId || n['@id'] === fullType(typeId))
  return node ? label(node, typeId) : typeId
}

export function propertyNameOf(state: any, propertyId: string): string {
  const graph = state?.ontology?.['@graph'] || []
  const node = graph.find((n: any) => n['@id'] === propertyId)
  return node ? label(node, propertyId) : propertyId
}

/** 共享定义被哪些对象属性引用（对象名 → 属性名，供影响确认与删除阻断共用）。 */
export function sharedPropertyUsages(state: any, sharedId: string): DependencyEntry[] {
  const graph = state?.ontology?.['@graph'] || []
  if (!sharedId) return []
  return graph
    .filter((n: any) => n['@type'] === 'owl:DatatypeProperty' && n['mg:sharedProperty']?.['@id'] === sharedId)
    .map((n: any) => {
      const objectId = n['rdfs:domain']?.['@id'] || ''
      // 共享引用属性记录本身不带名称：回退到共享定义名，再回退 apiName（不展示裸稳定 id）。
      const shared = graph.find((x: any) => x['@id'] === sharedId)
      const name = n['rdfs:label'] || shared?.['rdfs:label'] || n['mg:apiName'] || n['@id']
      return {
        name: typeNameOf(state, objectId) + ' → ' + name,
        reason: '对象属性引用',
        kind: 'property' as const,
        objectId,
        propertyId: n['@id'],
      }
    })
}

/** 契约/接口/项目映射等对某 id 的直接引用（图谱外依赖）。 */
export function externalDependencies(state: any, id: string): DependencyEntry[] {
  return graphReferenceEntries(state, id).map(r => ({ name: r.name, reason: r.reason, kind: 'other' as const }))
}

export function dependencyMessage(target: string, deps: DependencyEntry[], hint: string): string {
  if (!deps.length) return ''
  const names = deps.slice(0, 5).map(d => `${d.name}（${d.reason}）`).join('、')
  const more = deps.length > 5 ? ` 等 ${deps.length} 项` : ''
  return `暂不能删除「${target}」：仍被 ${names}${more} 依赖。${hint}`
}

/** 共享定义删除判定：契约/接口/项目映射或对象属性引用 → 阻断；无引用 → 允许（确认后删除）。 */
export function sharedDeleteCheck(state: any, sharedId: string): DeleteCheck {
  const name = propertyNameOf(state, sharedId)
  const local = sharedPropertyUsages(state, sharedId)
  // 图内本地引用属性（mg:sharedProperty）在本地清单已按「对象 → 属性」列出，
  // 从外部依赖里剔除同名「共享属性」条目，避免同一处引用重复计数。
  const external = externalDependencies(state, sharedId).filter(d => d.reason !== '共享属性')
  const deps = [...external, ...local]
  if (deps.length) {
    const hint = local.length
      ? '请先在对象建模中移除对应引用属性，或改为「转为私有」保留内容；共享定义不会自动解除引用。'
      : '请先处理上述引用。'
    return { blocked: true, deps, message: dependencyMessage(name, deps, hint) }
  }
  return { blocked: false, deps: [], message: '' }
}

/** 规则删除判定：对象关联（业务规则引用）与契约/接口等直接引用 → 阻断。 */
export function ruleDeleteCheck(state: any, ruleId: string): DeleteCheck {
  const rule = (state?.workflow?.businessRules || []).find((r: any) => r?.id === ruleId)
  const name = rule?.name || ruleId
  const assoc = objectsOfRule(state, ruleId).map((typeId: string) => ({
    name: typeNameOf(state, typeId),
    reason: '对象引用规则',
    kind: 'object' as const,
    objectId: typeId,
  }))
  const deps = [...assoc, ...externalDependencies(state, ruleId)]
  if (deps.length) {
    return { blocked: true, deps,
      message: dependencyMessage(name, deps, '请先到对象建模「规则」页签移除引用；规则定义不会自动解除。') }
  }
  return { blocked: false, deps: [], message: '' }
}

/** 动作删除判定：对象关联与契约/接口等直接引用 → 阻断（历史动作走转换提示，由调用方先行处理）。 */
export function actionDeleteCheck(state: any, actionId: string): DeleteCheck {
  const action = (state?.workflow?.actions || []).find((a: any) => a?.id === actionId)
  const name = action?.name || actionId
  const assoc = (state?.workflow?.actionAssociations || [])
    .filter((a: any) => a?.actionId === actionId)
    .map((a: any) => ({
      name: typeNameOf(state, a.objectTypeId),
      reason: '对象关联动作',
      kind: 'object' as const,
      objectId: fullType(a.objectTypeId),
    }))
  const deps = [...assoc, ...externalDependencies(state, actionId)]
  if (deps.length) {
    return { blocked: true, deps,
      message: dependencyMessage(name, deps, '请先到对象建模「动作」页签移除关联；动作定义不会自动删除。') }
  }
  return { blocked: false, deps: [], message: '' }
}

/** 对象删除判定：属性/链接/规则/动作关联等任一依赖 → 阻断（本期保持阻断式，不级联清理）。 */
export function objectDeleteCheck(state: any, objectId: string): DeleteCheck {
  const graph = state?.ontology?.['@graph'] || []
  const name = typeNameOf(state, objectId)
  const full = fullType(objectId)
  const deps: DependencyEntry[] = []
  for (const n of graph) {
    if (n['@id'] === full || n['@id'] === bare(objectId)) continue
    if (n['rdfs:domain']?.['@id'] === full) {
      deps.push({ name: label(n, n['@id']), reason: n['@type'] === 'owl:ObjectProperty' ? '起点链接' : '对象属性', kind: 'property', objectId: full, propertyId: n['@id'] })
    } else if (n['rdfs:range']?.['@id'] === full && n['@type'] === 'owl:ObjectProperty') {
      deps.push({ name: label(n, n['@id']), reason: '终点链接', kind: 'property', objectId: full, propertyId: n['@id'] })
    }
  }
  for (const a of (state?.workflow?.businessRuleAssociations || [])) {
    if (fullType(a?.objectTypeId) === full) {
      deps.push({ name: '业务规则「' + ((state.workflow.businessRules || []).find((r: any) => r.id === a.ruleId)?.name || a.ruleId) + '」',
                  reason: '对象引用规则', kind: 'rule', objectId: full })
    }
  }
  for (const a of (state?.workflow?.actionAssociations || [])) {
    if (fullType(a?.objectTypeId) === full) {
      deps.push({ name: '动作「' + ((state.workflow.actions || []).find((x: any) => x.id === a.actionId)?.name || a.actionId) + '」',
                  reason: '对象关联动作', kind: 'action', objectId: full })
    }
  }
  for (const dep of externalDependencies(state, objectId)) deps.push({ ...dep, kind: 'other' })
  if (deps.length) {
    return { blocked: true, deps,
      message: dependencyMessage(name, deps, '请先移除相应依赖；对象删除为阻断式，不提供一键级联清理。') }
  }
  return { blocked: false, deps: [], message: '' }
}

// ── 共享属性高影响修改的影响确认（需求 11）─────────────────────────────────────

export interface ImpactField { key: string; label: string; old: string; new: string }
export interface SharedImpact {
  /** 是否属于需要确认的高影响修改（数据类型/观测值类型/业务定义） */
  highImpact: boolean
  fields: ImpactField[]
  usages: DependencyEntry[]
}

const FIELD_LABELS: Record<string, string> = {
  'rdfs:comment': '业务定义', 'rdfs:range': '数据类型', 'mg:valueShape': '观测值形态', 'mg:valueType': '观测值类型',
}
const COMPARED = ['rdfs:comment', 'rdfs:range', 'mg:valueShape', 'mg:valueType']

function describe(key: string, node: any): string {
  if (!node) return '—'
  if (key === 'rdfs:range') return String(node['rdfs:range']?.['@id'] || '').replace(/^xsd:/, '') || '未设置'
  if (key === 'mg:valueShape') return node['mg:valueShape'] === 'timeSeries' ? '时间序列' : '单值'
  if (key === 'mg:valueType') return String(node['mg:valueType']?.['@id'] || '').replace(/^xsd:/, '') || '未设置'
  return String(node['rdfs:comment'] || '（空）')
}

/** 比较共享定义编辑前后（草稿节点 vs 提交内容），产出确认弹窗所需字段与引用清单。 */
export function sharedImpactOf(state: any, sharedId: string, before: any, after: any): SharedImpact {
  const fields: ImpactField[] = []
  for (const key of COMPARED) {
    const oldText = describe(key, before)
    const newText = describe(key, after)
    if (oldText !== newText) fields.push({ key, label: FIELD_LABELS[key] || key, old: oldText, new: newText })
  }
  const highImpact = fields.some(f => ['数据类型', '观测值形态', '观测值类型', '业务定义'].includes(f.label))
  return { highImpact, fields, usages: sharedPropertyUsages(state, sharedId) }
}

/** 影响的稳定指纹：编辑内容 + 当前引用集合；任一处变化即让既有确认失效（需求 11/§5）。 */
export function impactFingerprint(state: any, sharedId: string, after: any): string {
  const refs = sharedPropertyUsages(state, sharedId)
    .map(u => u.objectId + '#' + u.propertyId).sort().join('|')
  return JSON.stringify({ id: sharedId, node: after, refs })
}
