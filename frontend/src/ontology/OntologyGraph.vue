<!-- OntologyGraph — 本体图谱工作区（20260919 画布能力优化 v1，T1–T4）。
     内容投影：五类节点（对象/共享属性/私有属性/规则/动作）+ 全部实际关系
     （对象链接、共享引用、私有属性归属、规则关联、动作关联），由 ontologyGraphModel 纯函数负责；
     悬空引用不画假节点，按异常计数展示并可定位原定义。
     交互：搜索仅高亮 + 结果列表 + 显式定位；选择/移动工具、Shift 框选多选、整组拖动、Space 平移；
     1/2 跳与全可达的方向邻域（诱导子图保全部边）、环形临时布局与退出恢复；
     两种确定性整理布局 + 最多 30 步布局撤销/重做；缩放 10%–400%、适应窗口、定位选中；
     筛选/详情可收起、详情 280–480 可拖宽、窄屏覆盖抽屉、画布最大化；
     视图记忆经 app/auth 的 prefGet/prefSet 按账号 + 本体隔离（key: ontologyGraph:v2:<本体ID>）。
     约束：所有画布操作只改本机视图，不 emit before-change/changed、不调用保存/发布、不改业务 state 与 revision；
     也不读写旧的匿名缓存 ont-graph:*。 -->
<template>
<div ref="rootEl" class="og-app" :class="{ 'og-max': maximized, 'og-narrow': widthTier === 'narrow' }" @keydown="onStageKeydown" @keyup="onStageKeyup">
  <!-- 工具栏：一行主工具（窄屏由 CSS 折行收纳次要项） -->
  <div class="og-tools">
    <div class="og-search" :class="{ open: searchOpen }">
      <svg fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.4-3.4"/></svg>
      <input ref="searchEl" v-model="q" type="text" autocomplete="off" placeholder="搜索节点名称…"
        aria-label="搜索节点名称、别名" @focus="searchOpen = true" @keydown="onSearchKeydown"/>
      <button v-show="q" class="og-x" type="button" title="清除搜索" aria-label="清除搜索" @click="clearSearch">×</button>
      <div v-if="searchOpen && q.trim()" class="og-results" role="listbox" aria-label="搜索结果" @scroll="onResultsScroll">
        <p class="og-results-head">{{ searchView.total }} 个结果 · 搜索仅高亮，不隐藏其他节点</p>
        <button v-for="(hit, i) in searchView.items" :key="hit.id" type="button" role="option"
          :class="{ active: i === searchFocusIdx }" @mouseenter="searchFocusIdx = i" @click="locateSearchHit(hit)">
          <b>{{ hit.name }}</b><small>{{ hit.kind }}<template v-if="hit.ownerName"> · 所属：{{ hit.ownerName }}</template></small>
          <em v-if="hit.hidden" class="og-hidden-tag">被筛选隐藏 · 显示并定位</em>
        </button>
        <button v-if="searchView.items.length < searchView.total" type="button" class="og-more" @click="searchLimit += 20">
          加载更多（剩余 {{ searchView.total - searchView.items.length }}）
        </button>
        <p v-if="!searchView.total" class="og-results-head">没有匹配的节点</p>
      </div>
    </div>
    <div class="og-toolgroup" role="group" aria-label="画布工具">
      <button type="button" :class="{ active: tool === 'select' }" title="拖动空白框选" @click="setTool('select')">选择</button>
      <button type="button" :class="{ active: tool === 'pan' }" title="拖动空白平移" @click="setTool('pan')">移动</button>
    </div>
    <div class="og-toolgroup og-arrange">
      <select v-model="arrangeKind" aria-label="整理布局方式">
        <option value="types">按类型排列</option>
        <option value="groups">按关联聚集</option>
      </select>
      <button type="button" @click="arrange()">整理布局</button>
      <button type="button" :disabled="!canUndo" title="撤销布局" aria-label="撤销布局" @click="undoLayout">↶</button>
      <button type="button" :disabled="!canRedo" title="重做布局" aria-label="重做布局" @click="redoLayout">↷</button>
    </div>
    <div class="og-grow"></div>
    <div class="og-toolgroup">
      <button type="button" :class="{ active: panels.filters }" @click="toggleFilters">筛选</button>
      <button type="button" :class="{ active: panels.inspector }" @click="toggleInspector()">详情</button>
      <button class="og-optional" type="button" @click="toggleMaximize">{{ maximized ? '退出最大化' : '最大化' }}</button>
      <button type="button" class="og-help" title="操作帮助" aria-label="操作帮助" @click="helpOpen = true">?</button>
    </div>
  </div>

  <!-- 邻域状态条：仅邻域态出现 -->
  <div v-if="scope" class="og-scope">
    <b>{{ centerName }} · 关联查看</b>
    <label class="og-inline">范围
      <select :value="scope.depth" aria-label="关联范围" @change="setScopeDepth(Number(($event.target as HTMLSelectElement).value) as 1|2|99)">
        <option :value="1">1 跳</option><option :value="2">2 跳</option><option :value="99">全部可达</option>
      </select>
    </label>
    <label class="og-inline">方向
      <select :value="scope.direction" aria-label="关联方向" @change="setScopeDirection(($event.target as HTMLSelectElement).value as 'both'|'in'|'out')">
        <option value="both">相关（双向）</option><option value="in">流入</option><option value="out">流出</option>
      </select>
    </label>
    <label class="og-inline">布局
      <select :value="scope.localLayout" aria-label="邻域布局" @change="setScopeLayout(($event.target as HTMLSelectElement).value as 'original'|'circle')">
        <option value="original">原位置</option><option value="circle">环形展开</option>
      </select>
    </label>
    <span class="og-scope-note">仅显示范围内的节点与关系</span>
    <button type="button" @click="exitScope()">退出关联查看 ×</button>
  </div>

  <div class="og-body">
    <!-- 左：筛选（可收起；窄屏为覆盖抽屉） -->
    <aside v-show="panels.filters" class="og-side og-filters" aria-label="图谱筛选">
      <div class="og-panel-head"><span class="og-eyebrow">FILTERS</span><h3>图谱筛选</h3></div>
      <section class="og-section">
        <h4>节点类型</h4>
        <label v-for="t in NODE_KINDS" :key="t" class="og-check">
          <input type="checkbox" :checked="filters.kinds[t] !== false" @change="setKindFilter(t, ($event.target as HTMLInputElement).checked)"/>
          <i class="og-swatch" :style="{ background: KIND_STYLE[t].border }"></i>
          <span>{{ t }}</span><span class="og-count">{{ counts.kinds[t] || 0 }}</span>
        </label>
      </section>
      <section class="og-section">
        <h4>关系</h4>
        <label v-for="r in relationList" :key="r" class="og-check">
          <input type="checkbox" :checked="filters.relations[r] !== false" @change="setRelationFilter(r, ($event.target as HTMLInputElement).checked)"/>
          <i class="og-swatch" style="background:#4d7770"></i>
          <span class="og-relname">{{ r }}</span><span class="og-count">{{ counts.relations[r] || 0 }}</span>
        </label>
        <p v-if="!relationList.length" class="og-note">暂无关系</p>
      </section>
      <section v-if="model.dangling.length" class="og-section">
        <h4>数据异常</h4>
        <button type="button" class="og-dangling" @click="danglingOpen = !danglingOpen">
          ⚠ {{ model.dangling.length }} 处悬空引用{{ danglingOpen ? ' ▴' : ' ▾' }}
        </button>
        <div v-if="danglingOpen" class="og-dangling-list">
          <p v-for="d in model.dangling" :key="d.key" class="og-note">
            {{ d.summary }}
            <button v-for="t in d.nav" :key="t.label" type="button" class="og-mini" @click="go(t)">{{ t.label }} ↗</button>
          </p>
        </div>
      </section>
      <section class="og-section">
        <h4>图例</h4>
        <p class="og-note">
          <i v-for="t in NODE_KINDS" :key="t" class="og-dot" :style="{ background: KIND_STYLE[t].bg, borderColor: KIND_STYLE[t].border }"></i>
          <span v-for="t in NODE_KINDS" :key="'l' + t" class="og-legend">{{ t }}</span>
        </p>
        <p class="og-note">有向边：源节点 → 目标节点（环线为自关联，弧线错开为平行链接）。</p>
      </section>
      <div class="og-side-actions">
        <button type="button" @click="showAll()">显示全部</button>
        <button type="button" @click="resetPositions()">重置位置</button>
        <p class="og-note">筛选只影响当前视图。拖动与布局仅记在本机，不修改本体定义。</p>
      </div>
    </aside>

    <!-- 中：画布 -->
    <div class="og-center">
      <div ref="stageEl" class="og-stage" tabindex="0" aria-label="本体图谱画布"
        @pointerdown="onStagePointerDown" @pointermove="onStagePointerMove" @pointerup="onStagePointerUp" @pointercancel="onStagePointerUp">
        <div ref="canvasEl" class="og-canvas"></div>
        <div v-if="dragRect.visible" class="og-rubber" :style="{ left: dragRect.x + 'px', top: dragRect.y + 'px', width: dragRect.w + 'px', height: dragRect.h + 'px' }"></div>
        <div class="og-stats">{{ statsText }}</div>
        <div v-if="emptyHint" class="og-empty-hint">{{ emptyHint.title }}<small>{{ emptyHint.detail }}</small></div>
        <div v-if="edgeTip" class="og-edge-tip" :style="{ left: edgeTip.x + 'px', top: edgeTip.y + 'px' }">{{ edgeTip.text }}</div>
        <div class="og-hint">Shift 多选 / 框选 · Space 平移 · F 定位 · 滚轮缩放</div>
        <div class="og-zoom">
          <button type="button" title="缩小" aria-label="缩小" @click="zoomBy(1 / 1.2)">−</button>
          <button type="button" title="回到 100%" aria-label="回到 100%" @click="setZoomLevel(1)">{{ Math.round(zoomPct) }}%</button>
          <button type="button" title="放大" aria-label="放大" @click="zoomBy(1.2)">＋</button>
          <button type="button" @click="fitVisible()">适应窗口</button>
        </div>
      </div>
    </div>

    <!-- 右：详情（可收起 / 280–480 拖宽；窄屏为覆盖抽屉） -->
    <aside v-show="panels.inspector" class="og-side og-inspector" :class="{ 'og-overlay': widthTier === 'narrow' }" :style="{ width: (widthTier === 'narrow' ? 300 : panels.inspectorW) + 'px' }" aria-label="图谱详情">
      <div v-if="widthTier !== 'narrow'" class="og-resizer" title="拖动调整宽度" @mousedown="startResize"></div>
      <div class="og-panel-head">
        <span class="og-eyebrow">INSPECTOR</span>
        <div class="og-head-row"><h3>图谱详情</h3><button type="button" class="og-mini" aria-label="收起详情" @click="toggleInspector(false)">收起 ×</button></div>
      </div>

      <div v-if="!selectionCount && !selectedEdge" class="og-empty">
        <strong>选择一个节点或关系</strong>
        <span>单击查看详情；双击节点定位；查看关联进入邻域阅读。</span>
      </div>

      <article v-else class="og-details">
        <!-- 多选 -->
        <template v-if="selectionCount > 1">
          <div class="og-hero"><div class="og-hero-kicker"><span>多选</span></div><h3>已选择 {{ selectionCount }} 个节点</h3></div>
          <p class="og-note">{{ multiSummary }}</p>
          <p class="og-note">拖动其中任一节点可一起移动；一轮拖动记一个布局撤销点。</p>
          <div class="og-actions-col"><button type="button" @click="fitSelection()">定位选中</button><button type="button" @click="clearSelection()">取消选中</button></div>
        </template>

        <!-- 边详情 -->
        <template v-else-if="selectedEdge">
          <div class="og-hero">
            <div class="og-hero-kicker"><span class="og-tag">{{ selectedEdge.kind }}</span></div>
            <h3>{{ edgeEnds }}</h3>
            <p class="og-note">{{ selectedEdge.relation }}</p>
          </div>
          <div class="og-dsec">
            <h4>关系字段</h4>
            <div v-for="f in selectedEdge.detail" :key="f.label" class="og-field">
              <div class="og-flabel">{{ f.label }}</div>
              <div class="og-fvalue">{{ f.value || '—' }}</div>
            </div>
          </div>
          <div v-if="overlappingEdges.length > 1" class="og-dsec">
            <h4>同一端点间的多条关系（{{ overlappingEdges.length }}）</h4>
            <button v-for="e in overlappingEdges" :key="e.id" type="button" class="og-relbtn" :class="{ active: e.id === selectedEdge.id }" @click="selectEdge(e.id)">
              {{ e.relation }}<small>{{ e.kind }}</small>
            </button>
          </div>
          <div class="og-actions-col">
            <button v-for="t in selectedEdge.nav" :key="t.label" type="button" class="primary" @click="go(t)">{{ t.label }} ↗</button>
          </div>
        </template>

        <!-- 节点详情 -->
        <template v-else-if="selectedNode">
          <div class="og-hero">
            <div class="og-hero-kicker"><i :style="{ background: KIND_STYLE[selectedNode.kind].border }"></i><span>{{ selectedNode.kind }}</span></div>
            <h3>{{ selectedNode.name }}</h3>
          </div>
          <div class="og-dsec">
            <h4>业务定义</h4>
            <div v-for="f in selectedNode.detail" :key="f.label" class="og-field">
              <div class="og-flabel">{{ f.label }}</div>
              <div class="og-fvalue">{{ f.value || '—' }}</div>
            </div>
          </div>
          <div class="og-actions-col">
            <button type="button" @click="fitSelection()">定位选中</button>
            <button type="button" @click="toggleScope()">{{ scope ? '以此为中心' : '查看关联' }}</button>
          </div>
          <div class="og-dsec">
            <h4>直接关系（{{ nodeRelations.length }}）</h4>
            <div v-if="nodeRelations.length" class="og-rellist">
              <button v-for="r in nodeRelations" :key="r.id" type="button" class="og-relbtn" @click="pickNeighbor(r)">
                <span>{{ r.dir === 'out' ? r.label + ' →' : '← ' + r.label }}</span><small>{{ r.name }}</small>
              </button>
            </div>
            <p v-else class="og-note">（无关联连线）</p>
          </div>
          <div class="og-actions-col">
            <button v-for="t in selectedNode.nav" :key="t.label" type="button" class="primary" @click="go(t)">{{ t.label }} ↗</button>
          </div>
        </template>
      </article>
    </aside>
  </div>

  <!-- 帮助（Esc 关闭；画布内模态） -->
  <div v-if="helpOpen" class="og-modal" @click.self="helpOpen = false">
    <div class="og-modal-card" role="dialog" aria-modal="true" aria-label="画布操作帮助">
      <h3>画布操作</h3>
      <p>单击节点/边查看详情；Shift+单击增减选择；选择模式拖空白框选（Shift 追加），移动模式拖空白平移，按住 Space 临时平移。</p>
      <p>F 定位选中（无选中时适应窗口），Shift+F 适应窗口，+ / − 缩放，? 帮助，Esc 依次关闭搜索/帮助、退出邻域、取消选中、退出最大化。</p>
      <p>Ctrl/⌘+Z 在画布内只撤销布局（加 Shift 重做），不会影响本体定义的全局撤销；输入框内仍是文本撤销。</p>
      <p class="og-note">视图与布局只保存在本机（按账号与本体隔离），不修改本体定义，也不产生保存请求。</p>
      <div class="og-dialogtools"><button type="button" class="primary" @click="helpOpen = false">关闭</button></div>
    </div>
  </div>
  <p v-if="notice" class="og-toast" role="status">{{ notice }}</p>
