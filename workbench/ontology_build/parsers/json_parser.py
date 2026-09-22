"""JSON / JSON-LD / JSONL 专用解析：键路径事实、数组摘要、JSON-LD 节点高质量事实。

能力边界（支持 / 降级 / 不执行）
--------------------------------
* 支持（纯标准库 `json`）：
  - `.json` / `.jsonld`：整篇解析后按键路径遍历（`$.a.b[0].c`），每个叶子标量一条事实，
    数组产出摘要事实（长度 + 元素类型 + 首元素键），**不逐元素展开**（§2 抽取口径）；
  - **JSON-LD 识别**：顶层（或任意层级）出现 `@graph` 时，对每个图节点产出高质量事实
    （kind `jsonLdNode` / `jsonLdField`），字段取 `@id` / `name` / `rdfs:label` / `definition` /
    `skos:definition` / `@type` / `nodeType` / `description`（含完整 IRI 与前缀写法；值也可位于
    `data` / `properties` 等一层层嵌套容器内，最多下探 3 层），定位器带节点 `@id`；
  - `.jsonl` / `.ndjson`：逐行解析（每行一个 JSON），定位器带**真实行号**；坏行只进
    failedSegments，不中断其余行。
* 降级/失败：
  - 坏 JSON/JSONL 行 → failedSegments 含错误位置（行/列）；整篇坏 JSON 走**显式 failure**
    （零事实），**不产出任何降级线索**——按需求 §2 不回退 LLM 兜底或文本线索，修复后单物料重试；
  - JSONL 全部行都坏 → 显式 failure（不当作空文件成功）；
  - 超过 `structwalk.DOC_BYTE_LIMIT` 字节的文件不解析（显式 failure，不截断后半段硬解析）；
  - 深度超 32 层、数组过长、事实超上限：由 structwalk 产出摘要事实/注记，不静默丢弃。
* 不执行：不执行材料中的任何内容、不解析 JSON Schema 语义、不访问 `$ref`/外链、不做网络请求。

定位器：`{'kind':'json','file':rel,'path':'$.a.b'}`；JSONL 额外带 `line`；JSON-LD 节点事实额外带
`nodeId`（节点 `@id`）。数组元素只在 JSON-LD 节点展开时才出现在路径里。
"""

import json

from workbench.ontology_build.parsers import structwalk
from workbench.ontology_build.parsers import textline
from workbench.ontology_build.parsers.base import failure
from workbench.ontology_build.parsers.textline import FactSink, failed_segment, finish, rel_of

# JSON-LD 语义字段：短名（去掉 @ / 前缀 / IRI）→ 规范字段名
JSONLD_FIELD_ALIASES = {
    'id': 'id',
    'name': 'name',
    'label': 'name',
    'preflabel': 'name',
    'title': 'name',
    'definition': 'definition',
    'description': 'description',
    'type': 'type',
    'nodetype': 'nodeType',
}
GRAPH_KEY = '@graph'
# JSON-LD 字段下探：容器键（值也可放在嵌套容器里，例如储能 jsonId 的 node["data"]["definition"]）
JSONLD_SEARCH_DEPTH = 3
JSONLD_FIELDS_PER_NODE = 40
LINE_MODE_EXT = ('jsonl', 'ndjson', 'jsonlines', 'jl')

_JSON_ERROR_MAX_EXCERPT = 200


def is_line_mode(rel_path):
    """按后缀判断是否走 JSONL 逐行模式。"""
    name = str(rel_path or '').replace('\\', '/').rsplit('/', 1)[-1].lower()
    ext = name.rsplit('.', 1)[-1] if '.' in name else ''
    return ext in LINE_MODE_EXT


