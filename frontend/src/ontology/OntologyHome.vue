<!-- ─── 工作概览（20260919 本体工作概览优化：分状态概览，替代两步清单+建议推断）───
     状态（派生，不新增业务字段）：
     ① 真空本体（图与工作流含历史定义全空，且版本读取成功且无发布）→ 欢迎卡：
        唯一主按钮「创建第一个对象」（直接打开既有新建对象表单，经 navigate focus.create）；
        发布信息未读到成功前不判定为空（不能假设未发布）。
     ② 有内容 → 内容统计（五列，按定义口径）+ 对象入口（最多 5 项）+ 草稿校验 + 版本与引用。
     数据自取（只读，四模块独立 loading/error，不用 0/未发布/无项目替代未知值）：
       GET /api/versions（清单+发布时间）、POST /api/publish-check（草稿 vs 最新发布差异，
       复用发布页同一比较能力；reasons 空=一致，非空=有未发布变更，失败=尚未比较）、
       GET /api/projects?ontology=（实际固定引用版本）、POST /api/validate（显式发起）。
     校验/差异结果绑定本体 ID + 请求时草稿指纹：内容变化即过期（stale），旧响应不覆盖。
     本组件不改内存、不直接保存；发布仍在「校验与发布」页；问题定位复用稳定 ID→页面映射。 -->
<script setup lang="ts">
import {computed,onMounted,nextTick,ref} from 'vue'
import { listVersions, validateOntology, publishCheck } from './api'
import { listProjects } from '../project/api'
import { validationGroups } from './validationPresentation'
import { navIcons } from '../shared/icons'
import { formatDateTimeSec } from '../shared/format'
import OntologyImport from './OntologyImport.vue'
const props=defineProps<{state:any}>()
const emit=defineEmits<{navigate:[view:string,focus?:any];openProject:[id:string]}>()
// Excel 模板下载与导入（20260917 需求既有能力，范围仍为四类定义）；导入后旧校验按指纹自动失效
const importOpen=ref(false)
const templateHref=()=>((import.meta as any).env?.BASE_URL||'/')+'templates/ontology-import-rule-action-v1.xlsx'
function downloadTemplate(){
  const a=document.createElement('a')
  a.href=templateHref(); a.download='本体模型填写模板.xlsx'
  document.body.appendChild(a); a.click(); a.remove()
}
function _onImported(){importOpen.value=false;void checkDraft()}

// ── 当前本体与定义统计（口径：按定义数；引用节点/私有属性不计入五列）──
const ontologyId=computed(()=>String(props.state?.workspaceId||''))
const ontologyName=computed(()=>String(props.state?.workflow?.objective?.name||'当前本体'))
const graph=computed<any[]>(()=>props.state?.ontology?.['@graph']||[])
const objects=computed(()=>graph.value.filter(n=>n['@type']==='owl:Class'))
const links=computed(()=>graph.value.filter(n=>n['@type']==='owl:ObjectProperty'))
const sharedCount=computed(()=>graph.value.filter(n=>n['@type']==='mg:SharedProperty').length)
const workflow=computed<any>(()=>props.state?.workflow||{})
const rulesCount=computed(()=>(workflow.value.businessRules||[]).length)
const actionsCount=computed(()=>(workflow.value.actions||[]).length)
// 真空本体判定（需求 §2）：不是只看对象——图内任何节点（属性/链接/值类型）、工作流各分区
// （含历史 functions/interfaces 与对象关联）、遗留 metrics/rules 都算已有内容。
const hasAnyDefinition=computed(()=>graph.value.length>0
  ||(workflow.value.functions||[]).length>0||(workflow.value.actions||[]).length>0
  ||(workflow.value.interfaces||[]).length>0||(workflow.value.businessRules||[]).length>0
  ||(workflow.value.businessRuleAssociations||[]).length>0||(workflow.value.actionAssociations||[]).length>0
  ||((props.state as any)?.metrics?.metrics||[]).length>0||((props.state as any)?.rules?.rules||[]).length>0)

