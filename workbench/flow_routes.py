"""函数编排路由：/api/flows、/api/flow-state、/api/flow-save、/api/flow-check、
/api/flow-copy、/api/flow-delete 的业务逻辑。安全检查（Origin/白名单/大小限制）
在 server.py 的 Handler 统一完成。

写操作持 workbench.locking 的同一把全局写锁；/api/flow-check 是纯结构校验，
不写盘、不持锁、不执行任何代码（一期无执行语义）。
"""
from workbench import flows
from workbench.locking import LOCK


def get_flows(query):
    include_deleted = str(query.get('includeDeleted', [''])[0]).lower() in ('1', 'true')
    return {'items': flows.listing(include_deleted=include_deleted)}, 200


def get_flow_state(query):
    try:
        identifier = flows.clean_id(query.get('flow', [''])[0])
        state = flows.read_draft(identifier)
    except ValueError as exc:
        return {'error': str(exc)}, 400
    if state is None:
        return {'error': '编排不存在'}, 404
    return {'state': state, 'revision': flows.revision_of(state)}, 200


def post_create_flow(payload):
    try:
        with LOCK:
            created = flows.create(payload.get('name'), payload.get('description') or '')
    except ValueError as exc:
        return {'error': str(exc)}, 400
    return created, 201


def _conn_context(payload):
    """客户端随请求携带的项目数据连接上下文（仅 id/name/engine 元数据，无凭据）。"""
    conns = payload.get('connections')
    if isinstance(conns, list):
        return [c for c in conns if isinstance(c, dict)]
    return None


def post_flow_save(payload):
    state = payload.get('state')
    if not isinstance(state, dict):
        return {'error': '请求缺少编排状态'}, 400
    try:
        identifier = flows.clean_id(state.get('flowId'))
    except ValueError as exc:
        return {'error': str(exc)}, 400
    with LOCK:
        existing = flows.read_draft(identifier)
        if existing is None:
            return {'error': '编排不存在'}, 404
        expected = flows.revision_of(existing)
        if payload.get('revision') != expected:
            return {'error': '此编排已有新版本，请刷新后重试', 'currentRevision': expected}, 409
        try:
            result = flows.save_draft(state)
        except ValueError as exc:
            return {'error': str(exc)}, 400
    return {'revision': result['revision'],
            'check': flows.check_flow(state, _conn_context(payload))}, 200


def post_flow_check(payload):
    state = payload.get('state')
    if not isinstance(state, dict):
        return {'error': '请求缺少编排状态'}, 400
    try:
        flows.ensure_structure(state)
    except ValueError as exc:
        return {'error': str(exc)}, 400
    return flows.check_flow(state, _conn_context(payload)), 200


def post_flow_copy(payload):
    try:
        with LOCK:
            created = flows.copy_flow(payload.get('flowId'), payload.get('name'))
    except flows.FlowNotFound as exc:
        return {'error': str(exc)}, 404
    except ValueError as exc:
        return {'error': str(exc)}, 400
    return created, 201


def post_flow_delete(payload):
    try:
        with LOCK:
            flows.soft_delete(payload.get('flowId'))
    except flows.FlowNotFound as exc:
        return {'error': str(exc)}, 404
    except ValueError as exc:
        return {'error': str(exc)}, 400
    return {'ok': True}, 200
