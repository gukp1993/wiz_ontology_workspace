"""CSV / TSV 专用解析：表头事实、每列统计事实、总行数事实（含采样截断注记）。

能力边界（支持 / 降级 / 不执行）
--------------------------------
* 支持（纯标准库 `csv`）：分隔符嗅探（逗号 / 分号 / 制表符 / 竖线，以**首行各分隔符列数的
  众数**决定，嗅探不可靠时按后缀回退：`.tsv`→制表符，其余→逗号）；表头列名事实；每列一条统计
  事实（列名、非空计数、≤3 个**去重样本值**、按取值推断的类型）；总行数事实；定位器带真实行号
  （表头事实=表头行号、列统计事实=首条数据行号、总行数事实=第 1 行）。
* 采样（需求 §2 硬口径）：**超过 100 行仅采样前 100 行**（`SAMPLE_ROWS=100`），
  `coverage.notes` 明确写出截断范围（已统计区间 / 未统计行数），列统计事实内也带同样注记，
  超过部分**不猜内容**。
* 降级（写 notes / failedSegments，不静默）：字段数少于表头列数的行记 failedSegments
  （不做缺失值猜测，但已解析出的字段仍参与统计）；超过表头列数的行按多余字段丢弃并记录；
  分隔符嗅探结果与后缀不一致时在 notes 说明实际采用值。
* 不执行：不执行单元格内的公式/表达式（作为纯文本）、不推断外键、不访问外链、不做编码转换
  以外的任何加工。

定位器：`{'kind':'csv','file':rel,'line':行号}`（表头/列统计/总行数三类事实均带真实行号）。
"""

import csv
import io

from workbench.ontology_build.parsers import structwalk
from workbench.ontology_build.parsers import textline
from workbench.ontology_build.parsers.base import failure
from workbench.ontology_build.parsers.textline import FactSink, failed_segment, finish, rel_of

SAMPLE_ROWS = 100          # 超过该行数仅采样前 N 行做列统计（并在注记中说明截断范围）
SAMPLE_VALUES = 3          # 每列样本值上限
CELL_CHARS = 200           # 单元格入事实的字符上限
SNIFF_LINES = 20           # 分隔符嗅探参与的行数
CANDIDATE_DELIMITERS = (',', ';', '\t', '|')
NUMBER_HEAD_CHARS = '+-.'
# 布尔字面量：只认明确的真/假写法（`1`/`0` 归数字，避免整型列被误判为 boolean）
_BOOL_LITERALS = ('true', 'false', 'yes', 'no', 'y', 'n', '是', '否')


def _split_rows(text, delimiter):
    """按分隔符切分（保留引号与换行语义），返回行列表（含表头）。"""
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    return [row for row in reader]


def sniff_delimiter(text, rel_path):
    """分隔符嗅探：返回 `(分隔符, 注记列表)`。

    按前 `SNIFF_LINES` 行统计每个候选分隔符的列数，取「列数 >1 且众数最大」者；
    无法判定时按后缀回退（`.tsv`→制表符，其余→逗号），并在注记里写明。
    """
    notes = []
    lines = [line for line in str(text or '').splitlines() if line.strip()][:SNIFF_LINES]
    if not lines:
        return ',', ['文件无可读行，分隔符按逗号默认处理。']
    counts = {}
    for delimiter in CANDIDATE_DELIMITERS:
        try:
            rows = [row for row in csv.reader(io.StringIO('\n'.join(lines)), delimiter=delimiter)]
        except csv.Error:
            continue
        widths = [len(row) for row in rows if row]
        if not widths:
            continue
        mode = {}
        for width in widths:
            mode[width] = mode.get(width, 0) + 1
        best_width = max(mode.items(), key=lambda item: (item[1], item[0]))
        if best_width[0] > 1:
            counts[delimiter] = (best_width[1], best_width[0])
    name = str(rel_path or '').lower()
    preferred = '\t' if name.endswith('.tsv') else ','
    if not counts:
        notes.append('分隔符嗅探未发现多列结构，按后缀回退为 %s。' % _delimiter_label(preferred))
        return preferred, notes
    ordered = sorted(counts.items(), key=lambda item: (-item[1][0], -item[1][1]))
    chosen = ordered[0][0]
    if len(ordered) > 1 and ordered[0][1] == ordered[1][1] and ordered[1][0] == preferred:
        chosen = preferred
    label = _delimiter_label(chosen)
    if chosen != preferred:
        notes.append('分隔符嗅探结果为 %s（后缀默认 %s）：已按嗅探结果解析。'
                     % (label, _delimiter_label(preferred)))
    else:
        notes.append('分隔符：%s（嗅探与后缀一致）。' % label)
    return chosen, notes


