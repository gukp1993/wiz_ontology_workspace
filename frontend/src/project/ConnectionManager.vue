<script setup lang="ts">
import {computed,inject,nextTick,onBeforeUnmount,ref,watch} from 'vue'
import AppSelect from '../shared/AppSelect.vue'
import type {FormGuardAPI,FormGuardInstance,FormSaveAPI} from '../app/formGuard'
import { connectionTest, connectionSecret, catalogRefresh } from './api'
const props=defineProps<{projectState:any}>()
const emit=defineEmits(['before-change','changed'])
// T00 契约接入：新建/编辑连接进入编辑态时经 form-guard 登记（App 统一离开保护），保存经
// form-save 的 submitForm 统一提交；编辑态内部不再 emit('before-change')/emit('changed')。
// 列表页直接操作（删除连接、刷新表结构目录）不属于局部表单，仍走原 undo+自动保存链路。
const guardApi=inject<FormGuardAPI>('form-guard')!
const formSave=inject<FormSaveAPI>('form-save')!
function before(){emit('before-change')}
function changed(){emit('changed')}
function mutate(fn:()=>void){before();fn();changed()}
const newId=()=>'conn-'+crypto.randomUUID().replaceAll('-','').slice(0,12)
// 列表视图 / 编辑器视图：编辑表单整体替换主内容；draft 与 password 是局部草稿，输入过程不写 projectState
const mode=ref<'list'|'edit'>('list'),editingId=ref(''),focusId=ref('')
const connections=computed(()=>props.projectState?.connections?.connections||[])
const findConn=(id:string)=>connections.value.find((c:any)=>c.id===id)
const isLegacy=(c:any)=>!c.engine
const engineTag=(c:any)=>c.engine==='redis'?'Redis':c.engine==='mysql'?'MySQL':'文件'
const catalogInfo=(c:any)=>(props.projectState?.bindings?.catalogs||{})[c.id]||null
const mysqlConnections=computed(()=>connections.value.filter((c:any)=>!isLegacy(c)&&c.engine==='mysql'))
const addressOf=(c:any)=>isLegacy(c)?String(c.path||''):String(c.host||'')+':'+c.port+' · '+(c.engine==='redis'?'DB '+(c.dbIndex??0):String(c.database||''))
const message=ref(''),messageTone=ref('')
function notify(text:string,tone:string=''){message.value=text;messageTone.value=tone}
// 引用检查：对象绑定的实例来源／补充来源／关联来源（旧 related_sources 继承实例来源连接）、
// 属性来源里的数据库直选表与 Redis 直连（{kind:'database'|'redis',connection:id}），以及计算实现的连接引用
function referencesOf(id:string):string[]{
 const out:string[]=[]
 for(const b of props.projectState.bindings?.object_bindings||[]){
  if(b.connection===id)out.push('对象「'+b.object_type+'」的实例来源')
  for(const s of b.sources||[])if(s.connection===id)out.push('对象「'+b.object_type+'」的补充来源'+(s.name?'「'+s.name+'」':''))
  for(const r of b.related_sources||[])if(r.connection===id)out.push('对象「'+b.object_type+'」的关联来源'+(r.name?'「'+r.name+'」':''))
  for(const [api,v] of Object.entries(b.properties||{})){
   const src=v as any
   if(!src||typeof src!=='object')continue
   if(src.kind==='database'&&src.connection===id)out.push('对象「'+b.object_type+'」的属性「'+api+'」的数据库取值来源')
   if(src.kind==='redis'&&src.connection===id)out.push('对象「'+b.object_type+'」的属性「'+api+'」的 Redis 直连来源')
  }
 }
 for(const i of props.projectState.implementations||[])if(i.connection===id)out.push('计算实现（契约 '+i.contractId+'）')
 return out
}
// --- 编辑器表单 ---------------------------------------------------------------------
const draft=ref<any>({name:'',engine:'mysql',host:'',port:3306,database:'',dbIndex:0,username:'',tls:'none',caPath:''})
const password=ref(''),hasSecret=ref(false),secretBusy=ref(false),catalogBusy=ref('')
const engineOptions=[{value:'mysql',label:'MySQL 连接'},{value:'redis',label:'Redis 连接'}]
const tlsOptions=[{value:'none',label:'不使用 TLS'},{value:'encrypted',label:'TLS 加密（不校验证书）'},{value:'verify',label:'校验证书链'}]
const engineLocked=computed(()=>!!editingId.value&&referencesOf(editingId.value).length>0)
// form-guard：打开表单登记、关闭（保存成功或取消）撤销；isDirty 比较打开时快照，密码输入也算未保存修改
let originalSnapshot=''
function draftChanged():boolean{
 try{
  const o=JSON.parse(originalSnapshot)||{} as any
  return ['name','engine','host','port','database','dbIndex','username','tls','caPath'].some(k=>String(draft.value[k]??'')!==String(o[k]??''))||!!password.value.trim()
 }catch{return !!password.value.trim()}
}
const guard:FormGuardInstance={isDirty:()=>mode.value==='edit'&&draftChanged(),discard:()=>{closeEditor()}}
watch(mode,m=>{m==='edit'?guardApi.register(guard):guardApi.unregister(guard)},{immediate:true})
onBeforeUnmount(()=>guardApi.unregister(guard))
function openEditor(id:string=''){
 const saved=id?findConn(id):null
 editingId.value=id;hasSecret.value=!!saved&&saved.credentialRef==='local-vault';password.value=''
 draft.value={name:saved?.name||'',engine:saved?.engine||'mysql',host:saved?.host||'',port:saved?.port??(saved?.engine==='redis'?6379:3306),database:saved?.database||'',dbIndex:saved?.dbIndex??0,username:saved?.username||'',tls:saved?.tls||'none',caPath:saved?.caPath||''}
 originalSnapshot=JSON.stringify(draft.value)
 testGeneration++;testState.value={status:'idle',message:'未测试；允许先保存未验证的草稿。',category:''}
 focusId.value='';notify('');saveState.value='idle';mode.value='edit'
}
function closeEditor(){
 mode.value='list';editingId.value='';password.value='';saveState.value='idle';notify('')
}
function setEngine(v:string){
 if(engineLocked.value){notify('连接仍被数据来源或计算实现引用，不能改变类型','error');return}
 const old=draft.value.engine
 draft.value.engine=v==='redis'?'redis':'mysql'
 if(Number(draft.value.port)===(old==='redis'?6379:3306))draft.value.port=draft.value.engine==='redis'?6379:3306
 resetTest()
}
function setTls(v:string){draft.value.tls=v;if(v!=='verify')draft.value.caPath='';resetTest()}
// 表单 → 连接对象：端口／DB 索引转数字，caPath 仅 verify 时保留，密码永不进入
function buildConfig(){
 const eng=draft.value.engine==='redis'?'redis':'mysql'
 const out:any={engine:eng,name:String(draft.value.name||'').trim(),host:String(draft.value.host||'').trim(),port:Number(draft.value.port)||(eng==='redis'?6379:3306),username:String(draft.value.username||'').trim(),tls:draft.value.tls||'none'}
 if(eng==='mysql')out.database=String(draft.value.database||'').trim();else out.dbIndex=Number(draft.value.dbIndex)||0
 if(draft.value.tls==='verify')out.caPath=String(draft.value.caPath||'').trim()
 return out
}
// --- 测试状态机：generation 防旧响应覆盖；技术配置变化即失效（仅名称变化不重置） -------
const testState=ref<{status:'idle'|'testing'|'ok'|'fail';message:string;category:string;latencyMs?:number;note?:string}>({status:'idle',message:'',category:''})
let testGeneration=0
function resetTest(){testGeneration++;testState.value={status:'idle',message:'配置已修改，请重新测试',category:''}}
const testText=computed(()=>testState.value.status==='testing'?'正在测试连接…':testState.value.status==='ok'?'连接成功'+(testState.value.latencyMs!=null?'（'+testState.value.latencyMs+' ms'+(testState.value.note?'，'+testState.value.note:'')+'）':'')+'：'+testState.value.message+' · 不代表业务数据接入完成':testState.value.status==='fail'?'连接失败：'+testState.value.message:(testState.value.message||'未测试；允许先保存未验证的草稿。'))
async function runTest(){
 if(testState.value.status==='testing')return
 if(!String(draft.value.host||'').trim()){testState.value={status:'idle',message:'请先填写主机地址',category:''};return}
 const saved=editingId.value?findConn(editingId.value):null
 const useSaved=!password.value&&!!saved  // 表单密码留空且连接已保存 → 用受保护存储的凭据测试
 const generation=++testGeneration
 testState.value={status:'testing',message:'正在测试连接…',category:''}
 try{
  const d=await connectionTest({projectId:props.projectState.projectId,connection:buildConfig(),...(password.value?{password:password.value}:(useSaved?{useSaved:true}:{}))})
  if(generation!==testGeneration)return
  if(d.ok)testState.value={status:'ok',category:'ok',message:d.message,latencyMs:d.latencyMs,note:useSaved?'使用已保存凭据':''}
  else testState.value={status:'fail',category:d.category||'unknown',message:d.message}
 }catch(e){if(generation!==testGeneration)return;testState.value={status:'fail',category:'unknown',message:'无法访问工作台服务：'+(e as Error).message}}
}
// 列表行快速测试：已保存连接，固定 useSaved（使用已保存凭据）；每行保留最近一次结果，generation 防旧响应覆盖
type QuickEntry={status:'testing'|'ok'|'fail';message:string;latencyMs?:number}
const quickResults=ref<Record<string,QuickEntry>>({})
let quickGeneration=0
const quickBusy=computed(()=>Object.values(quickResults.value).some(q=>q.status==='testing'))
async function quickTest(c:any){
 if(quickBusy.value)return
 const generation=++quickGeneration
 quickResults.value[c.id]={status:'testing',message:''}
 try{
  const d=await connectionTest({projectId:props.projectState.projectId,connection:{...c},useSaved:true})
  if(generation!==quickGeneration)return
  quickResults.value[c.id]={status:d.ok?'ok':'fail',message:d.message,latencyMs:d.latencyMs}
 }catch(e){if(generation!==quickGeneration)return;quickResults.value[c.id]={status:'fail',message:'无法访问工作台服务：'+(e as Error).message}}
}
function rowTest(conn:any):{cls:string;text:string}{
 const q=quickResults.value[conn.id]
 if(!q)return{cls:'',text:'未测试'}
 if(q.status==='testing')return{cls:'',text:'测试中…（使用已保存凭据）'}
 if(q.status==='ok')return{cls:'conn-ok',text:'连接成功'+(q.latencyMs!=null?'（'+q.latencyMs+' ms）':'')+'（使用已保存凭据）'+(q.message?'：'+q.message:'')+' · 不代表业务数据接入完成'}
 return{cls:'conn-err',text:'连接失败：'+q.message}
}
// --- 保存 / 删除 ----------------------------------------------------------------------
// 保存直通三态：idle → saving（禁用重复提交）→ 成功返回列表并定位；失败（form-save 返回 !ok）
// 不关表单、保留输入，按钮变「重试保存」。表单保存不自动写密码：凭据持久化只经下方显式「保存密码」。
const saveState=ref<'idle'|'saving'|'fail'>('idle')
async function saveConnection(){
 if(saveState.value==='saving')return
 const name=String(draft.value.name||'').trim()
 if(!name){notify('请填写连接名称（connection name 为必填项）。','error');return}
 if(!String(draft.value.host||'').trim()){notify('请填写主机地址。','error');return}
 if(draft.value.engine==='mysql'&&!String(draft.value.database||'').trim()){notify('MySQL 连接需要填写数据库名称。','error');return}
 if(connections.value.some((c:any)=>!isLegacy(c)&&c.id!==editingId.value&&c.name===name)){notify('连接名称「'+name+'」已在当前项目使用（项目内唯一），请更换名称。','error');return}
 const saved=editingId.value?findConn(editingId.value):null
 if(saved&&saved.engine!==draft.value.engine&&referencesOf(editingId.value).length){notify('连接仍被数据来源或计算实现引用，不能改变类型。','error');return}
 const cfg=buildConfig()
 const unsavedPassword=!!password.value.trim()
 saveState.value='saving';notify('')
 let targetId=''
 const r=await formSave.submitForm('project',()=>{
  const container=props.projectState.connections=props.projectState.connections||{connections:[]}
  const list=container.connections=container.connections||[]
  const target:any=saved||{id:newId(),credentialRef:''}
  Object.assign(target,{name:cfg.name,engine:cfg.engine,host:cfg.host,port:cfg.port,username:cfg.username,tls:cfg.tls})
  if(cfg.engine==='mysql'){target.database=cfg.database;delete target.dbIndex}else{target.dbIndex=cfg.dbIndex;delete target.database}
  if(cfg.tls==='verify')target.caPath=cfg.caPath;else delete target.caPath
  if(!saved)list.push(target)
  targetId=target.id
 })
 if(!r.ok){saveState.value='fail';notify('保存失败：'+r.message+'；表单内容已保留，可点击「重试保存」。','error');return}
 saveState.value='idle'
 editingId.value='';password.value=''
 notify((saved?'已保存连接':'已新建连接')+'「'+cfg.name+'」'+(unsavedPassword?'；表单中填写的密码未写入（保存连接不自动写密码），如需保存请在配置页使用「保存密码」':''),'success')
 void locate(targetId)
}
// 保存成功返回列表并定位：高亮刚保存的行并滚动到可见处
async function locate(id:string){
 focusId.value=id;mode.value='list'
 await nextTick()
 document.querySelector('.conn-row.just-saved')?.scrollIntoView({block:'nearest',behavior:'smooth'})
}
function deleteConnection(c:any){
 if(!c)return
 const refs=referencesOf(c.id)
 if(refs.length){notify('连接仍被以下位置引用，不能删除：'+refs.join('；'),'error');return}
 if(!confirm('删除连接「'+(c.name||c.id)+'」？对象数据来源将不能再用此连接，其表结构目录也会一并清除。'))return
 mutate(()=>{
  const list=props.projectState.connections.connections
  list.splice(list.indexOf(c),1)
  if(props.projectState.bindings?.catalogs)delete props.projectState.bindings.catalogs[c.id]
 })
 delete quickResults.value[c.id]
 if(focusId.value===c.id)focusId.value=''
 notify('已删除连接「'+(c.name||c.id)+'」','success')
}
// --- 受保护凭据：密码只写入本机受保护存储，不进入 projectState 任何字段 ----------------
// 显式「保存密码／清除密码」：先调 /api/connection-secret，成功后经 form-save 提交 credentialRef 标记
async function writeSecret(connectionId:string,secret:string):Promise<{ok:boolean;error:string}>{
 try{
  await connectionSecret({projectId:props.projectState.projectId,connectionId,action:'set',secret})
  return{ok:true,error:''}
 }catch(e){return{ok:false,error:'无法访问工作台服务：'+(e as Error).message}}
}
async function saveSecret(){
 if(!editingId.value){notify('先保存连接才能写入密码','error');return}
 if(!password.value.trim())return  // 空输入不触发任何写操作
 secretBusy.value=true
 try{
  const out=await writeSecret(editingId.value,password.value)
  if(!out.ok){notify(out.error,'error');return}
  hasSecret.value=true;password.value=''
  const saved=findConn(editingId.value)
  if(saved){
   const r=await formSave.submitForm('project',()=>{saved.credentialRef='local-vault'})
   if(!r.ok){notify('密码已写入受保护存储，但项目状态保存失败：'+r.message,'error');return}
  }
  notify('已写入受保护存储（不进入项目文件）','success')
 }finally{secretBusy.value=false}
}
async function clearSecret(){
 if(!editingId.value||!hasSecret.value)return
 if(!confirm('清除已保存的密码？清除后此连接的测试与表结构读取将无法使用已保存凭据。'))return
 secretBusy.value=true
 try{
  await connectionSecret({projectId:props.projectState.projectId,connectionId:editingId.value,action:'clear'})
  hasSecret.value=false
  const saved=findConn(editingId.value)
  if(saved){
   const r=await formSave.submitForm('project',()=>{saved.credentialRef=''})
   if(!r.ok){notify('密码已从受保护存储清除，但项目状态保存失败：'+r.message,'error');return}
  }
  notify('已清除受保护存储中的密码','success')
 }catch(e){notify('无法访问工作台服务：'+(e as Error).message,'error')}finally{secretBusy.value=false}
}
// --- 刷新表结构：用已保存配置读取；失败保留旧目录 -------------------------------------
async function refreshCatalog(c:any){
 if(!c||catalogBusy.value)return
 catalogBusy.value=c.id
 try{
  const d=await catalogRefresh(props.projectState.projectId,c.id)
  if(!d.ok){notify('目录未刷新（'+(d.message||'请求失败')+'）；已保留旧选择','error');return}
  mutate(()=>{props.projectState.bindings.catalogs=props.projectState.bindings.catalogs||{};props.projectState.bindings.catalogs[c.id]={database:d.database,tables:d.tables,refreshedAt:d.refreshedAt}})
  notify('「'+(c.name||c.id)+'」'+d.message+'（'+new Date(d.refreshedAt).toLocaleString()+'）','success')
 }catch(e){notify('目录未刷新（'+(e as Error).message+'）；已保留旧选择','error')}finally{catalogBusy.value=''}
}
function catalogPreview(c:any):string{
 const info=catalogInfo(c)
 if(!info)return''
 const names=(info.tables||[]).map((t:any)=>typeof t==='string'?t:String(t.name||'')).filter(Boolean)
 if(!names.length)return''
 return names.length>8?names.slice(0,8).join('、')+' 等 '+names.length+' 张':names.join('、')
}
</script>
<template><div>
<!-- 列表态：原型 connectionsView —— 行＝名称+引擎 tag+地址摘要+测试状态，操作＝测试/配置/删除；折叠的表结构目录 -->
<section v-if="mode==='list'" class="card">
<div class="panelhead"><div><h2>项目数据连接</h2><p class="muted">连接统一配置，供属性、实例识别和计算实现选用。连接探测是真实网络请求；连接成功不代表业务数据接入完成。</p></div><button class="primary" @click="openEditor()">＋ 新建连接</button></div>
<div class="conn-list">
  <div v-for="conn in connections" :key="conn.id" class="conn-row" :class="{'just-saved':focusId===conn.id}">
    <div class="conn-main">
      <div class="conn-title"><strong>{{conn.name||conn.id}}</strong><span class="conn-tag">{{engineTag(conn)}}</span><span v-if="isLegacy(conn)" class="conn-tag">只读演示</span></div>
      <small>{{addressOf(conn)}}</small>
      <small v-if="isLegacy(conn)">演示数据文件，只读，随演示数据提供。</small>
      <small v-else :class="rowTest(conn).cls">{{rowTest(conn).text}}</small>
      <small v-if="!isLegacy(conn)" class="muted-inline">凭据：{{conn.credentialRef?'已配置（受保护）':'未配置'}}</small>
    </div>
    <div class="tools" v-if="!isLegacy(conn)">
      <button class="row-link" :disabled="quickBusy" @click="quickTest(conn)">{{quickResults[conn.id]?.status==='testing'?'测试中…':'测试'}}</button>
      <button class="row-link" @click="openEditor(conn.id)">配置</button>
      <button class="row-link danger" @click="deleteConnection(conn)">删除</button>
    </div>
    <div class="tools" v-else><span class="muted">随演示数据提供</span></div>
  </div>
  <div v-if="!connections.length" class="empty-state">
    <div class="empty-state-ico">◇</div>
    <p>还没有数据连接。添加第一个连接后，映射页即可下拉选择；也允许先保存未验证的草稿。</p>
    <button class="primary" @click="openEditor()">＋ 新建连接</button>
  </div>
