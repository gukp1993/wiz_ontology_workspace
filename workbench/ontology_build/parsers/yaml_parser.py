"""YAML 专用解析：`yaml.safe_load` 后复用 JSON 同一树遍历器（键路径事实）。

能力边界（支持 / 降级 / 不执行）
--------------------------------
* 支持（PyYAML `safe_load_all`，不引入新依赖、缺失时显式失败且**绝不 pip install**）：
  整篇加载后交给 `structwalk.walk_tree` 按与 JSON 完全相同的口径产出事实——叶子标量一条事实
  （`$.a.b`）、数组摘要事实（长度 + 元素类型 + 首元素键）、空容器与深度/事实上限处理一致。
  多文档 YAML（`---` 分隔）逐文档登记：第 1 个文档路径根为 `$`，其余为 `$[docN]`（N 为 1 基序号）。
* 降级（如实注记，不静默）：
  - 锚点/别名：PyYAML 在加载时已展开为普通节点，事实按展开后的结构登记；锚点/别名数量与名称
    由 `yaml.scan` 统计后写进 `coverage.notes`（含自引用造成的循环引用事实，见 structwalk）；
  - YAML 重复键由 PyYAML 按「后者覆盖」处理，本解析器不额外检测（已在 notes 声明）；
  - `datetime`/`date` 字面量按 `datetime` 类型登记（值文本取 ISO 格式）；
  - 超字节上限的文件不解析（显式 failure，不截断后半段硬解析）。
* 坏 YAML → **显式 failure**：failedSegments 含问题位置（行/列），产出 0 条事实，
  **不回退 LLM 兜底或文本线索**（需求 §2：语法损坏不当作低质量线索继续）。
* 不执行：不解析 `!!python/...` 等危险标签（`safe_load` 会直接拒绝）、不执行材料内容、
  不访问外链、不计算公式。

定位器：`{'kind':'yaml','file':rel,'path':'$.a.b'}`（键路径；YAML 支持非字符串键，
非字符串键在路径里以 `["<文本>"]` 记法呈现）。
"""

from workbench.ontology_build.parsers import structwalk
from workbench.ontology_build.parsers import textline
from workbench.ontology_build.parsers.base import failure
from workbench.ontology_build.parsers.textline import FactSink, failed_segment, finish, rel_of

try:            # PyYAML 是工作台既有核心依赖；缺失时显式失败，不自动安装
    import yaml
except ImportError:  # pragma: no cover - 仅在缺少依赖的环境出现
    yaml = None

ANCHOR_NOTE_LIMIT = 20


def _problem_position(exc):
    """从 PyYAML 异常里取问题位置（行/列）；取不到时返回 (0, 0)。"""
    mark = getattr(exc, 'problem_mark', None) or getattr(exc, 'context_mark', None)
    if mark is None:
        return 0, 0
    return int(getattr(mark, 'line', 0)) + 1, int(getattr(mark, 'column', 0)) + 1


def _anchor_stats(text):
    """统计锚点/别名：返回 `(锚点名列表, 别名名列表)`；扫描失败返回 None（不影响解析）。"""
    if yaml is None:
        return None
    anchors, aliases = [], []
    try:
        for token in yaml.scan(text):
            name = type(token).__name__
            value = getattr(token, 'value', '')
            if name == 'AnchorToken':
                anchors.append(str(value))
            elif name == 'AliasToken':
                aliases.append(str(value))
    except Exception:  # noqa: BLE001 - 标记扫描只是注记来源，失败不影响已加载的树
        return None
    return anchors, aliases


def _anchor_notes(anchors, aliases):
    if anchors is None:
        return []
    notes = []
    if anchors or aliases:
        notes.append('YAML 锚点 %d 个%s、别名 %d 个%s：PyYAML 已在加载时展开为普通节点，'
                     '事实按展开后的结构登记。'
                     % (len(anchors), _names(anchors), len(aliases), _names(aliases)))
        if aliases:
            notes.append('别名展开只会登记一份结构（按锚点定义处的节点），'
                         '引用处的行号/位置不单独产出事实。')
    return notes


