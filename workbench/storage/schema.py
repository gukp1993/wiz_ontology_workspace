"""Portable table definitions (SQLAlchemy Core), shared by SQLite and MySQL.

Design contract (需求说明 §4, 冻结于开发计划):
* 2026-09-18 账号体系：wb_assets / wb_model_configs 增加 owner_user_id（空串=未归属，
  历史数据由 transfer assign-owner 归属 admin）；唯一约束升级为按账号
  （uq_assets_kind_owner_external、wb_model_configs(owner_user_id, provider_id)）。
  用户级设置走 wb_user_settings（复合主键），旧 wb_settings 保持工作台全局语义。
* 完整快照为事实：wb_snapshots.payload_json 保存整份文档；定义/引用索引只是
  可重建的查询投影，删除重建不得触碰快照与发布记录。
* 内部主键为应用生成的 UUID 字符串；外部 ID（storage、UUID、短 id）原样保留。
* MySQL 类型差异全部用 with_variant 收口在本文件：LONGTEXT/LONGBLOB、
  name_key/ID 的二进制排序规则；业务代码不得出现方言分支。
"""
import sqlalchemy as sa
from sqlalchemy.dialects import mysql as ms

# 用于相等比较的键（ID/name_key/hash）在 MySQL 上必须用二进制排序规则，
# 不能依赖默认大小写/重音等价规则（需求说明 §4.3）。
BinV = lambda n: sa.String(n).with_variant(ms.VARCHAR(n, collation='utf8mb4_bin'), 'mysql')
LongText = lambda: sa.Text().with_variant(ms.LONGTEXT(), 'mysql')
LongBlob = lambda: sa.LargeBinary().with_variant(ms.LONGBLOB(), 'mysql')

METADATA = sa.MetaData()
SCHEMA_VERSION_BASELINE = '20260918_0001'

wb_assets = sa.Table('wb_assets', METADATA,
    sa.Column('asset_uid', BinV(36), primary_key=True),
    sa.Column('owner_user_id', BinV(36), nullable=False, default=''),  # 账号归属；''=未归属（仅迁移期）
    sa.Column('kind', sa.String(16), nullable=False),          # model / project / flow
    sa.Column('external_id', BinV(64), nullable=False),        # storage | UUID | 短 id
    sa.Column('name', sa.String(160), nullable=False, default=''),
    sa.Column('name_key', sa.String(160), nullable=False, default=''),  # strip().casefold()
    sa.Column('summary_json', LongText(), nullable=False, default='{}'),
    sa.Column('created_at', sa.String(32), nullable=False, default=''),
    sa.Column('updated_at', sa.String(32), nullable=False, default=''),
    sa.Column('deleted_at', sa.String(32), nullable=True),
    sa.UniqueConstraint('kind', 'owner_user_id', 'external_id', name='uq_assets_kind_owner_external'),
    sa.Index('ix_assets_owner_kind', 'owner_user_id', 'kind'),
    )

wb_snapshots = sa.Table('wb_snapshots', METADATA,
    sa.Column('snapshot_id', BinV(36), primary_key=True),
    sa.Column('asset_uid', BinV(36), sa.ForeignKey('wb_assets.asset_uid'), nullable=False),
    sa.Column('seq', sa.Integer(), nullable=False),
    sa.Column('purpose', sa.String(20), nullable=False),       # draft / release / imported-base
    sa.Column('payload_format', sa.String(40), nullable=False),
    sa.Column('payload_json', LongText(), nullable=False),
    sa.Column('content_hash', BinV(64), nullable=False, default=''),
    sa.Column('legacy_revision', sa.String(80), nullable=False, default=''),
    sa.Column('created_at', sa.String(32), nullable=False, default=''),
    sa.UniqueConstraint('asset_uid', 'seq', name='uq_snapshots_asset_seq'),
    sa.UniqueConstraint('asset_uid', 'snapshot_id', name='uq_snapshots_asset_snapshot'),
    sa.Index('ix_snapshots_asset_purpose', 'asset_uid', 'purpose'),
    )

