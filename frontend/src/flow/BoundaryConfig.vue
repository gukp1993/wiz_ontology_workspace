<!-- BoundaryConfig — 边界节点配置：编排输入（流程入口参数声明）与编排输出
     （命名输出 + 绑定到节点输出/对象字段；类型可按来源推导或手动声明并校验相容）。 -->
<script setup lang="ts">
import { computed } from 'vue'
import { appConfirm } from '../shared/appConfirm'
import AppSelect from '../shared/AppSelect.vue'
import TypeEditor from './TypeEditor.vue'
import { outputCandidates, flowInputRemovalImpact, resolveSourceType, typesCompatible, uid } from './flowModel'
const props = defineProps<{ state: any; kind: 'input' | 'output' }>()
const emit = defineEmits(['before-change', 'changed'])
const candidates = computed(() => outputCandidates(props.state, null))
const candidateOptions = computed(() => candidates.value.map((c: any) => ({
  value: c.nodeId + '/' + c.output.id,
  label: `「${c.nodeName}」· ${c.output.label || c.output.name}`,
})))
function candidateFields(binding: any) {
  const match = candidates.value.find((c: any) => c.nodeId === binding.nodeId && c.output.id === binding.outputId)
  if (!match || match.output.type?.type !== 'object') return []
  const rows: { path: string[]; label: string }[] = []
  const walk = (decl: any, path: string[], prefix: string) => {
    for (const field of (decl?.type === 'object' ? decl.fields : []) || []) {
      if (!field?.id) continue
      const next = [...path, field.id]
      rows.push({ path: next, label: prefix + (field.label || field.name || field.id) })
      walk(field.type, next, prefix + (field.label || field.name || '·') + '.')
    }
  }
  walk(match.output.type, [], '')
  return rows
}
function addEntry() {
  emit('before-change')
  if (props.kind === 'input') props.state.inputs.push({ id: uid(), name: '', label: '', type: { type: 'text' } })
  else props.state.outputs.push({ id: uid(), name: '', label: '', type: { type: 'text' }, binding: null })
  emit('changed')
}
async function removeEntry(list: 'inputs' | 'outputs', index: number) {
  const entry = props.state[list][index]
  const label = entry.label || entry.name || '未命名'
  const impact = list === 'inputs'
    ? flowInputRemovalImpact(props.state, entry.id)
    : (entry.binding ? [`编排输出「${label}」的来源绑定将一并清除`] : [])
  if (impact.length && !(await appConfirm({ message: `删除「${label}」？\n${impact.join('\n')}`, danger: true }))) return
  if (!impact.length && !(await appConfirm({ message: `删除「${label}」？`, danger: true }))) return
  emit('before-change')
  if (list === 'inputs') {
    for (const node of props.state.nodes) for (const input of node.inputs || []) {
      if (input.source?.kind === 'flowInput' && input.source.inputId === entry.id) input.source = null
    }
  } else if (entry.binding) {
    for (const out of props.state.outputs) if (out.id === entry.id) out.binding = null
  }
  props.state[list].splice(index, 1)
  emit('changed')
}
function setField(row: any, key: string, value: any) { emit('before-change'); row[key] = value; emit('changed') }
function setBindingSource(out: any, value: string) {
  emit('before-change')
  const [nodeId, outputId] = value ? String(value).split('/') : ['', '']
  out.binding = nodeId ? { kind: 'node', nodeId, outputId } : null
  emit('changed')
}
function setBindingField(out: any, value: string) {
  emit('before-change')
  if (value) out.binding = { kind: 'nodeField', nodeId: out.binding.nodeId, outputId: out.binding.outputId, fieldPath: String(value).split('/') }
  else out.binding = { kind: 'node', nodeId: out.binding.nodeId, outputId: out.binding.outputId }
  emit('changed')
}
function bindingKey(out: any): string {
  const b = out.binding
  if (!b) return ''
  const base = b.nodeId + '/' + b.outputId
  return b.kind === 'nodeField' ? base : base
}
function fieldKey(out: any): string { return out.binding?.kind === 'nodeField' ? (out.binding.fieldPath || []).join('/') : '' }
/** 输出类型按来源推导（来源可解析时覆盖声明；不可解析保持原声明待完善）。 */
function deriveType(out: any) {
  const resolved = out.binding ? resolveSourceType(props.state, out.binding) : null
  if (!resolved || resolved.error || !resolved.decl) return
  emit('before-change')
  out.type = JSON.parse(JSON.stringify(resolved.decl))
  emit('changed')
}
function compatibleText(out: any): string {
  if (!out.binding) return '未绑定来源（待完善）'
  const resolved = resolveSourceType(props.state, out.binding)
  if (resolved.error) return '绑定失效：' + resolved.error
  const verdict = typesCompatible(out.type, resolved.decl)
  return verdict.ok ? '声明类型与来源相容' : '类型矛盾：' + verdict.reason
}
</script>
<template>
<div class="boundary-config">
  <div class="badge">{{kind==='input'?'编排入口':'编排输出'}}</div>
  <p class="muted">{{kind==='input'
    ?'编排输入节点维护整个流程的入口参数声明；处理节点输入可绑定到这些参数。'
    :'编排输出声明命名返回值，逐项绑定到处理节点输出或其对象字段；不会默认取最后创建的节点。'}}</p>
  <div v-for="(entry,index) in (kind==='input'?state.inputs:state.outputs)" :key="entry.id" class="param-card">
    <div class="mapping-row">
      <label>技术名 *<input :value="entry.name" placeholder="如 device_id" :aria-label="'技术名'" @change="setField(entry,'name',($event.target as HTMLInputElement).value.trim())"/></label>
      <label>显示名<input :value="entry.label" :aria-label="'显示名'" @input="setField(entry,'label',($event.target as HTMLInputElement).value)"/></label>
      <button class="mini" @click="removeEntry(kind==='input'?'inputs':'outputs',Number(index))">删除</button>
    </div>
    <label>类型<TypeEditor :decl="entry.type" @before-change="emit('before-change')" @changed="emit('changed')"/></label>
    <template v-if="kind==='output'">
      <label>绑定来源输出
        <AppSelect :model-value="bindingKey(entry)" :options="[{value:'',label:'（未绑定）'},...candidateOptions]" aria-label="绑定来源输出" @before-change="emit('before-change')" @update:model-value="setBindingSource(entry,$event as string)"/>
      </label>
      <template v-if="entry.binding">
        <label v-if="candidateFields(entry.binding).length">引用对象字段（不选则传完整输出）
          <AppSelect :model-value="fieldKey(entry)" :options="[{value:'',label:'（完整输出）'},...candidateFields(entry.binding).map(f=>({value:f.path.join('/'),label:f.label}))]" aria-label="对象字段" @before-change="emit('before-change')" @update:model-value="setBindingField(entry,$event as string)"/>
        </label>
        <div class="row">
          <button class="mini" @click="deriveType(entry)">按来源推导类型</button>
          <small :class="resolveSourceType(state,entry.binding).error ? 'inline-error' : 'inline-success'">{{compatibleText(entry)}}</small>
        </div>
      </template>
    </template>
  </div>
  <button class="sheet-add" @click="addEntry">{{kind==='input'?'＋ 添加入口参数':'＋ 添加命名输出'}}</button>
</div>
</template>
<style scoped>
.mapping-row{align-items:end}
</style>