</div>
<details class="conn-catalog"><summary>表结构目录</summary>
  <small class="field-help">按连接缓存表／视图结构。「刷新表结构」使用连接的已保存配置与受保护凭据读取；失败时保留旧目录。目录只存在服务端，不进入项目草稿与发布快照。</small>
  <div v-for="c in mysqlConnections" :key="c.id" class="conn-row">
    <div class="conn-main">
      <strong>{{c.name||c.id}}</strong>
      <small v-if="catalogInfo(c)">已缓存 {{(catalogInfo(c).tables||[]).length}} 张表／视图 · 刷新于 {{new Date(catalogInfo(c).refreshedAt).toLocaleString()}}</small>
      <small v-else>尚未读取目录</small>
      <small v-if="catalogPreview(c)" class="mono">{{catalogPreview(c)}}</small>
    </div>
    <div class="tools"><button class="row-link" :disabled="catalogBusy===c.id" @click="refreshCatalog(c)">{{catalogBusy===c.id?'读取中…':'刷新表结构'}}</button></div>
  </div>
  <p v-if="!mysqlConnections.length" class="muted">MySQL 连接刷新后，目录缓存在此显示。</p>
</details>
<p v-if="message" :class="messageTone==='error'?'inline-error':messageTone==='success'?'inline-success':'muted'" role="status">{{message}}</p>
</section>
<!-- 编辑态：原型 editorView 的 connection 分支 —— 整体替换主内容，← 返回列表，底部保存/取消 -->
<section v-else class="card">
<div class="panelhead"><button type="button" class="back-btn" :disabled="saveState==='saving'" @click="closeEditor()">← 返回连接列表</button><span class="status-pill">{{draft.engine==='redis'?'Redis 连接':'MySQL 连接'}}</span></div>
<h2>{{editingId?'配置数据连接':'新建数据连接'}}</h2>
<p v-if="message&&messageTone==='error'" class="inline-error" role="alert">{{message}}</p>
<form class="form-grid" novalidate @submit.prevent="saveConnection()">
<label>连接名称 *<input :value="draft.name" maxlength="80" placeholder="项目内唯一，例如：业务数据库" @input="draft.name=($event.target as HTMLInputElement).value"></label>
<label>连接类型 *<AppSelect :model-value="draft.engine" aria-label="连接类型" :options="engineOptions" :disabled="engineLocked" @update:model-value="setEngine($event)"/><span v-if="engineLocked" class="field-help">连接仍被数据来源或计算实现引用，不能改变类型。</span></label>
<label>主机 *<input :value="draft.host" placeholder="域名或 IP，例如 127.0.0.1" @input="draft.host=($event.target as HTMLInputElement).value;resetTest()"></label>
<label>端口 *<input type="number" min="1" max="65535" :value="draft.port" @input="draft.port=($event.target as HTMLInputElement).value;resetTest()"></label>
<label v-if="draft.engine==='mysql'">数据库名称 *<input :value="draft.database" placeholder="例如 energy" @input="draft.database=($event.target as HTMLInputElement).value;resetTest()"></label>
<label v-else>DB 索引 *<input type="number" min="0" :value="draft.dbIndex" @input="draft.dbIndex=($event.target as HTMLInputElement).value;resetTest()"></label>
<label>用户名（可选）<input :value="draft.username" autocomplete="off" @input="draft.username=($event.target as HTMLInputElement).value;resetTest()"></label>
<label>密码（可选）<input type="password" :value="password" :placeholder="editingId&&hasSecret?'已配置（输入新值以替换，留空不删除）':'可选'" autocomplete="new-password" @input="password=($event.target as HTMLInputElement).value;resetTest()"><span class="field-help">密码只写入本机受保护存储，不从后端回填，也不进入项目文件；保存连接不会自动写入密码。</span></label>
<label>TLS<AppSelect :model-value="draft.tls" aria-label="TLS 设置" :options="tlsOptions" @update:model-value="setTls($event)"/></label>
<label v-if="draft.tls==='verify'">CA 证书路径 *<input :value="draft.caPath" placeholder="本机 CA 证书文件路径" @input="draft.caPath=($event.target as HTMLInputElement).value;resetTest()"><span class="field-help">仅「校验证书链」时需要。</span></label>
</form>
<div class="tools conn-test-row"><button type="button" :disabled="testState.status==='testing'||saveState==='saving'" @click="runTest()">{{testState.status==='testing'?'测试中…':'测试连接'}}</button></div>
<p :class="testState.status==='ok'?'inline-success':testState.status==='fail'?'inline-error':'muted'" role="status" aria-live="polite">{{testText}}</p>
<p class="fill-hint">测试连接只做探测，不保存表单；测试失败也允许保存未验证的草稿。修改主机、端口、库、用户名、TLS 或密码后，之前的测试结果即失效；仅修改名称不影响。</p>
<details class="conn-secret"><summary>受保护凭据（密码）<span>{{editingId?(hasSecret?'已配置（受保护）':'未配置'):'保存连接后可写入'}}</span></summary>
  <template v-if="editingId">
    <div class="tools"><button type="button" :disabled="secretBusy||saveState==='saving'||!password.trim()" @click="saveSecret()">保存密码到受保护存储</button><button v-if="hasSecret" type="button" class="conn-del" :disabled="secretBusy||saveState==='saving'" @click="clearSecret()">清除已保存密码</button></div>
    <p class="field-help">{{hasSecret?'密码已保存在本机受保护存储；在上方输入新值并点击保存可替换，输入框留空不会误删。':'在上方密码框输入后点击保存；空输入不触发任何写操作，清除需单独确认。'}}</p>
  </template>
  <p v-else class="field-help">先保存连接才能写入密码；新连接保存后仍可随时补写或清除。</p>
