"""计算函数（kind=calculationFunction）的受限制表达式语言。

本模块是公式的**权威**实现：解析、静态类型检查与试算求值都在后端完成；
前端只负责编辑（显示用参数名）与展示结果，不重复实现公式执行。

语法（calc-expression-1）：
- 字面量：数值、'文本'（'' 转义）、true/false；
- 参数引用：``{参数稳定标识}``（编辑器里显示为 ``{参数名称}``，保存前双向转换）；
- 运算：+ - * /（仅数值）、一元负号、比较 = != < <= > >=（结果为是/否）；
- 函数白名单：IF(c,a,b)（惰性，条件为是/否，两分支同型）、MIN/MAX(数值≥2)、
  ABS(n)、ROUND(n[,小数位0—6])（四舍五入）、ERROR('说明')（求值到才报错，可放任意分支）。

硬边界：不执行 SQL/代码/文件/网络；禁止 eval；禁止属性访问、赋值、循环与
白名单之外的函数调用；限制表达式长度、嵌套深度、节点数；NaN/Infinity 拒绝。
纯计算，无 IO，不读项目数据，可在不持全局写锁的探测类路由中使用。
"""
import re
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation

KIND = 'calculationFunction'
LANGUAGE = 'calc-expression-1'
VERSION = 1
TYPES = ('number', 'string', 'boolean')
TYPE_NAMES = {'number': '数值', 'string': '文本', 'boolean': '是/否'}
FUNCTIONS = {'IF': (3, 3), 'MIN': (2, None), 'MAX': (2, None), 'ABS': (1, 1), 'ROUND': (1, 2), 'ERROR': (1, 1)}
MAX_LEN = 500
MAX_DEPTH = 20
MAX_NODES = 200
MAX_INPUTS = 50

_NUM = re.compile(r'\d+(?:\.\d+)?(?:[eE][+-]?\d+)?')
_PARAM = re.compile(r'[A-Za-z0-9_-]+')
_IDENT = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')


class CalcError(Exception):
    pass


# ── 词法 ────────────────────────────────────────────────────────────────────
def _tokenize(expr):
    tokens, i, n = [], 0, len(expr)
    while i < n:
        ch = expr[i]
        if ch in ' \t\r\n':
            i += 1
            continue
        if ch.isdigit():
            m = _NUM.match(expr, i)
            tokens.append(('num', m.group(0)))
            i = m.end()
            continue
        if ch in ('"', "'"):
            j = i + 1
            buf = []
            while j < n:
                if expr[j] == ch:
                    if expr[j + 1:j + 2] == ch:
                        buf.append(ch); j += 2; continue
                    break
                if expr[j] in '\r\n':
                    raise CalcError('文本不能跨行')
                buf.append(expr[j]); j += 1
            if j >= n:
                raise CalcError('文本未闭合')
            tokens.append(('str', ''.join(buf)))
            i = j + 1
            continue
        if ch == '{':
            m = _PARAM.match(expr, i + 1)
            if not m or expr[m.end():m.end() + 1] != '}':
                raise CalcError('参数引用格式无效（应为 {参数标识}）')
            tokens.append(('param', m.group(0)))
            i = m.end() + 1
            continue
        two = expr[i:i + 2]
        if two in ('<=', '>=', '!='):
            tokens.append(('op', two)); i += 2; continue
        if ch in '+-*/()<>=,':
            tokens.append(('op', ch)); i += 1; continue
        ident = _IDENT.match(expr, i)
        if ident:
            tokens.append(('ident', ident.group(0))); i = ident.end(); continue
        raise CalcError(f'不能识别的字符 "{ch}"')
    return tokens


