// 多图谱画布布局器（纯函数，由 wiz_kq_builder_view v2.2 移植：思维导图式锚定展开，无避让）
// - 初始：根节点沿纵向主干列排开，内容向右侧生长；
// - 展开组：三列语义布局锚定根节点当前位置，根节点不移动、不做冲突避让
//   （与其他组内容重叠属预期，由用户拖动根节点手动处理）。

export const LAY = {
  ROOT_W: 160, // 根节点宽（单行名称，较小）
  ROOT_H: 40, // 根节点高
  BADGE_DX: 98, // 徽标中心相对根节点中心的 x 偏移（右缘外）
  COL_X0: 270, // 内容第一列（实体）相对根节点中心的 x 偏移
  COL_GAP: 360, // 组内列间距（实体/属性/规则）
  ROW_H: 96, // 组内列行距
  BAND_PAD: 90, // 内容区上下留白（半高）
  TRUNK_STEP: 110, // 初始根节点纵向间距
}

/** 徽标位置：根节点右缘 */
export function badgePos(rootPos) {
  return { x: rootPos.x + LAY.BADGE_DX, y: rootPos.y }
}

/** 初始主干列：n 个根节点纵向排开、垂直居中 */
export function initialRootPositions(n) {
  return Array.from({ length: n }, (_, i) => ({ x: 0, y: (i - (n - 1) / 2) * LAY.TRUNK_STEP }))
}

/**
 * 组内三列排序（源自 autoLayout）：
 * 实体按名称；属性按「关联实体排位」；规则按「关联属性排位，其次关联实体排位」。
 */
export function columnOrder(group) {
  const targets = { 实体: [], 属性: [], 规则: [] }
  group.nodes.forEach((n) => {
    ;(targets[n.type] || targets['属性']).push(n)
  })
  const orderMap = new Map()
  const byName = (a, b) => String(a.name || '').localeCompare(String(b.name || ''), 'zh')
  const byId = (a, b) => String(a.id || '').localeCompare(String(b.id || ''), 'zh')
  const adj = new Map()
  group.edges.forEach((e) => {
    if (!adj.has(e.source)) adj.set(e.source, new Set())
    if (!adj.has(e.target)) adj.set(e.target, new Set())
    adj.get(e.source).add(e.target)
    adj.get(e.target).add(e.source)
  })
  const nodeById = new Map(group.nodes.map((n) => [n.id, n]))
  const primaryOrder = (n, type) => {
    let best = Infinity
    const nb = adj.get(n.id)
    if (nb) {
      nb.forEach((other) => {
        const on = nodeById.get(other)
        if (on && on.type === type) {
          const idx = orderMap.get(on.id)
          if (idx != null && idx < best) best = idx
        }
      })
    }
    return best === Infinity ? 1e9 : best
  }
  targets['实体'].sort((a, b) => byName(a, b) || byId(a, b))
  targets['实体'].forEach((n, i) => orderMap.set(n.id, i))
  targets['属性'].sort((a, b) => primaryOrder(a, '实体') - primaryOrder(b, '实体') || byName(a, b) || byId(a, b))
  targets['属性'].forEach((n, i) => orderMap.set(n.id, i + 1e6))
  targets['规则'].sort(
    (a, b) =>
      primaryOrder(a, '属性') - primaryOrder(b, '属性') ||
      primaryOrder(a, '实体') - primaryOrder(b, '实体') ||
      byName(a, b) ||
      byId(a, b),
  )
  return targets
}

/**
 * 锚定根节点的内容三列布局。
 * @returns {{positions:Object.<string,{x,y}>, bbox:{x1,y1,x2,y2}}}
 */
export function contentLayout(group, rootPos) {
  const cols = columnOrder(group)
  const colKeys = ['实体', '属性', '规则']
  const maxRows = Math.max(1, ...colKeys.map((t) => cols[t].length))
  const bandH = (maxRows - 1) * LAY.ROW_H + LAY.BAND_PAD * 2
  const positions = {}
  colKeys.forEach((t, ci) => {
    const list = cols[t]
    list.forEach((n, i) => {
      positions[n.id] = {
        x: rootPos.x + LAY.COL_X0 + ci * LAY.COL_GAP,
        y: rootPos.y + (i - (list.length - 1) / 2) * LAY.ROW_H,
      }
    })
  })
  const bbox = {
    x1: rootPos.x + LAY.COL_X0 - 120,
    x2: rootPos.x + LAY.COL_X0 + 2 * LAY.COL_GAP + 120,
    y1: rootPos.y - bandH / 2,
    y2: rootPos.y + bandH / 2,
  }
  return { positions, bbox }
}
