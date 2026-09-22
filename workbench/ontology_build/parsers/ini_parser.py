"""INI / CFG / CONF 专用解析：节名 + 键路径事实，定位带真实行号。

能力边界（支持 / 降级 / 不执行）
--------------------------------
* 支持（纯标准库 `configparser`，`interpolation=None`、`strict=False`，保留键大小写）：
  - 节名 + 键 + 值逐条事实，locator `{'kind':'ini','file':rel,'line':行号,'section':节名}`，
    行号由**逐行扫描**维护（configparser 自身不给行号），键路径按 `节.键` 记入 `data.path`；
  - `[DEFAULT]` 节的键按 configparser 语义继承到各节：继承而来的键事实标注
    `data.inherited=True` 并保留来源节名，不重复造事实；
  - 任何节头之前的裸 `key=value`（configparser 会直接报 MissingSectionHeaderError）按
    **顶层键**登记：`section` 为空串、行号取原文真实行（内部为解析副本临时加合成节头，不改原文）；
  - 行首制表符缩进按 configparser 原生语义处理（tab 续行值可正常解析）；
  - 注释（`#` / `;` 行首）与空行只计数、不计事实。
* 降级（写 failedSegments，不静默，其余内容仍照常解析）：
  - **重复节**（`strict=False` 会合并）：逐条记录重复节及其行号，事实按合并后的结果登记；
  - **重复键**同样进 failedSegments（后者覆盖前者，如实说明取到的是最后一个值）；
  - 非 `key=value` 且非注释/节头的行（例如缺失分隔符）逐行记录，不产出事实；
  - 未闭合的节头（`[section`）按解析错误记录，该行在**解析副本**里注释化以保留行位，
    保证其余内容仍可解析（材料本身不被改写）；
  - 解析期仍无法恢复的异常 → 显式 failure（零事实），不伪装成功。
* 不执行：不做插值求值（`%(x)s` 原样保留）、不读取 `include`/`%(here)s` 指向的文件、
  不执行材料内容、不访问网络。

定位器：`{'kind':'ini','file':rel,'line':行号,'section':节名}`（section 为空串表示顶层）。
"""

import configparser
import re

from workbench.ontology_build.parsers import structwalk
from workbench.ontology_build.parsers import textline
from workbench.ontology_build.parsers.base import failure
from workbench.ontology_build.parsers.textline import FactSink, failed_segment, finish, rel_of

SNIPPET_CHARS = 400
DEFAULT_SECTION = 'DEFAULT'
TOP_SECTION = '__top__'          # 合成节名（仅解析副本使用，对外呈现为空串）
_SECTION = re.compile(r'^\s*\[([^\]]*)\]\s*(?:[;#].*)?$')
_UNCLOSED_SECTION = re.compile(r'^\s*\[[^\]]*$')


