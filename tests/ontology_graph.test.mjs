// 本体图谱纯函数回归（20260919 画布能力优化 T1/T2/T3；对应验收 G01–G04/G05–G07/G18）。
// 运行（仓库根）：node --import ./tests/ts_hooks.mjs tests/ontology_graph.test.mjs
// 覆盖：五类投影与命名空间隔离、同名同端点链接/自关联、悬空引用、增量差异不重排、
// 可见集/方向 BFS/诱导子图、搜索结果与分页、两种确定性布局、视图缓存规范化。
import assert from 'node:assert/strict'

const modelMod = await import('../frontend/src/ontology/ontologyGraphModel.ts')
const viewMod = await import('../frontend/src/ontology/ontologyGraphView.ts')

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
check('G05 类型筛选只影响可见集，不改变边定义', () => {
  const filters = { kinds: { '规则': false, '动作': false }, relations: {} }
  const vis = viewMod.visibleNodeIds(model, filters)
  assert.ok(![...vis].some(id => id.startsWith('rule:') || id.startsWith('action:')))
  const edges = viewMod.visibleEdgeIds(model, filters, vis)
  assert.ok([...edges].every(id => !id.startsWith('ruleassoc:') && !id.startsWith('actionassoc:')))
  assert.equal(model.edges.length, edges.size + model.edges.filter(e => e.kind === '规则关联' || e.kind === '动作关联').length)
})
check('G06 关系筛选按关系名隐藏边，且可见边两端都可见', () => {
  const filters = { kinds: {}, relations: { '包含': false } }
  const vis = viewMod.visibleNodeIds(model, filters)
  const edges = viewMod.visibleEdgeIds(model, filters, vis)
  for (const id of edges) {
    const e = model.edges.find(x => x.id === id)
    assert.ok(vis.has(e.source) && vis.has(e.target), '可见边两端必须可见')
  }
  assert.ok(![...edges].some(id => id === 'link:mg:l_contains_a' || id === 'link:mg:l_contains_b'), '关系名被取消勾选的边隐藏')
  assert.ok(edges.has('link:mg:l_self'), '其他关系名的边不受影响')
  // 隐藏节点不参与任何可见边：把对象类型关掉后，站点相关边全部不可见
  const noObj = { kinds: { '对象': false }, relations: {} }
  const vis2 = viewMod.visibleNodeIds(model, noObj)
  const edges2 = viewMod.visibleEdgeIds(model, noObj, vis2)
  assert.ok([...edges2].every(id => !id.startsWith('link:') && !id.startsWith('sref:') && !id.startsWith('own:')))
})
check('G07 邻域为止双向 BFS + 诱导子图保留范围内全部边（含平行与自关联）', () => {
  const center = modelMod.objectNodeId('mg:device')
  const nodes = viewMod.neighborhoodNodes(model, center, 1, 'both', allKinds)
  // 设备的 1 跳：站点（两条平行链接都指向它）、私有 SOC、共享引用（共享功率/SOC）、自关联仍是自身、规则、动作
  assert.ok(nodes.has(modelMod.objectNodeId('mg:site')))
  assert.ok(nodes.has(modelMod.sharedNodeId('mg:sp_power')))
  assert.ok(nodes.has(modelMod.ruleNodeId('rule_soc')))
  const edges = viewMod.inducedEdgeIds(model, nodes, allKinds)
  const links = [...edges].filter(id => id.startsWith('link:') || id.startsWith('sref:'))
  assert.ok(links.length >= 5, '范围内应保留全部符合筛选的边，实际 ' + links.length)
  assert.ok(edges.has('link:mg:l_self'), '自关联边在范围内')
  assert.ok(edges.has('sref:mg:p_dev_power') && edges.has('sref:mg:p_dev_soc'))
})
check('G07 方向 BFS：按边上方向取值，流入/流出严格区分', () => {
  const deviceId = modelMod.objectNodeId('mg:device')
  const siteId = modelMod.objectNodeId('mg:site')
  // 链接方向 site → device：站点的流出包含设备，设备的流出不包含站点
  const outSite = viewMod.neighborhoodNodes(model, siteId, 1, 'out', allKinds)
  assert.ok(outSite.has(deviceId), '站点流出到设备')
  const outDevice = viewMod.neighborhoodNodes(model, deviceId, 1, 'out', allKinds)
  assert.ok(!outDevice.has(siteId), '设备没有指向站点的出边')
  assert.ok(outDevice.has(modelMod.ruleNodeId('rule_soc')), '对象 → 规则关联是设备的出边')
  const inDevice = viewMod.neighborhoodNodes(model, deviceId, 1, 'in', allKinds)
  assert.ok(inDevice.has(siteId), '设备的入边来自站点')
  assert.ok(!inDevice.has(modelMod.ruleNodeId('rule_soc')), '规则关联不是设备的入边')
  // 规则的入边来自两个对象
  const inRule = viewMod.neighborhoodNodes(model, modelMod.ruleNodeId('rule_soc'), 1, 'in', allKinds)
  assert.ok(inRule.has(deviceId) && inRule.has(modelMod.objectNodeId('mg:cluster')))
})
check('G07 全部可达（99）在环上收敛（visited 防环）', () => {
  const center = modelMod.objectNodeId('mg:site')
  const nodes = viewMod.neighborhoodNodes(model, center, 99, 'both', allKinds)
  assert.ok(nodes.has(modelMod.objectNodeId('mg:device')) && nodes.has(modelMod.ruleNodeId('rule_soc')))
  assert.ok(nodes.size <= model.nodes.length)
})
check('G06 邻域内筛选隐藏中心 → 中心不在可见集（调用方据此退出邻域）', () => {
  const filters = { kinds: { '对象': false }, relations: {} }
  const center = modelMod.objectNodeId('mg:device')
  const base = viewMod.visibleNodeIds(model, filters)
  assert.ok(!base.has(center))
})

