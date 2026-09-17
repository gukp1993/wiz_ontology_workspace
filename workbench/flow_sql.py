"""函数编排 SQL 节点的 MyBatis 风格动态 SQL 子集（flow-sql-1）。

解析、参数扫描与渲染都是**纯函数**，无 IO、不连接数据库；供 flows.check_flow
（静态校验）与 flow_executor（渲染执行）共用。边界：
* 只支持白名单标签：<if test> <choose>/<when test>/<otherwise> <where> <set>
  <trim prefix prefixOverrides suffixOverrides> <foreach collection item index
  open close separator>；其余 `<xxx>` 一律按普通文本（与 `a <= 5` 等比较符不冲突）。
* 参数占位：#{技术名}（预编译）、:技术名（一期旧写法，等价 #{}）、${技术名}
  （原文拼接，校验层负责提示注入风险）。foreach 循环体里 #{循环变量} 取元素值、
  #{index 变量} 取下标。
* test 表达式子集：参数与 null/字符串/数值/布尔字面量的比较（== != < <= > >=）、
  and/or/not、括号；不追求完整 OGNL。
"""
import re
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation


class FlowSqlError(ValueError):
    pass


TAG_NAMES = ('if', 'choose', 'when', 'otherwise', 'where', 'set', 'trim', 'foreach')
_MAX_LEN = 50000
_MAX_DEPTH = 8

# 标签边界：`<` + 可选 `/` + 白名单名 + 边界（空白/>//），避免把 `<=`、`< 5` 当标签
_TAG_RE = re.compile(r'<(/?)(' + '|'.join(TAG_NAMES) + r')(?=[\s/>])')
_ATTR_RE = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)\s*=\s*"([^"]*)"')
_PARAM_RE = re.compile(r'(?<![A-Za-z0-9_])(?:(#)|(\$))\{([A-Za-z_][A-Za-z0-9_]*)\}')
# 一期旧写法 :name → #{name}（等价预编译）；字符串字面量先行掩码，避免 '12:30:00'、'a:b' 误伤
_STR_LIT_RE = re.compile(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"")
_LEGACY_RE = re.compile(r'(?<![A-Za-z0-9_]):([A-Za-z_][A-Za-z0-9_]*)')
_NUM_RE = re.compile(r'\d+(?:\.\d+)?')
_NAME_RE = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')


def _legacy_params(template):
    """把字符串字面量之外的 :name 统一改写为 #{name}（渲染与扫描共用）。"""
    parts, last = [], 0
    for match in _STR_LIT_RE.finditer(template):
        parts.append(_LEGACY_RE.sub(lambda m: '#{' + m.group(1) + '}', template[last:match.start()]))
        parts.append(match.group(0))
        last = match.end()
    parts.append(_LEGACY_RE.sub(lambda m: '#{' + m.group(1) + '}', template[last:]))
    return ''.join(parts)


# ── 模板解析：节点 = ('text', s) | (tag, attrs, children) ─────────────────────
def _find_tag_end(template, start):
    """开放标签的 > 位置：跳过属性引号内的内容（test="a > 10" 里的 > 不结束标签）。"""
    quote = None
    for i in range(start, len(template)):
        ch = template[i]
        if quote:
            if ch == quote:
                quote = None
        elif ch in ('"', "'"):
            quote = ch
        elif ch == '>':
            return i
    return -1


def parse_template(template):
    if not isinstance(template, str) or not template.strip():
        raise FlowSqlError('SQL 模板为空')
    if len(template) > _MAX_LEN:
        raise FlowSqlError('SQL 模板过长')
    root, stack = [], []  # stack: [tag, attrs, children]
    pos = 0

    def append(node):
        (stack[-1][2] if stack else root).append(node)

    while pos < len(template):
        match = _TAG_RE.search(template, pos)
        if match is None:
            append(('text', template[pos:]))
            break
        if match.start() > pos:
            append(('text', template[pos:match.start()]))
        closing, name = match.group(1) == '/', match.group(2)
        attr_end = _find_tag_end(template, match.end())
        if attr_end < 0:
            raise FlowSqlError(f'<{name}> 标签未闭合（缺少 >）')
        attrs = dict(_ATTR_RE.findall(template[match.end():attr_end]))
        if closing:
            if not stack or stack[-1][0] != name:
                raise FlowSqlError(f'</{name}> 没有对应的开始标签')
            append_done = stack.pop()
            append(append_done)
            pos = attr_end + 1
            continue
        if template[attr_end - 1:attr_end] == '/':  # 自闭合：不允许，避免歧义
            raise FlowSqlError(f'<{name}/> 不支持自闭合写法，请写成 <{name}>…</{name}>')
        node = [name, attrs, []]
        stack.append(node)
        pos = attr_end + 1
        if len(stack) > _MAX_DEPTH:
            raise FlowSqlError('动态标签嵌套过深')
    if stack:
        raise FlowSqlError(f'<{stack[-1][0]}> 标签未闭合')
    return root


