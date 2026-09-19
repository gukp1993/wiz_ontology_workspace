"""配置迁移：导出依赖收集、冻结快照、导入预检与单事务导入（20260919 需求核心）。

分层（开发计划 §2）：
* 本模块承载「包服务/纯转换 + 事务编排」：依赖闭包收集、manifest/ZIP 组装、
  命名计划、ID 映射与原子写入。路由层只做参数校验与鉴权（config_package_routes.py）。
* 临时状态（导出快照/上传暂存/预检计划）在 StateMachine（内存索引 + DATA_ROOT 磁盘），
  绑定账号 + TTL，绝不落业务表；导入成功回执走 configuration.put_request_receipt。
* ID 映射决定（详见开发计划实施记录「映射矩阵」）：
  - 资产身份全部新建：本体 external_id=uuid4、项目/编排 external_id=12hex、
    模型 provider_id=llm-*、asset_uid/snapshot_id/revision token 全新。
  - 业务稳定 ID 恒等保留：本体定义 mg:*、编排节点/输入/输出、项目连接 ID、
    apiName、登记实例 id、definitionOrder、表名字段名、SQL/代码与说明文本。
    （本体定义 ID 只在本体+引用它的项目内生效，导入永远新建资产，恒等安全。）
  - 跨资产引用重写：项目 payload 的 ontologyId/project.ontology → 新本体 id；
    项目内 {kind:'flow', flow:id} → 本次编排副本 id（M07 按连接上下文拆副本时
    引用重写到对应组副本）；编排 payload 内 providerId → 新 provider id。
"""
import json
import os
import secrets
import shutil
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from workbench import auth
from workbench import storage
from workbench.config_package_format import (
    CHUNK_SIZE, MAX_PACKAGE_BYTES, MAX_EXPORT_PREVIEWS_PER_OWNER, MAX_STAGED_PER_OWNER,
    STAGE_TTL_SECONDS, PackageFormatError,
    allocate_name, parse_manifest, read_package, sha256_hex, strip_sensitive,
)
from workbench.storage import assets as store
from workbench.storage import configuration as config_store
from workbench.storage import engine as sto
from workbench.storage.engine import read_connection, utcnow

KIND_MODEL, KIND_PROJECT, KIND_FLOW = 'model', 'project', 'flow'

# 待补凭据声明持久化（用户级设置 wb_user_settings，重启保留；按声明 ID 去重合并）
PENDING_KEY = 'config-package.pending-credentials'

IMPORT_OPERATION = 'config-import'


class TokenError(ValueError):
    """token 不存在/过期/不属于当前账号（路由层映射 404/410）。"""

    def __init__(self, message, expired=False):
        super().__init__(message)
        self.expired = expired


class ChunkConflict(ValueError):
    """同分片不同内容（路由层映射 409 CHUNK_CONFLICT）。"""


class ImportConflict(Exception):
    """导入事务内的业务冲突（路由层映射 409）。"""

    def __init__(self, code, message, extra=None):
        super().__init__(message)
        self.code = code
        self.extra = extra or {}


class StateMachine:
    """导出快照/上传暂存/预检计划的进程内状态（token→entry）。线程安全 + TTL。"""

    def __init__(self, name, ttl, max_per_owner):
        self.name = name
        self.ttl = ttl
        self.max_per_owner = max_per_owner
        self.lock = threading.Lock()
        self.entries = {}

    def create(self, owner, payload):
        with self.lock:
            self._evict_locked()
            owned = [t for t, e in self.entries.items() if e['owner'] == owner]
            if len(owned) >= self.max_per_owner:
                oldest = min(owned, key=lambda t: self.entries[t]['created'])
                self._drop_locked(oldest)
            token = secrets.token_urlsafe(24)
            self.entries[token] = {'owner': owner, 'created': time.time(), **payload}
            return token

    def get(self, token, owner):
        with self.lock:
            entry = self.entries.get(token)
            if entry is None or entry['owner'] != owner:
                raise TokenError('记录不存在或不属于当前账号，请重新操作。')
            if time.time() - entry['created'] > self.ttl:
                self._drop_locked(token)
                raise TokenError('记录已过期，请重新操作。', expired=True)
            return entry

    def drop(self, token, owner):
        with self.lock:
            entry = self.entries.get(token)
            if entry is not None and (owner is None or entry['owner'] == owner):
                self._drop_locked(token)

    def _evict_locked(self):
        expired = [t for t, e in self.entries.items() if time.time() - e['created'] > self.ttl]
        for t in expired:
            self._drop_locked(t)

    def _drop_locked(self, token):
        entry = self.entries.pop(token, None)
        if entry is None:
            return
        cleaner = entry.get('cleanup')
        if cleaner:
            try:
                cleaner()
            except OSError:
                pass


export_previews = StateMachine('export-preview', STAGE_TTL_SECONDS, MAX_EXPORT_PREVIEWS_PER_OWNER)
upload_stages = StateMachine('upload-stage', STAGE_TTL_SECONDS, MAX_STAGED_PER_OWNER)
import_previews = StateMachine('import-preview', STAGE_TTL_SECONDS, MAX_STAGED_PER_OWNER)


def _stage_root() -> Path:
    from workbench.paths import DATA_ROOT
    root = DATA_ROOT / 'tmp' / 'config-packages'
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(root, 0o700)
    except OSError:
        pass
    return root


def _expires_iso():
    return (datetime.now(timezone.utc) + timedelta(seconds=STAGE_TTL_SECONDS)).isoformat()


# ── 一致性读取：一次读连接取全部权威配置 ────────────────────────────────────────

