<template>
  <div class="mpv">
    <header class="topbar">
      <div class="brand">
        <div class="brand-icon">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="5" cy="6" r="2.2" /><circle cx="5" cy="18" r="2.2" /><circle cx="12" cy="12" r="2.2" /><circle cx="19.5" cy="7" r="2.2" /><circle cx="19.5" cy="17" r="2.2" />
            <path d="M6.8 7 10.3 10.8M6.8 17l3.5-3.8M13.8 11l4-2.8M13.8 13l4 2.8" />
          </svg>
        </div>
        <div>
          <h1>多图谱预览</h1>
          <p>{{ statsText }}</p>
        </div>
      </div>
      <div class="search">
        <svg fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
          <circle cx="11" cy="11" r="7"></circle><path d="m20 20-3.4-3.4"></path>
        </svg>
        <input v-model="q" type="text" autocomplete="off" placeholder="搜索节点名称（跨图谱）…" />
        <button class="search-clear" :class="{ hidden: !q }" title="清除" @click="q = ''">×</button>
      </div>
      <div class="actions">
        <button class="btn" :disabled="!groups.length" @click="expandAll">全部展开</button>
        <button class="btn" :disabled="!groups.length" @click="collapseAll">全部折叠</button>
        <i class="action-divider"></i>
        <button class="icon-btn" title="适应窗口" @click="fitView">
          <svg fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M8 3H3v5M16 3h5v5M8 21H3v-5M16 21h5v-5"></path></svg>
        </button>
        <button class="btn" @click="pickOpen = true">重新选择</button>
        <button class="btn primary" @click="$emit('back')">← 返回编辑</button>
      </div>
    </header>

    <div class="workspace">
      <main class="center">
        <div class="viewport">
          <div ref="canvasEl" class="canvas"></div>
          <div v-if="loading" class="overlay">加载中…</div>
          <div v-else-if="loadError" class="overlay error">{{ loadError }}</div>
          <div class="legend">
            <span><i class="lg-node" style="border-color: #14705d; background: #eef7f3"></i>图谱</span>
            <span><i class="lg-badge">＋</i>展开 / <i class="lg-badge" style="background:#e8801a">−</i>折叠</span>
            <span><i class="lg-node" style="border-color: #3978c5"></i>实体</span>
            <span><i class="lg-node" style="border-color: #d15e9a"></i>属性</span>
            <span><i class="lg-node" style="border-color: #35a167"></i>规则</span>
            <span><i class="lg-dash"></i>所属（软连接）</span>
          </div>
          <div class="hint">每个节点 = 一份「图谱 + 版本」 · 点 ＋ 展开、点 − 折叠 · 节点可拖动</div>
        </div>
      </main>

      <aside class="inspector">
        <div class="panel-head"><span class="eyebrow">INSPECTOR</span><h2>详情</h2></div>
        <div v-if="!selected" class="empty">
          <div><strong>选择一个节点或关系</strong>点击根节点旁的 ＋ 徽标展开整份图谱（− 折叠）；点击节点查看详情字段与直接关系；节点可拖动。</div>
        </div>

        <!-- 图谱根节点 -->
        <article v-else-if="selected.kind === 'root'" class="details">
          <div class="detail-hero">
            <div class="detail-kicker"><i class="detail-color" style="background: #14705d"></i><span>图谱 · {{ selected.group.expanded ? '已展开' : '已折叠' }}</span></div>
            <h3>{{ selected.group.title }}</h3>
          </div>
          <div class="detail-section">
            <h4>统计</h4>
            <p class="stats-line">{{ selected.group.statsText }}</p>
          </div>
          <div class="detail-section row-actions">
            <button class="btn" @click="toggleGroup(selected.groupKey)">{{ selected.group.expanded ? '折叠' : '展开' }}该图谱</button>
            <button class="btn danger" @click="removeGroup(selected.groupKey)">移除该组</button>
          </div>
          <div class="detail-section"><p class="note">画布上点击根节点旁的 ＋/− 徽标展开 / 折叠；根节点可拖动（展开后拖动带动整组）。</p></div>
        </article>

        <!-- 语义节点 -->
        <article v-else-if="selected.kind === 'node'" class="details">
          <div class="detail-hero">
            <div class="detail-kicker">
              <i class="detail-color" :style="{ background: TYPE_COLOR[selected.node.type] }"></i>
              <span>{{ selected.node.type }}节点</span>
            </div>
            <h3>{{ selected.node.name }}</h3>
          </div>
          <template v-if="entries.length">
            <div class="detail-section">
              <h4>详情字段</h4>
              <div v-for="f in entries" :key="f.key" class="field-row">
                <div class="f-label">{{ f.label }}</div>
                <div class="f-value">
                  <template v-if="f.isList">
                    <div v-for="(item, i) in f.value" :key="i" class="line">{{ item }}</div>
                  </template>
                  <span v-else>{{ f.value }}</span>
                </div>
              </div>
            </div>
          </template>
          <div class="detail-section">
            <h4>直接关系</h4>
            <div v-if="selected.relations.length" class="mini-rel-list">
              <span v-for="r in selected.relations" :key="r.id" class="mini-rel">
                <template v-if="r.dir === 'out'"><span class="rel">{{ r.label }}</span> → {{ r.name }}</template>
                <template v-else>{{ r.name }} → <span class="rel">{{ r.label }}</span></template>
              </span>
            </div>
            <p v-else class="note">（无关联连线）</p>
          </div>
        </article>

        <!-- 关系边 -->
        <article v-else class="details">
          <div class="edge-hero">
            <strong>{{ selected.edge.sourceName }} —<span class="rel">{{ selected.edge.label }}</span>→ {{ selected.edge.targetName }}</strong>
          </div>
          <div class="detail-section">
            <h4>关系说明</h4>
            <p>{{ selected.edge.description || '（未填写描述）' }}</p>
          </div>
        </article>
      </aside>
    </div>

    <MultiPickModal :show="pickOpen" :preselect="selectedIds" @close="pickOpen = false" @confirm="onPicked" />
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { useMultiGraph } from './composables/useMultiGraph'
import { TYPE_COLOR } from './shared/constants'
import { fieldEntries } from './shared/fields'
import MultiPickModal from './components/MultiPickModal.vue'

