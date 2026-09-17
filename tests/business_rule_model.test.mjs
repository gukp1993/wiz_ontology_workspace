// 业务规则纯辅助回归（20260917 一期）：容错读取、双向引用查询、mg: 前缀归一、
// 组合去重写回、四字段常量。与 tests/test_business_rules.py（后端）成对。
// 运行：node --import ./tests/ts_hooks.mjs tests/business_rule_model.test.mjs
import assert from 'node:assert/strict'
import { RULE_FIELDS, rulesOf, ruleAssociationsOf, ruleById, rulesOfObject,
         objectsOfRule, commitRuleAssociations, ruleComboKey } from '../frontend/src/ontology/businessRuleModel.ts'

const state = {
  workflow: {
    businessRules: [
      { id: 'r1', name: '异常判定', description: 'd', content: 'c', output: 'o' },
      { id: 'r2', name: '收益计算', description: 'd2', content: 'c2', output: 'o2' },
      null, { junk: true }, // 非法条目容错跳过
    ],
    businessRuleAssociations: [
      { objectTypeId: 'mg:StorageDevice', ruleId: 'r1' },
      { objectTypeId: 'mg:StorageCluster', ruleId: 'r1' },
      { objectTypeId: 'mg:StorageDevice', ruleId: 'r2' },
      'junk',
    ],
  },
}

assert.deepEqual(RULE_FIELDS.map(([k]) => k), ['name', 'description', 'content', 'output'], '字段协议固定四项')

// 容错读取
assert.equal(rulesOf(state).length, 2)
assert.deepEqual(rulesOf({ workflow: {} }), [])
assert.deepEqual(ruleAssociationsOf({ workflow: { businessRuleAssociations: 'oops' } }), [])
assert.equal(rulesOf(state).length, rulesOf(structuredClone(state)).length)

// 对象 → 规则（bare 与 mg: 等价）
assert.deepEqual(rulesOfObject(state, 'StorageDevice').map(r => r.ruleId).sort(), ['r1', 'r2'])
assert.deepEqual(rulesOfObject(state, 'mg:StorageDevice'), rulesOfObject(state, 'StorageDevice'))
assert.deepEqual(rulesOfObject(state, 'StorageCluster').map(r => r.ruleId), ['r1'])

// 规则 → 对象（反向引用排序稳定）
assert.deepEqual(objectsOfRule(state, 'r1'), ['mg:StorageCluster', 'mg:StorageDevice'])
assert.deepEqual(objectsOfRule(state, 'r2'), ['mg:StorageDevice'])
assert.deepEqual(objectsOfRule(state, 'ghost'), [])

assert.equal(ruleById(state, 'r2')?.name, '收益计算')
assert.equal(ruleById(state, 'missing'), null)
assert.equal(ruleComboKey('mg:A', 'x'), 'A::x')

// 写回去重 + 丢弃空值
const target = { workflow: { businessRules: [], businessRuleAssociations: [] } }
commitRuleAssociations(target, [
  { objectTypeId: 'mg:A', ruleId: 'x' },
  { objectTypeId: 'A', ruleId: 'x' },       // 等价 → 去重
  { objectTypeId: '', ruleId: 'y' },         // 空 → 丢弃
  { objectTypeId: 'mg:B', ruleId: 'z' }])
assert.deepEqual(target.workflow.businessRuleAssociations,
                 [{ objectTypeId: 'mg:A', ruleId: 'x' }, { objectTypeId: 'mg:B', ruleId: 'z' }])

console.log('通过：容错读取、双向引用、前缀等价、组合去重、字段协议。')
