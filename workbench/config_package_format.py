"""配置迁移包：格式常量、有界安全解析与 ZIP 安全读取（20260919 需求 T2）。

职责边界：
* 本模块只做「格式与安全」：manifest 校验、ZIP 成员安全检查、JSON 有界解析、
  命名后缀分配、敏感结构值剥离。不懂业务依赖、不触数据库。
* 一切读取有界：解压合计/条目数/单文件大小超限一律拒绝，绝不 extractall。
"""
import hashlib
import json
import re
import zipfile

PACKAGE_FORMAT = 'wiz-workbench-config-package'
FORMAT_VERSION = 1
PRODUCER_VERSION = '1.0'

# 技术上限（接口文档 07 §1；调整须先改文档）
CHUNK_SIZE = 512 * 1024
MAX_PACKAGE_BYTES = 20 * 1024 * 1024
MAX_UNCOMPRESSED = 100 * 1024 * 1024
MAX_ENTRIES = 2000
MAX_FILE_BYTES = 10 * 1024 * 1024
STAGE_TTL_SECONDS = 30 * 60
MAX_STAGED_PER_OWNER = 2
MAX_EXPORT_PREVIEWS_PER_OWNER = 2

PAYLOAD_FORMATS = {'workbench-state-1', 'release-state-1', 'project-state-1', 'flow-state-1'}
ASSET_KINDS = {'model', 'project', 'flow'}
NAME_MAX = 80
SUFFIX_FIRST = '（导入）'
SUFFIX_MORE = '（导入{name}）'


class PackageFormatError(ValueError):
    """包损坏/格式不支持/预算超限。调用方映射 415/422。"""


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ── ZIP 有界安全解析 ────────────────────────────────────────────────────────────

def _safe_member_name(name: str) -> bool:
    """拒绝绝对路径、.. 穿越、反斜杠、盘符。"""
    if not name or name.endswith('/'):
        return False
    if '\\' in name or name.startswith('/'):
        return False
    if re.match(r'^[A-Za-z]:', name):
        return False
    parts = name.split('/')
    return not any(p in ('..', '.') or p == '' for p in parts)


def read_package(data: bytes) -> dict:
    """把 ZIP 字节解析为 {path: bytes}；全部预算与安全检查在此。

    manifest.json 必须存在且第一个读取；条目/解压体积/单文件超限、
    穿越名、目录项、重复规范化路径一律 PackageFormatError。
    """
    if len(data) > MAX_PACKAGE_BYTES:
        raise PackageFormatError(f'配置包超过 {MAX_PACKAGE_BYTES // (1024 * 1024)} MiB 限制。')
    try:
        zf = zipfile.ZipFile(io_bytes(data))
    except zipfile.BadZipFile:
        raise PackageFormatError('配置包不是有效的 ZIP 文件，或已损坏。')
    with zf:
        infos = zf.infolist()
        if len(infos) > MAX_ENTRIES:
            raise PackageFormatError(f'配置包内文件数超过 {MAX_ENTRIES} 上限。')
        total = 0
        files: dict[str, bytes] = {}
        seen: set[str] = set()
        for info in infos:
            if info.is_dir():
                continue
            name = info.filename
            if not _safe_member_name(name):
                raise PackageFormatError(f'配置包内存在不安全的路径「{name}」，已拒绝导入。')
            if name in seen:
                raise PackageFormatError(f'配置包内存在重复路径「{name}」，已拒绝导入。')
            seen.add(name)
            if info.file_size > MAX_FILE_BYTES:
                raise PackageFormatError(f'配置包内单文件「{name}」超过 10 MiB 限制。')
            total += info.file_size
            if total > MAX_UNCOMPRESSED:
                raise PackageFormatError('配置包解压后总大小超过 100 MiB 限制。')
            try:
                files[name] = zf.read(info)
            except zipfile.BadZipFile:
                raise PackageFormatError(f'配置包内文件「{name}」已损坏。')
    if 'manifest.json' not in files:
        raise PackageFormatError('配置包缺少 manifest.json，不是本工作台导出的配置包。')
    return files


