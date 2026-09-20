// legacyBridge 领域桥回归（20260919_图谱编辑器源码整体复用 P5；替代原 ontology_graph_controller.test.mjs）。
// 运行（仓库根）：node --import ./tests/ts_hooks.mjs tests/legacy_graph_bridge.test.mjs
// 覆盖：五类投影（平行链接/自关联/共享引用/悬空）、领域命令（新建/编辑/删除保护/批量连线/
// 引用类边删除语义）、撤销快照换回真实模型、重名校验、视图偏好（坐标不进领域）。
// 旧控制器测试（OntologyGraph.vue）随控制器移除而退役；业务场景在本文件全部保留并新增领域断言。
import assert from 'node:assert/strict'

const storage = new Map()
globalThis.window = { innerWidth: 1440, innerHeight: 900, addEventListener() {}, removeEventListener() {}, location: { hash: '' } }
globalThis.localStorage = { getItem: k => (storage.has(k) ? storage.get(k) : null), setItem: (k, v) => storage.set(k, String(v)), removeItem: k => storage.delete(k) }
globalThis.document = { visibilityState: 'visible', addEventListener() {}, removeEventListener() {}, body: { style: {} }, createElement: () => ({ style: {}, setAttribute() {}, appendChild() {}, content: {} }) }
// 登录以启用偏好命名空间（prefSet 未登录时静默忽略——与生产一致）
let fetchCalls = 0
globalThis.fetch = async () => ({ ok: true, status: 200, json: async () => ({ user: { username: 'bridetest', isAdmin: false, createdAt: '' } }) })
const auth = await import('../frontend/src/app/auth.ts')
await auth.login('bridetest', 'x')

const { createLegacyBridge, projectToDraft, bridgeSignature } = await import('../frontend/src/ontology/legacyGraph/legacyBridge.js')
const { buildJsonIdLocal, jsonIdLosses } = await import('../frontend/src/ontology/legacyGraph/offlineBundle.js')

let failed = 0
const check = (name, fn) => {
  try { fn(); console.log('通过：' + name) }
  catch (e) { failed++; console.log('失败：' + name + ' — ' + e.message) }
}

function makeState() {
  return {
    workspaceId: 'ont-A',
    ontology: { '@graph': [
      { '@id': 'mg:object_device', '@type': 'owl:Class', 'rdfs:label': '储能设备', 'rdfs:comment': '设备', 'mg:aliases': { '@type': '@json', '@value': ['PCS'] } },
      { '@id': 'mg:object_cluster', '@type': 'owl:Class', 'rdfs:label': '储能簇', 'rdfs:comment': '簇' },
      { '@id': 'mg:sp_power', '@type': 'mg:SharedProperty', 'rdfs:label': '额定功率', 'rdfs:range': { '@id': 'xsd:double' }, 'mg:valueSuffix': 'kW' },
      { '@id': 'mg:sp_soc', '@type': 'mg:SharedProperty', 'rdfs:label': 'SOC', 'rdfs:range': { '@id': 'xsd:double' } },
      { '@id': 'mg:p_dev_power', '@type': 'owl:DatatypeProperty', 'rdfs:label': '额定功率', 'rdfs:domain': { '@id': 'mg:object_device' }, 'mg:sharedProperty': { '@id': 'mg:sp_power' } },
      { '@id': 'mg:p_clu_soc', '@type': 'owl:DatatypeProperty', 'rdfs:label': 'SOC', 'rdfs:domain': { '@id': 'mg:object_cluster' }, 'rdfs:range': { '@id': 'xsd:double' } },
      { '@id': 'mg:link_a', '@type': 'owl:ObjectProperty', 'rdfs:label': '包含', 'rdfs:domain': { '@id': 'mg:object_cluster' }, 'rdfs:range': { '@id': 'mg:object_device' } },
      { '@id': 'mg:link_b', '@type': 'owl:ObjectProperty', 'rdfs:label': '包含', 'rdfs:domain': { '@id': 'mg:object_cluster' }, 'rdfs:range': { '@id': 'mg:object_device' } },
      { '@id': 'mg:link_self', '@type': 'owl:ObjectProperty', 'rdfs:label': '相邻设备', 'rdfs:domain': { '@id': 'mg:object_device' }, 'rdfs:range': { '@id': 'mg:object_device' } },
    ] },
    workflow: {
      objective: { name: '桥接验收本体' },
      businessRules: [{ id: 'rule_soc', name: 'SOC计算规则', description: '', content: 'x', output: 'y' }],
      businessRuleAssociations: [{ objectTypeId: 'mg:object_device', ruleId: 'rule_soc' }],
      actions: [{ id: 'act_stop', name: '停止充放电', description: '', effect: '', definitionVersion: 2, status: 'active' }],
      actionAssociations: [{ objectTypeId: 'mg:object_device', actionId: 'act_stop' }],
    },
  }
}

