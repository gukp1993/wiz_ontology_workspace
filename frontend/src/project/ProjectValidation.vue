<!-- ─── 校验与发布（项目区 · T08 原型 releaseView 三纵向区块）───
     区块① 检查与问题清单（问题行 + 「去处理 →」精确定位到对象映射/实现/连接/版本引用页）；
     区块② 发布项目配置快照（有阻断问题时说明原因并禁用；两步发布保留）；
     区块③ 已发布版本（真实 published_versions 清单 + 「管理引用的本体版本 →」跳独立页 p-upgrade）。
     协议（任务板 §2 冻结）：
     props：report — POST /api/project-validate 的完整响应（errors/warnings/items）；busy — App 全局忙标志；
            projectState — 当前项目草稿（内存形态，含 projectId）。
     emits：refresh — 请求重新校验（App 调 /api/project-validate 并回填 report）；
            navigate(view, focus?) — 「去处理」按 items 的 kind/id 结构化跳转（可带 type/property/impl）；
            published — 发布成功后发出（App 刷新项目状态与引用数据）；发布 API 调用在本组件内完成。
     发布两步法（同 OntologyRelease）：① inject('commit-now') 落项目草稿；
       ② GET /api/project-state?project= 取服务端最新草稿与 revision（剥离大目录 catalogs 后提交）；
       ③ POST /api/project-publish {state, revision}；422 时展示服务端返回的 error/report。
     发布前检查 form-guard：存在未保存的局部表单时提示先保存或放弃，不静默发布。
     状态含义（四态）：未配置 / 配置有误 / 配置校验通过（未执行验证）/ 已执行验证——
     连接测试成功 ≠ 取值成功；配置校验通过 ≠ 已执行验证。
     「本项目属性使用范围」为固定例外（指令 §3.1 最后一项）：只读范围清单 + 明确的
     “排除功能暂未实现”说明——不做无持久化的假开关，也不因排除跳过任何校验。 -->
<script setup lang="ts">
import {computed,inject,onMounted,ref,watch} from 'vue'
import ReferenceNotice from './ReferenceNotice.vue'
import type {FormGuardAPI} from '../app/formGuard'
import {decodeState} from '../ontology/modelFormat'
import {effectiveProperty} from '../ontology/propertyModel'
import { listProjectReleases, loadProjectStateRaw, projectPost } from './api'
import { versionStateRaw } from '../ontology/api'
const props=defineProps<{report:any;busy:boolean;projectState:any;refState?:any}>()
const emit=defineEmits(['refresh','navigate','published','open-ontology'])
const commitNow=inject<(()=>Promise<void>)|undefined>('commit-now',undefined)
const guardApi=inject<FormGuardAPI|undefined>('form-guard',undefined)
const statusPill:Record<string,string>={unconfigured:'未配置',invalid:'配置有误',valid:'校验通过'}
const statusClass:Record<string,string>={unconfigured:'',invalid:'pill-error',valid:'pill-ok'}
const kindLabels:Record<string,string>={connection:'数据连接',objectBinding:'对象映射',objectSource:'对象来源',propertySource:'属性来源',linkMapping:'链接映射',implementation:'计算实现',reference:'本体引用'}
// 「去处理」跳转映射（任务板 §3）：复合 id（'{ot}.{…}'）按第一个 '.' 拆出对象类型，
// 并尽量带上属性 api 名（focus.property）供定位；类型统一补 mg: 前缀以匹配对象目录。
function fixTarget(item:any):{view:string,focus?:any}|null{
  const id=String(item?.id||'')
  if(item.kind==='connection')return {view:'connections'}
  if(item.kind==='implementation')return {view:'implements',focus:{impl:id}}
  if(item.kind==='reference')return {view:'p-upgrade'}
  if(['objectBinding','objectSource','propertySource','linkMapping'].includes(item.kind)){
    const parts=id.split('.')
    const ot=parts[0]
    if(!ot)return {view:'binding'}
    const type=ot.startsWith('mg:')?ot:'mg:'+ot
    return {view:'binding',focus:{type,...(parts[1]?{property:parts[1]}:{})}}
  }
  return {view:'binding'}
}
function goFix(item:any){const t=fixTarget(item);if(t)emit('navigate',t.view,t.focus)}
// errors 文本行的「去处理」：按 items 结构化清单反查（id/名称出现在错误文本中的第一个条目）
function fixForError(e:string):{view:string,focus?:any}|null{
  const items=(props.report?.items||[]) as any[]
  const hit=items.find(i=>String(e).includes(String(i.id||''))||String(e).includes(String(i.name||'')))
  return hit?fixTarget(hit):null
}
function goFixError(e:string){const t=fixForError(e);if(t)emit('navigate',t.view,t.focus)}
const fixable=(item:any)=>item.status!=='valid'||(item.issues||[]).length
const displayNames=computed(()=>{
  const names:Record<string,string>={}
  for(const n of props.refState?.ontology?.['@graph']||[]){
    const effective=effectiveProperty(n,props.refState.ontology['@graph'])
    const label=effective?.['rdfs:label'];if(!label)continue
    names[n['@id']]=label;names[String(n['@id']).replace(/^mg:/,'')]=label
    if(n['mg:apiName'])names[n['mg:apiName']]=label
  }
  return Object.entries(names).sort((a,b)=>b[0].length-a[0].length)
})
function friendly(text:any){let out=String(text||'');for(const [id,name] of displayNames.value)out=out.split(id).join(name);return out}
const hasErrors=computed(()=>!!(props.report?.errors?.length))
const checkReady=computed(()=>!!props.report&&!props.busy)

