"""B1 验收：统一 CODE_ROOT/DATA_ROOT，核心模块不依赖演示模块。

运行：python3 tests/test_paths_isolation.py
覆盖：
  1. 导入核心存储模块不触发 service.demo 加载
  2. WIZ_WORKBENCH_ROOT 覆盖 DATA_ROOT，未设置时回落仓库根
  3. server 的静态资源走 CODE_ROOT（不受临时数据根影响）
  4. 隔离根下 workflow.defaults() 不再静默读取真实 ontology 数据
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PASSED = []


def run(name, fn):
    try:
        fn()
        PASSED.append(name)
        print(f'通过：{name}')
    except AssertionError as exc:
        print(f'失败：{name}\n  {exc}')
        raise SystemExit(1)


def in_subprocess(env_extra, code):
    env = {**os.environ, **env_extra, 'PYTHONPATH': str(REPO)}
    return subprocess.run([sys.executable, '-c', code], env=env,
                          capture_output=True, text=True)


def core_imports_isolated():
    """1. 核心存储模块导入后 sys.modules 中不得出现 service.demo。"""
    code = (
        "import sys\n"
        "import workbench.projects, workbench.workspaces, workbench.versions\n"
        "import workbench.secrets, workbench.catalogs, workbench.workflow, workbench.paths\n"
        "assert 'service.demo' not in sys.modules, '核心模块加载了 service.demo'\n"
        "assert 'workbench.demo' not in sys.modules, '核心模块加载了 workbench.demo'\n"
        "print('ok')\n"
    )
    r = in_subprocess({'WIZ_WORKBENCH_ROOT': tempfile.mkdtemp()}, code)
    assert r.returncode == 0 and 'ok' in r.stdout, r.stderr + r.stdout


def data_root_override():
    """2. 临时根生效；未设置时 DATA_ROOT 为仓库根。"""
    tmp = tempfile.mkdtemp()
    r = in_subprocess({'WIZ_WORKBENCH_ROOT': tmp},
                      "from workbench import paths, workspaces\n"
                      "assert str(paths.DATA_ROOT) == %r, paths.DATA_ROOT\n"
                      "assert str(workspaces.DRAFT_ROOT).startswith(%r)\n"
                      "print('ok')" % (tmp, tmp))
    assert r.returncode == 0, r.stderr
    r2 = in_subprocess({'WIZ_WORKBENCH_ROOT': ''},
                       "from workbench import paths\n"
                       "assert paths.DATA_ROOT == paths.CODE_ROOT, (paths.DATA_ROOT, paths.CODE_ROOT)\n"
                       "print('ok')")
    assert r2.returncode == 0, r2.stderr


def static_uses_code_root():
    """3. server.STATIC 始终指向代码仓的 frontend/dist，与数据根无关。"""
    r = in_subprocess({'WIZ_WORKBENCH_ROOT': tempfile.mkdtemp()},
                      "from workbench import server\n"
                      "assert str(server.STATIC).startswith(%r), server.STATIC\n"
                      "print('ok')" % str(REPO))
    assert r.returncode == 0, r.stderr


def workflow_defaults_respects_root():
    """4. 隔离根没有基础文件时 defaults() 报 FileNotFoundError，而不是读真实数据。"""
    r = in_subprocess({'WIZ_WORKBENCH_ROOT': tempfile.mkdtemp()},
                      "from workbench import workflow\n"
                      "try:\n"
                      "    workflow.defaults()\n"
                      "    raise SystemExit('应当报错')\n"
                      "except FileNotFoundError:\n"
                      "    print('ok')")
    assert r.returncode == 0 and 'ok' in r.stdout, r.stderr + r.stdout


if __name__ == '__main__':
    run('核心导入不加载演示模块', core_imports_isolated)
    run('数据根覆盖与回落', data_root_override)
    run('静态资源走代码根', static_uses_code_root)
    run('defaults 不读真实数据', workflow_defaults_respects_root)
    print(f'统计：{len(PASSED)} 项全部通过')
