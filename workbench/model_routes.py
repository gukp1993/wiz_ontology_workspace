"""本体区路由处理（B3）：/api/ontologies、state、versions、releases、save/publish、
校验/预览/探索、导出与恢复的业务逻辑。URL、请求字段、状态码与响应结构由 server.py
统一分派，本模块不再做安全检查（Origin/白名单/大小限制都在 Handler 完成）。

从 server.py 机械提取：函数体保持原语义；写路径的 LOCK 使用 workbench.locking 的
同一把全局锁。
"""
import copy
import io
import json
import zipfile
import yaml
from datetime import datetime, timezone
from pathlib import Path

from workbench.demo import Demo
from workbench import workspaces, versions, contracts
from workbench.locking import LOCK
from workbench.model_format import encode_ontology, decode_state, encode_state
from workbench.paths import DATA_ROOT
from workbench.properties import effective, api_name, validate_properties
from workbench.workflow import ensure, definition_errors, demo_available, dry_run_action, readiness, empty_workflow
from workbench.value_types import validate_value_types, check_property_value, check_value, parse_input, VALUE_TYPE
from workbench.explorer import snapshot as explorer_snapshot

STATIC = None  # 静态托管属 HTTP 层职责，留在 server.py
DRAFT = DATA_ROOT / 'ontology/drafts/draft.json'
RELEASES = DATA_ROOT / 'ontology/releases'
FILES = {'workflow': 'ontology/models/storage/workflow.json', 'ontology': 'ontology/models/storage/ontology.json',
         'metrics': 'ontology/models/storage/metrics.yaml', 'rules': 'ontology/models/storage/rules.yaml',
         'bindings': 'ontology/projects/chuangzhi/bindings.yaml', 'parameters': 'ontology/projects/chuangzhi/parameters.yaml'}
EMPTY_BINDINGS = {'object_bindings': [], 'observation_binding': {}, 'source_candidates': [], 'notice': ''}
# Seed namespaces so a blank workbench bootstraps without the legacy storage base files.
DEFAULT_NAMESPACES = {'mg': 'https://example.com/microgrid/', 'owl': 'http://www.w3.org/2002/07/owl#',
                      'rdfs': 'http://www.w3.org/2000/01/rdf-schema#', 'xsd': 'http://www.w3.org/2001/XMLSchema#'}
UNAVAILABLE = '当前本体尚未配置可用的实例数据与计算实现；请先完成数据接入。'


def seed_state():
    return {'ontology': {'@context': copy.deepcopy(DEFAULT_NAMESPACES), '@graph': []},
            'workflow': empty_workflow(), 'metrics': {'metrics': []}, 'rules': {'rules': []}}


def initial():
    out = {}
    try:
        for key in ('workflow', 'ontology', 'metrics', 'rules'):
            path = DATA_ROOT / FILES[key]
            if not path.is_file():
                return seed_state()
            raw = path.read_text()
            out[key] = json.loads(raw) if FILES[key].endswith('.json') else yaml.safe_load(raw)
    except (OSError, ValueError, yaml.YAMLError):
        return seed_state()
    return decode_state(out)


def state_id(state):
    return workspaces.clean_id(state.get('workspaceId'))


def draft_path(identifier='storage'):
    return DRAFT if identifier == 'storage' else workspaces.folder(identifier) / 'draft.json'


def release_path(identifier='storage'):
    return RELEASES if identifier == 'storage' else workspaces.folder(identifier) / 'releases'


def revision(state):
    return workspaces.revision_of(state)


def current(identifier='storage'):
    info = workspaces.describe(identifier)
    state = workspaces.read_draft(info['id'])
    if state is None:
        state = workspaces.legacy_draft_state(info['id'])
    if state is None:
        state = blank_state(info['name'])
    state['workspaceId'] = info['id']
    return ensure(state)


def blank_state(name=''):
    return {'ontology': {'@context': copy.deepcopy(DEFAULT_NAMESPACES), '@graph': []},
            'workflow': empty_workflow(name), 'metrics': {'metrics': []}, 'rules': {'rules': []},
            'layout': {'positions': {}, 'zoom': 1, 'pan': {'x': 0, 'y': 0}}}


def demo_ready(state):
    return state_id(state) == 'storage' and bool(state.get('rules', {}).get('rules')) and bool(state.get('metrics', {}).get('metrics')) and bool(state.get('bindings', {}).get('object_bindings')) and bool(state.get('bindings', {}).get('observation_binding'))


