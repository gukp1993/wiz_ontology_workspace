<!-- EditorView —— 自旧仓库 views/EditorView.vue（HEAD b2f10d1）整体复制后适配（需求 20260919_图谱编辑器源码整体复用）。
     交互核心（画布事件/连线面板/搜索/邻域/自动整理/撤销重做/快捷键/边悬浮）逐行保留；适配仅限数据边界：
     · useGraph/api/auth/router → legacyBridge（当前本体草稿投影 + 领域命令 + 账号隔离偏好）
     · 保存=当前草稿协调器（inject commit-now）；版本管理→「校验与发布」页；发布版本只读预览
     · 旧 MCP/问数/登录徽标/另存版本/jsonId 导入等外围入口移除（登记于开发计划 §5 能力对照表）
     · 节点/边身份 = lg:<类型前缀>:<领域稳定ID>；坐标/视口是视图状态，不产生业务保存
     · C01：applyGraphState 边端点变化用 el.move；C04：键盘单监听消费即截断；C05：无自定义指针捕获 -->
<template>
  <div ref="rootEl" class="legacy-editor-root" :class="{ max: maximized }">
  <PreviewView v-if="viewMode === 'preview'" :key="'pv-' + previewKey" :ontology-id="ontologyId" :version="previewVersion" @back="viewMode = 'editor'"/>
  <MultiPreviewView v-else-if="viewMode === 'multi'" :key="'mpv-' + previewKey" :items="multiItems" @back="viewMode = 'editor'" @repick="showMultiPick = true"/>
  <div v-if="previewError" class="preview-error" role="alert">
    <strong>预览加载失败</strong>
    <p>{{ previewError }}</p>
    <div class="pe-actions">
      <button class="btn" @click="previewError = ''">返回编辑</button>
      <button class="btn primary" @click="previewError = ''; previewKey++; goPreview()">重试</button>
    </div>
  </div>
  <div v-else class="editor">
    <header class="topbar">
      <div class="brand">

        <h1>本体图谱</h1>
        <span class="brand-sub">{{ bridgeState.graphName || '未选择本体' }}</span>
      </div>

      <div class="body">
        <!-- 画布工具行（2026-09-20 排版优化：单行 + 状态右对齐，把高度让给画布） -->
        <div class="tool-row" v-if="hasGraph">
          <button class="btn" title="整理节点布局：按类型分组排列（效果满意再保存）· 快捷键无需记忆，随时可用「撤销整理」还原" @click="autoLayout">
            <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M8 2v12M2 8h12M4.5 4.5l7 7M11.5 4.5l-7 7" /></svg>
            <span>整理节点</span>
          </button>
          <!-- 类型筛选：点击显示/隐藏该类型全部节点（仅影响显示，保存/导出仍含全部；选择会记忆） -->
          <div class="type-filter">
            <button v-for="(color, t) in TYPE_COLOR" :key="t" class="chip" :class="{ off: !typeVisible[t] }"
              :title="typeVisible[t] ? `隐藏「${t}」节点（共 ${typeCounts[t]} 个，仅隐藏不删除）` : `显示「${t}」节点（共 ${typeCounts[t]} 个）`"
              @click="toggleType(t)">
              <span class="dot" :style="{ background: typeVisible[t] ? color : '#b6c2c6' }"></span>
              <span>{{ t }}</span><b>{{ typeCounts[t] }}</b>
            </button>
          </div>
          <!-- 节点搜索：实时匹配名称，仅高亮匹配节点，不过滤画布 -->
          <div class="search-box">
            <svg fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"></circle><path d="m20 20-3.4-3.4"></path></svg>
            <input v-model="searchQ" type="text" placeholder="搜索节点名称…" autocomplete="off" @input="applySearch" @keydown.enter.prevent="focusSearchAt(0)" @keydown.tab.prevent="focusSearchAt(searchFocusIdx + 1)" />
            <span v-if="searchQ && searchHits > 0" class="search-count" :title="searchHits > 1 ? '点击定位到首个命中节点，Tab 循环' : '点击定位到命中节点'" @click="focusSearchAt(0)">{{ searchHits }} 个匹配</span>
            <button v-if="searchQ" class="search-clear" title="清除搜索" @click="clearSearch">×</button>
          </div>
          <!-- 视图操作簇（2026-09-20 用户反馈「选中节点后工具栏样式有问题」）：
               状态 + 全图/详情/1跳/2跳/最大化 合成一组整体靠右；工具行放不下时这一组整体换行，
               不再被挤出视口右端裁切（此前选中节点出现 1跳/2跳 后「最大化」会被切掉）。 -->
          <div class="tool-right">
            <!-- 回到全图视图：常驻显示，F 放大聚焦后可随时一键退出 -->
            <div class="status">
              <span class="count">节点 <b>{{ nodeCount }}</b> · 连线 <b>{{ edgeCount }}</b></span>
              <span class="save" :class="{ unsaved: saveDotClass === 'unsaved' }"><span class="dot" :class="saveDotClass"></span>{{ saveText }}</span>
            </div>
            <button class="btn inspector-fit-all" title="回到全图视图（清除聚焦定位，退出放大）" @click="fitAllGraph">
              <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M2 6V2h4M10 2h4v4M14 10v4h-4M6 14H2v-4" /></svg>
              <span>全图</span>
            </button>
            <!-- 详情面板开关：默认隐藏，点击在最右侧展开/收起 -->
            <button class="btn inspector-toggle" :class="{ active: inspOpen }" title="打开/收起右侧详情面板" @click="toggleInspector">
              <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="2.5" width="12" height="11" rx="1.5"></rect><path d="M2 6h12M7 6v7.5"></path></svg>
              <span>详情</span>
            </button>
            <!-- 邻域高亮（右侧）：选中单个节点后按跳数聚焦，其余节点半透明 -->
            <template v-if="singleNodeSelected">
              <button class="btn" :class="{ active: hopMode === 1 }" title="1 跳邻域：只高亮该节点 1 跳内可达的节点，其余半透明（再点取消）" @click="setHop(hopMode === 1 ? 0 : 1)">1跳</button>
              <button class="btn" :class="{ active: hopMode === 2 }" title="2 跳邻域：高亮 2 跳内可达节点（当前工作台补接）" @click="setHop(hopMode === 2 ? 0 : 2)">2跳</button>
            </template>
            <button class="btn inspector-max" :class="{ active: maximized }" :title="maximized ? '退出最大化' : '最大化画布'" @click="maximized = !maximized">
              <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M2 6V2h4M10 2h4v4M14 10v4h-4M6 14H2v-4" /></svg>
              <span>{{ maximized ? '还原' : '最大化' }}</span>
            </button>
          </div>
        </div>
      </div>

    </header>

    <div class="wrap">
      <div ref="canvasEl" class="canvas"></div>
      <aside v-if="inspOpen" class="inspector">
        <div class="panel-head"><span class="eyebrow">INSPECTOR</span><h2>节点详情</h2></div>

        <!-- 未选中：常驻空提示 -->
        <div v-if="!insp" class="empty">
          <div><strong>选中节点查看详情</strong>单击节点看详情字段与直接关系，双击或点「编辑」跳转到对应定义页编辑。<br/>连线不在此编辑：单击连线跳转到它的来源定义（对象链接→链接页签，属性引用→属性页签，规则/动作关联→对应页签）。选中节点按 <b>F</b> 放大聚焦、<b>Shift+F</b> 缩小。</div>
        </div>

        <!-- 节点详情（唯一的详情形态；连线编辑一律跳转源端，不再有面板） -->
        <template v-else>
          <div class="details">
            <div class="detail-hero">
              <div class="detail-kicker">
                <i class="detail-color" :style="{ '--c': TYPE_COLOR[insp.type] }"></i>
                <span>{{ insp.type }}节点</span>
              </div>
              <h3>{{ insp.name }}</h3>
            </div>
            <div v-if="inspEntries.length" class="detail-section">
              <h4>详情字段</h4>
              <div v-for="f in inspEntries" :key="f.key" class="field-row">
                <div class="f-label">{{ f.label }}</div>
                <div class="f-value">
                  <template v-if="f.isList"><div v-for="(item, i) in f.value" :key="i" class="line">{{ item }}</div></template>
                  <span v-else>{{ f.value }}</span>
                </div>
              </div>
            </div>
            <div class="detail-section">
              <h4>直接关系（{{ inspRelations.length }}）</h4>
              <div v-if="inspRelations.length" class="mini-rel-list">
                <span v-for="r in inspRelations" :key="r.id" class="mini-rel">
                  <template v-if="r.dir === 'out'"><span class="rel">{{ r.label }}</span> → {{ r.name }}</template>
                  <template v-else>{{ r.name }} → <span class="rel">{{ r.label }}</span></template>
                </span>
              </div>
              <p v-else class="note">（无关联连线）</p>
            </div>
            <div class="actions">
              <button class="btn primary" @click="editInspNode">编辑 ↗</button>
            </div>
          </div>
        </template>

      </aside>
    </div>

    <!-- 连线模式面板：画布点击选点之外，支持按名称搜索选点（属性节点多时更顺手） -->
    <div class="link-panel" :class="{ show: linking }">
      <span class="lp-label">源</span>
      <div class="lp-slot">
        <span v-if="linkSource" class="lp-chip" :style="{ borderColor: TYPE_COLOR[linkSource.data('type')] }">
          <i class="lp-dot" :style="{ background: TYPE_COLOR[linkSource.data('type')] }"></i>
          <span class="lp-name">{{ linkSource.data('name') }}</span>
          <button class="lp-x" title="取消选择源节点" @click="clearLinkSource">×</button>
        </span>
        <template v-else>
          <div class="lp-field">
            <input v-model="linkQ.src" type="text" placeholder="搜索源节点名称…" autocomplete="off"
                   @input="onLinkQ('src')" @focus="onLinkQ('src')" @blur="closeLinkHits('src')"
                   @keydown.esc.stop="linkQ.src = ''; linkHits.src = []" />
            <ul v-if="linkHits.src.length" class="lp-list">
              <li v-for="h in linkHits.src" :key="h.id" :title="h.name" @mousedown.prevent="pickLinkNode('src', h.id)">
                <i class="lp-dot" :style="{ background: TYPE_COLOR[h.type] }"></i>
                <span class="lp-name">{{ h.name }}</span><span class="lp-type">{{ h.type }}</span>
              </li>
            </ul>
          </div>
          <div class="lp-types">
            <button :class="{ on: !linkTypeFilter.src }" @click="setLinkTypeFilter('src', '')">全部</button>
            <button v-for="(color, t) in TYPE_COLOR" :key="t" :class="{ on: linkTypeFilter.src === t }"
                    @click="setLinkTypeFilter('src', t)">{{ t }}</button>
          </div>
        </template>
      </div>
      <span class="lp-arrow">→</span>
      <span class="lp-label">目标</span>
      <div class="lp-slot">
        <div class="lp-field">
          <input v-model="linkQ.tgt" type="text" :disabled="!linkSource" placeholder="搜索目标节点（可多选）…" autocomplete="off"
                 @input="onLinkQ('tgt')" @focus="onLinkQ('tgt', true)" @blur="closeLinkHits('tgt')"
                 @keydown.esc.stop="linkQ.tgt = ''; linkHits.tgt = []; linkChecked = []" />
          <ul v-if="linkHits.tgt.length" class="lp-list">
            <li v-for="h in linkHits.tgt" :key="h.id"
                :class="{ on: !h.linked && linkChecked.includes(h.id), off: h.linked }"
                :title="h.linked ? '已与源节点连线，不可重复勾选' : h.name"
                @mousedown.prevent="toggleLinkCheck(h.id)">
              <i class="lp-check" :class="{ on: !h.linked && linkChecked.includes(h.id) }"></i>
              <i class="lp-dot" :style="{ background: TYPE_COLOR[h.type] }"></i>
              <span class="lp-name">{{ h.name }}</span>
              <span class="lp-type" :class="{ linked: h.linked }">{{ h.linked ? '已连线' : h.type }}</span>
            </li>
            <li class="lp-foot">
              <button class="lp-foot-btn" @mousedown.prevent="toggleLinkCheckAll">全选</button>
              <button class="lp-foot-btn primary" :disabled="!linkChecked.length" @mousedown.prevent="addCheckedLinkHits">
                加入所选{{ linkChecked.length ? `（${linkChecked.length}）` : '' }}
              </button>
            </li>
          </ul>
        </div>
        <div class="lp-types">
          <button :disabled="!linkSource" :class="{ on: !linkTypeFilter.tgt }" @click="setLinkTypeFilter('tgt', '')">全部</button>
          <button v-for="(color, t) in TYPE_COLOR" :key="t" :disabled="!linkSource"
                  :class="{ on: linkTypeFilter.tgt === t }" @click="setLinkTypeFilter('tgt', t)">{{ t }}</button>
        </div>
      </div>
      <button class="btn primary lp-confirm" :disabled="!linkSource || !linkTargets.length"
              title="为源节点与全部待连目标创建连线（关系/描述只填一次）" @click="confirmLink">
        连线{{ linkTargets.length ? `（${linkTargets.length}）` : '' }}
      </button>
      <span class="lp-tip">{{ linkHint }}</span>
      <!-- 待连目标列表：多选累积，单个移除/清空，点「连线」一次确认 -->
      <div v-if="linkTargets.length" class="lp-targets">
        <span class="lp-tlabel">待连 {{ linkTargets.length }}：</span>
        <span v-for="t in linkTargets" :key="t.id" class="lp-chip" :style="{ borderColor: TYPE_COLOR[t.type] }">
          <i class="lp-dot" :style="{ background: TYPE_COLOR[t.type] }"></i>
          <span class="lp-name">{{ t.name }}</span>
          <button class="lp-x" title="从待连列表移除" @click="removeLinkTarget(t.id)">×</button>
        </span>
        <button class="lp-clear" title="清空待连列表" @click="clearLinkTargets">清空</button>
      </div>
    </div>

    <!-- 画布角落常驻快捷键提示（不阻挡画布交互） -->
    <div class="shortcut-hint" title="按 ? 查看全部快捷键">F 聚焦 · Shift+F 缩小 · ? 全部快捷键</div>

    <!-- 自动整理后的临时操作条：满意再保存，不满意可撤销 -->
    <div v-if="showLayoutUndo" class="layout-undo">
      <span>已整理节点，检查效果满意后再保存（Ctrl/⌘+S）</span>
      <button class="btn danger" @click="undoLayout">撤销整理</button>
    </div>

    <!-- 快捷键说明弹窗（按 ? 打开） -->
    <div class="modal-mask" :class="{ show: showShortcuts }" @click.self="showShortcuts = false">
      <div class="modal shortcuts">
        <h3>快捷键</h3>
        <table class="shortcut-table">
          <tbody>
            <tr><td><kbd>Ctrl/⌘ + Z</kbd> / <kbd>Ctrl/⌘ + Shift + Z</kbd></td><td>撤销 / 重做画布操作（增删、拖拽、导入）</td></tr>
            <tr><td><kbd>Ctrl/⌘ + S</kbd></td><td>保存当前改动</td></tr>
            <tr><td><kbd>Delete</kbd> / <kbd>Backspace</kbd></td><td>删除选中的节点 / 连线</td></tr>
            <tr><td><kbd>F</kbd></td><td>放大聚焦到选中的节点（可连续按放大）；无选中时从视口中心原地放大</td></tr>
            <tr><td><kbd>Shift + F</kbd></td><td>缩小（缩到全图可见为止）</td></tr>
            <tr><td><kbd>Tab</kbd>（搜索框内）</td><td>循环定位下一个搜索命中的节点</td></tr>
            <tr><td><kbd>Esc</kbd></td><td>取消连线模式 / 关闭弹窗</td></tr>
          </tbody>
        </table>
        <div class="btns"><button class="btn primary" @click="showShortcuts = false">关闭</button></div>
      </div>
    </div>

    <!-- 边 hover tooltip（fixed 定位，跟随边中点） -->
    <div v-if="edgeTip" class="edge-tip" :style="{ left: edgeTipX + 'px', top: edgeTipY + 'px' }">{{ edgeTipText }}</div>

    <NewNodeModal
      :show="showNewNode"
      :existing-names="nodeNames"
      @create="createNode"
      @close="showNewNode = false"
    />
    <NewEdgeModal
      :show="showNewEdge"
      :source-name="edgeDraft.srcName"
      :target-name="edgeDraft.tgtName"
      @create="createEdges"
      @close="showNewEdge = false"
    />
    <GraphModal
      :show="showGraphs"
      :current-id="ontologyId"
      @close="showGraphs = false"
      @switch="id => $emit('switch-ontology', id)"
      @create="name => $emit('create-ontology', name)"
    />
    <MultiPickModal
      :show="showMultiPick"
      :preselect="multiPickPreselect"
      @close="showMultiPick = false"
      @confirm="goMultiPreview"
    />
  </div>
  <TheToast />
  <ModalConfirm />
  </div>
