"""Lossless adapter between the local JSON schema and the legacy RDF editor model.

The JSON schema is ours, inspired by Foundry concepts, not a Foundry import format.
Keep resource identifiers and definition order stable during migration.
"""
import copy

GROUPS = {'objectTypes':'owl:Class', 'linkTypes':'owl:ObjectProperty',
          'properties':'owl:DatatypeProperty', 'sharedProperties':'mg:SharedProperty',
          'valueTypes':'mg:ValueType'}
FIELDS = {'id':'@id', 'displayName':'rdfs:label', 'description':'rdfs:comment', 'aliases':'mg:aliases',
          'apiName':'mg:apiName', 'reverseDisplayName':'mg:reverseLabel',
          'cardinality':'mg:cardinality', 'visibility':'mg:visibility',
          'valueSuffix':'mg:valueSuffix', 'valueShape':'mg:valueShape', 'decimalPlaces':'mg:decimalPlaces',
          'sourceIds':'mg:sourceIds', 'reviewNote':'mg:reviewNote'}
REFS = {'sharedPropertyId':'mg:sharedProperty', 'valueTypeId':'mg:valueType'}
JSON_FIELDS = {'constraint':'mg:constraint', 'business':'mg:business', 'formatting':'mg:formatting', 'valueSource':'mg:valueSource', 'isDisplayName':'mg:isDisplayName'}

TOP_FIELDS = {'schemaVersion','namespaces','metadata','definitionOrder','extensions',*GROUPS}
RECORD_FIELDS = {'resourceType','objectTypeId','sourceObjectTypeId','targetObjectTypeId','dataType','extensions',*FIELDS,*REFS,*JSON_FIELDS}
LEGACY_FIELDS = {'@type','rdfs:domain','rdfs:range',*FIELDS.values(),*REFS.values(),*JSON_FIELDS.values()}

def _object(value, path):
    if not isinstance(value,dict):raise ValueError(f'{path} 必须是JSON对象')

def _unknown(value, allowed, path):
    extra=set(value)-allowed
    if extra:raise ValueError(f"{path} 包含未识别字段 {', '.join(sorted(extra))}；请放入 extensions，避免保存时丢失")

SERIES_VALUE_TYPES = {'string', 'double', 'decimal', 'integer', 'boolean', 'date', 'dateTime'}

def canonical_record(record):
    """Read legacy valueShape; write a single dataType descriptor. No on-disk migration."""
    out = copy.deepcopy(record)
    shape = out.pop('valueShape', None)
    if shape == 'timeSeries':
        out['dataType'] = {'type': 'timeSeries', 'valueType': out.get('dataType', {}).get('type', 'double')}
    return out

def _comparable_legacy(model):
    out = copy.deepcopy(model)
    for node in out.get('@graph', []):
        if node.get('mg:valueShape') == 'scalar':
            node.pop('mg:valueShape')
    return out


def validate_json(model):
    _object(model,'ontology')
    if type(model.get('schemaVersion')) is not int or model['schemaVersion']!=1:raise ValueError('不支持的本体JSON版本')
    _unknown(model,TOP_FIELDS,'ontology')
    _object(model.get('namespaces'),'namespaces')
    _object(model.get('extensions',{}),'ontology.extensions')
    if {'@context','@graph'} & set(model.get('extensions',{})):raise ValueError('ontology.extensions 不能覆盖内部结构字段')
    identifiers=[]
    for group in (*GROUPS,'metadata'):
        if not isinstance(model.get(group),list):raise ValueError(f'{group} 必须是数组')
        for i,record in enumerate(model[group]):
            path=f'{group}[{i}]';_object(record,path);_unknown(record,RECORD_FIELDS,path)
            if not isinstance(record.get('id'),str) or not record['id']:raise ValueError(f'{path}.id 必须是非空文本')
            if 'aliases' in record:
                if group!='objectTypes':raise ValueError(f'{path}.aliases 只适用于对象类型')
                if not isinstance(record['aliases'],list) or any(not isinstance(alias,str) for alias in record['aliases']):raise ValueError(f'{path}.aliases 必须是字符串数组')
            identifiers.append(record['id'])
            _object(record.get('extensions',{}),path+'.extensions')
            if LEGACY_FIELDS & set(record.get('extensions',{})):raise ValueError(f'{path}.extensions 不能覆盖已建模字段')
            if group=='metadata' and not isinstance(record.get('resourceType'),str):raise ValueError(f'{path}.resourceType 必须是文本')
            if group!='metadata' and 'resourceType' in record:raise ValueError(f'{path}.resourceType 只适用于metadata')
            if 'sourceObjectTypeId' in record and 'objectTypeId' in record:raise ValueError(f'{path} 不能同时指定两种起点字段')
            if 'targetObjectTypeId' in record and 'dataType' in record:raise ValueError(f'{path} 不能同时指定目标对象和数据类型')
            for key in (*REFS,'objectTypeId','sourceObjectTypeId','targetObjectTypeId'):
                if key in record and not isinstance(record[key],str):raise ValueError(f'{path}.{key} 必须是文本')
            if 'dataType' in record:
                _object(record['dataType'],path+'.dataType');_unknown(record['dataType'],{'type', 'valueType'},path+'.dataType')
                if not isinstance(record['dataType'].get('type'),str) or not record['dataType']['type']:raise ValueError(f'{path}.dataType.type 必须是非空文本')
            dtype = record.get('dataType', {})
            if dtype.get('type') == 'timeSeries':
                if group not in ('properties', 'sharedProperties'):raise ValueError(f'{path} 时间序列只适用于属性')
                if not isinstance(dtype.get('valueType'), str) or dtype['valueType'] not in SERIES_VALUE_TYPES:raise ValueError(f'{path}.dataType.valueType 必须是有效的时间序列观测值类型')
                if 'valueShape' in record:raise ValueError(f'{path} 时间序列数据类型不能同时包含旧 valueShape')
            elif 'valueType' in dtype:raise ValueError(f'{path}.dataType.valueType 只适用于时间序列')
            if 'valueShape' in record:
                if record['valueShape'] not in ('scalar','timeSeries'):raise ValueError(f'{path}.valueShape 必须是 scalar 或 timeSeries')
                if record['valueShape']=='timeSeries' and record.get('dataType',{}).get('type') in ('array','struct'):raise ValueError(f'{path}.valueShape 为时间序列时，数据类型不能是数组或结构体')
    order=model.get('definitionOrder')
    if not isinstance(order,list) or any(not isinstance(x,str) for x in order):raise ValueError('definitionOrder 必须是标识数组')
    if len(identifiers)!=len(set(identifiers)):raise ValueError('本体中存在重复标识')
    if len(order)!=len(identifiers) or set(order)!=set(identifiers):raise ValueError('definitionOrder 必须完整列出每个定义标识，且不得重复')

