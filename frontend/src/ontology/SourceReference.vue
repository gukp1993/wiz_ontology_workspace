<script setup lang="ts">
import {ref,computed} from 'vue'
const props=defineProps({ids:{type:Array,default:()=>[]},note:String})
import { sourceReference } from './api'
const nodes=ref([]),error=ref(''),loaded=ref(false)
const records=computed(()=>nodes.value.filter(n=>props.ids.includes(n.id)))
async function load(e){if(!e.target.open||loaded.value)return;try{nodes.value=(await sourceReference()).nodes||[];loaded.value=true}catch(e){error.value=e.message}}
const fieldLabels={definition:'原文定义',description:'补充说明',unit:'单位',instance_table:'数据表线索',fetch_method:'取数说明',rule_content:'规则原文',param_intro:'参数说明'}
</script>
<template><details v-if="ids.length||note" class="source-reference" @toggle="load"><summary>来源参考 <span>{{ids.length?ids.length+' 个原始节点':'建模说明'}}</span></summary><p v-if="note" class="source-note">{{note}}</p><p class="field-help">原始资料仅作参考；含义冲突、字段和公式仍需核实。</p><p v-if="error" role="alert">{{error}}</p><article v-for="n in records" :key="n.id"><strong>{{n.name}}</strong><small>{{n.id}}</small><template v-for="(label,key) in fieldLabels" :key="key"><div v-if="n.data?.[key]"><h4>{{label}}</h4><p>{{typeof n.data[key]==='string'?n.data[key]:JSON.stringify(n.data[key])}}</p></div></template></article><p v-if="loaded&&ids.length&&!records.length" class="muted">此项为归纳定义，没有直接对应的原文节点。</p></details></template>
