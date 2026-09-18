"""项目区路由处理（B3 + DB 存储切换）：/api/projects、project-state/-save/-validate/
-publish/-upgrade-check、project-releases、connection-*、catalog-refresh 的业务逻辑。
安全检查（Origin/白名单/大小限制）在 server.py 的 Handler 统一完成。

revision 是不透明 token（读自项目 head）；发布走 publish_draft 单事务（草稿快照 +
CAS + 发布记录 + 固定引用），失败绝不返回成功。connection-test / connection-catalog /
catalog-refresh 是真实网络探测，绝不持有全局写锁；迟到目录结果按配置指纹丢弃。
"""
from workbench import projects, versions, workspaces, contracts
from workbench import dbdrivers
from workbench import project_property_reader
from workbench import secrets as secrets_store
from workbench import api_credentials
from workbench import catalogs as catalog_store
from workbench.locking import LOCK


def referenced_ontology(project_state):
    """Load the ontology state of the version a project pins; raises if missing."""
    ontology_id = workspaces.clean_id(project_state.get('ontologyId'))
    return versions.read_state(ontology_id, str(project_state.get('ontologyVersion', '')))


# --- GET 路由 -----------------------------------------------------------------------

def get_projects(query):
    """GET /api/projects：显式筛选契约（2026-09-18 批次 B / R5）。

    - 无 `ontology` 参数：返回全部项目（其他任何参数都不改变范围）；
    - `?ontology=<有效ID>`：仅该本体的项目（不存在 → WorkspaceNotFound → 404）；
    - `?ontology=`（空值）：400 参数错误（查询全部时应省略该参数）。

    废除原「URL 带任意 query 串即按 ontology 参数过滤」的隐式语义
    （前端 listProjects 无筛选时不带参数，已核实无兼容问题）。
    """
    if 'ontology' in query:
        identifier = query['ontology'][0]
        if not identifier:
            return {'error': 'ontology 参数不能为空；查询全部项目时请省略该参数',
                    'code': 'INVALID_ARGUMENT'}, 400
        identifier = workspaces.describe(identifier)['id']
    else:
        identifier = None
    items = projects.listing(identifier)
    for item in items:
        try:
            item['latestVersion'] = (versions.latest(item['ontologyId']) or {}).get('version', '')
        except (ValueError, OSError):
            item['latestVersion'] = ''
    return {'items': items}, 200


def get_project_state(query):
    project_id = query.get('project', [''])[0]
    state, saved = projects.load(project_id)
    # 表结构目录是服务端派生数据：加载时注入缓存内容，随草稿/快照剥离。
    state['bindings']['catalogs'] = {**state['bindings'].get('catalogs', {}), **catalog_store.load_all(state['projectId'])}
    payload = {'state': state, 'revision': projects.current_token(state['projectId']), 'saved': saved,
               'project': {'id': state['projectId'], 'name': state['name'], 'ontologyId': state['ontologyId'], 'ontologyVersion': state['ontologyVersion']}}
    if state.get('ontologyId') and state.get('ontologyVersion'):
        try:
            ontology_state = referenced_ontology(state)
            payload['migrationTodos'] = contracts.value_source_todos(ontology_state)
            # 显示名称跟随本体引用版本的属性标记，读取时自动推导
            projects.derive_display_names(state, ontology_state)
        except (ValueError, OSError):
            payload['migrationTodos'] = []
            payload['warning'] = '引用的本体版本不存在，请先升级到已发布版本'
    else:
        payload['migrationTodos'] = []
    return payload, 200


def get_project_releases(query):
    project_id = query.get('project', [''])[0]
    return {'items': projects.published_versions(project_id)}, 200


def get_api_credentials(query):
    """GET /api/api-credentials：项目级 API 凭据元数据列举（只读，绝不返回密钥）。

    响应只有 id/name；密钥只存在于服务端 vault，客户端仅保存凭据 ID。
    """
    project_id = projects.clean_id(query.get('project', [''])[0])
    projects.load(project_id)  # 项目不存在时抛 ProjectNotFound → 404
    return {'items': api_credentials.list_metadata(project_id)}, 200


def get_project_config(query):
    """旧辅助接口：storage 本体的 chuangzhi 项目配置（供历史组件读取，保留兼容）。

    数据改经项目 Repository 读取；项目不存在时保持原"未配置"响应形态。
    """
    identifier = workspaces.describe(query.get('ontology', ['storage'])[0])['id']
    if identifier != 'storage':
        return {'project': {}, 'connections': [], 'available': False, 'message': '当前本体尚未配置项目与数据连接。'}, 200
    try:
        state, _saved = projects.load('chuangzhi')
    except projects.ProjectNotFound:
        return {'project': {}, 'connections': [], 'available': False, 'message': '当前本体尚未配置项目与数据连接。'}, 200
    meta = state.get('projectMeta') or {}
    project_view = {k: meta.get(k) for k in ('timezone', 'model_version', 'config_version', 'demo_only') if k in meta}
    project_view.update({'project_id': state.get('projectId'), 'name': state.get('name', '')})
    connections = [{'id': c.get('id'), 'adapter': c.get('adapter', ''), 'path': c.get('path', '')}
                   for c in (state.get('connections') or {}).get('connections', []) if isinstance(c, dict)]
    return {'project': project_view, 'connections': connections, 'available': True}, 200


