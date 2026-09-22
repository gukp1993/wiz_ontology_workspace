"""TOML 专用解析：自写最小子集解析器（Python 3.9 无 stdlib tomllib，禁止 pip install）。

能力边界（支持 / 降级 / 不执行）
--------------------------------
* 支持（`TOML 子集`，逐行 + 内联字面量解析，**不引入任何依赖**）：
  - 顶层键值、`[table]`、`[table.sub]` 表头（深表可嵌套，路径按 `$.a.b.c` 记入键路径事实）；
  - 值类型：基本字符串 `"…"`、字面字符串 `'…'`、整数（含 `+/-`、下划线分隔、`0x`/`0o`/`0b` 前缀）、
    浮点（含指数与下划线）、布尔 `true/false`、数组 `[…, …]`（可跨行、可嵌套）、内联表 `{a=1, b="x"}`；
  - 注释 `#`（整行与行尾，字符串内不误判，行尾注释在值解析前剥离）；
  - 空白与空行、表头前后的空行。
* 诚实降级（需求 §2 规定的四条不覆盖语法 → **逐条进 failedSegments**，quality='medium'）：
  1. 多行字符串 `\"\"\"…\"\"\"` 与 `'''…'''`；
  2. 日期时间字面量（`1979-05-27`、`07:32:00`、带 `T`/空格分隔的完整时间戳）；
  3. `[[数组表]]`（array of tables）；
  4. 虚键（dotted keys，如 `a.b.c = 1`）。
  另外：数组/内联表里出现上述不覆盖值、未闭合引号、括号不配对、重复键、无 `=` 的行
  也逐条记录。命中的行**不产出事实**（不猜测内容），其余行照常解析。
* 失败边界：读取失败/超字节上限 → 显式 failure（零事实），不回退兜底。
* 不执行：不执行材料中的任何内容、不访问外链、不做类型强制转换之外的加工
  （整数/浮点保持数值类型，字符串保持原文）。

定位器：`{'kind':'toml','file':rel,'path':'$.a.b[0]','line':行号}`（行号来自源文件真实行）。
事实 quality：可解析子集为 'high'，命中不覆盖语法而整体判为 partial 时降为 'medium'（诚实标注）。
"""

import re

from workbench.ontology_build.parsers import structwalk
from workbench.ontology_build.parsers import textline
from workbench.ontology_build.parsers.base import failure
from workbench.ontology_build.parsers.textline import FactSink, failed_segment, finish, rel_of

SUBSET_QUALITY = 'medium'        # 需求 §2：TOML 子集解析 quality=medium（注明）
TABLE_QUALITY = 'medium'
SNIPPET_CHARS = 400
MAX_LINE_CHARS = 8000

_BARE_KEY = re.compile(r'^[A-Za-z0-9_\-]+$')
_TABLE_HEADER = re.compile(r'^\[([^\[\]]+)\]\s*(?:#.*)?$')
_ARRAY_TABLE_HEADER = re.compile(r'^\[\[(.+)\]\]\s*(?:#.*)?$')
_KEY_VALUE = re.compile(r'^([^=]+)=(.*)$')
_DOTTED_KEY = re.compile(r'^(?:"[^"]*"|\'[^\']*\'|[A-Za-z0-9_\-]+)\.')
_DATETIME = re.compile(
    r'^\d{4}-\d{2}-\d{2}(?:[Tt ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[Zz]|[+-]\d{2}:\d{2})?)?$'
    r'|^\d{2}:\d{2}:\d{2}(?:\.\d+)?$')
_INT = re.compile(r'^[+-]?(?:0|[1-9][\d_]*|0[xX][0-9A-Fa-f_]+|0[oO][0-7_]+|0[bB][01_]+)$')
_FLOAT = re.compile(r'^[+-]?(?:\d[\d_]*)?\.\d[\d_]*(?:[eE][+-]?\d+)?$'
                    r'|^[+-]?\d[\d_]*[eE][+-]?\d+$'
                    r'|^[+-]?(?:inf|nan)$')
