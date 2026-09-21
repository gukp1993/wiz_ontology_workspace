"""PDF 解析：文本型 PDF 逐页提取；扫描页检测与逐页失败报告。

能力边界（支持 / 降级 / 不执行）
--------------------------------
* 支持：优先使用 `pypdf`，其次 `PyPDF2`（两者都缺失 → 返回
  failure('未安装 PDF 解析库，请安装 pypdf')，**绝不自动 pip install**）。逐页提取文本，
  每页产出 1–3 条事实（首行摘要 + 关键词/行线索），带真实页码定位。
* 降级（写入 notes / failedSegments / warnings，绝不假称解析成功）：
  - 单页提取文本为空或字符数 < 20 → 判定为**扫描页/图片页**，该页记 failedSegments
    并标注“需要 OCR”；本模块**不提供 OCR**（未配置 OCR 能力，不假装识别图片文字）。
  - 加密 PDF：尝试空口令解密；失败 → failure 明确原因（不暴力破解）。
  - 加密/损坏的对象流、无法确定页数的坏文件 → failure。
  - 表格与版面顺序：PDF 无稳定表格结构，只保留行文本，页内顺序可能与视觉顺序不同，
    该限制写入 notes。
  - 每页事实条数受限（最多 3 条），超长行截断到 400 字（不放大存储）。
* 不执行：不 pip install、不执行 PDF 内嵌 JavaScript/动作/启动脚本、不访问外链、
  不下载引用内容、不做 OCR、不渲染页面。

定位器：`{'kind':'pdf','file':rel_path,'page':页码}`（1 基真实页码）。
"""

import re

from workbench.ontology_build.parsers.base import failure
from workbench.ontology_build.parsers.textline import FactSink, failed_segment, finish, rel_of

MIN_PAGE_CHARS = 20          # 低于该字符数判定为扫描页/空白页
MAX_FACTS_PER_PAGE = 3
MAX_LINE_CHARS = 400
KEYWORD_LIMIT = 12

_STOPWORDS = {
    'the', 'and', 'for', 'with', 'this', 'that', 'which', 'are', 'was', 'were', 'has', 'have',
    'from', 'into', 'not', 'all', 'any', 'can', 'will', 'shall', 'may', 'must', 'its', 'their',
    'you', 'your', 'our', 'they', 'been', 'when', 'then', 'than', 'there', 'here', 'such',
    '的', '和', '与', '或', '为', '是', '在', '有', '对', '从', '到', '了', '并', '被', '把',
    '该', '其', '等', '中', '上', '下', '不', '也', '即', '及', '以', '将', '可', '需', '应',
}
_WORD = re.compile(r'[A-Za-z][A-Za-z0-9_\-]{2,}|[\u4e00-\u9fff]{2,6}')


def _load_library():
    """按 pypdf → PyPDF2 顺序尝试；返回 (模块, 名称) 或 (None, '')。"""
    try:
        import pypdf  # type: ignore
        return pypdf, 'pypdf'
    except Exception:  # noqa: BLE001 - 缺失依赖必须优雅降级
        pass
    try:
        import PyPDF2  # type: ignore
        return PyPDF2, 'PyPDF2'
    except Exception:  # noqa: BLE001
        pass
    return None, ''


def _keywords(text, limit=KEYWORD_LIMIT):
    """高频词/中文词组（不执行任何外部处理，仅统计）。"""
    counts = {}
    for match in _WORD.finditer(text):
        word = match.group(0)
        lowered = word.lower()
        if lowered in _STOPWORDS or len(lowered) < 2:
            continue
        counts[word] = counts.get(word, 0) + 1
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [word for word, _ in ordered[:limit]]


def _page_lines(text):
    return [line.strip() for line in str(text or '').splitlines() if line.strip()]


def parse(path, material_id, rel_path=''):
    """解析 PDF：打开文件后交给 `_extract` 逐页提取。"""
    rel = rel_of(path, rel_path)
    library, library_name = _load_library()
    if library is None:
        return failure('未安装 PDF 解析库，请安装 pypdf')
    try:
        handle = open(str(path), 'rb')
    except (OSError, IOError) as exc:
        return failure('读取文件失败：%s' % exc)
    try:
        # pypdf 惰性读取：文件句柄必须存活到解析结束。旧实现用 `with open(...)` 构造
        # PdfReader 后就关闭句柄，任何文本型 PDF 都会在取页数时抛「seek of closed file」
        # → 整份材料失败（D15：文本型 PDF 在交付环境不可用）。
        return _extract(library, library_name, handle, rel, material_id)
    finally:
        try:
            handle.close()
        except Exception:  # noqa: BLE001 - 关闭失败不得覆盖解析结果
            pass


