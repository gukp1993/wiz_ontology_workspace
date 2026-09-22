"""结构化格式解析器接线回归（需求《需求说明_结构化格式解析支持_v1》v1.1 §2/§3/§5/§6-S6）。

覆盖范围（纯函数/直调，不起 HTTP 服务、不碰真实数据根、不访问网络）：
* `protocol.detect_kind` 新后缀映射：`.json/.jsonld/.jsonid/.jsonl/.ndjson → json`、
  `.yaml/.yml → yaml`、`.properties → properties`、`.csv/.tsv → csv`、`.ini/.cfg/.conf → ini`、
  `.toml → toml`；既有后缀（.java/.sql/.md/.docx/.zip/.py/.xml 等）回归不变；
  PK/PDF 魔数优先级仍高于后缀（本轮只加后缀表，未动魔数分支）。
* `parsers/__init__.py` 注册：六个新 kind 在 `base.REGISTRY` 且经 `parse_material` 全链路
  产出事实（json-ld 片段 / yaml / csv / properties / ini / toml 各一份合成材料），
  每条事实的 locator 有 kind/file。
* 坏 JSON 显式 failure：零事实、coverage.failedSegments 有语法错误条目、无 LLM 兜底/文本线索
  降级事实（module 不为 llm-fallback/text），不回退兜底（§2 统一约束）。
* 三级分派位置：六个新 kind ∈ `pipeline.DEDICATED_KINDS`（第①层专用解析，不进 LLM 兜底）。
* 二进制软黑名单：parquet/proto/avro/msgpack 上传评估进排除（layer=soft，可查看、可被白名单
  越过）；`.xls` 不并入该组（维持既有「建议转 .xlsx」路径）；`.env` 仍为硬黑名单。
* `get_capabilities` 的 `parserMatrix`：7 个支持项 + 2 个排除项，exts 齐全、字段形状正确。

隔离：合成材料写进 `tempfile.mkdtemp()`；能力查询用隔离数据根（WIZ_WORKBENCH_ROOT +
该根下 SQLite），结束整体删除。

运行：python3 tests/test_ontology_build_parser_wiring.py
"""
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from workbench.ontology_build import blacklist  # noqa: E402
from workbench.ontology_build import pipeline  # noqa: E402
from workbench.ontology_build import protocol  # noqa: E402
from workbench.ontology_build.parsers import base, parse_material  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix='wiz_wiring_'))
ROOT = Path(tempfile.mkdtemp(prefix='wiz_wiring_root_'))
os.environ['WIZ_WORKBENCH_ROOT'] = str(ROOT)
os.environ['WIZ_DATABASE_URL'] = 'sqlite:///' + str(ROOT / 'data' / 'workbench.sqlite3')

PASSED = []
FAILED = []


def check(condition, name, actual=None):
    if condition:
        PASSED.append(name)
        print('  ok   %s' % name)
    else:
        FAILED.append(name)
        print('  FAIL %s%s' % (name, '' if actual is None else '  actual=%r' % (actual,)))


def write(name, text):
    """合成材料写入临时根，返回绝对路径（str）。"""
    path = TMP / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')
    return str(path)


# ---------------------------------------------------------------------------
# 1) detect_kind 新后缀映射 + 既有后缀回归
# ---------------------------------------------------------------------------
NEW_EXT_KINDS = [
    ('x.json', 'json'), ('x.jsonld', 'json'), ('x.jsonid', 'json'),
    ('x.jsonl', 'json'), ('x.ndjson', 'json'),
    ('x.yaml', 'yaml'), ('x.yml', 'yaml'),
    ('x.properties', 'properties'),
    ('x.csv', 'csv'), ('x.tsv', 'csv'),
    ('x.ini', 'ini'), ('x.cfg', 'ini'), ('x.conf', 'ini'),
    ('x.toml', 'toml'),
]
LEGACY_EXT_KINDS = [
    ('a/Board.java', 'code'), ('a/schema.sql', 'ddl'), ('a/readme.md', 'md'),
    ('a/doc.docx', 'docx'), ('a/tool.py', 'code'), ('a/mapper.xml', 'code'),
    ('a/app.ts', 'code'), ('a/photo.png', 'image'), ('a/data.txt', 'md'),
    ('a/legacy.xls', 'xlsx'),
]
# `.zip` 非空头（无 PK 魔数）在改动前后都归 other——既有行为，不属本轮回归范围

