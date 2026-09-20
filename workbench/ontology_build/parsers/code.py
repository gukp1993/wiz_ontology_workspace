"""代码材料解析：Java/Spring/MyBatis/JPA、JS/TS/Vue、Python。

能力边界（支持 / 降级 / 不执行）
--------------------------------
* 支持（括号配平/缩进归属定位，quality=high）：
  - Java：`class|interface|enum|record`（含内部类）、字段声明与 JPA 注解行
    （`@Entity/@Table/@Column/@Id/@GeneratedValue/@ManyToOne/@OneToMany/@OneToOne/
    @ManyToMany/@JoinColumn`）、方法签名、Spring 映射注解与请求路径、注解行本身。
  - MyBatis 映射 XML：`<select|insert|update|delete id=…>`、`resultMap` 字段映射、
    `<sql>` 片段、`#{}`/`${}` 参数（`${}` 标注为拼接风险）、动态标签。
  - JS/TS/Vue：`class`、`ref()/reactive()/computed()`、模块对象字段、选项式 `data()`/`props`
    /`emits`、`<script setup>` 的 `defineProps/defineEmits`、TS `interface|type` 成员、
    HTTP 调用 URL（`fetch(`/`axios.get(`/`request(` 的第一个字符串参数）、`.vue` 三段切分与
    模板绑定。
  - Python：`class`/`def`、Flask/FastAPI 路由装饰器路径、SQLAlchemy
    `Column(`/`__tablename__`/`relationship(`、pydantic/dataclass 注解字段。
* 降级（quality=medium/low，且进入 notes / failedSegments / warnings）：
  - 未适配后缀/语言（Kotlin/Scala/Go/Rust/C#/PHP/Ruby/Shell/JSON/YAML/Properties 等）→
    按文本线索降级，明确“覆盖不足”。
  - 注解与声明不配对、多行方法签名、括号未配平、XML 结构校验失败、动态调用（反射、
    字符串拼装 SQL、宏、代码生成）→ 记录证据边界，不编造结构。
* 不执行：不 eval/exec、不 import 材料、不运行构建、不连数据库、不访问网络或外链、
  不解析或执行宏。只做只读文本/语法层扫描。

定位器：`{'kind':'code','file':rel_path,'line':行号,'symbol':名称}`；line 为真实行号，
snippet 为该行原文（结构事实最多 3 行上下文）。失败片段形状见 textline 模块 docstring。
"""

import re

from workbench.ontology_build.parsers.base import failure
from workbench.ontology_build.parsers import textline
from workbench.ontology_build.parsers.textline import FactSink, failed_segment, finish, rel_of

MAX_TEMPLATE_BINDINGS = 200      # .vue 模板绑定事实上限
MAX_TYPESCRIPT_MEMBERS = 400     # TS interface/type 成员事实上限
MAX_XML_STATEMENTS = 2000        # MyBatis 语句事实上限

JS_EXTENSIONS = ('js', 'jsx', 'ts', 'tsx', 'mjs', 'cjs')
PY_EXTENSIONS = ('py', 'pyw')

_SPRING_MAPPING = {
    'RequestMapping': ('ALL', None), 'GetMapping': ('GET', None), 'PostMapping': ('POST', None),
    'PutMapping': ('PUT', None), 'DeleteMapping': ('DELETE', None),
    'PatchMapping': ('PATCH', None), 'GetExchange': ('GET', None),
}
_SPRING_STEREOTYPE = ('RestController', 'Controller', 'Service', 'Component', 'Repository',
                      'Configuration', 'Transactional', 'FeignClient')
_JPA_ANNOTATIONS = ('Entity', 'Table', 'Column', 'Id', 'GeneratedValue', 'ManyToOne', 'OneToMany',
                    'ManyToMany', 'OneToOne', 'JoinColumn', 'JoinTable', 'EmbeddedId', 'Embedded',
                    'MappedSuperclass', 'Transient', 'Enumerated', 'Version', 'Basic')

_JAVA_TYPE_DECL = re.compile(r'\b(class|interface|enum)\s+([A-Za-z_$][\w$]*)')
_JAVA_RECORD_DECL = re.compile(r'\brecord\s+([A-Za-z_$][\w$]*)\s*\(([^)]*)\)')
_JAVA_PACKAGE = re.compile(r'^\s*package\s+([\w\.]+)\s*;')
_JAVA_EXTENDS = re.compile(r'\b(extends|implements)\s+([^{]+)')
_JAVA_ANNOTATION_LEAD = re.compile(r'^@([A-Za-z_][\w\.]*)\s*(?:\(((?:[^()]|\([^()]*\))*)\))?\s*')
_JAVA_METHOD = re.compile(
    r'^\s*(?:(?:public|protected|private|static|final|abstract|synchronized|native|default)\s+)*'
    r'(?:(?:<[^>]*>)\s*)?([A-Za-z_$][\w$.<>\[\],\s\?]*?)\s+([A-Za-z_$][\w$]*)\s*\(([^)]*)\)\s*'
    r'(?:throws\s+[\w\s,\.]+)?(?:\{|;)?\s*$')
_JAVA_FIELD = re.compile(
    r'^\s*(?:(?:public|protected|private|static|final|transient|volatile)\s+)*'
    r'([A-Za-z_$][\w$.<>\[\],\s\?]*?)\s+([A-Za-z_$][\w$]*)\s*(?:=[^;]*)?;\s*$')
_JAVA_ENUM_CONST = re.compile(r'^\s*([A-Z][A-Z0-9_]{0,63})\s*(?:\(|,|;|$)')
_JAVA_KEYWORDS = ('if', 'else', 'for', 'while', 'switch', 'case', 'return', 'throw', 'try',
                  'catch', 'finally', 'do', 'break', 'continue', 'new', 'assert', 'yield',
                  'import', 'package', 'synchronized', 'this', 'super', 'var')

_MYBATIS_TAG = re.compile(r'<(select|insert|update|delete|resultMap|sql|parameterMap)\b([^>]*)>', re.I)
_XML_ATTR = re.compile(r'([\w:\.\-]+)\s*=\s*"([^"]*)"')
_MYBATIS_PARAM = re.compile(r'[#$]\{\s*([^},\)]+)')
_RESULT_MAPPING = re.compile(r'<(result|id|association|collection)\b([^>]*)>', re.I)
_DYNAMIC_TAGS = {'if', 'choose', 'when', 'otherwise', 'foreach', 'where', 'set', 'trim',
                 'include', 'bind', 'selectKey'}

_JS_CLASS = re.compile(r'^\s*(?:export\s+)?(?:default\s+)?class\s+([A-Za-z_$][\w$]*)')
_JS_VAR_CALL = re.compile(r'^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*'
                          r'(ref|shallowRef|reactive|computed|toRef|toRefs|readonly|wrap)\s*[<(]')
_JS_VAR_OBJECT = re.compile(r'^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*\{')
_JS_VAR_NEW = re.compile(r'^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*new\s+([\w$.]+)\s*\(')
_JS_PROPS = re.compile(r'^\s*props\s*:\s*(\[|\{)')
_JS_EMITS = re.compile(r'^\s*emits\s*:\s*(\[|\{)')
_JS_PROPS_ARRAY = re.compile(r'^\s*props\s*:\s*\[([^\]]*)\]')
_JS_EMITS_ARRAY = re.compile(r'^\s*emits\s*:\s*\[([^\]]*)\]')
_JS_DEFINE_PROPS = re.compile(r'\bdefineProps\s*(?:<([^>]*)>)?\s*\(')
_JS_DEFINE_EMITS = re.compile(r'\bdefineEmits\s*(?:<([^>]*)>)?\s*\(')
_JS_OBJECT_KEY = re.compile(r'^\s*(?:[\'"]?)([A-Za-z_$][\w$]*)(?:[\'"]?)\s*:')
_JS_STRING_LITERAL = re.compile(r'([\'"`])((?:\\.|(?!\1).)*)\1')
_JS_FETCH = re.compile(r'(?:^|[^\w$.])fetch\s*\(\s*([\'"`])([^\'"`]+)')
_JS_AXIOS_VERB = re.compile(r'(?:^|[^\w$])(?:axios|[A-Za-z_$][\w$]*\.(?:http|api|request|service|client|axios))'
                            r'\s*\.(get|post|put|delete|patch|head|options)\s*\(\s*([\'"`])([^\'"`]+)', re.I)
