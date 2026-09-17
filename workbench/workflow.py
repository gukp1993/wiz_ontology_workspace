"""Business definitions with explicit, allow-listed local demonstrations."""
import copy
import json
from .properties import effective
from .contracts import signature_errors, is_contract
from .paths import DATA_ROOT
def defaults():return json.loads((DATA_ROOT/'ontology/models/storage/workflow.json').read_text())
def empty_workflow(name=''):
    return {'objective':{'name':name,'question':'','scope':'','acceptance':''},'functions':[],'actions':[],'interfaces':[],'release':{'note':'','reviewer':''}}

def ensure(state):
    if 'workflow' not in state:state['workflow']=defaults() if state.get('workspaceId','storage')=='storage' else empty_workflow()
    return state

def applicable_types(record):
    return record.get('object_types', [record['object_type']] if record.get('object_type') else [])


def is_action_v2(record):
    """本期简化动作（20260917 需求）：definitionVersion 2，仅名称/业务定义/业务效果。"""
    return isinstance(record, dict) and record.get('definitionVersion') == 2


def action_associations(state):
    """workflow.actionAssociations 的容错读取：非法条目跳过，返回 [{objectTypeId, actionId}]。"""
    rows = state.get('workflow', {}).get('actionAssociations', [])
    if not isinstance(rows, list):
        return []
    return [dict(r) for r in rows if isinstance(r, dict) and isinstance(r.get('objectTypeId'), str) and isinstance(r.get('actionId'), str)]


def business_rules(state):
    """workflow.businessRules 容错读取（20260917 业务规则一期）：缺失按空数组，非法条目跳过。"""
    rows = state.get('workflow', {}).get('businessRules', [])
    if not isinstance(rows, list):
        return []
    return [dict(r) for r in rows if isinstance(r, dict) and isinstance(r.get('id'), str) and r.get('id')]


def business_rule_associations(state):
    """workflow.businessRuleAssociations 容错读取：[{objectTypeId(完整@id), ruleId}]。"""
    rows = state.get('workflow', {}).get('businessRuleAssociations', [])
    if not isinstance(rows, list):
        return []
    return [dict(r) for r in rows if isinstance(r, dict) and isinstance(r.get('objectTypeId'), str) and isinstance(r.get('ruleId'), str)]


def _business_rule_errors(state, classes):
    """业务规则一期校验：四字段必填（发布禁止不完整规则）、标识唯一、引用存在、组合唯一。"""
    w = state.get('workflow', {})
    rules = w.get('businessRules', [])
    if rules is None:
        return []
    errors = []
    if not isinstance(rules, list):
        return ['业务规则必须是列表']
    rule_ids = set()
    for index, rule in enumerate(rules, 1):
        if not isinstance(rule, dict):
            errors.append(f'业务规则第 {index} 条格式无效')
            continue
        label = str(rule.get('name') or '').strip() or str(rule.get('id') or f'#{index}')
        rid = rule.get('id')
        if not isinstance(rid, str) or not rid.strip():
            errors.append(f'规则 {label} 缺少稳定标识')
        elif rid in rule_ids:
            errors.append(f'业务规则存在重复标识：{rid}')
        rule_ids.add(rid if isinstance(rid, str) else '')
        for key, title in (('name', '名称'), ('description', '业务定义'), ('content', '规则内容'), ('output', '输出结果')):
            if not str(rule.get(key) or '').strip():
                errors.append(f'规则 {label} 缺少{title}')
    assoc = w.get('businessRuleAssociations', [])
    if assoc is not None and not isinstance(assoc, list):
        errors.append('对象规则引用必须是列表')
        assoc = []
    seen = set()
    for index, row in enumerate(assoc if isinstance(assoc, list) else [], 1):
        if not isinstance(row, dict):
            errors.append(f'对象规则引用第 {index} 条格式无效')
            continue
        object_id, rule_id = row.get('objectTypeId'), row.get('ruleId')
        if not isinstance(object_id, str) or not isinstance(rule_id, str):
            errors.append(f'对象规则引用第 {index} 条缺少对象类型或规则标识')
            continue
        if (object_id, rule_id) in seen:
            errors.append(f'对象规则引用重复：{object_id} + {rule_id}')
        seen.add((object_id, rule_id))
        if _bare_type(object_id) not in classes:
            errors.append(f'对象规则引用不存在的对象类型 {object_id}')
        if rule_id not in rule_ids:
            errors.append(f'对象规则引用不存在的规则 {rule_id}')
    return errors


