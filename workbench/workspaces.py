"""Filesystem registry for independent local ontologies; no global active workspace.

V3 draft storage: ontology/drafts/models/<id>/ holds multi-file draft revisions
(ontology.json, workflow.json, metrics.yaml, rules.yaml, layout.json, plus
optional learning.json / source-graph.json) and a current.json pointer that is
replaced atomically after a revision directory is complete. Legacy single-file
drafts and the models/ base directory are initialization inputs only and are
never rewritten.
"""
import copy
import hashlib
import json
import shutil
import yaml
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from workbench.paths import DATA_ROOT
from workbench.model_format import encode_state, decode_state, encode_ontology, decode_ontology

WORKSPACES = DATA_ROOT / 'ontology/workspaces'
DRAFT_ROOT = DATA_ROOT / 'ontology/drafts/models'
LEGACY_STORAGE_DRAFT = DATA_ROOT / 'ontology/drafts/draft.json'
DEFAULT_ID = 'storage'
DEFAULT_NAME = '储能本体'


class WorkspaceNotFound(ValueError):
    pass


class DuplicateName(ValueError):
    pass


def clean_id(identifier=None):
    if identifier is None:
        return DEFAULT_ID
    if identifier == DEFAULT_ID:
        return identifier
    if not isinstance(identifier, str):
        raise ValueError('本体标识无效')
    try:
        if str(UUID(identifier)) == identifier:
            return identifier
    except (ValueError, AttributeError):
        pass
    raise ValueError('本体标识必须为 storage 或规范 UUID')


def folder(identifier):
    identifier = clean_id(identifier)
    if identifier == DEFAULT_ID:
        raise ValueError('默认本体使用原有目录')
    path = WORKSPACES / identifier
    if path.resolve().parent != WORKSPACES.resolve():
        raise ValueError('本体目录无效')
    return path


def describe(identifier=None):
    identifier = clean_id(identifier)
    if identifier == DEFAULT_ID:
        return {'id': DEFAULT_ID, 'name': DEFAULT_NAME}
    path = folder(identifier) / 'workspace.json'
    if not path.is_file():
        raise WorkspaceNotFound('本体不存在')
    data = json.loads(path.read_text())
    if data.get('id') != identifier or not isinstance(data.get('name'), str):
        raise ValueError('本体登记信息无效')
    return {'id': identifier, 'name': data['name']}


def listing():
    items = []
    # The default storage ontology only appears once it holds content (a draft);
    # a wiped workbench lists nothing until the user creates an ontology.
    if (LEGACY_STORAGE_DRAFT.is_file() or draft_exists(DEFAULT_ID)):
        items.append(describe())
    for path in sorted(WORKSPACES.glob('*/workspace.json')):
        try:
            items.append(describe(path.parent.name))
        except (ValueError, OSError):
            continue
    return items


def create(name, blank):
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
        raise ValueError('本体名称需要填写 1～80 个字符')
    name = name.strip()
    if any(item['name'].casefold() == name.casefold() for item in listing()):
        raise DuplicateName('已存在同名本体，请换一个名称')
    identifier = str(uuid4())
    destination = folder(identifier)
    destination.mkdir(parents=True, exist_ok=False)
    try:
        state = copy.deepcopy(blank)
        state['workspaceId'] = identifier
        state['workflow']['objective']['name'] = name
        write_draft(state)
        (destination / 'releases').mkdir()
        info = {'id': identifier, 'name': name, 'createdAt': datetime.now(timezone.utc).isoformat()}
        (destination / 'workspace.json').write_text(json.dumps(info, ensure_ascii=False, indent=2)+'\n')
    except Exception:
        shutil.rmtree(destination)
        raise
    return {'id': identifier, 'name': name}


# --- multi-file draft store (V3) ----------------------------------------------

DRAFT_FILES = (('ontology', 'ontology.json'), ('workflow', 'workflow.json'),
               ('metrics', 'metrics.yaml'), ('rules', 'rules.yaml'), ('layout', 'layout.json'))


