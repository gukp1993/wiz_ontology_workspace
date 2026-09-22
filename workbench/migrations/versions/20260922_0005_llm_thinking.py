"""llm thinking: 提供方级思考强度设置（04 分册 §4.2，2026-09-22）

Revision ID: 20260922_0005
Revises: 20260921_0004
Create Date: 2026-09-22

wb_model_configs 新增一列：
* thinking  思考强度 'default'|'off'（缺省 default=跟随模型/账号默认，不追加参数；
  off 时对 bigmodel.cn 端点请求体追加 thinking:{"type":"disabled"}，显著降低延迟）

幂等：列已存在则跳过。存量行由列默认值回填 'default'。
"""
from alembic import op
import sqlalchemy as sa

revision = '20260922_0005'
down_revision = '20260921_0004'
branch_labels = None
depends_on = None

_TABLE = 'wb_model_configs'
_COLUMN = 'thinking'
_DDL = "VARCHAR(16) NOT NULL DEFAULT 'default'"


def _has_column(bind, table, column):
    try:
        return column in [c['name'] for c in sa.inspect(bind).get_columns(table)]
    except Exception:
        return False


def upgrade():
    bind = op.get_bind()
    if _has_column(bind, _TABLE, _COLUMN):
        return
    if bind.dialect.name == 'mysql':
        bind.execute(sa.text(
            'ALTER TABLE %s ADD COLUMN %s %s' % (_TABLE, _COLUMN, _DDL)))
    else:
        bind.execute(sa.text(
            'ALTER TABLE %s ADD COLUMN %s %s' % (_TABLE, _COLUMN, _DDL)))


def downgrade():
    bind = op.get_bind()
    if not _has_column(bind, _TABLE, _COLUMN):
        return
    bind.execute(sa.text('ALTER TABLE %s DROP COLUMN %s' % (_TABLE, _COLUMN)))