def collect_export(model_ids, project_ids, extra_flow_ids, owner=None):
    """依赖闭包收集（只读）。返回快照 dict 或 {'blockers': [...]}。

    闭包：根本体（草稿+全部发布版本）→ 选中项目（草稿+全部发布版本）→
    项目各版本引用的本体（自动补齐，标注 dependency）→ 被引用编排
    （M07：跨项目连接上下文不一致时按组拆副本）→ 模型配置元数据 + 凭据声明。
    不沿「被谁引用」扩散到无关项目。
    """
    owner = owner or auth.require_user_id()
    storage.ensure_ready()
    blockers, warnings = [], []
    with read_connection() as conn:
        models, projects = {}, {}
        for mid in model_ids:
            asset = store.get_asset(conn, KIND_MODEL, mid, owner)
            if asset is None:
                blockers.append(f'本体不存在或不属于当前账号：{mid}')
                continue
            snap = _asset_current(conn, asset)
            if snap is None:
                blockers.append(f'本体「{asset["name"]}」尚未保存任何草稿，无法导出。')
                continue
            models[mid] = {'asset': asset, 'draft': snap, 'releases': _asset_releases(conn, asset)}
        for pid in project_ids:
            asset = store.get_asset(conn, KIND_PROJECT, pid, owner)
            if asset is None:
                blockers.append(f'项目不存在或不属于当前账号：{pid}')
                continue
            snap = _asset_current(conn, asset)
            if snap is None:
                blockers.append(f'项目「{asset["name"]}」尚未保存任何草稿，无法导出。')
                continue
            projects[pid] = {'asset': asset, 'draft': snap, 'releases': _asset_releases(conn, asset)}
        if blockers:
            return {'blockers': blockers, 'warnings': warnings}

        # 项目各版本引用的本体自动补齐（M04）
        for pid, item in projects.items():
            for snap in [item['draft']] + item['releases']:
                for ref in _project_ontology_refs(snap['payload']):
                    ref_id = str(ref.get('ontologyId') or '')
                    if not ref_id or ref_id in models:
                        continue
                    asset = store.get_asset(conn, KIND_MODEL, ref_id, owner)
                    if asset is None:
                        blockers.append(f'项目「{item["asset"]["name"]}」引用的本体不存在或不可访问：{ref_id}')
                        continue
                    dep_snap = _asset_current(conn, asset)
                    if dep_snap is None:
                        blockers.append(f'被依赖本体「{asset["name"]}」尚无已保存草稿。')
                        continue
                    models[ref_id] = {'asset': asset, 'draft': dep_snap,
                                      'releases': _asset_releases(conn, asset),
                                      'dependency': f'项目「{item["asset"]["name"]}」的历史版本依赖'}
        if blockers:
            return {'blockers': blockers, 'warnings': warnings}

        # 编排收集：项目任一纳入快照引用的 flow（递归扫 payload 内 {kind:'flow'}）
        flow_users = {}
        project_connections = {}
        for pid, item in projects.items():
            project_connections[pid] = _project_connections(item['draft']['payload'])
            for snap in [item['draft']] + item['releases']:
                for fid in _payload_flow_ids(snap['payload']):
                    flow_users.setdefault(fid, set()).add(pid)
        flows, extra_flows = {}, {}
        for fid in sorted(flow_users):
            asset = store.get_asset(conn, KIND_FLOW, fid, owner)
            if asset is None:
                blockers.append(f'项目引用的编排不存在或不属于当前账号：{fid}')
                continue
            snap = _asset_current(conn, asset)
            if snap is None:
                blockers.append(f'编排「{asset["name"]}」没有已保存配置。')
                continue
            flows[fid] = {'asset': asset, 'draft': snap}
        for fid in extra_flow_ids:
            if fid in flows:
                continue
            asset = store.get_asset(conn, KIND_FLOW, fid, owner)
            if asset is None:
                blockers.append(f'额外编排不存在或不属于当前账号：{fid}')
                continue
            snap = _asset_current(conn, asset)
            if snap is None:
                blockers.append(f'额外编排「{asset["name"]}」没有已保存配置。')
                continue
            extra_flows[fid] = {'asset': asset, 'draft': snap, 'extra': True}
        if blockers:
            return {'blockers': blockers, 'warnings': warnings}

        # 模型配置元数据（无密钥）+ 凭据声明
        provider_refs = {}
        for fid, item in {**flows, **extra_flows}.items():
            for provider_id in _payload_provider_ids(item['draft']['payload']):
                provider_refs.setdefault(provider_id, []).append(fid)
        model_configs = []
        for provider_id in sorted(provider_refs):
            meta = _provider_metadata(conn, owner, provider_id)
            if meta is None:
                warnings.append(f'编排引用的模型配置不存在，导入后将列为待补：{provider_id}')
                model_configs.append({'providerId': provider_id, 'missing': True,
                                      'referencedBy': provider_refs[provider_id]})
            else:
                meta['referencedBy'] = provider_refs[provider_id]
                model_configs.append(meta)
        credential_declarations = []
        for pid, item in projects.items():
            for row in config_store.list_credentials(conn, config_store.NAMESPACE_API,
                                                     item['asset']['asset_uid']):
                credential_declarations.append({'declarationId': row['resource_id'],
                                                'name': row['display_name'] or row['resource_id'],
                                                'namespace': 'api',
                                                'usage': [f'项目「{item["asset"]["name"]}」']})

        # 敏感结构值剥离（对每个 payload 副本；记录位置不记原值）
        stripped = []
        for item in list(models.values()) + list(projects.values()) + list(flows.values()) + list(extra_flows.values()):
            item['draft']['payload'] = strip_sensitive(item['draft']['payload'], item['asset']['name'], stripped)
            if 'releases' in item:
                item['releases'] = [{**r, 'payload': strip_sensitive(r['payload'], item['asset']['name'], stripped)}
                                    for r in item['releases']]
        if stripped:
            warnings.append(f'已剥离 {len(stripped)} 处结构化敏感值（认证头/疑似秘密字段/带凭据 URL），'
                            '导入后需在原位置重新填写。')

        # M07：编排跨项目连接上下文分组（不一致拆副本）
        flow_groups = {}
        for fid, item in flows.items():
            groups = _flow_context_groups(item['draft']['payload'], sorted(flow_users[fid]),
                                          project_connections)
            flow_groups[fid] = groups
            if len(groups) > 1:
                warnings.append(f'编排「{item["asset"]["name"]}」在不同项目使用了不同的连接配置，'
                                f'导入时将拆为 {len(groups)} 个副本并重写各项目引用。')

    return {'snapshotAt': utcnow(), 'models': models, 'projects': projects, 'flows': flows,
            'extraFlows': extra_flows, 'flowGroups': flow_groups, 'modelConfigs': model_configs,
            'credentialDeclarations': credential_declarations,
            'stripped': stripped, 'warnings': warnings, 'blockers': []}


