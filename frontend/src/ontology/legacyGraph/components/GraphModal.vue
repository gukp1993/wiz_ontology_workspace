<template>
  <div class="modal-mask" :class="{ show }">
    <div class="modal graphs">
      <h3>本体管理</h3>
      <div class="new-row">
        <button class="btn primary" title="新建一个空白本体" @click="openNew">新建本体</button>
      </div>
      <div class="list">
        <p v-if="error" class="err" :class="{ show: !!error }">{{ error }}</p>
        <div v-if="loading" class="empty">加载中…</div>
        <template v-else-if="ontologies.length">
          <div v-for="g in ontologies" :key="g.id" class="row" :class="{ current: g.id === currentId }">
            <button class="gname" @click="pick(g)">
              {{ g.name }}
              <span v-if="g.id === currentId" class="cur-tag">当前</span>
            </button>
          </div>
        </template>
        <div v-else class="empty">暂无本体，先新建一个</div>
      </div>
      <p class="err" :class="{ show: !!error }">{{ error }}</p>
      <div class="btns">
        <button class="btn primary" @click="emit('close')">关闭</button>
      </div>
    </div>

    <!-- 新建图谱命名弹窗 -->
    <div class="modal-mask" :class="{ show: showNew }">
      <div class="modal">
        <h3>新建图谱</h3>
        <div class="field">
          <label>本体名称</label>
          <input v-model="newName" type="text" placeholder="如「储能本体」" autocomplete="off" @keydown.enter="create" />
        </div>
        <p class="err" :class="{ show: !!error }">{{ error }}</p>
        <div class="btns">
          <button class="btn" @click="showNew = false">取消</button>
          <button class="btn primary" @click="create">保存</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, watch } from 'vue'
import { listOntologies } from '../../api'

// 适配：图谱管理 → 本体管理。列表来自当前账号的本体接口；切换/新建经宿主
// （ObjectWorkspace→App switchOntology/createOntology，保留未保存守卫），删除不在本期范围。
const props = defineProps({ show: { type: Boolean, required: true }, currentId: { type: String, default: '' } })
const emit = defineEmits(['close', 'switch', 'create'])
const ontologies = ref([])
const loading = ref(false)
const error = ref('')
const newName = ref('')
const showNew = ref(false)

watch(() => props.show, async (on) => {
  if (!on) return
  error.value = ''
  loading.value = true
  try {
    const data = await listOntologies()
    ontologies.value = data?.items || []
  } catch (e) {
    error.value = e.message || '本体列表加载失败'
  } finally { loading.value = false }
})

function openNew() {
  newName.value = ''
  error.value = ''
  showNew.value = true
}

function create() {
  const n = newName.value.trim()
  if (!n) {
    error.value = '请输入本体名称'
    return
  }
  emit('create', n)
  showNew.value = false
}

function pick(g) {
  if (g.id === props.currentId) {
    emit('close')
    return
  }
  emit('switch', g.id)
  emit('close')
}
</script>

<style scoped>
.graphs.modal { width: 640px; max-width: 94vw; }
.new-row { display: flex; gap: 8px; margin-bottom: 12px; }
.list { max-height: 46vh; overflow-y: auto; }
.row {
  display: flex; align-items: center; gap: 10px; padding: 10px 2px;
  border-bottom: 1px solid var(--line);
}
.row:last-child { border-bottom: none; }
.row.current { background: #f4faf8; border-radius: 6px; padding-left: 8px; }
.gname {
  flex: 1; min-width: 0; text-align: left; border: 0; background: transparent; cursor: pointer;
  font-size: 13px; font-weight: 700; font-family: inherit; color: var(--ink); padding: 4px 0;
  word-break: break-all;
}
.gname:hover { color: var(--accent); }
.cur-tag {
  font-size: 10px; font-weight: 700; color: var(--accent); border: 1px solid var(--accent);
  border-radius: 4px; padding: 1px 5px; margin-left: 6px; vertical-align: 1px;
}
.gmeta { color: var(--muted); font-size: 11px; flex: 0 0 auto; }
.empty { padding: 14px 2px; color: var(--muted); font-size: 12px; }
</style>
