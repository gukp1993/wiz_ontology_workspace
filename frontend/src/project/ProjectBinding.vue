<script lang="ts">
// 模块级记忆：跨挂载保留最近选择的对象（从实现页等离开后返回时直接回到原对象）。
let lastBindingType=''
</script>
<script setup lang="ts">
// 对象映射工作区（T04 · 原型 objectWorkspace 项目态）：
// 浏览态左目录（157px）右详情，页签固定三项：实例识别／属性取值／链接映射；
// 任一页签进入编辑态后主内容整体替换为该编辑器（目录与页签隐藏），保存／取消后返回。
// 离开保护统一接 T00：子编辑器自行注册表单守卫（菜单／两区／项目切换由 App 统一拦截），
// 页内切对象／切页签由这里检查当前子编辑器 dirty 并确认丢弃；不能只保护链接表单。
import {computed,nextTick,ref,watch} from 'vue'
import { appConfirm } from '../shared/appConfirm'
import ObjectSources from './ObjectSources.vue'
import ReferenceNotice from './ReferenceNotice.vue'
import PropertySources from './PropertySources.vue'
import LinkMappings from './LinkMappings.vue'
import ActionBindings from './ActionBindings.vue'
import {lmPendingOf} from './LinkMappings.vue'
import {psPendingOf} from './PropertySources.vue'
const props=defineProps<{projectState:any;refState:any;focusType?:string;report?:any;returnTo?:{view:string;focus?:Record<string,any>;label:string}}>()
const emit=defineEmits(['before-change','changed','navigate','open-ontology'])
// S4（20260920 验收）：外部依赖「去处理」跳到本页时，页头给「← 返回共享属性「x」」入口；
// 处理完映射引用可一步回到来源定义继续；openUsages 让共享库恢复引用位置抽屉（前后端接口不变）。
function backToSource(){if(props.returnTo)emit('navigate',props.returnTo.view,{...(props.returnTo.focus||{}),openUsages:true})}
const selected=ref(''),detail=ref('sources'),search=ref('')
const editorOpen=ref(false)
const graph=computed(()=>props.refState?.ontology?.['@graph']||[])
const types=computed(()=>graph.value.filter(n=>n['@type']==='owl:Class'))
const bindings=computed(()=>props.projectState.bindings.object_bindings)
const active=computed(()=>bindings.value.find((b:any)=>b.object_type===selected.value))
const visibleTypes=computed(()=>types.value.filter(t=>(t['rdfs:label']||'').includes(search.value)))
// 函数 ref 取子组件实例（v-for 外字符串 ref 不可靠）；只有当前页签的子组件挂载。
let sourcesEl:any=null,propertyEl:any=null,linksEl:any=null,actionsEl:any=null
function setSourcesRef(el:any){sourcesEl=el}
function setPropertyRef(el:any){propertyEl=el}
function setLinksRef(el:any){linksEl=el}
function setActionsRef(el:any){actionsEl=el}
function activeChild():any{
  if(detail.value==='sources')return sourcesEl
  if(detail.value==='properties')return propertyEl
  if(detail.value==='actions')return actionsEl
  return linksEl
}
// 页内离开保护：当前子编辑器有未保存草稿时确认丢弃（App 级导航由 form-guard 统一拦截）。
async function guardUnsaved():Promise<boolean>{
  const el=activeChild()
  if(!el||typeof el.dirty!=='function'||!el.dirty())return true
  if(!(await appConfirm({ message: '当前表单有未保存的修改，离开将放弃本次修改。继续吗？', confirmLabel: '放弃并离开', cancelLabel: '继续编辑' })))return false
  el.discard()
  return true
}
function pick(id:string){if(guardUnsaved())selected.value=id}
function setDetail(d:string){if(guardUnsaved())detail.value=d}
// 回位页签：有未完成的链接草稿回「链接映射」，有未完成的属性向导回「属性取值」。
function defaultDetailFor(id:string){return lmPendingOf(id)?'links':psPendingOf(id)?'properties':'sources'}
// 程序化切换对象时指定回位页签（watch(selected) 消费后清空）。
let pendingDetailOverride:string|null=null
watch(types,ts=>{
  const ids=ts.map(t=>t['@id'].slice(3))
  if(props.focusType&&ids.includes(props.focusType.replace(/^mg:/,'')))selected.value=props.focusType.replace(/^mg:/,'')
  else if(lastBindingType&&ids.includes(lastBindingType))selected.value=lastBindingType
  else if(!ids.includes(selected.value))selected.value=ids[0]||''
},{immediate:true})
watch(()=>props.focusType,id=>{if(id&&types.value.some(t=>t['@id']===id)){if(!guardUnsaved())return;selected.value=id.replace(/^mg:/,'');detail.value='sources'}},{immediate:true})
watch(selected,id=>{
  lastBindingType=id
  detail.value=pendingDetailOverride??defaultDetailFor(id)
  pendingDetailOverride=null
  editorOpen.value=false
})
function before(){emit('before-change')}
function changed(){emit('changed')}
function mutate(fn){before();fn();changed()}
const name=(id:string)=>types.value.find(n=>n['@id']==='mg:'+id)?.['rdfs:label']||id
// 业务定义只读展示，取自引用版本本体（rdfs:comment），不随项目草稿变化
const desc=(id:string)=>{const n=types.value.find(t=>t['@id']==='mg:'+id);return (n?.['rdfs:comment'] as string)||''}
const bound=(id:string)=>bindings.value.some((b:any)=>b.object_type===id)
function addObject(t:string){mutate(()=>props.projectState.bindings.object_bindings.push({object_type:t,connection:'',table:'',primary_key:'',title_key:'',properties:{},relations:[],sources:[]}))}
// 空态主按钮：启用绑定并停在实例识别页签，随后直接进入实例识别表单（selected 未变，watch 不触发，显式归位）
async function enable(t:string){
  addObject(t)
  detail.value='sources'
  await nextTick()
  sourcesEl?.openIdentity?.()
}
async function removeObject(b:any){if(!(await guardUnsaved()))return;if(await appConfirm({ message: '移除此对象的绑定？对象定义保留。', danger: true }))mutate(()=>bindings.value.splice(bindings.value.indexOf(b),1))}
// 链接映射「去配置」：目标对象的实例识别页签（链接草稿已在 LinkMappings 内暂存，返回时恢复）
function onSetupEnd(t:string){
  if(!types.value.some(x=>x['@id']==='mg:'+t))return
  if(selected.value===t){detail.value='sources';editorOpen.value=false}
  else{pendingDetailOverride='sources';selected.value=t}
}
// 入向链接「去起点对象配置」：跳到起点对象的链接映射页签
function onGotoObject(t:string){
  if(!guardUnsaved())return
  if(!types.value.some(x=>x['@id']==='mg:'+t))return
  if(selected.value===t)detail.value='links'
  else{pendingDetailOverride='links';selected.value=t}
  editorOpen.value=false
}
// 关联聚合「去链接映射配置成员」：同对象切页签（编辑器内发起，先过离开保护）
function onGoTabFromEditor(d:string){if(guardUnsaved()){detail.value=d;editorOpen.value=false}}
// 关联聚合「去终点对象配置属性取值」：跳到终点对象的属性取值页签
function onGotoTargetProperties(t:string){
  if(!types.value.some(x=>x['@id']==='mg:'+t))return
  if(selected.value===t)detail.value='properties'
  else{pendingDetailOverride='properties';selected.value=t}
  editorOpen.value=false
}
</script>
<template><div v-if="!refState" class="card"><div class="empty-state"><div class="empty-state-ico">◇</div><p>该项目尚未绑定本体版本（或引用版本无法读取）。请到「项目概览」页的项目信息中完成「绑定本体与版本」后，再开始对象映射。</p><button @click="emit('navigate','p-home')">返回项目概览</button></div></div>
<template v-else>
<p class="muted binding-intro">先选对象，再配置实例识别、属性取值与链接映射。对象定义引用版本 {{projectState.ontologyVersion}}，不跟随本体草稿。</p>
<p v-if="returnTo" class="return-to"><button type="button" class="row-link" @click="backToSource">← 返回{{returnTo.label}}</button></p>
<ReferenceNotice v-if="!editorOpen" :project-state="projectState" :object-type="selected" @navigate="emit('navigate',$event)" @open-ontology="emit('open-ontology')"/>
<div class="mapping-workspace" :class="{'mapping-editor-active':editorOpen}">
<aside v-if="!editorOpen" class="card mapping-list"><h3>对象类型 · {{types.length}}</h3><input v-model="search" placeholder="搜索对象类型" aria-label="搜索对象绑定"><button v-for="t in visibleTypes" :key="t['@id']" :class="{active:selected===t['@id'].slice(3)}" @click="pick(t['@id'].slice(3))"><strong>{{t['rdfs:label']}}</strong><small>{{bound(t['@id'].slice(3))?'已启用映射':'尚未启用'}}</small></button><p v-if="!visibleTypes.length">没有匹配的对象类型</p></aside>
<div class="mapping-detail">
<section v-if="!active" class="card"><div class="detail-heading"><div><h2>{{name(selected)}}</h2><small class="head-sub">{{desc(selected)||'暂无业务定义'}}</small></div><span class="tag">未启用</span></div>
<div v-if="selected" class="empty-state"><div class="empty-state-ico">◇</div><p>这个项目需要使用{{name(selected)}}吗？启用后配置实例识别（来源表与主键），属性取值与链接映射再逐步补充。</p><button class="primary" @click="enable(selected)">启用并配置身份来源</button></div></section>
<!-- 未启用数据映射的对象同样可以做动作绑定：动作绑定只依赖本体引用版本中的对象动作关联 -->
<ActionBindings v-if="!active&&selected" :project-state="projectState" :ref-state="refState" :object-type="selected" @before-change="before" @changed="changed"/>
<section v-for="b in active?[active]:[]" :key="b.object_type" class="card">
<template v-if="!editorOpen">
<div class="detail-heading"><div><h2>{{name(b.object_type)}}</h2><small class="head-sub">{{desc(b.object_type)||'暂无业务定义'}}</small></div><div class="tools"><span class="status-pill">项目映射</span><button class="row-link danger" @click="removeObject(b)">删除此绑定</button></div></div>
<div class="mapping-tabs"><button :class="{active:detail==='sources'}" @click="setDetail('sources')">实例识别</button><button :class="{active:detail==='properties'}" @click="setDetail('properties')">属性取值</button><button :class="{active:detail==='links'}" @click="setDetail('links')">链接映射</button><button :class="{active:detail==='actions'}" @click="setDetail('actions')">动作绑定</button></div>
</template>
<ObjectSources v-if="detail==='sources'" :ref="setSourcesRef" :project-state="projectState" :ref-state="refState" :b="b" @before-change="before" @changed="changed" @edit-state="editorOpen=$event" @go-tab="setDetail($event)"/>
<PropertySources v-else-if="detail==='properties'" :ref="setPropertyRef" :project-state="projectState" :ref-state="refState" :b="b" :report="report" @before-change="before" @changed="changed" @edit-state="editorOpen=$event" @go-tab="onGoTabFromEditor" @setup-end="onSetupEnd" @goto-properties="onGotoTargetProperties" @open-ontology="emit('open-ontology')"/>
<LinkMappings v-else-if="detail==='links'" :ref="setLinksRef" :project-state="projectState" :ref-state="refState" :b="b" @before-change="before" @changed="changed" @edit-state="editorOpen=$event" @setup-end="onSetupEnd" @goto-object="onGotoObject"/>
<ActionBindings v-else-if="detail==='actions'" :ref="setActionsRef" :project-state="projectState" :ref-state="refState" :object-type="b.object_type" @before-change="before" @changed="changed" @edit-state="editorOpen=$event"/>
</section></div></div>
</template>
</template>
<style scoped>
/* 段落节奏与表头副行（原为内联 style；head-sub 取 13px，区别于全局 .item-note 的 12px） */
.binding-intro{margin:0 0 16px}.return-to{margin:-8px 0 16px}.head-sub{display:block;color:var(--muted);font-size:13px}
.mapping-workspace.mapping-editor-active{grid-template-columns:minmax(0,1fr)}
</style>
