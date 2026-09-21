"""OCR 可选依赖探测与调用（V2-2 / G18）。

能力边界（本模块 docstring 即对外承诺）：
* OCR 是**可选依赖**：pytesseract + Pillow + tesseract 二进制任一缺失即「未配置」，
  绝不自动 pip install、绝不让解析器因缺依赖崩溃。
* 扫描版 PDF 的整页 OCR 还需要**页面渲染依赖**（PyMuPDF 优先，其次 pdf2image+poppler）；
  两者都缺失时扫描页按 ocr_unconfigured 逐页报告，不假称识别成功。
* 状态码（写入 material.error / coverage.failedSegments[].reason，G18 逐文件可见）：
    ocr_unconfigured —— 依赖缺失（未配置）
    ocr_failed      —— 已配置但识别调用失败（异常）
    ocr_empty       —— 识别完成但没有可用文本
* 识别语言优先 chi_sim+eng（中文+英文）；语言包缺失时退回引擎默认（英文），
  该回退写入 notes，不静默。

本模块不持有任何网络/进程资源；`ocr_status()` 结果按进程缓存（tesseract 安装状态
在进程生命周期内不会变化）。测试可通过重置 `_STATUS` 缓存或直接替换
`image_to_text` 注入假 OCR（见 tests/test_ontology_build.py 的 G18 用例）。
"""

_STATUS = {}


class OcrError(Exception):
    """OCR 失败（code ∈ ocr_unconfigured / ocr_failed / ocr_empty；message 中文可读）。"""

    def __init__(self, code, message):
        self.code = str(code or 'ocr_failed')
        super().__init__(message)


def _probe():
    """探测一次完整 OCR 链路；返回 (available, reason)。"""
    try:
        import pytesseract  # noqa: F401
    except Exception as exc:  # noqa: BLE001 - 缺依赖属正常降级路径
        return False, 'OCR 未配置（ocr_unconfigured）：未安装 pytesseract（%s）' % exc.__class__.__name__
    try:
        import PIL  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        return False, 'OCR 未配置（ocr_unconfigured）：未安装 Pillow（%s）' % exc.__class__.__name__
    try:
        pytesseract.get_tesseract_version()
    except Exception as exc:  # noqa: BLE001 - tesseract 二进制缺失/不可执行
        return False, ('OCR 未配置（ocr_unconfigured）：tesseract 引擎不可用'
                       '（请安装 tesseract 并确认在 PATH 中；%s）' % exc.__class__.__name__)
    return True, ''


def ocr_status():
    """返回 (available: bool, reason: str)；reason 非 available 时含状态码 ocr_unconfigured。"""
    if 'available' not in _STATUS:
        _STATUS['available'], _STATUS['reason'] = _probe()
    return _STATUS['available'], _STATUS['reason']


def _require_available():
    available, reason = ocr_status()
    if not available:
        raise OcrError('ocr_unconfigured', reason)


def _image_to_text(image):
    """对 PIL Image 做 OCR；内部函数（供 image_to_text 与 PDF 页共用）。"""
    import pytesseract
    text = ''
    fallback_note = ''
    for langs in ('chi_sim+eng', None):
        try:
            if langs:
                text = pytesseract.image_to_string(image, lang=langs)
            else:
                text = pytesseract.image_to_string(image)
            break
        except Exception as exc:  # noqa: BLE001 - 语言包缺失等：退回默认语言
            fallback_note = '语言包 chi_sim 不可用，已退回引擎默认语言（%s）' % exc.__class__.__name__
            continue
    if not text and fallback_note:
        raise OcrError('ocr_failed', 'OCR 识别失败（ocr_failed）：%s' % fallback_note)
    return str(text or ''), fallback_note


def image_to_text(image):
    """对 PIL Image 做 OCR，返回 (text, notes)；未配置抛 OcrError(ocr_unconfigured)。"""
    _require_available()
    try:
        return _image_to_text(image)
    except OcrError:
        raise
    except Exception as exc:  # noqa: BLE001 - 识别异常统一 ocr_failed
        raise OcrError('ocr_failed', 'OCR 识别失败（ocr_failed）：%s: %s'
                       % (exc.__class__.__name__, str(exc)[:200])) from None


def image_from_path(path):
    """读取图片文件为 PIL Image；文件损坏/格式不受支持抛 OcrError(ocr_failed)。"""
    try:
        from PIL import Image
    except Exception as exc:  # noqa: BLE001
        raise OcrError('ocr_unconfigured', 'OCR 未配置（ocr_unconfigured）：未安装 Pillow（%s）'
                       % exc.__class__.__name__) from None
    try:
        image = Image.open(str(path))
        image.load()
        return image
    except Exception as exc:  # noqa: BLE001
        raise OcrError('ocr_failed', '图片无法读取或格式不受支持（ocr_failed）：%s: %s'
                       % (exc.__class__.__name__, str(exc)[:200])) from None


def render_pdf_page(path, page_number):
    """把 PDF 指定页（1 基）渲染为 PIL Image；无渲染依赖抛 OcrError(ocr_unconfigured)。

    渲染依赖优先 PyMuPDF（fitz，纯 pip 安装），其次 pdf2image（额外需要系统 poppler）。
    """
    _require_available()
    try:
        import fitz  # type: ignore
        from PIL import Image
    except ImportError:
        fitz = None
    if fitz is not None:
        try:
            document = fitz.open(str(path))
            try:
                pix = document[max(0, int(page_number) - 1)].get_pixmap(dpi=150)
                return Image.frombytes('RGB', (pix.width, pix.height), pix.samples)
            finally:
                document.close()
        except OcrError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise OcrError('ocr_failed', 'PDF 页面渲染失败（ocr_failed）：%s: %s'
                           % (exc.__class__.__name__, str(exc)[:200])) from None
    try:
        from pdf2image import convert_from_path
    except Exception as exc:  # noqa: BLE001
        raise OcrError('ocr_unconfigured',
                       '扫描页 OCR 未配置（ocr_unconfigured）：缺少 PDF 页面渲染依赖'
                       '（需要 PyMuPDF 或 pdf2image+poppler；%s）' % exc.__class__.__name__) from None
    try:
        pages = convert_from_path(str(path), dpi=150,
                                  first_page=max(1, int(page_number)),
                                  last_page=max(1, int(page_number)))
    except Exception as exc:  # noqa: BLE001
        raise OcrError('ocr_failed', 'PDF 页面渲染失败（ocr_failed）：%s: %s'
                       % (exc.__class__.__name__, str(exc)[:200])) from None
    if not pages:
        raise OcrError('ocr_failed', 'PDF 页面渲染失败（ocr_failed）：未得到页面图像')
    return pages[0]


def pdf_page_text(path, page_number):
    """扫描页 → OCR 文本；返回 (text, notes)。失败抛 OcrError（code 见类定义）。"""
    image = render_pdf_page(path, page_number)
    return image_to_text(image)
