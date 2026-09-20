<template>
  <div class="modal-mask" :class="{ show }" @click.self="close">
    <div class="modal multi-pick">
      <h3>多本体预览 · 选择本体与发布版本</h3>
      <p class="tip">从各本体的发布版本中多选，所选版本将在一个画布上并列只读预览。</p>

      <div class="list">
        <div v-if="loading" class="empty">加载中…</div>
        <div v-else-if="!graphs.length" class="empty">暂无本体，请先创建本体并发布</div>
        <template v-else>
          <div v-for="g in graphs" :key="g.id" class="graph-block">
            <div class="graph-row">
              <label class="gcheck" :title="allChecked(g) ? '取消全选' : '全选该图谱全部版本'">
                <input type="checkbox" :checked="allChecked(g)" :indeterminate.prop="someChecked(g) && !allChecked(g)" @change="toggleGraph(g)" />
                <strong>{{ g.name }}</strong>
              </label>
              <button class="btn exp" :title="expanded[g.id] ? '收起发布版本' : '展开发布版本'" @click="expanded[g.id] = !expanded[g.id]">
                {{ expanded[g.id] ? '▾' : '▸' }} 发布版本（{{ g.versions.length }}）
              </button>
            </div>
            <div v-show="expanded[g.id]" class="versions">
              <label v-for="v in g.versions" :key="v.key" class="vcheck">
                <input type="checkbox" :value="v.key" v-model="selected" />
                <span>{{ v.name }}</span>
                <i v-if="isCurrent(g, v)" class="cur" title="编辑器当前正在编辑的版本">当前</i>
              </label>
              <div v-if="!g.versions.length" class="no-ver">（该本体暂无发布版本）</div>
            </div>
          </div>
        </template>
      </div>

      <div class="btns">
        <span class="count">已选 {{ selected.length }} 个版本</span>
        <span class="flex"></span>
        <button class="btn" @click="close">取消</button>
        <button class="btn primary" :disabled="!selected.length" @click="confirm">开始预览</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, watch } from 'vue'
import { listOntologies, listReleases } from '../../api'

const props = defineProps({
  show: { type: Boolean, required: true },
  preselect: { type: Array, default: () => [] },
})
const emit = defineEmits(['close', 'confirm'])

const loading = ref(false)
const graphs = ref([]) // [{id, name, versions:[{key,name}]}]
const expanded = ref({})
const selected = ref([]) // 选中项：`本体ID@发布版本名`

async function load() {
  loading.value = true
  try {
    const data = await listOntologies()
    const list = data?.items || []
    const withVers = await Promise.all(
      list.map(async (g) => {
        let versions = []
        try {
          const rel = await listReleases(g.id)
          versions = (rel?.items || []).map((r) => ({ key: g.id + '@' + r.version, name: r.version }))
        } catch (e) { /* 单个本体失败不阻塞 */ }
        return { id: g.id, name: g.name, versions }
      }),
    )
    graphs.value = withVers
    // 有已选版本的图谱自动展开；无已选的展开第一个有版本的图谱
    const touched = new Set()
    withVers.forEach((g) => {
      if (g.versions.some((v) => selected.value.includes(v.key))) touched.add(g.id)
    })
    if (!touched.size) {
      const first = withVers.find((g) => g.versions.length)
      if (first) touched.add(first.id)
    }
    expanded.value = Object.fromEntries(withVers.map((g) => [g.id, touched.has(g.id)]))
  } finally {
    loading.value = false
  }
}

watch(
  () => props.show,
  (v) => {
    if (v) {
      selected.value = [...props.preselect]
      load()
    }
  },
)

const allChecked = (g) => g.versions.length > 0 && g.versions.every((v) => selected.value.includes(v.key))
const someChecked = (g) => g.versions.some((v) => selected.value.includes(v.key))
function toggleGraph(g) {
  const ids = g.versions.map((v) => v.key)
  if (allChecked(g)) selected.value = selected.value.filter((id) => !ids.includes(id))
  else selected.value = [...new Set([...selected.value, ...ids])]
}
const isCurrent = (g, v) => props.preselect.includes(v.key)

function close() {
  emit('close')
}
function confirm() {
  if (!selected.value.length) return
  emit('confirm', [...selected.value])
}
</script>

<style scoped>
.multi-pick { width: 520px; max-height: 82vh; display: flex; flex-direction: column; }
.tip { margin: -6px 0 12px; color: var(--muted); font-size: 11.5px; line-height: 1.7; }
.list { flex: 1; min-height: 120px; overflow: auto; border: 1px solid var(--line); border-radius: 7px; padding: 4px 10px; background: #fff; }
.empty { padding: 30px 0; text-align: center; color: var(--muted); font-size: 12px; }
.graph-block { border-bottom: 1px dashed var(--line); padding: 8px 0; }
.graph-block:last-child { border-bottom: 0; }
.graph-row { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.gcheck { display: flex; align-items: center; gap: 8px; cursor: pointer; min-width: 0; }
.gcheck strong { font-size: 13px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.btn.exp { height: 26px; padding: 0 9px; font-size: 11px; flex: 0 0 auto; }
.versions { padding: 4px 0 4px 24px; display: grid; gap: 3px; }
.vcheck { display: flex; align-items: center; gap: 8px; font-size: 12px; cursor: pointer; min-height: 26px; color: var(--ink); }
.vcheck span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.cur { font-style: normal; font-size: 10px; color: var(--accent2); border: 1px solid var(--accent); border-radius: 4px; padding: 0 4px; flex: 0 0 auto; }
.no-ver { color: var(--muted); font-size: 11px; padding: 2px 0; }
.btns { display: flex; align-items: center; gap: 8px; margin-top: 12px; }
.count { color: var(--muted); font-size: 11.5px; }
.flex { flex: 1; }
input[type='checkbox'] { accent-color: var(--accent); }
</style>
