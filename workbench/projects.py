"""Project binding store: base files, multi-file drafts, releases, validation.

Layout (V3 design section 7):
  ontology/projects/<id>/            base files, initialization & legacy input only
  ontology/drafts/projects/<id>/     multi-file draft revisions + current.json pointer
  ontology/releases/projects/<id>/   immutable published versions (v1, v2, ...)

Draft commits are whole-revision: all files are written into a fresh revision
directory first, then the current.json pointer is atomically replaced, so a
failed save never leaves a half-written draft.
"""
import hashlib
import json
import re
import yaml
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from workbench.paths import DATA_ROOT

BASE = DATA_ROOT / 'ontology/projects'
DRAFTS = DATA_ROOT / 'ontology/drafts/projects'
RELEASES = DATA_ROOT / 'ontology/releases/projects'
FILE_ORDER = (('project', 'project.yaml'), ('connections', 'connections.yaml'),
              ('bindings', 'bindings.yaml'), ('implementations', 'implementations.yaml'),
              ('parameters', 'parameters.yaml'))


class ProjectNotFound(ValueError):
    pass


def clean_id(identifier):
    if not isinstance(identifier, str) or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', identifier):
        raise ValueError('项目标识无效')
    return identifier


def _guarded(root, identifier):
    path = root / clean_id(identifier)
    if path.resolve().parent != root.resolve():
        raise ValueError('项目目录无效')
    return path


def base_dir(identifier):
    return _guarded(BASE, identifier)


def draft_dir(identifier):
    return _guarded(DRAFTS, identifier)


def release_dir(identifier):
    return _guarded(RELEASES, identifier)


def _empty_state(identifier, name, ontology_id, ontology_version):
    return {'projectId': identifier, 'name': name, 'ontologyId': ontology_id,
            'ontologyVersion': str(ontology_version),
            'connections': {'connections': []},
            'bindings': {'notice': '', 'object_bindings': [], 'observation_binding': {}, 'source_candidates': [],
                         'actionBindings': []},
            'implementations': [], 'parameters': {}, 'projectMeta': {}}


def _read_files(directory):
    data = {}
    for key, filename in FILE_ORDER:
        path = directory / filename
        if path.is_file():
            data[key] = yaml.safe_load(path.read_text()) or {}
    return data


_ID_KEYS = ('ontology', 'ontology_id')
_VERSION_KEYS = ('ontology_version', 'model_version')


def _state_from_files(identifier, data):
    project = data.get('project', {}) or {}
    name = str(project.get('name', '')).strip()
    ontology_id = next((project[k] for k in _ID_KEYS if project.get(k)), '')
    version = next((str(project[k]) for k in _VERSION_KEYS if project.get(k) is not None), '')
    if not ontology_id and version:
        ontology_id = 'storage'  # legacy convention: only the storage ontology had projects
    state = _empty_state(identifier, name, ontology_id, version)
    meta = {k: v for k, v in project.items()
            if k not in ('project_id', 'name', *_ID_KEYS, *_VERSION_KEYS)}
    state['projectMeta'] = meta
    if 'connections' in data:
        state['connections'] = data['connections']
    if 'bindings' in data:
        state['bindings'] = data['bindings']
    if 'implementations' in data:
        loaded = data['implementations']
        state['implementations'] = loaded.get('implementations', []) if isinstance(loaded, dict) else loaded
    if 'parameters' in data:
        state['parameters'] = data['parameters']
    return _normalize(state)


def _normalize(state):
    state.setdefault('connections', {'connections': []})
    bindings = state.setdefault('bindings', {})
    bindings.setdefault('object_bindings', [])
    bindings.setdefault('observation_binding', {})
    bindings.setdefault('source_candidates', [])
    bindings.setdefault('actionBindings', [])
    if not isinstance(state.get('implementations'), list):
        state['implementations'] = []
    state.setdefault('parameters', {})
    state.setdefault('projectMeta', {})
    return state


def _files_from_state(state):
    identifier = clean_id(state.get('projectId'))
    project = {'project_id': identifier, 'name': state.get('name', ''),
               'ontology': state.get('ontologyId', ''),
               'ontology_version': str(state.get('ontologyVersion', ''))}
    project.update(state.get('projectMeta') or {})
    # Table catalogs are derived server-side data (potentially megabytes); they are
    # stored under ontology/catalogs/ and must not enter draft or snapshot files.
    bindings = {k: v for k, v in state['bindings'].items() if k != 'catalogs'}
    return {'project': project, 'connections': state.get('connections', {'connections': []}),
            'bindings': bindings,
            'implementations': {'implementations': state.get('implementations', [])},
            'parameters': state.get('parameters', {})}


