<script setup lang="ts">
import {computed,inject,nextTick,onBeforeUnmount,ref,watch} from 'vue'
import AppSelect from '../shared/AppSelect.vue'
import {toSqlTemplate} from './sqlTemplates'
import {convertToSqlSteps,sqlStepsRuleErrors,stepOutputCandidates,newStepKey,blankSqlStepsRule} from './sqlSteps'
import {blankCalcFunction,newCalcInput,toDisplayFormula,toCanonicalFormula,calcFunctionErrors,isCalcFunction} from './calcFunction'
import {calcEval} from './api'
import QueryRuleImplementation from './QueryRuleImplementation.vue'
import ImplementationManager from './ImplementationManager.vue'
import {isQueryRule,queryRuleErrors,scadaFields,reusableScadaRule,isReusableRule,migrateScadaRule,editableRule} from './queryRules'
import {localProperties} from '../ontology/propertyModel'
import type {FormGuardAPI,FormSaveAPI} from '../app/formGuard'
const props=defineProps<{projectState:any;refState:any;focusImpl?:string}>()
const emit=defineEmits(['before-change','changed'])
const formSave=inject<FormSaveAPI>('form-save')!,guardApi=inject<FormGuardAPI>('form-guard')!
const draft=ref<any>(null),error=ref(''),saving=ref(false),saved=ref(''),legacy=ref(false)
const legacyReason=ref('')   // V4 转换失败原因：旧模板回退编辑时展示
let baseline=''
const migrationInputs=ref<Record<string,string>|null>(null)
const graph=computed(()=>props.refState?.ontology?.['@graph']||[])
const objects=computed(()=>graph.value.filter((n:any)=>n['@type']==='owl:Class').map((n:any)=>({value:n['@id'].replace(/^mg:/,''),label:n['rdfs:label']})))
const rules=computed(()=>(props.projectState.implementations||[]).filter((i:any)=>isQueryRule(i)||isCalcFunction(i)))
// 规则库固定展示一个采样规则入口；复用现有记录，不按属性重复创建。
const samplingRule=computed(()=>rules.value.find((r:any)=>isReusableRule(r)&&(scadaFields(r)||r.extensions?.samplingTemplate))||rules.value.find((r:any)=>scadaFields(r)))
function openSampling(){open(samplingRule.value)}
// 列表默认只显示规则名与引用；详情按需展开一条（QueryRuleImplementation 只读展示）。
const detailFor=ref<string|null>(null)
function toggleDetail(id:string){detailFor.value=detailFor.value===id?null:id}
const connections=computed(()=>(props.projectState.connections?.connections||[]).filter((c:any)=>c.engine==='mysql').map((c:any)=>({value:c.id,label:c.name||c.id})))
// 打开规则：优先无损转换为 V4 卡片；失败保留原文进入旧模板回退编辑（A12）。
function open(rule?:any){
  detailFor.value=null
  if(rule&&isCalcFunction(rule))return openCalc(rule)
  const migrated=migrateScadaRule(rule||reusableScadaRule())
  const converted=convertToSqlSteps(migrated.rule)
  if(converted.rule){
    draft.value=converted.rule
    if(scadaFields(migrated.rule))draft.value.extensions={...draft.value.extensions,samplingTemplate:true}
    migrationInputs.value=migrated.inputs
    legacy.value=false;legacyReason.value=''
  }else{
    const d=editableRule(migrated.rule)
    d.sqlTemplate=typeof migrated.rule.sqlTemplate==='string'?migrated.rule.sqlTemplate:toSqlTemplate(d)
    d.mode='sqlTemplate'
    draft.value=d;legacy.value=true;legacyReason.value=converted.reason
    migrationInputs.value=null
  }
  baseline=JSON.stringify(draft.value);error.value='';saved.value=''
}
function close(){draft.value=null;error.value='';insertFor.value=null}
const guard={isDirty:()=>!!draft.value&&(JSON.stringify(draft.value)!==baseline),discard:close}
watch(()=>!!draft.value,v=>v?guardApi.register(guard):guardApi.unregister(guard));onBeforeUnmount(()=>guardApi.unregister(guard))
watch(()=>props.focusImpl,id=>{const r=rules.value.find((r:any)=>r.id===id);if(r){legacy.value=false;open(r)}else if(id)legacy.value=true},{immediate:true})
const types=[{value:'string',label:'文本'},{value:'double',label:'数值'},{value:'boolean',label:'是/否'},{value:'dateTime',label:'日期时间'}]
const cardinalities=[{value:'one',label:'一条（唯一记录）'},{value:'many',label:'多条（记录集合）'}]
function newRule(){draft.value=blankSqlStepsRule();legacy.value=false;legacyReason.value='';migrationInputs.value=null;detailFor.value=null;baseline=JSON.stringify(draft.value);error.value='';saved.value=''}
// ---------- 计算函数：编辑器状态与保存/试算 ----------
const displayFormula=ref(''),trialValues=ref<Record<string,string>>({}),trialResult=ref<{ok:boolean;text:string}|null>(null),trialBusy=ref(false)
const formulaRef=ref<HTMLTextAreaElement|null>(null)
const calcTypes=[{value:'number',label:'数值'},{value:'string',label:'文本'},{value:'boolean',label:'是/否'}]
const calcTypeNames:Record<string,string>={number:'数值',string:'文本',boolean:'是/否'}
const calcErrs=computed(()=>draft.value&&draft.value.kind==='calculationFunction'?calcFunctionErrors(draft.value,displayFormula.value):[])
function openCalc(fn:any){
  draft.value=JSON.parse(JSON.stringify(fn));legacy.value=false;legacyReason.value='';migrationInputs.value=null
  displayFormula.value=toDisplayFormula(fn.implementation?.expression||'',fn.inputs||[])
  trialValues.value={};trialResult.value=null
  baseline=JSON.stringify(draft.value);error.value='';saved.value=''
}
function newCalc(){openCalc(blankCalcFunction())}
function insertParam(name:string){
  const ta=formulaRef.value,text='{'+name+'}'
  const start=ta?.selectionStart??displayFormula.value.length,end=ta?.selectionEnd??start
  displayFormula.value=displayFormula.value.slice(0,start)+text+displayFormula.value.slice(end)
  nextTick(()=>{ta?.focus();ta?.setSelectionRange(start+text.length,start+text.length)})
}
function calcCanonical():{expr:string;errors:string[]}{
  const errors=calcFunctionErrors(draft.value,displayFormula.value)
  const canonical=toCanonicalFormula(displayFormula.value,draft.value.inputs||[])
  for(const u of canonical.unknown)errors.push(`公式引用的参数「${u}」不存在或已被删除`)
  return {expr:canonical.expr,errors}
}
async function saveCalc(){
  if(saving.value)return
  const {expr,errors}=calcCanonical()
  if(errors.length){error.value=errors.join('；');return}
  saving.value=true;error.value=''
  try{
    const r=await calcEval({expression:expr,inputs:(draft.value.inputs||[]).map((p:any)=>({id:p.id,type:p.type})),outputType:draft.value.output.type,validateOnly:true})
    if(!r.ok){error.value=r.error||'公式校验未通过';saving.value=false;return}
    const payload=JSON.parse(JSON.stringify(draft.value))
    payload.implementation.expression=expr
    const r2=await formSave.submitForm('project',()=>{const list=props.projectState.implementations;const i=list.findIndex((x:any)=>x.id===payload.id);if(i<0)list.push(payload);else list[i]=payload})
    if(r2.ok){close();saved.value='计算函数已保存；请在属性取值中选择该函数并绑定输入。'}else error.value=r2.message
  }catch(e:any){error.value=e.message}finally{saving.value=false}
}
async function runTrial(){
  if(trialBusy.value)return
  const canonical=toCanonicalFormula(displayFormula.value,draft.value.inputs||[])
  if(canonical.unknown.length){trialResult.value={ok:false,text:`公式引用的参数「${canonical.unknown.join('、')}」不存在`};return}
  trialBusy.value=true;trialResult.value=null
  try{
    const r=await calcEval({expression:canonical.expr,inputs:(draft.value.inputs||[]).map((p:any)=>{
      const raw:any=trialValues.value[p.id]
      let value:any=null
      if(typeof raw==='string'?raw.trim()!=='':raw!==undefined&&raw!==null){
        if(p.type==='number')value=Number(String(raw).trim())
        else if(p.type==='boolean')value=raw==='true'||raw===true
        else value=raw
      }
      return {id:p.id,type:p.type,value}
    }),outputType:draft.value.output.type})
    trialResult.value=r.ok?{ok:true,text:'试算结果：'+r.value}:{ok:false,text:r.error||'试算失败'}
  }catch(e:any){trialResult.value={ok:false,text:e.message}}finally{trialBusy.value=false}
}
function usage(id:string){return (props.projectState.bindings?.object_bindings||[]).flatMap((b:any)=>Object.entries(b.properties||{}).filter(([k,v]:any)=>v?.kind==='computed'&&v.implementation===id).map(([k])=>(objects.value.find((o:any)=>o.value===b.object_type)?.label||b.object_type)+' · '+(localProperties(graph.value,'mg:'+b.object_type).find((p:any)=>(p['mg:apiName']||p['@id'].slice(3))===k)?.['rdfs:label']||k)))}
// 引用摘要有界展示：引用数随项目规模增长不可控，列表行不展示；详情/编辑警告最多列 3 处、超出计总数。
function usageSummary(id:string){const u=usage(id);return u.length?u.slice(0,3).join('、')+(u.length>3?` 等 ${u.length} 处`:''):''}

