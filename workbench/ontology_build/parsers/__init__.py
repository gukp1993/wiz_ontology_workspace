"""解析器包：登记全部格式适配器，并对外提供 `parse_material` 统一入口。

契约见 `workbench/ontology_build/parsers/base.py`（Fact / ParseResult / failure / register /
REGISTRY / parse 已冻结，本包不改动它）。适配器只产出带精确定位的 `Fact`，不产生候选定义；
候选由生成管线在证据之上抽象。

已登记 kind → 适配器：
    code  → code.parse       Java/Spring/MyBatis/JPA、JS/TS/Vue、Python
    ddl   → ddl.parse        SQL DDL（MySQL 常见方言优先）
    docx  → docx_parser.parse  DOCX 段落/标题/表格（.doc 明确拒绝并提示转换）
    pdf   → pdf_parser.parse   文本型 PDF；扫描页走 OCR 路径（未配置逐页报告，V2-2）
    xlsx  → xlsx_parser.parse  XLSX 多 sheet/表头/单元格/公式文本与缓存值
    md    → markdown_parser.parse  MD 标题层级/段落/表格/代码块
    image → image_parser.parse  位图 OCR（pytesseract 可选依赖）+ SVG 文本提取（V2-2）
    other → textline.parse   未知类型文本线索降级（明确标注覆盖不足）
    zip   → 不在此包解析：ZIP 由材料层安全展开成条目后再按扩展名分派（见 materials.py）

安全边界（所有适配器共同遵守）：
* 纯标准库 + 可选 pypdf/PyPDF2（PDF 专用；缺失时返回 failure 且绝不 pip install）。
* 绝不执行材料：不 eval/exec、不运行代码或 SQL、不执行公式/宏、不打开外链、不访问网络。
* docx/xlsx 在解析前先调用 `materials.zip_bomb_guard`（若该模块缺失则跳过守卫，不因此失败）；
  该守卫依据 ZIP 目录里的声明值（条目数、声明展开总量、单条目压缩比），声明值可被打包方伪造，
  所以部件读取另由 `parsers/zipguard.py` 按**实际解压字节**封顶
  （单部件 64 MiB、整包累计 `protocol.ZIP_EXPANDED_BYTES`），超限显式失败（D12）。
* 单文件事实数受 `protocol.MAX_FACTS_PER_MATERIAL` 限制，超出截断并在 coverage.notes 说明。
* 每个失败/降级都进入 `ParseResult.coverage['failedSegments']` 或 `warnings`，绝不静默跳过。
"""

from workbench.ontology_build import protocol as protocol  # 再导出：供 parsers 使用方统一从此处取常量
from workbench.ontology_build.parsers import base
from workbench.ontology_build.parsers import code
from workbench.ontology_build.parsers import ddl
from workbench.ontology_build.parsers import docx_parser
from workbench.ontology_build.parsers import image_parser
from workbench.ontology_build.parsers import markdown_parser
from workbench.ontology_build.parsers import pdf_parser
from workbench.ontology_build.parsers import textline
from workbench.ontology_build.parsers import xlsx_parser

# 登记表（base.REGISTRY 是唯一来源；这里只做一次显式登记）
base.register('code', code.parse)
base.register('ddl', ddl.parse)
base.register('docx', docx_parser.parse)
base.register('pdf', pdf_parser.parse)
base.register('xlsx', xlsx_parser.parse)
base.register('md', markdown_parser.parse)
base.register('image', image_parser.parse)
base.register('other', textline.parse)

# ZIP 守卫在展开后按条目解析；未展开的 zip 走明确的 failure，而不是猜内容
ZIP_KINDS = ('zip',)

__all__ = ['parse_material', 'REGISTRY']


