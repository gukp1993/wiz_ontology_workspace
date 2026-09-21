"""未知类型材料的文本线索降级解析 + 解析器包共用工具。

能力边界（本模块 docstring 即对外承诺）
--------------------------------------
* 支持：任意后缀（含无后缀 / 未知后缀）的**文本**文件按行读取。编码依次尝试
  utf-8 / utf-8-sig / utf-16(BOM) / gb18030 / gbk / latin-1；每行产出一条“文本线索”事实。
* 降级：本模块**不做任何结构解析**（不识别类、表、标题层级、字段），所有事实
  `quality='low'`，`partial=True`，`coverage.notes` 明确写明“该类型无结构解析，仅文本线索，
  覆盖不足”。定位器使用 `{'kind':'text','file','line'}`（接口文档 08 只为 code/ddl/docx/pdf/
  xlsx/md 定义定位器形状，`kind=other` 尚无定义，此处显式扩展并在 coverage 标注）。
* 不执行：不 eval/exec 材料内容，不解析/执行宏，不访问网络或外链，不渲染 HTML/脚本。
* 限额：单文件最多读取 `MAX_TEXT_CHARS` 个字符，超出部分丢弃（`partial=True` + notes 说明已处理
  范围）；事实数受 `protocol.MAX_FACTS_PER_MATERIAL` 限制，超出截断并在 notes 说明。

另外，本模块提供解析器包内共用的最小工具（文本读取与编码探测、行片段、事实收集与截断、
coverage 组装），避免为这点公共代码新增模块。共用工具不改变任何解析能力边界。

failedSegments 条目统一形状（本包约定，供扫描报告展示）：
    {'kind': 'statement'|'page'|'sheet'|'block'|'line', 'locator': {...}, 'reason': '中文原因'}
"""

from workbench.ontology_build import protocol
from workbench.ontology_build.parsers.base import Fact, ParseResult, failure

# --- 限额（软上限，超限即 partial + notes） -------------------------------------
MAX_TEXT_CHARS = 100000          # 单文件最多处理的字符数
MAX_LINE_CHARS = 2000            # 单行参与解析/入库的最大字符数
_BYTES_PER_CHAR_GUESS = 4        # utf-8 最坏情况；按此预估读取字节数，避免超大文件读入内存

UTF8_ALIASES = ('utf-8', 'utf-8-sig', 'ascii')
_TRUNCATED_NOTE_TMPL = '文件超过单文件解析软上限（%d 字符），仅处理前 %d 字符，其余未解析'


# ---------------------------------------------------------------------------
# 文本读取与编码探测
# ---------------------------------------------------------------------------
class TextContent:
    """按行读取结果：行文本、编码、是否被软上限截断。"""

    __slots__ = ('lines', 'encoding', 'truncated', 'chars', 'filesize')

    def __init__(self, lines, encoding, truncated, chars, filesize):
        self.lines = lines
        self.encoding = encoding
        self.truncated = bool(truncated)
        self.chars = int(chars)
        self.filesize = int(filesize)

    def line(self, number):
        """1 基行号取原文；越界返回空串。"""
        if 1 <= number <= len(self.lines):
            return self.lines[number - 1]
        return ''


def _detect_encoding(raw):
    """探测文本编码：BOM 优先，其次严格 utf-8，再次 gb18030，最后 latin-1 兜底。"""
    if raw.startswith(b'\xef\xbb\xbf'):
        return 'utf-8-sig'
    if raw.startswith(b'\xff\xfe') or raw.startswith(b'\xfe\xff'):
        return 'utf-16'
    if raw.startswith(b'\x00\x00\xfe\xff') or raw.startswith(b'\xff\xfe\x00\x00'):
        return 'utf-32'
    for encoding in ('utf-8', 'gb18030'):
        try:
            raw.decode(encoding)
            return encoding
        except (UnicodeDecodeError, LookupError):
            continue
    try:
        raw.decode('gbk')
        return 'gbk'
    except (UnicodeDecodeError, LookupError):
        pass
    return 'latin-1'


def read_text_lines(path, char_limit=MAX_TEXT_CHARS):
    """读取文本文件并切分为行；编码探测失败时退 latin-1（不抛 UnicodeDecodeError）。

    读满 `char_limit` 字符后停止（truncated=True），避免超大文件把内存/时间打满。
    读取失败（IOError/OSError）由调用方转成 failure。
    """
    with open(str(path), 'rb') as handle:
        raw = handle.read(int(char_limit) * _BYTES_PER_CHAR_GUESS + 8)
    truncated = len(raw) > int(char_limit) * _BYTES_PER_CHAR_GUESS
    if truncated:
        raw = raw[:int(char_limit) * _BYTES_PER_CHAR_GUESS]
    encoding = _detect_encoding(raw)
    try:
        text = raw.decode(encoding, errors='replace')
    except LookupError:  # 极端环境缺编解码器
        encoding = 'latin-1'
        text = raw.decode('latin-1', errors='replace')
    if len(text) > char_limit:
        text = text[:char_limit]
        truncated = True
    lines = [line[:MAX_LINE_CHARS] for line in text.splitlines()]
    return TextContent(lines, encoding, truncated, len(text), len(raw))


