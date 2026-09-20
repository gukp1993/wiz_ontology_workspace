// 多图谱画布：组构建与画布元素构造（由 wiz_kq_builder_view 移植，数据源为版本 draft）
// 一份「图谱+版本」= 一个组，元素 ID 加组前缀，多组共存无冲突。
import { nodeW, nodeH } from '../shared/layout.js'

let seq = 0

/**
 * 由版本 draft 构建一个图谱组（未展开）。
 * @param {object} draft getVersion 返回的 draft（nodes/edges 为编辑器内部格式，坐标忽略）
 * @param {{key?:string,title?:string}} [opts]
 */
export function buildGroup(draft, opts = {}) {
  const gk = opts.key || 'g' + ++seq
  const prefix = gk + ':'
  const nodes = (draft.nodes || []).map((n) => ({
    id: prefix + n.id,
    rawId: n.id,
    type: n.type,
    name: n.name,
    data: n.data || {},
  }))
  const idSet = new Set(nodes.map((n) => n.id))
  const edges = (draft.edges || [])
    .filter((e) => idSet.has(prefix + e.source) && idSet.has(prefix + e.target))
    .map((e) => ({
      id: prefix + e.id,
      source: prefix + e.source,
      target: prefix + e.target,
      relation: e.relation,
      description: e.description,
    }))
  const stats = { 实体: 0, 属性: 0, 规则: 0, nodes: nodes.length, edges: edges.length }
  nodes.forEach((n) => { stats[n.type] = (stats[n.type] || 0) + 1 })
  return {
    key: gk,
    title: opts.title || draft.name || '未命名',
    stats,
    nodes,
    edges,
    expanded: false,
  }
}

export function rootId(group) {
  return group.key + ':root'
}

export function badgeId(group) {
  return group.key + ':badge'
}

export function rootStatsText(group) {
  return `实体 ${group.stats['实体']} · 属性 ${group.stats['属性']} · 规则 ${group.stats['规则']} · 关系 ${group.stats.edges}`
}

/** 组根节点（仅图谱名一行，统计在详情面板查看），点击主体仅选中；展开切换由徽标承担 */
export function rootNode(group) {
  return {
    data: {
      id: rootId(group),
      name: group.title,
      label: group.title,
      type: '图谱',
      data: { stats: group.stats },
      w: 160,
      h: 40,
      groupKey: group.key,
    },
    classes: 'root' + (group.expanded ? ' expanded' : ''),
    position: { x: 0, y: 0 },
  }
}

/** ＋/− 徽标：根节点右缘小圆，点击切换展开/折叠；不可拖动，位置由根节点同步 */
export function badgeNode(group) {
  return {
    data: {
      id: badgeId(group),
      name: group.expanded ? '折叠' : '展开',
      label: group.expanded ? '−' : '+',
      type: '徽标',
      groupKey: group.key,
      w: 22,
      h: 22,
    },
    classes: 'badge' + (group.expanded ? ' expanded' : ''),
    position: { x: 0, y: 0 },
    grabbable: false,
  }
}

/** 组的语义节点（展开时加入画布） */
export function semanticNodes(group) {
  return group.nodes.map((n) => ({
    data: {
      id: n.id,
      name: n.name,
      type: n.type,
      w: nodeW(n.name),
      h: nodeH(n.name),
      data: n.data,
      groupKey: group.key,
    },
    classes: n.type,
    position: { x: 0, y: 0 },
  }))
}

/** 组内的语义关系边 */
export function relationEdges(group) {
  return group.edges.map((e) => ({
    data: {
      id: e.id,
      source: e.source,
      target: e.target,
      relation: e.relation,
      description: e.description,
      groupKey: group.key,
    },
    classes: 'rel',
  }))
}

/** 思维导图软连接：组根节点 → 每个实体节点（虚线，不代表语义关系） */
export function groupLinkEdges(group) {
  return group.nodes
    .filter((n) => n.type === '实体')
    .map((n) => ({
      data: {
        id: `${group.key}:link:${n.rawId}`,
        source: rootId(group),
        target: n.id,
        relation: '',
        groupKey: group.key,
      },
      classes: 'group-link',
    }))
}
