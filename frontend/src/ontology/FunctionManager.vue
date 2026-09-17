<!-- ─── 计算契约（本体区，T06 原型对齐）─── 列表 → 独立编辑表单（替换主内容）。
     协议冻结（勿改）：props {state:any; focusId?:string}；emits ['before-change','changed','properties']
     （App.vue 绑定 @properties=openProperties @before-change=pushUndo @changed=changed）。
     两态：selected 为空 = 清单（名称、输入→输出摘要、编辑入口）；非空 = 编辑态。
     编辑态（D10 消除 + T00 契约）：打开表单 = 深拷贝本地草稿 draft，打字只改草稿不 touch 自动保存；
     保存经 inject('form-save').submitForm('ontology', mutate) 一次持久化，成功关闭返回列表并定位高亮，
     失败留在表单显示 r.message；取消直接关闭（不落任何工作区数据，新建契约在保存前不进入 functions）。
     离开保护：编辑期间 register form-guard（isDirty 比对打开时快照），关闭/卸载 unregister。
     历史 guide_version 1/2 定义仍为只读展示 + 显式「转为通用契约」，不自动迁移。 -->
<script setup lang="ts">
import {computed,inject,nextTick,onBeforeUnmount,ref,watch} from 'vue'
import Field from '../shared/EditorField.vue'
import AppSelect from '../shared/AppSelect.vue'
import ContractParameter from './ContractParameter.vue'
import {graphReferences} from './editorModel'
import type{FormGuardAPI,FormSaveAPI} from '../app/formGuard'
import {signatureDataType, dataTypeLabel, effectiveProperty} from './propertyModel'
const props=defineProps<{state:any;focusId?:string}>(),emit=defineEmits(['before-change','changed','properties'])
const clone=(x:any)=>JSON.parse(JSON.stringify(x))
// --- T00 表单守卫接入（契约见 app/formGuard.ts）---
const guardApi=inject<FormGuardAPI>('form-guard')!
const formSave=inject<FormSaveAPI>('form-save')!

const functions=computed(()=>props.state.workflow.functions)
const contracts=computed(()=>functions.value.filter((f:any)=>f.guide_version===3))
const legacy=computed(()=>functions.value.filter((f:any)=>f.guide_version!==3))
const graph=computed(()=>props.state.ontology['@graph']),types=computed(()=>graph.value.filter((n:any)=>n['@type']==='owl:Class'))
const shared=computed(()=>graph.value.filter((n:any)=>n['@type']==='mg:SharedProperty'))
const dataProps=computed(()=>graph.value.filter((n:any)=>n['@type']==='owl:DatatypeProperty'))
const applicableLabel=(f:any)=>{const ids=f.applicable_objects?.length?f.applicable_objects:relatedObjects(f);return ids.length?ids.map((t:string)=>types.value.find((x:any)=>x['@id']===t)?.['rdfs:label']||t).join('、'):'通用'}

// --- 清单行：名称 + 输入→输出摘要（真实多输入多输出签名，不截断） ---
const sigNames=(list:any[])=>(list||[]).map((s:any)=>String(s.name||'').trim()||refLabelOf(s.ref)||'待命名').join('、')
const summaryOf=(f:any)=>(f.no_inputs?'无输入':(sigNames(f.inputs)||'待添加'))+' → '+(sigNames(f.outputs)||'待声明')
const query=ref('')
const contractRows=computed(()=>contracts.value.filter((f:any)=>(f.name||'').includes(query.value.trim())).map((f:any)=>({id:f.id,name:f.name||'未命名契约',meta:summaryOf(f)+' · 适用 '+applicableLabel(f)})))
const legacyRows=computed(()=>legacy.value.filter((f:any)=>(f.name||'').includes(query.value.trim())).map((f:any)=>({id:f.id,name:f.name||'未命名函数',note:f.superseded_by?'已转为通用契约，原文存档':(f.description||'保留原文，可转为通用契约')})))

