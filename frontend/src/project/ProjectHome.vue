<script setup lang="ts">
// ─── 项目概览（T07 · 原型 home() 项目态；D08/D09 修正）───
// 只展示当前项目（项目切换由侧栏承担，不再重复项目列表目录）：
//   ①头部卡：项目名 + 引用信息（或绑定入口）+「管理引用版本 / 绑定本体版本」跳独立页 p-upgrade；
//   ②三步清单卡：连接数据源 / 映射业务对象 / 检查并发布（真实计数与继续配置入口）；
//   ③「继续配置」推荐卡（真实缺项推导：第一个仍有属性未配置取值的已启用对象）；
//   ④项目参数卡：摘要 + 维护参数表单（D09：空参数也能进入维护并新增第一项；
//     进入编辑态本地草稿 + 保存/取消，经 inject('form-save') 一次落盘，guard 注册离开保护）。
// emits：created（新建项目后 App 加载并跳对象绑定）、navigate(view, focus?)。
// 引导数据自取（只读）：GET /api/version-state、POST /api/project-validate。
import {computed,inject,onBeforeUnmount,ref,watch} from 'vue'
import AppSelect from '../shared/AppSelect.vue'
import type {FormGuardAPI,FormSaveAPI} from '../app/formGuard'
import {decodeState} from '../ontology/modelFormat'
import {effectiveProperty} from '../ontology/propertyModel'
import { listVersions, versionStateRaw } from '../ontology/api'
import { projectPost, createProject as apiCreateProject } from './api'
const props=defineProps<{defaultOntologyId:string;ontologyOptions:{value:string;label:string}[];projects:any[];projectState:any;projectDirty:boolean;migrationTodos:any[]}>()
// 兼容声明：App 仍绑定 select/reference/upgrade/open-* 等旧事件（侧栏与 p-upgrade 已承接其职责），
// 此处声明以防监听器落到根 DOM；本页实际只发出 created 与 navigate。
const emit=defineEmits(['created','navigate','select','reference','upgrade','open-implementations','open-connections','open-binding','before-change','changed'])
const guardApi=inject<FormGuardAPI>('form-guard')!
const formSave=inject<FormSaveAPI>('form-save')!
const creating=ref(false),newName=ref(''),busy=ref(false),message=ref('')
const newProjectOntology=ref(''),newVersion=ref(''),npVersions=ref<any[]>([])
const changeTypePill={breaking:'破坏性',compatible:'兼容候选',initial:'初始版本',pending:'待确认'}
watch(()=>props.defaultOntologyId,v=>{if(v&&props.ontologyOptions.some(o=>o.value===v)&&!newProjectOntology.value)newProjectOntology.value=v},{immediate:true})
watch(()=>props.ontologyOptions,o=>{if(o.length===1&&!newProjectOntology.value)newProjectOntology.value=o[0].value},{immediate:true})
async function loadNewVersions(id:string){if(!id){npVersions.value=[];return}
  try{const items=(await listVersions(id)).items||[];npVersions.value=items;newVersion.value=items[items.length-1]?.version||''}catch(e){message.value=(e as Error).message}}
watch(newProjectOntology,id=>loadNewVersions(id))
const ontoName=(id:string)=>props.ontologyOptions.find(o=>o.value===id)?.label||id||''
async function createProject(){if(busy.value||!newName.value.trim())return;busy.value=true;try{
  const d=await apiCreateProject({name:newName.value.trim(),ontology:newProjectOntology.value,version:newProjectOntology.value?newVersion.value:''})
  creating.value=false;newName.value='';emit('created',d.id);message.value=''
}catch(e){message.value=(e as Error).message}finally{busy.value=false}}