def revision(state):
    # catalogs 是服务端注入的派生数据，不参与修订哈希：否则加载注入/保存未注入
    # 两种路径算出的哈希不同，会让所有保存误判 409。
    payload = {k: v for k, v in state.items() if not str(k).startswith('_')}
    bindings = payload.get('bindings')
    if isinstance(bindings, dict) and 'catalogs' in bindings:
        payload['bindings'] = {k: v for k, v in bindings.items() if k != 'catalogs'}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _pointer(identifier):
    path = draft_dir(identifier) / 'current.json'
    return json.loads(path.read_text()) if path.is_file() else None


def read_draft(identifier):
    pointer = _pointer(identifier)
    if not pointer:
        return None
    directory = draft_dir(identifier) / 'revisions' / pointer['revisionDir']
    if not directory.is_dir():
        raise ValueError('项目草稿修订不完整，请联系管理员检查 drafts 目录')
    state = _state_from_files(clean_id(identifier), _read_files(directory))
    state['_draft'] = {'seq': pointer.get('seq', 0), 'updatedAt': pointer.get('updatedAt', '')}
    return state


def save_draft(state):
    identifier = clean_id(state.get('projectId'))
    directory = draft_dir(identifier)
    pointer = _pointer(identifier)
    seq = (pointer.get('seq', 0) if pointer else 0) + 1
    rev = revision(state)
    revision_dir = directory / 'revisions' / f'{seq:04d}-{rev[:8]}'
    if revision_dir.exists():
        revision_dir = directory / 'revisions' / f'{seq:04d}-{rev[:8]}-{uuid4().hex[:4]}'
    revision_dir.mkdir(parents=True, exist_ok=False)
    try:
        for key, value in _files_from_state(state).items():
            filename = dict(FILE_ORDER)[key]
            (revision_dir / filename).write_text(yaml.safe_dump(value, allow_unicode=True, sort_keys=False))
        pointer_path = directory / 'current.json'
        temp = directory / '.current.tmp'
        temp.write_text(json.dumps({'seq': seq, 'revision': rev, 'revisionDir': revision_dir.name,
                                    'updatedAt': datetime.now(timezone.utc).isoformat()}, ensure_ascii=False))
        temp.replace(pointer_path)
    except Exception as exc:
        # 指针未替换，修订目录只是孤立垃圾；清掉后向上报错，绝不能让调用方误以为保存成功。
        import shutil
        shutil.rmtree(revision_dir, ignore_errors=True)
        return {'error': '草稿保存失败：' + str(exc)}
    return {'revision': rev, 'seq': seq}


def load(identifier):
    identifier = clean_id(identifier)
    state = read_draft(identifier)
    if state is not None:
        return state, True
    if (base_dir(identifier) / 'project.yaml').is_file():
        return _state_from_files(identifier, _read_files(base_dir(identifier))), False
    raise ProjectNotFound('项目不存在')


def listing(ontology_id=None):
    identifiers = set()
    if BASE.is_dir():
        identifiers.update(p.parent.name for p in BASE.glob('*/project.yaml'))
    if DRAFTS.is_dir():
        identifiers.update(p.parent.name for p in DRAFTS.glob('*/current.json'))
    items = []
    for identifier in sorted(identifiers):
        try:
            state, has_draft = load(identifier)
        except (ValueError, OSError, yaml.YAMLError):
            continue
        if ontology_id and state.get('ontologyId') != ontology_id:
            continue
        items.append({'id': identifier, 'name': state.get('name') or identifier,
                      'ontologyId': state.get('ontologyId', ''),
                      'ontologyVersion': state.get('ontologyVersion', ''),
                      'hasDraft': has_draft})
    return items


def create(name, ontology_id='', ontology_version=''):
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
        raise ValueError('项目名称需要填写 1～80 个字符')
    name = name.strip()
    if any(item['name'].casefold() == name.casefold() and (not ontology_id or item['ontologyId'] == ontology_id)
           for item in listing(ontology_id or None)):
        raise ValueError('已存在同名项目，请换一个名称')
    identifier = uuid4().hex[:12]
    while (base_dir(identifier) / 'project.yaml').is_file() or _pointer(identifier):
        identifier = uuid4().hex[:12]
    state = _empty_state(identifier, name, ontology_id, str(ontology_version))
    directory = base_dir(identifier)
    directory.mkdir(parents=True, exist_ok=False)
    try:
        for key, value in _files_from_state(state).items():
            (directory / dict(FILE_ORDER)[key]).write_text(yaml.safe_dump(value, allow_unicode=True, sort_keys=False))
    except Exception:
        directory.rmdir()
        raise
    return {'id': identifier, 'name': name, 'ontologyId': ontology_id, 'ontologyVersion': str(ontology_version)}


