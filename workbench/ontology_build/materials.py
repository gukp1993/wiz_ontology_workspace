"""从物料构建本体：分片上传、受管 blob 与 ZIP 安全展开（任务附件，不是本体存储）。

边界：
* 文件只落在 DATA_ROOT 下 `<root>/data/ontology-build-blobs`（目录 0700、文件 0600）与
  `<root>/data/ontology-build-tmp`；不写本体草稿、不按服务端任意路径读取、不执行材料内容。
* 数据库写入只在调用方给定的写事务 conn 内（store 层函数或 engine.text 参数化）；
  先落盘校验、后登记，未登记的内容不构成可用材料；失败只回收临时文件，不留下可用引用。
* open 上传超过 protocol.UPLOAD_TTL_SECONDS 视为过期，必须重新 init；临时文件由有界清理回收。

异常：UploadError(ValueError) 及子类，HTTP 层按 400/404/409/413/422 映射。
"""
import base64
import binascii
import fnmatch
import hashlib
import os
import re
import shutil
import stat
import zipfile
import zlib
from datetime import datetime, timezone
from pathlib import Path

from workbench.ontology_build import protocol
from workbench.paths import DATA_ROOT
from workbench.storage import engine as sto
from workbench.storage import ontology_build as store


class UploadError(ValueError):
    """上传/物料校验失败（HTTP 4xx）；失败后绝不登记可用材料。

    `code` / `status` 由 server.py 的 ValueError 分支读取，按 08 §4 映射状态码；
    基类保持 400 `INVALID_ARGUMENT`（形态错误：序号无效、base64 非法等）。
    """

    code = 'INVALID_ARGUMENT'
    status = 400


class UploadConflict(UploadError):
    """同一分片序号提交了不同内容（HTTP 409 UPLOAD_CONFLICT）。"""

    code = 'UPLOAD_CONFLICT'
    status = 409


class HashMismatch(UploadError):
    """分片或整体摘要不符（HTTP 422 HASH_MISMATCH）。"""

    code = 'HASH_MISMATCH'
    status = 422


class LimitExceeded(UploadError):
    """超出协议限额（HTTP 422 LIMIT_EXCEEDED；请求体本身过宽由 413 PAYLOAD_TOO_LARGE 拦）。"""

    code = 'LIMIT_EXCEEDED'
    status = 422


class UploadExpired(UploadError):
    """上传超过 protocol.UPLOAD_TTL_SECONDS，必须重新 init（不可续传，HTTP 409）。"""

    code = 'UPLOAD_EXPIRED'
    status = 409


class UploadNotFound(UploadError):
    """上传不存在、已结束或跨账号（按不存在处理，HTTP 404）。"""

    code = 'NOT_FOUND'
    status = 404


class ZipInvalid(UploadError):
    """压缩包结构不安全或不可读（HTTP 422 ZIP_INVALID）。"""

    code = 'ZIP_INVALID'
    status = 422


BLOB_SUBDIR = 'ontology-build-blobs'
TEMP_SUBDIR = 'ontology-build-tmp'
_DATA_SUBDIR = 'data'
# 分片元数据在 chunks_json 里的保留键（数字键只放分片 hash，二者不会撞键）
_LEN_KEY = '_len'
CLEANUP_BATCH = 200
ZIP_MAX_DEPTH = 2
LARGE_BINARY_BYTES = 8 * 1024 * 1024
_IO_BLOCK = 256 * 1024
# D12 解压炸弹口径：单条目声明展开量超过该值且压缩比超过 ZIP_BOMB_RATIO 即整包拒绝
# （小文件的天然高比例不算炸弹；1 MiB × 200:1 以下属正常文本/SQL 压缩）。
ZIP_BOMB_RATIO_MIN_BYTES = 1024 * 1024
ZIP_BOMB_RATIO = 200

