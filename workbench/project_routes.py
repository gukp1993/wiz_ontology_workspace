"""项目区路由处理（B3）：/api/projects、project-state/-save/-validate/-publish/
-upgrade-check、project-releases、connection-*、catalog-refresh 的业务逻辑。
安全检查（Origin/白名单/大小限制）在 server.py 的 Handler 统一完成。

注意：connection-test / connection-catalog / catalog-refresh 是真实网络探测，
绝不持有全局写锁（会阻塞其他保存）；connection-secret 本地快写短暂持锁。
project-property-preview（关联聚合只读预览，任务 C）的执行器在
workbench/project_property_reader.py，这里只做路由挂载。
"""
import json
import yaml

from workbench import projects, versions, workspaces, contracts
from workbench import dbdrivers
from workbench import project_property_reader
from workbench import secrets as secrets_store
from workbench import catalogs as catalog_store
from workbench.locking import LOCK
from workbench.paths import DATA_ROOT


def referenced_ontology(project_state):
    """Load the ontology state of the version a project pins; raises if missing."""
    ontology_id = workspaces.clean_id(project_state.get('ontologyId'))
    return versions.read_state(ontology_id, str(project_state.get('ontologyVersion', '')))


# --- GET 路由 -----------------------------------------------------------------------

def get_projects(query, url_query_present=False):
    """url_query_present 对应原实现的 `identifier if url.query else None` 语义。"""
    identifier = query.get('ontology', ['storage'])[0]
    items = projects.listing(identifier if url_query_present else None)
    for item in items:
        try:
            item['latestVersion'] = (versions.latest(item['ontologyId']) or {}).get('version', '')
        except (ValueError, OSError):
            item['latestVersion'] = ''
    return {'items': items}, 200


def get_project_state(query):
    project_id = query.get('project', [''])[0]
    state, saved = projects.load(project_id)
    # 表结构目录是服务端派生数据：加载时注入文件存储内容，随草稿/快照剥离。
    state['bindings']['catalogs'] = {**state['bindings'].get('catalogs', {}), **catalog_store.load_all(state['projectId'])}
    payload = {'state': state, 'revision': projects.revision(state), 'saved': saved,
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


def get_project_config(query):
    """旧辅助接口：storage 本体的 chuangzhi 项目配置（供历史组件读取，保留兼容）。"""
    identifier = workspaces.describe(query.get('ontology', ['storage'])[0])['id']
    project_file = DATA_ROOT / 'ontology/projects/chuangzhi/project.yaml'
    if identifier != 'storage' or not project_file.is_file():
        return {'project': {}, 'connections': [], 'available': False, 'message': '当前本体尚未配置项目与数据连接。'}, 200
    project = yaml.safe_load(project_file.read_text())
    connections = yaml.safe_load((DATA_ROOT / 'ontology/projects/chuangzhi/connections.yaml').read_text())['connections']
    return {'project': {k: project[k] for k in ('project_id', 'name', 'timezone', 'model_version', 'config_version', 'demo_only') if k in project},
            'connections': [{k: c[k] for k in ('id', 'adapter', 'path') if k in c} for c in connections]}, 200


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
        if payload.get('revision') != projects.revision(existing):
            return {'error': '此项目已有新版本，请刷新后重试', 'currentRevision': projects.revision(existing)}, 409
        if path == '/api/project-publish':
            if not has_reference:
                return {'error': '该项目尚未绑定本体版本，无法发布：请先在项目信息中完成绑定。'}, 422
            report = projects.validate_project(project_state, referenced_ontology(project_state))
            if report['errors']:
                return {'error': '项目配置校验未通过：' + '；'.join(report['errors']), 'report': report}, 422
        result = projects.save_draft(project_state)
        if result.get('error'):
            return {'error': result['error']}, 500
        response = {'revision': result['revision']}
        if path == '/api/project-publish':
            response.update(projects.publish(project_state))
        return response, 200


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
    # 从已保存连接 + 受保护凭据读取表结构并落到服务端目录存储；大库目录
    # 可达数 MB，绝不随项目草稿请求传输，也不进入草稿/快照文件。
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
        secret = secrets_store.read(project_id, connection_id)
    except (ValueError, projects.ProjectNotFound) as exc:
        code = 404 if isinstance(exc, projects.ProjectNotFound) else 400
        return {'error': str(exc)}, code
    result = dbdrivers.catalog(config, secret)
    if result.get('ok'):
        catalog_store.store(project_id, connection_id, result)
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
