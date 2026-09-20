"""Project binding store on the workbench database (base files, drafts, releases).

项目三态（base 初始化输入 / 草稿 / 发布）全部入库：资产 + head（imported-base 或
draft）+ 不可变快照 + 每快照至多一条的本体版本引用（wb_project_refs）。旧目录
ontology/projects、drafts/projects、releases/projects 只是迁移输入与备份。
表结构目录（catalogs）是服务端派生数据，仍不入草稿/快照（保存前剥离）。
"""
from datetime import datetime, timezone
from uuid import uuid4
import copy

from workbench import auth
from workbench import storage
from workbench.storage import assets as store
from workbench.storage import configuration as config_store
from workbench.storage.engine import read_connection, write_tx, utcnow

KIND = 'project'
FILE_ORDER = (('project', 'project.yaml'), ('connections', 'connections.yaml'),
              ('bindings', 'bindings.yaml'), ('implementations', 'implementations.yaml'),
              ('parameters', 'parameters.yaml'))


class ProjectNotFound(ValueError):
    pass


class DependencyChanged(Exception):
    """发布提交窗口内依赖（编排修订/目录缓存）发生变化：拒绝发布，零写入。

    2026-09-20 v2 冻结协议（03 分册 §2.3）：校验通过后、提交落库前复核依赖快照，
    不一致即拒绝；路由层转为 409 + reason='DEPENDENCY_CHANGED'，客户端重新检查后再发布。
    """


class IdempotencyConflict(Exception):
    """同一 requestId 被不同内容的请求复用：409，不产生新版本（03 分册 §2.3）。"""


class ReferenceInUse(Exception):
    """本次保存删除的连接仍被同一份项目状态引用：422 REFERENCE_IN_USE（03 分册 §2.1）。

    `references` 为结构化引用位置（kind/id/name），连接与其全部引用在同一份状态中
    一并移除 = 显式删除，放行；仅当「服务端已存但有引用、本次提交删除」时拒绝。
    """

    def __init__(self, message, references=None):
        super().__init__(message)
        self.references = references or []


# --- 项目状态内的引用扫描（L 接线用；与 project_validation 的引用形态对齐） ------------

def referenced_connection_ids(state):
    """状态中引用了哪些数据连接（稳定 id；不按名称匹配）。

    覆盖形态（与校验器一致）：对象实例来源、补充来源（sources/related_sources）、
    属性来源 database/redis 直连与 inlineSql（新旧两种 mode）、queryRule/契约实现。
    """
    ids = set()

    def add(value):
        text = str(value or '').strip()
        if text:
            ids.add(text)

    for b in state.get('bindings', {}).get('object_bindings') or []:
        if not isinstance(b, dict):
            continue
        add(b.get('connection'))
        for source in (b.get('sources') or []):
            if isinstance(source, dict):
                add(source.get('connection'))
        for source in (b.get('related_sources') or []):
            if isinstance(source, dict):
                add(b.get('connection'))  # 旧格式来源固定使用绑定自身连接
        for value in (b.get('properties') or {}).values():
            if not isinstance(value, dict):
                continue
            add(value.get('connection'))
            inline = value.get('inlineSql') or value.get('inline')
            if isinstance(inline, dict):
                add(inline.get('connection'))
    for impl in state.get('implementations') or []:
        if isinstance(impl, dict):
            add(impl.get('connection'))
    return ids


def referenced_flow_ids(state):
    """状态中引用了哪些函数编排（属性 kind=flow + 动作绑定 kind=flow）。"""
    ids = set()
    for b in state.get('bindings', {}).get('object_bindings') or []:
        if not isinstance(b, dict):
            continue
        for value in (b.get('properties') or {}).values():
            if isinstance(value, dict) and value.get('kind') == 'flow':
                fid = str(value.get('flow') or '').strip()
                if fid:
                    ids.add(fid)
    for row in state.get('bindings', {}).get('actionBindings') or []:
        if not isinstance(row, dict):
            continue
        impl = row.get('implementation')
        if isinstance(impl, dict) and impl.get('kind') == 'flow':
            fid = str(impl.get('flowId') or '').strip()
            if fid:
                ids.add(fid)
    return ids


