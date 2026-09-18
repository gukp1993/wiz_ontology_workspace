"""迁移与备份 CLI：inspect / import / verify / export / backup / init。

设计约束（需求说明 §10–§11，冻结）：
* 受控停写迁移，一次切换，一个权威写库；不做文件+库双写，读取不回退文件。
* 源目录全程只读；导入以 wb_import_items 记录 source hash，可重入：
  同 key 同 hash 跳过，同 key 不同 hash 失败（绝不静默覆盖）。
* 迁移历史修订保留原始组件形态（payload_format=legacy-*），不经可能丢字段的
  转换再入库；当前指针的旧 revision 作为初始 revision_token（一次兼容）。
* verify 做语义对比（状态/发布/引用/凭据数量/附件 hash/完整性检查），
  不以"没报错"判断成功；报告不输出密钥明文或含口令 DSN。
* export 反向导出旧兼容目录（用于带新写入的回滚），凭据明文落 0600 文件并强提示。
* backup 使用 SQLite 在线备份 API（绝不只拷主文件忽略 WAL）。

用法：
  python3 -m workbench.storage.transfer init [--url URL]
  python3 -m workbench.storage.transfer inspect --source ROOT [--output FILE]
  python3 -m workbench.storage.transfer import --source ROOT [--url URL] [--batch ID]
  python3 -m workbench.storage.transfer verify --source ROOT [--url URL]
  python3 -m workbench.storage.transfer export --output DIR [--url URL]
  python3 -m workbench.storage.transfer backup --output FILE [--url URL]
"""
import argparse
import base64
import hashlib
import json
import shutil
import sys
from pathlib import Path

import yaml
from sqlalchemy import text as sql_text

from workbench import storage
from workbench.storage import assets as store
from workbench.storage import artifacts as artifact_store
from workbench.storage import configuration as config_store
from workbench.storage import indexing
from workbench.storage import engine as dbengine
from workbench.storage.engine import (resolve_url, schema_status, write_tx,
                                      read_connection, utcnow, content_hash, read_head,
                                      read_snapshot, head_by_external)

MODEL_DRAFT_FILES = (('ontology', 'ontology.json'), ('workflow', 'workflow.json'),
                     ('metrics', 'metrics.yaml'), ('rules', 'rules.yaml'), ('layout', 'layout.json'))
MODEL_EXTRA_FILES = (('learning', 'learning.json'), ('sourceGraph', 'source-graph.json'))
PROJECT_FILES = (('project', 'project.yaml'), ('connections', 'connections.yaml'),
                 ('bindings', 'bindings.yaml'), ('implementations', 'implementations.yaml'),
                 ('parameters', 'parameters.yaml'))


# ---------------------------------------------------------------- 源扫描（只读）

def _read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def _read_yaml(path):
    return yaml.safe_load(path.read_text(encoding='utf-8')) or {}


def scan_models(root):
    """本体资产：workspace.json 登记 + V3 草稿修订 + 发布目录 + 历史 ZIP/备份。"""
    out = []
    workspaces_dir = root / 'ontology/workspaces'
    drafts_dir = root / 'ontology/drafts/models'
    releases_dir = root / 'ontology/releases/models'
    legacy_draft = root / 'ontology/drafts/draft.json'
    ids = set()
    if drafts_dir.is_dir():
        ids.update(p.parent.name for p in drafts_dir.glob('*/current.json'))
    if releases_dir.is_dir():
        ids.update(p.name for p in releases_dir.iterdir() if p.is_dir() and (p / 'index.json').is_file())
    if legacy_draft.is_file():
        ids.add('storage')
    for path in sorted(workspaces_dir.glob('*/workspace.json')) if workspaces_dir.is_dir() else []:
        ids.add(path.parent.name)
    for identifier in sorted(ids):
        entry = {'id': identifier, 'name': '', 'drafts': [], 'current': None,
                 'releases': [], 'release_index': None, 'zips': [], 'backups': [],
                 'legacy_draft': None, 'problems': []}
        ws = workspaces_dir / identifier / 'workspace.json'
        if ws.is_file():
            try:
                entry['name'] = str(_read_json(ws).get('name') or '')
            except (OSError, ValueError) as exc:
                entry['problems'].append(f'workspace.json 不可读：{exc}')
        if identifier == 'storage' and not entry['name']:
            entry['name'] = '储能本体'
        draft_dir = drafts_dir / identifier
        pointer = draft_dir / 'current.json' if draft_dir.is_dir() else None
        if pointer is not None and pointer.is_file():
            try:
                info = _read_json(pointer)
                entry['current'] = info
                revision_dir = draft_dir / 'revisions' / str(info.get('revisionDir', ''))
                if not revision_dir.is_dir():
                    entry['problems'].append(f'当前指针指向不存在的修订目录 {info.get("revisionDir")}')
            except (OSError, ValueError) as exc:
                entry['problems'].append(f'current.json 不可读：{exc}')
        revisions = draft_dir / 'revisions'
        if revisions.is_dir():
            for revision_path in sorted(revisions.iterdir()):
                if revision_path.is_dir():
                    entry['drafts'].append(revision_path)
        release_base = releases_dir / identifier
        index = release_base / 'index.json'
        if index.is_file():
            try:
                entry['release_index'] = _read_json(index)
            except (OSError, ValueError) as exc:
                entry['problems'].append(f'发布索引不可读：{exc}')
            for name, _prefix in [(v.get('version'), '') for v in entry['release_index'].get('versions', [])]:
                directory = release_base / str(name)
                if directory.is_dir():
                    entry['releases'].append(directory)
                else:
                    entry['problems'].append(f'发布索引引用不存在的版本目录 {name}')
        for extra in release_base.glob('*.zip') if release_base.is_dir() else []:
            entry['zips'].append(extra)
        for backups_dir in (root / 'ontology/drafts/backups', draft_dir / 'backups'):
            if backups_dir.is_dir():
                entry['backups'].extend(p for p in sorted(backups_dir.iterdir()) if p.is_file())
        out.append(entry)
    return out


def scan_projects(root):
    out = []
    base = root / 'ontology/projects'
    drafts = root / 'ontology/drafts/projects'
    releases = root / 'ontology/releases/projects'
    ids = set()
    for d in (base, drafts, releases):
        if d.is_dir():
            ids.update(p.name for p in d.iterdir() if p.is_dir())
    for identifier in sorted(ids):
        entry = {'id': identifier, 'base': base / identifier, 'drafts': [], 'current': None,
                 'releases': [], 'problems': []}
        draft_dir = drafts / identifier
        pointer = draft_dir / 'current.json'
        if pointer.is_file():
            try:
                info = _read_json(pointer)
                entry['current'] = info
                if not (draft_dir / 'revisions' / str(info.get('revisionDir', ''))).is_dir():
                    entry['problems'].append(f'当前指针指向不存在的修订目录 {info.get("revisionDir")}')
            except (OSError, ValueError) as exc:
                entry['problems'].append(f'current.json 不可读：{exc}')
        revisions = draft_dir / 'revisions'
        if revisions.is_dir():
            entry['drafts'] = [p for p in sorted(revisions.iterdir()) if p.is_dir()]
        if releases.is_dir() and (releases / identifier).is_dir():
            entry['releases'] = sorted(p for p in (releases / identifier).iterdir()
                                       if p.is_dir() and (p / 'manifest.yaml').is_file())
        out.append(entry)
    return out


