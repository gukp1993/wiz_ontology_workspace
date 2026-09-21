"""XLSX 解析：多 sheet、表头、单元格、公式文本与缓存值（zipfile 直读 OOXML）。

能力边界（支持 / 降级 / 不执行）
--------------------------------
* 支持：用标准库 `zipfile` + `xml.etree.ElementTree` 直读 `xl/workbook.xml`（sheet 名/顺序）、
  `xl/_rels/workbook.xml.rels`（sheet → 部件路径）、`xl/sharedStrings.xml`（共享字符串，
  含富文本 `r/t`）、`xl/worksheets/sheetN.xml`（`<c r="B3" t="s|str|inlineStr|b|e|n">`、
  `<v>` 缓存值、`<f>` 公式文本、`<is>` 内联字符串、合并单元格 `<mergeCell>`、`dimension`）。
  产出：sheet 名、表头行（首个非空行）与列名、每个非空单元格（A1 坐标）、公式文本与缓存值。
  部件读取走 `zipguard.CappedZipFile`：按**实际解压字节**计数封顶（单部件 + 整包累计），
  不信任 header 声明值，超限显式失败而不是解压进内存（D12 解析侧）。
* 降级（写入 notes / warnings / failedSegments）：
  - `<f>` 有公式但无 `<v>` 缓存 → 事实 quality='low' 且 `formulaCacheMissing=True`，
    标注「缺公式缓存，不当作计算结果」，**不自行计算公式**。
  - 公式计算链涉及外部引用/外链工作簿（`xl/externalLinks/*`）→ 忽略并记 notes；
    单元格 `<v>` 为 `#REF!`/`#VALUE!` 等错误值 → 标注 `cachedError`。
  - 日期/时间单元格（`numFmtId` 为日期格式）→ 只记录序列值与 `isDateLike=True`，
    不猜测时区或具体日期（不假定 1900/1904 基准）。
  - `.xls`（OLE 魔数）→ failure('旧版 .xls 建议转换为 .xlsx 后上传')。
  - 宏（`xl/vbaProject.bin`）、图片（`xl/media/*`）、图表（`xl/charts/*`）、
    数据透视表、条件格式、数据验证：不解析、不执行，只在 notes 中说明数量。
  - 单元格数超过单文件软上限时截断并说明。
* 不执行：不执行公式、不执行宏、不打开外链工作簿、不访问网络、不依赖 openpyxl
  （安装了也不用，避免与手写实现产生口径差异）。

定位器：`{'kind':'xlsx','file':rel_path,'sheet':sheet 名,'cell':'B3'}`；
sheet 级事实（表头行等）cell 为 ''。
"""

import re
import zipfile
import xml.etree.ElementTree as ET

from workbench.ontology_build.parsers.base import failure
from workbench.ontology_build.parsers import zipguard
from workbench.ontology_build.parsers.textline import FactSink, failed_segment, finish, rel_of

NS = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
NS_R = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
NS_PKG_REL = '{http://schemas.openxmlformats.org/package/2006/relationships}'
_DOC_MAGIC = b'\xd0\xcf\x11\xe0'
MAX_CELLS = 50000                # 单文件单元格事实上限（软上限，超出截断并说明）
MAX_SHARED_STRINGS = 200000      # 共享字符串表上限（防超大打满内存）
MAX_CELL_TEXT = 400
FORMULA_ERRORS = ('#REF!', '#VALUE!', '#DIV/0!', '#NAME?', '#NULL!', '#NUM!', '#N/A', '#GETTING_DATA')
_DATE_FORMAT_HINTS = (
    r'(?i)\by{2,4}\b', r'(?i)\bm{1,5}\b', r'(?i)\bd{1,4}\b', r'(?i)\bh{1,2}\b', r'(?i)\bs{1,2}\b',
)


def _local(tag):
    return tag.rsplit('}', 1)[-1] if isinstance(tag, str) and '}' in tag else str(tag)


