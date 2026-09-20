// 实际组件状态测试：不连接业务数据、不写用户草稿。
// node --import ./tests/ts_hooks.mjs tests/project_version.test.mjs
// T23/T25（F08）：比较绑定项目修订与目标版本；比较后项目配置变化 → 确认前先重新比较；
// 409/失败保持原引用；跨本体/首次绑定不虚构差异。
import assert from 'node:assert/strict'
import {createRequire} from 'node:module'
import {readFileSync,mkdtempSync,writeFileSync,rmSync} from 'node:fs'
import {tmpdir} from 'node:os'
import {join,resolve,dirname} from 'node:path'
import {pathToFileURL} from 'node:url'
const require=createRequire(new URL('../frontend/package.json',import.meta.url))
const {parse,compileScript,compileTemplate}=require('@vue/compiler-sfc'),ts=require('typescript')
const {createRenderer,createSSRApp,reactive,h,nextTick}=require('vue'),{renderToString}=require('@vue/server-renderer')
const root=mkdtempSync(join(tmpdir(),'wiz_reference_'))
const filename=resolve('frontend/src/project/ProjectVersion.vue')
const defer=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject}}
const settle=async()=>{await new Promise(r=>setImmediate(r));await nextTick()}
const reports=[]
globalThis.__referenceApi={
 async listVersions(id){return {items:id==='a'?[{version:'1.0.0'},{version:'2.0.0'},{version:'3.0.0'}]:[{version:'7.0.0'}]}},
 // upgradeCheck(state, revision, targetVersion)：revision 是本次比较基于的项目草稿修订
 upgradeCheck(state,revision,targetVersion){const d=defer();reports.push({...d,payload:{state,revision,targetVersion}});return d.promise},
}
globalThis.__referenceSelect={props:['options','modelValue','disabled'],render(){return h('select',{disabled:this.disabled},(this.options||[]).map(o=>h('option',{selected:o.value===this.modelValue},o.label)))}}
let app
try{
 const {descriptor}=parse(readFileSync(filename,'utf8'),{filename}),script=compileScript(descriptor,{id:'reference'})
 const template=compileTemplate({source:descriptor.template.content,filename,id:'reference',ssr:true,ssrCssVars:[],compilerOptions:{bindingMetadata:script.bindings}})
 let code=script.content.replace(/import AppSelect from ['"].*?['"]/,'const AppSelect=globalThis.__referenceSelect').replace(/import \{ listVersions \} from ['"].*?['"]/,'const {listVersions}=globalThis.__referenceApi').replace(/import \{ upgradeCheck \} from ['"].*?['"]/,'const {upgradeCheck}=globalThis.__referenceApi')
 code=code.replace('export default ','const Component=')+'\n'+template.code+'\nComponent.ssrRender=ssrRender;export default Component;'
 code=code.replace(/from (['"])([^'"]+)\1/g,(_,q,s)=>'from '+JSON.stringify(pathToFileURL(s.startsWith('.')?resolve(dirname(filename),s+'.ts'):require.resolve(s)).href))
 const file=join(root,'component.mjs');writeFileSync(file,ts.transpileModule(code,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText)
 const Component=(await import(pathToFileURL(file).href)).default
 const state=reactive({projectId:'p',ontologyId:'a',ontologyVersion:'1.0.0',bindings:{object_bindings:[{object_type:'cluster',table:'t',properties:{}}]}})
 let saving,saveCalls=0,api
 const props=reactive({projectState:state,revision:'r-1',ontologyOptions:[{value:'a',label:'储能'},{value:'b',label:'光伏'}],async applyReference(target){saveCalls++;saving=defer();await saving.promise;state.ontologyId=target.ontology;state.ontologyVersion=target.version}})
 // 空渲染器运行真实 Vue 生命周期/watch，渲染另用同一 setup 状态的实际 SSR 模板。
 const renderer=createRenderer({createElement:()=>({}),createText:()=>({}),createComment:()=>({}),insert(){},remove(){},setText(){},setElementText(){},parentNode(){},nextSibling(){},patchProp(){}})
 const setup=Component.setup;Component.setup=(p,c)=>{api=setup(p,c);return ()=>h('div')}
 app=renderer.createApp(Component,props);app.mount({});await settle()
 const html=()=>renderToString(createSSRApp({render:()=>h({props:Component.props,ssrRender:Component.ssrRender,setup:()=>api},props)}))
 const result={classification:'breaking',impacts:[{area:'ontology',severity:'breaking',text:'测试变更'}],blocking:[],reasons:[]}
 assert.equal(api.selectedVersion.value,'1.0.0');assert.equal(reports.length,0)
 api.selectedVersion.value='2.0.0';await settle();assert.equal(reports.length,1,'选择自动发起比较');assert.equal(api.busy.value,true);assert.equal(saveCalls,0)
 assert.equal(reports[0].payload.revision,'r-1','比较请求必须携带当前项目草稿 revision 作为比较基线')
 assert.equal(reports[0].payload.targetVersion,'2.0.0')
 api.selectedVersion.value='3.0.0';await settle();assert.equal(reports.length,2)
 reports[1].resolve(result);await settle();assert.equal(api.confirmed.value,true)
 reports[0].resolve({...result,classification:'outdated'});await settle();assert.equal(api.report.value.data.classification,'breaking','旧比较响应不能覆盖新版本')
 let page=await html();assert.ok(page.indexOf('确认切换到 3.0.0')<page.indexOf('测试变更'),'确认按钮位于报告之前')
 const failed=api.confirmApply();await settle();assert.equal(api.applying.value,true);assert.equal(api.applied.value,false)
 await api.confirmApply();assert.equal(saveCalls,1,'保存中防重复点击')
 saving.reject(new Error('保存失败'));await failed;assert.equal(state.ontologyVersion,'1.0.0');assert.equal(api.applied.value,false);assert.match(api.message.value,/切换未完成/);assert.equal(api.confirmed.value,true)
 const success=api.confirmApply();await settle();saving.resolve();await success;await settle();assert.equal(api.applied.value,true);assert.equal(state.ontologyVersion,'3.0.0');assert.match(await html(),/已保存：项目现在引用/)
 api.setOntology('b');await settle();assert.equal(api.selectedVersion.value,'7.0.0');assert.equal(api.report.value.kind,'cross');assert.equal(api.applied.value,false)
 api.setOntology('a');api.selectedVersion.value='2.0.0';await settle();reports.at(-1).reject(new Error('连接中断'));await settle();assert.match(api.message.value,/比较失败/);assert.equal(api.confirmed.value,false)
 const retry=api.runCompare();reports.at(-1).resolve(result);await retry;assert.equal(api.confirmed.value,true)
 console.log('通过：选择自动比较、比较携带 revision、旧响应隔离、确认按钮位置、保存等待、防重复、失败不假成功与重试、跨本体版本联动、比较失败可恢复。')

 // ── T23：比较后项目配置变化 → 旧比较结果不可确认，确认前先重新比较 ──
 {
  const before=saveCalls, compareCount=reports.length
  state.bindings.object_bindings[0].table='changed_after_compare'
  await settle()
  assert.equal(api.comparisonStale.value,true,'项目配置变化后旧比较结果必须标记过期')
  assert.equal(api.confirmed.value,false,'过期结果不得可确认')
  const again=api.confirmApply()   // 过期时确认：先重新比较，不直接保存
  await settle()
  assert.equal(saveCalls,before,'过期比较结果不得沿用保存引用')
  assert.equal(reports.length,compareCount+1,'确认前先重新比较')
  reports.at(-1).resolve(result);await again;await settle()
  assert.equal(api.confirmed.value,true,'重新比较后恢复可确认')
  assert.match(api.recompareNotice.value,/重新比较|变化/)
  // 重新比较确实带的是最新内容与 revision
  assert.equal(reports.at(-1).payload.revision,'r-1')
  assert.equal(reports.at(-1).payload.state.bindings.object_bindings[0].table,'changed_after_compare')
 }
 // ── T25：409（草稿已有新版本）→ 旧比较结果作废、原引用不变、提示重新比较 ──
 {
  const before=saveCalls
  const ontologyBefore409=state.ontologyId, versionBefore409=state.ontologyVersion
  const conflict=api.confirmApply();await settle()
  saving.reject(Object.assign(new Error('此项目已有新版本，请刷新后重试'),{conflict:true}));await conflict;await settle()
  assert.equal(saveCalls,before+1,'确实尝试了保存')
  assert.equal(state.ontologyId,ontologyBefore409,'409 后原引用保持不变')
  assert.equal(state.ontologyVersion,versionBefore409,'409 后原引用版本保持不变')
  assert.equal(api.applied.value,false,'不得显示切换成功')
  assert.equal(api.report.value,null,'409 后旧比较结果作废，需要重新比较')
  assert.equal(api.confirmed.value,false)
  assert.match(api.message.value,/切换未完成/)
 }
 console.log('通过：比较后改配置使确认失效并先重比；409 冲突原引用不变且旧比较作废。')
}finally{app?.unmount();delete globalThis.__referenceApi;delete globalThis.__referenceSelect;rmSync(root,{recursive:true,force:true})}