def _walk(nodes):
    for node in nodes:
        yield node
        if node[0] != 'text':
            yield from _walk(node[2])


# ── test 表达式（递归下降）：or → and → not → 比较 → 操作数 ───────────────────
_OPS = ('==', '!=', '<=', '>=', '<', '>')

def _tokenize_test(expr):
    tokens, i, n = [], 0, len(expr)
    while i < n:
        ch = expr[i]
        if ch in ' \t\r\n':
            i += 1
            continue
        if expr[i:i + 2] in _OPS:
            tokens.append(('op', expr[i:i + 2])); i += 2; continue
        if ch == '!':
            tokens.append(('not', None)); i += 1; continue
        if ch in '<>':
            tokens.append(('op', ch)); i += 1; continue
        if ch in '()=,':
            tokens.append(('punct', ch)); i += 1; continue
        if expr[i:i + 3] == 'and':
            tokens.append(('and', None)); i += 3; continue
        if expr[i:i + 2] == 'or':
            tokens.append(('or', None)); i += 2; continue
        if expr[i:i + 3] == 'not':
            tokens.append(('not', None)); i += 3; continue
        if ch in ('"', "'"):
            j = i + 1
            while j < n and expr[j] != ch:
                j += 1
            if j >= n:
                raise FlowSqlError('test 表达式的字符串未闭合')
            tokens.append(('str', expr[i + 1:j])); i = j + 1; continue
        num = _NUM_RE.match(expr, i)
        if num:
            tokens.append(('num', num.group(0))); i = num.end(); continue
        name = _NAME_RE.match(expr, i)
        if name:
            word = name.group(0)
            tokens.append(('null', None) if word == 'null' else
                          ('bool', word == 'true') if word in ('true', 'false') else
                          ('name', word))
            i = name.end(); continue
        raise FlowSqlError(f'test 表达式不能识别的字符 "{ch}"')
    return tokens


class _TestParser:
    """test AST：('cmp', op, left, right) | ('null?', x) | ('and'/'or', a, b) |
    ('not', x) | ('lit', v) | ('param', name)。比较左右退化成单操作数时按真值判断。"""

    def __init__(self, tokens):
        self.tokens, self.pos = tokens, 0

    def peek(self):
        return self.tokens[self.pos] if self.pos < len(self.tokens) else (None, None)

    def take(self):
        kind, val = self.peek()
        self.pos += 1
        return kind, val

    def parse(self):
        node = self.or_expr()
        if self.peek()[0] is not None:
            raise FlowSqlError('test 表达式有多余内容')
        return node

    def or_expr(self):
        node = self.and_expr()
        while self.peek()[0] == 'or':
            self.take()
            node = ('or', node, self.and_expr())
        return node

    def and_expr(self):
        node = self.not_expr()
        while self.peek()[0] == 'and':
            self.take()
            node = ('and', node, self.not_expr())
        return node

    def not_expr(self):
        if self.peek()[0] == 'not':
            self.take()
            return ('not', self.not_expr())
        return self.primary()

    def primary(self):
        kind, val = self.peek()
        if kind == 'punct' and val == '(':
            self.take()
            node = self.or_expr()
            if self.peek() != ('punct', ')'):
                raise FlowSqlError('test 表达式括号未闭合')
            self.take()
            return node
        left = self.operand()
        kind, val = self.peek()
        if kind == 'op':
            self.take()
            return ('cmp', val, left, self.operand())
        if left[0] == 'param':
            return ('null?', left)
        if left[0] == 'lit':
            return ('truth', left)
        raise FlowSqlError('test 表达式缺少比较运算符')

    def operand(self):
        kind, val = self.take()
        if kind == 'name':
            return ('param', val)
        if kind == 'null':
            return ('lit', None)
        if kind == 'bool':
            return ('lit', val)
        if kind == 'num':
            return ('lit', float(val) if '.' in val else int(val))
        if kind == 'str':
            return ('lit', val)
        raise FlowSqlError('test 表达式的操作数无效')