// --- 编辑态状态：draft 本地草稿 + 打开快照；selected 兼作历史定义只读视图的定位 ---
const selected=ref(''),draft=ref<any>(null),isNew=ref(false),formError=ref(''),notice=ref(''),saving=ref(false),attemptedSave=ref(false)
let original=''
const legacyItem=computed(()=>functions.value.find((f:any)=>f.id===selected.value&&f.guide_version!==3)||null)
watch(functions,fs=>{if(selected.value&&!draft.value&&!fs.some((f:any)=>f.id===selected.value))selected.value=''},{immediate:true})
watch(()=>props.focusId,id=>{if(!id)return;query.value='';const f=functions.value.find((x:any)=>x.id===id);if(!f){selected.value='';draft.value=null;return}if(f.guide_version===3)openEditor(f);else{draft.value=null;selected.value=id}},{immediate:true})
function openEditor(f:any){if(!f)return;const d=clone(f);d.inputs??=[];d.outputs??=[];d.applicable_objects??=[];d.conventions??='';d.name??='';d.description??='';selected.value=f.id;draft.value=d;original=JSON.stringify(d);isNew.value=false;formError.value='';notice.value='';attemptedSave.value=false}
function newContract(){const f={id:crypto.randomUUID(),name:'',description:'',status:'experimental',guide_version:3,no_inputs:false,inputs:[],outputs:[{id:'out_'+crypto.randomUUID().slice(0,12),name:'',ref:null,description:''}],conventions:'',applicable_objects:[]};query.value='';selected.value=f.id;draft.value=f;original=JSON.stringify(f);isNew.value=true;formError.value='';notice.value='';attemptedSave.value=false}
function closeEditor(){draft.value=null;original='';isNew.value=false;formError.value='';notice.value='';attemptedSave.value=false;saving.value=false}
// 保存成功后返回列表并定位：清空搜索保证行可见，滚动 + 短暂高亮
const locateId=ref('')
let locateTimer:ReturnType<typeof setTimeout>|undefined
async function locate(id:string){selected.value='';draft.value=null;query.value='';locateId.value=id;await nextTick();document.getElementById('contract-row-'+id)?.scrollIntoView({behavior:'smooth',block:'center'});if(locateTimer)clearTimeout(locateTimer);locateTimer=setTimeout(()=>{locateId.value=''},2500)}

const guard={isDirty:()=>!!draft.value&&JSON.stringify(draft.value)!==original,discard:()=>closeEditor()}
watch(()=>!!draft.value,open=>{open?guardApi.register(guard):guardApi.unregister(guard)},{immediate:true})
onBeforeUnmount(()=>{guardApi.unregister(guard);if(locateTimer)clearTimeout(locateTimer)})

// --- 签名行（紧凑）：名称 + 类型引用（object/property/base 三选）+ 引用目标；可增删多行 ---
function refLabelOf(ref:any){if(!ref||!ref.kind)return '';if(ref.kind==='base')return dataTypeLabel(signatureDataType(ref,graph.value));if(ref.kind==='object'){const t=types.value.find((x:any)=>x['@id']===ref.id);return t?.['rdfs:label']||ref.id||''}const p=[...shared.value,...dataProps.value].find((x:any)=>x['@id']===ref.id);return p?effectiveProperty(p,graph.value)['rdfs:label']||ref.id||'':ref.id||''}
function addInput(){draft.value.no_inputs=false;draft.value.inputs.push({id:'in_'+crypto.randomUUID().slice(0,12),name:'',ref:null,description:''})}
function addOutput(){draft.value.outputs.push({id:'out_'+crypto.randomUUID().slice(0,12),name:'',ref:null,description:''})}
function setNoInputs(v:boolean){draft.value.no_inputs=v;if(v)draft.value.inputs=[]}
function rowError(sig:any){if(!String(sig.name||'').trim())return '请填写这项内容的名称';const r=sig.ref;if(!r||!r.kind)return '请选择需要提供或返回的内容';if(r.kind!=='base'&&!r.id)return '请选择具体内容';if(r.kind==='base'&&!r.dataType)return '请选择具体内容';if(r.kind==='base'&&r.dataType==='timeSeries'&&!r.valueType)return '请选择观测值类型';return ''}
// --- 校验（就地显示）：名称/说明必填、至少一个输出；签名行完整（与后端 signature_errors 对齐） ---
function contractErrors(){const d=draft.value,out:string[]=[]
  if(!String(d.name||'').trim())out.push('请填写名称。')
  if(!String(d.description||'').trim())out.push('请填写业务说明。')
  if(!d.no_inputs&&!(d.inputs||[]).length)out.push('尚未添加输入参数；如确实无需输入，请勾选「无输入」。')
  if(!(d.outputs||[]).length)out.push('至少声明一个输出。')
  ;(d.inputs||[]).forEach((s:any,i:number)=>{const e=rowError(s);if(e)out.push(`输入 ${i+1}：${e}。`)})
  ;(d.outputs||[]).forEach((s:any,i:number)=>{const e=rowError(s);if(e)out.push(`输出 ${i+1}：${e}。`)})
  return out}