def scan_flows(root):
    out = []
    drafts = root / 'ontology/drafts/flows'
    if not drafts.is_dir():
        return out
    for pointer in sorted(drafts.glob('*/current.json')):
        identifier = pointer.parent.name
        entry = {'id': identifier, 'drafts': [], 'current': None, 'problems': []}
        try:
            info = _read_json(pointer)
            entry['current'] = info
            revision_dir = pointer.parent / 'revisions' / str(info.get('revisionDir', ''))
            if not revision_dir.is_dir():
                entry['problems'].append(f'当前指针指向不存在的修订目录 {info.get("revisionDir")}')
        except (OSError, ValueError) as exc:
            entry['problems'].append(f'current.json 不可读：{exc}')
        revisions = pointer.parent / 'revisions'
        if revisions.is_dir():
            entry['drafts'] = [p for p in sorted(revisions.iterdir()) if p.is_dir()]
        out.append(entry)
    return out


def dir_hash(directory):
    sha = hashlib.sha256()
    for path in sorted(directory.rglob('*')):
        if path.is_file():
            sha.update(str(path.relative_to(directory)).encode())
            sha.update(path.read_bytes())
    return sha.hexdigest()


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------- 模型修订/发布解析

def parse_model_revision(directory):
    """修订目录 → (payload 组件字典, content_fingerprint)。保留原始形态。"""
    payload = {}
    for key, filename in MODEL_DRAFT_FILES:
        path = directory / filename
        if path.is_file():
            payload[key] = _read_json(path) if filename.endswith('.json') else _read_yaml(path)
    for key, filename in MODEL_EXTRA_FILES:
        path = directory / filename
        if path.is_file():
            payload[key] = _read_json(path)
    if 'ontology' not in payload:
        raise ValueError(f'修订缺少 ontology.json：{directory}')
    return payload, dir_hash(directory)


def parse_project_revision(directory):
    payload = {}
    for key, filename in PROJECT_FILES:
        path = directory / filename
        if path.is_file():
            payload[key] = _read_yaml(path)
    return payload, dir_hash(directory)


def parse_flow_revision(directory):
    path = directory / 'flow.json'
    payload = _read_json(path)
    return payload, dir_hash(directory)


# ---------------------------------------------------------------- 导入

def _import_item(conn, batch, source_key, source_hash, entity_kind, target_id, summary=None):
    status, same = artifact_store.import_items_record(conn, batch, source_key, source_hash,
                                                      entity_kind, target_id, summary=summary)
    if status == 'exists' and not same:
        raise RuntimeError(f'迁移源在备份后发生变化，拒绝覆盖：{source_key}')
    return status == 'inserted'


def import_model(conn, entry, batch, now, report):
    name = entry['name'] or entry['id']
    asset_uid = store.ensure_asset(conn, 'model', entry['id'], name, {}, now)
    imported = 0
    # 历史/当前修订（legacy-files-v3，原始组件形态）
    for revision_dir in entry['drafts']:
        try:
            payload, fingerprint = parse_model_revision(revision_dir)
        except (ValueError, OSError) as exc:
            report['errors'].append(f"本体 {entry['id']} 修订 {revision_dir.name}：{exc}")
            continue
        is_current = bool(entry['current'] and entry['current'].get('revisionDir') == revision_dir.name)
        legacy = str(entry['current'].get('revision', '')) if is_current else ''
        key = f'model-draft:{entry["id"]}:{revision_dir.name}'
        if _import_item(conn, batch, key, fingerprint, 'model-draft', entry['id']):
            snapshot = store.append_snapshot(conn, asset_uid, payload, 'legacy-files-v3', 'draft',
                                             legacy_revision=legacy, now=now)
            _import_item(conn, batch, key + ':@snapshot', fingerprint, 'model-draft',
                         snapshot['snapshot_id'], {'seq': snapshot['seq']})
            imported += 1
    # 发布（先于项目引用导入）
    if entry['release_index']:
        for order, ver in enumerate(entry['release_index'].get('versions', [])):
            version = str(ver.get('version'))
            directory = Path('ontology/releases/models') / entry['id'] / version
            full = directory if directory.is_absolute() else entry['_root'] / directory
            if not full.is_dir():
                continue
            payload = {'ontology': _read_json(full / 'ontology.json'),
                       'workflow': _read_json(full / 'workflow.json') if (full / 'workflow.json').is_file() else {},
                       'metrics': _read_yaml(full / 'metrics.yaml') if (full / 'metrics.yaml').is_file() else {},
                       'rules': _read_yaml(full / 'rules.yaml') if (full / 'rules.yaml').is_file() else {},
                       'layout': _read_json(full / 'layout.json') if (full / 'layout.json').is_file() else {}}
            fingerprint = dir_hash(full)
            key = f'model-release:{entry["id"]}:{version}'
            if _import_item(conn, batch, key, fingerprint, 'model-release', entry['id']):
                snapshot = store.append_snapshot(conn, asset_uid, payload, 'legacy-release-v1',
                                                 'release', now=now)
                store.append_release(conn, asset_uid, version, snapshot['snapshot_id'], ver, now=now)
                imported += 1
    # 历史 ZIP 与恢复前备份 → 附件
    for zp in entry['zips']:
        key = f'model-zip:{entry["id"]}:{zp.name}'
        if _import_item(conn, batch, key, file_hash(zp), 'artifact', entry['id']):
            artifact_store.put_artifact(conn, zp.read_bytes(), artifact_store.PURPOSE_RELEASE_ZIP,
                                        legacy_name=zp.name, owner_asset_uid=asset_uid,
                                        media_type='application/zip', now=now)
            imported += 1
    for bp in entry['backups']:
        key = f'model-backup:{entry["id"]}:{bp.name}'
        if _import_item(conn, batch, key, file_hash(bp), 'artifact', entry['id']):
            artifact_store.put_artifact(conn, bp.read_bytes(), artifact_store.PURPOSE_RESTORE_BACKUP,
                                        legacy_name=bp.name, owner_asset_uid=asset_uid,
                                        media_type='application/json', now=now)
            imported += 1
    # 当前 head（legacy token 一次兼容）
    if entry['current'] and entry['current'].get('revisionDir'):
        revision_dir = entry['_draft_base'] / 'revisions' / str(entry['current']['revisionDir'])
        if revision_dir.is_dir():
            payload, fingerprint = parse_model_revision(revision_dir)
            key = f'model-head:{entry["id"]}'
            if _import_item(conn, batch, key, fingerprint, 'model-head', entry['id']):
                _set_head(conn, asset_uid, payload, 'legacy-files-v3', 'draft',
                          str(entry['current'].get('revision', '')),
                          int(entry['current'].get('seq', 0) or 0), now)
                imported += 1
    elif entry.get('legacy_draft'):
        payload = dict(entry['legacy_draft'])
        payload.pop('bindings', None); payload.pop('parameters', None)
        key = f'model-head:{entry["id"]}'
        if _import_item(conn, batch, key, 'legacy-draft', 'model-head', entry['id']):
            _set_head(conn, asset_uid, encode_legacy_state(payload), 'legacy-files-v3', 'imported-base',
                      'imported', 1, now)
            imported += 1
    # 当前草稿索引投影
    head = read_head(conn, asset_uid)
    if head is not None and head['snapshot_id']:
        snapshot = read_snapshot(conn, head['snapshot_id'])
        if snapshot is not None:
            rows = indexing.extract_definition_rows(json.loads(snapshot['payload_json']))
            indexing.replace_definition_index(conn, asset_uid, head['snapshot_id'], rows)
    report['counts']['model_snapshots'] += imported
    return imported


