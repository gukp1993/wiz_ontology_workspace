"""数据库引擎与事务边界：URL 解析、方言参数、Unit of Work、CAS、异常。

单一后端选择（composition root 在 __init__）：
* WIZ_DATABASE_URL 显式指定（MySQL 形如 mysql+pymysql://…；含口令的 URL 绝不打印/回传）。
* 未显式指定时从 WIZ_WORKBENCH_ROOT/DATA_ROOT 派生 SQLite 路径 <root>/data/workbench.sqlite3；
  测试根（WIZ_WORKBENCH_ROOT 已设置）缺失库文件时允许显式初始化；真实根缺失库文件
  必须先运行 python3 -m workbench.storage.transfer init —— 服务绝不静默建库、
  绝不回退旧文件（需求说明 §7/§10）。

事务配方（已在 Python 3.9 + SQLAlchemy 2.0 实测）：
* SQLite 连接 isolation_level=None（关闭驱动隐式事务），Engine "begin" 事件里
  发 BEGIN IMMEDIATE —— 写事务一开始就取写锁，配合 CAS 行数断言；
  读路径不调用 conn.begin()，按自动提交逐语句读取（快照不可变，读取即一致）。
* PRAGMA：foreign_keys=ON、busy_timeout=5000、synchronous=FULL 每连接启用；
  journal_mode=WAL 仅在初始化时设置一次（持久属性）。
* 库不可用/锁超时抛 StorageUnavailable（HTTP 503），绝不降级读文件。
"""
import os
import time
import uuid
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy import event, text

from workbench.paths import DATA_ROOT
from workbench.storage.schema import GUARD_KEYS

DB_FILENAME = 'workbench.sqlite3'
BUSY_TIMEOUT_MS = 5000
WRITE_RETRIES = 3            # SQLITE_BUSY/死锁仅对整个未提交事务有限次重试
RETRY_BACKOFF = (0.1, 0.25, 0.5)


class StorageError(Exception):
    """存储层基础异常（业务错误不使用本类型）。"""


class StorageUnavailable(StorageError):
    """库不可用/锁超时/schema 缺失 —— 可重试的基础设施错误，绝不回退文件。"""


class RevisionConflict(StorageError):
    """CAS 失败：head 已被并发提交推进。携带服务端最新 token 供 409。"""

    def __init__(self, current_revision=None, message=''):
        self.current_revision = current_revision or ''
        super().__init__(message or '此内容已有新版本，请刷新后重试')


class NotFound(StorageError):
    """资产/快照/发布不存在。"""


def derived_sqlite_url():
    root = Path(str(DATA_ROOT))
    return 'sqlite:///' + str(root / 'data' / DB_FILENAME)


def resolve_url():
    explicit = os.environ.get('WIZ_DATABASE_URL', '').strip()
    if explicit:
        return explicit
    return derived_sqlite_url()


def database_file(url):
    if url.startswith('sqlite:///'):
        return Path(url[len('sqlite:///'):])
    return None


def backend_kind(url):
    return 'mysql' if url.startswith('mysql') else 'sqlite'


_engine = None
_engine_url = None


