<script setup lang="ts">
// 关联取值/成员 只读预览面板（方案 §3.4）：用户点击触发，先取服务端已保存草稿的
// revision 再发起预览（保证预览的一定是已保存配置）；不写业务数据、不发布。
// property=null 时为仅成员预览（链接映射页使用）。
import { computed, onBeforeUnmount, ref } from 'vue'
import AppSelect from '../shared/AppSelect.vue'
import { loadProjectStateRaw, projectPropertyPreview } from './api'
const props = defineProps<{
  projectState: any
  objectType: string
  instances: { id: string; label: string }[]
  property: string | null
  title: string
}>()
const emit = defineEmits(['close'])
const instanceId = ref(props.instances[0]?.id || '')
const busy = ref(false), error = ref(''), result = ref<any>(null), evaluatedAt = ref('')
let sequence = 0
const instanceOptions = computed(() => props.instances.map(i => ({ value: i.id, label: i.label ? i.label + '（' + i.id + '）' : i.id })))
const canPreview = computed(() => !!instanceId.value)
async function run() {
  if (!canPreview.value || busy.value) return
  const seq = ++sequence
  busy.value = true; error.value = ''; result.value = null
  try {
    // 取服务端已保存草稿的 revision：预览必须针对已保存配置，未保存修改不参与
    const st = await loadProjectStateRaw(props.projectState.projectId)
    if (seq !== sequence) return
    const d = await projectPropertyPreview({
      projectId: props.projectState.projectId, revision: st.revision,
      objectType: props.objectType, instanceId: instanceId.value, property: props.property,
    })
    if (seq !== sequence) return
    result.value = d
    evaluatedAt.value = d.evaluatedAt ? new Date(d.evaluatedAt).toLocaleString('zh-CN', { hour12: false }) : ''
  } catch (e: any) {
    if (seq === sequence) error.value = e?.message || String(e)
  } finally {
    if (seq === sequence) busy.value = false
  }
}
onBeforeUnmount(() => { sequence++ })
const statusText: Record<string, string> = { ok: '查询成功', empty: '未匹配到成员', incomplete: '结果不完整（有成员数值缺失）', error: '执行失败' }
const statusCls: Record<string, string> = { ok: 'pill-ok', empty: 'pill-pending', incomplete: 'pill-pending', error: 'pill-error' }
</script>
<template>
<section class="sample-panel sp-panel" aria-label="预览">
  <div class="panelhead">
    <strong>{{ title }}</strong>
    <div class="tools">
      <label v-if="instances.length > 1" class="sp-inst">实例<AppSelect v-model="instanceId" aria-label="预览实例" :options="instanceOptions" /></label>
      <button class="primary" :disabled="!canPreview || busy" @click="run">{{ busy ? '查询中…' : '预览' }}</button>
      <button @click="emit('close')">关闭</button>
    </div>
  </div>
  <p class="field-help">只读预览，基于<strong>已保存</strong>的项目配置与数据库当前数据；不写入业务数据、不代表持续实时一致。成员属性需为身份表直接数值字段。</p>
  <p v-if="error" class="inline-error" role="alert">{{ error }}</p>
  <template v-if="result">
    <div class="sp-result">
      <span class="status-pill" :class="statusCls[result.status] || ''">{{ statusText[result.status] || result.status }}</span>
      <template v-if="property !== null">
        <strong class="sp-value">{{ result.value ?? '（空）' }}</strong>
        <small>成员 {{ result.memberCount }} · 缺失 {{ result.missingCount }}</small>
      </template>
      <template v-else>
        <strong class="sp-value">{{ result.memberCount }}</strong>
        <small>名成员</small>
      </template>
      <small v-if="evaluatedAt" class="muted">执行于 {{ evaluatedAt }}</small>
    </div>
    <p v-if="result.message" class="field-help" role="status">{{ result.message }}</p>
    <div v-if="(result.membersPreview || []).length" class="scroll">
      <table class="sp-members">
        <thead><tr><th>#</th><th v-for="k in Object.keys(result.membersPreview[0]).filter(k => k !== 'id')" :key="k">{{ k }}</th></tr></thead>
        <tbody>
          <tr v-for="(m, i) in result.membersPreview" :key="i"><td>{{ Number(i) + 1 }}</td><td v-for="k in Object.keys(m).filter(k => k !== 'id')" :key="k"><span class="mono">{{ m[k] }}</span></td></tr>
        </tbody>
      </table>
    </div>
    <p v-if="result.truncated" class="inline-warning">成员超过 20 条，仅显示前 20 条明细。</p>
  </template>
</section>
</template>
<style scoped>
.sp-panel{margin-top:14px}
.sp-inst{display:flex;align-items:center;gap:8px;min-width:240px}
.sp-inst :deep(.app-select){flex:1}
.sp-result{display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin:10px 0}
.sp-value{font-size:20px;font-weight:650}
.sp-members{width:100%}
</style>