_JS_REQUEST = re.compile(r'(?:^|[^\w$])(?:request|[A-Za-z_$][\w$]*\.(?:request|http|api|service|client))'
                         r'\s*\(\s*([\'"`])([^\'"`]+)', re.I)
_JS_URL_KEY = re.compile(r'\burl\s*:\s*([\'"`])([^\'"`]+)')
_JS_METHOD_KEY = re.compile(r'\bmethod\s*:\s*([\'"`])([A-Za-z]+)')
_JS_INTERFACE = re.compile(r'^\s*(?:export\s+)?(?:declare\s+)?(interface|type)\s+([A-Za-z_$][\w$]*)')
_JS_MEMBER = re.compile(r'^\s*(?:readonly\s+)?([A-Za-z_$][\w$]*)(\?)?\s*:\s*([^;]+);?\s*$')
_JS_DATA_DECL = re.compile(r'^\s*data\s*(?:\([^)]*\))?\s*(?:=[^>]*?=>|:|\{)\s*$')
_JS_SEGMENT_OPEN = re.compile(r'<(template|script|style)\b([^>]*)>', re.I)
_JS_SEGMENT_CLOSE = re.compile(r'</(template|script|style)\s*>', re.I)
# 属性绑定：v-bind:x="expr" / :x="expr" / @ev="handler" / v-on:ev="handler" /
# v-model(:x)?="expr" / v-if|v-else-if|v-show|v-for="expr"（只取引号内的表达式原文）
_TEMPLATE_BINDING = re.compile(
    r'(?:v-bind:(?P<bind>[\w\-]+)|:(?P<bind2>[\w\-]+)|v-on:(?P<on>[\w\-]+)|@(?P<on2>[\w\-]+)'
    r'|v-model(?::(?P<model>[\w\-]+))?|v-(?P<dir>if|else-if|show|for))\s*=\s*([\'"])(?P<expr>.*?)\4')
_TEMPLATE_EXPR = re.compile(r'\{\{\s*([^}]{1,120}?)\s*\}\}')

_PY_CLASS = re.compile(r'^(\s*)class\s+([A-Za-z_][\w]*)\s*(?:\(([^)]*)\))?\s*:')
_PY_FUNC = re.compile(r'^(\s*)(?:async\s+)?def\s+([A-Za-z_][\w]*)\s*\(([^)]*)\)')
_PY_DECORATOR = re.compile(r'^\s*@([\w\.]+)\s*(?:\((.*)\))?\s*$')
_PY_ROUTE = re.compile(r'^\s*@([\w\.]+)\.(route|get|post|put|delete|patch|head|options|websocket)\s*\((.*)$')
_PY_FIELD = re.compile(r'^(\s*)([A-Za-z_][\w]*)\s*:\s*([^=#]+?)\s*(?:=\s*(.+))?$')
_PY_TABLENAME = re.compile(r'^\s*__tablename__\s*=\s*([\'"])([^\'"]+)\1')
_PY_COLUMN = re.compile(r'^\s*([A-Za-z_][\w]*)\s*=\s*(?:db\.)?(?:mapped_)?[Cc]olumn\s*\(')
_PY_RELATIONSHIP = re.compile(r'^\s*([A-Za-z_][\w]*)\s*=\s*(?:db\.)?relationship\s*\((.*)$')
_PY_METHODS_KW = re.compile(r'\bmethods\s*=\s*\[([^\]]*)\]')


# ---------------------------------------------------------------------------
# 通用小工具
# ---------------------------------------------------------------------------
def _scan_c_like(lines):
    """C 系语法（Java/JS/TS）逐行清洗，返回两个**等长**变体：

    * `code`  —— 字符串内容与注释都置换为空格（供括号配平与结构匹配使用）。
    * `plain` —— 只把注释置换为空格，字符串原文保留（供注解参数、URL 等取值使用）。

    两个变体与原文逐字符同长（注释到行尾用空格补齐，跨行字符串/块注释状态在行间传递），
    因此可以用同一组偏移量在两者之间切换，既不误匹配注释里的内容，也不丢字符串参数。
    """
    code_lines = []
    plain_lines = []
    in_block_comment = False
    in_template = False
    for line in lines:
        code = []
        plain = []
        index = 0
        in_string = None
        while index < len(line):
            char = line[index]
            pair = line[index:index + 2]
            if in_block_comment:
                if pair == '*/':
                    in_block_comment = False
                    code.append('  ')
                    plain.append('  ')
                    index += 2
                    continue
                code.append(' ')
                plain.append(' ')
                index += 1
                continue
            if in_string:
                if char == '\\':
                    code.append('  ')
                    plain.append(line[index:index + 2])
                    index += 2
                    continue
                if char == in_string:
                    in_string = None
                code.append(' ')
                plain.append(char)
                index += 1
                continue
            if in_template:
                if char == '\\':
                    code.append('  ')
                    plain.append(line[index:index + 2])
                    index += 2
                    continue
                if char == '`':
                    in_template = False
                code.append(' ')
                plain.append(char)
                index += 1
                continue
            if pair == '//':
                code.append(' ' * (len(line) - index))
                plain.append(' ' * (len(line) - index))
                index = len(line)
                continue
            if pair == '/*':
                in_block_comment = True
                code.append('  ')
                plain.append('  ')
                index += 2
                continue
            if char in ('"', "'"):
                in_string = char
                code.append(' ')
                plain.append(char)
                index += 1
                continue
            if char == '`':
                in_template = True
                code.append(' ')
                plain.append(char)
                index += 1
                continue
            code.append(char)
            plain.append(char)
            index += 1
        code_lines.append(''.join(code))
        plain_lines.append(''.join(plain))
    return code_lines, plain_lines


def _clean_c_like(lines):
    """仅返回 code 变体（兼容旧调用点）。"""
    return _scan_c_like(lines)[0]


def _empty_line(line):
    return not str(line or '').strip()

def _strip_python_strings(line):
    """Python 行内字符串/注释置空（保留长度）；字符串可跨行时由调用方按行处理。"""
    buf = []
    index = 0
    quote = None
    while index < len(line):
        char = line[index]
        if quote:
            if char == '\\':
                buf.append('  ')
                index += 2
                continue
            if char == quote:
                quote = None
            buf.append(' ')
            index += 1
            continue
        if char in ('"', "'"):
            quote = char
            buf.append(' ')
            index += 1
            continue
        if char == '#':
            break
        buf.append(char)
        index += 1
    return ''.join(buf)


def _balanced(lines):
    """大括号/圆括号是否整体配平（未配平 → 结构化事实降为 medium 并加 warning）。"""
    depth = 0
    for line in lines:
        depth += line.count('{') + line.count('(') - line.count('}') - line.count(')')
        if depth < 0:
            return False
    return depth == 0