// ─── 三步清单：真实计数 ───
const connList=computed<any[]>(()=>props.projectState?.connections?.connections||[])
const enabledCount=computed(()=>props.projectState?.bindings?.object_bindings?.length||0)
const hasReference=computed(()=>!!(props.projectState?.ontologyId&&props.projectState?.ontologyVersion))
const refGraph=ref<any[]>([]),guideReady=ref(false)
let guideToken=0
const bare=(id:any)=>String(id||'').replace(/^mg:/,'')
async function loadRefGraph(){
  const st=props.projectState,token=++guideToken
  refGraph.value=[];guideReady.value=false
  if(!st?.ontologyId||!st.ontologyVersion){guideReady.value=true;return}
  try{
    const decoded=decodeState((await versionStateRaw(st.ontologyId,st.ontologyVersion)).state)
    if(token===guideToken){refGraph.value=decoded.ontology?.['@graph']||[];guideReady.value=true}
  }catch{if(token===guideToken)guideReady.value=true}
}
const step3=ref<{done:boolean;pending:number|null;error:string}>({done:false,pending:null,error:''})
async function loadStep3(){
  const st=props.projectState,token=guideToken
  step3.value={done:false,pending:null,error:''}
  if(!st)return
  try{
    const d=await projectPost('project-validate',{state:st}) // catalogs 剥除在 project/api 统一实现
    if(token===guideToken)step3.value={done:true,pending:(d.errors||[]).length,error:''}
  }catch(e){if(token===guideToken)step3.value={done:true,pending:null,error:(e as Error).message}}
}
watch(()=>[props.projectState?.projectId,props.projectState?.ontologyId,props.projectState?.ontologyVersion],()=>{loadRefGraph();loadStep3()},{immediate:true})
const step3Text=computed(()=>{
  if(!step3.value.done)return '检查中…'
  if(step3.value.error)return '待检查（'+step3.value.error+'）'
  if(step3.value.pending===null)return '待检查'
  return step3.value.pending?step3.value.pending+' 项待处理':'无待处理问题（未执行业务数据验证）'
})
// 「继续配置」推荐：第一个已启用但还有属性未配置取值的对象；无缺项或未绑定时隐藏
const typeLabel=(ot:string)=>{const n=refGraph.value.find(g=>g['@type']==='owl:Class'&&bare(g['@id'])===bare(ot));return n?.['rdfs:label']||bare(ot)}
const propApi=(p:any)=>String(p['mg:apiName']||String(p['@id']).replace(/^mg:/,''))
const propLabel=(p:any)=>{const eff=effectiveProperty(p,refGraph.value);return eff?.['rdfs:label']||propApi(p)}
const recommendation=computed(()=>{
  if(!guideReady.value||!hasReference.value)return null
  for(const b of props.projectState?.bindings?.object_bindings||[]){
    const configured=new Set(Object.keys(b.properties||{}))
    const missing=refGraph.value.find(n=>n['@type']==='owl:DatatypeProperty'&&bare(n['rdfs:domain']?.['@id'])===bare(b.object_type)&&!configured.has(propApi(n)))
    if(missing)return{typeId:'mg:'+bare(b.object_type),otLabel:typeLabel(b.object_type),propLabel:propLabel(missing),table:String(b.table||'')}
  }
  return null
})