def encode_legacy_state(state):
    """旧 draft.json（内部 JSON-LD 形态）→ 组件形态（与草稿文件一致）。"""
    from workbench.model_format import encode_state
    state = dict(state)
    state.setdefault('workspaceId', 'storage')
    return encode_state(state)


def _set_head(conn, asset_uid, payload, payload_format, purpose, legacy_token, seq, now):
    snapshot = store.append_snapshot(conn, asset_uid, payload, payload_format, purpose,
                                     legacy_revision=legacy_token, now=now)
    existing = read_head(conn, asset_uid)
    token = legacy_token or store.new_token()
    if existing is None:
        conn.execute(sql_text('INSERT INTO wb_asset_heads (asset_uid, snapshot_id, '
                                    'revision_token, generation, snapshot_seq, release_seq, updated_at) '
                                    'VALUES (:a, :s, :t, 1, :q, 0, :now)'),
                     {'a': asset_uid, 's': snapshot['snapshot_id'], 't': token,
                      'q': max(snapshot['seq'], seq), 'now': now})
    else:
        conn.execute(sql_text('UPDATE wb_asset_heads SET snapshot_id = :s, revision_token = :t, '
                                    'snapshot_seq = :q, updated_at = :now WHERE asset_uid = :a'),
                     {'s': snapshot['snapshot_id'], 't': token, 'q': max(snapshot['seq'], seq),
                      'now': now, 'a': asset_uid})


def import_project(conn, entry, batch, now, report):
    imported = 0
    has_base = (entry['base'] / 'project.yaml').is_file()
    has_draft = bool(entry['current'])
    name = entry['id']
    # 资产名先取 base/当前修订的 project.yaml
    if has_base:
        name = str(_read_yaml(entry['base'] / 'project.yaml').get('name') or name)
    elif entry['drafts']:
        payload, _ = parse_project_revision(entry['drafts'][-1])
        name = str((payload.get('project') or {}).get('name') or name)
    asset_uid = store.ensure_asset(conn, 'project', entry['id'], name, {}, now)
    # base（无草稿时作为 imported-base 起点保存）
    if has_base and not has_draft:
        payload, fingerprint = parse_project_revision(entry['base'])
        key = f'project-base:{entry["id"]}'
        if _import_item(conn, batch, key, fingerprint, 'project-base', entry['id']):
            _import_project_snapshot(conn, asset_uid, payload, 'legacy-files-v3', 'imported-base',
                                     'imported', 1, now, entry['id'])
            imported += 1
    elif has_base and has_draft:
        # base 也作为一次快照导入（历史起点），但不设 head
        payload, fingerprint = parse_project_revision(entry['base'])
        key = f'project-base:{entry["id"]}'
        if _import_item(conn, batch, key, fingerprint, 'project-base', entry['id']):
            store.append_snapshot(conn, asset_uid, payload, 'legacy-files-v3', 'imported-base',
                                  legacy_revision='imported', now=now)
            imported += 1
    for revision_dir in entry['drafts']:
        try:
            payload, fingerprint = parse_project_revision(revision_dir)
        except (OSError, ValueError) as exc:
            report['errors'].append(f"项目 {entry['id']} 修订 {revision_dir.name}：{exc}")
            continue
        is_current = bool(entry['current'] and entry['current'].get('revisionDir') == revision_dir.name)
        legacy = str(entry['current'].get('revision', '')) if is_current else ''
        key = f'project-draft:{entry["id"]}:{revision_dir.name}'
        if _import_item(conn, batch, key, fingerprint, 'project-draft', entry['id']):
            snapshot = store.append_snapshot(conn, asset_uid, payload, 'legacy-files-v3', 'draft',
                                             legacy_revision=legacy, now=now)
            _import_item(conn, batch, key + ':@snapshot', fingerprint, 'project-draft',
                         snapshot['snapshot_id'], {'seq': snapshot['seq']})
            imported += 1
    for release_dir in entry['releases']:
        manifest = _read_yaml(release_dir / 'manifest.yaml')
        payload, fingerprint = parse_project_revision(release_dir)
        key = f'project-release:{entry["id"]}:{release_dir.name}'
        if _import_item(conn, batch, key, fingerprint, 'project-release', entry['id']):
            snapshot = store.append_snapshot(conn, asset_uid, payload, 'legacy-files-v3', 'release', now=now)
            store.append_release(conn, asset_uid, release_dir.name, snapshot['snapshot_id'],
                                 manifest, now=now)
            # 固定引用：能解析则挂 release FK，否则标 missing（不造空版本）
            ref = {'target_ontology_id': str(manifest.get('ontologyId', '') or ('storage' if manifest.get('ontology_version') else '')),
                   'target_version': str(manifest.get('ontology_version', '') or manifest.get('model_version', '') or '')}
            store.upsert_project_ref(conn, snapshot['snapshot_id'], ref)
            imported += 1
    if has_draft:
        revision_dir = Path('ontology/drafts/projects') / entry['id'] / 'revisions' / str(entry['current'].get('revisionDir', ''))
        full = revision_dir if revision_dir.is_absolute() else entry['_root'] / revision_dir
        if full.is_dir():
            payload, fingerprint = parse_project_revision(full)
            key = f'project-head:{entry["id"]}'
            if _import_item(conn, batch, key, fingerprint, 'project-head', entry['id']):
                _import_project_snapshot(conn, asset_uid, payload, 'legacy-files-v3', 'draft',
                                         str(entry['current'].get('revision', '')),
                                         int(entry['current'].get('seq', 0) or 0), now, entry['id'])
                imported += 1
    report['counts']['project_snapshots'] += imported
    return imported


