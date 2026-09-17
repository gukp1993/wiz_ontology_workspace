"""动作 HTTP 接口配置（20260917 需求）：纯读取规范化与配置校验。

**只做配置解析与校验，不发起任何 HTTP 请求、不执行设备控制、不查询实时值。**

协议（`bindings.actionBindings[].implementation`，`kind='api'`）：

- 无 `schemaVersion` 的历史 api：旧格式，仅 `path` 必填，本模块不参与（旧规则在 project_validation）。
- `schemaVersion=2`：本期格式，字段如下。

```json
{
  "kind": "api", "schemaVersion": 2,
  "method": "POST",
  "path": "https://ems.example.com/api/devices/{deviceId}/stop",
  "bodyFormat": "json",
  "description": "停止充放电请求",
  "parameters": [
    {"id": "参数行稳定ID", "name": "deviceId", "in": "body", "value": {"from": "instanceId"}},
    {"id": "参数行稳定ID", "name": "power", "in": "body",
     "value": {"from": "constant", "type": "number", "value": 0}}
  ],
  "auth": {"type": "none"}
}
```

取值来源 `value`：

- `{"from": "actionInput", "inputId": "输入稳定ID"}`；历史输入无稳定 ID 时显式编码
  `"inputName": "参数名"`（只兼容已有非空唯一参数名，不凭空生成跨版本 ID）。
- `{"from": "instanceId"}`：当前对象实例主键（真实类型未知，界面显示"按实际主键类型"）。
- `{"from": "property", "propertyId": "属性稳定ID"}`：引用版本中该对象的属性。
- `{"from": "constant", "type": "string"|"number"|"boolean", "value": ...}`：固定值，
  `0`、`false` 有效，文本空串是显式固定值。

认证 `auth`：`{"type":"none"}` / `{"type":"bearer","credentialId":"..."}` /
`{"type":"apiKey","credentialId":"...","in":"header"|"query","name":"X-API-Key"}`。
凭据只存引用 ID，密钥不进项目配置、快照、日志或导出。

未知字段一律原样保留（零丢失）；本模块只**读取**，不补写默认值到存储。
"""

from urllib.parse import urlsplit

METHODS = ('GET', 'POST', 'PUT', 'PATCH', 'DELETE')
PARAM_IN = ('path', 'query', 'header', 'body')
BODY_FORMATS = ('json', 'form')
SOURCES = ('actionInput', 'instanceId', 'property', 'constant')
CONSTANT_TYPES = ('string', 'number', 'boolean')
AUTH_TYPES = ('none', 'bearer', 'apiKey')

IN_LABELS = {'path': '路径', 'query': 'Query', 'header': '请求头', 'body': '请求体'}
SOURCE_LABELS = {'actionInput': '动作输入', 'instanceId': '当前对象实例主键',
                 'property': '当前对象属性', 'constant': '固定值'}


def is_v2(implementation) -> bool:
    """本期格式判定：kind=api 且 schemaVersion==2。"""
    if not isinstance(implementation, dict):
        return False
    if str(implementation.get('kind') or '') != 'api':
        return False
    version = implementation.get('schemaVersion')
    return version == 2 or str(version or '') == '2'