// ── 版本模块（独立状态）：清单给发布时间；最新版本按版本元数据挑（接口未承诺排序），
//    publish-check 的 latest 与差异比较同基线，取到后优先用 ──
const versionItems=ref<any[]>([]),versionState=ref<'loading'|'ready'|'error'>('loading'),versionError=ref('')
function verNum(v:string){const m=String(v||'').match(/^(\d+)\.(\d+)\.(\d+)$/);return m?[Number(m[1]),Number(m[2]),Number(m[3])]:[-1,-1,-1]}
function newer(a:any,b:any){const x=verNum(a?.version),y=verNum(b?.version)
  for(let i=0;i<3;i++){if(x[i]!==y[i])return x[i]>y[i]}
  return String(a?.createdAt||'')>String(b?.createdAt||'')}
const metaLatest=computed(()=>{const items=versionItems.value;if(!items.length)return '';let best=items[0];for(const it of items)if(newer(it,best))best=it;return String(best?.version||'')})
const diffLatest=ref('')
// 已发布判定：仅版本清单读取成功后有效（失败时走错误分支，不显示「尚未发布」）；
// 优先 publish-check 的 latest（与差异比较同基线），否则按版本元数据挑最大
const published=computed(()=>versionState.value==='ready'&&!!(diffState.value!=='unknown'&&diffLatest.value||metaLatest.value))
const latestVersion=computed(()=>diffState.value!=='unknown'&&diffLatest.value?diffLatest.value:metaLatest.value)
const latestTime=computed(()=>{const hit=versionItems.value.find(v=>String(v.version||'')===latestVersion.value);if(!hit?.createdAt)return ''
  return formatDateTimeSec(String(hit.createdAt))})
async function loadVersions(){
  const oid=ontologyId.value
  versionState.value='loading';versionError.value=''
  try{const d=await listVersions(oid);if(oid!==ontologyId.value)return
    versionItems.value=d.items||[];versionState.value='ready'}
  catch(e){if(oid!==ontologyId.value)return;versionError.value=(e as Error).message;versionState.value='error'}
}
function retryVersions(){void loadVersions();void loadDiff()}

// ── 草稿 vs 最新发布差异（复用发布页 publish-check 同一能力；比较失败=尚未比较，不猜一致）──
const diffState=ref<'loading'|'same'|'changed'|'unknown'>('loading')
async function loadDiff(){
  const oid=ontologyId.value
  diffState.value='loading';diffLatest.value=''
  try{const d=await publishCheck({state:props.state});if(oid!==ontologyId.value)return
    diffLatest.value=String(d.latest||'')
    diffState.value=(d.reasons||[]).length?'changed':'same'}
  catch{if(oid!==ontologyId.value)return;diffState.value='unknown'}
}

// ── 引用项目模块（独立状态）：显示各项目实际固定引用版本；查看项目经 App 真实切换，不自动升级 ──
const usingProjects=ref<any[]>([]),projectsState=ref<'loading'|'ready'|'error'>('loading'),projectsError=ref('')
async function loadProjects(){
  const oid=ontologyId.value
  projectsState.value='loading';projectsError.value='';usingProjects.value=[]
  try{const d=await listProjects(oid);if(oid!==ontologyId.value)return
    usingProjects.value=d.items||[];projectsState.value='ready'}
  catch(e){if(oid!==ontologyId.value)return;projectsError.value=(e as Error).message;projectsState.value='error'}
}

// ── 草稿校验模块（显式发起）：结果绑定本体 ID+请求时内容指纹；编辑/导入后过期，不重复请求期间覆盖 ──
type CheckView='unchecked'|'loading'|'issues'|'passed'|'stale'|'error'
const checkState=ref<CheckView>('unchecked'),draftErrors=ref<string[]>([]),checkError=ref('')
const checkFingerprint=ref(''),checkOid=ref('')
let checkSeq=0
const fingerprint=computed(()=>JSON.stringify(graph.value)+'#'+JSON.stringify(workflow.value))
const checkStale=computed(()=>(checkState.value==='issues'||checkState.value==='passed')
  &&(checkOid.value!==ontologyId.value||checkFingerprint.value!==fingerprint.value))
