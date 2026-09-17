<script setup lang="ts">
import {graphReferences,dataTypeOptionsFor,shortType} from '../ontology/editorModel'
import { valueTypeCheck } from '../ontology/api'
import {computed,ref,watch} from 'vue'
import { appConfirm } from '../shared/appConfirm'
import EditorLayout from '../shared/EditorLayout.vue'
import Field from '../shared/EditorField.vue'
import SourceReference from '../ontology/SourceReference.vue'
import {newId} from '../ontology/propertyModel'
const props=defineProps({state:Object}),emit=defineEmits(['before-change','changed','properties'])
const graph=computed(()=>props.state.ontology['@graph']),items=computed(()=>graph.value.filter(n=>n['@type']==='mg:ValueType'))
const query=ref(''),selected=ref(''),sample=ref('70.5'),result=ref(''),busy=ref(false)
const node=computed(()=>items.value.find(n=>n['@id']===selected.value)),base=computed(()=>node.value?.['rdfs:range']['@id']),constraint=computed(()=>node.value?.['mg:constraint']['@value'])
const references=computed(()=>node.value?graphReferences(props.state,node.value['@id']):[])
const referenceLocations=computed(()=>{
 const id=node.value?.['@id'];if(!id)return []
 const match=v=>[id,id.replace(/^mg:/,'')].includes(typeof v==='string'?v:v?.['@id'])
 return graph.value.filter(n=>n['@id']!==id).flatMap(n=>{
  const c=n['mg:constraint']?.['@value'],owner=graph.value.find(o=>o['@id']===n['rdfs:domain']?.['@id'])
  const reasons=[]
  if(match(n['mg:valueType']))reasons.push('值类型')
  if(match(c?.elementValueType))reasons.push('数组元素')
  for(const f of c?.fields||[])if(match(f.valueType))reasons.push('字段 '+f.name)
  return reasons.map(reason=>({id:n['@id'],type:n['@type'],owner:owner?.['@id']||'',label:(owner?owner['rdfs:label']+' · ':'')+n['rdfs:label']+'（'+reason+'）'}))
 })
})
function openReference(location){
 if(location.type==='mg:ValueType'){query.value='';selected.value=location.id}
 else emit('properties',location.owner,location.id)
}
const labels={none:'不增加约束',range:'范围',enum:'枚举',regex:'文本格式（正则）',uuid:'UUID格式',rid:'RID格式',array:'数组约束',struct:'结构体字段约束'}
const list=computed(()=>items.value.filter(n=>(n['rdfs:label']||'').includes(query.value.trim())).map(n=>({id:n['@id'],name:n['rdfs:label'],meta:shortType(n['rdfs:range']['@id']),note:labels[n['mg:constraint']?.['@value']?.kind]})))
const complex=computed(()=>['xsd:array','xsd:struct'].includes(base.value))
const kindOptions=computed(()=>{
 const kinds=['none'];if(base.value==='xsd:array')kinds.push('array');else if(base.value==='xsd:struct')kinds.push('struct');else{
 if(base.value!=='xsd:boolean')kinds.push('range');if(!['xsd:date','xsd:dateTime'].includes(base.value))kinds.push('enum');if(base.value==='xsd:string')kinds.push('regex','uuid','rid')}
 return kinds.map(value=>({value,label:value==='range'?(base.value==='xsd:string'?'文本长度范围':base.value==='xsd:array'?'数组长度范围':'数值／日期范围'):labels[value]}))
})
function dependsOn(id:string,target:string,seen=new Set<string>()):boolean{
 if(id===target)return true;if(seen.has(id))return false;seen.add(id)
 const c=items.value.find(n=>n['@id']===id)?.['mg:constraint']?.['@value'];return !!c&&[c.elementValueType,...(c.fields||[]).map(f=>f.valueType)].filter(Boolean).some(ref=>dependsOn(ref,target,seen))
}
const nestedOptions=computed(()=>items.value.filter(n=>!dependsOn(n['@id'],selected.value)).map(n=>({value:n['@id'],label:n['rdfs:label']+' · '+shortType(n['rdfs:range']['@id'])})))
const example=computed(()=>base.value==='xsd:array'?'[70, 80]':base.value==='xsd:struct'?'{"soc":70}':constraint.value?.kind==='uuid'?'550e8400-e29b-41d4-a716-446655440000':constraint.value?.kind==='rid'?'ri.foundry.main.dataset.550e8400-e29b-41d4-a716-446655440000':constraint.value?.kind==='regex'?'ESS-000001':base.value==='xsd:boolean'?'true':base.value==='xsd:date'?'2026-09-09':base.value==='xsd:dateTime'?'2026-09-09T10:00:00+08:00':'70.5')
watch(items,v=>{if(!v.some(n=>n['@id']===selected.value))selected.value=v[0]?.['@id']||''},{immediate:true})
watch(()=>JSON.stringify(node.value),()=>result.value='')
watch(selected,()=>{sample.value='';result.value=''})
function before(){emit('before-change')}
function changed(){result.value='';emit('changed')}
function mutate(fn){before();fn();changed()}
function create(){query.value='';before();const id=newId();graph.value.push({'@id':id,'@type':'mg:ValueType','rdfs:label':'新值类型','rdfs:comment':'','rdfs:range':{'@id':'xsd:double'},'mg:constraint':{'@type':'@json','@value':{kind:'none'}}});selected.value=id;changed()}
async function remove(){if(references.value.length){result.value='暂不能删除：'+references.value.join('、');return}if(!(await appConfirm({ message: '删除这个未被引用的值类型？', danger: true })))return;mutate(()=>props.state.ontology['@graph']=graph.value.filter(n=>n['@id']!==selected.value))}
function setKind(kind){node.value['mg:constraint']['@value']={kind,...(kind==='enum'?{values:'',caseSensitive:true}:kind==='range'?{minInclusive:true,maxInclusive:true}:kind==='regex'?{pattern:'',matchMode:'full',caseSensitive:true}:kind==='array'?{unique:false,elementValueType:''}:kind==='struct'?{fields:[]}:{})};changed()}
function reset(){setKind(base.value==='xsd:array'?'array':base.value==='xsd:struct'?'struct':'none')}
function setBase(value){if(references.value.length)return;node.value['rdfs:range']['@id']=value;reset()}
function boundary(key,event){const text=event.target.value;constraint.value[key]=text===''?null:(['xsd:date','xsd:dateTime'].includes(base.value)?text:Number(text));changed()}
function bool(key,event){mutate(()=>constraint.value[key]=event.target.checked)}
function fieldRequired(field,event:Event){mutate(()=>field.required=(event.target as HTMLInputElement).checked)}
function addField(){mutate(()=>constraint.value.fields.push({name:'',valueType:'',required:true}))}
async function check(){busy.value=true;result.value='';try{const data=await valueTypeCheck({state:props.state,valueTypeId:selected.value,sample:sample.value});result.value=data.valid?'样本符合定义':(data.errors?.join('；')||'检验失败')}catch(e){result.value='检验失败：'+e.message}finally{busy.value=false}}
</script>
<template>
<EditorLayout title="值类型管理" subtitle="定义可复用的值含义与校验规则；约束按数据类型提供。" :items="list" :selected="selected" v-model:search="query" create-label="新建值类型" @create="create" @select="selected=$event">
<section v-if="node" :key="selected" class="card detail-card">
<div class="detail-heading"><div><span class="eyebrow">值类型</span><h2>{{node['rdfs:label']||'新值类型'}}</h2></div><span class="status-pill">{{references.length}} 处引用</span></div>
<Field label="值类型名称" v-model="node['rdfs:label']" required example="SOC百分数" @before-change="before" @update:model-value="changed"/>
<Field label="业务描述" v-model="node['rdfs:comment']" type="textarea" required example="以百分数表达荷电状态，70表示70%。" @before-change="before" @update:model-value="changed"/>
<Field label="数据类型" :model-value="base" type="select" :options="dataTypeOptionsFor(base)" required :disabled="references.length>0" :help="references.length?'已被引用，数据类型锁定。':'与引用属性的数据类型一致；切换类型会重新设置约束。'" @before-change="before" @update:model-value="setBase"/>
<Field label="约束方式" :model-value="constraint.kind" type="select" :options="kindOptions" @before-change="before" @update:model-value="setKind"/>
<div v-if="constraint.kind==='range'||constraint.kind==='array'" class="sample-panel">
<h3>{{base==='xsd:string'?'文本长度':base==='xsd:array'?'数组长度':'允许范围'}}</h3><p>{{constraint.kind==='range'?'至少填写一个边界。':'长度可不限制；元素规则和去重可同时使用。'}}</p>
<div class="form-grid"><label>最小值<input :value="constraint.min" aria-label="最小值" placeholder="例如 0" @focus="before" @input="boundary('min',$event)"></label><label>最大值<input :value="constraint.max" aria-label="最大值" placeholder="例如 100" @focus="before" @input="boundary('max',$event)"></label></div>
<div v-if="constraint.kind==='range'" class="form-grid"><label class="check-option"><input type="checkbox" :checked="constraint.minInclusive!==false" @change="bool('minInclusive',$event)">包含最小值</label><label class="check-option"><input type="checkbox" :checked="constraint.maxInclusive!==false" @change="bool('maxInclusive',$event)">包含最大值</label></div>
</div>
<template v-if="constraint.kind==='enum'"><Field label="允许值（每行一个）" v-model="constraint.values" type="textarea" required :example="'充电\n放电\n待机'" @before-change="before" @update:model-value="changed"/><label v-if="base==='xsd:string'" class="check-option"><input type="checkbox" :checked="constraint.caseSensitive!==false" @change="bool('caseSensitive',$event)">区分大小写</label></template>
<div v-if="constraint.kind==='regex'" class="sample-panel"><h3>文本格式</h3><Field label="正则表达式" v-model="constraint.pattern" required example="ESS-[0-9]{6}" help="示例匹配 ESS-000001；不会执行代码。复杂规则可由实施人员填写。" @before-change="before" @update:model-value="changed"/><Field label="匹配方式" v-model="constraint.matchMode" type="select" :options="[{value:'full',label:'整个值符合格式'},{value:'search',label:'包含符合格式的片段'}]" @before-change="before" @update:model-value="changed"/><label class="check-option"><input type="checkbox" :checked="constraint.caseSensitive!==false" @change="bool('caseSensitive',$event)">区分大小写</label></div>
<p v-if="constraint.kind==='uuid'||constraint.kind==='rid'" class="fill-hint">检查{{constraint.kind.toUpperCase()}}格式，不校验标识对应的资源是否存在。示例：{{example}}</p>
<div v-if="constraint.kind==='array'" class="sample-panel"><h3>数组元素</h3><label class="check-option"><input type="checkbox" :checked="constraint.unique" @change="bool('unique',$event)">元素不能重复</label><Field label="每个元素的值类型" v-model="constraint.elementValueType" type="select" :options="[{value:'',label:'不约束元素'},...nestedOptions]" help="例如引用 SOC百分数，逐个检查数组中的 SOC。" @before-change="before" @update:model-value="changed"/></div>
<div v-if="constraint.kind==='struct'" class="sample-panel"><div class="panelhead"><h3>字段约束</h3><button type="button" @click="addField">＋ 添加字段</button></div><p>为每个字段选择已有值类型。未列出的字段允许存在。</p><div v-for="(f,i) in constraint.fields" :key="i" class="parameter-card"><Field label="字段名" v-model="f.name" required example="soc" @before-change="before" @update:model-value="changed"/><Field label="字段值类型" v-model="f.valueType" type="select" required :options="nestedOptions" @before-change="before" @update:model-value="changed"/><label class="check-option"><input type="checkbox" :checked="f.required" @change="fieldRequired(f,$event)">字段必须有值</label><button type="button" @click="mutate(()=>constraint.fields.splice(i,1))">移除字段</button></div></div>
<div class="sample-panel"><h3>样本检验</h3><Field label="样本值" v-model="sample" :type="complex?'textarea':'text'" :example="example" :help="complex?'输入 JSON 数组或对象。':'数值不附带单位；是／否填写 true 或 false。'" @update:model-value="result=''"/><button :disabled="busy" @click="check">{{busy?'检验中…':'检验样本'}}</button><p role="status" :class="result==='样本符合定义'?'inline-success':'inline-error'" v-if="result">{{result}}</p></div>
<details class="technical-section"><summary>查看引用位置 · {{references.length}}</summary><p v-for="location in referenceLocations" :key="location.id+location.label"><button class="row-link" @click="openReference(location)">{{location.label}} ↗</button></p><p v-if="!references.length">尚未引用。</p></details>
<SourceReference :ids="node['mg:sourceIds']" :note="node['mg:reviewNote']"/>
<div class="detail-footer"><span>修改后点击顶部保存草稿</span><button class="danger" :disabled="references.length>0" @click="remove">删除值类型</button></div>
</section><section v-else class="card"><div class="empty-state"><div class="empty-state-ico">◇</div><p>还没有值类型。</p><button class="primary" @click="create">新建值类型</button></div></section>
</EditorLayout>
</template>