// ─── 项目参数（D09）：摘要 + 表单式维护（本地草稿 + form-save 一次落盘）───
const parameterRows=computed(()=>Object.entries(props.projectState?.parameters||{}))
const paramsEdit=ref(false),paramRows=ref<{key:string;value:string}[]>([]),paramOriginal=ref(''),paramBusy=ref(false)
const newParamKey=ref(''),newParamValue=ref('')
// 参数删除保护：被属性来源匹配条件（database lookup.match 的 {kind:'parameter'}）引用的参数不能删除
function parameterReferenced(key:string):boolean{
 for(const b of props.projectState?.bindings?.object_bindings||[]){
  for(const v of Object.values(b.properties||{})){
   const src=v as any
   if(!src||typeof src!=='object'||src.kind!=='database')continue
   for(const m of src.lookup?.match||[])if(m?.value&&m.value.kind==='parameter'&&m.value.parameter===key)return true
  }
 }
 return false
}
function openParams(){
  const params=props.projectState?.parameters||{}
  paramRows.value=Object.entries(params).map(([key,v])=>({key,value:v==null?'':String(v)}))
  paramOriginal.value=JSON.stringify(paramRows.value)
  newParamKey.value='';newParamValue.value='';message.value=''
  paramsEdit.value=true
}
function closeParams(){paramsEdit.value=false;message.value=''}
function addParamRow(){paramRows.value.push({key:newParamKey.value.trim(),value:newParamValue.value});newParamKey.value='';newParamValue.value=''}
function removeParamRow(i:number){paramRows.value.splice(i,1)}
// 保留任意已有参数与类型：未编辑的行原样带回（数值/布尔等 YAML 类型不变）；
// 仅当原本是数值/布尔且新文本可解析时才维持原类型，否则按文本保存
function typedValue(key:string,text:string,current:any){
  if(text===String(current??''))return current
  if(typeof current==='number'&&text.trim()!==''&&Number.isFinite(Number(text)))return Number(text)
  if(typeof current==='boolean'&&(text==='true'||text==='false'))return text==='true'
  return text
}
async function saveParams(){
  if(paramBusy.value)return
  const oldParams:any=props.projectState?.parameters||{}
  const rows=paramRows.value
  if(rows.some(r=>!r.key.trim())){message.value='参数名不能为空';return}
  const keys=rows.map(r=>r.key.trim())
  if(new Set(keys).size!==keys.length){message.value='参数名重复，请合并或改名';return}
  for(const k of Object.keys(oldParams))if(!keys.includes(k)&&parameterReferenced(k)){message.value='参数 '+k+' 正被属性来源匹配条件使用，不能在这里删除；请先解除引用';return}
  const next:any={};rows.forEach(r=>{next[r.key.trim()]=typedValue(r.key,r.value,oldParams[r.key.trim()])})
  paramBusy.value=true
  const out=await formSave.submitForm('project',()=>{props.projectState.parameters=next})
  paramBusy.value=false
  if(!out.ok){message.value=out.message||'保存失败，表单内容已保留';return}
  closeParams()
}
const paramsGuard={isDirty:()=>paramsEdit.value&&JSON.stringify(paramRows.value)!==paramOriginal.value,discard:()=>{closeParams()}}
watch(paramsEdit,open=>open?guardApi.register(paramsGuard):guardApi.unregister(paramsGuard),{immediate:false})
onBeforeUnmount(()=>guardApi.unregister(paramsGuard))
</script>
<template><div class="project-home">
<template v-if="!projectState">
<section class="card">
  <div v-if="!creating" class="empty-state">
    <div class="empty-state-ico">◇</div>
    <p>还没有项目。</p>
    <button class="primary" @click="creating=true">新建项目</button>
  </div>
  <template v-else>
  <div class="panelhead"><div><h2>新建项目</h2><p class="muted">创建空白项目；可以先配置数据连接，之后再绑定已发布本体版本。</p></div></div>
  <form class="sample-panel" @submit.prevent="createProject"><div class="form-grid">
    <label>项目名称 *<input v-model="newName" required maxlength="80" placeholder="例如：创智园二期"></label>
    <label>引用本体（可选，创建后可补选）<AppSelect v-model="newProjectOntology" aria-label="引用本体" :options="[{value:'',label:'暂不绑定，创建后选择'},...ontologyOptions]"/></label>
  </div>
  <div class="form-grid" v-if="newProjectOntology"><label>引用版本 *<AppSelect v-model="newVersion" aria-label="引用版本" :options="npVersions.map(v=>({value:v.version,label:v.version+' · '+(changeTypePill[v.changeType]||v.changeType)}))"/></label></div>
  <p class="muted">项目标识自动生成；不复制本体定义，升级由项目主动发起。</p>
  <div class="tools"><button type="submit" class="primary" :disabled="busy||!newName.trim()">{{busy?'创建中…':'创建项目'}}</button><button type="button" @click="creating=false">取消</button></div></form>
  </template>
  <p class="muted">已有项目请在左侧「当前项目」下拉中切换。</p>