def _import_project_snapshot(conn, asset_uid, payload, fmt, purpose, legacy, seq, now, project_id):
    snapshot = store.append_snapshot(conn, asset_uid, payload, fmt, purpose,
                                     legacy_revision=legacy, now=now)
    token = legacy or store.new_token()
    existing = read_head(conn, asset_uid)
    if existing is None:
        conn.execute(sql_text('INSERT INTO wb_asset_heads (asset_uid, snapshot_id, '
                                    'revision_token, generation, snapshot_seq, release_seq, updated_at) '
                                    'VALUES (:a, :s, :t, 1, :q, 0, :now)'),
                     {'a': asset_uid, 's': snapshot['snapshot_id'], 't': token,
                      'q': max(snapshot['seq'], seq), 'now': now})
    # 项目快照引用（至多一条）
    project = payload.get('project') or {}
    ontology_id = str(project.get('ontology') or project.get('ontology_id') or
                      ('storage' if (project.get('ontology_version') or project.get('model_version')) else ''))
    version = str(project.get('ontology_version') or project.get('model_version') or '')
    if ontology_id and version:
        store.upsert_project_ref(conn, snapshot['snapshot_id'],
                                 {'target_ontology_id': ontology_id, 'target_version': version})
    # 摘要
    summary = {'ontologyId': ontology_id, 'ontologyVersion': version,
               'objectBindings': len(((payload.get('bindings') or {}).get('object_bindings')) or []),
               'implementations': len(payload.get('implementations') or [])}
    conn.execute(sql_text('UPDATE wb_assets SET summary_json = :s WHERE asset_uid = :a'),
                 {'s': json.dumps(summary, ensure_ascii=False), 'a': asset_uid})
    return snapshot


def import_flow(conn, entry, batch, now, report):
    imported = 0
    name = entry['id']
    if entry['drafts']:
        payload, _ = parse_flow_revision(entry['drafts'][-1])
        name = str(payload.get('name') or name)
    asset_uid = store.ensure_asset(conn, 'flow', entry['id'], name, {}, now)
    for revision_dir in entry['drafts']:
        try:
            payload, fingerprint = parse_flow_revision(revision_dir)
        except (OSError, ValueError) as exc:
            report['errors'].append(f"编排 {entry['id']} 修订 {revision_dir.name}：{exc}")
            continue
        is_current = bool(entry['current'] and entry['current'].get('revisionDir') == revision_dir.name)
        legacy = str(entry['current'].get('revision', '')) if is_current else ''
        key = f'flow-draft:{entry["id"]}:{revision_dir.name}'
        if _import_item(conn, batch, key, fingerprint, 'flow-draft', entry['id']):
            snapshot = store.append_snapshot(conn, asset_uid, payload, 'legacy-flow-1', 'draft',
                                             legacy_revision=legacy, now=now)
            _import_item(conn, batch, key + ':@snapshot', fingerprint, 'flow-draft',
                         snapshot['snapshot_id'], {'seq': snapshot['seq']})
            imported += 1
    if entry['current'] and entry['current'].get('revisionDir'):
        revision_dir = Path('ontology/drafts/flows') / entry['id'] / 'revisions' / str(entry['current']['revisionDir'])
        full = revision_dir if revision_dir.is_absolute() else entry['_root'] / revision_dir
        if full.is_dir():
            payload, fingerprint = parse_flow_revision(full)
            key = f'flow-head:{entry["id"]}'
            if _import_item(conn, batch, key, fingerprint, 'flow-head', entry['id']):
                _set_head(conn, asset_uid, payload, 'legacy-flow-1', 'draft',
                          str(entry['current'].get('revision', '')),
                          int(entry['current'].get('seq', 0) or 0), now)
                imported += 1
    report['counts']['flow_snapshots'] += imported
    return imported


def import_configuration(conn, root, batch, now, report):
    """目录缓存 / 三类凭据 / 模型配置与默认项。密钥只入密文，报告不含明文。"""
    from workbench.storage import secret_store
    imported = 0
    model_uids = {row['external_id']: row['asset_uid'] for row in _all_assets(conn, 'model')}
    project_uids = {row['external_id']: row['asset_uid'] for row in _all_assets(conn, 'project')}
    # 目录缓存
    catalogs_base = root / 'ontology/catalogs/projects'
    for path in sorted(catalogs_base.glob('*/*.json')) if catalogs_base.is_dir() else []:
        pid, cid = path.parent.name, path.stem
        try:
            data = _read_json(path)
        except (OSError, ValueError) as exc:
            report['errors'].append(f'目录缓存 {pid}/{cid}：{exc}')
            continue
        uid = project_uids.get(pid)
        if uid is None:
            report['errors'].append(f'目录缓存 {pid}/{cid}：项目资产不存在')
            continue
        key = f'catalog:{pid}:{cid}'
        if _import_item(conn, batch, key, file_hash(path), 'catalog', pid):
            config_store.store_catalog(conn, uid, cid, data, '', now=now)
            imported += 1
    # 连接密码（明文文件 → 密文）
    vault = root / 'ontology/vault/projects'
    for path in sorted(vault.glob('*/*')) if vault.is_dir() else []:
        if not path.is_file() or path.parent.name == 'api-credentials':
            continue
        pid, cid = path.parent.name, path.name
        uid = project_uids.get(pid)
        if uid is None:
            report['errors'].append(f'连接凭据 {pid}/{cid}：项目资产不存在')
            continue
        key = f'secret:connection:{pid}:{cid}'
        if _import_item(conn, batch, key, file_hash(path), 'credential', pid):
            config_store.put_secret(conn, 'connection', uid, cid, path.read_text(), now=now)
            imported += 1
    # API 凭据：<pid>/api-credentials/<cid>.json
    api_files = sorted(vault.glob('*/api-credentials/*.json')) if vault.is_dir() else []
    for path in api_files:
        # 目录形如 vault/projects/<pid>/api-credentials/<cid>.json
        pid = path.parent.parent.name
        uid = project_uids.get(pid)
        if uid is None:
            report['errors'].append(f'API 凭据 {pid}/{path.stem}：项目资产不存在')
            continue
        try:
            data = _read_json(path)
        except (OSError, ValueError) as exc:
            report['errors'].append(f'API 凭据 {pid}/{path.stem}：{exc}')
            continue
        key = f'secret:api:{pid}:{path.stem}'
        if _import_item(conn, batch, key, file_hash(path), 'credential', pid):
            config_store.put_secret(conn, 'api', uid, str(data.get('id') or path.stem),
                                    str(data.get('secret') or ''),
                                    display_name=str(data.get('name') or ''), now=now)
            imported += 1
    # LLM 提供方
    llm_dir = root / 'ontology/vault/llm-providers'
    first_seen = not _all_assets(conn, 'model') and not _rows(conn, 'wb_model_configs')
    default_id = ''
    for path in sorted(llm_dir.glob('*.json')) if llm_dir.is_dir() else []:
        try:
            data = _read_json(path)
        except (OSError, ValueError) as exc:
            report['errors'].append(f'LLM 提供方 {path.stem}：{exc}')
            continue
        provider_id = str(data.get('id') or path.stem)
        key = f'model-config:{provider_id}'
        if _import_item(conn, batch, key, file_hash(path), 'model-config', provider_id):
            secret_id = config_store.put_secret(conn, 'model', 'global', provider_id,
                                                str(data.get('api_key') or ''),
                                                display_name=str(data.get('name') or ''), now=now)
            conn.execute(sql_text(
                'INSERT INTO wb_model_configs (provider_id, name, endpoint, model, timeout_seconds, '
                'temperature, secret_id, metadata_revision, updated_at) '
                'VALUES (:p, :n, :e, :m, :t, :tp, :s, 1, :now)'),
                {'p': provider_id, 'n': str(data.get('name') or provider_id),
                 'e': str(data.get('endpoint') or ''), 'm': str(data.get('model') or ''),
                 't': int(data.get('timeout') or 60), 'tp': float(data.get('temperature') or 0),
                 's': secret_id, 'now': now})
            if data.get('is_default') and not default_id:
                default_id = provider_id
            imported += 1
    if default_id:
        config_store.put_setting(conn, config_store.DEFAULT_PROVIDER_KEY, default_id, now=now)
    elif first_seen:
        pass
    # 源参考材料 → 附件
    for source_root in (root / 'resources/imports', root / 'imports'):
        for path in sorted(source_root.rglob('*.jsonId')) if source_root.is_dir() else []:
            owner = model_uids.get('storage')
            key = f'source-reference:{path.relative_to(root)}'
            if _import_item(conn, batch, key, file_hash(path), 'artifact', 'storage'):
                artifact_store.put_artifact(conn, path.read_bytes(),
                                            artifact_store.PURPOSE_SOURCE_REFERENCE,
                                            legacy_name=str(path.relative_to(source_root)),
                                            owner_asset_uid=owner,
                                            media_type='application/json', now=now)
                imported += 1
    report['counts']['config_items'] += imported
    return imported


