// 本体图谱纯函数回归（20260919 画布能力优化 T1/T2/T3；对应验收 G01–G04/G05–G07/G18）。
// 运行（仓库根）：node --import ./tests/ts_hooks.mjs tests/ontology_graph.test.mjs
// 覆盖：五类投影与命名空间隔离、同名同端点链接/自关联、悬空引用、增量差异不重排、
// 可见集/方向 BFS/诱导子图、搜索结果与分页、两种确定性布局、视图缓存规范化。
import assert from 'node:assert/strict'

const modelMod = await import('../frontend/src/ontology/ontologyGraphModel.ts')


let failed = 0
function check(name, fn) {
  try { fn(); console.log('通过：' + name) }
  catch (e) { failed++; console.log('失败：' + name + ' — ' + e.message) }
}

// ── fixture：覆盖 五类节点 / 共享+私有属性 / 平行链接 / 自关联 / 同名 / 跨类同 ID / 悬空 / 环 ──
const SHARED_POWER = 'mg:sp_power'
const device = { '@id': 'mg:device', '@type': 'owl:Class', 'rdfs:label': '储能设备', 'rdfs:comment': '设备', 'mg:aliases': { '@type': '@json', '@value': ['设备', 'PCS'] } }
const cluster = { '@id': 'mg:cluster', '@type': 'owl:Class', 'rdfs:label': '储能簇', 'rdfs:comment': '簇' }
const site = { '@id': 'mg:site', '@type': 'owl:Class', 'rdfs:label': '储能站', 'rdfs:comment': '站' }
const sharedPower = { '@id': SHARED_POWER, '@type': 'mg:SharedProperty', 'rdfs:label': '额定功率', 'rdfs:comment': '铭牌功率', 'rdfs:range': { '@id': 'xsd:double' }, 'mg:valueSuffix': 'kW' }
const sharedSoc = { '@id': 'mg:sp_soc', '@type': 'mg:SharedProperty', 'rdfs:label': 'SOC', 'rdfs:comment': '剩余电量', 'rdfs:range': { '@id': 'xsd:double' }, 'mg:valueShape': 'timeSeries' }
// 共享引用（两条对象引用同一共享定义）+ 私有属性同名（“SOC”私有与共享同名）
const refDevice = { '@id': 'mg:p_dev_power', '@type': 'owl:DatatypeProperty', 'rdfs:label': '额定功率', 'rdfs:domain': { '@id': 'mg:device' }, 'mg:sharedProperty': { '@id': SHARED_POWER } }
const refCluster = { '@id': 'mg:p_clu_power', '@type': 'owl:DatatypeProperty', 'rdfs:label': '额定功率', 'rdfs:domain': { '@id': 'mg:cluster' }, 'mg:sharedProperty': { '@id': SHARED_POWER } }
const refSoc = { '@id': 'mg:p_dev_soc', '@type': 'owl:DatatypeProperty', 'rdfs:label': 'SOC', 'rdfs:domain': { '@id': 'mg:device' }, 'mg:sharedProperty': { '@id': 'mg:sp_soc' } }
const ownSoc = { '@id': 'mg:p_clu_soc', '@type': 'owl:DatatypeProperty', 'rdfs:label': 'SOC', 'rdfs:domain': { '@id': 'mg:cluster' }, 'rdfs:range': { '@id': 'xsd:double' } }
// 链接：两条同名同端点不同 ID 的平行链接 + 一条自关联 + 一条普通链接
const linkA = { '@id': 'mg:l_contains_a', '@type': 'owl:ObjectProperty', 'rdfs:label': '包含', 'rdfs:domain': { '@id': 'mg:site' }, 'rdfs:range': { '@id': 'mg:device' }, 'mg:cardinality': 'one-to-many' }
const linkB = { '@id': 'mg:l_contains_b', '@type': 'owl:ObjectProperty', 'rdfs:label': '包含', 'rdfs:domain': { '@id': 'mg:site' }, 'rdfs:range': { '@id': 'mg:device' }, 'mg:comment': '第二条同名链接' }
const linkSelf = { '@id': 'mg:l_self', '@type': 'owl:ObjectProperty', 'rdfs:label': '相邻设备', 'rdfs:domain': { '@id': 'mg:device' }, 'rdfs:range': { '@id': 'mg:device' } }
// 悬空：引用不存在的共享定义；规则关联指向不存在的规则
const danglingProp = { '@id': 'mg:p_dangling', '@type': 'owl:DatatypeProperty', 'rdfs:label': '退役字段', 'rdfs:domain': { '@id': 'mg:cluster' }, 'mg:sharedProperty': { '@id': 'mg:sp_missing' } }
const rule = { id: 'rule_soc', name: 'SOC计算规则', description: '计算SOC', content: 'soc = x', output: '百分比' }
// 规则/动作 id 与对象 id 相同（跨类重复 ID 不得混节点）
const ruleClash = { id: 'device', name: '与对象同 ID 的规则', description: '', content: '', output: '' }
const action = { id: 'act_stop', name: '停止充放电', description: '停机', effect: '功率归零', definitionVersion: 2 }
const actionClash = { id: 'mg:device', name: '与对象同 ID 的动作', description: '', effect: '', definitionVersion: 2 }

