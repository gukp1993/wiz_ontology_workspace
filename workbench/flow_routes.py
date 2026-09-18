"""函数编排路由：/api/flows、/api/flow-state、/api/flow-save、/api/flow-check、
/api/flow-copy、/api/flow-delete、/api/flow-run 与 LLM 提供方配置
（/api/llm-providers、/api/llm-provider-save|delete|test）的业务逻辑。
安全检查（Origin/白名单/大小限制）在 server.py 的 Handler 统一完成。

写操作持 workbench.locking 的同一把全局写锁；/api/flow-check 是纯结构校验，
不写盘、不持锁；/api/flow-run 与 /api/llm-provider-test 是真实执行/网络探测，
绝不持全局锁（全图运行仅用进程内 per-flow 互斥 + 短暂持锁核对 revision）。
LLM 密钥只写不读回：save/delete 响应只含元数据，test 只回连通结果。
"""
from workbench import api_credentials, flow_executor, flows, llm_client, llm_providers, projects
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
    return {'state': state, 'revision': flows.current_token(identifier)}, 200


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


def _project_id(payload):
    project_id = str(payload.get('projectId') or '')
    return projects.clean_id(project_id) if project_id else ''


def _credential_ids(payload):
    """项目 API 凭据 id 集合（HTTP 节点认证引用校验）；无项目上下文返回 None。"""
    try:
        project_id = _project_id(payload)
    except ValueError:
        return None
    if not project_id:
        return None
    try:
        return api_credentials.ids(project_id)
    except Exception:
        return None  # 凭据目录不可读时跳过该项检查，不阻断校验


def post_flow_save(payload):
    state = payload.get('state')
    if not isinstance(state, dict):
        return {'error': '请求缺少编排状态'}, 400
    try:
        identifier = flows.clean_id(state.get('flowId'))
    except ValueError as exc:
        return {'error': str(exc)}, 400
    llm_meta = llm_providers.list_metadata()
    credential_ids = _credential_ids(payload)
    with LOCK:
        expected = flows.current_token(identifier)
        if expected is None:
            return {'error': '编排不存在'}, 404
        if payload.get('revision') != expected:
            return {'error': '此编排已有新版本，请刷新后重试', 'currentRevision': expected}, 409
        try:
            result = flows.save_draft(state, expected_token=expected)
        except ValueError as exc:
            return {'error': str(exc)}, 400
    return {'revision': result['revision'],
            'check': flows.check_flow(state, _conn_context(payload), llm_meta, credential_ids)}, 200


def post_flow_check(payload):
    state = payload.get('state')
    if not isinstance(state, dict):
        return {'error': '请求缺少编排状态'}, 400
    try:
        flows.ensure_structure(state)
    except ValueError as exc:
        return {'error': str(exc)}, 400
    return flows.check_flow(state, _conn_context(payload),
                            llm_providers.list_metadata(), _credential_ids(payload)), 200


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


# --- 运行 / 测试（/api/flow-run） -------------------------------------------------

def post_flow_run(payload):
    state = payload.get('state')
    if not isinstance(state, dict):
        return {'error': '请求缺少编排状态'}, 400
    try:
        flows.ensure_structure(state)
    except ValueError as exc:
        return {'error': str(exc)}, 400
    targets = payload.get('targets')
    if targets is not None and (not isinstance(targets, list) or not targets
                                or not all(isinstance(t, str) and t for t in targets)):
        return {'error': 'targets 无效（应为节点 ID 列表）'}, 400
    try:
        ctx = flow_executor.load_context(_project_id(payload))
    except projects.ProjectNotFound as exc:
        return {'error': str(exc)}, 404
    llm_meta = llm_providers.list_metadata()
    credential_ids = ctx.get('credential_ids')

    if targets is None:  # 全图运行：revision 并发 gate + errors gate + 进程内互斥
        try:
            identifier = flows.clean_id(state.get('flowId'))
        except ValueError as exc:
            return {'error': str(exc)}, 400
        mutex = flow_executor.flow_mutex(identifier)
        if not mutex.acquire(blocking=False):
            return {'error': '此编排正在运行中，请等待本次运行完成后再试'}, 409
        try:
            with LOCK:  # 仅短暂持锁核对版本；执行阶段绝不持全局锁
                expected = flows.current_token(identifier)
                if expected is None:
                    return {'error': '编排不存在'}, 404
                if payload.get('revision') != expected:
                    return {'error': '此编排已有新版本，请刷新后重试', 'currentRevision': expected}, 409
            report = flows.check_flow(state, _conn_context(payload), llm_meta, credential_ids)
            if report['errors']:
                return {'error': '配置检查未通过，无法运行：' + '；'.join(report['errors'][:3]),
                        'check': report}, 422
            return flow_executor.run(state, None, payload.get('inputs'), ctx), 200
        finally:
            mutex.release()

    # 测试形态（单节点/链）：不落盘、无 revision 要求；只要求被测节点自身配置可执行
    try:
        flow_executor.validate_chain(state, targets)
    except ValueError as exc:
        return {'error': str(exc)}, 400
    report = flows.check_flow(state, _conn_context(payload), llm_meta, credential_ids)
    target_errors = [issue for item in report['items']
                     if item['kind'] == 'node' and item['id'] in set(targets) and item['level'] == 'error'
                     for issue in item['issues']]
    if target_errors:
        return {'error': '被测节点配置有误：' + '；'.join(target_errors[:3]), 'check': report}, 422
    return flow_executor.run(state, targets, payload.get('inputs'), ctx), 200


# --- LLM 提供方配置（密钥只写不读回） ---------------------------------------------

def get_llm_providers(query):
    return {'items': llm_providers.list_metadata()}, 200


def post_llm_provider_save(payload):
    try:
        with LOCK:  # 本地快写，短暂持锁
            saved = llm_providers.save(name=payload.get('name'), endpoint=payload.get('endpoint'),
                                       model=payload.get('model'), api_key=payload.get('apiKey') or '',
                                       timeout=payload.get('timeout') or 60,
                                       temperature=payload.get('temperature') if payload.get('temperature') is not None else 0,
                                       is_default=bool(payload.get('isDefault')),
                                       provider_id=str(payload.get('providerId') or ''))
    except ValueError as exc:
        return {'error': str(exc)}, 400
    return {'saved': True, 'provider': saved}, 200


def post_llm_provider_delete(payload):
    try:
        provider_id = str(payload.get('providerId') or '')
        with LOCK:
            llm_providers.clear(provider_id)
    except ValueError as exc:
        return {'error': str(exc)}, 400
    return {'cleared': True}, 200


def post_llm_provider_test(payload):
    """连通性探测：按已存 providerId 测试，或按请求携带的临时配置测试（不落盘）。"""
    try:
        provider_id = str(payload.get('providerId') or '')
        if provider_id:
            provider = llm_providers.read(provider_id)
            if provider is None:
                return {'error': 'LLM 提供方不存在或已被删除'}, 404
        else:
            provider = {'name': payload.get('name'), 'endpoint': payload.get('endpoint'),
                        'model': payload.get('model'), 'api_key': payload.get('apiKey') or '',
                        'timeout': payload.get('timeout') or 30,
                        'temperature': payload.get('temperature') if payload.get('temperature') is not None else 0}
    except ValueError as exc:
        return {'error': str(exc)}, 400
    return llm_client.test_connect(provider), 200
