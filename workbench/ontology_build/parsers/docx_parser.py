"""DOCX 解析：段落、标题层级、表格（zipfile 直读 `word/document.xml`）。

能力边界（支持 / 降级 / 不执行）
--------------------------------
* 支持：用标准库 `zipfile` + `xml.etree.ElementTree` 直读 `word/document.xml`，按文档顺序
  产出段落（`w:p`）、标题（`w:pStyle` 含 `Heading`/`heading`/中文“标题 N” → 层级）、
  表格（`w:tbl` → 行 `w:tr` / 单元格 `w:tc` 文本矩阵），合并单元格按 `w:gridSpan` 标注跨度。
* 降级（写入 notes / failedSegments / warnings）：
  - `.doc`（OLE 复合文档魔数 D0 CF 11 E0）→ 直接返回 failure('旧版 .doc 建议转换为 .docx 后上传')。
  - 非 docx（无 `word/document.xml`）→ failure 明确原因。
  - 页眉页脚/脚注/批注/文本框/文本框内表格：默认不展开（`word/header*.xml`、`footer*.xml`、
    `footnotes.xml`、`comments.xml` 只统计数量并说明未纳入）。
  - 图片、OLE 嵌入对象、域代码 `w:instrText`、宏（`vbaProject.bin`）：一律不读、不解析、不执行。
  - 表格嵌套、样式继承得到的标题层级、`w:sdt` 内容控件内的段落：尽力而为，失败记 failedSegments。
* 不执行：不依赖 python-docx（缺失也可用，仅作为交叉核对不参与主流程）、不渲染文档、
  不执行宏/域/公式、不访问网络或外链、不解析 `w:hyperlink` 目标以外的内容。

定位器：`{'kind':'docx','file':rel_path,'section':标题路径,'block':文档顺序序号}`（从 1 开始，
按段落/表格在 `document.xml` 中出现的顺序编号）；`section` 为 `/` 连接的标题路径（无标题时为空串）。
"""

import re
import zipfile
import xml.etree.ElementTree as ET

from workbench.ontology_build.parsers.base import failure
from workbench.ontology_build.parsers import textline
from workbench.ontology_build.parsers.textline import FactSink, failed_segment, finish, rel_of

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
_DOC_MAGIC = b'\xd0\xcf\x11\xe0'
_MAX_CELLS_PER_TABLE = 4000
_MAX_HEADING_LEVEL = 9
_HEADING_STYLE = re.compile(r'^(Heading|heading|标题)\s*([1-9]\d*)?', re.I)
_OUTLINE_LEVEL = re.compile(r'^\s*(\d+)\s*$')
_NUMERIC_PREFIX = re.compile(r'^\s*(\d+(?:\.\d+)*)\s*[、\.\)]?\s*')


def _local(tag):
    return tag.rsplit('}', 1)[-1] if isinstance(tag, str) and '}' in tag else str(tag)


def _paragraph_text(node):
    """段落文本：拼接所有 w:t（w:tab→空格，w:br/w:cr→换行），跳过 instrText 域代码。"""
    parts = []
    for child in node.iter():
        name = _local(child.tag)
        if name == 't':
            parts.append(child.text or '')
        elif name == 'tab':
            parts.append(' ')
        elif name in ('br', 'cr'):
            parts.append('\n')
    text = ''.join(parts)
    return re.sub(r'[ \t]+', ' ', text).strip()


def _paragraph_style(node):
    """段落样式 ID 与大纲级别。"""
    style = ''
    outline = ''
    for child in node.iter():
        name = _local(child.tag)
        if name == 'pStyle':
            style = child.get(W + 'val') or child.get('val') or ''
        elif name == 'outlineLvl':
            outline = child.get(W + 'val') or child.get('val') or ''
    return style, outline


def _paragraph_list(node):
    """段落编号信息（w:numPr → numId/ilvl），用于说明“编号标题”判定依据。"""
    num_id = ''
    level = ''
    for child in node.iter():
        name = _local(child.tag)
        if name == 'numId':
            num_id = child.get(W + 'val') or child.get('val') or ''
        elif name == 'ilvl':
            level = child.get(W + 'val') or child.get('val') or ''
    return num_id, level


def _heading_level(style, outline, text):
    """标题层级判定：pStyle → outlineLvl（文本编号兜底仅作提示，不当作标题）。

    返回 (level, source)；level=0 表示不是标题。来源会写进事实的 levelSource 字段。
    """
    match = _HEADING_STYLE.match(style or '')
    if match:
        level = int(match.group(2)) if match.group(2) else 1
        return min(max(level, 1), _MAX_HEADING_LEVEL), 'pStyle'
    if outline:
        level_match = _OUTLINE_LEVEL.match(str(outline))
        if level_match:
            return min(int(level_match.group(1)) + 1, _MAX_HEADING_LEVEL), 'outlineLvl'
    return 0, ''


def _has_document_part(entry_names):
    """DOCX 必需部件检查。"""
    return 'word/document.xml' in entry_names