# ── 语法 → AST ──────────────────────────────────────────────────────────────
# 节点：(kind, ...) kind ∈ num/str/bool/param/call/bin/neg
class _Parser:
    def __init__(self, tokens):
        self.tokens = tokens
        self.pos = 0
        self.nodes = 0

    def peek(self):
        return self.tokens[self.pos] if self.pos < len(self.tokens) else (None, None)

    def take(self):
        kind, val = self.peek()
        if kind is None:
            raise CalcError('表达式不完整')
        self.pos += 1
        return kind, val

    def node(self):
        self.nodes += 1
        if self.nodes > MAX_NODES:
            raise CalcError('表达式过于复杂')

    def parse(self):
        node = self.comparison()
        if self.peek()[0] is not None:
            raise CalcError('表达式有多余内容')
        return node

    def comparison(self):
        left = self.additive()
        kind, val = self.peek()
        if kind == 'op' and val in ('=', '!=', '<', '<=', '>', '>='):
            self.take()
            self.node()
            right = self.additive()
            return ('bin', val, left, right)
        return left

    def additive(self):
        node = self.multiplicative()
        while True:
            kind, val = self.peek()
            if kind == 'op' and val in ('+', '-'):
                self.take(); self.node()
                node = ('bin', val, node, self.multiplicative())
            else:
                return node

    def multiplicative(self):
        node = self.unary()
        while True:
            kind, val = self.peek()
            if kind == 'op' and val in ('*', '/'):
                self.take(); self.node()
                node = ('bin', val, node, self.unary())
            else:
                return node

    def unary(self):
        kind, val = self.peek()
        if kind == 'op' and val in ('+', '-'):
            self.take(); self.node()
            inner = self.unary()
            return inner if val == '+' else ('neg', inner)
        return self.primary()

    def primary(self):
        self.node()
        kind, val = self.peek()
        if kind in ('num', 'str'):
            self.take()
            return (kind, val)
        if kind == 'param':
            self.take()
            return ('param', val)
        if kind == 'op' and val == '(':
            self.take()
            inner = self.comparison()
            k2, v2 = self.take()
            if (k2, v2) != ('op', ')'):
                raise CalcError('缺少右括号')
            return inner
        if kind == 'ident':
            self.take()
            name = val.upper()
            if name == 'TRUE':
                return ('bool', True)
            if name == 'FALSE':
                return ('bool', False)
            if name not in FUNCTIONS:
                raise CalcError(f'不支持的函数 "{val}"')
            k, v = self.take()
            if (k, v) != ('op', '('):
                raise CalcError(f'函数 {name} 后应有 "("')
            args = []
            if self.peek() != ('op', ')'):
                args.append(self.comparison())
                while self.peek() == ('op', ','):
                    self.take()
                    args.append(self.comparison())
            k2, v2 = self.take()
            if (k2, v2) != ('op', ')'):
                raise CalcError(f'函数 {name} 缺少右括号')
            lo, hi = FUNCTIONS[name]
            if not (len(args) >= lo and (hi is None or len(args) <= hi)):
                raise CalcError(f'函数 {name} 参数个数应为 {lo}' + (f'—{hi}' if hi else '及以上'))
            return ('call', name, args)
        raise CalcError('不能识别的表达式内容')


# ── 静态类型 ────────────────────────────────────────────────────────────────
def _static(node, param_types, depth=0):
    if depth > MAX_DEPTH:
        raise CalcError('嵌套过深')
    kind = node[0]
    if kind == 'num':
        v = float(node[1])
        if v != v or v in (float('inf'), float('-inf')):
            raise CalcError('数值字面量超出范围')
        return 'number'
    if kind == 'str':
        return 'string'
    if kind == 'bool':
        return 'boolean'
    if kind == 'param':
        pid = node[1]
        if pid not in param_types:
            raise CalcError(f'参数 {pid} 不存在（可能已被删除或改名）')
        return param_types[pid]
    if kind == 'neg':
        if _static(node[1], param_types, depth + 1) != 'number':
            raise CalcError('负号只能用于数值')
        return 'number'
    if kind == 'bin':
        op = node[1]
        lt = _static(node[2], param_types, depth + 1)
        rt = _static(node[3], param_types, depth + 1)
        if op in ('+', '-', '*', '/'):
            if lt != 'number' or rt != 'number':
                raise CalcError(f'运算符 "{op}" 只能用于两个数值（得到 {TYPE_NAMES.get(lt, lt)} 与 {TYPE_NAMES.get(rt, rt)}），文本不自动转数值')
            return 'number'
        if op in ('<', '<=', '>', '>='):
            if lt != 'number' or rt != 'number':
                raise CalcError(f'比较符 "{op}" 只能用于两个数值')
            return 'boolean'
        # = / !=
        if lt != rt:
            raise CalcError('相等比较两边类型必须一致')
        return 'boolean'
    if kind == 'call':
        name, args = node[1], node[2]
        arg_types = [_static(a, param_types, depth + 1) for a in args]
        if name == 'IF':
            if arg_types[0] != 'boolean':
                raise CalcError('IF 的条件必须为是/否')
            a, b = arg_types[1], arg_types[2]
            if a == 'error':
                return b if b != 'error' else 'error'
            if b == 'error':
                return a
            if a != b:
                raise CalcError('IF 两个分支的类型必须一致')
            return a
        if name == 'ERROR':
            if arg_types[0] != 'string':
                raise CalcError('ERROR 的参数应为文本说明')
            return 'error'
        if name in ('MIN', 'MAX', 'ABS', 'ROUND'):
            if any(t != 'number' for t in arg_types):
                raise CalcError(f'{name} 的参数必须为数值')
            if name == 'ROUND' and len(args) == 2:
                second = args[1]
                if second[0] != 'num' or float(second[1]) != int(float(second[1])) or not 0 <= int(float(second[1])) <= 6:
                    raise CalcError('ROUND 的小数位应为 0—6 的整数')
            return 'number'
    raise CalcError('不能识别的表达式节点')


