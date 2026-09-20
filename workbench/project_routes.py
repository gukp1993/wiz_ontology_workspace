"""项目区路由处理（B3 + DB 存储切换）：/api/projects、project-state/-save/-validate/
-publish/-upgrade-check、project-releases、connection-*、catalog-refresh 的业务逻辑。
安全检查（Origin/白名单/大小限制）在 server.py 的 Handler 统一完成。

revision 是不透明 token（读自项目 head）；发布走 publish_draft 单事务（草稿快照 +
CAS + 发布记录 + 固定引用），失败绝不返回成功。connection-test / connection-catalog /
catalog-refresh 是真实网络探测，绝不持有全局写锁；迟到目录结果按配置指纹丢弃。
"""
from workbench import config_packages, projects, versions, workspaces, contracts
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
    # 2026-09-20 v2：单条缓存损坏只跳过该连接（不静默冒充「无目录」，由校验路径
    # 报「目录缓存读取失败」）；存储层不可用按 503 冒泡（不降级为空）。
    meta_loader = getattr(catalog_store, 'load_all_meta', None)
    if meta_loader is not None:
        # 存储层整体读取失败（CatalogCacheUnreadable）冒泡 → Handler 转 503，不冒充「无目录」
        meta = meta_loader(state['projectId'])
        readable = {cid: entry.get('payload') for cid, entry in (meta or {}).items()
                    if isinstance(entry, dict) and entry.get('payload') is not None
                    and not entry.get('unreadable')}
        state['bindings']['catalogs'] = {**state['bindings'].get('catalogs', {}), **readable}
    else:
        state['bindings']['catalogs'] = {**state['bindings'].get('catalogs', {}),
                                         **catalog_store.load_all(state['projectId'])}
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

    响应 items 只有 id/name；密钥只存在于服务端 vault，客户端仅保存凭据 ID。
    2026-09-19 配置迁移：响应新增 `pending`——本项目的待补凭据声明
    （config-package.pending-credentials，见 07 分册 §4.1）；补填后自动消失。
    """
    project_id = projects.clean_id(query.get('project', [''])[0])
    projects.load(project_id)  # 项目不存在时抛 ProjectNotFound → 404
    pending = config_packages.pending_credentials_for_project(project_id)
    return {'items': api_credentials.list_metadata(project_id), 'pending': pending}, 200


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


def _catalog_meta(project_id):
    """目录缓存三态（读取失败按 G1 语义上报，不冒充「无目录」）。

    返回 (meta, degraded_ids)：
    - `meta`：{connectionId: {payload, fingerprint, generation, unreadable}}（可空字典）；
    - `degraded_ids`：损坏条目的连接 id（校验路径出 error 阻断发布，GET 路径跳过注入）；
    存储层整体读取失败抛 `CatalogCacheUnreadable(connection_ids=[])` —— 由 Handler 转 503，
    绝不降级为「零问题」。
    """
    loader = getattr(catalog_store, 'load_all_meta', None)
    if loader is None:  # helper 未落地时保持既有读取路径（不伪造成失败）
        return {}, []
    meta = loader(project_id)
    degraded = sorted(cid for cid, entry in (meta or {}).items()
                      if isinstance(entry, dict) and entry.get('unreadable'))
    return meta, degraded


def _validate_with_degraded(project_state, ontology_state, degraded_catalogs):
    """调用项目校验，并保证「目录缓存损坏的连接」阻断（03 分册 §2.2）。

    校验器（B 角色）落地 `degraded_catalogs` 形参后由其出 error；未落地时在路由层兜底
    追加同样的 error + items——**绝不静默跳过**：跳过会让用户在目录读取失败时把配置发布出去
    （F 独立 QA 复现：损坏 payload 后 validate errors=[]、publish 200 产出 v1）。
    """
    import inspect
    try:
        accepts = 'degraded_catalogs' in inspect.signature(projects.validate_project).parameters
    except (TypeError, ValueError):
        accepts = False
    if accepts:
        return projects.validate_project(project_state, ontology_state,
                                        degraded_catalogs=degraded_catalogs)
    report = projects.validate_project(project_state, ontology_state)
    if degraded_catalogs:
        names = {str(c.get('id')): str(c.get('name') or c.get('id') or '')
                 for c in (project_state.get('connections') or {}).get('connections') or []
                 if isinstance(c, dict)}
        for cid in degraded_catalogs:
            label = names.get(str(cid)) or str(cid)
            text = (f'数据连接 {label}：目录缓存读取失败（缓存内容损坏），'
                    f'无法核对表与字段；请重新刷新该连接的目录后再发布。')
            if text not in report['errors']:
                report['errors'].append(text)
            report['items'].append({'kind': 'connection', 'id': str(cid),
                                    'name': f'数据连接 · {label}',
                                    'status': 'invalid', 'issues': [text]})
    return report


def post_project_write(payload, path):
    """/api/project-save|project-validate|project-publish|project-upgrade-check 公共前置。

    2026-09-20 v2 冻结（03 分册 §2.1/§2.2/§2.3/§2.4）：
    - 校验响应附依赖基线 `baseline`；目录/编排依赖读取失败不降级为「零问题」；
    - 保存边界：删仍被引用的连接 → 422 REFERENCE_IN_USE；变更引用版本须存在且已发布；
    - 发布：校验后提交前复核依赖（快照不一致 → 409 + reason=DEPENDENCY_CHANGED，零写入）；
      `requestId` 幂等（同 key 同内容回放、异内容 409，回执与发布同一事务）。
    """
    from workbench import mapping_descriptions
    project_state = projects._normalize(payload['state'])
    projects.clean_id(project_state.get('projectId'))
    # 目录以服务端缓存为准：避免不同客户端的过期副本影响校验与修订哈希。
    # 损坏条目只跳过该连接（校验路径另行出 error）；存储层失败冒泡 → 503（不降级为空）。
    # 这一次读取同时给出 payload/fingerprint/generation（同一条 SELECT），发布路径把它
    # 作为依赖基线一并使用（R03）：校验所用 payload 与提交复核所用代际来自同一快照。
    meta, degraded_catalogs = _catalog_meta(project_state['projectId'])
    project_state['bindings']['catalogs'] = {
        cid: entry['payload'] for cid, entry in (meta or {}).items()
        if isinstance(entry, dict) and entry.get('payload') is not None and not entry.get('unreadable')}
    has_reference = bool(project_state.get('ontologyId') and project_state.get('ontologyVersion'))
    if path == '/api/project-validate':
        if not has_reference:
            return {'errors': ['该项目尚未绑定本体版本：请在项目信息中选择引用的本体与版本。'], 'warnings': [],
                    'items': [{'kind': 'reference', 'id': 'reference', 'name': '本体引用', 'status': 'unconfigured', 'issues': ['尚未绑定本体版本']}]}, 200
        ontology_state = referenced_ontology(project_state)
        # 校验请求省略说明块时按当前已存草稿合并（仅用于校验，不写库）
        saved, _ = projects.load(project_state['projectId'])
        mapping_descriptions.merge_omitted(project_state, saved)
        deps = _dependency_snapshot(project_state, catalog_meta=meta)
        report = _validate_with_degraded(project_state, ontology_state, degraded_catalogs)
        report['baseline'] = _baseline_payload(project_state, deps, meta)
        return report, 200
    if path == '/api/project-upgrade-check':
        if not has_reference:
            return {'error': '该项目尚未绑定本体版本，无需升级；请先在项目信息中完成绑定。'}, 422
        # 比较基线（§2.4）：携带 revision 时必须是当前草稿令牌；旧客户端缺字段不阻断读取
        revision = payload.get('revision')
        if revision not in (None, ''):
            current = projects.current_token(project_state['projectId'])
            if revision != current:
                return {'error': '项目草稿已有新版本，请刷新后重新比较',
                        'code': 'REVISION_CONFLICT', 'currentRevision': current}, 409
        ontology_state = referenced_ontology(project_state)
        target = versions.read_state(workspaces.clean_id(project_state.get('ontologyId')), str(payload.get('targetVersion', '')))
        return projects.upgrade_check(project_state, ontology_state, target), 200
    with LOCK:
        existing, _ = projects.load(project_state['projectId'])
        expected = projects.current_token(project_state['projectId'])
        # 发布幂等回放优先于 CAS（03 分册 §2.3）：首答丢失后客户端可能带旧 revision 重试，
        # 而草稿已被那次成功发布推进——同 key 同内容应回放同一结果，不报冲突。
        request_id = str(payload.get('requestId') or '')
        if path == '/api/project-publish' and request_id:
            from workbench import auth as _auth
            _owner = _auth.require_user_id()
            idem = {'owner_key': f'{_owner}/{project_state["projectId"]}',
                    'request_key': request_id[:128],
                    'request_hash': projects.revision(project_state)}
            try:
                prior = projects.idempotent_replay(project_state, idem['owner_key'],
                                                   idem['request_key'], idem['request_hash'])
            except projects.IdempotencyConflict as exc:
                return {'error': str(exc), 'code': 'REVISION_CONFLICT',
                        'currentRevision': expected}, 409
            if prior is not None:
                return {'revision': prior.get('revision', ''), 'version': prior.get('version', ''),
                        'idempotentReplay': True}, 200
        else:
            idem = None
        if payload.get('revision') != expected:
            return {'error': '此项目已有新版本，请刷新后重试', 'code': 'REVISION_CONFLICT', 'currentRevision': expected}, 409
        # 保存边界（§2.1）：本次提交删除了仍被引用的连接 → 拒绝（连接与其引用同批移除放行）
        def _connection_ids(state):
            return {str(c.get('id')) for c in (state.get('connections') or {}).get('connections') or []
                    if isinstance(c, dict) and c.get('id')}
        removed_connections = _connection_ids(existing) - _connection_ids(project_state)
        boundary = projects.save_boundary_issues(existing, project_state)
        if boundary:
            names = '、'.join(sorted({r.get('name') or r.get('id') or '' for r in boundary}))
            return {'error': '本次保存删除了仍被引用的数据连接：' + names + '。请先移除对应来源或一并移除其引用。',
                    'code': 'REFERENCE_IN_USE', 'references': boundary}, 422
        # 引用版本变更（§2.1）：目标必须是已发布版本（未发布/不存在 → 404）
        old_ref = (existing.get('ontologyId') or '', str(existing.get('ontologyVersion') or ''))
        new_ref = (project_state.get('ontologyId') or '', str(project_state.get('ontologyVersion') or ''))
        if new_ref != old_ref and new_ref[0] and new_ref[1]:
            referenced_ontology(project_state)
        # 说明块兼容（接口文档 01 §3.4）：旧客户端省略整块 → 在同一 CAS/写锁边界内沿用
        # 已存说明；携带完整块按完整块提交（显式清空按删键表达）。规范化顺带清除纯空白项。
        mapping_descriptions.merge_omitted(project_state, existing)
        try:
            block, _changed = mapping_descriptions.normalize_block(project_state['bindings'].get('mappingDescriptions'))
        except ValueError as exc:
            return {'error': str(exc)}, 400
        if block is None:
            project_state['bindings'].pop('mappingDescriptions', None)
        else:
            project_state['bindings']['mappingDescriptions'] = block
        if path == '/api/project-publish':
            if not has_reference:
                return {'error': '该项目尚未绑定本体版本，无法发布：请先在项目信息中完成绑定。'}, 422
            # 依赖快照取在校验之前：提交前复核此快照（校验后任何依赖变化 → 拒绝）。
            # 目录令牌直接取自本次校验所用 payload 的同一读取（R03），不再第二次读取目录，
            # 「payload 已读、令牌未读」的更新窗口因此不存在；编排令牌仍在此读取（变化会被
            # 事务内复核捕获）。
            deps = _dependency_snapshot(project_state, catalog_meta=meta)
            report = _validate_with_degraded(project_state, referenced_ontology(project_state),
                                             degraded_catalogs)
            if report['errors']:
                return {'error': '项目配置校验未通过：' + '；'.join(report['errors']), 'report': report}, 422
            # 原子发布：草稿快照 + CAS head + 发布快照 + 发布记录 + 引用（+幂等回执）一个事务
            # （前置回放已在上方处理；此处 idem 供事务内竞态兜底）
            try:
                result = projects.publish_draft(project_state, expected, idempotency=idem,
                                                expected_deps=deps,
                                                deps_probe=lambda conn: projects.dependency_probe(conn, project_state))
            except projects.DependencyChanged as exc:
                return {'error': str(exc), 'code': 'REVISION_CONFLICT', 'reason': 'DEPENDENCY_CHANGED',
                        'currentRevision': projects.current_token(project_state['projectId'])}, 409
            except projects.IdempotencyConflict as exc:
                return {'error': str(exc), 'code': 'REVISION_CONFLICT',
                        'currentRevision': projects.current_token(project_state['projectId'])}, 409
            response = {'revision': result['revision'], 'version': result['version']}
            if result.get('replay'):
                response['idempotentReplay'] = True
            return response, 200
        result = projects.save_draft(project_state, expected_token=expected)
        if result.get('error'):
            return {'error': result['error']}, 500
        # 本次显式删除的连接：保存成功后清理派生数据（目录缓存 + 凭据）。
        # 仅当「已保存草稿中确实不再引用」才清理（helper 内部在同一写事务内复核）；
        # 清理失败不影响保存与下次刷新（派生数据可重建，凭据可重填）。
        if removed_connections:
            for cid in sorted(removed_connections):
                try:
                    catalog_store.clear_if_unreferenced(project_state['projectId'], cid)
                except Exception:
                    pass
                try:
                    secrets_store.clear_if_unreferenced(project_state['projectId'], cid)
                except Exception:
                    pass
        return {'revision': result['revision']}, 200


def _dependency_snapshot(project_state, catalog_meta=None):
    """只读依赖快照（校验与发布重验使用同一函数，口径一致）。

    R03（2026-09-20 验收修复）：目录依赖必须与校验所用 payload 属于**同一读取基线**。
    `_catalog_meta` 的一次读取同时返回 payload/fingerprint/generation（单条 SELECT），
    发布路径把该 `catalog_meta` 传入本函数后，目录令牌直接由它派生，不再另行读一次——
    否则「payload 读取后、依赖令牌读取前」的目录更新会让校验用旧 payload、提交复核用新
    代际，从而把未经同基线校验的内容发布出去。编排令牌仍在本函数内读取（读点在校验之前，
    编排状态由校验器自身读取；若窗口内变化，同事务复核会发现令牌不一致 → 409）。
    未传 `catalog_meta` 时保持既有整表读取（供不依赖目录 payload 的调用方使用）。
    """
    from workbench.storage.engine import read_connection
    with read_connection() as conn:
        deps = projects.dependency_probe(conn, project_state)
    if catalog_meta is not None:
        deps['catalogs'] = _catalog_deps(catalog_meta)
    return deps


def _catalog_deps(meta):
    """目录依赖令牌 `fingerprint:generation`（与 projects.dependency_probe 同口径）。"""
    return {str(cid): f'{str(entry.get("fingerprint") or "")}:{int(entry.get("generation") or 0)}'
            for cid, entry in (meta or {}).items() if isinstance(entry, dict)}


def _baseline_payload(project_state, deps, meta):
    """校验响应的 baseline（03 分册 §2.2）：本次检查实际读取的依赖基线。"""
    catalogs = []
    for connection_id, entry in sorted((meta or {}).items()):
        fingerprint = str(entry.get('fingerprint') or '')
        catalogs.append({'connectionId': connection_id, 'fingerprint': fingerprint,
                         'generation': int(entry.get('generation') or 0),
                         'unreadable': bool(entry.get('unreadable'))})
    return {'projectId': project_state.get('projectId', ''),
            'revision': projects.current_token(project_state.get('projectId')) or '',
            'ontologyId': project_state.get('ontologyId', ''),
            'ontologyVersion': str(project_state.get('ontologyVersion') or ''),
            'flows': [{'id': fid, 'revision': token} for fid, token in sorted((deps or {}).get('flows', {}).items())],
            'catalogs': catalogs}


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
    # 迟到结果保护（2026-09-20 v2 冻结，03 分册 §3.3）：探测前记录「技术配置指纹
    # （排除显示名）+ 凭据安全代际」，探测后在同一短事务内以它们为条件复核并写入；
    # 期间连接被改/被删/凭据被换 → 结果丢弃（丢弃≠报成功）。
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
        secret_revision = _secret_generation(project_id, connection_id)
    except (ValueError, projects.ProjectNotFound) as exc:
        code = 404 if isinstance(exc, projects.ProjectNotFound) else 400
        return {'error': str(exc)}, code
    result = dbdrivers.catalog(config, secret)
    if result.get('ok'):
        # 条件写（C 角色 helper）：指纹 + 凭据代际在写事务内复核；不满足 → 丢弃结果。
        cond_store = getattr(catalog_store, 'store_if_current', None)
        if cond_store is not None:
            try:
                stored = cond_store(project_id, connection_id, result,
                                    expected_fingerprint=fingerprint,
                                    expected_secret_revision=secret_revision)
            except Exception:
                stored = False
            if not stored:
                result['stale'] = True
                result['message'] = (str(result.get('message') or '').strip() +
                                     '（探测期间连接配置或凭据已变化，本次结果未写入缓存；请重新刷新）').strip()
        else:  # helper 未落地时的保守回退：复核指纹（不做凭据代际，缺一条件不影响旧行为）
            state2, _ = projects.load(project_id)
            conn2 = next((c for c in state2['connections'].get('connections', [])
                          if c.get('id') == connection_id), None)
            fingerprint2 = catalog_store.config_fingerprint(dbdrivers.normalize_config(conn2)) if conn2 else ''
            if fingerprint2 == fingerprint:
                catalog_store.store(project_id, connection_id, result, config_fingerprint_value=fingerprint)
    return result, 200


def _secret_generation(project_id, connection_id):
    """凭据安全代际（只读整数；helper 未落地时返回 0，不影响旧行为）。"""
    reader = getattr(secrets_store, 'revision', None)
    if reader is None:
        return 0
    try:
        return int(reader(project_id, connection_id) or 0)
    except Exception:
        return 0


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
                # T07：补填命中待补声明 → 从迁移待补清单清除（按项目+声明 ID）
                config_packages.clear_pending_credential(project_id, credential_id)
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