def merged(state, project_state):
    if not isinstance(project_state, dict):
        return state
    out = copy.deepcopy(state)
    if isinstance(project_state.get('bindings'), dict):
        # The legacy demo engine materializes objects from direct field mappings only;
        # dict property sources (computed, database direct, redis direct, unknown kinds,
        # …) are project-layer concerns. They are filtered out of the engine input here
        # but reported as bindings.unsupportedSources so the demo views can show which
        # configured sources the demo execution does not support.
        bindings = copy.deepcopy(project_state['bindings'])
        unsupported = []
        # 方案 §5.6：登记身份（registered）与成员规则（membership）对象不进入旧演示引擎——
        # 它没有 primary_key/column 可读，会被 demo.py 的 KeyError 击穿；列为不支持来源而非伪造结果。
        kept_bindings = []
        for b in bindings.get('object_bindings', []):
            if isinstance(b.get('identity'), dict) and b['identity'].get('kind') == 'registered':
                unsupported.append({'objectType': b.get('object_type', ''), 'property': '', 'kind': 'registeredIdentity'})
                continue
            relations = b.get('relations') or []
            legacy_relations = [r for r in relations if not (isinstance(r, dict) and r.get('kind'))]
            for r in relations:
                if isinstance(r, dict) and r.get('kind'):
                    unsupported.append({'objectType': b.get('object_type', ''), 'property': '', 'kind': 'relation:' + str(r.get('kind'))})
            b['relations'] = legacy_relations
            kept_bindings.append(b)
        bindings['object_bindings'] = kept_bindings
        for b in bindings.get('object_bindings', []):
            props = b.get('properties') or {}
            kept = {}
            for key, value in props.items():
                if isinstance(value, str):
                    kept[key] = value
                    continue
                kind = value.get('kind') if isinstance(value, dict) else None
                entry = {'objectType': b.get('object_type', ''), 'property': key, 'kind': str(kind) if kind else 'unknown'}
                if entry not in unsupported:
                    unsupported.append(entry)
            b['properties'] = kept
            if b.get('title_key') and not isinstance(b['properties'].get(b['title_key']), str):
                b['title_key'] = ''
        bindings['unsupportedSources'] = unsupported
        out['bindings'] = bindings
    if isinstance(project_state.get('parameters'), dict):
        out['parameters'] = project_state['parameters']
    return out