// ---------- V4 卡片：步骤操作与插入引用（光标定位，A3/A4） ----------
const sqlRefs:{[k:number]:HTMLTextAreaElement|null}={}
function setSqlRef(i:number,el:any){if(el)sqlRefs[i]=el as HTMLTextAreaElement;else delete sqlRefs[i]}
function addStep(){const steps=draft.value.steps;steps.push({id:crypto.randomUUID(),key:newStepKey(steps),name:'',cardinality:'one',sql:''})}
function moveStep(i:number,dir:-1|1){const steps=draft.value.steps;const j=i+dir;if(j<0||j>=steps.length)return;const [s]=steps.splice(i,1);steps.splice(j,0,s)}
function removeStep(i:number){draft.value.steps.splice(i,1)}
// 插入引用面板：来源=输入参数或前置 one 步骤；步骤源再选列与方式。
const insertFor=ref<number|null>(null),insertSource=ref(''),insertColumn=ref(''),insertKind=ref('value')
function openInsert(i:number){insertFor.value=insertFor.value===i?null:i;insertSource.value='';insertColumn.value='';insertKind.value='value'}
function insertOptions(i:number){
  const out:(any)[]=[]
  for(const p of draft.value?.inputs||[])out.push({value:'input:'+p.name,label:'输入参数 '+p.name+(p.description?' · '+p.description:'')})
  ;(draft.value?.steps||[]).slice(0,i).forEach((s:any,j:number)=>{if(s.cardinality==='one')out.push({value:'step:'+s.key,label:`第 ${j+1} 步 · ${s.name||s.key}（${s.key}）的输出列`})})
  return out
}
const insertColumns=computed(()=>{if(!insertSource.value.startsWith('step:'))return []
  const key=insertSource.value.slice(5);const s=(draft.value?.steps||[]).find((x:any)=>x.key===key)
  return s?stepOutputCandidates(s.sql||''):[]})
