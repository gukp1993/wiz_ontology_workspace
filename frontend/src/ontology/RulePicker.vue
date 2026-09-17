<!-- 为对象添加规则的弹窗（20260917 一期）：多选、搜索、已勾选关闭需确认。
     2026-09 交互统一：与「添加动作」「从属性库添加」共用同一个选择器外壳 PickerDialog。 -->
<script setup lang="ts">
import { computed } from 'vue'
import PickerDialog from './PickerDialog.vue'

const props = defineProps<{ rules: any[]; objectName: string }>()
const emit = defineEmits(['close', 'confirm'])
const items = computed(() => props.rules.map((r: any) => ({ id: r.id, label: r.name || '未命名规则', note: r.description || '' })))
</script>
<template>
<PickerDialog
  :title="'为「' + objectName + '」添加规则'"
  hint="勾选要引用的规则；一条规则可被多个对象类型引用，移除引用不会删除规则。"
  :items="items"
  search-placeholder="搜索规则名称"
  confirm-label="添加所选规则"
  assign-label="规则"
  empty-text="没有可添加的规则。"
  empty-hint="先到「业务规则」页新建规则，再回到这里添加。"
  @close="emit('close')"
  @confirm="emit('confirm', $event)"/>
</template>