</template>

<script setup>
import { ref, shallowRef, computed, watch, onMounted, onBeforeUnmount, inject, onErrorCaptured } from 'vue'
import cytoscape from 'cytoscape'
import './legacy.css'
import { createLegacyBridge, bridgeSignature } from './legacyBridge'
import { buildJsonIdLocal, jsonIdLosses, buildOfflineBundle, downloadBlob } from './offlineBundle'
import { toast } from './composables/useToast'
import { confirmDialog } from './composables/useConfirm'
import { GRAPH_STYLE } from './shared/graphStyle'
import { TYPE_COLOR, _TYPE_PREFIX } from './shared/constants'
import { fieldEntries } from './shared/fields'
import { nodeW, nodeH } from './shared/layout'
import NewNodeModal from './components/NewNodeModal.vue'
import NewEdgeModal from './components/NewEdgeModal.vue'
import GraphModal from './components/GraphModal.vue'
import MultiPickModal from './components/MultiPickModal.vue'
import PreviewView from './PreviewView.vue'
import MultiPreviewView from './MultiPreviewView.vue'
import TheToast from './components/TheToast.vue'
import ModalConfirm from './components/ModalConfirm.vue'

// ---- 宿主边界（需求 §4 适配层）：数据/保存/导航全部来自当前工作台 ----
const props = defineProps({
  state: { type: Object, required: true },          // 当前本体草稿（App ontologySaver.working）
  ontologyId: { type: String, default: '' },
  saveState: { type: Object, default: () => ({ kind: 'saved', text: '已保存' }) },
  latestRelease: { type: String, default: '' },     // 最新发布版本名（只读预览用）
  focusTarget: { type: String, default: '' },       // 定义页返回定位：lg: 节点/边 ID
})
const emit = defineEmits(['navigate', 'before-change', 'changed', 'switch-ontology', 'create-ontology'])
const commitNow = inject('commit-now', () => {})
const bridge = createLegacyBridge({
  getState: () => props.state,
  ontologyId: props.ontologyId,
  emitBeforeChange: (label) => emit('before-change', typeof label === 'object' && label ? label : { actionLabel: String(label || '图谱编辑') }),
  emitChanged: () => emit('changed'),
})
const bridgeState = bridge.state
// 五类常量：必须在任何初始化即调用的函数（loadTypeVisible 等）之前声明（TDZ 防护）
const ALL_TYPES = ['对象', '共享属性', '私有属性', '规则', '动作']

const canvasEl = ref(null)
const cy = shallowRef(null)
const _seq = 0
let newNodeSeq = 0 // 新建节点错开步进，防止连续新建堆叠在中心
let initializing = false

// 弹窗与 UI 状态
const showNewNode = ref(false)
const showNewEdge = ref(false)
const edgeDraft = ref({ srcId: '', tgtId: '', srcName: '', tgtName: '' })
const showGraphs = ref(false)
// 多图谱预览：选择弹窗（列各本体的发布版本；数据由弹窗自行加载当前账号可见本体）
const showMultiPick = ref(false)
const multiPickPreselect = computed(() => (props.latestRelease ? [props.ontologyId + '@' + props.latestRelease] : []))
function goMultiPreview(ids) {
  showMultiPick.value = false
  if (ids && ids.length) { multiItems.value = ids; viewMode.value = 'multi' }
}
const showShortcuts = ref(false) // 快捷键说明弹窗（按 ? 打开）

const linking = ref(false)
const linkSource = ref(null)
const linkHint = ref('连线模式：先点源节点，再点目标节点')
const nodeCount = ref(0)
const edgeCount = ref(0)
// 类型筛选：一键显示/隐藏某类节点（属性动辄数百个，隐藏后先看实体-规则骨架）；仅影响显示，保存/导出仍含全部节点
const typeCounts = ref({}) // 按类型节点数（含隐藏，供筛选按钮展示）
const typeVisible = ref(loadTypeVisible())
const insp = ref(null) // 常驻 inspector 数据：{kind:'node',...} 或 {kind:'edge',...}；null=未选中
const inspOpen = ref(false) // 详情面板展开状态：默认隐藏，顶栏「详情」按钮切换
const searchQ = ref('') // 节点搜索关键词（仅高亮匹配节点，不过滤画布）
const searchHits = ref(0)
let searchHitIds = [] // 命中节点 id 数组，供 fit 定位 / Tab 循环
let searchFocusIdx = -1 // 当前定位下标（-1 = 未定位，Tab/回车从 0 开始）
const selSummary = ref('') // 当前选中对象动态摘要，如「2 个节点」「1 条连线」
const selNodeCount = ref(0) // 当前选中节点数（邻域按钮显隐用）
const selEdgeCount = ref(0) // 当前选中连线数
const hopMode = ref(0) // 邻域高亮：0=关 1=1跳
const singleNodeSelected = computed(() => selNodeCount.value === 1 && selEdgeCount.value === 0)
let hopOrigin = null // 聚焦重排前的原坐标 {id:{x,y}}
let hopMoved = null // 已重排的节点 collection
let hopCenterId = null // 当前重排中心节点 id
const showLayoutUndo = ref(false) // 自动整理后的「撤销整理」条
let layoutBackup = null // 自动整理前全部节点坐标 {id:{x,y}}
const edgeTip = ref(false) // 边 hover tooltip 显隐
const edgeTipText = ref('')
const edgeTipX = ref(0)
const edgeTipY = ref(0)

const _coordinatesInputEl = ref(null)
// 只读预览 / 最大化 / 多图预览清单（适配层：旧 router 跳转改为组件内切换）
const rootEl = ref(null)
const viewMode = ref('editor') // 'editor' | 'preview' | 'multi'
const previewVersion = ref('')
const previewKey = ref(0)      // 每次进入预览重新挂载（失败重试不留残骸）
const previewError = ref('')   // 预览/多图子视图渲染或加载失败时的可见错误（不是白屏死路）
const multiItems = ref([])
const maximized = ref(false)
// 子视图（预览/多图）出现未捕获错误时：退回编辑器并给出原因，避免整页卡死
onErrorCaptured((err) => {
  previewError.value = String((err && err.message) || err)
  viewMode.value = 'editor'
  return false
})

const hasGraph = computed(() => !!props.ontologyId)
const nodeNames = computed(() => (cy.value ? cy.value.nodes().map((n) => n.data('name')) : []))
const saveText = computed(() => props.saveState?.text || '已保存')
const saveDotClass = computed(() => {
  const k = props.saveState?.kind
  if (k === 'saved') return 'saved'
  if (k === 'dirty' || k === 'form') return 'unsaved'
  if (k === 'error' || k === 'conflict') return 'failed'
  return ''
})

// 常驻 inspector：节点详情字段 + 直接关系（对齐预览页 fieldEntries / relations 心智）
const inspEntries = computed(() => (insp.value?.kind === 'node' ? fieldEntries(insp.value.type, insp.value.data) : []))
const inspRelations = computed(() => {
  if (insp.value?.kind !== 'node' || !cy.value) return []
  const id = insp.value.id
  return cy.value
    .edges()
    .filter((e) => e.source().id() === id || e.target().id() === id)
    .map((e) => {
      const out = e.source().id() === id
      return {
        id: e.id(),
        label: e.data('relation') || '',
        name: (out ? e.target() : e.source()).data('name'),
        dir: out ? 'out' : 'in',
      }
    })
    .sort((a, b) => a.label.localeCompare(b.label))
})

