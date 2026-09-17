<!-- ─── 工作概览（本体区 · T07 原型 home() 本体态）───
     结构按原型两卡：①两步工作清单（定义对象、属性与链接 / 校验并发布本体，
     每步带真实计数与继续入口，建议主按钮来自真实缺项推导，不硬编码）；
     ②「定义完成后，交给具体项目」入口（emit navigate 到项目区 p-home）。
     协议：props state — 当前本体草稿（JSON-LD 内存形态，含 workspaceId）；
     emits navigate(view, focus?)。页面数据自取（只读）：GET /api/versions、
     GET /api/projects?ontology=（哪些项目固定引用了此本体）、POST /api/validate
     （草稿校验错误数）。本组件不改内存、不直接保存。 -->
<script setup lang="ts">
import {computed,onMounted,ref} from 'vue'
import { listVersions, validateOntology } from './api'
import { listProjects } from '../project/api'
const props=defineProps<{state:any}>()
const emit=defineEmits<{navigate:[view:string,focus?:any]}>()
const graph=computed<any[]>(()=>props.state?.ontology?.['@graph']||[])
const objects=computed(()=>graph.value.filter(n=>n['@type']==='owl:Class'))
const properties=computed<any[]>(()=>graph.value.filter(n=>n['@type']==='owl:DatatypeProperty'))
const sharedCount=computed(()=>graph.value.filter(n=>n['@type']==='mg:SharedProperty').length)
// 属性完成度：名称或业务定义为空视为未完成（下一步建议的真实依据之一）
const objectsNoDef=computed(()=>objects.value.filter(o=>!String(o['rdfs:label']||'').trim()||!String(o['rdfs:comment']||'').trim()).length)
const propsNoDef=computed(()=>properties.value.filter(p=>!String(p['rdfs:label']||'').trim()||!String(p['rdfs:comment']||'').trim()).length)

const ontologyId=computed(()=>String(props.state?.workspaceId||''))
// 发布版本（真实 listVersions；失败如实展示，不假定已发布）
const latestVersion=ref(''),versionError=ref('')
async function loadVersions(){
  versionError.value='';latestVersion.value=''
  if(!ontologyId.value)return
  try{const items=(await listVersions(ontologyId.value)).items||[];latestVersion.value=items[items.length-1]?.version||''}
  catch(e){versionError.value=(e as Error).message}
}
// 引用此本体的项目（真实项目清单，只读；不在此切换项目——切换由侧栏承担）
const usingProjects=ref<any[]>([]),projectsError=ref('')
async function loadUsingProjects(){
  projectsError.value='';usingProjects.value=[]
  if(!ontologyId.value)return
  try{usingProjects.value=(await listProjects(ontologyId.value)).items||[]}
  catch(e){projectsError.value=(e as Error).message}
}
// 草稿校验错误计数：只读校验，不走保存队列
const draftErrors=ref<string[]>([]),checkBusy=ref(false),checkError=ref('')
async function checkDraft(){
  if(checkBusy.value)return
  checkBusy.value=true;checkError.value=''
  try{draftErrors.value=(await validateOntology({state:props.state})).errors||[]}
  catch(e){checkError.value=(e as Error).message}finally{checkBusy.value=false}
}
onMounted(()=>{void loadVersions();void loadUsingProjects();void checkDraft()})