def api_view(implementation) -> dict:
    """宽容读取：缺省值只在内存视图里补，不写回存储（旧数据不静默升级）。"""
    impl = implementation if isinstance(implementation, dict) else {}
    method = str(impl.get('method') or 'POST').upper()
    body_format = str(impl.get('bodyFormat') or 'json')
    auth = impl.get('auth') if isinstance(impl.get('auth'), dict) else {'type': 'none'}
    rows = impl.get('parameters')
    return {
        'method': method,
        'path': str(impl.get('path') or ''),
        'bodyFormat': body_format if body_format in BODY_FORMATS else 'json',
        'description': str(impl.get('description') or ''),
        'parameters': [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else [],
        'auth': {
            'type': str(auth.get('type') or 'none'),
            'credentialId': str(auth.get('credentialId') or ''),
            'in': str(auth.get('in') or 'header'),
            'name': str(auth.get('name') or ''),
        },
    }


def path_params(impl) -> list:
    return [r for r in api_view(impl)['parameters'] if str(r.get('in') or '') == 'path']


def placeholders(path: str) -> list:
    """路径里的 {占位符} 名称（按出现顺序去重）。"""
    out, text = [], str(path or '')
    start = 0
    while True:
        left = text.find('{', start)
        if left < 0:
            return out
        right = text.find('}', left + 1)
        if right < 0:
            return out
        name = text[left + 1:right]
        if name and name not in out:
            out.append(name)
        start = right + 1


def effective_node(graph, node):
    """共享属性引用：返回生效定义（与前端 effectiveProperty 镜像）。"""
    ref = node.get('mg:sharedProperty') if isinstance(node, dict) else None
    if isinstance(ref, dict) and ref.get('@id'):
        for other in graph or []:
            if other.get('@id') == ref['@id'] and other.get('@type') == 'mg:SharedProperty':
                return other
    return node if isinstance(node, dict) else {}


def is_scalar(node) -> bool:
    """普通标量属性；时间序列不能作为本期标量参数。"""
    return str((node or {}).get('mg:valueShape') or '') != 'timeSeries'


def property_index(graph, type_id: str) -> dict:
    """引用版本中该对象类型的属性稳定 ID → 属性信息（configured 由调用方按项目「属性取值」补齐）。"""
    out = {}
    for node in graph or []:
        if not isinstance(node, dict) or node.get('@type') != 'owl:DatatypeProperty':
            continue
        if (node.get('rdfs:domain') or {}).get('@id') != type_id:
            continue
        merged = effective_node(graph, node)
        api = str(node.get('mg:apiName') or str(node.get('@id') or '').removeprefix('mg:'))
        out[str(node.get('@id'))] = {'name': str(merged.get('rdfs:label') or api), 'api': api,
                                     'scalar': is_scalar(merged), 'configured': False}
    return out


def configured_source(entry) -> bool:
    """属性在项目里是否已配置可用来源（与前端 hasConfiguredSource 镜像）。"""
    if isinstance(entry, str):
        return bool(entry.strip())
    if not isinstance(entry, dict):
        return False
    return str(entry.get('kind') or '') in ('field', 'redis', 'computed', 'database')


def identity_ready(row) -> bool:
    """当前对象是否已有可用实例识别（主键）。

    与项目绑定的身份语义一致（project_mapping.identity_kind）：
    - 无 identity 块 = 旧数据库身份，必须有 primary_key；
    - `identity.kind == 'registered'` = 项目登记实例，视为就绪；
    - identity 块存在但 kind 非 registered = 未知类型（objectBinding 校验已单独报错），
      这里判不就绪，不按默认类型解释。
    """
    if not isinstance(row, dict):
        return False
    identity = row.get('identity')
    if isinstance(identity, dict):
        # identity 块存在时只认 registered；其它 kind（含 'database'、空值）属未知类型，
        # 由 objectBinding 校验单独报错，这里不按默认类型解释为已就绪。
        return str(identity.get('kind') or '') == 'registered'
    return bool(str(row.get('primary_key') or '').strip())


def _param_label(index: int, row: dict) -> str:
    name = str(row.get('name') or '').strip()
    position = IN_LABELS.get(str(row.get('in') or ''), str(row.get('in') or '未选位置'))
    return f'参数 {index}（{name or "未填写参数名"} · {position}）'


def _url_issues(path: str) -> list:
    issues = []
    text = str(path or '').strip()
    if not text:
        return ['请填写接口地址。']
    if not text.lower().startswith(('http://', 'https://')):
        issues.append('接口地址必须是 http:// 或 https:// 开头的完整地址（一期没有项目 API 主机配置）。')
        return issues
    parts = urlsplit(text)
    if '@' in (parts.netloc or ''):
        issues.append('接口地址不能包含账号密码（userinfo），认证请通过凭据引用维护。')
    if '#' in text:
        issues.append('接口地址中的 #fragment 不参与 HTTP 请求，请移除。')
    if not parts.netloc:
        issues.append('接口地址缺少主机名。')
    return issues


def _constant_issues(label: str, value: dict) -> list:
    kind = str(value.get('type') or '')
    if kind not in CONSTANT_TYPES:
        return [f'{label}：固定值类型必须是文本、数值或是否。']
    raw = value.get('value')
    if kind == 'number':
        if isinstance(raw, bool) or raw is None or str(raw).strip() == '':
            return [f'{label}：请输入有效数值（0 也是有效固定值）。']
        try:
            float(raw)
        except (TypeError, ValueError):
            return [f'{label}：请输入有效数值（0 也是有效固定值）。']
    if kind == 'boolean':
        if not isinstance(raw, bool) and str(raw) not in ('true', 'false'):
            return [f'{label}：固定布尔值必须是「是」或「否」。']
    if kind == 'string' and raw is None:
        return [f'{label}：固定文本值无效（空串请显式保存为空文本）。']
    return []


def _source_issues(label: str, value: dict, *, position: str, action_inputs, properties,
                   identity_ready: bool) -> list:
    origin = str(value.get('from') or '')
    if origin not in SOURCES:
        return [f'{label}：请选择取值来源。']
    if origin == 'instanceId':
        if not identity_ready:
            return [f'{label}：当前对象尚未完成实例识别（主键）配置，请先在「实例识别」中配置后再选此来源。']
        return []
    if origin == 'actionInput':
        if not action_inputs:
            return [f'{label}：此动作在项目引用的本体版本中没有输入参数，请改用实例主键、对象属性或固定值。']
        wanted_id = str(value.get('inputId') or '')
        wanted_name = str(value.get('inputName') or '')
        for item in action_inputs:
            if wanted_id and str(item.get('id') or '') == wanted_id:
                return []
            if not wanted_id and wanted_name and str(item.get('name') or '') == wanted_name:
                return []
        return [f'{label}：选择的动作输入不在引用版本中，请重新选择。']
    if origin == 'property':
        property_id = str(value.get('propertyId') or '')
        if not property_id:
            return [f'{label}：请选择对象属性。']
        info = properties.get(property_id) if isinstance(properties, dict) else None
        if not info:
            return [f'{label}：引用的对象属性不在项目引用版本中，请重新选择。']
        if not info.get('scalar', True):
            return [f'{label}：时间序列属性不能作为本期的标量参数（如需要请改用其他属性）。']
        if not info.get('configured'):
            return [f'{label}：该属性尚未在「属性取值」中配置来源，无法作为参数取值。']
        return []
    # constant
    if position in ('path', 'query', 'header'):
        return _constant_issues(label, value)
    return _constant_issues(label, value)


def api_issues(implementation, *, action_inputs=None, properties=None, identity_ready: bool = False,
               credential_ids=None, query_names_in_path=None) -> tuple:
    """校验本期（schemaVersion=2）api 配置。

    返回 `(errors, warnings)`：错误阻断发布并定位到动作与参数位置；
    警告只提示（例如地址里已有同名 Query 参数）。

    - `properties`：属性稳定 ID → `{'name':..,'configured':bool,'scalar':bool}`（来自项目引用版本＋项目属性取值）。
    - `action_inputs`：该动作在引用版本中真实存在的输入 `[{'id','name',...}]`，无输入时传空列表。
    - `identity_ready`：当前对象是否已有可用实例识别（主键）。
    - `credential_ids`：当前项目 API 凭据 ID 集合。
    """
    view = api_view(implementation)
    errors: list = []
    warnings: list = []
    method = view['method']
    path = view['path']
    rows = view['parameters']

    if method not in METHODS:
        errors.append(f'请求方式无效：{method or "未填写"}；可选 {"、".join(METHODS)}。')
    errors.extend(_url_issues(path))

    # 路径占位符与 path 参数一一对应
    holders = placeholders(path)
    declared = [str(r.get('name') or '').strip() for r in rows if str(r.get('in') or '') == 'path']
    for name in holders:
        if name not in declared:
            errors.append(f'路径占位符 {{{name}}} 尚未配置对应的路径参数。')
    for name in declared:
        if name and name not in holders:
            errors.append(f'参数（{name} · 路径）：地址中不存在对应的 {{{name}}} 占位符。')

    # 地址里已有的 Query 名与参数表重名 → 提示统一维护
    known_query = set(query_names_in_path or ())
    if not known_query and path.lower().startswith(('http://', 'https://')):
        raw_query = urlsplit(path).query
        known_query = {pair.split('=', 1)[0] for pair in raw_query.split('&') if pair}

    seen: dict = {}
    has_body = False
    header_names = set()
    for index, row in enumerate(rows, 1):
        label = _param_label(index, row)
        name = str(row.get('name') or '').strip()
        position = str(row.get('in') or '')
        if not name:
            errors.append(f'{label}：请填写参数名。')
        if position not in PARAM_IN:
            errors.append(f'{label}：参数位置无效，请选择路径、Query、请求头或请求体。')
            continue
        if position == 'body':
            has_body = True
        key = position + ':' + (name.lower() if position == 'header' else name)
        if name and key in seen:
            errors.append(f'{label}：同一位置的参数名重复（与{seen[key]}冲突）。')
        elif name:
            seen[key] = label
        if position == 'query' and name in known_query:
            warnings.append(f'{label}：地址中已有同名 Query 参数，请在地址或参数表中统一维护一处。')
        if position == 'header' and name:
            header_names.add(name.lower())
        value = row.get('value') if isinstance(row.get('value'), dict) else {}
        errors.extend(_source_issues(label, value, position=position, action_inputs=action_inputs or [],
                                     properties=properties or {}, identity_ready=identity_ready))

    if method == 'GET' and has_body:
        errors.append('GET 请求不允许请求体参数，请把参数改到 Query 或路径。')
    if 'content-type' in header_names:
        errors.append('请求头参数重复声明 Content-Type；请求体格式会自动生成，请移除该请求头参数。')

    # 认证
    auth = view['auth']
    auth_type = auth['type']
    if auth_type not in AUTH_TYPES:
        errors.append(f'认证方式无效：{auth_type or "未填写"}。')
    if auth_type != 'none':
        credential = auth['credentialId']
        if not credential:
            errors.append('请选择当前项目的 API 凭据引用。')
        elif credential_ids is not None and credential not in set(credential_ids):
            errors.append('选择的 API 凭据不在当前项目中（可能已被清理），请重新选择或登记。')
    if auth_type == 'bearer' and 'authorization' in header_names:
        errors.append('Authorization 已由认证配置提供，请移除参数表中重复的请求头参数。')
    if auth_type == 'apiKey':
        if not auth['name'].strip():
            errors.append('请填写 API Key 的参数名称（例如 X-API-Key）。')
        if auth['in'] not in ('header', 'query'):
            errors.append('API Key 参数位置只能是请求头或 Query。')
        if auth['name']:
            key = auth['in'] + ':' + (auth['name'].lower() if auth['in'] == 'header' else auth['name'])
            if key in seen:
                errors.append(f'API Key 参数与参数表中的 {seen[key]} 位置和名称重复，请统一维护一处。')
    return errors, warnings
