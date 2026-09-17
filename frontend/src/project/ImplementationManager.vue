<!-- ─── 计算实现（项目区，T06 原型对齐）─── 列表 → 独立实现编辑表单（替换主内容）。
     协议冻结（勿改）：props {projectState:any; refState:any; filterType?:string; focusImpl?:string}；
     emits ['before-change','changed']（App.vue 绑定 pushProjectUndo / projectChanged）。
     两态：editingId 为空 = 清单（契约名、输入→输出摘要、实现状态、配置入口）；非空 = 编辑态。
     编辑态（D10 消除 + T00 契约）：上方只读契约签名（输入/输出清单与类型）；主要可编辑区是自然语言
     处理规则（不执行）；每项输入绑定来源（object/field/constant/output 四种绑定能力全保留）；
     输出声明按真实签名展示（沿用 outputDeclarations 逻辑）。打字只改本地草稿 draft，
     保存经 inject('form-save').submitForm('project', mutate) 一次持久化，成功返回列表并定位，
     失败留在表单显示 r.message；取消直接关闭（新实现保存前不进入 projectState.implementations）。
     离开保护：编辑期间 register form-guard；focusImpl（属性映射/校验页跳入）直达编辑态并高亮，
     返回上下文由 App focus 参数承担。 -->
<script setup lang="ts">
import {computed,inject,nextTick,onBeforeUnmount,ref,watch} from 'vue'
import AppSelect from '../shared/AppSelect.vue'
import {localProperties,effectiveProperty,signatureDataType,dataTypeLabel} from '../ontology/propertyModel'
import type{FormGuardAPI,FormSaveAPI} from '../app/formGuard'
const props=defineProps<{projectState:any;refState:any;filterType?:string;focusImpl?:string}>()
const emit=defineEmits(['before-change','changed'])
const clone=(x:any)=>JSON.parse(JSON.stringify(x))
// --- T00 表单守卫接入（契约见 app/formGuard.ts；项目区 area='project'） ---
const guardApi=inject<FormGuardAPI>('form-guard')!
const formSave=inject<FormSaveAPI>('form-save')!

const graph=computed(()=>props.refState?.ontology?.['@graph']||[])
const types=computed(()=>graph.value.filter((n:any)=>n['@type']==='owl:Class'))
const contracts=computed(()=>(props.refState?.workflow?.functions||[]).filter((f:any)=>f.guide_version===3))
const bindings=computed(()=>props.projectState.bindings.object_bindings)
const shownContracts=computed(()=>props.filterType?contractsFor(props.filterType):contracts.value)
const bare=(id:any)=>String(id||'').replace(/^mg:/,'')
const name=(id:string)=>types.value.find((n:any)=>n['@id']==='mg:'+bare(id))?.['rdfs:label']||id
const properties=(t:string)=>localProperties(graph.value,'mg:'+t)
const key=(p:any)=>p['mg:apiName']||p['@id'].slice(3)
const label=(p:any)=>effectiveProperty(p,graph.value)['rdfs:label']||key(p)
const implFor=(contractId:string)=>props.projectState.implementations.find((i:any)=>i.contractId===contractId)
const contractsFor=(t:string)=>contracts.value.filter((c:any)=>!c.applicable_objects?.length||c.applicable_objects.includes('mg:'+t)||c.applicable_objects.includes(t))
const statusOf=(c:any)=>{const impl=implFor(c.id);if(!impl)return '未实现';return impl.rule?.trim()?'已配置':'未配置'}
// 签名摘要与类型标签（只读契约签名用；属性引用解析为生效定义名称）
const typeName=(t:string)=>({string:'文本',double:'数值',integer:'整数',boolean:'是/否',timestamp:'时间',array:'数组',struct:'结构体'}[t]||t||'')
function sigTypeLabel(ref:any){if(!ref||!ref.kind)return '待选类型';if(ref.kind==='base')return '类型 · '+dataTypeLabel(signatureDataType(ref,graph.value))
  if(ref.kind==='object'){const t=graph.value.find((n:any)=>n['@id']===ref.id);return '对象 · '+(t?.['rdfs:label']||ref.id||'')}
  if(ref.kind==='property'){const p=graph.value.find((n:any)=>n['@id']===ref.id);return '属性 · '+(p?String(label(p)):ref.id||'')}
  return ref.kind}