def parse(path, material_id, rel_path=''):
    """入口：按后缀分派整篇 JSON 与 JSONL 逐行模式。"""
    rel = rel_of(path, rel_path)
    try:
        text, encoding, size = structwalk.read_document_text(path)
    except (OSError, IOError) as exc:
        return failure('读取文件失败：%s' % exc)
    except ValueError as exc:  # 超字节上限 / 路径为目录
        return failure(str(exc))
    if is_line_mode(rel):
        return parse_lines(text, material_id, rel, encoding=encoding, size=size)
    return parse_document(text, material_id, rel, encoding=encoding, size=size)


# ---------------------------------------------------------------------------
# 整篇 JSON / JSON-LD
# ---------------------------------------------------------------------------
def parse_document(text, material_id, rel, encoding='utf-8', size=0):
    """解析整篇 JSON；含 `@graph` 时额外产出 JSON-LD 节点事实。"""
    try:
        tree = json.loads(text)
    except ValueError as exc:      # json.JSONDecodeError 是其子类
        return _syntax_failure(exc, rel, text, encoding, size)
    sink = FactSink(material_id)
    nodes = _jsonld_nodes(tree)
    consumed = set()
    for node_path, node in nodes:
        consumed |= _emit_jsonld_node(sink, node, rel, node_path)
    stats = structwalk.walk_tree(tree, sink, rel, locator_kind='json', module='json')
    if consumed:
        # 已被 JSON-LD 字段事实覆盖的叶子不再重复登记（数组摘要等仍保留）
        sink.facts = [fact for fact in sink.facts
                      if not (fact.locator.get('path') in consumed
                              and 'nodeId' not in fact.locator and fact.kind.endswith('Leaf'))]
    notes = [
        'JSON 键路径遍历：每个叶子标量一条事实（locator.path 可用 JSONPath 回放到原值）；'
        '数组只产出摘要事实（长度 + 元素类型 + 首元素键），不逐元素展开。',
        '定位器使用 kind=json + JSONPath（`$.a.b[0].c`），数组元素仅在 JSON-LD 节点展开时出现。',
        '未执行：不执行材料内容、不解析 $ref/外链、不访问网络。',
    ]
    notes.extend(stats.get('notes') or [])
    if nodes:
        notes.append('识别为 JSON-LD：`%s` 中 %d 个节点已产出高质量节点/字段事实（定位器带节点 @id）。'
                     % (GRAPH_KEY, len(nodes)))
    if encoding not in textline.UTF8_ALIASES:
        notes.append('按 %s 编码解码（非 utf-8）。' % encoding)
    if not sink.facts:
        return finish(sink, ['json'],
                      notes + ['JSON 结构中没有可登记的叶子标量或数组（空文档/空对象）。'],
                      partial=True)
    coverage_extra = {'jsonLdNodes': len(nodes)} if nodes else {}
    return finish(sink, ['json'], notes, partial=bool(stats.get('notes') or stats.get('stopped')),
                  **coverage_extra)


def parse_lines(text, material_id, rel, encoding='utf-8', size=0):
    """JSONL/NDJSON 逐行解析：坏行进 failedSegments，不中断其余行。"""
    sink = FactSink(material_id)
    failed, notes = [], []
    good, blank, walked = 0, 0, {}
    for number, raw in enumerate(str(text or '').splitlines(), start=1):
        line = raw.strip()
        if not line:
            blank += 1
            continue
        try:
            tree = json.loads(line)
        except ValueError as exc:
            failed.append(failed_segment(
                'line', {'kind': 'json', 'file': rel, 'line': number, 'path': '$'},
                '第 %d 行 JSON 语法错误：%s（该行未产出事实，其余行继续解析）'
                % (number, _error_message(exc, inline=True)),
                line[:_JSON_ERROR_MAX_EXCERPT]))
            continue
        good += 1
        stats = structwalk.walk_tree(tree, sink, rel, locator_kind='json', module='json',
                                     extra_locator={'line': number})
        walked[number] = stats
    for stats in walked.values():
        notes.extend(stats.get('notes') or [])
    notes.insert(0, 'JSONL 逐行解析：每行一个 JSON 对象，定位器带真实行号（kind=json + line + path）。'
                    '已解析 %d 行、坏行 %d 行、空行 %d 行。' % (good, len(failed), blank))
    notes.append('未执行：不执行材料内容、不访问外链。')
    if encoding not in textline.UTF8_ALIASES:
        notes.append('按 %s 编码解码（非 utf-8）。' % encoding)
    if not sink.facts:
        if failed:
            reason = 'JSONL 每行都解析失败（共 %d 行），未产出任何事实。' % len(failed)
            return failure(reason, coverage={'modules': ['json'], 'notes': notes + [reason],
                                             'failedSegments': failed})
        return finish(sink, ['json'],
                      notes + ['JSONL 文件没有可解析的非空行。'], partial=True)
    return finish(sink, ['json'], notes, failed, partial=bool(failed or any(
        stats.get('notes') or stats.get('stopped') for stats in walked.values())),
        parsedLines=good, failedLines=len(failed))