def engine(url=None):
    """进程级单例引擎；不同 URL（并行测试）各建一个。"""
    global _engine, _engine_url
    url = url or resolve_url()
    if _engine is not None and _engine_url == url:
        return _engine
    kind = backend_kind(url)
    try:
        if kind == 'sqlite':
            db_file = database_file(url)
            if db_file is not None:
                db_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            eng = sa.create_engine(url, connect_args={'check_same_thread': False}, future=True)
        else:
            eng = sa.create_engine(url, pool_pre_ping=True, pool_recycle=3600,
                                   pool_size=5, max_overflow=5, future=True)
    except StorageUnavailable:
        raise
    except Exception as exc:
        raise StorageUnavailable('数据库不可用：' + _safe_reason(exc)) from exc

    @event.listens_for(eng, 'connect')
    def _on_connect(dbapi_connection, connection_record):
        if kind == 'sqlite':
            dbapi_connection.isolation_level = None  # 关闭隐式事务；BEGIN 由下方事件显式控制
            cursor = dbapi_connection.cursor()
            cursor.execute('PRAGMA foreign_keys=ON')
            cursor.execute(f'PRAGMA busy_timeout={BUSY_TIMEOUT_MS}')
            cursor.execute('PRAGMA synchronous=FULL')
            cursor.close()

    if kind == 'sqlite':
        @event.listens_for(eng, 'begin')
        def _on_begin(conn):
            # 驱动 isolation_level=None：显式发出 BEGIN。写事务（WriteTx 标记）用
            # IMMEDIATE 一开始就取写锁；读路径 autobegin 用 DEFERRED（WAL 下读写互不阻塞）。
            raw = _raw_dbapi(conn)
            if raw is not None and raw.in_transaction:
                return  # WriteTx 已手工 BEGIN IMMEDIATE，跳过
            conn.exec_driver_sql('BEGIN IMMEDIATE' if getattr(conn, '_wiz_write', False) else 'BEGIN')

    _engine, _engine_url = eng, url
    return eng


def _raw_dbapi(conn):
    try:
        return conn.connection.dbapi_connection
    except AttributeError:
        return conn.connection


def reset_engine():
    """测试隔离用：换 WIZ_DATABASE_URL / 临时根后丢弃旧引擎池。"""
    global _engine, _engine_url
    if _engine is not None:
        _engine.dispose()
    _engine, _engine_url = None, None


def initialize(url=None):
    """显式初始化：建表（含 alembic_version 基线）+ 预建 guard 行 + SQLite WAL。

    只能由 transfer CLI 或测试数据根引导调用；HTTP 请求路径绝不调用。
    """
    from workbench.storage import migrations as schema_migrations
    url = url or resolve_url()
    eng = engine(url)
    if backend_kind(url) == 'sqlite':
        raw = eng.raw_connection()
        try:
            cursor = raw.cursor()
            cursor.execute('PRAGMA journal_mode=WAL')
            cursor.close()
            raw.commit()
        finally:
            raw.close()
    schema_migrations.upgrade(url)
    with eng.begin() as conn:
        for key in GUARD_KEYS:
            if _guard_missing(conn, key):
                conn.execute(text('INSERT INTO wb_write_guards (guard_key, generation) VALUES (:k, 0)'),
                             {'k': key})
    return url


def _guard_missing(conn, key):
    row = conn.execute(text('SELECT guard_key FROM wb_write_guards WHERE guard_key = :k'), {'k': key}).first()
    return row is None


def schema_status(url=None):
    """(ready, schema_version)；未初始化返回 (False, '')。绝不含连接口令。"""
    url = url or resolve_url()
    eng = engine(url)
    try:
        has = sa.inspect(eng).has_table('alembic_version')
    except Exception:
        return False, ''
    if not has:
        return False, ''
    with eng.connect() as conn:
        row = conn.execute(text('SELECT version_num FROM alembic_version')).first()
    return True, (str(row[0]) if row else '')


def _is_busy(exc):
    text_ = str(exc).lower()
    return 'database is locked' in text_ or 'database table is locked' in text_ or 'deadlock' in text_


