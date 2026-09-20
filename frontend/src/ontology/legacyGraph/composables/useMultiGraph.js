// 多图谱预览状态管理（由 wiz_kq_builder_view v2.2/v2.3 移植，数据源为「图谱+版本」API）
// - 根节点 + 徽标常驻画布；点徽标展开/折叠，点根节点主体仅选中；
// - 展开锚定根节点当前位置、零位移、无避让（重叠由用户拖动处理）；
// - 根节点可拖动：折叠态自由拖（根+徽标），展开态带动整组平移；
// - 自动 fit 带缩放上限（内容少时不放大）。
import { ref, shallowRef } from 'vue'
import cytoscape from 'cytoscape'
import { MULTI_PREVIEW_STYLE } from '../shared/graphStyle.js'
import { versionStateRaw } from '../../api'
import { decodeState } from '../../modelFormat'
import { projectToDraft } from '../legacyBridge.js'
import {
  buildGroup,
  rootNode,
  badgeNode,
  semanticNodes,
  relationEdges,
  groupLinkEdges,
  rootId,
  badgeId,
  rootStatsText,
} from '../core/multiGraph.js'
import { badgePos, initialRootPositions, contentLayout } from '../core/multiLayout.js'

const ANIM = { duration: 420, easing: 'ease-out' }
// 自动 fit 的缩放上限：内容较少时（如几个根节点）fit 会过度放大，限到 1.0 按原始尺寸展示
const FIT_ZOOM_CAP = 1.0