// ---------- 画布 ----------
function centerPos() {
  return {
    x: (cy.value.width() / 2 - cy.value.pan().x) / cy.value.zoom(),
    y: (cy.value.height() / 2 - cy.value.pan().y) / cy.value.zoom(),
  }
}
function elementsFromDraft() {
  const els = []
  bridgeState.draft.nodes.forEach((n) => {
    els.push({
      data: { id: n.id, name: n.name, type: n.type, w: nodeW(n.name), h: nodeH(n.name), data: n.data || {} },
      classes: n.type,
      position: { x: n.x || 0, y: n.y || 0 },
    })
  })
  bridgeState.draft.edges.forEach((e) => {
    els.push({ data: { id: e.id, source: e.source, target: e.target, relation: e.relation, description: e.description, kind: e.kind, domainId: e.domainId } })
  })
  return els
}
function currentDraft() {
  const draft = {
    name: bridgeState.draft.name,
    nodes: cy.value.nodes().map((n) => ({
      id: n.id(),
      type: n.data('type'),
      name: n.data('name'),
      x: Math.round(n.position('x')),
      y: Math.round(n.position('y')),
      data: n.data('data') || {},
    })),
    edges: cy.value.edges().map((e) => ({
      id: e.id(),
      source: e.source().id(),
      target: e.target().id(),
      relation: e.data('relation'),
      description: e.data('description'),
      kind: e.data('kind'),
      domainId: e.data('domainId'),
    })),
  }
  return draft
}
function updateCounts() {
  if (!cy.value) return
  nodeCount.value = cy.value.nodes().length
  edgeCount.value = cy.value.edges().length
  const c = {}
  ALL_TYPES.forEach((t) => { c[t] = 0 })
  cy.value.nodes().forEach((n) => { const t = n.data('type'); if (t in c) c[t]++ })
  typeCounts.value = c
  applyTypeFilter() // 结构变化后重放显隐：隐藏类型下新建/导入/撤销回来的节点一并藏起
}
// 类型显隐记忆走 bridge 偏好（账号+本体隔离），替代旧全局 localStorage key
function loadTypeVisible() {
  const v = bridgeState.typeVisiblePref
  if (v && ALL_TYPES.every((t) => typeof v[t] === 'boolean')) return { ...v }
  const out = {}
  ALL_TYPES.forEach((t) => { out[t] = true })
  return out
}
// 按类型批量显隐：边随端点自动隐藏/恢复（cytoscape 内建），无需单独处理
function applyTypeFilter() {
  if (!cy.value) return
  for (const t of Object.keys(typeVisible.value)) {
    const els = cy.value.nodes().filter((n) => n.data('type') === t)
    if (typeVisible.value[t]) els.show()
    else els.hide()
  }
}
function toggleType(t) {
  typeVisible.value[t] = !typeVisible.value[t]
  bridgeState.typeVisiblePref = { ...typeVisible.value }
  bridge.persistPrefs()
  applyTypeFilter()
  fitAllGraph() // 显隐变化后回到可见部分的全图视野
}
function allNodesStacked() {
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity
  cy.value.nodes().forEach((n) => {
    const p = n.position()
    minX = Math.min(minX, p.x)
    minY = Math.min(minY, p.y)
    maxX = Math.max(maxX, p.x)
    maxY = Math.max(maxY, p.y)
  })
  return (maxX - minX) + (maxY - minY) < 5
}
function applyColumnLayout() {
  const TYPE_ORDER = ALL_TYPES
  const V_GAP = 30
  const nodes = cy.value.nodes().filter((n) => n.visible()) // 隐藏类型不参与布局，避免空列把两侧撑开
  const cols = {}
  let maxTotalH = 0
  TYPE_ORDER.forEach((type) => {
    const col = nodes.filter((n) => n.data('type') === type).sort((a, b) => a.data('name').localeCompare(b.data('name'), 'zh'))
    if (!col.length) return
    const maxW = Math.max(...col.map((n) => n.width()))
    const totalH = col.reduce((s, n) => s + n.height() + V_GAP, 0) - V_GAP
    cols[type] = { col, maxW }
    maxTotalH = Math.max(maxTotalH, totalH)
  })
  const cw = cy.value.width()
  const ch = cy.value.height()
  let W = maxTotalH * (cw / ch)
  const names = Object.keys(cols)
  let minSpacing = 0
  for (let i = 0; i < names.length - 1; i++) {
    minSpacing = Math.max(minSpacing, cols[names[i]].maxW / 2 + cols[names[i + 1]].maxW / 2)
  }
  W = Math.max(W, (minSpacing + 70) * names.length)
  // 五类均布（适配：旧三类固定三中心 → 按实际类数等距展开）
  const centers = names.map((_, i) => -W / 2 + (W / names.length) * (i + 0.5))
  const pos = {}
  let idx = 0
  TYPE_ORDER.forEach((type) => {
    if (!cols[type]) return
    const { col } = cols[type]
    const cx = centers[idx++]
    let y = -maxTotalH / 2
    col.forEach((n) => {
      pos[n.id()] = { x: cx, y: y + n.height() / 2 }
      y += n.height() + V_GAP
    })
  })
  nodes.positions((node) => pos[node.id()])
}

// ---------- 节点搜索（仅高亮，不过滤；回车/点击定位 + Tab 循环） ----------
function applySearch() {
  if (!cy.value) return
  const q = searchQ.value.trim().toLowerCase()
  const ids = []
  cy.value.nodes().forEach((n) => {
    const hit = !!q && n.data('name').toLowerCase().includes(q)
    n.toggleClass('search-hit', hit)
    n.removeClass('search-focus')
    if (hit) ids.push(n.id())
  })
  searchHitIds = ids
  searchFocusIdx = -1
  searchHits.value = q ? ids.length : 0
}
function clearSearch() {
  searchQ.value = ''
  searchHitIds = []
  searchFocusIdx = -1
  cy.value?.$('.search-focus').removeClass('search-focus')
  applySearch()
}
// 定位到第 i 个命中节点：高亮 focus + fit 到视口；i 越界按循环取模
function focusSearchAt(i) {
  if (!cy.value || !searchHitIds.length) return
  searchFocusIdx = ((i % searchHitIds.length) + searchHitIds.length) % searchHitIds.length
  const el = cy.value.getElementById(searchHitIds[searchFocusIdx])
  if (!el.visible()) el.show() // 命中节点属已隐藏类型：临时显示以便定位（筛选按钮状态不变，下次重放时恢复隐藏）
  cy.value.$('.search-focus').removeClass('search-focus')
  el.addClass('search-focus')
  cy.value.animate({ fit: { eles: el, padding: 90 }, duration: 320 })
}
// 搜索定位后回到全图：fit 全部节点，清除焦点定位，保留 search-hit 高亮；同时取消 1跳
function fitAllGraph() {
  if (!cy.value || !cy.value.nodes().length) return
  cy.value.$('.search-focus').removeClass('search-focus')
  searchFocusIdx = -1
  if (hopMode.value) {
    restoreHopPositions() // 还原聚焦重排位置（直接 fit 全图）
    hopMode.value = 0
    hopCenterId = null
    cy.value.$('node.dim').removeClass('dim')
    cy.value.$('edge.dim').removeClass('dim')
  }
  const vis = cy.value.nodes(':visible')
  if (vis.length) cy.value.fit(vis, 50) // 只适配可见节点：已隐藏类型不参与全图视野，否则会缩到看不见
}

// F 键聚焦放大：居中到当前选中的节点并放大（每次按键再放大一档，上限 4x）
const FOCUS_ZOOM_STEP = 1.6
function focusSelected() {
  const cyInst = cy.value
  if (!cyInst) return
  const sel = cyInst.$(':selected').filter('node')
  if (!sel.length) return
  const el = sel[0]
  const next = Math.min(4, cyInst.zoom() * FOCUS_ZOOM_STEP)
  cyInst.stop() // 取消进行中的缩放动画，避免连续按键排队导致画面跳动
  cyInst.animate({ center: { eles: el }, zoom: next, duration: 260 })
  // 高亮只在目标节点尚未聚焦时才切换，避免同节点连按闪动
  if (!el.hasClass('search-focus')) {
    cyInst.$('.search-focus').removeClass('search-focus')
    el.addClass('search-focus')
  }
}
// 全图刚好完整可见所需的 zoom（与「全图」按钮 fit(padding 50) 一致的下限）
function fitAllZoomLevel(cyInst) {
  const bb = cyInst.nodes().boundingBox()
  const pad = 50
  const zw = (cyInst.width() - pad * 2) / bb.w
  const zh = (cyInst.height() - pad * 2) / bb.h
  return Math.max(0.0001, Math.min(zw, zh))
}
// Shift+F：反向缩小（有选中节点则以它为中心缩小，无则原地缩小）；
// 缩小到「全图可见」级别即停（再按等价于回到全图视图）
function zoomOutSelected() {
  const cyInst = cy.value
  if (!cyInst || !cyInst.nodes().length) return
  const sel = cyInst.$(':selected').filter('node')
  const next = cyInst.zoom() / FOCUS_ZOOM_STEP
  if (next <= fitAllZoomLevel(cyInst)) {
    cyInst.$('.search-focus').removeClass('search-focus')
    cyInst.fit(undefined, 50) // 已到全图级：直接回到整图视图
    return
  }
  cyInst.stop() // 取消进行中的缩放动画，避免连续按键排队导致画面跳动
  cyInst.animate(sel.length
    ? { center: { eles: sel[0] }, zoom: next, duration: 260 }
    : { zoom: next, duration: 260 })
}
// 无选中节点时按 F：以视口中心为锚原地放大一档（与选中节点聚焦同一步长/上限，可连按）
function zoomInCenter() {
  const cyInst = cy.value
  if (!cyInst) return
  const next = Math.min(4, cyInst.zoom() * FOCUS_ZOOM_STEP)
  cyInst.stop()
  cyInst.animate({ zoom: next, duration: 260 }) // 不带 center → 视口中心点保持在原位放大
}

// ---------- 邻域高亮 + 聚焦子图重排 ----------
// 还原被重排的节点到原坐标。瞬时设置（不做动画）：先 stop 掉进行中的元素动画，
// 再同步写回位置——避免「取消动画未播完又点 1跳」时把中间位置当原坐标备份，累积漂移。
function restoreHopPositions() {
  const cyInst = cy.value
  if (!hopMoved || !hopOrigin) { hopMoved = null; hopOrigin = null; return }
  cyInst.nodes().stop()
  hopMoved.forEach((n) => {
    const p = hopOrigin[n.id()]
    if (p) n.position({ x: p.x, y: p.y })
  })
  hopMoved = null
  hopOrigin = null
}

// 聚焦子图径向重排：先还原上一组，备份当前坐标，按到中心节点的跳数做同心圆布局
function relayoutHop(keepNodes, centerId, dist) {
  const cyInst = cy.value
  if (!cyInst) return
  if (hopMoved) restoreHopPositions()
  const origin = {}
  keepNodes.forEach((n) => { origin[n.id()] = { x: n.position('x'), y: n.position('y') } })
  hopOrigin = origin
  hopMoved = keepNodes
  keepNodes.layout({
    name: 'concentric',
    animate: { duration: 300, easing: 'ease-out' },
    fit: true,
    padding: 60,
    randomize: false,
    minNodeSpacing: 45,
    // concentric 值越大越靠中心：用负跳数（中心=0 最大→最内圈）；levelWidth<1 让每跳各成环
    concentric: (node) => -dist.get(node.id()),
    levelWidth: () => 0.5,
  }).run()
}

// 邻域高亮：按 hopMode 保留「N 跳内可达」的节点/边，其余 dim 半透明；中心节点变化时自动径向重排
function applyHop() {
  const cyInst = cy.value
  if (!cyInst) return
  cyInst.$('node.dim').removeClass('dim')
  cyInst.$('edge.dim').removeClass('dim')
  if (!hopMode.value) return
  const sel = cyInst.$(':selected').filter('node')
  if (!sel.length) {
    restoreHopPositions() // 取消选中 → 还原重排
    hopCenterId = null
    return
  }
  const center = sel[0]
  // 计算聚焦集合内每节点到中心的跳数（0..hopMode），并收集 keep 集合
  const dist = new Map()
  dist.set(center.id(), 0)
  const seen = new Set([center.id()])
  let frontier = center
  for (let d = 1; d <= hopMode.value; d++) {
    const next = frontier.closedNeighborhood().nodes().filter((n) => !seen.has(n.id()))
    next.forEach((n) => { seen.add(n.id()); dist.set(n.id(), d) })
    frontier = next
  }
  const keepNodes = cyInst.collection()
  seen.forEach((id) => keepNodes.merge(cyInst.getElementById(id)))
  const keepEdges = cyInst.edges().filter((e) => seen.has(e.source().id()) && seen.has(e.target().id()))
  cyInst.nodes().difference(keepNodes).addClass('dim')
  cyInst.edges().difference(keepEdges).addClass('dim')
  // 中心节点变化 → 重新径向排布（同节点只切高亮，不动位置）
  if (center.id() !== hopCenterId) {
    relayoutHop(keepNodes, center.id(), dist)
    hopCenterId = center.id()
  }
}