# ---------------------------------------------------------------------------
# JSON-LD
# ---------------------------------------------------------------------------
def _jsonld_nodes(tree, base_path='$'):
    """找出 JSON-LD 图节点：返回 [(节点 JSON 路径, 节点 dict)]，无 `@graph` 时返回 []。

    支持顶层 `@graph`、嵌套容器里的 `@graph`（按文档顺序），以及 `@graph` 为单个对象的情形。
    """
    found = []

    def scan(node, path, depth=0):
        if depth > JSONLD_SEARCH_DEPTH or not isinstance(node, dict):
            return
        graph = node.get(GRAPH_KEY)
        if graph is not None:
            items = graph if isinstance(graph, list) else [graph]
            for index, item in enumerate(items):
                if isinstance(item, dict):
                    found.append((structwalk.index_path(structwalk.child_path(path, GRAPH_KEY),
                                                        index), item))
        for key, value in node.items():
            if key == GRAPH_KEY:
                continue
            if isinstance(value, dict):
                scan(value, structwalk.child_path(path, key), depth + 1)

    scan(tree, str(base_path or '$'))
    return found


def _short_field_name(key):
    """字段名规范化：去 `@`、去前缀（`skos:definition`）、去完整 IRI，返回小写短名。"""
    text = str(key or '').strip()
    if not text:
        return ''
    if text.startswith('@'):
        text = text[1:]
    if '#' in text and ('://' in text or text.startswith('http')):
        text = text.rsplit('#', 1)[-1]
    elif '://' in text or text.startswith('http'):
        text = text.rstrip('/').rsplit('/', 1)[-1]
    if ':' in text:
        text = text.split(':', 1)[1]
    return text.strip().lower()


def _field_value_text(value):
    """字段值文本：标量直接取，标量列表用 `；` 连接；其他类型返回 ''（不登记为语义字段）。"""
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, (int, float)):
        return structwalk.leaf_text(value)[0]
    if isinstance(value, (list, tuple)):
        parts = []
        for item in value[:20]:
            if isinstance(item, (str, bool, int, float)):
                parts.append(structwalk.leaf_text(item)[0])
            elif isinstance(item, dict):
                parts.append(str(item.get('@id') or item.get('name') or ''))
        return '；'.join(part for part in parts if part)
    return ''


def _node_id(node):
    for key in ('@id', 'id'):
        value = node.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ''


def _node_types(node):
    values = []
    for key in ('@type', 'type', 'rdf:type'):
        value = node.get(key)
        if isinstance(value, str) and value.strip():
            values.append(value.strip())
        elif isinstance(value, (list, tuple)):
            values.extend(str(item).strip() for item in value if str(item or '').strip())
    return values


