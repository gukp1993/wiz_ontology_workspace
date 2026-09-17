// 动作关联纯辅助回归（20260917 需求）：effective 关联推导/去重、对象与动作双向查询、
// 项目绑定行容错读取、commitAssociations 去重写回、mg: 前缀与 bare 等价。
// 运行：node --import ./tests/ts_hooks.mjs tests/action_model.test.mjs
import assert from 'node:assert/strict'
import { actionsOf, associationsOf, effectiveAssociations, associationsOfObject,
         objectsOfAction, actionBindingsOf, commitAssociations, comboKey } from '../frontend/src/ontology/actionModel.ts'

const state = {
  workflow: {
    actions: [
      { id: 'act-stop', name: '停止充放电', definitionVersion: 2, description: 'd', effect: 'e' },
      { id: 'legacy-move', name: '调整归属', object_type: 'StorageDevice', effect: 'e', criteria: 'c' },
      { id: 'legacy-multi', name: '多对象历史', object_types: ['StorageDevice', 'StorageCluster'], effect: 'e' },
    ],
    actionAssociations: [
      { objectTypeId: 'mg:StorageDevice', actionId: 'act-stop' },
      { objectTypeId: 'mg:StorageCluster', actionId: 'act-stop' },
      { junk: true }, // 非法条目被容错跳过
    ],
  },
}

// 有效关联 = 显式 2 条 + 历史动作推导 3 条，排序稳定、组合去重
const eff = effectiveAssociations(state)
assert.deepEqual(eff.map(r => comboKey(r.objectTypeId, r.actionId)), [
  'StorageDevice::act-stop', 'StorageCluster::act-stop',
  'StorageDevice::legacy-move', 'StorageDevice::legacy-multi', 'StorageCluster::legacy-multi'].sort())
assert.equal(eff.every(r => r.objectTypeId.startsWith('mg:')), true, '推导关联统一为 mg: 前缀')

// bare 与 mg: 前缀等价查询
assert.equal(associationsOfObject(state, 'StorageDevice').length,
             associationsOfObject(state, 'mg:StorageDevice').length)
assert.deepEqual(objectsOfAction(state, 'act-stop').sort(), ['mg:StorageCluster', 'mg:StorageDevice'])
assert.deepEqual(objectsOfAction(state, 'legacy-multi').sort(), ['mg:StorageCluster', 'mg:StorageDevice'])

// 容错读取：缺失/非列表/非法条目
assert.deepEqual(associationsOf({ workflow: {} }), [])
assert.deepEqual(associationsOf({ workflow: { actionAssociations: 'oops' } }), [])
assert.deepEqual(actionsOf({}), [])
assert.deepEqual(actionBindingsOf({ bindings: { actionBindings: [null, 'x', { objectTypeId: 'D', actionId: 'a' }] } }),
                 [{ objectTypeId: 'D', actionId: 'a' }])

// commitAssociations：去重 + 丢弃空行
const target = { workflow: { actions: [], actionAssociations: [] } }
commitAssociations(target, [
  { objectTypeId: 'mg:A', actionId: 'x' },
  { objectTypeId: 'A', actionId: 'x' },      // 与上一条等价 → 去重
  { objectTypeId: '', actionId: 'y' },        // 空 → 丢弃
  { objectTypeId: 'mg:B', actionId: 'z' }])
assert.deepEqual(target.workflow.actionAssociations,
                 [{ objectTypeId: 'mg:A', actionId: 'x' }, { objectTypeId: 'mg:B', actionId: 'z' }])

console.log('通过：关联推导与去重、双向查询、前缀等价、容错读取、写回去重。')