// ── 搜索（G05） ─────────────────────────────────────────────────────────
check('G05 搜索命中名称与别名（忽略大小写），列表标注被筛选隐藏', () => {
  assert.equal(viewMod.searchHitIds(model, 'pcs').has(modelMod.objectNodeId('mg:device')), true, '别名命中')
  const vis = viewMod.visibleNodeIds(model, { kinds: { '对象': false }, relations: {} })
  const list = viewMod.searchList(model, '储能', vis, 20)
  assert.ok(list.total >= 3)
  assert.ok(list.items.some(i => i.hidden), '被筛选隐藏的命中标注 hidden')
  assert.equal(viewMod.searchList(model, '储能', vis, 2).items.length, 2, '分页截断')
  assert.equal(viewMod.searchList(model, '不存在的词', vis, 20).total, 0)
})
check('G05 搜索不改变可见集（仅高亮）', () => {
  const before = viewMod.visibleNodeIds(model, allKinds).size
  viewMod.searchHitIds(model, 'SOC')
  assert.equal(viewMod.visibleNodeIds(model, allKinds).size, before)
})

// ── 布局（G09）：确定性、可见子图、多列、孤立节点 ───────────────────────────
check('G09 按类型排列确定且分组（对象左列、属性中列、规则/动作右列）', () => {
  const ids = viewMod.visibleNodeIds(model, allKinds)
  const a = viewMod.layoutByType(model, ids), b = viewMod.layoutByType(model, ids)
  assert.deepEqual(a, b, '同一输入结果稳定')
  const xs = new Set()
  for (const [id, p] of Object.entries(a)) {
    const node = model.nodes.find(n => n.id === id)
    xs.add(node.kind === '对象' ? 'L' : (node.kind === '规则' || node.kind === '动作') ? 'R' : 'M')
    assert.ok(Number.isFinite(p.x) && Number.isFinite(p.y))
  }
  assert.deepEqual([...xs].sort(), ['L', 'M', 'R'])
})
check('G09 超过 20 个节点分列（子列 x 递增）', () => {
  const many = { nodes: [], edges: [], dangling: [] }
  for (let i = 0; i < 25; i++) many.nodes.push({ id: 'obj:o' + i, domainId: 'o' + i, kind: '对象', name: '对象' + String(i).padStart(2, '0'), aliases: [], detail: [], nav: [] })
  const ids = new Set(many.nodes.map(n => n.id))
  const pos = viewMod.layoutByType(many, ids)
  const xs = new Set(Object.values(pos).map(p => p.x))
  assert.equal(xs.size, 2, '25 个对象应分两列，实际列数 ' + xs.size)
})
check('G09 按关联聚集按连通分量摆放且确定（同分量节点相邻）', () => {
  const ids = viewMod.visibleNodeIds(model, allKinds)
  const a = viewMod.layoutByComponents(model, ids), b = viewMod.layoutByComponents(model, ids)
  assert.deepEqual(a, b)
  // 设备与站点相连：同分量内 x 差应小于跨分量距离
  const d = a[modelMod.objectNodeId('mg:device')], s = a[modelMod.objectNodeId('mg:site')]
  assert.ok(Math.abs(d.x - s.x) <= 300)
})
check('G10 环形布局中心在原点、其余均匀分布且可重复推导', () => {
  const center = modelMod.objectNodeId('mg:device')
  const ids = viewMod.visibleNodeIds(model, allKinds)
  const a = viewMod.layoutCircle(model, ids, center), b = viewMod.layoutCircle(model, ids, center)
  assert.deepEqual(a, b)
  assert.deepEqual(a[center], { x: 0, y: 0 })
  assert.equal(Object.keys(a).length, ids.size)
})
check('G11 空集合布局不抛错且返回空对象', () => {
  const empty = { nodes: [], edges: [], dangling: [] }
  assert.deepEqual(viewMod.layoutByType(empty, new Set()), {})
  assert.deepEqual(viewMod.layoutByComponents(empty, new Set()), {})
})

