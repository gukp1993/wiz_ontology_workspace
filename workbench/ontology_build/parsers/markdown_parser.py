"""Markdown 解析：标题层级、段落、表格、代码块（保留章节路径与行号）。

能力边界（支持 / 降级 / 不执行）
--------------------------------
* 支持（纯标准库逐行扫描，不引入 markdown 库）：ATX 标题 `#`…`######`（含 `#` 结尾闭合写法）、
  Setext 标题（`===`/`---` 下划线）、段落、管道表格（`| a | b |`，含对齐行 `---` 与转义 `\|`）、
  围栏代码块（``` 与 ~~~，含语言标记，行范围记录）、引言块、列表项（`-`/`*`/`+`/`1.`）、
  水平线、HTML 块（只登记为原始文本，不渲染）、行内链接/图片/引用式链接定义。
* 降级（写入 notes / failedSegments）：未闭合的代码围栏（到文件末尾）、表格列数不齐、
  Setext 下划线歧义（`---` 同时可能是水平线/表格分隔）、脚本/HTML 片段（不执行、不渲染）、
  内容控件类扩展语法（脚注、定义列表、YAML front matter 原样登记为文本块）。
* 不执行：不渲染 Markdown/HTML、不执行代码块内容、不访问链接或图片地址、不执行 mermaid 等
  图表脚本、不解析内嵌 base64 资源。

定位器：`{'kind':'md','file':rel_path,'section':标题路径,'line':行号}`；
`section` 为 `/` 连接的标题路径（无标题时为空串），line 为真实行号。
"""

import re

from workbench.ontology_build.parsers.base import failure
from workbench.ontology_build.parsers import textline
from workbench.ontology_build.parsers.textline import FactSink, failed_segment, finish, rel_of

MAX_LEVEL = 6
MAX_TABLE_CELLS = 5000
_ATX = re.compile(r'^(#{1,6})\s+(.*?)\s*#*\s*$')
_SETEXT = re.compile(r'^(=+|-{2,})\s*$')
_FENCE = re.compile(r'^\s*(```+|~~~+)\s*([^\s`]*)')
_TABLE_ROW = re.compile(r'^\s*\|(.+)\|\s*$')
_TABLE_SEPARATOR = re.compile(r'^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$')
_LIST_ITEM = re.compile(r'^\s*([-*+]|\d+[.)])\s+(.*)$')
_QUOTE = re.compile(r'^\s*>\s?(.*)$')
_HR = re.compile(r'^\s{0,3}([-*_])(\s*\1){2,}\s*$')
_LINK = re.compile(r'(?<!\!)\[([^\]]+)\]\(([^)\s]+)(?:\s+"[^"]*")?\)')
_IMAGE = re.compile(r'!\[([^\]]*)\]\(([^)\s]+)(?:\s+"[^"]*")?\)')
_LINK_DEF = re.compile(r'^\s*\[([^\]]+)\]:\s*(\S+)')
_HTML_BLOCK = re.compile(r'^\s*<(/?)([A-Za-z][\w-]*)(\s[^>]*)?/?>')


def _unite_table_row(row_text):
    """拆分管表格行；支持 `\\|` 转义。

    行首的 `|` 是行标记而不是空单元格：从第 2 个字符开始切分（D18 表格内容修正——
    旧实现会把首列拆成一个恒为空的幽灵列，表头与列名对应关系整体错位一列）。
    """
    if not row_text.startswith('|'):
        return []
    cells = []
    buf = []
    index = 1
    text = row_text
    while index < len(text):
        char = text[index]
        if char == '\\' and index + 1 < len(text) and text[index + 1] == '|':
            buf.append('|')
            index += 2
            continue
        if char == '|':
            cells.append(''.join(buf).strip())
            buf = []
            index += 1
            continue
        buf.append(char)
        index += 1
    if ''.join(buf).strip():
        cells.append(''.join(buf).strip())
    return cells