_STRING_ESCAPES = {'b': '\b', 't': '\t', 'n': '\n', 'f': '\f', 'r': '\r', '"': '"',
                   '\\': '\\'}


class TomlSubsetError(Exception):
    """子集解析错误：携带行号、原因与命中语法名（用于 failedSegments）。"""

    def __init__(self, line, reason, syntax=''):
        super().__init__(reason)
        self.line = int(line or 0)
        self.reason = str(reason or '')
        self.syntax = str(syntax or '')


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def parse(path, material_id, rel_path=''):
    """解析 TOML 子集：可解析部分产出键路径事实，不覆盖语法逐条进 failedSegments。"""
    rel = rel_of(path, rel_path)
    try:
        text, encoding, size = structwalk.read_document_text(path)
    except (OSError, IOError) as exc:
        return failure('读取文件失败：%s' % exc)
    except ValueError as exc:
        return failure(str(exc))

    tree, roots, failed = _build_tree(str(text or ''), rel)
    sink = FactSink(material_id)
    if not tree and failed:
        # 全部内容都命中不覆盖语法/语法错误：显式失败、零事实（不产生空树事实假装成功）
        reason = 'TOML 子集解析未产出任何事实：全部内容命中不覆盖语法或语法错误。'
        return failure(reason, coverage={'modules': ['toml'], 'notes': [
            'TOML 子集解析（自写实现，不引入依赖）：支持 [table]/[table.sub]、键=字符串/整数/'
            '浮点/布尔/数组/内联表与注释。',
            reason, '修复后单物料重试；不回退 LLM 兜底或文本线索（需求 §2）。'],
            'failedSegments': failed})
    stats = structwalk.walk_tree(tree, sink, rel, locator_kind='toml', module='toml',
                                 quality=SUBSET_QUALITY,
                                 line_lookup=lambda path: _line_for(path, roots))
    notes = list(stats.get('notes') or [])
    notes.insert(0, 'TOML 子集解析（自写实现，不引入依赖）：支持 [table]/[table.sub]、键=字符串/整数/'
                    '浮点/布尔/数组/内联表与注释；事实 quality=%s（子集解析，非全语法）。'
                 % SUBSET_QUALITY)
    if failed:
        notes.append('命中 %d 处不覆盖语法/语法错误（多行字符串、日期时间、[[数组表]]、虚键、'
                     '未闭合引号、重复键等），已逐条记入 failedSegments、对应行不产出事实。'
                     % len(failed))
    notes.append('定位器使用 kind=toml + 键路径 + 源文件真实行号（表头行号用于该表下直接键）。')
    notes.append('未执行：不引入 tomllib/第三方库、不执行材料内容、不访问外链。')
    if encoding not in textline.UTF8_ALIASES:
        notes.append('按 %s 编码解码（非 utf-8）。' % encoding)
    partial = bool(failed or stats.get('stopped'))
    if not sink.facts:
        if failed:
            reason = 'TOML 子集解析未产出任何事实：全部内容命中不覆盖语法或语法错误。'
            return failure(reason, coverage={'modules': ['toml'], 'notes': notes + [reason],
                                             'failedSegments': failed})
        return finish(sink, ['toml'], notes + ['TOML 文件没有可登记的键值。'], partial=True)
    return finish(sink, ['toml'], notes, failed, partial=partial,
                  unsupportedSyntax=sorted({item.get('syntax') for item in failed if item.get('syntax')}))