class io_bytes:
    """zipfile 需要可读对象；包已整体在内存（≤20MiB），直接包装。"""

    def __init__(self, data: bytes):
        import io
        self._fp = io.BytesIO(data)

    def __getattr__(self, item):
        return getattr(self._fp, item)


# ── manifest 校验 ───────────────────────────────────────────────────────────────

def _load_json_bounded(raw: bytes, path: str, depth_limit: int = 24) -> object:
    if len(raw) > MAX_FILE_BYTES:
        raise PackageFormatError(f'文件「{path}」超过 10 MiB 限制。')
    text = raw.decode('utf-8', errors='strict') if len(raw) < 64 * 1024 * 1024 else ''
    try:
        value = json.loads(text, parse_constant=_reject_constant)
    except (ValueError, UnicodeDecodeError) as exc:
        raise PackageFormatError(f'文件「{path}」不是有效的 UTF-8 JSON。') from exc
    _check_depth(value, path, depth_limit)
    return value


def _reject_constant(name):
    raise PackageFormatError(f'JSON 含非法数字常量（{name}）。')


def _check_depth(value, path, limit, _d=0):
    if _d > limit:
        raise PackageFormatError(f'文件「{path}」JSON 嵌套过深（>{limit} 层）。')
    if isinstance(value, dict):
        for k, v in value.items():
            if not isinstance(k, str):
                raise PackageFormatError(f'文件「{path}」JSON 键必须是字符串。')
            _check_depth(v, path, limit, _d + 1)
    elif isinstance(value, list):
        for v in value:
            _check_depth(v, path, limit, _d + 1)


def parse_manifest(files: dict) -> dict:
    """校验 manifest 结构与逐文件 hash；返回 manifest dict。"""
    manifest = _load_json_bounded(files['manifest.json'], 'manifest.json')
    if not isinstance(manifest, dict):
        raise PackageFormatError('manifest.json 必须是 JSON 对象。')
    if manifest.get('format') != PACKAGE_FORMAT:
        raise PackageFormatError('这不是本工作台的配置包（format 不符）。')
    if manifest.get('formatVersion') != FORMAT_VERSION:
        raise PackageFormatError(f"配置包版本不支持（formatVersion={manifest.get('formatVersion')}），"
                                 '请使用本工作台导出的配置包。')
    caps = manifest.get('requiredCapabilities') or []
    if not isinstance(caps, list):
        raise PackageFormatError('manifest.requiredCapabilities 必须是数组。')
    assets = manifest.get('assets')
    if not isinstance(assets, list) or not assets:
        raise PackageFormatError('manifest.assets 缺失或为空。')
    keys: set[str] = set()
    for i, asset in enumerate(assets):
        where = f'assets[{i}]'
        if not isinstance(asset, dict):
            raise PackageFormatError(f'manifest.{where} 必须是对象。')
        kind = asset.get('kind')
        key = asset.get('packageKey')
        if kind not in ASSET_KINDS:
            raise PackageFormatError(f'manifest.{where}.kind 无效（{kind}）。')
        if not isinstance(key, str) or not key:
            raise PackageFormatError(f'manifest.{where}.packageKey 缺失。')
        if key in keys:
            raise PackageFormatError(f'manifest 中 packageKey「{key}」重复。')
        keys.add(key)
        if asset.get('payloadFormat') not in PAYLOAD_FORMATS:
            raise PackageFormatError(f'manifest.{where}.payloadFormat 不支持（{asset.get("payloadFormat")}）。')
        for field in ('draftPath',):
            if not isinstance(asset.get(field), str) or not asset[field]:
                raise PackageFormatError(f'manifest.{where}.{field} 缺失。')
        if asset['draftPath'] not in files:
            raise PackageFormatError(f'manifest.{where}.draftPath「{asset["draftPath"]}」在包内不存在。')
        releases = asset.get('releases') or []
        if not isinstance(releases, list):
            raise PackageFormatError(f'manifest.{where}.releases 必须是数组。')
        for r in releases:
            if not isinstance(r, dict) or not isinstance(r.get('path'), str) or r['path'] not in files:
                raise PackageFormatError(f'manifest.{where}.releases 引用的文件在包内不存在。')
    file_table = manifest.get('files')
    if not isinstance(file_table, dict):
        raise PackageFormatError('manifest.files 缺失。')
    for path, meta in file_table.items():
        if path not in files:
            raise PackageFormatError(f'manifest.files 登记的「{path}」在包内不存在。')
        if not isinstance(meta, dict) or not isinstance(meta.get('sha256'), str):
            raise PackageFormatError(f'manifest.files「{path}」缺少 sha256。')
        if sha256_hex(files[path]) != meta['sha256'].lower():
            raise PackageFormatError(f'文件「{path}」校验失败：内容与 manifest 记录的 SHA-256 不一致。')
    extra = set(files) - set(file_table) - {'manifest.json'}  # manifest 不自登记（hash 无法自包含）
    if extra:
        raise PackageFormatError('配置包存在 manifest 未登记的文件：' + '、'.join(sorted(extra)[:5]))
    if manifest.get('credentialsExcluded') is not True:
        raise PackageFormatError('manifest.credentialsExcluded 必须为 true（本版不迁移凭据）。')
    return manifest