</div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, shallowRef, watch } from 'vue'
import cytoscape from 'cytoscape'
import { prefGet, prefSet } from '../app/auth'
import {
  NODE_KINDS, buildGraphModel, diffModels, graphSignature, modelCounts, nodesById, relationNames,
  type EdgeKind, type GraphEdge, type GraphModel, type GraphNode, type NavTarget, type NodeKind,
} from './ontologyGraphModel'
import {
  CACHE_SCHEMA, HISTORY_LIMIT, clampInspectorW, clampZoom, defaultLayout, emptyView, inducedEdgeIds,
  layoutByComponents, layoutByType, layoutCircle, neighborhoodNodes, parseView, searchHitIds, searchList,
  serializeView, visibleNodeIds,
  type GraphFilters, type Position, type ScopeState, type SearchItem, type ViewMemory,
} from './ontologyGraphView'

// focusDomainId：从定义页返回时希望在图谱中定位的业务稳定 ID（可为对象/属性/规则/动作）
const props = defineProps<{ state: any; ontologyId?: string; focusDomainId?: string }>()
const emit = defineEmits(['navigate'])

const NODE_KIND_STYLE: Record<NodeKind, { border: string; bg: string; shape: string }> = {
  对象: { border: '#3978c5', bg: '#e9f2ff', shape: 'round-rectangle' },
  共享属性: { border: '#d15e9a', bg: '#fff0f7', shape: 'ellipse' },
  私有属性: { border: '#d18f3c', bg: '#fdf3e4', shape: 'ellipse' },
  规则: { border: '#35a167', bg: '#edf9f1', shape: 'round-rectangle' },
  动作: { border: '#7a5fb5', bg: '#f2eeff', shape: 'round-rectangle' },
}
const KIND_STYLE = NODE_KIND_STYLE

const rootEl = ref<HTMLElement | null>(null)
const stageEl = ref<HTMLElement | null>(null)
const canvasEl = ref<HTMLElement | null>(null)
const searchEl = ref<HTMLInputElement | null>(null)
const cy = shallowRef<any>(null)

const model = computed<GraphModel>(() => buildGraphModel(props.state))
const signature = computed(() => graphSignature(model.value))
const counts = computed(() => modelCounts(model.value))
const relationList = computed(() => relationNames(model.value))
const nodesIndex = computed(() => nodesById(model.value))