// 下一步建议：按真实缺项排序推导（对象→属性完成度→发布/校验问题），不硬编码
const steps=computed(()=>{
  const step3Detail=versionError.value?'版本信息读取失败'
    :latestVersion.value?('最新发布版本 '+latestVersion.value+(draftErrors.value.length?' · 草稿有 '+draftErrors.value.length+' 项校验问题':''))
    :'尚未发布；发布后项目才能引用'
  return [
    {view:'objects',no:'1',name:'定义对象、属性与链接',
     detail:`${objects.value.length} 个对象 · ${properties.value.length} 个属性 · 共享库 ${sharedCount.value}`+(propsNoDef.value?` · ${propsNoDef.value} 项缺名称或业务定义`:''),action:objects.value.length?'继续建模':'开始建模'},
    {view:'o-release',no:'2',name:'校验并发布本体',detail:'形成可供多个项目引用的固定版本 · '+step3Detail,action:'查看发布'},
  ]
})
const suggestion=computed(()=>{
  if(!objects.value.length)return{view:'objects',text:'还没有对象：先创建第一个对象类型，补上名称与业务定义。'}
  if(objectsNoDef.value||propsNoDef.value)return{view:'objects',text:`有 ${objectsNoDef.value+propsNoDef.value} 项定义缺少名称或业务定义；进入对象建模补齐。`}
  if(!properties.value.length)return{view:'objects',text:'对象还没有属性；进入对象建模为对象添加属性。'}
  if(draftErrors.value.length)return{view:'o-release',text:`草稿有 ${draftErrors.value.length} 项校验问题；处理后再发布。`}
  if(!latestVersion.value&&!versionError.value)return{view:'o-release',text:'定义已就绪但尚未发布；发布后项目才能固定引用。'}
  return null
})
const usageLine=computed(()=>{
  const head=latestVersion.value||'尚未发布'
  if(projectsError.value)return `版本 ${head} · 引用项目读取失败：${projectsError.value}`
  if(!usingProjects.value.length)return `版本 ${head} · 尚无项目引用；发布后可在项目映射区固定引用`
  return `版本 ${head} → ${usingProjects.value.map(p=>`${p.name} 固定引用 ${p.ontologyVersion}`).join('；')}`
})
</script>
<template>
<section class="card">
  <div class="panelhead"><div><h2>从一个对象开始，把业务讲清楚</h2>
  <p class="muted">先定义对象、属性与链接，再校验发布；属性的具体取值方式在项目映射中配置。</p></div></div>
  <!-- 任务清单式：整行可点击直达对应页面；原按钮动作上移到整行，右侧保留原按钮文案作为引导 -->
  <div class="step-list">
    <button v-for="s in steps" :key="s.view" type="button" class="step-row" :class="{ suggested: suggestion?.view === s.view }" @click="emit('navigate', s.view)">
      <span class="step-no" aria-hidden="true">{{ s.no }}</span>
      <span class="step-main"><strong>{{ s.name }}</strong><small class="muted">{{ s.detail }}</small></span>
      <span class="step-go">{{ s.action }} →</span>
    </button>
  </div>
  <p v-if="suggestion" class="next-step">下一步建议：{{ suggestion.text }}<button class="go-fix" @click="emit('navigate', suggestion.view)">去处理 →</button></p>
  <p v-else class="inline-success">定义与发布均已就绪；可以在下方把本体交给具体项目使用。</p>
</section>
<section class="card">
  <div class="panelhead"><div><h3>定义完成后，交给具体项目</h3>
  <p class="muted">表名、Redis Key 和函数实现，都在项目映射区维护。</p></div>
  <button class="primary" @click="emit('navigate','p-home')">进入项目映射 →</button></div>
  <div class="readonly-line">本本体 {{usageLine}}</div>
</section>
</template>

<style scoped>
.step-list{display:grid}
/* 整行作为任务入口：还原全局 button 外观为安静清单行 */
.step-row{display:flex;align-items:center;gap:14px;padding:14px 8px;border:0;border-bottom:1px solid var(--line);border-radius:var(--r-sm);background:none;width:100%;text-align:left;font:inherit;color:inherit;cursor:pointer}
.step-row:hover{background:var(--paper-2)}
.step-row:hover strong,.step-row:hover .step-go{color:var(--blue)}
.step-row:focus-visible{outline:2px solid var(--focus);outline-offset:-2px}
.step-row:last-child{border-bottom:0}
.step-no{flex:none;width:27px;height:27px;border-radius:50%;background:var(--blue-soft);color:var(--blue);font-size:13px;font-weight:650;display:flex;align-items:center;justify-content:center}
.step-row.suggested .step-no{background:var(--blue);color:var(--paper)}
.step-main{flex:1;min-width:0;display:block}
.step-main strong{display:block;font-size:15px;font-weight:500}
.step-main small{display:block;margin-top:2px;overflow-wrap:anywhere}
.step-go{flex:none;font-size:13px;color:var(--muted);white-space:nowrap}
.next-step{margin:16px 0 0}
.next-step .go-fix{margin-left:10px}
.readonly-line{padding:9px 12px;background:var(--bg);border:1px solid var(--line);border-radius:7px;overflow-wrap:anywhere;font-size:13px;color:var(--muted)}
@media(max-width:640px){.step-row{flex-wrap:wrap}.step-go{flex-basis:100%;text-align:right}}
</style>