// 适配：旧路由 query items → props.items（`本体ID@版本` 数组）；返回/重选向编辑器发事件。
const props = defineProps({
  items: { type: Array, default: () => [] },
})
const _emit = defineEmits(['back', 'repick'])
const canvasEl = ref(null)
const q = ref('')
const pickOpen = ref(false)
const selectedIds = ref([])

const {
  groups, loading, loadError, selected,
  initCanvas, loadItems, toggleGroup, expandAll, collapseAll, removeGroup,
  setQuery, fitView,
} = useMultiGraph()

const entries = computed(() =>
  selected.value?.kind === 'node' ? fieldEntries(selected.value.node.type, selected.value.node.data) : [],
)
const statsText = computed(() => {
  const n = groups.value.length
  const expanded = groups.value.filter((g) => g.expanded).length
  if (!n) return '尚未选择图谱版本'
  return `图谱版本 ${n} 份 · 展开 ${expanded} 份`
})

watch(q, setQuery)

function onPicked(ids) {
  pickOpen.value = false
  if (ids.join(',') === selectedIds.value.join(',')) return
  selectedIds.value = ids
  loadItems(ids)
}

// items 变化（首次进入 / 重新选择）→ 加载对应发布版本
watch(
  () => props.items,
  (items) => {
    selectedIds.value = [...(items || [])]
    if (selectedIds.value.length) loadItems(selectedIds.value)
  },
  { immediate: true },
)

onMounted(() => {
  initCanvas(canvasEl.value)
  if (selectedIds.value.length) loadItems(selectedIds.value)
})
</script>