const displayCheck=computed<CheckView>(()=>checkStale.value?'stale':checkState.value)
async function checkDraft(){
  const s=++checkSeq,oid=ontologyId.value,fp=fingerprint.value
  checkState.value='loading';checkError.value=''
  try{const d=await validateOntology({state:props.state})
    if(s!==checkSeq||oid!==ontologyId.value)return // 旧响应不覆盖（重复发起/本体已变）
    draftErrors.value=d.errors||[]
    checkOid.value=oid;checkFingerprint.value=fp // 记录请求时点指纹：期间内容变化会判过期
    checkState.value=draftErrors.value.length?'issues':'passed'}
  catch(e){if(s!==checkSeq||oid!==ontologyId.value)return;checkError.value=(e as Error).message;checkState.value='error'}
}
const errorGroups=computed(()=>validationGroups(draftErrors.value,props.state))
const shownGroups=computed(()=>errorGroups.value.slice(0,5))
const checkPill=computed(()=>({unchecked:{text:'尚未校验',class:''},loading:{text:'校验中',class:''},
  issues:{text:draftErrors.value.length+' 项待处理',class:'pill-warn'},passed:{text:'本次校验通过',class:'pill-ok'},
  stale:{text:'校验结果已过期',class:'pill-info'},error:{text:'校验失败',class:'pill-warn'}}[displayCheck.value]))
function neutralOf(v:CheckView):{title:string;desc:string}{
  if(v==='passed')return{title:'当前草稿未发现阻断问题',desc:'结果仅对应本次检查的草稿，不代表业务建模已经完整。'}
  if(v==='stale')return{title:'内容已修改，请重新校验',desc:'之前的结果不再代表当前草稿。'}
  if(v==='error')return{title:'暂时无法完成校验',desc:'这是请求失败，不表示草稿存在错误或已经通过。'}
  return{title:'尚未检查当前草稿',desc:'校验会检查定义与引用关系，不会保存或发布本体。'}
}
const checkNeutral=computed(()=>neutralOf(displayCheck.value))

// ── 问题定位：与发布页同一映射（稳定 id → 对应页签 focus；文本里找最长命中 id）──
const nodeIndex=computed(()=>{const m=new Map<string,any>()
  for(const n of [...graph.value,...(workflow.value.functions||[]),...(workflow.value.actions||[]),...(workflow.value.interfaces||[]),...(workflow.value.businessRules||[])]){const id=n['@id']||n.id;if(id)m.set(String(id),n)}
  return m})
function targetOfNode(node:any):{view:string,focus?:any}|null{
  const id=String(node['@id']||node.id||'')
  if(node['@type']==='owl:DatatypeProperty'){const dom=node['rdfs:domain']?.['@id'];return {view:'objects',focus:{...(dom?{type:dom}:{}),property:id}}}
  if(node['@type']==='owl:Class'||node['@type']==='owl:ObjectProperty')return {view:'objects',focus:{type:id}}
  if(node['@type']==='mg:SharedProperty')return {view:'library'}
  if(node['@type']==='mg:ValueType')return {view:'valuetypes'}
  if((workflow.value.actions||[]).some((n:any)=>n.id===id))return {view:'actions',focus:{definition:id}}
  if((workflow.value.businessRules||[]).some((n:any)=>n.id===id))return {view:'rules',focus:{definition:id}}
  if((workflow.value.interfaces||[]).some((n:any)=>n.id===id))return {view:'interfaces',focus:{definition:id}}
  if(node.guide_version)return {view:'contracts',focus:{contract:id}}
  return null}
function locate(text:string):{view:string,focus?:any}|null{
  const ids=[...nodeIndex.value.keys()].filter(id=>id.length>3&&text.includes(id)).sort((a,b)=>b.length-a.length)
  for(const id of ids){const t=targetOfNode(nodeIndex.value.get(id));if(t)return t}
  return null}