# ---------------------------------------------------------------------------
# 子集解析：行扫描 → 树
# ---------------------------------------------------------------------------
def _build_tree(text, rel):
    """逐行把 TOML 子集解析成树。

    返回 `(tree, roots, failed)`：`roots` 是 `键路径 → 行号` 映射（供定位器取真实行号），
    `failed` 是 failedSegments 条目列表。
    """
    tree = {}
    roots = {}
    failed = []
    current = []                     # 当前表路径（键段列表），空表示顶层
    skip_until_header = None         # 非 None 表示位于未解析的 [[数组表]] 内（值为表名）
    lines = text.splitlines()
    index = 0

    def declare(path, line, value):
        """把一个值写入树；重复键记录 failed 并保留首个值（不静默覆盖）。"""
        node = tree
        for segment in path[:-1]:
            existing = node.get(segment)
            if not isinstance(existing, dict):
                existing = {}
                node[segment] = existing
            node = existing
        key = path[-1]
        if key in node:
            failed.append(failed_segment(
                'statement', _locator(rel, _path_text(path), line),
                '重复键（该键已在第 %d 行定义）：保留首个值，未覆盖' % roots.get(_path_text(path), 0),
                '%s = %s' % (key, value)))
            return
        node[key] = value
        _register_value_paths(_path_text(path), value, line, roots)

    while index < len(lines):
        number = index + 1
        raw = lines[index]
        line = raw[:MAX_LINE_CHARS]
        stripped = line.strip()
        index += 1
        if not stripped or stripped.startswith('#'):
            continue

        array_header = _ARRAY_TABLE_HEADER.match(stripped)
        if array_header:
            table_name = array_header.group(1).strip()
            failed.append(failed_segment(
                'statement', _locator(rel, '$.' + table_name, number),
                '不覆盖语法：[[数组表]]（array of tables）未解析；该表内的键值同样未解析，'
                '逐条记入 failedSegments', stripped[:120]))
            _attach_syntax(failed[-1], 'arrayOfTables')
            current = []
            skip_until_header = table_name
            continue
        header = _TABLE_HEADER.match(stripped)
        if header:
            name = header.group(1).strip()
            segments, header_failed = _table_path(name, number, rel)
            for item in header_failed:
                failed.append(item)
            if header_failed:
                continue
            current = segments
            skip_until_header = None
            _declare_table(tree, segments)
            continue

        if stripped.startswith('"""') or stripped.startswith("'''"):
            failed.append(failed_segment(
                'statement', _locator(rel, _path_text(current), number),
                '不覆盖语法：多行字符串（%s…%s）未解析，该行未产出事实'
                % (stripped[:3], stripped[:3]), stripped[:120]))
            _attach_syntax(failed[-1], 'multilineString')
            index = _skip_multiline_string(lines, stripped[:3], index)
            continue
        match = _KEY_VALUE.match(stripped)
        if not match:
            failed.append(failed_segment(
                'statement', _locator(rel, _path_text(current), number),
                '不是 `键 = 值` 形式：未产出事实', stripped[:120]))
            continue
        key_text = match.group(1).strip()
        value_text = _strip_comment(match.group(2).strip())
        if _DOTTED_KEY.match(key_text) and not key_text.startswith(('"', "'")):
            # 虚键判定先于「数组表内跳过」：行自身的语法问题优先如实归因
            failed.append(failed_segment(
                'statement', _locator(rel, '$.' + key_text.replace(' ', ''), number),
                '不覆盖语法：虚键（dotted keys，`a.b.c = …`）未解析，该行未产出事实',
                stripped[:120]))
            _attach_syntax(failed[-1], 'dottedKey')
            continue
        if skip_until_header is not None:
            # 位于未解析的 [[数组表]] 内：该行不属于已解析子集，逐条记录且不产出事实，
            # 避免把它挂到错误的父路径上（诚实降级而不是猜结构）
            failed.append(failed_segment(
                'statement', _locator(rel, _path_text(current), number),
                '位于未解析的 [[%s]] 数组表内：未产出事实' % skip_until_header, stripped[:120]))
            _attach_syntax(failed[-1], 'arrayOfTables')
            continue
        key, key_failed = _parse_key(key_text, number, rel)
        failed.extend(key_failed)
        if key_failed:
            continue
        if value_text.startswith('"""') or value_text.startswith("'''"):
            failed.append(failed_segment(
                'statement', _locator(rel, _path_text(current), number),
                '不覆盖语法：多行字符串（%s…%s）未解析，该键值未产出事实'
                % (value_text[:3], value_text[:3]), stripped[:120]))
            _attach_syntax(failed[-1], 'multilineString')
            index = _skip_multiline_string(lines, value_text[:3], index)
            continue
        # 跨行数组/内联表：把后续行并入（括号/引号配平前继续读），行号仍取起始行
        while _needs_more(value_text) and index < len(lines):
            value_text = value_text + ' ' + _strip_comment(lines[index].strip())
            index += 1
        try:
            value = _parse_value(value_text, number, rel)
        except TomlSubsetError as exc:
            reason = exc.reason
            if index > number:
                reason += '（该键值并入至第 %d 行；范围内 %d 行未产出事实）' % (index, index - number)
            failed.append(failed_segment(
                'statement', _locator(rel, _path_text(current), exc.line or number),
                reason, stripped[:120]))
            _attach_syntax(failed[-1], exc.syntax)
            continue
        declare(list(current) + [key], number, value)
    return tree, roots, failed