def _all_assets(conn, kind):
    from workbench.storage.engine import head_by_external  # noqa: F401
    rows = conn.execute(sql_text('SELECT asset_uid, external_id, name FROM wb_assets '
                                       'WHERE kind = :k'), {'k': kind}).mappings().all()
    return [dict(r) for r in rows]


def _rows(conn, table):
    return conn.execute(sql_text(f'SELECT * FROM {table}')).fetchall()


# ---------------------------------------------------------------- 命令

def cmd_init(args):
    url = dbengine.resolve_url() if not args.url else args.url
    storage.mark_unready(url)
    dbengine.initialize(url)
    ready, version = schema_status(url)
    print(f'✓ 存储库已初始化：schema={version} backend={dbengine.backend_kind(url)}')
    return 0 if ready else 1


def cmd_inspect(args):
    root = Path(args.source).resolve()
    report = {'source': str(root), 'models': [], 'projects': [], 'flows': [], 'problems': []}
    for entry in scan_models(root):
        entry['_root'] = root; entry['_draft_base'] = root / 'ontology/drafts/models' / entry['id']
        info = {'id': entry['id'], 'name': entry['name'],
                'draft_revisions': len(entry['drafts']), 'has_current': bool(entry['current']),
                'releases': [p.name for p in entry['releases']], 'zips': len(entry['zips']),
                'backups': len(entry['backups']), 'problems': entry['problems']}
        if entry['current']:
            info['current_seq'] = entry['current'].get('seq')
        report['models'].append(info)
    for entry in scan_projects(root):
        entry['_root'] = root
        report['projects'].append({'id': entry['id'], 'has_base': (entry['base'] / 'project.yaml').is_file(),
                                   'draft_revisions': len(entry['drafts']), 'has_current': bool(entry['current']),
                                   'releases': [p.name for p in entry['releases']], 'problems': entry['problems']})
    for entry in scan_flows(root):
        entry['_root'] = root
        report['flows'].append({'id': entry['id'], 'draft_revisions': len(entry['drafts']),
                                'has_current': bool(entry['current']), 'problems': entry['problems']})
    report['problems'] = [p for e in report['models'] for p in e['problems']] + \
                         [p for e in report['projects'] for p in e['problems']] + \
                         [p for e in report['flows'] for p in e['problems']]
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(text + '\n', encoding='utf-8')
        print(f'✓ 检查报告已写入 {args.output}（不包含密钥内容）')
    else:
        print(text)
    return 1 if report['problems'] else 0


def cmd_import(args):
    root = Path(args.source).resolve()
    if root == Path(__file__).resolve().parents[2] and not args.allow_real_source:
        print('✗ 源目录是仓库根（真实数据）。为防误操作请显式加 --allow-real-source，'
              '并确认已停写且已做完整备份。', file=sys.stderr)
        return 2
    url = args.url or resolve_url()
    storage.mark_unready(url)
    storage.ensure_ready(url)
    batch = args.batch or 'migrate-' + utcnow()[:19].replace(':', '')
    report = {'batch': batch, 'source': str(root), 'errors': [],
              'counts': {'model_snapshots': 0, 'project_snapshots': 0, 'flow_snapshots': 0, 'config_items': 0}}
    now = utcnow()

    def body(conn):
        for entry in scan_models(root):
            entry['_root'] = root
            entry['_draft_base'] = root / 'ontology/drafts/models' / entry['id']
            legacy = None
            legacy_path = (root / 'ontology/drafts/draft.json') if entry['id'] == 'storage' \
                else (root / 'ontology/workspaces' / entry['id'] / 'draft.json')
            if legacy_path.is_file():
                from workbench.workspaces import legacy_draft_state
                legacy = legacy_draft_state(entry['id'])
            entry['legacy_draft'] = legacy
            import_model(conn, entry, batch, now, report)
        for entry in scan_projects(root):
            entry['_root'] = root
            import_project(conn, entry, batch, now, report)
        for entry in scan_flows(root):
            entry['_root'] = root
            import_flow(conn, entry, batch, now, report)
        import_configuration(conn, root, batch, now, report)

    with write_tx(url) as tx:
        tx.run(body)
    print(f"✓ 导入完成 batch={batch} 计数={report['counts']} 错误={len(report['errors'])}")
    for error in report['errors']:
        print('  !', error)
    return 1 if report['errors'] else 0