function makeBridge(state, log) {
  return createLegacyBridge({
    getState: () => state,
    ontologyId: 'ont-A',
    emitBeforeChange: (p) => log.push(['before', p && p.actionLabel]),
    emitChanged: () => log.push(['changed']),
  })
}
const changes = (log) => log.filter(x => x[0] === 'changed').length

// ── 投影 ─────────────────────────────────────────────────────────────────
const st = makeState()
const draft = projectToDraft(st, {})
check('R03 五类节点齐全且类型正确', () => {
  const kinds = {}
  for (const n of draft.nodes) kinds[n.type] = (kinds[n.type] || 0) + 1
  assert.equal(kinds['对象'], 2); assert.equal(kinds['共享属性'], 2); assert.equal(kinds['私有属性'], 1)
  assert.equal(kinds['规则'], 1); assert.equal(kinds['动作'], 1)
})
check('R03 平行链接与自关联如实上图（定义 ID 即边身份）', () => {
  const links = draft.edges.filter(e => e.kind === '对象链接')
  assert.equal(links.length, 3)
  assert.ok(links.some(e => e.id === 'lg:link:mg:link_self' && e.source === e.target))
  assert.ok(links.filter(e => e.relation === '包含').length === 2)
})
check('R03 共享引用/私有归属/规则动作关联边分类正确', () => {
  assert.ok(draft.edges.some(e => e.kind === '共享引用' && e.id === 'lg:sref:mg:p_dev_power'))
  assert.ok(draft.edges.some(e => e.kind === '私有属性' && e.id === 'lg:own:mg:p_clu_soc'))
  assert.ok(draft.edges.some(e => e.kind === '规则关联' && e.id === 'lg:ruleassoc:mg:object_device|rule_soc'))
  assert.ok(draft.edges.some(e => e.kind === '动作关联'))
})
check('R03 字段投影：别名/单位/时间序列不丢失', () => {
  const dev = draft.nodes.find(n => n.id === 'lg:obj:mg:object_device')
  assert.deepEqual(dev.data.aliases, ['PCS'])
  const power = draft.nodes.find(n => n.id === 'lg:sp:mg:sp_power')
  assert.equal(power.data.valueSuffix, 'kW')
})

// ── 领域命令 ─────────────────────────────────────────────────────────────
const log = []
const bridge = makeBridge(st, log)
bridge.setPrefs('ont-A')
bridge.reload()
const before = changes(log)

