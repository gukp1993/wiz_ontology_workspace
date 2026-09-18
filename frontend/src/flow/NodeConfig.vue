<!-- NodeConfig — 处理节点详情内容（页签化：输入/实现/输出/高级）。由 FlowEditor 下发 tab，
     只渲染当前页签；focus 携带 parameterId/field 时展开对应参数并高亮字段（问题定位入口）。
     连接/LLM/凭据下拉区分 加载中/失败/未配置/已加载；Python 实现页固定显示 LLM 推演标识；
     代码编辑支持换行切换与展开编辑（emit expand-code，缓冲由编辑页统一落盘）。 -->
<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { appConfirm } from '../shared/appConfirm'
import AppSelect from '../shared/AppSelect.vue'
import TypeEditor from './TypeEditor.vue'
import BindingEditor from './BindingEditor.vue'
import { EXEC_DEFAULT_TIMEOUT, EXEC_MAX_TIMEOUT_MS, HTTP_METHODS, NODE_KIND_LABELS, REDIS_COMMAND_OPTIONS, SQL_MAX_ROWS_MAX, TYPE_LABELS, flowTypeToCalcType, outputRemovalImpact, pythonSkeleton, sourceSummary, uid } from './flowModel'
const props = defineProps<{ state: any; node: any; tab: string; connections?: any[]; credentials?: any[]; providers?: any[]; providersStatus?: string; connectionsStatus?: string; focus?: { token: number; parameterId?: string; field?: string } }>()
const emit = defineEmits(['before-change', 'changed', 'expand-code', 'open-llm-config', 'open-connections', 'retry-providers'])
const notice = ref('')
let noticeTimer: any = null
function warn(text: string) { notice.value = text; clearTimeout(noticeTimer); noticeTimer = setTimeout(() => notice.value = '', 6000) }
const kind = computed(() => props.node.kind as string)
const isPython = computed(() => kind.value === 'python')
const isRedis = computed(() => kind.value === 'redis')
const isHttp = computed(() => kind.value === 'http')
const isCalc = computed(() => kind.value === 'calc')
const needsLlm = computed(() => isPython.value || (isCalc.value && props.node.implementation?.mode === 'llm'))
const impl = computed(() => props.node.implementation || (props.node.implementation = {}))
const exec = computed(() => props.node.execution || (props.node.execution = {}))

