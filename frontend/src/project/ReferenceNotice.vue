<script setup lang="ts">
import {computed,onBeforeUnmount,ref,watch} from 'vue'
import {loadStateRaw,versionStateRaw} from '../ontology/api'
import {definitionSignature,propertyDifferences} from './referenceChanges'
const props=defineProps<{projectState:any;objectType?:string}>()
const emit=defineEmits(['navigate','open-ontology'])
const loading=ref(false),error=ref(''),draft=ref<any>(null),current=ref<any>(null),latest=ref<any>(null),latestVersion=ref('')
let generation=0
async function refresh(){
  const gen=++generation,oid=props.projectState?.ontologyId,version=props.projectState?.ontologyVersion
  draft.value=current.value=latest.value=null;latestVersion.value='';error.value=''
  if(!oid||!version){loading.value=false;return}
  loading.value=true
  try{
    const [d,c]=await Promise.all([loadStateRaw(oid),versionStateRaw(oid,version)])
    const lv=d.latestVersion||version
    const l=lv===version?c:await versionStateRaw(oid,lv)
    if(gen!==generation)return
    draft.value=d.state;current.value=c.state;latest.value=l.state;latestVersion.value=lv
  }catch(e){if(gen===generation)error.value=(e as Error).message}
  finally{if(gen===generation)loading.value=false}
}
watch(()=>[props.projectState?.ontologyId,props.projectState?.ontologyVersion],refresh,{immediate:true})
onBeforeUnmount(()=>{generation++})
const unpublished=computed(()=>!!draft.value&&!!latest.value&&definitionSignature(draft.value)!==definitionSignature(latest.value))
const upgradeAvailable=computed(()=>!!latestVersion.value&&latestVersion.value!==props.projectState?.ontologyVersion)
const differences=computed(()=>props.objectType?propertyDifferences(current.value,draft.value,props.objectType):[])
</script>
<template>
<section v-if="loading||error||unpublished||upgradeAvailable" class="reference-notice" aria-label="本体版本变化">
<p v-if="loading" class="muted">正在检查引用版本与本体变化…</p>
<template v-else-if="error"><p>本体变化暂时无法读取：{{error}}</p><button @click="refresh">重新检查</button></template>
<template v-else>
<strong>当前项目使用本体 {{projectState.ontologyVersion}}</strong>
<p v-if="unpublished">本体有尚未发布的修改，项目属性列表仍按已引用版本显示。</p>
<p v-if="upgradeAvailable">已有本体新版本 {{latestVersion}}，更新项目引用后才能使用其中的定义。</p>
<details v-if="differences.length"><summary>查看当前对象的属性差异（{{differences.length}} 项）</summary>
<p class="muted">对比项目引用版本与本体已保存草稿；不自动改变映射。</p>
<table><thead><tr><th>属性</th><th>草稿中的变化</th></tr></thead><tbody><tr v-for="row in differences" :key="row.id"><td>{{row.name}}</td><td>{{row.change}}</td></tr></tbody></table>
</details>
<div class="tools">
<button v-if="unpublished" @click="emit('open-ontology')">查看变化并发布本体新版本 →</button>
<button @click="emit('navigate','p-upgrade')">更新项目引用 →</button>
</div>
<small>本体有修改：先发布本体新版本，再更新项目引用。发布项目配置不会发布本体，也不会自动升级引用。</small>
</template>
</section>
</template>
<style scoped>
.reference-notice{background:var(--warn-soft);border:1px solid var(--warn-line);border-radius:8px;padding:14px 16px;margin:0 0 16px;font-size:13px;color:#624b23}
.reference-notice p{margin:6px 0}.reference-notice summary{cursor:pointer;margin:8px 0}.reference-notice small{display:block;margin-top:8px;color:#77654b}.reference-notice .tools{margin-top:10px;flex-wrap:wrap}
</style>