</section>
</template>
<template v-else>
<section class="card">
  <div class="panelhead"><div><h2>{{projectState.name}}</h2>
  <p class="muted">{{hasReference?('引用 '+ontoName(projectState.ontologyId)+' '+projectState.ontologyVersion+'；升级仅修改项目草稿'):'尚未绑定本体：可以先配置连接，对象映射与校验需要先绑定已发布版本'}}</p></div>
  <div class="tools"><span v-if="projectDirty" class="status-pill">未保存修改</span>
  <button class="primary" @click="emit('navigate','p-upgrade')">{{hasReference?'管理引用版本':'绑定本体版本'}}</button>
  <button :disabled="busy" @click="creating=!creating">＋ 新建项目</button></div></div>
  <form v-if="creating" class="sample-panel" @submit.prevent="createProject"><div class="form-grid">
    <label>项目名称 *<input v-model="newName" required maxlength="80" placeholder="例如：园区三期"></label>
    <label>引用本体（可选）<AppSelect v-model="newProjectOntology" aria-label="引用本体" :options="[{value:'',label:'暂不绑定，创建后选择'},...ontologyOptions]"/></label>
  </div>
  <div class="form-grid" v-if="newProjectOntology"><label>引用版本 *<AppSelect v-model="newVersion" aria-label="引用版本" :options="npVersions.map(v=>({value:v.version,label:v.version+' · '+(changeTypePill[v.changeType]||v.changeType)}))"/></label></div>
  <div class="tools"><button type="submit" class="primary" :disabled="busy||!newName.trim()">{{busy?'创建中…':'创建项目'}}</button><button type="button" @click="creating=false">取消</button></div></form>
</section>

<section class="card">
  <div class="step-list">
    <div class="guide-step"><span class="step-no">1</span><div class="step-main"><button class="step-title" @click="emit('navigate','connections')">连接数据源</button>
    <small class="muted">{{connList.length?connList.length+' 个连接 · '+(connList.map((c:any)=>c.name||c.id).join('、')):'还没有数据连接；添加后即可供对象映射复用'}}</small>
    <details v-if="connList.length" class="step-more"><summary>连接明细</summary><div class="sample-panel"><p v-for="c in connList" :key="c.id"><strong>{{c.name||c.id}}</strong> · {{c.engine==='mysql'?'MySQL 连接':c.engine==='redis'?'Redis 连接':'演示数据文件'}} <span class="muted">{{c.engine?(c.host+':'+c.port)+(c.engine==='mysql'?' · '+c.database:' · DB '+(c.dbIndex||0)):c.path}}</span> <span v-if="c.credentialRef" class="muted">凭据已受保护</span></p><p class="muted">密码等凭据存放在本机受保护存储，不写入项目文件、快照或日志。</p></div></details></div></div>
    <div class="guide-step"><span class="step-no">2</span><div class="step-main"><button class="step-title" @click="emit('navigate','binding')">映射业务对象</button><small class="muted">{{hasReference?(enabledCount?('当前启用 '+enabledCount+' 个对象'):'尚未启用对象映射'):'绑定本体后可选择对象'}}</small></div></div>
    <div class="guide-step"><span class="step-no">3</span><div class="step-main"><button class="step-title" @click="emit('navigate','p-release')">检查并发布项目配置</button><small class="muted">{{step3Text}}</small></div></div>
    <div class="guide-step step-extra"><span class="muted">属性与链接的派生取值在计算实现中配置。</span><button class="row-link" @click="emit('navigate','implements')">全部计算实现 →</button></div>
  </div>
</section>

<section v-if="recommendation" class="card recommend-card">
  <div class="panelhead"><div><h3>继续配置：{{recommendation.otLabel}}</h3><p class="muted">{{recommendation.table?('从表 '+recommendation.table+' 映射 '+recommendation.propLabel+' 的取值。'):('为 '+recommendation.otLabel+' 补充属性 '+recommendation.propLabel+' 的取值来源。')}}</p></div>
  <button class="primary" @click="emit('navigate','binding',{type:recommendation.typeId})">去配置 →</button></div>
</section>

