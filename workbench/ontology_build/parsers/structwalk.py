"""JSON / YAML / TOML 共用树遍历器：键路径事实、数组摘要、深度上限与事实上限。

能力边界（支持 / 降级 / 不执行）
--------------------------------
* 输入：**已解析**的 Python 树（`json.loads` / `yaml.safe_load` / 本包 TOML 子集解析器的产物）。
  本模块不做格式解析（除共用的字节读取与编码探测）、不执行材料内容、不访问网络。
* 支持：每个叶子标量产出一条事实（kind `<前缀>Leaf`，snippet=值原文）；**数组不逐元素展开**，
  每个数组产出一条摘要事实（kind `<前缀>ArraySummary`：长度 + 元素类型分布 + 首元素键）；
  空容器产出 `<前缀>EmptyObject` 事实；循环引用（YAML 锚点自引用）产出 `<前缀>Cycle`
  事实并停止该分支（不递归、不崩）。
* 上限（凡是上限都写在事实/notes 里，绝不静默丢弃）：
  - 深度上限 `MAX_DEPTH=32` 层：达到上限的容器只产出一条 `<前缀>DepthSummary` 事实
    （路径 + 类型 + 规模），不递归展开；
  - 数组类型统计上限 `ARRAY_SCAN_LIMIT` 项：超出只计长度不统计类型，事实内注明比例；
  - 叶子值截断到 `LEAF_CHARS` 字符（事实内 `truncated=True`）；
  - 事实总数达到 `MAX_FACTS_PER_MATERIAL` 立即停止遍历并如实注记（`textline.finish` 还会追加
    统一截断说明）。
* 不执行：不计算公式/表达式、不访问外链、不做 schema 校验。

定位器：`{'kind': locator_kind, 'file': rel, 'path': '$.a.b[0].c'}`；`line_lookup` 非空时附加
真实行号（TOML 子集解析器用它把键路径映射回源文件行号）。
"""

import json
import re

from workbench.ontology_build.parsers import textline

# 结构化文档读取上限：超过该字节数直接判失败（不做截断后解析——截断的 JSON/YAML 必然语法错误，
# 报「语法错误」是误导）。上限本身如实写进失败原因。
DOC_BYTE_LIMIT = 16 * 1024 * 1024
# 深度上限（需求 §2 统一约束：默认 32 层）
MAX_DEPTH = 32
# 数组类型统计的采样上限（超出只计长度）
ARRAY_SCAN_LIMIT = 5000
# 单条叶子/摘要事实的片段上限（base.Fact 另有 500 字硬截断）
LEAF_CHARS = 400
# 首元素键的展示上限
FIRST_KEYS_LIMIT = 12
# 事实上限：优先取协议常量；protocol.py 由并行任务（V2-10）维护，取值失败时用同值兜底常量，
# 以免本模块的 import 与既有解析器前置耦合（FactSink 自身仍按协议常量截断）。
MAX_FACTS_PER_MATERIAL = int(
    getattr(getattr(textline, 'protocol', None), 'MAX_FACTS_PER_MATERIAL', 20000) or 20000)

_BARE_KEY = re.compile(r'^[A-Za-z_][A-Za-z0-9_\-]*$')


# ---------------------------------------------------------------------------
# 字节读取与编码探测（复用 textline 既有探测链）
# ---------------------------------------------------------------------------
def detect_encoding(raw):
    """编码探测：复用 textline 的探测链（BOM → utf-8 → gb18030 → gbk → latin-1）。

    textline 的探测函数是包内私有实现，若被并行任务重命名则退到等价最小实现，
    保证解码行为不变（utf-8/gbk 中文材料不会乱码）。
    """
    detector = getattr(textline, '_detect_encoding', None)
    if callable(detector):
        try:
            return detector(raw)
        except Exception:  # noqa: BLE001 - 探测失败退兜底，不因探测实现变化整体失败
            pass
    if raw.startswith(b'\xef\xbb\xbf'):
        return 'utf-8-sig'
    if raw.startswith(b'\xff\xfe') or raw.startswith(b'\xfe\xff'):
        return 'utf-16'
    for encoding in ('utf-8', 'gb18030', 'gbk'):
        try:
            raw.decode(encoding)
            return encoding
        except (UnicodeDecodeError, LookupError):
            continue
    return 'latin-1'