function goLocate(text:string){const t=locate(text);if(t)emit('navigate',t.view,t.focus)}

// ── 空态判定收敛：内容为空 且 版本清单读取成功且确无发布 → 欢迎卡；版本读取中→骨架，读取失败→常规概览 ──
const emptyReady=computed(()=>!hasAnyDefinition.value&&versionState.value==='ready'&&!versionItems.value.length)

// ── 五列统计入口（链接无全局维护页：打开只读清单再定位到起点对象链接页签）──
const assets=computed(()=>[
  {key:'objects',name:'对象类型',hint:'对象定义数',count:objects.value.length,icon:navIcons.objects,go:()=>emit('navigate','objects')},
  {key:'shared',name:'共享属性',hint:'共享库中的定义数',count:sharedCount.value,icon:navIcons.library,go:()=>emit('navigate','library')},
  {key:'links',name:'链接类型',hint:'正反方向共一份定义',count:links.value.length,icon:navIcons.binding,go:openLinkList},
  {key:'rules',name:'业务规则',hint:'业务规则定义数',count:rulesCount.value,icon:navIcons.rules,go:()=>emit('navigate','rules')},
  {key:'actions',name:'动作定义',hint:'动作定义数',count:actionsCount.value,icon:navIcons.actions,go:()=>emit('navigate','actions')},
])
const objectEntries=computed(()=>objects.value.slice(0,5).map(o=>({id:String(o['@id']),name:String(o['rdfs:label']||'未命名对象'),def:String(o['rdfs:comment']||'')})))

// 链接只读清单（零条时空清单，不报错）；维护入口在起点对象「链接」页签
const linkListOpen=ref(false),linkCloseBtn=ref<HTMLButtonElement|null>(null)
function openLinkList(){linkListOpen.value=true;void nextTick(()=>linkCloseBtn.value?.focus())}
const CARD_LABELS:Record<string,string>={'one-to-one':'一对一','one-to-many':'一对多','many-to-one':'多对一','many-to-many':'多对多'}
function nameOf(id?:string){if(!id)return '—';const hit:any=objects.value.find(n=>n['@id']===id);return hit?.['rdfs:label']||String(id).replace(/^mg:/,'')}
function gotoLink(l:any){linkListOpen.value=false;const dom=l['rdfs:domain']?.['@id'];emit('navigate','objects',dom?{type:dom,tab:'links'}:{tab:'links'})}

// 差异状态徽标：没有比较结果（读取中/失败）一律「尚未比较」，不能猜一致
const diffPill=computed(()=>diffState.value==='same'?{text:'草稿与最新发布一致',class:'pill-ok'}
  :diffState.value==='changed'?{text:'草稿有未发布变更',class:'pill-info'}:{text:'尚未比较',class:''})

onMounted(()=>{void loadVersions();void loadDiff();void loadProjects()})
</script>
<template>
<OntologyImport v-if="importOpen" :state="state" @close="importOpen=false" @navigate="v=>{importOpen=false;emit('navigate',v)}"/>

<!-- 链接类型只读清单：正反向共一条；去起点对象「链接」页签维护 -->
<div v-if="linkListOpen" class="modal-backdrop" @click.self="linkListOpen=false">
  <section class="modal-card dialog-md" role="dialog" aria-modal="true" aria-label="链接类型清单" @keydown.esc="linkListOpen=false">
    <h2>链接类型清单</h2>
    <p class="field-help">链接正反向共一份定义，共 {{links.length}} 条；在起点对象的「链接」页签维护。</p>
    <div v-if="links.length" class="scroll home-table">
      <table><thead><tr><th>链接</th><th>起点 → 终点</th><th>数量关系</th><th></th></tr></thead>
      <tbody><tr v-for="l in links" :key="l['@id']">
        <td><strong>{{l['rdfs:label']||'未命名链接'}}</strong></td>
        <td>{{nameOf(l['rdfs:domain']?.['@id'])}} → {{nameOf(l['rdfs:range']?.['@id'])}}</td>
        <td>{{CARD_LABELS[String(l['mg:cardinality']||'')]||'—'}}</td>
        <td><button class="row-link" @click="gotoLink(l)">去起点对象 →</button></td>
      </tr></tbody></table>
    </div>
    <p v-else class="muted">当前本体还没有链接类型；在对象建模中为对象添加链接。</p>
    <div class="dialogtools"><button ref="linkCloseBtn" @click="linkListOpen=false">关闭</button></div>
  </section>