// ── 视图状态（只读业务 state，全部为画布本机状态） ──────────────────────────
const filters = reactive<GraphFilters>({ kinds: {}, relations: {} })
const panels = reactive({ filters: true, inspector: false, inspectorW: 320 })
const selection = ref<string[]>([])
const selectedEdgeId = ref('')
const scope = ref<ScopeState | null>(null)
const q = ref('')
const searchLimit = ref(20)
const searchFocusIdx = ref(-1)
const searchOpen = ref(false)
const danglingOpen = ref(false)
const tool = ref<'select' | 'pan'>('select')
const arrangeKind = ref<'types' | 'groups'>('types')
const maximized = ref(false)
const helpOpen = ref(false)
const notice = ref('')
const zoomPct = ref(100)
const widthTier = ref<'wide' | 'mid' | 'narrow'>('wide')
const dragRect = reactive({ visible: false, x: 0, y: 0, w: 0, h: 0 })
const edgeTip = ref<{ x: number; y: number; text: string } | null>(null)

let noticeTimer: any = null
function notify(text: string) { notice.value = text; clearTimeout(noticeTimer); noticeTimer = setTimeout(() => { notice.value = '' }, 4000) }

const centerName = computed(() => (scope.value ? nodesIndex.value.get(scope.value.center)?.name || scope.value.center : ''))
const selectionCount = computed(() => selection.value.length)
const selectedNode = computed<GraphNode | null>(() => (selection.value.length === 1 ? nodesIndex.value.get(selection.value[0]) || null : null))
const selectedEdge = computed<GraphEdge | null>(() => (selectedEdgeId.value ? model.value.edges.find(e => e.id === selectedEdgeId.value) || null : null))
const nameOf = (id: string) => nodesIndex.value.get(id)?.name || id
const edgeEnds = computed(() => (selectedEdge.value ? `${nameOf(selectedEdge.value.source)} → ${nameOf(selectedEdge.value.target)}` : ''))
const overlappingEdges = computed(() => {
  const e = selectedEdge.value
  if (!e) return []
  return model.value.edges.filter(x => (x.source === e.source && x.target === e.target && x.kind === e.kind) || x.id === e.id)
})
const nodeRelations = computed(() => {
  const n = selectedNode.value
  if (!n) return []
  return model.value.edges.filter(e => e.source === n.id || e.target === n.id)
    .map(e => ({ id: e.id, label: e.relation, name: nameOf(e.source === n.id ? e.target : e.source), dir: (e.source === n.id ? 'out' : 'in') as 'out' | 'in' }))
})
const multiSummary = computed(() => {
  const kinds: Record<string, number> = {}
  for (const id of selection.value) { const k = nodesIndex.value.get(id)?.kind; if (k) kinds[k] = (kinds[k] || 0) + 1 }
  return Object.entries(kinds).map(([k, v]) => `${k} ${v}`).join(' · ')
})

// ── 可见集（筛选 → 邻域） ────────────────────────────────────────────────
const kindVisibleIds = computed(() => visibleNodeIds(model.value, filters))
const visibleIds = computed(() => {
  const base = kindVisibleIds.value
  if (!scope.value) return base
  const inScope = neighborhoodNodes(model.value, scope.value.center, scope.value.depth, scope.value.direction, filters)
  const out = new Set<string>()
  for (const id of base) if (inScope.has(id)) out.add(id)
  return out
})
const visibleEdges = computed(() => inducedEdgeIds(model.value, visibleIds.value, filters))
const searchHits = computed(() => searchHitIds(model.value, q.value))
const searchView = computed(() => searchList(model.value, q.value, visibleIds.value, searchLimit.value))

const statsText = computed(() => {
  const visibleNodes = visibleIds.value.size, visibleEdgeCount = visibleEdges.value.size
  return `显示 ${visibleNodes}/${counts.value.nodeTotal} 节点 · ${visibleEdgeCount}/${counts.value.edgeTotal} 关系`
    + (selectionCount.value ? ` · 已选 ${selectionCount.value} 项` : '')
    + (scope.value ? ' · 关联查看中' : '')
})
const emptyHint = computed(() => {
  if (!counts.value.nodeTotal) return { title: '本体还没有可上图的定义', detail: '在对象建模中添加对象、属性或规则后回到本页。' }
  if (!visibleIds.value.size) return { title: '当前筛选没有可见节点', detail: '请在左侧调整节点类型/关系筛选，或点击「显示全部」。' }
  return null
})

// ── Cytoscape 生命周期与增量同步 ─────────────────────────────────────────
let syncedModel: GraphModel | null = null
let positionsCache: Record<string, Position> = {}
let scopeBackup: { positions: Record<string, Position>; viewport: { zoom: number; pan: { x: number; y: number } }; selection: string[]; edgeId: string; undo: LayoutStep[]; redo: LayoutStep[] } | null = null
let undoStack: LayoutStep[] = []
let redoStack: LayoutStep[] = []
interface LayoutStep { positions: Record<string, Position>; viewport: { zoom: number; pan: { x: number; y: number } }; label: string }
const canUndo = ref(false), canRedo = ref(false)
const syncHistoryFlags = () => { canUndo.value = undoStack.length > 0; canRedo.value = redoStack.length > 0 }

let prefKey = ''
let cacheRaw: string | null = null
let hasSavedViewport = false
// 视图意图计数：任何显式的视口设置（适应/定位/恢复/百分比缩放）之后，容器尺寸变化不得再叠加"保持中心"
// 的平移补偿——否则邻域状态条的出现/消失会把视口推走（G10 退出不漂移）。
let viewportIntent = 0
let saveTimer: any = null
let saveWarned = false
let resizeObserver: ResizeObserver | null = null
let spaceHeld = false
let dragStart: { x: number; y: number; add: boolean; moved: boolean } | null = null
let grabbedSnapshot: LayoutStep | null = null

function cacheKeyFor(ontologyId: string) { return 'ontologyGraph:v2:' + (ontologyId || '') }

function vp() { const c = cy.value; return c ? { zoom: c.zoom(), pan: { x: c.pan().x, y: c.pan().y } } : { zoom: 1, pan: { x: 0, y: 0 } } }
function livePositions(): Record<string, Position> {
  const out: Record<string, Position> = {}
  cy.value?.nodes().forEach((n: any) => { out[n.id()] = { x: n.position('x'), y: n.position('y') } })
  return out
}
/** 当前应持久化的全图基础位置：邻域态下用进入前快照（临时坐标不落盘）。 */
function basePositions(): Record<string, Position> { return scopeBackup ? { ...scopeBackup.positions } : livePositions() }

function buildView(): ViewMemory {
  return {
    schemaVersion: CACHE_SCHEMA,
    positions: basePositions(),
    viewport: vp(),
    filters: { kinds: { ...filters.kinds }, relations: { ...filters.relations } },
    panels: { filters: panels.filters, inspector: panels.inspector, inspectorW: panels.inspectorW },
    selection: { nodeIds: [...selection.value], edgeId: selectedEdgeId.value },
    scope: scope.value ? { ...scope.value } : null,
  }
}
function saveViewNow() { if (!prefKey) return; try { prefSet(prefKey, serializeView(buildView())) } catch { /* 存储不可用时仅本次会话有效 */ } }
function saveViewDebounced() { if (!prefKey) return; clearTimeout(saveTimer); saveTimer = setTimeout(saveViewNow, 300) }
function flushView() { clearTimeout(saveTimer); saveViewNow() }

function styleFor(): any[] {
  const styles: any[] = [
    { selector: 'node', style: {
      width: 'data(w)', height: 'data(h)', 'background-color': '#fff', 'border-width': 2.2, 'border-color': '#94a3a3',
      label: 'data(label)', color: '#172126', 'font-size': 12.5, 'font-weight': 700, 'text-valign': 'center', 'text-halign': 'center',
      'text-wrap': 'wrap', 'text-max-width': 'data(w)', 'line-height': 1.25, 'overlay-opacity': 0, shape: 'round-rectangle' } },
    ...NODE_KINDS.map(k => ({ selector: `[kind = "${k}"]`, style: { 'background-color': KIND_STYLE[k].bg, 'border-color': KIND_STYLE[k].border, shape: KIND_STYLE[k].shape as any } })),
    { selector: 'node:selected', style: { 'border-color': '#2858e8', 'border-width': 3.4 } },
    { selector: 'node.search-hit', style: { 'border-color': '#f3a12d', 'border-width': 3.6 } },
    { selector: 'node:selected.search-hit', style: { 'border-color': '#2858e8' } },
    { selector: 'edge', style: {
      width: 1.5, 'curve-style': 'bezier', 'line-color': '#90a4ba', 'line-opacity': 0.85,
      'target-arrow-shape': 'triangle', 'target-arrow-color': '#90a4ba', 'target-arrow-fill': 'filled', 'arrow-scale': 0.7,
      label: 'data(relation)', 'font-size': 10.5, color: '#718298', 'font-weight': 600,
      'text-outline-color': '#fff', 'text-outline-width': 3, 'text-outline-opacity': 1, 'overlay-opacity': 0 } },
    { selector: 'edge.loop', style: { 'loop-direction': '-45deg', 'loop-sweep': '70deg' } },
    { selector: 'edge:selected', style: { 'line-color': '#2858e8', 'line-opacity': 1, 'target-arrow-color': '#2858e8', width: 2.6 } },
    { selector: 'edge.hovered', style: { 'line-color': '#2858e8', 'line-opacity': 1, 'target-arrow-color': '#2858e8', width: 2.4 } },
  ]
  return styles
}