def _asset_current(conn, asset):
    head = sto.head_by_external(conn, asset['kind'], asset['external_id'],
                                owner_user_id=asset['owner_user_id'])
    if head is None:
        return None
    snapshot = sto.read_snapshot(conn, head['snapshot_id'])
    if snapshot is None:
        return None
    return {'snapshot_id': snapshot['snapshot_id'],
            'payload_format': snapshot['payload_format'],
            'payload': json.loads(snapshot['payload_json'])}


def _asset_releases(conn, asset):
    out = []
    for row in store.release_rows(conn, asset['asset_uid']):
        snapshot = sto.read_snapshot(conn, row['snapshot_id'])
        if snapshot is None:
            raise storage.StorageError(f'发布快照缺失：{asset["name"]} {row["version_label"]}')
        out.append({'version_label': row['version_label'], 'release_order': row['release_order'],
                    'manifest': row['manifest'], 'created_at': row['created_at'],
                    'payload_format': snapshot['payload_format'],
                    'payload': json.loads(snapshot['payload_json'])})
    return out


def _payload_flow_ids(payload):
    """递归收集 payload 内 {kind:'flow', flow:<id>} 的编排 id（去重保序）。"""
    out = []

    def walk(node):
        if isinstance(node, dict):
            if node.get('kind') == 'flow' and isinstance(node.get('flow'), str) and node['flow']:
                if node['flow'] not in out:
                    out.append(node['flow'])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(payload)
    return out


def _payload_provider_ids(payload):
    out = []

    def walk(node):
        if isinstance(node, dict):
            pid = node.get('providerId')
            if isinstance(pid, str) and pid and pid not in out:
                out.append(pid)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(payload)
    return out


def _project_ontology_refs(payload):
    if isinstance(payload, dict) and payload.get('ontologyId'):
        return [{'ontologyId': payload['ontologyId'], 'version': payload.get('ontologyVersion')}]
    return []


def _project_connections(payload):
    """项目草稿连接配置指纹表 {connId: fingerprint}（不含任何秘密）。"""
    out = {}
    conns = ((payload or {}).get('connections') or {}).get('connections') or []
    for c in conns:
        if not isinstance(c, dict) or not c.get('id'):
            continue
        out[str(c['id'])] = json.dumps({k: c.get(k) for k in ('engine', 'host', 'port', 'database',
                                                              'dbIndex', 'username', 'tls')},
                                       sort_keys=True)
    return out


def _flow_uses_connection(flow_payload, conn_id):
    hit = {'v': False}

    def walk(node):
        if isinstance(node, dict):
            for key in ('connection', 'connectionId'):
                if node.get(key) == conn_id:
                    hit['v'] = True
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(flow_payload)
    return hit['v']


def _flow_context_groups(flow_payload, used_by, project_connections):
    """按「同 ID 连接的配置指纹是否一致」把使用方项目分组（M07）。返回组列表。"""
    if len(used_by) <= 1:
        return [{'projects': list(used_by), 'fingerprint': None}]
    per_project = {}
    for pid in used_by:
        conns = project_connections.get(pid, {})
        per_project[pid] = {cid: fp for cid, fp in conns.items()
                            if _flow_uses_connection(flow_payload, cid)}
    groups = []
    for pid in used_by:
        for group in groups:
            if group['fingerprint'] == per_project[pid]:
                group['projects'].append(pid)
                break
        else:
            groups.append({'fingerprint': per_project[pid], 'projects': [pid]})
    return groups


def _provider_metadata(conn, owner, provider_id):
    """模型配置安全元数据（无密钥；只报 keyConfigured 布尔）。"""
    row = conn.execute(sto.text(
        'SELECT provider_id, name, endpoint, model, timeout_seconds, temperature, secret_id '
        'FROM wb_model_configs WHERE provider_id = :p AND owner_user_id = :o'),
        {'p': provider_id, 'o': owner}).mappings().first()
    if row is None:
        return None
    meta = dict(row)
    key_configured = meta.pop('secret_id') is not None
    return {'providerId': meta['provider_id'], 'name': meta.get('name') or provider_id,
            'endpoint': meta.get('endpoint') or '', 'model': meta.get('model') or '',
            'timeoutSeconds': meta.get('timeout_seconds'), 'temperature': meta.get('temperature'),
            'missingApiKey': not key_configured}


# ── 导出预览：冻结快照 + manifest 预览 ─────────────────────────────────────────

