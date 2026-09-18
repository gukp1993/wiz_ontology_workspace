"""Ontology asset registry and draft store on the workbench database (V3+DB).

在线权威读写全部经 workbench.storage Repository：资产登记、当前 head、不可变
快照。旧文件目录（ontology/drafts/models、workspaces/）只是迁移输入与备份，
在线服务不再读写；revision 是不透明 token（首次保存前为迁移兼容值），与内容
hash 分离（workspaces.revision_of 仅用于审计/导出与无 head 时的空白基线）。
"""
import copy
from uuid import uuid4

from workbench import storage
from workbench.storage import assets as store
from workbench.storage.engine import read_connection, write_tx
from workbench.model_format import encode_state, decode_state

DEFAULT_ID = 'storage'
DEFAULT_NAME = '储能本体'
KIND = 'model'


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
    from uuid import UUID
    try:
        if str(UUID(identifier)) == identifier:
            return identifier
    except (ValueError, AttributeError):
        pass
    raise ValueError('本体标识必须为 storage 或规范 UUID')


def describe(identifier=None):
    identifier = clean_id(identifier)
    if identifier == DEFAULT_ID:
        return {'id': DEFAULT_ID, 'name': DEFAULT_NAME}
    storage.ensure_ready()
    with read_connection() as conn:
        asset = store.get_asset(conn, KIND, identifier)
    if asset is None:
        raise WorkspaceNotFound('本体不存在')
    if not asset['name']:
        raise ValueError('本体登记信息无效')
    return {'id': identifier, 'name': asset['name']}


def listing():
    """默认 storage 本体只在持有草稿时出现（与文件版语义一致：资产行随首次保存建立）。"""
    storage.ensure_ready()
    with read_connection() as conn:
        rows = store.list_assets(conn, KIND)
    return [{'id': row['external_id'], 'name': row['name']}
            for row in rows if row['name']]


def create(name, blank):
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
        raise ValueError('本体名称需要填写 1～80 个字符')
    name = name.strip()
    storage.ensure_ready()
    identifier = str(uuid4())
    state = copy.deepcopy(blank)
    state['workspaceId'] = identifier
    state['workflow']['objective']['name'] = name

    def body(conn):
        store.bump_guard(conn, 'asset-name:model')
        if store.name_taken(conn, KIND, name):
            raise DuplicateName('已存在同名本体，请换一个名称')
        return store._save_draft_in_conn(conn, KIND, identifier, _payload_of(state),
                                         store.PAYLOAD_FORMAT_ONTOLOGY, expected_token=None,
                                         name=name, summary=summary_of(state), project_ref=None,
                                         purpose='draft', legacy_revision='',
                                         allow_create=True, allow_advance=False,
                                         now=None, conflict={})

    with write_tx() as tx:
        tx.run(body)
    return {'id': identifier, 'name': name}


# --- draft store（DB 版；DRAFT_FILES 仅为迁移/导出兼容保留映射名） ----------------

DRAFT_FILES = (('ontology', 'ontology.json'), ('workflow', 'workflow.json'),
               ('metrics', 'metrics.yaml'), ('rules', 'rules.yaml'), ('layout', 'layout.json'))


def _payload_of(state):
    """在线保存的快照形态：encode_state 结果整体入库（schemaVersion 本体 + 组件）。"""
    return encode_state({k: v for k, v in state.items() if not str(k).startswith('_')})


def _state_of_payload(payload, identifier):
    state = decode_state(copy.deepcopy(payload))
    state['workspaceId'] = clean_id(identifier)
    return state


def draft_exists(identifier):
    storage.ensure_ready()
    return store.read_head(KIND, clean_id(identifier)) is not None


def revision_of(state):
    """内容 hash：仅用于空白基线/审计与导出兼容，不再作为并发令牌。"""
    value = encode_state({k: v for k, v in state.items() if not str(k).startswith('_')})
    value['workspaceId'] = clean_id(state.get('workspaceId'))
    import hashlib
    import json
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def current_token(identifier):
    """head 的不透明 revision token；无草稿返回 None。"""
    storage.ensure_ready()
    return store.current_token(KIND, clean_id(identifier))


def read_draft(identifier):
    storage.ensure_ready()
    current = store.read_current(KIND, clean_id(identifier))
    if current is None:
        return None
    return _state_of_payload(current['snapshot']['payload'], identifier)


def write_draft(state, expected_token=None, blank_baseline=False):
    """提交整份草稿快照并 CAS 推进 head。

    expected_token：客户端基线（路由层必传，head 存在时 CAS 校验）；None = 内部
    路径按当前 head 推进（旧文件版本模块层不做 revision 检查，语义一致）。
    blank_baseline=True：资产尚无草稿，路由层已按空白内容 hash 核对基线，
    此处直接建立首个 head。
    """
    identifier = clean_id(state.get('workspaceId'))
    storage.ensure_ready()
    result = store.save_draft(KIND, identifier, _payload_of(state),
                              store.PAYLOAD_FORMAT_ONTOLOGY,
                              expected_token=None if blank_baseline else expected_token,
                              name=None, summary=summary_of(state),
                              allow_create=True, allow_advance=(expected_token is None))
    return {'revision': result['revision'], 'seq': result['seq']}


def legacy_draft_state(identifier):
    """一次性迁移输入：旧单文件草稿（项目部分剥离）。仅供 transfer/测试导入使用，
    在线读取不再回退到该文件。"""
    from workbench.paths import DATA_ROOT
    import json
    path = DATA_ROOT / 'ontology/drafts/draft.json' if identifier == DEFAULT_ID \
        else DATA_ROOT / 'ontology/workspaces' / clean_id(identifier) / 'draft.json'
    if not path.is_file():
        return None
    state = json.loads(path.read_text())
    for key in ('bindings', 'parameters'):
        state.pop(key, None)
    state['workspaceId'] = clean_id(identifier)
    return decode_state(state)


def summary_of(state):
    """派生列表摘要：与 head 同步更新；实时校验结论不在此冒充。"""
    ontology = state.get('ontology') or {}
    try:
        encoded = encode_state({'ontology': ontology})['ontology']
        definition_count = sum(len(encoded.get(group) or [])
                               for group in ('objectTypes', 'linkTypes', 'properties',
                                             'sharedProperties', 'valueTypes', 'metadata'))
    except Exception:
        definition_count = 0
    return {'definitionCount': definition_count,
            'functionCount': len((state.get('workflow') or {}).get('functions') or [])}
