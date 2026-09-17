<!-- BindingEditor — 输入来源绑定（唯一业务事实）。四种来源：编排入口参数 / 固定值 /
     其他节点完整输出 / 对象字段（按稳定 ID 路径）。绑定变更即时派生画布连线。
     作为对话框使用时（dialog=true）取消不落任何绑定（A4）；确定经 apply 事件由父组件落盘。
     所有事件处理器都是脚本函数（不用模板内联多语句），避免编译边界与作用域问题。 -->
<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import AppSelect from '../shared/AppSelect.vue'
import TypeEditor from './TypeEditor.vue'
import { SCALAR_TYPES, TYPE_LABELS, outputCandidates, processingNodes, resolveSourceType, typesCompatible, fieldOf, derivedEdges } from './flowModel'
const props = defineProps<{ state: any; owner: { nodeId: string; input: any }; dialog?: boolean }>()
const emit = defineEmits(['before-change', 'changed', 'close', 'apply'])
const ownerNode = computed(() => (props.state.nodes || []).find((n: any) => n.id === props.owner.nodeId))
const source = computed<any>(() => props.owner.input.source)
const kind = ref<string>(source.value ? source.value.kind : '')

watch(source, value => { kind.value = value ? value.kind : '' })

const flowInputOptions = computed(() =>
  (props.state.inputs || []).map((i: any) => ({ value: i.id, label: i.label || i.name || '未命名参数' })))
const candidates = computed(() => outputCandidates(props.state, props.owner.nodeId))
const outputOptions = computed(() => candidates.value.map((c: any) => ({
  value: c.nodeId + '/' + c.output.id,
  label: `「${c.nodeName}」· ${c.output.label || c.output.name}（${typeShort(c.output.type)}）`,
})))
function typeShort(decl: any): string {
  if (!decl || !decl.type) return '?'
  if (decl.type === 'object') return '对象'
  if (decl.type === 'list') return '列表'
  return TYPE_LABELS[decl.type] || decl.type
}
const selectedOutput = computed(() => {
  if (!source.value || (source.value.kind !== 'node' && source.value.kind !== 'nodeField')) return null
  return candidates.value.find((c: any) => c.nodeId === source.value.nodeId && c.output.id === source.value.outputId) || null
})
/** 选中输出下的对象字段树（含嵌套，路径上每层都必须是对象）。 */
const fieldRows = computed(() => {
  const rows: { path: string[]; field: any; label: string; decl: any }[] = []
  const walk = (decl: any, path: string[], prefix: string) => {
    for (const field of (decl?.type === 'object' ? decl.fields : []) || []) {
      if (!field || !field.id) continue
      const next = [...path, field.id]
      rows.push({ path: next, field, label: prefix + (field.label || field.name || field.id), decl: field.type })
      walk(field.type, next, prefix + (field.label || field.name || '·') + '.')
    }
  }
  if (selectedOutput.value) walk(selectedOutput.value.output.type, [], '')
  return rows
})
const fieldOptions = computed(() => {
  if (selectedOutput.value && selectedOutput.value.output.type?.type === 'list')
    return [{ value: '', label: '（列表不能按字段引用：不自动遍历，请整体绑定）', disabled: true }]
  return fieldRows.value.map(r => ({
    value: r.path.join('/'),
    label: `${r.label}（${typeShort(r.decl)}）`,
  }))
})
const compatible = computed(() => {
  if (!source.value || kind.value === 'fixed') return { ok: true, reason: '' }
  const resolved = resolveSourceType(props.state, source.value)
  if (resolved.error) return { ok: false, reason: resolved.error }
  if (!resolved.decl) return { ok: false, reason: '来源类型未知' }
  return typesCompatible(props.owner.input.type, resolved.decl)
})
const cycleWarning = computed(() => {
  if (!source.value || (source.value.kind !== 'node' && source.value.kind !== 'nodeField')) return ''
  if (wouldCreateCycleWithout(props.state, props.owner, source.value.nodeId)) return '此绑定会形成循环依赖，保存后配置检查将拒绝'
  return ''
})
function wouldCreateCycleWithout(state: any, owner: { nodeId: string; input: any }, candidateNodeId: string): boolean {
  // 非变体检查：按派生边构建依赖图时排除该输入自身的当前绑定，再附加候选边判断成环。
  const deps = new Map<string, Set<string>>()
  for (const edge of derivedEdges(state)) {
    if (edge.nodeId === owner.nodeId && edge.inputId === owner.input.id) continue
    if (!deps.has(edge.target)) deps.set(edge.target, new Set())
    deps.get(edge.target)!.add(edge.source)
  }
  if (!deps.has(owner.nodeId)) deps.set(owner.nodeId, new Set())
  deps.get(owner.nodeId)!.add(candidateNodeId)
  const seen = new Set<string>()
  const stack = [candidateNodeId]
  while (stack.length) {
    const current = stack.pop()!
    if (current === owner.nodeId) return true
    if (seen.has(current)) continue
    seen.add(current)
    for (const next of deps.get(current) || []) stack.push(next)
  }
  return false
}