def _declare_table(tree, segments):
    """确保表路径存在（重复表头不覆盖已有内容；表头本身不产出事实）。"""
    node = tree
    for segment in segments:
        existing = node.get(segment)
        if not isinstance(existing, dict):
            existing = {}
            node[segment] = existing
        node = existing
    return node


def _table_path(name, line, rel):
    """解析表头名（支持 `a.b` 与引号段）；非法表头返回 failedSegments。"""
    segments, failed = [], []
    for piece in name.split('.'):
        token = piece.strip()
        if not token:
            failed.append(failed_segment(
                'statement', _locator(rel, '$', line),
                '表头存在空段（`%s`）：该表头未解析' % name, name[:120]))
            return [], failed
        if token[0] in ('"', "'"):
            if len(token) < 2 or token[-1] != token[0]:
                failed.append(failed_segment(
                    'statement', _locator(rel, '$', line),
                    '表头引号未闭合（`%s`）：该表头未解析' % name, name[:120]))
                return [], failed
            segments.append(token[1:-1])
        elif _BARE_KEY.match(token):
            segments.append(token)
        else:
            failed.append(failed_segment(
                'statement', _locator(rel, '$', line),
                '表头包含非法字符（`%s`）：该表头未解析' % name, name[:120]))
            return [], failed
    return segments, failed


def _parse_key(text, line, rel):
    """解析键：裸键或引号键；返回 `(键, failed 列表)`。"""
    failed = []
    if text.startswith(('"', "'")):
        try:
            value, position = _parse_string(text, 0, line)
        except TomlSubsetError as exc:
            return '', [failed_segment('statement', _locator(rel, '$', line), exc.reason,
                                       text[:120])]
        if text[position:].strip():
            failed.append(failed_segment(
                'statement', _locator(rel, '$', line),
                '引号键之后存在多余内容（%s）：该行未产出事实' % text[position:].strip()[:60],
                text[:120]))
            return '', failed
        return value, failed
    if _BARE_KEY.match(text):
        return text, failed
    failed.append(failed_segment(
        'statement', _locator(rel, '$', line),
        '键不是裸键或引号键（`%s`）：该行未产出事实' % text[:60], text[:120]))
    return '', failed


def _parse_value(text, line, rel):
    """解析子集值：字符串 / 整数 / 浮点 / 布尔 / 数组 / 内联表。"""
    value = text.strip()
    if not value:
        raise TomlSubsetError(line, '缺少值（`键 =` 后为空）：该行未产出事实')
    if value.startswith(('"""', "'''")):
        raise TomlSubsetError(line, '不覆盖语法：多行字符串未解析，该行未产出事实',
                              syntax='multilineString')
    if value[0] in ('"', "'"):
        parsed, position = _parse_string(value, 0, line)
        if value[position:].strip():
            raise TomlSubsetError(line, '字符串之后存在多余内容（%s）：该行未产出事实'
                                  % value[position:].strip()[:60])
        return parsed
    if value.startswith('['):
        return _parse_array(value, line, rel)
    if value.startswith('{'):
        return _parse_inline_table(value, line, rel)
    lowered = value.lower()
    if lowered in ('true', 'false'):
        return lowered == 'true'
    if _DATETIME.match(value):
        raise TomlSubsetError(line, '不覆盖语法：日期时间字面量（`%s`）未解析，该行未产出事实'
                              % value[:40], syntax='datetime')
    if _INT.match(value):
        return _parse_int(value, line)
    if _FLOAT.match(value):
        return _parse_float(value, line)
    raise TomlSubsetError(line, '无法识别的值（`%s`）：未产出事实' % value[:60])


