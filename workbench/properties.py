"""Explicit local properties and shared metadata in the local JSON-LD model."""
import copy

SHARED='mg:SharedProperty'
FIELDS=('rdfs:label','rdfs:comment','rdfs:range','mg:visibility','mg:valueSuffix','mg:valueShape','mg:decimalPlaces','mg:valueType','mg:formatting','mg:isDisplayName')
TYPES={'xsd:string','xsd:double','xsd:decimal','xsd:integer','xsd:boolean','xsd:date','xsd:dateTime','xsd:array','xsd:struct'}
def effective(node, graph):
    ref=node.get('mg:sharedProperty',{}).get('@id')
    shared=next((n for n in graph if n['@id']==ref and n['@type']==SHARED),None)
    out=copy.deepcopy(node)
    if shared:
        for key in FIELDS:
            out.pop(key,None)
            if key in shared:out[key]=copy.deepcopy(shared[key])
    return out

def data_type(node, graph):
    """Public property type; legacy RDF fields are confined to the adapter."""
    e = effective(node, graph)
    base = e.get('rdfs:range', {}).get('@id', 'xsd:string').removeprefix('xsd:')
    return {'type': 'timeSeries', 'valueType': base} if e.get('mg:valueShape') == 'timeSeries' else {'type': base}

def signature_data_type(ref, graph, declaration=None):
    ref = ref or {}
    if ref.get('kind') == 'property':
        node = next((n for n in graph if n['@id'] == ref.get('id')), None)
        dtype = data_type(node, graph) if node else None
    elif ref.get('kind') == 'base':
        dtype = {'type': ref.get('dataType')}
        if dtype['type'] == 'timeSeries':dtype['valueType'] = ref.get('valueType')
    else:dtype = None
    # Existing project snapshots may override series output. Read only; new UI never writes it.
    if (declaration or {}).get('valueShape') in ('scalar', 'timeSeries'):
        dtype = dtype or {'type': 'unknown'}
        base = dtype.get('valueType') if dtype['type'] == 'timeSeries' else dtype['type']
        dtype = {'type': 'timeSeries', 'valueType': base} if declaration['valueShape'] == 'timeSeries' else {'type': base}
    return dtype


def api_name(node):return node.get('mg:apiName',node['@id'].removeprefix('mg:'))

def is_display_name(node):return node.get('mg:isDisplayName',{}).get('@value') is True

def validate_properties(graph):
    errors=[]; seen=set();by_id={n['@id']:n for n in graph}
    classes={n['@id'] for n in graph if n['@type']=='owl:Class'}
    title_counts={}
    for n in graph:
        if n['@type'] not in ('owl:DatatypeProperty',SHARED):continue
        e=effective(n,graph)
        if n['@type']=='owl:DatatypeProperty' and is_display_name(e):
            title_counts[n.get('rdfs:domain',{}).get('@id')]=title_counts.get(n.get('rdfs:domain',{}).get('@id'),0)+1
        if e.get('rdfs:range',{}).get('@id') not in TYPES:errors.append(f"{n['@id']} 属性类型不支持")
        shape=e.get('mg:valueShape','scalar')
        if shape not in ('scalar','timeSeries'):errors.append(f"{n['@id']} 属性结果形态无效")
        elif shape=='timeSeries' and e.get('rdfs:range',{}).get('@id') in ('xsd:array','xsd:struct'):errors.append(f"{n['@id']} 结果形态为时间序列时，数据类型不能是数组或结构体")
        if e.get('mg:visibility','normal') not in ('normal','prominent','hidden'):errors.append('属性展示设置无效')
        places=e.get('mg:decimalPlaces',0)
        if not isinstance(places,int) or isinstance(places,bool) or not 0<=places<=8:errors.append('小数位数应为0至8的整数')
        formatting=e.get('mg:formatting',{}).get('@value')
        if formatting is not None:
            if not isinstance(formatting,dict) or formatting.get('mode') not in ('builtin','code','natural'):errors.append('显示格式模式无效')
            else:
                if formatting.get('mode')=='code' and not str(formatting.get('code','')).strip():errors.append('自定义格式函数不能为空')
                if formatting.get('mode')=='natural' and not str(formatting.get('instruction','')).strip():errors.append('自然语言格式规则不能为空')
                for key in ('minDecimals','maxDecimals'):
                    v=formatting.get(key)
                    if v is not None and (type(v)!=int or not 0<=v<=20):errors.append('显示小数位应为0至20的整数')
                lo,hi=formatting.get('minDecimals',0),formatting.get('maxDecimals',2)
                if type(lo)==int and type(hi)==int and lo>hi:errors.append('最少小数位不能大于最多小数位')
        if n['@type']==SHARED:continue
        domain=n.get('rdfs:domain',{}).get('@id')
        if domain not in classes:errors.append(f"{n['@id']} 必须关联存在的对象类型")
        key=(domain,api_name(n))
        if key in seen:errors.append('同一对象类型存在重复属性API名称')
        seen.add(key)
        ref=n.get('mg:sharedProperty',{}).get('@id')
        if ref:
            if by_id.get(ref,{}).get('@type')!=SHARED:errors.append(f"{n['@id']} 引用了不存在的共享属性")
            elif any(k in n and n[k]!=by_id[ref].get(k) for k in FIELDS):errors.append('共享属性的继承内容不能在对象属性中覆盖')
    for domain,count in title_counts.items():
        if count>1:errors.append(f"{domain} 标记了 {count} 个显示名称属性，每个对象类型只能有一个")
    return errors