def draft_root(identifier):
    clean = clean_id(identifier)
    path = DRAFT_ROOT / clean
    if path.resolve().parent != DRAFT_ROOT.resolve():
        raise ValueError('草稿目录无效')
    return path


def _pointer(identifier):
    path = draft_root(identifier) / 'current.json'
    return json.loads(path.read_text()) if path.is_file() else None


def draft_exists(identifier):
    return _pointer(identifier) is not None


def revision_of(state):
    value = encode_state({k: v for k, v in state.items() if not str(k).startswith('_')})
    value['workspaceId'] = clean_id(state.get('workspaceId'))
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def read_draft(identifier):
    """Return the draft state behind the current pointer, or None."""
    pointer = _pointer(identifier)
    if not pointer:
        return None
    directory = draft_root(identifier) / 'revisions' / pointer['revisionDir']
    if not directory.is_dir():
        raise ValueError('本体草稿修订不完整，请检查 ontology/drafts/models 目录')
    data = {}
    for key, filename in DRAFT_FILES:
        path = directory / filename
        if not path.is_file():
            continue
        raw = path.read_text()
        data[key] = json.loads(raw) if filename.endswith('.json') else yaml.safe_load(raw)
    for key, filename in (('learning', 'learning.json'), ('sourceGraph', 'source-graph.json')):
        path = directory / filename
        if path.is_file():
            data[key] = json.loads(path.read_text())
    if 'ontology' not in data:
        raise ValueError('本体草稿修订缺少 ontology.json')
    state = {**data, 'workspaceId': clean_id(identifier)}
    state['ontology'] = decode_ontology(data['ontology'] if 'schemaVersion' in data['ontology'] else data['ontology'])
    return state


def write_draft(state):
    """Commit a whole revision directory, then atomically swap the pointer."""
    identifier = clean_id(state.get('workspaceId'))
    directory = draft_root(identifier)
    pointer = _pointer(identifier)
    seq = (pointer.get('seq', 0) if pointer else 0) + 1
    rev = revision_of(state)
    revision_dir = directory / 'revisions' / f'{seq:04d}-{rev[:8]}'
    if revision_dir.exists():
        revision_dir = directory / 'revisions' / f'{seq:04d}-{rev[:8]}-{uuid4().hex[:4]}'
    revision_dir.mkdir(parents=True, exist_ok=False)
    try:
        encoded = encode_state(state)
        for key, filename in DRAFT_FILES:
            value = encoded.get(key)
            if value is None and key in ('metrics', 'rules'):
                value = {'metrics': []} if key == 'metrics' else {'rules': []}
            if value is None:
                continue
            text = json.dumps(value, ensure_ascii=False, indent=2)+'\n' if filename.endswith('.json') else yaml.safe_dump(value, allow_unicode=True, sort_keys=False)
            (revision_dir / filename).write_text(text)
        if state.get('learning'):
            (revision_dir / 'learning.json').write_text(json.dumps(state['learning'], ensure_ascii=False, indent=2)+'\n')
        if state.get('sourceGraph'):
            (revision_dir / 'source-graph.json').write_text(json.dumps(state['sourceGraph'], ensure_ascii=False, indent=2)+'\n')
        temp = directory / '.current.tmp'
        temp.write_text(json.dumps({'seq': seq, 'revision': rev, 'revisionDir': revision_dir.name,
                                    'updatedAt': datetime.now(timezone.utc).isoformat()}, ensure_ascii=False))
        temp.replace(directory / 'current.json')
    except Exception:
        shutil.rmtree(revision_dir, ignore_errors=True)
        raise
    return {'revision': rev, 'seq': seq}


def legacy_draft_state(identifier):
    """One-time initialization input: old single-file drafts, project parts stripped."""
    path = LEGACY_STORAGE_DRAFT if identifier == DEFAULT_ID else folder(identifier) / 'draft.json'
    if not path.is_file():
        return None
    state = json.loads(path.read_text())
    for key in ('bindings', 'parameters'):
        state.pop(key, None)
    state['workspaceId'] = clean_id(identifier)
    return decode_state(state)