def zip_bomb_guard(path):
    """调用材料层的 ZIP 炸弹守卫（存在才调用）；返回 (ok, reason)。

    守卫用于 docx/xlsx（本质是 ZIP 容器，可能被构造为解压炸弹）。导入失败时按“跳过守卫”
    处理并返回 ok=True，避免解析因为缺少材料模块而整体崩溃。
    """
    try:
        from workbench.ontology_build import materials
    except Exception as exc:  # noqa: BLE001 - 模块缺失/未就绪不应导致解析失败
        return True, '未加载 materials.zip_bomb_guard（%s），跳过 ZIP 守卫。' % exc.__class__.__name__
    guard = getattr(materials, 'zip_bomb_guard', None)
    if guard is None:
        return True, 'materials 未提供 zip_bomb_guard，跳过 ZIP 守卫。'
    try:
        result = guard(str(path))
    except Exception as exc:  # noqa: BLE001 - 守卫自身异常按跳过处理并明确记录
        return True, 'zip_bomb_guard 调用异常（%s），跳过 ZIP 守卫。' % exc.__class__.__name__
    if isinstance(result, tuple):
        ok = bool(result[0]) if result else False
        reason = str(result[1]) if len(result) > 1 else ''
        return ok, reason
    if isinstance(result, bool):
        return result, '' if result else '材料层 ZIP 守卫判定为不安全。'
    if isinstance(result, dict):
        ok = bool(result.get('ok', True))
        return ok, str(result.get('reason') or result.get('message') or '')
    return True, '无法识别的 zip_bomb_guard 返回值，按跳过处理。'


def _plan_dispatch(kind):
    """按 kind 决定实际解析器与前置守卫。"""
    kind = str(kind or '').strip() or 'other'
    if kind in ZIP_KINDS:
        return None, ['ZIP 材料需要先由材料层安全展开成条目（materials.py），'
                      '再按条目扩展名调用 parse_material；此处不猜压缩包内容。']
    if kind not in base.REGISTRY:
        return 'other', ['材料类型 %s 没有专用解析器，按文本线索降级（覆盖不足）。' % kind]
    return kind, []


def parse_material(path, material_id, kind, rel_path=''):
    """解析一份材料，返回 `base.ParseResult`。

    * kind 缺失/未知 → 走 other 的文本线索降级（明确标注覆盖不足）。
    * zip → 明确失败（需材料层先展开），不尝试猜包内内容。
    * docx/xlsx → 先调 `zip_bomb_guard`；守卫不通过则 failure（不解析可能为解压炸弹的容器）。
      guard 缺失/异常时跳过守卫继续解析，并把该事实写进 warnings。
    * 任何解析器异常都转成显式 failure，不向上抛（避免后台作业整体崩掉）。
    """
    rel = str(rel_path or '')
    actual_kind, pre_notes = _plan_dispatch(kind)
    if actual_kind is None:
        return base.failure(pre_notes[0] if pre_notes else '该材料类型不能直接解析。',
                            coverage={'modules': [], 'notes': pre_notes, 'failedSegments': []})

    extra_warnings = []
    if actual_kind in ('docx', 'xlsx'):
        ok, reason = zip_bomb_guard(path)
        if not ok:
            message = '材料未通过 ZIP 安全守卫：%s' % (reason or '展开体积或条目数超限')
            return base.failure(message, coverage={
                'modules': [], 'notes': [message], 'failedSegments': [
                    {'kind': 'document',
                     'locator': {'kind': actual_kind, 'file': rel, 'section': '', 'block': 0,
                                 'sheet': '', 'cell': ''},
                     'reason': message}]})
        if reason:
            extra_warnings.append(reason)
    if pre_notes:
        extra_warnings.extend(pre_notes)

    try:
        result = base.parse(actual_kind, path, material_id, rel)
    except Exception as exc:  # noqa: BLE001 - 适配器异常必须显式失败，不得静默
        return base.failure('解析材料失败（%s）：%s: %s'
                            % (actual_kind, exc.__class__.__name__, exc))

    if extra_warnings:
        result.warnings = list(result.warnings) + extra_warnings
    # coverage.modules 至少要有可识别的模块名，便于扫描报告展示
    modules = result.coverage.get('modules') or []
    if not modules:
        result.coverage['modules'] = [actual_kind]
    if not result.kind:
        result.kind = actual_kind
    return result


REGISTRY = base.REGISTRY