def _delimiter_label(delimiter):
    return {'\t': '制表符（TSV）', ',': '逗号', ';': '分号', '|': '竖线'}.get(delimiter, repr(delimiter))


def _infer_type(values):
    """按样本值推断列类型：integer / double / boolean / dateTime / date / string / empty。"""
    non_empty = [value.strip() for value in values if str(value or '').strip()]
    if not non_empty:
        return 'empty'
    kinds = set()
    for value in non_empty:
        kinds.add(_value_kind(value))
    if kinds == {'integer'}:
        return 'integer'
    if kinds <= {'integer', 'double'}:
        return 'double'
    if kinds == {'boolean'}:
        return 'boolean'
    if kinds == {'dateTime'}:
        return 'dateTime'
    if kinds == {'date'}:
        return 'date'
    return 'string'


def _value_kind(value):
    """单值类型判定顺序：整数 → 浮点 → 日期/时间 → 布尔 → 字符串。"""
    if _is_int(value):
        return 'integer'
    if _is_float(value):
        return 'double'
    date_kind = _date_kind(value)
    if date_kind:
        return date_kind
    if value.lower() in _BOOL_LITERALS:
        return 'boolean'
    return 'string'


def _is_int(value):
    text = value[1:] if value[:1] in '+-' else value
    return bool(text) and text.isdigit()


def _is_float(value):
    try:
        float(value)
    except (TypeError, ValueError):
        return False
    return any(char in value for char in NUMBER_HEAD_CHARS + 'eE')


def _date_kind(value):
    """粗略日期/时间识别（只做形状判断，不解析时区/语义）。"""
    parts = value.split(' ')
    head = parts[0]
    if head.count('-') == 2 and len(head) == 10 and all(
            piece.isdigit() for piece in head.split('-')):
        return 'dateTime' if len(parts) > 1 and ':' in parts[1] else 'date'
    return None