wb_asset_heads = sa.Table('wb_asset_heads', METADATA,
    sa.Column('asset_uid', BinV(36), sa.ForeignKey('wb_assets.asset_uid'), primary_key=True),
    # 复合外键保证 head 指向的快照属于同一资产（不能让项目指向另一资产的草稿）
    sa.Column('snapshot_id', BinV(36), nullable=True),
    sa.Column('revision_token', BinV(80), nullable=False),
    sa.Column('generation', sa.Integer(), nullable=False, default=0),
    sa.Column('snapshot_seq', sa.Integer(), nullable=False, default=0),
    sa.Column('release_seq', sa.Integer(), nullable=False, default=0),
    sa.Column('updated_at', sa.String(32), nullable=False, default=''),
    sa.ForeignKeyConstraint(['asset_uid', 'snapshot_id'],
                            ['wb_snapshots.asset_uid', 'wb_snapshots.snapshot_id'],
                            name='fk_heads_same_asset'),
    sa.UniqueConstraint('revision_token', name='uq_heads_token'),
    )

wb_releases = sa.Table('wb_releases', METADATA,
    sa.Column('release_id', BinV(36), primary_key=True),
    sa.Column('asset_uid', BinV(36), sa.ForeignKey('wb_assets.asset_uid'), nullable=False),
    sa.Column('version_label', BinV(40), nullable=False),
    sa.Column('release_order', sa.Integer(), nullable=False),
    sa.Column('snapshot_id', BinV(36), nullable=False),
    sa.Column('source_draft_id', BinV(36), nullable=True),
    sa.Column('manifest_json', LongText(), nullable=False, default='{}'),
    sa.Column('created_at', sa.String(32), nullable=False, default=''),
    sa.ForeignKeyConstraint(['asset_uid', 'snapshot_id'],
                            ['wb_snapshots.asset_uid', 'wb_snapshots.snapshot_id'],
                            name='fk_releases_same_asset'),
    sa.UniqueConstraint('asset_uid', 'version_label', name='uq_releases_asset_version'),
    sa.UniqueConstraint('asset_uid', 'release_order', name='uq_releases_asset_order'),
    )

wb_project_refs = sa.Table('wb_project_refs', METADATA,
    sa.Column('project_snapshot_id', BinV(36),
              sa.ForeignKey('wb_snapshots.snapshot_id'), primary_key=True),  # 每个项目快照至多一条
    sa.Column('target_ontology_id', BinV(64), nullable=False, default=''),
    sa.Column('target_version', BinV(40), nullable=False, default=''),
    sa.Column('target_release_id', BinV(36), nullable=True),
    sa.Column('resolution', sa.String(16), nullable=False, default='ok'),  # ok / missing
    )

wb_definition_index = sa.Table('wb_definition_index', METADATA,
    sa.Column('row_id', BinV(36), primary_key=True),
    sa.Column('asset_uid', BinV(36), sa.ForeignKey('wb_assets.asset_uid'), nullable=False),
    sa.Column('snapshot_id', BinV(36), sa.ForeignKey('wb_snapshots.snapshot_id'), nullable=False),
    sa.Column('definition_kind', sa.String(40), nullable=False, default=''),   # 空串约定，避免 NULL 唯一语义差异
    sa.Column('stable_id', LongText(), nullable=False),
    sa.Column('stable_id_hash', BinV(64), nullable=False),
    sa.Column('owner_id', LongText(), nullable=False, default=''),
    sa.Column('owner_hash', BinV(64), nullable=False, default=''),
    sa.Column('name', sa.String(255), nullable=False, default=''),
    sa.Column('name_key', sa.String(255), nullable=False, default=''),
    sa.Column('ordinal', sa.Integer(), nullable=False, default=0),
    sa.Column('document_locator', sa.String(255), nullable=False, default=''),
    sa.UniqueConstraint('snapshot_id', 'definition_kind', 'stable_id_hash', 'owner_hash',
                        name='uq_defindex_snapshot'),
    sa.Index('ix_defindex_asset_snapshot', 'asset_uid', 'snapshot_id'),
    sa.Index('ix_defindex_name', 'snapshot_id', 'name_key'),
    )

wb_reference_index = sa.Table('wb_reference_index', METADATA,
    sa.Column('row_id', BinV(36), primary_key=True),
    sa.Column('snapshot_id', BinV(36), sa.ForeignKey('wb_snapshots.snapshot_id'), nullable=False),
    sa.Column('source_kind', sa.String(40), nullable=False, default=''),
    sa.Column('source_id', LongText(), nullable=False),
    sa.Column('target_kind', sa.String(40), nullable=False, default=''),
    sa.Column('target_id', LongText(), nullable=False),
    sa.Column('target_id_hash', BinV(64), nullable=False),
    sa.Column('relation_kind', sa.String(40), nullable=False, default=''),
    sa.Column('document_locator', sa.String(255), nullable=False, default=''),
    sa.Index('ix_refindex_snapshot', 'snapshot_id'),
    sa.Index('ix_refindex_target', 'snapshot_id', 'target_id_hash'),
    )