def validate(s):
    errors = []
    try:
        graph = s['ontology']['@graph']; ids = [n['@id'] for n in graph]
        if len(ids) != len(set(ids)):
            errors.append('本体中存在重复标识')
        classes = {n['@id'].removeprefix('mg:') for n in graph if n['@type'] == 'owl:Class'}
        errors.extend(validate_properties(graph))
        errors.extend(validate_value_types(graph))
        errors.extend(definition_errors(s))
        rels = {n['@id'].removeprefix('mg:') for n in graph if n['@type'] == 'owl:ObjectProperty'}
        for raw in graph:
            n = effective(raw, graph)
            if n['@type'] != 'owl:Ontology' and not str(n.get('rdfs:label', '')).strip():
                errors.append(f"{n['@id']} 缺少名称")
            for field in ('rdfs:domain', 'rdfs:range'):
                ref = n.get(field, {}).get('@id', '')
                if ref.startswith('mg:') and ref[3:] not in classes:
                    errors.append(f"{n['@id']} 引用不存在的对象类型 {ref}")
        for r in graph:
            if r['@type'] == 'owl:ObjectProperty' and r.get('mg:cardinality') not in (None, 'one-to-one', 'one-to-many', 'many-to-one', 'many-to-many'):
                errors.append('链接数量关系无效')
        rules = {r['id'] for r in s['rules']['rules']}
        for m in s['metrics']['metrics']:
            if m['rule_ref'] not in rules:
                errors.append(f"指标 {m['name']} 引用不存在的规则")
            for t in m['applicable_types']:
                if t not in classes:
                    errors.append(f'指标引用不存在的类型 {t}')
        bindings = s.get('bindings', {}).get('object_bindings', [])
        if len({b['object_type'] for b in bindings}) != len(bindings):
            errors.append('同一对象类型重复配置数据映射')
        for b in bindings:
            if b.get('title_key') and b['title_key'] not in b['properties']:
                errors.append('显示名称必须有数据字段映射')
            if b['object_type'] not in classes:
                errors.append(f"映射引用不存在的类型 {b['object_type']}")
            for p in b['properties']:
                if p not in {api_name(n) for n in graph if n['@type'] == 'owl:DatatypeProperty'}:
                    errors.append(f'映射属性 {p} 未在本体中定义')
            for r in b['relations']:
                if r['relation'] not in rels or r['target_type'] not in classes:
                    errors.append('项目关系映射引用不存在的定义')
        if demo_ready(s):
            engine = Demo(); engine.rule = s['rules']['rules'][0]; engine.metric = s['metrics']['metrics'][0]; engine.parameters = s['parameters']; engine.bindings = s['bindings']
            engine.validate()
            objects = engine.objects()
            node_map = {n['@id'].removeprefix('mg:'): n for n in graph}
            for obj in objects.values():
                for key, value in obj['properties'].items():
                    definition = next((effective(n, graph) for n in graph if n['@type'] == 'owl:DatatypeProperty' and api_name(n) == key and n.get('rdfs:domain', {}).get('@id') == 'mg:' + obj['type']), {})
                    if not definition:
                        errors.append(f"属性 {key} 未关联对象类型 {obj['type']}")
                    domain = definition.get('rdfs:domain', {}).get('@id')
                    if domain and domain != 'mg:' + obj['type']:
                        errors.append(f"属性 {key} 不适用于 {obj['type']}")
                    errors.extend(f"{obj['id']} / {key}：{e}" for e in check_property_value(definition, value, graph))
                    dtype = definition.get('rdfs:range', {}).get('@id')
                    if dtype == 'xsd:string' and not isinstance(value, str):
                        errors.append(f"属性 {key} 的映射值不是文本")
                    if dtype in ('xsd:decimal', 'xsd:integer', 'xsd:double') and (isinstance(value, bool) or not isinstance(value, (int, float))):
                        errors.append(f"属性 {key} 的映射值不是数值")
                    if dtype == 'xsd:boolean' and not isinstance(value, bool):
                        errors.append(f"属性 {key} 的映射值不是布尔值")
                for relation, target in obj['parents'].items():
                    definition = node_map.get(relation, {})
                    if definition.get('rdfs:domain', {}).get('@id') != 'mg:' + obj['type'] or definition.get('rdfs:range', {}).get('@id') != 'mg:' + objects[target]['type']:
                        errors.append(f"关系 {relation} 的方向与项目映射不一致")
        from rdflib import Graph
        # Local context only: never dereference a supplied remote JSON-LD context.
        if s['ontology']['@context'] != initial()['ontology']['@context']:
            errors.append('命名空间上下文不可更改')
        else:
            Graph().parse(data=json.dumps(s['ontology']), format='json-ld')
    except Exception as e:
        errors.append('配置不完整或不受当前执行器支持：' + str(e))
    return list(dict.fromkeys(errors))


def preview(s):
    if not demo_ready(s):
        return {'errors': [UNAVAILABLE], 'objects': [], 'available': False}
    errors = validate(s)
    if errors:
        return {'errors': errors}
    engine = Demo(); engine.rule = s['rules']['rules'][0]; engine.metric = s['metrics']['metrics'][0]; engine.parameters = s['parameters']; engine.bindings = s['bindings']
    objects = list(engine.objects().values())
    return {'errors': [], 'objects': objects, 'result': engine.execute('chuangzhi:StorageSystem:south', '2026-09-08T10:00:00+08:00')}


def snapshot_files(state):
    if state_id(state) == 'storage':
        return FILES
    return {'ontology': 'models/ontology.json', 'workflow': 'models/workflow.json', 'metrics': 'models/metrics.yaml',
            'rules': 'models/rules.yaml', 'bindings': 'project/bindings.yaml', 'parameters': 'project/parameters.yaml'}


