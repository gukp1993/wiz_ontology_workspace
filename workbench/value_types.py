"""Local value constraints with bounded nesting and regex execution."""
import json
import math
import re
import time
from datetime import date, datetime
from .properties import TYPES, effective
from .regex_constraints import check_regex

VALUE_TYPE = 'mg:ValueType'
NUMERIC = {'xsd:double', 'xsd:decimal', 'xsd:integer'}
MAX_DEPTH = 16
MAX_NODES = 10000
MAX_TEXT = 200000
MAX_FIELDS = 200
MAX_ENUM = 1000
MAX_ERRORS = 20
MAX_SECONDS = 2
UUID_PATTERN = re.compile(r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}')
# Palantir's public resource-identifier specification; locator may contain periods.
RID_PATTERN = re.compile(r'ri\.[a-z][a-z0-9-]*\.(?:[a-z0-9][a-z0-9-]*)?\.[a-z][a-z0-9-]*\.[a-zA-Z0-9._-]+')


def scalar(value, base):
    if base in NUMERIC:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError('需要有限数值')
        if base == 'xsd:integer' and int(value) != value:
            raise ValueError('需要整数')
    elif base == 'xsd:boolean':
        if not isinstance(value, bool):
            raise ValueError('需要 true 或 false')
    elif base == 'xsd:array':
        if not isinstance(value, list):
            raise ValueError('需要 JSON 数组，例如 [1, 2, 3]')
    elif base == 'xsd:struct':
        if not isinstance(value, dict):
            raise ValueError('需要 JSON 对象，例如 {"soc": 70}')
    elif base in ('xsd:string', 'xsd:date', 'xsd:dateTime'):
        if not isinstance(value, str):
            raise ValueError('需要文本值')
        if base == 'xsd:date':
            if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
                raise ValueError('日期需要 YYYY-MM-DD 格式')
            return date.fromisoformat(value)
        if base == 'xsd:dateTime':
            stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
            if stamp.tzinfo is None:
                raise ValueError('时间戳必须包含时区，例如 +08:00')
            return stamp
    else:
        raise ValueError('基础类型不支持')
    return value


def _bounded_value(value):
    """Check all JSON data, including unconstrained/unknown nested fields."""
    pending = [(value, 0)]; count = 0; text_size = 0
    while pending:
        item, depth = pending.pop(); count += 1
        if count > MAX_NODES:
            raise ValueError(f'样本最多包含 {MAX_NODES} 个值')
        if depth > MAX_DEPTH:
            raise ValueError(f'样本嵌套最多 {MAX_DEPTH} 层')
        if isinstance(item, str):
            text_size += len(item)
        elif isinstance(item, dict):
            if len(item) + count > MAX_NODES: raise ValueError(f'样本最多包含 {MAX_NODES} 个值')
            if any(not isinstance(key, str) for key in item):
                raise ValueError('JSON 对象字段名必须是文本')
            text_size += sum(len(key) for key in item)
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            if len(item) + count > MAX_NODES: raise ValueError(f'样本最多包含 {MAX_NODES} 个值')
            pending.extend((child, depth + 1) for child in item)
        elif isinstance(item, (int, float)) and not isinstance(item, bool):
            scalar(item, 'xsd:double')
        elif item is not None and not isinstance(item, bool):
            raise ValueError('样本必须是 JSON 支持的值')
        if text_size > MAX_TEXT:
            raise ValueError(f'样本文本总长度最多 {MAX_TEXT} 个字符')


def parse_input(text, base):
    if not isinstance(text, str) or len(text) > MAX_TEXT:
        raise ValueError(f'样本输入必须为文本且最多 {MAX_TEXT} 个字符')
    if base in NUMERIC or base in ('xsd:boolean', 'xsd:array', 'xsd:struct'):
        try:
            value = json.loads(text)
        except (ValueError, TypeError, RecursionError):
            raise ValueError('请输入有效 JSON 数组或对象' if base in ('xsd:array', 'xsd:struct') else '请输入数字或 true / false，不要附加单位') from None
    else:
        value = text
    try:
        _bounded_value(value); scalar(value, base)
    except OverflowError:
        raise ValueError('数值超出支持范围') from None
    return value


