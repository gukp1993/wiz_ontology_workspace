"""SQL DDL 解析：`CREATE TABLE` 表/列/约束/索引（MySQL 常见方言优先）。

能力边界（支持 / 降级 / 不执行）
--------------------------------
* 支持：`CREATE TABLE [IF NOT EXISTS] \`t\` (…)` 的表名、列名、列类型与长度精度、
  `NOT NULL`/`NULL`/`DEFAULT`/`AUTO_INCREMENT`/`COMMENT`/`CHARACTER SET`/`COLLATE`/`ON UPDATE`、
  复合与单列 `PRIMARY KEY`、`UNIQUE [KEY|INDEX]`、`KEY|INDEX`、`CONSTRAINT`、
  `FOREIGN KEY … REFERENCES … ON DELETE/UPDATE`；`COMMENT='…'` 表注释；`CREATE [UNIQUE] INDEX`
  独立索引语句；MySQL 反引号、`--`/`#`/`/* */` 注释剥离（注释内容单独作为事实保留，不污染
  列文本）；`DROP TABLE`/`ALTER TABLE` 按语句登记（ALTER 只登记动作与目标，不重建列）
  其他方言（PostgreSQL/SQLServer/Oracle/Hive 等）只要识别出 `CREATE TABLE` 就按同一套规则
  尽力解析，并在 `coverage.notes` 标注方言判定结果与未识别到的构造。
* 降级（写入 failedSegments / notes，绝不静默跳过）：括号不成对的语句、无法拆出列定义的
  表体、未识别的列定义行、`CREATE TABLE … AS SELECT`、`CREATE VIEW|PROCEDURE|TRIGGER|FUNCTION`、
  `DELIMITER` 段、存储过程体、动态 SQL 拼接、方言专有语法（分区、`ENGINE` 变体、
  Oracle 的 `NUMBER(p,s)` 之外的复杂类型等）。
* 不执行：不连接任何数据库、不执行 SQL（含注释中的 SQL）、不解析动态 SQL/存储过程逻辑、
  不访问网络或外链、不加载扩展。

定位器：`{'kind':'ddl','file':rel_path,'line':行号,'table':表名,'column':列名}`（table 级事实
column 为空字符串）；`kind` 取 `table`/`column`/`fk`/`constraint`/`index`/`comment`/`statement`。
line 为真实行号，snippet 为该行原文（表级事实最多 3 行上下文）。
"""

import re

from workbench.ontology_build.parsers.base import failure
from workbench.ontology_build.parsers import textline
from workbench.ontology_build.parsers.textline import FactSink, failed_segment, finish, rel_of

MAX_COLUMNS_PER_TABLE = 2000     # 单表列事实上限
MAX_TABLES = 1000                # 单文件表事实上限

_CREATE_TABLE = re.compile(
    r'^\s*CREATE\s+(?:TEMPORARY\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?'
    r'(?P<name>(?:`[^`]+`|"[^"]+"|\[[^\]]+\]|\w+)(?:\s*\.\s*(?:`[^`]+`|"[^"]+"|\[[^\]]+\]|\w+))?)'
    r'\s*(?P<tail>.*)$', re.I)
_CREATE_INDEX = re.compile(
    r'^\s*CREATE\s+(?P<unique>UNIQUE\s+)?(?:FULLTEXT\s+|SPATIAL\s+)?INDEX\s+'
    r'(?P<name>`[^`]+`|"[^"]+"|\[[^\]]+\]|\w+)\s+ON\s+'
    r'(?P<table>`[^`]+`|"[^"]+"|\[[^\]]+\]|\w+)\s*\((?P<cols>[^)]*)\)', re.I)
_DROP_TABLE = re.compile(r'^\s*DROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?P<name>[^\s;,]+)', re.I)
_ALTER_TABLE = re.compile(
    r'^\s*ALTER\s+TABLE\s+(?P<name>`[^`]+`|"[^"]+"|\[[^\]]+\]|\w+)\s+(?P<action>.+)$', re.I)
_OTHER_DDL = re.compile(r'^\s*CREATE\s+(?P<object>VIEW|PROCEDURE|PROC|FUNCTION|TRIGGER|EVENT|'
                        r'DATABASE|SCHEMA|SEQUENCE|USER|ROLE)\b', re.I)
_DELIMITER = re.compile(r'^\s*DELIMITER\b', re.I)
_TABLE_OPTION = re.compile(r'^\s*(ENGINE|DEFAULT\s+CHARSET|CHARSET|COLLATE|AUTO_INCREMENT|'
                           r'COMMENT|ROW_FORMAT|PARTITION|TABLESPACE|WITH|ON\s+COMMIT)\b', re.I)

