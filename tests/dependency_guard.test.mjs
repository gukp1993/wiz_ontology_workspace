// 本体建设维护保留项 11～13（20260920）回归：
// 统一依赖判断（dependencyModel）、共享属性高影响保存确认、规则库安全删除、各入口语义一致。
// 运行（仓库根）：node --import ./tests/ts_hooks.mjs tests/dependency_guard.test.mjs
import assert from 'node:assert/strict'
import { pathToFileURL } from 'node:url'
import { createRequire } from 'node:module'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const require = createRequire(new URL('../frontend/package.json', import.meta.url))
const ts = require('typescript')

const root = resolve('.')
const results = []
async function check(name, fn) {
  try { await fn(); results.push({ name, ok: true }); console.log('通过：' + name) }
  catch (e) { results.push({ name, ok: false }); console.log('失败：' + name + ' — ' + e.message) }
}

/** 直接编译 TS 纯模块（无组件依赖），便于断言纯函数语义。 */
async function loadTs(rel) {
  const code = readFileSync(resolve(rel), 'utf8')
  const file = resolve('/tmp', 'dep_' + rel.replace(/[^\w]/g, '_') + '.mjs')
  const out = ts.transpileModule(code, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } })
  const fs = await import('node:fs')
  let text = out.outputText
  // 依赖模块路径改写为同目录已编译副本
  text = text.replace(/from '\.\/(\w+)'/g, (_, name) => `from './dep_src_ontology_${name}_ts.mjs'`)
  // dependencyModel 内部仅依赖 editorModel；businessRuleModel 由 editorModel 之外传入（见下）
  fs.writeFileSync(file, text)
  return await import(pathToFileURL(file).href)
}

// 预编译被依赖模块（editorModel / businessRuleModel）到 /tmp 同名规则
const fsmod = await import('node:fs')
for (const [name, rel] of [['editorModel', 'frontend/src/ontology/editorModel.ts'],
                           ['businessRuleModel', 'frontend/src/ontology/businessRuleModel.ts']]) {
  const src = readFileSync(resolve(rel), 'utf8')
  const out = ts.transpileModule(src, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } })
  fsmod.writeFileSync(resolve('/tmp', `dep_src_ontology_${name}_ts.mjs`), out.outputText
    .replace(/from '\.\/(\w+)'/g, (_, n) => `from './dep_src_ontology_${n}_ts.mjs'`))
}
const dep = await loadTs('frontend/src/ontology/dependencyModel.ts')

// ── 样例草稿：对象 + 共享定义 + 两个引用属性 + 私有属性 + 规则/动作关联 ──
function sampleState() {
  return {
    ontology: { '@graph': [
      { '@id': 'mg:obj1', '@type': 'owl:Class', 'rdfs:label': '储能设备' },
      { '@id': 'mg:obj2', '@type': 'owl:Class', 'rdfs:label': '储能系统' },
      { '@id': 'mg:sp1', '@type': 'mg:SharedProperty', 'rdfs:label': 'soc', 'rdfs:comment': '剩余电量百分比', 'rdfs:range': { '@id': 'xsd:double' } },
      { '@id': 'mg:p1', '@type': 'owl:DatatypeProperty', 'rdfs:label': 'soc', 'rdfs:domain': { '@id': 'mg:obj1' }, 'mg:sharedProperty': { '@id': 'mg:sp1' } },
      { '@id': 'mg:p2', '@type': 'owl:DatatypeProperty', 'rdfs:label': 'soc', 'rdfs:domain': { '@id': 'mg:obj2' }, 'mg:sharedProperty': { '@id': 'mg:sp1' } },
      { '@id': 'mg:priv1', '@type': 'owl:DatatypeProperty', 'rdfs:label': '簇编号', 'rdfs:domain': { '@id': 'mg:obj1' }, 'rdfs:range': { '@id': 'xsd:string' } },
    ] },
    workflow: {
      businessRules: [{ id: 'rule1', name: '储能SOC计算规则', description: 'd', content: 'c', output: 'o' },
                      { id: 'rule2', name: '未引用规则', description: 'd', content: 'c', output: 'o' }],
      businessRuleAssociations: [{ objectTypeId: 'mg:obj1', ruleId: 'rule1' }],
      actions: [{ id: 'act1', name: '停止充放电', definitionVersion: 2 }, { id: 'act2', name: '未关联动作' }],
      actionAssociations: [{ objectTypeId: 'mg:obj2', actionId: 'act1' }],
      functions: [], interfaces: [],
    },
    bindings: {}, metrics: { metrics: [] }, rules: { rules: [] },
  }
}

