"""函数编排 HTTP 节点的 URL/请求体模板与纯校验。渲染为纯函数，无 IO；
真实出站调用在 flow_executor（urllib）。边界：
* URL 模板：{技术名} 占位 → URL 编码替换（quote）；请求体模板：{技术名} 占位 →
  字符串原样插入（引号由模板自带）、数值/布尔/null 按字面量、对象/列表按紧凑 JSON。
* 仅支持 http(s)；method ∈ GET/POST/PUT/DELETE；responsePath 为点路径。
"""
import json
import re
from urllib.parse import quote


class FlowHttpError(ValueError):
    pass


METHODS = ('GET', 'POST', 'PUT', 'DELETE')
_MAX_URL = 2000
_MAX_BODY = 200000
_PLACEHOLDER_RE = re.compile(r'\{([A-Za-z_][A-Za-z0-9_]*)\}')
_PATH_RE = re.compile(r'^[A-Za-z0-9_]*(\.[A-Za-z0-9_]+)*$')


def scan_placeholders(template):
    """模板中 {技术名} 占位，去重保序。"""
    seen, out = set(), []
    for match in _PLACEHOLDER_RE.finditer(str(template or '')):
        if match.group(1) not in seen:
            seen.add(match.group(1))
            out.append(match.group(1))
    return out


def render_url(url, params):
    url = str(url or '')
    if len(url) > _MAX_URL:
        raise FlowHttpError('URL 过长')
    scheme = url.split(':', 1)[0].lower()
    if scheme not in ('http', 'https'):
        raise FlowHttpError('URL 仅支持 http(s)')
    params = params or {}

    def repl(match):
        name = match.group(1)
        if name not in params:
            raise FlowHttpError(f'URL 占位引用了未提供的参数 {name}')
        value = params[name]
        if value is None:
            raise FlowHttpError(f'URL 占位参数 {name} 为 null')
        if isinstance(value, (dict, list)):
            raise FlowHttpError(f'URL 占位参数 {name} 不能是对象/列表')
        return quote(str(value), safe='')

    return _PLACEHOLDER_RE.sub(repl, url)


def render_body(body, params):
    """字符串占位原样插入（模板自带引号）；标量按字面量；对象/列表按紧凑 JSON。"""
    body = str(body or '')
    if len(body) > _MAX_BODY:
        raise FlowHttpError('请求体模板过大')
    params = params or {}

    def repl(match):
        name = match.group(1)
        if name not in params:
            raise FlowHttpError(f'请求体占位引用了未提供的参数 {name}')
        value = params[name]
        if isinstance(value, str):
            return value
        return json.dumps(value, ensure_ascii=False, default=str)

    return _PLACEHOLDER_RE.sub(repl, body)


def pick_path(payload, response_path):
    """按点路径提取响应子树；responsePath 空 = 整个响应。路径不存在返回 None。"""
    current = payload
    for part in str(response_path or '').split('.'):
        if not part:
            continue
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def implementation_issues(impl, declared_names, credential_ids=None):
    """HTTP 节点静态校验，返回 (message, code, field) 三元组列表（不含执行）。"""
    errors = []
    url = str(impl.get('url') or '').strip()
    if not url:
        errors.append(('请填写请求 URL', 'HTTP_URL_MISSING', 'url'))
    elif not url.lower().startswith(('http://', 'https://')):
        errors.append(('URL 仅支持 http(s)', 'HTTP_URL_INVALID', 'url'))
    method = str(impl.get('method') or 'GET').upper()
    if method not in METHODS:
        errors.append((f'请求方法无效（支持 {" / ".join(METHODS)}）', 'HTTP_METHOD_INVALID', 'method'))
    declared = set(declared_names or [])
    for name in scan_placeholders(url):
        if name not in declared:
            errors.append((f'URL 占位 {{{name}}} 不是已声明的输入参数', 'HTTP_PLACEHOLDER_UNDECLARED', 'url'))
    body_mode = impl.get('bodyMode') or 'none'
    if body_mode not in ('none', 'json'):
        errors.append(('请求体类型无效（none/json）', 'HTTP_BODYMODE_INVALID', 'bodyMode'))
    body = impl.get('body')
    if body_mode == 'none' and body:
        errors.append(('请求体类型为 none 时不应填写请求体', 'HTTP_BODY_UNEXPECTED', 'body'))
    if body_mode == 'json' and not str(body or '').strip() and method in ('POST', 'PUT'):
        errors.append(('POST/PUT 需要填写 JSON 请求体模板', 'HTTP_BODY_MISSING', 'body'))
    if method == 'GET' and str(body or '').strip():
        errors.append(('GET 请求不应携带请求体（请改用 POST 或清空）', 'HTTP_BODY_ON_GET', 'body'))
    if body_mode == 'json':
        for name in scan_placeholders(body):
            if name not in declared:
                errors.append((f'请求体占位 {{{name}}} 不是已声明的输入参数', 'HTTP_PLACEHOLDER_UNDECLARED', 'body'))
    headers = impl.get('headers')
    if headers is not None:
        if not isinstance(headers, dict):
            errors.append(('请求头必须是「名: 值」对象', 'HTTP_HEADERS_INVALID', 'headers'))
        else:
            for key, value in headers.items():
                if not str(key).strip() or '\n' in str(key) + str(value):
                    errors.append((f'请求头 {key or "（空）"} 格式无效', 'HTTP_HEADER_INVALID', 'headers'))
    path = str(impl.get('responsePath') or '').strip()
    if path and not _PATH_RE.fullmatch(path):
        errors.append(('响应提取路径无效（应为 a.b.c 形式的点路径）', 'HTTP_PATH_INVALID', 'responsePath'))
    credential_id = str(impl.get('credentialId') or '')
    if credential_id and credential_ids is not None and credential_id not in credential_ids:
        errors.append(('认证凭据不存在或已被删除，请重新选择', 'CREDENTIAL_NOT_FOUND', 'credentialId'))
    if credential_id and isinstance(headers, dict) and any(str(k).lower() == 'authorization' for k in headers):
        errors.append(('已选择认证凭据时不要自填 Authorization 头（两者冲突，凭据会被忽略）；'
                       '请二选一：删除自带头或清除凭据', 'HTTP_AUTH_CONFLICT', 'credentialId'))
    return errors