# ZIP 默认排除策略（相对材料自身路径匹配；命中的条目记入 excluded_paths，不进入材料清单）
_EXCLUDE_DIRS = frozenset({'node_modules', '.git', 'dist', 'build', 'target', '__pycache__'})
_EXCLUDE_GLOBS = ('*.min.js', '*.map', '*.lock', '.env*', '*.pem', '*.key', '*.p12', '*.jks',
                  '*.jar', '*.war')
_DOC_EXTS = frozenset({'docx', 'doc', 'pdf', 'xlsx', 'xls', 'pptx', 'ppt', 'md', 'markdown',
                       'txt', 'csv', 'rtf'})
_TEXT_EXTS = frozenset({'java', 'kt', 'kts', 'scala', 'js', 'jsx', 'mjs', 'cjs', 'ts', 'tsx',
                        'vue', 'py', 'json', 'xml', 'yaml', 'yml', 'properties', 'gradle', 'sql',
                        'go', 'rs', 'cs', 'php', 'rb', 'sh', 'html', 'htm', 'css', 'less', 'scss',
                        'ini', 'cfg', 'conf', 'toml', 'rst', 'log', 'bat', 'cmd', 'ps1', 'proto',
                        'graphql', 'hbs', 'ejs', 'jsp', 'ftl', 'tpl'})

_WINDOWS_DRIVE = re.compile(r'^[A-Za-z]:')
_SAFE_EXT = re.compile(r'^[a-z0-9]{1,8}$')


# --- 受管目录与路径解析 -------------------------------------------------------------

def data_dir():
    return Path(str(DATA_ROOT)) / _DATA_SUBDIR


def blob_dir():
    return data_dir() / BLOB_SUBDIR


def temp_dir():
    return data_dir() / TEMP_SUBDIR


def _ensure_dir(path):
    Path(path).mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(str(path), 0o700)
    except OSError:
        pass
    return Path(path)


def _resolve_under_data(rel_path):
    """登记的相对路径 → DATA_ROOT/data 之内的真实路径；越界/绝对路径返回 None。"""
    raw = str(rel_path or '').strip().replace('\\', '/')
    if not raw or raw.startswith('/') or _WINDOWS_DRIVE.match(raw):
        return None
    if any(part in ('', '.', '..') for part in raw.split('/')):
        return None
    base = os.path.realpath(str(data_dir()))
    target = data_dir() / raw
    real = os.path.realpath(str(target))
    if real != base and not real.startswith(base + os.sep):
        return None
    return target


def _age_seconds(stamp_text):
    raw = str(stamp_text or '')
    if not raw:
        return None
    try:
        stamp = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - stamp).total_seconds()


def _expired(row):
    age = _age_seconds(row.get('updated_at'))
    return age is not None and age > protocol.UPLOAD_TTL_SECONDS


def _temp_path_of(row):
    """上传临时文件路径；只接受落在临时目录内的登记值（防越界删除）。"""
    resolved = _resolve_under_data(row.get('temp_path') or '')
    if resolved is None:
        return None
    if os.path.realpath(str(resolved.parent)) != os.path.realpath(str(temp_dir())):
        return None
    return resolved


def _remove_file(path):
    if path is None:
        return
    try:
        os.remove(str(path))
    except OSError:
        pass


def _head(path, count=8):
    try:
        with open(str(path), 'rb') as handle:
            return handle.read(count)
    except OSError:
        return b''


def _sha256_file(path):
    digest = hashlib.sha256()
    with open(str(path), 'rb') as handle:
        while True:
            block = handle.read(_IO_BLOCK)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _blob_filename(rel_path):
    """blob 文件名：随机不可枚举 + 保留合法后缀（解析器按扩展名分支时需要）。"""
    name = str(rel_path or '').rsplit('/', 1)[-1]
    suffix = ''
    if '.' in name:
        ext = name.rsplit('.', 1)[-1].lower()
        if _SAFE_EXT.match(ext):
            suffix = '.' + ext
    return sto.new_id() + suffix


