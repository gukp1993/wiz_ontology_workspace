"""格式黑名单三层（需求 v2 V2-4 / G20）：硬（安全边界）/ 默认软（任务级可覆盖）/ 任务级自定义追加。

判定优先级（需求 §5.4 冻结，不可调整）：
    硬黑名单 > 用户后缀白名单 > 默认软黑名单 > 任务级自定义追加

* 硬黑名单是安全边界：二进制可执行/库文件、密钥凭据、构建/依赖目录、
  大型未知二进制。任何配置（白名单、任务覆盖）都不能越过。
* 默认软黑名单：音频/视频、嵌套归档（zip 之外的 7z/rar/tar.gz 等）、
  .doc（提示转 .docx）、.db/.sqlite/.mdb。任务可用自己的软名单整体覆盖。
* 任务级自定义追加：用户在本任务内追加的排除后缀。
* 用户后缀白名单（08 §12.1 前端后缀过滤同源口径）：命中白名单的后缀
  越过软黑名单与自定义追加，但绝不越过硬黑名单。

本模块是纯函数（不 import 存储/材料层），供 materials.py（上传登记与 ZIP 展开）、
路由层（任务过滤设置、过滤报告视图）与前端契约（能力接口的黑名单枚举）共同引用。
被过滤条目一律由调用方登记为过滤事件（layer + rule），绝不静默消失（G20）。
"""
import fnmatch

# --- 硬黑名单（安全边界，不可配置）-------------------------------------------------
# 可执行/库/字节码 + 密钥凭据后缀。密钥类沿用既有上传排除口径并显式化：
# pem/key（密钥）、pub（公钥）、pfx/p12（证书包）、jks（Java 密钥库）、env（环境变量凭据）。
HARD_EXTS = frozenset({
    'exe', 'dll', 'so', 'class', 'jar', 'bin',      # 二进制可执行 / 库 / 字节码
    'pem', 'key', 'pub', 'pfx', 'p12', 'jks', 'env',  # 密钥与凭据
})
# 硬黑名单目录：任一路径段命中即整条过滤（构建产物、依赖与版本控制目录）。
HARD_DIRS = frozenset({'.git', 'node_modules', 'target', 'build', 'dist', '__pycache__'})
# 硬黑名单文件名通配（fnmatch，对文件名匹配）：.env 及 .env.local 等变体、
# 前端构建产物与锁文件（沿用既有 ZIP 排除口径，升级为显式硬规则）。
HARD_GLOBS = ('.env*', '*.min.js', '*.map', '*.lock')

# --- 默认软黑名单（任务级可整体覆盖）-----------------------------------------------
SOFT_MEDIA_EXTS = frozenset({
    'mp3', 'wav', 'flac', 'ogg', 'm4a', 'aac', 'wma',            # 音频
    'avi', 'mp4', 'mov', 'mkv', 'webm', 'wmv', 'flv', 'm4v', 'mpg', 'mpeg',  # 视频
})
SOFT_ARCHIVE_EXTS = frozenset({'7z', 'rar', 'tar', 'gz', 'tgz', 'bz2', 'xz', 'zst'})  # zip 之外的嵌套归档
SOFT_LEGACY_DOC_EXTS = frozenset({'doc'})     # 提示转换为 .docx
SOFT_DATABASE_EXTS = frozenset({'db', 'sqlite', 'sqlite3', 'mdb'})  # 二进制数据库文件
SOFT_EXTS = SOFT_MEDIA_EXTS | SOFT_ARCHIVE_EXTS | SOFT_LEGACY_DOC_EXTS | SOFT_DATABASE_EXTS

# 层级取值（过滤事件 layer 字段；空串 = 未被过滤）
LAYER_HARD = 'hard'
LAYER_SOFT = 'soft'
LAYER_CUSTOM = 'custom'

# 大型未知二进制的硬过滤阈值与“已知文档/文本后缀”豁免表（自 materials.py 收口到本模块，
# 避免循环导入；materials.py 引用这里的常量保持原行为不变）。
LARGE_BINARY_BYTES = 8 * 1024 * 1024
DOC_EXTS = frozenset({'docx', 'doc', 'pdf', 'xlsx', 'xls', 'pptx', 'ppt', 'md', 'markdown',
                      'txt', 'csv', 'rtf'})
TEXT_EXTS = frozenset({'java', 'kt', 'kts', 'scala', 'js', 'jsx', 'mjs', 'cjs', 'ts', 'tsx',
                       'vue', 'py', 'json', 'xml', 'yaml', 'yml', 'properties', 'gradle', 'sql',
                       'go', 'rs', 'cs', 'php', 'rb', 'sh', 'html', 'htm', 'css', 'less', 'scss',
                       'ini', 'cfg', 'conf', 'toml', 'rst', 'log', 'bat', 'cmd', 'ps1', 'proto',
                       'graphql', 'hbs', 'ejs', 'jsp', 'ftl', 'tpl'})

_HARD_RULE_EXT = '硬黑名单后缀 .%s（安全边界，不可配置）'
_HARD_RULE_DIR = '硬黑名单目录 %s/（安全边界，不可配置）'
_HARD_RULE_GLOB = '硬黑名单文件名匹配 %s（安全边界，不可配置）'
_SOFT_RULE = '默认软黑名单后缀 .%s（任务级可覆盖）'
_CUSTOM_RULE = '任务级追加排除后缀 .%s'
_SIZE_RULE = '超过 %d MB 的非文档/非文本二进制（安全边界，不可配置）'