class WriteTx:
    """一次业务写事务：BEGIN IMMEDIATE 起步，提交/回滚/有限次忙重试。"""

    def __init__(self, url=None):
        self._url = url or resolve_url()
        self.conn = None

    def __enter__(self):
        return self

    def connection(self):
        if self.conn is None:
            self.conn = engine(self._url).connect()
        return self.conn

    def run(self, body, attempts=WRITE_RETRIES):
        """body(conn) 在写事务内执行；抛 RevisionConflict 时不重试（业务裁决）。
        SQLITE_BUSY/deadlock 重试整个事务；其余异常原样抛出。"""
        last_exc = None
        for attempt in range(attempts):
            conn = self.connection()
            conn._wiz_write = True  # begin 事件据此发 BEGIN IMMEDIATE
            trans = conn.begin()
            try:
                result = body(conn)
                trans.commit()
                return result
            except RevisionConflict:
                trans.rollback()
                raise
            except sa.exc.OperationalError as exc:
                trans.rollback()
                if _is_busy(exc) and attempt < attempts - 1:
                    last_exc = exc
                    time.sleep(RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF) - 1)])
                    continue
                raise StorageUnavailable('数据库写入超时或繁忙，请稍后重试：' + _safe_reason(exc)) from exc
            except Exception:
                trans.rollback()
                raise
        raise StorageUnavailable('数据库持续繁忙，请稍后重试') from last_exc

    def close(self):
        if self.conn is not None:
            self.conn.close()
            self.conn = None

    def __exit__(self, *args):
        self.close()
        return False


def write_tx(url=None):
    return WriteTx(url)


def read_connection(url=None):
    return engine(url or resolve_url()).connect()


def cas_head(conn, asset_uid, expected_token, expected_generation,
             new_snapshot_id, new_token, new_seq, now):
    """条件更新推进 head；rowcount≠1 抛 RevisionConflict（A→B→A 后旧 token 必拒）。"""
    result = conn.execute(
        text('UPDATE wb_asset_heads SET snapshot_id = :s, revision_token = :t, '
             'generation = generation + 1, snapshot_seq = :seq, updated_at = :now '
             'WHERE asset_uid = :a AND revision_token = :et AND generation = :eg'),
        {'s': new_snapshot_id, 't': new_token, 'seq': new_seq, 'now': now,
         'a': asset_uid, 'et': expected_token, 'eg': expected_generation})
    return result.rowcount == 1


def read_head(conn, asset_uid):
    row = conn.execute(text('SELECT asset_uid, snapshot_id, revision_token, generation, '
                            'snapshot_seq, release_seq, updated_at FROM wb_asset_heads '
                            'WHERE asset_uid = :a'), {'a': asset_uid}).mappings().first()
    return dict(row) if row else None


def head_by_external(conn, kind, external_id, owner_user_id=None):
    """按 (kind, external_id) 取 head；owner_user_id 非 None 时附加归属过滤
    （跨账号按不存在处理；CLI/迁移路径传 None 不限定归属）。"""
    params = {'k': kind, 'e': external_id}
    sql = ('SELECT h.asset_uid, h.snapshot_id, h.revision_token, h.generation, '
           'h.snapshot_seq, h.release_seq, h.updated_at FROM wb_asset_heads h '
           'JOIN wb_assets a ON a.asset_uid = h.asset_uid '
           "WHERE a.kind = :k AND a.external_id = :e AND a.deleted_at IS NULL")
    if owner_user_id is not None:
        sql += ' AND a.owner_user_id = :o'
        params['o'] = owner_user_id or ''
    row = conn.execute(text(sql), params).mappings().first()
    return dict(row) if row else None


def read_snapshot(conn, snapshot_id):
    row = conn.execute(text('SELECT snapshot_id, asset_uid, seq, purpose, payload_format, '
                            'payload_json, content_hash, legacy_revision, created_at '
                            'FROM wb_snapshots WHERE snapshot_id = :s'), {'s': snapshot_id}).mappings().first()
    return dict(row) if row else None


def new_id():
    return str(uuid.uuid4())


def utcnow():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def content_hash(payload_bytes):
    import hashlib
    return hashlib.sha256(payload_bytes).hexdigest()


def stable_hash(value):
    if isinstance(value, str):
        raw = value.encode('utf-8')
    else:
        raw = str(value).encode('utf-8')
    import hashlib
    return hashlib.sha256(raw).hexdigest()


def json_dumps(value):
    import json
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _safe_reason(exc):
    text_ = str(exc)
    return (text_[:160] + '…') if len(text_) > 160 else text_