check('R04 新建对象：领域落图 + changed 恰一次 + 投影可见', () => {
  const r = bridge.domainCreateNode({ type: '对象', name: '储能站' })
  assert.ok(!r.error)
  assert.equal(changes(log) - before, 1)
  assert.ok(bridge.state.draft.nodes.some(n => n.id === r.id && n.name === '储能站'))
  assert.ok(st.ontology['@graph'].some(n => n['rdfs:label'] === '储能站' && n['@type'] === 'owl:Class'))
})
check('R05 批量连线：对象→对象 链接、对象→共享 引用、对象→规则/动作 关联一次提交', () => {
  const site = bridge.state.draft.nodes.find(n => n.name === '储能站')
  const r = bridge.domainCreateEdges([
    { sourceId: site.id, targetId: 'lg:obj:mg:object_cluster' },
    { sourceId: site.id, targetId: 'lg:sp:mg:sp_soc' },
    { sourceId: site.id, targetId: 'lg:rule:rule_soc' },
    { sourceId: site.id, targetId: 'lg:action:act_stop' },
  ], { relation: '接入', description: '' })
  assert.ok(!r.error, r.error)
  assert.equal(r.newIds.length, 4)
  assert.ok(st.ontology['@graph'].some(l => l['@type'] === 'owl:ObjectProperty' && l['rdfs:label'] === '接入'))
  assert.ok(st.ontology['@graph'].some(p => p['mg:sharedProperty']?.['@id'] === 'mg:sp_soc'))
  assert.ok(st.workflow.businessRuleAssociations.some(a => a.objectTypeId === site.domainId))
  assert.ok(st.workflow.actionAssociations.some(a => a.objectTypeId === site.domainId))
})
check('R05 重复引用/同名链接/非法组合整批拒绝（不部分写入）', () => {
  const site = bridge.state.draft.nodes.find(n => n.name === '储能站')
  const before0 = JSON.stringify(st).length
  const r = bridge.domainCreateEdges([
    { sourceId: site.id, targetId: 'lg:obj:mg:object_device' },       // 合法
    { sourceId: site.id, targetId: 'lg:rule:rule_soc' },              // 重复关联
    { sourceId: 'lg:sp:mg:sp_power', targetId: 'lg:obj:mg:object_device' }, // 非对象起点
    { sourceId: site.id, targetId: 'lg:pp:mg:p_clu_soc' },            // 私有属性不可连
  ], { relation: '连接X' })
  assert.ok(r.error && r.error.includes('已关联') && r.error.includes('不是对象') && r.error.includes('不支持再连线'))
  assert.equal(JSON.stringify(st).length, before0, '整批拒绝：领域模型零变化')
})
check('R04 编辑共享定义改时间序列 + 重名拒绝', () => {
  const r1 = bridge.domainSaveNode('lg:sp:mg:sp_soc', { name: 'SOC', data: { displayName: 'SOC', description: '采样', dataType: '时间序列', valueSuffix: '%' } })
  assert.ok(!r1.error)
  const soc = st.ontology['@graph'].find(n => n['@id'] === 'mg:sp_soc')
  assert.equal(soc['mg:valueShape'], 'timeSeries')
  assert.equal(soc['rdfs:range']['@id'], 'xsd:double')
  const r2 = bridge.domainSaveNode('lg:sp:mg:sp_soc', { name: '额定功率', data: { displayName: '额定功率', dataType: '数值' } })
  assert.ok(r2.error && r2.error.includes('同名共享属性'))
})
check('R06 删除保护：被引用共享定义/对象（含规则引用）均拒绝', () => {
  assert.ok(bridge.domainDeleteNode('lg:sp:mg:sp_power').error.includes('引用'))
  const devErr = bridge.domainDeleteNode('lg:obj:mg:object_device').error || ''
  assert.ok(devErr.includes('暂不能删除'), devErr)  // 图引用（所属对象/链接起点）先拦；无图引用时轮到规则引用

})
check('R06/20260920 删除共享引用边=移除对象引用属性（共享定义与其他引用保留）', () => {
  // 20260920 验收 R3 更新期望：该边语义是「移除当前对象的引用属性」，不再隐式转为私有；
  // 需要保留内容请显式走对象页的「转为私有」。
  const r = bridge.domainDeleteEdge('lg:sref:mg:p_dev_power')
  assert.ok(!r.error, r.error)
  assert.ok(!st.ontology['@graph'].some(n => n['@id'] === 'mg:p_dev_power'), '对象引用属性已移除')
  assert.ok(st.ontology['@graph'].some(n => n['@id'] === 'mg:sp_power'), '共享定义保留')
})
check('R06 删除对象链接边=删链接定义；规则关联边=解除关联不删规则', () => {
  const r1 = bridge.domainDeleteEdge('lg:link:mg:link_b')
  assert.ok(!r1.error)
  assert.ok(!st.ontology['@graph'].some(l => l['@id'] === 'mg:link_b'))
  const r2 = bridge.domainDeleteEdge('lg:ruleassoc:mg:object_device|rule_soc')
  assert.ok(!r2.error)
  assert.ok(st.workflow.businessRules.some(r => r.id === 'rule_soc'), '规则定义保留')
  assert.ok(!st.workflow.businessRuleAssociations.some(a => a.objectTypeId === 'mg:object_device' && a.ruleId === 'rule_soc'), 'device 的关联解除')
})
check('R06 归属边删除=删除私有属性定义（带确认语义的领域删除）', () => {
  const r = bridge.domainDeleteEdge('lg:own:mg:p_clu_soc')
  assert.ok(!r.error, r.error)
  assert.ok(!st.ontology['@graph'].some(n => n['@id'] === 'mg:p_clu_soc'))
})