def _column_index(reference):
    """A1 坐标 → 列号（1 基）；非法返回 0。"""
    match = re.match(r'^([A-Za-z]+)', str(reference or ''))
    if not match:
        return 0
    value = 0
    for char in match.group(1).upper():
        value = value * 26 + (ord(char) - 64)
    return value


def _row_index(reference):
    match = re.search(r'(\d+)$', str(reference or ''))
    return int(match.group(1)) if match else 0


def _split_reference(reference):
    """A1 → (列号, 行号, 列字母)。"""
    match = re.match(r'^\$?([A-Za-z]+)\$?(\d+)$', str(reference or '').strip())
    if not match:
        return 0, 0, ''
    letters = match.group(1).upper()
    value = 0
    for char in letters:
        value = value * 26 + (ord(char) - 64)
    return value, int(match.group(2)), letters


def _shared_strings(archive, names):
    """读取共享字符串表；缺失返回 []，解析失败记显式失败。"""
    if 'xl/sharedStrings.xml' not in names:
        return [], ''
    try:
        root = xml_root(archive.read('xl/sharedStrings.xml'))
    except zipguard.ZipBombDetected:
        raise                  # 解压预算耗尽：整份工作簿不可信，交由上层显式失败
    except Exception as exc:  # noqa: BLE001 - 损坏的共享字符串必须显式报告
        return [], '共享字符串表（xl/sharedStrings.xml）解析失败：%s: %s' \
                   % (exc.__class__.__name__, str(exc)[:160])
    values = []
    for item in root:
        if _local(item.tag) != 'si':
            continue
        if len(values) >= MAX_SHARED_STRINGS:
            break
        parts = []
        for node in item.iter():
            if _local(node.tag) == 't':
                parts.append(node.text or '')
        values.append(''.join(parts))
    return values, ''


