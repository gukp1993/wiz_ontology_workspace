// 仓库根运行：node 文档/需求/20260919_本体图谱画布能力优化/验收复现.mjs
// 仅内存组件与 headless Cytoscape，不访问真实工作台。当前基线应失败。
import {readFileSync,writeFileSync,unlinkSync} from 'node:fs'
import {resolve} from 'node:path'
import {spawnSync} from 'node:child_process'
const harness=readFileSync('tests/ontology_graph_controller.test.mjs','utf8')
const split=harness.lastIndexOf('await main()')
if(split<0)throw new Error('现有控制器测试结构已变化，请重新核对复现适配')
const cases=String.raw`
async function independentReview(){
  await auth.login('reviewer','x');
  function fresh(){const a=makeHarness();a.__props.state=makeState();a.__mounted();return a}
  await check('C01 实际边端点随定义更新',async()=>{
    storage.clear();const a=fresh();
    try{
      const e=a.model.value.edges.find(e=>e.domainId==='mg:l_contains');
      a.__props.state.ontology['@graph'].find(n=>n['@id']==='mg:l_contains')['rdfs:range']={'@id':'mg:cluster'};
      await vue.nextTick();
      assert.equal(a.cyInst().getElementById(e.id).target().id(),'obj:mg:cluster');
    }finally{a.__unmount()}
  });
  await check('C02 环形跨挂载后退出恢复全图坐标',async()=>{
    storage.clear();const a=fresh();const id=a.model.value.nodes.find(n=>n.domainId==='mg:device').id;
    a.cyInst().getElementById(id).position({x:700,y:900});
    a.cyInst().zoom(.7);a.cyInst().pan({x:80,y:60});
    a.selection.value=[id];a.toggleScope();a.setScopeLayout('circle');a.flushView();a.__unmount();
    const b=fresh();try{
      assert.ok(b.scope.value,'邻域参数应恢复');b.exitScope();
      assert.deepEqual(b.buildView().positions[id],{x:700,y:900});
    }finally{b.__unmount()}
  });
  await check('C03 有效链接返回不能误判不存在',async()=>{
    storage.clear();const a=fresh();try{
      const e=a.model.value.edges.find(e=>e.domainId==='mg:l_contains');
      a.focusDomain('mg:l_contains');
      assert.equal(a.selectedEdgeId.value,e.id);
      assert.ok(!a.notice.value.includes('已不存在'));
    }finally{a.__unmount()}
  });
  console.log('C04/C05 必须另外执行真实浏览器键盘传播与指针点击验收，不能由此脚本替代。');
  rmSync(rootTmp,{recursive:true,force:true});process.exit(failed?1:0)
}
await independentReview();
`
const target=resolve('tests',`.canvas_review_${process.pid}.mjs`)
try{
  writeFileSync(target,harness.slice(0,split)+cases)
  const result=spawnSync(process.execPath,['--import','./tests/ts_hooks.mjs',target],{stdio:'inherit'})
  process.exitCode=result.status??1
}finally{unlinkSync(target)}