# --- POST 路由 ----------------------------------------------------------------------

def post_create_project(payload):
    with LOCK:
        ontology = payload.get('ontology') or ''; version = str(payload.get('version') or '')
        if ontology:
            info = workspaces.describe(ontology); versions.read_state(info['id'], version)
            created = projects.create(payload.get('name'), info['id'], version)
        else:
            created = projects.create(payload.get('name'))
    return created, 201


def post_project_write(payload, path):
    """/api/project-save|project-validate|project-publish|project-upgrade-check 公共前置。"""
    project_state = projects._normalize(payload['state'])
    projects.clean_id(project_state.get('projectId'))
    # 目录以服务端文件存储为准：避免不同客户端的过期副本影响校验与修订哈希。
    project_state['bindings']['catalogs'] = catalog_store.load_all(project_state['projectId'])
    has_reference = bool(project_state.get('ontologyId') and project_state.get('ontologyVersion'))
    if path == '/api/project-validate':
        if not has_reference:
            return {'errors': ['该项目尚未绑定本体版本：请在项目信息中选择引用的本体与版本。'], 'warnings': [],
                    'items': [{'kind': 'reference', 'id': 'reference', 'name': '本体引用', 'status': 'unconfigured', 'issues': ['尚未绑定本体版本']}]}, 200
        ontology_state = referenced_ontology(project_state)
        return projects.validate_project(project_state, ontology_state), 200
    if path == '/api/project-upgrade-check':
        if not has_reference:
            return {'error': '该项目尚未绑定本体版本，无需升级；请先在项目信息中完成绑定。'}, 422
        ontology_state = referenced_ontology(project_state)
        target = versions.read_state(workspaces.clean_id(project_state.get('ontologyId')), str(payload.get('targetVersion', '')))
        return projects.upgrade_check(project_state, ontology_state, target), 200
    with LOCK:
        existing, _ = projects.load(project_state['projectId'])
        expected = projects.current_token(project_state['projectId'])
        if payload.get('revision') != expected:
            return {'error': '此项目已有新版本，请刷新后重试', 'code': 'REVISION_CONFLICT', 'currentRevision': expected}, 409
        if path == '/api/project-publish':
            if not has_reference:
                return {'error': '该项目尚未绑定本体版本，无法发布：请先在项目信息中完成绑定。'}, 422
            report = projects.validate_project(project_state, referenced_ontology(project_state))
            if report['errors']:
                return {'error': '项目配置校验未通过：' + '；'.join(report['errors']), 'report': report}, 422
        if path == '/api/project-publish':
            # 原子发布：草稿快照 + CAS head + 发布快照 + 发布记录 + 引用一个事务
            result = projects.publish_draft(project_state, expected)
            return {'revision': result['revision'], 'version': result['version']}, 200
        result = projects.save_draft(project_state, expected_token=expected)
        if result.get('error'):
            return {'error': result['error']}, 500
        return {'revision': result['revision']}, 200


def post_connection_probe(payload, path):
    """/api/connection-test | /api/connection-catalog：真实探测，不持 LOCK。"""
    try:
        project_id = projects.clean_id(payload.get('projectId'))
        config = dbdrivers.normalize_config(payload.get('connection') or {})
        secret = str(payload.get('password') or '')
        if not secret and payload.get('useSaved') and config.get('id'):
            secret = secrets_store.read(project_id, config['id'])
    except ValueError as exc:
        return {'error': str(exc)}, 400
    # 不持 LOCK：网络探测绝不能阻塞其他写操作
    if path == '/api/connection-test':
        return dbdrivers.probe(config, secret), 200
    return dbdrivers.catalog(config, secret), 200