wb_catalog_cache = sa.Table('wb_catalog_cache', METADATA,
    sa.Column('project_uid', BinV(36), sa.ForeignKey('wb_assets.asset_uid'), primary_key=True),
    sa.Column('connection_id', BinV(80), primary_key=True),
    sa.Column('config_fingerprint', BinV(64), nullable=False, default=''),
    sa.Column('generation', sa.Integer(), nullable=False, default=0),
    sa.Column('payload_json', LongText(), nullable=False, default='{}'),
    sa.Column('refreshed_at', sa.String(32), nullable=False, default=''),
    )

wb_model_configs = sa.Table('wb_model_configs', METADATA,
    sa.Column('owner_user_id', BinV(36), primary_key=True, default=''),  # 账号归属；''=未归属（仅迁移期）
    sa.Column('provider_id', BinV(64), primary_key=True),      # 保持原值（llm-…）
    sa.Column('name', sa.String(160), nullable=False),
    sa.Column('endpoint', sa.String(500), nullable=False, default=''),
    sa.Column('model', sa.String(500), nullable=False, default=''),
    sa.Column('timeout_seconds', sa.Integer(), nullable=False, default=60),
    sa.Column('temperature', sa.Float(), nullable=False, default=0),
    sa.Column('secret_id', BinV(36), nullable=True),
    sa.Column('metadata_revision', sa.Integer(), nullable=False, default=0),
    sa.Column('updated_at', sa.String(32), nullable=False, default=''),
    )

wb_credentials = sa.Table('wb_credentials', METADATA,
    sa.Column('secret_id', BinV(36), primary_key=True),
    sa.Column('namespace', sa.String(20), nullable=False),     # connection / api / model
    sa.Column('owner_key', BinV(80), nullable=False),          # 项目资产 uid 或固定 'global'
    sa.Column('resource_id', BinV(80), nullable=False),
    sa.Column('display_name', sa.String(160), nullable=False, default=''),
    sa.Column('key_id', BinV(32), nullable=False, default=''),
    sa.Column('nonce', sa.LargeBinary(16), nullable=False),
    sa.Column('ciphertext', sa.LargeBinary(1024), nullable=False),
    sa.Column('secret_revision', sa.Integer(), nullable=False, default=0),
    sa.Column('updated_at', sa.String(32), nullable=False, default=''),
    sa.UniqueConstraint('namespace', 'owner_key', 'resource_id', name='uq_credentials_scope'),
    sa.Index('ix_credentials_owner', 'namespace', 'owner_key'),
    )

wb_settings = sa.Table('wb_settings', METADATA,
    sa.Column('setting_key', BinV(80), primary_key=True),
    sa.Column('value_json', LongText(), nullable=False, default='{}'),
    sa.Column('revision', sa.Integer(), nullable=False, default=0),
    sa.Column('updated_at', sa.String(32), nullable=False, default=''),
    )

wb_artifacts = sa.Table('wb_artifacts', METADATA,
    sa.Column('artifact_id', BinV(36), primary_key=True),
    sa.Column('owner_asset_uid', BinV(36), sa.ForeignKey('wb_assets.asset_uid'), nullable=True),
    sa.Column('snapshot_id', BinV(36), nullable=True),
    sa.Column('release_id', BinV(36), nullable=True),
    sa.Column('purpose', sa.String(40), nullable=False),       # release-zip / restore-backup / source-reference / export
    sa.Column('legacy_name', sa.String(255), nullable=False, default=''),
    sa.Column('media_type', sa.String(120), nullable=False, default=''),
    sa.Column('content_hash', BinV(64), nullable=False, default=''),
    sa.Column('bytes_data', LongBlob(), nullable=False),
    sa.Column('created_at', sa.String(32), nullable=False, default=''),
    sa.Index('ix_artifacts_owner_purpose', 'owner_asset_uid', 'purpose'),
    )