def effective_action_associations(state):
    """显式新式关联 ∪ 历史动作 object_type 推导（对象 @id 统一为含 mg: 前缀形式）。"""
    w = state.get('workflow', {})
    out = {(r['objectTypeId'], r['actionId']) for r in action_associations(state)}
    for n in w.get('actions', []):
        if is_action_v2(n):
            continue
        for t in applicable_types(n):
            if isinstance(t, str) and t:
                out.add((t if t.startswith('mg:') else 'mg:' + t, n.get('id', '')))
    return [{'objectTypeId': o, 'actionId': a} for o, a in sorted(out)]


def _bare_type(identifier):
    return identifier[3:] if isinstance(identifier, str) and identifier.startswith('mg:') else identifier


def _action_association_errors(state, classes, action_ids):
    """对象动作关联校验：objectTypeId 兼容 mg: 前缀与 bare 两种写法，按 bare 归一比较。"""
    w = state.get('workflow', {})
    rows = w.get('actionAssociations', [])
    if rows is None:
        return []
    if not isinstance(rows, list):
        return ['对象动作关联必须是列表']
    errors = []
    seen = set()
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            errors.append(f'对象动作关联第 {index} 条格式无效')
            continue
        object_id, action_id = row.get('objectTypeId'), row.get('actionId')
        if not isinstance(object_id, str) or not isinstance(action_id, str):
            errors.append(f'对象动作关联第 {index} 条缺少对象类型或动作标识')
            continue
        if (object_id, action_id) in seen:
            errors.append(f'对象动作关联重复：{object_id} + {action_id}')
        seen.add((object_id, action_id))
        if _bare_type(object_id) not in classes:
            errors.append(f'对象动作关联引用不存在的对象类型 {object_id}')
        if action_id not in action_ids:
            errors.append(f'对象动作关联引用不存在的动作 {action_id}；请先移除关联再删除动作')
    return errors