function nodeData(n: GraphNode) {
  const hasKindLine = true
  const label = n.name + (hasKindLine ? '\n' + n.kind : '')
  const nameW = [...n.name].reduce((sum, ch) => sum + (ch.charCodeAt(0) > 255 ? 12.5 : 7), 0)
  const w = Math.min(210, Math.max(112, nameW + 30))
  const h = n.name.length > 12 ? 62 : 56
  return { id: n.id, name: n.name, kind: n.kind, label, w, h, domainId: n.domainId }
}
function edgeData(e: GraphEdge) { return { id: e.id, source: e.source, target: e.target, relation: e.relation, kind: e.kind, loop: e.source === e.target ? 'yes' : 'no' } }

function initCy() {
  if (!canvasEl.value) return
  const memory = loadMemory()
  const ids = new Set(model.value.nodes.map(n => n.id))
  positionsCache = { ...defaultLayout(model.value, ids), ...memory.positions }
  const elements = [
    ...model.value.nodes.map(n => ({ data: nodeData(n), position: positionsCache[n.id] || { x: 0, y: 0 } })),
    ...model.value.edges.map(e => ({ data: edgeData(e), classes: e.source === e.target ? 'loop' : '' })),
  ]
  cy.value = cytoscape({
    container: canvasEl.value, elements, wheelSensitivity: 0.2, minZoom: 0.1, maxZoom: 4,
    autoungrabify: false, boxSelectionEnabled: false, userPanningEnabled: tool.value === 'pan',
    layout: { name: 'preset' }, style: styleFor(),
  })
  bindCyEvents()
  syncedModel = model.value
  applyFiltersToCy()
  applySelectionToCy()
  // 仅当确有本机记忆时恢复视口；首次进入（或缓存不可用）必须适应可见节点，
  // 否则空视图的默认 zoom=1/pan=(0,0) 会把图谱留在画布左上角之外。
  viewportIntent++
  if (hasSavedViewport) {
    cy.value.zoom(clampZoom(memory.viewport.zoom))
    cy.value.pan(memory.viewport.pan)
  } else {
    fitVisible(false)
  }
  updateZoomPct()
  // 邻域参数随刷新恢复：重新派生临时布局
  if (memory.scope) { scope.value = { ...memory.scope }; applyFiltersToCy(); if (memory.scope.localLayout === 'circle') applyScopeLayout() }
  syncHistoryFlags()
  resizeObserver = new ResizeObserver(() => onContainerResize())
  if (stageEl.value) resizeObserver.observe(stageEl.value)
}

function loadMemory(): ViewMemory {
  prefKey = cacheKeyFor(props.ontologyId || props.state?.workspaceId || '')
  const ids = new Set(model.value.nodes.map(n => n.id))
  const edgeIds = new Set(model.value.edges.map(e => e.id))
  let raw: string | null = null
  try { raw = prefKey ? prefGet(prefKey) : null } catch { raw = null }
  if (raw && !cacheRaw) cacheRaw = raw
  const parsed = raw ? parseView(raw, ids, edgeIds) : null
  // 「有本机视图记忆」= 解析成功且确实存有节点坐标：结构合法但坐标被清洗为空的缓存
  // （例如损坏内容只剩未知 ID）不得阻止首次适应窗口。
  hasSavedViewport = !!(parsed && Object.keys(parsed.positions).length > 0)
  if (raw && !parsed && !saveWarned) {
    saveWarned = true
    notify('本机视图缓存不可用，已改用默认布局（不影响本体定义）。')
  }
  const memory = parsed || emptyView()
  for (const k of NODE_KINDS) if (!(k in memory.filters.kinds)) filters.kinds[k] = true
  for (const r of relationList.value) if (!(r in memory.filters.relations)) filters.relations[r] = true
  for (const [k, v] of Object.entries(memory.filters.kinds)) filters.kinds[k] = v
  for (const [k, v] of Object.entries(memory.filters.relations)) filters.relations[k] = v
  if (parsed) {
    panels.filters = memory.panels.filters
    panels.inspector = memory.panels.inspector
    panels.inspectorW = clampInspectorW(memory.panels.inspectorW)
    selection.value = memory.selection.nodeIds.filter(id => ids.has(id))
    selectedEdgeId.value = memory.selection.edgeId
  } else {
    panels.filters = window.innerWidth > 1280
  }
  return memory
}

function bindCyEvents() {
  const c = cy.value
  c.on('tap', (evt: any) => {
    if (evt.target === c) { clearSelection(); return }
    if (evt.target.isNode()) {
      const id = evt.target.id()
      const shift = !!(evt.originalEvent && (evt.originalEvent as MouseEvent).shiftKey)
      if (shift) toggleSelection(id)
      else selectNode(id, false)
      searchOpen.value = false
    } else if (evt.target.isEdge()) {
      selectEdge(evt.target.id())
    }
  })
  c.on('dbltap', 'node', (evt: any) => { selectNode(evt.target.id(), false); openInspector(true); fitSelection() })
  c.on('dbltap', 'edge', (evt: any) => { selectEdge(evt.target.id()); openInspector(true) })
  c.on('grab', 'node', () => { grabbedSnapshot = { positions: livePositions(), viewport: vp(), label: '拖动节点' } })
  c.on('dragfree', 'node', () => {
    if (!grabbedSnapshot) return
    const before = grabbedSnapshot; grabbedSnapshot = null
    if (JSON.stringify(before.positions) !== JSON.stringify(livePositions())) { undoStack.push(before); redoStack = []; if (undoStack.length > HISTORY_LIMIT) undoStack.shift(); syncHistoryFlags(); saveViewDebounced() }
  })
  c.on('mouseover', 'edge', (evt: any) => {
    evt.target.addClass('hovered')
    const e = model.value.edges.find(x => x.id === evt.target.id())
    if (e) {
      const s = evt.target.source().position(), t = evt.target.target().position()
      const zoom = c.zoom(), pan = c.pan()
      edgeTip.value = { x: (s.x + t.x) / 2 * zoom + pan.x + 10, y: (s.y + t.y) / 2 * zoom + pan.y - 8, text: `${nameOf(e.source)} → ${e.relation} → ${nameOf(e.target)}` }
    }
  })
  c.on('mouseout', 'edge', (evt: any) => { evt.target.removeClass('hovered'); edgeTip.value = null })
  c.on('zoom', () => { updateZoomPct(); saveViewDebounced() })
  c.on('pan', () => saveViewDebounced())
  c.on('select unselect', () => syncSelectionFromCy())
}

let applyingSelection = false
function syncSelectionFromCy() {
  const c = cy.value
  if (!c || applyingSelection) return
  selection.value = c.$('node:selected').map((n: any) => n.id())
  const edges = c.$('edge:selected')
  selectedEdgeId.value = edges.length ? edges[0].id() : ''
}

function applyFiltersToCy() {
  const c = cy.value
  if (!c) return
  const vis = visibleIds.value, eVis = visibleEdges.value
  c.batch(() => {
    c.edges().forEach((e: any) => { eVis.has(e.id()) ? e.show() : e.hide() })
    c.nodes().forEach((n: any) => { vis.has(n.id()) ? n.show() : n.hide() })
  })
  applySearchHighlight()
}
function applySearchHighlight() {
  const c = cy.value
  if (!c) return
  const hits = searchHits.value
  c.nodes().forEach((n: any) => n.toggleClass('search-hit', hits.has(n.id())))
}

/** 增量同步：只增删改变化元素；现存节点位置、视口、选择与邻域不因更新而重置。 */
function syncModel() {
  const c = cy.value
  if (!c) return
  const diff = diffModels(syncedModel, model.value)
  const structural = diff.addedNodes.length || diff.removedNodeIds.length || diff.addedEdges.length || diff.removedEdgeIds.length
  const removedSelected = diff.removedNodeIds.some(id => selection.value.includes(id))
  const removedEdge = diff.removedEdgeIds.includes(selectedEdgeId.value)
  const centerRemoved = !!(scope.value && diff.removedNodeIds.includes(scope.value.center))
  if (!structural && !diff.updatedNodes.length && !diff.updatedEdges.length && !diff.danglingChanged) return
  c.batch(() => {
    for (const id of diff.removedEdgeIds) { const el = c.getElementById(id); if (el.nonempty()) el.remove() }
    for (const id of diff.removedNodeIds) { const el = c.getElementById(id); if (el.nonempty()) el.remove() }
    for (const n of diff.addedNodes) {
      const pos = scope.value?.localLayout === 'circle' ? undefined : defaultLayout(model.value, new Set([n.id]))[n.id] || positionsCache[n.id] || { x: 0, y: 0 }
      c.add({ data: nodeData(n), position: pos || { x: 0, y: 0 } })
    }
    for (const e of diff.addedEdges) c.add({ data: edgeData(e), classes: e.source === e.target ? 'loop' : '' })
    for (const n of diff.updatedNodes) { const el = c.getElementById(n.id); if (el.nonempty()) el.data(nodeData(n)) }
    for (const e of diff.updatedEdges) {
      const el = c.getElementById(e.id)
      if (el.nonempty()) { el.data(edgeData(e)); el.removeClass('loop'); if (e.source === e.target) el.addClass('loop') }
    }
  })
  syncedModel = model.value
  positionsCache = { ...positionsCache, ...livePositions() }
  for (const id of diff.removedNodeIds) delete positionsCache[id]
  if (structural) {
    if (undoStack.length || redoStack.length) { undoStack = []; redoStack = []; syncHistoryFlags(); notify('图谱内容已变化，布局撤销历史已清空。') }
  }
  if (scope.value && (!nodesIndex.value.has(scope.value.center) || filters.kinds[nodesIndex.value.get(scope.value.center)?.kind || ''] === false)) {
    exitScope(true); notify('邻域中心已删除或被筛选隐藏，已退出关联查看。')
  }
  if (removedSelected) { selection.value = []; selectedEdgeId.value = ''; notify('选中的定义已删除，选择已清除。') }
  else if (removedEdge) selectedEdgeId.value = ''
  applyFiltersToCy()
  saveViewDebounced()
}