def normalize_ext(value):
    """单个后缀输入 → '.ext' 规范形态（小写、去前导点）；非法/空返回 ''。"""
    text = str(value or '').strip().lower().lstrip('.')
    if not text or '/' in text or '\\' in text or any(ch.isspace() for ch in text):
        return ''
    return '.' + text


def normalize_ext_list(values):
    """后缀列表 → 排序去重后的 ['.ext', ...]；非法项丢弃。接受字符串（分隔符切分）。"""
    if isinstance(values, str):
        values = values.replace('；', ',').replace('、', ',').split(',')
    out = set()
    for item in values if isinstance(values, (list, tuple)) else []:
        ext = normalize_ext(item)
        if ext:
            out.add(ext)
    return sorted(out)


def file_ext(rel_path):
    """取路径最后一段的后缀（含点、小写；无后缀返回 ''）。"""
    name = str(rel_path or '').replace('\\', '/').rsplit('/', 1)[-1].lower()
    if '.' not in name[1:]:  # '.env' 这类隐藏文件：首字符后的点才算后缀分隔
        return ''
    return name.rsplit('.', 1)[-1] if '.' in name else ''


def _hard_hit(rel_path, size=0):
    """硬黑名单判定；返回命中规则文案或 ''。绝不接受任何豁免。"""
    parts = [part.lower() for part in str(rel_path or '').replace('\\', '/').split('/') if part]
    if not parts:
        return '空路径'
    for part in parts[:-1] or []:
        if part in HARD_DIRS:
            return _HARD_RULE_DIR % part
    name = parts[-1]
    for pattern in HARD_GLOBS:
        if fnmatch.fnmatch(name, pattern):
            return _HARD_RULE_GLOB % pattern
    ext = file_ext(name)
    if ext in HARD_EXTS:
        return _HARD_RULE_EXT % ext
    # 大型未知二进制（沿用既有 ZIP 展开口径）：既不是已知文档也不是已知文本后缀时，
    # 超过大型二进制阈值按硬黑名单处理，避免把巨型二进制读进解析管线。
    if int(size or 0) > LARGE_BINARY_BYTES:
        if ext not in DOC_EXTS and ext not in TEXT_EXTS:
            return _SIZE_RULE % (LARGE_BINARY_BYTES // (1024 * 1024))
    return ''


def evaluate(rel_path, spec=None, size=0):
    """按三层优先级判定一条路径。

    返回 {'filtered': bool, 'layer': 'hard'|'soft'|'custom'|'', 'rule': str, 'bypassed': bool}。
    * spec 为任务过滤设置（task_view 的 filter 结构）：{'allowExts': [...], 'softExts': [...]|None,
      'excludeExts': [...]}；None 表示未配置（软名单用默认值、无白名单、无追加）。
    * bypassed=True 表示该路径命中软/自定义名单、但被用户白名单豁免（仍可上传/解析）。
    * 硬黑名单命中时 filtered=True 且不可被 bypass。
    """
    hard_rule = _hard_hit(rel_path, size)
    if hard_rule:
        return {'filtered': True, 'layer': LAYER_HARD, 'rule': hard_rule, 'bypassed': False}
    spec = spec if isinstance(spec, dict) else {}
    ext = file_ext(rel_path)
    allow = {item.lower() for item in (spec.get('allowExts') or [])}
    custom = {item.lower() for item in (spec.get('excludeExts') or [])}
    soft_values = spec.get('softExts')
    soft = {item.lower() for item in soft_values} if isinstance(soft_values, list) else None
    dotted_ext = ('.' + ext) if ext else ''
    if dotted_ext and dotted_ext in allow:
        if soft and dotted_ext in soft:
            return {'filtered': False, 'layer': LAYER_SOFT, 'rule': _SOFT_RULE % ext, 'bypassed': True}
        if dotted_ext in custom:
            return {'filtered': False, 'layer': LAYER_CUSTOM, 'rule': _CUSTOM_RULE % ext, 'bypassed': True}
        return {'filtered': False, 'layer': '', 'rule': '', 'bypassed': False}
    if soft is None:
        soft = {('.' + item) for item in SOFT_EXTS}
    if dotted_ext and dotted_ext in soft:
        return {'filtered': True, 'layer': LAYER_SOFT, 'rule': _SOFT_RULE % ext, 'bypassed': False}
    if dotted_ext and dotted_ext in custom:
        return {'filtered': True, 'layer': LAYER_CUSTOM, 'rule': _CUSTOM_RULE % ext, 'bypassed': False}
    return {'filtered': False, 'layer': '', 'rule': '', 'bypassed': False}


def is_hard_blocked(rel_path, size=0):
    """仅硬黑名单判定（不读任务设置）：上传安全边界用。"""
    return bool(_hard_hit(rel_path, size))


def normalize_spec(spec):
    """任务过滤设置规范化：非法输入丢弃；softExts None 保持 None（用默认软名单）。"""
    spec = spec if isinstance(spec, dict) else {}
    soft = spec.get('softExts')
    return {
        'allowExts': normalize_ext_list(spec.get('allowExts') or []),
        'softExts': normalize_ext_list(soft) if isinstance(soft, list) else None,
        'excludeExts': normalize_ext_list(spec.get('excludeExts') or []),
    }


def default_spec():
    """未配置时的等价任务设置（软名单用默认值）。"""
    return {'allowExts': [], 'softExts': None, 'excludeExts': []}


def effective_soft_exts(spec=None):
    """当前生效的软名单（'.ext' 列表；任务覆盖或默认），供能力接口/前端展示。"""
    spec = spec if isinstance(spec, dict) else {}
    soft = spec.get('softExts')
    if isinstance(soft, list):
        return sorted({item.lower() for item in soft})
    return sorted({('.' + item) for item in SOFT_EXTS})
