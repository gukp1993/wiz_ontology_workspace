// ConnectionManager.vue 最小逻辑适配（C06）与连接删除的前端保护（T14 前端部分）。
// 真实组件 + SSR 渲染，不连接服务：api 层与 form-save/appConfirm 全部替换为受控桩。
// 运行：node --import ./tests/ts_hooks.mjs tests/connection_manager.test.mjs
import assert from 'node:assert/strict'
import {createRequire} from 'node:module'
import {readFileSync,mkdtempSync,writeFileSync,rmSync} from 'node:fs'
import {tmpdir} from 'node:os'
import {join,resolve} from 'node:path'
import {pathToFileURL} from 'node:url'
const require=createRequire(new URL('../frontend/package.json',import.meta.url))
const {parse,compileScript}=require('@vue/compiler-sfc')
const ts=require('typescript'),{createSSRApp,reactive}=require('vue'),{renderToString}=require('@vue/server-renderer')
const root=mkdtempSync(join(tmpdir(),'wiz_conn_manager_'))
process.env.WIZ_WORKBENCH_ROOT=root;process.env.WIZ_WORKBENCH_PORT='18993'
const tick=(n=4)=>new Promise(async r=>{for(let i=0;i<n;i++)await Promise.resolve();setTimeout(r,0)})
try{
  const filename=resolve('frontend/src/project/ConnectionManager.vue')
  const {descriptor}=parse(readFileSync(filename,'utf8'),{filename})
  let code=compileScript(descriptor,{id:'conn-manager-test'}).content
  // 受控桩：appConfirm / 子组件 / ./api（探测、凭据、目录刷新）
  code=code.replace(/import\s*\{[^}]*\}\s*from\s*['"]\.\.\/shared\/appConfirm['"]/,
    'const appConfirm=async()=>globalThis.__connTest.confirm')
  code=code.replace(/import AppSelect from ['"].*?['"]/,'const AppSelect = {}')
  code=code.replace(/import RowMenu from ['"].*?['"]/,'const RowMenu = {}')
  code=code.replace(/import\s*\{([^}]*)\}\s*from\s*['"]\.\.\/shared\/format['"]/,'const {$1} = { formatDateTimeSec: (v:any)=>String(v??\'\') }')
  code=code.replace(/import\s*\{[^}]*\}\s*from\s*['"]\.\/api['"]/,
    'const connectionTest=async p=>{globalThis.__connTest.calls.test.push(p);return globalThis.__connTest.testResult}\n'+
    'const connectionSecret=async p=>{globalThis.__connTest.calls.secret.push(p);return globalThis.__connTest.secretResult}\n'+
    'const catalogRefresh=async(a,b)=>{globalThis.__connTest.calls.refresh.push([a,b]);return globalThis.__connTest.refreshResult}')
  const file=join(root,'component.mjs')
  // 剩下的裸导入只有 vue（type-only import 会被 transpile 擦除）：解析到 frontend 的 node_modules
  code=code.replace(/from (['"])vue\1/g,()=>'from '+JSON.stringify(pathToFileURL(require.resolve('vue')).href))
  writeFileSync(file,ts.transpileModule(code,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText)
  const Component=(await import(pathToFileURL(file).href)).default
  let api
  const setup=Component.setup
  Component.setup=(props,ctx)=>{api=setup(props,ctx);return api}
  Component.render=()=>null

  globalThis.__connTest={calls:{test:[],secret:[],refresh:[]},
    testResult:{ok:true,message:'连接成功：假探测',latencyMs:3},secretResult:{saved:true},
    refreshResult:{ok:true,database:'energy',tables:[],refreshedAt:'2026-09-20T00:00:00+00:00',message:'已读取 0 张表'},confirm:true}
  // 保存成功后的列表定位（locate → nextTick → DOM 查询）：SSR 环境无 document，桩成无操作
  globalThis.document={querySelector:()=>null}

  const projectState=reactive({
    projectId:'p1',
    connections:{connections:[
      {id:'c1',name:'业务库',engine:'mysql',host:'10.0.0.1',port:3306,database:'energy',username:'reader',tls:'none',credentialRef:'local-vault'},
      {id:'c2',name:'缓存',engine:'redis',host:'10.0.0.9',port:6379,dbIndex:0,credentialRef:''}
    ]},
    bindings:{catalogs:{c1:{tables:[{name:'t_device'}],refreshedAt:'2026-09-20T00:00:00+00:00'}}},
    implementations:[]
  })
  const formSaveCalls=[],events={beforeChange:0,changed:0}
  let formSaveResult={ok:true,message:''},pendingGate=null
  const app=createSSRApp(Component,{projectState,
    onBeforeChange:()=>{events.beforeChange++},onChanged:()=>{events.changed++}})
  app.provide('form-guard',{register(){},unregister(){},hasDirty(){return false},editing(){return false}})
  app.provide('form-save',{async submitForm(area,apply,action){
    formSaveCalls.push({area,action})
    if(pendingGate)await pendingGate
    if(formSaveResult.ok)apply()
    return formSaveResult
  }})
  await renderToString(app)

  // --- C06-1 删除持久化失败：不提示成功、行保留、不上报本地事件 ----------------------
  formSaveResult={ok:false,message:'保存失败：工作区有未保存成功的修改'}
  await api.deleteConnection(projectState.connections.connections[0])
  assert.equal(projectState.connections.connections.length,2,'删除失败时连接行必须保留')
  assert.equal(api.messageTone.value,'error')
  assert.match(api.message.value,/删除失败/);assert.doesNotMatch(api.message.value,/已删除连接/)
  assert.equal(events.beforeChange,0,'删除不再走本地 mutate 链路')
  assert.equal(formSaveCalls.length,1);assert.equal(formSaveCalls[0].area,'project')

  // --- C06-2 提示成功必须晚于持久化完成 -------------------------------------------
  let release
  pendingGate=new Promise(r=>{release=r})
  formSaveResult={ok:true,message:''}
  const deleting=api.deleteConnection(projectState.connections.connections[0])
  await tick()
  assert.equal(formSaveCalls.length,2)
  assert.equal(api.messageTone.value,'','持久化进行中不得先提示成功')
  assert.equal(projectState.connections.connections.length,2,'持久化完成前不得清除行')
  release();pendingGate=null
  await deleting
  assert.equal(projectState.connections.connections.length,1,'持久化成功后行被移除')
  assert.equal(projectState.connections.connections[0].id,'c2')
  assert.equal(projectState.bindings.catalogs.c1,undefined,'删除连接同时清掉本地目录副本')
  assert.equal(api.messageTone.value,'success');assert.match(api.message.value,/已删除连接/)
  assert.equal(events.changed,0,'删除不再走本地 mutate 链路')

  // --- C06-3 仍被引用的连接：不进保存、不删除（T14 前端部分，服务端由 L 复核）--------
  projectState.implementations=[{id:'impl1',name:'采样实现',connection:'c2',contractId:'ct-1'}]
  const before=formSaveCalls.length
  await api.deleteConnection(projectState.connections.connections[0])
  assert.equal(formSaveCalls.length,before,'被引用连接不得发起保存')
  assert.equal(projectState.connections.connections.length,1)
  assert.equal(api.messageTone.value,'error');assert.match(api.message.value,/不能删除/)
  projectState.implementations=[]
  assert.equal(globalThis.__connTest.confirm,true)

  // --- C06-4 编辑态「测试连接」必须携带连接 id（服务端 useSaved 才能取到已保存凭据）---
  api.openEditor('c2')
  await api.runTest()
  let call=globalThis.__connTest.calls.test.at(-1)
  assert.equal(call.connection.id,'c2','编辑态探测必须带连接 id')
  assert.equal(call.useSaved,true,'留空密码且连接已保存 → useSaved')
  assert.equal('password' in call,false)
  assert.equal(call.connection.engine,'redis');assert.equal(call.connection.dbIndex,0)
  api.password.value='pw-typed'
  await api.runTest()
  call=globalThis.__connTest.calls.test.at(-1)
  assert.equal(call.connection.id,'c2');assert.equal(call.password,'pw-typed')
  assert.equal('useSaved' in call,false,'显式密码时不带 useSaved')
  api.closeEditor()

  // --- C06-5 新建态不得发送伪造 id；保存失败不提示成功 -------------------------------
  api.openEditor()
  api.draft.value.name='新连接';api.draft.value.host='10.0.0.5';api.draft.value.database='db1'
  await api.runTest()
  call=globalThis.__connTest.calls.test.at(-1)
  assert.equal(call.connection.id,undefined,'新建态不携带连接 id')
  formSaveResult={ok:false,message:'冲突：请刷新'}
  await api.saveConnection()
  assert.equal(api.messageTone.value,'error');assert.match(api.message.value,/保存失败/)
  assert.doesNotMatch(api.message.value,/已新建连接/)
  assert.equal(projectState.connections.connections.length,1,'保存失败不新增连接')
  formSaveResult={ok:true,message:''}
  await api.saveConnection()
  assert.equal(projectState.connections.connections.length,2,'保存成功新增连接')
  assert.match(api.message.value,/已新建连接/)
  await tick()  // 等保存成功后的定位（异步、无 DOM 副作用）跑完，避免悬挂 Promise

  console.log('通过：删除先持久化再提示（失败保留行）、被引用连接不删除、编辑态测试连接携带 id、新建态不带 id、保存失败不假成功。')
} finally {rmSync(root,{recursive:true,force:true})}