_COLUMN_DEF = re.compile(
    r'^(?P<name>`[^`]+`|"[^"]+"|\[[^\]]+\]|[A-Za-z_][\w$]*)'
    r'(?:\s+(?P<type>[A-Za-z][\w]*(?:\s*\([^)]*\))?'
    r'(?:\s+UNSIGNED)?(?:\s+ZEROFILL)?(?:\s+CHARACTER\s+SET\s+[\w]+)?(?:\s+COLLATE\s+[\w]+)?'
    r'(?:\[\])?))?'
    r'(?P<rest>.*)$', re.I)
_TABLE_CONSTRAINT_START = re.compile(
    r'^\s*(PRIMARY\s+KEY|UNIQUE(\s+(?:KEY|INDEX))?|FOREIGN\s+KEY|CONSTRAINT|KEY|INDEX|FULLTEXT|'
    r'SPATIAL|CHECK|EXCLUDE)\b', re.I)
_FK = re.compile(
    r'(?:CONSTRAINT\s+(?P<cname>`[^`]+`|"[^"]+"|\w+)\s+)?FOREIGN\s+KEY\s*\((?P<cols>[^)]*)\)\s*'
    r'REFERENCES\s+(?P<table>`[^`]+`|"[^"]+"|\w+(?:\s*\.\s*\w+)?)\s*(?:\((?P<refcols>[^)]*)\))?'
    r'\s*(?P<actions>(?:ON\s+(?:DELETE|UPDATE)\s+(?:RESTRICT|CASCADE|SET\s+NULL|NO\s+ACTION|'
    r'SET\s+DEFAULT)\s*)*)', re.I)
_PRIMARY_KEY = re.compile(r'PRIMARY\s+KEY\s*(?:USING\s+\w+\s*)?\((?P<cols>[^)]*)\)', re.I)
_UNIQUE_KEY = re.compile(r'(?:UNIQUE(?:\s+(?:KEY|INDEX))?|CONSTRAINT\s+\S+\s+UNIQUE)\s*'
                         r'(?:`[^`]+`|"[^"]+"|\w+)?\s*\((?P<cols>[^)]*)\)', re.I)
_PLAIN_INDEX = re.compile(r'^\s*(?P<kind>KEY|INDEX|FULLTEXT|SPATIAL)\s*(?P<name>`[^`]+`|"[^"]+"|\w+)?\s*'
                          r'(?:USING\s+\w+\s*)?\((?P<cols>[^)]*)\)', re.I)
_COLUMN_COMMENT = re.compile(r"COMMENT\s+('(?:[^']|'')*'|\"(?:[^\"]|\"\")*\")", re.I)
_TABLE_COMMENT = re.compile(r"COMMENT\s*=\s*('(?:[^']|'')*'|\"(?:[^\"]|\"\")*\")", re.I)
_DEFAULT_VALUE = re.compile(r'\bDEFAULT\s+((?:\'(?:[^\']|\'\')*\'|"(?:[^"]|"")*"|`[^`]+`|'
                            r'[-\w\.\+]+|\([^)]*\)))', re.I)
_ON_UPDATE = re.compile(r'\bON\s+UPDATE\s+([\w\(\)]+)', re.I)
_REFERENCES_INLINE = re.compile(r'\bREFERENCES\s+(`[^`]+`|"[^"]+"|\w+(?:\s*\.\s*\w+)?)\s*'
                                r'(?:\(([^)]*)\))?', re.I)
_CHECK_INLINE = re.compile(r'\bCHECK\s*\(([^)]*)\)', re.I)
_GENERATED = re.compile(r'\bGENERATED\s+(?:ALWAYS\s+)?AS\s*\(([^)]*)\)', re.I)
_ENUM_TYPE = re.compile(r'^ENUM\s*\(([^)]*)\)', re.I)
_TRAILING_TABLE_OPTIONS = re.compile(r'^\s*\)\s*(?P<options>.*)$')
_SET_TYPE = re.compile(r'^SET\s*\(([^)]*)\)', re.I)


def _rel(path, rel_path):
    return rel_of(path, rel_path)


def _unquote_ident(value):
    text = str(value or '').strip()
    for left, right in (('`', '`'), ('"', '"'), ('[', ']')):
        if text.startswith(left) and text.endswith(right) and len(text) >= 2:
            return text[1:-1].strip()
    return text.strip()


def _table_name(raw):
    """把 `db`.`t` / db.t 规范为裸表名（保留库名到 data.schema）。"""
    text = str(raw or '').strip()
    parts = [item for item in re.split(r'\s*\.\s*', text) if item]
    cleaned = [_unquote_ident(item) for item in parts]
    if len(cleaned) >= 2:
        return cleaned[-1], '.'.join(cleaned[:-1])
    return (cleaned[0] if cleaned else ''), ''


