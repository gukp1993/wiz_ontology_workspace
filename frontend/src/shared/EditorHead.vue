<!-- 编辑表单头部返回控件（2026-09-20 全局统一样式）：对象/链接（ObjectWorkspace）、
     私有属性与共享属性（PropertyManager）、业务规则、动作定义五类编辑表单共用同一行控件，
     不再各自复制一份按钮行——此前规则表单缺这一行、动作的返回入口留在列表页头，样式与其他页不一致。
     从图谱跳入（canvasReturn 非空）：显示「← 返回图谱」+「关闭」；
     非图谱来源：显示 backLabel 指定的返回入口（调用方不传则不渲染，返回路径由表单底部「取消」承担）。 -->
<script setup lang="ts">
defineProps<{ canvasReturn?: string; backLabel?: string; closeLabel?: string }>()
const emit = defineEmits(['back-to-graph', 'close'])
</script>
<template>
<div v-if="canvasReturn || backLabel" class="editor-head">
  <button v-if="canvasReturn" type="button" @click="emit('back-to-graph')">← 返回图谱</button>
  <button v-else type="button" @click="emit('close')">{{ backLabel }}</button>
  <button v-if="canvasReturn" type="button" @click="emit('close')">{{ closeLabel || '关闭' }}</button>
</div>
</template>
<style scoped>
.editor-head{display:flex;align-items:center;gap:10px;margin-bottom:14px;flex-wrap:wrap}
</style>