def package(s):
    stream = io.BytesIO(); files = snapshot_files(s); info = workspaces.describe(state_id(s))
    with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as z:
        for key, path in files.items():
            value = encode_ontology(s[key]) if key == 'ontology' else s.get(key, EMPTY_BINDINGS if key == 'bindings' else {})
            z.writestr(path, json.dumps(value, ensure_ascii=False, indent=2) if path.endswith('.json') else yaml.safe_dump(value, allow_unicode=True, sort_keys=False))
        if s.get('learning'):
            z.writestr('learning.json', json.dumps(s['learning'], ensure_ascii=False, indent=2))
        if s.get('sourceGraph'):
            z.writestr('imports/source-graph.json', json.dumps(s['sourceGraph'], ensure_ascii=False, indent=2))
        z.writestr('layout.json', json.dumps(s.get('layout', {}), ensure_ascii=False, indent=2))
        z.writestr('manifest.json', json.dumps({'created_at': datetime.now(timezone.utc).isoformat(), 'revision': revision(s), 'kind': 'workbench-model-snapshot',
                                                'ontology': info, 'files': files, 'note': '模型与项目配置快照，不包含执行服务',
                                                'change_note': s.get('workflow', {}).get('release', {}).get('note', ''),
                                                'reviewer_note': s.get('workflow', {}).get('release', {}).get('reviewer', '')}, ensure_ascii=False))
    return stream.getvalue()


def restore_snapshot(identifier, release):
    if not isinstance(release, str) or Path(release).name != release or '/' in release or '\\' in release or not release.endswith('.zip'):
        raise ValueError('快照名称无效')
    directory = release_path(identifier); path = directory / release
    if not path.is_file() or path.resolve().parent != directory.resolve():
        raise ValueError('当前本体中不存在此快照')
    state = blank_state(workspaces.describe(identifier)['name']); state['workspaceId'] = identifier
    with zipfile.ZipFile(path) as archive:
        if sum(item.file_size for item in archive.infolist()) > 20_000_000:
            raise ValueError('快照内容超过大小限制')
        names = set(archive.namelist())
        manifest = json.loads(archive.read('manifest.json')) if 'manifest.json' in names else {}
        source_id = manifest.get('ontology', {}).get('id', 'storage')
        if source_id != identifier:
            raise ValueError('不能将其他本体的快照恢复到当前本体')
        found = set()
        for key, current_path in snapshot_files(state).items():
            choices = [manifest.get('files', {}).get(key), current_path]
            if identifier == 'storage':
                choices += [FILES[key].removeprefix('ontology/'), FILES[key].replace('ontology.json', 'ontology.jsonld').replace('workflow.json', 'workflow.yaml')]
                choices += [choice.removeprefix('ontology/') for choice in choices if isinstance(choice, str)]
            chosen = next((name for name in choices if name in names), None)
            if chosen:
                raw = archive.read(chosen).decode('utf-8'); state[key] = json.loads(raw) if chosen.endswith(('.json', '.jsonld', '.jsonId')) else yaml.safe_load(raw); found.add(key)
        if 'ontology' not in found:
            raise ValueError('快照缺少本体定义文件')
        for key, path in {'learning': 'learning.json', 'sourceGraph': 'imports/source-graph.json', 'layout': 'layout.json'}.items():
            if path in names:
                state[key] = json.loads(archive.read(path))
    # Projects own their drafts independently; legacy project parts are not restored into the ontology.
    state.pop('bindings', None); state.pop('parameters', None)
    return ensure(state)


# --- GET 路由：query 为 parse_qs 结果；返回 (payload, status) -------------------------

def get_ontologies(query):
    return {'items': workspaces.listing(), 'defaultId': 'storage'}, 200


def get_state(query):
    identifier = query.get('ontology', ['storage'])[0]
    info = workspaces.describe(identifier)
    state = current(info['id'])
    return {'state': encode_state(state), 'revision': revision(state), 'saved': workspaces.draft_exists(info['id']),
            'ontology': info, 'latestVersion': (versions.latest(info['id']) or {}).get('version', '')}, 200


def get_versions(query):
    identifier = query.get('ontology', ['storage'])[0]
    info = workspaces.describe(identifier)
    return {'items': versions.listing(info['id'])}, 200


def get_version_state(query):
    identifier = query.get('ontology', ['storage'])[0]
    info = workspaces.describe(identifier)
    version = query.get('version', [''])[0]
    if not version:
        return {'error': '缺少版本参数'}, 400
    return {'state': encode_state(versions.read_state(info['id'], version)), 'version': version}, 200


def get_releases(query):
    identifier = query.get('ontology', ['storage'])[0]
    info = workspaces.describe(identifier)
    return sorted([p.name for p in release_path(info['id']).glob('*.zip')], reverse=True), 200