// ── 选择 ────────────────────────────────────────────────────────────────
function selectNode(id: string, additive = false) {
  if (additive) { toggleSelection(id); return }
  selection.value = [id]; selectedEdgeId.value = ''
  applySelectionToCy(); openInspector()
}
function toggleSelection(id: string) {
  const set = new Set(selection.value)
  set.has(id) ? set.delete(id) : set.add(id)
  selection.value = [...set]; selectedEdgeId.value = ''
  applySelectionToCy(); if (selection.value.length) openInspector()
}
function selectEdge(id: string) { selection.value = []; selectedEdgeId.value = id; applySelectionToCy(); openInspector() }
function clearSelection() { selection.value = []; selectedEdgeId.value = ''; applySelectionToCy(); if (scope.value) { /* 邻域中心保持，退出由按钮/Esc */ } }
function applySelectionToCy() {
  const c = cy.value
  if (!c) return
  const nodes = new Set(selection.value)
  applyingSelection = true
  try {
    c.batch(() => {
      c.nodes().forEach((n: any) => { if (nodes.has(n.id())) { if (!n.selected()) n.select() } else if (n.selected()) n.unselect() })
      c.edges().forEach((e: any) => { const on = e.id() === selectedEdgeId.value; if (on !== e.selected()) { on ? e.select() : e.unselect() } })
    })
  } finally { applyingSelection = false }
}
// 详情展开策略：未选中默认收起；第一次单选自动展开；用户主动关闭后单击只选中，双击或「详情」按钮再展开。
let userClosedInspector = false
function openInspector(force = false) {
  if (!panels.inspector && (force || !userClosedInspector)) {
    panels.inspector = true
    if (widthTier.value === 'narrow') panels.filters = false
    void nextTick(() => onContainerResize())
  } else if (panels.inspector && widthTier.value === 'narrow') {
    panels.filters = false
  }
}
function toggleInspector(force?: boolean) {
  const next = typeof force === 'boolean' ? force : !panels.inspector
  panels.inspector = next
  if (!next) userClosedInspector = true
  else userClosedInspector = false
  if (next && widthTier.value === 'narrow') panels.filters = false
  if (next) void nextTick(() => { const c = cy.value; c && c.resize() })
}
function toggleFilters() {
  panels.filters = !panels.filters
  if (panels.filters && widthTier.value === 'narrow') panels.inspector = false
  void nextTick(() => onContainerResize())
}
function pickNeighbor(r: { id: string; name: string }) { const e = model.value.edges.find(x => x.id === r.id); if (e) selectEdge(e.id) }
/** 打开定义：复用现有 focus 协议并附画布返回上下文（canvas），返回时由 ObjectWorkspace 恢复图谱视图。 */
function go(target: NavTarget) {
  const focus: Record<string, any> = { ...(target.focus || {}), canvas: true }
  // 返回图谱时的定位目标：优先定义 ID / 属性 ID，其次对象类型 ID（三类跳转都非空，
  // 目标页据此显示「返回图谱」，ObjectWorkspace 也据此切回列表/详情而不是停在图谱）。
  focus.canvasNode = target.focus?.definition || target.focus?.property || target.focus?.type || ''
  emit('navigate', target.view, focus)
}
/** 从定义页返回：定位到目标业务 ID（不存在则清空选择并提示）。 */
function focusDomain(id: string) {
  const hit = model.value.nodes.find(n => n.domainId === id)
  if (!hit) { selection.value = []; selectedEdgeId.value = ''; applySelectionToCy(); notify('目标定义已不存在，已清除画布选择。'); return }
  if (scope.value && !visibleIds.value.has(hit.id)) exitScope(true)
  if (filters.kinds[hit.kind] === false) filters.kinds[hit.kind] = true
  applyFiltersToCy()
  selection.value = [hit.id]; selectedEdgeId.value = ''; applySelectionToCy(); openInspector(true)
  const el = cy.value?.getElementById(hit.id)
  if (el && el.nonempty()) { const c = cy.value; c.animate({ center: { eles: el }, zoom: Math.min(1.6, Math.max(c.zoom(), 1)), duration: 260 }) }
}
watch(() => props.focusDomainId, (id) => { if (id && cy.value) focusDomain(id) })

// ── 筛选/搜索/邻域 ──────────────────────────────────────────────────────
function setKindFilter(kind: string, on: boolean) {
  filters.kinds[kind] = on
  if (scope.value && nodesIndex.value.get(scope.value.center)?.kind === kind && !on) { exitScope(true); notify('中心节点被筛选隐藏，已退出关联查看。') }
  afterFilterChange()
}
function setRelationFilter(relation: string, on: boolean) { filters.relations[relation] = on; afterFilterChange() }
function afterFilterChange() {
  selection.value = selection.value.filter(id => visibleIds.value.has(id))
  if (selectedEdgeId.value && !visibleEdges.value.has(selectedEdgeId.value)) selectedEdgeId.value = ''
  applyFiltersToCy(); applySelectionToCy(); saveViewDebounced()
}
function clearSearch() { q.value = ''; searchLimit.value = 20; searchFocusIdx.value = -1; searchOpen.value = false }
function onSearchKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter') { const hit = searchView.value.items[searchFocusIdx.value] || searchView.value.items[0]; if (hit) locateSearchHit(hit); e.preventDefault() }
  else if (e.key === 'ArrowDown') { searchFocusIdx.value = Math.min(searchView.value.items.length - 1, searchFocusIdx.value + 1); e.preventDefault() }
  else if (e.key === 'ArrowUp') { searchFocusIdx.value = Math.max(0, searchFocusIdx.value - 1); e.preventDefault() }
  else if (e.key === 'Escape') { searchOpen.value = false; e.stopPropagation() }
}
function onResultsScroll(e: Event) {
  const el = e.target as HTMLElement
  if (el.scrollTop + el.clientHeight >= el.scrollHeight - 8 && searchView.value.items.length < searchView.value.total) searchLimit.value += 20
}
/** 定位：被筛选隐藏时显式打开其类型（不改关系筛选），邻域外先退出邻域；缩放上限 1.6。 */
function locateSearchHit(hit: SearchItem) {
  openInspector(true)
  if (scope.value && !visibleIds.value.has(hit.id)) exitScope(true)
  if (filters.kinds[hit.kind] === false) filters.kinds[hit.kind] = true
  applyFiltersToCy()
  selection.value = [hit.id]; selectedEdgeId.value = ''
  applySelectionToCy()
  const c = cy.value
  const el = c?.getElementById(hit.id)
  if (c && el && el.nonempty()) {
    const targetZoom = Math.max(c.zoom(), 1) > 1.6 ? 1.6 : Math.max(c.zoom(), 1)
    c.animate({ center: { eles: el }, zoom: targetZoom, duration: 260 })
  }
  searchOpen.value = false
  void nextTick(() => searchEl.value?.blur())
}
function toggleScope() {
  if (scope.value) { // 以此为中心
    const id = selection.value[0]
    if (!id) return
    scope.value = { ...scope.value, center: id }
    afterScopeChange()
    return
  }
  if (selection.value.length !== 1) return
  scopeBackup = { positions: livePositions(), viewport: vp(), selection: [...selection.value], edgeId: selectedEdgeId.value, undo: undoStack, redo: redoStack }
  undoStack = []; redoStack = []; syncHistoryFlags()
  scope.value = { center: selection.value[0], depth: 1, direction: 'both', localLayout: 'original' }
  afterScopeChange()
}
function setScopeDepth(d: 1 | 2 | 99) { if (!scope.value) return; scope.value = { ...scope.value, depth: d }; afterScopeChange() }
function setScopeDirection(dir: 'both' | 'in' | 'out') { if (!scope.value) return; scope.value = { ...scope.value, direction: dir }; afterScopeChange() }
function setScopeLayout(style: 'original' | 'circle') { if (!scope.value) return; scope.value = { ...scope.value, localLayout: style }; afterScopeChange() }
function afterScopeChange() {
  applyFiltersToCy()
  if (scope.value?.localLayout === 'circle') applyScopeLayout()
  else if (scopeBackup) { applyPositions(scopeBackup.positions); restoreViewport(scopeBackup.viewport) }
  fitVisible(false)
  saveViewDebounced()
  selection.value = selection.value.filter(id => visibleIds.value.has(id))
  applySelectionToCy()
}
function applyScopeLayout() {
  if (!scope.value) return
  applyPositions(layoutCircle(model.value, visibleIds.value, scope.value.center))
  const c = cy.value
  if (c) { const el = c.getElementById(scope.value.center); if (el.nonempty()) { c.zoom(Math.min(1.2, c.zoom())); c.center(el) } }
}
function exitScope(silent = false) {
  const backup = scopeBackup
  scope.value = null; scopeBackup = null
  if (backup) {
    applyPositions(backup.positions); restoreViewport(backup.viewport)
    selection.value = backup.selection.filter(id => nodesIndex.value.has(id)); selectedEdgeId.value = backup.edgeId
    undoStack = backup.undo; redoStack = backup.redo; syncHistoryFlags()
  }
  applyFiltersToCy(); applySelectionToCy(); saveViewDebounced()
  if (!silent) notify('已退出关联查看，位置与选择已恢复。')
}