def parse(path, material_id, rel_path=''):
    """解析 Markdown：标题/段落/表格/代码块，全部带章节路径与行号。"""
    rel = rel_of(path, rel_path)
    try:
        content = textline.read_text_lines(path)
    except (OSError, IOError) as exc:
        return failure('读取文件失败：%s' % exc)
    sink = FactSink(material_id)
    if not content.lines:
        return finish(sink, ['md'], ['Markdown 文件无可读文本内容（0 行）。'], partial=True)

    lines = content.lines
    total = len(lines)
    notes, failed = [], []
    headings = []            # [(level, text)]
    modules = ['markdown']
    counts = {'heading': 0, 'paragraph': 0, 'table': 0, 'code': 0, 'list': 0, 'quote': 0}
    paragraph_buf = []
    paragraph_start = 0
    in_fence = None
    fence_start = 0
    fence_language = ''
    table_buf = []
    table_start = 0            # 当前表格首行行号（D18：必须随表格开始真实赋值，不得留 0）
    front_matter_done = False

    def section_path():
        return ' / '.join(item[1] for item in headings)

    def flush_paragraph():
        if not paragraph_buf:
            return
        text = '\n'.join(paragraph_buf).strip()
        if text:
            counts['paragraph'] += 1
            sink.add('md', {'kind': 'md', 'file': rel, 'section': section_path(),
                            'line': paragraph_start},
                     text[:800], 'paragraph',
                     {'text': text, 'startLine': paragraph_start,
                      'endLine': paragraph_start + len(paragraph_buf) - 1,
                      'chars': len(text)}, 'high')
        del paragraph_buf[:]

    def flush_table():
        if not table_buf:
            return
        # 定位行以缓冲里第一条记录的真实行号为准（D18：table_start 曾未赋值 → line=0）
        start = table_buf[0]['line'] or table_start
        # 对齐分隔行（`| --- | --- |` / `--- | ---`）不是数据行：剔除后再编号，
        # 同时将行号随单元格行一起携带，保证 locator 指向原文真实行（D18）。
        data, separator_line = [], 0
        for item in table_buf:
            if item['separator']:
                separator_line = separator_line or item['line']
                continue
            cells = _unite_table_row(item['raw'])
            if cells:
                data.append({'line': item['line'], 'cells': cells})
        header = data[0]['cells'] if data else []
        body = data[1:]
        width = max((len(item['cells']) for item in data), default=0)
        counts['table'] += 1
        sink.add('md', {'kind': 'md', 'file': rel, 'section': section_path(),
                        'line': start},
                 ' | '.join(header)[:400] or '(空表头)', 'table',
                 {'header': header, 'rows': len(body), 'columns': width,
                  'startLine': start,
                  'endLine': table_buf[-1]['line'], 'separatorLine': separator_line}, 'high')
        emitted = 0
        for offset, entry in enumerate(body, start=2):
            row = entry['cells']
            row_line = entry['line'] or start
            if len(row) != width:
                failed.append(failed_segment(
                    'block', {'kind': 'md', 'file': rel, 'section': section_path(),
                              'line': row_line},
                    '表格行列数不齐（该行 %d 列，表宽 %d 列）：未猜测缺失单元格。'
                    % (len(row), width)))
            for index, cell in enumerate(row, start=1):
                if emitted >= MAX_TABLE_CELLS:
                    notes.append('表格单元格超过上限 %d，其余未解析。' % MAX_TABLE_CELLS)
                    break
                if not cell:
                    continue
                emitted += 1
                column_name = header[index - 1] if index - 1 < len(header) else ''
                sink.add('md', {'kind': 'md', 'file': rel, 'section': section_path(),
                                'line': row_line},
                         ('%s：%s' % (column_name, cell) if column_name else cell)[:400],
                         'tableCell',
                         {'row': offset, 'column': index, 'columnName': column_name,
                          'header': header[index - 1] if index - 1 < len(header) else '',
                          'value': cell, 'text': cell}, 'high')
        del table_buf[:]

    for number, raw in enumerate(lines, start=1):
        line = raw
        fence = _FENCE.match(line)
        if in_fence:
            if fence and fence.group(1)[0] == in_fence['char'] \
                    and len(fence.group(1)) >= in_fence['len']:
                counts['code'] += 1
                body = '\n'.join(in_fence['body'])
                sink.add('md', {'kind': 'md', 'file': rel, 'section': section_path(),
                                'line': fence_start},
                         body.strip()[:800] or '(空代码块)', 'codeBlock',
                         {'language': fence_language, 'startLine': fence_start,
                          'endLine': number, 'fence': in_fence['raw'],
                          'lines': number - fence_start - 1, 'body': body[:2000],
                          'info': in_fence['info']}, 'high')
                in_fence = None
            else:
                in_fence['body'].append(line)
            continue
        if fence:
            flush_paragraph()
            flush_table()
            marker = fence.group(1)
            in_fence = {'char': marker[0], 'len': len(marker), 'raw': marker, 'body': [],
                        'info': (fence.group(2) or '').strip()}
            fence_start = number
            fence_language = in_fence['info'].split()[0] if in_fence['info'] else ''
            continue

        heading_match = _ATX.match(line)
        if heading_match:
            flush_paragraph()
            flush_table()
            level = len(heading_match.group(1))
            text = heading_match.group(2).strip()
            headings = [item for item in headings if item[0] < level]
            headings.append((level, text))
            counts['heading'] += 1
            sink.add('md', {'kind': 'md', 'file': rel, 'section': section_path(), 'line': number},
                     line.strip(), 'heading',
                     {'level': level, 'text': text, 'section': section_path(),
                      'explicitLevel': True}, 'high')
            continue

        if _TABLE_ROW.match(line):
            next_line = lines[number] if number < total else ''
            if table_buf or _TABLE_SEPARATOR.match(next_line):
                flush_paragraph()
                if not table_buf:
                    table_start = number      # 表格首行：真实起始行（locator 与 startLine 用）
                # 对齐分隔行也以 `|` 开头：必须单独标记，否则会被当成第一条数据行（D18）
                table_buf.append({'raw': line, 'line': number,
                                  'separator': bool(_TABLE_SEPARATOR.match(line))})
                continue
        if _TABLE_SEPARATOR.match(line) and table_buf:
            table_buf.append({'raw': line, 'line': number, 'separator': True})
            continue

        setext = _SETEXT.match(line)
        if setext and paragraph_buf and len(paragraph_buf) == 1 and not _LIST_ITEM.match(
                paragraph_buf[0]):
            text = paragraph_buf[0].strip()
            del paragraph_buf[:]
            flush_table()
            level = 1 if setext.group(1).startswith('=') else 2
            headings = [item for item in headings if item[0] < level]
            headings.append((level, text))
            counts['heading'] += 1
            sink.add('md', {'kind': 'md', 'file': rel, 'section': section_path(),
                            'line': number - 1},
                     text, 'heading',
                     {'level': level, 'text': text, 'section': section_path(),
                      'explicitLevel': False,
                      'note': 'Setext 下划线标题（下划线在第 %d 行）' % number}, 'high')
            continue
        if setext:
            flush_paragraph()
            flush_table()
            sink.add('md', {'kind': 'md', 'file': rel, 'section': section_path(), 'line': number},
                     line.strip(), 'horizontalRule', {'text': line.strip()}, 'medium')
            continue

        list_match = _LIST_ITEM.match(line)
        if list_match:
            flush_paragraph()
            flush_table()
            marker, text = list_match.group(1), list_match.group(2)
            counts['list'] += 1
            sink.add('md', {'kind': 'md', 'file': rel, 'section': section_path(), 'line': number},
                     line.strip(), 'listItem',
                     {'marker': marker, 'text': text, 'ordered': marker[0].isdigit(),
                      'level': len(line) - len(line.lstrip(' '))}, 'medium')
            for match in _LINK.finditer(text):
                sink.add('md', {'kind': 'md', 'file': rel, 'section': section_path(),
                                'line': number},
                         match.group(0)[:400], 'link',
                         {'text': match.group(1), 'target': match.group(2),
                          'external': '://' in match.group(2)}, 'medium')
            continue

        quote_match = _QUOTE.match(line)
        if quote_match:
            flush_paragraph()
            counts['quote'] += 1
            text = quote_match.group(1).strip()
            sink.add('md', {'kind': 'md', 'file': rel, 'section': section_path(), 'line': number},
                     line.strip(), 'quote', {'text': text, 'blockStart': paragraph_start == 0},
                     'medium')
            continue

        link_def = _LINK_DEF.match(line)
        if link_def:
            flush_paragraph()
            flush_table()
            sink.add('md', {'kind': 'md', 'file': rel, 'section': section_path(), 'line': number},
                     line.strip(), 'linkDefinition',
                     {'label': link_def.group(1), 'target': link_def.group(2),
                      'external': '://' in link_def.group(2)}, 'medium')
            continue

        html_match = _HTML_BLOCK.match(line)
        if html_match:
            flush_paragraph()
            flush_table()
            sink.add('md', {'kind': 'md', 'file': rel, 'section': section_path(), 'line': number},
                     line.strip()[:400], 'htmlBlock',
                     {'tag': html_match.group(2), 'closing': html_match.group(1) == '/',
                      'name': html_match.group(2),
                      'note': '原样登记，未渲染、未执行'}, 'medium')
            continue

        if _HR.match(line):
            flush_paragraph()
            flush_table()
            sink.add('md', {'kind': 'md', 'file': rel, 'section': section_path(), 'line': number},
                     line.strip(), 'horizontalRule', {'text': line.strip()}, 'medium')
            continue

        if not line.strip():
            flush_paragraph()
            flush_table()
            continue

        if not front_matter_done and number == 1 and line.strip() == '---':
            front_matter_done = True
            sink.add('md', {'kind': 'md', 'file': rel, 'section': '', 'line': number},
                     line.strip(), 'frontMatterMarker',
                     {'note': 'YAML front matter 起始标记；内容按普通段落登记，未做 YAML 解析'},
                     'low')

        if table_buf and not _TABLE_ROW.match(line):
            flush_table()
        if not paragraph_buf:
            paragraph_start = number
        paragraph_buf.append(line)
        if len(paragraph_buf) >= 40:
            flush_paragraph()

    flush_paragraph()
    flush_table()
    if in_fence:
        failed.append(failed_segment(
            'block', {'kind': 'md', 'file': rel, 'section': section_path(), 'line': fence_start},
            '代码围栏未闭合（到文件末尾）：该块内容按原文登记但边界不可靠。'))
        sink.add('md', {'kind': 'md', 'file': rel, 'section': section_path(), 'line': fence_start},
                 '\n'.join(in_fence['body'])[:800], 'codeBlock',
                 {'language': fence_language, 'startLine': fence_start, 'endLine': total,
                  'fence': in_fence['raw'], 'unterminated': True,
                  'body': '\n'.join(in_fence['body'])[:2000]}, 'low')

    notes.append('已处理 %d 行：标题 %d、段落 %d、表格 %d、代码块 %d、列表项 %d、引言 %d。'
                 % (total, counts['heading'], counts['paragraph'], counts['table'],
                    counts['code'], counts['list'], counts['quote']))
    notes.append('定位为「章节路径 + 行号」；章节路径由 ATX/Setext 标题层级维护。')
    notes.append('未执行：不渲染 Markdown/HTML、不执行代码块内容、不访问链接/图片地址。')
    if content.truncated:
        notes.append(textline.truncation_note())
    if not sink.facts:
        return finish(sink, modules, notes + ['Markdown 中未提取到任何结构化内容。'], failed,
                      partial=True)
    return finish(sink, modules, notes, failed, partial=bool(failed or content.truncated),
                  **counts)