def parse(path, material_id, rel_path=''):
    """解析 CSV/TSV：表头 + 每列统计 + 总行数事实（>100 行采样并注记截断范围）。"""
    rel = rel_of(path, rel_path)
    try:
        text, encoding, size = structwalk.read_document_text(path)
    except (OSError, IOError) as exc:
        return failure('读取文件失败：%s' % exc)
    except ValueError as exc:
        return failure(str(exc))

    delimiter, notes = sniff_delimiter(text, rel)
    try:
        rows = _split_rows(text, delimiter)
    except csv.Error as exc:
        return failure('CSV 解析失败：%s' % exc)
    rows = [row for row in rows if row and any(str(cell).strip() for cell in row)]
    if not rows:
        return finish(FactSink(material_id), ['csv'],
                      notes + ['CSV 没有可解析的非空行。'], partial=True)

    sink = FactSink(material_id)
    failed = []
    header = [str(cell).strip() for cell in rows[0]]
    width = len(header)
    body = rows[1:]
    sampled = body[:SAMPLE_ROWS]
    truncated = len(body) > SAMPLE_ROWS
    total = len(body)

    sink.add('csv', {'kind': 'csv', 'file': rel, 'line': 1},
             '表头：%s' % ' | '.join(header)[:CELL_CHARS], 'csvHeader',
             {'columns': header, 'columnCount': width, 'delimiter': delimiter,
              'rowCount': total, 'sampledRows': len(sampled), 'line': 1}, 'high')

    columns = [[] for _ in range(width)]
    for offset, row in enumerate(sampled, start=2):
        if len(row) < width:
            failed.append(failed_segment(
                'line', {'kind': 'csv', 'file': rel, 'line': offset},
                '字段数少于表头（该行 %d 列，表头 %d 列）：缺列不做猜测，已解析字段仍参与统计。'
                % (len(row), width), ' | '.join(str(cell) for cell in row)[:CELL_CHARS]))
        if len(row) > width:
            failed.append(failed_segment(
                'line', {'kind': 'csv', 'file': rel, 'line': offset},
                '字段数多于表头（该行 %d 列，表头 %d 列）：多余字段未纳入统计。'
                % (len(row), width), ' | '.join(str(cell) for cell in row)[:CELL_CHARS]))
        for index in range(width):
            value = str(row[index]).strip() if index < len(row) else ''
            columns[index].append(value)

    for index, name in enumerate(header, start=1):
        values = columns[index - 1]
        non_empty = [value for value in values if value]
        samples, seen = [], set()
        for value in non_empty:
            if value not in seen:
                seen.add(value)
                samples.append(value[:CELL_CHARS])
            if len(samples) >= SAMPLE_VALUES:
                break
        data = {
            'column': name,
            'index': index,
            'nonEmptyCount': len(non_empty),
            'sampleValues': samples,
            'inferredType': _infer_type(values),
            'sampledRows': len(sampled),
            'rowCount': total,
            'line': 2 if total else 1,
        }
        if truncated:
            data['truncated'] = True
            data['sampleRange'] = '第 2–%d 行' % (SAMPLE_ROWS + 1)
            data['unscannedRows'] = total - SAMPLE_ROWS
        snippet = '列「%s」：非空 %d/%d，样本 %s，推断类型 %s' % (
            name, len(non_empty), len(sampled),
            '、'.join(samples) if samples else '（无样本）', data['inferredType'])
        if truncated:
            snippet += '；仅统计前 %d 行，其余 %d 行未纳入' % (SAMPLE_ROWS, total - SAMPLE_ROWS)
        sink.add('csv', {'kind': 'csv', 'file': rel, 'line': data['line']}, snippet[:400],
                 'csvColumn', data, 'high')

    summary = ('数据行 %d 行、%d 列、分隔符 %s' % (total, width, _delimiter_label(delimiter)))
    if truncated:
        summary += '；列统计仅采样前 %d 行（截断范围：第 2–%d 行已统计，其余 %d 行未统计）' % (
            SAMPLE_ROWS, SAMPLE_ROWS + 1, total - SAMPLE_ROWS)
    summary_data = {'rowCount': total, 'columnCount': width, 'delimiter': delimiter,
                    'sampledRows': len(sampled), 'line': 1, 'truncated': truncated}
    if truncated:
        summary_data['sampleRange'] = '第 2–%d 行' % (SAMPLE_ROWS + 1)
        summary_data['unscannedRows'] = total - SAMPLE_ROWS
    sink.add('csv', {'kind': 'csv', 'file': rel, 'line': 1}, summary, 'csvSummary',
             summary_data, 'high')

    notes.append('已解析 %d 行（表头 1 行 + 数据 %d 行）、%d 列；每列产出统计事实'
                 '（非空计数、≤%d 个去重样本、推断类型）与总行数事实。'
                 % (len(rows), total, width, SAMPLE_VALUES))
    if truncated:
        notes.append('超过 %d 行仅采样前 %d 行：列统计基于第 2–%d 行，其余 %d 行未统计'
                     '（总行数仍为真实值 %d）。'
                     % (SAMPLE_ROWS, SAMPLE_ROWS, SAMPLE_ROWS + 1, total - SAMPLE_ROWS, total))
    notes.append('定位器使用 kind=csv + 真实行号（表头=1、列统计=首个数据行、总行数=1）。')
    notes.append('未执行：不执行单元格内容、不推断外键、不访问外链；类型为按样本形状的粗推断。')
    if encoding not in textline.UTF8_ALIASES:
        notes.append('按 %s 编码解码（非 utf-8）。' % encoding)
    if not sink.facts:
        return finish(sink, ['csv'], notes + ['CSV 未提取到任何表头/列信息。'], failed,
                      partial=True)
    return finish(sink, ['csv'], notes, failed, partial=bool(failed or truncated),
                  rowCount=total, columnCount=width, delimiter=delimiter,
                  sampledRows=len(sampled))
