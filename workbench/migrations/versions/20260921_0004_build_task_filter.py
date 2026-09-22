"""build task filter: 格式黑名单三层的任务级设置与过滤报告（V2-4 / G20）

Revision ID: 20260921_0004
Revises: 20260920_0003
Create Date: 2026-09-21

wb_build_tasks 新增两列（08 分册 §13 冻结）：
* filter_json         任务级过滤设置 {'allowExts':[...], 'softExts':[...]|None, 'excludeExts':[...]}
* filter_report_json  被过滤文件报告 {'items':[...≤500], 'counts':{...}, 'truncated':bool}

判定优先级（需求 §5.4）：硬黑名单 > 用户白名单 > 默认软黑名单 > 任务级追加；
硬黑名单是安全边界，不受任何任务设置影响。幂等：列已存在则跳过。
"""
from alembic import op
import sqlalchemy as sa

revision = '20260921_0004'
down_revision = '20260920_0003'
branch_labels = None
depends_on = None

_COLUMNS = (
    ('filter_json', "TEXT NOT NULL DEFAULT '{}'"),
    ('filter_report_json', "TEXT NOT NULL DEFAULT '{}'"),
)


def _has_column(bind, table, column):
    try:
        return column in [c['name'] for c in sa.inspect(bind).get_columns(table)]
    except Exception:
        return False


def upgrade():
    bind = op.get_bind()
    for name, ddl in _COLUMNS:
        if _has_column(bind, 'wb_build_tasks', name):
            continue
        if bind.dialect.name == 'mysql':
            bind.exec_driver_sql(f'ALTER TABLE wb_build_tasks ADD COLUMN {name} {ddl.replace("TEXT", "LONGTEXT")}')
        else:
            bind.exec_driver_sql(f"ALTER TABLE wb_build_tasks ADD COLUMN {name} {ddl}")


def downgrade():
    bind = op.get_bind()
    for name, _ddl in _COLUMNS:
        if _has_column(bind, 'wb_build_tasks', name):
            bind.exec_driver_sql(f'ALTER TABLE wb_build_tasks DROP COLUMN {name}')
