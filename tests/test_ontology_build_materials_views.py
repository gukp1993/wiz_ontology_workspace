"""物料分组/分页查询回归（08 §12.1：view=groups 与 folder 分页的服务端语义）。

隔离：临时数据根（WIZ_WORKBENCH_ROOT + 该根下 SQLite），结束清理；
不访问网络、不连真实库、不碰 18765/18881。

运行：python3 tests/test_ontology_build_materials_views.py
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from workbench import storage  # noqa: E402
from workbench.storage import engine as sto  # noqa: E402
from workbench.storage import ontology_build as store  # noqa: E402

UID = 'mat-views-owner'
OTHER = 'mat-views-other'

PASSED = []
FAILED = []
SEQ = [0]
ROOTS = []


def check(cond, message, actual=None):
    SEQ[0] += 1
    if cond:
        PASSED.append(message)
        print('通过 %d) %s' % (SEQ[0], message))
    else:
        FAILED.append(message)
        print('[失败] %d) %s  实际: %s' % (SEQ[0], message, actual))


def new_isolated_root(tag):
    root = Path(tempfile.mkdtemp(prefix='wiz_mat_views_%s_' % tag))
    ROOTS.append(root)
    os.environ['WIZ_WORKBENCH_ROOT'] = str(root)
    os.environ['WIZ_DATABASE_URL'] = 'sqlite:///' + str(root / 'data' / 'workbench.sqlite3')
    sto.reset_engine()
    storage.mark_unready()
    storage.ensure_ready()
    return root


def cleanup_roots():
    for root in ROOTS:
        shutil.rmtree(root, ignore_errors=True)
    del ROOTS[:]


def seed_materials(conn, task_id, owner, rows):
    for rel, state, size in rows:
        blob_id = store.create_blob(conn, owner, task_id, rel, size, 'h-' + rel, 'blobs/' + rel)
        store.create_material(conn, owner, task_id, blob_id, rel, 'code', size, 'h-' + rel)
        items = store.list_materials(conn, task_id, owner)
        target = next(m for m in items if m['relPath'] == rel)
        store.update_material(conn, target['id'], owner, parse_state=state)


def main():
    new_isolated_root('main')
    with sto.write_tx() as tx:
        def seed(conn):
            task_id = store.create_task(conn, UID, '分组分页回归')
            rows = [
                ('src/Device.java', 'success', 100),
                ('src/DeviceMapper.xml', 'success', 200),
                ('src/util/Json.java', 'pending', 30),
                ('docs/需求.md', 'success', 400),
                ('root.sql', 'failed', 50),
            ]
            seed_materials(conn, task_id, UID, rows)
            return task_id
        task_id = tx.run(seed)

    with sto.read_connection() as conn:
        groups, total = store.list_material_groups(conn, task_id, UID)
    check(total == 5, '分组查询 total=5', total)
    by_folder = {g['folder']: g for g in groups}
    check(sorted(by_folder) == ['', 'docs', 'src'], '顶层目录分组正确（根目录组为空串）',
          sorted(by_folder))
    src = by_folder['src']
    check(src['total'] == 3 and src['bytes'] == 330, 'src 组计数与字节合计', src)
    check(src['byParseState'] == {'success': 2, 'pending': 1}, 'src 组状态分布', src['byParseState'])
    check([g['folder'] for g in groups] == ['src', 'docs', ''],
          'groups 按 total 降序（src=3 > docs=1 > 根=1）',
          [(g['folder'], g['total']) for g in groups])

    with sto.read_connection() as conn:
        page1, total1 = store.list_materials_page(conn, task_id, UID, folder='src', offset=0, limit=2)
        page2, total2 = store.list_materials_page(conn, task_id, UID, folder='src', offset=2, limit=2)
        root_group, root_total = store.list_materials_page(conn, task_id, UID, folder='', offset=0, limit=10)
        all_items, all_total = store.list_materials_page(conn, task_id, UID, offset=0, limit=2)
    check([m['relPath'] for m in page1] == ['src/Device.java', 'src/DeviceMapper.xml']
          and [m['relPath'] for m in page2] == ['src/util/Json.java'],
          'folder 组内分页正确（第 2 页含剩余 1 条）',
          ([m['relPath'] for m in page1], [m['relPath'] for m in page2]))
    check(total1 == 3 and total2 == 3, 'folder 组 total 为组内总数', (total1, total2))
    check(len(root_group) == 1 and root_group[0]['relPath'] == 'root.sql' and root_total == 1,
          '空串 folder 过滤根目录组', root_group)
    check(all_total == 5 and len(all_items) == 2, '不传 folder 全量分页兼容', (all_total, len(all_items)))

    with sto.read_connection() as conn:
        other_tasks, other_total = store.list_materials_page(conn, task_id, OTHER, offset=0, limit=10)
        other_groups, other_groups_total = store.list_material_groups(conn, task_id, OTHER)
    check(other_total == 0 and other_groups_total == 0, '跨账号查询为空', (other_total, other_groups_total))
    del other_tasks, other_groups

    cleanup_roots()
    print('\n========== 汇总 ==========')
    print('通过 %d / %d' % (len(PASSED), len(PASSED) + len(FAILED)))
    for name in FAILED:
        print('  失败: ' + name)
    return 0 if not FAILED else 1


if __name__ == '__main__':
    try:
        code = main()
    finally:
        cleanup_roots()
    sys.exit(code)
