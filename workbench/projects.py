"""Project binding store on the workbench database (base files, drafts, releases).

项目三态（base 初始化输入 / 草稿 / 发布）全部入库：资产 + head（imported-base 或
draft）+ 不可变快照 + 每快照至多一条的本体版本引用（wb_project_refs）。旧目录
ontology/projects、drafts/projects、releases/projects 只是迁移输入与备份。
表结构目录（catalogs）是服务端派生数据，仍不入草稿/快照（保存前剥离）。
"""
from datetime import datetime, timezone
from uuid import uuid4
import copy

from workbench import storage
from workbench.storage import assets as store
from workbench.storage.engine import read_connection, write_tx, utcnow

KIND = 'project'
FILE_ORDER = (('project', 'project.yaml'), ('connections', 'connections.yaml'),
              ('bindings', 'bindings.yaml'), ('implementations', 'implementations.yaml'),
              ('parameters', 'parameters.yaml'))


class ProjectNotFound(ValueError):
    pass


def clean_id(identifier):
    import re
    if not isinstance(identifier, str) or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', identifier):
        raise ValueError('项目标识无效')
    return identifier


def _empty_state(identifier, name, ontology_id, ontology_version):
    return {'projectId': identifier, 'name': name, 'ontologyId': ontology_id,
            'ontologyVersion': str(ontology_version),
            'connections': {'connections': []},
            'bindings': {'notice': '', 'object_bindings': [], 'observation_binding': {}, 'source_candidates': [],
                         'actionBindings': []},
            'implementations': [], 'parameters': {}, 'projectMeta': {}}


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


def _payload_of(state):
    """在线快照形态：规范化项目状态（剥离 catalogs 与运行期 _ 字段）。"""
    state = _normalize(state)
    payload = {k: v for k, v in state.items() if not str(k).startswith('_')}
    bindings = {k: v for k, v in payload['bindings'].items() if k != 'catalogs'}
    payload['bindings'] = bindings
    return payload


def summary_of(state):
    bindings = state.get('bindings') or {}
    return {'ontologyId': state.get('ontologyId', ''),
            'ontologyVersion': str(state.get('ontologyVersion', '')),
            'objectBindings': len(bindings.get('object_bindings') or []),
            'implementations': len(state.get('implementations') or [])}


def revision(state):
    """内容 hash：仅用于审计（manifest contentHash）与旧导出兼容，不作并发令牌。"""
    import hashlib
    import json
    payload = {k: v for k, v in state.items() if not str(k).startswith('_')}
    bindings = payload.get('bindings')
    if isinstance(bindings, dict) and 'catalogs' in bindings:
        payload['bindings'] = {k: v for k, v in bindings.items() if k != 'catalogs'}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def current_token(identifier):
    """head 的不透明 revision token；项目不存在返回 None。"""
    storage.ensure_ready()
    return store.current_token(KIND, clean_id(identifier))


def _project_ref_of(state):
    if state.get('ontologyId') and state.get('ontologyVersion'):
        return {'target_ontology_id': state['ontologyId'],
                'target_version': str(state['ontologyVersion'])}
    return None


def _state_from_payload(payload, identifier, payload_format=''):
    """版本化读取适配：project-state-1 直读；legacy-files-v3（迁移的历史文件组件）
    按旧 project.yaml 结构还原为规范化状态（不经二次转换、不丢未知键）。"""
    if payload_format in ('', 'project-state-1'):
        state = _normalize(copy.deepcopy(payload))
    else:
        project = payload.get('project') or {}
        ontology_id = str(project.get('ontology') or project.get('ontology_id') or '')
        version = str(project.get('ontology_version') or project.get('model_version') or '')
        if not ontology_id and version:
            ontology_id = 'storage'  # 旧约定：只有 storage 本体有项目
        state = _normalize({
            'projectId': identifier,
            'name': str(project.get('name', '') or ''),
            'ontologyId': ontology_id,
            'ontologyVersion': version,
            'connections': payload.get('connections') or {'connections': []},
            'bindings': payload.get('bindings') or {},
            'implementations': (payload.get('implementations') or {}).get('implementations', [])
                               if isinstance(payload.get('implementations'), dict)
                               else (payload.get('implementations') or []),
            'parameters': payload.get('parameters') or {},
            'projectMeta': {k: v for k, v in project.items()
                            if k not in ('project_id', 'name', 'ontology', 'ontology_id',
                                         'ontology_version', 'model_version')},
        })
    state['projectId'] = clean_id(identifier)
    return state