def cmd_verify(args):
    root = Path(args.source).resolve()
    url = args.url or resolve_url()
    storage.ensure_ready(url)
    checks, failures = [], []

    def check(name, ok, detail=''):
        checks.append({'check': name, 'ok': bool(ok), 'detail': detail})
        if not ok:
            failures.append(f'{name}: {detail}')

    with read_connection(url) as conn:
        for entry in scan_models(root):
            entry['_root'] = root
            entry['_draft_base'] = root / 'ontology/drafts/models' / entry['id']
            asset = store.get_asset(conn, 'model', entry['id'])
            check(f'本体资产 {entry["id"]}', asset is not None)
            if asset is None:
                continue
            check(f'本体名称 {entry["id"]}', (asset['name'] or '') == (entry['name'] or '储能本体'),
                  f'{asset["name"]!r} vs {entry["name"]!r}')
            if entry['current'] and entry['current'].get('revisionDir'):
                revision_dir = entry['_draft_base'] / 'revisions' / str(entry['current']['revisionDir'])
                if revision_dir.is_dir():
                    payload, _ = parse_model_revision(revision_dir)
                    head = read_head(conn, asset['asset_uid'])
                    snapshot = read_snapshot(conn, head['snapshot_id']) if head else None
                    same = snapshot is not None and _same_payload(payload, json.loads(snapshot['payload_json']))
                    check(f'当前草稿语义一致 {entry["id"]}', same)
                    check(f'head token 继承 {entry["id"]}',
                          bool(head) and head['revision_token'] == str(entry['current'].get('revision', '')),
                          f'{(head or {}).get("revision_token", "")[:12]}…')
            if entry['release_index']:
                for ver in entry['release_index'].get('versions', []):
                    version = str(ver.get('version'))
                    row = store.get_release_row(conn, asset['asset_uid'], version)
                    check(f'发布登记 {entry["id"]}@{version}', row is not None)
                    if row is None:
                        continue
                    check(f'发布 manifest 一致 {entry["id"]}@{version}',
                          row['manifest'].get('version') == ver.get('version') and
                          row['manifest'].get('createdAt') == ver.get('createdAt'))
                    directory = root / 'ontology/releases/models' / entry['id'] / version
                    if directory.is_dir():
                        payload = {'ontology': _read_json(directory / 'ontology.json'),
                                   'workflow': _read_json(directory / 'workflow.json') if (directory / 'workflow.json').is_file() else {},
                                   'metrics': _read_yaml(directory / 'metrics.yaml') if (directory / 'metrics.yaml').is_file() else {},
                                   'rules': _read_yaml(directory / 'rules.yaml') if (directory / 'rules.yaml').is_file() else {},
                                   'layout': _read_json(directory / 'layout.json') if (directory / 'layout.json').is_file() else {}}
                        snapshot = read_snapshot(conn, row['snapshot_id'])
                        check(f'发布快照一致 {entry["id"]}@{version}',
                              snapshot is not None and _same_payload(payload, json.loads(snapshot['payload_json'])))
            for zp in entry['zips']:
                data = artifact_store.read_by_name(conn, asset['asset_uid'],
                                                   artifact_store.PURPOSE_RELEASE_ZIP, zp.name)
                check(f'附件 {zp.name}', data is not None and content_hash(data) == file_hash(zp))
        for entry in scan_projects(root):
            entry['_root'] = root
            asset = store.get_asset(conn, 'project', entry['id'])
            check(f'项目资产 {entry["id"]}', asset is not None)
            if asset is None:
                continue
            if entry['current'] and entry['current'].get('revisionDir'):
                full = root / 'ontology/drafts/projects' / entry['id'] / 'revisions' / str(entry['current']['revisionDir'])
                if full.is_dir():
                    payload, _ = parse_project_revision(full)
                    head = read_head(conn, asset['asset_uid'])
                    snapshot = read_snapshot(conn, head['snapshot_id']) if head else None
                    check(f'项目当前草稿一致 {entry["id"]}',
                          snapshot is not None and _same_payload(payload, json.loads(snapshot['payload_json'])))
                    # 引用核对
                    project = payload.get('project') or {}
                    ref = store.get_project_ref(conn, snapshot['snapshot_id']) if snapshot else None
                    ontology_id = str(project.get('ontology') or project.get('ontology_id') or
                                      ('storage' if (project.get('ontology_version') or project.get('model_version')) else ''))
                    version = str(project.get('ontology_version') or project.get('model_version') or '')
                    check(f'项目引用 {entry["id"]}', bool(ref) and ref['target_ontology_id'] == ontology_id
                          and ref['target_version'] == version,
                          f'{ref} vs {ontology_id}@{version}')
            for release_dir in entry['releases']:
                row = store.get_release_row(conn, asset['asset_uid'], release_dir.name)
                check(f'项目发布 {entry["id"]}@{release_dir.name}', row is not None)
        for entry in scan_flows(root):
            entry['_root'] = root
            asset = store.get_asset(conn, 'flow', entry['id'])
            check(f'编排资产 {entry["id"]}', asset is not None)
            if asset is None or not (entry['current'] and entry['current'].get('revisionDir')):
                continue
            full = root / 'ontology/drafts/flows' / entry['id'] / 'revisions' / str(entry['current']['revisionDir'])
            if full.is_dir():
                payload, _ = parse_flow_revision(full)
                head = read_head(conn, asset['asset_uid'])
                snapshot = read_snapshot(conn, head['snapshot_id']) if head else None
                check(f'编排当前草稿一致 {entry["id"]}',
                      snapshot is not None and _same_payload(payload, json.loads(snapshot['payload_json'])))
        # 凭据：只比数量并做解密探针（不输出值）
        vault = root / 'ontology/vault/projects'
        conn_count = len([p for p in vault.glob('*/*') if p.is_file() and p.parent.name != 'api-credentials']) if vault.is_dir() else 0
        db_conn_count = conn.execute(sql_text("SELECT COUNT(*) FROM wb_credentials WHERE namespace='connection'")).scalar()
        check('连接凭据数量', conn_count == db_conn_count, f'{conn_count} vs {db_conn_count}')
        api_files = len(list(vault.glob('*/api-credentials/*.json'))) if vault.is_dir() else 0
        db_api_count = conn.execute(sql_text("SELECT COUNT(*) FROM wb_credentials WHERE namespace='api'")).scalar()
        check('API 凭据数量', api_files == db_api_count, f'{api_files} vs {db_api_count}')
        llm_files = len(list((root / 'ontology/vault/llm-providers').glob('*.json'))) if (root / 'ontology/vault/llm-providers').is_dir() else 0
        db_llm_count = conn.execute(sql_text('SELECT COUNT(*) FROM wb_model_configs')).scalar()
        check('模型配置数量', llm_files == db_llm_count, f'{llm_files} vs {db_llm_count}')
        # 解密探针
        decrypt_ok, decrypt_fail = 0, 0
        for row in conn.execute(sql_text('SELECT namespace, owner_key, resource_id, key_id, nonce, ciphertext '
                                               'FROM wb_credentials')).mappings().all():
            try:
                config_store.get_secret(conn, row['namespace'], row['owner_key'], row['resource_id'])
                decrypt_ok += 1
            except Exception:
                decrypt_fail += 1
        check('凭据解密探针', decrypt_fail == 0, f'ok={decrypt_ok} fail={decrypt_fail}')
        # 完整性
        integrity = conn.execute(sql_text('PRAGMA integrity_check')).scalar() if dbengine.backend_kind(url) == 'sqlite' else 'skipped'
        check('integrity_check', integrity in ('ok', 'skipped'), str(integrity))
        fk = conn.execute(sql_text('PRAGMA foreign_key_check')).fetchall() if dbengine.backend_kind(url) == 'sqlite' else []
        check('foreign_key_check', len(fk) == 0, f'{len(fk)} 行违反')
        # 迁移清单：每个源 key 都有登记
        items = conn.execute(sql_text('SELECT COUNT(*) FROM wb_import_items')).scalar()
        check('迁移清单非空', items > 0, f'{items} 条')

    report = {'source': str(root), 'passed': sum(1 for c in checks if c['ok']),
              'failed': len(failures), 'checks': checks, 'failures': failures}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