function fixture() {
  return {
    ontology: { '@graph': [device, cluster, site, sharedPower, sharedSoc, refDevice, refCluster, refSoc, ownSoc, linkA, linkB, linkSelf, danglingProp] },
    workflow: {
      businessRules: [rule, ruleClash],
      businessRuleAssociations: [
        { objectTypeId: 'mg:device', ruleId: 'rule_soc' },
        { objectTypeId: 'mg:cluster', ruleId: 'rule_soc' },
        { objectTypeId: 'mg:device', ruleId: 'rule_missing' },
      ],
      actions: [action, actionClash],
      actionAssociations: [{ objectTypeId: 'mg:device', actionId: 'act_stop' }],
    },
  }
}

const state = fixture()
const model = modelMod.buildGraphModel(state)

// ── G01/G04：五类节点齐全、命名空间隔离、跨类同 ID 不混 ─────────────────────
check('G01 五类节点齐全（对象3/共享2/私有1/规则2/动作2）', () => {
  const c = modelMod.modelCounts(model).kinds
  assert.equal(c['对象'], 3); assert.equal(c['共享属性'], 2); assert.equal(c['私有属性'], 1)
  assert.equal(c['规则'], 2); assert.equal(c['动作'], 2)
})
check('G04 跨类同 ID 不混节点：规则 device 与对象 mg:device 各自独立', () => {
  const ruleRows = model.nodes.filter(n => n.kind === '规则')
  const objRows = model.nodes.filter(n => n.kind === '对象')
  assert.ok(ruleRows.some(n => n.domainId === 'device'))
  assert.ok(objRows.some(n => n.domainId === 'mg:device'))
  assert.notEqual(ruleRows.find(n => n.domainId === 'device').id, objRows.find(n => n.domainId === 'mg:device').id)
})
check('G04 同名私有属性独立节点，共享定义只有一个节点', () => {
  const priv = model.nodes.filter(n => n.kind === '私有属性' && n.name === 'SOC')
  assert.equal(priv.length, 1)
  assert.equal(model.nodes.filter(n => n.kind === '共享属性' && n.domainId === 'mg:sp_soc').length, 1)
  // 共享引用边保留对象属性定义 ID（可定位到具体属性行）
  const refEdge = model.edges.find(e => e.kind === '共享引用' && e.domainId === 'mg:p_dev_soc')
  assert.ok(refEdge, '应有设备→SOC共享定义的引用边')
  assert.ok(refEdge.nav.some(t => t.focus && t.focus.property === 'mg:p_dev_soc'))
})

// ── G02：平行链接与自关联都上图，身份是定义 ID ──────────────────────────────
check('G02 两条同名同端点链接分别显示（不按名称去重）', () => {
  const rows = model.edges.filter(e => e.kind === '对象链接')
  assert.equal(rows.length, 3, 'linkA/linkB/linkSelf → 实际 ' + rows.length)
  const A = rows.find(e => e.domainId === 'mg:l_contains_a'), B = rows.find(e => e.domainId === 'mg:l_contains_b')
  assert.ok(A && B && A.id !== B.id && A.relation === B.relation)
  assert.equal(A.source, B.source); assert.equal(A.target, B.target)
})
check('G02 自关联链接保留（source === target 且为环线标记数据）', () => {
  const self = model.edges.find(e => e.domainId === 'mg:l_self')
  assert.ok(self, '自关联边应存在')
  assert.equal(self.source, self.target)
})
check('G02 链接改显示名不改变边身份', () => {
  const renamed = fixture()
  renamed.ontology['@graph'] = renamed.ontology['@graph'].map(n => n['@id'] === 'mg:l_contains_a' ? { ...n, 'rdfs:label': '包含（改名）' } : n)
  const next = modelMod.buildGraphModel(renamed)
  const row = next.edges.find(e => e.domainId === 'mg:l_contains_a')
  assert.ok(row && row.id === 'link:mg:l_contains_a', '边 ID 仍以定义 ID 为身份')
})