def post_catalog_refresh(payload):
    # 从已保存连接 + 受保护凭据读取表结构并落到服务端目录缓存；大库目录
    # 可达数 MB，绝不随项目草稿请求传输，也不进入草稿/快照文件。
    # 迟到结果保护：以探测前的连接配置指纹为准，探测后配置已变则丢弃结果。
    try:
        project_id = projects.clean_id(payload.get('projectId'))
        connection_id = str(payload.get('connectionId') or '')
        if not connection_id:
            raise ValueError('缺少连接标识')
        state, _ = projects.load(project_id)
        conn = next((c for c in state['connections'].get('connections', [])
                     if c.get('id') == connection_id), None)
        if not conn or not conn.get('engine'):
            raise ValueError('项目中不存在此数据连接，请先在数据连接页保存')
        if conn.get('engine') != 'mysql':
            return {'ok': False, 'category': 'config', 'message': 'Redis 连接没有表结构目录；请选择 MySQL 连接'}, 200
        config = dbdrivers.normalize_config(conn)
        fingerprint = catalog_store.config_fingerprint(config)
        secret = secrets_store.read(project_id, connection_id)
    except (ValueError, projects.ProjectNotFound) as exc:
        code = 404 if isinstance(exc, projects.ProjectNotFound) else 400
        return {'error': str(exc)}, code
    result = dbdrivers.catalog(config, secret)
    if result.get('ok'):
        # 外部探测完成后再核对配置指纹：不匹配 = 探测期间连接被改过 → 丢弃
        state2, _ = projects.load(project_id)
        conn2 = next((c for c in state2['connections'].get('connections', [])
                      if c.get('id') == connection_id), None)
        fingerprint2 = catalog_store.config_fingerprint(dbdrivers.normalize_config(conn2)) if conn2 else ''
        if fingerprint2 == fingerprint:
            catalog_store.store(project_id, connection_id, result, config_fingerprint_value=fingerprint)
    return result, 200


def post_connection_secret(payload):
    try:
        project_id = projects.clean_id(payload.get('projectId'))
        connection_id = str(payload.get('connectionId') or '')
        action = payload.get('action')
    except ValueError as exc:
        return {'error': str(exc)}, 400
    if action not in ('set', 'clear'):
        return {'error': '操作无效'}, 400
    with LOCK:  # 本地快写，短暂持锁
        try:
            if action == 'set':
                secrets_store.save(project_id, connection_id, payload.get('secret') or '')
                return {'saved': True}, 200
            secrets_store.clear(project_id, connection_id)
            return {'cleared': True}, 200
        except ValueError as exc:
            return {'error': str(exc)}, 400


def post_api_credential(payload):
    """/api/api-credential：项目级 API 凭据登记/清除（动作接口映射 P3）。

    与数据库连接密码 vault（secrets.py）隔离的独立命名空间，只做配置存储。
    密钥只写不读回：响应只含 id/name，任何情况下不写入日志、异常信息或响应。
    """
    try:
        project_id = projects.clean_id(payload.get('projectId'))
        action = payload.get('action')
        credential_id = str(payload.get('credentialId') or '')
    except ValueError as exc:
        return {'error': str(exc)}, 400
    if action not in ('set', 'clear'):
        return {'error': '动作无效'}, 400
    projects.load(project_id)  # 项目不存在时抛 ProjectNotFound → 404
    with LOCK:  # 本地快写，短暂持锁
        try:
            if action == 'set':
                saved = api_credentials.save(project_id, payload.get('name'), payload.get('secret'), credential_id)
                return {'saved': True, 'credential': {'id': saved['id'], 'name': saved['name']}}, 200
            if not credential_id:
                raise ValueError('缺少凭据标识')
            api_credentials.clear(project_id, credential_id)
            return {'cleared': True}, 200
        except ValueError as exc:
            return {'error': str(exc)}, 400


def post_property_preview(payload):
    """POST /api/project-property-preview：受限只读预览。

    业务全部在只读执行器（project_property_reader）：短锁核对 revision 后释放，
    联网查询不持锁；本函数仅保持路由分层。
    """
    return project_property_reader.preview(payload)


def post_calc_eval(payload):
    """/api/calc-eval：计算函数纯公式试算。只读、不持 LOCK、不写草稿、不访问业务数据。"""
    from workbench import calc_functions
    expression = payload.get('expression')
    inputs = payload.get('inputs') if isinstance(payload.get('inputs'), list) else []
    if len(inputs) > calc_functions.MAX_INPUTS:
        return {'ok': False, 'error': '输入参数过多'}, 200
    param_types, values = {}, {}
    for item in inputs:
        if not isinstance(item, dict):
            continue
        pid = str(item.get('id', '') or '')
        ptype = item.get('type')
        if not pid or ptype not in calc_functions.TYPES:
            return {'ok': False, 'error': '输入参数声明无效'}, 200
        param_types[pid] = ptype
        values[pid] = item.get('value')
    output_type = payload.get('outputType')
    if output_type not in calc_functions.TYPES:
        return {'ok': False, 'error': '输出数据类型无效'}, 200
    if payload.get('validateOnly'):
        err = calc_functions.validate_expression(expression, param_types, output_type)
        return ({'ok': False, 'error': err} if err else {'ok': True}), 200
    result = calc_functions.evaluate(expression, param_types, values, output_type)
    return result, 200