<section class="card">
  <div class="panelhead"><div><h3>项目参数</h3><p class="muted">{{parameterRows.length?('共 '+parameterRows.length+' 项 · parameters.yaml，供映射条件以参数引用'):'还没有参数；也可以先不配置'}}</p></div>
  <div class="tools"><button @click="paramsEdit?closeParams():openParams()">{{paramsEdit?'取消维护':'维护参数'}}</button></div></div>
  <template v-if="paramsEdit">
    <p class="muted">在下方直接编辑参数取值；保存一次生效，取消放弃本次修改。空参数也可以在这里新增第一项。</p>
    <div class="scroll"><table><thead><tr><th style="width:34%">参数名</th><th>取值</th><th style="width:90px"></th></tr></thead>
    <tbody><tr v-for="(row,i) in paramRows" :key="i"><td><input v-model="row.key" maxlength="120" placeholder="例如 soc_max_age_seconds" aria-label="参数名"></td>
    <td><input v-model="row.value" placeholder="例如 300" aria-label="参数取值"></td>
    <td><button :disabled="parameterReferenced(row.key.trim())" :title="parameterReferenced(row.key.trim())?'该参数正被属性来源匹配条件引用，不能删除':''" @click="removeParamRow(i)">移除</button></td></tr></tbody></table></div>
    <div class="row"><label>新增参数名<input v-model="newParamKey" placeholder="例如 soc_max_age_seconds"></label><label>取值<input v-model="newParamValue" placeholder="例如 300"></label><button type="button" style="flex:0 0 auto" :disabled="!newParamKey.trim()" @click="addParamRow">添加一行</button></div>
    <p class="field-help">已有参数的原有类型（数值、布尔等）在未修改时保持不变；删除被来源匹配条件引用的参数会被阻止。</p>
    <div class="tools"><button class="primary" :disabled="paramBusy" @click="saveParams">{{paramBusy?'保存中…':'保存参数'}}</button><button :disabled="paramBusy" @click="closeParams">取消</button></div>
  </template>
  <template v-else>
    <div v-if="parameterRows.length" class="param-grid"><div v-for="row in parameterRows.slice(0,6)" :key="row[0]" class="param-item"><strong>{{row[0]}}</strong><span class="muted">{{row[1]===''||row[1]==null?'（空值）':String(row[1])}}</span></div></div>
    <p v-if="!parameterRows.length" class="muted">暂无项目参数。</p>
    <p v-else-if="parameterRows.length>6" class="muted">其余 {{parameterRows.length-6}} 项进入维护页查看。</p>
  </template>
</section>

<section v-if="migrationTodos.length" class="card"><h3>迁移待核对项 · {{migrationTodos.length}}</h3>
<p class="muted">以下属性在旧版本中配置了“函数取值”，已从本体层迁出；请在属性来源中用新流程重新配置后核对。</p>
<div v-for="t in migrationTodos" :key="t.propertyId" class="sample-panel"><p><strong>{{t.propertyId}}</strong>（{{t.ownerType}}）→ 函数 {{t.functionId}}</p><p class="muted">输入说明：{{t.arguments||'（未填写）'}}</p></div></section>
</template>
<p v-if="message" class="inline-error" role="status">{{message}}</p>
</div></template>

<style scoped>
.guide-step{display:flex;align-items:flex-start;gap:14px;padding:14px 0;border-bottom:1px solid var(--line)}
.guide-step:last-child{border-bottom:0;padding-bottom:2px}
.step-no{flex:none;width:26px;height:26px;border-radius:50%;background:var(--blue-soft);color:var(--blue);font-size:13px;font-weight:650;display:flex;align-items:center;justify-content:center;margin-top:2px}
.step-main{flex:1;min-width:0}
.step-title{display:inline-block;border:0;background:none;padding:0;font:inherit;font-size:15px;font-weight:500;color:var(--ink);text-align:left;cursor:pointer}
.step-title::after{content:' →';font-size:13px;color:var(--faint)}
.step-title:hover{color:var(--blue)}
.step-title:hover::after{color:var(--blue)}
.step-main small{display:block;margin-top:2px}
.step-more{margin-top:6px}
.step-more summary{cursor:pointer;font-size:12px;color:var(--muted)}
.step-extra{align-items:center;padding-top:12px}
.step-extra .row-link{flex:none}
.recommend-card .panelhead{margin-bottom:0}
.param-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px 22px;margin:4px 0 8px}
.param-item{border:1px solid var(--line);border-radius:8px;padding:10px 14px;background:var(--paper-2)}
.param-item strong{display:block;font-size:13px;overflow-wrap:anywhere}
.project-home .row{align-items:end}
@media(max-width:760px){.param-grid{grid-template-columns:1fr}}
</style>