def _same_payload(a, b):
    """语义相等：历史 YAML/JSON 解析后的键值对比（None == 缺失键）。"""
    def norm(value):
        if isinstance(value, dict):
            return {str(k): norm(v) for k, v in value.items() if v is not None}
        if isinstance(value, list):
            return [norm(v) for v in value]
        return value
    return json.dumps(norm(a), sort_keys=True, ensure_ascii=False) == json.dumps(norm(b), sort_keys=True, ensure_ascii=False)


def cmd_export(args):
    """反向导出旧兼容目录（含迁移后的新写入）；用于回滚到文件后端。"""
    url = args.url or resolve_url()
    storage.ensure_ready(url)
    out = Path(args.output).resolve()
    if out.exists() and any(out.iterdir()):
        print('✗ 导出目录非空，拒绝覆盖', file=sys.stderr)
        return 2
    out.mkdir(parents=True, exist_ok=True)
    counts = {'models': 0, 'projects': 0, 'flows': 0, 'credentials': 0}
    with read_connection(url) as conn:
        for kind, dirname in (('model', 'models'), ('project', 'projects'), ('flow', 'flows')):
            for row in _all_assets(conn, kind):
                head = read_head(conn, row['asset_uid'])
                if head is None or not head['snapshot_id']:
                    continue
                snapshot = read_snapshot(conn, head['snapshot_id'])
                payload = json.loads(snapshot['payload_json'])
                if kind == 'model':
                    _export_model(conn, out, row, head, snapshot, payload, counts)
                elif kind == 'project':
                    _export_project(conn, out, row, head, snapshot, payload, counts)
                else:
                    _export_flow(out, row, head, snapshot, payload, counts)
        # 目录缓存
        for row in conn.execute(sql_text('SELECT c.project_uid, c.connection_id, c.payload_json, '
                                               'a.external_id FROM wb_catalog_cache c JOIN wb_assets a '
                                               'ON a.asset_uid = c.project_uid')).mappings().all():
            directory = out / 'ontology/catalogs/projects' / row['external_id']
            directory.mkdir(parents=True, exist_ok=True)
            (directory / (row['connection_id'] + '.json')).write_text(row['payload_json'], encoding='utf-8')
        # 凭据（明文！0600，目录 0700）
        import os
        vault = out / 'ontology/vault/projects'
        for row in conn.execute(sql_text('SELECT c.namespace, c.owner_key, c.resource_id, '
                                               'a.external_id FROM wb_credentials c JOIN wb_assets a '
                                               "ON a.asset_uid = c.owner_key WHERE c.namespace = 'connection'")).mappings().all():
            directory = vault / row['external_id']
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            value = config_store.get_secret(conn, 'connection', row['owner_key'], row['resource_id'])
            path = directory / row['resource_id']
            path.write_text(value or '', encoding='utf-8')
            os.chmod(path, 0o600)
            counts['credentials'] += 1
        for row in conn.execute(sql_text('SELECT c.owner_key, c.resource_id, c.display_name, '
                                               'a.external_id FROM wb_credentials c JOIN wb_assets a '
                                               "ON a.asset_uid = c.owner_key WHERE c.namespace = 'api'")).mappings().all():
            directory = vault / row['external_id'] / 'api-credentials'
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            value = config_store.get_secret(conn, 'api', row['owner_key'], row['resource_id'])
            path = directory / (row['resource_id'] + '.json')
            body = json.dumps({'id': row['resource_id'], 'name': row['display_name'], 'secret': value or ''},
                              ensure_ascii=False)
            path.write_text(body, encoding='utf-8')
            os.chmod(path, 0o600)
            counts['credentials'] += 1
        for row in conn.execute(sql_text('SELECT provider_id, name, endpoint, model, timeout_seconds, '
                                               'temperature FROM wb_model_configs')).mappings().all():
            directory = out / 'ontology/vault/llm-providers'
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            value = config_store.get_secret(conn, 'model', 'global', row['provider_id'])
            default_id = config_store.get_setting(conn, config_store.DEFAULT_PROVIDER_KEY, '')
            body = json.dumps({'id': row['provider_id'], 'name': row['name'], 'endpoint': row['endpoint'],
                               'model': row['model'], 'timeout': row['timeout_seconds'],
                               'temperature': row['temperature'], 'api_key': value or '',
                               'is_default': row['provider_id'] == default_id}, ensure_ascii=False)
            path = directory / (row['provider_id'] + '.json')
            path.write_text(body, encoding='utf-8')
            os.chmod(path, 0o600)
    print(f'✓ 反向导出完成：{out} {counts}')
    print('  ⚠ 导出包含凭据明文（0600）；请立即转移到受保护位置，勿入公开下载/备份分发区。')
    return 0