def rel_of(path, rel_path=''):
    """定位器里使用的相对路径：优先调用方给的 rel_path，否则取文件名（绝不放绝对路径）。"""
    rel = str(rel_path or '').strip().replace('\\', '/')
    if rel:
        return rel
    name = str(path or '').replace('\\', '/')
    return name.rsplit('/', 1)[-1]


def snippet_span(content, start_line, end_line=None, max_lines=3):
    """取原文片段：从 start_line 起最多 max_lines 行（1 基，含首尾）。"""
    if content is None:
        return ''
    end = end_line if end_line else start_line
    end = min(end, start_line + max_lines - 1)
    parts = []
    for number in range(start_line, end + 1):
        text = content.line(number)
        if text == '':
            continue
        parts.append(text.strip())
    return '\n'.join(parts)


# ---------------------------------------------------------------------------
# 事实收集与结果组装
# ---------------------------------------------------------------------------
class FactSink:
    """事实收集器：去重（同 locator+片段 id 相同）、按 MAX_FACTS_PER_MATERIAL 截断。"""

    __slots__ = ('material_id', 'cap', 'facts', 'seen', 'dropped')

    def __init__(self, material_id, cap=None):
        self.material_id = material_id
        self.cap = int(cap or protocol.MAX_FACTS_PER_MATERIAL)
        self.facts = []
        self.seen = set()
        self.dropped = 0

    @property
    def truncated(self):
        return self.dropped > 0

    def add(self, module, locator, snippet, kind, data=None, quality='high'):
        """登记一条事实；超出上限或重复（同 id）时返回 None，不抛异常。"""
        if len(self.facts) >= self.cap:
            self.dropped += 1
            return None
        text = str(snippet or '')
        if not text.strip():
            text = ''
        fact = Fact(self.material_id, module, locator, text, kind, data, quality)
        if fact.id in self.seen:
            return None
        self.seen.add(fact.id)
        self.facts.append(fact)
        return fact

    def truncation_note(self):
        """截断说明（没有截断则返回 None）。"""
        if not self.truncated:
            return None
        return ('事实数达到单材料上限 %d 条，已截断 %d 条（截断部分未纳入证据）'
                % (self.cap, self.dropped))


def finish(sink, modules, notes, failed_segments=None, partial=False, warnings=None, **extra):
    """组装 base.ParseResult；自动附加截断说明。"""
    note_list = list(notes or [])
    note = sink.truncation_note()
    if note:
        note_list.append(note)
    coverage = {
        'modules': _unique(modules or []),
        'notes': note_list,
        'failedSegments': list(failed_segments or []),
    }
    for key, value in (extra or {}).items():
        coverage[key] = value
    return ParseResult(facts=sink.facts, coverage=coverage,
                       warnings=list(warnings or []), partial=bool(partial or sink.truncated))


def _unique(values):
    out = []
    for value in values:
        text = str(value or '').strip()
        if text and text not in out:
            out.append(text)
    return out


def truncation_note(char_limit=MAX_TEXT_CHARS):
    return _TRUNCATED_NOTE_TMPL % (char_limit, char_limit)


def failed_segment(kind, locator, reason, excerpt=''):
    """构造统一形状的失败片段条目。"""
    item = {'kind': str(kind or 'segment'), 'locator': locator if isinstance(locator, dict) else {},
            'reason': str(reason or '')}
    if excerpt:
        item['excerpt'] = protocol.clamp_snippet(excerpt, 200)
    return item


# ---------------------------------------------------------------------------
# 未知类型降级解析
# ---------------------------------------------------------------------------
def parse(path, material_id, rel_path=''):
    """未知类型降级：按行产出文本线索事实（quality=low，partial=True）。"""
    rel = rel_of(path, rel_path)
    try:
        content = read_text_lines(path)
    except (OSError, IOError) as exc:
        return failure('读取文件失败：%s' % exc)
    except ValueError as exc:  # 例如路径为目录
        return failure('无法读取材料：%s' % exc)

    sink = FactSink(material_id)
    if not content.lines:
        return finish(sink, ['text'],
                      ['该类型无结构解析，仅文本线索，覆盖不足：文件无可读文本行。'],
                      partial=True)

    for number, raw_line in enumerate(content.lines, start=1):
        text = raw_line.strip()
        if not text:
            continue
        sink.add('text', {'kind': 'text', 'file': rel, 'line': number, 'symbol': ''}, text,
                 'textLine', {'line': number, 'chars': len(text)}, quality='low')

    notes = [
        '该类型无结构解析，仅文本线索，覆盖不足：不识别类/表/标题层级，全部事实 quality=low，'
        '需要人工确认或补充可解析材料。',
        '文本线索定位器使用 kind=text（接口文档未定义 other 类型定位器，此处显式扩展）。',
        '已处理 %d 行；空行不计入事实。' % len(content.lines),
    ]
    if content.truncated:
        notes.append(truncation_note())
    if content.encoding not in UTF8_ALIASES:
        notes.append('按 %s 编码解码（非 utf-8），可能出现替换字符。' % content.encoding)
    warnings = []
    if any('\ufffd' in line for line in content.lines):
        warnings.append('解码出现替换字符，部分文本可能不可读。')
    return finish(sink, ['text'], notes, partial=True, warnings=warnings,
                  locatorKind='text')