function setHop(mode) {
  if (mode === 0) {
    restoreHopPositions()
    hopMode.value = 0
    hopCenterId = null
    const cyInst = cy.value
    cyInst?.$('node.dim').removeClass('dim')
    cyInst?.$('edge.dim').removeClass('dim')
    fitAllGraph() // 取消聚焦：回到全图视图
    return
  }
  hopMode.value = mode
  applyHop()
}

// ---------- 全局自动整理（满意再保存） ----------
// 三列布局：实体在 1/3、属性在 1/2、规则在 2/3（画布可见宽度），整图一屏放下。
// 列内按关联关系排序（属性贴近所属实体、规则贴近所属属性），边尽量短、少交叉。
// 不标记未保存（不触发自动保存），展示「撤销整理」条——满意再保存，不满意一键还原。
function autoLayout() {
  const cyInst = cy.value
  if (!cyInst || !cyInst.nodes().length) return
  if (hopMode.value) setHop(0) // 先退出聚焦重排，避免两套布局打架
  layoutBackup = {}
  cyInst.nodes().forEach((n) => { layoutBackup[n.id()] = { x: n.position('x'), y: n.position('y') } })

  // 五类分带（适配）：对象 | 共享属性 | 私有属性 | 规则 | 动作；组内排序沿用旧
  // 「按关联对象排位、再按属性类排位、最后名称」的确定性策略。
  const targets = {}
  ALL_TYPES.forEach((t) => { targets[t] = [] })
  cyInst.nodes().filter((n) => n.visible()).forEach((n) => {
    const t = n.data('type')
    if (t in targets) targets[t].push(n)
    else targets['共享属性'].push(n) // 未知类型归属性侧
  })
  const rowHeight = 96
  const orderMap = new Map()
  const byName = (a, b) => String(a.data('name') || a.id()).localeCompare(String(b.data('name') || b.id()), 'zh')
  // 节点与「指定类型集合」相连节点中最靠前的排位（未连到返回大数，排到列尾）
  const primaryOrder = (n, types) => {
    let best = Infinity
    n.connectedEdges().forEach((e) => {
      const o = e.source().id() === n.id() ? e.target() : e.source()
      if (types.includes(o.data('type'))) {
        const idx = orderMap.get(o.id())
        if (idx != null && idx < best) best = idx
      }
    })
    return best === Infinity ? 1e9 : best
  }
  targets['对象'].sort(byName)
  targets['对象'].forEach((n, i) => orderMap.set(n.id(), i))
  const PROP_TYPES = ['共享属性', '私有属性']
  targets['共享属性'].sort((a, b) => primaryOrder(a, ['对象']) - primaryOrder(b, ['对象']) || byName(a, b))
  targets['私有属性'].sort((a, b) => primaryOrder(a, ['对象']) - primaryOrder(b, ['对象']) || byName(a, b))
  ;[...targets['共享属性'], ...targets['私有属性']].forEach((n, i) => orderMap.set(n.id(), i + 100000))
  targets['规则'].sort((a, b) => primaryOrder(a, PROP_TYPES) - primaryOrder(b, PROP_TYPES)
    || primaryOrder(a, ['对象']) - primaryOrder(b, ['对象']) || byName(a, b))
  targets['动作'].sort((a, b) => primaryOrder(a, PROP_TYPES) - primaryOrder(b, PROP_TYPES)
    || primaryOrder(a, ['对象']) - primaryOrder(b, ['对象']) || byName(a, b))

  // 五带布局（适配）：单列最多 30 个节点、超过均分成子列的旧规则保留；
  // 旧三带的「1/4~3/4 收敛循环」推广为五带等距锚点（-2/5..+2/5 视口模型宽），
  // fitZ 取各带行数决定的高度上限（行为与旧三类图谱接近，窗口尺寸不改变列数）。
  const P = 60 // 边距
  const Vw = cyInst.width()
  const Vh = cyInst.height()
  const colGap = 40 // 带内子列间距
  const ORDER = ALL_TYPES
  const bandW = {}
  ORDER.forEach((t) => {
    bandW[t] = targets[t].length ? Math.max(120, ...targets[t].map((n) => n.width())) : 0
  })
  const MAX_COL_NODES = 30
  const zHeight = (rows) => (Vh - 2 * P) / Math.max(rows, 1) / rowHeight
  const maxRows = Math.max(1, ...ORDER.map((t) => targets[t].length))
  const fitZ = Math.max(0.05, zHeight(maxRows))
  const positions = {}
  // dir: -1 = 从锚点往左延伸，0 = 围绕锚点对称，+1 = 往右延伸；anchorX 为锚点模型 x
  const putBand = (t, k, anchorX, dir) => {
    if (!k) return
    const rows = Math.ceil(targets[t].length / k)
    const step = bandW[t] + colGap
    targets[t].forEach((n, j) => {
      const c = Math.floor(j / rows) // 子列号（自上而下、从锚点向外）
      const r = j % rows // 行号
      const off = dir === 0 ? (c - (k - 1) / 2) * step : dir * c * step
      positions[n.id()] = {
        x: anchorX + off,
        y: (r - (rows - 1) / 2) * rowHeight, // 各带垂直居中
      }
    })
  }
  const viewW = Vw / fitZ // 视口模型宽
  const live = ORDER.filter((t) => targets[t].length)
  const anchors = live.map((_, i) => -viewW * 0.4 + (viewW * 0.8 / Math.max(1, live.length - 1 || 1)) * i)
  live.forEach((t, i) => {
    const k = Math.ceil(targets[t].length / MAX_COL_NODES)
    putBand(t, k, anchors[i], t === '对象' ? -1 : t === '规则' || t === '动作' ? 1 : 0)
  })
  cyInst.nodes().forEach((n) => {
    const p = positions[n.id()]
    if (p) n.animate({ position: p }, { duration: 400, easing: 'ease-out' })
  })
  setTimeout(() => {
    // 视口：布局中心 (0,0) 映射到画布中心；zoom 取整图 fit 值（夹在 min/max 内）
    cyInst.zoom(Math.min(Math.max(fitZ, cyInst.minZoom()), cyInst.maxZoom()))
    cyInst.pan({ x: Vw / 2, y: Vh / 2 })
    showLayoutUndo.value = true
    syncPositionsToBridge()
  }, 450)
}

// ---- 视图位置回写 bridge（坐标=视图状态：只进偏好，不产生业务保存）----
function syncPositionsToBridge() {
  if (!cy.value) return
  cy.value.nodes().forEach((n) => bridge.setPosition(n.id(), n.position('x'), n.position('y')))
}

// 撤销自动整理：恢复到整理前的坐标（瞬时设置，避免动画中途再点自动整理时备份到中间位置）
function undoLayout() {
  const cyInst = cy.value
  if (!cyInst || !layoutBackup) return
  cyInst.nodes().stop()
  cyInst.nodes().forEach((n) => {
    const p = layoutBackup[n.id()]
    if (p) n.position({ x: p.x, y: p.y })
  })
  layoutBackup = null
  showLayoutUndo.value = false
}

// 用户开始其他编辑/保存后，撤销条自动消失（视为接受当前布局）
function dismissLayoutUndo() {
  layoutBackup = null
  showLayoutUndo.value = false
}

// ---------- 撤销 / 重做（适配：领域命令快照 + 视图坐标快照双层） ----------
// 域变（增删/改名/连线）：{ domain } —— 撤销=bridge.applyUndo 换回真实模型（列表/数据库一致）；
// 视图（拖拽/坐标导入）：{ draft } —— 只回退画布坐标，不产生业务变更。
const UNDO_LIMIT = 50
let undoStack = []
let redoStack = []
let pendingDrag = null // 拖拽手势：{ snapshot, moved }
const skipUndoReset = false // 保留：导入重建时不清撤销历史（当前无 jsonId 导入，保留旧结构）
let syncing = false // applyGraphState 期间抑制 add/remove 监听（域变已单独 emit changed）

function captureState() {
  return currentDraft()
}
// 记录一个「操作前」撤销点：域变在领域命令内部 emit before-change/changed（桥负责）；
// 这里只为视图类操作记录坐标快照。domainSnapshot 非空时代表域变撤销点（由 undo 管理调用方传入）。
function pushUndo(domainSnapshot = null, draftSnapshot = null) {
  undoStack.push(domainSnapshot ? { domain: domainSnapshot } : { draft: draftSnapshot || captureState() })
  if (undoStack.length > UNDO_LIMIT) undoStack.shift()
  redoStack = []
}
// 用快照（draft 格式）原地重建画布：删多余、补缺失、更新变化的数据/位置
function applyGraphState(state) {
  const cyInst = cy.value
  if (!cyInst) return
  const nodes = new Map(state.nodes.map((n) => [n.id, n]))
  const edges = new Map(state.edges.map((e) => [e.id, e]))
  // 先删多余连线（避免悬空边），再删多余节点
  cyInst.edges().forEach((e) => { if (!edges.has(e.id())) cyInst.remove(e) })
  cyInst.nodes().forEach((n) => { if (!nodes.has(n.id())) cyInst.remove(n) })
  // 新增 / 更新节点
  nodes.forEach((n) => {
    const el = cyInst.getElementById(n.id)
    if (!el.length) {
      cyInst.add({
        data: { id: n.id, name: n.name, type: n.type, w: nodeW(n.name), h: nodeH(n.name), data: n.data || {} },
        classes: n.type,
        position: { x: n.x || 0, y: n.y || 0 },
      })
    } else {
      el.position({ x: n.x || 0, y: n.y || 0 })
      el.data('name', n.name)
      el.data('type', n.type)
      el.data('data', n.data || {})
      el.data('w', nodeW(n.name))
      el.data('h', nodeH(n.name))
      el.classes(n.type)
    }
  })
  // 新增 / 更新连线
  edges.forEach((e) => {
    if (!cyInst.getElementById(e.source).length || !cyInst.getElementById(e.target).length) return
    const el = cyInst.getElementById(e.id)
    if (!el.length) {
      cyInst.add({ data: { id: e.id, source: e.source, target: e.target, relation: e.relation, description: e.description, kind: e.kind, domainId: e.domainId } })
    } else {
      // C01 修复：端点变化必须走结构更新（move），只改 data 不会移动连线（验收报告 C01）
      if (el.source().id() !== e.source || el.target().id() !== e.target) {
        el.move({ source: e.source, target: e.target })
      }
      el.data('relation', e.relation)
      el.data('description', e.description)
      if (e.kind) el.data('kind', e.kind)
      if (e.domainId) el.data('domainId', e.domainId)
    }
  })
}

// ---- 桥→画布同步：领域命令/外部变化后，把最新投影增量应用到画布（不销毁、保留视口）----
function refreshFromBridge() {
  if (!cy.value) return
  syncing = true
  try {
    applyGraphState(bridgeState.draft)
  } finally { syncing = false }
  updateCounts()
  applySearch()
}
function afterUndoRedo() {
  // 清理临时态：选中/详情、1跳、整理条；刷新计数/搜索高亮（域变的 changed 已由桥 emit）
  clearInspector()
  if (hopMode.value) {
    restoreHopPositions()
    hopMode.value = 0
    hopCenterId = null
    cy.value?.$('node.dim').removeClass('dim')
    cy.value?.$('edge.dim').removeClass('dim')
  }
  dismissLayoutUndo()
  updateCounts()
  updateSelSummary()
  applySearch()
}
function undo() {
  if (!cy.value || !undoStack.length) return
  const entry = undoStack.pop()
  if (entry.domain) {
    redoStack.push({ domain: bridge.captureUndo() })
    bridge.applyUndo(entry.domain, '撤销')
    refreshFromBridge()
  } else {
    redoStack.push({ draft: captureState() })
    applyGraphState(entry.draft)
    syncPositionsToBridge()
  }
  afterUndoRedo()
}
function redo() {
  if (!cy.value || !redoStack.length) return
  const entry = redoStack.pop()
  if (entry.domain) {
    undoStack.push({ domain: bridge.captureUndo() })
    bridge.applyUndo(entry.domain, '重做')
    refreshFromBridge()
  } else {
    undoStack.push({ draft: captureState() })
    applyGraphState(entry.draft)
    syncPositionsToBridge()
  }
  afterUndoRedo()
}
// 领域命令的撤销点：命令执行前由调用方取快照
function captureDomain() {
  return bridge.captureUndo()
}
// 领域命令包装（P0 修复）：执行前记录领域快照，成功后入撤销栈；
// 失败（桥未变更）不入栈、不污染 redo。Ctrl+Z 经 applyUndo 换回真实模型。
function domainCmd(fn) {
  const snap = captureDomain()
  const r = fn()
  if (r && r.error) return r
  undoStack.push({ domain: snap })
  if (undoStack.length > UNDO_LIMIT) undoStack.shift()
  redoStack = []
  return r
}