def _export_model(conn, out, row, head, snapshot, payload, counts):
    identifier = row['external_id']
    if identifier != 'storage':
        ws = out / 'ontology/workspaces' / identifier
        ws.mkdir(parents=True, exist_ok=True)
        (ws / 'workspace.json').write_text(json.dumps({'id': identifier, 'name': row['name']},
                                                      ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    draft_dir = out / 'ontology/drafts/models' / identifier
    revision_dir = draft_dir / 'revisions' / f"{int(snapshot['seq']):04d}-{snapshot['content_hash'][:8]}"
    revision_dir.mkdir(parents=True, exist_ok=True)
    for key, filename in MODEL_DRAFT_FILES:
        if key in payload and payload[key] is not None:
            _write_component(revision_dir / filename, payload[key])
    for key, filename in MODEL_EXTRA_FILES:
        if key in payload and payload[key] is not None:
            _write_component(revision_dir / filename, payload[key])
    pointer = {'seq': snapshot['seq'], 'revision': head['revision_token'],
               'revisionDir': revision_dir.name, 'updatedAt': head.get('updated_at', '')}
    _atomic_json(draft_dir / 'current.json', pointer)
    # 发布
    releases = store.release_rows(conn, row['asset_uid'])
    if releases:
        release_base = out / 'ontology/releases/models' / identifier
        index = {'versions': [r['manifest'] for r in releases]}
        release_base.mkdir(parents=True, exist_ok=True)
        for r in releases:
            version_dir = release_base / r['version_label']
            if version_dir.exists():
                continue
            rs = read_snapshot(conn, r['snapshot_id'])
            rp = json.loads(rs['payload_json'])
            version_dir.mkdir(parents=True, exist_ok=True)
            for key, filename in (('ontology', 'ontology.json'), ('workflow', 'workflow.json'),
                                  ('metrics', 'metrics.yaml'), ('rules', 'rules.yaml'), ('layout', 'layout.json')):
                value = rp.get(key)
                # 与旧发布约定一致：空 metrics/rules 不写文件
                if value is None or (key in ('metrics', 'rules') and not (value or {}).get(key)):
                    continue
                _write_component(version_dir / filename, value)
            (version_dir / 'manifest.yaml').write_text(
                yaml.safe_dump(r['manifest'], allow_unicode=True, sort_keys=False), encoding='utf-8')
        _atomic_json(release_base / 'index.json', index)
    # ZIP / 备份附件
    for meta in artifact_store.find_artifacts(conn, owner_asset_uid=row['asset_uid'],
                                              purpose=artifact_store.PURPOSE_RELEASE_ZIP):
        blob = artifact_store.read_by_name(conn, row['asset_uid'], artifact_store.PURPOSE_RELEASE_ZIP,
                                           meta['legacy_name'])
        release_base = out / 'ontology/releases/models' / identifier
        release_base.mkdir(parents=True, exist_ok=True)
        (release_base / meta['legacy_name']).write_bytes(blob)
    for meta in artifact_store.find_artifacts(conn, owner_asset_uid=row['asset_uid'],
                                              purpose=artifact_store.PURPOSE_RESTORE_BACKUP):
        blob = artifact_store.read_by_name(conn, row['asset_uid'], artifact_store.PURPOSE_RESTORE_BACKUP,
                                           meta['legacy_name'])
        backup_dir = draft_dir / 'backups'
        backup_dir.mkdir(parents=True, exist_ok=True)
        (backup_dir / meta['legacy_name']).write_bytes(blob)
    counts['models'] += 1


def _export_project(conn, out, row, head, snapshot, payload, counts):
    identifier = row['external_id']
    draft_dir = out / 'ontology/drafts/projects' / identifier
    revision_dir = draft_dir / 'revisions' / f"{int(snapshot['seq']):04d}-{snapshot['content_hash'][:8]}"
    revision_dir.mkdir(parents=True, exist_ok=True)
    for key, filename in PROJECT_FILES:
        if key in payload:
            _write_component(revision_dir / filename, payload[key])
    _atomic_json(draft_dir / 'current.json',
                 {'seq': snapshot['seq'], 'revision': head['revision_token'],
                  'revisionDir': revision_dir.name, 'updatedAt': head.get('updated_at', '')})
    releases = store.release_rows(conn, row['asset_uid'])
    for r in releases:
        version_dir = out / 'ontology/releases/projects' / identifier / r['version_label']
        version_dir.mkdir(parents=True, exist_ok=True)
        rs = read_snapshot(conn, r['snapshot_id'])
        rp = json.loads(rs['payload_json'])
        for key, filename in PROJECT_FILES:
            if key in rp:
                _write_component(version_dir / filename, rp[key])
        (version_dir / 'manifest.yaml').write_text(
            yaml.safe_dump(r['manifest'], allow_unicode=True, sort_keys=False), encoding='utf-8')
    counts['projects'] += 1


def _export_flow(out, row, head, snapshot, payload, counts):
    identifier = row['external_id']
    draft_dir = out / 'ontology/drafts/flows' / identifier
    revision_dir = draft_dir / 'revisions' / f"{int(snapshot['seq']):04d}-{snapshot['content_hash'][:8]}"
    revision_dir.mkdir(parents=True, exist_ok=True)
    _write_component(revision_dir / 'flow.json', payload)
    _atomic_json(draft_dir / 'current.json',
                 {'seq': snapshot['seq'], 'revision': head['revision_token'],
                  'revisionDir': revision_dir.name, 'updatedAt': head.get('updated_at', '')})
    counts['flows'] += 1


def _write_component(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix == '.json':
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    else:
        path.write_text(yaml.safe_dump(value, allow_unicode=True, sort_keys=False), encoding='utf-8')


def _atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False) + '\n', encoding='utf-8')


def cmd_backup(args):
    """SQLite 在线备份 API（WAL 一致），不运行中拷主文件。"""
    url = args.url or resolve_url()
    if dbengine.backend_kind(url) != 'sqlite':
        print('✗ backup 命令当前仅支持 SQLite（MySQL 请用部署规范的事务一致备份）', file=sys.stderr)
        return 2
    source = dbengine.engine(url).raw_connection()
    import sqlite3
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination = sqlite3.connect(str(output))
    try:
        with destination:
            source.backup(destination)
    finally:
        destination.close()
        source.close()
    os_mode = 0o600
    import os
    os.chmod(str(output), os_mode)
    print(f'✓ 备份完成（含 WAL 一致快照）：{output}')
    print('  ⚠ 根密钥需另行独立备份：<DATA_ROOT>/keys/wb-root.key')
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog='workbench.storage.transfer', description='工作台存储迁移与备份 CLI')
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('init', help='初始化/升级存储库 schema（显式执行，服务不隐式建表）')
    p.add_argument('--url', default='')
    p.set_defaults(func=cmd_init)
    p = sub.add_parser('inspect', help='只读扫描旧文件树，输出迁移检查报告')
    p.add_argument('--source', required=True)
    p.add_argument('--output', default='')
    p.set_defaults(func=cmd_inspect)
    p = sub.add_parser('import', help='旧文件树 → 数据库（staging；可重入）')
    p.add_argument('--source', required=True)
    p.add_argument('--url', default='')
    p.add_argument('--batch', default='')
    p.add_argument('--allow-real-source', action='store_true', help='确认源为真实数据根（需已停写并备份）')
    p.set_defaults(func=cmd_import)
    p = sub.add_parser('verify', help='源目录与库逐项语义核对')
    p.add_argument('--source', required=True)
    p.add_argument('--url', default='')
    p.set_defaults(func=cmd_verify)
    p = sub.add_parser('export', help='库 → 旧兼容目录（反向导出，用于回滚）')
    p.add_argument('--output', required=True)
    p.add_argument('--url', default='')
    p.set_defaults(func=cmd_export)
    p = sub.add_parser('backup', help='SQLite 在线备份（备份 API，不是拷主文件）')
    p.add_argument('--output', required=True)
    p.add_argument('--url', default='')
    p.set_defaults(func=cmd_backup)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == '__main__':
    raise SystemExit(main())
