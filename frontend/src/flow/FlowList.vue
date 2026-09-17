<!-- FlowList — 编排列表页：名称/说明/节点数/更新时间/配置状态，搜索、新建（仅名称，
     说明选填）、编辑、复制、删除（软删除，明确确认）。不依赖当前选中的本体或项目。 -->
<script setup lang="ts">
import { computed, ref } from 'vue'
import { createFlow, copyFlow, deleteFlow, listFlows } from './api'
import { formatTime } from './flowModel'
const emit = defineEmits(['open', 'created', 'deleted', 'copied', 'navigate'])
const items = ref<any[]>([])
const loading = ref(false)
const error = ref('')
const search = ref('')
const showCreate = ref(false)
const newName = ref(''), newDescription = ref('')
const busy = ref(false)
const showDeleted = ref(false)
async function refresh() {
  loading.value = true; error.value = ''
  try { items.value = (await listFlows(showDeleted.value)).items }
  catch (e: any) { error.value = e?.message || '加载编排列表失败' }
  finally { loading.value = false }
}
refresh()
const filtered = computed(() => {
  const q = search.value.trim().toLowerCase()
  return q ? items.value.filter((i: any) => (i.name + ' ' + i.description).toLowerCase().includes(q)) : items.value
})
async function create() {
  if (busy.value || !newName.value.trim()) return
  busy.value = true
  try {
    const created = await createFlow({ name: newName.value.trim(), description: newDescription.value.trim() })
    showCreate.value = false; newName.value = ''; newDescription.value = ''
    emit('created', created)
  } catch (e: any) { error.value = e?.message || '创建失败' }
  finally { busy.value = false }
}
async function copy(item: any) {
  if (busy.value) return
  busy.value = true
  try {
    const created = await copyFlow(item.id)
    emit('copied', created)
    await refresh()
  } catch (e: any) { error.value = e?.message || '复制失败' }
  finally { busy.value = false }
}
async function remove(item: any) {
  if (busy.value) return
  if (!confirm(`删除编排「${item.name}」？\n采用软删除：修订历史保留，可由管理员恢复；列表默认不再显示。`)) return
  busy.value = true
  try {
    await deleteFlow(item.id)
    emit('deleted', item.id)
    await refresh()
  } catch (e: any) { error.value = e?.message || '删除失败' }
  finally { busy.value = false }
}
</script>
<template>
<section class="card">
  <div class="manager-heading">
    <div>
      <p>把 Python 与 SQL 处理节点编排成可复用的取数与加工流程。第一期只做定义与配置检查，不执行代码或 SQL；与项目取值规则库相互独立。</p>
    </div>
    <button class="primary" @click="showCreate=true">＋ 新建编排</button>
  </div>
  <p v-if="error" class="inline-error" role="alert">{{error}}</p>
  <div class="library-toolbar">
    <label class="list-search" style="margin:0">搜索编排
      <input v-model="search" type="search" aria-label="搜索编排" placeholder="按名称或说明搜索"/>
    </label>
    <label class="check-option" style="margin:0">
      <input v-model="showDeleted" type="checkbox" @change="refresh"/>
      显示已删除
    </label>
    <button :disabled="loading" @click="refresh">{{loading?'加载中…':'刷新'}}</button>
  </div>
  <div class="scroll">
    <table>
      <thead><tr><th>名称</th><th>说明</th><th>节点数</th><th>更新时间</th><th>配置状态</th><th>操作</th></tr></thead>
      <tbody>
        <tr v-for="item in filtered" :key="item.id" :class="{deleted:item.status==='deleted'}">
          <td class="prop-name">{{item.name}}<small v-if="item.status==='deleted'" class="muted">（已删除）</small></td>
          <td><span class="muted">{{item.description||'—'}}</span></td>
          <td>{{item.nodeCount}}</td>
          <td>{{formatTime(item.updatedAt)}}</td>
          <td>
            <span v-if="item.status==='deleted'" class="muted">—</span>
            <span v-else-if="item.configStatus==='passed'" class="property-pill">检查通过</span>
            <span v-else class="property-pill" style="background:var(--warn-soft);color:var(--warn)">待完善<template v-if="item.errorCount"> · {{item.errorCount}} 个问题</template></span>
          </td>
          <td>
            <button class="mini" :disabled="busy||item.status==='deleted'" @click="emit('open',item.id)">编辑</button>
            <button class="mini" :disabled="busy||item.status==='deleted'" @click="copy(item)">复制</button>
            <button class="mini" :disabled="busy" @click="remove(item)">删除</button>
          </td>
        </tr>
        <tr v-if="!filtered.length && !loading"><td colspan="6" class="empty">{{search?'没有匹配的编排':'还没有编排；点击右上角「新建编排」开始'}}</td></tr>
      </tbody>
    </table>
  </div>
</section>
<div v-if="showCreate" class="modal-backdrop" @click.self="showCreate=false">
  <form class="modal-card" role="dialog" aria-modal="true" aria-label="新建编排" @submit.prevent="create">
    <h2>新建编排</h2>
    <p class="field-help">从空白编排开始：默认包含不可删除的「编排输入」「编排输出」边界节点。</p>
    <label>名称 *<input v-model="newName" required maxlength="80" placeholder="例如：SOC 日采样计算流程"/></label>
    <label>说明<textarea v-model="newDescription" rows="2" placeholder="这个编排做什么（选填）"/></label>
    <div class="dialogtools">
      <button type="button" @click="showCreate=false">取消</button>
      <button type="submit" class="primary" :disabled="busy||!newName.trim()">创建</button>
    </div>
  </form>
</div>
</template>
<style scoped>
tr.deleted td{opacity:.55}
.list-search input{margin-top:5px}
</style>