def _unquote_string(value):
    """去掉 SQL 字符串字面量引号并还原 '' / "" 转义（注释文本用）。"""
    text = str(value or '').strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        inner = text[1:-1]
        return inner.replace(text[0] * 2, text[0])
    return text


def _column_names(raw):
    """把 `a`,`b` / a, b 拆成名字列表。"""
    names = []
    for item in _split_commas(raw):
        name = _unquote_ident(item.strip())
        if not name:
            continue
        # 索引定义里可能带长度前缀，如 `name`(20)
        match = re.match(r'^([^\(\s]+)\s*(?:\([^)]*\))?\s*(?:ASC|DESC)?$', name, re.I)
        names.append(match.group(1) if match else name)
    return names


def _split_commas(text):
    """按顶层逗号切分（忽略括号与引号内的逗号，支持 '' / \\' 转义）。"""
    parts, depth, buf, quote, index = [], 0, [], None, 0
    raw = str(text or '')
    while index < len(raw):
        char = raw[index]
        if quote:
            buf.append(char)
            if char == '\\' and index + 1 < len(raw):
                buf.append(raw[index + 1])
                index += 2
                continue
            if char == quote:
                if index + 1 < len(raw) and raw[index + 1] == quote:
                    buf.append(raw[index + 1])
                    index += 2
                    continue
                quote = None
            index += 1
            continue
        if char in ("'", '"', '`'):
            quote = char
            buf.append(char)
            index += 1
            continue
        if char == '(':
            depth += 1
        elif char == ')':
            depth -= 1
        if char == ',' and depth <= 0:
            parts.append(''.join(buf).strip())
            buf = []
            index += 1
            continue
        buf.append(char)
        index += 1
    tail = ''.join(buf).strip()
    if tail:
        parts.append(tail)
    return [item for item in parts if item]


def _mask_strings(line):
    """把字符串字面量内容置空，供语句切分/括号配平使用。

    保留引号定界符与转义反斜杠（内容置换为等长空格）：若把结束引号也吃掉，
    下游按引号切分的逻辑会认为字符串未闭合，把后续列与逗号整段吞掉。
    转义对（\\' 与 ''）同样原样保留，保证引号数配平。
    """
    out = []
    index = 0
    quote = None
    while index < len(line):
        char = line[index]
        if quote:
            if char == '\\' and index + 1 < len(line):
                out.append(char)
                nxt = line[index + 1]
                out.append(nxt if nxt == quote else ' ')
                index += 2
                continue
            if char == quote:
                if index + 1 < len(line) and line[index + 1] == quote:
                    out.append(quote); out.append(quote)
                    index += 2
                    continue
                quote = None
                out.append(char)
                index += 1
                continue
            out.append(' ')
            index += 1
            continue
        if char in ("'", '"'):
            quote = char
            out.append(char)
            index += 1
            continue
        out.append(char)
        index += 1
    return ''.join(out)


class Comment:
    __slots__ = ('text', 'line', 'style')

    def __init__(self, text, line, style):
        self.text = text
        self.line = line
        self.style = style


def _scan_comments(lines):
    """剥离注释：返回 (去注释后的行, 行号→注释列表)。同时保留行号与原文行结构。

    支持 `--`（SQL 标准，要求后接空白）、`#`（MySQL）、`/* */`（可跨行，含 MySQL 版本注释）。
    """
    stripped_lines = []
    comments = {}
    in_block = False
    block_start = 0
    block_buf = []
    for number, line in enumerate(lines, start=1):
        buf = []
        index = 0
        while index < len(line):
            char = line[index]
            pair = line[index:index + 2]
            if in_block:
                end = line.find('*/', index)
                if end < 0:
                    block_buf.append(line[index:])
                    index = len(line)
                    continue
                block_buf.append(line[index:end])
                comments.setdefault(block_start, []).append(
                    Comment(' '.join(part.strip() for part in block_buf if part.strip()).strip(),
                            block_start, 'block'))
                in_block = False
                buf.append(' ' * (end + 2 - index))
                index = end + 2
                continue
            if pair == '--' and (index + 2 >= len(line) or line[index + 2] in ' \t'):
                comment = line[index + 2:].strip()
                if comment:
                    comments.setdefault(number, []).append(Comment(comment, number, 'dash'))
                index = len(line)
                continue
            if char == '#':
                comment = line[index + 1:].strip()
                if comment:
                    comments.setdefault(number, []).append(Comment(comment, number, 'hash'))
                index = len(line)
                continue
            if pair == '/*':
                in_block = True
                block_start = number
                block_buf = []
                index += 2
                continue
            buf.append(char)
            index += 1
        stripped_lines.append(''.join(buf))
    if in_block and block_buf:
        comments.setdefault(block_start, []).append(
            Comment(' '.join(part.strip() for part in block_buf if part.strip()).strip(),
                    block_start, 'block-unterminated'))
    return stripped_lines, comments