def definition_errors(state):
    w=state.get('workflow',{});g=state['ontology']['@graph'];errors=[]
    classes={n['@id'][3:] for n in g if n['@type']=='owl:Class'}
    shared={n['@id'] for n in g if n['@type']=='mg:SharedProperty'}
    props={n['@id'] for n in g if n['@type'] in ('mg:SharedProperty','owl:DatatypeProperty')}
    rels={n['@id'] for n in g if n['@type']=='owl:ObjectProperty'}
    for prop in g:
        source=prop.get('mg:valueSource',{}).get('@value')
        if source is None:continue
        if prop.get('@type')!='owl:DatatypeProperty' or not isinstance(source,dict) or source.get('kind') not in ('mapping','function'):
            errors.append('属性值来源必须配置在具体对象属性上，选择数据映射或函数计算');continue
        if source['kind']=='function':
            label=prop.get('rdfs:label') or prop['@id']
            if not any(f['id']==source.get('functionId') for f in w.get('functions',[])):errors.append(f'{label} 请选择存在的取值函数')
            if not isinstance(source.get('arguments'),str) or not source['arguments'].strip():errors.append(f'{label} 请说明函数输入从哪里取得')
    ids=[]
    for kind in ('functions','actions','interfaces'):
        for n in w.get(kind,[]):
            ids.append(n['id']);label=n.get('name') or n['id']
            if not n.get('name','').strip():errors.append(f'{label} 缺少名称')
            if not n.get('description','').strip():errors.append(f'{label} 缺少业务描述')
            if n.get('status') not in ('experimental','active','deprecated'):errors.append(f'{label} 状态无效')
            if kind=='interfaces':
                for ref in n.get('properties',[]):
                    if ref not in shared:errors.append(f'{label} 引用不存在的共享属性')
                for t in n.get('implementations',[]):
                    if t not in classes:errors.append(f'{label} 引用不存在的实现对象类型')
                    own=[p for p in g if p['@type']=='owl:DatatypeProperty' and p.get('rdfs:domain',{}).get('@id')=='mg:'+t]
                    for ref in n.get('properties',[]):
                        if not any(p.get('mg:sharedProperty',{}).get('@id')==ref for p in own):errors.append(f'{label}：{t} 未满足共享属性 {ref}')
                continue
            if kind=='actions' and is_action_v2(n):
                # 本期简化动作：仅名称/业务描述/业务效果/状态；不再要求对象、参数、条件、权限、验收。
                if not str(n.get('effect') or '').strip():errors.append(f'{label} 缺少业务效果')
                continue
            if kind=='functions' and n.get('guide_version')==2:
                for key,title in (('input_description','输入'),('logic','计算规则'),('output_description_text','输出')):
                    if not isinstance(n.get(key),str) or not n[key].strip():errors.append(f'{label} 请填写{title}')
                continue
            if kind=='functions' and is_contract(n):
                continue  # V3 契约由 signature_errors 做结构化校验
            targets=applicable_types(n) if kind=='functions' else [n.get('object_type')]
            if not (kind=='functions' and n.get('guide_version')==1) and (not isinstance(targets,list) or not targets or any(not isinstance(t,str) or t not in classes for t in targets)):errors.append(f'{label} 请至少选择一个有效的适用对象类型')
            elif isinstance(targets,list) and all(isinstance(t,str) for t in targets) and len(set(targets))!=len(targets):errors.append(f'{label} 适用对象类型重复')
            names=[]
            for p in n.get('inputs',[]):
                names.append(p['name'])
                if not p['name'].strip():errors.append(f'{label} 参数缺少名称')
                if p['type'] not in ('object','string','double','integer','boolean','timestamp','array','struct'):errors.append(f'{label} 参数类型无效')
                if p['type']=='object' and p.get('object_type') not in classes:errors.append(f'{label} 参数引用不存在的对象类型')
            if len(names)!=len(set(names)):errors.append(f'{label} 参数名称重复')
            if kind=='functions':
                for key in ('logic',):
                    if not n.get(key,'').strip():errors.append(f'{label} 计算定义未完整：{key}')
                if n.get('output_type') and n['output_type'] not in ('string','double','integer','boolean','timestamp','array','struct'):errors.append(f'{label} 输出类型无效')
                if n.get('output_property') and n['output_property'] not in props:errors.append(f'{label} 输出属性引用不存在')
                if n.get('guide_version')==1:
                    if n.get('scenario') not in ('query','formula','compose'):errors.append(f'{label} 请选择函数场景')
                    if not n.get('inputs') and not n.get('no_inputs'):errors.append(f'{label} 添加输入参数或明确无输入')
                    if n.get('inputs') and n.get('no_inputs'):errors.append(f'{label} 无输入选项与参数冲突')
                    for p in n.get('inputs',[]):
                        if not p.get('description','').strip():errors.append(f'{label} 输入参数需说明含义')
                        if p.get('object_type') and p['object_type'] not in classes:errors.append(f'{label} 输入参数的关联对象不存在')
                    for key in ('output_name','output_type','output_description'):
                        if not n.get(key,'').strip():errors.append(f'{label} 请补充输出名称、类型和结果说明')
                    if n.get('scenario')=='compose' and not n.get('steps'):errors.append(f'{label} 组合调用需添加步骤')
                for index,step in enumerate(n.get('steps',[]),1):
                    if any(not step.get(k,'').strip() for k in ('operation','arguments','result')):errors.append(f'{label} 步骤{index}需填写操作、输入和结果')
                    if step.get('function_ref') and not any(f['id']==step['function_ref'] for f in w.get('functions',[])):errors.append(f'{label} 步骤{index}引用函数不存在')
                for binding in n.get('property_bindings',[]):
                    target=next((p for p in g if p['@id']==binding.get('property_ref') and p['@type']=='owl:DatatypeProperty'),None)
                    if not target or target.get('rdfs:domain',{}).get('@id')!='mg:'+binding.get('object_type',''):errors.append(f'{label} 属性绑定必须选择所属对象的属性')
                    if not binding.get('arguments','').strip():errors.append(f'{label} 属性绑定需说明参数来源')

            else:
                for key in ('effect','criteria','permission','acceptance'):
                    if not n.get(key,'').strip():errors.append(f'{label} 动作定义未完整：{key}')
                if n.get('relation_ref') and n['relation_ref'] not in rels:errors.append(f'{label} 链接类型引用不存在')
    dependencies={f['id']:[step.get('function_ref') for step in f.get('steps',[]) if step.get('function_ref')] for f in w.get('functions',[]) if f.get('guide_version')!=2}
    visiting=set();done=set()
    def visit(identifier):
        if identifier in visiting:return True
        if identifier in done:return False
        visiting.add(identifier)
        for child in dependencies.get(identifier,[]):
            if visit(child):return True
        visiting.remove(identifier);done.add(identifier);return False
    if any(visit(identifier) for identifier in dependencies):errors.append('计算函数存在循环调用，请调整步骤引用')
    if len(ids)!=len(set(ids)):errors.append('计算、动作或接口定义存在重复标识')
    errors.extend(_action_association_errors(state, classes, {n['id'] for n in w.get('actions',[]) if isinstance(n,dict) and n.get('id')}))
    errors.extend(_business_rule_errors(state, classes))
    errors.extend(signature_errors(state))
    return errors