// ── ① 共享定义删除：有对象属性引用 → 阻断并列出对象名与属性名 ──
await check('① 共享定义有引用 → 阻断并给出名称与原因', async () => {
  const state = sampleState()
  const r = dep.sharedDeleteCheck(state, 'mg:sp1')
  assert.equal(r.blocked, true)
  assert.match(r.message, /储能设备 → soc/)
  assert.match(r.message, /储能系统 → soc/)
  assert.match(r.message, /对象属性引用/)
  assert.equal(r.deps.length, 2)
  assert.equal(r.deps[0].objectId, 'mg:obj1')
})

await check('② 共享定义无引用 → 允许删除（确认后走保存）', async () => {
  const state = sampleState()
  state.ontology['@graph'] = state.ontology['@graph'].filter((n) => n['@id'] !== 'mg:p1' && n['@id'] !== 'mg:p2')
  const r = dep.sharedDeleteCheck(state, 'mg:sp1')
  assert.equal(r.blocked, false)
  assert.equal(r.deps.length, 0)
})

// ── ③ 规则删除：有对象关联阻断；无关联允许 ──
await check('③ 规则删除判定（对象关联阻断 / 无引用允许）', async () => {
  const state = sampleState()
  const blocked = dep.ruleDeleteCheck(state, 'rule1')
  assert.equal(blocked.blocked, true)
  assert.match(blocked.message, /储能设备/)
  assert.match(blocked.message, /对象引用规则/)
  const free = dep.ruleDeleteCheck(state, 'rule2')
  assert.equal(free.blocked, false)
})

// ── ④ 动作删除：对象关联阻断；无关联允许 ──
await check('④ 动作删除判定（对象关联阻断 / 无关联允许）', async () => {
  const state = sampleState()
  const blocked = dep.actionDeleteCheck(state, 'act1')
  assert.equal(blocked.blocked, true)
  assert.match(blocked.message, /储能系统/)
  assert.match(blocked.message, /对象关联动作/)
  assert.equal(dep.actionDeleteCheck(state, 'act2').blocked, false)
})

// ── ⑤ 对象删除：阻断式（属性/链接/规则/动作任一存在都阻断，不级联） ──
await check('⑤ 对象删除阻断：属性/规则/动作依赖均列出', async () => {
  const state = sampleState()
  const r = dep.objectDeleteCheck(state, 'mg:obj1')
  assert.equal(r.blocked, true)
  const reasons = r.deps.map(d => d.reason)
  assert.ok(reasons.includes('对象属性'), '属性依赖')
  assert.ok(reasons.includes('对象引用规则'), '规则依赖')
  assert.match(r.message, /阻断式/)
})

await check('⑥ 空对象可以删除', async () => {
  const state = sampleState()
  state.ontology['@graph'] = state.ontology['@graph'].filter(n => n['@id'] !== 'mg:priv1' && n['@id'] !== 'mg:p1')
  state.workflow.businessRuleAssociations = []
  const r = dep.objectDeleteCheck(state, 'mg:obj1')
  assert.equal(r.blocked, false)
})