_STANDARD_NS = (
    ('xmlns', 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'),
    ('xmlns:r', 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'),
    ('xmlns:mc', 'http://schemas.openxmlformats.org/markup-compatibility/2006'))


def xml_root(data):
    """解析 OOXML 部件；对未声明命名空间前缀的生成器产物做一次显式修复后重试。

    第三方导出工具偶尔省略某个前缀声明（典型：用 r:id 却不声明 xmlns:r），
    ElementTree 会以 "unbound prefix" 直接失败。这里只在**解析失败时**给根元素
    补上缺失的标准声明（已声明的不重复注入，避免 duplicate attribute），
    不改写任何内容；仍然失败则原样抛出，让上层按损坏文件报告。
    """
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        if 'unbound prefix' not in str(exc):
            raise
    import re as _re
    match = _re.search(rb'<(?![?!])[^>]*?>', data)
    if not match:
        raise
    tag = match.group(0)
    missing = [('%s="%s"' % (name, uri)).encode('utf-8')
               for name, uri in _STANDARD_NS if (name + '=').encode('utf-8') not in tag]
    if not missing:
        raise
    injected = data[:match.end() - 1] + b' ' + b' '.join(missing) + b'>' + data[match.end():]
    return ET.fromstring(injected)


def _sheet_paths(archive, names):
    """sheet name → 部件路径（按 workbook.xml 顺序 + rels 映射）。"""
    info = []
    workbook = xml_root(archive.read('xl/workbook.xml'))
    rels = {}
    if 'xl/_rels/workbook.xml.rels' in names:
        rel_root = xml_root(archive.read('xl/_rels/workbook.xml.rels'))
        for relationship in rel_root:
            rels[relationship.get('Id')] = relationship.get('Target') or ''
    for sheets in workbook:
        if _local(sheets.tag) != 'sheets':
            continue
        for sheet in sheets:
            if _local(sheet.tag) != 'sheet':
                continue
            name = sheet.get('name') or ''
            relationship_id = sheet.get(NS_R + 'id') or ''
            target = rels.get(relationship_id, '')
            state = sheet.get('state') or 'visible'
            path = target
            if path and not path.startswith('/'):
                path = 'xl/' + path.lstrip('./')
            elif path.startswith('/'):
                path = path.lstrip('/')
            info.append({'name': name, 'path': path, 'sheetId': sheet.get('sheetId') or '',
                         'state': state, 'relationshipId': relationship_id})
    return info


def _cell_value(cell, shared, formats, notes):
    """单元格 → (显示文本, 原始类型, 公式文本, 缓存值, 元信息)。"""
    raw_type = cell.get('t') or 'n'
    formula = ''
    cached = ''
    inline = ''
    for child in cell:
        name = _local(child.tag)
        if name == 'f':
            formula = (child.text or '').strip()
        elif name == 'v':
            cached = (child.text or '').strip()
        elif name == 'is':
            parts = []
            for node in child.iter():
                if _local(node.tag) == 't':
                    parts.append(node.text or '')
            inline = ''.join(parts)
    meta = {}
    if raw_type == 's':
        try:
            index = int(cached)
            text = shared[index] if 0 <= index < len(shared) else ''
            if not (0 <= index < len(shared)):
                notes.append('共享字符串索引 %d 越界（表大小 %d），该单元格按空值处理。'
                             % (index, len(shared)))
        except (TypeError, ValueError):
            text = ''
            notes.append('共享字符串索引 `%s` 不是整数，该单元格按空值处理。' % cached)
    elif raw_type == 'inlineStr':
        text = inline
    elif raw_type == 'str':
        text = cached
    elif raw_type == 'b':
        text = 'TRUE' if cached in ('1', 'true', 'TRUE') else 'FALSE'
    elif raw_type == 'e':
        text = cached
        meta['cachedError'] = cached
    else:
        text = cached
    if formula:
        meta['formula'] = formula
        meta['formulaCache'] = cached
        meta['formulaCacheMissing'] = (cached == '')
        if cached in FORMULA_ERRORS:
            meta['cachedError'] = cached
    style = cell.get('s')
    if style is not None:
        meta['styleIndex'] = style
        number_format = formats.get(str(style))
        if number_format:
            meta['numberFormat'] = number_format
            if _is_date_like(number_format):
                meta['isDateLike'] = True
    return text, raw_type, formula, cached, meta


def _is_date_like(number_format):
    """粗略判定日期/时间格式（只看格式串特征，不推断具体日期值）。"""
    text = str(number_format or '')
    if not text:
        return False
    if re.search(r'[yYmMdDhHsS]', text) and not re.search(r'[#0]', text):
        return True
    return bool(re.search(r'(\[?-?\]?)(yy|yyyy|mm?|dd?|hh?|ss?)', text))


def _number_formats(archive, names):
    """style 索引 → numberFormat 代码（只取自定义与内置映射中出现的项）。"""
    if 'xl/styles.xml' not in names:
        return {}
    try:
        root = xml_root(archive.read('xl/styles.xml'))
    except zipguard.ZipBombDetected:
        raise                  # 解压预算耗尽属于安全边界，不能被样式表降级吞掉
    except Exception:  # noqa: BLE001 - 样式表损坏不影响单元格值
        return {}
    custom = {}
    cell_xfs = []
    for child in root:
        name = _local(child.tag)
        if name == 'numFmts':
            for fmt in child:
                custom[fmt.get('numFmtId') or ''] = fmt.get('formatCode') or ''
        elif name == 'cellXfs':
            for xf in child:
                cell_xfs.append(xf)
    builtin = {'14': 'm/d/yyyy', '15': 'd-mmm-yy', '16': 'd-mmm', '17': 'mmm-yy',
               '18': 'h:mm AM/PM', '19': 'h:mm:ss AM/PM', '20': 'h:mm', '21': 'h:mm:ss',
               '22': 'm/d/yyyy h:mm', '45': 'mm:ss', '46': '[h]:mm:ss', '47': 'mmss.0'}
    result = {}
    for index, xf in enumerate(cell_xfs):
        num_fmt_id = xf.get('numFmtId') or ''
        code = custom.get(num_fmt_id) or builtin.get(num_fmt_id, '')
        if code:
            result[str(index)] = code
    return result


def parse(path, material_id, rel_path=''):
    """解析 XLSX：sheet、表头、单元格、公式与缓存值。"""
    rel = rel_of(path, rel_path)
    try:
        with open(str(path), 'rb') as handle:
            head = handle.read(8)
    except (OSError, IOError) as exc:
        return failure('读取文件失败：%s' % exc)
    if head.startswith(_DOC_MAGIC):
        return failure('旧版 .xls 建议转换为 .xlsx 后上传（当前不支持 OLE 复合文档解析）。',
                       coverage={'modules': [], 'notes': [], 'failedSegments': [
                           failed_segment('document', {'kind': 'xlsx', 'file': rel, 'sheet': '',
                                                       'cell': ''},
                                          '检测到 OLE 复合文档（.xls）魔数，未解析任何内容。')]})

    sink = FactSink(material_id)
    notes, failed, warnings = [], [], []
    try:
        with zipguard.open_capped(str(path)) as archive:
            names = set(archive.namelist())
            if 'xl/workbook.xml' not in names:
                return failure('不是有效的 XLSX（缺少 xl/workbook.xml）。')
            shared, shared_error = _shared_strings(archive, names)
            if shared_error:
                failed.append(failed_segment(
                    'document', {'kind': 'xlsx', 'file': rel, 'sheet': '', 'cell': ''},
                    shared_error))
                warnings.append('共享字符串表不可用：文本型单元格可能缺失。')
            formats = _number_formats(archive, names)
            try:
                sheets = _sheet_paths(archive, names)
            except ET.ParseError as exc:
                return failure('xl/workbook.xml 解析失败（文件可能损坏）：%s' % exc)

            externals = sorted(name for name in names if name.startswith('xl/externalLinks/'))
            macros = [name for name in names if name.lower().endswith('vbaproject.bin')]
            media = sorted(name for name in names if name.startswith('xl/media/'))
            charts = sorted(name for name in names if name.startswith('xl/charts/'))
            pivots = sorted(name for name in names if name.startswith('xl/pivot'))
            if externals:
                notes.append('外链工作簿部件 %d 个（xl/externalLinks/）：按安全边界忽略，不读取外链内容。'
                             % len(externals))
            if macros:
                warnings.append('工作簿包含宏部件（%s）：不读取、不解析、不执行。' % '、'.join(macros))
                sink.add('xlsx', {'kind': 'xlsx', 'file': rel, 'sheet': '', 'cell': ''},
                         '（宏部件）', 'warning', {'macros': macros, 'note': '宏未读取、未执行'},
                         'low')
            if charts:
                notes.append('图表部件 %d 个：不解析图表数据系列。' % len(charts))
            if pivots:
                notes.append('数据透视表部件 %d 个：不解析透视定义。' % len(pivots))
            if media:
                notes.append('图片 %d 个：未做图像内容识别。' % len(media))

            cell_count = 0
            truncated = False
            formula_missing = 0
            formula_total = 0
            for sheet in sheets:
                if not sheet['path'] or sheet['path'] not in names:
                    failed.append(failed_segment(
                        'sheet', {'kind': 'xlsx', 'file': rel, 'sheet': sheet['name'], 'cell': ''},
                        '未找到该 sheet 的部件（%s），未解析内容。' % (sheet['path'] or '无路径')))
                    continue
                try:
                    root = xml_root(archive.read(sheet['path']))
                except zipguard.ZipBombDetected:
                    raise      # 解压预算耗尽：继续解析其他 sheet 也不会得到可信结果
                except Exception as exc:  # noqa: BLE001 - 单 sheet 失败不中断其他 sheet
                    failed.append(failed_segment(
                        'sheet', {'kind': 'xlsx', 'file': rel, 'sheet': sheet['name'], 'cell': ''},
                        '工作表 XML 解析失败：%s: %s' % (exc.__class__.__name__, str(exc)[:160])))
                    continue
                if sheet['state'] != 'visible':
                    notes.append('sheet「%s」状态为 %s（隐藏/非常可见），仍纳入解析但请人工确认。'
                                 % (sheet['name'], sheet['state']))
                rows, merges, dimension = _read_sheet(root)
                _fill_cell_values(rows, shared, formats, notes)
                header_row = None
                header_values = []
                for row_number in sorted(rows):
                    values = rows[row_number]
                    if any(value['text'] for value in values.values()):
                        header_row = row_number
                        header_values = [values[index]['text'] if index in values else ''
                                         for index in range(1, max(values) + 1)]
                        break
                sink.add('xlsx', {'kind': 'xlsx', 'file': rel, 'sheet': sheet['name'], 'cell': ''},
                         ' | '.join(item for item in header_values if item)[:MAX_CELL_TEXT]
                         or '（无表头文本）', 'sheet',
                         {'name': sheet['name'], 'path': sheet['path'], 'state': sheet['state'],
                          'headerRow': header_row or 0, 'header': header_values,
                          'rows': len(rows), 'dimension': dimension, 'merges': merges[:200],
                          'mergeCount': len(merges), 'sharedStrings': len(shared)},
                         'high')
                header_by_column = {index + 1: value for index, value in enumerate(header_values)}
                for row_number in sorted(rows):
                    values = rows[row_number]
                    for column_number in sorted(values):
                        cell = values[column_number]
                        text = cell['text']
                        meta = cell['meta']
                        if not text and 'formula' not in meta:
                            continue  # 完全空且无公式的单元格不产事实（避免噪声）
                        if cell_count >= MAX_CELLS:
                            truncated = True
                            break
                        cell_count += 1
                        quality = 'high'
                        cell_notes = []
                        if meta.get('formula'):
                            formula_total += 1
                            if meta.get('formulaCacheMissing'):
                                formula_missing += 1
                                quality = 'low'
                                cell_notes.append('缺公式缓存，不当作计算结果')
                            if meta.get('cachedError'):
                                quality = 'low'
                                cell_notes.append('缓存值为错误值 %s' % meta['cachedError'])
                            if quality == 'high':
                                quality = 'medium'
                                cell_notes.append('公式与缓存值均保留；公式未执行，缓存值来自文件')
                        if meta.get('isDateLike'):
                            cell_notes.append('日期/时间格式：仅记录原始序列值，不推断具体日期')
                        reference = cell['reference']
                        column_name = header_by_column.get(column_number, '')
                        data = {
                            'sheet': sheet['name'], 'cell': reference,
                            'row': row_number, 'column': column_number,
                            'columnLetter': cell['columnLetter'],
                            'columnName': column_name,
                            'role': 'header' if row_number == header_row else 'body',
                            'text': text, 'cellType': cell['rawType'],
                            'value': text,
                            'isFormula': bool(meta.get('formula')),
                        }
                        data.update({key: value for key, value in meta.items()})
                        if cell_notes:
                            data['qualityNotes'] = cell_notes
                        sink.add('xlsx',
                                 {'kind': 'xlsx', 'file': rel, 'sheet': sheet['name'],
                                  'cell': reference},
                                 _cell_snippet(cell, column_name), 'formula' if meta.get('formula')
                                 else ('header' if row_number == header_row else 'cell'),
                                 data, quality)
                    if truncated:
                        break
                if truncated:
                    break

    except zipguard.ZipBombDetected as exc:
        message = 'XLSX 解压超过安全上限，读取已中止：%s' % exc
        return failure(message, coverage={
            'modules': ['xlsx'], 'notes': [message, zipguard.limit_note()],
            'failedSegments': [failed_segment(
                'document', {'kind': 'xlsx', 'file': rel, 'sheet': '', 'cell': ''}, message)]})
    except zipfile.BadZipFile as exc:
        return failure('XLSX 不是有效的 ZIP 包：%s' % exc)
    except (OSError, IOError) as exc:
        return failure('读取 XLSX 失败：%s' % exc)

    if truncated:
        notes.append('单元格数达到单文件上限 %d，其余单元格未解析（可拆分工作簿后重试）。' % MAX_CELLS)
    if formula_missing:
        warnings.append('存在 %d 个公式单元格缺少缓存值：不能当作计算结果，需要打开文件重算或提供取值。'
                        % formula_missing)
    if formula_total:
        notes.append('公式单元格 %d 个（缺缓存 %d 个）：公式文本与缓存值均按原文保留，公式未执行。'
                     % (formula_total, formula_missing))
    notes.append('已解析 %d 个 sheet；定位为「sheet + 单元格坐标（A1）」，表头行取每 sheet 首个非空行。' % len(sheets))
    notes.append(zipguard.limit_note())
    notes.append('未执行：不计算公式、不执行宏、不打开外链工作簿、不访问网络、不依赖 openpyxl。')
    if not sink.facts:
        return finish(sink, ['xlsx'], notes + ['工作簿中没有可读单元格。'], failed, partial=True,
                      warnings=warnings)
    return finish(sink, ['xlsx'], notes, failed, partial=bool(failed or truncated),
                  warnings=warnings, sheetCount=len(sheets), cellCount=cell_count,
                  formulaCount=formula_total, formulaMissingCache=formula_missing)


def _cell_snippet(cell, column_name):
    """单元格片段：带表头名与公式标记，长度受限。"""
    text = cell['text']
    meta = cell['meta']
    label = ''
    if column_name and meta.get('formula'):
        label = '%s（公式）=' % column_name
    elif column_name:
        label = '%s=' % column_name
    body = (label + text)[:MAX_CELL_TEXT]
    if meta.get('formula') and not text:
        body = (body + '[无缓存值]')[:MAX_CELL_TEXT]
    return body or (cell['reference'] + '（空/仅格式）')


def _read_sheet(root):
    """工作表 XML → ({行号: {列号: 单元格}}, 合并区间, dimension)。

    sheetData 内的 `<c>` 按 `r` 属性定位；缺失 `r` 时按出现顺序与上一行推断列（并记入 notes
    由调用方说明），保证不静默错位。
    """
    rows = {}
    merges = []
    dimension = ''
    for child in root:
        name = _local(child.tag)
        if name == 'dimension':
            dimension = child.get('ref') or ''
        elif name == 'mergeCells':
            for merge in child:
                merges.append(merge.get('ref') or '')
        elif name == 'sheetData':
            for row in child:
                if _local(row.tag) != 'row':
                    continue
                row_number = row.get('r')
                try:
                    row_number = int(row_number) if row_number else 0
                except ValueError:
                    row_number = 0
                cells = {}
                auto_column = 0
                for cell in row:
                    if _local(cell.tag) != 'c':
                        continue
                    reference = cell.get('r') or ''
                    column_number, parsed_row, letters = _split_reference(reference)
                    if not column_number:
                        auto_column += 1
                        column_number = auto_column
                        letters = _letters(column_number)
                        parsed_row = row_number
                    if not parsed_row:
                        parsed_row = row_number
                    if not row_number:
                        row_number = parsed_row
                    cells[column_number] = {
                        'reference': '%s%d' % (letters, parsed_row or row_number),
                        'row': parsed_row or row_number,
                        'column': column_number,
                        'columnLetter': letters,
                        'noReference': not bool(reference),
                        'node': cell,
                        'text': '',
                        'rawType': cell.get('t') or 'n',
                        'meta': {},
                    }
                rows[row_number] = cells
    return rows, merges, dimension


def _letters(column_number):
    value = int(column_number)
    letters = ''
    while value > 0:
        value, remainder = divmod(value - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def _fill_cell_values(rows, shared, formats, notes):
    """把单元格节点解析成文本与元信息（保持 sheet 内的行/列顺序）。"""
    for row_number in sorted(rows):
        for column_number in sorted(rows[row_number]):
            cell = rows[row_number][column_number]
            text, raw_type, formula, cached, meta = _cell_value(cell['node'], shared, formats, notes)
            cell['text'] = text
            cell['rawType'] = raw_type
            cell['meta'] = meta
            if cell['noReference']:
                notes.append('第 %d 行存在缺少 r 属性的单元格，列号按出现顺序推断为 %s（可能错位）。'
                             % (row_number, cell['reference']))