const insertIsStep=computed(()=>insertSource.value.startsWith('step:'))
function insertText():string{
  if(!insertSource.value)return ''
  if(!insertIsStep.value)return ':'+insertSource.value.slice(6)
  const key=insertSource.value.slice(5),col=insertColumn.value.trim()
  if(!key||!col)return ''
  return insertKind.value==='value'?`:${key}.${col}`:`{{${key}.${col}}}`
}
function doInsert(){
  const i=insertFor.value;if(i==null||!draft.value?.steps?.[i])return
  const text=insertText();if(!text){error.value='请选择引用来源'+(insertIsStep.value?'与输出列':'');return}
  error.value=''
  const s=draft.value.steps[i],ta=sqlRefs[i]
  if(ta){
    const start=ta.selectionStart??ta.value.length,end=ta.selectionEnd??start
    s.sql=(s.sql||'').slice(0,start)+text+(s.sql||'').slice(end)
    nextTick(()=>{ta.focus();const pos=start+text.length;ta.setSelectionRange(pos,pos)})
  }else s.sql=(s.sql||'')+text
  insertFor.value=null
}
// 本步骤校验错误（按「步骤「名」：」前缀归属到卡片），其余为全局错误。
const stepErrs=computed(()=>{const map:{[k:number]:string[]}={};if(!draft.value||legacy.value)return map
  const all=sqlStepsRuleErrors(draft.value)
  ;(draft.value.steps||[]).forEach((s:any,i:number)=>{map[i]=all.filter((e:string)=>e.startsWith(`步骤「${s.name}」：`)||e.startsWith(`步骤「${s.key}」：`))})
  return map})
