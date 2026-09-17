"""Server-side catalog store: refreshed table metadata lives outside drafts.

Table catalogs are derived data that can exceed the 2MB API request limit by a
wide margin (hundreds of tables), so they are stored per project/connection
under ontology/catalogs/ and injected into project state on load. They never
enter draft revisions or published snapshots (projects.py strips them).
"""
import json
import tempfile
from pathlib import Path

from workbench.paths import DATA_ROOT

BASE = DATA_ROOT / 'ontology/catalogs/projects'


def _guarded(project_id, connection_id):
    from workbench.projects import clean_id
    pid, cid = clean_id(project_id), str(connection_id)
    if not pid or '/' in cid or '\\' in cid or cid in ('.', '..') or not cid:
        raise ValueError('目录存储标识无效')
    directory = BASE / pid
    if directory.resolve().parent != BASE.resolve():
        raise ValueError('目录存储路径无效')
    return directory, cid


def store(project_id, connection_id, catalog):
    directory, cid = _guarded(project_id, connection_id)
    directory.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile('w', dir=str(directory), delete=False, suffix='.tmp')
    try:
        with handle:
            json.dump({'database': catalog.get('database', ''), 'tables': catalog.get('tables', []),
                       'refreshedAt': catalog.get('refreshedAt', '')}, handle, ensure_ascii=False)
        import os
        os.replace(handle.name, directory / (cid + '.json'))
    except Exception:
        try:
            os.unlink(handle.name)
        except (OSError, UnboundLocalError):
            pass
        raise


def read(project_id, connection_id):
    directory, cid = _guarded(project_id, connection_id)
    path = directory / (cid + '.json')
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def load_all(project_id):
    """All stored catalogs for a project, keyed by connection id."""
    from workbench.projects import clean_id
    directory = BASE / clean_id(project_id)
    out = {}
    if not directory.is_dir():
        return out
    for path in directory.glob('*.json'):
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and 'tables' in data:
            out[path.stem] = data
    return out


def clear(project_id, connection_id):
    directory, cid = _guarded(project_id, connection_id)
    try:
        (directory / (cid + '.json')).unlink()
    except FileNotFoundError:
        pass