// ── 视图缓存（G12）：账号/本体隔离由调用方 key 控制；此处验结构、数值与未知 ID 剔除 ──
const ids = new Set(model.nodes.map(n => n.id))
const edgeIds = new Set(model.edges.map(e => e.id))
check('G12 缓存往返：位置/视口/筛选/面板/选择/邻域完整保留', () => {
  const raw = viewMod.serializeView({
    schemaVersion: 1,
    positions: { [modelMod.objectNodeId('mg:device')]: { x: 12, y: -8 } },
    viewport: { zoom: 1.4, pan: { x: 30, y: 40 } },
    filters: { kinds: { '动作': false }, relations: { '对象链接': false } },
    panels: { filters: false, inspector: true, inspectorW: 402 },
    selection: { nodeIds: [modelMod.objectNodeId('mg:device')], edgeId: 'link:mg:l_contains_a' },
    scope: { center: modelMod.objectNodeId('mg:device'), depth: 2, direction: 'out', localLayout: 'circle' },
  })
  const mem = viewMod.parseView(raw, ids, edgeIds)
  assert.ok(mem)
  assert.equal(mem.viewport.zoom, 1.4)
  assert.deepEqual(mem.panels, { filters: false, inspector: true, inspectorW: 402 })
  assert.equal(mem.filters.kinds['动作'], false)
  assert.equal(mem.scope.depth, 2)
  assert.deepEqual(mem.scope, { center: modelMod.objectNodeId('mg:device'), depth: 2, direction: 'out', localLayout: 'circle' })
})
check('G12 损坏缓存/错误版本/未知 ID/非法数值安全降级', () => {
  assert.equal(viewMod.parseView('{ 不是 JSON', ids, edgeIds), null)
  assert.equal(viewMod.parseView(JSON.stringify({ schemaVersion: 99 }), ids, edgeIds), null)
  assert.equal(viewMod.parseView(null, ids, edgeIds), null)
  const mem = viewMod.parseView(JSON.stringify({
    schemaVersion: 1,
    positions: { 'obj:ghost': { x: 1, y: 1 }, [modelMod.objectNodeId('mg:device')]: { x: Infinity, y: 0 }, [modelMod.objectNodeId('mg:site')]: { x: 5, y: 6 } },
    viewport: { zoom: 999, pan: { x: NaN, y: 3 } },
    panels: { inspectorW: 5000 },
    selection: { nodeIds: ['obj:ghost'], edgeId: 'link:ghost' },
    scope: { center: 'obj:ghost' },
  }), ids, edgeIds)
  assert.deepEqual(Object.keys(mem.positions), [modelMod.objectNodeId('mg:site')], '未知 ID 与非有限坐标剔除')
  assert.equal(mem.viewport.zoom, 4, 'zoom 夹紧到上限')
  assert.deepEqual(mem.viewport.pan, { x: 0, y: 0 })
  assert.equal(mem.panels.inspectorW, 480)
  assert.deepEqual(mem.selection, { nodeIds: [], edgeId: '' })
  assert.equal(mem.scope, null)
})

console.log(failed ? `\n${failed} 项失败` : '\n全部通过')
process.exit(failed ? 1 : 0)