const globalErrs=computed(()=>{if(!draft.value||legacy.value)return queryRuleErrorsSilently()
  const all=sqlStepsRuleErrors(draft.value)
  const prefixes=(draft.value.steps||[]).flatMap((s:any)=>[`步骤「${s.name}」：`,`步骤「${s.key}」：`])
  return all.filter((e:string)=>!prefixes.some(p=>e.startsWith(p)))})
function queryRuleErrorsSilently():string[]{return []}
const outStep=computed(()=>(draft.value?.steps||[]).find((s:any)=>s.id===draft.value?.result?.step))
const outCandidates=computed(()=>outStep.value?stepOutputCandidates(outStep.value.sql||''):[])
const outStepOptions=computed(()=>(draft.value?.steps||[]).map((s:any,i:number)=>({value:s.id,label:`第 ${i+1} 步 · ${s.name||s.key}（${s.key} · ${s.cardinality==='one'?'一条':'多条'}）`})))
const outNotLast=computed(()=>{const steps=draft.value?.steps||[];return steps.length>1&&outStep.value&&steps[steps.length-1].id!==outStep.value.id})

async function save(){if(saving.value)return
  const issues=legacy.value?queryRuleErrors(draft.value):sqlStepsRuleErrors(draft.value)
  if(!connections.value.some((c:any)=>c.value===draft.value.connection))issues.push('请选择当前项目已有的数据库连接')
  if(issues.length){error.value=issues.join('；');return}
  saving.value=true;error.value=''
  try{
  const payload=JSON.parse(JSON.stringify(draft.value))
  if(legacy.value){if(payload.steps){payload.extensions={...payload.extensions,structuredRuleBeforeSql:{steps:payload.steps,result:{...payload.result}}};delete payload.steps}delete payload.result.step}
  const r=await formSave.submitForm('project',()=>{const list=props.projectState.implementations;const i=list.findIndex((x:any)=>x.id===payload.id);if(i<0)list.push(payload);else list[i]=payload
  if(migrationInputs.value)for(const b of props.projectState.bindings?.object_bindings||[])for(const value of Object.values(b.properties||{}) as any[])if(value?.kind==='computed'&&value.implementation===payload.id&&value.inputs===undefined)value.inputs={...migrationInputs.value}
  })
  if(r.ok){close();saved.value='取值规则已保存；请在属性取值中选择规则并填写入参。'}else error.value=r.message
  }catch(e:any){error.value=e.message}finally{saving.value=false}}
</script>
<template>
<template v-if="draft">
<div class="tools"><button :disabled="saving" @click="close">← 返回取值规则库</button></div>

