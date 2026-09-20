// Codex 独立复验（2026-09-20 五项合并验收）· A02 图谱保存反例重演
// 运行（仓库根）：node --import ./tests/ts_hooks.mjs tests/codex_reacceptance_a02_20260920.mjs
// 与实施者测试（legacy_graph_bridge.test.mjs）独立：自己构造数据与断言，
// 验证图谱领域桥的记录级必填/类型口径（A02）与历史字段零丢失。
import assert from 'node:assert/strict'

const storage = new Map()
globalThis.window = { innerWidth: 1440, innerHeight: 900, addEventListener() {}, removeEventListener() {}, location: { hash: '' } }
globalThis.localStorage = { getItem: k => (storage.has(k) ? storage.get(k) : null), setItem: (k, v) => storage.set(k, String(v)), removeItem: k => storage.delete(k) }
globalThis.document = { visibilityState: 'visible', addEventListener() {}, removeEventListener() {}, body: { style: {} }, createElement: () => ({ style: {}, setAttribute() {}, appendChild() {}, content: {} }) }
globalThis.fetch = async () => ({ ok: true, status: 200, json: async () => ({ user: { username: 'codexrv', isAdmin: false, createdAt: '' } }) })
const auth = await import('../frontend/src/app/auth.ts')
await auth.login('codexrv', 'x')

const { createLegacyBridge } = await import('../frontend/src/ontology/legacyGraph/legacyBridge.js')

let failed = 0
const check = (name, fn) => {
  try { fn(); console.log('通过：' + name) }
  catch (e) { failed++; console.log('失败：' + name + ' — ' + e.message) }
}

function makeState() {
  return {
    workspaceId: 'ont-rv',
    ontology: { '@graph': [
      { '@id': 'mg:object_dev', '@type': 'owl:Class', 'rdfs:label': '储能设备' },
    ] },
    workflow: {
      objective: { name: '独立复验本体' },
      businessRules: [{ id: 'rule_a', name: '规则A', description: '有效定义', content: '历史内容',
                        output: '历史输出', codexUnknownExt: { deep: [1, 2] } }],
      businessRuleAssociations: [{ objectTypeId: 'mg:object_dev', ruleId: 'rule_a' }],
      actions: [{ id: 'act_a', name: '动作A', description: '有效定义', effect: '有效效果',
                  definitionVersion: 2, status: 'active' }],
      actionAssociations: [{ objectTypeId: 'mg:object_dev', actionId: 'act_a' }],
    },
  }
}

function harness() {
  const state = makeState()
  const log = []
  const bridge = createLegacyBridge({
    getState: () => state,
    ontologyId: 'ont-rv',
    emitBeforeChange: (p) => log.push(['before', p && p.actionLabel]),
    emitChanged: () => log.push(['changed']),
  })
  return { state, log, bridge }
}
const events = (log, kind) => log.filter(x => x[0] === kind).length

// --- 1. 规则：业务定义清空为纯空白（含换行/制表符）→ 必须失败且原记录不变 ----------------
{
  const { state, log, bridge } = harness()
  const before = structuredClone(state.workflow.businessRules[0])
  const r = bridge.domainSaveNode('lg:rule:rule_a', {
    name: '规则A', data: { name: '规则A', description: ' \n\t ', content: 'c' } })
  check('R1 规则业务定义纯空白 → 返回 error 且含必填文案', () => {
    assert.ok(r && r.error, '应返回错误: ' + JSON.stringify(r))
    assert.ok(/业务定义/.test(String(r.error)), '错误文案应定位业务定义字段: ' + r.error)
    assert.ok(r.fieldErrors && r.fieldErrors.description, '应有字段级错误')
  })
  check('R1 失败后原记录逐字段不变、零 changed/before 事件', () => {
    assert.deepEqual(state.workflow.businessRules[0], before)
    assert.equal(events(log, 'changed'), 0)
    assert.equal(events(log, 'before'), 0)
  })
}

// --- 2. 规则：名称清空同样阻断（不得只靠 UI） ------------------------------------------
{
  const { state, log, bridge } = harness()
  const before = structuredClone(state.workflow.businessRules[0])
  const r = bridge.domainSaveNode('lg:rule:rule_a', {
    name: '   ', data: { name: '   ', description: '有效定义', content: 'c' } })
  check('R2 规则名称纯空白 → error、记录不变、零事件', () => {
    assert.ok(r && r.error, JSON.stringify(r))
    assert.deepEqual(state.workflow.businessRules[0], before)
    assert.equal(events(log, 'changed') + events(log, 'before'), 0)
  })
}

