<!-- ─── 校验与发布（本体区 · T08 原型 releaseView 三纵向区块）───
     区块① 检查与问题清单（定义校验 + 发布预检；每行问题带「去处理 →」精确定位：
            经 emit navigate(view, {type|property|contract}) 跳对象建模/契约页并 focus）；
     区块② 发布本体版本（变更说明 + 变更类型（publish-check 服务端分类驱动，破坏性禁兼容）
            + 业务验收记录 +「确认发布」；有阻断校验问题时禁用并说明原因）；
     区块③ 历史版本（真实版本清单 manifest 信息 + 恢复快照；仅在真实成功后刷新）。
     协议（任务板 §2 冻结）：
     props：state — 当前本体草稿（JSON-LD 内存形态，含 workspaceId）。
     emits：before-change / changed — 预留（本组件不改内存）；published — 发布或恢复快照成功后发出，
            App 收到后刷新版本相关数据；navigate(view, focus?) — 问题定位（App 需挂 @navigate="navigate"）；
            发布表单与 POST /api/publish 完全在本组件内完成。
     发布/恢复前检查 form-guard：存在未保存的局部表单时提示先保存或放弃，不静默发布。
     页面数据自取：POST /api/validate、POST /api/publish-check（requestBody 编码，只读、不走保存队列）、
     GET /api/versions?ontology=、GET /api/releases?ontology=（挂载即加载）。
     发布两步法（保证“发布前必须有已保存的完整修订”）：
       ① inject('commit-now')（App provide）把当前工作副本落草稿；
       ② GET /api/state?ontology= 取服务端最新草稿与 revision（响应中的 state 已是 schema 形态）；
       ③ POST /api/publish {state, revision, changeType, changeNote, reviewer}——state 原样提交，不再二次 encode。
     恢复快照：POST /api/restore {state, revision, release}（同样取服务端草稿+revision）；
     成功后 emit('published') 并整页刷新——App 的 Saver 无法从外部注入恢复后的草稿，整页刷新是
     让工作副本与 revision 跟进的唯一方式（原 App.vue 的 restoreRelease 同样 reload）。 -->
<script setup lang="ts">
import {computed,inject,onMounted,ref} from 'vue'
import { appConfirm } from '../shared/appConfirm'
import AppSelect from '../shared/AppSelect.vue'
import AppError from '../shared/AppError.vue'
import { isOutcomeUnknown } from '../app/http'
import type {FormGuardAPI} from '../app/formGuard'
import {requestBody,decodeState} from './modelFormat'
import {effectiveProperty} from './propertyModel'
import {validationGroups} from './validationPresentation'
import { listVersions, listReleases, loadStateRaw, validateOntology, publishCheck, publishServerState, restoreServerState, versionStateRaw } from './api'
const props=defineProps<{state:any}>()
const emit=defineEmits(['before-change','changed','published','navigate'])
const commitNow=inject<(()=>Promise<void>)|undefined>('commit-now',undefined)
const guardApi=inject<FormGuardAPI|undefined>('form-guard',undefined)
const ontologyId=computed(()=>String(props.state?.workspaceId||''))
const graph=computed<any[]>(()=>props.state?.ontology?.['@graph']||[])

// --- 卡1：定义校验 errors + 发布预检 suggested/reasons（severity 徽标）---
const checking=ref(false),draftErrors=ref<string[]>([]),checkError=ref('')
const precheck=ref<any>(null),previousGraph=ref<any[]>([])
const errorGroups=computed(()=>validationGroups(draftErrors.value,props.state))
async function runChecks(){
  if(checking.value)return
  checking.value=true;checkError.value=''
  try{
    draftErrors.value=(await validateOntology({state:props.state})).errors||[]
    precheck.value=await publishCheck({state:props.state})
    previousGraph.value=precheck.value.latest?decodeState((await versionStateRaw(ontologyId.value,precheck.value.latest)).state).ontology['@graph']:[]
  }catch(e){checkError.value=(e as Error).message}finally{checking.value=false}
}
// 稳定 id → 显示名替换（纯展示；校验定位不依赖错误文字解析）
const nameBy=computed(()=>{const m=new Map<string,string>()
  for(const g of [previousGraph.value,graph.value])for(const n of g){const label=effectiveProperty(n,g)?.['rdfs:label'];if(n['@id']&&label)m.set(n['@id'],label)}
  for(const area of ['functions','actions','interfaces','businessRules'])for(const [i,n] of (props.state?.workflow?.[area]||[]).entries())m.set(n.id,n.name||'未命名定义 '+(i+1))
  return m})