def connection_references(state, connection_ids):
    """连接的引用位置明细（保存边界 422 的 `references` 结构）。

    返回 {connectionId: [{'kind', 'id', 'name'}]}；只统计状态中确实存在的引用，
    供「删被引用连接」的拒绝响应与前端定位使用（kind 与 items 定位口径一致）。
    """
    wanted = {str(c) for c in connection_ids}
    found = {cid: [] for cid in wanted}

    def hit(cid, kind, ref_id, name):
        cid = str(cid or '').strip()
        if cid in wanted:
            found[cid].append({'kind': kind, 'id': str(ref_id or ''), 'name': str(name or '')})
            return True
        return False

    bindings = state.get('bindings', {}) or {}
    for b in bindings.get('object_bindings') or []:
        if not isinstance(b, dict):
            continue
        otype = str(b.get('object_type') or b.get('objectType') or '')
        if hit(b.get('connection'), 'objectBinding', otype, f'对象实例来源 · {otype}'):
            pass
        for source in (b.get('sources') or []):
            if isinstance(source, dict):
                hit(source.get('connection'), 'objectSource', source.get('id'),
                    f"{otype} · 来源 {source.get('name') or source.get('id') or ''}")
        for source in (b.get('related_sources') or []):
            if isinstance(source, dict):
                hit(b.get('connection'), 'objectSource', source.get('id'),
                    f"{otype} · 历史来源 {source.get('name') or source.get('id') or ''}")
        for prop, value in (b.get('properties') or {}).items():
            if not isinstance(value, dict):
                continue
            hit(value.get('connection'), 'propertySource', f'{otype}.{prop}', f'{otype}.{prop} 属性来源')
            inline = value.get('inlineSql') or value.get('inline')
            if isinstance(inline, dict):
                hit(inline.get('connection'), 'propertySource', f'{otype}.{prop}', f'{otype}.{prop} 内联 SQL')
    for impl in state.get('implementations') or []:
        if isinstance(impl, dict):
            hit(impl.get('connection'), 'implementation', impl.get('id'),
                f"实现 {impl.get('name') or impl.get('id') or ''}")
    return {cid: refs for cid, refs in found.items() if refs}


def save_boundary_issues(stored, submitted):
    """保存边界（03 分册 §2.1）：本次提交删除了仍被同一份状态引用的连接 → 拒绝。

    「连接与其全部引用同批移除」= 显式删除，放行；只删连接、引用仍在 → 拒绝并列出引用位置。
    """
    def ids_of(state):
        return {str(c.get('id')) for c in (state.get('connections') or {}).get('connections') or []
                if isinstance(c, dict) and c.get('id')}

    removed = ids_of(stored) - ids_of(submitted)
    if not removed:
        return []
    refs = connection_references(submitted, removed)
    issues = []
    for cid, entries in refs.items():
        for entry in entries:
            issues.append({'connectionId': cid, **entry})
    return issues


def idempotent_replay(state, owner_key, request_key, request_hash):
    """发布幂等前置回放（03 分册 §2.3）：同 key 同内容返回既有回执；同 key 异内容拒绝。

    回放优先于 CAS：首答丢失后客户端可能带着**旧 revision** 重试，此时草稿已被那次
    成功发布推进——按 requestId 回放同一结果，而不是报冲突。无回执返回 None。
    """
    storage.ensure_ready()
    with read_connection() as conn:
        receipt = config_store.get_request_receipt(conn, 'project-publish', owner_key, request_key)
    if receipt is None:
        return None
    if receipt['request_hash'] != request_hash:
        raise IdempotencyConflict('requestId 已被不同的发布请求使用，请刷新后重试')
    return receipt['response']


