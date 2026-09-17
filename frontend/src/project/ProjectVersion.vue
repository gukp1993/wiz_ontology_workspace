<script setup lang="ts">
// 选择版本自动比较，明确确认后通过 App 的表单事务保存；只修改项目草稿。
// applyReference 必须等服务端保存成功才 resolve，失败 reject 并恢复原引用。
// 同本体走升级预检；首次/跨本体绑定明确说明比较边界，不虚构差异。
import {computed,onMounted,ref,watch} from 'vue'
import AppSelect from '../shared/AppSelect.vue'
import { listVersions } from '../ontology/api'
import { projectPost } from './api'
const props=defineProps<{projectState:any;ontologyOptions:{value:string;label:string}[];applyReference:(target:{ontology:string;version:string})=>Promise<void>}>()
const emit=defineEmits(['navigate'])
const changeTypePill:Record<string,string>={breaking:'破坏性',compatible:'兼容候选',initial:'初始版本',pending:'待确认'}
const areaLabels:Record<string,string>={objectBinding:'对象映射',propertyMapping:'属性来源',linkMapping:'链接映射',implementation:'计算实现',propertySource:'属性计算来源',ontology:'本体定义'}
const severityLabel=(s:string)=>s==='breaking'?'破坏性':s==='pending'?'待确认':'兼容'
const ontoName=(id:string)=>props.ontologyOptions.find(o=>o.value===id)?.label||id||''
const current=computed(()=>({ontology:String(props.projectState?.ontologyId||''),version:String(props.projectState?.ontologyVersion||'')}))
const hasReference=computed(()=>!!(current.value.ontology&&current.value.version))

// 各本体的已发布版本（真实 listVersions；只保留有发布版本的本体可选）
const versionCache=ref<Record<string,any[]>>({}),versionsBusy=ref(false),loadError=ref('')
async function loadAllVersions(){
  versionsBusy.value=true;loadError.value=''
  const cache:Record<string,any[]>={}
  try{
    await Promise.all(props.ontologyOptions.map(async o=>{cache[o.value]=((await listVersions(o.value)).items||[])}))
    versionCache.value=cache
  }catch(e:any){loadError.value='读取已发布版本失败：'+e.message
  }finally{versionsBusy.value=false}
}
const eligibleOntologies=computed(()=>props.ontologyOptions.filter(o=>(versionCache.value[o.value]||[]).length>0))
const selectedOntology=ref(''),selectedVersion=ref('')
function pickDefaultOntology(){
  const eligible=eligibleOntologies.value
  if(current.value.ontology&&(versionCache.value[current.value.ontology]||[]).length){setOntology(current.value.ontology);return}
  setOntology(eligible[0]?.value||'')
}
function setOntology(id:string){
  selectedOntology.value=id||''
  const items=versionCache.value[id]||[]
  // 同本体时默认停在当前引用版本；切换本体时默认最新发布版本（选择不触发升级）
  const stay=current.value.ontology===id?items.find(v=>v.version===current.value.version):null
  selectedVersion.value=(stay||items[items.length-1])?.version||''
  clearCompare()
}
const versionOptions=computed(()=>((versionCache.value[selectedOntology.value]||[]).slice().reverse()).map(v=>({
  value:v.version,label:v.version+' · '+(changeTypePill[v.changeType]||v.changeType)+((selectedOntology.value===current.value.ontology&&v.version===current.value.version)?'（当前引用）':'')})))
onMounted(async()=>{await loadAllVersions();pickDefaultOntology()})
// 本体清单异步加载完成后重算可选范围（保持当前选择若仍有效）
watch(()=>props.ontologyOptions,()=>{void loadAllVersions().then(()=>{if(!selectedOntology.value||!eligibleOntologies.value.some(o=>o.value===selectedOntology.value))pickDefaultOntology()})})

