<!-- ToolsPage — “更多工具”页（P01）。旧辅助页面（actions/interfaces/discover/knowledge/explorer/instances/learning/mapping/valuetypes）
     全部保留可达：本页只提供入口卡片，页面组件仍由 App.vue 挂载（“App 挂载旧组件的分支全部保留”）。
     emits：navigate(view) — 交给 App 统一导航；旧 view 名经 App 的 alias 自动落到新页面（如 mapping→binding）。 -->
<script setup lang="ts">
const emit = defineEmits<{ navigate: [view: string] }>()
const tools = [
  { view: 'contracts', name: '计算契约（已有定义维护）', desc: '保留已有契约及项目引用的维护入口，不属于本体基础建设步骤。' },
  { view: 'actions', name: '动作定义', desc: '已并入本体主菜单「动作定义」；新格式只有三项业务字段，历史动作只读查看。' },
  { view: 'interfaces', name: '接口定义', desc: '维护本体对外暴露的接口契约描述。' },
  { view: 'discover', name: '本体浏览', desc: '按资源类型浏览定义、属性与引用关系。' },
  { view: 'knowledge', name: '完整知识模型', desc: '以画布方式查看草稿的全部定义、映射与来源。' },
  { view: 'explorer', name: '对象数据浏览', desc: '浏览当前项目的实例数据与对象间关系。' },
  { view: 'instances', name: '实例与计算预览', desc: '预览项目实例与 SOC 等计算结果（演示执行器）。' },
  { view: 'learning', name: '历史图谱导览', desc: '查看导入的历史图谱分类与教学材料。' },
  { view: 'mapping', name: '旧版映射入口', desc: '历史映射页签已并入“对象映射”，点击直达。' },
  { view: 'valuetypes', name: '值类型管理', desc: '维护值类型、约束与示例格式，供属性引用。' },
]
</script>

<template>
<section class="card">
  <div class="tools-grid">
    <div v-for="t in tools" :key="t.view" class="tool-card card">
      <h3>{{t.name}}</h3>
      <p class="muted">{{t.desc}}</p>
      <button @click="emit('navigate',t.view)">打开 →</button>
    </div>
  </div>
</section>
</template>

<style scoped>
.tools-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:14px}
.tool-card{margin:0;display:flex;flex-direction:column;align-items:flex-start;gap:8px;transition:border-color .15s ease,background-color .15s ease}
.tool-card:hover{border-color:var(--line-2);background:var(--paper-2)}
.tool-card h3{font-size:16px}
.tool-card p{margin:0;flex:1}
</style>
