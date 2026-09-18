"""Alembic schema 迁移（程序化调用，不依赖仓库根 alembic.ini）。

脚本目录固定 workbench/migrations/；目标 URL 经 env.py 的 config.attributes 注入，
不写进任何配置文件（含口令的 URL 不落盘）。upgrade() 幂等：已是最新版本时无操作。
"""
from pathlib import Path

from alembic import command
from alembic.config import Config

from workbench.storage.engine import engine, resolve_url

SCRIPT_LOCATION = Path(__file__).resolve().parents[1] / 'migrations'


def _config(url):
    cfg = Config()
    cfg.set_main_option('script_location', str(SCRIPT_LOCATION))
    cfg.attributes['wiz_database_url'] = url
    cfg.attributes['wiz_engine'] = engine(url)
    return cfg


def upgrade(url=None):
    url = url or resolve_url()
    command.upgrade(_config(url), 'heads')
    return url