function friendly(text:string){let out=String(text);for(const [id,name] of [...nameBy.value].sort((a,b)=>b[0].length-a[0].length))if(name)out=out.split(id).join('「'+name+'」');return out}
// --- 「去处理」定位：错误文本/预检 reason 里出现真实稳定 id 时，映射到对应页并 focus ---
const nodeIndex=computed(()=>{const m=new Map<string,any>()
  for(const n of [...graph.value,...(props.state?.workflow?.functions||[]),...(props.state?.workflow?.actions||[]),...(props.state?.workflow?.interfaces||[]),...(props.state?.workflow?.businessRules||[])]){const id=n['@id']||n.id;if(id)m.set(String(id),n)}
  return m})
function targetOfNode(node:any):{view:string,focus?:any}|null{
  const id=String(node['@id']||node.id||'')
  if(node['@type']==='owl:DatatypeProperty'){const dom=node['rdfs:domain']?.['@id'];return {view:'objects',focus:{...(dom?{type:dom}:{}),property:id}}}
  if(node['@type']==='owl:Class'||node['@type']==='owl:ObjectProperty')return {view:'objects',focus:{type:id}}
  if(node['@type']==='mg:SharedProperty')return {view:'library'}
  if(node['@type']==='mg:ValueType')return {view:'valuetypes'}
  if((props.state?.workflow?.actions||[]).some((n:any)=>n.id===id))return {view:'actions',focus:{definition:id}}
  if((props.state?.workflow?.businessRules||[]).some((n:any)=>n.id===id))return {view:'rules',focus:{definition:id}}
  if((props.state?.workflow?.interfaces||[]).some((n:any)=>n.id===id))return {view:'interfaces',focus:{definition:id}}
  if(node.guide_version)return {view:'contracts',focus:{contract:id}}
  return null}
// 文本定位：取文本中出现的最长稳定 id（校验文案引用的就是这些 id），再按节点类型映射
function locate(text:string):{view:string,focus?:any}|null{
  const ids=[...nodeIndex.value.keys()].filter(id=>id.length>3&&text.includes(id)).sort((a,b)=>b.length-a.length)
  for(const id of ids){const t=targetOfNode(nodeIndex.value.get(id));if(t)return t}
  return null}
function goLocate(text:string){const t=locate(text);if(t)emit('navigate',t.view,t.focus)}
// 预检 reason 定位：area + id 结构化映射（id 为空时不给按钮，如实无可定位目标）
function fixReason(r:any):{view:string,focus?:any}|null{
  const id=String(r?.id||'');if(!id)return null
  if(r.area==='objectTypes'||r.area==='linkTypes')return {view:'objects',focus:{type:id}}
  if(r.area==='properties'||r.area==='sharedProperties'){const node=nodeIndex.value.get(id);if(!node)return null;if(r.area==='sharedProperties')return {view:'library'};const dom=node['rdfs:domain']?.['@id'];return {view:'objects',focus:{...(dom?{type:dom}:{}),property:id}}}
  if(r.area==='valueTypes')return {view:'valuetypes'}
  if(r.area==='contracts')return {view:'contracts',focus:{contract:id}}
  return locate(r.text||'')}
function goFixReason(r:any){const t=fixReason(r);if(t)emit('navigate',t.view,t.focus)}
// 发布/恢复前的表单守卫：有未保存的局部表单时阻止，不静默发布旧草稿
function guardBlocked():boolean{
  if(guardApi?.hasDirty()){publishError.value='还有打开的编辑表单未保存；请先在对应页面保存或放弃本次修改，再发布。';return true}
  return false}