def dependency_probe(conn, state):
    """依赖快照（L02/L03 冻结协议）：被引用编排的 head 令牌 + 目录缓存指纹与代际。

    `conn` 由调用方提供：校验基线用只读连接；发布重验在写事务内用同一连接读取，
    因此复核与提交在同一事务边界内（SQLite BEGIN IMMEDIATE 串行化写入者）。
    """
    owner = auth.require_user_id()
    project_id = clean_id(state.get('projectId'))
    out = {'flows': {}, 'catalogs': {}}
    import sqlalchemy as _sa
    for fid in sorted(referenced_flow_ids(state)):
        row = conn.execute(
            _sa.text('SELECT h.revision_token FROM wb_asset_heads h '
                  'JOIN wb_assets a ON a.asset_uid = h.asset_uid '
                  'WHERE a.kind = :k AND a.external_id = :e AND a.owner_user_id = :o '
                  'AND a.deleted_at IS NULL'),
            {'k': 'flow', 'e': fid, 'o': owner}).first()
        if row is None:  # 编排不存在 / 非本账号 → 空令牌（与缺失同判，发布重验会捕获）
            out['flows'][fid] = ''
            continue
        token = row[0]
        out['flows'][fid] = token.decode() if isinstance(token, (bytes, bytearray)) else str(token or '')
    asset = store.get_asset(conn, KIND, project_id, owner)
    if asset is not None:
        rows = conn.execute(
            _sa.text('SELECT connection_id, config_fingerprint, generation FROM wb_catalog_cache '
                     'WHERE project_uid = :p'),
            {'p': asset['asset_uid']}).all()
        for connection_id, fingerprint, generation in rows:
            out['catalogs'][connection_id] = f'{str(fingerprint or "")}:{int(generation or 0)}'
    return out


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
    """head 的不透明 revision token；项目不存在（或非本账号）返回 None。"""
    storage.ensure_ready()
    return store.current_token(KIND, clean_id(identifier), owner_user_id=auth.require_user_id())


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
    current = store.read_current(KIND, identifier, owner_user_id=auth.require_user_id())
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
    owner = auth.require_user_id()
    storage.ensure_ready()
    with read_connection() as conn:
        rows = store.list_assets(conn, KIND, owner)
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
    owner = auth.require_user_id()
    storage.ensure_ready()
    ontology_id = ontology_id or ''
    ontology_version = str(ontology_version or '')
    identifier = uuid4().hex[:12]
    state = _empty_state(identifier, name, ontology_id, ontology_version)
    payload = _payload_of(state)

    def body(conn):
        store.bump_guard(conn, 'asset-name:project')
        # 名称唯一范围与旧行为一致：同名且（未指明本体 或 同一本体）才冲突
        rows = store.list_assets(conn, KIND, owner)
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
            conflict={}, owner_user_id=owner)
        return result

    with write_tx() as tx:
        tx.run(body)
    return {'id': identifier, 'name': name, 'ontologyId': ontology_id,
            'ontologyVersion': ontology_version}


def save_draft(state, expected_token=None):
    """保存项目草稿。expected_token：客户端基线（路由层必传）；None = 内部/测试
    路径，按当前 head 推进（旧文件版模块层不做 revision 检查，语义保持一致）。"""
    identifier = clean_id(state.get('projectId'))
    owner = auth.require_user_id()
    storage.ensure_ready()
    try:
        result = store.save_draft(KIND, identifier, _payload_of(state),
                                  store.PAYLOAD_FORMAT_PROJECT, expected_token=expected_token,
                                  name=str(state.get('name') or identifier),
                                  summary=summary_of(state), project_ref=_project_ref_of(state),
                                  purpose='draft', allow_create=True,
                                  allow_advance=(expected_token is None), owner_user_id=owner)
    except storage.StorageUnavailable as exc:
        return {'error': '草稿保存失败：' + str(exc)}
    return {'revision': result['revision'], 'seq': result['seq']}