def _jsonld_field_matches(node, node_path):
    """在节点及其嵌套容器里找出语义字段：返回 [(字段路径, 原键名, 规范名, 值文本)]。"""
    matches = []
    seen_paths = set()

    def scan(current, path, depth):
        if depth > JSONLD_SEARCH_DEPTH or not isinstance(current, dict):
            return
        for key, value in current.items():
            child = structwalk.child_path(path, key)
            canonical = JSONLD_FIELD_ALIASES.get(_short_field_name(key))
            if canonical and child not in seen_paths:
                text = _field_value_text(value)
                if text:
                    seen_paths.add(child)
                    matches.append((child, str(key), canonical, text))
            if isinstance(value, dict):
                scan(value, child, depth + 1)

    scan(node, node_path, 0)
    return matches[:JSONLD_FIELDS_PER_NODE]


def _emit_jsonld_node(sink, node, rel, node_path):
    """产出一个 JSON-LD 节点的摘要事实与字段事实；返回被覆盖的字段路径集合。"""
    node_id = _node_id(node)
    types = _node_types(node)
    matches = _jsonld_field_matches(node, node_path)
    label = ''
    for _path, _key, canonical, text in matches:
        if canonical == 'name':
            label = text
            break
    node_locator = {'kind': 'json', 'file': rel, 'path': node_path}
    if node_id:
        node_locator['nodeId'] = node_id
    snippet = '%s %s' % (node_id, label) if node_id and label else (label or node_id or '(无名称节点)')
    if types:
        snippet = '%s（%s）' % (snippet, '、'.join(types[:3]))
    sink.add('json', node_locator, snippet, 'jsonLdNode',
             {'nodeId': node_id, 'types': types, 'label': label, 'path': node_path,
              'fields': [canonical for _p, _k, canonical, _t in matches],
              'fieldCount': len(matches)}, 'high')
    consumed = set()
    for path, key, canonical, text in matches:
        locator = {'kind': 'json', 'file': rel, 'path': path}
        if node_id:
            locator['nodeId'] = node_id
        sink.add('json', locator, text, 'jsonLdField',
                 {'nodeId': node_id, 'field': key, 'canonical': canonical, 'value': text,
                  'path': path}, 'high')
        consumed.add(path)
    return consumed


# ---------------------------------------------------------------------------
# 失败路径
# ---------------------------------------------------------------------------
def _error_message(exc, inline=False):
    """统一错误文案：JSONDecodeError 带行列；`inline=True` 用于 JSONL 单行（行号已在文案里）。"""
    line = getattr(exc, 'lineno', None)
    column = getattr(exc, 'colno', None)
    message = str(getattr(exc, 'msg', None) or exc)
    if line and inline:
        return '行内第 %d 列：%s' % (column or 0, message)
    if line:
        return '第 %d 行第 %d 列：%s' % (line, column or 0, message)
    return message


def _syntax_failure(exc, rel, text, encoding, size):
    """整篇 JSON 语法错误：显式 failure、零事实、不回退兜底。"""
    line = int(getattr(exc, 'lineno', 0) or 0)
    column = int(getattr(exc, 'colno', 0) or 0)
    reason = 'JSON 语法错误：%s' % _error_message(exc)
    lines = str(text or '').splitlines()
    excerpt = lines[line - 1].strip()[:_JSON_ERROR_MAX_EXCERPT] if 1 <= line <= len(lines) else ''
    failed = [failed_segment('statement',
                             {'kind': 'json', 'file': rel, 'line': line, 'column': column,
                              'path': '$'}, reason, excerpt)]
    notes = [
        'JSON 解析失败，产出 0 条事实；失败位置见 failedSegments（行/列）。',
        '不回退 LLM 兜底或文本线索：语法损坏的材料必须修复后单物料重试（需求 §2）。',
        '已读取 %d 字节，按 %s 解码。' % (size, encoding or 'utf-8'),
    ]
    return failure(reason, coverage={'modules': ['json'], 'notes': notes,
                                     'failedSegments': failed})


# 供解析器包内复用（避免重复实现）
def read_material_text(path, byte_limit=structwalk.DOC_BYTE_LIMIT):
    """读取结构化材料全文（structwalk 的稳定再导出，便于同族解析器共用同一 I/O 口径）。"""
    return structwalk.read_document_text(path, byte_limit)