def _names(values):
    if not values:
        return ''
    shown = '、'.join(list(dict.fromkeys(values))[:ANCHOR_NOTE_LIMIT])
    return '（%s）' % shown


def parse(path, material_id, rel_path=''):
    """解析 YAML：加载后按 JSON 同口径遍历；坏 YAML 显式失败。"""
    rel = rel_of(path, rel_path)
    if yaml is None:
        return failure('未安装 PyYAML，无法解析 YAML 材料（不自动安装依赖）。')
    try:
        text, encoding, size = structwalk.read_document_text(path)
    except (OSError, IOError) as exc:
        return failure('读取文件失败：%s' % exc)
    except ValueError as exc:
        return failure(str(exc))

    try:
        documents = list(yaml.safe_load_all(text))
    except Exception as exc:  # yaml.YAMLError（子类含 ScannerError/ParserError/ComposerError）
        return _syntax_failure(exc, rel, text, encoding, size)

    sink = FactSink(material_id)
    notes, failed, stats_all = [], [], []
    anchor_note = _anchor_notes(*_anchor_stats(text)) if isinstance(text, str) else []
    notes.extend(anchor_note)
    multi = len(documents) > 1
    for index, document in enumerate(documents):
        base_path = '$' if index == 0 else '$[doc%d]' % (index + 1)
        stats = structwalk.walk_tree(document, sink, rel, locator_kind='yaml', module='yaml',
                                     base_path=base_path)
        stats_all.append(stats)
    for stats in stats_all:
        notes.extend(stats.get('notes') or [])
    notes.append('YAML 与 JSON 使用同一树遍历口径：叶子标量一条事实（locator.path 可回放）、'
                 '数组只产出摘要事实，深度上限与事实上限一致。')
    if multi:
        notes.append('多文档 YAML：共 %d 个文档（`---` 分隔），第 1 个路径根为 `$`，'
                     '其余为 `$[docN]`。' % len(documents))
    notes.append('YAML 重复键由 PyYAML 按「后者覆盖」处理，本解析器不额外检测；'
                 '非字符串键在路径中呈现为 `["<文本>"]`。')
    notes.append('未执行：safe_load 拒绝 !!python/... 等危险标签、不执行材料内容、不访问外链。')
    if encoding not in textline.UTF8_ALIASES:
        notes.append('按 %s 编码解码（非 utf-8）。' % encoding)

    empty = all(document is None for document in documents) or not documents
    if not sink.facts:
        if empty:
            return finish(sink, ['yaml'], notes + ['YAML 文件为空（无可解析节点）。'], partial=True)
        return finish(sink, ['yaml'], notes + ['YAML 中没有可登记的叶子标量或数组。'], partial=True)
    return finish(sink, ['yaml'], notes, failed,
                  partial=bool(multi or any(stats.get('notes') or stats.get('stopped')
                                            for stats in stats_all)),
                  documents=len(documents))


def _syntax_failure(exc, rel, text, encoding, size):
    """坏 YAML：显式 failure + failedSegments（行/列），零事实、不回退兜底。"""
    line, column = _problem_position(exc)
    reason = 'YAML 语法错误：%s' % str(getattr(exc, 'problem', None) or exc).splitlines()[0]
    lines = str(text or '').splitlines()
    excerpt = lines[line - 1].strip()[:200] if 1 <= line <= len(lines) else ''
    locator = {'kind': 'yaml', 'file': rel, 'line': line, 'column': column, 'path': '$'}
    failed = [failed_segment('statement', locator, reason, excerpt)]
    notes = [
        'YAML 解析失败，产出 0 条事实；失败位置见 failedSegments（行/列）。',
        '不回退 LLM 兜底或文本线索：语法损坏的材料必须修复后单物料重试（需求 §2）。',
        '已读取 %d 字节，按 %s 解码。' % (size, encoding or 'utf-8'),
    ]
    return failure(reason, coverage={'modules': ['yaml'], 'notes': notes, 'failedSegments': failed})