</div>

<!-- ① 真空本体：唯一主开始动作 + 导入/模板次要入口（无统计大卡、无项目映射推广） -->
<section v-if="emptyReady" class="card home-welcome">
  <div class="welcome-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path :d="navIcons.objects"/></svg></div>
  <h2>开始构建你的本体</h2>
  <p>从一个业务对象开始，例如储能簇、储能设备；也可以通过 Excel 批量导入已有内容。</p>
  <div class="start-actions">
    <button class="primary" @click="emit('navigate','objects',{create:true})">＋ 创建第一个对象</button>
    <button @click="emit('navigate','build')">从物料生成</button>
    <button @click="importOpen=true">从 Excel 导入</button>
    <button class="text" @click="downloadTemplate">下载 Excel 模板</button>
  </div>
  <div class="example"><span>对象填写示例</span><div><strong>储能簇</strong>
    <p>多个电池模组串联或并联组成的电池单元。<br>创建后，可按需添加属性、链接，并关联业务规则和动作。</p></div></div>
</section>
<p v-if="emptyReady" class="footnote">完成建模并发布版本后，可供具体项目引用。项目中的表、字段和接口在项目映射中配置。</p>

<!-- 版本清单读取中且内容为空：等待空态判定，不抢先显示 0 值统计 -->
<section v-else-if="!hasAnyDefinition&&versionState==='loading'" class="card">
  <div class="skeleton sk-title"></div>
  <div class="skeleton sk-text"></div>
  <div class="skeleton sk-text sk-w80"></div>
  <p class="muted">正在读取本体内容与发布信息…</p>
</section>