def _extract(library, library_name, handle, rel, material_id):
    """在句柄保持打开的前提下逐页提取文本并产出事实。"""
    try:
        reader = library.PdfReader(handle)
    except Exception as exc:  # noqa: BLE001 - 损坏/加密/未知结构必须显式失败
        return failure('打开 PDF 失败（文件可能损坏或结构不受支持）：%s: %s'
                       % (exc.__class__.__name__, str(exc)[:200]))

    notes, failed, warnings = [], [], []
    sink = FactSink(material_id)
    decrypt_note = ''
    if getattr(reader, 'is_encrypted', False):
        try:
            result = reader.decrypt('')
        except Exception as exc:  # noqa: BLE001
            return failure('PDF 已加密且无法用空口令解密：%s' % exc)
        if not result:
            return failure('PDF 已加密（需要口令），不尝试破解；请提供解密后的文件。')
        decrypt_note = 'PDF 已加密，使用空口令（owner password 为空）成功打开。'
        warnings.append(decrypt_note)

    try:
        page_count = len(reader.pages)
    except Exception as exc:  # noqa: BLE001
        return failure('无法确定 PDF 页数：%s' % exc)

    text_chars = 0
    scanned_pages = []
    table_hint_pages = []
    for index in range(page_count):
        page_number = index + 1
        try:
            page = reader.pages[index]
            text = page.extract_text() or ''
        except Exception as exc:  # noqa: BLE001 - 单页失败不能中断整份文件
            failed.append(failed_segment(
                'page', {'kind': 'pdf', 'file': rel, 'page': page_number},
                '该页文本提取失败（对象损坏或使用了不支持的编码）：%s: %s'
                % (exc.__class__.__name__, str(exc)[:120])))
            continue
        stripped = text.strip()
        text_chars += len(stripped)
        if len(stripped) < MIN_PAGE_CHARS:
            scanned_pages.append(page_number)
            failed.append(failed_segment(
                'page', {'kind': 'pdf', 'file': rel, 'page': page_number},
                '该页文本仅 %d 个字符（< %d），判定为扫描页/图片页，需要 OCR；'
                '本模块不提供 OCR，未产出该页事实。' % (len(stripped), MIN_PAGE_CHARS)))
            continue

        lines = _page_lines(stripped)
        first_line = lines[0][:MAX_LINE_CHARS] if lines else ''
        joined = '\n'.join(lines)
        keywords = _keywords(joined)
        data = {'page': page_number, 'lineCount': len(lines), 'chars': len(stripped),
                'firstLine': first_line, 'keywords': keywords,
                'pageCount': page_count, 'extractor': library_name}
        sink.add('pdf', {'kind': 'pdf', 'file': rel, 'page': page_number},
                 first_line or '（本页仅有空白/符号）', 'pageSummary', data, 'high')
        if len(lines) > 1:
            middle = lines[1][:MAX_LINE_CHARS]
            sink.add('pdf', {'kind': 'pdf', 'file': rel, 'page': page_number},
                     middle, 'pageLine',
                     {'page': page_number, 'line': 2, 'text': middle,
                      'extractor': library_name}, 'medium')
        if len(lines) > 2:
            tail = lines[-1][:MAX_LINE_CHARS]
            if tail != middle:
                sink.add('pdf', {'kind': 'pdf', 'file': rel, 'page': page_number},
                         tail, 'pageTail',
                         {'page': page_number, 'line': len(lines), 'text': tail,
                          'extractor': library_name}, 'medium')
        if re.search(r'\s{4,}\S+\s{4,}', joined) or '\t' in joined:
            table_hint_pages.append(page_number)

    if decrypt_note:
        notes.append(decrypt_note)
    if scanned_pages:
        notes.append('扫描页/图片页 %d 页（%s）：需要 OCR 才能获取文本，当前未提供 OCR；'
                     '这些页已在 failedSegments 逐页列出。'
                     % (len(scanned_pages), '、'.join(str(page) for page in scanned_pages[:20])))
        warnings.append('存在扫描页，材料文本覆盖不完整（需要 OCR）。')
    if table_hint_pages:
        notes.append('检测到疑似表格页面（多列空白对齐）：PDF 无稳定表格结构，仅保留行文本，'
                     '列对应关系需要人工确认。页：%s'
                     % '、'.join(str(page) for page in table_hint_pages[:20]))
    notes.append('使用 %s 提取文本，共 %d 页、%d 字符；每页最多 %d 条事实（首行摘要/行线索/页尾）。'
                 % (library_name, page_count, text_chars, MAX_FACTS_PER_PAGE))
    notes.append('版面说明：PDF 不保留可靠的段落/标题/表格结构，页内行顺序可能与视觉顺序不同，'
                 '事实 quality 按摘要=high、行线索=medium 标注。')
    notes.append('未执行：不 pip install、不执行 PDF 内嵌 JavaScript/动作、不访问外链、不做 OCR。')

    if not sink.facts:
        # 全部页面都失败（例如整份是扫描件）：保留失败明细，partial=True，绝不当作成功
        return finish(sink, ['pdf'],
                      notes + ['PDF 未提取到任何文本（可能全部为图片/扫描页，需要 OCR）。'],
                      failed, partial=True, warnings=warnings)
    return finish(sink, ['pdf'], notes, failed, partial=bool(failed), warnings=warnings,
                  pageCount=page_count, scannedPages=scanned_pages,
                  extractor=library_name)