// ── 布局：整理 / 撤销 / 重置 ────────────────────────────────────────────
function applyPositions(positions: Record<string, Position>) {
  const c = cy.value
  if (!c) return
  c.batch(() => { for (const [id, p] of Object.entries(positions)) { const el = c.getElementById(id); if (el.nonempty()) el.position(p) } })
  positionsCache = { ...positionsCache, ...positions }
}
function restoreViewport(v: { zoom: number; pan: { x: number; y: number } }) { const c = cy.value; if (!c) return; viewportIntent++; c.zoom(clampZoom(v.zoom)); c.pan(v.pan) }
function currentStep(label: string): LayoutStep { return { positions: scope.value ? { ...livePositions() } : livePositions(), viewport: vp(), label } }
function pushHistory(step: LayoutStep) {
  undoStack.push(step); if (undoStack.length > HISTORY_LIMIT) undoStack.shift(); redoStack = []
  syncHistoryFlags(); saveViewDebounced()
}
function arrange() {
  if (!cy.value) return
  const before = currentStep('整理布局')
  const ids = visibleIds.value
  const positions = arrangeKind.value === 'types' ? layoutByType(model.value, ids) : layoutByComponents(model.value, ids)
  if (!Object.keys(positions).length) return
  pushHistory(before)
  applyPositions(positions)
  fitVisible(false)
  notify(scope.value ? '已整理当前范围内的节点（临时布局，退出邻域后恢复）。' : '已整理当前可见节点，可撤销；不修改业务定义。')
}
function undoLayout() {
  const c = cy.value
  if (!c || !undoStack.length) return
  const step = undoStack.pop() as LayoutStep
  redoStack.push({ positions: livePositions(), viewport: vp(), label: step.label })
  if (redoStack.length > HISTORY_LIMIT) redoStack.shift()
  applyPositions(step.positions); restoreViewport(step.viewport); syncHistoryFlags(); saveViewDebounced()
}
function redoLayout() {
  const c = cy.value
  if (!c || !redoStack.length) return
  const step = redoStack.pop() as LayoutStep
  undoStack.push({ positions: livePositions(), viewport: vp(), label: step.label })
  if (undoStack.length > HISTORY_LIMIT) undoStack.shift()
  applyPositions(step.positions); restoreViewport(step.viewport); syncHistoryFlags(); saveViewDebounced()
}
function resetPositions() {
  if (!cy.value) return
  pushHistory(currentStep('重置位置'))
  const ids = new Set(model.value.nodes.map(n => n.id))
  applyPositions(defaultLayout(model.value, ids))
  if (prefKey) saveViewNow()   // 重置位置 = 清除本机记忆的拖动位置
  fitVisible(false)
  notify('已重置全部节点位置（可撤销）。')
}
function showAll() {
  if (scope.value) exitScope(true)
  for (const k of NODE_KINDS) filters.kinds[k] = true
  for (const r of relationList.value) filters.relations[r] = true
  q.value = ''; searchOpen.value = false; searchLimit.value = 20
  clearSelection()
  applyFiltersToCy(); applySelectionToCy(); fitVisible(false); saveViewDebounced()
}

// ── 缩放 / 适应 / 定位 ──────────────────────────────────────────────────
function updateZoomPct() { zoomPct.value = cy.value ? Math.round(cy.value.zoom() * 100) : 100 }
function setZoomLevel(level: number, renderedPosition?: { x: number; y: number }) {
  const c = cy.value
  if (!c) return
  const z = clampZoom(level)
  const pos = renderedPosition || { x: c.width() / 2, y: c.height() / 2 }
  const model = { x: (pos.x - c.pan().x) / c.zoom(), y: (pos.y - c.pan().y) / c.zoom() }
  c.zoom({ level: z, renderedPosition: { x: pos.x, y: pos.y } })
  const pan = { x: pos.x - model.x * z, y: pos.y - model.y * z }
  viewportIntent++
  c.pan(pan)
  updateZoomPct(); saveViewDebounced()
}
function zoomBy(factor: number) { const c = cy.value; if (c) setZoomLevel(c.zoom() * factor) }
function fitVisible(animate = true) {
  const c = cy.value
  if (!c) return
  const vis = c.nodes().filter((n: any) => n.visible())
  if (!vis.length) { updateZoomPct(); return }
  viewportIntent++
  try { animate ? c.animate({ fit: { eles: vis, padding: 60 }, duration: 260 }) : c.fit(vis, 60) } catch { /* 空集合或退化边界时保持原视口 */ }
  updateZoomPct(); saveViewDebounced()
}
function fitSelection() {
  const c = cy.value
  if (!c) return
  if (!selection.value.length) { fitVisible(); return }
  const eles = c.collection()
  for (const id of selection.value) { const el = c.getElementById(id); if (el.nonempty()) eles.merge(el) }
  if (!eles.length) return
  const maxZoom = 1.6
  const bb = eles.boundingBox()
  const pad = 90
  const level = clampZoom(Math.min(maxZoom, Math.min((c.width() - pad * 2) / Math.max(1, bb.w), (c.height() - pad * 2) / Math.max(1, bb.h))))
  viewportIntent++
  c.animate({ center: { eles }, zoom: level, duration: 260 })
  updateZoomPct(); saveViewDebounced()
}

// ── 指针：选择模式框选、移动模式平移、Space 临时平移 ─────────────────────
function stagePoint(e: PointerEvent) { const r = stageEl.value?.getBoundingClientRect(); return { x: e.clientX - (r?.left || 0), y: e.clientY - (r?.top || 0) } }
function onStagePointerDown(e: PointerEvent) {
  if (e.button !== 0) return
  const c = cy.value
  if (!c) return
  stageEl.value?.focus()
  const boxMode = !spaceHeld && (tool.value === 'select' ? true : e.shiftKey)
  if (!boxMode) return
  if (tool.value === 'pan' || spaceHeld) c.userPanningEnabled(false)
  const p = stagePoint(e)
  dragStart = { x: p.x, y: p.y, add: e.shiftKey, moved: false }
  dragRect.visible = false
  try { stageEl.value?.setPointerCapture(e.pointerId) } catch { /* 容器已卸载 */ }
}
function onStagePointerMove(e: PointerEvent) {
  if (!dragStart) return
  const p = stagePoint(e)
  const dx = p.x - dragStart.x, dy = p.y - dragStart.y
  if (!dragStart.moved && Math.hypot(dx, dy) > 3) dragStart.moved = true
  if (!dragStart.moved) return
  dragRect.visible = true
  dragRect.x = Math.min(p.x, dragStart.x); dragRect.y = Math.min(p.y, dragStart.y)
  dragRect.w = Math.abs(dx); dragRect.h = Math.abs(dy)
}
function onStagePointerUp(e: PointerEvent) {
  const c = cy.value
  const start = dragStart
  dragStart = null
  if (c) c.userPanningEnabled(tool.value === 'pan' || spaceHeld)
  if (!start || !c) { dragRect.visible = false; return }
  const boxed = dragRect.visible
  dragRect.visible = false
  if (!boxed) return
  const x0 = Math.min(dragRect.x, dragRect.x + dragRect.w), x1 = Math.max(dragRect.x, dragRect.x + dragRect.w)
  const y0 = Math.min(dragRect.y, dragRect.y + dragRect.h), y1 = Math.max(dragRect.y, dragRect.y + dragRect.h)
  const hit: string[] = []
  const zoom = c.zoom(), pan = c.pan()
  c.nodes().forEach((n: any) => {
    if (!n.visible()) return
    const p = n.position()
    const np = { x: p.x * zoom + pan.x, y: p.y * zoom + pan.y }
    if (np.x >= x0 && np.x <= x1 && np.y >= y0 && np.y <= y1) hit.push(n.id())
  })
  selection.value = start.add ? [...new Set([...selection.value, ...hit])] : hit
  selectedEdgeId.value = ''
  applySelectionToCy()
  if (selection.value.length) openInspector(true)
  saveViewDebounced()
}
function setTool(t: 'select' | 'pan') { tool.value = t; const c = cy.value; if (c) c.userPanningEnabled(t === 'pan' || spaceHeld) }