<!-- ② 常规概览（含「有资产无对象」提示与版本读取失败的如实展示） -->
<template v-else>
  <div class="pageintro">
    <div><h2>{{ontologyName}}</h2>
    <p class="muted">{{published?'维护当前草稿；已发布版本和项目引用保持独立。':'先整理业务对象和定义，准备好后再校验发布。'}}</p></div>
    <div class="tools home-actions">
      <button @click="importOpen=true">导入 Excel</button>
      <button @click="emit('navigate','settings-transfer',{preselect:{ontologyId:ontologyId}})">导出配置</button>
      <button @click="emit('navigate','build')">从物料生成</button>
      <button v-if="objects.length" class="primary" @click="emit('navigate','objects')">继续建模</button>
      <button v-else class="primary" @click="emit('navigate','objects',{create:true})">创建第一个对象</button>
    </div>
  </div>

  <div v-if="!objects.length&&hasAnyDefinition" class="callout"><div>
    <strong>已有共享属性、链接、规则或动作定义，还没有对象类型</strong>
    <p>可以继续整理资产，也可以创建对象后再引用，已有内容不会丢失。</p></div></div>

  <section class="card">
    <div class="panelhead"><h3>本体内容</h3><span class="muted">按定义统计，不是建设完成度</span></div>
    <div class="asset-grid">
      <button v-for="a in assets" :key="a.key" class="asset" :aria-label="a.name+' '+a.count+' 项'" @click="a.go()">
        <span class="asset-label"><svg class="nav-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path :d="a.icon"/></svg>{{a.name}}</span>
        <strong>{{a.count}}</strong><small>{{a.hint}}</small>
      </button>
    </div>
    <p class="note-line">对象内的属性在对象建模中维护。共享属性引用次数不计入共享定义数；链接、规则和动作按业务需要建设，为零不代表不完整。</p>
  </section>

  <section v-if="objects.length" class="card">
    <div class="panelhead"><h3>对象类型</h3><button class="text" @click="emit('navigate','objects')">查看全部对象 →</button></div>
    <p class="muted">从已有对象继续维护属性、链接、规则和动作；此处显示前 {{objectEntries.length}} 项。</p>
    <div class="scroll home-table">
      <table><thead><tr><th>对象</th><th>业务定义</th><th></th></tr></thead>
      <tbody><tr v-for="o in objectEntries" :key="o.id">
        <td class="object-name">{{o.name}}</td><td class="object-def">{{o.def||'—'}}</td>
        <td><button class="row-link" @click="emit('navigate','objects',{type:o.id})">查看对象 →</button></td>
      </tr></tbody></table>
    </div>
  </section>

  <section class="card" :class="{'home-card-error':displayCheck==='error'}">
    <div class="panelhead"><h3>{{displayCheck==='issues'?'需要处理':'草稿校验'}}</h3>
      <div class="check-tools">
        <span class="pill" :class="checkPill.class">{{checkPill.text}}</span>
        <button v-if="displayCheck==='unchecked'||displayCheck==='passed'||displayCheck==='stale'" class="text" @click="checkDraft">{{displayCheck==='unchecked'?'开始校验':'重新校验'}}</button>
        <button v-if="displayCheck==='issues'" class="text" @click="emit('navigate','o-release')">查看全部 {{draftErrors.length}} 项问题 →</button>
      </div></div>
    <div v-if="displayCheck==='issues'" class="issues">
      <div v-for="g in shownGroups" :key="String(g.id)" class="issue">
        <span class="issue-marker" aria-hidden="true"></span>
        <div class="issue-copy"><strong>{{g.title}}</strong><small>{{g.issues.join('；')}}</small></div>
        <button v-if="locate(g.raw[0])" class="text" @click="goLocate(g.raw[0])">去处理 →</button>
      </div>
    </div>
    <div v-else-if="displayCheck==='loading'" class="check-loading">
      <div class="skeleton sk-w70"></div><div class="skeleton sk-w45"></div>
      <p class="muted">正在检查当前草稿…</p>
    </div>
    <div v-else class="neutral-state">
      <span class="state-icon" aria-hidden="true">{{displayCheck==='passed'?'✓':displayCheck==='error'?'!':'—'}}</span>
      <div><strong>{{checkNeutral.title}}</strong><p>{{checkNeutral.desc}}</p></div>
      <button v-if="displayCheck==='error'" @click="checkDraft">重试校验</button>
    </div>
  </section>

  <section class="card" :class="{'home-card-error':versionState==='error'}">
    <div class="panelhead"><h3>版本与引用</h3>
      <button v-if="versionState==='error'" @click="retryVersions">重新加载</button>
      <button v-else class="text" @click="emit('navigate','o-release')">{{published?'查看发布记录 →':'校验与发布 →'}}</button></div>
    <div v-if="versionState==='loading'" class="check-loading">
      <div class="skeleton sk-w60"></div>
      <p class="muted">正在读取版本与引用项目，不展示未知数据的零值。</p>
    </div>
    <template v-else-if="versionState==='error'">
      <p class="muted">版本信息暂时无法读取：{{versionError}}。读取失败不代表尚未发布，也不代表没有项目引用。</p>
    </template>
    <template v-else-if="!published">
      <p class="muted">尚未发布。发布版本后，项目才能固定引用这份本体定义；发布前请先到「校验与发布」完成检查。</p>
    </template>
    <template v-else>
      <div class="versionline">
        <div><span class="muted">最新发布版本</span><strong class="ver-num">{{latestVersion}}</strong>
        <p>{{latestTime?'发布于 '+latestTime:'发布时间未随版本记录'}}</p></div>
        <span class="pill" :class="diffPill.class">{{diffPill.text}}</span>
      </div>
      <div v-if="diffState==='changed'" class="versionnote changed">当前修改尚未发布，不影响项目正在引用的版本。
        <button class="text inline-link" @click="emit('navigate','o-release')">查看变化 →</button></div>
      <div v-if="projectsState==='loading'" class="check-loading"><div class="skeleton sk-w50"></div></div>
      <p v-else-if="projectsState==='error'" class="muted">引用项目暂时无法读取：{{projectsError}}。
        <button class="text inline-link" @click="loadProjects">重试</button></p>
      <div v-else-if="usingProjects.length" class="scroll home-table">
        <table><thead><tr><th>引用项目</th><th>实际引用版本</th><th></th></tr></thead>
        <tbody><tr v-for="p in usingProjects" :key="String(p.id)">
          <td><strong>{{p.name}}</strong></td><td>{{p.ontologyVersion||'—'}}</td>
          <td><button class="row-link" @click="emit('openProject',p.id)">查看项目 →</button></td>
        </tr></tbody></table>
      </div>
      <p v-else class="muted">暂无项目引用；发布后可在项目区把这份定义交给具体项目。
        <button class="text inline-link" @click="emit('navigate','p-home')">查看项目 →</button></p>
      <p class="note-line">项目使用各自固定引用的版本。查看项目不会自动升级引用。</p>
    </template>
  </section>