def _split_top_level(raw):
    """按顶层逗号切分（忽略括号与引号内部逗号）。"""
    parts, depth, buf, quote = [], 0, [], None
    for char in str(raw or ''):
        if quote:
            buf.append(char)
            if char == quote:
                quote = None
            continue
        if char in ('"', "'"):
            quote = char
            buf.append(char)
            continue
        if char in '([{':
            depth += 1
        elif char in ')]}':
            depth -= 1
        if char == ',' and depth <= 0:
            parts.append(''.join(buf).strip())
            buf = []
            continue
        buf.append(char)
    tail = ''.join(buf).strip()
    if tail:
        parts.append(tail)
    return parts


def _unquote(value):
    text = str(value or '').strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ('"', "'"):
        return text[1:-1]
    return text


def _annotation_args(raw):
    """注解/装饰器参数：位置参数列表 + 命名参数字典。"""
    positional, named = [], {}
    for part in _split_top_level(raw):
        if not part:
            continue
        head, sep, tail = part.partition('=')
        if sep and re.match(r'^[\w\.]+$', head.strip()):
            named[head.strip()] = _unquote(tail.strip())
        else:
            positional.append(_unquote(part.strip()))
    return positional, named


def _first_string(raw):
    match = re.search(r'([\'"])([^\'"]*)\1', str(raw or ''))
    return match.group(2) if match else ''


# ---------------------------------------------------------------------------
# Java
# ---------------------------------------------------------------------------
def _java_package(code_lines):
    for line in code_lines:
        match = _JAVA_PACKAGE.match(line)
        if match:
            return match.group(1)
    return ''


def _take_annotations(code_line, plain_line):
    """从行首（可含空白）连续取出注解，返回 (annotations, 剩余 code 文本, 剩余起点)。

    括号配平在 `code` 变体上做（字符串已置空，括号不会误判），参数文本取 `plain` 变体
    （字符串原文保留）。两变体等长，偏移可直接互换。annotations 元素为
    {'name','fullName','args','start'}。
    """
    annotations = []
    index = 0
    length = len(code_line)
    while index < length and code_line[index] in ' \t':
        index += 1
    while index < length and code_line[index] == '@':
        match = re.match(r'@([A-Za-z_][\w\.]*)', code_line[index:])
        if not match:
            break
        full_name = match.group(1)
        cursor = index + match.end()
        args = ''
        probe = cursor
        while probe < length and code_line[probe] in ' \t':
            probe += 1
        if probe < length and code_line[probe] == '(':
            depth = 0
            close = -1
            scan = probe
            while scan < length:
                char = code_line[scan]
                if char == '(':
                    depth += 1
                elif char == ')':
                    depth -= 1
                    if depth == 0:
                        close = scan
                        break
                scan += 1
            if close > 0:
                args = plain_line[probe + 1:close]
                cursor = close + 1
            else:
                # 注解参数跨行/未闭合：记录证据边界
                args = plain_line[probe + 1:]
                cursor = length
        annotations.append({'name': full_name.rsplit('.', 1)[-1], 'fullName': full_name,
                            'args': args.strip(), 'start': index})
        index = cursor
        while index < length and code_line[index] in ' \t':
            index += 1
    return annotations, code_line[index:], index


