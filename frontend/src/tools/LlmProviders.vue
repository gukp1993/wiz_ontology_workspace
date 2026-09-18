<!-- LlmProviders — 模型设置页（设置中心「基础设置 → 模型设置」分类；内部文件名/providerId 保留）。
     部署级配置：所有本体、项目与函数编排共享；不属于本体/项目草稿，保存不进业务 Saver。
     密钥只写不读回：编辑留空 = 沿用已存密钥；接口地址按后端元数据正常回显（仅密钥不回传）。
     测试连通为用户主动点击的极小探测请求（服务端不持全局锁）。 -->
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
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
  formOriginal.value = JSON.stringify(form.value)
  dialog.value = true
}
function openEdit(item: any) {
  editingId.value = item.id
  form.value = { name: item.name, endpoint: item.endpoint || '', model: item.model, apiKey: '',
                 timeout: item.timeout || 60, temperature: item.temperature ?? 0, isDefault: item.isDefault }
  formOriginal.value = JSON.stringify(form.value)
  dialog.value = true
}
// 新增：API Key 必填；编辑留空沿用。配置更新后旧连通性结果作废。
const formOriginal = ref('')
const formOk = computed(() => form.value.name.trim() && form.value.endpoint.trim() && form.value.model.trim() && (editingId.value || form.value.apiKey.trim()))
const formDirty = computed(() => dialog.value && JSON.stringify(form.value) !== formOriginal.value)
async function closeDialog() {
  if (formDirty.value && !(await appConfirm({ message: '放弃未保存的修改？' }))) return
  dialog.value = false
}
async function save() {
  try {
    await llm.saveProvider({ providerId: editingId.value || undefined, name: form.value.name,
      endpoint: form.value.endpoint, model: form.value.model, apiKey: form.value.apiKey || undefined,
      timeout: Number(form.value.timeout) || 60, temperature: Number(form.value.temperature) || 0,
      isDefault: form.value.isDefault })
    testResults.value = {} // 配置已变化：旧连通性结果作废，待用户重新测试
    dialog.value = false
    warn('已保存模型配置。')
    await load()
  } catch (e: any) { warn(e?.message || '保存失败') }
}
async function remove(item: any) {
  const isDefault = item.isDefault
  const tail = isDefault && items.value.length > 1 ? '该配置当前是默认配置，删除后未明确指定的节点将改用其他可用配置。' : ''
  if (!(await appConfirm({ message: `删除模型配置「${item.name}」？正在引用它的节点配置检查会报错。${tail}`, danger: true, confirmLabel: '删除' }))) return
  try { await llm.deleteProvider(item.id); warn('已删除。'); await load() } catch (e: any) { warn(e?.message || '删除失败') }
}
async function test(item: any) {
  testingId.value = item.id; testResults.value[item.id] = ''
  try {
    const d = await llm.testProvider({ providerId: item.id })
    testResults.value[item.id] = d.ok ? `最近一次连通成功（${d.latencyMs}ms）` : `最近一次连通失败：${d.message}`
  } catch (e: any) { testResults.value[item.id] = '✗ ' + (e?.message || '测试失败') } finally { testingId.value = '' }
}
</script>
<template>
<section class="card">
  <div class="panelhead"><div>
    <h2>模型设置</h2>
    <p class="muted">配置供全工作台使用的模型接口：函数编排的 Python 节点与 LLM 计算节点把代码与输入交给这里的模型代为求值，本机不执行任何代码。配置为部署级共用；API Key 只写不读回，接口地址可回显。</p>
  </div><button class="primary" @click="openCreate">＋ 新增模型配置</button></div>
  <p v-if="notice" class="property-feedback" role="status">{{notice}}</p>
  <p v-if="loading" class="muted">加载中…</p>
  <p v-else-if="loadError" class="inline-error">{{loadError}} <button class="mini" @click="load">重试</button></p>
  <p v-else-if="!items.length" class="muted">还没有模型配置。新增一条 OpenAI 兼容接口的模型配置后，函数编排的 Python / LLM 计算节点即可使用。</p>
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

  <div v-if="dialog" class="modal-backdrop" @click.self="closeDialog">
    <section class="modal-card" role="dialog" aria-modal="true" aria-label="模型配置">
      <h2>{{editingId?'编辑模型配置':'新增模型配置'}}</h2>
      <p class="field-help">OpenAI 兼容 chat/completions 接口。API Key 加密保存、只写不读回；接口地址按已保存内容回显。</p>
      <label>配置名称 *<input v-model="form.name" maxlength="60" placeholder="例如：通用推理模型"/></label>
      <label>接口地址（chat/completions 完整 URL）*<input v-model="form.endpoint" placeholder="https://…/v1/chat/completions"/></label>
      <label>模型标识 *<input v-model="form.model" placeholder="模型参数名，例如 deepseek-chat"/></label>
      <label>API Key{{editingId?'（留空沿用已保存密钥）':' *'}}<input v-model="form.apiKey" type="password" :placeholder="editingId?'留空 = 沿用已保存密钥（不回显）':'sk-…'"/></label>
      <label class="check-line"><input v-model="form.isDefault" type="checkbox"/> 设为默认</label>
      <p class="field-help">未指定模型配置的节点将使用此默认配置；不影响已明确指定的配置。</p>
      <details class="technical-section">
        <summary>高级设置</summary>
        <div class="form-row">
          <label>超时（秒）<input v-model.number="form.timeout" type="number" min="1" max="300"/></label>
          <label>温度（求值建议 0）<input v-model.number="form.temperature" type="number" min="0" max="2" step="0.1"/></label>
        </div>
      </details>
      <div class="dialogtools">
        <button @click="closeDialog">取消</button>
        <button class="primary" :disabled="!formOk" @click="save">保存配置</button>
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
