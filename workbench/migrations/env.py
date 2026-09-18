"""Alembic 迁移环境：URL 由调用方经 config.attributes 注入（不落盘、不含口令打印）。

SQLite 表重建（改唯一约束/主键）需要在**事务之外**关闭外键强制——这是 SQLite 官方
推荐的 12 步表重建流程；PRAGMA foreign_keys 在事务内是空操作，而 Alembic 的迁移
跑在事务里，所以在本文件用原始 DBAPI 连接（不触发 SQLAlchemy autobegin）切换。
迁移结束后重新打开外键并做一次 foreign_key_check，把完整性核对留在同一连接上。
"""
from alembic import context

config = context.config
target_metadata = None  # 基线迁移显式声明表；后续迁移按迁移文件演进


def _url():
    url = config.attributes.get('wiz_database_url')
    if url:
        return url
    from workbench.storage.engine import resolve_url
    return resolve_url()


def _raw_dbapi(connection):
    try:
        return connection.connection.dbapi_connection
    except AttributeError:
        return connection.connection


def _sqlite_fk(connection, enabled):
    """在原始 DBAPI 连接上切换 SQLite 外键强制（不进入 SQLAlchemy 事务）。"""
    raw = _raw_dbapi(connection)
    if raw is None or connection.dialect.name != 'sqlite':
        return
    cursor = raw.cursor()
    cursor.execute('PRAGMA foreign_keys=' + ('ON' if enabled else 'OFF'))
    cursor.close()


def _sqlite_fk_check(connection):
    raw = _raw_dbapi(connection)
    if raw is None or connection.dialect.name != 'sqlite':
        return []
    cursor = raw.cursor()
    cursor.execute('PRAGMA foreign_key_check')
    rows = cursor.fetchall()
    cursor.close()
    return rows


def run_migrations_offline():
    context.configure(url=_url(), literal_binds=True, dialect_opts={'paramstyle': 'named'})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    engine = config.attributes.get('wiz_engine')
    if engine is None:
        from workbench.storage.engine import engine as make_engine
        engine = make_engine(_url())
    with engine.connect() as connection:
        _sqlite_fk(connection, False)  # 表重建期间不强制外键；提交后立即核对并恢复
        context.configure(connection=connection, target_metadata=target_metadata)
        try:
            with context.begin_transaction():
                context.run_migrations()
        finally:
            violations = _sqlite_fk_check(connection)
            _sqlite_fk(connection, True)
            if violations:
                raise RuntimeError('迁移后外键完整性核对失败（前 3 条）：' + repr(violations[:3]))


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