function bindCyEvents() {
  cy.value.on('tap', (evt) => {
    pendingDrag = null // 纯点击（未拖拽）→ 清掉 grab 记录的临时快照，不产生撤销点
    if (evt.target === cy.value) {
      exitLinking()
      clearInspector()
      return
    }
    if (linking.value) {
      handleLinkTap(evt.target)
      return
    }
    // 节点：单击显示右侧详情；连线：单击直接跳转到来源定义编辑（图上不编辑连线）
    if (evt.target.isEdge()) openEdgeInWorkspace(evt.target)
    else if (evt.target.isNode()) showNodeInspector(evt.target)
  })
  // 双击节点（2026-09-20 用户要求）：与详情「编辑」一致，跳转到该节点的编辑页面；
  // 不再弹出画布内编辑弹窗（图上内容都来自对象建模，编辑统一在源端）。
  cy.value.on('dbltap', 'node', (evt) => {
    if (linking.value) return
    const n = bridgeState.draft.nodes.find((x) => x.id === evt.target.id())
    if (n) openEditInWorkspace(n)
  })
  cy.value.on('grab', 'node', () => {
    // grab = 按下开始（拖前），每次都覆盖：多选拖拽取同一时刻快照；也避免中断拖拽残留脏状态
    pendingDrag = { snapshot: captureState(), moved: false }
  })
  cy.value.on('dragfree', 'node', (evt) => {
    if (pendingDrag) {
      const snapNode = pendingDrag.snapshot.nodes.find((n) => n.id === evt.target.id())
      const p = evt.target.position()
      if (snapNode && (snapNode.x !== Math.round(p.x) || snapNode.y !== Math.round(p.y))) pendingDrag.moved = true
      if (pendingDrag.moved) {
        undoStack.push({ draft: pendingDrag.snapshot }) // 撤销点：拖拽前的位置（视图点，不动领域）
        if (undoStack.length > UNDO_LIMIT) undoStack.shift()
        redoStack = []
      }
      pendingDrag = null
    }
    bridge.setPosition(evt.target.id(), evt.target.position('x'), evt.target.position('y')) // 坐标=视图偏好
    dismissLayoutUndo() // 用户手动拖拽后，撤销条视为接受
    updateCounts()
  })
  cy.value.on('add remove', () => {
    if (initializing || syncing) return // 域变的 changed 已由桥 emit，画布同步不重复提交
    dismissLayoutUndo()
    updateCounts()
  })
  cy.value.on('select unselect', () => {
    updateSelSummary()
    if (hopMode.value) applyHop()
  })
  // 边 hover：高亮 + tooltip（relation + description，跟随边中点）；平移/缩放时隐藏避免错位
  cy.value.on('mouseover', 'edge', (evt) => {
    evt.target.addClass('hovered')
    showEdgeTip(evt.target)
  })
  cy.value.on('mouseout', 'edge', (evt) => {
    evt.target.removeClass('hovered')
    hideEdgeTip()
  })
  cy.value.on('pan zoom', () => hideEdgeTip())
  // 指针离开元素/画布时隐藏 tooltip（cytoscape 只追踪容器内事件，画布 mouseleave 兜底）
  cy.value.on('mouseout', (evt) => {
    if (evt.target === cy.value) hideEdgeTip()
  })
}

// 工具栏「删除」按钮的动态摘要：按当前选中对象实时统计节点/连线数量
function updateSelSummary() {
  if (!cy.value) {
    selSummary.value = ''
    selNodeCount.value = 0
    selEdgeCount.value = 0
    return
  }
  const sel = cy.value.$(':selected')
  const nodes = sel.filter((e) => e.isNode()).length
  const edges = sel.filter((e) => e.isEdge()).length
  selNodeCount.value = nodes
  selEdgeCount.value = edges
  const parts = []
  if (nodes) parts.push(nodes + ' 个节点')
  if (edges) parts.push(edges + ' 条连线')
  selSummary.value = parts.join('、')
}

// 边 hover tooltip：以边中点的渲染坐标 + 画布容器偏移换算视口坐标（fixed 定位）
function showEdgeTip(edge) {
  const rel = edge.data('relation') || ''
  const desc = String(edge.data('description') || '').trim()
  edgeTipText.value = desc ? rel + '：' + desc : rel || '连线'
  const mp = edge.midpoint()
  const rp = { x: mp.x * cy.value.zoom() + cy.value.pan().x, y: mp.y * cy.value.zoom() + cy.value.pan().y }
  const rect = canvasEl.value.getBoundingClientRect()
  edgeTipX.value = rect.left + rp.x
  edgeTipY.value = rect.top + rp.y - 10
  edgeTip.value = true
}
function hideEdgeTip() {
  edgeTip.value = false
}

function initCy() {
  initializing = true
  if (cy.value) cy.value.destroy()
  cy.value = cytoscape({
    container: canvasEl.value,
    elements: elementsFromDraft(),
    wheelSensitivity: 0.2,
    boxSelectionEnabled: true,
    minZoom: 0.1,
    maxZoom: 4,
    layout: { name: 'preset' },
    style: GRAPH_STYLE,
  })
  hopMode.value = 0 // 画布重建：重置邻域高亮/重排状态
  hopCenterId = null
  hopMoved = null
  hopOrigin = null
  showLayoutUndo.value = false
  layoutBackup = null
  if (!skipUndoReset) { undoStack = []; redoStack = []; pendingDrag = null } // 非导入重建时清空撤销历史
  newNodeSeq = 0
  bindCyEvents()
  // 视口偏好（适配：旧版不存视口；现按账号+本体隔离持久化）
  const vp = bridgeState.prefsViewport
  const validVp = vp && Number.isFinite(vp.zoom) && vp.pan && Number.isFinite(vp.pan.x) && Number.isFinite(vp.pan.y)
    && !(Math.abs(vp.zoom - 0.1) < 1e-6 && vp.pan.x === 0 && vp.pan.y === 0) // 退化值（0 尺寸画布写下的 minZoom/pan=0）不恢复
  if (validVp) {
    cy.value.zoom(Math.min(Math.max(vp.zoom, cy.value.minZoom()), cy.value.maxZoom()))
    cy.value.pan({ x: vp.pan.x, y: vp.pan.y })
  } else {
    // 无有效记忆（首次打开 / 历史退化值）：适应可见节点，避免落在 minZoom
    const vis = cy.value.nodes().filter(n => n.visible())
    if (vis.length) { try { cy.value.fit(vis, 50) } catch { /* 空集合保原视口 */ } }
  }
  cy.value.on('pan zoom', () => {
    // 尺寸为 0 时（容器尚未布局/被遮挡）不保存视口：那时 zoom/pan 是退化值，
    // 写入会把下次打开钉在 minZoom（实测 0.1）。
    if (cy.value.width() <= 0 || cy.value.height() <= 0) return
    bridgeState.prefsViewport = { zoom: cy.value.zoom(), pan: { x: cy.value.pan().x, y: cy.value.pan().y } }
    bridge.persistPrefs()
  })
  updateCounts()
  applySearch() // 画布重建后恢复搜索高亮（搜索词未清时）
  initializing = false
}

// 键盘（C04 修复）：仅 document 一个监听；处理到的键一律 stopPropagation，
// 避免工作台全局快捷键（Ctrl+S/Ctrl+Z/Esc）与画布操作重复执行；输入框让行。
function onKeydown(e) {
  const consumed = handleKey(e)
  if (consumed) {
    e.preventDefault()
    e.stopPropagation()
  }
}
function handleKey(e) {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'z' && !isTyping(e.target)) {
    if (e.shiftKey) redo()
    else undo()
    return true
  }
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 's') {
    dismissLayoutUndo() // 保存即视为接受当前布局
    commitNow() // 保存=当前草稿协调器立即提交
    return true
  }
  // F：聚焦放大到当前选中节点；Shift+F：反向缩小（输入框内不触发）
  if (e.key.toLowerCase() === 'f' && !isTyping(e.target)) {
    const sel = cy.value?.$(':selected').filter('node')
    if (sel && sel.length) {
      if (e.shiftKey) {
        zoomOutSelected()
      } else {
        focusSelected()
      }
    } else {
      // 无选中节点：F 从视口中心原地放大，Shift+F 原地缩小
      if (e.shiftKey) zoomOutSelected()
      else zoomInCenter()
    }
    return true
  }
  if (e.key === 'Delete' || e.key === 'Backspace') {
    const sel = cy.value?.$(':selected')
    if (sel && sel.length && !isTyping(e.target)) {
      deleteSelection(sel)
      return true
    }
    return false
  }
  if (e.key === 'Escape') {
    const closed = exitLinking()
    const modalClosed = closeAllModals()
    return closed || modalClosed // 都没处理时放行给工作台（如关闭全局弹层）
  }
  // ?：打开快捷键说明（输入框内不触发）
  if (e.key === '?' && !isTyping(e.target)) {
    showShortcuts.value = !showShortcuts.value
    return true
  }
  return false
}
function isTyping(t) {
  return t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)
}
function closeAllModals() {
  let any = false
  for (const v of [showNewNode, showNewEdge, showGraphs, showMultiPick, showShortcuts]) {
    if (v.value) { v.value = false; any = true }
  }
  if (viewMode.value !== 'editor') { viewMode.value = 'editor'; any = true }
  if (maximized.value) { maximized.value = false; any = true }
  clearInspector()
  return any
}

// ---------- 节点操作 ----------
function _openNewNode() {
  exitLinking()
  clearInspector()
  showNewNode.value = true
}
function createNode({ type, name }) {
  const r = domainCmd(() => bridge.domainCreateNode({ type, name }))
  if (r.error) { toast(r.error, true); return }
  const pos = centerPos()
  // 连续新建错开，避免新节点全部堆叠在视口中心
  pos.x += newNodeSeq * 22
  pos.y += newNodeSeq * 22
  newNodeSeq += 1
  bridge.setPosition(r.id, pos.x, pos.y)
  refreshFromBridge()
  cy.value.getElementById(r.id).select()
  showNewNode.value = false
  toast('已创建节点：' + name)
}

