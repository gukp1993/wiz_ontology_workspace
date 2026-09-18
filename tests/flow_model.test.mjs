// 函数编排 flowModel 纯函数回归（交互评审优化）。运行（仓库根）：
//   node --import ./tests/ts_hooks.mjs tests/flow_model.test.mjs
// 覆盖：配置签名排除布局、缺上游闭包（传递）、写操作摘要、测试输入类型校验
//（必填布尔不默认 false、对象/数组 JSON 提示）、范围拓扑排序。
globalThis.window = { addEventListener() {} }
if (!globalThis.crypto) globalThis.crypto = { randomUUID: () => 'u' + Math.random().toString(16).slice(2) }
const mod = await import('../frontend/src/flow/flowModel.ts')
const { configSignature, missingUpstreams, writeCapabilities, validateTestValue, chainOrder } = mod

let failed = 0
function assert(name, cond, detail = '') {
  console.log(`${cond ? '通过' : '失败'}：${name}${cond ? '' : ' — ' + (detail || '断言不成立')}`)
  if (!cond) failed++
}

const baseState = {
  flowId: 'f1', name: '测试编排', description: '',
  inputs: [{ id: 'fin_1', name: 'device_id', type: { type: 'text' } }],
  outputs: [], connections: [],
  nodes: [
    { id: 'nd_cap', kind: 'sql', name: '查询容量', inputs: [], outputs: [{ id: 'o1', name: 'capacity', type: { type: 'number' } }], implementation: { language: 'sql', sql: 'SELECT 1', connectionId: 'c1' } },
    { id: 'nd_calc', kind: 'calc', name: '计算', inputs: [{ id: 'i1', name: 'cap', type: { type: 'number' }, source: { kind: 'node', nodeId: 'nd_cap', outputId: 'o1' } }], outputs: [{ id: 'o2', name: 'soc', type: { type: 'number' } }], implementation: { language: 'calc', mode: 'formula', formulas: { soc: '{cap}' } } },
  ],
  layout: { positions: { nd_cap: { x: 1, y: 2 } }, zoom: 1.3, pan: { x: 9, y: 9 } },
}

// 1) 配置签名：布局无关；配置变化可感知
const sig1 = configSignature(baseState)
const moved = JSON.parse(JSON.stringify(baseState))
moved.layout.positions.nd_cap = { x: 999, y: 999 }
moved.layout.zoom = 0.5
assert('签名不因布局/缩放变化', configSignature(moved) === sig1)
const edited = JSON.parse(JSON.stringify(baseState))
edited.nodes[1].implementation.formulas.soc = '{cap} * 2'
assert('实现内容变化反映在签名', configSignature(edited) !== sig1)

// 2) 缺上游闭包（传递）：只选下游计算节点 → 需补选上游 SQL 节点
assert('缺上游闭包返回上游节点', JSON.stringify(missingUpstreams(baseState, ['nd_calc'])) === JSON.stringify(['nd_cap']))
assert('上游齐全时为空', missingUpstreams(baseState, ['nd_cap', 'nd_calc']).length === 0)

// 3) 写操作摘要：只读范围为空；勾选允许写/非 GET/写命令时逐项列出
assert('只读范围无写摘要', writeCapabilities(baseState, ['nd_cap', 'nd_calc']).length === 0)
const writer = JSON.parse(JSON.stringify(baseState))
writer.nodes[0].execution = { allowWrite: true }
const capWrites = writeCapabilities(writer, ['nd_cap'])
assert('允许写的 SQL 节点进入摘要', capWrites.length === 1 && capWrites[0].includes('查询容量'))
const poster = JSON.parse(JSON.stringify(baseState))
poster.nodes[0].kind = 'http'
poster.nodes[0].implementation = { language: 'http', method: 'POST' }
assert('非 GET HTTP 进入摘要', writeCapabilities(poster, ['nd_cap']).length === 1)

// 4) 测试输入校验：必填布尔不默认 false；数值转换；对象/数组 JSON
assert('布尔未选报错', validateTestValue({ type: 'boolean' }, '').ok === false)
assert('布尔选择解析为真值', validateTestValue({ type: 'boolean' }, 'true').value === true)
assert('空数值允许为 null', validateTestValue({ type: 'number' }, '').ok && validateTestValue({ type: 'number' }, '').value === null)
assert('非数值被拦截', validateTestValue({ type: 'number' }, 'abc').ok === false)
assert('对象 JSON 解析', validateTestValue({ type: 'object' }, '{"a":1}').value.a === 1)
assert('对象非法 JSON 报错', validateTestValue({ type: 'object' }, '{bad}').ok === false)
assert('数组空示例提示', validateTestValue({ type: 'list' }, '').error.includes('[]'))

// 5) 范围拓扑排序：按依赖而非点击顺序
const order = chainOrder(baseState, ['nd_calc', 'nd_cap'])
assert('拓扑排序上游在前', JSON.stringify(order.order) === JSON.stringify(['nd_cap', 'nd_calc']) && !order.missing.length)
const broken = chainOrder(baseState, ['nd_calc'])
assert('断链给缺失提示', broken.order.length === 0 && broken.missing.length === 1)

console.log(failed ? `\n${failed} 项失败` : '\n全部通过')
process.exit(failed ? 1 : 0)
