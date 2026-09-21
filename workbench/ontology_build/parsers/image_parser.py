"""图片解析（V2-2 / G18）：SVG 确定性文本提取 + 位图 OCR。

支持格式（需求 §5.1 支持矩阵）：jpg/jpeg/png/gif/bmp/tiff/tif/webp/svg。

能力边界：
* SVG（文本格式）：用标准库 ElementTree 提取 `<text>/<tspan>/<title>/<desc>` 等全部
  文本内容，**确定性解析、不需要 OCR**；无文本元素的 SVG 明确报告（不假称成功）。
* 位图：Pillow 读图 + pytesseract OCR（可选依赖；见 ocr_support）。
  - OCR 未配置 → 整份材料 failed，失败原因带状态码 **ocr_unconfigured**；
  - 识别调用失败 → failed，原因 **ocr_failed**；
  - 识别完成但无文字 → failed，原因 **ocr_empty**。
  绝不把未配置/失败的图片当空文件继续（G18：不假称解析成功）。
* 产物事实：整图级定位 `{'kind':'image','file',region,'segment'}`，quality='medium'
  （OCR 文本按解析质量分级呈现，**不因来自图片而豁免证据状态判定**——需求 §5.1）。
* 不执行：不渲染脚本、不访问外链、不执行图片内嵌元数据指令。

安全：ElementTree 禁用外部实体解析（Python 标准库默认不解析外部实体，且我们不读取
任何 DOCTYPE 引用的外部资源）；OCR 只把像素送本地 tesseract，不联网。
"""
import xml.etree.ElementTree as ET

from workbench.ontology_build.parsers.base import failure
from workbench.ontology_build.parsers.ocr_support import OcrError, image_from_path, image_to_text, ocr_status
from workbench.ontology_build.parsers.textline import FactSink, failed_segment, finish, rel_of

MAX_SEGMENT_CHARS = 480     # 单条事实承载的 OCR 文本长度（Fact.snippet 上限 500 以内）
MAX_FACTS = 50              # 单图事实条数上限（超出截断并记 notes）


def file_ext(rel_path):
    name = str(rel_path or '').replace('\\', '/').rsplit('/', 1)[-1].lower()
    if '.' not in name:
        return ''
    return name.rsplit('.', 1)[-1]


def parse(path, material_id, rel_path=''):
    """解析一份图片材料：SVG 走确定性文本提取，位图走 OCR。"""
    rel = rel_of(path, rel_path)
    ext = file_ext(rel)
    if ext == 'svg':
        return _parse_svg(path, material_id, rel)
    return _parse_bitmap(path, material_id, rel)


def _local_tag(node):
    return str(getattr(node, 'tag', '') or '').rsplit('}', 1)[-1].lower()


def _parse_svg(path, material_id, rel):
    try:
        root = ET.parse(str(path)).getroot()
    except ET.ParseError as exc:
        return failure('SVG 无法解析（结构错误）：%s' % exc)
    except (OSError, IOError) as exc:
        return failure('读取文件失败：%s' % exc)
    except ValueError as exc:  # 例如路径为目录
        return failure('无法读取材料：%s' % exc)
    # 文本只在 <text> 元素里（含嵌套 <tspan>）；itertext 取整段避免父子重复计数。
    texts = []
    for node in root.iter():
        if _local_tag(node) != 'text':
            continue
        content = ' '.join(''.join(node.itertext()).split())
        if content:
            texts.append(content)
    meta = []
    for node in root.iter():
        if _local_tag(node) in ('title', 'desc') and (node.text or '').strip():
            meta.append(node.text.strip()[:200])
    sink = FactSink(material_id)
    if not texts:
        reason = 'SVG 内没有可提取的文本元素（svg_empty）：图片无文字内容，未产出事实。'
        return failure(reason,
                       coverage={'modules': ['svg', 'image'],
                                 'notes': [reason] + ['元数据：%s' % item for item in meta[:3]],
                                 'failedSegments': [failed_segment(
                                     'image', _locator(rel),
                                     'SVG 未包含文本元素（svg_empty），未产出任何事实。')]})
    joined = '\n'.join(texts)
    segments = _segments(joined)
    for index, segment in enumerate(segments, start=1):
        sink.add('svg', _locator(rel, index), segment, 'svgText',
                 {'segment': index, 'segments': len(segments), 'vector': True}, 'medium')
    notes = ['SVG 为矢量文本格式：直接提取文本元素（确定性解析，未调用 OCR）。',
             '提取 %d 段文本、%d 个文本节点；title/desc 元数据不计入正文事实。'
             % (len(segments), len(texts))]
    notes += ['元数据：%s' % item for item in meta[:3]]
    notes.append('事实 quality=medium：文本内容精确，但图形语义（形状/颜色/布局）未解析。')
    return finish(sink, ['svg', 'image'], notes, partial=False, region='文本元素',
                  textChars=len(joined))


def _parse_bitmap(path, material_id, rel):
    available, reason = ocr_status()
    if not available:
        # G18：OCR 未配置 → 该文件明确失败（ocr_unconfigured），绝不假称解析成功
        return failure(reason,
                       coverage={'modules': ['image', 'ocr'], 'notes': [
                           '图片需要 OCR 才能提取文字；OCR 未配置时不产出事实，'
                           '也不把图片当空文件继续。',
                           '可在安装 pytesseract + tesseract 后对单份材料执行「重试解析」。'],
                           'failedSegments': [failed_segment(
                               'image', _locator(rel), reason + '（ocr_unconfigured）')]})
    try:
        image = image_from_path(path)
    except OcrError as exc:
        return failure(str(exc), _failure_coverage(rel, str(exc)))
    try:
        text, ocr_notes = image_to_text(image)
    except OcrError as exc:
        return failure(str(exc), _failure_coverage(rel, str(exc)))
    stripped = text.strip()
    if not stripped:
        message = 'OCR 识别完成但未得到文字（ocr_empty）：图片可能不含文字或对比度过低。'
        return failure(message, _failure_coverage(rel, message))
    sink = FactSink(material_id)
    segments = _segments(stripped)
    for index, segment in enumerate(segments, start=1):
        sink.add('ocr', _locator(rel, index), segment, 'ocrText',
                 {'segment': index, 'segments': len(segments), 'chars': len(segment),
                  'ocr': True}, 'medium')
    notes = ['OCR 引擎：本地 tesseract（pytesseract）；识别文字 %d 字符，产出 %d 段事实。'
             % (len(stripped), len(segments))]
    notes += [ocr_notes] if ocr_notes else []
    notes.append('事实 quality=medium：OCR 文本按解析质量分级呈现，不豁免证据状态判定；'
                 '整图级定位（图片区域级文字框不在本期范围）。')
    return finish(sink, ['image', 'ocr'], notes, partial=False, region='整图',
                  textChars=len(stripped), ocr=True)


def _locator(rel, segment=1):
    return {'kind': 'image', 'file': rel, 'region': '整图', 'segment': segment}


def _segments(text, limit=MAX_SEGMENT_CHARS, max_facts=MAX_FACTS):
    text = str(text or '').strip()
    out = []
    while text and len(out) < max_facts:
        out.append(text[:limit])
        text = text[limit:].strip()
    return out


def _failure_coverage(rel, message):
    return {'modules': ['image', 'ocr'],
            'notes': ['图片需要 OCR 才能提取文字；失败时不产出事实，也不把图片当空文件继续。',
                      '可修复环境后对单份材料执行「重试解析」。'],
            'failedSegments': [failed_segment('image', _locator(rel), message)]}