// ── ⑦ 共享属性高影响确认：数据类型/业务定义变化 → highImpact + 前后值；仅名称 → 不触发 ──
await check('⑦ 共享修改影响：类型/定义变化为高影响并含前后值与引用对象', async () => {
  const state = sampleState()
  const before = state.ontology['@graph'].find(n => n['@id'] === 'mg:sp1')
  const after = JSON.parse(JSON.stringify(before))
  after['rdfs:range'] = { '@id': 'xsd:string' }
  after['rdfs:comment'] = '剩余电量（文本口径）'
  const info = dep.sharedImpactOf(state, 'mg:sp1', before, after)
  assert.equal(info.highImpact, true)
  const labels = info.fields.map(f => f.label)
  assert.ok(labels.includes('数据类型'))
  assert.ok(labels.includes('业务定义'))
  const typeRow = info.fields.find(f => f.label === '数据类型')
  assert.equal(typeRow.old, 'double')
  assert.equal(typeRow.new, 'string')
  assert.equal(info.usages.length, 2, '引用对象清单')
})

await check('⑧ 仅名称变化 → 非高影响（普通保存）', async () => {
  const state = sampleState()
  const before = state.ontology['@graph'].find(n => n['@id'] === 'mg:sp1')
  const after = JSON.parse(JSON.stringify(before))
  after['rdfs:label'] = 'SOC'
  const info = dep.sharedImpactOf(state, 'mg:sp1', before, after)
  assert.equal(info.highImpact, false)
  assert.equal(info.fields.length, 0)
})

// ── ⑨ 影响指纹：编辑内容或引用集合变化都失效 ──
await check('⑨ 影响指纹随编辑与引用变化（确认失效依据）', async () => {
  const state = sampleState()
  const node = state.ontology['@graph'].find(n => n['@id'] === 'mg:sp1')
  const after = JSON.parse(JSON.stringify(node))
  after['rdfs:comment'] = 'A'
  const fp1 = dep.impactFingerprint(state, 'mg:sp1', after)
  const after2 = { ...after, 'rdfs:comment': 'B' }
  assert.notEqual(dep.impactFingerprint(state, 'mg:sp1', after2), fp1, '内容变化 → 指纹变化')
  const state2 = sampleState()
  state2.ontology['@graph'] = state2.ontology['@graph'].filter(n => n['@id'] !== 'mg:p2')
  assert.notEqual(dep.impactFingerprint(state2, 'mg:sp1', after), fp1, '引用变化 → 指纹变化')
})

// ── ⑩ 契约/接口等外部依赖也算依赖（历史契约引用共享定义 → 阻断） ──
await check('⑩ 契约引用共享定义 → 删除阻断且原因可定位', async () => {
  const state = sampleState()
  state.workflow.functions = [{ id: 'fn1', name: '查询储能soc', outputs: [{ id: 'o', name: 'soc', ref: { kind: 'property', id: 'mg:sp1' } }] }]
  const r = dep.sharedDeleteCheck(state, 'mg:sp1')
  assert.equal(r.blocked, true)
  assert.match(r.message, /查询储能soc/)
})

// ── ⑪ 各入口共用同一判断（源码级：图谱/共享库/规则库/动作库/对象页都引用 dependencyModel） ──
await check('⑪ 所有删除入口复用统一判断（源码引用检查）', async () => {
  const files = ['frontend/src/ontology/SharedLibrary.vue', 'frontend/src/ontology/BusinessRuleLibrary.vue',
                 'frontend/src/ontology/ActionLibrary.vue', 'frontend/src/ontology/ObjectWorkspace.vue',
                 'frontend/src/ontology/legacyGraph/legacyBridge.js']
  for (const f of files) {
    const src = readFileSync(resolve(f), 'utf8')
    assert.match(src, /dependencyModel/, f + ' 未引用统一依赖模块')
  }
  const bridge = readFileSync(resolve('frontend/src/ontology/legacyGraph/legacyBridge.js'), 'utf8')
  assert.match(bridge, /sharedDeleteCheck\(s, domainId\)/, '图谱共享属性删除走统一检查')
  assert.match(bridge, /ruleDeleteCheck\(s, domainId\)/, '图谱规则删除走统一检查')
  assert.match(bridge, /actionDeleteCheck\(s, domainId\)/, '图谱动作删除走统一检查')
  assert.match(bridge, /objectDeleteCheck\(s, domainId\)/, '图谱对象删除走统一检查')
})