const sigNames=(list:any[])=>(list||[]).map((s:any)=>String(s.name||'').trim()||sigTypeLabel(s.ref)).join('、')
const summaryOf=(c:any)=>(c.no_inputs?'无输入':(sigNames(c.inputs)||'待添加'))+' → '+(sigNames(c.outputs)||'待声明')
const metaOf=(c:any)=>summaryOf(c)+(c.applicable_objects?.length?' · 适用 '+c.applicable_objects.map((t:string)=>name(String(t).replace(/^mg:/,''))).join('、'):' · 通用')

// --- 编辑态状态：draft 本地草稿 + 打开快照；新实现保存前不进入 projectState ---
const editingId=ref(''),draft=ref<any>(null),isNewImpl=ref(false),formError=ref(''),saving=ref(false),attemptedSave=ref(false)
let original=''
const editingContract=computed(()=>contracts.value.find((c:any)=>c.id===editingId.value)||null)
function newImplTemplate(contractId:string){return {id:crypto.randomUUID(),contractId,connection:'',rule:'',parameters:{},environment:'',inputBindings:[],outputDeclarations:[]}}
function openContract(c:any){if(!c)return;editingId.value=c.id;const existing=implFor(c.id);isNewImpl.value=!existing;const d=clone(existing||newImplTemplate(c.id));d.inputBindings??=[];d.outputDeclarations??=[];d.rule??='';d.connection??='';d.environment??='';draft.value=d;original=JSON.stringify(d);formError.value='';attemptedSave.value=false}
function closeEditor(){editingId.value='';draft.value=null;original='';isNewImpl.value=false;formError.value='';attemptedSave.value=false;saving.value=false}
const guard={isDirty:()=>!!draft.value&&JSON.stringify(draft.value)!==original,discard:()=>closeEditor()}
watch(()=>!!draft.value,open=>{open?guardApi.register(guard):guardApi.unregister(guard)},{immediate:true})
onBeforeUnmount(()=>guardApi.unregister(guard))
// 保存成功后返回列表并定位高亮（不丢焦点：列表行滚动到视野内）
const locateId=ref('')
let locateTimer:ReturnType<typeof setTimeout>|undefined
async function locate(id:string){editingId.value='';draft.value=null;locateId.value=id;await nextTick();document.getElementById('impl-row-'+id)?.scrollIntoView({behavior:'smooth',block:'center'});if(locateTimer)clearTimeout(locateTimer);locateTimer=setTimeout(()=>{locateId.value=''},2500)}

const connectionOptions=computed(()=>(props.projectState.connections?.connections||[]).map((c:any)=>({value:c.id,label:c.id})))
// --- 输入绑定（object/field/constant/output 四种来源，能力与旧版一致，仅改为写本地草稿） ---
function bindingOf(inputId:string){return draft.value?.inputBindings.find((b:any)=>b.inputId===inputId)}
function bindingSourceTemplate(kind:string,input:any){if(kind==='object')return {kind:'object',objectType:input.ref?.kind==='object'?input.ref.id:''};if(kind==='field')return {kind:'field',objectType:'',property:''};if(kind==='constant')return {kind:'constant',value:''};return {kind:'output',implementation:'',outputId:''}}
function bindingSourceChanged(input:any,value:string){if(value==='none'){draft.value.inputBindings=draft.value.inputBindings.filter((b:any)=>b.inputId!==input.id);return}
  const source=bindingSourceTemplate(value,input),existing=bindingOf(input.id)
  if(existing)existing.source=source;else draft.value.inputBindings.push({inputId:input.id,source})}
