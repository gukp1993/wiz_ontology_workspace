"""Alembic 迁移环境：URL 由调用方经 config.attributes 注入（不落盘、不含口令打印）。"""
from alembic import context

config = context.config
target_metadata = None  # 基线迁移显式声明表；后续迁移按迁移文件演进


def _url():
    url = config.attributes.get('wiz_database_url')
    if url:
        return url
    from workbench.storage.engine import resolve_url
    return resolve_url()


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
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
