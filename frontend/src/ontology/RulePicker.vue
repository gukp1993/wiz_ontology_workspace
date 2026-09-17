<!-- 为对象添加规则的多选弹窗（20260917 一期，原型 pickRules）：
     搜索规则名称、勾选多项；搜索变化不丢失已勾选项；已引用规则不进可选列表（父组件过滤）。
     20260917 浅色改版：外层对齐全局弹窗（.modal-backdrop + .modal-card dialog-md，620px），
     底部按钮区用全局 .dialogtools（取消 / 添加所选规则）；空态用全局 .empty-state；
     已勾选时取消/关闭需确认放弃，不写任何数据。 -->
<script setup lang="ts">
import { computed, ref } from 'vue'
import { appConfirm } from '../shared/appConfirm'

const props = defineProps<{ rules: any[]; objectName: string }>()
const emit = defineEmits(['close', 'confirm'])
const query = ref('')
const chosen = ref(new Set<string>())

const visible = computed(() => props.rules
  .filter((r: any) => (r.name || '').toLowerCase().includes(query.value.trim().toLowerCase())))
function toggle(id: string, event: Event) {
  const next = new Set(chosen.value)
  if ((event.target as HTMLInputElement).checked) next.add(id)
  else next.delete(id)
  chosen.value = next
}
async function close() {
  if (chosen.value.size && !(await appConfirm({ title: '放弃勾选的规则？', message: '已勾选的规则尚未添加，关闭将放弃这些勾选。', danger: false }))) return
  emit('close')
}
function confirmPick() {
  if (!chosen.value.size) return
  emit('confirm', [...chosen.value])
}
</script>
<template>
<div class="modal-backdrop" @click.self="close">
  <section class="modal-card dialog-md" role="dialog" aria-modal="true" :aria-label="'为' + objectName + '添加规则'">
    <h2>为{{ objectName }}添加规则</h2>
    <input type="search" v-model="query" placeholder="搜索规则名称" aria-label="搜索可添加规则">
    <div class="rp-list">
      <label v-for="r in visible" :key="r.id" class="rp-row">
        <input type="checkbox" :checked="chosen.has(r.id)" :aria-label="r.name" @change="toggle(r.id, $event)">
        <span><strong>{{ r.name || '未命名规则' }}</strong><small class="muted">{{ r.description || '' }}</small></span>
      </label>
      <div v-if="!visible.length" class="empty-state">
        <span aria-hidden="true">◇</span>
        <p>{{ query ? '没有匹配的规则。' : '没有可添加的规则。' }}</p>
        <small>{{ query ? '换个关键词，或先到「业务规则」页新建。' : '先到「业务规则」页新建规则，再回到这里添加。' }}</small>
      </div>
    </div>
    <div class="dialogtools">
      <button type="button" @click="close">取消</button>
      <button type="button" class="primary" :disabled="!chosen.size" @click="confirmPick">添加所选规则{{ chosen.size ? '（已选 ' + chosen.size + ' 项）' : '' }}</button>
    </div>
  </section>
</div>
</template>
<style scoped>
/* 卡片外观/按钮区走全局 .modal-card/.dialogtools；这里只留规则列表排版。 */
.rp-list{max-height:45vh;overflow:auto;margin-top:10px}
.rp-row{display:flex;gap:10px;padding:12px 2px;border-bottom:1px solid var(--line);align-items:flex-start}
.rp-row input[type=checkbox]{width:auto;margin-top:4px}
.rp-row strong{font-size:14px}
.rp-row small{display:block}
</style>
