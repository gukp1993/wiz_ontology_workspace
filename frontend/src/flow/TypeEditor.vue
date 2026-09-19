<!-- TypeEditor — 数据类型声明编辑器（递归）。协议见 flowModel.ts：
     文本/数值/是-否/日期时间 为标量；对象带字段（稳定 ID + 技术/显示名分离）；
     列表声明元素类型。对象嵌套上限 3 层（与后端 flows.MAX_TYPE_DEPTH 镜像）。
     直接原地修改传入的 decl 对象；每次修改 emit('before-change') → 改 → emit('changed')。 -->
<script setup lang="ts">
import { computed } from 'vue'
import AppSelect from '../shared/AppSelect.vue'
import { TYPE_LABELS, TYPE_OPTIONS, uid } from './flowModel'
const props = defineProps<{ decl: any; depth?: number; disabled?: boolean }>()
const emit = defineEmits(['before-change', 'changed'])
const depth = computed(() => props.depth ?? 0)
const options = computed(() => TYPE_OPTIONS.map(o => (o.value === 'object' && depth.value >= 3) ? { ...o, disabled: true } : o))
function changeType(type: string) {
  emit('before-change')
  // 按目标类型清理：object 只带 fields、list 只带 elementType、标量两者都不带（原实现按当前类型判断，切换会残留矛盾键）
  props.decl.type = type
  if (type === 'object') {
    if (!Array.isArray(props.decl.fields)) props.decl.fields = []
    delete props.decl.elementType
  } else if (type === 'list') {
    if (props.decl.elementType === undefined || props.decl.elementType === null) props.decl.elementType = { type: 'text' }
    delete props.decl.fields
  } else {
    delete props.decl.fields
    delete props.decl.elementType
  }
  emit('changed')
}
function addField() {
  emit('before-change')
  if (!Array.isArray(props.decl.fields)) props.decl.fields = []
  props.decl.fields.push({ id: uid(), name: '', label: '', type: { type: 'text' } })
  emit('changed')
}
function removeField(index: number) {
  emit('before-change')
  props.decl.fields.splice(index, 1)
  emit('changed')
}
function setField(row: any, key: 'name' | 'label', value: string) {
  emit('before-change')
  row[key] = value
  emit('changed')
}
</script>
<template>
<div class="type-editor">
  <AppSelect :model-value="decl.type" :options="options" :disabled="disabled" :aria-label="'数据类型'" @before-change="emit('before-change')" @update:model-value="changeType($event as string)"/>
  <div v-if="decl.type==='object'" class="type-object">
    <p v-if="!(decl.fields||[]).length" class="muted">对象尚未声明字段；可先保存草稿，稍后补齐。</p>
    <div v-for="(field,index) in decl.fields||[]" :key="field.id || index" class="type-field">
      <div class="type-field-head">
        <input class="cell-input" :value="field.name" :disabled="disabled" placeholder="技术名（如 station_id）" :aria-label="'字段技术名'" @input="setField(field,'name',($event.target as HTMLInputElement).value)"/>
        <input class="cell-input" :value="field.label" :disabled="disabled" placeholder="显示名" :aria-label="'字段显示名'" @input="setField(field,'label',($event.target as HTMLInputElement).value)"/>
        <button class="mini" :disabled="disabled" title="删除字段" @click="removeField(Number(index))">删除</button>
      </div>
      <TypeEditor :decl="field.type" :depth="depth+1" :disabled="disabled" @before-change="emit('before-change')" @changed="emit('changed')"/>
    </div>
    <button class="sheet-add" :disabled="disabled" @click="addField">＋ 添加字段</button>
  </div>
  <div v-else-if="decl.type==='list'" class="type-list">
    <small class="muted">列表元素类型：</small>
    <TypeEditor :decl="decl.elementType || (decl.elementType={type:'text'})" :depth="depth" :disabled="disabled" @before-change="emit('before-change')" @changed="emit('changed')"/>
  </div>
</div>
</template>
<style scoped>
.type-editor{margin-top:6px}
.type-object{border-left:2px solid var(--line);padding:2px 0 2px 10px;margin-top:8px}
.type-field{border-bottom:1px dashed var(--line);padding:6px 0;margin-bottom:4px}
.type-field-head{display:flex;gap:6px;align-items:center}
.type-field-head .cell-input{flex:1;min-width:0}
.type-list{margin-top:6px}
</style>
