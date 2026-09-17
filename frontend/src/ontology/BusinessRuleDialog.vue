<!-- 业务规则弹窗（20260917 一期）：detail=只读四字段详情；edit=四字段编辑表单。
     20260917 浅色改版：外层对齐全局弹窗（.modal-backdrop + .modal-card dialog-md，
     宽度 620px，内边距/滚动由全局 .modal-card 提供），底部按钮区用全局 .dialogtools
     （右对齐：查看=关闭+编辑，编辑=取消+保存），不再自定义卡片头/脚与宽度。
     字段校验由父组件完成（errors 传回并聚焦首个缺失字段），
     表单输入经 emit('field', key, value) 写回父组件草稿，弹窗自身不改数据。
     allowEdit 控制详情底部是否显示"编辑"入口（规则库 true，对象规则页签只读 false）。 -->
<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
import { RULE_FIELDS } from './businessRuleModel'

const props = defineProps<{ mode: 'detail' | 'edit'; rule: any; isNew?: boolean; saving?: boolean; errors?: Record<string, string>; allowEdit?: boolean }>()
const emit = defineEmits(['close', 'save', 'edit', 'field'])

const formEl = ref<HTMLElement | null>(null)
watch(() => props.errors, async (e) => {
  if (!e || !Object.keys(e).length) return
  await nextTick()
  const first = RULE_FIELDS.find(([k]) => e[k])
  if (first) formEl.value?.querySelector<HTMLElement>(`[data-field="${first[0]}"]`)?.focus()
}, { deep: true })

function fieldError(key: string) { return props.errors?.[key] || '' }
function onKeydown(e: KeyboardEvent) { if (e.key === 'Escape') { e.stopPropagation(); emit('close') } }
function input(key: string, event: Event) { emit('field', key, (event.target as HTMLInputElement | HTMLTextAreaElement).value) }
function rowsOf(key: string) { return key === 'content' ? 10 : 3 }
</script>
<template>
<div class="modal-backdrop" @click.self="emit('close')">
  <section class="modal-card dialog-md" role="dialog" aria-modal="true" :aria-label="mode === 'detail' ? '规则详情' : (isNew ? '新建规则' : '编辑规则')" @keydown.esc="onKeydown">
    <h2>{{ mode === 'detail' ? '规则详情' : (isNew ? '新建规则' : '编辑规则') }}</h2>

    <template v-if="mode === 'detail'">
      <section v-for="[key, label] in RULE_FIELDS" :key="key" class="brc-readfield"><h3>{{ label }}</h3><p>{{ rule?.[key] || '—' }}</p></section>
      <div class="dialogtools"><button type="button" @click="emit('close')">关闭</button><button v-if="allowEdit" type="button" class="primary" @click="emit('edit')">编辑</button></div>
    </template>

    <form v-else ref="formEl" @submit.prevent="emit('save')">
      <label v-for="[key, label] in RULE_FIELDS" :key="key" class="brc-field">{{ label }} <span class="required-mark" aria-hidden="true">*</span>
        <input v-if="key === 'name'" :data-field="key" :value="rule?.[key] || ''" autocomplete="off" :aria-invalid="!!fieldError(key)" @input="input(key, $event)">
        <textarea v-else :data-field="key" :value="rule?.[key] || ''" :rows="rowsOf(key)" :aria-invalid="!!fieldError(key)" @input="input(key, $event)"></textarea>
        <small v-if="fieldError(key)" class="field-error" role="alert">{{ fieldError(key) }}</small>
      </label>
      <div class="dialogtools"><button type="button" @click="emit('close')">取消</button><button type="submit" class="primary" :disabled="saving">{{ saving ? '保存中…' : '保存' }}</button></div>
    </form>
  </section>
</div>
</template>
<style scoped>
/* 仅保留四字段排版；卡片外观/按钮区走全局 .modal-card/.dialogtools。 */
.brc-readfield{margin-top:18px}
.brc-readfield:first-of-type{margin-top:4px}
.brc-readfield h3{margin:0 0 6px;font-size:14px}
.brc-readfield p{margin:0;white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.9}
.brc-field{display:block;margin-top:16px;font-weight:550;color:var(--ink)}
.brc-field input,.brc-field textarea{display:block;width:100%;margin-top:7px;font-weight:400}
</style>