def encode_ontology(legacy):
    if 'schemaVersion' in legacy:
        validate_json(legacy)
        out = copy.deepcopy(legacy)
        for group in ('properties', 'sharedProperties'):
            out[group] = [canonical_record(r) for r in out[group]]
        return out
    out = {'schemaVersion':1, 'namespaces':copy.deepcopy(legacy['@context']),
           **{k:[] for k in GROUPS}, 'metadata':[], 'definitionOrder':[]}
    for source in legacy['@graph']:
        node = copy.deepcopy(source)
        kind = node.pop('@type')
        record = {k:node.pop(v) for k,v in FIELDS.items() if v in node}
        for k,v in REFS.items():
            if v in node:record[k]=node.pop(v)['@id']
        for k,v in JSON_FIELDS.items():
            if v in node:record[k]=node.pop(v)['@value']
        if 'rdfs:domain' in node:
            record['sourceObjectTypeId' if kind=='owl:ObjectProperty' else 'objectTypeId']=node.pop('rdfs:domain')['@id']
        if 'rdfs:range' in node:
            target=node.pop('rdfs:range')['@id']
            if kind=='owl:ObjectProperty':record['targetObjectTypeId']=target
            else:record['dataType']={'type':target.removeprefix('xsd:')}
        group=next((k for k,v in GROUPS.items() if v==kind),'metadata')
        if group=='metadata':record['resourceType']=kind
        if node:record['extensions']=node
        if group in ('properties', 'sharedProperties'):record = canonical_record(record)
        out[group].append(record)
        out['definitionOrder'].append(record['id'])
    extra={k:v for k,v in legacy.items() if k not in ('@context','@graph')}
    if extra:out['extensions']=copy.deepcopy(extra)
    if _comparable_legacy(decode_ontology(out))!=_comparable_legacy(legacy):raise ValueError('内部模型包含不能无损转换的字段结构，请保留原文件并补充格式适配')
    return out

def decode_ontology(model):
    if '@graph' in model:
        if 'schemaVersion' in model:raise ValueError('不能混用JSON与JSON-LD结构')
        return copy.deepcopy(model)
    validate_json(model)
    graph=[]
    for group,default_type in {**GROUPS,'metadata':None}.items():
        for record in model.get(group,[]):
            node=copy.deepcopy(record.get('extensions',{}))
            node['@type']=default_type or record['resourceType']
            node.update({v:copy.deepcopy(record[k]) for k,v in FIELDS.items() if k in record})
            for k,v in REFS.items():
                if k in record:node[v]={'@id':record[k]}
            for k,v in JSON_FIELDS.items():
                if k in record:node[v]={'@type':'@json','@value':copy.deepcopy(record[k])}
            domain=record.get('sourceObjectTypeId',record.get('objectTypeId'))
            if domain is not None:node['rdfs:domain']={'@id':domain}
            if 'targetObjectTypeId' in record:node['rdfs:range']={'@id':record['targetObjectTypeId']}
            elif 'dataType' in record:
                dtype = record['dataType']
                if dtype['type'] == 'timeSeries':
                    node['rdfs:range'] = {'@id': 'xsd:' + dtype['valueType']}
                    node['mg:valueShape'] = 'timeSeries'  # legacy RDF editor adapter only
                else:node['rdfs:range']={'@id':'xsd:'+dtype['type']}
            graph.append(node)
    order={v:i for i,v in enumerate(model.get('definitionOrder',[]))}
    graph.sort(key=lambda n:order.get(n['@id'],len(order)))
    return {**copy.deepcopy(model.get('extensions',{})), '@context':copy.deepcopy(model['namespaces']), '@graph':graph}

def encode_state(state):
    out=copy.deepcopy(state);out['ontology']=encode_ontology(out['ontology']);return out

def decode_state(state):
    out=copy.deepcopy(state);out['ontology']=decode_ontology(out['ontology']);return out