def _java_lines(content, rel, sink):
    """Java 结构化扫描：注解归属 + 括号配平 + 类型/方法/字段/枚举常量。"""
    code_lines, plain_lines = _scan_c_like(content.lines)
    file_balanced = _balanced(code_lines)
    package = _java_package(code_lines)
    depth = 0
    stack = []            # [{'kind','name','depth','bodyDepth','isEnum'}]
    pending = []          # [{'name','args','line','fact'}]
    awaiting_open = None
    notes, failed = [], []
    modules = ['java']
    observed = {'spring': False, 'jpa': False}
    annotated_targets = 0

    for number, code_line in enumerate(code_lines, start=1):
        raw = content.line(number)
        plain_line = plain_lines[number - 1]
        stripped = code_line.strip()
        if not stripped:
            continue

        if awaiting_open is not None:
            if code_line.lstrip().startswith('{'):
                stack.append({'kind': awaiting_open['kind'], 'name': awaiting_open['name'],
                              'depth': depth, 'bodyDepth': depth + 1,
                              'isEnum': awaiting_open.get('isEnum', False)})
                awaiting_open = None
                depth += code_line.count('{') - code_line.count('}')
                continue
            awaiting_open = None

        # 1) 注解（可多枚，可与声明同行）
        annotations, remainder, offset = _take_annotations(code_line, plain_line)
        if annotations:
            pending = [item for item in pending if item['line'] >= number - 3]
            for item in annotations:
                short = item['name']
                positional, named = _annotation_args(item['args'])
                fact = sink.add(
                    'jpa' if short in _JPA_ANNOTATIONS else 'spring',
                    {'kind': 'code', 'file': rel, 'line': number, 'symbol': short},
                    textline.snippet_span(content, number, number, 1), 'annotation',
                    {'annotation': short, 'fullName': item['fullName'], 'args': item['args'],
                     'named': named, 'positional': positional, 'package': package,
                     'owner': stack[-1]['name'] if stack else '', 'target': ''}, 'high')
                if short in _JPA_ANNOTATIONS:
                    observed['jpa'] = True
                if short in _SPRING_MAPPING or short in _SPRING_STEREOTYPE:
                    observed['spring'] = True
                pending.append({'name': short, 'args': item['args'], 'line': number, 'fact': fact})
            if not remainder.strip():
                continue
            stripped = remainder.strip()
            code_line = remainder
        if not stripped:
            continue

        # 2) 类/接口/枚举/record 声明
        declaration = _JAVA_TYPE_DECL.search(code_line)
        record = _JAVA_RECORD_DECL.search(code_line)
        if declaration or record:
            if declaration:
                keyword, name = declaration.group(1), declaration.group(2)
                components = []
            else:
                keyword, name = 'record', record.group(1)
                components = [item.strip() for item in _split_top_level(record.group(2))]
            data = {'keyword': keyword, 'name': name, 'package': package,
                    'annotations': [item['name'] for item in pending],
                    'annotationLines': [{'name': item['name'], 'args': item['args'],
                                         'line': item['line']} for item in pending],
                    'qualified': ('%s.%s' % (package, name)) if package else name,
                    'declaration': raw.strip()}
            if components:
                data['components'] = components
            extends = _JAVA_EXTENDS.search(code_line)
            if extends:
                data[extends.group(1)] = [item.strip() for item in extends.group(2).split(',')
                                          if item.strip()]
            symbol = data['qualified']
            sink.add('java', {'kind': 'code', 'file': rel, 'line': number, 'symbol': symbol},
                     textline.snippet_span(content, number, number, 1), keyword, data,
                     'high' if file_balanced else 'medium')
            annotated_targets += _retarget(pending, symbol)
            pending = []
            if code_line.count('{') > code_line.count('}'):
                stack.append({'kind': 'type', 'name': symbol, 'depth': depth,
                              'bodyDepth': depth + 1, 'isEnum': (keyword == 'enum')})
            else:
                awaiting_open = {'kind': 'type', 'name': symbol, 'isEnum': (keyword == 'enum')}
            depth += code_line.count('{') - code_line.count('}')
            continue

        # 3) 方法签名（含 Spring 路由）
        method = _JAVA_METHOD.match(stripped)
        field = _JAVA_FIELD.match(stripped) if not method else None

        if method and not _is_control_flow(stripped):
            return_type = method.group(1).strip()
            method_name = method.group(2)
            params = method.group(3).strip()
            owner = stack[-1]['name'] if stack and stack[-1]['kind'] == 'type' else ''
            data = {'name': method_name, 'owner': owner, 'returnType': return_type,
                    'params': params,
                    'annotations': [item['name'] for item in pending],
                    'annotationLines': [{'name': item['name'], 'args': item['args'],
                                         'line': item['line']} for item in pending],
                    'package': package, 'declaration': raw.strip()}
            route = None
            for item in pending:
                if item['name'] not in _SPRING_MAPPING:
                    continue
                positional, named = _annotation_args(item['args'])
                verbs = _SPRING_MAPPING[item['name']][0]
                path = named.get('value') or named.get('path') or (positional[0] if positional else '')
                route = {'annotation': item['name'], 'method': verbs, 'path': path,
                         'annotationLine': item['line'], 'produces': named.get('produces', ''),
                         'consumes': named.get('consumes', ''), 'params': named.get('params', '')}
                observed['spring'] = True
            if route:
                data['route'] = route
            symbol = '%s.%s' % (owner, method_name) if owner else method_name
            sink.add('spring' if route else 'java',
                     {'kind': 'code', 'file': rel, 'line': number, 'symbol': symbol},
                     textline.snippet_span(content, number, number, 1),
                     'route' if route else 'method', data,
                     'high' if owner and file_balanced else 'medium')
            annotated_targets += _retarget(pending, symbol)
            pending = []
            if code_line.count('{') > code_line.count('}'):
                stack.append({'kind': 'method', 'name': symbol, 'depth': depth,
                              'bodyDepth': depth + 1})
            elif stripped.endswith('{') or stripped.endswith(')'):
                awaiting_open = {'kind': 'method', 'name': symbol}
            depth += code_line.count('{') - code_line.count('}')
            continue

        if field and not _is_control_flow(stripped):
            type_name, field_name = field.group(1).strip(), field.group(2)
            in_type = bool(stack) and stack[-1]['kind'] == 'type'
            owner = stack[-1]['name'] if in_type else ''
            data = {'name': field_name, 'type': type_name, 'owner': owner, 'package': package,
                    'annotations': [item['name'] for item in pending],
                    'annotationLines': [{'name': item['name'], 'args': item['args'],
                                         'line': item['line']} for item in pending],
                    'declaration': raw.strip()}
            jpa = False
            for item in pending:
                positional, named = _annotation_args(item['args'])
                if item['name'] == 'Column':
                    jpa = True
                    data['column'] = named.get('name') or (positional[0] if positional else '')
                    data['columnDefinition'] = named.get('columnDefinition', '')
                    data['nullable'] = named.get('nullable', '')
                    data['length'] = named.get('length', '')
                elif item['name'] == 'JoinColumn':
                    jpa = True
                    data['joinColumn'] = named.get('name') or (positional[0] if positional else '')
                    data['referencedColumnName'] = named.get('referencedColumnName', '')
                elif item['name'] in ('ManyToOne', 'OneToMany', 'OneToOne', 'ManyToMany'):
                    jpa = True
                    data.setdefault('relations', []).append({'kind': item['name'],
                                                             'args': item['args'],
                                                             'line': item['line']})
                elif item['name'] == 'Id':
                    jpa = True
                    data['id'] = True
                elif item['name'] == 'GeneratedValue':
                    jpa = True
                    data['generated'] = item['args']
                elif item['name'] == 'Transient':
                    jpa = True
                    data['transient'] = True
                elif item['name'] in _JPA_ANNOTATIONS:
                    jpa = True
            if jpa:
                observed['jpa'] = True
            symbol = '%s.%s' % (owner, field_name) if owner else field_name
            sink.add('jpa' if jpa else 'java',
                     {'kind': 'code', 'file': rel, 'line': number, 'symbol': symbol},
                     textline.snippet_span(content, number, number, 1), 'field', data,
                     'high' if in_type and file_balanced else 'medium')
            annotated_targets += _retarget(pending, symbol)
            if not in_type and stack and stack[-1]['kind'] == 'method':
                notes.append('第 %d 行 `%s` 位于方法体内，未登记为类字段（只登记类/接口字段）。'
                             % (number, field_name))
            pending = []
            depth += code_line.count('{') - code_line.count('}')
            continue

        # 4) 枚举常量（枚举体内、与成员同层）
        if stack and stack[-1]['kind'] == 'type' and stack[-1].get('isEnum') \
                and depth == stack[-1]['bodyDepth']:
            const = _JAVA_ENUM_CONST.match(stripped)
            if const:
                owner = stack[-1]['name']
                sink.add('java',
                         {'kind': 'code', 'file': rel, 'line': number,
                          'symbol': '%s.%s' % (owner, const.group(1))},
                         textline.snippet_span(content, number, number, 1), 'enumValue',
                         {'name': const.group(1), 'owner': owner, 'package': package}, 'high')

        # 5) 未归属的注解 / 跨行签名 → 明确记录证据边界
        if pending and _looks_like_signature(stripped) and '(' in stripped and ')' not in stripped:
            failed.append(failed_segment(
                'line', {'kind': 'code', 'file': rel, 'line': number, 'symbol': ''},
                '方法签名跨行（单行解析无法确认参数与返回类型），未产出结构化事实。', raw))
            pending = []
        elif pending:
            names = '、'.join('@%s' % item['name'] for item in pending)
            notes.append('第 %d 行附近的注解 %s 未能归属到声明（可能是未适配写法或注解参数复杂），'
                         'target 留空，证据不完整。' % (pending[0]['line'], names))
            pending = []

        depth += code_line.count('{') - code_line.count('}')
        while stack and depth <= stack[-1]['depth']:
            stack.pop()
            if not stack:
                awaiting_open = None

    if not file_balanced:
        notes.append('文件括号未整体配平（可能是生成代码、正则字面量或片段文件），'
                     '结构化事实按 medium 标注。')
    notes.append('已扫描 %d 行 Java；包名 %s；注解归属到声明 %d 处。'
                 % (len(content.lines), package or '(未声明)', annotated_targets))
    if observed['spring']:
        modules.append('spring')
    if observed['jpa']:
        modules.append('jpa')
    notes.append('未解析：注释文字中的结构描述、反射/字节码生成代码、字符串拼装 SQL、'
                 'Lombok 等注解处理器生成的成员（不编造结构）。')
    return modules, notes, failed


def _retarget(pending, symbol):
    """把注解事实的 target 指向其归属的声明；返回归属数量。"""
    count = 0
    for item in pending:
        if item['fact'] is not None:
            item['fact'].data['target'] = symbol
            count += 1
    return count


def _is_control_flow(stripped):
    head = re.split(r'[\s(]', stripped, 1)[0]
    return head in _JAVA_KEYWORDS


def _looks_like_signature(stripped):
    return bool(re.match(r'^(?:public|protected|private|static|final|abstract|synchronized|default)\b',
                         stripped)) and '(' in stripped


# ---------------------------------------------------------------------------
# MyBatis / 通用 XML
# ---------------------------------------------------------------------------
def _tag_span(lines, start_line, tag):
    """按标签配平找到元素结束行（未闭合则到文件末尾）。"""
    depth = 0
    collected = []
    open_re = re.compile(r'<%s\b' % re.escape(tag), re.I)
    self_re = re.compile(r'<%s\b[^>]*/>' % re.escape(tag), re.I)
    close_re = re.compile(r'</%s\s*>' % re.escape(tag), re.I)
    for number in range(start_line, len(lines) + 1):
        raw = lines[number - 1]
        collected.append(raw)
        depth += len(open_re.findall(raw)) - len(self_re.findall(raw)) - len(close_re.findall(raw))
        if depth <= 0:
            return collected, number
    return collected, len(lines)