def publish(state):
    identifier = clean_id(state.get('projectId'))
    directory = release_dir(identifier)
    directory.mkdir(parents=True, exist_ok=True)
    existing = [p.name for p in directory.iterdir() if p.is_dir() and p.name.startswith('v')]
    version = f'v{len(existing) + 1}'
    while (directory / version).exists():
        version = 'v' + str(int(version[1:]) + 1)
    temp = directory / f'.{version}.tmp'
    if temp.exists():
        temp.rmdir()
    temp.mkdir()
    try:
        for key, value in _files_from_state(state).items():
            (temp / dict(FILE_ORDER)[key]).write_text(yaml.safe_dump(value, allow_unicode=True, sort_keys=False))
        manifest = {'version': version, 'projectId': identifier, 'ontologyId': state.get('ontologyId', ''),
                    'ontologyVersion': str(state.get('ontologyVersion', '')),
                    'createdAt': datetime.now(timezone.utc).isoformat(), 'revision': revision(state),
                    'note': '项目配置快照；配置校验通过不等于已执行验证或上线'}
        (temp / 'manifest.yaml').write_text(yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False))
        temp.replace(directory / version)
    except Exception:
        import shutil
        shutil.rmtree(temp, ignore_errors=True)
        raise
    return {'version': version}


def published_versions(identifier):
    directory = release_dir(identifier)
    if not directory.is_dir():
        return []
    out = []
    for path in sorted(directory.iterdir()):
        manifest = path / 'manifest.yaml'
        if path.is_dir() and manifest.is_file():
            try:
                out.append(yaml.safe_load(manifest.read_text()) or {})
            except yaml.YAMLError:
                continue
    return out


# --- configuration validation (V3 section 8) ----------------------------------
# B2 拆分：validate_project 及各职责校验移至 workbench.project_validation，
# 双方共用纯辅助移至 workbench.project_mapping；此处保留兼容转发入口，
# server.py 与既有测试经 projects.validate_project / projects.derive_display_names
# 调用不需改动。

from workbench.project_mapping import bare  # noqa: E402  (upgrade_check 使用)
from workbench.project_mapping import derive_display_names  # noqa: F401  (兼容转发)
from workbench.project_validation import validate_project  # noqa: F401  (兼容转发)


def upgrade_check(state, current_state, target_state):
    from workbench.contracts import classify
    diff = classify(current_state, target_state)
    bindings = state.get('bindings', {})
    implementations = state.get('implementations', [])
    impacts = []
    for reason in diff['reasons']:
        area, rid, severity, text = reason['area'], reason['id'], reason['severity'], reason['text']
        matched = False
        if area in ('objectTypes', 'linkTypes', 'properties', 'sharedProperties', 'valueTypes'):
            for b in bindings.get('object_bindings', []):
                if area == 'objectTypes' and bare(rid) == b.get('object_type'):
                    impacts.append({'area': 'objectBinding', 'ref': b.get('object_type'), 'severity': severity,
                                    'text': text + '；此对象的数据映射需要核对'})
                    matched = True
                for prop in b.get('properties', {}):
                    if area in ('properties', 'sharedProperties') and (prop in rid or rid in prop or bare(rid).endswith(prop)):
                        impacts.append({'area': 'propertyMapping', 'ref': f"{b.get('object_type')}.{prop}", 'severity': severity,
                                        'text': text + '；此属性的来源绑定需要核对'})
                        matched = True
                for r in b.get('relations', []):
                    if area == 'linkTypes' and bare(rid) == r.get('relation'):
                        impacts.append({'area': 'linkMapping', 'ref': f"{b.get('object_type')}.{r.get('relation')}", 'severity': severity,
                                        'text': text + '；此链接映射需要核对'})
                        matched = True
        if area == 'contracts':
            for impl in implementations:
                if isinstance(impl, dict) and impl.get('contractId') == rid:
                    impacts.append({'area': 'implementation', 'ref': impl.get('id'), 'severity': severity,
                                    'text': text + '；此实现需要核对或重写'})
                    matched = True
            for b in bindings.get('object_bindings', []):
                for prop, value in (b.get('properties') or {}).items():
                    if isinstance(value, dict) and value.get('kind') == 'computed':
                        impl = next((i for i in implementations if isinstance(i, dict) and i.get('id') == value.get('implementation')), None)
                        if impl and impl.get('contractId') == rid:
                            impacts.append({'area': 'propertySource', 'ref': f"{b.get('object_type')}.{prop}", 'severity': severity,
                                            'text': text + '；此属性的计算来源需要核对'})
                            matched = True
        if not matched and severity in ('breaking', 'pending'):
            impacts.append({'area': 'ontology', 'ref': rid, 'severity': severity, 'text': text + '；本项目当前配置未直接引用'})
    blocking = [i for i in impacts if i['severity'] == 'breaking' and i['area'] != 'ontology']
    return {'classification': diff['type'], 'reasons': diff['reasons'], 'impacts': impacts, 'blocking': blocking}
