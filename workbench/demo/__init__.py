"""内置演示执行器（B4 自 service/ 迁入）：仅 storage 本体可用、只读、不发任意 SQL。

核心存储模块不得反向依赖本包；演示按需依赖 workbench.paths / model_format。
旧命令 ``python3 -m service.demo`` 由 service/ 下的转发垫片短期保留。
"""
from workbench.demo.demo import Demo
from workbench.paths import DATA_ROOT as ROOT

__all__ = ['Demo', 'ROOT']
