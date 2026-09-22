"""Java `.properties` 专用解析：`key=value` 逐条事实、点号层级提示、Java 转义解码。

能力边界（支持 / 降级 / 不执行）
--------------------------------
* 支持（纯标准库，逐行扫描）：
  - `key=value` / `key: value` / `key value` / 仅 `key`（空值）四种 Java Properties 写法；
  - 行续接（行尾未转义的反斜杠把下一行并入同一逻辑条目，定位取起始行号）；
  - 注释行（`#` / `!` 开头，允许前导空白）与空行：只计入 `coverage.notes` 计数，**不计事实**；
  - **编码探测复用 textline 既有探测链**（BOM → utf-8 → gb18030 → gbk → latin-1），
    中文值不会按 ASCII 误解；
  - **`\\uXXXX` 按 Java 规范解码**（4 位十六进制；顺带支持 `\\t\\n\\r\\f` 与 `\\`、`\\:`、`\\=`、
    `\\#`、`\\!`、`\\ ` 等转义，未知转义按 Java 语义丢弃反斜杠保留字符）；
    代理对（`\\uD83D\\uDE00`）合并为一个字符，残留孤立代理码位替换为 U+FFFD 并进 failedSegments
    （避免孤立代理码位写库时编码失败）；
  - 键中的点号只作**层级提示**记入 `data.hierarchy` / `data.leaf`，不做嵌套展开（值仍是原文）。
* 降级（写 notes / failedSegments，不静默）：无效 `\\uXXXX` 转义逐条记录并保留原文；
  键为空的条目（`=value`）记 failedSegments 且不产出事实；续接行到文件末尾未闭合时记录。
* 不执行：不解析 SpEL/占位符、不做变量替换、不读取被引用的其他文件、不访问网络。

定位器：`{'kind':'properties','file':rel,'line':行号}`（1 基真实行号，续接条目取首行）。
"""

from workbench.ontology_build.parsers import structwalk
from workbench.ontology_build.parsers import textline
from workbench.ontology_build.parsers.base import failure
from workbench.ontology_build.parsers.textline import FactSink, failed_segment, finish, rel_of

SIMPLE_ESCAPES = {'t': '\t', 'n': '\n', 'r': '\r', 'f': '\f'}
SNIPPET_CHARS = 400
_HIGH_SURROGATE = (0xD800, 0xDBFF)
_LOW_SURROGATE = (0xDC00, 0xDFFF)


def _split_entry(line):
    """按 Java Properties 规则切分键与值：键在首个未转义的 `=`、`:` 或空白处结束。"""
    key_chars, index, escaped = [], 0, False
    length = len(line)
    while index < length:
        char = line[index]
        if escaped:
            key_chars.append(char)
            escaped = False
            index += 1
            continue
        if char == '\\':
            key_chars.append(char)
            escaped = True
            index += 1
            continue
        if char in '=:' or char.isspace():
            break
        key_chars.append(char)
        index += 1
    while index < length and line[index].isspace():
        index += 1
    if index < length and line[index] in '=:':
        index += 1
        while index < length and line[index].isspace():
            index += 1
    return ''.join(key_chars), line[index:]


def _unescape(text):
    """Java 转义解码；返回 `(解码结果, 问题说明列表)`。"""
    out, problems, index, length = [], [], 0, len(text)
    while index < length:
        char = text[index]
        if char != '\\' or index + 1 >= length:
            out.append(char)
            index += 1
            continue
        nxt = text[index + 1]
        if nxt == 'u':
            hexa = text[index + 2:index + 6]
            if len(hexa) == 4 and all(c in '0123456789abcdefABCDEF' for c in hexa):
                out.append(chr(int(hexa, 16)))
                index += 6
                continue
            problems.append('无效 \\uXXXX 转义（第 %d 个字符起，需要 4 位十六进制）：已按原文保留'
                            % (index + 1))
            out.append(char)
            index += 1
            continue
        if nxt in SIMPLE_ESCAPES:
            out.append(SIMPLE_ESCAPES[nxt])
            index += 2
            continue
        out.append(nxt)     # Java：未知转义丢弃反斜杠、保留字符本身
        index += 2
    return _combine_surrogates(''.join(out), problems)


