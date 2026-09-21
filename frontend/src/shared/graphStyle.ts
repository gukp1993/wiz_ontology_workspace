// cytoscape 画布样式（编辑页与预览页共用，保证视觉一致）
// 本模块是图谱/画布色值的唯一来源（DESIGN.md 登记例外 2、3）：cytoscape 不解析 CSS 变量，
// 色值只能落在这里。页面需要同一色值时用 v-bind 读回常量，不得再写十六进制副本。
import type { StylesheetJson, StylesheetJsonBlock } from 'cytoscape'

// 画布白：节点底色/描边、文字描底与 PNG 导出底色的唯一一份定义（例外 2：同一色值全站只允许一处）。
export const CANVAS_WHITE = '#ffffff'

// 节点/边基础块：编辑图与预览图共用同一份，避免两图各自漂移。
const BASE_NODE: StylesheetJsonBlock = {
  selector: 'node',
  style: {
    width: 'data(w)',
    height: 'data(h)',
    'background-color': CANVAS_WHITE,
    'border-width': 2.2,
    'border-color': '#94a3a3',
    'border-style': 'solid',
    label: 'data(name)',
    color: '#172126',
    'font-size': 12.5,
    'font-weight': 700,
    'text-valign': 'center',
    'text-halign': 'center',
    'text-wrap': 'wrap',
    'text-max-width': 'data(w)',
    'overlay-opacity': 0,
  },
}

const BASE_EDGE: StylesheetJsonBlock = {
  selector: 'edge',
  style: {
    width: 1.5,
    'curve-style': 'bezier',
    'line-color': '#4d7770',
    'line-opacity': 0.45,
    'target-arrow-shape': 'triangle',
    'target-arrow-color': '#657d78',
    'target-arrow-fill': 'filled',
    'arrow-scale': 0.7,
    label: 'data(relation)',
    'font-size': 10.5,
    color: '#87500f',
    'font-weight': 600,
    'text-outline-color': CANVAS_WHITE,
    'text-outline-width': 3,
    'text-outline-opacity': 1,
    'overlay-opacity': 0,
  },
}

// 本体类型分类配色（实体/属性/规则）：唯一来源，页面侧同一色值从这里读。
export const ONTOLOGY_CATEGORY = {
  entity: { fill: '#e9f2ff', border: '#3978c5' },
  attribute: { fill: '#fff0f7', border: '#d15e9a' },
  rule: { fill: '#edf9f1', border: '#35a167' },
} as const

const NODE_ENTITY: StylesheetJsonBlock = {
  selector: '[type = "实体"]',
  style: { 'background-color': ONTOLOGY_CATEGORY.entity.fill, 'border-color': ONTOLOGY_CATEGORY.entity.border, shape: 'round-rectangle' },
}
const NODE_ATTRIBUTE: StylesheetJsonBlock = {
  selector: '[type = "属性"]',
  style: { 'background-color': ONTOLOGY_CATEGORY.attribute.fill, 'border-color': ONTOLOGY_CATEGORY.attribute.border, shape: 'ellipse' },
}
const NODE_RULE: StylesheetJsonBlock = {
  selector: '[type = "规则"]',
  style: { 'background-color': ONTOLOGY_CATEGORY.rule.fill, 'border-color': ONTOLOGY_CATEGORY.rule.border, shape: 'round-rectangle' },
}

// 选中/追踪高亮蓝：与 style.css 的 --blue / --blue-deep 同值（画布侧镜像，改令牌需同步这里）。
const TRACE_UP = '#245cdf'
const TRACE_DOWN = '#1a49b0'

const NODE_SELECTED: StylesheetJsonBlock = { selector: 'node:selected', style: { 'border-color': TRACE_UP, 'border-width': 4.2 } }
const EDGE_SELECTED: StylesheetJsonBlock = {
  selector: 'edge:selected',
  style: { 'line-color': TRACE_UP, 'line-opacity': 0.98, 'target-arrow-color': TRACE_UP, width: 2.6 },
}
const EDGE_HOVERED: StylesheetJsonBlock = {
  selector: 'edge.hovered',
  style: { 'line-color': TRACE_UP, 'line-opacity': 0.98, 'target-arrow-color': TRACE_UP, width: 2.6 },
}
const EDGE_TRACE: StylesheetJsonBlock = {
  selector: 'edge.trace',
  style: { 'line-color': TRACE_UP, 'line-opacity': 0.98, 'target-arrow-color': TRACE_UP, width: 2.6 },
}
const EDGE_DOWN: StylesheetJsonBlock = {
  selector: 'edge.down',
  style: { 'line-color': TRACE_DOWN, 'line-opacity': 0.98, 'target-arrow-color': TRACE_DOWN, width: 2.6 },
}

export const GRAPH_STYLE: StylesheetJson = [
  BASE_NODE,
  NODE_ENTITY,
  NODE_ATTRIBUTE,
  NODE_RULE,
  NODE_SELECTED,
  { selector: 'node.link-src', style: { 'border-style': 'dashed', 'border-color': '#9bb2ac', 'border-width': 1.8 } },
  { selector: 'node.search-hit', style: { 'border-color': '#0b7ec2', 'border-width': 4.5, 'z-index': 20 } },
  { selector: 'node.search-focus', style: { 'border-color': TRACE_UP, 'border-width': 5.2, 'z-index': 25 } },
  { selector: 'node.dim', style: { opacity: 0.09 } },
  BASE_EDGE,
  EDGE_SELECTED,
  EDGE_HOVERED,
  { selector: 'edge.dim', style: { opacity: 0.08 } },
]