def get_source_reference(query):
    identifier = query.get('ontology', ['storage'])[0]
    info = workspaces.describe(identifier)
    source_file = DATA_ROOT / 'resources/imports/storage_20260814/source.jsonId'
    if not source_file.is_file():
        source_file = DATA_ROOT / 'imports/storage_20260814/source.jsonId'  # 旧位置回退（外部数据根兼容）
    if info['id'] != 'storage' or not source_file.is_file():
        return {'nodes': []}, 200
    source = json.loads(source_file.read_text())
    return {'nodes': [{'id': n['@id'], 'name': n.get('name', ''), 'data': n.get('data', {})} for n in source['@graph'] if n.get('nodeType')]}, 200


# --- POST 路由：payload 已解析；返回 (payload, status) -------------------------------

def _decoded_state(payload):
    """POST 公共前置：解码本体形态并定位工作区（与原 do_POST 内联逻辑一致）。"""
    state = ensure(decode_state(payload['state']))
    info = workspaces.describe(state_id(state))
    state['workspaceId'] = info['id']
    return state, info


def post_format_preview(payload):
    from .formatting import format_value
    return {'formatted': format_value(payload.get('value'), payload.get('dataType', 'string'), payload.get('config', {}))}, 200


def post_explorer(payload):
    state, _info = _decoded_state(payload)
    state = merged(state, payload.get('projectState'))
    snap = explorer_snapshot(state)
    sources = (state.get('bindings') or {}).get('unsupportedSources') or []
    if sources:
        snap['bindings'] = {'unsupportedSources': sources}
    return snap, 200


def post_workflow_check(payload):
    state, _info = _decoded_state(payload)
    return readiness(state, validate(state)), 200


def post_demo_objects(payload):
    state, _info = _decoded_state(payload)
    state = merged(state, payload.get('projectState'))
    if not demo_ready(state):
        return {'error': UNAVAILABLE, 'available': False}, 422
    engine = Demo(); engine.bindings = state['bindings']
    out = {'objects': list(engine.objects().values())}
    sources = (state.get('bindings') or {}).get('unsupportedSources') or []
    if sources:
        out['bindings'] = {'unsupportedSources': sources}
    return out, 200


def post_action_preview(payload):
    state, _info = _decoded_state(payload)
    state = merged(state, payload.get('projectState'))
    if not demo_ready(state):
        return {'error': UNAVAILABLE, 'available': False}, 422
    errors = definition_errors(state)
    if errors:
        return {'errors': errors}, 422
    return dry_run_action(state, payload['definitionId'], payload.get('parameters', {})), 200


def post_function_preview(payload):
    state, _info = _decoded_state(payload)
    state = merged(state, payload.get('projectState'))
    if not demo_ready(state):
        return {'error': UNAVAILABLE, 'available': False}, 422
    definition = next((n for n in state['workflow']['functions'] if n['id'] == payload['definitionId']), None)
    if not definition or not demo_available(definition, 'functions'):
        return {'error': '定义发生变化或尚无匹配实现，请交由开发实现后再试算。'}, 422
    errors = validate(state)
    if errors:
        return {'errors': errors}, 422
    engine = Demo(); engine.rule = state['rules']['rules'][0]; engine.metric = state['metrics']['metrics'][0]; engine.parameters = state['parameters']; engine.bindings = state['bindings']; engine.validate()
    parameters = payload.get('parameters', {}); object_id = parameters.get('object_id', '')
    if engine.objects().get(object_id, {}).get('type') not in definition.get('object_types', [definition.get('object_type')]):
        return {'error': '计算对象不存在或类型不匹配'}, 422
    result = engine.execute(object_id, parameters.get('at', ''))
    prop = next((effective(p, state['ontology']['@graph']) for p in state['ontology']['@graph'] if p['@id'] == definition.get('output_property')), None)
    if prop and result.get('value') is not None:
        problems = check_property_value(prop, result['value'], state['ontology']['@graph'])
        if problems:
            return {'errors': problems}, 422
    return {'result': result, 'applied': False}, 200


def post_value_type_check(payload):
    state, _info = _decoded_state(payload)
    node = next((n for n in state['ontology']['@graph'] if n['@id'] == payload['valueTypeId'] and n['@type'] == VALUE_TYPE), None)
    if node is None:
        return {'error': '值类型不存在，请刷新后重试'}, 404
    try:
        value = parse_input(payload.get('sample', ''), node['rdfs:range']['@id']); errors = check_value(node, value, state['ontology']['@graph'])
    except ValueError as exc:
        errors = [str(exc)]
    return {'valid': not errors, 'errors': errors}, 200


def post_validate(payload):
    state, _info = _decoded_state(payload)
    return {'errors': validate(state)}, 200


