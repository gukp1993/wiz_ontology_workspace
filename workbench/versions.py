"""Immutable ontology version registry on the workbench database.

发布记录与发布快照都存库（wb_releases + purpose=release 快照），一个发布事务内
与草稿保存一起原子完成（versions.publish）。版本号严格按数值递增，不按字符串
排序；同资产并发发布用 head CAS 串行分配。历史目录（ontology/releases/models）
只作为迁移输入与备份，在线读取不再触碰。
"""
import re

from workbench import auth
from workbench import storage
from workbench.storage import assets as store
from workbench.storage.engine import read_connection, write_tx, utcnow, read_head, read_snapshot
from workbench.model_format import decode_ontology, encode_ontology

KIND = 'model'


class VersionNotFound(ValueError):
    pass


def clean(identifier):
    from workbench import workspaces
    return workspaces.clean_id(identifier)


def listing(identifier):
    """版本清单（manifest 列表，按发布顺序）；不在线读取目录、不触发基础版本初始化。"""
    storage.ensure_ready()
    identifier = clean(identifier)
    with read_connection() as conn:
        asset = store.get_asset(conn, KIND, identifier, auth.require_user_id())
        if asset is None:
            return []
        return [row['manifest'] for row in store.release_rows(conn, asset['asset_uid'])]


def latest(identifier):
    versions = listing(identifier)
    return versions[-1] if versions else None


def _decode_release_payload(payload):
    """发布快照组件 → API state（legacy-release-v1 与 release-state-1 同构：组件字典）。"""
    ontology = payload.get('ontology')
    state = {'ontology': decode_ontology(ontology),
             'workflow': payload.get('workflow') or {'objective': {}, 'functions': [], 'actions': [], 'interfaces': []},
             'metrics': payload.get('metrics') or {'metrics': []},
             'rules': payload.get('rules') or {'rules': []}}
    if payload.get('layout') is not None:
        state['layout'] = payload['layout']
    return state


def read_state(identifier, version):
    storage.ensure_ready()
    identifier = clean(identifier)
    with read_connection() as conn:
        asset = store.get_asset(conn, KIND, identifier, auth.require_user_id())
        if asset is None:
            raise VersionNotFound('本体版本不存在')
        row = store.get_release_row(conn, asset['asset_uid'], str(version))
        if row is None:
            raise VersionNotFound('本体版本不存在')
        snapshot = read_snapshot(conn, row['snapshot_id'])
    if snapshot is None:
        raise VersionNotFound('本体版本不存在')
    import json
    state = _decode_release_payload(json.loads(snapshot['payload_json']))
    state['workspaceId'] = identifier
    return state


def _next_version(labels, change_type):
    numbers = []
    for version in labels:
        match = re.fullmatch(r'(\d+)\.(\d+)\.(\d+)', str(version))
        if match:
            numbers.append(tuple(int(x) for x in match.groups()))
    if not numbers:
        return '1.0.0'
    major, minor, patch = max(numbers)
    if change_type == 'breaking':
        return f'{major + 1}.0.0'
    return f'{major}.{minor + 1}.0'


def release_payload(state):
    """发布快照组件（保留旧目录发布的裁剪约定：空 metrics/rules 不写入）。"""
    return {'ontology': state['ontology'] if 'schemaVersion' in state['ontology'] else encode_ontology(state['ontology']),
            'workflow': state.get('workflow', {}),
            'metrics': state.get('metrics') or {},
            'rules': state.get('rules') or {},
            'layout': state.get('layout', {})}