def _split_statements(lines, comments):
    """按分号切分语句，返回 [{'text','start','end'}]；带行号与未终止语句报告。"""
    statements = []
    buf = []
    start = 1
    depth = 0
    for number, line in enumerate(lines, start=1):
        masked = _mask_strings(line)
        if not buf:
            start = number
        buf.append(line)
        depth += masked.count('(') - masked.count(')')
        if ';' in masked and depth <= 0:
            text = '\n'.join(buf)
            statements.append({'text': text, 'start': start, 'end': number})
            buf = []
            depth = 0
    if buf and any(item.strip() for item in buf):
        statements.append({'text': '\n'.join(buf), 'start': start, 'end': len(lines),
                           'unterminated': True})
    return statements

def _parse_column_list(body, table, schema):
    """解析表体（括号内内容）→ 列定义、约束、未识别行。"""
    columns, constraints, failed = [], [], []
    for item in _split_commas(body):
        entry = {'text': item}
        if _TABLE_CONSTRAINT_START.match(item):
            constraints.append(item)
            continue
        match = _COLUMN_DEF.match(item)
        if not match or not match.group('name'):
            failed.append(item)
            continue
        name = _unquote_ident(match.group('name'))
        entry['name'] = name
        entry['type'] = (match.group('type') or '').strip()
        entry['rest'] = (match.group('rest') or '').strip()
        columns.append(entry)
    return columns, constraints, failed


def _column_data(entry, table, schema, line, raw_line):
    """把列定义行转成结构化 data（类型、可空、默认值、注释、约束、平台细节）。"""
    rest = entry['rest']
    type_text = entry['type']
    data = {
        'table': table, 'schema': schema, 'column': entry['name'],
        'type': type_text, 'declaration': entry['text'].strip(),
        'line': line,
    }
    base_type_match = re.match(r'^([A-Za-z][\w]*)', type_text)
    data['baseType'] = (base_type_match.group(1) if base_type_match else type_text).lower()
    size = re.search(r'\(([^)]*)\)', type_text)
    if size:
        data['typeArgs'] = [item.strip() for item in size.group(1).split(',')]
    data['unsigned'] = bool(re.search(r'\bUNSIGNED\b', type_text, re.I))
    data['notNull'] = bool(re.search(r'\bNOT\s+NULL\b', rest, re.I))
    data['nullable'] = not data['notNull'] and not re.search(r'\bPRIMARY\s+KEY\b', rest, re.I)
    for keyword, key in (('AUTO_INCREMENT', 'autoIncrement'), ('UNSIGNED', 'unsigned'),
                         ('BINARY', 'binary'), ('ZEROFILL', 'zerofill')):
        if re.search(r'\b%s\b' % keyword, rest, re.I):
            data[key] = True
    default = _DEFAULT_VALUE.search(rest)
    if default:
        data['default'] = _unquote_string(default.group(1).strip())
        data['defaultRaw'] = default.group(1).strip()
    on_update = _ON_UPDATE.search(rest)
    if on_update:
        data['onUpdate'] = on_update.group(1)
    comment = _COLUMN_COMMENT.search(rest)
    if comment:
        data['comment'] = _unquote_string(comment.group(1))
    charset = re.search(r'\bCHARACTER\s+SET\s+(\w+)', rest, re.I)
    if charset:
        data['charset'] = charset.group(1)
    collate = re.search(r'\bCOLLATE\s+(\w+)', rest, re.I)
    if collate:
        data['collate'] = collate.group(1)
    if re.search(r'\bPRIMARY\s+KEY\b', rest, re.I):
        data['primaryKey'] = True
    if re.search(r'\bUNIQUE\b', rest, re.I):
        data['unique'] = True
    check = _CHECK_INLINE.search(rest)
    if check:
        data['check'] = check.group(1).strip()
    generated = _GENERATED.search(rest)
    if generated:
        data['generated'] = generated.group(1).strip()
        data['qualityNote'] = '生成列：表达式文本已保留，未计算取值。'
    reference = _REFERENCES_INLINE.search(rest)
    data['reference'] = None
    if reference:
        ref_table, ref_schema = _table_name(reference.group(1))
        data['reference'] = {'table': ref_table, 'schema': ref_schema,
                             'columns': _column_names(reference.group(2) or '')}
    enum_values = _ENUM_TYPE.match(type_text)
    if enum_values:
        data['enumValues'] = [_unquote_string(item) for item in _split_commas(enum_values.group(1))]
    set_values = _SET_TYPE.match(type_text)
    if set_values:
        data['setValues'] = [_unquote_string(item) for item in _split_commas(set_values.group(1))]
    return data


