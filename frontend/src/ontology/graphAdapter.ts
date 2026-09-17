// Stable ontology IDs are retained. Layout is presentation-only.
import type { Core, ElementDefinition } from 'cytoscape'
import type { WorkbenchState } from './modelFormat'
import { nodeW, nodeH } from '../shared/layout'
export function toElements(state:WorkbenchState):ElementDefinition[] {
 const graph=state.ontology['@graph'],types=graph.filter(n=>n['@type']==='owl:Class'),ids=new Set(types.map(n=>n['@id']))
 return [...types.map((n,i)=>({data:{id:n['@id'],name:n['rdfs:label'],type:'实体',w:Math.max(160,nodeW(n['rdfs:label'])),h:Math.max(62,nodeH(n['rdfs:label']))},position:state.layout?.positions?.[n['@id']]||{x:200+i*270,y:220}})),...graph.filter(n=>n['@type']==='owl:ObjectProperty'&&ids.has(n['rdfs:domain']?.['@id'])&&ids.has(n['rdfs:range']?.['@id'])).map(n=>({data:{id:n['@id'],source:n['rdfs:domain']['@id'],target:n['rdfs:range']['@id'],relation:n['rdfs:label']}}))]
}
export function captureLayout(cy:Core){return {positions:Object.fromEntries(cy.nodes().map(n=>[n.id(),{...n.position()}])),zoom:cy.zoom(),pan:{...cy.pan()}}}
// Adapted from the original EditorView.applyGraphState: update in place,
// retaining the canvas instance, selection and viewport.
export function applyElements(cy:Core,elements:ElementDefinition[]){
 const nodes=new Map(elements.filter(e=>!e.data.source).map(e=>[e.data.id,e]))
 const edges=new Map(elements.filter(e=>e.data.source).map(e=>[e.data.id,e]))
 cy.batch(()=>{
  cy.edges().forEach(e=>{if(!edges.has(e.id()))cy.remove(e)})
  cy.nodes().forEach(n=>{if(!nodes.has(n.id()))cy.remove(n)})
  nodes.forEach(n=>{const el=cy.getElementById(n.data.id);if(!el.length)cy.add(n);else{el.data(n.data);el.position(n.position)}})
  edges.forEach(e=>{const el=cy.getElementById(e.data.id);if(!el.length)cy.add(e);else{el.move({source:e.data.source,target:e.data.target});el.data('relation',e.data.relation)}})
 })
}
export function importedElements(state:WorkbenchState):ElementDefinition[]{
 const g=state.sourceGraph;if(!g)return []
 const counts={'实体':0,'属性':0,'规则':0},cols={'实体':0,'属性':1,'规则':2}
 return [...g.nodes.map(n=>({data:{id:n.id,name:n.name,type:n.type,w:Math.max(165,nodeW(n.name)),h:Math.max(52,nodeH(n.name))},position:{x:120+cols[n.type]*430,y:90+(counts[n.type]++)*92}})),...g.edges.map(e=>({data:{...e}}))]
}