// 选择数据字段来源的对象后，默认预填该对象的主键
function fieldObjectChanged(input:any,objectTypeFull:string){const src=bindingOf(input.id).source;src.objectType=objectTypeFull;const ob=bindings.value.find((x:any)=>x.object_type===bare(objectTypeFull));src.property=ob?.primary_key||''}
// 输入绑定的“数据字段”来源：候选 = 主键 + 显示名称 + 已配置来源的属性，中文名展示
function mappedPropertyOptions(fullId:string){
  const b=bare(fullId),ob=bindings.value.find((x:any)=>x.object_type===b)
  const out:{value:string;label:string}[]=[],seen=new Set<string>()
  if(ob?.primary_key){out.push({value:ob.primary_key,label:'主键 ID（'+ob.primary_key+'）'});seen.add(ob.primary_key)}
  if(ob?.title_key&&!seen.has(ob.title_key)){out.push({value:ob.title_key,label:'显示名称（'+ob.title_key+'）'});seen.add(ob.title_key)}
  for(const k of Object.keys(ob?.properties||{})){
    if(seen.has(k))continue
    const p=properties(b).find((x:any)=>key(x)===k)
    out.push({value:k,label:p?String(label(p)):k});seen.add(k)
  }
  return out
}
// 输出类型由本体契约确定，项目只声明是否返回；旧声明只做兼容读取。
function isDeclared(outputId:string){return draft.value.outputDeclarations.some((d:any)=>d.outputId===outputId)}
function toggleDeclaration(outputId:string){if(isDeclared(outputId))draft.value.outputDeclarations=draft.value.outputDeclarations.filter((d:any)=>d.outputId!==outputId);else draft.value.outputDeclarations.push({outputId,note:''})}
function declaredTypeLabel(output:any){const d=draft.value.outputDeclarations.find((x:any)=>x.outputId===output.id);return dataTypeLabel(signatureDataType(output.ref,graph.value,d))}
// --- 校验（就地显示，与后端 _check_implementations 对齐）：规则必填、契约输入全部绑定、输出已声明 ---
function inputIssue(input:any){const s=bindingOf(input.id)?.source
  if(!s)return '未绑定来源'
  if(s.kind==='object'&&!s.objectType)return '已选「对象」来源，请选择对象类型'
  if(s.kind==='field')return !s.objectType?'已选「数据字段」来源，请选择对象类型':(!s.property?'请选择映射字段':'')
  if(s.kind==='constant'&&!String(s.value||'').trim())return '请填写常量取值'
  if(s.kind==='output')return !s.implementation?'请选择来源实现':(!s.outputId?'请选择来源输出':'')
  return ''}
function outputIssue(output:any){return isDeclared(output.id)?'':'未声明返回'}
function implErrors(){const c=editingContract.value,out:string[]=[]
  if(!String(draft.value.rule||'').trim())out.push('请说明项目的处理规则。')
  for(const input of c.inputs||[])if(inputIssue(input))out.push(`输入「${input.name||input.id}」${inputIssue(input)}。`)
  for(const output of c.outputs||[])if(outputIssue(output))out.push(`输出「${output.name||output.id}」${outputIssue(output)}。`)
  return out}
async function saveImpl(){if(saving.value)return;const errs=implErrors();if(errs.length){formError.value=errs[0];attemptedSave.value=true;return}
  saving.value=true;const payload=draft.value,cid=editingId.value
  const r=await formSave.submitForm('project',()=>{const list=props.projectState.implementations;const i=list.findIndex((x:any)=>x.id===payload.id);if(i>=0)list[i]=payload;else list.push(payload)})
  saving.value=false
  if(r.ok)await locate(cid);else formError.value=r.message}