// ---------- 连线 ----------
// 搜索式连线面板：源/目标既可画布点击选择，也可在面板按名称搜索选择
// （属性节点数百个时画布点选困难、源目标相距远，搜索选点不依赖画布视野）
const linkQ = ref({ src: '', tgt: '' }) // 两个搜索框的关键词
const linkHits = ref({ src: [], tgt: [] }) // 搜索候选 [{id,name,type}]
const linkTypeFilter = ref({ src: '', tgt: '' }) // 类型筛选：'' = 全部（连线会话内保持，连续连线不用重选）
const linkTargets = ref([]) // 待连线目标列表（多选累积，一次确认批量建线）[{id,name,type}]
function linkCandidates(q, excludeId, type, markLinked = false) {
  if (!cy.value || !q.trim()) return []
  const s = q.trim().toLowerCase()
  const srcId = markLinked ? excludeId : null // 目标侧：标记与源节点已连线的候选
  const edges = srcId ? cy.value.edges() : null
  return cy.value
    .nodes()
    .filter((n) => (!type || n.data('type') === type)
      && typeVisible.value[n.data('type')] !== false && n.id() !== excludeId) // 隐藏类型不参与（连线后看不见，容易误解）
    .filter((n) => String(n.data('name') || '').toLowerCase().includes(s))
    .sort((a, b) => a.data('name').localeCompare(b.data('name'), 'zh'))
    .slice(0, 30)
    .map((n) => {
      const id = n.id()
      return {
        id, name: n.data('name'), type: n.data('type'),
        linked: !!(edges && edges.some((e) => (e.source().id() === srcId && e.target().id() === id)
          || (e.target().id() === srcId && e.source().id() === id))), // 与源节点已直接相连（双向算）
      }
    })
}
function onLinkQ(slot, keepChecks = false) {
  // 重新计算候选；关键词/类型变化时清空目标侧勾选（focus 重算不丢已勾选）
  if (slot === 'tgt' && !keepChecks) linkChecked.value = []
  linkHits.value[slot] = linkCandidates(
    linkQ.value[slot],
    slot === 'tgt' ? linkSource.value?.id() : '',
    linkTypeFilter.value[slot],
    slot === 'tgt',
  )
}
function setLinkTypeFilter(slot, t) {
  linkTypeFilter.value[slot] = t
  onLinkQ(slot) // 已有关键词时切换类型即时重算候选
}
function closeLinkHits(slot) {
  setTimeout(() => { linkHits.value[slot] = [] }, 150) // 延迟关闭：给 mousedown 选点留时间
}
function resetLinkPanel() {
  linkQ.value = { src: '', tgt: '' }
  linkHits.value = { src: [], tgt: [] }
  linkChecked.value = []
}
function pickLinkNode(slot, id) {
  const el = cy.value.getElementById(id)
  if (!el.length) return
  if (slot === 'src') {
    if (linkSource.value) linkSource.value.removeClass('link-src')
    linkSource.value = el
    el.addClass('link-src')
    linkHint.value = '已选源节点 ' + el.data('name') + '，选目标节点（可多选，画布点击或搜索）'
  } else {
    addLinkTarget(el)
  }
  resetLinkPanel()
}
function clearLinkSource() {
  linkSource.value?.removeClass('link-src')
  linkSource.value = null
  linkHint.value = '连线模式：先选源节点，再选目标节点（可多选）'
}
// 选中一个目标 → 进待连列表（不立即建线）；源未选/重复/自连/已连线会被拦下
function addLinkTarget(el) {
  const src = linkSource.value
  if (!src) return
  if (el.id() === src.id()) { toast('目标不能与源节点相同', true); return }
  if (linkTargets.value.some((t) => t.id === el.id())) { toast('该节点已在待连列表中', true); return }
  if (isLinkedToSource(el.id())) { toast('该节点已与源节点连线', true); return }
  linkTargets.value.push({ id: el.id(), name: el.data('name'), type: el.data('type') })
  linkHint.value = `已选 ${linkTargets.value.length} 个目标，继续选（画布点击/搜索）或点「连线」确认`
}
// 与当前源节点已直接相连（任一方向算，避免同两点间重复建线）
function isLinkedToSource(id) {
  const srcId = linkSource.value?.id()
  if (!srcId || id === srcId) return id === srcId
  return cy.value.edges().some((e) => (e.source().id() === srcId && e.target().id() === id)
    || (e.target().id() === srcId && e.source().id() === id))
}
function removeLinkTarget(id) {
  linkTargets.value = linkTargets.value.filter((t) => t.id !== id)
}
function clearLinkTargets() {
  linkTargets.value = []
}
// 目标候选勾选：勾一部分再「加入所选」，比逐个点/全量加更省事
const linkChecked = ref([]) // 已勾选的目标候选 id
function toggleLinkCheck(id) {
  const h = linkHits.value.tgt.find((x) => x.id === id)
  if (h?.linked) { toast('该节点已与源节点连线', true); return }
  linkChecked.value = linkChecked.value.includes(id)
    ? linkChecked.value.filter((x) => x !== id)
    : [...linkChecked.value, id]
}
function toggleLinkCheckAll() {
  const pickable = linkHits.value.tgt.filter((h) => !h.linked).map((h) => h.id)
  linkChecked.value = linkChecked.value.length === pickable.length ? [] : pickable
}
function addCheckedLinkHits() {
  const src = linkSource.value
  if (!src || !linkChecked.value.length) return
  let added = 0
  let skipped = 0
  linkChecked.value.forEach((id) => {
    const h = linkHits.value.tgt.find((x) => x.id === id)
    if (!h || h.linked || h.id === src.id() || linkTargets.value.some((t) => t.id === id)) { skipped++; return }
    linkTargets.value.push(h) // 候选行本身就是 {id,name,type}
    added++
  })
  linkHits.value.tgt = []
  linkChecked.value = []
  if (!added) { toast('所选节点都已加入过', true); return }
  linkHint.value = `已选 ${linkTargets.value.length} 个目标，继续选或点「连线」确认`
  toast(`已加入 ${added} 个目标` + (skipped ? `（跳过 ${skipped} 个已存在）` : ''))
}
// 一次确认：源节点 × 全部待连目标 → 弹一次关系填写窗（同一关系/描述批量建线）
function confirmLink() {
  const src = linkSource.value
  if (!src || !linkTargets.value.length) return
  edgeDraft.value = {
    srcId: src.id(),
    srcName: src.data('name'),
    tgtName: linkTargets.value.map((t) => t.name).join('、'),
  }
  showNewEdge.value = true
}
function _toggleLinking() {
  linking.value = !linking.value
  linkSource.value = null
  clearInspector()
  cy.value?.$('.link-src').removeClass('link-src')
  resetLinkPanel()
  linkTypeFilter.value = { src: '', tgt: '' } // 进入/退出连线会话时类型筛选回到默认「全部」
  clearLinkTargets()
  linkHint.value = '连线模式：先选源节点，再选目标节点（可多选）'
}
function exitLinking() {
  const was = linking.value
  linking.value = false
  linkSource.value = null
  cy.value?.$('.link-src').removeClass('link-src')
  resetLinkPanel()
  linkTypeFilter.value = { src: '', tgt: '' }
  clearLinkTargets()
  return was
}
function handleLinkTap(el) {
  if (!el.isNode()) return
  if (!linkSource.value) {
    linkSource.value = el
    el.addClass('link-src')
    linkHint.value = '已选源节点 ' + el.data('name') + '，选目标节点（可多选，画布点击或搜索）'
    return
  }
  if (linkSource.value.id() === el.id()) {
    clearLinkSource()
    return
  }
  addLinkTarget(el)
}
function createEdges({ relation, description }) {
  const srcId = edgeDraft.value.srcId
  const valid = linkTargets.value.filter((t) => t.id !== srcId && cy.value.getElementById(t.id).length)
  if (!srcId || !cy.value.getElementById(srcId).length || !valid.length) {
    showNewEdge.value = false
    toast('连线目标已失效，请重新选择', true)
    return
  }
  // 领域批量连线：桥先全量校验（对象链接/共享引用/规则与动作引用/非法组合），
  // 全部合法一次提交；有失败整批拒绝并列出原因（需求 §3.2）。
  const r = domainCmd(() => bridge.domainCreateEdges(valid.map((t) => ({ sourceId: srcId, targetId: t.id })), { relation, description }))
  if (r.error) {
    toast('连线未执行：' + r.error, true)
    return
  }
  refreshFromBridge()
  showNewEdge.value = false
  clearLinkTargets()
  resetLinkPanel()
  if (linking.value) linkHint.value = '连线模式：先选源节点，再选目标节点（可多选）'
  toast(`已创建 ${r.newIds.length} 条连线`)
}

// ---------- 常驻 inspector / 删除 ----------
// 顶栏「详情」开关：展开/收起右侧面板（默认收起，画布占满）
function toggleInspector() {
  inspOpen.value = !inspOpen.value
}
// 编辑跳转（2026-09-20 用户要求，取代原「打开定义」）：详情「编辑」把该节点交给
// 工作台既有编辑表单页（对象/属性/共享属性/规则/动作），携 edit:true 让目标页直接打开编辑表单；
// 同时携画布返回上下文（canvas/canvasNode=lg 节点 ID），保存或取消后经「← 返回图谱」回到画布并定位。
function openEditInWorkspace(n) {
  if (!n) return
  const common = { canvas: true, canvasNode: n.id, edit: true }
  if (n.type === '对象') emit('navigate', 'objects', { type: n.domainId, ...common })
  else if (n.type === '共享属性') emit('navigate', 'library', { definition: n.domainId, ...common })
  else if (n.type === '私有属性') emit('navigate', 'objects', { type: n.data?.ownerDomainId || '', property: n.domainId, tab: 'props', ...common })
  else if (n.type === '规则') emit('navigate', 'rules', { definition: n.domainId, ...common })
  else emit('navigate', 'actions', { definition: n.domainId, ...common })
}

// 连线编辑跳转（2026-09-20 用户要求）：图谱上的内容都来自对象建模，连线的编辑一律到源端——
// ① 对象链接 → 起点对象的「链接」页签并打开该链接表单；
// ② 共享引用 / 私有属性归属 → 所属对象的「属性」页签并打开该属性表单（引用在对象属性上维护）；
// ③ 规则关联 / 动作关联 → 对象的「规则」/「动作」页签并定位该关联。
// 防重：双击会连发两次 tap，同一连线 500ms 内只跳转一次。
let lastEdgeJump = { id: '', at: 0 }
function openEdgeInWorkspace(el) {
  const edgeId = el && el.id ? el.id() : ''
  if (!edgeId) return
  const now = Date.now()
  if (lastEdgeJump.id === edgeId && now - lastEdgeJump.at < 500) return
  const edge = bridgeState.draft.edges.find((e) => e.id === edgeId)
  if (!edge) return
  const src = bridgeState.draft.nodes.find((n) => n.id === edge.source)
  const tgt = bridgeState.draft.nodes.find((n) => n.id === edge.target)
  if (!src) { toast('连线起点已不存在', true); return }
  lastEdgeJump = { id: edgeId, at: now }
  const common = { canvas: true, canvasNode: edge.id }
  if (edge.kind === '对象链接') {
    emit('navigate', 'objects', { type: src.domainId, tab: 'links', definition: edge.domainId, edit: true, ...common })
  } else if (edge.kind === '共享引用' || edge.kind === '私有属性') {
    emit('navigate', 'objects', { type: src.domainId, tab: 'props', property: edge.domainId, edit: true, ...common })
  } else if (edge.kind === '规则关联') {
    emit('navigate', 'objects', { type: src.domainId, tab: 'rules', definition: tgt?.domainId || '', ...common })
  } else {
    emit('navigate', 'objects', { type: src.domainId, tab: 'actions', definition: tgt?.domainId || '', ...common })
  }
}
function showNodeInspector(el) {
  insp.value = {
    kind: 'node',
    id: el.id(),
    name: el.data('name'),
    type: el.data('type'),
    data: el.data('data') || {},
  }
}
function clearInspector() {
  insp.value = null
}
// 节点面板「编辑」→ 跳转到该节点的编辑页面（2026-09-20 用户要求；双击同效，见 dbltap 处理）
function editInspNode() {
  if (insp.value?.kind !== 'node') return
  const n = bridgeState.draft.nodes.find((x) => x.id === insp.value.id)
  if (!n) { toast('节点已不存在', true); return }
  openEditInWorkspace(n)
}
async function deleteSelection(els) {
  const nodes = els.filter((e) => e.isNode())
  const edges = els.filter((e) => e.isEdge())
  if (!nodes.length && !edges.length) return
  const parts = []
  if (nodes.length) parts.push(nodes.length + ' 个节点')
  if (edges.length) parts.push(edges.length + ' 条连线')
  let extra = 0
  if (nodes.length) {
    const conn = new Set()
    nodes.forEach((x) => x.connectedEdges().forEach((e) => conn.add(e.id())))
    extra = Math.max(0, conn.size - edges.length)
  }
  let msg = '确认删除选中：' + parts.join('、') + '？'
  if (extra > 0) msg += '（另有 ' + extra + ' 条连线因连接被删节点将一并删除）'
  if (await confirmDialog(msg)) {
    const nodeIds = els.filter((e) => e.isNode()).map((e) => e.id())
    const edgeIds = els.filter((e) => e.isEdge()).map((e) => e.id())
    const r = domainCmd(() => bridge.domainDeleteSelection(nodeIds, edgeIds)) // 先全量校验再一次提交
    if (r.error) { toast(r.error, true); return }
    refreshFromBridge()
    clearInspector()
    updateSelSummary()
  }
}

// 工具栏「删除」：删除当前选中的节点/连线，与键盘 Delete 同一逻辑
function _deleteSelectedNodes() {
  const sel = cy.value?.$(':selected')
  if (!sel || !sel.length) {
    toast('请先在画布上选中要删除的节点或连线', true)
    return
  }
  deleteSelection(sel)
}

// ---------- 图谱（本体）----------
function _openGraphs() {
  exitLinking()
  clearInspector()
  showGraphs.value = true
}
function onGraphSwitched() {
  // 本体切换后（宿主重新载入 state）：桥换偏好键 + 重投影 + 重建画布
  bridge.setPrefs(props.ontologyId)
  bridge.loadPrefs()
  bridge.reload()
  initCy()
  if (cy.value.nodes().length) cy.value.fit(undefined, 50)
  updateCounts()
  if (!cy.value.nodes().length) toast('本体「' + bridgeState.graphName + '」暂无内容，点击「新建节点」开始')
}