# ── 命名（同名后缀）────────────────────────────────────────────────────────────

def clean_name(name: str) -> str:
    return str(name or '').strip()


def allocate_name(source_name: str, taken: set) -> tuple[str, str]:
    """分配目标名：原名可用则原名；否则「N（导入）」「N（导入2）」递增。

    taken：当前账号同类已有 name_key ∪ 本次包内已占用。返回 (final_name, reason)。
    主体按 NAME_MAX 截短预留后缀空间；后缀递增直到不冲突。
    """
    base = clean_name(source_name)
    if not base:
        base = '未命名'
    from workbench.storage.assets import name_key
    if name_key(base) not in taken:
        return base, ''
    counter = 1
    while True:
        suffix = SUFFIX_FIRST if counter == 1 else SUFFIX_MORE.format(name=counter)
        room = NAME_MAX - len(suffix)
        stem = base[:room] if len(base) > room else base
        candidate = stem + suffix
        if name_key(candidate) not in taken:
            return candidate, ('当前账号已有同名' if counter == 1 else '名称冲突')
        counter += 1


# ── 敏感结构值剥离（导出侧）────────────────────────────────────────────────────

SENSITIVE_HEADER_RE = re.compile(r'^(authorization|cookie|set-cookie|x-api-key|api-key|proxy-authorization)$', re.I)
SENSITIVE_URL_RE = re.compile(r'://[^\s/:@]+:[^\s/@]+@')
SENSITIVE_KEY_RE = re.compile(r'^(password|secret|api_key|apikey|token)$', re.I)


def strip_sensitive(value, path: str, stripped: list):
    """递归剥离已知结构敏感值；返回新值，stripped 追加 {path, reason}。不含明文。"""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            where = f'{path}.{k}' if path else str(k)
            if isinstance(k, str) and SENSITIVE_HEADER_RE.match(k or ''):
                stripped.append({'path': where, 'reason': '认证/会话头已剥离'})
                out[k] = ''
                continue
            if isinstance(k, str) and SENSITIVE_KEY_RE.match(k or '') and isinstance(v, str) and v:
                stripped.append({'path': where, 'reason': '疑似秘密字段已剥离'})
                out[k] = ''
                continue
            if isinstance(v, str) and SENSITIVE_URL_RE.search(v):
                stripped.append({'path': where, 'reason': '带凭据的 URL 已剥离'})
                out[k] = ''
                continue
            out[k] = strip_sensitive(v, where, stripped)
        return out
    if isinstance(value, list):
        return [strip_sensitive(v, f'{path}[{i}]', stripped) for i, v in enumerate(value)]
    return value
