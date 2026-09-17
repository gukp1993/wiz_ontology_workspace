"""Protected credential vault for project data connections.

Secrets live under <root>/ontology/vault/projects/<projectId>/<connectionId>
with 0600 permissions, deliberately outside drafts/, releases/ and every
snapshot/export path: draft revisions and published versions are produced from
project state only and can never pick these files up. Secrets are write-only
from the API's perspective — read() is called solely by the connection probe
path, values are never returned to browsers and never logged.
"""
import os
import re
import tempfile
from pathlib import Path

from workbench.paths import DATA_ROOT

VAULT = DATA_ROOT / 'ontology/vault/projects'
_ID = re.compile(r'[a-z0-9][a-z0-9_-]{0,63}')


def _path(project_id, connection_id):
    if not _ID.fullmatch(str(project_id or '')) or not _ID.fullmatch(str(connection_id or '')):
        raise ValueError('凭据存储标识无效')
    path = VAULT / str(project_id) / str(connection_id)
    if path.resolve().parent.parent != VAULT.resolve():
        raise ValueError('凭据存储路径无效')
    return path


def save(project_id, connection_id, secret):
    secret = str(secret or '')
    if len(secret) > 512 or '\n' in secret or '\r' in secret:
        raise ValueError('密码内容无效（过长或包含换行）')
    path = _path(project_id, connection_id)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    handle = tempfile.NamedTemporaryFile('w', dir=str(path.parent), delete=False)
    try:
        with handle:
            handle.write(secret)
        os.chmod(handle.name, 0o600)
        os.replace(handle.name, path)
    except Exception:
        try:
            os.unlink(handle.name)
        except OSError:
            pass
        raise


def read(project_id, connection_id):
    """Return the stored secret ('' when absent). Probe path only."""
    path = _path(project_id, connection_id)
    try:
        return path.read_text()
    except FileNotFoundError:
        return ''
    except OSError:
        raise


def clear(project_id, connection_id):
    path = _path(project_id, connection_id)
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def exists(project_id, connection_id):
    return _path(project_id, connection_id).is_file()