def _dialect_hint(lines):
    """方言判定：只根据语法特征标注，不假定数据库版本。"""
    text = '\n'.join(lines)
    hints = []
    if re.search(r'`', text):
        hints.append('反引号标识符（MySQL 风格）')
    if re.search(r'\bAUTO_INCREMENT\b', text, re.I):
        hints.append('AUTO_INCREMENT（MySQL）')
    if re.search(r'\bENGINE\s*=', text, re.I):
        hints.append('ENGINE 表选项（MySQL）')
    if re.search(r'\bSERIAL\b|\bBIGSERIAL\b', text, re.I):
        hints.append('SERIAL（PostgreSQL）')
    if re.search(r'\bIDENTITY\b|\bGO\b\s*$', text, re.I | re.M):
        hints.append('IDENTITY/GO（SQL Server）')
    if re.search(r'\bNUMBER\s*\(', text, re.I):
        hints.append('NUMBER(p,s)（Oracle）')
    if re.search(r'\bPARTITION\s+BY\b', text, re.I):
        hints.append('PARTITION BY（Hive/PostgreSQL 分区）')
    if re.search(r'\bCOMMENT\s+ON\s+TABLE\b', text, re.I):
        hints.append('COMMENT ON TABLE（PostgreSQL/Oracle）')
    if not hints:
        hints.append('未识别到明确方言特征（按通用 SQL 解析）')
    return hints