def _xml_line_facts(content, rel, sink):
    """XML：行扫描定位 MyBatis 语句 + ElementTree 结构校验；无语句则按文本线索降级。"""
    notes, failed = [], []
    modules = ['xml']
    namespace = ''
    for line in content.lines:
        match = re.search(r'<mapper\b[^>]*namespace\s*=\s*"([^"]*)"', line, re.I)
        if match:
            namespace = match.group(1)
            break

    structure_ok = True
    structure_error = ''
    try:
        import xml.etree.ElementTree as ET
        ET.fromstring('\n'.join(content.lines))
    except Exception as exc:  # noqa: BLE001 - XML 结构错误必须显式降级
        structure_ok = False
        structure_error = '%s: %s' % (exc.__class__.__name__, str(exc)[:160])

    count = 0
    truncated_statements = False
    for number, line in enumerate(content.lines, start=1):
        for match in _MYBATIS_TAG.finditer(line):
            if count >= MAX_XML_STATEMENTS:
                truncated_statements = True
                break
            tag = match.group(1).lower()
            attrs = {item.group(1): item.group(2) for item in _XML_ATTR.finditer(match.group(2))}
            statement_id = attrs.get('id', '')
            body_lines, end_line = _tag_span(content.lines, number, tag)
            body = '\n'.join(body_lines)
            symbol = '%s.%s' % (namespace, statement_id) if namespace and statement_id else statement_id
            params = []
            for item in _MYBATIS_PARAM.finditer(body):
                name = item.group(1).strip()
                if name and name not in params:
                    params.append(name)
            data = {
                'statementId': symbol, 'namespace': namespace, 'id': statement_id, 'type': tag,
                'attrs': attrs, 'params': params,
                'paramStyle': {'hash': '#{}' in body, 'dollar': '${' in body},
                'dynamicTags': sorted(set(re.findall(r'<(\w+)\b', body)) & _DYNAMIC_TAGS),
                'startLine': number, 'endLine': end_line,
            }
            if data['paramStyle']['dollar']:
                notes.append('第 %d 行语句 `%s` 使用 ${} 字符串拼接（SQL 注入风险，且动态语句无法静态确定）。'
                             % (number, symbol or '?'))
            if tag == 'resultmap':
                mappings = []
                for item in _RESULT_MAPPING.finditer(body):
                    mapping = {sub.group(1): sub.group(2) for sub in _XML_ATTR.finditer(item.group(2))}
                    mappings.append({'element': item.group(1).lower(),
                                     'column': mapping.get('column', ''),
                                     'property': mapping.get('property', ''),
                                     'jdbcType': mapping.get('jdbcType', ''),
                                     'javaType': mapping.get('javaType', '')})
                data['mappings'] = mappings[:200]
                if len(mappings) > 200:
                    notes.append('resultMap `%s` 字段映射 %d 条，仅保留前 200 条。'
                                 % (symbol or '?', len(mappings)))
                kind = 'resultMap'
            elif tag == 'sql':
                kind = 'sqlFragment'
            else:
                kind = 'statement'
            sink.add('mybatis', {'kind': 'code', 'file': rel, 'line': number, 'symbol': symbol},
                     textline.snippet_span(content, number, min(end_line, number + 1), 2), kind,
                     data, 'high' if structure_ok else 'medium')
            modules.append('mybatis')
            count += 1
        if truncated_statements:
            break

    if truncated_statements:
        notes.append('MyBatis 语句超过单文件上限 %d 条，其余未解析。' % MAX_XML_STATEMENTS)
    if not count:
        for number, line in enumerate(content.lines, start=1):
            text = line.strip()
            if text:
                sink.add('xml', {'kind': 'text', 'file': rel, 'line': number, 'symbol': ''}, text,
                         'textLine', {'line': number}, quality='low')
        notes.append('未识别到 MyBatis 映射语句（未找到 <select|insert|update|delete|resultMap|sql>），'
                     '按文本线索降级，覆盖不足。')
        if not structure_ok:
            notes.append('XML 结构校验失败：%s' % structure_error)
        return modules, notes, failed, True

    if not structure_ok:
        failed.append(failed_segment(
            'document', {'kind': 'code', 'file': rel, 'line': 1, 'symbol': ''},
            'XML 结构校验失败（%s）：语句定位来自行扫描，可能不完整。' % structure_error))
    if namespace:
        notes.append('MyBatis mapper namespace=%s，语句 %d 条。' % (namespace, count))
    else:
        notes.append('XML 中未找到 <mapper namespace>，语句按本地 id 登记。')
    notes.append('未解析：外部 include 引用的 fragment、动态 SQL 运行期拼接结果、CDATA 外的脚本逻辑。')
    return modules, notes, failed, (not structure_ok)


# ---------------------------------------------------------------------------
# JS / TS / Vue
# ---------------------------------------------------------------------------
def _object_keys(cleaned, start, end, base_depth, prefer_return=True):
    """提取对象字面量的顶层键（用于 data()/props 对象）；返回 [(name, line, typeHint)]。"""
    depth = base_depth
    open_line = None
    if prefer_return:
        for number in range(start, end + 1):
            if re.search(r'\breturn\b[^;{]*\{', cleaned[number - 1]):
                open_line = number
                break
    if open_line is None:
        for number in range(start, end + 1):
            if '{' in cleaned[number - 1]:
                open_line = number
                break
    if open_line is None:
        return []
    depth += cleaned[open_line - 1].count('{') - cleaned[open_line - 1].count('}')
    keys_depth = depth
    keys = []
    for number in range(open_line + 1, end + 1):
        line = cleaned[number - 1]
        if depth == keys_depth:
            match = _JS_OBJECT_KEY.match(line)
            if match:
                hint = line.split(':', 1)[1].strip().rstrip(',')
                keys.append((match.group(1), number, hint))
        depth += line.count('{') - line.count('}')
        if depth < keys_depth:
            break
    return keys