// ── 输入页签：参数摘要 + 展开编辑 ─────────────────────────────────────────────
const expandedInputs = ref<Record<string, boolean>>({})
const flashField = ref('')
let flashTimer: any = null
watch(() => props.focus, f => {
  if (!f?.token) return
  if (props.tab === 'inputs' && f.parameterId) expandedInputs.value[f.parameterId] = true
  flashField.value = f.field || ''
  clearTimeout(flashTimer)
  flashTimer = setTimeout(() => { flashField.value = '' }, 2200)
  void nextTickScroll()
}, { deep: true, immediate: true })
function nextTickScroll() {
  return Promise.resolve().then(() => {
    const el = document.querySelector('.field-flash')
    el?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  })
}
const inputRows = computed(() => (props.node.inputs || []).map(i => ({
  input: i,
  summary: `${i.label || i.name || '未命名输入'}（${TYPE_LABELS[i.type?.type] || i.type?.type || '?'}）`,
  source: i.source ? sourceSummary(props.state, i.source) : '未绑定来源',
})))
const wantedEngine = computed(() => (isRedis.value ? 'redis' : 'mysql'))
const connectionOptions = computed(() => isHttp.value ? [] : [
  { value: '', label: `（请选择${isRedis.value ? 'Redis' : 'MySQL'}数据连接）` },
  ...(props.connections || []).filter((c: any) => c.engine === wantedEngine.value)
    .map((c: any) => ({ value: c.id, label: `${c.name || c.id}（${wantedEngine.value === 'redis' ? 'Redis' : 'MySQL'}）` })),
  ...(props.state.connections || []).filter((c: any) => c.engine === wantedEngine.value)
    .map((c: any) => ({ value: c.id, label: `${c.name || '未命名'}（旧）` })),
])
const hasConnectionOptions = computed(() => connectionOptions.value.length > 1)
const providerOptions = computed(() => [
  { value: '', label: `（默认：${props.providers?.find((p: any) => p.isDefault)?.name || '未配置'}）` },
  ...(props.providers || []).map((p: any) => ({ value: p.id, label: `${p.name} · ${p.model}` })),
])
const llmReady = computed(() => (props.providers || []).length > 0)
const credentialOptions = computed(() => [
  { value: '', label: '（无认证）' },
  ...(props.credentials || []).map((c: any) => ({ value: c.id, label: `${c.name || c.id}（Bearer）` })),
])
const headersText = computed({
  get: () => Object.entries(props.node.implementation?.headers || {}).map(([k, v]) => `${k}: ${v}`).join('\n'),
  set: (text: string) => {
    emit('before-change')
    const headers: Record<string, string> = {}
    for (const line of String(text).split('\n')) {
      const idx = line.indexOf(':')
      if (idx > 0 && line.slice(0, idx).trim()) headers[line.slice(0, idx).trim()] = line.slice(idx + 1).trim()
    }
    props.node.implementation.headers = headers
    emit('changed')
  },
})
const argsText = computed({
  get: () => (props.node.implementation?.args || []).join(', '),
  set: (text: string) => {
    emit('before-change')
    props.node.implementation.args = String(text).split(',').map((x: string) => x.trim()).filter(Boolean)
    emit('changed')
  },
})
const bodyModeOptions = [{ value: 'none', label: '无请求体' }, { value: 'json', label: 'JSON' }]
const calcModeOptions = [{ value: 'formula', label: '公式模式（确定性）' }, { value: 'llm', label: 'LLM 模式' }]
const formulaOutputs = computed(() => (props.node.outputs || []).filter((o: any) => flowTypeToCalcType(o.type?.type)))
const untitled = computed(() => (props.node.outputs || []).filter((o: any) => !flowTypeToCalcType(o.type?.type)))
const wrapOn = ref(false)
const codeField = computed<'code' | 'sql' | 'keyTemplate' | 'body' | 'llmInstruction' | null>(() =>
  isPython.value ? 'code' : isRedis.value ? 'keyTemplate' : isHttp.value ? 'body' : isCalc.value && (props.node.implementation?.mode || 'formula') === 'llm' ? 'llmInstruction' : 'sql')
const codeValue = computed(() => codeField.value ? (props.node.implementation?.[codeField.value] || '') : '')
function setBody(value: string) {
  emit('before-change')
  if (codeField.value) impl.value[codeField.value] = value
  emit('changed')
}
function expandCode() {
  if (!codeField.value) return
  const titles: Record<string, string> = { code: 'Python 代码', sql: 'SQL 模板', keyTemplate: 'Redis Key 模板', body: 'HTTP 请求体模板', llmInstruction: 'LLM 计算规则' }
  emit('expand-code', { field: codeField.value, title: titles[codeField.value], value: codeValue.value })
}
const fieldFlash = (field: string) => (flashField.value === field ? 'field-flash' : '')

