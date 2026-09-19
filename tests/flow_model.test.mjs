// 函数编排 flowModel 纯函数回归（交互评审优化 + 20260919 隔离片段测试镜像）。运行（仓库根）：
//   node --import ./tests/ts_hooks.mjs tests/flow_model.test.mjs
// 覆盖：配置签名排除布局、缺上游闭包（传递）、写操作摘要、测试输入类型校验
//（必填布尔不默认 false、对象/数组 JSON 提示）、范围拓扑排序；
// testScopePlan（拓扑序/环/连通/外部输入分类/入口需求）、dependencyPaths（多路径不猜测）、
// testInputSignature（输入/范围变化可感知）。服务端 workbench/flow_test_plan.py 为权威。
globalThis.window = { addEventListener() {} }
if (!globalThis.crypto) globalThis.crypto = { randomUUID: () => 'u' + Math.random().toString(16).slice(2) }
const mod = await import('../frontend/src/flow/flowModel.ts')
const { configSignature, missingUpstreams, writeCapabilities, validateTestValue, chainOrder, testScopePlan, dependencyPaths, testInputSignature, nameOfFlowNode, describeOutput } = mod

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

// 6) 隔离片段测试镜像（20260919）：与 workbench/flow_test_plan.py 语义一致
const chain = {
  flowId: 'f9', name: '公式链', description: '',
  inputs: [{ id: 'fin_x', name: 'x', type: { type: 'number' } }],
  outputs: [], connections: [],
  nodes: ['A', 'B', 'C', 'D', 'E'].map((id, i) => ({
    id, kind: 'calc', name: '节点' + id,
    inputs: [{ id: 'in_x', name: 'x', type: { type: 'number' },
               source: i === 0 ? { kind: 'flowInput', inputId: 'fin_x' }
                               : { kind: 'node', nodeId: ['A', 'B', 'C', 'D', 'E'][i - 1], outputId: 'out_v' } }],
    outputs: [{ id: 'out_v', name: 'v', type: { type: 'number' } }],
    implementation: { mode: 'formula', formulas: { v: '{x}' } },
  })),
}
const plan = testScopePlan(chain, ['B', 'C', 'D'])
assert('镜像：B–D 拓扑序', JSON.stringify(plan.order) === JSON.stringify(['B', 'C', 'D']))
assert('镜像：A/E 为范围外', JSON.stringify(plan.excluded.map(x => x.id)) === JSON.stringify(['A', 'E']))
assert('镜像：仅 B.x 为必填外部输入', plan.externalInputs.length === 1 && plan.externalInputs[0].nodeId === 'B' && plan.externalInputs[0].required === true)
assert('镜像：B–D 无入口需求（A 在范围外）', plan.entryNeeds.length === 0)
assert('镜像：无错误', plan.error === '')
const planAll = testScopePlan(chain, ['A', 'B', 'C', 'D', 'E'])
assert('镜像：全范围入口需求识别', planAll.entryNeeds.length === 1 && planAll.entryNeeds[0].inputId === 'fin_x')
assert('镜像：全范围无外部输入', planAll.externalInputs.length === 0)
assert('镜像：集合不连通报错', testScopePlan(chain, ['B', 'D']).error.includes('不连通'))
assert('镜像：环检测', testScopePlan({ ...chain, nodes: chain.nodes.map(n => ({ ...n, source: undefined, inputs: [{ id: 'in_x', name: 'x', type: { type: 'number' }, source: { kind: 'node', nodeId: n.id === 'A' ? 'B' : 'A', outputId: 'out_v' } }] })) }, ['A', 'B']).error.includes('循环依赖'))
assert('镜像：不存在节点报错', testScopePlan(chain, ['Z']).error.includes('不存在'))
// 侧路范围外输入按目标节点+输入列出
const sideState = JSON.parse(JSON.stringify(chain))
sideState.nodes[3].inputs.push({ id: 'in_y', name: 'y', type: { type: 'text' }, source: { kind: 'node', nodeId: 'X', outputId: 'out_o' } })
const planSide = testScopePlan(sideState, ['B', 'C', 'D'])
assert('镜像：侧路外部输入分别列出', planSide.externalInputs.length === 2 && planSide.externalInputs.some(x => x.nodeId === 'D' && x.inputId === 'in_y'))
// dependencyPaths：唯一路径 / 多路径不猜测 / 无路径
assert('路径：线性链唯一', JSON.stringify(dependencyPaths(chain, 'B', 'D')) === JSON.stringify([['B', 'C', 'D']]))
assert('路径：单节点等价', JSON.stringify(dependencyPaths(chain, 'C', 'C')) === JSON.stringify([['C']]))
const branch = JSON.parse(JSON.stringify(chain))
branch.nodes.push({ id: 'B2', kind: 'calc', name: '节点B2', inputs: [{ id: 'in_x', name: 'x', type: { type: 'number' }, source: { kind: 'node', nodeId: 'A', outputId: 'out_v' } }], outputs: [{ id: 'out_v', name: 'v', type: { type: 'number' } }], implementation: { mode: 'formula', formulas: { v: '{x}' } } })
branch.nodes[3].inputs.push({ id: 'in_x2', name: 'x2', type: { type: 'number' }, source: { kind: 'node', nodeId: 'B2', outputId: 'out_v' } })
const multi = dependencyPaths(branch, 'A', 'D')
assert('路径：多条路径全部返回（由调用方要求显式选集，不猜测）', multi.length === 2)
assert('路径：无路径为空', dependencyPaths(chain, 'E', 'B').length === 0)
// testInputSignature：输入/范围变化可感知，结果新鲜度判据之一
const sigA = testInputSignature({ kind: 'segment', targets: ['B', 'C', 'D'] }, { o: { k: '10' }, e: {} }, {})
const sigB = testInputSignature({ kind: 'segment', targets: ['B', 'C', 'D'] }, { o: { k: '11' }, e: {} }, {})
const sigC = testInputSignature({ kind: 'single', targets: ['B'] }, { o: { k: '10' }, e: {} }, {})
assert('输入签名：值变化可感知', sigA !== sigB)
assert('输入签名：范围变化可感知', sigA !== sigC)
assert('nameOfFlowNode：显示名回退', nameOfFlowNode(chain, 'B') === '节点B' && nameOfFlowNode(chain, 'zz') === 'zz')