// ── ⑫ 规则库有安全删除入口且无强制删除 ──
await check('⑫ 规则库：删除按钮 + 阻断走 ruleDeleteCheck + 无强制删除', async () => {
  const src = readFileSync(resolve('frontend/src/ontology/BusinessRuleLibrary.vue'), 'utf8')
  assert.match(src, /row-link danger" @click="removeRule\(row\.id\)"/, '行内删除按钮')
  assert.match(src, /const check = ruleDeleteCheck\(props\.state, id\)/, '统一判断')
  assert.match(src, /check\.blocked[\s\S]{0,200}dialog\.value = \{ kind: 'owners'/, '阻断后打开引用对象抽屉作为定位')
  const withoutComments = src.replace(/<!--[\s\S]*?-->/g, '').replace(/\/\/[^\n]*/g, '')
  assert.ok(!/强制删除|forceDelete/.test(withoutComments), '无强制删除入口/文案')
})

// ── ⑬ 共享库：不再自动私有化，改为阻断 ──
await check('⑬ 共享库删除：阻断式（不再自动私有化）', async () => {
  const src = readFileSync(resolve('frontend/src/ontology/SharedLibrary.vue'), 'utf8')
  assert.match(src, /const check = sharedDeleteCheck\(props\.state, s\['@id'\]\)/, '统一判断')
  assert.ok(!/for \(const p of usages\) detachProperty/.test(src), '不再批量 detach 私有化')
})

// ── ⑭ 属性保存确认：高影响才弹窗、勾选后确认、取消保留编辑 ──
await check('⑭ PropertyManager：高影响确认流程与失效处理', async () => {
  const src = readFileSync(resolve('frontend/src/ontology/PropertyManager.vue'), 'utf8')
  assert.match(src, /if \(info\?\.highImpact\) \{/, '仅高影响触发')
  assert.match(src, /openImpact\(info, id\)/, '打开确认')
  assert.match(src, /请先勾选「已查看字段变化与受影响对象」/, '必须勾选')
  assert.match(src, /本次确认已失效；请重新核对后再次确认/, '指纹失效提示')
  assert.match(src, /确认并保存/, '确认按钮')
  assert.match(src, /返回编辑/, '取消返回编辑')
  assert.match(src, /act as|doSave\(\)/, '确认后执行保存')
})

// ── ⑮ 属性移除/转私有语义：对象页移除共享引用是「移除引用」；转私有保留对象属性 ID ──
await check('⑮ 移除引用与转为私有语义（源码语义检查）', async () => {
  const ws = readFileSync(resolve('frontend/src/ontology/ObjectWorkspace.vue'), 'utf8')
  assert.match(ws, /propRemoveLabel = \(p: \{ sharedId: string \}\) => p\.sharedId \? '移除引用' : '删除属性'/, '行内文案')
  assert.match(ws, /共享定义与其他对象的引用不受影响/, '移除引用说明')
  const pm = readFileSync(resolve('frontend/src/ontology/PropertyManager.vue'), 'utf8')
  assert.match(pm, /转为私有/, '转私有入口')
  assert.match(pm, /保留本对象属性及生效内容（ID 不变）/, '转私有语义说明')
  // detachProperty 保留节点、只删共享引用键
  const propSrc = readFileSync(resolve('frontend/src/ontology/propertyModel.ts'), 'utf8')
  assert.match(propSrc, /export function detachProperty\(p,graph\)\{const shared=effectiveProperty\(p,graph\);for\(const k of metadataKeys\)if\(k in shared\)p\[k\]=JSON\.parse\(JSON\.stringify\(shared\[k\]\)\);delete p\['mg:sharedProperty'\]\}/,
    'detachProperty 保留对象属性节点与 ID')
})

// ── ⑯ S4（20260920 验收）：外部依赖「去处理」携带 returnTo，处理完能返回原共享定义继续操作 ──
await check('⑯ 共享库 goExternal 携带 returnTo（原共享定义 id + 名称）', async () => {
  const src = readFileSync(resolve('frontend/src/ontology/SharedLibrary.vue'), 'utf8')
  assert.match(src, /const returnTo = s \? \{ view: 'library', focus: \{ definition: s\['@id'\] \}/, 'returnTo 应指向原共享定义')
  assert.match(src, /label: '共享属性「' \+ \(s\['rdfs:label'\]/, 'returnTo 应带业务名称（不是裸 id）')
  assert.match(src, /emit\('navigate', target\.view, returnTo \? \{ \.\.\.target\.focus, returnTo \} : target\.focus\)/, '跳转应带上 returnTo')
  assert.match(src, /usagesId\.value = ''/, '离开前关闭原抽屉（返回时重新打开）')
})

await check('⑰ App 分发 returnTo：navigate 形参、ref、无条件清空、透传五个目标页', async () => {
  const src = readFileSync(resolve('frontend/src/App.vue'), 'utf8')
  assert.match(src, /edit\?: boolean; openUsages\?: boolean; returnTo\?: \{ view: string; focus\?: Record<string, any>; label: string \}/, 'navigate 形参应接受 returnTo')
  assert.match(src, /const definitionReturn = ref<\{ view: string; focus\?: Record<string, any>; label: string \} \| null\>\(null\)/, 'App 持有 returnTo 上下文')
  assert.match(src, /definitionReturn\.value = focus\?\.returnTo \|\| null/, '每次跳转无条件更新（无来源即清空，避免陈旧返回）')
  assert.match(src, /usagesFocusFlag\.value = !!focus\?\.openUsages/, 'openUsages 标记随跳转更新')
  for (const [tag, label] of [['FunctionManager', '契约'], ['DefinitionManager', '接口'], ['BusinessRuleLibrary', '规则'],
                              ['ActionLibrary', '动作'], ['ProjectBinding', '对象映射']]) {
    assert.match(src, new RegExp('<' + tag + '[^>]*:return-to="definitionReturn"'), label + ' 页未接收 returnTo')
  }
  assert.match(src, /<SharedLibrary[^>]*:open-usages="usagesFocusFlag"/, '共享库应接收 openUsages（回到引用位置抽屉）')
})

await check('⑱ 四个目标页接收并渲染返回来源入口（不新建菜单、不新增页面）', async () => {
  const pages = {
    'FunctionManager.vue': /emit\('navigate',\s*props\.returnTo\.view/,
    'BusinessRuleLibrary.vue': /emit\('navigate',\s*props\.returnTo\.view/,
    'ActionLibrary.vue': /emit\('navigate',\s*props\.returnTo\.view/,
  }
  const propDecl = /returnTo\?:\s*\{\s*view:\s*string;\s*focus\?:\s*Record<string,\s*any>;\s*label:\s*string\s*\}/
  for (const [file, emitPattern] of Object.entries(pages)) {
    const src = readFileSync(resolve('frontend/src/ontology/' + file), 'utf8')
    assert.match(src, propDecl, file + ' 应声明可选 returnTo prop')
    assert.match(src, /← 返回\{\{\s*returnTo\.label\s*\}\}/, file + ' 应渲染「← 返回来源定义」入口')
    assert.match(src, emitPattern, file + ' 点击应经既有 navigate 事件返回')
    assert.ok(!/return-menu|new-page|新增菜单/.test(src), file + ' 不得新建菜单/页面')
  }
  const dm = readFileSync(resolve('frontend/src/tools/DefinitionManager.vue'), 'utf8')
  assert.match(dm, /returnTo:Object/, 'DefinitionManager 应声明 returnTo prop')
  assert.match(dm, /emit\('navigate',\s*r\.view/, 'DefinitionManager 应经既有 navigate 事件返回')
  assert.match(dm, /← 返回\{\{returnTo\.label\}\}/, 'DefinitionManager 应渲染返回入口')
  // 返回共享库时带 openUsages：App → SharedLibrary → 引用位置抽屉
  const fn = readFileSync(resolve('frontend/src/ontology/FunctionManager.vue'), 'utf8')
  assert.match(fn, /openUsages:true/, '返回跳转应带 openUsages')
  const shared = readFileSync(resolve('frontend/src/ontology/SharedLibrary.vue'), 'utf8')
  assert.match(shared, /openUsages\?: boolean/, '共享库应声明 openUsages prop')
  assert.match(shared, /else if \(usages\) usagesId\.value = String\(id\)/, 'openUsages + focusId → 打开引用位置抽屉')
  assert.match(shared, /if \(edit\) openEditor\(String\(id\)\)/, 'editFocus 打开编辑表单的行为保留')
})

// ── ⑲ S4：规则库/动作库删除阻断时给出外部依赖「去处理」入口，且不提供强制删除 ──
await check('⑲ 规则库 blocked：引用对象抽屉内列外部依赖并可去处理（带 returnTo 回原规则）', async () => {
  const src = readFileSync(resolve('frontend/src/ontology/BusinessRuleLibrary.vue'), 'utf8')
  assert.match(src, /externalDependencies\(props\.state, ownersRule\.value\.id\)/, '外部依赖取数走统一模块')
  assert.match(src, /function goExternal\(dep: any\)/, '应提供去处理入口')
  assert.match(src, /label: '规则「' \+ \(rule\.name \|\| '未命名规则'\) \+ '」'/, 'returnTo 应回原规则并带名称')
  assert.match(src, /view: 'rules', focus: \{ definition: rule\.id \}/, 'returnTo 应定位原规则定义')
  assert.ok(!/强制删除|forceDelete/.test(src.replace(/<!--[\s\S]*?-->/g, '').replace(/\/\/[^\n]*/g, '')), '不得提供强制删除')
})

await check('⑳ 动作库 blocked：消息区下方给出外部依赖去处理行，且不提供强制删除', async () => {
  const src = readFileSync(resolve('frontend/src/ontology/ActionLibrary.vue'), 'utf8')
  assert.match(src, /externalDependencies\(props\.state, a\.id\)\.filter/, '外部依赖取数走统一模块')
  assert.match(src, /v-if="blockedDeps\.length" class="blocked-deps"/, '阻断提示下应有去处理行')
  assert.match(src, /function goExternal\(dep: any\)/, '应提供去处理入口')
  assert.match(src, /label: '动作「' \+ \(a\.name \|\| '未命名动作'\) \+ '」'/, 'returnTo 应回原动作并带名称')
  assert.match(src, /view: 'actions', focus: \{ definition: a\.id \}/, 'returnTo 应定位原动作定义')
  assert.ok(!/强制删除|forceDelete/.test(src.replace(/<!--[\s\S]*?-->/g, '').replace(/\/\/[^\n]*/g, '')), '不得提供强制删除')
})

// ── ㉑ S4：返回路径必须恢复共享库引用位置抽屉；「去处理/返回」不得夹带删除（不自动替用户再删） ──
await check('㉑ S4 返回路径完整：五页返回带 openUsages、共享库 edit 优先、去处理无删除副作用', async () => {
  // 返回共享库时统一带 openUsages，App 才能用 focusId 恢复「引用位置」抽屉（继续处理剩余依赖）。
  const backFiles = {
    'frontend/src/ontology/FunctionManager.vue': '契约页',
    'frontend/src/ontology/BusinessRuleLibrary.vue': '规则库',
    'frontend/src/ontology/ActionLibrary.vue': '动作库',
    'frontend/src/tools/DefinitionManager.vue': '接口页',
    'frontend/src/project/ProjectBinding.vue': '对象映射页',
  }
  for (const [rel, label] of Object.entries(backFiles)) {
    const src = readFileSync(resolve(rel), 'utf8')
    assert.match(src, /emit\('navigate',[^\n]*openUsages:\s*true/, label + ' 返回跳转应带 openUsages（回到引用位置抽屉）')
  }
  // 共享库保持「edit 优先」：仅显式 openUsages 才开引用位置抽屉，普通进入不受历史 focusId 影响。
  const shared = readFileSync(resolve('frontend/src/ontology/SharedLibrary.vue'), 'utf8')
  assert.match(shared, /if \(edit\) openEditor\(String\(id\)\)\s*\n\s*else if \(usages\) usagesId\.value = String\(id\)/,
    '共享库应保持 edit 优先、仅显式 openUsages 才开引用位置抽屉')
  // 无定位入口的依赖不伪造跳转（按钮禁用，只列名称与原因）。
  assert.match(shared, /:disabled="!externalDependencyTarget\(d\)"/, '无定位入口的依赖应禁用（不伪造跳转）')
  // 「去处理」只做导航：处理依赖后不自动替用户再次删除（handler 内不得出现删除动作）。
  for (const rel of ['frontend/src/ontology/SharedLibrary.vue', 'frontend/src/ontology/BusinessRuleLibrary.vue',
                     'frontend/src/ontology/ActionLibrary.vue']) {
    const body = new RegExp('function goExternal\\(dep: any\\) \\{([\\s\\S]*?)\\n\\}').exec(readFileSync(resolve(rel), 'utf8'))?.[1] || ''
    assert.ok(body, rel + ' 未找到 goExternal 主体')
    assert.ok(!/splice|removeShared|removeRule|remove\(/.test(body), rel + '「去处理」不得携带删除动作')
    assert.match(body, /emit\('navigate'/, rel + '「去处理」应只经既有 navigate 跳转')
  }
})

// ── B1（20260920 W3 最小修复）：applicable_objects 是当前草稿依赖，前端删除判定必须同步 ──
await check('B1 契约 applicable_objects 引用对象 → 对象删除预告阻断（与后端 new_broken_references 同口径）', async () => {
  const state = sampleState()
  // 清掉对象自身的属性/规则依赖，确保阻断只来自契约的 applicable_objects
  state.ontology['@graph'] = state.ontology['@graph'].filter((n) => n['@id'] !== 'mg:p1' && n['@id'] !== 'mg:priv1')
  state.workflow.businessRuleAssociations = []
  state.workflow.functions = [{ id: 'fn1', name: '查询储能soc', guide_version: 3, applicable_objects: ['mg:obj1'], outputs: [] }]
  const check = dep.objectDeleteCheck(state, 'mg:obj1')
  assert.equal(check.blocked, true, '适用对象引用应阻断对象删除')
  assert.ok(check.deps.some(d => d.reason === '适用对象类型' && d.sourceKind === 'contract' && d.sourceId === 'fn1'),
    '依赖条目应含适用对象类型与契约来源（供「去处理」定位）')
  assert.match(check.message, /查询储能soc/)
  // 未引用时不误报；省 mg: 前缀写法同样命中（与后端 field_ref 的 _full 容错一致）
  const bare = sampleState()
  bare.ontology['@graph'] = bare.ontology['@graph'].filter((n) => n['@id'] !== 'mg:p1' && n['@id'] !== 'mg:priv1')
  bare.workflow.businessRuleAssociations = []
  bare.workflow.functions = [{ id: 'fn1', name: '查询储能soc', guide_version: 3, applicable_objects: ['obj1'], outputs: [] }]
  assert.equal(dep.objectDeleteCheck(bare, 'mg:obj1').blocked, true, '省前缀写法同样命中')
  const free = sampleState()
  free.ontology['@graph'] = free.ontology['@graph'].filter((n) => n['@id'] !== 'mg:p1' && n['@id'] !== 'mg:priv1')
  free.workflow.businessRuleAssociations = []
  free.workflow.functions = [{ id: 'fn1', name: '查询储能soc', guide_version: 3, applicable_objects: ['mg:obj2'], outputs: [] }]
  assert.equal(dep.objectDeleteCheck(free, 'mg:obj1').blocked, false, '其他对象的适用应不受影响')
})

const failed = results.filter(r => !r.ok)
console.log(`\n${results.length - failed.length}/${results.length} 项通过`)
if (failed.length) process.exit(1)