// --- 已发布项目版本（自取；发布成功后刷新）---
const published=ref<any[]>([]),publishedError=ref('')
async function loadPublished(){
  if(!props.projectState?.projectId)return
  publishedError.value=''
  try{
    published.value=[...(((await listProjectReleases(props.projectState.projectId)).items||[]) as any[])].reverse() // 最新在前
  }catch(e){publishedError.value=(e as Error).message}
}
onMounted(loadPublished)

// --- 「本项目属性使用范围」只读清单：引用版本属性清单 vs 草稿已配置来源（自取 version-state）---
const scopeGraph=ref<any[]>([]),scopeReady=ref(false),scopeError=ref('')
let scopeToken=0
const bare=(id:any)=>String(id||'').replace(/^mg:/,'')
const propApi=(p:any)=>String(p['mg:apiName']||String(p['@id']).replace(/^mg:/,''))
async function loadScope(){
  const st=props.projectState,token=++scopeToken
  scopeGraph.value=[];scopeError.value=''
  if(!st?.ontologyId||!st.ontologyVersion){scopeReady.value=true;return}
  try{
    const decoded=decodeState((await versionStateRaw(st.ontologyId,st.ontologyVersion)).state)
    if(token===scopeToken){scopeGraph.value=decoded.ontology?.['@graph']||[];scopeReady.value=true}
  }catch(e){if(token===scopeToken){scopeError.value=(e as Error).message;scopeReady.value=true}}
}
watch(()=>[props.projectState?.projectId,props.projectState?.ontologyId,props.projectState?.ontologyVersion],loadScope,{immediate:true})
const scopeRows=computed(()=>{
  if(!scopeReady.value)return []
  return (props.projectState?.bindings?.object_bindings||[]).map((b:any)=>{
    const ot=bare(b.object_type)
    const configuredKeys=new Set(Object.keys(b.properties||{}))
    const propsOfType=scopeGraph.value.filter((n:any)=>n['@type']==='owl:DatatypeProperty'&&bare(n['rdfs:domain']?.['@id'])===ot)
    const configured=Object.keys(b.properties||{}).map(api=>{
      const node=propsOfType.find((n:any)=>propApi(n)===api)
      const eff=node?effectiveProperty(node,scopeGraph.value):null
      return String(eff?.['rdfs:label']||node?.['mg:apiName']||api)
    })
    const missing=propsOfType.filter((n:any)=>!configuredKeys.has(propApi(n))).length
    const label=scopeGraph.value.find((g:any)=>g['@type']==='owl:Class'&&bare(g['@id'])===ot)?.['rdfs:label']||ot
    return {ot,label,configured,missing}
  })
})