def _max_chunks(size):
    total = int(size)
    return max(1, -(-total // protocol.CHUNK_BYTES))


# --- 上传会话 ---------------------------------------------------------------------

def _discard_upload(conn, row, state='aborted'):
    """回收临时文件、清空分片记录并标记终态（调用方事务提交后生效）。"""
    _remove_file(_temp_path_of(row))
    store.upload_chunks(conn, row['upload_id'], row.get('owner_user_id') or '', {},
                        int(row['received'] or 0))
    store.close_upload(conn, row['upload_id'], row.get('owner_user_id') or '', state)


def _require_open_upload(conn, owner_user_id, upload_id):
    row = store.get_upload(conn, upload_id, owner_user_id)
    if row is None:
        raise UploadNotFound('上传不存在')
    if row['state'] != 'open':
        raise UploadNotFound('上传已结束，请重新发起')
    if _expired(row):
        _discard_upload(conn, row)
        raise UploadExpired('上传已超过 %d 分钟，请重新发起'
                            % (protocol.UPLOAD_TTL_SECONDS // 60))
    path = _temp_path_of(row)
    if path is None or not path.is_file():
        _discard_upload(conn, row)
        raise UploadExpired('上传临时文件已清理，请重新发起上传')
    return row


def _reconcile_open_uploads(conn, owner_user_id, task_id):
    """惰性回收：过期或临时文件已丢失的 open 上传不计入任务额度（有界）。"""
    rows = store.list_uploads(conn, owner_user_id, task_id=task_id, states=('open',))
    for row in rows[:CLEANUP_BATCH]:
        path = _temp_path_of(row)
        if _expired(row) or path is None or not path.is_file():
            _discard_upload(conn, row)


def upload_init(conn, owner_user_id, task_id, rel_path, size):
    """创建上传会话与临时文件；超限/路径非法直接拒绝，不留下任何登记。"""
    owner_id = str(owner_user_id or '')
    safe_path = protocol.safe_rel_path(rel_path)
    if store.require_task(conn, task_id, owner_id) is None:
        raise UploadNotFound('生成任务不存在')
    try:
        total = int(size)
    except (TypeError, ValueError):
        raise UploadError('文件大小无效')
    if total <= 0:
        raise UploadError('文件大小无效')
    if total > protocol.FILE_BYTES:
        raise LimitExceeded('单个文件超过上限 %d 字节' % protocol.FILE_BYTES)
    _reconcile_open_uploads(conn, owner_id, task_id)
    pending = sum(int(item.get('size') or 0)
                  for item in store.list_uploads(conn, owner_id, task_id=task_id, states=('open',)))
    used = store.task_storage_bytes(conn, task_id, owner_id)
    if used + pending + total > protocol.TASK_BYTES:
        raise LimitExceeded('任务材料总量超过上限 %d 字节' % protocol.TASK_BYTES)
    upload_id = store.create_upload(conn, owner_id, task_id, safe_path, total,
                                    protocol.CHUNK_BYTES, '')
    temp_rel = '%s/%s.part' % (TEMP_SUBDIR, upload_id)
    conn.execute(sto.text('UPDATE wb_build_uploads SET temp_path = :p WHERE upload_id = :u '
                          'AND owner_user_id = :o'),
                 {'p': temp_rel, 'u': upload_id, 'o': owner_id})
    _ensure_dir(temp_dir())
    part = _resolve_under_data(temp_rel)
    try:
        handle = os.open(str(part), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.close(handle)
        os.chmod(str(part), 0o600)
    except OSError as exc:
        raise UploadError('无法创建上传临时文件：%s' % exc)
    return {'uploadId': upload_id, 'chunkBytes': protocol.CHUNK_BYTES,
            'maxChunks': _max_chunks(total), 'received': 0}


def _decode_chunk(data_b64):
    if isinstance(data_b64, bytes):
        raw = data_b64
    elif isinstance(data_b64, str):
        # 先按 base64 文本长度粗筛（D01）：标准 base64 的编码长度上界由 chunkBytes 决定，
        # 超过就一定超分片限额，直接按 LIMIT_EXCEEDED(422) 报，省掉一次无谓的解码。
        if len(data_b64) > protocol.CHUNK_BASE64_CHARS:
            raise LimitExceeded('分片超过上限 %d 字节' % protocol.CHUNK_BYTES)
        try:
            raw = data_b64.encode('ascii')
        except UnicodeEncodeError:
            raise UploadError('分片数据不是合法 base64')
    else:
        raise UploadError('分片数据不是合法 base64')
    try:
        return base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        raise UploadError('分片数据不是合法 base64')


def _chunk_index(index):
    try:
        value = int(index)
    except (TypeError, ValueError):
        raise UploadError('分片序号无效')
    if value < 0:
        raise UploadError('分片序号无效')
    return value


def _chunk_lengths(chunks):
    """分片长度表（存在保留键里，供重试时按已接受字节数定位写入偏移）。"""
    raw = chunks.get(_LEN_KEY)
    if not isinstance(raw, dict):
        return {}
    out = {}
    for key, value in raw.items():
        if str(key).isdigit():
            try:
                out[str(key)] = int(value)
            except (TypeError, ValueError):
                continue
    return out


def _write_offset(position, lengths):
    """第 position 片在临时文件中的写入偏移；缺失长度按标准分片大小回退。"""
    total = 0
    for index in range(position):
        total += lengths.get(str(index), protocol.CHUNK_BYTES)
    return total


def upload_chunk(conn, owner_user_id, upload_id, index, chunk_hash, data_b64):
    """写入一个分片：严格顺序、内容摘要校验、同 index 同 hash 幂等。"""
    owner_id = str(owner_user_id or '')
    row = _require_open_upload(conn, owner_id, upload_id)
    data = _decode_chunk(data_b64)
    if not data:
        raise UploadError('分片内容为空')
    if len(data) > protocol.CHUNK_BYTES:
        raise LimitExceeded('分片超过上限 %d 字节' % protocol.CHUNK_BYTES)
    position = _chunk_index(index)
    max_chunks = _max_chunks(row['size'])
    if position >= max_chunks:
        raise UploadError('分片序号超出范围（0..%d）' % (max_chunks - 1))
    expected = str(chunk_hash or '').strip().lower()
    chunks = dict(row['chunks'] or {})
    received = int(row['received'])
    if position < received:
        recorded = str(chunks.get(str(position)) or '')
        if recorded and recorded == expected:
            return {'received': received, 'nextIndex': received}
        raise UploadConflict('分片 %d 已提交过不同内容' % position)
    if position > received:
        raise UploadError('分片必须按顺序提交，期望序号 %d' % received)
    actual = hashlib.sha256(data).hexdigest()
    if expected != actual:
        raise HashMismatch('分片 %d 摘要不符' % position)
    part = _temp_path_of(row)
    lengths = _chunk_lengths(chunks)
    offset = _write_offset(position, lengths)
    if offset + len(data) > int(row['size']):
        raise LimitExceeded('分片累计超过文件声明大小')
    try:
        # 按已接受字节数定位后截断再写：事务忙重试会重跑整个 body，
        # 固定偏移保证重复写入自我纠正，不会把同一分片追加两次。
        handle = os.open(str(part), os.O_WRONLY | os.O_CREAT, 0o600)
        with os.fdopen(handle, 'r+b') as out:
            out.truncate(offset)
            out.seek(offset)
            out.write(data)
    except OSError as exc:
        raise UploadError('分片写入失败：%s' % exc)
    os.chmod(str(part), 0o600)
    chunks[str(position)] = actual
    lengths[str(position)] = len(data)
    chunks[_LEN_KEY] = lengths
    store.upload_chunks(conn, upload_id, owner_id, chunks, position + 1)
    return {'received': position + 1, 'nextIndex': position + 1}


def upload_complete(conn, owner_user_id, upload_id, final_hash):
    """校验整体大小与摘要后登记材料；ZIP 走安全展开，失败回收临时内容。"""
    owner_id = str(owner_user_id or '')
    row = _require_open_upload(conn, owner_id, upload_id)
    task_id = row['task_id']
    rel_path = row['rel_path']
    size = int(row['size'])
    received = int(row['received'])
    max_chunks = _max_chunks(size)
    if received != max_chunks:
        raise UploadError('分片未全部上传（%d/%d），可继续续传' % (received, max_chunks))
    part = _temp_path_of(row)
    if part is None or not part.is_file():
        _discard_upload(conn, row)
        raise UploadExpired('上传临时文件已清理，请重新发起上传')
    actual_size = part.stat().st_size
    if actual_size != size:
        _discard_upload(conn, row)
        raise HashMismatch('材料实际大小 %d 与声明 %d 不一致' % (actual_size, size))
    try:
        digest = _sha256_file(part)
    except OSError as exc:
        _discard_upload(conn, row)
        raise UploadError('材料临时文件不可读：%s' % exc)
    if digest != str(final_hash or '').strip().lower():
        # D07 口径：整体摘要与请求携带的 finalHash 不符，最可能是客户端算错了摘要——
        # 这是**可纠正的请求错误**（422 HASH_MISMATCH），不得销毁会话或临时文件，
        # 客户端带正确摘要重发 complete 即可登记。内容级损坏（实际大小≠声明）仍整包回收。
        raise HashMismatch('材料内容与声明摘要不一致')
    kind = protocol.detect_kind(rel_path, head=_head(part, 8))
    if kind == 'zip':
        return _complete_zip(conn, row, part, digest)
    source_group = digest[:16]
    existing = store.find_blob_by_hash(conn, task_id, owner_id, digest)
    if existing:
        blob_id = existing['blob_id']
        _remove_file(part)
    else:
        _ensure_dir(blob_dir())
        name = _blob_filename(rel_path)
        stored = blob_dir() / name
        try:
            os.replace(str(part), str(stored))
            os.chmod(str(stored), 0o600)
        except OSError as exc:
            _remove_file(part)
            raise UploadError('材料写入存储失败：%s' % exc)
        blob_id = store.create_blob(conn, owner_id, task_id, rel_path, size, digest,
                                    '%s/%s' % (BLOB_SUBDIR, name))
    store.upload_chunks(conn, upload_id, owner_id, {}, received)
    store.close_upload(conn, upload_id, owner_id, 'complete')
    material_id = store.create_material(conn, owner_id, task_id, blob_id, rel_path, kind, size,
                                        digest, source_group)
    return {'materials': [store.material_view(store.get_material(conn, material_id, owner_id))]}


def _complete_zip(conn, row, part, digest):
    """ZIP 上传收尾：先原子挪进受管目录再展开，容器本身按材料集合呈现。

    容器文件不登记为 blob（额度按材料字节计），展开结束后即回收；
    展开结果里的每份文件才是登记对象（共享 source_group = 容器摘要前 16 位）。
    """
    owner_id = row.get('owner_user_id') or ''
    upload_id = row['upload_id']
    _ensure_dir(blob_dir())
    staged = blob_dir() / _blob_filename(row['rel_path'])
    try:
        os.replace(str(part), str(staged))
        os.chmod(str(staged), 0o600)
    except OSError as exc:
        _remove_file(part)
        raise UploadError('压缩包写入存储失败：%s' % exc)
    try:
        materials, excluded = expand_zip(conn, owner_id, row['task_id'], staged, row['rel_path'])
    except UploadError:
        _remove_file(staged)
        _discard_upload(conn, row)
        raise
    finally:
        _remove_file(staged)
    store.upload_chunks(conn, upload_id, owner_id, {}, int(row['received']))
    store.close_upload(conn, upload_id, owner_id, 'complete')
    payload = {'materials': materials}
    if excluded:
        payload['excludedPaths'] = excluded
    return payload


def upload_abort(conn, owner_user_id, upload_id):
    """显式放弃上传：删除临时文件并把会话标为 aborted（重复调用幂等）。"""
    owner_id = str(owner_user_id or '')
    row = store.get_upload(conn, upload_id, owner_id)
    if row is None:
        raise UploadNotFound('上传不存在')
    _remove_file(_temp_path_of(row))
    store.upload_chunks(conn, upload_id, owner_id, {}, int(row['received'] or 0))
    if row['state'] != 'complete':
        store.close_upload(conn, upload_id, owner_id, 'aborted')
    return {'ok': True}


def cleanup_expired(conn, owner_user_id=None):
    """有界回收过期 open 上传的临时文件（owner 省略时跨账号清理，供启动钩子调用）。"""
    params = {'l': CLEANUP_BATCH}
    sql = "SELECT * FROM wb_build_uploads WHERE state = 'open'"
    if owner_user_id is not None:
        sql += ' AND owner_user_id = :o'
        params['o'] = str(owner_user_id or '')
    rows = conn.execute(sto.text(sql + ' ORDER BY updated_at LIMIT :l'), params).mappings().all()
    cleaned = 0
    for row in rows:
        if not _expired(dict(row)):
            continue
        _discard_upload(conn, dict(row))
        cleaned += 1
    return cleaned


def material_blob_path(conn, owner_user_id, material_id):
    """材料对应的实际文件路径；跨账号、登记缺失或文件已丢失一律返回 None。"""
    owner_id = str(owner_user_id or '')
    row = store.get_material(conn, material_id, owner_id)
    if row is None:
        return None
    blob = store.get_blob(conn, row['blob_id'], owner_id, task_id=row['task_id'])
    if not blob:
        return None
    path = _resolve_under_data(blob.get('blob_path') or '')
    if path is None or not path.is_file():
        return None
    return path


# --- ZIP 安全展开 -----------------------------------------------------------------

def zip_bomb_guard(path):
    """打开 Office/压缩包前的低成本防爆检查（只读目录，不解压）。

    返回 (ok, reason)；解析器在打开 DOCX/XLSX 前调用，避免解压炸弹。
    D12：目录声明值可被打包方伪造，这里除条目数与声明总量外，另对**单条目压缩比**
    设限（大声明量 + 极小压缩量 = 典型炸弹特征）；展开期的实际字节计数由
    `expand_zip/_write_member` 兜底（ parsers 内部的读取上限另见 parsers/zipguard）。
    """
    try:
        with zipfile.ZipFile(str(path)) as archive:
            infos = archive.infolist()
    except FileNotFoundError:
        return False, '文件不存在或已被清理'
    except (zipfile.BadZipFile, OSError):
        return False, '压缩包结构不可读'
    if len(infos) > protocol.ZIP_MAX_ENTRIES:
        return False, '压缩包条目数 %d 超过上限 %d' % (len(infos), protocol.ZIP_MAX_ENTRIES)
    total = 0
    for info in infos:
        size = max(0, int(info.file_size or 0))
        total += size
        if total > protocol.ZIP_EXPANDED_BYTES:
            return False, '解压总量超过上限 %d 字节' % protocol.ZIP_EXPANDED_BYTES
        compressed = max(0, int(info.compress_size or 0))
        if size > ZIP_BOMB_RATIO_MIN_BYTES and compressed and size > compressed * ZIP_BOMB_RATIO:
            return False, ('条目「%s」声明展开 %d 字节但压缩后仅 %d 字节（比例超过 %d:1），'
                           '判定为解压炸弹' % (info.filename, size, compressed, ZIP_BOMB_RATIO))
    return True, ''


def _member_rel_path(name):
    """zip 条目名 → 安全相对路径；结构性问题抛 ZipInvalid，形状非法返回 None（跳过）。"""
    raw = str(name or '').replace('\\', '/')
    if raw.startswith('/') or _WINDOWS_DRIVE.match(raw):
        raise ZipInvalid('压缩包包含绝对路径条目：%s' % raw)
    parts = []
    for part in raw.split('/'):
        if part in ('', '.'):
            continue
        if part == '..':
            raise ZipInvalid('压缩包包含目录穿越条目：%s' % raw)
        parts.append(part)
    if not parts:
        return None
    try:
        return protocol.safe_rel_path('/'.join(parts))
    except protocol.InvalidPath:
        return None


def _reject_special_entry(info, name):
    """只放行普通文件与目录；Unix 归档里符号链接/设备/FIFO 一律整包拒绝。

    仅当高位带上类型位时才判定：多数打包工具只写权限位（如 0o600），
    没有类型位的一律按普通文件看待。
    """
    if int(info.create_system or 0) != 3:
        return
    fmt = ((int(info.external_attr or 0) >> 16) & 0xFFFF) & 0o170000
    if fmt not in (0, stat.S_IFREG, stat.S_IFDIR):
        raise ZipInvalid('压缩包包含符号链接或特殊文件条目：%s' % name)


def _is_excluded(rel_path, size=0):
    parts = [part.lower() for part in str(rel_path or '').split('/') if part]
    if not parts:
        return True
    if any(part in _EXCLUDE_DIRS for part in parts):
        return True
    name = parts[-1]
    for pattern in _EXCLUDE_GLOBS:
        if fnmatch.fnmatch(name, pattern):
            return True
    if int(size or 0) > LARGE_BINARY_BYTES:
        ext = name.rsplit('.', 1)[-1] if '.' in name else ''
        if ext not in _DOC_EXTS and ext not in _TEXT_EXTS:
            return True
    return False


def _suffixed_path(rel_path, index):
    head, _, name = str(rel_path).rpartition('/')
    if '.' in name[1:]:
        stem, _, ext = name.rpartition('.')
        name = '%s~%d.%s' % (stem, index, ext)
    else:
        name = '%s~%d' % (name, index)
    return ('%s/%s' % (head, name)) if head else name


def _unique_rel_path(rel_path, used):
    """解压期同名冲突改名（~1/~2，大小写不敏感），绝不覆盖清单里已有条目。"""
    candidate = rel_path
    index = 0
    while candidate.casefold() in used:
        index += 1
        candidate = _suffixed_path(rel_path, index)
    used.add(candidate.casefold())
    return candidate


def _write_member(archive, info, dest, expected, budget=None):
    digest = hashlib.sha256()
    written = 0
    handle = None
    try:
        handle = os.open(str(dest), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with archive.open(info) as source, os.fdopen(handle, 'wb') as out:
            handle = None
            while True:
                block = source.read(_IO_BLOCK)
                if not block:
                    break
                written += len(block)
                if written > expected:
                    raise ZipInvalid('压缩包条目实际大小与目录不一致：%s' % info.filename)
                # D12：跨条目、跨嵌套层的**实际**解出字节统一计入共享预算，
                # 目录声明值造假也拦得住（超预算立即中止整包展开）。
                if budget is not None:
                    budget['real'] = budget.get('real', 0) + len(block)
                    if budget['real'] > protocol.ZIP_EXPANDED_BYTES:
                        raise LimitExceeded('实际解压总量超过上限 %d 字节'
                                            % protocol.ZIP_EXPANDED_BYTES)
                digest.update(block)
                out.write(block)
    except UploadError:
        _remove_file(dest)
        raise
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError, zlib.error, EOFError,
            OSError) as exc:
        _remove_file(dest)
        raise ZipInvalid('压缩包条目解压失败：%s' % info.filename)
    finally:
        if handle is not None:
            os.close(handle)
    os.chmod(str(dest), 0o600)
    return written, digest.hexdigest()


def _walk_zip(archive_path, prefix, staging, depth, budget, extracted, excluded, used):
    try:
        archive = zipfile.ZipFile(str(archive_path))
    except (zipfile.BadZipFile, OSError) as exc:
        raise ZipInvalid('压缩包无法读取：%s' % exc)
    with archive:
        for info in archive.infolist():
            raw_name = str(info.filename or '')
            if not raw_name or raw_name.endswith('/') or info.is_dir():
                continue
            budget['entries'] += 1
            if budget['entries'] > protocol.ZIP_MAX_ENTRIES:
                raise LimitExceeded('解压条目数超过上限 %d' % protocol.ZIP_MAX_ENTRIES)
            display = '%s/%s' % (prefix, raw_name.replace('\\', '/'))
            _reject_special_entry(info, raw_name)
            inner = _member_rel_path(raw_name)
            size = int(info.file_size or 0)
            if inner is None or _is_excluded(inner, size) or size > protocol.FILE_BYTES:
                excluded.append(display)
                continue
            try:
                combined = protocol.safe_rel_path('%s/%s' % (prefix, inner))
            except protocol.InvalidPath:
                excluded.append(display)
                continue
            if budget['bytes'] + size > protocol.ZIP_EXPANDED_BYTES:
                raise LimitExceeded('解压总量超过上限 %d 字节' % protocol.ZIP_EXPANDED_BYTES)
            if info.flag_bits & 0x1:
                excluded.append(display)
                continue
            budget['bytes'] += size
            staged = Path(staging) / (sto.new_id() + '.part')
            written, digest = _write_member(archive, info, staged, size, budget)
            kind = protocol.detect_kind(inner, head=_head(staged, 8))
            if kind == 'zip':
                if depth >= ZIP_MAX_DEPTH:
                    excluded.append(display)
                    _remove_file(staged)
                    continue
                try:
                    _walk_zip(staged, display, staging, depth + 1, budget, extracted, excluded, used)
                finally:
                    _remove_file(staged)
                continue
            extracted.append({'rel_path': _unique_rel_path(combined, used), 'path': staged,
                              'size': written, 'hash': digest, 'kind': kind})


def _register_extracted(conn, owner_user_id, task_id, extracted, source_group):
    materials = []
    _ensure_dir(blob_dir())
    for item in extracted:
        existing = store.find_blob_by_hash(conn, task_id, owner_user_id, item['hash'])
        if existing:
            blob_id = existing['blob_id']
            _remove_file(item['path'])
        else:
            name = _blob_filename(item['rel_path'])
            dest = blob_dir() / name
            try:
                os.replace(str(item['path']), str(dest))
                os.chmod(str(dest), 0o600)
            except OSError as exc:
                _remove_file(item['path'])
                raise UploadError('解压文件写入存储失败：%s' % exc)
            item['stored'] = dest
            blob_id = store.create_blob(conn, owner_user_id, task_id, item['rel_path'],
                                        item['size'], item['hash'],
                                        '%s/%s' % (BLOB_SUBDIR, name))
        material_id = store.create_material(conn, owner_user_id, task_id, blob_id, item['rel_path'],
                                            item['kind'], item['size'], item['hash'], source_group)
        materials.append(store.material_view(store.get_material(conn, material_id, owner_user_id)))
    return materials


def _rollback_extracted(extracted):
    """本次调用已挪进 blob 目录、但未随事务登记成功的文件（孤儿不留）。"""
    for item in extracted:
        _remove_file(item.get('stored'))


def expand_zip(conn, owner_user_id, task_id, zip_path, rel_path):
    """安全展开 ZIP：逐条校验 → 解压到临时目录 → 逐份登记 blob + material。

    返回 (materials, excluded_paths)。结构性不安全（绝对路径、..、符号链接、不可读）抛
    ZipInvalid 并丢弃全部已解压内容，不登记半份材料。
    """
    owner_id = str(owner_user_id or '')
    base_path = protocol.safe_rel_path(rel_path)
    if store.require_task(conn, task_id, owner_id) is None:
        raise UploadNotFound('生成任务不存在')
    source = Path(str(zip_path))
    if not source.is_file():
        raise UploadError('压缩包文件缺失，请重新上传')
    try:
        source_group = _sha256_file(source)[:16]
    except OSError as exc:
        raise UploadError('压缩包不可读：%s' % exc)
    staging = _ensure_dir(temp_dir() / ('expand-' + sto.new_id()))
    extracted, excluded, used = [], [], set()
    budget = {'bytes': 0, 'entries': 0, 'real': 0}
    try:
        _walk_zip(source, base_path, staging, 1, budget, extracted, excluded, used)
        return _register_extracted(conn, owner_id, task_id, extracted, source_group), excluded
    except BaseException:
        _rollback_extracted(extracted)
        raise
    finally:
        shutil.rmtree(str(staging), ignore_errors=True)