// 预览查看器专用样式（移植自旧版 kq-editor 静态预览页）：
// 浅色填充 + 边框色的节点、选中/追踪蓝色高亮（上游 #245cdf、下游 #1a49b0）、上游/下游/淡出分类、关系作用域高亮
export const PREVIEW_GRAPH_STYLE: StylesheetJson = [
  BASE_NODE,
  NODE_ENTITY,
  NODE_ATTRIBUTE,
  NODE_RULE,
  NODE_SELECTED,
  { selector: 'node.ghost', style: { 'border-style': 'dashed', 'border-color': '#9bb2ac', 'border-width': 1.8 } },
  BASE_EDGE,
  EDGE_SELECTED,
  { selector: 'node.anc', style: { 'border-color': TRACE_UP, 'border-width': 3.0 } },
  { selector: 'node.desc', style: { 'border-color': TRACE_DOWN, 'border-width': 2.8 } },
  { selector: 'node.dim', style: { opacity: 0.09 } },
  EDGE_TRACE,
  EDGE_DOWN,
  { selector: 'edge.dim', style: { opacity: 0.08 } },
  EDGE_HOVERED,
]

// 编排画布节点分类配色：数据可视编码的唯一来源。
export const FLOW_CATEGORY = {
  python: { fill: '#e9f2ff', border: '#3978c5' },
  sql: { fill: '#edf9f1', border: '#35a167' },
  redis: { fill: '#fff2e5', border: '#d9832b' },
  http: { fill: '#e6f6f8', border: '#2b9db0' },
  calc: { fill: '#fdf0f6', border: '#c9566e' },
  boundary: { fill: '#f3f0ff', border: '#8464d8' },
} as const

export const FLOW_STATE = {
  failed: '#c4534d',
  waiting: '#8a8f9f',
  checked: '#2b9db0',
  bindSrc: '#3978c5',
} as const

// 知识图谱（本体浏览画布）节点分类配色：七类资源的可视编码唯一来源，页面图例必须从这里读，
// 否则图例色与画布色会各自漂移。
export const KNOWLEDGE_KIND_FILL = {
  object: '#dfeaff',
  property: '#e1f3ef',
  valueType: '#e9e2f7',
  function: '#fff0d9',
  action: '#ffe3e8',
  interface: '#dff2f7',
  mapping: '#e9edf3',
} as const

export const KNOWLEDGE_CANVAS = {
  nodeText: '#29415e',
  nodeBorder: '#aebed3',
  edgeLine: '#8b9db6',
  edgeLabel: '#61748c',
  edgeDependency: '#acb8c8',
  focused: '#2563b9',
  focusedLabel: '#164e99',
  labelBg: CANVAS_WHITE,
  exportBg: CANVAS_WHITE,
} as const

// 实例关系图：按对象轮转取色，色相数与图例同源。
export const INSTANCE_NODE_PALETTE = ['#3978d5', '#149b8e', '#9561ca', '#d78a31', '#cc6485', '#537c95'] as const

// 对象类型数超过调色板容量时按黄金角递增色相补色：直接取 palette[index] 会让第 7 个类型起重复最后一色。
export function instanceNodeColor(index: number): string {
  return index < INSTANCE_NODE_PALETTE.length
    ? INSTANCE_NODE_PALETTE[Math.max(0, index)]
    : `hsl(${(index * 137.508) % 360}, 55%, 45%)`
}

export const INSTANCE_GRAPH = {
  nodeText: '#243b55',
  nodeBorder: CANVAS_WHITE,
  edgeLine: '#aabbd0',
  edgeLabel: '#63768c',
  focused: '#123f82',
  labelBg: CANVAS_WHITE,
  exportBg: CANVAS_WHITE,
} as const

// 编排画布在 GRAPH_STYLE 之上的增量：节点标签换行、分类配色、运行态与绑定态描边。
export const FLOW_EXTRA_STYLE: StylesheetJson = [
  { selector: 'node', style: { label: 'data(label)', 'font-weight': 600, 'text-wrap': 'wrap' } },
  { selector: '[type = "python"]', style: { 'background-color': FLOW_CATEGORY.python.fill, 'border-color': FLOW_CATEGORY.python.border, shape: 'round-rectangle' } },
  { selector: '[type = "sql"]', style: { 'background-color': FLOW_CATEGORY.sql.fill, 'border-color': FLOW_CATEGORY.sql.border, shape: 'round-rectangle' } },
  { selector: '[type = "redis"]', style: { 'background-color': FLOW_CATEGORY.redis.fill, 'border-color': FLOW_CATEGORY.redis.border, shape: 'round-rectangle' } },
  { selector: '[type = "http"]', style: { 'background-color': FLOW_CATEGORY.http.fill, 'border-color': FLOW_CATEGORY.http.border, shape: 'round-rectangle' } },
  { selector: '[type = "calc"]', style: { 'background-color': FLOW_CATEGORY.calc.fill, 'border-color': FLOW_CATEGORY.calc.border, shape: 'round-rectangle' } },
  { selector: '[type = "input"],[type = "output"]', style: { 'background-color': FLOW_CATEGORY.boundary.fill, 'border-color': FLOW_CATEGORY.boundary.border, shape: 'round-tag' } },
  { selector: 'node.boundary', style: { 'border-style': 'double', 'border-width': 4 } },
  { selector: 'node.run-failed', style: { 'border-color': FLOW_STATE.failed, 'border-width': 4.5 } },
  { selector: 'node.run-waiting', style: { 'border-style': 'dashed', 'border-color': FLOW_STATE.waiting } },
  { selector: 'node.test-checked', style: { 'border-style': 'dashed', 'border-color': FLOW_STATE.checked, 'border-width': 4 } },
  { selector: 'node.bind-src', style: { 'border-style': 'dashed', 'border-color': FLOW_STATE.bindSrc, 'border-width': 4 } },
]