def _js_lines(content, rel, sink, start=1, end=None):
    """JS/TS 行扫描：类、组合式/选项式字段、props/emits、HTTP URL、TS 类型成员。"""
    cleaned, plain_lines = _scan_c_like(content.lines)
    last = end if end else len(content.lines)
    notes, failed, modules = [], [], []
    depth = 0
    in_type = None
    type_members = 0
    seen_data = set()
    seen_props = set()

    for number in range(start, last + 1):
        raw = content.lines[number - 1]
        line = cleaned[number - 1]
        plain = plain_lines[number - 1]
        stripped = line.strip()
        if not stripped:
            continue

        class_match = _JS_CLASS.match(line)
        if class_match:
            name = class_match.group(1)
            sink.add('javascript', {'kind': 'code', 'file': rel, 'line': number, 'symbol': name},
                     textline.snippet_span(content, number, number, 1), 'class',
                     {'name': name, 'declaration': raw.strip()}, 'high')
            modules.append('javascript')

        interface_match = _JS_INTERFACE.match(line)
        if interface_match:
            name = interface_match.group(2)
            in_type = (name, depth + 1)
            sink.add('typescript', {'kind': 'code', 'file': rel, 'line': number, 'symbol': name},
                     textline.snippet_span(content, number, number, 1), interface_match.group(1),
                     {'name': name, 'declaration': raw.strip(), 'kind': interface_match.group(1)},
                     'high')
            modules.append('typescript')

        var_call = _JS_VAR_CALL.match(line)
        if var_call:
            name, factory = var_call.group(1), var_call.group(2)
            sink.add('vue' if factory in ('ref', 'shallowRef', 'reactive', 'computed', 'toRef',
                                          'toRefs', 'readonly') else 'javascript',
                     {'kind': 'code', 'file': rel, 'line': number, 'symbol': name},
                     textline.snippet_span(content, number, number, 1), 'field',
                     {'name': name, 'factory': factory, 'owner': 'composition',
                      'typeHint': _type_hint(plain), 'declaration': raw.strip()}, 'high')
            modules.append('composition' if factory in ('ref', 'shallowRef', 'reactive') else 'javascript')

        var_object = _JS_VAR_OBJECT.match(line)
        if var_object:
            name = var_object.group(1)
            sink.add('javascript', {'kind': 'code', 'file': rel, 'line': number, 'symbol': name},
                     textline.snippet_span(content, number, number, 1), 'field',
                     {'name': name, 'owner': 'module', 'shape': 'object', 'declaration': raw.strip()},
                     'high')

        var_new = _JS_VAR_NEW.match(line)
        if var_new:
            sink.add('javascript', {'kind': 'code', 'file': rel, 'line': number, 'symbol': var_new.group(1)},
                     textline.snippet_span(content, number, number, 1), 'field',
                     {'name': var_new.group(1), 'owner': 'module', 'shape': var_new.group(2),
                      'declaration': raw.strip()}, 'medium')

        props_array = _JS_PROPS_ARRAY.match(plain)
        if props_array:
            for item in _JS_STRING_LITERAL.finditer(props_array.group(1)):
                sink.add('vue', {'kind': 'code', 'file': rel, 'line': number, 'symbol': item.group(2)},
                         textline.snippet_span(content, number, number, 1), 'prop',
                         {'name': item.group(2), 'style': 'options-array'}, 'high')
        emits_array = _JS_EMITS_ARRAY.match(plain)
        if emits_array:
            for item in _JS_STRING_LITERAL.finditer(emits_array.group(1)):
                sink.add('vue', {'kind': 'code', 'file': rel, 'line': number, 'symbol': item.group(2)},
                         textline.snippet_span(content, number, number, 1), 'emit',
                         {'name': item.group(2), 'style': 'options-array'}, 'high')

        if _JS_PROPS.match(line) and not props_array and number not in seen_props:
            seen_props.add(number)
            for name, key_line, hint in _object_keys(cleaned, number, min(number + 60, last), depth,
                                                    prefer_return=False):
                sink.add('vue', {'kind': 'code', 'file': rel, 'line': key_line, 'symbol': name},
                         textline.snippet_span(content, key_line, key_line, 1), 'prop',
                         {'name': name, 'style': 'options-object', 'typeHint': hint}, 'high')

        if _JS_DATA_DECL.match(line) and number not in seen_data:
            seen_data.add(number)
            for name, key_line, hint in _object_keys(cleaned, number, min(number + 80, last), depth,
                                                     prefer_return=True):
                sink.add('vue', {'kind': 'code', 'file': rel, 'line': key_line, 'symbol': name},
                         textline.snippet_span(content, key_line, key_line, 1), 'field',
                         {'name': name, 'owner': 'options-data', 'typeHint': hint}, 'high')

        for match in _JS_DEFINE_PROPS.finditer(plain):
            for name in _define_names(match.group(1), plain):
                sink.add('vue', {'kind': 'code', 'file': rel, 'line': number, 'symbol': name},
                         textline.snippet_span(content, number, min(number + 2, last), 3), 'prop',
                         {'name': name, 'style': 'script-setup'}, 'high')
        for match in _JS_DEFINE_EMITS.finditer(plain):
            for name in _define_names(match.group(1), plain):
                sink.add('vue', {'kind': 'code', 'file': rel, 'line': number, 'symbol': name},
                         textline.snippet_span(content, number, min(number + 2, last), 3), 'emit',
                         {'name': name, 'style': 'script-setup'}, 'high')

        # --- HTTP 调用（只读取第一个字符串参数，不发起请求） ---
        fetch_match = _JS_FETCH.search(plain)
        if fetch_match:
            module = 'http'
            sink.add(module, {'kind': 'code', 'file': rel, 'line': number, 'symbol': fetch_match.group(2)},
                     textline.snippet_span(content, number, number, 1), 'httpCall',
                     {'url': fetch_match.group(2), 'method': '', 'api': 'fetch',
                      'methodKnown': False}, 'medium')
            modules.append('http')
        axios_matches = list(_JS_AXIOS_VERB.finditer(plain))
        for match in axios_matches:
            sink.add('http', {'kind': 'code', 'file': rel, 'line': number, 'symbol': match.group(3)},
                     textline.snippet_span(content, number, number, 1), 'httpCall',
                     {'url': match.group(3), 'method': match.group(1).upper(), 'api': 'axios',
                      'methodKnown': True}, 'high')
            modules.append('http')
        if not axios_matches and not fetch_match:
            request_match = _JS_REQUEST.search(plain)
            if request_match:
                sink.add('http', {'kind': 'code', 'file': rel, 'line': number,
                                  'symbol': request_match.group(2)},
                         textline.snippet_span(content, number, number, 1), 'httpCall',
                         {'url': request_match.group(2), 'method': '', 'api': 'request',
                          'methodKnown': False}, 'medium')
                modules.append('http')
        if not axios_matches and not fetch_match:
            url_match = _JS_URL_KEY.search(plain)
            if url_match:
                method_match = _JS_METHOD_KEY.search(plain)
                sink.add('http', {'kind': 'code', 'file': rel, 'line': number,
                                  'symbol': url_match.group(2)},
                         textline.snippet_span(content, number, number, 1), 'httpCall',
                         {'url': url_match.group(2),
                          'method': method_match.group(2).upper() if method_match else '',
                          'api': 'config', 'methodKnown': bool(method_match)}, 'medium')
                modules.append('http')

        if in_type and depth == in_type[1]:
            member = _JS_MEMBER.match(line)
            if member and type_members < MAX_TYPESCRIPT_MEMBERS:
                type_members += 1
                sink.add('typescript',
                         {'kind': 'code', 'file': rel, 'line': number,
                          'symbol': '%s.%s' % (in_type[0], member.group(1))},
                         textline.snippet_span(content, number, number, 1), 'typeMember',
                         {'name': member.group(1), 'type': member.group(3).strip(),
                          'optional': bool(member.group(2)), 'owner': in_type[0]}, 'high')

        depth += line.count('{') - line.count('}')
        if in_type and depth < in_type[1]:
            in_type = None

    if type_members >= MAX_TYPESCRIPT_MEMBERS:
        notes.append('TS interface/type 成员超过上限 %d 条，其余未解析。' % MAX_TYPESCRIPT_MEMBERS)
    return modules, notes, failed


def _type_hint(raw):
    """从 `ref<Type>(…)` / `name: Type` 提示类型（尽力而为，不确定则空）。"""
    match = re.search(r'\bref\s*<([^>]+)>', raw)
    if match:
        return match.group(1).strip()
    match = re.search(r'[:=]\s*([A-Za-z_$][\w$<>,\[\]\.\s\|]*?)[,;}\)]\s*$', raw)
    if match:
        return match.group(1).strip()
    return ''


def _define_names(type_arg, raw):
    """从 defineProps<T>() / defineProps(['a']) / defineProps({a: …}) 提取名称。"""
    names = []
    if type_arg:
        for item in _split_top_level(type_arg):
            match = re.match(r'^\s*([A-Za-z_$][\w$]*)\s*\??\s*:', item)
            if match:
                names.append(match.group(1))
        return names
    for item in _JS_STRING_LITERAL.finditer(raw):
        names.append(item.group(2))
    for item in _split_top_level(raw):
        match = re.match(r'^\s*([A-Za-z_$][\w$]*)\s*\??\s*:', item)
        if match:
            names.append(match.group(1))
    return names


