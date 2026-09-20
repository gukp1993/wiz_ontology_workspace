"""ontology build: tasks/materials/facts/runs/candidates/deliveries

Revision ID: 20260920_0003
Revises: 20260918_0002
Create Date: 2026-09-20

从物料自动构建本体（需求 20260920_从物料自动构建本体）：
* 新增 11 张任务族表（wb_build_*）。任务数据独立于 wb_assets 本体资产：
  候选/证据/批次永不写本体快照；交付时才在同一写事务里创建新本体草稿。
* 全部幂等：表已存在则跳过（新库由 schema.METADATA 直接建出目标形态）。
* 不改动任何既有表，无需重建；downgrade 只删除本迁移新增的表。

blob 实体不进库：wb_build_blobs 只登记归属/哈希/相对路径，文件由
workbench/ontology_build/materials.py 管理在 DATA_ROOT/data/ontology-build-blobs/。
"""
from alembic import op
import sqlalchemy as sa

revision = '20260920_0003'
down_revision = '20260918_0002'
branch_labels = None
depends_on = None

_TABLES = ('wb_build_tasks', 'wb_build_blobs', 'wb_build_uploads', 'wb_build_materials',
           'wb_build_facts', 'wb_build_messages', 'wb_build_scopes', 'wb_build_runs',
           'wb_build_batches', 'wb_build_candidates', 'wb_build_review_ops',
           'wb_build_deliveries')


def upgrade():
    bind = op.get_bind()
    from workbench.storage import schema
    tables = [getattr(schema, name) for name in _TABLES]
    schema.METADATA.create_all(bind, tables=tables, checkfirst=True)


def downgrade():
    bind = op.get_bind()
    from workbench.storage import schema
    for name in reversed(_TABLES):
        getattr(schema, name).drop(bind, checkfirst=True)