def _combine_surrogates(text, problems):
    """合并 \\uXXXX 代理对；孤立代理码位替换为 U+FFFD（保证事实可安全写库）。"""
    out, index, length = [], 0, len(text)
    while index < length:
        code = ord(text[index])
        if _HIGH_SURROGATE[0] <= code <= _HIGH_SURROGATE[1] and index + 1 < length:
            low = ord(text[index + 1])
            if _LOW_SURROGATE[0] <= low <= _LOW_SURROGATE[1]:
                out.append(chr(0x10000 + ((code - 0xD800) << 10) + (low - 0xDC00)))
                index += 2
                continue
        if _HIGH_SURROGATE[0] <= code <= _LOW_SURROGATE[1]:
            problems.append('孤立代理码位 U+%04X：已替换为 U+FFFD（原文不可直接解码）' % code)
            out.append('\ufffd')
            index += 1
            continue
        out.append(text[index])
        index += 1
    return ''.join(out), problems


def _ends_with_continuation(text):
    """行尾是否为未转义的反斜杠（奇数个连续反斜杠即续接）。"""
    count = 0
    for char in reversed(text):
        if char != '\\':
            break
        count += 1
    return count % 2 == 1


def _logical_lines(lines):
    """把物理行合并成逻辑条目：返回 `[(首行行号, 文本, 跨行数)]`。"""
    entries, index, total = [], 0, len(lines)
    while index < total:
        number = index + 1
        text = lines[index]
        span = 1
        while _ends_with_continuation(text) and index + 1 < total:
            text = text[:len(text) - 1] + lines[index + 1].lstrip()
            index += 1
            span += 1
        entries.append((number, text, span))
        index += 1
    return entries


def parse(path, material_id, rel_path=''):
    """解析 `.properties`：逐条键值事实（含 Java 转义解码与点号层级提示）。"""
    rel = rel_of(path, rel_path)
    try:
        text, encoding, size = structwalk.read_document_text(path)
    except (OSError, IOError) as exc:
        return _read_failure('读取文件失败：%s' % exc)
    except ValueError as exc:
        return _read_failure(str(exc))

    sink = FactSink(material_id)
    failed, notes = [], []
    comments = {'#': 0, '!': 0}
    blanks, continuations, hierarchical = 0, 0, 0
    entries = _logical_lines(str(text or '').splitlines())
    for number, raw, span in entries:
        stripped = raw.strip()
        if not stripped:
            blanks += 1
            continue
        if stripped[0] in ('#', '!'):
            comments[stripped[0]] += 1
            continue
        if span > 1:
            continuations += 1
        else:
            stripped = raw.lstrip()
        raw_key, raw_value = _split_entry(stripped)
        key, key_problems = _unescape(raw_key)
        value, value_problems = _unescape(raw_value)
        for problem in key_problems + value_problems:
            failed.append(failed_segment('line',
                                        {'kind': 'properties', 'file': rel, 'line': number},
                                        problem))
        if not key.strip():
            failed.append(failed_segment('line',
                                        {'kind': 'properties', 'file': rel, 'line': number},
                                        '键为空（条目以分隔符开头），未产出事实',
                                        stripped[:120]))
            continue
        segments = [part for part in key.split('.') if part]
        hierarchical += 1 if len(segments) > 1 else 0
        hierarchy = segments if len(segments) > 1 else []
        data = {'key': key, 'value': value, 'line': number, 'rawKey': raw_key,
                'chars': len(value), 'continued': span > 1}
        if hierarchy:
            data['hierarchy'] = hierarchy
            data['leaf'] = segments[-1]
        snippet = '%s=%s' % (key, value)
        sink.add('properties', {'kind': 'properties', 'file': rel, 'line': number},
                 snippet[:SNIPPET_CHARS], 'propertyEntry', data, 'high')

    notes.append('已处理 %d 行：键值事实 %d 条、注释 %d 行（# %d、! %d）、空行 %d，'
                 '续接条目 %d 条。注释按口径只计入说明、不计入事实。'
                 % (len(entries), len(sink.facts), comments['#'] + comments['!'],
                    comments['#'], comments['!'], blanks, continuations))
    notes.append('点号只作层级提示（data.hierarchy / data.leaf，共 %d 条），值保持原文不做嵌套展开；'
                 '`\\uXXXX` 与常见转义按 Java 规范解码（key/value 均已解码）。' % hierarchical)
    notes.append('定位器使用 kind=properties + 真实行号（续接条目取起始行）；'
                 '未执行：不做占位符/SpEL 求值、不读取被引用文件、不访问网络。')
    if encoding not in textline.UTF8_ALIASES:
        notes.append('按 %s 编码解码（非 utf-8），中文值按该编码还原。' % encoding)
    if not sink.facts:
        return finish(sink, ['properties'], notes + ['文件没有可登记的键值条目。'], failed,
                      partial=True)
    return finish(sink, ['properties'], notes, failed, partial=bool(failed))


def _read_failure(reason):
    """读取失败统一转成显式 failure（零事实）。"""
    return failure(reason, coverage={'modules': ['properties'], 'notes': [reason],
                                     'failedSegments': []})