def _vue_segments(content, rel, sink):
    """切分 .vue 的 <template>/<script>/<style> 段（含行范围与属性）。"""
    segments = []
    for number, line in enumerate(content.lines, start=1):
        for match in _JS_SEGMENT_OPEN.finditer(line):
            tag = match.group(1).lower()
            end_line = len(content.lines)
            for probe in range(number, len(content.lines) + 1):
                close = _JS_SEGMENT_CLOSE.search(content.lines[probe - 1])
                if close and close.group(1).lower() == tag:
                    end_line = probe
                    break
            attrs = {}
            for item in _XML_ATTR.finditer(match.group(2)):
                attrs[item.group(1).lower()] = item.group(2)
            segments.append({'tag': tag, 'start': number, 'end': end_line, 'attrs': attrs,
                             'selfClosing': match.group(2).rstrip().endswith('/')})
    segments.sort(key=lambda item: item['start'])
    for segment in segments:
        sink.add('vue', {'kind': 'code', 'file': rel, 'line': segment['start'],
                         'symbol': '<%s>' % segment['tag']},
                 textline.snippet_span(content, segment['start'], None, 1), 'segment',
                 {'tag': segment['tag'], 'startLine': segment['start'], 'endLine': segment['end'],
                  'attrs': segment['attrs']}, 'high')
    return segments


def _template_facts(content, rel, sink, segments):
    """模板绑定：v-bind/v-model/@event/{{ }} 引用的标识符（字段使用证据，medium）。"""
    template = next((item for item in segments if item['tag'] == 'template'), None)
    if not template:
        return 0, []
    count = 0
    for number in range(template['start'], template['end'] + 1):
        raw = content.line(number)
        for match in _TEMPLATE_BINDING.finditer(raw):
            expression = match.group('expr') or ''
            directive = _directive_name(match)
            names = _expression_identifiers(expression)
            if not names:
                continue
            for name in names:
                if count >= MAX_TEMPLATE_BINDINGS:
                    return count, ['模板绑定超过上限 %d 条，其余未解析。' % MAX_TEMPLATE_BINDINGS]
                count += 1
                sink.add('vue', {'kind': 'code', 'file': rel, 'line': number, 'symbol': name},
                         textline.snippet_span(content, number, number, 1), 'templateBinding',
                         {'name': name, 'directive': directive, 'expression': expression.strip()},
                         'medium')
        for match in _TEMPLATE_EXPR.finditer(raw):
            if count >= MAX_TEMPLATE_BINDINGS:
                return count, ['模板插值超过上限 %d 条，其余未解析。' % MAX_TEMPLATE_BINDINGS]
            count += 1
            sink.add('vue', {'kind': 'code', 'file': rel, 'line': number,
                             'symbol': match.group(1)[:60]},
                     textline.snippet_span(content, number, number, 1), 'templateExpression',
                     {'expression': match.group(1)}, 'medium')
    return count, []


def _parse_vue(content, rel, sink):
    segments = _vue_segments(content, rel, sink)
    notes, failed = [], []
    script = next((item for item in segments if item['tag'] == 'script'), None)
    if script:
        modules, script_notes, script_failed = _js_lines(content, rel, sink, start=script['start'],
                                                        end=script['end'])
        notes.extend(script_notes)
        failed.extend(script_failed)
        if 'setup' not in script['attrs']:
            notes.append('未检测到 <script setup>（按选项式/组合式混合解析）。')
    else:
        failed.append(failed_segment('segment', {'kind': 'code', 'file': rel, 'line': 1, 'symbol': ''},
                                     '未找到 <script> 段：脚本结构与字段未解析。'))
    binding_count, binding_notes = _template_facts(content, rel, sink, segments)
    notes.extend(binding_notes)
    notes.append('.vue 分段 %d 个，模板绑定/插值 %d 条。' % (len(segments), binding_count))
    if not any(item['tag'] == 'style' for item in segments):
        notes.append('未找到 <style> 段（样式不影响本体事实）。')
    return notes, failed


# ---------------------------------------------------------------------------
# Python
# ---------------------------------------------------------------------------
def _py_framework(source_text, decorator_object):
    """判定路由框架：文件内 FastAPI/Flask 导入 + 装饰器对象名。"""
    has_fastapi = 'FastAPI' in source_text or 'from fastapi' in source_text
    has_flask = 'Flask' in source_text or 'from flask' in source_text
    if decorator_object.lower() in ('router', 'app_router', 'api_router') and has_fastapi:
        return 'fastapi'
    if has_fastapi and not has_flask:
        return 'fastapi'
    if has_flask and not has_fastapi:
        return 'flask'
    return 'unknown'


