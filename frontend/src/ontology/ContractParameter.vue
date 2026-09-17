<script setup lang="ts">
import {computed} from 'vue'
import AppSelect from '../shared/AppSelect.vue'
import Field from '../shared/EditorField.vue'
import {effectiveProperty, propertyTypeLabel, dataTypeLabel} from './propertyModel'

const props=defineProps<{item:any;graph:any[];direction:'input'|'output';index:number;error?:string}>()
defineEmits<{remove:[]}>()
const label=computed(()=>`${props.direction==='input'?'提供':'返回'}项 ${props.index+1}`)
const basics=[['string','文本'],['double','数值'],['boolean','是／否'],['timestamp','时间'],['array','数组'],['struct','结构体'],['timeSeries','时间序列']]
const key=(r:any)=>r?.kind==='base'?'base:'+r.dataType:r?.id?r.kind+':'+r.id:''
function nameOf(r:any):string {
  if(r?.kind==='base')return dataTypeLabel({type:r.dataType,valueType:r.valueType})
  const node=props.graph.find(n=>n['@id']===r?.id)
  return node?(effectiveProperty(node,props.graph)['rdfs:label']||node['@id']):''
}
const choices=computed(()=>{
  const objects=props.graph.filter(n=>n['@type']==='owl:Class').map(n=>({value:'object:'+n['@id'],label:'对象 · '+(n['rdfs:label']||n['@id'])}))
  const properties=props.graph.filter(n=>['owl:DatatypeProperty','mg:SharedProperty'].includes(n['@type'])).map(n=>{
    const owner=props.graph.find(o=>o['@id']===n['rdfs:domain']?.['@id'])
    const prefix=n['@type']==='mg:SharedProperty'?'共享属性':(owner?.['rdfs:label']||'对象属性')
    return {value:'property:'+n['@id'],label:prefix+' → '+nameOf({id:n['@id']})+' · '+propertyTypeLabel(n,props.graph)}
  })
  const all=[...objects,...properties,...basics.map(([t,n])=>({value:'base:'+t,label:'自定义内容 · '+n}))]
  const current=key(props.item.ref)
  if(current&&!all.some(c=>c.value===current))all.push({value:current,label:props.item.ref.kind==='base'?'自定义内容 · '+nameOf(props.item.ref):'原选择已不可用，请重新选择'})
  return all
})
const meaning=computed(()=>{
  const r=props.item.ref,n=props.graph.find(n=>n['@id']===r?.id)
  if(!n)return ''
  if(r.kind==='object')return '调用时选择一个具体的'+nameOf(r)+'；项目中负责关联它的实际数据。'
  return effectiveProperty(n,props.graph)['rdfs:comment']||'名称和数据类型沿用已选属性的定义。'
})
function choose(value:string){
  const previous=nameOf(props.item.ref), split=value.indexOf(':'), kind=value.slice(0,split), target=value.slice(split+1)
  props.item.ref=kind==='base'?{kind,dataType:target,...(target==='timeSeries'?{valueType:'double'}:{})}:{kind,id:target}
  if(!props.item.name?.trim()||props.item.name===previous)props.item.name=kind==='base'?'':nameOf(props.item.ref)
}
const observations=computed(()=>{
  const all=[{value:'double',label:'数值'},{value:'string',label:'文本'},{value:'boolean',label:'是／否'},{value:'dateTime',label:'时间'}]
  const current=props.item.ref?.valueType
  if(current&&!all.some(x=>x.value===current))all.push({value:current,label:dataTypeLabel({type:current})})
  return all
})
</script>

<template>
<div class="parameter-card">
  <div class="parameter-top"><strong>{{label}}</strong><button type="button" :aria-label="'移除'+label" @click="$emit('remove')">移除</button></div>
  <label class="editor-field"><span class="field-title">{{direction==='input'?'需要提供的内容':'希望返回的内容'}} <b class="required-mark">*</b></span>
    <AppSelect :aria-label="label+'内容'" :model-value="key(item.ref)" :options="choices" searchable placeholder="搜索对象、属性，或选择自定义内容" @update:model-value="choose"/>
  </label>
  <p v-if="meaning" class="field-help">{{meaning}}</p>
  <div v-if="item.ref?.kind==='base'" class="form-grid">
    <Field label="这项内容叫什么" v-model="item.name" required :example="direction==='input'?'例如：开始时间、结束时间':'例如：平均 SOC、可用容量'"/>
    <Field v-if="item.ref.dataType==='timeSeries'" label="观测值类型" type="select" v-model="item.ref.valueType" :options="observations" required/>
  </div>
  <details v-if="item.ref" class="parameter-details">
    <summary>{{item.ref.kind==='base'?'补充说明（选填）':'名称与补充说明（选填）'}}<span v-if="item.ref.kind!=='base' && item.name!==nameOf(item.ref)"> · {{item.name}}</span></summary>
    <Field v-if="item.ref.kind!=='base'" label="在这个能力中称为" v-model="item.name" :example="nameOf(item.ref)" help="默认沿用所选名称；例如同为储能簇，可区分为源储能簇和目标储能簇。"/>
    <Field label="补充说明" v-model="item.description" example="仅在需要补充用途或业务口径时填写。"/>
  </details>
  <p v-if="error" class="inline-error" role="alert">{{error}}</p>
</div>
</template>

<style scoped>
.parameter-card{background:white;border:1px solid var(--line);border-radius:8px;padding:14px 18px;margin:12px 0}
.parameter-top{display:flex;align-items:center;justify-content:space-between;gap:12px}
.parameter-top strong{font-size:13px;color:var(--muted)}
.parameter-top button{font-size:12px}
.parameter-card :deep(.editor-field){margin:12px 0}
.parameter-details{margin-top:12px;border-top:1px solid var(--line);padding-top:10px}
.parameter-details summary{cursor:pointer;font-size:12px;color:var(--muted)}
</style>
