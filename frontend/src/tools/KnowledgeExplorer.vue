<script setup lang="ts">
import {computed,ref,watch} from 'vue'
import type {WorkbenchState} from '../ontology/modelFormat'
import type {KnowledgeKind} from './knowledgeTypes'
import {buildKnowledgeModel} from './knowledgeModel'
import KnowledgeCanvas from './KnowledgeCanvas.vue'
import AppSelect from '../shared/AppSelect.vue'
import KnowledgeDetails from './KnowledgeDetails.vue'
const props=defineProps<{state:WorkbenchState;focusId?:string}>()
const emit=defineEmits<{navigate:[page:string]}>()
const model=computed(()=>buildKnowledgeModel(props.state))
const presets=[{id:'business',name:'业务模型',hint:'对象、链接和节点内属性'},{id:'full',name:'完整定义',hint:'展开属性、计算、动作及接口'},{id:'mapping',name:'项目映射',hint:'在完整定义上叠加当前项目的数据来源'}]
const categories:{id:KnowledgeKind;label:string}[]=[{id:'object',label:'对象类型'},{id:'property',label:'属性'},{id:'function',label:'计算定义'},{id:'action',label:'动作定义'},{id:'interface',label:'接口定义'},{id:'mapping',label:'项目数据映射'}]
const preset=ref('business'),enabled=ref<KnowledgeKind[]>(['object']),links=ref(true),inline=ref(true),query=ref(''),selectedId=ref(''),edgeId=ref(''),details=ref(true)
function choose(id:string){preset.value=id;enabled.value=id==='business'?['object']:categories.filter(c=>id==='mapping'||c.id!=='mapping').map(c=>c.id);links.value=true;inline.value=id==='business';query.value='';selectedId.value='';edgeId.value=''}
function toggle(kind:KnowledgeKind){enabled.value=enabled.value.includes(kind)?enabled.value.filter(k=>k!==kind):[...enabled.value,kind];preset.value='custom'}
const searchMatches=computed(()=>{const q=query.value.trim().toLowerCase();return new Set(model.value.nodes.filter(n=>!q||[n.label,n.resourceId,n.description,...(n.properties||[]).map(p=>p.label)].join(' ').toLowerCase().includes(q)).map(n=>n.id))})
const nodes=computed(()=>{let allowed=model.value.nodes.filter(n=>enabled.value.includes(n.kind));if(query.value.trim()){const keep=new Set(searchMatches.value);for(const e of model.value.edges)if(keep.has(e.source)||keep.has(e.target)){if(searchMatches.value.has(e.source))keep.add(e.target);if(searchMatches.value.has(e.target))keep.add(e.source)}allowed=allowed.filter(n=>keep.has(n.id))}return allowed})
const edges=computed(()=>{const ids=new Set(nodes.value.map(n=>n.id));return model.value.edges.filter(e=>ids.has(e.source)&&ids.has(e.target)&&(e.kind!=='link'||links.value))})
const selectedNode=computed(()=>model.value.nodes.find(n=>n.id===selectedId.value)||null)
const selectedEdge=computed(()=>model.value.edges.find(e=>e.id===edgeId.value)||null)
function select(id:string){selectedId.value=id;edgeId.value='';if(id)details.value=true;const n=model.value.nodes.find(n=>n.id===id);if(n&&!enabled.value.includes(n.kind)){enabled.value.push(n.kind);preset.value='custom'}if(id&&!nodes.value.some(n=>n.id===id))query.value=''}
function selectEdge(id:string){edgeId.value=id;selectedId.value='';if(id)details.value=true}
watch(()=>props.focusId,id=>{if(id){const n=model.value.nodes.find(n=>n.resourceId===id);if(n)select(n.id)}},{immediate:true})
watch(nodes,ns=>{if(selectedId.value&&!ns.some(n=>n.id===selectedId.value))selectedId.value='';if(edgeId.value&&!edges.value.some(e=>e.id===edgeId.value))edgeId.value=''})
</script>
<template>
<section class="knowledge-explorer">
 <div class="ke-head"><div><h2>当前本体 · 完整模型画布</h2><p>由当前草稿的定义与映射自动生成，包含尚未保存的修改。点选资源查看定义与来源。</p></div><button @click="emit('navigate','graph')">编辑对象与链接 ↗</button></div>
 <div class="ke-presets" aria-label="模型展示方案"><button v-for="p in presets" :key="p.id" :class="{active:preset===p.id}" :aria-pressed="preset===p.id" @click="choose(p.id)"><strong>{{p.name}}</strong><small>{{p.hint}}</small></button></div>
 <div class="ke-options"><label v-for="c in categories" :key="c.id"><input type="checkbox" :checked="enabled.includes(c.id)" @change="toggle(c.id)">{{c.label}} <small>{{model.nodes.filter(n=>n.kind===c.id).length}}</small></label><label><input v-model="links" type="checkbox" @change="preset='custom'">链接类型 <small>{{model.edges.filter(e=>e.kind==='link').length}}</small></label></div>
 <div class="ke-search"><input v-model="query" aria-label="搜索模型资源" placeholder="搜索名称、标识或属性；同时保留直接关联"><button v-if="query" @click="query=''">清空搜索</button><AppSelect :model-value="selectedId" aria-label="定位模型资源" :options="[{value:'',label:'定位模型资源…'},...nodes.map(n=>({value:n.id,label:n.label+' · '+categories.find(c=>c.id===n.kind)?.label}))]" @update:model-value="select"/><label><input v-model="inline" type="checkbox">节点内显示属性</label><button @click="details=!details">{{details?'收起详情':'展开详情'}}</button><span>{{nodes.length}} 个节点 · {{edges.length}} 条连线</span></div>
 <p class="ke-legend">实线：业务链接类型 · 虚线：属性引用、计算依赖、接口实现或数据映射。完整定义中的属性节点是模型资源，不是储能业务对象。</p>
 <p v-if="enabled.includes('mapping')" class="ke-project">当前项目：{{state.project?.name || state.project?.id || '创智园'}}。{{state.bindings?.notice || '数据来源与字段映射取自当前项目配置。'}}</p>
 <details v-if="model.warnings.length" class="ke-warnings"><summary>{{model.warnings.length}} 项定义或引用提示（点击查看）</summary><ul><li v-for="(w,i) in model.warnings" :key="i">{{w}}</li></ul></details>
 <div class="ke-workspace" :class="{expanded:!details}"><KnowledgeCanvas :nodes="nodes" :edges="edges" :selected-id="selectedId || edgeId" :show-properties="inline" @select="select" @select-edge="selectEdge"/><KnowledgeDetails v-if="details" :node="selectedNode" :edge="selectedEdge" :model="model" @navigate="emit('navigate',$event)" @select="select"/></div>