def _python_blocks(content, rel, sink):
    """Python 缩进归属扫描：class/def、路由装饰器、SQLAlchemy、pydantic/dataclass 字段。"""
    notes, failed = [], []
    modules = ['python']
    stack = []          # [(kind, name, indent)]
    pending = []        # [{'name','args','line','route'}]
    framework_note_done = False
    source_text = '\n'.join(content.lines)

    for number, raw in enumerate(content.lines, start=1):
        line = _strip_python_strings(raw)
        stripped = line.strip()
        if not stripped:
            continue
        indent = len(line) - len(line.lstrip(' '))
        while stack and stack[-1][2] >= indent:
            stack.pop()

        decorator = _PY_DECORATOR.match(line)
        if decorator:
            name = decorator.group(1)
            args = decorator.group(2) or ''
            entry = {'name': name, 'args': args, 'line': number, 'route': None}
            route = _PY_ROUTE.match(line)
            if route:
                route_object = route.group(1)
                verb = route.group(2).lower()
                decorator_args = route.group(3).rsplit(')', 1)[0] if route.group(3).endswith(')') \
                    else route.group(3)
                positional, named = _annotation_args(decorator_args)
                path = named.get('path') or (positional[0] if positional else '')
                methods = ''
                methods_match = _PY_METHODS_KW.search(decorator_args)
                if methods_match:
                    methods = ','.join(item.strip().strip('\'"').upper()
                                       for item in methods_match.group(1).split(',') if item.strip())
                if not methods and verb != 'route':
                    methods = verb.upper()
                framework = _py_framework(source_text, route_object)
                if verb == 'route' and framework == 'unknown':
                    framework = 'flask'
                if not framework_note_done:
                    notes.append('Python 路由框架判定：%s（依据装饰器对象与文件内 FastAPI/Flask 导入）。'
                                 % framework)
                    framework_note_done = True
                entry['route'] = {'path': path, 'methods': methods,
                                  'framework': framework, 'decorator': '%s.%s' % (route_object, verb),
                                  'routeLine': number, 'routeStyle': verb}
            pending.append(entry)
            continue

        class_match = _PY_CLASS.match(line)
        if class_match:
            name = class_match.group(2)
            bases = [item.strip() for item in (class_match.group(3) or '').split(',') if item.strip()]
            data = {'name': name, 'bases': bases,
                    'decorators': [item['name'] for item in pending],
                    'declaration': raw.strip()}
            if any(item['name'].rsplit('.', 1)[-1] == 'dataclass' for item in pending):
                data['dataclass'] = True
                modules.append('dataclass')
            if any(base.split('.')[-1] in ('BaseModel', 'BaseSettings') for base in bases):
                data['pydantic'] = True
                modules.append('pydantic')
            sink.add('python', {'kind': 'code', 'file': rel, 'line': number, 'symbol': name},
                     textline.snippet_span(content, number, number, 1), 'class', data, 'high')
            stack.append(('class', name, indent))
            pending = []
            continue

        func_match = _PY_FUNC.match(line)
        if func_match:
            name = func_match.group(2)
            params = func_match.group(3)
            owner = stack[-1][1] if stack and stack[-1][0] == 'class' else ''
            route = None
            for item in pending:
                if item['route']:
                    route = dict(item['route'])
            data = {'name': name, 'owner': owner, 'params': params,
                    'decorators': [item['name'] for item in pending],
                    'declaration': raw.strip()}
            if route:
                route['handler'] = name
                data['route'] = route
            symbol = '%s.%s' % (owner, name) if owner else name
            module = 'python'
            if route:
                module = route.get('framework') if route.get('framework') in ('flask', 'fastapi') \
                    else 'flask'
                modules.append(module)
            sink.add(module, {'kind': 'code', 'file': rel, 'line': number, 'symbol': symbol},
                     textline.snippet_span(content, number, number, 1),
                     'route' if route else 'method', data, 'high')
            pending = []
            if not owner and not route:
                stack.append(('func', name, indent))
            continue

        tablename = _PY_TABLENAME.match(line)
        if tablename:
            owner = stack[-1][1] if stack else ''
            sink.add('sqlalchemy', {'kind': 'code', 'file': rel, 'line': number,
                                    'symbol': tablename.group(2)},
                     textline.snippet_span(content, number, number, 1), 'table',
                     {'name': tablename.group(2), 'owner': owner, 'declaration': raw.strip()}, 'high')
            modules.append('sqlalchemy')
            pending = []
            continue

        column = _PY_COLUMN.match(line)
        if column:
            name = column.group(1)
            owner = stack[-1][1] if stack else ''
            data = {'name': name, 'owner': owner, 'declaration': raw.strip(),
                    'type': _column_first_arg(raw)}
            for key in ('primary_key', 'nullable', 'unique', 'index', 'default', 'foreign_keys',
                        'autoincrement', 'comment', 'server_default', 'doc'):
                value = _kwarg(raw, key)
                if value is not None:
                    data[key] = value
            sink.add('sqlalchemy', {'kind': 'code', 'file': rel, 'line': number,
                                    'symbol': '%s.%s' % (owner, name) if owner else name},
                     textline.snippet_span(content, number, number, 1), 'column', data, 'high')
            modules.append('sqlalchemy')
            pending = []
            continue

        relationship = _PY_RELATIONSHIP.match(line)
        if relationship:
            owner = stack[-1][1] if stack else ''
            target = re.search(r'[\'"]([\w\.]+)[\'"]', relationship.group(2))
            sink.add('sqlalchemy', {'kind': 'code', 'file': rel, 'line': number,
                                    'symbol': '%s.%s' % (owner, relationship.group(1)) if owner else relationship.group(1)},
                     textline.snippet_span(content, number, number, 1), 'relationship',
                     {'name': relationship.group(1), 'owner': owner,
                      'target': target.group(1) if target else '',
                      'declaration': raw.strip()}, 'high')
            modules.append('sqlalchemy')
            pending = []
            continue

        if stack and stack[-1][0] == 'class':
            field = _PY_FIELD.match(line)
            if field and not field.group(1).strip():
                name = field.group(2)
                if name not in ('return', 'yield', 'from', 'import', 'class', 'def', 'pass'):
                    owner = stack[-1][1]
                    data = {'name': name, 'owner': owner, 'type': field.group(3).strip(),
                            'declaration': raw.strip()}
                    if field.group(4):
                        data['default'] = field.group(4).strip()
                    sink.add('python', {'kind': 'code', 'file': rel, 'line': number,
                                        'symbol': '%s.%s' % (owner, name)},
                             textline.snippet_span(content, number, number, 1), 'field', data, 'high')
                    pending = []
                    continue

        if pending:
            pending_item = pending[0]
            if len(pending_item['args']) > 60:
                failed.append(failed_segment(
                    'line', {'kind': 'code', 'file': rel, 'line': pending_item['line'], 'symbol': ''},
                    '装饰器 `@%s` 未在后续 1 步内对应到 class/def（可能为多层装饰或动态注册），'
                    '未产出结构化事实。' % pending_item['name'], content.line(pending_item['line'])))
            pending = []

    notes.append('已扫描 %d 行 Python；缩进归属用于类/方法/字段定位。' % len(content.lines))
    notes.append('未解析：动态注册的路由（add_url_rule/运行时导入）、装饰器内计算逻辑、'
                 '字符串拼装 SQL 与 ORM 运行期反射。')
    return modules, notes, failed


def _column_first_arg(raw):
    match = re.search(r'[Cc]olumn\s*\(([^,\)]*)', raw)
    return _unquote(match.group(1).strip()) if match else ''


def _kwarg(raw, key):
    match = re.search(r'\b%s\s*=\s*([^,\)]+)' % re.escape(key), raw)
    return match.group(1).strip() if match else None


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def parse(path, material_id, rel_path=''):
    """按扩展名分派到 Java / XML(MyBatis) / JS-TS-Vue / Python 适配器。"""
    rel = rel_of(path, rel_path)
    name = rel.rsplit('/', 1)[-1].lower()
    extension = name.rsplit('.', 1)[-1] if '.' in name else ''
    try:
        content = textline.read_text_lines(path)
    except (OSError, IOError) as exc:
        return failure('读取文件失败：%s' % exc)

    sink = FactSink(material_id)
    if not content.lines:
        return finish(sink, [extension or 'code'], ['代码文件无可读文本内容（0 行）。'], partial=True)

    notes, failed, modules, partial = [], [], [], False
    try:
        if extension in PY_EXTENSIONS:
            modules, notes, failed = _python_blocks(content, rel, sink)
        elif extension == 'vue':
            notes, failed = _parse_vue(content, rel, sink)
            modules = ['vue', 'vue-sfc']
        elif extension in JS_EXTENSIONS:
            modules = ['typescript' if extension in ('ts', 'tsx') else 'javascript']
            if extension in ('tsx', 'jsx'):
                modules.append('jsx')
            js_modules, notes, failed = _js_lines(content, rel, sink)
            modules += js_modules
        elif extension == 'xml':
            modules, notes, failed, partial = _xml_line_facts(content, rel, sink)
        elif extension == 'java':
            modules, notes, failed = _java_lines(content, rel, sink)
        else:
            modules, notes, failed, partial = _fallback_text(content, rel, sink, extension)
    except RecursionError as exc:
        return failure('解析 %s 时递归过深：%s' % (rel, exc))
    except Exception as exc:  # noqa: BLE001 - 适配器异常必须转成显式失败，不得静默
        return failure('解析代码材料失败：%s: %s' % (exc.__class__.__name__, exc))

    if not sink.facts and not failed:
        return finish(sink, modules, notes + ['未从该代码材料中提取到任何结构化事实。'], partial=True)
    if content.truncated:
        notes.append(textline.truncation_note())
        partial = True
    if content.encoding not in textline.UTF8_ALIASES:
        notes.append('按 %s 编码解码（非 utf-8）。' % content.encoding)
    return finish(sink, modules, notes, failed, partial=partial, language=extension or 'unknown')


def _fallback_text(content, rel, sink, extension):
    """未适配语言/后缀：按行输出文本线索，明确覆盖不足。"""
    for number, raw in enumerate(content.lines, start=1):
        text = raw.strip()
        if not text:
            continue
        sink.add(extension or 'text', {'kind': 'text', 'file': rel, 'line': number, 'symbol': ''},
                 text, 'textLine', {'line': number}, quality='low')
    notes = ['后缀 .%s 未适配专项解析器，按文本线索降级（quality=low），覆盖不足：不识别类/字段/表。'
             % (extension or '(无后缀)')]
    return [extension or 'text'], notes, [], True
