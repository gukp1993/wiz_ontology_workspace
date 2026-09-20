<template>
  <div class="modal-mask" :class="{ show }">
    <div class="modal nodeedit">
      <div class="head">
        <h3>节点编辑</h3>
        <span class="kicker">
          <i class="dot" :style="{ background: TYPE_COLOR[node.type] }"></i>
          <span>{{ node.type }}节点 · {{ node.name }}</span>
        </span>
      </div>
      <div class="fields">
        <div class="field">
          <label>节点名称</label>
          <input v-model="name" type="text" autocomplete="off" @keydown.enter="save" />
        </div>
        <div class="field">
          <label>节点类型（创建后不可修改）</label>
          <select :value="node.type" disabled>
            <option v-for="t in SEMANTIC_TYPES" :key="t" :value="t">{{ t }}</option>
          </select>
        </div>
        <template v-if="schema.length">
          <div class="divider">详情字段</div>
          <div v-for="spec in schema" :key="spec.key" class="field">
            <label>
              {{ spec.label }}
              <span v-if="spec.hint" class="hint">· {{ spec.hint }}</span>
            </label>
            <textarea
              v-if="spec.type === 'textarea' || spec.type === 'list'"
              v-model="form[spec.key]"
              :class="spec.type === 'list' ? 'list' : spec.cls || ''"
              :rows="spec.type === 'list' ? 3 : 4"
            ></textarea>
            <select v-else-if="spec.type === 'select'" v-model="form[spec.key]">
              <option value="">（未设置）</option>
              <option v-for="o in spec.options" :key="o" :value="o">{{ o }}</option>
            </select>
            <input v-else v-model="form[spec.key]" type="text" :readonly="spec.readonly" :title="spec.readonly ? spec.hint || '不可修改' : ''" />
          </div>
          <!-- 历史只读区（20260920 字段精简）：旧 output 等保留字段有值时展示，不参与编辑与保存。 -->
          <div v-for="spec in legacyFields" :key="'legacy-' + spec.key" class="field">
            <label>{{ spec.label }} <span class="hint">· 历史数据，仅展示</span></label>
            <textarea :value="props.node.data?.[spec.key] || ''" rows="3" readonly></textarea>
          </div>
        </template>
      </div>
      <p class="err" :class="{ show: !!error }">{{ error }}</p>
      <div class="btns">
        <button class="btn danger" @click="emit('delete')">删除</button>
        <button class="btn" @click="emit('close')">取消</button>
        <button class="btn primary" @click="save">保存</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, watch } from 'vue'
import { SEMANTIC_TYPES, TYPE_COLOR } from '../shared/constants'
import { FIELDS, LEGACY_FIELDS } from '../shared/fields'

const props = defineProps({
  show: { type: Boolean, required: true },
  node: { type: Object, default: () => ({ type: '实体', name: '', data: {} }) },
  existingNames: { type: Array, default: () => [] },
})
const emit = defineEmits(['save', 'delete', 'close'])

const name = ref('')
const form = reactive({})
const error = ref('')

const schema = computed(() => FIELDS[props.node.type] || [])
// 历史只读字段（20260920 字段精简）：有值才展示，不参与编辑与保存。
const legacyFields = computed(() => (LEGACY_FIELDS[props.node.type] || []).filter(spec => String(props.node.data?.[spec.key] || '').trim()))

watch(
  () => props.show,
  (on) => {
    if (!on) return
    name.value = props.node.name
    error.value = ''
    ;(FIELDS[props.node.type] || []).forEach((spec) => {
      const v = props.node.data?.[spec.key]
      form[spec.key] = Array.isArray(v) ? v.join('\n') : v ?? ''
    })
  },
)

function collectData() {
  const data = {}
  ;(FIELDS[props.node.type] || []).forEach((spec) => {
    const v = form[spec.key]
    data[spec.key] =
      spec.type === 'list'
        ? String(v || '')
            .split('\n')
            .map((s) => s.trim())
            .filter(Boolean)
        : v
  })
  return data
}

function save() {
  const n = name.value.trim()
  const errs = []
  if (!n) errs.push('名称不能为空')
  if (n !== props.node.name && props.existingNames.includes(n)) errs.push(`名称「${n}」已存在`)
  if (errs.length) {
    error.value = errs.join('；')
    return
  }
  emit('save', { name: n, data: collectData() })
}
</script>

<style scoped>
.modal.nodeedit {
  width: 880px;
  max-width: 94vw;
}
.head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 14px;
}
.head h3 { margin: 0; }
.kicker {
  display: flex;
  gap: 6px;
  align-items: center;
  color: var(--muted);
  font-size: 11px;
  white-space: nowrap;
}
.dot {
  width: 9px;
  height: 9px;
  border-radius: 2px;
  flex: 0 0 auto;
}
.fields { max-height: 56vh; overflow-y: auto; padding-right: 4px; }
.divider {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 2px 0 10px;
  color: var(--muted);
  font-size: 10px;
  font-weight: 800;
  letter-spacing: 0.05em;
}
.divider::after {
  content: '';
  flex: 1;
  height: 1px;
  background: var(--line);
}
.hint {
  font-weight: 400;
  font-size: 10.5px;
  color: #8a9396;
  line-height: 1.45;
}
.list, .definition {
  font-family: ui-monospace, Menlo, Consolas, monospace;
  font-size: 12px;
}
.definition { min-height: 86px; }
</style>