def parse_expression(expr, param_types):
    """解析 + 静态检查。返回 (ast, None) 或 (None, 错误消息)。"""
    if not isinstance(expr, str) or not expr.strip():
        return None, '公式未填写'
    if len(expr) > MAX_LEN:
        return None, f'公式过长（最多 {MAX_LEN} 字符）'
    try:
        parser = _Parser(_tokenize(expr))
        ast = parser.parse()
        out_type = _static(ast, param_types)
        return (ast, out_type), None
    except CalcError as exc:
        return None, str(exc)
    except RecursionError:
        return None, '嵌套过深'


def validate_expression(expr, param_types, output_type):
    """静态校验。返回 None（通过）或错误消息；结果类型须与输出相容（ERROR 分支视为可通过）。"""
    parsed, err = parse_expression(expr, param_types)
    if err:
        return err
    _, out_type = parsed
    if out_type not in ('error', output_type):
        return f'公式结果类型为 {TYPE_NAMES[out_type]}，与输出类型 {TYPE_NAMES[output_type]} 不匹配'
    return None


# ── 求值（IF 惰性：未选中的分支完全不执行） ─────────────────────────────────
class _EvalError(Exception):
    pass


def _to_number(value):
    try:
        f = float(value)
    except (TypeError, ValueError):
        raise _EvalError('数值参数不能转换为数字') from None
    if f != f or f in (float('inf'), float('-inf')):
        raise _EvalError('数值超出范围')
    return f


def _round_half(value, digits):
    try:
        q = Decimal(str(value)).quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        raise _EvalError('舍入失败') from None
    return float(q)


def _eval(node, param_types, values, depth=0):
    if depth > MAX_DEPTH:
        raise _EvalError('嵌套过深')
    kind = node[0]
    if kind == 'num':
        return _to_number(node[1])
    if kind == 'str':
        return node[1]
    if kind == 'bool':
        return node[1]
    if kind == 'param':
        pid = node[1]
        if pid not in param_types:
            raise _EvalError(f'参数 {pid} 不存在')
        if pid not in values or values[pid] is None:
            raise _EvalError(f'参数 {pid} 未提供取值')
        return values[pid]
    if kind == 'neg':
        return -_eval(node[1], param_types, values, depth + 1)
    if kind == 'bin':
        op = node[1]
        left = _eval(node[2], param_types, values, depth + 1)
        right = _eval(node[3], param_types, values, depth + 1)
        if op in ('+', '-', '*', '/'):
            if op == '+':
                result = left + right
            elif op == '-':
                result = left - right
            elif op == '*':
                result = left * right
            else:
                if right == 0:
                    raise _EvalError('除数不能为零')
                result = left / right
            if result != result or result in (float('inf'), float('-inf')):
                raise _EvalError('计算结果超出范围')
            return result
        if op == '=':
            return left == right
        if op == '!=':
            return left != right
        if op == '<':
            return left < right
        if op == '<=':
            return left <= right
        if op == '>':
            return left > right
        return left >= right
    if kind == 'call':
        name, args = node[1], node[2]
        if name == 'IF':
            cond = _eval(args[0], param_types, values, depth + 1)
            if cond is True:
                return _eval(args[1], param_types, values, depth + 1)
            if cond is False:
                return _eval(args[2], param_types, values, depth + 1)
            raise _EvalError('IF 条件结果不是是/否')
        if name == 'ERROR':
            message = _eval(args[0], param_types, values, depth + 1)
            raise _EvalError(str(message))
        nums = [_eval(a, param_types, values, depth + 1) for a in args]
        if name == 'MIN':
            return min(nums)
        if name == 'MAX':
            return max(nums)
        if name == 'ABS':
            return abs(nums[0])
        digits = int(nums[1]) if len(nums) == 2 else 0
        return _round_half(nums[0], digits)
    raise _EvalError('不能识别的节点')


