"""存储层 composition root。

业务模块只 import workbench.storage 并调用 ensure_ready() + 各子模块；
URL/后端选择、引擎参数、事务配方全部收口在 engine.py。

初始化策略（冻结）：
* 显式 CLI：python3 -m workbench.storage.transfer init —— 真实根唯一合法的建库方式。
* 隔离数据根（WIZ_WORKBENCH_ROOT 已设置，仅自动化测试/并行实例使用）允许惰性
  自动初始化空库——与旧文件版"目录按需创建"语义对齐，且物理上隔离真实数据。
* HTTP 请求路径绝不执行 DDL、绝不自动导入旧文件、库不可用绝不回退读文件。
"""
import threading

from workbench.storage import engine, schema  # noqa: F401  (re-export)
from workbench.storage.engine import (  # noqa: F401
    RevisionConflict, StorageError, StorageUnavailable, NotFound,
    derived_sqlite_url, resolve_url, backend_kind, schema_status,
)

_ready_cache = {}
_ready_lock = threading.Lock()


def ensure_ready(url=None):
    """库就绪检查；隔离根允许惰性初始化。返回 (url, schema_version)。"""
    url = url or engine.resolve_url()
    with _ready_lock:
        cached = _ready_cache.get(url)
        if cached:
            return url, cached
        ready, version = engine.schema_status(url)
        if not ready:
            import os
            if os.environ.get('WIZ_WORKBENCH_ROOT'):
                engine.initialize(url)
                ready, version = engine.schema_status(url)
            else:
                raise StorageUnavailable(
                    '工作台数据库尚未初始化：请先运行 python3 -m workbench.storage.transfer init')
        _ready_cache[url] = version
        return url, version


def mark_unready(url=None):
    """测试隔离用：数据库文件被替换后清空就绪缓存并丢弃引擎池。"""
    url = url or engine.resolve_url()
    with _ready_lock:
        _ready_cache.pop(url, None)
    engine.reset_engine()