<section v-if="draft.kind==='calculationFunction'" class="card rule-editor"><div class="panelhead"><h2>{{draft.name||'新建计算函数'}}</h2><button class="primary" :disabled="saving" @click="saveCalc">{{saving?'保存中…':'保存计算函数'}}</button></div>
<label>函数名称 *<input aria-label="函数名称" v-model="draft.name" placeholder="例如：计算 SOC"></label>
<p class="fill-hint">计算函数是项目级纯公式定义：不连数据库、不取真实属性数据；在属性取值中绑定输入后由取值链路使用。试算只是公式计算，不代表属性数据链路已经运行。</p>
<label>业务说明<input aria-label="业务说明" v-model="draft.description" placeholder="选填：业务口径与单位约定，例如『剩余电量与额定容量使用相同能量单位』"></label>
<h3>输入参数 *</h3>
<div v-for="(p,i) in draft.inputs" :key="p.id" class="input-row">
<label>参数名称<input v-model="p.name" :aria-label="'参数名称 '+(Number(i)+1)" placeholder="例如：剩余电量"></label>
<label>数据类型<AppSelect :model-value="p.type" :aria-label="'参数类型 '+(Number(i)+1)" :options="calcTypes" @update:model-value="p.type=$event"/></label>
<button :disabled="!p.name" @click="insertParam(p.name)">插入参数</button>
<button @click="draft.inputs.splice(i,1)">删除参数</button>
</div>
<button @click="draft.inputs.push(newCalcInput(draft.inputs.length))">＋ 添加输入参数</button>
<p class="field-help">公式用 {参数名} 引用输入（推荐点「插入参数」在光标处插入）。参数名仅用于显示，内部保存稳定标识：改名不影响公式；删除仍被公式引用的参数会被阻止保存。</p>
<h3>公式 *</h3>
<textarea ref="formulaRef" v-model="displayFormula" class="sql-editor" aria-label="计算公式" spellcheck="false" rows="4" placeholder="剩余电量 / 额定容量 * 100"></textarea>
<p class="field-help">支持 + − * /、括号、比较 = != &lt; &lt;= &gt; &gt;=、IF(条件, 是, 否)（惰性）、MIN/MAX、ABS、ROUND、ERROR('说明')；文本不自动转数值；不自动补零或截断结果。</p>
<h3>输出 *</h3>
<div class="row">
<label>输出名称 *<input aria-label="输出名称" v-model="draft.output.name" placeholder="例如：SOC"></label>
<label>输出数据类型 *<AppSelect aria-label="输出数据类型" v-model="draft.output.type" :options="calcTypes"/></label>
</div>
<h3>示例试算（选填）</h3>
<div v-for="p in draft.inputs" :key="'t-'+p.id" class="input-row">
<label>{{p.name||p.id}}（{{calcTypeNames[p.type]}}）<input v-model="trialValues[p.id]" :aria-label="'试算值 '+(p.name||p.id)" :placeholder="p.type==='number'?'例如 40':(p.type==='boolean'?'true / false':'例如 文本')"></label>
</div>
<button :disabled="trialBusy" @click="runTrial">{{trialBusy?'试算中…':'试算'}}</button>
<p v-if="trialResult" :class="trialResult.ok?'inline-success':'inline-error'" role="status">{{trialResult.text}}</p>
<ul v-if="calcErrs.length" class="inline-error"><li v-for="e in calcErrs" :key="e">{{e}}</li></ul>
<div class="tools"><button class="primary" :disabled="saving" @click="saveCalc">{{saving?'保存中…':'保存计算函数'}}</button><button :disabled="saving" @click="close">取消</button></div>
</section>