</section>
</template>
<style scoped>
.knowledge-explorer{display:grid;gap:14px}.ke-head{display:flex;align-items:center;justify-content:space-between;gap:20px}.ke-head h2{margin:0 0 8px;font-size:20px}.ke-head p,.ke-legend{margin:0;color:var(--muted);font-size:13px;line-height:1.7}.ke-presets{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}.ke-presets button{text-align:left;padding:13px 16px;background:var(--paper);border:1px solid var(--line);border-radius:8px}.ke-presets button.active{border-color:#4162d6;background:var(--blue-soft);box-shadow:inset 0 0 0 1px #4162d6}.ke-presets strong,.ke-presets small{display:block}.ke-presets strong{font-size:15px;margin-bottom:5px}.ke-presets small{font-size:12px;color:var(--muted)}.ke-options{display:flex;gap:14px;flex-wrap:wrap;background:var(--paper);padding:12px;border:1px solid var(--line);border-radius:8px}.ke-options label,.ke-search label{display:flex;gap:6px;align-items:center;font-size:13px;white-space:nowrap;margin:0}.ke-options small{color:var(--muted)}.ke-options input,.ke-search input[type=checkbox]{width:auto;margin:0;accent-color:#4162d6}.ke-search{display:flex;gap:12px;align-items:center;flex-wrap:wrap}.ke-search>input{max-width:420px;flex:1;min-width:220px;margin:0}.ke-search>:deep(.app-select){width:260px;max-width:100%;flex:0 1 260px}.ke-search span{font-size:12px;color:var(--muted);margin-left:auto}.ke-workspace{display:grid;grid-template-columns:minmax(0,1fr) 320px;gap:14px;align-items:start}.ke-workspace.expanded{grid-template-columns:1fr}.ke-project{font-size:13px;margin:0;background:var(--blue-soft);padding:10px;border-radius:7px}.ke-warnings{background:var(--warn-soft);border:1px solid var(--warn-line);border-radius:7px;padding:10px;font-size:13px}.ke-warnings summary{cursor:pointer}.ke-warnings ul{max-height:220px;overflow:auto;padding-left:20px;line-height:1.8}@media(max-width:1100px){.ke-workspace{grid-template-columns:1fr}.ke-presets small{line-height:1.5}}@media(max-width:650px){.ke-presets{grid-template-columns:1fr}.ke-head{align-items:flex-start;flex-direction:column}}
</style>