def parse_test(expr):
    return _TestParser(_tokenize_test(expr or '')).parse()


def test_params(node, out=None):
    """收集 test 表达式引用的参数名。"""
    out = set() if out is None else out
    kind = node[0]
    if kind == 'param':
        out.add(node[1])
    elif kind in ('cmp', 'null?'):
        for sub in node[2:]:
            if isinstance(sub, tuple):
                test_params(sub, out)
    elif kind in ('and', 'or'):
        test_params(node[1], out); test_params(node[2], out)
    elif kind == 'not':
        test_params(node[1], out)
    return out


def eval_test(node, params):
    kind = node[0]
    if kind == 'lit':
        return node[1]
    if kind == 'param':
        return params.get(node[1])
    if kind == 'null?':
        return eval_test(node[1], params) is not None
    if kind == 'truth':
        return bool(eval_test(node[1], params))
    if kind == 'not':
        return not bool(eval_test(node[1], params))
    if kind == 'and':
        return bool(eval_test(node[1], params)) and bool(eval_test(node[2], params))
    if kind == 'or':
        return bool(eval_test(node[1], params)) or bool(eval_test(node[2], params))
    if kind == 'cmp':
        left, right = eval_test(node[2], params), eval_test(node[3], params)
        op = node[1]
        try:
            if op == '==': return left == right
            if op == '!=': return left != right
            if left is None or right is None:
                raise FlowSqlError('test 比较的参数为 null（请先判断 != null）')
            if op == '<': return left < right
            if op == '<=': return left <= right
            if op == '>': return left > right
            return left >= right
        except TypeError:
            raise FlowSqlError('test 比较的两侧类型不可比较') from None
    raise FlowSqlError('test 表达式节点无效')


# ── 参数扫描（静态校验用） ───────────────────────────────────────────────────
def scan_template(template):
    """返回 {'named': [#/{}/:/${} 参数名去重保序], 'tests': [test 参数], 'collections': [foreach 集合名]}。
    循环变量不出现在 named（渲染时局部绑定）。"""
    nodes = parse_template(_legacy_params(template))
    named, collections = [], []

    def walk(children, loop_vars):
        for node in children:
            if node[0] == 'text':
                for match in _PARAM_RE.finditer(node[1]):
                    name = match.group(3)
                    if name not in loop_vars and name not in named:
                        named.append(name)
            elif node[0] == 'foreach':
                col = node[1].get('collection', '')
                if col and col not in collections:
                    collections.append(col)
                inner = set(loop_vars)
                for var in (node[1].get('item'), node[1].get('index')):
                    if var:
                        inner.add(var)
                walk(node[2], inner)
            elif node[0] in ('if', 'when') and node[1].get('test'):
                for name in sorted(test_params(parse_test(node[1]['test']))):
                    if name not in named:
                        named.append(name)
                walk(node[2], loop_vars)
            else:
                walk(node[2], loop_vars)

    walk(nodes, set())
    return {'named': named, 'collections': collections}


