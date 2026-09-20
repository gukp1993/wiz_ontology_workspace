#!/usr/bin/env python3
"""轻量一键测试入口（批次 C，2026-09-18）。

用法：
    python3 tests/run.py              # 快速组：本轮改动路径（导出/恢复 + 错误边界 + 保存迭代）
    python3 tests/run.py http         # 全部起真实 HTTP 服务的集成测试
    python3 tests/run.py unit         # 其余单元/逻辑测试
    python3 tests/run.py all          # 上述全部（不含 external）
    python3 tests/run.py external     # 需要本机 MySQL 的测试（环境就绪时单独跑）
    python3 tests/run.py --list       # 只列出各组清单
    python3 tests/run.py --test test_flows.py   # 只跑指定文件

设计约束（《代码审查修改意见_20260918.md》第六节）：
- 每个测试用独立子进程运行——避免同进程模块缓存、环境变量与端口互相污染；
  现有脚本是「可直接执行」风格（顶层 main），不是 pytest 收集风格，文件名
  符合 test_*.py 不代表可被 pytest 安全收集。
- 子进程环境显式剥除 WIZ_DATABASE_URL / WIZ_WORKBENCH_ROOT / WIZ_WORKBENCH_PORT，
  由各测试自行设置隔离值，绝不继承本机环境连到真实数据库。
- 「零测试被收集」不算通过：组清单硬编码，文件不存在即报错退出。
- 失败可靠报告：逐个记录退出码与尾部输出，存在非零退出码则整体退出码为 1。
"""
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TESTS = REPO / 'tests'

# 其余 python 测试（纯逻辑 / 直调存储层）：按目录实际存在且不在 HTTP/QUICK 组的
# test_*.py 运行时推导，防止清单与目录漂移导致漏跑或跑到不存在的文件。
HTTP_TESTS = [
    'test_references.py',               # 20260920：草稿悬空引用检查与保存边界
    'test_config_packages.py',          # 20260919：配置迁移导出/导入全链路（格式安全+幂等+隔离）
    'test_export_restore_http.py',      # 批次 A：导出 / 恢复链路
    'test_http_error_boundaries.py',    # 批次 B：错误分类 / R4 / R5
    'test_save_iteration.py',
    'test_project_api_roundtrip.py',
    'test_formatting_roundtrip.py',
    'test_flow_executor.py',
]

# 需要外部依赖（本机 MySQL）的测试：不进 all 默认回归，避免环境缺失误报；
# 本机 MySQL 就绪后可 `python3 tests/run.py external` 单独跑。
EXTERNAL_TESTS = [
    'test_property_preview.py',       # 需 MySQL（pymysql 连 127.0.0.1）
    'test_incoming_aggregate.py',     # 需 MySQL
]

# 快速组：本轮（批次 A/B）改动路径的定向回归
QUICK_TESTS = [
    'test_export_restore_http.py',
    'test_http_error_boundaries.py',
    'test_save_iteration.py',
]

GROUPS = {}


def _discover_unit():
    known = set(HTTP_TESTS) | set(QUICK_TESTS) | set(EXTERNAL_TESTS)
    return sorted(p.name for p in TESTS.glob('test_*.py') if p.name not in known)


GROUPS['quick'] = QUICK_TESTS
GROUPS['http'] = HTTP_TESTS
GROUPS['unit'] = _discover_unit()
GROUPS['external'] = EXTERNAL_TESTS
GROUPS['all'] = GROUPS['quick'] + [t for t in GROUPS['http'] if t not in QUICK_TESTS] + GROUPS['unit']

DEFAULT_TIMEOUT = 600  # 单个测试最长 10 分钟


def run_one(name):
    path = TESTS / name
    if not path.is_file():
        print(f'[错误] 测试文件不存在：{path}')
        return None
    env = {k: v for k, v in os.environ.items()
           if k not in ('WIZ_DATABASE_URL', 'WIZ_WORKBENCH_ROOT', 'WIZ_WORKBENCH_PORT')}
    started = time.time()
    print(f'\n=== {name} ===')
    try:
        proc = subprocess.run([sys.executable, str(path)], cwd=str(REPO), env=env,
                              capture_output=True, text=True, timeout=DEFAULT_TIMEOUT)
        output, code = (proc.stdout or '') + (proc.stderr or ''), proc.returncode
    except subprocess.TimeoutExpired as exc:
        output = ((exc.stdout or b'').decode(errors='replace')
                  + (exc.stderr or b'').decode(errors='replace'))
        code = 'TIMEOUT'
    tail = '\n'.join((output or '').strip().splitlines()[-6:])
    if tail:
        print(tail)
    status = '通过' if code == 0 else f'失败（退出码 {code}）'
    print(f'--- {name}: {status}（{time.time() - started:.1f}s）')
    return code


def main(argv):
    if '--list' in argv:
        for group, names in GROUPS.items():
            print(f'[{group}] ({len(names)})')
            for n in names:
                print(f'  {n}')
        return 0
    explicit_test = None
    if '--test' in argv:
        idx = argv.index('--test')
        if idx + 1 >= len(argv):
            print('用法：python3 tests/run.py --test <文件名>')
            return 2
        explicit_test = argv[idx + 1]
        target = [explicit_test]
        group_name = '--test'
    else:
        group_name = next((a for a in argv if not a.startswith('-')), 'quick')
        if group_name not in GROUPS:
            print(f'未知组：{group_name}（可用：{" / ".join(GROUPS)}，或 --test <文件>）')
            return 2
        target = GROUPS[group_name]

    print(f'组：{group_name}，共 {len(target)} 个测试')
    results = {}
    for name in target:
        code = run_one(name)
        if code is None:
            return 2
        results[name] = code

    failed = {n: c for n, c in results.items() if c != 0}
    print('\n========== 汇总 ==========')
    print(f'通过 {len(results) - len(failed)} / {len(results)}')
    for name, code in failed.items():
        print(f'  失败: {name}（退出码 {code}）')
    if not results:
        print('[错误] 没有收集到任何测试——不算通过')
        return 2
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