<section v-else class="card rule-editor"><div class="panelhead"><h2>{{draft.name||'新建取值规则'}}</h2><button class="primary" :disabled="saving" @click="save">{{saving?'保存中…':'保存取值规则'}}</button></div>
<label>规则名称 *<input aria-label="规则名称" v-model="draft.name" placeholder="例如：采样值查询"></label>
<p class="fill-hint">规则可被不同对象、不同属性复用。表名、属性名和实例主键在“属性取值”绑定时填写。</p>
<p v-if="migrationInputs" class="field-help">保存后，旧规则的表名和属性名会移到已有属性绑定中，原取值参数保持不变。</p>
<label>数据连接 *<AppSelect aria-label="规则数据连接" v-model="draft.connection" :options="connections"/></label>
<p v-if="usage(draft.id).length" class="inline-warning">当前引用 {{usage(draft.id).length}} 处：{{usageSummary(draft.id)}}。修改参数或返回类型后，请检查这些属性的取值配置；新增必填参数不会自动补值。</p>
<h3>输入参数</h3><p class="field-help">定义参数名称与类型；属性绑定时填写具体值，或由查询调用时传入时间范围等参数。</p>
<div v-for="(p,i) in draft.inputs" :key="i" class="input-row">
<label>参数名称<input v-model="p.name" placeholder="例如 model_id"></label><label>数据类型<AppSelect v-model="p.type" :options="types"/></label>
<label>说明<input v-model="p.description" placeholder="例如当前实例主键"></label><label>传入方式<AppSelect v-model="p.source" :options="[{value:'binding',label:'属性绑定时填写'},{value:'runtime',label:'查询时传入'}]"/></label><button @click="draft.inputs.splice(i,1)">删除参数</button></div>
<button @click="draft.inputs.push({name:'',type:'string',description:'',source:'binding'})">＋ 添加输入参数</button>

<!-- ===== V4 卡片编辑器 ===== -->
<template v-if="!legacy">
<h3>查询步骤 *</h3>
<p class="field-help">每张卡片一段 SELECT 查询（含 JOIN、聚合、子查询均可写在 SQL 内）。后续卡片用 <code>:技术名.输出列</code> 引用前面“一条”步骤的值，用 <code v-text="'{{技术名.输出列}}'"></code> 引用动态表名／字段名；输入参数写 <code>:参数名</code>。本期不支持 WITH 与多语句。</p>
<section v-for="(s,i) in draft.steps" :key="s.id" class="rule-step-card">
<div class="card-head">
<span class="step-no">步骤 {{Number(i)+1}}</span>
<input class="step-name" v-model="s.name" placeholder="步骤名称（中文，如：定位测点）" aria-label="步骤名称">
<code class="tech-name" :title="'稳定技术引用名，不可修改'">:{{s.key}} · {{s.key}}.列</code>
<span class="card-select"><AppSelect :model-value="s.cardinality" :aria-label="'记录数 '+Number(i)" :options="cardinalities" @update:model-value="s.cardinality=$event"/></span>
<div class="step-tools">
<button :disabled="Number(i)===0" @click="moveStep(Number(i),-1)">上移</button>
<button :disabled="Number(i)===draft.steps.length-1" @click="moveStep(Number(i),1)">下移</button>
<button @click="removeStep(Number(i))">删除</button>
</div>
</div>
<textarea v-model="s.sql" :ref="(el:any)=>setSqlRef(Number(i),el)" class="sql-editor" :aria-label="'SQL 步骤 '+(Number(i)+1)" spellcheck="false" rows="10" placeholder="SELECT scada_table, scada_id&#10;FROM s_attr_scada&#10;WHERE model_id = :model_id;"></textarea>
<div class="card-foot">
<button type="button" @click="openInsert(Number(i))">插入引用</button>
<small class="field-help">可供引用的输出列：{{stepOutputCandidates(s.sql||'').join('、')||'未能识别（可手动填写，列名未验证）'}}</small>
</div>
<div v-if="insertFor===Number(i)" class="insert-panel">
<label>引用来源<AppSelect v-model="insertSource" :options="[{value:'',label:'请选择'},...insertOptions(Number(i))]"/></label>
<template v-if="insertIsStep">
<label>输出列<input v-model="insertColumn" list="v4-insert-cols" placeholder="从候选选择或填写"></label>
<datalist id="v4-insert-cols"><option v-for="c in insertColumns" :key="c" :value="c"/></datalist>
<label>插入方式<AppSelect v-model="insertKind" :options="[{value:'value',label:'作为值（:技术名.列）'},{value:'identifier',label:'作为表名／字段名（{{技术名.列}}）'}]"/></label>
</template>
<div class="tools"><button type="button" class="primary" @click="doInsert">插入到光标处</button><button type="button" @click="insertFor=null">关闭</button></div>
<p v-if="insertIsStep" class="field-help">仅展示排在当前步骤之前且预期“一条”的步骤；输出列来自 SQL 别名识别，未验证实际存在。</p>
</div>
<ul v-if="stepErrs[Number(i)]?.length" class="inline-error"><li v-for="e in stepErrs[Number(i)]" :key="e">{{e}}</li></ul>
</section>
<button @click="addStep">＋ 添加步骤</button>
<section class="sample-panel"><h3>返回结果</h3><div class="form-grid">
<label>输出步骤<AppSelect v-model="draft.result.step" :options="outStepOptions"/></label>
<label>返回类型<AppSelect v-model="draft.result.type" :options="[{value:'scalar',label:'单值'},{value:'timeSeries',label:'时间序列'}]"/></label>
<label>值的数据类型<AppSelect v-model="draft.result.valueType" :options="types"/></label>
<label v-if="draft.result.type==='timeSeries'">时间列名<input v-model="draft.result.timestamp" list="v4-out-cols" placeholder="timestamp"></label>
<label>值列名<input v-model="draft.result.value" list="v4-out-cols" placeholder="value"></label>
</div>
<datalist id="v4-out-cols"><option v-for="c in outCandidates" :key="c" :value="c"/></datalist>
<p v-if="outNotLast" class="inline-warning">输出步骤之后的步骤不参与返回，建议删除无用步骤；所有步骤仍参与配置校验。</p>
<p class="field-help">列名可从识别出的输出候选中选择或明确填写（未验证）。单值须选“一条”的步骤；时间序列须选“多条”的步骤，并在该步 SQL 中写清 ORDER BY 时间列升序。空值保留 null，不转零。</p>
</section>
<ul v-if="globalErrs.length" class="inline-warning"><li v-for="issue in globalErrs" :key="issue">{{issue}}</li></ul>
</template>