def _line_map(text):
    """逐行扫描：返回 (节头行号映射, 键行号映射, 问题列表, 统计)。

    * 节 → 首次出现行号、键 → 首次出现行号（同一节内重复键只记第一个行号）；
    * 问题：重复节、未闭合节头、非键值行、裸 `[` 行。
    """
    sections, keys, problems = {}, {}, []
    seen_sections, seen_keys = {}, set()
    counts = {'blank': 0, 'comment': 0, 'section': 0, 'entry': 0}
    current = ''
    for number, raw in enumerate(str(text or '').splitlines(), start=1):
        stripped = raw.strip()
        if not stripped:
            counts['blank'] += 1
            continue
        if stripped[0] in ('#', ';'):
            counts['comment'] += 1
            continue
        match = _SECTION.match(raw)
        if match:
            name = match.group(1).strip()
            counts['section'] += 1
            if name in seen_sections:
                problems.append(failed_segment(
                    'statement', {'kind': 'ini', 'file': '', 'line': number, 'section': name},
                    '重复节 [%s]：首次出现在第 %d 行，configparser 按 strict=False 合并（后者覆盖同名键）'
                    % (name, seen_sections[name])))
            else:
                seen_sections[name] = number
                sections.setdefault(name, number)
            current = name
            continue
        if _UNCLOSED_SECTION.match(raw):
            problems.append(failed_segment(
                'statement', {'kind': 'ini', 'file': '', 'line': number, 'section': current},
                '节头未闭合（缺少 `]`）：该行未按节处理，后续键仍归入上一个节 [%s]' % current,
                stripped[:120]))
            continue
        if '=' in stripped or ':' in stripped:
            key = re.split(r'[=:]', stripped, maxsplit=1)[0].strip()
            marker = (current, key)
            if marker in seen_keys:
                problems.append(failed_segment(
                    'statement', {'kind': 'ini', 'file': '', 'line': number, 'section': current},
                    '重复键 `%s`（节 [%s]）：configparser 保留最后一个值，事实按最终值登记'
                    % (key, current or '顶层')))
            else:
                seen_keys.add(marker)
                keys.setdefault(marker, number)
            counts['entry'] += 1
            continue
        problems.append(failed_segment(
            'statement', {'kind': 'ini', 'file': '', 'line': number, 'section': current},
            '无法识别的行（既不是节头/注释，也没有 `=` 或 `:` 分隔符）：未产出事实',
            stripped[:120]))
    return sections, keys, problems, counts, seen_sections


def parse(path, material_id, rel_path=''):
    """解析 INI/CFG/CONF：节 + 键路径事实，重复节/解析错误进 failedSegments。"""
    rel = rel_of(path, rel_path)
    try:
        text, encoding, size = structwalk.read_document_text(path)
    except (OSError, IOError) as exc:
        return failure('读取文件失败：%s' % exc)
    except ValueError as exc:
        return failure(str(exc))

    sections, keys, problems, counts, seen_sections = _line_map(text)
    for item in problems:
        item['locator']['file'] = rel

    parse_text, top_keys = _sanitize_for_parser(text, problems)
    parser = configparser.ConfigParser(interpolation=None, strict=False,
                                       comment_prefixes=('#', ';'), inline_comment_prefixes=None)
    parser.optionxform = str          # 保留键大小写（配置键大小写敏感）
    try:
        parser.read_string(parse_text)
    except configparser.Error as exc:
        reason = 'INI 解析失败：%s' % str(exc).replace('\n', ' ')
        locator = {'kind': 'ini', 'file': rel, 'line': 0, 'section': ''}
        return failure(reason, coverage={
            'modules': ['ini'], 'notes': [reason, '解析错误已记录，产出 0 条事实；修复后单物料重试。'],
            'failedSegments': [failed_segment('statement', locator, reason, str(text or '')[:200])]})

    sink = FactSink(material_id)
    failed = list(problems)
    defaults = parser.defaults()
    total = 0
    for section in parser.sections():
        section_line = sections.get(section) or 0
        # 顶层键（文件开头、任何节头之前）由合成节承载：对外仍按「无节」呈现（section=''）。
        # 合成节不是材料里的真实节，因此不参与 [DEFAULT] 继承——否则 DEFAULT 的键会以
        # 「顶层键」的名义重复登记（且行号取不到）。
        shown_section = '' if section == TOP_SECTION else section
        for key, value in parser.items(section, raw=True):
            if section == TOP_SECTION and (shown_section, key) not in keys:
                continue
            line = keys.get((section, key)) or keys.get((shown_section, key)) or section_line or 1
            inherited = key in defaults and (section, key) not in keys
            source_section = DEFAULT_SECTION if inherited else shown_section
            data = {'section': shown_section, 'key': key, 'value': value, 'line': line,
                    'path': '%s.%s' % (shown_section, key) if shown_section else key,
                    'inherited': inherited, 'sourceSection': source_section,
                    'chars': len(str(value)), 'topLevel': shown_section == ''}
            if shown_section:
                data['hierarchy'] = [shown_section]
            snippet = ('[%s] %s=%s' % (shown_section, key, value)) if shown_section \
                else '%s=%s' % (key, value)
            sink.add('ini', {'kind': 'ini', 'file': rel, 'line': line, 'section': shown_section},
                     snippet[:SNIPPET_CHARS], 'iniEntry', data, 'high')
            total += 1
    for key, value in defaults.items():
        line = keys.get((DEFAULT_SECTION, key)) or keys.get(('', key)) or 1
        sink.add('ini',
                 {'kind': 'ini', 'file': rel, 'line': line, 'section': DEFAULT_SECTION},
                 '[%s] %s=%s' % (DEFAULT_SECTION, key, value), 'iniEntry',
                 {'section': DEFAULT_SECTION, 'key': key, 'value': value, 'line': line,
                  'path': '%s.%s' % (DEFAULT_SECTION, key), 'inherited': False,
                  'sourceSection': DEFAULT_SECTION, 'chars': len(str(value)),
                  'hierarchy': [DEFAULT_SECTION]}, 'high')

    notes = [
        '已扫描 %d 行：节 %d 个、键值条目 %d 条、注释 %d 行、空行 %d；注释与空行只计入说明、'
        '不计入事实。' % (sum(counts.values()), counts['section'], counts['entry'],
                          counts['comment'], counts['blank']),
        '定位器使用 kind=ini + 真实行号 + 节名（行号由逐行扫描维护，configparser 自身不提供行号）。',
        '键路径记入 data.path（`节.键`，顶层为裸键）；[DEFAULT] 的键按 configparser 语义继承到'
        '各节，继承事实标注 inherited=True 且保留来源节名。',
        '未执行：不做百分号占位符插值求值、不读取 include 指向的文件、不执行材料内容。',
    ]
    if top_keys:
        notes.append('文件开头、任何节头之前有 %d 条顶层键值：按无节（section 为空串）登记，'
                     '行号取原文真实行（内部为解析副本临时加合成节头，不改材料本身）。' % top_keys)
    if counts['section'] and not parser.sections():
        notes.append('文件只包含 [DEFAULT] 节或空节，没有其他配置节。')
    if encoding not in textline.UTF8_ALIASES:
        notes.append('按 %s 编码解码（非 utf-8）。' % encoding)
    if not sink.facts:
        if not parser.sections() and not defaults:
            return finish(sink, ['ini'], notes + ['文件没有可登记的节/键。'], failed, partial=True)
    return finish(sink, ['ini'], notes, failed,
                  partial=bool(failed), sections=len(parser.sections()),
                  defaultKeys=len(defaults), entries=total)