def _parse_int(value, line):
    body = value.replace('_', '')
    sign = -1 if body.startswith('-') else 1
    body = body.lstrip('+-')
    try:
        if body[:2].lower() in ('0x', '0o', '0b'):
            return sign * abs(int(body, 0))
        return sign * int(body)
    except ValueError:
        raise TomlSubsetError(line, '整数格式非法（`%s`）：未产出事实' % value[:40]) from None


def _parse_float(value, line):
    try:
        return float(value.replace('_', ''))
    except ValueError:
        raise TomlSubsetError(line, '浮点格式非法（`%s`）：未产出事实' % value[:40]) from None


def _parse_string(text, start, line, delimiter=''):
    """解析字符串（基本串支持转义，字面串不转义）；返回 `(值, 结束位置)`。"""
    if not delimiter:
        delimiter = text[start]
    if delimiter not in ('"', "'"):
        raise TomlSubsetError(line, '字符串引号非法：未产出事实')
    index = start + 1
    out = []
    while index < len(text):
        char = text[index]
        if char == delimiter:
            return ''.join(out), index + 1
        if delimiter == '"' and char == '\\':
            if index + 1 >= len(text):
                break
            nxt = text[index + 1]
            if nxt in _STRING_ESCAPES:
                out.append(_STRING_ESCAPES[nxt])
                index += 2
                continue
            if nxt == 'u' and index + 6 <= len(text):
                hexa = text[index + 2:index + 6]
                if all(c in '0123456789abcdefABCDEF' for c in hexa):
                    out.append(chr(int(hexa, 16)))
                    index += 6
                    continue
            if nxt == 'U' and index + 10 <= len(text):
                hexa = text[index + 2:index + 10]
                if all(c in '0123456789abcdefABCDEF' for c in hexa):
                    out.append(chr(int(hexa, 16)))
                    index += 10
                    continue
            raise TomlSubsetError(line, '字符串转义非法（\\%s）：未产出事实' % nxt[:1])
        out.append(char)
        index += 1
    raise TomlSubsetError(line, '字符串引号未闭合：未产出事实')


def _parse_array(text, line, rel):
    """解析数组（支持嵌套与内联表元素、允许尾随逗号）；返回 Python 列表。"""
    items, index, expect_item = [], 1, True
    while index < len(text):
        char = text[index]
        if char.isspace():
            index += 1
            continue
        if char == ',':
            if expect_item:
                raise TomlSubsetError(line, '数组存在空元素（连续逗号）：未产出事实')
            expect_item = True
            index += 1
            continue
        if char == ']':
            return items                     # TOML 允许尾随逗号，`[1, 2,]` 合法
        if not expect_item:
            raise TomlSubsetError(line, '数组元素之间缺少逗号：未产出事实')
        start = index
        index = _skip_value(text, index, line)
        items.append(_parse_value(text[start:index].strip(), line, rel))
        expect_item = False
    raise TomlSubsetError(line, '数组方括号未闭合：未产出事实')