def config(node):
    return node.get('mg:constraint', {}).get('@value', {'kind': 'none'})


def _fingerprint(value, case_sensitive=True):
    # JSON structural equality: object ordering is irrelevant; true differs from 1.
    if value is None: return ('null',)
    if isinstance(value, bool): return ('boolean', value)
    if isinstance(value, (int, float)): return ('number', value)
    if isinstance(value, str): return ('string', value if case_sensitive else value.casefold())
    if isinstance(value, list): return ('array', tuple(_fingerprint(item) for item in value))
    return ('struct', tuple(sorted((key, _fingerprint(item)) for key, item in value.items())))


def _enum_values(c, base):
    raw = c.get('values', '')
    if not isinstance(raw, str) or len(raw) > MAX_TEXT:
        raise ValueError('枚举值需逐行填写，且总长度不能超过 200000 个字符')
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    if not lines or len(lines) > MAX_ENUM:
        raise ValueError(f'请填写 1～{MAX_ENUM} 个枚举值')
    return [parse_input(line, base) for line in lines]


def _local_definition(node):
    base = node.get('rdfs:range', {}).get('@id'); c = config(node)
    if not str(node.get('rdfs:label', '')).strip(): raise ValueError('名称不能为空')
    if base not in TYPES: raise ValueError('基础类型不支持')
    if not isinstance(c, dict): raise ValueError('约束定义必须是 JSON 对象')
    kind = c.get('kind', 'none')
    if kind not in ('none', 'range', 'enum', 'regex', 'uuid', 'rid', 'array', 'struct'):
        raise ValueError('约束类型不支持')
    if kind in ('regex', 'uuid', 'rid') and base != 'xsd:string':
        raise ValueError('此格式约束仅适用于文本类型')
    if kind == 'range':
        if base in ('xsd:boolean', 'xsd:array', 'xsd:struct'): raise ValueError('此基础类型不支持范围约束')
        vals = []
        for key in ('min', 'max'):
            value = c.get(key)
            if value is None or value == '': vals.append(None); continue
            value = scalar(value, 'xsd:integer' if base == 'xsd:string' else base)
            if base == 'xsd:string' and value < 0: raise ValueError('文本长度不能小于 0')
            vals.append(value)
        if all(value is None for value in vals): raise ValueError('请至少填写一个范围边界')
        for key in ('minInclusive', 'maxInclusive'):
            if key in c and not isinstance(c[key], bool): raise ValueError('边界包含选项必须为布尔值')
        lo, hi = vals
        if lo is not None and hi is not None and (lo > hi or (lo == hi and not (c.get('minInclusive', True) and c.get('maxInclusive', True)))):
            raise ValueError('范围为空或最小值大于最大值')
    elif kind == 'enum':
        if base in ('xsd:date', 'xsd:dateTime', 'xsd:array', 'xsd:struct'):
            raise ValueError('枚举约束仅适用于文本、数值或布尔类型')
        if not isinstance(c.get('caseSensitive', True), bool): raise ValueError('区分大小写选项必须为布尔值')
        values = _enum_values(c, base)
        if len({_fingerprint(value, c.get('caseSensitive', True)) for value in values}) != len(values):
            raise ValueError('枚举值重复（按当前大小写规则判断）')
    elif kind == 'regex':
        check_regex(c.get('pattern'), c.get('matchMode', 'full'), c.get('caseSensitive', True))
    elif kind == 'array':
        if base != 'xsd:array': raise ValueError('数组约束需要数组基础类型')
        bounds = []
        for key in ('min', 'max'):
            bound = c.get(key)
            if bound is None or bound == '': bounds.append(None); continue
            if type(bound) is not int or bound < 0: raise ValueError('数组长度必须是大于或等于 0 的整数')
            bounds.append(bound)
        if all(bound is not None for bound in bounds) and bounds[0] > bounds[1]: raise ValueError('数组最小长度不能超过最大长度')
        if not isinstance(c.get('unique', False), bool): raise ValueError('元素唯一选项必须为布尔值')
        if not isinstance(c.get('elementValueType', ''), str): raise ValueError('数组元素值类型标识必须是文本')
    elif kind == 'struct':
        if base != 'xsd:struct': raise ValueError('结构体约束需要结构体基础类型')
        fields = c.get('fields', [])
        if not isinstance(fields, list) or len(fields) > MAX_FIELDS: raise ValueError(f'结构体字段必须是数组，且最多 {MAX_FIELDS} 项')
        names = set()
        for field in fields:
            if not isinstance(field, dict): raise ValueError('结构体字段定义必须是对象')
            name = field.get('name')
            if not isinstance(name, str) or not name.strip(): raise ValueError('结构体字段名称不能为空')
            if name in names: raise ValueError(f'结构体字段 {name} 重复')
            names.add(name)
            if not isinstance(field.get('valueType'), str) or not field['valueType']: raise ValueError(f'字段 {name} 需要选择值类型')
            if not isinstance(field.get('required', False), bool): raise ValueError(f'字段 {name} 的必填选项必须为布尔值')


