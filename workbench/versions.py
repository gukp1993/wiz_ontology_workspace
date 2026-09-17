"""Immutable ontology version registry: ontology/releases/models/<id>/<version>/.

Every publish writes a directory snapshot plus a manifest.yaml carrying the
version number and change type. The index.json pointer is updated atomically
after the version directory is complete. Legacy zip snapshots stay where they
are and remain restorable; they are not part of this registry.
"""
import json
import re
import shutil
import yaml
from datetime import datetime, timezone
from pathlib import Path

from workbench.paths import DATA_ROOT
from workbench import workspaces
from workbench.model_format import decode_ontology

REGISTRY = DATA_ROOT / 'ontology/releases/models'


class VersionNotFound(ValueError):
    pass


def folder(identifier):
    clean = workspaces.clean_id(identifier)
    path = REGISTRY / clean
    if path.resolve().parent != REGISTRY.resolve():
        raise ValueError('版本目录无效')
    return path


def _read_index(identifier):
    path = folder(identifier) / 'index.json'
    if path.is_file():
        data = json.loads(path.read_text())
        if isinstance(data.get('versions'), list):
            return data
    return {'versions': []}


def _write_index(identifier, data):
    path = folder(identifier) / 'index.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    temp.replace(path)


def ensure_base_release(identifier):
    """Register version 1.0.0 from legacy base files once, so projects can pin it."""
    if _read_index(identifier)['versions']:
        return
    if identifier != 'storage':
        return
    sources = {'ontology.json': DATA_ROOT / 'ontology/models/storage/ontology.json',
               'workflow.json': DATA_ROOT / 'ontology/models/storage/workflow.json',
               'metrics.yaml': DATA_ROOT / 'ontology/models/storage/metrics.yaml',
               'rules.yaml': DATA_ROOT / 'ontology/models/storage/rules.yaml'}
    if not all(p.is_file() for p in sources.values()):
        return
    destination = folder(identifier) / '1.0.0'
    if not destination.is_dir():
        temp = folder(identifier) / '.1.0.0.tmp'
        if temp.exists():
            shutil.rmtree(temp)
        temp.mkdir(parents=True)
        for name, source in sources.items():
            shutil.copyfile(source, temp / name)
        temp.replace(destination)
    _write_index(identifier, {'versions': [{
        'version': '1.0.0', 'changeType': 'initial', 'changeNote': '由基础定义文件登记的历史版本',
        'reviewer': '', 'createdAt': datetime.now(timezone.utc).isoformat(),
        'revision': 'imported', 'parentVersion': None, 'imported': True}]})


def listing(identifier):
    ensure_base_release(identifier)
    return _read_index(identifier)['versions']


def latest(identifier):
    versions = listing(identifier)
    return versions[-1] if versions else None


def read_state(identifier, version):
    entry = next((v for v in listing(identifier) if v['version'] == version), None)
    if entry is None:
        raise VersionNotFound('本体版本不存在')
    directory = folder(identifier) / version
    data = {}
    for key in ('ontology', 'workflow'):
        path = directory / f'{key}.json'
        if path.is_file():
            data[key] = json.loads(path.read_text())
    for key in ('metrics', 'rules'):
        path = directory / f'{key}.yaml'
        if path.is_file():
            data[key] = yaml.safe_load(path.read_text()) or {}
    if 'ontology' not in data:
        raise VersionNotFound('版本缺少本体定义文件')
    state = {'ontology': decode_ontology(data['ontology']),
             'workflow': data.get('workflow', {'objective': {}, 'functions': [], 'actions': [], 'interfaces': []}),
             'metrics': data.get('metrics') or {'metrics': []},
             'rules': data.get('rules') or {'rules': []},
             'workspaceId': workspaces.clean_id(identifier)}
    layout = directory / 'layout.json'
    if layout.is_file():
        state['layout'] = json.loads(layout.read_text())
    return state


def _next_version(identifier, change_type):
    versions = [v['version'] for v in listing(identifier)]
    numbers = []
    for version in versions:
        match = re.fullmatch(r'(\d+)\.(\d+)\.(\d+)', str(version))
        if match:
            numbers.append(tuple(int(x) for x in match.groups()))
    if not numbers:
        return '1.0.0'
    major, minor, patch = max(numbers)
    if change_type == 'breaking':
        return f'{major + 1}.0.0'
    return f'{major}.{minor + 1}.0'


def publish(identifier, state, meta):
    """Write an immutable version snapshot; returns the index entry."""
    identifier = workspaces.clean_id(identifier)
    versions = listing(identifier)
    parent = versions[-1]['version'] if versions else None
    change_type = meta.get('changeType') or 'initial'
    version = _next_version(identifier, change_type)
    directory = folder(identifier) / version
    if directory.exists():
        raise ValueError('版本目录已存在，请重试发布')
    temp = folder(identifier) / f'.{version}.tmp'
    if temp.exists():
        shutil.rmtree(temp)
    temp.mkdir(parents=True)
    try:
        encoded = dict(state)
        encoded['ontology'] = state['ontology'] if 'schemaVersion' in state['ontology'] else _encode(state['ontology'])
        (temp / 'ontology.json').write_text(json.dumps(encoded['ontology'], ensure_ascii=False, indent=2) + '\n')
        (temp / 'workflow.json').write_text(json.dumps(state.get('workflow', {}), ensure_ascii=False, indent=2) + '\n')
        # Legacy metrics/rules snapshots are only written when they actually carry content.
        if state.get('metrics', {}).get('metrics'):
            (temp / 'metrics.yaml').write_text(yaml.safe_dump(state['metrics'], allow_unicode=True, sort_keys=False))
        if state.get('rules', {}).get('rules'):
            (temp / 'rules.yaml').write_text(yaml.safe_dump(state['rules'], allow_unicode=True, sort_keys=False))
        (temp / 'layout.json').write_text(json.dumps(state.get('layout', {}), ensure_ascii=False, indent=2) + '\n')
        entry = {'version': version, 'changeType': change_type,
                 'changeNote': meta.get('changeNote', ''), 'reviewer': meta.get('reviewer', ''),
                 'createdAt': datetime.now(timezone.utc).isoformat(),
                 'revision': meta.get('revision', ''), 'parentVersion': parent,
                 'reasons': meta.get('reasons', [])}
        (temp / 'manifest.yaml').write_text(yaml.safe_dump(entry, allow_unicode=True, sort_keys=False))
        temp.replace(directory)
    except Exception:
        shutil.rmtree(temp, ignore_errors=True)
        raise
    index = _read_index(identifier)
    index['versions'].append(entry)
    _write_index(identifier, index)
    return entry


def _encode(legacy):
    from workbench.model_format import encode_ontology
    return encode_ontology(legacy)