// --- 3. 规则：选填 content 为非文本（数值/对象）→ 受控报错，不被清洗 ---------------------
for (const [label, bad] of [['数值 5', 5], ['对象 {a:1}', { a: 1 }], ['数组 [1]', [1]], ['布尔 true', true]]) {
  const { state, log, bridge } = harness()
  const before = structuredClone(state.workflow.businessRules[0])
  const r = bridge.domainSaveNode('lg:rule:rule_a', {
    name: '规则A', data: { name: '规则A', description: '有效定义', content: bad } })
  check(`R3 规则 content 为${label} → 报「必须是文本」且记录不变`, () => {
    assert.ok(r && r.error, JSON.stringify(r))
    assert.ok(/必须是文本/.test(String(r.error)), '应为文本类型错误: ' + r.error)
    assert.deepEqual(state.workflow.businessRules[0], before)
    assert.equal(events(log, 'changed') + events(log, 'before'), 0)
  })
}

// --- 4. 动作：业务定义空白阻断；effect 非文本阻断；effect 空串合法 ----------------------
{
  const { state, log, bridge } = harness()
  const before = structuredClone(state.workflow.actions[0])
  const r1 = bridge.domainSaveNode('lg:action:act_a', {
    name: '动作A', data: { name: '动作A', description: '  ', effect: '有效效果' } })
  check('R4 动作业务定义空白 → error 且记录不变', () => {
    assert.ok(r1 && r1.error, JSON.stringify(r1))
    assert.deepEqual(state.workflow.actions[0], before)
  })
  const r2 = bridge.domainSaveNode('lg:action:act_a', {
    name: '动作A', data: { name: '动作A', description: 'd', effect: { wrong: 'object' } } })
  check('R4 动作 effect 为对象 → 「必须是文本」且记录不变', () => {
    assert.ok(r2 && r2.error && /必须是文本/.test(String(r2.error)), JSON.stringify(r2))
    assert.deepEqual(state.workflow.actions[0], before)
    assert.equal(events(log, 'changed') + events(log, 'before'), 0)
  })
  const r3 = bridge.domainSaveNode('lg:action:act_a', {
    name: '动作A', data: { name: '动作A', description: '新定义', effect: '' } })
  check('R4 动作 effect 留空合法保存（1 changed + 1 before，键保留为空串）', () => {
    assert.ok(!r3 || !r3.error, JSON.stringify(r3))
    assert.equal(state.workflow.actions[0].description, '新定义')
    assert.equal(state.workflow.actions[0].effect, '')
    assert.equal(events(log, 'changed'), 1)
    assert.equal(events(log, 'before'), 1)
  })
}

// --- 5. 合法保存：历史 output、未知扩展字段、稳定 id 与关联全部保留 ---------------------
{
  const { state, log, bridge } = harness()
  const r = bridge.domainSaveNode('lg:rule:rule_a', {
    name: '规则A改', data: { name: '规则A改', description: '新定义' } })
  check('R5 合法保存（payload 缺 content/output 键）→ 历史值与未知字段零丢失', () => {
    assert.ok(!r || !r.error, JSON.stringify(r))
    const rule = state.workflow.businessRules[0]
    assert.equal(rule.id, 'rule_a', '稳定 id 不变')
    assert.equal(rule.name, '规则A改')
    assert.equal(rule.description, '新定义')
    assert.equal(rule.content, '历史内容', '缺 content 键不得清空历史值')
    assert.equal(rule.output, '历史输出', '历史 output 保留')
    assert.deepEqual(rule.codexUnknownExt, { deep: [1, 2] }, '未知扩展字段保留')
    assert.deepEqual(state.workflow.businessRuleAssociations,
                     [{ objectTypeId: 'mg:object_dev', ruleId: 'rule_a' }], '关联保留')
  })
}

// --- 6. 其他节点类型不受新必填口径误伤（对象节点仅名称必填） ----------------------------
{
  const { state, log, bridge } = harness()
  const r = bridge.domainSaveNode('lg:obj:mg:object_dev', {
    name: '储能设备', data: { displayName: '储能设备', description: '' } })
  check('R6 对象节点空描述仍按原口径保存（不误加必填）', () => {
    assert.ok(!r || !r.error, JSON.stringify(r))
    assert.equal(events(log, 'changed'), 1)
  })
}

console.log(failed ? `\n独立复验A02：存在 ${failed} 项失败` : '\n独立复验A02：全部通过')
process.exit(failed ? 1 : 0)
