// legacyBridge —— 旧图谱编辑器与当前工作台之间的唯一适配层（需求 §4：适配层不承担交互算法）。
//
// 方向一（读投影）：当前本体草稿（JSON-LD @graph + workflow）→ 旧编辑器 draft 会话模型
//   { name, nodes:[{id,type,name,x,y,data}], edges:[{id,source,target,relation,description}] }。
//   节点 ID = `lg:<类型前缀>:<领域稳定ID>`（改名不变）；私有属性天然携带所属对象（rdfs:domain）。
//   边 ID = 稳定身份：链接定义 ID / 对象属性定义 ID（共享引用）/ 私有属性 ID（归属）/
//   类型+双方稳定 ID（规则、动作关联）——平行边、自关联如实上图。
// 方向二（领域命令）：画布上的新建/编辑/连线/删除 → 对当前 state 的明确领域变更，
//   经宿主 before-change（具名撤销）→ 变更 → changed（保存协调器自动持久化，409 沿用当前策略）。
//   绝不把 cy.json 反向覆盖本体；坐标/视口/筛选是视图状态，只写本机偏好。
// 撤销/重做：快照 = 领域五片（@graph + businessRules/Associations + actions/Associations）的深克隆；
//   应用即整体替换这五片（换回真实模型，不是只动画布）；本体切换或外部变更后重建基线。
import { reactive } from 'vue'
import { graphSignature } from '../ontologyGraphModel'
import {
  effectiveAssociations, commitAssociations,
} from '../actionModel'
import {
  ruleAssociationsOf, commitRuleAssociations,
} from '../businessRuleModel'
import { effectiveProperty, localProperties, referencesOf } from '../propertyModel'
import { graphReferences } from '../editorModel'
import { prefGet, prefSet } from '../../app/auth'
import { dataTypeToLabel, labelToDataType } from './shared/fields.js'

const trim = (v) => String(v ?? '').trim()
const fullTypeId = (id) => { const s = trim(id); return s && !s.startsWith('mg:') ? 'mg:' + s : s }

// ---------- 读投影 ----------