const severityClass:Record<string,string>={breaking:'error',pending:'warn',compatible:'info',initial:'info'}
const severityLabel:Record<string,string>={breaking:'破坏性',pending:'待人工确认',compatible:'兼容',initial:'初始'}
const pill=(sev:string)=>sev==='breaking'?'pill-error':(sev==='compatible'||sev==='initial')?'pill-ok':''
// 预检问题分段展示：错误（破坏性）/ 警告（待人工确认）两段各带计数标题；兼容/初始等非问题项归入「变更提示」
const reasonSections=computed(()=>{
  const list=(precheck.value?.reasons||[]) as any[]
  const by=(sev:string)=>list.filter((r:any)=>severityClass[r.severity]===sev)
  return [
    {key:'err',title:'错误',muted:false,rows:by('error')},
    {key:'warn',title:'警告',muted:false,rows:by('warn')},
    {key:'info',title:'变更提示',muted:true,rows:list.filter((r:any)=>severityClass[r.severity]!=='error'&&severityClass[r.severity]!=='warn')},
  ]
})
const changeLabels:Record<string,string>={initial:'初始版本',compatible:'兼容变更',breaking:'破坏性变更',pending:'待人工确认'}

// --- 卡3：版本注册表 + 历史快照（zip，可恢复；快照收进底部折叠）---
const versions=ref<any[]>([]),snapshots=ref<string[]>([]),versionError=ref('')
async function loadLists(){
  versionError.value=''
  try{
    const d=await listVersions(ontologyId.value)
    versions.value=[...(d.items||[])].reverse()
  }catch(e){versionError.value=(e as Error).message}
  try{
    snapshots.value=await listReleases(ontologyId.value)
  }catch(e){versionError.value=versionError.value||((e as Error).message)}
}
onMounted(()=>{runChecks();loadLists()})

// --- 卡2：发布表单（内联，复刻原对话框规则）——兼容候选按 canCompatible 决定可选；破坏性时仅破坏性可选；
//     存在待人工确认变更时必须显式选择变更类型。 ---
const publishing=ref(false)
const changeType=ref(''),changeNote=ref(''),reviewer=ref('')
const publishErrors=ref<string[]>([]),publishError=ref(''),publishReasons=ref<string[]>([]),publishedInfo=ref<any>(null)
// G2：结果未知（超时/网络中断）时不显示成功也不断言未写入；用现有版本清单核对
const publishUnknown=ref(false),publishCheckNote=ref(''),publishBaseline=ref<string[]>([]),restoreError=ref('')
const suggested=computed(()=>String(precheck.value?.suggested||''))
const canCompatible=computed(()=>precheck.value?!!precheck.value.canCompatible:suggested.value!=='breaking')
const changeTypeOptions=computed(()=>[
  {value:'',label:'按预检建议 · '+(changeLabels[suggested.value]||'自动分类'),disabled:suggested.value==='pending'},
  {value:'compatible',label:'兼容变更 · 小版本号递增',disabled:!canCompatible.value},
  {value:'breaking',label:'破坏性变更 · 大版本号递增'},
])
async function doPublish(){
  if(publishing.value)return
  if(checking.value||checkError.value||!precheck.value||draftErrors.value.length)return // 阻断：草稿校验问题未清空
  publishing.value=true;publishErrors.value=[];publishError.value='';publishReasons.value=[];publishUnknown.value=false;publishCheckNote.value=''
  publishBaseline.value=versions.value.map((v:any)=>String(v.version||''))
  try{
    if(guardBlocked())return // 有未保存表单：不静默发布
    if(commitNow)await commitNow() // ① 未落盘修改先持久化，不借发布偷偷提交表单
    const d=await loadStateRaw(ontologyId.value) // ② 服务端最新草稿与 revision
    try{
      // ③ state 已是 schema 形态，原样提交（publishServerState 不做 requestBody 编码）
      publishedInfo.value=await publishServerState({state:d.state,revision:d.revision,changeType:changeType.value,changeNote:changeNote.value.trim(),reviewer:reviewer.value.trim()})
    }catch(err:any){
      if(err.status===422){ // 422 带结构化错误体：errors[]（校验）或 error+reasons（变更类型）
        if(err.data?.errors)publishErrors.value=err.data.errors
        else{publishError.value=err.data?.error||err.message||'不能按所选变更类型发布';publishReasons.value=err.data?.reasons||[]}
        return
      }
      throw err
    }
    changeType.value='';changeNote.value='';reviewer.value=''
    emit('published')
    await loadLists();await runChecks()
  }catch(e:any){
    publishUnknown.value=isOutcomeUnknown(e)
    publishError.value=publishUnknown.value?'未收到发布结果，暂不能确认是否成功。':((e as Error).message||'发布失败')
  }finally{publishing.value=false}
}
// 核对是否已产生新版本：只读版本清单，不覆盖草稿、不自动重发
async function verifyPublished(){
  if(publishing.value)return
  const before=publishBaseline.value
  await loadLists()
  const now=versions.value.map((v:any)=>String(v.version||''))
  const added=now.filter(v=>v&&!before.includes(v))
  publishCheckNote.value=added.length
    ? '服务端已有新版本 '+added.join('、')+'：本次发布很可能已经成功，请勿重复发布。'
    : (versionError.value?'暂时无法读取版本清单：'+versionError.value:'服务端未发现新版本：本次发布很可能没有写入，可修正后重新发布。')
  if(added.length)publishUnknown.value=false
}