// ─── 比较：真实 /api/project-upgrade-check；结果与所选绑定，选择变化即失效 ───
const report=ref<any>(null),comparedKey=ref(''),busy=ref(false),message=ref('')
const lastApplied=ref(''),applying=ref(false)
let compareGeneration=0
// 切换成功标记：当前引用确实等于最近一次确认切换的目标时才显示（失败/未生效不显示成功）
const applied=computed(()=>!!lastApplied.value&&lastApplied.value===(String(props.projectState?.ontologyId||'')+'@'+String(props.projectState?.ontologyVersion||'')))
function clearCompare(){compareGeneration++;busy.value=false;report.value=null;comparedKey.value=''}
watch([selectedOntology,selectedVersion],()=>{clearCompare();message.value='';lastApplied.value='';void runCompare()})
const selectionKey=computed(()=>selectedOntology.value+'@'+selectedVersion.value)
const canCompare=computed(()=>!!selectedOntology.value&&!!selectedVersion.value&&selectionKey.value!==current.value.ontology+'@'+current.value.version)
async function runCompare(){
  if(applying.value||!canCompare.value)return
  busy.value=true;message.value=''
  const key=selectionKey.value,generation=++compareGeneration
  try{
    if(!hasReference.value){
      report.value={kind:'first'};comparedKey.value=key;return
    }
    if(selectedOntology.value!==current.value.ontology){
      report.value={kind:'cross'};comparedKey.value=key;return
    }
    try{
      const data=await projectPost('project-upgrade-check',{state:props.projectState,targetVersion:selectedVersion.value})
      if(generation!==compareGeneration||key!==selectionKey.value)return
      report.value={kind:'diff',data}
      comparedKey.value=key
    }catch(e:any){
      if(generation===compareGeneration)message.value='比较失败：'+(e.data?.error||e.message)+'。请点击“重新比较”重试。'
    }
  }finally{if(generation===compareGeneration)busy.value=false}
}
const confirmed=computed(()=>!!report.value&&comparedKey.value===selectionKey.value)
async function confirmApply(){
  if(!confirmed.value||busy.value||applying.value)return
  applying.value=true;message.value=''
  const key=selectionKey.value
  try{
    await props.applyReference({ontology:selectedOntology.value,version:selectedVersion.value})
    lastApplied.value=key;clearCompare()
  }catch(e:any){message.value='切换未完成：'+e.message+'。原引用保留，可重试。'}
  finally{applying.value=false}
}
watch(()=>[props.projectState?.projectId,props.projectState?.ontologyId,props.projectState?.ontologyVersion],()=>{if(!applying.value){clearCompare();lastApplied.value='';pickDefaultOntology()}})
</script>
<template><div class="project-home">
<section class="card">
  <div class="panelhead"><div><h2>{{hasReference?'管理引用版本':'绑定本体版本'}}</h2>
  <p class="muted">先看变化，再决定是否切换项目草稿；已发布项目快照保持不变，切换也不会自动发布。</p></div></div>
  <div v-if="hasReference" class="readonly-line">当前固定引用：{{ontoName(current.ontology)}} {{current.version}}</div>
  <p v-else class="fill-hint">尚未绑定本体。只有发布过的版本可以被项目引用；也可以先到「数据连接」配置连接。</p>
  <div v-if="loadError" class="inline-error">{{loadError}} <button @click="loadAllVersions().then(pickDefaultOntology)">重试加载</button></div>
  <p v-else-if="versionsBusy" class="muted">正在读取各本体的已发布版本…</p>
  <template v-else-if="!eligibleOntologies.length">
  <div class="empty-state">
    <div class="empty-state-ico">◇</div>
    <p>当前还没有任何已发布的本体版本。先到「本体建设 → 校验与发布」发布一个版本。</p>
    <button @click="emit('navigate','p-home')">返回项目概览</button>
  </div>
  </template>
  <template v-else>
  <div class="form-grid">
    <label>本体<AppSelect :model-value="selectedOntology" @update:model-value="setOntology" aria-label="选择本体" :options="eligibleOntologies" :disabled="applying" placeholder="选择要引用的本体"/></label>
    <label>目标版本<AppSelect v-model="selectedVersion" aria-label="选择目标版本" :options="versionOptions" :disabled="!selectedOntology||applying" placeholder="先选择本体"/></label>
  </div>
  <p class="field-help">选择目标版本后自动比较；点击“确认切换”才保存项目引用。</p>
  <div class="tools compare-foot">
    <button class="primary" :disabled="busy||applying||!confirmed" @click="confirmApply">{{applying?'正在保存引用…':busy?'正在比较版本…':`确认切换到 ${selectedVersion||'目标版本'}`}}</button>
    <button v-if="canCompare" :disabled="busy||applying" @click="runCompare">重新比较</button>
    <button :disabled="applying" @click="emit('navigate','p-home')">返回项目概览</button>
  </div>
  <p v-if="!canCompare&&!applied&&!applying" class="field-help">此版本已是当前引用，请选择其他已发布版本。</p>
  <p v-if="message" class="inline-error" role="status">{{message}}</p>
  <div v-if="applied" class="inline-success" role="status">已保存：项目现在引用 {{ontoName(current.ontology)}} {{current.version}}。已发布项目快照不变。
    <button class="row-link" @click="emit('navigate','binding')">查看更新后的对象与属性 →</button>
  </div>
  <template v-if="report">
    <div v-if="report.kind==='first'" class="sample-panel">
      <p><strong>首次绑定：</strong>绑定后即可在对象映射中选择要启用的对象；属性取值与链接映射随后逐项配置。</p>
    </div>
    <div v-else-if="report.kind==='cross'" class="sample-panel">
      <p><strong>切换到其他本体（{{ontoName(selectedOntology)}}）：</strong>服务端升级预检只比较同一本体的两个版本，不提供跨本体差异清单。</p>
      <p class="muted">切换后对象绑定、属性来源与链接映射将按新本体检核；项目校验会列出需要重新处理的配置项。此操作仍只修改项目草稿。</p>
    </div>
    <div v-else class="sample-panel">
      <p><strong>目标：</strong>{{ontoName(selectedOntology)}} {{selectedVersion}}　<strong>分类：</strong>{{changeTypePill[report.data.classification]||report.data.classification}}　<strong>受影响配置：</strong>{{report.data.impacts.length}} 项（阻断 {{report.data.blocking.length}}）</p>
      <div v-if="report.data.impacts.length" class="scroll"><table><thead><tr><th style="width:120px">范围</th><th>影响</th><th style="width:90px">级别</th></tr></thead>
      <tbody><tr v-for="(i,idx) in report.data.impacts" :key="idx"><td>{{areaLabels[i.area]||i.area}}</td><td>{{i.text}}</td><td>{{severityLabel(i.severity)}}</td></tr></tbody></table></div>
      <p v-else class="muted">未发现受当前项目配置影响的差异。</p>
      <details v-if="report.data.reasons?.length" class="reason-details"><summary>分类依据（{{report.data.reasons.length}}）</summary>
        <p v-for="(r,i) in report.data.reasons" :key="i" class="muted">{{severityLabel(r.severity)}} · {{r.text}}<template v-if="r.id">（{{r.id}}）</template></p>
      </details>
      <p class="muted">阻断条目表示该处配置在新版本下会失效；未处理条目也会在项目校验中标记。取消升级不影响已保存引用。</p>
    </div>
  </template>
  </template>
</section>
</div></template>

<style scoped>
.readonly-line{padding:9px 12px;background:var(--bg);border:1px solid var(--line);border-radius:7px;overflow-wrap:anywhere;font-size:13px;color:var(--ink-2);margin-bottom:16px}
.compare-foot{border-top:1px solid var(--line);margin-top:18px;padding-top:14px}
.reason-details{border-top:1px solid var(--line);margin-top:12px;padding-top:10px}
.reason-details summary{cursor:pointer;font-size:12px;color:var(--muted)}
.reason-details p{margin:6px 0}
</style>