// ── 增删改操作（与既有协议一致） ───────────────────────────────────────────────
function addInput() {
  emit('before-change')
  props.node.inputs.push({ id: uid(), name: '', label: '', type: { type: 'text' }, source: null })
  expandedInputs.value[props.node.inputs[props.node.inputs.length - 1].id] = true
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
  if (impact.length && !(await appConfirm({ message: `删除输出「${label}」正被引用：\n${impact.join('\n')}\n\n删除后这些引用将变为未绑定。`, danger: true, confirmLabel: '删除' }))) return
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
function renameTechnical(container: any, list: 'inputs' | 'outputs', index: number, next: string) {
  const item = props.node[list][index]
  const old = item.name
  if (old === next) return
  emit('before-change')
  item.name = next
  emit('changed')
  const body = isPython.value ? (props.node.implementation?.code || '') : isCalc.value ? JSON.stringify(props.node.implementation?.formulas || {}) : (props.node.implementation?.sql || '')
  if (old && body.includes(isPython.value ? old : `:${old}`) && !isHttp.value && !isRedis.value) {
    warn(`技术名「${old}」已改为「${next || '（空）'}」，实现正文仍引用旧名；请检查${isPython.value ? '代码参数' : 'SQL :参数'}，系统不会自动重写。`)
  } else if (old && isCalc.value && body.includes(`"${old}"`)) {
    warn(`技术名「${old}」已改为「${next || '（空）'}」，公式仍引用旧名，请检查计算定义。`)
  }
}
function setInputLabel(input: any, value: string) { emit('before-change'); input.label = value; emit('changed') }
function setOutputLabel(out: any, value: string) { emit('before-change'); out.label = value; emit('changed') }
function setField(row: any, key: string, value: any) { emit('before-change'); row[key] = value; emit('changed') }
function setFormula(outputName: string, value: string) {
  emit('before-change')
  const formulas = impl.value.formulas || (impl.value.formulas = {})
  formulas[outputName] = value
  emit('changed')
}
function setExec(key: string, value: any) {
  emit('before-change')
  if (value === '' || value == null) delete exec.value[key]
  else exec.value[key] = value
  emit('changed')
}
function setConnection(value: string) { emit('before-change'); impl.value.connectionId = value; emit('changed') }
async function refillSkeleton() {
  if (!isPython.value) return
  if (!(await appConfirm({ message: '重建 main 骨架将覆盖当前 Python 代码正文（按当前输入/输出参数生成）。确认覆盖？', danger: true, confirmLabel: '确认覆盖' }))) return
  emit('before-change')
  props.node.implementation.code = pythonSkeleton(props.node.inputs, props.node.outputs)
  emit('changed')
  warn('已按当前参数重建 def main 骨架；此操作可撤销（顶栏撤销）。')
}
</script>
<template>
<div class="node-config">
  <p v-if="notice" class="property-feedback" role="status">{{notice}}</p>

  <template v-if="tab==='inputs'">
    <p class="muted tight">每个输入只有一个来源；绑定即业务事实，画布连线由此派生。点击摘要展开配置。</p>
    <div v-for="(row,index) in inputRows" :key="row.input.id" class="param-fold" :class="{open: expandedInputs[row.input.id]}">
      <button class="param-summary" @click="expandedInputs[row.input.id]=!expandedInputs[row.input.id]">
        <strong>{{row.summary}}</strong><small :class="{'inline-error': !row.input.source}">{{row.source}}</small>
      </button>
      <div v-if="expandedInputs[row.input.id]" class="param-body">
        <div class="mapping-row">
          <label>技术名（实现中引用）*<input :value="row.input.name" :class="fieldFlash('name')" placeholder="如 limit_n" :aria-label="'输入技术名'" @change="renameTechnical(node,'inputs',Number(index),($event.target as HTMLInputElement).value.trim())"/></label>
          <label>显示名<input :value="row.input.label" :aria-label="'输入显示名'" @input="setInputLabel(row.input,($event.target as HTMLInputElement).value)"/></label>
          <button class="mini" @click="removeInput(Number(index))">删除</button>
        </div>
        <label>类型<TypeEditor :decl="row.input.type" @before-change="emit('before-change')" @changed="emit('changed')"/></label>
        <label :class="fieldFlash('binding')">来源绑定<BindingEditor :state="state" :owner="{nodeId:node.id,input:row.input}" @before-change="emit('before-change')" @changed="emit('changed')"/></label>
      </div>
    </div>
    <button class="sheet-add" @click="addInput">＋ 添加输入参数</button>
  </template>

  <template v-else-if="tab==='implementation'">
    <template v-if="isPython">
      <div class="engine-note" role="note">由大模型推演执行，非本地 Python 运行；结果具非确定性，日志保留请求与响应摘要。</div>
      <div class="row-between"><label class="tight">Python 代码（def main(输入…) → dict | None）</label>
        <span class="code-tools"><button class="mini" @click="wrapOn=!wrapOn">{{wrapOn?'不换行':'自动换行'}}</button><button class="mini" @click="expandCode">展开编辑</button></span></div>
      <textarea class="code-editor" :class="{wrap: wrapOn}" :value="node.implementation?.code || ''" rows="14" spellcheck="false" aria-label="Python 代码" @input="setBody(($event.target as HTMLTextAreaElement).value)"/>
      <button class="mini" @click="refillSkeleton">按当前参数重建 main 骨架…</button>
    </template>
    <template v-else-if="isCalc">
      <div class="mode-row">
        <button v-for="m in calcModeOptions" :key="m.value" class="mini" :class="{active: (impl.mode||'formula')===m.value}"
                @click="setField(impl,'mode',m.value)">{{m.label}}</button>
      </div>
      <template v-if="(impl.mode||'formula')==='formula'">
        <p class="muted tight">受限公式引擎 calc-expression-1：四则/比较/IF/MIN/MAX/ABS/ROUND/ERROR，确定性求值（不执行任意代码）。引用输入用 <b v-pre>{技术名}</b>。</p>
        <div v-for="out in formulaOutputs" :key="out.id" class="param-card">
          <label>输出「{{out.label || out.name}}」的公式<input :value="impl.formulas?.[out.name] || ''" :aria-label="`公式 ${out.name}`" placeholder="如 ROUND(MIN({total}/10000, 100), 0)" @input="setFormula(out.name, ($event.target as HTMLInputElement).value)"/></label>
        </div>
        <p v-for="out in untitled" :key="out.id" class="inline-error">公式模式不支持 {{TYPE_LABELS[out.type?.type] || out.type?.type}} 类型的输出「{{out.label || out.name}}」</p>
      </template>
      <template v-else>
        <p class="muted tight">用自然语言描述计算规则，连同输入交给 LLM 计算（结果非确定性）。</p>
        <div class="row-between"><label class="tight">LLM 计算规则</label>
          <span class="code-tools"><button class="mini" @click="expandCode">展开编辑</button></span></div>
        <textarea class="code-editor wrap" :value="impl.llmInstruction || ''" rows="4" aria-label="LLM 计算规则" @input="setBody(($event.target as HTMLTextAreaElement).value)"/>
      </template>
    </template>
    <template v-else-if="isHttp">
      <div class="form-row">
        <label>方法<AppSelect :model-value="impl.method || 'GET'" :options="HTTP_METHODS.map(m=>({value:m,label:m}))" aria-label="请求方法" @before-change="emit('before-change')" @update:model-value="setField(impl,'method',($event as string))"/>
        </label>
        <label>请求体类型<AppSelect :model-value="impl.bodyMode || 'none'" :options="bodyModeOptions" aria-label="请求体类型" @before-change="emit('before-change')" @update:model-value="setField(impl,'bodyMode',($event as string))"/>
        </label>
      </div>
      <label>URL 模板（{技术名} 占位将 URL 编码替换；占位参数必须已声明）<input :value="impl.url || ''" placeholder="https://host/api/items/{device_id}" aria-label="请求 URL" @input="setField(impl,'url',($event.target as HTMLInputElement).value)"/></label>
      <label>请求头（每行「名: 值」）<textarea class="code-editor wrap" :value="headersText" rows="3" aria-label="请求头" @input="headersText=($event.target as HTMLTextAreaElement).value"/></label>
      <template v-if="(impl.bodyMode||'none')==='json'">
        <div class="row-between"><label class="tight">JSON 请求体模板（{技术名} 占位；字符串占位请自带引号）</label>
          <span class="code-tools"><button class="mini" @click="expandCode">展开编辑</button></span></div>
        <textarea class="code-editor" :class="{wrap: wrapOn}" :value="impl.body || ''" rows="5" spellcheck="false" aria-label="请求体模板" @input="setBody(($event.target as HTMLTextAreaElement).value)"/>
      </template>
      <label>认证凭据（可选；项目 API 凭据，密钥只写不读回）<AppSelect :model-value="impl.credentialId || ''" :options="credentialOptions" aria-label="认证凭据" @before-change="emit('before-change')" @update:model-value="setField(impl,'credentialId',($event as string))"/>
      </label>
      <label>响应提取路径（点路径，留空 = 整个 JSON）<input :value="impl.responsePath || ''" placeholder="data" aria-label="响应提取路径" @input="setField(impl,'responsePath',($event.target as HTMLInputElement).value)"/></label>
    </template>
    <template v-else-if="isRedis">
      <label>数据连接
        <AppSelect :model-value="node.implementation?.connectionId || ''" :options="connectionOptions" :disabled="connectionsStatus==='loading'" aria-label="数据连接" @before-change="emit('before-change')" @update:model-value="setConnection($event as string)"/>
      </label>
      <p v-if="connectionsStatus==='loading'" class="muted tight">数据连接加载中…</p>
      <p v-else-if="connectionsStatus==='failed'" class="inline-error">项目数据连接加载失败。<button class="mini" @click="emit('open-connections')">前往数据连接页</button></p>
      <p v-else-if="!hasConnectionOptions" class="inline-error">当前项目还没有 {{isRedis?'Redis':'MySQL'}} 数据连接。<button class="mini" @click="emit('open-connections')">前往数据连接页</button></p>
      <div class="form-row">
        <label>命令（白名单）
          <AppSelect :model-value="impl.command || ''" :options="[{value:'',label:'（旧草稿：按 key 模板推断 GET）'},...REDIS_COMMAND_OPTIONS]" aria-label="Redis 命令" @before-change="emit('before-change')" @update:model-value="setField(impl,'command',($event as string))"/>
        </label>
        <label>行模式<input type="checkbox" class="inline-check" :checked="!!impl.rowMode" @change="setField(impl,'rowMode',($event.target as HTMLInputElement).checked)"/>
        </label>
      </div>
      <div class="row-between"><label class="tight">Key 模板</label>
        <span class="code-tools"><button class="mini" @click="expandCode">展开编辑</button></span></div>
      <textarea class="code-editor" :class="{wrap: wrapOn}" :value="node.implementation?.keyTemplate || ''" rows="2" spellcheck="false" aria-label="Redis Key 模板" @input="setBody(($event.target as HTMLTextAreaElement).value)"/>
      <label>命令参数（逗号分隔：输入技术名 / 数字字面量 / 行模式 <span v-pre>{{字段}}</span>）<input :value="argsText" aria-label="命令参数" @input="argsText=($event.target as HTMLInputElement).value"/></label>
      <p class="muted tight">行模式：输入为列表时用 <b v-pre>{{行字段}}</b> 引用元素字段，逐行执行，输出列表按序对应。单键模式：用 <b v-pre>${参数}</b> 引用输入参数。</p>
    </template>
    <template v-else>
      <label>数据连接
        <AppSelect :model-value="node.implementation?.connectionId || ''" :options="connectionOptions" :disabled="connectionsStatus==='loading'" aria-label="数据连接" @before-change="emit('before-change')" @update:model-value="setConnection($event as string)"/>
      </label>
      <p v-if="connectionsStatus==='loading'" class="muted tight">数据连接加载中…</p>
      <p v-else-if="connectionsStatus==='failed'" class="inline-error">项目数据连接加载失败。<button class="mini" @click="emit('open-connections')">前往数据连接页</button></p>
      <p v-else-if="!hasConnectionOptions" class="inline-error">当前项目还没有 MySQL 数据连接。<button class="mini" @click="emit('open-connections')">前往数据连接页</button></p>
      <p class="muted tight">动态标签子集：&lt;if test&gt; &lt;choose&gt; &lt;where&gt; &lt;set&gt; &lt;foreach&gt;；参数用 <b v-pre>#{技术名}</b> 或旧写法 :技术名；<b v-pre>${技术名}</b> 为原文拼接（检查会提示注入风险）。</p>
      <div class="row-between"><label class="tight">SQL 模板</label>
        <span class="code-tools"><button class="mini" @click="wrapOn=!wrapOn">{{wrapOn?'不换行':'自动换行'}}</button><button class="mini" @click="expandCode">展开编辑</button></span></div>
      <textarea class="code-editor" :class="{wrap: wrapOn}" :value="node.implementation?.sql || ''" rows="12" spellcheck="false" aria-label="SQL 模板" @input="setBody(($event.target as HTMLTextAreaElement).value)"/>
    </template>
    <template v-if="needsLlm">
      <label>LLM 提供方（留空 = 默认）
        <AppSelect v-if="providersStatus==='ready'" :model-value="impl.providerId || ''" :options="providerOptions" aria-label="LLM 提供方" @before-change="emit('before-change')" @update:model-value="setField(impl,'providerId',($event as string))"/>
      </label>
      <p v-if="providersStatus==='loading'" class="muted tight">LLM 提供方加载中…</p>
      <p v-else-if="providersStatus==='failed'" class="inline-error">LLM 提供方列表加载失败。<button class="mini" @click="emit('retry-providers')">重试</button></p>
      <p v-else-if="!llmReady" class="inline-error">尚未配置可用模型，此节点无法执行。<button class="mini" @click="emit('open-llm-config')">前往模型设置</button></p>
    </template>
  </template>

  <template v-else-if="tab==='outputs'">
    <p class="muted tight">输出参数名用于下游绑定；改名继续以稳定 ID 维持引用。</p>
    <div v-for="(out,index) in node.outputs" :key="out.id" class="param-card">
      <div class="mapping-row">
        <label>技术名 *<input :value="out.name" :class="fieldFlash('name')" placeholder="如 rows" :aria-label="'输出技术名'" @change="renameTechnical(node,'outputs',Number(index),($event.target as HTMLInputElement).value.trim())"/></label>
        <label>显示名<input :value="out.label" :aria-label="'输出显示名'" @input="setOutputLabel(out,($event.target as HTMLInputElement).value)"/></label>
        <button class="mini" @click="removeOutput(Number(index))">删除</button>
      </div>
      <label>类型<TypeEditor :decl="out.type" @before-change="emit('before-change')" @changed="emit('changed')"/></label>
    </div>
    <button class="sheet-add" @click="addOutput">＋ 添加输出</button>
  </template>

  <template v-else-if="tab==='advanced'">
    <div class="form-row">
      <label>超时（毫秒，留空 = {{EXEC_DEFAULT_TIMEOUT[kind] || 30000}}）<input type="number" :min="1000" :max="EXEC_MAX_TIMEOUT_MS" :value="exec.timeoutMs ?? ''" aria-label="节点超时" @change="setExec('timeoutMs', ($event.target as HTMLInputElement).value ? Number(($event.target as HTMLInputElement).value) : '')"/></label>
      <label v-if="kind==='sql'">行数上限（留空 = 1000，最多 {{SQL_MAX_ROWS_MAX}}）<input type="number" :min="1" :max="SQL_MAX_ROWS_MAX" :value="exec.maxRows ?? ''" aria-label="行数上限" @change="setExec('maxRows', ($event.target as HTMLInputElement).value ? Number(($event.target as HTMLInputElement).value) : '')"/></label>
    </div>
    <label v-if="kind==='sql'" class="check-line"><input type="checkbox" :checked="!!exec.allowWrite" @change="setExec('allowWrite', ($event.target as HTMLInputElement).checked)"/> 允许写（DML；默认只读会话，勾选后关闭）</label>
    <p class="muted tight">执行参数为可选项；测试与运行前都会经过配置检查。</p>
  </template>
</div>
</template>
<style scoped>
.node-config{padding:0}
.tight{margin:4px 0 8px}
.engine-note{background:var(--blue-soft);color:var(--blue-ink);border-radius:6px;padding:8px 10px;font-size:12px;margin-bottom:10px}
.param-fold{border:1px solid var(--line);border-radius:8px;margin:8px 0;overflow:hidden}
.param-summary{display:flex;flex-direction:column;align-items:flex-start;gap:2px;width:100%;text-align:left;border:0;background:transparent;padding:9px 10px}
.param-summary:hover{background:var(--paper-2)}
.param-body{border-top:1px solid var(--line);padding:10px}
.form-row{display:flex;gap:10px}
.form-row label{flex:1}
.mode-row{display:flex;gap:6px;margin-bottom:8px}
.mode-row .mini.active{background:var(--blue-soft);border-color:var(--blue-line);color:var(--blue-ink)}
.row-between{display:flex;align-items:end;gap:8px}
.row-between label{flex:1;margin-bottom:0}
.code-tools{display:flex;gap:5px;flex:none}
.code-editor{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12.5px;line-height:1.65;white-space:pre;overflow:auto;min-height:110px}
.code-editor.wrap{white-space:pre-wrap;word-break:break-word}
.check-line{display:flex;align-items:center;gap:6px}
.check-line input{width:auto}
.inline-check{width:auto}
.mapping-row{align-items:end}
.field-flash{outline:2px solid var(--blue,#2458d5);background:var(--blue-soft,#edf3ff)!important;border-radius:4px;transition:background .6s ease}
</style>