def post_preview(payload):
    state, _info = _decoded_state(payload)
    state = merged(state, payload.get('projectState'))
    return preview(state), 200


def post_restore(payload):
    state, info = _decoded_state(payload)
    identifier = info['id']
    with LOCK:
        existing = current(identifier)
        if payload.get('revision') != revision(existing):
            return {'error': '此本体已有新版本，请刷新后重试'}, 409
        restored = restore_snapshot(identifier, payload.get('release')); errors = validate(restored)
        folder = draft_path(identifier).parent / 'backups'; folder.mkdir(parents=True, exist_ok=True)
        backup = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '-' + revision(existing)[:8] + '.json'
        data = draft_path(identifier).read_bytes() if draft_path(identifier).exists() else json.dumps(encode_state(existing), ensure_ascii=False, indent=2).encode()
        with (folder / backup).open('xb') as stream:
            stream.write(data)
        workspaces.write_draft(restored)
        return {'revision': revision(restored), 'state': encode_state(restored), 'ontology': info, 'errors': errors, 'backup': backup,
                'release': payload['release'], 'note': '快照中的项目配置未恢复到项目草稿；项目数据由项目绑定区独立维护。'}, 200


def post_publish_check(payload):
    state, info = _decoded_state(payload)
    identifier = info['id']
    latest = versions.latest(identifier)
    if latest:
        previous = versions.read_state(identifier, latest['version'])
        diff = contracts.classify(previous, state); suggested = diff['type']
    else:
        diff = {'type': 'initial', 'reasons': []}; suggested = 'initial'
    return {'suggested': suggested, 'reasons': diff['reasons'], 'latest': (latest or {}).get('version', ''), 'canCompatible': suggested != 'breaking'}, 200


def post_save(payload):
    state, info = _decoded_state(payload)
    identifier = info['id']
    with LOCK:
        current_state = current(identifier)
        if payload.get('revision') != revision(current_state):
            return {'error': '此本体已有新版本，请刷新后重试', 'currentRevision': revision(current_state)}, 409
        errors = validate(state)
        workspaces.write_draft(state)
        return {'revision': revision(state), 'errors': errors, 'ontology': info}, 200


def post_publish(payload):
    state, info = _decoded_state(payload)
    identifier = info['id']
    with LOCK:
        current_state = current(identifier)
        if payload.get('revision') != revision(current_state):
            return {'error': '此本体已有新版本，请刷新后重试', 'currentRevision': revision(current_state)}, 409
        errors = validate(state)
        if state.get('sourceGraph', {}).get('status') in ('pending_review', 'pending_field_verification'):
            errors.append('导入图谱存在未审核内容，请先完成业务口径确认；可保存草稿或导出审核包。')
        if errors:
            return {'errors': errors}, 422
        latest = versions.latest(identifier)
        if latest:
            previous = versions.read_state(identifier, latest['version'])
            diff = contracts.classify(previous, state); suggested = diff['type']
        else:
            diff = {'type': 'initial', 'reasons': []}; suggested = 'initial'
        override = payload.get('changeType')
        if override == 'compatible' and suggested == 'breaking':
            return {'error': '检测到引用失效的结构变化，不能标记为兼容；请按破坏性变更发布。',
                    'reasons': [r['text'] for r in diff['reasons'] if r['severity'] == 'breaking']}, 422
        if not override and suggested == 'pending':
            return {'error': '存在待人工确认的变更，请先选择变更类型后再发布。',
                    'reasons': [r['text'] for r in diff['reasons'] if r['severity'] == 'pending']}, 422
        change_type = override or suggested
        workspaces.write_draft(state)
        entry = versions.publish(identifier, state, {'changeType': change_type,
                                                     'changeNote': payload.get('changeNote') or state.get('workflow', {}).get('release', {}).get('note', ''),
                                                     'reviewer': payload.get('reviewer', ''), 'revision': revision(state), 'reasons': diff.get('reasons', [])})
        return {'revision': revision(state), 'errors': [], 'version': entry['version'], 'changeType': entry['changeType'],
                'reasons': entry.get('reasons', []), 'ontology': info}, 200


def post_ontologies(payload):
    with LOCK:
        info = workspaces.create(payload.get('name'), blank_state())
    return info, 201


def export_bytes(payload):
    """导出 ZIP 的二进制响应体（Handler 走非 JSON 通道）。"""
    state, _info = _decoded_state(payload)
    return package(state)