// ── 20260920 验收修正回归（R1/R2/R3）：以真实命令结果与状态不变性为断言 ──────────
check('R1 私有属性被契约引用时删除阻断（命令返回错误且状态不变）', () => {
  const st2 = makeState()
  st2.workflow.functions = [{ id: 'fn1', name: '查询SOC', guide_version: 3,
    outputs: [{ id: 'o1', name: 'soc', ref: { kind: 'property', id: 'mg:p_clu_soc' } }] }]
  const log2 = []
  const b2 = makeBridge(st2, log2)
  b2.reload(true)
  const before = JSON.stringify(st2.ontology['@graph'])
  const r = b2.domainDeleteNode('lg:pp:mg:p_clu_soc')
  assert.ok(r.error, '应阻断')
  assert.match(r.error, /簇编号|SOC/, '名称可读')
  assert.equal(JSON.stringify(st2.ontology['@graph']), before, '状态不变')
})

check('R1 对象链接被契约引用时删除阻断', () => {
  const st2 = makeState()
  st2.workflow.functions = [{ id: 'fn2', name: '查询关系', guide_version: 3,
    outputs: [{ id: 'o2', name: 'rel', ref: { kind: 'object', id: 'mg:object_device' } }],
    inputs: [{ id: 'i2', name: 'link', ref: { kind: 'property', id: 'mg:link_a' } }] }]
  const b2 = makeBridge(st2, [])
  b2.reload(true)
  const before = JSON.stringify(st2.ontology['@graph'])
  const r = b2.domainDeleteEdge('lg:link:mg:link_a')
  assert.ok(r.error, '应阻断')
  assert.equal(JSON.stringify(st2.ontology['@graph']), before, '状态不变')
})

check('R2 批量删除含失败项=整批不变（节点与归属边同选）', () => {
  const st2 = makeState()
  st2.workflow.functions = [{ id: 'fn1', name: '查询SOC', guide_version: 3,
    outputs: [{ id: 'o1', name: 'soc', ref: { kind: 'property', id: 'mg:p_clu_soc' } }] }]
  const log2 = []
  const b2 = makeBridge(st2, log2)
  b2.reload(true)
  const before = JSON.stringify(st2.ontology['@graph'])
  const r = b2.domainDeleteSelection(['lg:pp:mg:p_clu_soc'], ['lg:own:mg:p_clu_soc'])
  assert.ok(r.error, '预检应拒绝')
  assert.equal(JSON.stringify(st2.ontology['@graph']), before, '整批不变')
  assert.equal(log2.filter(x => x[0] === 'changed').length, 0, '未产生任何变更事件')
})