/** 五类节点 + 全部关系的 draft 投影。positions: {节点ID:{x,y}} 视图坐标（可空）。 */
export function projectToDraft(state, positions = {}) {
  const graph = Array.isArray(state?.ontology?.['@graph']) ? state.ontology['@graph'] : []
  const objects = graph.filter(n => n?.['@type'] === 'owl:Class' && n['@id'])
  const shared = graph.filter(n => n?.['@type'] === 'mg:SharedProperty' && n['@id'])
  const props = graph.filter(n => n?.['@type'] === 'owl:DatatypeProperty' && n['@id'])
  const links = graph.filter(n => n?.['@type'] === 'owl:ObjectProperty' && n['@id'])
  const rules = (Array.isArray(state?.workflow?.businessRules) ? state.workflow.businessRules : []).filter(r => r?.id)
  const actions = (Array.isArray(state?.workflow?.actions) ? state.workflow.actions : []).filter(a => a?.id)

  const nodes = []
  const edges = []
  const idOf = {
    对象: d => 'lg:obj:' + d, 共享属性: d => 'lg:sp:' + d, 私有属性: d => 'lg:pp:' + d,
    规则: d => 'lg:rule:' + d, 动作: d => 'lg:action:' + d,
  }
  for (const o of objects) {
    const d = trim(o['@id'])
    nodes.push({ id: idOf.对象(d), domainId: d, type: '对象', name: trim(o['rdfs:label']) || d, x: 0, y: 0,
      data: { displayName: trim(o['rdfs:label']) || d, description: trim(o['rdfs:comment']), aliases: aliasesOf(o) } })
  }
  for (const s of shared) {
    const d = trim(s['@id'])
    nodes.push({ id: idOf.共享属性(d), domainId: d, type: '共享属性', name: trim(s['rdfs:label']) || d, x: 0, y: 0,
      data: { displayName: trim(s['rdfs:label']) || d, description: trim(s['rdfs:comment']), dataType: dataTypeToLabel(dtOf(s)), valueSuffix: trim(s['mg:valueSuffix']) } })
  }
  for (const p of props) {
    const sharedId = trim(p['mg:sharedProperty']?.['@id'])
    if (sharedId) continue // 共享引用以边表达，定义节点在共享属性
    const d = trim(p['@id'])
    const owner = trim(p['rdfs:domain']?.['@id'])
    const ownerName = owner ? trim(objects.find(o => trim(o['@id']) === owner)?.['rdfs:label']) || owner : ''
    nodes.push({ id: idOf.私有属性(d), domainId: d, type: '私有属性', name: trim(p['rdfs:label']) || trim(p['mg:apiName']) || d, x: 0, y: 0,
      data: { displayName: trim(p['rdfs:label']) || trim(p['mg:apiName']) || d, description: trim(p['rdfs:comment']), dataType: dataTypeToLabel(dtOf(p)), ownerName } })
  }
  for (const r of rules) {
    const d = trim(r.id)
    nodes.push({ id: idOf.规则(d), domainId: d, type: '规则', name: trim(r.name) || d, x: 0, y: 0,
      data: { name: trim(r.name) || d, description: trim(r.description), content: trim(r.content), output: trim(r.output) } })
  }
  for (const a of actions) {
    const d = trim(a.id)
    nodes.push({ id: idOf.动作(d), domainId: d, type: '动作', name: trim(a.name) || d, x: 0, y: 0,
      data: { name: trim(a.name) || d, description: trim(a.description), effect: trim(a.effect) } })
  }
  // 坐标：视图偏好（未命中的新节点由编辑器落位）
  for (const n of nodes) { const p = positions[n.id]; if (p) { n.x = p.x; n.y = p.y } }

  const objectIds = new Set(objects.map(o => trim(o['@id'])))
  for (const l of links) {
    const source = fullTypeId(l['rdfs:domain']?.['@id']), target = fullTypeId(l['rdfs:range']?.['@id'])
    if (!objectIds.has(source) || !objectIds.has(target)) continue // 悬空端点不建边（数据异常由概览校验呈现）
    edges.push({ id: 'lg:link:' + trim(l['@id']), source: idOf.对象(source), target: idOf.对象(target),
      relation: trim(l['rdfs:label']) || '未命名链接', description: trim(l['rdfs:comment']), kind: '对象链接', domainId: trim(l['@id']) })
  }
  for (const p of props) {
    const sharedId = trim(p['mg:sharedProperty']?.['@id'])
    const owner = fullTypeId(p['rdfs:domain']?.['@id'])
    if (sharedId && objectIds.has(owner) && shared.some(s => trim(s['@id']) === sharedId)) {
      edges.push({ id: 'lg:sref:' + trim(p['@id']), source: idOf.对象(owner), target: idOf.共享属性(sharedId),
        relation: '引用共享属性', description: trim(p['rdfs:comment']) || '对象引用共享属性库中的定义。', kind: '共享引用', domainId: trim(p['@id']) })
    } else if (!sharedId && owner && objectIds.has(owner)) {
      edges.push({ id: 'lg:own:' + trim(p['@id']), source: idOf.对象(owner), target: idOf.私有属性(trim(p['@id'])),
        relation: '私有属性', description: '对象私有的属性定义。', kind: '私有属性', domainId: trim(p['@id']) })
    }
  }
  for (const a of ruleAssociationsOf(state)) {
    const obj = fullTypeId(a.objectTypeId), rule = trim(a.ruleId)
    if (!objectIds.has(obj) || !rules.some(r => trim(r.id) === rule)) continue
    edges.push({ id: `lg:ruleassoc:${obj}|${rule}`, source: idOf.对象(obj), target: idOf.规则(rule),
      relation: '关联规则', description: '对象引用此业务规则。', kind: '规则关联', domainId: `${obj}|${rule}` })
  }
  for (const a of effectiveAssociations(state)) {
    const obj = fullTypeId(a.objectTypeId), act = trim(a.actionId)
    if (!objectIds.has(obj) || !actions.some(x => trim(x.id) === act)) continue
    edges.push({ id: `lg:actionassoc:${obj}|${act}`, source: idOf.对象(obj), target: idOf.动作(act),
      relation: '关联动作', description: '对象关联此动作。', kind: '动作关联', domainId: `${obj}|${act}` })
  }
  return { name: trim(state?.workflow?.objective?.name) || '本体图谱', nodes, edges }
}