// ---------- 保存 / 版本 ----------
// 保存=当前草稿协调器立即提交（409/失败由工作台五态与冲突面板呈现，不假成功）
function _saveDraftFlow() {
  exitLinking()
  dismissLayoutUndo()
  commitNow()
  toast('已提交保存')
}
// 版本管理入口 → 当前「本体校验与发布」页（发布版本只读、修订历史在该页维护）
function _openVersions() {
  exitLinking()
  emit('navigate', 'o-release')
}
function goPreview() {
  exitLinking()
  clearInspector()
  if (!props.latestRelease) {
    toast('当前本体还没有发布版本；请先在「校验与发布」完成发布', true)
    return
  }
  previewVersion.value = props.latestRelease
  previewError.value = ''
  previewKey.value += 1
  viewMode.value = 'preview'
}

// ---------- 导入 ----------
async function _onCoordinatesFile(e) {
  const file = e.target.files[0]
  e.target.value = ''
  if (!file) return
  exitLinking()
  clearInspector()
  if (!hasGraph.value) {
    toast('请先选择本体', true)
    return
  }
  let coords
  try {
    coords = JSON.parse(await file.text())
  } catch {
    toast('坐标文件不是有效的 JSON', true)
    return
  }
  if (typeof coords !== 'object' || coords === null || Array.isArray(coords)) {
    toast('坐标文件格式不正确：应为 { "节点id": { x, y } }', true)
    return
  }
  // 稳定 ID（lg:*）直接命中优先；旧 czy: 序号做一次兼容映射（对象→entity，
  // 共享/私有按投影顺序合用 attribute 序列，规则→rule；动作无旧映射，登记于 §5）。
  const counters = { entity: 0, attribute: 0, rule: 0 }
  const seqOf = (n) => {
    if (n.data('type') === '对象') return `czy:entity:${String(++counters.entity).padStart(2, '0')}`
    if (n.data('type') === '规则') return `czy:rule:${String(++counters.rule).padStart(2, '0')}`
    if (n.data('type') === '共享属性' || n.data('type') === '私有属性') return `czy:attribute:${String(++counters.attribute).padStart(2, '0')}`
    return ''
  }
  let applied = 0
  let missing = 0
  pushUndo(null, captureState()) // 视图撤销点：应用坐标前
  cy.value.nodes().forEach((n) => {
    const p = coords[n.id()] || coords[seqOf(n)]
    if (p && Number.isFinite(p.x) && Number.isFinite(p.y)) {
      n.position({ x: p.x, y: p.y })
      applied += 1
    } else {
      missing += 1
    }
  })
  if (!applied) {
    undoStack.pop()
    toast('没有节点匹配到坐标，请检查坐标文件与当前本体是否对应', true)
    return
  }
  cy.value.fit(undefined, 50)
  syncPositionsToBridge() // 坐标=视图状态：只写本机偏好，不产生业务保存
  toast('已导入坐标：' + applied + ' 个节点' + (missing ? '，' + missing + ' 个未匹配' : ''))
}
// ---------- 导出（客户端本地生成；不经旧后端） ----------
async function _exportBundleFlow() {
  if (!hasGraph.value) {
    toast('请先选择本体', true)
    return
  }
  const { blob, name } = buildOfflineBundle(currentDraft(), bridgeState.graphName)
  downloadBlob(blob, name)
  toast('已导出图谱包：' + name + '（坐标 + 离线预览 HTML，断网可查看）')
}
async function _exportJsonIdFlow() {
  if (!hasGraph.value) {
    toast('请先选择本体', true)
    return
  }
  const draft = currentDraft()
  const losses = jsonIdLosses(draft)
  if (losses.length) {
    // 需求 §2：存在不可表达内容默认阻止有损导出，显示损失清单
    await confirmDialog('jsonId 为旧版语义格式，当前本体包含其无法表达的内容，导出已被阻止：\n· ' + losses.join('\n· ') +
      '\n\n完整迁移请使用「设置 → 配置迁移」。', '我知道了')
    return
  }
  const jsonid = buildJsonIdLocal(draft, bridgeState.graphName)
  const blob = new Blob([JSON.stringify(jsonid, null, 1)], { type: 'application/json;charset=utf-8' })
  downloadBlob(blob, (bridgeState.graphName || 'graph') + '.jsonId')
  toast('已导出 jsonId：' + (bridgeState.graphName || 'graph'))
}
function _gotoImport() {
  emit('navigate', 'o-home')
  toast('请在本体工作概览使用「导入本体」')
}

// ---------- 加载 / 卸载（宿主已备好 state；本体切换走 onOntologyChanged） ----------
function bootstrap() {
  bridge.setPrefs(props.ontologyId)
  bridge.loadPrefs()
  typeVisible.value = loadTypeVisible()
  bridge.reload()
  initCy()
  if (cy.value.nodes().length > 1 && allNodesStacked()) {
    applyColumnLayout()
    syncPositionsToBridge()
  }
  if (cy.value.nodes().length && !bridgeState.prefsViewport) cy.value.fit(undefined, 50)
  updateCounts()
  applyFocusTarget()
}
function onOntologyChanged() { onGraphSwitched() }

onMounted(() => {
  document.addEventListener('keydown', onKeydown)
  canvasEl.value.addEventListener('mouseleave', hideEdgeTip)
  bootstrap()
})

// 外部内容变化（定义页编辑、保存回滚、App 撤销）：重投影并同步画布；本体切换重建基线
watch(() => bridgeSignature(props.state), () => {
  if (!bridge.checkExternal()) return
  refreshFromBridge()
  if (undoStack.length || redoStack.length) {
    undoStack = []; redoStack = []
    toast('本体内容已在其他页面更新，画布已同步，旧撤销历史已清空')
  }
})
watch(() => props.ontologyId, () => onOntologyChanged())
// 定义页返回定位（C03 补接）：按 lg: 身份选中节点或边；不存在才提示
// 返回画布定位（2026-09-20 修正）：只做「选中 + 必要时轻微居中」，绝不放缩与改坐标——
// 用户反馈双击进编辑页返回后坐标/视图变了；根因是这里曾强制 zoom 到 ≥1 并重新居中。
function applyFocusTarget() {
  const t = props.focusTarget
  if (!t || !cy.value) return
  const el = cy.value.getElementById(t)
  if (!el.length) { toast('目标定义已不存在，已清除画布选择。'); return }
  cy.value.$(':selected').unselect()
  el.select()
  if (!el.isEdge()) showNodeInspector(el)
  // 仅在目标不在当前视口内时才平移使其可见（保持缩放不变）；已在视口内则完全不动视图
  try {
    const rp = el.renderedPosition()
    const w = cy.value.width(), h = cy.value.height()
    const pad = 60
    if (rp.x < pad || rp.y < pad || rp.x > w - pad || rp.y > h - pad) {
      cy.value.animate({ center: { eles: el }, duration: 220 }) // 不传 zoom：保持用户当前缩放
    }
  } catch { /* 定位失败保持视口 */ }
}
watch(() => props.focusTarget, (t) => { if (t) applyFocusTarget() })

onBeforeUnmount(() => {
  document.removeEventListener('keydown', onKeydown)
  bridge.flushPrefs()
  if (cy.value) cy.value.destroy()
})
</script>

