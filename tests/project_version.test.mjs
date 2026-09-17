// 实际组件状态测试：不连接业务数据、不写用户草稿。
// node --import ./tests/ts_hooks.mjs tests/project_version.test.mjs
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
 projectPost(path,payload){const d=defer();reports.push({...d,payload});return d.promise},
}
globalThis.__referenceSelect={props:['options','modelValue','disabled'],render(){return h('select',{disabled:this.disabled},(this.options||[]).map(o=>h('option',{selected:o.value===this.modelValue},o.label)))}}
let app
try{
 const {descriptor}=parse(readFileSync(filename,'utf8'),{filename}),script=compileScript(descriptor,{id:'reference'})
 const template=compileTemplate({source:descriptor.template.content,filename,id:'reference',ssr:true,ssrCssVars:[],compilerOptions:{bindingMetadata:script.bindings}})
 let code=script.content.replace(/import AppSelect from ['"].*?['"]/,'const AppSelect=globalThis.__referenceSelect').replace(/import \{ listVersions \} from ['"].*?['"]/,'const {listVersions}=globalThis.__referenceApi').replace(/import \{ projectPost \} from ['"].*?['"]/,'const {projectPost}=globalThis.__referenceApi')
 code=code.replace('export default ','const Component=')+'\n'+template.code+'\nComponent.ssrRender=ssrRender;export default Component;'
 code=code.replace(/from (['"])([^'"]+)\1/g,(_,q,s)=>'from '+JSON.stringify(pathToFileURL(s.startsWith('.')?resolve(dirname(filename),s+'.ts'):require.resolve(s)).href))
 const file=join(root,'component.mjs');writeFileSync(file,ts.transpileModule(code,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText)
 const Component=(await import(pathToFileURL(file).href)).default
 const state=reactive({projectId:'p',ontologyId:'a',ontologyVersion:'1.0.0'})
 let saving,saveCalls=0,api
 const props=reactive({projectState:state,ontologyOptions:[{value:'a',label:'储能'},{value:'b',label:'光伏'}],async applyReference(target){saveCalls++;saving=defer();await saving.promise;state.ontologyId=target.ontology;state.ontologyVersion=target.version}})
 // 空渲染器运行真实 Vue 生命周期/watch，渲染另用同一 setup 状态的实际 SSR 模板。
 const renderer=createRenderer({createElement:()=>({}),createText:()=>({}),createComment:()=>({}),insert(){},remove(){},setText(){},setElementText(){},parentNode(){},nextSibling(){},patchProp(){}})
 const setup=Component.setup;Component.setup=(p,c)=>{api=setup(p,c);return ()=>h('div')}
 app=renderer.createApp(Component,props);app.mount({});await settle()
 const html=()=>renderToString(createSSRApp({render:()=>h({props:Component.props,ssrRender:Component.ssrRender,setup:()=>api},props)}))
 const result={classification:'breaking',impacts:[{area:'ontology',severity:'breaking',text:'测试变更'}],blocking:[],reasons:[]}
 assert.equal(api.selectedVersion.value,'1.0.0');assert.equal(reports.length,0)
 api.selectedVersion.value='2.0.0';await settle();assert.equal(reports.length,1,'选择自动发起比较');assert.equal(api.busy.value,true);assert.equal(saveCalls,0)
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
 console.log('通过：选择自动比较、旧响应隔离、确认按钮位置、保存等待、防重复、失败不假成功与重试、跨本体版本联动、比较失败可恢复。')
}finally{app?.unmount();delete globalThis.__referenceApi;delete globalThis.__referenceSelect;rmSync(root,{recursive:true,force:true})}
