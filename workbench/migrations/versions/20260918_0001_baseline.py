"""baseline: workbench storage tables

Revision ID: 20260918_0001
Revises:
Create Date: 2026-09-18

首期基线：资产/快照/head/发布/项目引用、定义与引用索引（可重建投影）、
目录缓存、模型配置、凭据、设置、附件、迁移清单、幂等回执、写护栏。
"""
from alembic import op

revision = '20260918_0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    from workbench.storage.schema import METADATA, ALL_TABLES
    METADATA.create_all(op.get_bind(), tables=ALL_TABLES)


def downgrade():
    from workbench.storage.schema import METADATA, ALL_TABLES
    METADATA.drop_all(op.get_bind(), tables=list(reversed(ALL_TABLES)))