def read_document_text(path, byte_limit=DOC_BYTE_LIMIT):
    """读取结构化文档全文，返回 `(text, encoding, byte_size)`。

    与 textline.read_text_lines 的差别：结构化格式不能按行截断（一行超 2000 字是合法 JSON），
    所以这里按**字节上限**读全文；超限抛 `ValueError`，由调用方转成显式 failure。
    IOError/OSError 原样抛出，同样由调用方转 failure。
    """
    with open(str(path), 'rb') as handle:
        raw = handle.read(int(byte_limit) + 1)
    if len(raw) > int(byte_limit):
        raise ValueError('文件超过结构化解析字节上限（%d 字节），未解析：请拆分材料或改用其他格式'
                         % int(byte_limit))
    encoding = detect_encoding(raw)
    try:
        text = raw.decode(encoding, errors='replace')
    except LookupError:  # 极端环境缺编解码器
        encoding = 'latin-1'
        text = raw.decode('latin-1', errors='replace')
    return text, encoding, len(raw)


# ---------------------------------------------------------------------------
# 取值呈现
# ---------------------------------------------------------------------------
def type_name(value):
    """节点类型名（跨 JSON/YAML/TOML 统一口径：object/array/string/number/boolean/null/...）。"""
    if isinstance(value, bool):
        return 'boolean'
    if value is None:
        return 'null'
    if isinstance(value, str):
        return 'string'
    if isinstance(value, (int, float)):
        return 'number'
    if isinstance(value, dict):
        return 'object'
    if isinstance(value, (list, tuple)):
        return 'array'
    if isinstance(value, (bytes, bytearray)):
        return 'binary'
    if hasattr(value, 'isoformat'):        # YAML/TOML 的 date/datetime/time 字面量
        return 'datetime'
    if isinstance(value, (set, frozenset)):
        return 'set'
    return type(value).__name__


def leaf_text(value, limit=LEAF_CHARS):
    """叶子值的文本呈现；返回 `(文本, 是否被截断)`。"""
    if isinstance(value, str):
        text = value
    elif value is None:
        text = 'null'
    elif isinstance(value, bool):
        text = 'true' if value else 'false'
    elif isinstance(value, float):
        text = repr(value)
    elif hasattr(value, 'isoformat'):
        text = str(value.isoformat())
    elif isinstance(value, (bytes, bytearray)):
        text = '<%d 字节二进制>' % len(value)
    else:
        text = str(value)
    if len(text) > limit:
        return text[:limit] + '…（截断）', True
    return text, False


def child_path(parent, key):
    """子节点路径：裸键用点号（`$.a.b`），其他键用 JSON 括号记法（`$["a.b"]`）。"""
    if isinstance(key, str) and _BARE_KEY.match(key):
        return '%s.%s' % (parent, key)
    if isinstance(key, str):
        return '%s[%s]' % (parent, json.dumps(key, ensure_ascii=False))
    text, _ = leaf_text(key, 60)
    return '%s[%s]' % (parent, json.dumps(text, ensure_ascii=False))


def index_path(parent, index):
    """数组元素路径（本模块仅在需要展开时使用，例如 JSON-LD 节点）。"""
    return '%s[%d]' % (parent, int(index))


def array_summary(path, values):
    """数组摘要：长度 + 元素类型分布 + 首元素键（不逐元素展开）。"""
    length = len(values)
    scan_limit = min(length, ARRAY_SCAN_LIMIT)
    type_counts = {}
    for item in values[:scan_limit]:
        name = type_name(item)
        type_counts[name] = type_counts.get(name, 0) + 1
    first_keys, first_index = [], -1
    for index, item in enumerate(values):
        if isinstance(item, dict):
            first_keys = [str(key) for key in list(item)[:FIRST_KEYS_LIMIT]]
            first_index = index
            break
    truncated = length > scan_limit
    parts = ['数组：%d 项' % length]
    if type_counts:
        parts.append('元素类型 %s' % '、'.join(
            '%s×%d' % (name, count) for name, count in sorted(type_counts.items())))
    if first_keys:
        parts.append('首元素键 %s' % '、'.join(first_keys))
    elif length:
        parts.append('无对象元素（无键可列）')
    if truncated:
        parts.append('类型统计仅覆盖前 %d 项，其余 %d 项只计长度未统计'
                     % (scan_limit, length - scan_limit))
    snippet = '；'.join(parts)
    data = {
        'path': path,
        'type': 'array',
        'length': length,
        'elementTypes': type_counts,
        'firstElementKeys': first_keys,
        'firstObjectIndex': first_index,
        'scanned': scan_limit,
        'truncated': truncated,
    }
    if truncated:
        data['sampleRange'] = '前 %d 项' % scan_limit
    return {'snippet': snippet, 'data': data}


