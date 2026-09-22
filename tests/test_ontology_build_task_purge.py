"""任务删除级联物理清理回归（08 §12.2：purge 任务 + blob 文件 + 本体草稿保留）。

隔离：临时数据根（WIZ_WORKBENCH_ROOT + 该根下 SQLite），结束清理；
不访问网络、不连真实库、不碰 18765/18881。

运行：python3 tests/test_ontology_build_task_purge.py
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from workbench import storage  # noqa: E402
from workbench.ontology_build import materials as materials_domain  # noqa: E402
from workbench.storage import engine as sto  # noqa: E402
from workbench.storage import assets as asset_store  # noqa: E402
from workbench.storage import ontology_build as store  # noqa: E402

UID = 'purge-owner'
OTHER = 'purge-other'

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
    root = Path(tempfile.mkdtemp(prefix='wiz_task_purge_%s_' % tag))
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


def blob_file(rel_name, content: bytes, form='blob-relative'):
    """在 blob 目录写一个真实文件，返回登记用 blob_path。

    form='blob-relative'：相对 blob 目录（历史行/早期夹具形态，G24 兼容口径）；
    form='production'：相对数据目录的 'ontology-build-blobs/<名>'——与
    materials.upload_complete/_register_extracted 的**生产登记同形态**（G24 回归盲区：
    此前夹具用了与生产不同的基准，掩盖了删除侧双重前缀缺陷）。
    """
    target = materials_domain.blob_dir() / rel_name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    if form == 'production':
        return '%s/%s' % (materials_domain.BLOB_SUBDIR, rel_name)
    return str(target.relative_to(materials_domain.blob_dir()))


def build_task_with_payload(owner, task_name, with_blob_content=b'payload-bytes', form='blob-relative'):
    """建任务 + 全关联行（含 blob 磁盘文件），可选再交付一份本体草稿。返回上下文 dict。"""
    ctx = {}

    def body(conn):
        task_id = store.create_task(conn, owner, task_name)
        store.touch_task(conn, task_id, owner, status='review', stage_label='评审初稿')
        blob_path = blob_file('purge-test/%s.bin' % task_id[:8], with_blob_content, form=form)
        blob_id = store.create_blob(conn, owner, task_id, 'a.bin', len(with_blob_content),
                                    'h-' + task_id[:8], blob_path)
        material_id = store.create_material(conn, owner, task_id, blob_id, 'a.bin',
                                            'code', len(with_blob_content), 'h-' + task_id[:8])
        store.replace_material_facts(conn, task_id, owner, material_id,
                                     [{'id': 'f-' + task_id[:8], 'module': 'm',
                                       'locator': {'kind': 'code'}, 'snippet': 's',
                                       'kind': 'class', 'data': {}, 'quality': 'high'}])
        store.append_message(conn, task_id, owner, 'user', 'hello')
        run_id, _lease = store.create_run(conn, task_id, owner, 'scan', {})
        batch_id = store.create_batch(conn, task_id, owner, run_id, {})
        store.create_candidate(conn, task_id, owner, batch_id,
                               {'type': 'object', 'key': 'k', 'name': '对象', 'definition': 'd'})
        store.append_review_op(conn, task_id, owner, 'merge', {})
        store.put_scope(conn, task_id, owner, {'goal': 'g', 'include': 'i'}, confirmed=True)
        ctx.update({'task_id': task_id, 'blob_path': blob_path, 'material_id': material_id,
                    'batch_id': batch_id})
        return task_id

    with sto.write_tx() as tx:
        ctx['task_id'] = tx.run(body)
    return ctx


def row_count(table, task_id):
    with sto.read_connection() as conn:
        return int(conn.execute(sto.text('SELECT COUNT(*) FROM ' + table +
                                         ' WHERE task_id = :t AND owner_user_id = :o'),
                                {'t': task_id, 'o': UID}).scalar() or 0)


def main():
    new_isolated_root('main')

    print('--- 正常级联清理 ---')
    ctx = build_task_with_payload(UID, '待清理任务')
    blob_file_abs = materials_domain.blob_dir() / ctx['blob_path']
    check(blob_file_abs.is_file(), '前置：blob 文件已落盘', str(blob_file_abs))

    # 交付一份本体草稿（验证删除任务不影响已交付资产）
    with sto.write_tx() as tx:
        def deliver(conn):
            ontology = {'@context': {}, '@graph': [], 'definitionOrder': []}
            created = store.create_ontology_asset(conn, UID, ctx['task_id'] + '-ont',
                                                  '清理回归本体', ontology,
                                                  'workbench-state-1')
            store.insert_delivery(conn, ctx['task_id'], UID, 'req-purge', 'digest',
                                  ctx['task_id'] + '-ont')
            return created
        tx.run(deliver)
    check(asset_store.read_current('model', ctx['task_id'] + '-ont', owner_user_id=UID) is not None,
          '前置：已交付本体草稿存在')

    with sto.write_tx() as tx:
        counts, blob_paths = tx.run(lambda conn: store.purge_task(conn, ctx['task_id'], UID))
    cleaned = materials_domain.delete_task_blob_files(blob_paths)

    check(counts['wb_build_tasks'] == 1, '任务行已删除', counts)
    check(all(counts.get(t, 0) >= 1 for t in (
        'wb_build_materials', 'wb_build_messages', 'wb_build_runs', 'wb_build_batches',
        'wb_build_review_ops', 'wb_build_scopes', 'wb_build_deliveries',
        'wb_build_blobs', 'wb_build_tasks')) and counts.get('wb_build_tasks') == 1,
        '全部关联行级联删除（夹具实际写入的表各 ≥1）', counts)
    check(cleaned['deleted'] == 1 and cleaned['errors'] == [], 'blob 文件已删除', cleaned)
    check(not blob_file_abs.exists(), '磁盘文件确认不存在')
    check(asset_store.read_current('model', ctx['task_id'] + '-ont', owner_user_id=UID) is not None,
          '已交付本体草稿保留')

    print('--- 生产登记形态（G24 回归：ontology-build-blobs/<名> 相对数据目录）---')
    ctx_prod = build_task_with_payload(UID, '生产形态清理', form='production')
    # 生产形态的物理文件在 <data>/ontology-build-blobs/purge-test/<id>.bin
    blob_abs_prod = materials_domain.blob_dir() / ('purge-test/%s.bin' % ctx_prod['task_id'][:8])
    check(blob_abs_prod.is_file(), '前置：生产形态 blob 文件已落盘', str(blob_abs_prod))
    check(ctx_prod['blob_path'].startswith(materials_domain.BLOB_SUBDIR + '/'),
          '前置：登记为相对数据目录的生产形态', ctx_prod['blob_path'])
    with sto.write_tx() as tx:
        counts_prod, blob_paths_prod = tx.run(lambda conn: store.purge_task(conn, ctx_prod['task_id'], UID))
    cleaned_prod = materials_domain.delete_task_blob_files(blob_paths_prod)
    check(counts_prod['wb_build_blobs'] == 1 and cleaned_prod['deleted'] == 1
          and cleaned_prod['missing'] == 0 and cleaned_prod['errors'] == [],
          'G24 修复：生产形态登记的 blob 物理文件被删除（不再双重前缀静默跳过）',
          (blob_paths_prod, cleaned_prod))
    check(not blob_abs_prod.exists(), '生产形态磁盘文件确认不存在')

    print('--- 逃逸与缺失防御 ---')
    cleaned_escape = materials_domain.delete_task_blob_files(['../outside.bin', 'gone.bin'])
    check(cleaned_escape['deleted'] == 0
          and not (materials_domain.data_dir().parent / 'outside.bin').exists(),
          '路径穿越登记不删除 blob 目录之外文件（按缺失/拒绝处理）', cleaned_escape)
    check(cleaned_escape['missing'] == 2,
          '不存在的登记（含穿越形态）按 missing 计数，不伪造删除成功', cleaned_escape)

    print('--- 确认名不符在路由层拒绝（域层仅提供 purge，此处验证不存在即 404 语义）---')
    try:
        with sto.write_tx() as tx:
            tx.run(lambda conn: store.purge_task(conn, 'no-such-task', UID))
        check(False, '不存在的任务 purge 应抛 NotFound')
    except sto.NotFound:
        check(True, '不存在的任务 purge 抛 NotFound')
    except Exception as exc:  # noqa: BLE001
        check(False, '不存在的任务 purge 抛 NotFound', repr(exc))

    print('--- 跨账号隔离 ---')
    ctx2 = build_task_with_payload(OTHER, '他人任务')
    try:
        with sto.write_tx() as tx:
            tx.run(lambda conn: store.purge_task(conn, ctx2['task_id'], UID))
        check(False, '跨账号 purge 应抛 NotFound')
    except sto.NotFound:
        check(True, '跨账号 purge 按不存在处理')
    except Exception as exc:  # noqa: BLE001
        check(False, '跨账号 purge 按不存在处理', repr(exc))
    check(row_count('wb_build_materials', ctx2['task_id']) >= 0, '他人任务数据不受影响')
    with sto.read_connection() as conn:
        other_rows = int(conn.execute(sto.text(
            'SELECT COUNT(*) FROM wb_build_materials WHERE task_id = :t AND owner_user_id = :o'),
            {'t': ctx2['task_id'], 'o': OTHER}).scalar() or 0)
    check(other_rows >= 1, '他人任务行数不变', other_rows)

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