def _parse_inline_table(text, line, rel):
    """解析内联表 `{a=1, b="x"}`；返回字典。"""
    table, index = {}, 1
    while index < len(text):
        char = text[index]
        if char.isspace() or char == ',':
            index += 1
            continue
        if char == '}':
            return table
        start = index
        index = _skip_value(text, index, line)
        chunk = text[start:index].strip().rstrip(', ')
        if '=' not in chunk:
            raise TomlSubsetError(line, '内联表元素缺少 `=`（`%s`）：未产出事实' % chunk[:40])
        raw_key, raw_value = chunk.split('=', 1)
        key, key_failed = _parse_key(raw_key.strip(), line, rel)
        if key_failed:
            raise TomlSubsetError(line, key_failed[0]['reason'])
        if key in table:
            raise TomlSubsetError(line, '内联表存在重复键 `%s`：未产出事实' % key)
        table[key] = _parse_value(raw_value.strip(), line, rel)
    raise TomlSubsetError(line, '内联表花括号未闭合：未产出事实')


def _skip_value(text, index, line):
    """跳过一个字面量（用于数组/内联表切分），返回下一个分割位置。"""
    start = index
    depth = 0
    while index < len(text):
        char = text[index]
        if char in ('"', "'"):
            index = _skip_string(text, index, line)
            continue
        if char in '[{':
            depth += 1
        elif char in ']}':
            if depth == 0:
                return index
            depth -= 1
        elif char == ',' and depth == 0:
            return index
        index += 1
    return max(start + 1, min(index, len(text)))


def _skip_string(text, index, line):
    delimiter = text[index]
    index += 1
    while index < len(text):
        char = text[index]
        if delimiter == '"' and char == '\\':
            index += 2
            continue
        if char == delimiter:
            return index + 1
        index += 1
    raise TomlSubsetError(line, '字符串引号未闭合：未产出事实')


def _skip_multiline_string(lines, delimiter, index):
    """跳过 `\"\"\"…\"\"\"` / `'''…'''` 多行字符串主体；返回多行串之后的下一行下标。

    `index` 指向多行串起始行的下一行；在后续行里找到闭合定界符即返回其下一行下标；
    到文件末尾仍未闭合则返回 `len(lines)`（该块整体未解析，如实体现在 failedSegments 文案里）。
    """
    while index < len(lines):
        if delimiter in lines[index]:
            return index + 1
        index += 1
    return index


def _needs_more(text):
    """值文本是否还需要后续行（方括号/花括号未配平，或字符串未闭合）。"""
    depth, index, in_string = 0, 0, ''
    while index < len(text):
        char = text[index]
        if in_string:
            if in_string == '"' and char == '\\':
                index += 2
                continue
            if char == in_string:
                in_string = ''
            index += 1
            continue
        if char in ('"', "'"):
            in_string = char
        elif char in '[{':
            depth += 1
        elif char in ']}':
            depth -= 1
        index += 1
    return bool(in_string) or depth > 0


def _strip_comment(text):
    """剥离行尾注释（字符串内的 `#` 不剥离）。"""
    index, in_string = 0, ''
    while index < len(text):
        char = text[index]
        if in_string:
            if in_string == '"' and char == '\\':
                index += 2
                continue
            if char == in_string:
                in_string = ''
            index += 1
            continue
        if char in ('"', "'"):
            in_string = char
        elif char == '#':
            return text[:index].strip()
        index += 1
    return text.strip()


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def _path_text(segments):
    path = '$'
    for segment in segments:
        path = structwalk.child_path(path, segment)
    return path


def _locator(rel, path, line):
    return {'kind': 'toml', 'file': rel, 'path': path, 'line': int(line or 0)}


def _line_for(path, roots):
    """键路径 → 源文件行号；未登记的路径返回 0（parse 会因此省略 locator.line）。"""
    return roots.get(path, 0)


def _register_value_paths(path, value, line, roots):
    """把键路径及其内联表子树登记到行号映射（数组不展开，故只沿字典下探）。"""
    roots[path] = line
    if isinstance(value, dict):
        for key, item in value.items():
            _register_value_paths(structwalk.child_path(path, key), item, line, roots)


def _attach_syntax(item, syntax):
    item['syntax'] = str(syntax or '')
    return item