print('== 1. detect_kind 后缀映射（新 + 既有回归） ==')
for rel, expected in NEW_EXT_KINDS:
    check(protocol.detect_kind(rel) == expected, 'detect_kind %s → %s' % (rel, expected),
          protocol.detect_kind(rel))
# 大小写不敏感（用户真实样本扩展名为 .jsonId）
check(protocol.detect_kind('储能_V20260814_0002 (3).jsonId') == 'json',
      'detect_kind 大小写混排 .jsonId → json',
      protocol.detect_kind('储能_V20260814_0002 (3).jsonId'))
for rel, expected in LEGACY_EXT_KINDS:
    check(protocol.detect_kind(rel) == expected, 'detect_kind 回归 %s → %s' % (rel, expected),
          protocol.detect_kind(rel))
# 魔数优先级不变（只加了后缀表，PK/PDF 分支仍在前）
check(protocol.detect_kind('a/x.json', head=b'PK\x03\x04') == 'zip',
      '魔数 PK 优先于 .json 后缀（顺序未变）',
      protocol.detect_kind('a/x.json', head=b'PK\x03\x04'))
check(protocol.detect_kind('a/x.csv', head=b'%PDF-1.7') == 'pdf',
      '魔数 %PDF 优先于 .csv 后缀（顺序未变）',
      protocol.detect_kind('a/x.csv', head=b'%PDF-1.7'))
check(protocol.detect_kind('a/pkg.zip', head=b'PK\x03\x04') == 'zip',
      'ZIP 仍按 PK 魔数识别（既有路径不变）',
      protocol.detect_kind('a/pkg.zip', head=b'PK\x03\x04'))