def parse(path, material_id, rel_path=''):
    """解析 SQL DDL：表、列、主键/唯一/外键、索引、注释与未识别语句。"""
    rel = _rel(path, rel_path)
    try:
        content = textline.read_text_lines(path)
    except (OSError, IOError) as exc:
        return failure('读取文件失败：%s' % exc)
    sink = FactSink(material_id)
    if not content.lines:
        return finish(sink, ['ddl'], ['DDL 文件无可读文本内容（0 行）。'], partial=True)

    lines, comments = _scan_comments(content.lines)
    statements = _split_statements(lines, comments)
    notes, failed = [], []
    modules = ['ddl']
    tables = 0
    partial = False
    dialect = _dialect_hint(lines)

    for statement in statements:
        text = statement['text']
        first = ''
        for candidate in text.splitlines():
            if candidate.strip():
                first = candidate.strip()
                break
        start = statement['start']
        masked = _mask_strings(text)

        table_match = _CREATE_TABLE.match(first)
        if table_match:
            # 语句可能跨多行；表体从第一个 '(' 起配平
            body, body_start, body_end, complete = _extract_body(lines, start, statement['end'])
            table, schema = _table_name(table_match.group('name'))
            if not table:
                failed.append(failed_segment('statement', _loc(rel, start, '', ''),
                                             'CREATE TABLE 语句未解析出表名。', first))
                continue
            if tables >= MAX_TABLES:
                notes.append('表数量超过单文件上限 %d，其余表未解析。' % MAX_TABLES)
                partial = True
                break
            tables += 1
            options_text = _table_options_text(lines, body_end if complete else statement['end'],
                                               table_match.group('tail'))
            upper = masked.upper()
            if 'AS' in upper and 'SELECT' in upper:
                notes.append('第 %d 行 `%s` 使用 CREATE TABLE … AS SELECT：只登记表名，不从查询推断列。'
                             % (start, table))
                partial = True
            table_comment = _TABLE_COMMENT.search(options_text or '')
            data = {
                'name': table, 'schema': schema,
                'declaration': _mask_lines(text, lines, statement)[0] or first,
                'line': start, 'bodyStart': body_start, 'bodyEnd': body_end,
                'bodyComplete': complete,
                'comment': _unquote_string(table_comment.group(1)) if table_comment else '',
                'options': _parse_options(options_text),
                'dialectHints': dialect,
                'statementKind': 'createTable',
            }
            if data['comment']:
                modules.append('comment')
            sink.add('ddl', _loc(rel, start, table, ''),
                     textline.snippet_span(content, start,
                                           min(statement['end'], start + 2), 3), 'table', data,
                     'high' if complete else 'medium')
            if not complete:
                failed.append(failed_segment(
                    'statement', _loc(rel, start, table, ''),
                    'CREATE TABLE `%s` 的列定义括号未闭合（语句可能被截断），列解析可能不完整。' % table))
                partial = True

            columns, constraints, unrecognized = _parse_column_list(body, table, schema)
            if len(columns) > MAX_COLUMNS_PER_TABLE:
                notes.append('表 `%s` 列数 %d 超过上限 %d，其余列未解析。'
                             % (table, len(columns), MAX_COLUMNS_PER_TABLE))
                columns = columns[:MAX_COLUMNS_PER_TABLE]
                partial = True
            # 列 / 约束 / 未识别条目共用同一套行号映射（D18：约束定位曾一律落到 body 起始行）
            column_lines = _column_line_map(
                lines, body_start, body_end,
                [entry['text'] for entry in columns] + list(constraints) + list(unrecognized))
            data['columns'] = [entry['name'] for entry in columns]
            data['columnCount'] = len(columns)
            if not columns:
                notes.append('表 `%s` 未解析出列定义（可能是空表或未适配语法）。' % table)
                partial = True
            for entry in columns:
                entry_line = column_lines.get(entry['text'], body_start)
                column_data = _column_data(entry, table, schema, entry_line,
                                           textline.snippet_span(content, entry_line, None, 1))
                quality = 'high'
                if not column_data['type']:
                    quality = 'medium'
                    notes.append('第 %d 行列 `%s.%s` 未识别到数据类型（按 medium 记录）。'
                                 % (entry_line, table, entry['name']))
                sink.add('ddl', _loc(rel, entry_line, table, entry['name']),
                         textline.snippet_span(content, entry_line, None, 1), 'column',
                         column_data, quality)
                if column_data.get('comment'):
                    sink.add('ddl', _loc(rel, entry_line, table, entry['name']),
                             textline.snippet_span(content, entry_line, None, 1), 'columnComment',
                             {'table': table, 'column': entry['name'],
                              'comment': column_data['comment']}, 'high')
                if column_data.get('reference'):
                    reference = column_data['reference']
                    sink.add('ddl', _loc(rel, entry_line, table, entry['name']), 
                             textline.snippet_span(content, entry_line, None, 1), 'fk',
                             {'from': {'table': table, 'columns': [entry['name']]},
                              'to': {'table': reference['table'], 'columns': reference['columns']},
                              'inline': True, 'constraint': ''}, 'high')
                    modules.append('foreignkey')

            for constraint in constraints:
                constraint_line = column_lines.get(constraint, body_start)
                _constraint_facts(sink, rel, content, constraint, constraint_line, table, schema)
                modules.append('constraint')
            for item in unrecognized:
                item_line = column_lines.get(item, body_start)
                failed.append(failed_segment(
                    'statement', _loc(rel, item_line, table, ''),
                    '表 `%s` 中该定义行未识别为列或约束，未产出结构化事实。' % table, item))
                partial = True

            for number, comment_list in sorted(comments.items()):
                if start <= number <= max(statement['end'], body_end):
                    for comment in comment_list:
                        sink.add('ddlComment', _loc(rel, comment.line, table, ''),
                                 textline.snippet_span(content, comment.line, None, 1), 'comment',
                                 {'text': comment.text, 'style': comment.style,
                                  'table': table, 'scope': 'inline'},
                                 'medium')
            continue

        index_match = _CREATE_INDEX.match(first)
        if index_match:
            table, schema = _table_name(index_match.group('table'))
            name = _unquote_ident(index_match.group('name'))
            columns = _column_names(index_match.group('cols'))
            sink.add('ddl', _loc(rel, start, table, ''),
                     textline.snippet_span(content, start, min(statement['end'], start + 2), 3),
                     'index', {'name': name, 'table': table, 'schema': schema,
                               'columns': columns,
                               'unique': bool(index_match.group('unique')),
                               'declaration': first, 'line': start}, 'high')
            modules.append('index')
            continue

        drop_match = _DROP_TABLE.match(first)
        if drop_match:
            table, schema = _table_name(drop_match.group('name'))
            sink.add('ddl', _loc(rel, start, table, ''),
                     textline.snippet_span(content, start, None, 1), 'statement',
                     {'name': table, 'schema': schema, 'action': 'dropTable',
                      'declaration': first, 'line': start}, 'high')
            continue

        alter_match = _ALTER_TABLE.match(first)
        if alter_match:
            table, schema = _table_name(alter_match.group('name'))
            sink.add('ddl', _loc(rel, start, table, ''),
                     textline.snippet_span(content, start, min(statement['end'], start + 2), 3),
                     'statement', {'name': table, 'schema': schema, 'action': 'alterTable',
                                   'clause': _mask_lines(text, lines, statement)[0] or first,
                                   'declaration': first, 'line': start,
                                   'startLine': start, 'endLine': statement['end']}, 'high')
            notes.append('第 %d 行 ALTER TABLE `%s`：只登记动作与目标，不重建或改写列定义。'
                         % (start, table))
            continue

        other = _OTHER_DDL.match(first)
        if other:
            object_kind = other.group('object').upper()
            failed.append(failed_segment(
                'statement', _loc(rel, start, '', ''),
                'CREATE %s 超出 DDL 表结构解析范围（不执行、不解析其中逻辑）。' % object_kind, first))
            partial = True
            continue

        if _DELIMITER.match(first):
            failed.append(failed_segment(
                'statement', _loc(rel, start, '', ''),
                'DELIMITER 段（存储过程/函数体）不在解析范围内，未产出事实。', first))
            partial = True
            continue

        if first:
            failed.append(failed_segment(
                'statement', _loc(rel, start, '', ''),
                '未识别的 SQL 语句（不在 CREATE TABLE/INDEX/DROP/ALTER 范围内）。', first))
            partial = True

    # 语句块之外的注释（文件头说明、分组标题）单独登记为注释事实，避免静默丢弃
    covered = set()
    for statement in statements:
        for number in range(statement['start'], statement['end'] + 1):
            covered.add(number)
    for number, comment_list in sorted(comments.items()):
        if number in covered:
            continue
        for comment in comment_list:
            sink.add('ddlComment', _loc(rel, comment.line, '', ''),
                     textline.snippet_span(content, comment.line, None, 1), 'comment',
                     {'text': comment.text, 'style': comment.style, 'table': '',
                      'scope': 'file'}, 'medium')
            modules.append('comment')

    for statement in statements:
        if statement.get('unterminated') and statement['text'].strip():
            failed.append(failed_segment(
                'statement', _loc(rel, statement['start'], '', ''),
                '文件末尾语句未以分号结束（可能被截断或使用自定义分隔符），已按完整语句解析。',
                statement['text'].strip().splitlines()[0]))

    if not sink.facts and not failed:
        return finish(sink, modules, ['DDL 文件中未找到任何 SQL 语句。'], partial=True)
    notes.append('方言特征：%s。' % '；'.join(dialect))
    notes.append('已解析 %d 张表；非 MySQL 方言按同一规则尽力解析，未识别构造都在 failedSegments 中列出。'
                 % tables)
    notes.append('未执行：不连接业务数据库、不执行任何 SQL（含注释内 SQL）、不解析存储过程与动态 SQL。')
    if content.truncated:
        notes.append(textline.truncation_note())
        partial = True
    return finish(sink, modules, notes, failed, partial=partial, dialectHints=dialect)