def _sanitize_for_parser(text, problems):
    """为 configparser 准备解析副本：问题行注释化 + 顶层键归入合成节。

    返回 `(解析用文本, 顶层键条数)`。**只改解析副本，不改材料本身，也不改行号**：
    * 逐行扫描已判定的问题行（无法识别的行、未闭合节头）注释成 `# …` 保留行位，
      使其余内容仍能解析；每一条都已在 failedSegments 里如实记录；
    * 任何节头之前的裸 `key=value` 由临时合成节 `TOP_SECTION` 承载，对外 section 呈现为空串。
    """
    lines = str(text or '').splitlines()
    bad_lines = {int(item['locator'].get('line') or 0) for item in problems
                 if '无法识别' in str(item.get('reason')) or '未闭合' in str(item.get('reason'))}
    prepared = []
    for number, line in enumerate(lines, start=1):
        if number in bad_lines:
            prepared.append('# [解析器降级] ' + line)
        else:
            prepared.append(line)
    entries = 0
    for line in prepared:
        stripped = line.strip()
        if _SECTION.match(line):
            break                     # 已有节头：顶层键由原结构承载，无需合成节
        if not stripped or stripped[0] in ('#', ';'):
            continue
        if '=' in line or ':' in line:
            entries += 1
    if not entries:
        return '\n'.join(prepared), 0
    return '[%s]\n' % TOP_SECTION + '\n'.join(prepared), entries
