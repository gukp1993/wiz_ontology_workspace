import assert from 'node:assert/strict'
import {definitionSignature,propertyDifferences} from '../frontend/src/project/referenceChanges.ts'
const p=(id,name,extra={})=>({id,displayName:name,objectTypeId:'mg:cluster',dataType:{type:'double'},...extra})
const current={ontology:{properties:[p('power','额定功率'),p('sample','采样功率')],sharedProperties:[],definitionOrder:['power','sample']}}
const draft={ontology:{properties:[p('soc','采样SOC',{dataType:{type:'timeSeries',valueType:'double'}}),p('power','额定功率',{description:'更新口径'})],sharedProperties:[]}}
assert.deepEqual(propertyDifferences(current,draft,'cluster').map(r=>[r.name,r.change]),[['采样SOC','草稿新增'],['额定功率','定义已修改'],['采样功率','草稿已移除']])
assert.deepEqual(propertyDifferences(current,draft,'other'),[])
const reordered=structuredClone(current);reordered.ontology.properties.reverse();reordered.ontology.definitionOrder.reverse()
assert.equal(definitionSignature(current),definitionSignature(reordered))
assert.notEqual(definitionSignature(current),definitionSignature(draft))
const shared={ontology:{properties:[{id:'p',objectTypeId:'mg:cluster',sharedPropertyId:'s'}],sharedProperties:[{id:'s',displayName:'SOC',dataType:{type:'double'}}]}}
const changed=structuredClone(shared);changed.ontology.sharedProperties[0].dataType={type:'timeSeries',valueType:'double'}
assert.equal(propertyDifferences(shared,changed,'cluster')[0].change,'定义已修改')
console.log('通过：属性增删修改、共享类型继承、对象范围隔离、排序不误报。')

// 同一动作的六条技术错误应合并成可操作的一条定义卡片。
const {validationGroups}=await import('../frontend/src/ontology/validationPresentation.ts')
const id='e5a4a69d-b80d-4e2b-89d9-5f33fc4cd435'
const errors=['缺少名称','缺少业务描述',...['effect','criteria','permission','acceptance'].map(f=>'动作定义未完整: '+f)].map(e=>id+' '+e)
const groups=validationGroups(errors,{ontology:{'@graph':[{'@id':'mg:cluster','rdfs:label':'储能簇'}]},workflow:{actions:[{id,name:'',object_type:'cluster'}]}})
assert.equal(groups.length,1);assert.equal(groups[0].title,'动作定义 · 未命名动作定义 1');assert.equal(groups[0].object,'储能簇')
assert.equal(groups[0].issues.length,6);assert.match(groups[0].issues[2],/变更效果/);assert.match(groups[0].issues[5],/验收案例/)
assert.ok(groups[0].issues.every(i=>!i.includes(id)&&!i.includes('effect')));assert.deepEqual(groups[0].raw,errors)
console.log('通过：六条动作错误归组、中文字段说明、原始诊断保留。')

const {initialSpace}=await import('../frontend/src/app/navigation.ts')
assert.equal(initialSpace('#o-release','project'),'ontology')
assert.equal(initialSpace('#p-release','ontology'),'project')
assert.equal(initialSpace('#versions','project'),'ontology')
assert.equal(initialSpace('','project'),'project')
console.log('通过：发布深链接优先于历史工作区。')
