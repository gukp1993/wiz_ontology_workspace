export type KnowledgeKind = 'object'|'property'|'valueType'|'function'|'action'|'interface'|'mapping'
export interface KnowledgeNode { id:string; label:string; kind:KnowledgeKind; description:string; source:string; record:Record<string,any>; properties?:{id:string;label:string;type:string}[]; page:string; resourceId:string }
export interface KnowledgeEdge { id:string;source:string;target:string;label:string;kind:'link'|'dependency';record?:Record<string,any> }
export interface KnowledgeModel {nodes:KnowledgeNode[];edges:KnowledgeEdge[];warnings:string[]}