# ---------------------------------------------------------------------------
# 树遍历
# ---------------------------------------------------------------------------
def walk_tree(tree, sink, rel, locator_kind='json', module='', quality='high', base_path='$',
              max_depth=MAX_DEPTH, extra_locator=None, line_lookup=None, kind_prefix=''):
    """遍历已解析树，把键路径事实写入 `sink`（textline.FactSink）；返回统计与注记。

    * `locator_kind`：定位器里的 kind（json/yaml/toml）。
    * `line_lookup`：可选 `path -> 行号` 回调，返回真值时写进定位器（TOML 用）。
    * `extra_locator`：附加到每个定位器的固定字段（不覆盖 kind/file/path）。
    * `kind_prefix`：事实 kind 前缀，默认与 `locator_kind` 相同。
    返回值：`{leaves, arrays, objects, depthSummaries, cycles, deepest, stopped, notes}`。
    """
    module = str(module or locator_kind)
    prefix = str(kind_prefix or locator_kind)
    extra = dict(extra_locator or {})
    stats = {'leaves': 0, 'arrays': 0, 'objects': 0, 'emptyContainers': 0,
             'depthSummaries': 0, 'cycles': 0, 'deepest': 0, 'stopped': False}
    notes = []
    branch = set()          # 当前路径上的容器 id：YAML 锚点可造出循环引用

    def locator(path):
        item = {'kind': locator_kind, 'file': rel, 'path': path}
        item.update(extra)
        if line_lookup is not None:
            line = line_lookup(path)
            if line:
                item['line'] = int(line)
        return item

    def add(kind, path, snippet, data, fact_quality=None):
        if stats['stopped'] or len(sink.facts) >= sink.cap:
            stats['stopped'] = True
            return None
        return sink.add(module, locator(path), snippet, '%s%s' % (prefix, kind), data,
                        fact_quality or quality)

    def visit(node, path, depth):
        stats['deepest'] = max(stats['deepest'], depth)
        if stats['stopped']:
            return
        if isinstance(node, dict):
            stats['objects'] += 1
            if not node:
                stats['emptyContainers'] += 1
                add('EmptyObject', path, '(空对象)', {'path': path, 'type': 'object', 'size': 0},
                    'medium' if depth >= max_depth else None)
                return
            if depth >= max_depth:
                stats['depthSummaries'] += 1
                add('DepthSummary', path,
                    '深度超限（>%d 层）：%s，%d 个键 %s'
                    % (max_depth, path, len(node), '、'.join(str(k) for k in list(node)[:FIRST_KEYS_LIMIT])),
                    {'path': path, 'type': 'object', 'depth': depth, 'size': len(node),
                     'keys': [str(key) for key in list(node)[:FIRST_KEYS_LIMIT]],
                     'maxDepth': max_depth, 'expanded': False}, 'medium')
                return
            marker = id(node)
            if marker in branch:
                stats['cycles'] += 1
                add('Cycle', path, '循环引用（锚点自引用），该分支停止展开：%s' % path,
                    {'path': path, 'type': 'object', 'note': '循环引用，未展开'}, 'medium')
                return
            branch.add(marker)
            try:
                for key, value in node.items():
                    visit(value, child_path(path, key), depth + 1)
                    if stats['stopped']:
                        return
            finally:
                branch.discard(marker)
            return
        if isinstance(node, (list, tuple)):
            stats['arrays'] += 1
            summary = array_summary(path, node)
            data = dict(summary['data'])
            if depth >= max_depth:
                data['depthLimited'] = True
                data['depth'] = depth
            add('ArraySummary', path, summary['snippet'], data)
            return
        stats['leaves'] += 1
        text, truncated = leaf_text(node)
        data = {'path': path, 'type': type_name(node), 'chars': len(text)}
        if truncated:
            data['truncated'] = True
            data['sourceChars'] = len(node) if isinstance(node, str) else len(text)
        add('Leaf', path, text, data)

    visit(tree, str(base_path or '$'), 0)
    if stats['depthSummaries']:
        notes.append('深度超过 %d 层的子树共 %d 处未展开，已各产出一条摘要事实（含路径/类型/规模）。'
                     % (max_depth, stats['depthSummaries']))
    if stats['cycles']:
        notes.append('检测到 %d 处循环引用（YAML 锚点自引用）：该分支停止展开并产出事实，未崩溃。'
                     % stats['cycles'])
    if stats['stopped']:
        notes.append('事实数达到单材料上限 %d 条（已截断）：树遍历提前停止，其余节点未产出事实，'
                     '截断部分不纳入证据。' % MAX_FACTS_PER_MATERIAL)
    stats['notes'] = notes
    return stats