def evaluate(expr, param_types, values, output_type):
    """试算。返回 {'ok': True, 'value': v} 或 {'ok': False, 'error': 消息}。纯计算，无 IO。"""
    parsed, err = parse_expression(expr, param_types)
    if err:
        return {'ok': False, 'error': err}
    ast, static_type = parsed
    try:
        value = _eval(ast, param_types, values or {})
    except _EvalError as exc:
        return {'ok': False, 'error': str(exc)}
    if static_type != 'error' and static_type != output_type:
        return {'ok': False, 'error': '结果类型与输出类型不匹配'}
    if isinstance(value, float) and (value != value or value in (float('inf'), float('-inf'))):
        return {'ok': False, 'error': '计算结果超出范围'}
    return {'ok': True, 'value': value}


# ── 函数记录校验 ────────────────────────────────────────────────────────────
def constant_value_ok(calc_type, value):
    """固定值有效性：明确填写的 0／false／空字符串是有效值；仅缺失或类型不符无效。"""
    if value is None:
        return False
    if calc_type == 'number':
        if isinstance(value, bool):
            return False
        try:
            return float(str(value).strip()) == float(str(value).strip())
        except (ValueError, TypeError):
            return False
    if calc_type == 'boolean':
        return value is True or value is False or value in ('true', 'false')
    return True


def is_calc_function(value):
    return isinstance(value, dict) and value.get('kind') == KIND


def check_function(impl):
    """实现记录结构 + 公式校验。返回中文错误列表（空＝通过）。只读，不修改数据。"""
    errors = []
    if impl.get('schemaVersion') != VERSION:
        errors.append('计算函数版本不受支持，配置已原样保留')
    if not str(impl.get('name', '') or '').strip():
        errors.append('请填写函数名称')
    inputs = impl.get('inputs') if isinstance(impl.get('inputs'), list) else []
    if not inputs:
        errors.append('至少需要一个输入参数')
    ids, names, param_types = set(), set(), {}
    for item in inputs:
        if not isinstance(item, dict):
            errors.append('输入参数结构无效')
            continue
        pid = item.get('id')
        if not isinstance(pid, str) or not pid.strip():
            errors.append('参数标识缺失')
        elif pid in ids:
            errors.append(f'参数标识 {pid} 重复')
        else:
            ids.add(pid)
        name = str(item.get('name', '') or '').strip()
        if not name:
            errors.append('请填写参数名称')
        elif name in names:
            errors.append(f'参数名称「{name}」重复')
        elif '{' in name or '}' in name:
            errors.append('参数名称不能包含大括号')
        else:
            names.add(name)
        if item.get('type') not in TYPES:
            errors.append(f'参数「{name or pid}」数据类型无效')
        elif isinstance(pid, str) and pid.strip():
            param_types[pid] = item.get('type')
    output = impl.get('output') if isinstance(impl.get('output'), dict) else None
    output_type = None
    if not output:
        errors.append('输出定义无效')
    else:
        if not str(output.get('id', '') or '').strip():
            errors.append('输出标识缺失')
        if not str(output.get('name', '') or '').strip():
            errors.append('请填写输出名称')
        if output.get('type') not in TYPES:
            errors.append('输出数据类型无效')
        else:
            output_type = output.get('type')
    impl_obj = impl.get('implementation') if isinstance(impl.get('implementation'), dict) else {}
    if impl_obj.get('language') not in (None, '', LANGUAGE):
        errors.append('计算函数表达式语言不受支持')
    expression = impl_obj.get('expression')
    if output_type is not None:
        err = validate_expression(expression, param_types, output_type)
        if err:
            errors.append('公式' + err if not err.startswith(('公式', '参数', '不支持', '嵌套', '文本')) else err)
    return list(dict.fromkeys(errors))