def read_draft(identifier):
    storage.ensure_ready()
    identifier = clean_id(identifier)
    current = store.read_current(KIND, identifier)
    if current is None:
        return None
    head, snapshot = current['head'], current['snapshot']
    state = _state_from_payload(snapshot['payload'], identifier, snapshot.get('payload_format', ''))
    state['_draft'] = {'seq': head.get('snapshot_seq', snapshot.get('seq', 0)),
                       'updatedAt': head.get('updated_at', ''),
                       'purpose': snapshot.get('purpose', '')}
    return state


def load(identifier):
    identifier = clean_id(identifier)
    state = read_draft(identifier)
    if state is not None:
        has_draft = state.get('_draft', {}).get('purpose') == 'draft'
        return state, has_draft
    raise ProjectNotFound('项目不存在')


def listing(ontology_id=None):
    storage.ensure_ready()
    with read_connection() as conn:
        rows = store.list_assets(conn, KIND)
    items = []
    for row in rows:
        summary = row.get('summary') or {}
        if ontology_id and summary.get('ontologyId') != ontology_id:
            continue
        items.append({'id': row['external_id'], 'name': row['name'] or row['external_id'],
                      'ontologyId': summary.get('ontologyId', ''),
                      'ontologyVersion': str(summary.get('ontologyVersion', '')),
                      'hasDraft': row.get('purpose') == 'draft'})
    return items


def create(name, ontology_id='', ontology_version=''):
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
        raise ValueError('项目名称需要填写 1～80 个字符')
    name = name.strip()
    storage.ensure_ready()
    ontology_id = ontology_id or ''
    ontology_version = str(ontology_version or '')
    identifier = uuid4().hex[:12]
    state = _empty_state(identifier, name, ontology_id, ontology_version)
    payload = _payload_of(state)

    def body(conn):
        store.bump_guard(conn, 'asset-name:project')
        # 名称唯一范围与旧行为一致：同名且（未指明本体 或 同一本体）才冲突
        rows = store.list_assets(conn, KIND)
        for row in rows:
            if row['name_key'] != store.name_key(name):
                continue
            other = (row.get('summary') or {}).get('ontologyId', '')
            if not ontology_id or other == ontology_id:
                raise ValueError('已存在同名项目，请换一个名称')
        result = store._save_draft_in_conn(
            conn, KIND, identifier, payload, store.PAYLOAD_FORMAT_PROJECT,
            expected_token=None, name=name, summary=summary_of(state),
            project_ref=_project_ref_of(state), purpose='imported-base',
            legacy_revision='', allow_create=True, allow_advance=False, now=utcnow(),
            conflict={})
        return result

    with write_tx() as tx:
        tx.run(body)
    return {'id': identifier, 'name': name, 'ontologyId': ontology_id,
            'ontologyVersion': ontology_version}


def save_draft(state, expected_token=None):
    """保存项目草稿。expected_token：客户端基线（路由层必传）；None = 内部/测试
    路径，按当前 head 推进（旧文件版模块层不做 revision 检查，语义保持一致）。"""
    identifier = clean_id(state.get('projectId'))
    storage.ensure_ready()
    try:
        result = store.save_draft(KIND, identifier, _payload_of(state),
                                  store.PAYLOAD_FORMAT_PROJECT, expected_token=expected_token,
                                  name=str(state.get('name') or identifier),
                                  summary=summary_of(state), project_ref=_project_ref_of(state),
                                  purpose='draft', allow_create=True,
                                  allow_advance=(expected_token is None))
    except storage.StorageUnavailable as exc:
        return {'error': '草稿保存失败：' + str(exc)}
    return {'revision': result['revision'], 'seq': result['seq']}


