<!-- LegacyGraphHost —— 迁入编辑器的宿主薄壳（需求 §4：接入边界，不含交互逻辑）。
     职责：把 App 的本体草稿/保存状态/导航递给 EditorView；本体管理弹窗的
     切换/新建事件向上转发（App switchOntology/createOntology 保留未保存守卫）。 -->
<template>
  <div class="lgh">
    <EditorView
      :state="state"
      :ontology-id="ontologyId"
      :save-state="saveState"
      :latest-release="latestRelease"
      :focus-target="focusTarget"
      @navigate="(v, f) => emit('navigate', v, f)"
      @before-change="p => emit('before-change', p)"
      @changed="() => emit('changed')"
      @switch-ontology="id => emit('switch-ontology', id)"
      @create-ontology="name => emit('create-ontology', name)"
    />
  </div>
</template>

<script setup lang="ts">
import EditorView from './EditorView.vue'

defineProps<{
  state: any
  ontologyId: string
  saveState?: { kind: string; text: string; hint?: string }
  latestRelease?: string
  focusTarget?: string
}>()
const emit = defineEmits(['navigate', 'before-change', 'changed', 'switch-ontology', 'create-ontology'])
</script>

<style scoped>
.lgh { height: calc(100vh - 190px); min-height: 520px; background: var(--canvas); border: 1px solid var(--line); border-radius: var(--r-md); overflow: hidden; }
@media(max-width: 900px) { .lgh { height: auto; min-height: 480px; } }
</style>