// 删除实现：同时清理引用该实现的属性来源（computed 计算来源），经 submitForm 立即持久化
async function removeImpl(){if(saving.value)return
  saving.value=true
  const implId=draft.value.id
  const r=await formSave.submitForm('project',()=>{const list=props.projectState.implementations;const impl=list.find((x:any)=>x.id===implId);if(!impl)return;list.splice(list.indexOf(impl),1);for(const b of bindings.value)for(const api of Object.keys(b.properties)){const v=b.properties[api];if(v&&typeof v==='object'&&v.implementation===implId)delete b.properties[api]}})
  saving.value=false
  if(r.ok){editingId.value='';draft.value=null}else formError.value=r.message}
// 校验/跳转定位：focusImpl 为实现 uuid → 直达该契约编辑态，滚动并高亮（.impl-focus 由全局 style.css 约定）
const focusContractId=ref('')
let focusTimer:ReturnType<typeof setTimeout>|undefined
watch(()=>props.focusImpl,id=>{
  if(focusTimer){clearTimeout(focusTimer);focusTimer=undefined}
  focusContractId.value=''
  if(!id)return
  const impl=props.projectState.implementations.find((i:any)=>i.id===id)
  if(!impl)return
  const c=contracts.value.find((x:any)=>x.id===impl.contractId)
  if(!c)return // 引用版本中已无该契约：不进入编辑态，留在列表
  focusContractId.value=impl.contractId
  openContract(c)
  nextTick(()=>{
    document.getElementById('impl-card-'+impl.contractId)?.scrollIntoView({behavior:'smooth',block:'start'})
    focusTimer=setTimeout(()=>{focusContractId.value=''},4000)
  })
},{immediate:true})
onBeforeUnmount(()=>{if(focusTimer)clearTimeout(focusTimer);if(locateTimer)clearTimeout(locateTimer)})
</script>
<template>
<!-- 编辑态：独立实现表单（上方只读契约签名；主内容整体替换） -->
<template v-if="editingContract&&draft">
<div class="edit-top"><button @click="closeEditor">← 返回实现列表</button></div>
<section :id="'impl-card-'+editingContract.id" class="card detail-card" :class="{'impl-focus':focusContractId===editingContract.id}">
<div class="detail-heading"><div><h2>实现 · {{editingContract.name||'未命名契约'}}</h2></div><span class="status-pill">{{draft.rule?.trim()?'已配置规则':'待填写规则'}}</span></div>
<p v-if="formError" class="inline-error im-error" role="alert">{{formError}}</p>
<div class="ro-box">
<strong>{{editingContract.name||'未命名契约'}}</strong>
<p v-if="editingContract.description" class="muted">{{editingContract.description}}</p>
<div class="ro-sig"><span class="ro-key">输入：</span><template v-if="(editingContract.inputs||[]).length"><span v-for="i in editingContract.inputs" :key="i.id" class="ro-item">{{i.name||i.id}}<small>（{{sigTypeLabel(i.ref)}}）</small></span></template><span v-else class="ro-item">无输入</span></div>
<div class="ro-sig"><span class="ro-key">输出：</span><span v-for="o in editingContract.outputs" :key="o.id" class="ro-item">{{o.name||o.id}}<small>（{{sigTypeLabel(o.ref)}}）</small></span></div>
</div>
<label>处理规则 *<textarea v-model="draft.rule" placeholder="例如：读取 Redis，Key 为 m_storage_cluster-{id}-soc；id 使用当前储能簇实例主键，将结果转为数值。"></textarea></label>
<p class="field-help">用自然语言说明来源、步骤和返回结果；不执行。本页维护实现说明与绑定，不执行 Redis 或函数。</p>
<section class="sample-panel">
<div class="panelhead"><h3>输入绑定 · 契约要求 {{(editingContract.inputs||[]).length}} 项</h3></div>
<p class="field-help">来源可为当前对象、数据字段、常量或其他计算的输出；选择适用对象不能替代输入绑定。</p>
<div v-for="input in editingContract.inputs||[]" :key="input.id" class="parameter-card">
<div class="panelhead"><strong>{{input.name||input.id}}</strong><small class="muted">{{sigTypeLabel(input.ref)}}</small></div>
<div class="row">
<label>绑定来源<AppSelect :model-value="bindingOf(input.id)?.source?.kind||'none'" aria-label="输入来源方式" :options="[{value:'none',label:'未绑定'},{value:'object',label:'对象'},{value:'field',label:'数据字段'},{value:'constant',label:'常量'},{value:'output',label:'其他计算输出'}]" @update:model-value="bindingSourceChanged(input,$event)"/></label>
<template v-if="bindingOf(input.id)?.source?.kind==='object'"><label>对象类型<AppSelect :model-value="bindingOf(input.id).source.objectType" aria-label="对象类型" :options="types.map(t=>({value:t['@id'],label:t['rdfs:label']}))" searchable @update:model-value="bindingOf(input.id).source.objectType=$event"/></label></template>
<template v-else-if="bindingOf(input.id)?.source?.kind==='field'"><label>对象类型<AppSelect :model-value="bindingOf(input.id).source.objectType" aria-label="字段来源对象" :options="types.map(t=>({value:'mg:'+t['@id'].slice(3),label:t['rdfs:label']}))" searchable @update:model-value="fieldObjectChanged(input,$event)"/></label><label>映射属性<AppSelect :model-value="bindingOf(input.id).source.property" aria-label="映射属性" :options="mappedPropertyOptions(bindingOf(input.id).source.objectType)" @update:model-value="bindingOf(input.id).source.property=$event"/></label></template>
<template v-else-if="bindingOf(input.id)?.source?.kind==='constant'"><label>常量取值<input :value="bindingOf(input.id).source.value" @input="bindingOf(input.id).source.value=($event.target as HTMLInputElement).value" placeholder="禁止填写密码或密钥"></label></template>
<template v-else-if="bindingOf(input.id)?.source?.kind==='output'"><label>来源实现<AppSelect :model-value="bindingOf(input.id).source.implementation" aria-label="来源实现" :options="[{value:'',label:'请选择'},...projectState.implementations.filter((i:any)=>i.id!==draft.id&&i.kind!=='calculationFunction').map((i:any)=>({value:i.id,label:(contracts.find(x=>x.id===i.contractId)?.name||i.contractId)}))]" @update:model-value="bindingOf(input.id).source.implementation=$event;bindingOf(input.id).source.outputId=''"/><small class="field-help">不引用本实现自身，避免循环。</small></label><label>来源输出<AppSelect :model-value="bindingOf(input.id).source.outputId" aria-label="来源输出" :options="(()=>{const src=bindingOf(input.id).source;const other=projectState.implementations.find(i=>i.id===src.implementation);const oc=contracts.find(x=>x.id===other?.contractId);return (oc?.outputs||[]).map(o=>({value:o.id,label:o.name}))})()" @update:model-value="bindingOf(input.id).source.outputId=$event"/></label></template>
</div>
<p v-if="attemptedSave&&inputIssue(input)" class="inline-error">{{inputIssue(input)}}</p>
</div>
<p v-if="!(editingContract.inputs||[]).length" class="muted">此契约无输入，无需绑定。</p>
</section>
<section class="sample-panel">
<div class="panelhead"><h3>输出声明 · 契约要求 {{(editingContract.outputs||[]).length}} 项</h3></div>
<div v-for="output in editingContract.outputs||[]" :key="output.id"><label class="check-option"><input type="checkbox" :checked="isDeclared(output.id)" @change="toggleDeclaration(output.id)"><span>本实现返回「{{output.name||output.id}}」<small class="muted">（{{sigTypeLabel(output.ref)}}）</small></span></label><small v-if="isDeclared(output.id)" class="field-help" style="margin-left:26px">输出数据类型：{{declaredTypeLabel(output)}}</small><p v-if="attemptedSave&&outputIssue(output)" class="inline-error" style="margin:0 0 8px 26px">{{outputIssue(output)}}</p></div>
<p class="field-help">所有输出均为必需；已声明采集时间的契约必须返回采集时间，不得用查询时间替代。标量输出只能绑定单值属性；时间序列输出须返回时间＋值结构。形态声明用于属性来源校验，本轮不执行函数。</p>
</section>
<details class="technical-section"><summary>更多设置（选填）</summary>
<div class="row"><label>使用的数据连接<AppSelect :model-value="draft.connection||''" aria-label="数据连接" :options="[{value:'',label:'无外部数据需求'},...connectionOptions]" @update:model-value="draft.connection=$event"/></label><label>所属环境（预留）<AppSelect :model-value="draft.environment||''" aria-label="所属环境" :options="[{value:'',label:'默认（单一环境）'},{value:'testing',label:'测试'},{value:'production',label:'生产'}]" @update:model-value="draft.environment=$event"/></label></div>
<div class="detail-footer"><span>删除实现会同时清理引用它的属性计算来源。</span><button class="danger" :disabled="saving" @click="removeImpl">删除此实现</button></div>
</details>
<div class="detail-footer">
<span class="muted">只影响当前项目草稿。</span>
<div class="tools"><button :disabled="saving" @click="closeEditor">取消</button><button class="primary" :disabled="saving" @click="saveImpl">{{saving?'保存中…':'保存到草稿'}}</button></div>
</div>
</section>
</template>
<!-- 列表态：契约名 + 输入→输出摘要 + 实现状态 + 配置入口 -->
<template v-else>
<div class="manager-heading"><div><h2>{{filterType?'适用于 '+name(filterType)+' 的计算契约':'计算实现'}}</h2><p>{{filterType?'引用版本中适用于该对象的通用契约及本项目实现。':'让通用契约在当前项目中实现：参数从哪里取、Redis 或数据库怎么查，在实现内填写。'}}</p></div></div>
<section class="card">
<div v-if="shownContracts.length">
<div v-for="c in shownContracts" :key="c.id" :id="'impl-row-'+c.id" class="line-row" :class="{'row-located':locateId===c.id}" @click="openContract(c)">
<div class="row-main"><strong>{{c.name||'未命名契约'}}</strong><small>{{metaOf(c)}}</small></div>
<span class="status-pill" :class="{'pill-ok':statusOf(c)==='已配置'}">{{statusOf(c)}}</span>
<button class="row-link" @click.stop="openContract(c)">{{implFor(c.id)?'维护实现':'配置实现'}}</button>
</div>
</div>
<div v-else class="empty-state"><div class="empty-state-ico">◇</div><p>引用版本中没有通用契约；历史定义需先在「更多工具 → 计算契约」转为通用契约。</p></div>
</section>
</template>
</template>
<style scoped>
.edit-top{margin-bottom:14px}
.im-error{margin:0 0 14px}
.line-row{display:flex;align-items:center;gap:14px;padding:13px 8px;border-bottom:1px solid var(--line);cursor:pointer;border-radius:7px}
.line-row:hover{background:var(--paper-2)}
.line-row:last-child{border-bottom:0}
.line-row>button,.line-row>.status-pill{flex:none}
.row-main{flex:1;min-width:0}
.row-main strong{display:block;font-size:14px;font-weight:600}
.row-main small{display:block;margin-top:3px;font-size:12px;color:var(--muted);overflow-wrap:anywhere}
.row-located{outline:2px solid var(--focus);outline-offset:2px}
.ro-box{border:1px solid var(--line);border-radius:8px;background:var(--paper-2);padding:14px 16px;margin:0 0 18px}
.ro-box>strong{font-size:15px}
.ro-box .muted{margin:6px 0 0}
.ro-sig{margin:10px 0 0;font-size:13px;color:var(--muted);display:flex;align-items:baseline;gap:6px;flex-wrap:wrap}
.ro-key{flex:none;font-weight:600}
.ro-item{display:inline-block;background:var(--paper);border:1px solid var(--line);border-radius:5px;padding:2px 8px;font-size:12px;color:var(--ink-2)}
.ro-item small{color:var(--muted);margin-left:2px}
</style>