function aliasesOf(n) {
  const raw = n?.['mg:aliases']
  const v = raw && typeof raw === 'object' && '@value' in raw ? raw['@value'] : raw
  return Array.isArray(v) ? v.map(x => trim(x)).filter(Boolean) : []
}
function dtOf(p) {
  const m = effectiveProperty(p, [])
  if (m?.['mg:valueShape'] === 'timeSeries') return { type: 'timeSeries', valueType: String(m?.['rdfs:range']?.['@id'] || 'xsd:double').replace('xsd:', '') }
  return { type: String(m?.['rdfs:range']?.['@id'] || 'xsd:string').replace('xsd:', '') }
}

// ---------- 领域桥 ----------

export function createLegacyBridge({ getState, ontologyId, emitBeforeChange, emitChanged, commitNow, saveStatus }) {
  const state = reactive({
    graphName: '',
    draft: { name: '', nodes: [], edges: [] },
    saveText: '已保存',
    externalChanged: 0,   // 外部内容变化计数（编辑器 watch 后重建画布并清撤销栈）
  })
  let positions = {}       // 会话位置表 {节点ID:{x,y}}：视图状态，不进领域
  let baseSignature = ''
  let selfMutating = false // 本桥发起的领域变更：signature watch 跳过（不算外部变化）
  let prefKey = ''
  let saveTimer = null

  const snap = () => {
    const s = getState()
    return JSON.stringify({
      graph: s?.ontology?.['@graph'] || [],
      rules: s?.workflow?.businessRules || [], ruleAssoc: s?.workflow?.businessRuleAssociations || [],
      actions: s?.workflow?.actions || [], actionAssoc: s?.workflow?.actionAssociations || [],
    })
  }
  const captureUndo = () => JSON.parse(snap())
  const applyUndo = (snapshot, actionLabel) => {
    emitBeforeChange({ actionLabel: actionLabel || '撤销图谱编辑' })
    const s = getState()
    s.ontology['@graph'] = JSON.parse(JSON.stringify(snapshot.graph))
    s.workflow.businessRules = JSON.parse(JSON.stringify(snapshot.rules))
    s.workflow.businessRuleAssociations = JSON.parse(JSON.stringify(snapshot.ruleAssoc))
    s.workflow.actions = JSON.parse(JSON.stringify(snapshot.actions))
    s.workflow.actionAssociations = JSON.parse(JSON.stringify(snapshot.actionAssoc))
    selfMutating = true
    emitChanged()
    reload(true)
  }

  // ---- 视图偏好（账号+本体隔离由 prefGet/prefSet 前缀保证；此处 key 不含账号） ----
  function setPrefs(key) { prefKey = 'ontologyGraph:legacy:' + (key || '') }
  function loadPrefs() {
    try {
      const raw = prefGet(prefKey)
      if (!raw) return
      const v = JSON.parse(raw)
      if (v && typeof v === 'object') {
        if (v.positions && typeof v.positions === 'object') positions = { ...positions, ...filterPositions(v.positions) }
        if (v.viewport && Number.isFinite(v.viewport.zoom)) state.prefsViewport = v.viewport
        if (typeof v.inspOpen === 'boolean') state.inspOpenPref = v.inspOpen
        if (v.typeVisible && typeof v.typeVisible === 'object') state.typeVisiblePref = v.typeVisible
      }
    } catch { /* 损坏缓存退默认 */ }
  }
  function filterPositions(raw) {
    const out = {}
    for (const [id, p] of Object.entries(raw)) if (p && Number.isFinite(p.x) && Number.isFinite(p.y)) out[id] = { x: p.x, y: p.y }
    return out
  }
  function savePrefsDebounced() {
    clearTimeout(saveTimer)
    saveTimer = setTimeout(() => {
      try { prefSet(prefKey, JSON.stringify({ positions, viewport: state.prefsViewport || null, inspOpen: !!state.inspOpenPref, typeVisible: state.typeVisiblePref || null })) } catch { /* 存储不可用仅本次会话 */ }
    }, 300)
  }
  function flushPrefs() { clearTimeout(saveTimer); try { prefSet(prefKey, JSON.stringify({ positions, viewport: state.prefsViewport || null, inspOpen: !!state.inspOpenPref, typeVisible: state.typeVisiblePref || null })) } catch { /* 同上 */ } }
  function setPosition(id, x, y) { positions[id] = { x: Math.round(x), y: Math.round(y) }; savePrefsDebounced() }
  function dropPositions(ids) { for (const id of ids) delete positions[id]; savePrefsDebounced() }
  function allPositions() { return { ...positions } }

  // ---- 会话模型 ----
  function reload(metaOnly = false) {
    const s = getState()
    state.graphName = trim(s?.workflow?.objective?.name) || '本体图谱'
    state.draft = projectToDraft(s, positions)
    baseSignature = snap()
    if (!metaOnly) state.externalChanged++
  }
  function checkExternal() {
    if (selfMutating) { selfMutating = false; return false }
    const sig = snap()
    if (sig !== baseSignature) { reload(); return true }
    return false
  }

  // ---- 领域命令 ----
  const newHex = () => crypto.randomUUID().replaceAll('-', '')
  const graph = () => getState().ontology['@graph']
  const workflow = () => getState().workflow

  function domainCreateNode({ type, name }) {
    const s = getState()
    let id, record
    if (type === '对象') {
      id = 'mg:object_' + newHex()
      record = { '@id': id, '@type': 'owl:Class', 'rdfs:label': name, 'rdfs:comment': '' }
    } else if (type === '共享属性') {
      id = 'mg:p_' + newHex()
      record = { '@id': id, '@type': 'mg:SharedProperty', 'rdfs:label': name, 'rdfs:comment': '', 'rdfs:range': { '@id': 'xsd:string' } }
    } else if (type === '规则') {
      id = 'rule_' + newHex().slice(0, 12)
      record = { id, name, description: '', content: '', output: '' }
    } else if (type === '动作') {
      id = 'act_' + newHex().slice(0, 12)
      record = { id, name, description: '', effect: '', definitionVersion: 2, status: 'active' }
    } else {
      return { error: '私有属性请通过连线面板或对象详情创建：需要先确定所属对象。' }
    }
    emitBeforeChange({ actionLabel: `新建${type}「${name}」`, target: { kind: type, id } })
    if (type === '规则') (s.workflow.businessRules = s.workflow.businessRules || []).push(record)
    else if (type === '动作') (s.workflow.actions = s.workflow.actions || []).push(record)
    else graph().push(record)
    selfMutating = true
    emitChanged()
    const nid = { 对象: 'lg:obj:', 共享属性: 'lg:sp:', 规则: 'lg:rule:', 动作: 'lg:action:' }[type] + id
    reload(true)
    return { id: nid, domainId: id }
  }

  /** 编辑保存：data 为表单字段（含 dataType 显示标签）；按类型写回领域字段。 */
  function domainSaveNode(nodeId, { name, data }) {
    const s = getState()
    const [, family, ...rest] = nodeId.split(':')
    const domainId = rest.join(':')
    const target = { obj: '对象', sp: '共享属性', pp: '私有属性', rule: '规则', action: '动作' }[family]
    if (!target) return { error: '未知节点类型' }
    const labelBefore = nodeNameOf(nodeId)
    const dup = nameDuplication(target, domainId, name)
    if (dup) return { error: dup }
    emitBeforeChange({ actionLabel: `修改${target}「${labelBefore}」`, target: { kind: target, id: domainId } })
    if (target === '对象') {
      const n = graph().find(x => x['@id'] === domainId)
      if (!n) return { error: '定义已不存在' }
      n['rdfs:label'] = name
      n['rdfs:comment'] = trim(data.description)
      writeAliases(n, data.aliases)
    } else if (target === '共享属性') {
      const n = graph().find(x => x['@id'] === domainId)
      if (!n) return { error: '定义已不存在' }
      n['rdfs:label'] = name
      n['rdfs:comment'] = trim(data.description)
      writeDataType(n, data.dataType)
      if ('valueSuffix' in data) n['mg:valueSuffix'] = trim(data.valueSuffix)
    } else if (target === '私有属性') {
      const n = graph().find(x => x['@id'] === domainId)
      if (!n) return { error: '定义已不存在' }
      n['rdfs:label'] = name
      n['rdfs:comment'] = trim(data.description)
      writeDataType(n, data.dataType)
    } else if (target === '规则') {
      const r = (s.workflow.businessRules || []).find(x => x.id === domainId)
      if (!r) return { error: '定义已不存在' }
      r.name = name; r.description = trim(data.description); r.content = trim(data.content); r.output = trim(data.output)
    } else {
      const a = (s.workflow.actions || []).find(x => x.id === domainId)
      if (!a) return { error: '定义已不存在' }
      a.name = name; a.description = trim(data.description); a.effect = trim(data.effect)
    }
    selfMutating = true
    emitChanged()
    reload(true)
    return {}
  }
  function writeAliases(n, list) {
    const arr = Array.isArray(list) ? list.map(x => trim(x)).filter(Boolean) : []
    if (arr.length) n['mg:aliases'] = { '@type': '@json', '@value': arr }
    else delete n['mg:aliases']
  }
  function writeDataType(n, label) {
    const dt = labelToDataType(label)
    if (dt.type === 'timeSeries') { n['mg:valueShape'] = 'timeSeries' }
    else delete n['mg:valueShape']
    n['rdfs:range'] = { '@id': 'xsd:' + (dt.type === 'timeSeries' ? dt.valueType : dt.type) }
  }
  function nodeNameOf(nodeId) {
    return state.draft.nodes.find(n => n.id === nodeId)?.name || nodeId
  }
  /** 同类显示名唯一（私有属性按所属对象内唯一）；改名撞名给明确原因。 */
  function nameDuplication(target, domainId, name) {
    const n = name.trim()
    if (!n) return '名称不能为空'
    const d = projectToDraft(getState(), {})
    if (target === '私有属性') {
      const me = d.nodes.find(x => x.id === 'lg:pp:' + domainId)
      const owner = me?.data?.ownerName || ''
      const hit = d.nodes.find(x => x.type === '私有属性' && x.id !== 'lg:pp:' + domainId && x.data.ownerName === owner && x.name === n)
      return hit ? `同一对象下已有同名属性「${n}」` : ''
    }
    const hit = d.nodes.find(x => x.type === target && x.id !== prefixId(target, domainId) && x.name === n)
    return hit ? `已存在同名${target}「${n}」` : ''
  }
  const KIND_PREFIX = { 对象: 'lg:obj:', 共享属性: 'lg:sp:', 私有属性: 'lg:pp:', 规则: 'lg:rule:', 动作: 'lg:action:' }
  function prefixId(target, domainId) { return (KIND_PREFIX[target] || '') + domainId }

  /** 删除节点（含引用保护）。返回 {error} 或 {removedEdgeIds}（画布级联由编辑器处理）。 */
  function domainDeleteNode(nodeId) {
    const s = getState()
    const [, family, ...rest] = nodeId.split(':')
    const domainId = rest.join(':')
    const target = { obj: '对象', sp: '共享属性', pp: '私有属性', rule: '规则', action: '动作' }[family]
    const label = nodeNameOf(nodeId)
    if (target === '对象') {
      const refs = graphReferences(s, domainId)
      if (refs.length) return { error: `暂不能删除：请先处理引用（${refs.join('、')}）。` }
      const ruleRefs = ruleAssociationsOf(s).filter(a => fullTypeId(a.objectTypeId) === domainId)
      if (ruleRefs.length) return { error: `暂不能删除：此对象仍引用 ${ruleRefs.length} 条业务规则；请先在「规则」页签移除引用。` }
      const assocCount = effectiveAssociations(s).filter(a => fullTypeId(a.objectTypeId) === domainId).length
      emitBeforeChange({ actionLabel: `删除对象「${label}」`, target: { kind: 'object', id: domainId } })
      s.ontology['@graph'] = graph().filter(n => n['@id'] !== domainId)
      if (assocCount) commitAssociations(s, effectiveAssociations(s).filter(a => fullTypeId(a.objectTypeId) !== domainId))
    } else if (target === '共享属性') {
      const refs = referencesOf(graph(), domainId)
      if (refs.length) return { error: `暂不能删除：${refs.length} 个对象属性正引用此共享定义；请先移除引用。` }
      emitBeforeChange({ actionLabel: `删除共享属性「${label}」`, target: { kind: 'sharedProperty', id: domainId } })
      s.ontology['@graph'] = graph().filter(n => n['@id'] !== domainId)
    } else if (target === '私有属性') {
      emitBeforeChange({ actionLabel: `删除私有属性「${label}」`, target: { kind: 'property', id: domainId } })
      s.ontology['@graph'] = graph().filter(n => n['@id'] !== domainId)
    } else if (target === '规则') {
      const refs = ruleAssociationsOf(s).filter(a => trim(a.ruleId) === domainId)
      if (refs.length) return { error: `暂不能删除：${refs.length} 个对象仍引用此规则；请先移除引用。` }
      emitBeforeChange({ actionLabel: `删除规则「${label}」`, target: { kind: 'rule', id: domainId } })
      s.workflow.businessRules = (s.workflow.businessRules || []).filter(r => r.id !== domainId)
    } else {
      const refs = effectiveAssociations(s).filter(a => trim(a.actionId) === domainId)
      if (refs.length) return { error: `暂不能删除：${refs.length} 个对象仍关联此动作；请先移除关联。` }
      emitBeforeChange({ actionLabel: `删除动作「${label}」`, target: { kind: 'action', id: domainId } })
      s.workflow.actions = (s.workflow.actions || []).filter(a => a.id !== domainId)
    }
    selfMutating = true
    emitChanged()
    reload(true)
    return {}
  }

  /**
   * 批量连线：先全量校验，全部合法才一次提交（需求 §3.2：有失败不静默部分成功）。
   * pairs: [{sourceId, targetId}]（lg: 节点 ID）；relation/description 只在对象→对象链接时使用。
   */
  function domainCreateEdges(pairs, { relation, description } = {}) {
    const s = getState()
    if (!pairs.length) return { error: '没有待连目标' }
    const rel = trim(relation)
    const failures = []
    const plan = []
    for (const { sourceId, targetId } of pairs) {
      const src = parseNode(sourceId), tgt = parseNode(targetId)
      if (!src || !tgt) { failures.push('节点已不存在'); continue }
      if (src.family !== 'obj') { failures.push(`「${src.label}」不是对象：连线只能从对象出发`); continue }
      if (tgt.family === 'obj') {
        if (!rel) { failures.push('对象→对象连线需要填写关系名'); continue }
        const dup = graph().some(l => l['@type'] === 'owl:ObjectProperty'
          && fullTypeId(l['rdfs:domain']?.['@id']) === src.domainId && fullTypeId(l['rdfs:range']?.['@id']) === tgt.domainId
          && trim(l['rdfs:label']) === rel)
        if (dup) { failures.push(`「${src.label} → ${rel} → ${tgt.label}」已存在同名链接`); continue }
        plan.push({ kind: '链接', src, tgt, rel })
      } else if (tgt.family === 'sp') {
        const shared = graph().find(x => x['@id'] === tgt.domainId)
        if (!shared) { failures.push(`共享定义「${tgt.label}」已不存在`); continue }
        const existing = localProperties(graph(), src.domainId).find(p => trim(p['mg:sharedProperty']?.['@id']) === tgt.domainId)
        if (existing) { failures.push(`「${src.label}」已引用「${tgt.label}」`); continue }
        plan.push({ kind: '共享引用', src, tgt, shared })
      } else if (tgt.family === 'rule') {
        if (!(s.workflow.businessRules || []).some(r => r.id === tgt.domainId)) { failures.push(`规则「${tgt.label}」已不存在`); continue }
        if (ruleAssociationsOf(s).some(a => fullTypeId(a.objectTypeId) === src.domainId && trim(a.ruleId) === tgt.domainId)) { failures.push(`「${src.label}」已关联规则「${tgt.label}」`); continue }
        plan.push({ kind: '规则关联', src, tgt })
      } else if (tgt.family === 'action') {
        if (!(s.workflow.actions || []).some(a => a.id === tgt.domainId)) { failures.push(`动作「${tgt.label}」已不存在`); continue }
        if (effectiveAssociations(s).some(a => fullTypeId(a.objectTypeId) === src.domainId && trim(a.actionId) === tgt.domainId)) { failures.push(`「${src.label}」已关联动作「${tgt.label}」`); continue }
        plan.push({ kind: '动作关联', src, tgt })
      } else {
        failures.push(`「${tgt.label}」是私有属性：归属于其所属对象，不支持再连线（如需共享请先转为共享属性）`)
      }
    }
    if (failures.length) return { error: failures.join('；'), failures }
    const label0 = plan[0] ? `${nodeNameOf(plan[0].src.id)} → ${plan[0].kind === '链接' ? rel : plan[0].kind} 等 ${plan.length} 项` : '批量连线'
    emitBeforeChange({ actionLabel: `批量连线：${label0}` })
    const newIds = []
    for (const item of plan) {
      if (item.kind === '链接') {
        const id = 'mg:link_' + newHex()
        graph().push({ '@id': id, '@type': 'owl:ObjectProperty', 'rdfs:label': rel, 'rdfs:comment': trim(description),
          'rdfs:domain': { '@id': item.src.domainId }, 'rdfs:range': { '@id': item.tgt.domainId }, 'mg:cardinality': 'many-to-one' })
        newIds.push('lg:link:' + id)
      } else if (item.kind === '共享引用') {
        const id = 'mg:p_' + newHex()
        graph().push({ '@id': id, '@type': 'owl:DatatypeProperty', 'mg:apiName': id.slice(3),
          'rdfs:domain': { '@id': item.src.domainId }, 'mg:sharedProperty': { '@id': item.tgt.domainId } })
        newIds.push('lg:sref:' + id)
      } else if (item.kind === '规则关联') {
        commitRuleAssociations(s, [...ruleAssociationsOf(s), { objectTypeId: item.src.domainId, ruleId: item.tgt.domainId }])
        newIds.push(`lg:ruleassoc:${fullTypeId(item.src.domainId)}|${item.tgt.domainId}`)
      } else {
        commitAssociations(s, [...effectiveAssociations(s), { objectTypeId: item.src.domainId, actionId: item.tgt.domainId }])
        newIds.push(`lg:actionassoc:${fullTypeId(item.src.domainId)}|${item.tgt.domainId}`)
      }
    }
    selfMutating = true
    emitChanged()
    reload(true)
    return { newIds }
  }

  /** 删除边：按边类别执行领域语义（解引用≠删定义；私有归属边=删私有属性）。 */
  function domainDeleteEdge(edgeId) {
    const s = getState()
    const edge = state.draft.edges.find(e => e.id === edgeId)
    if (!edge) return { error: '连线已不存在' }
    if (edge.kind === '对象链接') {
      const link = graph().find(l => l['@type'] === 'owl:ObjectProperty' && l['@id'] === edge.domainId)
      if (!link) return { error: '链接定义已不存在' }
      emitBeforeChange({ actionLabel: `删除链接「${trim(link['rdfs:label']) || edge.domainId}」`, target: { kind: 'link', id: edge.domainId } })
      s.ontology['@graph'] = graph().filter(n => n['@id'] !== edge.domainId)
    } else if (edge.kind === '共享引用') {
      const p = graph().find(x => x['@id'] === edge.domainId)
      if (!p) return { error: '引用属性已不存在' }
      emitBeforeChange({ actionLabel: `移除「${nodeNameOf(edge.source)}」对共享属性「${nodeNameOf(edge.target)}」的引用（属性转为私有保留）` })
      detachShared(p)
    } else if (edge.kind === '私有属性') {
      return domainDeleteNode(edge.target) // 归属边不可单独断开：走删除私有属性确认
    } else if (edge.kind === '规则关联') {
      const [obj, rule] = String(edge.domainId).split('|')
      emitBeforeChange({ actionLabel: `移除对象「${nodeNameOf(edge.source)}」的规则引用「${nodeNameOf(edge.target)}」` })
      commitRuleAssociations(s, ruleAssociationsOf(s).filter(a => !(fullTypeId(a.objectTypeId) === obj && trim(a.ruleId) === rule)))
    } else {
      const [obj, act] = String(edge.domainId).split('|')
      emitBeforeChange({ actionLabel: `移除对象「${nodeNameOf(edge.source)}」的动作关联「${nodeNameOf(edge.target)}」` })
      commitAssociations(s, effectiveAssociations(s).filter(a => !(fullTypeId(a.objectTypeId) === obj && trim(a.actionId) === act)))
    }
    selfMutating = true
    emitChanged()
    reload(true)
    return {}
  }
  function detachShared(p) {
    // 与 propertyModel.detachProperty 同语义：共享引用解除后升级为私有属性，继承显示字段（零丢失）
    const shared = graph().find(n => n['@id'] === p['mg:sharedProperty']?.['@id'])
    if (shared) {
      for (const k of ['mg:valueSuffix', 'mg:decimalPlaces', 'mg:formatting', 'mg:constraint', 'mg:business', 'mg:reviewNote', 'mg:sourceIds']) {
        if (k in shared && !(k in p)) p[k] = JSON.parse(JSON.stringify(shared[k]))
      }
    }
    delete p['mg:sharedProperty']
  }

  /** 连线编辑（仅对象链接可改关系名/描述；引用类边只读）。 */
  function domainSaveEdge(edgeId, { relation, description }) {
    const edge = state.draft.edges.find(e => e.id === edgeId)
    if (!edge) return { error: '连线已不存在' }
    if (edge.kind !== '对象链接') return { error: '引用类连线的关系名由系统定义，不可修改' }
    const link = graph().find(l => l['@type'] === 'owl:ObjectProperty' && l['@id'] === edge.domainId)
    if (!link) return { error: '链接定义已不存在' }
    const r = trim(relation)
    if (!r) return { error: '关系名不能为空' }
    emitBeforeChange({ actionLabel: `修改链接「${trim(link['rdfs:label']) || edge.domainId}」` })
    link['rdfs:label'] = r
    link['rdfs:comment'] = trim(description)
    selfMutating = true
    emitChanged()
    reload(true)
    return {}
  }

  function parseNode(nodeId) {
    const n = state.draft.nodes.find(x => x.id === nodeId)
    if (!n) return null
    const [, family, ...rest] = nodeId.split(':')
    return { id: nodeId, family, domainId: rest.join(':'), label: n.name }
  }

  /**
   * 领域批量删除：先对全部节点做删除校验（任何一项不合法即整批拒绝，不部分写入），
   * 校验通过后一次性应用（节点先于边；边的领域删除各自带具名撤销前钩）。
   */
  function domainDeleteSelection(nodeIds, edgeIds) {
    for (const id of nodeIds) {
      const err = canDeleteNode(id)
      if (err) return { error: err }
    }
    for (const id of nodeIds) {
      const r = domainDeleteNode(id)
      if (r.error) return { error: r.error }
    }
    for (const id of edgeIds) {
      const r = domainDeleteEdge(id)
      if (r.error) return { error: r.error }
    }
    if (nodeIds.length) dropPositions(nodeIds)
    return {}
  }
  /** 删除前置校验（与 domainDeleteNode 的保护规则一致，供批量预检复用）。 */
  function canDeleteNode(nodeId) {
    const s = getState()
    const [, family, ...rest] = nodeId.split(':')
    const domainId = rest.join(':')
    const target = { obj: '对象', sp: '共享属性', pp: '私有属性', rule: '规则', action: '动作' }[family]
    if (!target) return '未知节点类型'
    if (target === '对象') {
      const refs = graphReferences(s, domainId)
      if (refs.length) return `暂不能删除「${nodeNameOf(nodeId)}」：请先处理引用（${refs.join('、')}）。`
      const ruleRefs = ruleAssociationsOf(s).filter(a => fullTypeId(a.objectTypeId) === domainId)
      if (ruleRefs.length) return `暂不能删除「${nodeNameOf(nodeId)}」：此对象仍引用 ${ruleRefs.length} 条业务规则。`
    } else if (target === '共享属性') {
      const refs = referencesOf(graph(), domainId)
      if (refs.length) return `暂不能删除「${nodeNameOf(nodeId)}」：${refs.length} 个对象属性正引用此共享定义。`
    } else if (target === '规则') {
      const refs = ruleAssociationsOf(s).filter(a => trim(a.ruleId) === domainId)
      if (refs.length) return `暂不能删除「${nodeNameOf(nodeId)}」：${refs.length} 个对象仍引用此规则。`
    } else if (target === '动作') {
      const refs = effectiveAssociations(s).filter(a => trim(a.actionId) === domainId)
      if (refs.length) return `暂不能删除「${nodeNameOf(nodeId)}」：${refs.length} 个对象仍关联此动作。`
    }
    return ''
  }

  return {
    state, reload, checkExternal, captureUndo, applyUndo,
    setPrefs, loadPrefs, flushPrefs, persistPrefs: savePrefsDebounced, setPosition, dropPositions, allPositions,
    domainCreateNode, domainSaveNode, domainDeleteNode, domainCreateEdges, domainDeleteEdge, domainSaveEdge, domainDeleteSelection,
    get positions() { return positions },
  }
}

/** 投影签名：外部内容变化检测（draft 结构化序列化 + 名称字段）。 */
export function bridgeSignature(state) {
  const d = projectToDraft(state, {})
  return JSON.stringify([d.name, d.nodes, d.edges])
}