<style scoped>
.preview-error { padding: 40px 32px; color: var(--ink); background: #fff; border: 1px solid var(--line); border-radius: 10px; margin: 24px; }
.preview-error strong { font-size: 15px; }
.preview-error p { margin: 10px 0 18px; color: var(--muted); font-size: 12.5px; overflow-wrap: anywhere; white-space: pre-wrap; }
.pe-actions { display: flex; gap: 8px; }
.legacy-editor-root { height: 100%; min-height: 0; }
.legacy-editor-root.max { position: fixed; inset: 0; z-index: 90; background: var(--canvas); }
.brand-sub { font-size: 11px; color: var(--muted); max-width: 132px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.edge-kind { display: inline-block; margin-left: 8px; font-size: 10px; color: var(--muted); border: 1px solid var(--line); border-radius: 4px; padding: 1px 6px; vertical-align: 1px; }
.editor { display: flex; flex-direction: column; height: 100%; }
.topbar {
  display: flex;
  align-items: stretch;
  padding: 7px 12px 8px;
  background: #fff;
  border-bottom: 1px solid var(--line);
  z-index: 20;
}
.body {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 0;
}
.tool-row {
  /* 2026-09-20 用户指出此处有一条多余空行+分隔线：这是头部两行时代的残留
     （margin-top:7px + padding-top:6px + border-top），单行布局后必须清零。 */
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 0;
  padding-top: 0;
  border-top: 0;
  /* 工具行放不下时整组换行：宁可多占一行，也不把右端按钮挤出视口裁切（.tool-right 见下） */
  flex-wrap: wrap;
}
/* 视图操作簇：与前面的「整理节点/类型筛选/搜索」分开成组，空间足够时右侧一行，
   不够时整体落到第二行并保持右对齐（簇内不再各自换行，保持一排按钮的整齐） */
.tool-right {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 6px;
  flex: none;
  flex-wrap: nowrap;
}
/* 窄屏（≤1100）：换行会让工具行长高、把画布挤到 0 高（768 实测），
   改回单行横向滚动；视图操作簇仍作为整体排在最右。 */
@media (max-width: 1100px) {
  .tool-row { flex-wrap: nowrap; overflow-x: auto; overflow-y: hidden; scrollbar-width: thin; padding-bottom: 4px; }
  .tool-row > * { flex: 0 0 auto; }
  .type-filter { flex-wrap: nowrap; }
  .tool-right { margin-left: 8px; }
}
.type-filter { display: flex; align-items: center; gap: 5px; }
.type-filter .chip {
  display: inline-flex; align-items: center; gap: 6px; height: 32px; padding: 0 10px;
  border: 1px solid var(--line); border-radius: 16px; background: #fff; color: var(--ink);
  font-family: inherit; font-size: 12px; cursor: pointer;
  transition: background .12s, border-color .12s, opacity .12s;
}
.type-filter .chip:hover { background: #f3f6f5; border-color: #bdc8ca; }
.type-filter .chip .dot { width: 8px; height: 8px; border-radius: 50%; flex: none; }
.type-filter .chip b { font-weight: 700; font-size: 11px; color: var(--muted); }
.type-filter .chip.off { opacity: .5; }
.search-box {
  position: relative; display: flex; align-items: center; margin-left: 10px;
}
.search-box input {
  width: 200px; height: 32px; border: 1px solid var(--line); border-radius: 6px;
  background: #f1f4f3; padding: 0 30px 0 30px; outline: none; font-size: 12px;
}
.search-box input:focus { background: #fff; border-color: var(--accent); box-shadow: 0 0 0 3px var(--soft); }
.search-box > svg {
  position: absolute; left: 9px; top: 8px; width: 15px; height: 15px; color: var(--muted); pointer-events: none;
}
.search-count { position: absolute; right: 28px; color: var(--muted); font-size: 10px; cursor: pointer; user-select: none; }
.search-count:hover { color: var(--accent); }
.search-clear {
  position: absolute; right: 4px; top: 4px; width: 24px; height: 24px; border: 0; background: transparent;
  border-radius: 4px; cursor: pointer; color: var(--muted); font-size: 14px; line-height: 1;
}
.search-clear:hover { background: #e7ecea; }
/* 视图按钮间距统一交给 .tool-right 的 gap，不再各自加 margin-left（避免换行后间距不齐） */
.inspector-toggle { margin-left: 0; }
.inspector-fit-all { margin-left: 0; }
.ver-entry {
  display: inline-flex; align-items: center; gap: 7px; height: 35px; padding: 0 11px;
  border: 1px solid var(--line); border-radius: 7px; background: #fff; color: var(--ink);
  font-family: inherit; cursor: pointer; transition: background .12s, border-color .12s;
}
.ver-entry:hover { background: #f3f6f5; border-color: #bdc8ca; }
.ver-tag { font-size: 10px; font-weight: 800; letter-spacing: .05em; color: var(--accent); }
.ver-name { font-weight: 700; font-size: 12px; max-width: 140px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ver-caret { color: var(--muted); font-size: 10px; }
.graph-entry { border-color: #cfe0dc; background: #f6fbf9; }
.brand {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 5px;
  padding-right: 14px;
  margin-right: 12px;
  border-right: 1px solid var(--line);
  min-width: 0;
}
.brand-icon {
  width: 34px; height: 34px; display: grid; place-items: center; border-radius: 7px;
  background: var(--accent); color: #fff; flex: 0 0 auto;
}
.brand-icon svg { width: 21px; height: 21px; }
.brand h1 { font-size: 12px; margin: 0; white-space: nowrap; }
/* 工具栏分组：左侧竖线划分 编辑 / 发布 两类 */
.grp { display: flex; align-items: center; gap: 6px; padding-left: 10px; border-left: 1px solid var(--line); }
.grp.selectors { border-left: none; padding-left: 0; }
.btn svg { width: 15px; height: 15px; flex: 0 0 auto; }
/* 状态并入 .tool-right 组内：不再需要左侧竖线分隔（间距由组内 gap 统一给） */
.status {
  font-size: 12px; color: var(--muted);
  display: flex; align-items: center; gap: 14px; white-space: nowrap;
}
.status .count b { color: var(--ink); font-variant-numeric: tabular-nums; }
.status .save { display: flex; align-items: center; gap: 6px; }
.dot { width: 8px; height: 8px; border-radius: 50%; background: #cbd5d1; display: inline-block; }
.dot.saved { background: var(--accent); }
.dot.unsaved { background: var(--amber); }
.dot.failed { background: #dc2626; }
.save.unsaved {
  background: #fff4e6; border: 1px solid #fcd9a8; color: #b45309;
  border-radius: 5px; padding: 3px 9px;
}
.wrap { flex: 1; display: flex; min-height: 0; }
.canvas {
  flex: 1; min-width: 0; position: relative;
  background-color: var(--canvas);
  background-image: linear-gradient(#e0e8e5 1px, transparent 1px), linear-gradient(90deg, #e0e8e5 1px, transparent 1px);
  background-size: 24px 24px; cursor: grab;
}
.canvas:active { cursor: grabbing; }
.canvas canvas { display: block; }
/* 连线模式面板：源/目标搜索选点 + 状态提示，与画布点击选点并存 */
.link-panel {
  position: absolute; left: 50%; bottom: 16px; transform: translateX(-50%);
  display: none; align-items: flex-start; gap: 8px; z-index: 5; flex-wrap: wrap;
  background: #fff; border: 1px solid #9db1ac; border-radius: 8px;
  box-shadow: 0 8px 20px rgba(20, 40, 38, .15); padding: 8px 12px;
  max-width: calc(100% - 28px);
}
.link-panel.show { display: flex; }
/* 确认连线按钮：源 + ≥1 个待连目标才可用 */
.lp-confirm { height: 30px; padding: 0 12px; font-size: 12px; flex: none; }
/* 待连目标列表：面板第二行，多选累积展示 */
.lp-targets {
  width: 100%; display: flex; flex-wrap: wrap; gap: 5px; align-items: center;
  padding-top: 7px; border-top: 1px dashed var(--line);
}
.lp-tlabel { font-size: 11px; font-weight: 600; color: var(--muted); flex: none; }
.lp-clear {
  border: 0; background: transparent; color: var(--muted); font-size: 11px;
  cursor: pointer; text-decoration: underline; font-family: inherit; padding: 0; flex: none;
}
.lp-clear:hover { color: var(--ink); }
/* 标签/箭头/提示都以输入框行（30px）为基准垂直居中——面板顶部对齐后第一行拉平 */
.lp-label { font-size: 11px; font-weight: 700; color: var(--muted); flex: none; height: 30px; display: inline-flex; align-items: center; }
.lp-slot { display: flex; flex-direction: column; gap: 6px; }
.lp-field { position: relative; }
/* 类型筛选行：全部/实体/属性/规则，收紧候选范围（默认全部） */
.lp-types { display: flex; gap: 3px; }
.lp-types button {
  border: 1px solid var(--line); background: #fff; border-radius: 9px; height: 18px;
  font-size: 10px; padding: 0 7px; line-height: 1; cursor: pointer; color: var(--muted);
  font-family: inherit; transition: background .12s, border-color .12s;
}
.lp-types button.on { background: var(--soft); border-color: var(--accent); color: var(--accent2); font-weight: 600; }
.lp-types button:disabled { opacity: .45; cursor: default; }
.lp-slot input {
  width: 150px; height: 30px; border: 1px solid var(--line); border-radius: 6px;
  background: #f1f4f3; padding: 0 9px; font-size: 12px; outline: none; font-family: inherit;
}
.lp-slot input:focus { background: #fff; border-color: var(--accent); }
.lp-slot input:disabled { opacity: .5; }
.lp-chip {
  display: inline-flex; align-items: center; gap: 6px; height: 30px; padding: 0 3px 0 9px;
  border: 1px solid; border-radius: 15px; font-size: 12px; background: #fff; max-width: 240px;
}
.lp-x {
  border: 0; background: transparent; cursor: pointer; color: var(--muted);
  font-size: 14px; width: 20px; height: 20px; border-radius: 4px; line-height: 1; flex: none;
}
.lp-x:hover { background: #e7ecea; color: var(--ink); }
.lp-arrow { color: var(--muted); font-size: 13px; flex: none; height: 30px; display: inline-flex; align-items: center; }
.lp-dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; flex: none; }
.lp-name { max-width: 150px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.lp-list {
  /* 面板贴在视口底部，下拉向上展开（挂在输入框上沿），向下会被屏幕裁掉选不到 */
  position: absolute; bottom: 34px; left: 0; min-width: 100%; max-height: 260px; overflow-y: auto;
  background: #fff; border: 1px solid var(--line); border-radius: 7px; list-style: none; margin: 0 0 4px;
  padding: 4px; box-shadow: 0 10px 24px rgba(20, 40, 38, .14); z-index: 6;
}
.lp-list li {
  display: flex; align-items: center; gap: 7px; padding: 6px 9px; border-radius: 5px;
  font-size: 12px; cursor: pointer; white-space: nowrap;
}
.lp-list li:hover { background: #f1f4f3; }
.lp-list li.on { background: var(--soft); }
/* 与源节点已连线的候选：置灰不可选，类型位换成「已连线」标注 */
.lp-list li.off { opacity: .45; }
.lp-list li.off:hover { background: #fff; }
.lp-type.linked { color: #b45309; font-weight: 600; }
/* 勾选框（纯 CSS 方块 + 对勾） */
.lp-check {
  width: 13px; height: 13px; border: 1.5px solid #b6c2c6; border-radius: 3px; flex: none;
  display: inline-flex; align-items: center; justify-content: center; font-size: 9px; color: transparent;
  transition: background .1s, border-color .1s;
}
.lp-check.on { background: var(--accent); border-color: var(--accent); color: #fff; }
.lp-check.on::after { content: '✓'; }
/* 候选列表底部操作行：全选 / 加入所选 */
.lp-foot {
  display: flex; gap: 6px; justify-content: center; cursor: default;
  border-top: 1px dashed var(--line); margin-top: 3px; padding-top: 7px; border-radius: 0 0 5px 5px;
}
.lp-foot-btn {
  border: 1px solid var(--line); background: #fff; border-radius: 5px; height: 22px;
  font-size: 11px; padding: 0 10px; cursor: pointer; color: var(--ink); font-family: inherit;
}
.lp-foot-btn:hover { background: #f1f4f3; }
.lp-foot-btn.primary { background: var(--accent); border-color: var(--accent); color: #fff; font-weight: 600; }
.lp-foot-btn.primary:hover { background: var(--accent2); }
.lp-foot-btn:disabled { opacity: .45; cursor: default; }
.lp-type { color: var(--muted); font-size: 10px; flex: none; }
/* 状态提示独占一行、允许换行——长文案（如带节点名的引导语）完整可见 */
.lp-tip {
  width: 100%; font-size: 11px; color: #184e43; line-height: 1.5;
  white-space: normal; flex: none; margin-top: 3px;
}
.shortcut-hint {
  position: absolute; left: 14px; bottom: 12px;
  font-size: 11px; color: var(--muted); opacity: .78;
  background: rgba(255, 255, 255, .88); border: 1px solid var(--line);
  padding: 4px 10px; border-radius: 5px;
  pointer-events: none; z-index: 4; white-space: nowrap; user-select: none;
}
.layout-undo {
  position: absolute; left: 50%; bottom: 14px; transform: translateX(-50%);
  display: flex; align-items: center; gap: 12px;
  font-size: 12px; color: #184e43; background: #fff;
  padding: 8px 14px; border-radius: 8px; border: 1px solid #9db1ac;
  box-shadow: 0 8px 20px rgba(20, 40, 38, .15); z-index: 6; white-space: nowrap;
}
.layout-undo .btn { height: 28px; padding: 0 12px; font-size: 12px; }
.modal.shortcuts { width: 430px; max-width: 92vw; }
.shortcut-table { width: 100%; border-collapse: collapse; font-size: 12.5px; margin-bottom: 4px; }
.shortcut-table td { padding: 7px 4px; border-bottom: 1px solid #eef2f1; vertical-align: middle; }
.shortcut-table tr:last-child td { border-bottom: none; }
.shortcut-table td:first-child { white-space: nowrap; width: 42%; }
.shortcut-table kbd {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 11.5px;
  background: var(--panel); border: 1px solid var(--line); border-bottom-width: 2px;
  border-radius: 5px; padding: 1px 7px; color: var(--ink); white-space: nowrap;
}
.inspector {
  width: 360px; background: #fff; border-left: 1px solid var(--line);
  overflow-y: auto; font-size: 13px; display: flex; flex-direction: column;
}
.panel-head { padding: 15px 16px 12px; background: #fff; border-bottom: 1px solid var(--line); }
.panel-head h2 { margin: 0; font-size: 15px; }
.eyebrow { display: block; color: var(--accent); font-size: 9px; font-weight: 800; letter-spacing: .04em; margin-bottom: 3px; }
.edge-hero {
  padding: 12px 14px; border-left: 4px solid var(--accent); border-radius: 6px;
  background: #edf7f3; margin: 12px 16px 0;
}
.edge-hero strong { font-size: 13px; line-height: 1.6; word-break: break-all; }
.rel-color { color: #87500f; }
.fields { padding: 13px 16px; }
.field { margin-bottom: 12px; }
.field:last-child { margin-bottom: 0; }
.field label { display: block; font-size: 11px; color: var(--muted); margin-bottom: 4px; font-weight: 700; }
.field input[type=text], .field textarea {
  width: 100%; padding: 7px 10px; border: 1px solid var(--line); border-radius: 6px;
  font-size: 12.5px; background: #fff; outline: none;
}
.field textarea { min-height: 70px; resize: vertical; line-height: 1.5; }
.field input:focus, .field textarea:focus { border-color: var(--accent); box-shadow: 0 0 0 2px var(--soft); }
.actions { display: flex; gap: 8px; padding: 13px 16px; margin-top: auto; }
.actions .btn { flex: 1; justify-content: center; }

/* ---- 常驻 inspector：未选中空提示 ---- */
.empty {
  min-height: 260px; display: grid; place-content: center; text-align: center;
  padding: 25px; color: var(--muted); font-size: 11px; line-height: 1.8;
}
.empty strong { display: block; color: var(--ink); font-size: 13px; margin-bottom: 5px; }

/* ---- 常驻 inspector：节点详情（对齐预览页） ---- */
.details { padding: 0 16px; }
.detail-hero { padding: 13px 0 14px; border-bottom: 1px solid var(--line); }
.detail-kicker { display: flex; gap: 7px; align-items: center; color: var(--muted); font-size: 10px; }
.detail-color { width: 9px; height: 9px; border-radius: 2px; background: var(--c, #999); }
.detail-hero h3 { font-size: 19px; line-height: 1.35; margin: 8px 0 0; overflow-wrap: anywhere; }
.detail-section { padding: 13px 0; border-bottom: 1px solid var(--line); }
.detail-section h4 { font-size: 10px; color: var(--muted); margin: 0 0 8px; }
.field-row { display: grid; grid-template-columns: 82px 1fr; gap: 7px 8px; font-size: 11px; padding: 3px 0; }
.f-label { color: var(--muted); }
.f-value { overflow-wrap: anywhere; white-space: pre-wrap; }
.f-value .line { line-height: 1.7; }
.mini-rel-list { display: flex; flex-wrap: wrap; gap: 5px; }
.mini-rel {
  display: inline-block; border: 1px solid var(--line); background: #fafcfc; border-radius: 5px;
  padding: 4px 6px; font-size: 9.5px; color: #38504e;
}
.mini-rel .rel { font-weight: 700; color: #1d594d; }
.note { font-size: 10px; color: var(--muted); line-height: 1.8; margin: 0; }

/* ---- 边 hover tooltip（fixed 跟随边中点） ---- */
.edge-tip {
  position: fixed; transform: translate(-50%, -100%);
  background: #12332c; color: #fff; font-size: 11px; line-height: 1.5;
  padding: 5px 9px; border-radius: 5px; max-width: 260px;
  pointer-events: none; z-index: 60; white-space: pre-wrap; word-break: break-word;
}
</style>