def _references(node):
    c = config(node)
    if c.get('kind') == 'array' and c.get('elementValueType'):
        return [('数组元素', c['elementValueType'])]
    if c.get('kind') == 'struct':
        return [(f"字段 {field['name']}", field['valueType']) for field in c.get('fields', [])]
    return []


def definition_errors(node, graph=None, _deadline=None):
    types = {item['@id']: item for item in (graph or [node]) if item.get('@type') == VALUE_TYPE}
    types[node.get('@id', '')] = node
    errors = []; done = set(); active = set(); count = 0
    deadline = _deadline if _deadline is not None else time.monotonic() + MAX_SECONDS
    def visit(item, depth):
        nonlocal count
        identifier = item.get('@id', ''); label = item.get('rdfs:label') or '未命名值类型'
        if time.monotonic() > deadline:
            errors.append('值类型定义校验超过时间限制，请减少嵌套规则或简化正则'); return
        if identifier in active:
            errors.append(f'{label}：值类型存在循环引用'); return
        if identifier in done: return
        count += 1
        if depth > MAX_DEPTH or count > MAX_NODES:
            errors.append(f'{label}：值类型引用过深或数量超过限制（最多 {MAX_DEPTH} 层）'); return
        try:
            _local_definition(item)
            active.add(identifier)
            for position, ref in _references(item):
                if ref not in types: errors.append(f'{label}：{position}引用不存在的值类型 {ref}')
                else: visit(types[ref], depth + 1)
                if len(errors) >= MAX_ERRORS: break
            active.remove(identifier); done.add(identifier)
        except (ValueError, TypeError, AttributeError, KeyError, OverflowError) as exc:
            errors.append(f'{label}：{exc}')
    visit(node, 0)
    return list(dict.fromkeys(errors))[:MAX_ERRORS]


