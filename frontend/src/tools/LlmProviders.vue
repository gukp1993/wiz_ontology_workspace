<!-- LlmProviders — LLM 提供方配置页（编排 Python/计算节点 LLM 代执行用）。
     密钥只写不读回：列表只有元数据与「密钥已配置」状态；编辑留空 = 沿用已存密钥；
     接口地址同样不回显，编辑需重填。测试连通为极小探测请求（服务端不持全局锁）。 -->
<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { appConfirm } from '../shared/appConfirm'
import * as llm from './llm'

const items = ref<any[]>([])
const loading = ref(true), loadError = ref('')
const notice = ref('')
let noticeTimer: any = null
function warn(text: string) { notice.value = text; clearTimeout(noticeTimer); noticeTimer = setTimeout(() => notice.value = '', 5000) }

const dialog = ref(false)
const editingId = ref('')
const form = ref({ name: '', endpoint: '', model: '', apiKey: '', timeout: 60, temperature: 0, isDefault: false })
const testingId = ref(''), testResults = ref<Record<string, string>>({})

async function load() {
  loading.value = true; loadError.value = ''
  try { items.value = (await llm.listProviders()).items } catch (e: any) { loadError.value = e?.message || '读取失败' } finally { loading.value = false }
}
onMounted(load)

function openCreate() {
  editingId.value = ''
  form.value = { name: '', endpoint: '', model: '', apiKey: '', timeout: 60, temperature: 0, isDefault: items.value.length === 0 }
  dialog.value = true
}
function openEdit(item: any) {
  editingId.value = item.id
  form.value = { name: item.name, endpoint: item.endpoint || '', model: item.model, apiKey: '',
                 timeout: item.timeout || 60, temperature: item.temperature ?? 0, isDefault: item.isDefault }
  dialog.value = true
}
async function save() {
  try {
    await llm.saveProvider({ providerId: editingId.value || undefined, name: form.value.name,
      endpoint: form.value.endpoint, model: form.value.model, apiKey: form.value.apiKey || undefined,
      timeout: Number(form.value.timeout) || 60, temperature: Number(form.value.temperature) || 0,
      isDefault: form.value.isDefault })
    dialog.value = false
    warn(editingId.value ? '已保存提供方配置。' : '已新增提供方配置。')
    await load()
  } catch (e: any) { warn(e?.message || '保存失败') }
}
async function remove(item: any) {
  if (!(await appConfirm({ message: `删除 LLM 提供方「${item.name}」？正在引用它的节点配置检查会报错。`, danger: true, confirmLabel: '删除' }))) return
  try { await llm.deleteProvider(item.id); warn('已删除。'); await load() } catch (e: any) { warn(e?.message || '删除失败') }
}
async function test(item: any) {
  testingId.value = item.id; testResults.value[item.id] = ''
  try {
    const d = await llm.testProvider({ providerId: item.id })
    testResults.value[item.id] = d.ok ? `✓ 连通正常（${d.latencyMs}ms）` : `✗ ${d.message}`
  } catch (e: any) { testResults.value[item.id] = '✗ ' + (e?.message || '测试失败') } finally { testingId.value = '' }
}
</script>
<template>
<section class="card">
  <div class="panelhead"><div>
    <h2>LLM 配置</h2>
    <p class="muted">函数编排的 Python 节点与计算节点（LLM 模式）把代码与输入交给这里配置的模型代为求值；本机不执行任何代码。密钥与接口地址只写不读回。</p>
  </div><button class="primary" @click="openCreate">＋ 新增提供方</button></div>
  <p v-if="notice" class="property-feedback" role="status">{{notice}}</p>
  <p v-if="loading" class="muted">加载中…</p>
  <p v-else-if="loadError" class="inline-error">{{loadError}} <button class="mini" @click="load">重试</button></p>
  <p v-else-if="!items.length" class="muted">尚未配置任何 LLM 提供方。Python / LLM 计算节点在配置检查中会报错并指引到这里。</p>
  <div v-else class="provider-list">
    <div v-for="item in items" :key="item.id" class="provider-row">
      <div class="grow">
        <strong>{{item.name}}</strong>
        <span v-if="item.isDefault" class="pill">默认</span>
        <div class="muted small">模型：{{item.model}} · {{item.keyConfigured ? '密钥已配置' : '未配置密钥'}}</div>
      </div>
      <small v-if="testingId===item.id" class="muted">测试中…</small>
      <small v-else-if="testResults[item.id]" :class="testResults[item.id].startsWith('✓')?'inline-success':'inline-error'">{{testResults[item.id]}}</small>
      <button class="mini" :disabled="!!testingId" @click="test(item)">测试连通</button>
      <button class="mini" @click="openEdit(item)">编辑</button>
      <button class="mini danger" @click="remove(item)">删除</button>
    </div>
  </div>

  <div v-if="dialog" class="modal-backdrop" @click.self="dialog=false">
    <section class="modal-card" role="dialog" aria-modal="true" aria-label="LLM 提供方">
      <h2>{{editingId?'编辑提供方':'新增提供方'}}</h2>
      <p class="field-help">OpenAI 兼容 chat/completions 接口。密钥留空 = 沿用已保存密钥（不回显）。</p>
      <label>名称 *<input v-model="form.name" maxlength="60" placeholder="如 DeepSeek 主力"/></label>
      <label>接口地址（chat/completions 完整 URL）*<input v-model="form.endpoint" placeholder="https://api.minimax.cn/v1/chat/completions"/></label>
      <label>模型 *<input v-model="form.model" placeholder="deepseek-chat"/></label>
      <label>API Key<input v-model="form.apiKey" type="password" :placeholder="editingId?'留空 = 沿用已保存密钥':'sk-…'"/></label>
      <div class="form-row">
        <label>超时（秒）<input v-model.number="form.timeout" type="number" min="1" max="300"/></label>
        <label>温度（求值建议 0）<input v-model.number="form.temperature" type="number" min="0" max="2" step="0.1"/></label>
      </div>
      <label class="check-line"><input v-model="form.isDefault" type="checkbox"/> 设为默认提供方</label>
      <div class="dialogtools">
        <button @click="dialog=false">取消</button>
        <button class="primary" :disabled="!form.name.trim()||!form.endpoint.trim()||!form.model.trim()" @click="save">保存</button>
      </div>
    </section>
  </div>
</section>
</template>
<style scoped>
.provider-list{display:flex;flex-direction:column;gap:8px;margin-top:10px}
.provider-row{display:flex;align-items:center;gap:10px;border:1px solid var(--line);border-radius:8px;padding:9px 14px}
.provider-row .grow{flex:1}
.provider-row .small{font-size:12px}
.pill{font-size:10px;border-radius:8px;padding:1px 8px;background:var(--blue-soft);color:var(--blue-ink);margin-left:6px}
.check-line{display:flex;align-items:center;gap:6px}
.check-line input{width:auto}
.form-row{display:flex;gap:10px}
.form-row label{flex:1}
</style>