// ── 键盘（仅画布上下文；输入控件全部让给文本编辑） ──────────────────────
function inCanvasContext(e: KeyboardEvent): boolean {
  const root = rootEl.value
  if (!root) return false
  const target = e.target as HTMLElement | null
  const active = document.activeElement as HTMLElement | null
  return !!((target && root.contains(target)) || (active && root.contains(active)))
}
function typingIn(e: KeyboardEvent): boolean {
  const path: any[] = (e as any).composedPath ? (e as any).composedPath() : [e.target]
  for (const node of path) {
    if (!(node instanceof HTMLElement)) continue
    const tag = node.tagName
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true
    if (node.isContentEditable) return true
    if (node.closest?.('[data-code-editor], .code-editor, [role="textbox"]')) return true
  }
  return false
}
function onStageKeydown(e: KeyboardEvent) {
  if (typingIn(e)) return
  const inCanvas = inCanvasContext(e) || e.target === stageEl.value
  if (!inCanvas) return
  const mod = e.metaKey || e.ctrlKey
  if (mod && e.key.toLowerCase() === 'z') {
    e.preventDefault(); e.stopPropagation()
    if (e.shiftKey) redoLayout(); else undoLayout()
    return
  }
  if (mod || e.altKey) return
  const key = e.key
  if (key === ' ') { e.preventDefault(); spaceHeld = true; const c = cy.value; if (c) c.userPanningEnabled(true); return }
  if (key === 'Escape') { escapeLayer(); return }
  if (key === '?') { helpOpen.value = true; e.preventDefault(); return }
  if (key.toLowerCase() === 'f') { e.preventDefault(); e.shiftKey ? fitVisible() : (selection.value.length ? fitSelection() : fitVisible()); return }
  if (key === '+' || key === '=') { e.preventDefault(); zoomBy(1.2); return }
  if (key === '-' || key === '_') { e.preventDefault(); zoomBy(1 / 1.2); return }
}
function onStageKeyup(e: KeyboardEvent) {
  if (e.key === ' ') { spaceHeld = false; if (cy.value) cy.value.userPanningEnabled(tool.value === 'pan') }
}
function escapeLayer() {
  if (helpOpen.value) { helpOpen.value = false; return }
  if (searchOpen.value && q.value) { searchOpen.value = false; return }
  if (scope.value) { exitScope(); return }
  if (selection.value.length || selectedEdgeId.value) { clearSelection(); return }
  if (maximized.value) { toggleMaximize(); return }
}

// ── 面板：拖宽 / 最大化 / 容器尺寸 ──────────────────────────────────────
function startResize(e: MouseEvent) {
  e.preventDefault()
  const startX = e.clientX, startW = panels.inspectorW
  const onMove = (ev: MouseEvent) => { panels.inspectorW = clampInspectorW(startW - (ev.clientX - startX)) }
  const onUp = () => {
    document.removeEventListener('mousemove', onMove); document.removeEventListener('mouseup', onUp)
    document.body.style.cursor = ''; document.body.style.userSelect = ''
    void nextTick(() => onContainerResize())
    saveViewDebounced()
  }
  document.addEventListener('mousemove', onMove); document.addEventListener('mouseup', onUp)
  document.body.style.cursor = 'col-resize'; document.body.style.userSelect = 'none'
}
function toggleMaximize() {
  maximized.value = !maximized.value
  document.body.style.overflow = maximized.value ? 'hidden' : ''
  void nextTick(() => onContainerResize())
}
/** 容器尺寸变化（拖宽/收起面板/最大化）：resize 后把模型中心重新对齐到新视口中心，避免画布跳动。 */
function onContainerResize() {
  const c = cy.value
  const stage = stageEl.value
  if (!c || !stage) return
  const w = stage.clientWidth, h = stage.clientHeight
  if (w <= 0 || h <= 0) return
  const oldW = c.width(), oldH = c.height(), zoom = c.zoom(), oldPan = c.pan()
  const center = { x: (oldW / 2 - oldPan.x) / zoom, y: (oldH / 2 - oldPan.y) / zoom }
  c.resize()
  if (viewportIntent > 0) { viewportIntent--; saveViewDebounced(); return }   // 刚显式设置过视口：不再叠加补偿
  if (oldW > 0 && oldH > 0) c.pan({ x: w / 2 - center.x * zoom, y: h / 2 - center.y * zoom })
  saveViewDebounced()
}
// 断点（需求 §4）：>1280 三栏可见；1100–1280 筛选默认收起（三栏仍可展开）；
// ≤1100 两侧为覆盖抽屉（互斥，CSS 同步 1100）。
function updateWidthTier() {
  const w = window.innerWidth
  widthTier.value = w > 1280 ? 'wide' : w > 1100 ? 'mid' : 'narrow'
}

// ── 生命周期 ────────────────────────────────────────────────────────────
function onVisibilityChange() { if (document.visibilityState === 'hidden') flushView() }
onMounted(() => {
  updateWidthTier()
  initCy()
  window.addEventListener('keydown', onStageKeydown, true)
  window.addEventListener('keyup', onStageKeyup, true)
  window.addEventListener('resize', updateWidthTier)
  window.addEventListener('beforeunload', flushView)
  window.addEventListener('pagehide', flushView)
  document.addEventListener('visibilitychange', onVisibilityChange)
  document.addEventListener('pointerdown', onDocPointerDown, true)
  // 首次进入的默认面板：≤1280 收起筛选（已有本机记忆时尊重记忆）
  if (panels.filters && widthTier.value !== 'wide' && !cacheRaw) panels.filters = false
  // 从定义页返回（图谱视图重新挂载）：按目标业务 ID 定位
  if (props.focusDomainId) void nextTick(() => focusDomain(props.focusDomainId as string))
})
let lastOntologyId = ''
watch(() => props.ontologyId, (id) => {
  const next = cacheKeyFor(id || props.state?.workspaceId || '')
  if (next === lastOntologyId) return
  flushView()
  lastOntologyId = next
  prefKey = next
  cacheRaw = null
  undoStack = []; redoStack = []; syncHistoryFlags()
  scope.value = null; scopeBackup = null
  selection.value = []; selectedEdgeId.value = ''
}, { immediate: true })
watch(() => props.state?.workspaceId, (id) => {
  if (!props.ontologyId && id) { flushView(); prefKey = cacheKeyFor(id); cacheRaw = null }
})
watch(signature, () => syncModel())
watch([() => filters.kinds, () => filters.relations], () => { applyFiltersToCy(); saveViewDebounced() }, { deep: true })
watch(q, () => { searchLimit.value = 20; searchFocusIdx.value = -1; applySearchHighlight() })
watch([panels], () => saveViewDebounced(), { deep: true })
watch([selection, selectedEdgeId], () => saveViewDebounced())
watch(widthTier, (t) => { if (t === 'narrow' && panels.filters && panels.inspector) panels.filters = false; void nextTick(() => onContainerResize()) })
// 点击画布外部关闭搜索结果
function onDocPointerDown(e: PointerEvent) {
  const root = rootEl.value
  if (root && e.target instanceof Node && !root.contains(e.target)) searchOpen.value = false
}

onBeforeUnmount(() => {
  flushView()
  document.body.style.overflow = ''
  window.removeEventListener('keydown', onStageKeydown, true)
  window.removeEventListener('keyup', onStageKeyup, true)
  window.removeEventListener('resize', updateWidthTier)
  window.removeEventListener('beforeunload', flushView)
  window.removeEventListener('pagehide', flushView)
  document.removeEventListener('visibilitychange', onVisibilityChange)
  document.removeEventListener('pointerdown', onDocPointerDown, true)
  clearTimeout(saveTimer); clearTimeout(noticeTimer)
  resizeObserver?.disconnect(); resizeObserver = null
  if (cy.value) { cy.value.destroy(); cy.value = null }
})

defineExpose({
  // 供父级/测试观察的只读入口（不暴露业务写能力）
  model, filters, panels, scope, selection,
})
</script>