<!-- ===== 旧模板回退编辑（V4 转换失败时，A12） ===== -->
<template v-else>
<h3>SQL 模板 *（旧格式回退编辑）</h3>
<p class="inline-warning">无法无损转换为步骤卡片：{{legacyReason}}。以下保留原文，可继续用旧模板编辑保存，或修正后重新打开。</p>
<textarea aria-label="SQL 模板" v-model="draft.sqlTemplate" class="sql-editor" spellcheck="false" rows="22" placeholder="-- @step query one&#10;SELECT capacity AS value FROM devices WHERE id = :model_id;"/>
<p class="field-help">每段以 <code>-- @step 名称 one</code> 或 <code>-- @step 名称 many</code> 开始。输入值写 <code>:model_id</code>；前段结果值写 <code>:point.scadaId</code>；动态表名／字段名写 <code v-text="'{{point.scadaTable}}'"></code>。</p>
<ul v-if="queryRuleErrors(draft).length" class="inline-warning"><li v-for="issue in queryRuleErrors(draft)" :key="issue">{{issue}}</li></ul>
</template>

<p class="field-help">保存的是取值配置，当前不执行真实数据库查询。</p>
<p v-if="error" class="inline-error" role="alert">{{error}}</p><div class="tools"><button class="primary" :disabled="saving" @click="save">{{saving?'保存中…':'保存取值规则'}}</button><button :disabled="saving" @click="close">取消</button></div>
</section></template>
<template v-else>
<div class="manager-heading"><button class="primary" @click="newRule">＋ 新建查询规则</button><button @click="newCalc">＋ 新建计算函数</button><div><p>每种取值规则只维护一套流程。属性取值时选择规则、填写入参，SOC、温度、功率等属性可共用同一规则。</p></div></div>
<p v-if="saved" class="inline-success" role="status">{{saved}}</p>
<section class="card">
<div class="panelhead"><h2>查询规则（{{rules.length}}）</h2><span class="status-pill">{{rules.length?'已保存配置':'待配置'}}</span></div>
<p class="field-help">列表默认只显示规则名；点「详情」查看 SQL 步骤实现与引用摘要（有界展示），点「编辑」进入步骤卡片编辑。</p>
<template v-for="r in rules" :key="r.id">
<div class="panelhead rule-row">
<div><strong>{{r.name||'（未命名规则）'}}</strong> <span v-if="r.id===samplingRule?.id" class="status-pill">采样示例</span><span v-if="isCalcFunction(r)" class="status-pill">计算函数</span></div>
<div class="step-tools">
<button class="row-link" :aria-expanded="detailFor===r.id" @click="toggleDetail(r.id)">{{detailFor===r.id?'收起详情':'详情'}}</button>
<button class="row-link" @click="open(r)">编辑</button>
</div>
</div>
<div v-if="detailFor===r.id&&isCalcFunction(r)" class="rule-detail"><p class="field-help">{{usage(r.id).length?('被 '+usage(r.id).length+' 处属性引用：'+usageSummary(r.id))+'；修改前请到对应对象的「属性取值」确认影响。':'尚未被属性引用'}}</p><p class="field-help">输入：{{r.inputs.map((p:any)=>p.name+'（'+calcTypeNames[p.type]+')').join('、')}}</p><p class="field-help">公式：{{toDisplayFormula(r.implementation.expression,r.inputs)}}</p><p class="field-help">输出：{{r.output.name}}（{{calcTypeNames[r.output.type]}}）</p><p class="field-help">已保存取值配置；尚未执行实际属性取值。</p></div><div v-if="detailFor===r.id&&!isCalcFunction(r)" class="rule-detail">
<p class="field-help">{{usage(r.id).length?('被 '+usage(r.id).length+' 处属性引用：'+usageSummary(r.id)):'尚未被属性引用'}}；修改规则前请到对应对象的「属性取值」确认影响。</p>
<QueryRuleImplementation :rule="r"/>
</div>
</template>
<div v-if="!rules.length" class="empty-state">
<div class="empty-state-ico">◇</div>
<p>还没有取值规则；可从采样示例创建，或点上方「＋ 新建查询规则」从空白开始。</p>
<button @click="openSampling">按采样示例创建</button>
</div>
</section>
<details v-if="(refState?.workflow?.functions||[]).length||(projectState.implementations||[]).some((i:any)=>!isQueryRule(i))" :open="legacy"><summary>原有计算实现（兼容保留）</summary><ImplementationManager :project-state="projectState" :ref-state="refState" :focus-impl="focusImpl" @before-change="emit('before-change')" @changed="emit('changed')"/></details></template>
</template>
<style scoped>
.sql-editor{width:100%;box-sizing:border-box;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:14px;line-height:1.65;tab-size:2;resize:vertical;padding:16px;background:var(--paper-2);color:var(--ink);border:1px solid var(--line);border-radius:8px;min-height:120px}
.input-row{display:grid;grid-template-columns:1fr 140px 1.5fr 180px auto;gap:10px;align-items:end;margin:12px 0}
.rule-editor{margin-top:14px}
.rule-row{padding:14px 0;border-bottom:1px solid var(--line)}
.rule-detail{border:1px solid var(--line);border-radius:8px;background:var(--paper-2);padding:2px 16px 10px;margin:10px 0 16px}
summary{cursor:pointer;padding:12px}p{overflow-wrap:anywhere}
.rule-step-card{border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin:14px 0;background:var(--paper)}
.card-head{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:10px}
.step-no{font-weight:650;color:var(--muted);white-space:nowrap}
.step-name{flex:1;min-width:220px}
.tech-name{font-size:12px;color:var(--muted);background:var(--paper-3);border:1px solid var(--line);border-radius:5px;padding:4px 8px;white-space:nowrap}
.card-foot{display:flex;gap:12px;align-items:center;margin-top:8px;flex-wrap:wrap}
.insert-panel{display:grid;gap:10px;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));background:var(--paper-2);border:1px solid var(--line);border-radius:8px;padding:12px;margin-top:10px;align-items:end}
.step-tools{display:flex;gap:6px}
.card-select{display:inline-block;width:180px;flex:0 0 auto}
@media(max-width:1000px){.input-row{grid-template-columns:1fr}}
</style>
