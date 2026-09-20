// 业务规则纯辅助回归（20260917 一期；20260920 字段精简后更新）：
// 容错读取、双向引用查询、mg: 前缀归一、组合去重写回、三字段编辑协议 + 历史 output 只读常量。
// 与 tests/test_business_rules.py（后端）成对。
// 运行：node --import ./tests/ts_hooks.mjs tests/business_rule_model.test.mjs
import assert from 'node:assert/strict'
import { RULE_FIELDS, RULE_LEGACY_FIELDS, rulesOf, ruleAssociationsOf, ruleById, rulesOfObject,
         objectsOfRule, commitRuleAssociations, ruleComboKey,
         ruleFieldErrors, ruleRecordValid, ruleDraftOf } from '../frontend/src/ontology/businessRuleModel.ts'

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

// 编辑协议（20260920 精简）：三字段；历史 output 移到只读兼容常量，不在编辑字段内
assert.deepEqual(RULE_FIELDS.map(([k]) => k), ['name', 'description', 'content'], '编辑字段协议为三字段')
assert.deepEqual(RULE_LEGACY_FIELDS.map(([k]) => k), ['output'], '历史保留字段仅 output（只读展示）')
assert.ok(!RULE_FIELDS.some(([k]) => k === 'output'), 'output 不再出现在编辑字段里（新建表单不提供该输入）')

// 历史 output 不在编辑字段内，但仍可原样读取（表单据此做只读历史区，不参与保存）
const legacyView = rulesOf(state).find(r => r.id === 'r1')
assert.equal(legacyView.output, 'o', '历史 output 仍可从记录中读取（只读展示用）')
assert.equal(legacyView.content, 'c', '规则内容仍原样读取')
const formDraft = Object.fromEntries(RULE_FIELDS.map(([k]) => [k, legacyView[k] || '']))
assert.deepEqual(Object.keys(formDraft), ['name', 'description', 'content'], '编辑草稿只承载三字段')
assert.ok(!('output' in formDraft), '编辑草稿不含 output，保存 payload 不会提交该键')

// 容错读取
assert.equal(rulesOf(state).length, 2)
assert.deepEqual(rulesOf({ workflow: {} }), [])
assert.deepEqual(ruleAssociationsOf({ workflow: { businessRuleAssociations: 'oops' } }), [])
assert.equal(rulesOf(state).length, rulesOf(structuredClone(state)).length)

// 只填名称+业务定义的新规则（无 content/output 键）合法读取，不因缺可选键被过滤
const minimal = { workflow: { businessRules: [{ id: 'r3', name: '最小规则', description: '只填必填两项' }] } }
assert.equal(rulesOf(minimal).length, 1, '缺 content/output 的新规则仍是合法记录')
assert.equal(rulesOf(minimal)[0].content, undefined, '不凭空补 content 键')
assert.equal(rulesOf(minimal)[0].output, undefined, '不凭空补 output 键')

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

// ── A01（2026-09-20 验收修复）：记录级文本字段校验（与后端 workflow.py 镜像） ────────────────
// 名称/业务定义必填文本；规则内容选填文本；非文本一律报「必须是文本」，绝不静默转字符串。
assert.deepEqual(ruleFieldErrors({ name: 'n', description: 'd' }), {}, '名称+定义（无 content 键）合法')
assert.deepEqual(ruleFieldErrors({ name: 'n', description: 'd', content: '' }), {}, 'content 空串合法（选填）')
assert.deepEqual(ruleFieldErrors({ name: 'n', description: 'd', content: '   ' }), {}, 'content 纯空白合法（选填）')
assert.deepEqual(ruleFieldErrors({ name: 'n', description: 'd', content: null }), {}, 'content=null 按未填处理')
assert.deepEqual(Object.keys(ruleFieldErrors({ name: '  ', description: '' })).sort(), ['description', 'name'],
                 '空白必填项分别报错')
assert.deepEqual(Object.keys(ruleFieldErrors({ name: null, description: 'd' })), ['name'], 'name=null 报必填')
for (const value of [{ wrong: 'object' }, ['wrong'], 3, true]) {
  const errors = ruleFieldErrors({ name: 'n', description: 'd', content: value })
  assert.deepEqual(Object.keys(errors), ['content'], `content 为 ${JSON.stringify(value)} 时报错`)
  assert.match(errors.content, /必须是文本/, `content 非文本给「必须是文本」文案（${JSON.stringify(value)}）`)
}
assert.match(ruleFieldErrors({ name: { a: 1 }, description: 'd' }).name, /必须是文本/,
             '名称非文本同样受控报错（不 String() 掩盖）')
assert.equal(ruleRecordValid({ name: 'n', description: 'd' }), true, 'ruleRecordValid：最小合法记录')
assert.equal(ruleRecordValid({ name: 'n', description: 'd', content: { bad: 1 } }), false,
             'ruleRecordValid：非法 content 不可保存')
// 非文本旧值进入编辑草稿时保留原值（由校验报错提示用户改写），不被静默清洗
const legacyDraft = ruleDraftOf({ id: 'r1', name: '旧规则', description: 'd', content: { wrong: 'object' }, output: '历史' })
assert.deepEqual(legacyDraft.content, { wrong: 'object' }, '编辑草稿保留非文本旧值（不清洗）')
assert.ok(!('output' in legacyDraft), '编辑草稿不含 output（历史只读）')
assert.deepEqual(Object.keys(ruleFieldErrors(legacyDraft)), ['content'], '保留的非法值在保存校验时被报出')

console.log('通过：容错读取、双向引用、前缀等价、组合去重、三字段协议、历史 output 兼容与 A01 记录级校验。')
