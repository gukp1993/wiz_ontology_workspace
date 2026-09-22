"""从物料构建本体：共享常量、状态枚举、限额与路径/类型工具（唯一口径）。

所有模块只从这里取状态字符串、限额与 ID 前缀；接口文档 08 分册与前端必须与此一致。
本模块不 import 存储层，保持可被解析器/前端契约测试单独加载。
"""
import os
import re

# --- 限额（工程保守默认，能力接口原样展示；不得宣称更大容量） ---
CHUNK_BYTES = 512 * 1024
# 分片 base64 文本上限：由 CHUNK_BYTES 独立推出（4*ceil(chunk/3)），
# 与通用参数上限（512 字符）无关；否则 >384 字节的分片必然被 400 拒收（D01）。
CHUNK_BASE64_CHARS = ((CHUNK_BYTES + 2) // 3) * 4
# 2026-09-21 实测试点临时放宽（用户指令，仅本工作树实例）：容纳 177MB 项目 zip 全量上传；
# 正式默认值与可配置化待样本校准后另行定稿（开发计划 §1 规模未定项）。
FILE_BYTES = 256 * 1024 * 1024
TASK_BYTES = 512 * 1024 * 1024
ZIP_EXPANDED_BYTES = 512 * 1024 * 1024
ZIP_MAX_ENTRIES = 20000
PARSE_TIMEOUT_SECONDS = 120
# LIMITS 暴露给能力接口的键（parseConcurrency 为 V2-10/G25 新增，值随 env 覆盖变化）
UPLOAD_TTL_SECONDS = 30 * 60
MAX_CANDIDATES_PER_BATCH = 500
MAX_FACTS_PER_MATERIAL = 20000
# 2026-09-22 实测修正：40 条/批会让模型为每批产出 40 组候选 JSON，输出超 max_tokens
# 被截断（真实复现：40 条 → finish_reason=length）。降到 20 条，输出量减半且更稳。
LLM_BATCH_FACTS = 20
RUN_WORKERS = 2
SNIPPET_LIMIT = 500
PROMPT_VERSION = 'v1'
# 2026-09-22：结构化格式解析支持上线（六新 kind + JSON-LD/CSV 等专用解析器），
# 解析行为变化 → 版本提升；旧批次基线以 v1 记录，可据此区分。
PARSER_VERSION = 'v2'
# V2-3（G19）LLM 兜底解析限额：单任务兜底文件数 / 字节数（跨多次扫描/单物料重试累计，
# 不因重试重置——已消耗量按任务全部 scan 运行检查点累计，见 storage.scan_fallback_usage）；
# 超限回退文本线索降级并在扫描报告注明。可配置：环境变量在服务启动前覆盖默认值
# （正整数；未设置/非法用默认，改动需重启服务生效），capabilities 的 llmFallback
# 展示当前生效值。
LLM_FALLBACK_SLICE_CHARS = 6000
LLM_FALLBACK_MAX_SLICES = 16


def _limit_from_env(name, default):
    """从环境变量读取正整数限额；未设置/非法/非正值返回默认（可配置限额的唯一入口）。"""
    raw = str(os.environ.get(name) or '').strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


LLM_FALLBACK_MAX_FILES = _limit_from_env('WIZ_BUILD_LLM_FALLBACK_MAX_FILES', 200)
LLM_FALLBACK_MAX_BYTES = _limit_from_env('WIZ_BUILD_LLM_FALLBACK_MAX_BYTES', 50 * 1024 * 1024)
# V2-10（G25）解析并发度：默认 min(8, CPU 核数)，环境变量 WIZ_BUILD_PARSE_CONCURRENCY
# 覆盖（正整数，非法/非正值回退默认）；扫描 run 内线程池的 worker 上限，worker 只解析
# 不写库（落库串行在 run 主线程，08 §4）。
PARSE_CONCURRENCY = _limit_from_env('WIZ_BUILD_PARSE_CONCURRENCY', min(8, os.cpu_count() or 2))

# 抽取批次的并发度（2026-09-22）：批次之间无依赖（各自独立调模型、结果按序落库），
# 串行等待是纯浪费——实测单批 57.7 秒、2 批 190 秒。并发默认 4，env 可覆盖；
# 落库仍按批次序号串行（保 facts 顺序确定性，与 V2-10 解析并发同一原则）。
# 默认 1（串行）：实测 provider 对多路并发大请求会限流（MiniMax 账号级 429）
# 或显著变慢（单批 57 秒 → 121 秒+），并发反而让整批失败；默认串行最稳。
# 需要加速时用 env WIZ_BUILD_LLM_CONCURRENCY 显式开启（如额度充足时设 3–4）。
LLM_CONCURRENCY = _limit_from_env('WIZ_BUILD_LLM_CONCURRENCY', 1)

LIMITS = {
    'chunkBytes': CHUNK_BYTES,
    'fileBytes': FILE_BYTES,
    'taskBytes': TASK_BYTES,
    'zipExpandedBytes': ZIP_EXPANDED_BYTES,
    'zipMaxEntries': ZIP_MAX_ENTRIES,
    'parseTimeoutSeconds': PARSE_TIMEOUT_SECONDS,
    'parseConcurrency': PARSE_CONCURRENCY,
}

# --- 状态 ---
TASK_STATUSES = ('draft', 'materials', 'scope', 'generating', 'review', 'delivered')
TASK_STAGE_LABELS = {
    'draft': '新建',
    'materials': '添加物料',
    'scope': '确定范围',
    'generating': '生成中',
    'review': '评审初稿',
    'delivered': '已创建本体',
}
MATERIAL_PARSE_STATES = ('pending', 'running', 'success', 'partial', 'failed', 'excluded')
# 结构化格式解析支持（需求《结构化格式解析支持_v1》v1.1 §3）：json/yaml/properties/csv/ini/toml
# 为新增专用 kind（登记表 kind→解析器一对一，进三级分派第①层，不再走 LLM 兜底/文本线索降级）。
MATERIAL_KINDS = ('code', 'ddl', 'docx', 'pdf', 'xlsx', 'md', 'image', 'zip', 'other',
                  'json', 'yaml', 'properties', 'csv', 'ini', 'toml')
RUN_KINDS = ('scan', 'dialog', 'generate')
RUN_STATES = ('queued', 'running', 'succeeded', 'failed', 'cancelled', 'interrupted')
GENERATE_STAGES = ('retrieve', 'align', 'abstract', 'verify', 'adapt')
GENERATE_STAGE_LABELS = {
    'retrieve': '筛选材料片段',
    'align': '跨来源对齐证据',
    'abstract': '抽象候选定义',
    'verify': '校验证据与引用',
    'adapt': '适配本体协议',
}
SCAN_STAGES = ('parse', 'index')
SCAN_STAGE_LABELS = {'parse': '解析材料', 'index': '建立结构索引'}
EVIDENCE_STATUSES = ('supported', 'inferred', 'conflict', 'insufficient')
EVIDENCE_STATUS_LABELS = {
    'supported': '有依据',
    'inferred': '推断待确认',
    'conflict': '来源冲突',
    'insufficient': '材料不足',
}
DECISIONS = ('include', 'defer', 'exclude')
DECISION_LABELS = {'include': '拟纳入', 'defer': '暂缓', 'exclude': '已排除'}
CANDIDATE_TYPES = ('object', 'property', 'link', 'rule', 'action')
TYPE_LABELS = {'object': '对象', 'property': '属性', 'link': '链接', 'rule': '规则', 'action': '动作'}

# 现有本体协议的数据类型枚举（与 model_format.py / modelFormat.ts 对齐）
PROPERTY_DATA_TYPES = ('text', 'number', 'boolean', 'dateTime', 'array', 'struct', 'timeSeries')
# 时间序列观测值类型：必须与 workbench/model_format.py 的 SERIES_VALUE_TYPES 完全一致（D02）。
# model_format 校验 dataType.valueType ∈ SERIES_VALUE_TYPES，这里漂移一格就会出现
# 「预检 ok=true、deliver 400」的假成功；改一处必改另一处并在 08 分册登记。
VALUE_TYPES = ('string', 'double', 'decimal', 'integer', 'boolean', 'date', 'dateTime')

# --- ID 前缀 ---
TASK_PREFIX = 'bk-'
MATERIAL_PREFIX = 'bm-'
FACT_PREFIX = 'bf-'
MESSAGE_PREFIX = 'bg-'
RUN_PREFIX = 'br-'
BATCH_PREFIX = 'bb-'
CANDIDATE_PREFIX = 'bc-'
OP_PREFIX = 'bo-'
DELIVERY_PREFIX = 'bd-'

# --- 路径安全 ---
_MAX_REL_PATH = 512
_WINDOWS_RESERVED = re.compile(r'[<>:"|?*\x00-\x1f]')


class InvalidPath(ValueError):
    pass


def safe_rel_path(rel_path):
    """校验并规范化浏览器提交的相对路径；非法一律抛 InvalidPath（HTTP 400）。

    规则：非空、≤512 字符、无控制字符/Windows 保留字符、不允许绝对路径与盘符、
    不允许 `..` 段、不允许段内为空或只有空白、不允许尾随分隔符。
    返回规范化（统一 `/`）的相对路径。
    """
    raw = str(rel_path or '')
    if not raw or len(raw) > _MAX_REL_PATH:
        raise InvalidPath('材料路径为空或过长')
    if _WINDOWS_RESERVED.search(raw):
        raise InvalidPath('材料路径包含非法字符')
    normalized = raw.replace('\\', '/')
    if normalized.startswith('/') or re.match(r'^[A-Za-z]:', normalized):
        raise InvalidPath('不允许绝对路径')
    parts = [p for p in normalized.split('/')]
    if not parts or any(not p or not p.strip() for p in parts):
        raise InvalidPath('材料路径存在空段')
    if any(p in ('.', '..') for p in parts):
        raise InvalidPath('材料路径不允许 . 或 .. 段')
    if parts[-1].endswith('.'):
        raise InvalidPath('材料路径以点结尾')
    return '/'.join(parts)


_CODE_EXT = {
    'java', 'kt', 'scala', 'js', 'jsx', 'ts', 'tsx', 'vue', 'py', 'pyw', 'xml',
    'gradle', 'sql', 'go', 'rs', 'cs', 'php', 'rb', 'sh',
}
# 结构化格式后缀 → 新 kind（需求 §2/§3/§5 矩阵；detect_kind 在魔数判定之后、_CODE_EXT 之前
# 命中本表）。`.json/.yaml/.yml/.properties` 原先经 _CODE_EXT 归 code 并在 code 解析器内部降级，
# 本表上线后改走专用解析层；`.jsonid` 为用户真实样本的非标准扩展名（JSON-LD 内容），按 json 处理；
# `.jsonl/.ndjson` 由 json 解析器按后缀内部走逐行模式（json_parser.is_line_mode）。
_STRUCTURED_KIND_EXT = {
    'json': 'json', 'jsonld': 'json', 'jsonid': 'json',
    'jsonl': 'json', 'ndjson': 'json',
    'yaml': 'yaml', 'yml': 'yaml',
    'properties': 'properties',
    'csv': 'csv', 'tsv': 'csv',
    'ini': 'ini', 'cfg': 'ini', 'conf': 'ini',
    'toml': 'toml',
}
_DOC_EXT = {
    'docx': 'docx', 'doc': 'docx-convert-hint', 'pdf': 'pdf', 'xlsx': 'xlsx',
    'xls': 'xlsx-convert-hint', 'md': 'md', 'markdown': 'md', 'txt': 'md',
}
# V2-2（G18）：图片物料（jpg/jpeg/png/gif/bmp/tiff/tif/webp/svg）走 OCR/矢量文本解析
_IMAGE_EXT = {'jpg', 'jpeg', 'png', 'gif', 'bmp', 'tif', 'tiff', 'webp', 'svg'}


def detect_kind(rel_path, head=b''):
    """按后缀 + 头部魔数判定材料类型，返回 MATERIAL_KINDS 之一。

    后缀与魔数冲突时以魔数为准（保守）；未知后缀按内容探测再降级 other。
    """
    name = str(rel_path or '').rsplit('/', 1)[-1].lower()
    ext = name.rsplit('.', 1)[-1] if '.' in name else ''
    magic = bytes(head[:12])
    if magic.startswith(b'PK\x03\x04'):
        if ext == 'docx':
            return 'docx'
        if ext == 'xlsx':
            return 'xlsx'
        if ext == 'zip':
            return 'zip'
        return 'zip'
    if magic.startswith(b'%PDF'):
        return 'pdf'
    if magic.startswith(b'\xd0\xcf\x11\xe0'):
        return 'docx' if ext in ('doc', 'docx') else ('xlsx' if ext in ('xls', 'xlsx') else 'other')
    if ext in _IMAGE_EXT and (
            magic.startswith(b'\x89PNG') or magic.startswith(b'\xff\xd8\xff')
            or magic.startswith((b'GIF87a', b'GIF89a'))
            or (magic.startswith(b'BM') and ext in ('bmp',))
            or magic.startswith((b'II*\x00', b'MM\x00*'))
            or (magic[:4] == b'RIFF' and magic[8:12] == b'WEBP')
            or ext == 'svg'):
        return 'image'
    if magic.startswith(b'\x89PNG') or magic.startswith(b'\xff\xd8\xff') \
            or magic.startswith((b'GIF87a', b'GIF89a')) \
            or magic.startswith((b'II*\x00', b'MM\x00*')) \
            or (magic[:4] == b'RIFF' and magic[8:12] == b'WEBP'):
        return 'image'
    if ext == 'sql':
        return 'ddl'
    if ext in _DOC_EXT:
        mapped = _DOC_EXT[ext]
        if mapped == 'docx-convert-hint':
            return 'docx'
        if mapped == 'xlsx-convert-hint':
            return 'xlsx'
        return mapped
    # 结构化格式（json/yaml/properties/csv/ini/toml）：魔数判定之后、代码/其他之前。
    if ext in _STRUCTURED_KIND_EXT:
        return _STRUCTURED_KIND_EXT[ext]
    if ext in _CODE_EXT:
        return 'code'
    if ext in _IMAGE_EXT:
        return 'image'
    return 'other'


def clamp_snippet(text, limit=SNIPPET_LIMIT):
    """单向截断原文片段（保留可读性标记），绝不放大存储。"""
    value = str(text or '')
    if len(value) <= limit:
        return value
    return value[:limit] + '…（截断）'


def default_decision(evidence_status, has_structural_issues):
    """默认人工决定：有依据且结构完整 → include；弱证据/冲突/结构问题 → defer。"""
    if evidence_status == 'supported' and not has_structural_issues:
        return 'include'
    return 'defer'


def provider_fingerprint(provider):
    """模型配置指纹（不含密钥）：固定到任务基线与批次比较。"""
    return {
        'id': str(provider.get('id') or ''),
        'name': str(provider.get('name') or ''),
        'model': str(provider.get('model') or ''),
    }
