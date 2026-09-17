<script setup lang="ts">
// 项目登记实例编辑（无表对象任务 A）：只编辑本地草稿数组（经 update:instances 上抛副本），
// 由父级 ObjectSources 的实例识别表单统一经 form-save 落盘，本组件不直接写 b、不自起保存。
// 编号即实例身份（v1 不可变）：编辑时禁改并提示“如需变更编号请新建实例并处理引用”；
// 删除前检查 membership 规则引用（props.b 已保存态），被引用则阻止并列出链接名；未引用 confirm 后删。
import {computed,ref} from 'vue'
import {instanceMembershipReferencesOf,hasRegisteredPropertySources} from './bindingModel'
import type {RegisteredInstanceView} from './bindingModel'
const props=defineProps<{b:any;refState?:any;instances:RegisteredInstanceView[]}>()
const emit=defineEmits(['update:instances'])
const graph=computed(()=>props.refState?.ontology?.['@graph']||[])
function relationLabel(id:string){const n=graph.value.find((x:any)=>x['@id']==='mg:'+id);return String(n?.['rdfs:label']||id)}
// 引用标记：被 membership 规则引用（具体实例）；存在登记信息属性时整列间接使用（泛引用）
const registeredUsed=computed(()=>hasRegisteredPropertySources(props.b))
function memberRefs(id:string){return instanceMembershipReferencesOf(props.b,id)}
function setInstances(next:RegisteredInstanceView[]){emit('update:instances',next.map(i=>({id:i.id,label:i.label})))}
// ---------- 行内编辑器：index=-1 新增；编号编辑时禁改 ----------
const editing=ref<{index:number;id:string;label:string}|null>(null)
const message=ref('')
function openAdd(){editing.value={index:-1,id:'',label:''};message.value=''}
function openEdit(i:number){const inst=props.instances[i];if(!inst)return;editing.value={index:i,id:inst.id,label:inst.label};message.value=''}
function cancelRow(){editing.value=null;message.value=''}
function commitRow(){
  const d=editing.value;if(!d)return
  const id=d.id.trim(),label=d.label.trim()
  if(!id){message.value='请填写实例编号。';return}
  if(props.instances.some((inst,i)=>i!==d.index&&inst.id===id)){message.value='实例编号「'+id+'」在本对象已登记，请换一个编号。';return}
  const next=props.instances.map(i=>({id:i.id,label:i.label}))
  if(d.index<0)next.push({id,label});else next[d.index]={id,label}
  setInstances(next)
  editing.value=null;message.value=''
}
function remove(i:number){
  const inst=props.instances[i];if(!inst)return
  const refs=memberRefs(inst.id)
  if(refs.length){message.value='实例「'+inst.id+'」仍被成员规则引用（'+refs.map(relationLabel).join('、')+'），请先到「链接映射」移除对应规则后再删除。';return}
  if(!confirm('删除登记实例「'+inst.id+(inst.label?'（'+inst.label+'）':'')+'」？删除需保存表单后才落盘。'))return
  message.value=''
  setInstances(props.instances.filter((_,x)=>x!==i))
}
</script>
<template><div class="ri">
<div class="ri-head"><h4>已登记实例</h4><small class="muted">编号在当前项目同一对象类型中唯一；修改随实例识别表单一并保存。</small></div>
<div v-for="(inst,i) in instances" :key="inst.id+'-'+i" class="ri-row">
  <div class="ri-main"><code class="ri-id">{{inst.id}}</code><span>{{inst.label||'（未填显示名称）'}}</span></div>
  <span v-if="memberRefs(inst.id).length" class="status-pill" :title="'链接：'+memberRefs(inst.id).map(relationLabel).join('、')">被成员规则引用</span>
  <span v-if="registeredUsed" class="status-pill" title="存在登记信息来源的属性取值">被登记信息属性引用</span>
  <div class="tools"><button @click="openEdit(i)">编辑</button><button @click="remove(i)">删除</button></div>
</div>
<p v-if="!instances.length" class="field-help">尚未登记实例：点击下方「＋ 登记实例」添加，例如编号 CZYEQ-ESS-001、显示名称「创智园二期储能系统」。</p>
<div v-if="!editing" class="tools"><button @click="openAdd">＋ 登记实例</button></div>
<div v-else class="ri-editor">
  <h4>{{editing.index<0?'新增实例':'编辑实例'}}</h4>
  <div class="row">
    <label>实例编号 *<input v-model="editing.id" :disabled="editing.index>=0" placeholder="例如 CZYEQ-ESS-001" /><small class="field-help">{{editing.index>=0?'编号即实例身份，第一版不允许直接修改；如需变更编号请新建实例并处理引用。':'在当前项目的同一对象类型中保持唯一；保存后不可直接修改。'}}</small></label>
    <label>显示名称<input v-model="editing.label" placeholder="例如 创智园二期储能系统" /><small class="field-help">便于管理的标签；属性取值可把文本属性绑定到登记信息的显示名称。</small></label>
  </div>
  <p v-if="message" class="inline-error" role="alert">{{message}}</p>
  <div class="tools"><button class="primary" @click="commitRow">{{editing.index<0?'加入列表':'保存修改'}}</button><button @click="cancelRow">取消</button></div>
</div>
</div></template>
<style scoped>
.ri-head{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;margin:0 0 10px}
.ri-head h4{margin:0;font-size:14px}
.ri-row{display:flex;justify-content:space-between;align-items:center;gap:12px;padding:10px 0;border-bottom:1px solid var(--line);flex-wrap:wrap}
.ri-row:last-of-type{border-bottom:0}
.ri-main{display:flex;align-items:baseline;gap:10px;flex:1;min-width:240px;flex-wrap:wrap}
.ri-id{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:13px;background:var(--paper-2);border-radius:5px;padding:2px 7px}
.ri-editor{border:1px solid var(--line);border-radius:8px;padding:14px 16px;margin-top:14px;background:var(--paper-2)}
.ri-editor h4{margin:0 0 12px;font-size:14px}
.ri-editor .tools{margin-top:6px}
</style>