// 6) 结果分类渲染模型（R05）：不空白、空列表明确、混合列表不强凑表格
const view = v => describeOutput(v).kind
assert('数值/文本/布尔/null 为标量', view(0) === 'scalar' && view(false) === 'scalar' && view('') === 'scalar' && view(null) === 'scalar')
assert('对象为 JSON 视图', view({ a: 1 }) === 'json')
assert('对象列表为表格视图', view([{ a: 1 }, { a: 2, b: 3 }]) === 'object-table')
const objTable = describeOutput([{ a: 1 }, { a: 2, b: 3 }])
assert('对象列表列来自实际字段（一次扫描）', objTable.kind === 'object-table' && JSON.stringify(objTable.keys) === JSON.stringify(['a', 'b']))
const scalarTable = describeOutput([1, 2, 3])
assert('数值列表为序号表视图', scalarTable.kind === 'index-table' && scalarTable.rows.length === 3)
assert('混合列表为 JSON 视图', view([1, { a: 1 }]) === 'json')
assert('嵌套列表为 JSON 视图', view([[1], [2]]) === 'json')
const empty = describeOutput([])
assert('空列表有明确视图', empty.kind === 'empty')
assert('101+ 列表预览截断到 100', describeOutput(Array.from({ length: 150 }, (_, i) => i)).truncated === true)

// 7) testScopePlan 连通选项（R02b）：整条/单节点允许并列分支，片段保持连通校验
const paraState = {
  flowId: 'f', name: 'n', description: '', inputs: [], outputs: [], connections: [],
  nodes: [
    { id: 'nd_a', kind: 'calc', name: 'A', inputs: [], outputs: [{ id: 'oa', name: 'a', type: { type: 'number' } }], implementation: {} },
    { id: 'nd_b', kind: 'calc', name: 'B', inputs: [], outputs: [{ id: 'ob', name: 'b', type: { type: 'number' } }], implementation: {} },
  ],
  layout: { positions: {} },
}
assert('整条允许并列分支', testScopePlan(paraState, ['nd_a', 'nd_b'], { requireConnected: false }).error === '')
assert('片段保持连通校验', testScopePlan(paraState, ['nd_a', 'nd_b'], { requireConnected: true }).error.includes('不连通'))
assert('默认保持连通校验（向后兼容）', testScopePlan(paraState, ['nd_a', 'nd_b']).error.includes('不连通'))

console.log(failed ? `\n${failed} 项失败` : '\n全部通过')
process.exit(failed ? 1 : 0)