async function saveContract(){if(saving.value)return;const errs=contractErrors();if(errs.length){formError.value=errs[0];attemptedSave.value=true;return}
  saving.value=true;const id=draft.value.id;const payload=draft.value
  const r=await formSave.submitForm('ontology',()=>{const list=functions.value;const i=list.findIndex((f:any)=>f.id===id);if(i>=0)list[i]=payload;else list.push(payload)})
  saving.value=false
  if(r.ok)await locate(id);else formError.value=r.message}
async function removeContract(){if(saving.value)return;const id=draft.value.id
  const refs=graphReferences(props.state,id);if(refs.length){formError.value='请先处理引用：'+refs.join('、');return}
  saving.value=true
  const r=await formSave.submitForm('ontology',()=>{const list=functions.value;const i=list.findIndex((f:any)=>f.id===id);if(i>=0)list.splice(i,1)})
  saving.value=false
  if(r.ok){selected.value='';draft.value=null}else formError.value=r.message}
function relatedObjects(f:any):string[]{
  return [...new Set<string>([...(f.inputs||[]),...(f.outputs||[])].flatMap((s:any)=>{
    if(s.ref?.kind==='object')return [s.ref.id]
    if(s.ref?.kind!=='property')return []
    return graph.value.filter((p:any)=>p['@type']==='owl:DatatypeProperty'&&(p['@id']===s.ref.id||p['mg:sharedProperty']?.['@id']===s.ref.id)).map((p:any)=>p['rdfs:domain']?.['@id']).filter(Boolean)
  }))]
}
const suggested=computed(()=>relatedObjects(draft.value||{}))
const usages=computed(()=>props.state.ontology['@graph'].filter((p:any)=>p['@type']==='owl:DatatypeProperty'&&p['mg:valueSource']?.['@value']?.kind==='function'&&p['mg:valueSource']['@value'].functionId===selected.value).map((p:any)=>{const g=props.state.ontology['@graph'],owner=g.find((n:any)=>n['@id']===p['rdfs:domain']?.['@id']),sh=g.find((n:any)=>n['@id']===p['mg:sharedProperty']?.['@id']);return {id:p['@id'],owner:owner?.['@id'],label:(owner?.['rdfs:label']||'对象')+' · '+(sh?.['rdfs:label']||p['rdfs:label']||'属性')}}))
// 历史定义显式转换（用户主动，不自动迁移）：直接落工作区（touch 自动保存），随后进入新契约编辑表单补全
function convertLegacy(){const f=legacyItem.value;if(!f)return;emit('before-change');const c={id:crypto.randomUUID(),name:f.name||'',description:f.description||'',status:f.status||'experimental',guide_version:3,no_inputs:false,inputs:[],outputs:[{id:'out_'+crypto.randomUUID().slice(0,12),name:'',ref:null,description:''}],conventions:[f.logic,f.missing_policy,f.input_description,f.output_description_text].filter(Boolean).join('\n'),applicable_objects:(f.object_types||(f.object_type?[f.object_type]:[])).map((t:string)=>'mg:'+t)};functions.value.push(c);f.superseded_by=c.id;emit('changed');openEditor(c);notice.value='已按原文生成契约草稿：请补全结构化输入输出后，再核对原函数内容。'}
</script>
<template>
<!-- 编辑态：独立契约表单（本地草稿；主内容整体替换，无并列清单） -->
<template v-if="draft">
<div class="edit-top"><button @click="closeEditor">← 返回契约列表</button></div>
<section class="card detail-card">
<div class="detail-heading"><div><h2>{{isNew?'新建计算契约':'维护 · '+(draft.name||'未命名契约')}}</h2></div><span class="status-pill">{{draft.outputs.length||0}} 个输出</span></div>
<p v-if="notice" class="fill-hint">{{notice}}</p>
<p v-if="formError" class="inline-error fm-error" role="alert">{{formError}}</p>
<Field label="这个能力叫什么？" v-model="draft.name" required example="例如：查询储能簇 SOC 采样记录"/>
<Field label="它做什么？" type="textarea" v-model="draft.description" required example="返回指定储能簇在指定时间范围内的 SOC 采样记录。" help="描述业务目的；数据库、Redis 和执行规则由项目实现维护。"/>
<section class="sample-panel">
<div class="panelhead"><h3>使用时需要提供什么？</h3><label class="check-option"><input type="checkbox" :checked="draft.no_inputs" @change="setNoInputs(($event.target as HTMLInputElement).checked)"><span>不需要提供任何内容</span></label></div>
<p class="field-help">例如：一个储能簇、开始时间、结束时间。已有对象或属性直接搜索选择；其他内容选择文本、数值、时间等，再填写名称。</p>
<ContractParameter v-for="(sig,i) in draft.inputs" :key="sig.id" :item="sig" :graph="graph" direction="input" :index="Number(i)" :error="attemptedSave?rowError(sig):''" @remove="draft.inputs.splice(Number(i),1)"/>
<button v-if="!draft.no_inputs" type="button" @click="addInput">＋ 添加需要提供的内容</button>
<p v-if="draft.no_inputs" class="muted">调用时不需要提供参数。</p>
</section>
<section class="sample-panel">
<div class="panelhead"><h3>希望得到什么？</h3></div>
<p class="field-help">例如：储能簇的 SOC 采样序列。选择已有属性后沿用其名称、业务含义和数据类型；也可以返回对象或自行命名的数值、文本等结果。每项声明的结果都需要由项目实现返回。</p>
<ContractParameter v-for="(sig,i) in draft.outputs" :key="sig.id" :item="sig" :graph="graph" direction="output" :index="Number(i)" :error="attemptedSave?rowError(sig):''" @remove="draft.outputs.splice(Number(i),1)"/>
<button type="button" @click="addOutput">＋ 添加希望返回的结果</button>
</section>
<div class="fill-hint"><strong>调用预览</strong><p>{{summaryOf(draft)}}</p><small v-if="suggested.length">关联对象：{{suggested.map(id=>types.find(t=>t['@id']===id)?.['rdfs:label']).join('、')}}，根据所选内容自动识别。</small></div>
<details class="technical-section"><summary>通用业务约定（选填）</summary>
<label>通用业务约定<textarea v-model="draft.conventions" placeholder="说明各项目都应满足的业务口径，例如：无有效数据时返回不可用，不使用 0 冒充有效 SOC。"></textarea></label>
<p class="field-help">口径统一在本体维护；项目实现不得改变输出的业务含义。</p>
</details>
<details class="technical-section"><summary>更多设置（选填）</summary>
<label>生命周期<AppSelect :model-value="draft.status" aria-label="生命周期" :options="[{value:'experimental',label:'草拟／实验中'},{value:'active',label:'使用中'},{value:'deprecated',label:'已弃用'}]" @update:model-value="draft.status=$event"/></label>
<details><summary>调整对象归属（选填）</summary><p class="field-help">留空时根据提供和返回的内容自动归属；需要在其他对象下展示时再指定。</p><div class="choice-grid"><label v-for="t in types" :key="t['@id']" class="check-option"><input type="checkbox" :value="t['@id']" v-model="draft.applicable_objects"><span>{{t['rdfs:label']}}</span></label></div></details>
<fieldset><legend class="field-help">使用此函数的属性 · {{usages.length}}（历史关联）</legend>
<p v-for="u in usages" :key="u.id"><button class="row-link" @click="emit('properties',u.owner,u.id)">{{u.label}} →</button></p>
<p v-if="!usages.length" class="field-help">新契约的属性来源在项目区的「对象映射」页配置；此处仅显示旧版本留下的关联。</p></fieldset>
<div class="detail-footer"><span>删除前需先处理图内引用。</span><button class="danger" :disabled="saving" @click="removeContract">删除契约</button></div>
</details>
<div class="detail-footer">
<span class="muted">保存到当前本体草稿；项目中再配置具体实现。</span>
<div class="tools"><button :disabled="saving" @click="closeEditor">取消</button><button class="primary" :disabled="saving" @click="saveContract">{{saving?'保存中…':'保存'}}</button></div>
</div>
</section>
</template>
<!-- 历史定义：只读展示（guide_version 1/2 永不改写；转换为显式动作） -->
<template v-else-if="legacyItem">
<div class="edit-top"><button @click="selected=''">← 返回契约列表</button></div>
<section class="card detail-card"><div class="detail-heading"><div><span class="eyebrow">历史定义</span><h2>{{legacyItem.name||'未命名函数'}}</h2></div><span class="status-pill">待核对</span></div>
<div class="fill-hint">这是历史定义：原文完整保留，尚未拆分为通用契约与项目实现。转成契约不会删除原文。</div>
<Field label="名称" :model-value="legacyItem.name" disabled/>
<Field label="业务说明" type="textarea" :model-value="legacyItem.description" disabled/>
<Field v-if="legacyItem.logic" label="计算规则（原文）" type="textarea" :model-value="legacyItem.logic" disabled/>
<Field v-if="legacyItem.output_description_text" label="输出（原文）" type="textarea" :model-value="legacyItem.output_description_text" disabled/>
<div v-if="legacyItem.superseded_by" class="inline-success">已转为通用契约（{{functions.find(f=>f.id===legacyItem.superseded_by)?.name||legacyItem.superseded_by}}），原文仅作存档。</div>
<div class="detail-footer"><span>历史定义不参与新流程的发布校验；原文保留，不自动迁移。</span><button class="primary" :disabled="!!legacyItem.superseded_by" @click="convertLegacy">转为通用契约 →</button></div>
</section>
</template>
<!-- 列表态：名称 + 输入→输出摘要 + 编辑入口 -->
<template v-else>
<div class="manager-heading"><div><h2>计算契约</h2><p>说明一个业务能力需要提供什么、希望得到什么。具体实现放在项目映射中。</p></div><button class="primary" @click="newContract">＋ 新建契约</button></div>
<section class="card">
<div class="panelhead list-head"><span class="muted">共 {{contractRows.length}} 项契约</span><input v-model="query" type="search" placeholder="搜索契约名称…" aria-label="搜索契约"></div>
<div v-if="contractRows.length">
<div v-for="r in contractRows" :key="r.id" :id="'contract-row-'+r.id" class="line-row" :class="{'row-located':locateId===r.id}" @click="openEditor(functions.find(f=>f.id===r.id))">
<div class="row-main"><strong>{{r.name}}</strong><small>{{r.meta}}</small></div>
<button class="row-link" @click.stop="openEditor(functions.find(f=>f.id===r.id))">查看与编辑</button>
</div>
</div>
<div v-else class="empty-state">
<span class="empty-state-ico">◇</span>
<p>{{query?'没有匹配的契约，可调整搜索。':'还没有通用契约。点击右上角「新建契约」创建第一个。'}}</p>
</div>
</section>
<section v-if="legacyRows.length" class="card">
<div class="panelhead list-head"><h3>历史定义</h3><span class="muted">旧版函数原文完整保留；转为通用契约不会删除原文。</span></div>
<div v-for="r in legacyRows" :key="r.id" class="line-row" @click="selected=r.id;query=''">
<div class="row-main"><strong>{{r.name}}</strong><small>{{r.note}}</small></div>
<button class="row-link" @click.stop="selected=r.id;query=''">查看原文</button>
</div>
</section>
</template>
</template>
<style scoped>
.edit-top{margin-bottom:14px}
.list-head{margin-bottom:6px;flex-wrap:wrap;gap:10px}
.list-head input[type=search]{width:min(240px,100%);margin:0}
.line-row{display:flex;align-items:center;gap:14px;padding:13px 8px;border-bottom:1px solid var(--line);cursor:pointer;border-radius:7px}
.line-row:hover{background:var(--paper-2)}
.line-row:last-child{border-bottom:0}
.line-row>button{flex:none}
.row-main{flex:1;min-width:0}
.row-main strong{display:block;font-size:14px;font-weight:600}
.row-main small{display:block;margin-top:3px;font-size:12px;color:var(--muted);overflow-wrap:anywhere}
.row-located{outline:2px solid var(--focus);outline-offset:2px}
.fm-error{margin:0 0 14px}
.span2{grid-column:1/-1}
 .sample-panel>.field-help{margin:10px 0 14px}
.fill-hint p{margin:8px 0;font-size:15px}
</style>