# ── 渲染：产出 (sql, args)；args 为 PyMySQL %s 预编译参数 ─────────────────────
def _num(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FlowSqlError('数字占位需要数值参数')
    return value


def _render_expr(text, params, loops, args):
    """把文本里的 #{} ${} 占位替换成 %s 或原文；循环变量优先于入参。"""
    def repl(match):
        name = match.group(3)
        kind = '#' if match.group(1) else '$'
        if name in loops:
            value = loops[name]
        elif name in params:
            value = params[name]
        else:
            raise FlowSqlError(f'占位符引用了未提供的参数 {name}')
        if kind == '$':
            return value if isinstance(value, str) and not isinstance(value, bool) else _inline(value)
        args.append(value)
        return '%s'

    return _PARAM_RE.sub(repl, text)


def _inline(value):
    if value is None:
        return 'null'
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return value.replace("'", "''")
    import json
    return json.dumps(value, ensure_ascii=False, default=str)


def _render_body(nodes, params, loops, args):
    parts = []
    for node in nodes:
        if node[0] == 'text':
            parts.append(_render_expr(node[1], params, loops, args))
            continue
        parts.append(_render_tag(node, params, loops, args))
    return ''.join(parts)


def _strip_prefixes(body, overrides):
    items = [x.strip() for x in overrides.split(',') if x.strip()] if overrides else []
    result = body.lstrip()
    changed = True
    while changed and items:
        changed = False
        for prefix in items:
            if result[:len(prefix)].upper() == prefix.upper():
                result = result[len(prefix):].lstrip()
                changed = True
                break
    return result


def _render_tag(node, params, loops, args):
    tag, attrs, children = node
    if tag in ('if', 'when'):
        if not attrs.get('test'):
            raise FlowSqlError(f'<{tag}> 缺少 test 表达式')
        return _render_body(children, params, loops, args) if eval_test(parse_test(attrs['test']), params) else ''
    if tag == 'choose':
        for child in children:
            if child[0] == 'when':
                if not child[1].get('test'):
                    raise FlowSqlError('<when> 缺少 test 表达式')
                if eval_test(parse_test(child[1]['test']), params):
                    return _render_body(child[2], params, loops, args)
            elif child[0] == 'otherwise':
                return _render_body(child[2], params, loops, args)
            elif child[0] != 'text' or child[1].strip():
                raise FlowSqlError('<choose> 内只允许 <when>/<otherwise>')
        return ''
    if tag == 'where':
        body = _strip_prefixes(_render_body(children, params, loops, args), 'AND,OR').strip()
        return ('WHERE ' + body) if body else ''
    if tag == 'set':
        body = _render_body(children, params, loops, args).strip().rstrip(',').strip()
        return ('SET ' + body) if body else ''
    if tag == 'trim':
        body = _strip_prefixes(_render_body(children, params, loops, args),
                               attrs.get('prefixOverrides', '')).strip()
        if attrs.get('suffixOverrides'):
            body = body.rstrip().rstrip(attrs['suffixOverrides'].strip())
        body = body.strip()
        return (attrs.get('prefix', '') + body + attrs.get('suffix', '')) if body else ''
    if tag == 'foreach':
        collection = attrs.get('collection', '')
        item, index = attrs.get('item', ''), attrs.get('index', '')
        if not collection:
            raise FlowSqlError('<foreach> 缺少 collection')
        if not item and not index:
            raise FlowSqlError('<foreach> 缺少 item 或 index 循环变量')
        if collection in loops:
            values = loops[collection]
        elif collection in params:
            values = params[collection]
        else:
            raise FlowSqlError(f'foreach 引用了未提供的集合 {collection}')
        if not isinstance(values, list):
            raise FlowSqlError(f'foreach 的集合 {collection} 不是列表')
        rendered = []
        for position, element in enumerate(values):
            inner = dict(loops)
            if item:
                inner[item] = element
            if index:
                inner[index] = position
            inner_args = []
            rendered.append(_render_body(children, params, inner, inner_args).strip())
            args.extend(inner_args)
        body = (attrs.get('separator') or ', ').join(x for x in rendered if x)
        return attrs.get('open', '') + body + attrs.get('close', '')
    raise FlowSqlError(f'不支持的标签 <{tag}>')


def render(template, params):
    """渲染模板。params: 技术名 → 值；返回 (sql, args)。未知占位/无效 test 抛 FlowSqlError。"""
    params = dict(params or {})
    args = []
    sql = _render_body(parse_template(_legacy_params(template)), params, {}, args)
    return ' '.join(sql.split()), args


def render_preview(template, params):
    """校验/日志用渲染预览：占位替换为 ?，不要求参数齐全（缺参当 None）。"""
    try:
        sql, args = render(template, params or {})
        return sql, len(args)
    except FlowSqlError:
        return '', 0