def _table_rows(table_node):
    """表格 → 行 → 单元格文本；标注 gridSpan/vMerge。"""
    rows = []
    for row in table_node:
        if _local(row.tag) != 'tr':
            continue
        cells = []
        for cell in row:
            if _local(cell.tag) != 'tc':
                continue
            text_parts = []
            for paragraph in cell:
                if _local(paragraph.tag) == 'p':
                    text = _paragraph_text(paragraph)
                    if text:
                        text_parts.append(text)
            span = 1
            merged = ''
            for prop in cell.iter():
                name = _local(prop.tag)
                if name == 'gridSpan':
                    try:
                        span = int(prop.get(W + 'val') or prop.get('val') or '1')
                    except (TypeError, ValueError):
                        span = 1
                elif name == 'vMerge':
                    merged = prop.get(W + 'val') or prop.get('val') or 'continue'
            cells.append({'text': '\n'.join(text_parts), 'gridSpan': span, 'vMerge': merged})
        rows.append(cells)
    return rows


def parse(path, material_id, rel_path=''):
    """解析 DOCX：段落 / 标题 / 表格，按文档顺序编号（block）。"""
    rel = rel_of(path, rel_path)
    try:
        with open(str(path), 'rb') as handle:
            head = handle.read(8)
    except (OSError, IOError) as exc:
        return failure('读取文件失败：%s' % exc)
    if head.startswith(_DOC_MAGIC):
        return failure('旧版 .doc 建议转换为 .docx 后上传（当前不支持 OLE 复合文档解析）。',
                       coverage={'modules': [], 'notes': [], 'failedSegments': [
                           failed_segment('document', {'kind': 'docx', 'file': rel, 'section': '',
                                                       'block': 0},
                                          '检测到 OLE 复合文档（.doc）魔数，未解析任何内容。')]})

    sink = FactSink(material_id)
    notes, failed, warnings = [], [], []
    try:
        with zipfile.ZipFile(str(path)) as archive:
            names = set(archive.namelist())
            if not _has_document_part(names):
                return failure('不是有效的 DOCX（缺少 word/document.xml，可能是 .doc 改名或其他 OOXML 文档）。')
            raw = archive.read('word/document.xml')
            extras = {
                'header': sorted(name for name in names if re.match(r'word/header\d*\.xml$', name)),
                'footer': sorted(name for name in names if re.match(r'word/footer\d*\.xml$', name)),
                'footnotes': [name for name in names if name == 'word/footnotes.xml'],
                'comments': [name for name in names if re.match(r'word/comments\d*\.xml$', name)],
                'macros': [name for name in names if name.lower().endswith('vbaproject.bin')],
                'embeddings': sorted(name for name in names if name.startswith('word/embeddings/')),
                'media': sorted(name for name in names if name.startswith('word/media/')),
            }
    except zipfile.BadZipFile as exc:
        return failure('DOCX 不是有效的 ZIP 包：%s' % exc)
    except (OSError, IOError) as exc:
        return failure('读取 DOCX 失败：%s' % exc)
    except KeyError as exc:
        return failure('DOCX 缺少必要部件：%s' % exc)

    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        return failure('word/document.xml 解析失败（文档可能损坏）：%s' % exc)

    body = None
    for child in root:
        if _local(child.tag) == 'body':
            body = child
            break
    if body is None:
        return failure('DOCX 结构异常：document.xml 中未找到 w:body。')

    blocks = 0
    headings = []            # [(level, text)]
    paragraph_count = 0
    table_count = 0
    empty_paragraphs = 0
    for node in body:
        name = _local(node.tag)
        if name == 'p':
            blocks += 1
            text = _paragraph_text(node)
            style, outline = _paragraph_style(node)
            if not text:
                empty_paragraphs += 1
                continue
            level, source = _heading_level(style, outline, text)
            if level:
                headings = [(item_level, item_text) for item_level, item_text in headings
                            if item_level < level]
                headings.append((level, text))
                section = ' / '.join(item[1] for item in headings)
                sink.add('docx', {'kind': 'docx', 'file': rel, 'section': section, 'block': blocks},
                         text, 'heading',
                         {'level': level, 'style': style, 'outlineLevel': outline,
                          'levelSource': source, 'text': text, 'block': blocks}, 'high')
                continue
            section = ' / '.join(item[1] for item in headings)
            data = {'text': text, 'style': style, 'outlineLevel': outline, 'block': blocks,
                    'chars': len(text)}
            if _NUMERIC_PREFIX.match(text) and level == 0:
                data['numberedLikeHeading'] = True
            sink.add('docx', {'kind': 'docx', 'file': rel, 'section': section, 'block': blocks},
                     text, 'paragraph', data, 'high')
            paragraph_count += 1
        elif name == 'tbl':
            blocks += 1
            table_count += 1
            rows = _table_rows(node)
            section = ' / '.join(item[1] for item in headings)
            cell_count = sum(len(row) for row in rows)
            if cell_count > _MAX_CELLS_PER_TABLE:
                notes.append('第 %d 个块（表格）单元格数 %d 超过上限 %d，仅登记前 %d 个单元格文本。'
                             % (blocks, cell_count, _MAX_CELLS_PER_TABLE, _MAX_CELLS_PER_TABLE))
                failed.append(failed_segment(
                    'block', {'kind': 'docx', 'file': rel, 'section': section, 'block': blocks},
                    '表格过大，单元格事实被截断。'))
            emitted = 0
            header = rows[0] if rows else []
            header_text = [cell['text'] for cell in header]
            sink.add('docx', {'kind': 'docx', 'file': rel, 'section': section, 'block': blocks},
                     ' | '.join(header_text)[:400] or '(表格无表头文本)', 'table',
                     {'rows': len(rows), 'columns': max((len(row) for row in rows), default=0),
                      'header': header_text, 'block': blocks,
                      'firstRow': [[cell['text'] for cell in row] for row in rows[:3]],
                      'hasMergedCells': any(cell['gridSpan'] > 1 or cell['vMerge']
                                            for row in rows for cell in row)}, 'high')
            for row_index, row in enumerate(rows, start=1):
                for column_index, cell in enumerate(row, start=1):
                    if emitted >= _MAX_CELLS_PER_TABLE:
                        break
                    text = cell['text']
                    if not text:
                        continue
                    emitted += 1
                    if row_index == 1:
                        role = 'header'
                    else:
                        role = 'body'
                    header_name = header_text[column_index - 1] if column_index - 1 < len(header_text) \
                        else ''
                    sink.add('docx',
                             {'kind': 'docx', 'file': rel, 'section': section, 'block': blocks},
                             '%s%s' % (('%s：' % header_name) if header_name and role == 'body' else '',
                                       text)[:400],
                             'tableCell',
                             {'row': row_index, 'column': column_index, 'role': role,
                              'columnName': header_name, 'text': text,
                              'gridSpan': cell['gridSpan'], 'vMerge': cell['vMerge'],
                              'block': blocks}, 'high' if role == 'header' else 'medium')
            if not rows:
                failed.append(failed_segment(
                    'block', {'kind': 'docx', 'file': rel, 'section': section, 'block': blocks},
                    '表格内未解析出任何行。'))
        elif name in ('sdt',):
            # 内容控件：尽力展开内部段落，失败记 failedSegments
            inner = [child for child in node.iter() if _local(child.tag) == 'p']
            if not inner:
                failed.append(failed_segment(
                    'block', {'kind': 'docx', 'file': rel, 'section':
                              ' / '.join(item[1] for item in headings), 'block': blocks + 1},
                    '内容控件（w:sdt）内未找到可解析段落。'))
            for paragraph in inner:
                blocks += 1
                text = _paragraph_text(paragraph)
                if not text:
                    continue
                style, outline = _paragraph_style(paragraph)
                section = ' / '.join(item[1] for item in headings)
                sink.add('docx', {'kind': 'docx', 'file': rel, 'section': section, 'block': blocks},
                         text, 'paragraph',
                         {'text': text, 'style': style, 'outlineLevel': outline,
                          'inContentControl': True}, 'medium')

    if empty_paragraphs:
        notes.append('跳过 %d 个空段落（无文本节点，不产出事实）。' % empty_paragraphs)
    if extras['macros']:
        warnings.append('文档包含宏部件（%s）：按安全边界不读取、不解析、不执行。'
                        % '、'.join(extras['macros']))
        sink.add('docx', {'kind': 'docx', 'file': rel, 'section': '', 'block': 0}, '（宏部件）',
                 'warning', {'macros': extras['macros'],
                             'note': '宏未读取、未执行'}, 'low')
    for label, key, description in (
            ('页眉', 'header', '页眉内容未纳入事实（页面级结构，可能包含页码等噪声）'),
            ('页脚', 'footer', '页脚内容未纳入事实（页面级结构，可能包含页码等噪声）'),
            ('脚注', 'footnotes', '脚注内容未纳入事实'),
            ('批注', 'comments', '批注内容未纳入事实（协作痕迹，不是文档正文）')):
        if extras[key]:
            notes.append('%s部件 %d 个：%s。' % (label, len(extras[key]), description))
    if extras['embeddings']:
        notes.append('内嵌 OLE 对象 %d 个：未解析、未执行。' % len(extras['embeddings']))
        failed.append(failed_segment(
            'document', {'kind': 'docx', 'file': rel, 'section': '', 'block': 0},
            '内嵌对象 %d 个（word/embeddings/）未解析：需要人工提供对应源文件。'
            % len(extras['embeddings'])))
    if extras['media']:
        notes.append('图片 %d 个：未做图像内容识别（不宣称解析图片中的文字）。' % len(extras['media']))

    notes.append('已按文档顺序处理 %d 个块（段落 %d、表格 %d）。' % (blocks, paragraph_count, table_count))
    notes.append('定位为「标题路径 + 块序号」（DOCX 无稳定页码），标题层级来源已在 heading 事实的 '
                 'levelSource 中标注。')
    if not sink.facts:
        return finish(sink, ['docx'], notes + ['DOCX 中未提取到任何段落或表格文本。'],
                      failed, partial=True)
    return finish(sink, ['docx'], notes, failed, partial=bool(failed), warnings=warnings,
                  tableCount=table_count, paragraphCount=paragraph_count)