def publish(identifier, state, meta, expected_token=None):
    """发布：草稿保存 + 发布快照 + 发布记录一个事务（原子）。

    expected_token：客户端草稿基线（head 存在时 CAS 校验；无 head 的首次发布由
    路由层完成空白基线核对后传 None）。CAS 失败抛 RevisionConflict（409）。
    返回 (entry, save_result)。
    """
    identifier = clean(identifier)
    owner = auth.require_user_id()
    storage.ensure_ready()
    from workbench import workspaces
    change_type = meta.get('changeType') or 'initial'
    now = utcnow()
    outcome = {}

    def body(conn, save_fn, conflict):
        from workbench import workspaces as _ws
        asset = store.get_asset(conn, KIND, identifier, owner)
        if asset is None:
            # 首次发布（无草稿）：与旧文件版一致，允许直接建立资产与首个草稿
            name = _ws.DEFAULT_NAME if identifier == _ws.DEFAULT_ID \
                else str((state.get('workflow') or {}).get('objective', {}).get('name') or identifier)
            asset_uid = store.ensure_asset(conn, KIND, identifier, name, {}, now, owner_user_id=owner)
        else:
            asset_uid = asset['asset_uid']
        head = read_head(conn, asset_uid)
        has_head = head is not None
        labels = [row['version_label'] for row in store.release_rows(conn, asset_uid)]
        parent = labels[-1] if labels else None
        version = _next_version(labels, change_type)
        save = save_fn(conn, kind=KIND, external_id=identifier,
                       payload=workspaces._payload_of(state),
                       payload_format=store.PAYLOAD_FORMAT_ONTOLOGY,
                       expected_token=expected_token if has_head else None,
                       summary=workspaces.summary_of(state),
                       allow_create=not has_head,
                       allow_advance=(expected_token is None),
                       owner_user_id=owner)
        release_snapshot = store.append_snapshot(conn, asset_uid, release_payload(state),
                                                 'release-state-1', 'release', now=now)
        entry = {'version': version, 'changeType': change_type,
                 'changeNote': meta.get('changeNote', ''), 'reviewer': meta.get('reviewer', ''),
                 'createdAt': now, 'revision': save['revision'], 'parentVersion': parent,
                 'reasons': meta.get('reasons', [])}
        store.append_release(conn, asset_uid, version, release_snapshot['snapshot_id'],
                             entry, source_draft_id=save['snapshotId'], now=now)
        outcome.update({'entry': entry})
        return outcome

    store.run_in_write_tx(body)
    return outcome['entry']


def ensure_base_release(identifier, owner_user_id=''):
    """显式迁移动作：从旧基础文件登记 1.0.0（imported），供项目引用历史版本。

    仅由迁移 CLI / 测试播种调用；在线读取路径不再触发文件扫描。
    owner_user_id：迁移导入时指定归属（CLI 传目标账号）；在线路径不经此函数。
    """
    from workbench import workspaces
    from workbench.paths import DATA_ROOT
    import json
    import yaml
    identifier = clean(identifier)
    # CLI/迁移路径没有请求上下文：按归属直查，不走需要登录态的 listing()
    storage.ensure_ready()
    with read_connection() as conn:
        existing = store.get_asset(conn, KIND, identifier, owner_user_id)
        if existing is not None and store.release_rows(conn, existing['asset_uid']):
            return None
    if identifier != 'storage':
        return None
    sources = {'ontology.json': DATA_ROOT / 'ontology/models/storage/ontology.json',
               'workflow.json': DATA_ROOT / 'ontology/models/storage/workflow.json',
               'metrics.yaml': DATA_ROOT / 'ontology/models/storage/metrics.yaml',
               'rules.yaml': DATA_ROOT / 'ontology/models/storage/rules.yaml'}
    if not all(p.is_file() for p in sources.values()):
        return None
    payload = {'ontology': json.loads(sources['ontology.json'].read_text()),
               'workflow': json.loads(sources['workflow.json'].read_text()),
               'metrics': yaml.safe_load(sources['metrics.yaml'].read_text()) or {},
               'rules': yaml.safe_load(sources['rules.yaml'].read_text()) or {},
               'layout': {}}
    now = utcnow()
    entry = {'version': '1.0.0', 'changeType': 'initial', 'changeNote': '由基础定义文件登记的历史版本',
             'reviewer': '', 'createdAt': now, 'revision': 'imported', 'parentVersion': None,
             'imported': True, 'reasons': []}

    def body(conn):
        asset_uid = store.ensure_asset(conn, KIND, identifier, workspaces.DEFAULT_NAME, {}, now,
                                       owner_user_id=owner_user_id)
        snapshot = store.append_snapshot(conn, asset_uid, payload, 'legacy-release-v1',
                                         'imported-base', legacy_revision='imported', now=now)
        store.append_release(conn, asset_uid, '1.0.0', snapshot['snapshot_id'], entry, now=now)

    with write_tx() as tx:
        tx.run(body)
    return entry