<style scoped>
.mpv { height: 100%; display: grid; grid-template-rows: 58px minmax(0, 1fr); }
.topbar {
  display: grid;
  grid-template-columns: 300px minmax(260px, 520px) 1fr;
  gap: 16px;
  align-items: center;
  padding: 8px 14px;
  background: #fff;
  border-bottom: 1px solid var(--line);
  z-index: 20;
}
.brand { display: flex; gap: 11px; align-items: center; min-width: 0; }
.brand-icon {
  width: 36px; height: 36px; display: grid; place-items: center; border-radius: 7px;
  background: var(--accent); color: #fff; flex: 0 0 auto;
}
.brand-icon svg { width: 22px; height: 22px; }
.brand h1 { font-size: 15px; margin: 0 0 3px; white-space: nowrap; }
.brand p { margin: 0; color: var(--muted); font-size: 11px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.search { position: relative; }
.search input {
  width: 100%; height: 36px; border: 1px solid transparent; border-radius: 7px; background: #f1f4f3;
  padding: 0 36px; outline: none; font-size: 12px;
}
.search input:focus { background: #fff; border-color: var(--accent); box-shadow: 0 0 0 3px var(--soft); }
.search > svg { position: absolute; left: 11px; top: 10px; width: 16px; height: 16px; color: var(--muted); }
.search-clear {
  position: absolute; right: 5px; top: 5px; width: 26px; height: 26px; border: 0; background: transparent;
  border-radius: 5px; cursor: pointer; color: var(--muted); font-size: 15px; line-height: 1;
}
.search-clear:hover { background: #e7ecea; }
.actions { display: flex; align-items: center; justify-content: flex-end; gap: 7px; min-width: 0; }
.action-divider { width: 1px; height: 22px; background: var(--line); margin: 0 2px; flex: 0 0 auto; }
.icon-btn {
  width: 33px; height: 33px; border: 1px solid var(--line); background: #fff; border-radius: 6px;
  display: grid; place-items: center; cursor: pointer; color: #42514e; padding: 0;
}
.icon-btn:hover { background: #f3f6f5; border-color: #bdc8ca; }
.icon-btn svg { width: 16px; height: 16px; }

.workspace { min-height: 0; display: grid; grid-template-columns: minmax(0, 1fr) 320px; }
.center { min-width: 0; min-height: 0; }
.viewport {
  position: relative; width: 100%; height: 100%; overflow: hidden; cursor: grab;
  background-color: var(--canvas);
  background-image: linear-gradient(#e6edea 1px, transparent 1px), linear-gradient(90deg, #e6edea 1px, transparent 1px);
  background-size: 26px 26px;
}
.viewport:active { cursor: grabbing; }
.canvas { width: 100%; height: 100%; }
.canvas canvas { display: block; }
.overlay {
  position: absolute; inset: 0; display: grid; place-content: center;
  background: rgba(248, 250, 249, 0.86); color: var(--muted); font-size: 13px; z-index: 15; text-align: center;
}
.overlay.error { color: var(--danger, #a3402f); }
.legend {
  position: absolute; left: 10px; bottom: 10px; display: flex; gap: 10px; flex-wrap: wrap; max-width: 70%;
  padding: 6px 9px; background: rgba(255, 255, 255, 0.93); border: 1px solid var(--line);
  border-radius: 5px; color: var(--muted); font-size: 9.5px; pointer-events: none; z-index: 10;
}
.legend span { display: flex; align-items: center; gap: 5px; }
.lg-node { width: 17px; height: 11px; border-radius: 4px; border: 2px solid #3978c5; background: #fff; background-clip: padding-box; }
.lg-badge {
  display: inline-grid; place-items: center; width: 14px; height: 14px; border-radius: 50%;
  background: #14705d; color: #fff; font-size: 9px; font-weight: 800; font-style: normal; line-height: 1;
}
.lg-dash { width: 20px; border-top: 2px dashed #8fa8a2; }
.hint {
  position: absolute; right: 10px; bottom: 10px; padding: 6px 9px;
  background: rgba(255, 255, 255, 0.93); border: 1px solid var(--line); border-radius: 5px;
  color: var(--muted); font-size: 9.5px; pointer-events: none; z-index: 10;
}

.inspector { border-left: 1px solid var(--line); background: #fff; overflow: auto; }
.panel-head { position: sticky; top: 0; z-index: 5; padding: 14px 15px 11px; background: #fff; border-bottom: 1px solid var(--line); }
.eyebrow { display: block; color: var(--accent); font-size: 9px; font-weight: 800; letter-spacing: 0.04em; margin-bottom: 3px; }
.panel-head h2 { margin: 0; font-size: 15px; }
.empty { min-height: 260px; display: grid; place-content: center; text-align: center; padding: 25px; color: var(--muted); font-size: 11px; line-height: 1.8; }
.empty strong { display: block; color: var(--ink); font-size: 13px; margin-bottom: 5px; }
.details { padding: 15px; }
.detail-hero { padding-bottom: 13px; border-bottom: 1px solid var(--line); }
.detail-kicker { display: flex; gap: 7px; align-items: center; color: var(--muted); font-size: 10px; }
.detail-color { width: 9px; height: 9px; border-radius: 2px; }
.detail-hero h3 { font-size: 19px; line-height: 1.35; margin: 8px 0 4px; overflow-wrap: anywhere; }
.detail-section { padding: 12px 0; border-bottom: 1px solid var(--line); }
.detail-section h4 { font-size: 10px; color: var(--muted); margin: 0 0 8px; }
.detail-section p { font-size: 12px; line-height: 1.75; margin: 0; white-space: pre-wrap; }
.stats-line { color: var(--accent2); font-weight: 600; }
.row-actions { display: flex; gap: 8px; flex-wrap: wrap; border-bottom: none; }
.note { color: var(--muted); font-size: 10.5px; }
.field-row { display: grid; grid-template-columns: 82px 1fr; gap: 7px 8px; font-size: 11px; padding: 3px 0; }
.f-label { color: var(--muted); }
.f-value { overflow-wrap: anywhere; white-space: pre-wrap; }
.f-value .line { line-height: 1.7; }
.mini-rel-list { display: flex; flex-wrap: wrap; gap: 5px; }
.mini-rel {
  display: inline-block; border: 1px solid var(--line); background: #fafcfc; border-radius: 5px;
  padding: 4px 6px; font-size: 9.5px; color: #38504e;
}
.mini-rel .rel, .edge-hero .rel { font-weight: 700; color: #87500f; }
.edge-hero { padding: 12px; border-left: 4px solid var(--accent); border-radius: 6px; background: var(--soft); margin-bottom: 12px; }
.edge-hero strong { font-size: 13px; line-height: 1.6; word-break: break-all; }
.hidden { display: none !important; }
</style>// items 变化（首次进入 / 重新选择）→ 加载对应发布版本
watch(
  () => props.items,
  (items) => {
    selectedIds.value = [...(items || [])]
    if (selectedIds.value.length) loadItems(selectedIds.value)
  },
  { immediate: true },
)
onMounted(() => initCanvas())