def publish_draft(state, expected_token, note=''):
    """发布与草稿保存同一事务：CAS head → 草稿快照 → 发布快照 → 发布记录 → 引用。"""
    identifier = clean_id(state.get('projectId'))
    storage.ensure_ready()
    now = utcnow()
    outcome = {}

    def body(conn, save_fn, conflict):
        asset = store.get_asset(conn, KIND, identifier)
        if asset is None:
            raise ProjectNotFound('项目不存在')
        save = save_fn(conn, kind=KIND, external_id=identifier,
                       payload=_payload_of(state), payload_format=store.PAYLOAD_FORMAT_PROJECT,
                       expected_token=expected_token, name=str(state.get('name') or identifier),
                       summary=summary_of(state), project_ref=_project_ref_of(state),
                       purpose='draft', allow_create=True)
        labels = [row['version_label'] for row in store.release_rows(conn, asset['asset_uid'])]
        version = f'v{len(labels) + 1}'
        while version in labels:  # 唯一约束兜底前的防御
            version = 'v' + str(int(version[1:]) + 1)
        release_snapshot = store.append_snapshot(conn, asset['asset_uid'], _payload_of(state),
                                                 store.PAYLOAD_FORMAT_PROJECT, 'release', now=now)
        manifest = {'version': version, 'projectId': identifier,
                    'ontologyId': state.get('ontologyId', ''),
                    'ontologyVersion': str(state.get('ontologyVersion', '')),
                    'createdAt': now, 'revision': save['revision'],
                    'contentHash': revision(state),
                    'note': note or '项目配置快照；配置校验通过不等于已执行验证或上线'}
        store.append_release(conn, asset['asset_uid'], version, release_snapshot['snapshot_id'],
                             manifest, source_draft_id=save['snapshotId'], now=now)
        outcome.update({'version': version, 'revision': save['revision']})
        return outcome

    store.run_in_write_tx(body)
    return {'version': outcome['version'], 'revision': outcome['revision']}


def publish(state):
    """兼容入口：直接为当前状态追加一条发布记录（不动草稿 head）。"""
    identifier = clean_id(state.get('projectId'))
    storage.ensure_ready()
    now = utcnow()
    outcome = {}

    def body(conn):
        asset = store.get_asset(conn, KIND, identifier)
        if asset is None:
            raise ProjectNotFound('项目不存在')
        labels = [row['version_label'] for row in store.release_rows(conn, asset['asset_uid'])]
        version = f'v{len(labels) + 1}'
        while version in labels:
            version = 'v' + str(int(version[1:]) + 1)
        release_snapshot = store.append_snapshot(conn, asset['asset_uid'], _payload_of(state),
                                                 store.PAYLOAD_FORMAT_PROJECT, 'release', now=now)
        manifest = {'version': version, 'projectId': identifier,
                    'ontologyId': state.get('ontologyId', ''),
                    'ontologyVersion': str(state.get('ontologyVersion', '')),
                    'createdAt': now, 'revision': revision(state),
                    'note': '项目配置快照；配置校验通过不等于已执行验证或上线'}
        store.append_release(conn, asset['asset_uid'], version, release_snapshot['snapshot_id'],
                             manifest, now=now)
        outcome['version'] = version
        return outcome

    with write_tx() as tx:
        tx.run(body)
    return {'version': outcome['version']}


def published_versions(identifier):
    identifier = clean_id(identifier)
    storage.ensure_ready()
    with read_connection() as conn:
        asset = store.get_asset(conn, KIND, identifier)
        if asset is None:
            return []
        return [row['manifest'] for row in store.release_rows(conn, asset['asset_uid'])]


# --- configuration validation (V3 section 8) ----------------------------------
# B2 拆分：validate_project 及各职责校验移至 workbench.project_validation，
# 双方共用纯辅助移至 workbench.project_mapping；此处保留兼容转发入口，
# server.py 与既有测试经 projects.validate_project / projects.derive_display_names
# 调用不需改动。

from workbench.project_mapping import bare  # noqa: E402,F401  (upgrade_check 使用)
from workbench.project_mapping import derive_display_names  # noqa: F401  (兼容转发)
from workbench.project_validation import validate_project  # noqa: F401,E402  (兼容转发)


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
