<!-- NodeConfig — Python/SQL 处理节点的右侧配置：名称/说明、输入声明（来源绑定）、
     输出声明、实现正文（等宽 textarea，保留缩进换行）、SQL 数据连接选择（来自项目"数据连接"菜单）。
     技术名修改若影响正文引用只提示检查代码，不自动重写。 -->
<script setup lang="ts">
import { computed, ref } from 'vue'
import { appConfirm } from '../shared/appConfirm'
import AppSelect from '../shared/AppSelect.vue'
import TypeEditor from './TypeEditor.vue'
import BindingEditor from './BindingEditor.vue'
import { TYPE_LABELS, uid, pythonSkeleton, outputRemovalImpact, resolveSourceType, typesCompatible, sourceSummary } from './flowModel'
const props = defineProps<{ state: any; node: any; connections?: any[] }>()
const emit = defineEmits(['before-change', 'changed'])
const notice = ref('')
let noticeTimer: any = null
function warn(text: string) { notice.value = text; clearTimeout(noticeTimer); noticeTimer = setTimeout(() => notice.value = '', 6000) }
const isPython = computed(() => props.node.kind === 'python')
const isRedis = computed(() => props.node.kind === 'redis')
const wantedEngine = computed(() => (isRedis.value ? 'redis' : 'mysql'))
/** 数据连接下拉：项目"数据连接"菜单中对应引擎的连接（仅引用其稳定 ID）；旧草稿自带声明向后兼容。 */
const connectionOptions = computed(() => [
  { value: '', label: `（请选择${isRedis.value ? 'Redis' : 'MySQL'}数据连接）` },
  ...(props.connections || []).filter((c: any) => c.engine === wantedEngine.value)
    .map((c: any) => ({ value: c.id, label: `${c.name || c.id}（${wantedEngine.value === 'redis' ? 'Redis' : 'MySQL'}）` })),
  ...(props.state.connections || []).filter((c: any) => c.engine === wantedEngine.value)
    .map((c: any) => ({ value: c.id, label: `${c.name || '未命名'}（旧）` })),
])
const hasConnectionOptions = computed(() => connectionOptions.value.length > 1)
const ROW_FIELD_HINT = '{{行字段}}'
const PARAM_HINT = '${输入参数名}'
function addInput() {
  emit('before-change')
  props.node.inputs.push({ id: uid(), name: '', label: '', type: { type: 'text' }, source: null })
  emit('changed')
}
function addOutput() {
  emit('before-change')
  props.node.outputs.push({ id: uid(), name: '', label: '', type: { type: 'text' } })
  emit('changed')
}
function removeInput(index: number) {
  emit('before-change')
  props.node.inputs.splice(index, 1)
  emit('changed')
}
async function removeOutput(index: number) {
  const out = props.node.outputs[index]
  const impact = outputRemovalImpact(props.state, props.node.id, out.id)
  const label = out.label || out.name || '未命名输出'
  if (impact.length && !(await appConfirm({ message: `输出「${label}」正被引用：\n${impact.join('\n')}\n\n删除后这些引用将变为未绑定。`, danger: true, confirmLabel: '删除' }))) return
  if (!impact.length && !(await appConfirm({ message: `删除输出「${label}」？`, danger: true }))) return
  emit('before-change')
  for (const other of props.state.nodes) for (const input of other.inputs || []) {
    const src = input.source
    if (src && (src.kind === 'node' || src.kind === 'nodeField') && src.nodeId === props.node.id && src.outputId === out.id) input.source = null
  }
  for (const fo of props.state.outputs) {
    const b = fo.binding
    if (b && (b.kind === 'node' || b.kind === 'nodeField') && b.nodeId === props.node.id && b.outputId === out.id) fo.binding = null
  }
  props.node.outputs.splice(index, 1)
  emit('changed')
}
/** 技术名修改：正文含旧名时提示检查代码（不自动重写任意代码）。 */
function renameTechnical(container: any, list: 'inputs' | 'outputs', index: number, next: string) {
  const item = props.node[list][index]
  const old = item.name
  if (old === next) return
  emit('before-change')
  item.name = next
  emit('changed')
  const body = isPython.value ? (props.node.implementation?.code || '') : (props.node.implementation?.sql || '')
  if (old && body.includes(isPython.value ? old : `:${old}`)) {
    warn(`技术名「${old}」已改为「${next || '（空）'}」，正文仍引用旧名；请检查${isPython.value ? '代码参数' : 'SQL :参数'}，系统不会自动重写代码。`)
  }
}
function setInputLabel(input: any, value: string) { emit('before-change'); input.label = value; emit('changed') }
function setOutputLabel(out: any, value: string) { emit('before-change'); out.label = value; emit('changed') }
function setField(row: any, key: string, value: any) { emit('before-change'); row[key] = value; emit('changed') }
function setBody(value: string) {
  emit('before-change')
  const impl = props.node.implementation || (props.node.implementation = {})
  if (isPython.value) impl.code = value
  else if (isRedis.value) impl.keyTemplate = value
  else impl.sql = value
  emit('changed')
}
function setConnection(value: string) { emit('before-change'); props.node.implementation.connectionId = value; emit('changed') }
function refillSkeleton() {
  if (!isPython.value) return
  emit('before-change')
  props.node.implementation.code = pythonSkeleton(props.node.inputs, props.node.outputs)
  emit('changed')
  warn('已按当前参数重新生成 def main 骨架；此操作会覆盖代码正文，请立即检查。')
}
</script>
<template>
<div class="node-config">
  <div class="badge">{{isPython?'PYTHON 节点':(isRedis?'REDIS 节点':'SQL 节点')}}</div>
  <label>名称 *<input :value="node.name" :aria-label="'节点名称'" @input="setField(node,'name',($event.target as HTMLInputElement).value)"/></label>
  <label>说明<textarea :value="node.description" rows="2" :aria-label="'节点说明'" @input="setField(node,'description',($event.target as HTMLTextAreaElement).value)"/></label>

  <h3>输入参数</h3>
  <p class="muted">每个输入只有一个来源；绑定即业务事实，画布连线由此派生。</p>
  <div v-for="(input,index) in node.inputs" :key="input.id" class="param-card">
    <div class="mapping-row">
      <label>技术名（代码中引用）*<input :value="input.name" placeholder="如 limit_n" :aria-label="'输入技术名'" @change="renameTechnical(node,'inputs',Number(index),($event.target as HTMLInputElement).value.trim())"/></label>
      <label>显示名<input :value="input.label" :aria-label="'输入显示名'" @input="setInputLabel(input,($event.target as HTMLInputElement).value)"/></label>
      <button class="mini" @click="removeInput(Number(index))">删除</button>
    </div>
    <label>类型<TypeEditor :decl="input.type" @before-change="emit('before-change')" @changed="emit('changed')"/></label>
    <p v-if="input.source" class="muted">当前来源：{{sourceSummary(state,input.source)}}</p>
    <details open><summary>来源绑定</summary>
      <BindingEditor :state="state" :owner="{nodeId:node.id,input}" @before-change="emit('before-change')" @changed="emit('changed')"/>
    </details>
  </div>
  <button class="sheet-add" @click="addInput">＋ 添加输入参数</button>

  <h3>输出</h3>
  <p class="muted">后续节点与编排输出可引用完整输出，也可选择对象中的具体字段；列表不会被隐式展开。</p>
  <div v-for="(out,index) in node.outputs" :key="out.id" class="param-card">
    <div class="mapping-row">
      <label>技术名 *<input :value="out.name" placeholder="如 rows" :aria-label="'输出技术名'" @change="renameTechnical(node,'outputs',Number(index),($event.target as HTMLInputElement).value.trim())"/></label>
      <label>显示名<input :value="out.label" :aria-label="'输出显示名'" @input="setOutputLabel(out,($event.target as HTMLInputElement).value)"/></label>
      <button class="mini" @click="removeOutput(Number(index))">删除</button>
    </div>
    <label>类型<TypeEditor :decl="out.type" @before-change="emit('before-change')" @changed="emit('changed')"/></label>
  </div>
  <button class="sheet-add" @click="addOutput">＋ 添加输出</button>

  <h3>{{isPython?'代码':'SQL 模板'}}</h3>
  <template v-if="isPython">
    <p class="muted">工作台只保存配置，不执行代码、不安装依赖；参数变化不会自动覆盖正文。</p>
    <textarea class="code-editor" :value="node.implementation?.code || ''" rows="14" spellcheck="false" aria-label="Python 代码" @input="setBody(($event.target as HTMLTextAreaElement).value)"/>
    <button class="mini" @click="refillSkeleton">按当前参数重建 main 骨架（覆盖正文）</button>
  </template>
  <template v-else-if="isRedis">
    <label>数据连接
      <AppSelect :model-value="node.implementation?.connectionId || ''" :options="connectionOptions" aria-label="数据连接" @before-change="emit('before-change')" @update:model-value="setConnection($event as string)"/>
    </label>
    <p class="muted">数据连接来自项目映射 → 数据连接 菜单（Redis 类型）；此处只引用连接，不保存地址与凭据、不做连接测试。{{hasConnectionOptions?'':'当前项目还没有 Redis 数据连接，请先到数据连接菜单添加。'}}</p>
    <label>Key 模板
      <textarea class="code-editor" :value="node.implementation?.keyTemplate || ''" rows="3" spellcheck="false" aria-label="Redis Key 模板" @input="setBody(($event.target as HTMLTextAreaElement).value)"/>
    </label>
    <p class="muted">行模式：输入为列表时用 <b>{{ ROW_FIELD_HINT }}</b> 引用元素字段，对列表每行各取一个 key，输出列表与输入顺序一一对应（不做隐式展开之外的变换）。单键模式：用 <b>{{ PARAM_HINT }}</b> 引用输入参数，取一个 key。本节点只声明取数配置，不连接 Redis。</p>
  </template>
  <template v-else>
    <label>数据连接
      <AppSelect :model-value="node.implementation?.connectionId || ''" :options="connectionOptions" aria-label="逻辑连接" @before-change="emit('before-change')" @update:model-value="setConnection($event as string)"/>
    </label>
    <p class="muted">数据连接来自项目映射 → 数据连接 菜单（仅 MySQL）；此处只引用连接，不保存地址与凭据、不做连接测试。{{hasConnectionOptions?'':'当前项目还没有 MySQL 数据连接，请先到数据连接菜单添加。'}}</p>
    <p class="muted">SQL 只引用上方选定的数据连接；输入值用 :参数名。不查询真实数据库，多步处理通过节点连接表达。</p>
    <textarea class="code-editor" :value="node.implementation?.sql || ''" rows="10" spellcheck="false" aria-label="SQL 模板" @input="setBody(($event.target as HTMLTextAreaElement).value)"/>
  </template>
  <p v-if="notice" class="property-feedback" role="status">{{notice}}</p>
</div>
</template>
<style scoped>
.node-config h3{margin:22px 0 8px}
.code-editor{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:13px;line-height:1.65;white-space:pre;overflow:auto;min-height:220px}
.mapping-row{align-items:end}
.conn-manage{border:1px dashed var(--line);border-radius:8px;padding:10px;margin:8px 0}
.conn-manage summary{cursor:pointer;font-size:13px;color:var(--muted)}
.conn-manage .sheet-add{margin-top:8px}
.conn-row{display:flex;gap:6px;align-items:end;margin:8px 0}
.conn-row input{flex:1;min-width:0;margin:0}
.conn-row .app-select{flex:0 0 108px;margin:0}
</style>