def demo_available(record,kind):
    registered={'functions':{'function.storage_soc':'storage.calculate_weighted_soc.v1'},'actions':{'action.change_system':'storage.change_system.dry_run.v1'}}
    if registered.get(kind,{}).get(record.get('id'))!=record.get('implementation_ref') or not record.get('implementation_ref'):return False
    original=next((n for n in defaults()[kind] if n['id']==record['id']),None)
    if not original or record.get('status')=='deprecated':return False
    # Display wording can change; executable contract changes require implementation work.
    if kind=='functions':
        record=copy.deepcopy(record);original=copy.deepcopy(original)
        for entry in (record,original):
            entry['object_types']=applicable_types(entry);entry.pop('object_type',None)
    ignored={'name','description','status'}
    return {k:v for k,v in record.items() if k not in ignored}=={k:v for k,v in original.items() if k not in ignored}

def dry_run_action(state,action_id,params):
    from workbench.demo import Demo
    n=next(n for n in state['workflow']['actions'] if n['id']==action_id)
    if not demo_available(n,'actions'):raise ValueError('此动作尚无匹配的本地模拟实现；请先交由开发实现。')
    engine=Demo();engine.bindings=state['bindings'];objects=engine.objects()
    device=objects.get(params.get('device'));system=objects.get(params.get('system'))
    if not device or device['type']!='StorageDevice':raise ValueError('请选择存在的储能设备')
    if not system or system['type']!='StorageSystem':raise ValueError('请选择存在的储能系统')
    previous=device['parents'].get('belongsToSystem')
    if previous==system['id']:raise ValueError('设备已经属于目标系统，无需变更')
    return {'dry_run':True,'object':device['id'],'before':previous,'after':system['id'],'relation':'belongsToSystem','applied':False}

def readiness(state,errors):
    w=state['workflow'];warnings=[]
    for kind in ('functions','actions'):
        for n in w[kind]:
            if kind=='actions' and is_action_v2(n):
                continue  # 简化动作没有本地模拟实现；执行由项目绑定配置，不作为发布提示
            if kind=='functions' and not is_contract(n) and n.get('superseded_by') is None:
                warnings.append(f"{n.get('name') or n['id']}：历史定义待核对，可转为通用契约")
            elif not demo_available(n,kind):warnings.append(f"{n.get('name') or n['id']}：定义已记录，尚无匹配的本地实现")
    warnings.append('当前数据为模拟实例；发布为定义快照，不是生产部署。' if state.get('workspaceId','storage')=='storage' else '当前本体尚未接入实例数据与执行服务；发布为定义快照。')
    return {'errors':errors,'warnings':warnings,'can_publish':not errors,'counts':{k:len(w[k]) for k in ('functions','actions','interfaces')}}