// --- 发布区：两步法；服务端发布时会再跑一次正式校验，422 返回 error/report ---
const publishBusy=ref(false),publishError=ref(''),publishReport=ref<any>(null),lastPublish=ref('')
async function doPublish(){
  if(publishBusy.value)return
  publishBusy.value=true;publishError.value='';publishReport.value=null
  try{
    if(guardApi?.hasDirty()){publishError.value='还有打开的编辑表单未保存；请先在对应页面保存或放弃本次修改，再发布。';return}
    if(commitNow)await commitNow() // ① 未落盘修改先持久化
    const pid=String(props.projectState?.projectId||'');if(!pid)throw Error('未选择项目')
    const d=await loadProjectStateRaw(pid) // ② 服务端最新草稿与 revision（catalogs 由 projectPost 统一剥除）
    try{
      const pd=await projectPost('project-publish',{state:d.state,revision:d.revision}) // ③
      lastPublish.value=pd.version||''
    }catch(err:any){
      if(err.status===422){ // 422：服务端发布前校验未通过，error + report
        publishError.value=err.data?.error||err.message||'项目配置校验未通过，不能发布'
        publishReport.value=err.data?.report||null
        return
      }
      throw err
    }
    emit('published')
    await loadPublished()
  }catch(e){publishError.value=(e as Error).message}finally{publishBusy.value=false}
}
</script>
<template><div class="project-home">
<p class="fill-hint"><strong>这里发布的是项目配置。</strong> 当前引用本体 {{projectState.ontologyVersion}}；发布本项目不会更新本体定义或属性列表。</p>
<ReferenceNotice :project-state="projectState" @navigate="emit('navigate',$event)" @open-ontology="emit('open-ontology')"/>
<div class="card"><div class="panelhead"><div><h2>检查项目配置</h2>
<p class="muted">只检查当前启用的对象与属性，不要求实现全部本体。配置校验检查结构完整性：引用版本、对象与属性归属、契约与实现匹配、输入输出绑定。它不能证明自然语言规则已正确执行或输出真实有效。</p>
<p class="muted">配置校验通过 ≠ 已执行验证；执行验证在实例浏览/计算预览中。</p></div>
<div class="tools"><span v-if="report" class="status-pill" :class="report.errors.length?'pill-error':'pill-ok'">{{report.errors.length?report.errors.length+' 个问题':'校验通过'}}</span><button :disabled="busy" @click="emit('refresh')">{{busy?'校验中…':'刷新校验'}}</button></div></div>
<template v-if="report">
<p :class="report.errors.length?'inline-error':'inline-success'">{{report.errors.length?('发现 '+report.errors.length+' 个问题，发布前需全部解决：这些是阻断问题，下方发布已禁用'):'已配置项校验通过（未执行业务数据验证）；未配置项见下方清单'}}</p>
<template v-if="report.errors.length"><h3 class="issue-group">错误 {{report.errors.length}}</h3><div v-for="e in report.errors" :key="e" class="issue-row error"><span>{{friendly(e)}}</span>
<button v-if="fixForError(e)" class="row-link" @click="goFixError(e)">去处理 →</button></div></template>
<template v-if="report.warnings.length"><h3 class="issue-group">警告 {{report.warnings.length}}</h3><div v-for="w in report.warnings" :key="w" class="issue-row warn"><span>{{friendly(w)}}</span></div></template>
<template v-if="report.items?.length">
<h3 class="issue-group">逐项清单</h3>
<div v-for="i in report.items" :key="i.id+i.name" class="issue-row" :class="i.status==='invalid'?'error':i.status==='unconfigured'?'warn':''">
<span class="status-pill" :class="statusClass[i.status]">{{statusPill[i.status]||i.status}}</span>
<span><strong>{{friendly(i.name)}}</strong><small class="muted"> · {{kindLabels[i.kind]||i.kind}}</small><details><summary>技术标识</summary>{{i.id}}</details><br>{{friendly(i.issues?.join('；'))||'—'}}</span>
<button v-if="fixable(i)" class="row-link" @click="goFix(i)">去处理 →</button></div>
</template>
<div class="sample-panel">
<h3>状态含义与边界</h3>
<p><strong>未配置</strong>：该配置项尚未登记——先点「去处理」补齐。</p>
<p><strong>配置有误</strong>：已配置但引用或结构有问题——「去处理」直接进入对应表单定位修改。</p>
<p><strong>配置校验通过</strong>：结构完整、引用有效——只代表配置正确，不代表数据真的能取到。</p>
<p><strong>已执行验证</strong>：运行测试输入核对实际输出——需要执行器，首期未包含。</p>
<p class="muted">因此：连接测试成功 ≠ 取值成功；配置校验通过 ≠ 已执行验证。发布的是配置快照，不等于上线或可运行。</p>
</div>
</template>
<p v-else class="muted">尚未执行校验。点击“刷新校验”获取当前配置状态。</p>
<details class="scope-details"><summary>本项目属性使用范围</summary>
<p class="muted">只读范围清单：逐对象列出已配置取值的属性；未配置数 = 引用版本中该对象尚未配置来源的属性数。</p>
<div class="sample-panel">
<p><strong>属性排除功能暂未实现。</strong>本工作台当前只有只读范围清单，尚无对应的排除持久化协议；因此此处不提供排除开关（避免保存后丢失的假设置），也不会因为排除而跳过任何校验。如需暂不使用某属性，保持其来源未配置即可——校验会如实提示“未配置”。新增排除语义需单独设计后再开放。</p>
</div>
<p v-if="scopeError" class="inline-error">{{scopeError}}</p>
<p v-else-if="!scopeReady" class="muted">清单加载中…</p>
<template v-else-if="scopeRows.length">
<div v-for="row in scopeRows" :key="row.ot" class="scope-row"><div><strong>{{row.label}}</strong><small class="muted">已配置 {{row.configured.length}} 项<template v-if="row.configured.length">：{{row.configured.join('、')}}</template></small></div><span class="status-pill" :class="row.missing?'':'pill-ok'">{{row.missing?('未配置 '+row.missing+' 项'):'属性已全部配置'}}</span></div>
</template>
<p v-else class="muted">还没有启用的对象映射。到「对象映射」启用并配置对象。</p>
</details></div>
<div class="card"><div class="panelhead"><div><h2>发布项目配置快照</h2>
<p class="muted">保存可追溯的配置版本，发布不代表已运行。发布先生成已保存的完整项目修订（未落盘修改会先自动保存），再写入不可变配置快照；引用的本体发布版本随之固定在快照中。</p></div>
<div class="tools"><span v-if="hasErrors" class="muted">先处理上方问题，再发布当前草稿。</span><button class="primary" :disabled="publishBusy||!checkReady||hasErrors" @click="doPublish">{{publishBusy?'发布中…':'发布项目配置'}}</button></div></div>
<p v-if="lastPublish" class="inline-success">已发布 {{lastPublish}}。项目引用的本体版本与配置已固定在快照中。</p>
<p v-if="publishError" class="inline-error">{{publishError}}</p>
<template v-if="publishReport">
<p class="inline-error">服务端校验发现 {{publishReport.errors?.length||0}} 个问题：</p>
<div v-for="e in publishReport.errors" :key="e" class="issue-row error"><span>{{friendly(e)}}</span></div>
<template v-if="publishReport.items?.length">
<div v-for="i in publishReport.items" :key="'pub-'+i.id+i.name" class="issue-row" :class="i.status==='invalid'?'error':'warn'">
<span class="status-pill" :class="statusClass[i.status]">{{statusPill[i.status]||i.status}}</span>
<span><strong>{{friendly(i.name)}}</strong><small class="muted"> · {{kindLabels[i.kind]||i.kind}}</small><details><summary>技术标识</summary>{{i.id}}</details><br>{{friendly(i.issues?.join('；'))||'—'}}</span>
<button v-if="fixable(i)" class="row-link" @click="goFix(i)">去处理 →</button></div>
</template>
</template></div>
<div class="card"><div class="panelhead"><div><h2>已发布版本</h2><p class="muted">发布后的项目配置快照不可变；引用的本体版本随之固定。</p></div></div>
<p v-if="publishedError" class="inline-error">{{publishedError}}</p>
<template v-if="published.length">
<div v-for="v in published" :key="v.version" class="version-card">
<div class="version-head"><strong>{{v.version}}</strong><span class="status-pill">引用本体 {{v.ontologyVersion||'—'}}</span></div>
<small>发布时间 {{String(v.createdAt||'').slice(0,19).replace('T',' ')}}<template v-if="v.projectId"> · {{v.projectId}}</template></small>
</div>
</template>
<p v-else-if="!publishedError" class="empty">暂无发布版本。配置校验通过后，在上方发布第一个项目版本。</p>
<div class="release-foot"><button @click="emit('navigate','p-upgrade')">管理引用的本体版本 →</button></div></div>
</div></template>

<style scoped>
.issue-group{margin:16px 0 6px;font-size:13px;font-weight:650;color:var(--muted)}
.issue-row .row-link{flex:none;margin-left:auto;white-space:nowrap}
.scope-details{margin-top:16px;border-top:1px solid var(--line);padding-top:12px}
.scope-details summary{cursor:pointer;font-size:13px;color:var(--muted)}
.scope-row{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;border-bottom:1px solid var(--line);padding:10px 0}
.scope-row:last-child{border-bottom:0}
.scope-row small{display:block;margin-top:2px}
.release-foot{border-top:1px solid var(--line);margin-top:16px;padding-top:14px}
</style>