// --- 历史快照恢复：confirm 后执行；成功 emit('published') 并整页刷新（见文件头说明）---
const restoring=ref('')
async function restore(name:string){
  if(restoring.value)return
  if(!(await appConfirm({ message: '将此快照恢复为当前本体的草稿？现有已保存草稿会自动备份，其他本体不受影响。' })))return
  restoring.value=name;restoreError.value=''
  try{
    if(guardApi?.hasDirty()){restoreError.value='还有打开的编辑表单未保存；请先保存或放弃本次修改，再恢复快照。';return}
    if(commitNow)await commitNow() // 先落当前草稿，保证恢复请求携带的 revision 与服务端一致
    const d=await loadStateRaw(ontologyId.value)
    await restoreServerState({state:d.state,revision:d.revision,release:name})
    emit('published')
    location.reload()
  }catch(e){restoreError.value='恢复快照未完成：'+((e as Error).message||'未知原因')+'。当前草稿未被改动，可重试或先核对版本清单。'}finally{restoring.value=''}
}
</script>
<template>
<div class="project-home">
<p class="fill-hint"><strong>这里发布的是本体定义。</strong> 发布成功后，项目需要更新引用版本才能看到新的对象和属性。</p>
<p class="muted release-note">配置校验通过 ≠ 已执行验证；发布前请确认业务定义完整。</p>
  <div class="card">
    <div class="panelhead"><div><h2>检查本体定义</h2>
      <p class="muted">只检查通用定义，不要求配置数据库。定义校验检查本体草稿的结构完整性；发布预检对比最新发布版本，给出变更分类建议与理由。两项均为只读检查，不会修改草稿。</p></div>
      <div class="tools"><button :disabled="checking" @click="runChecks">{{checking?'校验中…':'重新校验'}}</button></div></div>
    <AppError v-if="checkError" title="未能完成校验" :reason="checkError" hint="这是检查请求本身失败（本地服务或网络问题），不代表定义有问题，也不代表校验通过；同时也未取得发布预检结果。" retry-label="重新校验" @retry="runChecks"/>
    <template v-else>
      <p v-if="!draftErrors.length" class="inline-success">定义校验通过：结构完整。仅覆盖定义结构完整性，未执行业务数据验证。</p>
      <template v-else><p class="inline-error">{{errorGroups.length}} 项定义尚未完成，共 {{draftErrors.length}} 处需要补充。请在下方处理后发布。</p>
        <section v-for="g in errorGroups" :key="g.id" class="definition-issue">
          <div class="panelhead"><strong>{{g.title}}</strong><button v-if="locate(g.raw[0])" @click="goLocate(g.raw[0])">去处理 →</button></div>
          <p v-if="g.object" class="muted">适用对象：{{g.object}}</p>
          <p>需要补充以下内容：</p><ul><li v-for="issue in g.issues" :key="issue">{{issue}}</li></ul>
          <p v-if="g.kind==='动作定义'" class="muted">位置：更多工具 → 动作定义。名称、描述、变更效果、提交条件和权限要求在“业务定义”中填写；验收案例在“验收与试运行”中填写。如果不需要这项动作，可进入后删除该定义。</p>
          <details><summary>技术详情（排查时查看）</summary><p v-for="raw in g.raw" :key="raw">{{raw}}</p></details>
        </section></template>
      <div class="subsection">
        <h3>与上一版本相比有哪些变化</h3>
          <p class="muted">下面是版本变更记录，不是缺填项。删除或修改定义可能影响旧项目，因此需要发布新版本，再由项目决定是否升级。</p>
        <template v-if="precheck">
          <p>相对{{precheck.latest?('最新发布版本 '+precheck.latest):'首次发布'}}，预检建议：<strong>{{changeLabels[precheck.suggested]||precheck.suggested}}</strong>。</p>
          <template v-if="precheck.reasons?.length">
            <template v-for="sec in reasonSections" :key="sec.key">
              <template v-if="sec.rows.length">
                <h4 class="issue-group-title" :class="{ 'issue-group-info': sec.muted }">{{ sec.title }} {{ sec.rows.length }}</h4>
                <div v-for="(r,i) in sec.rows" :key="sec.key + i" class="issue-row" :class="severityClass[r.severity]||'info'">
                  <span class="status-pill" :class="pill(r.severity)">{{severityLabel[r.severity]||r.severity}}</span>
                  <span>{{friendly(r.text)}}<details v-if="r.id"><summary>技术标识</summary>{{r.id}}</details></span>
                  <button v-if="fixReason(r)" class="go-fix" @click="goFixReason(r)">去处理 →</button>
                </div>
              </template>
            </template>
          </template>
          <p v-else class="muted">没有相对上一版本的结构差异。</p>
          <p v-if="precheck.suggested==='pending'" class="property-feedback">存在待人工确认的变更：发布时必须显式选择变更类型。</p>
          <p v-if="!precheck.canCompatible&&precheck.latest" class="property-feedback">检测到引用失效的结构变化，本次发布不能标记为兼容，请按破坏性变更发布。</p>
        </template>
        <p v-else class="muted">预检加载中…</p>
      </div>
    </template>
  </div>
  <div class="card">
    <div class="panelhead"><div><h2>发布本体版本</h2>
      <p class="muted">发布后可被多个项目固定引用；项目主动升级。发布内容 = 服务端最新已保存草稿（点击发布时会先自动保存当前修改）。破坏性变更递增大版本号，兼容变更递增小版本号。</p></div></div>
    <p v-if="publishedInfo" class="inline-success">已发布 {{publishedInfo.version}}（{{changeLabels[publishedInfo.changeType]||publishedInfo.changeType}}）。</p>
    <p v-if="draftErrors.length" class="inline-error">发布已禁用：草稿尚有 {{draftErrors.length}} 项阻断校验问题；请先按上方「去处理」修复。</p>
    <p v-if="suggested==='pending'" class="property-feedback">存在待人工确认的变更，请在下方显式选择变更类型。</p>
    <label>变更说明<textarea v-model="changeNote" placeholder="例如：新增充电桩对象类型与 SOC 时间序列属性"></textarea></label>
    <label>变更类型<AppSelect v-model="changeType" :options="changeTypeOptions" aria-label="变更类型"/></label>
    <p v-if="!canCompatible&&suggested==='breaking'" class="field-help">检测到引用失效的结构变化，本次不能标记为兼容；只能按破坏性变更发布。</p>
    <label>业务验收记录<input v-model="reviewer" placeholder="例如：验收人／结论／日期（选填）"></label>
    <template v-if="publishErrors.length"><p class="inline-error">发布未通过校验，请修复后重试：</p>
      <p v-for="e in publishErrors" :key="e" class="property-feedback">{{friendly(e)}}</p></template>
    <AppError v-if="publishError" :compact="true" :title="publishUnknown?'未收到发布结果，暂不能确认是否成功':'发布未完成'" :reason="publishError" :hint="publishUnknown?'发布请求可能已到达服务端。请先核对下方版本清单是否已产生新版本，再决定是否重新发布——不要重复发布。':'当前草稿不受影响；修正后可在上方重新校验并再次发布。'" retry-label="核对已发布版本" @retry="verifyPublished"/>
    <p v-if="publishCheckNote" :class="publishCheckNote.startsWith('服务端已有')?'inline-warning':'muted'">{{publishCheckNote}}</p>
    <p v-for="r in publishReasons" :key="r" class="property-feedback">{{r}}</p>
    <div class="tools"><button class="primary" :disabled="publishing||checking||!!checkError||!precheck||!!draftErrors.length" @click="doPublish">{{publishing?'发布中…':'发布本体版本'}}</button></div>
  </div>
  <div class="card">
    <div class="panelhead"><div><h2>已发布版本</h2>
      <p class="muted">项目侧主动升级后即固定引用该版本；每个版本都是不可变快照。</p></div></div>
    <p v-if="versionError" class="inline-error">{{versionError}}</p>
    <template v-if="versions.length">
      <div v-for="v in versions" :key="v.version" class="version-card">
        <div class="version-head"><strong>{{v.version}}</strong>
          <span class="status-pill" :class="pill(v.changeType)">{{changeLabels[v.changeType]||v.changeType}}</span></div>
        <small>发布时间 {{String(v.createdAt||'').slice(0,19).replace('T',' ')}}
          <template v-if="v.parentVersion"> · 上一版本 {{v.parentVersion}}</template>
          <template v-if="v.reviewer"> · 验收记录：{{v.reviewer}}</template></small>
        <small v-if="v.changeNote">变更说明：{{v.changeNote}}</small>
        <small v-if="v.reasons?.length">分类理由：<template v-for="(r,i) in (v.reasons as any[])" :key="i">{{r.text}}<template v-if="i<(v.reasons as any[]).length-1">；</template></template></small>
      </div>
    </template>
    <div v-else-if="!versionError" class="empty-state">
      <span class="empty-state-ico">⚑</span>
      <p>尚未发布任何版本。完成检查后，在上方发布第一个版本。</p>
    </div>
    <details class="snapshot-details"><summary>历史快照（{{snapshots.length}}）</summary>
      <p class="muted">每次发布都会留一份完整快照。恢复会把快照写回当前草稿（现有草稿自动备份），不影响已发布版本。</p>
      <AppError v-if="restoreError" :compact="true" title="恢复快照未完成" :reason="restoreError" hint="已发布版本与当前草稿都未被破坏；处理原因后可重试。" retry-label="知道了" @retry="restoreError=''"/>
      <template v-if="snapshots.length">
        <div v-for="s in snapshots" :key="s" class="issue-row"><span>{{s.replace('.zip','')}}</span>
          <button class="go-fix" :disabled="!!restoring" @click="restore(s)">{{restoring===s?'恢复中…':'恢复此快照'}}</button></div>
      </template>
      <p v-else class="muted">暂无历史快照。</p>
    </details>
  </div>
</div>
</template>

<style scoped>
.release-note{margin:-8px 0 16px;font-size:12px}
.issue-group-title{font-size:13px;font-weight:650;color:var(--ink);margin:16px 0 4px}
.issue-group-title:first-of-type{margin-top:8px}
.issue-group-info{color:var(--muted);font-weight:500}
.definition-issue{border:1px solid var(--danger-line);border-left:3px solid var(--danger);border-radius:7px;padding:16px;margin:12px 0;background:var(--danger-soft)}.definition-issue .panelhead{margin-bottom:6px}.definition-issue p{margin:6px 0}.definition-issue li{margin:5px 0}.definition-issue details{font-size:12px;color:var(--muted)}.definition-issue summary{cursor:pointer}
.snapshot-details{margin-top:16px;border-top:1px solid var(--line);padding-top:12px}
.snapshot-details summary{cursor:pointer;font-size:13px;color:var(--muted)}
</style>
