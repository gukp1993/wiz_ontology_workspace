// 动作关联纯辅助回归（20260917 需求；20260920 字段精简后更新）：effective 关联推导/去重、
// 对象与动作双向查询、项目绑定行容错读取、commitAssociations 去重写回、mg: 前缀与 bare 等价；
// 补充：预期效果（effect）选填——空/缺键不改变记录合法性与关联语义。
// 运行：node --import ./tests/ts_hooks.mjs tests/action_model.test.mjs
import assert from 'node:assert/strict'
import { isActionV2, actionsOf, associationsOf, effectiveAssociations, associationsOfObject,
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

// ── 20260920 字段精简：预期效果（effect）选填 ────────────────────────────────────────
// 只填名称/业务定义（effect 缺键或为空）的 v2 动作仍是合法记录，读取与关联推导不依赖 effect。
const minimal = { workflow: {
  actions: [{ id: 'act-min', name: '最小动作', description: '只填名称与业务定义', definitionVersion: 2 },
            { id: 'act-blank', name: '空效果动作', description: 'd', effect: '', definitionVersion: 2 }],
  actionAssociations: [{ objectTypeId: 'mg:StorageDevice', actionId: 'act-min' },
                       { objectTypeId: 'mg:StorageDevice', actionId: 'act-blank' }],
} }
assert.equal(actionsOf(minimal).length, 2, 'effect 缺键/空串的动作仍被读取（不再按非法过滤）')
assert.ok(isActionV2(actionsOf(minimal)[0]) && isActionV2(actionsOf(minimal)[1]), 'v2 判定只看 definitionVersion')
assert.equal(actionsOf(minimal)[0].effect, undefined, '不凭空补 effect 键（缺键保持缺键）')
assert.equal(actionsOf(minimal)[1].effect, '', '空字符串 effect 原样读取（不转为未定义）')
assert.deepEqual(objectsOfAction(minimal, 'act-min'), ['mg:StorageDevice'], '无 effect 的动作关联照常生效')
assert.deepEqual(associationsOfObject(minimal, 'StorageDevice').map(r => r.actionId).sort(),
                 ['act-blank', 'act-min'], '无 effect 的动作关联不丢')
assert.equal(effectiveAssociations(minimal).length, 2, 'effective 关联与 effect 有无无关')

// 三字段更新（名称/业务定义）不改动 effect：表单 payload 只含三字段，命中记录原地合并
const legacyKeep = { workflow: { actions: [{ id: 'act-keep', name: '旧名', description: '旧定义',
                                             effect: '设备退出充放电运行状态', definitionVersion: 2 }] } }
const record = actionsOf(legacyKeep)[0]
Object.assign(record, { name: '新名', description: '新定义' })  // 表单只提交三字段中的两个必填项
assert.equal(record.effect, '设备退出充放电运行状态', '改名/改定义后 effect 原值保留（保存 payload 不含该键时不覆盖）')
assert.deepEqual(Object.keys(record).sort(), ['definitionVersion', 'description', 'effect', 'id', 'name'],
                 '记录键集合不因三字段保存发生变化（不新增/不删除存储键）')

console.log('通过：关联推导与去重、双向查询、前缀等价、容错读取、写回去重、effect 选填。')