def publish_draft(state, expected_token, note='', idempotency=None, expected_deps=None, deps_probe=None):
    """发布与草稿保存同一事务：CAS head → 草稿快照 → 发布快照 → 发布记录 → 引用。

    2026-09-20 v2 冻结协议（03 分册 §2.3）：
    - `idempotency`（可选）：{'owner_key', 'request_key', 'request_hash'} —— 幂等回执
      与发布写入**同一事务**；同 key 同 hash 回放既有结果（不写任何东西、不推进
      revision），同 key 异 hash 抛 IdempotencyConflict（409）。
    - `expected_deps` + `deps_probe`（可选）：提交前在写事务内复核依赖快照
      （编排 head 令牌 + 目录缓存内容指纹）；不一致抛 DependencyChanged（零写入）。
      SQLite 下端到端由 BEGIN IMMEDIATE 串行化写入者保证窗口内无其他提交。
    """
    identifier = clean_id(state.get('projectId'))
    owner = auth.require_user_id()
    storage.ensure_ready()
    now = utcnow()
    outcome = {}

    def body(conn, save_fn, conflict):
        asset = store.get_asset(conn, KIND, identifier, owner)
        if asset is None:
            raise ProjectNotFound('项目不存在')
        if idempotency:
            receipt = config_store.get_request_receipt(conn, 'project-publish',
                                                       idempotency['owner_key'], idempotency['request_key'])
            if receipt is not None:
                if receipt['request_hash'] != idempotency['request_hash']:
                    raise IdempotencyConflict('requestId 已被不同的发布请求使用，请刷新后重试')
                outcome['replay'] = receipt['response']
                return outcome
        if expected_deps is not None and deps_probe is not None and deps_probe(conn) != expected_deps:
            raise DependencyChanged('发布提交前检测到依赖发生变化（编排或目录已更新），请重新检查后再发布')
        save = save_fn(conn, kind=KIND, external_id=identifier,
                       payload=_payload_of(state), payload_format=store.PAYLOAD_FORMAT_PROJECT,
                       expected_token=expected_token, name=str(state.get('name') or identifier),
                       summary=summary_of(state), project_ref=_project_ref_of(state),
                       purpose='draft', allow_create=True, owner_user_id=owner)
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
        if idempotency:
            # 回执与发布写入同一事务：不存在「已发布未记回执」的崩溃窗口
            config_store.put_request_receipt(
                conn, 'project-publish', idempotency['owner_key'], idempotency['request_key'],
                idempotency['request_hash'],
                {'version': version, 'revision': save['revision'],
                 'ontologyId': manifest['ontologyId'], 'ontologyVersion': manifest['ontologyVersion']},
                now=now)
        outcome.update({'version': version, 'revision': save['revision']})
        return outcome

    store.run_in_write_tx(body)
    if 'replay' in outcome:
        prior = outcome['replay']
        return {'version': prior.get('version', ''), 'revision': prior.get('revision', ''),
                'ontologyId': prior.get('ontologyId', ''), 'ontologyVersion': prior.get('ontologyVersion', ''),
                'replay': True}
    return {'version': outcome['version'], 'revision': outcome['revision']}


def publish(state):
    """兼容入口：直接为当前状态追加一条发布记录（不动草稿 head）。"""
    identifier = clean_id(state.get('projectId'))
    owner = auth.require_user_id()
    storage.ensure_ready()
    now = utcnow()
    outcome = {}

    def body(conn):
        asset = store.get_asset(conn, KIND, identifier, owner)
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
    owner = auth.require_user_id()
    storage.ensure_ready()
    with read_connection() as conn:
        asset = store.get_asset(conn, KIND, identifier, owner)
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
    from workbench.project_impact import binding_impacts
    diff = classify(current_state, target_state)
    # 绑定级影响由 project_impact 纯函数按稳定身份匹配（2026-09-20 v2）：
    # 稳定 id 精确匹配、共享属性继承（sharedPropertyId）与 valueTypeId 计入，
    # 修正旧子串/endswith 匹配的误报（删未绑定定义不再牵连同名属性）与漏报（共享继承/相似 id）。
    impacts, matched = binding_impacts(state, current_state, target_state, diff['reasons'])
    for reason in diff['reasons']:
        area, rid, severity, text = reason['area'], reason['id'], reason['severity'], reason['text']
        if (area, rid) not in matched and severity in ('breaking', 'pending'):
            impacts.append({'area': 'ontology', 'ref': rid, 'severity': severity, 'text': text + '；本项目当前配置未直接引用'})
    # 项目说明失效（2026-09-19）：说明指向的对象/属性/链接/动作在目标版本中消失 → 定位提示，不静默丢失
    from workbench import mapping_descriptions
    for ref in mapping_descriptions.stale_references(state, target_state):
        where = ref['id'] + ((' / ' + ref['item_id']) if ref['item_id'] else '')
        impacts.append({'area': 'mappingDescription', 'ref': where, 'severity': 'pending',
                        'text': '项目说明指向的元素在目标版本中不存在（' + ref['text'] + '）；升级后该条说明将失效，请先修复或清除'})
    blocking = [i for i in impacts if i['severity'] == 'breaking' and i['area'] != 'ontology']
    return {'classification': diff['type'], 'reasons': diff['reasons'], 'impacts': impacts, 'blocking': blocking}