def build_export_preview(model_ids, project_ids, extra_flow_ids, owner=None):
    snapshot = collect_export(model_ids, project_ids, extra_flow_ids, owner)
    if snapshot.get('blockers'):
        return {'blockers': snapshot['blockers'], 'warnings': snapshot.get('warnings', [])}
    owner = owner or auth.require_user_id()
    preview = {'snapshotAt': snapshot['snapshotAt'],
               'assets': {'models': [], 'projects': [], 'flows': [], 'modelConfigs': []},
               'dependencyEdges': [], 'warnings': list(snapshot['warnings']), 'blockers': []}
    for key, kind, prefix in (('models', KIND_MODEL, 'm'), ('projects', KIND_PROJECT, 'p')):
        for source_id, item in snapshot[key].items():
            package_key = prefix + secrets.token_hex(4)
            item['packageKey'] = package_key
            preview['assets'][key].append({
                'packageKey': package_key, 'id': source_id, 'name': item['asset']['name'],
                'releaseCount': len(item['releases']),
                'includeReason': item.get('dependency') or '根选择'})
    # 编排：M07 分组 → 每组一个 packageKey（一条 asset）
    for source_id, item in {**snapshot['flows'], **snapshot['extraFlows']}.items():
        item['copies'] = []
        if item.get('extra'):
            package_key = 'f' + secrets.token_hex(4)
            item['copies'].append({'packageKey': package_key, 'projects': []})
        else:
            for gi, group in enumerate(snapshot['flowGroups'].get(source_id) or [{'projects': []}]):
                package_key = 'f' + secrets.token_hex(4)
                item['copies'].append({'packageKey': package_key, 'projects': group['projects']})
        for ci, copy in enumerate(item['copies']):
            preview['assets']['flows'].append({
                'packageKey': copy['packageKey'], 'id': source_id,
                'name': item['asset']['name'], 'usedBy': copy['projects'],
                'shared': len(item['copies']) == 1 and not item.get('extra'),
                'copyIndex': ci,
                'includeReason': '额外勾选（未绑定）' if item.get('extra') else '项目引用'})
    for meta in snapshot['modelConfigs']:
        package_key = 'c' + secrets.token_hex(4)
        meta['packageKey'] = package_key
        preview['assets']['modelConfigs'].append({
            'packageKey': package_key, 'id': meta['providerId'],
            'name': meta.get('name') or meta['providerId'],
            'referencedBy': meta.get('referencedBy') or [],
            'missingApiKey': bool(meta.get('missingApiKey'))})
        if meta.get('missingApiKey'):
            preview['warnings'].append(f"模型「{meta.get('name') or meta['providerId']}」未配置 API Key，"
                                       '导入后需补填。')
    if snapshot['credentialDeclarations']:
        preview['warnings'].append(f"包含 {len(snapshot['credentialDeclarations'])} 项 API 凭据声明"
                                   '（仅名称与用途，不含密钥值；导入后需补填）。')
    preview['warnings'].append('本包不含连接密码、API Key 等任何受管理凭据；也不迁移业务数据库数据。'
                               '请自行检查 SQL/说明等手写内容中是否含敏感信息。')
    token = export_previews.create(owner, {'snapshot': snapshot, 'preview': preview})
    return {'exportToken': token, 'expiresAt': _expires_iso(), 'snapshotAt': preview['snapshotAt'],
            **preview}


# ── ZIP 组装 ───────────────────────────────────────────────────────────────────

def build_package_zip(snapshot) -> bytes:
    """从冻结快照生成 ZIP 字节（manifest + payload 原样文件）。"""
    import io
    import zipfile

    manifest = {
        'format': 'wiz-workbench-config-package', 'formatVersion': 1,
        'packageId': str(uuid4()), 'exportedAt': snapshot['snapshotAt'],
        'producerVersion': '1.0', 'requiredCapabilities': [],
        'rootSelection': {'models': sorted(snapshot['models']),
                          'projects': sorted(snapshot['projects']),
                          'extraFlows': sorted(snapshot['extraFlows'])},
        'assets': [], 'dependencyEdges': [],
        'credentialDeclarations': snapshot['credentialDeclarations'],
        'pendingItems': list(snapshot['stripped']), 'credentialsExcluded': True,
        'files': {}}
    files = {}

    def add(path, obj):
        raw = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        files[path] = raw
        manifest['files'][path] = {'bytes': len(raw), 'sha256': sha256_hex(raw)}

    edges = manifest['dependencyEdges']
    for source_id, item in snapshot['models'].items():
        key = item['packageKey']
        draft_path = f'models/{key}/draft.json'
        manifest['assets'].append({
            'kind': KIND_MODEL, 'packageKey': key, 'sourceId': source_id,
            'name': item['asset']['name'], 'payloadFormat': item['draft']['payload_format'],
            'draftPath': draft_path, 'releases': [],
            'dependencyNote': item.get('dependency') or ''})
        add(draft_path, item['draft']['payload'])
        for rel in item['releases']:
            path = f"models/{key}/releases/{_safe_filename(rel['version_label'])}.json"
            manifest['assets'][-1]['releases'].append({
                'version': rel['version_label'], 'path': path, 'releaseOrder': rel['release_order'],
                'changeType': (rel['manifest'] or {}).get('changeType', ''),
                'createdAt': rel['created_at'], 'payloadFormat': rel['payload_format']})
            add(path, rel['payload'])
    for source_id, item in snapshot['projects'].items():
        key = item['packageKey']
        draft_path = f'projects/{key}/draft.json'
        manifest['assets'].append({
            'kind': KIND_PROJECT, 'packageKey': key, 'sourceId': source_id,
            'name': item['asset']['name'], 'payloadFormat': item['draft']['payload_format'],
            'draftPath': draft_path, 'releases': []})
        add(draft_path, item['draft']['payload'])
        ref_model = _project_ref_model(item['draft']['payload'])
        target = _model_item_by_source(snapshot, ref_model)
        if target is not None:
            edges.append({'from': key, 'to': target['packageKey'], 'kind': 'ontology-version',
                          'detail': str(item['draft']['payload'].get('ontologyVersion') or '')})
        for rel in item['releases']:
            path = f"projects/{key}/releases/{_safe_filename(rel['version_label'])}.json"
            manifest['assets'][-1]['releases'].append({
                'version': rel['version_label'], 'path': path, 'releaseOrder': rel['release_order'],
                'changeType': (rel['manifest'] or {}).get('note', ''),
                'createdAt': rel['created_at'], 'payloadFormat': rel['payload_format']})
            add(path, rel['payload'])
            ref_model = _project_ref_model(rel['payload'])
            target = _model_item_by_source(snapshot, ref_model)
            if target is not None:
                edges.append({'from': key, 'to': target['packageKey'], 'kind': 'ontology-version',
                              'detail': str((rel['payload'] or {}).get('ontologyVersion') or ''),
                              'sourceVersion': rel['version_label']})
    for collection in ('flows', 'extraFlows'):
        for source_id, item in snapshot[collection].items():
            for copy in item['copies']:
                draft_path = f"flows/{copy['packageKey']}/draft.json"
                manifest['assets'].append({
                    'kind': KIND_FLOW, 'packageKey': copy['packageKey'], 'sourceId': source_id,
                    'name': item['asset']['name'], 'payloadFormat': item['draft']['payload_format'],
                    'draftPath': draft_path, 'releases': [],
                    'contextProjects': copy['projects']})
                add(draft_path, item['draft']['payload'])
    providers = []
    for meta in snapshot['modelConfigs']:
        if meta.get('missing'):
            continue
        providers.append({'providerId': meta['providerId'], 'name': meta.get('name') or '',
                          'endpoint': meta.get('endpoint') or '', 'model': meta.get('model') or '',
                          'timeoutSeconds': meta.get('timeoutSeconds'),
                          'temperature': meta.get('temperature')})
        for fid in meta.get('referencedBy') or []:
            flow_item = snapshot['flows'].get(fid) or snapshot['extraFlows'].get(fid)
            if flow_item is not None:
                for copy in flow_item['copies']:
                    edges.append({'from': copy['packageKey'], 'to': meta['packageKey'],
                                  'kind': 'model-config'})
    add('configuration/models.json', {'providers': providers})
    add('configuration/credential-declarations.json',
        {'declarations': snapshot['credentialDeclarations']})

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False).encode('utf-8'))
        for path, raw in files.items():
            zf.writestr(path, raw)
    return buf.getvalue()


