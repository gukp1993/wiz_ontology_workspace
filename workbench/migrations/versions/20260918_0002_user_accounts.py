"""user accounts: users/sessions/user settings + owner-scoped assets

Revision ID: 20260918_0002
Revises: 20260918_0001
Create Date: 2026-09-18

账号体系（需求 20260918_登录与账号体系）：
* 新增 wb_users / wb_sessions / wb_user_settings 三张表（幂等：已存在则跳过）。
* wb_assets 唯一约束 (kind, external_id) → (kind, owner_user_id, external_id)；
  wb_model_configs 主键 provider_id → (owner_user_id, provider_id)。
  历史行 owner_user_id 填空串（=未归属），由 transfer assign-owner 归属到指定账号。

实现说明（为什么是「重建表」而不是 ALTER）：
* SQLite 不能删除/替换既有 UNIQUE 约束、不能改主键，只能按官方推荐的
  新建→搬数据→删旧→改名流程重建。重建需要**事务外**关闭外键强制
  （PRAGMA foreign_keys 在事务内为空操作），已统一在 migrations/env.py 处理：
  迁移连接进入前关闭外键、提交后做 foreign_key_check 再恢复。
  （实测：仅靠 PRAGMA defer_foreign_keys 不够——DROP TABLE 的隐式 DELETE
  会把子表行压入延迟检查队列，提交时按旧表状态判定为违规。）
* MySQL 用等价的 SET FOREIGN_KEY_CHECKS=0 包裹同一流程（会话级，事务内有效）。
* 本迁移幂等：列/主键已是目标形态时整体跳过（新库由 0001 直接建出目标形态）。
"""
from alembic import op
import sqlalchemy as sa

revision = '20260918_0002'
down_revision = '20260918_0001'
branch_labels = None
depends_on = None


def _has_column(bind, table, column):
    try:
        return column in [c['name'] for c in sa.inspect(bind).get_columns(table)]
    except Exception:
        return False


def _has_primary_key(bind, table, columns):
    try:
        pk = sa.inspect(bind).get_pk_constraint(table)
    except Exception:
        return False
    return [str(c) for c in (pk.get('constrained_columns') or [])] == [str(c) for c in columns]


def _rebuild_table(bind, table, filler_column, dialect):
    """按目标形态重建表：复制全部旧列，filler_column（新增归属列）填空串。

    table 为目标形态（来自 schema.METADATA）；旧表结构与目标只在新增列/约束上不同。
    外键检查在重建窗口内推迟（提交时校验），因此子表引用不受中间态影响。
    """
    tmp_name = table.name + '_mig_new'
    tmp = table.to_metadata(sa.MetaData(), name=tmp_name)
    # 索引/约束改到临时表名下重建：SQLite 的索引名是库级作用域，避免与旧表对象重名
    for index in list(tmp.indexes):
        index.name = tmp_name + '_' + index.name
    cols = [c.name for c in table.columns if c.name != filler_column]
    col_list = ', '.join(cols)
    if dialect == 'mysql':
        bind.exec_driver_sql('SET FOREIGN_KEY_CHECKS=0')
    try:
        tmp.create(bind, checkfirst=False)
        bind.exec_driver_sql(
            f'INSERT INTO {tmp_name} ({col_list}, {filler_column}) '
            f"SELECT {col_list}, '' FROM {table.name}")
        bind.exec_driver_sql(f'DROP TABLE {table.name}')
        if dialect == 'mysql':
            bind.exec_driver_sql(f'RENAME TABLE {tmp_name} TO {table.name}')
        else:
            bind.exec_driver_sql(f'ALTER TABLE {tmp_name} RENAME TO {table.name}')
    finally:
        if dialect == 'mysql':
            bind.exec_driver_sql('SET FOREIGN_KEY_CHECKS=1')


def upgrade():
    bind = op.get_bind()
    from workbench.storage.schema import METADATA, wb_assets, wb_model_configs, \
        wb_users, wb_sessions, wb_user_settings
    dialect = bind.dialect.name

    # ① 三张新表（幂等：已存在则跳过）
    METADATA.create_all(bind, tables=[wb_users, wb_sessions, wb_user_settings], checkfirst=True)

    # ② 资产按账号唯一
    if not _has_column(bind, 'wb_assets', 'owner_user_id'):
        _rebuild_table(bind, wb_assets, 'owner_user_id', dialect)
    else:
        # 列已在（新库由 0001 建出）：只补索引，避免重复执行报错
        insp = sa.inspect(bind)
        names = {ix['name'] for ix in insp.get_indexes('wb_assets')}
        if 'ix_assets_owner_kind' not in names:
            op.create_index('ix_assets_owner_kind', 'wb_assets', ['owner_user_id', 'kind'])

    # ③ 模型配置按账号唯一
    if not _has_primary_key(bind, 'wb_model_configs', ['owner_user_id', 'provider_id']):
        _rebuild_table(bind, wb_model_configs, 'owner_user_id', dialect)


def downgrade():
    """回退仅删除账号三表；资产/模型配置的归属列保留（列宽空串等价于未归属，无破坏性）。"""
    bind = op.get_bind()
    from workbench.storage.schema import wb_sessions, wb_user_settings, wb_users
    for table in (wb_user_settings, wb_sessions, wb_users):
        table.drop(bind, checkfirst=True)