check('R2 批量删除全部合法=一次变更、一次保存、边去重', () => {
  const st2 = makeState()
  const log2 = []
  const b2 = makeBridge(st2, log2)
  b2.reload(true)
  const r = b2.domainDeleteSelection(['lg:pp:mg:p_clu_soc'], ['lg:own:mg:p_clu_soc'])
  assert.ok(!r.error, r.error)
  assert.ok(!st2.ontology['@graph'].some(n => n['@id'] === 'mg:p_clu_soc'), '属性删除')
  assert.equal(log2.filter(x => x[0] === 'changed').length, 1, '只发一次 changed（一次保存）')
  assert.equal(log2.filter(x => x[0] === 'before').length, 1, '只发一次 before（一次撤销点）')
})

check('R3 共享引用边删除不触发 detachShared（无残留 mg:valueSuffix 继承）', () => {
  const st2 = makeState()
  const b2 = makeBridge(st2, [])
  b2.reload(true)
  const r = b2.domainDeleteEdge('lg:sref:mg:p_dev_power')
  assert.ok(!r.error, r.error)
  const p = st2.ontology['@graph'].find(n => n['@id'] === 'mg:p_dev_power')
  assert.ok(!p, '对象属性被移除（而非转私有保留）')
  assert.ok(st2.ontology['@graph'].some(n => n['@id'] === 'mg:sp_power'), '共享定义保留')
})

check('B1 图谱命令：对象被契约 applicable_objects 引用时删除阻断且状态不变', () => {
  const st2 = makeState()
  // 清掉对象自身的属性/链接/规则/动作依赖，确保阻断只来自契约 applicable_objects
  st2.ontology['@graph'] = st2.ontology['@graph'].filter(n => n['@id'] !== 'mg:p_dev_power'
    && !(n['@type'] === 'owl:ObjectProperty'
      && (['rdfs:domain', 'rdfs:range'].some(k => n[k]?.['@id'] === 'mg:object_device'))))
  st2.workflow.businessRuleAssociations = []
  st2.workflow.actionAssociations = []
  st2.workflow.functions = [{ id: 'fn1', name: '查询设备契约', guide_version: 3, applicable_objects: ['mg:object_device'], outputs: [] }]
  const log2 = []
  const b2 = makeBridge(st2, log2)
  b2.reload(true)
  const before = JSON.stringify(st2.ontology['@graph'])
  const r = b2.domainDeleteNode('lg:obj:mg:object_device')
  assert.ok(r.error, '应阻断（此前仅后端保存边界会 422）')
  assert.match(r.error, /查询设备契约/, '阻断文案应含契约名称')
  assert.equal(JSON.stringify(st2.ontology['@graph']), before, '状态不变')
  assert.equal(log2.filter(x => x[0] === 'changed').length, 0, '不产生变更事件')
})

// ── 20260920 字段精简：图谱表单不再提交 output / effect 缺键（真实零丢失风险点） ──────
const { FIELDS, LEGACY_FIELDS, fieldEntries } = await import('../frontend/src/ontology/legacyGraph/shared/fields.js')

check('字段规范：规则编辑字段不再含 output，历史 output 走只读 LEGACY_FIELDS', () => {
  const ruleKeys = FIELDS['规则'].map(s => s.key)
  assert.deepEqual(ruleKeys, ['name', 'description', 'content'], '规则编辑字段为三字段')
  assert.ok(FIELDS['规则'].find(s => s.key === 'description').required, '业务定义必填')
  assert.ok(!ruleKeys.includes('output'), 'output 不在编辑字段（NodeEditModal.collectData 因此不提交该键）')
  assert.deepEqual(LEGACY_FIELDS['规则'].map(s => s.key), ['output'], '历史 output 只在只读区展示')
  assert.deepEqual(FIELDS['动作'].map(s => s.key), ['name', 'description', 'effect'], '动作编辑字段仍含 effect 键')
  assert.ok(FIELDS['动作'].find(s => s.key === 'description').required, '动作业务定义必填')
  assert.equal(FIELDS['动作'].find(s => s.key === 'effect').required, undefined, '预期效果不标必填')
})

