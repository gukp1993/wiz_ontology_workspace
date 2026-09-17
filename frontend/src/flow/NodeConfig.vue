<!-- NodeConfig — 处理节点右侧配置（v2 五类：Python/SQL/Redis/HTTP/计算）。
     名称/说明、输入声明（来源绑定）、输出声明、实现正文、执行参数（超时/行数上限/允许写）。
     Python/LLM 计算不在本机执行：由所选 LLM 提供方代为求值（providers 留空时给出配置指引）。
     「测试本节点」emit('test')：由编辑页打开指定输入的测试弹层。 -->
<script setup lang="ts">
import { computed, ref } from 'vue'
import { appConfirm } from '../shared/appConfirm'
import AppSelect from '../shared/AppSelect.vue'
import TypeEditor from './TypeEditor.vue'
import BindingEditor from './BindingEditor.vue'
import { EXEC_DEFAULT_TIMEOUT, EXEC_MAX_TIMEOUT_MS, HTTP_METHODS, NODE_KIND_LABELS, REDIS_COMMAND_OPTIONS, SQL_MAX_ROWS_MAX, TYPE_LABELS, flowTypeToCalcType, outputRemovalImpact, pythonSkeleton, sourceSummary, uid } from './flowModel'
const props = defineProps<{ state: any; node: any; connections?: any[]; credentials?: any[]; providers?: any[] }>()
const emit = defineEmits(['before-change', 'changed', 'test'])
const notice = ref('')
let noticeTimer: any = null
function warn(text: string) { notice.value = text; clearTimeout(noticeTimer); noticeTimer = setTimeout(() => notice.value = '', 6000) }
const kind = computed(() => props.node.kind as string)
const isPython = computed(() => kind.value === 'python')
const isRedis = computed(() => kind.value === 'redis')
const isHttp = computed(() => kind.value === 'http')
const isCalc = computed(() => kind.value === 'calc')
const needsLlm = computed(() => isPython.value || (isCalc.value && props.node.implementation?.mode === 'llm'))
const wantedEngine = computed(() => (isRedis.value ? 'redis' : 'mysql'))
/** 数据连接下拉：项目"数据连接"菜单中对应引擎的连接（仅引用其稳定 ID）；旧草稿自带声明向后兼容。 */
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
const impl = computed(() => props.node.implementation || (props.node.implementation = {}))
const exec = computed(() => props.node.execution || (props.node.execution = {}))
/** HTTP 请求头：文本（每行 名: 值）↔ 对象。 */
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
/** 技术名修改：正文含旧名时提示检查代码，不自动重写。 */
function renameTechnical(container: any, list: 'inputs' | 'outputs', index: number, next: string) {
  const item = props.node[list][index]
  const old = item.name
  if (old === next) return
  emit('before-change')
  item.name = next
  emit('changed')
  const body = isPython.value ? (props.node.implementation?.code || '') : isCalc.value ? JSON.stringify(props.node.implementation?.formulas || {}) : (props.node.implementation?.sql || '')
  if (old && body.includes(isPython.value ? old : `:${old}`) && !isHttp.value && !isRedis.value) {
    warn(`技术名「${old}」已改为「${next || '（空）'}」，正文仍引用旧名；请检查${isPython.value ? '代码参数' : 'SQL :参数'}，系统不会自动重写。`)
  } else if (old && isCalc.value && body.includes(`"${old}"`)) {
    warn(`技术名「${old}」已改为「${next || '（空）'}」，公式仍引用旧名，请检查计算定义。`)
  }
}
function setInputLabel(input: any, value: string) { emit('before-change'); input.label = value; emit('changed') }
function setOutputLabel(out: any, value: string) { emit('before-change'); out.label = value; emit('changed') }
function setField(row: any, key: string, value: any) { emit('before-change'); row[key] = value; emit('changed') }
function setBody(value: string) {
  emit('before-change')
  if (isPython.value) impl.value.code = value
  else if (isRedis.value) impl.value.keyTemplate = value
  else if (isHttp.value) impl.value.body = value
  else if (isCalc.value) impl.value.llmInstruction = value
  else impl.value.sql = value
  emit('changed')
}
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
  <div class="badge">{{NODE_KIND_LABELS[kind]?.toUpperCase() || kind}} 节点</div>
  <label>名称 *<input :value="node.name" :aria-label="'节点名称'" @input="setField(node,'name',($event.target as HTMLInputElement).value)"/></label>
  <label>说明<textarea :value="node.description" rows="2" :aria-label="'节点说明'" @input="setField(node,'description',($event.target as HTMLTextAreaElement).value)"/></label>

  <h3>输入参数</h3>
  <p class="muted">每个输入只有一个来源；绑定即业务事实，画布连线由此派生。</p>
  <div v-for="(input,index) in node.inputs" :key="input.id" class="param-card">
    <div class="mapping-row">
      <label>技术名（实现中引用）*<input :value="input.name" placeholder="如 limit_n" :aria-label="'输入技术名'" @change="renameTechnical(node,'inputs',Number(index),($event.target as HTMLInputElement).value.trim())"/></label>
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

  <h3 v-if="isPython">Python 代码（LLM 代执行）</h3>
  <h3 v-else-if="isHttp">HTTP 接口</h3>
  <h3 v-else-if="isCalc">计算定义</h3>
  <h3 v-else-if="isRedis">Redis 命令</h3>
  <h3 v-else>SQL 模板（MyBatis 风格）</h3>
  <template v-if="isPython">
    <p class="muted">契约：def main(输入技术名…) → dict | None。本机不运行代码：保存后由 LLM 代为求值，结果具非确定性（运行日志保留请求与响应摘要）。</p>
    <textarea class="code-editor" :value="node.implementation?.code || ''" rows="12" spellcheck="false" aria-label="Python 代码" @input="setBody(($event.target as HTMLTextAreaElement).value)"/>
    <button class="mini" @click="refillSkeleton">按当前参数重建 main 骨架（覆盖正文）</button>
  </template>
  <template v-else-if="isCalc">
    <div class="mode-row">
      <button v-for="m in calcModeOptions" :key="m.value" class="mini" :class="{active: (impl.mode||'formula')===m.value}"
              @click="setField(node.implementation,'mode',m.value)">{{m.label}}</button>
    </div>
    <template v-if="(impl.mode||'formula')==='formula'">
      <p class="muted">受限公式引擎 calc-expression-1：四则/比较/IF/MIN/MAX/ABS/ROUND/ERROR，确定性求值（不执行任意代码）。引用输入用 <b>{技术名}</b>。</p>
      <div v-for="out in formulaOutputs" :key="out.id" class="param-card">
        <label>输出「{{out.label || out.name}}」的公式<input :value="impl.formulas?.[out.name] || ''" :aria-label="`公式 ${out.name}`" placeholder="如 ROUND(MIN({total}/10000, 100), 0)" @input="setFormula(out.name, ($event.target as HTMLInputElement).value)"/></label>
      </div>
      <p v-for="out in untitled" :key="out.id" class="inline-error">公式模式不支持 {{TYPE_LABELS[out.type?.type] || out.type?.type}} 类型的输出「{{out.label || out.name}}」</p>
    </template>
    <template v-else>
      <p class="muted">用自然语言描述计算规则，连同输入交给 LLM 计算（适合公式表达不了的逻辑；结果非确定性）。</p>
      <textarea class="code-editor" :value="impl.llmInstruction || ''" rows="4" aria-label="LLM 计算规则" @input="setBody(($event.target as HTMLTextAreaElement).value)"/>
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
    <label>请求头（每行「名: 值」）<textarea class="code-editor" :value="headersText" rows="3" aria-label="请求头" @input="headersText=($event.target as HTMLTextAreaElement).value"/></label>
    <template v-if="(impl.bodyMode||'none')==='json'">
      <label>JSON 请求体模板（{技术名} 占位；字符串占位请自带引号）<textarea class="code-editor" :value="impl.body || ''" rows="5" spellcheck="false" aria-label="请求体模板" @input="setBody(($event.target as HTMLTextAreaElement).value)"/></label>
    </template>
    <label>认证凭据（可选；项目 API 凭据，密钥只写不读回）<AppSelect :model-value="impl.credentialId || ''" :options="credentialOptions" aria-label="认证凭据" @before-change="emit('before-change')" @update:model-value="setField(impl,'credentialId',($event as string))"/>
    </label>
    <label>响应提取路径（点路径，留空 = 整个 JSON）<input :value="impl.responsePath || ''" placeholder="data" aria-label="响应提取路径" @input="setField(impl,'responsePath',($event.target as HTMLInputElement).value)"/></label>
  </template>
  <template v-else-if="isRedis">
    <label>数据连接
      <AppSelect :model-value="node.implementation?.connectionId || ''" :options="connectionOptions" aria-label="数据连接" @before-change="emit('before-change')" @update:model-value="setConnection($event as string)"/>
    </label>
    <p class="muted">数据连接来自项目映射 → 数据连接 菜单（Redis 类型）；此处只引用连接，不保存地址与凭据。{{hasConnectionOptions?'':'当前项目还没有 Redis 数据连接，请先到数据连接菜单添加。'}}</p>
    <div class="form-row">
      <label>命令（白名单）
        <AppSelect :model-value="impl.command || ''" :options="[{value:'',label:'（旧草稿：按 key 模板推断 GET）'},...REDIS_COMMAND_OPTIONS]" aria-label="Redis 命令" @before-change="emit('before-change')" @update:model-value="setField(impl,'command',($event as string))"/>
      </label>
      <label>行模式<input type="checkbox" class="inline-check" :checked="!!impl.rowMode" @change="setField(impl,'rowMode',($event.target as HTMLInputElement).checked)"/>
      </label>
    </div>
    <label>Key 模板<textarea class="code-editor" :value="node.implementation?.keyTemplate || ''" rows="2" spellcheck="false" aria-label="Redis Key 模板" @input="setBody(($event.target as HTMLTextAreaElement).value)"/></label>
    <label>命令参数（逗号分隔：输入技术名 / 数字字面量 / 行模式 <span v-pre>{{字段}}</span>）<input :value="argsText" aria-label="命令参数" @input="argsText=($event.target as HTMLInputElement).value"/></label>
    <p class="muted">行模式：输入为列表时用 <b v-pre>{{行字段}}</b> 引用元素字段，逐行执行，输出列表按序对应。单键模式：用 <b v-pre>${参数}</b> 引用输入参数。</p>
  </template>
  <template v-else>
    <label>数据连接
      <AppSelect :model-value="node.implementation?.connectionId || ''" :options="connectionOptions" aria-label="数据连接" @before-change="emit('before-change')" @update:model-value="setConnection($event as string)"/>
    </label>
    <p class="muted">数据连接来自项目映射 → 数据连接 菜单（仅 MySQL）；此处只引用连接，不保存地址与凭据。{{hasConnectionOptions?'':'当前项目还没有 MySQL 数据连接，请先到数据连接菜单添加。'}}</p>
    <p class="muted">动态标签子集：&lt;if test&gt; &lt;choose&gt; &lt;where&gt; &lt;set&gt; &lt;foreach&gt;；参数用 <b v-pre>#{技术名}</b> 或旧写法 :技术名；<b v-pre>${技术名}</b> 为原文拼接（检查会提示注入风险）。</p>
    <textarea class="code-editor" :value="node.implementation?.sql || ''" rows="10" spellcheck="false" aria-label="SQL 模板" @input="setBody(($event.target as HTMLTextAreaElement).value)"/>
  </template>

  <template v-if="needsLlm">
    <label>LLM 提供方（留空 = 默认）
      <AppSelect :model-value="impl.providerId || ''" :options="providerOptions" aria-label="LLM 提供方" @before-change="emit('before-change')" @update:model-value="setField(impl,'providerId',($event as string))"/>
    </label>
    <p v-if="!llmReady" class="inline-error">尚未配置 LLM 提供方：请到「更多工具 → LLM 配置」添加，否则此节点无法执行。</p>
  </template>

  <h3>执行参数</h3>
  <div class="form-row">
    <label>超时（毫秒，留空 = {{EXEC_DEFAULT_TIMEOUT[kind] || 30000}}）<input type="number" :min="1000" :max="EXEC_MAX_TIMEOUT_MS" :value="exec.timeoutMs ?? ''" aria-label="节点超时" @change="setExec('timeoutMs', ($event.target as HTMLInputElement).value ? Number(($event.target as HTMLInputElement).value) : '')"/></label>
    <label v-if="kind==='sql'">行数上限（留空 = 1000，最多 {{SQL_MAX_ROWS_MAX}}）<input type="number" :min="1" :max="SQL_MAX_ROWS_MAX" :value="exec.maxRows ?? ''" aria-label="行数上限" @change="setExec('maxRows', ($event.target as HTMLInputElement).value ? Number(($event.target as HTMLInputElement).value) : '')"/></label>
  </div>
  <label v-if="kind==='sql'" class="check-line"><input type="checkbox" :checked="!!exec.allowWrite" @change="setExec('allowWrite', ($event.target as HTMLInputElement).checked)"/> 允许写（DML；默认只读会话，勾选后关闭）</label>

  <div class="test-row">
    <button class="primary" @click="emit('test')">▶ 测试本节点（指定输入）</button>
  </div>
  <p class="muted">测试 = 单函数调用：给定全部输入值立即返回输出；不落盘、不影响草稿。</p>
  <p v-if="notice" class="property-feedback" role="status">{{notice}}</p>
</div>
</template>
<style scoped>
.node-config h3{margin:22px 0 8px}
.code-editor{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:13px;line-height:1.65;white-space:pre;overflow:auto;min-height:120px}
.mapping-row{align-items:end}
.form-row{display:flex;gap:10px}
.form-row label{flex:1}
.mode-row{display:flex;gap:6px;margin-bottom:8px}
.mode-row .mini.active{background:var(--blue-soft);border-color:var(--blue-line);color:var(--blue-ink)}
.check-line{display:flex;align-items:center;gap:6px}
.check-line input{width:auto}
.inline-check{width:auto}
.test-row{margin-top:16px}
</style>
