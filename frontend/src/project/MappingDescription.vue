<!-- MappingDescription — 项目说明编辑（20260919 项目映射 v2.1 最小组件）。
     输入区只有一个 textarea（填写提示仅放 placeholder，无插入变量/连接选择/字数工具栏）；
     「说明」标题可折叠：折叠只改变展示，不清空文字、不触发保存、不算业务变更——
     文本经 update:modelValue 上抛，由父级并入表单草稿（dirty/保存/取消/离开保护随父级）。 -->
<script setup lang="ts">
import { ref } from 'vue'
const props = defineProps<{ hint: string; modelValue: string; rows?: number }>()
const emit = defineEmits(['update:modelValue'])
const open = ref(true)
function onInput(e: Event) { emit('update:modelValue', (e.target as HTMLTextAreaElement).value) }
</script>
<template>
<div class="md-desc">
<div class="md-head"><span class="md-title">说明</span><button type="button" class="md-fold" @click="open = !open">{{ open ? '收起 ▴' : '展开 ▾' }}</button></div>
<textarea v-if="open" :value="modelValue" :placeholder="props.hint" :rows="props.rows || 7" aria-label="项目说明" @input="onInput"></textarea>
<p v-else-if="!modelValue.trim()" class="md-collapsed muted">（暂无说明）</p>
<p v-else class="md-collapsed md-preview">{{ modelValue }}</p>
</div>
</template>
<style scoped>
.md-desc{margin:0 0 18px}
.md-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:8px}
.md-title{font-size:13px;font-weight:600}
.md-fold{border:0;background:none;color:var(--muted);font-size:12px;padding:2px 4px}
.md-fold:hover{color:var(--blue);background:none}
textarea{display:block;width:100%;min-height:140px;resize:vertical;border:1px solid #ccd8e6;border-radius:7px;padding:12px 14px;font-size:13px;line-height:1.9;color:var(--ink);background:#fff;margin:0}
textarea:focus{outline:2px solid var(--focus);outline-offset:-1px}
.md-collapsed{margin:0;padding:10px 12px;border:1px dashed var(--line);border-radius:7px;font-size:13px}
.md-preview{white-space:pre-wrap;max-height:88px;overflow:hidden}
</style>