def _loc(rel, line, table, column):
    return {'kind': 'ddl', 'file': rel, 'line': int(line), 'table': table or '', 'column': column or ''}


def _mask_lines(text, lines, statement):
    """返回语句文本对应的原始行列表（用于 declaration 展示）。"""
    out = []
    for number in range(statement['start'], statement['end'] + 1):
        if 1 <= number <= len(lines):
            out.append(lines[number - 1].strip())
    return [' '.join(item for item in out if item)]


def _extract_body(lines, start, end):
    """从 start 起找第一个 '('，按圆括号配平取表体。

    返回 (body_text, body_start_line, body_end_line, complete)；body_text 保留**原文**
    （字符串内容与注释文字不能丢：列注释、默认值、枚举都要按原文取证），
    括号配平用掩码副本判断，避免字符串里的括号干扰深度。
    行号由调用方用 `_column_line_map` 回读真实行。
    """
    depth = 0
    collected = []
    body_start = start
    started = False
    for number in range(start, len(lines) + 1):
        raw = lines[number - 1]
        masked = _mask_strings(raw)
        for index, char in enumerate(masked):
            raw_char = raw[index] if index < len(raw) else char
            if char == '(':
                depth += 1
                if not started:
                    started = True
                    body_start = number
                    continue
                collected.append(raw_char)
                continue
            if char == ')':
                depth -= 1
                if started and depth <= 0:
                    return ''.join(collected), body_start, number, True
                if started:
                    collected.append(raw_char)
                continue
            if started:
                collected.append(raw_char)
        if started:
            collected.append('\n')
    return ''.join(collected), body_start, min(end, len(lines)), False


def _table_options_text(lines, statement_end, tail):
    """表选项文本：CREATE TABLE 行尾 + 表体结束行之后到语句末尾的内容。"""
    if statement_end <= 0:
        return tail
    last = lines[statement_end - 1] if statement_end <= len(lines) else ''
    after = last.split(')', 1)[-1] if ')' in last else ''
    return (str(tail or '') + ' ' + after).strip()