function beginEdit() { emit('before-change') }
function doneEdit() { emit('changed') }
function setKind(next: string) {
  emit('before-change')
  kind.value = next
  if (next === 'flowInput') props.owner.input.source = { kind: 'flowInput', inputId: flowInputOptions.value[0]?.value || '' }
  else if (next === 'fixed') props.owner.input.source = { kind: 'fixed', valueType: 'text', value: '' }
  else if (next === 'node' || next === 'nodeField') {
    const first = candidates.value[0]
    props.owner.input.source = first ? { kind: next, nodeId: first.nodeId, outputId: first.output.id, ...(next === 'nodeField' ? { fieldPath: [] } : {}) } : null
    if (!first) kind.value = ''
  } else props.owner.input.source = null
  emit('changed')
}
function setFlowInput(value: string) {
  emit('before-change')
  if (source.value) source.value.inputId = value
  emit('changed')
}
function setFixedType(value: string) {
  emit('before-change')
  if (source.value) {
    source.value.valueType = value
    if (value === 'number') source.value.value = 0
    else if (value === 'boolean') source.value.value = false
    else source.value.value = ''
  }
  emit('changed')
}
function setFixedBoolean(value: string) {
  emit('before-change')
  if (source.value) source.value.value = value === 'true'
  emit('changed')
}
function setFixedScalar(value: string) {
  emit('before-change')
  if (source.value) source.value.value = source.value.valueType === 'number' ? (value === '' ? '' : Number(value)) : value
  emit('changed')
}
function setBindingSource(value: string) {
  emit('before-change')
  const parts = String(value || '').split('/')
  if (source.value && parts.length === 2 && parts[0] && parts[1]) {
    source.value.nodeId = parts[0]
    source.value.outputId = parts[1]
    if (kind.value === 'nodeField') source.value.fieldPath = []
  }
  emit('changed')
}
function setBindingField(value: string) {
  emit('before-change')
  if (source.value && value) source.value.fieldPath = String(value).split('/')
  emit('changed')
}
function apply() {
  // 对话框模式由父组件（apply 事件）复制影子绑定并落盘；表单内模式直接通知变更。
  if (props.dialog) { emit('apply'); return }
  emit('before-change')
  emit('changed')
  emit('close')
}
function cancel() { emit('close') }
function fieldPathString(src: any): string { return (src?.fieldPath || []).join('/') }
</script>
<template>
<div class="binding-editor" :class="{dialog}">
  <label>来源类型
    <AppSelect :model-value="kind" :options="[{value:'',label:'未绑定'},{value:'flowInput',label:'编排入口参数'},{value:'fixed',label:'固定值'},{value:'node',label:'其他节点的输出'},{value:'nodeField',label:'其他节点输出的对象字段'}]" aria-label="来源类型" @update:model-value="setKind"/>
  </label>
  <template v-if="kind==='flowInput'">
    <label>入口参数
      <AppSelect :model-value="source?.inputId||''" :options="flowInputOptions.length?flowInputOptions:[{value:'',label:'（编排尚未声明入口参数）',disabled:true}]" aria-label="入口参数" @update:model-value="setFlowInput"/>
    </label>
  </template>
  <template v-else-if="kind==='fixed'">
    <label>固定值类型
      <AppSelect :model-value="source?.valueType||'text'" :options="SCALAR_TYPES.map(t=>({value:t,label:TYPE_LABELS[t]}))" aria-label="固定值类型" @update:model-value="setFixedType"/>
    </label>
    <label v-if="source?.valueType==='boolean'">值
      <AppSelect :model-value="String(source.value)" :options="[{value:'true',label:'是（true）'},{value:'false',label:'否（false）'}]" aria-label="固定值" @update:model-value="setFixedBoolean"/>
    </label>
    <label v-else>值<input :value="source?.value ?? ''" :type="source?.valueType==='number'?'number':'text'" aria-label="固定值" @input="setFixedScalar(($event.target as HTMLInputElement).value)"/></label>
    <p class="muted">空字符串、0、false 都是有效固定值；与未绑定不同。</p>
  </template>
  <template v-else-if="kind==='node'||kind==='nodeField'">
    <label>来源输出
      <AppSelect :model-value="selectedOutput?selectedOutput.nodeId+'/'+selectedOutput.output.id:''" :options="outputOptions.length?outputOptions:[{value:'',label:'（暂无可用的其他节点输出）',disabled:true}]" aria-label="来源输出" @update:model-value="setBindingSource"/>
    </label>
    <template v-if="kind==='nodeField'">
      <label>对象字段（按声明路径选择）
        <AppSelect :model-value="fieldPathString(source)" :options="fieldOptions.length?fieldOptions:[{value:'',label:'（该输出不是对象或没有声明字段）',disabled:true}]" aria-label="对象字段" @update:model-value="setBindingField"/>
      </label>
    </template>
    <p v-if="cycleWarning" class="inline-warning">{{cycleWarning}}</p>
  </template>
  <p v-if="source && !compatible.ok" class="inline-warning">类型不相容：{{compatible.reason}}（可先保存草稿，配置检查会提示）</p>
  <p v-else-if="source && kind==='nodeField' && selectedOutput && fieldPathString(source) && !fieldOf(selectedOutput.output.type,(source.fieldPath||[]))" class="inline-warning">字段路径已失效，请重新选择</p>
  <div v-if="dialog" class="dialogtools">
    <button @click="cancel">取消</button>
    <button class="primary" :disabled="!source" @click="apply">确定绑定</button>
  </div>
</div>
</template>
<style scoped>
.binding-editor label{display:block}
.binding-editor.dialog{display:flex;flex-direction:column;gap:2px}
</style>
