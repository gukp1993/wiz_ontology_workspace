"""从物料构建本体：共享常量、状态枚举、限额与路径/类型工具（唯一口径）。

所有模块只从这里取状态字符串、限额与 ID 前缀；接口文档 08 分册与前端必须与此一致。
本模块不 import 存储层，保持可被解析器/前端契约测试单独加载。
"""
import re

# --- 限额（工程保守默认，能力接口原样展示；不得宣称更大容量） ---
CHUNK_BYTES = 512 * 1024
# 分片 base64 文本上限：由 CHUNK_BYTES 独立推出（4*ceil(chunk/3)），
# 与通用参数上限（512 字符）无关；否则 >384 字节的分片必然被 400 拒收（D01）。
CHUNK_BASE64_CHARS = ((CHUNK_BYTES + 2) // 3) * 4
FILE_BYTES = 50 * 1024 * 1024
TASK_BYTES = 200 * 1024 * 1024
ZIP_EXPANDED_BYTES = 300 * 1024 * 1024
ZIP_MAX_ENTRIES = 10000
PARSE_TIMEOUT_SECONDS = 120
UPLOAD_TTL_SECONDS = 30 * 60
MAX_CANDIDATES_PER_BATCH = 500
MAX_FACTS_PER_MATERIAL = 20000
LLM_BATCH_FACTS = 40
RUN_WORKERS = 2
SNIPPET_LIMIT = 500
PROMPT_VERSION = 'v1'
PARSER_VERSION = 'v1'

LIMITS = {
    'chunkBytes': CHUNK_BYTES,
    'fileBytes': FILE_BYTES,
    'taskBytes': TASK_BYTES,
    'zipExpandedBytes': ZIP_EXPANDED_BYTES,
    'zipMaxEntries': ZIP_MAX_ENTRIES,
    'parseTimeoutSeconds': PARSE_TIMEOUT_SECONDS,
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
MATERIAL_KINDS = ('code', 'ddl', 'docx', 'pdf', 'xlsx', 'md', 'zip', 'other')
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
    'java', 'kt', 'scala', 'js', 'jsx', 'ts', 'tsx', 'vue', 'py', 'pyw', 'json', 'xml',
    'yaml', 'yml', 'properties', 'gradle', 'sql', 'go', 'rs', 'cs', 'php', 'rb', 'sh',
}
_DOC_EXT = {
    'docx': 'docx', 'doc': 'docx-convert-hint', 'pdf': 'pdf', 'xlsx': 'xlsx',
    'xls': 'xlsx-convert-hint', 'md': 'md', 'markdown': 'md', 'txt': 'md',
}


def detect_kind(rel_path, head=b''):
    """按后缀 + 头部魔数判定材料类型，返回 MATERIAL_KINDS 之一。

    后缀与魔数冲突时以魔数为准（保守）；未知后缀按内容探测再降级 other。
    """
    name = str(rel_path or '').rsplit('/', 1)[-1].lower()
    ext = name.rsplit('.', 1)[-1] if '.' in name else ''
    magic = bytes(head[:8])
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
    if ext == 'sql':
        return 'ddl'
    if ext in _DOC_EXT:
        mapped = _DOC_EXT[ext]
        if mapped == 'docx-convert-hint':
            return 'docx'
        if mapped == 'xlsx-convert-hint':
            return 'xlsx'
        return mapped
    if ext in _CODE_EXT:
        return 'code'
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