def _column_line_map(lines, body_start, body_end, items):
    """表体条目原文 → 起始行号（用去注释后的原文行定位，保持行号真实）。

    D18 定位修正：旧实现只为**列**建映射，表级约束与未识别条目一律落回 body 起始行——
    行号不真实，且同一行上的多个约束事实会因 locator+snippet 相同而被 FactSink 去重吞掉
    （表现为主键约束“吃掉”同表的 UNIQUE KEY 事实）。现在按条目原文前缀在 body 行内匹配，
    列 / 约束 / 未识别条目共用同一套真实行号。
    """
    mapping = {}
    window = range(body_start, min(body_end + 1, len(lines) + 1))
    normalized = {}
    for number in window:
        normalized[number] = ' '.join(str(lines[number - 1] or '').split())
    for item in items:
        head = ' '.join(str(item or '').split()).strip()
        if not head:
            continue
        probe = head[:60]
        for number in window:
            text = normalized[number]
            if not text:
                continue
            if probe in text or text in head:
                mapping[str(item)] = number
                break
    return mapping


def _parse_options(text):
    """表选项：ENGINE/CHARSET/COLLATE/AUTO_INCREMENT/COMMENT/ROW_FORMAT 等（原样保留）。"""
    options = {}
    if not text:
        return options
    for match in re.finditer(r'\b(ENGINE|DEFAULT\s+CHARSET|CHARSET|COLLATE|AUTO_INCREMENT|ROW_FORMAT|'
                             r'TABLESPACE|COMMENT)\s*=\s*([^\s,;]+)', text, re.I):
        key = re.sub(r'\s+', ' ', match.group(1)).upper()
        options[key] = _unquote_string(match.group(2))
    return options


def _constraint_facts(sink, rel, content, constraint, line, table, schema):
    """主键/唯一/外键/普通索引事实。"""
    text = constraint.strip()
    primary = _PRIMARY_KEY.search(text)
    if primary:
        columns = _column_names(primary.group(1))
        sink.add('ddl', _loc(rel, line, table, ''), textline.snippet_span(content, line, None, 1),
                 'constraint', {'kind': 'primaryKey', 'table': table, 'schema': schema,
                                'columns': columns, 'declaration': text}, 'high')
        return
    foreign = _FK.search(text)
    if foreign:
        # D18：_FK 全部是命名捕获组（cname/cols/table/refcols/actions），
        # 早先用 group(1)/(3)/(4) 取列，实际取到的是约束名/目标表/引用列 → 错位。
        target_table, target_schema = _table_name(foreign.group('table'))
        columns = _column_names(foreign.group('cols') or '')
        ref_columns = _column_names(foreign.group('refcols') or '')
        actions = {}
        for match in re.finditer(r'ON\s+(DELETE|UPDATE)\s+(RESTRICT|CASCADE|SET\s+NULL|NO\s+ACTION|'
                                 r'SET\s+DEFAULT)', foreign.group('actions') or '', re.I):
            actions[match.group(1).upper()] = re.sub(r'\s+', ' ', match.group(2).upper())
        sink.add('ddl', _loc(rel, line, table, columns[0] if columns else ''),
                 textline.snippet_span(content, line, None, 1), 'fk',
                 {'from': {'table': table, 'columns': columns},
                  'to': {'table': target_table, 'columns': ref_columns, 'schema': target_schema},
                  'constraint': _unquote_ident(foreign.group('cname') or '') if foreign.group('cname') else '',
                  'actions': actions, 'declaration': text, 'inline': False}, 'high')
        return
    unique = _UNIQUE_KEY.search(text)
    if unique:
        sink.add('ddl', _loc(rel, line, table, ''), textline.snippet_span(content, line, None, 1),
                 'constraint', {'kind': 'unique', 'table': table, 'schema': schema,
                                'columns': _column_names(unique.group(1)),
                                'declaration': text}, 'high')
        return
    index = _PLAIN_INDEX.match(text)
    if index:
        sink.add('ddl', _loc(rel, line, table, ''), textline.snippet_span(content, line, None, 1),
                 'index', {'name': '', 'table': table, 'schema': schema,
                           'columns': _column_names(index.group('cols')),
                           'kind': index.group('kind').upper(), 'unique': False,
                           'declaration': text}, 'high')
        return
    if re.match(r'^\s*CONSTRAINT\b', text, re.I):
        sink.add('ddl', _loc(rel, line, table, ''), textline.snippet_span(content, line, None, 1),
                 'constraint', {'kind': 'namedConstraint', 'table': table, 'schema': schema,
                                'declaration': text}, 'medium')
        return
    sink.add('ddl', _loc(rel, line, table, ''), textline.snippet_span(content, line, None, 1),
             'constraint', {'kind': 'unclassified', 'table': table, 'schema': schema,
                            'declaration': text}, 'low')