<style scoped>
.og-app{height:calc(100vh - 214px);min-height:520px;display:grid;grid-template-rows:auto auto minmax(0,1fr);background:#fff;border:1px solid var(--line);border-radius:var(--r-md);overflow:hidden;position:relative}
/* 网格行显式指派：邻域状态条是条件渲染的，缺行时 .og-body 会被自动放进 auto 行而塌成 0 高 */
.og-tools{grid-row:1}.og-scope{grid-row:2}.og-body{grid-row:3}
.og-app.og-max{position:fixed;inset:0;height:100vh;min-height:0;border-radius:0;z-index:60}
.og-tools{display:flex;align-items:center;gap:8px;padding:10px 12px;border-bottom:1px solid var(--line);background:#fff;flex-wrap:wrap}
.og-toolgroup{display:inline-flex;align-items:center;gap:4px}
.og-grow{flex:1;min-width:0}
.og-tools select{width:auto;min-width:118px;margin:0;padding:6px 8px;font-size:13px}
.og-tools button{font-size:13px;padding:6px 11px}
.og-tools button.active{color:var(--blue);background:var(--blue-soft);border-color:var(--blue-line);font-weight:600}
.og-search{position:relative;display:flex;align-items:center;gap:8px;flex:1;min-width:210px;max-width:360px;border:1px solid var(--line-2);border-radius:var(--r-pill);padding:6px 12px;background:var(--bg)}
.og-search svg{width:15px;height:15px;color:#8aa;flex:none}
.og-search input{display:block;width:100%;min-width:0;margin:0;padding:0;border:0;background:transparent;font-size:13px}
.og-search input:focus{outline:none}
.og-x{margin:0;padding:0 3px;border:0;background:none;font-size:15px;color:#8aa}
.og-results{position:absolute;top:calc(100% + 8px);left:0;right:0;z-index:9;background:#fff;border:1px solid var(--line);border-radius:var(--r-md);box-shadow:var(--shadow-2);max-height:280px;overflow:auto;padding:4px}
.og-results-head{margin:6px 8px;font-size:12px;color:var(--muted)}
.og-results button{display:block;width:100%;text-align:left;border:0;border-radius:var(--r-sm);background:none;padding:8px 10px}
.og-results button:hover,.og-results button.active{background:var(--blue-soft)}
.og-results b{font-size:13.5px;font-weight:600}
.og-results small{display:block;font-size:12px;color:var(--muted)}
.og-hidden-tag{display:block;font-size:11.5px;color:var(--warn);font-style:normal;margin-top:2px}
.og-more{border-top:1px solid var(--line);border-radius:0;color:var(--blue)}
.og-scope{display:flex;align-items:center;gap:12px;flex-wrap:wrap;padding:8px 14px;background:var(--blue-soft);border-bottom:1px solid var(--blue-line);font-size:13px}
.og-scope b{color:var(--blue-ink)}
.og-inline{display:inline-flex;align-items:center;gap:6px;margin:0;font-size:12px;color:var(--muted)}
.og-inline select{width:auto;margin:0;padding:4px 7px;font-size:12px}
.og-scope-note{color:var(--muted);font-size:12px}
.og-body{display:grid;grid-template-columns:auto minmax(0,1fr) auto;min-height:0;position:relative}
/* display:none 的侧栏不生成网格盒，自动放置会把画布塞进 0 宽的 auto 列：显式指派列 */
.og-filters{grid-column:1}.og-center{grid-column:2}.og-inspector{grid-column:3}
.og-side{background:#fafbfb;border-right:1px solid var(--line);overflow:auto;padding:14px 12px;min-width:0;width:224px}
.og-inspector{border-right:0;border-left:1px solid var(--line);background:#fff;position:relative;overflow:auto}
.og-side.og-overlay{position:absolute;top:0;bottom:0;z-index:8;box-shadow:var(--shadow-2);background:#fff}
.og-filters.og-overlay,.og-narrow .og-filters{left:0}
.og-narrow .og-inspector{right:0}
.og-panel-head{margin-bottom:12px}
.og-head-row{display:flex;align-items:center;justify-content:space-between;gap:8px}
.og-eyebrow{display:block;font-size:10px;letter-spacing:1.6px;color:#9ab;font-weight:700}
.og-panel-head h3{margin:2px 0 0;font-size:14px}
.og-section{margin-bottom:16px}
.og-section h4{margin:0 0 6px;font-size:12px;color:#576d82}
.og-check{display:flex;align-items:center;gap:7px;padding:3px 0;font-size:12.5px;cursor:pointer;margin:0;color:var(--ink)}
.og-check input{display:inline-block;width:14px;height:14px;min-width:14px;margin:0;padding:0;accent-color:var(--blue)}
.og-swatch{width:11px;height:11px;border-radius:3px;flex:none}
.og-count{margin-left:auto;color:#9ab;font-variant-numeric:tabular-nums}
.og-relname{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:120px}
.og-note{margin:0;font-size:12px;color:#708198;line-height:1.7;overflow-wrap:anywhere}
.og-dot{display:inline-block;width:10px;height:10px;border-radius:50%;border:1.6px solid;margin:0 4px 0 0;vertical-align:-1px}
.og-legend{margin:0 10px 0 0;font-size:12px;color:#708198}
.og-dangling{width:100%;text-align:left;font-size:12.5px;color:var(--warn);background:var(--warn-soft);border-color:var(--warn-line);padding:6px 9px}
.og-dangling-list{margin-top:6px}
.og-dangling-list p{padding:4px 0;border-bottom:1px dashed var(--line)}
.og-mini{font-size:11.5px;padding:2px 7px;margin-left:6px}
.og-side-actions{display:grid;gap:6px;margin-top:14px}
.og-center{display:grid;min-width:0;position:relative}
.og-stage{position:relative;min-height:0;outline:none;background-color:var(--bg);background-image:radial-gradient(var(--grid) 1px,transparent 1px);background-size:20px 20px;overflow:hidden}
.og-stage:focus-visible{box-shadow:inset 0 0 0 2px var(--blue-line)}
.og-canvas{position:absolute;inset:0}
.og-rubber{position:absolute;border:1px dashed var(--blue);background:rgba(36,92,223,.08);pointer-events:none;z-index:6}
.og-stats{position:absolute;top:10px;left:12px;background:#ffffffd9;padding:5px 9px;border-radius:var(--r-sm);font-size:12px;color:var(--ink-2);pointer-events:none;font-variant-numeric:tabular-nums}
.og-empty-hint{position:absolute;top:42%;width:100%;text-align:center;color:var(--muted);pointer-events:none;font-size:14px}
.og-empty-hint small{display:block;margin-top:6px;font-size:12px;color:var(--faint)}
.og-edge-tip{position:absolute;transform:translate(0,-50%);background:#20364bee;color:#fff;border-radius:6px;padding:4px 9px;font-size:11.5px;pointer-events:none;z-index:7;white-space:nowrap}
.og-hint{position:absolute;bottom:14px;left:14px;font-size:11px;color:var(--faint);pointer-events:none}
.og-zoom{position:absolute;bottom:14px;right:14px;display:flex;gap:3px;background:#fff;border:1px solid var(--line);padding:3px;border-radius:8px;box-shadow:var(--shadow-1)}
.og-zoom button{border:0;padding:6px 9px;font-size:12px;background:none}
.og-zoom button:hover{background:var(--paper-2);color:var(--blue)}
.og-resizer{position:absolute;left:0;top:0;bottom:0;width:5px;cursor:col-resize;background:transparent;z-index:5}
.og-resizer:hover{background:var(--blue-soft)}
.og-empty{display:grid;gap:6px;font-size:13px;color:#708198;padding:6px 2px}
.og-details{padding:2px}
.og-hero{padding:10px 12px;background:var(--bg);border:1px solid var(--line);border-radius:8px;margin-bottom:12px}
.og-hero h3{margin:4px 0 0;font-size:15px;overflow-wrap:anywhere}
.og-hero-kicker{display:flex;align-items:center;gap:6px;font-size:11px;color:#576d82}
.og-hero-kicker i{width:10px;height:10px;border-radius:3px}
.og-tag{border:1px solid var(--line);border-radius:4px;padding:1px 6px;background:#fff}
.og-dsec{margin-bottom:14px}
.og-dsec h4{margin:0 0 6px;font-size:12px;color:#576d82}
.og-field{padding:5px 0;border-bottom:1px dashed var(--line)}
.og-flabel{font-size:11px;color:#9ab;margin-bottom:1px}
.og-fvalue{font-size:13px;color:var(--ink);white-space:pre-wrap;overflow-wrap:anywhere}
.og-rellist{display:grid;gap:5px}
.og-relbtn{display:flex;justify-content:space-between;gap:8px;text-align:left;font-size:12.5px;padding:6px 9px}
.og-relbtn small{color:var(--muted)}
.og-relbtn.active{border-color:var(--blue-line);background:var(--blue-soft);color:var(--blue-ink)}
.og-actions-col{display:flex;gap:6px;flex-wrap:wrap;margin:12px 0}
.og-modal{position:absolute;inset:0;background:var(--backdrop);z-index:20;display:grid;place-items:center}
.og-modal-card{background:#fff;border-radius:var(--r-lg);padding:24px;width:min(560px,92%);box-shadow:var(--shadow-2)}
.og-modal-card h3{margin-bottom:10px}
.og-modal-card p{margin:8px 0;font-size:13.5px;line-height:1.8}
.og-dialogtools{display:flex;justify-content:flex-end;margin-top:16px}
.og-toast{position:absolute;left:50%;bottom:26px;transform:translateX(-50%);background:#20364b;color:#fff;border-radius:8px;padding:9px 16px;font-size:12.5px;z-index:30;box-shadow:var(--shadow-2);margin:0}
@media(max-width:1200px){.og-search{max-width:280px}}
@media(max-width:1100px){.og-side{position:absolute;top:0;bottom:0;z-index:8;box-shadow:var(--shadow-2);background:#fff;width:230px}.og-filters{left:0}.og-inspector{right:0}}
@media(max-width:860px){.og-tools{padding:8px}.og-search{order:1;flex-basis:100%;max-width:none}.og-arrange select{min-width:96px}.og-hint{display:none}.og-app{min-height:420px}}
</style>