</details>
<div class="editor-footer">
  <button type="button" class="primary" :disabled="saveState==='saving'" @click="saveConnection()">{{saveState==='saving'?'保存中…':saveState==='fail'?'重试保存':'保存连接'}}</button>
  <button type="button" :disabled="saveState==='saving'" @click="closeEditor()">取消</button>
  <small>只影响当前项目草稿；保存成功后立即写入并返回列表。</small>
</div>
</section>
</div></template>
<style scoped>
/* 原型 connectionsView / editorView(connection) 的行与页脚布局；视觉基准：分隔线 var(--line)、tag var(--blue-soft)/var(--blue)、成功 var(--ok)、失败 var(--danger) */
.conn-row{display:flex;justify-content:space-between;gap:15px;align-items:center;padding:15px 0;border-bottom:1px solid var(--line);flex-wrap:wrap}
.conn-row:last-child{border-bottom:0}
.conn-row.just-saved{background:var(--blue-soft);border-radius:7px;padding-left:12px;padding-right:12px;box-shadow:inset 3px 0 0 var(--blue)}
.conn-main{min-width:0;flex:1}
.conn-title{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.conn-tag{font-size:12px;border-radius:5px;padding:3px 7px;background:var(--blue-soft);color:var(--blue);white-space:nowrap}
.conn-main small{display:block;margin-top:4px;font-size:12px;color:var(--muted)}
.conn-main small.conn-ok{color:var(--ok)}
.conn-main small.conn-err{color:var(--danger)}
.conn-main small.muted-inline{color:var(--muted)}
.conn-del{color:var(--danger)}
.back-btn{background:transparent;border-color:transparent;color:var(--blue);padding:5px 0}
.back-btn:hover{background:transparent;border-color:transparent;text-decoration:underline}
.conn-test-row{margin-top:6px}
.conn-catalog,.conn-secret{border-top:1px solid var(--line);margin-top:18px;padding-top:14px}
.conn-catalog summary,.conn-secret summary{cursor:pointer;color:var(--muted);font-size:13px}
.conn-secret summary span{margin-left:8px;font-size:12px;color:var(--faint)}
.editor-footer{border-top:1px solid var(--line);margin-top:22px;padding-top:16px;display:flex;gap:9px;align-items:center;flex-wrap:wrap}
.editor-footer small{font-size:12px;color:var(--muted)}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
@media(max-width:620px){.conn-row{align-items:flex-start}}
</style>