check(protocol.detect_kind('a/legacy.xls', head=b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1') == 'xlsx',
      'OLE 魔数 .xls 仍归 xlsx（转换提示路径不变）',
      protocol.detect_kind('a/legacy.xls', head=b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'))
# MATERIAL_KINDS 覆盖六个新 kind（协议枚举不漂移）
for kind in ('json', 'yaml', 'properties', 'csv', 'ini', 'toml'):
    check(kind in protocol.MATERIAL_KINDS, 'MATERIAL_KINDS 含 %s' % kind)

# ---------------------------------------------------------------------------
# 2) 注册表 + 全链路产出事实（合成材料，六种格式各一份）
# ---------------------------------------------------------------------------
print('== 2. parsers 注册与全链路解析 ==')
for kind in ('json', 'yaml', 'properties', 'csv', 'ini', 'toml'):
    check(kind in base.REGISTRY, 'REGISTRY 已登记 %s' % kind)

JSONLD_SAMPLE = json.dumps({
    '@context': {'rdfs': 'http://www.w3.org/2000/01/rdf-schema#'},
    '@graph': [
        {'@id': 'urn:ess:BatteryCluster', 'nodeType': 'object',
         'name': '电池簇', 'rdfs:label': '电池簇', 'definition': '储能系统中的电池簇'},
    ],
}, ensure_ascii=False)

SAMPLES = [
    ('samples/model.jsonld', 'json', JSONLD_SAMPLE),
    ('samples/conf.yaml', 'yaml', 'battery:\n  id: b1\n  soc: 0.5\n  tags: [a, b]\n'),
    ('samples/app.properties', 'properties', '# 注释\nsoc.max=1.0\nname=\\u7535\\u6c60\n'),
    ('samples/table.csv', 'csv', 'id,soc\nb1,0.5\nb2,0.7\n'),
    ('samples/app.ini', 'ini', '[battery]\nid=b1\nsoc=0.5\n'),
    ('samples/min.toml', 'toml', 'name = "cell"\n[table]\nk = 1\n'),
]
for rel, kind, text in SAMPLES:
    path = write(rel.replace('samples/', ''), text)
    result = parse_material(path, 'bm-wiring', kind, rel)
    facts = list(result.facts)
    check(len(facts) > 0, '%s 全链路 facts>0' % rel, len(facts))
    check(not result.error, '%s 无解析错误' % rel, result.error)
    ok_locator = all(isinstance(f.locator, dict) and f.locator.get('kind') and f.locator.get('file')
                     for f in facts)
    check(ok_locator, '%s 每条事实 locator 有 kind/file' % rel,
          [f.locator for f in facts[:2]] if not ok_locator else None)

# JSON-LD 节点事实（需求 §6-S1：定位=节点 @id）
jsonld_result = parse_material(write('ld.jsonid', JSONLD_SAMPLE), 'bm-wiring', 'json', 'ld.jsonid')
node_facts = [f for f in jsonld_result.facts if str(f.locator.get('nodeId') or '')
              or f.kind.startswith('jsonLd')]
check(len(node_facts) > 0 and any(f.locator.get('nodeId') for f in node_facts),
      'JSON-LD @graph 节点事实带 locator.nodeId',
      [(f.kind, f.locator) for f in jsonld_result.facts[:3]])

# JSONL 逐行模式（同一 kind，按后缀分支）
jsonl_result = parse_material(
    write('rows.jsonl', '{"id": "b1", "soc": 0.5}\n{"id": "b2", "soc": 0.7}\n'),
    'bm-wiring', 'json', 'rows.jsonl')
check(len(jsonl_result.facts) > 0
      and all(int(f.locator.get('line') or 0) >= 1 for f in jsonl_result.facts
              if f.kind.endswith('Leaf')),
      'JSONL 逐行模式事实带真实行号',
      [(f.kind, f.locator) for f in jsonl_result.facts[:3]])

# 三态：解析质量与失败段可见（csv 列统计/行号）
csv_result = parse_material(write('t.csv', 'id,soc\nb1,0.5\nb2,0.7\n'), 'bm-wiring', 'csv', 't.csv')
check(all(int(f.locator.get('line') or 0) >= 1 for f in csv_result.facts),
      'csv 事实均带真实行号', [f.locator for f in csv_result.facts[:3]])

# ---------------------------------------------------------------------------
# 3) 坏 JSON：显式 failure、零事实、不回退兜底
# ---------------------------------------------------------------------------
print('== 3. 坏 JSON 显式失败 ==')
bad = parse_material(write('broken.json', '{oops'), 'bm-wiring', 'json', 'broken.json')
bad_payload = bad.coverage_payload()
check(bool(bad.error) and not bad.facts, '坏 JSON：error 非空且零事实', (bad.error, len(bad.facts)))
check(bool(bad.coverage.get('failedSegments')), '坏 JSON：coverage.failedSegments 有条目',
      bad.coverage.get('failedSegments'))
check(bad_payload.get('parseState') == 'failed' and bad_payload.get('error'),
      '坏 JSON：coverage_payload().parseState=failed（失败对页面可见）', bad_payload.get('parseState'))
# 不回退兜底：零事实已隐含无降级线索（兜底/文本线索路径必然产出事实）
check(all(f.module not in ('llm-fallback', 'text') for f in bad.facts),
      '坏 JSON：不产 LLM 兜底/文本线索降级事实',
      [f.module for f in bad.facts])

# ---------------------------------------------------------------------------
# 4) 三级分派位置：新 kind 属第①层（不进 LLM 兜底）
# ---------------------------------------------------------------------------
print('== 4. pipeline.DEDICATED_KINDS ==')
for kind in ('json', 'yaml', 'properties', 'csv', 'ini', 'toml'):
    check(kind in pipeline.DEDICATED_KINDS, '%s ∈ DEDICATED_KINDS（第①层）' % kind)
check('other' not in pipeline.DEDICATED_KINDS, 'other 仍走兜底（不在 DEDICATED_KINDS）')

# ---------------------------------------------------------------------------
# 5) 二进制软黑名单
# ---------------------------------------------------------------------------
print('== 5. 二进制软黑名单 ==')
for ext in ('parquet', 'proto', 'avro', 'msgpack'):
    verdict = blacklist.evaluate('pkg/data.%s' % ext)
    check(verdict['filtered'] and verdict['layer'] == blacklist.LAYER_SOFT
          and ext in verdict['rule'],
          '%s 进软黑名单排除（可查看）' % ext, verdict)
    # 白名单可越过软黑名单（G20 既有语义：软层非硬边界）。任务级 softExts 显式给出时
    # 该次越过在过滤事件里以 layer=soft + bypassed=true 呈现（evaluate 既有口径）。
    bypass = blacklist.evaluate('pkg/data.%s' % ext,
                                spec={'allowExts': ['.%s' % ext], 'softExts': ['.%s' % ext]})
    check(bypass['filtered'] is False and bypass['bypassed'] is True
          and bypass['layer'] == blacklist.LAYER_SOFT,
          '%s 可被用户白名单越过（layer=soft + bypassed）' % ext, bypass)
check('xls' not in blacklist.SOFT_EXTS, '.xls 未并入二进制组（维持转换提示路径）')
check(protocol.detect_kind('a/legacy.xls') == 'xlsx', '.xls 仍映射 xlsx（转 .xlsx 提示路径不变）')
check(blacklist.evaluate('cfg/.env')['layer'] == blacklist.LAYER_HARD,
      '.env 仍为硬黑名单（凭据边界不可越过）', blacklist.evaluate('cfg/.env'))
check(blacklist.evaluate('cfg/.env', spec={'allowExts': ['.env']})['filtered'] is True,
      '.env 白名单不能越过硬黑名单')

# ---------------------------------------------------------------------------
# 6) get_capabilities.parserMatrix
# ---------------------------------------------------------------------------
print('== 6. capabilities.parserMatrix ==')
from workbench import storage  # noqa: E402
from workbench.storage import engine as sto  # noqa: E402
from workbench import ontology_build_routes as build_routes  # noqa: E402

sto.reset_engine()
storage.mark_unready()
storage.ensure_ready()
payload, status = build_routes.get_capabilities({})
check(status == 200, 'get_capabilities → 200', status)
matrix = payload.get('parserMatrix')
check(isinstance(matrix, list) and len(matrix) == 9, 'parserMatrix 共 9 项（7 支持 + 2 排除）',
      None if not isinstance(matrix, list) else len(matrix))
shaped = all(isinstance(item, dict) and isinstance(item.get('exts'), list) and item['exts']
             and isinstance(item.get('label'), str) and item['label']
             and isinstance(item.get('locator'), str) and isinstance(item.get('note'), str)
             for item in (matrix or []))
check(shaped, 'parserMatrix 每项形如 {exts[], label, locator, note}', matrix)
support, excluded = (matrix or [])[:7], (matrix or [])[7:]
support_exts = [ext for item in support for ext in item['exts']]
expected_exts = ['.json', '.jsonld', '.jsonid', '.jsonl', '.ndjson', '.yaml', '.yml', '.properties',
                 '.csv', '.tsv', '.ini', '.cfg', '.conf', '.toml']
check(support_exts == expected_exts, '7 个支持项 exts 齐全且有序', support_exts)
excluded_exts = [ext for item in excluded for ext in item['exts']]
check('.env*' in excluded_exts and set(excluded_exts) >= {'.parquet', '.proto', '.avro', '.msgpack'},
      '2 个排除项覆盖 .env* 与二进制组', excluded_exts)
# 排除项与黑名单实现一致（避免能力表与判定漂移；SOFT_EXTS 为裸后缀、矩阵为点前缀）
binary_exts = {ext.lstrip('.') for ext in excluded_exts if ext != '.env*'}
check(binary_exts <= set(blacklist.SOFT_EXTS),
      '排除项二进制后缀与 blacklist.SOFT_EXTS 一致',
      sorted(binary_exts - set(blacklist.SOFT_EXTS)))
check(payload.get('blacklist', {}).get('softDefaults') is not None
      and '.parquet' in payload['blacklist']['softDefaults'],
      'capabilities.blacklist.softDefaults 含 .parquet（任务级可见）',
      payload.get('blacklist', {}).get('softDefaults'))

# 契约一致性（08 分册 §2.1 示例必须等于实现输出，文档与代码不得漂移）
DOC = REPO / '文档' / '接口文档' / '08-从物料自动构建本体接口.md'
doc_block = next((block for block in
                  re.findall(r'```json\n(.*?)```', DOC.read_text(encoding='utf-8'), re.S)
                  if 'parserMatrix' in block), '')
doc_matrix = {}
if doc_block:
    # 示例里 `{"id": "…"} | null` 是既有说明性写法（非合法 JSON），替换后再解析
    doc_matrix = json.loads(doc_block.replace('{"id": "…", "name": "…", "model": "…"} | null', 'null'))
check(bool(doc_matrix), '08 §2.1 示例块可解析（含 parserMatrix）')
check(doc_matrix.get('parserMatrix') == matrix, '08 示例 parserMatrix 与实现输出逐项相等',
      doc_matrix.get('parserMatrix'))
check(set(payload['blacklist']['softDefaults']) <= set(doc_matrix.get('blacklist', {})
                                                      .get('softDefaults') or []),
      '08 示例 softDefaults 覆盖实现当前软名单（文档不落后）',
      sorted(set(payload['blacklist']['softDefaults'])
             - set(doc_matrix.get('blacklist', {}).get('softDefaults') or [])))

def _check_scan_plan_redetects_kind():
    import os
    import tempfile
    from pathlib import Path
    from workbench import storage
    from workbench.ontology_build import pipeline
    from workbench.ontology_build import protocol
    from workbench.storage import engine as sto
    from workbench.storage import ontology_build as store

    root = Path(tempfile.mkdtemp(prefix='wiz_kind_redetect_'))
    os.environ['WIZ_WORKBENCH_ROOT'] = str(root)
    os.environ['WIZ_DATABASE_URL'] = 'sqlite:///' + str(root / 'data' / 'workbench.sqlite3')
    sto.reset_engine()
    storage.mark_unready()
    storage.ensure_ready()
    try:
        owner = 'kind-redetect-owner'
        payload = b'{"a": 1, "b": {"c": [1, 2]}}'

        def seed(conn):
            task_id = store.create_task(conn, owner, 'kind 重检测')
            blob_id = store.create_blob(conn, owner, task_id, 'sample.json', len(payload),
                                        'h' * 8, 'ontology-build-blobs/kind-redetect.json')
            # 模拟"升级前上传"：库里存的是旧解析器时代的 kind（code），
            # 文件真实内容却是 JSON —— 重扫必须按内容改判为 json。
            material_id = store.create_material(conn, owner, task_id, blob_id, 'sample.json',
                                                'code', len(payload), 'h' * 8)
            run_id, _lease = store.create_run(conn, task_id, owner, 'scan', {})
            return task_id, material_id, run_id

        with sto.write_tx() as tx:
            task_id, material_id, run_id = tx.run(seed)
        blob_path = pipeline.material_store.blob_dir() / 'kind-redetect.json'
        blob_path.parent.mkdir(parents=True, exist_ok=True)
        blob_path.write_bytes(payload)
        with sto.write_tx() as tx:
            plan = tx.run(lambda conn: pipeline._scan_plan(conn, owner, task_id, run_id, None))
        kind = plan[0]['kind'] if plan else ''
        check(kind == 'json',
              '扫描计划按实际内容重检测 kind（存量 code 材料 → json，升级路径可用）',
              kind)
        check(protocol.detect_kind('sample.json', head=payload[:8]) == 'json',
              'detect_kind 对 JSON 头内容判定为 json', kind)
    finally:
        import shutil
        shutil.rmtree(root, ignore_errors=True)

# ---------------------------------------------------------------------------
_check_scan_plan_redetects_kind()
print('\n通过 %d / 共 %d' % (len(PASSED), len(PASSED) + len(FAILED)))
shutil.rmtree(TMP, ignore_errors=True)
shutil.rmtree(ROOT, ignore_errors=True)
sys.exit(1 if FAILED else 0)