export function useMultiGraph() {
  const groups = ref([])
  const loading = ref(true)
  const loadError = ref('')
  const cy = shallowRef(null)
  const selected = ref(null)
  let fitTimer = null

  const findGroup = (key) => groups.value.find((g) => g.key === key)

  // ---------- 画布基础操作 ----------
  function groupNodes(key) {
    return cy.value.nodes(`[groupKey = "${key}"]`)
  }

  /** fit 后如过度放大则缩回上限，内容居中保持 */
  function fitCapped() {
    try {
      cy.value.fit(undefined, 70)
      if (cy.value.zoom() > FIT_ZOOM_CAP) cy.value.zoom(FIT_ZOOM_CAP)
    } catch (e) { /* 空画布忽略 */ }
  }

  function scheduleFit() {
    clearTimeout(fitTimer)
    fitTimer = setTimeout(fitCapped, 480)
  }

  /** 根节点展示态（label / 类）与展开状态同步 */
  function refreshRootPresentation(group) {
    const inst = cy.value
    if (!inst) return
    const rootDef = rootNode(group)
    const badgeDef = badgeNode(group)
    const root = inst.getElementById(rootDef.data.id)
    const badge = inst.getElementById(badgeDef.data.id)
    if (!root.empty()) {
      root.data(rootDef.data)
      root.classes(rootDef.classes)
    }
    if (!badge.empty()) {
      badge.data(badgeDef.data)
      badge.classes(badgeDef.classes)
    }
  }

  /** 新组入画：根节点 + 徽标落在指定位置 */
  function addGroupToCanvas(group, pos) {
    const root = rootNode(group)
    const badge = badgeNode(group)
    root.position = { ...pos }
    badge.position = badgePos(pos)
    cy.value.add([root, badge])
    applySearch()
  }

  // ---------- 展开 / 折叠 ----------
  function toggleGroup(key) {
    const g = findGroup(key)
    if (!g || !cy.value) return
    if (g.expanded) collapseGroup(g)
    else expandGroup(g)
  }

  function expandGroup(g) {
    const inst = cy.value
    g.expanded = true
    const rootEl = inst.getElementById(rootId(g))
    const cur = { ...rootEl.position() }

    // 三列内容锚定根节点当前位置就位；根节点不移动、不避让（重叠由用户拖动处理）
    const { positions } = contentLayout(g, cur)
    const nodes = semanticNodes(g).map((n) => ({ ...n, position: positions[n.data.id] || { x: 0, y: 0 } }))
    inst.add([...nodes, ...relationEdges(g), ...groupLinkEdges(g)])

    refreshRootPresentation(g)
    applySearch()
    // 原位展开：根节点不动，不自动缩放/平移视口
  }

  function collapseGroup(g) {
    const inst = cy.value
    g.expanded = false
    // 内容元素移除（根/徽标保留，位置不变）
    const keep = new Set([rootId(g), badgeId(g)])
    inst.remove(groupNodes(g.key).filter((n) => !keep.has(n.id())))
    if (selected.value?.groupKey === g.key && selected.value.kind !== 'root') selected.value = null
    inst.remove(inst.edges(`[groupKey = "${g.key}"]`))

    refreshRootPresentation(g)
    // 原位折叠：根节点留在当前位置，不自动缩放/平移视口
  }

  function expandAll() {
    groups.value.forEach((g) => { if (!g.expanded) expandGroup(g) })
  }
  function collapseAll() {
    groups.value.forEach((g) => { if (g.expanded) collapseGroup(g) })
  }

  // ---------- 拖动 ----------
  // 根节点拖动：徽标始终跟随右缘；展开组则整组平移；折叠组自由移动。
  function bindDragHandlers(inst) {
    const dragPrev = new Map()
    // 拖动开始先记基准，保证首个 drag 事件即可算出增量（整组平移不丢第一段位移）
    inst.on('grab mousedown touchstart', 'node.root', (evt) => {
      dragPrev.set(evt.target.id(), { ...evt.target.position() })
    })
    inst.on('drag', 'node.root', (evt) => {
      const rootEl = evt.target
      const g = findGroup(rootEl.data('groupKey'))
      if (!g) return
      const cur = { ...rootEl.position() }
      const prev = dragPrev.get(rootEl.id())
      if (prev) {
        const dx = cur.x - prev.x
        const dy = cur.y - prev.y
        if (g.expanded && (dx || dy)) {
          groupNodes(g.key).forEach((n) => {
            if (n.id() === rootEl.id() || n.id() === badgeId(g)) return
            n.position({ x: n.position('x') + dx, y: n.position('y') + dy })
          })
        }
      }
      dragPrev.set(rootEl.id(), cur)
      inst.getElementById(badgeId(g)).position(badgePos(cur))
    })
    inst.on('free dragfree', 'node.root', (evt) => {
      dragPrev.delete(evt.target.id())
    })
  }

  // ---------- 搜索 ----------
  let query = ''
  function setQuery(q) {
    query = String(q || '').trim().toLowerCase()
    applySearch()
  }
  function applySearch() {
    const inst = cy.value
    if (!inst) return
    inst.nodes().removeClass('search-hit dim')
    inst.edges().removeClass('dim')
    if (!query) return
    inst.nodes().forEach((n) => {
      const hit = String(n.data('name') || '').toLowerCase().includes(query)
      n.addClass(hit ? 'search-hit' : 'dim')
    })
    inst.edges().forEach((e) => {
      if (!e.source().hasClass('search-hit') || !e.target().hasClass('search-hit')) e.addClass('dim')
    })
  }

  // ---------- 组管理 ----------
  function removeGroup(key) {
    cy.value.remove(cy.value.elements(`[groupKey = "${key}"]`))
    groups.value = groups.value.filter((g) => g.key !== key)
    if (selected.value?.groupKey === key) selected.value = null
    scheduleFit()
  }

  /** 清空画布，按版本 id 列表加载各组（每份「图谱+版本」= 一个组） */
  async function loadItems(vids) {
    const inst = cy.value
    if (!inst) return
    loading.value = true
    loadError.value = ''
    groups.value = []
    selected.value = null
    inst.elements().remove()
    try {
      const list = []
      for (const key of vids) {
        // key = `本体ID@发布版本名`：加载只读发布快照并复用编辑器投影
        const at = String(key).lastIndexOf('@')
        const ontologyId = String(key).slice(0, at)
        const version = String(key).slice(at + 1)
        const raw = await versionStateRaw(ontologyId, version)
        const state = decodeState(raw.state)
        state.workflow = state.workflow || {}
        const baseName = state.workflow.objective?.name || ontologyId
        state.workflow.objective = { name: baseName }
        const projected = projectToDraft(state, {})
        const draft = { name: version, nodes: projected.nodes, edges: projected.edges }
        list.push(buildGroup(draft, { title: `${baseName} · ${version}` }))
      }
      if (!list.length) throw new Error('未选择任何发布版本')
      groups.value = list
      const posList = initialRootPositions(list.length)
      list.forEach((g, i) => addGroupToCanvas(g, posList[i]))
      setTimeout(fitCapped, 60)
    } catch (e) {
      loadError.value = '加载失败：' + e.message
    } finally {
      loading.value = false
    }
  }

  // ---------- 选中（详情面板数据） ----------
  function selectNode(el) {
    const g = findGroup(el.data('groupKey'))
    const id = el.id()
    const relations = g
      ? g.edges
          .filter((e) => e.source === id || e.target === id)
          .map((e) => {
            const other = e.source === id ? e.target : e.source
            const on = g.nodes.find((n) => n.id === other)
            return { id: e.id, label: e.relation, name: on ? on.name : other, dir: e.source === id ? 'out' : 'in' }
          })
      : []
    selected.value = {
      id,
      kind: 'node',
      groupKey: el.data('groupKey'),
      node: { id, name: el.data('name'), type: el.data('type'), data: el.data('data') || {} },
      relations,
    }
  }
  function selectEdge(el) {
    selected.value = {
      id: el.id(),
      kind: 'edge',
      groupKey: el.data('groupKey'),
      edge: {
        id: el.id(),
        label: el.data('relation'),
        description: el.data('description'),
        sourceName: el.source().data('name'),
        targetName: el.target().data('name'),
      },
    }
  }
  function selectRoot(el) {
    const g = findGroup(el.data('groupKey'))
    if (!g) return
    selected.value = {
      id: el.id(),
      kind: 'root',
      groupKey: g.key,
      group: { key: g.key, title: g.title, statsText: rootStatsText(g), expanded: g.expanded },
    }
  }

  // ---------- 初始化 ----------
  function initCanvas(canvasEl) {
    cy.value = cytoscape({
      container: canvasEl,
      elements: [],
      wheelSensitivity: 0.2,
      minZoom: 0.02,
      maxZoom: 4,
      autoungrabify: false,
      boxSelectionEnabled: false,
      layout: { name: 'preset' },
      style: MULTI_PREVIEW_STYLE,
    })
    window.__cy = cy.value // 调试 / 测试用（与编辑页/预览页一致）
    bindDragHandlers(cy.value)
    cy.value.on('tap', (evt) => {
      if (evt.target === cy.value) {
        selected.value = null
        return
      }
      const el = evt.target
      if (el.hasClass('badge')) {
        toggleGroup(el.data('groupKey')) // 点 ＋/− 切换展开折叠
      } else if (el.hasClass('root')) {
        selectRoot(el) // 点根节点主体仅选中，不切换
      } else if (el.isNode()) {
        selectNode(el)
      } else if (el.isEdge()) {
        selectEdge(el)
      }
    })
  }

  function fitView() {
    if (cy.value) fitCapped()
  }

  return {
    groups, loading, loadError, selected,
    initCanvas, loadItems,
    toggleGroup, expandAll, collapseAll, removeGroup,
    setQuery, fitView,
  }
}