def _safe_filename(version):
    return ''.join(c if c.isalnum() or c in '._-' else '_' for c in str(version))


def _project_ref_model(payload):
    payload = payload or {}
    return str(payload.get('ontologyId') or (payload.get('project') or {}).get('ontology') or '')


def _model_item_by_source(snapshot, source_id):
    if not source_id:
        return None
    for item in snapshot['models'].values():
        if item['asset']['external_id'] == source_id:
            return item
    return None


# ── 上传暂存 ───────────────────────────────────────────────────────────────────

def stage_begin(owner, filename, total_bytes, sha256):
    if not isinstance(filename, str) or not str(filename).lower().endswith('.zip'):
        raise ValueError('请上传 .zip 配置包。')
    if not isinstance(total_bytes, int) or total_bytes <= 0:
        raise ValueError('文件大小无效。')
    if total_bytes > MAX_PACKAGE_BYTES:
        raise ValueError(f'配置包超过 {MAX_PACKAGE_BYTES // (1024 * 1024)} MiB 限制，请拆分或减小包体积。')
    # 先建状态条目（token 即 uploadId，目录名与之一一对应）
    upload_id = upload_stages.create(owner, {'placeholder': True})
    directory = _stage_root() / upload_id
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    (directory / 'meta.json').write_text(json.dumps(
        {'filename': str(filename)[:200], 'bytes': total_bytes, 'sha256': str(sha256 or '').lower(),
         'chunkSize': CHUNK_SIZE, 'received': []}, ensure_ascii=False), encoding='utf-8')
    entry = upload_stages.get(upload_id, owner)
    entry.pop('placeholder', None)
    entry['uploadId'] = upload_id
    entry['dir'] = directory
    entry['cleanup'] = lambda: shutil.rmtree(directory, ignore_errors=True)
    return {'uploadId': upload_id, 'chunkSize': CHUNK_SIZE,
            'totalChunks': (total_bytes + CHUNK_SIZE - 1) // CHUNK_SIZE}


def stage_chunk(owner, upload_id, index, b64_data, chunk_hash):
    entry = upload_stages.get(upload_id, owner)
    if not isinstance(b64_data, str):
        raise ValueError('分片数据必须是 base64 字符串。')
    import base64
    import binascii
    try:
        raw = base64.b64decode(b64_data, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError('分片数据不是有效的 base64。')
    if len(raw) > CHUNK_SIZE:
        raise ValueError(f'单个分片不得超过 {CHUNK_SIZE // 1024} KiB。')
    chunk_hash = str(chunk_hash or '').lower()
    if sha256_hex(raw) != chunk_hash:
        raise ValueError('分片校验失败：内容与 chunkHash 不一致，请重传该分片。')
    meta = json.loads((entry['dir'] / 'meta.json').read_text(encoding='utf-8'))
    total_chunks = (int(meta['bytes']) + CHUNK_SIZE - 1) // CHUNK_SIZE
    if not isinstance(index, int) or not 0 <= index < total_chunks:
        raise ValueError(f'分片序号无效（应为 0～{total_chunks - 1}）。')
    hash_file = entry['dir'] / f'part-{index:05d}.json'
    if hash_file.exists():
        existing = json.loads(hash_file.read_text(encoding='utf-8')).get('hash')
        if existing == chunk_hash:
            return {'received': index, 'totalChunks': total_chunks}  # 幂等重传确认
        raise ChunkConflict(f'分片 {index} 已存在且内容不同，请刷新上传后重试。')
    hash_file.write_text(json.dumps({'hash': chunk_hash}), encoding='utf-8')
    (entry['dir'] / f'part-{index:05d}.bin').write_bytes(raw)
    meta['received'] = sorted(set(meta.get('received', [])) | {index})
    (entry['dir'] / 'meta.json').write_text(json.dumps(meta, ensure_ascii=False), encoding='utf-8')
    return {'received': index, 'totalChunks': total_chunks}


def stage_assemble(owner, upload_id) -> tuple:
    """校验全部分片并组装；返回 (zip_bytes, sha256)。"""
    entry = upload_stages.get(upload_id, owner)
    meta = json.loads((entry['dir'] / 'meta.json').read_text(encoding='utf-8'))
    total_chunks = (int(meta['bytes']) + CHUNK_SIZE - 1) // CHUNK_SIZE
    received = set(meta.get('received', []))
    missing = [i for i in range(total_chunks) if i not in received]
    if missing:
        raise ValueError(f'上传未完成：还缺 {len(missing)} 个分片，请续传后再检查。')
    parts = []
    for i in range(total_chunks):
        part = entry['dir'] / f'part-{i:05d}.bin'
        if not part.exists():
            raise ValueError(f'分片 {i} 数据缺失，请重传。')
        parts.append(part.read_bytes())
    data = b''.join(parts)
    if len(data) != int(meta['bytes']):
        raise ValueError('组装后大小与声明不一致，请重新上传。')
    if sha256_hex(data) != str(meta['sha256']):
        raise ValueError('整体校验失败：SHA-256 与声明不一致，请重新上传。')
    return data, meta['sha256']


# ── 导入预检：安全解析 + 命名计划（零资产写入）────────────────────────────────

def build_import_preview(owner, upload_id):
    data, _ = stage_assemble(owner, upload_id)
    files = read_package(data)
    manifest = parse_manifest(files)
    package_hash = sha256_hex(data)
    blockers, warnings = [], []
    if not any(a['kind'] in (KIND_MODEL, KIND_PROJECT) for a in manifest['assets']):
        blockers.append('包内没有本体或项目，无法导入。')

    assets = []
    taken = {}
    with read_connection() as conn:
        rows = conn.execute(sto.text('SELECT kind, name_key FROM wb_assets '
                                     'WHERE owner_user_id = :o AND deleted_at IS NULL'),
                            {'o': owner}).all()
        for kind, nk in rows:
            taken.setdefault(kind, set()).add(nk)
    from workbench.storage.assets import name_key
    for asset in manifest['assets']:
        kind = asset['kind']
        source_name = str(asset.get('name') or '')
        final_name, reason = allocate_name(source_name, set(taken.get(kind) or ()))
        taken.setdefault(kind, set()).add(name_key(final_name))
        assets.append({'kind': kind, 'packageKey': asset['packageKey'],
                       'sourceName': source_name, 'suggestedName': final_name,
                       'renameReason': reason, 'releaseCount': len(asset.get('releases') or []),
                       'dependencyNote': asset.get('dependencyNote') or ''})

    preview = {'packageHash': package_hash, 'files': files, 'manifest': manifest,
               'assets': assets, 'modelConfigs': _preview_model_configs(files),
               'warnings': warnings, 'blockers': blockers}
    token = import_previews.create(owner, {'preview': preview})
    return {'previewToken': token, 'expiresAt': _expires_iso(), 'packageHash': package_hash,
            'assets': assets, 'modelConfigs': preview['modelConfigs'],
            'warnings': warnings, 'blockers': blockers}


def _preview_model_configs(files):
    raw = files.get('configuration/models.json')
    if not raw:
        return []
    try:
        data = json.loads(raw.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        raise PackageFormatError('configuration/models.json 不是有效的 JSON。')
    return [{'packageKey': 'c-' + str(p.get('providerId')), 'sourceName': p.get('name') or '',
             'missingApiKey': True} for p in (data.get('providers') or [])]


# ── 命名覆盖 ───────────────────────────────────────────────────────────────────

def plan_name_overrides(preview, overrides):
    """合并用户改名：校验空值/长度/包内重复；返回 {packageKey: finalName}。"""
    from workbench.storage.assets import name_key
    by_key = {item['packageKey']: item for item in preview['assets']}
    final = {}
    for item in preview['assets']:
        final[item['packageKey']] = item['suggestedName']
    for key, name in (overrides or {}).items():
        if key not in by_key:
            raise ValueError(f'nameOverrides 引用了包内不存在的资产：{key}')
        if not isinstance(name, str) or not name.strip():
            raise ValueError('名称不能为空。')
        name = name.strip()
        if len(name) > 80:
            raise ValueError('名称不能超过 80 个字符。')
        kind = by_key[key]['kind']
        for other_key, other in by_key.items():
            if other_key != key and other['kind'] == kind and name_key(other['suggestedName']) == name_key(name):
                raise ValueError(f'名称「{name}」与包内另一资产重复。')
        final[key] = name
    return final


# ── 导入：ID 生成 + 单事务写入 ─────────────────────────────────────────────────

def new_model_id():
    return str(uuid4())


def new_project_id():
    return secrets.token_hex(6)


def new_flow_id():
    return secrets.token_hex(6)


def new_provider_id():
    return 'llm-' + secrets.token_hex(5)


def import_transaction(owner, preview, request_id, final_names):
    """单事务原子导入。成功返回 {'receipt':...}；重放返回 {'receipt':..., 'replayed': True}。

    幂等与名称冲突在事务内权威复查（previewToken 状态可能滞后）。
    """
    manifest = preview['manifest']
    files = preview['files']
    package_hash = preview['packageHash']
    request_hash = sha256_hex(json.dumps({'packageHash': package_hash, 'names': final_names},
                                         ensure_ascii=False, sort_keys=True).encode('utf-8'))

    def body(conn):
        existing = config_store.get_request_receipt(conn, IMPORT_OPERATION, owner, request_id)
        if existing is not None:
            if existing['request_hash'] != request_hash:
                raise ImportConflict('IDEMPOTENCY_CONFLICT',
                                     '同一 requestId 已用于不同导入参数；请刷新页面后用新的导入操作重试。')
            return {'receipt': existing['response'], 'replayed': True}
        taken_by_kind = {}
        rows = conn.execute(sto.text('SELECT kind, name_key FROM wb_assets '
                                     'WHERE owner_user_id = :o AND deleted_at IS NULL'),
                            {'o': owner}).all()
        for kind, nk in rows:
            taken_by_kind.setdefault(kind, set()).add(nk)
        from workbench.storage.assets import name_key
        conflicts, seen = [], {}
        for asset in manifest['assets']:
            kind, key = asset['kind'], asset['packageKey']
            nk = name_key(final_names[key])
            if nk in taken_by_kind.get(kind, ()) or nk in seen:
                suggested = allocate_name(final_names[key], taken_by_kind.get(kind, ()) | set(seen))[0]
                conflicts.append({'packageKey': key, 'kind': kind, 'suggestedName': suggested})
            seen[nk] = key
        if conflicts:
            raise ImportConflict('DUPLICATE_NAME', '部分名称已被占用（可能刚被并发创建），'
                                 '请使用建议名称后重新提交。', {'conflicts': conflicts})

        new_ids = {}
        payloads = {}
        for asset in manifest['assets']:
            key = asset['packageKey']
            new_ids[key] = (new_model_id() if asset['kind'] == KIND_MODEL
                            else new_project_id() if asset['kind'] == KIND_PROJECT else new_flow_id())
            payloads[key] = json.loads(files[asset['draftPath']].decode('utf-8'))
        provider_map = {str(meta['packageKey']).removeprefix('c-'): new_provider_id()
                        for meta in preview['modelConfigs']}
        flow_copy_ids = {asset['packageKey']: new_ids[asset['packageKey']]
                         for asset in manifest['assets'] if asset['kind'] == KIND_FLOW}
        created = []

        # 1) 本体：资产 → 草稿+head → 发布版本（保序；新 revision token）
        for asset in manifest['assets']:
            if asset['kind'] != KIND_MODEL:
                continue
            key = asset['packageKey']
            payload = payloads[key]
            payload['workspaceId'] = new_ids[key]
            name = final_names[key]
            asset_uid = store.ensure_asset(conn, KIND_MODEL, new_ids[key], name,
                                           {'importedFrom': manifest['packageId']}, None,
                                           owner_user_id=owner)
            _write_draft(conn, asset_uid, payload, asset['payloadFormat'])
            for rel in asset.get('releases') or []:
                rel_payload = json.loads(files[rel['path']].decode('utf-8'))
                rel_payload['workspaceId'] = new_ids[key]
                snap = store.append_snapshot(conn, asset_uid, rel_payload,
                                             str(rel.get('payloadFormat') or 'release-state-1'),
                                             'release', now=utcnow())
                entry = {'version': rel['version'], 'changeType': rel.get('changeType') or 'initial',
                         'changeNote': f"配置迁移自「{asset.get('name') or key}」", 'reviewer': '',
                         'createdAt': utcnow(), 'revision': store.new_token(), 'parentVersion': None,
                         'reasons': [], 'originPackageId': manifest['packageId'],
                         'sourceVersion': rel['version'],
                         'sourceHash': (manifest['files'].get(rel['path']) or {}).get('sha256', '')}
                store.append_release(conn, asset_uid, rel['version'], snap['snapshot_id'],
                                     entry, now=utcnow())
            created.append({'kind': KIND_MODEL, 'packageKey': key, 'newId': new_ids[key],
                            'newName': name, 'sourceName': asset.get('name') or '',
                            'releaseCount': len(asset.get('releases') or [])})

        # 2) 模型配置元数据（owner=接收账号；无密钥，secret_id 空）
        raw = files.get('configuration/models.json')
        providers = (json.loads(raw.decode('utf-8')).get('providers') or []) if raw else []
        for provider in providers:
            old_pid = str(provider.get('providerId') or '')
            new_pid = provider_map.get(old_pid) or new_provider_id()
            provider_map[old_pid] = new_pid
            conn.execute(sto.text(
                'INSERT INTO wb_model_configs (owner_user_id, provider_id, name, endpoint, model, '
                'timeout_seconds, temperature, metadata_revision, updated_at) '
                'VALUES (:o, :p, :n, :e, :m, :t, :tp, 1, :u)'),
                {'o': owner, 'p': new_pid, 'n': str(provider.get('name') or new_pid)[:160],
                 'e': str(provider.get('endpoint') or '')[:500],
                 'm': str(provider.get('model') or '')[:500],
                 't': int(provider.get('timeoutSeconds') or 60),
                 'tp': float(provider.get('temperature') or 0), 'u': utcnow()})
            created.append({'kind': 'modelConfig', 'packageKey': 'c-' + old_pid, 'newId': new_pid,
                            'newName': provider.get('name') or new_pid, 'sourceName': old_pid,
                            'releaseCount': 0})

        # 3) 编排副本（M07：每条 flow asset 一个副本；contextProjects 标注归属项目）
        for asset in manifest['assets']:
            if asset['kind'] != KIND_FLOW:
                continue
            key = asset['packageKey']
            payload = _remap_flow_payload(payloads[key], provider_map)
            name = final_names[key]
            context = asset.get('contextProjects') or []
            if len(context) > 1:
                name = f"{name}（{'、'.join(str(c) for c in context[:2])}）"
            asset_uid = store.ensure_asset(conn, KIND_FLOW, new_ids[key], name,
                                           {'importedFrom': manifest['packageId']}, None,
                                           owner_user_id=owner)
            _write_draft(conn, asset_uid, payload, asset['payloadFormat'])
            created.append({'kind': KIND_FLOW, 'packageKey': key, 'newId': new_ids[key],
                            'newName': name, 'sourceName': asset.get('name') or '',
                            'releaseCount': 0})

        # 4) 项目：资产 → 草稿+head+项目版本引用 → 发布版本（引用指向本次新本体）
        for asset in manifest['assets']:
            if asset['kind'] != KIND_PROJECT:
                continue
            key = asset['packageKey']
            name = final_names[key]
            project_source_id = str(_project_source_id(manifest, asset))
            new_ontology = _resolve_new_ontology(manifest, payloads[key], new_ids)
            ontology_version = str(payloads[key].get('ontologyVersion')
                                   or (payloads[key].get('project') or {}).get('ontology_version') or '')
            payload = _remap_project_payload(payloads[key], new_ontology, manifest,
                                             flow_copy_ids, project_source_id)
            payload['projectId'] = new_ids[key]
            payload['name'] = name
            if isinstance(payload.get('project'), dict):
                payload['project']['project_id'] = new_ids[key]
                payload['project']['name'] = name
                if new_ontology:
                    payload['project']['ontology'] = new_ontology
            asset_uid = store.ensure_asset(conn, KIND_PROJECT, new_ids[key], name,
                                           {'importedFrom': manifest['packageId']}, None,
                                           owner_user_id=owner)
            snapshot = _write_draft(conn, asset_uid, payload, asset['payloadFormat'],
                                    project_ref={'target_ontology_id': new_ontology,
                                                 'target_version': ontology_version})
            for rel in asset.get('releases') or []:
                rel_payload = _remap_project_payload(json.loads(files[rel['path']].decode('utf-8')),
                                                     new_ontology, manifest, flow_copy_ids,
                                                     project_source_id)
                rel_payload['projectId'] = new_ids[key]
                rel_payload['name'] = name
                if isinstance(rel_payload.get('project'), dict):
                    rel_payload['project']['project_id'] = new_ids[key]
                    rel_payload['project']['name'] = name
                    if new_ontology:
                        rel_payload['project']['ontology'] = new_ontology
                snap = store.append_snapshot(conn, asset_uid, rel_payload,
                                             str(rel.get('payloadFormat') or 'project-state-1'),
                                             'release', now=utcnow())
                rel_manifest = {'version': rel['version'], 'projectId': new_ids[key],
                                'ontologyId': new_ontology, 'ontologyVersion': ontology_version,
                                'createdAt': utcnow(), 'revision': store.new_token(),
                                'contentHash': '', 'note': f"配置迁移自「{asset.get('name') or key}」",
                                'originPackageId': manifest['packageId'],
                                'sourceVersion': rel['version'],
                                'sourceHash': (manifest['files'].get(rel['path']) or {}).get('sha256', '')}
                store.append_release(conn, asset_uid, rel['version'], snap['snapshot_id'],
                                     rel_manifest, now=utcnow())
            created.append({'kind': KIND_PROJECT, 'packageKey': key, 'newId': new_ids[key],
                            'newName': name, 'sourceName': asset.get('name') or '',
                            'releaseCount': len(asset.get('releases') or [])})

        # 5) 待补凭据声明（wb_user_settings 持久化；按声明 ID 去重合并）
        pending = config_store.get_user_setting(conn, owner, PENDING_KEY, []) or []
        raw = files.get('configuration/credential-declarations.json')
        declarations = (json.loads(raw.decode('utf-8')).get('declarations') or []) if raw else []
        pending_ids = {p.get('declarationId') for p in pending}
        for declaration in declarations:
            if declaration.get('declarationId') in pending_ids:
                continue
            pending.append({**declaration, 'importedAt': utcnow()})
        config_store.put_user_setting(conn, owner, PENDING_KEY, pending, now=utcnow())

        response = {'receiptId': 'rcpt-' + secrets.token_hex(6), 'requestId': request_id,
                    'assets': created, 'pendingCredentials': declarations,
                    'packageHash': package_hash, 'importedAt': utcnow(),
                    'warnings': ['导入完成：配置已就绪，但未执行任何连接测试或业务查询；'
                                 '连接密码与 API Key 需在原位置重新配置后才能运行。']}
        config_store.put_request_receipt(conn, IMPORT_OPERATION, owner, request_id, request_hash,
                                         response, now=utcnow())
        return {'receipt': response}

    with sto.write_tx() as tx:
        return tx.run(body)


def _project_source_id(manifest, project_asset):
    """项目 draft payload 里的来源项目 id（用于 M07 副本归属判定）。"""
    payload = None
    for asset in manifest['assets']:
        if asset['packageKey'] == project_asset['packageKey']:
            payload = asset
            break
    return project_asset.get('sourceId') or ''


def _resolve_new_ontology(manifest, project_payload, new_ids):
    """项目引用的本体 → 本次新本体 id（按 sourceId 找对应 model asset）。"""
    ref = _project_ref_model(project_payload)
    for asset in manifest['assets']:
        if asset['kind'] == KIND_MODEL and str(asset.get('sourceId')) == ref:
            return new_ids[asset['packageKey']]
    return ''


def _write_draft(conn, asset_uid, payload, payload_format, project_ref=None):
    """导入侧草稿写入：快照 + 新建 head（全新 revision token）。"""
    snapshot = store.append_snapshot(conn, asset_uid, payload, payload_format, 'draft', now=utcnow())
    conn.execute(sto.text('INSERT INTO wb_asset_heads (asset_uid, snapshot_id, revision_token, '
                          'generation, snapshot_seq, release_seq, updated_at) '
                          'VALUES (:a, :s, :t, 1, :q, 0, :now)'),
                 {'a': asset_uid, 's': snapshot['snapshot_id'], 't': store.new_token(),
                  'q': snapshot['seq'], 'now': utcnow()})
    if project_ref is not None:
        store.upsert_project_ref(conn, snapshot['snapshot_id'], project_ref)
    conn.execute(sto.text('UPDATE wb_assets SET updated_at = :now WHERE asset_uid = :a'),
                 {'now': utcnow(), 'a': asset_uid})
    return snapshot


def _remap_flow_payload(payload, provider_map):
    """编排 payload：providerId → 本次新 provider；其余稳定 ID 恒等。"""

    def walk(node):
        if isinstance(node, dict):
            return {k: (provider_map[v] if k == 'providerId' and isinstance(v, str) and v in provider_map
                        else walk(v)) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(payload)


def _remap_project_payload(payload, new_ontology, manifest, flow_copy_ids, project_source_id):
    """项目 payload：本体引用 + flow 引用重写为本次新资产；其余恒等。

    flow 副本选择（M07）：优先选 contextProjects 包含本项目 sourceId 的副本；
    无精确归属时选首个副本。
    """
    source_to_copy = {}
    for asset in manifest['assets']:
        if asset['kind'] != KIND_FLOW:
            continue
        context = asset.get('contextProjects') or []
        chosen = None
        if project_source_id and project_source_id in context:
            chosen = flow_copy_ids[asset['packageKey']]
        source_to_copy.setdefault(str(asset.get('sourceId')), chosen)
        if chosen is None:
            source_to_copy[str(asset.get('sourceId'))] = flow_copy_ids[asset['packageKey']]

    def walk(node):
        if isinstance(node, dict):
            out = {}
            for k, v in node.items():
                if k == 'flow' and isinstance(v, str) and v in source_to_copy and source_to_copy[v]:
                    out[k] = source_to_copy[v]
                else:
                    out[k] = walk(v)
            return out
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    payload = walk(payload)
    if isinstance(payload, dict):
        if 'ontologyId' in payload and new_ontology:
            payload['ontologyId'] = new_ontology
        project = payload.get('project')
        if isinstance(project, dict) and new_ontology:
            project['ontology'] = new_ontology
    return payload


def pending_credentials_list(owner):
    storage.ensure_ready()
    with read_connection() as conn:
        return config_store.get_user_setting(conn, owner, PENDING_KEY, []) or []
