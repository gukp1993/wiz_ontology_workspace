"""G25f 数据点：≥1000 小文件物料集的串行（并发=1）vs 默认并发解析耗时实测。

* 隔离：临时数据根（WIZ_WORKBENCH_ROOT + 该根下 SQLite），结束清理；不访问网络、
  不连真实库、不碰 18765/18881/18882。
* 物料为运行时生成的合成 md（每份 ~12 行），经生产登记路径（materials.upload_*）
  落库；扫描直调 pipeline.run_scan（与白盒测试同一方式，不持全局锁）。
* 两组各跑一次：并发=1（串行基线）与默认并发（min(8, CPU)，env 未覆盖时的生效值）。
  输出两个耗时数字，不做倍数承诺（G25f）。

运行：.venv/bin/python tests/benchmark_ontology_build_parse.py [文件数，默认 1200]
"""
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

FILE_COUNT = int(sys.argv[1]) if len(sys.argv) > 1 else 1200

TMP = Path(tempfile.mkdtemp(prefix='wiz_parse_bench_'))
os.environ['WIZ_WORKBENCH_ROOT'] = str(TMP)
os.environ['WIZ_DATABASE_URL'] = 'sqlite:///' + str(TMP / 'data' / 'workbench.sqlite3')

from workbench import auth  # noqa: E402
from workbench import storage  # noqa: E402
from workbench.ontology_build import materials as materials_domain  # noqa: E402
from workbench.ontology_build import pipeline  # noqa: E402
from workbench.ontology_build import protocol  # noqa: E402
from workbench.ontology_build import runner  # noqa: E402
from workbench.storage import engine as sto  # noqa: E402
from workbench.storage import ontology_build as store  # noqa: E402

UID = 'bench-user'


def register_file(task_id, rel_path, content: bytes):
    """生产登记路径：upload_init → chunk → complete（单分片小文件，三个短事务）。"""
    import base64
    import hashlib

    def init(conn):
        return materials_domain.upload_init(conn, UID, task_id, rel_path, len(content))

    def chunk(conn, upload_id):
        return materials_domain.upload_chunk(conn, UID, upload_id, 0,
                                             hashlib.sha256(content).hexdigest(),
                                             base64.b64encode(content).decode())

    def complete(conn, upload_id):
        return materials_domain.upload_complete(conn, UID, upload_id,
                                                hashlib.sha256(content).hexdigest())

    with sto.write_tx() as tx:
        init_result = tx.run(init)
    with sto.write_tx() as tx:
        tx.run(lambda conn: chunk(conn, init_result['uploadId']))
    with sto.write_tx() as tx:
        tx.run(lambda conn: complete(conn, init_result['uploadId']))


def build_task(name, concurrency):
    """建任务 + 上传全部合成物料（不动 protocol.PARSE_CONCURRENCY 之外的环境）。"""
    auth.bind_request({'userId': UID})
    try:
        with sto.write_tx() as tx:
            task_id = tx.run(lambda conn: store.create_task(conn, UID, name))
        import os as _os
        lines = int(_os.environ.get('BENCH_FILE_LINES', '10'))
        body = ('# 设备台账 {n}\n\n'
                '额定容量与监测数据说明：储能设备 {n} 的运行台账。\n\n'
                '| 字段 | 说明 |\n|---|---|\n'
                '| capacity | 额定容量（kWh） |\n| soc | 当前荷电状态 |\n\n'
                + ('设备运行监测记录行：SOC、电压、温度采样与告警说明。\n' * lines))
        for index in range(FILE_COUNT):
            register_file(task_id, 'bench/bench_%05d.md' % index,
                          body.format(n=index).encode('utf-8'))
        return task_id
    finally:
        auth.bind_request(None)


def scan_once(task_id, concurrency):
    """直调 run_scan（并发度临时覆盖），返回 wall 秒。"""
    saved = protocol.PARSE_CONCURRENCY
    protocol.PARSE_CONCURRENCY = concurrency
    auth.bind_request({'userId': UID})
    try:
        with sto.write_tx() as tx:
            run_id, _lease = tx.run(lambda conn: store.create_run(
                conn, task_id, UID, 'scan', {}))
        started = time.monotonic()
        summary = pipeline.run_scan(UID, task_id, run_id, provider=None)
        elapsed = time.monotonic() - started
        assert summary['state'] == 'succeeded', summary
        return elapsed, summary
    finally:
        protocol.PARSE_CONCURRENCY = saved
        auth.bind_request(None)


def main():
    storage.ensure_ready()
    runner.interrupt_stale_runs()
    print('文件数：%d（合成 md，经生产上传路径登记）' % FILE_COUNT)
    print('默认并发（capabilities.limits.parseConcurrency）：%d'
          % protocol.LIMITS['parseConcurrency'])
    task_serial = build_task('G25f-串行基线', concurrency=1)
    task_parallel = build_task('G25f-默认并发', concurrency=protocol.LIMITS['parseConcurrency'])

    t_serial, summary_serial = scan_once(task_serial, 1)
    t_parallel, summary_parallel = scan_once(task_parallel,
                                             protocol.LIMITS['parseConcurrency'])
    print('串行（并发=1）解析落库耗时：%.2f s（facts=%d, parsed=%d）'
          % (t_serial, summary_serial['facts'], summary_serial['parsed']))
    print('默认并发（=%d）解析落库耗时：%.2f s（facts=%d, parsed=%d）'
          % (protocol.LIMITS['parseConcurrency'], t_parallel,
             summary_parallel['facts'], summary_parallel['parsed']))
    print('G25F_RESULT files=%d serial=%.2fs parallel=%.2fs concurrency=%d'
          % (FILE_COUNT, t_serial, t_parallel, protocol.LIMITS['parseConcurrency']))
    return 0


if __name__ == '__main__':
    try:
        code = main()
    finally:
        shutil.rmtree(str(TMP), ignore_errors=True)
    sys.exit(code)