def check_value(node, value, graph=None):
    errors = definition_errors(node, graph)
    if errors: return errors
    try: _bounded_value(value)
    except (ValueError, TypeError, OverflowError) as exc: return [str(exc)]
    types = {item['@id']: item for item in (graph or [node]) if item.get('@type') == VALUE_TYPE}
    types[node.get('@id', '')] = node
    deadline = time.monotonic() + MAX_SECONDS
    def visit(item, sample, path, depth):
        if len(errors) >= MAX_ERRORS: return
        try:
            if depth > MAX_DEPTH: raise ValueError(f'样本嵌套最多 {MAX_DEPTH} 层')
            if time.monotonic() > deadline: raise ValueError('样本校验超过时间限制，请减少样本大小')
            if sample is None: raise ValueError('缺失值不是有效样本；是否必填由属性要求决定')
            base = item['rdfs:range']['@id']; c = config(item); kind = c.get('kind', 'none')
            actual = scalar(sample, base)
            if kind == 'enum':
                if _fingerprint(sample, c.get('caseSensitive', True)) not in {_fingerprint(v, c.get('caseSensitive', True)) for v in _enum_values(c, base)}:
                    raise ValueError('不在允许的枚举值中')
            elif kind == 'range':
                actual = len(sample) if base == 'xsd:string' else actual
                for key, lower in [('min', True), ('max', False)]:
                    bound = c.get(key)
                    if bound is None or bound == '': continue
                    bound = scalar(bound, 'xsd:integer' if base == 'xsd:string' else base)
                    if (actual < bound if lower else actual > bound) or (actual == bound and not c.get(key + 'Inclusive', True)):
                        raise ValueError('超出允许范围')
            elif kind == 'regex':
                if not check_regex(c['pattern'], c.get('matchMode', 'full'), c.get('caseSensitive', True), sample):
                    raise ValueError('不符合正则表达式格式')
            elif kind == 'uuid' and not UUID_PATTERN.fullmatch(sample):
                raise ValueError('需要标准 UUID 格式，例如 550e8400-e29b-41d4-a716-446655440000')
            elif kind == 'rid' and not RID_PATTERN.fullmatch(sample):
                raise ValueError('需要 RID 格式：ri.<服务>.<实例>.<资源类型>.<定位符>')
            elif kind == 'array':
                if (c.get('min') not in (None, '') and len(sample) < c['min']) or (c.get('max') not in (None, '') and len(sample) > c['max']):
                    raise ValueError('数组长度超出允许范围')
                if c.get('unique') and len({_fingerprint(item) for item in sample}) != len(sample):
                    raise ValueError('数组包含重复元素')
                if c.get('elementValueType'):
                    for index, child in enumerate(sample):
                        visit(types[c['elementValueType']], child, f'{path}[{index}]', depth + 1)
                        if len(errors) >= MAX_ERRORS: break
            elif kind == 'struct':
                for field in c.get('fields', []):
                    field_path = f"{path}.{field['name']}"
                    if field['name'] in sample: visit(types[field['valueType']], sample[field['name']], field_path, depth + 1)
                    elif field.get('required'): errors.append(f'{field_path}：缺少必填字段')
                    if len(errors) >= MAX_ERRORS: break
        except (ValueError, TypeError, OverflowError) as exc:
            errors.append(f'{path}：{exc}' if path else str(exc))
    visit(node, value, '$' if isinstance(value, (list, dict)) else '', 0)
    return errors[:MAX_ERRORS]


def validate_value_types(graph):
    errors = []; types = {node['@id']: node for node in graph if node['@type'] == VALUE_TYPE}
    deadline = time.monotonic() + MAX_SECONDS
    for node in types.values():
        errors.extend(definition_errors(node, graph, deadline))
        if time.monotonic() > deadline: break
    for raw in graph:
        if raw['@type'] not in ('owl:DatatypeProperty', 'mg:SharedProperty'): continue
        node = effective(raw, graph); ref = node.get('mg:valueType', {}).get('@id')
        if not ref: continue
        if ref not in types: errors.append(f"{raw['@id']} 引用不存在的值类型")
        elif types[ref].get('rdfs:range') != node.get('rdfs:range'): errors.append(f"{raw['@id']} 与值类型的基础类型不一致")
    return list(dict.fromkeys(errors))


def check_property_value(prop, value, graph):
    ref = prop.get('mg:valueType', {}).get('@id')
    if not ref or value is None: return []
    node = next((item for item in graph if item['@type'] == VALUE_TYPE and item['@id'] == ref), None)
    return check_value(node, value, graph) if node else ['属性引用的值类型不存在']