</template>
</template>

<style scoped>
/* ── 空态欢迎卡（原型 welcome；复用既有令牌，不新造颜色）── */
.home-welcome{padding:40px 36px 28px;min-height:320px}
.welcome-icon{width:42px;height:42px;display:grid;place-items:center;border:1px solid var(--blue-line);border-radius:10px;background:var(--blue-soft);color:var(--blue);margin-bottom:20px}
.welcome-icon svg{width:23px;height:23px}
.home-welcome h2{font-size:23px;margin-bottom:9px}
.home-welcome>p{color:var(--muted);font-size:14px;max-width:700px}
.start-actions{display:flex;gap:10px;align-items:center;margin:23px 0 28px;flex-wrap:wrap}
.start-actions .text{margin-left:5px;padding:2px 0;font-size:12px}
.example{display:flex;gap:18px;align-items:flex-start;border-top:1px solid var(--line);padding-top:20px;max-width:790px;font-size:13px}
.example>span{color:var(--muted);font-size:12px;white-space:nowrap}
.example strong{font-weight:600;display:block;margin-bottom:3px}
.example p{color:var(--muted);font-size:12px}
.footnote{font-size:12px;color:var(--muted);padding:0 4px}
/* ── 页头与提示 ── */
.pageintro{display:flex;align-items:center;justify-content:space-between;gap:15px;margin-bottom:18px}
.pageintro p{color:var(--muted);font-size:12px;margin-top:5px}
.pageintro h2{margin:0}
.home-actions{align-items:center;flex-wrap:wrap}
.callout{padding:12px 15px;background:var(--blue-soft);border:1px solid var(--blue-line);border-radius:6px;color:var(--blue-ink);font-size:13px;margin-bottom:16px}
.callout p{font-size:12px;color:var(--muted);margin-top:3px}
/* ── 五列统计（窄屏换行；不做完成度）── */
.asset-grid{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:0}
.asset{background:transparent;text-align:left;border:0;border-radius:0;border-right:1px solid var(--line);padding:3px 20px 7px}
.asset:first-child{padding-left:0}
.asset:last-child{border-right:0}
.asset:hover{color:var(--blue);background:var(--paper-2)}
.asset-label{display:flex;gap:7px;align-items:center;color:var(--muted);font-size:12px}
.asset strong{font-size:28px;font-weight:600;display:block;line-height:1.4;margin:8px 0 3px;font-variant-numeric:tabular-nums}
.asset small{display:block;font-size:11px;color:var(--muted)}
.note-line{font-size:12px;color:var(--muted);padding-top:17px;margin-top:16px;border-top:1px solid var(--line)}
/* ── 校验与版本卡 ── */
.pill{font-size:11px;border:1px solid var(--line);background:var(--paper-2);color:var(--muted);padding:2px 8px;border-radius:4px;white-space:nowrap}
.pill.pill-ok{background:var(--ok-soft);border-color:var(--ok-line);color:var(--ok)}
.pill.pill-warn{background:var(--warn-soft);border-color:var(--warn-line);color:var(--warn)}
.pill.pill-info{background:var(--blue-soft);border-color:var(--blue-line);color:var(--blue-ink)}
.home-card-error{border-color:var(--warn-line)}
.issues{border-top:1px solid var(--line)}
.issue{display:flex;align-items:center;gap:14px;padding:15px 0;border-bottom:1px solid var(--line)}
.issue:last-child{border-bottom:0;padding-bottom:0}
.issue-marker{width:7px;height:7px;border-radius:50%;background:var(--danger);flex:none}
.issue-copy{flex:1;min-width:0}
.issue-copy strong{font-weight:500;font-size:13px}
.issue-copy small{display:block;color:var(--muted);font-size:12px;margin-top:4px;overflow-wrap:anywhere}
.issue button{font-size:12px;white-space:nowrap}
.check-tools{display:flex;gap:8px;align-items:center}
.check-loading{padding:5px 0}
.check-loading .skeleton{height:12px;border-radius:var(--r-sm);margin:12px 0}
.neutral-state{display:flex;align-items:center;gap:15px;padding:5px 0}
.neutral-state .state-icon{width:32px;height:32px;background:var(--paper-2);border-radius:50%;display:grid;place-items:center;color:var(--muted);flex:none}
.neutral-state strong{font-size:13px;font-weight:500}
.neutral-state p{font-size:12px;color:var(--muted);margin-top:3px}
.neutral-state button{margin-left:auto;font-size:12px}
.versionline{display:flex;align-items:flex-start;gap:22px;flex-wrap:wrap}
.ver-num{font-size:18px;font-weight:600;margin-left:10px}
.versionline p{font-size:12px;color:var(--muted);margin-top:4px}
.versionnote{padding:11px 14px;border:1px solid var(--line);background:var(--paper-2);border-radius:var(--r-sm);font-size:12px;margin-top:16px;color:var(--muted)}
.versionnote.changed{background:var(--blue-soft);border-color:var(--blue-line);color:var(--blue-ink)}
.inline-link{font-size:12px}
/* ── 表格（对象入口/链接清单/引用项目；局部滚动）── */
.home-table{margin-top:14px}
.home-table table{margin-top:0}
.home-table th:last-child,.home-table td:last-child{text-align:right;width:110px}
.object-name{font-weight:600}
.object-def{color:var(--muted)}
/* ── 响应式（1440/1024/768；620 以下沿全局侧栏适配）── */
@media(max-width:1150px){.asset{padding:3px 12px}.asset strong{font-size:25px}.asset-grid{grid-template-columns:repeat(3,minmax(0,1fr));row-gap:22px}.asset:nth-child(3){border-right:0}.asset:nth-child(4){padding-left:0}}
@media(max-width:850px){main .pageintro{align-items:flex-start;flex-wrap:wrap}.home-welcome{padding:26px 22px}.home-welcome h2{font-size:21px}.asset-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.asset:nth-child(3){border-right:1px solid var(--line);padding-left:0}.asset:nth-child(2n){border-right:0;padding-left:12px}.asset:nth-child(5){padding-left:0}.issue{align-items:flex-start}.neutral-state{flex-wrap:wrap}.neutral-state button{margin-left:47px}.example{gap:12px}.start-actions .text{margin-left:0}}
@media(max-width:620px){.home-welcome{padding:24px 20px}.home-welcome h2{font-size:20px}.home-welcome>p{font-size:13px}.start-actions{gap:8px}.start-actions button{padding:7px 10px;font-size:12px}.example{display:block}.example>span{display:block;margin-bottom:6px}.check-tools{flex-wrap:wrap;justify-content:flex-end}.check-tools .pill{display:none}.asset small{font-size:10px}}
</style>