wb_import_items = sa.Table('wb_import_items', METADATA,
    sa.Column('item_id', BinV(36), primary_key=True),
    sa.Column('batch_id', BinV(80), nullable=False),
    sa.Column('source_key', LongText(), nullable=False),
    sa.Column('source_key_hash', BinV(64), nullable=False),    # 唯一键用定长 hash，命中后核对完整 source_key
    sa.Column('source_hash', BinV(64), nullable=False),
    sa.Column('entity_kind', sa.String(40), nullable=False, default=''),
    sa.Column('target_id', BinV(80), nullable=False, default=''),
    sa.Column('status', sa.String(20), nullable=False, default='imported'),
    sa.Column('summary_json', LongText(), nullable=False, default='{}'),
    sa.Column('updated_at', sa.String(32), nullable=False, default=''),
    sa.UniqueConstraint('batch_id', 'source_key_hash', name='uq_import_batch_key'),
    sa.Index('ix_import_batch', 'batch_id'),
    )

wb_requests = sa.Table('wb_requests', METADATA,
    sa.Column('request_id', BinV(36), primary_key=True),
    sa.Column('operation', sa.String(60), nullable=False),
    sa.Column('owner_key', BinV(80), nullable=False),
    sa.Column('request_key', BinV(120), nullable=False),
    sa.Column('request_hash', BinV(64), nullable=False, default=''),
    sa.Column('response_json', LongText(), nullable=False, default='{}'),
    sa.Column('created_at', sa.String(32), nullable=False, default=''),
    sa.UniqueConstraint('operation', 'owner_key', 'request_key', name='uq_requests_idempotency'),
    )

wb_write_guards = sa.Table('wb_write_guards', METADATA,
    sa.Column('guard_key', BinV(80), primary_key=True),
    sa.Column('generation', sa.Integer(), nullable=False, default=0),
    )

# --- 账号体系（2026-09-18）--------------------------------------------------------
wb_users = sa.Table('wb_users', METADATA,
    sa.Column('user_id', BinV(36), primary_key=True),
    sa.Column('username', BinV(32), nullable=False),
    sa.Column('username_key', BinV(32), nullable=False),       # strip().casefold()，唯一性以此为准
    sa.Column('password_hash', sa.String(255), nullable=False),  # scrypt$n$r$p$salt$hash；明文绝不入库
    sa.Column('is_admin', sa.Integer(), nullable=False, default=0),
    sa.Column('created_at', sa.String(32), nullable=False, default=''),
    sa.Column('updated_at', sa.String(32), nullable=False, default=''),
    sa.UniqueConstraint('username_key', name='uq_users_username_key'),
    )

wb_sessions = sa.Table('wb_sessions', METADATA,
    sa.Column('session_id', BinV(36), primary_key=True),
    sa.Column('user_id', BinV(36), sa.ForeignKey('wb_users.user_id'), nullable=False),
    sa.Column('token_hash', BinV(64), nullable=False),         # 只存摘要；Cookie 令牌不入库
    sa.Column('created_at', sa.String(32), nullable=False, default=''),
    sa.Column('last_seen_at', sa.String(32), nullable=False, default=''),
    sa.Column('expires_at', sa.String(32), nullable=False, default=''),
    sa.UniqueConstraint('token_hash', name='uq_sessions_token_hash'),
    sa.Index('ix_sessions_user', 'user_id'),
    )

wb_user_settings = sa.Table('wb_user_settings', METADATA,
    sa.Column('user_id', BinV(36), sa.ForeignKey('wb_users.user_id'), primary_key=True),
    sa.Column('setting_key', BinV(80), primary_key=True),
    sa.Column('value_json', LongText(), nullable=False, default='{}'),
    sa.Column('revision', sa.Integer(), nullable=False, default=0),
    sa.Column('updated_at', sa.String(32), nullable=False, default=''),
    )

# 预建 guard 行（迁移/初始化时插入，INSERT OR IGNORE 语义由调用方处理）
GUARD_KEYS = ('asset-name:model', 'asset-name:project', 'asset-name:flow', 'model-default')

ALL_TABLES = (wb_assets, wb_snapshots, wb_asset_heads, wb_releases, wb_project_refs,
              wb_definition_index, wb_reference_index, wb_catalog_cache, wb_model_configs,
              wb_credentials, wb_settings, wb_artifacts, wb_import_items, wb_requests,
              wb_write_guards, wb_users, wb_sessions, wb_user_settings)