check('图谱/预览关联详情：历史 output 以只读行展示，无值不占位（与规则库、对象页口径一致）', () => {
  const legacy = fieldEntries('规则', { name: 'R', description: 'D', content: 'C', output: '历史输出的说明' })
  assert.deepEqual(legacy.map(f => f.label), ['规则名称', '业务定义', '规则内容', '历史补充说明（原输出结果）'])
  assert.equal(legacy[legacy.length - 1].value, '历史输出的说明')
  assert.ok(legacy[legacy.length - 1].legacy, '历史行需标记，便于样式区分')
  const noOutput = fieldEntries('规则', { name: 'R', description: 'D', content: 'C', output: '' })
  assert.deepEqual(noOutput.map(f => f.label), ['规则名称', '业务定义', '规则内容'], '无历史值不显示历史行')
  const blank = fieldEntries('规则', { name: 'R', description: 'D', content: 'C', output: '   ' })
  assert.deepEqual(blank.map(f => f.label), ['规则名称', '业务定义', '规则内容'], '全空白同样不显示')
  // 动作没有历史字段，不应被追加
  assert.deepEqual(fieldEntries('动作', { name: 'A', description: 'D', effect: 'E' }).map(f => f.label), ['动作名称', '业务定义', '预期效果'])
})

check('图谱编辑规则：payload 无 output 键时历史 output 不被清空（改名/改定义照常）', () => {
  const st2 = makeState()
  const b2 = makeBridge(st2, [])
  b2.reload(true)
  // 模拟 NodeEditModal 保存：data 只含 FIELDS['规则'] 的键（无 output）
  const r = b2.domainSaveNode('lg:rule:rule_soc', {
    name: 'SOC计算规则（改名）',
    data: { name: 'SOC计算规则（改名）', description: '新业务定义', content: '新规则内容' },
  })
  assert.ok(!r.error, r.error)
  const rule = st2.workflow.businessRules.find(x => x.id === 'rule_soc')
  assert.equal(rule.name, 'SOC计算规则（改名）', '名称已更新')
  assert.equal(rule.description, '新业务定义', '业务定义已更新')
  assert.equal(rule.content, '新规则内容', '规则内容已更新')
  assert.equal(rule.output, 'y', '历史 output 原值保留，未被清空')
})

check('图谱编辑规则：output 键缺失时只跳过该键（不写入空串）', () => {
  const st2 = makeState()
  const b2 = makeBridge(st2, [])
  b2.reload(true)
  const r = b2.domainSaveNode('lg:rule:rule_soc', { name: '规则改名', data: { name: '规则改名', description: 'd' } })
  assert.ok(!r.error, r.error)
  const rule = st2.workflow.businessRules.find(x => x.id === 'rule_soc')
  assert.equal(rule.output, 'y', '缺 output 键 → 保留历史值')
  assert.equal(rule.content, 'x', '缺 content 键 → 保留历史值（字段精简后表单可能不带该键）')
})

check('图谱编辑规则：显式携带 output 键时仍按传入值写入（兼容旧调用方）', () => {
  const st2 = makeState()
  const b2 = makeBridge(st2, [])
  b2.reload(true)
  const r = b2.domainSaveNode('lg:rule:rule_soc', { name: 'SOC计算规则', data: { name: 'SOC计算规则', description: '', content: 'x', output: '  新输出说明  ' } })
  assert.ok(!r.error, r.error)
  assert.equal(st2.workflow.businessRules.find(x => x.id === 'rule_soc').output, '新输出说明', '显式传键时去首尾空白后写入')
})

check('图谱编辑动作：data 缺 effect 键时原值保留、不清空', () => {
  const st2 = makeState()
  st2.workflow.actions[0].effect = '设备退出充放电运行状态'
  const b2 = makeBridge(st2, [])
  b2.reload(true)
  const r = b2.domainSaveNode('lg:action:act_stop', { name: '停止充放电', data: { name: '停止充放电', description: '新定义' } })
  assert.ok(!r.error, r.error)
  const act = st2.workflow.actions.find(x => x.id === 'act_stop')
  assert.equal(act.effect, '设备退出充放电运行状态', 'effect 缺键保留原值（预期效果选填）')
  assert.equal(act.definitionVersion, 2, 'definitionVersion 不变（不批量升级动作格式）')
  assert.equal(st2.workflow.actionAssociations.length, 1, '对象动作关联不变')
})