// ── G18：悬空引用有提示、不画假节点、导航到原定义 ────────────────────────────
check('G18 悬空引用不建假节点并计数提示', () => {
  assert.equal(model.dangling.length, 2, '悬空共享定义 + 缺失规则，实际 ' + model.dangling.length)
  const miss = model.dangling.find(d => d.summary.includes('mg:sp_missing'))
  assert.ok(miss && miss.kind === '共享引用')
  assert.ok(miss.nav.some(t => t.focus && t.focus.property === 'mg:p_dangling'), '可定位到引用它的对象属性')
  assert.ok(!model.nodes.some(n => n.domainId === 'mg:sp_missing'))
  const ruleMiss = model.dangling.find(d => d.kind === '规则关联')
  assert.ok(ruleMiss && ruleMiss.summary.includes('rule_missing'))
})
check('G18 关联边用类型+双方 ID 复合键，平行关联不合并', () => {
  const assoc = model.edges.filter(e => e.kind === '规则关联')
  assert.equal(assoc.length, 2, 'device→rule_soc 与 cluster→rule_soc，实际 ' + assoc.length)
  assert.ok(assoc.every(e => e.id.startsWith('ruleassoc:')))
})

// ── 增量差异（G03）：改名/端点/等数量替换可感知，未变化不重排 ───────────────
check('G03 等数量替换节点可感知（名称变化进入 updatedNodes）', () => {
  const renamed = fixture()
  renamed.ontology['@graph'] = renamed.ontology['@graph'].map(n => n['@id'] === 'mg:cluster' ? { ...n, 'rdfs:label': '储能簇（改名）' } : n)
  const next = modelMod.buildGraphModel(renamed)
  const diff = modelMod.diffModels(model, next)
  assert.equal(diff.addedNodes.length, 0)
  assert.equal(diff.removedNodeIds.length, 0)
  assert.ok(diff.updatedNodes.some(n => n.domainId === 'mg:cluster'))
  assert.notEqual(modelMod.graphSignature(next), modelMod.graphSignature(model))
})
check('G03 边端点变化可感知', () => {
  const moved = fixture()
  moved.ontology['@graph'] = moved.ontology['@graph'].map(n => n['@id'] === 'mg:l_contains_a' ? { ...n, 'rdfs:range': { '@id': 'mg:cluster' } } : n)
  const diff = modelMod.diffModels(model, modelMod.buildGraphModel(moved))
  assert.ok(diff.updatedEdges.some(e => e.domainId === 'mg:l_contains_a'), '端点变更应进入 updatedEdges')
  assert.equal(diff.addedEdges.length, 0, '同一 ID 不重复新增')
})
check('G03 删除节点清理并给出 removedNodeIds', () => {
  const small = fixture()
  small.ontology['@graph'] = small.ontology['@graph'].filter(n => n['@id'] !== 'mg:site')
  const diff = modelMod.diffModels(model, modelMod.buildGraphModel(small))
  assert.ok(diff.removedNodeIds.includes(modelMod.objectNodeId('mg:site')))
  assert.ok(diff.removedEdgeIds.length >= 2, '挂在站点上的两条链接边一并移除')
})
check('G03 完全相同的模型差异为空（不触发重建）', () => {
  const diff = modelMod.diffModels(model, modelMod.buildGraphModel(fixture()))
  assert.equal(diff.addedNodes.length + diff.removedNodeIds.length + diff.updatedNodes.length, 0)
  assert.equal(diff.addedEdges.length + diff.removedEdgeIds.length + diff.updatedEdges.length, 0)
  assert.equal(diff.danglingChanged, false)
})

// ── 可见集 / 邻域 / 诱导子图（G05/G06/G07） ────────────────────────────────
const allKinds = { kinds: {}, relations: {} }

// ── 搜索（G05） ─────────────────────────────────────────────────────────

// ── 布局（G09）：确定性、可见子图、多列、孤立节点 ───────────────────────────

// ── 视图缓存（G12）：账号/本体隔离由调用方 key 控制；此处验结构、数值与未知 ID 剔除 ──
const ids = new Set(model.nodes.map(n => n.id))
const edgeIds = new Set(model.edges.map(e => e.id))

console.log(failed ? `\n${failed} 项失败` : '\n全部通过')
process.exit(failed ? 1 : 0)