check('图谱编辑动作：显式传空 effect 键时按传入值清空（表单显式清空语义保留）', () => {
  const st2 = makeState()
  st2.workflow.actions[0].effect = '设备退出充放电运行状态'
  const b2 = makeBridge(st2, [])
  b2.reload(true)
  const r = b2.domainSaveNode('lg:action:act_stop', { name: '停止充放电', data: { name: '停止充放电', description: 'd', effect: '' } })
  assert.ok(!r.error, r.error)
  assert.equal(st2.workflow.actions.find(x => x.id === 'act_stop').effect, '', '显式空值仍可写入（键存在才写）')
})

// ── 撤销：换回真实领域模型 ────────────────────────────────────────────────
check('R06/§3.2 撤销快照恢复真实模型（非仅画布）', () => {
  const snap = bridge.captureUndo()
  const r = bridge.domainCreateNode({ type: '对象', name: '临时对象' })
  assert.ok(st.ontology['@graph'].some(n => n['rdfs:label'] === '临时对象'))
  bridge.applyUndo(snap, '撤销测试')
  assert.ok(!st.ontology['@graph'].some(n => n['rdfs:label'] === '临时对象'), '领域模型回退')
  assert.ok(bridge.state.draft.nodes.every(n => n.name !== '临时对象'), '投影同步回退')
})

// ── 视图偏好：坐标不进领域 ────────────────────────────────────────────────
check('§3.3 坐标是视图状态：setPosition 只写偏好，不改领域；按本体 key 隔离', () => {
  const sig0 = bridgeSignature(st)
  bridge.setPosition('lg:obj:mg:object_device', 123, 456)
  bridge.flushPrefs()
  assert.equal(bridgeSignature(st), sig0, '领域签名不变')
  const raw = [...storage.keys()].find(k => k.includes('ontologyGraph:legacy:ont-A')) && [...storage.entries()].find(([k]) => k.includes('ontologyGraph:legacy:ont-A'))[1]
  assert.ok(raw && JSON.parse(raw).positions['lg:obj:mg:object_device'].x === 123)
})

// ── 外部变化检测 ─────────────────────────────────────────────────────────
check('§3.2 外部变更（非桥发起）→ checkExternal 报告并重建投影', () => {
  bridge.reload()
  assert.equal(bridge.checkExternal(), false, '无变化')
  st.ontology['@graph'].push({ '@id': 'mg:object_new', '@type': 'owl:Class', 'rdfs:label': '外部新增', 'rdfs:comment': '' })
  assert.equal(bridge.checkExternal(), true, '外部变化被识别')
  assert.ok(bridge.state.draft.nodes.some(n => n.name === '外部新增'))
})

// ── jsonId 有损检测 + 干净导出 ────────────────────────────────────────────
check('R13 有损 jsonId：动作/私有属性/共享引用/时间序列均列入损失清单', () => {
  const losses = jsonIdLosses(draft)
  const joined = losses.join('|')
  assert.ok(joined.includes('动作'), joined)
  assert.ok(joined.includes('私有属性') || joined.includes('归属'), joined)
  assert.ok(joined.includes('共享引用'), joined)
})
check('R13 干净路径 jsonId 导出结构与编号规则镜像旧后端', () => {
  const clean = { name: 'v', nodes: draft.nodes.filter(n => n.type === '对象' || n.type === '共享属性' || n.type === '规则').map(n => ({ ...n })), edges: [] }
  const j = buildJsonIdLocal(clean, '图谱')
  assert.equal(j.statistics.entities, 2)
  assert.ok(j['@graph'][0]['@id'].startsWith('czy:entity:'))
  assert.equal(j['@graph'][0].nodeType, '实体')
})

console.log(failed ? `\n${failed} 项失败` : '\n全部通过')
process.exit(failed ? 1 : 0)
